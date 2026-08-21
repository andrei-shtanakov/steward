"""Merge-actor classification: closed policy, no default-human (AP-4).

Local git proves merge *provenance* only (``MergeProvenance`` in
:mod:`steward.gatecheck.git_facts`) — never the *actor* who performed or
approved a merge. Actor identity is a materialized fact from GitHub's
``mergedBy`` (see :mod:`steward.approvalfacts`), classified here against a
closed allowlist policy (``profiles/approval-policy.yaml``).

There is no default-human path: an identity absent from both
``human_identities`` and ``agent_identities`` — and not hinted ``Bot`` — is
``"unknown"``, never assumed human because it merely fails to look like a
bot ("doesn't look like a bot" = human is fail-open, rejected by the owner
2026-08-08).

Whether a correctly classified ``"agent"`` actor *satisfies* the release
policy is a separate question from classification, and it is a **policy
value**, not a constant in this code: ``agent_merge_allowed`` in
``profiles/approval-policy.yaml``. ADR-ECO-008 D1 puts merges in automatic
runs on an agent, so the gate must be able to permit exactly that; the default
— including for a policy file written before the field existed — is denied, and
the standing reason lives in that file's own comment rather than here, where it
would drift. Permission has to arrive as an explicit ``true``; it never appears
on its own. Consuming the value belongs to ``check_approval_evidence``, not to
``classify_actor``.

The module also owns the gate's half of the ``approval-facts/v2`` migration:
``resolve_facts`` turns a path into either :class:`ApprovalFactsV2` or a typed
:class:`FactsUnavailable`, and ``check_approval_evidence`` combines that with
git provenance, scope membership, the terminal result and the current policy.
Classification itself is NOT redone there — it arrives in the file, produced
under exactly the policy bytes whose digest ``resolve_facts`` verifies. Two
policy engines able to diverge is the defect this contract exists to prevent.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from steward.approvalfacts.model import (
    MAX_LEASE_SECONDS,
    ActorType,
    ApprovalFactsV2,
    RequestId,
    Result,
)
from steward.approvalfacts.publish import ConfigError
from steward.approvalfacts.reader import UnreadableFacts, detect_legacy_v1, load_facts
from steward.gatecheck.checks import Artifact, Finding
from steward.gatecheck.git_facts import GitFacts

__all__ = [
    "ActorType",
    "ApprovalPolicy",
    "FACTS_UNAVAILABLE_CODES",
    "MAX_LEASE_SECONDS",
    "FactsOutcome",
    "FactsUnavailable",
    "PolicyError",
    "check_approval_evidence",
    "classify_actor",
    "load_approval_policy",
    "policy_digest",
    "resolve_facts",
]


class PolicyError(ValueError):
    """Malformed ``approval-policy.yaml`` — fail-closed, config-level error."""


@dataclass(frozen=True)
class ApprovalPolicy:
    """Closed merge-actor classification policy.

    Empty allowlists are a legitimate state ("we don't know anyone yet"),
    not a config error — only a malformed *shape* is.
    """

    version: int
    human_identities: frozenset[str]
    agent_identities: frozenset[str]
    agent_merge_allowed: bool = False
    #: Lease (§6.4) on an `approval-facts/v2` observation: `valid_until =
    #: generated_at + approval_facts_lease_seconds`. `load_approval_policy`
    #: requires the key in the policy *file* (no silent default there); this
    #: dataclass default exists only so tests/callers that build a policy
    #: in-process without going through the loader keep working.
    approval_facts_lease_seconds: int = 86400


def classify_actor(identity: str | None, hint: str | None, policy: ApprovalPolicy) -> ActorType:
    """Classify a merge actor against the closed policy.

    - ``identity`` is ``None`` (no evidence) -> ``"unknown"``.
    - exact match in ``policy.human_identities`` -> ``"human"`` (checked
      first, so a listed human is never reclassified by a misleading hint).
    - exact match in ``policy.agent_identities`` OR ``hint == "Bot"`` ->
      ``"agent"``.
    - anything else, including an unrecognized identity with a ``"User"``
      hint -> ``"unknown"``. There is no default-human fallback.
    """
    if identity is None:
        return "unknown"
    if identity in policy.human_identities:
        return "human"
    if identity in policy.agent_identities or hint == "Bot":
        return "agent"
    return "unknown"


def _check_identity_list(value: object, field: str, path: Path) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PolicyError(f"{path}: '{field}' must be a list of strings")
    return frozenset(value)


_ALLOWED_KEYS = {
    "version",
    "human_identities",
    "agent_identities",
    "agent_merge_allowed",
    "approval_facts_lease_seconds",
}


def _check_lease(value: object, path: Path) -> int:
    """Validate `approval_facts_lease_seconds`: a positive int, bounded.

    `bool` is a subclass of `int` in Python, so it is rejected explicitly —
    otherwise `approval_facts_lease_seconds: true` would silently become a
    one-second lease instead of a config error.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise PolicyError(
            f"{path}: 'approval_facts_lease_seconds' must be a positive int "
            f"(bool and non-int are rejected, not coerced), got {value!r}"
        )
    if value <= 0:
        raise PolicyError(f"{path}: 'approval_facts_lease_seconds' must be > 0, got {value}")
    if value > MAX_LEASE_SECONDS:
        raise PolicyError(
            f"{path}: 'approval_facts_lease_seconds' must be <= {MAX_LEASE_SECONDS} "
            f"(30 days), got {value}"
        )
    return value


def policy_digest(path: Path) -> str:
    """sha256 over the policy file's raw bytes.

    Raw bytes, not the parsed document: a comment is part of the audited
    artifact, so editing it honestly changes the digest.

    An unreadable policy file is a :class:`PolicyError` — the same typed,
    config-level failure ``load_approval_policy`` raises for the same file, so
    every caller reaches exit 2 instead of a traceback. This function sits on
    the gate's read path (table row 3), and the gate's whole contract is that
    a broken instrument never surfaces as a property of the actor; a bare
    ``OSError`` escaping here would be exactly that.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise PolicyError(f"cannot read approval policy {path}: {exc}") from exc
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def load_approval_policy(path: Path) -> ApprovalPolicy:
    """Load and validate ``approval-policy.yaml``, fail-closed.

    Follows the same shape-validation discipline as
    :mod:`steward.gatecatalog`: a non-mapping document, missing required
    lists, or non-string list entries all raise :class:`PolicyError` naming
    the file and field. Empty ``human_identities``/``agent_identities``
    lists are accepted — they mean "no known actors yet", not an error.

    ``agent_merge_allowed`` is optional and defaults to ``False``; when
    present it must be a real ``bool``. A truthy scalar (``1``, ``"yes"``)
    is a :class:`PolicyError`, never a coerced grant — permission is
    something a policy states, not something a parser infers.

    ``approval_facts_lease_seconds`` is **required** — a policy file written
    before the field existed fails to load rather than silently treating
    every observation as eternally fresh. It must be a positive ``int`` (not
    ``bool``, which is a Python subclass of ``int``) no greater than
    :data:`MAX_LEASE_SECONDS` (30 days) — the same contract-level bound
    :mod:`steward.approvalfacts.reader` enforces on the emitted
    ``valid_until``.
    """
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyError(f"cannot read approval policy {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"{path}: top level must be a mapping")

    unknown_keys = set(data.keys()) - _ALLOWED_KEYS
    if unknown_keys:
        raise PolicyError(f"{path}: unknown key(s) {sorted(unknown_keys)}")

    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise PolicyError(f"{path}: 'version' must be an int >= 1")

    if "human_identities" not in data:
        raise PolicyError(f"{path}: missing required 'human_identities'")
    if "agent_identities" not in data:
        raise PolicyError(f"{path}: missing required 'agent_identities'")

    human_identities = _check_identity_list(data["human_identities"], "human_identities", path)
    agent_identities = _check_identity_list(data["agent_identities"], "agent_identities", path)

    agent_merge_allowed = data.get("agent_merge_allowed", False)
    if not isinstance(agent_merge_allowed, bool):
        raise PolicyError(f"{path}: 'agent_merge_allowed' must be a bool")

    if "approval_facts_lease_seconds" not in data:
        raise PolicyError(f"{path}: missing required 'approval_facts_lease_seconds'")
    approval_facts_lease_seconds = _check_lease(data["approval_facts_lease_seconds"], path)

    return ApprovalPolicy(
        version=version,
        human_identities=human_identities,
        agent_identities=agent_identities,
        agent_merge_allowed=agent_merge_allowed,
        approval_facts_lease_seconds=approval_facts_lease_seconds,
    )


_APPROVED = "approved"


@dataclass(frozen=True)
class FactsUnavailable:
    """Исход чтения источника фактов как ТИПИЗИРОВАННОЕ значение.

    Гейт не переоткрывает файл и не переразбирает ошибки чтения: он получает
    либо :class:`ApprovalFactsV2`, либо этот объект. ``code`` — закрытый
    словарь причин (:data:`FACTS_UNAVAILABLE_CODES`), и различает строки 1-5
    таблицы §8.3 именно он; ``detail`` несёт диагностику читателя (путь,
    нарушенный инвариант) и в различении не участвует — два разных ``code``
    обязаны давать разные сообщения гейта даже при пустом ``detail``.
    """

    code: str
    detail: str = ""


#: Исход разрешения источника: факты либо типизированная причина их отсутствия.
FactsOutcome = ApprovalFactsV2 | FactsUnavailable


#: Сообщение на каждую причину недоступности. Ключи этого словаря —
#: единственный источник истины о допустимых ``code``; отдельного enum нет,
#: чтобы код без своего сообщения нельзя было завести незаметно.
_UNAVAILABLE_MESSAGES: dict[str, str] = {
    "absent": (
        "materialized merge evidence is absent at the resolved path — "
        "materialize it with the `steward approval-facts` command"
    ),
    "legacy_v1": (
        "facts source is an unsupported legacy approval-facts/v1; "
        "reissue it with the `steward approval-facts` command"
    ),
    "unreadable": ("facts source is not a valid approval-facts/v2 and cannot be read in full"),
    "policy_digest_mismatch": (
        "observation was produced under a DIFFERENT classification policy: policy_digest "
        "does not match the current approval-policy.yaml bytes"
    ),
    "lease_mismatch": (
        "declared observation duration does not equal the active approval_facts_lease_seconds"
    ),
    "stale": "observation is stale: lease has expired",
}

#: Закрытый набор причин недоступности (§8.3, строки 1-5 плюс правило §8.3.1).
FACTS_UNAVAILABLE_CODES = frozenset(_UNAVAILABLE_MESSAGES)


def resolve_facts(
    path: Path,
    *,
    expected_repository: str,
    policy: ApprovalPolicy,
    policy_path: Path,
    now: datetime,
    explicit: bool,
) -> FactsOutcome:
    """Разрешить источник фактов в факты или в типизированную причину их отсутствия.

    Строки 1-5 таблицы §8.3 применяются **в этом порядке**, и порядок
    нормативен: поломка прибора не должна читаться как характеристика актора.
    Поэтому несовпавший ``policy_digest`` (строка 3) перебивает и живую lease,
    и любой терминальный результат — наблюдение по другой политике не является
    наблюдением по текущей.

    ``explicit`` различает два пути ввода по правилу §8.3.1: файл по
    bundle-default просто «оказался таким» — это свойство среды, finding; тот
    же файл по явному ``--approval-facts`` означает, что оператор указал не тот
    файл, — :class:`ConfigError`, exit 2. Граница проходит по **валидности**
    файла, не по свежести: просроченная lease, несовпавший digest и
    несоответствие длительности — свойства КОРРЕКТНОГО файла, который честно
    сообщает о себе, и дают finding на обоих путях.

    Нечитаемый файл политики поднимает :class:`PolicyError` на обоих путях: это
    сломанный вход конфигурации, а не наблюдение, и притворяться, что тут есть
    исход для актора, значило бы выдать поломку прибора за его показание.
    """
    path = Path(path)
    # Строка 1.
    if not path.exists():
        return FactsUnavailable("absent", f"file not found: {path}")

    # Строка 2. Легаси опознаётся отдельно от прочей невалидности: «не тот
    # формат» и «испорченный файл того же формата» требуют разных действий
    # оператора, и одно сообщение на оба случая скрывало бы это.
    if detect_legacy_v1(path):
        detail = f"{path}: detected approval-facts/v1"
        if explicit:
            raise ConfigError(f"unsupported legacy approval-facts/v1: {path}")
        return FactsUnavailable("legacy_v1", detail)
    try:
        facts = load_facts(path, expected_repository=expected_repository, now=now)
    except UnreadableFacts as exc:
        if explicit:
            raise ConfigError(str(exc)) from exc
        return FactsUnavailable("unreadable", str(exc))

    # Строка 3 — приоритет выше живой lease и выше любого результата.
    current_digest = policy_digest(policy_path)
    if facts.header.policy_digest != current_digest:
        return FactsUnavailable(
            "policy_digest_mismatch",
            f"{facts.header.policy_digest} != {current_digest} ({policy_path})",
        )

    # Строка 4 — точное равенство заявленной длительности действующей
    # конфигурации. Это проверка ГЕЙТА, а не общая: у постороннего читателя
    # контракта нашей политики нет и требовать её от него нельзя (§7).
    declared = (facts.header.valid_until - facts.header.generated_at).total_seconds()
    if declared != policy.approval_facts_lease_seconds:
        return FactsUnavailable(
            "lease_mismatch",
            f"declared {declared:.0f}s, active {policy.approval_facts_lease_seconds}s",
        )

    # Строка 5.
    if now >= facts.header.valid_until:
        return FactsUnavailable(
            "stale", f"valid_until {facts.header.valid_until:%Y-%m-%dT%H:%M:%SZ} has already passed"
        )

    return facts


_OUT_OF_SCOPE = (
    "merge {sha} is outside the declared observation scope — actor is unknown "
    "regardless of the file's age"
)

#: Строки 7-8: SHA заявлен в scope, но определённого разрешения не получил.
#: Два случая — два разных сообщения: они требуют разного расследования.
_SOURCE_CONFLICT: dict[str, str] = {
    "not_found": (
        "source conflict for merge {sha}: git presents merge-provenance, "
        "but the forge reports the commit absent (not_found)"
    ),
    "no_matching_pr": (
        "source conflict for merge {sha}: the commit exists but is not the "
        "merge commit of the linked PR (no_matching_pr), while locally it is "
        "presented as merge-provenance"
    ),
}

_ACTOR_UNAVAILABLE = (
    "merge {sha} was observed, but the actor cannot be resolved (actor_unavailable)"
)
_ACTOR_UNKNOWN = (
    "actor of merge {sha} ({identity}) is not recognized by the closed classification (unknown)"
)
_AGENT_DENIED = (
    "actor of merge {sha} ({identity}) is classified as agent, but agent_merge does "
    "not satisfy the release policy: 'agent_merge_allowed' is false in "
    "approval-policy.yaml — set it to true there to allow agent merges"
)
_ACTOR_UNCLASSIFIED = (
    "merge {sha} was observed, but its actor classification {actor_class!r} is not "
    "in the closed set human/agent/unknown — fail-closed"
)
_PROVENANCE_ABSENT = (
    "required merge evidence is absent: the current blob has no merge-provenance "
    "via the first parent of the default branch"
)


def _unavailable_message(sha: str, outcome: FactsUnavailable) -> str:
    base = _UNAVAILABLE_MESSAGES.get(outcome.code, f"facts source is unavailable: {outcome.code}")
    message = f"merge {sha}: {base}"
    return f"{message} [{outcome.detail}]" if outcome.detail else message


def _result_for_requested_sha(facts: ApprovalFactsV2, sha: str) -> Result | None:
    """Второй lookup (§8.2): членство в scope по идентичности ЗАПРОСА.

    Это не то же самое, что индекс разрешённых SHA, и путать их нельзя:
    индекс отвечает «этот мерж наблюдён», а этот поиск — «про этот мерж
    спрашивали». Совпадение по идентичности запроса при промахе индекса
    означает определённый отрицательный ответ, то есть противоречие
    источников, а не неизвестного актора.
    """
    wanted = RequestId("merge_sha", sha)
    return next((r for r in facts.results if r.request == wanted), None)


def _evaluate(sha: str, facts: FactsOutcome, policy: ApprovalPolicy) -> str | None:
    """Комбинировать оси для одного merge SHA; `None` — политика удовлетворена."""
    if isinstance(facts, FactsUnavailable):
        return _unavailable_message(sha, facts)

    result = facts.by_merge_sha().get(sha)
    if result is None:
        if not facts.scope_has_sha(sha):
            return _OUT_OF_SCOPE.format(sha=sha)  # строка 6
        requested = _result_for_requested_sha(facts, sha)
        state = requested.state if requested is not None else "?"
        template = _SOURCE_CONFLICT.get(state)
        if template is None:
            # Достижимо: `merged`/`actor_unavailable` по запросу этого SHA,
            # разрешившиеся в ДРУГОЙ merge-коммит. Наблюдение определённое и
            # всё равно не подтверждает предъявленную provenance.
            return (
                f"source conflict for merge {sha}: it is declared in scope, but "
                f"the observation in state {state!r} does not resolve to this SHA"
            )
        return template.format(sha=sha)  # строки 7-8

    if result.state == "actor_unavailable":
        return _ACTOR_UNAVAILABLE.format(sha=sha)  # строка 9
    if result.actor_class == "unknown":
        return _ACTOR_UNKNOWN.format(sha=sha, identity=result.identity)  # строка 10
    if result.actor_class == "agent":
        if policy.agent_merge_allowed:
            return None  # строка 12
        return _AGENT_DENIED.format(sha=sha, identity=result.identity)  # строка 11
    if result.actor_class == "human":
        return None  # строка 13
    # Классификации, которой нет в таблице, соответствует ОТКАЗ, а не тишина.
    # Через читателя сюда не попасть (`merged` обязан нести один из трёх
    # классов), но эта функция публична и принимает любой `ApprovalFactsV2`;
    # «политика удовлетворена по умолчанию» — ровно тот fail-open, который
    # закрытая классификация и запрещает.
    return _ACTOR_UNCLASSIFIED.format(sha=sha, actor_class=result.actor_class)


def check_approval_evidence(
    artifacts: list[Artifact],
    git: GitFacts,
    policy: ApprovalPolicy,
    facts: FactsOutcome | None,
    *,
    stage: str,
) -> list[Finding]:
    """GC-APPROVAL-MISSING: release-гейт merge-evidence как комбинатор (§8.1).

    Файл владеет **per-item наблюдением**; здесь комбинируются пять осей —
    git provenance × доступность и свежесть контракта × членство в scope ×
    терминальный результат × действующая политика. Полная таблица «условие →
    исход» и её нормативный порядок — §8.3; первое сработавшее условие
    определяет исход.

    Запускается только при ``stage == "release"`` — на ``"authoring"`` чек не
    выполняется вовсе (не «выполняется и ничего не находит»).

    ``facts`` — уже разрешённый исход чтения (:func:`resolve_facts`), а не
    путь: гейт не переоткрывает файл и не переразбирает ошибки чтения сам.
    ``None`` эквивалентен ``FactsUnavailable("absent")`` — у «фактов нет» не
    бывает второго, молчаливого представления.

    Классификация актора берётся ИЗ ФАЙЛА (``Result.actor_class``) и здесь не
    пересчитывается: владелец классификации один, а два policy engine способны
    разойтись (§1). Право применять её даёт строка 3 таблицы — совпадение
    ``policy_digest`` доказывает, что наблюдение выпущено под ровно теми
    байтами политики, что действуют сейчас. Решением ГЕЙТА остаётся только
    ``agent_merge_allowed`` (§8.1): продюсер не превращает ``actor_class:
    agent`` в вердикт одобрения.

    Комбинатор никогда не читает ``MergeProvenance.actor``/``actor_source`` —
    это путь прямой инъекции для фикстур, а не авторитетный источник.
    """
    if stage != "release":
        return []

    # `None` несёт ровно тот же исход, что явный `absent`, включая пустую
    # деталь: второй, чуть иначе звучащий способ сказать «фактов нет» сделал
    # бы два состояния различимыми в выводе, не будучи различимыми по сути.
    outcome: FactsOutcome = facts if facts is not None else FactsUnavailable("absent")

    findings: list[Finding] = []
    for artifact in artifacts:
        if artifact.node_id is None or artifact.meta.status != _APPROVED:
            continue
        if not git.on_default_branch(artifact.path):
            continue

        provenance = git.merge_provenance(artifact.path)
        if provenance is None:
            findings.append(
                Finding("error", "GC-APPROVAL-MISSING", artifact.path, _PROVENANCE_ABSENT)
            )
            continue

        message = _evaluate(provenance.sha, outcome, policy)
        if message is not None:
            findings.append(Finding("error", "GC-APPROVAL-MISSING", artifact.path, message))
    return findings
