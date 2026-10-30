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
from collections.abc import Callable
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
    """Serves the committed captures; ``corrupt`` breaks the exchanges it selects.

    ``corrupt(n, key)`` receives the call index and the request key (REST path, or
    GraphQL op + variables) and returns the name of the corruption or ``None``.
    """

    def __init__(self, corrupt: Callable[[int, str], str | None] | None = None) -> None:
        self.served = _served()
        self.calls = 0
        self.corrupt = corrupt or (lambda n, key: None)

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
        how = self.corrupt(n, key)
        if how == "rc":
            return 1, out, "gh: HTTP 502"
        if how == "status":
            return 1, _gh_output({**response, "status": 403}), "gh: HTTP 403"
        if how == "body":
            return 0, "HTTP/2.0 200 OK\n\n<html>rate limited</html>", ""
        if how == "graphql_errors":
            bad = {**body, "errors": [{"type": "FORBIDDEN", "message": "x"}]}
            return 1, _gh_output({**response, "body": bad}), "gh: GraphQL errors"
        if how == "empty":
            return 0, "", ""
        if how == "has_next":
            text = json.dumps(body).replace('"hasNextPage": false', '"hasNextPage": true', 1)
            return 0, _gh_output({**response, "body": json.loads(text)}), ""
        if how == "truncated":
            return 0, _gh_output({**response, "body": {**body, "truncated": True}}), ""
        if how == "link_next":
            return 0, _gh_output({**response, "link": '<https://x/?page=2>; rel="next"'}), ""
        if how == "private":
            return 0, _gh_output({**response, "body": {**body, "private": True}}), ""
        if how == "internal":
            return 0, _gh_output({**response, "body": {**body, "visibility": "internal"}}), ""
        return rc, out, ""


def _forbidden(argv: list[str], stdin: str | None) -> tuple[int, str, str]:
    """A refusal must happen before the process boundary: reaching gh fails the test."""
    raise AssertionError(f"gh must not be called: {argv}")


def _at(index: int, how: str) -> Callable[[int, str], str | None]:
    return lambda n, key: how if n == index else None


def _where(pred: Callable[[str], bool], how: str) -> Callable[[int, str], str | None]:
    return lambda n, key: how if pred(key) else None


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


# --- MANIFEST and publicity of the committed captures --------------------------------


def test_manifest_matches_directory_both_ways() -> None:
    manifest = json.loads((LIVE / "MANIFEST.json").read_text(encoding="utf-8"))
    on_disk = {p.name for p in LIVE.iterdir() if p.name != "MANIFEST.json"}
    assert set(manifest["files"]) == on_disk, "missing or extra capture file"
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((LIVE / name).read_bytes()).hexdigest() == digest, name


def test_allowlist_is_exactly_the_two_public_repositories() -> None:
    assert CAP.ALLOWED_REPOS == {
        "andrei-shtanakov/devtools",
        "andrei-shtanakov/approval-policy",
    }


def test_every_allowlisted_repository_has_a_live_publicity_proof() -> None:
    """Publicity is a property proven live, not a name: one REST repo capture per repo."""
    manifest = json.loads((LIVE / "MANIFEST.json").read_text(encoding="utf-8"))
    assert set(manifest["repos"]) == set(CAP.ALLOWED_REPOS)
    for repo in CAP.ALLOWED_REPOS:
        proof = json.loads(
            (LIVE / f"pub_{repo.split('/')[1].replace('-', '_')}.json").read_text("utf-8")
        )
        assert proof["request"] == {"method": "GET", "path": f"repos/{repo}"}
        body = proof["response"]["body"]
        assert body["full_name"] == repo
        assert body["private"] is False and body["visibility"] == "public"


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


def test_publicity_proof_is_the_first_exchange_per_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    stub = _Stub()

    def recording(argv: list[str], stdin: str | None) -> tuple[int, str, str]:
        if stdin is not None:
            variables = json.loads(stdin)["variables"]
            seen.append(f"{variables['owner']}/{variables['name']}")
        else:
            parts = argv[-1].split("/")
            seen.append(argv[-1] if len(parts) == 3 else f"{parts[1]}/{parts[2]}")
        return stub(argv, stdin)

    monkeypatch.setattr(CAP, "_run_gh", recording)
    CAP.captures()
    for repo in CAP.ALLOWED_REPOS:
        assert seen.index(f"repos/{repo}") < seen.index(repo), repo


def _aborts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub: Any) -> None:
    monkeypatch.setattr(CAP, "_run_gh", stub)
    out = tmp_path / "live"
    assert CAP.main(["--out", str(out)]) != 0
    assert not out.exists()
    assert list(tmp_path.iterdir()) == [], "temporary directory left behind"


@pytest.mark.parametrize("how", ["rc", "status", "body", "empty"])
@pytest.mark.parametrize("index", [0, 11, 25])
def test_any_bad_exchange_aborts_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str, index: int
) -> None:
    _aborts(tmp_path, monkeypatch, _Stub(_at(index, how)))


@pytest.mark.parametrize("op", ["StewardV3Delivery", "StewardV3Reviews", "StewardV3Blobs"])
def test_undeclared_graphql_errors_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, op: str
) -> None:
    _aborts(tmp_path, monkeypatch, _Stub(_where(lambda key: key.startswith(op), "graphql_errors")))


@pytest.mark.parametrize("op", ["StewardV3Reviews", "StewardV3Timeline", "StewardV3CommitPulls"])
def test_next_page_in_a_single_page_capture_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, op: str
) -> None:
    _aborts(tmp_path, monkeypatch, _Stub(_where(lambda key: key.startswith(op), "has_next")))


def test_rest_next_link_in_a_complete_capture_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pick = _where(lambda key: key.endswith("/pulls?per_page=100"), "link_next")
    _aborts(tmp_path, monkeypatch, _Stub(pick))


def test_truncated_tree_aborts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pick = _where(lambda key: "/git/trees/" in key and "0000000000" not in key, "truncated")
    _aborts(tmp_path, monkeypatch, _Stub(pick))


@pytest.mark.parametrize("how", ["private", "internal"])
@pytest.mark.parametrize("repo", sorted(CAP.ALLOWED_REPOS))
def test_unproven_publicity_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str, repo: str
) -> None:
    _aborts(tmp_path, monkeypatch, _Stub(_where(lambda key: key == f"repos/{repo}", how)))


def test_failed_run_keeps_previous_capture_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "live"
    out.mkdir()
    (out / "old.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(CAP, "_run_gh", _Stub(_at(5, "status")))
    assert CAP.main(["--out", str(out)]) != 0
    assert _snapshot(out) == {"old.json": b"{}\n"}


def test_successful_run_drops_stale_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "live"
    out.mkdir()
    (out / "renamed_capture.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(CAP, "_run_gh", _Stub())
    assert CAP.main(["--out", str(out)]) == 0
    assert not (out / "renamed_capture.json").exists()


def _prove_all(monkeypatch: pytest.MonkeyPatch) -> None:
    CAP._PROVEN_PUBLIC.clear()
    monkeypatch.setattr(CAP, "_run_gh", _Stub())
    for repo in sorted(CAP.ALLOWED_REPOS):
        CAP.prove_public(repo)


def test_declared_negative_shapes_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """`neg_e2_delivery_absent_pr` (200 + errors) and `h5_tree_absent_422` are declared."""
    _prove_all(monkeypatch)
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
    _prove_all(monkeypatch)
    tree = json.loads((LIVE / "h5_tree_absent_422.json").read_text())["request"]["path"]
    with pytest.raises(CAP.CaptureError, match=r"gh exit code 1"):
        CAP.rest(tree)  # 422 without Expect(status=422)


# --- guards: refused before the process boundary (gh is never reached) ----------------


def test_private_repository_rest_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CAP, "_run_gh", _forbidden)
    with pytest.raises(CAP.CaptureError, match="outside the read-only allowlist"):
        CAP.rest("repos/andrei-shtanakov/df-roles-probe-private/pulls/1")


def test_private_repository_proof_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CAP, "_run_gh", _forbidden)
    with pytest.raises(CAP.CaptureError, match="outside the read-only allowlist"):
        CAP.prove_public("andrei-shtanakov/df-roles-probe-private")


def test_private_repository_graphql_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CAP, "_run_gh", _forbidden)
    variables = {"owner": "andrei-shtanakov", "name": "df-roles-probe-private", "number": 1}
    with pytest.raises(CAP.CaptureError, match="outside the read-only allowlist"):
        CAP.graphql("StewardV3Delivery", CAP.Q_DELIVERY, variables)


def test_mutation_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CAP, "_run_gh", _forbidden)
    with pytest.raises(CAP.CaptureError, match="refusing a GraphQL text containing 'mutation'"):
        CAP.graphql("X", "mutation X{x}", {"owner": "andrei-shtanakov", "name": "devtools"})


@pytest.mark.parametrize("call", ["rest", "graphql"])
def test_exchange_before_publicity_proof_is_refused(
    monkeypatch: pytest.MonkeyPatch, call: str
) -> None:
    CAP._PROVEN_PUBLIC.clear()
    monkeypatch.setattr(CAP, "_run_gh", _forbidden)
    with pytest.raises(CAP.CaptureError, match="publicity of .* not proven"):
        if call == "rest":
            CAP.rest("repos/andrei-shtanakov/devtools/pulls/657")
        else:
            CAP.graphql(
                "StewardV3Delivery",
                CAP.Q_DELIVERY,
                {"owner": "andrei-shtanakov", "name": "devtools", "number": 657},
            )
