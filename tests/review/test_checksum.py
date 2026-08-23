"""Тесты scripts/review/checksum.sh — переносимая сверка вендор-копии с PIN."""

import hashlib
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "review" / "checksum.sh"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


KIT_FILES = [
    "scripts/review/build-prompt.sh",
    "scripts/review/collect-context.sh",
    "scripts/review/apply-threshold.sh",
    "scripts/review/local.sh",
    "scripts/review/checksum.sh",
    ".github/codex/review-schema.json",
]


def make_kit(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    for rel in KIT_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"содержимое {rel}\n", encoding="utf-8")
    return root


def full_pin(root: Path, extra: list[str] | None = None) -> Path:
    lines = ["# SOURCE: steward @ deadbeef"]
    lines += [pin_line(root, rel) for rel in KIT_FILES]
    lines += extra or []
    return write_pin(root, lines)


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
    pin = full_pin(root)
    result = run(root, pin)
    assert result.returncode == 0, result.stderr


def test_drifted_file_is_named_and_red(tmp_path: Path) -> None:
    """Расхождение — код 1 и ИМЯ файла: безымянное красное неотличимо от
    других отказов и отправляет человека не туда."""
    root = make_kit(tmp_path)
    pin = full_pin(root)
    (root / "scripts" / "review" / "local.sh").write_text("patched\n", "utf-8")
    result = run(root, pin)
    assert result.returncode == 1, result.stderr
    assert "scripts/review/local.sh" in result.stderr


def test_all_drifts_are_reported_not_only_first(tmp_path: Path) -> None:
    """Отчёт полный, не до первого расхождения: чинить копию по одному
    файлу за прогон — это N прогонов вместо одного."""
    root = make_kit(tmp_path)
    pin = full_pin(root)
    (root / "scripts" / "review" / "local.sh").write_text("x\n", encoding="utf-8")
    (root / "scripts" / "review" / "checksum.sh").write_text("y\n", "utf-8")
    result = run(root, pin)
    assert result.returncode == 1
    assert "scripts/review/local.sh" in result.stderr
    assert "scripts/review/checksum.sh" in result.stderr


def test_missing_listed_file_is_red(tmp_path: Path) -> None:
    root = make_kit(tmp_path)
    pin = full_pin(root)
    (root / "scripts" / "review" / "apply-threshold.sh").unlink()
    result = run(root, pin)
    assert result.returncode == 1
    assert "scripts/review/apply-threshold.sh" in result.stderr


def test_extra_file_in_kit_dir_is_ignored_by_design(tmp_path: Path) -> None:
    """Проверяется ПЕРЕЧЕНЬ, не каталог (§5 спеки): install-hook.sh и
    настроенная копия промпта у потребителя — штатные соседи кита, «лишний
    файл» не должен спотыкать целостность в первый же день."""
    root = make_kit(tmp_path)
    pin = full_pin(root)
    (root / "scripts" / "review" / "install-hook.sh").write_text("x\n", "utf-8")
    result = run(root, pin)
    assert result.returncode == 0, result.stderr


def test_malformed_pin_line_is_config_error(tmp_path: Path) -> None:
    """Битая строка PIN — код 2, не молчаливый пропуск: пропущенная строка
    это непроверенный файл, который выглядел бы проверенным."""
    root = make_kit(tmp_path)
    pin = full_pin(root, extra=["не-хеш и не комментарий"])
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


def test_subset_pin_not_covering_kit_is_config_error(tmp_path: Path) -> None:
    """PIN обязан покрывать ВЕСЬ состав кита из §5 — subset не проходит.

    Перечень (а не каталог) существует, чтобы исключать известных не-китовых
    соседей, а не чтобы позволять пропуски членов кита: PIN без checksum.sh
    оставлял бы подменённый файл непроверенным при зелёном исходе (major
    гейта на #101). Непокрытие — код 2: это негодный PIN, не дрейф копии."""
    root = make_kit(tmp_path)
    lines = ["# SOURCE: steward @ deadbeef"]
    lines += [pin_line(root, rel) for rel in KIT_FILES if not rel.endswith("checksum.sh")]
    pin = write_pin(root, lines)
    result = run(root, pin)
    assert result.returncode == 2, result.stderr
    assert "checksum.sh" in result.stderr


def test_unreadable_listed_file_is_config_error_not_drift(tmp_path: Path) -> None:
    """Нечитаемый файл из перечня — код 2, не «расхождение».

    Сломанный пайплайн хеширования давал пустой hash_actual, и файл, чьи
    байты сверить НЕВОЗМОЖНО, отчитывался как дрейф с советом ре-вендорить
    (minor гейта на #101) — ложный диагноз."""
    root = make_kit(tmp_path)
    pin = full_pin(root)
    target = root / "scripts" / "review" / "local.sh"
    target.chmod(0o000)
    try:
        unreadable = False
        try:
            target.read_text(encoding="utf-8")
        except PermissionError:
            unreadable = True
        if not unreadable:
            pytest.skip("chmod 000 не блокирует чтение под текущим пользователем/FS")
        result = run(root, pin)
    finally:
        target.chmod(0o644)
    assert result.returncode == 2, result.stderr
    assert "scripts/review/local.sh" in result.stderr
    assert "РАСХОЖДЕНИЕ" not in result.stderr


def test_decoy_paths_with_right_basenames_do_not_satisfy_inventory(
    tmp_path: Path,
) -> None:
    """Инвентарь — полные вендор-пути §5, не basename.

    PIN, указывающий на шесть обманок с правильными именами в чужих путях,
    проходил зелёным, пока настоящие вендор-пути дрейфовали (major третьего
    захода гейта на #101) — fail-open целостности. Раскладка кита не
    свободна: local.sh вычисляет соседей от своего каталога, схему — от
    .github/codex; смена раскладки = правка кита через ревью."""
    root = make_kit(tmp_path)
    decoy_dir = root / "vendor" / "decoys"
    decoy_dir.mkdir(parents=True)
    lines = []
    for rel in KIT_FILES:
        decoy = decoy_dir / Path(rel).name
        decoy.write_text((root / rel).read_text(encoding="utf-8"), "utf-8")
        rel_decoy = decoy.relative_to(root).as_posix()
        lines.append(f"{sha(decoy)}  {rel_decoy}")
    pin = write_pin(root, lines)
    (root / "scripts" / "review" / "local.sh").write_text("drift\n", "utf-8")

    result = run(root, pin)

    assert result.returncode == 2, result.stderr


def test_symlinked_kit_member_is_integrity_failure(tmp_path: Path) -> None:
    """Симлинк на месте члена кита — отказ целостности, даже при тех же байтах.

    `-f` разыменовывает симлинк, и хеш содержимого совпадал — структурно
    подменённый кит проходил зелёным (major четвёртого захода гейта на
    #101). Тот же класс, что проверка «обычный файл, не симлинк» при
    извлечении механики из base в CI."""
    root = make_kit(tmp_path)
    pin = full_pin(root)
    target = root / "scripts" / "review" / "local.sh"
    aside = root / "aside-copy.sh"
    aside.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    target.unlink()
    target.symlink_to(aside)

    result = run(root, pin)

    assert result.returncode == 1, result.stderr
    assert "scripts/review/local.sh" in result.stderr
    assert "симлинк" in result.stderr.lower()


def test_non_kit_entry_in_pin_is_config_error(tmp_path: Path) -> None:
    """Запись вне состава кита в PIN — негодная конфигурация, не дрейф.

    Настроенная копия review-prompt.md — данные репо вне copy-integrity по
    спеке; PIN с такой записью превращал легальную настройку в «дрейф кита,
    ре-вендорьте» (minor четвёртого захода). PIN — ровно канонический
    инвентарь §5, ни больше ни меньше."""
    root = make_kit(tmp_path)
    prompt = root / ".github" / "codex" / "review-prompt.md"
    prompt.write_text("настроенный промпт\n", encoding="utf-8")
    pin = full_pin(root, extra=[pin_line(root, ".github/codex/review-prompt.md")])

    result = run(root, pin)

    assert result.returncode == 2, result.stderr
    assert "review-prompt.md" in result.stderr
