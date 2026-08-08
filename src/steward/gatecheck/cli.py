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
import yaml

from steward.approvalfacts import ApprovalFactsError, load_approval_facts
from steward.gatecatalog import CatalogError, GateCatalog, load_catalog
from steward.gatecheck.approval import (
    ApprovalPolicy,
    PolicyError,
    check_approval_evidence,
    load_approval_policy,
)
from steward.gatecheck.architecture import (
    ArchPolicyError,
    check_arch_conformance,
    check_arch_evidence,
    check_arch_schema,
    collect_arch_bundle,
    load_arch_policy,
)
from steward.gatecheck.checks import Finding, collect_bundle, run_checks
from steward.gatecheck.git_facts import (
    FactsError,
    GitFacts,
    InjectedGitFacts,
    LiveGitFacts,
)
from steward.gatecheck.roles_refs import unresolved_role_refs
from steward.gatecheck.trace_matrix import (
    build_trace_matrix,
    render_matrix_json,
    render_matrix_text,
)
from steward.graph import ProfileError, SpecGraph, load_profile
from steward.roles import RolesCatalog, RolesError, load_roles_catalog
from steward.verdicts import EmitError, emit_verdicts

app = typer.Typer(add_completion=False, help=__doc__)

_EXIT_FINDINGS = 1
_EXIT_CONFIG = 2

_STAGES = frozenset({"authoring", "release"})
_DEFAULT_STAGE = "authoring"


def _fail_config(message: str) -> None:
    typer.echo(f"config error: {message}", err=True)
    raise typer.Exit(_EXIT_CONFIG)


def _resolve_stage(stage: str | None, arch_stage: str | None) -> str:
    """Normalize `--stage` and its deprecated alias `--arch-stage` (D4а).

    Both set with different values is a config error; both set with the
    same value is not a conflict; either one alone wins; neither set falls
    back to ``"authoring"``. The resolved value must be a known stage.
    """
    if stage is not None and arch_stage is not None and stage != arch_stage:
        _fail_config(f"--stage and --arch-stage conflict ({stage!r} vs {arch_stage!r})")
    resolved = stage if stage is not None else arch_stage
    if resolved is None:
        resolved = _DEFAULT_STAGE
    if resolved not in _STAGES:
        _fail_config(f"unknown stage {resolved!r} (expected one of {sorted(_STAGES)})")
    return resolved


def _resolve_profile(profile: str) -> tuple[SpecGraph, Path]:
    """Resolve a profile name/path to its graph AND the YAML path it loaded from.

    The path anchors sibling policy files (arch-policy.yaml) so they resolve
    relative to the profiles directory actually used, not the current CWD.
    """
    candidate = Path(profile)
    if not candidate.is_file():
        candidate = Path("profiles") / f"{profile}.yaml"
    if not candidate.is_file():
        _fail_config(f"profile {profile!r} not found (looked for {candidate})")
    try:
        return load_profile(candidate), candidate
    except ProfileError as err:
        _fail_config(str(err))
        raise AssertionError from None  # unreachable; keeps type-checkers calm


def _load_roles(profile_path: Path) -> RolesCatalog:
    """Load the roles catalog anchored to ``profile_path``'s directory.

    roles.yaml is a MANDATORY sibling of the profile on every run since
    DEC-007 D3 — role references in frontmatter are validated against it,
    so its absence is a configuration error, not a soft skip.
    """
    try:
        return load_roles_catalog(profile_path.parent / "roles.yaml")
    except RolesError as err:
        _fail_config(str(err))
        raise AssertionError from None  # unreachable; keeps type-checkers calm


def _load_catalog(profile_path: Path, roles: RolesCatalog) -> GateCatalog:
    """Load the gate-id catalog anchored to ``profile_path``'s directory, reusing ``roles``.

    Sibling files (gate-catalog.yaml) resolve relative to the profile
    directory actually in use, not the current CWD — the same anchoring
    ``_resolve_profile`` documents for arch-policy.yaml.
    """
    try:
        return load_catalog(profile_path.parent / "gate-catalog.yaml", roles)
    except (CatalogError, OSError, yaml.YAMLError) as err:
        _fail_config(str(err))
        raise AssertionError from None  # unreachable; keeps type-checkers calm


def _load_approval_policy(profile_path: Path) -> ApprovalPolicy:
    """Load ``approval-policy.yaml`` anchored to ``profile_path``'s directory.

    Same anchoring discipline as ``_load_catalog``/arch-policy.yaml — resolves
    relative to the profile directory actually in use, not the current CWD.
    """
    try:
        return load_approval_policy(profile_path.parent / "approval-policy.yaml")
    except PolicyError as err:
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
    trace_matrix: bool = typer.Option(
        False,
        "--trace-matrix",
        help="Render the derived requirement→scenario→check matrix instead of the "
        "findings list. Exit codes still reflect the findings.",
    ),
    emit_verdicts_flag: bool = typer.Option(
        False,
        "--emit-verdicts",
        help="Write the run's facts to <repo-root>/.steward/gate_verdicts.jsonl "
        "(contract gate-verdicts/v1). Requires live git provenance — incompatible "
        "with --no-fs. Exit codes still reflect the findings.",
    ),
    stage: str | None = typer.Option(
        None,
        "--stage",
        help="Governance stage: authoring | release. Selects the GC-ARCH-CONFORMANCE "
        "policy (profiles/arch-policy.yaml). Default: authoring.",
    ),
    arch_stage: str | None = typer.Option(
        None,
        "--arch-stage",
        help=r"\[deprecated alias of --stage]",
    ),
    approval_facts: Path | None = typer.Option(
        None,
        "--approval-facts",
        help="Materialized merge-actor evidence (schema approval-facts/v1, "
        "written by `steward approval-facts`). Only consulted at --stage "
        "release; absent means every merge actor is unavailable, not unknown.",
    ),
) -> None:
    """Lint a governance bundle against its profile's gates."""
    resolved_stage = _resolve_stage(stage, arch_stage)
    if output not in ("text", "json"):
        _fail_config(f"unknown format {output!r} (expected text or json)")
    if not spec_dir.is_dir():
        _fail_config(f"bundle directory not found: {spec_dir}")

    if emit_verdicts_flag and no_fs is not None:
        _fail_config(
            "--emit-verdicts needs live git provenance (HEAD + dirty state) "
            "and cannot run under --no-fs"
        )

    graph, profile_path = _resolve_profile(profile)
    git = _git_facts(no_fs, spec_dir)

    artifacts, findings = collect_bundle(graph, spec_dir)

    roles_catalog = _load_roles(profile_path)
    role_problems = unresolved_role_refs(artifacts, roles_catalog)
    if role_problems:
        roles_path = profile_path.parent / "roles.yaml"
        _fail_config("\n".join([*role_problems, f"roles catalog: {roles_path}"]))

    findings.extend(run_checks(graph, artifacts, git))

    if resolved_stage == "release":
        approval_policy = _load_approval_policy(profile_path)
        actor_facts = None
        if approval_facts is not None:
            try:
                actor_facts = load_approval_facts(approval_facts)
            except ApprovalFactsError as err:
                _fail_config(str(err))
                raise AssertionError from None  # unreachable; keeps type-checkers calm
        try:
            findings.extend(
                check_approval_evidence(
                    artifacts, git, approval_policy, actor_facts, resolved_stage
                )
            )
        except FactsError as err:
            _fail_config(str(err))
            raise AssertionError from None  # unreachable; keeps type-checkers calm

    arch = collect_arch_bundle(spec_dir)
    if arch is not None:
        try:
            policy = load_arch_policy(profile_path.parent / "arch-policy.yaml", resolved_stage)
        except ArchPolicyError as err:
            _fail_config(str(err))
            raise AssertionError from None  # unreachable; keeps type-checkers calm
        findings.extend(check_arch_schema(arch))
        findings.extend(check_arch_evidence(arch))
        findings.extend(check_arch_conformance(arch, policy, git))

    if emit_verdicts_flag:
        catalog = _load_catalog(profile_path, roles_catalog)
        try:
            out_path = emit_verdicts(graph, artifacts, findings, spec_dir, catalog)
        except EmitError as err:
            _fail_config(str(err))
        else:
            typer.echo(f"verdicts written: {out_path}", err=True)

    if trace_matrix:
        matrix = build_trace_matrix(graph, artifacts)
        if matrix is None:
            _fail_config(
                f"profile {graph.profile!r} has no behaviour-spec node, or the bundle "
                "is missing the behaviour-spec / its upstream artifact"
            )
            raise AssertionError from None  # unreachable; keeps type-checkers calm
        renderer = render_matrix_json if output == "json" else render_matrix_text
        typer.echo(renderer(matrix))
    elif output == "json":
        _render_json(findings)
    else:
        _render_text(findings)

    if any(f.severity == "error" for f in findings):
        raise typer.Exit(_EXIT_FINDINGS)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
