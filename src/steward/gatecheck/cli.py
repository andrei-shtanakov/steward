"""gate-check CLI: aggregate findings, map to CI exit codes (WS-002, REQ-207).

Exit codes: ``0`` clean (warnings allowed) · ``1`` error findings · ``2``
config error (bad profile, bad facts file, missing bundle dir).
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer
import yaml

from steward.approvalfacts.publish import FACTS_RELPATH, ConfigError, parse_origin
from steward.gatecatalog import CatalogError, GateCatalog, load_catalog
from steward.gatecheck.approval import (
    ApprovalPolicy,
    FactsOutcome,
    FactsUnavailable,
    PolicyError,
    check_approval_evidence,
    load_approval_policy,
    resolve_facts,
)
from steward.gatecheck.architecture import (
    ArchPolicyError,
    check_arch_conformance,
    check_arch_evidence,
    check_arch_schema,
    collect_arch_bundle,
    load_arch_policy,
)
from steward.gatecheck.candidate import NOT_EVALUATED, CandidateGitFacts
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
from steward.graph import ProfileError, load_profile
from steward.roleassignments import AssignmentsError, load_role_assignments
from steward.roles import RolesCatalog, RolesError, load_roles_catalog
from steward.verdicts import EmitError, emit_verdicts

app = typer.Typer(add_completion=False, help=__doc__)

_EXIT_FINDINGS = 1
_EXIT_CONFIG = 2

_STAGES = frozenset({"authoring", "release"})
_DEFAULT_STAGE = "authoring"

# Where this run's facts came from. Reported in both renderers so a consumer
# never has to infer from flags what the exit code actually covered.
_MODE_LIVE = "live"
_MODE_INJECTED = "injected"
_MODE_CANDIDATE = "candidate"


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


def _resolve_profile_path(profile: str) -> Path:
    """Resolve a profile name/path to the YAML path (no loading).

    The path anchors sibling policy files (arch-policy.yaml, roles.yaml) so
    they resolve relative to the profiles directory actually used, not the
    current CWD.
    """
    candidate = Path(profile)
    if not candidate.is_file():
        candidate = Path("profiles") / f"{profile}.yaml"
    if not candidate.is_file():
        _fail_config(f"profile {profile!r} not found (looked for {candidate})")
    return candidate


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
    ``_resolve_profile_path`` documents for arch-policy.yaml.
    """
    try:
        return load_catalog(profile_path.parent / "gate-catalog.yaml", roles)
    except (CatalogError, OSError, yaml.YAMLError) as err:
        _fail_config(str(err))
        raise AssertionError from None  # unreachable; keeps type-checkers calm


def _approval_policy_path(profile_path: Path) -> Path:
    """``approval-policy.yaml`` anchored to ``profile_path``'s directory.

    Same anchoring discipline as ``_load_catalog``/arch-policy.yaml — resolves
    relative to the profile directory actually in use, not the current CWD.
    One helper rather than two literals: ``policy_digest`` (table row 3) is
    computed over the bytes of exactly the file the policy was loaded from, and
    a second spelling of the path could silently drift from the first.
    """
    return profile_path.parent / "approval-policy.yaml"


def _load_approval_policy(policy_path: Path) -> ApprovalPolicy:
    """Load the anchored ``approval-policy.yaml``, mapping defects to exit 2."""
    try:
        return load_approval_policy(policy_path)
    except PolicyError as err:
        _fail_config(str(err))
        raise AssertionError from None  # unreachable; keeps type-checkers calm


def _git_out(cwd: Path, *args: str) -> str | None:
    proc = subprocess.run(  # noqa: S603 S607 — fixed argv, no user input
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def approval_facts_outcome(
    explicit: Path | None,
    spec_dir: Path,
    *,
    policy: ApprovalPolicy,
    policy_path: Path,
    now: datetime,
) -> FactsOutcome:
    """Resolve the merge-evidence source for this run (§8.4).

    Two explicitly chosen input paths, one format: ``--approval-facts`` is an
    operator override (and therefore accepts **only** v2 — an invalid file
    there is a call error, per §8.3.1), and without it the path is the bundle
    default ``<repo-root>/.steward/approval_facts.jsonl``, next to
    ``gate_verdicts.jsonl``.

    ``expected_repository`` is derived from the checkout's **``origin``** — the
    remote named ``origin``, not "any remote that fits": working copies in this
    fleet carry several remotes for one repository, including mirrors on other
    forges. It is needed for both paths, because reader invariant 11 is what
    stops a file from a *different* repository that happens to contain the same
    merge SHA from influencing enforcement.

    A repository we cannot identify (bundle outside a git repo, no ``origin``,
    unparseable ``origin``) yields ``FactsUnavailable("absent")`` rather than
    aborting the run: the gate must not crash, and "no usable evidence" is
    already the safe, fail-closed state.

    **This identification check runs unconditionally, before ``explicit`` is
    even consulted — so the explicit override is inert whenever the checkout
    can't be identified, full stop.** It is not only that an *invalid* file
    at that path reads as ``FactsUnavailable("absent")`` instead of the
    §8.3.1-promised config error, exit 2 (Codex gate round 2 on PR #86 named
    that half). A **perfectly valid** ``approval-facts/v2`` file passed via
    ``--approval-facts`` on an unpacked bundle, in a non-git directory, or in
    a checkout with no ``origin`` is *never read at all* — §8.4 calls
    ``--approval-facts`` an override, and outside an identified checkout it
    isn't one (Codex gate round 3 sharpened this from "the invalid case is
    misclassified" to "the whole path is unreachable there"). Left as-is
    rather than reordered: making the explicit path win here would require
    validating that file's content — including reader invariant 11
    (``header.repository`` matches the checkout) — against a repository
    identity we by definition don't have in this branch. Closing that gap
    needs a new way for the caller to *declare* the expected repository when
    none can be derived from ``origin`` (e.g. an explicit ``--repo`` companion
    to ``--approval-facts``) — a new CLI option and an owner decision on its
    shape, not a fix that belongs at the end of a workstream. The failure
    direction is still correct either way — every outcome here is a finding,
    never a silent pass — only the *class* of error, and whether the override
    can be used at all outside a checkout, is what §8.3.1 would prefer and
    doesn't get here. See ``approval-facts-explicit-path-subordinate-to-repo-id``
    in ``TODO.md``.
    """
    top = _git_out(spec_dir, "rev-parse", "--show-toplevel")
    if top is None:
        return FactsUnavailable("absent", f"{spec_dir} не внутри git-репозитория")
    origin = _git_out(Path(top), "remote", "get-url", "origin")
    if origin is None:
        return FactsUnavailable("absent", f"{top}: у чекаута нет remote 'origin'")
    try:
        owner, name = parse_origin(origin)
    except ConfigError as err:
        return FactsUnavailable("absent", f"{top}: origin не разбирается — {err}")

    path = explicit if explicit is not None else Path(top) / FACTS_RELPATH
    return resolve_facts(
        path,
        expected_repository=f"{owner}/{name}",
        policy=policy,
        policy_path=policy_path,
        now=now,
        explicit=explicit is not None,
    )


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


def _render_text(findings: list[Finding], mode: str) -> None:
    for finding in findings:
        typer.echo(
            f"{finding.severity.upper():5} {finding.rule_id:16} "
            f"{finding.artifact}: {finding.message}"
        )
    errors = sum(1 for f in findings if f.severity == "error")
    warns = len(findings) - errors
    typer.echo(f"gate-check[{mode}]: {errors} error(s), {warns} warning(s)")


def _echo_not_evaluated() -> None:
    """Declare the gates a prospective run structurally could not reach.

    On **stderr**, and emitted for every output branch — including
    ``--trace-matrix``, whose stdout is a derived view rather than a findings
    list. stdout stays exactly one parseable payload (findings text, findings
    JSON, or the matrix) while the declaration reaches a human in all three;
    the machine-readable copy rides in the JSON payload's ``not_evaluated``.
    Silence here would be the fail-open the mode exists to avoid: an exit code
    that looks like every gate ran.
    """
    typer.echo("не проверено в prospective-режиме:", err=True)
    for gate_id, reason in NOT_EVALUATED:
        typer.echo(f"  {gate_id:20} {reason}", err=True)


def _render_json(findings: list[Finding], mode: str) -> None:
    payload: dict[str, object] = {
        "mode": mode,
        "findings": [vars(f) for f in findings],
        "errors": sum(1 for f in findings if f.severity == "error"),
        "warnings": sum(1 for f in findings if f.severity == "warn"),
    }
    # Only the prospective run declares this: the list is a property of the
    # MODE (which gates it structurally cannot reach), not a per-run audit.
    # Emitting `[]` for a ref-bound run would read as "everything else ran",
    # which is a claim gate-check is not making — a gate can be inapplicable
    # to a bundle for reasons that have nothing to do with git.
    if mode == _MODE_CANDIDATE:
        payload["not_evaluated"] = [
            {"gate": gate_id, "reason": reason} for gate_id, reason in NOT_EVALUATED
        ]
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
    candidate: bool = typer.Option(
        False,
        "--candidate",
        help="Prospective mode: lint the directory as a CANDIDATE revision — "
        "content that is not a git ref yet (uncommitted files, an assembled "
        "bundle). Runs the content-addressed gates, including the stale "
        "cascade; ref-bound gates are reported as not evaluated, never as "
        "passed. Does not require a git checkout. Incompatible with --no-fs, "
        "--emit-verdicts, --approval-facts and --stage release.",
    ),
    approval_facts: Path | None = typer.Option(
        None,
        "--approval-facts",
        help="Override the materialized merge-actor evidence path "
        "(approval-facts/v2 only). Default: <repo-root>/.steward/"
        "approval_facts.jsonl. Consulted at --stage release.",
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

    # Every conflict below is one shape of the same defect: a flag that only
    # means something about a committed revision, asked of content that has
    # none. Rejected as a config error (exit 2) rather than quietly ignored —
    # an ignored --approval-facts would look like evidence was consulted.
    if candidate:
        if no_fs is not None:
            _fail_config("--candidate and --no-fs are two different fact sources")
        if emit_verdicts_flag:
            _fail_config(
                "--emit-verdicts needs live git provenance (HEAD + dirty state) "
                "and cannot run under --candidate"
            )
        if approval_facts is not None:
            _fail_config(
                "--approval-facts is merge evidence keyed by merge SHA; a candidate "
                "revision has not been merged"
            )
        if resolved_stage == "release":
            _fail_config(
                "--stage release consults merge evidence, which a candidate revision "
                "cannot have — run the candidate at --stage authoring"
            )

    profile_path = _resolve_profile_path(profile)
    roles_catalog = _load_roles(profile_path)
    try:
        graph = load_profile(profile_path, roles_catalog)
    except ProfileError as err:
        _fail_config(str(err))
        raise AssertionError from None  # unreachable; keeps type-checkers calm

    assignments = None
    if not graph.solo_auto_approve:
        try:
            assignments = load_role_assignments(
                profile_path.parent / "role-assignments.yaml", roles_catalog
            )
        except AssignmentsError as err:
            _fail_config(str(err))
            raise AssertionError from None  # unreachable; keeps type-checkers calm

    if candidate:
        git: GitFacts = CandidateGitFacts(spec_dir)
        mode = _MODE_CANDIDATE
    else:
        git = _git_facts(no_fs, spec_dir)
        mode = _MODE_INJECTED if no_fs is not None else _MODE_LIVE

    artifacts, findings = collect_bundle(graph, spec_dir)

    role_problems = unresolved_role_refs(artifacts, roles_catalog)
    if role_problems:
        roles_path = profile_path.parent / "roles.yaml"
        _fail_config("\n".join([*role_problems, f"roles catalog: {roles_path}"]))

    try:
        findings.extend(run_checks(graph, artifacts, git, assignments, prospective=candidate))
    except FactsError as err:
        _fail_config(str(err))
        raise AssertionError from None  # unreachable; keeps type-checkers calm

    if resolved_stage == "release":
        approval_policy_path = _approval_policy_path(profile_path)
        approval_policy = _load_approval_policy(approval_policy_path)
        try:
            facts = approval_facts_outcome(
                approval_facts,
                spec_dir,
                policy=approval_policy,
                policy_path=approval_policy_path,
                now=datetime.now(UTC),
            )
        except (ConfigError, PolicyError) as err:
            # §8.3.1: an invalid file the operator pointed at explicitly is a
            # call error, not an observed property of the environment. A policy
            # file that became unreadable between load and digest is the same
            # class of failure — a broken input, not an observed actor.
            _fail_config(str(err))
            raise AssertionError from None  # unreachable; keeps type-checkers calm
        try:
            findings.extend(
                check_approval_evidence(
                    artifacts, git, approval_policy, facts, stage=resolved_stage
                )
            )
        except FactsError as err:
            _fail_config(str(err))
            raise AssertionError from None  # unreachable; keeps type-checkers calm

    arch = collect_arch_bundle(spec_dir)
    if arch is not None:
        findings.extend(check_arch_schema(arch))
        findings.extend(check_arch_evidence(arch))
        # Schema and evidence-presence are content questions and stay on; the
        # conformance verdict is compared against self-freshness read from
        # history, which a candidate has none of (NOT_EVALUATED). arch-policy.yaml
        # is loaded inside this branch and not above it: it is the conformance
        # gate's input and nothing else's, so a prospective run must not fail
        # with exit 2 over a policy file it was never going to consult.
        if not candidate:
            try:
                policy = load_arch_policy(profile_path.parent / "arch-policy.yaml", resolved_stage)
            except ArchPolicyError as err:
                _fail_config(str(err))
                raise AssertionError from None  # unreachable; keeps type-checkers calm
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
        _render_json(findings, mode)
    else:
        _render_text(findings, mode)

    if mode == _MODE_CANDIDATE:
        _echo_not_evaluated()

    if any(f.severity == "error" for f in findings):
        raise typer.Exit(_EXIT_FINDINGS)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
