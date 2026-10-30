"""Канонические тексты GraphQL `approval-facts/v3` (спека §3.8): библиотека = снимки = спека."""

import re

import pytest

from steward.approvalfacts.v3.queries import (
    Q_COMMIT_PULLS,
    Q_DELIVERY,
    Q_REVIEWS,
    Q_TIMELINE,
    q_blobs,
)
from ._live import LIVE_CAPTURES, ROOT, exchanges  # pyrefly: ignore[missing-import]

SPEC = ROOT / "docs" / "superpowers" / "specs" / "2026-10-30-approval-facts-v3-design.md"
SCRIPT = ROOT / "scripts" / "capture_approval_facts_v3_live.py"


def _blob_paths(req: dict) -> int:
    return sum(1 for k in req["variables"] if re.fullmatch(r"m\d+", k))


def _expected(req: dict) -> str:
    return {
        "StewardV3Delivery": Q_DELIVERY,
        "StewardV3Reviews": Q_REVIEWS,
        "StewardV3Timeline": Q_TIMELINE,
        "StewardV3CommitPulls": Q_COMMIT_PULLS,
        "StewardV3Blobs": q_blobs(_blob_paths(req)),
    }[req["op"]]


@pytest.mark.parametrize("name", LIVE_CAPTURES)
def test_library_query_text_equals_live_capture(name):
    for ex in exchanges(name):
        req = ex["request"]
        if req["method"] != "POST":
            continue
        assert req["query"] == _expected(req), (name, req["op"])


def test_every_graphql_operation_is_captured_live():
    ops = {
        ex["request"]["op"]
        for name in LIVE_CAPTURES
        for ex in exchanges(name)
        if ex["request"]["method"] == "POST"
    }
    assert ops == {
        "StewardV3Delivery",
        "StewardV3Reviews",
        "StewardV3Timeline",
        "StewardV3CommitPulls",
        "StewardV3Blobs",
    }


def _spec_queries() -> dict[str, str]:
    block = SPEC.read_text(encoding="utf-8").split("```graphql\n", 1)[1].split("```", 1)[0]
    return {line.split("(", 1)[0].removeprefix("query "): line for line in block.splitlines()}


def test_library_query_text_equals_spec_table():
    spec = _spec_queries()
    assert spec["StewardV3Delivery"] == Q_DELIVERY
    assert spec["StewardV3Reviews"] == Q_REVIEWS
    assert spec["StewardV3Timeline"] == Q_TIMELINE
    assert spec["StewardV3CommitPulls"] == Q_COMMIT_PULLS
    # В спеке `StewardV3Blobs` дан для одного пути с многоточиями «…» на месте прочих.
    assert spec["StewardV3Blobs"].replace(",…", "").replace(" …", "") == q_blobs(1)


def test_q_blobs_aliases_per_path():
    text = q_blobs(3)
    for i in range(3):
        assert f",$m{i}:String!,$h{i}:String!" in text
        assert f" m{i}:object(expression:$m{i})" in text
        assert f" h{i}:object(expression:$h{i})" in text
    assert "$m3" not in text


def test_queries_are_read_only():
    for text in (Q_DELIVERY, Q_REVIEWS, Q_TIMELINE, Q_COMMIT_PULLS, q_blobs(2)):
        assert text.startswith("query StewardV3") and "mutation" not in text


def test_capture_script_imports_library_queries():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "from steward.approvalfacts.v3.queries import" in src
    assert "Q_DELIVERY = (" not in src and "def q_blobs" not in src
