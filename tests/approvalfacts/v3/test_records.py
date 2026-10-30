"""Записи контракта `approval-facts/v3` (спека steward §4) и `SCHEMA.json`.

Записи — сериализация тех же фактов, что возвращает библиотека: факты берутся прогоном
закоммиченных векторов (`clean_approve_app_merge`, `not_merged`, история), а не собираются
руками. Схема — draft 2020-12, `additionalProperties: false` у каждого объекта.
"""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

from steward.approvalfacts.v3 import (
    Delivery,
    HistoryViolation,
    ReviewFacts,
    Unavailable,
    read_act_facts,
    read_policy_history,
    unavailable,
    violation,
)
from steward.approvalfacts.v3.records import (
    delivery_record,
    policy_history_record,
    review_record,
)
from steward.approvalfacts.v3.transport import replay_transport

Record = dict[str, Any]

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "contracts" / "approval-facts" / "v3"
SCHEMA = json.loads((CONTRACT / "SCHEMA.json").read_text(encoding="utf-8"))
OBSERVED = "2026-10-31T09:00:00Z"
DEFS = ("review_record", "delivery_record", "policy_history_record", "header", "result")


def _vector(name: str) -> Record:
    return json.loads((CONTRACT / "vectors" / name).read_text(encoding="utf-8"))


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{name}"})


def _act(name: str) -> tuple[Record, Delivery, ReviewFacts | None]:
    spec = _vector(name)
    got = read_act_facts(
        replay_transport(spec["transport"]), spec["repo"], spec["pr"], paths=spec["paths"]
    )
    assert not isinstance(got, Unavailable), got
    return spec, got[0], got[1]


def _history(name: str) -> tuple[Record, object]:
    spec = _vector(name)
    got = read_policy_history(
        replay_transport(spec["transport"]),
        spec["repo"],
        spec["branch"],
        anchor=spec["anchor"],
        until=spec["until"],
        files=tuple(spec["files"]),
    )
    return spec, got


@pytest.fixture
def clean() -> tuple[Record, Delivery, ReviewFacts]:
    spec, delivery, facts = _act("clean_approve_app_merge.json")
    assert facts is not None
    return spec, delivery, facts


# --- схема ----------------------------------------------------------------------------------


def test_schema_is_draft_2020_12_with_all_defs():
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(DEFS) <= set(SCHEMA["$defs"])


def _objects(node: object):
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            yield node
        for value in node.values():
            yield from _objects(value)
    elif isinstance(node, list):
        for value in node:
            yield from _objects(value)


def test_every_object_schema_is_closed():
    """Объект — закрытый набор ключей; единственный словарь (`files_sha256`: путь → sha256)
    ограничивает и имена (`propertyNames`), и значения."""
    for obj in _objects(SCHEMA["$defs"]):
        if obj.get("type") != "object":
            continue
        if "propertyNames" in obj:
            assert isinstance(obj["additionalProperties"], dict) and "properties" not in obj
        else:
            assert obj.get("additionalProperties") is False, obj


# --- review ---------------------------------------------------------------------------------


def test_review_record_validates(clean):
    spec, _, facts = clean
    record = review_record(facts, observed_at=OBSERVED, t=spec["t"], before=frozenset())
    _validator("review_record").validate(record)
    assert record["complete"] is True and record["observed_at"] == OBSERVED
    assert [(e["login"], e["state"]) for e in record["effective"]] == [("alice", "APPROVED")]
    assert {r["login"] for r in record["reviews"]} == {"alice", "bob"}
    assert record["head_sha"] == facts.head_sha and record["dismissals"] == []


def test_review_record_extra_key_rejected(clean):
    spec, _, facts = clean
    record = review_record(facts, observed_at=OBSERVED, t=spec["t"], before=frozenset())
    record["reviews"][0]["body"] = "lgtm"
    with pytest.raises(ValidationError):
        _validator("review_record").validate(record)


def test_review_record_with_dismissal_validates():
    spec, _, facts = _act("dismissal_with_previous_state_twin.json")
    assert facts is not None
    record = review_record(facts, observed_at=OBSERVED, t=spec["t"], before=frozenset())
    _validator("review_record").validate(record)
    assert record["dismissals"][0]["previous_state"] == "CHANGES_REQUESTED"
    assert {e["login"]: e["state"] for e in record["effective"]} == {
        "alice": "APPROVED",
        "bob": "DISMISSED",
    }


def test_review_record_honours_before():
    spec, _, facts = _act("equal_stamp_approve_before.json")
    assert facts is not None
    before = frozenset((k[0], k[1]) for k in spec["before"])
    with_key = review_record(facts, observed_at=OBSERVED, t=spec["t"], before=before)
    without = review_record(facts, observed_at=OBSERVED, t=spec["t"], before=frozenset())
    assert [e["login"] for e in with_key["effective"]] == ["alice"]
    assert without["effective"] == []


@pytest.mark.parametrize("observed", ["2026-10-31T09:00:00.5Z", "2026-10-31 09:00:00", ""])
def test_review_record_observed_at_must_be_canonical(clean, observed):
    spec, _, facts = clean
    with pytest.raises(ValueError):
        review_record(facts, observed_at=observed, t=spec["t"], before=frozenset())


def test_review_record_refuses_incomplete_facts(clean):
    """Факты, собранные вызывающим с отказом §3.5, не становятся записью `complete`."""
    spec, _, facts = clean
    unsubmitted = replace(facts.reviews[0], submitted_at=None)
    broken = replace(facts, reviews=(unsubmitted, *facts.reviews[1:]))
    with pytest.raises(ValueError, match="missing_submitted_at"):
        review_record(broken, observed_at=OBSERVED, t=spec["t"], before=frozenset())


# --- delivery -------------------------------------------------------------------------------


def test_delivery_record_validates(clean):
    _, delivery, _ = clean
    record = delivery_record(delivery, actor_class="agent")
    _validator("delivery_record").validate(record)
    assert record["merged_by"] == {
        "identity": "github:df-reviewer",
        "type_hint": "Bot",
        "actor_class": "agent",
    }
    assert record["paths"] == [
        {"path": p.path, "blob_at_merge": p.blob_at_merge, "blob_at_head": p.blob_at_head}
        for p in delivery.paths
    ]
    assert record["complete"] is True and record["in_base"] is True


def test_delivery_record_unclassified_merger(clean):
    """Классифицирует потребитель (спека §2): `actor_class` библиотекой не вычисляется."""
    _, delivery, _ = clean
    record = delivery_record(delivery, actor_class=None)
    _validator("delivery_record").validate(record)
    assert record["merged_by"]["actor_class"] is None


def test_delivery_record_path_deleted_at_merge():
    _, delivery, _ = _act("paths_deleted_at_merge.json")
    record = delivery_record(delivery, actor_class=None)
    _validator("delivery_record").validate(record)
    assert record["paths"][0]["blob_at_merge"] is None


def test_delivery_record_not_merged():
    _, delivery, facts = _act("not_merged.json")
    assert facts is None
    record = delivery_record(delivery, actor_class=None)
    _validator("delivery_record").validate(record)
    assert record["merged"] is False and record["merged_by"] is None and record["paths"] == []
    with pytest.raises(ValueError):
        delivery_record(delivery, actor_class="human")


def test_delivery_record_bad_actor_class(clean):
    _, delivery, _ = clean
    with pytest.raises(ValueError):
        delivery_record(delivery, actor_class="robot")


def test_delivery_record_extra_key_rejected(clean):
    _, delivery, _ = clean
    record = delivery_record(delivery, actor_class="agent")
    record["merged_by"]["login"] = "df-reviewer"
    with pytest.raises(ValidationError):
        _validator("delivery_record").validate(record)


def test_delivery_record_merged_requires_merge_fields(clean):
    _, delivery, _ = clean
    record = delivery_record(delivery, actor_class="agent")
    record["merge_commit"] = None
    with pytest.raises(ValidationError):
        _validator("delivery_record").validate(record)


# --- policy_history -------------------------------------------------------------------------


def test_policy_history_record_activations():
    spec, got = _history("history/merge_squash_neutral.json")
    assert isinstance(got, tuple)
    record = policy_history_record(spec["repo"], spec["branch"], spec["anchor"], None, got)
    _validator("policy_history_record").validate(record)
    assert record["complete"] is True and record["until"] is None
    assert record["activations"] == spec["expect"]["activations"]


def test_policy_history_record_violation():
    spec, got = _history("history/direct_push.json")
    assert isinstance(got, HistoryViolation)
    record = policy_history_record(spec["repo"], spec["branch"], spec["anchor"], None, got)
    _validator("policy_history_record").validate(record)
    assert record["complete"] is True and record["violation"] == got.reason
    assert "activations" not in record


def test_policy_history_record_unavailable():
    spec, got = _history("history/tree_truncated.json")
    assert isinstance(got, Unavailable)
    record = policy_history_record(spec["repo"], spec["branch"], spec["anchor"], None, got)
    _validator("policy_history_record").validate(record)
    assert record["complete"] is False and record["unavailable"] == got.reason


@pytest.mark.parametrize(
    "got",
    [unavailable("http", "404 x"), violation("commit_without_pr", "abc")],
    ids=["unavailable", "violation"],
)
def test_policy_history_record_extra_key_rejected(got):
    record = policy_history_record("o/r", "main", "a" * 40, "b" * 40, got)
    _validator("policy_history_record").validate(record)
    record["activations"] = []
    with pytest.raises(ValidationError):
        _validator("policy_history_record").validate(record)


@pytest.mark.parametrize("anchor", ["main", "A" * 40, ""])
def test_policy_history_record_anchor_must_be_hex(anchor):
    with pytest.raises(ValueError):
        policy_history_record("o/r", "main", anchor, None, ())
