"""gate-check CLI: aggregate findings, map to CI exit codes (WS-002, REQ-207).

Exit codes: ``0`` clean (warnings allowed) · ``1`` error findings · ``2``
config error (bad profile, bad facts file, missing bundle dir).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import typer

from steward.gatecheck.checks import Finding, collect_bundle, run_checks
from steward.gatecheck.git_facts import (
    FactsError,
    GitFacts,
    InjectedGitFacts,
    LiveGitFacts,
)
from steward.graph import ProfileError, SpecGraph, load_profile

app = typer.Typer(add_completion=False, help=__doc__)

_EXIT_FINDINGS = 1
_EXIT_CONFIG = 2


def _fail_config(message: str) -> None:
    typer.echo(f"config error: {message}", err=True)
    raise typer.Exit(_EXIT_CONFIG)


def _resolve_profile(profile: str) -> SpecGraph:
    candidate = Path(profile)
    if not candidate.is_file():
        candidate = Path("profiles") / f"{profile}.yaml"
    if not candidate.is_file():
        _fail_config(f"profile {profile!r} not found (looked for {candidate})")
    try:
        return load_profile(candidate)
    except ProfileError as err:
        _fail_config(str(err))
        raise AssertionError from None  # unreachable; keeps type-checkers calm


def _git_facts(no_fs: Path | None, spec_dir: Path) -> GitFacts:
    if no_fs is not None:
        try:
            return InjectedGitFacts.from_file(no_fs)
        except FactsError as err:
            _fail_config(str(err))
    proc = subprocess.run(  # noqa: S603 S607 — fixed argv, no user input
        ["git", "rev-parse", "--show-toplevel"],
        cwd=spec_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        _fail_config("not inside a git repository (use --no-fs facts.json)")
    return LiveGitFacts(Path(proc.stdout.strip()), spec_dir)


def _render_text(findings: list[Finding]) -> None:
    for finding in findings:
        typer.echo(
            f"{finding.severity.upper():5} {finding.rule_id:16} "
            f"{finding.artifact}: {finding.message}"
        )
    errors = sum(1 for f in findings if f.severity == "error")
    warns = len(findings) - errors
    typer.echo(f"gate-check: {errors} error(s), {warns} warning(s)")


def _render_json(findings: list[Finding]) -> None:
    payload = {
        "findings": [vars(f) for f in findings],
        "errors": sum(1 for f in findings if f.severity == "error"),
        "warnings": sum(1 for f in findings if f.severity == "warn"),
    }
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command()
def main(
    spec_dir: Path = typer.Argument(Path("spec"), help="Bundle directory to lint."),
    profile: str = typer.Option(
        "lite", "--profile", help="Profile name (profiles/<name>.yaml) or a YAML path."
    ),
    no_fs: Path | None = typer.Option(
        None, "--no-fs", help="Deterministic mode: read git facts from this JSON file."
    ),
    output: str = typer.Option("text", "--format", help="Output format: text | json."),
) -> None:
    """Lint a governance bundle against its profile's gates."""
    if output not in ("text", "json"):
        _fail_config(f"unknown format {output!r} (expected text or json)")
    if not spec_dir.is_dir():
        _fail_config(f"bundle directory not found: {spec_dir}")

    graph = _resolve_profile(profile)
    git = _git_facts(no_fs, spec_dir)

    artifacts, findings = collect_bundle(graph, spec_dir)
    findings.extend(run_checks(graph, artifacts, git))

    if output == "json":
        _render_json(findings)
    else:
        _render_text(findings)

    if any(f.severity == "error" for f in findings):
        raise typer.Exit(_EXIT_FINDINGS)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
