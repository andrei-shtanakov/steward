"""Risk classification engine (WS-006, REQ-602..605).

Pure functions over :class:`RiskModel`: no I/O, no git — callers supply the
changed paths (ex-post) or declared scope globs (ex-ante). Determinism is the
contract (REQ-610): same inputs, same :class:`Classification`.

Glob semantics (documented, tested): ``**`` crosses ``/``; ``*`` and ``?``
stay within one segment. Ex-ante matches rule globs against scope globs via
fixed-prefix intersection — conservative on purpose (fail-closed): a broad
scope picks up every narrower rule it might reach.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from steward.riskclassify.model import RiskModel, tier_max

__all__ = ["Classification", "classify_declared", "classify_diff"]

_AXES = ("change_class", "blast_radius", "trust_boundary")
_SCOPE_VIOLATION_MIN = "high"
_CONTRACTS_PREFIX = "contracts/"


@dataclass(frozen=True)
class Classification:
    """One tier decision with everything a post-mortem needs (DESIGN-610)."""

    tier: str
    phase: str  # "ex_ante" | "ex_post"
    inputs: dict[str, str]
    dominant_axis: str
    floor_profile: str
    mandatory_gates: list[str]
    sha: str
    risk_model_version: str
    flags: list[str] = field(default_factory=list)


def classify_diff(
    model: RiskModel,
    *,
    project: str,
    paths: list[str],
    sha: str,
    profile: str = "lite",
    flags: list[str] | None = None,
    declared_scope: list[str] | None = None,
) -> Classification:
    """Ex-post classification of an actual diff (REQ-605).

    With ``declared_scope`` given, any path outside it raises the tier to at
    least ``high`` and sets the ``scope_violation`` flag (bridge to RD-006).
    """
    change_class = _class_of_paths(model, project, paths)
    blast = _blast_of_paths(model, project, paths)
    trust = _trust_of_paths(model, project, paths, flags or [])
    out_flags = []
    extra_floor = None
    if declared_scope is not None and _outside_scope(paths, declared_scope):
        out_flags.append("scope_violation")
        extra_floor = _SCOPE_VIOLATION_MIN
    return _combine(
        model,
        "ex_post",
        change_class,
        blast,
        trust,
        profile,
        sha,
        flags=out_flags,
        extra_floor=extra_floor,
    )


def classify_declared(
    model: RiskModel,
    *,
    project: str,
    scope: list[str],
    sha: str,
    profile: str = "lite",
    flags: list[str] | None = None,
) -> Classification:
    """Ex-ante classification of a declared scope — the diff does not exist yet.

    Conservative: every rule whose glob may intersect a scope glob contributes
    its class, and the max wins (REQ-605, fail-closed).
    """
    rules = [*model.path_class.get(project, []), *model.generic_class]
    classes = {r.value for r in rules if _may_intersect_any(r.glob, scope)}
    classes.add("unknown") if not classes else None
    change_class = _max_class(model, classes or {"unknown"})

    # contracts/** plays the *rule* role here: a prefix-less scope ("**")
    # covers the whole repo and must count as touching contracts (fail-closed).
    # Registered contract paths outside contracts/ (exact-file registry keys,
    # e.g. an ecosystem SSOT) count the same way.
    touches_contracts = any(
        _globs_may_intersect(_CONTRACTS_PREFIX + "**", g) for g in scope
    ) or any(_may_intersect_any(p, scope) for p in _registry_paths(model, project))
    blast = "single-repo"
    if touches_contracts:
        blast = _registry_blast(model, project, pinned_key=None)

    boundaries = {r.value for r in model.trust_rules if _may_intersect_any(r.glob, scope)}
    trust = _max_trust(model, boundaries, flags or [])

    return _combine(model, "ex_ante", change_class, blast, trust, profile, sha)


# --- combinator (REQ-604) ---


def _combine(
    model: RiskModel,
    phase: str,
    change_class: str,
    blast: str,
    trust: str,
    profile: str,
    sha: str,
    *,
    flags: list[str] | None = None,
    extra_floor: str | None = None,
) -> Classification:
    floor = model.profile_floors.get(profile)
    if floor is None:
        raise ValueError(f"unknown profile '{profile}' (no floor in risk model)")
    axis_tiers = {
        "change_class": model.class_tiers[change_class],
        "blast_radius": model.blast_tiers[blast],
        "trust_boundary": model.trust_tiers[trust],
    }
    tier = tier_max(floor, *axis_tiers.values())
    if extra_floor is not None:
        tier = tier_max(tier, extra_floor)
    dominant = next((a for a in _AXES if axis_tiers[a] == tier), "floor")
    return Classification(
        tier=tier,
        phase=phase,
        inputs={"change_class": change_class, "blast_radius": blast, "trust_boundary": trust},
        dominant_axis=dominant,
        floor_profile=profile,
        mandatory_gates=list(model.tier_gates[tier]),
        sha=sha,
        risk_model_version=model.version_sha,
        flags=flags or [],
    )


# --- change_class axis ---


def _class_of_paths(model: RiskModel, project: str, paths: list[str]) -> str:
    return _max_class(model, {_class_of_path(model, project, p) for p in paths} or {"unknown"})


def _class_of_path(model: RiskModel, project: str, path: str) -> str:
    for rule in [*model.path_class.get(project, []), *model.generic_class]:
        if _glob_match(rule.glob, path):
            return rule.value
    return "unknown"  # built-in fail-closed default (REQ-603)


def _max_class(model: RiskModel, classes: set[str]) -> str:
    # Tie-break by name: set iteration order is not stable across processes,
    # and byte-identical output is a requirement (REQ-610).
    return max(classes, key=lambda c: (_tier_index(model.class_tiers[c]), c))


# --- blast_radius axis ---


def _blast_of_paths(model: RiskModel, project: str, paths: list[str]) -> str:
    blast = "single-repo"
    for path in paths:
        if f"{project}/{path}" in model.consumer_registry:
            # Exact-file registry key — a registered contract may live outside
            # contracts/ (e.g. the ecosystem agents-catalog SSOT).
            key = f"{project}/{path}"
        elif path.startswith(_CONTRACTS_PREFIX):
            segments = path.split("/")
            key = f"{project}/{segments[0]}/{segments[1]}" if len(segments) > 2 else None
        else:
            continue
        blast = tier_str_max(model, blast, _registry_blast(model, project, pinned_key=key))
    return blast


def _registry_paths(model: RiskModel, project: str) -> list[str]:
    """Repo-relative paths of the project's registered contracts."""
    prefix = project + "/"
    return [k.removeprefix(prefix) for k in model.consumer_registry if k.startswith(prefix)]


def _registry_blast(model: RiskModel, project: str, *, pinned_key: str | None) -> str:
    """Blast for a contract touch: registry consumers decide the grade.

    A pinned key looks up one contract; no pin (ex-ante broad scope) takes the
    worst entry of the project — conservative. Unregistered contract dirs are
    still cross-repo: it IS a contract, we just don't know the consumers.
    """
    if pinned_key is not None:
        consumers = model.consumer_registry.get(pinned_key, [])
        return "ecosystem-contract" if len(consumers) >= 2 else "cross-repo"
    worst = "cross-repo"
    for key, consumers in model.consumer_registry.items():
        if key.startswith(project + "/") and len(consumers) >= 2:
            worst = "ecosystem-contract"
    return worst


def tier_str_max(model: RiskModel, a: str, b: str) -> str:
    return max(a, b, key=lambda v: _tier_index(model.blast_tiers[v]))


# --- trust_boundary axis ---


def _trust_of_paths(model: RiskModel, project: str, paths: list[str], flags: list[str]) -> str:
    boundaries = {
        rule.value for rule in model.trust_rules for path in paths if _glob_match(rule.glob, path)
    }
    return _max_trust(model, boundaries, flags)


def _max_trust(model: RiskModel, boundaries: set[str], flags: list[str]) -> str:
    for flag in flags:
        if flag not in model.declared_flags:
            raise ValueError(f"unknown declared flag '{flag}' (allowed: {model.declared_flags})")
    candidates = boundaries | set(flags) | {"none"}
    return max(candidates, key=lambda b: (_tier_index(model.trust_tiers[b]), b))


# --- scope check (REQ-605) ---


def _outside_scope(paths: list[str], scope: list[str]) -> bool:
    return any(not any(_glob_match(g, p) for g in scope) for p in paths)


# --- glob machinery ---


def _tier_index(tier: str) -> int:
    from steward.riskclassify.model import TIERS

    return TIERS.index(tier)


def _glob_match(pattern: str, path: str) -> bool:
    return _glob_regex(pattern).fullmatch(path) is not None


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """``**`` crosses ``/``; ``*``/``?`` stay within a segment."""
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("".join(out))


def _fixed_prefix(pattern: str) -> str:
    for i, ch in enumerate(pattern):
        if ch in "*?[":
            return pattern[:i]
    return pattern


def _globs_may_intersect(rule_glob: str, scope_glob: str) -> bool:
    """Could some path match both globs? Asymmetric by design.

    A scope with no fixed prefix (``**``) covers the whole repo — every rule
    intersects. A *rule* with no fixed prefix (name-based generics such as
    ``**/.env*``) is deliberately excluded from directory-shaped scopes:
    matching it everywhere would collapse every ex-ante tier to critical,
    and the real diff re-checks those rules ex-post anyway (REQ-605).
    """
    scope_prefix = _fixed_prefix(scope_glob)
    if scope_prefix == "":
        return True
    rule_prefix = _fixed_prefix(rule_glob)
    if rule_prefix == "":
        return False
    return rule_prefix.startswith(scope_prefix) or scope_prefix.startswith(rule_prefix)


def _may_intersect_any(rule_glob: str, scope: list[str]) -> bool:
    return any(_globs_may_intersect(rule_glob, g) for g in scope)
