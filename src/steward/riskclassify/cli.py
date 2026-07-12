"""`steward risk-classify` CLI (WS-006, REQ-610, DESIGN-610).

Single source of truth for tiers: Maestro consumes this JSON and never
computes risk itself. Exit codes mirror gate-check: ``0`` classified,
``2`` config error (bad model / bad input). Classification never "fails"
with findings — it is a function, not a check.

Inputs (exactly one):
- ``--diff BASE..HEAD`` — live git (changed paths + head sha from the repo
  in ``--repo``, default cwd);
- ``--no-fs facts.json`` — injected facts for deterministic CI:
  ``{project, sha, paths[], declared_scope[]?, flags[]?}``;
- ``--declared scope.json`` — ex-ante over a declared scope:
  ``{project, sha, scope[], flags[]?}``.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from pathlib import Path

import typer

from steward.riskclassify.classify import Classification, classify_declared, classify_diff
from steward.riskclassify.model import RiskModelError, load_risk_model

app = typer.Typer(add_completion=False, help="steward: risk model tooling (WS-006)")


@app.callback()
def _root() -> None:
    """Keep `risk-classify` a named subcommand even while it is the only one."""


_EXIT_CONFIG = 2
_DEFAULT_MODEL = Path("profiles/risk-model.yaml")


class InputError(Exception):
    """Input file is missing required fields or cannot be parsed."""


@app.command("risk-classify")
def risk_classify(
    diff: str | None = typer.Option(None, "--diff", help="BASE..HEAD range for live git"),
    no_fs: Path | None = typer.Option(None, "--no-fs", help="facts.json (deterministic CI)"),
    declared: Path | None = typer.Option(None, "--declared", help="scope.json (ex-ante)"),
    risk_model: Path = typer.Option(_DEFAULT_MODEL, "--risk-model", help="risk-model.yaml"),
    repo: Path = typer.Option(Path("."), "--repo", help="repo root for --diff"),
    project: str | None = typer.Option(None, "--project", help="project name for --diff"),
    profile: str = typer.Option("lite", "--profile", help="floor profile"),
) -> None:
    """Classify a change (ex-post) or a declared scope (ex-ante) into a risk tier."""
    sources = [s for s in (diff, no_fs, declared) if s is not None]
    if len(sources) != 1:
        typer.echo("exactly one of --diff / --no-fs / --declared is required", err=True)
        raise typer.Exit(_EXIT_CONFIG)
    try:
        model = load_risk_model(risk_model)
        if declared is not None:
            data = _read_json(declared, required=("project", "sha", "scope"))
            result = classify_declared(
                model,
                project=data["project"],
                scope=data["scope"],
                sha=data["sha"],
                profile=profile,
                flags=data.get("flags"),
            )
        elif no_fs is not None:
            data = _read_json(no_fs, required=("project", "sha", "paths"))
            result = classify_diff(
                model,
                project=data["project"],
                paths=data["paths"],
                sha=data["sha"],
                profile=profile,
                flags=data.get("flags"),
                declared_scope=data.get("declared_scope"),
            )
        else:
            assert diff is not None
            result = _classify_live(model, diff, repo, project, profile)
    except (RiskModelError, InputError, ValueError) as exc:
        typer.echo(f"config error: {exc}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from exc
    typer.echo(_render(result))


@app.command("waivers-check")
def waivers_check(
    waivers_dir: Path = typer.Argument(Path("spec/waivers"), help="waiver files directory"),
    sha: str | None = typer.Option(None, "--sha", help="head SHA (default: git HEAD of --repo)"),
    repo: Path = typer.Option(Path("."), "--repo", help="repo root for live git HEAD"),
    risk_model: Path = typer.Option(_DEFAULT_MODEL, "--risk-model", help="risk-model.yaml"),
) -> None:
    """Validate waiver files: parse strictly, flag stale/forbidden ones (REQ-609).

    Exit codes mirror gate-check: 0 clean, 1 findings, 2 config error. A
    missing directory is clean — no waivers, nothing to validate.
    """
    from steward.riskclassify.waivers import FULL_SHA_RE, load_waivers, validate_waivers

    try:
        model = load_risk_model(risk_model)
        head = sha if sha is not None else _git(repo, "rev-parse", "HEAD").strip()
        if not FULL_SHA_RE.fullmatch(head):
            raise InputError(f"--sha must be a full 40-hex commit SHA, got '{head}'")
        try:
            waivers = load_waivers(waivers_dir, strict=True)
        except ValueError as exc:
            typer.echo(f"error waiver-malformed: {exc}")
            raise typer.Exit(1) from exc
    except (RiskModelError, InputError) as exc:
        typer.echo(f"config error: {exc}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from exc
    findings = validate_waivers(waivers, model, head_sha=head)
    for f in findings:
        typer.echo(f"{f.severity} {f.rule_id}: {f.path}: {f.message}")
    if any(f.severity == "error" for f in findings):
        raise typer.Exit(1)
    typer.echo(f"ok: {len(waivers)} waiver(s) valid for {head[:12]}")


def _classify_live(
    model, diff: str, repo: Path, project: str | None, profile: str
) -> Classification:
    if ".." not in diff:
        raise InputError(f"--diff expects BASE..HEAD, got '{diff}'")
    paths = _git(repo, "diff", "--name-only", diff).splitlines()
    head = diff.split("..")[-1] or "HEAD"
    sha = _git(repo, "rev-parse", head).strip()
    name = project or repo.resolve().name
    return classify_diff(
        model, project=name, paths=[p for p in paths if p], sha=sha, profile=profile
    )


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InputError(f"git {' '.join(args)} failed: {exc}") from exc


# Field -> expected shape for both input files; wrong types must be a config
# error (exit 2), not a TypeError from inside the classifier.
_STR_FIELDS = ("project", "sha")
_LIST_FIELDS = ("paths", "scope", "declared_scope", "flags")


def _read_json(path: Path, *, required: tuple[str, ...]) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise InputError(f"{path}: top level must be an object")
    missing = [k for k in required if k not in data]
    if missing:
        raise InputError(f"{path}: missing required fields {missing}")
    for key in _STR_FIELDS:
        if key in data and not isinstance(data[key], str):
            raise InputError(f"{path}: '{key}' must be a string")
    for key in _LIST_FIELDS:
        value = data.get(key)
        if value is not None and (
            not isinstance(value, list) or not all(isinstance(v, str) for v in value)
        ):
            raise InputError(f"{path}: '{key}' must be a list of strings")
    return data


def _render(result: Classification) -> str:
    # sort_keys + fixed separators: byte-identical output is part of the
    # contract (REQ-610) — Maestro and CI may diff two runs directly.
    return json.dumps(asdict(result), sort_keys=True, indent=2)
