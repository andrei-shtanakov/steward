"""Vendored, pinned copy of spec-runner's SpecMeta frontmatter core.

SOURCE:   spec-runner/src/spec_runner/spec.py
SYMBOLS:  split_frontmatter, SpecMeta, meta_from_dict
CONTRACT: SPEC_META_CONTRACT v1 (spec-runner C2 SpecMeta contract)

DO NOT EDIT to change behaviour. This is a hand-pinned copy kept byte-faithful
to the upstream so steward reuses the same state shape (DEC-003) without a
runtime dependency on the sibling repo. Re-vendor when SPEC_META_CONTRACT bumps.

Note: this v1 copy predates C2's additive `owner_role`/`approver` fields, so
``SpecMeta`` here has neither. steward's ``meta.py`` reads ``owner_role`` from
the raw frontmatter dict until that field lands upstream and is re-vendored.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

import yaml

_FM_DELIM = "---"

SPEC_META_CONTRACT: int = 1


@dataclass
class SpecMeta:
    """Frontmatter state for one spec document."""

    spec_stage: str
    status: str = "draft"  # draft | approved | stale
    version: int = 1
    generated_by: str = ""
    generated_at: str = ""
    source_prompt_version: str = ""
    validation: str = ""  # pass | fail | warn | ""
    approved_by: str | None = None
    approved_at: str | None = None


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


def meta_from_dict(d: dict) -> SpecMeta:
    """Build a SpecMeta from a dict, ignoring unknown keys."""
    known = {f.name for f in fields(SpecMeta)}
    return SpecMeta(**{k: v for k, v in d.items() if k in known})
