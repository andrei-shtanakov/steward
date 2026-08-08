"""Sync код ↔ каталог: три гарантии полноты (дизайн-решение владельца).

НЕ сырой скан строк "GC-*" — в коде есть префикс "GC-ARCH-", не являющийся
gate id (ложный 20-й). Извлекаем ТОЛЬКО строковый аргумент rule_id из
конструкторов Finding(...): позиционный №2 (после severity) или keyword.
Динамический rule_id (не строковый литерал) — ошибка теста с требованием
явного registry: сегодня registry пуст намеренно.
"""

from __future__ import annotations

import ast
from pathlib import Path

from steward.gatecatalog import load_catalog_files

SRC = Path(__file__).resolve().parents[2] / "src" / "steward"
PROFILES = Path(__file__).resolve().parents[2] / "profiles"

# Явный registry динамических rule_id (решение владельца: динамика требует
# обоснования здесь, а не молчаливого пропуска). Пуст намеренно.
ALLOWED_DYNAMIC: dict[str, str] = {}  # "file.py:line" -> обоснование


def _extract_finding_rule_ids() -> set[str]:
    found: set[str] = set()
    dynamic: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Finding"
            ):
                continue
            arg = None
            if len(node.args) >= 2:
                arg = node.args[1]
            for kw in node.keywords:
                if kw.arg == "rule_id":
                    arg = kw.value
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.add(arg.value)
            else:
                key = f"{path.name}:{node.lineno}"
                if key not in ALLOWED_DYNAMIC:
                    dynamic.append(key)
    assert not dynamic, f"динамический rule_id без записи в ALLOWED_DYNAMIC: {dynamic}"
    return found


def test_every_emitted_rule_id_is_active_in_catalog():
    cat = load_catalog_files(PROFILES / "gate-catalog.yaml", PROFILES / "roles.yaml")
    emitted = _extract_finding_rule_ids()
    assert emitted, "экстрактор не нашёл ни одного Finding(...) — сломан сам тест"
    missing = emitted - cat.active_ids()
    assert not missing, f"код эмитит id вне active-каталога: {sorted(missing)}"


def test_every_active_id_is_reachable_from_code():
    cat = load_catalog_files(PROFILES / "gate-catalog.yaml", PROFILES / "roles.yaml")
    emitted = _extract_finding_rule_ids()
    dead = cat.active_ids() - emitted
    assert not dead, (
        f"active id недостижим из кода (переведи в declared/deprecated): {sorted(dead)}"
    )
