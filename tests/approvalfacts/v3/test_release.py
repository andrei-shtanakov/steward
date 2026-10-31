"""Release-продюсер `approval-facts/v3` (спека steward §6): `resolve_release`,
`classify_release`, `release_record`, `materialize_release`.

Ответы форджа — только из живых снимков (`pr657_act_sequence`, `closed606_act_sequence`,
`e7_commit_pulls_graphql_merge`, `neg_e2_delivery_absent_pr`) и их производных
преобразований §5 (фикстура `derive`). Обмены продюсера: `pr` — E2, E1, E5 (у
несмёрженного — E2, E1); `merge_sha` — E7, затем те же E2, E1, E5 найденного PR.
Продюсер и читатель v3 в этой задаче неактивны: CLI и гейт остаются на v2 (Task 11
переключает их одним изменением).
"""

import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from steward.approvalfacts import producer
from steward.approvalfacts.model import Header, RequestId, Result, scope_digest
from steward.approvalfacts.producer import MechanicalFailure
from steward.approvalfacts.publish import build_header, publish
from steward.approvalfacts.reader import UnreadableFacts
from steward.approvalfacts.v3._github import _Fail
from steward.approvalfacts.v3.model import Delivery, PathBlob
from steward.approvalfacts.v3.release import (
    SCHEMA_VERSION_V3,
    ReleaseResult,
    classify_release,
    materialize_release,
    release_record,
    resolve_release,
)
from steward.approvalfacts.v3.transport import ReplayTransport, replay_transport
from steward.gatecheck import approval as gate_approval
from steward.gatecheck.approval import ApprovalPolicy
from steward.riskclassify.cli import app

Record = dict[str, Any]
Derive = Callable[[list[str], list[Record]], Record]

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads(
    (ROOT / "contracts" / "approval-facts" / "v3" / "SCHEMA.json").read_text(encoding="utf-8")
)
OWNER, NAME = "andrei-shtanakov", "devtools"
REPO = f"{OWNER}/{NAME}"
SEQ, E7 = "pr657_act_sequence", "e7_commit_pulls_graphql_merge"
MERGE = "aa4e7e37d9f1ea34581c9c484d60074b6d3ac18e"
HEAD = "b3651bcb2907bd6d1b6d421b018701520fb64066"
MERGED_AT = "2026-10-30T12:09:17Z"
OTHER = "1111111111111111111111111111111111111111"
GQL_MERGER = [0, "response", "body", "data", "repository", "pullRequest", "mergedBy"]
REST_MERGER = [1, "response", "body", "merged_by"]
E7_OBJECT = [0, "response", "body", "data", "repository", "object"]
E7_NODE = [*E7_OBJECT, "associatedPullRequests", "nodes", 0]
POLICY = ApprovalPolicy(
    version=1,
    human_identities=frozenset({"github:andrei-shtanakov"}),
    agent_identities=frozenset({"github:ai-prosto"}),
)
NOW = datetime(2026, 10, 31, 9, 0, 0, tzinfo=UTC)
PR657 = RequestId("pr", 657)
SHA657 = RequestId("merge_sha", MERGE)


def _tf(op: str, path: list[Any], **kw: Any) -> Record:
    return {"op": op, "path": path, **kw}


def _pr657(derive: Derive, transforms: list[Record] | None = None) -> ReplayTransport:
    """E2, E1, E5 снимка `pr657_act_sequence` после преобразований."""
    keep = {"op": "select", "keep": [0, 1, 2]}
    return replay_transport(derive([SEQ], [*(transforms or []), keep]))


def _by_sha(derive: Derive, transforms: list[Record] | None = None) -> ReplayTransport:
    """E7 + E2, E1, E5 PR #657 (составной вектор в порядке запросов продюсера)."""
    keep = {"op": "select", "keep": [0, 1, 2, 3]}
    return replay_transport(derive([E7, SEQ], [*(transforms or []), keep]))


def _resolve(t: ReplayTransport, request: RequestId) -> ReleaseResult:
    got = resolve_release(t, OWNER, NAME, request)
    assert t.unused() == [] and t.unexpected == [], (t.unused(), t.unexpected)
    return got


def _mechanical(t: ReplayTransport, request: RequestId) -> MechanicalFailure:
    with pytest.raises(MechanicalFailure) as failed:
        resolve_release(t, OWNER, NAME, request)
    return failed.value


def _delivery(login: str | None, type_hint: str | None) -> Delivery:
    return Delivery(True, MERGE, MERGED_AT, login, type_hint, HEAD, True, ())


# --- pr ------------------------------------------------------------------------------------


def test_pr_merged_record_carries_delivery(derive: Derive) -> None:
    """Мержер `ai-prosto` (производный от живого #657 на обеих сторонах E2/E1) — агент."""
    t = _pr657(
        derive,
        [
            _tf("set_field", GQL_MERGER, value={"login": "ai-prosto", "__typename": "User"}),
            _tf("set_field", [*REST_MERGER, "login"], value="ai-prosto"),
        ],
    )
    r = _resolve(t, PR657)
    assert r.state == "merged" and r.delivery is not None and r.delivery.merge_commit == MERGE
    assert r.actor_class is None, "resolve свободен от политики"
    (classified,) = classify_release([r], POLICY)
    record = release_record(classified)
    assert record["delivery"]["merged_by"] == {
        "identity": "github:ai-prosto",
        "type_hint": "User",
        "actor_class": "agent",
    }
    assert record["request"] == {"kind": "pr", "value": 657}
    assert record["delivery"]["paths"] == [] and record["delivery"]["in_base"] is True


def test_pr_merged_on_unchanged_live_capture(derive: Derive) -> None:
    r = _resolve(_pr657(derive), PR657)
    assert r == ReleaseResult(PR657, "merged", _delivery("andrei-shtanakov", "User"), None)
    (classified,) = classify_release([r], POLICY)
    assert classified.actor_class == "human"


def test_pr_not_merged_has_null_delivery() -> None:
    """Несмёрженный #606 (REST несёт тестовый `merge_commit_sha`) — `not_merged`, E2, E1."""
    spec = json.loads(
        (ROOT / "tests/fixtures/approvalfacts_v3/live/closed606_act_sequence.json").read_text()
    )
    r = _resolve(replay_transport(spec), RequestId("pr", 606))
    assert r == ReleaseResult(RequestId("pr", 606), "not_merged", None, None)
    assert release_record(r)["delivery"] is None


def test_absent_pr_is_mechanical_failure_not_not_found(exchanges: Callable) -> None:
    """В v3 нет терпимости v2 к `NOT_FOUND` (спека §3.2): отсутствующий PR — сбой."""
    t = replay_transport({"exchanges": exchanges("neg_e2_delivery_absent_pr")})
    failure = _mechanical(t, RequestId("pr", 999999))
    assert "graphql_errors" in str(failure)


# --- merge_sha (строка E7) -----------------------------------------------------------------


def test_merge_sha_resolves_via_associated_pr(derive: Derive) -> None:
    t = _by_sha(derive)
    r = _resolve(t, SHA657)
    assert r == ReleaseResult(SHA657, "merged", _delivery("andrei-shtanakov", "User"), None)
    ops = [req.op or req.path for req in t.log]
    assert ops == [
        "StewardV3CommitPulls",
        "StewardV3Delivery",
        f"repos/{OWNER}/{NAME}/pulls/657",
        f"repos/{OWNER}/{NAME}/compare/master...{MERGE}",
    ]


def test_no_matching_pr_and_not_found_have_null_delivery(derive: Derive) -> None:
    """`object: null` (E7, `null_ok_at="object"`) — `not_found`; PR коммита с другим
    `mergeCommit` — `no_matching_pr`. Ни в одном случае доставка не читается."""
    not_found = replay_transport(derive([E7], [_tf("set_field", E7_OBJECT, value=None)]))
    assert _resolve(not_found, SHA657) == ReleaseResult(SHA657, "not_found", None, None)
    for merge_commit in (None, {"oid": OTHER}):
        t = replay_transport(
            derive([E7], [_tf("set_field", [*E7_NODE, "mergeCommit"], value=merge_commit)])
        )
        r = _resolve(t, SHA657)
        assert r == ReleaseResult(SHA657, "no_matching_pr", None, None)
        assert release_record(r) == {
            "kind": "result",
            "request": {"kind": "merge_sha", "value": MERGE},
            "state": "no_matching_pr",
            "delivery": None,
        }


@pytest.mark.parametrize(
    "transform",
    [
        _tf("set_field", [*E7_OBJECT, "associatedPullRequests"], value=None),
        _tf("set_field", [*E7_NODE, "number"], value="657"),
        _tf("drop_field", [*E7_NODE, "mergeCommit"]),
        _tf("set_field", [*E7_NODE, "mergeCommit"], value={"oid": 5}),
    ],
    ids=["connection_null", "number_not_int", "merge_commit_missing", "oid_not_str"],
)
def test_malformed_commit_pulls_is_mechanical_failure(derive: Derive, transform: Record) -> None:
    """`null` разрешён только у `object` (E7); прочие нарушения формы — сбой."""
    failure = _mechanical(replay_transport(derive([E7], [transform])), SHA657)
    assert "malformed" in str(failure)


def test_merge_sha_whose_pr_is_not_merged_is_mechanical_failure(derive: Derive) -> None:
    """PR, чей `mergeCommit` — запрошенный SHA, по доставке не смёржен: противоречие."""
    t = replay_transport(
        derive(
            [E7, "closed606_act_sequence"],
            [_tf("set_field", [*E7_NODE, "number"], value=606)],
        )
    )
    assert "not merged" in str(_mechanical(t, SHA657))


def test_merge_sha_with_other_delivery_merge_commit_is_mechanical_failure(
    derive: Derive,
) -> None:
    t = _by_sha(
        derive,
        [
            _tf("set_field", [0, "request", "variables", "sha"], value=OTHER),
            _tf("set_field", [*E7_NODE, "mergeCommit"], value={"oid": OTHER}),
        ],
    )
    assert "merge commit" in str(_mechanical(t, RequestId("merge_sha", OTHER)))


# --- мержер не установлен → actor_unavailable; прочие отказы → MechanicalFailure -------


@pytest.mark.parametrize(
    "transforms",
    [
        [_tf("set_field", GQL_MERGER, value=None), _tf("set_field", REST_MERGER, value=None)],
        [_tf("set_field", GQL_MERGER, value={"login": "ghost", "__typename": "User"})],
        [_tf("set_field", GQL_MERGER, value={"login": "m", "__typename": "Mannequin"})],
        [_tf("set_field", [*REST_MERGER, "login"], value="andrei-shtanakov[bot]")],
    ],
    ids=["both_null", "graphql_ghost", "graphql_mannequin", "rest_bot_suffix_user_type"],
)
def test_actor_unavailable_has_delivery_with_null_merged_by(
    derive: Derive, transforms: list[Record]
) -> None:
    """`actor_unresolved` мержера (любая сторона, §3.4.1) — `actor_unavailable` с доставкой
    без мержера (спека §6); гейт отвергает такую запись строкой 9."""
    r = _resolve(_pr657(derive, transforms), PR657)
    assert r == ReleaseResult(PR657, "actor_unavailable", _delivery(None, None), None)
    (classified,) = classify_release([r], POLICY)
    assert classified == r, "классификация не трогает actor_unavailable"
    record = release_record(classified)
    assert record["delivery"]["merged_by"] is None
    assert record["delivery"]["merge_commit"] == MERGE and record["delivery"]["merged"] is True


def test_actor_unavailable_via_merge_sha(derive: Derive) -> None:
    t = _by_sha(derive, [_tf("set_field", [1, *GQL_MERGER[1:]], value=None)])
    assert _resolve(t, SHA657).state == "actor_unavailable"


@pytest.mark.parametrize(
    "transforms,code",
    [
        (
            [_tf("set_field", [0, *GQL_MERGER[1:-1], "merged"], value=False)],
            "delivery_mismatch",
        ),
        (
            [_tf("set_field", GQL_MERGER, value={"login": "someone", "__typename": "User"})],
            "bot_login_mismatch",
        ),
        ([_tf("set_field", [2, "response", "body", "status"], value="weird")], "malformed"),
        ([_tf("set_field", [0, "response", "status"], value=502)], "http"),
        (
            [
                _tf("set_field", GQL_MERGER, value=None),
                _tf("retime", [1, "response", "body", "merged_at"], ts="2026-10-30T12:09:18Z"),
            ],
            "delivery_mismatch",
        ),
        (
            [
                _tf("set_field", GQL_MERGER, value=None),
                _tf("set_field", [1, "response", "body", "merge_commit_sha"], value="zz"),
            ],
            "malformed",
        ),
    ],
    ids=[
        "merged_differs",
        "merger_differs",
        "compare_status",
        "http",
        "no_merger_still_checks_merged_at",
        "no_merger_still_checks_sha_shape",
    ],
)
def test_other_delivery_unavailable_is_mechanical_failure(
    derive: Derive, transforms: list[Record], code: str
) -> None:
    """Любой `Unavailable` доставки, кроме `actor_unresolved` мержера, — сбой, не запись."""
    failure = _mechanical(_pr657(derive, transforms), PR657)
    assert isinstance(failure.__cause__, _Fail) and failure.__cause__.code == code


def test_delivery_unavailable_is_mechanical_failure(derive: Derive) -> None:
    """Транспорт кончился посреди доставки (`_Fail: transport`) — `MechanicalFailure`."""
    t = replay_transport(derive([SEQ], [{"op": "select", "keep": [0]}]))
    failure = _mechanical(t, PR657)
    assert isinstance(failure.__cause__, _Fail) and failure.__cause__.code == "transport"


# --- materialize, release_record ------------------------------------------------------------


def test_materialize_release_resolves_scope_in_order(derive: Derive) -> None:
    # `pr657_act_sequence` — 10 обменов: E2, E1, E5 первого — 0..2; E7 — 10; второй — 11..13.
    keep = {"op": "select", "keep": [0, 1, 2, 10, 11, 12, 13]}
    t = replay_transport(derive([SEQ, E7, SEQ], [keep]))
    got = materialize_release(t, REPO, [PR657, SHA657])
    assert [r.request for r in got] == [PR657, SHA657]
    assert {r.delivery for r in got} == {_delivery("andrei-shtanakov", "User")}
    assert t.unused() == []


@pytest.mark.parametrize("repo", ["devtools", "a/b/c", "/devtools", "owner/"])
def test_materialize_release_rejects_bad_repo(repo: str) -> None:
    with pytest.raises(ValueError):
        materialize_release(replay_transport({"exchanges": []}), repo, [PR657])


@pytest.mark.parametrize(
    "result",
    [
        ReleaseResult(PR657, "merged", _delivery("andrei-shtanakov", "User"), None),
        ReleaseResult(PR657, "merged", None, None),
        ReleaseResult(PR657, "merged", _delivery(None, None), None),
        ReleaseResult(PR657, "actor_unavailable", _delivery("andrei-shtanakov", "User"), None),
        ReleaseResult(PR657, "actor_unavailable", None, None),
        ReleaseResult(PR657, "not_merged", _delivery("andrei-shtanakov", "User"), None),
        ReleaseResult(SHA657, "not_found", None, "human"),
        ReleaseResult(
            PR657, "merged", Delivery(False, None, None, None, None, None, False, ()), "human"
        ),
    ],
    ids=[
        "merged_unclassified",
        "merged_without_delivery",
        "merged_without_merger",
        "actor_unavailable_with_merger",
        "actor_unavailable_without_delivery",
        "negative_with_delivery",
        "negative_with_actor_class",
        "merged_with_unmerged_delivery",
    ],
)
def test_release_record_rejects_state_matrix_violation(result: ReleaseResult) -> None:
    """Запись — только по матрице полей спеки §6; иначе `ValueError`, не запись."""
    with pytest.raises(ValueError):
        release_record(result)


def test_release_record_rejects_paths() -> None:
    """`paths` в файле гейта — `[]` (спека §6)."""
    d = Delivery(True, MERGE, MERGED_AT, "a", "User", HEAD, True, (PathBlob("x", None, MERGE),))
    with pytest.raises(ValueError):
        release_record(ReleaseResult(PR657, "merged", d, "human"))


# --- публикация -----------------------------------------------------------------------------


def _all_states() -> list[ReleaseResult]:
    return [
        ReleaseResult(PR657, "merged", _delivery("ai-prosto", "Bot"), "agent"),
        ReleaseResult(SHA657, "actor_unavailable", _delivery(None, None), None),
        ReleaseResult(RequestId("pr", 606), "not_merged", None, None),
        ReleaseResult(RequestId("merge_sha", OTHER), "not_found", None, None),
        ReleaseResult(RequestId("merge_sha", HEAD), "no_matching_pr", None, None),
    ]


def test_published_v3_validates_schema(tmp_path: Path) -> None:
    results = _all_states()
    header = build_header(
        repository=REPO,
        scope=[r.request for r in results],
        policy_version=1,
        policy_digest_value="sha256:" + "0" * 64,
        lease_seconds=86400,
        now=NOW,
    )
    path = tmp_path / ".steward" / "approval_facts.jsonl"
    publish(path, header, results, schema_version=SCHEMA_VERSION_V3, result_record=release_record)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    validator = Draft202012Validator(SCHEMA)
    for line in lines:
        validator.validate(line)
    assert lines[0]["schema_version"] == "3" == SCHEMA_VERSION_V3
    assert [line["state"] for line in lines[1:]] == [r.state for r in results]


#: sha256 файла, который v2-публикатор писал до Task 10 (снят на 6693ca0); не меняется
#: и после переключения Task 11 — низкоуровневый v2-публикатор остаётся прежним.
V2_GOLDEN_SHA256 = "07730b3538b74e07658479b36dc766d1eda4bacd752d140573d12b76515bcf7d"
V2_SHA = "221457933968be9e95acd51d548e080f739c794c"


def test_v2_publish_bytes_unchanged(tmp_path: Path) -> None:
    scope = [RequestId("pr", 42), RequestId("merge_sha", V2_SHA), RequestId("pr", 7)]
    header = Header(
        repository="andrei-shtanakov/steward",
        generated_at=datetime(2026, 8, 21, 12, tzinfo=UTC),
        valid_until=datetime(2026, 8, 22, 12, tzinfo=UTC),
        policy_version=1,
        policy_digest="sha256:" + "0" * 64,
        scope=tuple(scope),
        scope_sha256=scope_digest(scope),
    )
    path = tmp_path / "facts.jsonl"
    publish(
        path,
        header,
        [
            Result(scope[0], "merged", V2_SHA, "github:merge-broker", "Bot", "agent"),
            Result(scope[1], "actor_unavailable", V2_SHA),
            Result(scope[2], "not_merged"),
        ],
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == V2_GOLDEN_SHA256


# --- переключения нет (временный тест, удаляется коммитом переключения Task 11) -----------


def test_cli_and_gate_still_v2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ВРЕМЕННЫЙ: `steward approval-facts` пишет `schema_version: "2"`, гейт читает v2 и
    отвергает v3. Task 11 заменяет его `test_cli_writes_schema_version_3` и
    `test_gate_reads_v3`."""
    policy = tmp_path / "approval-policy.yaml"
    policy.write_text(
        "version: 1\nhuman_identities: []\nagent_identities:\n  - github:merge-broker\n"
        "approval_facts_lease_seconds: 86400\n",
        encoding="utf-8",
    )
    body = {
        "data": {
            "repository": {
                "pullRequest": {
                    "mergeCommit": {"oid": V2_SHA},
                    "mergedBy": {"login": "merge-broker", "__typename": "Bot"},
                }
            }
        }
    }
    monkeypatch.setattr(producer, "_gh", lambda args: (0, json.dumps(body), ""))
    out = tmp_path / "facts.jsonl"
    root = tmp_path / "root"
    root.mkdir()
    args = ["approval-facts", "--repo", "andrei-shtanakov/steward", "--repo-root", str(root)]
    args += ["--prs", "42", "--out", str(out), "--policy", str(policy)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text(encoding="utf-8").splitlines()[0])["schema_version"] == "2"
    gate_now = datetime.now(UTC)
    assert gate_approval.load_facts(
        out, expected_repository="andrei-shtanakov/steward", now=gate_now
    )

    v3 = tmp_path / "v3.jsonl"
    results = [ReleaseResult(PR657, "merged", _delivery("ai-prosto", "Bot"), "agent")]
    header = build_header(
        repository="andrei-shtanakov/steward",
        scope=[PR657],
        policy_version=1,
        policy_digest_value="sha256:" + "0" * 64,
        lease_seconds=86400,
        now=gate_now,
    )
    publish(v3, header, results, schema_version=SCHEMA_VERSION_V3, result_record=release_record)
    with pytest.raises(UnreadableFacts, match="schema_version"):
        gate_approval.load_facts(v3, expected_repository="andrei-shtanakov/steward", now=gate_now)


# --- граница импорта и замороженный v2 ------------------------------------------------------


def test_release_reader_does_not_import_policy_or_v2_producer() -> None:
    """Гейт (Task 11) импортирует `release_reader`; тот не тянет `gatecheck` и v2-продюсер
    (тот импортирует `gatecheck.approval`) — иначе цикл импорта. Оба порядка импорта
    `release` и `gatecheck.approval` в свежем интерпретаторе проходят."""
    probe = (
        "import sys; import steward.approvalfacts.v3.release_reader; "
        "bad = [m for m in ('steward.gatecheck.approval', 'steward.approvalfacts.producer', "
        "'steward.approvalfacts.v3.release') if m in sys.modules]; assert not bad, bad"
    )
    orders = [
        probe,
        "import steward.approvalfacts.v3.release; import steward.gatecheck.approval",
        "import steward.gatecheck.approval; import steward.approvalfacts.v3.release",
    ]
    for code in orders:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert proc.returncode == 0, (code, proc.stderr)


#: sha256 файлов `contracts/approval-facts/v2/**` (сняты на 6693ca0): v2 заморожен.
V2_CONTRACT_SHA256 = {
    "README.md": "3258ac6f600a8a74c0b882619bbfcfca8201a86195a2b6340fd819584cc34c50",
    "SCHEMA.json": "1ba3dd0122f60c00f1ca214291dc7cff6adfe543272f1ce5f4df6d6d07c77441",
    "fixtures/bad_state_for_kind.jsonl": (
        "1cef0ac079b244a9bb74c5d00f3d41d52c799207a6b85999ae3b789c78f7175a"
    ),
    "fixtures/bad_timestamp_format.jsonl": (
        "b527587b575bd6a2a99011c529133ebd72e47ccb34384949a072ae784c2630ab"
    ),
    "fixtures/clean.jsonl": "98f15fcf0c5c7cbec7130cb35cd578d5a9fe3369a389b4bccf8fcd20da7bdfdb",
    "fixtures/extra_field_on_negative.jsonl": (
        "a00a8796ce02a956e54ebc45c3241c0c23f64bd4b829a0f4e59aa44908e51fc8"
    ),
    "fixtures/missing_merge_sha_negative.jsonl": (
        "1f25e29614efeb8c0c5e1aef8042f5d2630ed11595f149ab3761d99ec87c9dbe"
    ),
    "fixtures/negative_states.jsonl": (
        "b814b88acc2cabf579a08652fd3366488ad8646851d92c23389b276e954f1757"
    ),
}


def test_v2_contract_untouched() -> None:
    v2 = ROOT / "contracts" / "approval-facts" / "v2"
    got = {
        p.relative_to(v2).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(v2.rglob("*"))
        if p.is_file()
    }
    assert got == V2_CONTRACT_SHA256
