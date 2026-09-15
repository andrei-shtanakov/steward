"""Тесты steward.review_eval.matcher: рёбра, mutual-best, остаток, очередь.

Таблица §12 дизайна целиком: перефразированная находка → тот же дефект;
другой файл / строка вне окна → нет ребра; `line: 0` — указатель уровня файла;
две находки на один дефект → TP + duplicate; равные веса → очередь, а не
назначение; цепочка предпочтений разрешается итерацией; prediction с рёбрами и
к назначенному, и к неназначенному дефекту → очередь; non_defect → known_fp;
инвариантность к перестановке предсказаний, дефектов и non_defects на всех
перестановках малых входов; `rules_digest` проверяется по форме, стабильности
и реакции на смену правил — константой он не закрепляется намеренно (хэшируются
исходники, и константа превратилась бы в ритуал обновления).

Дизайн: docs/superpowers/specs/2026-09-14-review-eval-harness-design.md §8.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from typing import Any

import pytest

from steward.review_eval.corpus import Defect, Match, NonDefect
from steward.review_eval.matcher import (
    MATCHER_VERSION,
    Component,
    Edge,
    MatchResult,
    Prediction,
    assign,
    build_edges,
    match,
    normalize_path,
    rules_digest,
)


def make_finding(
    *,
    file: str = "src/a.py",
    line: object = 10,
    title: str = "",
    scenario: str = "",
    expected_result: str = "",
    evidence: object = None,
    kind: str = "defect",
    **extra: Any,
) -> dict[str, Any]:
    """Находка вердикта: только поля, которые читает матчер, плюс `extra`."""
    finding: dict[str, Any] = {
        "kind": kind,
        "file": file,
        "line": line,
        "title": title,
        "scenario": scenario,
        "expected_result": expected_result,
        "evidence": [] if evidence is None else evidence,
    }
    finding.update(extra)
    return finding


def make_defect(
    defect_id: str,
    *,
    file: str = "src/a.py",
    line_hint: int = 10,
    line_window: int = 5,
    keywords_any: tuple[str, ...] = ("PATH",),
    evidence: tuple[str, ...] = (),
    files: tuple[str, ...] | None = None,
    kind: str = "defect",
) -> Defect:
    """Gold-дефект с `match`-правилом."""
    return Defect(
        id=defect_id,
        severity="major",
        file=file,
        line_hint=line_hint,
        scenario="gold scenario",
        evidence=evidence,
        match=Match(
            files=(file,) if files is None else files,
            line_window=line_window,
            keywords_any=keywords_any,
        ),
        kind=kind,
    )


def make_non_defect(
    non_defect_id: str, *, file: str = "src/a.py", line_hint: int = 10
) -> NonDefect:
    """Исторически ложная находка с тем же `match`-правилом."""
    return NonDefect(
        id=non_defect_id,
        file=file,
        line_hint=line_hint,
        scenario="known false positive",
        match=Match(files=(file,), line_window=5, keywords_any=("PATH",)),
    )


def preds(*findings: Mapping[str, Any]) -> list[Prediction]:
    """Предсказания с индексами по позиции — как в ``verdict.json.findings``."""
    return [Prediction(index=i, finding=f) for i, f in enumerate(findings)]


# --- нормализация путей -----------------------------------------------------


def test_normalize_path_strips_dot_slash_collapses_and_uses_posix() -> None:
    assert normalize_path("./src/a.py") == "src/a.py"
    assert normalize_path("src//nested///a.py") == "src/nested/a.py"
    assert normalize_path("src\\a.py") == "src/a.py"
    assert normalize_path("././src/a.py") == "src/a.py"
    assert normalize_path("src/a.py") == "src/a.py"


def test_edge_survives_path_spelling_differences() -> None:
    """Расхождение в написании пути — не повод потерять находку."""
    prediction = preds(make_finding(file="./src//a.py", title="PATH hijack"))
    result = match(prediction, [make_defect("d1", file="src/a.py")], [])
    assert result.assigned == {0: "d1"}


# --- предикат ребра ---------------------------------------------------------


def test_rephrased_finding_matches_by_keyword_in_scenario() -> None:
    """Ключевое слово в `scenario`, а не в `title` — дефект тот же (D7)."""
    prediction = preds(
        make_finding(title="подмена окружения", scenario="переменная path собирается из kit_dir")
    )
    defect = make_defect("d1", keywords_any=("PATH", "hijack"))
    result = match(prediction, [defect], [])
    assert result.assigned == {0: "d1"}
    assert result.unmatched_defects == ()


def test_keyword_in_expected_result_matches() -> None:
    prediction = preds(make_finding(expected_result="kit_dir must not shadow system tools"))
    result = match(prediction, [make_defect("d1", keywords_any=("kit_dir",))], [])
    assert result.assigned == {0: "d1"}


def test_different_file_gives_no_edge() -> None:
    prediction = preds(make_finding(file="src/other.py", title="PATH hijack"))
    result = match(prediction, [make_defect("d1", file="src/a.py")], [])
    assert result.assigned == {}
    assert result.unlabeled == (0,)
    assert result.unmatched_defects == ("d1",)


def test_line_outside_window_gives_no_edge() -> None:
    prediction = preds(make_finding(line=100, title="PATH hijack"))
    result = match(prediction, [make_defect("d1", line_hint=10, line_window=5)], [])
    assert result.unlabeled == (0,)
    assert result.unmatched_defects == ("d1",)


def test_line_on_window_boundary_gives_edge() -> None:
    prediction = preds(make_finding(line=15, title="PATH hijack"))
    result = match(prediction, [make_defect("d1", line_hint=10, line_window=5)], [])
    assert result.assigned == {0: "d1"}


def test_no_keyword_hit_gives_no_edge() -> None:
    prediction = preds(make_finding(title="unrelated wording"))
    result = match(prediction, [make_defect("d1", keywords_any=("PATH",))], [])
    assert result.unlabeled == (0,)


@pytest.mark.parametrize("keyword", ["", " ", "\t\n"], ids=["empty", "space", "tabs"])
def test_blank_keyword_gives_no_edge(keyword: str) -> None:
    """Пустое ключевое слово не матчит ничего, хотя `"" in haystack` истинно.

    Схема корпуса такой `keywords_any` не пропускает — это вторая линия
    обороны: `Defect` собирают и в коде, а ребро «по пустому слову» сделало бы
    дефект находимым любой находкой в его файле и окне, то есть завысило бы
    recall молча.
    """
    prediction = preds(make_finding(title="совсем другая формулировка"))
    result = match(prediction, [make_defect("d1", keywords_any=(keyword,))], [])
    assert result.unlabeled == (0,)
    assert result.assigned == {}


def test_keyword_with_surrounding_whitespace_still_matches() -> None:
    """`" PATH "` — валидный keyword корпуса (непробельный), и матчится как `PATH`.

    Нормализация объявлена как strip+casefold; проверка вхождения без strip
    искала `" path "` в стоге и теряла ребро — FP плюс пропуск на одной паре.
    """
    prediction = preds(make_finding(title="PATH hijack"))
    padded = match(prediction, [make_defect("d1", keywords_any=(" PATH ",))], [])
    plain = match(prediction, [make_defect("d1", keywords_any=("PATH",))], [])
    assert padded.assigned == plain.assigned == {0: "d1"}


def test_blank_keyword_does_not_add_weight() -> None:
    """Пустое слово рядом с настоящим не считается попаданием: вес не растёт."""
    prediction = preds(make_finding(title="PATH hijack"))
    real = build_edges(prediction, [make_defect("d1", keywords_any=("PATH",))])
    padded = build_edges(prediction, [make_defect("d1", keywords_any=("PATH", "", "   "))])
    assert [edge.weight for edge in padded] == [edge.weight for edge in real]


def test_file_missing_prediction_matches_file_missing_gold() -> None:
    """Верное «файла нет» становится TP — если gold это и утверждает.

    Правило «file-missing не назначается строчным дефектам» осталось, но без
    gold-стороны оно делало верную находку невозможной: она уходила в FP, а
    дефект — в пропуски, то есть один и тот же факт наказывался дважды.
    """
    prediction = preds(make_finding(kind="file-missing", line=0, title="PATH hijack"))
    gold = make_defect("d1", line_hint=0, kind="file-missing")

    result = match(prediction, [gold], [])

    assert result.assigned == {0: "d1"}
    assert result.unlabeled == ()
    assert result.unmatched_defects == ()


def test_file_missing_pair_scores_as_exact() -> None:
    """У пары file-missing расстояния нет: штраф 0, а не «как у указателя файла».

    Обе стороны говорят о файле целиком, и штрафовать их за неназванную строку
    (как строчную находку с `line: 0`) значило бы наказывать за отсутствие
    того, чего в этом утверждении не бывает.
    """
    prediction = preds(make_finding(kind="file-missing", line=0, title="PATH hijack"))
    gold = make_defect("d1", line_hint=0, kind="file-missing", line_window=5)

    edges = build_edges(prediction, [gold])

    assert [edge.weight for edge in edges] == [(1, 0, 0)]


def test_defect_prediction_gets_no_edge_to_file_missing_gold() -> None:
    """И наоборот: строчная находка не назначается gold-у «файла нет»."""
    prediction = preds(make_finding(title="PATH hijack"))
    gold = make_defect("d1", line_hint=0, kind="file-missing")

    result = match(prediction, [gold], [])

    assert result.assigned == {}
    assert result.unlabeled == (0,)
    assert result.unmatched_defects == ("d1",)


def test_file_missing_prediction_matches_a_file_missing_non_defect() -> None:
    """Ложное «файла нет», размеченное non-defect, уходит в `known_fp`."""
    prediction = preds(make_finding(kind="file-missing", line=0, title="PATH hijack"))
    non_defect = NonDefect(
        id="nf1",
        file="src/a.py",
        line_hint=0,
        scenario="известная ложная тревога",
        match=Match(files=("src/a.py",), line_window=5, keywords_any=("PATH",)),
        kind="file-missing",
    )

    result = match(prediction, [], [non_defect])

    assert result.known_fp == {0: "nf1"}
    assert result.unlabeled == ()


def test_file_missing_finding_gets_no_edges() -> None:
    """`kind: file-missing` не назначается строчному gold-дефекту (§8 шаг 1).

    Утверждение «файла нет» и дефект **в строке** этого файла — разные
    утверждения: они не могут быть одним дефектом, даже когда путь и ключевые
    слова совпали. Такое предсказание остаётся неразмеченным (FP в
    `precision_lower_bound`), а опровержение по дереву head — дело метрик
    (`evaluate_case` через `file_lines`), не матчера.
    """
    prediction = preds(make_finding(kind="file-missing", title="PATH hijack"))

    result = match(prediction, [make_defect("d1", keywords_any=("PATH",))], [])

    assert result.assigned == {}
    assert result.unlabeled == (0,)
    assert result.unmatched_defects == ("d1",)


def test_file_missing_finding_gets_no_edges_even_at_file_level() -> None:
    """И с `line: 0`: указатель уровня файла не делает утверждения одинаковыми."""
    prediction = preds(make_finding(kind="file-missing", line=0, title="PATH hijack"))

    result = match(prediction, [make_defect("d1", keywords_any=("PATH",))], [])

    assert result.assigned == {}
    assert result.unlabeled == (0,)


def test_file_missing_does_not_shadow_a_real_finding() -> None:
    """Рядом с настоящей находкой правило ничего не ломает: назначается она."""
    prediction = preds(
        make_finding(kind="file-missing", title="PATH hijack"),
        make_finding(title="PATH hijack"),
    )

    result = match(prediction, [make_defect("d1", keywords_any=("PATH",))], [])

    assert result.assigned == {1: "d1"}
    assert result.unlabeled == (0,)


def test_keyword_match_is_case_insensitive() -> None:
    prediction = preds(make_finding(title="PaTh подменён"))
    result = match(prediction, [make_defect("d1", keywords_any=("pAtH",))], [])
    assert result.assigned == {0: "d1"}


def test_integral_float_line_matches_like_an_int() -> None:
    """`line: 10.0` — та же строка 10: JSON-число, равное своему floor.

    Схема кита (и её зеркало `threshold`) такое значение принимает, а матчер
    требовал именно `int` — годная находка не получала рёбер и уходила в FP,
    а дефект в пропуски. Определение «что такое номер строки» теперь одно на
    пакет (`threshold.as_line_number`).
    """
    gold = make_defect("d1", line_hint=10)
    as_int = match(preds(make_finding(line=10, title="PATH hijack")), [gold], [])
    as_float = match(preds(make_finding(line=10.0, title="PATH hijack")), [gold], [])

    assert as_float.assigned == {0: "d1"} == as_int.assigned
    assert build_edges(preds(make_finding(line=10.0, title="PATH hijack")), [gold]) == build_edges(
        preds(make_finding(line=10, title="PATH hijack")), [gold]
    )


@pytest.mark.parametrize(
    "line",
    [10.5, True, -1, "10", None],
    ids=["fractional", "boolean", "negative", "string", "missing"],
)
def test_line_outside_the_schema_gives_no_edges(line: object) -> None:
    """Дробное, булево, отрицательное, строковое, отсутствующее — ребра нет."""
    prediction = preds(make_finding(line=line, title="PATH hijack"))

    result = match(prediction, [make_defect("d1", line_hint=10)], [])

    assert result.assigned == {}
    assert result.unlabeled == (0,)


def test_evidence_with_an_integral_float_line_still_matches() -> None:
    """`evidence[].line: 3.0` не мешает: матчер читает из evidence только файлы."""
    prediction = preds(
        make_finding(
            line=10.0,
            title="PATH hijack",
            evidence=[{"file": "src/a.py", "line": 3.0, "reason": "r"}],
        )
    )
    gold = make_defect("d1", line_hint=10, evidence=("src/a.py:3",))

    result = match(prediction, [gold], [])

    assert result.assigned == {0: "d1"}
    # Пересечение файлов evidence посчитано: вес по второй координате — 1.
    assert build_edges(prediction, [gold])[0].weight[1] == 1


def test_non_int_line_and_missing_file_give_no_edges() -> None:
    """Значение вне схемы вердикта не даёт ребра и не роняет матчер."""
    prediction = [
        Prediction(index=0, finding=make_finding(line="10", title="PATH")),
        Prediction(index=1, finding=make_finding(line=True, title="PATH")),
        Prediction(index=2, finding={"title": "PATH", "line": 10}),
    ]
    result = match(prediction, [make_defect("d1")], [])
    assert result.assigned == {}
    assert result.unlabeled == (0, 1, 2)


# --- line: 0 как указатель уровня файла -------------------------------------


def test_line_zero_skips_the_window_and_still_matches() -> None:
    """``line: 0`` — файл без строки (§9), а не строка 0 вне окна."""
    prediction = preds(make_finding(line=0, title="PATH hijack"))
    result = match(prediction, [make_defect("d1", line_hint=644, line_window=5)], [])
    assert result.assigned == {0: "d1"}


def test_line_zero_with_wrong_file_is_still_unlabeled() -> None:
    """Пропуск окна не ослабляет ни файл, ни ключевые слова."""
    prediction = preds(make_finding(line=0, file="src/other.py", title="PATH hijack"))
    result = match(prediction, [make_defect("d1", file="src/a.py")], [])
    assert result.unlabeled == (0,)
    assert result.unmatched_defects == ("d1",)


def test_line_zero_without_keyword_is_still_unlabeled() -> None:
    prediction = preds(make_finding(line=0, title="unrelated wording"))
    result = match(prediction, [make_defect("d1")], [])
    assert result.unlabeled == (0,)


def test_line_zero_penalty_is_worse_than_any_in_window_line() -> None:
    """Штраф −(`line_window` + 1): хуже любой находки внутри окна.

    Находка, назвавшая строку, — сильнее файловой: строка 15 при
    `line_hint` 10 и окне 5 даёт −5, файловая — −6, поэтому TP достаётся
    первой, а файловая становится дубликатом.
    """
    prediction = preds(
        make_finding(line=0, title="PATH"),
        make_finding(line=15, title="PATH"),
    )
    edges = build_edges(prediction, [make_defect("d1", line_hint=10, line_window=5)])
    assert edges == [
        Edge(prediction=0, defect_id="d1", weight=(1, 0, -6)),
        Edge(prediction=1, defect_id="d1", weight=(1, 0, -5)),
    ]
    result = match(prediction, [make_defect("d1", line_hint=10, line_window=5)], [])
    assert result.assigned == {1: "d1"}
    assert result.duplicates == {0: "d1"}


def test_line_zero_penalty_is_better_than_no_edge_at_all() -> None:
    """«Лучше отсутствия ребра»: единственная файловая находка назначается."""
    prediction = preds(make_finding(line=0, title="PATH"))
    result = match(prediction, [make_defect("d1", line_hint=10, line_window=5)], [])
    assert result.assigned == {0: "d1"}
    assert result.unmatched_defects == ()


# --- вес ребра --------------------------------------------------------------


def test_weight_counts_keywords_evidence_overlap_and_line_distance() -> None:
    prediction = preds(
        make_finding(
            line=12,
            title="PATH подмена",
            scenario="kit_dir",
            evidence=[{"file": "./src/a.py", "line": 12, "reason": "…"}],
        )
    )
    defect = make_defect(
        "d1",
        keywords_any=("PATH", "kit_dir", "missing"),
        evidence=("src/a.py:644", "src/b.py:157"),
    )
    assert build_edges(prediction, [defect]) == [
        Edge(prediction=0, defect_id="d1", weight=(2, 1, -2))
    ]


def test_gold_evidence_entry_without_line_is_still_a_file() -> None:
    prediction = preds(
        make_finding(title="PATH", evidence=[{"file": "src/a.py", "line": 1, "reason": "…"}])
    )
    edges = build_edges(prediction, [make_defect("d1", evidence=("src/a.py",))])
    assert edges[0].weight[1] == 1


def test_malformed_finding_evidence_is_ignored_in_overlap() -> None:
    prediction = preds(make_finding(title="PATH", evidence=["src/a.py:1", {"line": 1}]))
    edges = build_edges(prediction, [make_defect("d1", evidence=("src/a.py:1",))])
    assert edges[0].weight[1] == 0


# --- назначение и остаток ---------------------------------------------------


def test_two_findings_on_one_defect_give_one_assigned_and_one_duplicate() -> None:
    """Классификация — по исходному `E0`, хотя ребро дубликата удалено."""
    prediction = preds(
        make_finding(line=10, title="PATH подмена", scenario="kit_dir"),
        make_finding(line=12, title="PATH"),
    )
    defect = make_defect("d1", keywords_any=("PATH", "kit_dir"))
    result = match(prediction, [defect], [])
    assert result.assigned == {0: "d1"}
    assert result.duplicates == {1: "d1"}
    assert result.unlabeled == ()
    assert result.ambiguous == ()
    assert result.unmatched_defects == ()


def test_equal_weights_on_one_defect_go_to_queue_not_assignment() -> None:
    """Ничья с любой стороны блокирует назначение целиком (§8.3)."""
    prediction = preds(
        make_finding(line=10, title="PATH"),
        make_finding(line=10, title="PATH"),
    )
    result = match(prediction, [make_defect("d1")], [])
    assert result.assigned == {}
    assert result.duplicates == {}
    assert result.ambiguous == (
        Component(
            predictions=(0, 1),
            defect_ids=("d1",),
            edges=(
                Edge(prediction=0, defect_id="d1", weight=(1, 0, 0)),
                Edge(prediction=1, defect_id="d1", weight=(1, 0, 0)),
            ),
        ),
    )
    assert result.unmatched_defects == ("d1",)
    assert result.unlabeled == ()


def test_preference_chain_resolves_by_iteration_not_by_queue() -> None:
    """Цепочка предпочтений назначение НЕ блокирует.

    Брифовая прикидка «A→d1 лучший для A, но d1 лучший для B, B лучший для
    d2 → ни одной mutual-пары» неверна для честной неподвижной точки: ребро
    глобального максимума при различных весах всегда взаимно-лучшее, поэтому
    первый круг назначает B→d2, а второй — освободившуюся пару A→d1.
    Неподвижная точка без назначений требует **равного** веса, см. тест
    выше. Результат посчитан руками и закреплён.
    """
    edges = [
        Edge(prediction=0, defect_id="d1", weight=(1, 0, 0)),  # единственное ребро A
        Edge(prediction=1, defect_id="d1", weight=(2, 0, 0)),
        Edge(prediction=1, defect_id="d2", weight=(3, 0, 0)),  # глобальный максимум
    ]
    assigned, residual = assign(edges)
    assert assigned == {1: "d2", 0: "d1"}
    assert residual == []


def test_duplicate_keyword_does_not_inflate_the_weight() -> None:
    """Повтор ключевого слова не даёт второго попадания: считаются уникальные.

    Вес ребра — число попавших ключей, и `["PATH", "path"]` давало двойку там,
    где сказано одно слово: такой дефект побеждал равного конкурента, и
    неоднозначность (двух gold с одинаковым правилом различить нечем)
    превращалась в TP. Схема корпуса повтор теперь отвергает, но `Defect`
    приходит и из кода — здесь вторая линия обороны.
    """
    prediction = preds(make_finding(line=10, title="PATH"))
    gold = [
        make_defect("d1", keywords_any=("PATH",)),
        make_defect("d2", keywords_any=("PATH", "path")),
    ]

    result = match(prediction, gold, [])

    assert result.assigned == {}
    assert result.ambiguous == (
        Component(
            predictions=(0,),
            defect_ids=("d1", "d2"),
            edges=(
                Edge(prediction=0, defect_id="d1", weight=(1, 0, 0)),
                Edge(prediction=0, defect_id="d2", weight=(1, 0, 0)),
            ),
        ),
    )
    assert result.unmatched_defects == ("d1", "d2")


def test_prediction_touching_assigned_and_unassigned_defect_is_ambiguous() -> None:
    """Ребро к неназначенному дефекту переводит prediction в очередь, не в duplicate."""
    prediction = preds(
        make_finding(line=10, title="PATH подмена", scenario="kit_dir"),  # 0 → d1
        make_finding(line=10, title="PATH", file="src/a.py"),  # 1 → d1 и d2
        make_finding(line=10, title="PATH", file="src/b.py"),  # 2 → d2
    )
    d1 = make_defect("d1", file="src/a.py", keywords_any=("PATH", "kit_dir"))
    d2 = make_defect("d2", file="src/a.py", files=("src/a.py", "src/b.py"))
    result = match(prediction, [d1, d2], [])
    assert result.assigned == {0: "d1"}
    assert result.duplicates == {}
    assert result.ambiguous == (
        Component(
            predictions=(1, 2),
            defect_ids=("d2",),
            edges=(
                Edge(prediction=1, defect_id="d2", weight=(1, 0, 0)),
                Edge(prediction=2, defect_id="d2", weight=(1, 0, 0)),
            ),
        ),
    )
    assert result.unmatched_defects == ("d2",)


def test_defect_whose_only_edge_went_to_an_assigned_prediction_is_unmatched() -> None:
    """Такой дефект не найден, но и в очередь не идёт: остаточных рёбер нет."""
    prediction = preds(make_finding(line=10, title="PATH подмена", scenario="kit_dir"))
    d1 = make_defect("d1", keywords_any=("PATH", "kit_dir"))
    d2 = make_defect("d2", keywords_any=("PATH",))
    result = match(prediction, [d1, d2], [])
    assert result.assigned == {0: "d1"}
    assert result.ambiguous == ()
    assert result.unmatched_defects == ("d2",)


def test_assign_returns_only_edges_between_unassigned_vertices() -> None:
    edges = [
        Edge(prediction=0, defect_id="d1", weight=(3, 0, 0)),
        Edge(prediction=1, defect_id="d1", weight=(1, 0, 0)),
        Edge(prediction=1, defect_id="d2", weight=(1, 0, 0)),
        Edge(prediction=2, defect_id="d2", weight=(1, 0, 0)),
    ]
    assigned, residual = assign(edges)
    assert assigned == {0: "d1"}
    assert residual == [
        Edge(prediction=1, defect_id="d2", weight=(1, 0, 0)),
        Edge(prediction=2, defect_id="d2", weight=(1, 0, 0)),
    ]


def _two_assigned_defects_and_one_duplicate(
    *, d1_line_hint: int, duplicate_line: int
) -> MatchResult:
    """Раскладка «два назначенных дефекта + дубликат с рёбрами к обоим».

    `P0` и `P1` привязаны к своему дефекту единственным файлом и весом 2 —
    взаимно-лучшая пара на первом круге. `P2` лежит в общем файле и после
    назначений оказывается дубликатом с двумя рёбрами в `E0`; `d1_line_hint`
    и `duplicate_line` управляют тем, равны ли их веса.
    """
    d1 = make_defect(
        "d1",
        file="src/a.py",
        files=("src/a.py", "src/c.py"),
        line_hint=d1_line_hint,
        keywords_any=("PATH", "kit_dir"),
    )
    d2 = make_defect(
        "d2",
        file="src/a.py",
        files=("src/a.py", "src/b.py"),
        line_hint=10,
        keywords_any=("PATH", "kit_dir"),
    )
    prediction = preds(
        make_finding(line=d1_line_hint, title="PATH kit_dir", file="src/c.py"),
        make_finding(line=10, title="PATH kit_dir", file="src/b.py"),
        make_finding(line=duplicate_line, title="PATH", file="src/a.py"),
    )
    return match(prediction, [d1, d2], [])


def test_duplicate_display_value_prefers_the_best_weight_over_the_id() -> None:
    """Значение `duplicates` — для отображения, но детерминированное.

    Вес побеждает порядок id: ребро к `d2` ближе по строке, поэтому
    отображается `d2`, хотя `d1` лексикографически меньше.
    """
    result = _two_assigned_defects_and_one_duplicate(d1_line_hint=12, duplicate_line=10)
    assert result.assigned == {0: "d1", 1: "d2"}
    assert result.duplicates == {2: "d2"}
    assert result.ambiguous == ()


def test_duplicate_display_value_breaks_weight_ties_by_smallest_id() -> None:
    """При равных весах — меньший id: выбор произволен, но не случаен."""
    result = _two_assigned_defects_and_one_duplicate(d1_line_hint=10, duplicate_line=10)
    assert result.assigned == {0: "d1", 1: "d2"}
    assert result.duplicates == {2: "d1"}


# --- non_defects ------------------------------------------------------------


def test_non_defect_match_is_known_fp() -> None:
    prediction = preds(make_finding(line=10, title="PATH подмена"))
    result = match(prediction, [], [make_non_defect("NF-1")])
    assert result.known_fp == {0: "NF-1"}
    assert result.unlabeled == ()
    assert result.assigned == {}


def test_no_edges_at_all_is_unlabeled() -> None:
    prediction = preds(make_finding(file="src/z.py", title="unrelated"))
    result = match(prediction, [make_defect("d1")], [make_non_defect("NF-1")])
    assert result.unlabeled == (0,)
    assert result.known_fp == {}


def test_defect_wins_over_non_defect_on_the_same_prediction() -> None:
    """Дефекты приоритетнее: `known_fp` считается только по остатку (§8.5)."""
    prediction = preds(make_finding(line=10, title="PATH подмена"))
    result = match(prediction, [make_defect("d1")], [make_non_defect("NF-1")])
    assert result.assigned == {0: "d1"}
    assert result.known_fp == {}


def test_duplicate_is_not_reclassified_as_known_fp() -> None:
    prediction = preds(
        make_finding(line=10, title="PATH подмена", scenario="kit_dir"),
        make_finding(line=12, title="PATH"),
    )
    defect = make_defect("d1", keywords_any=("PATH", "kit_dir"))
    result = match(prediction, [defect], [make_non_defect("NF-1")])
    assert result.duplicates == {1: "d1"}
    assert result.known_fp == {}


def test_best_of_two_non_defects_wins_known_fp() -> None:
    """Между `non_defects` тот же mutual-best: ближе по строке — тот и known_fp."""
    prediction = preds(make_finding(line=10, title="PATH подмена"))
    non_defects = [
        make_non_defect("NF-1", file="src/a.py", line_hint=12),
        make_non_defect("NF-2", file="src/a.py", line_hint=10),
    ]
    result = match(prediction, [], non_defects)
    assert result.known_fp == {0: "NF-2"}
    assert result.unlabeled == ()


def test_ambiguous_non_defect_match_stays_unlabeled() -> None:
    """Ничья против `non_defects` в `known_fp` не переводится — консервативно."""
    prediction = preds(
        make_finding(line=10, title="PATH"),
        make_finding(line=10, title="PATH"),
    )
    result = match(prediction, [], [make_non_defect("NF-1")])
    assert result.known_fp == {}
    assert result.unlabeled == (0, 1)


# --- дисциплина результата --------------------------------------------------


def _scenarios() -> list[tuple[list[Prediction], list[Defect], list[NonDefect]]]:
    """Наборы для property-тестов: ≤ 4 предсказаний × ≤ 4 дефектов."""
    d1 = make_defect("d1", file="src/a.py", keywords_any=("PATH", "kit_dir"))
    d2 = make_defect("d2", file="src/a.py", files=("src/a.py", "src/b.py"))
    d3 = make_defect("d3", file="src/b.py", keywords_any=("PATH",), evidence=("src/b.py:10",))
    d4 = make_defect("d4", file="src/c.py", line_hint=40, line_window=10)
    non_defects = [
        make_non_defect("NF-1", file="src/z.py", line_hint=12),
        make_non_defect("NF-2", file="src/z.py", line_hint=10),
    ]
    findings = [
        make_finding(line=10, title="PATH подмена", scenario="kit_dir"),
        make_finding(line=12, title="PATH"),
        make_finding(line=10, title="PATH", file="src/b.py", evidence=[{"file": "src/b.py"}]),
        make_finding(line=45, title="PATH", file="src/c.py"),
        make_finding(line=10, title="PATH", file="src/z.py"),
        make_finding(line=999, title="unrelated", file="src/nowhere.py"),
        make_finding(line=0, title="PATH", file="src/a.py"),
    ]
    return [
        (preds(*findings[:2]), [d1], non_defects),
        (preds(*findings[:3]), [d1, d2], non_defects),
        (preds(*findings[1:5]), [d1, d2, d3], non_defects),
        (preds(*findings[2:6]), [d2, d3, d4], non_defects),
        (preds(findings[0], findings[0], findings[5]), [d1, d2], non_defects),
        (preds(findings[6], findings[0], findings[4]), [d1, d2], non_defects),
    ]


def _permutations_of(
    predictions: list[Prediction], defects: list[Defect], non_defects: list[NonDefect]
) -> "itertools.product[tuple[Any, ...]]":
    """Все перестановки предсказаний × дефектов × non_defects."""
    return itertools.product(
        itertools.permutations(predictions),
        itertools.permutations(defects),
        itertools.permutations(non_defects),
    )


def test_buckets_are_pairwise_disjoint_on_every_permutation() -> None:
    """`assigned` не пересекается ни с чем; `duplicates ∩ unlabeled = ∅`."""
    for predictions, defects, non_defects in _scenarios():
        for permuted in _permutations_of(predictions, defects, non_defects):
            result = match(*(list(part) for part in permuted))
            buckets = [
                set(result.assigned),
                set(result.duplicates),
                set(result.known_fp),
                set(result.unlabeled),
                {i for c in result.ambiguous for i in c.predictions},
            ]
            seen: set[int] = set()
            for bucket in buckets:
                assert not (bucket & seen), (result, bucket)
                seen |= bucket
            assert seen == {p.index for p in predictions}


def test_result_is_invariant_to_input_permutation() -> None:
    """Порядок вердикта, дефектов и non_defects — только отображение (§8.2).

    Сравниваются и `items()` словарей: равенство `dict` порядок ключей не
    видит, а результат уезжает в отчёт и сравнивается побайтно.
    """
    for predictions, defects, non_defects in _scenarios():
        expected: MatchResult | None = None
        for permuted in _permutations_of(predictions, defects, non_defects):
            result = match(*(list(part) for part in permuted))
            if expected is None:
                expected = result
            assert result == expected
            assert list(result.assigned.items()) == list(expected.assigned.items())
            assert list(result.duplicates.items()) == list(expected.duplicates.items())
            assert list(result.known_fp.items()) == list(expected.known_fp.items())


def test_result_dict_keys_are_sorted() -> None:
    """Ключи всех словарей результата — по возрастанию индекса."""
    for predictions, defects, non_defects in _scenarios():
        result = match(list(reversed(predictions)), list(reversed(defects)), non_defects)
        for mapping in (result.assigned, result.duplicates, result.known_fp):
            assert list(mapping) == sorted(mapping)


def test_duplicate_prediction_index_is_rejected() -> None:
    """Повтор индекса — `ValueError`, а не молча склеенные находки."""
    prediction = [
        Prediction(index=0, finding=make_finding(title="PATH")),
        Prediction(index=0, finding=make_finding(title="PATH", line=12)),
    ]
    with pytest.raises(ValueError, match="duplicate Prediction.index: 0"):
        match(prediction, [make_defect("d1")], [])


def test_assigned_defects_are_unique_and_complement_unmatched() -> None:
    """Один defect получает максимум один TP (D10)."""
    for predictions, defects, non_defects in _scenarios():
        result = match(predictions, defects, non_defects)
        values = list(result.assigned.values())
        assert len(values) == len(set(values))
        assert set(values) | set(result.unmatched_defects) == {d.id for d in defects}
        assert not set(values) & set(result.unmatched_defects)


def test_ambiguous_components_are_sorted_and_defects_are_unassigned() -> None:
    for predictions, defects, non_defects in _scenarios():
        result = match(predictions, defects, non_defects)
        keys = [(c.predictions, c.defect_ids) for c in result.ambiguous]
        assert keys == sorted(keys)
        for component in result.ambiguous:
            assert component.predictions == tuple(sorted(component.predictions))
            assert component.defect_ids == tuple(sorted(component.defect_ids))
            assert not set(component.defect_ids) & set(result.assigned.values())


def test_build_edges_output_is_sorted_and_order_independent() -> None:
    predictions, defects, _ = _scenarios()[2]
    baseline = build_edges(predictions, defects)
    assert baseline == sorted(baseline, key=lambda e: (e.prediction, e.defect_id, e.weight))
    for permuted_defects in itertools.permutations(defects):
        assert build_edges(list(reversed(predictions)), list(permuted_defects)) == baseline


# --- версия и дайджест правил ----------------------------------------------


def test_matcher_version_is_one() -> None:
    assert MATCHER_VERSION == 1


def test_rules_digest_has_the_declared_shape_and_is_stable() -> None:
    """``sha256:<64 hex>``, одинаковый от вызова к вызову (§8.6).

    Значение здесь **не** пинуется константой: дайджест хэширует исходники
    функций матчера, поэтому любая переформулировка докстринга двигала бы
    константу и превращала тест в ритуал её обновления. Роль дайджеста —
    различать несравнимые прогоны в ``run.json``, и проверять надо именно
    это свойство: форму, стабильность и реакцию на смену правил.
    """
    digest = rules_digest()
    prefix, _, hex_part = digest.partition(":")
    assert prefix == "sha256"
    assert len(hex_part) == 64
    assert set(hex_part) <= set("0123456789abcdef")
    assert digest == rules_digest()


def test_rules_digest_tracks_the_source_of_a_rule_callable(monkeypatch: Any) -> None:
    """Правка кода правила меняет дайджест сама, без правки `RULES`.

    Именно это и есть смысл механической привязки: проза `RULES` и
    реализация расходятся молча, а различитель прогонов расходиться не
    имеет права. Здесь подменён сам источник исходников, чтобы не
    переписывать модуль на ходу.
    """
    import steward.review_eval.matcher as matcher_module

    before = matcher_module.rules_digest()
    patched = dict(matcher_module._rule_sources())
    patched["_edge"] = patched["_edge"] + "\n# изменённое правило\n"
    monkeypatch.setattr(matcher_module, "_rule_sources", lambda: patched)
    assert matcher_module.rules_digest() != before


def test_rules_digest_covers_every_matcher_function() -> None:
    """Список исходников собирается из модуля, а не перечисляется руками."""
    import steward.review_eval.matcher as matcher_module

    sources = matcher_module._rule_sources()
    assert {"normalize_path", "_edge", "_gold_evidence_files", "_strictly_best"} <= set(sources)
    assert "rules_digest" not in sources
    assert "_rule_sources" not in sources
    assert "dataclass" not in sources
    for name, source in sources.items():
        assert source.lstrip().startswith("def "), name


def test_rules_digest_covers_imported_rule_callables() -> None:
    """Правило, живущее в другом модуле, тоже под дайджестом.

    `as_line_number` переехал в `threshold` и стал определением «что такое
    номер строки» для матчинга. Фильтр по `__module__` выбрасывал его из
    дайджеста: правку правила `line` различитель прогонов не заметил бы, хотя
    она меняет, какие находки вообще получают рёбра.
    """
    import steward.review_eval.matcher as matcher_module

    assert "as_line_number" in matcher_module._rule_sources()


def test_rules_digest_tracks_the_source_of_an_imported_rule(monkeypatch: Any) -> None:
    """Смена реализации `as_line_number` двигает дайджест (как и своих функций)."""
    import steward.review_eval.matcher as matcher_module

    before = matcher_module.rules_digest()

    def other_line_number(value: object) -> int | None:
        """Другая реализация того же правила."""
        return None

    monkeypatch.setattr(matcher_module, "as_line_number", other_line_number)
    assert matcher_module.rules_digest() != before


def test_rules_digest_tracks_the_weight_declaration(monkeypatch: Any) -> None:
    """Дайджест обязан реагировать и на правку декларации правил."""
    import steward.review_eval.matcher as matcher_module

    before = matcher_module.rules_digest()
    patched = dict(matcher_module.RULES)
    patched["weight"] = ["keyword hits only"]
    monkeypatch.setattr(matcher_module, "RULES", patched)
    assert matcher_module.rules_digest() != before


def test_rules_digest_tracks_the_matcher_version(monkeypatch: Any) -> None:
    """Бамп версии виден в дайджесте — прогоны разных версий различимы."""
    import steward.review_eval.matcher as matcher_module

    before = matcher_module.rules_digest()
    monkeypatch.setattr(matcher_module, "MATCHER_VERSION", MATCHER_VERSION + 1)
    assert matcher_module.rules_digest() != before
