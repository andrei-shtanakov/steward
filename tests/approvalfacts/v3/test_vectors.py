"""Векторы сбора `approval-facts/v3` (спека steward §5, §5.6 спеки devtools).

Вектор — вход и записанные обмены `ReplayTransport`, выведенные генератором
`scripts/gen_approval_facts_v3_vectors.py` из живых снимков §3.8 именованными
преобразованиями §5. Прогон каждого вектора требует не только ожидаемого исхода, но и
точного потребления: запросы библиотеки идут ровно в порядке записанных обменов, лишних и
непотреблённых нет — иначе вектор с ожиданием `unavailable` проходил бы вакуумно. Копию
`vectors/` вендорит devtools; `MANIFEST.json` контракта несёт sha256 каждого файла.
"""

import copy
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from steward.approvalfacts import v3
from steward.approvalfacts.v3 import Unavailable, _github, passes, read_act_facts, run_vector
from steward.approvalfacts.v3.queries import (
    Q_COMMIT_PULLS,
    Q_DELIVERY,
    Q_REVIEWS,
    Q_TIMELINE,
    q_blobs,
)
from steward.approvalfacts.v3.transport import replay_transport

Record = dict[str, Any]

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "contracts" / "approval-facts" / "v3"
VECTORS = CONTRACT / "vectors"
MANIFEST = CONTRACT / "MANIFEST.json"
GENERATOR = ROOT / "scripts" / "gen_approval_facts_v3_vectors.py"
LIVE = ROOT / "tests" / "fixtures" / "approvalfacts_v3" / "live"

ACT = [
    "clean_approve_app_merge",
    "pagination_reviews_no_cursor",
    "pagination_timeline_over_limit",
    "access_rest_403",
    "access_graphql_401",
    "head_rest_graphql_mismatch",
    "pass_head_differs_from_delivery",
    "pass_base_differs_from_delivery",
    "delivery_changed_on_reread",
    "dismiss_and_cr_between_passes",
    "dismissed_after_preflight_before_merge",
    "graphql_200_partial_errors_approved",
    "restore_approved_cr_dismissed",
    "restore_approved_dismissed_after_t",
    "dismissal_without_previous_state",
    "dismissal_without_previous_state_after_t",
    "dismissal_with_previous_state_twin",
    "login_approved_then_cr",
    "login_cr_then_approved",
    "login_approved_old_then_new",
    "login_approved_then_commented",
    "dismissal_before_merged_at",
    "dismissal_after_merged_at",
    "post_merge_dismissal_between_passes",
    "bot_login_two_forms",
    "bot_login_mismatch",
    "paths_deleted_at_merge",
    "equal_stamp_dismissal_before",
    "equal_stamp_dismissal_after",
    "equal_stamp_approve_before",
    "equal_stamp_approve_after",
    "pending_without_submitted_at",
    "submitted_without_submitted_at",
    "unknown_state",
    "missing_commit_id",
    "dismissed_without_event",
    "actor_unresolved_other_reviewer_null",
    "actor_unresolved_other_reviewer_ghost",
    "actor_unresolved_dismissal_actor",
    "actor_unresolved_merger",
    "actor_unresolved_mannequin",
    "not_merged",
    "live_pr657",
    "live_pr636",
    "paths_three_negative",
    "paths_not_a_blob",
    "dismissal_normal_twin",
    "dismissal_actor_no_typename",
    "bot_login_type_mismatch",
    "merger_bot_suffix_with_user_type",
    "merged_disagreement",
    "merged_event_commit_mismatch",
    "in_base_diverged",
    "dismissal_previous_state_changed_between_passes",
]
OPEN_REVIEW = [
    "clean",
    "head_changed",
    "race_between_pages",
    "actor_unresolved_ghost",
    "review_on_non_head_commit",
    "head_changed_between_pages",
]
HISTORY = [
    "merge_squash_neutral",
    "rebase_multi_commit",
    "rebase_multi_commit_unassociated",
    "rebase_single_commit_accepted",
    "rebase_intermediate_live_association",
    "direct_push",
    "anchor_not_reached",
    "pr_base_mismatch",
    "revert_bytes",
    "branch_not_default",
    "commit_404",
    "tree_404",
    "tree_truncated",
    "policy_file_absent",
    "live_root_tree_blob",
    "live_root_is_commit_without_pr",
]
ACTIVATION = ["ok", "no_pr", "live_merge_commit"]
REQUIRED = frozenset(
    [f"{n}.json" for n in ACT]
    + [f"open_review/{n}.json" for n in OPEN_REVIEW]
    + [f"history/{n}.json" for n in HISTORY]
    + [f"activation/{n}.json" for n in ACTIVATION]
)

#: Форма `expect` по виду вектора (спека §5): ровно один основной ключ и допустимые к нему.
EXPECT_SHAPES: dict[str, dict[str, frozenset[str]]] = {
    "act": {
        "unavailable": frozenset(),
        "waiting_delivery": frozenset(),
        "effective": frozenset({"effective_commit", "delivery"}),
    },
    "open_review": {"unavailable": frozenset(), "effective": frozenset({"effective_commit"})},
    "history": {"activations": frozenset(), "violation": frozenset(), "unavailable": frozenset()},
    "activation": {"activation": frozenset(), "unavailable": frozenset()},
}
KIND_BY_DIR = {".": "act", "open_review": "open_review", "history": "history"}
KIND_BY_DIR["activation"] = "activation"
INPUT_KEYS = {
    "act": {"repo", "pr", "paths", "t", "before"},
    "open_review": {"repo", "pr", "t", "before"},
    "history": {"repo", "branch", "anchor", "until", "files"},
    "activation": {"repo", "merge_commit", "files"},
}
COMMON_KEYS = {"kind", "title", "transport", "expect", "derived_from", "transforms"}

#: Строка §3.8 → положительный двойник: (вектор, живой снимок, чей обмен вектор несёт без
#: изменений) или тест, проходящий на неизменённом снимке (E7 — продюсер release, Task 10).
ROW_TWINS: dict[str, list[tuple[str, str]]] = {
    "G0": [("live_pr657.json", "pr657_act_sequence")],
    "R0": [("live_pr657.json", "pr657_act_sequence")],
    "P1": [("activation/live_merge_commit.json", "h4_commit_pulls_merge_commit")],
    "E1": [("live_pr657.json", "pr657_act_sequence"), ("live_pr636.json", "pr636_act_sequence")],
    "E2": [("live_pr657.json", "pr657_act_sequence"), ("live_pr636.json", "pr636_act_sequence")],
    "E3": [
        ("live_pr657.json", "pr657_act_sequence"),
        ("open_review/clean.json", "closed606_review_pass"),
    ],
    "E4": [("live_pr657.json", "pr657_act_sequence"), ("live_pr636.json", "pr636_act_sequence")],
    "E5": [("live_pr657.json", "pr657_act_sequence"), ("live_pr636.json", "pr636_act_sequence")],
    "E6": [("live_pr657.json", "pr657_act_sequence"), ("live_pr636.json", "pr636_act_sequence")],
    "H1": [("history/live_root_tree_blob.json", "h1_repo_policy")],
    "H2": [("history/live_root_tree_blob.json", "h2_branch_policy")],
    "H3": [
        ("history/live_root_tree_blob.json", "h3_commit_root_no_parents"),
        ("activation/live_merge_commit.json", "h3_commit_merge_two_parents"),
    ],
    "H4": [("activation/live_merge_commit.json", "h4_commit_pulls_merge_commit")],
    "H5": [("history/live_root_tree_blob.json", "h5_tree_recursive_policy")],
    "H6": [("history/live_root_tree_blob.json", "h6_blob_policy_approvers")],
}
ROW_TWIN_TESTS = {"E7": ("test_github.py", "test_e7_positive_twin_single_page")}
#: Операция строки §3.8: по ней ищется обмен строки в векторе-двойнике.
ROW_OP = {"G0": "graphql", "R0": "rest", "P1": "/pulls?"}
ROW_OP |= {"E1": "/pulls/", "E2": "StewardV3Delivery", "E3": "StewardV3Reviews"}
ROW_OP |= {"E4": "StewardV3Timeline", "E5": "/compare/", "E6": "StewardV3Blobs"}
ROW_OP |= {"H1": "repo", "H2": "/branches/", "H3": "/commits/", "H4": "/pulls?"}
ROW_OP |= {"H5": "/git/trees/", "H6": "/git/blobs/"}
NEGATIVE = ("unavailable", "violation")


def _all() -> list[Path]:
    return sorted(VECTORS.rglob("*.json")) if VECTORS.is_dir() else []


def _name(p: Path) -> str:
    return p.relative_to(VECTORS).as_posix()


def _load(name: str) -> Record:
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


def _live(name: str) -> Record:
    return json.loads((LIVE / f"{name}.json").read_text(encoding="utf-8"))


def _live_exchanges(name: str) -> list[Record]:
    data = _live(name)
    return data["exchanges"] if "exchanges" in data else [data]


def _exchanges(spec: Record) -> list[Record]:
    return spec["transport"]["exchanges"]


# --- состав и форма -------------------------------------------------------------------------


def test_required_vectors_present():
    names = {_name(p) for p in _all()}
    assert REQUIRED <= names, sorted(REQUIRED - names)


def test_no_unlisted_vectors():
    """Каждый файл вектора назван здесь: лишний вектор не появляется без ожидания в тесте."""
    assert {_name(p) for p in _all()} <= REQUIRED


def test_manifest_matches_vectors_both_ways():
    """devtools вендорит `vectors/` и сверяет копию: манифест — sha256 каждого файла."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    on_disk = {
        _name(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(VECTORS.rglob("*"))
        if p.is_file()
    }
    assert manifest["vectors"] == on_disk


def test_act_vectors_have_consumer_shape():
    """Форма, которую читает тест devtools Task 11: `transport`, `repo`, `pr`, `t`, `before`."""
    root = sorted(VECTORS.glob("*.json"))
    assert root
    for p in root:
        spec = json.loads(p.read_text(encoding="utf-8"))
        assert spec["kind"] == "act", p.name
        assert {"transport", "repo", "pr", "paths", "t", "before", "expect"} <= spec.keys()


@pytest.mark.parametrize("case", _all(), ids=_name)
def test_vector_shape(case: Path):
    spec = json.loads(case.read_text(encoding="utf-8"))
    kind = KIND_BY_DIR[case.parent.relative_to(VECTORS).as_posix()]
    assert spec["kind"] == kind
    allowed = COMMON_KEYS | INPUT_KEYS[kind] | {"unverified_shape"}
    assert COMMON_KEYS | INPUT_KEYS[kind] <= spec.keys() <= allowed
    assert isinstance(spec["title"], str) and spec["title"]
    primary = [k for k in spec["expect"] if k in EXPECT_SHAPES[kind]]
    assert len(primary) == 1, spec["expect"]
    assert set(spec["expect"]) - {primary[0]} <= EXPECT_SHAPES[kind][primary[0]]


# --- прогон ---------------------------------------------------------------------------------


@pytest.mark.parametrize("case", _all(), ids=_name)
def test_vector(case: Path, key, key_of):
    """Строгий прогон библиотекой (`run_vector`, решение M0-J) плюс независимая сверка
    порядка запросов ключом, написанным без кода библиотеки."""
    spec = json.loads(case.read_text(encoding="utf-8"))
    outcome = run_vector(spec)
    assert outcome.ok, outcome.problems
    assert [key(r) for r in outcome.requests] == [key_of(ex) for ex in _exchanges(spec)]


# --- run_vector: строгие проверки — код библиотеки (M0-J) --------------------------------------


def _mutated(name: str, edit: Callable[[Record], None]) -> Record:
    spec = copy.deepcopy(_load(name))
    edit(spec)
    return spec


def _problems(spec: Record) -> str:
    outcome = run_vector(spec)
    assert not outcome.ok
    return "; ".join(outcome.problems)


def test_run_vector_is_public():
    assert v3.run_vector is run_vector and "run_vector" in v3.__all__
    assert "VectorOutcome" in v3.__all__


def test_run_vector_requires_exact_code():
    spec = _mutated("unknown_state.json", lambda s: s["expect"].update(unavailable="malformed"))
    assert "expected unavailable 'malformed'" in _problems(spec)


def test_run_vector_requires_full_consumption():
    extra = _live("pr657_act_sequence")["exchanges"][6]
    spec = _mutated("missing_commit_id.json", lambda s: _exchanges(s).append(extra))
    assert "unused exchanges: 1" in _problems(spec)


def test_run_vector_reports_unrecorded_request():
    spec = _mutated("missing_commit_id.json", lambda s: _exchanges(s).pop())
    problems = _problems(spec)
    assert "unexpected requests: 1" in problems and "request order" in problems


def test_run_vector_requires_request_order():
    """FIFO на ключ перестановку E5/E6 не ловит; общий порядок — ловит."""

    def swap(s: Record) -> None:
        ex = _exchanges(s)
        ex[2], ex[3] = ex[3], ex[2]

    problems = _problems(_mutated("live_pr657.json", swap))
    assert "request order differs from exchanges at #2" in problems
    assert "unused" not in problems and "unexpected" not in problems


def test_run_vector_checks_delivery_and_effective_commit():
    spec = _mutated("live_pr657.json", lambda s: s["expect"]["delivery"].update(in_base=False))
    assert "delivery.in_base" in _problems(spec)
    spec = _mutated(
        "login_approved_old_then_new.json",
        lambda s: s["expect"]["effective_commit"].update(alice="0" * 40),
    )
    assert "effective_commit.alice" in _problems(spec)


def test_run_vector_waiting_delivery_needs_no_t():
    assert _load("not_merged.json")["t"] is None
    assert run_vector(_load("not_merged.json")).ok
    spec = _mutated("live_pr657.json", lambda s: s.update(expect={"waiting_delivery": True}))
    assert "expected waiting_delivery" in _problems(spec)


@pytest.mark.parametrize(
    "edit",
    [
        lambda s: s.update(kind="nope"),
        lambda s: s["expect"].update(waiting_delivery=True),
        lambda s: s.pop("transport"),
        lambda s: s.pop("repo"),
    ],
    ids=["kind", "two-primary", "no-transport", "no-repo"],
)
def test_run_vector_malformed_vector_is_a_problem(edit):
    assert _problems(_mutated("clean_approve_app_merge.json", edit))


#: Мутанты библиотеки из ревью Task 7, выживавшие при проверке «какой-то `Unavailable`»: на
#: каждый — вектор, который через `run_vector` его убивает.
def _mutant_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:  # M13
    monkeypatch.setattr(_github, "MAX_PAGES", 51)


def _mutant_errors_at_200(monkeypatch: pytest.MonkeyPatch) -> None:  # M14
    real = _github.graphql

    def graphql(t, op, query, variables):
        class _Strip:
            def send(self, request):
                response = t.send(request)
                if response.status == 200 and isinstance(response.body, dict):
                    body = {k: v for k, v in response.body.items() if k != "errors"}
                    return replace(response, body=body)
                return response

        return real(_Strip(), op, query, variables)

    monkeypatch.setattr(_github, "graphql", graphql)


def _mutant_ghost_resolved(monkeypatch: pytest.MonkeyPatch) -> None:  # M20
    monkeypatch.setattr(passes, "actor_resolved", lambda login, kind: kind in ("User", "Bot"))


@pytest.mark.parametrize(
    "mutant,vector",
    [
        (_mutant_max_pages, "pagination_timeline_over_limit.json"),
        (_mutant_errors_at_200, "graphql_200_partial_errors_approved.json"),
        (_mutant_ghost_resolved, "actor_unresolved_other_reviewer_ghost.json"),
        (_mutant_ghost_resolved, "open_review/actor_unresolved_ghost.json"),
    ],
    ids=["M13", "M14", "M20-act", "M20-open"],
)
def test_run_vector_kills_consumer_mutants(monkeypatch, mutant, vector):
    """Под мутантом библиотека всё равно отказывает (`transport` на обрезанной записи) —
    вакуумная проверка типа прошла бы; `run_vector` вектор валит."""
    spec = _load(vector)
    assert run_vector(spec).ok
    mutant(monkeypatch)
    outcome = run_vector(spec)
    assert not outcome.ok, "mutant survived run_vector"
    assert any("expected unavailable" in p for p in outcome.problems), outcome.problems


# --- происхождение: только из живых снимков ---------------------------------------------------


def test_positive_live_vectors_replay_unmodified(key, key_of):
    pairs = [("live_pr657.json", "pr657_act_sequence"), ("live_pr636.json", "pr636_act_sequence")]
    for name, capture in pairs:
        spec = _load(name)
        assert spec["transforms"] == [] and spec["derived_from"] == [capture]
        assert _exchanges(spec) == _live(capture)["exchanges"]
        assert spec["paths"] == _live(capture)["paths"] and len(spec["paths"]) == 1
        t = replay_transport(spec["transport"])
        got = read_act_facts(t, spec["repo"], spec["pr"], paths=spec["paths"])
        assert not isinstance(got, Unavailable), got
        delivery, facts = got
        assert delivery.merged and facts is not None and spec["t"] == delivery.merged_at
        assert t.unused() == [] and t.unexpected == []
        # общий порядок вызовов библиотеки = порядок снимка (спека §3.8, «согласованные
        # наборы»): FIFO на ключ перестановку E5/E6 после проходов не поймал бы
        assert [key(r) for r in t.log] == [key_of(ex) for ex in _exchanges(spec)]


def test_not_merged_is_unmodified_live_capture():
    spec = _load("not_merged.json")
    assert spec["transforms"] == [] and spec["derived_from"] == ["closed606_act_sequence"]
    assert _exchanges(spec) == _live("closed606_act_sequence")["exchanges"]
    rest = next(ex for ex in _exchanges(spec) if ex["request"]["method"] == "GET")
    assert rest["response"]["body"]["merge_commit_sha"]  # ненулевой тестовый SHA (§3.3 п. 1)


@pytest.mark.parametrize("case", _all(), ids=_name)
def test_vector_is_derived_from_live_captures(case: Path, derive, derive_transforms):
    """Обмены вектора — ровно `derive(derived_from, transforms)`: ни одного рукописного."""
    spec = json.loads(case.read_text(encoding="utf-8"))
    assert spec["derived_from"]
    assert all((LIVE / f"{n}.json").exists() for n in spec["derived_from"])
    assert {t["op"] for t in spec["transforms"]} <= derive_transforms
    derived = derive(spec["derived_from"], spec["transforms"])
    assert _exchanges(spec) == derived["exchanges"]
    assert spec.get("unverified_shape") == derived.get("unverified_shape")


@pytest.mark.parametrize("case", _all(), ids=_name)
def test_unverified_shape_marks_dismissal_nodes(case: Path):
    """Узел `ReviewDismissedEvent` (живьём не снят) — только с пометкой `unverified_shape`."""
    spec = json.loads(case.read_text(encoding="utf-8"))
    text = json.dumps(_exchanges(spec))
    if '"ReviewDismissedEvent"' in text:
        assert spec.get("unverified_shape") == "ReviewDismissedEvent"


@pytest.mark.parametrize("case", _all(), ids=_name)
def test_vector_exchanges_carry_library_query_text(case: Path):
    spec = json.loads(case.read_text(encoding="utf-8"))
    texts = {
        "StewardV3Delivery": Q_DELIVERY,
        "StewardV3Reviews": Q_REVIEWS,
        "StewardV3Timeline": Q_TIMELINE,
        "StewardV3CommitPulls": Q_COMMIT_PULLS,
    }
    for ex in _exchanges(spec):
        req = ex["request"]
        if req["method"] != "POST":
            assert "query" not in req and "op" not in req
            continue
        if req["op"] == "StewardV3Blobs":
            n = sum(1 for v in req["variables"] if v.startswith("m") and v[1:].isdigit())
            assert req["query"] == q_blobs(n)
        else:
            assert req["query"] == texts[req["op"]]


def _row_exchanges(row: str, exchanges: list[Record]) -> list[Record]:
    op = ROW_OP[row]
    if op == "graphql":
        return [ex for ex in exchanges if ex["request"]["method"] == "POST"]
    if op == "rest":
        return [ex for ex in exchanges if ex["request"]["method"] == "GET"]
    if op == "repo":
        return [ex for ex in exchanges if ex["request"]["path"].count("/") == 2]
    if op.startswith("Steward"):
        return [ex for ex in exchanges if ex["request"].get("op") == op]
    return [ex for ex in exchanges if op in ex["request"]["path"]]


@pytest.mark.parametrize("row", sorted(ROW_TWINS))
def test_each_contract_row_has_positive_twin(row: str):
    """§3.8: у строки есть вектор без отказа, несущий её обмен из снимка без изменений."""
    for vector, capture in ROW_TWINS[row]:
        spec = _load(vector)
        assert not any(k in spec["expect"] for k in NEGATIVE), vector
        assert capture in spec["derived_from"], vector
        live_rows = _row_exchanges(row, _live_exchanges(capture))
        assert live_rows, (row, capture)
        assert all(ex in _exchanges(spec) for ex in live_rows), (row, vector)


def test_every_contract_row_is_covered():
    rows = {"G0", "R0", "P1"} | {f"E{i}" for i in range(1, 8)} | {f"H{i}" for i in range(1, 7)}
    assert set(ROW_TWINS) | set(ROW_TWIN_TESTS) == rows
    for module, test in ROW_TWIN_TESTS.values():
        text = (Path(__file__).parent / module).read_text(encoding="utf-8")
        assert f"def {test}(" in text


def test_single_commit_rebase_title_names_the_limit():
    spec = _load("history/rebase_single_commit_accepted.json")
    assert "предел, не нарушение" in spec["title"]


# --- генератор ------------------------------------------------------------------------------


def test_generator_is_reproducible(tmp_path: Path):
    """Повторный запуск генератора даёт те же байты, что закоммичены (векторы и манифест)."""
    out = tmp_path / "vectors"
    manifest = tmp_path / "MANIFEST.json"
    subprocess.run(  # noqa: S603 — свой скрипт, фиксированный argv
        [sys.executable, str(GENERATOR), "--out", str(out), "--manifest", str(manifest)],
        check=True,
        cwd=tmp_path,
        capture_output=True,
        timeout=120,
    )
    generated = {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*.json")}
    committed = {_name(p): p.read_bytes() for p in _all()}
    assert generated.keys() == committed.keys()
    assert [n for n in committed if generated[n] != committed[n]] == []
    assert manifest.read_bytes() == MANIFEST.read_bytes()


def _generate(out: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — свой скрипт, фиксированный argv
        [sys.executable, str(GENERATOR), "--out", str(out), "--manifest", str(tmp_path / "M")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_generator_refuses_out_not_named_vectors(tmp_path: Path):
    """`--out contracts/approval-facts/v3` не стирает SCHEMA.json и README.md."""
    out = tmp_path / "v3"
    out.mkdir()
    (out / "SCHEMA.json").write_text("{}", encoding="utf-8")
    proc = _generate(out, tmp_path)
    assert proc.returncode == 2 and "named 'vectors'" in proc.stderr
    assert (out / "SCHEMA.json").read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize("foreign", ["README.md", "notes/x.json", "history/x.txt"])
def test_generator_refuses_foreign_entries(tmp_path: Path, foreign: str):
    out = tmp_path / "vectors"
    target = out / foreign
    target.parent.mkdir(parents=True)
    target.write_text("keep", encoding="utf-8")
    proc = _generate(out, tmp_path)
    assert proc.returncode == 2 and "foreign entry" in proc.stderr
    assert target.read_text(encoding="utf-8") == "keep"


# --- derive: `select` (порядок и потреблённый префикс) ------------------------------------------


def test_derive_select_keeps_order(derive):
    base = ["pr657_act_sequence", "neg_e6_blobs_present_absent_tree"]
    full = derive(base, [])["exchanges"]
    got = derive(base, [{"op": "select", "keep": [0, 1, 2, 10]}])["exchanges"]
    assert got == [full[0], full[1], full[2], full[10]]


@pytest.mark.parametrize("keep", [[], [0, 0], [10], [-1], ["0"], [True], "0"])
def test_derive_select_rejects_bad_keep(derive, keep):
    with pytest.raises(ValueError, match="select"):
        derive(["pr657_act_sequence"], [{"op": "select", "keep": keep}])
