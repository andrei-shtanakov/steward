"""Публикация фактов: разрешение цели, транзакция, долговечная запись.

Порядок действий здесь — не деталь реализации, а требование §6.1 и §8.4:
удаление прежней публикации выполняется ПОСЛЕ полного preflight. Удаление
ради безопасности — осознанный размен; удаление из-за опечатки в конфиге —
потеря данных на ровном месте.

Этот модуль не знает о политике: `build_header` принимает
`policy_digest_value` и `lease_seconds` аргументами, а не вычисляет их сам —
зависимость идёт в одну сторону (публикация знает про пути, транзакции и
байты, не про классификационную политику), чтобы её мог по-прежнему
проверить `test_import_cycle.py`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from steward.approvalfacts.model import (
    SCHEMA_VERSION,
    Header,
    RequestId,
    Result,
    scope_digest,
)

FACTS_RELPATH = Path(".steward") / "approval_facts.jsonl"

_ORIGIN_RE = re.compile(
    r"^(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"
)


class ConfigError(ValueError):
    """Ошибка конфигурации вызова — exit 2, прежняя публикация не тронута."""


def parse_origin(url: str) -> tuple[str, str]:
    """Разобрать URL remote в пару `(owner, repo)`.

    Сравнение по полной паре, без суффиксного: `endswith("owner/repo")`
    совпал бы и с `other-owner/repo`, и с чужим хостом.
    """
    match = _ORIGIN_RE.match(url.strip())
    if match is None:
        raise ConfigError(f"не удалось разобрать origin: {url!r}")
    return match.group("owner"), match.group("repo")


def _git(cwd: Path, *args: str) -> str | None:
    proc = subprocess.run(  # noqa: S603 S607 — фиксированный argv
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def resolve_bundle_target(repo: str, repo_root: Path) -> Path:
    """Разрешить путь бандла и доказать, что это тот самый репозиторий."""
    top = _git(repo_root, "rev-parse", "--show-toplevel")
    if top is None:
        raise ConfigError(f"{repo_root} не внутри git-репозитория")
    origin = _git(Path(top), "remote", "get-url", "origin")
    if origin is None:
        raise ConfigError(
            f"{top}: нет remote 'origin' — в рабочих копиях флота бывает несколько "
            "remote, и сверяется именно origin"
        )
    owner, name = parse_origin(origin)
    if (owner.lower(), name.lower()) != tuple(part.lower() for part in repo.split("/", 1)):
        raise ConfigError(
            f"{top}: origin указывает на {owner}/{name}, а --repo говорит {repo} — "
            "публикация в чужой бандл запрещена"
        )
    return Path(top) / FACTS_RELPATH


def build_header(
    *,
    repository: str,
    scope: Sequence[RequestId],
    policy_version: int,
    policy_digest_value: str,
    lease_seconds: int,
    now: datetime,
) -> Header:
    generated_at = now.astimezone(UTC).replace(microsecond=0)
    return Header(
        repository=repository,
        generated_at=generated_at,
        valid_until=generated_at + timedelta(seconds=lease_seconds),
        policy_version=policy_version,
        policy_digest=policy_digest_value,
        scope=tuple(scope),
        scope_sha256=scope_digest(scope),
    )


def _header_record(header: Header) -> dict[str, object]:
    return {
        "kind": "header",
        "schema_version": SCHEMA_VERSION,
        "repository": header.repository,
        "generated_at": header.generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "valid_until": header.valid_until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policy_version": header.policy_version,
        "policy_digest": header.policy_digest,
        "complete": True,
        "scope_sha256": header.scope_sha256,
        "scope": [r.as_dict() for r in header.scope],
    }


def _result_record(result: Result) -> dict[str, object]:
    """Запрещённые схемой поля НЕ пишутся вовсе, а не пишутся как null."""
    record: dict[str, object] = {
        "kind": "result",
        "request": result.request.as_dict(),
        "state": result.state,
        "merge_sha": result.merge_sha,
    }
    if result.state == "merged":
        record["identity"] = result.identity
        record["type_hint"] = result.type_hint
        record["actor_class"] = result.actor_class
    return record


def remove_previous(path: Path) -> None:
    """Снять прежнюю публикацию и зафиксировать это на диске."""
    Path(path).unlink(missing_ok=True)
    parent = Path(path).parent
    if parent.is_dir():
        _fsync_dir(parent)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(path: Path, header: Header, results: Sequence[Result]) -> None:
    """Атомарно и долговечно опубликовать файл фактов."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [_header_record(header), *(_result_record(r) for r in results)]
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".approval_facts-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        _fsync_dir(path.parent)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
