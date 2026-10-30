"""Модель `approval-facts/v3`: типы фактов, закрытые словари причин, канон меток."""

import dataclasses

import pytest

from steward.approvalfacts.v3.model import (
    EFFECTIVE_STATES,
    HISTORY_VIOLATION_CODES,
    REVIEW_STATES,
    UNAVAILABLE_CODES,
    BadTimestamp,
    Review,
    canon_ts,
    unavailable,
    violation,
)


def test_canon_ts_accepts_z_and_offset():
    assert canon_ts("2026-10-12T10:00:00Z") == "2026-10-12T10:00:00Z"
    assert canon_ts("2026-10-12T10:00:00+00:00") == "2026-10-12T10:00:00Z"


@pytest.mark.parametrize(
    "raw",
    [
        "2026-10-12T10:00:00.5Z",
        "2026-10-12T10:00:00",
        "2026-10-12T12:00:00+02:00",
        1700000000,
        None,
        "",
        "2026-13-12T10:00:00Z",
        "٢٠٢٦-10-12T10:00:00Z",
        "2026-10-12T10:00:00Z ",
        "2026-10-12T10:00:00Z\n",
        " 2026-10-12T10:00:00Z",
        "2026-10-12t10:00:00z",
        "2026-10-12T10:00:00-00:00",
    ],
)
def test_canon_ts_rejects(raw):
    with pytest.raises(BadTimestamp):
        canon_ts(raw)


def test_bad_timestamp_is_value_error():
    assert issubclass(BadTimestamp, ValueError)


def test_unavailable_code_and_detail():
    u = unavailable("http", "403 repos/o/r/pulls/7")
    assert u.reason == "http: 403 repos/o/r/pulls/7" and u.code == "http"
    assert unavailable("unstable").reason == "unstable"
    assert unavailable("unstable").code == "unstable"


def test_violation_code_and_detail():
    v = violation("commit_without_pr", "abc123")
    assert v.reason == "commit_without_pr: abc123" and v.code == "commit_without_pr"


def test_unknown_code_is_rejected():
    with pytest.raises(ValueError):
        unavailable("whatever")
    with pytest.raises(ValueError):
        violation("unavailable", "x")


def test_direct_construction_validates_code():
    from steward.approvalfacts.v3.model import HistoryViolation, Unavailable

    with pytest.raises(ValueError):
        Unavailable("nope: x")
    with pytest.raises(ValueError):
        HistoryViolation("http: x")


def test_codes_are_exactly_the_spec_vocabulary():
    assert UNAVAILABLE_CODES == {
        "transport", "http", "graphql_errors", "malformed", "pagination", "bad_timestamp",
        "unknown_state", "missing_commit_id", "missing_submitted_at",
        "dismissal_without_previous_state", "dismissed_without_event", "duplicate_dismissal",
        "orphan_dismissal", "delivery_mismatch", "bot_login_mismatch", "unstable",
        "object_missing", "path_absent_at_head", "not_a_blob", "chain_limit",
        "branch_not_default", "no_pr_for_commit", "tree_truncated", "actor_unresolved",
    }  # fmt: skip
    assert HISTORY_VIOLATION_CODES == {
        "commit_without_pr", "rebase_intermediate", "pr_base_mismatch", "anchor_not_reached",
    }  # fmt: skip


def test_state_vocabularies():
    assert REVIEW_STATES == {"PENDING", "COMMENTED", "APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
    assert EFFECTIVE_STATES == {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}


def test_public_names_exported():
    from steward.approvalfacts import v3

    for name in (
        "Review", "Dismissal", "ReviewFacts", "PathBlob", "Delivery", "Unavailable",
        "Activation", "HistoryViolation", "EventKey",
    ):  # fmt: skip
        assert hasattr(v3, name)
        assert name in v3.__all__


def test_dataclasses_are_frozen():
    r = Review("1", "alice", "User", "APPROVED", "a" * 40, "2026-10-12T09:00:00Z")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.state = "DISMISSED"  # type: ignore[misc]


def test_empty_detail_gives_bare_code():
    assert unavailable("http", "").reason == "http"


@pytest.mark.parametrize("reason", ["", "http:", "http: ", "http:x", "transport : x", "HTTP"])
def test_malformed_reason_is_rejected(reason):
    from steward.approvalfacts.v3.model import Unavailable

    with pytest.raises(ValueError):
        Unavailable(reason)


def test_empty_code_is_rejected():
    with pytest.raises(ValueError):
        unavailable("")
    with pytest.raises(ValueError):
        violation("", "x")
    with pytest.raises(ValueError):
        violation("commit_without_pr", "")


@pytest.mark.parametrize("reason", ["commit_without_pr", "commit_without_pr: "])
def test_history_violation_requires_detail_on_direct_construction(reason):
    from steward.approvalfacts.v3.model import HistoryViolation

    with pytest.raises(ValueError):
        HistoryViolation(reason)
