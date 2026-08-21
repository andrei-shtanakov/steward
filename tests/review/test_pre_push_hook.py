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
    # На этом пути local_sha ещё не разобран (несколько строк — неоднозначно):
    # подсказка обязана не падать по set -u, а не молчать честно про то, что
    # подставить нечего.
    assert "unbound variable" not in result.stderr


def test_ref_whose_sha_is_not_head_is_unsupported(tmp_path: Path) -> None:
    """Иначе проверили бы одно дерево, а отправили другое."""
    line = f"refs/heads/other {'b' * 40} refs/heads/other {ZERO}\n"
    result = run_hook(line, "a" * 40, tmp_path)
    assert result.returncode != 0
    # Не порознь "--head" и sha где-то в выводе — а ОДНА исполнимая строка:
    # копипастнуть можно сразу, не подставляя ничего руками.
    assert f"--head {'b' * 40} [--base <ref>]" in result.stderr, (
        "подсказка обязана быть одной исполнимой строкой с настоящим sha"
    )
    assert "unbound variable" not in result.stderr, "не должна падать по чужой причине"


def test_tag_is_unsupported(tmp_path: Path) -> None:
    """У тега тоже есть настоящий sha — подсказка обязана его подставить,
    хоть путь блокировки и другой (не ветка, а не расхождение с HEAD)."""
    sha = "a" * 40
    line = f"refs/tags/v1 {sha} refs/tags/v1 {ZERO}\n"
    result = run_hook(line, sha, tmp_path)
    assert result.returncode != 0
    assert f"--head {sha} [--base <ref>]" in result.stderr


def test_branch_deletion_is_unsupported(tmp_path: Path) -> None:
    """Настоящий git на удалении шлёт литеральный `(delete)` вместо
    `refs/heads/*` как local ref — блокируется веткой "не ветка", не
    отдельным стражем на нулевой sha (такого стража больше нет: он был
    недостижим, см. находку 4 финального ревью). Подсказка не должна врать,
    предлагая «просмотреть» несуществующее дерево из нулевого sha."""
    line = f"(delete) {ZERO} refs/heads/gone {'c' * 40}\n"
    result = run_hook(line, "a" * 40, tmp_path)
    assert result.returncode != 0
    assert "отправляется не ветка ((delete))" in result.stderr
    assert f"--head {ZERO}" not in result.stderr
    assert "unbound variable" not in result.stderr


def test_empty_stdin_is_green_nothing_to_push(tmp_path: Path) -> None:
    """Ноль ссылок на stdin — git включает ref тогда и только тогда, когда
    old_sha != new_sha, значит по проводу не идёт ничего и подменять
    проверкой нечего. Зелёный ранний выход, а не unsupported-форма."""
    result = run_hook("", "a" * 40, tmp_path)
    assert result.returncode == 0
    assert "ревью выполнено" not in result.stdout, "local.sh не должен вызываться"
    assert "unbound variable" not in result.stderr


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


# --- живой прогон: настоящий git push, а не сфабрикованный stdin -----------
#
# Всё выше проверяет реакцию хука на stdin, который ФОРМИРУЮТ ТЕСТЫ по
# описанию из брифа. Если это описание неверно, все эти тесты остаются
# зелёными, а хук в бою сломан — единственное место, где сходится сам
# контракт задачи («правильно разобрать то, что реально шлёт git»), не
# проверено ничем. Ниже — настоящий git push через хук, установленный
# настоящим install-hook.sh, без подмены stdin и без REVIEW_HEAD_SHA: HEAD
# берётся хуком через реальный `git rev-parse HEAD` внутри настоящего репо.

INSTALLER = ROOT / "scripts" / "review" / "install-hook.sh"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def make_bare_remote_and_clone(tmp_path: Path) -> tuple[Path, Path]:
    """Голый remote + чистый клон: без denyCurrentBranch-ловушек обычного репо."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(remote)], check=True)

    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(remote), str(seed)], check=True, capture_output=True)
    git(seed, "config", "user.email", "t@t")
    git(seed, "config", "user.name", "t")
    (seed / "base.txt").write_text("base\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "base")
    git(seed, "push", "-q", "origin", "master")

    local = tmp_path / "local"
    subprocess.run(["git", "clone", "-q", str(remote), str(local)], check=True, capture_output=True)
    git(local, "config", "user.email", "t@t")
    git(local, "config", "user.name", "t")
    return remote, local


def install_hook_via_installer(local: Path) -> None:
    """Установить хук РОВНО тем же install-hook.sh, что получит разработчик.

    В local нет .github/hooks/pre-push (это одноразовый тестовый клон, не
    чекаут steward) — кладём туда копию реального хука, как если бы local был
    таким чекаутом, и дальше install-hook.sh сам решает, куда его ставить.
    """
    hooks_src_dir = local / ".github" / "hooks"
    hooks_src_dir.mkdir(parents=True, exist_ok=True)
    dest = hooks_src_dir / "pre-push"
    dest.write_text(HOOK.read_text(encoding="utf-8"), encoding="utf-8")
    dest.chmod(0o755)

    result = subprocess.run(["sh", str(INSTALLER)], cwd=str(local), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (local / ".git" / "hooks" / "pre-push").is_file()


def make_stub_kit(tmp_path: Path, name: str, body: str) -> Path:
    kit = tmp_path / name
    kit.mkdir()
    local_sh = kit / "local.sh"
    local_sh.write_text(body, encoding="utf-8")
    local_sh.chmod(0o755)
    return kit


STUB_GREEN = "#!/bin/sh\ncat > /dev/null\necho 'ревью выполнено'\nexit 0\n"
STUB_WOULD_BLOCK = "#!/bin/sh\ncat > /dev/null\necho 'не должен был вызываться' >&2\nexit 3\n"


def test_real_single_branch_push_is_reviewed_and_succeeds(tmp_path: Path) -> None:
    """Настоящий `git push` одной новой ветки — хук вызван git'ом, отработал,
    пропустил. Проверяет то же условие 3 (local sha == HEAD), но на реальном
    stdin от git, а не на сфабрикованном тестом."""
    _, local = make_bare_remote_and_clone(tmp_path)
    install_hook_via_installer(local)
    kit = make_stub_kit(tmp_path, "kit-green", STUB_GREEN)

    git(local, "switch", "-qc", "feature")
    (local / "work.txt").write_text("работа\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")

    env = dict(os.environ)
    env["REVIEW_KIT_DIR"] = str(kit)
    result = subprocess.run(
        ["git", "-C", str(local), "push", "origin", "feature"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "ревью выполнено" in (result.stdout + result.stderr)


def test_real_two_new_refs_in_one_push_is_blocked(tmp_path: Path) -> None:
    """Настоящий `git push origin alpha beta` — ОБЕ ссылки новые, значит git
    реально передаёт хуку 2 строки за один вызов (если бы одна из веток уже
    была на remote в том же состоянии, git не включил бы её в stdin вовсе —
    поэтому обе здесь свежесозданные)."""
    _, local = make_bare_remote_and_clone(tmp_path)
    install_hook_via_installer(local)
    kit = make_stub_kit(tmp_path, "kit-green", STUB_GREEN)

    git(local, "switch", "-qc", "alpha")
    (local / "a.txt").write_text("a\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "alpha")

    git(local, "switch", "-qc", "beta")
    (local / "b.txt").write_text("b\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "beta")

    env = dict(os.environ)
    env["REVIEW_KIT_DIR"] = str(kit)
    result = subprocess.run(
        ["git", "-C", str(local), "push", "origin", "alpha", "beta"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode != 0
    assert "ревью выполнено" not in result.stdout
    assert "--no-verify" in result.stderr


def test_real_no_verify_skips_the_hook_entirely(tmp_path: Path) -> None:
    """`git push --no-verify` — штатный обход. kit гарантированно уронил бы
    пуш, если бы хук вообще был вызван; пуш обязан пройти."""
    _, local = make_bare_remote_and_clone(tmp_path)
    install_hook_via_installer(local)
    kit = make_stub_kit(tmp_path, "kit-would-block", STUB_WOULD_BLOCK)

    git(local, "switch", "-qc", "feature")
    (local / "work.txt").write_text("работа\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")

    env = dict(os.environ)
    env["REVIEW_KIT_DIR"] = str(kit)
    result = subprocess.run(
        ["git", "-C", str(local), "push", "--no-verify", "origin", "feature"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "не должен был вызываться" not in result.stderr


def test_real_repeat_push_with_nothing_to_send_is_green(tmp_path: Path) -> None:
    """Настоящий повторный `git push` той же ветки без новых коммитов — git
    печатает `Everything up-to-date` и НЕ включает ref в stdin хука (old_sha
    == new_sha). До этой ветки такой пуш выходил с 0; находка 3 финального
    ревью — что он стал падать как unsupported с "получено 0". Кит не должен
    даже вызываться: `local.sh`, гарантированно роняющий пуш, обязан пройти
    незамеченным."""
    _, local = make_bare_remote_and_clone(tmp_path)
    install_hook_via_installer(local)

    git(local, "switch", "-qc", "feature")
    (local / "work.txt").write_text("работа\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")

    # Первый пуш — реально новая ссылка, ей есть что провизьюить: пускаем
    # через зелёный стенд, чтобы ветка действительно легла на remote.
    green_kit = make_stub_kit(tmp_path, "kit-green", STUB_GREEN)
    env = dict(os.environ)
    env["REVIEW_KIT_DIR"] = str(green_kit)
    landed = subprocess.run(
        ["git", "-C", str(local), "push", "origin", "feature"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert landed.returncode == 0, landed.stderr

    # Повтор без новых коммитов: git не включит ref в stdin вовсе (old_sha ==
    # new_sha). Стенд гарантированно уронил бы пуш, если бы хук его вызвал —
    # значит зелёный код здесь доказывает, что local.sh не был вызван.
    kit = make_stub_kit(tmp_path, "kit-would-block", STUB_WOULD_BLOCK)
    env["REVIEW_KIT_DIR"] = str(kit)
    repeat = subprocess.run(
        ["git", "-C", str(local), "push", "origin", "feature"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert repeat.returncode == 0, repeat.stderr
    assert "не должен был вызываться" not in repeat.stderr
    assert "up-to-date" in (repeat.stdout + repeat.stderr).lower()


def test_real_push_origin_head_is_reviewed_and_succeeds(tmp_path: Path) -> None:
    """Настоящий `git push origin HEAD` — повседневная форма, git подставляет
    ЛИТЕРАЛЬНУЮ строку `HEAD` как local ref, не имя ветки. До этой правки
    хук отвергал её как "не ветка", хотя local sha честно совпадает с HEAD —
    ровно безопасный случай, ради которого написан весь контракт."""
    _, local = make_bare_remote_and_clone(tmp_path)
    install_hook_via_installer(local)
    kit = make_stub_kit(tmp_path, "kit-green", STUB_GREEN)

    git(local, "switch", "-qc", "feature")
    (local / "work.txt").write_text("работа\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")

    env = dict(os.environ)
    env["REVIEW_KIT_DIR"] = str(kit)
    result = subprocess.run(
        ["git", "-C", str(local), "push", "origin", "HEAD"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "ревью выполнено" in (result.stdout + result.stderr)


def test_real_push_origin_head_to_a_tag_is_blocked(tmp_path: Path) -> None:
    """Регресс от предыдущей правки: `git push origin HEAD:refs/tags/v1`
    даёт local_ref=HEAD (безопасная форма, принятая ради `git push origin
    HEAD`) и remote_ref=refs/tags/v1 — тег ушёл бы на remote непровизьюенным,
    хотя пуш тега заявлен неподдержанным (§8.1) и README обещает его
    блокировку. Кит подставлен STUB_WOULD_BLOCK: если бы хук всё же вызвал
    local.sh, пуш точно упал бы, и мы не отличили бы "заблокирован формой" от
    "заблокирован китом"."""
    _, local = make_bare_remote_and_clone(tmp_path)
    install_hook_via_installer(local)
    kit = make_stub_kit(tmp_path, "kit-would-block", STUB_WOULD_BLOCK)

    git(local, "switch", "-qc", "feature")
    (local / "work.txt").write_text("работа\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")

    env = dict(os.environ)
    env["REVIEW_KIT_DIR"] = str(kit)
    result = subprocess.run(
        ["git", "-C", str(local), "push", "origin", "HEAD:refs/tags/v1"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode != 0
    assert "не должен был вызываться" not in result.stderr
    assert "--no-verify" in result.stderr


def test_real_push_origin_head_to_a_branch_still_succeeds(tmp_path: Path) -> None:
    """Контрольный к находке выше: `git push origin HEAD:feature` — тоже
    local_ref=HEAD, но remote_ref=refs/heads/feature — обязан по-прежнему
    проходить, страж на remote_ref не должен зацепить безопасный случай."""
    _, local = make_bare_remote_and_clone(tmp_path)
    install_hook_via_installer(local)
    kit = make_stub_kit(tmp_path, "kit-green", STUB_GREEN)

    git(local, "switch", "-qc", "feature")
    (local / "work.txt").write_text("работа\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")

    env = dict(os.environ)
    env["REVIEW_KIT_DIR"] = str(kit)
    result = subprocess.run(
        ["git", "-C", str(local), "push", "origin", "HEAD:feature"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "ревью выполнено" in (result.stdout + result.stderr)
