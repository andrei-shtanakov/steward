"""Артефакты отчёта review-eval: ``metrics.json``, ``report.md``,
``adjudication-queue.md`` (дизайн §10; §9 про статусы и знаменатели, §8
шаги 3/5 про содержимое очереди).

Три файла — три разных читателя:

- ``metrics.json`` — машинный, канонический (``sort_keys``): вход для
  дальнейшей обработки (``review-eval compare`` читает его же форму).
- ``report.md`` — человек, который должен **сначала** увидеть, что метрика
  отсутствует или прогон не измерен, и только потом — числа: статус-строка
  печатается раньше таблиц (D9 — precision не публикуется при открытой
  очереди, и отчёт обязан сказать это словами, а не молчанием).
- ``adjudication-queue.md`` — человек, который разбирает очередь: у него
  под рукой должно быть всё, что нужно для решения, включая **весь**
  пересчитанный набор рёбер неоднозначной компоненты (не только остаточные
  рёбра `Component.edges`), иначе не видно, почему совпадение не было
  строго лучшим.

Ничего не считается заново: этот модуль только форматирует то, что уже
посчитано `metrics.metrics_for_variant`/`metrics.compare` и разобрано
`metrics.evaluate_case`.

Дизайн: docs/superpowers/specs/2026-09-14-review-eval-harness-design.md §8, §9, §10.
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from steward.review_eval.corpus import Case
from steward.review_eval.matcher import Prediction, build_edges
from steward.review_eval.metrics import (
    COMPARE_METRICS,
    CaseEval,
    queue_counts,
    unlabeled_predictions,
)
from steward.review_eval.runner import RunManifest
from steward.review_eval.threshold import is_blocking


class ReportError(RuntimeError):
    """Отчёт не может быть записан: путь выводит запись за пределы каталога прогона."""


def _check_no_symlink_on_path(root: Path, relative: str) -> None:
    """Отказать, если `root/relative` (или что-то по пути к нему) — симлинк."""
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ReportError(
                f"{current}: символическая ссылка на пути отчёта — запись только "
                "внутри каталога прогона, по ссылкам отчёт не пишется"
            )


def check_writable(run_dir: Path, name: str) -> None:
    """Отказать, если `run_dir/name` или его temp-путь — симлинк.

    Ровно проверка, которую `write_inside` делает перед записью, вынесенная
    отдельно: комплект из трёх артефактов отчёта (`metrics.json`, `report.md`,
    `adjudication-queue.md`) пишется тремя раздельными вызовами `write_inside`,
    и отказ второго или третьего писателя на симлинке, встреченном уже
    ПОСЛЕ первого `os.replace`, оставлял каталог прогона с артефактами от
    РАЗНЫХ пересчётов: `metrics.json` уже новый, `report.md` — ещё старый
    (ревью-находка части 3, minor). Вызывающий обязан проверить **все** пути
    комплекта этой функцией, прежде чем звать `write_inside` для любого из
    них — тогда симлинк-препятствие отказывает раньше первой записи.

    **Проверяется и `.{name}.tmp`, не только `name`.** Первая версия этой
    функции проверяла только конечную цель — симлинк на месте
    детерминированного temp-файла `write_inside` (тот, через который идёт
    сама запись, `os.open(..., O_CREAT|O_EXCL)`) проходил незамеченным, и
    отказ на нём случался уже ПОСЛЕ `os.replace` предыдущего артефакта — то
    самое смешанное состояние, которое эта функция должна предотвращать
    (ревью-находка части 3, minor: пред-проверка предыдущего раунда сама не
    покрывала temp-путь).

    **Не гарантия «ничего не тронуто» на любой отказ, только на симлинк.**
    Каталог (не файл) или неудаляемый обычный файл на месте `.tmp`, ENOSPC
    посреди `handle.write`/`os.replace` — эта функция такое не ловит вовсе
    (проверяется только `is_symlink`), и второй/третий писатель по-прежнему
    может отказать уже после того, как первый заменил свой артефакт (ревью-
    находка части 3, minor: комментарий предыдущего раунда обещал больше,
    чем эта проверка на деле даёт). Полная атомарность комплекта из трёх
    независимых `os.replace` потребовала бы двухфазной записи (staging-
    каталог + один атомарный коммит) — вне масштаба точечной пред-проверки;
    здесь закрыт конкретный, воспроизводимый класс (симлинк), не весь класс
    возможных отказов файловой системы. Не панацея и от TOCTOU (симлинк
    может появиться после проверки, до записи) — тот же остаточный риск
    несёт и сам `write_inside`.
    """
    root = Path(os.path.normpath(os.path.abspath(run_dir)))
    _check_no_symlink_on_path(root, name)
    _check_no_symlink_on_path(root, f".{name}.tmp")


def write_inside(run_dir: Path, name: str, text: str) -> Path:
    """Записать `run_dir/name` так, чтобы запись не покинула `run_dir`.

    Ни один компонент **внутри** `run_dir` до цели не симлинк, и сам файл не
    симлинк (`check_writable`): `write_text` по ссылке пишет в её цель, и
    подготовленный каталог прогона портил бы произвольный файл вне него.
    `run_dir` **сам** симлинком быть вправе — та же политика, что у
    `runner._require_no_symlinks` (проверяет только `tail.parts`, компоненты
    пути относительно `root`, никогда сам `root`): `run_all` резолвит
    `--out`-симлинк и работает с целью, и раньше `write_inside` требовал
    строже, чем сам раннер — оплаченный `run` с `--out`-симлинком отрабатывал
    целиком, а последующий `_report` отказывал кодом 2 на ровно том же пути,
    который раннер уже принял (ревью-находка части 3, minor). Запись
    атомарна: временный файл рядом, созданный эксклюзивно, затем
    `os.replace`. Обычный (не symlink) temp-файл, оставшийся от прогона,
    прерванного между созданием temp-файла и `os.replace`, снимается и
    попытка создания повторяется один раз — иначе то же детерминированное
    имя отказывало бы навсегда после любого сбоя посередине записи.
    """
    check_writable(run_dir, name)
    root = Path(os.path.normpath(os.path.abspath(run_dir)))
    path = root / name
    tmp = root / f".{name}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(tmp, flags, 0o644)
    except FileExistsError:
        # Имя временного файла детерминировано (по `name`), поэтому обычный
        # regular-файл, оставшийся после прежнего прогона, прерванного между
        # `os.open` и `os.replace`, отказывал бы здесь вечно — до ручной
        # уборки. `O_EXCL` даёт EEXIST по самому факту существования пути —
        # для symlink на месте `tmp` тоже (в т.ч. висящего), цель значения не
        # имеет. Убрать можно только regular-файл: symlink на месте `tmp` —
        # тот же периметр, что и остальной `write_inside`, его не трогаем.
        if tmp.is_symlink():
            raise ReportError(
                f"{tmp}: временный файл отчёта — символическая ссылка, не трогаем"
            ) from None
        try:
            tmp.unlink()
        except OSError as exc:
            # Не снимается — чужой uid, sticky-бит каталога, права. Это
            # препятствие в каталоге прогона (конфигурация, код 2), а не
            # дефект review-eval: необёрнутый `OSError` уходил бы мимо
            # `except ReportError` в `_report` и ловился бы только `_guarded`
            # как «internal error» кодом 3 — уже ПОСЛЕ того, как соседний
            # writer успел заменить свой артефакт (ревью-находка части 3,
            # minor).
            raise ReportError(
                f"{tmp}: не удалось снять оставшийся временный файл отчёта: {exc}"
            ) from exc
        try:
            descriptor = os.open(tmp, flags, 0o644)
        except OSError as exc:
            raise ReportError(f"{tmp}: не удалось создать временный файл отчёта: {exc}") from exc
    except OSError as exc:
        raise ReportError(f"{tmp}: не удалось создать временный файл отчёта: {exc}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    finally:
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink(missing_ok=True)
    return path


__all__ = [
    "check_writable",
    "render_compare",
    "render_queue",
    "render_report",
    "status_text",
    "write_metrics_json",
    "write_queue",
    "write_report",
]

QUALITY_METRIC_ORDER: tuple[str, ...] = (
    "precision_lower_bound",
    "precision",
    "blocking_recall",
    "detection_recall_any_severity",
    "false_block_rate",
    "resolvable_evidence_rate",
)
"""Порядок строк таблицы «Качество» (§9). ``precision`` — не всегда ключ."""

OPERATIONAL_METRIC_ORDER: tuple[str, ...] = (
    "completion_rate",
    "valid_verdict_rate",
    "config_failure_rate",
    "mechanical_failure_rate",
    "unexpected_outcome_count",
    "empty_range_count",
    "duplicate_rate",
    "refuted_file_missing_count",
    "contradicted_gold_count",
)
"""Порядок строк таблицы «Эксплуатация» (§9)."""

COST_FIELD_ORDER: tuple[str, ...] = (
    "cost_usd_total",
    "cost_usd_mean_per_case",
    "cost_available_cases",
    "cost_unavailable_cases",
    "n_runs_with_cost",
    "n_runs_total",
    "partial",
)
"""Порядок строк таблицы «Стоимость» — ключи `metrics._cost` (§9, D6)."""

DURATION_FIELD_ORDER: tuple[str, ...] = (
    "wall_clock_s_mean",
    "wall_clock_s_median",
    "wall_clock_s_p90",
    "provider_duration_ms_mean",
    "n_runs",
    "n_provider_duration_runs",
)
"""Порядок строк таблицы «Длительность» — ключи `metrics._duration` (§9, D5)."""

_STATUS_TEXT: Mapping[str, str] = {
    "ok": "✅ ok",
    "no_gold": "⚠️ no_gold — в варианте нет gold-кейсов: метрики качества не считаются",
    "no_quality_runs": (
        "⚠️ no_quality_runs — нет прогонов, пригодных для метрик качества (нет вердикта "
        "или исход не совпал с ожидаемым): только эксплуатационные метрики"
    ),
    "pending_adjudication": (
        "⚠️ pending_adjudication — precision не публикуется, пока очередь не разобрана"
    ),
}
"""Текст статус-строки — словами, а не кодом (читатель не должен путать
`ok`-выглядящие числа с измеренными, пока статус не `ok`)."""


def status_text(status: str) -> str:
    """Тот же словесный текст статус-строки, что печатает `report.md` (D9).

    Публичная обёртка над `_STATUS_TEXT` — переиспользуется `compare`
    (`cli.py`), чтобы «нет gold-кейсов» / «нет прогонов для качества» не
    выглядели как состоявшееся измерение с нулевыми знаменателями.
    """
    return _STATUS_TEXT.get(status, f"⚠️ {status}")


# ---------------------------------------------------------------------------
# metrics.json
# ---------------------------------------------------------------------------


def write_metrics_json(
    run_dir: Path,
    metrics_by_variant: Mapping[str, Mapping[str, object]],
    *,
    comparisons: Mapping[str, object] | None = None,
    recomputed_with: Mapping[str, object] | None = None,
) -> Path:
    """Пишет ``metrics.json`` — канонический JSON (§10).

    ``sort_keys=True, ensure_ascii=False, indent=2`` и завершающий перевод
    строки: тот же файл, записанный дважды с переставленными по-другому
    входными словарями, даёт побайтно тот же результат.

    `recomputed_with` — провенанс **пересчёта**, если он шёл матчером, отличным
    от матчера прогона (`review-eval metrics --allow-matcher-drift`). Ключ
    присутствует всегда (``null``, когда матчер тот же): потребитель не должен
    разбирать два варианта записи, а «пересчитано другим» обязано быть видно в
    файле, а не только в stderr команды.
    """
    payload: dict[str, object] = {
        "variants": {name: value for name, value in metrics_by_variant.items()},
        "comparisons": dict(comparisons) if comparisons is not None else None,
        "recomputed_with": dict(recomputed_with) if recomputed_with is not None else None,
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    return write_inside(run_dir, "metrics.json", text)


# ---------------------------------------------------------------------------
# report.md
# ---------------------------------------------------------------------------


def render_report(
    metrics_by_variant: Mapping[str, Mapping[str, object]],
    evals_by_variant: Mapping[str, Sequence[CaseEval]],
    manifest: RunManifest | Mapping[str, object],
    *,
    recomputed_with: Mapping[str, object] | None = None,
) -> str:
    """Рендерит ``report.md`` (§10) в фиксированном порядке разделов (§9).

    Статус-строка — сразу после шапки, **до** любой таблицы с числами
    (D9): вариант со статусом, отличным от `ok`, не должен читаться как
    измеренный только потому, что таблица напечатана раньше объяснения.

    Порядок колонок — **из манифеста** (`variants`), а не по алфавиту: это
    порядок, в котором варианты задал автор прогона, и первый `--variant` —
    базис сравнения в `metrics.json`. Вариант, которого в манифесте нет
    (долитый прошлым запуском), идёт следом по алфавиту.
    """
    manifest_map = _manifest_map(manifest)
    variants = _variant_order(manifest_map, (*metrics_by_variant, *evals_by_variant))
    lines: list[str] = []
    lines.extend(_render_header(manifest_map, recomputed_with))
    lines.append("")
    lines.extend(_render_status(metrics_by_variant, variants))
    lines.append("")
    lines.extend(_render_quality_table(metrics_by_variant, variants))
    lines.append("")
    lines.extend(_render_operational_table(metrics_by_variant, variants))
    lines.append("")
    lines.extend(_render_cost_table(metrics_by_variant, variants))
    lines.append("")
    lines.extend(_render_duration_table(metrics_by_variant, variants))
    lines.append("")
    lines.extend(_render_case_table(evals_by_variant, variants))
    lines.append("")
    lines.extend(_render_queue_summary(evals_by_variant))
    return "\n".join(lines) + "\n"


def _variant_order(manifest: Mapping[str, object], names: Sequence[str]) -> list[str]:
    """Варианты в порядке манифеста, затем остальные по алфавиту (без повторов).

    То же правило, по которому `cli._ordered_labels` решает, что с чем
    сравнивать в `metrics.json`: порядок колонок отчёта и базис сравнения не
    должны расходиться.
    """
    known = dict.fromkeys(names)
    ordered: list[str] = []
    records = manifest.get("variants")
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, Mapping):
                continue
            label = record.get("label")
            if isinstance(label, str) and label in known and label not in ordered:
                ordered.append(label)
    ordered += sorted(name for name in known if name not in ordered)
    return ordered


def write_report(
    run_dir: Path,
    metrics_by_variant: Mapping[str, Mapping[str, object]],
    evals_by_variant: Mapping[str, Sequence[CaseEval]],
    manifest: RunManifest | Mapping[str, object],
    *,
    recomputed_with: Mapping[str, object] | None = None,
) -> Path:
    """Рендерит и пишет ``report.md``."""
    return write_inside(
        run_dir,
        "report.md",
        render_report(
            metrics_by_variant, evals_by_variant, manifest, recomputed_with=recomputed_with
        ),
    )


def _manifest_map(manifest: RunManifest | Mapping[str, object]) -> Mapping[str, object]:
    """`run.json` как `Mapping` — принимает и датакласс, и загруженный JSON."""
    if isinstance(manifest, RunManifest):
        return dataclasses.asdict(manifest)
    return manifest


def _render_header(
    manifest: Mapping[str, object], recomputed_with: Mapping[str, object] | None = None
) -> list[str]:
    """Раздел (1): run_id, кит, инструменты, матчер, время, варианты (§10).

    При пересчёте другим матчером печатаются **оба** провенанса: строка
    прогона и строка пересчёта. Одна строка вместо двух соврала бы в любую
    сторону — либо о том, чем прогон шёл, либо о том, чем получены числа.
    """
    lines = ["# Review-eval report", "", "## Прогон", "", f"- run_id: `{manifest.get('run_id')}`"]

    kit = manifest.get("kit")
    if isinstance(kit, Mapping):
        commit = kit.get("commit")
        lines.append(
            f"- kit commit: `{_short12(str(commit))}`"
            if commit is not None
            else "- kit commit: n/a"
        )
        for key in sorted(k for k in kit if k != "commit"):
            lines.append(f"- {key}: `{_short_digest(str(kit[key]))}`")

    tools = manifest.get("tools")
    if isinstance(tools, Mapping):
        for key in sorted(tools):
            lines.append(f"- tool {key}: {tools[key]}")

    lines.append(
        f"- matcher: version={manifest.get('matcher_version')}, "
        f"rules_digest=`{_short_digest(str(manifest.get('matcher_rules_digest')))}`"
    )
    if recomputed_with is not None:
        lines.append(
            f"- пересчитано матчером: version={recomputed_with.get('matcher_version')}, "
            f"rules_digest=`{_short_digest(str(recomputed_with.get('matcher_rules_digest')))}`, "
            f"at {recomputed_with.get('at')}"
        )
    lines.append(f"- started: {manifest.get('started') or 'n/a'}")
    lines.append(f"- finished: {manifest.get('finished') or 'n/a'}")
    lines.append("")
    lines.append("### Варианты")
    lines.append("")

    rows: list[list[str]] = []
    variants = manifest.get("variants")
    if isinstance(variants, list):
        for entry in variants:
            if not isinstance(entry, Mapping):
                continue
            effort = entry.get("requested_effort")
            rows.append(
                [
                    str(entry.get("label", "")),
                    str(entry.get("harness", "")),
                    str(entry.get("model", "")),
                    "—" if effort is None else str(effort),
                ]
            )
    lines.extend(_table(["label", "harness", "model", "effort"], rows))
    return lines


def _render_status(
    metrics_by_variant: Mapping[str, Mapping[str, object]], variants: Sequence[str]
) -> list[str]:
    """Раздел (2): статус-строка на вариант, словами (D9)."""
    lines = ["## Статус", ""]
    for variant in variants:
        summary = metrics_by_variant.get(variant)
        status = summary.get("status") if isinstance(summary, Mapping) else None
        lines.append(f"- **{variant}**: {status_text(str(status))}")
    return lines


def _render_quality_table(
    metrics_by_variant: Mapping[str, Mapping[str, object]], variants: Sequence[str]
) -> list[str]:
    """Раздел (3): метрика | value (num/den) | CI на вариант (§9)."""
    headers = ["метрика"]
    for variant in variants:
        headers.append(variant)
        headers.append(f"{variant} CI")
    rows: list[list[str]] = []
    for name in QUALITY_METRIC_ORDER:
        row = [name]
        for variant in variants:
            summary = metrics_by_variant.get(variant)
            row.append(_metric_cell(_lookup(summary, "metrics", name)))
            row.append(_ci_cell(_lookup(summary, "ci", name)))
        rows.append(row)
    lines = ["## Качество", ""] + _table(headers, rows)
    notes = _quality_notes(metrics_by_variant, variants)
    if notes:
        lines.append("")
        lines.extend(notes)
    return lines


def _quality_notes(
    metrics_by_variant: Mapping[str, Mapping[str, object]], variants: Sequence[str]
) -> list[str]:
    """Сноски к таблице качества: пометки метрик, у которых они есть.

    В ячейке помеченная метрика выглядит как `— (0/0)` — «мерить не на чем», —
    и без пояснения читатель не отличит недоступный источник фактов (§7) от
    пустого знаменателя по любой другой причине.
    """
    return [
        f"- {name} ({variant}): {note}"
        for name in QUALITY_METRIC_ORDER
        for variant in variants
        if (note := _metric_note(_lookup(metrics_by_variant.get(variant), "metrics", name)))
    ]


def _metric_note(entry: object) -> str | None:
    """Непустая пометка метрики или ``None``."""
    if not isinstance(entry, Mapping):
        return None
    note = entry.get("note")
    return note if isinstance(note, str) and note else None


def _render_operational_table(
    metrics_by_variant: Mapping[str, Mapping[str, object]], variants: Sequence[str]
) -> list[str]:
    """Раздел (4): эксплуатационные метрики, без CI."""
    headers = ["метрика", *variants]
    rows: list[list[str]] = []
    for name in OPERATIONAL_METRIC_ORDER:
        row = [name]
        for variant in variants:
            row.append(_metric_cell(_lookup(metrics_by_variant.get(variant), "metrics", name)))
        rows.append(row)
    return ["## Эксплуатация", ""] + _table(headers, rows)


def _render_cost_table(
    metrics_by_variant: Mapping[str, Mapping[str, object]], variants: Sequence[str]
) -> list[str]:
    """Раздел (5): стоимость, единицы из `cost["notes"]`, `partial` (D6)."""
    headers = ["метрика", *variants]
    rows: list[list[str]] = []
    for name in COST_FIELD_ORDER:
        label = _cost_label(metrics_by_variant, variants, name)
        row = [label]
        for variant in variants:
            cost = _section(metrics_by_variant.get(variant), "cost")
            row.append(_cost_value(cost, name))
        rows.append(row)
    return ["## Стоимость", ""] + _table(headers, rows)


def _cost_label(
    metrics_by_variant: Mapping[str, Mapping[str, object]],
    variants: Sequence[str],
    name: str,
) -> str:
    """Имя строки стоимости с единицей измерения, если она объявлена в `notes`."""
    for variant in variants:
        notes = _section(metrics_by_variant.get(variant), "cost").get("notes")
        if isinstance(notes, Mapping) and name in notes:
            return f"{name} ({notes[name]})"
    return name


def _render_duration_table(
    metrics_by_variant: Mapping[str, Mapping[str, object]], variants: Sequence[str]
) -> list[str]:
    """Раздел (6): wall-clock раннера и provider-длительность (D5)."""
    headers = ["метрика", *variants]
    rows: list[list[str]] = []
    for name in DURATION_FIELD_ORDER:
        row = [name]
        for variant in variants:
            duration = _section(metrics_by_variant.get(variant), "duration")
            row.append(_scalar_cell(duration.get(name)))
        rows.append(row)
    return ["## Длительность", ""] + _table(headers, rows)


def _render_case_table(
    evals_by_variant: Mapping[str, Sequence[CaseEval]], variants: Sequence[str]
) -> list[str]:
    """Раздел (7): по кейсу и повторению — исход каждого варианта."""
    rows = _case_rows(evals_by_variant, variants)
    headers = ["case", "rep", "class", "annotation"]
    for variant in variants:
        headers.extend(
            [
                f"{variant}: outcome",
                f"{variant}: exit",
                f"{variant}: reviewer_ran",
                f"{variant}: wall_clock_s",
                f"{variant}: cost",
                f"{variant}: flags",
            ]
        )
    return ["## Кейсы", ""] + _table(headers, rows)


def _case_rows(
    evals_by_variant: Mapping[str, Sequence[CaseEval]], variants: Sequence[str]
) -> list[list[str]]:
    """Строки таблицы кейсов: одна на `(case_id, repetition_id)`, по кейсам всех вариантов."""
    case_by_id: dict[str, Case] = {}
    lookup: dict[tuple[str, str, int], CaseEval] = {}
    for variant, evs in evals_by_variant.items():
        for ev in evs:
            case_by_id.setdefault(ev.case.case_id, ev.case)
            lookup[(variant, ev.case.case_id, ev.result.repetition_id)] = ev

    keys = sorted({(case_id, rep) for _, case_id, rep in lookup})
    rows: list[list[str]] = []
    for case_id, rep in keys:
        case = case_by_id[case_id]
        row = [case_id, str(rep), case.cls, case.annotation.status]
        for variant in variants:
            row.extend(_case_variant_cells(lookup.get((variant, case_id, rep))))
        rows.append(row)
    return rows


def _case_variant_cells(ev: CaseEval | None) -> list[str]:
    """Шесть ячеек одного `(вариант, кейс, повторение)`; `—` — прогона не было."""
    if ev is None:
        return ["—", "—", "—", "—", "—", "—"]
    result = ev.result
    cost = "unavailable"
    if result.cost_status == "available" and isinstance(ev.usage, Mapping):
        value = ev.usage.get("total_cost_usd")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            cost = f"{float(value):.3f}"
    flags = []
    if result.unexpected:
        flags.append("unexpected")
    if result.teardown_error is not None:
        flags.append("teardown_error")
    return [
        result.outcome,
        str(result.exit_code),
        "да" if result.reviewer_ran else "нет",
        f"{result.wall_clock_s:.3f}",
        cost,
        ", ".join(flags) if flags else "-",
    ]


def _render_queue_summary(evals_by_variant: Mapping[str, Sequence[CaseEval]]) -> list[str]:
    """Раздел (8): очередь в двух половинах со ссылкой на `adjudication-queue.md`.

    Одно число здесь врало бы дважды: оно складывало бы прогоны draft-кейсов и
    неблокирующие находки с тем, из-за чего `precision` действительно не
    публикуется, — и «непустая очередь» стояла бы рядом со `status: ok` как
    противоречие. Деление — ровно по `metrics.queue_counts` (тот же предикат
    годности прогона, что у метрик), поэтому гейтящая половина пуста тогда и
    только тогда, когда `precision` опубликован.
    """
    counts = queue_counts(ev for evs in evals_by_variant.values() for ev in evs)
    if counts.total == 0:
        return ["## Очередь адъюдикации", "", "Очередь пуста."]
    return [
        "## Очередь адъюдикации",
        "",
        f"- влияет на precision: предсказаний {counts.gating_predictions}, "
        f"неоднозначных компонент {counts.gating_components}",
        f"- прочее (draft / не блокирующие): предсказаний {counts.other_predictions}, "
        f"неоднозначных компонент {counts.other_components}",
        *(
            [f"- gold противоречит дереву head: дефектов {counts.contradicted_gold}"]
            if counts.contradicted_gold
            else []
        ),
        "",
        "См. `adjudication-queue.md`.",
    ]


# ---------------------------------------------------------------------------
# adjudication-queue.md
# ---------------------------------------------------------------------------


def render_queue(evals_by_variant: Mapping[str, Sequence[CaseEval]]) -> str:
    """Рендерит ``adjudication-queue.md`` (§8 шаги 3/5, §10).

    Детерминированный порядок: варианты и кейсы по возрастанию, находки — по
    позиции в исходном вердикте (§8.2). Пустая очередь — единственная строка
    «Очередь пуста.», без заголовков разделов.
    """
    sections: list[str] = []
    for variant in sorted(evals_by_variant):
        variant_lines = _variant_queue_lines(evals_by_variant[variant])
        if variant_lines:
            sections.append(f"## {variant}")
            sections.append("")
            sections.extend(variant_lines)

    if not sections:
        return "Очередь пуста.\n"
    lines = ["# Adjudication queue", "", *sections]
    return "\n".join(lines).rstrip("\n") + "\n"


def write_queue(run_dir: Path, evals_by_variant: Mapping[str, Sequence[CaseEval]]) -> Path:
    """Рендерит и пишет ``adjudication-queue.md``."""
    return write_inside(run_dir, "adjudication-queue.md", render_queue(evals_by_variant))


def _variant_queue_lines(evs: Sequence[CaseEval]) -> list[str]:
    """Строки очереди одного варианта — по кейсу и повторению, по возрастанию."""
    lines: list[str] = []
    for ev in sorted(evs, key=lambda e: (e.case.case_id, e.result.repetition_id)):
        if ev.match is None:
            continue
        case_lines = _case_queue_lines(ev)
        if not case_lines:
            continue
        # `annotation.status` в заголовке: разбор draft-кейса и gold-кейса —
        # разная работа (draft не входит ни в одну метрику), и это должно быть
        # видно по строке очереди, без сверки с корпусом.
        lines.append(
            f"### {ev.case.case_id} (rep {ev.result.repetition_id}, "
            f"annotation: {ev.case.annotation.status})"
        )
        lines.append("")
        lines.extend(case_lines)
        lines.append("")
    return lines


def _case_queue_lines(ev: CaseEval) -> list[str]:
    """Строки очереди прогона: блокирующие unlabeled, ambiguous, противоречащий
    дереву gold, прочие unlabeled."""
    if ev.match is None:
        raise ValueError(
            f"{ev.case.case_id}: строки очереди запрошены для прогона без матчинга "
            "(исход не 'verdict') — сопоставлять нечего"
        )
    positions = _positions(ev)
    pos_to_finding = dict(zip(positions, ev.findings, strict=True))

    # Опровергнутые деревом head находки «файла нет» в очередь не идут: они
    # размечены фактом, и просить человека разобрать их значило бы дать ему
    # работу, у которой уже есть ответ (§9).
    unlabeled = unlabeled_predictions(ev)
    blocking = sorted(i for i in unlabeled if is_blocking(pos_to_finding[i]))
    non_blocking = sorted(i for i in unlabeled if not is_blocking(pos_to_finding[i]))

    lines: list[str] = []

    if blocking:
        lines.append("#### Блокирующие — неразмеченные")
        lines.append("")
        lines.extend(_finding_line(pos, pos_to_finding[pos]) for pos in blocking)
        lines.append("")

    if ev.match.ambiguous:
        lines.append("#### Неоднозначные компоненты")
        lines.append("")
        preds = tuple(
            Prediction(index=pos, finding=pos_to_finding[pos]) for pos in sorted(pos_to_finding)
        )
        all_edges = build_edges(preds, ev.case.defects)
        for component in ev.match.ambiguous:
            predictions = set(component.predictions)
            defect_ids = set(component.defect_ids)
            edges = sorted(
                (e for e in all_edges if e.prediction in predictions or e.defect_id in defect_ids),
                key=lambda e: (e.prediction, e.defect_id, e.weight),
            )
            lines.append(
                f"- predictions={list(component.predictions)} defects={list(component.defect_ids)}"
            )
            for edge in edges:
                lines.append(
                    f"  - #{edge.prediction} -> {edge.defect_id} weight={list(edge.weight)}"
                )
        lines.append("")

    if ev.contradicted_gold:
        # Отдельный раздел, потому что это другая работа: править **разметку
        # кейса**, а не решать про находку. Gold утверждает «файла нет», дерево
        # head говорит обратное — спорит корпус с материалом, не модель с gold.
        lines.append("#### gold противоречит дереву head")
        lines.append("")
        lines.extend(f"- {gold_id}" for gold_id in ev.contradicted_gold)
        lines.append("")

    if non_blocking:
        lines.append(
            f"<details><summary>Неблокирующие неразмеченные ({len(non_blocking)})</summary>"
        )
        lines.append("")
        lines.extend(_finding_line(pos, pos_to_finding[pos]) for pos in non_blocking)
        lines.append("")
        lines.append("</details>")
        lines.append("")

    return lines


def _positions(ev: CaseEval) -> tuple[int, ...]:
    """Позиции находок в исходном вердикте; пустое поле — позиции по порядку.

    Дублирует `metrics._positions` (модульно-приватную): контракт тот же —
    `finding_positions` пуст, когда он совпадает с индексами `findings`.
    """
    if ev.finding_positions:
        return ev.finding_positions
    return tuple(range(len(ev.findings)))


def _finding_line(pos: int, finding: Mapping[str, object]) -> str:
    """Одна строка находки очереди: позиция, severity/confidence, адрес, текст."""
    file = finding.get("file")
    line = finding.get("line")
    severity = finding.get("severity")
    confidence = finding.get("confidence")
    title = finding.get("title")
    scenario = finding.get("scenario")
    scenario_text = scenario[:200] if isinstance(scenario, str) else ""
    return (
        f"- #{pos} severity={severity} confidence={confidence} "
        f"{file}:{line} — {title!r} — {scenario_text!r}"
    )


# ---------------------------------------------------------------------------
# review-eval compare
# ---------------------------------------------------------------------------


def render_compare(comparison: Mapping[str, object]) -> str:
    """Рендерит markdown-таблицу `review-eval compare` (§9, D12).

    Колонки `a`/`b` — **парные** значения со своим знаменателем
    (`1.000 (2/2)` против `0.500 (2/4)`): вариант, упавший на трудных кейсах,
    иначе выглядел бы лучше по всем метрикам сразу, потому что отвечал только
    на лёгкие. Знаменатель и `n_common_cases` в шапке — то, чем читатель это
    различает; значение по всему варианту лежит в `metrics.json`
    (`variant_value`) и в таблицу вариантов `report.md`.

    Строки — `COMPARE_METRICS` в фиксированном порядке; неизвестные ключи
    (если они когда-нибудь появятся) добавляются следом по алфавиту, а не
    отбрасываются молча.
    """
    metrics = comparison.get("metrics")
    metrics_map = metrics if isinstance(metrics, Mapping) else {}
    order = [name for name in COMPARE_METRICS if name in metrics_map]
    order += sorted(name for name in metrics_map if name not in COMPARE_METRICS)

    rows: list[list[str]] = []
    for name in order:
        entry = metrics_map[name]
        if not isinstance(entry, Mapping):
            continue
        note = entry.get("note")
        rows.append(
            [
                name,
                _side_cell(entry.get("a")),
                _side_cell(entry.get("b")),
                _fmt_diff(_as_float(entry.get("diff"))),
                _ci_cell(entry.get("ci")),
                "—" if note is None else str(note),
            ]
        )

    lines = [
        "# Сравнение вариантов",
        "",
        f"n_common_cases: {comparison.get('n_common_cases')}, n_pairs: {comparison.get('n_pairs')}",
        "",
    ]
    lines.extend(_table(["метрика", "a", "b", "diff", "CI", "note"], rows))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Общие форматтеры
# ---------------------------------------------------------------------------


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """Markdown-таблица: шапка, разделитель, строки — в заданном порядке."""
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _lookup(summary: object, section: str, name: str) -> object:
    """`summary[section][name]` без падения на нестроковой форме."""
    return _section(summary, section).get(name)


def _section(summary: object, section: str) -> Mapping[str, object]:
    """Раздел сводки как `Mapping`; отсутствующий или негодный — пустой."""
    if not isinstance(summary, Mapping):
        return {}
    part = summary.get(section)
    return part if isinstance(part, Mapping) else {}


def _metric_cell(entry: object) -> str:
    """Ячейка метрики: `value (num/den)`, `— (num/den)` при пустом знаменателе,
    `n/a`, когда ключа нет вовсе (D9 — например `precision` при открытой очереди)."""
    if not isinstance(entry, Mapping):
        return "n/a"
    value = entry.get("value")
    num = entry.get("numerator")
    den = entry.get("denominator")
    if value is None:
        return f"— ({num}/{den})"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    return f"{float(value):.3f} ({num}/{den})"


def _ci_cell(ci: object) -> str:
    """`[lo, hi]` с тремя знаками, `—`, если интервал не посчитан/не найден."""
    if not isinstance(ci, (list, tuple)) or len(ci) != 2:
        return "—"
    lo, hi = ci
    if isinstance(lo, bool) or isinstance(hi, bool):
        return "—"
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        return "—"
    return f"[{float(lo):.3f}, {float(hi):.3f}]"


def _cost_value(cost: Mapping[str, object], name: str) -> str:
    """Ячейка таблицы стоимости: булево — словом, число с плавающей — 3 знака."""
    value = cost.get(name)
    if isinstance(value, bool):
        return "да" if value else "нет"
    return _scalar_cell(value)


def _scalar_cell(value: object) -> str:
    """Скалярное значение таблиц длительности/стоимости: `n/a`, число, счёт."""
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _side_cell(side: object) -> str:
    """Ячейка одной стороны сравнения: `value (num/den)` по парной популяции.

    Знаменатель печатается всегда, в том числе при пустом (`— (0/0)`): без него
    «лучшее» значение неотличимо от посчитанного на половине кейсов (§9).
    """
    if not isinstance(side, Mapping):
        return "n/a"
    value = _as_float(side.get("value"))
    fraction = f"({_count_cell(side.get('numerator'))}/{_count_cell(side.get('denominator'))})"
    return f"— {fraction}" if value is None else f"{value:.3f} {fraction}"


def _count_cell(value: object) -> str:
    """Числитель/знаменатель: целое — как счёт, дробное (стоимость) — три знака."""
    number = _as_float(value)
    if number is None:
        return "?"
    return str(int(number)) if number.is_integer() else f"{number:.3f}"


def _fmt_diff(value: float | None) -> str:
    """Разница со знаком (`+`/`-`) и тремя знаками; `n/a` для `None`."""
    return "n/a" if value is None else f"{value:+.3f}"


def _as_float(value: object) -> float | None:
    """`float` для числового значения (`bool` — не число), иначе `None`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _short12(value: str) -> str:
    """Первые 12 символов — короткая форма commit/дайджеста для шапки отчёта."""
    return value[:12]


def _short_digest(value: str) -> str:
    """Короткий дайджест: префикс `sha256:` сохраняется, хвост обрезается до 12 hex."""
    if value.startswith("sha256:"):
        return "sha256:" + value[7:19]
    return _short12(value)
