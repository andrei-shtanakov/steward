"""История источника политики и активация (спека steward §3.7, строки H1–H6 §3.8).

Положительные двойники — неизменённые живые снимки: `approval-policy` (H1, H2, корень H3 без
родителей, H4 `[]`, полное дерево H5, блоб H6) и devtools (merge-коммит #657 с боковым
коммитом, squash #636). Прочие истории — производные `_derive.py` (фикстура `derive`) от тех
же снимков: `set_field` подменяет путь запроса, `sha`, `parents`, `commit.tree.sha`, список PR,
записи дерева, содержимое блоба. Обмены собираются в порядке запросов §3.7 — H1, H2, H3 от
головы к якорю, H4 от якоря к голове, H5 от якоря к голове, H6 по активациям — и тест требует
ровно этот порядок и полного потребления. Каждый отрицательный вектор несёт ровно одно
нарушение.
"""

import base64
import copy
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

import steward.approvalfacts.v3 as v3
from steward.approvalfacts.v3 import history
from steward.approvalfacts.v3.history import POLICY_FILES, read_activation, read_policy_history
from steward.approvalfacts.v3.model import Activation, HistoryViolation, Unavailable
from steward.approvalfacts.v3.transport import ReplayTransport, replay_transport

Record = dict[str, Any]

OWNER = "andrei-shtanakov"
REPO = f"{OWNER}/approval-policy"
DEVTOOLS = f"{OWNER}/devtools"
APPROVERS, MODES, ACTORS = POLICY_FILES

# Живые значения снимков.
ROOT = "12d198fb4db9ed6fbb1c5b00af308eeb31e4ec22"  # approval-policy: корень, прямой коммит
ROOT_TREE = "11134f9a63befbba4706708a1a42036b00400136"
LIVE_APPROVERS_SHA256 = "098f56b6f2afe0b97950ddfb50cd0ed5f386728fa2e22f29855eae1c336a8e0f"
MERGE_657 = "aa4e7e37d9f1ea34581c9c484d60074b6d3ac18e"  # merge-коммит, родители [BASE, SIDE]
SIDE_657 = "b3651bcb2907bd6d1b6d421b018701520fb64066"  # боковой коммит #657
BASE_657 = "9da14cff73a8e2558d1f7f1b19599d50b5456bd7"
TREE_657 = "c9527aa44763f57b3a4c310809f92378486a7e4f"
SQUASH_636 = "1f350c74a4909454619b820d29cbaea244a68733"
ABSENT_TREE = "0" * 40  # путь снимка h5_tree_absent_422

V1 = b"AUTHORIZED_APPROVER_ACCOUNTS=alice\n"
V2 = b"AUTHORIZED_APPROVER_ACCOUNTS=alice,bob\n"
MODES_V1 = b"default: legacy\n"
ACTORS_V1 = b"agents: [robot]\n"


def _sha(tag: str) -> str:
    """Синтетический 40-hex SHA производной истории."""
    return hashlib.sha1(tag.encode()).hexdigest()


def _oid(data: bytes) -> str:
    """Git blob oid содержимого (как у GitHub)."""
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


A, B, C, D, S, R = (_sha(x) for x in "ABCDSR")
X1, X2 = _sha("X1"), _sha("X2")


@dataclass
class _Builder:
    """Обмены истории из живых шаблонов: имена снимков + `set_field` (через `derive`)."""

    derive: Callable[[list[str], list[Record]], Record]
    live: Callable[[str], Record]
    repo: str = REPO
    names: list[str] = field(default_factory=list)
    transforms: list[Record] = field(default_factory=list)

    def raw(self, name: str, **sets: Any) -> "_Builder":
        """Снимок `name`; `sets` — `set_field` по пути `a__b__0` от корня обмена."""
        i = len(self.names)
        self.names.append(name)
        for dotted, value in sets.items():
            steps = [int(s) if s.isdigit() else s for s in dotted.split("__")]
            self.transforms.append({"op": "set_field", "path": [i, *steps], "value": value})
        return self

    def _body(self, name: str) -> Any:
        return self.live(name)["response"]["body"]

    def repository(self, default: str = "main") -> "_Builder":
        return self.raw(
            "h1_repo_policy",
            request__path=f"repos/{self.repo}",
            response__body__default_branch=default,
        )

    def branch(self, head: str, name: str = "main") -> "_Builder":
        return self.raw(
            "h2_branch_policy",
            request__path=f"repos/{self.repo}/branches/{name}",
            response__body__commit__sha=head,
        )

    def commit(self, sha: str, parents: list[str], tree: str) -> "_Builder":
        template = self._body("h3_commit_side_one_parent")["parents"][0]
        return self.raw(
            "h3_commit_side_one_parent",
            request__path=f"repos/{self.repo}/commits/{sha}",
            response__body__sha=sha,
            response__body__parents=[{**template, "sha": p} for p in parents],
            response__body__commit__tree__sha=tree,
        )

    def pulls(self, sha: str, *prs: Record) -> "_Builder":
        template = self._body("h4_commit_pulls_squash")[0]
        body = []
        for pr in prs:
            item = copy.deepcopy(template)
            item["number"] = pr["number"]
            item["merge_commit_sha"] = pr.get("merge", sha)
            item["merged_at"] = pr.get("merged_at", "2026-10-01T10:00:00Z")
            item["base"]["ref"] = pr.get("base", "main")
            body.append(item)
        return self.raw(
            "h4_commit_pulls_squash",
            request__path=f"repos/{self.repo}/commits/{sha}/pulls?per_page=100",
            response__body=body,
        )

    def tree(self, tree: str, files: dict[str, bytes], **opts: Any) -> "_Builder":
        """Полное дерево; `kinds` — тип записи по пути (иначе `blob`), `truncated`."""
        template = self._body("h5_tree_recursive_policy")["tree"]
        kinds = opts.get("kinds", {})
        entries = [copy.deepcopy(template[0])]  # README.md — посторонняя запись
        for path, data in files.items():
            entry = copy.deepcopy(template[2])
            entry.update(path=path, type=kinds.get(path, "blob"), sha=_oid(data))
            entries.append(entry)
        if (dup := opts.get("duplicate")) is not None:
            entries.append(copy.deepcopy(next(e for e in entries if e["path"] == dup)))
        return self.raw(
            "h5_tree_recursive_policy",
            request__path=f"repos/{self.repo}/git/trees/{tree}?recursive=1",
            response__body__sha=tree,
            response__body__tree=entries,
            response__body__truncated=opts.get("truncated", False),
        )

    def blob(self, data: bytes, **sets: Any) -> "_Builder":
        oid = _oid(data)
        defaults = {
            "request__path": f"repos/{self.repo}/git/blobs/{oid}",
            "response__body__sha": oid,
            "response__body__content": base64.b64encode(data).decode(),
        }
        return self.raw("h6_blob_policy_approvers", **{**defaults, **sets})

    def missing(self, path: str, status: int = 404) -> "_Builder":
        """Отказ форджа на `path` — из живого снимка 422 (`h5_tree_absent_422`)."""
        return self.raw(
            "h5_tree_absent_422", request__path=f"repos/{self.repo}/{path}", response__status=status
        )

    def build(self) -> tuple[Record, ReplayTransport]:
        spec = self.derive(self.names, self.transforms)
        return spec, replay_transport(spec)


@dataclass
class Link:
    """Звено first-parent: PR-мерж `pr` (None — без PR) и файлы политики дерева."""

    sha: str
    parent: str
    pr: int | None
    files: dict[str, bytes]
    merged_at: str = "2026-10-01T10:00:00Z"

    @property
    def tree(self) -> str:
        return _sha(f"tree-{self.sha}")


@pytest.fixture
def builder(derive, live) -> Callable[..., _Builder]:
    return lambda repo=REPO: _Builder(derive, live, repo)


@pytest.fixture
def check(key, key_of) -> Callable[[Record, ReplayTransport], None]:
    """Обмены потреблены ровно в записанном порядке, лишних запросов нет."""

    def _check(spec: Record, t: ReplayTransport) -> None:
        assert t.unexpected == [] and t.unused() == []
        assert [key(r) for r in t.log] == [key_of(ex) for ex in spec["exchanges"]]

    return _check


def _full(b: _Builder, links: list[Link], *, head: bool = True, files=POLICY_FILES) -> _Builder:
    """Успешная история `links` (от якоря к голове) в порядке запросов §3.7."""
    b.repository()
    if head:
        b.branch(links[-1].sha)
    for link in reversed(links):
        b.commit(link.sha, [link.parent], link.tree)
    for link in links:
        b.pulls(link.sha, {"number": link.pr, "merged_at": link.merged_at})
    for link in links:
        b.tree(link.tree, link.files)
    previous = None
    for link in links:
        current = {p: _oid(d) for p, d in link.files.items() if p in files}
        if current != previous:
            for path in files:
                if path in link.files:
                    b.blob(link.files[path])
        previous = current
    return b


def _chain_prefix(b: _Builder, links: list[Link]) -> _Builder:
    """H1, H2 и цепочка H3 от головы к якорю."""
    b.repository().branch(links[-1].sha)
    for link in reversed(links):
        b.commit(link.sha, [link.parent], link.tree)
    return b


def _history(t: ReplayTransport, *, anchor: str = A, until: str | None = None, **kw: Any):
    return read_policy_history(t, REPO, "main", anchor=anchor, until=until, **kw)


# --- положительные двойники на неизменённых живых снимках ------------------------------------


def test_live_root_as_anchor_is_commit_without_pr(exchanges, key, key_of):
    """H1, H2, H3 (корень), H4 (`[]`) без преобразований: корень `approval-policy` — прямой
    коммит, якорем он даёт `commit_without_pr` (спека §3.8, наблюдения для #211)."""
    names = ["h1_repo_policy", "h2_branch_policy", "h3_commit_root_no_parents"]
    seq = [ex for n in [*names, "h4_commit_pulls_direct_push"] for ex in exchanges(n)]
    t = replay_transport({"exchanges": seq})
    got = _history(t, anchor=ROOT)
    assert isinstance(got, HistoryViolation) and got.code == "commit_without_pr"
    assert t.unused() == [] and [key(r) for r in t.log] == [key_of(ex) for ex in seq]


def test_live_root_without_anchor_is_anchor_not_reached(exchanges, key, key_of):
    """Цепочка прочитана до корня (`parents: []`) без якоря — нарушение, не недоступность."""
    seq = [
        ex
        for n in ["h1_repo_policy", "h2_branch_policy", "h3_commit_root_no_parents"]
        for ex in exchanges(n)
    ]
    t = replay_transport({"exchanges": seq})
    got = _history(t, anchor=A)
    assert isinstance(got, HistoryViolation) and got.code == "anchor_not_reached"
    assert t.unused() == [] and [key(r) for r in t.log] == [key_of(ex) for ex in seq]


def test_live_tree_and_blob_give_activation(builder, check):
    """H3 (корень), H5, H6 без преобразований; H4 — squash-PR, связанный с корнем."""
    b = builder().raw("h1_repo_policy").raw("h2_branch_policy").raw("h3_commit_root_no_parents")
    b.raw(
        "h4_commit_pulls_squash",
        request__path=f"repos/{REPO}/commits/{ROOT}/pulls?per_page=100",
        response__body__0__merge_commit_sha=ROOT,
        response__body__0__base__ref="main",
    )
    b.raw("h5_tree_recursive_policy").raw("h6_blob_policy_approvers")
    spec, t = b.build()
    got = _history(t, anchor=ROOT)
    assert got == (
        Activation(636, ROOT, "2026-10-26T15:48:49Z", {APPROVERS: LIVE_APPROVERS_SHA256}),
    )
    check(spec, t)


def test_activation_live_merge_commit(builder, check):
    """H3 merge-коммита и H4 #657 без преобразований (дерево — шаблон H5 на `TREE_657`)."""
    b = builder(DEVTOOLS).raw("h3_commit_merge_two_parents").raw("h4_commit_pulls_merge_commit")
    b.raw(
        "h5_tree_recursive_policy",
        request__path=f"repos/{DEVTOOLS}/git/trees/{TREE_657}?recursive=1",
        response__body__sha=TREE_657,
    )
    b.raw(
        "h6_blob_policy_approvers",
        request__path=f"repos/{DEVTOOLS}/git/blobs/07b96dccd88cbc3e0582bc1a5f63dcd8f61fe55c",
    )
    spec, t = b.build()
    got = read_activation(t, DEVTOOLS, MERGE_657)
    assert got == Activation(
        657, MERGE_657, "2026-10-30T12:09:17Z", {APPROVERS: LIVE_APPROVERS_SHA256}
    )
    check(spec, t)


def test_activation_live_squash(builder, check):
    """H4 squash #636 без преобразований."""
    b = builder(DEVTOOLS).commit(SQUASH_636, [BASE_657], TREE_657)
    b.raw("h4_commit_pulls_squash")
    b.raw(
        "h5_tree_recursive_policy",
        request__path=f"repos/{DEVTOOLS}/git/trees/{TREE_657}?recursive=1",
        response__body__sha=TREE_657,
    )
    b.raw(
        "h6_blob_policy_approvers",
        request__path=f"repos/{DEVTOOLS}/git/blobs/07b96dccd88cbc3e0582bc1a5f63dcd8f61fe55c",
    )
    spec, t = b.build()
    got = read_activation(t, DEVTOOLS, SQUASH_636)
    assert got == Activation(
        636, SQUASH_636, "2026-10-26T15:48:49Z", {APPROVERS: LIVE_APPROVERS_SHA256}
    )
    check(spec, t)


def test_activation_live_side_commit_has_no_pr(exchanges, key, key_of):
    """H3/H4 бокового коммита #657 без преобразований: PR ассоциирован, но его
    `merge_commit_sha` другой — у коммита нет своего PR-мержа."""
    seq = [
        ex
        for n in ["h3_commit_side_one_parent", "h4_commit_pulls_side_commit"]
        for ex in exchanges(n)
    ]
    t = replay_transport({"exchanges": seq})
    got = read_activation(t, DEVTOOLS, SIDE_657)
    assert isinstance(got, Unavailable) and got.code == "no_pr_for_commit"
    assert t.unused() == [] and [key(r) for r in t.log] == [key_of(ex) for ex in seq]


def test_activation_live_direct_push_has_no_pr(exchanges):
    seq = [
        ex
        for n in ["h3_commit_root_no_parents", "h4_commit_pulls_direct_push"]
        for ex in exchanges(n)
    ]
    t = replay_transport({"exchanges": seq})
    got = read_activation(t, REPO, ROOT)
    assert isinstance(got, Unavailable) and got.code == "no_pr_for_commit"
    assert t.unused() == []


def test_rebase_intermediate_on_live_side_commit_association(builder, check):
    """Гипотеза А живьём (H4 `h4_commit_pulls_side_commit` без преобразований): коммит,
    ассоциированный со смёрженным PR, чей `merge_commit_sha` другой, на first-parent —
    `rebase_intermediate` (merge-коммит #657 с одним родителем = rebase-merge из двух)."""
    b = builder(DEVTOOLS).repository("master")
    b.commit(MERGE_657, [SIDE_657], TREE_657)
    b.raw("h3_commit_side_one_parent")
    b.commit(BASE_657, [R], TREE_657)
    b.pulls(BASE_657, {"number": 600, "base": "master"})
    b.raw("h4_commit_pulls_side_commit")
    spec, t = b.build()
    got = read_policy_history(t, DEVTOOLS, "master", anchor=BASE_657, until=MERGE_657)
    assert isinstance(got, HistoryViolation) and got.code == "rebase_intermediate"
    check(spec, t)


def test_tree_absent_422_is_unavailable(builder, check):
    """Живой 422 на несуществующее дерево (`h5_tree_absent_422` без преобразований) — `http`,
    а не «файлов нет» (строка R0/H5)."""
    b = builder().repository().branch(A).commit(A, [R], ABSENT_TREE).pulls(A, {"number": 1})
    b.raw("h5_tree_absent_422")
    spec, t = b.build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "http"
    check(spec, t)


# --- форма истории ----------------------------------------------------------------------------


def test_merge_squash_and_neutral_chain(builder, check):
    # A (якорь, PR#1) ← B (PR#2 меняет approvers.env, merge-коммит) ← C (PR#3 README, squash)
    # ← D (PR#4 добавляет actors.yaml, squash) = голова
    links = [
        Link(A, R, 1, {APPROVERS: V1}),
        Link(B, A, 2, {APPROVERS: V2}, merged_at="2026-10-12T08:00:00Z"),
        Link(C, B, 3, {APPROVERS: V2}),
        Link(D, C, 4, {APPROVERS: V2, ACTORS: ACTORS_V1}),
    ]
    spec, t = _full(builder(), links).build()
    got = _history(t)
    assert not isinstance(got, (Unavailable, HistoryViolation))
    assert [a.pr for a in got] == [1, 2, 4]
    assert [a.merge_commit for a in got] == [A, B, D]
    assert got[0].files_sha256 == {APPROVERS: _sha256(V1)}
    assert got[1].files_sha256 == {APPROVERS: _sha256(V2)}
    assert got[1].merged_at == "2026-10-12T08:00:00Z"  # из PR, не из коммита
    assert got[2].files_sha256 == {APPROVERS: _sha256(V2), ACTORS: _sha256(ACTORS_V1)}
    check(spec, t)


def test_merge_commit_reads_first_parent_only(builder, check):
    """B — merge-коммит с родителями [A, S]: боковой S не читается."""
    b = builder().repository().branch(B).commit(B, [A, S], _sha("tB")).commit(A, [R], _sha("tA"))
    b.pulls(A, {"number": 1}).pulls(B, {"number": 2})
    b.tree(_sha("tA"), {APPROVERS: V1}).tree(_sha("tB"), {APPROVERS: V1}).blob(V1)
    spec, t = b.build()
    got = _history(t)
    assert [a.pr for a in got] == [1]  # B не меняет файлов
    check(spec, t)


def test_multi_commit_rebase_merge_is_violation(builder, check):
    # PR#2 rebase-merge из X1, X2: merge_commit_sha = X2; гипотеза А:
    # commits/X1/pulls → [PR#2 (merge_commit_sha X2)]
    links = [Link(A, R, 1, {}), Link(X1, A, None, {}), Link(X2, X1, 2, {})]
    b = (
        _chain_prefix(builder(), links)
        .pulls(A, {"number": 1})
        .pulls(X1, {"number": 2, "merge": X2})
    )
    spec, t = b.build()
    got = _history(t)
    assert isinstance(got, HistoryViolation) and got.code == "rebase_intermediate"
    check(spec, t)


def test_multi_commit_rebase_merge_violation_without_association(builder, check):
    # Гипотеза Б (живьём не проверено, спека §3.7 п. 4): commits/X1/pulls → [] — тоже
    # нарушение, но с кодом `commit_without_pr`.
    links = [Link(A, R, 1, {}), Link(X1, A, None, {}), Link(X2, X1, 2, {})]
    b = _chain_prefix(builder(), links).pulls(A, {"number": 1}).pulls(X1)
    spec, t = b.build()
    got = _history(t)
    assert isinstance(got, HistoryViolation) and got.code == "commit_without_pr"
    check(spec, t)


def test_single_commit_rebase_merge_accepted_as_squash(builder, check):
    """Предел обнаружения (спека devtools §6.1 п. 2, приёмка №28): однокоммитный rebase-merge
    неотличим от squash — не доказательство отсутствия rebase-merge."""
    links = [Link(A, R, 1, {APPROVERS: V1}), Link(X1, A, 2, {APPROVERS: V2})]
    spec, t = _full(builder(), links).build()
    assert [a.pr for a in _history(t)] == [1, 2]
    check(spec, t)


def test_direct_push_is_violation(builder, check):
    links = [Link(A, R, 1, {}), Link(B, A, None, {})]
    spec, t = _chain_prefix(builder(), links).pulls(A, {"number": 1}).pulls(B).build()
    got = _history(t)
    assert isinstance(got, HistoryViolation) and got.code == "commit_without_pr"
    assert B in got.reason
    check(spec, t)


def test_unmerged_pr_with_matching_sha_is_not_a_merge(builder, check):
    """§13: PR с `merge_commit_sha == sha`, но `merged_at = null` — не мерж (fail-closed)."""
    links = [Link(A, R, 1, {}), Link(B, A, None, {})]
    b = _chain_prefix(builder(), links).pulls(A, {"number": 1})
    spec, t = b.pulls(B, {"number": 2, "merged_at": None}).build()
    got = _history(t)
    assert isinstance(got, HistoryViolation) and got.code == "commit_without_pr"
    check(spec, t)


def test_anchor_not_reached(builder, check):
    """Корень R прочитан, якорь A не встречен."""
    b = builder().repository().branch(C).commit(C, [B], _sha("tC")).commit(B, [R], _sha("tB"))
    spec, t = b.commit(R, [], _sha("tR")).build()
    got = _history(t)
    assert isinstance(got, HistoryViolation) and got.code == "anchor_not_reached"
    check(spec, t)


def test_pr_base_mismatch(builder, check):
    links = [Link(A, R, 1, {}), Link(B, A, 2, {})]
    b = _chain_prefix(builder(), links).pulls(A, {"number": 1})
    spec, t = b.pulls(B, {"number": 2, "base": "dev"}).build()
    got = _history(t)
    assert isinstance(got, HistoryViolation) and got.code == "pr_base_mismatch"
    check(spec, t)


def test_anchor_pr_base_mismatch(builder, check):
    """Якорь — тоже PR-мерж в ветку (§3.7 п. 5): его PR в другую ветку — нарушение."""
    links = [Link(A, R, 1, {}), Link(B, A, 2, {})]
    spec, t = _chain_prefix(builder(), links).pulls(A, {"number": 1, "base": "dev"}).build()
    got = _history(t)
    assert isinstance(got, HistoryViolation) and got.code == "pr_base_mismatch"
    check(spec, t)


def test_other_merged_pr_into_other_branch_is_not_a_violation(builder, check):
    """База сверяется только у PR, чей `merge_commit_sha` — это звено: ассоциированный
    смёрженный PR в `release` с другим мерж-коммитом нарушением не является."""
    other = {"number": 7, "merge": _sha("release-merge"), "base": "release"}
    b = builder().repository().branch(A).commit(A, [R], _sha("tA"))
    b.pulls(A, {"number": 1}, other).tree(_sha("tA"), {APPROVERS: V1}).blob(V1)
    spec, t = b.build()
    assert _history(t) == (Activation(1, A, "2026-10-01T10:00:00Z", {APPROVERS: _sha256(V1)}),)
    check(spec, t)


def test_anchor_itself_must_be_pr_merge(builder, check):
    links = [Link(A, R, None, {}), Link(B, A, 2, {})]
    spec, t = _chain_prefix(builder(), links).pulls(A).build()
    got = _history(t)
    assert isinstance(got, HistoryViolation) and got.code == "commit_without_pr"
    check(spec, t)


def test_anchor_is_first_even_without_policy_change(builder, check):
    """Якорь — всегда первая активация, даже без файлов политики."""
    spec, t = _full(builder(), [Link(A, R, 1, {}), Link(B, A, 2, {})]).build()
    assert _history(t) == (Activation(1, A, "2026-10-01T10:00:00Z", {}),)
    check(spec, t)


def test_two_merged_prs_claim_the_commit_is_malformed(builder, check):
    links = [Link(A, R, 1, {})]
    spec, t = _chain_prefix(builder(), links).pulls(A, {"number": 1}, {"number": 9}).build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "malformed"
    check(spec, t)


# --- активации --------------------------------------------------------------------------------


def test_revert_to_same_bytes_is_new_activation(builder, check):
    # X → Y → X: три активации, третья с теми же files_sha256, что первая
    links = [
        Link(A, R, 1, {APPROVERS: V1}),
        Link(B, A, 2, {APPROVERS: V2}),
        Link(C, B, 3, {APPROVERS: V1}),
    ]
    spec, t = _full(builder(), links).build()
    got = _history(t)
    assert [a.pr for a in got] == [1, 2, 3]
    assert got[2].files_sha256 == got[0].files_sha256 != got[1].files_sha256
    check(spec, t)


def test_absent_policy_file_omitted(builder, check):
    # полное дерево (truncated=false) без policy/modes.yaml до PR#3 →
    # у активации PR#2 нет ключа "policy/modes.yaml"
    links = [
        Link(A, R, 1, {APPROVERS: V1}),
        Link(B, A, 2, {APPROVERS: V2}),
        Link(C, B, 3, {APPROVERS: V2, MODES: MODES_V1}),
    ]
    spec, t = _full(builder(), links).build()
    got = _history(t)
    assert got[1].files_sha256 == {APPROVERS: _sha256(V2)}
    assert got[2].files_sha256 == {APPROVERS: _sha256(V2), MODES: _sha256(MODES_V1)}
    check(spec, t)


def test_files_parameter_respected(builder, check):
    """Изменение файла вне `files` — не активация; в `files_sha256` только `files`."""
    links = [
        Link(A, R, 1, {APPROVERS: V1, ACTORS: ACTORS_V1}),
        Link(B, A, 2, {APPROVERS: V1, ACTORS: b"agents: []\n"}),
    ]
    spec, t = _full(builder(), links, files=(APPROVERS,)).build()
    assert _history(t, files=[APPROVERS]) == (
        Activation(1, A, "2026-10-01T10:00:00Z", {APPROVERS: _sha256(V1)}),
    )
    check(spec, t)


def test_until_explicit_not_head(builder, check):
    """`until=C`: ветка (H2) и D не читаются."""
    links = [Link(A, R, 1, {APPROVERS: V1}), Link(B, A, 2, {}), Link(C, B, 3, {APPROVERS: V2})]
    spec, t = _full(builder(), links, head=False).build()
    assert [a.pr for a in _history(t, until=C)] == [1, 2, 3]
    check(spec, t)


def test_until_equal_to_anchor(builder, check):
    spec, t = _full(builder(), [Link(A, R, 1, {APPROVERS: V1})], head=False).build()
    assert [a.pr for a in _history(t, until=A)] == [1]
    check(spec, t)


# --- недоступность ----------------------------------------------------------------------------


def test_branch_not_default(builder, check):
    """default_branch "master" ≠ "main" — `Unavailable`, не нарушение; дальше не читается."""
    spec, t = builder().repository("master").build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "branch_not_default"
    check(spec, t)


def test_missing_commit_object_is_unavailable(builder, check):
    b = builder().repository().branch(B).commit(B, [A], _sha("tB"))
    spec, t = b.missing(f"commits/{A}").build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "http"
    check(spec, t)


def test_chain_limit(builder, check, monkeypatch):
    monkeypatch.setattr(history, "MAX_CHAIN", 3)
    b = builder().repository().branch(D)
    spec, t = b.commit(D, [C], "1" * 40).commit(C, [B], "2" * 40).commit(B, [A], "3" * 40).build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "chain_limit"
    check(spec, t)


def test_chain_at_limit_is_read(builder, check, monkeypatch):
    monkeypatch.setattr(history, "MAX_CHAIN", 3)
    links = [Link(A, R, 1, {}), Link(B, A, 2, {}), Link(C, B, 3, {})]
    spec, t = _full(builder(), links).build()
    assert [a.pr for a in _history(t)] == [1]
    check(spec, t)


def test_default_chain_limit_is_1000():
    assert history.MAX_CHAIN == 1000


def test_tree_404_after_commit_read_is_unavailable(builder, check):
    # commits/{sha} 200, git/trees/{tree} 404 → Unavailable("http"), не «файлов нет»
    b = builder().repository().branch(A).commit(A, [R], _sha("tA")).pulls(A, {"number": 1})
    spec, t = b.missing(f"git/trees/{_sha('tA')}?recursive=1").build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "http"
    check(spec, t)


def test_tree_truncated_is_unavailable(builder, check):
    b = builder().repository().branch(A).commit(A, [R], _sha("tA")).pulls(A, {"number": 1})
    spec, t = b.tree(_sha("tA"), {APPROVERS: V1}, truncated=True).build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "tree_truncated"
    check(spec, t)


@pytest.mark.parametrize("truncated", [None, "false", 0])
def test_tree_truncated_not_bool_is_malformed(builder, check, truncated):
    """Полнота дерева не доказана — отсутствие файла не устанавливается (`malformed`)."""
    b = builder().repository().branch(A).commit(A, [R], _sha("tA")).pulls(A, {"number": 1})
    spec, t = b.tree(_sha("tA"), {APPROVERS: V1}, truncated=truncated).build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "malformed"
    check(spec, t)


def test_tree_without_truncated_is_malformed(builder, check):
    b = builder().repository().branch(A).commit(A, [R], _sha("tA")).pulls(A, {"number": 1})
    b.tree(_sha("tA"), {APPROVERS: V1})
    b.transforms.append({"op": "drop_field", "path": [4, "response", "body", "truncated"]})
    spec, t = b.build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "malformed"
    check(spec, t)


def test_tree_duplicate_policy_entry_is_malformed(builder, check):
    b = builder().repository().branch(A).commit(A, [R], _sha("tA")).pulls(A, {"number": 1})
    spec, t = b.tree(_sha("tA"), {APPROVERS: V1}, duplicate=APPROVERS).build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "malformed"
    check(spec, t)


def test_tree_answer_for_other_tree_is_malformed(builder, check):
    b = builder().repository().branch(A).commit(A, [R], _sha("tA")).pulls(A, {"number": 1})
    b.tree(_sha("tA"), {APPROVERS: V1})
    b.transforms.append(
        {"op": "set_field", "path": [4, "response", "body", "sha"], "value": _sha("tX")}
    )
    spec, t = b.build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "malformed"
    check(spec, t)


def test_blob_content_not_matching_oid_is_malformed(builder, check):
    """Байты блоба обязаны давать oid, названный деревом."""
    b = builder().repository().branch(A).commit(A, [R], _sha("tA")).pulls(A, {"number": 1})
    content = base64.b64encode(V2).decode()
    spec, t = b.tree(_sha("tA"), {APPROVERS: V1}).blob(V1, response__body__content=content).build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "malformed"
    check(spec, t)


def test_policy_path_not_blob(builder, check):
    b = builder().repository().branch(A).commit(A, [R], _sha("tA")).pulls(A, {"number": 1})
    spec, t = b.tree(_sha("tA"), {ACTORS: b""}, kinds={ACTORS: "tree"}).build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "not_a_blob"
    check(spec, t)


def test_blob_encoding_not_base64_is_malformed(builder, check):
    b = builder().repository().branch(A).commit(A, [R], _sha("tA")).pulls(A, {"number": 1})
    spec, t = b.tree(_sha("tA"), {APPROVERS: V1}).blob(V1, response__body__encoding="utf-8").build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "malformed"
    check(spec, t)


def test_blob_content_not_base64_is_malformed(builder, check):
    b = builder().repository().branch(A).commit(A, [R], _sha("tA")).pulls(A, {"number": 1})
    spec, t = b.tree(_sha("tA"), {APPROVERS: V1}).blob(V1, response__body__content="@@@").build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "malformed"
    check(spec, t)


def test_commit_answer_for_other_sha_is_malformed(builder, check):
    """Ответ H3 на `commits/A` несёт другой `sha` — не тот объект."""
    b = builder().repository().branch(A)
    b.raw("h3_commit_side_one_parent", request__path=f"repos/{REPO}/commits/{A}")
    spec, t = b.build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "malformed"
    check(spec, t)


@pytest.mark.parametrize(
    "parents", [[{"sha": R}, {"sha": S}, {"sha": C}], "x", [{"sha": R.upper()}], ["x"]]
)
def test_commit_parents_shape_is_checked(builder, check, parents):
    """`parents` — список из 0–2 объектов с 40-hex `sha` (строка H3); иное — `malformed`."""
    b = builder().repository().branch(B)
    b.raw(
        "h3_commit_side_one_parent",
        request__path=f"repos/{REPO}/commits/{B}",
        response__body__sha=B,
        response__body__parents=parents,
    )
    spec, t = b.build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "malformed"
    check(spec, t)


def test_head_not_hex_is_malformed(builder, check):
    spec, t = builder().repository().branch("main").build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "malformed"
    check(spec, t)


def test_pr_merged_at_bad_timestamp(builder, check):
    b = builder().repository().branch(A).commit(A, [R], _sha("tA"))
    spec, t = b.pulls(A, {"number": 1, "merged_at": "2026-10-01T10:00:00.5Z"}).build()
    got = _history(t)
    assert isinstance(got, Unavailable) and got.code == "bad_timestamp"
    check(spec, t)


# --- read_activation --------------------------------------------------------------------------


def test_activation_ok(builder, check):
    b = (
        builder()
        .commit(B, [A], _sha("tB"))
        .pulls(B, {"number": 2, "merged_at": "2026-10-12T08:00:00Z"})
    )
    spec, t = b.tree(_sha("tB"), {APPROVERS: V2, MODES: MODES_V1}).blob(V2).blob(MODES_V1).build()
    assert read_activation(t, REPO, B) == Activation(
        2, B, "2026-10-12T08:00:00Z", {APPROVERS: _sha256(V2), MODES: _sha256(MODES_V1)}
    )
    check(spec, t)


def test_activation_no_pr(builder, check):
    spec, t = builder().commit(B, [A], _sha("tB")).pulls(B, {"number": 2, "merge": X2}).build()
    got = read_activation(t, REPO, B)
    assert isinstance(got, Unavailable) and got.code == "no_pr_for_commit"
    check(spec, t)


def test_activation_tree_404_is_unavailable(builder, check):
    b = builder().commit(B, [A], _sha("tB")).pulls(B, {"number": 2})
    spec, t = b.missing(f"git/trees/{_sha('tB')}?recursive=1").build()
    got = read_activation(t, REPO, B)
    assert isinstance(got, Unavailable) and got.code == "http"
    check(spec, t)


def test_activation_missing_commit_is_unavailable(builder, check):
    spec, t = builder().missing(f"commits/{B}").build()
    got = read_activation(t, REPO, B)
    assert isinstance(got, Unavailable) and got.code == "http"
    check(spec, t)


# --- ошибки вызывающего -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "files",
    [
        (),
        [],
        ("/abs",),
        ("../x",),
        ("",),
        ("policy/../x",),
        "policy/approvers.env",
        ("policy//approvers.env",),
        ("./policy/approvers.env",),
        ("policy/",),
    ],
)
def test_bad_files_raise(files):
    t = replay_transport({"exchanges": []})
    with pytest.raises(ValueError):
        _history(t, files=files)
    with pytest.raises(ValueError):
        read_activation(t, REPO, A, files=files)
    assert t.log == []


@pytest.mark.parametrize("sha", ["", "main", A.upper(), A[:39], A + "0", "g" * 40])
def test_anchor_not_hex_raises(sha):
    t = replay_transport({"exchanges": []})
    with pytest.raises(ValueError):
        _history(t, anchor=sha)
    with pytest.raises(ValueError):
        _history(t, until=sha)
    with pytest.raises(ValueError):
        read_activation(t, REPO, sha)
    assert t.log == []


def test_repo_not_owner_name_raises():
    with pytest.raises(ValueError):
        read_policy_history(replay_transport({"exchanges": []}), "x", "main", anchor=A, until=None)


def test_policy_files_and_exports():
    assert POLICY_FILES == ("policy/approvers.env", "policy/modes.yaml", "policy/actors.yaml")
    assert v3.POLICY_FILES is POLICY_FILES
    assert v3.read_policy_history is read_policy_history
    assert v3.read_activation is read_activation
    assert {"POLICY_FILES", "read_policy_history", "read_activation"} <= set(v3.__all__)
