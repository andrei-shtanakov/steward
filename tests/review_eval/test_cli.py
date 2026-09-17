"""Тесты `review-eval` CLI: подкоманды, коды выхода, склейка с метриками.

Модель не вызывается ни разу: большинство тестов `run` идут с подменённым
`cli.run_all`, который пишет ровно те же артефакты, что настоящий раннер
(`result.json` + sidecar-ы), — так проверяемой остаётся вся склейка
«артефакты → метрики → три файла → код выхода», а платный путь заменён.
`kit_under_test` подменяется там, где кит как таковой не проверяется.

Отдельно — сквозной тест с **настоящим** `run_all`
(`test_run_end_to_end_with_the_real_runner`): корпус, bare-кэш от фикстурного
репо, подставной каталог кита, чей `local.sh` записывает окружение и пишет
sidecar-ы. Подмены нет нигде, кроме самого кита: раннер создаёт worktree,
запускает кит, классифицирует исход, CLI считает метрики и пишет три файла.
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from steward.review_eval import cli
from steward.review_eval.corpus import (
    append_registry,
    case_material_digest,
    corpus_digest,
    load_case,
    load_corpus,
)
from steward.review_eval.matcher import MATCHER_VERSION, rules_digest
from steward.review_eval.runner import (
    KitUnderTest,
    RunManifest,
    RunResult,
    provider_env_fingerprint,
)

runner = CliRunner()

BASE = "1" * 40
HEAD = "2" * 40
#: merge-base диапазона ревью: её резолвит `resolve_review_base` через API, и
#: она **не равна** `base.sha` PR (после мержа голова базы содержит сам PR).
MERGE_BASE = "3" * 40
#: Шапка формата кита: без неё `draft_case` тело не признаёт (`parse_findings`).
KIT_HEADER = "## Ревью Codex — независимый чек"
VARIANT = "claude:claude-opus-5"


def _case_payload(pr: int, *, status: str = "adjudicated", defects: bool = True) -> dict[str, Any]:
    """Кейс корпуса с одним major-дефектом (или чистый)."""
    payload: dict[str, Any] = {
        "schema": "review-eval-case/v1",
        "case_id": f"andrei-shtanakov.steward-{pr}",
        "repo": "andrei-shtanakov/steward",
        "pr": pr,
        "base_sha": BASE,
        "head_sha": HEAD,
        "class": "defective" if defects else "clean",
        "expected_outcome": "verdict",
        "annotation": {
            "status": status,
            "blocking_complete": True,
            "source": "manual",
            "adjudicated_by": "github:andrei-shtanakov" if status == "adjudicated" else None,
            "adjudicated_at": "2026-09-15" if status == "adjudicated" else None,
        },
        "defects": [],
        "non_defects": [],
        "notes": "",
    }
    if defects:
        payload["defects"] = [
            {
                "id": f"D-andrei-shtanakov.steward-{pr}-1",
                "severity": "major",
                "file": "scripts/review/local.sh",
                "line_hint": 644,
                "scenario": "PATH расширяется для умолчания",
                "evidence": ["scripts/review/local.sh:644"],
                "match": {
                    "files": ["scripts/review/local.sh"],
                    "line_window": 40,
                    "keywords_any": ["path", "расширяется"],
                },
            }
        ]
    return payload


def _corpus(tmp_path: Path, *prs: int, **kwargs: Any) -> Path:
    """Каталог корпуса с зарегистрированными id — валидный для `load_corpus`."""
    directory = tmp_path / "corpus"
    directory.mkdir(parents=True, exist_ok=True)
    for pr in prs:
        payload = _case_payload(pr, **kwargs)
        path = directory / f"andrei-shtanakov.steward-{pr}.yaml"
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8")
    append_registry([load_case(p) for p in sorted(directory.glob("*.yaml"))], directory)
    return directory


def _finding(*, severity: str = "major", line: int = 644) -> dict[str, Any]:
    return {
        "kind": "defect",
        "severity": severity,
        "confidence": "high",
        "title": "PATH расширяется",
        "file": "scripts/review/local.sh",
        "line": line,
        "scenario": "Ревьюер с расширенным PATH",
        "observed_result": "подставной codex побеждает",
        "expected_result": "PATH не расширяется",
        "evidence": [{"file": "scripts/review/local.sh", "line": 644, "reason": "вот тут"}],
    }


def _case_for(case_id: str, *, defects: bool = True) -> Any:
    """`Case` по case_id тестового корпуса (материал совпадает с `_case_payload`).

    `defects` выбирает исходный (на момент прогона) класс материала —
    `defects=False` даёт `class: clean`, как и в `_case_payload`/`_corpus`.
    """
    pr = int(case_id.rsplit("-", 1)[1])
    payload = _case_payload(pr, defects=defects)
    path = Path(tempfile.mkdtemp()) / f"{case_id}.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8")
    return load_case(path)


def _write_run(
    out: Path,
    case_ids: list[str],
    *,
    labels: list[str] | None = None,
    outcome: str = "verdict",
    findings: list[dict[str, Any]] | None = None,
    repetitions: int = 1,
    matcher_version: int = MATCHER_VERSION,
    matcher_rules_digest: str | None = None,
    case_material_defects: bool = True,
) -> RunManifest:
    """Артефакты прогона: `run.json`, `result.json` и sidecar-вердикты.

    Провенанс матчера по умолчанию — **настоящий** (`MATCHER_VERSION`,
    `rules_digest()`): иначе каждый пересчёт упирался бы в проверку дрейфа, и
    тесты проверяли бы её вместо того, что им нужно. Дрейф задаётся явно.

    `case_material_defects` — исходный (на момент прогона) класс материала,
    зашитый в `case_digests` манифеста; по умолчанию `True` (`defective`), как
    и раньше. Тест перехода `clean` → `defective` ставит `False`, чтобы
    манифест нёс дайджест ЧИСТОГО кейса, а корпус потом правился разметчиком.
    """
    labels = labels or [VARIANT]
    for case_id in case_ids:
        for label in labels:
            for rep in range(1, repetitions + 1):
                rep_dir = out / "cases" / case_id / label / str(rep)
                rep_dir.mkdir(parents=True, exist_ok=True)
                verdict_written = outcome == "verdict"
                if verdict_written:
                    (rep_dir / "verdict.json").write_text(
                        json.dumps(
                            {"note": "n", "findings": findings if findings is not None else []}
                        ),
                        "utf-8",
                    )
                (rep_dir / "stdout.txt").write_text("", "utf-8")
                (rep_dir / "stderr.txt").write_text("", "utf-8")
                relative = f"cases/{case_id}/{label}/{rep}"
                result = RunResult(
                    case_id=case_id,
                    variant=label,
                    repetition_id=rep,
                    exit_code=0 if outcome == "verdict" else 2,
                    outcome=outcome,
                    reviewer_ran=verdict_written,
                    wall_clock_s=1.5,
                    verdict_path=f"{relative}/verdict.json" if verdict_written else None,
                    usage_path=None,
                    cost_status="unavailable",
                    requested_effort=None,
                    stdout_path=f"{relative}/stdout.txt",
                    stderr_path=f"{relative}/stderr.txt",
                    unexpected=outcome != "verdict",
                )
                (rep_dir / "result.json").write_text(
                    json.dumps(dataclasses.asdict(result)), "utf-8"
                )
    manifest = RunManifest(
        run_id="20260915T000000Z-deadbeef",
        # Манифест обязан нести и дайджесты кита, и версии инструментов, и
        # дайджест конфига git по каждому репо: без них провенанс результатов
        # неизвестен, и `load_results` их не читает.
        kit={"commit": "c" * 40, "local_sh_sha256": "a" * 64},
        tools={"git": "git version 2.0", "claude": "claude 1.0", "codex": "unavailable"},
        git_config_digests={"andrei-shtanakov/steward": "sha256:" + "b" * 64},
        case_digests={
            case_id: case_material_digest(_case_for(case_id, defects=case_material_defects))
            for case_id in case_ids
        },
        variants=[
            {
                "label": label,
                "harness": "claude",
                "model": "claude-opus-5",
                "requested_effort": None,
            }
            for label in labels
        ],
        corpus_digest="sha256:" + "0" * 64,
        matcher_version=matcher_version,
        matcher_rules_digest=(
            rules_digest() if matcher_rules_digest is None else matcher_rules_digest
        ),
        started="2026-09-15T00:00:00Z",
        finished="2026-09-15T00:01:00Z",
        jobs=1,
        repetitions=repetitions,
        # Манифест обязан объявлять состав прогона: `load_results` сверяет с
        # ним каждый результат и требует полноты произведения
        # «кейсы × варианты × повторения».
        cases=sorted(case_ids),
        provider_env_names=[],
        provider_env_fingerprint=provider_env_fingerprint({}),
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "run.json").write_text(json.dumps(dataclasses.asdict(manifest)), "utf-8")
    return manifest


@pytest.fixture
def stub_kit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KitUnderTest:
    """Кит под измерением без настоящего чекаута steward."""
    kit_dir = tmp_path / "kit"
    kit_dir.mkdir()
    kit = KitUnderTest(
        kit_dir=kit_dir,
        prompt=kit_dir / "prompt.md",
        schema=kit_dir / "schema.json",
        commit="c" * 40,
        digests={"prompt_sha256": "a" * 64},
    )
    monkeypatch.setattr(cli, "kit_under_test", lambda *_args, **_kwargs: kit)
    return kit


@pytest.fixture
def captured_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Подмена `run_all`: пишет артефакты как настоящий раннер, запоминает аргументы."""
    captured: dict[str, Any] = {}

    def fake_run_all(cases: Any, variants: Any, **kwargs: Any) -> RunManifest:
        captured["cases"] = [case.case_id for case in cases]
        captured["variants"] = [v for v in variants]
        captured.update(kwargs)
        labels = [f"{v.harness}:{v.model}" + (f":{v.effort}" if v.effort else "") for v in variants]
        return _write_run(
            kwargs["out_dir"],
            captured["cases"],
            labels=labels,
            findings=captured.get("findings", [_finding()]),
            repetitions=kwargs.get("repetitions", 1),
        )

    monkeypatch.setattr(cli, "run_all", fake_run_all)
    return captured


# ---------------------------------------------------------------------------
# corpus validate
# ---------------------------------------------------------------------------


def test_corpus_validate_reports_a_valid_corpus(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, 155)
    result = runner.invoke(cli.app, ["corpus", "validate", "--corpus", str(corpus)])
    assert result.exit_code == 0, result.output
    assert "кейсов: 1 (gold: 1, draft: 0)" in result.output
    assert corpus_digest(load_corpus(corpus)) in result.output


def test_corpus_validate_exits_2_on_a_missing_git_binary(tmp_path: Path) -> None:
    """`--git` пред-проверяется у `corpus validate` так же, как у остальных
    команд — раньше у неё не было флага вовсе, и append-only проверка
    реестра всегда шла умолчанием `"git"` из PATH.
    """
    corpus = _corpus(tmp_path, 155)

    result = runner.invoke(
        cli.app,
        ["corpus", "validate", "--corpus", str(corpus), "--git", "/nonexistent/git"],
    )

    assert result.exit_code == 2, result.output
    assert "--git" in result.output
    assert "/nonexistent/git" in result.output


def test_corpus_validate_passes_git_through_to_load_corpus_and_append_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--git` доходит до `load_corpus`/`append_registry`, а не только до
    предпроверки `_require_git`: раньше append-only проверка истории
    `_ids.txt` всегда шла непроверенным умолчанием `"git"` из PATH,
    независимо от `--git` (ревью-находка части 3).
    """
    corpus = _corpus(tmp_path, 155)
    git_path = shutil.which("git")
    assert git_path is not None
    real_load_corpus = cli.load_corpus
    real_append_registry = cli.append_registry
    load_corpus_calls: list[dict[str, Any]] = []
    append_registry_calls: list[dict[str, Any]] = []

    def load_corpus_spy(directory: Path, **kwargs: Any) -> Any:
        load_corpus_calls.append(kwargs)
        return real_load_corpus(directory, **kwargs)

    def append_registry_spy(cases: Any, directory: Path, **kwargs: Any) -> Any:
        append_registry_calls.append(kwargs)
        return real_append_registry(cases, directory, **kwargs)

    monkeypatch.setattr(cli, "load_corpus", load_corpus_spy)
    monkeypatch.setattr(cli, "append_registry", append_registry_spy)

    result = runner.invoke(
        cli.app,
        ["corpus", "validate", "--corpus", str(corpus), "--register", "--git", git_path],
    )

    assert result.exit_code == 0, result.output
    assert load_corpus_calls and load_corpus_calls[0].get("git") == git_path
    assert append_registry_calls and append_registry_calls[0].get("git") == git_path


def test_corpus_validate_exits_2_on_an_unregistered_id(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, 155)
    (corpus / "_ids.txt").write_text("", encoding="utf-8")
    result = runner.invoke(cli.app, ["corpus", "validate", "--corpus", str(corpus)])
    assert result.exit_code == 2
    assert "не зарегистрирован" in result.output


def test_corpus_validate_register_registers_then_revalidates(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, 155)
    (corpus / "_ids.txt").write_text("", encoding="utf-8")
    result = runner.invoke(cli.app, ["corpus", "validate", "--corpus", str(corpus), "--register"])
    assert result.exit_code == 0, result.output
    assert "D-andrei-shtanakov.steward-155-1" in (corpus / "_ids.txt").read_text(encoding="utf-8")


def test_corpus_validate_reregisters_an_edited_defect(tmp_path: Path) -> None:
    """Правка размеченного дефекта чинится `--register`, а не переписыванием id.

    Рабочий цикл разметчика: правка → `validate` падает кодом 2 и называет
    `--register` → `validate --register` → корпус снова валиден. Реестр при
    этом остаётся append-only: у id появляется вторая строка.
    """
    corpus = _corpus(tmp_path, 155)
    payload = _case_payload(155)
    payload["defects"][0]["match"]["line_window"] = 10
    (corpus / "andrei-shtanakov.steward-155.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8"
    )

    stale = runner.invoke(cli.app, ["corpus", "validate", "--corpus", str(corpus)])
    assert stale.exit_code == 2
    assert "--register" in stale.output

    fixed = runner.invoke(cli.app, ["corpus", "validate", "--corpus", str(corpus), "--register"])
    assert fixed.exit_code == 0, fixed.output

    lines = [
        line
        for line in (corpus / "_ids.txt").read_text(encoding="utf-8").splitlines()
        if line.startswith("D-andrei-shtanakov.steward-155-1 ")
    ]
    assert len(lines) == 2


def test_corpus_validate_exits_2_on_a_broken_case(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, 155)
    (corpus / "andrei-shtanakov.steward-155.yaml").write_text("schema: wrong\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["corpus", "validate", "--corpus", str(corpus)])
    assert result.exit_code == 2
    assert "corpus invalid" in result.output


def test_corpus_validate_refuses_a_duplicate_yaml_key(tmp_path: Path) -> None:
    """Повторный ключ в YAML кейса — отказ: CLI читает строгим загрузчиком корпуса.

    `yaml.safe_load` оставил бы **последнее** значение молча, то есть второй
    `defects:` стёр бы первый, и корпус утверждал бы не то, что написал
    разметчик. CLI своего чтения YAML не имеет вовсе — только через
    `corpus.load_case`, где загрузчик строгий.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    payload = _case_payload(155)
    text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    (corpus / "andrei-shtanakov.steward-155.yaml").write_text(
        text + "defects: []\n", encoding="utf-8"
    )

    plain = runner.invoke(cli.app, ["corpus", "validate", "--corpus", str(corpus)])
    registering = runner.invoke(
        cli.app, ["corpus", "validate", "--corpus", str(corpus), "--register"]
    )

    assert plain.exit_code == 2
    assert registering.exit_code == 2, registering.output
    assert "повторный ключ" in registering.output


def test_corpus_validate_retire_deleted_tombstones_a_removed_case(tmp_path: Path) -> None:
    """`--register --retire-deleted` списывает id ушедшего кейса надгробием.

    Регистрация обязана читать кейсы **без** обратной проверки реестра (живой
    id без кейса): она и есть то, что списание чинит, — иначе списание
    недостижимо (отказ приходит до `append_registry`).
    """
    corpus = _corpus(tmp_path, 155, 157)
    (corpus / "andrei-shtanakov.steward-157.yaml").unlink()

    refused = runner.invoke(cli.app, ["corpus", "validate", "--corpus", str(corpus)])
    assert refused.exit_code == 2
    assert "зарегистрирован, но кейса с ним нет" in refused.output

    result = runner.invoke(
        cli.app,
        ["corpus", "validate", "--corpus", str(corpus), "--register", "--retire-deleted"],
    )

    assert result.exit_code == 0, result.output
    registry = (corpus / "_ids.txt").read_text(encoding="utf-8")
    assert "D-andrei-shtanakov.steward-157-1 deleted" in registry
    assert load_corpus(corpus)[0].case_id == "andrei-shtanakov.steward-155"
    assert "списаны надгробием" in result.output
    assert "D-andrei-shtanakov.steward-157-1" in result.output


def test_corpus_validate_register_without_retire_deleted_names_candidates_not_additions(
    tmp_path: Path,
) -> None:
    """Без `--retire-deleted` пропавший id — кандидат, а не «дописанная» строка.

    `append_registry` возвращает id без кейса независимо от флага; без него
    реестр не меняется вовсе — печатать это как `реестр: + <id>` читалось бы
    добавлением, хотя единственный смысл, который несёт этот список без
    флага, — «спишите --retire-deleted» (docstring `append_registry`).
    """
    corpus = _corpus(tmp_path, 155, 157)
    (corpus / "andrei-shtanakov.steward-157.yaml").unlink()

    result = runner.invoke(cli.app, ["corpus", "validate", "--corpus", str(corpus), "--register"])

    # Без --retire-deleted живой id без кейса всё равно отказывает при
    # последующей загрузке корпуса (check_registry) — тот же контракт, что
    # у обычного `corpus validate` без --register. Здесь важно только, что
    # напечатанное перед этим отказом сообщение не лжёт про запись в реестр.
    assert result.exit_code == 2, result.output
    registry = (corpus / "_ids.txt").read_text(encoding="utf-8")
    assert "deleted" not in registry
    assert "кандидаты на списание" in result.output
    assert "--retire-deleted" in result.output
    assert "D-andrei-shtanakov.steward-157-1" in result.output
    assert "реестр: +" not in result.output


def test_corpus_validate_register_leaves_the_registry_untouched_on_a_corpus_error(
    tmp_path: Path,
) -> None:
    """Межфайловая ошибка (дубликат case_id) при `--register --retire-deleted` —
    код 2 и реестр **байт в байт** прежний: надгробие терминально, и запись
    его до полной валидации превращала неудачную команду в порчу реестра.
    """
    corpus = _corpus(tmp_path, 155, 157, 159)
    (corpus / "andrei-shtanakov.steward-159.yaml").unlink()  # кандидат на списание
    duplicate = yaml.safe_dump(_case_payload(155), allow_unicode=True, sort_keys=False)
    (corpus / "andrei-shtanakov.steward-157.yaml").write_text(duplicate, "utf-8")
    before = (corpus / "_ids.txt").read_bytes()

    result = runner.invoke(
        cli.app,
        ["corpus", "validate", "--corpus", str(corpus), "--register", "--retire-deleted"],
    )

    assert result.exit_code == 2, result.output
    assert "duplicate case_id" in result.output
    assert (corpus / "_ids.txt").read_bytes() == before


def test_corpus_validate_reidentify_accepts_a_comma_list(tmp_path: Path) -> None:
    """`--reidentify a,b` подтверждает смену ядра идентичности у перечисленных id."""
    corpus = _corpus(tmp_path, 155)
    payload = _case_payload(155)
    payload["defects"][0]["scenario"] = "другой дефект под тем же id по решению разметчика"
    (corpus / "andrei-shtanakov.steward-155.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8"
    )

    refused = runner.invoke(cli.app, ["corpus", "validate", "--corpus", str(corpus), "--register"])
    assert refused.exit_code == 2
    assert "--reidentify" in refused.output

    result = runner.invoke(
        cli.app,
        [
            "corpus",
            "validate",
            "--corpus",
            str(corpus),
            "--register",
            "--reidentify",
            "D-andrei-shtanakov.steward-155-1,NF-andrei-shtanakov.steward-155-9",
        ],
    )

    assert result.exit_code == 0, result.output
    line = [
        row
        for row in (corpus / "_ids.txt").read_text(encoding="utf-8").splitlines()
        if row.startswith("D-andrei-shtanakov.steward-155-1 ")
    ][-1]
    assert line.split()[-1] == "reidentified"


def test_corpus_validate_register_on_an_empty_corpus_points_at_tombstones(
    tmp_path: Path,
) -> None:
    """Пустой корпус с живыми id: отказ советует надгробия, а не удаление реестра."""
    corpus = _corpus(tmp_path, 155)
    (corpus / "andrei-shtanakov.steward-155.yaml").unlink()

    result = runner.invoke(
        cli.app,
        ["corpus", "validate", "--corpus", str(corpus), "--register", "--retire-deleted"],
    )

    assert result.exit_code == 2
    # Сообщение ведёт к надгробиям, дописанным руками под ревью PR, и прямо
    # запрещает удалять файл: реестр append-only, его нельзя «почистить».
    assert "вручную" in result.output
    assert "deleted" in result.output
    assert "файл не удалять" in result.output


def test_corpus_validate_says_when_there_is_no_gold(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, 155, status="draft")
    result = runner.invoke(cli.app, ["corpus", "validate", "--corpus", str(corpus)])
    assert result.exit_code == 0, result.output
    assert "gold-кейсов нет" in result.output


# ---------------------------------------------------------------------------
# corpus materialize
# ---------------------------------------------------------------------------


def test_corpus_materialize_prefers_a_local_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = _corpus(tmp_path, 155)
    workspace = tmp_path / "workspace"
    (workspace / "steward" / ".git").mkdir(parents=True)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cli, "materialize", lambda *args, **kwargs: calls.append({"args": args, **kwargs})
    )
    result = runner.invoke(
        cli.app,
        [
            "corpus",
            "materialize",
            "--corpus",
            str(corpus),
            "--cache",
            str(tmp_path / "cache"),
            "--workspace-root",
            str(workspace),
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls[0]["local_checkout"] == workspace / "steward"
    assert calls[0]["remote_url"] == "https://github.com/andrei-shtanakov/steward.git"
    assert sorted(calls[0]["args"][2]) == [BASE, HEAD]


def test_corpus_materialize_scrubs_an_inherited_git_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`GIT_DIR` унаследованный процессом не должен уйти в `clone`/`fetch`.

    Без вычищенного окружения git-вызовы `materialize` работали бы с чужим
    репозиторием, на который указывает `GIT_DIR`, вместо заявленного bare-кэша
    — кэш выглядел бы готовым, хотя объекты легли не туда.
    """
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "чужой" / ".git"))
    corpus = _corpus(tmp_path, 155)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cli, "materialize", lambda *args, **kwargs: calls.append({"args": args, **kwargs})
    )
    result = runner.invoke(
        cli.app,
        ["corpus", "materialize", "--corpus", str(corpus), "--workspace-root", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "GIT_DIR" not in calls[0]["env"]


def test_corpus_materialize_without_a_local_checkout_goes_to_github(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = _corpus(tmp_path, 155)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "materialize", lambda *args, **kwargs: calls.append(kwargs))
    result = runner.invoke(
        cli.app,
        ["corpus", "materialize", "--corpus", str(corpus), "--workspace-root", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert calls[0]["local_checkout"] is None


def test_corpus_materialize_exits_2_when_an_object_is_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = _corpus(tmp_path, 155)

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise cli.CacheError("cannot materialize andrei-shtanakov/steward@2222")

    monkeypatch.setattr(cli, "materialize", boom)
    result = runner.invoke(cli.app, ["corpus", "materialize", "--corpus", str(corpus)])
    assert result.exit_code == 2
    assert "cannot materialize" in result.output


def test_corpus_materialize_exits_2_on_a_missing_git_binary(tmp_path: Path) -> None:
    """Неверный `--git` — ошибка конфигурации (код 2), а не traceback.

    `OSError` от `subprocess` («нет такого файла») означает ровно то же, что
    непройденная проверка аргументов: инструмент не может работать с тем, что
    ему задали.
    """
    corpus = _corpus(tmp_path, 155)
    result = runner.invoke(
        cli.app,
        [
            "corpus",
            "materialize",
            "--corpus",
            str(corpus),
            "--cache",
            str(tmp_path / "cache"),
            "--git",
            "/nonexistent/git",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "/nonexistent/git" in result.output


def test_corpus_validate_exits_3_on_an_unexpected_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Непредусмотренное исключение — механический сбой (код 3) одной строкой."""
    corpus = _corpus(tmp_path, 155)

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise TypeError("нечто непредусмотренное")

    monkeypatch.setattr(cli, "load_corpus", boom)
    result = runner.invoke(cli.app, ["corpus", "validate", "--corpus", str(corpus)])

    assert result.exit_code == 3
    assert "нечто непредусмотренное" in result.output
    assert "Traceback" not in result.output


def test_repo_corpus_is_valid_and_registered() -> None:
    """Корпус самого репозитория загружается: шесть черновиков, id в реестре.

    Гейт ветки проверяется этим же вызовом, но руками; тест делает его
    постоянным: правка черновика или реестра, ломающая загрузку, падает здесь,
    а не в чужом прогоне.
    """
    corpus = Path(__file__).resolve().parents[2] / "eval" / "corpus"

    cases = load_corpus(corpus)

    assert [case.case_id for case in cases] == [
        "andrei-shtanakov.steward-152",
        "andrei-shtanakov.steward-155",
        "andrei-shtanakov.steward-156",
        "andrei-shtanakov.steward-157",
        "andrei-shtanakov.steward-159",
        "andrei-shtanakov.steward-161",
    ]
    # Все шесть — прокси из истории ревью: в метрики они не входят, пока
    # разметчик не перевёл их в `adjudicated` (D1).
    assert all(case.annotation.status == "draft" for case in cases)
    assert sum(len(case.defects) for case in cases) == 4


# ---------------------------------------------------------------------------
# corpus candidates
# ---------------------------------------------------------------------------


def _patch_candidate_sources(monkeypatch: pytest.MonkeyPatch, resolved: dict[str, Any]) -> None:
    """Подменить сетевые источники `corpus candidates` одним валидным ревью."""
    body = (
        f"{KIT_HEADER}\n\n"
        "### [major] PATH расширяется — `scripts/review/local.sh:644`\n"
        "- Сценарий: s\n- Наблюдаемое: o\n- Ожидаемое: e\n"
        "- Evidence: `scripts/review/local.sh:644` — r\n"
        "- confidence: high → БЛОКИРУЕТ\n"
        f"\n<!-- codex-terminal-review head={HEAD} -->\n"
    )

    def fake_resolve(repo: str, pr_meta: Any, head_sha: str, *_a: Any, **_k: Any) -> str:
        resolved.update(repo=repo, head_sha=head_sha, pr_meta=pr_meta)
        return MERGE_BASE

    monkeypatch.setattr(cli, "resolve_review_base", fake_resolve)
    monkeypatch.setattr(
        cli, "fetch_pr", lambda *_a, **_k: {"base": {"sha": BASE}, "head": {"sha": HEAD}}
    )
    monkeypatch.setattr(
        cli,
        "fetch_reviews",
        lambda *_a, **_k: [
            {
                "id": 1,
                "user": {"login": "ai-prosto"},
                "submitted_at": "2026-09-14T08:00:00Z",
                "body": body,
            }
        ],
    )
    monkeypatch.setattr(cli, "fetch_commits", lambda *_a, **_k: [])


def test_corpus_candidates_refuses_a_symlinked_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Симлинк на месте `eval/corpus/<case_id>.yaml` — отказ (код 2), внешняя
    цель не тронута: `write_text` по ссылке писал бы черновик в чужой файл.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("important", encoding="utf-8")
    (corpus / "andrei-shtanakov.steward-155.yaml").symlink_to(victim)
    _patch_candidate_sources(monkeypatch, {})

    result = runner.invoke(
        cli.app,
        [
            "corpus",
            "candidates",
            "--repo",
            "andrei-shtanakov/steward",
            "--pr",
            "155",
            "--corpus",
            str(corpus),
        ],
    )

    assert result.exit_code == 2, result.output
    assert "символическая ссылка" in result.output
    assert victim.read_text(encoding="utf-8") == "important"


def test_corpus_candidates_refuses_to_overwrite_an_existing_case_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Путь назначения по умолчанию детерминирован (repo+PR) — повторный запуск
    без `--force` не должен молча стереть уже размеченный кейс.

    Раньше `write_inside` заменял существующий файл безоговорочно
    (`os.replace`): adjudicated-разметка (найденные дефекты, снятые ложные
    находки, `blocking_complete`) терялась бы под свежим `draft`-черновиком.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    existing = corpus / "andrei-shtanakov.steward-155.yaml"
    existing.write_text("adjudicated: не трогать\n", encoding="utf-8")
    _patch_candidate_sources(monkeypatch, {})

    result = runner.invoke(
        cli.app,
        [
            "corpus",
            "candidates",
            "--repo",
            "andrei-shtanakov/steward",
            "--pr",
            "155",
            "--corpus",
            str(corpus),
        ],
    )

    assert result.exit_code == 2, result.output
    assert "уже существует" in result.output
    assert existing.read_text(encoding="utf-8") == "adjudicated: не трогать\n"


def test_corpus_candidates_force_overwrites_an_existing_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--force` — явный, осознанный оверрайд той же защиты."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    existing = corpus / "andrei-shtanakov.steward-155.yaml"
    existing.write_text("adjudicated: не трогать\n", encoding="utf-8")
    _patch_candidate_sources(monkeypatch, {})

    result = runner.invoke(
        cli.app,
        [
            "corpus",
            "candidates",
            "--repo",
            "andrei-shtanakov/steward",
            "--pr",
            "155",
            "--corpus",
            str(corpus),
            "--force",
        ],
    )

    assert result.exit_code == 0, result.output
    draft = load_case(existing)
    assert draft.annotation.status == "draft"


def test_corpus_candidates_writes_a_draft_into_the_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "corpus"
    body = (
        f"{KIT_HEADER}\n\n"
        "### [major] PATH расширяется — `scripts/review/local.sh:644`\n"
        "- Сценарий: s\n- Наблюдаемое: o\n- Ожидаемое: e\n"
        "- Evidence: `scripts/review/local.sh:644` — r\n"
        "- confidence: high → БЛОКИРУЕТ\n"
        f"\n<!-- codex-terminal-review head={HEAD} -->\n"
    )
    resolved: dict[str, Any] = {}

    def fake_resolve(repo: str, pr_meta: Any, head_sha: str, *_a: Any, **_k: Any) -> str:
        resolved.update(repo=repo, head_sha=head_sha, pr_meta=pr_meta)
        return MERGE_BASE

    monkeypatch.setattr(cli, "resolve_review_base", fake_resolve)
    monkeypatch.setattr(
        cli, "fetch_pr", lambda *_a, **_k: {"base": {"sha": BASE}, "head": {"sha": HEAD}}
    )
    monkeypatch.setattr(
        cli,
        "fetch_reviews",
        lambda *_a, **_k: [
            {
                "id": 1,
                "user": {"login": "ai-prosto"},
                "submitted_at": "2026-09-14T08:00:00Z",
                "body": body,
            }
        ],
    )
    monkeypatch.setattr(cli, "fetch_commits", lambda *_a, **_k: [])
    result = runner.invoke(
        cli.app,
        [
            "corpus",
            "candidates",
            "--repo",
            "andrei-shtanakov/steward",
            "--pr",
            "155",
            "--corpus",
            str(corpus),
        ],
    )
    assert result.exit_code == 0, result.output
    draft = load_case(corpus / "andrei-shtanakov.steward-155.yaml")
    assert draft.annotation.status == "draft"
    assert draft.annotation.source == "history-proxy"
    assert draft.cls == "defective"
    # База кейса — merge-base диапазона ревью, а не `base.sha` PR: после мержа
    # голова базы содержит сам PR, и диапазон вышел бы пустым (раунд 21).
    assert draft.base_sha == MERGE_BASE
    # Голову для резолва берём ту же, что возьмёт `draft_case`: из маркера тела.
    assert resolved == {
        "repo": "andrei-shtanakov/steward",
        "head_sha": HEAD,
        "pr_meta": {"base": {"sha": BASE}, "head": {"sha": HEAD}},
    }


def test_corpus_candidates_picks_the_head_of_the_last_review_by_time_not_by_list_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Голова резолвится по последнему ревью **по времени публикации**, а не
    по последнему элементу списка, который вернул `gh api` — тот порядок не
    гарантирован. `_review_head_sha` и `draft_case` обязаны сойтись на одном и
    том же ревью: разные головы дали бы кейсу базу от одного ревью и
    `head_sha` от другого.
    """
    old_head = "6" * 40
    new_head = "7" * 40

    def body_of(head: str) -> str:
        return f"{KIT_HEADER}\n\nНаходок нет.\n\n<!-- codex-terminal-review head={head} -->\n"

    corpus = tmp_path / "corpus"
    resolved: dict[str, Any] = {}

    def fake_resolve(repo: str, pr_meta: Any, head_sha: str, *_a: Any, **_k: Any) -> str:
        resolved.update(head_sha=head_sha)
        return MERGE_BASE

    monkeypatch.setattr(cli, "resolve_review_base", fake_resolve)
    monkeypatch.setattr(
        cli, "fetch_pr", lambda *_a, **_k: {"base": {"sha": BASE}, "head": {"sha": HEAD}}
    )
    monkeypatch.setattr(
        cli,
        "fetch_reviews",
        # Порядок списка — обратный порядку публикации: новое (по времени)
        # ревью стоит ПЕРВЫМ, старое — ПОСЛЕДНИМ. Раздельная сортировка в
        # `_review_head_sha`/`draft_case` не заметила бы расхождения.
        lambda *_a, **_k: [
            {
                "id": 2,
                "user": {"login": "ai-prosto"},
                "submitted_at": "2026-09-14T09:00:00Z",
                "body": body_of(new_head),
            },
            {
                "id": 1,
                "user": {"login": "ai-prosto"},
                "submitted_at": "2026-09-14T08:00:00Z",
                "body": body_of(old_head),
            },
        ],
    )
    monkeypatch.setattr(cli, "fetch_commits", lambda *_a, **_k: [])
    result = runner.invoke(
        cli.app,
        [
            "corpus",
            "candidates",
            "--repo",
            "andrei-shtanakov/steward",
            "--pr",
            "155",
            "--corpus",
            str(corpus),
        ],
    )
    assert result.exit_code == 0, result.output
    draft = load_case(corpus / "andrei-shtanakov.steward-155.yaml")
    assert resolved["head_sha"] == new_head
    assert draft.head_sha == new_head


def test_corpus_candidates_resolves_the_base_from_the_pr_head_without_a_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Маркера в теле нет — голова берётся из PR, и база резолвится от неё."""
    body = f"{KIT_HEADER}\n\nНаходок нет.\n"
    seen: dict[str, Any] = {}

    def fake_resolve(repo: str, pr_meta: Any, head_sha: str, *_a: Any, **_k: Any) -> str:
        seen["head_sha"] = head_sha
        return MERGE_BASE

    monkeypatch.setattr(cli, "resolve_review_base", fake_resolve)
    monkeypatch.setattr(
        cli, "fetch_pr", lambda *_a, **_k: {"base": {"sha": BASE}, "head": {"sha": HEAD}}
    )
    monkeypatch.setattr(
        cli,
        "fetch_reviews",
        lambda *_a, **_k: [
            {"id": 1, "user": {"login": "ai-prosto"}, "submitted_at": "z", "body": body}
        ],
    )
    monkeypatch.setattr(cli, "fetch_commits", lambda *_a, **_k: [])

    result = runner.invoke(
        cli.app,
        ["corpus", "candidates", "--repo", "org/repo", "--pr", "7", "--corpus", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert seen["head_sha"] == HEAD
    assert load_case(tmp_path / "org.repo-7.yaml").base_sha == MERGE_BASE


def test_corpus_candidates_exits_2_on_an_unrecognised_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тело без признаков формата кита — отказ кода 2 с сообщением кандидата."""
    monkeypatch.setattr(cli, "resolve_review_base", lambda *_a, **_k: MERGE_BASE)
    monkeypatch.setattr(
        cli, "fetch_pr", lambda *_a, **_k: {"base": {"sha": BASE}, "head": {"sha": HEAD}}
    )
    monkeypatch.setattr(
        cli,
        "fetch_reviews",
        lambda *_a, **_k: [
            {"id": 1, "user": {"login": "ai-prosto"}, "submitted_at": "z", "body": "проза"}
        ],
    )
    monkeypatch.setattr(cli, "fetch_commits", lambda *_a, **_k: [])

    result = runner.invoke(
        cli.app,
        ["corpus", "candidates", "--repo", "org/repo", "--pr", "7", "--corpus", str(tmp_path)],
    )

    assert result.exit_code == 2
    assert "тело ревью не распознано" in result.output


def test_corpus_candidates_exits_2_without_an_ai_prosto_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli, "fetch_pr", lambda *_a, **_k: {"base": {"sha": BASE}, "head": {"sha": HEAD}}
    )
    monkeypatch.setattr(cli, "fetch_reviews", lambda *_a, **_k: [])
    monkeypatch.setattr(cli, "fetch_commits", lambda *_a, **_k: [])
    result = runner.invoke(
        cli.app,
        [
            "corpus",
            "candidates",
            "--repo",
            "andrei-shtanakov/steward",
            "--pr",
            "155",
            "--corpus",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2
    assert "нет ревью" in result.output


def test_last_ai_prosto_submitted_at_ignores_a_later_empty_review() -> None:
    """Точка отсчёта `commits_after` — то же ревью, что выбрал бы `draft_case`.

    Пустое (например approve без находок) ревью ai-prosto, опубликованное
    позже содержательного, не в счёт: `draft_case`/`ai_prosto_reviews`
    отбрасывают пустое тело и строят кейс по содержательному ревью, и точка
    отсчёта коммитов обязана совпасть — иначе коммит между ними выпал бы из
    `commits_after`, а notes называли бы одно ревью последним, использовав
    время другого.
    """
    reviews = [
        {
            "id": 1,
            "user": {"login": "ai-prosto"},
            "submitted_at": "2026-09-14T08:00:00Z",
            "body": f"{KIT_HEADER}\n\nНаходок нет.\n",
        },
        {
            "id": 2,
            "user": {"login": "ai-prosto"},
            "submitted_at": "2026-09-14T09:00:00Z",
            "body": "",
        },
    ]

    assert cli._last_ai_prosto_submitted_at(reviews) == "2026-09-14T08:00:00Z"


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def test_run_writes_three_artifacts_and_exits_0(
    tmp_path: Path, stub_kit: KitUnderTest, captured_run: dict[str, Any]
) -> None:
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    result = runner.invoke(
        cli.app,
        ["run", "--corpus", str(corpus), "--variant", VARIANT, "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert payload["variants"][VARIANT]["status"] == "ok"
    assert payload["comparisons"] is None
    assert (out / "report.md").is_file()
    assert (out / "adjudication-queue.md").is_file()
    assert "run_id: 20260915T000000Z-deadbeef" in result.output


def test_run_passes_the_whole_corpus_digest_with_a_case_subset(
    tmp_path: Path, stub_kit: KitUnderTest, captured_run: dict[str, Any]
) -> None:
    corpus = _corpus(tmp_path, 155, 157)
    whole = corpus_digest(load_corpus(corpus))
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--corpus",
            str(corpus),
            "--variant",
            VARIANT,
            "--out",
            str(tmp_path / "run"),
            "--cases",
            "andrei-shtanakov.steward-157",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured_run["cases"] == ["andrei-shtanakov.steward-157"]
    assert captured_run["corpus_digest_override"] == whole
    assert whole != corpus_digest([load_case(corpus / "andrei-shtanakov.steward-157.yaml")])


def test_run_exits_2_on_an_unknown_case_id(
    tmp_path: Path, stub_kit: KitUnderTest, captured_run: dict[str, Any]
) -> None:
    corpus = _corpus(tmp_path, 155)
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--corpus",
            str(corpus),
            "--variant",
            VARIANT,
            "--out",
            str(tmp_path / "run"),
            "--cases",
            "steward-999",
        ],
    )
    assert result.exit_code == 2
    assert "нет таких кейсов" in result.output


def test_run_exits_2_on_a_variant_label_with_a_slash(
    tmp_path: Path, stub_kit: KitUnderTest, captured_run: dict[str, Any]
) -> None:
    corpus = _corpus(tmp_path, 155)
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--corpus",
            str(corpus),
            "--variant",
            "claude:anthropic/claude-opus-5",
            "--out",
            str(tmp_path / "run"),
        ],
    )
    assert result.exit_code == 2
    assert "model must be one word" in result.output
    assert "cases" not in captured_run


def test_run_exits_2_on_an_unsupported_harness_and_on_no_variant(
    tmp_path: Path, stub_kit: KitUnderTest, captured_run: dict[str, Any]
) -> None:
    corpus = _corpus(tmp_path, 155)
    bad = runner.invoke(
        cli.app,
        ["run", "--corpus", str(corpus), "--variant", "gemini:x", "--out", str(tmp_path / "r1")],
    )
    assert bad.exit_code == 2
    assert "unsupported harness" in bad.output

    none = runner.invoke(cli.app, ["run", "--corpus", str(corpus), "--out", str(tmp_path / "r2")])
    assert none.exit_code == 2
    assert "хотя бы один --variant" in none.output


def test_run_exits_2_on_a_non_positive_repetitions_or_jobs(tmp_path: Path) -> None:
    """`--repetitions 0`/`--jobs 0` — конфигурация (код 2), не пустой прогон.

    `run_all` уже отвергает и то, и другое (`repetitions must be >= 1`,
    `jobs must be >= 1`) до любой работы с worktree — но до этого теста ни
    один тест CLI или раннера не проверял это сквозным вызовом; ревью-заход
    части 3 заподозрил здесь молчаливый пустой прогон кодом 0, оказавшийся
    ложным (guard уже стоит), и тест фиксирует фактическое, уже верное
    поведение, чтобы предположение не повторялось.
    """
    corpus = _corpus(tmp_path, 155)

    zero_repetitions = runner.invoke(
        cli.app,
        [
            "run",
            "--corpus",
            str(corpus),
            "--variant",
            VARIANT,
            "--out",
            str(tmp_path / "r1"),
            "--repetitions",
            "0",
        ],
    )
    assert zero_repetitions.exit_code == 2, zero_repetitions.output
    assert "repetitions must be >= 1" in zero_repetitions.output

    zero_jobs = runner.invoke(
        cli.app,
        [
            "run",
            "--corpus",
            str(corpus),
            "--variant",
            VARIANT,
            "--out",
            str(tmp_path / "r2"),
            "--jobs",
            "0",
        ],
    )
    assert zero_jobs.exit_code == 2, zero_jobs.output
    assert "jobs must be >= 1" in zero_jobs.output


def test_run_exits_2_when_the_object_is_not_in_the_cache(
    tmp_path: Path, stub_kit: KitUnderTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = _corpus(tmp_path, 155)

    def boom(*_args: Any, **_kwargs: Any) -> RunManifest:
        raise cli.RunnerError("objects not in the cache at eval/cache")

    monkeypatch.setattr(cli, "run_all", boom)
    result = runner.invoke(
        cli.app,
        ["run", "--corpus", str(corpus), "--variant", VARIANT, "--out", str(tmp_path / "run")],
    )
    assert result.exit_code == 2
    assert "not in the cache" in result.output


def test_run_exits_2_on_a_missing_git_binary(tmp_path: Path) -> None:
    """`run --git /nonexistent/git` — код 2: кит не опознать, мерить нечего.

    Кит здесь настоящий (фикстура `stub_kit` не подставлена): проверяется
    именно путь `kit_under_test` → `git rev-parse`, который без бинаря git
    бросал `FileNotFoundError` наружу.
    """
    corpus = _corpus(tmp_path, 155)
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--corpus",
            str(corpus),
            "--variant",
            VARIANT,
            "--out",
            str(tmp_path / "run"),
            "--git",
            "/nonexistent/git",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "/nonexistent/git" in result.output


def test_run_exits_3_on_a_mechanical_runner_failure(
    tmp_path: Path, stub_kit: KitUnderTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = _corpus(tmp_path, 155)

    def boom(*_args: Any, **_kwargs: Any) -> RunManifest:
        raise RuntimeError("worktree exploded")

    monkeypatch.setattr(cli, "run_all", boom)
    result = runner.invoke(
        cli.app,
        ["run", "--corpus", str(corpus), "--variant", VARIANT, "--out", str(tmp_path / "run")],
    )
    assert result.exit_code == 3
    assert "runner failed" in result.output


def test_run_exits_1_on_an_unexpected_outcome(
    tmp_path: Path, stub_kit: KitUnderTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"

    def fake_run_all(cases: Any, variants: Any, **kwargs: Any) -> RunManifest:
        return _write_run(
            kwargs["out_dir"], ["andrei-shtanakov.steward-155"], outcome="config_failure"
        )

    monkeypatch.setattr(cli, "run_all", fake_run_all)
    result = runner.invoke(
        cli.app, ["run", "--corpus", str(corpus), "--variant", VARIANT, "--out", str(out)]
    )
    assert result.exit_code == 1
    assert "unexpected_outcome" in result.output


def test_run_exits_1_when_the_adjudication_queue_is_open(
    tmp_path: Path, stub_kit: KitUnderTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    # Находка в другом файле не сопоставляется ни с дефектом, ни с non_defect —
    # это неразмеченное предсказание, то есть открытая очередь (D9).
    unlabeled = _finding()
    unlabeled["file"] = "scripts/review/other.sh"
    unlabeled["evidence"] = [{"file": "scripts/review/other.sh", "line": 1, "reason": "r"}]

    def fake_run_all(cases: Any, variants: Any, **kwargs: Any) -> RunManifest:
        return _write_run(kwargs["out_dir"], ["andrei-shtanakov.steward-155"], findings=[unlabeled])

    monkeypatch.setattr(cli, "run_all", fake_run_all)
    result = runner.invoke(
        cli.app, ["run", "--corpus", str(corpus), "--variant", VARIANT, "--out", str(out)]
    )
    assert result.exit_code == 1
    assert "очередь adjudication непуста" in result.output
    payload = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert payload["variants"][VARIANT]["status"] == "pending_adjudication"
    assert "precision" not in payload["variants"][VARIANT]["metrics"]
    assert "precision_lower_bound" in payload["variants"][VARIANT]["metrics"]


def test_run_says_when_the_corpus_has_no_gold(
    tmp_path: Path, stub_kit: KitUnderTest, captured_run: dict[str, Any]
) -> None:
    corpus = _corpus(tmp_path, 155, status="draft")
    result = runner.invoke(
        cli.app,
        ["run", "--corpus", str(corpus), "--variant", VARIANT, "--out", str(tmp_path / "run")],
    )
    assert result.exit_code == 0, result.output
    assert "gold-кейсов нет, метрики не публикуются" in result.output


def test_run_compares_the_first_two_variants(
    tmp_path: Path, stub_kit: KitUnderTest, captured_run: dict[str, Any]
) -> None:
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--corpus",
            str(corpus),
            "--variant",
            VARIANT,
            "--variant",
            "codex:gpt-5.4:high",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    comparisons = payload["comparisons"]
    assert list(comparisons) == [f"codex:gpt-5.4:high vs {VARIANT}"]
    assert comparisons[f"codex:gpt-5.4:high vs {VARIANT}"]["n_common_cases"] == 1
    assert set(payload["variants"]) == {VARIANT, "codex:gpt-5.4:high"}


def test_run_rejects_a_repeated_variant(
    tmp_path: Path, stub_kit: KitUnderTest, captured_run: dict[str, Any]
) -> None:
    corpus = _corpus(tmp_path, 155)
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--corpus",
            str(corpus),
            "--variant",
            VARIANT,
            "--variant",
            VARIANT,
            "--out",
            str(tmp_path / "run"),
        ],
    )
    assert result.exit_code == 2
    assert "варианты повторяются" in result.output


def test_run_forwards_the_flags_to_the_runner(
    tmp_path: Path, stub_kit: KitUnderTest, captured_run: dict[str, Any]
) -> None:
    corpus = _corpus(tmp_path, 155)
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--corpus",
            str(corpus),
            "--variant",
            VARIANT,
            "--out",
            str(tmp_path / "run"),
            "--cache",
            str(tmp_path / "cache"),
            "--repetitions",
            "2",
            "--jobs",
            "3",
            "--keep-worktrees",
            "--rerun",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured_run["repetitions"] == 2
    assert captured_run["jobs"] == 3
    assert captured_run["keep_worktrees"] is True
    assert captured_run["rerun"] is True
    assert captured_run["cache_root"] == tmp_path / "cache"


# ---------------------------------------------------------------------------
# run — сквозной прогон с настоящим раннером
# ---------------------------------------------------------------------------

#: Подставной кит сквозного теста: пишет окружение рядом с sidecar-ами (в
#: каталоге повторения — он переживает снятие worktree), кладёт вердикт с
#: находкой на gold-дефект и usage со стоимостью, выходит кодом 1 (находка
#: блокирует — так же, как настоящий `apply-threshold.sh`).
_E2E_LOCAL_SH = """#!/bin/sh
out_dir=$(dirname "$REVIEW_VERDICT_OUT")
{
  printf 'args:%s\\n' "$*"
  printf 'pwd:%s\\n' "$(pwd)"
  printf 'atxt:%s\\n' "$(cat a.txt 2>/dev/null)"
  env | sed -n -e 's/^\\(REVIEW_[A-Z_]*\\)=\\(.*\\)/env:\\1=\\2/p' | sort
} > "$out_dir/env.txt"
cat > "$REVIEW_VERDICT_OUT" <<'VERDICT'
{"note": "one blocking finding", "findings": [{"kind": "defect",
 "severity": "major", "confidence": "high", "title": "PATH расширяется",
 "file": "a.txt", "line": 2, "scenario": "Ревьюер с расширенным PATH",
 "observed_result": "подставной codex побеждает",
 "expected_result": "PATH не расширяется",
 "evidence": [{"file": "a.txt", "line": 2, "reason": "вот тут"}]}]}
VERDICT
printf '{"total_cost_usd": 0.125, "provider_duration_ms": 1500}' > "$REVIEW_USAGE_OUT"
echo "stub reviewer stdout"
exit 1
"""

#: Пустые файлы кита, которых `kit_under_test` требует для дайджестов: они не
#: исполняются, но их отсутствие — ошибка конфигурации (мерить нечего).
_E2E_KIT_PLACEHOLDERS = (
    "scripts/review/apply-threshold.sh",
    "scripts/review/collect-context.sh",
    "scripts/review/harness-claude",
    "scripts/review/build-prompt.sh",
)


def _e2e_kit(root: Path) -> None:
    """Каталог кита под измерением: настоящая раскладка, подставной `local.sh`."""
    (root / "scripts" / "review").mkdir(parents=True)
    (root / ".github" / "codex").mkdir(parents=True)
    local_sh = root / "scripts" / "review" / "local.sh"
    local_sh.write_text(_E2E_LOCAL_SH, encoding="utf-8")
    local_sh.chmod(0o755)
    (root / ".github" / "codex" / "review-prompt.md").write_text("prompt\n", encoding="utf-8")
    (root / ".github" / "codex" / "review-schema.json").write_text("{}\n", encoding="utf-8")
    for relative in _E2E_KIT_PLACEHOLDERS:
        (root / relative).write_text("", encoding="utf-8")


def _e2e_fixture_repo(root: Path) -> tuple[str, str]:
    """Репо кейса с двумя коммитами; `a.txt` на head — три строки."""
    import subprocess

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    root.mkdir(parents=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-qm", "first")
    base = git("rev-parse", "HEAD")
    (root / "a.txt").write_text("one\nPATH расширяется\nthree\n", encoding="utf-8")
    git("commit", "-qam", "second")
    return base, git("rev-parse", "HEAD")


def _e2e_corpus(directory: Path, base: str, head: str) -> None:
    """Корпус из одного gold-кейса, чей дефект матчится находкой кита."""
    directory.mkdir(parents=True)
    payload = _case_payload(155)
    payload["base_sha"] = base
    payload["head_sha"] = head
    payload["defects"][0]["file"] = "a.txt"
    payload["defects"][0]["line_hint"] = 2
    payload["defects"][0]["evidence"] = ["a.txt:2"]
    payload["defects"][0]["match"] = {
        "files": ["a.txt"],
        "line_window": 5,
        "keywords_any": ["path", "расширяется"],
    }
    (directory / "andrei-shtanakov.steward-155.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8"
    )
    append_registry([load_case(directory / "andrei-shtanakov.steward-155.yaml")], directory)


def test_run_end_to_end_with_the_real_runner(tmp_path: Path) -> None:
    """Сквозной прогон без подмен, кроме самого кита: код 0 и три артефакта.

    Проверяется то, что подменённый `run_all` проверить не может: раннер
    создаёт worktree на историческом `head_sha`, запускает кит **в нём**,
    вычищает унаследованный `REVIEW_*`, ставит переменные варианта и
    sidecar-ов, классифицирует исход по коду выхода и вердикту — а CLI затем
    считает метрики и пишет три файла. Находка кита матчится gold-дефекту,
    очередь пуста, исход ожидаемый, поэтому код 0.
    """
    steward_root = tmp_path / "kit-root"
    _e2e_kit(steward_root)
    base, head = _e2e_fixture_repo(tmp_path / "steward")
    corpus = tmp_path / "corpus"
    _e2e_corpus(corpus, base, head)
    cache = tmp_path / "cache"
    out = tmp_path / "run"

    materialized = runner.invoke(
        cli.app,
        [
            "corpus",
            "materialize",
            "--corpus",
            str(corpus),
            "--cache",
            str(cache),
            "--workspace-root",
            str(tmp_path),
        ],
    )
    assert materialized.exit_code == 0, materialized.output

    result = runner.invoke(
        cli.app,
        [
            "run",
            "--corpus",
            str(corpus),
            "--variant",
            "codex:gpt-5.4:high",
            "--out",
            str(out),
            "--cache",
            str(cache),
            "--steward-root",
            str(steward_root),
        ],
        # Унаследованное окружение с оверрайдом целиком и чужой моделью (§6.3):
        # раннер обязан вычистить `REVIEW_*`, а имя provider-переменной —
        # записать в манифест.
        env={
            "REVIEW_CMD": "echo not-the-measured-path",
            "REVIEW_MODEL": "stale-model",
            "ANTHROPIC_API_KEY": "секрет",
        },
    )

    assert result.exit_code == 0, result.output
    assert (out / "report.md").is_file()
    assert (out / "adjudication-queue.md").is_file()

    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    variant = metrics["variants"]["codex:gpt-5.4:high"]
    assert variant["status"] == "ok"
    assert variant["metrics"]["precision"]["value"] == 1.0
    assert variant["metrics"]["blocking_recall"]["value"] == 1.0
    assert variant["cost"]["cost_usd_total"] == 0.125
    assert variant["metrics"]["resolvable_evidence_rate"]["value"] == 1.0
    assert (out / "adjudication-queue.md").read_text(encoding="utf-8") == "Очередь пуста.\n"

    rep_dir = out / "cases" / "andrei-shtanakov.steward-155" / "codex:gpt-5.4:high" / "1"
    outcome = json.loads((rep_dir / "result.json").read_text(encoding="utf-8"))
    assert (outcome["outcome"], outcome["exit_code"], outcome["unexpected"]) == (
        "verdict",
        1,
        False,
    )
    assert outcome["cost_status"] == "available"

    recorded = (rep_dir / "env.txt").read_text(encoding="utf-8")
    assert "args:--base " + base in recorded
    assert "--head " + head in recorded
    # Кит читал дерево на **head** (на base у `a.txt` одна строка) и делал это
    # в worktree прогона, а не в чекауте фикстуры.
    assert "atxt:one\nPATH расширяется\nthree" in recorded
    assert f"pwd:{out / 'scratch'}" in recorded
    assert "env:REVIEW_HARNESS=codex" in recorded
    assert "env:REVIEW_MODEL=gpt-5.4" in recorded
    assert "env:REVIEW_EFFORT=high" in recorded
    assert f"env:REVIEW_KIT_DIR={steward_root / 'scripts' / 'review'}" in recorded
    # Унаследованный оверрайд вычищен, а не просто перекрыт.
    assert "REVIEW_CMD" not in recorded
    assert "stale-model" not in recorded

    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert manifest["variants"][0]["label"] == "codex:gpt-5.4:high"
    assert "ANTHROPIC_API_KEY" in manifest["provider_env_names"]
    assert "секрет" not in (out / "run.json").read_text(encoding="utf-8")
    # Рядом с каждым дайджестом — права на исполнение: `chmod -x` содержимого
    # не меняет, а прогон вариантом claude ломает, и провенанс это обязан видеть.
    digest_keys = {
        "prompt_sha256",
        "schema_sha256",
        "threshold_sha256",
        "local_sh_sha256",
        "collect_context_sha256",
        "harness_claude_sha256",
        "build_prompt_sha256",
    }
    assert set(manifest["kit"]) == {"commit"} | digest_keys | {
        key.removesuffix("_sha256") + "_executable" for key in digest_keys
    }
    # Чистый прогон не оставляет пустого дерева worktree.
    assert not (out / "scratch").exists()


def test_run_accepts_a_relative_out_and_a_git_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    captured_run: dict[str, Any],
    stub_kit: KitUnderTest,
) -> None:
    """`--out` может быть относительным, а `--git` — путём к бинарю.

    Относительный `--out` — обычный способ запуска из корня репо
    (`--out eval/runs/…`); путь в `--git` нужен, чтобы мерить конкретной
    версией, и раннер сам делает её видимой киту (`pin_git`).
    """
    corpus = _corpus(tmp_path, 155)
    monkeypatch.chdir(tmp_path)
    git_path = shutil.which("git")
    assert git_path is not None

    result = runner.invoke(
        cli.app,
        [
            "run",
            "--corpus",
            str(corpus),
            "--variant",
            VARIANT,
            "--out",
            "runs/one",
            "--cache",
            str(tmp_path / "cache"),
            "--git",
            git_path,
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured_run["out_dir"] == Path("runs/one")
    assert captured_run["git"] == git_path
    assert (tmp_path / "runs" / "one" / "metrics.json").is_file()


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def test_metrics_recomputes_from_artifacts_after_annotation(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    unlabeled = _finding()
    unlabeled["file"] = "scripts/review/other.sh"
    unlabeled["evidence"] = [{"file": "scripts/review/other.sh", "line": 1, "reason": "r"}]
    _write_run(out, ["andrei-shtanakov.steward-155"], findings=[unlabeled])

    open_queue = runner.invoke(cli.app, ["metrics", str(out), "--corpus", str(corpus)])
    assert open_queue.exit_code == 1
    assert "очередь adjudication непуста" in open_queue.output

    # Разметчик признал находку ложной: non_defect закрывает очередь.
    payload = _case_payload(155)
    payload["non_defects"] = [
        {
            "id": "NF-andrei-shtanakov.steward-155-1",
            "file": "scripts/review/other.sh",
            "line_hint": 644,
            "scenario": "историческая ложная находка",
            "match": {
                "files": ["scripts/review/other.sh"],
                "line_window": 40,
                "keywords_any": ["path", "расширяется"],
            },
        }
    ]
    (corpus / "andrei-shtanakov.steward-155.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8"
    )
    append_registry([load_case(corpus / "andrei-shtanakov.steward-155.yaml")], corpus)

    closed = runner.invoke(cli.app, ["metrics", str(out), "--corpus", str(corpus)])
    assert closed.exit_code == 0, closed.output
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["variants"][VARIANT]["status"] == "ok"
    assert "precision" in metrics["variants"][VARIANT]["metrics"]


def test_metrics_recomputes_across_a_class_change_from_clean_to_defective(
    tmp_path: Path,
) -> None:
    """Adjudication дописывает пропущенный дефект в `clean`-кейс — `class`
    становится `defective` — и пересчёт по-прежнему разрешён (§5): `class` не
    входит в неизменяемый материал, потому что для `clean`/`defective` он сам
    произведение от наличия дефектов, а не независимый факт о диапазоне.
    """
    corpus = _corpus(tmp_path, 155, defects=False)
    out = tmp_path / "run"
    # Модель дефект пропустила: verdict без находок — обычный false negative,
    # не «неразмеченная находка», поэтому очередь adjudication пуста с начала.
    _write_run(out, ["andrei-shtanakov.steward-155"], findings=[], case_material_defects=False)

    before = runner.invoke(cli.app, ["metrics", str(out), "--corpus", str(corpus)])
    assert before.exit_code == 0, before.output

    # Разметчик находит пропущенный major-дефект руками и переводит кейс из
    # `clean` в `defective` — ровно переход, который цикл §5 разрешает без
    # повторного прогона.
    payload = _case_payload(155)
    payload["class"] = "defective"
    payload["defects"] = [
        {
            "id": "D-andrei-shtanakov.steward-155-1",
            "severity": "major",
            "file": "scripts/review/local.sh",
            "line_hint": 644,
            "scenario": "PATH расширяется для умолчания",
            "evidence": ["scripts/review/local.sh:644"],
            "match": {
                "files": ["scripts/review/local.sh"],
                "line_window": 40,
                "keywords_any": ["path", "расширяется"],
            },
        }
    ]
    (corpus / "andrei-shtanakov.steward-155.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8"
    )
    append_registry([load_case(corpus / "andrei-shtanakov.steward-155.yaml")], corpus)

    after = runner.invoke(cli.app, ["metrics", str(out), "--corpus", str(corpus)])
    assert after.exit_code == 0, after.output
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    # Дефект остался необнаруженным (модель ничего не находила) — recall = 0,
    # а не отказ пересчёта: сам факт посчитанной метрики и есть проверка.
    assert metrics["variants"][VARIANT]["status"] == "ok"
    assert metrics["variants"][VARIANT]["metrics"]["blocking_recall"]["value"] == 0.0


def test_metrics_exits_2_on_a_missing_git_binary(tmp_path: Path) -> None:
    """Неверный `--git` у `metrics` — код 2, а не «посчитали» с нулём.

    Отказ git внутри `_file_lines_at` читался как «файла нет», и
    `resolvable_evidence_rate` выходил посчитанным по всем ссылкам как
    неразрешимым — метрика без данных выглядела метрикой с данными.
    """
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    _write_run(out, ["andrei-shtanakov.steward-155"], findings=[_finding()])

    result = runner.invoke(
        cli.app,
        ["metrics", str(out), "--corpus", str(corpus), "--git", "/nonexistent/git"],
    )

    assert result.exit_code == 2, result.output
    assert "--git" in result.output
    assert "/nonexistent/git" in result.output


def test_metrics_passes_git_through_to_load_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--git` пред-проверен `_require_git`, но раньше не доходил до
    `load_corpus`/`check_registry`: реестр `_ids.txt` сверялся на append-only
    непроверенным умолчанием `"git"` из PATH независимо от `--git` — на
    машине без `git` в PATH это тихо выключало проверку истории реестра.
    """
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    _write_run(out, ["andrei-shtanakov.steward-155"], findings=[_finding()])
    git_path = shutil.which("git")
    assert git_path is not None
    real_load_corpus = cli.load_corpus
    calls: list[dict[str, Any]] = []

    def spy(directory: Path, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return real_load_corpus(directory, **kwargs)

    monkeypatch.setattr(cli, "load_corpus", spy)

    result = runner.invoke(
        cli.app, ["metrics", str(out), "--corpus", str(corpus), "--git", git_path]
    )

    assert result.exit_code == 0, result.output
    assert calls and calls[0].get("git") == git_path


def test_compare_exits_2_on_a_missing_git_binary(tmp_path: Path) -> None:
    """Та же пред-проверка у `compare`: он читает кэш теми же средствами."""
    corpus = _corpus(tmp_path, 155)
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a, ["andrei-shtanakov.steward-155"], findings=[_finding()])
    _write_run(run_b, ["andrei-shtanakov.steward-155"], findings=[])

    result = runner.invoke(
        cli.app,
        [
            "compare",
            str(run_a),
            str(run_b),
            "--corpus",
            str(corpus),
            "--git",
            "/nonexistent/git",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "/nonexistent/git" in result.output


def test_metrics_without_a_cache_publishes_no_resolvable_rate(tmp_path: Path) -> None:
    """Пересчёт из копии прогона без кэша (док §7): код 0, но метрика — `None`.

    Кэш нужен только `resolvable_evidence_rate`: остальное считается по
    артефактам прогона. Поэтому команда работает, а метрика, для которой данных
    нет, публикуется с `value: null` и пометкой — не посчитанным нулём.
    """
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    _write_run(out, ["andrei-shtanakov.steward-155"], findings=[_finding()])

    result = runner.invoke(
        cli.app,
        ["metrics", str(out), "--corpus", str(corpus), "--cache", str(tmp_path / "no-cache")],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    entry = payload["variants"][VARIANT]["metrics"]["resolvable_evidence_rate"]
    assert entry["value"] is None
    # Пометка называет обе потери: и evidence, и сверку gold «файла нет» с
    # деревом head — без источника фактов о файлах не проверено ни то, ни другое.
    assert entry["note"] == (
        "кэш недоступен — evidence не проверялся; сверка gold 'файла нет' с деревом "
        "head_sha тоже пропущена (1 кейсов)"
    )
    assert (entry["numerator"], entry["denominator"]) == (0, 0)
    # Метрики, не зависящие от кэша, на месте.
    assert payload["variants"][VARIANT]["metrics"]["precision"]["value"] == 1.0
    assert payload["variants"][VARIANT]["status"] == "ok"
    assert "кэш недоступен" in (out / "report.md").read_text(encoding="utf-8")


FOREIGN_DIGEST = "sha256:" + "1" * 64


def test_metrics_exits_2_on_matcher_drift(tmp_path: Path) -> None:
    """Пересчёт другим матчером — отказ: иначе отчёт врал бы о провенансе.

    `metrics` берёт шапку из старого `run.json`, а сопоставляет **текущим**
    матчером. Без проверки в отчёте стояли бы версия и дайджест правил, которых
    эти числа не видели.
    """
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    _write_run(
        out,
        ["andrei-shtanakov.steward-155"],
        findings=[_finding()],
        matcher_rules_digest=FOREIGN_DIGEST,
    )

    result = runner.invoke(cli.app, ["metrics", str(out), "--corpus", str(corpus)])

    assert result.exit_code == 2, result.output
    assert "matcher_rules_digest" in result.output
    assert "--allow-matcher-drift" in result.output


def test_metrics_exits_2_on_matcher_version_drift(tmp_path: Path) -> None:
    """Версия матчера — такой же различитель, как дайджест правил."""
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    _write_run(
        out,
        ["andrei-shtanakov.steward-155"],
        findings=[_finding()],
        matcher_version=MATCHER_VERSION + 1,
    )

    result = runner.invoke(cli.app, ["metrics", str(out), "--corpus", str(corpus)])

    assert result.exit_code == 2, result.output
    assert "matcher_version" in result.output


def test_metrics_refuses_when_the_case_material_changed_since_the_run(tmp_path: Path) -> None:
    """`head_sha` кейса изменился после прогона — вердикт получен на другом дереве:
    пересчёт по новому материалу отвергается (код 2), разметку менять можно.
    """
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    _write_run(out, ["andrei-shtanakov.steward-155"], findings=[_finding()])
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert "andrei-shtanakov.steward-155" in manifest["case_digests"]

    payload = _case_payload(155)
    payload["head_sha"] = "f" * 40
    (corpus / "andrei-shtanakov.steward-155.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8"
    )

    result = runner.invoke(cli.app, ["metrics", str(out), "--corpus", str(corpus)])

    assert result.exit_code == 2, result.output
    assert "материал кейса" in result.output
    assert "andrei-shtanakov.steward-155" in result.output


def test_metrics_refuses_a_manifest_whose_case_digests_do_not_cover_declared_cases(
    tmp_path: Path,
) -> None:
    """`case_digests` без записи для объявленного кейса — отказ, не «совпало».

    Пустая или неполная карта раньше проходила молча: цикл сравнения идёт
    только по присутствующим ключам, и отсутствие дайджеста не отличалось бы
    от материала, который не менялся. Здесь `head_sha` кейса и правда
    изменился — но проверка обязана отказать по отсутствию покрытия, а не по
    случайно не сработавшему сравнению.
    """
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    _write_run(out, ["andrei-shtanakov.steward-155"], findings=[_finding()])
    manifest_path = out / "run.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["case_digests"] = {}
    manifest_path.write_text(json.dumps(manifest), "utf-8")

    payload = _case_payload(155)
    payload["head_sha"] = "f" * 40
    (corpus / "andrei-shtanakov.steward-155.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8"
    )

    result = runner.invoke(cli.app, ["metrics", str(out), "--corpus", str(corpus)])

    assert result.exit_code == 2, result.output
    assert "не покрывает" in result.output
    assert "andrei-shtanakov.steward-155" in result.output


def test_metrics_allow_matcher_drift_records_the_recompute(tmp_path: Path) -> None:
    """С флагом пересчёт разрешён, но **назван**: оба провенанса рядом.

    `recomputed_with` в `metrics.json` и вторая строка в шапке `report.md` —
    чтобы читатель видел, каким матчером получены числа и каким шёл прогон.
    """
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    _write_run(
        out,
        ["andrei-shtanakov.steward-155"],
        findings=[_finding()],
        matcher_rules_digest=FOREIGN_DIGEST,
    )

    result = runner.invoke(
        cli.app,
        ["metrics", str(out), "--corpus", str(corpus), "--allow-matcher-drift"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    recomputed = payload["recomputed_with"]
    assert recomputed["matcher_version"] == MATCHER_VERSION
    assert recomputed["matcher_rules_digest"] == rules_digest()
    assert recomputed["at"].endswith("Z")

    report = (out / "report.md").read_text(encoding="utf-8")
    assert f"- matcher: version={MATCHER_VERSION}, rules_digest=`sha256:111111111111`" in report
    assert "- пересчитано матчером: version=" in report
    assert rules_digest()[:19] in report


def test_compare_exits_2_on_matcher_drift(tmp_path: Path) -> None:
    """Сравнение — тоже пересчёт: дрейф на любой стороне блокирует его."""
    corpus = _corpus(tmp_path, 155)
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(
        run_a,
        ["andrei-shtanakov.steward-155"],
        findings=[_finding()],
        matcher_rules_digest=FOREIGN_DIGEST,
    )
    _write_run(run_b, ["andrei-shtanakov.steward-155"], findings=[])

    drifted = runner.invoke(cli.app, ["compare", str(run_a), str(run_b), "--corpus", str(corpus)])
    assert drifted.exit_code == 2, drifted.output
    assert "matcher_rules_digest" in drifted.output

    allowed = runner.invoke(
        cli.app,
        ["compare", str(run_a), str(run_b), "--corpus", str(corpus), "--allow-matcher-drift"],
    )
    assert allowed.exit_code == 0, allowed.output
    assert "blocking_recall" in allowed.output


def test_metrics_exits_2_without_a_run_manifest(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    out.mkdir()
    result = runner.invoke(cli.app, ["metrics", str(out), "--corpus", str(corpus)])
    assert result.exit_code == 2
    assert "не читается" in result.output


def test_metrics_exits_2_when_a_result_has_no_case_in_the_corpus(tmp_path: Path) -> None:
    """Кейс удалён из корпуса, а его результат в прогоне остался — код 2,
    не 3: артефакты прогона целы и сходятся сами с собой, разошёлся корпус,
    а не что-то, что поломало бы сам инструмент (ревью-находка части 3,
    minor). Раньше `_evaluate` поднимал `MetricsError`, и это читалось как
    механический сбой review-eval, хотя правка корпуса — обычная
    конфигурация.
    """
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    _write_run(out, ["steward-999"])
    result = runner.invoke(cli.app, ["metrics", str(out), "--corpus", str(corpus)])
    assert result.exit_code == 2, result.output
    assert "кейса в корпусе нет" in result.output


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def test_compare_exits_2_when_a_result_has_no_case_in_the_corpus(tmp_path: Path) -> None:
    """Тот же случай, что у `metrics`, но через `compare`: код 2, не 3."""
    corpus = _corpus(tmp_path, 155)
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a, ["steward-999"])
    _write_run(run_b, ["steward-999"])

    result = runner.invoke(cli.app, ["compare", str(run_a), str(run_b), "--corpus", str(corpus)])

    assert result.exit_code == 2, result.output
    assert "кейса в корпусе нет" in result.output


def test_compare_prints_a_table_per_common_variant(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, 155, 157)
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(
        run_a,
        ["andrei-shtanakov.steward-155", "andrei-shtanakov.steward-157"],
        findings=[_finding()],
    )
    _write_run(run_b, ["andrei-shtanakov.steward-155", "andrei-shtanakov.steward-157"], findings=[])
    result = runner.invoke(cli.app, ["compare", str(run_a), str(run_b), "--corpus", str(corpus)])
    assert result.exit_code == 0, result.output
    assert f"## {VARIANT}" in result.output
    assert "blocking_recall" in result.output


def test_compare_echoes_provenance_and_warns_on_a_kit_mismatch(tmp_path: Path) -> None:
    """`compare` печатает кит/`corpus_digest` A и B и предупреждает при расхождении.

    Ни `_require_same_case_material`, ни `_recompute_provenance` не сверяют
    `kit`/`corpus_digest` двух прогонов друг с другом — раньше разница
    чисел, целиком вызванная правкой кита между A и B, читалась бы как
    разница между вариантами, ничем в выводе не отличаясь от настоящего
    измерения (ревью-находка части 3, major/medium).
    """
    corpus = _corpus(tmp_path, 155)
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a, ["andrei-shtanakov.steward-155"], findings=[_finding()])
    _write_run(run_b, ["andrei-shtanakov.steward-155"], findings=[])
    manifest_b_path = run_b / "run.json"
    manifest_b = json.loads(manifest_b_path.read_text(encoding="utf-8"))
    manifest_b["kit"]["commit"] = "d" * 40
    manifest_b_path.write_text(json.dumps(manifest_b), "utf-8")

    result = runner.invoke(cli.app, ["compare", str(run_a), str(run_b), "--corpus", str(corpus)])

    assert result.exit_code == 0, result.output
    assert "c" * 40 in result.output
    assert "d" * 40 in result.output
    assert "внимание: A и B измерены разным китом" in result.output


def test_compare_names_variants_outside_the_comparison(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, 155)
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a, ["andrei-shtanakov.steward-155"], labels=[VARIANT, "codex:gpt-5.4"])
    _write_run(run_b, ["andrei-shtanakov.steward-155"], labels=[VARIANT])
    result = runner.invoke(cli.app, ["compare", str(run_a), str(run_b), "--corpus", str(corpus)])
    assert result.exit_code == 0, result.output
    assert "codex:gpt-5.4" in result.output
    assert "варианты вне сравнения" in result.output


def test_compare_exits_2_without_a_common_variant(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, 155)
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a, ["andrei-shtanakov.steward-155"], labels=[VARIANT])
    _write_run(run_b, ["andrei-shtanakov.steward-155"], labels=["codex:gpt-5.4"])
    result = runner.invoke(cli.app, ["compare", str(run_a), str(run_b), "--corpus", str(corpus)])
    assert result.exit_code == 2
    assert "общих вариантов" in result.output


def test_compare_exits_2_when_the_common_variant_has_no_overlapping_cases(
    tmp_path: Path,
) -> None:
    """Общая метка варианта не значит общие кейсы: пустая парная популяция —
    отказ (код 2), а не сравнение из нуля пар с кодом 0.
    """
    corpus = _corpus(tmp_path, 155, 157)
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a, ["andrei-shtanakov.steward-155"], labels=[VARIANT])
    _write_run(run_b, ["andrei-shtanakov.steward-157"], labels=[VARIANT])

    result = runner.invoke(cli.app, ["compare", str(run_a), str(run_b), "--corpus", str(corpus)])

    assert result.exit_code == 2, result.output
    assert "общих пар" in result.output
    assert VARIANT in result.output


def test_compare_exits_1_when_a_side_has_an_open_adjudication_queue(tmp_path: Path) -> None:
    """Одна из сторон не измерена (очередь открыта) — сравнение не зелёное.

    Печать таблицы с числами не заменяет проверку: `pending_adjudication`
    убирает `precision` из сводки, и без явного кода 1 автоматизация не
    отличила бы посчитанное сравнение от незавершённого измерения (§11, тот
    же контракт, что у `metrics`).
    """
    corpus = _corpus(tmp_path, 155)
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    unlabeled = _finding()
    unlabeled["file"] = "scripts/review/other.sh"
    unlabeled["evidence"] = [{"file": "scripts/review/other.sh", "line": 1, "reason": "r"}]
    _write_run(run_a, ["andrei-shtanakov.steward-155"], findings=[unlabeled])
    _write_run(run_b, ["andrei-shtanakov.steward-155"], findings=[])

    result = runner.invoke(cli.app, ["compare", str(run_a), str(run_b), "--corpus", str(corpus)])

    assert result.exit_code == 1, result.output
    assert f"{VARIANT} (A)" in result.output
    assert "не публикуется" in result.output


def test_compare_exits_1_on_an_unexpected_outcome(tmp_path: Path) -> None:
    """Один из прогонов получил не тот исход, что объявлен в кейсе — код 1."""
    corpus = _corpus(tmp_path, 155)
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a, ["andrei-shtanakov.steward-155"], outcome="config_failure")
    _write_run(run_b, ["andrei-shtanakov.steward-155"], findings=[_finding()])

    result = runner.invoke(cli.app, ["compare", str(run_a), str(run_b), "--corpus", str(corpus)])

    assert result.exit_code == 1, result.output
    assert "unexpected_outcome" in result.output
    assert "A/andrei-shtanakov.steward-155" in result.output


# ---------------------------------------------------------------------------
# внутренние помощники CLI
# ---------------------------------------------------------------------------


def test_steward_root_points_at_the_checkout_with_the_kit() -> None:
    root = cli._steward_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "scripts" / "review" / "local.sh").is_file()


def test_metrics_exits_2_on_an_unfinished_run(tmp_path: Path) -> None:
    """Прогон не закрыт (`finished: null`) — читать нечего: код 2 с сообщением.

    Это отказ **артефактов прогона**, а не дефект инструмента: раннер
    оборвался между последней тройкой и финальной записью манифеста. Код 3
    объявил бы сбоем сам `review-eval`.
    """
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    _write_run(out, ["andrei-shtanakov.steward-155"], findings=[_finding()])
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    manifest["finished"] = None
    (out / "run.json").write_text(json.dumps(manifest), "utf-8")

    result = runner.invoke(cli.app, ["metrics", str(out), "--corpus", str(corpus)])

    assert result.exit_code == 2, result.output
    assert "прогон не завершён" in result.output


def test_metrics_exits_2_on_an_incomplete_run(tmp_path: Path) -> None:
    """Манифест объявляет тройку, которой нет — прогон неполон, код 2."""
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    _write_run(out, ["andrei-shtanakov.steward-155"], findings=[_finding()], repetitions=2)
    import shutil

    shutil.rmtree(out / "cases" / "andrei-shtanakov.steward-155" / VARIANT / "2")

    result = runner.invoke(cli.app, ["metrics", str(out), "--corpus", str(corpus)])

    assert result.exit_code == 2, result.output
    assert "прогон неполон" in result.output


def test_metrics_exits_2_without_a_manifest(tmp_path: Path) -> None:
    """Результаты без `run.json` — провенанс неизвестен, код 2."""
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    _write_run(out, ["andrei-shtanakov.steward-155"], findings=[_finding()])
    (out / "run.json").unlink()

    result = runner.invoke(cli.app, ["metrics", str(out), "--corpus", str(corpus)])

    assert result.exit_code == 2, result.output


def test_metrics_exits_2_on_a_symlinked_report_artifact(tmp_path: Path) -> None:
    """Симлинк на месте `report.md` — конфигурация каталога (код 2), не
    «internal error».

    `write_metrics_json`/`write_report`/`write_queue` в `_report` не были
    обёрнуты ни одним `except`, поэтому `ReportError` от подложенной ссылки
    ловил только `_guarded` и выдавал код 3 — тот же класс ошибки, что
    `corpus candidates` уже отображает в код 2 для точно такого же
    `ReportError`.
    """
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    _write_run(out, ["andrei-shtanakov.steward-155"], findings=[_finding()])
    external = tmp_path / "victim.md"
    external.write_text("important", encoding="utf-8")
    (out / "report.md").symlink_to(external)

    result = runner.invoke(cli.app, ["metrics", str(out), "--corpus", str(corpus)])

    assert result.exit_code == 2, result.output
    assert "символическая ссылка" in result.output
    assert external.read_text(encoding="utf-8") == "important"


def test_metrics_leaves_metrics_json_untouched_when_report_md_is_symlinked(
    tmp_path: Path,
) -> None:
    """Отказ на `report.md` не должен успеть перезаписать `metrics.json`.

    Раньше три писателя шли одним `try` без общей пред-проверки:
    `write_metrics_json` (первый) успевал заменить `metrics.json` новыми
    числами атомарным `os.replace` ДО того, как второй писатель наткнётся
    на симлинк и оборвёт остальное — каталог прогона (единица копирования
    в `docs/evidence/`, §7) оставался с артефактами от разных пересчётов,
    хотя код 2 читается как «ничего не тронуто» (ревью-находка части 3,
    minor). Очередь между двумя вызовами закрывается нарочно — иначе
    пересчёт дал бы байт-идентичный `metrics.json`, и тест не отличил бы
    «не записано» от «записано, но не изменилось».
    """
    corpus = _corpus(tmp_path, 155)
    out = tmp_path / "run"
    unlabeled = _finding()
    unlabeled["file"] = "scripts/review/other.sh"
    unlabeled["evidence"] = [{"file": "scripts/review/other.sh", "line": 1, "reason": "r"}]
    _write_run(out, ["andrei-shtanakov.steward-155"], findings=[unlabeled])

    open_queue = runner.invoke(cli.app, ["metrics", str(out), "--corpus", str(corpus)])
    assert open_queue.exit_code == 1, open_queue.output
    before = (out / "metrics.json").read_bytes()
    assert b'"pending_adjudication"' in before

    # Разметчик признал находку ложной: non_defect закрывает очередь, и
    # следующий пересчёт дал бы другой metrics.json (status: ok).
    payload = _case_payload(155)
    payload["non_defects"] = [
        {
            "id": "NF-andrei-shtanakov.steward-155-1",
            "file": "scripts/review/other.sh",
            "line_hint": 644,
            "scenario": "историческая ложная находка",
            "match": {
                "files": ["scripts/review/other.sh"],
                "line_window": 40,
                "keywords_any": ["path", "расширяется"],
            },
        }
    ]
    (corpus / "andrei-shtanakov.steward-155.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8"
    )
    append_registry([load_case(corpus / "andrei-shtanakov.steward-155.yaml")], corpus)
    external = tmp_path / "victim.md"
    external.write_text("important", encoding="utf-8")
    (out / "report.md").unlink()
    (out / "report.md").symlink_to(external)

    second = runner.invoke(cli.app, ["metrics", str(out), "--corpus", str(corpus)])

    assert second.exit_code == 2, second.output
    assert "символическая ссылка" in second.output
    assert (out / "metrics.json").read_bytes() == before


def test_file_lines_at_pins_git_config_and_scrubs_git_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Чтение дерева идёт тем же git и в том же окружении, что и прогон.

    `GIT_DIR` процесса увёл бы `git show` в чужое репо, а глобальный
    `.gitconfig` мог бы подменить обработку файла; провенанс прогона фиксирует
    конфиг кэша, и пересчёт метрик обязан читать так же.
    """
    import subprocess as sp

    checkout = tmp_path / "repo"
    checkout.mkdir()
    sp.run(["git", "init", "-q", str(checkout)], check=True)
    (checkout / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    sp.run(["git", "-C", str(checkout), "add", "a.txt"], check=True)
    sp.run(
        [
            "git",
            "-C",
            str(checkout),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "c",
        ],
        check=True,
    )
    sha = sp.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    cache_root = tmp_path / "cache"
    (cache_root / "org").mkdir(parents=True)
    sp.run(
        ["git", "clone", "--bare", "-q", str(checkout), str(cache_root / "org" / "repo.git")],
        check=True,
    )
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "nowhere.git"))

    lines = cli._file_lines_at(cache_root, "org/repo", sha)

    assert lines("a.txt") == 2
    # Путь нормализуется правилом матчера: `./a.txt` — тот же файл.
    assert lines("./a.txt") == 2


def test_file_lines_at_counts_lines_and_reports_a_missing_object(tmp_path: Path) -> None:
    import subprocess

    checkout = tmp_path / "repo"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    (checkout / "a.txt").write_text("one\ntwo\nthree", encoding="utf-8")
    (checkout / "nested").mkdir()
    (checkout / "nested" / "b.txt").write_text("one\n", encoding="utf-8")
    (checkout / "back\\slash.md").write_text("x\ny\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(checkout), "add", "a.txt", "nested/b.txt", "back\\slash.md"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "c",
        ],
        check=True,
    )
    sha = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    subprocess.run(
        ["git", "clone", "--bare", "-q", str(checkout), str(cache_root / "org" / "repo.git")],
        check=True,
    )
    lines = cli._file_lines_at(cache_root, "org/repo", sha)
    assert lines("a.txt") == 3
    assert lines("missing.txt") is None
    # Литеральный backslash в имени — настоящий git-путь: сперва ищется сырой
    # путь, и только потом Windows-нормализация как запасной вариант.
    assert lines("back\\slash.md") == 2
    assert lines("./a.txt") == 3
    # Композиция: `./` снят, backslash цел. Раньше `./`-префикс отключал
    # сырой вариант целиком, а нормализованный уже терял backslash — ни один
    # кандидат не находил существующий файл (ревью-находка minor, часть 3).
    assert lines("./back\\slash.md") == 2
    # Каталог — не адрес строки: `git show <sha>:dir` печатает листинг дерева,
    # и evidence на каталог иначе считалась бы разрешимой (§9, D11).
    assert lines("nested") is None
    # Схемно допустимый, но не git-путь: `..` не запрещён схемой evidence, а
    # git отказывает не «does not exist»/«not a valid object name», а «is
    # outside repository» — без этой подписи в _MISSING_OBJECT_SIGNATURES
    # такая ссылка поднимала бы CacheError и роняла отчёт всего прогона.
    assert lines("../outside.py") is None
    # NUL в пути — схемно допустимая строка, но `subprocess` не передаёт её
    # в argv вовсе (`ValueError` до всякого exec, до git). Валидного
    # git-пути с NUL не бывает — неразрешимая ссылка, не сбой инструмента.
    assert lines("a\x00b.py") is None

    # Нет кэша (или негоден слаг репо) — спрашивать некого: это `CacheUnavailable`,
    # а не «файла нет». Метрики превращают его в непосчитанную метрику с
    # пометкой, а не в посчитанный ноль (§7).
    with pytest.raises(cli.CacheUnavailable, match="нет"):
        cli._file_lines_at(tmp_path / "nope", "org/repo", sha)("a.txt")
    with pytest.raises(cli.CacheUnavailable, match="org/repo|not-a-slug"):
        cli._file_lines_at(cache_root, "not-a-slug", sha)("a.txt")

    # Кэш существует (клон на месте), но именно этого коммита в нём нет —
    # не материализован, а не «удалён из дерева». `None` здесь означал бы
    # «файла нет на head», хотя дерево этого head вообще не проверялось:
    # находка `file-missing` осталась бы неопровергнутой как TP, а не
    # `evidence_unchecked`.
    unmaterialized_sha = "f" * 40
    with pytest.raises(cli.CacheUnavailable, match="не материализован"):
        cli._file_lines_at(cache_root, "org/repo", unmaterialized_sha)("a.txt")


def test_file_lines_at_raises_when_git_itself_fails(tmp_path: Path) -> None:
    """Сбой git — не факт о файле: `_file_lines_at` обязан бросить, а не вернуть `None`.

    «Объекта нет» git сообщает узнаваемо (код 128 и `does not exist` /
    `Not a valid object name`) — только это и есть «ссылка неразрешима». Любой
    другой отказ (бинарь не запускается, кэш не репозиторий, git упал) —
    ошибка конфигурации, и молчаливый `None` подменил бы её нулём в
    `resolvable_evidence_rate`.
    """
    shim = tmp_path / "git-broken"
    shim.write_text('#!/bin/sh\necho "нечто своё" >&2\nexit 1\n', encoding="utf-8")
    shim.chmod(0o755)
    cache_root = tmp_path / "cache"
    (cache_root / "org" / "repo.git").mkdir(parents=True)

    lines = cli._file_lines_at(cache_root, "org/repo", "a" * 40, git=str(shim))

    with pytest.raises(cli.CacheError, match="нечто своё"):
        lines("a.txt")


def test_file_lines_at_raises_when_the_git_binary_is_missing(tmp_path: Path) -> None:
    """Бинаря git нет вовсе — отказ **сразу**, а не «файла нет» на первом пути.

    Бинарь резолвится тем же `pin_git`, что у прогона, поэтому негодный `--git`
    виден до первого чтения дерева: возвращать функцию, которая упадёт на
    каждом пути, значило бы откладывать один и тот же отказ.
    """
    cache_root = tmp_path / "cache"
    (cache_root / "org" / "repo.git").mkdir(parents=True)

    with pytest.raises(cli.CacheError, match="/nonexistent/git"):
        cli._file_lines_at(cache_root, "org/repo", "a" * 40, git="/nonexistent/git")
