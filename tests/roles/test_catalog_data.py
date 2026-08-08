"""The real profiles/roles.yaml: composition pinned to its version (DEC-007 D5).

Deleting or adding a role without bumping `version` must fail here — the
same discipline as the gate-id catalog. Reference-resolution (profiles,
frontmatter, gate-catalog applicable_roles, role assignments) makes deleting
a USED role fail loudly in the respective loaders; this file guards the
version contract itself.
"""

from __future__ import annotations

from pathlib import Path

from steward.roles import load_roles_catalog

PROFILES = Path(__file__).resolve().parents[2] / "profiles"

# Baseline: the version this pin was recorded against. Composition changes
# require version > baseline AND updating EXPECTED_SLUGS in the same commit.
BASELINE_VERSION = 1
EXPECTED_SLUGS = frozenset({"product", "architects", "qa", "tech-lead", "stream-owner", "owner"})


def _catalog():
    return load_roles_catalog(PROFILES / "roles.yaml")


def test_real_catalog_loads_fail_closed() -> None:
    cat = _catalog()
    assert cat.version >= 1


def test_composition_change_requires_version_bump() -> None:
    cat = _catalog()
    if cat.slugs() == EXPECTED_SLUGS:
        assert cat.version == BASELINE_VERSION, (
            "composition unchanged but version moved — revert the bump or "
            "change the composition it announces"
        )
    else:
        assert cat.version > BASELINE_VERSION, (
            "roles composition changed without a version bump — bump "
            "`version` in profiles/roles.yaml AND update EXPECTED_SLUGS/"
            "BASELINE_VERSION here in the same commit"
        )


def test_version_never_below_baseline() -> None:
    assert _catalog().version >= BASELINE_VERSION
