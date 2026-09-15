"""Предикат блокировки — Python-зеркало ``BLOCKING_DEF`` из
``scripts/review/apply-threshold.sh``.

Зеркало двухступенчато, как и сам скрипт: сначала **схема** вердикта
(`is_schema_valid_finding` — негодную находку кит отвергает кодом 2 до
порога), затем предикат блокировки.

«Блокирующее предсказание» определяется **ровно** этим bash/jq-предикатом, не
его пересказом (дизайн §9): ``severity ∈ {blocker, major}`` и
``confidence == "high"`` и непустые после удаления всех пробельных символов
``file``, ``scenario``, ``observed_result``, и хотя бы один элемент
``evidence`` с непустыми (по тому же правилу) ``file`` и ``reason``. В самом
пороге ``kind`` не участвует (``file-missing`` блокирует так же, как
``defect``) и ``line`` не сравнивается (0 — легитимный указатель уровня
файла) — но схема требует от ``file-missing`` ровно ``line: 0``, а от
``line`` — целого ≥ 0, поэтому на негодной находке решение не выносится вовсе.

Все эти поля обязаны быть **строками**: скрипт валидирует вердикт по схеме
**до** порога, поэтому находка с ``file: 0`` или ``scenario: []`` не доживает
до решения о блокировке (код 2). Зеркало обязано отвечать на неё ``False``, а
не «поле не пусто, значит заполнено».

Закреплено контрактным тестом (``tests/review_eval/test_threshold.py``)
против настоящего скрипта: два определения не должны разойтись незамеченно.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

__all__ = [
    "BLOCKING_SEVERITIES",
    "CONFIDENCES",
    "KINDS",
    "SEVERITIES",
    "as_line_number",
    "is_blank",
    "is_blocking",
    "is_schema_valid_finding",
    "is_text",
]

BLOCKING_SEVERITIES: frozenset[str] = frozenset({"blocker", "major"})

#: Перечисления схемы вердикта v2 — ровно как в jq-проверке `apply-threshold.sh`.
KINDS: frozenset[str] = frozenset({"defect", "file-missing"})
SEVERITIES: frozenset[str] = frozenset({"blocker", "major", "minor", "nit"})
CONFIDENCES: frozenset[str] = frozenset({"high", "medium", "low"})

#: Обязательные текстовые поля находки: присутствие проверяется наравне с типом
#: (в jq — тем же `all(type == "string")` по списку).
_TEXT_FIELDS: tuple[str, ...] = (
    "title",
    "file",
    "scenario",
    "observed_result",
    "expected_result",
)


def is_blank(value: object) -> bool:
    """``True``, если ``value`` — ``None`` либо строка, пустая после удаления
    всех символов, для которых истинно ``str.isspace()`` (совпадает с jq
    ``gsub("\\s"; "")`` для типичных пробельных символов ``\\s``).

    Нестроковое значение (число, список, объект) — **не текст**: ``is_blank``
    отвечает про него ``False``, зеркаля jq ``(. // "") | ...``, но
    блокировать такая находка не может — это решает `is_text`, потому что
    настоящий порог до предиката не доходит: вердикт с нестроковым текстовым
    полем отвергает схема (код 2).
    """
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    return "".join(ch for ch in value if not ch.isspace()) == ""


def is_text(value: object) -> bool:
    """``True``, если ``value`` — **строка** с непробельным содержимым.

    Требование типа здесь не педантизм: `apply-threshold.sh` валидирует
    вердикт по схеме до порога, и находка с ``file: 0`` или ``scenario: []``
    не доживает до решения о блокировке (код 2, «находка вне схемы»). Зеркало,
    считавшее такое поле заполненным, называло находку блокирующей там, где
    гейт вообще не срабатывал, — и метрики получали TP/FP из вердикта, который
    кит отверг.

    Известное расхождение (не закрыто): «пробельность» здесь — `str.isspace()`,
    у jq — `\\s` Oniguruma; они расходятся на управляющих разделителях
    U+001C–U+001F (Python считает их пробелами, jq — нет). Через реальный
    вердикт такое поле недостижимо на практике; фиксируется как хвост владельцу.
    """
    return isinstance(value, str) and not is_blank(value)


def _is_blocking_evidence_item(item: object) -> bool:
    """Годится ли элемент evidence для блокировки: объект со строковыми полями.

    Негодные элементы (не объект, нестроковые ``file``/``reason``) не
    «блокируют» и не «разблокируют» — они просто не считаются, как и в jq,
    где блокировку даёт **хотя бы один** годный элемент.
    """
    return isinstance(item, Mapping) and is_text(item.get("file")) and is_text(item.get("reason"))


def as_line_number(value: object) -> int | None:
    """Номер строки как ``int`` либо ``None``, если это не номер строки.

    Зеркало jq ``type == "number" and . == floor and . >= 0``: ``bool``
    исключён (в jq у ``true`` тип ``boolean``, а не ``number``), дробное и
    отрицательное не проходят, а ``10.0`` — проходит и даёт ``10``: JSON не
    различает целое и дробную запись целого, и кит такую находку принимает.

    Одно определение на пакет: матчер требовал именно ``int`` и терял годную
    находку (`matcher._as_line`), а схема порога её принимала — расхождение
    двух «что такое номер строки» давало FP и пропуск на одной находке.

    Целое **не приводится** к ``float``: на ``10**400`` такое приведение
    бросало `OverflowError`, то есть вердикт с абсурдным номером строки ронял
    инструмент вместо ответа «годен / не годен». Такое число принимается как
    есть — это законное JSON-число, равное своему floor, и jq его тоже
    пропускает; отвергнет его уже разметка, а не парсер. `inf` и `nan`
    номером строки не становятся (матчеру нечего сопоставлять), но схемная
    проверка `_is_line_number` +inf **принимает** — как jq, у которого
    `inf == floor(inf)`: вердикт с `line: 1e400` кит блокирует, и зеркало
    обязано ответить так же.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer() or value < 0:
            return None
        return int(value)
    return None


def _is_line_number(value: object) -> bool:
    """Годится ли значение по схеме: см. `as_line_number`, плюс +inf — как jq.

    `1e400` в JSON jq читает как бесконечность и пропускает через
    `. == floor and . >= 0`; Python-разбор даёт `float('inf')`. Схемное зеркало
    повторяет jq, а номер строки у такого значения всё равно `None`.
    """
    if isinstance(value, float) and value == math.inf:
        return True
    return as_line_number(value) is not None


def _in_enum(value: object, allowed: frozenset[str]) -> bool:
    """Строка из закрытого набора; список/объект — не TypeError, а просто «нет»."""
    return isinstance(value, str) and value in allowed


def is_schema_valid_finding(finding: Mapping[str, object]) -> bool:
    """Годна ли **одна находка** по схеме вердикта v2 — зеркало jq-проверки кита.

    `apply-threshold.sh` валидирует вердикт до порога и на негодном выходит
    кодом 2, не решая ничего про блокировку. Значит, предикат блокировки обязан
    начинаться здесь: иначе зеркало отвечало бы «блокирует» на вердикт, по
    которому гейт вообще не выносил решения, и метрики получали бы из него
    TP/FP.

    Проверяется ровно то же, что в jq (`scripts/review/apply-threshold.sh`):
    ``kind`` и ``severity``/``confidence`` внутри перечислений; ``line`` —
    целое число ≥ 0 и **строго 0** при ``kind: file-missing``; пять текстовых
    полей — строки (присутствие наравне с типом); ``evidence`` — массив
    объектов со строковыми ``file``/``reason`` и целым ``line`` ≥ 0.

    Чего здесь нет: проверки ``note`` и типа ``findings`` — они уровня
    вердикта, а не находки, и живут у вызывающего (`metrics._findings`,
    `runner._verdict_is_structural`).
    """
    if not isinstance(finding, Mapping):
        return False
    if not _in_enum(finding.get("kind"), KINDS):
        return False
    if not _is_line_number(finding.get("line")):
        return False
    if finding.get("kind") == "file-missing" and finding.get("line") != 0:
        return False
    if not _in_enum(finding.get("severity"), SEVERITIES):
        return False
    if not _in_enum(finding.get("confidence"), CONFIDENCES):
        return False
    if not all(isinstance(finding.get(field), str) for field in _TEXT_FIELDS):
        return False
    evidence = finding.get("evidence")
    if not isinstance(evidence, list):
        return False
    return all(_is_schema_valid_evidence(item) for item in evidence)


def _is_schema_valid_evidence(item: object) -> bool:
    """Элемент ``evidence`` по схеме: объект с ``file``/``line``/``reason``."""
    return (
        isinstance(item, Mapping)
        and isinstance(item.get("file"), str)
        and _is_line_number(item.get("line"))
        and isinstance(item.get("reason"), str)
    )


def is_blocking(finding: Mapping[str, object]) -> bool:
    """Блокирует ли находка мерж — зеркало ``BLOCKING_DEF::blocking``.

    Сначала схема (`is_schema_valid_finding`): негодную находку кит отвергает
    кодом 2 **до** порога, и блокирующей она быть не может. Поэтому ``kind`` и
    ``line`` в решении всё-таки участвуют — но только через схему:
    ``file-missing`` обязан нести ``line: 0``, а само ``line`` обязано быть
    целым ≥ 0. В самом пороге ни то, ни другое роли не играет:
    ``file-missing`` блокирует так же, как ``defect``, и значение ``line`` не
    сравнивается ни с чем.

    Далее — предикат порога: ``severity ∈ {blocker, major}``,
    ``confidence == "high"``, непустые (`is_text`) ``file``/``scenario``/
    ``observed_result`` и хотя бы один годный элемент ``evidence`` с непустыми
    ``file`` и ``reason``.
    """
    if not is_schema_valid_finding(finding):
        return False
    severity = finding.get("severity")
    if not isinstance(severity, str) or severity not in BLOCKING_SEVERITIES:
        return False
    if finding.get("confidence") != "high":
        return False
    if not is_text(finding.get("file")):
        return False
    if not is_text(finding.get("scenario")):
        return False
    if not is_text(finding.get("observed_result")):
        return False

    evidence = finding.get("evidence")
    if not isinstance(evidence, list):
        return False
    return any(_is_blocking_evidence_item(item) for item in evidence)
