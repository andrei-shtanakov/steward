"""Читатель файла release-гейта `approval-facts/v3` (спека steward §6): `load_release_facts`,
`detect_legacy_v2`, `ApprovalFactsV3`.

Одиннадцать инвариантов v2-читателя переносятся с матрицей полей §6: поля мержа живут в
`result.delivery`. Хороший файл пишет сам v3-публикатор (`publish` + `release_record`);
отрицательные — одна правка JSON-записи хорошего файла, по нарушению на тест.
"""

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from steward.approvalfacts.model import Header, RequestId, Result, scope_digest
from steward.approvalfacts.publish import build_header, publish
from steward.approvalfacts.reader import UnreadableFacts
from steward.approvalfacts.v3.model import Delivery
from steward.approvalfacts.v3.release import release_record
from steward.approvalfacts.v3.release_reader import (
    SCHEMA_VERSION_V3,
    ApprovalFactsV3,
    ReleaseResult,
    detect_legacy_v2,
    load_release_facts,
)

Record = dict[str, Any]

REPO = "andrei-shtanakov/devtools"
MERGE = "aa4e7e37d9f1ea34581c9c484d60074b6d3ac18e"
HEAD = "b3651bcb2907bd6d1b6d421b018701520fb64066"
OTHER = "1111111111111111111111111111111111111111"
MERGED_AT = "2026-10-30T12:09:17Z"
NOW = datetime(2026, 10, 31, 9, 0, 0, tzinfo=UTC)
PR657 = RequestId("pr", 657)
SHA657 = RequestId("merge_sha", MERGE)
MERGED = Delivery(True, MERGE, MERGED_AT, "ai-prosto", "User", HEAD, True, ())
NO_MERGER = Delivery(True, MERGE, MERGED_AT, None, None, HEAD, True, ())
#: Строки файла: 0 — header, 1 — merged pr:657, 2 — merge_sha:MERGE (алиас), 3 — not_merged,
#: 4 — not_found, 5 — no_matching_pr.
RESULTS = (
    ReleaseResult(PR657, "merged", MERGED, "agent"),
    ReleaseResult(SHA657, "merged", MERGED, "agent"),
    ReleaseResult(RequestId("pr", 606), "not_merged", None, None),
    ReleaseResult(RequestId("merge_sha", OTHER), "not_found", None, None),
    ReleaseResult(RequestId("merge_sha", HEAD), "no_matching_pr", None, None),
)


def _publish(path: Path, results: tuple[ReleaseResult, ...] = RESULTS) -> Path:
    header = build_header(
        repository=REPO,
        scope=[r.request for r in results],
        policy_version=1,
        policy_digest_value="sha256:" + "0" * 64,
        lease_seconds=86400,
        now=NOW,
    )
    publish(path, header, results, schema_version=SCHEMA_VERSION_V3, result_record=release_record)
    return path


def _records(path: Path) -> list[Record]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write(path: Path, records: list[Record]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def _load(path: Path, *, now: datetime = NOW, repo: str = REPO) -> ApprovalFactsV3:
    return load_release_facts(path, expected_repository=repo, now=now)


def _mutated(tmp_path: Path, mutate: Callable[[list[Record]], object]) -> Path:
    records = _records(_publish(tmp_path / "good.jsonl"))
    mutate(records)
    return _write(tmp_path / "bad.jsonl", records)


def _rejects(tmp_path: Path, mutate: Callable[[list[Record]], object], match: str) -> None:
    with pytest.raises(UnreadableFacts, match=match):
        _load(_mutated(tmp_path, mutate))


def test_reader_roundtrip(tmp_path: Path) -> None:
    facts = _load(_publish(tmp_path / "facts.jsonl"))
    assert facts.results == RESULTS
    assert facts.by_merge_sha()[MERGE].delivery.in_base is True  # type: ignore[union-attr]
    assert set(facts.by_merge_sha()) == {MERGE}, "записи без доставки в индекс не попадают"
    assert facts.scope_has_sha(MERGE) and not facts.scope_has_sha(OTHER[:-1] + "2")
    assert facts.header.repository == REPO and facts.header.scope == tuple(
        r.request for r in RESULTS
    )


def test_reader_roundtrip_actor_unavailable(tmp_path: Path) -> None:
    results = (ReleaseResult(PR657, "actor_unavailable", NO_MERGER, None),)
    facts = _load(_publish(tmp_path / "facts.jsonl", results))
    assert facts.results == results
    assert facts.by_merge_sha()[MERGE].state == "actor_unavailable"


# --- матрица полей §6 (инвариант 7) ----------------------------------------------------------


def test_reader_rejects_delivery_on_negative_state(tmp_path: Path) -> None:
    def mutate(rs: list[Record]) -> None:
        rs[3]["delivery"] = rs[1]["delivery"]

    _rejects(tmp_path, mutate, "delivery")


def test_reader_rejects_missing_merged_by_on_merged(tmp_path: Path) -> None:
    def mutate(rs: list[Record]) -> None:
        rs[1]["delivery"]["merged_by"] = None

    _rejects(tmp_path, mutate, "merged_by")


def _set(line: int, *path_value: Any) -> Callable[[list[Record]], None]:
    *path, value = path_value

    def mutate(rs: list[Record]) -> None:
        node = rs[line]
        for step in path[:-1]:
            node = node[step]
        node[path[-1]] = value

    return mutate


def _drop(line: int, *path: str) -> Callable[[list[Record]], None]:
    def mutate(rs: list[Record]) -> None:
        node = rs[line]
        for step in path[:-1]:
            node = node[step]
        del node[path[-1]]

    return mutate


@pytest.mark.parametrize(
    "mutate,match",
    [
        (_drop(3, "delivery"), "delivery"),
        (_drop(1, "delivery"), "delivery"),
        (_set(1, "delivery", None), "delivery"),
        (_set(1, "extra", 1), "полей"),
        (_set(1, "delivery", "merged", False), "merged"),
        (_set(1, "delivery", "merged", 1), "merged"),
        (
            _set(
                1,
                "delivery",
                "paths",
                [{"path": "x", "blob_at_merge": None, "blob_at_head": MERGE}],
            ),
            "paths",
        ),
        (_set(1, "delivery", "complete", False), "complete"),
        (_set(1, "delivery", "extra", 1), "delivery"),
        (_drop(1, "delivery", "in_base"), "delivery"),
        (_set(1, "delivery", "in_base", "yes"), "in_base"),
        (_set(1, "delivery", "merge_commit", MERGE.upper()), "merge_commit"),
        (_set(1, "delivery", "head_at_merge", None), "head_at_merge"),
        (_set(1, "delivery", "merged_at", "2026-10-30T12:09:17+00:00"), "merged_at"),
        (_set(1, "delivery", "merged_at", "2026-13-30T12:09:17Z"), "merged_at"),
        (_set(1, "delivery", "merged_by", "extra", 1), "merged_by"),
        (_set(1, "delivery", "merged_by", "identity", "ai-prosto"), "identity"),
        (_set(1, "delivery", "merged_by", "identity", "github:ghost"), "identity"),
        (_set(1, "delivery", "merged_by", "identity", "github:x[bot]"), "identity"),
        (_set(1, "delivery", "merged_by", "type_hint", "Mannequin"), "type_hint"),
        (_set(1, "delivery", "merged_by", "actor_class", None), "actor_class"),
        (_set(1, "delivery", "merged_by", "actor_class", "robot"), "actor_class"),
        (_set(1, "delivery", "merged_by", "actor_class", []), "actor_class"),
        (_set(1, "delivery", "merged_by", "actor_class", {}), "actor_class"),
        (_set(2, "delivery", "merge_commit", OTHER), "merge_commit"),
        (_set(3, "request", {"kind": "merge_sha", "value": OTHER[:-1] + "3"}), "not_merged"),
        (_set(5, "request", {"kind": "pr", "value": 5}), "no_matching_pr"),
        (_set(1, "state", "merged-ish"), "состояние"),
        (_set(1, "kind", "row"), "kind"),
    ],
    ids=[
        "negative_without_delivery_key",
        "merged_without_delivery_key",
        "merged_delivery_null",
        "extra_result_key",
        "merged_delivery_not_merged",
        "merged_not_bool",
        "paths_not_empty",
        "delivery_not_complete",
        "extra_delivery_key",
        "missing_delivery_key",
        "in_base_not_bool",
        "merge_commit_not_hex",
        "head_at_merge_null",
        "merged_at_not_canonical",
        "merged_at_not_a_date",
        "extra_merged_by_key",
        "identity_without_prefix",
        "identity_ghost",
        "identity_bot_suffix",
        "type_hint_outside_vocabulary",
        "merged_actor_class_null",
        "actor_class_outside_vocabulary",
        "actor_class_unhashable_list",
        "actor_class_unhashable_object",
        "merge_sha_request_other_merge_commit",
        "not_merged_for_merge_sha",
        "no_matching_pr_for_pr",
        "unknown_state",
        "unknown_kind",
    ],
)
def test_reader_rejects_field_matrix_violation(
    tmp_path: Path, mutate: Callable[[list[Record]], None], match: str
) -> None:
    _rejects(tmp_path, mutate, match)


def test_reader_rejects_merged_by_on_actor_unavailable(tmp_path: Path) -> None:
    results = (ReleaseResult(PR657, "actor_unavailable", NO_MERGER, None),)
    records = _records(_publish(tmp_path / "good.jsonl", results))
    records[1]["delivery"]["merged_by"] = {
        "identity": "github:ai-prosto",
        "type_hint": "User",
        "actor_class": None,
    }
    with pytest.raises(UnreadableFacts, match="merged_by"):
        _load(_write(tmp_path / "bad.jsonl", records))


# --- инварианты 1–6, 8–11 v2, перенесённые без изменения смысла ------------------------------


def _header_set(key: str, value: Any) -> Callable[[list[Record]], None]:
    return _set(0, key, value)


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda rs: rs.insert(1, dict(rs[0])), "ровно один"),
        (lambda rs: rs.append(rs.pop(0)), "первой"),
        (_header_set("schema_version", "2"), "schema_version"),
        (_header_set("complete", False), "complete"),
        (_header_set("repository", "devtools"), "owner/repo"),
        (_header_set("policy_version", 0), "policy_version"),
        (_header_set("policy_digest", "sha256:x"), "policy_digest"),
        (_header_set("scope", []), "пуст"),
        (_header_set("generated_at", "2026-10-31T09:00:00+00:00"), "generated_at"),
        (_header_set("valid_until", "2026-10-31T09:00:00Z"), "valid_until"),
        (_header_set("valid_until", "2026-12-31T09:00:00Z"), "lease"),
        (_header_set("generated_at", "2026-10-31T09:10:00Z"), "будущем"),
        (_header_set("scope_sha256", "sha256:" + "0" * 64), "scope_sha256"),
        (_header_set("extra", 1), "набор полей"),
        (lambda rs: rs.pop(3), "не хватает"),
        (lambda rs: rs.append(dict(rs[3])), "повторный"),
        (_set(4, "request", {"kind": "merge_sha", "value": OTHER[:-1] + "9"}), "незаявленный"),
        (lambda rs: rs[0]["scope"].append(rs[0]["scope"][0]), "дублир"),
    ],
    ids=[
        "inv1_two_headers",
        "inv1_header_not_first",
        "schema_version",
        "complete",
        "inv11_repository_shape",
        "policy_version",
        "policy_digest",
        "inv2_empty_scope",
        "inv8_generated_at",
        "inv10_valid_until_not_after",
        "inv10_lease_too_long",
        "inv10_clock_skew",
        "scope_sha256",
        "header_extra_key",
        "inv3_missing_result",
        "inv5_duplicate_result",
        "inv4_result_outside_scope",
        "inv2_duplicate_scope",
    ],
)
def test_reader_rejects_header_and_bijection(
    tmp_path: Path, mutate: Callable[[list[Record]], object], match: str
) -> None:
    _rejects(tmp_path, mutate, match)


def test_reader_rejects_repository_mismatch(tmp_path: Path) -> None:
    with pytest.raises(UnreadableFacts, match="не совпадает"):
        _load(_publish(tmp_path / "facts.jsonl"), repo="andrei-shtanakov/steward")


@pytest.mark.parametrize(
    "twin",
    [
        ReleaseResult(SHA657, "actor_unavailable", NO_MERGER, None),
        ReleaseResult(SHA657, "merged", MERGED, "human"),
        ReleaseResult(SHA657, "merged", replace(MERGED, in_base=False), "agent"),
        ReleaseResult(SHA657, "merged", replace(MERGED, head_at_merge=OTHER), "agent"),
    ],
    ids=["state", "actor_class", "in_base", "head_at_merge"],
)
def test_reader_rejects_contradicting_aliases(tmp_path: Path, twin: ReleaseResult) -> None:
    """Инвариант 9: алиасы одного мержа сравниваются записью без `request` — состоянием,
    доставкой и `actor_class`; `by_merge_sha()` иначе выбрал бы одного из них молча."""
    results = (ReleaseResult(PR657, "merged", MERGED, "agent"), twin)
    with pytest.raises(UnreadableFacts, match="алиасы"):
        _load(_publish(tmp_path / "facts.jsonl", results))


@pytest.mark.parametrize(
    "content",
    ["", "not json\n", "[1]\n"],
    ids=["empty", "not_json", "not_object"],
)
def test_reader_rejects_unparseable(tmp_path: Path, content: str) -> None:
    path = tmp_path / "facts.jsonl"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(UnreadableFacts):
        _load(path)


def test_reader_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(UnreadableFacts):
        _load(tmp_path / "absent.jsonl")


# --- распознавание v2 и v1 -------------------------------------------------------------------


def _v2_file(path: Path) -> Path:
    scope = [RequestId("pr", 42)]
    header = Header(
        repository=REPO,
        generated_at=NOW,
        valid_until=NOW + timedelta(days=1),
        policy_version=1,
        policy_digest="sha256:" + "0" * 64,
        scope=tuple(scope),
        scope_sha256=scope_digest(scope),
    )
    publish(path, header, [Result(scope[0], "not_merged")])
    return path


def test_detect_legacy_v2(tmp_path: Path) -> None:
    v2 = _v2_file(tmp_path / "v2.jsonl")
    assert detect_legacy_v2(v2) is True
    assert detect_legacy_v2(_publish(tmp_path / "v3.jsonl")) is False
    garbage = tmp_path / "garbage.jsonl"
    garbage.write_text("{not json\n", encoding="utf-8")
    assert detect_legacy_v2(garbage) is False
    assert detect_legacy_v2(tmp_path / "absent.jsonl") is False
    with pytest.raises(UnreadableFacts, match="перевыпустите"):
        _load(v2)


def test_reader_rejects_legacy_v1(tmp_path: Path) -> None:
    path = tmp_path / "v1.jsonl"
    path.write_text('{"schema": "approval-facts/v1"}\n', encoding="utf-8")
    with pytest.raises(UnreadableFacts, match="v1"):
        _load(path)
