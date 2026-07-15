"""Emitter: workstreams → spec-runner authoring delegation manifest (REQ-005, DEC-005).

steward never writes a leaf ``tasks.md`` itself — each workstream's spec is
authored by spec-runner (``plan [--gated] --profile <name>``) inside that
workstream's directory. This emitter renders the delegation manifest: which
directory to run in and the exact spec-runner invocation, in dependency order.
The manifest is data for whoever drives the calls (Maestro orchestrate, CI, or
a human) — steward does not execute spec-runner.
"""

from __future__ import annotations

from steward.compile._yaml import render_yaml
from steward.compile.decomposition import Decomposition, dag_depths

__all__ = ["emit_delegation"]

_HEADER = (
    "# steward compile-down: WS → spec-runner authoring delegation (DEC-005).\n"
    "# Порядок — топологический по depends_on; исполнитель — Maestro/CI/человек,\n"
    "# steward сам spec-runner не запускает.\n"
)


def emit_delegation(decomposition: Decomposition, profile: str = "lite", gated: bool = True) -> str:
    """Render the per-workstream spec-runner invocation manifest."""
    command = ["spec-runner", "plan"]
    if gated:
        command.append("--gated")
    command += ["--profile", profile]

    depths = dag_depths(decomposition.workstreams)
    ordered = sorted(decomposition.workstreams, key=lambda w: (depths[w.id], w.id))
    payload = {
        "project": decomposition.project,
        "workstreams": [
            {
                "ws": w.ws,
                "id": w.id,
                "dir": f"workstreams/{w.ws}-{w.id}",
                "depends_on": list(w.depends_on),
                # fresh list per entry — a shared object would make safe_dump
                # emit &id/*id YAML aliases
                "command": list(command),
            }
            for w in ordered
        ],
    }
    return f"{_HEADER}\n{render_yaml(payload)}"
