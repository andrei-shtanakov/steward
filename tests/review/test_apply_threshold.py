"""Тесты scripts/review/apply-threshold.sh — порог, рендер, коды выхода."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "review" / "apply-threshold.sh"

# sh на macOS — это bash 3.2, а на Ubuntu (CI) /bin/sh — уже сам dash. Голый
# флаг без значения расходится между ними (см. Fix round 1): bash молча
# продвигает `shift 2` за край и даёт exit 1, dash падает своим сообщением
# мимо usage(). Гоняем сторож под обоими, чтобы расхождение не спряталось.
#
# На стоковом macOS `dash` не установлен: безусловный вызов ронял бы весь
# набор `FileNotFoundError`'ом ДО того, как проверяемые скрипты вообще
# запустились — красный локальный прогон не про предмет. `sh` всегда в
# наборе безусловно; `dash` пропускается точечно и громко там, где его нет
# — так, чтобы skip читался как «покрытие сузилось», а не как «тест прошёл».
INTERPRETERS = [
    "sh",
    pytest.param(
        "dash",
        marks=pytest.mark.skipif(
            shutil.which("dash") is None,
            reason="dash не найден в PATH — сторож не проверен под dash, покрытие уже",
        ),
    ),
]


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


def finding(**overrides: object) -> dict:
    """Полная находка схемы v2; поля переопределяются по месту.

    По умолчанию — блокирующая major: high confidence, заполненные scenario/
    observed_result и один элемент evidence. Тесты, проверяющие неблокировку,
    выключают ровно одно требование — так каждое из них закреплено отдельно.
    """
    base: dict = {
        "severity": "major",
        "title": "продюсер молчит про пустой ответ",
        "file": "src/a.py",
        "line": 10,
        "scenario": "вход X при пустом ответе API",
        "observed_result": "функция возвращает 0 и не пишет файл",
        "expected_result": "отказ с кодом 2",
        "evidence": [{"file": "src/b.py", "line": 5, "reason": "return 0 без записи"}],
        "confidence": "high",
    }
    base.update(overrides)
    return base


def test_no_findings_is_green(tmp_path: Path) -> None:
    result = run(write(tmp_path, {"findings": [], "note": "чисто"}))
    assert result.returncode == 0
    assert "Находок нет." in result.stdout


def test_full_major_with_high_confidence_blocks(tmp_path: Path) -> None:
    result = run(write(tmp_path, {"findings": [finding()], "note": ""}))
    assert result.returncode == 1
    assert "БЛОКИРУЕТ" in result.stdout


def test_full_blocker_blocks(tmp_path: Path) -> None:
    result = run(write(tmp_path, {"findings": [finding(severity="blocker")], "note": ""}))
    assert result.returncode == 1


def test_minor_never_blocks_even_fully_evidenced(tmp_path: Path) -> None:
    result = run(write(tmp_path, {"findings": [finding(severity="minor")], "note": ""}))
    assert result.returncode == 0
    assert "не блокирует по severity" in result.stdout


# --- Каждое требование блокировки выключается по отдельности --------------
#
# Правило владельца (2026-08-23): блокируют только blocker/major с
# confidence: high и заполненными scenario, observed_result и evidence.
# Убедительно звучащая гипотеза без проверенного кода не имеет права
# останавливать мерж — precision гейта важнее полноты.


@pytest.mark.parametrize(
    ("override", "expected_reason"),
    [
        ({"confidence": "medium"}, "confidence не high"),
        ({"confidence": "low"}, "confidence не high"),
        ({"scenario": ""}, "нет scenario"),
        ({"observed_result": ""}, "нет observed_result"),
        ({"evidence": []}, "нет evidence"),
    ],
)
def test_major_without_one_requirement_does_not_block(
    tmp_path: Path, override: dict, expected_reason: str
) -> None:
    result = run(write(tmp_path, {"findings": [finding(**override)], "note": ""}))
    assert result.returncode == 0, result.stdout
    # Находка не исчезает — рендерится с явной причиной, чего не хватило:
    # молча понижать значило бы прятать от человека сигнал уровня major.
    assert expected_reason in result.stdout


def test_reason_names_every_missing_requirement(tmp_path: Path) -> None:
    weak = finding(confidence="low", scenario="", observed_result="", evidence=[])
    result = run(write(tmp_path, {"findings": [weak], "note": ""}))
    assert result.returncode == 0
    for reason in ("confidence не high", "нет scenario", "нет observed_result", "нет evidence"):
        assert reason in result.stdout


def test_one_blocking_among_weak_still_reddens(tmp_path: Path) -> None:
    payload = {"findings": [finding(confidence="low"), finding()], "note": ""}
    result = run(write(tmp_path, payload))
    assert result.returncode == 1


# --- Негодный вердикт — код 2, не «замечаний нет» --------------------------


def test_findings_not_an_array_is_config_error(tmp_path: Path) -> None:
    assert run(write(tmp_path, {"findings": "мусор", "note": ""})).returncode == 2


@pytest.mark.parametrize(
    "override",
    [
        {"severity": "critical"},
        {"severity": "Major"},
        {"confidence": "sure"},
        {"confidence": 1},
        {"title": 7},
        {"file": ["a"]},
        {"line": "десять"},
        # Схема требует integer >= 0: дробное и отрицательное — вне схемы,
        # хотя для jq оба «number» (замечание Copilot на #98).
        {"line": 3.5},
        {"line": -1},
        {"evidence": [{"file": "a", "line": 2.5, "reason": "r"}]},
        {"scenario": {"x": 1}},
        {"observed_result": 3.5},
        {"evidence": "прочитал всё"},
        {"evidence": [{"file": 1, "line": 2, "reason": "r"}]},
        {"evidence": [{"file": "a", "line": "два", "reason": "r"}]},
        {"evidence": ["строка"]},
    ],
)
def test_malformed_finding_is_config_error(tmp_path: Path, override: dict) -> None:
    """Всё вне схемы v2 — отказ ЗДЕСЬ, а не тихое «не блокирует».

    Порог читает severity/confidence как allow-list: значение вне enum'а,
    молча оценённое как «не блокирует», красило бы негодный вердикт зелёным —
    инвертированный инвариант кита.
    """
    result = run(write(tmp_path, {"findings": [finding(**override)], "note": ""}))
    assert result.returncode == 2, result.stdout
    assert "вердикт нечитаем" in result.stderr


def test_finding_without_severity_is_config_error(tmp_path: Path) -> None:
    broken = finding()
    del broken["severity"]
    assert run(write(tmp_path, {"findings": [broken], "note": ""})).returncode == 2


def test_finding_that_is_not_an_object_is_config_error(tmp_path: Path) -> None:
    assert run(write(tmp_path, {"findings": ["строка"], "note": ""})).returncode == 2


def test_non_string_note_is_config_error(tmp_path: Path) -> None:
    result = run(write(tmp_path, {"findings": [], "note": {"объект": 1}}))
    assert result.returncode == 2


def test_missing_note_is_config_error(tmp_path: Path) -> None:
    """`note` обязателен схемой — отсутствие значит вердикт вне схемы, код 2.

    Прежний порог терпел отсутствие note; с выравниванием валидации по
    присутствию полей (гейт на #98) терпимость снята везде единообразно.
    """
    assert run(write(tmp_path, {"findings": []})).returncode == 2


@pytest.mark.parametrize(
    "absent",
    [
        "title",
        "file",
        "line",
        "scenario",
        "observed_result",
        "expected_result",
        "evidence",
        "confidence",
    ],
)
def test_absent_required_field_is_config_error(tmp_path: Path, absent: str) -> None:
    """Отсутствующее поле — негодный вердикт (2), а не мягкое «не блокирует».

    Гейт на #98: находка без scenario/evidence уходила в «не блокирует» с
    кодом 0 — негодный вердикт красился зелёным. Отсутствие ключа и пустая
    строка — разные состояния: пустая строка это ответ «нечего», она понижает
    блокировку; отсутствие — вердикт вне схемы, судить по нему нельзя.
    """
    broken = finding()
    del broken[absent]
    result = run(write(tmp_path, {"findings": [broken], "note": ""}))
    assert result.returncode == 2, result.stdout
    assert "вердикт нечитаем" in result.stderr


def test_evidence_item_without_keys_is_config_error(tmp_path: Path) -> None:
    """`evidence: [{}]` — негодный вердикт, а не «заполненный evidence».

    Вторая сторона той же находки гейта: пустой объект проходил проверку типов
    (отсутствующие ключи читались null) и СЧИТАЛСЯ evidence — блокировал мерж
    записью, в которой нет ни файла, ни причины.
    """
    result = run(write(tmp_path, {"findings": [finding(evidence=[{}])], "note": ""}))
    assert result.returncode == 2, result.stdout


@pytest.mark.parametrize("missing_key", ["file", "line", "reason"])
def test_evidence_item_missing_one_key_is_config_error(tmp_path: Path, missing_key: str) -> None:
    item = {"file": "src/b.py", "line": 5, "reason": "return 0"}
    del item[missing_key]
    result = run(write(tmp_path, {"findings": [finding(evidence=[item])], "note": ""}))
    assert result.returncode == 2, result.stdout


# --- Рендер ----------------------------------------------------------------


def test_markdown_renders_all_v2_fields(tmp_path: Path) -> None:
    result = run(write(tmp_path, {"findings": [finding()], "note": "читал b"}))
    out = result.stdout
    assert "### [major] продюсер молчит про пустой ответ — `src/a.py:10`" in out
    assert "- Сценарий: вход X при пустом ответе API" in out
    assert "- Наблюдаемое: функция возвращает 0 и не пишет файл" in out
    assert "- Ожидаемое: отказ с кодом 2" in out
    assert "- Evidence: `src/b.py:5` — return 0 без записи" in out
    assert "- confidence: high → БЛОКИРУЕТ" in out
    assert "читал b" in out


def test_newlines_in_fields_are_flattened(tmp_path: Path) -> None:
    """Текст пишет модель: перевод строки внутри поля разъезжал бы markdown."""
    tricky = finding(scenario="строка раз\nстрока два", title="заголовок\nхвост")
    result = run(write(tmp_path, {"findings": [tricky], "note": ""}))
    assert "строка раз строка два" in result.stdout
    assert "заголовок хвост" in result.stdout


def test_text_format_has_no_markdown_headings(tmp_path: Path) -> None:
    result = run(write(tmp_path, {"findings": [finding()], "note": ""}), fmt="text")
    assert result.returncode == 1
    assert "### " not in result.stdout
    assert "[major/high]" in result.stdout


def test_missing_file_is_config_error(tmp_path: Path) -> None:
    assert run(tmp_path / "нет.json").returncode == 2


def test_unknown_format_is_config_error(tmp_path: Path) -> None:
    result = run(write(tmp_path, {"findings": [], "note": ""}), fmt="html")
    assert result.returncode == 2


@pytest.mark.parametrize("interpreter", INTERPRETERS)
def test_bare_verdict_flag_is_config_error(interpreter: str) -> None:
    result = subprocess.run([interpreter, str(SCRIPT), "--verdict"], capture_output=True, text=True)
    assert result.returncode == 2
    assert "usage" in result.stderr


@pytest.mark.parametrize("interpreter", INTERPRETERS)
def test_bare_format_flag_is_config_error(interpreter: str, tmp_path: Path) -> None:
    verdict = write(tmp_path, {"findings": [], "note": ""})
    result = subprocess.run(
        [interpreter, str(SCRIPT), "--verdict", str(verdict), "--format"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "usage" in result.stderr
