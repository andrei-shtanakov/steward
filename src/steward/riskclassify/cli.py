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
import os
import re
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import typer

from steward.riskclassify.classify import Classification, classify_declared, classify_diff
from steward.riskclassify.model import RiskModelError, load_risk_model

app = typer.Typer(
    add_completion=False,
    help="steward: governance tooling — risk model, waivers, approval-facts",
)


@app.callback()
def _root() -> None:
    """Keep `risk-classify` a named subcommand even while it is the only one."""


_EXIT_CONFIG = 2
_EXIT_MATERIALIZE_FAILED = 3
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


#: Форма `--merge-sha`, та же, что и везде в контракте v2 (см.
#: `steward.approvalfacts.reader._MERGE_SHA_RE`): 40-символьный hex в
#: нижнем регистре.
_MERGE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@app.command("approval-facts")
def approval_facts(
    repo: str = typer.Option(..., "--repo", help="owner/name на форже"),
    repo_root: Path = typer.Option(
        Path("."), "--repo-root", help="Чекаут наблюдаемого репозитория (для пути бандла)."
    ),
    out: Path | None = typer.Option(
        None, "--out", help="Явный override пути; без него пишется в бандл."
    ),
    policy: Path | None = typer.Option(None, "--policy", help="Файл политики классификации."),
    merge_sha: list[str] = typer.Option([], "--merge-sha", help="Merge SHA (повторяемо)."),
    prs: str | None = typer.Option(None, "--prs", help="Номера PR через запятую."),
) -> None:
    """Материализовать `approval-facts/v2` и опубликовать его.

    Порядок здесь — не деталь реализации, а требование §8.4 спеки
    approval-facts/v2: полный config preflight (шаги 1-5) обязан пройти
    целиком ДО первого разрушающего действия (шаг 6, `remove_previous`).
    Ошибка на любом из шагов 1-5 — config error, прежняя публикация не
    тронута; ошибка после шага 6 (механический сбой материализации)
    оставляет источник отсутствующим, а не тихо стухшим.

    Exit: ``0`` опубликовано, ``2`` ошибка конфигурации (прежняя публикация
    НЕ тронута), ``3`` механический сбой материализации ИЛИ публикации
    (файла нет в обоих случаях — ``publish()`` тоже происходит после шага 6,
    так что типизированный отказ обязан покрывать и её).
    """
    from steward.approvalfacts.model import RequestId
    from steward.approvalfacts.producer import MechanicalFailure, classify_results, materialize
    from steward.approvalfacts.publish import (
        FACTS_RELPATH,
        ConfigError,
        NotAGitRepository,
        build_header,
        publish,
        remove_previous,
        resolve_repo_root,
    )
    from steward.gatecheck.approval import PolicyError, load_approval_policy
    from steward.gatecheck.approval import policy_digest as compute_policy_digest

    def parse_scope() -> list[RequestId]:
        scope: list[RequestId] = []
        for sha in merge_sha:
            if not _MERGE_SHA_RE.fullmatch(sha):
                raise ConfigError(
                    f"--merge-sha обязан быть 40-символьным hex в нижнем регистре, получено {sha!r}"
                )
            scope.append(RequestId("merge_sha", sha))
        if prs:
            for token in (raw.strip() for raw in prs.split(",")):
                if not token:
                    continue
                try:
                    number = int(token)
                except ValueError as exc:
                    raise ConfigError(
                        f"--prs обязан быть списком целых чисел через запятую, получено {token!r}"
                    ) from exc
                if number <= 0:
                    raise ConfigError(
                        f"--prs: номер PR обязан быть положительным, получено {number}"
                    )
                scope.append(RequestId("pr", number))
        if not scope:
            raise ConfigError("нужен хотя бы один идентификатор: --merge-sha или --prs")
        if len(set(scope)) != len(scope):
            raise ConfigError("объявленный scope содержит дублирующиеся элементы")
        return scope

    try:
        # 1: форма --repo. Ровно один `/`, а не «хотя бы один»: `.partition("/")`
        # брал только первый слэш, так что `owner/repo/extra` тоже проходил
        # (owner="owner", name="repo/extra") — config-опечатка, которую
        # ничто ниже гарантированно не ловит ДО шага 6 (`resolve_repo_root`
        # в bundle-default пути сравнивает через `split("/", 1)`, ту же
        # laxность, и в `--out`-ветке при откате `NotAGitRepository` сверки
        # origin вообще нет) — значит прежняя публикация могла быть удалена,
        # а на её месте материализован невалидный `header.repository`,
        # который отверг бы только последующий читатель, не эта команда
        # (Codex gate round 4 на PR #86, blocker).
        repo_parts = repo.split("/")
        if len(repo_parts) != 2 or not repo_parts[0] or not repo_parts[1]:
            raise ConfigError(f"--repo обязан быть в форме 'owner/name', получено {repo!r}")
        owner, name = repo_parts

        # 2: цель — bundle-target (git-root + сверка origin) либо явный
        #    --out (сверка origin пропущена, путь всё равно валидируется).
        #
        # `policy_root` — тот же самый якорь, из которого по умолчанию
        # берётся `--policy`, а не пересчитанный отдельно: `--repo-root`
        # может быть ЛЮБЫМ подкаталогом чекаута (например, `spec/`), и
        # `resolve_repo_root` уходит к настоящему git top-level. Если бы
        # дефолт политики считался от сырого `repo_root`, а цель публикации —
        # от резолвленного корня, они разъехались бы на два разных каталога
        # ровно при `--repo-root <подкаталог>` — найдено Codex-гейтом на
        # PR #86, см. TODO.md/final-fix-report.md.
        if out is not None:
            target = Path(out)
            parent = target.parent
            if not parent.is_dir():
                raise ConfigError(f"--out: родительский каталог {parent} не существует")
            if not os.access(parent, os.W_OK):
                raise ConfigError(f"--out: родительский каталог {parent} недоступен для записи")
            # `--out` явно освобождён от сверки origin — это законный режим
            # публикации вне git-чекаута, и требовать git там, где команда
            # обещала его не требовать, было бы неверно. Но ВНУТРИ чекаута
            # `--out` не обязан вести себя иначе, чем bundle-default путь:
            # если `resolve_repo_root` находит настоящий git top-level,
            # дефолт политики анкорится на нём же — иначе `--repo-root
            # <подкаталог>` работал бы в режиме бандла и падал в режиме
            # `--out`, хотя политика в обоих случаях лежит в одном и том же
            # настоящем корне (Codex gate round 3 на PR #86: прошлая версия
            # этой правки безусловно анкорила `--out` на сыром `repo_root`,
            # что и создавало эту асимметрию — принятое ранее обоснование
            # «резолвинг обязателен только вне отката» было ошибкой
            # ревью-приёмки, не результатом резолвинга без отката). Откат на
            # сырой `repo_root` срабатывает ТОЛЬКО когда `repo_root` вообще
            # не внутри git-репозитория (`NotAGitRepository`) — отсутствующий
            # или несовпавший `origin` внутри настоящего git-чекаута
            # остаётся config error и наружу, а не тихо маскируется под
            # «просто нет git».
            #
            # Но всё это нужно резолвить ТОЛЬКО когда дефолт политики вообще
            # будет использован — то есть когда `--policy` не передан явно.
            # Раньше `resolve_repo_root` звалась безусловно, даже если
            # оператор УЖЕ дал и `--out`, и `--policy` явно — тогда `--repo`
            # и origin текущего каталога вообще не участвуют ни в цели, ни в
            # политике, но команда всё равно падала на несовпадении origin,
            # если `repo_root` оказывался чекаутом ДРУГОГО репозитория
            # (например, тестовый прогон из чужого checkout'а с полностью
            # explicit `--out`/`--policy`) — Codex gate round 6 на PR #86.
            policy_root: Path | None = None
            if policy is None:
                try:
                    policy_root = resolve_repo_root(repo, repo_root)
                except NotAGitRepository:
                    policy_root = repo_root
        else:
            policy_root = resolve_repo_root(repo, repo_root)
            target = policy_root / FACTS_RELPATH

        # Цель не может быть уже существующим каталогом: `remove_previous`
        # (шаг 6) зовёт `unlink(missing_ok=True)`, а `missing_ok` гасит только
        # `FileNotFoundError` — на каталоге `unlink()` поднимает
        # `IsADirectoryError`, необработанную здесь, и после ЧАСТИЧНО
        # пройденного preflight это была бы трассировка вместо кода выхода.
        # Это вход конфигурации (путь назвал оператор), значит config error,
        # exit 2, ДО любого разрушающего действия — Codex gate round 2 на
        # PR #86.
        if target.is_dir():
            raise ConfigError(f"цель публикации {target} уже существует как каталог")

        # 3: объявленный scope — непуст, без дублей, форма.
        scope = parse_scope()

        # 4: политика + policy_digest. 5: approval_facts_lease_seconds
        #    валидируется внутри load_approval_policy.
        policy_path = (
            policy if policy is not None else policy_root / "profiles" / "approval-policy.yaml"
        )
        approval_policy = load_approval_policy(policy_path)
        digest = compute_policy_digest(policy_path)
    except (ConfigError, PolicyError) as exc:
        typer.echo(f"config error: {exc}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from exc

    # 6: только теперь разрушающее действие — весь preflight уже прошёл.
    # `remove_previous` само может отказать (`unlink`/`_fsync_dir` на
    # read-only каталоге, ФС-ошибка) — это post-preflight I/O, тот же класс,
    # что materialize()/publish(), и обязано давать тот же типизированный
    # отказ, а не сырое исключение (Codex gate round 4 на PR #86).
    try:
        remove_previous(target)
    except OSError as exc:
        typer.echo(f"approval-facts remove_previous failed: {exc}", err=True)
        raise typer.Exit(_EXIT_MATERIALIZE_FAILED) from exc
    try:
        results = materialize(repo, scope)
    except MechanicalFailure as exc:
        typer.echo(f"approval-facts materialize failed: {exc}", err=True)
        raise typer.Exit(_EXIT_MATERIALIZE_FAILED) from exc

    results = classify_results(results, approval_policy)
    header = build_header(
        repository=repo,
        scope=scope,
        policy_version=approval_policy.version,
        policy_digest_value=digest,
        lease_seconds=approval_policy.approval_facts_lease_seconds,
        now=datetime.now(UTC),
    )
    # `publish()` тоже происходит после шага 6 (previous публикация уже
    # снята) — ENOSPC/EIO/сбой os.replace или fsync во время записи обязан
    # давать тот же типизированный отказ, что и сбой materialize(), а не
    # сырую трассировку в месте, где мы обещали ровно два кода ошибки —
    # Codex gate round 3 на PR #86.
    try:
        publish(target, header, results)
    except OSError as exc:
        typer.echo(f"approval-facts publish failed: {exc}", err=True)
        raise typer.Exit(_EXIT_MATERIALIZE_FAILED) from exc
    typer.echo(f"ok: {len(results)} result(s) published to {target}")


@app.command("verdicts-verify")
def verdicts_verify(
    file: Path = typer.Argument(
        Path(".steward/gate_verdicts.jsonl"),
        help="gate_verdicts.jsonl to verify (default: the bundle emission path)",
    ),
) -> None:
    """Verify the hash chain of a `gate_verdicts.jsonl` ledger (steward#105).

    Three outcomes: ``chained`` (every record from the first carrier on links
    to its predecessor), ``legacy`` (every line parses and no ``prev_hash``
    anywhere — valid by the contract's additive rule, files predating the
    field), ``broken`` (a substituted or chain-dropping line, or ANY
    unparseable line — corrupt input never verifies as valid).

    Exit codes mirror gate-check: ``0`` chained or legacy, ``1`` broken,
    ``2`` config error (file missing/unreadable). The chain proves mid-ledger
    integrity only — tail truncation or a wholesale rewrite with recomputed
    hashes needs an external anchor and is documented as out of scope in the
    contract README.
    """
    from steward.verdicts.chain import verify_chain

    try:
        text = file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        typer.echo(f"config error: cannot read {file}: {exc}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from exc
    report = verify_chain(text)
    if report.status == "broken":
        typer.echo(
            f"broken: line {report.broken_line} of {report.lines} — {report.reason}"
            + (f" (chained from line {report.chained_from})" if report.chained_from else "")
        )
        raise typer.Exit(1)
    if report.status == "legacy":
        typer.echo(f"legacy: {report.lines} line(s), no prev_hash — valid, chain not present")
        return
    typer.echo(f"chained: {report.lines} line(s), chain intact from line {report.chained_from}")


@app.command("proposal-intake")
def proposal_intake(
    bundle_dir: Path = typer.Argument(
        ...,
        help="proposal bundle: proposal.yaml + decisions/*.yaml (impresario layout)",
    ),
) -> None:
    """Admission check for an approved ProductProposal (steward#64).

    Evidence, not status: admit only when the proposal is a schema-valid
    ``product-proposal/v1`` with ``status: approved`` AND both QG-5 gates
    (``qg5_business``, ``qg5_committee``) have an active — not superseded —
    ``approve`` gate-decision/v1 referencing exactly this proposal. Exit
    codes mirror gate-check: 0 admit, 1 reject (findings), 2 config error.
    """
    from steward.proposalintake import IntakeConfigError, check_intake

    try:
        result = check_intake(bundle_dir)
    except IntakeConfigError as exc:
        typer.echo(f"config error: {exc}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from exc
    for f in result.findings:
        typer.echo(f"{f.severity} {f.rule_id}: {f.path}: {f.message}")
    if not result.admitted:
        typer.echo(f"reject: {result.proposal_id or 'proposal'} is not admissible")
        raise typer.Exit(1)
    typer.echo(
        f"admit: {result.proposal_id} v{result.proposal_version} "
        "(active approve for qg5_business + qg5_committee)"
    )


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
