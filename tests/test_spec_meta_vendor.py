"""Round-trip tests for the vendored SpecMeta v2 core (TODO §2 revendor-specmeta-v2).

These pin the two contracts the re-vendor depends on: (1) steward's own
governance fields (``traces_to``, ``upstream_hashes``, ``reviewer_roles``,
``allowed_approver_roles``) are NOT part of spec-runner's canonical set and
therefore survive ``meta_from_dict`` as verbatim pass-through in
``SpecMeta.extra`` — steward's own parsers in ``meta.py`` keep reading them
from the raw frontmatter dict directly, unaffected by the vendor bump; and
(2) ``owner_role`` is first-class canonical in v2, validated only as
"string or null" (DEC-007 semantics live in ``meta.py``, not here).
"""

from __future__ import annotations

import pytest

from steward._vendor.spec_meta import SpecMeta, SpecMetaError, meta_from_dict

_GOVERNANCE_ONLY_FIELDS = {
    "traces_to": ["requirements"],
    "upstream_hashes": {"requirements": "abc123"},
    "reviewer_roles": ["qa"],
    "allowed_approver_roles": ["qa", "product"],
}


def test_governance_fields_survive_as_extra_pass_through() -> None:
    d = {"spec_stage": "design", **_GOVERNANCE_ONLY_FIELDS}
    meta = meta_from_dict(d)
    assert isinstance(meta, SpecMeta)
    for key, value in _GOVERNANCE_ONLY_FIELDS.items():
        assert meta.extra[key] == value, f"{key} did not survive as pass-through"
    # Not silently promoted to canonical attributes.
    assert not hasattr(meta, "traces_to")
    assert not hasattr(meta, "upstream_hashes")


def test_owner_role_is_first_class_canonical() -> None:
    meta = meta_from_dict({"spec_stage": "design", "owner_role": "product"})
    assert meta.owner_role == "product"
    assert "owner_role" not in meta.extra


def test_owner_role_absent_defaults_to_none() -> None:
    meta = meta_from_dict({"spec_stage": "design"})
    assert meta.owner_role is None


def test_owner_role_legacy_at_form_passes_through_unvalidated() -> None:
    """v2 only checks "string or null" — the DEC-007 slug grammar is meta.py's job."""
    meta = meta_from_dict({"spec_stage": "design", "owner_role": "@product,@qa"})
    assert meta.owner_role == "@product,@qa"


def test_non_string_owner_role_raises_spec_meta_error() -> None:
    with pytest.raises(SpecMetaError, match="owner_role"):
        meta_from_dict({"spec_stage": "design", "owner_role": 7})


@pytest.mark.parametrize("bad_status", ["approved-ish", "", "APPROVED", None])
def test_invalid_status_value_raises_spec_meta_error(bad_status: object) -> None:
    with pytest.raises(SpecMetaError, match="status"):
        meta_from_dict({"spec_stage": "design", "status": bad_status})


def test_bool_version_rejected_despite_being_an_int_subclass() -> None:
    with pytest.raises(SpecMetaError, match="version"):
        meta_from_dict({"spec_stage": "design", "version": True})


def test_unquoted_date_scalar_normalized_to_string() -> None:
    """A hand-written `generated_at: 2026-07-05` parses as datetime.date via
    YAML; the v2 coercion normalizes it to a string (design §3.3) — every
    real spec/*.md file in this repo relies on exactly this normalization."""
    import datetime

    meta = meta_from_dict({"spec_stage": "design", "generated_at": datetime.date(2026, 7, 5)})
    assert meta.generated_at == "2026-07-05"
    assert isinstance(meta.generated_at, str)


def test_missing_spec_stage_raises_spec_meta_error() -> None:
    with pytest.raises(SpecMetaError, match="spec_stage"):
        meta_from_dict({"status": "draft"})


def test_non_string_key_raises_spec_meta_error() -> None:
    with pytest.raises(SpecMetaError, match="not a string"):
        meta_from_dict({"spec_stage": "design", 7: "value"})
