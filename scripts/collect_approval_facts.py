#!/usr/bin/env python3
"""Регулярный сбор `approval-facts/v2` по явному охвату (Stage A0).

Продюсер (`steward approval-facts`) умеет спросить GitHub про один репозиторий.
Здесь — только маршрутизация: прочитать перечисленный охват, проверить, что
чекаут действительно тот, и позвать продюсера. **Никакой логики фактов и
никакой классификации** этот файл не содержит и содержать не должен: владелец
классификации — steward, и второй policy engine здесь завёлся бы незаметно.

Зачем вообще регулярно. У бандла в заголовке есть `valid_until` (lease 24 ч).
Он делает молчание расписания **обнаружимым при чтении** — но сам о молчании не
сообщает: выключенный ноутбук не отправит уведомление о том, что не проснулся.
Поэтому период обязан быть с запасом (6 ч при lease 24 ч), а проверка установки
(`--check`) существует, чтобы посмотреть свежесть глазами, пока не появился
потребитель, который отрендерит её сам.

Fail-closed, и не теоретически: у продюсера `--repo-root` имеет дефолт `.`, так
что вызов для репозитория без чекаута **записал бы его факты в бандл steward** —
подмена наблюдаемого объекта, которая выглядела бы как успешная публикация.
Раннер поэтому не полагается на дефолт никогда и отказывается раньше вызова.

Использование:

    uv run python scripts/collect_approval_facts.py --workspace-root ..
    uv run python scripts/collect_approval_facts.py --workspace-root .. --check

Коды выхода: ``0`` — опубликованы все; ``1`` — хоть один не опубликован
(провал или пропуск). Пропуск успехом не считается: репозиторий, которого не
спросили, не должен читаться зелёным.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCOPE = REPO_ROOT / "profiles" / "approval-facts-scope.yaml"
DEFAULT_POLICY = REPO_ROOT / "profiles" / "approval-policy.yaml"

#: Относительный путь бандла — тот же, по которому его читает потребитель
#: (`dispatcher/core/governance.py` использует ровно такую форму для соседнего
#: `gate_verdicts.jsonl`).
BUNDLE_RELPATH = Path(".steward") / "approval_facts.jsonl"


@dataclass(frozen=True, slots=True)
class Outcome:
    """Итог по одному репозиторию. `skipped` — не разновидность успеха."""

    repo: str
    status: str  # published | failed | skipped
    detail: str


def _load_scope(path: Path) -> list[dict[str, Any]]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path}: охват не объект")
    repositories = document.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise ValueError(f"{path}: пустой или отсутствующий `repositories`")
    return repositories


def _git(path: Path, *args: str) -> str | None:
    proc = subprocess.run(  # noqa: S603 S607 — фиксированный argv
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def _origin_slug(url: str) -> str | None:
    """`owner/name` из ssh- или https-формы remote'а."""
    trimmed = url.removesuffix(".git")
    if trimmed.startswith("git@"):
        _, _, tail = trimmed.partition(":")
    elif "://" in trimmed:
        tail = trimmed.split("://", 1)[1].partition("/")[2]
    else:
        return None
    parts = [p for p in tail.split("/") if p]
    return "/".join(parts[-2:]) if len(parts) >= 2 else None


def preflight(entry: dict[str, Any], workspace_root: Path) -> tuple[Path | None, str]:
    """Тот ли это чекаут. Возвращает (путь, причина отказа).

    Проверки идут до вызова продюсера и все до единой обязательны: неверный
    путь не должен «просто не сработать», он должен не дойти до записи.
    """
    repo = entry.get("repo")
    checkout = entry.get("checkout")
    if not isinstance(repo, str) or "/" not in repo:
        return None, f"в охвате нет корректного `repo`: {repo!r}"
    if not isinstance(checkout, str) or not checkout:
        return None, f"{repo}: в охвате нет `checkout`"

    path = (workspace_root / checkout).resolve()
    root = workspace_root.resolve()
    if not path.is_relative_to(root):
        return None, f"{repo}: чекаут {path} вне workspace-root {root}"
    if not path.is_dir():
        return None, f"{repo}: чекаута нет — {path}"
    if not (path / ".git").exists():
        return None, f"{repo}: {path} не git-корень"

    origin = _git(path, "remote", "get-url", "origin")
    if origin is None:
        return None, f"{repo}: у {path} нет origin"
    slug = _origin_slug(origin)
    if slug != repo:
        # Самая опасная из ошибок конфигурации: путь есть, git есть, а
        # наблюдали бы не тот объект — и бандл выглядел бы законным.
        return None, f"{repo}: origin чекаута — {slug!r}, а не {repo!r}"
    return path, ""


def run_producer(repo: str, repo_root: Path, policy: Path, prs: list[int]) -> tuple[int, str]:
    """Единственная точка вызова продюсера — тесты подменяют её целиком."""
    proc = subprocess.run(  # noqa: S603 S607 — фиксированный argv
        [
            "uv",
            "run",
            "steward",
            "approval-facts",
            "--repo",
            repo,
            "--repo-root",
            str(repo_root),
            "--policy",
            str(policy),
            "--prs",
            ",".join(str(n) for n in prs),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    return proc.returncode, (proc.stderr or proc.stdout).strip()


def collect(
    repositories: list[dict[str, Any]], workspace_root: Path, policy: Path
) -> list[Outcome]:
    """Пройти охват. Каждый репозиторий независим — по требованию, не по случаю.

    Отказ на одном не перенаправляет запись и не останавливает остальных: иначе
    один неверный чекаут делал бы ненаблюдаемым весь флот, а причина была бы
    видна только в хвосте лога.
    """
    outcomes: list[Outcome] = []
    for entry in repositories:
        repo = str(entry.get("repo"))
        path, refusal = preflight(entry, workspace_root)
        if path is None:
            outcomes.append(Outcome(repo, "skipped", refusal))
            continue
        prs = entry.get("prs")
        if not isinstance(prs, list) or not prs:
            outcomes.append(Outcome(repo, "skipped", f"{repo}: пустой список `prs`"))
            continue
        code, detail = run_producer(repo, path, policy, [int(n) for n in prs])
        if code == 0:
            outcomes.append(Outcome(repo, "published", str(path / BUNDLE_RELPATH)))
        else:
            outcomes.append(Outcome(repo, "failed", f"продюсер вышел с кодом {code}: {detail}"))
    return outcomes


def freshness(repositories: list[dict[str, Any]], workspace_root: Path) -> list[Outcome]:
    """Свежесть уже опубликованных бандлов — проверка установки расписания.

    Существует потому, что `valid_until` делает молчание обнаружимым **при
    чтении**, а читателя пока нет: до появления потребителя посмотреть, что
    расписание живо, можно только глазами.
    """
    outcomes: list[Outcome] = []
    now = datetime.now(UTC)
    for entry in repositories:
        repo = str(entry.get("repo"))
        path, refusal = preflight(entry, workspace_root)
        if path is None:
            outcomes.append(Outcome(repo, "skipped", refusal))
            continue
        bundle = path / BUNDLE_RELPATH
        if not bundle.is_file():
            outcomes.append(Outcome(repo, "failed", f"бандла нет — {bundle}"))
            continue
        try:
            header = json.loads(bundle.read_text(encoding="utf-8").splitlines()[0])
            valid_until = datetime.fromisoformat(str(header["valid_until"]).replace("Z", "+00:00"))
        except (ValueError, KeyError, IndexError) as exc:
            outcomes.append(Outcome(repo, "failed", f"заголовок нечитаем: {exc}"))
            continue
        if now >= valid_until:
            hours = int((now - valid_until).total_seconds() // 3600)
            outcomes.append(Outcome(repo, "failed", f"lease истёк {hours} ч назад ({valid_until})"))
        else:
            outcomes.append(Outcome(repo, "published", f"свеж до {valid_until}"))
    return outcomes


def report(outcomes: list[Outcome]) -> int:
    """Напечатать итог. Ненулевой код, если опубликовано не всё."""
    for status in ("published", "failed", "skipped"):
        named = [o for o in outcomes if o.status == status]
        if named:
            print(f"{status}: {len(named)}")
            for outcome in named:
                print(f"  {outcome.repo}: {outcome.detail}")
    unpublished = [o for o in outcomes if o.status != "published"]
    if unpublished:
        # Пропуск не считается успехом: репозиторий, которого не спросили,
        # не должен читаться зелёным — это то же «неизвестность как зелёное»,
        # против которого написан весь контракт.
        sys.stdout.flush()  # иначе итог на stderr обгоняет перечисление на stdout
        print(f"не опубликовано: {len(unpublished)} из {len(outcomes)}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        required=True,
        help="Корень воркспейса с чекаутами. Обязателен: cwd под launchd не тот, "
        "что в интерактивной сессии, и угадывать его нельзя.",
    )
    parser.add_argument("--scope", type=Path, default=DEFAULT_SCOPE)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Не собирать, а показать свежесть уже опубликованных бандлов.",
    )
    args = parser.parse_args(argv)

    try:
        repositories = _load_scope(args.scope)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"охват не прочитан: {exc}", file=sys.stderr)
        return 2

    if args.check:
        return report(freshness(repositories, args.workspace_root))
    if not args.policy.is_file():
        print(f"политики нет: {args.policy}", file=sys.stderr)
        return 2
    return report(collect(repositories, args.workspace_root, args.policy))


if __name__ == "__main__":
    raise SystemExit(main())
