"""Тесты steward.review_eval.report: metrics.json, report.md, adjudication-queue.md.

Модель не вызывается: `CaseEval` собираются руками теми же конструкторами, что
и в `test_metrics.py` (`make_case`, `make_defect`, `finding`, `build_eval`), а
сводки вариантов считает настоящий `metrics_for_variant` — тест не подделывает
числа, а фиксирует, как модуль их форматирует. Отчёт и очередь сравниваются
побайтно с эталонной строкой (снапшот): раздел статуса обязан идти раньше
таблиц, а `precision` — быть либо `n/a` (ключа нет вовсе), либо `— (0/0)`
(ключ есть, знаменатель пуст) — две эти формы нельзя перепутать.

Дизайн: docs/superpowers/specs/2026-09-14-review-eval-harness-design.md §8, §9, §10.
"""

from __future__ import annotations

import dataclasses
import json

import pytest
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from steward.review_eval.corpus import Annotation, Case, Defect, Match
from steward.review_eval.matcher import Prediction, match
from steward.review_eval.metrics import CaseEval, metrics_for_variant
from steward.review_eval.report import (
    ReportError,
    render_compare,
    render_queue,
    render_report,
    write_metrics_json,
    write_queue,
    write_report,
)
from steward.review_eval.runner import RunManifest, RunResult

# ---------------------------------------------------------------------------
# Фикстуры-конструкторы
#
# Те же самые конструкторы, что и в `test_metrics.py` (не импортируются:
# pyrefly не резолвит `tests.*` как пакет — import-root проекта — `src`, а
# кросс-модульный импорт тестов не является принятым паттерном репозитория).
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


def file_missing_finding(
    *,
    file: str = "app/gone.py",
    title: str = "boom: обязательного файла нет",
) -> dict[str, Any]:
    """Находка `kind: file-missing`: `line` строго 0 по схеме вердикта."""
    return {
        "kind": "file-missing",
        "severity": "major",
        "title": title,
        "file": file,
        "line": 0,
        "scenario": "запуск загрузчика",
        "observed_result": "файла нет",
        "expected_result": "файл есть",
        "evidence": [{"file": file, "line": 0, "reason": "файла нет в дереве"}],
        "confidence": "high",
    }


def make_case(
    case_id: str = "C-1",
    *,
    defects: Sequence[Defect] = (),
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
        non_defects=(),
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


def compact_manifest(
    *,
    run_id: str,
    commit: str,
    rules_digest: str,
    label: str,
    harness: str,
    model: str,
    effort: str | None,
    started: str,
    finished: str,
) -> dict[str, object]:
    """Минимальный `run.json` — один вариант, один дайджест кита, без tools."""
    return {
        "run_id": run_id,
        "kit": {"commit": commit},
        "tools": {},
        "variants": [
            {"label": label, "harness": harness, "model": model, "requested_effort": effort}
        ],
        "corpus_digest": "ignored",
        "matcher_version": 1,
        "matcher_rules_digest": rules_digest,
        "started": started,
        "finished": finished,
        "jobs": 1,
        "repetitions": 1,
    }


# ---------------------------------------------------------------------------
# render_report — (a) два варианта, оба `ok`
# ---------------------------------------------------------------------------


def test_render_report_two_variant_ok_snapshot() -> None:
    """Оба варианта `ok`: полный отчёт, побайтное сравнение с эталоном."""
    case = make_case(case_id="C-1", defects=[make_defect()])
    ev_a = build_eval(
        case,
        findings=[finding()],
        cost=0.5,
        wall=1.0,
        provider_ms=2000.0,
        resolvable=(True,),
    )
    ev_b = build_eval(
        case,
        findings=[finding()],
        cost=0.75,
        wall=2.5,
        resolvable=(True,),
    )
    metrics_by_variant = {
        "variant-a": metrics_for_variant([ev_a]),
        "variant-b": metrics_for_variant([ev_b]),
    }
    evals_by_variant = {"variant-a": [ev_a], "variant-b": [ev_b]}
    manifest = {
        "run_id": "20260914T100000Z-deadbeef",
        "kit": {
            "commit": "c" * 40,
            "prompt_sha256": "a" * 64,
            "schema_sha256": "b" * 64,
        },
        "tools": {"claude": "claude 1.2.3", "codex": "codex 4.5.6", "git": "git version 2.43.0"},
        "variants": [
            {
                "label": "variant-a",
                "harness": "claude",
                "model": "claude-opus-5",
                "requested_effort": None,
            },
            {
                "label": "variant-b",
                "harness": "codex",
                "model": "gpt-5.4",
                "requested_effort": "high",
            },
        ],
        "corpus_digest": "ignored",
        "matcher_version": 1,
        "matcher_rules_digest": "sha256:" + "f" * 64,
        "started": "2026-09-14T10:00:00Z",
        "finished": "2026-09-14T10:05:00Z",
        "jobs": 1,
        "repetitions": 1,
    }

    text = render_report(metrics_by_variant, evals_by_variant, manifest)

    expected = """# Review-eval report

## Прогон

- run_id: `20260914T100000Z-deadbeef`
- kit commit: `cccccccccccc`
- prompt_sha256: `aaaaaaaaaaaa`
- schema_sha256: `bbbbbbbbbbbb`
- tool claude: claude 1.2.3
- tool codex: codex 4.5.6
- tool git: git version 2.43.0
- matcher: version=1, rules_digest=`sha256:ffffffffffff`
- started: 2026-09-14T10:00:00Z
- finished: 2026-09-14T10:05:00Z

### Варианты

| label | harness | model | effort |
|---|---|---|---|
| variant-a | claude | claude-opus-5 | — |
| variant-b | codex | gpt-5.4 | high |

## Статус

- **variant-a**: ✅ ok
- **variant-b**: ✅ ok

## Качество

| метрика | variant-a | variant-a CI | variant-b | variant-b CI |
|---|---|---|---|---|
| precision_lower_bound | 1.000 (1/1) | — | 1.000 (1/1) | — |
| precision | 1.000 (1/1) | — | 1.000 (1/1) | — |
| blocking_recall | 1.000 (1/1) | — | 1.000 (1/1) | — |
| detection_recall_any_severity | 1.000 (1/1) | — | 1.000 (1/1) | — |
| false_block_rate | 0.000 (0/1) | — | 0.000 (0/1) | — |
| resolvable_evidence_rate | 1.000 (1/1) | — | 1.000 (1/1) | — |

## Эксплуатация

| метрика | variant-a | variant-b |
|---|---|---|
| completion_rate | 1.000 (1/1) | 1.000 (1/1) |
| valid_verdict_rate | 1.000 (1/1) | 1.000 (1/1) |
| config_failure_rate | 0.000 (0/1) | 0.000 (0/1) |
| mechanical_failure_rate | 0.000 (0/1) | 0.000 (0/1) |
| unexpected_outcome_count | 0.000 (0/1) | 0.000 (0/1) |
| empty_range_count | 0.000 (0/1) | 0.000 (0/1) |
| duplicate_rate | 0.000 (0/1) | 0.000 (0/1) |
| refuted_file_missing_count | 0.000 (0/1) | 0.000 (0/1) |
| contradicted_gold_count | 0.000 (0/1) | 0.000 (0/1) |

## Стоимость

| метрика | variant-a | variant-b |
|---|---|---|
| cost_usd_total | 0.500 | 0.750 |
| cost_usd_mean_per_case (unit: "run") | 0.500 | 0.750 |
| cost_available_cases | 1 | 1 |
| cost_unavailable_cases (unit: "run") | 0 | 0 |
| n_runs_with_cost | 1 | 1 |
| n_runs_total | 1 | 1 |
| partial | нет | нет |

## Длительность

| метрика | variant-a | variant-b |
|---|---|---|
| wall_clock_s_mean | 1.000 | 2.500 |
| wall_clock_s_median | 1.000 | 2.500 |
| wall_clock_s_p90 | 1.000 | 2.500 |
| provider_duration_ms_mean | 2000.000 | n/a |
| n_runs | 1 | 1 |
| n_provider_duration_runs | 1 | 0 |

## Кейсы

| case | rep | class | annotation | variant-a: outcome | variant-a: exit | variant-a: reviewer_ran | variant-a: wall_clock_s | variant-a: cost | variant-a: flags | variant-b: outcome | variant-b: exit | variant-b: reviewer_ran | variant-b: wall_clock_s | variant-b: cost | variant-b: flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C-1 | 1 | defective | adjudicated | verdict | 1 | да | 1.000 | 0.500 | - | verdict | 1 | да | 2.500 | 0.750 | - |

## Очередь адъюдикации

Очередь пуста.
"""
    assert text == expected


# ---------------------------------------------------------------------------
# render_report — (b) pending_adjudication
# ---------------------------------------------------------------------------


def test_render_report_pending_adjudication_snapshot() -> None:
    """Один вариант, `pending_adjudication`: предупреждение раньше таблиц,
    `precision` — ключ есть, значение `— (0/0)` (не `n/a`: очередь непуста, но
    знаменатель после исключения неразмеченных ещё не посчитан)."""
    case = make_case(case_id="C-pending", defects=[])
    ev = build_eval(
        case, findings=[finding()], exit_code=1, cost=None, wall=3.0, resolvable=(True,)
    )

    metrics_by_variant = {"variant-x": metrics_for_variant([ev])}
    evals_by_variant = {"variant-x": [ev]}
    manifest = compact_manifest(
        run_id="rid-1",
        commit="d" * 40,
        rules_digest="sha256:" + "0" * 64,
        label="variant-x",
        harness="h",
        model="m",
        effort=None,
        started="2026-09-14T00:00:00Z",
        finished="2026-09-14T00:01:00Z",
    )

    text = render_report(metrics_by_variant, evals_by_variant, manifest)

    expected = """# Review-eval report

## Прогон

- run_id: `rid-1`
- kit commit: `dddddddddddd`
- matcher: version=1, rules_digest=`sha256:000000000000`
- started: 2026-09-14T00:00:00Z
- finished: 2026-09-14T00:01:00Z

### Варианты

| label | harness | model | effort |
|---|---|---|---|
| variant-x | h | m | — |

## Статус

- **variant-x**: ⚠️ pending_adjudication — precision не публикуется, пока очередь не разобрана

## Качество

| метрика | variant-x | variant-x CI |
|---|---|---|
| precision_lower_bound | 0.000 (0/1) | — |
| precision | n/a | — |
| blocking_recall | — (0/0) | — |
| detection_recall_any_severity | — (0/0) | — |
| false_block_rate | 1.000 (1/1) | — |
| resolvable_evidence_rate | 1.000 (1/1) | — |

## Эксплуатация

| метрика | variant-x |
|---|---|
| completion_rate | 1.000 (1/1) |
| valid_verdict_rate | 1.000 (1/1) |
| config_failure_rate | 0.000 (0/1) |
| mechanical_failure_rate | 0.000 (0/1) |
| unexpected_outcome_count | 0.000 (0/1) |
| empty_range_count | 0.000 (0/1) |
| duplicate_rate | 0.000 (0/1) |
| refuted_file_missing_count | 0.000 (0/1) |
| contradicted_gold_count | 0.000 (0/0) |

## Стоимость

| метрика | variant-x |
|---|---|
| cost_usd_total | n/a |
| cost_usd_mean_per_case (unit: "run") | n/a |
| cost_available_cases | 0 |
| cost_unavailable_cases (unit: "run") | 1 |
| n_runs_with_cost | 0 |
| n_runs_total | 1 |
| partial | да |

## Длительность

| метрика | variant-x |
|---|---|
| wall_clock_s_mean | 3.000 |
| wall_clock_s_median | 3.000 |
| wall_clock_s_p90 | 3.000 |
| provider_duration_ms_mean | n/a |
| n_runs | 1 |
| n_provider_duration_runs | 0 |

## Кейсы

| case | rep | class | annotation | variant-x: outcome | variant-x: exit | variant-x: reviewer_ran | variant-x: wall_clock_s | variant-x: cost | variant-x: flags |
|---|---|---|---|---|---|---|---|---|---|
| C-pending | 1 | defective | adjudicated | verdict | 1 | да | 3.000 | unavailable | - |

## Очередь адъюдикации

- влияет на precision: предсказаний 1, неоднозначных компонент 0
- прочее (draft / не блокирующие): предсказаний 0, неоднозначных компонент 0

См. `adjudication-queue.md`.
"""
    assert text == expected
    # `precision` — не ключ вовсе (D9: очередь непуста), в отличие от
    # `— (0/0)` в сценарии (c), где ключ есть, но знаменатель пуст.
    quality_metrics = metrics_by_variant["variant-x"]["metrics"]
    assert isinstance(quality_metrics, dict)
    assert "precision" not in quality_metrics
    assert quality_metrics["blocking_recall"]["value"] is None


# ---------------------------------------------------------------------------
# render_report — (c) no_quality_runs
# ---------------------------------------------------------------------------


def test_render_report_no_quality_runs_snapshot() -> None:
    """Ни один прогон не дал вердикта: `precision` — ключ есть, `— (0/0)`
    (очередь пуста при пустом qualiy-множестве — но статус решает `no_quality_runs`
    раньше, чем это замечается). Манифест — датакласс `RunManifest`, не `Mapping`."""
    case = make_case(case_id="C-noquality", defects=[])
    ev = build_eval(
        case,
        findings=(),
        outcome="config_failure",
        exit_code=2,
        reviewer_ran=False,
        cost=None,
        wall=5.0,
    )

    metrics_by_variant = {"variant-y": metrics_for_variant([ev])}
    evals_by_variant = {"variant-y": [ev]}
    manifest = RunManifest(
        run_id="rid-2",
        kit={"commit": "e" * 40},
        tools={},
        variants=[
            {"label": "variant-y", "harness": "h2", "model": "m2", "requested_effort": "medium"}
        ],
        corpus_digest="ignored",
        matcher_version=1,
        matcher_rules_digest="sha256:" + "1" * 64,
        started="2026-09-14T01:00:00Z",
        finished="2026-09-14T01:02:00Z",
        jobs=1,
        repetitions=1,
    )

    text = render_report(metrics_by_variant, evals_by_variant, manifest)

    expected = """# Review-eval report

## Прогон

- run_id: `rid-2`
- kit commit: `eeeeeeeeeeee`
- matcher: version=1, rules_digest=`sha256:111111111111`
- started: 2026-09-14T01:00:00Z
- finished: 2026-09-14T01:02:00Z

### Варианты

| label | harness | model | effort |
|---|---|---|---|
| variant-y | h2 | m2 | medium |

## Статус

- **variant-y**: ⚠️ no_quality_runs — нет прогонов, пригодных для метрик качества (нет вердикта или исход не совпал с ожидаемым): только эксплуатационные метрики

## Качество

| метрика | variant-y | variant-y CI |
|---|---|---|
| precision_lower_bound | — (0/0) | — |
| precision | — (0/0) | — |
| blocking_recall | — (0/0) | — |
| detection_recall_any_severity | — (0/0) | — |
| false_block_rate | — (0/0) | — |
| resolvable_evidence_rate | — (0/0) | — |

## Эксплуатация

| метрика | variant-y |
|---|---|
| completion_rate | 0.000 (0/1) |
| valid_verdict_rate | — (0/0) |
| config_failure_rate | 1.000 (1/1) |
| mechanical_failure_rate | 0.000 (0/1) |
| unexpected_outcome_count | 0.000 (0/1) |
| empty_range_count | 0.000 (0/1) |
| duplicate_rate | — (0/0) |
| refuted_file_missing_count | 0.000 (0/0) |
| contradicted_gold_count | 0.000 (0/0) |

## Стоимость

| метрика | variant-y |
|---|---|
| cost_usd_total | n/a |
| cost_usd_mean_per_case (unit: "run") | n/a |
| cost_available_cases | 0 |
| cost_unavailable_cases (unit: "run") | 1 |
| n_runs_with_cost | 0 |
| n_runs_total | 1 |
| partial | да |

## Длительность

| метрика | variant-y |
|---|---|
| wall_clock_s_mean | 5.000 |
| wall_clock_s_median | 5.000 |
| wall_clock_s_p90 | 5.000 |
| provider_duration_ms_mean | n/a |
| n_runs | 1 |
| n_provider_duration_runs | 0 |

## Кейсы

| case | rep | class | annotation | variant-y: outcome | variant-y: exit | variant-y: reviewer_ran | variant-y: wall_clock_s | variant-y: cost | variant-y: flags |
|---|---|---|---|---|---|---|---|---|---|
| C-noquality | 1 | defective | adjudicated | config_failure | 2 | нет | 5.000 | unavailable | - |

## Очередь адъюдикации

Очередь пуста.
"""
    assert text == expected


# ---------------------------------------------------------------------------
# write_report / write_metrics_json
# ---------------------------------------------------------------------------


def test_write_report_writes_render_report_output(tmp_path: Path) -> None:
    """`write_report` пишет ровно то, что вернул бы `render_report`."""
    case = make_case(defects=[make_defect()])
    ev = build_eval(case, findings=[finding()])
    metrics_by_variant = {"v": metrics_for_variant([ev])}
    evals_by_variant = {"v": [ev]}
    manifest = compact_manifest(
        run_id="r",
        commit="0" * 40,
        rules_digest="sha256:" + "2" * 64,
        label="v",
        harness="h",
        model="m",
        effort=None,
        started="s",
        finished="f",
    )

    path = write_report(tmp_path, metrics_by_variant, evals_by_variant, manifest)

    assert path == tmp_path / "report.md"
    assert path.read_text(encoding="utf-8") == render_report(
        metrics_by_variant, evals_by_variant, manifest
    )


def test_write_metrics_json_is_canonical_and_reloadable(tmp_path: Path) -> None:
    """`sort_keys`, завершающий `\\n`, содержимое читается обратно без потерь."""
    metrics_by_variant = {"b": {"status": "ok", "z": 1}, "a": {"status": "no_gold"}}
    comparisons = {"a_vs_b": {"x": 1}}

    path = write_metrics_json(tmp_path, metrics_by_variant, comparisons=comparisons)
    text = path.read_text(encoding="utf-8")

    assert text.endswith("\n") and not text.endswith("\n\n")
    reloaded = json.loads(text)
    assert reloaded == {
        "variants": {"a": {"status": "no_gold"}, "b": {"status": "ok", "z": 1}},
        "comparisons": {"a_vs_b": {"x": 1}},
        "recomputed_with": None,
    }
    # sort_keys: "comparisons" < "variants" на верхнем уровне.
    assert text.index('"comparisons"') < text.index('"variants"')


def test_write_metrics_json_comparisons_absent_is_null(tmp_path: Path) -> None:
    """Без сравнения и без дрейфа матчера — явные `null`, не отсутствующие ключи."""
    path = write_metrics_json(tmp_path, {"a": {"status": "no_gold"}})
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded == {
        "variants": {"a": {"status": "no_gold"}},
        "comparisons": None,
        "recomputed_with": None,
    }


def test_write_metrics_json_records_a_matcher_recompute(tmp_path: Path) -> None:
    """Пересчёт другим матчером виден в файле, а не только в stderr команды."""
    recomputed = {
        "matcher_version": 2,
        "matcher_rules_digest": "sha256:" + "7" * 64,
        "at": "2026-09-15T10:00:00Z",
    }

    path = write_metrics_json(tmp_path, {"a": {"status": "ok"}}, recomputed_with=recomputed)

    assert json.loads(path.read_text(encoding="utf-8"))["recomputed_with"] == recomputed


def test_render_report_shows_both_matcher_provenances() -> None:
    """Шапка при пересчёте несёт обе строки: прогона и пересчёта."""
    case = make_case(case_id="C-1", defects=[make_defect()])
    ev = build_eval(case, findings=[finding()])
    manifest = compact_manifest(
        run_id="r",
        commit="0" * 40,
        rules_digest="sha256:" + "8" * 64,
        label="v",
        harness="h",
        model="m",
        effort=None,
        started="2026-09-14T00:00:00Z",
        finished="2026-09-14T00:01:00Z",
    )

    text = render_report(
        {"v": metrics_for_variant([ev])},
        {"v": [ev]},
        manifest,
        recomputed_with={
            "matcher_version": 2,
            "matcher_rules_digest": "sha256:" + "7" * 64,
            "at": "2026-09-15T10:00:00Z",
        },
    )

    assert "- matcher: version=1, rules_digest=`sha256:888888888888`" in text
    assert (
        "- пересчитано матчером: version=2, rules_digest=`sha256:777777777777`, "
        "at 2026-09-15T10:00:00Z" in text
    )
    # Строка пересчёта идёт до времени прогона — рядом с провенансом, а не в конце.
    assert text.index("пересчитано матчером") < text.index("- started:")


def test_write_metrics_json_is_byte_stable_across_input_order(tmp_path: Path) -> None:
    """Тот же набор вариантов в другом порядке словаря — те же байты (`sort_keys`)."""
    forward = {"a": {"status": "ok"}, "b": {"status": "no_gold"}}
    backward = {"b": {"status": "no_gold"}, "a": {"status": "ok"}}

    first = write_metrics_json(tmp_path, forward)
    text_forward = first.read_text(encoding="utf-8")
    second = write_metrics_json(tmp_path, backward)
    text_backward = second.read_text(encoding="utf-8")

    assert text_forward == text_backward


# ---------------------------------------------------------------------------
# render_queue / write_queue
# ---------------------------------------------------------------------------


def test_render_queue_empty_says_queue_is_empty() -> None:
    """Ничего не назначено в очередь ни у одного варианта — одна строка."""
    case = make_case(defects=[make_defect()])
    ev = build_eval(case, findings=[finding()])  # назначается mutual-best, очередь пуста

    assert render_queue({"v": [ev]}) == "Очередь пуста.\n"


def test_render_queue_no_verdict_run_is_empty() -> None:
    """Прогон без матчинга (`ev.match is None`) не порождает записей в очереди."""
    case = make_case(defects=[])
    ev = build_eval(case, findings=(), outcome="config_failure", exit_code=2, reviewer_ran=False)

    assert render_queue({"v": [ev]}) == "Очередь пуста.\n"


def _ambiguous_queue_eval() -> tuple[object, object]:
    """Кейс с неоднозначной компонентой (2 дефекта, 1 predication) и двумя
    неразмеченными находками — одна блокирующая, одна нет. Возвращает `(case, ev)`."""
    defect_a = make_defect("D-steward-1-1", file="app/a.py", line_hint=10, keywords=("boom",))
    defect_b = make_defect("D-steward-1-2", file="app/a.py", line_hint=10, keywords=("boom",))
    case = make_case(case_id="C-queue", defects=[defect_a, defect_b])

    tied = finding(file="app/a.py", line=10, title="boom candidate")
    blocking_unlabeled = finding(
        file="app/other.py",
        line=5,
        severity="major",
        confidence="high",
        title="unlabeled-blocking",
    )
    quiet_unlabeled = finding(
        file="app/quiet.py",
        line=1,
        severity="minor",
        confidence="medium",
        title="unlabeled-nonblocking",
    )
    ev = build_eval(case, findings=[tied, blocking_unlabeled, quiet_unlabeled])
    assert ev.match is not None
    assert ev.match.unlabeled == (1, 2)
    assert len(ev.match.ambiguous) == 1
    return case, ev


def test_render_queue_lists_unlabeled_and_ambiguous_with_rederived_edges() -> None:
    """Блокирующие unlabeled наверху, ambiguous — со всеми рёбрами и весами,
    неблокирующие unlabeled — во вторичном свёрнутом списке."""
    _, ev = _ambiguous_queue_eval()

    text = render_queue({"variant-a": [ev]})

    expected = (
        "# Adjudication queue\n"
        "\n"
        "## variant-a\n"
        "\n"
        "### C-queue (rep 1, annotation: adjudicated)\n"
        "\n"
        "#### Блокирующие — неразмеченные\n"
        "\n"
        "- #1 severity=major confidence=high app/other.py:5 — "
        "'unlabeled-blocking' — 'запуск загрузчика'\n"
        "\n"
        "#### Неоднозначные компоненты\n"
        "\n"
        "- predictions=[0] defects=['D-steward-1-1', 'D-steward-1-2']\n"
        "  - #0 -> D-steward-1-1 weight=[1, 0, 0]\n"
        "  - #0 -> D-steward-1-2 weight=[1, 0, 0]\n"
        "\n"
        "<details><summary>Неблокирующие неразмеченные (1)</summary>\n"
        "\n"
        "- #2 severity=minor confidence=medium app/quiet.py:1 — "
        "'unlabeled-nonblocking' — 'запуск загрузчика'\n"
        "\n"
        "</details>\n"
    )
    assert text == expected


def test_render_queue_omits_a_refuted_file_missing_prediction() -> None:
    """Опровергнутая деревом head находка «файла нет» в очередь не попадает.

    Она размечена фактом: дерево `head_sha` говорит, что файл есть. Показывать
    её разбирающему значило бы дать работу, у которой уже есть ответ, — и
    очередь никогда бы не пустела.
    """
    case = make_case(case_id="C-refuted", defects=[make_defect()])
    ev = build_eval(case, findings=[file_missing_finding()], refuted=[0])

    assert render_queue({"variant-a": [ev]}) == "Очередь пуста.\n"


def test_render_queue_keeps_an_unrefuted_file_missing_prediction() -> None:
    """Та же находка без опровержения — законная работа для разбирающего."""
    case = make_case(case_id="C-refuted", defects=[make_defect()])
    ev = build_eval(case, findings=[file_missing_finding()])

    text = render_queue({"variant-a": [ev]})

    assert "#### Блокирующие — неразмеченные" in text
    assert "app/gone.py:0" in text


def test_render_queue_lists_contradicted_gold_under_its_own_heading() -> None:
    """Противоречащий дереву gold — работа по **корпусу**, и она отделена.

    Разбирающий очередь правит разметку кейса, а не решает про находку:
    gold утверждает «файла нет», дерево `head_sha` говорит обратное. Смешивать
    это с неразмеченными предсказаниями значило бы прятать правку корпуса среди
    решений про находки.
    """
    gold = make_defect("D-steward-1-2", file="app/gone.py", line_hint=0)
    case = make_case(case_id="C-contra", defects=[gold])
    ev = build_eval(
        case,
        findings=[file_missing_finding()],
        refuted=[0],
        contradicted_gold=["D-steward-1-2"],
    )

    text = render_queue({"variant-a": [ev]})

    assert "#### gold противоречит дереву head" in text
    assert "- D-steward-1-2" in text
    # Опровергнутая находка в «неразмеченные» не попадает.
    assert "#### Блокирующие — неразмеченные" not in text


def test_queue_summary_reports_contradicted_gold_from_any_run() -> None:
    """Сводка очереди не говорит «пуста», пока разметка спорит с деревом."""
    gold = make_defect("D-steward-1-2", file="app/gone.py", line_hint=0)
    case = make_case(case_id="C-contra", defects=[gold])
    ev = build_eval(
        case,
        findings=[file_missing_finding()],
        refuted=[0],
        contradicted_gold=["D-steward-1-2"],
        unexpected=True,
    )
    manifest = compact_manifest(
        run_id="rid-contra",
        commit="a" * 40,
        rules_digest="sha256:" + "0" * 64,
        label="variant-a",
        harness="claude",
        model="claude-opus-5",
        effort="high",
        started="2026-09-14T00:00:00Z",
        finished="2026-09-14T00:01:00Z",
    )

    text = render_report({"variant-a": metrics_for_variant([ev])}, {"variant-a": [ev]}, manifest)

    assert "Очередь пуста." not in text
    assert "gold противоречит дереву head: дефектов 1" in text


def test_render_report_prints_the_contradicted_gold_count() -> None:
    """Счётчик противоречий печатается в блоке «Эксплуатация»."""
    gold = make_defect("D-steward-1-2", file="app/gone.py", line_hint=0)
    case = make_case(case_id="C-contra", defects=[gold])
    ev = build_eval(
        case,
        findings=[file_missing_finding()],
        refuted=[0],
        contradicted_gold=["D-steward-1-2"],
    )
    manifest = compact_manifest(
        run_id="rid-contra",
        commit="a" * 40,
        rules_digest="sha256:" + "0" * 64,
        label="variant-a",
        harness="claude",
        model="claude-opus-5",
        effort="high",
        started="2026-09-14T00:00:00Z",
        finished="2026-09-14T00:01:00Z",
    )

    text = render_report({"variant-a": metrics_for_variant([ev])}, {"variant-a": [ev]}, manifest)

    assert "| contradicted_gold_count | 1.000 (1/1) |" in text


def test_render_report_prints_the_refuted_count() -> None:
    """Счётчик опровергнутых находок печатается в блоке «Эксплуатация»."""
    case = make_case(case_id="C-refuted", defects=[make_defect()])
    ev = build_eval(case, findings=[finding(), file_missing_finding()], refuted=[1])

    manifest = compact_manifest(
        run_id="rid-refuted",
        commit="a" * 40,
        rules_digest="sha256:" + "0" * 64,
        label="variant-a",
        harness="claude",
        model="claude-opus-5",
        effort="high",
        started="2026-09-14T00:00:00Z",
        finished="2026-09-14T00:01:00Z",
    )

    text = render_report({"variant-a": metrics_for_variant([ev])}, {"variant-a": [ev]}, manifest)

    assert "| refuted_file_missing_count | 1.000 (1/2) |" in text


def test_metrics_json_always_carries_the_refuted_count(tmp_path: Path) -> None:
    """Ключ есть и при нуле опровержений: «нечего опровергать» — это число."""
    case = make_case(case_id="C-plain", defects=[make_defect()])
    ev = build_eval(case, findings=[finding()])

    path = write_metrics_json(tmp_path, {"variant-a": metrics_for_variant([ev])})
    reloaded = json.loads(path.read_text(encoding="utf-8"))

    entry = reloaded["variants"]["variant-a"]["metrics"]["refuted_file_missing_count"]
    assert entry["value"] == 0.0
    assert (entry["numerator"], entry["denominator"]) == (0, 1)


def test_render_queue_edges_are_rederived_not_residual() -> None:
    """`build_edges` пересчитан заново: у компоненты видны рёбра к обоим
    дефектам, хотя `Component.edges` — те же (тут остаточные равны полным,
    проверяем согласованность форм записи, а не только сам факт наличия)."""
    case, ev = _ambiguous_queue_eval()
    assert ev.match is not None
    component = ev.match.ambiguous[0]

    # Пересчитанное множество (то, что должно попасть в очередь) содержит
    # обе связи предсказания-0 с обоими дефектами.
    preds = tuple(Prediction(index=i, finding=f) for i, f in enumerate(ev.findings))
    recomputed = match(preds, case.defects, case.non_defects)
    assert recomputed.ambiguous == ev.match.ambiguous
    assert {e.defect_id for e in component.edges} == {"D-steward-1-1", "D-steward-1-2"}


def test_render_queue_deterministic_across_variant_dict_order() -> None:
    """Тот же набор вариантов в другом порядке словаря → те же байты."""
    _, ev_ambiguous = _ambiguous_queue_eval()
    empty_case = make_case(case_id="C-empty", defects=[make_defect()])
    ev_empty = build_eval(empty_case, findings=[finding()])

    forward = {"variant-a": [ev_ambiguous], "variant-b": [ev_empty]}
    backward = {"variant-b": [ev_empty], "variant-a": [ev_ambiguous]}

    text_forward = render_queue(forward)
    text_backward = render_queue(backward)

    assert text_forward == text_backward
    # тот же вызов дважды на том же входе → те же байты
    assert render_queue(forward) == text_forward


def test_quality_table_prints_metric_notes_under_the_table() -> None:
    """Пометка метрики (например «кэш недоступен») печатается под таблицей.

    В ячейке стоит `— (0/0)` — форма «мерить не на чем», — и без пояснения
    читатель не отличит её от пустого знаменателя по другой причине.
    """
    case = make_case(case_id="C-1", defects=[make_defect()])
    ev = build_eval(case, findings=[finding()], evidence_unchecked=True)

    text = render_report(
        {"variant-x": metrics_for_variant([ev])},
        {"variant-x": [ev]},
        compact_manifest(
            run_id="r",
            commit="0" * 40,
            rules_digest="sha256:" + "6" * 64,
            label="variant-x",
            harness="h",
            model="m",
            effort=None,
            started="s",
            finished="f",
        ),
    )

    quality = text.split("## Качество")[1].split("## Эксплуатация")[0]
    assert "| resolvable_evidence_rate | — (0/0) | — |" in quality
    assert (
        "- resolvable_evidence_rate (variant-x): кэш недоступен — "
        "evidence не проверялся; сверка gold 'файла нет' с деревом head_sha "
        "тоже пропущена (1 кейсов)" in quality
    )


def test_queue_summary_separates_gating_from_the_rest() -> None:
    """Непустая очередь рядом с `status: ok` — не противоречие, если сказать, чем
    она непуста: неблокирующая находка precision не задерживает (D9).

    Один gold-TP плюс одно неразмеченное **неблокирующее** предсказание:
    `precision` публикуется, статус `ok`, а в сводке 0 влияющих на precision и
    1 прочее.
    """
    case = make_case(case_id="C-1", defects=[make_defect()])
    quiet = finding(
        file="app/quiet.py", line=1, severity="minor", confidence="medium", title="не блокирует"
    )
    ev = build_eval(case, findings=[finding(), quiet], resolvable=(True,))
    assert ev.match is not None
    assert ev.match.unlabeled == (1,)

    summary = metrics_for_variant([ev])
    assert summary["status"] == "ok"
    metrics = summary["metrics"]
    assert isinstance(metrics, Mapping)
    assert "precision" in metrics

    text = render_report(
        {"v": summary},
        {"v": [ev]},
        compact_manifest(
            run_id="r",
            commit="0" * 40,
            rules_digest="sha256:" + "2" * 64,
            label="v",
            harness="h",
            model="m",
            effort=None,
            started="s",
            finished="f",
        ),
    )

    assert "- влияет на precision: предсказаний 0, неоднозначных компонент 0" in text
    assert "- прочее (draft / не блокирующие): предсказаний 1, неоднозначных компонент 0" in text


def test_queue_summary_counts_a_draft_case_as_other() -> None:
    """Прогон draft-кейса в метрики не входит вовсе — и в gating-очередь тоже."""
    draft = make_case(case_id="C-draft", defects=[], status="draft")
    ev = build_eval(draft, findings=[finding()])

    text = render_report(
        {"v": metrics_for_variant([ev])},
        {"v": [ev]},
        compact_manifest(
            run_id="r",
            commit="0" * 40,
            rules_digest="sha256:" + "3" * 64,
            label="v",
            harness="h",
            model="m",
            effort=None,
            started="s",
            finished="f",
        ),
    )

    assert "- влияет на precision: предсказаний 0, неоднозначных компонент 0" in text
    assert "- прочее (draft / не блокирующие): предсказаний 1, неоднозначных компонент 0" in text


def test_report_orders_variants_as_in_the_manifest() -> None:
    """Порядок колонок — из манифеста (первый `--variant` — базис), не по алфавиту."""
    case = make_case(case_id="C-1", defects=[make_defect()])
    ev = build_eval(case, findings=[finding()])
    metrics_by_variant = {"alpha": metrics_for_variant([ev]), "omega": metrics_for_variant([ev])}
    manifest = {
        "run_id": "r",
        "kit": {"commit": "0" * 40},
        "tools": {},
        "variants": [
            {"label": "omega", "harness": "h", "model": "m", "requested_effort": None},
            {"label": "alpha", "harness": "h", "model": "m", "requested_effort": None},
        ],
        "corpus_digest": "d",
        "matcher_version": 1,
        "matcher_rules_digest": "sha256:" + "4" * 64,
        "started": "s",
        "finished": "f",
        "jobs": 1,
        "repetitions": 1,
    }

    text = render_report(metrics_by_variant, {"alpha": [ev], "omega": [ev]}, manifest)

    assert "| метрика | omega | omega CI | alpha | alpha CI |" in text
    assert text.index("- **omega**:") < text.index("- **alpha**:")
    assert "| omega: outcome |" in text.split("## Кейсы")[1].splitlines()[2]


def test_report_appends_variants_absent_from_the_manifest_alphabetically() -> None:
    """Вариант, долитый прошлым запуском, идёт следом и в алфавитном порядке."""
    case = make_case(case_id="C-1", defects=[make_defect()])
    ev = build_eval(case, findings=[finding()])
    summary = metrics_for_variant([ev])
    metrics_by_variant = {"b-extra": summary, "in-manifest": summary, "a-extra": summary}
    manifest = compact_manifest(
        run_id="r",
        commit="0" * 40,
        rules_digest="sha256:" + "5" * 64,
        label="in-manifest",
        harness="h",
        model="m",
        effort=None,
        started="s",
        finished="f",
    )

    text = render_report(
        metrics_by_variant,
        {name: [ev] for name in metrics_by_variant},
        manifest,
    )

    assert (
        "| метрика | in-manifest | in-manifest CI | a-extra | a-extra CI | b-extra | b-extra CI |"
        in text
    )


def test_queue_labels_every_case_with_its_annotation_status() -> None:
    """У кейса в очереди видно `annotation.status`: разбирать draft и gold —
    разная работа, и по заголовку это должно быть понятно без корпуса."""
    _, ev = _ambiguous_queue_eval()

    text = render_queue({"variant-a": [ev]})

    assert "### C-queue (rep 1, annotation: adjudicated)" in text


@pytest.mark.parametrize("name", ["metrics.json", "report.md", "adjudication-queue.md"])
def test_writers_refuse_a_symlinked_output_and_keep_the_target(tmp_path: Path, name: str) -> None:
    """Симлинк на месте выходного файла — отказ, внешняя цель не тронута.

    `write_text` по ссылке пишет в её цель: подготовленный каталог прогона
    выводил бы запись за пределы `run_dir` и портил бы произвольный файл.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    external = tmp_path / "victim.txt"
    external.write_text("important", encoding="utf-8")
    (run_dir / name).symlink_to(external)
    case = make_case(defects=[make_defect()])
    ev = build_eval(case, findings=[finding()])
    evals_by_variant = {"v": [ev]}
    metrics_by_variant = {"v": metrics_for_variant([ev])}
    manifest = RunManifest(
        run_id="r",
        kit={"commit": "c"},
        tools={},
        variants=[{"label": "v", "harness": "h", "model": "m", "effort": None}],
        corpus_digest="d",
        matcher_version=1,
        matcher_rules_digest="x",
        started="s",
        finished="f",
        jobs=1,
        repetitions=1,
    )

    with pytest.raises(ReportError, match="символическая ссылка"):
        if name == "metrics.json":
            write_metrics_json(run_dir, metrics_by_variant)
        elif name == "report.md":
            write_report(run_dir, metrics_by_variant, evals_by_variant, manifest)
        else:
            write_queue(run_dir, evals_by_variant)

    assert external.read_text(encoding="utf-8") == "important"


def test_writers_accept_a_symlinked_prefix_above_the_run_dir(tmp_path: Path) -> None:
    """Симлинк **выше** `run_dir` (macOS `/tmp` → `/private/tmp`) — не нарушение:
    та же политика, что у раннера — ссылки запрещены внутри каталога прогона.
    """
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    run_dir = link / "sub"
    run_dir.mkdir()

    path = write_metrics_json(run_dir, {"v": {"status": "no_gold"}})

    assert path == run_dir / "metrics.json"
    assert (real / "sub" / "metrics.json").is_file()


def test_write_queue_writes_render_queue_output(tmp_path: Path) -> None:
    """`write_queue` пишет ровно то, что вернул бы `render_queue`."""
    case = make_case(defects=[make_defect()])
    ev = build_eval(case, findings=[finding()])
    evals_by_variant = {"v": [ev]}

    path = write_queue(tmp_path, evals_by_variant)

    assert path == tmp_path / "adjudication-queue.md"
    assert path.read_text(encoding="utf-8") == render_queue(evals_by_variant)


# ---------------------------------------------------------------------------
# render_compare
# ---------------------------------------------------------------------------


def _side(
    value: float | None,
    numerator: float | int,
    denominator: float | int,
    *,
    n_runs: int = 4,
) -> dict[str, object]:
    """Сторона сравнения в форме `metrics.compare` (парное значение + знаменатель)."""
    return {
        "value": value,
        "numerator": numerator,
        "denominator": denominator,
        "n_runs": n_runs,
        "variant_value": value,
    }


def test_render_compare_table() -> None:
    """Таблица `метрика | a | b | diff | CI | note`: значения — со знаменателями.

    Знаменатель в каждой ячейке и есть смысл этой таблицы: `1.000 (2/2)` рядом
    с `0.500 (2/4)` читается как «измерено на половине», а два голых числа
    читались бы как «лучше».
    """
    comparison: dict[str, object] = {
        "n_common_cases": 4,
        "n_pairs": 4,
        "metrics": {
            "precision_lower_bound": {
                "a": _side(0.5, 2, 4),
                "b": _side(1.0, 2, 2),
                "diff": 0.5,
                "ci": [-0.05, 0.25],
                "note": None,
            },
            "blocking_recall": {
                "a": _side(None, 0, 0),
                "b": _side(0.5, 1, 2),
                "diff": None,
                "ci": None,
                "note": None,
            },
            "false_block_rate": {
                "a": _side(0.1, 1, 10),
                "b": _side(0.05, 1, 20),
                "diff": -0.05,
                "ci": None,
                "note": "без CI",
            },
            "cost_usd_mean_per_case": {
                "a": _side(1.0, 4.0, 4),
                "b": _side(1.2, 4.800, 4),
                "diff": 0.2,
                "ci": [0.1, 0.3],
                "note": None,
            },
        },
    }

    text = render_compare(comparison)

    expected = (
        "# Сравнение вариантов\n"
        "\n"
        "n_common_cases: 4, n_pairs: 4\n"
        "\n"
        "| метрика | a | b | diff | CI | note |\n"
        "|---|---|---|---|---|---|\n"
        "| precision_lower_bound | 0.500 (2/4) | 1.000 (2/2) | +0.500 | [-0.050, 0.250] | — |\n"
        "| blocking_recall | — (0/0) | 0.500 (1/2) | n/a | — | — |\n"
        "| false_block_rate | 0.100 (1/10) | 0.050 (1/20) | -0.050 | — | без CI |\n"
        "| cost_usd_mean_per_case | 1.000 (4/4) | 1.200 (4.800/4) | +0.200 | [0.100, 0.300] | — |\n"
    )
    assert text == expected


def test_render_compare_unknown_metric_appended_alphabetically() -> None:
    """Ключ вне `COMPARE_METRICS` не отбрасывается — идёт следом по алфавиту."""
    comparison = {
        "n_common_cases": 1,
        "n_pairs": 1,
        "metrics": {
            "false_block_rate": {
                "a": _side(0.0, 0, 1),
                "b": _side(0.0, 0, 1),
                "diff": 0.0,
                "ci": None,
                "note": None,
            },
            "zzz_extra": {
                "a": _side(1.0, 1, 1),
                "b": _side(1.0, 1, 1),
                "diff": 0.0,
                "ci": None,
                "note": None,
            },
        },
    }
    text = render_compare(comparison)
    rows = [line for line in text.splitlines() if line.startswith("| ")][1:]  # без шапки
    names = [row.split(" | ")[0].lstrip("| ") for row in rows]
    assert names == ["false_block_rate", "zzz_extra"]


def test_manifest_dataclass_and_mapping_are_equivalent() -> None:
    """`_manifest_map` (через `render_report`) даёт тот же результат для
    датакласса `RunManifest` и для эквивалентного `Mapping`."""
    manifest = RunManifest(
        run_id="r",
        kit={"commit": "1" * 40},
        tools={},
        variants=[{"label": "v", "harness": "h", "model": "m", "requested_effort": None}],
        corpus_digest="d",
        matcher_version=1,
        matcher_rules_digest="sha256:" + "9" * 64,
        started="s",
        finished="f",
        jobs=1,
        repetitions=1,
    )
    case = make_case(defects=[])
    ev = build_eval(case, findings=())
    metrics_by_variant = {"v": metrics_for_variant([ev])}
    evals_by_variant = {"v": [ev]}

    from_dataclass = render_report(metrics_by_variant, evals_by_variant, manifest)
    from_mapping = render_report(metrics_by_variant, evals_by_variant, dataclasses.asdict(manifest))

    assert from_dataclass == from_mapping
