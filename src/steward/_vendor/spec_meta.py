"""Vendored, pinned copy of spec-runner's SpecMeta frontmatter core.

SOURCE:   spec-runner/src/spec_runner/spec.py
SYMBOLS:  split_frontmatter, SpecMeta, SpecMetaError, meta_from_dict, canonical_fields
CONTRACT: SPEC_META_CONTRACT v2 (spec-runner C2 SpecMeta contract)
PIN:      spec-runner tag v2.22.0, commit de9a31c4ca07d065ae777b9e3c180464d58b2bf6

DO NOT EDIT to change behaviour. This is a hand-pinned copy kept byte-faithful
to the upstream so steward reuses the same state shape (DEC-003) without a
runtime dependency on the sibling repo. Re-vendor when SPEC_META_CONTRACT bumps.

Scope: only the read-side symbols steward consumes. spec-runner's own stage
profile system (StageDef/StageProfile/load_profile/...) and the write-side
(meta_to_dict/_render/write_spec) are out of scope — steward never writes
frontmatter (DEC-008: it is a validator, not a rewriter of generated
artifacts).

v2 adds first-class ``owner_role`` (DEC-007) and an ``extra`` dict that
carries every foreign frontmatter key verbatim — steward's own governance
fields (``traces_to``, ``upstream_hashes``, ``reviewer_roles``,
``allowed_approver_roles``) are NOT part of spec-runner's canonical set, so
they land in ``extra`` unchanged; ``meta.py`` continues to parse them from
the raw frontmatter dict directly (see ``tests/test_meta.py`` for the
pass-through pin). v2 also validates canonical fields against a fixed
matrix (``_coerce_canonical``) and can raise ``SpecMetaError`` — callers
must translate that into their own configuration-error contour rather than
letting it escape as a traceback.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field, fields
from typing import Any

import yaml

_FM_DELIM = "---"

SPEC_META_CONTRACT: int = 2


class SpecMetaError(Exception):
    """Raised when a managed spec's frontmatter cannot be parsed faithfully."""


@dataclass
class SpecMeta:
    """Frontmatter state for one spec document.

    ``extra`` holds foreign frontmatter keys verbatim so spec-runner is a
    lossless intermediary for extending layers (steward). It is an internal
    field, not a wire field: see :func:`canonical_fields`.
    """

    spec_stage: str
    status: str = "draft"  # draft | approved | stale
    version: int = 1
    generated_by: str = ""
    generated_at: str = ""
    source_prompt_version: str = ""
    validation: str = ""  # pass | fail | warn | ""
    approved_by: str | None = None
    approved_at: str | None = None
    # DEC-007 role slug (one accountable role, no @); steward owns the
    # semantics — legacy "@role[,@role]" values are carried verbatim.
    owner_role: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Copy: a caller-owned mapping must not mutate metadata via an alias.
        self.extra = dict(self.extra)


def split_frontmatter(text: str) -> tuple[dict | None, str]:
    """Split a leading ``---\\n...\\n---`` YAML block from the body.

    Returns ``(meta_dict, body)`` or ``(None, text)`` when no frontmatter.
    """
    if not text.startswith(_FM_DELIM + "\n"):
        return None, text
    end = text.find("\n" + _FM_DELIM, len(_FM_DELIM) + 1)
    if end == -1:
        return None, text
    raw = text[len(_FM_DELIM) + 1 : end]
    # Body starts after the closing delimiter's line.
    after = text.find("\n", end + 1)
    body = text[after + 1 :] if after != -1 else ""
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None, text
    if not isinstance(loaded, dict):
        return None, text
    return loaded, body


_STATUS_VALUES = frozenset({"draft", "approved", "stale"})
_STR_FIELDS = frozenset(
    {
        "spec_stage",
        "status",
        "generated_by",
        "source_prompt_version",
        "validation",
    }
)
_NULLABLE_STR_FIELDS = frozenset({"approved_by", "owner_role"})
#: Timestamp wire fields: accept YAML's native date scalars and normalize them
#: to a string, so a hand-written `generated_at: 2026-07-05` is not a hard
#: error. ``approved_at`` is additionally nullable.
_TIMESTAMP_FIELDS = frozenset({"generated_at", "approved_at"})
_NULLABLE_TIMESTAMP_FIELDS = frozenset({"approved_at"})


def canonical_fields() -> frozenset[str]:
    """Frontmatter (wire) field names: every SpecMeta field except ``extra``.

    Derived by subtraction so an internal dataclass field can never silently
    widen the wire contract.
    """
    return frozenset(f.name for f in fields(SpecMeta)) - {"extra"}


def _coerce_canonical(key: str, value: object) -> object:
    """Validate one canonical field against the v2 matrix, returning its value.

    Only the two timestamp fields change their value: YAML parses a bare
    ``2026-07-05`` into a ``datetime.date``, which is normalized here to a
    string so the next write canonicalizes the file (design §3.3).

    Raises:
        SpecMetaError: if the value violates the matrix.
    """
    if key in _TIMESTAMP_FIELDS:
        if value is None:
            if key in _NULLABLE_TIMESTAMP_FIELDS:
                return None
            raise SpecMetaError(f"frontmatter field {key!r} must not be null")
        if isinstance(value, str):
            return value
        # datetime BEFORE date: datetime.datetime subclasses datetime.date.
        if isinstance(value, datetime.datetime | datetime.date):
            return value.isoformat()
        raise SpecMetaError(
            f"frontmatter field {key!r} must be a string or a date, got {type(value).__name__}"
        )
    if key in _STR_FIELDS:
        if not isinstance(value, str):
            raise SpecMetaError(
                f"frontmatter field {key!r} must be a string, got {type(value).__name__}"
            )
        if key == "status" and value not in _STATUS_VALUES:
            raise SpecMetaError(
                f"frontmatter field 'status' must be one of {sorted(_STATUS_VALUES)}, got {value!r}"
            )
    elif key in _NULLABLE_STR_FIELDS:
        if value is not None and not isinstance(value, str):
            raise SpecMetaError(
                f"frontmatter field {key!r} must be a string or null, got {type(value).__name__}"
            )
    elif key == "version":
        # type() not isinstance(): isinstance(True, int) is True.
        if type(value) is not int:
            raise SpecMetaError(
                f"frontmatter field 'version' must be an integer, got {type(value).__name__}"
            )
    return value


def meta_from_dict(d: dict) -> SpecMeta:
    """Build a SpecMeta from a frontmatter dict.

    Canonical fields are validated against the v2 matrix. Unknown *string*
    keys are preserved verbatim (see ``SpecMeta.extra``). A non-string key
    raises, since it cannot be round-tripped faithfully.

    Raises:
        SpecMetaError: on a non-string key or a malformed canonical field.
    """
    canonical = canonical_fields()
    known: dict[str, object] = {}
    extra: dict[str, Any] = {}
    for key, value in d.items():
        if not isinstance(key, str):
            raise SpecMetaError(f"frontmatter key {key!r} is not a string")
        if key in canonical:
            known[key] = _coerce_canonical(key, value)
        else:
            extra[key] = value
    if "spec_stage" not in known:
        raise SpecMetaError("frontmatter is missing required field 'spec_stage'")
    return SpecMeta(**known, extra=extra)  # type: ignore[arg-type]
