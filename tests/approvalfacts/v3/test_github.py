"""Чтение GitHub `approval-facts/v3` по строкам G0, R0, P1 таблицы §3.8 спеки.

Положительные случаи — неизменённые живые снимки. Отрицательные — производные от них
именованными преобразованиями (`_with_response`, `_paged`, `_at_path`, …), рукописных тел
нет. Хелперы снимков — фикстуры `conftest.py`.
"""

import ast
import copy
from collections.abc import Callable
from typing import Any

import pytest

from steward.approvalfacts.v3._github import (
    MAX_PAGES,
    _api_path,
    _Fail,
    graphql,
    graphql_pages,
    parse_link,
    require_sha,
    rest_array_pages,
    rest_object,
    rest_path,
)
from steward.approvalfacts.v3.transport import ReplayTransport, replay_transport

REVIEWS = ("repository", "pullRequest", "reviews")
COMMIT_PULLS = ("repository", "object", "associatedPullRequests")
API = "https://api.github.com/"

Record = dict[str, Any]


def _replay(*exs: Record) -> ReplayTransport:
    return replay_transport({"exchanges": list(exs)})


def _with_response(ex: Record, **response: Any) -> Record:
    """Производный обмен: живой запрос, в ответе заменены поля `response`."""
    out = copy.deepcopy(ex)
    out["response"].update(response)
    return out


def _with_path(ex: Record, path: str) -> Record:
    """Производный обмен: живой ответ на запрос по другому пути."""
    out = copy.deepcopy(ex)
    out["request"]["path"] = path
    return out


def _at_path(ex: Record, path: tuple[str, ...], value: Any = None, *, drop: bool = False) -> Record:
    """Производный обмен: в `data` по пути `path` значение заменено (или ключ стёрт)."""
    out = copy.deepcopy(ex)
    node = out["response"]["body"]["data"]
    for step in path[:-1]:
        node = node[step]
    if drop:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return out


def _paged(ex: Record, connection: tuple[str, ...], after: str | None, has_next: bool, cursor: Any):
    """Производная страница: переменная `after` и `pageInfo` соединения заменены."""
    out = copy.deepcopy(ex)
    out["request"]["variables"]["after"] = after
    node = out["response"]["body"]["data"]
    for step in connection:
        node = node[step]
    node["pageInfo"].update(hasNextPage=has_next, endCursor=cursor)
    return out


def _fail(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> _Fail:
    with pytest.raises(_Fail) as exc:
        fn(*args, **kwargs)
    return exc.value


@pytest.fixture
def call_graphql(request_from) -> Callable[[ReplayTransport, Record], dict]:
    """`graphql()` с op, текстом и переменными записанного обмена."""

    def call(t: ReplayTransport, ex: Record) -> dict:
        req = request_from(ex)
        assert req.op is not None and req.query is not None and req.variables is not None
        return graphql(t, req.op, req.query, dict(req.variables))

    return call


@pytest.fixture
def pages(request_from) -> Callable[..., list[dict]]:
    """`graphql_pages()` по записанному обмену; переменная `after` — у библиотеки."""

    def call(t: ReplayTransport, ex: Record, connection=REVIEWS, **kw: Any) -> list[dict]:
        req = request_from(ex)
        assert req.op is not None and req.query is not None and req.variables is not None
        variables = {k: v for k, v in req.variables.items() if k != "after"}
        return graphql_pages(t, req.op, req.query, variables, connection=connection, **kw)

    return call


@pytest.fixture
def reviews_ex(exchanges) -> Record:
    return exchanges("pr657_act_sequence")[4]  # StewardV3Reviews, одна страница


@pytest.fixture
def e7_ex(live) -> Record:
    return live("e7_commit_pulls_graphql_merge")


# --- G0 ----------------------------------------------------------------------------------


def test_g0_positive_twin_returns_data_object(exchanges, call_graphql):
    ex = exchanges("pr657_act_sequence")[0]
    data = call_graphql(_replay(ex), ex)
    assert data == ex["response"]["body"]["data"]
    assert data["repository"]["pullRequest"]["number"] == 657


def test_g0_graphql_errors_with_200_and_partial_data(live, call_graphql):
    ex = live("neg_e2_delivery_absent_pr")
    assert ex["response"]["status"] == 200
    assert ex["response"]["body"]["data"]["repository"] == {"pullRequest": None}
    got = _fail(call_graphql, _replay(ex), ex)
    assert got.code == "graphql_errors" and "999999" in got.detail


def test_g0_graphql_errors_win_over_non_2xx(live, call_graphql):
    ex = _with_response(live("neg_e2_delivery_absent_pr"), status=502)
    assert _fail(call_graphql, _replay(ex), ex).code == "graphql_errors"


def test_g0_non_2xx_without_errors_is_http(exchanges, call_graphql):
    ex = exchanges("pr657_act_sequence")[0]
    body = {k: v for k, v in ex["response"]["body"].items() if k != "errors"}
    derived = _with_response(ex, status=502, body=body)
    got = _fail(call_graphql, _replay(derived), derived)
    assert got.code == "http" and got.detail == "502 graphql"


@pytest.mark.parametrize(
    "body", [None, [], "text", {"errors": []}, {"data": None}, {"data": []}, {"data": "x"}]
)
def test_g0_envelope_or_data_not_object_is_malformed(exchanges, call_graphql, body):
    ex = _with_response(exchanges("pr657_act_sequence")[0], body=body)
    assert _fail(call_graphql, _replay(ex), ex).code == "malformed"


@pytest.mark.parametrize("errors", [{}, "x", 0, None, {"message": "x"}])
def test_g0_errors_not_a_list_is_malformed(exchanges, call_graphql, errors):
    ex = exchanges("pr657_act_sequence")[0]
    derived = _with_response(ex, body={**ex["response"]["body"], "errors": errors})
    assert _fail(call_graphql, _replay(derived), derived).code == "malformed"


def test_g0_empty_errors_list_is_no_errors(exchanges, call_graphql):
    ex = exchanges("pr657_act_sequence")[0]
    derived = _with_response(ex, body={**ex["response"]["body"], "errors": []})
    assert call_graphql(_replay(derived), derived) == ex["response"]["body"]["data"]


def test_transport_failure_is_transport(exchanges, call_graphql):
    ex = exchanges("pr657_act_sequence")[0]
    got = _fail(call_graphql, _replay(), ex)  # запрос не записан → status None
    assert got.code == "transport" and "unexpected" in got.detail


def test_fail_converts_to_unavailable():
    assert _Fail("http", "422 x").unavailable().reason == "http: 422 x"
    assert _Fail("transport").unavailable().reason == "transport"
    with pytest.raises(ValueError):
        _Fail("no_such_code", "x")


# --- R0 и формы строк endpoint ---------------------------------------------------------


def test_r0_non_2xx_is_http(live):
    ex = live("h5_tree_absent_422")  # живой снимок: несуществующее дерево → 422
    got = _fail(rest_object, _replay(ex), ex["request"]["path"])
    assert got.code == "http" and got.detail == f"422 {ex['request']['path']}"


@pytest.mark.parametrize("status", [401, 403, 404, 500, 502])
def test_r0_any_non_2xx_is_http_on_both_shapes(live, status):
    # 404 — никогда не «установленное отсутствие» (Global Constraints): только `http`.
    for name, read in (
        ("h1_repo_policy", rest_object),
        ("h4_commit_pulls_squash", rest_array_pages),
    ):
        ex = _with_response(live(name), status=status)
        got = _fail(read, _replay(ex), ex["request"]["path"])
        assert (got.code, got.detail) == ("http", f"{status} {ex['request']['path']}")


def test_r0_transport_failure_is_transport():
    assert _fail(rest_object, _replay(), "repos/o/r").code == "transport"


@pytest.mark.parametrize("name", ["h1_repo_policy", "h2_branch_policy", "h6_blob_policy_approvers"])
def test_object_endpoint_positive_twin(live, name):
    ex = live(name)
    assert rest_object(_replay(ex), ex["request"]["path"]) == ex["response"]["body"]


def test_e1_object_from_act_sequence(exchanges):
    ex = exchanges("pr657_act_sequence")[1]  # GET pulls/657
    assert rest_object(_replay(ex), ex["request"]["path"])["number"] == 657


def test_h4_array_body_is_accepted_positive_twin(live):
    ex = live("h4_commit_pulls_merge_commit")
    got = rest_array_pages(_replay(ex), ex["request"]["path"])
    assert got[0]["number"] == 657


def test_h4_empty_array_is_empty_list(live):
    ex = live("h4_commit_pulls_direct_push")
    assert ex["response"]["body"] == []
    assert rest_array_pages(_replay(ex), ex["request"]["path"]) == []


def test_object_endpoint_rejects_array_and_vice_versa(live):
    # Форма — по строке §3.8: массив на месте объекта (H4 → rest_object) и наоборот.
    arr = live("h4_commit_pulls_merge_commit")
    assert _fail(rest_object, _replay(arr), arr["request"]["path"]).code == "malformed"
    obj = live("h1_repo_policy")
    assert _fail(rest_array_pages, _replay(obj), obj["request"]["path"]).code == "malformed"


def test_array_endpoint_rejects_non_object_element(live):
    ex = live("h4_commit_pulls_merge_commit")
    derived = _with_response(ex, body=[*ex["response"]["body"], 657])
    assert _fail(rest_array_pages, _replay(derived), ex["request"]["path"]).code == "malformed"


def test_rest_body_not_json_is_malformed(live):
    ex = _with_response(live("h1_repo_policy"), body=None, error="body is not JSON")
    got = _fail(rest_object, _replay(ex), ex["request"]["path"])
    assert got.code == "malformed" and "not JSON" in got.detail


# --- P1 ----------------------------------------------------------------------------------


def test_p1_link_from_live_capture_parses(live):
    rels = parse_link(live("p1_rest_list_with_link_next")["response"]["link"])
    assert set(rels) == {"next", "last"}
    assert rels["next"].startswith(API) and rels["next"].endswith("&page=2")


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


def test_p1_no_next_is_end_without_extra_request(live):
    ex = live("h4_commit_pulls_side_commit")
    assert ex["response"]["link"] is None
    t = _replay(ex)
    rest_array_pages(t, ex["request"]["path"])
    assert t.unused() == [] and t.unexpected == [] and len(t.log) == 1


def test_p1_link_without_next_is_end(live):
    last_only = live("p1_rest_list_with_link_next")["response"]["link"].split(", ")[1]
    ex = _with_response(live("h4_commit_pulls_squash"), link=last_only)
    t = _replay(ex)
    assert len(rest_array_pages(t, ex["request"]["path"])) == len(ex["response"]["body"])
    assert len(t.log) == 1 and t.unexpected == []


def test_p1_unparseable_link_on_page_is_malformed(live):
    ex = _with_response(live("h4_commit_pulls_merge_commit"), link="garbage")
    assert _fail(rest_array_pages, _replay(ex), ex["request"]["path"]).code == "malformed"


def _p1_and_next_path(live) -> tuple[Record, str]:
    p1 = live("p1_rest_list_with_link_next")
    return p1, parse_link(p1["response"]["link"])["next"].removeprefix(API)


def test_p1_follows_next_to_the_end(live, key, key_of):
    p1, next_path = _p1_and_next_path(live)
    assert next_path.startswith("repositories/")
    # Вторая страница — производная: живое тело H4 по пути из `rel="next"`, без Link.
    page2 = _with_path(live("h4_commit_pulls_merge_commit"), next_path)
    t = _replay(p1, page2)
    got = rest_array_pages(t, p1["request"]["path"])
    assert got == p1["response"]["body"] + page2["response"]["body"]
    assert [key(r) for r in t.log] == [key_of(p1), key_of(page2)] and t.unused() == []


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [(None, None, "transport"), (502, None, "http"), (404, None, "http"), (200, {}, "malformed")],
)
def test_p1_failure_on_later_page_is_refusal_not_partial(live, status, body, code):
    p1, next_path = _p1_and_next_path(live)
    exs = [p1]
    if status is not None:  # None — страница 2 не записана: сбой транспорта
        page2 = _with_path(live("h4_commit_pulls_merge_commit"), next_path)
        changes = {"status": status} if body is None else {"status": status, "body": body}
        exs.append(_with_response(page2, **changes))
    assert _fail(rest_array_pages, _replay(*exs), p1["request"]["path"]).code == code


#: `rel="next"` вне `https://api.github.com/<относительный путь>`: каждый — `malformed`.
NEXT_OUTSIDE_API = (
    "https://evil.example/repos/o/r/pulls?page=2",
    "http://api.github.com/x",
    API,
    f"{API}-X",
    "https://api.github.com",
    "https://api.github.com.evil/repos/o/r/pulls?page=2",
    "https://api.github.com.evil.example/repos/o/r/pulls?page=2",
    "https://api.github.com/https://evil.example/x",  # вложенный URL
    "https://api.github.com/repos/o/r/pulls?next=https://evil.example/x",
    "https://x@api.github.com/repos/o/r/pulls?page=2",  # userinfo
    "https://api.github.com:443/repos/o/r/pulls?page=2",  # порт
    "https://api.github.com//evil.example/x",  # `//` после префикса
    "https://api.github.com/repos\\o/r/pulls?page=2",  # обратный слэш
    "https://api.github.com/repos/o/r/pulls?page=%202 x",
    "HTTPS://api.github.com/repos/o/r/pulls?page=2",
    "https://[api.github.com/repos/o/r/pulls?page=2",  # urlsplit: ValueError
)


@pytest.mark.parametrize("next_url", NEXT_OUTSIDE_API)
def test_p1_next_outside_api_host_is_malformed(live, next_url):
    ex = _with_response(live("h4_commit_pulls_squash"), link=f'<{next_url}>; rel="next"')
    t = _replay(ex)
    assert _fail(rest_array_pages, t, ex["request"]["path"]).code == "malformed"
    assert len(t.log) == 1  # чужой URL не запрашивается


@pytest.mark.parametrize("next_url", NEXT_OUTSIDE_API)
def test_api_path_refuses_url_outside_api(next_url):
    # Прямо, минуя грамматику P1 (пробел в URL она отвергла бы раньше).
    assert _fail(_api_path, next_url).code == "malformed"


def test_api_path_positive_twin(live):
    url = parse_link(live("p1_rest_list_with_link_next")["response"]["link"])["next"]
    assert _api_path(url) == url.removeprefix(API)


def test_rest_page_limit_is_pagination(live):
    p1 = live("p1_rest_list_with_link_next")
    page_paths = [p1["request"]["path"]] + [f"x/p{n}" for n in range(1, MAX_PAGES)]
    exs = [
        _with_response(_with_path(p1, path), link=f'<{API}x/p{n + 1}>; rel="next"')
        for n, path in enumerate(page_paths)
    ]
    t = _replay(*exs)
    assert _fail(rest_array_pages, t, p1["request"]["path"]).code == "pagination"
    assert len(t.log) == MAX_PAGES and t.unexpected == [] and t.unused() == []


# --- GraphQL-пагинация -------------------------------------------------------------------


def test_graphql_single_page_positive_twin(reviews_ex, pages, key, key_of):
    t = _replay(reviews_ex)
    assert pages(t, reviews_ex) == [reviews_ex["response"]["body"]["data"]]
    assert t.unused() == [] and [key(r) for r in t.log] == [key_of(reviews_ex)]


def test_graphql_pages_follow_end_cursor(reviews_ex, pages, key, key_of):
    first = _paged(reviews_ex, REVIEWS, None, True, "c1")
    second = _paged(reviews_ex, REVIEWS, "c1", False, None)
    t = _replay(first, second)
    assert len(pages(t, reviews_ex)) == 2
    assert [key(r) for r in t.log] == [key_of(first), key_of(second)] and t.unused() == []


@pytest.mark.parametrize("cursor", [None, 7, ""])
def test_has_next_without_cursor_is_pagination(reviews_ex, pages, cursor):
    ex = _paged(reviews_ex, REVIEWS, None, True, cursor)
    assert _fail(pages, _replay(ex), ex).code == "pagination"


def test_page_limit_is_pagination(reviews_ex, pages):
    exs = [
        _paged(reviews_ex, REVIEWS, None if n == 0 else f"c{n}", True, f"c{n + 1}")
        for n in range(MAX_PAGES)
    ]
    t = _replay(*exs)
    assert _fail(pages, t, reviews_ex).code == "pagination"
    assert len(t.log) == MAX_PAGES and t.unexpected == []  # 51-й запрос не отправлен


@pytest.mark.parametrize(
    "page_info", [None, {}, {"hasNextPage": "yes", "endCursor": "c"}, {"endCursor": "c"}]
)
def test_connection_without_valid_page_info_is_malformed(reviews_ex, pages, page_info):
    ex = copy.deepcopy(reviews_ex)
    conn = ex["response"]["body"]["data"]["repository"]["pullRequest"]["reviews"]
    if page_info is None:
        del conn["pageInfo"]
    else:
        conn["pageInfo"] = page_info
    assert _fail(pages, _replay(ex), ex).code == "malformed"


def test_connection_not_object_is_malformed(reviews_ex, pages):
    ex = _at_path(reviews_ex, REVIEWS, [])
    assert _fail(pages, _replay(ex), ex).code == "malformed"


@pytest.mark.parametrize("path", [REVIEWS[:1], REVIEWS[:2], REVIEWS])
def test_first_page_null_on_non_e7_path_is_malformed(reviews_ex, pages, path):
    # E3 `pullRequest: null` без `errors` — не пустой список ревью.
    ex = _at_path(reviews_ex, path, None)
    assert _fail(pages, _replay(ex), ex).code == "malformed"


@pytest.mark.parametrize("path", [REVIEWS[:1], REVIEWS[:2], REVIEWS])
def test_first_page_missing_key_on_path_is_malformed(reviews_ex, pages, path):
    ex = _at_path(reviews_ex, path, drop=True)
    assert _fail(pages, _replay(ex), ex).code == "malformed"


def test_missing_key_is_malformed_even_when_null_is_allowed(e7_ex, pages):
    ex = _at_path(e7_ex, COMMIT_PULLS[:2], drop=True)
    got = _fail(pages, _replay(ex), ex, connection=COMMIT_PULLS, null_ok_at="object")
    assert got.code == "malformed"


@pytest.mark.parametrize("drop", [False, True])
@pytest.mark.parametrize("path", [REVIEWS[:2], REVIEWS])
def test_later_page_null_or_missing_on_path_is_pagination(reviews_ex, pages, path, drop):
    first = _paged(reviews_ex, REVIEWS, None, True, "c1")
    second = _at_path(_paged(reviews_ex, REVIEWS, "c1", False, None), path, drop=drop)
    t = _replay(first, second)
    assert _fail(pages, t, reviews_ex).code == "pagination"
    assert t.unused() == []


@pytest.mark.parametrize("drop", [False, True])
def test_e7_later_page_null_is_pagination_even_when_null_allowed(e7_ex, pages, drop):
    first = _paged(e7_ex, COMMIT_PULLS, None, True, "c1")
    second = _at_path(_paged(e7_ex, COMMIT_PULLS, "c1", False, None), COMMIT_PULLS[:2], drop=drop)
    t = _replay(first, second)
    got = _fail(pages, t, e7_ex, connection=COMMIT_PULLS, null_ok_at="object")
    assert got.code == "pagination"


@pytest.mark.parametrize(
    ("response", "code"),
    [({"status": 502}, "http"), (None, "transport"), ("errors", "graphql_errors")],
)
def test_graphql_failure_on_later_page_is_refusal_not_partial(
    reviews_ex, pages, live, response, code
):
    first = _paged(reviews_ex, REVIEWS, None, True, "c1")
    second = _paged(reviews_ex, REVIEWS, "c1", False, None)
    if response == "errors":  # `errors` — из живого `neg_e2_delivery_absent_pr`
        errors = live("neg_e2_delivery_absent_pr")["response"]["body"]["errors"]
        second = _with_response(second, body={**second["response"]["body"], "errors": errors})
    elif response is not None:
        second = _with_response(second, **response)
    exs = [first] if response is None else [first, second]
    assert _fail(pages, _replay(*exs), reviews_ex).code == code


def test_e7_first_page_null_is_returned_when_allowed(e7_ex, pages):
    # E7: `object: null` — допустимая форма строки; `null` виден вызывающему (Task 10).
    ex = _at_path(e7_ex, COMMIT_PULLS[:2], None)
    got = pages(_replay(ex), ex, connection=COMMIT_PULLS, null_ok_at="object")
    assert got == [ex["response"]["body"]["data"]]
    assert got[0]["repository"]["object"] is None


def test_e7_first_page_null_without_permission_is_malformed(e7_ex, pages):
    ex = _at_path(e7_ex, COMMIT_PULLS[:2], None)
    assert _fail(pages, _replay(ex), ex, connection=COMMIT_PULLS).code == "malformed"


@pytest.mark.parametrize("path", [COMMIT_PULLS[:1], COMMIT_PULLS])
def test_e7_null_on_other_step_is_malformed_when_object_allowed(e7_ex, pages, path):
    # §3.8 E7 допускает только `object: null`; `associatedPullRequests: null` — не «PR нет».
    ex = _at_path(e7_ex, path, None)
    got = _fail(pages, _replay(ex), ex, connection=COMMIT_PULLS, null_ok_at="object")
    assert got.code == "malformed"


def test_null_ok_at_must_be_a_step_of_the_connection(e7_ex, pages):
    with pytest.raises(ValueError):
        pages(_replay(e7_ex), e7_ex, connection=COMMIT_PULLS, null_ok_at="pullRequest")


def test_e7_positive_twin_single_page(e7_ex, pages):
    t = _replay(e7_ex)
    got = pages(t, e7_ex, connection=COMMIT_PULLS, null_ok_at="object")
    nodes = got[0]["repository"]["object"]["associatedPullRequests"]["nodes"]
    assert nodes[0]["number"] == 657 and t.unused() == []


# --- пути REST и SHA (спека §3.2 «Пути REST») ------------------------------------------


@pytest.mark.parametrize(
    "seg,encoded",
    [
        ("a?b", "a%3Fb"),
        ("a#b", "a%23b"),
        ("a%2Fb", "a%252Fb"),
        ("../x", "..%2Fx"),
        ("a b", "a%20b"),
        ("ветка", "%D0%B2%D0%B5%D1%82%D0%BA%D0%B0"),
        ("-x", "-x"),
    ],
)
def test_rest_path_encodes_every_segment(seg, encoded):
    got = rest_path("repos/{owner}/{name}/compare/{base}...{merge}", owner="o", name="r",
                    base=seg, merge="m")  # fmt: skip
    assert got == f"repos/o/r/compare/{encoded}...m"


def test_rest_path_keeps_template_literal_and_encodes_ints():
    assert rest_path("repos/{o}/{r}/pulls/{n}?per_page=100", o="a b", r="x", n=7) == (
        "repos/a%20b/x/pulls/7?per_page=100"
    )


@pytest.mark.parametrize("seg", ["", ".", ".."])
def test_rest_path_refuses_dot_segments(seg):
    with pytest.raises(_Fail) as failed:
        rest_path("repos/{owner}/x", owner=seg)
    assert failed.value.code == "malformed"


@pytest.mark.parametrize("segments", [{}, {"owner": "o", "extra": "e"}])
def test_rest_path_segment_names_must_match_template(segments):
    with pytest.raises(ValueError):
        rest_path("repos/{owner}/x", **segments)


@pytest.mark.parametrize(
    "value", ["A" * 40, "a" * 39, "a" * 41, "g" * 40, "٠" * 40, "a" * 39 + "\n", ""]
)
def test_require_sha_refuses_non_hex40(value):
    with pytest.raises(_Fail) as failed:
        require_sha(value, "where")
    assert failed.value.code == "malformed"


def test_require_sha_accepts_hex40():
    assert (
        require_sha("0123456789abcdef" * 2 + "01234567", "where")
        == "0123456789abcdef" * 2 + "01234567"
    )


#: Вызовы REST-слоя: имя → индекс позиционного аргумента пути.
_REST_SINKS = {"rest_object": 1, "rest_array_pages": 1, "_rest": 1, "rest": 0, "_gh": 1}
#: Функции, которым разрешено передавать путь дальше как есть: внутренности REST-слоя
#: `_github.py` (путь уже собран вызывающим или пришёл через `_api_path`) и обёртки скрипта
#: захвата; `prove_public` — константы allowlist под `_check_repo_path` (принято ревью).
_PASS_THROUGH = {
    ("_github.py", "rest_object"),
    ("_github.py", "rest_array_pages"),
    ("capture_approval_facts_v3_live.py", "rest"),
    ("capture_approval_facts_v3_live.py", "graphql"),
    ("capture_approval_facts_v3_live.py", "prove_public"),
}
#: Сборщики пути: `rest_path` (шаблон — строковая константа) и `_api` скрипта, который
#: сам обязан вернуть `rest_path(...)` (`test_capture_api_builds_with_rest_path`).
_BUILDERS = {"rest_path": 0, "_api": 1}


def _const_str(node: ast.AST, assigns: dict[str, list[ast.AST]]) -> bool:
    """Строковая константа, конкатенация констант или имя, связанное только с ними."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _const_str(node.left, assigns) and _const_str(node.right, assigns)
    if isinstance(node, ast.Name) and node.id in assigns:
        return all(_const_str(v, assigns) for v in assigns[node.id])
    return False


def _built_path(node: ast.AST, assigns: dict[str, list[ast.AST]]) -> bool:
    """Путь — вызов сборщика с константным шаблоном или имя, связанное только с ними."""
    if isinstance(node, ast.Name) and node.id in assigns:
        return all(_built_path(v, assigns) for v in assigns[node.id])
    if not isinstance(node, ast.Call):
        return False
    index = _BUILDERS.get(_call_name(node) or "")
    if index is None:
        return False
    if len(node.args) <= index:
        return index == 1  # `_api(repo)` — шаблон по умолчанию
    return _const_str(node.args[index], assigns)


def _assignments(func: ast.AST) -> dict[str, list[ast.AST]]:
    """Имя → все присвоенные ему в функции значения (с распаковкой кортежей)."""
    out: dict[str, list[ast.AST]] = {}
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            pairs = [(target, node.value)]
            if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                pairs = list(zip(target.elts, node.value.elts, strict=False))
            for name, value in pairs:
                if isinstance(name, ast.Name):
                    out.setdefault(name.id, []).append(value)
                elif isinstance(name, ast.Tuple):  # распаковка не из кортежа литералов
                    for elt in name.elts:
                        if isinstance(elt, ast.Name):
                            out.setdefault(elt.id, []).append(node.value)
    return out


_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _call_name(call: ast.Call) -> str | None:
    """Конечное имя вызываемого: `f(...)` → `f`, `mod.f(...)`/`self.f(...)` → `f`."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _scopes(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    """Узел → ближайшая объемлющая функция (или модуль) — контекст присваиваний."""
    owner: dict[ast.AST, ast.AST] = {}

    def visit(node: ast.AST, scope: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            owner[child] = scope
            visit(child, child if isinstance(child, _FUNCS) else scope)

    visit(tree, tree)
    return owner


def _scope_assignments(scope: ast.AST) -> dict[str, list[ast.AST]]:
    """Присваивания своей области: у модуля — без тел функций и классов."""
    if isinstance(scope, ast.Module):
        top = [st for st in scope.body if not isinstance(st, (*_FUNCS, ast.ClassDef))]
        return _assignments(ast.Module(body=top, type_ignores=[]))
    return _assignments(scope)


def _raw_rest_paths(source: str, filename: str) -> list[str]:
    """Вызовы REST-слоя, чей путь собран не сборщиком (f-строка, `+`, `%`, `.format`, …).

    Осматривается **каждый** вызов модуля — на уровне модуля, в функции, в методе — по
    конечному имени (`rest_object`, `_github.rest_object`, `self.rest`); исключены только
    вызовы, чья ближайшая объемлющая функция — в `_PASS_THROUGH`.
    """
    tree = ast.parse(source)
    scopes = _scopes(tree)
    offenders = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        index = _REST_SINKS.get(_call_name(call) or "")
        if index is None:
            continue
        scope = scopes[call]
        name = scope.name if isinstance(scope, _FUNCS) else "<module>"
        if (filename, name) in _PASS_THROUGH:
            continue
        path = call.args[index] if len(call.args) > index else None
        path = path or next((k.value for k in call.keywords if k.arg == "path"), None)
        if path is None or not _built_path(path, _scope_assignments(scope)):
            offenders.append(f"{filename}:{call.lineno} {name}")
    return offenders


def _scanned(repo_root) -> list:
    v3 = sorted((repo_root / "src/steward/approvalfacts/v3").glob("*.py"))
    return [*v3, repo_root / "scripts/capture_approval_facts_v3_live.py"]


def test_rest_paths_go_through_rest_path(repo_root):
    """Ни модуль v3, ни скрипт захвата не передают REST-слою путь в обход `rest_path`."""
    offenders = [
        o for f in _scanned(repo_root) for o in _raw_rest_paths(f.read_text("utf-8"), f.name)
    ]
    assert offenders == []


def test_rest_sinks_are_present_in_scanned_files(repo_root):
    """Детектор не вакуумен: в сканируемых файлах есть вызовы REST-слоя."""
    calls = [
        node.func.id
        for f in _scanned(repo_root)
        for node in ast.walk(ast.parse(f.read_text("utf-8")))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert {"rest_object", "rest"} <= set(calls)


def test_capture_api_builds_with_rest_path(repo_root):
    """`_api` скрипта — сборщик, только если сам возвращает `rest_path(...)`."""
    tree = ast.parse((repo_root / "scripts/capture_approval_facts_v3_live.py").read_text("utf-8"))
    (api,) = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_api"]
    (ret,) = [n for n in ast.walk(api) if isinstance(n, ast.Return)]
    assert isinstance(ret.value, ast.Call) and isinstance(ret.value.func, ast.Name)
    assert ret.value.func.id == "rest_path"


_SNIPPETS_RAW = [
    'def f(t, o):\n    rest_object(t, f"repos/{o}/x")',
    'def f(t, o):\n    rest_object(t, "repos/" + o)',
    'def f(t, b):\n    rest_object(t, rest_path("repos/{o}", o="x") + "/compare/" + b)',
    'def f(t, b):\n    rest_object(t, "repos/o/r/compare/%s" % b)',
    'def f(t, b):\n    rest_object(t, "repos/o/r/compare/{}".format(b))',
    'def f(t, b):\n    p = f"x/{b}"\n    rest_object(t, p)',
    'def f(t, b):\n    rest_object(t, rest_path(b, o="x"))',
    'def f(t, b):\n    rest(_api("o/r", "/compare/" + b))',
    "def f(t, b):\n    rest_array_pages(t, path=b)",
    # уровень модуля, атрибут, метод, вложенная функция внутри разрешённой обёртки
    'rest_object(t, f"repos/{o}/x")',
    'P = f"repos/{o}/x"\nrest_object(t, P)',
    'def f(t, o):\n    _github.rest_object(t, f"repos/{o}/x")',
    'class C:\n    def m(self, o):\n        self.rest(f"repos/{o}/x")',
    'def f(t, o):\n    _github.rest_object(t, _github.rest_path(f"repos/{o}"))',
]
_SNIPPETS_BUILT = [
    'def f(t, o):\n    rest_object(t, rest_path("repos/{o}/x", o=o))',
    'def f(t, o):\n    p = rest_path("repos/{o}/x", o=o)\n    rest_object(t, p)',
    'def f(t):\n    a, b = "/x/{s}", "/y"\n    rest(_api("o/r", a, s="1"))',
    'def f(t):\n    rest(_api("o/r"))',
    'rest_object(t, rest_path("repos/{o}/x", o=o))',
    'P = "repos/{o}/x"\nrest_object(t, rest_path(P, o=o))',
    'def f(t, o):\n    _github.rest_object(t, _github.rest_path("repos/{o}/x", o=o))',
    'class C:\n    def m(self, o):\n        self.rest(_api("o/r", "/x"))',
]


@pytest.mark.parametrize("snippet", _SNIPPETS_RAW)
def test_raw_rest_path_detector_flags(snippet):
    assert _raw_rest_paths(snippet, "x.py") != []


@pytest.mark.parametrize("snippet", _SNIPPETS_BUILT)
def test_raw_rest_path_detector_accepts_builders(snippet):
    assert _raw_rest_paths(snippet, "x.py") == []


def test_nested_function_in_pass_through_is_not_exempt():
    """Исключение `_PASS_THROUGH` — по ближайшей функции: вложенная функция в обёртке
    `rest` скрипта не наследует его, а сама обёртка остаётся исключённой."""
    snippet = 'def rest(p):\n    def g(o):\n        _gh([], f"repos/{o}")\n    _gh([p], p)'
    name = "capture_approval_facts_v3_live.py"
    assert _raw_rest_paths(snippet, name) == [f"{name}:3 g"]
