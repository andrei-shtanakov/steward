"""Продюсер: определённый отрицательный ответ — запись, всё остальное — отказ.

Тесты подменяют `_gh`, поэтому сети нет ни в одном сценарии.
"""

import json
from collections.abc import Iterator

import pytest

from steward.approvalfacts import producer
from steward.approvalfacts.model import RequestId, Result
from steward.approvalfacts.producer import MechanicalFailure, classify_results, materialize, resolve
from steward.gatecheck.approval import ApprovalPolicy

SHA = "221457933968be9e95acd51d548e080f739c794c"
OWNER, NAME = "andrei-shtanakov", "steward"


def _fake_gh(responses: list[tuple[int, object]]):
    """Подмена `_gh`, отдающая (code, stdout, stderr).

    Соглашение фикстур: словарь — тело на stdout; строка — вывод при коде 0 и
    диагностика при ненулевом, как у настоящего `gh`; кортеж `(stdout, stderr)`
    задаёт оба потока явно — реальная форма GraphQL-ответа с ненулевым кодом.

    Копия того же помощника живёт в `tests/riskclassify/test_approval_facts_cli.py`;
    они обязаны меняться вместе.
    """
    it: Iterator[tuple[int, object]] = iter(responses)

    def fake(args: list[str]) -> tuple[int, str, str]:
        code, payload = next(it)
        if isinstance(payload, str):
            # Как настоящий `gh`: при успехе строка — это вывод, при отказе —
            # диагностика. Ставить её всегда в stderr значило бы подменить
            # смысл теста «stdout не JSON» на «stdout пуст», и подмена была бы
            # незаметной: оба случая дают `MechanicalFailure`.
            return (code, payload, "") if code == 0 else (code, "", payload)
        if isinstance(payload, tuple):
            body, err = payload
            return code, body if isinstance(body, str) else json.dumps(body), err
        return code, json.dumps(payload), ""

    return fake


def test_merged_pr_yields_merged_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        producer,
        "_gh",
        _fake_gh(
            [
                (
                    0,
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "mergeCommit": {"oid": SHA},
                                    "mergedBy": {"login": "merge-broker", "__typename": "Bot"},
                                }
                            }
                        }
                    },
                )
            ]
        ),
    )
    result = resolve(OWNER, NAME, RequestId("pr", 42))
    assert (result.state, result.merge_sha, result.identity, result.type_hint) == (
        "merged",
        SHA,
        "github:merge-broker",
        "Bot",
    )


def test_unmerged_pr_is_a_record_not_an_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    """Определённый отрицательный ответ не уничтожает батч."""
    monkeypatch.setattr(
        producer,
        "_gh",
        _fake_gh(
            [
                (
                    0,
                    {
                        "data": {
                            "repository": {"pullRequest": {"mergeCommit": None, "mergedBy": None}}
                        }
                    },
                )
            ]
        ),
    )
    result = resolve(OWNER, NAME, RequestId("pr", 42))
    assert result.state == "not_merged"
    assert result.merge_sha is None


def test_pull_request_null_with_no_errors_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pins the reader's contract for ONE payload shape — not live GitHub
    behavior. Codex gate round 2 on PR #86: this test used to be named
    `test_absent_pr_is_not_found`, which reads as "a nonexistent PR gives
    `not_found`" — but live `gh api graphql` never sends this shape for a
    nonexistent PR (see `test_nonexistent_pr_on_live_github_is_mechanical_failure`
    right below, and the live acceptance finding in
    `docs/evidence/2026-08-21-approval-facts-v2-migration/manifest.md`, шаг 3).
    This test only proves: IF a well-formed, `errors`-free response ever
    carries `pullRequest: null`, the reader treats that as a definite
    negative. Kept because §4.2/§9.1 promise `not_found` is a legal terminal
    state for `request.kind: pr`, and this is the shape that would produce
    it — it is just not the shape GitHub sends today
    (`approval-facts-not-found-vs-mechanical-failure` in `TODO.md`)."""
    monkeypatch.setattr(
        producer, "_gh", _fake_gh([(0, {"data": {"repository": {"pullRequest": None}}})])
    )
    assert resolve(OWNER, NAME, RequestId("pr", 42)).state == "not_found"


#: Реальная форма ответа `gh api graphql` на несуществующий номер PR, снятая
#: живой приёмкой (`docs/evidence/2026-08-21-approval-facts-v2-migration/manifest.md`,
#: шаг 3): валидное тело с `pullRequest: null` И непустой top-level `errors`,
#: из-за которого `gh` завершается кодом 1.
_LIVE_ABSENT_PR = (
    {
        "data": {"repository": {"pullRequest": None}},
        "errors": [
            {
                "type": "NOT_FOUND",
                "path": ["repository", "pullRequest"],
                "message": "Could not resolve to a PullRequest with the number of 999999.",
            }
        ],
    },
    "gh: Could not resolve to a PullRequest with the number of 999999.",
)


def test_nonexistent_pr_on_live_github_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Перевёрнутый характеризационный тест: раньше он фиксировал дефект.

    Живая приёмка 2026-08-21 показала, что `gh api graphql` на несуществующий
    номер PR завершается кодом 1, но его stdout содержит валидный JSON с
    `data.repository.pullRequest: null` рядом с `errors: [{type: NOT_FOUND}]`.
    Прежний `_gh()` отдавал stdout только при нулевом коде, поэтому тело
    терялось, и `not_found` не мог стать записью против живого GitHub —
    хотя §4.2/§9.1 обещают его законным терминальным состоянием.

    Тест был написан как характеризационный — «меняется намеренно, когда
    владелец починит». Вот эта смена."""
    monkeypatch.setattr(producer, "_gh", _fake_gh([(1, _LIVE_ABSENT_PR)]))

    assert resolve(OWNER, NAME, RequestId("pr", 999999)).state == "not_found"


def test_not_found_mixed_with_another_error_stays_mechanical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Терпимость распространяется только на «объекта нет», и ни на что рядом.

    Ответ, где вместе с `NOT_FOUND` приехал, например, rate limit, истолковать
    как авторитетное отсутствие нельзя: часть данных не получена по другой
    причине, и доложить это отсутствием значит доложить недоступность как факт
    об акторе."""
    body, err = _LIVE_ABSENT_PR
    mixed = {**body, "errors": [*body["errors"], {"type": "RATE_LIMITED", "message": "slow down"}]}
    monkeypatch.setattr(producer, "_gh", _fake_gh([(1, (mixed, err))]))

    with pytest.raises(MechanicalFailure, match="GraphQL errors"):
        resolve(OWNER, NAME, RequestId("pr", 999999))


def test_not_found_without_data_is_mechanical(monkeypatch: pytest.MonkeyPatch) -> None:
    """`NOT_FOUND` при `data: null` — читать нечем, значит это не ответ."""
    body, err = _LIVE_ABSENT_PR
    monkeypatch.setattr(producer, "_gh", _fake_gh([(1, ({**body, "data": None}, err))]))

    with pytest.raises(MechanicalFailure, match="GraphQL errors"):
        resolve(OWNER, NAME, RequestId("pr", 999999))


def test_nonzero_exit_with_clean_json_stays_mechanical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Тело без ошибок при недовольном `gh` — истолковать нечего.

    Терпимость к ненулевому коду держится ровно на объяснении: ошибки есть, и
    все они про отсутствие. Без такого объяснения ненулевой код остаётся
    сбоем."""
    monkeypatch.setattr(
        producer,
        "_gh",
        _fake_gh([(1, ({"data": {"repository": {"pullRequest": None}}}, "boom"))]),
    )

    with pytest.raises(MechanicalFailure, match="завершился с кодом"):
        resolve(OWNER, NAME, RequestId("pr", 999999))


def test_absent_repository_is_still_unavailability_not_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Решение, которое нельзя сломать заодно.

    `NOT_FOUND` у самого репозитория приходит с `repository: null`. Теперь
    такой ответ доходит до `_repository` — и тот по-прежнему отказывается
    читать его как «репозитория нет»: auth и visibility выглядят точно так же,
    и доложить недоступность отсутствием — то самое, ради чего это правило
    и заводили."""
    monkeypatch.setattr(
        producer,
        "_gh",
        _fake_gh(
            [
                (
                    1,
                    (
                        {
                            "data": {"repository": None},
                            "errors": [{"type": "NOT_FOUND", "path": ["repository"]}],
                        },
                        "gh: Could not resolve to a Repository",
                    ),
                )
            ]
        ),
    )

    with pytest.raises(MechanicalFailure, match="недоступность"):
        resolve(OWNER, NAME, RequestId("pr", 42))


def test_missing_pull_request_key_is_mechanical_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """`pullRequest: null` (key present) is a definite negative — `not_found`.
    The key simply MISSING (`{"repository": {}}`) is a different thing: a
    malformed/truncated response, not a proof the PR doesn't exist. Codex gate
    round 2 on PR #86: `.get("pullRequest")` conflated the two because both
    return `None`."""
    monkeypatch.setattr(producer, "_gh", _fake_gh([(0, {"data": {"repository": {}}})]))
    with pytest.raises(MechanicalFailure, match="repository.pullRequest"):
        resolve(OWNER, NAME, RequestId("pr", 42))


def test_pull_request_without_merge_commit_key_is_mechanical_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`mergeCommit: null` (key present) is a definite negative — `not_merged`.
    The key simply MISSING (`{"pullRequest": {}}`) is a malformed response,
    same class as the sibling tests above. Codex gate round 6 on PR #86's
    exact repro: `{"data":{"repository":{"pullRequest":{}}}}` used to fall
    through to `not_merged` via `pull_request.get("mergeCommit")`."""
    monkeypatch.setattr(
        producer, "_gh", _fake_gh([(0, {"data": {"repository": {"pullRequest": {}}}})])
    )
    with pytest.raises(MechanicalFailure, match="pullRequest.mergeCommit"):
        resolve(OWNER, NAME, RequestId("pr", 42))


def test_null_repository_is_mechanical_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """`repository: null` может быть auth/visibility failure и НЕ является
    доказанным отсутствием — иначе отказ доступа выглядел бы как факт."""
    monkeypatch.setattr(producer, "_gh", _fake_gh([(0, {"data": {"repository": None}})]))
    with pytest.raises(MechanicalFailure, match="repository"):
        resolve(OWNER, NAME, RequestId("pr", 42))


def test_graphql_errors_are_mechanical_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Непустой `errors` — недоступность результата, даже при exit 0."""
    monkeypatch.setattr(
        producer,
        "_gh",
        _fake_gh(
            [
                (
                    0,
                    {
                        "data": {"repository": {"pullRequest": None}},
                        "errors": [{"message": "RATE_LIMITED"}],
                    },
                )
            ]
        ),
    )
    with pytest.raises(MechanicalFailure, match="errors"):
        resolve(OWNER, NAME, RequestId("pr", 42))


def test_gh_nonzero_exit_is_mechanical_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(producer, "_gh", _fake_gh([(1, "gh: not authenticated")]))
    with pytest.raises(MechanicalFailure):
        resolve(OWNER, NAME, RequestId("pr", 42))


def test_gh_response_not_json_is_mechanical_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """`gh` exits 0 but stdout doesn't parse as JSON at all — still a mechanical
    failure, not a crash and not a silently-empty result."""
    monkeypatch.setattr(producer, "_gh", _fake_gh([(0, "not json output")]))
    with pytest.raises(MechanicalFailure, match="не JSON"):
        resolve(OWNER, NAME, RequestId("pr", 42))


def test_gh_response_not_object_is_mechanical_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """`gh` returns syntactically valid JSON that isn't a top-level object
    (e.g. a bare list) — parses fine, but isn't a shape we can read `data`
    from, so it's a mechanical failure rather than an `AttributeError`."""
    monkeypatch.setattr(producer, "_gh", _fake_gh([(0, [1, 2, 3])]))
    with pytest.raises(MechanicalFailure, match="не объект"):
        resolve(OWNER, NAME, RequestId("pr", 42))


def test_sha_pagination_is_exhaustive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Совпадение на ВТОРОЙ странице обязано быть найдено: иначе продюсер
    выдал бы ложный no_matching_pr."""
    page1 = {
        "data": {
            "repository": {
                "object": {
                    "associatedPullRequests": {
                        "nodes": [{"mergeCommit": {"oid": "0" * 40}, "mergedBy": None}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "CUR"},
                    }
                }
            }
        }
    }
    page2 = {
        "data": {
            "repository": {
                "object": {
                    "associatedPullRequests": {
                        "nodes": [
                            {
                                "mergeCommit": {"oid": SHA},
                                "mergedBy": {"login": "andrei-shtanakov", "__typename": "User"},
                            }
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }
    }
    monkeypatch.setattr(producer, "_gh", _fake_gh([(0, page1), (0, page2)]))
    result = resolve(OWNER, NAME, RequestId("merge_sha", SHA))
    assert result.state == "merged"
    assert result.identity == "github:andrei-shtanakov"


def _no_match_page(*, has_next: bool, cursor: str | None) -> dict:
    return {
        "data": {
            "repository": {
                "object": {
                    "associatedPullRequests": {
                        "nodes": [],
                        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                    }
                }
            }
        }
    }


def test_pagination_limit_exceeded_is_mechanical_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_MAX_PAGES` consecutive `hasNextPage: true` pages without a match is a
    mechanical failure — never a false `no_matching_pr`. Any cap surfaced as a
    negative fact would reproduce exactly the truncated-traversal defect this
    module exists to remove."""
    pages = [
        (0, _no_match_page(has_next=True, cursor=f"CUR{i}")) for i in range(producer._MAX_PAGES)
    ]
    monkeypatch.setattr(producer, "_gh", _fake_gh(pages))
    with pytest.raises(MechanicalFailure, match="обход не завершён"):
        resolve(OWNER, NAME, RequestId("merge_sha", SHA))


def test_hasnextpage_without_cursor_is_mechanical_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """`hasNextPage: true` paired with a missing/null `endCursor` cannot be
    followed — continuing would silently re-request the same page forever.
    Two pages are queued: if the guard is removed, `after=None` is dropped by
    `_graphql`'s arg builder, the *same* query re-fires, and the second queued
    page (`hasNextPage: false`) would be consumed and reported as
    `no_matching_pr` — a false negative, not a crash. The guard must pre-empt
    that with `MechanicalFailure` before the second page is ever requested."""
    page1 = _no_match_page(has_next=True, cursor=None)
    page2 = _no_match_page(has_next=False, cursor=None)
    monkeypatch.setattr(producer, "_gh", _fake_gh([(0, page1), (0, page2)]))
    with pytest.raises(MechanicalFailure, match="без endCursor"):
        resolve(OWNER, NAME, RequestId("merge_sha", SHA))


def test_no_match_after_full_traversal_is_no_matching_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        producer,
        "_gh",
        _fake_gh(
            [
                (
                    0,
                    {
                        "data": {
                            "repository": {
                                "object": {
                                    "associatedPullRequests": {
                                        "nodes": [],
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    }
                                }
                            }
                        }
                    },
                )
            ]
        ),
    )
    assert resolve(OWNER, NAME, RequestId("merge_sha", SHA)).state == "no_matching_pr"


def test_absent_commit_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        producer, "_gh", _fake_gh([(0, {"data": {"repository": {"object": None}}})])
    )
    assert resolve(OWNER, NAME, RequestId("merge_sha", SHA)).state == "not_found"


def test_missing_object_key_is_mechanical_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """`object: null` (key present) is `not_found`; the key simply MISSING is
    a malformed response — same distinction as `test_missing_pull_request_key_is_mechanical_failure`."""
    monkeypatch.setattr(producer, "_gh", _fake_gh([(0, {"data": {"repository": {}}})]))
    with pytest.raises(MechanicalFailure, match="repository.object"):
        resolve(OWNER, NAME, RequestId("merge_sha", SHA))


def test_object_without_associated_pull_requests_is_mechanical_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex gate round 2's exact repro: `{"object": {}}` — the commit object
    is present, but `associatedPullRequests` is missing entirely (not merely
    empty). Before this fix, `commit.get("associatedPullRequests") or {}`
    silently produced an empty connection, and the traversal fell through to
    `no_matching_pr` — a truncated/malformed payload masquerading as an
    exhaustive negative search."""
    monkeypatch.setattr(producer, "_gh", _fake_gh([(0, {"data": {"repository": {"object": {}}}})]))
    with pytest.raises(MechanicalFailure, match="object.associatedPullRequests"):
        resolve(OWNER, NAME, RequestId("merge_sha", SHA))


def test_node_without_merge_commit_key_is_mechanical_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same class, one level deeper: an `associatedPullRequests.nodes[]`
    entry that legitimately has `mergeCommit: null` (a PR with no merge
    commit) is normal data and must be skipped as a non-match. A node
    entirely MISSING the `mergeCommit` key is a malformed response — Codex
    gate round 6 on PR #86: `node.get("mergeCommit") or {}` treated both the
    same, silently skipping a malformed node as "doesn't match" instead of
    failing the batch."""
    monkeypatch.setattr(
        producer,
        "_gh",
        _fake_gh(
            [
                (
                    0,
                    {
                        "data": {
                            "repository": {
                                "object": {
                                    "associatedPullRequests": {
                                        "nodes": [{"mergedBy": None}],
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    }
                                }
                            }
                        }
                    },
                )
            ]
        ),
    )
    with pytest.raises(MechanicalFailure, match=r"associatedPullRequests\.nodes\[\]\.mergeCommit"):
        resolve(OWNER, NAME, RequestId("merge_sha", SHA))


def test_merged_without_mergedby_is_actor_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        producer,
        "_gh",
        _fake_gh(
            [
                (
                    0,
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {"mergeCommit": {"oid": SHA}, "mergedBy": None}
                            }
                        }
                    },
                )
            ]
        ),
    )
    result = resolve(OWNER, NAME, RequestId("pr", 42))
    assert result.state == "actor_unavailable"
    assert result.merge_sha == SHA
    assert result.identity is None


def test_materialize_aborts_whole_batch_on_mechanical_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Один недоступный элемент валит публикацию целиком — но неслитый PR нет."""
    monkeypatch.setattr(
        producer,
        "_gh",
        _fake_gh(
            [
                (
                    0,
                    {
                        "data": {
                            "repository": {"pullRequest": {"mergeCommit": None, "mergedBy": None}}
                        }
                    },
                ),
                (1, "gh: boom"),
            ]
        ),
    )
    with pytest.raises(MechanicalFailure):
        materialize("andrei-shtanakov/steward", [RequestId("pr", 1), RequestId("pr", 2)])


def test_batch_survives_one_absent_pr_and_records_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """То, ради чего чинили: один битый номер больше не уносит весь батч.

    До починки несуществующий номер PR давал `MechanicalFailure`, то есть
    exit 3 и отсутствие файла целиком — при регулярном прогоне это означало бы
    «наблюдаемости нет» из-за одной опечатки в списке. Теперь отсутствие
    становится записью, а остальные элементы доезжают.
    """
    monkeypatch.setattr(
        producer,
        "_gh",
        _fake_gh(
            [
                (
                    0,
                    {
                        "data": {
                            "repository": {"pullRequest": {"mergeCommit": None, "mergedBy": None}}
                        }
                    },
                ),
                (1, _LIVE_ABSENT_PR),
            ]
        ),
    )

    results = materialize("andrei-shtanakov/steward", [RequestId("pr", 1), RequestId("pr", 999999)])

    assert [r.state for r in results] == ["not_merged", "not_found"]


def test_malformed_repo_is_value_error() -> None:
    with pytest.raises(ValueError, match="owner/name"):
        materialize("not-a-slug", [RequestId("pr", 1)])


def test_repo_with_extra_slash_is_value_error() -> None:
    """Codex gate round 4 на PR #86, blocker: `.partition("/")` находит
    только первый слэш, так что `owner/repo/extra` тоже проходил бы
    (owner="owner", name="repo/extra") — ровно один `/` обязателен, а не
    «хотя бы один»."""
    with pytest.raises(ValueError, match="owner/name"):
        materialize("owner/repo/extra", [RequestId("pr", 1)])


# --- classify_results (правка контроллера: resolve()/materialize() не знают
# политику, actor_class на merged-записях проставляет отдельная функция) ---


def _policy(
    *, human: frozenset[str] = frozenset(), agent: frozenset[str] = frozenset()
) -> ApprovalPolicy:
    return ApprovalPolicy(
        version=1, human_identities=human, agent_identities=agent, agent_merge_allowed=False
    )


def test_classify_results_tags_bot_identity_as_agent() -> None:
    merged = Result(
        RequestId("pr", 1),
        "merged",
        merge_sha=SHA,
        identity="github:merge-broker",
        type_hint="Bot",
    )
    [result] = classify_results([merged], _policy())
    assert result.actor_class == "agent"
    # identity/state/merge_sha/type_hint непотревожены — только actor_class добавлен.
    assert (result.request, result.state, result.merge_sha, result.identity, result.type_hint) == (
        merged.request,
        merged.state,
        merged.merge_sha,
        merged.identity,
        merged.type_hint,
    )


def test_classify_results_tags_listed_human() -> None:
    merged = Result(
        RequestId("pr", 2),
        "merged",
        merge_sha=SHA,
        identity="github:andrei-shtanakov",
        type_hint="User",
    )
    policy = _policy(human=frozenset({"github:andrei-shtanakov"}))
    [result] = classify_results([merged], policy)
    assert result.actor_class == "human"


def test_classify_results_tags_unrecognised_identity_as_unknown() -> None:
    merged = Result(
        RequestId("pr", 3),
        "merged",
        merge_sha=SHA,
        identity="github:someone-else",
        type_hint="User",
    )
    [result] = classify_results([merged], _policy())
    assert result.actor_class == "unknown"


def test_classify_results_leaves_negative_state_untouched() -> None:
    not_merged = Result(RequestId("pr", 4), "not_merged")
    [result] = classify_results([not_merged], _policy())
    assert result.actor_class is None
    assert result == not_merged


# --- находка codex-гейта на PR #95: терпимость привязана к пути ------------


def test_not_found_deeper_than_the_asked_node_stays_mechanical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Частичный отказ резолвера внутри объекта, который резолвнулся.

    Сценарий из находки: `pullRequest` пришёл целым, но `NOT_FOUND` стоит на
    `mergeCommit`. Проверка одного лишь ТИПА ошибки пропустила бы это, и
    `mergeCommit: null` был бы опубликован как факт «PR не слит» — то есть
    недоступность доложена отсутствием, ровно тот класс, ради которого
    `repository: null` уже отказываются читать как «репо нет».
    """
    monkeypatch.setattr(
        producer,
        "_gh",
        _fake_gh(
            [
                (
                    1,
                    (
                        {
                            "data": {
                                "repository": {
                                    "pullRequest": {
                                        "mergeCommit": None,
                                        "mergedBy": {"login": "alice", "__typename": "User"},
                                    }
                                }
                            },
                            "errors": [
                                {
                                    "type": "NOT_FOUND",
                                    "path": ["repository", "pullRequest", "mergeCommit"],
                                }
                            ],
                        },
                        "gh: partial failure",
                    ),
                )
            ]
        ),
    )

    with pytest.raises(MechanicalFailure, match="GraphQL errors"):
        resolve(OWNER, NAME, RequestId("pr", 42))


def test_not_found_without_a_path_stays_mechanical(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ошибка без `path` не привязывается ни к какому узлу — значит не факт."""
    body, err = _LIVE_ABSENT_PR
    monkeypatch.setattr(
        producer,
        "_gh",
        _fake_gh([(1, ({**body, "errors": [{"type": "NOT_FOUND"}]}, err))]),
    )

    with pytest.raises(MechanicalFailure, match="GraphQL errors"):
        resolve(OWNER, NAME, RequestId("pr", 999999))


def test_absent_sha_is_not_found_scoped_to_its_own_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Вторая форма запроса — по SHA — объявляет свой узел, а не чужой."""
    monkeypatch.setattr(
        producer,
        "_gh",
        _fake_gh(
            [
                (
                    1,
                    (
                        {
                            "data": {"repository": {"object": None}},
                            "errors": [{"type": "NOT_FOUND", "path": ["repository", "object"]}],
                        },
                        "gh: Could not resolve to a Commit",
                    ),
                )
            ]
        ),
    )

    assert resolve(OWNER, NAME, RequestId("merge_sha", "deadbeef")).state == "not_found"
