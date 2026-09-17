"""Метрики review-eval: качество, эксплуатация, стоимость, длительность —
каждая со своим знаменателем (дизайн §9; D6, D8, D9, D11, D12).

Что здесь важно понимать про знаменатели, а не только про значения:

- **Качество считается только по gold-кейсам** (`annotation.status:
  adjudicated`, `corpus.is_gold`) **с исходом `verdict`**, у которых исход
  совпал с ожидаемым (§6.6: `unexpected_outcome` — вне метрик качества, но
  внутри эксплуатационных). Прогон без вердикта нечего сопоставлять.
- **Неразмеченное предсказание не FP окончательно** (D9): строгая метрика —
  `precision_lower_bound` (unlabeled и duplicate считаются FP), а официальный
  `precision` **отсутствует как ключ**, пока очередь adjudication непуста; у
  варианта тогда `status: pending_adjudication`. `null` вместо числа был бы
  неотличим от «посчитали и вышло ничего».
- **Завышение класса — FP, а не TP** (§9): блокирующее предсказание,
  назначенное gold-дефекту `minor`, красит гейт там, где gold красить не
  велит. В recall такой дефект не входит вовсе (знаменатель recall — только
  major/blocker).
- **Recall и false-block — только `blocking_complete: true`** (D8): перечень
  дефектов кейса должен быть исчерпывающим, иначе знаменатель врёт.
- **Стоимость никогда не подставляется нулём** (D6): прогон без
  provider-стоимости попадает в `cost_unavailable_cases`, а средняя
  помечается `partial: true`. Считается по **всем** прогонам с sidecar,
  включая неуспешные (D12).
- **Повторения** (D12): каждый `(кейс, повторение)` — отдельная строка и в
  эксплуатационных, и в метриках качества (знаменатели растут кратно числу
  повторений). Разрез по повторениям — `per_repetition`, разброс по кейсам —
  `ci` (bootstrap 95 %, ресемплинг **кейсов**, повторения внутри кейса
  пулятся).

Дизайн: docs/superpowers/specs/2026-09-14-review-eval-harness-design.md §9.
"""

from __future__ import annotations

import json
import math
import random
import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from steward.review_eval.cache import CacheUnavailable
from steward.review_eval.corpus import FILE_MISSING_KIND, Case, is_gold
from steward.review_eval.matcher import MatchResult, Prediction
from steward.review_eval.matcher import match as match_predictions
from steward.review_eval.runner import EMPTY_RANGE_OUTCOME, RunResult
from steward.review_eval.threshold import as_line_number, is_blocking, is_schema_valid_verdict

__all__ = [
    "CACHE_UNAVAILABLE_NOTE",
    "CI_METRICS",
    "COMPARE_METRICS",
    "GOLD_SEVERITIES",
    "NO_CI_NOTE",
    "CaseEval",
    "Metric",
    "MetricsError",
    "QueueCounts",
    "bootstrap_ci",
    "compare",
    "evaluate_case",
    "is_quality_run",
    "metrics_for_variant",
    "contradicted_gold",
    "effective_assigned",
    "queue_counts",
    "unlabeled_predictions",
]

GOLD_SEVERITIES: frozenset[str] = frozenset({"blocker", "major"})
"""Severity дефектов, которые обязаны блокировать мерж (знаменатель recall)."""

CI_METRICS: tuple[str, ...] = (
    "precision_lower_bound",
    "blocking_recall",
    "detection_recall_any_severity",
    "false_block_rate",
    "resolvable_evidence_rate",
)
"""Метрики качества с bootstrap-CI и разрезом по повторениям (§9, D12).

Все пять, а не только сравниваемые: разброс по кейсам нужен и там, где
вариантов пока один — иначе «recall 0.6» читается как точное число.
"""

COMPARE_METRICS: tuple[str, ...] = (
    "precision_lower_bound",
    "blocking_recall",
    "false_block_rate",
    "cost_usd_mean_per_case",
)
"""Метрики парного сравнения `review-eval compare` (§9, D12)."""

NO_CI_NOTE = "без CI"
"""Пометка точечной разницы, для которой интервал не считался (§9, N=1)."""

CACHE_UNAVAILABLE_NOTE = (
    "кэш недоступен — evidence не проверялся; сверка gold 'файла нет' с деревом "
    "head_sha тоже пропущена"
)
"""Пометка `resolvable_evidence_rate`, когда проверять ссылки было нечем (§7).

Метрика тогда публикуется без значения: «ноль разрешимых ссылок» и «ссылки не
смотрели» — разные факты, и второй обязан выглядеть как отсутствие числа.

Пометка называет и вторую потерю: без источника фактов о файлах не проверены и
gold-дефекты `kind: file-missing`. Противоречие разметки дереву в таком прогоне
не обнаружится, то есть знаменатели recall могут содержать негодный gold — и
это надо читать рядом с самой метрикой, а не догадываться.
"""


class MetricsError(Exception):
    """Артефакты прогона противоречат `result.json` — считать метрики нельзя."""


@dataclass(frozen=True)
class Metric:
    """Значение с явным знаменателем.

    `value` — ``None``, если знаменатель нулевой: пустой знаменатель это не
    «ноль процентов», а «мерить не на чем», и отчёт обязан их различать.
    Счётчики (например `unexpected_outcome_count`) публикуются в той же форме:
    `value` равно счёту, `note` объясняет, что это не доля.
    """

    value: float | None
    numerator: int
    denominator: int
    note: str | None = None

    def as_dict(self) -> dict[str, object]:
        """JSON-форма с **фиксированным** набором ключей.

        Форма одна для всех метрик (включая `note: null`): потребитель
        (`metrics.json`, `report.md`) не должен разбирать два варианта записи.
        """
        return {
            "value": self.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "note": self.note,
        }


@dataclass(frozen=True)
class CaseEval:
    """Один прогон `(кейс, вариант, повторение)`, разобранный до чисел.

    `findings` — находки из sidecar-вердикта (только элементы-объекты, в
    исходном порядке). `finding_positions` — **позиции этих находок в исходном
    ``verdict.json.findings``**, параллельно `findings`: индекс `Prediction` и
    ключи матчера — позиция в вердикте, а не в отфильтрованном кортеже, иначе
    находка в очереди adjudication указывала бы на чужую строку вердикта.
    Пустой кортеж означает «позиции совпадают с индексами `findings`» (случай
    вердикта без мусорных элементов).
    `match` — результат матчера, ``None`` для любого исхода, кроме `verdict`
    (сопоставлять нечего). `usage` — разобранный usage-sidecar или ``None``.
    `resolvable_evidence` — по одному флагу на каждую evidence-запись
    **блокирующих** находок, в порядке находок и записей внутри находки.
    `evidence_unchecked` — evidence **не проверялся**: источник фактов о файлах
    недоступен (нет bare-кэша, §7). Тогда `resolvable_evidence` пуст, но пустой
    он и у прогона без блокирующих находок — поэтому «не проверяли» и «нечего
    было проверять» различает именно этот флаг, а не длина кортежа.
    `refuted` — позиции находок `kind: file-missing`, **опровергнутых деревом**
    `head_sha`: файл, которого «нет», на самом деле есть. Опровержение не
    зависит от того, назначил ли её матчер: матчер дерева не читает, а дерево —
    факт. Опровергнутая находка считается FP, её назначение снимается, и в
    очередь adjudication она не идёт — разбирать там нечего.
    `contradicted_gold` — id gold-дефектов, которым была назначена
    опровергнутая находка: gold утверждает «файла нет», дерево говорит
    обратное, значит **разметка кейса** противоречит материалу. Такой дефект не
    даёт TP, уходит из знаменателей recall и печатается в очереди отдельным
    разделом: это правка корпуса, а не решение про находку.
    """

    case: Case
    result: RunResult
    findings: tuple[Mapping[str, object], ...]
    match: MatchResult | None
    usage: Mapping[str, object] | None
    resolvable_evidence: tuple[bool, ...]
    finding_positions: tuple[int, ...] = ()
    evidence_unchecked: bool = False
    refuted: tuple[int, ...] = ()
    contradicted_gold: tuple[str, ...] = ()


def evaluate_case(
    case: Case,
    result: RunResult,
    out_dir: Path,
    *,
    file_lines: Callable[[str], int | None],
) -> CaseEval:
    """Разбирает артефакты одного прогона в `CaseEval`.

    `out_dir` — корень прогона: пути в `RunResult` относительны ему (§10).
    `file_lines(path)` возвращает число строк файла на `head_sha` кейса или
    ``None``, если файла там нет; источник фактов выбирает вызывающий
    (worktree или ``git show``) — метрики не лазают в git сами. Если источника
    нет вовсе (`CacheUnavailable`), прогон помечается `evidence_unchecked`:
    «не проверяли» — это не «ссылка неразрешима», и в метрику такой прогон
    войти не имеет права.

    Матчер вызывается **только** при исходе `verdict`. Расхождение артефактов
    с `result.json` — `MetricsError`, а не тихий пропуск: «вердикт обещан, но
    не читается» и «стоимость обещана, но её нет» искажают метрики молча.

    **Вердикт обязан быть годным при исходе `verdict`.** Раннер пишет этот
    исход по тому же правилу (`threshold.is_schema_valid_verdict`: структура
    плюс схема каждой находки), поэтому несовпадение означает подмену
    артефакта — `MetricsError`, а не тихая фильтрация: прогон, часть вердикта
    которого не прочли или который настоящий гейт отверг бы кодом 2, не имеет
    права считаться годным для метрик качества.

    `file_lines` используется трижды: для флагов разрешимости evidence, для
    **опровержения** находок `kind: file-missing` и для **сверки gold** того же
    вида с деревом (§9). Матчер сопоставляет
    такую находку только с gold того же вида, поэтому неназначенная
    `file-missing` уходила в очередь adjudication — а разбирать там нечего:
    если файл на `head_sha` существует, находка опровергнута деревом, это FP, и
    человеку её показывать не за чем. Нет источника фактов — нет и
    опровержений: `CacheUnavailable` оставляет находку в очереди.
    """
    verdict = _load_sidecar(
        out_dir,
        result.verdict_path,
        required=result.outcome == "verdict",
        what="verdict.json",
        where=_where(result),
    )
    if result.outcome == "verdict" and not is_schema_valid_verdict(verdict):
        raise MetricsError(
            f"{_where(result)}: {result.verdict_path}: verdict.json не структурен или "
            "схемно негоден при outcome=verdict — findings обязан быть списком "
            "объектов, note строкой, и каждая находка годной по схеме кита; "
            "иначе метрики посчитались бы по вердикту, который настоящий гейт "
            "отверг бы кодом 2"
        )
    findings, positions = _findings(verdict)
    usage = _load_sidecar(
        out_dir,
        result.usage_path,
        required=result.cost_status == "available",
        what="usage.json",
        where=_where(result),
    )
    if result.cost_status == "available" and _number(_get(usage, "total_cost_usd")) is None:
        raise MetricsError(
            f"{_where(result)}: cost_status=available, но usage.json без числового "
            "total_cost_usd (D6: стоимость не подставляется нулём)"
        )

    matched: MatchResult | None = None
    if result.outcome == "verdict":
        preds = tuple(
            Prediction(index=pos, finding=f) for pos, f in zip(positions, findings, strict=True)
        )
        matched = match_predictions(preds, case.defects, case.non_defects)

    try:
        resolvable = _resolvable_evidence(findings, file_lines)
        refuted = _refuted_file_missing(findings, positions, file_lines)
        contradicted = _contradicted_gold(case, refuted, matched, file_lines)
        unchecked = False
    except CacheUnavailable:
        # Частично собранные флаги отбрасываются целиком: доля по случайному
        # префиксу ссылок — не измерение. Опровержения — тем же правилом:
        # «файла нет в недоступном кэше» не факт о дереве head.
        resolvable = ()
        refuted = ()
        contradicted = ()
        unchecked = True

    return CaseEval(
        case=case,
        result=result,
        findings=findings,
        match=matched,
        usage=usage,
        resolvable_evidence=resolvable,
        finding_positions=positions,
        evidence_unchecked=unchecked,
        refuted=refuted,
        contradicted_gold=contradicted,
    )


def metrics_for_variant(evals: Sequence[CaseEval]) -> dict[str, object]:
    """Сводка по одному варианту (§9) — готовая к записи в `metrics.json`.

    Знаменатели публикуются рядом с каждым значением, а не выводятся из
    `n_cases`: варианты падают на разных кейсах, и общий `n_cases` скрыл бы,
    что precision посчитан по остатку.

    Метрики качества берут **каждое повторение** как отдельную строку
    (знаменатели кратны числу повторений); `per_repetition` даёт разрез по
    повторениям, `ci` — bootstrap-интервал с ресемплингом кейсов, где
    повторения внутри кейса пулятся в одну пару (числитель, знаменатель).

    Прогоны не-gold кейсов (`draft`) отбрасываются целиком — и из качества, и
    из эксплуатации: официальные метрики только по gold (D1/D8).
    """
    gold = [ev for ev in evals if is_gold(ev.case)]
    matched = [ev for ev in gold if ev.match is not None]
    quality = [ev for ev in gold if is_quality_run(ev)]
    gold_cases = {ev.case.case_id: ev.case for ev in gold}

    metrics: dict[str, object] = {
        "precision_lower_bound": _ratio(*_pooled(_precision_lb_pair, evals)).as_dict(),
    }
    # Противоречие разметки дереву — факт про **корпус**, и он не зависит от
    # исхода прогона, в котором его заметили: очередь и статус считают его по
    # всем прогонам, где отработал матчер, а не только по годным для качества.
    # Прежде противоречие в прогоне с неожидаемым исходом не держало вариант
    # вне `ok`, и отчёт говорил «очередь пуста» при спорящей с деревом разметке.
    if not _queue_open(quality) and not contradicted_gold(matched):
        metrics["precision"] = _ratio(*_pooled(_precision_pair, evals)).as_dict()
    metrics["blocking_recall"] = _ratio(*_pooled(_blocking_recall_pair, evals)).as_dict()
    metrics["detection_recall_any_severity"] = _ratio(
        *_pooled(_detection_recall_pair, evals)
    ).as_dict()
    metrics["false_block_rate"] = _ratio(*_pooled(_false_block_pair, evals)).as_dict()
    unchecked = _unchecked_cases(quality)
    metrics["resolvable_evidence_rate"] = _resolvable_metric(
        *_pooled(_resolvable_pair, evals), unchecked_cases=unchecked
    ).as_dict()
    metrics.update(_operational(gold, matched))

    return {
        "status": _status(gold, quality, precision_published="precision" in metrics),
        "n_cases": len({ev.case.case_id for ev in evals}),
        "n_gold_cases": len(gold_cases),
        "n_runs": len(gold),
        "n_defects": sum(len(case.defects) for case in gold_cases.values()),
        "n_predictions": sum(len(ev.findings) for ev in matched),
        "metrics": metrics,
        "per_repetition": _per_repetition(quality),
        "ci": _ci_by_metric(quality, unchecked_cases=unchecked),
        "cost": _cost(gold),
        "duration": _duration(gold),
    }


def _status(
    gold: Sequence[CaseEval],
    quality: Sequence[CaseEval],
    *,
    precision_published: bool,
) -> str:
    """Статус варианта: `no_gold` → `no_quality_runs` → `pending_adjudication` → `ok`.

    `no_quality_runs` — gold-кейсы есть, но ни один прогон для метрик качества
    не годится (все упали или исход не совпал с ожидаемым). Без этого статуса
    вариант, не выдавший ни одного вердикта, выглядел бы как `ok` с пустыми
    знаменателями — то есть как измеренный.
    """
    if not gold:
        return "no_gold"
    if not quality:
        return "no_quality_runs"
    return "ok" if precision_published else "pending_adjudication"


def bootstrap_ci(
    per_case: Sequence[tuple[int, int]],
    *,
    n: int = 1000,
    seed: int = 0,
) -> tuple[float, float] | None:
    """95 %-интервал отношения ``Σnum / Σden`` бутстрапом **по кейсам**.

    `per_case` — по одной паре (числитель, знаменатель) на кейс; ресемплинг с
    возвращением того же числа кейсов, `n` ресемплов, перцентили 2.5/97.5
    (nearest-rank). Единица ресемплинга — кейс, а не находка: находки внутри
    кейса зависимы, и ресемплинг находок дал бы ложно узкий интервал.

    ``None``, если кейсов с непустым знаменателем меньше двух — тот же порог,
    что у парного интервала (`_paired_diff_ci`): по одному наблюдению любой
    ресемпл повторяет его же, и «интервал» выходит вырожденным не потому, что
    разброса нет, а потому, что мерить разброс не на чем.
    Детерминирована по `seed` (`random.Random(seed)`), результат всегда в
    ``[0, 1]`` при ``num ≤ den``. Пары с нулевым знаменателем остаются в
    популяции: они часть выборки кейсов, а не отсутствующие наблюдения.
    """
    return _ratio_ci([(float(num), float(den)) for num, den in per_case], n=n, seed=seed)


def compare(
    a: dict[str, object],
    b: dict[str, object],
    paired: Sequence[tuple[CaseEval, CaseEval]],
    *,
    n: int = 1000,
    seed: int = 0,
) -> dict[str, object]:
    """Парное сравнение вариантов: разница ``b − a`` по общим кейсам (§9, D12).

    `a`/`b` — выходы `metrics_for_variant` (источник **контекстного**
    `variant_value`), `paired` — прогоны **одних и тех же** кейсов слева и
    справа. Единица ресемплинга — **кейс**, а не пара прогонов: при
    `--repetitions N` один кейс даёт N пар, и ресемплинг пар выдал бы
    повторения за независимые наблюдения (тот же пулинг, что в `ci` варианта, —
    `_pool_by_case`).

    **Значение каждой стороны считается по одной и той же популяции** — по
    общим парным кейсам, со своим числителем и знаменателем:

        "a": {"value", "numerator", "denominator", "n_runs", "variant_value"}

    Иначе сравнение смешивало бы популяции: вариант, упавший на трудных
    кейсах, отвечает только на лёгкие и выглядит лучше по **обеим** метрикам
    сразу — и по precision, и по false-block. Знаменатель рядом со значением —
    единственное, что отличает «лучше» от «мерили на половине» (§9);
    `variant_value` (значение по всему варианту) остаётся для контекста, но
    первичны парные числа, и разница считается по ним.

    Интервал считается, только если общих кейсов ≥ 2 **и** у обеих сторон ≥ 2
    кейсов с непустым знаменателем; иначе `note` = «без CI» (случай одного
    кейса, в том числе с несколькими повторениями).

    Пара с разными `case_id` — `MetricsError`: «парность» такого сравнения
    была бы мнимой, а разница — между разными кейсами.
    """
    for left, right in paired:
        if left.case.case_id != right.case.case_id:
            raise MetricsError(
                "compare: пара ссылается на разные кейсы "
                f"({left.case.case_id} vs {right.case.case_id}) — сравнение не парное"
            )
    lefts = [left for left, _ in paired]
    rights = [right for _, right in paired]
    metrics: dict[str, object] = {}
    for name in COMPARE_METRICS:
        extract = _FLOAT_PAIRS[name]
        pooled_a = _pool_by_case(lefts, extract)
        pooled_b = _pool_by_case(rights, extract)
        case_ids = sorted(set(pooled_a) & set(pooled_b))
        pairs_a = [pooled_a[case_id] for case_id in case_ids]
        pairs_b = [pooled_b[case_id] for case_id in case_ids]
        side_a = _compare_side(pairs_a, lefts, case_ids, variant_value=_metric_value(a, name))
        side_b = _compare_side(pairs_b, rights, case_ids, variant_value=_metric_value(b, name))
        value_a, value_b = side_a["value"], side_b["value"]
        diff = (
            None
            if not isinstance(value_a, float) or not isinstance(value_b, float)
            else value_b - value_a
        )
        interval = _paired_diff_ci(pairs_a, pairs_b, n=n, seed=seed)
        metrics[name] = {
            "a": side_a,
            "b": side_b,
            "diff": diff,
            "ci": None if interval is None else [interval[0], interval[1]],
            "note": NO_CI_NOTE if interval is None else None,
        }
    return {
        "n_common_cases": len({left.case.case_id for left, _ in paired}),
        "n_pairs": len(paired),
        "metrics": metrics,
    }


def _compare_side(
    pairs: Sequence[tuple[float, float]],
    evals: Sequence[CaseEval],
    case_ids: Sequence[str],
    *,
    variant_value: float | None,
) -> dict[str, object]:
    """Одна сторона сравнения по общим кейсам: значение со своим знаменателем.

    `n_runs` — сколько прогонов этой стороны попало в популяцию сравнения;
    рядом со знаменателем он и отвечает на вопрос «сколько из них реально
    измерено» (прогон без вердикта даёт ``(0, 0)`` и в знаменатель не входит).
    """
    numerator = math.fsum(num for num, _ in pairs)
    denominator = math.fsum(den for _, den in pairs)
    wanted = set(case_ids)
    return {
        "value": numerator / denominator if denominator > 0 else None,
        "numerator": _compact(numerator),
        "denominator": _compact(denominator),
        "n_runs": sum(1 for ev in evals if ev.case.case_id in wanted),
        "variant_value": variant_value,
    }


def _compact(value: float) -> float | int:
    """Целое число — как `int` (знаменатели читаются как счёт, а не как `4.0`)."""
    return int(value) if float(value).is_integer() else value


# ---------------------------------------------------------------------------
# Разбор артефактов прогона
# ---------------------------------------------------------------------------


def _load_sidecar(
    out_dir: Path,
    relative: str | None,
    *,
    required: bool,
    what: str,
    where: str,
) -> Mapping[str, object] | None:
    """Sidecar-JSON как объект; ``None`` только если путь не был объявлен.

    `required` — исход прогона обещает **содержимое** этого файла (`verdict`
    обещает вердикт, `cost_status: available` обещает usage с числом): при
    `relative is None` (путь не объявлен вовсе) непредоставленное содержимое
    — `MetricsError`, а без `required` — легитимное «метрика не считается».

    **Объявленный путь (`relative is not None`) обязан читаться всегда,
    независимо от `required`.** Раннер выставляет `verdict_path`/`usage_path`
    только когда файл на момент записи `result.json` был непуст
    (`_is_non_empty`); отсутствие или порча файла позже — рассогласование
    `result.json` с диском (данные потеряны или подменены), а не «метрика
    просто не считается». Раньше это давало разные коды выхода в
    зависимости от `required` — то есть от значения `cost_status`/`outcome`,
    к самому факту потери файла отношения не имеющего (ревью-находка части
    3, minor): `usage_path` без файла при `cost_status: available` шёл
    кодом 3 отсюда, а тот же самый пропавший файл при `cost_status:
    unavailable` — кодом 2 из отдельной проверки в `runner.load_results`.
    Теперь потеря объявленного sidecar — всегда `MetricsError` здесь,
    `load_results` эту проверку не дублирует вовсе.
    """
    if relative is None:
        if required:
            raise MetricsError(f"{where}: исход требует {what}, но путь в result.json пуст")
        return None
    path = out_dir / relative
    payload = _read_json(path)
    if not isinstance(payload, Mapping):
        raise MetricsError(f"{where}: {path} не читается как JSON-объект")
    return payload


def _read_json(path: Path) -> object:
    """JSON из файла или ``None``, если файла нет / он не разбирается."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _findings(
    verdict: Mapping[str, object] | None,
) -> tuple[tuple[Mapping[str, object], ...], tuple[int, ...]]:
    """Находки вердикта и их **позиции в исходном списке**.

    Не-объекты отбрасываются (в вердикте по схеме их нет, но негодный вердикт
    схему и не проходил) — **без перенумерации остатка**: позиция в
    ``verdict.json.findings`` это адрес находки для очереди adjudication и
    отчёта, и сдвинуть его значило бы указать разбирающему на чужую строку.
    """
    if verdict is None:
        return ((), ())
    raw = verdict.get("findings")
    if not isinstance(raw, list):
        return ((), ())
    kept = [(pos, item) for pos, item in enumerate(raw) if isinstance(item, Mapping)]
    return tuple(item for _, item in kept), tuple(pos for pos, _ in kept)


def _refuted_file_missing(
    findings: Sequence[Mapping[str, object]],
    positions: Sequence[int],
    file_lines: Callable[[str], int | None],
) -> tuple[int, ...]:
    """Позиции находок «файла нет», которым дерево `head_sha` возражает (§9).

    **Назначение не спасает находку.** Матчер дерева не читает: он сопоставляет
    `file-missing` с gold того же вида по пути и ключевым словам, и такая пара
    выглядит идеальной, даже когда файл на месте. Утверждение «файла нет» при
    существующем файле ложно — значит и предсказание FP, и gold, утверждающий
    то же, противоречит материалу (`_contradicted_gold`). Прежде опровергались
    только `unlabeled`, и назначенная ложная находка становилась TP.

    Путь передаётся **сырым**, без `normalize_path`: у `file_lines`
    (`_file_lines_at`) уже есть своё правило «сырой путь сперва, нормализация —
    запасной вариант», и предварительная нормализация здесь уничтожала бы
    литеральный обратный слэш в настоящем git-имени раньше, чем до него
    доходил сырой поиск — различить символ имени и виндовый разделитель
    путей может только сам поиск по дереву, знающий оба варианта.
    """
    refuted: list[int] = []
    for pos, item in zip(positions, findings, strict=True):
        if item.get("kind") != FILE_MISSING_KIND:
            continue
        path = item.get("file")
        if not isinstance(path, str) or not path.strip():
            continue
        if file_lines(path) is not None:
            refuted.append(pos)
    return tuple(refuted)


def _contradicted_gold(
    case: Case,
    refuted: Sequence[int],
    matched: MatchResult | None,
    file_lines: Callable[[str], int | None],
) -> tuple[str, ...]:
    """Id gold-дефектов, которым возражает дерево `head_sha` (§9).

    Дерево — материал кейса, разметка — утверждение о нём. Если gold говорит
    «обязательного файла нет», а файл есть, неверна **разметка**, и чинить её
    человеку: варианту нельзя ни зачесть такой дефект как найденный, ни
    требовать его найти.

    Проверяется **каждый** gold-дефект `kind: file-missing`, независимо от
    находок. Прежде противоречие замечалось только через назначенную
    опровергнутую находку, то есть требовало, чтобы модель повторила то же
    ложное утверждение: молчаливая модель оставляла негодный gold в
    знаменателе recall. Сторона предсказаний осталась — она ловит случай, когда
    назначение состоялось, а `kind` gold по какой-то причине иной.

    Источник фактов недоступен (`CacheUnavailable`) — исключение уходит наружу
    к вызывающему, и там прогон помечается `evidence_unchecked`: сверять gold
    было нечем, и объявлять его противоречащим нельзя.
    """
    ids: set[str] = set()
    for defect in case.defects:
        if defect.kind != FILE_MISSING_KIND:
            continue
        # Сырой путь — та же причина, что у `_refuted_file_missing`: нормализация
        # здесь стёрла бы литеральный backslash раньше, чем до него дошёл бы
        # сырой поиск в `file_lines`.
        if file_lines(defect.file) is not None:
            ids.add(defect.id)
    if matched is not None:
        ids.update(matched.assigned[pos] for pos in refuted if pos in matched.assigned)
    return tuple(sorted(ids))


def _resolvable_evidence(
    findings: Sequence[Mapping[str, object]],
    file_lines: Callable[[str], int | None],
) -> tuple[bool, ...]:
    """Флаги «ссылка разрешима» по evidence блокирующих находок (§9, D11).

    Проверяется только адресуемость: файл существует на head и строка лежит
    в файле. Поддерживает ли evidence сценарий — ручная разметка, не здесь.
    """
    flags: list[bool] = []
    for finding in findings:
        if not is_blocking(finding):
            continue
        evidence = finding.get("evidence")
        if not isinstance(evidence, list):
            continue
        flags.extend(_is_resolvable(item, file_lines) for item in evidence)
    return tuple(flags)


def _is_resolvable(item: object, file_lines: Callable[[str], int | None]) -> bool:
    """Разрешима ли одна evidence-запись.

    ``line: 0`` — легитимный указатель уровня файла (§9): разрешим, если файл
    существует. Нецелая или отсутствующая строка — не разрешима: адреса нет.
    """
    if not isinstance(item, Mapping):
        return False
    file = item.get("file")
    if not isinstance(file, str) or not file.strip():
        return False
    # Сырой путь — `file_lines` (`_file_lines_at`) сам пробует его первым и
    # только потом нормализованный: предварительная нормализация здесь
    # стирала бы литеральный backslash настоящего git-имени раньше, чем до
    # него доходил бы сырой поиск.
    lines = file_lines(file)
    if lines is None:
        return False
    line = as_line_number(
        item.get("line")
    )  # тот же контракт, что у схемы и матчера: `10.0` — строка 10
    if line is None:
        return False
    if line == 0:
        return True
    return 1 <= line <= lines


def _where(result: RunResult) -> str:
    """Человекочитаемый адрес прогона для сообщений об ошибке."""
    return f"{result.case_id}/{result.variant}/{result.repetition_id}"


# ---------------------------------------------------------------------------
# Пары (числитель, знаменатель) по прогону — сами решают, входит ли прогон
# ---------------------------------------------------------------------------


def is_quality_run(ev: CaseEval) -> bool:
    """Годен ли прогон для метрик качества.

    Gold (D1/D8), исход `verdict`, исход совпал с ожидаемым (§6.6:
    `unexpected_outcome` в качестве не участвует) и матчер отработал.

    Публичный предикат: отчёт обязан делить очередь adjudication ровно по тому
    же признаку, по которому метрики решают, считать ли прогон, — иначе рядом
    со `status: ok` появляется «непустая очередь» без объяснения.
    """
    return (
        is_gold(ev.case)
        and ev.result.outcome == "verdict"
        and not ev.result.unexpected
        and ev.match is not None
    )


def _positions(ev: CaseEval) -> tuple[int, ...]:
    """Позиции находок в исходном вердикте; пустое поле — позиции по порядку."""
    if ev.finding_positions:
        return ev.finding_positions
    return tuple(range(len(ev.findings)))


def _blocking_indices(ev: CaseEval) -> tuple[int, ...]:
    """Позиции блокирующих находок — ровно предикат `is_blocking` (§9).

    Возвращаются позиции в вердикте (ключи матчера), а не индексы кортежа
    `findings`: они совпадают ровно тогда, когда вердикт без мусорных
    элементов.
    """
    return tuple(
        pos
        for pos, finding in zip(_positions(ev), ev.findings, strict=True)
        if is_blocking(finding)
    )


def _gold_blocking_ids(ev: CaseEval) -> frozenset[str]:
    """Id gold-дефектов major/blocker **без противоречащих дереву** (§9).

    Дефект, которому возразило дерево `head_sha` (`contradicted_gold`), из
    знаменателей recall уходит: требовать найти дефект, которого в материале
    нет, значит наказывать вариант за правильное поведение. Он и в числитель
    попасть не может — назначение снято вместе с опровержением находки.
    """
    contradicted = frozenset(ev.contradicted_gold)
    return frozenset(
        d.id for d in ev.case.defects if d.severity in GOLD_SEVERITIES and d.id not in contradicted
    )


def effective_assigned(ev: CaseEval) -> dict[int, str]:
    """Назначения матчера **без опровергнутых** находок (§9).

    Опровержение деревом отменяет назначение: находка «файла нет» при
    существующем файле ложна, и пара с gold перестаёт быть парой.
    """
    if ev.match is None:
        return {}
    refuted = frozenset(ev.refuted)
    return {pos: gold_id for pos, gold_id in ev.match.assigned.items() if pos not in refuted}


def _true_positives(ev: CaseEval) -> tuple[int, ...]:
    """Блокирующие находки, назначенные gold-дефекту major/blocker.

    Назначение на gold-`minor` сюда не входит — это FP на блокирующем пороге
    (§9): модель красит гейт там, где gold красить не велит.
    """
    if ev.match is None:
        return ()
    gold = _gold_blocking_ids(ev)
    assigned = effective_assigned(ev)
    return tuple(i for i in _blocking_indices(ev) if assigned.get(i) in gold)


def unlabeled_predictions(ev: CaseEval) -> frozenset[int]:
    """`unlabeled` **без опровергнутых** — то, что действительно ждёт разметки.

    Опровергнутая деревом `head_sha` находка «файла нет» размечена фактом, а не
    человеком: показывать её в очереди значило бы просить разобрать то, что уже
    разобрано. В FP она при этом входит (знаменатель
    `precision_lower_bound` — все блокирующие предсказания).
    """
    if ev.match is None:
        return frozenset()
    return frozenset(ev.match.unlabeled) - frozenset(ev.refuted)


def _queued(ev: CaseEval) -> frozenset[int]:
    """Предсказания в очереди adjudication: `unlabeled` + компоненты (D9, §8.3)."""
    if ev.match is None:
        return frozenset()
    ambiguous = {index for component in ev.match.ambiguous for index in component.predictions}
    return unlabeled_predictions(ev) | ambiguous


def contradicted_gold(evals: Iterable[CaseEval]) -> tuple[str, ...]:
    """Все id gold-дефектов, которым возразило дерево head, по прогонам варианта."""
    return tuple(sorted({gold_id for ev in evals for gold_id in ev.contradicted_gold}))


def _queue_open(evals: Iterable[CaseEval]) -> bool:
    """Есть ли хоть один неразмеченный блокирующий prediction или компонента.

    Пока да — официальный `precision` не публикуется (D9), у варианта
    `status: pending_adjudication`.

    Противоречащий дереву gold открывает очередь наравне с ними: пока разметка
    кейса спорит с материалом, метрики варианта посчитаны по корпусу, который
    сам требует правки, и объявлять их окончательными нельзя.
    """
    for ev in evals:
        if ev.contradicted_gold:
            return True
        if ev.match is None:
            continue
        if ev.match.ambiguous:
            return True
        queued = _queued(ev)
        if any(i in queued for i in _blocking_indices(ev)):
            return True
    return False


@dataclass(frozen=True)
class QueueCounts:
    """Очередь adjudication в двух половинах — гейтящая и остальная (D9, §8.3).

    Гейтящая — ровно то, из-за чего `precision` не публикуется (`_queue_open`):
    блокирующие предсказания без разметки и неоднозначные компоненты **годных
    для качества** прогонов. Остальная — draft-кейсы, прогоны без вердикта и
    неблокирующие находки: их разбор ничего не разблокирует, и без этого
    деления непустая очередь стоит рядом со `status: ok` как противоречие.

    `contradicted_gold` считается **по всем** прогонам, где отработал матчер:
    разметка спорит с деревом независимо от того, каким вышел исход прогона, в
    котором это заметили, и `precision` она держит наравне с гейтящей
    половиной.
    """

    gating_predictions: int
    gating_components: int
    other_predictions: int
    other_components: int
    #: Gold-дефекты, которым возражает дерево `head_sha`. Третья половина по
    #: смыслу: это правка **корпуса**, а не решение про находку, и считается
    #: она по всем прогонам с матчингом — исход прогона про разметку ничего не
    #: говорит.
    contradicted_gold: int = 0

    @property
    def total(self) -> int:
        """Вся очередь одним числом — для решения «печатать ли раздел вовсе»."""
        return (
            self.gating_predictions
            + self.gating_components
            + self.other_predictions
            + self.other_components
            + self.contradicted_gold
        )


def queue_counts(evals: Iterable[CaseEval]) -> QueueCounts:
    """Посчитать очередь adjudication по прогонам варианта (см. `QueueCounts`)."""
    gating_predictions = gating_components = 0
    other_predictions = other_components = 0
    contradictions: set[str] = set()
    for ev in evals:
        if ev.match is None:
            continue
        # Независимо от годности прогона: спорит с деревом разметка кейса, а
        # не этот прогон.
        contradictions.update(ev.contradicted_gold)
        queued = _queued(ev)
        blocking = frozenset(_blocking_indices(ev))
        components = len(ev.match.ambiguous)
        if is_quality_run(ev):
            gating_predictions += len(queued & blocking)
            gating_components += components
            other_predictions += len(queued - blocking)
        else:
            other_predictions += len(queued)
            other_components += components
    return QueueCounts(
        gating_predictions=gating_predictions,
        gating_components=gating_components,
        other_predictions=other_predictions,
        other_components=other_components,
        contradicted_gold=len(contradictions),
    )


def _precision_lb_pair(ev: CaseEval) -> tuple[int, int]:
    """TP / все блокирующие: unlabeled и duplicate — FP (строгая оценка, D9)."""
    if not is_quality_run(ev):
        return (0, 0)
    return (len(_true_positives(ev)), len(_blocking_indices(ev)))


def _precision_pair(ev: CaseEval) -> tuple[int, int]:
    """TP / блокирующие без неразмеченных (публикуется при пустой очереди).

    Исключение неразмеченных **недостижимо** при живом вызове: по D9
    `precision` вообще не публикуется, пока очередь непуста, а при пустой
    очереди исключать нечего. Ветка оставлена для симметрии с определением из
    §9 и как страховка, если правило публикации когда-нибудь ослабят.
    """
    if not is_quality_run(ev):
        return (0, 0)
    queued = _queued(ev)
    denominator = sum(1 for i in _blocking_indices(ev) if i not in queued)
    return (len(_true_positives(ev)), denominator)


def _blocking_recall_pair(ev: CaseEval) -> tuple[int, int]:
    """Gold major/blocker, найденные **блокирующим** предсказанием (D11).

    Только кейсы с `blocking_complete: true` (D8): иначе знаменатель —
    «известные дефекты», а не «все».
    """
    if ev.match is None or not is_quality_run(ev) or not ev.case.annotation.blocking_complete:
        return (0, 0)
    gold = _gold_blocking_ids(ev)
    assigned = effective_assigned(ev)
    hit = {assigned[i] for i in _blocking_indices(ev) if i in assigned}
    return (len(gold & hit), len(gold))


def _detection_recall_pair(ev: CaseEval) -> tuple[int, int]:
    """Gold major/blocker, замеченные **любым** предсказанием (D11)."""
    if ev.match is None or not is_quality_run(ev) or not ev.case.annotation.blocking_complete:
        return (0, 0)
    gold = _gold_blocking_ids(ev)
    hit = frozenset(effective_assigned(ev).values())
    return (len(gold & hit), len(gold))


def _false_block_pair(ev: CaseEval) -> tuple[int, int]:
    """Кейс покрашен (код 1) при отсутствии gold major/blocker — на уровне кейса."""
    if not is_quality_run(ev) or not ev.case.annotation.blocking_complete:
        return (0, 0)
    blocked = ev.result.exit_code == 1
    return (1 if blocked and not _gold_blocking_ids(ev) else 0, 1)


def _resolvable_pair(ev: CaseEval) -> tuple[int, int]:
    """Разрешимые evidence-записи / все записи блокирующих находок."""
    if not is_quality_run(ev):
        return (0, 0)
    return (sum(1 for flag in ev.resolvable_evidence if flag), len(ev.resolvable_evidence))


def _cost_pair(ev: CaseEval) -> tuple[float, float]:
    """Стоимость прогона и 1 в знаменателе — только там, где она сообщена (D6).

    Исход не важен: неуспешные прогоны тоже стоят денег (D12). Прогон без
    provider-стоимости даёт ``(0, 0)`` — он **не** удешевляет среднюю.
    """
    if not is_gold(ev.case) or ev.result.cost_status != "available":
        return (0.0, 0.0)
    cost = _number(_get(ev.usage, "total_cost_usd"))
    if cost is None:
        return (0.0, 0.0)
    return (cost, 1.0)


_QUALITY_PAIRS: dict[str, Callable[[CaseEval], tuple[int, int]]] = {
    "precision_lower_bound": _precision_lb_pair,
    "blocking_recall": _blocking_recall_pair,
    "detection_recall_any_severity": _detection_recall_pair,
    "false_block_rate": _false_block_pair,
    "resolvable_evidence_rate": _resolvable_pair,
}
"""Метрики качества как пары (числитель, знаменатель) по прогону.

Ключи — `CI_METRICS`; отсутствие ключа здесь сразу валит любой прогон, поэтому
списки не разъедутся молча.
"""


def _as_float_extractor(
    pair: Callable[[CaseEval], tuple[int, int]],
) -> Callable[[CaseEval], tuple[float, float]]:
    """Целочисленную пару — во `float`, чтобы бутстрап был один на все метрики."""
    return lambda ev: _as_floats(pair(ev))


_FLOAT_PAIRS: dict[str, Callable[[CaseEval], tuple[float, float]]] = {
    **{name: _as_float_extractor(pair) for name, pair in _QUALITY_PAIRS.items()},
    "cost_usd_mean_per_case": _cost_pair,
}


# ---------------------------------------------------------------------------
# Агрегация
# ---------------------------------------------------------------------------


def _pooled(
    pair: Callable[[CaseEval], tuple[int, int]],
    evals: Iterable[CaseEval],
) -> tuple[int, int]:
    """Сумма числителей и знаменателей по прогонам."""
    numerator = 0
    denominator = 0
    for ev in evals:
        num, den = pair(ev)
        numerator += num
        denominator += den
    return numerator, denominator


def _ratio(numerator: int, denominator: int, note: str | None = None) -> Metric:
    """Метрика-доля; нулевой знаменатель → `value: None` (не ноль)."""
    value = numerator / denominator if denominator else None
    return Metric(value=value, numerator=numerator, denominator=denominator, note=note)


def _operational(gold: Sequence[CaseEval], matched: Sequence[CaseEval]) -> dict[str, object]:
    """Эксплуатационные метрики: по всем gold-прогонам, независимо от исхода (§9).

    `valid_verdict_rate` делится на прогоны с `reviewer_ran` — включая
    `invalid_verdict`: иначе вариант, регулярно выдающий негодные вердикты,
    выглядел бы как вариант с проблемной конфигурацией и `1.0` по валидности.
    `duplicate_rate` считается по прогонам, где матчер отработал: в прогоне
    без матчинга дубликат физически не мог быть обнаружен. То же у
    `refuted_file_missing_count`: опровергать нечего там, где не сопоставляли.
    """
    runs = len(gold)
    completed = sum(
        1
        for ev in gold
        if ev.result.outcome == "verdict"
        or (
            ev.result.outcome == "guardrail_rejection"
            and ev.case.expected_outcome == "guardrail_rejection"
        )
    )
    reviewer_ran = [ev for ev in gold if ev.result.reviewer_ran]
    unexpected = sum(1 for ev in gold if ev.result.unexpected)
    empty_range = sum(1 for ev in gold if ev.result.outcome == EMPTY_RANGE_OUTCOME)
    duplicates = sum(len(ev.match.duplicates) for ev in matched if ev.match is not None)
    predictions = sum(len(ev.findings) for ev in matched)
    refuted = sum(len(ev.refuted) for ev in matched)
    # По прогонам, как и знаменатель: эксплуатационные метрики считают каждое
    # повторение отдельной строкой, и `set` по id занижал бы числитель.
    contradicted = sum(len(ev.contradicted_gold) for ev in matched)
    gold_defects = sum(len(ev.case.defects) for ev in matched)
    return {
        "completion_rate": _ratio(completed, runs).as_dict(),
        # Знаменатель — прогоны, где гейт дошёл до оценки вывода модели:
        # `config_failure` (нет jq, негодный аргумент порога) — сбой инструмента и
        # уже посчитан в `config_failure_rate`; годный при этом вердикт нельзя
        # записывать модели как негодный.
        "valid_verdict_rate": _ratio(
            sum(1 for ev in reviewer_ran if ev.result.outcome == "verdict"),
            sum(1 for ev in reviewer_ran if ev.result.outcome != "config_failure"),
        ).as_dict(),
        "config_failure_rate": _ratio(
            sum(1 for ev in gold if ev.result.outcome == "config_failure"), runs
        ).as_dict(),
        "mechanical_failure_rate": _ratio(
            sum(1 for ev in gold if ev.result.outcome == "mechanical_failure"), runs
        ).as_dict(),
        "unexpected_outcome_count": Metric(
            value=float(unexpected),
            numerator=unexpected,
            denominator=runs,
            note=(
                "счётчик, не доля; пересекается с config_failure_rate и "
                "mechanical_failure_rate по построению — один прогон бывает и "
                "неожиданным, и упавшим"
            ),
        ).as_dict(),
        "empty_range_count": Metric(
            value=float(empty_range),
            numerator=empty_range,
            denominator=runs,
            note=(
                "счётчик, не доля; кейсы, у которых base_sha и head_sha дают "
                "пустой диф — ревьюировать нечего, кейс негоден: это проблема "
                "корпуса, а не инструмента, и в метрики качества такой прогон "
                "не входит"
            ),
        ).as_dict(),
        "duplicate_rate": _ratio(duplicates, predictions).as_dict(),
        "refuted_file_missing_count": Metric(
            value=float(refuted),
            numerator=refuted,
            denominator=predictions,
            note=(
                "счётчик, не доля; находки 'файла нет', опровергнутые деревом "
                "head_sha: они считаются FP в precision_lower_bound и в очередь "
                "adjudication не попадают"
            ),
        ).as_dict(),
        "contradicted_gold_count": Metric(
            value=float(contradicted),
            numerator=contradicted,
            denominator=gold_defects,
            note=(
                "счётчик, не доля; gold-дефекты 'файла нет', которым возражает "
                "дерево head_sha: они исключены из знаменателей recall, TP не "
                "дают и держат вариант вне status: ok, пока разметка не поправлена"
            ),
        ).as_dict(),
    }


def _cost(gold: Sequence[CaseEval]) -> dict[str, object]:
    """Стоимость варианта (D6, D12): суммы только по сообщённым значениям.

    Отсутствующая стоимость не подставляется нулём ни в сумму, ни в среднюю:
    она уходит в `cost_unavailable_cases`, а средняя помечается
    `partial: true`. Полное отсутствие данных — ``None``, не ``0.0``.

    Единица счёта — **прогон**, а не кейс: при `--repetitions N` один кейс
    стоит N прогонов, и «средняя на кейс» без этой пометки читалась бы как
    цена кейса. Имена ключей — из спеки §9, единица объявлена в `notes`, чтобы
    отчёт печатал её, а не догадывался; `n_runs_with_cost`/`n_runs_total`
    показывают охват (`cost_available_cases` — то же число под прежним именем).
    """
    pairs = [_cost_pair(ev) for ev in gold]
    available = [cost for cost, weight in pairs if weight > 0]
    unavailable = sum(1 for _, weight in pairs if weight == 0)
    total = math.fsum(available) if available else None
    mean = total / len(available) if total is not None and available else None
    return {
        "cost_usd_total": total,
        "cost_usd_mean_per_case": mean,
        "cost_available_cases": len(available),
        "cost_unavailable_cases": unavailable,
        "n_runs_with_cost": len(available),
        "n_runs_total": len(gold),
        "partial": unavailable > 0,
        "notes": {
            "cost_usd_mean_per_case": 'unit: "run"',
            "cost_unavailable_cases": 'unit: "run"',
        },
    }


def _duration(gold: Sequence[CaseEval]) -> dict[str, object]:
    """Длительность: wall-clock раннера (D5) и provider-метрика отдельно.

    Считаются прогоны, где кит **вызывался** (в том числе упавший на
    префлайте — его время измерено): `empty_range` кита не запускает и пишет
    `wall_clock_s = 0.0`, а по значению это не отличить от «мерили и вышло
    ноль» — исключение идёт по исходу.
    """
    walls = [ev.result.wall_clock_s for ev in gold if ev.result.outcome != EMPTY_RANGE_OUTCOME]
    provider = [
        value
        for ev in gold
        if (value := _number(_get(ev.usage, "provider_duration_ms"))) is not None
    ]
    return {
        "wall_clock_s_mean": statistics.fmean(walls) if walls else None,
        "wall_clock_s_median": statistics.median(walls) if walls else None,
        "wall_clock_s_p90": _percentile(walls, 0.9),
        "provider_duration_ms_mean": statistics.fmean(provider) if provider else None,
        "n_runs": len(walls),
        "n_provider_duration_runs": len(provider),
    }


def _unchecked_cases(quality: Sequence[CaseEval]) -> int:
    """Сколько **кейсов** варианта остались с непроверенным evidence (§7)."""
    return len({ev.case.case_id for ev in quality if ev.evidence_unchecked})


def _resolvable_metric(numerator: int, denominator: int, *, unchecked_cases: int) -> Metric:
    """`resolvable_evidence_rate` с учётом непроверенных кейсов (D11, §7).

    Хотя бы один непроверенный кейс — значение ``None`` и пометка: доля по
    проверенному остатку это доля другой популяции, а не «доля разрешимых
    ссылок варианта». Знаменатель при этом публикуется как есть — он и
    показывает, сколько всё-таки проверили (``0/0``, если ничего).
    """
    if unchecked_cases:
        return Metric(
            value=None,
            numerator=numerator,
            denominator=denominator,
            note=f"{CACHE_UNAVAILABLE_NOTE} ({unchecked_cases} кейсов)",
        )
    return _ratio(numerator, denominator)


def _per_repetition(quality: Sequence[CaseEval]) -> dict[str, object]:
    """Разрез метрик качества по повторениям (D12); ключ — `str(repetition_id)`.

    `resolvable_evidence_rate` и здесь молчит о варианте, чей evidence не
    проверялся: разрез не имеет права публиковать значение, которого нет в
    сводке.
    """
    by_rep: dict[int, list[CaseEval]] = {}
    for ev in quality:
        by_rep.setdefault(ev.result.repetition_id, []).append(ev)
    return {
        str(rep): {name: _repetition_metric(name, rows).as_dict() for name in CI_METRICS}
        for rep, rows in sorted(by_rep.items())
    }


def _repetition_metric(name: str, rows: Sequence[CaseEval]) -> Metric:
    """Метрика одного повторения; для evidence — с той же оговоркой, что в сводке."""
    numerator, denominator = _pooled(_QUALITY_PAIRS[name], rows)
    if name == "resolvable_evidence_rate":
        return _resolvable_metric(numerator, denominator, unchecked_cases=_unchecked_cases(rows))
    return _ratio(numerator, denominator)


def _ci_by_metric(quality: Sequence[CaseEval], *, unchecked_cases: int) -> dict[str, object]:
    """Bootstrap-CI по кейсам; повторения кейса пулятся в одну пару (D12).

    У `resolvable_evidence_rate` интервала нет, пока есть непроверенные кейсы:
    интервал без точечного значения читался бы как «значение всё-таки есть».
    """
    intervals = {
        name: _interval_json(bootstrap_ci(_pairs_by_case(quality, _QUALITY_PAIRS[name])))
        for name in CI_METRICS
    }
    if unchecked_cases:
        intervals["resolvable_evidence_rate"] = None
    return intervals


def _pairs_by_case(
    quality: Sequence[CaseEval],
    pair: Callable[[CaseEval], tuple[int, int]],
) -> list[tuple[int, int]]:
    """По одной целочисленной паре на кейс, в порядке `case_id`."""
    pooled = _pool_by_case(quality, _as_float_extractor(pair))
    return [(int(pooled[key][0]), int(pooled[key][1])) for key in sorted(pooled)]


def _pool_by_case(
    evals: Iterable[CaseEval],
    extract: Callable[[CaseEval], tuple[float, float]],
) -> dict[str, tuple[float, float]]:
    """Пары по кейсам: повторения кейса суммируются в одну пару.

    Единица независимости — **кейс**: ресемплить повторения одного кейса как
    независимые наблюдения значило бы сузить интервал обманом. Этим же
    пулингом пользуется `compare`, поэтому вариантный и парный интервалы
    считают одну и ту же популяцию.
    """
    by_case: dict[str, tuple[float, float]] = {}
    for ev in evals:
        num, den = extract(ev)
        prev = by_case.get(ev.case.case_id, (0.0, 0.0))
        by_case[ev.case.case_id] = (prev[0] + num, prev[1] + den)
    return by_case


def _interval_json(interval: tuple[float, float] | None) -> list[float] | None:
    """Интервал в JSON-форме (список), ``None`` — если не посчитан."""
    return None if interval is None else [interval[0], interval[1]]


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def _ratio_ci(
    per_case: Sequence[tuple[float, float]],
    *,
    n: int,
    seed: int,
) -> tuple[float, float] | None:
    """Перцентильный bootstrap-CI отношения сумм по ресемплам кейсов.

    ``None``, если кейсов с непустым знаменателем меньше двух (см.
    `bootstrap_ci`): порог совпадает с парным интервалом, чтобы вариантный и
    парный разбросы не жили по разным правилам.
    """
    if sum(1 for _, den in per_case if den > 0) < 2:
        return None
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(n):
        picks = rng.choices(per_case, k=len(per_case))
        denominator = math.fsum(den for _, den in picks)
        if denominator <= 0:
            continue
        samples.append(math.fsum(num for num, _ in picks) / denominator)
    return _interval(samples)


def _paired_diff_ci(
    pairs_a: Sequence[tuple[float, float]],
    pairs_b: Sequence[tuple[float, float]],
    *,
    n: int,
    seed: int,
) -> tuple[float, float] | None:
    """Парный bootstrap разницы ``b − a``: ресемплятся **пары кейсов**.

    Вход — уже пулированные по кейсу пары (`_pool_by_case`), по одной на кейс
    с каждой стороны. ``None``, если общих кейсов меньше двух или у какой-то
    стороны меньше двух кейсов с непустым знаменателем: интервал по одному
    наблюдению — это не интервал.
    """
    size = len(pairs_a)
    if size != len(pairs_b):
        raise MetricsError("парный bootstrap: стороны разной длины")
    if size < 2:
        return None
    if sum(1 for _, den in pairs_a if den > 0) < 2:
        return None
    if sum(1 for _, den in pairs_b if den > 0) < 2:
        return None
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(n):
        indices = rng.choices(range(size), k=size)
        den_a = math.fsum(pairs_a[i][1] for i in indices)
        den_b = math.fsum(pairs_b[i][1] for i in indices)
        if den_a <= 0 or den_b <= 0:
            continue
        ratio_a = math.fsum(pairs_a[i][0] for i in indices) / den_a
        ratio_b = math.fsum(pairs_b[i][0] for i in indices) / den_b
        samples.append(ratio_b - ratio_a)
    return _interval(samples)


def _interval(samples: Sequence[float]) -> tuple[float, float] | None:
    """Перцентили 2.5/97.5 набора ресемплов; ``None`` для пустого набора."""
    low = _percentile(samples, 0.025)
    high = _percentile(samples, 0.975)
    if low is None or high is None:
        return None
    return (low, high)


def _percentile(values: Sequence[float], q: float) -> float | None:
    """Перцентиль nearest-rank (без интерполяции — она даёт значения, которых
    в выборке не было); ``None`` для пустой выборки."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(q * len(ordered)) - 1)
    return ordered[min(index, len(ordered) - 1)]


# ---------------------------------------------------------------------------
# Мелкие утилиты
# ---------------------------------------------------------------------------


def _metric_value(summary: Mapping[str, object], name: str) -> float | None:
    """Точечное значение метрики из выхода `metrics_for_variant`.

    Отсутствующий ключ (подавленный `precision`) и `value: null` дают
    ``None`` — в сравнении они одинаково «не измерено».
    """
    if name.startswith("cost_"):
        cost = summary.get("cost")
        return _number(_get(cost if isinstance(cost, Mapping) else None, name))
    metrics = summary.get("metrics")
    if not isinstance(metrics, Mapping):
        return None
    entry = metrics.get(name)
    if not isinstance(entry, Mapping):
        return None
    return _number(entry.get("value"))


def _get(payload: Mapping[str, object] | None, key: str) -> object:
    """Значение по ключу или ``None``, если объекта нет."""
    return None if payload is None else payload.get(key)


def _number(value: object) -> float | None:
    """`float` для числового значения (``bool`` — не число), иначе ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _as_floats(pair: tuple[int, int]) -> tuple[float, float]:
    """Целочисленная пара как пара `float` — для общего кода бутстрапа."""
    return (float(pair[0]), float(pair[1]))
