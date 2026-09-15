"""Тесты steward.review_eval.runner: изоляция, sidecar, исходы, run.json.

Модель никогда не вызывается: под измерением — подставной `local.sh`, который
записывает свой env/argv/cwd в файл, пишет вердикт и usage по путям из
`REVIEW_VERDICT_OUT`/`REVIEW_USAGE_OUT` и выходит кодом из `STUB_EXIT`.

Дизайн: docs/superpowers/specs/2026-09-14-review-eval-harness-design.md §6,
§10, §12, D3, D5, D6, D12, D13.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from steward.review_eval.cache import materialize
from steward.review_eval.corpus import Annotation, Case, corpus_digest
from steward.review_eval.matcher import MATCHER_VERSION, rules_digest
from steward.review_eval.runner import (
    KitUnderTest,
    RunManifest,
    RunnerError,
    Variant,
    classify,
    kit_under_test,
    load_results,
    parse_variant,
    provider_env_fingerprint,
    run_all,
    run_case,
    variant_label,
)

REPO = "andrei-shtanakov/steward"
REMOTE_URL = "https://example.invalid/andrei-shtanakov/steward.git"
MISSING_SHA = "f" * 40

VALID_VERDICT = json.dumps({"findings": [], "note": "ok"}, ensure_ascii=False)
#: Sidecar с литералом `Infinity`: Python-декодер принял бы его как float('inf'),
#: jq порога — нет (код 2). Собирается текстом: `json.dumps` такой JSON не пишет.
INFINITY_VERDICT = (
    '{"findings": [{"kind": "defect", "severity": "major", "confidence": "high", '
    '"title": "t", "file": "a.py", "line": Infinity, "scenario": "s", '
    '"observed_result": "o", "expected_result": "e", '
    '"evidence": [{"file": "a.py", "line": 1, "reason": "r"}]}], "note": "ok"}'
)

#: Вердикт, структурно годный (`findings` — список объектов, `note` — строка),
#: но схемно негодный: у находки нет обязательного `title`. Настоящий кит на
#: таком выходит кодом 2 из `apply-threshold.sh`.
SCHEMA_INVALID_VERDICT = json.dumps(
    {
        "findings": [
            {
                "kind": "defect",
                "severity": "major",
                "file": "a.txt",
                "line": 1,
                "scenario": "s",
                "observed_result": "o",
                "expected_result": "e",
                "evidence": [],
                "confidence": "high",
            }
        ],
        "note": "ok",
    },
    ensure_ascii=False,
)
GUARDRAIL_TEXT = "диф больше поддерживаемого одним прогоном: 900 файл(ов)"

#: Подставной кит: пишет протокол вызова, sidecar-и по путям из env и выходит
#: кодом `STUB_EXIT`. `STUB_COUNTER` копит по строке на вызов — так видно, что
#: идемпотентный повтор `run_all` кит не запускал.
_STUB_LOCAL_SH = """#!/bin/sh
{
  printf 'args:%s\\n' "$*"
  env | sed -n -e 's/^\\(REVIEW_[A-Z_]*\\)=.*/have:\\1/p' \\
            -e 's/^\\(GIT_[A-Z_]*\\)=.*/have:\\1/p' | sort
  printf 'harness:%s model:%s effort:%s\\n' \\
      "${REVIEW_HARNESS-<unset>}" "${REVIEW_MODEL-<unset>}" "${REVIEW_EFFORT-<unset>}"
  printf 'kit_dir:%s prompt:%s schema:%s\\n' \\
      "${REVIEW_KIT_DIR-<unset>}" "${REVIEW_PROMPT-<unset>}" "${REVIEW_SCHEMA-<unset>}"
  printf 'pwd:%s\\n' "$(pwd)"
  printf 'atxt:%s\\n' "$(cat a.txt 2>/dev/null)"
} > "$STUB_RECORD"
[ -z "${STUB_COUNTER-}" ] || echo call >> "$STUB_COUNTER"
if [ -n "${STUB_VERDICT_BODY-}" ]; then
  mkdir -p "$(dirname "$REVIEW_VERDICT_OUT")"
  printf '%s' "$STUB_VERDICT_BODY" > "$REVIEW_VERDICT_OUT"
fi
if [ -n "${STUB_USAGE_BODY-}" ]; then
  mkdir -p "$(dirname "$REVIEW_USAGE_OUT")"
  printf '%s' "$STUB_USAGE_BODY" > "$REVIEW_USAGE_OUT"
fi
[ -z "${STUB_STDERR-}" ] || echo "$STUB_STDERR" >&2
echo "stub stdout"
exit "${STUB_EXIT:-0}"
"""

#: git-шим, падающий на любом `fetch`: доказывает, что офлайн-путь раннера
#: (§6, D3) сети не касается даже попыткой.
_NO_FETCH_GIT = """#!/bin/sh
for arg in "$@"; do
  [ "$arg" = fetch ] || continue
  echo "fetch blocked (test shim)" >&2
  exit 1
done
exec "{real_git}" "$@"
"""

#: git-шим, ломающий уборку worktree: прогон завершён и оплачен, артефакты
#: обязаны остаться, а сбой уборки — попасть в `teardown_error`.
_NO_REMOVE_GIT = """#!/bin/sh
prev=""
for arg in "$@"; do
  if [ "$prev" = worktree ] && [ "$arg" = remove ]; then
    echo "worktree remove blocked (test shim)" >&2
    exit 1
  fi
  prev="$arg"
done
exec "{real_git}" "$@"
"""


def _write_shim(tmp_path: Path, name: str, template: str) -> Path:
    shim = tmp_path / name
    shim.write_text(template.format(real_git=_real_git()), encoding="utf-8")
    shim.chmod(0o755)
    return shim


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _rev_parse(repo: Path, ref: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ref],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _real_git() -> str:
    result = subprocess.run(["which", "git"], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _make_fixture_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """Репо с двумя коммитами (``a.txt`` = ``first`` → ``second``)."""
    repo = tmp_path / "fixture-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("first\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "first")
    first = _rev_parse(repo, "HEAD")
    (repo / "a.txt").write_text("second\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-a", "-m", "second")
    second = _rev_parse(repo, "HEAD")
    return repo, first, second


def _make_three_commit_repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    """Репо из трёх коммитов (``a.txt`` = ``zero`` → ``first`` → ``second``).

    Нужен там, где кейсу требуется непустой диапазон, **не** оканчивающийся на
    текущем чекауте: базой берётся `zero`, головой — `first`.
    """
    repo = tmp_path / "three-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    shas: list[str] = []
    for body in ("zero", "first", "second"):
        (repo / "a.txt").write_text(f"{body}\n", encoding="utf-8")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-q", "-m", body)
        shas.append(_rev_parse(repo, "HEAD"))
    return repo, shas[0], shas[1], shas[2]


def _make_empty_range_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """Репо, где база и голова — **разные коммиты с одинаковым деревом**.

    Правка и её откат следующим коммитом: sha различаются, диф диапазона пуст,
    и кит выходит кодом 0 без sidecar — то есть выглядит как механический сбой.
    """
    repo = tmp_path / "flat-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("first\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "first")
    base = _rev_parse(repo, "HEAD")
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-a", "-m", "изменение")
    (repo / "a.txt").write_text("first\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-a", "-m", "откат изменения")
    head = _rev_parse(repo, "HEAD")
    return repo, base, head


def _make_cache(tmp_path: Path, repo: Path, shas: list[str]) -> Path:
    cache_root = tmp_path / "cache"
    materialize(cache_root, REPO, shas, local_checkout=repo, remote_url=REMOTE_URL)
    return cache_root


def _make_stub_kit(tmp_path: Path) -> KitUnderTest:
    """Подставной кит: только `local.sh` реален, дайджесты фиктивны."""
    kit_dir = tmp_path / "stub-kit"
    kit_dir.mkdir()
    local_sh = kit_dir / "local.sh"
    local_sh.write_text(_STUB_LOCAL_SH, encoding="utf-8")
    local_sh.chmod(0o755)
    prompt = tmp_path / "review-prompt.md"
    prompt.write_text("prompt\n", encoding="utf-8")
    schema = tmp_path / "review-schema.json"
    schema.write_text("{}\n", encoding="utf-8")
    return KitUnderTest(
        kit_dir=kit_dir,
        prompt=prompt,
        schema=schema,
        commit="0" * 40,
        digests={"local_sh_sha256": "deadbeef"},
    )


def _make_case(
    *,
    base_sha: str,
    head_sha: str,
    case_id: str = "steward-155",
    expected_outcome: str = "verdict",
    local_args: tuple[str, ...] = (),
    cls: str = "defective",
) -> Case:
    return Case(
        case_id=case_id,
        repo=REPO,
        pr=155,
        base_sha=base_sha,
        head_sha=head_sha,
        cls=cls,
        local_args=local_args,
        expected_outcome=expected_outcome,
        annotation=Annotation(
            status="adjudicated",
            blocking_complete=True,
            adjudicated_by="github:andrei-shtanakov",
            adjudicated_at="2026-09-14",
            source="manual",
        ),
        defects=(),
        non_defects=(),
        notes="",
    )


def _env_base(record: Path, **extra: str) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "STUB_RECORD": str(record),
        # Наследованный оверрайд целиком: раннер обязан его вычистить (§6.3).
        "REVIEW_CMD": "echo not-the-measured-path",
        "REVIEW_MODEL": "stale-model",
    }
    env.update(extra)
    return env


# --- parse_variant / variant_label ------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("claude:claude-opus-5", Variant("claude", "claude-opus-5", None)),
        ("codex:gpt-5.4", Variant("codex", "gpt-5.4", None)),
        ("codex:gpt-5.4:high", Variant("codex", "gpt-5.4", "high")),
        ("claude:claude-opus-5:medium", Variant("claude", "claude-opus-5", "medium")),
    ],
)
def test_parse_variant_table(text: str, expected: Variant) -> None:
    assert parse_variant(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "claude",
        "claude:",
        ":claude-opus-5",
        "llama:l3",
        "claude:claude-opus-5:",
        "claude:claude opus:high",
        "claude:claude-opus-5:high --model x",
        "claude:claude-opus-5:high:extra",
        " claude:claude-opus-5",
        # Метка варианта — имя каталога артефактов, и `--rerun` его rmtree-ит.
        # Слеш и `..` в модели/effort — путь наружу из `--out`.
        "codex:x/../../../outside",
        "codex:../../etc",
        "codex:gpt-5.4:../..",
        "codex:..",
        "codex:.",
        "codex:gpt-5.4:.",
        "codex:a/b",
    ],
)
def test_parse_variant_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_variant(text)


@pytest.mark.parametrize(
    "text",
    ["claude:claude-opus-5", "codex:gpt-5.4:high"],
)
def test_variant_label_round_trip(text: str) -> None:
    assert variant_label(parse_variant(text)) == text


@pytest.mark.parametrize("model", ["a/b", "..", "x y"], ids=["slash", "dotdot", "space"])
def test_variant_label_refuses_a_variant_built_in_code_with_a_bad_model(model: str) -> None:
    """Метка — имя каталога артефактов; `Variant`, собранный в коде в обход
    `parse_variant`, обязан пройти те же проверки, иначе `a/b` даст лишний
    уровень каталога, который поиск результатов не увидит.
    """
    with pytest.raises(RunnerError, match="метка варианта"):
        variant_label(Variant("codex", model, None))


# --- classify ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("exit_code", "sidecar", "valid", "stderr", "outcome"),
    [
        (0, True, True, "", "verdict"),
        (1, True, True, "", "verdict"),
        (2, False, False, GUARDRAIL_TEXT, "guardrail_rejection"),
        # Код 2 при годном вердикте: sidecar пишется **до** порога, значит
        # порог отказал по своей причине — конфигурация, не ошибка модели.
        (2, True, True, "", "config_failure"),
        (2, True, True, GUARDRAIL_TEXT, "config_failure"),
        (2, True, False, GUARDRAIL_TEXT, "invalid_verdict"),
        (2, True, False, "", "invalid_verdict"),
        (0, True, False, "", "invalid_verdict"),
        (1, True, False, "", "invalid_verdict"),
        (2, False, False, "нет манифеста контекста", "config_failure"),
        (3, False, False, "", "mechanical_failure"),
        (3, True, True, "", "mechanical_failure"),
        (0, False, False, "", "mechanical_failure"),
        (1, False, False, "", "mechanical_failure"),
        (127, False, False, "", "mechanical_failure"),
    ],
)
def test_classify_table(
    exit_code: int, sidecar: bool, valid: bool, stderr: str, outcome: str
) -> None:
    assert classify(exit_code, sidecar, valid, stderr) == outcome


def test_classify_guardrail_beats_config_failure() -> None:
    """Текст потолка дифа отличает ожидаемый отказ `large` от ошибки конфигурации."""
    assert classify(2, False, False, f"prefix {GUARDRAIL_TEXT} suffix") == "guardrail_rejection"


# --- run_case: исходы -------------------------------------------------------


@pytest.mark.parametrize(
    ("exit_code", "verdict_body", "stderr", "outcome", "reviewer_ran"),
    [
        (0, VALID_VERDICT, "", "verdict", True),
        (1, VALID_VERDICT, "", "verdict", True),
        (2, "", GUARDRAIL_TEXT, "guardrail_rejection", False),
        # Код 2 при **схемно годном** вердикте — сбой конфигурации, а не
        # ошибка модели: sidecar пишется до порога, и порог на годном вердикте
        # кодом 2 отказывает по своей причине (нет jq, негодный аргумент).
        (2, VALID_VERDICT, "", "config_failure", True),
        (2, VALID_VERDICT, GUARDRAIL_TEXT, "config_failure", True),
        # Код 2 при негодном по схеме вердикте — ровно отказ порога.
        (2, SCHEMA_INVALID_VERDICT, "", "invalid_verdict", True),
        (0, "{not json", "", "invalid_verdict", True),
        (0, '{"findings": "nope", "note": "x"}', "", "invalid_verdict", True),
        # Структурно годен, схемно нет (находка без `title`) — настоящий кит
        # отказал бы кодом 2, и это ошибка **модели**, а не инструмента.
        (0, SCHEMA_INVALID_VERDICT, "", "invalid_verdict", True),
        # `Infinity` — не JSON: jq порога такой файл не разбирает и уходит в код 2.
        # Python-декодер по умолчанию принял бы его как float('inf') и превратил
        # «код 2 + якобы годный sidecar» в config_failure — ошибку не модели.
        (2, INFINITY_VERDICT, "", "invalid_verdict", True),
        (2, "", "REVIEW_PROMPT не найден", "config_failure", False),
        (3, "", "harness-claude упал", "mechanical_failure", False),
        (0, "", "", "mechanical_failure", False),
    ],
)
def test_run_case_classifies_outcomes(
    tmp_path: Path,
    exit_code: int,
    verdict_body: str,
    stderr: str,
    outcome: str,
    reviewer_ran: bool,
) -> None:
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    record = tmp_path / "record.txt"
    env = _env_base(
        record,
        STUB_EXIT=str(exit_code),
        STUB_VERDICT_BODY=verdict_body,
        STUB_STDERR=stderr,
    )
    case = _make_case(base_sha=first, head_sha=second)

    result = run_case(
        case,
        Variant("claude", "claude-opus-5", None),
        1,
        kit=kit,
        cache_root=cache_root,
        out_dir=out_dir,
        env_base=env,
    )

    assert result.outcome == outcome
    assert result.reviewer_ran is reviewer_ran
    assert result.exit_code == exit_code
    assert result.unexpected is (outcome != "verdict")


def test_run_case_guardrail_expected_is_not_unexpected(tmp_path: Path) -> None:
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    case = _make_case(
        base_sha=first,
        head_sha=second,
        cls="large",
        expected_outcome="guardrail_rejection",
    )

    result = run_case(
        case,
        Variant("codex", "gpt-5.4", None),
        1,
        kit=kit,
        cache_root=cache_root,
        out_dir=tmp_path / "run",
        env_base=_env_base(tmp_path / "record.txt", STUB_EXIT="2", STUB_STDERR=GUARDRAIL_TEXT),
    )

    assert result.outcome == "guardrail_rejection"
    assert result.unexpected is False


# --- run_case: артефакты, env, изоляция -------------------------------------


def test_run_case_artefacts_and_env(tmp_path: Path) -> None:
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    record = tmp_path / "record.txt"
    case = _make_case(base_sha=first, head_sha=second, local_args=("--max-diff-bytes", "900000"))

    result = run_case(
        case,
        Variant("claude", "claude-opus-5", "high"),
        2,
        kit=kit,
        cache_root=cache_root,
        out_dir=out_dir,
        env_base=_env_base(
            record,
            STUB_EXIT="0",
            STUB_VERDICT_BODY=VALID_VERDICT,
            STUB_USAGE_BODY=json.dumps({"total_cost_usd": 0.42}),
        ),
    )

    rep_dir = out_dir / "cases" / "steward-155" / "claude:claude-opus-5:high" / "2"
    assert (rep_dir / "verdict.json").is_file()
    assert (rep_dir / "usage.json").is_file()
    assert (rep_dir / "stdout.txt").read_text(encoding="utf-8").strip() == "stub stdout"
    assert (rep_dir / "stderr.txt").is_file()
    assert json.loads((rep_dir / "result.json").read_text(encoding="utf-8"))["outcome"] == "verdict"

    assert result.repetition_id == 2
    assert result.variant == "claude:claude-opus-5:high"
    assert result.requested_effort == "high"
    assert result.cost_status == "available"
    assert result.wall_clock_s > 0
    assert (out_dir / result.stdout_path).is_file()
    assert result.verdict_path is not None
    assert (out_dir / result.verdict_path).is_file()
    assert result.usage_path is not None
    assert (out_dir / result.usage_path).is_file()

    text = record.read_text(encoding="utf-8")
    assert "have:REVIEW_CMD" not in text
    assert "harness:claude model:claude-opus-5 effort:high" in text
    assert f"kit_dir:{kit.kit_dir} prompt:{kit.prompt} schema:{kit.schema}" in text
    assert f"args:--base {first} --head {second} --format text --max-diff-bytes 900000" in text


def test_run_case_omits_effort_when_absent(tmp_path: Path) -> None:
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    record = tmp_path / "record.txt"

    result = run_case(
        _make_case(base_sha=first, head_sha=second),
        Variant("claude", "claude-opus-5", None),
        1,
        kit=kit,
        cache_root=cache_root,
        out_dir=tmp_path / "run",
        env_base=_env_base(record, STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT),
    )

    text = record.read_text(encoding="utf-8")
    assert "have:REVIEW_EFFORT" not in text
    assert "effort:<unset>" in text
    assert result.requested_effort is None


@pytest.mark.parametrize(
    ("usage_body", "cost_status"),
    [
        ("", "unavailable"),
        (json.dumps({"total_cost_usd": None}), "unavailable"),
        (json.dumps({"usage": None}), "unavailable"),
        ("{not json", "unavailable"),
        (json.dumps({"total_cost_usd": 0.0}), "available"),
        (json.dumps({"total_cost_usd": 12}), "available"),
    ],
)
def test_run_case_cost_status(tmp_path: Path, usage_body: str, cost_status: str) -> None:
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)

    result = run_case(
        _make_case(base_sha=first, head_sha=second),
        Variant("codex", "gpt-5.4", None),
        1,
        kit=kit,
        cache_root=cache_root,
        out_dir=tmp_path / "run",
        env_base=_env_base(
            tmp_path / "record.txt",
            STUB_EXIT="0",
            STUB_VERDICT_BODY=VALID_VERDICT,
            STUB_USAGE_BODY=usage_body,
        ),
    )

    assert result.cost_status == cost_status
    assert (result.usage_path is None) is (usage_body == "")


def test_run_case_runs_in_worktree_on_historical_sha(tmp_path: Path) -> None:
    """D3: кит читает дерево `head_sha`, а не текущий чекаут.

    Репо из трёх коммитов: голова кейса — **средний**, а локальный чекаут стоит
    на последнем. Прежде тест брал `base == head`, но такой диапазон пуст, и
    прогон теперь честно объявляет его `empty_range` — кит не запускается, и
    проверять было бы нечего.
    """
    repo, zero, first, second = _make_three_commit_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [zero, first, second])
    kit = _make_stub_kit(tmp_path)
    record = tmp_path / "record.txt"
    out_dir = tmp_path / "run"

    run_case(
        _make_case(base_sha=zero, head_sha=first),
        Variant("claude", "claude-opus-5", None),
        1,
        kit=kit,
        cache_root=cache_root,
        out_dir=out_dir,
        env_base=_env_base(record, STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT),
    )

    text = record.read_text(encoding="utf-8")
    assert "atxt:first" in text
    assert "atxt:second" not in text
    assert str(out_dir) in text  # cwd — worktree под out_dir/scratch, не чекаут steward


def test_run_case_removes_worktree_unless_kept(tmp_path: Path) -> None:
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    env = _env_base(tmp_path / "record.txt", STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT)
    case = _make_case(base_sha=first, head_sha=second)

    run_case(
        case,
        Variant("claude", "claude-opus-5", None),
        1,
        kit=kit,
        cache_root=cache_root,
        out_dir=out_dir,
        env_base=env,
    )
    assert not (out_dir / "scratch" / "steward-155" / "claude:claude-opus-5" / "1").exists()

    run_case(
        case,
        Variant("claude", "claude-opus-5", None),
        2,
        kit=kit,
        cache_root=cache_root,
        out_dir=out_dir,
        env_base=env,
        keep_worktrees=True,
    )
    kept = out_dir / "scratch" / "steward-155" / "claude:claude-opus-5" / "2"
    assert (kept / "a.txt").is_file()


def test_run_case_missing_object_never_fetches(tmp_path: Path) -> None:
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    shim = tmp_path / "git-no-fetch"
    shim.write_text(_NO_FETCH_GIT.format(real_git=_real_git()), encoding="utf-8")
    shim.chmod(0o755)

    with pytest.raises(RunnerError) as excinfo:
        run_case(
            _make_case(base_sha=first, head_sha=MISSING_SHA),
            Variant("claude", "claude-opus-5", None),
            1,
            kit=kit,
            cache_root=cache_root,
            out_dir=tmp_path / "run",
            env_base=_env_base(tmp_path / "record.txt"),
            git=str(shim),
        )

    message = str(excinfo.value)
    assert MISSING_SHA in message
    assert REPO in message


def test_run_case_missing_base_object_is_runner_error(tmp_path: Path) -> None:
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [second])
    kit = _make_stub_kit(tmp_path)

    with pytest.raises(RunnerError):
        run_case(
            _make_case(base_sha=MISSING_SHA, head_sha=second),
            Variant("claude", "claude-opus-5", None),
            1,
            kit=kit,
            cache_root=cache_root,
            out_dir=tmp_path / "run",
            env_base=_env_base(tmp_path / "record.txt"),
        )
    assert first  # фикстура держит оба коммита, в кэше — только head


# --- run_all ----------------------------------------------------------------


def test_run_all_writes_manifest_and_is_idempotent(tmp_path: Path) -> None:
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    counter = tmp_path / "calls.txt"
    env = _env_base(
        tmp_path / "record.txt",
        STUB_EXIT="0",
        STUB_VERDICT_BODY=VALID_VERDICT,
        STUB_COUNTER=str(counter),
    )
    cases = [_make_case(base_sha=first, head_sha=second)]
    variants = [Variant("claude", "claude-opus-5", None), Variant("codex", "gpt-5.4", "high")]

    manifest = run_all(
        cases,
        variants,
        repetitions=2,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base=env,
    )

    assert counter.read_text(encoding="utf-8").count("call") == 4
    assert manifest.variants == [
        {
            "label": "claude:claude-opus-5",
            "harness": "claude",
            "model": "claude-opus-5",
            "requested_effort": None,
        },
        {
            "label": "codex:gpt-5.4:high",
            "harness": "codex",
            "model": "gpt-5.4",
            "requested_effort": "high",
        },
    ]
    assert manifest.corpus_digest == corpus_digest(cases)
    # Кейсы прогона названы по именам, а не только дайджестом: дайджест
    # отвечает «тот ли корпус», список — «какие кейсы измеряли».
    assert manifest.cases == sorted(case.case_id for case in cases)
    assert manifest.matcher_version == MATCHER_VERSION
    assert manifest.matcher_rules_digest == rules_digest()
    assert manifest.repetitions == 2
    assert manifest.jobs == 1
    assert manifest.finished >= manifest.started

    payload = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
    assert payload["run_id"] == manifest.run_id
    assert payload["finished"] == manifest.finished
    assert payload["kit"]["commit"] == kit.commit
    assert payload["kit"]["local_sh_sha256"] == "deadbeef"
    assert set(payload["tools"]) == {"claude", "codex", "git"}
    assert payload["variants"] == manifest.variants
    assert payload["cases"] == manifest.cases

    # Повтор с тем же --out: готовые result.json пропускаются, кит не зовётся.
    again = run_all(
        cases,
        variants,
        repetitions=2,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base=env,
    )
    assert counter.read_text(encoding="utf-8").count("call") == 4
    assert again.run_id == manifest.run_id

    run_all(
        cases,
        variants,
        repetitions=2,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base=env,
        rerun=True,
    )
    assert counter.read_text(encoding="utf-8").count("call") == 8


def test_run_all_run_id_from_out_dir_name(tmp_path: Path) -> None:
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "runs" / "20260914T101112Z-0123abcd"

    manifest = run_all(
        [_make_case(base_sha=first, head_sha=second)],
        [Variant("claude", "claude-opus-5", None)],
        repetitions=1,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base=_env_base(tmp_path / "record.txt", STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT),
    )

    assert manifest.run_id == "20260914T101112Z-0123abcd"


def test_run_all_generated_run_id_is_stable(tmp_path: Path) -> None:
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    cases = [_make_case(base_sha=first, head_sha=second)]
    variants = [Variant("claude", "claude-opus-5", None)]
    env = _env_base(tmp_path / "record.txt", STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT)

    one = run_all(
        cases,
        variants,
        repetitions=1,
        out_dir=tmp_path / "a",
        kit=kit,
        cache_root=cache_root,
        env_base=env,
    )
    two = run_all(
        cases,
        variants,
        repetitions=1,
        out_dir=tmp_path / "b",
        kit=kit,
        cache_root=cache_root,
        env_base=env,
    )

    assert one.run_id.split("-")[1] == two.run_id.split("-")[1]
    assert one.run_id.endswith(
        hashlib.sha256(
            ("claude:claude-opus-5\n" + corpus_digest(cases)).encode("utf-8")
        ).hexdigest()[:8]
    )


@pytest.mark.parametrize(
    "local_args",
    [
        ("--head", "c" * 40),
        ("--base", "c" * 40),
        ("--format", "markdown"),
        ("--fetch",),
        ("--remote", "upstream"),
        ("--fingerprint-only",),
        ("--print-review-cmd",),
        ("--head=" + "c" * 40,),
    ],
    ids=[
        "head",
        "base",
        "format",
        "fetch",
        "remote",
        "fingerprint-only",
        "print-review-cmd",
        "head-with-equals",
    ],
)
def test_run_case_refuses_range_overriding_local_args(
    tmp_path: Path, local_args: tuple[str, ...]
) -> None:
    """Вторая линия обороны: раннер сам отказывается подменять диапазон.

    Схема корпуса такие `local_args` не пропускает, но `Case` собирают и в
    коде (тесты, будущие вызовы), а цена ошибки — измерение чужого диапазона
    под gold этого. Отказ — до создания worktree и до вызова кита.
    """
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    record = tmp_path / "record.txt"

    with pytest.raises(RunnerError, match="local_args"):
        run_case(
            _make_case(base_sha=first, head_sha=second, local_args=local_args),
            Variant("claude", "claude-opus-5", None),
            1,
            kit=kit,
            cache_root=cache_root,
            out_dir=tmp_path / "run",
            env_base=_env_base(record, STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT),
        )

    assert not record.exists(), "кит не должен был запуститься"


def test_run_all_refuses_range_overriding_local_args_before_the_first_run(
    tmp_path: Path,
) -> None:
    """Проверка — до первого прогона: иначе оплаченные прогоны обрываются на середине."""
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    record = tmp_path / "record.txt"
    cases = [
        _make_case(base_sha=first, head_sha=second, case_id="steward-155"),
        _make_case(
            base_sha=first,
            head_sha=second,
            case_id="steward-157",
            local_args=("--head", "c" * 40),
        ),
    ]

    with pytest.raises(RunnerError, match="steward-157"):
        run_all(
            cases,
            [Variant("claude", "claude-opus-5", None)],
            repetitions=1,
            out_dir=tmp_path / "run",
            kit=kit,
            cache_root=cache_root,
            env_base=_env_base(record, STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT),
        )

    assert not record.exists(), "кит не должен был запуститься ни по одному кейсу"


def test_run_all_records_provider_env_names_without_values(tmp_path: Path) -> None:
    """`run.json` фиксирует **имена** provider-переменных окружения, не значения.

    «Каким китом мерили» уже в манифесте; «в каком окружении» — нет, а именно
    оно решает, к какому аккаунту и через какой прокси ушёл вызов. Значения не
    пишутся никогда: это ключи.
    """
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    env = _env_base(
        tmp_path / "record.txt",
        STUB_EXIT="0",
        STUB_VERDICT_BODY=VALID_VERDICT,
        ANTHROPIC_API_KEY="секрет",
        anthropic_base_url="https://example.invalid",
        CODEX_HOME="/tmp/codex",
        HTTPS_PROXY="http://proxy.invalid:3128",
        no_proxy="localhost",
        OPENAI_API_KEY="секрет-2",
        CLAUDE_CODE_SOMETHING="x",
        UNRELATED_TOKEN="секрет-3",
    )

    manifest = run_all(
        [_make_case(base_sha=first, head_sha=second)],
        [Variant("claude", "claude-opus-5", None)],
        repetitions=1,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base=env,
    )

    assert manifest.provider_env_names == [
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_SOMETHING",
        "CODEX_HOME",
        "HTTPS_PROXY",
        "OPENAI_API_KEY",
        "anthropic_base_url",
        "no_proxy",
    ]
    payload = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
    assert payload["provider_env_names"] == manifest.provider_env_names
    text = (out_dir / "run.json").read_text(encoding="utf-8")
    assert "секрет" not in text
    assert "UNRELATED_TOKEN" not in text


def test_run_all_removes_the_empty_scratch_tree(tmp_path: Path) -> None:
    """После чистого прогона пустое дерево `scratch/` не остаётся мусором."""
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"

    run_all(
        [_make_case(base_sha=first, head_sha=second)],
        [Variant("claude", "claude-opus-5", None)],
        repetitions=1,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base=_env_base(tmp_path / "record.txt", STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT),
    )

    assert not (out_dir / "scratch").exists()
    assert (out_dir / "cases").is_dir()


def test_run_all_keeps_scratch_when_worktrees_are_kept(tmp_path: Path) -> None:
    """`--keep-worktrees` — явная просьба оставить деревья: не убираем ничего."""
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"

    run_all(
        [_make_case(base_sha=first, head_sha=second)],
        [Variant("claude", "claude-opus-5", None)],
        repetitions=1,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base=_env_base(tmp_path / "record.txt", STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT),
        keep_worktrees=True,
    )

    kept = out_dir / "scratch" / "steward-155" / "claude:claude-opus-5" / "1"
    assert (kept / "a.txt").is_file()


# --- kit_under_test ---------------------------------------------------------


def _steward_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_kit_under_test_digests_match_real_files() -> None:
    root = _steward_root()
    kit = kit_under_test(root)

    assert kit.kit_dir == root / "scripts" / "review"
    assert kit.prompt == root / ".github" / "codex" / "review-prompt.md"
    assert kit.schema == root / ".github" / "codex" / "review-schema.json"
    assert kit.commit == _rev_parse(root, "HEAD")

    expected = {
        "prompt_sha256": kit.prompt,
        "schema_sha256": kit.schema,
        "threshold_sha256": kit.kit_dir / "apply-threshold.sh",
        "local_sh_sha256": kit.kit_dir / "local.sh",
        "collect_context_sha256": kit.kit_dir / "collect-context.sh",
        "harness_claude_sha256": kit.kit_dir / "harness-claude",
        "build_prompt_sha256": kit.kit_dir / "build-prompt.sh",
    }
    assert set(kit.digests) == set(expected)
    for key, path in expected.items():
        assert kit.digests[key] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_kit_under_test_missing_files_is_runner_error(tmp_path: Path) -> None:
    with pytest.raises(RunnerError) as excinfo:
        kit_under_test(tmp_path)
    assert "local.sh" in str(excinfo.value)


def test_run_all_parallel_jobs_cover_every_triple(tmp_path: Path) -> None:
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    counter = tmp_path / "calls.txt"
    cases = [
        _make_case(base_sha=first, head_sha=second, case_id="steward-155"),
        _make_case(base_sha=first, head_sha=second, case_id="steward-157"),
    ]

    manifest = run_all(
        cases,
        [Variant("claude", "claude-opus-5", None)],
        repetitions=2,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        jobs=2,
        env_base=_env_base(
            tmp_path / "record.txt",
            STUB_EXIT="0",
            STUB_VERDICT_BODY=VALID_VERDICT,
            STUB_COUNTER=str(counter),
        ),
    )

    assert manifest.jobs == 2
    assert counter.read_text(encoding="utf-8").count("call") == 4
    for case_id in ("steward-155", "steward-157"):
        for rep in ("1", "2"):
            result = out_dir / "cases" / case_id / "claude:claude-opus-5" / rep / "result.json"
            assert json.loads(result.read_text(encoding="utf-8"))["outcome"] == "verdict"


@pytest.mark.parametrize(("repetitions", "jobs"), [(0, 1), (1, 0), (-1, 1)])
def test_run_all_rejects_bad_counts(tmp_path: Path, repetitions: int, jobs: int) -> None:
    kit = _make_stub_kit(tmp_path)
    with pytest.raises(RunnerError):
        run_all(
            [],
            [Variant("claude", "claude-opus-5", None)],
            repetitions=repetitions,
            out_dir=tmp_path / "run",
            kit=kit,
            cache_root=tmp_path / "cache",
            jobs=jobs,
        )


def test_run_all_rejects_an_empty_variant_list(tmp_path: Path) -> None:
    """Без варианта измерять нечего: пустой список — отказ, а не «успешный» прогон
    с завершённым run.json и без единого result.json.
    """
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    with pytest.raises(RunnerError, match="variants"):
        run_all(
            [],
            [],
            repetitions=1,
            out_dir=out_dir,
            kit=kit,
            cache_root=tmp_path / "cache",
        )
    assert not (out_dir / "run.json").exists()


def test_run_case_keeps_artefacts_when_teardown_fails(tmp_path: Path) -> None:
    """Сбой уборки worktree не имеет права потерять уже оплаченный прогон."""
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    shim = _write_shim(tmp_path, "git-no-remove", _NO_REMOVE_GIT)

    result = run_case(
        _make_case(base_sha=first, head_sha=second),
        Variant("claude", "claude-opus-5", None),
        1,
        kit=kit,
        cache_root=cache_root,
        out_dir=out_dir,
        env_base=_env_base(tmp_path / "record.txt", STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT),
        git=str(shim),
    )

    rep_dir = out_dir / "cases" / "steward-155" / "claude:claude-opus-5" / "1"
    payload = json.loads((rep_dir / "result.json").read_text(encoding="utf-8"))
    assert payload["outcome"] == "verdict"
    assert payload["teardown_error"] is not None
    assert "worktree remove" in payload["teardown_error"]
    assert result.outcome == "verdict"
    assert result.teardown_error is not None
    assert (rep_dir / "stdout.txt").is_file()
    assert (rep_dir / "verdict.json").is_file()


def test_run_all_continues_after_teardown_failure(tmp_path: Path) -> None:
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    shim = _write_shim(tmp_path, "git-no-remove", _NO_REMOVE_GIT)
    cases = [
        _make_case(base_sha=first, head_sha=second, case_id="steward-155"),
        _make_case(base_sha=first, head_sha=second, case_id="steward-157"),
    ]

    run_all(
        cases,
        [Variant("claude", "claude-opus-5", None)],
        repetitions=1,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base=_env_base(tmp_path / "record.txt", STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT),
        git=str(shim),
    )

    results = load_results(out_dir)
    assert [item.case_id for item in results] == ["steward-155", "steward-157"]
    assert all(item.outcome == "verdict" for item in results)
    assert all(item.teardown_error is not None for item in results)


def test_run_case_scrubs_git_env(tmp_path: Path) -> None:
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    record = tmp_path / "record.txt"

    run_case(
        _make_case(base_sha=first, head_sha=second),
        Variant("claude", "claude-opus-5", None),
        1,
        kit=kit,
        cache_root=cache_root,
        out_dir=tmp_path / "run",
        env_base=_env_base(
            record,
            STUB_EXIT="0",
            STUB_VERDICT_BODY=VALID_VERDICT,
            GIT_DIR="/nonexistent/.git",
            GIT_WORK_TREE="/nonexistent",
            GIT_INDEX_FILE="/nonexistent/index",
        ),
    )

    text = record.read_text(encoding="utf-8")
    assert "have:GIT_" not in text
    assert "have:REVIEW_KIT_DIR" in text  # наши переменные на месте
    assert "atxt:second" in text  # кит читал именно worktree кейса


def test_run_case_drops_stale_sidecars(tmp_path: Path) -> None:
    """Вердикт прошлого прогона этой тройки не должен стать фактом нового."""
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    rep_dir = out_dir / "cases" / "steward-155" / "claude:claude-opus-5" / "1"
    rep_dir.mkdir(parents=True)
    (rep_dir / "verdict.json").write_text(VALID_VERDICT, encoding="utf-8")
    (rep_dir / "usage.json").write_text(json.dumps({"total_cost_usd": 9.99}), encoding="utf-8")

    result = run_case(
        _make_case(base_sha=first, head_sha=second),
        Variant("claude", "claude-opus-5", None),
        1,
        kit=kit,
        cache_root=cache_root,
        out_dir=out_dir,
        env_base=_env_base(tmp_path / "record.txt", STUB_EXIT="3"),
    )

    assert not (rep_dir / "verdict.json").exists()
    assert not (rep_dir / "usage.json").exists()
    assert result.reviewer_ran is False
    assert result.outcome == "mechanical_failure"
    assert result.verdict_path is None
    assert result.cost_status == "unavailable"


def test_run_all_preserves_started_on_resume(tmp_path: Path) -> None:
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    cases = [_make_case(base_sha=first, head_sha=second)]
    variants = [Variant("claude", "claude-opus-5", None)]
    env = _env_base(tmp_path / "record.txt", STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT)

    first_manifest = run_all(
        cases,
        variants,
        repetitions=1,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base=env,
    )

    run_json = out_dir / "run.json"
    payload = json.loads(run_json.read_text(encoding="utf-8"))
    payload["started"] = "2026-01-01T00:00:00Z"
    run_json.write_text(json.dumps(payload), encoding="utf-8")

    resumed = run_all(
        cases,
        variants,
        repetitions=2,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base=env,
    )

    assert resumed.started == "2026-01-01T00:00:00Z"
    assert resumed.run_id == first_manifest.run_id
    assert resumed.finished > resumed.started
    assert json.loads(run_json.read_text(encoding="utf-8"))["started"] == "2026-01-01T00:00:00Z"


@dataclasses.dataclass(frozen=True)
class _Resume:
    """Готовый прогон плюс аргументы, которыми в него доливают."""

    cases: list[Case]
    variants: list[Variant]
    kit: KitUnderTest
    cache_root: Path
    env_base: dict[str, str]
    out_dir: Path
    counter: Path

    def calls(self) -> int:
        """Сколько раз кит был запущен за всё время (счётчик подставного `local.sh`)."""
        return self.counter.read_text(encoding="utf-8").count("call")

    def again(
        self,
        *,
        kit: KitUnderTest | None = None,
        cases: list[Case] | None = None,
        variants: list[Variant] | None = None,
        corpus_digest_override: str | None = None,
        rerun: bool = False,
        env_base: dict[str, str] | None = None,
        repetitions: int = 1,
    ) -> RunManifest:
        """Повторный `run_all` в тот же `out_dir` с точечными подменами."""
        return run_all(
            self.cases if cases is None else cases,
            self.variants if variants is None else variants,
            repetitions=repetitions,
            out_dir=self.out_dir,
            kit=self.kit if kit is None else kit,
            cache_root=self.cache_root,
            env_base=self.env_base if env_base is None else env_base,
            corpus_digest_override=corpus_digest_override,
            rerun=rerun,
        )


def _other_kit(kit: KitUnderTest) -> KitUnderTest:
    """Тот же кит с другим дайджестом `local.sh` — «промпт поправили»."""
    return dataclasses.replace(kit, digests={"local_sh_sha256": "f" * 64})


def _resume_fixture(tmp_path: Path) -> _Resume:
    """Один готовый прогон в `out_dir`, готовый к доливке."""
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    counter = tmp_path / "calls.txt"
    resume = _Resume(
        cases=[_make_case(base_sha=first, head_sha=second)],
        variants=[Variant("claude", "claude-opus-5", None)],
        kit=kit,
        cache_root=cache_root,
        env_base=_env_base(
            tmp_path / "record.txt",
            STUB_EXIT="0",
            STUB_VERDICT_BODY=VALID_VERDICT,
            STUB_COUNTER=str(counter),
        ),
        out_dir=tmp_path / "run",
        counter=counter,
    )
    run_all(
        resume.cases,
        resume.variants,
        repetitions=1,
        out_dir=resume.out_dir,
        kit=resume.kit,
        cache_root=resume.cache_root,
        env_base=resume.env_base,
    )
    assert resume.calls() == 1
    return resume


def test_run_all_refuses_resume_when_the_kit_changed(tmp_path: Path) -> None:
    """Доливка чужим китом запрещена: манифест описывал бы смесь двух китов.

    `run.json` называет кит по commit и дайджестам — один на прогон. Прежнее
    поведение перезаписывало манифест текущим китом и пропускало готовые
    тройки, так что половина результатов оставалась от прежнего кита, а
    провенанс — от нового.
    """
    resume = _resume_fixture(tmp_path)

    with pytest.raises(RunnerError, match="kit.local_sh_sha256") as excinfo:
        resume.again(kit=_other_kit(resume.kit))

    assert "--rerun" in str(excinfo.value)
    assert resume.calls() == 1, "кит не должен был запускаться"


def test_run_all_refuses_resume_when_the_kit_commit_changed(tmp_path: Path) -> None:
    """Commit чекаута — тоже часть провенанса кита."""
    resume = _resume_fixture(tmp_path)

    with pytest.raises(RunnerError, match="kit.commit"):
        resume.again(kit=dataclasses.replace(resume.kit, commit="9" * 40))


def test_run_all_refuses_resume_when_the_corpus_digest_changed(tmp_path: Path) -> None:
    """Корпус изменился — это другой прогон, а не продолжение прежнего."""
    resume = _resume_fixture(tmp_path)

    with pytest.raises(RunnerError, match="corpus_digest"):
        resume.again(corpus_digest_override="sha256:" + "0" * 64)


def test_run_all_refuses_resume_when_the_variants_changed(tmp_path: Path) -> None:
    """Набор вариантов записан в манифест: доливка другого — смесь под одной шапкой."""
    resume = _resume_fixture(tmp_path)

    with pytest.raises(RunnerError, match="variants"):
        resume.again(variants=[Variant("codex", "gpt-5.4", "high")])


def test_run_all_rerun_wipes_the_previous_results_and_resets_started(tmp_path: Path) -> None:
    """`--rerun` начинает прогон заново: результаты выбранных троек удаляются."""
    resume = _resume_fixture(tmp_path)
    rep_dir = resume.out_dir / "cases" / "steward-155" / "claude:claude-opus-5" / "1"
    (rep_dir / "stale.txt").write_text("остаток прошлого прогона", encoding="utf-8")
    before = json.loads((resume.out_dir / "run.json").read_text(encoding="utf-8"))["started"]

    manifest = resume.again(kit=_other_kit(resume.kit), rerun=True)

    assert resume.calls() == 2
    assert not (rep_dir / "stale.txt").exists()
    assert (rep_dir / "result.json").is_file()
    assert manifest.started >= before
    assert manifest.kit["local_sh_sha256"] == "f" * 64
    assert not (resume.out_dir / "scratch").exists()


def test_run_all_rerun_refuses_to_keep_results_of_another_kit(tmp_path: Path) -> None:
    """`--rerun` не смешивает: чужие результаты вне выборки — отказ, а не «перезапишем своё».

    `--rerun --cases <подмножество>` с другим китом оставил бы в каталоге
    результаты прежнего кита, и манифест снова описывал бы смесь. Удалять их
    молча нельзя — они оплачены, — поэтому отказ с подсказкой про новый `--out`.
    """
    resume = _resume_fixture(tmp_path)
    other_case = dataclasses.replace(resume.cases[0], case_id="steward-157")

    with pytest.raises(RunnerError, match="--out"):
        resume.again(kit=_other_kit(resume.kit), cases=[other_case], rerun=True)


def test_run_case_refuses_a_label_that_escapes_out_dir(tmp_path: Path) -> None:
    """Вторая линия обороны: запись обязана остаться внутри `--out`.

    `parse_variant` слеши и `..` больше не пропускает, но `Variant` собирают и
    в коде (CLI, тесты, будущие вызовы). Метка идёт в путь артефактов, поэтому
    перед любым созданием каталога проверяется, что он внутри `out_dir` —
    иначе отказ **до** записи, а не «почти правильный» путь на диске.
    """
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    # Метка собирается напрямую, минуя `parse_variant`: так делает код.
    # Каталог тройки — `<out>/cases/<case>/<label>/<rep>`, и приставка
    # `codex:` сама съедает один уровень, поэтому наружу выводит ровно такая
    # форма (её и называет находка ревью).
    escaping = Variant("codex", "x/../../../../outside", None)

    # Первой срабатывает проверка самой метки (`variant_label`); guard на путь
    # (`_inside_out_dir`) остаётся второй линией и проверяется отдельно.
    with pytest.raises(RunnerError, match="метка варианта|вне каталога прогона"):
        run_case(
            _make_case(base_sha=first, head_sha=second),
            escaping,
            1,
            kit=kit,
            out_dir=out_dir,
            cache_root=cache_root,
            env_base=_env_base(tmp_path / "record.txt", STUB_EXIT="0"),
        )

    assert not (tmp_path / "outside").exists()
    assert not (tmp_path / "cases").exists()


def test_run_all_rerun_refuses_before_deleting_anything(tmp_path: Path) -> None:
    """Отказ `--rerun` наступает **до** удаления: оплаченный результат остаётся.

    Прежде `_reset_results` сносил выбранные тройки, и только потом
    обнаруживалось, что в каталоге остались результаты другого кита. Отказ был
    честным, а результаты выбранных троек — уже удалёнными: команда,
    завершившаяся ошибкой, успевала уничтожить данные.
    """
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    case_a = _make_case(base_sha=first, head_sha=second, case_id="steward-155")
    case_b = _make_case(base_sha=first, head_sha=second, case_id="steward-157")
    variants = [Variant("claude", "claude-opus-5", None)]
    env_base = _env_base(tmp_path / "record.txt", STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT)
    both = corpus_digest([case_a, case_b])
    run_all(
        [case_a, case_b],
        variants,
        repetitions=1,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base=env_base,
    )
    result_a = out_dir / "cases" / "steward-155" / "claude:claude-opus-5" / "1" / "result.json"
    result_b = out_dir / "cases" / "steward-157" / "claude:claude-opus-5" / "1" / "result.json"
    before_a, before_b = result_a.read_bytes(), result_b.read_bytes()

    with pytest.raises(RunnerError, match="--out"):
        run_all(
            [case_a],
            variants,
            repetitions=1,
            out_dir=out_dir,
            kit=_other_kit(kit),
            cache_root=cache_root,
            env_base=env_base,
            corpus_digest_override=both,
            rerun=True,
        )

    assert result_a.read_bytes() == before_a, "выбранный результат удалён до отказа"
    assert result_b.read_bytes() == before_b


def test_run_all_refuses_resume_when_the_provider_env_changed(tmp_path: Path) -> None:
    """Набор provider-переменных — часть провенанса: доливка с другим запрещена.

    `run.json` называет **одно** окружение. Прежде повторение 2 с другим
    набором доливалось молча, а манифест перезаписывался новым набором: файл
    утверждал, что весь прогон шёл через один аккаунт, хотя половина шла через
    другой.
    """
    resume = _resume_fixture(tmp_path)
    run_json = resume.out_dir / "run.json"
    before = run_json.read_bytes()

    with pytest.raises(RunnerError, match="provider_env_names") as excinfo:
        resume.again(env_base={**resume.env_base, "ANTHROPIC_API_KEY": "secret"})

    message = str(excinfo.value)
    assert "ANTHROPIC_API_KEY" in message, message
    assert run_json.read_bytes() == before
    assert resume.calls() == 1, "кит не должен был запускаться"


def test_run_all_resume_with_the_same_provider_env_works(tmp_path: Path) -> None:
    """Тот же набор переменных — обычная доливка (значения не сравниваются)."""
    resume = _resume_fixture(tmp_path)

    resumed = resume.again(env_base={**resume.env_base, "STUB_EXIT": "0"})

    assert resumed.provider_env_names == []
    assert resume.calls() == 1, "готовая тройка перезапуска не требует"


def test_run_case_refuses_a_symlinked_scratch_path(tmp_path: Path) -> None:
    """Симлинк внутри `scratch/` уводит `rmtree` наружу — отказ до удаления.

    Путь scratch резолвился, но внутрь `--out` не проверялся, а
    `_clear_scratch` его `rmtree`-ит. Симлинка в каталоге прогона не бывает
    законной: раннер создаёт там только настоящие каталоги.
    """
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    external = tmp_path / "external"
    (external / "1").mkdir(parents=True)
    (external / "1" / "important.txt").write_text("не наше", encoding="utf-8")
    label = "claude:claude-opus-5"
    link = out_dir / "scratch" / "steward-155" / label
    link.parent.mkdir(parents=True)
    link.symlink_to(external, target_is_directory=True)

    with pytest.raises(RunnerError, match="scratch"):
        run_case(
            _make_case(base_sha=first, head_sha=second),
            Variant("claude", "claude-opus-5", None),
            1,
            kit=kit,
            out_dir=out_dir,
            cache_root=cache_root,
            env_base=_env_base(tmp_path / "record.txt", STUB_EXIT="0"),
        )

    assert (external / "1" / "important.txt").read_text(encoding="utf-8") == "не наше"


def test_run_all_refuses_results_without_a_manifest(tmp_path: Path) -> None:
    """Результаты есть, `run.json` нет — провенанс неизвестен, доливать нельзя.

    Прежде отсутствующий манифест читался как «каталог пуст»: раннер писал
    свой `run.json` и пропускал готовые тройки как свои. Результаты чужого
    прогона получали чужой провенанс — и выглядели измеренными этим китом.
    """
    resume = _resume_fixture(tmp_path)
    (resume.out_dir / "run.json").unlink()

    with pytest.raises(RunnerError, match="провенанс неизвестен"):
        resume.again()

    assert not (resume.out_dir / "run.json").exists(), "манифест не должен появиться"
    assert resume.calls() == 1


def test_run_all_refuses_results_with_a_corrupt_manifest(tmp_path: Path) -> None:
    """Нечитаемый `run.json` — то же самое: провенанс неизвестен."""
    resume = _resume_fixture(tmp_path)
    run_json = resume.out_dir / "run.json"
    run_json.write_text("{ это не json", encoding="utf-8")
    before = run_json.read_bytes()

    with pytest.raises(RunnerError, match="провенанс неизвестен"):
        resume.again()

    assert run_json.read_bytes() == before
    assert resume.calls() == 1


def test_run_all_rerun_recovers_a_directory_without_a_manifest(tmp_path: Path) -> None:
    """Выход один: `--rerun` по полному набору кейсов — он и переизмеряет всё."""
    resume = _resume_fixture(tmp_path)
    (resume.out_dir / "run.json").unlink()

    manifest = resume.again(rerun=True)

    assert manifest.run_id
    assert resume.calls() == 2


def test_run_case_records_empty_range_without_calling_the_reviewer(tmp_path: Path) -> None:
    """База и голова с одинаковым деревом — `empty_range`, а не механический сбой.

    Кит на пустом дифе выходит кодом 0 и sidecar не пишет, поэтому исход
    вычислялся как `mechanical_failure` — то есть негодный **кейс** выглядел
    сбоем инструмента. Проверка диапазона идёт до вызова ревьюера: платить за
    прогон, которому нечего ревьюировать, незачем.
    """
    repo, base, head = _make_empty_range_repo(tmp_path)
    assert base != head
    cache_root = _make_cache(tmp_path, repo, [base, head])
    kit = _make_stub_kit(tmp_path)
    record = tmp_path / "record.txt"
    out_dir = tmp_path / "run"

    result = run_case(
        _make_case(base_sha=base, head_sha=head),
        Variant("claude", "claude-opus-5", None),
        1,
        kit=kit,
        out_dir=out_dir,
        cache_root=cache_root,
        env_base=_env_base(record, STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT),
    )

    assert result.outcome == "empty_range"
    assert result.reviewer_ran is False
    assert result.verdict_path is None
    assert result.usage_path is None
    assert result.cost_status == "unavailable"
    assert result.unexpected is True
    assert result.wall_clock_s >= 0.0
    assert not record.exists(), "кит не должен был запускаться"
    stored = json.loads(
        (
            out_dir / "cases" / "steward-155" / "claude:claude-opus-5" / "1" / "result.json"
        ).read_text(encoding="utf-8")
    )
    assert stored["outcome"] == "empty_range"


def test_run_case_runs_the_reviewer_on_a_non_empty_range(tmp_path: Path) -> None:
    """Непустой диф — обычный прогон: проверка не глушит нормальные кейсы."""
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    record = tmp_path / "record.txt"

    result = run_case(
        _make_case(base_sha=first, head_sha=second),
        Variant("claude", "claude-opus-5", None),
        1,
        kit=_make_stub_kit(tmp_path),
        out_dir=tmp_path / "run",
        cache_root=cache_root,
        env_base=_env_base(record, STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT),
    )

    assert result.outcome == "verdict"
    assert record.exists()


def test_load_results_sorted_and_strict(tmp_path: Path) -> None:
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    cases = [
        _make_case(base_sha=first, head_sha=second, case_id="steward-157"),
        _make_case(base_sha=first, head_sha=second, case_id="steward-155"),
    ]

    run_all(
        cases,
        [Variant("codex", "gpt-5.4", "high"), Variant("claude", "claude-opus-5", None)],
        repetitions=2,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base=_env_base(tmp_path / "record.txt", STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT),
    )

    results = load_results(out_dir)
    assert [(r.case_id, r.variant, r.repetition_id) for r in results] == [
        ("steward-155", "claude:claude-opus-5", 1),
        ("steward-155", "claude:claude-opus-5", 2),
        ("steward-155", "codex:gpt-5.4:high", 1),
        ("steward-155", "codex:gpt-5.4:high", 2),
        ("steward-157", "claude:claude-opus-5", 1),
        ("steward-157", "claude:claude-opus-5", 2),
        ("steward-157", "codex:gpt-5.4:high", 1),
        ("steward-157", "codex:gpt-5.4:high", 2),
    ]
    assert all(r.teardown_error is None for r in results)
    assert {r.requested_effort for r in results} == {None, "high"}

    victim = out_dir / "cases" / "steward-155" / "claude:claude-opus-5" / "1" / "result.json"
    payload = json.loads(victim.read_text(encoding="utf-8"))
    del payload["outcome"]
    victim.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RunnerError) as excinfo:
        load_results(out_dir)
    assert "outcome" in str(excinfo.value)


def test_run_all_refuses_to_shrink_repetitions_on_resume(tmp_path: Path) -> None:
    """Уменьшить `repetitions` на возобновлении нельзя: результаты остались бы вне манифеста.

    Прежде `run.json` перезаписывался меньшим числом, а `result.json`
    повторений с большими номерами оставались на диске — и `load_results` их
    читал. Метрики считались по прогонам, которых манифест не объявлял.
    """
    resume = _resume_fixture(tmp_path)
    resume.again(repetitions=2)
    run_json = resume.out_dir / "run.json"
    before = run_json.read_bytes()

    with pytest.raises(RunnerError, match="уменьшение repetitions") as excinfo:
        resume.again(repetitions=1)

    message = str(excinfo.value)
    assert "2" in message and "1" in message, message
    assert run_json.read_bytes() == before


def test_run_all_allows_growing_repetitions_on_resume(tmp_path: Path) -> None:
    """Дописать повторения — законная доливка: манифест начинает объявлять больше."""
    resume = _resume_fixture(tmp_path)

    manifest = resume.again(repetitions=3)

    assert manifest.repetitions == 3
    assert resume.calls() == 3  # повторение 1 уже было


def test_load_results_refuses_a_result_outside_the_manifest(tmp_path: Path) -> None:
    """Результат повторения, которого манифест не объявляет, — отказ, а не «лишний».

    Каталог прогона может нести остаток от прогона с большим `repetitions`.
    Тихо включить его в метрики нельзя (числа посчитаны по необъявленному
    прогону), тихо выбросить — тоже: исход оплачен, и его исчезновение надо
    объяснять, а не скрывать.
    """
    resume = _resume_fixture(tmp_path)
    rep1 = resume.out_dir / "cases" / "steward-155" / "claude:claude-opus-5" / "1"
    stray = rep1.parent / "3"
    stray.mkdir()
    payload = json.loads((rep1 / "result.json").read_text(encoding="utf-8"))
    payload["repetition_id"] = 3
    (stray / "result.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RunnerError, match="манифест") as excinfo:
        load_results(resume.out_dir)

    assert "repetition" in str(excinfo.value) or "повторение" in str(excinfo.value)


def test_load_results_refuses_a_result_of_an_unknown_case(tmp_path: Path) -> None:
    """Кейс, которого манифест не называет, — результат необъявленного прогона.

    Прежде манифест перечислял варианты и число повторений, но не кейсы,
    поэтому результат чужого кейса читался как свой: метрики считались по
    прогону, которого `run.json` не объявлял.
    """
    resume = _resume_fixture(tmp_path)
    rep1 = resume.out_dir / "cases" / "steward-155" / "claude:claude-opus-5" / "1"
    stray = resume.out_dir / "cases" / "steward-999" / "claude:claude-opus-5" / "1"
    stray.mkdir(parents=True)
    payload = json.loads((rep1 / "result.json").read_text(encoding="utf-8"))
    payload["case_id"] = "steward-999"
    (stray / "result.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RunnerError, match="манифест") as excinfo:
        load_results(resume.out_dir)

    assert "steward-999" in str(excinfo.value)


def test_run_all_refuses_a_case_outside_the_manifest(tmp_path: Path) -> None:
    """Кейс, которого манифест не называет, доливать нельзя — даже сверх набора.

    Границей служит **список манифеста**: внутрь него можно (подмножество —
    законная выборка), наружу нельзя. Прогон по A и прогон по A+B — разные
    измерения, и один `run.json` их не описывает.
    """
    resume = _resume_fixture(tmp_path)
    extra = dataclasses.replace(resume.cases[0], case_id="steward-157")
    run_json = resume.out_dir / "run.json"
    before = run_json.read_bytes()

    with pytest.raises(RunnerError, match="кейсы вне манифеста") as excinfo:
        resume.again(
            cases=[*resume.cases, extra],
            corpus_digest_override=corpus_digest([*resume.cases, extra]),
        )

    assert "steward-157" in str(excinfo.value)
    assert run_json.read_bytes() == before
    assert resume.calls() == 1


def _two_case_run(
    tmp_path: Path, *, repetitions: int = 1
) -> tuple[list[Case], Path, Path, KitUnderTest, Path, str, dict[str, str]]:
    """Готовый прогон по двум кейсам; возвращает всё нужное для доливки."""
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    counter = tmp_path / "calls.txt"
    cases = [
        _make_case(base_sha=first, head_sha=second, case_id="steward-155"),
        _make_case(base_sha=first, head_sha=second, case_id="steward-157"),
    ]
    env_base = _env_base(
        tmp_path / "record.txt",
        STUB_EXIT="0",
        STUB_VERDICT_BODY=VALID_VERDICT,
        STUB_COUNTER=str(counter),
    )
    run_all(
        cases,
        [Variant("claude", "claude-opus-5", None)],
        repetitions=repetitions,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base=env_base,
    )
    return cases, out_dir, cache_root, kit, counter, corpus_digest(cases), env_base


def _calls(counter: Path) -> int:
    return counter.read_text(encoding="utf-8").count("call")


def test_run_all_rerun_of_a_subset_keeps_the_other_cases(tmp_path: Path) -> None:
    """`--rerun` подмножества переизмеряет только его; манифест не теряет кейсы.

    Прежде любой набор, отличный от записанного, объявлялся дрейфом
    провенанса, и переизмерить один кейс существующего прогона было нельзя —
    приходилось заводить новый `--out` и платить за весь корпус. Границей стало
    **вхождение** в список манифеста, а сам список не сжимается: он описывает
    прогон целиком, а не последнюю выборку.
    """
    cases, out_dir, cache_root, kit, counter, digest, env_base = _two_case_run(tmp_path)
    kept = out_dir / "cases" / "steward-157" / "claude:claude-opus-5" / "1" / "result.json"
    before = kept.read_bytes()
    assert _calls(counter) == 2

    manifest = run_all(
        [cases[0]],
        [Variant("claude", "claude-opus-5", None)],
        repetitions=1,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base=env_base,
        corpus_digest_override=digest,
        rerun=True,
    )

    assert _calls(counter) == 3, "переизмерен ровно один кейс"
    assert kept.read_bytes() == before, "результат другого кейса не тронут"
    assert manifest.cases == ["steward-155", "steward-157"]
    stored = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
    assert stored["cases"] == ["steward-155", "steward-157"]


def test_run_all_resume_of_a_subset_runs_only_its_missing_reps(tmp_path: Path) -> None:
    """Доливка подмножества добирает только его недостающие повторения.

    `repetitions` при этом **не меняется**: менять их разрешено только запросом
    по полному набору манифеста, иначе у кейсов вне выборки старших повторений
    не появится, а манифест объявит их все.
    """
    cases, out_dir, cache_root, kit, counter, digest, env_base = _two_case_run(
        tmp_path, repetitions=2
    )
    victim = out_dir / "cases" / "steward-157" / "claude:claude-opus-5" / "2"
    shutil.rmtree(victim)
    assert _calls(counter) == 4

    manifest = run_all(
        [cases[1]],
        [Variant("claude", "claude-opus-5", None)],
        repetitions=2,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base=env_base,
        corpus_digest_override=digest,
    )

    assert _calls(counter) == 5, "добран ровно один прогон"
    assert (victim / "result.json").is_file()
    assert manifest.cases == ["steward-155", "steward-157"]


def test_run_all_refuses_changing_repetitions_for_a_subset(tmp_path: Path) -> None:
    """Менять `repetitions` можно только запросом по полному набору манифеста.

    Доливка `[B]` с `repetitions: 2` после прогона по A+B×1 давала манифест,
    объявляющий A и B по два повторения, при отсутствующем A/2 — и метрики
    публиковали такой прогон как `ok`. Число повторений описывает **весь**
    прогон, поэтому менять его выборкой нельзя.
    """
    cases, out_dir, cache_root, kit, counter, digest, env_base = _two_case_run(tmp_path)
    run_json = out_dir / "run.json"
    before = run_json.read_bytes()

    with pytest.raises(RunnerError, match="repetitions") as excinfo:
        run_all(
            [cases[1]],
            [Variant("claude", "claude-opus-5", None)],
            repetitions=2,
            out_dir=out_dir,
            kit=kit,
            cache_root=cache_root,
            env_base=env_base,
            corpus_digest_override=digest,
        )

    assert "steward-155" in str(excinfo.value)
    assert run_json.read_bytes() == before
    assert _calls(counter) == 2


def test_run_all_rerun_also_refuses_to_shrink_repetitions(tmp_path: Path) -> None:
    """`--rerun` от запрета уменьшения не освобождает: rep 2 остался бы вне манифеста.

    Прежде проверка стояла под `if not rerun`, и `--rerun` с меньшим числом
    перезаписывал манифест, оставляя каталоги старших повторений на диске.
    Отказ наступает до любого удаления.
    """
    cases, out_dir, cache_root, kit, counter, digest, env_base = _two_case_run(
        tmp_path, repetitions=2
    )
    rep2 = out_dir / "cases" / "steward-155" / "claude:claude-opus-5" / "2" / "result.json"
    rep1 = out_dir / "cases" / "steward-155" / "claude:claude-opus-5" / "1" / "result.json"
    before = (rep1.read_bytes(), rep2.read_bytes())

    with pytest.raises(RunnerError, match="уменьшение repetitions"):
        run_all(
            cases,
            [Variant("claude", "claude-opus-5", None)],
            repetitions=1,
            out_dir=out_dir,
            kit=kit,
            cache_root=cache_root,
            env_base=env_base,
            corpus_digest_override=digest,
            rerun=True,
        )

    assert (rep1.read_bytes(), rep2.read_bytes()) == before
    assert _calls(counter) == 4


def test_load_results_refuses_an_incomplete_run(tmp_path: Path) -> None:
    """Неполный прогон не читается: метрики не должны видеть половину прогона.

    Манифест объявляет произведение «кейсы × варианты × повторения». Если
    какого-то `result.json` нет, прогон либо ещё идёт, либо оборвался — в обоих
    случаях считать по нему метрики нельзя, а `status: ok` по половине прогона
    выглядел бы измерением.
    """
    _cases, out_dir, _cache_root, _kit, _counter, _digest, _env = _two_case_run(
        tmp_path, repetitions=2
    )
    shutil.rmtree(out_dir / "cases" / "steward-155" / "claude:claude-opus-5" / "2")

    with pytest.raises(RunnerError, match="прогон неполон") as excinfo:
        load_results(out_dir)

    message = str(excinfo.value)
    assert "steward-155" in message and "claude:claude-opus-5" in message and "/2" in message


def test_load_results_reads_a_complete_run(tmp_path: Path) -> None:
    """Полный прогон читается целиком — проверка не мешает нормальному случаю."""
    _cases, out_dir, _cache_root, _kit, _counter, _digest, _env = _two_case_run(
        tmp_path, repetitions=2
    )

    assert len(load_results(out_dir)) == 4


def test_run_all_resume_with_the_same_case_set_works(tmp_path: Path) -> None:
    """Тот же набор — обычная доливка: готовые тройки не перезапускаются."""
    resume = _resume_fixture(tmp_path)

    resumed = resume.again()

    assert resumed.cases == ["steward-155"]
    assert resume.calls() == 1


def test_load_results_refuses_a_result_of_an_unknown_variant(tmp_path: Path) -> None:
    """Вариант, которого нет в манифесте, — тот же случай с другой стороны."""
    resume = _resume_fixture(tmp_path)
    rep1 = resume.out_dir / "cases" / "steward-155" / "claude:claude-opus-5" / "1"
    stray = rep1.parent.parent / "codex:gpt-5.4:high" / "1"
    stray.mkdir(parents=True)
    payload = json.loads((rep1 / "result.json").read_text(encoding="utf-8"))
    payload["variant"] = "codex:gpt-5.4:high"
    (stray / "result.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RunnerError, match="манифест"):
        load_results(resume.out_dir)


def test_run_case_refuses_a_symlinked_sidecar(tmp_path: Path) -> None:
    """Симлинк на месте `verdict.json` уводит удаление и запись за пределы прогона.

    Путь sidecar-а резолвился **до** удаления, поэтому `unlink` уносил цель
    ссылки, а кит потом писал вердикт туда же: прогон правил файл вне `--out`.
    Симлинка в каталоге тройки не бывает законной — раннер создаёт там только
    настоящие файлы.
    """
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    external = tmp_path / "external.json"
    external.write_text("не наше", encoding="utf-8")
    rep_dir = out_dir / "cases" / "steward-155" / "claude:claude-opus-5" / "1"
    rep_dir.mkdir(parents=True)
    (rep_dir / "verdict.json").symlink_to(external)
    record = tmp_path / "record.txt"

    with pytest.raises(RunnerError, match="символическая ссылка"):
        run_case(
            _make_case(base_sha=first, head_sha=second),
            Variant("claude", "claude-opus-5", None),
            1,
            kit=kit,
            out_dir=out_dir,
            cache_root=cache_root,
            env_base=_env_base(record, STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT),
        )

    assert external.read_text(encoding="utf-8") == "не наше"
    assert not record.exists(), "кит не должен был запускаться"


def test_run_all_refuses_partial_rerun_without_a_manifest(tmp_path: Path) -> None:
    """Без манифеста `--rerun` допустим только по всему каталогу.

    Провенанс результатов неизвестен, поэтому переизмерить часть и записать
    свой `run.json` значит выдать чужие результаты за свои. Прежде отказ стоял
    под `if not rerun`, и частичный `--rerun` проходил.
    """
    cases, out_dir, cache_root, kit, counter, digest, env_base = _two_case_run(tmp_path)
    (out_dir / "run.json").unlink()
    kept = out_dir / "cases" / "steward-157" / "claude:claude-opus-5" / "1" / "result.json"
    mine = out_dir / "cases" / "steward-155" / "claude:claude-opus-5" / "1" / "result.json"
    before = (kept.read_bytes(), mine.read_bytes())

    with pytest.raises(RunnerError, match="результаты без манифеста"):
        run_all(
            [cases[0]],
            [Variant("claude", "claude-opus-5", None)],
            repetitions=1,
            out_dir=out_dir,
            kit=kit,
            cache_root=cache_root,
            env_base=env_base,
            corpus_digest_override=digest,
            rerun=True,
        )

    assert (kept.read_bytes(), mine.read_bytes()) == before
    assert _calls(counter) == 2


def test_run_all_rerun_of_everything_recovers_a_dir_without_a_manifest(
    tmp_path: Path,
) -> None:
    """`--rerun` по всем кейсам и вариантам восстанавливает каталог без манифеста."""
    cases, out_dir, cache_root, kit, counter, digest, env_base = _two_case_run(tmp_path)
    (out_dir / "run.json").unlink()

    manifest = run_all(
        cases,
        [Variant("claude", "claude-opus-5", None)],
        repetitions=1,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base=env_base,
        corpus_digest_override=digest,
        rerun=True,
    )

    assert manifest.cases == ["steward-155", "steward-157"]
    assert _calls(counter) == 4


def test_load_results_refuses_a_path_that_disagrees_with_the_payload(
    tmp_path: Path,
) -> None:
    """Путь — тоже утверждение о прогоне, и оно обязано совпасть с содержимым.

    `load_results` верил только полям `result.json`, поэтому файл, положенный
    в каталог чужого кейса, читался по своему содержимому: тройка, которую
    объявляет дерево каталогов, и тройка внутри файла расходились молча.
    """
    _cases, out_dir, _cache_root, _kit, _counter, _digest, _env = _two_case_run(tmp_path)
    source = out_dir / "cases" / "steward-155" / "claude:claude-opus-5" / "1" / "result.json"
    alien = out_dir / "cases" / "steward-157" / "claude:claude-opus-5" / "1" / "result.json"
    alien.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(RunnerError, match="путь не совпадает") as excinfo:
        load_results(out_dir)

    assert "steward-157" in str(excinfo.value)


def test_load_results_refuses_a_duplicate_of_a_result(tmp_path: Path) -> None:
    """Копия результата под другим именем каталога — отказ, а не вторая тройка."""
    _cases, out_dir, _cache_root, _kit, _counter, _digest, _env = _two_case_run(tmp_path)
    source = out_dir / "cases" / "steward-155" / "claude:claude-opus-5" / "1" / "result.json"
    copy_dir = source.parent.parent / "copy"
    copy_dir.mkdir()
    (copy_dir / "result.json").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(RunnerError, match="путь не совпадает"):
        load_results(out_dir)


def test_run_all_refuses_resume_when_a_tool_version_changed(tmp_path: Path) -> None:
    """Версия **используемого** CLI — часть провенанса: доливка после обновления запрещена.

    `run.json` называет версии `claude`/`codex`/`git`, но сверяются только те
    клиенты, которыми пользуются варианты прогона: они и есть измеряемый
    ревьюер снаружи кита. Прежде не сверялся никто, и половина результатов
    оказывалась от прежней версии CLI под манифестом от новой.
    """
    resume = _resume_fixture(tmp_path)
    run_json = resume.out_dir / "run.json"
    before = _retool(resume.out_dir, "claude", "claude 0.0.1-ancient")

    with pytest.raises(RunnerError, match="tools.claude") as excinfo:
        resume.again()

    assert "0.0.1-ancient" in str(excinfo.value)
    assert run_json.read_bytes() == before
    assert resume.calls() == 1


def _retool(out_dir: Path, name: str, version: str) -> bytes:
    """Подменить в `run.json` записанную версию инструмента; вернуть новые байты."""
    run_json = out_dir / "run.json"
    payload = json.loads(run_json.read_text(encoding="utf-8"))
    payload["tools"][name] = version
    run_json.write_text(json.dumps(payload), encoding="utf-8")
    return run_json.read_bytes()


def test_run_all_refuses_resume_when_the_git_version_changed(tmp_path: Path) -> None:
    """Версия `git` — часть провенанса: она формирует **сам диф**, который читает модель.

    Прошлый раунд вывел `git` из сверки как «обстоятельство»; это была ошибка.
    `local.sh` выбирает алгоритм подготовки дифа по возможностям git
    (`check-attr --source`), то есть обновление git меняет вход ревьюера —
    ровно то, что провенанс обязан фиксировать. Неиспользуемые клиенты
    ревьюера из сверки по-прежнему исключены: они на вход не влияют.
    """
    resume = _resume_fixture(tmp_path)
    before = _retool(resume.out_dir, "git", "git version 0.0.1-ancient")

    with pytest.raises(RunnerError, match="tools.git") as excinfo:
        resume.again()

    assert "0.0.1-ancient" in str(excinfo.value)
    assert (resume.out_dir / "run.json").read_bytes() == before
    assert resume.calls() == 1


def test_run_all_resume_ignores_an_unused_client(tmp_path: Path) -> None:
    """Клиент, которым варианты прогона не пользуются, из сверки исключён.

    Прогон идёт вариантом `claude:*`, поэтому появление или обновление `codex`
    на машине к измеренному отношения не имеет.
    """
    resume = _resume_fixture(tmp_path)
    assert [v.harness for v in resume.variants] == ["claude"]
    _retool(resume.out_dir, "codex", "codex 9.9.9-installed-later")

    resumed = resume.again()

    assert resumed.run_id
    assert resume.calls() == 1


def test_run_all_refuses_resume_when_an_unavailable_used_client_appears(
    tmp_path: Path,
) -> None:
    """`unavailable` → настоящая версия у **используемого** клиента — тоже дрейф.

    Прогон, где ревьюер записан как недоступный, и прогон, где он есть, —
    разные измерения, даже если запись выглядит «просто уточнением».
    """
    resume = _resume_fixture(tmp_path)
    before = _retool(resume.out_dir, "claude", "unavailable")

    with pytest.raises(RunnerError, match="tools.claude") as excinfo:
        resume.again()

    assert "unavailable" in str(excinfo.value)
    assert (resume.out_dir / "run.json").read_bytes() == before


_STUB_CODEX = """#!/bin/sh
echo "codex 42.0.0-stub"
"""


def test_run_all_records_tool_versions_from_env_base(tmp_path: Path) -> None:
    """Версии CLI берутся из `env_base`, а не из окружения процесса.

    Раннер сам вычищает и собирает окружение прогона (§6.3) и в нём же ищет
    ревьюера. Резолвить версии по `PATH` процесса значило бы записать в
    `run.json` версию не того бинаря, который вызывался, — то есть провенанс,
    расходящийся с измерением.
    """
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    stub_codex = bin_dir / "codex"
    stub_codex.write_text(_STUB_CODEX, encoding="utf-8")
    stub_codex.chmod(0o755)
    env_base = _env_base(tmp_path / "record.txt", STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT)
    env_base["PATH"] = f"{bin_dir}:{env_base['PATH']}"

    manifest = run_all(
        [_make_case(base_sha=first, head_sha=second)],
        [Variant("codex", "gpt-5.4", "high")],
        repetitions=1,
        out_dir=tmp_path / "run",
        kit=kit,
        cache_root=cache_root,
        env_base=env_base,
    )

    assert manifest.tools["codex"] == "codex 42.0.0-stub"


def test_run_all_refuses_duplicate_variants(tmp_path: Path) -> None:
    """Два одинаковых варианта — одна тройка, оплаченная дважды, и гонка за каталог.

    Метка варианта — имя каталога артефактов, поэтому дубликат писал бы
    `verdict.json` и `result.json` по одному пути из двух задач (при
    `--jobs > 1` — одновременно), а счёт прогонов вырос бы вдвое без второго
    измерения.
    """
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    record = tmp_path / "record.txt"
    variant = Variant("claude", "claude-opus-5", None)

    with pytest.raises(RunnerError, match="claude:claude-opus-5"):
        run_all(
            [_make_case(base_sha=first, head_sha=second)],
            [variant, variant],
            repetitions=1,
            out_dir=tmp_path / "run",
            kit=_make_stub_kit(tmp_path),
            cache_root=cache_root,
            env_base=_env_base(record, STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT),
        )

    assert not record.exists(), "кит не должен был запускаться"


def test_run_all_refuses_a_symlinked_result_on_resume(tmp_path: Path) -> None:
    """Симлинк на месте `result.json` — не готовый результат и не повод пропустить тройку.

    `_result_exists` шёл по ссылке, поэтому подложенный симлинк выглядел
    готовым прогоном: тройка пропускалась, а в метрики попадал чужой файл.
    Пропустить нельзя, прогнать поверх ссылки тоже нельзя — отказ.
    """
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    out_dir = tmp_path / "run"
    external = tmp_path / "external-result.json"
    external.write_text("{}", encoding="utf-8")
    rep_dir = out_dir / "cases" / "steward-155" / "claude:claude-opus-5" / "1"
    rep_dir.mkdir(parents=True)
    (rep_dir / "result.json").symlink_to(external)
    record = tmp_path / "record.txt"

    with pytest.raises(RunnerError, match="символическая ссылка"):
        run_all(
            [_make_case(base_sha=first, head_sha=second)],
            [Variant("claude", "claude-opus-5", None)],
            repetitions=1,
            out_dir=out_dir,
            kit=_make_stub_kit(tmp_path),
            cache_root=cache_root,
            env_base=_env_base(record, STUB_EXIT="0", STUB_VERDICT_BODY=VALID_VERDICT),
        )

    assert external.read_text(encoding="utf-8") == "{}"


def test_load_results_refuses_a_symlinked_result(tmp_path: Path) -> None:
    """`load_results` по ссылке не читает: содержимое пришло бы извне прогона."""
    _cases, out_dir, _cache_root, _kit, _counter, _digest, _env = _two_case_run(tmp_path)
    victim = out_dir / "cases" / "steward-157" / "claude:claude-opus-5" / "1" / "result.json"
    external = out_dir.parent / "outside-result.json"
    external.write_text(victim.read_text(encoding="utf-8"), encoding="utf-8")
    victim.unlink()
    victim.symlink_to(external)

    with pytest.raises(RunnerError, match="символическая ссылка"):
        load_results(out_dir)


def test_load_results_refuses_an_unfinished_run(tmp_path: Path) -> None:
    """`finished: null` — прогон не завершён, даже если все `result.json` на месте.

    Манифест пишется дважды: в начале с `finished: null`, в конце целиком.
    Обрыв между последней тройкой и финальной записью оставлял полный набор
    результатов при незакрытом манифесте — и такой каталог читался как
    готовый прогон, хотя раннер до конца не дошёл (уборка scratch, финальные
    поля манифеста).
    """
    _cases, out_dir, _cache_root, _kit, _counter, _digest, _env = _two_case_run(tmp_path)
    run_json = out_dir / "run.json"
    payload = json.loads(run_json.read_text(encoding="utf-8"))
    payload["finished"] = None
    run_json.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RunnerError, match="прогон не завершён"):
        load_results(out_dir)


def test_load_results_refuses_a_manifest_without_finished(tmp_path: Path) -> None:
    """Отсутствующий ключ `finished` — то же самое, что `null`."""
    _cases, out_dir, _cache_root, _kit, _counter, _digest, _env = _two_case_run(tmp_path)
    run_json = out_dir / "run.json"
    payload = json.loads(run_json.read_text(encoding="utf-8"))
    del payload["finished"]
    run_json.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RunnerError, match="прогон не завершён"):
        load_results(out_dir)


def test_run_all_records_a_provider_env_fingerprint(tmp_path: Path) -> None:
    """`run.json` несёт отпечаток **значений** provider-переменных, кроме секретных."""
    resume = _resume_fixture(tmp_path)

    payload = json.loads((resume.out_dir / "run.json").read_text(encoding="utf-8"))
    fingerprint = payload["provider_env_fingerprint"]

    assert isinstance(fingerprint, str)
    assert len(fingerprint) == 64
    assert all(char in "0123456789abcdef" for char in fingerprint)


def _env_run(
    tmp_path: Path, **extra: str
) -> tuple[Case, Path, Path, KitUnderTest, Path, dict[str, str]]:
    """Готовый прогон в окружении с переданными переменными; всё нужное для доливки."""
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    counter = tmp_path / "calls.txt"
    env_base = _env_base(
        tmp_path / "record.txt",
        STUB_EXIT="0",
        STUB_VERDICT_BODY=VALID_VERDICT,
        STUB_COUNTER=str(counter),
        **extra,
    )
    case = _make_case(base_sha=first, head_sha=second)
    out_dir = tmp_path / "run"
    run_all(
        [case],
        [Variant("claude", "claude-opus-5", None)],
        repetitions=1,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base=env_base,
    )
    return case, out_dir, cache_root, kit, counter, env_base


def test_run_all_refuses_resume_when_a_proxy_value_changed(tmp_path: Path) -> None:
    """Смена **значения** прокси — дрейф: запрос ушёл бы другим маршрутом.

    Имена переменных при этом те же, поэтому `provider_env_names` такую смену
    не видел вовсе: половина прогона могла уйти через один прокси, половина
    через другой, а манифест утверждал бы одно окружение.
    """
    case, out_dir, cache_root, kit, counter, env_base = _env_run(
        tmp_path, HTTPS_PROXY="http://one.invalid"
    )
    run_json = out_dir / "run.json"
    before = run_json.read_bytes()

    with pytest.raises(RunnerError, match="provider_env_fingerprint"):
        run_all(
            [case],
            [Variant("claude", "claude-opus-5", None)],
            repetitions=1,
            out_dir=out_dir,
            kit=kit,
            cache_root=cache_root,
            env_base={**env_base, "HTTPS_PROXY": "http://two.invalid"},
        )

    assert run_json.read_bytes() == before
    assert _calls(counter) == 1


def test_run_all_resume_ignores_a_secret_value_change(tmp_path: Path) -> None:
    """Смена значения секретной переменной дрейфом не считается — принятый предел.

    Значение ключа в отпечаток не входит: манифест публикуется (копируется в
    `docs/evidence/`), а дайджест секрета — даже с солью в том же файле —
    вскрывается словарём. Поэтому смена аккаунта при том же имени переменной
    остаётся невидимой; ловит её только человек.
    """
    case, out_dir, cache_root, kit, counter, env_base = _env_run(
        tmp_path, ANTHROPIC_API_KEY="sk-one"
    )

    resumed = run_all(
        [case],
        [Variant("claude", "claude-opus-5", None)],
        repetitions=1,
        out_dir=out_dir,
        kit=kit,
        cache_root=cache_root,
        env_base={**env_base, "ANTHROPIC_API_KEY": "sk-two"},
    )

    assert resumed.provider_env_names == ["ANTHROPIC_API_KEY"]
    assert _calls(counter) == 1, "готовая тройка перезапуска не требует"


def test_run_all_refuses_resume_when_the_stored_fingerprint_is_missing(tmp_path: Path) -> None:
    """Манифест без `provider_env_fingerprint` — провенанс окружения неизвестен:
    доливка запрещена, иначе смена значения прокси прошла бы через старый
    манифест незамеченной.
    """
    case, out_dir, cache_root, kit, counter, env_base = _env_run(
        tmp_path, HTTPS_PROXY="http://one.invalid"
    )
    run_json = out_dir / "run.json"
    manifest = json.loads(run_json.read_text(encoding="utf-8"))
    del manifest["provider_env_fingerprint"]
    run_json.write_text(json.dumps(manifest), encoding="utf-8")
    before = run_json.read_bytes()

    with pytest.raises(RunnerError, match="provider_env_fingerprint"):
        run_all(
            [case],
            [Variant("claude", "claude-opus-5", None)],
            repetitions=2,
            out_dir=out_dir,
            kit=kit,
            cache_root=cache_root,
            env_base=env_base,
        )

    assert run_json.read_bytes() == before


def test_provider_env_fingerprint_ignores_secret_values_only() -> None:
    """Отпечаток меняется от несекретного значения и не меняется от секретного."""
    base = {"HTTPS_PROXY": "http://one.invalid", "ANTHROPIC_API_KEY": "sk-one"}

    same_secret_other_value = {**base, "ANTHROPIC_API_KEY": "sk-two"}
    other_proxy = {**base, "HTTPS_PROXY": "http://two.invalid"}

    assert provider_env_fingerprint(base) == provider_env_fingerprint(same_secret_other_value)
    assert provider_env_fingerprint(base) != provider_env_fingerprint(other_proxy)


_SLOW_LOCAL_SH = """#!/bin/sh
sleep 0.2
printf '%s' "$STUB_VERDICT_BODY" > "$REVIEW_VERDICT_OUT"
exit 0
"""


def _make_kit_tree(tmp_path: Path) -> Path:
    """Дерево чекаута steward со всеми пинуемыми файлами кита (все исполняемые)."""
    root = tmp_path / "fake-steward"
    kit_dir = root / "scripts" / "review"
    kit_dir.mkdir(parents=True)
    codex = root / ".github" / "codex"
    codex.mkdir(parents=True)
    (codex / "review-prompt.md").write_text("prompt\n", encoding="utf-8")
    (codex / "review-schema.json").write_text("{}\n", encoding="utf-8")
    for name in (
        "apply-threshold.sh",
        "local.sh",
        "collect-context.sh",
        "harness-claude",
        "build-prompt.sh",
    ):
        path = kit_dir / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
    return root


def test_run_all_scrubs_git_env_for_its_own_git_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`GIT_DIR` из окружения процесса не должен уводить **наши** git-вызовы.

    Вычищалось только окружение кита, а `has_object`/`worktree` и проверка
    диапазона наследовали `GIT_*` процесса: `GIT_DIR` увёл бы их в чужое репо,
    и раннер объявил бы объекты отсутствующими в кэше — прогон падал бы на
    машине, где переменная просто выставлена.
    """
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "nowhere.git"))
    monkeypatch.setenv("STUB_EXIT", "0")
    monkeypatch.setenv("STUB_VERDICT_BODY", VALID_VERDICT)
    monkeypatch.setenv("STUB_RECORD", str(tmp_path / "record.txt"))

    manifest = run_all(
        [_make_case(base_sha=first, head_sha=second)],
        [Variant("claude", "claude-opus-5", None)],
        repetitions=1,
        out_dir=tmp_path / "run",
        kit=_make_stub_kit(tmp_path),
        cache_root=cache_root,
        env_base=None,
    )

    assert manifest.finished
    results = load_results(tmp_path / "run")
    assert [item.outcome for item in results] == ["verdict"]


def test_load_results_refuses_results_without_a_manifest(tmp_path: Path) -> None:
    """Результаты есть, `run.json` нет — читать нечего: провенанс неизвестен.

    Прежде `load_results` просто возвращал такие результаты, и метрики
    считались по прогону, о котором артефакты не говорят ни кита, ни корпуса,
    ни варианта.
    """
    _cases, out_dir, _cache_root, _kit, _counter, _digest, _env = _two_case_run(tmp_path)
    (out_dir / "run.json").unlink()

    with pytest.raises(RunnerError, match="результаты без манифеста"):
        load_results(out_dir)


def test_run_case_measures_wall_clock_around_the_kit_only(tmp_path: Path) -> None:
    """`wall_clock_s` — только вызов кита; предпроверка диапазона отдельно.

    Проверка «диапазон пуст» (два вызова git) шла внутрь измерения, то есть в
    метрику длительности ревьюера попадало время раннера. Теперь таймер
    открывается прямо перед `local.sh` и закрывается сразу после, а
    предпроверка записана в `precheck_s`.
    """
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    (kit.kit_dir / "local.sh").write_text(_SLOW_LOCAL_SH, encoding="utf-8")
    (kit.kit_dir / "local.sh").chmod(0o755)

    result = run_case(
        _make_case(base_sha=first, head_sha=second),
        Variant("claude", "claude-opus-5", None),
        1,
        kit=kit,
        cache_root=cache_root,
        out_dir=tmp_path / "run",
        env_base=_env_base(tmp_path / "record.txt", STUB_VERDICT_BODY=VALID_VERDICT),
    )

    assert result.wall_clock_s >= 0.2
    assert result.precheck_s > 0.0
    assert result.precheck_s < result.wall_clock_s


def test_run_case_empty_range_keeps_wall_clock_at_zero(tmp_path: Path) -> None:
    """У `empty_range` ревьюер не вызывался, поэтому его время — ноль, не время git."""
    repo, base, head = _make_empty_range_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [base, head])

    result = run_case(
        _make_case(base_sha=base, head_sha=head),
        Variant("claude", "claude-opus-5", None),
        1,
        kit=_make_stub_kit(tmp_path),
        cache_root=cache_root,
        out_dir=tmp_path / "run",
        env_base=_env_base(tmp_path / "record.txt", STUB_EXIT="0"),
    )

    assert result.outcome == "empty_range"
    assert result.wall_clock_s == 0.0
    assert result.precheck_s > 0.0


def test_kit_under_test_records_executable_bits() -> None:
    """Права на исполнение — такой же факт про кит, как дайджест."""
    root = Path(__file__).resolve().parents[2]

    kit = kit_under_test(root)

    assert kit.executables["harness_claude_executable"] is True
    assert kit.executables["prompt_executable"] is False


def test_kit_under_test_refuses_a_non_executable_harness_for_claude(tmp_path: Path) -> None:
    """`chmod -x harness-claude` при запрошенном варианте claude — кит негоден.

    Прежде это было невидимо: дайджест файла не менялся, прогон шёл и падал
    `config_failure` под тем же манифестом — сбой конфигурации выглядел
    свойством варианта.
    """
    root = _make_kit_tree(tmp_path)
    (root / "scripts" / "review" / "harness-claude").chmod(0o644)

    with pytest.raises(RunnerError, match="harness-claude не исполняем"):
        kit_under_test(root, harnesses=["claude"])

    # Без варианта claude адаптер не нужен — кит годен.
    assert kit_under_test(root, harnesses=["codex"]).commit


def test_run_all_refuses_a_non_executable_harness_before_running(tmp_path: Path) -> None:
    """Тот же отказ на стороне прогона: он знает и кит, и варианты."""
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    root = _make_kit_tree(tmp_path)
    (root / "scripts" / "review" / "harness-claude").chmod(0o644)
    kit = kit_under_test(root)
    record = tmp_path / "record.txt"

    with pytest.raises(RunnerError, match="harness-claude не исполняем"):
        run_all(
            [_make_case(base_sha=first, head_sha=second)],
            [Variant("claude", "claude-opus-5", None)],
            repetitions=1,
            out_dir=tmp_path / "run",
            kit=kit,
            cache_root=cache_root,
            env_base=_env_base(record, STUB_EXIT="0"),
        )

    assert not record.exists()
    assert not (tmp_path / "run" / "cases").exists()


def test_run_all_refuses_resume_when_an_executable_bit_changed(tmp_path: Path) -> None:
    """Снятый бит исполнения — дрейф провенанса наравне с дайджестом."""
    resume = _resume_fixture(tmp_path)
    run_json = resume.out_dir / "run.json"
    payload = json.loads(run_json.read_text(encoding="utf-8"))
    payload["kit"]["local_sh_executable"] = False
    run_json.write_text(json.dumps(payload), encoding="utf-8")
    before = run_json.read_bytes()

    with pytest.raises(RunnerError, match="kit.local_sh_executable"):
        resume.again()

    assert run_json.read_bytes() == before


def test_load_results_empty_run_dir(tmp_path: Path) -> None:
    assert load_results(tmp_path / "empty") == []


def test_run_all_pre_checks_every_case_before_first_run(tmp_path: Path) -> None:
    """Непокрытый кейс в середине очереди обрывал бы прогон после оплаченных."""
    repo, first, second = _make_fixture_repo(tmp_path)
    cache_root = _make_cache(tmp_path, repo, [first, second])
    kit = _make_stub_kit(tmp_path)
    out_dir = tmp_path / "run"
    counter = tmp_path / "calls.txt"
    cases = [
        _make_case(base_sha=first, head_sha=second, case_id="steward-155"),
        _make_case(base_sha=first, head_sha=MISSING_SHA, case_id="steward-157"),
    ]

    with pytest.raises(RunnerError) as excinfo:
        run_all(
            cases,
            [Variant("claude", "claude-opus-5", None)],
            repetitions=1,
            out_dir=out_dir,
            kit=kit,
            cache_root=cache_root,
            env_base=_env_base(
                tmp_path / "record.txt",
                STUB_EXIT="0",
                STUB_VERDICT_BODY=VALID_VERDICT,
                STUB_COUNTER=str(counter),
            ),
        )

    message = str(excinfo.value)
    assert MISSING_SHA in message
    assert "steward-157" in message
    assert not counter.exists()  # ни одного вызова кита
    assert not (out_dir / "run.json").exists()
