"""Публичный API `steward.approvalfacts.v3` (спека steward §3.1, §3.2; решения M0-J, M0-K)."""

import importlib
import inspect
import socket
import subprocess
import sys
from collections.abc import Callable

import pytest

import steward.approvalfacts
from steward.approvalfacts import v3

EXPECTED = [
    "Activation",
    "Delivery",
    "Dismissal",
    "EventKey",
    "GhTransport",
    "HISTORY_VIOLATION_CODES",
    "HistoryViolation",
    "POLICY_FILES",
    "PathBlob",
    "ReplayTransport",
    "Request",
    "Response",
    "Review",
    "ReviewFacts",
    "Transport",
    "UNAVAILABLE_CODES",
    "Unavailable",
    "VectorOutcome",
    "effective_reviews",
    "read_act_facts",
    "read_activation",
    "read_open_review",
    "read_policy_history",
    "replay_transport",
    "run_vector",
]

_KW = inspect.Parameter.KEYWORD_ONLY


def _params(fn: Callable[..., object]) -> dict[str, inspect.Parameter]:
    return dict(inspect.signature(fn).parameters)


def test_all_is_exact():
    assert sorted(v3.__all__) == sorted(EXPECTED)
    assert len(v3.__all__) == len(set(v3.__all__))


def test_every_exported_name_resolves():
    for name in v3.__all__:
        assert hasattr(v3, name), name


def test_transport_names_come_from_transport_module():
    from steward.approvalfacts.v3 import transport

    for name in ("GhTransport", "ReplayTransport", "Request", "Response", "Transport"):
        assert getattr(v3, name) is getattr(transport, name)
    assert v3.replay_transport is transport.replay_transport


def test_signatures_match_consumer_interface():
    act = _params(v3.read_act_facts)
    assert list(act) == ["transport", "repo", "pr", "paths"]
    assert act["paths"].kind is _KW

    eff = _params(v3.effective_reviews)
    assert list(eff) == ["facts", "t", "before"]
    assert eff["before"].kind is _KW

    hist = _params(v3.read_policy_history)
    assert list(hist) == ["transport", "repo", "branch", "anchor", "until", "files"]
    assert all(hist[n].kind is _KW for n in ("anchor", "until", "files"))
    assert hist["files"].default == v3.POLICY_FILES

    assert list(_params(v3.read_open_review)) == ["transport", "repo", "pr"]

    act1 = _params(v3.read_activation)
    assert list(act1) == ["transport", "repo", "merge_commit", "files"]
    assert act1["files"].kind is _KW
    assert act1["files"].default == v3.POLICY_FILES

    assert list(_params(v3.run_vector)) == ["vector"]


def test_gh_transport_constructor():
    params = _params(v3.GhTransport)
    assert list(params) == ["gh_config_dir", "timeout"]
    assert params["gh_config_dir"].default is None
    assert params["timeout"].kind is _KW and params["timeout"].default == 60


def test_import_has_no_io(monkeypatch: pytest.MonkeyPatch):
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("I/O at import time")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(steward.approvalfacts, "v3", v3)
    for mod in [m for m in sys.modules if m.startswith("steward.approvalfacts.v3")]:
        monkeypatch.delitem(sys.modules, mod)

    fresh = importlib.import_module("steward.approvalfacts.v3")

    assert fresh is not v3
    assert sorted(fresh.__all__) == sorted(EXPECTED)
