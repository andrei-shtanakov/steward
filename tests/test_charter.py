"""`steward charter-check` — charter schema 2 and workstream-code uniqueness.

steward#190 item 3. Contract mirrors devtools ``governance/charter_guard.py``
(bundle-criteria oracle spec rev 10 §1.1–1.2): the code registry is the schema-2
charters themselves; a schema-2 charter is a tombstone (no delete, no move); a
code collision's violator is the one merged later on first-parent, and only it
may change its code.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from steward.charter import check_repo, read_charter
from steward.riskclassify.cli import app

runner = CliRunner()

_TODO = "# TODO\n\n- [ ] item @id:enc-work\n- [ ] item @id:other-work\n"


def _charter(code: str = "ENC", plan_item: str = "todo://repo/enc-work", schema: object = 2) -> str:
    return (
        f"---\nspec_stage: charter\nschema: {schema}\ncode: {code}\n"
        f"plan_item: {plan_item}\n---\n# Charter\n"
    )


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "steward-test")
    (repo / "TODO.md").write_text(_TODO)
    return repo


def _write(repo: Path, ws: str, text: str) -> Path:
    path = repo / "workstreams" / ws / "spec" / "00-charter.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _codes(findings) -> list[str]:
    return [f.rule_id for f in findings]


# --- read_charter -----------------------------------------------------------


def test_no_frontmatter_is_schema_1() -> None:
    assert read_charter("# Charter\n").schema == 1


def test_broken_frontmatter_is_malformed_not_schema_1() -> None:
    assert read_charter("---\nschema: 2\ncode: [unclosed\n---\n").malformed


def test_string_schema_is_read_as_its_number() -> None:
    assert read_charter(_charter(schema='"2"')).schema == 2


# --- grammar ------------------------------------------------------------------


def test_clean_schema_2_charter_has_no_findings(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo, "ws-a", _charter())
    assert check_repo(repo, base_ref=None) == []


def test_schema_1_charter_is_not_checked(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo, "ws-a", "---\nspec_stage: charter\n---\n# Charter\n")
    assert check_repo(repo, base_ref=None) == []


def test_grammar_findings(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo, "bad-code", _charter(code="enc"))
    _write(repo, "no-item", _charter(code="NOI", plan_item="todo://repo/missing-item"))
    _write(repo, "foreign", _charter(code="FOR", plan_item="todo://other-repo/enc-work"))
    _write(repo, "shape", _charter(code="SHP", plan_item="https://example/x"))
    _write(repo, "schema3", _charter(code="THR", schema=3))
    _write(repo, "broken", "---\ncode: [unclosed\n---\n")
    messages = {f.path.split("/")[1]: (f.rule_id, f.message) for f in check_repo(repo, None)}
    assert messages["bad-code"][0] == "CHARTER-CODE"
    assert (
        messages["no-item"][0] == "CHARTER-PLAN-ITEM" and "missing-item" in messages["no-item"][1]
    )
    assert messages["foreign"][0] == "CHARTER-PLAN-ITEM" and "other-repo" in messages["foreign"][1]
    assert messages["shape"][0] == "CHARTER-PLAN-ITEM"
    assert messages["schema3"][0] == "CHARTER-SCHEMA"
    assert messages["broken"][0] == "CHARTER-MALFORMED"


def test_missing_code_and_plan_item(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo, "ws-a", "---\nspec_stage: charter\nschema: 2\n---\n")
    assert sorted(_codes(check_repo(repo, None))) == ["CHARTER-CODE", "CHARTER-PLAN-ITEM"]


def test_repo_name_override(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo, "ws-a", _charter(plan_item="todo://steward/enc-work"))
    assert _codes(check_repo(repo, None)) == ["CHARTER-PLAN-ITEM"]
    assert check_repo(repo, None, repo_name="steward") == []


# --- collision: the later first-parent merge is the violator ---------------------


def test_collision_names_the_later_merged_charter(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo, "zz-first", _charter())
    _commit(repo, "first")
    _write(repo, "aa-second", _charter(plan_item="todo://repo/other-work"))
    _commit(repo, "second")
    findings = check_repo(repo, None)
    assert _codes(findings) == ["CHARTER-COLLISION"]
    assert "aa-second" in findings[0].path and "zz-first" in findings[0].message


def test_uncommitted_charter_is_the_violator(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo, "zz-merged", _charter())
    _commit(repo, "merged")
    _write(repo, "aa-new", _charter(plan_item="todo://repo/other-work"))
    findings = check_repo(repo, None)
    assert len(findings) == 1 and "aa-new" in findings[0].path


# --- against a base: tombstone and code immutability -------------------------


def test_deleting_or_moving_a_schema_2_charter_is_a_tombstone_finding(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = _write(repo, "ws-a", _charter())
    base = _commit(repo, "base")
    path.unlink()
    _write(repo, "ws-renamed", _charter())
    assert "CHARTER-TOMBSTONE" in _codes(check_repo(repo, base))


def test_downgrading_to_schema_1_is_a_tombstone_finding(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = _write(repo, "ws-a", _charter())
    base = _commit(repo, "base")
    path.write_text("---\nspec_stage: charter\n---\n")
    assert _codes(check_repo(repo, base)) == ["CHARTER-TOMBSTONE"]


def test_code_is_immutable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = _write(repo, "ws-a", _charter())
    base = _commit(repo, "base")
    path.write_text(_charter(code="NEW"))
    assert _codes(check_repo(repo, base)) == ["CHARTER-CODE-CHANGE"]


def test_only_the_collision_violator_may_change_its_code(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo, "zz-first", _charter())
    _commit(repo, "first")
    late = _write(repo, "aa-late", _charter(plan_item="todo://repo/other-work"))
    base = _commit(repo, "second — collision lands in base")
    late.write_text(_charter(code="LAT", plan_item="todo://repo/other-work"))
    assert check_repo(repo, base) == []


def test_unresolvable_base_is_a_finding_not_an_empty_base(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo, "ws-a", _charter())
    _commit(repo, "c")
    assert _codes(check_repo(repo, "no-such-ref")) == ["CHARTER-BASE"]


# --- CLI ----------------------------------------------------------------------------


def test_cli_exit_codes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo, "ws-a", _charter())
    ok = runner.invoke(app, ["charter-check", "--repo", str(repo)])
    assert ok.exit_code == 0, ok.output
    _write(repo, "ws-b", _charter(code="bad"))
    bad = runner.invoke(app, ["charter-check", "--repo", str(repo)])
    assert bad.exit_code == 1 and "CHARTER-CODE" in bad.output
    missing = runner.invoke(app, ["charter-check", "--repo", str(tmp_path / "nope")])
    assert missing.exit_code == 2
