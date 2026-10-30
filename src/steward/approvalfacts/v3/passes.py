"""Проход по ревью и таймлайну, проекция на T, `read_open_review` (спека steward §3.4).

Один проход — все страницы `StewardV3Reviews` (строка E3 §3.8), затем все страницы
`StewardV3Timeline` (E4). Чтение только переводит записи в факты (`databaseId` → десятичная
строка, метки → канон) и отвергает то, чего модель не держит (форма записи, тип актора
отзыва); целостность фактов решает общая `validate_review_facts`.
"""

from dataclasses import dataclass, fields
from typing import Any

from steward.approvalfacts.v3._github import _Fail, graphql_pages
from steward.approvalfacts.v3.model import (
    BadTimestamp,
    Dismissal,
    Review,
    ReviewFacts,
    Unavailable,
    canon_ts,
    unavailable,
)
from steward.approvalfacts.v3.queries import Q_REVIEWS, Q_TIMELINE
from steward.approvalfacts.v3.transport import Transport
from steward.approvalfacts.v3.validate import actor_resolved, validate_review_facts

_REVIEWS = ("repository", "pullRequest", "reviews")
_TIMELINE = ("repository", "pullRequest", "timelineItems")
_DISMISSED_EVENT = "ReviewDismissedEvent"
_MERGED_EVENT = "MergedEvent"


@dataclass(frozen=True)
class Pass:
    """Один полный проход: поля PR, ревью, отзывы и коммиты `MergedEvent` таймлайна."""

    head_sha: str
    base_ref: str
    pr_state: str
    reviews: tuple[Review, ...]
    dismissals: tuple[Dismissal, ...]
    merged_commits: tuple[str, ...]


def read_open_review(transport: Transport, repo: str, pr: int) -> ReviewFacts | Unavailable:
    """Факты ревью открытого PR: два полных прохода §3.4, устойчивость — их полное равенство.

    Доставка и пути не читаются; `pr_state` возвращается как есть. Проходы различаются
    (в т. ч. `headRefOid`) — `unstable`; факты не целы — отказ `validate_review_facts`.
    `repo` не `owner/name` — `ValueError`.
    """
    owner, name = split_repo(repo)
    try:
        first = read_pass(transport, owner, name, pr)
        second = read_pass(transport, owner, name, pr)
    except _Fail as fail:
        return fail.unavailable()
    if first != second:
        return unavailable("unstable", f"passes differ in {_differing(first, second)}")
    facts = pass_facts(second, repo, pr)
    broken = validate_review_facts(facts)
    return facts if broken is None else broken


def split_repo(repo: str) -> tuple[str, str]:
    """`owner/name` → `(owner, name)`; не ровно один `/` или пустая часть — `ValueError`."""
    owner, sep, name = repo.partition("/")
    if not sep or not owner or not name or "/" in name:
        raise ValueError(f"repo is not owner/name: {repo!r}")
    return owner, name


def pass_facts(p: Pass, repository: str, pr: int) -> ReviewFacts:
    """`ReviewFacts` прохода (без проверки — её делает вызывающий)."""
    return ReviewFacts(repository, pr, p.head_sha, p.base_ref, p.pr_state, p.reviews, p.dismissals)


def read_pass(t: Transport, owner: str, name: str, pr: int) -> Pass:
    """Один проход §3.4; отказ — `_Fail`.

    Страница без нужной формы — `malformed`; поля PR различаются между страницами ревью —
    `unstable`; метка не в каноне — `bad_timestamp`; актор отзыва не установлен (§3.4.1;
    тип актора в `Dismissal` не хранится) — `actor_unresolved`.
    """
    variables = {"owner": owner, "name": name, "number": pr}
    review_pages = graphql_pages(t, "StewardV3Reviews", Q_REVIEWS, variables, connection=_REVIEWS)
    timeline_pages = graphql_pages(
        t, "StewardV3Timeline", Q_TIMELINE, variables, connection=_TIMELINE
    )
    head_sha, base_ref, pr_state = _pr_fields(review_pages)
    items = _nodes(timeline_pages, _TIMELINE)
    for item in items:
        if item.get("__typename") not in (_DISMISSED_EVENT, _MERGED_EVENT):
            raise _Fail("malformed", f"timeline item {item.get('__typename')!r}")
    return Pass(
        head_sha=head_sha,
        base_ref=base_ref,
        pr_state=pr_state,
        reviews=tuple(_review(node) for node in _nodes(review_pages, _REVIEWS)),
        dismissals=tuple(_dismissal(i) for i in items if i["__typename"] == _DISMISSED_EVENT),
        merged_commits=tuple(_merged_commit(i) for i in items if i["__typename"] == _MERGED_EVENT),
    )


def projection_at(p: Pass, t: str) -> frozenset[tuple]:
    """Проекция прохода на T (спека steward §3.4.1, «Устойчивость на T»).

    Ревью с `submitted_at ≤ t` (и без метки): `("review", id, login, type_hint, commit_id,
    submitted_at, state_T)`, `state_T` — `previous_state` отзыва после `t`, иначе `state`;
    отзывы с `created_at ≤ t`: `("dismissal", id, actor, created_at, previous_state)`.
    `t` не в каноне — `ValueError`.
    """
    if canon_ts(t) != t:
        raise ValueError(f"t is not canonical: {t!r}")
    later = {d.review_id: d.previous_state for d in p.dismissals if d.created_at > t}
    reviews = {
        ("review", r.review_id, r.login, r.type_hint, r.commit_id, r.submitted_at)
        + (later.get(r.review_id, r.state),)
        for r in p.reviews
        if r.submitted_at is None or r.submitted_at <= t
    }
    dismissals = {
        ("dismissal", d.review_id, d.actor, d.created_at, d.previous_state)
        for d in p.dismissals
        if d.created_at <= t
    }
    return frozenset(reviews | dismissals)


def _differing(a: Pass, b: Pass) -> str:
    return ", ".join(f.name for f in fields(Pass) if getattr(a, f.name) != getattr(b, f.name))


def _connection(page: dict, connection: tuple[str, ...]) -> dict:
    """Соединение страницы; путь и объект уже проверены `graphql_pages`."""
    node = page
    for step in connection:
        node = node[step]
    return node


def _nodes(pages: list[dict], connection: tuple[str, ...]) -> list[dict]:
    """Узлы всех страниц по порядку; `nodes` не список объектов — `malformed`."""
    out: list[dict] = []
    for page in pages:
        nodes = _connection(page, connection).get("nodes")
        if not isinstance(nodes, list) or not all(isinstance(n, dict) for n in nodes):
            raise _Fail("malformed", f"{'.'.join(connection)}.nodes is not a list of objects")
        out.extend(nodes)
    return out


def _pr_fields(pages: list[dict]) -> tuple[str, str, str]:
    """`(headRefOid, baseRefName, state)`: непустые строки, одинаковые на всех страницах."""
    seen = {_pr_triple(page["repository"]["pullRequest"]) for page in pages}
    if len(seen) != 1:
        raise _Fail("unstable", "pull request fields changed between review pages")
    return seen.pop()


def _pr_triple(pull: dict) -> tuple[str, str, str]:
    triple = (pull.get("headRefOid"), pull.get("baseRefName"), pull.get("state"))
    head, base, state = triple
    if not (_text(head) and _text(base) and _text(state)):
        raise _Fail("malformed", f"pull request fields {triple!r}")
    return head, base, state


def _review(node: dict) -> Review:
    """Ревью как прочитано; целостность (актор, состояние, коммит) — `validate`."""
    author, commit = node.get("author"), node.get("commit")
    return Review(
        review_id=_database_id(node.get("databaseId"), "review"),
        login=_get(author, "login"),
        type_hint=_get(author, "__typename"),
        state=node.get("state"),
        commit_id=_get(commit, "oid"),
        submitted_at=None if node.get("submittedAt") is None else _stamp(node["submittedAt"]),
    )


def _dismissal(node: dict) -> Dismissal:
    """Отзыв: `review` null — `malformed`; актор не установлен — `actor_unresolved` до
    сборки `Dismissal` (тип актора модель не хранит, `validate` его не увидит)."""
    review = node.get("review")
    if not isinstance(review, dict):
        raise _Fail("malformed", "ReviewDismissedEvent without review")
    review_id = _database_id(review.get("databaseId"), "dismissal")
    actor = node.get("actor")
    login = _get(actor, "login")
    if not actor_resolved(login, _get(actor, "__typename")):
        raise _Fail("actor_unresolved", f"dismissal {review_id}")
    return Dismissal(
        review_id=review_id,
        actor=login,
        created_at=_stamp(node.get("createdAt")),
        previous_state=node.get("previousReviewState"),
    )


def _merged_commit(node: dict) -> str:
    oid = _get(node.get("commit"), "oid")
    if not _text(oid):
        raise _Fail("malformed", "MergedEvent without commit oid")
    _stamp(node.get("createdAt"))
    return oid


def _database_id(raw: object, where: str) -> str:
    """`databaseId` — целое JSON (не bool) → десятичная строка; иначе `malformed`."""
    if type(raw) is not int:
        raise _Fail("malformed", f"{where} databaseId {raw!r} is not an integer")
    return str(raw)


def _stamp(raw: object) -> str:
    try:
        return canon_ts(raw)
    except BadTimestamp as exc:
        raise _Fail("bad_timestamp", str(exc)) from exc


def _get(obj: object, key: str) -> Any:
    """Поле объекта или `None`, если объекта нет (null, не объект)."""
    return obj.get(key) if isinstance(obj, dict) else None


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value)
