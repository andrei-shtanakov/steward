"""Матчер review-eval: детерминированное взаимно-однозначное сопоставление
предсказаний ревьюера с gold-дефектами кейса (дизайн §8; D7, D9, D10).

Три шага, каждый со своей ответственностью:

1. **Рёбра-кандидаты** (`build_edges`) — генератор, а не арбитр (D10):
   ребро prediction ↔ defect существует, только если совпал нормализованный
   путь, строка попала в окно ``line_window`` и встретилось хотя бы одно
   ключевое слово. Вес ребра — целочисленный кортеж
   ``(kw_hits, evidence_overlap, -abs(dline))``; плавающей точки нет нигде.
   ``line: 0`` — указатель уровня файла, а не строка: оконная проверка
   пропускается, штраф расстояния ``-(line_window + 1)`` (см. `_edge`).
2. **Назначение** (`assign`) — консервативное взаимно-лучшее (mutual-best) до
   неподвижной точки: пара назначается, только если ребро **строго**
   максимально и для своего prediction, и для своего defect. Порядок
   предсказаний в вердикте и порядок дефектов в кейсе не участвуют в семантике
   никогда — только в отображении; результат инвариантен к перестановке входа.
3. **Классификация остатка** (`match`) — по **исходному** множеству рёбер
   ``E0``, а не по остатку после удаления инцидентных рёбер: иначе дубликат,
   у которого единственное ребро вело к назначенному дефекту, выглядел бы
   неразмеченным.

Что важно про ambiguity: цепочка предпочтений («лучший для A — d1, но лучший
для d1 — B») сама по себе назначение не блокирует — mutual-best просто
итерирует и доназначает на следующем круге. Пока веса различны, ребро
глобального максимума всегда взаимно-лучшее, поэтому неподвижная точка без
назначений требует **равных** весов. Именно ничья (с любой стороны) и
отправляет компоненту в adjudication-queue целиком (§8.3), где prediction
считаются неразмеченными (D9), а defect — ненайденными.

Компонента неоднозначности собирается по **остаточным** рёбрам: обе её
вершины не назначены. Ребро неназначенного prediction к уже назначенному
defect в компоненту не входит (иначе назначенный defect затянуло бы в
очередь), но на классификацию влияет: prediction с рёбрами и к назначенному,
и к неназначенному defect — `ambiguous`, а не `duplicate`.

Дизайн: docs/superpowers/specs/2026-09-14-review-eval-harness-design.md §8.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar

from steward.review_eval.corpus import FILE_MISSING_KIND, Defect, NonDefect
from steward.review_eval.threshold import as_line_number

_Vertex = TypeVar("_Vertex", int, str)

__all__ = [
    "FILE_MISSING_KIND",
    "MATCHER_VERSION",
    "RULES",
    "Component",
    "Edge",
    "MatchResult",
    "Prediction",
    "assign",
    "build_edges",
    "match",
    "normalize_path",
    "rules_digest",
]

#: `kind`, при котором находка сопоставляется **только** с gold того же вида:
#: «файла нет» — утверждение о существовании файла, а не о дефекте в его строке
#: (§8 шаг 1). В предикат блокировки `kind` не входит (порог красит
#: `file-missing` так же, как `defect`) — это правило только про матчинг.
#: Значение то же, что `corpus.FILE_MISSING_KIND` (импортируется оттуда).

MATCHER_VERSION: int = 1

#: Декларация правил матчинга для человека и для `rules_digest`. Дайджест
#: хэширует и её, и исходники функций матчера, поэтому «поправить код, забыв
#: поправить декларацию» дайджест не обманет. Любое семантическое изменение
#: обязано поднимать `MATCHER_VERSION`: отчёты с разными версиями не
#: сравниваются без пометки (§8.6).
RULES: Mapping[str, object] = {
    "matcher_version": MATCHER_VERSION,
    "path_normalisation": [
        "backslash-to-slash",
        "collapse-repeated-slash",
        "strip-leading-dot-slash",
    ],
    "line_number": "JSON number equal to its floor and >= 0 (10.0 == 10; bool rejected)",
    "edge_predicate": [
        f"no edge unless finding.kind == defect.kind ('{FILE_MISSING_KIND}' pairs only "
        f"with '{FILE_MISSING_KIND}' gold, and scores as exact: no line window, penalty 0)",
        "normalized finding.file in normalized defect.match.files",
        "abs(finding.line - defect.line_hint) <= defect.match.line_window, "
        "skipped when finding.line == 0 (file-level pointer)",
        "at least one defect.match.keywords_any occurs casefold-wise in "
        "'title scenario expected_result'",
        "duplicate keywords count once (dedup after strip+casefold)",
    ],
    "gold_evidence_file": "text before the last ':' of each defect.evidence entry",
    "weight": [
        "count of DISTINCT matched keywords_any (strip+casefold)",
        "size of the intersection of normalized evidence file sets",
        "-abs(finding.line - defect.line_hint), or -(line_window + 1) when finding.line == 0",
    ],
    "assignment": "mutual strictly-best edge, iterated to a fixed point",
    "residual": "components over residual edges; classification over the initial edge set",
}


@dataclass(frozen=True)
class Prediction:
    """Одна находка варианта. `index` — позиция в ``verdict.json.findings``.

    Индекс используется как стабильный идентификатор в результате и при
    отображении; в семантику матчинга он не входит (§8.2). Вызывающий обязан
    выдавать индексы уникально.
    """

    index: int
    finding: Mapping[str, object]


@dataclass(frozen=True)
class Edge:
    """Ребро-кандидат prediction ↔ defect с целочисленным весом."""

    prediction: int
    defect_id: str
    weight: tuple[int, int, int]


@dataclass(frozen=True)
class Component:
    """Компонента неоднозначности — целиком в adjudication-queue (§8.3)."""

    predictions: tuple[int, ...]
    defect_ids: tuple[str, ...]
    edges: tuple[Edge, ...]


@dataclass(frozen=True)
class MatchResult:
    """Итог матчинга по одному кейсу.

    `assigned` — TP-кандидаты (prediction → defect); `duplicates` — prediction,
    все рёбра которого ведут к уже назначенным дефектам (значение — только для
    отображения); `known_fp` — совпадение с `non_defects`; `unlabeled` — без
    рёбер к gold вообще (FP только в `precision_lower_bound`, D9); `ambiguous`
    — компоненты в очередь; `unmatched_defects` — всё, что не назначено,
    включая дефекты из компонент очереди.
    """

    assigned: dict[int, str]
    duplicates: dict[int, str]
    known_fp: dict[int, str]
    unlabeled: tuple[int, ...]
    ambiguous: tuple[Component, ...]
    unmatched_defects: tuple[str, ...]


def rules_digest() -> str:
    """``sha256:<hex>`` канонического JSON правил матчинга (§8.6).

    Хэшируется не только декларация (`MATCHER_VERSION` и `RULES`), но и
    **исходники** всех функций, доступных модулю (`_rule_sources`) — своих и
    импортированных, включая `threshold.as_line_number`, которая задаёт
    правило «что такое номер строки». Прозаическое описание правила и его
    реализация расходятся молча, а дайджест обязан отличать несравнимые
    прогоны. Поэтому правка кода правил меняет дайджест сама по себе, без
    ручного обновления `RULES`.

    Дайджест — различитель для машины; `MATCHER_VERSION` — для человека.
    Любое семантическое изменение обязано поднимать и версию: по дайджесту
    видно, что прогоны несравнимы, но не видно, в какую сторону, а отчёты с
    разными версиями не сравниваются без пометки.
    """
    payload = {
        "matcher_version": MATCHER_VERSION,
        "rules": dict(RULES),
        "sources": _rule_sources(),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


#: Импортированные функции, задающие правила матчинга: их исходники входят в
#: дайджест наравне со своими (см. `_rule_sources`). Список явный и по именам —
#: «все импортированные» затянули бы в дайджест стандартную библиотеку.
_IMPORTED_RULE_NAMES: tuple[str, ...] = ("as_line_number",)


def _rule_sources() -> dict[str, str]:
    """Исходники всех функций, определяющих семантику матчинга.

    Список не перечисляется руками, а собирается из пространства имён модуля:
    иначе он разойдётся с кодом при первом же новом хелпере — ровно та тишина,
    от которой дайджест и должен защищать. Исключены только `rules_digest` и
    сам `_rule_sources`: они дайджест вычисляют, а не задают правила.

    **Импортированные правила включены по имени** (`_IMPORTED_RULE_NAMES`):
    «что такое номер строки» живёт в `threshold.as_line_number` и от переезда
    не перестало быть правилом матчинга — его правка меняет, какие находки
    вообще получают рёбра, а фильтр по `__module__` выбрасывал его из
    дайджеста. Список именно явный, а не «все импортированные функции»: иначе в
    дайджест попадал бы, например, `dataclass`, и различитель прогонов зависел
    бы от версии Python.

    Имена резолвятся в момент вызова, а не при импорте: подмена функции в
    модуле (в т.ч. в тесте) обязана двигать дайджест так же, как правка
    исходника.
    """
    module = sys.modules[__name__]
    excluded = {"rules_digest", "_rule_sources"}
    sources = {
        name: inspect.getsource(value)
        for name, value in sorted(vars(module).items())
        if inspect.isfunction(value) and value.__module__ == __name__ and name not in excluded
    }
    for name in _IMPORTED_RULE_NAMES:
        imported = vars(module).get(name)
        if inspect.isfunction(imported):
            sources[name] = inspect.getsource(imported)
    return dict(sorted(sources.items()))


def normalize_path(path: str) -> str:
    """Нормализует путь: ``\\`` → ``/``, повторные ``/`` в один, снятый ``./``.

    Никакого обращения к файловой системе: матчер работает по тексту вердикта
    и кейса, а не по чекауту.
    """
    normalized = path.replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def match(
    preds: Sequence[Prediction],
    defects: Sequence[Defect],
    non_defects: Sequence[NonDefect],
) -> MatchResult:
    """Сопоставляет предсказания с gold кейса и классифицирует остаток (§8).

    Сначала дефекты (они приоритетнее), затем — только для prediction, не
    попавших ни в `assigned`, ни в `duplicates`, ни в очередь — те же правила
    против `non_defects`. Неоднозначное совпадение с `non_defects` в
    `known_fp` не переводится: такой prediction остаётся `unlabeled`.

    Порядок ключей во всех словарях результата — по возрастанию индекса, а не
    по порядку обхода: результат уходит в отчёт и сравнивается побайтно.

    Индексы предсказаний обязаны быть уникальны (контракт вызывающего: это
    позиции в ``verdict.json.findings``). Повтор — `ValueError`: молча
    склеить две находки в одну значило бы исказить и precision, и
    `duplicate_rate`.
    """
    _require_unique_indices(preds)
    initial = build_edges(preds, defects)
    assigned, residual = assign(initial)
    assigned_defects = frozenset(assigned.values())

    by_prediction: dict[int, list[Edge]] = {p.index: [] for p in preds}
    for edge in initial:
        by_prediction[edge.prediction].append(edge)

    ambiguous = _components(residual)
    in_queue = {index for component in ambiguous for index in component.predictions}

    duplicates: dict[int, str] = {}
    without_gold_edges: list[int] = []
    for index, edges in sorted(by_prediction.items()):
        if index in assigned or index in in_queue:
            continue
        if not edges:
            without_gold_edges.append(index)
            continue
        if all(edge.defect_id in assigned_defects for edge in edges):
            duplicates[index] = _display_choice(edges)

    known_fp = _match_non_defects([p for p in preds if p.index in without_gold_edges], non_defects)
    unlabeled = tuple(sorted(i for i in without_gold_edges if i not in known_fp))
    unmatched = tuple(sorted(d.id for d in defects if d.id not in assigned_defects))
    return MatchResult(
        assigned=_by_sorted_key(assigned),
        duplicates=_by_sorted_key(duplicates),
        known_fp=_by_sorted_key(known_fp),
        unlabeled=unlabeled,
        ambiguous=ambiguous,
        unmatched_defects=unmatched,
    )


def build_edges(preds: Sequence[Prediction], defects: Sequence[Defect]) -> list[Edge]:
    """Строит рёбра-кандидаты для всех пар (prediction, defect).

    Отсутствующий или нестроковый ``file``, нецелая ``line`` (``bool`` — не
    целое), пустое пересечение по ключевым словам — ребра нет. Результат
    отсортирован: порядок входа на него не влияет.

    **`kind` обеих сторон обязан совпадать.** Утверждение «файла нет»
    (`file-missing`) и дефект **в строке** этого файла — разные утверждения, и
    одним дефектом они быть не могут, даже когда путь и ключевые слова
    совпали: назначение сделало бы из промаха про существование файла
    попадание в gold. Поэтому находка `file-missing` получает рёбра **только**
    к gold с тем же `kind` (и наоборот), а не ни к чему: иначе верная находка
    «нужного файла нет» не могла стать TP вовсе — она уходила бы в FP, а
    gold-дефект в пропуски, то есть один факт наказывался дважды.

    У пары `file-missing` оконная проверка не применяется и штраф расстояния
    равен 0: обе стороны говорят о файле целиком, и расстояния между ними нет
    (в отличие от строчной находки с ``line: 0``, где строка не названа, но
    существует). Опровержение по дереву head — дело метрик (`evaluate_case`
    через `file_lines`), не матчера: матчер файлов не читает.
    """
    edges: list[Edge] = []
    for pred in preds:
        finding = pred.finding
        file = finding.get("file")
        line = _as_line(finding.get("line"))
        if not isinstance(file, str) or line is None:
            continue
        normalized_file = normalize_path(file)
        haystack = _keyword_haystack(finding)
        evidence_files = _finding_evidence_files(finding)
        pred_kind = _finding_kind(finding)
        for defect in defects:
            edge = _edge(
                pred.index,
                normalized_file,
                line,
                haystack,
                evidence_files,
                defect,
                pred_kind=pred_kind,
            )
            if edge is not None:
                edges.append(edge)
    return sorted(edges, key=_edge_order)


def assign(edges: Sequence[Edge]) -> tuple[dict[int, str], list[Edge]]:
    """Взаимно-лучшее назначение до неподвижной точки (§8.2).

    Возвращает назначения и остаток рёбер — только те, у которых **обе**
    вершины остались неназначенными: рёбра к назначенной вершине удаляются
    вместе с ней. Ничья (несколько рёбер максимального веса) у любой из
    вершин лишает её строго лучшего ребра, поэтому пара не назначается.

    При строгих весах цепочка предпочтений назначение не блокирует: ребро
    глобального максимума всегда строго лучшее для обоих своих концов,
    значит взаимно-лучшая пара существует, а следующий круг доназначает
    освободившиеся. Неподвижная точка без назначений требует **равных**
    весов — именно ничья и отправляет компоненту в очередь (§8.3).

    Порядок ключей назначений — по возрастанию индекса, не по кругу, на
    котором пара нашлась.
    """
    assigned: dict[int, str] = {}
    live = list(edges)
    while True:
        best_by_prediction = _strictly_best(live, lambda e: e.prediction)
        best_by_defect = _strictly_best(live, lambda e: e.defect_id)
        pairs = [
            edge
            for edge in best_by_prediction.values()
            if best_by_defect.get(edge.defect_id) == edge
        ]
        if not pairs:
            return _by_sorted_key(assigned), sorted(live, key=_edge_order)
        taken_predictions = {edge.prediction for edge in pairs}
        taken_defects = {edge.defect_id for edge in pairs}
        for edge in pairs:
            assigned[edge.prediction] = edge.defect_id
        live = [
            edge
            for edge in live
            if edge.prediction not in taken_predictions and edge.defect_id not in taken_defects
        ]


def _match_non_defects(
    preds: Sequence[Prediction], non_defects: Sequence[NonDefect]
) -> dict[int, str]:
    """`known_fp` по тем же правилам: `non_defects` как дефекты без evidence."""
    if not preds or not non_defects:
        return {}
    as_defects = [
        Defect(
            id=nd.id,
            severity="",
            file=nd.file,
            line_hint=nd.line_hint,
            scenario=nd.scenario,
            evidence=(),
            match=nd.match,
            kind=nd.kind,
        )
        for nd in non_defects
    ]
    assigned, _ = assign(build_edges(preds, as_defects))
    return assigned


def _components(edges: Sequence[Edge]) -> tuple[Component, ...]:
    """Компоненты связности остаточного графа, детерминированно отсортированные."""
    parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(vertex: tuple[str, str]) -> tuple[str, str]:
        parent.setdefault(vertex, vertex)
        while parent[vertex] != vertex:
            parent[vertex] = parent[parent[vertex]]
            vertex = parent[vertex]
        return vertex

    def union(left: tuple[str, str], right: tuple[str, str]) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for edge in edges:
        union(("p", str(edge.prediction)), ("d", edge.defect_id))

    grouped: dict[tuple[str, str], list[Edge]] = {}
    for edge in edges:
        grouped.setdefault(find(("p", str(edge.prediction))), []).append(edge)

    components = [
        Component(
            predictions=tuple(sorted({edge.prediction for edge in group})),
            defect_ids=tuple(sorted({edge.defect_id for edge in group})),
            edges=tuple(sorted(group, key=_edge_order)),
        )
        for group in grouped.values()
    ]
    return tuple(sorted(components, key=lambda c: (c.predictions, c.defect_ids)))


def _finding_kind(finding: Mapping[str, object]) -> str:
    """`kind` находки; нестроковое или отсутствующее — «обычный дефект».

    Схему вердикта проверяет порог (`threshold.is_schema_valid_finding`), а
    матчер обязан отвечать на любой вход: неизвестный `kind` трактуется как
    `defect` — тогда находка сопоставляется строчным дефектам, а не
    `file-missing`-gold, и не получает попадания там, где утверждение
    непонятно.
    """
    kind = finding.get("kind")
    return FILE_MISSING_KIND if kind == FILE_MISSING_KIND else "defect"


def _edge(
    index: int,
    normalized_file: str,
    line: int,
    haystack: str,
    evidence_files: frozenset[str],
    defect: Defect,
    *,
    pred_kind: str,
) -> Edge | None:
    """Ребро для одной пары либо ``None``, если предикат не выполнен.

    `kind` сторон обязан совпадать: «файла нет» и «дефект в строке файла» —
    разные утверждения (см. `build_edges`).

    У пары `file-missing` расстояния нет вовсе — штраф 0, окно не проверяется.
    А ``line == 0`` у **строчной** находки — легитимный указатель уровня файла
    (§9), а не строка 0: оконная проверка пропускается, файла и keyword
    достаточно, но штраф равен ``-(line_window + 1)`` — хуже любой находки
    внутри окна (та назовёт строку), лучше отсутствия ребра.
    """
    if pred_kind != defect.kind:
        return None
    if normalized_file not in {normalize_path(f) for f in defect.match.files}:
        return None
    window = defect.match.line_window
    if pred_kind == FILE_MISSING_KIND:
        penalty = 0
    elif line == 0:
        penalty = -(window + 1)
    else:
        delta = abs(line - defect.line_hint)
        if delta > window:
            return None
        penalty = -delta
    # Пустое ключевое слово отбрасывается: `"" in haystack` истинно всегда, и
    # такое «попадание» сделало бы дефект находимым любой находкой в его файле
    # и окне. Схема корпуса пустые слова не пропускает — здесь вторая линия
    # обороны для `Defect`, собранных в коде.
    #
    # Считаются **различные** слова: вес — число попавших ключей, и повтор
    # (`["PATH", "path"]`) давал двойку там, где сказано одно слово. Такой
    # дефект побеждал равного конкурента, то есть неоднозначность превращалась
    # в TP. Схема корпуса повтор теперь отвергает; здесь то же правило для
    # `Defect`, собранных в коде.
    normalized = {kw.strip().casefold() for kw in defect.match.keywords_any if kw.strip()}
    hits = len({kw for kw in normalized if kw in haystack})
    if hits == 0:
        return None
    overlap = len(evidence_files & _gold_evidence_files(defect))
    return Edge(prediction=index, defect_id=defect.id, weight=(hits, overlap, penalty))


def _strictly_best(edges: Sequence[Edge], key: Callable[[Edge], _Vertex]) -> dict[_Vertex, Edge]:
    """Для каждой вершины — её **строго** лучшее ребро, если оно единственно.

    Ничья на максимуме означает отсутствие строго лучшего ребра: вершина
    выпадает из словаря и в этом круге ничего не назначает.
    """
    grouped: dict[_Vertex, list[Edge]] = {}
    for edge in edges:
        grouped.setdefault(key(edge), []).append(edge)
    best: dict[_Vertex, Edge] = {}
    for vertex, group in grouped.items():
        top = max(edge.weight for edge in group)
        candidates = [edge for edge in group if edge.weight == top]
        if len(candidates) == 1:
            best[vertex] = candidates[0]
    return best


def _keyword_haystack(finding: Mapping[str, object]) -> str:
    """``title scenario expected_result`` в casefold; нестроковое поле — пусто."""
    parts = [finding.get("title"), finding.get("scenario"), finding.get("expected_result")]
    return " ".join(part for part in parts if isinstance(part, str)).casefold()


def _finding_evidence_files(finding: Mapping[str, object]) -> frozenset[str]:
    """Нормализованные файлы из ``evidence[]`` находки; пробельные — мимо.

    Пустой путь совпал бы с таким же пустым путём gold-дефекта и дал бы
    `evidence_overlap` там, где ни одна сторона файла не назвала.
    """
    evidence = finding.get("evidence")
    if not isinstance(evidence, list):
        return frozenset()
    return frozenset(
        normalize_path(item["file"])
        for item in evidence
        if isinstance(item, Mapping) and isinstance(item.get("file"), str) and item["file"].strip()
    )


def _gold_evidence_files(defect: Defect) -> frozenset[str]:
    """Файлы из ``defect.evidence``: текст до последнего ``:`` (``path:line``)."""
    files: set[str] = set()
    for entry in defect.evidence:
        head = entry.rsplit(":", 1)[0] if ":" in entry else entry
        if head.strip():
            files.add(normalize_path(head))
    return frozenset(files)


def _display_choice(edges: Sequence[Edge]) -> str:
    """Дефект для отображения дубликата: лучший вес, при ничьей — меньший id."""
    return min(edges, key=lambda e: (tuple(-w for w in e.weight), e.defect_id)).defect_id


def _require_unique_indices(preds: Sequence[Prediction]) -> None:
    """Проверяет уникальность `Prediction.index`; повтор — `ValueError`."""
    seen: set[int] = set()
    for pred in preds:
        if pred.index in seen:
            raise ValueError(f"duplicate Prediction.index: {pred.index}")
        seen.add(pred.index)


def _by_sorted_key(mapping: Mapping[int, str]) -> dict[int, str]:
    """Тот же словарь с ключами по возрастанию — порядок обхода не протекает."""
    return dict(sorted(mapping.items()))


def _edge_order(edge: Edge) -> tuple[int, str, tuple[int, int, int]]:
    """Полный порядок на рёбрах — для стабильного вывода, не для семантики."""
    return (edge.prediction, edge.defect_id, edge.weight)


def _as_line(value: object) -> int | None:
    """Номер строки находки — одно определение с порогом (`as_line_number`).

    Своя проверка требовала именно ``int`` и отвергала ``10.0``, которое кит
    и его зеркало принимают: годная находка не получала рёбер, уходила в FP, а
    gold-дефект — в пропуски. ``bool``, дробное и отрицательное ребра не дают.
    """
    return as_line_number(value)
