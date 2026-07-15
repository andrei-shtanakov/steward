"""steward-compile CLI: render compile-down targets from a governance bundle.

Exit codes mirror gate-check: ``0`` emitted · ``2`` config error (no/invalid
decomposition artifact, bad base config). Output goes to stdout by default —
steward never writes into another repo or into ``_cowork_output/`` on its own;
redirect or pass ``-o`` explicitly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
import yaml

from steward.compile.decomposition import CompileError, Decomposition, parse_decomposition
from steward.compile.delegation import emit_delegation
from steward.compile.project_yaml import emit_project_yaml
from steward.meta import MetaError, parse_artifact

app = typer.Typer(add_completion=False, help=__doc__)

_EXIT_CONFIG = 2

_DECOMPOSITION_STAGE = "decomposition"


def _fail_config(message: str) -> None:
    typer.echo(f"config error: {message}", err=True)
    raise typer.Exit(_EXIT_CONFIG)


def _load_decomposition(spec_dir: Path) -> Decomposition:
    if not spec_dir.is_dir():
        _fail_config(f"bundle directory not found: {spec_dir}")
    candidates: list[Path] = []
    for path in sorted(spec_dir.rglob("*.md")):
        try:
            meta = parse_artifact(path.read_text(encoding="utf-8"))
        except MetaError:
            continue  # gate-check owns frontmatter findings; compile just skips
        if meta is not None and meta.spec_stage == _DECOMPOSITION_STAGE:
            candidates.append(path)
    if not candidates:
        _fail_config(f"no artifact with spec_stage: {_DECOMPOSITION_STAGE} under {spec_dir}")
    if len(candidates) > 1:
        listed = ", ".join(p.name for p in candidates)
        _fail_config(f"more than one decomposition artifact under {spec_dir}: {listed}")
    try:
        return parse_decomposition(candidates[0].read_text(encoding="utf-8"))
    except CompileError as err:
        _fail_config(f"{candidates[0]}: {err}")
        raise AssertionError from None  # unreachable; keeps type-checkers calm


def _write_output(text: str, out: Path | None) -> None:
    if out is None:
        typer.echo(text, nl=False)
    else:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {out}", err=True)


@app.command("project-yaml")
def project_yaml(
    spec_dir: Path = typer.Argument(Path("spec"), help="Bundle directory with the decomposition."),
    base: Path | None = typer.Option(
        None, "--base", help="YAML mapping of Maestro deployment knobs, passed through verbatim."
    ),
    out: Path | None = typer.Option(None, "--out", "-o", help="Output file (default: stdout)."),
) -> None:
    """Emit the Maestro project.yaml for the bundle's decomposition (REQ-005)."""
    decomposition = _load_decomposition(spec_dir)
    base_data = None
    if base is not None:
        try:
            base_data = yaml.safe_load(base.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as err:
            _fail_config(f"cannot read base config {base}: {err}")
    try:
        text = emit_project_yaml(decomposition, base_data)
    except CompileError as err:
        _fail_config(str(err))
        raise AssertionError from None
    _write_output(text, out)


@app.command()
def delegation(
    spec_dir: Path = typer.Argument(Path("spec"), help="Bundle directory with the decomposition."),
    profile: str = typer.Option("lite", "--profile", help="spec-runner authoring profile."),
    gated: bool = typer.Option(True, "--gated/--no-gated", help="Author with --gated."),
    out: Path | None = typer.Option(None, "--out", "-o", help="Output file (default: stdout)."),
) -> None:
    """Emit the WS → spec-runner authoring delegation manifest (DEC-005)."""
    decomposition = _load_decomposition(spec_dir)
    _write_output(emit_delegation(decomposition, profile=profile, gated=gated), out)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
