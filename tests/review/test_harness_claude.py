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
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.env_file.write_text(envelope_text, encoding="utf-8")
        env = {
            k: v for k, v in os.environ.items() if k not in ("REVIEW_USAGE_OUT", "REVIEW_EFFORT")
        }
        env["PATH"] = path if path is not None else f"{self.bin}{os.pathsep}{os.environ['PATH']}"
        env["CLAUDE_STUB_ARGV"] = str(self.argv)
        env["CLAUDE_STUB_PROMPT"] = str(self.prompt)
        env["CLAUDE_STUB_ENVELOPE"] = str(self.env_file)
        if claude_exit is not None:
            env["CLAUDE_STUB_EXIT"] = claude_exit
        if extra_env:
            env.update(extra_env)
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


def test_effort_empty_arg_is_config_error(tmp_path: Path) -> None:
    """Minor #6: `--effort ""` принимался и молча отбрасывался — тот же
    класс отказа, что у голого флага без значения выше."""
    s = Stand(tmp_path)
    res = s.run(*s.codex_args("--effort", ""))
    assert res.returncode == 2, res.stderr
    assert "--effort" in res.stderr
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


def test_directory_as_verdict_path_is_config_error(tmp_path: Path) -> None:
    """`--output-last-message`, указывающий на КАТАЛОГ (а не файл), должен
    отказать конфигурационным кодом 2 до вызова claude, а не молча вернуть 0
    без записанного вердикта — иначе `mv "$tmp" "$verdict"` переносит
    временный файл ВНУТРЬ каталога и оставляет цель кита без вердикта при
    зелёном коде выхода."""
    s = Stand(tmp_path)
    res = s.run(
        "--sandbox",
        "read-only",
        "--output-schema",
        str(s.schema),
        "--output-last-message",
        str(s.out),
        "-",
    )
    assert res.returncode == 2, res.stderr
    assert "каталог" in res.stderr
    assert list(s.out.glob(".verdict.*")) == [], "осиротевший временный файл"


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


# --- Боевой путь: argv claude, промпт, вердикт ---------------------------------


@pytest.mark.parametrize("interp", INTERPRETERS)
def test_success_writes_structured_output_and_uses_readonly_claude_flags(
    tmp_path: Path, interp: str
) -> None:
    """Вердикт JSON-семантически равен structured_output; claude получил ровно
    read-only набор флагов и промпт со stdin (диф не в argv)."""
    s = Stand(tmp_path)
    res = s.run(*s.codex_args("--model", "claude-opus-5"), interp=interp, stdin="ДИФ-В-STDIN\n")

    assert res.returncode == 0, res.stderr
    assert json.loads(s.verdict.read_text(encoding="utf-8")) == VERDICT_OK
    argv = s.argv.read_text(encoding="utf-8").splitlines()
    assert argv[:3] == ["-p", "--model", "claude-opus-5"]
    for flag in (
        "--json-schema",
        "--output-format",
        "json",
        "--restricted",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--permission-prompts",
        "none",
        "--tools",
        "Read",
        "Glob",
        "Grep",
    ):
        assert flag in argv, flag
    assert "ДИФ-В-STDIN" not in " ".join(argv)
    assert s.prompt.read_text(encoding="utf-8") == "ДИФ-В-STDIN\n"
    # Схема передаётся СОДЕРЖИМЫМ файла; точная форма пробелов — не контракт.
    assert json.loads(argv[argv.index("--json-schema") + 1]) == {"type": "object"}


def test_default_model_is_opus_5(tmp_path: Path) -> None:
    s = Stand(tmp_path)
    res = s.run(*s.codex_args())
    assert res.returncode == 0, res.stderr
    argv = s.argv.read_text(encoding="utf-8").splitlines()
    assert argv[argv.index("--model") + 1] == "claude-opus-5"


def test_verdict_is_canonical_jq_compact(tmp_path: Path) -> None:
    """Каноническая сериализация — `jq -c`: без пробелов, одной строкой."""
    s = Stand(tmp_path)
    res = s.run(*s.codex_args(), envelope_text=envelope({"findings": [], "note": "x y"}))
    assert res.returncode == 0, res.stderr
    text = s.verdict.read_text(encoding="utf-8")
    assert text == '{"findings":[],"note":"x y"}\n'


# --- Негодные ответы: не-0 и никакого вердикта ---------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        envelope(VERDICT_OK, is_error=True),
        envelope(VERDICT_OK, subtype="error_max_turns"),
        envelope(None),
        envelope({}),
        envelope("строка вместо объекта"),
        '{"type":"result","subtype":"success","is_error":false}',
        # `is_error` ОТСУТСТВУЕТ (не false) — `envelope()` всегда добавляет
        # поле, поэтому конструируется литералом. В jq `null | not` даёт
        # true, и старый фильтр `(.is_error | not)` принимал бы это как
        # успех — находка второго терминального ревью ветки.
        json.dumps({"type": "result", "subtype": "success", "structured_output": VERDICT_OK}),
        "это не JSON {",
        "",
    ],
)
@pytest.mark.parametrize("interp", INTERPRETERS)
def test_bad_envelope_is_failure_without_verdict(tmp_path: Path, bad: str, interp: str) -> None:
    s = Stand(tmp_path)
    res = s.run(*s.codex_args(), envelope_text=bad, interp=interp)
    assert res.returncode == 3, res.stderr
    assert "structured_output" in res.stderr
    assert not s.verdict.exists()
    assert list(s.out.glob(".verdict.*")) == [], "осиротевший временный файл"


def test_claude_nonzero_exit_is_failure_with_stderr_passthrough(tmp_path: Path) -> None:
    s = Stand(tmp_path)
    res = s.run(*s.codex_args(), claude_exit="7")
    assert res.returncode == 3, res.stderr
    assert "кодом 7" in res.stderr
    assert "падаю по просьбе" in res.stderr
    assert not s.verdict.exists()
    assert list(s.out.glob(".verdict.*")) == []


def test_temp_file_lives_next_to_verdict(tmp_path: Path) -> None:
    """Временный файл — в каталоге целевого: только тогда mv — rename, а не
    копирование через границу ФС. Подставной claude «зависает» ровно настолько,
    чтобы увидеть .verdict.* рядом с целевым путём."""
    s = Stand(tmp_path)
    probe = tmp_path / "probe.txt"
    stub = s.bin / "claude"
    stub.write_text(
        f'#!/bin/sh\ncat > /dev/null\nls -a "{s.out}" > "{probe}"\ncat "{s.env_file}"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    res = s.run(*s.codex_args())
    assert res.returncode == 0, res.stderr
    assert ".verdict." in probe.read_text(encoding="utf-8")
    assert list(s.out.glob(".verdict.*")) == []


# --- REVIEW_USAGE_OUT и --effort (спека review-eval §7, D12/D13) ----------------

FULL_ENVELOPE = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "structured_output": VERDICT_OK,
        "duration_ms": 12345,
        "total_cost_usd": 0.42,
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 900,
        },
    }
)


def _usage_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    out = tmp_path / "side" / "usage.json"
    return {"REVIEW_USAGE_OUT": str(out)}, out


def test_usage_sidecar_written_on_success(tmp_path: Path) -> None:
    s = Stand(tmp_path)
    env, out = _usage_env(tmp_path)
    res = s.run(
        *s.codex_args("--model", "claude-opus-5", "--effort", "high"),
        envelope_text=FULL_ENVELOPE,
        extra_env=env,
    )
    assert res.returncode == 0, res.stderr
    u = json.loads(out.read_text(encoding="utf-8"))
    assert u["schema"] == "review-usage/v1" and u["provider"] == "claude"
    assert u["model"] == "claude-opus-5" and u["requested_effort"] == "high"
    assert u["usage"]["input_tokens"] == 1000 and u["total_cost_usd"] == 0.42
    assert u["provider_duration_ms"] == 12345 and u["outcome"] == "success"
    argv = s.argv.read_text(encoding="utf-8").splitlines()
    assert argv[argv.index("--effort") + 1] == "high"


def test_usage_sidecar_written_on_error_envelope(tmp_path: Path) -> None:
    """Ошибочный ответ тоже стоил денег (D12): sidecar есть, outcome=error, код адаптера 3."""
    s = Stand(tmp_path)
    env, out = _usage_env(tmp_path)
    bad = json.dumps(
        {
            "type": "result",
            "subtype": "error_max_turns",
            "is_error": True,
            "total_cost_usd": 0.05,
            "usage": {"input_tokens": 10, "output_tokens": 1},
        }
    )
    res = s.run(*s.codex_args(), envelope_text=bad, extra_env=env)
    assert res.returncode == 3
    u = json.loads(out.read_text(encoding="utf-8"))
    assert u["outcome"] == "error" and u["total_cost_usd"] == 0.05
    assert u["provider_duration_ms"] is None  # отсутствует → null, не 0
    assert list(out.parent.glob(".usage.*")) == []


def test_usage_sidecar_on_unparseable_envelope(tmp_path: Path) -> None:
    s = Stand(tmp_path)
    env, out = _usage_env(tmp_path)
    res = s.run(*s.codex_args(), envelope_text="not json {", extra_env=env)
    assert res.returncode == 3
    u = json.loads(out.read_text(encoding="utf-8"))
    assert u["outcome"] == "error" and u["usage"] is None


def test_usage_out_empty_is_config_error(tmp_path: Path) -> None:
    s = Stand(tmp_path)
    res = s.run(*s.codex_args(), extra_env={"REVIEW_USAGE_OUT": ""})
    assert res.returncode == 2 and "REVIEW_USAGE_OUT" in res.stderr


def test_usage_out_directory_is_config_error(tmp_path: Path) -> None:
    """Каталог вместо файла: `mv` унёс бы временный файл ВНУТРЬ каталога, и
    адаптер вышел бы кодом 0, не записав sidecar по заявленному пути —
    находка финального ревью этой ветки. Проверка теперь в префлайте, до
    вызова claude."""
    s = Stand(tmp_path)
    res = s.run(*s.codex_args(), extra_env={"REVIEW_USAGE_OUT": str(tmp_path)})
    assert res.returncode == 2, res.stderr
    assert "каталог" in res.stderr
    assert list(tmp_path.glob(".usage.*")) == []
    assert not s.argv.exists()  # claude не вызван


def test_usage_out_unwritable_dir_fails_before_claude(tmp_path: Path) -> None:
    """Важное #2: валидация REVIEW_USAGE_OUT — целиком в префлайте, ДО
    платного вызова claude. Неписуемый каталог ловится на `mktemp` временного
    файла (mkdir -p на уже существующем каталоге успеха не гарантирует
    записи в него)."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root игнорирует биты доступа")
    s = Stand(tmp_path)
    unwritable = tmp_path / "unwritable"
    unwritable.mkdir()
    unwritable.chmod(0o500)
    try:
        res = s.run(*s.codex_args(), extra_env={"REVIEW_USAGE_OUT": str(unwritable / "usage.json")})
        assert res.returncode == 2, res.stderr
        assert not s.argv.exists()  # claude не вызван
    finally:
        unwritable.chmod(0o700)


def test_usage_post_call_failure_keeps_claude_code_3(tmp_path: Path) -> None:
    """Важное #2: sidecar не собрался ПОСЛЕ вызова claude, но сам claude уже
    отказал (claude_exit=7) — код адаптера остаётся 3 (сбой ревьюера), а не
    маскируется 2 (конфигурация sidecar'а). `jq` подставной: делегирует
    настоящему jq для всего, кроме `-c`/`-nc` (сборка sidecar-документа),
    которую намеренно проваливает."""
    real_jq = shutil.which("jq")
    assert real_jq, "нужен системный jq для теста"
    jq_bin = tmp_path / "jq-bin"
    jq_bin.mkdir()
    shim = jq_bin / "jq"
    shim.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '    case "$a" in\n'
        "        -c|-nc|-cn)\n"
        '            echo "jq shim: intentional failure" >&2\n'
        "            exit 1\n"
        "            ;;\n"
        "    esac\n"
        "done\n"
        f'exec "{real_jq}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    s = Stand(tmp_path)
    env, _out = _usage_env(tmp_path)
    path = f"{s.bin}{os.pathsep}{jq_bin}{os.pathsep}{os.environ['PATH']}"
    res = s.run(*s.codex_args(), path=path, claude_exit="7", extra_env=env)
    assert res.returncode == 3, res.stderr
    assert "sidecar" in res.stderr


def test_no_effort_flag_without_effort_arg(tmp_path: Path) -> None:
    s = Stand(tmp_path)
    assert s.run(*s.codex_args()).returncode == 0
    assert "--effort" not in s.argv.read_text(encoding="utf-8").splitlines()


def test_adapter_is_executable_in_git_tree() -> None:
    """Бит исполнения зафиксирован в ДЕРЕВЕ (100755), не в чекауте: адаптер
    запускается local.sh по абсолютному пути (D7), и потерянный при
    вендоринге бит ловит префлайт local.sh, а не copy-integrity (checksum
    сверяет байты)."""
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-s", "scripts/review/harness-claude"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out.startswith("100755 "), out
