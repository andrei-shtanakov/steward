"""Capture script for `approval-facts/v3` live snapshots: fail-closed, atomic, digested.

The real ``gh`` is never called: ``_run_gh`` is replaced by a stub that serves the
committed captures back (so a successful run must reproduce them byte for byte)
and can be told to break exactly one exchange.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "capture_approval_facts_v3_live.py"
LIVE = ROOT / "tests" / "fixtures" / "approvalfacts_v3" / "live"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("capture_approval_facts_v3_live", SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging error
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["capture_approval_facts_v3_live"] = module
    spec.loader.exec_module(module)
    return module


CAP = _load()


def _exchanges() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(LIVE.glob("*.json")):
        if path.name == "MANIFEST.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        out.extend(data["exchanges"] if "exchanges" in data else [data])
    return out


def _key(request: dict[str, Any]) -> str:
    if request["method"] == "GET":
        return request["path"]
    return request["op"] + json.dumps(request["variables"], sort_keys=True)


def _served() -> dict[str, dict[str, Any]]:
    served = {_key(ex["request"]): ex["response"] for ex in _exchanges()}
    # `_full_sha(POL, "main")` asks for the branch tip by name; it is the root commit.
    root = json.loads((LIVE / "h3_commit_root_no_parents.json").read_text(encoding="utf-8"))
    served["repos/andrei-shtanakov/approval-policy/commits/main"] = root["response"]
    return served


def _gh_output(response: dict[str, Any]) -> str:
    head = f"HTTP/2.0 {response['status']} X"
    if response.get("link"):
        head += f"\nLink: {response['link']}"
    return f"{head}\n\n{json.dumps(response['body'])}"


class _Stub:
    """Serves the committed captures; ``break_at``/``how`` corrupt one exchange."""

    def __init__(self, break_at: int | None = None, how: str = "") -> None:
        self.served = _served()
        self.calls = 0
        self.break_at = break_at
        self.how = how

    def __call__(self, argv: list[str], stdin: str | None) -> tuple[int, str, str]:
        if stdin is not None:
            payload = json.loads(stdin)
            key = payload["operationName"] + json.dumps(payload["variables"], sort_keys=True)
        else:
            key = argv[-1]
        response = self.served[key]
        n, self.calls = self.calls, self.calls + 1
        status = response["status"]
        body = response["body"]
        failed = (not 200 <= status < 300) or (isinstance(body, dict) and bool(body.get("errors")))
        rc, out = (1 if failed else 0), _gh_output(response)
        if n == self.break_at:
            if self.how == "rc":
                return 1, out, "gh: HTTP 502"
            if self.how == "status":
                return 1, _gh_output({**response, "status": 403}), "gh: HTTP 403"
            if self.how == "body":
                return 0, "HTTP/2.0 200 OK\n\n<html>rate limited</html>", ""
            if self.how == "graphql_errors" and stdin is not None:
                bad = {**body, "errors": [{"type": "FORBIDDEN", "message": "x"}]}
                return 1, _gh_output({**response, "body": bad}), "gh: GraphQL errors"
            if self.how == "empty":
                return 0, "", ""
        return rc, out, ""


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


# --- MANIFEST of the committed captures --------------------------------------------


def test_manifest_matches_directory_both_ways() -> None:
    manifest = json.loads((LIVE / "MANIFEST.json").read_text(encoding="utf-8"))
    on_disk = {p.name for p in LIVE.iterdir() if p.name != "MANIFEST.json"}
    assert set(manifest["files"]) == on_disk, "missing or extra capture file"
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((LIVE / name).read_bytes()).hexdigest() == digest, name


def test_committed_captures_hold_no_private_repository() -> None:
    manifest = json.loads((LIVE / "MANIFEST.json").read_text(encoding="utf-8"))
    assert set(manifest["repos"]) == set(CAP.ALLOWED_REPOS)
    assert "df-roles-probe-private" not in json.dumps(sorted(CAP.ALLOWED_REPOS))
    for path in LIVE.glob("*.json"):
        assert '"private": true' not in path.read_text(encoding="utf-8"), path.name


# --- fail-closed ----------------------------------------------------------------------


def test_replay_of_committed_captures_reproduces_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive twin: a clean run writes exactly the committed bytes and digests."""
    monkeypatch.setattr(CAP, "_run_gh", _Stub())
    out = tmp_path / "live"
    assert CAP.main(["--out", str(out)]) == 0
    written, committed = _snapshot(out), _snapshot(LIVE)
    assert set(written) == set(committed)
    for name in committed:
        if name != "MANIFEST.json":
            assert written[name] == committed[name], name
    assert (
        json.loads(written["MANIFEST.json"])["files"]
        == json.loads(committed["MANIFEST.json"])["files"]
    )


@pytest.mark.parametrize("how", ["rc", "status", "body", "graphql_errors", "empty"])
@pytest.mark.parametrize("break_at", [0, 9, 23])
def test_any_bad_exchange_aborts_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str, break_at: int
) -> None:
    if how == "graphql_errors":
        # GraphQL exchanges only: 0 = Delivery #657, 4 = Reviews #657, 10 = Delivery #636.
        break_at = {0: 0, 9: 4, 23: 10}[break_at]
    monkeypatch.setattr(CAP, "_run_gh", _Stub(break_at=break_at, how=how))
    out = tmp_path / "live"
    assert CAP.main(["--out", str(out)]) != 0
    assert not out.exists()
    assert list(tmp_path.iterdir()) == [], "temporary directory left behind"


def test_failed_run_keeps_previous_capture_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "live"
    out.mkdir()
    (out / "old.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(CAP, "_run_gh", _Stub(break_at=3, how="status"))
    assert CAP.main(["--out", str(out)]) != 0
    assert _snapshot(out) == {"old.json": b"{}\n"}


def test_successful_run_drops_stale_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "live"
    out.mkdir()
    (out / "renamed_capture.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(CAP, "_run_gh", _Stub())
    assert CAP.main(["--out", str(out)]) == 0
    assert not (out / "renamed_capture.json").exists()


def test_declared_negative_shapes_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """`neg_e2_delivery_absent_pr` (200 + errors) and `h5_tree_absent_422` are declared."""
    monkeypatch.setattr(CAP, "_run_gh", _Stub())
    absent = json.loads((LIVE / "neg_e2_delivery_absent_pr.json").read_text())["request"]
    got = CAP.graphql(
        absent["op"],
        absent["query"],
        absent["variables"],
        CAP.Expect(status=200, graphql_errors=True),
    )
    assert got["response"]["body"]["errors"]
    tree = json.loads((LIVE / "h5_tree_absent_422.json").read_text())["request"]["path"]
    assert CAP.rest(tree, CAP.Expect(status=422))["response"]["status"] == 422


def test_undeclared_negative_shape_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CAP, "_run_gh", _Stub())
    tree = json.loads((LIVE / "h5_tree_absent_422.json").read_text())["request"]["path"]
    with pytest.raises(CAP.CaptureError):
        CAP.rest(tree)  # 422 without Expect(status=422)


def test_private_repository_is_refused() -> None:
    with pytest.raises(CAP.CaptureError):
        CAP.rest("repos/andrei-shtanakov/df-roles-probe-private/pulls/1")


def test_mutation_is_refused() -> None:
    with pytest.raises(CAP.CaptureError):
        CAP.graphql("X", "mutation X{x}", {"owner": "andrei-shtanakov", "name": "devtools"})
