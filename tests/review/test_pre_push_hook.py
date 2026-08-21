"""Тесты .github/hooks/pre-push — контракт по ref'ам.

Хук читает stdin как настоящий git, поэтому тесты подают stdin, а не зовут
внутреннюю функцию: иначе тест закрепил бы форму разбора, а не поведение хука.
"""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".github" / "hooks" / "pre-push"
ZERO = "0" * 40


def run_hook(stdin: str, head_sha: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Запустить хук с подставным local.sh, который всегда зелёный."""
    stub_kit = tmp_path / "kit"
    stub_kit.mkdir(exist_ok=True)
    stub_local = stub_kit / "local.sh"
    stub_local.write_text("#!/bin/sh\necho 'ревью выполнено'\nexit 0\n", encoding="utf-8")
    stub_local.chmod(0o755)

    env = dict(os.environ)
    env["REVIEW_KIT_DIR"] = str(stub_kit)
    env["REVIEW_HEAD_SHA"] = head_sha
    return subprocess.run(
        ["sh", str(HOOK), "origin", "git@example.com:o/r.git"],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )


def test_single_branch_ref_at_head_runs_review(tmp_path: Path) -> None:
    sha = "a" * 40
    line = f"refs/heads/feature {sha} refs/heads/feature {ZERO}\n"
    result = run_hook(line, sha, tmp_path)
    assert result.returncode == 0
    assert "ревью выполнено" in result.stdout


def test_two_refs_are_unsupported(tmp_path: Path) -> None:
    """Пуш нескольких ref'ов проверил бы в лучшем случае один из них."""
    sha = "a" * 40
    stdin = (
        f"refs/heads/one {sha} refs/heads/one {ZERO}\n"
        f"refs/heads/two {'b' * 40} refs/heads/two {ZERO}\n"
    )
    result = run_hook(stdin, sha, tmp_path)
    assert result.returncode != 0
    assert "ревью выполнено" not in result.stdout
    assert "--no-verify" in result.stderr


def test_ref_whose_sha_is_not_head_is_unsupported(tmp_path: Path) -> None:
    """Иначе проверили бы одно дерево, а отправили другое."""
    line = f"refs/heads/other {'b' * 40} refs/heads/other {ZERO}\n"
    result = run_hook(line, "a" * 40, tmp_path)
    assert result.returncode != 0
    assert "--head" in result.stderr, "подсказка обязана быть исполнимой"
    assert "b" * 40 in result.stderr


def test_tag_is_unsupported(tmp_path: Path) -> None:
    sha = "a" * 40
    line = f"refs/tags/v1 {sha} refs/tags/v1 {ZERO}\n"
    result = run_hook(line, sha, tmp_path)
    assert result.returncode != 0


def test_branch_deletion_is_unsupported(tmp_path: Path) -> None:
    line = f"(delete) {ZERO} refs/heads/gone {'c' * 40}\n"
    result = run_hook(line, "a" * 40, tmp_path)
    assert result.returncode != 0


def test_empty_stdin_is_unsupported(tmp_path: Path) -> None:
    """Пустой stdin — не «нечего проверять»; форма не та, о которой договаривались."""
    result = run_hook("", "a" * 40, tmp_path)
    assert result.returncode != 0


def test_any_non_zero_from_local_blocks_the_push(tmp_path: Path) -> None:
    """Спека §8: блокирует ЛЮБОЙ неположительный исход, не только находки.

    Механический сбой ревьюера (3) обязан останавливать пуш наравне с
    находками (1) и ошибкой конфигурации (2). Иначе «инструмент не отработал»
    молча превратилось бы в «замечаний нет» — тот же класс, что чинили в #87.
    """
    sha = "a" * 40
    line = f"refs/heads/feature {sha} refs/heads/feature {ZERO}\n"
    for code in (1, 2, 3):
        stub_kit = tmp_path / f"kit-{code}"
        stub_kit.mkdir()
        stub_local = stub_kit / "local.sh"
        stub_local.write_text(f"#!/bin/sh\nexit {code}\n", encoding="utf-8")
        stub_local.chmod(0o755)
        env = dict(os.environ)
        env["REVIEW_KIT_DIR"] = str(stub_kit)
        env["REVIEW_HEAD_SHA"] = sha
        result = subprocess.run(
            ["sh", str(HOOK), "origin", "git@example.com:o/r.git"],
            input=line,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0, f"код {code} от local.sh обязан блокировать пуш"
