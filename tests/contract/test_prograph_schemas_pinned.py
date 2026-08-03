"""Copy-integrity guarantee for the vendored prograph schemas (offline PR-gate).

Upstream-drift is the OTHER guarantee — scheduled observation outside PR CI
(two-guarantees rule); this test must never call out to the sibling repo.
"""

import hashlib
import json
from pathlib import Path

import pytest

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
VENDORED = [
    CONTRACTS / "prograph-intended-graph" / "v1",
    CONTRACTS / "prograph-conformance-report" / "v1",
]


def _pinned_sha(pin_text: str) -> str:
    for line in pin_text.splitlines():
        if line.startswith("sha256:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("PIN file has no sha256: line")


@pytest.mark.parametrize("vdir", VENDORED, ids=lambda p: p.parent.name)
def test_vendored_schema_matches_pin(vdir: Path) -> None:
    schema_bytes = (vdir / "schema.json").read_bytes()
    assert hashlib.sha256(schema_bytes).hexdigest() == _pinned_sha(
        (vdir / "PIN").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("vdir", VENDORED, ids=lambda p: p.parent.name)
def test_vendored_schema_is_valid_json_schema(vdir: Path) -> None:
    import jsonschema

    schema = json.loads((vdir / "schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
