"""`read_act_facts`: факты акта approve — доставка первой, два прохода, срез на T (§3.3).

Порядок обменов фиксирован (спека devtools §5.5, снимки `pr657_act_sequence`,
`pr636_act_sequence`): E2, E1 → E5 → E6 → E3, E4 (проход 1) → E3, E4 (проход 2) → E2, E1
(повторное чтение доставки). Несмёрженный PR — только E2, E1.
"""

from collections.abc import Sequence
from dataclasses import fields, replace

from steward.approvalfacts.v3._github import _Fail
from steward.approvalfacts.v3.delivery import (
    NOT_MERGED,
    Merge,
    check_paths,
    delivery_of,
    read_in_base,
    read_merge,
    read_paths,
)
from steward.approvalfacts.v3.model import Delivery, ReviewFacts, Unavailable
from steward.approvalfacts.v3.passes import (
    Pass,
    pass_facts,
    projection_at,
    read_pass,
    split_repo,
)
from steward.approvalfacts.v3.transport import Transport
from steward.approvalfacts.v3.validate import validate_review_facts


def read_act_facts(
    transport: Transport, repo: str, pr: int, *, paths: Sequence[str]
) -> tuple[Delivery, ReviewFacts | None] | Unavailable:
    """Доставка и факты ревью PR на момент мержа `T = merged_at` (спека steward §3.3).

    Несмёрженный PR — `(NOT_MERGED, None)`, ревью не читаются. Смёрженный — `(Delivery,
    ReviewFacts)`: факты ревью — второго прохода с `head_sha = head_at_merge`. Отказ —
    `Unavailable`: доставка или проходы расходятся с повторным чтением/между собой на T —
    `unstable`; таймлайн не несёт ровно одного `MergedEvent` с `commit.oid` мерж-коммита
    (время события не сверяется), или голова/база прохода не та, что у доставки, —
    `delivery_mismatch`; факты ревью не целы — код `validate_review_facts`. `repo` не
    `owner/name` или недопустимый путь — `ValueError` до первого запроса.
    """
    owner, name = split_repo(repo)
    checked = check_paths(paths)
    try:
        return _read(transport, owner, name, repo, pr, checked)
    except _Fail as fail:
        return fail.unavailable()


def _read(
    t: Transport, owner: str, name: str, repo: str, pr: int, paths: tuple[str, ...]
) -> tuple[Delivery, ReviewFacts | None] | Unavailable:
    merge = read_merge(t, owner, name, pr)
    if merge is None:
        return NOT_MERGED, None
    in_base = read_in_base(t, owner, name, merge)
    blobs = read_paths(t, owner, name, merge.merge_commit, merge.head, paths)
    first = read_pass(t, owner, name, pr)
    second = read_pass(t, owner, name, pr)
    again = read_merge(t, owner, name, pr)
    if again != merge:
        raise _Fail("unstable", f"delivery re-read differs in {_differing(merge, again)}")
    for p in (first, second):
        _check_pass_delivery(p, merge)
    if projection_at(first, merge.merged_at) != projection_at(second, merge.merged_at):
        raise _Fail("unstable", f"review passes differ at T={merge.merged_at}")
    for p in (first, second):
        broken = validate_review_facts(pass_facts(p, repo, pr))
        if broken is not None:
            return broken
    facts = replace(pass_facts(second, repo, pr), head_sha=merge.head)
    return delivery_of(merge, in_base, blobs), facts


def _check_pass_delivery(p: Pass, merge: Merge) -> None:
    """Проход согласован с доставкой: ровно один `MergedEvent`, его коммит — мерж-коммит
    (нормативна только сверка SHA, §3.3); голова и база PR — те же, что у доставки."""
    if p.merged_commits != (merge.merge_commit,):
        raise _Fail(
            "delivery_mismatch",
            f"timeline MergedEvent commits {list(p.merged_commits)} != [{merge.merge_commit}]",
        )
    if (p.head_sha, p.base_ref) != (merge.head, merge.base_ref):
        raise _Fail("delivery_mismatch", "review pass head or base differs from delivery")


def _differing(a: Merge, b: Merge | None) -> str:
    if b is None:
        return "merged"
    return ", ".join(f.name for f in fields(Merge) if getattr(a, f.name) != getattr(b, f.name))
