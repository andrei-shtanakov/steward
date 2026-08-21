"""Гейт как комбинатор пяти осей (§8) и двадцать четыре сценария приёмки §9.1.

Проверяется не только вердикт, но и РАЗЛИЧИМОСТЬ: сведение двух разных
причин к одному тексту прячет поломку прибора под видом свойства актора.

Герметичность — требование, а не пожелание: ни один тест не читает
`profiles/approval-policy.yaml` этого репозитория. Каждый строит свой файл
политики внутри `tmp_path` и передаёт его явно, иначе шаг сверки
`policy_digest` не смог бы упасть ни в одном тесте и его порядок в таблице
остался бы незапиненным.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from steward.approvalfacts.model import (
    ApprovalFactsV2,
    Header,
    RequestId,
    Result,
    scope_digest,
)
from steward.approvalfacts.publish import ConfigError, publish
from steward.gatecheck.approval import (
    FACTS_UNAVAILABLE_CODES,
    ApprovalPolicy,
    FactsUnavailable,
    check_approval_evidence,
    load_approval_policy,
    policy_digest,
    resolve_facts,
)
from steward.gatecheck.checks import Artifact
from steward.gatecheck.cli import approval_facts_outcome
from steward.gatecheck.git_facts import MergeProvenance
from steward.meta import parse_artifact

REPO = "andrei-shtanakov/steward"
SHA = "221457933968be9e95acd51d548e080f739c794c"
OTHER_SHA = "b" * 40

NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
GENERATED = datetime(2026, 8, 21, 9, 0, 0, tzinfo=UTC)
LEASE = 86400
VALID_UNTIL = GENERATED + timedelta(seconds=LEASE)

#: Закрытый словарь причин недоступности (§8.3, строки 1-5 плюс §8.3.1).
#: Литерал живёт здесь, а не импортируется, чтобы новый код без своего
#: сообщения не проехал молча.
CODES = {
    "absent",
    "unreadable",
    "legacy_v1",
    "policy_digest_mismatch",
    "lease_mismatch",
    "stale",
}

ARTIFACT_PATH = "20-design.md"

POLICY = ApprovalPolicy(
    version=1,
    human_identities=frozenset({"github:andrei-shtanakov"}),
    agent_identities=frozenset({"github:merge-broker"}),
)
POLICY_ALLOWING = ApprovalPolicy(
    version=1,
    human_identities=frozenset({"github:andrei-shtanakov"}),
    agent_identities=frozenset({"github:merge-broker"}),
    agent_merge_allowed=True,
)

HUMAN = Result(
    RequestId("merge_sha", SHA), "merged", SHA, "github:andrei-shtanakov", "User", "human"
)
AGENT = Result(RequestId("merge_sha", SHA), "merged", SHA, "github:merge-broker", "Bot", "agent")


# --------------------------------------------------------------------------
# Фикстуры: политика, файл фактов, git — всё внутри tmp_path.
# --------------------------------------------------------------------------


def _policy_text(*, lease: int = LEASE, agent_allowed: bool = False, note: str = "") -> str:
    return (
        "version: 1\n"
        "human_identities:\n"
        "  - github:andrei-shtanakov\n"
        "agent_identities:\n"
        "  - github:merge-broker\n"
        f"agent_merge_allowed: {'true' if agent_allowed else 'false'}\n"
        f"approval_facts_lease_seconds: {lease}\n" + note
    )


def _policy_file(
    tmp_path: Path,
    *,
    name: str = "approval-policy.yaml",
    lease: int = LEASE,
    agent_allowed: bool = False,
    note: str = "",
) -> Path:
    """Свой файл политики на каждый тест — герметичность (правило задачи 8)."""
    path = tmp_path / name
    path.write_text(
        _policy_text(lease=lease, agent_allowed=agent_allowed, note=note), encoding="utf-8"
    )
    return path


def _loaded_policy(
    tmp_path: Path,
    *,
    name: str = "approval-policy.yaml",
    lease: int = LEASE,
    agent_allowed: bool = False,
    note: str = "",
) -> tuple[ApprovalPolicy, Path]:
    path = _policy_file(tmp_path, name=name, lease=lease, agent_allowed=agent_allowed, note=note)
    return load_approval_policy(path), path


def _facts(
    *,
    scope: list[RequestId] | None = None,
    results: list[Result] | None = None,
    repository: str = REPO,
    generated_at: datetime = GENERATED,
    valid_until: datetime | None = None,
    digest: str = "sha256:" + "0" * 64,
) -> ApprovalFactsV2:
    scope = [RequestId("merge_sha", SHA)] if scope is None else scope
    results = [HUMAN] if results is None else results
    header = Header(
        repository=repository,
        generated_at=generated_at,
        valid_until=generated_at + timedelta(seconds=LEASE) if valid_until is None else valid_until,
        policy_version=1,
        policy_digest=digest,
        scope=tuple(scope),
        scope_sha256=scope_digest(scope),
    )
    return ApprovalFactsV2(header=header, results=tuple(results))


def _write_valid(
    tmp_path: Path,
    *,
    policy_path: Path,
    name: str = "approval_facts.jsonl",
    scope: list[RequestId] | None = None,
    results: list[Result] | None = None,
    repository: str = REPO,
    generated_at: datetime = GENERATED,
    valid_until: datetime | None = None,
    digest: str | None = None,
) -> Path:
    """Файл фактов, по умолчанию согласованный с `policy_path`."""
    facts = _facts(
        scope=scope,
        results=results,
        repository=repository,
        generated_at=generated_at,
        valid_until=valid_until,
        digest=policy_digest(policy_path) if digest is None else digest,
    )
    path = tmp_path / name
    publish(path, facts.header, facts.results)
    return path


def _write_v1(tmp_path: Path, name: str = "approval_facts.jsonl") -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                "schema": "approval-facts/v1",
                "actors": {SHA: {"identity": "github:andrei-shtanakov", "actor_type_hint": "User"}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _write_broken(tmp_path: Path, *, policy_path: Path, name: str = "approval_facts.jsonl") -> Path:
    """Валидный файл минус последняя запись: нарушен инвариант 3 (биекция)."""
    path = _write_valid(tmp_path, policy_path=policy_path, name=name)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    return path


class FakeGitFacts:
    """Только два члена, которые гейт действительно вызывает."""

    def __init__(
        self, on_default: set[str], provenance: dict[str, MergeProvenance] | None = None
    ) -> None:
        self._on_default = on_default
        self._provenance = provenance or {}

    def on_default_branch(self, path: str) -> bool:
        return path in self._on_default

    def merge_provenance(self, path: str) -> MergeProvenance | None:
        return self._provenance.get(path)


def _git(*, sha: str | None, on_default_branch: bool = True) -> FakeGitFacts:
    provenance = (
        {
            ARTIFACT_PATH: MergeProvenance(
                sha=sha,
                subject="Merge PR #7",
                current_blob_sha="deadbeef",
                merge_method="merge_commit",
                actor=None,
                actor_source="unavailable",
            )
        }
        if sha is not None
        else {}
    )
    return FakeGitFacts({ARTIFACT_PATH} if on_default_branch else set(), provenance)


def _artifacts(status: str = "approved", node_id: str | None = "design") -> list[Artifact]:
    text = f"---\nspec_stage: design\nstatus: {status}\nversion: 1\n---\nтело\n"
    meta = parse_artifact(text)
    assert meta is not None
    return [Artifact(path=ARTIFACT_PATH, node_id=node_id, meta=meta, text=text)]


def _check(facts: object, *, git: FakeGitFacts, policy: ApprovalPolicy = POLICY) -> list:
    return check_approval_evidence(_artifacts(), git, policy, facts, stage="release")


# --------------------------------------------------------------------------
# resolve_facts: строки 1-5 таблицы §8.3 и правило §8.3.1.
# --------------------------------------------------------------------------


def test_unavailable_codes_are_exactly_the_declared_set() -> None:
    """Новый код без своего сообщения не должен проехать молча."""
    assert FACTS_UNAVAILABLE_CODES == CODES


def test_absent_file_is_unavailable_not_unknown(tmp_path: Path) -> None:
    """Строка 1: файла нет — это недоступность прибора, не свойство актора."""
    policy, policy_path = _loaded_policy(tmp_path)
    outcome = resolve_facts(
        tmp_path / "nope.jsonl",
        expected_repository=REPO,
        policy=policy,
        policy_path=policy_path,
        now=NOW,
        explicit=False,
    )
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "absent"


def test_legacy_v1_on_bundle_default_is_finding(tmp_path: Path) -> None:
    """Строка 2: легаси опознаётся ОТДЕЛЬНЫМ кодом, а не общим `unreadable`."""
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_v1(tmp_path)
    outcome = resolve_facts(
        path,
        expected_repository=REPO,
        policy=policy,
        policy_path=policy_path,
        now=NOW,
        explicit=False,
    )
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "legacy_v1"


def test_legacy_v1_on_explicit_path_is_config_error(tmp_path: Path) -> None:
    """Правило §8.3.1: оператор указал не тот файл — это ошибка вызова."""
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_v1(tmp_path)
    with pytest.raises(ConfigError):
        resolve_facts(
            path,
            expected_repository=REPO,
            policy=policy,
            policy_path=policy_path,
            now=NOW,
            explicit=True,
        )


def test_broken_invariant_on_bundle_default_is_unreadable(tmp_path: Path) -> None:
    """Сценарий 21а: нарушенный инвариант по пути бандла — finding."""
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_broken(tmp_path, policy_path=policy_path)
    outcome = resolve_facts(
        path,
        expected_repository=REPO,
        policy=policy,
        policy_path=policy_path,
        now=NOW,
        explicit=False,
    )
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "unreadable"


def test_broken_invariant_on_explicit_path_is_config_error(tmp_path: Path) -> None:
    """Сценарий 21б: тот же файл по явному пути — config error."""
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_broken(tmp_path, policy_path=policy_path)
    with pytest.raises(ConfigError):
        resolve_facts(
            path,
            expected_repository=REPO,
            policy=policy,
            policy_path=policy_path,
            now=NOW,
            explicit=True,
        )


def test_foreign_repository_header_is_unreadable(tmp_path: Path) -> None:
    """Сценарий 23: `header.repository` мимо наблюдаемого — файл невалиден."""
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_valid(tmp_path, policy_path=policy_path, repository="other-owner/steward")
    outcome = resolve_facts(
        path,
        expected_repository=REPO,
        policy=policy,
        policy_path=policy_path,
        now=NOW,
        explicit=False,
    )
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "unreadable"


def test_foreign_repository_header_on_explicit_path_is_config_error(tmp_path: Path) -> None:
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_valid(tmp_path, policy_path=policy_path, repository="other-owner/steward")
    with pytest.raises(ConfigError):
        resolve_facts(
            path,
            expected_repository=REPO,
            policy=policy,
            policy_path=policy_path,
            now=NOW,
            explicit=True,
        )


def test_foreign_repository_file_with_same_sha_does_not_satisfy_gate(tmp_path: Path) -> None:
    """Контрольный к сценарию 23: файл ЧУЖОГО репозитория с тем же merge SHA и
    человеческим актором не должен влиять на enforcement."""
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_valid(tmp_path, policy_path=policy_path, repository="other-owner/steward")
    outcome = resolve_facts(
        path,
        expected_repository=REPO,
        policy=policy,
        policy_path=policy_path,
        now=NOW,
        explicit=False,
    )
    findings = _check(outcome, git=_git(sha=SHA))
    assert len(findings) == 1


def test_generated_at_in_future_beyond_skew_is_unreadable(tmp_path: Path) -> None:
    """Сценарий 15: часы убежали вперёд — это `unreadable`, не «очень свежий»."""
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_valid(
        tmp_path, policy_path=policy_path, generated_at=NOW + timedelta(seconds=3600)
    )
    outcome = resolve_facts(
        path,
        expected_repository=REPO,
        policy=policy,
        policy_path=policy_path,
        now=NOW,
        explicit=False,
    )
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "unreadable"


def test_generated_at_within_clock_skew_still_resolves(tmp_path: Path) -> None:
    """Контрольный к сценарию 15: допуск в 300 с не превращает годный файл в
    невалидный — иначе проверка была бы неотличима от «любое будущее плохо»."""
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_valid(
        tmp_path, policy_path=policy_path, generated_at=NOW + timedelta(seconds=200)
    )
    outcome = resolve_facts(
        path,
        expected_repository=REPO,
        policy=policy,
        policy_path=policy_path,
        now=NOW,
        explicit=False,
    )
    assert isinstance(outcome, ApprovalFactsV2)


def test_policy_digest_mismatch_beats_live_lease(tmp_path: Path) -> None:
    """Приоритет строки 3: наблюдение по другой политике не является
    наблюдением по текущей, даже если lease ещё жива."""
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_valid(tmp_path, policy_path=policy_path, digest="sha256:" + "e" * 64)
    outcome = resolve_facts(
        path,
        expected_repository=REPO,
        policy=policy,
        policy_path=policy_path,
        now=NOW,
        explicit=False,
    )
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "policy_digest_mismatch"


def test_policy_digest_follows_a_comment_only_edit(tmp_path: Path) -> None:
    """Digest считается по СЫРЫМ байтам: правка комментария честно
    инвалидирует evidence. Без этого проверка строки 3 была бы неотличима от
    сравнения разобранных значений."""
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_valid(tmp_path, policy_path=policy_path)
    policy_path.write_text(
        _policy_text(note="# комментарий добавлен после публикации\n"), encoding="utf-8"
    )
    outcome = resolve_facts(
        path,
        expected_repository=REPO,
        policy=policy,
        policy_path=policy_path,
        now=NOW,
        explicit=False,
    )
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "policy_digest_mismatch"


def test_lease_duration_mismatch_is_its_own_code(tmp_path: Path) -> None:
    """Строка 4. Фикстура НЕ просрочена (`now` внутри окна), иначе удаление
    строки 4 маскировалось бы строкой 5."""
    policy, policy_path = _loaded_policy(tmp_path)
    generated = NOW - timedelta(minutes=30)
    path = _write_valid(
        tmp_path,
        policy_path=policy_path,
        generated_at=generated,
        valid_until=generated + timedelta(hours=1),
    )
    outcome = resolve_facts(
        path,
        expected_repository=REPO,
        policy=policy,
        policy_path=policy_path,
        now=NOW,
        explicit=False,
    )
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "lease_mismatch"


def test_stale_lease_is_its_own_code(tmp_path: Path) -> None:
    """Строка 5. Заявленная длительность РАВНА политике, чтобы удаление
    строки 5 не маскировалось строкой 4."""
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_valid(tmp_path, policy_path=policy_path, generated_at=NOW - timedelta(days=2))
    outcome = resolve_facts(
        path,
        expected_repository=REPO,
        policy=policy,
        policy_path=policy_path,
        now=NOW,
        explicit=False,
    )
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "stale"


def test_stale_on_explicit_path_stays_finding(tmp_path: Path) -> None:
    """Граница §8.3.1 проходит по валидности файла, НЕ по свежести:
    просроченный файл корректен и честно сообщает о себе."""
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_valid(tmp_path, policy_path=policy_path, generated_at=NOW - timedelta(days=2))
    outcome = resolve_facts(
        path,
        expected_repository=REPO,
        policy=policy,
        policy_path=policy_path,
        now=NOW,
        explicit=True,
    )
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "stale"


def test_digest_mismatch_on_explicit_path_stays_finding(tmp_path: Path) -> None:
    """Та же граница §8.3.1 со стороны строки 3: несовпавший digest — свойство
    КОРРЕКТНОГО файла, а не ошибка вызова."""
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_valid(tmp_path, policy_path=policy_path, digest="sha256:" + "e" * 64)
    outcome = resolve_facts(
        path,
        expected_repository=REPO,
        policy=policy,
        policy_path=policy_path,
        now=NOW,
        explicit=True,
    )
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "policy_digest_mismatch"


def test_valid_fresh_file_resolves_to_facts(tmp_path: Path) -> None:
    """Контрольный: без него удаление любой из строк 3-5 было бы неразличимо."""
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_valid(tmp_path, policy_path=policy_path)
    outcome = resolve_facts(
        path,
        expected_repository=REPO,
        policy=policy,
        policy_path=policy_path,
        now=NOW,
        explicit=False,
    )
    assert isinstance(outcome, ApprovalFactsV2)
    assert outcome.by_merge_sha()[SHA].actor_class == "human"


def test_lease_from_policy_file_is_what_row_four_compares(tmp_path: Path) -> None:
    """Строка 4 сверяется с ДЕЙСТВУЮЩЕЙ конфигурацией, а не с константой:
    один и тот же файл фактов годен под своей политикой и не соответствует
    политике с другим `approval_facts_lease_seconds`."""
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_valid(tmp_path, policy_path=policy_path)

    changed_dir = tmp_path / "changed"
    changed_dir.mkdir()
    changed, changed_path = _loaded_policy(changed_dir, lease=3600)
    assert policy.approval_facts_lease_seconds != changed.approval_facts_lease_seconds

    mismatched = resolve_facts(
        path,
        expected_repository=REPO,
        policy=changed,
        policy_path=policy_path,
        now=NOW,
        explicit=False,
    )
    assert isinstance(mismatched, FactsUnavailable)
    assert mismatched.code == "lease_mismatch"

    fresh_generated = NOW - timedelta(minutes=30)
    reissued = _write_valid(
        changed_dir,
        policy_path=changed_path,
        generated_at=fresh_generated,
        valid_until=fresh_generated + timedelta(seconds=3600),
    )
    ok = resolve_facts(
        reissued,
        expected_repository=REPO,
        policy=changed,
        policy_path=changed_path,
        now=NOW,
        explicit=False,
    )
    assert isinstance(ok, ApprovalFactsV2)


# --------------------------------------------------------------------------
# check_approval_evidence: охват, provenance, строки 6-13.
# --------------------------------------------------------------------------


def test_authoring_stage_does_not_run_at_all() -> None:
    """Сценарий 10: на `authoring` чек не запускается вовсе."""
    assert (
        check_approval_evidence(_artifacts(), _git(sha=SHA), POLICY, None, stage="authoring") == []
    )


def test_artifact_outside_default_branch_is_out_of_scope() -> None:
    """Сценарий 11."""
    git = _git(sha=SHA, on_default_branch=False)
    assert check_approval_evidence(_artifacts(), git, POLICY, None, stage="release") == []


def test_draft_artifact_is_out_of_scope() -> None:
    findings = check_approval_evidence(
        _artifacts(status="draft"), _git(sha=SHA), POLICY, None, stage="release"
    )
    assert findings == []


def test_unmanaged_artifact_is_out_of_scope_even_if_approved() -> None:
    findings = check_approval_evidence(
        _artifacts(node_id=None), _git(sha=SHA), POLICY, None, stage="release"
    )
    assert findings == []


def test_no_provenance_is_absent() -> None:
    """Сценарий 1: локальной merge-provenance нет."""
    findings = check_approval_evidence(_artifacts(), _git(sha=None), POLICY, None, stage="release")
    assert len(findings) == 1
    assert findings[0].rule_id == "GC-APPROVAL-MISSING"
    assert findings[0].severity == "error"
    assert "merge-provenance" in findings[0].message


def test_evidence_file_absent_is_its_own_message() -> None:
    """Сценарий 2: provenance есть, источника фактов нет."""
    findings = _check(FactsUnavailable("absent", ""), git=_git(sha=SHA))
    assert len(findings) == 1
    assert "steward approval-facts" in findings[0].message


def test_facts_argument_none_behaves_as_absent() -> None:
    """`None` — тот же исход, что явный `absent`: у гейта нет второго
    молчаливого способа «фактов нет»."""
    explicit_absent = _check(FactsUnavailable("absent", ""), git=_git(sha=SHA))
    implicit = _check(None, git=_git(sha=SHA))
    assert [f.message for f in implicit] == [f.message for f in explicit_absent]


def test_every_unavailable_code_has_its_own_message() -> None:
    """Сведение двух причин к одному тексту прячет поломку прибора."""
    messages = {_check(FactsUnavailable(code, ""), git=_git(sha=SHA))[0].message for code in CODES}
    assert len(messages) == len(CODES)


def test_sha_outside_scope_is_unknown_not_conflict() -> None:
    """Контрольный (сценарий 16): PR-запрос с `not_merged` при живой локальной
    provenance даёт `unknown` вне scope, а не доказанное противоречие —
    связать нечем."""
    facts = _facts(
        scope=[RequestId("pr", 42)],
        results=[Result(RequestId("pr", 42), "not_merged")],
    )
    findings = _check(facts, git=_git(sha=SHA))
    assert len(findings) == 1
    assert "вне объявленного scope" in findings[0].message


def test_merge_absent_from_index_and_scope_is_out_of_scope() -> None:
    """Строка 6 в чистом виде: чужой мерж в scope, наш — ни там, ни там."""
    facts = _facts(
        scope=[RequestId("merge_sha", OTHER_SHA)],
        results=[
            Result(
                RequestId("merge_sha", OTHER_SHA),
                "merged",
                OTHER_SHA,
                "github:andrei-shtanakov",
                "User",
                "human",
            )
        ],
    )
    findings = _check(facts, git=_git(sha=SHA))
    assert len(findings) == 1
    assert "вне объявленного scope" in findings[0].message


def test_not_found_for_requested_sha_is_source_conflict() -> None:
    """Строка 7."""
    facts = _facts(
        scope=[RequestId("merge_sha", SHA)],
        results=[Result(RequestId("merge_sha", SHA), "not_found")],
    )
    findings = _check(facts, git=_git(sha=SHA))
    assert len(findings) == 1
    assert "противоречие источников" in findings[0].message


def test_no_matching_pr_for_requested_sha_is_source_conflict() -> None:
    """Строка 8."""
    facts = _facts(
        scope=[RequestId("merge_sha", SHA)],
        results=[Result(RequestId("merge_sha", SHA), "no_matching_pr")],
    )
    findings = _check(facts, git=_git(sha=SHA))
    assert len(findings) == 1
    assert "противоречие источников" in findings[0].message


def test_two_source_conflicts_have_distinct_messages() -> None:
    """Сценарий 16: два случая, два РАЗНЫХ сообщения."""
    not_found = _facts(
        scope=[RequestId("merge_sha", SHA)],
        results=[Result(RequestId("merge_sha", SHA), "not_found")],
    )
    no_pr = _facts(
        scope=[RequestId("merge_sha", SHA)],
        results=[Result(RequestId("merge_sha", SHA), "no_matching_pr")],
    )
    a = _check(not_found, git=_git(sha=SHA))[0].message
    b = _check(no_pr, git=_git(sha=SHA))[0].message
    assert a != b


def test_actor_unavailable_is_distinct_from_unknown() -> None:
    """Строки 9 и 10 (сценарии 5 и 6)."""
    unavailable = _facts(
        scope=[RequestId("merge_sha", SHA)],
        results=[Result(RequestId("merge_sha", SHA), "actor_unavailable", SHA)],
    )
    unknown = _facts(
        scope=[RequestId("merge_sha", SHA)],
        results=[
            Result(RequestId("merge_sha", SHA), "merged", SHA, "github:stranger", "User", "unknown")
        ],
    )
    a = _check(unavailable, git=_git(sha=SHA))[0].message
    b = _check(unknown, git=_git(sha=SHA))[0].message
    assert a != b, "две разные причины обязаны быть различимы в сообщении"


def test_classified_unknown_gives_finding() -> None:
    """Строка 10 (сценарий 6): fail-closed, личность названа."""
    facts = _facts(
        scope=[RequestId("merge_sha", SHA)],
        results=[
            Result(RequestId("merge_sha", SHA), "merged", SHA, "github:stranger", "User", "unknown")
        ],
    )
    findings = _check(facts, git=_git(sha=SHA))
    assert len(findings) == 1
    assert "github:stranger" in findings[0].message


def test_unknown_stays_fail_closed_under_allowing_policy() -> None:
    """`agent_merge_allowed` расширяет РОВНО одну классификацию."""
    facts = _facts(
        scope=[RequestId("merge_sha", SHA)],
        results=[
            Result(RequestId("merge_sha", SHA), "merged", SHA, "github:stranger", "User", "unknown")
        ],
    )
    findings = _check(facts, git=_git(sha=SHA), policy=POLICY_ALLOWING)
    assert len(findings) == 1


def test_human_merge_yields_no_finding() -> None:
    """Строка 13 (сценарий 7)."""
    facts = _facts(scope=[RequestId("merge_sha", SHA)], results=[HUMAN])
    assert _check(facts, git=_git(sha=SHA)) == []


def test_agent_merge_denied_by_default() -> None:
    """Строка 11 (сценарий 8): сообщение обязано назвать ключ политики."""
    facts = _facts(scope=[RequestId("merge_sha", SHA)], results=[AGENT])
    findings = _check(facts, git=_git(sha=SHA))
    assert len(findings) == 1
    assert "agent_merge_allowed" in findings[0].message


def test_agent_merge_allowed_by_policy() -> None:
    """Строка 12 (сценарий 9)."""
    facts = _facts(scope=[RequestId("merge_sha", SHA)], results=[AGENT])
    assert _check(facts, git=_git(sha=SHA), policy=POLICY_ALLOWING) == []


def test_gate_does_not_reclassify_the_actor_locally() -> None:
    """§8.1: владелец классификации один. `actor_class` в файле выпущен под
    политикой, чей digest уже сверен (строка 3), поэтому гейт применяет его,
    а не пересчитывает — иначе появились бы два policy engine, способных
    разойтись. Личность здесь НЕ входит ни в один список политики: локальный
    пересчёт дал бы `unknown` и находку."""
    facts = _facts(
        scope=[RequestId("merge_sha", SHA)],
        results=[
            Result(RequestId("merge_sha", SHA), "merged", SHA, "github:newcomer", "User", "human")
        ],
    )
    assert _check(facts, git=_git(sha=SHA)) == []


def test_aliases_that_agree_are_used_by_the_gate() -> None:
    """Сценарий 18 со стороны гейта: `pr:N` и `merge_sha:<oid>` в одном scope,
    согласованные наблюдения — индекс разрешённых SHA находит мерж."""
    merged_by_pr = Result(
        RequestId("pr", 42), "merged", SHA, "github:andrei-shtanakov", "User", "human"
    )
    facts = _facts(
        scope=[RequestId("pr", 42), RequestId("merge_sha", SHA)],
        results=[merged_by_pr, HUMAN],
    )
    assert _check(facts, git=_git(sha=SHA)) == []


def test_index_holds_only_resolved_shas() -> None:
    """§8.2(1): записи без разрешённого SHA в индекс не входят. `not_merged`
    по PR-запросу не должен «находиться» ни по какому SHA."""
    facts = _facts(
        scope=[RequestId("pr", 42)],
        results=[Result(RequestId("pr", 42), "not_merged")],
    )
    assert facts.by_merge_sha() == {}


def test_stale_facts_do_not_satisfy_a_human_merge(tmp_path: Path) -> None:
    """Сквозной: строки 3-5 обязаны отсекать наблюдение ДО терминального
    результата. Файл содержит человеческий мерж и всё равно даёт находку."""
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_valid(tmp_path, policy_path=policy_path, generated_at=NOW - timedelta(days=2))
    outcome = resolve_facts(
        path,
        expected_repository=REPO,
        policy=policy,
        policy_path=policy_path,
        now=NOW,
        explicit=False,
    )
    findings = _check(outcome, git=_git(sha=SHA))
    assert len(findings) == 1
    assert "просрочен" in findings[0].message


def test_fresh_facts_from_the_same_file_do_satisfy_it(tmp_path: Path) -> None:
    """Парный контроль к предыдущему: тот же файл, но свежий, — находок нет."""
    policy, policy_path = _loaded_policy(tmp_path)
    path = _write_valid(tmp_path, policy_path=policy_path)
    outcome = resolve_facts(
        path,
        expected_repository=REPO,
        policy=policy,
        policy_path=policy_path,
        now=NOW,
        explicit=False,
    )
    assert _check(outcome, git=_git(sha=SHA)) == []


# --------------------------------------------------------------------------
# Разводка CLI: путь бандла, override, наблюдаемый репозиторий из `origin`.
# --------------------------------------------------------------------------


def _checkout(tmp_path: Path, *, origin: str | None, name: str = "checkout") -> Path:
    root = tmp_path / name
    root.mkdir()
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    if origin is not None:
        subprocess.run(
            ["git", "remote", "add", "origin", origin], cwd=root, capture_output=True, check=True
        )
    return root


ORIGIN = "git@github.com:andrei-shtanakov/steward.git"


def test_cli_reads_the_bundle_default_path(tmp_path: Path) -> None:
    policy, policy_path = _loaded_policy(tmp_path)
    root = _checkout(tmp_path, origin=ORIGIN)
    (root / ".steward").mkdir()
    _write_valid(root / ".steward", policy_path=policy_path)
    outcome = approval_facts_outcome(None, root, policy=policy, policy_path=policy_path, now=NOW)
    assert isinstance(outcome, ApprovalFactsV2)


def test_cli_missing_bundle_file_is_absent(tmp_path: Path) -> None:
    policy, policy_path = _loaded_policy(tmp_path)
    root = _checkout(tmp_path, origin=ORIGIN)
    outcome = approval_facts_outcome(None, root, policy=policy, policy_path=policy_path, now=NOW)
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "absent"


def test_cli_explicit_override_wins_over_bundle_default(tmp_path: Path) -> None:
    """`--approval-facts` — override: читается указанный файл, не бандл."""
    policy, policy_path = _loaded_policy(tmp_path)
    root = _checkout(tmp_path, origin=ORIGIN)
    (root / ".steward").mkdir()
    _write_valid(root / ".steward", policy_path=policy_path)  # валидный в бандле
    override = _write_v1(tmp_path, name="override.jsonl")  # заведомо легаси
    with pytest.raises(ConfigError):
        approval_facts_outcome(override, root, policy=policy, policy_path=policy_path, now=NOW)


def test_cli_explicit_override_reads_a_file_outside_the_bundle(tmp_path: Path) -> None:
    """Позитивная половина того же: указанный файл читается вместо бандла.
    Здесь оба файла ВАЛИДНЫ и различаются только свежестью, поэтому тест
    пинает выбор пути, а не поведение на невалидном файле."""
    policy, policy_path = _loaded_policy(tmp_path)
    root = _checkout(tmp_path, origin=ORIGIN)
    (root / ".steward").mkdir()
    _write_valid(  # в бандле — просроченный
        root / ".steward", policy_path=policy_path, generated_at=NOW - timedelta(days=2)
    )
    override = _write_valid(tmp_path, policy_path=policy_path, name="override.jsonl")
    outcome = approval_facts_outcome(
        override, root, policy=policy, policy_path=policy_path, now=NOW
    )
    assert isinstance(outcome, ApprovalFactsV2)


def test_cli_without_origin_is_absent_not_a_crash(tmp_path: Path) -> None:
    """Правило задачи: репозиторий неопознаваем — гейт обязан не падать."""
    policy, policy_path = _loaded_policy(tmp_path)
    root = _checkout(tmp_path, origin=None)
    outcome = approval_facts_outcome(None, root, policy=policy, policy_path=policy_path, now=NOW)
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "absent"


def test_cli_unparseable_origin_is_absent_not_a_crash(tmp_path: Path) -> None:
    policy, policy_path = _loaded_policy(tmp_path)
    root = _checkout(tmp_path, origin="not-a-url")
    outcome = approval_facts_outcome(None, root, policy=policy, policy_path=policy_path, now=NOW)
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "absent"


def test_cli_outside_git_repository_is_absent(tmp_path: Path) -> None:
    policy, policy_path = _loaded_policy(tmp_path)
    outside = tmp_path / "loose"
    outside.mkdir()
    outcome = approval_facts_outcome(None, outside, policy=policy, policy_path=policy_path, now=NOW)
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "absent"


def test_cli_observed_repository_comes_from_origin(tmp_path: Path) -> None:
    """`expected_repository` выводится из `origin` ЭТОГО чекаута: файл с
    заголовком другого владельца по пути бандла становится невалидным."""
    policy, policy_path = _loaded_policy(tmp_path)
    root = _checkout(tmp_path, origin="git@github.com:other-owner/steward.git")
    (root / ".steward").mkdir()
    _write_valid(root / ".steward", policy_path=policy_path)  # header: andrei-shtanakov/steward
    outcome = approval_facts_outcome(None, root, policy=policy, policy_path=policy_path, now=NOW)
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "unreadable"
