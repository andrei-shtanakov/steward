"""Тесты scripts/review/checksum.sh — переносимая сверка вендор-копии с PIN."""

import hashlib
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "review" / "checksum.sh"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_kit(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    (root / "scripts" / "review").mkdir(parents=True)
    (root / "scripts" / "review" / "a.sh").write_text("echo a\n", encoding="utf-8")
    (root / "scripts" / "review" / "b.sh").write_text("echo b\n", encoding="utf-8")
    return root


def write_pin(root: Path, entries: list[str]) -> Path:
    pin = root / "scripts" / "review" / "PIN"
    pin.write_text("".join(f"{line}\n" for line in entries), encoding="utf-8")
    return pin


def pin_line(root: Path, rel: str) -> str:
    return f"{sha(root / rel)}  {rel}"


def run(root: Path, pin: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(SCRIPT), "--pin", str(pin)],
        capture_output=True,
        text=True,
        cwd=root,
    )


def test_matching_copy_is_green(tmp_path: Path) -> None:
    root = make_kit(tmp_path)
    pin = write_pin(
        root,
        [
            "# SOURCE: steward @ deadbeef",
            pin_line(root, "scripts/review/a.sh"),
            pin_line(root, "scripts/review/b.sh"),
        ],
    )
    result = run(root, pin)
    assert result.returncode == 0, result.stderr


def test_drifted_file_is_named_and_red(tmp_path: Path) -> None:
    """Расхождение — код 1 и ИМЯ файла: безымянное красное неотличимо от
    других отказов и отправляет человека не туда."""
    root = make_kit(tmp_path)
    pin = write_pin(
        root,
        [
            pin_line(root, "scripts/review/a.sh"),
            pin_line(root, "scripts/review/b.sh"),
        ],
    )
    (root / "scripts" / "review" / "b.sh").write_text("echo patched\n", "utf-8")
    result = run(root, pin)
    assert result.returncode == 1, result.stderr
    assert "scripts/review/b.sh" in result.stderr


def test_all_drifts_are_reported_not_only_first(tmp_path: Path) -> None:
    """Отчёт полный, не до первого расхождения: чинить копию по одному
    файлу за прогон — это N прогонов вместо одного."""
    root = make_kit(tmp_path)
    pin = write_pin(
        root,
        [
            pin_line(root, "scripts/review/a.sh"),
            pin_line(root, "scripts/review/b.sh"),
        ],
    )
    (root / "scripts" / "review" / "a.sh").write_text("x\n", encoding="utf-8")
    (root / "scripts" / "review" / "b.sh").write_text("y\n", encoding="utf-8")
    result = run(root, pin)
    assert result.returncode == 1
    assert "scripts/review/a.sh" in result.stderr
    assert "scripts/review/b.sh" in result.stderr


def test_missing_listed_file_is_red(tmp_path: Path) -> None:
    root = make_kit(tmp_path)
    pin = write_pin(
        root,
        [
            pin_line(root, "scripts/review/a.sh"),
            pin_line(root, "scripts/review/b.sh"),
        ],
    )
    (root / "scripts" / "review" / "b.sh").unlink()
    result = run(root, pin)
    assert result.returncode == 1
    assert "scripts/review/b.sh" in result.stderr


def test_extra_file_in_kit_dir_is_ignored_by_design(tmp_path: Path) -> None:
    """Проверяется ПЕРЕЧЕНЬ, не каталог (§5 спеки): install-hook.sh и
    настроенная копия промпта у потребителя — штатные соседи кита, «лишний
    файл» не должен спотыкать целостность в первый же день."""
    root = make_kit(tmp_path)
    pin = write_pin(root, [pin_line(root, "scripts/review/a.sh")])
    (root / "scripts" / "review" / "install-hook.sh").write_text("x\n", "utf-8")
    result = run(root, pin)
    assert result.returncode == 0, result.stderr


def test_malformed_pin_line_is_config_error(tmp_path: Path) -> None:
    """Битая строка PIN — код 2, не молчаливый пропуск: пропущенная строка
    это непроверенный файл, который выглядел бы проверенным."""
    root = make_kit(tmp_path)
    pin = write_pin(root, ["не-хеш и не комментарий"])
    result = run(root, pin)
    assert result.returncode == 2
    assert "PIN" in result.stderr


def test_missing_pin_is_config_error(tmp_path: Path) -> None:
    root = make_kit(tmp_path)
    result = run(root, root / "нет-PIN")
    assert result.returncode == 2


def test_empty_pin_is_config_error(tmp_path: Path) -> None:
    """Пустой PIN — «проверять нечего» как успех был бы fail-open: нулевая
    проверка неотличима от пройденной."""
    root = make_kit(tmp_path)
    pin = write_pin(root, ["# только комментарий"])
    result = run(root, pin)
    assert result.returncode == 2
