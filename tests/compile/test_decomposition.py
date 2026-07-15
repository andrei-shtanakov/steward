"""Unit tests for the normalized-decomposition parser (WS-004, REQ-005)."""

from __future__ import annotations

import pytest

from steward.compile.decomposition import (
    CompileError,
    dag_depths,
    extract_compile_block,
    parse_decomposition,
)


def _doc(block_body: str) -> str:
    return f"# Decomposition\n\nprose\n\n```yaml steward-compile\n{block_body}```\n\ntail\n"


_VALID = """\
project: demo
description: |
  two-line
  description
workstreams:
  - id: core
    ws: WS-001
    title: "Core"
    description: "base"
    scope: ["src/core/**"]
    depends_on: []
  - id: linter
    ws: WS-002
    title: "Linter"
    description: "checks"
    scope: ["src/linter/**"]
    depends_on: [core]
"""


def test_parse_valid_block() -> None:
    d = parse_decomposition(_doc(_VALID))
    assert d.project == "demo"
    assert [w.id for w in d.workstreams] == ["core", "linter"]
    assert d.workstreams[1].depends_on == ("core",)
    assert d.workstreams[0].scope == ("src/core/**",)
    assert d.workstreams[0].ws == "WS-001"


def test_plain_yaml_fences_are_ignored() -> None:
    text = "```yaml\nproject: nope\n```\n"
    assert extract_compile_block(text) is None
    with pytest.raises(CompileError, match="no 'yaml steward-compile' block"):
        parse_decomposition(text)


def test_two_blocks_is_an_error() -> None:
    text = _doc(_VALID) + _doc(_VALID)
    with pytest.raises(CompileError, match="more than one"):
        parse_decomposition(text)


def test_dangling_depends_on_is_caught() -> None:
    # The exact class of breakage Maestro validate --no-fs lets through.
    body = _VALID.replace("depends_on: [core]", "depends_on: [does-not-exist]")
    with pytest.raises(CompileError, match="unknown workstream 'does-not-exist'"):
        parse_decomposition(_doc(body))


def test_dependency_cycle_is_caught() -> None:
    body = _VALID.replace("depends_on: []", "depends_on: [linter]")
    with pytest.raises(CompileError, match="cycle"):
        parse_decomposition(_doc(body))


def test_self_dependency_is_caught() -> None:
    body = _VALID.replace("depends_on: [core]", "depends_on: [linter]")
    with pytest.raises(CompileError, match="depends on itself"):
        parse_decomposition(_doc(body))


def test_duplicate_workstream_id_is_caught() -> None:
    body = _VALID.replace("id: linter", "id: core")
    with pytest.raises(CompileError, match="duplicate workstream id"):
        parse_decomposition(_doc(body))


def test_duplicate_dependency_is_caught() -> None:
    body = _VALID.replace("depends_on: [core]", "depends_on: [core, core]")
    with pytest.raises(CompileError, match="duplicate dependency"):
        parse_decomposition(_doc(body))


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (("project: demo", "project: ''"), "project"),
        (("id: core", "id: Core"), "lowercase slug"),
        (('scope: ["src/core/**"]', "scope: []"), "scope"),
        (("depends_on: [core]", "depends_on: core"), "depends_on"),
        (("workstreams:", "workstreams: []\nignored:"), "non-empty list"),
    ],
)
def test_malformed_blocks_raise(mutation: tuple[str, str], match: str) -> None:
    body = _VALID.replace(*mutation)
    with pytest.raises(CompileError, match=match):
        parse_decomposition(_doc(body))


def test_broken_yaml_in_block_raises() -> None:
    with pytest.raises(CompileError, match="not valid YAML"):
        parse_decomposition(_doc("a: : :\n"))


def test_explicit_priority_must_be_int() -> None:
    body = _VALID.replace("ws: WS-001", "ws: WS-001\n    priority: high")
    with pytest.raises(CompileError, match="priority"):
        parse_decomposition(_doc(body))


def test_dag_depths() -> None:
    d = parse_decomposition(_doc(_VALID))
    assert dag_depths(d.workstreams) == {"core": 0, "linter": 1}
