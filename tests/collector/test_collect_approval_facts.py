"""Раннер сбора `approval-facts` — маршрутизация, preflight, агрегация.

Логики фактов здесь нет и быть не должно: раннер решает только, тот ли это
чекаут и кого звать. Соответственно и тесты — про отказ дойти до записи, а не
про содержание бандла.
"""

from __future__ import annotations

import importlib.util
import json
import xml.dom.minidom
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest


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
    """

    def fake(repo: str, root: Path, policy: Path, prs: list[int]) -> tuple[int, str]:
        if prs_by_call is not None:
            prs_by_call.append(prs)
        bundle = root / RUNNER.BUNDLE_RELPATH
        bundle.parent.mkdir(parents=True, exist_ok=True)
        header = {
            "kind": "header",
            "valid_until": (datetime.now(UTC) + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "repository": REPO,
            "scope_sha256": "sha256:" + "0" * 64,
            "scope": [{"kind": "pr", "value": str(n)} for n in prs],
        }
        lines = [json.dumps(header)] + [
            json.dumps(
                {"kind": "result", "request": {"kind": "pr", "value": str(n)}, "state": "merged"}
            )
            for n in prs
        ]
        bundle.write_text("\n".join(lines) + "\n", encoding="utf-8")
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

    outcomes = RUNNER.collect([_entry("nowhere")], tmp_path, tmp_path / "policy.yaml")

    assert [o.status for o in outcomes] == ["skipped"]
    assert "чекаута нет" in outcomes[0].detail
    assert called == [], "продюсер не должен вызываться вовсе"


def test_wrong_origin_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Самая опасная ошибка конфигурации: путь есть, git есть, объект не тот.

    Без этой проверки бандл выглядел бы законным — с чужими фактами внутри.
    """
    _checkout(tmp_path, "steward", origin="andrei-shtanakov/maestro")
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: pytest.fail("не должен вызываться"))

    outcomes = RUNNER.collect([_entry()], tmp_path, tmp_path / "policy.yaml")

    assert outcomes[0].status == "skipped"
    assert "origin чекаута" in outcomes[0].detail


def test_checkout_outside_workspace_root_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`..` в охвате не должен выводить запись за пределы наблюдаемого набора."""
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: pytest.fail("не должен вызываться"))
    workspace = tmp_path / "ws"
    workspace.mkdir()

    outcomes = RUNNER.collect([_entry("../elsewhere")], workspace, tmp_path / "policy.yaml")

    assert outcomes[0].status == "skipped"
    assert "вне workspace-root" in outcomes[0].detail


def test_directory_without_git_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "steward").mkdir()
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: pytest.fail("не должен вызываться"))

    outcomes = RUNNER.collect([_entry()], tmp_path, tmp_path / "policy.yaml")

    assert outcomes[0].status == "skipped"
    assert "не git-корень" in outcomes[0].detail


def test_empty_pr_list_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустой охват — не «спросили и ничего не нашли», а «не спрашивали»."""
    _checkout(tmp_path, "steward")
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: pytest.fail("не должен вызываться"))

    outcomes = RUNNER.collect([_entry(prs=[])], tmp_path, tmp_path / "policy.yaml")

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
        tmp_path / "policy.yaml",
    )

    assert [o.status for o in outcomes] == ["skipped", "published"]


def test_producer_failure_is_failed_not_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Разные вещи: «не спросили» и «спросили, не смогли»."""
    _checkout(tmp_path, "steward")
    monkeypatch.setattr(RUNNER, "run_producer", lambda *a: (3, "механический сбой"))

    outcomes = RUNNER.collect([_entry()], tmp_path, tmp_path / "policy.yaml")

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


def _bundle(path: Path, valid_until: datetime, prs: list[int] | None = None) -> None:
    (path / ".steward").mkdir(parents=True, exist_ok=True)
    header = {
        "kind": "header",
        "valid_until": valid_until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        # Заголовок обязан нести охват: `--check` сверяет с ним настроенный,
        # иначе свежий бандл под выросшим охватом читался бы зелёным.
        "repository": REPO,
        "scope_sha256": "sha256:" + "0" * 64,
        "scope": [{"kind": "pr", "value": str(n)} for n in (prs if prs is not None else [1])],
    }
    lines = [json.dumps(header)] + [
        json.dumps(
            {"kind": "result", "request": {"kind": "pr", "value": str(n)}, "state": "merged"}
        )
        for n in (prs if prs is not None else [1])
    ]
    (path / ".steward" / "approval_facts.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def test_expired_lease_is_failed(tmp_path: Path) -> None:
    """Ровно то состояние, в котором единственный бандл флота был найден."""
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) - timedelta(hours=20))

    outcomes = RUNNER.freshness([_entry()], tmp_path)

    assert outcomes[0].status == "failed"
    assert "lease истёк" in outcomes[0].detail


def test_live_lease_is_published(tmp_path: Path) -> None:
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6))

    assert RUNNER.freshness([_entry()], tmp_path)[0].status == "published"


def test_absent_bundle_is_failed_not_silent(tmp_path: Path) -> None:
    _checkout(tmp_path, "steward")

    outcomes = RUNNER.freshness([_entry()], tmp_path)

    assert outcomes[0].status == "failed"
    assert "бандла нет" in outcomes[0].detail


def test_unreadable_header_is_failed(tmp_path: Path) -> None:
    path = _checkout(tmp_path, "steward")
    (path / ".steward").mkdir(parents=True, exist_ok=True)
    (path / ".steward" / "approval_facts.jsonl").write_text("not json\n", encoding="utf-8")

    assert RUNNER.freshness([_entry()], tmp_path)[0].status == "failed"


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

    outcomes = RUNNER.collect(["просто строка"], tmp_path, tmp_path / "policy.yaml")

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

    outcomes = RUNNER.collect([_entry(prs=[value])], tmp_path, tmp_path / "policy.yaml")

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
        tmp_path / "policy.yaml",
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

    outcomes = RUNNER.freshness([_entry()], tmp_path)

    assert outcomes[0].status == "failed"
    assert "часового пояса" in outcomes[0].detail


def test_relative_policy_is_resolved_before_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверка `is_file()` шла в текущем каталоге, а продюсер стартует в другом.

    Относительный путь означал бы там другой файл — preflight зелёный про не тот.
    """
    _checkout(tmp_path, "steward")
    scope = tmp_path / "scope.yaml"
    scope.write_text(json.dumps({"version": 1, "repositories": [_entry()]}), encoding="utf-8")
    policy = tmp_path / "policy.yaml"
    policy.write_text("version: 1\n", encoding="utf-8")
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
        tmp_path / "policy.yaml",
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

    outcomes = RUNNER.freshness([_entry(prs=[1, 2])], tmp_path)

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

    outcomes = RUNNER.collect([_entry()], tmp_path, tmp_path / "policy.yaml")

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

    outcomes = RUNNER.collect([_entry()], tmp_path, tmp_path / "policy.yaml")

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

    assert RUNNER.freshness([_entry(prs=[75])], tmp_path)[0].status == "failed"


def test_non_object_header_is_failed_not_a_traceback(tmp_path: Path) -> None:
    """`[]` — валидный JSON, но не заголовок; индексация давала TypeError."""
    path = _checkout(tmp_path, "steward")
    (path / ".steward").mkdir(parents=True, exist_ok=True)
    (path / ".steward" / "approval_facts.jsonl").write_text("[]\n", encoding="utf-8")

    outcomes = RUNNER.freshness([_entry()], tmp_path)

    assert outcomes[0].status == "failed"
    assert "нечитаем" in outcomes[0].detail


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

    checked = RUNNER.freshness(duplicated, tmp_path)

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

    assert RUNNER.freshness([_entry(prs=[95])], tmp_path)[0].status == "failed"


def test_first_line_without_kind_header_is_failed(tmp_path: Path) -> None:
    """Объект с `valid_until`, но без `kind: header`, — структурно не бандл."""
    path = _checkout(tmp_path, "steward")
    (path / ".steward").mkdir(parents=True, exist_ok=True)
    (path / ".steward" / "approval_facts.jsonl").write_text(
        json.dumps({"valid_until": "2026-12-31T00:00:00Z"}) + "\n", encoding="utf-8"
    )

    outcomes = RUNNER.freshness([_entry()], tmp_path)

    assert outcomes[0].status == "failed"
    assert "нечитаем" in outcomes[0].detail


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

    outcomes = RUNNER.collect([_entry()], tmp_path, tmp_path / "policy.yaml")

    assert outcomes[0].status == "failed"
    assert "просрочен" in outcomes[0].detail


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

    outcomes = RUNNER.collect([_entry()], tmp_path, tmp_path / "policy.yaml")

    assert outcomes[0].status == "failed"
    assert "kind: header" in outcomes[0].detail


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

    collected = RUNNER.collect(scope, tmp_path, tmp_path / "policy.yaml")
    checked = RUNNER.freshness(scope, tmp_path)

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

    outcomes = RUNNER.collect([_entry()], tmp_path, tmp_path / "policy.yaml")

    assert outcomes[0].status == "skipped"
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

    outcomes = RUNNER.collect([_entry()], tmp_path, tmp_path / "policy.yaml")

    assert outcomes[0].status == "failed"
    assert "lease не сдвинулся" in outcomes[0].detail


def test_invalid_prs_is_skipped_in_both_passes(tmp_path: Path) -> None:
    """«Не спрашивали» — `skipped` в обоих проходах, а не `failed` в одном."""
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6))

    checked = RUNNER.freshness([_entry(prs=["abc"])], tmp_path)

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

    outcomes = RUNNER.collect([_entry(prs=[1, 2])], tmp_path, tmp_path / "policy.yaml")

    assert outcomes[0].status == "published"


def test_check_validates_scope_before_bundle_state(tmp_path: Path) -> None:
    """Негодный охват на репо без бандла — `skipped`, а не поломка публикации.

    Порядок проверок обязан совпадать с `collect()`: иначе дефект строки охвата
    докладывается как отказ публикации, которой и не могло быть.
    """
    _checkout(tmp_path, "steward")  # бандла нет вовсе

    outcomes = RUNNER.freshness([_entry(prs=[])], tmp_path)

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
        tmp_path / "policy.yaml",
    )

    assert [o.status for o in outcomes] == ["skipped", "published"]


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
        tmp_path / "policy.yaml",
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
        tmp_path / "policy.yaml",
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
    """Бандл, чей `scope` разошёлся с содержимым, перестаёт доказывать охват."""
    path = _checkout(tmp_path, "steward")
    (path / ".steward").mkdir(parents=True, exist_ok=True)
    header = {
        "kind": "header",
        "valid_until": (datetime.now(UTC) + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repository": REPO,
        "scope_sha256": "sha256:" + "0" * 64,
        "scope": [{"kind": "pr", "value": "1"}],
    }
    record = {"kind": "result", "request": {"kind": "pr", "value": "2"}, "state": "merged"}
    (path / ".steward" / "approval_facts.jsonl").write_text(
        json.dumps(header) + "\n" + json.dumps(record) + "\n", encoding="utf-8"
    )

    outcomes = RUNNER.freshness([_entry(prs=[2])], tmp_path)

    assert outcomes[0].status == "failed"
    assert "не заявляет" in outcomes[0].detail


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

    outcomes = RUNNER.collect([_entry()], tmp_path, tmp_path / "policy.yaml")

    assert outcomes[0].status == "skipped"
    assert "github.com" in outcomes[0].detail


@pytest.mark.parametrize(
    ("url", "host", "slug"),
    [
        ("git@github.com:andrei-shtanakov/steward.git", "github.com", REPO),
        ("https://github.com/andrei-shtanakov/steward.git/", "github.com", REPO),
        ("https://user@github.com/andrei-shtanakov/steward", "github.com", REPO),
        ("git@git.epam.com:andrei-shtanakov/steward.git", "git.epam.com", REPO),
        ("не-url", None, None),
    ],
)
def test_origin_parsing_reports_host_and_slug(url: str, host: str | None, slug: str | None) -> None:
    assert RUNNER._origin_host_and_slug(url) == (host, slug)


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

    outcomes = RUNNER.freshness([_entry()], tmp_path)

    assert outcomes[0].status == "failed"
    assert "заявляет репозиторий" in outcomes[0].detail


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

    assert RUNNER.freshness([_entry()], tmp_path)[0].status == "failed"


def test_ssh_over_443_host_is_accepted() -> None:
    """`ssh.github.com` — штатный обход корпоративных фаерволов, не зеркало."""
    assert RUNNER._origin_host_and_slug("git@ssh.github.com:andrei-shtanakov/steward.git") == (
        "ssh.github.com",
        REPO,
    )
    assert "ssh.github.com" in RUNNER.GITHUB_HOSTS


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

    assert RUNNER.collect([_entry()], tmp_path, tmp_path / "policy.yaml")[0].status == "published"


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

    outcomes = RUNNER.collect([_entry()], tmp_path, tmp_path / "policy.yaml")

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

    assert RUNNER.freshness([_entry()], tmp_path)[0].status == "published"


def test_narrowed_scope_is_not_green_on_the_old_wider_bundle(tmp_path: Path) -> None:
    """Сужение охвата тоже событие: старый широкий бандл его не доказывает.

    «Не меньше нужного» пропускало бы такой бандл, хотя текущий охват не
    собирался ни разу.
    """
    path = _checkout(tmp_path, "steward")
    _bundle(path, datetime.now(UTC) + timedelta(hours=6), prs=[1, 2])

    outcomes = RUNNER.freshness([_entry(prs=[1])], tmp_path)

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
        tmp_path / "policy.yaml",
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
    extra = {"kind": "result", "request": {"kind": "pr", "value": "77"}, "state": "merged"}
    with bundle.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(extra) + "\n")

    outcomes = RUNNER.freshness([_entry(prs=[1])], tmp_path)

    assert outcomes[0].status == "failed"
    assert "вне охвата" in outcomes[0].detail
