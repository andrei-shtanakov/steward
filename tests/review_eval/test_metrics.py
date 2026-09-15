"""Тесты steward.review_eval.metrics: формулы, знаменатели, статусы, bootstrap.

Модель не вызывается и раннер не запускается: `CaseEval` собираются руками из
`Case`/`RunResult`/находок, матчер — настоящий (он и решает, что назначено).
Каждая формула проверяется на крошечном наборе с выписанной в имени/докстринге
дробью: тест обязан падать при подмене знаменателя, а не только значения.

Дизайн: docs/superpowers/specs/2026-09-14-review-eval-harness-design.md §9.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from steward.review_eval.cache import CacheUnavailable
from steward.review_eval.corpus import Annotation, Case, Defect, Match, NonDefect
from steward.review_eval.matcher import Prediction, match
from steward.review_eval.metrics import (
    CI_METRICS,
    CaseEval,
    Metric,
    MetricsError,
    bootstrap_ci,
    compare,
    evaluate_case,
    metrics_for_variant,
    queue_counts,
)
from steward.review_eval.runner import RunResult

# ---------------------------------------------------------------------------
# Фикстуры-конструкторы
# ---------------------------------------------------------------------------


def make_match(file: str, keywords: tuple[str, ...] = ("boom",), window: int = 5) -> Match:
    """Правило матчинга gold-записи."""
    return Match(files=(file,), line_window=window, keywords_any=keywords)


def make_defect(
    defect_id: str = "D-steward-1-1",
    *,
    severity: str = "major",
    file: str = "app/a.py",
    line_hint: int = 10,
    keywords: tuple[str, ...] = ("boom",),
) -> Defect:
    """Gold-дефект с одним файлом и одним ключевым словом."""
    return Defect(
        id=defect_id,
        severity=severity,
        file=file,
        line_hint=line_hint,
        scenario="сценарий",
        evidence=(),
        match=make_match(file, keywords),
    )


def make_non_defect(
    non_defect_id: str = "NF-steward-1-1",
    *,
    file: str = "app/known.py",
    line_hint: int = 5,
    keywords: tuple[str, ...] = ("known",),
) -> NonDefect:
    """Размеченный known-FP."""
    return NonDefect(
        id=non_defect_id,
        file=file,
        line_hint=line_hint,
        scenario="сценарий",
        match=make_match(file, keywords),
    )


def make_case(
    case_id: str = "C-1",
    *,
    defects: Sequence[Defect] = (),
    non_defects: Sequence[NonDefect] = (),
    status: str = "adjudicated",
    blocking_complete: bool = True,
    cls: str = "defective",
    expected_outcome: str = "verdict",
) -> Case:
    """Кейс корпуса; по умолчанию gold с исчерпывающей разметкой."""
    return Case(
        case_id=case_id,
        repo="steward",
        pr=1,
        base_sha="0" * 40,
        head_sha="1" * 40,
        cls=cls,
        local_args=(),
        expected_outcome=expected_outcome,
        annotation=Annotation(
            status=status,
            blocking_complete=blocking_complete,
            adjudicated_by="github:owner",
            adjudicated_at="2026-09-14",
            source="manual",
        ),
        defects=tuple(defects),
        non_defects=tuple(non_defects),
        notes="",
    )


def finding(
    *,
    file: str = "app/a.py",
    line: int = 10,
    severity: str = "major",
    confidence: str = "high",
    title: str = "boom в загрузчике",
    evidence: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, Any]:
    """Находка вердикта; по умолчанию блокирующая (`is_blocking` истинен)."""
    return {
        "kind": "defect",
        "severity": severity,
        "title": title,
        "file": file,
        "line": line,
        "scenario": "запуск загрузчика",
        "observed_result": "падает",
        "expected_result": "не падает",
        "evidence": list(evidence)
        if evidence is not None
        else [{"file": file, "line": line, "reason": "строка"}],
        "confidence": confidence,
    }


def file_missing_finding(
    *,
    file: str = "app/gone.py",
    severity: str = "major",
    confidence: str = "high",
    title: str = "boom: обязательного файла нет",
) -> dict[str, Any]:
    """Находка `kind: file-missing`: утверждение «файла нет», `line` строго 0."""
    return {
        "kind": "file-missing",
        "severity": severity,
        "title": title,
        "file": file,
        "line": 0,
        "scenario": "запуск загрузчика",
        "observed_result": "файла нет",
        "expected_result": "файл есть",
        "evidence": [{"file": file, "line": 0, "reason": "файла нет в дереве"}],
        "confidence": confidence,
    }


def build_eval(
    case: Case,
    *,
    findings: Sequence[Mapping[str, object]] = (),
    outcome: str = "verdict",
    exit_code: int = 1,
    reviewer_ran: bool = True,
    rep: int = 1,
    cost: float | None = None,
    wall: float = 1.0,
    unexpected: bool = False,
    resolvable: Sequence[bool] = (),
    provider_ms: float | None = None,
    evidence_unchecked: bool = False,
    refuted: Sequence[int] = (),
    contradicted_gold: Sequence[str] = (),
) -> CaseEval:
    """`CaseEval` без чтения файлов; матчер — настоящий, как в `evaluate_case`."""
    result = RunResult(
        case_id=case.case_id,
        variant="claude:claude-opus-5",
        repetition_id=rep,
        exit_code=exit_code,
        outcome=outcome,
        reviewer_ran=reviewer_ran,
        wall_clock_s=wall,
        verdict_path="verdict.json" if findings else None,
        usage_path="usage.json" if cost is not None or provider_ms is not None else None,
        cost_status="available" if cost is not None else "unavailable",
        requested_effort=None,
        stdout_path="stdout.txt",
        stderr_path="stderr.txt",
        unexpected=unexpected,
    )
    matched = None
    if outcome == "verdict":
        preds = tuple(Prediction(index=i, finding=f) for i, f in enumerate(findings))
        matched = match(preds, case.defects, case.non_defects)
    usage: dict[str, object] | None = None
    if cost is not None or provider_ms is not None:
        usage = {"total_cost_usd": cost, "provider_duration_ms": provider_ms}
    return CaseEval(
        case=case,
        result=result,
        findings=tuple(findings),
        match=matched,
        usage=usage,
        resolvable_evidence=tuple(resolvable),
        evidence_unchecked=evidence_unchecked,
        refuted=tuple(refuted),
        contradicted_gold=tuple(contradicted_gold),
    )


def value_of(summary: Mapping[str, object], name: str) -> object:
    """`metrics[name]["value"]` из сводки варианта."""
    return _entry(summary, name)["value"]


def fraction_of(summary: Mapping[str, object], name: str) -> tuple[int, int]:
    """`(numerator, denominator)` метрики — знаменатель проверяется отдельно."""
    entry = _entry(summary, name)
    return (int(entry["numerator"]), int(entry["denominator"]))


def _entry(summary: Mapping[str, object], name: str) -> Mapping[str, Any]:
    metrics = summary["metrics"]
    assert isinstance(metrics, Mapping)
    entry = metrics[name]
    assert isinstance(entry, Mapping)
    return entry


# ---------------------------------------------------------------------------
# Metric
# ---------------------------------------------------------------------------


def test_metric_as_dict_keeps_all_keys_including_empty_note() -> None:
    """Форма записи одна для всех метрик: потребитель не разбирает два варианта."""
    assert Metric(value=0.5, numerator=1, denominator=2).as_dict() == {
        "value": 0.5,
        "numerator": 1,
        "denominator": 2,
        "note": None,
    }


# ---------------------------------------------------------------------------
# evaluate_case
# ---------------------------------------------------------------------------


def write_run(
    out_dir: Path,
    *,
    verdict: Mapping[str, object] | None = None,
    usage: Mapping[str, object] | None = None,
) -> None:
    """Пишет sidecar-артефакты прогона в корень `out_dir`."""
    if verdict is not None:
        (out_dir / "verdict.json").write_text(json.dumps(verdict), encoding="utf-8")
    if usage is not None:
        (out_dir / "usage.json").write_text(json.dumps(usage), encoding="utf-8")


def result_for(
    case: Case,
    *,
    outcome: str = "verdict",
    verdict_path: str | None = "verdict.json",
    usage_path: str | None = None,
    cost_status: str = "unavailable",
    exit_code: int = 1,
) -> RunResult:
    """`RunResult`, указывающий на sidecar-файлы в корне `out_dir`."""
    return RunResult(
        case_id=case.case_id,
        variant="claude:claude-opus-5",
        repetition_id=1,
        exit_code=exit_code,
        outcome=outcome,
        reviewer_ran=verdict_path is not None,
        wall_clock_s=2.0,
        verdict_path=verdict_path,
        usage_path=usage_path,
        cost_status=cost_status,
        requested_effort="high",
        stdout_path="stdout.txt",
        stderr_path="stderr.txt",
        unexpected=False,
    )


def test_evaluate_case_loads_sidecars_and_runs_matcher(tmp_path: Path) -> None:
    """Вердикт и usage разобраны, находка назначена дефекту."""
    case = make_case(defects=[make_defect()])
    write_run(
        tmp_path,
        verdict={"findings": [finding()], "note": "ok"},
        usage={"total_cost_usd": 0.25, "provider_duration_ms": 1200},
    )
    result = result_for(case, usage_path="usage.json", cost_status="available")

    ev = evaluate_case(case, result, tmp_path, file_lines=lambda path: 40)

    assert len(ev.findings) == 1
    assert ev.match is not None
    assert ev.match.assigned == {0: "D-steward-1-1"}
    assert ev.usage is not None
    assert ev.usage["total_cost_usd"] == 0.25
    assert ev.resolvable_evidence == (True,)


def test_evaluate_case_refutes_file_missing_when_the_file_exists(tmp_path: Path) -> None:
    """`file-missing` на существующем файле — опровергнута деревом head.

    Матчер такой находке рёбер к строчному gold не даёт вовсе (это разные
    утверждения), поэтому она уходила в `unlabeled`, то есть в очередь
    adjudication. Но разбирать там нечего: дерево `head_sha` — факт, и оно
    говорит, что файл есть. Опровергнутая находка — FP, а не работа для
    человека.
    """
    case = make_case(defects=[make_defect()])
    write_run(tmp_path, verdict={"findings": [file_missing_finding()], "note": "ok"})

    ev = evaluate_case(case, result_for(case), tmp_path, file_lines=lambda path: 12)

    assert ev.match is not None
    assert ev.match.unlabeled == (0,)
    assert ev.refuted == (0,)


def test_evaluate_case_keeps_file_missing_unlabeled_when_the_file_is_absent(
    tmp_path: Path,
) -> None:
    """Файла на head нет — находка правдоподобна, и она остаётся в очереди."""
    case = make_case(defects=[make_defect()])
    write_run(tmp_path, verdict={"findings": [file_missing_finding()], "note": "ok"})

    ev = evaluate_case(case, result_for(case), tmp_path, file_lines=lambda path: None)

    assert ev.match is not None
    assert ev.match.unlabeled == (0,)
    assert ev.refuted == ()


def test_evaluate_case_does_not_refute_a_matched_file_missing(tmp_path: Path) -> None:
    """Назначенная gold-дефекту `file-missing` не опровергается: она уже TP.

    Опровержение — про находки **без** разметки. У назначенной разметка есть, и
    дерево head тут не арбитр: gold говорит, что дефект настоящий.
    """
    gold = Defect(
        id="D-steward-1-2",
        severity="major",
        file="app/gone.py",
        line_hint=0,
        scenario="сценарий",
        evidence=(),
        match=make_match("app/gone.py", ("boom",)),
        kind="file-missing",
    )
    case = make_case(defects=[gold])
    write_run(tmp_path, verdict={"findings": [file_missing_finding()], "note": "ok"})

    ev = evaluate_case(case, result_for(case), tmp_path, file_lines=lambda path: 12)

    assert ev.match is not None
    # Матчер назначение сделал — он дерева не читает.
    assert ev.match.assigned == {0: "D-steward-1-2"}
    # А дерево head его отменяет: файл есть, значит «файла нет» ложно с обеих
    # сторон, и gold, утверждающий обратное, противоречит дереву.
    assert ev.refuted == (0,)
    assert ev.contradicted_gold == ("D-steward-1-2",)


def test_evaluate_case_does_not_contradict_gold_when_the_file_is_absent(tmp_path: Path) -> None:
    """Файла на head нет — и назначение, и gold в порядке: это честный TP."""
    gold = Defect(
        id="D-steward-1-2",
        severity="major",
        file="app/gone.py",
        line_hint=0,
        scenario="сценарий",
        evidence=(),
        match=make_match("app/gone.py", ("boom",)),
        kind="file-missing",
    )
    case = make_case(defects=[gold])
    write_run(tmp_path, verdict={"findings": [file_missing_finding()], "note": "ok"})

    ev = evaluate_case(case, result_for(case), tmp_path, file_lines=lambda path: None)

    assert ev.refuted == ()
    assert ev.contradicted_gold == ()


def test_evaluate_case_refutes_nothing_when_the_cache_is_unavailable(tmp_path: Path) -> None:
    """Источника фактов о файлах нет — опровергать нечем, находка остаётся в очереди."""
    case = make_case(defects=[make_defect()])
    write_run(tmp_path, verdict={"findings": [file_missing_finding()], "note": "ok"})

    def unavailable(path: str) -> int | None:
        raise CacheUnavailable("кэша нет")

    ev = evaluate_case(case, result_for(case), tmp_path, file_lines=unavailable)

    assert ev.evidence_unchecked is True
    assert ev.refuted == ()


def test_evaluate_case_skips_matcher_for_invalid_verdict(tmp_path: Path) -> None:
    """Исход не `verdict` — находки читаются, матчер не зовётся (сопоставлять нечего)."""
    case = make_case(defects=[make_defect()])
    write_run(tmp_path, verdict={"findings": [finding()], "note": "ok"})
    result = result_for(case, outcome="invalid_verdict", exit_code=2)

    ev = evaluate_case(case, result, tmp_path, file_lines=lambda path: 40)

    assert len(ev.findings) == 1
    assert ev.match is None


def test_evaluate_case_resolvable_evidence_semantics(tmp_path: Path) -> None:
    """Разрешимость: строка в файле — да, строка 0 — да, за концом файла и нет
    файла — нет; evidence неблокирующих находок не учитывается вовсе."""
    case = make_case(defects=[make_defect()])
    blocking = finding(
        evidence=[
            {"file": "app/a.py", "line": 7, "reason": "в файле"},
            {"file": "app/a.py", "line": 8.0, "reason": "целое JSON-число в дробной записи"},
            {"file": "./app/a.py", "line": 9, "reason": "путь нормализуется, как в матчере"},
            {"file": "app/a.py", "line": 0, "reason": "указатель уровня файла"},
            {"file": "app/a.py", "line": 99, "reason": "за концом файла"},
            {"file": "app/gone.py", "line": 1, "reason": "файла нет на head"},
        ]
    )
    quiet = finding(
        confidence="medium",
        evidence=[{"file": "app/a.py", "line": 3, "reason": "не блокирует"}],
    )
    write_run(tmp_path, verdict={"findings": [blocking, quiet], "note": "ok"})
    sizes = {"app/a.py": 10}

    ev = evaluate_case(
        case,
        result_for(case),
        tmp_path,
        file_lines=lambda path: sizes.get(path),
    )

    assert ev.resolvable_evidence == (True, True, True, True, False, False)


def test_evaluate_case_raises_when_promised_verdict_is_unreadable(tmp_path: Path) -> None:
    """Исход `verdict` обещает вердикт: битый sidecar — ошибка, не пустые метрики."""
    case = make_case()
    (tmp_path / "verdict.json").write_text("{не json", encoding="utf-8")

    with pytest.raises(MetricsError, match="verdict.json"):
        evaluate_case(case, result_for(case), tmp_path, file_lines=lambda path: 1)


def test_evaluate_case_raises_when_cost_promised_but_absent(tmp_path: Path) -> None:
    """`cost_status: available` без числового `total_cost_usd` — ошибка (D6)."""
    case = make_case()
    write_run(
        tmp_path,
        verdict={"findings": [], "note": "ok"},
        usage={"total_cost_usd": None},
    )
    result = result_for(case, usage_path="usage.json", cost_status="available")

    with pytest.raises(MetricsError, match="total_cost_usd"):
        evaluate_case(case, result, tmp_path, file_lines=lambda path: 1)


# ---------------------------------------------------------------------------
# precision_lower_bound / precision
# ---------------------------------------------------------------------------


def test_precision_lower_bound_known_fp_is_fp_1_of_2() -> None:
    """TP=1, known-FP=1 → precision_lower_bound = 1/2; знаменатель — только
    блокирующие предсказания, поэтому `minor` и `confidence: medium` в него не
    входят, хотя находок в вердикте четыре."""
    case = make_case(defects=[make_defect()], non_defects=[make_non_defect()])
    evals = [
        build_eval(
            case,
            findings=[
                finding(),
                finding(file="app/known.py", line=5, title="known ложная тревога"),
                finding(file="app/style.py", line=1, title="мелочь", severity="minor"),
                finding(file="app/maybe.py", line=1, title="возможно", confidence="medium"),
            ],
        )
    ]

    summary = metrics_for_variant(evals)

    assert fraction_of(summary, "precision_lower_bound") == (1, 2)
    assert value_of(summary, "precision_lower_bound") == 0.5
    assert summary["n_predictions"] == 4
    assert summary["status"] == "ok"


def test_duplicate_is_fp_and_counted_in_duplicate_rate_1_of_2() -> None:
    """Две находки на один дефект → 1 TP + 1 duplicate: plb = 1/2, duplicate_rate = 1/2."""
    case = make_case(defects=[make_defect()])
    evals = [build_eval(case, findings=[finding(line=10), finding(line=12)])]

    summary = metrics_for_variant(evals)

    assert fraction_of(summary, "precision_lower_bound") == (1, 2)
    assert fraction_of(summary, "duplicate_rate") == (1, 2)


def test_unlabeled_blocking_suppresses_precision_and_sets_pending() -> None:
    """Неразмеченное блокирующее предсказание: `precision` отсутствует как ключ (D9)."""
    case = make_case(defects=[make_defect()])
    evals = [
        build_eval(
            case,
            findings=[finding(), finding(file="app/other.py", line=3, title="boom иное")],
        )
    ]

    summary = metrics_for_variant(evals)
    metrics = summary["metrics"]
    assert isinstance(metrics, Mapping)

    assert "precision" not in metrics
    assert summary["status"] == "pending_adjudication"
    assert fraction_of(summary, "precision_lower_bound") == (1, 2)


def test_refuted_file_missing_is_fp_and_does_not_block_precision() -> None:
    """Опровергнутая `file-missing` — FP в plb и **не** держит `precision`.

    TP=1, опровергнутая находка=1 → plb = 1/2. Очередь пуста: разбирать
    опровергнутое нечего, поэтому `precision` публикуется, а статус остаётся
    `ok`. Без этого одна находка «файла нет» на существующем файле навсегда
    удерживала бы официальный precision варианта.
    """
    case = make_case(defects=[make_defect()])
    evals = [
        build_eval(case, findings=[finding(), file_missing_finding()], refuted=[1]),
    ]

    summary = metrics_for_variant(evals)
    metrics = summary["metrics"]
    assert isinstance(metrics, Mapping)

    assert fraction_of(summary, "precision_lower_bound") == (1, 2)
    assert "precision" in metrics
    assert summary["status"] == "ok"
    assert fraction_of(summary, "refuted_file_missing_count") == (1, 2)
    assert value_of(summary, "refuted_file_missing_count") == 1.0


def test_unrefuted_file_missing_still_blocks_precision() -> None:
    """Та же находка без опровержения остаётся в очереди и держит `precision`."""
    case = make_case(defects=[make_defect()])
    evals = [build_eval(case, findings=[finding(), file_missing_finding()])]

    summary = metrics_for_variant(evals)
    metrics = summary["metrics"]
    assert isinstance(metrics, Mapping)

    assert "precision" not in metrics
    assert summary["status"] == "pending_adjudication"
    assert fraction_of(summary, "refuted_file_missing_count") == (0, 2)


def _contradicted_gold_eval() -> CaseEval:
    """Прогон, где `file-missing` назначена gold, но файл на head существует."""
    gold = Defect(
        id="D-steward-1-2",
        severity="major",
        file="app/gone.py",
        line_hint=0,
        scenario="сценарий",
        evidence=(),
        match=make_match("app/gone.py", ("boom",)),
        kind="file-missing",
    )
    case = make_case(case_id="C-contra", defects=[gold])
    return build_eval(
        case,
        findings=[file_missing_finding()],
        refuted=[0],
        contradicted_gold=["D-steward-1-2"],
    )


def test_contradicted_gold_is_not_a_true_positive_and_not_in_recall() -> None:
    """Gold, которому возразило дерево, не даёт ни TP, ни знаменателя recall.

    Назначение снимается: находка «файла нет» ложна (файл есть), значит TP из
    неё быть не может. И gold-дефект, утверждающий то же самое, из знаменателя
    recall уходит — иначе вариант наказывался бы за то, что не нашёл
    несуществующий дефект.
    """
    summary = metrics_for_variant([_contradicted_gold_eval()])
    metrics = summary["metrics"]
    assert isinstance(metrics, Mapping)

    assert fraction_of(summary, "precision_lower_bound") == (0, 1)
    assert fraction_of(summary, "blocking_recall") == (0, 0)
    assert fraction_of(summary, "detection_recall_any_severity") == (0, 0)
    assert fraction_of(summary, "refuted_file_missing_count") == (1, 1)
    assert fraction_of(summary, "contradicted_gold_count") == (1, 1)


def test_contradicted_gold_keeps_the_variant_out_of_ok() -> None:
    """Пока есть противоречащий gold, вариант не `ok`: корпус требует правки."""
    summary = metrics_for_variant([_contradicted_gold_eval()])
    metrics = summary["metrics"]
    assert isinstance(metrics, Mapping)

    assert summary["status"] == "pending_adjudication"
    assert "precision" not in metrics


def test_contradicted_gold_count_counts_every_repetition() -> None:
    """Один противоречащий gold в двух повторениях — 2/2, не 1/2: числитель и
    знаменатель считаются по прогонам одинаково.
    """
    case = make_case("C-1", defects=[make_defect("D-1")])
    evals = [
        build_eval(case, contradicted_gold=("D-1",), rep=1),
        build_eval(case, contradicted_gold=("D-1",), rep=2),
    ]

    summary = metrics_for_variant(evals)
    assert fraction_of(summary, "contradicted_gold_count") == (2, 2)


def test_contradicted_gold_count_is_zero_by_default() -> None:
    """Ключ есть всегда: «противоречий нет» — это число, а не отсутствие ключа."""
    case = make_case(defects=[make_defect()])

    summary = metrics_for_variant([build_eval(case, findings=[finding()])])

    assert fraction_of(summary, "contradicted_gold_count") == (0, 1)
    assert summary["status"] == "ok"


def test_gold_file_missing_is_checked_against_the_tree_without_any_prediction(
    tmp_path: Path,
) -> None:
    """Gold «файла нет» на существующем файле противоречит дереву сам по себе.

    Прежде противоречие замечалось только через **назначенную** находку, то
    есть требовало, чтобы модель повторила то же ложное утверждение. Молчаливая
    модель оставляла негодный gold в знаменателе recall — и вариант наказывался
    за то, что не нашёл дефект, которого в материале нет.
    """
    gold = Defect(
        id="D-steward-1-2",
        severity="major",
        file="app/gone.py",
        line_hint=0,
        scenario="сценарий",
        evidence=(),
        match=make_match("app/gone.py", ("boom",)),
        kind="file-missing",
    )
    case = make_case(defects=[gold])
    write_run(tmp_path, verdict={"findings": [], "note": "ok"})

    ev = evaluate_case(case, result_for(case), tmp_path, file_lines=lambda path: 12)

    assert ev.refuted == ()
    assert ev.contradicted_gold == ("D-steward-1-2",)

    summary = metrics_for_variant([ev])
    metrics = summary["metrics"]
    assert isinstance(metrics, Mapping)
    assert fraction_of(summary, "blocking_recall") == (0, 0)
    assert summary["status"] == "pending_adjudication"
    assert "precision" not in metrics


def test_gold_file_missing_on_an_absent_file_is_a_normal_miss(tmp_path: Path) -> None:
    """Файла на head нет — gold верен, и пропуск считается пропуском."""
    gold = Defect(
        id="D-steward-1-2",
        severity="major",
        file="app/gone.py",
        line_hint=0,
        scenario="сценарий",
        evidence=(),
        match=make_match("app/gone.py", ("boom",)),
        kind="file-missing",
    )
    case = make_case(defects=[gold])
    write_run(tmp_path, verdict={"findings": [], "note": "ok"})

    ev = evaluate_case(case, result_for(case), tmp_path, file_lines=lambda path: None)

    assert ev.contradicted_gold == ()
    summary = metrics_for_variant([ev])
    assert fraction_of(summary, "blocking_recall") == (0, 1)


def test_line_level_gold_is_never_contradicted_by_an_existing_file(tmp_path: Path) -> None:
    """Проверке подлежит только gold `kind: file-missing`.

    Обычный дефект утверждает «в строке файла ошибка», и существование файла
    это подтверждает, а не опровергает.
    """
    case = make_case(defects=[make_defect()])
    write_run(tmp_path, verdict={"findings": [], "note": "ok"})

    ev = evaluate_case(case, result_for(case), tmp_path, file_lines=lambda path: 40)

    assert ev.contradicted_gold == ()


def test_contradicted_gold_in_an_unexpected_run_still_holds_the_status() -> None:
    """Противоречие корпуса материалу не зависит от исхода прогона.

    Прежде очередь и статус считались только по прогонам, годным для метрик
    качества, поэтому противоречие, найденное в прогоне с неожидаемым исходом,
    не держало вариант вне `ok`: отчёт говорил «очередь пуста», а разметка
    кейса при этом спорила с деревом. Факт про корпус остаётся фактом, каким
    бы ни был исход прогона, в котором его заметили.
    """
    clean = make_case(case_id="C-clean", defects=[make_defect()])
    contra = _contradicted_gold_eval()
    unexpected = dataclasses.replace(
        contra, result=dataclasses.replace(contra.result, unexpected=True)
    )
    evals = [build_eval(clean, findings=[finding()]), unexpected]

    summary = metrics_for_variant(evals)
    metrics = summary["metrics"]
    assert isinstance(metrics, Mapping)
    counts = queue_counts(evals)

    assert summary["status"] == "pending_adjudication"
    assert "precision" not in metrics
    assert counts.contradicted_gold == 1
    assert counts.total > 0
    assert fraction_of(summary, "contradicted_gold_count") == (1, 2)


def test_empty_range_run_is_counted_but_not_a_quality_run() -> None:
    """`empty_range` — проблема корпуса: счётчик растёт, качество не считается."""
    case = make_case(case_id="C-empty", defects=[make_defect()])
    ev = build_eval(case, findings=(), outcome="empty_range", exit_code=0, reviewer_ran=False)

    summary = metrics_for_variant([ev])

    assert fraction_of(summary, "empty_range_count") == (1, 1)
    assert summary["status"] == "no_quality_runs"


def test_empty_range_count_is_zero_by_default() -> None:
    """Ключ есть всегда: «негодных кейсов нет» — это число."""
    case = make_case(defects=[make_defect()])

    summary = metrics_for_variant([build_eval(case, findings=[finding()])])

    assert fraction_of(summary, "empty_range_count") == (0, 1)


def test_refuted_count_is_zero_when_there_is_nothing_to_refute() -> None:
    """Ключ есть всегда: «нечего опровергать» и «ключа нет» — разные факты."""
    case = make_case(defects=[make_defect()])

    summary = metrics_for_variant([build_eval(case, findings=[finding()])])

    assert fraction_of(summary, "refuted_file_missing_count") == (0, 1)
    assert value_of(summary, "refuted_file_missing_count") == 0.0


def test_precision_excludes_unlabeled_when_queue_empty_1_of_2() -> None:
    """Очередь пуста → `precision` публикуется: TP=1, known-FP=1 → 1/2."""
    case = make_case(defects=[make_defect()], non_defects=[make_non_defect()])
    evals = [
        build_eval(
            case,
            findings=[
                finding(),
                finding(file="app/known.py", line=5, title="known ложная тревога"),
            ],
        )
    ]

    summary = metrics_for_variant(evals)

    assert fraction_of(summary, "precision") == (1, 2)


def test_blocking_prediction_on_gold_minor_is_fp_not_recall() -> None:
    """Завышение класса: назначение на gold-`minor` — FP в precision (1/2), в
    знаменатель recall дефект не входит, blocking_recall = 1/1."""
    case = make_case(
        defects=[
            make_defect("D-steward-1-1", severity="major", file="app/a.py", line_hint=10),
            make_defect(
                "D-steward-1-2",
                severity="minor",
                file="app/b.py",
                line_hint=20,
                keywords=("nit",),
            ),
        ]
    )
    evals = [
        build_eval(
            case,
            findings=[finding(), finding(file="app/b.py", line=20, title="nit в стиле")],
        )
    ]

    summary = metrics_for_variant(evals)

    assert fraction_of(summary, "precision_lower_bound") == (1, 2)
    assert fraction_of(summary, "blocking_recall") == (1, 1)
    assert summary["status"] == "ok"


# ---------------------------------------------------------------------------
# recall / false_block_rate
# ---------------------------------------------------------------------------


def test_blocking_recall_half_and_detection_recall_full() -> None:
    """Второй дефект замечен как `minor` (не блокирует): blocking_recall = 1/2,
    detection_recall_any_severity = 2/2."""
    case = make_case(
        defects=[
            make_defect("D-steward-1-1", file="app/a.py", line_hint=10),
            make_defect("D-steward-1-2", file="app/b.py", line_hint=20, keywords=("leak",)),
        ]
    )
    evals = [
        build_eval(
            case,
            findings=[
                finding(),
                finding(file="app/b.py", line=20, title="leak в кэше", severity="minor"),
            ],
        )
    ]

    summary = metrics_for_variant(evals)

    assert fraction_of(summary, "blocking_recall") == (1, 2)
    assert fraction_of(summary, "detection_recall_any_severity") == (2, 2)


def test_blocking_complete_false_excluded_from_recall_kept_in_precision() -> None:
    """`blocking_complete: false` — вне recall/false-block, но внутри precision (D8)."""
    case = make_case(defects=[make_defect()], blocking_complete=False)
    evals = [build_eval(case, findings=[finding()])]

    summary = metrics_for_variant(evals)

    assert fraction_of(summary, "blocking_recall") == (0, 0)
    assert value_of(summary, "blocking_recall") is None
    assert fraction_of(summary, "false_block_rate") == (0, 0)
    assert fraction_of(summary, "precision_lower_bound") == (1, 1)


def test_false_block_rate_clean_case_blocked_1_of_2() -> None:
    """Чистый кейс покрашен кодом 1, дефектный — законно: false_block_rate = 1/2."""
    clean = make_case("C-clean", cls="clean")
    defective = make_case("C-def", defects=[make_defect()])
    evals = [
        build_eval(clean, findings=[finding(file="app/z.py", line=1, title="boom")], exit_code=1),
        build_eval(defective, findings=[finding()], exit_code=1),
    ]

    summary = metrics_for_variant(evals)

    assert fraction_of(summary, "false_block_rate") == (1, 2)
    assert value_of(summary, "false_block_rate") == 0.5


CACHE_UNAVAILABLE_NOTE = (
    "кэш недоступен — evidence не проверялся; сверка gold 'файла нет' с деревом "
    "head_sha тоже пропущена (1 кейсов)"
)


def test_evaluate_case_marks_evidence_unchecked_when_the_cache_is_unavailable(
    tmp_path: Path,
) -> None:
    """Недоступный кэш — не факт о ссылке: флаги пусты, прогон помечен непроверенным."""
    case = make_case(defects=[make_defect()])
    write_run(tmp_path, verdict={"findings": [finding()], "note": "ok"})

    def no_cache(_path: str) -> int | None:
        raise CacheUnavailable("bare-кэша нет")

    ev = evaluate_case(case, result_for(case), tmp_path, file_lines=no_cache)

    assert ev.resolvable_evidence == ()
    assert ev.evidence_unchecked is True
    # Остальной разбор не пострадал: находка назначена дефекту.
    assert ev.match is not None
    assert ev.match.assigned == {0: "D-steward-1-1"}


def test_resolvable_rate_is_none_when_the_cache_was_unavailable() -> None:
    """Непроверенный кейс — значение `None` с пометкой, а не посчитанный ноль.

    Нулевая доля разрешимых ссылок и «ссылки не проверялись» — разные факты, и
    именно второй получался бы напечатанным как первый, если бы отсутствие
    кэша считалось «файла нет».
    """
    case = make_case("C-1", defects=[make_defect()])
    ev = build_eval(case, findings=[finding()], evidence_unchecked=True)

    summary = metrics_for_variant([ev])
    entry = _entry(summary, "resolvable_evidence_rate")

    assert entry["value"] is None
    assert entry["note"] == CACHE_UNAVAILABLE_NOTE
    assert (entry["numerator"], entry["denominator"]) == (0, 0)
    # Остальные метрики качества считаются как обычно.
    assert value_of(summary, "precision_lower_bound") == pytest.approx(1.0)
    assert summary["status"] == "ok"


def test_resolvable_rate_none_even_when_some_cases_were_checked() -> None:
    """Смешанная популяция: знаменатель показывает проверенный остаток, значение — `None`.

    Доля по половине кейсов — число из другой популяции, и печатать её как
    долю варианта нельзя; знаменатель при этом остаётся видимым.
    """
    checked = make_case("C-checked", defects=[make_defect()])
    unchecked = make_case("C-unchecked", defects=[make_defect("D-steward-2-1")])
    evals = [
        build_eval(checked, findings=[finding()], resolvable=(True, False)),
        build_eval(unchecked, findings=[finding()], evidence_unchecked=True),
    ]

    entry = _entry(metrics_for_variant(evals), "resolvable_evidence_rate")

    assert entry["value"] is None
    assert (entry["numerator"], entry["denominator"]) == (1, 2)
    assert entry["note"] == CACHE_UNAVAILABLE_NOTE


def test_resolvable_rate_ci_and_per_repetition_follow_the_note() -> None:
    """Интервал и разрез по повторениям не публикуют значение, которого нет."""
    case = make_case("C-1", defects=[make_defect()])
    other = make_case("C-2", defects=[make_defect("D-steward-2-1")])
    evals = [
        build_eval(case, findings=[finding()], evidence_unchecked=True),
        build_eval(other, findings=[finding()], resolvable=(True,)),
    ]

    summary = metrics_for_variant(evals)
    ci = summary["ci"]
    per_rep = summary["per_repetition"]
    assert isinstance(ci, Mapping)
    assert isinstance(per_rep, Mapping)

    assert ci["resolvable_evidence_rate"] is None
    assert per_rep["1"]["resolvable_evidence_rate"]["value"] is None
    assert per_rep["1"]["resolvable_evidence_rate"]["note"] == CACHE_UNAVAILABLE_NOTE
    # Метрики, не зависящие от кэша, интервал сохраняют.
    assert ci["precision_lower_bound"] is not None


def test_resolvable_evidence_rate_2_of_3() -> None:
    """Разрешимые evidence-записи блокирующих находок: 2/3."""
    case = make_case(defects=[make_defect()])
    evals = [build_eval(case, findings=[finding()], resolvable=[True, False, True])]

    summary = metrics_for_variant(evals)

    assert fraction_of(summary, "resolvable_evidence_rate") == (2, 3)


# ---------------------------------------------------------------------------
# Эксплуатационные метрики
# ---------------------------------------------------------------------------


def test_invalid_verdict_counts_in_valid_verdict_denominator_not_in_quality() -> None:
    """Негодный вердикт — ошибка модели, а не конфигурации: знаменатель
    valid_verdict_rate — прогоны с `reviewer_ran` (1/2), а не все три прогона;
    в качестве такой прогон не участвует (precision_lower_bound = 1/1)."""
    good = make_case("C-1", defects=[make_defect()])
    bad = make_case("C-2", defects=[make_defect("D-steward-2-1")])
    broken = make_case("C-3")
    evals = [
        build_eval(good, findings=[finding()]),
        build_eval(bad, findings=[finding()], outcome="invalid_verdict", exit_code=2),
        build_eval(broken, outcome="config_failure", exit_code=2, reviewer_ran=False),
    ]

    summary = metrics_for_variant(evals)

    assert fraction_of(summary, "valid_verdict_rate") == (1, 2)
    assert fraction_of(summary, "config_failure_rate") == (1, 3)
    assert fraction_of(summary, "precision_lower_bound") == (1, 1)
    assert summary["n_runs"] == 3


def test_completion_rate_counts_expected_guardrail_rejection_2_of_3() -> None:
    """Ожидаемый отказ гардрейла — завершённый прогон; конфиг-сбой — нет: 2/3."""
    large = make_case("C-large", cls="large", expected_outcome="guardrail_rejection")
    ok = make_case("C-ok", defects=[make_defect()])
    broken = make_case("C-broken")
    evals = [
        build_eval(large, outcome="guardrail_rejection", exit_code=2, reviewer_ran=False),
        build_eval(ok, findings=[finding()]),
        build_eval(broken, outcome="config_failure", exit_code=2, reviewer_ran=False),
    ]

    summary = metrics_for_variant(evals)

    assert fraction_of(summary, "completion_rate") == (2, 3)
    assert fraction_of(summary, "config_failure_rate") == (1, 3)
    assert fraction_of(summary, "mechanical_failure_rate") == (0, 3)


def test_unexpected_outcome_excluded_from_quality_but_counted() -> None:
    """Несовпавший исход: качество по нему не считается, счётчик и эксплуатация — да."""
    large = make_case("C-large", cls="large", expected_outcome="guardrail_rejection")
    evals = [build_eval(large, findings=[finding()], unexpected=True)]

    summary = metrics_for_variant(evals)

    assert fraction_of(summary, "precision_lower_bound") == (0, 0)
    assert value_of(summary, "unexpected_outcome_count") == 1.0
    assert fraction_of(summary, "valid_verdict_rate") == (1, 1)


def test_mechanical_failure_rate_1_of_2() -> None:
    """Код 3 — механический сбой раннера/кита: mechanical_failure_rate = 1/2."""
    case_a = make_case("C-1")
    case_b = make_case("C-2", defects=[make_defect()])
    evals = [
        build_eval(case_a, outcome="mechanical_failure", exit_code=3, reviewer_ran=False),
        build_eval(case_b, findings=[finding()]),
    ]

    summary = metrics_for_variant(evals)

    assert fraction_of(summary, "mechanical_failure_rate") == (1, 2)


def test_draft_cases_are_not_measured_at_all() -> None:
    """Черновая разметка вне метрик: `status: no_gold`, все знаменатели нулевые."""
    draft = make_case("C-draft", defects=[make_defect()], status="draft")
    evals = [build_eval(draft, findings=[finding()])]

    summary = metrics_for_variant(evals)

    assert summary["status"] == "no_gold"
    assert summary["n_cases"] == 1
    assert summary["n_gold_cases"] == 0
    assert fraction_of(summary, "precision_lower_bound") == (0, 0)
    assert fraction_of(summary, "completion_rate") == (0, 0)


def test_zero_denominator_reports_none_value_with_denominators() -> None:
    """Пустой знаменатель — `value: None`, а не 0.0: мерить не на чем."""
    summary = metrics_for_variant([])

    assert summary["status"] == "no_gold"
    assert value_of(summary, "completion_rate") is None
    assert fraction_of(summary, "duplicate_rate") == (0, 0)


# ---------------------------------------------------------------------------
# Стоимость и длительность
# ---------------------------------------------------------------------------


def test_cost_sums_available_only_and_flags_partial() -> None:
    """Прогон без стоимости не подставляется нулём: сумма 0.75, средняя по 2, partial."""
    case_a = make_case("C-1", defects=[make_defect()])
    case_b = make_case("C-2", defects=[make_defect("D-steward-2-1")])
    case_c = make_case("C-3", defects=[make_defect("D-steward-3-1")])
    evals = [
        build_eval(case_a, findings=[finding()], cost=0.5),
        build_eval(case_b, findings=[finding()], cost=0.25),
        build_eval(case_c, findings=[finding()]),
    ]

    cost = metrics_for_variant(evals)["cost"]
    assert isinstance(cost, Mapping)

    assert cost["cost_usd_total"] == pytest.approx(0.75)
    assert cost["cost_usd_mean_per_case"] == pytest.approx(0.375)
    assert cost["cost_available_cases"] == 2
    assert cost["cost_unavailable_cases"] == 1
    assert cost["n_runs_with_cost"] == 2
    assert cost["n_runs_total"] == 3
    assert cost["partial"] is True


def test_cost_declares_run_as_the_unit() -> None:
    """Единица счёта — прогон, а не кейс: при повторениях один кейс стоит N прогонов."""
    case = make_case("C-1", defects=[make_defect()])
    evals = [
        build_eval(case, findings=[finding()], rep=1, cost=0.2),
        build_eval(case, findings=[finding()], rep=2, cost=0.4),
    ]

    cost = metrics_for_variant(evals)["cost"]
    assert isinstance(cost, Mapping)
    notes = cost["notes"]
    assert isinstance(notes, Mapping)

    assert cost["n_runs_total"] == 2
    assert cost["cost_usd_mean_per_case"] == pytest.approx(0.3)
    assert notes["cost_usd_mean_per_case"] == 'unit: "run"'
    assert notes["cost_unavailable_cases"] == 'unit: "run"'


def test_cost_counts_failed_runs_and_stays_none_without_data() -> None:
    """Неуспешный прогон тоже стоит денег (D12); без данных — `None`, не 0.0."""
    paid = make_case("C-1")
    free = make_case("C-2")
    with_cost = metrics_for_variant(
        [build_eval(paid, outcome="config_failure", exit_code=2, reviewer_ran=False, cost=0.4)]
    )["cost"]
    without = metrics_for_variant([build_eval(free, findings=[finding()])])["cost"]
    assert isinstance(with_cost, Mapping)
    assert isinstance(without, Mapping)

    assert with_cost["cost_usd_total"] == pytest.approx(0.4)
    assert without["cost_usd_total"] is None
    assert without["cost_usd_mean_per_case"] is None
    assert without["partial"] is True


def test_duration_mean_median_p90_and_provider_mean() -> None:
    """Wall-clock 1/2/3/10 → mean 4.0, median 2.5, p90 (nearest-rank) 10.0;
    provider-длительность — отдельная метрика по прогонам, где она есть."""
    cases = [make_case(f"C-{i}") for i in range(4)]
    evals = [
        build_eval(cases[0], wall=1.0, provider_ms=1000),
        build_eval(cases[1], wall=2.0, provider_ms=3000),
        build_eval(cases[2], wall=3.0),
        build_eval(cases[3], wall=10.0),
    ]

    duration = metrics_for_variant(evals)["duration"]
    assert isinstance(duration, Mapping)

    assert duration["wall_clock_s_mean"] == pytest.approx(4.0)
    assert duration["wall_clock_s_median"] == pytest.approx(2.5)
    assert duration["wall_clock_s_p90"] == pytest.approx(10.0)
    assert duration["provider_duration_ms_mean"] == pytest.approx(2000.0)
    assert duration["n_runs"] == 4
    assert duration["n_provider_duration_runs"] == 2


def test_valid_verdict_rate_ignores_config_failures_with_a_valid_sidecar() -> None:
    """Код 2 порога при годном sidecar (нет jq) — `config_failure` с `reviewer_ran`:
    сбой инструмента не входит в знаменатель валидности вердиктов модели.
    """
    cases = [make_case(f"C-{i}") for i in range(3)]
    evals = [
        build_eval(cases[0]),
        build_eval(cases[1]),
        build_eval(cases[2], outcome="config_failure", exit_code=2, reviewer_ran=True),
    ]

    summary = metrics_for_variant(evals)
    assert fraction_of(summary, "valid_verdict_rate") == (2, 2)
    assert fraction_of(summary, "config_failure_rate") == (1, 3)


def test_duration_excludes_empty_range_runs_by_outcome() -> None:
    """`empty_range` (кит не вызывался, wall 0.0) исключается **по исходу**, не по
    значению: иначе нулевые «прогоны» тянули бы среднее время вниз. Упавший на
    префлайте кит (`config_failure`) в статистике остаётся — его время измерено.
    """
    cases = [make_case(f"C-{i}") for i in range(3)]
    evals = [
        build_eval(cases[0], wall=2.0),
        build_eval(cases[1], wall=4.0),
        build_eval(cases[2], wall=0.0, outcome="empty_range", reviewer_ran=False),
        build_eval(cases[2], wall=6.0, outcome="config_failure", reviewer_ran=False),
    ]

    duration = metrics_for_variant(evals)["duration"]
    assert isinstance(duration, Mapping)

    assert duration["wall_clock_s_mean"] == pytest.approx(4.0)
    assert duration["n_runs"] == 3


def test_denominators_are_disclosed_at_top_level() -> None:
    """Числа кейсов, дефектов и предсказаний публикуются рядом с метриками (§9)."""
    case_a = make_case("C-1", defects=[make_defect(), make_defect("D-steward-1-2")])
    case_b = make_case("C-2", defects=[make_defect("D-steward-2-1")])
    evals = [
        build_eval(case_a, findings=[finding()]),
        build_eval(case_b, findings=[finding(), finding(line=12)]),
    ]

    summary = metrics_for_variant(evals)

    assert summary["n_cases"] == 2
    assert summary["n_gold_cases"] == 2
    assert summary["n_defects"] == 3
    assert summary["n_predictions"] == 3


# ---------------------------------------------------------------------------
# Повторения: per_repetition и ci
# ---------------------------------------------------------------------------


def two_repetition_evals() -> list[CaseEval]:
    """Два кейса × два повторения: во втором повторении у кейса B лишний known-FP."""
    case_a = make_case("C-a", defects=[make_defect("D-steward-1-1")])
    case_b = make_case(
        "C-b",
        defects=[make_defect("D-steward-2-1", file="app/b.py", line_hint=20, keywords=("leak",))],
        non_defects=[make_non_defect("NF-steward-2-1")],
    )
    hit_a = finding()
    hit_b = finding(file="app/b.py", line=20, title="leak в кэше")
    noise = finding(file="app/known.py", line=5, title="known ложная тревога")
    return [
        build_eval(case_a, findings=[hit_a], rep=1),
        build_eval(case_b, findings=[hit_b], rep=1),
        build_eval(case_a, findings=[hit_a], rep=2),
        build_eval(case_b, findings=[hit_b, noise], rep=2),
    ]


def test_per_repetition_splits_quality_metrics_by_repetition() -> None:
    """Повторение 1 → 2/2, повторение 2 → 2/3; пул по варианту → 4/5."""
    summary = metrics_for_variant(two_repetition_evals())
    per_rep = summary["per_repetition"]
    assert isinstance(per_rep, Mapping)

    assert sorted(per_rep) == ["1", "2"]
    first = per_rep["1"]["precision_lower_bound"]
    second = per_rep["2"]["precision_lower_bound"]
    assert (first["numerator"], first["denominator"]) == (2, 2)
    assert (second["numerator"], second["denominator"]) == (2, 3)
    assert fraction_of(summary, "precision_lower_bound") == (4, 5)
    assert sorted(per_rep["1"]) == sorted(CI_METRICS)
    assert len(CI_METRICS) == 5


def test_ci_is_computed_over_cases_with_repetitions_pooled() -> None:
    """Интервал — по двум кейсам (A=2/2, B=2/3), значит лежит в [2/3, 1]."""
    summary = metrics_for_variant(two_repetition_evals())
    ci = summary["ci"]
    assert isinstance(ci, Mapping)

    interval = ci["precision_lower_bound"]
    assert isinstance(interval, list)
    low, high = interval
    assert 2 / 3 - 1e-9 <= low <= high <= 1.0
    assert ci["false_block_rate"] is not None
    assert sorted(ci) == sorted(CI_METRICS)
    assert ci["resolvable_evidence_rate"] is None  # флагов evidence в фикстуре нет


# ---------------------------------------------------------------------------
# bootstrap_ci
# ---------------------------------------------------------------------------


def test_bootstrap_ci_is_deterministic_for_a_seed() -> None:
    """Один seed — один интервал; интервал лежит в [0, 1]."""
    per_case = [(1, 2), (2, 2), (0, 1), (3, 4), (1, 1)]

    first = bootstrap_ci(per_case, n=200, seed=7)
    second = bootstrap_ci(per_case, n=200, seed=7)

    assert first == second
    assert first is not None
    assert 0.0 <= first[0] <= first[1] <= 1.0


def test_bootstrap_ci_requires_two_cases_with_a_denominator() -> None:
    """Интервал по одному наблюдению — не интервал (тот же порог, что у парного).

    Один кейс — ``None``; два кейса, но знаменатель непустой лишь у одного —
    тоже ``None``: ресемплинг «по кейсам» в такой популяции просто повторяет
    единственное наблюдение и даёт ложно узкий интервал.
    """
    assert bootstrap_ci([(1, 2)], n=50, seed=0) is None
    assert bootstrap_ci([(1, 2), (0, 0)], n=50, seed=0) is None
    assert bootstrap_ci([(1, 2), (1, 2)], n=50, seed=0) is not None


def test_ci_is_absent_for_a_single_case_variant() -> None:
    """Вариант из одного кейса: значения есть, интервалов нет (отчёт печатает `—`)."""
    case = make_case("C-1", defects=[make_defect()])
    summary = metrics_for_variant([build_eval(case, findings=[finding()], resolvable=(True,))])
    ci = summary["ci"]
    assert isinstance(ci, Mapping)

    assert value_of(summary, "precision_lower_bound") == pytest.approx(1.0)
    assert all(ci[name] is None for name in CI_METRICS), ci


def test_bootstrap_ci_collapses_when_every_case_is_identical() -> None:
    """Все кейсы 1/2 → любой ресемпл даёт 0.5, интервал вырожден."""
    assert bootstrap_ci([(1, 2)] * 4, n=50, seed=0) == (0.5, 0.5)


def test_bootstrap_ci_none_on_empty_denominator() -> None:
    """Нулевой суммарный знаменатель — интервала нет (не 0.0)."""
    assert bootstrap_ci([(0, 0), (0, 0)], n=50, seed=0) is None
    assert bootstrap_ci([], n=50, seed=0) is None


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def paired_variants() -> tuple[list[CaseEval], list[CaseEval]]:
    """Вариант A: по 1 TP и 1 known-FP на кейс; вариант B: по 1 TP. Стоимость 0.5 / 0.25."""
    cases = [
        make_case(
            f"C-{i}",
            defects=[make_defect(f"D-steward-{i}-1")],
            non_defects=[make_non_defect(f"NF-steward-{i}-1")],
        )
        for i in (1, 2)
    ]
    noise = finding(file="app/known.py", line=5, title="known ложная тревога")
    left = [build_eval(case, findings=[finding(), noise], cost=0.5) for case in cases]
    right = [build_eval(case, findings=[finding()], cost=0.25) for case in cases]
    return left, right


def test_compare_point_difference_and_paired_ci() -> None:
    """plb 0.5 → 1.0 даёт разницу +0.5, стоимость 0.5 → 0.25 даёт −0.25; CI есть."""
    left, right = paired_variants()
    summary = compare(
        metrics_for_variant(left),
        metrics_for_variant(right),
        list(zip(left, right, strict=True)),
        n=200,
        seed=1,
    )
    metrics = summary["metrics"]
    assert isinstance(metrics, Mapping)

    assert summary["n_common_cases"] == 2
    assert metrics["precision_lower_bound"]["diff"] == pytest.approx(0.5)
    assert metrics["cost_usd_mean_per_case"]["diff"] == pytest.approx(-0.25)
    interval = metrics["precision_lower_bound"]["ci"]
    assert isinstance(interval, list)
    assert interval[0] <= 0.5 <= interval[1]
    assert metrics["precision_lower_bound"]["note"] is None


def test_compare_single_case_reports_point_difference_without_ci() -> None:
    """Один общий кейс: разница печатается, интервал — «без CI» (§9, N=1)."""
    left, right = paired_variants()
    summary = compare(
        metrics_for_variant(left[:1]),
        metrics_for_variant(right[:1]),
        [(left[0], right[0])],
        n=200,
        seed=1,
    )
    metrics = summary["metrics"]
    assert isinstance(metrics, Mapping)

    assert metrics["precision_lower_bound"]["diff"] == pytest.approx(0.5)
    assert metrics["precision_lower_bound"]["ci"] is None
    assert metrics["precision_lower_bound"]["note"] == "без CI"


def test_compare_leaves_diff_none_when_a_side_is_not_measured() -> None:
    """Сторона без годных прогонов — знаменатель 0, значение `None`, разница `None`.

    «Не измерено» и «измерено и вышло ноль» — разные вещи: подставить 0 значило
    бы объявить вариант, не выдавший ни одного вердикта, вариантом с нулевым
    recall.
    """
    _left, right = paired_variants()
    dead = [
        build_eval(ev.case, outcome="config_failure", exit_code=2, reviewer_ran=False)
        for ev in right
    ]
    summary = compare(
        metrics_for_variant(dead),
        metrics_for_variant(right),
        list(zip(dead, right, strict=True)),
        n=50,
        seed=1,
    )
    metrics = summary["metrics"]
    assert isinstance(metrics, Mapping)

    assert metrics["blocking_recall"]["a"]["value"] is None
    assert metrics["blocking_recall"]["a"]["denominator"] == 0
    assert metrics["blocking_recall"]["a"]["n_runs"] == 2
    assert metrics["blocking_recall"]["diff"] is None
    assert metrics["blocking_recall"]["b"]["value"] == pytest.approx(1.0)


def test_compare_publishes_paired_denominators_per_side() -> None:
    """Вариант, упавший на трудных кейсах, не выглядит лучше измеренного (§9).

    A отвечает на все четыре кейса: 2 TP на «дефектных» и 2 ложные блокировки
    на чистых. B отвечает только на два простых, на трудных — `config_failure`.
    Точечные значения B тогда лучше по **обеим** метрикам (precision 1.0 против
    0.5, false-block 0.0 против 0.5) — и это артефакт популяции, а не качества:
    знаменатели обязаны быть в отчёте рядом со значением.
    """
    defective = [make_case(f"C-def-{i}", defects=[make_defect(f"D-steward-{i}-1")]) for i in (1, 2)]
    clean = [make_case(f"C-clean-{i}", defects=[], cls="clean") for i in (3, 4)]
    hit = finding()
    false_alarm = finding(file="app/z.py", line=99, title="boom которого нет")

    left = [build_eval(case, findings=[hit]) for case in defective]
    left += [build_eval(case, findings=[false_alarm]) for case in clean]
    right = [build_eval(case, findings=[hit]) for case in defective]
    right += [
        build_eval(case, outcome="config_failure", exit_code=2, reviewer_ran=False)
        for case in clean
    ]

    summary = compare(
        metrics_for_variant(left),
        metrics_for_variant(right),
        list(zip(left, right, strict=True)),
        n=200,
        seed=2,
    )
    metrics = summary["metrics"]
    assert isinstance(metrics, Mapping)

    assert summary["n_common_cases"] == 4
    assert summary["n_pairs"] == 4

    plb = metrics["precision_lower_bound"]
    assert (plb["a"]["value"], plb["a"]["numerator"], plb["a"]["denominator"]) == (0.5, 2, 4)
    assert (plb["b"]["value"], plb["b"]["numerator"], plb["b"]["denominator"]) == (1.0, 2, 2)
    # Обе стороны прогнали по четыре прогона — в знаменатель вошла половина.
    assert plb["a"]["n_runs"] == plb["b"]["n_runs"] == 4
    assert plb["diff"] == pytest.approx(0.5)

    false_block = metrics["false_block_rate"]
    assert (false_block["a"]["numerator"], false_block["a"]["denominator"]) == (2, 4)
    assert (false_block["b"]["numerator"], false_block["b"]["denominator"]) == (0, 2)

    # Значение по всему варианту остаётся под рукой, но это не первая колонка.
    assert plb["a"]["variant_value"] == pytest.approx(0.5)
    assert plb["b"]["variant_value"] == pytest.approx(1.0)


def test_compare_resamples_cases_not_repetitions() -> None:
    """Один кейс в двух различающихся повторениях — это один кейс: CI нет,
    разница печатается с пометкой «без CI»."""
    case = make_case(
        "C-1",
        defects=[make_defect()],
        non_defects=[make_non_defect()],
    )
    noise = finding(file="app/known.py", line=5, title="known ложная тревога")
    left = [
        build_eval(case, findings=[finding(), noise], rep=1, cost=0.5),
        build_eval(case, findings=[finding()], rep=2, cost=0.4),
    ]
    right = [
        build_eval(case, findings=[finding()], rep=1, cost=0.25),
        build_eval(case, findings=[finding(), noise], rep=2, cost=0.2),
    ]

    summary = compare(
        metrics_for_variant(left),
        metrics_for_variant(right),
        list(zip(left, right, strict=True)),
        n=200,
        seed=3,
    )
    metrics = summary["metrics"]
    assert isinstance(metrics, Mapping)

    assert summary["n_pairs"] == 2
    assert summary["n_common_cases"] == 1
    for name in ("precision_lower_bound", "cost_usd_mean_per_case"):
        assert metrics[name]["ci"] is None, name
        assert metrics[name]["note"] == "без CI", name
    assert metrics["precision_lower_bound"]["diff"] == pytest.approx(0.0)


def test_compare_ci_is_deterministic_and_bounded() -> None:
    """Два кейса → интервал есть, детерминирован по seed и лежит в [−1, 1]."""
    left, right = paired_variants()
    pairs = list(zip(left, right, strict=True))
    args = (metrics_for_variant(left), metrics_for_variant(right), pairs)

    first = compare(*args, n=200, seed=5)
    second = compare(*args, n=200, seed=5)
    metrics = first["metrics"]
    assert isinstance(metrics, Mapping)

    assert first == second
    interval = metrics["precision_lower_bound"]["ci"]
    assert isinstance(interval, list)
    assert -1.0 <= interval[0] <= interval[1] <= 1.0


def test_compare_rejects_pairs_from_different_cases() -> None:
    """Пара из разных кейсов — `MetricsError`: парность была бы мнимой."""
    left, right = paired_variants()

    with pytest.raises(MetricsError, match="не парное"):
        compare(
            metrics_for_variant(left),
            metrics_for_variant(right),
            [(left[0], right[1])],
            n=50,
            seed=0,
        )


# ---------------------------------------------------------------------------
# Позиции находок и статус no_quality_runs
# ---------------------------------------------------------------------------


def test_non_object_finding_at_outcome_verdict_is_an_error(tmp_path: Path) -> None:
    """Мусорный элемент в `findings` при `outcome: verdict` — отказ, не фильтр.

    Прежде такой элемент молча выбрасывался, а прогон считался годным для
    метрик качества: числа считались по вердикту, часть которого не прочли. У
    «структурен» есть одно определение (`threshold.is_structural_verdict`), и
    раннер с метриками обязаны отвечать по нему одинаково — иначе прогон,
    который раннер объявил `verdict`, метрики читают по-своему.
    """
    case = make_case(
        defects=[make_defect("D-steward-1-2", file="app/b.py", line_hint=20, keywords=("leak",))]
    )
    write_run(
        tmp_path,
        verdict={
            "findings": [
                finding(file="app/z.py", line=1, title="без дефекта"),
                "мусор",
                finding(file="app/b.py", line=20, title="leak в кэше"),
            ],
            "note": "ok",
        },
    )

    with pytest.raises(MetricsError, match="не структурен"):
        evaluate_case(case, result_for(case), tmp_path, file_lines=lambda path: 40)


def test_findings_not_a_list_at_outcome_verdict_is_an_error(tmp_path: Path) -> None:
    """`findings` не список — тот же отказ: читать нечего, а исход обещает вердикт."""
    case = make_case(defects=[make_defect()])
    write_run(tmp_path, verdict={"findings": "nope", "note": "ok"})

    with pytest.raises(MetricsError, match="не структурен"):
        evaluate_case(case, result_for(case), tmp_path, file_lines=lambda path: 40)


def test_note_not_a_string_at_outcome_verdict_is_an_error(tmp_path: Path) -> None:
    """`note` не строка — структура нарушена так же, как у `findings`."""
    case = make_case(defects=[make_defect()])
    write_run(tmp_path, verdict={"findings": [finding()], "note": 7})

    with pytest.raises(MetricsError, match="не структурен"):
        evaluate_case(case, result_for(case), tmp_path, file_lines=lambda path: 40)


def test_schema_invalid_finding_at_outcome_verdict_is_an_error(tmp_path: Path) -> None:
    """Находка без `title` при `outcome: verdict` — отказ: кит такой вердикт отверг бы.

    `apply-threshold.sh` валидирует вердикт по схеме и на негодном выходит
    кодом 2, то есть исходом `invalid_verdict`. Значит `outcome: verdict` со
    схемно негодным sidecar — противоречие в артефактах: либо раннер записал
    не тот исход, либо файл подменили. Прежде такая находка молча входила в
    метрики качества — то есть в TP/FP попадало то, по чему настоящий гейт
    вообще не выносил решения.
    """
    case = make_case(defects=[make_defect()])
    broken = finding()
    del broken["title"]
    write_run(tmp_path, verdict={"findings": [broken], "note": "ok"})

    with pytest.raises(MetricsError, match="схем"):
        evaluate_case(case, result_for(case), tmp_path, file_lines=lambda path: 40)


def test_schema_invalid_finding_without_a_verdict_outcome_is_read(tmp_path: Path) -> None:
    """При исходе `invalid_verdict` тот же sidecar читается: там он и ожидается."""
    case = make_case(defects=[make_defect()])
    broken = finding()
    del broken["title"]
    write_run(tmp_path, verdict={"findings": [broken], "note": "ok"})
    result = result_for(case, outcome="invalid_verdict", exit_code=2)

    ev = evaluate_case(case, result, tmp_path, file_lines=lambda path: 40)

    assert len(ev.findings) == 1
    assert ev.match is None


def test_findings_keep_their_verdict_positions_without_a_verdict_outcome(
    tmp_path: Path,
) -> None:
    """Позиции находок сохраняются и там, где структура не требуется.

    При исходе не `verdict` (например `invalid_verdict`) вердикт мусорным быть
    вправе, и позиция находки в исходном списке остаётся её адресом: сдвинуть
    его значило бы указать разбирающему на чужую строку.
    """
    case = make_case(defects=[make_defect()])
    write_run(
        tmp_path,
        verdict={
            "findings": [finding(file="app/z.py", line=1), "мусор", finding(file="app/b.py")],
            "note": "ok",
        },
    )
    result = result_for(case, outcome="invalid_verdict", exit_code=2)

    ev = evaluate_case(case, result, tmp_path, file_lines=lambda path: 40)

    assert len(ev.findings) == 2
    assert ev.finding_positions == (0, 2)
    assert ev.match is None


def test_status_no_quality_runs_when_gold_exists_but_nothing_ran() -> None:
    """Gold-кейс есть, годных для качества прогонов нет — это не `ok`."""
    case = make_case("C-1", defects=[make_defect()])
    evals = [build_eval(case, outcome="config_failure", exit_code=2, reviewer_ran=False)]

    summary = metrics_for_variant(evals)

    assert summary["status"] == "no_quality_runs"
    assert summary["n_gold_cases"] == 1
    assert fraction_of(summary, "config_failure_rate") == (1, 1)
