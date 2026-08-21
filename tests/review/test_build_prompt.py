"""Тесты scripts/review/build-prompt.sh — склейка инструкций с дифом."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "review" / "build-prompt.sh"


def run(prompt: Path, diff: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(SCRIPT), "--prompt", str(prompt), "--diff", str(diff)],
        capture_output=True,
        text=True,
    )


def make(tmp_path: Path, prompt_text: str, diff_text: str) -> tuple[Path, Path]:
    prompt = tmp_path / "prompt.md"
    diff = tmp_path / "diff.patch"
    prompt.write_text(prompt_text, encoding="utf-8")
    diff.write_text(diff_text, encoding="utf-8")
    return prompt, diff


def test_prompt_precedes_diff_between_markers(tmp_path: Path) -> None:
    prompt, diff = make(tmp_path, "ИНСТРУКЦИИ", "ДИФ-ТЕЛО")
    out = run(prompt, diff).stdout
    assert out.index("ИНСТРУКЦИИ") < out.index("--- ДИФ НАЧАЛО ---")
    assert out.index("--- ДИФ НАЧАЛО ---") < out.index("ДИФ-ТЕЛО")
    assert out.index("ДИФ-ТЕЛО") < out.index("--- ДИФ КОНЕЦ ---")


def test_shell_metacharacters_in_diff_pass_through_verbatim(tmp_path: Path) -> None:
    """Диф идёт через файл, а не через argv: подстановки не исполняются."""
    payload = "$(touch /tmp/pwned) `id` ${HOME} && rm -rf / ; echo x"
    prompt, diff = make(tmp_path, "И", payload)
    result = run(prompt, diff)
    assert result.returncode == 0
    assert payload in result.stdout


def test_end_marker_inside_diff_is_passed_through(tmp_path: Path) -> None:
    """Закрепляет СЕГОДНЯШНЕЕ поведение, а не желаемое.

    Диф, содержащий строку конца, попадает в промпт как есть. Усиление
    разделителя — отдельное решение владельца, см. хвосты плана.
    """
    prompt, diff = make(tmp_path, "И", "before\n--- ДИФ КОНЕЦ ---\nafter")
    out = run(prompt, diff).stdout
    assert out.count("--- ДИФ КОНЕЦ ---") == 2


def test_missing_prompt_is_config_error(tmp_path: Path) -> None:
    _, diff = make(tmp_path, "И", "Д")
    result = subprocess.run(
        ["sh", str(SCRIPT), "--prompt", str(tmp_path / "нет.md"), "--diff", str(diff)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


def test_missing_diff_is_config_error(tmp_path: Path) -> None:
    prompt, _ = make(tmp_path, "И", "Д")
    result = subprocess.run(
        ["sh", str(SCRIPT), "--prompt", str(prompt), "--diff", str(tmp_path / "нет.patch")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
