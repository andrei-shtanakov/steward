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
    it: Iterator[tuple[int, object]] = iter(responses)

    def fake(args: list[str]) -> tuple[int, str]:
        code, payload = next(it)
        return code, payload if isinstance(payload, str) else json.dumps(payload)

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


def test_absent_pr_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        producer, "_gh", _fake_gh([(0, {"data": {"repository": {"pullRequest": None}}})])
    )
    assert resolve(OWNER, NAME, RequestId("pr", 42)).state == "not_found"


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


def test_malformed_repo_is_value_error() -> None:
    with pytest.raises(ValueError, match="owner/name"):
        materialize("not-a-slug", [RequestId("pr", 1)])


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
