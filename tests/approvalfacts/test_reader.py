"""Читатель обязан ДОКАЗАТЬ полноту, а не поверить `complete: true`.

Каждый тест ломает ровно один инвариант: если бы проверки не было, файл
прошёл бы и повлиял на enforcement.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from steward.approvalfacts.model import RequestId, scope_digest
from steward.approvalfacts.reader import UnreadableFacts, detect_legacy_v1, load_facts

SHA = "221457933968be9e95acd51d548e080f739c794c"
OTHER_SHA = "05aa16e12981b35c224c2ca28d65f0a9c15c274e"
REPO = "andrei-shtanakov/steward"
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
DIGEST = "sha256:" + "0" * 64
CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "approval-facts" / "v2"


def _header(scope, **over):
    base = {
        "kind": "header",
        "schema_version": "2",
        "repository": REPO,
        "generated_at": "2026-08-21T09:00:00Z",
        "valid_until": "2026-08-22T09:00:00Z",
        "policy_version": 1,
        "policy_digest": DIGEST,
        "complete": True,
        "scope_sha256": scope_digest(scope),
        "scope": [r.as_dict() for r in scope],
    }
    base.update(over)
    return base


def _write(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "approval_facts.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def _merged(request, sha=SHA):
    return {
        "kind": "result",
        "request": request.as_dict(),
        "state": "merged",
        "merge_sha": sha,
        "identity": "github:merge-broker",
        "type_hint": "Bot",
        "actor_class": "agent",
    }


def test_valid_file_loads(tmp_path: Path) -> None:
    scope = [RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_header(scope), _merged(scope[0])])
    facts = load_facts(path, expected_repository=REPO, now=NOW)
    assert facts.by_merge_sha()[SHA].actor_class == "agent"


def test_missing_result_for_scope_item_is_unreadable(tmp_path: Path) -> None:
    """Усечённый JSONL — не валидный префикс: header обещает scope целиком."""
    scope = [RequestId("merge_sha", SHA), RequestId("pr", 42)]
    path = _write(tmp_path, [_header(scope), _merged(scope[0])])
    with pytest.raises(UnreadableFacts, match="scope"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_result_outside_scope_is_unreadable(tmp_path: Path) -> None:
    scope = [RequestId("merge_sha", SHA)]
    stray = RequestId("pr", 7)
    path = _write(tmp_path, [_header(scope), _merged(scope[0]), _merged(stray, OTHER_SHA)])
    with pytest.raises(UnreadableFacts, match="вне scope|outside scope"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_header_must_be_first_and_only(tmp_path: Path) -> None:
    scope = [RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_header(scope), _merged(scope[0]), _header(scope)])
    with pytest.raises(UnreadableFacts, match="header"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_duplicate_scope_item_is_unreadable(tmp_path: Path) -> None:
    scope = [RequestId("pr", 42), RequestId("pr", 42)]
    path = _write(tmp_path, [_header(scope), _merged(scope[0])])
    with pytest.raises(UnreadableFacts, match="дубл|duplicate"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_contradicting_aliases_are_unreadable(tmp_path: Path) -> None:
    """Оба алиаса разрешились в один merge, но наблюдения разошлись."""
    scope = [RequestId("pr", 42), RequestId("merge_sha", SHA)]
    good = _merged(scope[0])
    bad = _merged(scope[1])
    bad["actor_class"] = "human"
    path = _write(tmp_path, [_header(scope), good, bad])
    with pytest.raises(UnreadableFacts, match="алиас|alias"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_agreeing_aliases_are_valid(tmp_path: Path) -> None:
    scope = [RequestId("pr", 42), RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_header(scope), _merged(scope[0]), _merged(scope[1])])
    facts = load_facts(path, expected_repository=REPO, now=NOW)
    assert len(facts.results) == 2


def test_foreign_repository_is_unreadable(tmp_path: Path) -> None:
    """Инвариант 11: файл чужого репозитория с тем же SHA не влияет ни на что."""
    scope = [RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_header(scope, repository="someone/else"), _merged(scope[0])])
    with pytest.raises(UnreadableFacts, match="repository"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_repository_comparison_ignores_case(tmp_path: Path) -> None:
    scope = [RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_header(scope, repository=REPO.upper()), _merged(scope[0])])
    assert load_facts(path, expected_repository=REPO, now=NOW).results


def test_future_generated_at_beyond_skew_is_unreadable(tmp_path: Path) -> None:
    scope = [RequestId("merge_sha", SHA)]
    future = (NOW + timedelta(seconds=301)).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = _write(
        tmp_path,
        [
            _header(scope, generated_at=future, valid_until="2026-08-23T09:00:00Z"),
            _merged(scope[0]),
        ],
    )
    with pytest.raises(UnreadableFacts, match="будущ|future"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_lease_longer_than_contract_bound_is_unreadable(tmp_path: Path) -> None:
    scope = [RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_header(scope, valid_until="2026-12-31T09:00:00Z"), _merged(scope[0])])
    with pytest.raises(UnreadableFacts, match="lease"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_scope_sha256_mismatch_is_unreadable(tmp_path: Path) -> None:
    scope = [RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_header(scope, scope_sha256="sha256:" + "9" * 64), _merged(scope[0])])
    with pytest.raises(UnreadableFacts, match="scope_sha256"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_legacy_v1_is_detected_not_interpreted(tmp_path: Path) -> None:
    path = tmp_path / "approval_facts.jsonl"
    path.write_text(json.dumps({"schema": "approval-facts/v1", "actors": {}}), encoding="utf-8")
    assert detect_legacy_v1(path) is True
    with pytest.raises(UnreadableFacts, match="legacy"):
        load_facts(path, expected_repository=REPO, now=NOW)


# --- Self-review additions: invariants the brief's own 13 tests left unproven ---


def test_header_not_on_first_line_is_unreadable(tmp_path: Path) -> None:
    """Инвариант 1 has two halves: header is the *only* header (already
    covered above) and header is *first*. A result line before the header
    must be rejected too."""
    scope = [RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_merged(scope[0]), _header(scope)])
    with pytest.raises(UnreadableFacts, match="header"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_duplicate_result_for_same_scope_item_is_unreadable(tmp_path: Path) -> None:
    """Инвариант 5: two result rows echoing the same, non-duplicated, scope
    item. Distinct from the scope-duplicate test above (invariant 2), which
    breaks before any result row is even read."""
    scope = [RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_header(scope), _merged(scope[0]), _merged(scope[0])])
    with pytest.raises(UnreadableFacts, match="дубл|duplicate"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_valid_until_not_after_generated_at_is_unreadable(tmp_path: Path) -> None:
    """Инвариант 10: `valid_until` must be strictly after `generated_at`."""
    scope = [RequestId("merge_sha", SHA)]
    path = _write(
        tmp_path,
        [
            _header(scope, generated_at="2026-08-21T09:00:00Z", valid_until="2026-08-21T08:00:00Z"),
            _merged(scope[0]),
        ],
    )
    with pytest.raises(UnreadableFacts, match="valid_until"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_merged_result_missing_actor_fields_is_unreadable(tmp_path: Path) -> None:
    """Инвариант 7: `state: merged` requires `identity`/`type_hint`/`actor_class`
    (README field matrix) — a result missing them must not silently pass."""
    scope = [RequestId("merge_sha", SHA)]
    incomplete = {
        "kind": "result",
        "request": scope[0].as_dict(),
        "state": "merged",
        "merge_sha": SHA,
    }
    path = _write(tmp_path, [_header(scope), incomplete])
    with pytest.raises(UnreadableFacts):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_negative_state_with_forbidden_actor_field_is_unreadable(tmp_path: Path) -> None:
    """Инвариант 7: negative states forbid identity/type_hint/actor_class —
    mirrors the contract's `extra_field_on_negative.jsonl` fixture."""
    scope = [RequestId("pr", 42)]
    stray = {
        "kind": "result",
        "request": scope[0].as_dict(),
        "state": "not_found",
        "identity": "github:x",
    }
    path = _write(tmp_path, [_header(scope), stray])
    with pytest.raises(UnreadableFacts):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_negative_state_with_merge_sha_present_is_unreadable(tmp_path: Path) -> None:
    """Инвариант 7: negative states require `merge_sha: null`, not a resolved
    oid — mirrors the contract's `missing_merge_sha_negative.jsonl` intent
    from the other direction (present instead of absent)."""
    scope = [RequestId("pr", 42)]
    stray = {
        "kind": "result",
        "request": scope[0].as_dict(),
        "state": "not_found",
        "merge_sha": SHA,
    }
    path = _write(tmp_path, [_header(scope), stray])
    with pytest.raises(UnreadableFacts):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_actor_unavailable_requires_merge_sha_but_forbids_actor_fields(tmp_path: Path) -> None:
    """Инвариант 7: `actor_unavailable` is the one state with a resolved
    `merge_sha` but no actor fields — both halves of that asymmetric row
    must be enforced."""
    scope = [RequestId("merge_sha", SHA)]
    missing_sha = {"kind": "result", "request": scope[0].as_dict(), "state": "actor_unavailable"}
    path = _write(tmp_path, [_header(scope), missing_sha])
    with pytest.raises(UnreadableFacts):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_negative_state_fixture_from_contract_is_valid(tmp_path: Path) -> None:
    """Positive counterpart: the contract's `negative_states.jsonl` fixture
    (four terminal states, header + four results) must load cleanly."""
    fixture = CONTRACT / "fixtures" / "negative_states.jsonl"
    records = [json.loads(ln) for ln in fixture.read_text(encoding="utf-8").splitlines() if ln]
    header = records[0]
    facts = load_facts(
        _write(tmp_path, records),
        expected_repository=header["repository"],
        now=datetime.fromisoformat(header["generated_at"].replace("Z", "+00:00")),
    )
    assert len(facts.results) == 4


# --- Ruling 2: reader must enforce the same canonical wire form as the schema ---


@pytest.mark.parametrize(
    "bad_generated_at",
    [
        "2026-08-21T09:00:00+00:00",  # explicit offset instead of Z
        "2026-08-21t09:00:00Z",  # lowercase t
        "2026-08-21T09:00:00z",  # lowercase z
        "2026-08-21T09:00:00.000Z",  # sub-second precision
    ],
)
def test_non_canonical_timestamp_form_is_unreadable(tmp_path: Path, bad_generated_at) -> None:
    """`fromisoformat` after replacing `Z` would accept all of these — the
    reader must be at least as strict as the schema's `pattern`, not looser."""
    scope = [RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_header(scope, generated_at=bad_generated_at), _merged(scope[0])])
    with pytest.raises(UnreadableFacts):
        load_facts(path, expected_repository=REPO, now=NOW)


# --- Ruling 3: the reader owns calendar validity, the schema only checks shape ---


def test_impossible_calendar_date_is_unreadable(tmp_path: Path) -> None:
    """`2026-13-45T99:99:99Z` matches the schema's shape pattern but is not a
    real date — the reader, not the schema, must catch this."""
    scope = [RequestId("merge_sha", SHA)]
    path = _write(
        tmp_path,
        [_header(scope, generated_at="2026-13-45T99:99:99Z"), _merged(scope[0])],
    )
    with pytest.raises(UnreadableFacts):
        load_facts(path, expected_repository=REPO, now=NOW)


# --- Ruling 4: `complete` must be exactly `true` ---


def test_incomplete_header_is_unreadable(tmp_path: Path) -> None:
    scope = [RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_header(scope, complete=False), _merged(scope[0])])
    with pytest.raises(UnreadableFacts, match="complete"):
        load_facts(path, expected_repository=REPO, now=NOW)


# --- Ruling 5: canonicalisation anchored to the published contract fixture ---


def test_scope_digest_matches_published_fixture() -> None:
    """Both sides of every other test build headers with `scope_digest` and
    compare against `scope_digest` — a canonicalisation regression (changed
    separator, changed sort key) would stay invisible. The fixture published
    by Task 1 is the external anchor that catches it."""
    lines = (CONTRACT / "fixtures" / "clean.jsonl").read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    scope = [RequestId(item["kind"], item["value"]) for item in header["scope"]]
    assert scope_digest(scope) == header["scope_sha256"]
