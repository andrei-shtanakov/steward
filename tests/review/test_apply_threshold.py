"""Тесты scripts/review/apply-threshold.sh — порог, рендер, коды выхода."""

import json
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "review" / "apply-threshold.sh"

# sh на macOS — это bash 3.2, а на Ubuntu (CI) /bin/sh — уже сам dash. Голый
# флаг без значения расходится между ними (см. Fix round 1): bash молча
# продвигает `shift 2` за край и даёт exit 1, dash падает своим сообщением
# мимо usage(). Гоняем сторож под обоими, чтобы расхождение не спряталось.
INTERPRETERS = ["sh", "dash"]


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


def test_severity_outside_enum_is_config_error(tmp_path: Path) -> None:
    """`CRITICAL` (иная капитализация/синоним) не в enum'е схемы — не должна
    молча читаться как «ниже порога» и красить чек зелёным."""
    payload = {
        "findings": [{"severity": "CRITICAL", "file": "a.py", "summary": "дыра", "failure": "f"}],
        "note": "n",
    }
    result = run(write(tmp_path, payload))
    assert result.returncode == 2
    assert "severity" in result.stderr


def test_finding_without_severity_is_config_error(tmp_path: Path) -> None:
    """Находка вообще без ключа `severity` — тот же инвертированный инвариант:
    без явной проверки она тоже читалась бы как «ниже порога»."""
    result = run(write(tmp_path, {"findings": [{"file": "a"}], "note": "n"}))
    assert result.returncode == 2


def test_finding_that_is_not_an_object_is_config_error(tmp_path: Path) -> None:
    """Элемент-строка внутри `findings` обязан давать код 2 (§7), не 5 —
    иначе CI разбирает механический сбой как находку уровня blocker/major."""
    result = run(write(tmp_path, {"findings": ["строка"], "note": "n"}))
    assert result.returncode == 2


def test_non_string_file_is_config_error(tmp_path: Path) -> None:
    """Числовой `file` проходит проверку `severity`, но падает ПОЗЖЕ внутри
    `gsub` в `cell` — вызывающий получил бы частично напечатанный body.md
    (заголовок + note + начало таблицы) и код 5, вне набора §7."""
    payload = {
        "findings": [{"severity": "major", "file": 123, "summary": "s", "failure": "f"}],
        "note": "n",
    }
    result = run(write(tmp_path, payload))
    assert result.returncode == 2
    assert result.stdout == ""


def test_non_string_summary_is_config_error(tmp_path: Path) -> None:
    payload = {
        "findings": [{"severity": "major", "file": "a.py", "summary": {"x": 1}, "failure": "f"}],
        "note": "n",
    }
    result = run(write(tmp_path, payload))
    assert result.returncode == 2
    assert result.stdout == ""


def test_non_string_failure_is_config_error(tmp_path: Path) -> None:
    payload = {
        "findings": [{"severity": "major", "file": "a.py", "summary": "s", "failure": [1, 2]}],
        "note": "n",
    }
    result = run(write(tmp_path, payload))
    assert result.returncode == 2
    assert result.stdout == ""


def test_null_file_summary_failure_still_render(tmp_path: Path) -> None:
    """`null` — легитимное отсутствие поля: `cell` подставляет пустую строку,
    а не крашится. Guard обязан пропускать null наравне со строкой."""
    payload = {
        "findings": [{"severity": "minor", "file": None, "summary": None, "failure": None}],
        "note": "n",
    }
    result = run(write(tmp_path, payload))
    assert result.returncode == 0
    assert "| minor | `` |  |  |" in result.stdout


def test_valid_verdict_rendering_is_unchanged(tmp_path: Path) -> None:
    """Контрольный: ужесточение проверки не должно тронуть рендер годного
    вердикта — сверка байт-в-байт с ожидаемым выводом."""
    payload = {
        "findings": [{"severity": "major", "file": "a.py", "summary": "s", "failure": "f"}],
        "note": "n",
    }
    result = run(write(tmp_path, payload))
    assert result.returncode == 1
    assert result.stdout == (
        "## Ревью Codex — независимый чек\n"
        "\n"
        "n\n"
        "\n"
        "| уровень | файл | находка | сценарий отказа |\n"
        "|---|---|---|---|\n"
        "| major | `a.py` | s | f |\n"
        "\n"
        "_Порог: красным делают `blocker` и `major`. Это чек, не аппрув —\n"
        "и не замена ревью человека._\n"
    )


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


@pytest.mark.parametrize("interpreter", INTERPRETERS)
def test_bare_verdict_flag_is_config_error(interpreter: str) -> None:
    """Голый `--verdict` без значения — код 2 и usage(), а не сообщение shell."""
    result = subprocess.run([interpreter, str(SCRIPT), "--verdict"], capture_output=True, text=True)
    assert result.returncode == 2
    assert "usage" in result.stderr


@pytest.mark.parametrize("interpreter", INTERPRETERS)
def test_bare_format_flag_is_config_error(interpreter: str, tmp_path: Path) -> None:
    """Голый `--format` в конце командной строки — тоже код 2, а не exit 1/крах shell."""
    verdict = write(tmp_path, {"findings": []})
    result = subprocess.run(
        [interpreter, str(SCRIPT), "--verdict", str(verdict), "--format"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "usage" in result.stderr
