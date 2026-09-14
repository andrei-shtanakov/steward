"""Тесты scripts/review/harness-claude — адаптер claude CLI под codex-диалект кита.

Свойство одно: адаптер говорит codex-диалектом снаружи (ровно те флаги, что
шлёт local.sh) и claude-диалектом внутри, а вердиктом становится ТОЛЬКО
structured_output успешного ответа. Любой другой исход — не-0, который кит
превращает в код 3; молчаливый approve при сломанном ревьюере невозможен по
построению. `claude` подставной: записывает argv и промпт, отдаёт конверт.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "scripts" / "review" / "harness-claude"

INTERPRETERS = [
    "sh",
    pytest.param(
        "dash",
        marks=pytest.mark.skipif(
            shutil.which("dash") is None,
            reason="dash не найден в PATH — покрытие уже, чем в CI",
        ),
    ),
]

CLAUDE_STUB = """#!/bin/sh
# Подставной claude: argv — в $CLAUDE_STUB_ARGV (по слову на строку), промпт
# (stdin) — в $CLAUDE_STUB_PROMPT, ответ — содержимое $CLAUDE_STUB_ENVELOPE;
# $CLAUDE_STUB_EXIT — завершиться этим кодом вместо ответа.
printf '%s\\n' "$@" > "$CLAUDE_STUB_ARGV"
cat > "$CLAUDE_STUB_PROMPT"
if [ -n "${CLAUDE_STUB_EXIT:-}" ]; then
    echo "claude stub: падаю по просьбе" >&2
    exit "$CLAUDE_STUB_EXIT"
fi
cat "$CLAUDE_STUB_ENVELOPE"
"""

VERDICT_OK = {"findings": [], "note": "stub"}


def envelope(structured: object, *, subtype: str = "success", is_error: bool = False) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": subtype,
            "is_error": is_error,
            "structured_output": structured,
        }
    )


class Stand:
    """Каталог с подставным claude + настоящим jq в PATH, схема и пути вывода."""

    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        stub = self.bin / "claude"
        stub.write_text(CLAUDE_STUB, encoding="utf-8")
        stub.chmod(0o755)
        self.schema = tmp_path / "schema.json"
        self.schema.write_text('{"type":"object"}\n', encoding="utf-8")
        self.out = tmp_path / "out"
        self.out.mkdir()
        self.verdict = self.out / "verdict.json"
        self.argv = tmp_path / "argv.txt"
        self.prompt = tmp_path / "prompt.txt"
        self.env_file = tmp_path / "envelope.json"

    def run(
        self,
        *args: str,
        envelope_text: str = envelope(VERDICT_OK),
        interp: str = "sh",
        stdin: str = "ПРОМПТ\n",
        path: str | None = None,
        claude_exit: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.env_file.write_text(envelope_text, encoding="utf-8")
        env = dict(os.environ)
        env["PATH"] = path if path is not None else f"{self.bin}{os.pathsep}{os.environ['PATH']}"
        env["CLAUDE_STUB_ARGV"] = str(self.argv)
        env["CLAUDE_STUB_PROMPT"] = str(self.prompt)
        env["CLAUDE_STUB_ENVELOPE"] = str(self.env_file)
        if claude_exit is not None:
            env["CLAUDE_STUB_EXIT"] = claude_exit
        # Интерпретатор — АБСОЛЮТНЫМ путём, найденным по PATH теста ДО сужения:
        # CPython резолвит executable по env["PATH"] переданного окружения, и
        # при PATH из одного каталога голое `sh` дало бы FileNotFoundError
        # стенда вместо проверки префлайта (прецедент —
        # tests/review/test_apply_threshold.py: `/bin/sh` при пустом PATH).
        interp_abs = shutil.which(interp)
        assert interp_abs, f"{interp} не найден в PATH теста"
        return subprocess.run(
            [interp_abs, str(ADAPTER), *args],
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
        )

    def codex_args(self, *extra: str) -> list[str]:
        return [
            "--sandbox",
            "read-only",
            "--output-schema",
            str(self.schema),
            "--output-last-message",
            str(self.verdict),
            *extra,
            "-",
        ]


@pytest.mark.parametrize("bad", [["--frobnicate"], ["--model"], ["--output-schema"]])
def test_unknown_or_bare_flag_is_config_error(tmp_path: Path, bad: list[str]) -> None:
    """Неизвестный флаг и голый флаг без значения — код 2, не молчаливое
    игнорирование: кит нового поколения мог добавить семантику, которую адаптер
    не понимает, и «продолжить как понял» дало бы ревью не на тех условиях."""
    s = Stand(tmp_path)
    res = s.run(*bad)
    assert res.returncode == 2, res.stderr
    assert not s.verdict.exists()


@pytest.mark.parametrize(
    "args, reason",
    [
        (
            [
                "--sandbox",
                "workspace-write",
                "--output-schema",
                "S",
                "--output-last-message",
                "V",
                "-",
            ],
            "read-only",
        ),
        (["--sandbox", "read-only", "--output-last-message", "V", "-"], "--output-schema"),
        (["--sandbox", "read-only", "--output-schema", "S", "-"], "--output-last-message"),
        (["--sandbox", "read-only", "--output-schema", "S", "--output-last-message", "V"], "stdin"),
    ],
)
def test_contract_violations_are_config_errors(
    tmp_path: Path, args: list[str], reason: str
) -> None:
    s = Stand(tmp_path)
    args = [str(s.schema) if a == "S" else str(s.verdict) if a == "V" else a for a in args]
    res = s.run(*args)
    assert res.returncode == 2, res.stderr
    assert reason in res.stderr


def test_unreadable_schema_is_config_error(tmp_path: Path) -> None:
    """codex_args() всегда подставляет читаемую схему — здесь путь задан явно."""
    s = Stand(tmp_path)
    res = s.run(
        "--sandbox",
        "read-only",
        "--output-schema",
        str(tmp_path / "нет.json"),
        "--output-last-message",
        str(s.verdict),
        "-",
    )
    assert res.returncode == 2, res.stderr
    assert "схема" in res.stderr


def test_missing_claude_in_path_is_config_error(tmp_path: Path) -> None:
    """Нет бинаря — конфигурация (код 2), не «ревьюер не отработал»."""
    s = Stand(tmp_path)
    jq_dir = Path(shutil.which("jq") or "").parent
    res = s.run(*s.codex_args(), path=str(jq_dir))
    assert res.returncode == 2, res.stderr
    assert "claude" in res.stderr


def test_missing_jq_in_path_is_config_error_not_127(tmp_path: Path) -> None:
    """Без префлайта отсутствие jq выродилось бы в 127 → код 3 кита, вопреки
    заявленному контракту «конфигурация → 2» (поправка владельца к спеке)."""
    s = Stand(tmp_path)
    res = s.run(*s.codex_args(), path=str(s.bin))  # только подставной claude, без jq
    assert res.returncode == 2, res.stderr
    assert "jq" in res.stderr
