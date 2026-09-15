"""Тесты steward.review_eval.threshold: предикат блокировки.

Два уровня проверки (дизайн §9):

- юнит-таблица на ``is_blank``/``is_blocking`` — прямые вызовы предиката;
- контрактный тест против настоящего ``scripts/review/apply-threshold.sh``:
  код выхода скрипта (0/1) обязан совпасть с ``is_blocking`` на каждой строке
  таблицы вердиктов.

Дизайн: docs/superpowers/specs/2026-09-14-review-eval-harness-design.md §9.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from steward.review_eval.threshold import (
    as_line_number,
    is_blank,
    is_blocking,
    is_schema_valid_finding,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "review" / "apply-threshold.sh"


# ---------------------------------------------------------------------------
# is_blank / is_blocking — юнит-таблица
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ("", True),
        ("   ", True),
        ("\t\n ", True),
        ("x", False),
        ("  x  ", False),
        (0, False),
        (0.0, False),
        (False, False),
        ([], False),
        ({}, False),
    ],
    ids=[
        "none",
        "empty-string",
        "spaces",
        "tabs-newlines",
        "non-blank",
        "padded-non-blank",
        "int-zero",
        "float-zero",
        "bool-false",
        "empty-list",
        "empty-dict",
    ],
)
def test_is_blank(value: object, expected: bool) -> None:
    """``is_blank`` матчит jq ``blank``: только ``None``/строка учитываются."""
    assert is_blank(value) is expected


HUGE_LINE = 10**400


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (10, 10),
        (10.0, 10),
        (0, 0),
        (HUGE_LINE, HUGE_LINE),
        (10.5, None),
        (-1, None),
        (-1.0, None),
        (True, None),
        (False, None),
        (float("inf"), None),
        (float("nan"), None),
        ("10", None),
        (None, None),
    ],
    ids=[
        "int",
        "integral-float",
        "zero",
        "huge-int",
        "fractional",
        "negative-int",
        "negative-float",
        "true",
        "false",
        "infinity",
        "nan",
        "string",
        "missing",
    ],
)
def test_as_line_number(value: object, expected: int | None) -> None:
    """Номер строки: целое ≥ 0 либо дробная запись целого; без исключений.

    Огромное целое (``10**400``) — законное JSON-число, равное своему floor:
    `float(value)` на нём бросал `OverflowError`, то есть вердикт с таким
    `line` ронял инструмент вместо ответа «годен/не годен».
    """
    assert as_line_number(value) == expected


def _finding(**overrides: Any) -> dict[str, Any]:
    """Находка, заведомо блокирующая (severity blocker, confidence high, все
    текстовые поля и evidence заполнены), с точечными переопределениями.
    """
    base: dict[str, Any] = {
        "kind": "defect",
        "severity": "blocker",
        "title": "t",
        "file": "a.py",
        "line": 10,
        "scenario": "s",
        "observed_result": "o",
        "expected_result": "e",
        "evidence": [{"file": "a.py", "line": 10, "reason": "r"}],
        "confidence": "high",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    ("finding", "expected"),
    [
        (_finding(), True),
        (_finding(severity="major"), True),
        (_finding(severity="minor"), False),
        (_finding(severity="nit"), False),
        pytest.param({"severity": "blocker", "confidence": "high"}, False, id="missing-fields"),
        (_finding(confidence="medium"), False),
        (_finding(confidence="low"), False),
        (_finding(file="   "), False),
        (_finding(file=None), False),
        (_finding(scenario="\t\n"), False),
        (_finding(observed_result=" "), False),
        (_finding(evidence=[]), False),
        (_finding(evidence="not-a-list"), False),
        (_finding(evidence=[{"file": "a.py", "line": 1, "reason": "  "}]), False),
        (_finding(evidence=[{"file": "  ", "line": 1, "reason": "r"}]), False),
        (_finding(evidence=[{}]), False),
        (_finding(evidence=["x"]), False),
        (_finding(file=0), False),
        (_finding(scenario=[]), False),
        (_finding(observed_result={}), False),
        (_finding(severity=0), False),
        (_finding(confidence=1), False),
        (_finding(evidence=[{"file": 0, "line": 1, "reason": 0}]), False),
        (
            _finding(
                file=0,
                scenario=[],
                observed_result={},
                evidence=[{"file": 0, "line": 1, "reason": 0}],
            ),
            False,
        ),
        (
            _finding(
                evidence=[
                    {"file": "", "line": 1, "reason": "r"},
                    {"file": "b.py", "line": 2, "reason": "ok"},
                ]
            ),
            True,
        ),
        (_finding(kind="file-missing", line=0), True),
        (_finding(expected_result=""), True),
    ],
    ids=[
        "base-blocker",
        "severity-major",
        "severity-minor",
        "severity-nit",
        "missing-fields",
        "confidence-medium",
        "confidence-low",
        "file-blank",
        "file-none",
        "scenario-blank",
        "observed-result-blank",
        "evidence-empty",
        "evidence-not-list",
        "evidence-blank-reason",
        "evidence-blank-file",
        "evidence-item-empty-dict",
        "evidence-item-not-mapping",
        "file-not-a-string",
        "scenario-not-a-string",
        "observed-result-not-a-string",
        "severity-not-a-string",
        "confidence-not-a-string",
        "evidence-item-fields-not-strings",
        "every-text-field-not-a-string",
        "evidence-first-blank-second-valid",
        "kind-file-missing-line-zero",
        "expected-result-blank-unaffected",
    ],
)
def test_is_blocking_unit(finding: dict[str, Any], expected: bool) -> None:
    """Прямые вызовы предиката на изолированных находках.

    Часть строк заведомо вне схемы вердикта (`evidence: ["x"]`, находка без
    обязательных полей): настоящий `apply-threshold.sh` такие вердикты
    отвергает кодом 2 ещё до предиката, поэтому в контрактной таблице ниже их
    нет. Предикат всё равно обязан отвечать `False`, а не падать: он читает и
    негодные вердикты (метрики считаются по сохранённому sidecar, каким бы он
    ни оказался).
    """
    assert is_blocking(finding) is expected


# ---------------------------------------------------------------------------
# Контрактный тест против apply-threshold.sh
# ---------------------------------------------------------------------------


def test_huge_line_is_answered_not_raised() -> None:
    """Предикаты отвечают булевым на огромный `line`, а не падают."""
    finding = _finding(line=HUGE_LINE)

    assert is_schema_valid_finding(finding) is True
    assert is_blocking(finding) is True


def _verdict(findings: list[dict[str, Any]]) -> dict[str, Any]:
    return {"findings": findings, "note": "t"}


CONTRACT_TABLE: list[tuple[str, list[dict[str, Any]]]] = [
    ("base-blocking-blocker", [_finding()]),
    ("confidence-medium", [_finding(confidence="medium")]),
    ("file-whitespace", [_finding(file="   ")]),
    ("scenario-whitespace", [_finding(scenario="\t\n ")]),
    ("observed-result-whitespace", [_finding(observed_result=" ")]),
    ("evidence-empty", [_finding(evidence=[])]),
    (
        "evidence-blank-reason",
        [_finding(evidence=[{"file": "a.py", "line": 1, "reason": "  "}])],
    ),
    (
        "evidence-blank-file",
        [_finding(evidence=[{"file": "  ", "line": 1, "reason": "r"}])],
    ),
    (
        "evidence-first-blank-second-valid",
        [
            _finding(
                evidence=[
                    {"file": "", "line": 1, "reason": "r"},
                    {"file": "b.py", "line": 2, "reason": "ok"},
                ]
            )
        ],
    ),
    (
        "kind-file-missing-line-zero",
        [_finding(kind="file-missing", line=0)],
    ),
    ("severity-minor", [_finding(severity="minor")]),
    ("severity-blocker", [_finding(severity="blocker")]),
    (
        "two-findings-one-blocking",
        [_finding(), _finding(severity="minor")],
    ),
    ("expected-result-blank", [_finding(expected_result="")]),
]


#: Вердикты **вне схемы** кита: текстовое поле не строка. Настоящий
#: `apply-threshold.sh` отвергает такой вердикт на валидации (код 2) и до
#: предиката не доходит — значит, зеркало обязано не считать находку
#: блокирующей. Это отдельная таблица: у этих строк проверяется не совпадение
#: «код 1 ⇔ блокирует», а то, что блокировки нет ни у скрипта, ни у предиката.
#: Обязательные поля находки по схеме кита (`apply-threshold.sh`, jq-проверка).
REQUIRED_FIELDS = (
    "kind",
    "severity",
    "confidence",
    "title",
    "file",
    "line",
    "scenario",
    "observed_result",
    "expected_result",
    "evidence",
)


def _without(field: str) -> dict[str, Any]:
    """Заведомо блокирующая находка без одного обязательного поля."""
    finding = _finding()
    del finding[field]
    return finding


SCHEMA_INVALID_TABLE: list[tuple[str, list[dict[str, Any]]]] = [
    *((f"missing-{field}", [_without(field)]) for field in REQUIRED_FIELDS),
    ("evidence-item-without-line", [_finding(evidence=[{"file": "a.py", "reason": "r"}])]),
    ("evidence-item-without-reason", [_finding(evidence=[{"file": "a.py", "line": 1}])]),
    ("evidence-item-without-file", [_finding(evidence=[{"line": 1, "reason": "r"}])]),
    (
        "evidence-item-negative-line",
        [_finding(evidence=[{"file": "a.py", "line": -1, "reason": "r"}])],
    ),
    ("file-missing-with-a-line", [_finding(kind="file-missing", line=1)]),
    ("line-fractional", [_finding(line=1.5)]),
    ("line-negative", [_finding(line=-1)]),
    ("line-boolean", [_finding(line=True)]),
    ("kind-outside-the-enum", [_finding(kind="bogus")]),
    ("severity-outside-the-enum", [_finding(severity="critical")]),
    ("confidence-outside-the-enum", [_finding(confidence="certain")]),
    ("file-not-a-string", [_finding(file=0)]),
    ("scenario-not-a-string", [_finding(scenario=[])]),
    ("observed-result-not-a-string", [_finding(observed_result={})]),
    (
        "evidence-item-fields-not-strings",
        [_finding(evidence=[{"file": 0, "line": 1, "reason": 0}])],
    ),
    (
        "every-text-field-not-a-string",
        [
            _finding(
                file=0,
                scenario=[],
                observed_result={},
                evidence=[{"file": 0, "line": 1, "reason": 0}],
            )
        ],
    ),
]


@pytest.mark.skipif(not SCRIPT.exists(), reason=f"скрипт не найден: {SCRIPT}")
@pytest.mark.parametrize(
    ("_case_id", "findings"),
    SCHEMA_INVALID_TABLE,
    ids=[case_id for case_id, _ in SCHEMA_INVALID_TABLE],
)
def test_contract_schema_invalid_verdict_never_blocks(
    _case_id: str, findings: list[dict[str, Any]], tmp_path: Path
) -> None:
    """Вердикт вне схемы: скрипт отвечает кодом 2, предикат — `False`.

    Нестроковое текстовое поле не «заполнено»: до порога такой вердикт не
    доживает, и зеркало, называвшее находку блокирующей, расходилось со
    скриптом в самую опасную сторону — считало бы её FP/TP в метриках там, где
    гейт вообще не срабатывал.
    """
    verdict_path = tmp_path / "verdict.json"
    verdict_path.write_text(json.dumps(_verdict(findings)), encoding="utf-8")

    result = subprocess.run(
        ["sh", str(SCRIPT), "--verdict", str(verdict_path), "--format", "text"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2, f"ожидался отказ валидации: {result.stdout} {result.stderr}"
    # Обе ступени зеркала: находка негодна по схеме и потому не блокирует.
    assert not all(is_schema_valid_finding(finding) for finding in findings)
    assert not any(is_blocking(finding) for finding in findings)


@pytest.mark.skipif(not SCRIPT.exists(), reason=f"скрипт не найден: {SCRIPT}")
@pytest.mark.parametrize(
    ("_case_id", "findings"),
    CONTRACT_TABLE,
    ids=[case_id for case_id, _ in CONTRACT_TABLE],
)
def test_contract_matches_apply_threshold(
    _case_id: str, findings: list[dict[str, Any]], tmp_path: Path
) -> None:
    """Код выхода настоящего ``apply-threshold.sh`` обязан совпасть с
    ``is_blocking`` на каждой строке таблицы: 1, если хоть одна находка
    блокирует, иначе 0. Код 2 означает, что фикстура вышла за пределы схемы —
    чинить фикстуру, не ослаблять проверку.
    """
    verdict = _verdict(findings)
    verdict_path = tmp_path / "verdict.json"
    verdict_path.write_text(json.dumps(verdict), encoding="utf-8")

    result = subprocess.run(
        ["sh", str(SCRIPT), "--verdict", str(verdict_path), "--format", "text"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode in (0, 1), (
        f"вердикт негоден (код {result.returncode}): {result.stderr}"
    )
    expected_blocking = any(is_blocking(f) for f in findings)
    assert (result.returncode == 1) == expected_blocking
