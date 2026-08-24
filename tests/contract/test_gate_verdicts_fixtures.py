"""Contract fixtures of gate-verdicts/v1: each negative breaks exactly its class.

The fixtures are canon for both sides: producer tests validate emitter output
against the same schema; the consumer (dispatcher) vendors this directory and
drives its classifier with these files.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

V1 = Path(__file__).resolve().parents[2] / "contracts" / "gate-verdicts" / "v1"
SCHEMA = json.loads((V1 / "SCHEMA.json").read_text())
FIXTURES = V1 / "fixtures"


def _lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text().splitlines()


@pytest.mark.parametrize(
    "name",
    [
        "clean.jsonl",
        "findings.jsonl",
        "dangling_artifact.jsonl",
        "chained.jsonl",
        "broken_chain.jsonl",  # schema-silent on purpose: the chain verifier sees it
    ],
)
def test_schema_valid_fixtures(name: str) -> None:
    records = [json.loads(line) for line in _lines(name)]
    for record in records:
        jsonschema.validate(record, SCHEMA)
    assert records[0]["kind"] == "header"  # line 1 is always the header


def test_clean_has_no_findings() -> None:
    kinds = [json.loads(line)["kind"] for line in _lines("clean.jsonl")]
    assert "finding" not in kinds and "artifact" in kinds


def test_findings_fixture_covers_fail_and_warn() -> None:
    verdicts = {
        json.loads(line)["verdict"]
        for line in _lines("findings.jsonl")
        if json.loads(line)["kind"] == "finding"
    }
    assert verdicts == {"fail", "warn"}


def test_malformed_line_breaks_json_not_schema() -> None:
    lines = _lines("malformed_line.jsonl")
    for line in lines[:2]:  # prefix is valid — the break is mid-file (BEH-03)
        jsonschema.validate(json.loads(line), SCHEMA)
    with pytest.raises(json.JSONDecodeError):
        json.loads(lines[2])


def test_future_schema_header_fails_validation() -> None:
    header = json.loads(_lines("future_schema.jsonl")[0])
    assert header["schema_version"] == "99"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(header, SCHEMA)


def test_dangling_artifact_is_semantic_not_schematic() -> None:
    # BEH-06: schema-valid on purpose — the defect is a finding pointing outside
    # the artifact inventory, which only the reader's resolver can see.
    records = [json.loads(line) for line in _lines("dangling_artifact.jsonl")]
    inventory = {r["path"] for r in records if r["kind"] == "artifact"}
    dangling = [r for r in records if r["kind"] == "finding" and r["artifact"] not in inventory]
    assert len(dangling) == 1


def test_chained_fixture_verifies_and_legacy_fixtures_stay_legacy() -> None:
    from steward.verdicts.chain import verify_chain

    assert verify_chain((FIXTURES / "chained.jsonl").read_text()).status == "chained"
    # Files predating prev_hash stay valid — the additive rule, verbatim.
    assert verify_chain((FIXTURES / "clean.jsonl").read_text()).status == "legacy"
    assert verify_chain((FIXTURES / "findings.jsonl").read_text()).status == "legacy"


def test_broken_chain_fixture_is_schema_silent_but_verifier_red() -> None:
    from steward.verdicts.chain import verify_chain

    report = verify_chain((FIXTURES / "broken_chain.jsonl").read_text())
    assert report.status == "broken"
    assert report.broken_line == 5  # line 4 was substituted; its successor exposes it
