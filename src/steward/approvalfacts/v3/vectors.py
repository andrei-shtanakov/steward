"""Строгий прогон вектора сбора `approval-facts/v3` (спека steward §4, §5; решение M0-J).

Отрицательный вектор несёт только обмены, которые библиотека потребляет до отказа: мутант,
пропустивший дефект, дочитывает до конца записи и получает `Unavailable: transport`.
Поэтому проверка «результат — какой-то отказ» вакуумна, и прогон обязан требовать три
вещи сразу: точный исход по `expect`, полное потребление обменов (`unexpected == []`,
`unused() == []`) и общий порядок запросов, равный порядку обменов (`ReplayTransport` —
FIFO на ключ, общий порядок сам не проверяет). `run_vector` — эта проверка как код
библиотеки: потребители гоняют векторы через неё или через эквивалент с обеими строгими
проверками.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from steward.approvalfacts.v3.act import read_act_facts
from steward.approvalfacts.v3.effective import effective_reviews
from steward.approvalfacts.v3.history import read_activation, read_policy_history
from steward.approvalfacts.v3.model import HistoryViolation, ReviewFacts, Unavailable
from steward.approvalfacts.v3.passes import read_open_review
from steward.approvalfacts.v3.transport import (
    ReplayTransport,
    Request,
    _exchange,
    replay_transport,
)

#: Вид вектора → основной ключ `expect` → допустимые дополнительные ключи (спека §5).
EXPECT_SHAPES: Mapping[str, Mapping[str, frozenset[str]]] = {
    "act": {
        "unavailable": frozenset(),
        "waiting_delivery": frozenset(),
        "effective": frozenset({"effective_commit", "delivery"}),
    },
    "open_review": {"unavailable": frozenset(), "effective": frozenset({"effective_commit"})},
    "history": {"activations": frozenset(), "violation": frozenset(), "unavailable": frozenset()},
    "activation": {"activation": frozenset(), "unavailable": frozenset()},
}


@dataclass(frozen=True)
class VectorOutcome:
    """Итог строгого прогона: `problems` пусто — вектор пройден.

    `requests` — запросы библиотеки в порядке отправки (для независимой сверки вызывающим).
    """

    problems: tuple[str, ...]
    requests: tuple[Request, ...]

    @property
    def ok(self) -> bool:
        """Вектор пройден: исход, потребление и порядок совпали."""
        return not self.problems


def run_vector(vector: Mapping[str, Any]) -> VectorOutcome:
    """Прогнать вектор: вызов библиотеки по `kind`, сверка с `expect`, точное потребление.

    Каждое расхождение — строка в `problems`: исход не тот (в т. ч. код `unavailable`),
    непотреблённые или незаписанные обмены, порядок запросов ≠ порядку обменов, форма
    вектора не по спеке §5. Исключение не бросается: неверный вектор — тоже `problems`.
    """
    try:
        kind, expect = _shape(vector)
        expected = [_exchange(ex)[0] for ex in vector["transport"]["exchanges"]]
        transport = replay_transport(vector["transport"])
    except (KeyError, TypeError, ValueError) as exc:
        return VectorOutcome((f"vector: {exc}",), ())
    try:
        problems = _RUNNERS[kind](vector, expect, transport)
    except (KeyError, TypeError, ValueError) as exc:
        problems = [f"call: {type(exc).__name__}: {exc}"]
    problems += _consumption(transport, expected)
    return VectorOutcome(tuple(problems), tuple(transport.log))


def _shape(vector: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    kind, expect = vector["kind"], vector["expect"]
    if kind not in EXPECT_SHAPES or not isinstance(expect, Mapping):
        raise ValueError(f"unknown kind {kind!r} or expect not an object")
    shapes = EXPECT_SHAPES[kind]
    primary = [k for k in expect if k in shapes]
    if len(primary) != 1 or set(expect) - {primary[0]} - shapes[primary[0]]:
        raise ValueError(f"expect keys {sorted(expect)} do not fit kind {kind!r}")
    return kind, expect


def _consumption(t: ReplayTransport, expected: Sequence[Request]) -> list[str]:
    problems = []
    if t.unexpected:
        problems.append(f"unexpected requests: {len(t.unexpected)}")
    if unused := t.unused():
        problems.append(f"unused exchanges: {len(unused)}")
    if list(t.log) != list(expected):
        at = next(
            (i for i, (a, b) in enumerate(zip(t.log, expected, strict=False)) if a != b),
            min(len(t.log), len(expected)),
        )
        problems.append(f"request order differs from exchanges at #{at}")
    return problems


def _refusal(got: object, expect: Mapping[str, Any]) -> list[str] | None:
    """`expect.unavailable`: отказ с точным кодом; `None` — ожидание не про отказ."""
    if "unavailable" not in expect:
        return None
    if isinstance(got, Unavailable) and got.code == expect["unavailable"]:
        return []
    return [f"expected unavailable {expect['unavailable']!r}, got {got!r}"]


def _run_act(v: Mapping[str, Any], expect: Mapping[str, Any], t: ReplayTransport) -> list[str]:
    got = read_act_facts(t, v["repo"], v["pr"], paths=v["paths"])
    refused = _refusal(got, expect)
    if refused is not None:
        return refused
    if isinstance(got, Unavailable):
        return [f"unexpected {got!r}"]
    delivery, facts = got
    if expect.get("waiting_delivery"):
        if facts is None and not delivery.merged and delivery.merge_commit is None:
            return []
        return [f"expected waiting_delivery, got merged={delivery.merged}"]
    if facts is None:
        return ["expected facts of a merged pull request, got none"]
    problems = _effective(facts, v, expect)
    for name, want in expect.get("delivery", {}).items():
        actual = getattr(delivery, name)
        if name == "paths":
            actual = [asdict(p) for p in actual]
        if actual != want:
            problems.append(f"delivery.{name}: expected {want!r}, got {actual!r}")
    return problems


def _run_open_review(
    v: Mapping[str, Any], expect: Mapping[str, Any], t: ReplayTransport
) -> list[str]:
    got = read_open_review(t, v["repo"], v["pr"])
    refused = _refusal(got, expect)
    if refused is not None:
        return refused
    if not isinstance(got, ReviewFacts):
        return [f"unexpected {got!r}"]
    return _effective(got, v, expect)


def _effective(facts: ReviewFacts, v: Mapping[str, Any], expect: Mapping[str, Any]) -> list[str]:
    before = frozenset((str(k[0]), str(k[1])) for k in v["before"])
    effective = effective_reviews(facts, v["t"], before=before)
    if isinstance(effective, Unavailable):
        return [f"effective_reviews refused: {effective.reason}"]
    problems = []
    states = {login: r.state for login, r in effective.items()}
    if states != expect["effective"]:
        problems.append(f"effective: expected {expect['effective']!r}, got {states!r}")
    for login, commit in expect.get("effective_commit", {}).items():
        actual = effective[login].commit_id if login in effective else None
        if actual != commit:
            problems.append(f"effective_commit.{login}: expected {commit}, got {actual}")
    return problems


def _run_history(v: Mapping[str, Any], expect: Mapping[str, Any], t: ReplayTransport) -> list[str]:
    got = read_policy_history(
        t, v["repo"], v["branch"], anchor=v["anchor"], until=v["until"], files=tuple(v["files"])
    )
    refused = _refusal(got, expect)
    if refused is not None:
        return refused
    if "violation" in expect:
        if isinstance(got, HistoryViolation) and got.code == expect["violation"]:
            return []
        return [f"expected violation {expect['violation']!r}, got {got!r}"]
    if not isinstance(got, tuple):
        return [f"expected activations, got {got!r}"]
    actual = [asdict(a) for a in got]
    if actual != expect["activations"]:
        return [f"activations: expected {expect['activations']!r}, got {actual!r}"]
    return []


def _run_activation(
    v: Mapping[str, Any], expect: Mapping[str, Any], t: ReplayTransport
) -> list[str]:
    got = read_activation(t, v["repo"], v["merge_commit"], files=tuple(v["files"]))
    refused = _refusal(got, expect)
    if refused is not None:
        return refused
    if isinstance(got, Unavailable):
        return [f"unexpected {got!r}"]
    if asdict(got) != expect["activation"]:
        return [f"activation: expected {expect['activation']!r}, got {asdict(got)!r}"]
    return []


_RUNNERS: Mapping[
    str, Callable[[Mapping[str, Any], Mapping[str, Any], ReplayTransport], list[str]]
] = {
    "act": _run_act,
    "open_review": _run_open_review,
    "history": _run_history,
    "activation": _run_activation,
}
