"""Раннер сбора `approval-facts` — маршрутизация, preflight, агрегация.

Логики фактов здесь нет и быть не должно: раннер решает только, тот ли это
чекаут и кого звать. Соответственно и тесты — про отказ дойти до записи, а не
про содержание бандла.
"""

from __future__ import annotations

import importlib.util
import json
import os
import time
import xml.dom.minidom
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from steward.approvalfacts.model import RequestId, scope_digest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "collect_approval_facts.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("collect_approval_facts", SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging error
        raise RuntimeError(f"cannot load the collector from {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["collect_approval_facts"] = module
    spec.loader.exec_module(module)
    return module


RUNNER = _load()
REPO = "andrei-shtanakov/steward"

#: Длительность lease в тестовой политике. Читатель гейта сверяет РАВЕНСТВО
#: `valid_until - generated_at` с политикой, поэтому фикстура бандла обязана
#: строить `generated_at` из этой же константы — иначе каждый бандл падал бы
#: как `lease_mismatch` ещё до проверяемого свойства.
LEASE_SECONDS = 86400

POLICY_TEXT = (
    "version: 1\n"
    "human_identities:\n"
    "  - github:andrei-shtanakov\n"
    "agent_identities:\n"
    "  - github:merge-broker\n"
    "agent_merge_allowed: false\n"
    f"approval_facts_lease_seconds: {LEASE_SECONDS}\n"
)

#: Дайджест считается от того же текста, что пишет `_policy()`: заголовок
#: бандла и активная политика обязаны сходиться байт в байт, как в бою.
POLICY_DIGEST = "sha256:" + __import__("hashlib").sha256(POLICY_TEXT.encode()).hexdigest()


def _policy(tmp_path: Path) -> Path:
    path = tmp_path / "approval-policy.yaml"
    if not path.exists():
        path.write_text(POLICY_TEXT, encoding="utf-8")
    return path


def _digest_for(prs: list[int]) -> str:
    """Дайджест охвата ТОЙ ЖЕ функцией, что у продюсера и читателя контракта.

    Фикстура с выдуманным `scope_sha256` проходила бы мимо проверки, которая
    ради этого и заведена.
    """
    return scope_digest([RequestId(kind="pr", value=n) for n in prs])


def _checkout(root: Path, name: str, origin: str | None = REPO) -> Path:
    path = root / name
    (path / ".git").mkdir(parents=True)
    if origin is not None:
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(
            ["git", "-C", str(path), "remote", "add", "origin", f"git@github.com:{origin}.git"],
            check=True,
        )
    return path


def _publishing_producer(prs_by_call: list[list[int]] | None = None):
    """Фейк продюсера, который ДЕЙСТВИТЕЛЬНО публикует бандл.

    Раннер проверяет постусловие: нулевой код без появившегося (или
    обновлённого) файла — не публикация. Значит фейк, который «успешен» и
    ничего не пишет, моделирует ровно тот отказ, а не счастливый путь.
    Пишет ПОЛНУЮ форму (`_bundle`): постусловие теперь проверяет читатель
    контракта, и урезанный фейковый бандл он бы отверг.
    """

    def fake(repo: str, root: Path, policy: Path, prs: list[int]) -> tuple[int, str]:
        if prs_by_call is not None:
            prs_by_call.append(prs)
        _bundle(root, datetime.now(UTC) + timedelta(hours=6), prs=prs)
        return 0, ""

    return fake


def _entry(checkout: str = "steward", prs: list[int] | None = None) -> dict[str, Any]:
    return {"repo": REPO, "checkout": checkout, "prs": prs if prs is not None else [1]}


# --- preflight: не дойти до записи -----------------------------------------


def test_missing_checkout_is_skipped_and_never_calls_the_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главный fail-closed: у продюсера `--repo-root` имеет дефолт `.`,

    поэтому вызов для репозитория без чекаута записал бы его факты в бандл
    steward — подмена наблюдаемого объекта, выглядящая как успешная публикация.
    """
    called: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        RUNNER,
        "run_producer",
        lambda repo, root, policy, prs: called.append((repo, root)) or (0, ""),
    )

    outcomes = RUNNER.collect([_entry("nowhere")], tmp_path, _policy(tmp_path))

    assert [o.status for o in outcomes] == ["skipped"]
    assert "чекаута нет" in outcomes[0].detail
    assert called == [], "продюсер не должен вызываться вовсе"


def test_wrong_origin_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Самая опасная ошибка конфигурации: путь есть, git есть, объект не тот.

    Без этой проверки бандл выглядел бы законным — с чужими фактами внутри.
    """
    _checkout(tmp_path, "steward", origin="andrei-shtanakov/maestro")
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: pytest.fail("не должен вызываться"))

    outcomes = RUNNER.collect([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "skipped"
    assert "origin чекаута" in outcomes[0].detail


def test_checkout_outside_workspace_root_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`..` в охвате не должен выводить запись за пределы наблюдаемого набора."""
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: pytest.fail("не должен вызываться"))
    workspace = tmp_path / "ws"
    workspace.mkdir()

    outcomes = RUNNER.collect([_entry("../elsewhere")], workspace, _policy(tmp_path))

    assert outcomes[0].status == "skipped"
    assert "вне workspace-root" in outcomes[0].detail


def test_directory_without_git_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "steward").mkdir()
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: pytest.fail("не должен вызываться"))

    outcomes = RUNNER.collect([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "skipped"
    assert "не git-корень" in outcomes[0].detail


def test_empty_pr_list_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустой охват — не «спросили и ничего не нашли», а «не спрашивали»."""
    _checkout(tmp_path, "steward")
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: pytest.fail("не должен вызываться"))

    outcomes = RUNNER.collect([_entry(prs=[])], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "skipped"


# --- независимость и агрегация ---------------------------------------------


def test_one_bad_checkout_does_not_stop_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Требование зафиксировано на одном репо, чтобы не всплыло на двадцати:

    иначе один неверный чекаут делал бы ненаблюдаемым весь набор, а причина
    была бы видна только в хвосте лога.
    """
    _checkout(tmp_path, "good")
    monkeypatch.setattr(RUNNER, "run_producer", _publishing_producer())

    outcomes = RUNNER.collect(
        [
            {"repo": REPO, "checkout": "nowhere", "prs": [1]},
            {"repo": REPO, "checkout": "good", "prs": [2]},
        ],
        tmp_path,
        _policy(tmp_path),
    )

    assert [o.status for o in outcomes] == ["skipped", "published"]


def test_producer_failure_is_failed_not_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Разные вещи: «не спросили» и «спросили, не смогли»."""
    _checkout(tmp_path, "steward")
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: (3, "механический сбой"))

    outcomes = RUNNER.collect([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "кодом 3" in outcomes[0].detail


def test_skipped_alone_is_not_success(capsys: pytest.CaptureFixture[str]) -> None:
    """Репозиторий, которого не спросили, не должен читаться зелёным."""
    code = RUNNER.report([RUNNER.Outcome(REPO, "skipped", "чекаута нет")])

    assert code == 1
    assert "не опубликовано" in capsys.readouterr().err


def test_all_published_is_zero() -> None:
    assert RUNNER.report([RUNNER.Outcome(REPO, "published", "ok")]) == 0


# --- свежесть: проверка установки расписания -------------------------------


def _result(n: int, state: str = "merged") -> dict[str, Any]:
    """Запись результата, которую примет читатель контракта.

    `merged` по матрице полей требует 40-hex `merge_sha` и все три поля актора;
    отрицательные состояния — наоборот, `merge_sha: null` и НИ ОДНОГО поля
    актора. Упрощённая запись (одно `state`) не проходит `load_facts`, и это не
    строгость ради строгости: раннер теперь спрашивает читателя, значит и
    фикстуры обязаны писать то, что пишет настоящий продюсер.
    """
    record: dict[str, Any] = {
        "kind": "result",
        "request": {"kind": "pr", "value": n},
        "state": state,
    }
    if state == "merged":
        record.update(
            merge_sha=f"{n:040x}",
            identity="github:andrei-shtanakov",
            type_hint="User",
            actor_class="human",
        )
    else:
        record["merge_sha"] = None
    return record


def _bundle(
    path: Path,
    valid_until: datetime,
    prs: list[int] | None = None,
    results: list[dict[str, Any]] | None = None,
    header_overrides: dict[str, Any] | None = None,
) -> None:
    """Бандл в той же форме, что пишет настоящий продюсер.

    В частности `value` для `pr` — ЧИСЛО, а не строка: дайджест охвата зависит
    от типа, и фикстура со строками не совпала бы с тем, что проверяет читатель
    контракта. Первая версия писала строки и молча расходилась с реальностью.
    Второй заход на те же грабли: упрощённый header без `policy_digest` и
    записи без матрицы полей перестали проходить, как только раннер стал
    спрашивать самого читателя (`bundle_verdict`), — фикстура выросла до полной
    формы вместе с ним.
    """
    numbers = prs if prs is not None else [1]
    generated_at = valid_until - timedelta(seconds=LEASE_SECONDS)
    (path / ".steward").mkdir(parents=True, exist_ok=True)
    header: dict[str, Any] = {
        "kind": "header",
        "schema_version": "2",
        "repository": REPO,
        "generated_at": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "valid_until": valid_until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policy_version": 1,
        "policy_digest": POLICY_DIGEST,
        "complete": True,
        "scope": [{"kind": "pr", "value": n} for n in numbers],
        "scope_sha256": _digest_for(numbers),
    }
    if header_overrides:
        header.update(header_overrides)
    rows = results if results is not None else [_result(n) for n in numbers]
    lines = [json.dumps(header)] + [json.dumps(r) for r in rows]
    (path / ".steward" / "approval_facts.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def test_expired_lease_is_failed(tmp_path: Path) -> None:
    """Ровно то состояние, в котором единственный бандл флота был найден."""
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) - timedelta(hours=20))

    outcomes = RUNNER.freshness([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "lease истёк" in outcomes[0].detail


def test_live_lease_is_published(tmp_path: Path) -> None:
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6))

    assert RUNNER.freshness([_entry()], tmp_path, _policy(tmp_path))[0].status == "published"


def test_absent_bundle_is_failed_not_silent(tmp_path: Path) -> None:
    _checkout(tmp_path, "steward")

    outcomes = RUNNER.freshness([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "бандла нет" in outcomes[0].detail


def test_unreadable_header_is_failed(tmp_path: Path) -> None:
    path = _checkout(tmp_path, "steward")
    (path / ".steward").mkdir(parents=True, exist_ok=True)
    (path / ".steward" / "approval_facts.jsonl").write_text("not json\n", encoding="utf-8")

    assert RUNNER.freshness([_entry()], tmp_path, _policy(tmp_path))[0].status == "failed"


def test_workspace_root_has_no_default() -> None:
    """cwd под launchd не тот, что в интерактивной сессии — угадывать нельзя."""
    with pytest.raises(SystemExit):
        RUNNER.main([])


# --- находки машинного ревью PR #96 ----------------------------------------


def test_non_dict_scope_entry_is_skipped_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Строка вместо объекта в охвате падала на `entry.get` с AttributeError."""
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: pytest.fail("не должен вызываться"))

    outcomes = RUNNER.collect(["просто строка"], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "skipped"
    assert "не объект" in outcomes[0].detail


@pytest.mark.parametrize(
    ("value", "why"),
    [
        pytest.param(74.5, "дробное", id="float"),
        pytest.param("abc", "строка", id="string"),
        pytest.param(True, "bool", id="bool"),
        pytest.param(0, "ноль", id="zero"),
        pytest.param(-1, "отрицательное", id="negative"),
    ],
)
def test_non_integer_pr_number_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: Any, why: str
) -> None:
    """Оба тихих отказа `int(n)` сразу.

    `74.5` превращался в запрос PR `74` — спросили не тот объект, а отчёт
    сказал бы `published`. `"abc"` бросал ValueError посреди батча и обрывал
    остальные репозитории. `True` — это `int` в Python, и он стал бы PR №1.
    """
    _checkout(tmp_path, "steward")
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: pytest.fail(f"{why}: не звать"))

    outcomes = RUNNER.collect([_entry(prs=[value])], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "skipped"


def test_bad_pr_number_does_not_abort_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Заявленная независимость репозиториев проверяется именно на этом."""
    _checkout(tmp_path, "bad")
    _checkout(tmp_path, "good")
    monkeypatch.setattr(RUNNER, "run_producer", _publishing_producer())

    outcomes = RUNNER.collect(
        [
            {"repo": REPO, "checkout": "bad", "prs": ["abc"]},
            {"repo": REPO, "checkout": "good", "prs": [2]},
        ],
        tmp_path,
        _policy(tmp_path),
    )

    assert [o.status for o in outcomes] == ["skipped", "published"]


def test_naive_valid_until_is_failed_not_a_traceback(tmp_path: Path) -> None:
    """Сравнение naive с aware бросает TypeError и обрывало бы весь `--check`."""
    path = _checkout(tmp_path, "steward")
    (path / ".steward").mkdir(parents=True, exist_ok=True)
    (path / ".steward" / "approval_facts.jsonl").write_text(
        json.dumps({"kind": "header", "valid_until": "2026-08-23T12:00:00"}) + "\n",
        encoding="utf-8",
    )

    outcomes = RUNNER.freshness([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "не примет читатель контракта" in outcomes[0].detail


def test_relative_policy_is_resolved_before_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверка `is_file()` шла в текущем каталоге, а продюсер стартует в другом.

    Относительный путь означал бы там другой файл — preflight зелёный про не тот.
    """
    _checkout(tmp_path, "steward")
    scope = tmp_path / "scope.yaml"
    scope.write_text(json.dumps({"version": 1, "repositories": [_entry()]}), encoding="utf-8")
    # Политика теперь читается fail-closed до обхода, поэтому файл обязан быть
    # валидным, а не любым: `version: 1` без остальных ключей — PolicyError.
    policy = tmp_path / "policy.yaml"
    policy.write_text(POLICY_TEXT, encoding="utf-8")
    seen: list[Path] = []
    monkeypatch.setattr(
        RUNNER, "run_producer", lambda repo, root, pol, prs: (seen.append(pol), (0, ""))[1]
    )
    monkeypatch.chdir(tmp_path)

    RUNNER.main(
        ["--workspace-root", str(tmp_path), "--scope", "scope.yaml", "--policy", "policy.yaml"]
    )

    assert seen == [policy.resolve()]
    assert seen[0].is_absolute()


def test_duplicate_checkout_is_refused_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Бандл лежит по фиксированному пути, поэтому вторая запись затёрла бы первую.

    Оба прогона отчитались бы `published`, а на диске остался бы последний —
    часть заявленного охвата потерялась бы при зелёном отчёте.
    """
    _checkout(tmp_path, "steward")
    monkeypatch.setattr(RUNNER, "run_producer", _publishing_producer())

    outcomes = RUNNER.collect(
        [
            {"repo": REPO, "checkout": "steward", "prs": [1]},
            {"repo": REPO, "checkout": "steward", "prs": [2]},
        ],
        tmp_path,
        _policy(tmp_path),
    )

    assert [o.status for o in outcomes] == ["published", "skipped"]
    assert "затёр бы первый" in outcomes[1].detail


def test_grown_scope_is_not_green_until_collected(tmp_path: Path) -> None:
    """Свежесть без сверки охвата — зелёное без факта.

    Добавили PR в охват, а бандл до истечения lease продолжает показываться
    как `published`, хотя новый охват ни разу не собирался.
    """
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6), prs=[1])

    outcomes = RUNNER.freshness([_entry(prs=[1, 2])], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "нет записей по PR" in outcomes[0].detail


def test_producer_is_called_from_the_active_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Вложенный `uv run` вернул бы ровно ту поломку, от которой защищается plist.

    Там `uv` зовётся по абсолютному пути именно потому, что PATH под launchd
    ненадёжен; голый `uv` внутри раннера дал бы `command not found` и молча
    остановил бы сбор.
    """
    assert RUNNER.STEWARD_BIN.name == "steward"
    assert RUNNER.STEWARD_BIN.is_absolute()

    captured: list[list[str]] = []
    monkeypatch.setattr(RUNNER, "_run", lambda argv, **kw: captured.append(argv) or None)
    RUNNER.run_producer(REPO, Path("/tmp"), Path("/tmp/p.yaml"), [1])

    assert captured and "uv" not in captured[0]


def test_hung_subprocess_does_not_abort_the_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """`TimeoutExpired` улетал бы из `collect()` и обрывал остальные записи."""
    monkeypatch.setattr(
        RUNNER.subprocess,
        "run",
        lambda *a, **kw: (_ for _ in ()).throw(RUNNER.subprocess.TimeoutExpired("x", 1)),
    )

    assert RUNNER._run(["true"], timeout=1) is None


def test_zero_exit_without_a_bundle_is_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Нулевой код — заявление продюсера, а не факт публикации."""
    _checkout(tmp_path, "steward")
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: (0, ""))

    outcomes = RUNNER.collect([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "бандла нет" in outcomes[0].detail


def test_zero_exit_without_touching_the_bundle_is_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Старый бандл на месте, но не тронут — тоже не публикация.

    Признак содержательный, а не временной: сравниваются байты и lease, потому
    что на ФС с секундной гранулярностью `mtime` двух прогонов подряд совпал бы
    и отверг бы настоящую публикацию.
    """
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6))
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: (0, ""))

    outcomes = RUNNER.collect([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "не изменился" in outcomes[0].detail


def test_record_of_a_different_kind_does_not_count_as_coverage(tmp_path: Path) -> None:
    """Ответ про `merge_sha:75` — не ответ про PR №75."""
    path = _checkout(tmp_path, "steward")
    (path / ".steward").mkdir(parents=True, exist_ok=True)
    header = {
        "kind": "header",
        "valid_until": (datetime.now(UTC) + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    record = {"kind": "result", "request": {"kind": "merge_sha", "value": "75"}, "state": "merged"}
    (path / ".steward" / "approval_facts.jsonl").write_text(
        json.dumps(header) + "\n" + json.dumps(record) + "\n", encoding="utf-8"
    )

    assert RUNNER.freshness([_entry(prs=[75])], tmp_path, _policy(tmp_path))[0].status == "failed"


def test_non_object_header_is_failed_not_a_traceback(tmp_path: Path) -> None:
    """`[]` — валидный JSON, но не заголовок; индексация давала TypeError."""
    path = _checkout(tmp_path, "steward")
    (path / ".steward").mkdir(parents=True, exist_ok=True)
    (path / ".steward" / "approval_facts.jsonl").write_text("[]\n", encoding="utf-8")

    outcomes = RUNNER.freshness([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "не примет читатель контракта" in outcomes[0].detail


def test_check_refuses_the_same_duplicate_scope_that_collect_refuses(
    tmp_path: Path,
) -> None:
    """Два прохода не должны расходиться в оценке одного охвата.

    Иначе обещанное доказательство установки зелёное, а плановый сбор на том же
    охвате падает.
    """
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6), prs=[1])
    duplicated = [
        {"repo": REPO, "checkout": "steward", "prs": [1]},
        {"repo": REPO, "checkout": "steward", "prs": [1]},
    ]

    checked = RUNNER.freshness(duplicated, tmp_path, _policy(tmp_path))

    assert [o.status for o in checked] == ["published", "skipped"]
    assert RUNNER.report(checked) == 1


PLIST = (
    Path(__file__).resolve().parents[2] / "scripts" / "com.steward.approval-facts.plist.template"
)


def test_plist_quotes_substituted_paths() -> None:
    """Путь с пробелом иначе разбивается шеллом, и сбор не происходит никогда."""
    template = PLIST.read_text(encoding="utf-8")

    for placeholder in ("@STEWARD_ROOT@", "@UV_BIN@", "@WORKSPACE_ROOT@"):
        assert f"'{placeholder}'" in template, f"{placeholder} не в кавычках"


def test_plist_template_is_valid_xml() -> None:
    """XML-комментарий не может содержать `--`, а инструкция полна `--flag`.

    Пока проза лежала внутри комментария, шаблон был невалидным XML, и
    `launchctl load` не принял бы его вовсе: сбор не запускался бы никогда, а
    plist выглядел бы установленным. Проза вынесена в
    `scripts/approval-facts-schedule.md`; этот тест сторожит возврат.
    """
    xml.dom.minidom.parse(str(PLIST))


def test_plist_placeholders_are_not_angle_bracketed() -> None:
    """`<ИМЯ>` внутри XML пришлось бы экранировать, и `sed` по `<ИМЯ>` не нашёл
    бы ничего: plist установился бы с литеральными плейсхолдерами."""
    template = PLIST.read_text(encoding="utf-8")

    assert "&lt;" not in template and "&gt;" not in template


def test_non_result_record_does_not_count_as_an_answer(tmp_path: Path) -> None:
    """`{"kind": "error", "request": {...}}` — не ответ по этому PR."""
    path = _checkout(tmp_path, "steward")
    (path / ".steward").mkdir(parents=True, exist_ok=True)
    header = {
        "kind": "header",
        "valid_until": (datetime.now(UTC) + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    broken = {"kind": "error", "request": {"kind": "pr", "value": "95"}}
    (path / ".steward" / "approval_facts.jsonl").write_text(
        json.dumps(header) + "\n" + json.dumps(broken) + "\n", encoding="utf-8"
    )

    assert RUNNER.freshness([_entry(prs=[95])], tmp_path, _policy(tmp_path))[0].status == "failed"


def test_first_line_without_kind_header_is_failed(tmp_path: Path) -> None:
    """Объект с `valid_until`, но без `kind: header`, — структурно не бандл."""
    path = _checkout(tmp_path, "steward")
    (path / ".steward").mkdir(parents=True, exist_ok=True)
    (path / ".steward" / "approval_facts.jsonl").write_text(
        json.dumps({"valid_until": "2026-12-31T00:00:00Z"}) + "\n", encoding="utf-8"
    )

    outcomes = RUNNER.freshness([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "не примет читатель контракта" in outcomes[0].detail


def test_install_instructions_substitute_the_log_dir() -> None:
    """`mkdir -p @LOG_DIR@` создал бы каталог с буквальным именем.

    Логи писались бы не туда, куда смотрит проверка, а в худшем случае launchd
    не смог бы открыть `StandardErrorPath` — и отказ сбора остался бы невидим.
    """
    doc = (
        Path(__file__).resolve().parents[2] / "scripts" / "approval-facts-schedule.md"
    ).read_text(encoding="utf-8")
    install = doc[doc.index("Установка") : doc.index("Снятие")]

    assert "mkdir -p @LOG_DIR@" not in install
    assert 'mkdir -p "$LOGS"' in install


def test_publication_of_an_already_expired_bundle_is_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Публикация, родившаяся просроченной, — не публикация."""
    _checkout(tmp_path, "steward")

    def stale_producer(repo: str, root: Path, policy: Path, prs: list[int]) -> tuple[int, str]:
        _bundle(root, datetime.now(UTC) - timedelta(hours=1), prs=prs)
        return 0, ""

    monkeypatch.setattr(RUNNER, "run_producer", stale_producer)

    outcomes = RUNNER.collect([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "lease истёк" in outcomes[0].detail


def test_publication_without_a_header_is_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`result`-строки без заголовка проходили как успешная публикация."""
    _checkout(tmp_path, "steward")

    def headerless(repo: str, root: Path, policy: Path, prs: list[int]) -> tuple[int, str]:
        bundle = root / RUNNER.BUNDLE_RELPATH
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text(
            json.dumps({"kind": "result", "request": {"kind": "pr", "value": "1"}}) + "\n",
            encoding="utf-8",
        )
        return 0, ""

    monkeypatch.setattr(RUNNER, "run_producer", headerless)

    outcomes = RUNNER.collect([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "header обязан присутствовать ровно один раз" in outcomes[0].detail


def test_path_constraint_is_stated_not_silently_broken() -> None:
    """Экранировать XML и shell одной строкой `sed` честно нельзя.

    Молчаливый отказ стоил бы дороже названного ограничения: plist установился
    бы, а сбор не запускался бы никогда.
    """
    doc = (
        Path(__file__).resolve().parents[2] / "scripts" / "approval-facts-schedule.md"
    ).read_text(encoding="utf-8")

    assert "Ограничение путей" in doc


def test_both_passes_refuse_identical_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`collect()` и `--check` обязаны судить путь одинаково.

    Расхождение уже возвращалось дважды: сперва дубликаты чекаутов проверял
    только сбор, потом сбор проверял записи, а проверка — заголовок. Каждый раз
    следствие одно: обещанное доказательство установки зелёное, а плановый сбор
    на том же охвате падает. Поэтому одинаковость закреплена тестом, а не
    намерением — оба прохода ходят через общий `_resolved()`.
    """
    _checkout(tmp_path, "good")
    _checkout(tmp_path, "wrong", origin="andrei-shtanakov/maestro")
    (tmp_path / "plain").mkdir()
    scope = [
        {"repo": REPO, "checkout": "nowhere", "prs": [1]},
        {"repo": REPO, "checkout": "wrong", "prs": [1]},
        {"repo": REPO, "checkout": "plain", "prs": [1]},
        {"repo": REPO, "checkout": "../outside", "prs": [1]},
        "не объект",
        {"repo": REPO, "checkout": "good", "prs": [1]},
        {"repo": REPO, "checkout": "good", "prs": [2]},
    ]
    monkeypatch.setattr(RUNNER, "run_producer", _publishing_producer())

    collected = RUNNER.collect(scope, tmp_path, _policy(tmp_path))
    checked = RUNNER.freshness(scope, tmp_path, _policy(tmp_path))

    def refusals(outcomes: list[Any]) -> list[tuple[str, str]]:
        return [(o.repo, o.detail) for o in outcomes if o.status == "skipped"]

    assert refusals(collected) == refusals(checked)
    # Шесть: отсутствующий, чужой origin, не-git, вне корня, не объект
    # и дубликат чекаута — последний тоже отказ по пути, а не по данным.
    assert len(refusals(collected)) == 6


def test_symlinked_bundle_dir_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`.steward` симлинком уводит публикацию наружу чекаута.

    Два репозитория с симлинками в один каталог писали бы в один файл, оба
    выглядя успешными: проверка «путь внутри workspace-root» этого не ловит,
    потому что смотрит на чекаут, а не на резолвнутый файл бандла.
    """
    path = _checkout(tmp_path, "steward")
    shared = tmp_path / "shared"
    shared.mkdir()
    (path / ".steward").symlink_to(shared, target_is_directory=True)
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: pytest.fail("не должен вызываться"))

    outcomes = RUNNER.collect([_entry()], tmp_path, _policy(tmp_path))

    # `failed`, не `skipped`: увод публикации наружу чекаута — поломанное
    # состояние, а не «не спрашивали»; оператор должен его увидеть как отказ.
    assert outcomes[0].status == "failed"
    assert "вне чекаута" in outcomes[0].detail


def test_unmoved_lease_is_not_a_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Файл тронут, записи на месте, lease в будущем — и снимка всё равно не было.

    Старый бандл с близким `valid_until` удовлетворял всем прочим проверкам;
    свежесть доказывает только сдвинувшийся вперёд lease.
    """
    path = _checkout(tmp_path, "steward")
    lease = datetime.now(UTC) + timedelta(minutes=5)
    _bundle(path, lease)

    def toucher(repo: str, root: Path, policy: Path, prs: list[int]) -> tuple[int, str]:
        _bundle(root, lease)  # тот же lease, новый mtime
        return 0, ""

    monkeypatch.setattr(RUNNER, "run_producer", toucher)

    outcomes = RUNNER.collect([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "lease не сдвинулся" in outcomes[0].detail


def test_invalid_prs_is_skipped_in_both_passes(tmp_path: Path) -> None:
    """«Не спрашивали» — `skipped` в обоих проходах, а не `failed` в одном."""
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6))

    checked = RUNNER.freshness([_entry(prs=["abc"])], tmp_path, _policy(tmp_path))

    assert checked[0].status == "skipped"


def test_install_creates_the_launch_agents_dir() -> None:
    """На свежей учётной записи каталога может не быть, и plist не появился бы."""
    doc = (
        Path(__file__).resolve().parents[2] / "scripts" / "approval-facts-schedule.md"
    ).read_text(encoding="utf-8")

    install = doc[doc.index("  Установка.") : doc.index("  Снятие:")]

    assert "mkdir -p" in install
    assert "~/Library/LaunchAgents" in install[install.index("mkdir -p") :]


def test_same_second_republication_is_not_falsely_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`RunAtLoad` плюс рекомендованный `kickstart` дают два прогона подряд.

    На ФС с секундной гранулярностью их `mtime` совпал бы, и настоящая
    публикация отвергалась бы ложно. Признак содержательный, поэтому
    изменившиеся байты достаточны даже при неподвижном lease.
    """
    path = _checkout(tmp_path, "steward")
    lease = datetime.now(UTC) + timedelta(hours=6)
    _bundle(path, lease, prs=[1])

    def republish(repo: str, root: Path, policy: Path, prs: list[int]) -> tuple[int, str]:
        _bundle(root, lease, prs=[1, 2])  # тот же lease, другое содержимое
        return 0, ""

    monkeypatch.setattr(RUNNER, "run_producer", republish)

    outcomes = RUNNER.collect([_entry(prs=[1, 2])], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "published"


def test_check_validates_scope_before_bundle_state(tmp_path: Path) -> None:
    """Негодный охват на репо без бандла — `skipped`, а не поломка публикации.

    Порядок проверок обязан совпадать с `collect()`: иначе дефект строки охвата
    докладывается как отказ публикации, которой и не могло быть.
    """
    _checkout(tmp_path, "steward")  # бандла нет вовсе

    outcomes = RUNNER.freshness([_entry(prs=[])], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "skipped"


def test_unresolvable_path_does_not_abort_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`OSError` из резолвера улетал бы наверх и обрывал остальные записи."""
    _checkout(tmp_path, "good")
    monkeypatch.setattr(RUNNER, "run_producer", _publishing_producer())

    def exploding(path: Path) -> tuple[Path | None, str]:
        if "boom" in str(path):
            return None, "путь не резолвится (boom): ELOOP"
        return path, ""

    monkeypatch.setattr(RUNNER, "_resolve", exploding)

    outcomes = RUNNER.collect(
        [
            {"repo": REPO, "checkout": "boom", "prs": [1]},
            {"repo": REPO, "checkout": "good", "prs": [2]},
        ],
        tmp_path,
        _policy(tmp_path),
    )

    # Нерезолвящийся путь — отказ ИНСТРУМЕНТА (ELOOP), не конфигурация:
    # с шестого зрячего раунда такие состояния — `failed`, не `skipped`.
    assert [o.status for o in outcomes] == ["failed", "published"]


def test_path_alias_of_the_same_checkout_is_a_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Две записи, разными путями указывающие в один чекаут, — дубликат.

    Портируемый случай: `./steward` и `steward` резолвятся в одно на любой ФС.
    """
    _checkout(tmp_path, "steward")
    monkeypatch.setattr(RUNNER, "run_producer", _publishing_producer())

    outcomes = RUNNER.collect(
        [
            {"repo": REPO, "checkout": "steward", "prs": [1]},
            {"repo": REPO, "checkout": "./steward", "prs": [2]},
        ],
        tmp_path,
        _policy(tmp_path),
    )

    assert outcomes[1].status == "skipped"
    assert "уже занят" in outcomes[1].detail


def test_case_only_alias_is_a_duplicate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """На case-insensitive томе `steward` и `STEWARD` — один файл.

    У этого флота такой инцидент уже был: переименование `Maestro` -> `maestro`
    держалось только на регистре, и списки репозиториев расходились молча.

    Тест обязан спрашивать ФС, а не платформу: первая версия проходила на APFS
    и падала на Linux-раннере, потому что там `STEWARD` просто не существует и
    preflight отказывает раньше дедупликации.
    """
    _checkout(tmp_path, "steward")
    if not (tmp_path / "STEWARD").is_dir():
        pytest.skip("файловая система чувствительна к регистру — алиаса нет")
    monkeypatch.setattr(RUNNER, "run_producer", _publishing_producer())

    outcomes = RUNNER.collect(
        [
            {"repo": REPO, "checkout": "steward", "prs": [1]},
            {"repo": REPO, "checkout": "STEWARD", "prs": [2]},
        ],
        tmp_path,
        _policy(tmp_path),
    )

    assert outcomes[1].status == "skipped"
    assert "уже занят" in outcomes[1].detail


def test_duplicate_key_is_filesystem_identity(tmp_path: Path) -> None:
    """Ключ — инод, а не строка: строка ошибается на регистре и симлинках.

    `os.path.normcase` для этого не годится и выглядел бы работающим: на POSIX
    это тождественная функция, она понижает регистр только на Windows.
    """
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    assert RUNNER._identity(real) == RUNNER._identity(link)
    assert RUNNER._identity(real) != RUNNER._identity(tmp_path)


def test_identity_falls_back_instead_of_raising(tmp_path: Path) -> None:
    """ФС без осмысленных инодов не должна ронять обход."""
    assert RUNNER._identity(tmp_path / "нет-такого").startswith("path:")


def test_header_must_declare_what_the_records_answer(tmp_path: Path) -> None:
    """Результат по PR, которого нет в заявленном scope, — отказ читателя.

    Раньше это ловил сам раннер («заголовок не заявляет»); теперь — биекция
    scope ↔ results у читателя контракта, и раннер просто доносит её вердикт.
    """
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6), prs=[1], results=[_result(2)])

    outcomes = RUNNER.freshness([_entry(prs=[2])], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "незаявленный элемент" in outcomes[0].detail


def test_mirror_on_another_host_is_not_the_same_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Совпадение `owner/name` не делает зеркало тем же объектом.

    В этом воркспейсе такое есть: atp-platform держит GitHub и GitLab-зеркало
    под одним именем. Факты берутся из GraphQL GitHub, поэтому чекаут зеркала
    наблюдаемым быть не может — иначе бандл про GitHub-репозиторий лёг бы в
    дерево зеркала и выглядел бы законным.
    """
    path = tmp_path / "steward"
    (path / ".git").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", f"git@git.epam.com:{REPO}.git"],
        check=True,
    )
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: pytest.fail("не должен вызываться"))

    outcomes = RUNNER.collect([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "skipped"
    assert "github.com" in outcomes[0].detail


def _preflight_with_origin(tmp_path: Path, url: str) -> "Any":
    """Preflight на чекауте с заданным origin — то, что видит оператор."""
    path = _checkout(tmp_path, "steward", origin=None)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", url], check=True)
    resolved, refusal, _status = RUNNER.preflight(_entry(), tmp_path)
    return resolved, refusal


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:andrei-shtanakov/steward.git",
        "ssh://git@github.com/andrei-shtanakov/steward.git",
        "https://github.com/andrei-shtanakov/steward.git",
        "https://github.com/andrei-shtanakov/steward",
    ],
)
def test_origin_forms_the_producer_accepts_pass_preflight(tmp_path: Path, url: str) -> None:
    """Ровно формы `parse_origin` продюсера — и никакие другие."""
    resolved, refusal = _preflight_with_origin(tmp_path, url)
    assert resolved is not None, refusal


@pytest.mark.parametrize(
    "url",
    [
        # Всё это прежний, более терпимый разбор ПРИНИМАЛ — а продюсер
        # (`publish.py::parse_origin`) отвергает. Зелёный preflight на таком
        # origin означал бы `--check` про расписание, каждый прогон которого
        # падает кодом 2, — установка выглядела бы живой, не работая никогда.
        "git@ssh.github.com:andrei-shtanakov/steward.git",
        "https://www.github.com/andrei-shtanakov/steward.git",
        "https://user@github.com/andrei-shtanakov/steward",
        "https://github.com/mirror/andrei-shtanakov/steward.git",
        "git@git.epam.com:andrei-shtanakov/steward.git",
        "не-url",
    ],
)
def test_origin_forms_the_producer_rejects_fail_preflight(tmp_path: Path, url: str) -> None:
    resolved, refusal = _preflight_with_origin(tmp_path, url)
    assert resolved is None
    assert "принимает продюсер" in refusal


def test_pipe_is_listed_among_forbidden_path_characters() -> None:
    """`|` — сам разделитель в `sed -e "s|...|...|g"`, и путь с ним порвёт подстановку."""
    doc = (
        Path(__file__).resolve().parents[2] / "scripts" / "approval-facts-schedule.md"
    ).read_text(encoding="utf-8")
    constraint = doc[doc.index("Ограничение путей") : doc.index("Установка.")]

    assert "`|`" in constraint


def test_bundle_of_another_repository_is_failed(tmp_path: Path) -> None:
    """Чужой бандл в этом чекауте несёт валидный lease и записи — и неотличим
    по всем прочим признакам. Отличает только заявленный `repository`."""
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6))
    bundle = path / ".steward" / "approval_facts.jsonl"
    lines = bundle.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    header["repository"] = "andrei-shtanakov/maestro"
    bundle.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n", encoding="utf-8")

    outcomes = RUNNER.freshness([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "не совпадает с ожидаемым" in outcomes[0].detail


def test_header_without_scope_digest_is_failed(tmp_path: Path) -> None:
    """`scope_sha256` проверяется на наличие, но не пересчитывается.

    Канонизация — инвариант продюсера, у него же и покрыта. Второй реализацией
    того же дайджеста мы завели бы пару, которая расходится молча — ровно тот
    класс, который эта ветка чинила трижды.
    """
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6))
    bundle = path / ".steward" / "approval_facts.jsonl"
    lines = bundle.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    del header["scope_sha256"]
    bundle.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n", encoding="utf-8")

    assert RUNNER.freshness([_entry()], tmp_path, _policy(tmp_path))[0].status == "failed"


def test_slug_comparison_is_case_insensitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub не различает регистр в слагах, значит и мы не должны."""
    path = tmp_path / "steward"
    (path / ".git").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "remote",
            "add",
            "origin",
            "git@github.com:Andrei-Shtanakov/Steward.git",
        ],
        check=True,
    )
    monkeypatch.setattr(RUNNER, "run_producer", _publishing_producer())

    assert RUNNER.collect([_entry()], tmp_path, _policy(tmp_path))[0].status == "published"


def test_ssh_alias_is_refused_with_an_actionable_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ssh-алиас не поддерживается, и это названное ограничение.

    Прежняя попытка разрешить его полем `origin_host` снимала защиту от зеркал
    для ЛЮБОГО хоста, то есть расширяла дыру вместо закрытия. Отказ обязан
    называть лекарство, иначе репозиторий молча выпадет из наблюдения.
    """
    path = tmp_path / "steward"
    (path / ".git").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", f"git@github-work:{REPO}.git"],
        check=True,
    )
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: pytest.fail("не должен вызываться"))

    outcomes = RUNNER.collect([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "skipped"
    assert "github.com" in outcomes[0].detail


def test_repository_comparison_is_case_insensitive(tmp_path: Path) -> None:
    """Preflight уже признал слаг регистронезависимым — заголовок не должен спорить."""
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6))
    bundle = path / ".steward" / "approval_facts.jsonl"
    lines = bundle.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    header["repository"] = "Andrei-Shtanakov/Steward"
    bundle.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n", encoding="utf-8")

    assert RUNNER.freshness([_entry()], tmp_path, _policy(tmp_path))[0].status == "published"


def test_narrowed_scope_is_not_green_on_the_old_wider_bundle(tmp_path: Path) -> None:
    """Сужение охвата тоже событие: старый широкий бандл его не доказывает.

    «Не меньше нужного» пропускало бы такой бандл, хотя текущий охват не
    собирался ни разу.
    """
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6), prs=[1, 2])

    outcomes = RUNNER.freshness([_entry(prs=[1])], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "лишние PR" in outcomes[0].detail


def test_broken_entry_does_not_occupy_the_checkout_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Битая первая запись не должна подавлять рабочую вторую для того же пути.

    Дедупликация шла раньше валидации охвата, поэтому негодная строка
    застолбляла чекаут — и репозиторий оставался несобранным при внешне
    объяснённом отказе.
    """
    _checkout(tmp_path, "steward")
    monkeypatch.setattr(RUNNER, "run_producer", _publishing_producer())

    outcomes = RUNNER.collect(
        [
            {"repo": REPO, "checkout": "steward", "prs": ["мусор"]},
            {"repo": REPO, "checkout": "steward", "prs": [1]},
        ],
        tmp_path,
        _policy(tmp_path),
    )

    assert [o.status for o in outcomes] == ["skipped", "published"]


def test_unresolvable_scope_path_is_config_error_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`Path.resolve()` бросает на циклическом симлинке — до отчёта не дошло бы."""
    monkeypatch.setattr(RUNNER, "_resolve", lambda path: (None, "ELOOP"))

    assert RUNNER.main(["--workspace-root", str(tmp_path)]) == 2


def test_stale_records_outside_the_scope_are_failed(tmp_path: Path) -> None:
    """Заголовок можно переписать, а хвост старых ответов — остаться.

    Тогда бандл содержит ответы, которых текущий охват не запрашивал, и
    проверка заголовка одна этого не видит.
    """
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6), prs=[1])
    bundle = path / ".steward" / "approval_facts.jsonl"
    with bundle.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_result(77)) + "\n")

    outcomes = RUNNER.freshness([_entry(prs=[1])], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    # Запись вне заявленного scope режет биекция читателя — до раннерской
    # проверки «лишних PR» такой файл не доходит.
    assert "незаявленный элемент" in outcomes[0].detail


def test_concatenated_bundle_is_failed(tmp_path: Path) -> None:
    """Второй заголовок — склейка двух бандлов.

    Раньше побеждал последний, и файл «доказывал» охват той половины, которая
    оказалась ниже.
    """
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6), prs=[1])
    bundle = path / ".steward" / "approval_facts.jsonl"
    doubled = bundle.read_text(encoding="utf-8")
    bundle.write_text(doubled + doubled, encoding="utf-8")

    outcomes = RUNNER.freshness([_entry(prs=[1])], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "header обязан присутствовать ровно один раз, найдено 2" in outcomes[0].detail


def test_duplicate_answer_for_one_pr_is_failed(tmp_path: Path) -> None:
    """Два ответа на один запрос — противоречие внутри файла."""
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6), prs=[1])
    bundle = path / ".steward" / "approval_facts.jsonl"
    with bundle.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_result(1, state="not_merged")) + "\n")

    outcomes = RUNNER.freshness([_entry(prs=[1])], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "повторный результат" in outcomes[0].detail


def test_scope_digest_mismatch_is_failed(tmp_path: Path) -> None:
    """Раннер не должен звать `published` бандл, который читатель отвергнет.

    `reader.py` сверяет `scope_sha256` с `scope`. Проверка наличия, стоявшая
    здесь раньше, этого не ловила — а вторую реализацию канонизации писать
    было нельзя. Верным был не отказ от проверки, а переиспользование ТОЙ ЖЕ
    функции продюсера.
    """
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6), prs=[1])
    bundle = path / ".steward" / "approval_facts.jsonl"
    lines = bundle.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    header["scope_sha256"] = "sha256:" + "0" * 64
    bundle.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n", encoding="utf-8")

    outcomes = RUNNER.freshness([_entry(prs=[1])], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "scope_sha256" in outcomes[0].detail


def test_concurrent_run_is_refused_not_credited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Параллельный прогон создаёт моя же инструкция: `load` запускает задачу по
    `RunAtLoad`, а следом рекомендуется `kickstart`. Без замка чужая публикация
    засчиталась бы этому прогону — «файл изменился» не доказывает, кто его писал.
    """
    path = _checkout(tmp_path, "steward")
    bundle = path / RUNNER.BUNDLE_RELPATH
    held, _, _ = RUNNER._claim(bundle)
    assert held is not None
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: pytest.fail("не должен вызываться"))

    outcomes = RUNNER.collect([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "skipped"
    assert "другой прогон" in outcomes[0].detail


def test_lock_is_released_after_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Незанятый замок иначе заблокировал бы чекаут до истечения часа."""
    path = _checkout(tmp_path, "steward")
    monkeypatch.setattr(RUNNER, "run_producer", _publishing_producer())

    RUNNER.collect([_entry()], tmp_path, _policy(tmp_path))

    lock = (path / RUNNER.BUNDLE_RELPATH).with_suffix(".jsonl.lock")
    assert not lock.exists()


def test_stale_lock_is_reclaimed(tmp_path: Path) -> None:
    """Убитый прогон не должен блокировать чекаут навсегда: расписание молча
    перестало бы работать, а причина не была бы видна нигде."""
    path = _checkout(tmp_path, "steward")
    bundle = path / RUNNER.BUNDLE_RELPATH
    lock, _, _ = RUNNER._claim(bundle)
    assert lock is not None
    import os as _os

    ancient = RUNNER.time.time() - 7200
    _os.utime(lock, (ancient, ancient))

    assert RUNNER._claim(bundle)[0] is not None


def test_result_without_state_is_not_coverage(tmp_path: Path) -> None:
    """Строка `result` без `state` — эхо запроса, а не факт.

    Считать её покрытием значило бы звать `published` файл, в котором
    потреблять нечего.
    """
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6), prs=[95])
    bundle = path / ".steward" / "approval_facts.jsonl"
    lines = bundle.read_text(encoding="utf-8").splitlines()
    echo = {"kind": "result", "request": {"kind": "pr", "value": 95}}
    bundle.write_text(lines[0] + "\n" + json.dumps(echo) + "\n", encoding="utf-8")

    outcomes = RUNNER.freshness([_entry(prs=[95])], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "не хватает обязательных полей" in outcomes[0].detail


def test_unwritable_lock_is_not_reported_as_concurrency(tmp_path: Path) -> None:
    """Недоступный каталог — не параллельный прогон.

    Раньше любая `OSError` докладывалась как второй процесс: оператор искал бы
    несуществующий, а расписание стояло бы неизвестно сколько.
    """
    blocked = tmp_path / "ro" / RUNNER.BUNDLE_RELPATH
    (tmp_path / "ro").mkdir()
    (tmp_path / "ro").chmod(0o500)
    try:
        lock, refusal, status = RUNNER._claim(blocked)
    finally:
        (tmp_path / "ro").chmod(0o700)

    assert lock is None
    assert "не взять" in refusal
    assert status == "failed", "поломка инструмента — не «не спрашивали»"


def test_git_is_invoked_by_absolute_path() -> None:
    """Голое имя было бы той же ошибкой, от которой plist защищается `uv`."""
    assert Path(RUNNER.GIT_BIN).is_absolute()


def test_lock_error_is_failed_not_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Поломка инструмента — не «не спрашивали».

    `skipped` означает решение не спрашивать; отказ взять замок из-за
    недоступного каталога — это невозможность спросить, и читаться она должна
    как провал.
    """
    _checkout(tmp_path, "steward")
    monkeypatch.setattr(RUNNER, "_claim", lambda bundle: (None, "диск полон", "failed"))

    outcomes = RUNNER.collect([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"


def test_concurrency_is_skipped_not_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """А вот параллельный прогон — именно `skipped`: спрашивает кто-то другой."""
    path = _checkout(tmp_path, "steward")
    held, _, _ = RUNNER._claim(path / RUNNER.BUNDLE_RELPATH)
    assert held is not None
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: pytest.fail("не должен вызываться"))

    assert RUNNER.collect([_entry()], tmp_path, _policy(tmp_path))[0].status == "skipped"


def test_install_check_does_not_kill_a_running_collection() -> None:
    """`kickstart -k` убил бы прогон, запущенный `RunAtLoad`, и оставил замок.

    Тот перехватывается только через час, так что совет «проверьте установку»
    сам ломал бы сбор. И смотреть надо оба лога: штатный вывод идёт в out.log.
    """
    doc = (
        Path(__file__).resolve().parents[2] / "scripts" / "approval-facts-schedule.md"
    ).read_text(encoding="utf-8")

    assert "kickstart -k" not in doc
    assert "out.log" in doc and "err.log" in doc


def test_mirror_path_with_extra_segments_is_not_the_same_repo(tmp_path: Path) -> None:
    """`https://github.com/mirror/owner/repo` — НЕ `owner/repo`.

    Суффиксный разбор («последние два сегмента») принимал такой remote за
    настоящий репозиторий, и раннер записывал бы факты `owner/repo` в чужое
    дерево. Канонический разбор (`publish.py::parse_origin`) лишних сегментов
    не терпит — хелпер раннера не имеет права быть мягче.
    """
    path = _checkout(tmp_path, "steward", origin=None)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "remote",
            "add",
            "origin",
            f"https://github.com/mirror/{REPO}.git",
        ],
        check=True,
    )

    outcomes = RUNNER.freshness([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "skipped"
    assert "origin" in outcomes[0].detail


def test_stale_policy_digest_makes_check_red(tmp_path: Path) -> None:
    """Бандл под сменившейся политикой не проходит `--check`.

    Прямой регресс находки первого зрячего ревью: `freshness()` смотрел только
    на срок и покрытие, и зелёный `--check` благословлял бандл, который гейт
    отверг бы как `policy_digest_mismatch`. Сценарий буквальный: собрали в
    08:00, в 10:00 поменяли `approval-policy.yaml`, в 10:05 `--check`.
    """
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6))
    policy = _policy(tmp_path)
    policy.write_text(POLICY_TEXT + "# правка после публикации\n", encoding="utf-8")

    outcomes = RUNNER.freshness([_entry()], tmp_path, policy)

    assert outcomes[0].status == "failed"
    assert "под другой политикой" in outcomes[0].detail


def test_lease_length_must_match_the_active_policy(tmp_path: Path) -> None:
    """Длительность lease в бандле сверяется с активной политикой точно.

    Потребитель (`resolve_facts`, «строка 4») требует РАВЕНСТВА; бандл с
    прежней длительностью после смены `approval_facts_lease_seconds` гейт не
    примет, и `--check` не имеет права звать его установленным.
    """
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6))
    policy = _policy(tmp_path)
    # Дайджест в бандле должен сойтись, а lease — нет: перепишем политику с
    # другой длительностью и подделаем дайджест бандла под неё.
    changed = POLICY_TEXT.replace(
        f"approval_facts_lease_seconds: {LEASE_SECONDS}",
        "approval_facts_lease_seconds: 43200",
    )
    policy.write_text(changed, encoding="utf-8")
    bundle = path / ".steward" / "approval_facts.jsonl"
    lines = bundle.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    header["policy_digest"] = "sha256:" + __import__("hashlib").sha256(changed.encode()).hexdigest()
    bundle.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n", encoding="utf-8")

    outcomes = RUNNER.freshness([_entry()], tmp_path, policy)

    assert outcomes[0].status == "failed"
    assert "в активной политике 43200s" in outcomes[0].detail


def test_duplicate_pr_numbers_in_scope_are_refused(tmp_path: Path) -> None:
    """`prs: [74, 74]` — отказ охвата, а не «одна запись покрывает оба».

    Продюсер (`parse_scope`) отвергает дублирующийся охват целиком, каждый
    плановый прогон падал бы кодом 2. Пропустить дубль здесь значило бы
    зеленеть `--check`'ом про расписание, которое не работает никогда, — тот
    же класс, что терпимый разбор origin.
    """
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6), prs=[74])

    outcomes = RUNNER.freshness([_entry(prs=[74, 74])], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "skipped"
    assert "повторяется" in outcomes[0].detail


def test_release_removes_own_lock(tmp_path: Path) -> None:
    bundle = tmp_path / ".steward" / "approval_facts.jsonl"
    lock, token, _ = RUNNER._claim(bundle)
    assert lock is not None

    RUNNER._release(lock, token)

    assert not lock.exists()


def test_release_leaves_a_foreign_lock_in_place(tmp_path: Path) -> None:
    """Проснувшийся прогон не снимает замок, перехваченный после его протухания.

    Безусловный `unlink` при снятии открывал цепочку: A уснул дольше порога,
    B перехватил протухший замок, проснувшийся A снял ЗАМОК B — и C вошёл
    параллельно с B в один бандл. Оба могли отчитаться `published`.
    """
    bundle = tmp_path / ".steward" / "approval_facts.jsonl"
    lock, token_a, _ = RUNNER._claim(bundle)
    assert lock is not None
    # B перехватил: содержимое замка теперь чужое.
    lock.write_text("other-owner", encoding="ascii")

    RUNNER._release(lock, token_a)

    assert lock.exists()
    assert lock.read_text(encoding="ascii") == "other-owner"


def test_check_names_the_in_progress_window(tmp_path: Path) -> None:
    """Нет бандла + живой замок = «публикация в процессе», а не «бандла нет».

    Продюсер снимает прежнюю публикацию ДО записи новой (`remove_previous`),
    так что у штатного сбора есть окно без файла. Исход остаётся `failed` —
    свежесть в этот момент недоказуема, — но сообщение обязано называть
    происходящее, иначе оператор ищет потерю данных там, где идёт запись.
    """
    path = _checkout(tmp_path, "steward")
    (path / ".steward").mkdir(parents=True)
    (path / ".steward" / "approval_facts.jsonl.lock").write_text("w", encoding="ascii")

    outcomes = RUNNER.freshness([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "публикация в процессе" in outcomes[0].detail


def test_check_reports_loss_when_the_lock_is_stale(tmp_path: Path) -> None:
    """Протухший замок не маскирует настоящую потерю бандла."""
    path = _checkout(tmp_path, "steward")
    (path / ".steward").mkdir(parents=True)
    lock = path / ".steward" / "approval_facts.jsonl.lock"
    lock.write_text("w", encoding="ascii")
    two_hours_ago = time.time() - 7200
    os.utime(lock, (two_hours_ago, two_hours_ago))

    outcomes = RUNNER.freshness([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "бандла нет" in outcomes[0].detail


def test_lock_staleness_is_derived_from_the_producer_bound(tmp_path: Path) -> None:
    """Порог протухания замка выведен из границы продюсера, не выбран отдельно.

    Разрыв между независимыми константами стоил дорого: продюсер ограничен
    десятью минутами, а замок протухал час — убитый после `remove_previous`
    прогон оставлял чекаут без бандла и без писателя, и до пятидесяти минут
    восстановление не начиналось. Тест закрепляет саму связь: кто меняет
    таймаут продюсера, тот двигает и порог замка.
    """
    assert RUNNER.LOCK_STALE_SECONDS == 2 * RUNNER.PRODUCER_TIMEOUT_SECONDS

    bundle = tmp_path / ".steward" / "approval_facts.jsonl"
    bundle.parent.mkdir(parents=True)
    lock = tmp_path / ".steward" / "approval_facts.jsonl.lock"
    lock.write_text("dead-run", encoding="ascii")
    just_stale = time.time() - RUNNER.LOCK_STALE_SECONDS - 5
    os.utime(lock, (just_stale, just_stale))

    held, _, _ = RUNNER._claim(bundle)

    assert held is not None, "замок старше выведенного порога обязан перехватываться"


def test_bundle_with_non_pr_scope_is_not_the_configured_collection(tmp_path: Path) -> None:
    """Бандл со scope шире PR (ручной `--merge-sha`) не проходит за настроенный.

    Проверка одних PR-подмножеств зеленила бы его: все настроенные PR отвечены,
    лишних PR нет — а бандл собран не из A0-охвата. `--check` обязан отличать
    «расписание работает» от «кто-то опубликовал вручную с другим охватом».
    """
    path = _checkout(tmp_path, "steward")
    sha = "a" * 40
    scope = [{"kind": "pr", "value": 1}, {"kind": "merge_sha", "value": sha}]
    digest = scope_digest([RequestId(kind=i["kind"], value=i["value"]) for i in scope])
    _bundle(
        path,
        datetime.now(UTC) + timedelta(hours=6),
        prs=[1],
        results=[
            _result(1),
            {
                "kind": "result",
                "request": {"kind": "merge_sha", "value": sha},
                "state": "no_matching_pr",
                "merge_sha": None,
            },
        ],
        header_overrides={"scope": scope, "scope_sha256": digest},
    )

    outcomes = RUNNER.freshness([_entry(prs=[1])], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "вне A0-охвата" in outcomes[0].detail


def test_unsupported_scope_version_is_a_config_error(tmp_path: Path) -> None:
    """`version: 2` — отказ, а не молчаливое чтение по правилам версии 1."""
    (tmp_path / "scope.yaml").write_text(
        json.dumps({"version": 2, "repositories": [_entry()]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="не поддерживается"):
        RUNNER._load_scope(tmp_path / "scope.yaml")


def test_unknown_entry_field_is_a_config_error(tmp_path: Path) -> None:
    """`merge_shas:` в записи — отказ файла, а не молчаливый PR-only сбор.

    Незнакомое поле охвата — не «лишнее», а ЗАПРОШЕННОЕ и не исполненное:
    прочитав одни `prs`, раннер отдал бы `published` про охват, половину
    которого никто не собирал.
    """
    entry = dict(_entry(), merge_shas=["a" * 40])
    (tmp_path / "scope.yaml").write_text(
        json.dumps({"version": 1, "repositories": [entry]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="merge_shas"):
        RUNNER._load_scope(tmp_path / "scope.yaml")


def test_policy_change_during_collection_is_not_a_false_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Правка политики между стартом обхода и продюсером — не провал публикации.

    Продюсер перечитывает файл политики сам; сверять его результат со
    снапшотом, взятым ДО него, значило бы провалить честную публикацию под
    новой политикой. Снапшот берётся после продюсера — в том же порядке, в
    котором читал бы гейт.
    """
    _checkout(tmp_path, "steward")
    policy = _policy(tmp_path)
    changed = POLICY_TEXT + "# правка в середине обхода\n"
    changed_digest = "sha256:" + __import__("hashlib").sha256(changed.encode()).hexdigest()

    def producer_after_policy_edit(
        repo: str, root: Path, pol: Path, prs: list[int]
    ) -> tuple[int, str]:
        policy.write_text(changed, encoding="utf-8")
        _bundle(root, datetime.now(UTC) + timedelta(hours=6), prs=prs)
        bundle = root / RUNNER.BUNDLE_RELPATH
        lines = bundle.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        header["policy_digest"] = changed_digest
        bundle.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n", encoding="utf-8")
        return 0, ""

    monkeypatch.setattr(RUNNER, "run_producer", producer_after_policy_edit)

    outcomes = RUNNER.collect([_entry()], tmp_path, policy)

    assert outcomes[0].status == "published", outcomes[0].detail


def test_nested_checkout_sharing_a_bundle_file_is_a_duplicate(tmp_path: Path) -> None:
    """Два чекаута, чьи бандлы резолвятся в ОДИН файл, — дубликат, а не пара.

    Идентичность каталога чекаута этого не ловит: `outer/.steward` симлинком на
    `outer/inner/.steward` даёт разные каталоги (разные иноды), `_inside`
    доволен (inner внутри outer), а файл бандла один — вторая публикация молча
    затирала бы первую, обе отчитываясь `published`. Ключ дубликатов обязан
    включать резолвнутый путь самого файла.
    """
    outer = _checkout(tmp_path, "outer")
    inner = _checkout(tmp_path / "outer", "inner")
    (inner / ".steward").mkdir()
    (outer / ".steward").symlink_to(inner / ".steward", target_is_directory=True)
    _bundle(inner, datetime.now(UTC) + timedelta(hours=6))

    outcomes = RUNNER.freshness(
        [
            {"repo": REPO, "checkout": "outer/inner", "prs": [1]},
            {"repo": REPO, "checkout": "outer", "prs": [1]},
        ],
        tmp_path,
        _policy(tmp_path),
    )

    statuses = [o.status for o in outcomes]
    assert statuses[0] == "published"
    assert statuses[1] == "skipped"
    assert "уже занят" in outcomes[1].detail


def test_git_probe_timeout_is_failed_not_no_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Зависший или отсутствующий git — отказ инструмента, не «нет origin».

    `_run` схлопывал таймаут и ENOENT в None, и preflight читал это как факт о
    чекауте: репозиторий числился `skipped` с сообщением про origin, хотя
    опросить его не удалось вовсе. Тот же класс, что уже чинился на замке:
    поломка прибора не должна выглядеть свойством объекта.
    """
    _checkout(tmp_path, "steward")
    monkeypatch.setattr(RUNNER, "_run", lambda *a, **k: None)

    outcomes = RUNNER.freshness([_entry()], tmp_path, _policy(tmp_path))

    assert outcomes[0].status == "failed"
    assert "не смог опросить" in outcomes[0].detail
    assert "нет origin" not in outcomes[0].detail
