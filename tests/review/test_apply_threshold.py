"""Тесты scripts/review/apply-threshold.sh — порог, рендер, коды выхода."""

import json
import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "review" / "apply-threshold.sh"


def run(verdict_path: Path, fmt: str = "markdown") -> subprocess.CompletedProcess[str]:
    """Запустить скрипт как настоящий sh, а не импортом функции."""
    return subprocess.run(
        ["sh", str(SCRIPT), "--verdict", str(verdict_path), "--format", fmt],
        capture_output=True,
        text=True,
    )


def write(tmp_path: Path, payload: object) -> Path:
    target = tmp_path / "verdict.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def test_no_findings_is_green(tmp_path: Path) -> None:
    result = run(write(tmp_path, {"findings": [], "note": "смотрел диф"}))
    assert result.returncode == 0
    assert "Находок нет." in result.stdout
    assert "смотрел диф" in result.stdout


def test_major_reddens(tmp_path: Path) -> None:
    payload = {"findings": [{"severity": "major", "file": "a.py", "summary": "s", "failure": "f"}]}
    assert run(write(tmp_path, payload)).returncode == 1


def test_blocker_reddens(tmp_path: Path) -> None:
    payload = {
        "findings": [{"severity": "blocker", "file": "a.py", "summary": "s", "failure": "f"}]
    }
    assert run(write(tmp_path, payload)).returncode == 1


def test_minor_alone_stays_green(tmp_path: Path) -> None:
    """Порог — blocker и major. minor виден в выводе, но не красит."""
    payload = {"findings": [{"severity": "minor", "file": "a.py", "summary": "s", "failure": "f"}]}
    result = run(write(tmp_path, payload))
    assert result.returncode == 0
    assert "minor" in result.stdout


def test_findings_not_an_array_is_config_error(tmp_path: Path) -> None:
    assert run(write(tmp_path, {"findings": "нет"})).returncode == 2


def test_missing_file_is_config_error(tmp_path: Path) -> None:
    result = run(tmp_path / "нет-такого.json")
    assert result.returncode == 2


def test_unknown_format_is_config_error(tmp_path: Path) -> None:
    assert run(write(tmp_path, {"findings": []}), fmt="хтмл").returncode == 2


def test_pipe_and_newline_in_cells_do_not_break_the_table(tmp_path: Path) -> None:
    """Текст вердикта пишет модель: `|` или перевод строки рвали бы таблицу молча."""
    payload = {
        "findings": [
            {
                "severity": "major",
                "file": "a.py",
                "summary": "две|трубы|внутри",
                "failure": "первая строка\nвторая строка",
            }
        ]
    }
    result = run(write(tmp_path, payload))
    rows = [ln for ln in result.stdout.splitlines() if ln.startswith("| major")]
    assert len(rows) == 1, "находка обязана остаться одной строкой таблицы"
    # Экранирование — это `\|`, не удаление трубы: символ остаётся, но с
    # предшествующим бэкслешем. Считаем СТРУКТУРНЫЕ трубы отдельно от
    # экранированных, а не сырое число символов `|` в строке.
    unescaped = re.sub(r"\\\|", "", rows[0]).count("|")
    assert unescaped == 5, "внутренние трубы обязаны быть экранированы"


def test_text_format_has_no_markdown_table(tmp_path: Path) -> None:
    payload = {"findings": [{"severity": "major", "file": "a.py", "summary": "s", "failure": "f"}]}
    result = run(write(tmp_path, payload), fmt="text")
    assert "|---|" not in result.stdout
    assert "major" in result.stdout
