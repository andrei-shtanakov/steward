"""Фикстуры контракта проверяются против его же схемы.

Тест намеренно проверяет ОБА направления: валидные фикстуры проходят, а
невалидные — падают. Схема, которая ничего не отвергает, выглядит рабочей и
не является контрактом.
"""

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "approval-facts" / "v2"
SCHEMA = json.loads((CONTRACT / "SCHEMA.json").read_text(encoding="utf-8"))


def _records(name: str) -> list[dict]:
    text = (CONTRACT / "fixtures" / name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.mark.parametrize("fixture", ["clean.jsonl", "negative_states.jsonl"])
def test_valid_fixtures_conform(fixture: str) -> None:
    for record in _records(fixture):
        jsonschema.validate(record, SCHEMA)


@pytest.mark.parametrize(
    "fixture",
    ["bad_state_for_kind.jsonl", "extra_field_on_negative.jsonl"],
)
def test_invalid_fixtures_are_rejected(fixture: str) -> None:
    with pytest.raises(jsonschema.ValidationError):
        for record in _records(fixture):
            jsonschema.validate(record, SCHEMA)


def _canonical_scope_sha256(scope: list[dict]) -> str:
    """Independent re-implementation of the §4.4 scope_sha256 recipe.

    Deliberately duplicated rather than imported: the canonical function does
    not exist yet (it arrives in Task 2), and even once it exists this stays
    an independent oracle — importing it would let a bug in the canonical
    implementation validate itself.
    """
    normalized = sorted(scope, key=lambda item: (item["kind"], item["value"]))
    canonical_bytes = json.dumps(
        normalized,
        separators=(",", ":"),
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical_bytes).hexdigest()}"


@pytest.mark.parametrize("fixture", ["clean.jsonl", "negative_states.jsonl"])
def test_fixture_scope_sha256_is_real(fixture: str) -> None:
    """scope_sha256 in fixture headers must be a real digest, not a plausible fake.

    jsonschema only checks the hash's regex shape (`^sha256:[0-9a-f]{64}$`), so
    a fixture could declare a fake-but-well-formed digest and still pass. That
    would teach readers of this contract to trust the shape instead of the
    content it claims to summarize.
    """
    header = _records(fixture)[0]
    assert header["kind"] == "header"
    assert header["scope_sha256"] == _canonical_scope_sha256(header["scope"])
