"""Shared test fixtures (DEC-007: roles.yaml is a mandatory profile sibling).

pytest auto-discovers ``conftest.py`` for every test module below it in the
directory tree, so ``tests/gatecheck/`` and ``tests/verdicts/`` both pick up
the ``write_roles`` fixture below by name — no import statement needed
(pyrefly has no configured import root for the top-level ``tests`` package,
and this repo's own convention is no cross-test-module imports; fixture
injection sidesteps both).
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Wide-open pattern + every slug the legacy test profiles mention (after '@'
# strip). Tests that need a NARROW catalog write their own file instead.
DEFAULT_ROLES = (
    "version: 1\n"
    'slug_pattern: "^[a-z][a-z0-9-]{1,31}$"\n'
    "roles:\n"
    "  - {slug: product, display: Product}\n"
    "  - {slug: architects, display: Architecture}\n"
    "  - {slug: qa, display: QA}\n"
    "  - {slug: tech-lead, display: Tech lead}\n"
    "  - {slug: stream-owner, display: Workstream owner}\n"
    "  - {slug: owner, display: Solo owner}\n"
)


@pytest.fixture
def write_roles(tmp_path: Path) -> Path:
    """Drop a permissive roles.yaml into ``tmp_path`` (mandatory profile sibling).

    Tests that write a profile directly into ``tmp_path`` just need to request
    this fixture by name; it writes before the test body runs. Tests whose
    profile lives in a different directory, or that need a NARROW catalog,
    write their own ``roles.yaml`` instead of requesting this fixture.
    """
    path = tmp_path / "roles.yaml"
    path.write_text(DEFAULT_ROLES, encoding="utf-8")
    return path
