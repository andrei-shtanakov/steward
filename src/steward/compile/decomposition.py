"""Normalized decomposition: the machine-readable half of the decomposition artifact.

The decomposition artifact stays a human document (prose, DAG picture), but it
carries one fenced block — `````yaml steward-compile`` — holding the normalized
workstream list (DEC-005: "steward отдаёт нормализованный список"). That block
is the single input of every compile-down emitter.

Validation here is deliberately stricter than Maestro's own preflight: the
contract check (``emitter-contract-check.md``) proved ``maestro validate
--no-fs`` silently accepts a dangling ``depends_on``, so dep-link integrity
(unknown refs, self-refs, duplicates, cycles) is enforced upstream, both by
this parser and by gate-check before compilation ever runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

__all__ = [
    "CompileError",
    "Decomposition",
    "Workstream",
    "dag_depths",
    "extract_compile_block",
    "parse_decomposition",
]

# Fence info string marks the block explicitly: plain ```yaml blocks stay
# free for illustration, only ```yaml steward-compile is normative.
_BLOCK_RE = re.compile(
    r"^```yaml[ \t]+steward-compile[ \t]*\n(?P<body>.*?)^```[ \t]*$",
    re.MULTILINE | re.DOTALL,
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class CompileError(ValueError):
    """Malformed or inconsistent normalized decomposition (config error, exit 2)."""


@dataclass(frozen=True)
class Workstream:
    """One workstream of the normalized decomposition."""

    id: str  # slug used as the Maestro workstream id
    ws: str  # governance id (WS-001, ...) linking back to the artifact prose
    title: str
    description: str
    scope: tuple[str, ...]
    depends_on: tuple[str, ...]
    priority: int | None = None  # explicit override; None -> derived from DAG depth


@dataclass(frozen=True)
class Decomposition:
    """The normalized decomposition block, validated."""

    project: str
    description: str
    workstreams: tuple[Workstream, ...]


def extract_compile_block(text: str) -> str | None:
    """Return the raw YAML of the ``steward-compile`` block, or None when absent."""
    matches = _BLOCK_RE.findall(text)
    if not matches:
        return None
    if len(matches) > 1:
        raise CompileError("more than one 'yaml steward-compile' block in the artifact")
    return matches[0]


def parse_decomposition(text: str) -> Decomposition:
    """Parse and validate the normalized decomposition out of an artifact body.

    Raises :class:`CompileError` when the block is missing, malformed, or the
    workstream graph is inconsistent (duplicate/dangling/self/cyclic deps).
    """
    raw = extract_compile_block(text)
    if raw is None:
        raise CompileError("decomposition artifact carries no 'yaml steward-compile' block")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as err:
        raise CompileError(f"steward-compile block is not valid YAML: {err}") from err
    if not isinstance(data, dict):
        raise CompileError("steward-compile block must be a YAML mapping")

    project = data.get("project")
    if not isinstance(project, str) or not project.strip():
        raise CompileError("steward-compile block needs a non-empty string 'project'")
    description = data.get("description", "")
    if not isinstance(description, str):
        raise CompileError("'description' must be a string")

    raw_ws = data.get("workstreams")
    if not isinstance(raw_ws, list) or not raw_ws:
        raise CompileError("'workstreams' must be a non-empty list")
    workstreams = tuple(_parse_workstream(entry, idx) for idx, entry in enumerate(raw_ws))

    _validate_graph(workstreams)
    return Decomposition(project=project.strip(), description=description, workstreams=workstreams)


def dag_depths(workstreams: tuple[Workstream, ...]) -> dict[str, int]:
    """Depth of each workstream in the dependency DAG (roots are depth 0)."""
    depths: dict[str, int] = {}
    by_id = {w.id: w for w in workstreams}

    def depth_of(ws_id: str) -> int:
        if ws_id not in depths:
            deps = by_id[ws_id].depends_on
            depths[ws_id] = 1 + max(depth_of(d) for d in deps) if deps else 0
        return depths[ws_id]

    for w in workstreams:
        depth_of(w.id)
    return depths


def _parse_workstream(entry: object, idx: int) -> Workstream:
    where = f"workstreams[{idx}]"
    if not isinstance(entry, dict):
        raise CompileError(f"{where} must be a mapping")

    def req_str(key: str) -> str:
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            raise CompileError(f"{where} needs a non-empty string {key!r}")
        return value.strip()

    ws_id = req_str("id")
    if not _ID_RE.match(ws_id):
        raise CompileError(f"{where}: id {ws_id!r} is not a lowercase slug")

    scope = entry.get("scope")
    if (
        not isinstance(scope, list)
        or not scope
        or not all(isinstance(s, str) and s.strip() for s in scope)
    ):
        raise CompileError(f"{where}: 'scope' must be a non-empty list of path patterns")

    deps = entry.get("depends_on", [])
    if not isinstance(deps, list) or not all(isinstance(d, str) and d.strip() for d in deps):
        raise CompileError(f"{where}: 'depends_on' must be a list of workstream ids")

    priority = entry.get("priority")
    if priority is not None and (not isinstance(priority, int) or isinstance(priority, bool)):
        raise CompileError(f"{where}: 'priority' must be an integer")

    description = entry.get("description", "")
    if not isinstance(description, str):
        raise CompileError(f"{where}: 'description' must be a string")

    return Workstream(
        id=ws_id,
        ws=req_str("ws"),
        title=req_str("title"),
        description=description,
        scope=tuple(s.strip() for s in scope),
        depends_on=tuple(d.strip() for d in deps),
        priority=priority,
    )


def _validate_graph(workstreams: tuple[Workstream, ...]) -> None:
    seen: set[str] = set()
    for w in workstreams:
        if w.id in seen:
            raise CompileError(f"duplicate workstream id {w.id!r}")
        seen.add(w.id)

    for w in workstreams:
        if w.id in w.depends_on:
            raise CompileError(f"workstream {w.id!r} depends on itself")
        if len(set(w.depends_on)) != len(w.depends_on):
            raise CompileError(f"workstream {w.id!r} lists a duplicate dependency")
        for dep in w.depends_on:
            if dep not in seen:
                # The trap Maestro preflight misses (emitter-contract-check.md).
                raise CompileError(f"workstream {w.id!r} depends on unknown workstream {dep!r}")

    # Kahn: whatever survives peeling has a cycle.
    remaining = {w.id: set(w.depends_on) for w in workstreams}
    while remaining:
        roots = [ws_id for ws_id, deps in remaining.items() if not deps]
        if not roots:
            cyclic = ", ".join(sorted(remaining))
            raise CompileError(f"dependency cycle among workstreams: {cyclic}")
        for root in roots:
            del remaining[root]
        for deps in remaining.values():
            deps.difference_update(roots)
