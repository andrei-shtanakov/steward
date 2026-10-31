"""Записи контракта `approval-facts/v3` (спека steward §4): сериализация фактов библиотеки.

Запись — те же факты, что возвращают `read_*`, в форме `SCHEMA.json` контракта
(`review_record`, `delivery_record`, `policy_history_record`). Потребители в процессе
работают с dataclass-ами; записи нужны там, где факт уходит в файл. Неполный или
недоказанный вход записью `complete` не становится: отказ — `ValueError` (ошибка
вызывающего), а не запись с разрешающим умолчанием.
"""

import re
from dataclasses import asdict

from steward.approvalfacts.v3.effective import effective_reviews
from steward.approvalfacts.v3.model import (
    Activation,
    Delivery,
    EventKey,
    HistoryViolation,
    ReviewFacts,
    Unavailable,
    canon_ts,
)

#: Классы актора политики steward (`classify_actor`); `None` — не классифицирован (спека §2).
ACTOR_CLASSES = frozenset({"human", "agent", "unknown"})
_SHA = re.compile(r"[0-9a-f]{40}", re.ASCII)


def review_record(
    facts: ReviewFacts, *, observed_at: str, t: str, before: frozenset[EventKey]
) -> dict:
    """Запись `review` (спека devtools §5.2): ревью, отзывы и эффективные ревью на `t`.

    `effective` — `effective_reviews(facts, t, before=before)`, по логину в порядке логинов;
    `t` и `before` (ключи по возрастанию) входят в запись (решение M0-I): `effective` — на T
    вызывающего, а не на `observed_at`. `observed_at` или `t` не в каноне меток, факты с
    отказом `effective_reviews` — `ValueError`.
    """
    _require_canon(observed_at, "observed_at")
    effective = effective_reviews(facts, t, before=before)
    if isinstance(effective, Unavailable):
        raise ValueError(f"review facts are not complete: {effective.reason}")
    return {
        "repository": facts.repository,
        "pr": facts.pr,
        "observed_at": observed_at,
        "head_sha": facts.head_sha,
        "base_ref": facts.base_ref,
        "pr_state": facts.pr_state,
        "reviews": [asdict(r) for r in facts.reviews],
        "dismissals": [asdict(d) for d in facts.dismissals],
        "t": t,
        "before": [list(k) for k in sorted(before)],
        "effective": [asdict(effective[login]) for login in sorted(effective)],
        "complete": True,
    }


def delivery_record(d: Delivery, *, actor_class: str | None) -> dict:
    """Запись `delivery` (спека devtools §5.4); мержер — `{identity, type_hint, actor_class}`.

    `identity` — `github:<login>` (форма политики акторов steward). `actor_class` —
    классификация вызывающего по пину политики или `None`; вне словаря, или задан у
    несмёрженного PR — `ValueError`.
    """
    if actor_class is not None and actor_class not in ACTOR_CLASSES:
        raise ValueError(f"actor_class {actor_class!r} is not one of {sorted(ACTOR_CLASSES)}")
    if not d.merged and actor_class is not None:
        raise ValueError("actor_class is given for a pull request that is not merged")
    merged_by = None
    if d.merged and d.merged_by_login is not None:
        merged_by = {
            "identity": f"github:{d.merged_by_login}",
            "type_hint": d.merged_by_type_hint,
            "actor_class": actor_class,
        }
    return {
        "merged": d.merged,
        "merge_commit": d.merge_commit,
        "merged_at": d.merged_at,
        "merged_by": merged_by,
        "head_at_merge": d.head_at_merge,
        "in_base": d.in_base,
        "paths": [asdict(p) for p in d.paths],
        "complete": True,
    }


def policy_history_record(
    repo: str,
    branch: str,
    anchor: str,
    until: str | None,
    got: tuple[Activation, ...] | HistoryViolation | Unavailable,
) -> dict:
    """Запись `policy_history` (спека devtools §6.1 п. 6) результата `read_policy_history`.

    Активации или нарушение — `complete: true` (цепочка прочитана целиком); `Unavailable` —
    `complete: false` с причиной. `anchor`/`until` не 40-hex — `ValueError`.
    """
    _require_sha(anchor, "anchor")
    if until is not None:
        _require_sha(until, "until")
    record: dict = {"repository": repo, "branch": branch, "anchor": anchor, "until": until}
    if isinstance(got, Unavailable):
        return {**record, "unavailable": got.reason, "complete": False}
    if isinstance(got, HistoryViolation):
        return {**record, "violation": got.reason, "complete": True}
    return {**record, "activations": [asdict(a) for a in got], "complete": True}


def _require_canon(stamp: str, name: str) -> None:
    if canon_ts(stamp) != stamp:
        raise ValueError(f"{name} is not canonical: {stamp!r}")


def _require_sha(value: str, name: str) -> None:
    if _SHA.fullmatch(value) is None:
        raise ValueError(f"{name} is not a 40-hex SHA: {value!r}")
