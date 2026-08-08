"""`steward approval-facts` materializer: gh `mergedBy` -> approval-facts/v1.

All `gh` interaction goes through the single choke point
`steward.approvalfacts._gh`, monkeypatched here — the real `gh` binary is
never invoked in this suite (devtools-sensor pattern: `_gh(args) ->
(returncode, stdout-or-stderr)`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import steward.approvalfacts as approvalfacts
from steward.approvalfacts import (
    SCHEMA,
    ApprovalFacts,
    ApprovalFactsError,
    GhNotFoundError,
    GhUnavailableError,
    load_approval_facts,
    materialize_approval_facts,
    write_approval_facts,
)
from steward.gatecheck.approval import ActorFact
from steward.riskclassify.cli import app

runner = CliRunner()

SHA = "a" * 40


def _sha_response(login: str, typename: str, sha: str = SHA) -> str:
    return json.dumps(
        {
            "data": {
                "repository": {
                    "object": {
                        "associatedPullRequests": {
                            "nodes": [
                                {
                                    "mergeCommit": {"oid": sha},
                                    "mergedBy": {"login": login, "__typename": typename},
                                }
                            ]
                        }
                    }
                }
            }
        }
    )


def _pr_response(login: str, typename: str, sha: str = SHA) -> str:
    return json.dumps(
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "mergeCommit": {"oid": sha},
                        "mergedBy": {"login": login, "__typename": typename},
                    }
                }
            }
        }
    )


# --- materialize_approval_facts (unit level) --------------------------------


def test_materialize_by_merge_sha_resolves_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approvalfacts, "_gh", lambda args: (0, _sha_response("alice", "User")))
    actors = materialize_approval_facts("acme/widgets", merge_shas=[SHA])
    assert actors == {SHA: ActorFact(identity="github:alice", actor_type_hint="User")}


def test_materialize_by_pr_number_resolves_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approvalfacts, "_gh", lambda args: (0, _pr_response("bot-x", "Bot")))
    actors = materialize_approval_facts("acme/widgets", prs=[42])
    assert actors == {SHA: ActorFact(identity="github:bot-x", actor_type_hint="Bot")}


def test_materialize_bot_hint_round_trips_for_downstream_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The materialized fact carries the raw "Bot" hint through unchanged
    — classification (human/agent/unknown) is `classify_actor`'s job, not
    the materializer's; this only proves the hint survives the trip."""
    monkeypatch.setattr(
        approvalfacts, "_gh", lambda args: (0, _sha_response("dependabot[bot]", "Bot"))
    )
    actors = materialize_approval_facts("acme/widgets", merge_shas=[SHA])
    assert actors[SHA].actor_type_hint == "Bot"


def test_materialize_gh_unavailable_raises_distinct_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`gh` missing/not authenticated/erroring is a distinguishable class
    from "PR not found" — never a silently empty result."""
    monkeypatch.setattr(approvalfacts, "_gh", lambda args: (127, "gh: command not found"))
    with pytest.raises(GhUnavailableError):
        materialize_approval_facts("acme/widgets", merge_shas=[SHA])


def test_materialize_pr_not_found_raises_distinct_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = json.dumps({"data": {"repository": {"pullRequest": None}}})
    monkeypatch.setattr(approvalfacts, "_gh", lambda args: (0, empty))
    with pytest.raises(GhNotFoundError):
        materialize_approval_facts("acme/widgets", prs=[999])


def test_materialize_merge_sha_not_associated_with_any_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = json.dumps(
        {"data": {"repository": {"object": {"associatedPullRequests": {"nodes": []}}}}}
    )
    monkeypatch.setattr(approvalfacts, "_gh", lambda args: (0, empty))
    with pytest.raises(GhNotFoundError):
        materialize_approval_facts("acme/widgets", merge_shas=[SHA])


def test_materialize_merged_pr_with_no_merged_by_raises_distinct_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PR that was merged (has a mergeCommit) but whose `mergedBy` can't
    be resolved (e.g. a deleted account) must fail honestly, not silently
    omit the entry."""
    payload = json.dumps(
        {"data": {"repository": {"pullRequest": {"mergeCommit": {"oid": SHA}, "mergedBy": None}}}}
    )
    monkeypatch.setattr(approvalfacts, "_gh", lambda args: (0, payload))
    with pytest.raises(GhNotFoundError):
        materialize_approval_facts("acme/widgets", prs=[1])


def test_materialize_bad_repo_format_raises_value_error_not_gh_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed --repo is a caller bug, never a MaterializeError: gh is
    never even called, so it must not masquerade as GhNotFoundError's
    "asked gh, got an authoritative no" or GhUnavailableError's "gh call
    failed"."""
    calls: list[list[str]] = []
    monkeypatch.setattr(approvalfacts, "_gh", lambda args: calls.append(args) or (0, "{}"))
    with pytest.raises(ValueError, match="owner/name"):
        materialize_approval_facts("not-a-slug", merge_shas=[SHA])
    assert calls == []


def test_materialize_first_failure_aborts_without_partial_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two SHAs requested, the second fails to resolve — the whole call
    must raise, not return a mapping with only the first entry."""
    calls = {"n": 0}

    def fake_gh(args: list[str]) -> tuple[int, str]:
        calls["n"] += 1
        if calls["n"] == 1:
            return 0, _sha_response("alice", "User", sha="a" * 40)
        return 127, "network error"

    monkeypatch.setattr(approvalfacts, "_gh", fake_gh)
    with pytest.raises(GhUnavailableError):
        materialize_approval_facts("acme/widgets", merge_shas=["a" * 40, "b" * 40])


# --- write_approval_facts / load_approval_facts round trip ------------------


def test_write_then_load_round_trips(tmp_path: Path) -> None:
    out = tmp_path / "approval-facts.json"
    actors = {SHA: ActorFact(identity="github:alice", actor_type_hint="User")}
    write_approval_facts(out, actors)

    facts = load_approval_facts(out)
    assert facts == ApprovalFacts(actors=actors)

    on_disk = json.loads(out.read_text())
    assert on_disk["schema"] == SCHEMA
    assert on_disk["actors"][SHA] == {"identity": "github:alice", "type_hint": "User"}


def test_load_approval_facts_wrong_schema_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "approval-facts.json"
    path.write_text(json.dumps({"schema": "approval-facts/v0", "actors": {}}))
    with pytest.raises(ApprovalFactsError):
        load_approval_facts(path)


def test_load_approval_facts_actors_not_mapping_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "approval-facts.json"
    path.write_text(json.dumps({"schema": SCHEMA, "actors": []}))
    with pytest.raises(ApprovalFactsError):
        load_approval_facts(path)


def test_load_approval_facts_entry_missing_field_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "approval-facts.json"
    path.write_text(json.dumps({"schema": SCHEMA, "actors": {SHA: {"identity": "github:x"}}}))
    with pytest.raises(ApprovalFactsError):
        load_approval_facts(path)


def test_load_approval_facts_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ApprovalFactsError):
        load_approval_facts(Path("/nonexistent/approval-facts.json"))


def test_load_approval_facts_empty_actors_is_legitimate(tmp_path: Path) -> None:
    """An authored file that legitimately declares zero known actors is
    valid — the honesty rule is about the materializer never silently
    dropping a *requested* identifier, not about forbidding empty files
    in general (e.g. a hand-authored fixture)."""
    path = tmp_path / "approval-facts.json"
    path.write_text(json.dumps({"schema": SCHEMA, "actors": {}}))
    facts = load_approval_facts(path)
    assert facts.actors == {}


# --- CLI: `steward approval-facts` -------------------------------------------


def test_cli_writes_file_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approvalfacts, "_gh", lambda args: (0, _sha_response("alice", "User")))
    out = tmp_path / "facts.json"
    result = runner.invoke(
        app, ["approval-facts", "--repo", "acme/widgets", "--merge-sha", SHA, "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    on_disk = json.loads(out.read_text())
    assert on_disk["actors"][SHA]["identity"] == "github:alice"


def test_cli_gh_failure_exits_nonzero_and_writes_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(approvalfacts, "_gh", lambda args: (127, "gh: command not found"))
    out = tmp_path / "facts.json"
    result = runner.invoke(
        app, ["approval-facts", "--repo", "acme/widgets", "--merge-sha", SHA, "--out", str(out)]
    )
    assert result.exit_code != 0
    assert not out.exists(), "a failed materialization must never write a partial/empty file"


def test_cli_bad_repo_format_is_config_error_not_materialize_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 1 regression test: a malformed --repo must fail as a
    config error (exit 2) validated before gh is ever touched — not fall
    through to materialize_approval_facts and surface as exit 3."""
    calls: list[list[str]] = []
    monkeypatch.setattr(approvalfacts, "_gh", lambda args: calls.append(args) or (0, "{}"))
    out = tmp_path / "facts.json"
    result = runner.invoke(
        app,
        ["approval-facts", "--repo", "not-a-slug", "--merge-sha", SHA, "--out", str(out)],
    )
    assert result.exit_code == 2, result.output
    assert not out.exists()
    assert calls == [], "gh must never be called for a malformed --repo"


def test_cli_no_identifiers_is_config_error(tmp_path: Path) -> None:
    out = tmp_path / "facts.json"
    result = runner.invoke(app, ["approval-facts", "--repo", "acme/widgets", "--out", str(out)])
    assert result.exit_code == 2
    assert not out.exists()


def test_cli_prs_flag_parses_comma_separated_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    def fake_gh(args: list[str]) -> tuple[int, str]:
        seen.append(args)
        return 0, _pr_response("alice", "User")

    monkeypatch.setattr(approvalfacts, "_gh", fake_gh)
    out = tmp_path / "facts.json"
    result = runner.invoke(
        app, ["approval-facts", "--repo", "acme/widgets", "--prs", "1,2", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert len(seen) == 2
