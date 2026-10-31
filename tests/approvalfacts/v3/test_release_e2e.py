"""Связка «выпуск → чтение → `gate-check --stage release`» на v3 (спека v3 §6, Task 11).

Переключение атомарное: штатная команда выпускает ровно тот файл, который читает гейт.
Здесь проверяется вся цепочка настоящим кодом, без подмены звеньев: коллектор
(`scripts/collect_approval_facts.py`) → `steward approval-facts` (in-process, `CliRunner`;
подменён только транспорт — `make_transport` → replay живых снимков `pr657_act_sequence`
E2, E1, E5 и `e7_commit_pulls_graphql_merge`, без преобразований) →
`<checkout>/.steward/approval_facts.jsonl` v3 → `bundle_verdict`/`--check` коллектора →
`gate-check --act-mode legacy --stage release --no-fs` с merge-provenance артефакта = merge
#657. Сеть, `gh` и чужие профили не трогаются: всё — внутри `tmp_path`.
"""

import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from typer.testing import CliRunner

from steward.approvalfacts.model import RequestId, Result
from steward.approvalfacts.publish import build_header, publish
from steward.approvalfacts.v3.transport import ReplayTransport, replay_transport
from steward.gatecheck.approval import policy_digest
from steward.gatecheck.cli import app as gate_app
from steward.riskclassify import cli as producer_cli
from steward.riskclassify.cli import app as steward_app

ROOT = Path(__file__).resolve().parents[3]
REPO = "andrei-shtanakov/devtools"
ORIGIN = f"git@github.com:{REPO}.git"
MERGE = "aa4e7e37d9f1ea34581c9c484d60074b6d3ac18e"
OTHER = "1111111111111111111111111111111111111111"
ARTIFACT = "req.md"
POLICY = (
    "version: 1\n"
    "human_identities:\n"
    "  - github:andrei-shtanakov\n"
    "agent_identities:\n"
    "  - github:ai-prosto\n"
    "agent_merge_allowed: false\n"
    "approval_facts_lease_seconds: 86400\n"
)
PROFILE = """\
profile: e2e
solo_auto_approve: false
artifacts:
  - {id: requirements, owner_role: product, upstream: []}
"""
ASSIGNMENTS = "version: 1\nassignments:\n  github:alice:\n    roles: [product]\n"

Exchanges = Callable[[str], list[dict[str, Any]]]


def _load_collector() -> ModuleType:
    name = "collect_approval_facts"
    path = ROOT / "scripts" / "collect_approval_facts.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging error
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


COLLECTOR: Any = _load_collector()


@pytest.fixture
def world(tmp_path: Path, write_roles: Path) -> dict[str, Path]:
    """Профиль с соседями (`roles.yaml`, `role-assignments.yaml`, `approval-policy.yaml`) и
    чекаут `devtools` (origin — репозиторий снимков) с бандлом в `spec/`."""
    (tmp_path / "role-assignments.yaml").write_text(ASSIGNMENTS, encoding="utf-8")
    policy = tmp_path / "approval-policy.yaml"
    policy.write_text(POLICY, encoding="utf-8")
    profile = tmp_path / "e2e.yaml"
    profile.write_text(PROFILE, encoding="utf-8")
    workspace = tmp_path / "ws"
    checkout = workspace / "devtools"
    checkout.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin", ORIGIN], check=True)
    spec = checkout / "spec"
    spec.mkdir()
    (spec / ARTIFACT).write_text(
        "---\nspec_stage: requirements\nstatus: approved\nversion: 1\n---\n## REQ-001\n",
        encoding="utf-8",
    )
    return {
        "policy": policy,
        "profile": profile,
        "workspace": workspace,
        "checkout": checkout,
        "spec": spec,
    }


def _pr657(exchanges: Exchanges) -> list[dict[str, Any]]:
    """E2, E1, E5 живого `pr657_act_sequence` — обмены доставки продюсера, как есть."""
    return exchanges("pr657_act_sequence")[:3]


def _use(monkeypatch: pytest.MonkeyPatch, t: ReplayTransport) -> None:
    monkeypatch.setattr(producer_cli, "make_transport", lambda: t)


def _gate(world: dict[str, Path], sha: str, *extra: str) -> Any:
    """`gate-check --act-mode legacy --stage release --no-fs` над артефактом с
    merge-provenance `sha`; факты — по пути бандла (или `extra`)."""
    facts = world["spec"].parent / "facts.json"
    facts.write_text(
        json.dumps(
            {
                "default_branch_files": [ARTIFACT],
                "approvals": {ARTIFACT: [{"identity": "github:alice"}]},
                "merge_provenance": {
                    ARTIFACT: {
                        "sha": sha,
                        "subject": "Merge pull request #657",
                        "current_blob_sha": "0" * 40,
                        "merge_method": "merge_commit",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    args = ["--act-mode", "legacy", "--profile", str(world["profile"]), "--stage", "release"]
    args += ["--no-fs", str(facts), *extra, str(world["spec"])]
    return CliRunner().invoke(gate_app, args)


def _in_process_producer(repo: str, root: Path, policy: Path, prs: list[int]) -> tuple[int, str]:
    """`run_producer` коллектора с тем же argv, но in-process: подмена транспорта не
    пересекает границу процесса, а сама команда — настоящая."""
    argv = ["approval-facts", "--repo", repo, "--repo-root", str(root), "--policy", str(policy)]
    result = CliRunner().invoke(steward_app, [*argv, "--prs", ",".join(map(str, prs))])
    return result.exit_code, result.output.strip()


def test_collector_real_producer_publish_check_release_gate(
    world: dict[str, Path], monkeypatch: pytest.MonkeyPatch, exchanges: Exchanges
) -> None:
    t = replay_transport({"exchanges": _pr657(exchanges)})
    _use(monkeypatch, t)
    monkeypatch.setattr(COLLECTOR, "run_producer", _in_process_producer)
    entry = {"repo": REPO, "checkout": "devtools", "prs": [657]}

    (published,) = COLLECTOR.collect([entry], world["workspace"], world["policy"])
    assert published.status == "published", published.detail
    assert t.unused() == [] and t.unexpected == []

    bundle = world["checkout"] / ".steward" / "approval_facts.jsonl"
    header = json.loads(bundle.read_text(encoding="utf-8").splitlines()[0])
    assert header["schema_version"] == "3"
    (checked,) = COLLECTOR.freshness([entry], world["workspace"], world["policy"])
    assert checked.status == "published", checked.detail

    passed = _gate(world, MERGE)
    assert passed.exit_code == 0, passed.output
    # Отрицательный двойник: тот же бандл, provenance другого мержа — гейт честно
    # смотрит в факты, а не зеленеет на любом артефакте.
    refused = _gate(world, OTHER)
    assert refused.exit_code == 1, refused.output
    assert "outside the declared observation scope" in refused.output


def test_issue_then_read_then_release(
    world: dict[str, Path], monkeypatch: pytest.MonkeyPatch, exchanges: Exchanges
) -> None:
    """CLI-путь без коллектора: `--merge-sha` (E7 → PR #657 → E2, E1, E5) в бандл чекаута
    по пути по умолчанию, затем гейт по тому же пути."""
    t = replay_transport(
        {"exchanges": [*exchanges("e7_commit_pulls_graphql_merge"), *_pr657(exchanges)]}
    )
    _use(monkeypatch, t)
    issued = CliRunner().invoke(
        steward_app,
        [
            "approval-facts",
            "--repo",
            REPO,
            "--repo-root",
            str(world["spec"]),
            "--merge-sha",
            MERGE,
            "--policy",
            str(world["policy"]),
        ],
    )
    assert issued.exit_code == 0, issued.output
    assert t.unused() == [] and t.unexpected == []
    passed = _gate(world, MERGE)
    assert passed.exit_code == 0, passed.output


def test_policy_digest_mismatch_still_first(
    world: dict[str, Path], monkeypatch: pytest.MonkeyPatch, exchanges: Exchanges
) -> None:
    """Строка 3 §8.3 по-прежнему первая: правка комментария политики после выпуска —
    находка о другой политике, хотя мерж человеческий, а lease жива."""
    _use(monkeypatch, replay_transport({"exchanges": _pr657(exchanges)}))
    assert _in_process_producer(REPO, world["checkout"], world["policy"], [657])[0] == 0
    world["policy"].write_text(POLICY + "# правка после выпуска\n", encoding="utf-8")
    refused = _gate(world, MERGE)
    assert refused.exit_code == 1, refused.output
    assert "DIFFERENT classification policy" in refused.output


def _write_v2_bundle(world: dict[str, Path]) -> Path:
    """Годный v2-бандл с человеческим мержем #657 — v2-публикатором, байты не выдуманы."""
    scope = [RequestId("merge_sha", MERGE)]
    header = build_header(
        repository=REPO,
        scope=scope,
        policy_version=1,
        policy_digest_value=policy_digest(world["policy"]),
        lease_seconds=86400,
        now=datetime.now(UTC),
    )
    bundle = world["checkout"] / ".steward" / "approval_facts.jsonl"
    v2 = Result(scope[0], "merged", MERGE, "github:andrei-shtanakov", "User", "human")
    publish(bundle, header, [v2])
    return bundle


def test_gate_cli_v2_bundle_is_legacy_v2_finding(world: dict[str, Path]) -> None:
    """Свежий v2-бандл по пути по умолчанию — находка `legacy_v2` (exit 1), не допуск."""
    _write_v2_bundle(world)
    refused = _gate(world, MERGE)
    assert refused.exit_code == 1, refused.output
    assert "unsupported approval-facts/v2" in refused.output


def test_gate_cli_v2_file_explicit_is_config_error(world: dict[str, Path]) -> None:
    """Тот же файл по явному `--approval-facts` — ошибка вызова (exit 2)."""
    bundle = _write_v2_bundle(world)
    refused = _gate(world, MERGE, "--approval-facts", str(bundle))
    assert refused.exit_code == 2, refused.output
    assert "approval-facts/v2" in refused.output
