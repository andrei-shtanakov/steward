"""Фикстуры контракта проверяются против его же схемы.

Тест намеренно проверяет ОБА направления: валидные фикстуры проходят, а
невалидные — падают. Схема, которая ничего не отвергает, выглядит рабочей и
не является контрактом.
"""

import hashlib
import json
import re
from pathlib import Path

import jsonschema
import pytest

from steward.approvalfacts.model import MAX_CLOCK_SKEW_SECONDS, MAX_LEASE_SECONDS

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "approval-facts" / "v2"
SCHEMA = json.loads((CONTRACT / "SCHEMA.json").read_text(encoding="utf-8"))
README = (CONTRACT / "README.md").read_text(encoding="utf-8")


def _records(name: str) -> list[dict]:
    text = (CONTRACT / "fixtures" / name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.mark.parametrize("fixture", ["clean.jsonl", "negative_states.jsonl"])
def test_valid_fixtures_conform(fixture: str) -> None:
    for record in _records(fixture):
        jsonschema.validate(record, SCHEMA)


@pytest.mark.parametrize(
    "fixture",
    [
        "bad_state_for_kind.jsonl",
        "extra_field_on_negative.jsonl",
        "bad_timestamp_format.jsonl",
        "missing_merge_sha_negative.jsonl",
    ],
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


def test_readme_normative_constants_match_model() -> None:
    """§4.3(10): the clock-skew tolerance and the lease bound are promised as
    fixed "normatively... in SCHEMA.json/README.md contract". SCHEMA.json does
    not carry either value (it cannot express a numeric bound on a duration
    derived from two other fields), so README.md's prose is the only place a
    stranger vendoring this contract can read them from. Nothing previously
    tied that prose to `model.py`'s literals — changing
    `MAX_CLOCK_SKEW_SECONDS`/`MAX_LEASE_SECONDS` here would silently leave the
    published contract stating the old numbers while steward enforces new
    ones. This test reads the numbers straight out of the README's own prose
    (not a second hardcoded copy) so it fails the moment either side moves
    without the other.
    """
    skew_match = re.search(
        r"допуск часов на будущее \(инвариант 10\) — \*\*(\d+) секунд\*\*", README
    )
    assert skew_match is not None, "README no longer states the clock-skew tolerance as expected"
    readme_skew = int(skew_match.group(1))

    lease_match = re.search(
        r"верхняя граница заявленной длительности.*?— \*\*([\d ]+) секунд", README, re.DOTALL
    )
    assert lease_match is not None, "README no longer states the lease bound as expected"
    readme_lease = int(lease_match.group(1).replace(" ", "").replace("\xa0", ""))

    assert readme_skew == MAX_CLOCK_SKEW_SECONDS
    assert readme_lease == MAX_LEASE_SECONDS
