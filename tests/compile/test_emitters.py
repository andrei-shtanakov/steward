"""Unit tests for the project.yaml / delegation emitters (WS-004, REQ-005)."""

from __future__ import annotations

import pytest
import yaml

from steward.compile.decomposition import (
    CompileError,
    Decomposition,
    Workstream,
    parse_decomposition,
)
from steward.compile.delegation import emit_delegation
from steward.compile.project_yaml import emit_project_yaml


def _ws(ws_id: str, deps: tuple[str, ...] = (), priority: int | None = None) -> Workstream:
    return Workstream(
        id=ws_id,
        ws=f"WS-{ws_id}",
        title=ws_id.title(),
        description=f"{ws_id} does things\nacross two lines\n",
        scope=(f"src/{ws_id}/**",),
        depends_on=deps,
        priority=priority,
    )


def _decomposition(*workstreams: Workstream) -> Decomposition:
    return Decomposition(project="demo", description="demo project\n", workstreams=workstreams)


def test_priorities_derived_from_dag_depth() -> None:
    d = _decomposition(_ws("a"), _ws("b", deps=("a",)), _ws("c", deps=("b",)))
    payload = yaml.safe_load(emit_project_yaml(d))
    priorities = {w["id"]: w["priority"] for w in payload["workstreams"]}
    assert priorities == {"a": 20, "b": 10, "c": 0}


def test_explicit_priority_wins_over_derived() -> None:
    d = _decomposition(_ws("a"), _ws("b", deps=("a",), priority=99))
    payload = yaml.safe_load(emit_project_yaml(d))
    priorities = {w["id"]: w["priority"] for w in payload["workstreams"]}
    assert priorities == {"a": 10, "b": 99}


def test_base_config_passthrough_between_header_and_workstreams() -> None:
    base = {"max_concurrent": 2, "spec_runner": {"max_retries": 3}}
    payload = yaml.safe_load(emit_project_yaml(_decomposition(_ws("a")), base))
    assert payload["max_concurrent"] == 2
    assert payload["spec_runner"] == {"max_retries": 3}
    assert list(payload) == [
        "project",
        "description",
        "max_concurrent",
        "spec_runner",
        "workstreams",
    ]


def test_base_config_cannot_shadow_emitter_keys() -> None:
    with pytest.raises(CompileError, match="project"):
        emit_project_yaml(_decomposition(_ws("a")), {"project": "hijack"})


def test_multiline_strings_render_as_literal_blocks() -> None:
    text = emit_project_yaml(_decomposition(_ws("a")))
    assert "description: |" in text  # not the single-quoted safe_dump mangling


def test_delegation_manifest_topological_order_and_commands() -> None:
    d = _decomposition(_ws("late", deps=("early",)), _ws("early"))
    payload = yaml.safe_load(emit_delegation(d, profile="team", gated=False))
    assert [w["id"] for w in payload["workstreams"]] == ["early", "late"]
    for w in payload["workstreams"]:
        assert w["command"] == ["spec-runner", "plan", "--profile", "team"]
        assert w["dir"] == f"workstreams/WS-{w['id']}-{w['id']}"


def test_delegation_gated_default_and_no_yaml_aliases() -> None:
    d = _decomposition(_ws("a"), _ws("b", deps=("a",)))
    text = emit_delegation(d)
    assert "&id" not in text and "*id" not in text  # shared command object would alias
    payload = yaml.safe_load(text)
    assert payload["workstreams"][0]["command"] == [
        "spec-runner",
        "plan",
        "--gated",
        "--profile",
        "lite",
    ]


def test_emitters_accept_parser_output_end_to_end() -> None:
    doc = (
        "```yaml steward-compile\n"
        "project: demo\n"
        "description: d\n"
        "workstreams:\n"
        "  - {id: a, ws: WS-001, title: A, description: d, scope: ['x/**'], depends_on: []}\n"
        "```\n"
    )
    d = parse_decomposition(doc)
    assert yaml.safe_load(emit_project_yaml(d))["workstreams"][0]["id"] == "a"
    assert yaml.safe_load(emit_delegation(d))["workstreams"][0]["ws"] == "WS-001"
