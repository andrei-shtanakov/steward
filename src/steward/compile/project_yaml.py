"""Emitter: normalized decomposition → Maestro ``project.yaml`` (REQ-005, AC-006).

Maestro owns this format — the emitter renders exactly the shape that
``maestro.config.load_orchestrator_config`` + preflight accepted in
``emitter-contract-check.md`` and adds nothing of its own. Deployment knobs
steward has no business knowing (repo paths, spec_runner settings, concurrency)
come from a *base config* mapping passed through verbatim.

``priority`` is derived from DAG depth unless a workstream pins it explicitly:
roots get the highest value, each level down steps by 10, the deepest level
lands on 0 — matching the hand-compiled contract artifact.
"""

from __future__ import annotations

from steward.compile._yaml import render_yaml
from steward.compile.decomposition import CompileError, Decomposition, dag_depths

__all__ = ["emit_project_yaml"]

_PRIORITY_STEP = 10

# Keys the emitter itself produces; a base config must not smuggle them in.
_RESERVED_KEYS = frozenset({"project", "description", "workstreams"})

_HEADER = (
    "# Maestro Multi-Process Orchestrator config for `{project}`\n"
    "# Скомпилирован steward-compile из артефакта decomposition (compile-down, REQ-005).\n"
    "# steward сам этот файл не пишет в рантайме — компиляция запускается явно.\n"
)


def emit_project_yaml(decomposition: Decomposition, base: dict | None = None) -> str:
    """Render the Maestro ``project.yaml`` text for a validated decomposition."""
    if base is not None:
        if not isinstance(base, dict):
            raise CompileError("base config must be a YAML mapping")
        clash = _RESERVED_KEYS & set(base)
        if clash:
            raise CompileError(
                f"base config must not define emitter-owned keys: {', '.join(sorted(clash))}"
            )

    priorities = _priorities(decomposition)
    payload: dict = {
        "project": decomposition.project,
        "description": decomposition.description,
    }
    payload.update(base or {})
    payload["workstreams"] = [
        {
            "id": w.id,
            "title": w.title,
            "description": w.description,
            "scope": list(w.scope),
            "depends_on": list(w.depends_on),
            "priority": priorities[w.id],
        }
        for w in decomposition.workstreams
    ]

    header = _HEADER.format(project=decomposition.project)
    return f"{header}\n{render_yaml(payload)}"


def _priorities(decomposition: Decomposition) -> dict[str, int]:
    depths = dag_depths(decomposition.workstreams)
    max_depth = max(depths.values())
    return {
        w.id: w.priority if w.priority is not None else (max_depth - depths[w.id]) * _PRIORITY_STEP
        for w in decomposition.workstreams
    }
