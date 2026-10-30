"""Доставка PR: `read_delivery`, `read_paths` (спека steward §3.3 пп. 1–4, строки E1, E2, E5,
E6 §3.8).

Положительные двойники — неизменённые живые снимки (`pr657`/`pr636`/`closed606_act_sequence`,
отрицательный трёхпутевой `neg_e6_blobs_present_absent_tree`). Отрицательные — производные
преобразований §5 (фикстура `derive`) от `pr657_act_sequence`: обмены 0 (E2), 1 (E1), 2 (E5),
3 (E6); каждое нарушение — ровно одно. Чтение доставки потребляет первые три обмена,
`read_paths` — четвёртый.
"""

from collections.abc import Callable
from typing import Any

import pytest

from steward.approvalfacts.v3._github import _Fail
from steward.approvalfacts.v3.delivery import NOT_MERGED, read_delivery, read_paths
from steward.approvalfacts.v3.model import Delivery, PathBlob
from steward.approvalfacts.v3.queries import q_blobs
from steward.approvalfacts.v3.transport import ReplayTransport, replay_transport

Record = dict[str, Any]
Derive = Callable[[list[str], list[Record]], Record]

OWNER, NAME = "andrei-shtanakov", "devtools"
SEQ = "pr657_act_sequence"
E2, E1, E5, E6 = 0, 1, 2, 3
MERGE = "aa4e7e37d9f1ea34581c9c484d60074b6d3ac18e"
HEAD = "b3651bcb2907bd6d1b6d421b018701520fb64066"
MERGED_AT = "2026-10-30T12:09:17Z"
P = "docs/evidence/2026-10-30-df-stage2a-acceptance.md"
BLOB = "1d902e2395fb61e960eefc21ef04f471d139e774"
GQL_PULL = ["response", "body", "data", "repository", "pullRequest"]
REST_PULL = ["response", "body"]
BLOBS = ["response", "body", "data", "repository"]


def _tf(op: str, path: list[Any], **kw: Any) -> Record:
    return {"op": op, "path": path, **kw}


def _replay(spec: Record, keep: slice) -> ReplayTransport:
    return replay_transport({"exchanges": spec["exchanges"][keep]})


def _delivery(derive: Derive, transforms: list[Record]) -> tuple[Delivery, ReplayTransport]:
    t = _replay(derive([SEQ], transforms), slice(E2, E5 + 1))
    return read_delivery(t, OWNER, NAME, 657), t


def _delivery_code(derive: Derive, transforms: list[Record]) -> str:
    with pytest.raises(_Fail) as failed:
        _delivery(derive, transforms)
    return failed.value.code


def _paths(derive: Derive, transforms: list[Record], paths: list[str] | None = None):
    t = _replay(derive([SEQ], transforms), slice(E6, E6 + 1))
    return read_paths(t, OWNER, NAME, MERGE, HEAD, [P] if paths is None else paths), t


def _paths_code(derive: Derive, transforms: list[Record]) -> str:
    with pytest.raises(_Fail) as failed:
        _paths(derive, transforms)
    return failed.value.code


# --- положительные двойники E1/E2/E5 на неизменённых живых снимках ------------------------


@pytest.mark.parametrize(
    "name,pr,expected",
    [
        (
            "pr657_act_sequence",
            657,
            Delivery(True, MERGE, MERGED_AT, "andrei-shtanakov", "User", HEAD, True, ()),
        ),
        (
            "pr636_act_sequence",
            636,
            Delivery(
                True,
                "1f350c74a4909454619b820d29cbaea244a68733",
                "2026-10-26T15:48:49Z",
                "ai-prosto",
                "User",
                "dfe8b0110b2b81ff32fddcf2bc4bb845a5dfb120",
                True,  # compare `behind`
                (),
            ),
        ),
    ],
)
def test_read_delivery_live_positive_twin(name, pr, expected, exchanges, key, key_of):
    seq = exchanges(name)[E2 : E5 + 1]
    t = replay_transport({"exchanges": seq})
    assert read_delivery(t, OWNER, NAME, pr) == expected
    assert t.unused() == [] and [key(r) for r in t.log] == [key_of(ex) for ex in seq]


def test_unknown_state_of_unmerged_pr(derive):
    """`state` E2 — не поле мержа: проверяется и у несмёрженного PR (closed606)."""
    spec = derive(
        ["closed606_act_sequence"], [_tf("set_field", [E2, *GQL_PULL, "state"], value="DRAFT")]
    )
    with pytest.raises(_Fail) as failed:
        read_delivery(replay_transport(spec), OWNER, NAME, 606)
    assert failed.value.code == "unknown_state"


def test_read_delivery_not_merged_live(exchanges):
    """devtools #606: REST `merged=false` при ненулевом тестовом `merge_commit_sha` — поля
    мержа не сверяются, compare не запрашивается."""
    t = replay_transport({"exchanges": exchanges("closed606_act_sequence")})
    assert read_delivery(t, OWNER, NAME, 606) == NOT_MERGED
    assert t.unused() == [] and t.unexpected == []


# --- E1/E2: сведение форм ---------------------------------------------------------------


def test_bot_login_forms_agree_in_read_delivery(derive):
    bot = {"login": "df-reviewer", "__typename": "Bot"}
    d, _ = _delivery(
        derive,
        [
            _tf("set_field", [E2, *GQL_PULL, "mergedBy"], value=bot),
            _tf("set_field", [E1, *REST_PULL, "merged_by", "login"], value="df-reviewer[bot]"),
            _tf("set_field", [E1, *REST_PULL, "merged_by", "type"], value="Bot"),
        ],
    )
    assert (d.merged_by_login, d.merged_by_type_hint) == ("df-reviewer", "Bot")


def test_user_login_with_bot_suffix_is_not_reduced(derive):
    """Сведение `[bot]` — только при REST `type = "Bot"`: у `User` логин остаётся как есть
    и не устанавливается (§3.4.1, О9)."""
    code = _delivery_code(
        derive, [_tf("set_field", [E1, *REST_PULL, "merged_by", "login"], value="x[bot]")]
    )
    assert code == "actor_unresolved"


@pytest.mark.parametrize(
    "side,path,value",
    [
        ("graphql", "merged", "true"),
        ("rest", "merged", None),
        ("graphql", "mergeCommit", None),
        ("rest", "merge_commit_sha", None),
        ("graphql", "headRefOid", None),
        ("rest", "head", None),
        ("graphql", "baseRefName", 7),
        ("rest", "base", {"ref": ""}),
    ],
)
def test_merge_field_defect_is_malformed(derive, side, path, value):
    at = [E2, *GQL_PULL] if side == "graphql" else [E1, *REST_PULL]
    assert _delivery_code(derive, [_tf("set_field", [*at, path], value=value)]) == "malformed"


@pytest.mark.parametrize("side", ["graphql", "rest"])
def test_merged_at_bad_timestamp(derive, side):
    path = [E2, *GQL_PULL, "mergedAt"] if side == "graphql" else [E1, *REST_PULL, "merged_at"]
    code = _delivery_code(derive, [_tf("retime", path, ts="2026-10-30T12:09:17.5Z")])
    assert code == "bad_timestamp"


def test_graphql_pull_request_null_is_malformed(derive):
    t = _replay(derive([SEQ], [_tf("set_field", [E2, *GQL_PULL], value=None)]), slice(E2, E1 + 1))
    with pytest.raises(_Fail) as failed:
        read_delivery(t, OWNER, NAME, 657)
    assert failed.value.code == "malformed"


def test_absent_pr_live_negative_is_graphql_errors(exchanges):
    """G0: живой `200` + `errors[NOT_FOUND]` + `pullRequest: null` — отказ, а не «нет PR»."""
    ex = exchanges("neg_e2_delivery_absent_pr")[0]
    t = replay_transport({"exchanges": [ex]})
    number = ex["request"]["variables"]["number"]
    with pytest.raises(_Fail) as failed:
        read_delivery(t, OWNER, NAME, number)
    assert failed.value.code == "graphql_errors"


# --- SHA и пути REST (спека §3.2 «Пути REST») -------------------------------------------


@pytest.mark.parametrize(
    "side,path",
    [
        ("graphql", ["mergeCommit", "oid"]),
        ("graphql", ["headRefOid"]),
        ("rest", ["merge_commit_sha"]),
        ("rest", ["head", "sha"]),
    ],
)
@pytest.mark.parametrize(
    "bad", [MERGE.upper(), MERGE[:39], MERGE + "0", "../" + MERGE[3:], "٠" * 40]
)
def test_non_hex_sha_is_malformed(derive, side, path, bad):
    at = [E2, *GQL_PULL] if side == "graphql" else [E1, *REST_PULL]
    assert _delivery_code(derive, [_tf("set_field", [*at, *path], value=bad)]) == "malformed"


@pytest.mark.parametrize(
    "base,encoded",
    [
        ("a?b", "a%3Fb"),
        ("a#b", "a%23b"),
        ("a%2Fb", "a%252Fb"),
        ("../x", "..%2Fx"),
        ("a b", "a%20b"),
        ("feature/x", "feature%2Fx"),
        ("ветка", "%D0%B2%D0%B5%D1%82%D0%BA%D0%B0"),
    ],
)
def test_base_ref_is_one_encoded_path_segment(derive, base, encoded):
    """Ветка от форджа уходит в E5 одним закодированным сегментом: ни endpoint, ни запрос
    (`?`, `#`) не меняются; ожидаемый путь записан литералом, не библиотекой."""
    expected = f"repos/{OWNER}/{NAME}/compare/{encoded}...{MERGE}"
    d, t = _delivery(
        derive,
        [
            _tf("set_field", [E2, *GQL_PULL, "baseRefName"], value=base),
            _tf("set_field", [E1, *REST_PULL, "base", "ref"], value=base),
            _tf("set_field", [E5, "request", "path"], value=expected),
        ],
    )
    assert d.in_base is True and t.unused() == [] and t.unexpected == []
    assert t.log[E5].path == expected


@pytest.mark.parametrize("base", [".", ".."])
def test_dot_base_ref_is_refused_before_request(derive, base):
    transforms = [
        _tf("set_field", [E2, *GQL_PULL, "baseRefName"], value=base),
        _tf("set_field", [E1, *REST_PULL, "base", "ref"], value=base),
    ]
    t = _replay(derive([SEQ], transforms), slice(E2, E5 + 1))
    with pytest.raises(_Fail) as failed:
        read_delivery(t, OWNER, NAME, 657)
    assert failed.value.code == "malformed" and len(t.log) == E5


# --- E5: in_base ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,in_base", [("identical", True), ("behind", True), ("ahead", False), ("diverged", False)]
)
def test_in_base_from_compare(derive, status, in_base):
    d, t = _delivery(derive, [_tf("set_field", [E5, *REST_PULL, "status"], value=status)])
    assert d.in_base is in_base and t.unused() == []


@pytest.mark.parametrize("status", ["unknown", None, 1])
def test_compare_unknown_status_malformed(derive, status):
    code = _delivery_code(derive, [_tf("set_field", [E5, *REST_PULL, "status"], value=status)])
    assert code == "malformed"


def test_compare_path_is_base_then_merge(exchanges):
    path = exchanges(SEQ)[E5]["request"]["path"]
    assert path == f"repos/{OWNER}/{NAME}/compare/master...{MERGE}"


# --- E6: пути ---------------------------------------------------------------------------


def test_read_paths_live_positive_twin(exchanges, key, key_of):
    ex = exchanges(SEQ)[E6]
    t = replay_transport({"exchanges": [ex]})
    assert read_paths(t, OWNER, NAME, MERGE, HEAD, [P]) == (PathBlob(P, BLOB, BLOB),)
    assert t.unused() == [] and [key(r) for r in t.log] == [key_of(ex)]


def test_three_path_live_negative_absent_at_head(exchanges):
    """Живой `neg_e6`: Blob, `null` (в мерже и на голове), `Tree` — первый отказ —
    отсутствие на голове второго пути."""
    t = replay_transport({"exchanges": exchanges("neg_e6_blobs_present_absent_tree")})
    with pytest.raises(_Fail) as failed:
        read_paths(t, OWNER, NAME, MERGE, HEAD, ["CLAUDE.md", "no/such/path.md", "docs"])
    assert failed.value.code == "path_absent_at_head"
    assert failed.value.detail == "no/such/path.md"


def test_blobs_at_merge_and_head_read_independently(derive):
    other = "b1" * 20
    blobs, _ = _paths(derive, [_tf("set_field", [E6, *BLOBS, "m0", "oid"], value=other)])
    assert blobs == (PathBlob(P, other, BLOB),)


def test_path_deleted_at_merge_is_none_in_read_paths(derive):
    blobs, _ = _paths(derive, [_tf("set_field", [E6, *BLOBS, "m0"], value=None)])
    assert blobs == (PathBlob(P, None, BLOB),)


def test_path_absent_at_head_in_read_paths(derive):
    code = _paths_code(derive, [_tf("set_field", [E6, *BLOBS, "h0"], value=None)])
    assert code == "path_absent_at_head"


@pytest.mark.parametrize("alias", ["m0", "h0"])
def test_tree_is_not_a_blob(derive, alias):
    tree = {"__typename": "Tree"}
    assert _paths_code(derive, [_tf("set_field", [E6, *BLOBS, alias], value=tree)]) == "not_a_blob"


@pytest.mark.parametrize("alias", ["mc", "hc"])
def test_commit_object_missing(derive, alias):
    code = _paths_code(derive, [_tf("set_field", [E6, *BLOBS, alias], value=None)])
    assert code == "object_missing"


@pytest.mark.parametrize("alias", ["mc", "m0", "h0"])
def test_missing_alias_is_malformed_not_absence(derive, alias):
    """Отсутствие пути устанавливает только `null` успешного полного ответа: пропавший
    ключ алиаса — дефект формы."""
    code = _paths_code(derive, [_tf("drop_field", [E6, *BLOBS, alias])])
    assert code == "malformed"


def test_blob_without_oid_is_malformed(derive):
    code = _paths_code(derive, [_tf("set_field", [E6, *BLOBS, "h0"], value={"__typename": "Blob"})])
    assert code == "malformed"


def test_path_with_colon_goes_as_variable(derive):
    """Путь `a: b.md`: выражение уходит переменной `m0 = "<MERGE>:a: b.md"`, текст запроса
    пути не содержит и совпадает с каноническим `q_blobs(1)`."""
    path = "a: b.md"
    blobs, t = _paths(
        derive,
        [
            _tf("set_field", [E6, "request", "variables", "m0"], value=f"{MERGE}:{path}"),
            _tf("set_field", [E6, "request", "variables", "h0"], value=f"{HEAD}:{path}"),
        ],
        paths=[path],
    )
    assert blobs == (PathBlob(path, BLOB, BLOB),)
    (sent,) = t.log
    assert sent.variables is not None and sent.variables["m0"] == f"{MERGE}:{path}"
    assert sent.query == q_blobs(1) and path not in sent.query
    assert t.unused() == [] and t.unexpected == []


@pytest.mark.parametrize("which", ["merge", "head"])
def test_read_paths_refuses_non_hex_sha_before_request(which):
    t = replay_transport({"exchanges": []})
    merge, head = (MERGE[:39], HEAD) if which == "merge" else (MERGE, "HEAD")
    with pytest.raises(_Fail) as failed:
        read_paths(t, OWNER, NAME, merge, head, [P])
    assert failed.value.code == "malformed" and t.log == []


def test_iterator_paths_are_materialized_once(exchanges):
    """Итератор путей не превращается в `paths=()` (§13): он читается один раз."""
    t = replay_transport({"exchanges": [exchanges(SEQ)[E6]]})
    paths: Any = iter([P])
    assert read_paths(t, OWNER, NAME, MERGE, HEAD, paths) == (PathBlob(P, BLOB, BLOB),)


def test_empty_paths_read_nothing():
    t = replay_transport({"exchanges": []})
    assert read_paths(t, OWNER, NAME, MERGE, HEAD, []) == () and t.log == []


@pytest.mark.parametrize("bad", ["", "/abs", "../x", "a/../b", "a\x00b", ".."])
def test_bad_path_raises_in_read_paths(bad):
    t = replay_transport({"exchanges": []})
    with pytest.raises(ValueError):
        read_paths(t, OWNER, NAME, MERGE, HEAD, [P, bad])
    assert t.log == []


def test_str_instead_of_paths_raises():
    with pytest.raises(ValueError):
        read_paths(replay_transport({"exchanges": []}), OWNER, NAME, MERGE, HEAD, P)
