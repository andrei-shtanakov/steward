"""Регрессия: `steward.approvalfacts` и `steward.gatecheck.approval`
импортируются друг в друга по одному направлению (approvalfacts не знает
про gatecheck ни через producer, ни через publish), но взаимная ссылка
проходится по обоим модулям — цикл был бы виден только при импорте в обоих
порядках, в свежем интерпретаторе, а не в процессе, где один из модулей
уже частично загружен предыдущим тестом.

Унаследовано от ревью задачи 5: гарантия «нет цикла» была проверена вручную
и не запинена ничем. Задача 6 добавляет четвёртый модуль в тот же пакет —
ещё один шанс замкнуть цикл, и регрессия проявилась бы как непонятный
`ImportError` далеко от причины.
"""

import subprocess
import sys


def test_import_approvalfacts_then_gatecheck_approval() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", "import steward.approvalfacts; import steward.gatecheck.approval"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_import_gatecheck_approval_then_approvalfacts() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", "import steward.gatecheck.approval; import steward.approvalfacts"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_import_v3_then_gatecheck_approval_and_back() -> None:
    """Гейт читает файл release-гейта через `steward.approvalfacts.v3` (Task 11 M0): тот же
    цикл не должен замкнуться и через v3 — оба порядка, свежий интерпретатор."""
    for code in (
        "import steward.approvalfacts.v3; import steward.gatecheck.approval",
        "import steward.gatecheck.approval; import steward.approvalfacts.v3",
    ):
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=False
        )
        assert proc.returncode == 0, (code, proc.stderr)
