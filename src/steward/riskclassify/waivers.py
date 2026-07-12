"""Waiver files: human overrides for mandatory-gate fails (WS-006, REQ-609).

A waiver is a file under ``spec/waivers/`` with YAML frontmatter, merged via
PR by the gate's owner role (CODEOWNERS on the directory) — git stays the only
approval authority, no new RBAC (DESIGN-609). Waivers are SHA-bound: a new
commit invalidates them together with every verdict (DESIGN-608), and the
``critical`` tier admits no waivers at all (waiver_policy, OQ-3).

Frontmatter fields: ``gate_id``, ``sha`` (full head SHA the waiver covers),
``tier`` (the classification tier it was issued against — how the forbidden
policy is enforced without re-running the diff), ``waived_by`` (git handle),
``reason``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from steward._vendor.spec_meta import split_frontmatter
from steward.riskclassify.model import TIERS, RiskModel

__all__ = ["Waiver", "WaiverFinding", "find_waiver", "load_waivers", "validate_waivers"]

_REQUIRED = ("gate_id", "sha", "tier", "waived_by", "reason")
_FORBIDDEN = "forbidden"


@dataclass(frozen=True)
class Waiver:
    """One parsed waiver file."""

    gate_id: str
    sha: str
    tier: str
    waived_by: str
    reason: str
    path: str


@dataclass(frozen=True)
class WaiverFinding:
    """A defect in a waiver file (error severity blocks, like gate-check)."""

    severity: Literal["error", "warn"]
    rule_id: str
    path: str
    message: str


def load_waivers(directory: Path, *, strict: bool = False) -> list[Waiver]:
    """Parse every ``*.md`` waiver under ``directory`` (missing dir → empty).

    Files without frontmatter or with missing fields are skipped — a waiver
    that does not parse must never accidentally waive anything (fail-closed).
    With ``strict=True`` such files raise instead, for authoring-time checks.
    """
    if not directory.is_dir():
        return []
    waivers = []
    for path in sorted(directory.glob("*.md")):
        try:
            meta, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            if strict:
                raise ValueError(f"{path}: unreadable waiver: {exc}") from exc
            continue
        if meta is None:
            if strict:
                raise ValueError(f"{path}: waiver file has no frontmatter")
            continue
        missing = [k for k in _REQUIRED if not isinstance(meta.get(k), str) or not meta[k]]
        if missing:
            if strict:
                raise ValueError(f"{path}: waiver missing/invalid fields: {missing}")
            continue
        waivers.append(
            Waiver(
                gate_id=meta["gate_id"],
                sha=meta["sha"],
                tier=meta["tier"],
                waived_by=meta["waived_by"],
                reason=meta["reason"],
                path=str(path),
            )
        )
    return waivers


def validate_waivers(
    waivers: list[Waiver], model: RiskModel, *, head_sha: str
) -> list[WaiverFinding]:
    """Findings for stale, forbidden-tier, or malformed-tier waivers (REQ-609)."""
    findings = []
    for w in waivers:
        if w.tier not in TIERS:
            findings.append(
                WaiverFinding(
                    severity="error",
                    rule_id="waiver-bad-tier",
                    path=w.path,
                    message=f"unknown tier '{w.tier}' (expected one of {TIERS})",
                )
            )
            continue
        if model.waiver_policy.get(w.tier) == _FORBIDDEN:
            findings.append(
                WaiverFinding(
                    severity="error",
                    rule_id="waiver-forbidden-tier",
                    path=w.path,
                    message=f"waivers are forbidden on tier '{w.tier}' (waiver_policy)",
                )
            )
            continue
        if w.sha != head_sha:
            findings.append(
                WaiverFinding(
                    severity="error",
                    rule_id="waiver-stale-sha",
                    path=w.path,
                    message=(
                        f"waiver is bound to {w.sha[:12]} but HEAD is {head_sha[:12]} — "
                        f"a new commit invalidates waivers (DESIGN-608); remove or re-issue"
                    ),
                )
            )
    return findings


def find_waiver(waivers: list[Waiver], gate_id: str, sha: str) -> Waiver | None:
    """The consumer-side lookup: a `waived` verdict needs exactly this match."""
    return next((w for w in waivers if w.gate_id == gate_id and w.sha == sha), None)
