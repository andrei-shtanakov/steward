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
import hashlib
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


def _run(argv: list[str], *, cwd: Path | None = None, timeout: int) -> Any:
    """`subprocess.run`, у которого зависание — значение, а не исключение.

    `TimeoutExpired` и `OSError` иначе улетали бы из `collect()` наверх и
    обрывали весь батч на первом же зависшем чекауте — вопреки заявленной
    независимости репозиториев, и тем заметнее, чем больше их станет.
    """
    try:
        return subprocess.run(  # noqa: S603 — фиксированный argv
            argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return None


def _git(path: Path, *args: str) -> str | None:
    proc = _run(["git", "-C", str(path), *args], timeout=30)
    if proc is None:
        return None
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


def _label(entry: Any) -> str:
    """Как назвать запись в отчёте, когда она может быть чем угодно.

    Считается ДО preflight: сам отчёт не должен падать на той же записи,
    про которую собирается сказать, что она негодна.
    """
    if isinstance(entry, dict) and isinstance(entry.get("repo"), str):
        return entry["repo"]
    return f"<негодная запись охвата: {entry!r}>"


def preflight(entry: dict[str, Any], workspace_root: Path) -> tuple[Path | None, str]:
    """Тот ли это чекаут. Возвращает (путь, причина отказа).

    Проверки идут до вызова продюсера и все до единой обязательны: неверный
    путь не должен «просто не сработать», он должен не дойти до записи.
    """
    if not isinstance(entry, dict):
        return None, f"элемент охвата не объект: {entry!r}"
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


def pr_numbers(entry: dict[str, Any]) -> tuple[list[int] | None, str]:
    """Номера PR из записи охвата, или причина отказа.

    `int(n)` без проверки давал два разных отказа, и оба тихие: `74.5`
    превращался в запрос PR `74` — спросили не тот объект, а отчёт сказал бы
    `published`; а `"abc"` выбрасывал `ValueError` посреди батча и обрывал
    остальные репозитории, вопреки объявленной независимости. Найдено
    codex-гейтом на PR #96.

    `bool` отвергается отдельно: `True` — это `int` в Python, и он молча стал
    бы запросом PR №1.
    """
    repo = entry.get("repo")
    prs = entry.get("prs")
    if not isinstance(prs, list) or not prs:
        return None, f"{repo}: пустой или отсутствующий список `prs`"
    numbers: list[int] = []
    for value in prs:
        if isinstance(value, bool) or not isinstance(value, int):
            return None, f"{repo}: номер PR должен быть целым, получено {value!r}"
        if value <= 0:
            return None, f"{repo}: номер PR должен быть положительным, получено {value}"
        numbers.append(value)
    return numbers, ""


#: Консольный скрипт `steward` из ТОГО ЖЕ окружения, что и текущий интерпретатор.
#: Вложенный `uv run` был бы той же ошибкой, от которой plist защищается
#: абсолютным путём к `uv`: PATH под launchd ненадёжен, и голый `uv` там не
#: находится — сбор молча переставал бы происходить.
STEWARD_BIN = Path(sys.executable).parent / "steward"


def run_producer(repo: str, repo_root: Path, policy: Path, prs: list[int]) -> tuple[int, str]:
    """Единственная точка вызова продюсера — тесты подменяют её целиком."""
    if not STEWARD_BIN.exists():
        return 127, f"нет {STEWARD_BIN} — раннер запущен вне окружения steward"
    proc = _run(
        [
            str(STEWARD_BIN),
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
        timeout=600,
    )
    if proc is None:
        return 124, "продюсер не завершился в отведённое время или не запустился"
    return proc.returncode, (proc.stderr or proc.stdout).strip()


def _resolved(
    repositories: list[Any], workspace_root: Path
) -> list[tuple[Any, str, Path | None, str]]:
    """Один обход охвата на оба прохода: сбор и проверка.

    Проверка дубликатов живёт ЗДЕСЬ, а не в `collect()`, потому что иначе два
    прохода расходятся: `--check` показывал бы зелёное на охвате, который сбор
    отвергает. Обещанное доказательство установки не должно быть зелёным,
    когда плановый сбор на том же охвате падает.

    Бандл лежит по фиксированному пути внутри чекаута, поэтому две записи на
    один чекаут — не двойной охват, а молчаливая потеря первого.
    """
    resolved: list[tuple[Any, str, Path | None, str]] = []
    seen: dict[str, str] = {}
    for entry in repositories:
        repo = _label(entry)
        path, refusal = preflight(entry, workspace_root)
        if path is not None:
            # Резолвим ПУТЬ БАНДЛА, а не только чекаут: `.steward` может быть
            # симлинком, и тогда публикация уезжает наружу, а два репозитория
            # пишут в один файл, оба выглядя успешными. Ключ дубликатов —
            # именно резолвнутый файл, а не каталог, который на него указывает.
            bundle = (path / BUNDLE_RELPATH).resolve()
            if not bundle.is_relative_to(path.resolve()):
                resolved.append(
                    (
                        entry,
                        repo,
                        None,
                        f"{repo}: бандл резолвится в {bundle} — вне чекаута {path}",
                    )
                )
                continue
            previous = seen.get(str(bundle))
            if previous is not None:
                resolved.append(
                    (
                        entry,
                        repo,
                        None,
                        f"{repo}: бандл {bundle} уже занят записью {previous} — второй "
                        f"затёр бы первый по тому же пути",
                    )
                )
                continue
            seen[str(bundle)] = repo
        resolved.append((entry, repo, path, refusal))
    return resolved


def collect(
    repositories: list[dict[str, Any]], workspace_root: Path, policy: Path
) -> list[Outcome]:
    """Пройти охват. Каждый репозиторий независим — по требованию, не по случаю.

    Отказ на одном не перенаправляет запись и не останавливает остальных: иначе
    один неверный чекаут делал бы ненаблюдаемым весь флот, а причина была бы
    видна только в хвосте лога.
    """
    outcomes: list[Outcome] = []
    for entry, repo, path, refusal in _resolved(repositories, workspace_root):
        if path is None:
            outcomes.append(Outcome(repo, "skipped", refusal))
            continue
        numbers, refusal = pr_numbers(entry)
        if numbers is None:
            outcomes.append(Outcome(repo, "skipped", refusal))
            continue
        bundle = path / BUNDLE_RELPATH
        # Дайджест, а не mtime: на ФС с секундной гранулярностью два прогона
        # подряд (`RunAtLoad` плюс рекомендованный `kickstart`) дают одинаковый
        # `st_mtime_ns`, и свежая публикация отвергалась бы ложно.
        before = _digest(bundle)
        lease_before, _ = bundle_header(bundle) if bundle.is_file() else (None, "")
        code, detail = run_producer(repo, path, policy, numbers)
        if code != 0:
            outcomes.append(Outcome(repo, "failed", f"продюсер вышел с кодом {code}: {detail}"))
            continue
        # Нулевой код — заявление продюсера, а не факт публикации. Если он
        # вернул 0, не записав бандл (ранний return, проглоченная ошибка ФС),
        # раннер отчитался бы `published`, и весь прогон вышел бы нулём при
        # том, что публикации не было.
        if not bundle.is_file():
            outcomes.append(Outcome(repo, "failed", f"код 0, но бандла нет: {bundle}"))
            continue
        after = _digest(bundle)
        valid_until, refusal = bundle_header(bundle)
        if valid_until is None:
            outcomes.append(Outcome(repo, "failed", f"код 0, но {refusal}"))
            continue
        if datetime.now(UTC) >= valid_until:
            # Продюсер мог записать уже протухший lease. Публикация, которая
            # родилась просроченной, — не публикация.
            outcomes.append(
                Outcome(repo, "failed", f"код 0, но бандл уже просрочен ({valid_until})")
            )
            continue
        if after == before and (lease_before is None or valid_until <= lease_before):
            # Ни байты, ни lease не сдвинулись — снимка не было. Старый бандл с
            # близким `valid_until` иначе удовлетворял бы всем прочим проверкам.
            # Достаточно ЛЮБОГО из двух признаков: содержимое обычно меняется
            # (в нём `generated_at`), а lease — гарантированно при новом окне.
            outcomes.append(
                Outcome(
                    repo,
                    "failed",
                    f"код 0, но бандл не изменился и lease не сдвинулся ({valid_until})",
                )
            )
            continue
        gap = bundle_gap(bundle, numbers)
        if gap is not None:
            outcomes.append(Outcome(repo, "failed", f"код 0, но {gap}"))
        else:
            outcomes.append(Outcome(repo, "published", str(bundle)))
    return outcomes


def _digest(path: Path) -> str | None:
    """sha256 содержимого, или None если файла нет."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def bundle_header(bundle: Path) -> tuple[datetime | None, str]:
    """Заголовок бандла и его lease, или причина, почему это не бандл.

    Живёт отдельно, потому что нужна ОБОИМ проходам: `collect()` как
    постусловие публикации, `--check` как проверка установки. Пока проверка
    была только в одном, продюсер, записавший `result`-строки без заголовка
    или с уже протухшим `valid_until`, проходил как `published`.
    """
    try:
        lines = bundle.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return None, f"бандл не читается: {exc}"
    if not lines:
        return None, "бандл пуст"
    try:
        header = json.loads(lines[0])
    except ValueError as exc:
        return None, f"заголовок не JSON: {exc}"
    if not isinstance(header, dict) or header.get("kind") != "header":
        return None, "первая строка не `kind: header`"
    raw = header.get("valid_until")
    try:
        valid_until = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        return None, f"`valid_until` нечитаем ({raw!r}): {exc}"
    if valid_until.tzinfo is None:
        return None, f"`valid_until` без часового пояса: {valid_until}"
    return valid_until, ""


def bundle_gap(bundle: Path, numbers: list[int]) -> str | None:
    """Чего из настроенного охвата нет в бандле — по ЗАПИСЯМ, а не по заявке.

    Сверять с `scope` в заголовке было слабее по двум причинам сразу, и обе
    нашлись машинным ревью:

    * заголовок объявляет, что спрашивали, но не доказывает, что ответ записан;
      обрезанный после заголовка файл читался бы зелёным без единого факта;
    * запись другого вида (`{"kind": "merge_sha", "value": "75"}`) засчитывалась
      бы как покрытие PR №75.

    Поэтому считаются `result`-записи с `request.kind == "pr"`. Заодно это
    постусловие публикации: продюсер, вернувший 0 и оставивший пустой или
    нечитаемый файл, больше не проходит как `published`.
    """
    try:
        lines = bundle.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return f"бандл не читается: {exc}"
    if not lines:
        return "бандл пуст"
    answered: set[str] = set()
    for number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except ValueError as exc:
            return f"строка {number} не JSON: {exc}"
        if not isinstance(record, dict):
            return f"строка {number} не объект"
        # Только `result`: строка вида `{"kind": "error", "request": {...}}`
        # иначе засчиталась бы как ответ по этому PR — зелёное без факта.
        if record.get("kind") != "result":
            continue
        request = record.get("request")
        if isinstance(request, dict) and request.get("kind") == "pr":
            answered.add(str(request.get("value")))
    missing = [n for n in numbers if str(n) not in answered]
    return f"в бандле нет записей по PR {missing}" if missing else None


def freshness(repositories: list[dict[str, Any]], workspace_root: Path) -> list[Outcome]:
    """Свежесть и полнота опубликованных бандлов — проверка установки.

    Существует потому, что `valid_until` делает молчание обнаружимым **при
    чтении**, а читателя пока нет: до появления потребителя посмотреть, что
    расписание живо, можно только глазами. Проверяется не только «не протух»,
    но и «покрывает то, что сейчас настроено».
    """
    outcomes: list[Outcome] = []
    now = datetime.now(UTC)
    for entry, repo, path, refusal in _resolved(repositories, workspace_root):
        if path is None:
            outcomes.append(Outcome(repo, "skipped", refusal))
            continue
        # Охват валидируется ПЕРВЫМ, как и в `collect()`: иначе негодная
        # строка охвата на репозитории без бандла доложилась бы поломкой
        # публикации, хотя спрашивать было нечего.
        numbers, refusal = pr_numbers(entry)
        if numbers is None:
            outcomes.append(Outcome(repo, "skipped", refusal))
            continue
        bundle = path / BUNDLE_RELPATH
        if not bundle.is_file():
            outcomes.append(Outcome(repo, "failed", f"бандла нет — {bundle}"))
            continue
        valid_until, refusal = bundle_header(bundle)
        if valid_until is None:
            outcomes.append(Outcome(repo, "failed", f"заголовок нечитаем: {refusal}"))
            continue
        if now >= valid_until:
            hours = int((now - valid_until).total_seconds() // 3600)
            outcomes.append(Outcome(repo, "failed", f"lease истёк {hours} ч назад ({valid_until})"))
        else:
            gap = bundle_gap(bundle, numbers)
            if gap is not None:
                outcomes.append(Outcome(repo, "failed", gap))
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
    # Резолвим до любого использования: проверка `is_file()` идёт в текущем
    # каталоге, а продюсер запускается с `cwd=REPO_ROOT` — относительный путь
    # означал бы там другой файл, и preflight был бы зелёным про не тот.
    scope_path = args.scope.resolve()
    policy_path = args.policy.resolve()

    try:
        repositories = _load_scope(scope_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"охват не прочитан: {exc}", file=sys.stderr)
        return 2

    if args.check:
        return report(freshness(repositories, args.workspace_root))
    if not policy_path.is_file():
        print(f"политики нет: {policy_path}", file=sys.stderr)
        return 2
    return report(collect(repositories, args.workspace_root, policy_path))


if __name__ == "__main__":
    raise SystemExit(main())
