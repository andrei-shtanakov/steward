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

Допущение об области определения, названное явно
------------------------------------------------

Stage A0 — один репозиторий, на одной машине, в воркспейсе, которым владеет
оператор. Чекауты предполагаются **не враждебными**, и из этого выведено ровно
три отказа от защиты — не больше, чем на самом деле:

* **hardlink бандла не проверяется.** Два чекаута, чьи файлы бандла жёстко
  связаны, оба отчитаются об успехе.
* **containment проверяется ДО прогона и не перепроверяется после.** Подмена
  ``.steward`` симлинком во время работы продюсера не будет замечена.
* **ssh-алиасы не разрешаются.** Хост origin обязан быть настоящим хостом
  GitHub; алиас из ``~/.ssh/config`` отвергается, хотя мог указывать туда же.

Что осталось и работает, чтобы текст не расходился с кодом: путь бандла обязан
лежать внутри чекаута (``_inside``, до прогона), повторные записи охвата
ловятся по идентичности каталога в ФС (``_identity`` — она же покрывает
регистровые и «./» алиасы), хост origin сверяется со списком GitHub.

Причина не в лени, а в том, что каждая такая защита живёт в двух местах —
в сборе и в проверке, — и обязана с собой согласовываться. За время работы над
этим раннером расхождение двух проходов случалось пять раз, и каждый раз
следствие было одинаковым: обещанное доказательство установки зелёное, а
плановый сбор на том же охвате падает. То есть парные защиты сами порождали
дефект того же класса, который должны были предотвращать.

Что защищается по-прежнему — данные, а не топология: бандл обязан заявлять
свой репозиторий, нести читаемый заголовок с непросроченным lease, содержать
ответы по каждому запрошенному PR, лежать внутри чекаута и быть результатом
именно этого прогона. Когда появится настоящий многорепный коллектор (Stage B,
чужие машины, общая ФС), у него будет и настоящая модель угроз — тогда защиты
вернутся вместе с ней.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

# Раннер НЕ разбирает бандл сам — он спрашивает читателя контракта. История
# поучительная: сперва тут была своя проверка «есть ли scope_sha256», потом
# переиспользованный scope_digest, и всё равно рядом жил параллельный разбор
# записей — который считал покрытием `state: "bogus"` и строковый `value`,
# то есть зеленел на файле, который `load_facts` отверг бы fail-closed
# (найдено первым зрячим прогоном codex-ревью). Вопрос «примет ли потребитель
# этот бандл» имеет ровно один честный ответ: спросить код потребителя.
from steward.approvalfacts.publish import ConfigError as OriginError
from steward.approvalfacts.publish import parse_origin
from steward.approvalfacts.reader import UnreadableFacts, load_facts
from steward.gatecheck.approval import PolicyError, load_approval_policy, policy_digest


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


#: `git` по абсолютному пути. Голое имя было бы ровно той ошибкой, от которой
#: plist защищается абсолютным `uv`: PATH под launchd не тот, что в оболочке.
#: `/usr/bin/git` на macOS даёт Command Line Tools, поэтому он и запасной.
GIT_BIN = shutil.which("git") or "/usr/bin/git"


def _git(path: Path, *args: str) -> str | None:
    proc = _run([GIT_BIN, "-C", str(path), *args], timeout=30)
    if proc is None:
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


#: Продюсер спрашивает GraphQL GitHub, поэтому наблюдаемым может быть только
#: чекаут, чей `origin` там и живёт. Зеркало на другом хосте с тем же
#: `owner/name` — не тот же объект: в этом воркспейсе такое есть (atp-platform
#: держит GitHub и GitLab-зеркало), и совпадение имени приняло бы зеркало за
#: оригинал.
#: `ssh.github.com` — штатный хост GitHub для SSH поверх 443, которым люди
#: пользуются из-за корпоративных фаерволов. Не включить его значило бы
#: объявить исправный чекаут зеркалом.
def _resolve(path: Path) -> tuple[Path | None, str]:
    """`Path.resolve()`, у которого отказ — значение, а не исключение.

    Циклический симлинк, недоступный родитель, слишком длинная цепочка — всё
    это `OSError`, и он улетал бы из обхода наверх, обрывая остальные
    репозитории вопреки объявленной независимости.
    """
    try:
        return path.resolve(strict=False), ""
    except OSError as exc:
        return None, f"путь не резолвится ({path}): {exc}"


def _inside(bundle: Path, checkout: Path) -> str | None:
    """Причина, по которой бандл нельзя писать в этот чекаут, или None."""
    resolved_bundle, refusal = _resolve(bundle)
    if resolved_bundle is None:
        return refusal
    resolved_checkout, refusal = _resolve(checkout)
    if resolved_checkout is None:
        return refusal
    if not resolved_bundle.is_relative_to(resolved_checkout):
        return f"бандл резолвится в {resolved_bundle} — вне чекаута {resolved_checkout}"
    return None


def _identity(path: Path) -> str:
    """Устойчивый ключ каталога: (устройство, инод), с откатом на строку.

    Строковое сравнение путей ошибается на регистре, симлинках и хардлинках;
    инод не ошибается ни на чём из этого. Откат нужен на случай ФС без
    осмысленных инодов — там лучше строка, чем исключение.
    """
    try:
        stat = path.stat()
    except OSError:
        return f"path:{path}"
    return f"ino:{stat.st_dev}:{stat.st_ino}"


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

    path, refusal = _resolve(workspace_root / checkout)
    if path is None:
        return None, f"{repo}: {refusal}"
    root, refusal = _resolve(workspace_root)
    if root is None:
        return None, f"{repo}: {refusal}"
    if not path.is_relative_to(root):
        return None, f"{repo}: чекаут {path} вне workspace-root {root}"
    if not path.is_dir():
        return None, f"{repo}: чекаута нет — {path}"
    if not (path / ".git").exists():
        return None, f"{repo}: {path} не git-корень"

    origin = _git(path, "remote", "get-url", "origin")
    if origin is None:
        return None, f"{repo}: у {path} нет origin"
    # Разбор origin — ТЕМ ЖЕ кодом, которым его разберёт продюсер
    # (`publish.py::parse_origin`), и это не удобство, а условие смысла всей
    # проверки: собственный, более терпимый разбор (принимавший
    # `ssh.github.com`, `www.`, `user@`, суффиксные пути) пропускал чекауты,
    # на которых `steward approval-facts` затем вечно падал кодом 2 — то есть
    # `--check` зеленел про установку, которую плановый сбор не может обновить
    # никогда. Ssh-алиасы из `~/.ssh/config` не проходят по той же причине:
    # их не принимает продюсер. Лекарство у оператора одно — прописать в
    # `origin` каноническую форму GitHub.
    try:
        owner, name = parse_origin(origin)
    except OriginError:
        return None, (
            f"{repo}: origin {origin!r} не в форме, которую принимает продюсер "
            f"(git@github.com:, ssh://git@github.com/ или https://github.com/); "
            f"факты берутся из GitHub, пропишите канонический origin"
        )
    slug = f"{owner}/{name}"
    # GitHub не различает регистр в слагах, поэтому и мы не должны: иначе
    # `Andrei-Shtanakov/Steward` вечно числился бы чужим.
    if slug.lower() != repo.lower():
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
        if value in numbers:
            # Продюсер (`parse_scope`) отвергает дублирующийся охват целиком:
            # пропустить дубль здесь значило бы зеленеть `--check`'ом про
            # расписание, каждый прогон которого падает кодом 2.
            return None, f"{repo}: PR {value} повторяется в охвате — продюсер такой scope отвергнет"
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
) -> list[tuple[str, Path | None, list[int], str]]:
    """Один обход охвата на оба прохода: сбор и проверка.

    Проверка дубликатов живёт ЗДЕСЬ, а не в `collect()`, потому что иначе два
    прохода расходятся: `--check` показывал бы зелёное на охвате, который сбор
    отвергает. Обещанное доказательство установки не должно быть зелёным,
    когда плановый сбор на том же охвате падает.

    Бандл лежит по фиксированному пути внутри чекаута, поэтому две записи на
    один чекаут — не двойной охват, а молчаливая потеря первого.
    """
    resolved: list[tuple[str, Path | None, list[int], str]] = []
    seen: dict[str, str] = {}
    for entry in repositories:
        repo = _label(entry)
        numbers: list[int] = []
        path, refusal = preflight(entry, workspace_root)
        if path is not None:
            # Резолвим ПУТЬ БАНДЛА, а не только чекаут: `.steward` может быть
            # симлинком, и тогда публикация уезжает наружу, а два репозитория
            # пишут в один файл, оба выглядя успешными. Ключ дубликатов —
            # именно резолвнутый файл, а не каталог, который на него указывает.
            escape = _inside(path / BUNDLE_RELPATH, path)
            if escape is not None:
                resolved.append((repo, None, [], f"{repo}: {escape}"))
                continue
            bundle, _ = _resolve(path / BUNDLE_RELPATH)
            # Ключ — идентичность каталога в ФС, а не строка пути. На
            # case-insensitive томе (APFS по умолчанию) `steward` и `Steward`
            # резолвятся в РАЗНЫЕ строки, но в один каталог, и вторая запись
            # тихо затирала бы первую. `os.path.normcase` тут не помогает: на
            # POSIX это тождественная функция, она понижает регистр только на
            # Windows — защита выглядела бы работающей и не работала.
            # Путь бандла внутри чекаута фиксирован, поэтому идентичности
            # каталога достаточно.
            # Валидация охвата ДО занятия слота: битая первая запись иначе
            # застолбила бы чекаут и подавила рабочую вторую для того же пути.
            numbers, refusal_prs = pr_numbers(entry)
            if numbers is None:
                resolved.append((repo, None, [], refusal_prs))
                continue
            key = _identity(path)
            previous = seen.get(key)
            if previous is not None:
                resolved.append(
                    (
                        repo,
                        None,
                        [],
                        f"{repo}: бандл {bundle} уже занят записью {previous} — второй "
                        f"затёр бы первый по тому же пути",
                    )
                )
                continue
            seen[key] = repo
        resolved.append((repo, path, numbers, refusal))
    return resolved


def _publish_one(
    repo: str,
    path: Path,
    bundle: Path,
    policy: Path,
    numbers: list[int],
    active_digest: str,
    lease_seconds: int,
) -> Outcome:
    """Опубликовать один бандл и доказать, что публикация состоялась.

    Все проверки после нулевого кода — про доказательство, а не про вежливость:
    нулевой код это заявление продюсера, а не факт. Само доказательство — у
    `bundle_verdict()`, то есть у читателя контракта; здесь остаётся только то,
    чего у читателя быть не может: сдвинулся ли файл относительно ДО-состояния.
    """
    # Дайджест, а не mtime: на ФС с секундной гранулярностью два прогона подряд
    # (`RunAtLoad` плюс рекомендованный `kickstart`) дают одинаковый
    # `st_mtime_ns`, и свежая публикация отвергалась бы ложно.
    before = _digest(bundle)
    # До-состояние читается ТЕРПИМО (`bundle_header`, только `valid_until`), а
    # не читателем контракта: прежний файл может быть битым или под старой
    # политикой, и это не причина отказать НОВОЙ публикации.
    lease_before, _ = bundle_header(bundle) if bundle.is_file() else (None, "")

    code, detail = run_producer(repo, path, policy, numbers)
    if code != 0:
        return Outcome(repo, "failed", f"продюсер вышел с кодом {code}: {detail}")
    if not bundle.is_file():
        return Outcome(repo, "failed", f"код 0, но бандла нет: {bundle}")

    after = _digest(bundle)
    valid_until, refusal = bundle_verdict(
        bundle, repo, numbers, active_digest, lease_seconds, datetime.now(UTC)
    )
    if valid_until is None:
        return Outcome(repo, "failed", f"код 0, но {refusal}")
    if after == before and (lease_before is None or valid_until <= lease_before):
        # Ни байты, ни lease не сдвинулись — снимка не было. Достаточно ЛЮБОГО
        # из двух признаков: содержимое обычно меняется (в нём `generated_at`),
        # а lease — гарантированно при новом окне.
        #
        # НАЗВАННОЕ ОКНО ЛОЖНОГО ОТКАЗА (найдено машинным ревью, оставлено
        # сознательно): `generated_at` в контракте усечён до секунды, поэтому
        # честный повтор в ту же секунду с теми же ответами даёт побайтово тот
        # же файл — и неотличим от продюсера, который не писал вовсе. Признак
        # вроде mtime не решает, а переворачивает ошибку: продюсер, тронувший
        # старый живой файл БЕЗ нового снапшота, стал бы `published` — ложное
        # зелёное вместо ложного красного. Здесь выбран fail-closed: редкий
        # повтор в ту же секунду виден как отказ и лечится перезапуском.
        return Outcome(
            repo, "failed", f"код 0, но бандл не изменился и lease не сдвинулся ({valid_until})"
        )
    return Outcome(repo, "published", str(bundle))


def collect(
    repositories: list[dict[str, Any]], workspace_root: Path, policy: Path
) -> list[Outcome]:
    """Пройти охват. Каждый репозиторий независим — по требованию, не по случаю.

    Отказ на одном не перенаправляет запись и не останавливает остальных: иначе
    один неверный чекаут делал бы ненаблюдаемым весь флот, а причина была бы
    видна только в хвосте лога.
    """
    # Политика читается один раз и fail-closed ДО обхода: с нечитаемой или
    # невалидной политикой нечего доказывать ни по одному репозиторию — это
    # ошибка конфигурации вызова (PolicyError наверх, exit 2 в main), а не
    # независимый отказ каждого чекаута.
    active_digest = policy_digest(policy)
    lease_seconds = load_approval_policy(policy).approval_facts_lease_seconds

    outcomes: list[Outcome] = []
    for repo, path, numbers, refusal in _resolved(repositories, workspace_root):
        if path is None:
            outcomes.append(Outcome(repo, "skipped", refusal))
            continue
        bundle = path / BUNDLE_RELPATH
        lock, refusal, status = _claim(bundle)
        if lock is None:
            # Параллельный прогон по тому же чекауту создаёт моя же инструкция:
            # `launchctl load` запускает задачу по `RunAtLoad`, а следом
            # рекомендуется `kickstart`. Без замка чужая публикация засчиталась
            # бы этому прогону — «файл изменился» не доказывает, кто его писал.
            outcomes.append(Outcome(repo, status, f"{repo}: {refusal}"))
            continue
        try:
            outcomes.append(
                _publish_one(repo, path, bundle, policy, numbers, active_digest, lease_seconds)
            )
        finally:
            lock.unlink(missing_ok=True)
    return outcomes


def _claim(bundle: Path) -> tuple[Path | None, str, str]:
    """Взять эксклюзивный замок на публикацию в этот бандл, или None.

    `O_EXCL` — атомарное «создал я»: второй прогон получает отказ, а не
    догадку. Замок снимается по выходу процесса не сам, поэтому устаревший
    (старше часа) перехватывается: иначе убитый прогон заблокировал бы чекаут
    навсегда, и расписание молча перестало бы работать.
    """
    lock = bundle.with_suffix(bundle.suffix + ".lock")
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        if lock.exists() and time.time() - lock.stat().st_mtime > 3600:
            lock.unlink(missing_ok=True)
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # Кто-то уже спрашивает — мы не спрашивали, это `skipped`.
        return None, f"по {bundle} уже идёт другой прогон", "skipped"
    except OSError as exc:
        # Каталог недоступен на запись, диск полон, `stat`/`unlink` не удались.
        # Раньше всё это докладывалось как параллельный прогон: оператор искал
        # бы несуществующий второй процесс, а расписание стояло бы неизвестно
        # сколько.
        # А это поломка инструмента: спросить не смогли. `skipped` здесь
        # означал бы «не спрашивали», и отказ читался бы как решение.
        return None, f"замок {lock} не взять: {exc}", "failed"
    os.close(handle)
    return lock, "", ""


def _digest(path: Path) -> str | None:
    """sha256 содержимого, или None если файла нет."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def bundle_verdict(
    bundle: Path,
    repo: str,
    numbers: list[int],
    active_digest: str,
    lease_seconds: int,
    now: datetime,
) -> tuple[datetime | None, str]:
    """Примет ли этот бандл потребитель — `(valid_until, "")` или `(None, почему нет)`.

    Здесь НЕТ собственного разбора бандла, и это главный инвариант функции.
    Прежние `bundle_repository()`/`bundle_gap()` разбирали файл сами — и первый
    же зрячий прогон codex-ревью нашёл, чем это кончается: `state: "bogus"` и
    строковый `value` считались покрытием, разнотипный `scope` ронял весь обход
    TypeError'ом из сортировки, а бандл под уже сменившейся политикой числился
    `published`, хотя гейт вернул бы `policy_digest_mismatch`. Все четыре —
    один дефект: параллельная реализация чтения, которая мягче настоящей.

    Поэтому лесенка повторяет `resolve_facts()` потребителя
    (`gatecheck/approval.py`) ЕГО ЖЕ кодом: `load_facts` (форма, репозиторий,
    `scope_sha256`, матрица `state`) → дайджест активной политики → точная
    длительность lease → срок. Плюс единственная проверка, которой у
    потребителя нет и которая принадлежит именно раннеру: покрывают ли записи
    настроенный охват.
    """
    try:
        facts = load_facts(bundle, expected_repository=repo, now=now)
    except UnreadableFacts as exc:
        return None, f"бандл не примет читатель контракта: {exc}"
    if facts.header.policy_digest != active_digest:
        # Дальше живой lease не смотрим — у потребителя несовпавший дайджест
        # перебивает и его (resolve_facts, «строка 3»).
        return None, (
            f"бандл собран под другой политикой: {facts.header.policy_digest} "
            f"!= активной {active_digest}"
        )
    declared = (facts.header.valid_until - facts.header.generated_at).total_seconds()
    if declared != lease_seconds:
        return None, f"lease в бандле {declared:.0f}s, в активной политике {lease_seconds}s"
    if now >= facts.header.valid_until:
        hours = int((now - facts.header.valid_until).total_seconds() // 3600)
        return None, f"lease истёк {hours} ч назад ({facts.header.valid_until})"
    answered = {r.request.value for r in facts.results if r.request.kind == "pr"}
    missing = [n for n in numbers if n not in answered]
    if missing:
        return None, f"в бандле нет записей по PR {missing}"
    extra = sorted(n for n in answered if n not in set(numbers))
    if extra:
        # Сужение охвата не делает старый широкий бандл годным: он доказывает
        # не то, что сейчас настроено. Читатель этого не поймает — файл валиден,
        # про конфигурацию раннера он не знает; проверка принадлежит здесь.
        return None, f"в бандле лишние PR {extra} — охват сузился, а бандл остался шире"
    return facts.header.valid_until, ""


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


def freshness(
    repositories: list[dict[str, Any]], workspace_root: Path, policy: Path
) -> list[Outcome]:
    """Свежесть и полнота опубликованных бандлов — проверка установки.

    Существует потому, что `valid_until` делает молчание обнаружимым **при
    чтении**, а читателя пока нет: до появления потребителя посмотреть, что
    расписание живо, можно только глазами.

    Проверка — руками читателя контракта (`bundle_verdict`), включая дайджест
    АКТИВНОЙ политики и точную длительность lease. Раньше `--check` смотрел
    только на срок и покрытие: смени политику после публикации — и зелёный
    `--check` благословлял бы бандл, который гейт уже отверг бы как
    `policy_digest_mismatch`. Зелёное «установлено» обязано значить «потребитель
    это примет», иначе оно не значит ничего.
    """
    active_digest = policy_digest(policy)
    lease_seconds = load_approval_policy(policy).approval_facts_lease_seconds

    outcomes: list[Outcome] = []
    now = datetime.now(UTC)
    for repo, path, numbers, refusal in _resolved(repositories, workspace_root):
        if path is None:
            outcomes.append(Outcome(repo, "skipped", refusal))
            continue
        bundle = path / BUNDLE_RELPATH
        if not bundle.is_file():
            outcomes.append(Outcome(repo, "failed", f"бандла нет — {bundle}"))
            continue
        valid_until, refusal = bundle_verdict(
            bundle, repo, numbers, active_digest, lease_seconds, now
        )
        if valid_until is None:
            outcomes.append(Outcome(repo, "failed", refusal))
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
    scope_path, refusal = _resolve(args.scope)
    if scope_path is None:
        print(f"охват не прочитан: {refusal}", file=sys.stderr)
        return 2
    policy_path, refusal = _resolve(args.policy)
    if policy_path is None:
        print(f"политика не прочитана: {refusal}", file=sys.stderr)
        return 2

    try:
        repositories = _load_scope(scope_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"охват не прочитан: {exc}", file=sys.stderr)
        return 2

    # Политика нужна ОБОИМ режимам: `--check` сверяет дайджест и lease бандлов
    # с активной политикой (иначе зелёное «установлено» не значит «потребитель
    # примет»), сбор передаёт её продюсеру. Нечитаемая или невалидная политика —
    # ошибка конфигурации вызова, код 2, ни одного репозитория не трогаем.
    if not policy_path.is_file():
        print(f"политики нет: {policy_path}", file=sys.stderr)
        return 2
    try:
        if args.check:
            return report(freshness(repositories, args.workspace_root, policy_path))
        return report(collect(repositories, args.workspace_root, policy_path))
    except PolicyError as exc:
        print(f"политика не прочитана: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
