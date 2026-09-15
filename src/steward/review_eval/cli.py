"""`review-eval` CLI — корпус, материализация, прогон, метрики, сравнение (§11).

Коды выхода (дизайн §11, одинаково для всех подкоманд):

- ``0`` — успех;
- ``1`` — прогон завершён, но есть `unexpected_outcome` или вариант со
  статусом `pending_adjudication` (информационно, как у гейта);
- ``2`` — конфигурация: корпус невалиден, объект не материализован в кэше,
  вариант не поддержан китом, аргументы противоречивы;
- ``3`` — механический сбой самого инструмента (в т.ч. артефакты прогона,
  не сходящиеся с `result.json`).

`run` **никогда** не касается сети: единственная сетевая команда —
`corpus materialize` (и чтение истории в `corpus candidates`). Сеть для
`run` заменена отказом кода 2 «объекта нет в кэше» — так измерение не может
молча подтянуть другое дерево (§5, §6).
"""

from __future__ import annotations

import functools
import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import typer

from steward.review_eval.cache import CacheError, CacheUnavailable, materialize, repo_cache_dir
from steward.review_eval.candidates import (
    AI_PROSTO,
    CandidatesError,
    commits_after,
    draft_case,
    fetch_commits,
    fetch_pr,
    fetch_reviews,
    render_case,
    resolve_review_base,
    review_head,
)
from steward.review_eval.corpus import (
    Case,
    CorpusError,
    append_registry,
    corpus_digest,
    is_gold,
    load_case,
    load_corpus,
    registry_path,
)
from steward.review_eval.matcher import MATCHER_VERSION, normalize_path, rules_digest
from steward.review_eval.metrics import CaseEval, MetricsError, compare, evaluate_case
from steward.review_eval.metrics import metrics_for_variant as summarize_variant
from steward.review_eval.report import (
    render_compare,
    write_metrics_json,
    write_queue,
    write_report,
)
from steward.review_eval.runner import (
    RunManifest,
    RunnerError,
    Variant,
    kit_under_test,
    load_results,
    parse_variant,
    pin_git,
    run_all,
    scrubbed_git_env,
    utc_now,
    variant_label,
)

app = typer.Typer(
    add_completion=False,
    help="review-eval: измеримый eval review-kit (корпус, прогон, метрики)",
)
corpus_app = typer.Typer(add_completion=False, help="корпус кейсов (schema review-eval-case/v1)")
app.add_typer(corpus_app, name="corpus")

_EXIT_OK = 0
_EXIT_ATTENTION = 1
_EXIT_CONFIG = 2
_EXIT_MECHANICAL = 3

_DEFAULT_CORPUS = Path("eval/corpus")
_DEFAULT_CACHE = Path("eval/cache")
_DEFAULT_WORKSPACE_ROOT = Path("..")

#: Статус варианта, требующий разбора очереди перед публикацией precision (D9).
_PENDING = "pending_adjudication"


#: Подписи отказа git, означающие «объекта/пути в дереве нет» (код 128). Всё
#: остальное — отказ самого git (нет бинаря, кэш не репозиторий, падение), и
#: путать эти два случая нельзя: первое — факт о данных, второе — о конфигурации.
_MISSING_OBJECT_SIGNATURES: tuple[bytes, ...] = (b"does not exist", b"not a valid object name")

#: Форма sha коммита — та же, что требует корпус: 40 строчных hex.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _require_git(git: str) -> None:
    """Пред-проверка `--git`: бинарь есть и запускается (``git --version``).

    Одна проверка в начале команды вместо разбора отказов по ходу. Неверный
    `--git` иначе всплывает там, где его легко принять за факт о данных:
    `_file_lines_at` читал «git не запустился» как «файла нет» и молча обнулял
    `resolvable_evidence_rate`, то есть публиковал метрику без данных как
    метрику с данными.
    """
    try:
        result = subprocess.run(  # noqa: S603 — argv фиксирован, без shell
            [git, "--version"], capture_output=True, text=True, check=False
        )
    except OSError as error:
        raise RunnerError(f"--git '{git}' не запускается: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or f"код выхода {result.returncode}"
        raise RunnerError(f"--git '{git}' не работает: {detail}")


#: Подсказка в отказе по дрейфу матчера — флаг, которым пересчёт разрешают явно.
_DRIFT_FLAG = "--allow-matcher-drift"


def _matcher_drift(manifest: Mapping[str, object]) -> list[str]:
    """Поля матчера, в которых манифест прогона расходится с текущим кодом.

    Сопоставление делает **текущий** матчер, а шапку отчёта даёт манифест
    прогона: без этой проверки `metrics` печатал бы версию и дайджест правил,
    которых опубликованные числа никогда не видели.
    """
    drift: list[str] = []
    if manifest.get("matcher_version") != MATCHER_VERSION:
        drift.append(
            f"matcher_version: прогон {manifest.get('matcher_version')} → сейчас {MATCHER_VERSION}"
        )
    if manifest.get("matcher_rules_digest") != rules_digest():
        drift.append("matcher_rules_digest: правила матчера изменились с момента прогона")
    return drift


def _recompute_provenance(
    manifest: Mapping[str, object],
    *,
    allow_drift: bool,
    where: str,
) -> dict[str, object] | None:
    """Провенанс пересчёта: ``None``, если матчер тот же; иначе — запись о дрейфе.

    Дрейф без явного флага — код 2: цифры, полученные другим матчером, нельзя
    публиковать под шапкой прогона молча. С флагом пересчёт разрешён, но
    **назван**: `recomputed_with` уходит в `metrics.json` и в шапку отчёта
    рядом с провенансом прогона.
    """
    drift = _matcher_drift(manifest)
    if not drift:
        return None
    if not allow_drift:
        typer.echo(
            f"config error: {where} — матчер разошёлся с прогоном ({'; '.join(drift)}); "
            f"передайте {_DRIFT_FLAG}, чтобы пересчитать и записать это в артефакты",
            err=True,
        )
        raise typer.Exit(_EXIT_CONFIG)
    typer.echo(f"{where}: пересчёт другим матчером ({'; '.join(drift)})", err=True)
    return {
        "matcher_version": MATCHER_VERSION,
        "matcher_rules_digest": rules_digest(),
        "at": utc_now(),
    }


def _guarded(command: Callable[..., None]) -> Callable[..., None]:
    """Последняя надежда каждой подкоманды: непредусмотренное — код 3, одной строкой.

    Коды 0/1/2 — заявленный контракт (§11), и их несёт `typer.Exit`, который
    здесь пропускается насквозь вместе с остальными управляющими исключениями
    typer (`Abort`, `BadParameter` — только публичные имена: click у typer
    0.26 вендорён как приватный `typer._click`). Всё прочее — дефект
    инструмента, а не его ответ: traceback в stderr выглядел бы как «упало
    непонятно что» и не отличался бы кодом выхода от ошибки конфигурации.
    """

    @functools.wraps(command)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            command(*args, **kwargs)
        except (typer.Exit, typer.Abort, typer.BadParameter):
            raise
        except Exception as error:  # noqa: BLE001 — граница CLI: код 3 вместо traceback
            typer.echo(f"internal error: {type(error).__name__}: {error}", err=True)
            raise typer.Exit(_EXIT_MECHANICAL) from error

    # `functools.wraps` переносит и `__wrapped__`, поэтому typer видит исходную
    # подпись команды (`inspect.signature` идёт по `__wrapped__`) вместе с её
    # `typer.Option`, — обёртка не ломает разбор аргументов.
    return wrapper


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------


@corpus_app.command("validate")
@_guarded
def corpus_validate(
    corpus: Path = typer.Option(_DEFAULT_CORPUS, "--corpus", help="каталог кейсов"),
    register: bool = typer.Option(
        False, "--register", help="дописать новые id дефектов в append-only реестр _ids.txt"
    ),
    retire_deleted: bool = typer.Option(
        False,
        "--retire-deleted",
        help="списать надгробием id, которых больше нет в корпусе (необратимо)",
    ),
    reidentify: str | None = typer.Option(
        None,
        "--reidentify",
        help="id через запятую, у которых смена ядра идентичности подтверждена",
    ),
) -> None:
    """Проверить схему, уникальность id и реестр корпуса (код 2 при нарушении).

    `--register` дописывает новые id; `--retire-deleted` списывает надгробием
    те, что ушли из корпуса (необратимо — id больше никому не достанется);
    `--reidentify` подтверждает, что под перечисленными id теперь другой
    дефект (сменилось ядро `kind` + `file` + `scenario`).
    """
    ids = _reidentify_ids(reidentify)
    if (retire_deleted or ids) and not register:
        typer.echo(
            "config error: --retire-deleted/--reidentify имеют смысл только с --register",
            err=True,
        )
        raise typer.Exit(_EXIT_CONFIG)
    try:
        if register:
            # Регистрация обязана идти до полной валидации и **без** обратной
            # проверки реестра: `load_corpus` отказывает и на
            # незарегистрированном id (регистрировать было бы нечем), и на
            # живом id без кейса — а это ровно то, что чинит `--retire-deleted`,
            # так что списание через `load_corpus` было бы недостижимо. Схема
            # каждого кейса при этом проверена: `load_case` валидирует те же
            # правила, кроме кросс-кейсовых.
            drafts = [load_case(path) for path in sorted(corpus.glob("*.yaml"))]
            appended = append_registry(
                drafts, corpus, retire_deleted=retire_deleted, reidentify=ids
            )
            for line in appended:
                typer.echo(f"реестр: + {line}")
        cases = load_corpus(corpus)
    except CorpusError as error:
        typer.echo(f"corpus invalid: {error}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from error

    gold = [case for case in cases if is_gold(case)]
    typer.echo(f"корпус: {corpus}")
    typer.echo(f"кейсов: {len(cases)} (gold: {len(gold)}, draft: {len(cases) - len(gold)})")
    typer.echo(f"дефектов: {sum(len(case.defects) for case in cases)}")
    typer.echo(f"реестр: {registry_path(corpus)}")
    typer.echo(f"corpus_digest: {corpus_digest(cases)}")
    if not gold:
        typer.echo(
            "gold-кейсов нет: метрики не публикуются, пока разметка не переведена "
            "в annotation.status: adjudicated",
            err=True,
        )
    raise typer.Exit(_EXIT_OK)


@corpus_app.command("candidates")
@_guarded
def corpus_candidates(
    repo: str = typer.Option(..., "--repo", help="org/repo"),
    pr: int = typer.Option(..., "--pr", help="номер PR"),
    corpus: Path = typer.Option(_DEFAULT_CORPUS, "--corpus", help="каталог кейсов"),
    out: Path | None = typer.Option(None, "--out", help="файл черновика (по умолчанию в корпусе)"),
    gh: str = typer.Option("gh", "--gh", help="бинарь gh"),
) -> None:
    """Черновик кейса из истории ревью ai-prosto на PR (**сеть**: чтение `gh api`).

    Пишет кейс с `annotation.status: draft` — прокси, а не ground truth: в
    метрики он не входит, пока разметчик не переведёт его в `adjudicated`.
    """
    try:
        pr_meta = fetch_pr(repo, pr, gh=gh)
        reviews = fetch_reviews(repo, pr, gh=gh)
        commits = fetch_commits(repo, pr, gh=gh)
        last_review_at = _last_ai_prosto_submitted_at(reviews)
        # База кейса — merge-base **диапазона ревью**, и считает её API, а не
        # `draft_case` (он чистая функция). Копировать `base.sha` PR нельзя:
        # после мержа голова базы содержит сам PR и может быть потомком
        # `head_sha` из маркера — диапазон кейса вышел бы пустым (раунд 21).
        head_sha = _review_head_sha(repo, pr, pr_meta, reviews)
        base_sha = resolve_review_base(repo, pr_meta, head_sha, gh=gh)
        case = draft_case(
            repo,
            pr,
            pr_meta,
            reviews,
            base_sha=base_sha,
            commits_after=commits_after(commits, last_review_at),
        )
    except CandidatesError as error:
        typer.echo(f"candidates: {error}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from error

    destination = out if out is not None else corpus / f"{case['case_id']}.yaml"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_case(case), encoding="utf-8")
    typer.echo(f"черновик записан: {destination}")
    typer.echo(
        "дальше: 'review-eval corpus validate --register' зарегистрирует id дефектов, "
        "затем разметка (severity, evidence, match, blocking_complete) и status: adjudicated"
    )
    raise typer.Exit(_EXIT_OK)


@corpus_app.command("materialize")
@_guarded
def corpus_materialize(
    corpus: Path = typer.Option(_DEFAULT_CORPUS, "--corpus", help="каталог кейсов"),
    cache: Path = typer.Option(_DEFAULT_CACHE, "--cache", help="корень bare-кэша"),
    workspace_root: Path = typer.Option(
        _DEFAULT_WORKSPACE_ROOT, "--workspace-root", help="корень воркспейса с чекаутами соседей"
    ),
    git: str = typer.Option("git", "--git", help="бинарь git"),
) -> None:
    """Гарантировать наличие `base_sha`/`head_sha` всех кейсов в bare-кэше (**сеть**).

    Источник по умолчанию — локальный чекаут соседа `<workspace-root>/<имя
    репо>` (быстро, без сети); если объекта там нет — `git fetch` с GitHub.
    Соседние чекауты только читаются (полирепо-правило).
    """
    try:
        _require_git(git)
        cases = load_corpus(corpus)
    except RunnerError as error:
        typer.echo(f"config error: {error}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from error
    except CorpusError as error:
        typer.echo(f"corpus invalid: {error}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from error

    for repo in sorted({case.repo for case in cases}):
        shas = sorted(
            {sha for case in cases if case.repo == repo for sha in (case.base_sha, case.head_sha)}
        )
        local = _local_checkout(workspace_root, repo)
        source = str(local) if local is not None else "GitHub"
        typer.echo(f"{repo}: {len(shas)} объект(ов), источник — {source}")
        try:
            materialize(
                cache,
                repo,
                shas,
                local_checkout=local,
                remote_url=f"https://github.com/{repo}.git",
                git=git,
            )
        except CacheError as error:
            typer.echo(f"materialize: {error}", err=True)
            raise typer.Exit(_EXIT_CONFIG) from error
        typer.echo(f"{repo}: кэш готов — {repo_cache_dir(cache, repo)}")
    raise typer.Exit(_EXIT_OK)


# ---------------------------------------------------------------------------
# run / metrics / compare
# ---------------------------------------------------------------------------


@app.command("run")
@_guarded
def run(
    corpus: Path = typer.Option(_DEFAULT_CORPUS, "--corpus", help="каталог кейсов"),
    variant: list[str] = typer.Option(
        [], "--variant", help="<harness>:<model>[:<effort>], можно несколько раз"
    ),
    out: Path = typer.Option(..., "--out", help="каталог прогона (eval/runs/<run_id>)"),
    cache: Path = typer.Option(_DEFAULT_CACHE, "--cache", help="корень bare-кэша"),
    repetitions: int = typer.Option(1, "--repetitions", help="повторений на (кейс, вариант)"),
    jobs: int = typer.Option(1, "--jobs", help="параллельных прогонов (>1 — явный opt-in)"),
    cases: str | None = typer.Option(None, "--cases", help="подмножество case_id через запятую"),
    keep_worktrees: bool = typer.Option(False, "--keep-worktrees", help="не убирать worktree"),
    rerun: bool = typer.Option(False, "--rerun", help="прогнать заново готовые тройки"),
    steward_root: Path | None = typer.Option(
        None, "--steward-root", help="чекаут steward с китом под измерением"
    ),
    git: str = typer.Option("git", "--git", help="бинарь git"),
) -> None:
    """Прогнать кит по корпусу и посчитать метрики. **Всегда офлайн** (§6)."""
    try:
        _require_git(git)
        variants = _parse_variants(variant)
        root = steward_root if steward_root is not None else _steward_root()
        kit = kit_under_test(root, git=git)
        all_cases = load_corpus(corpus)
        digest = corpus_digest(all_cases)
        selected = _select_cases(all_cases, cases)
    except (CorpusError, RunnerError, CacheError, ValueError) as error:
        typer.echo(f"config error: {error}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from error

    try:
        manifest = run_all(
            selected,
            variants,
            repetitions=repetitions,
            out_dir=out,
            kit=kit,
            cache_root=cache,
            jobs=jobs,
            rerun=rerun,
            keep_worktrees=keep_worktrees,
            corpus_digest_override=digest,
            git=git,
        )
    except (RunnerError, CacheError) as error:
        typer.echo(f"config error: {error}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from error
    except Exception as error:  # noqa: BLE001 — механический сбой раннера → код 3
        typer.echo(f"runner failed: {error}", err=True)
        raise typer.Exit(_EXIT_MECHANICAL) from error

    typer.echo(f"run_id: {manifest.run_id}")
    _report(out, all_cases, manifest, cache_root=cache, git=git)


@app.command("metrics")
@_guarded
def metrics(
    run_dir: Path = typer.Argument(..., help="каталог прогона"),
    corpus: Path = typer.Option(_DEFAULT_CORPUS, "--corpus", help="каталог кейсов"),
    cache: Path = typer.Option(_DEFAULT_CACHE, "--cache", help="корень bare-кэша"),
    git: str = typer.Option("git", "--git", help="бинарь git"),
    allow_matcher_drift: bool = typer.Option(
        False,
        _DRIFT_FLAG,
        help="пересчитать, даже если матчер изменился с момента прогона (будет записано)",
    ),
) -> None:
    """Пересчитать `metrics.json`/`report.md`/очередь по уже собранным артефактам.

    Команда разбора очереди: разметчик правит корпус (`draft` → `adjudicated`,
    новые дефекты, `blocking_complete`) и пересчитывает — прогон не
    повторяется, модель не вызывается.

    Матчер при этом берётся **текущий**, а шапка отчёта — из `run.json`
    прогона: если они разошлись, команда отказывается (код 2), пока пересчёт
    не разрешён явно `--allow-matcher-drift`. С флагом оба провенанса
    публикуются рядом (`recomputed_with`).
    """
    try:
        _require_git(git)
        cases = load_corpus(corpus)
    except RunnerError as error:
        typer.echo(f"config error: {error}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from error
    except CorpusError as error:
        typer.echo(f"corpus invalid: {error}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from error
    manifest = _load_manifest(run_dir)
    recomputed = _recompute_provenance(
        manifest, allow_drift=allow_matcher_drift, where=str(run_dir / "run.json")
    )
    _report(run_dir, cases, manifest, cache_root=cache, git=git, recomputed_with=recomputed)


@app.command("compare")
@_guarded
def compare_runs(
    run_a: Path = typer.Argument(..., help="каталог прогона A (базис)"),
    run_b: Path = typer.Argument(..., help="каталог прогона B (сравниваемый)"),
    corpus: Path = typer.Option(_DEFAULT_CORPUS, "--corpus", help="каталог кейсов"),
    cache: Path = typer.Option(_DEFAULT_CACHE, "--cache", help="корень bare-кэша"),
    git: str = typer.Option("git", "--git", help="бинарь git"),
    allow_matcher_drift: bool = typer.Option(
        False,
        _DRIFT_FLAG,
        help="сравнить, даже если матчер изменился с момента прогонов",
    ),
) -> None:
    """Парное сравнение двух прогонов по общим вариантам и общим кейсам (§9, D12).

    Единица парности — `(case_id, повторение)`: сравниваются те же кейсы на
    тех же повторениях, иначе «парность» была бы мнимой. Вариант, которого нет
    в обоих прогонах, называется в выводе и не сравнивается.
    """
    try:
        _require_git(git)
        cases = load_corpus(corpus)
    except RunnerError as error:
        typer.echo(f"config error: {error}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from error
    except CorpusError as error:
        typer.echo(f"corpus invalid: {error}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from error

    # Сравнение — такой же пересчёт, как `metrics`, только по двум прогонам:
    # дрейф матчера на любой стороне делает числа несравнимыми с их шапками.
    for label, run_dir in (("A", run_a), ("B", run_b)):
        _recompute_provenance(
            _load_manifest(run_dir),
            allow_drift=allow_matcher_drift,
            where=f"прогон {label} ({run_dir / 'run.json'})",
        )

    try:
        evals_a = _evaluate(run_a, cases, cache_root=cache, git=git)
        evals_b = _evaluate(run_b, cases, cache_root=cache, git=git)
    except CacheError as error:
        typer.echo(f"config error: {error}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from error
    except RunnerError as error:
        # Отказ **артефактов прогона**: манифест не закрыт, прогон неполон,
        # результат вне манифеста, манифеста нет. Это конфигурация каталога, а
        # не дефект инструмента, поэтому код 2: код 3 объявил бы сбоем сам
        # `review-eval` и в CI читался бы как «инструмент сломался».
        typer.echo(f"config error: {error}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from error
    except MetricsError as error:
        # А расхождение артефактов с `result.json` — механический сбой (§11):
        # прогон обещал вердикт, которого нет или который не читается.
        typer.echo(f"cannot read run artifacts: {error}", err=True)
        raise typer.Exit(_EXIT_MECHANICAL) from error

    common = sorted(set(evals_a) & set(evals_b))
    only = sorted(set(evals_a) ^ set(evals_b))
    if not common:
        typer.echo("общих вариантов у прогонов нет — сравнивать нечего", err=True)
        raise typer.Exit(_EXIT_CONFIG)
    if only:
        typer.echo(f"варианты вне сравнения (есть только в одном прогоне): {', '.join(only)}")

    for label in common:
        summary_a = summarize_variant(evals_a[label])
        summary_b = summarize_variant(evals_b[label])
        paired = _pairs(evals_a[label], evals_b[label])
        typer.echo("")
        typer.echo(f"## {label}")
        typer.echo("")
        typer.echo(render_compare(compare(summary_a, summary_b, paired)))
    raise typer.Exit(_EXIT_OK)


# ---------------------------------------------------------------------------
# внутреннее
# ---------------------------------------------------------------------------


def _report(
    run_dir: Path,
    cases: Sequence[Case],
    manifest: RunManifest | Mapping[str, object],
    *,
    cache_root: Path,
    git: str,
    recomputed_with: Mapping[str, object] | None = None,
) -> None:
    """Посчитать метрики прогона, записать три артефакта и выйти по §11.

    Код 1 — «прогон состоялся, но смотреть глазами обязательно»: исход, не
    совпавший с ожидаемым, или открытая очередь adjudication (precision тогда
    не публикуется вовсе, D9).
    """
    try:
        evals_by_variant = _evaluate(run_dir, cases, cache_root=cache_root, git=git)
    except CacheError as error:
        # Отказ git посреди подсчёта — конфигурация (код 2), а не механический
        # сбой: артефакты прогона в порядке, читать их нечем.
        typer.echo(f"config error: {error}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from error
    except RunnerError as error:
        # Отказ **артефактов прогона**: манифест не закрыт, прогон неполон,
        # результат вне манифеста, манифеста нет. Это конфигурация каталога, а
        # не дефект инструмента, поэтому код 2: код 3 объявил бы сбоем сам
        # `review-eval` и в CI читался бы как «инструмент сломался».
        typer.echo(f"config error: {error}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from error
    except MetricsError as error:
        # А расхождение артефактов с `result.json` — механический сбой (§11):
        # прогон обещал вердикт, которого нет или который не читается.
        typer.echo(f"cannot read run artifacts: {error}", err=True)
        raise typer.Exit(_EXIT_MECHANICAL) from error

    labels = _ordered_labels(manifest, evals_by_variant)
    summaries = {label: summarize_variant(evals_by_variant[label]) for label in labels}
    comparisons = _comparisons(labels, summaries, evals_by_variant)

    write_metrics_json(run_dir, summaries, comparisons=comparisons, recomputed_with=recomputed_with)
    write_report(
        run_dir,
        summaries,
        {label: evals_by_variant[label] for label in labels},
        manifest,
        recomputed_with=recomputed_with,
    )
    write_queue(run_dir, {label: evals_by_variant[label] for label in labels})
    for name in ("metrics.json", "report.md", "adjudication-queue.md"):
        typer.echo(f"артефакт: {run_dir / name}")

    pending = [label for label, summary in summaries.items() if summary.get("status") == _PENDING]
    unexpected = [
        f"{ev.case.case_id}/{label}/{ev.result.repetition_id}: {ev.result.outcome}"
        for label in labels
        for ev in evals_by_variant[label]
        if ev.result.unexpected
    ]
    if not summaries or all(summary.get("status") == "no_gold" for summary in summaries.values()):
        typer.echo("gold-кейсов нет, метрики не публикуются (§13)", err=True)
    for label in pending:
        typer.echo(f"{label}: очередь adjudication непуста — precision не публикуется", err=True)
    for line in unexpected:
        typer.echo(f"unexpected_outcome — {line}", err=True)
    raise typer.Exit(_EXIT_ATTENTION if (pending or unexpected) else _EXIT_OK)


def _evaluate(
    run_dir: Path,
    cases: Sequence[Case],
    *,
    cache_root: Path,
    git: str,
) -> dict[str, list[CaseEval]]:
    """Разобрать артефакты прогона в `CaseEval` по вариантам.

    Результат по кейсу, которого в корпусе больше нет, — `MetricsError`:
    считать метрики по прогону, чей ground truth удалён, значит публиковать
    число без знаменателя.
    """
    by_id = {case.case_id: case for case in cases}
    evals: dict[str, list[CaseEval]] = {}
    for result in load_results(run_dir):
        case = by_id.get(result.case_id)
        if case is None:
            raise MetricsError(
                f"{run_dir}: результат кейса '{result.case_id}' есть, а кейса в корпусе нет"
            )
        evals.setdefault(result.variant, []).append(
            evaluate_case(
                case,
                result,
                run_dir,
                file_lines=_file_lines_at(cache_root, case.repo, case.head_sha, git=git),
            )
        )
    return evals


def _file_lines_at(
    cache_root: Path, repo: str, sha: str, *, git: str = "git"
) -> Callable[[str], int | None]:
    """Функция «сколько строк в файле на `sha`» поверх bare-кэша (для §9 evidence).

    Читает `git show <sha>:<path>` из кэша — не из worktree: worktree к моменту
    подсчёта метрик уже снят, а объект в кэше остаётся. Файла нет (или кэша
    нет вовсе) — ``None``, и метрика evidence считает такой указатель
    неразрешимым, а не падает.

    **Отказ git — не факт о файле.** ``None`` возвращается только на
    узнаваемое «объекта/пути в дереве нет» (код 128 и `does not exist` /
    `Not a valid object name`, см. `_MISSING_OBJECT_SIGNATURES`); любой другой
    отказ — `CacheError`: неверный `--git` или испорченный кэш иначе выглядели
    бы как «все ссылки неразрешимы», то есть подменяли бы отсутствие данных
    посчитанным нулём `resolvable_evidence_rate`.

    Сначала `git cat-file -t` и требование ``blob``: на каталог `git show`
    печатает листинг дерева, и evidence вида `src/:12` иначе считалась бы
    разрешимой с числом строк листинга — адреса строки у каталога нет.

    Байты, а не текст: в дереве бывают не-UTF-8 файлы, и падать на декодировании
    при подсчёте строк незачем.

    Git берётся тот же, что у прогона (`pin_git`), и запускается в том же
    вычищенном окружении (`scrubbed_git_env`): унаследованный `GIT_DIR` увёл бы
    чтение дерева в чужое репо, а глобальный `.gitconfig` мог бы подменить
    обработку файла — evidence считалась бы по другому материалу, чем мерили.
    """
    # Кэша нет вовсе (или слаг репо негоден) — так бывает при пересчёте метрик
    # из скопированного прогона (§7). Это не «файла нет»: спрашивать некого, и
    # ответом обязан быть отказ, который метрики превратят в непосчитанную
    # метрику, а не в посчитанный ноль.
    try:
        cache = repo_cache_dir(cache_root, repo)
    except CacheError as error:
        return _unavailable(f"{repo}: {error}")
    if not cache.exists():
        return _unavailable(f"bare-кэша {cache} нет — evidence проверить нечем")

    # Тот же git и то же окружение, что у прогона: `pin_git` резолвит бинарь
    # против PATH (и ставит его каталог первым), `scrubbed_git_env` снимает
    # `GIT_*` процесса и пинует конфиг. Иначе унаследованный `GIT_DIR` увёл бы
    # чтение дерева в чужое репо, а глобальный `.gitconfig` подменил бы
    # обработку файла — и evidence считалась бы по другому материалу.
    try:
        binary, env_base = pin_git(git, None)
    except RunnerError as error:
        raise CacheError(f"{error}") from error
    env = scrubbed_git_env(env_base)

    def run_git(args: list[str]) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(  # noqa: S603 — argv фиксирован, без shell
                [binary, "-C", str(cache), *args],
                capture_output=True,
                check=False,
                env=env,
            )
        except OSError as error:
            raise CacheError(f"--git '{git}' не запускается: {error}") from error

    def file_lines(raw_path: str) -> int | None:
        # Путь нормализуется правилом матчера: `./app/a.py` и `app//a.py` — тот
        # же файл, и в дереве он лежит под нормализованным именем.
        path = normalize_path(raw_path)
        kind = run_git(["cat-file", "-t", f"{sha}:{path}"])
        if kind.returncode != 0:
            if _is_missing_object(kind):
                return None
            raise CacheError(f"git cat-file -t {sha}:{path} в {cache} отказал: {_git_error(kind)}")
        if kind.stdout.strip() != b"blob":
            return None
        completed = run_git(["show", f"{sha}:{path}"])
        if completed.returncode != 0:
            # Объект только что опознан как blob — отказ здесь про git, не про данные.
            raise CacheError(f"git show {sha}:{path} в {cache} отказал: {_git_error(completed)}")
        content = completed.stdout
        if not content:
            return 0
        return content.count(b"\n") + (0 if content.endswith(b"\n") else 1)

    return file_lines


def _unavailable(reason: str) -> Callable[[str], int | None]:
    """Функция `file_lines`, которая на любой путь отвечает `CacheUnavailable`."""

    def file_lines(_path: str) -> int | None:
        raise CacheUnavailable(reason)

    return file_lines


def _is_missing_object(result: subprocess.CompletedProcess[bytes]) -> bool:
    """Отказ git означает «такого объекта или пути в дереве нет» (а не сбой git)."""
    if result.returncode != 128:
        return False
    stderr = result.stderr.lower()
    return any(signature in stderr for signature in _MISSING_OBJECT_SIGNATURES)


def _git_error(result: subprocess.CompletedProcess[bytes]) -> str:
    """Текст отказа git для сообщения об ошибке; пусто — код выхода."""
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    return stderr or f"код выхода {result.returncode}"


def _comparisons(
    labels: Sequence[str],
    summaries: Mapping[str, Mapping[str, object]],
    evals_by_variant: Mapping[str, Sequence[CaseEval]],
) -> dict[str, object] | None:
    """Сравнение первых двух вариантов прогона — в `metrics.json` (§9, D12).

    Первых двух, а не всех пар: прогон с двумя вариантами — основной сценарий
    P3 («модель × уровень»), и полная матрица пар в машинном артефакте
    раздувала бы его без спроса. Остальные пары считает `review-eval compare`.
    """
    if len(labels) < 2:
        return None
    first, second = labels[0], labels[1]
    paired = _pairs(evals_by_variant[first], evals_by_variant[second])
    return {
        f"{second} vs {first}": compare(dict(summaries[first]), dict(summaries[second]), paired)
    }


def _pairs(left: Sequence[CaseEval], right: Sequence[CaseEval]) -> list[tuple[CaseEval, CaseEval]]:
    """Пары прогонов по общим `(case_id, повторение)`, в детерминированном порядке."""
    index_left = {(ev.case.case_id, ev.result.repetition_id): ev for ev in left}
    index_right = {(ev.case.case_id, ev.result.repetition_id): ev for ev in right}
    keys = sorted(set(index_left) & set(index_right))
    return [(index_left[key], index_right[key]) for key in keys]


def _ordered_labels(
    manifest: RunManifest | Mapping[str, object],
    evals_by_variant: Mapping[str, Sequence[CaseEval]],
) -> list[str]:
    """Варианты в порядке манифеста, затем остальные по алфавиту.

    Порядок из `run.json` — это порядок, в котором варианты задал автор
    прогона: он же определяет, что с чем сравнивает `metrics.json` (базис —
    первый `--variant`). Вариант, которого в манифесте нет (долитый прошлым
    запуском), не теряется — он идёт следом.
    """
    ordered: list[str] = []
    for record in _manifest_variants(manifest):
        label = record.get("label")
        if isinstance(label, str) and label in evals_by_variant and label not in ordered:
            ordered.append(label)
    ordered += sorted(label for label in evals_by_variant if label not in ordered)
    return ordered


def _manifest_variants(
    manifest: RunManifest | Mapping[str, object],
) -> list[Mapping[str, object]]:
    records = manifest.variants if isinstance(manifest, RunManifest) else manifest.get("variants")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, Mapping)]


def _load_manifest(run_dir: Path) -> Mapping[str, object]:
    """`run.json` прогона как словарь; без него отчёт не собрать (код 2)."""
    path = run_dir / "run.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        typer.echo(f"config error: {path} не читается: {error}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from error
    except json.JSONDecodeError as error:
        typer.echo(f"config error: {path} не JSON: {error}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from error
    if not isinstance(payload, dict):
        typer.echo(f"config error: {path} — не объект", err=True)
        raise typer.Exit(_EXIT_CONFIG)
    return payload


def _reidentify_ids(raw: str | None) -> frozenset[str]:
    """`--reidentify a,b` → множество id; пустые элементы отбрасываются."""
    if raw is None:
        return frozenset()
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def _parse_variants(raw: Sequence[str]) -> list[Variant]:
    """Разобрать `--variant` и отсеять метки, ломающие раскладку артефактов.

    Метка варианта — имя каталога в `cases/<case>/<label>/<rep>`, а `/` в ней
    добавил бы уровень пути: `load_results` (glob ``*/*/*/result.json``) такой
    прогон просто не увидел бы. Класс токена в `runner.parse_variant` остаётся
    как есть (он зеркалит валидацию кита), а ограничение раскладки живёт
    здесь, где раскладка и назначается.
    """
    if not raw:
        raise ValueError("нужен хотя бы один --variant <harness>:<model>[:<effort>]")
    variants = [parse_variant(text) for text in raw]
    labels = [variant_label(v) for v in variants]
    with_slash = [label for label in labels if "/" in label]
    if with_slash:
        raise ValueError(
            f"метка варианта не может содержать '/': {', '.join(with_slash)} — "
            "она же имя каталога артефактов cases/<case>/<label>/<rep>"
        )
    duplicates = sorted({label for label in labels if labels.count(label) > 1})
    if duplicates:
        raise ValueError(f"варианты повторяются: {', '.join(duplicates)}")
    return variants


def _select_cases(cases: Sequence[Case], selector: str | None) -> list[Case]:
    """Подмножество корпуса по `--cases id,…`; неизвестный id — ошибка конфигурации."""
    if selector is None:
        return list(cases)
    wanted = [item.strip() for item in selector.split(",") if item.strip()]
    if not wanted:
        raise ValueError("--cases задан пустым")
    known = {case.case_id: case for case in cases}
    unknown = [case_id for case_id in wanted if case_id not in known]
    if unknown:
        raise ValueError(f"нет таких кейсов в корпусе: {', '.join(unknown)}")
    return [known[case_id] for case_id in dict.fromkeys(wanted)]


def _steward_root() -> Path:
    """Чекаут steward, из которого берётся кит: ближайший каталог с `pyproject.toml`.

    От файла пакета, а не от cwd: `review-eval` запускают из любого каталога, а
    кит под измерением — всегда тот, чей код сейчас исполняется (§6.2).
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RunnerError(
        "не удалось определить чекаут steward (нет pyproject.toml выше "
        f"{Path(__file__).resolve()}); задайте --steward-root"
    )


def _local_checkout(workspace_root: Path, repo: str) -> Path | None:
    """Чекаут соседа `<workspace-root>/<имя репо>`, если это git-каталог.

    Имя — каноническое имя репо после обычного `git clone` (правило
    репо-границ воркспейса), не имя каталога на диске.
    """
    _owner, _sep, name = repo.partition("/")
    candidate = workspace_root / name
    return candidate if (candidate / ".git").exists() else None


def _review_head_sha(
    repo: str,
    pr: int,
    pr_meta: Mapping[str, object],
    reviews: Sequence[Mapping[str, object]],
) -> str:
    """Голова, от которой считается диапазон ревью: маркер тела, иначе `head.sha` PR.

    Правило то же, что внутри `draft_case`: маркер называет ровно то дерево,
    которое ревьюер видел, и `head.sha` нужен только там, где маркера нет.
    Здесь оно повторено потому, что база (`resolve_review_base`) считается
    **до** черновика и от той же головы; разойдись эти два выбора — кейс
    получил бы базу от одной головы и `head_sha` от другой.
    """
    bodies = [
        review.get("body")
        for review in reviews
        if _login(review) == AI_PROSTO and isinstance(review.get("body"), str)
    ]
    if not bodies:
        raise CandidatesError(f"{repo}#{pr}: нет ревью от {AI_PROSTO} — черновик не из чего делать")
    marker = review_head(str(bodies[-1]))
    if marker is not None:
        return marker
    head = pr_meta.get("head")
    sha = head.get("sha") if isinstance(head, Mapping) else None
    if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
        raise CandidatesError(f"{repo}#{pr}: head.sha не 40-hex sha: {sha!r}")
    return sha


def _last_ai_prosto_submitted_at(reviews: Sequence[Mapping[str, object]]) -> str | None:
    """`submitted_at` последнего ревью ai-prosto — точка отсчёта `commits_after`."""
    stamps = [
        review.get("submitted_at")
        for review in reviews
        if isinstance(review.get("user"), Mapping)
        and _login(review) == AI_PROSTO
        and isinstance(review.get("submitted_at"), str)
    ]
    strings = [stamp for stamp in stamps if isinstance(stamp, str)]
    return max(strings) if strings else None


def _login(review: Mapping[str, object]) -> object:
    user = review.get("user")
    return user.get("login") if isinstance(user, Mapping) else None
