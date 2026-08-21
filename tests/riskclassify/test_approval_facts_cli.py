"""CLI: ни один шаг preflight не должен разрушать прежнюю публикацию.

Порядок в команде (§8.4 спеки approval-facts/v2, задача 7):
1. разбор и валидация ``--repo``;
2. цель — bundle-target (git-root + сверка origin) либо явный ``--out``
   (сверка origin пропущена, но путь резолвится и валидируется);
3. валидация объявленного scope (непуст, без дублей, форма);
4. загрузка политики и вычисление ``policy_digest``;
5. валидация ``approval_facts_lease_seconds`` (внутри ``load_approval_policy``);
6. только теперь ``remove_previous`` -> ``materialize`` -> classify -> publish.

Каждый тест ниже привязан к КОНКРЕТНОМУ шагу: он ломает preflight именно на
этом шаге (и ни на каком другом) и проверяет, что прежняя публикация
осталась байт-в-байт прежней.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from steward.approvalfacts import producer
from steward.riskclassify.cli import app

runner = CliRunner()

SHA = "221457933968be9e95acd51d548e080f739c794c"
GOOD_POLICY = (
    "version: 1\n"
    "human_identities:\n"
    "  - github:andrei-shtanakov\n"
    "agent_identities:\n"
    "  - github:merge-broker\n"
    "approval_facts_lease_seconds: 86400\n"
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


def _git_repo(tmp_path: Path, *, origin: str | None) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    if origin is not None:
        _git(root, "remote", "add", "origin", origin)
    return root


def _hermetic_root(tmp_path: Path) -> Path:
    """`--repo-root` без `profiles/` — гарантированно НЕ настоящий репозиторий.

    Round 1 review: тесты, не передававшие `--repo-root`, получали дефолт
    `.`, который во время прогона pytest резолвится в РЕАЛЬНЫЙ, валидный
    `profiles/approval-policy.yaml` ЭТОГО репозитория. Из-за этого шаг 4
    (загрузка политики) никогда не падал в этих тестах — не потому что
    порядок preflight соблюдён, а потому что дефолтный путь случайно вёл к
    годному файлу. Перестановка шагов 3 и 4 оставалась незамеченной.

    Каждый вызов CLI в этом файле обязан передавать `--repo-root` явно и
    внутрь `tmp_path` — тест обязан вести себя одинаково независимо от
    того, из какого каталога запущен pytest.
    """
    root = tmp_path / "hermetic-root"
    root.mkdir(exist_ok=True)
    return root


def _fake_gh(responses: list[tuple[int, object]]):
    it: Iterator[tuple[int, object]] = iter(responses)

    def fake(args: list[str]) -> tuple[int, str]:
        code, payload = next(it)
        return code, payload if isinstance(payload, str) else json.dumps(payload)

    return fake


# --- Step 1: --repo shape ---------------------------------------------------


def test_bad_repo_is_config_error_and_keeps_previous(tmp_path: Path) -> None:
    previous = tmp_path / "facts.jsonl"
    previous.write_text("prior", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "approval-facts",
            "--repo",
            "bad",
            "--repo-root",
            str(_hermetic_root(tmp_path)),
            "--prs",
            "1",
            "--out",
            str(previous),
        ],
    )
    assert result.exit_code == 2
    assert previous.read_text(encoding="utf-8") == "prior", "прежняя публикация уничтожена"
    assert "'owner/name'" in result.output


def test_step1_repo_shape_checked_before_step2_target_resolution(tmp_path: Path) -> None:
    """Шаги 1 и 2 невалидны ОДНОВРЕМЕННО: сообщение обязано быть от более
    раннего шага (1 — форма `--repo`), а не от шага 2 (`--repo-root` не
    git-репозиторий). Это и есть проверка ОТНОСИТЕЛЬНОГО порядка: каждый
    шаг по отдельности уже падает (см. `test_bad_repo_...` и
    `test_bundle_target_not_a_git_repo_...`), но по отдельности они не
    доказывают, в каком порядке preflight их проверяет.
    """
    not_a_repo = tmp_path / "not-a-repo"  # шаг 2 тоже невалиден: не git

    result = runner.invoke(
        app,
        [
            "approval-facts",
            "--repo",
            "bad",  # шаг 1 тоже невалиден: нет '/'
            "--repo-root",
            str(not_a_repo),
            "--prs",
            "1",
        ],
    )
    assert result.exit_code == 2
    assert "'owner/name'" in result.output, (
        "сообщение должно быть о форме --repo (шаг 1); если оно про "
        "git-репозиторий — шаг 2 сработал первым, порядок нарушен"
    )
    assert "git-репозитори" not in result.output


# --- Step 2: bundle target (git-root + origin) / --out override ------------


def test_bundle_target_not_a_git_repo_is_config_error_and_keeps_previous(tmp_path: Path) -> None:
    """`--repo-root` вне git-репозитория — ошибка на шаге 2, до удаления.

    `--out` не задан, поэтому целью служит путь бандла под `--repo-root`;
    файл кладём туда заранее, ровно на месте будущего бандла, чтобы доказать
    что `remove_previous` по этому пути не вызывался.
    """
    not_a_repo = tmp_path / "not-a-repo"
    bundle_dir = not_a_repo / ".steward"
    bundle_dir.mkdir(parents=True)
    previous = bundle_dir / "approval_facts.jsonl"
    previous.write_text("prior", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "approval-facts",
            "--repo",
            "o/n",
            "--repo-root",
            str(not_a_repo),
            "--prs",
            "1",
        ],
    )
    assert result.exit_code == 2
    assert previous.read_text(encoding="utf-8") == "prior"
    assert "git-репозитори" in result.output


def test_bundle_target_origin_mismatch_is_config_error_and_keeps_previous(tmp_path: Path) -> None:
    repo_root = _git_repo(tmp_path, origin="git@github.com:andrei-shtanakov/steward-real.git")
    bundle_dir = repo_root / ".steward"
    bundle_dir.mkdir()
    previous = bundle_dir / "approval_facts.jsonl"
    previous.write_text("prior", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "approval-facts",
            "--repo",
            "andrei-shtanakov/wrong-repo",
            "--repo-root",
            str(repo_root),
            "--prs",
            "1",
        ],
    )
    assert result.exit_code == 2
    assert previous.read_text(encoding="utf-8") == "prior"
    assert "origin" in result.output


def test_out_parent_missing_is_config_error_and_keeps_previous(tmp_path: Path) -> None:
    """`--out` в несуществующий каталог — ошибка на шаге 2 до удаления.

    Родительский каталог не существует, значит прежней публикации по этому
    пути в принципе быть не может — проверяем, что она и не появляется
    (файл отсутствует), плюс точное сообщение об ошибке (не про scope, не
    про политику — про сам путь), чтобы отличить провал шага 2 от провала
    шага 3/4 с тем же exit-кодом.
    """
    missing_parent = tmp_path / "nonexistent-dir" / "facts.jsonl"
    result = runner.invoke(
        app,
        [
            "approval-facts",
            "--repo",
            "o/n",
            "--repo-root",
            str(_hermetic_root(tmp_path)),
            "--prs",
            "1",
            "--out",
            str(missing_parent),
        ],
    )
    assert result.exit_code == 2
    assert not missing_parent.exists()
    assert "родительский каталог" in result.output
    assert "не существует" in result.output


def test_out_parent_not_writable_is_config_error_and_keeps_previous(tmp_path: Path) -> None:
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    previous = readonly_dir / "facts.jsonl"
    previous.write_text("prior", encoding="utf-8")
    readonly_dir.chmod(0o555)
    try:
        result = runner.invoke(
            app,
            [
                "approval-facts",
                "--repo",
                "o/n",
                "--repo-root",
                str(_hermetic_root(tmp_path)),
                "--prs",
                "1",
                "--out",
                str(previous),
            ],
        )
    finally:
        readonly_dir.chmod(0o755)
    assert result.exit_code == 2
    assert previous.read_text(encoding="utf-8") == "prior"
    assert "недоступен для записи" in result.output


# --- Step 3: declared scope --------------------------------------------------
#
# `test_duplicate_scope_is_config_error` and `test_no_identifiers_is_config_error`
# below are given a VALID `--policy` (unlike the other two step-3 tests). Round-2
# re-review finding: with `_hermetic_root` and no `--policy`, step 4 fails on its
# own (missing policy file) whenever step 3's guard doesn't stop execution first —
# same exit code 2, and for the duplicate case even a message that spuriously
# contains "duplicate" (pytest's own `tmp_path` embeds this test's function name).
# So deleting the duplicate/empty-scope guard left both tests green for the wrong
# reason: masking by a coincidentally-identical downstream failure, not because the
# guard fired. A valid policy removes that coincidence — if the guard is ever
# deleted, execution now runs all the way through to `publish` and exits **0**,
# an unambiguous, unspoofable signal no policy-file wording can accidentally match.
# `producer._gh` is monkeypatched defensively so that scenario, reached only under
# a deliberate guard-deletion mutation, never touches the network either.
#
# The other two step-3 tests below (`test_malformed_merge_sha_...`,
# `test_non_positive_pr_...`) keep `_hermetic_root` with no `--policy`: verified by
# mutation (see the round-2 report) that their guard-specific message assertions
# ("hex" / "положительным") do not accidentally match the step-4 fallback message
# or a `tmp_path` name, so they are not subject to the same masking.


def test_duplicate_scope_is_config_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    previous = tmp_path / "f.jsonl"
    previous.write_text("prior", encoding="utf-8")
    policy = tmp_path / "approval-policy.yaml"
    policy.write_text(GOOD_POLICY, encoding="utf-8")
    # Дублирующийся scope значит materialize(), если бы guard не остановил
    # выполнение, разрешал бы pr:1 дважды — по одному ответу на каждый вызов.
    monkeypatch.setattr(
        producer,
        "_gh",
        _fake_gh(
            [
                (0, {"data": {"repository": {"pullRequest": {"mergeCommit": None}}}}),
                (0, {"data": {"repository": {"pullRequest": {"mergeCommit": None}}}}),
            ]
        ),
    )
    result = runner.invoke(
        app,
        [
            "approval-facts",
            "--repo",
            "o/n",
            "--repo-root",
            str(_hermetic_root(tmp_path)),
            "--policy",
            str(policy),
            "--prs",
            "1,1",
            "--out",
            str(previous),
        ],
    )
    assert result.exit_code == 2, (
        "с валидной политикой exit 2 может значить только то, что guard шага 3 "
        "сработал — реальной коллизии с провалом шага 4 больше нет"
    )
    assert "дубл" in result.output
    assert previous.read_text(encoding="utf-8") == "prior"


def test_no_identifiers_is_config_error(tmp_path: Path) -> None:
    previous = tmp_path / "f.jsonl"
    previous.write_text("prior", encoding="utf-8")
    policy = tmp_path / "approval-policy.yaml"
    policy.write_text(GOOD_POLICY, encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "approval-facts",
            "--repo",
            "o/n",
            "--repo-root",
            str(_hermetic_root(tmp_path)),
            "--policy",
            str(policy),
            "--out",
            str(previous),
        ],
    )
    assert result.exit_code == 2, (
        "с валидной политикой exit 2 может значить только то, что guard шага 3 "
        "сработал — реальной коллизии с провалом шага 4 больше нет"
    )
    assert "идентификатор" in result.output
    assert previous.read_text(encoding="utf-8") == "prior"


def test_malformed_merge_sha_is_config_error_and_keeps_previous(tmp_path: Path) -> None:
    previous = tmp_path / "f.jsonl"
    previous.write_text("prior", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "approval-facts",
            "--repo",
            "o/n",
            "--repo-root",
            str(_hermetic_root(tmp_path)),
            "--merge-sha",
            "not-a-sha",
            "--out",
            str(previous),
        ],
    )
    assert result.exit_code == 2
    assert previous.read_text(encoding="utf-8") == "prior"
    assert "hex" in result.output


def test_non_positive_pr_is_config_error_and_keeps_previous(tmp_path: Path) -> None:
    previous = tmp_path / "f.jsonl"
    previous.write_text("prior", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "approval-facts",
            "--repo",
            "o/n",
            "--repo-root",
            str(_hermetic_root(tmp_path)),
            "--prs",
            "0",
            "--out",
            str(previous),
        ],
    )
    assert result.exit_code == 2
    assert previous.read_text(encoding="utf-8") == "prior"
    assert "положительным" in result.output


def test_step3_scope_checked_before_step4_policy_load(tmp_path: Path) -> None:
    """Шаги 3 и 4 невалидны ОДНОВРЕМЕННО: сообщение обязано быть про scope
    (шаг 3), а не про политику (шаг 4). `_hermetic_root` не содержит
    `profiles/approval-policy.yaml`, так что шаг 4 тоже упал бы, если бы
    выполнился, — если сообщение вдруг окажется про политику, значит
    порядок нарушен и шаг 4 сработал первым.
    """
    result = runner.invoke(
        app,
        [
            "approval-facts",
            "--repo",
            "o/n",
            "--repo-root",
            str(_hermetic_root(tmp_path)),
            "--prs",
            "1,1",  # шаг 3 тоже невалиден: дубли
            "--out",
            str(tmp_path / "f.jsonl"),
        ],
    )
    assert result.exit_code == 2
    assert "дубл" in result.output, (
        "сообщение должно быть про scope (шаг 3); если оно про политику — "
        "шаг 4 сработал первым, порядок нарушен"
    )


# --- Step 4/5: policy load + lease ------------------------------------------


def test_bad_lease_config_is_caught_before_delete(tmp_path: Path) -> None:
    """Битая lease-конфигурация обязана упасть ДО удаления."""
    previous = tmp_path / "facts.jsonl"
    previous.write_text("prior", encoding="utf-8")
    policy = tmp_path / "approval-policy.yaml"
    policy.write_text(
        "version: 1\nhuman_identities: []\nagent_identities: []\n"
        "approval_facts_lease_seconds: 864000000\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "approval-facts",
            "--repo",
            "o/n",
            "--repo-root",
            str(_hermetic_root(tmp_path)),
            "--prs",
            "1",
            "--out",
            str(previous),
            "--policy",
            str(policy),
        ],
    )
    assert result.exit_code == 2
    assert previous.read_text(encoding="utf-8") == "prior"


def test_missing_policy_file_is_config_error_and_keeps_previous(tmp_path: Path) -> None:
    repo_root = _git_repo(tmp_path, origin="git@github.com:andrei-shtanakov/steward-real.git")
    bundle_dir = repo_root / ".steward"
    bundle_dir.mkdir()
    previous = bundle_dir / "approval_facts.jsonl"
    previous.write_text("prior", encoding="utf-8")
    # никакого profiles/approval-policy.yaml под repo_root не создаём и
    # --policy не задаём — дефолтный путь обязан не найтись.

    result = runner.invoke(
        app,
        [
            "approval-facts",
            "--repo",
            "andrei-shtanakov/steward-real",
            "--repo-root",
            str(repo_root),
            "--prs",
            "1",
        ],
    )
    assert result.exit_code == 2
    assert previous.read_text(encoding="utf-8") == "prior"


def test_policy_defaults_to_repo_root_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--policy` не задан: политика читается из `<repo-root>/profiles/...`."""
    repo_root = _git_repo(tmp_path, origin="git@github.com:andrei-shtanakov/steward-real.git")
    (repo_root / "profiles").mkdir()
    (repo_root / "profiles" / "approval-policy.yaml").write_text(GOOD_POLICY, encoding="utf-8")

    monkeypatch.setattr(
        producer,
        "_gh",
        _fake_gh(
            [
                (
                    0,
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "mergeCommit": {"oid": SHA},
                                    "mergedBy": {
                                        "login": "andrei-shtanakov",
                                        "__typename": "User",
                                    },
                                }
                            }
                        }
                    },
                )
            ]
        ),
    )
    result = runner.invoke(
        app,
        [
            "approval-facts",
            "--repo",
            "andrei-shtanakov/steward-real",
            "--repo-root",
            str(repo_root),
            "--prs",
            "7",
        ],
    )

    assert result.exit_code == 0, result.output
    published = repo_root / ".steward" / "approval_facts.jsonl"
    assert published.exists()
    lines = published.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    assert header["kind"] == "header"
    record = json.loads(lines[1])
    assert record["state"] == "merged"
    assert record["actor_class"] == "human"


# --- Step 6: destructive phase, ordering after full preflight succeeds -----


def test_successful_run_removes_previous_and_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = tmp_path / "approval-policy.yaml"
    policy.write_text(GOOD_POLICY, encoding="utf-8")
    out = tmp_path / "facts.jsonl"
    out.write_text("stale-prior", encoding="utf-8")

    monkeypatch.setattr(
        producer,
        "_gh",
        _fake_gh(
            [
                (
                    0,
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "mergeCommit": {"oid": SHA},
                                    "mergedBy": {
                                        "login": "merge-broker",
                                        "__typename": "Bot",
                                    },
                                }
                            }
                        }
                    },
                )
            ]
        ),
    )

    result = runner.invoke(
        app,
        [
            "approval-facts",
            "--repo",
            "andrei-shtanakov/steward",
            "--repo-root",
            str(_hermetic_root(tmp_path)),
            "--prs",
            "42",
            "--out",
            str(out),
            "--policy",
            str(policy),
        ],
    )
    assert result.exit_code == 0, result.output
    lines = out.read_text(encoding="utf-8").splitlines()
    assert "stale-prior" not in out.read_text(encoding="utf-8")
    header = json.loads(lines[0])
    assert header["repository"] == "andrei-shtanakov/steward"
    assert header["policy_version"] == 1
    record = json.loads(lines[1])
    assert record == {
        "kind": "result",
        "request": {"kind": "pr", "value": 42},
        "state": "merged",
        "merge_sha": SHA,
        "identity": "github:merge-broker",
        "type_hint": "Bot",
        "actor_class": "agent",
    }


def test_mechanical_materialize_failure_is_exit_3_and_previous_already_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Механический сбой после удаления — прежняя публикация НЕ восстанавливается.

    Это осознанное поведение (ruling 4 задачи): неудачный refresh оставляет
    источник отсутствующим, а не тихо стухшим под видом свежего.
    """
    policy = tmp_path / "approval-policy.yaml"
    policy.write_text(GOOD_POLICY, encoding="utf-8")
    out = tmp_path / "facts.jsonl"
    out.write_text("stale-prior", encoding="utf-8")

    monkeypatch.setattr(producer, "_gh", _fake_gh([(1, "gh: not authenticated")]))

    result = runner.invoke(
        app,
        [
            "approval-facts",
            "--repo",
            "andrei-shtanakov/steward",
            "--repo-root",
            str(_hermetic_root(tmp_path)),
            "--prs",
            "42",
            "--out",
            str(out),
            "--policy",
            str(policy),
        ],
    )
    assert result.exit_code == 3
    assert not out.exists(), "прежняя публикация обязана быть уже снята к моменту сбоя"
