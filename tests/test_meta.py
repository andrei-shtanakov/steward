"""Tests for artifact metadata parsing (WS-001, REQ-002)."""

from pathlib import Path

import pytest

from steward.meta import (
    ArtifactMeta,
    MetaError,
    load_artifact,
    parse_artifact,
    parse_owner_roles,
)

SPEC_DIR = Path(__file__).resolve().parent.parent / "spec"

MANAGED = """---
spec_stage: design
status: approved
version: 2
owner_role: "@architects,@product"
generated_by: claude@claude-opus-4-8
approved_by: alice
traces_to: [REQ-001, DEC-002]
---

# Design

body text
"""


def test_parse_managed_artifact_base_fields() -> None:
    meta = parse_artifact(MANAGED)
    assert isinstance(meta, ArtifactMeta)
    assert meta.spec_stage == "design"
    assert meta.status == "approved"
    assert meta.version == 2
    assert meta.approved_by == "alice"


def test_owner_roles_split_and_stripped() -> None:
    meta = parse_artifact(MANAGED)
    assert meta is not None
    assert meta.owner_roles == ("@architects", "@product")


def test_traces_to_parsed() -> None:
    meta = parse_artifact(MANAGED)
    assert meta is not None
    assert meta.traces_to == ("REQ-001", "DEC-002")


def test_owner_role_and_traces_absent_are_empty() -> None:
    meta = parse_artifact("---\nspec_stage: charter\n---\nbody\n")
    assert meta is not None
    assert meta.owner_roles == ()
    assert meta.traces_to == ()


def test_no_frontmatter_is_unmanaged() -> None:
    assert parse_artifact("# just markdown\n") is None


def test_frontmatter_without_spec_stage_is_unmanaged() -> None:
    assert parse_artifact("---\ntitle: notes\n---\nbody\n") is None


def test_empty_spec_stage_is_unmanaged() -> None:
    assert parse_artifact("---\nspec_stage:\n---\nbody\n") is None


def test_malformed_traces_to_raises() -> None:
    text = "---\nspec_stage: design\ntraces_to: REQ-001\n---\nbody\n"
    with pytest.raises(MetaError, match="traces_to"):
        parse_artifact(text)


def test_broken_yaml_frontmatter_raises() -> None:
    # Starts a frontmatter block but the YAML is malformed — must not slip
    # through as "unmanaged" and bypass governance.
    text = "---\nspec_stage: design\n  bad: : :\n---\nbody\n"
    with pytest.raises(MetaError, match="frontmatter"):
        parse_artifact(text)


def test_unterminated_frontmatter_raises() -> None:
    text = "---\nspec_stage: design\nno closing delimiter\n"
    with pytest.raises(MetaError, match="frontmatter"):
        parse_artifact(text)


def test_non_string_spec_stage_raises() -> None:
    text = "---\nspec_stage: 123\n---\nbody\n"
    with pytest.raises(MetaError, match="spec_stage"):
        parse_artifact(text)


def test_whitespace_only_trace_id_raises() -> None:
    text = "---\nspec_stage: design\ntraces_to: ['   ']\n---\nbody\n"
    with pytest.raises(MetaError, match="traces_to"):
        parse_artifact(text)


def test_trace_ids_are_stripped() -> None:
    text = "---\nspec_stage: design\ntraces_to: [' REQ-001 ', 'DEC-002']\n---\nbody\n"
    meta = parse_artifact(text)
    assert meta is not None
    assert meta.traces_to == ("REQ-001", "DEC-002")


def test_unknown_frontmatter_keys_ignored() -> None:
    text = "---\nspec_stage: design\nbogus_key: 1\n---\nbody\n"
    meta = parse_artifact(text)
    assert meta is not None
    assert meta.spec_stage == "design"


def test_parse_owner_roles_helper() -> None:
    assert parse_owner_roles("@a, @b ,@c") == ("@a", "@b", "@c")
    assert parse_owner_roles("@solo") == ("@solo",)
    assert parse_owner_roles("") == ()
    assert parse_owner_roles(None) == ()


def test_load_real_dogfood_design_spec() -> None:
    meta = load_artifact(SPEC_DIR / "20-design.md")
    assert meta is not None
    assert meta.spec_stage == "design"
    assert meta.owner_roles == ("@architects",)
    assert "REQ-001" in meta.traces_to


def test_load_real_dogfood_requirements_multi_owner() -> None:
    meta = load_artifact(SPEC_DIR / "10-requirements.md")
    assert meta is not None
    assert meta.owner_roles == ("@product", "@architects")


def test_all_dogfood_specs_are_managed() -> None:
    for path in sorted(SPEC_DIR.glob("*.md")):
        if path.name.startswith("maestro-"):
            # Maestro-injected task artifacts (git-excluded via info/exclude),
            # spec-runner format without steward frontmatter — not dogfood specs.
            continue
        meta = load_artifact(path)
        assert meta is not None, f"{path.name} should be managed"
        assert meta.spec_stage
