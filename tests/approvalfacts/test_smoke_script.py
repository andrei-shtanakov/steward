"""Pure verdict logic of the live smoke script (M0 Task 13), offline.

No network: verdicts over library results are driven by ``replay_transport`` on the
committed live captures; the rest are plain values.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from steward.approvalfacts.v3 import (
    Dismissal,
    Request,
    Review,
    ReviewFacts,
    Unavailable,
    read_open_review,
    replay_transport,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "approval_facts_v3_smoke.py"
PR657 = ROOT / "tests" / "fixtures" / "approvalfacts_v3" / "live" / "pr657_act_sequence.json"
SMOKE_OPS = ("StewardV3Reviews", "StewardV3Timeline")
DEVTOOLS = "andrei-shtanakov/devtools"
SHA = "b3651bcb2907bd6d1b6d421b018701520fb64066"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("approval_facts_v3_smoke", SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging error
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["approval_facts_v3_smoke"] = module
    spec.loader.exec_module(module)
    return module


SMOKE = _load()


def _passes() -> list[dict[str, Any]]:
    """The two review/timeline passes of the live #657 sequence (E3, E4, E3, E4)."""
    exchanges = json.loads(PR657.read_text())["exchanges"]
    return [ex for ex in exchanges if ex["request"].get("op") in SMOKE_OPS]


def _facts(dismissals: tuple[Dismissal, ...], state: str) -> ReviewFacts:
    review = Review("1", "alice", "User", state, SHA, "2026-10-30T11:34:44Z")
    return ReviewFacts("o/r", 7, SHA, "master", "MERGED", (review,), dismissals)


def test_summary_counts_every_outcome() -> None:
    rows = [SMOKE.Row("a", "verified", "x"), SMOKE.Row("b", "not_verified", "y")]
    got = SMOKE.summarize(rows)
    assert got["counts"] == {"verified": 1, "not_verified": 1, "contradicts": 0}
    assert [r["item"] for r in got["rows"]] == ["a", "b"]


def test_row_rejects_unknown_outcome() -> None:
    with pytest.raises(ValueError, match="unknown outcome"):
        SMOKE.Row("a", "passed", "x")


def test_no_dismissal_candidate_is_not_verified_with_owner_reason() -> None:
    row = SMOKE.dismissal_row("o/r", {})
    assert row.outcome == "not_verified"
    assert SMOKE.NO_WRITE in row.detail


def test_dismissal_row_over_live_replay_is_verified() -> None:
    transport = replay_transport({"exchanges": _passes()})
    facts = read_open_review(transport, "andrei-shtanakov/devtools", 657)
    assert transport.unexpected == [] and transport.unused() == []
    row = SMOKE.dismissal_row("andrei-shtanakov/devtools", {657: facts})
    assert row.outcome == "verified"
    assert "events=0" in row.detail


def test_dismissed_review_without_event_contradicts() -> None:
    passes = copy.deepcopy(_passes())
    for ex in passes:
        if ex["request"]["op"] == "StewardV3Reviews":
            nodes = ex["response"]["body"]["data"]["repository"]["pullRequest"]["reviews"]
            nodes["nodes"][1]["state"] = "DISMISSED"
    facts = read_open_review(replay_transport({"exchanges": passes}), DEVTOOLS, 657)
    assert isinstance(facts, Unavailable) and facts.code == "dismissed_without_event"
    assert SMOKE.dismissal_row("o/r", {657: facts}).outcome == "contradicts"


def test_dismissal_count_mismatch_contradicts() -> None:
    row = SMOKE.dismissal_row("o/r", {7: _facts((), "DISMISSED")})
    assert row.outcome == "contradicts"
    ok = _facts((Dismissal("1", "bob", "2026-10-30T12:00:00Z", "APPROVED"),), "DISMISSED")
    assert SMOKE.dismissal_row("o/r", {7: ok}).outcome == "verified"


def test_other_library_refusal_is_not_verified() -> None:
    row = SMOKE.dismissal_row("o/r", {7: Unavailable("transport: down")})
    assert row.outcome == "not_verified"


@pytest.mark.parametrize(
    ("status", "body", "outcome"),
    [
        (200, {"status": "ahead"}, "verified"),
        (200, {"status": "behind"}, "not_verified"),
        (200, {"status": "weird"}, "contradicts"),
        (404, {"message": "Not Found"}, "contradicts"),
    ],
)
def test_compare_status_row(status: int, body: object, outcome: str) -> None:
    assert SMOKE.compare_status_row("4a", "ahead", status, body, "t").outcome == outcome


def _cmp(status: str, base: str = "m" * 40) -> dict[str, Any]:
    return {"status": status, "ahead_by": 1, "behind_by": 2, "merge_base_commit": {"sha": base}}


@pytest.mark.parametrize(
    ("encoded", "plain", "outcome"),
    [
        ((200, _cmp("diverged")), (200, _cmp("diverged")), "verified"),
        ((404, {"message": "Not Found"}), (200, _cmp("diverged")), "contradicts"),
        ((200, _cmp("diverged", "x" * 40)), (200, _cmp("diverged")), "contradicts"),
        ((200, _cmp("diverged")), (404, None), "not_verified"),
    ],
)
def test_slash_compare_row(encoded: Any, plain: Any, outcome: str) -> None:
    assert SMOKE.slash_compare_row(encoded, plain, ()).outcome == outcome


@pytest.mark.parametrize(
    ("actor", "want"),
    [
        (None, True),
        ({"login": "Ghost", "__typename": "User"}, True),
        ({"login": "m", "__typename": "Mannequin"}, True),
        ({"login": "alice", "__typename": "User"}, False),
        ({"login": "app", "__typename": "Bot"}, False),
    ],
)
def test_unresolved_actor(actor: object, want: bool) -> None:
    assert SMOKE.unresolved(actor) is want


def _scan(*reviews: tuple[str, str]) -> Any:
    nodes = [{"state": s, "author": {"login": who, "__typename": "User"}} for s, who in reviews]
    return SMOKE.Scan("ai-prosto", [{"number": 1, "reviews": {"nodes": nodes}}])


def test_pending_visibility() -> None:
    assert SMOKE.check_pending(_scan(("PENDING", "ai-prosto")), "o/r").outcome == "not_verified"
    got = SMOKE.check_pending(_scan(("PENDING", "alice")), "o/r")
    assert got.outcome == "verified" and got.targets == ("o/r#1",)


def test_anonymous_transport_is_get_only() -> None:
    with pytest.raises(ValueError, match="GET-only"):
        SMOKE.AnonTransport().send(Request("POST", "graphql", op="X", query="query X{a}"))


def test_repository_outside_allowlist_is_refused() -> None:
    with pytest.raises(ValueError, match="not allowed"):
        SMOKE.scan_prs(replay_transport({"exchanges": []}), "someone/private")
