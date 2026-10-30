"""Чтение GitHub `approval-facts/v3` по строкам G0, R0, P1 таблицы §3.8 спеки.

Положительные случаи — неизменённые живые снимки. Отрицательные — производные от них
именованными преобразованиями (`_with_response`, `_paged`, …), рукописных тел нет.
"""

import copy
from typing import Any

import pytest

from steward.approvalfacts.v3._github import (
    MAX_PAGES,
    _Fail,
    graphql,
    graphql_pages,
    parse_link,
    rest_array_pages,
    rest_object,
)
from steward.approvalfacts.v3.transport import replay_transport
from ._live import exchanges, key, key_of, live, request_from  # pyrefly: ignore[missing-import]

REVIEWS = ("repository", "pullRequest", "reviews")


def _replay(*exs: dict[str, Any]):
    return replay_transport({"exchanges": list(exs)})


def _with_response(ex: dict[str, Any], **response: Any) -> dict[str, Any]:
    """Производный обмен: живой запрос, в ответе заменены поля `response`."""
    out = copy.deepcopy(ex)
    out["response"].update(response)
    return out


def _reviews_ex() -> dict[str, Any]:
    return exchanges("pr657_act_sequence")[4]  # StewardV3Reviews, одна страница


def _paged(ex: dict[str, Any], after: str | None, has_next: bool, cursor: Any) -> dict[str, Any]:
    """Производная страница E3: переменная `after` и `pageInfo` заменены."""
    out = copy.deepcopy(ex)
    out["request"]["variables"]["after"] = after
    page = out["response"]["body"]["data"]["repository"]["pullRequest"]["reviews"]["pageInfo"]
    page.update(hasNextPage=has_next, endCursor=cursor)
    return out


def _call_graphql(t, ex):
    req = request_from(ex)
    assert req.op is not None and req.query is not None and req.variables is not None
    return graphql(t, req.op, req.query, dict(req.variables))


def _fail(fn, *args, **kwargs) -> _Fail:
    with pytest.raises(_Fail) as exc:
        fn(*args, **kwargs)
    return exc.value


# --- G0 ----------------------------------------------------------------------------------


def test_g0_positive_twin_returns_data_object():
    ex = exchanges("pr657_act_sequence")[0]
    data = _call_graphql(_replay(ex), ex)
    assert data == ex["response"]["body"]["data"]
    assert data["repository"]["pullRequest"]["number"] == 657


def test_g0_graphql_errors_with_200_and_partial_data():
    ex = live("neg_e2_delivery_absent_pr")
    assert ex["response"]["status"] == 200
    assert ex["response"]["body"]["data"]["repository"] == {"pullRequest": None}
    got = _fail(_call_graphql, _replay(ex), ex)
    assert got.code == "graphql_errors" and "999999" in got.detail


def test_g0_graphql_errors_win_over_non_2xx():
    ex = _with_response(live("neg_e2_delivery_absent_pr"), status=502)
    assert _fail(_call_graphql, _replay(ex), ex).code == "graphql_errors"


def test_g0_non_2xx_without_errors_is_http():
    ex = exchanges("pr657_act_sequence")[0]
    body = {k: v for k, v in ex["response"]["body"].items() if k != "errors"}
    derived = _with_response(ex, status=502, body=body)
    got = _fail(_call_graphql, _replay(derived), derived)
    assert got.code == "http" and got.detail == "502 graphql"


@pytest.mark.parametrize(
    "body", [None, [], "text", {"errors": []}, {"data": None}, {"data": []}, {"data": "x"}]
)
def test_g0_envelope_or_data_not_object_is_malformed(body):
    ex = _with_response(exchanges("pr657_act_sequence")[0], body=body)
    assert _fail(_call_graphql, _replay(ex), ex).code == "malformed"


def test_transport_failure_is_transport():
    ex = exchanges("pr657_act_sequence")[0]
    got = _fail(_call_graphql, _replay(), ex)  # запрос не записан → status None
    assert got.code == "transport" and "unexpected" in got.detail


def test_fail_converts_to_unavailable():
    assert _Fail("http", "422 x").unavailable().reason == "http: 422 x"
    assert _Fail("transport").unavailable().reason == "transport"
    with pytest.raises(ValueError):
        _Fail("no_such_code", "x")


# --- R0 и формы строк endpoint ---------------------------------------------------------


def test_r0_non_2xx_is_http():
    ex = live("h5_tree_absent_422")  # живой снимок: несуществующее дерево → 422
    got = _fail(rest_object, _replay(ex), ex["request"]["path"])
    assert got.code == "http" and got.detail == f"422 {ex['request']['path']}"


def test_r0_transport_failure_is_transport():
    assert _fail(rest_object, _replay(), "repos/o/r").code == "transport"


@pytest.mark.parametrize("name", ["h1_repo_policy", "h2_branch_policy", "h6_blob_policy_approvers"])
def test_object_endpoint_positive_twin(name):
    ex = live(name)
    assert rest_object(_replay(ex), ex["request"]["path"]) == ex["response"]["body"]


def test_e1_object_from_act_sequence():
    ex = exchanges("pr657_act_sequence")[1]  # GET pulls/657
    assert rest_object(_replay(ex), ex["request"]["path"])["number"] == 657


def test_h4_array_body_is_accepted_positive_twin():
    ex = live("h4_commit_pulls_merge_commit")
    got = rest_array_pages(_replay(ex), ex["request"]["path"])
    assert got[0]["number"] == 657


def test_h4_empty_array_is_empty_list():
    ex = live("h4_commit_pulls_direct_push")
    assert ex["response"]["body"] == []
    assert rest_array_pages(_replay(ex), ex["request"]["path"]) == []


def test_object_endpoint_rejects_array_and_vice_versa():
    # Форма — по строке §3.8: массив на месте объекта (H4 → rest_object) и наоборот.
    arr = live("h4_commit_pulls_merge_commit")
    assert _fail(rest_object, _replay(arr), arr["request"]["path"]).code == "malformed"
    obj = live("h1_repo_policy")
    assert _fail(rest_array_pages, _replay(obj), obj["request"]["path"]).code == "malformed"


def test_array_endpoint_rejects_non_object_element():
    ex = live("h4_commit_pulls_merge_commit")
    derived = _with_response(ex, body=[*ex["response"]["body"], 657])
    assert _fail(rest_array_pages, _replay(derived), ex["request"]["path"]).code == "malformed"


def test_rest_body_not_json_is_malformed():
    ex = _with_response(live("h1_repo_policy"), body=None, error="body is not JSON")
    got = _fail(rest_object, _replay(ex), ex["request"]["path"])
    assert got.code == "malformed" and "not JSON" in got.detail


# --- P1 ----------------------------------------------------------------------------------


def test_p1_link_from_live_capture_parses():
    header = live("p1_rest_list_with_link_next")["response"]["link"]
    rels = parse_link(header)
    assert set(rels) == {"next", "last"}
    assert rels["next"].endswith("&page=2")


@pytest.mark.parametrize(
    "header",
    [
        "garbage",
        "",
        '<https://x/1>; rel="next",<https://x/2>; rel="last"',
        '<https://x/1>; rel="next", <https://x/2>; rel="next"',
        "<https://x/1>; rel=next",
        '<https://x/1>;rel="next"',
    ],
)
def test_p1_unparseable_link_is_malformed(header):
    assert _fail(parse_link, header).code == "malformed"


def test_p1_no_next_is_end_without_extra_request():
    ex = live("h4_commit_pulls_side_commit")
    assert ex["response"]["link"] is None
    t = _replay(ex)
    rest_array_pages(t, ex["request"]["path"])
    assert t.unused() == [] and t.unexpected == [] and len(t.log) == 1


def test_p1_link_without_next_is_end():
    p1 = live("p1_rest_list_with_link_next")
    last_only = p1["response"]["link"].split(", ")[1]  # только rel="last"
    ex = _with_response(live("h4_commit_pulls_squash"), link=last_only)
    t = _replay(ex)
    assert len(rest_array_pages(t, ex["request"]["path"])) == len(ex["response"]["body"])
    assert len(t.log) == 1 and t.unexpected == []


def test_p1_unparseable_link_on_page_is_malformed():
    ex = _with_response(live("h4_commit_pulls_merge_commit"), link="garbage")
    assert _fail(rest_array_pages, _replay(ex), ex["request"]["path"]).code == "malformed"


def test_p1_follows_next_to_the_end():
    p1 = live("p1_rest_list_with_link_next")
    next_url = parse_link(p1["response"]["link"])["next"]
    # Вторая страница — производная: живое тело H4, запрос по URL из `rel="next"`, без Link.
    page2 = copy.deepcopy(live("h4_commit_pulls_merge_commit"))
    page2["request"]["path"] = next_url
    t = _replay(p1, page2)
    got = rest_array_pages(t, p1["request"]["path"])
    assert got == p1["response"]["body"] + page2["response"]["body"]
    assert [key(r) for r in t.log] == [key_of(p1), key_of(page2)] and t.unused() == []


def test_rest_page_limit_is_pagination():
    p1 = live("p1_rest_list_with_link_next")
    pages = []
    for n in range(MAX_PAGES):
        page = copy.deepcopy(p1)
        page["request"]["path"] = p1["request"]["path"] if n == 0 else f"https://x/p{n}"
        page["response"]["link"] = f'<https://x/p{n + 1}>; rel="next"'
        pages.append(page)
    t = _replay(*pages)
    assert _fail(rest_array_pages, t, p1["request"]["path"]).code == "pagination"
    assert len(t.log) == MAX_PAGES and t.unexpected == [] and t.unused() == []


# --- GraphQL-пагинация -------------------------------------------------------------------


def _pages(t, ex):
    req = request_from(ex)
    assert req.op is not None and req.query is not None and req.variables is not None
    variables = {k: v for k, v in req.variables.items() if k != "after"}
    return graphql_pages(t, req.op, req.query, variables, connection=REVIEWS)


def test_graphql_single_page_positive_twin():
    ex = _reviews_ex()
    t = _replay(ex)
    assert _pages(t, ex) == [ex["response"]["body"]["data"]]
    assert t.unused() == [] and [key(r) for r in t.log] == [key_of(ex)]


def test_graphql_pages_follow_end_cursor():
    ex = _reviews_ex()
    first, second = _paged(ex, None, True, "c1"), _paged(ex, "c1", False, None)
    t = _replay(first, second)
    got = _pages(t, ex)
    assert len(got) == 2
    assert [key(r) for r in t.log] == [key_of(first), key_of(second)] and t.unused() == []


@pytest.mark.parametrize("cursor", [None, 7, ""])
def test_has_next_without_cursor_is_pagination(cursor):
    ex = _paged(_reviews_ex(), None, True, cursor)
    assert _fail(_pages, _replay(ex), ex).code == "pagination"


def test_page_limit_is_pagination():
    ex = _reviews_ex()
    pages = [_paged(ex, None if n == 0 else f"c{n}", True, f"c{n + 1}") for n in range(MAX_PAGES)]
    t = _replay(*pages)
    assert _fail(_pages, t, ex).code == "pagination"
    assert len(t.log) == MAX_PAGES and t.unexpected == []  # 51-й запрос не отправлен


@pytest.mark.parametrize(
    "page_info", [None, {}, {"hasNextPage": "yes", "endCursor": "c"}, {"endCursor": "c"}]
)
def test_connection_without_valid_page_info_is_malformed(page_info):
    ex = copy.deepcopy(_reviews_ex())
    conn = ex["response"]["body"]["data"]["repository"]["pullRequest"]["reviews"]
    if page_info is None:
        del conn["pageInfo"]
    else:
        conn["pageInfo"] = page_info
    assert _fail(_pages, _replay(ex), ex).code == "malformed"


def test_connection_not_object_is_malformed():
    ex = copy.deepcopy(_reviews_ex())
    ex["response"]["body"]["data"]["repository"]["pullRequest"]["reviews"] = []
    assert _fail(_pages, _replay(ex), ex).code == "malformed"


def test_null_on_connection_path_is_left_to_the_row():
    # E7: `object: null` — допустимая форма строки; разбор — у вызывающего (Task 10).
    ex = copy.deepcopy(live("e7_commit_pulls_graphql_merge"))
    ex["response"]["body"]["data"]["repository"]["object"] = None
    req = request_from(ex)
    assert req.op is not None and req.query is not None and req.variables is not None
    variables = {k: v for k, v in req.variables.items() if k != "after"}
    got = graphql_pages(
        _replay(ex),
        req.op,
        req.query,
        variables,
        connection=("repository", "object", "associatedPullRequests"),
    )
    assert got == [ex["response"]["body"]["data"]]


def test_e7_positive_twin_single_page():
    ex = live("e7_commit_pulls_graphql_merge")
    req = request_from(ex)
    assert req.op is not None and req.query is not None and req.variables is not None
    variables = {k: v for k, v in req.variables.items() if k != "after"}
    t = _replay(ex)
    got = graphql_pages(
        t,
        req.op,
        req.query,
        variables,
        connection=("repository", "object", "associatedPullRequests"),
    )
    nodes = got[0]["repository"]["object"]["associatedPullRequests"]["nodes"]
    assert nodes[0]["number"] == 657 and t.unused() == []
