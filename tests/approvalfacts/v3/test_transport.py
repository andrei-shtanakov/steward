"""Транспорт `approval-facts/v3` (спека §3.2): replay по живым снимкам и `GhTransport`.

`GhTransport` проверяется подменой `subprocess.run` — сети в тестах нет. Вывод `gh api
--include` собирается из живого снимка (строка статуса, `Link`, тело), а не пишется руками.
"""

import copy
import dataclasses
import json
import subprocess
from pathlib import Path

import pytest

from steward.approvalfacts.v3 import transport as transport_mod
from steward.approvalfacts.v3.transport import (
    GhTransport,
    ReplayTransport,
    Request,
    Response,
    Transport,
    replay_transport,
)
from ._live import exchanges, key, key_of, live, request_from  # pyrefly: ignore[missing-import]


def test_replay_key_includes_query_text():
    ex = exchanges("pr636_act_sequence")[0]  # первый обмен: StewardV3Delivery
    assert ex["request"]["op"] == "StewardV3Delivery"
    t = replay_transport({"exchanges": [ex]})
    req = request_from(ex)
    assert req.query is not None
    other = dataclasses.replace(req, query=req.query.replace(" baseRefName", ""))
    assert t.send(other).status is None and t.unexpected == [other]
    assert t.send(req).status == 200 and t.unused() == []


def test_replay_key_includes_variables_order_free():
    ex = exchanges("pr636_act_sequence")[0]
    t = replay_transport({"exchanges": [ex]})
    req = request_from(ex)
    assert req.variables is not None
    reordered = dataclasses.replace(req, variables=dict(reversed(list(req.variables.items()))))
    assert reordered == req and hash(reordered) == hash(req)
    other = dataclasses.replace(req, variables={**req.variables, "number": 1})
    assert other != req
    assert t.send(other).status is None
    assert t.send(reordered).status == 200


def test_replay_fifo_per_key():
    # Производный снимок: тот же обмен дважды, второй ответ — со статусом 502.
    ex = exchanges("pr657_act_sequence")[4]  # StewardV3Reviews, проход 1
    second = copy.deepcopy(ex)
    second["response"]["status"] = 502
    t = replay_transport({"exchanges": [ex, second]})
    req = request_from(ex)
    assert t.send(req).status == 200
    assert t.send(req).status == 502
    assert t.send(req).status is None and t.unexpected == [req]


def test_replay_of_live_act_sequence_consumes_it_exactly_in_order():
    seq = exchanges("pr657_act_sequence")
    t = replay_transport({"exchanges": seq})
    got = [t.send(request_from(ex)) for ex in seq]
    assert [r.status for r in got] == [ex["response"]["status"] for ex in seq]
    assert [r.body for r in got] == [ex["response"]["body"] for ex in seq]
    # Два прохода E3/E4 — две разные записи одного ключа, и обе потреблены.
    assert key_of(seq[4]) == key_of(seq[6])
    assert t.unused() == [] and t.unexpected == []
    assert [key(r) for r in t.log] == [key_of(ex) for ex in seq]


def test_replay_unused_lists_unconsumed_requests():
    seq = exchanges("closed606_act_sequence")
    t = replay_transport({"exchanges": seq})
    t.send(request_from(seq[0]))
    assert t.unused() == [request_from(seq[1])]


def test_replay_unexpected_request_is_transport_failure():
    t = replay_transport({"exchanges": []})
    req = Request("GET", "repos/o/r/pulls/1")
    got = t.send(req)
    assert got == Response(None, error="replay: unexpected request")
    assert t.unexpected == [req] and t.log == [req]


def test_replay_response_carries_link_and_error_fields():
    ex = live("p1_rest_list_with_link_next")
    got = replay_transport({"exchanges": [ex]}).send(request_from(ex))
    assert got.link == ex["response"]["link"] and got.error is None


@pytest.mark.parametrize("spec", [{}, {"exchanges": [{"request": {"method": "GET"}}]}])
def test_replay_transport_rejects_malformed_spec(spec):
    with pytest.raises(ValueError):
        replay_transport(spec)


def test_transports_satisfy_protocol():
    assert isinstance(replay_transport({"exchanges": []}), ReplayTransport)
    t: Transport = GhTransport()
    assert isinstance(t, GhTransport)


# --- GhTransport: подмена subprocess.run --------------------------------------------------


class _Run:
    """Запоминает вызовы `subprocess.run` и отдаёт заданный результат или исключение."""

    def __init__(self, stdout: str = "", returncode: int = 0, raises: Exception | None = None):
        self.stdout, self.returncode, self.raises = stdout, returncode, raises
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "gh: stderr")


def _gh_output(response: dict, *, crlf: bool = False) -> str:
    """Вывод `gh api --include`, собранный из живого ответа."""
    head = f"HTTP/2.0 {response['status']} X"
    if response.get("link"):
        head += f"\nLink: {response['link']}"
    out = f"{head}\nContent-Type: application/json\n\n{json.dumps(response['body'])}"
    return out.replace("\n", "\r\n") if crlf else out


def _patch(monkeypatch, run: _Run) -> _Run:
    monkeypatch.setattr(transport_mod.subprocess, "run", run)
    return run


@pytest.mark.parametrize("crlf", [False, True])
def test_gh_transport_parses_include_output(monkeypatch, crlf):
    ex = live("p1_rest_list_with_link_next")
    run = _patch(monkeypatch, _Run(_gh_output(ex["response"], crlf=crlf)))
    got = GhTransport().send(request_from(ex))
    assert got.status == 200
    assert got.link == ex["response"]["link"]
    assert got.body == ex["response"]["body"]
    assert run.calls[0][0] == ["gh", "api", "--include", ex["request"]["path"]]


def test_gh_transport_keeps_body_of_non_2xx_and_graphql_errors(monkeypatch):
    # gh завершается с кодом 1 и на 422, и на GraphQL `errors`: тело всё равно читается.
    for name in ("h5_tree_absent_422", "neg_e2_delivery_absent_pr"):
        ex = live(name)
        _patch(monkeypatch, _Run(_gh_output(ex["response"]), returncode=1))
        got = GhTransport().send(request_from(ex))
        assert (got.status, got.body) == (ex["response"]["status"], ex["response"]["body"])


def test_gh_response_without_status_line_is_transport(monkeypatch):
    _patch(monkeypatch, _Run("not a status line\n\n{}", returncode=1))
    got = GhTransport().send(Request("GET", "repos/o/r"))
    assert got.status is None and got.error is not None and "status line" in got.error


def test_gh_response_without_separator_is_transport(monkeypatch):
    _patch(monkeypatch, _Run("", returncode=1))
    assert GhTransport().send(Request("GET", "repos/o/r")).status is None


def test_gh_response_with_non_json_body_has_no_body(monkeypatch):
    _patch(monkeypatch, _Run("HTTP/2.0 502 Bad Gateway\n\n<html>"))
    got = GhTransport().send(Request("GET", "repos/o/r"))
    assert got.status == 502 and got.body is None and got.error is not None


@pytest.mark.parametrize(
    "exc", [subprocess.TimeoutExpired(["gh"], 60), FileNotFoundError("gh"), OSError("boom")]
)
def test_gh_transport_timeout_is_transport_error(monkeypatch, exc):
    _patch(monkeypatch, _Run(raises=exc))
    got = GhTransport().send(Request("GET", "repos/o/r"))
    assert got.status is None and got.error


def test_gh_transport_builds_argv_and_env(monkeypatch, tmp_path: Path):
    ex = exchanges("pr657_act_sequence")[0]  # StewardV3Delivery
    run = _patch(monkeypatch, _Run(_gh_output(ex["response"])))
    monkeypatch.setenv("GH_CONFIG_DIR", "/elsewhere")
    got = GhTransport(tmp_path, timeout=7).send(request_from(ex))
    assert got.status == 200
    argv, kwargs = run.calls[0]
    assert argv == ["gh", "api", "--include", "graphql", "--input", "-"]
    assert json.loads(kwargs["input"]) == {
        "query": ex["request"]["query"],
        "variables": ex["request"]["variables"],
        "operationName": "StewardV3Delivery",
    }
    assert kwargs["env"]["GH_CONFIG_DIR"] == str(tmp_path)
    assert kwargs["timeout"] == 7 and kwargs["check"] is False


def test_gh_transport_without_config_dir_keeps_environment(monkeypatch):
    ex = live("h1_repo_policy")
    run = _patch(monkeypatch, _Run(_gh_output(ex["response"])))
    monkeypatch.setenv("GH_CONFIG_DIR", "/profile/of/caller")
    GhTransport().send(request_from(ex))
    argv, kwargs = run.calls[0]
    assert kwargs["env"]["GH_CONFIG_DIR"] == "/profile/of/caller"
    assert kwargs["input"] is None and kwargs["timeout"] == 60


@pytest.mark.parametrize(
    "req",
    [Request("POST", "repos/o/r/pulls"), Request("DELETE", "repos/o/r"), Request("PUT", "x")],
)
def test_gh_transport_is_read_only(monkeypatch, req):
    run = _patch(monkeypatch, _Run())
    with pytest.raises(ValueError):
        GhTransport().send(req)
    assert run.calls == []


def test_gh_transport_refuses_mutation_text(monkeypatch):
    run = _patch(monkeypatch, _Run())
    with pytest.raises(ValueError):
        GhTransport().send(Request("POST", "graphql", op="X", variables={}, query="mutation X{}"))
    assert run.calls == []
