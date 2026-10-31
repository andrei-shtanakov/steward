#!/usr/bin/env python3
"""Генератор векторов сбора `approval-facts/v3` из живых снимков (спека steward §5, §5.6).

Векторы не пишутся руками: каждый — это живые снимки `tests/fixtures/approvalfacts_v3/live/`
(`derived_from`) после закрытого списка именованных преобразований (`transforms`), которые
применяет `scripts/approval_facts_v3_derive.py` — тот же модуль, что фикстура `derive`
тестов (загружается по пути файла; тестовый пакет генератор не импортирует). Положительный
вектор акта — неизменённая живая последовательность (`transforms: []`); отрицательный несёт
**ровно одно** нарушение и ровно потреблённые библиотекой обмены (`select`): чтение
останавливается на первом отказе. Ожидания (`expect`) объявлены здесь по спеке, а не
получены прогоном библиотеки: тест `tests/approvalfacts/v3/test_vectors.py` гоняет каждый
вектор и требует точного потребления обменов в записанном порядке.

Персоны `alice`/`bob` — `set_field` логинов авторов двух живых ревью #657 (ai-prosto
APPROVED, copilot COMMENTED); ими пользуются векторы, чьё ожидание называет логины (как
таблица §5.6). Узел `ReviewDismissedEvent` живьём не снят — вектор с ним помечен
`unverified_shape`.

Повторный запуск даёт те же байты; запись атомарна (временный каталог подменяет `--out`
целиком), `MANIFEST.json` несёт sha256 каждого файла векторов::

    uv run python scripts/gen_approval_facts_v3_vectors.py \
        --out contracts/approval-facts/v3/vectors
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib.util
import json
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "tests" / "fixtures" / "approvalfacts_v3" / "live"
DEFAULT_OUT = ROOT / "contracts" / "approval-facts" / "v3" / "vectors"

Record = dict[str, Any]
Transform = dict[str, Any]


def _load_derive() -> ModuleType:
    name = "approval_facts_v3_derive"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging error
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


D: Any = _load_derive()


def _live(name: str) -> Record:
    return json.loads((LIVE / f"{name}.json").read_text(encoding="utf-8"))


def _vector(
    kind: str,
    title: str,
    base: list[str],
    transforms: list[Transform],
    expect: Record,
    **inputs: Any,
) -> Record:
    derived = D.derive(base, transforms)
    spec: Record = {
        "kind": kind,
        "title": title,
        **inputs,
        "transport": {"exchanges": derived["exchanges"]},
        "expect": expect,
        "derived_from": derived["derived_from"],
        "transforms": derived["transforms"],
    }
    if "unverified_shape" in derived:
        spec["unverified_shape"] = derived["unverified_shape"]
    return spec


def _tf(op: str, path: list[Any], **kw: Any) -> Transform:
    return {"op": op, "path": path, **kw}


def _keep(n: int) -> list[Transform]:
    """Префикс из `n` обменов: библиотека остановилась на отказе."""
    return [{"op": "select", "keep": list(range(n))}]


# === акт: `read_act_facts` над `pr657_act_sequence` (merge-коммит) ===========================

SEQ = "pr657_act_sequence"
SEQ636 = "pr636_act_sequence"
CLOSED606 = "closed606_act_sequence"
NEG_E6 = "neg_e6_blobs_present_absent_tree"
NEG_E2 = "neg_e2_delivery_absent_pr"
DEVTOOLS = "andrei-shtanakov/devtools"
#: Обмены `pr657_act_sequence` в порядке библиотеки (спека §3.8, «согласованные наборы»).
E2, E1, E5, E6, R1, T1, R2, T2, E2B, E1B = range(10)
FULL = 10
T = "2026-10-30T12:09:17Z"  # merged_at #657
P = "docs/evidence/2026-10-30-df-stage2a-acceptance.md"
MERGE = "aa4e7e37d9f1ea34581c9c484d60074b6d3ac18e"
HEAD = "b3651bcb2907bd6d1b6d421b018701520fb64066"
BLOB = "1d902e2395fb61e960eefc21ef04f471d139e774"
OTHER = "9da14cff73a8e2558d1f7f1b19599d50b5456bd7"  # реальный коммит devtools (база #657)
ALICE_ID = 5711633458  # ai-prosto APPROVED
BOB_ID = 5711735077  # copilot-pull-request-reviewer COMMENTED
OWNER = "andrei-shtanakov"  # актор отзывов: владелец репо
GQL_PULL = ["response", "body", "data", "repository", "pullRequest"]
REST_PULL = ["response", "body"]
REVIEW_NODES = [*GQL_PULL, "reviews", "nodes"]
TIMELINE_NODES = [*GQL_PULL, "timelineItems", "nodes"]
BLOBS = ["response", "body", "data", "repository"]
ALICE, BOB = 0, 1  # индексы узлов ревью


def _reviews(
    op: str, sub: list[Any], passes: tuple[int, ...] = (R1, R2), **kw: Any
) -> list[Transform]:
    return [_tf(op, [i, *REVIEW_NODES, *sub], **kw) for i in passes]


def _timeline(
    op: str, sub: list[Any], passes: tuple[int, ...] = (T1, T2), **kw: Any
) -> list[Transform]:
    return [_tf(op, [i, *TIMELINE_NODES, *sub], **kw) for i in passes]


PERSONA = _reviews("set_field", [ALICE, "author"], value={"login": "alice", "__typename": "User"})
PERSONA += _reviews("set_field", [BOB, "author"], value={"login": "bob", "__typename": "User"})


def _review(
    review_id: int, login: str, state: str, submitted: str | None, commit: str = HEAD
) -> Record:
    """Узел ревью: копия живого узла E3 (ревью ai-prosto #657) с подменёнными значениями."""
    node = copy.deepcopy(_live(SEQ)["exchanges"][R1]["response"]["body"]["data"])
    node = node["repository"]["pullRequest"]["reviews"]["nodes"][ALICE]
    node.update(databaseId=review_id, state=state, submittedAt=submitted)
    node["commit"]["oid"] = commit
    node["author"]["login"] = login
    return node


def _dismiss(review_id: int, created: str, prev: str | None, passes=(T1, T2)) -> list[Transform]:
    node = D.dismissal_node(review_id, created, prev, OWNER, "User")
    return _timeline("append_node", [], passes=passes, node=node)


def _dismiss_bob(
    created: str = "2026-10-30T12:00:00Z", prev: str | None = "CHANGES_REQUESTED"
) -> list[Transform]:
    """Ревью Bob отозвано (в обоих проходах): `DISMISSED` + событие отзыва."""
    return _reviews("set_field", [BOB, "state"], value="DISMISSED") + _dismiss(
        BOB_ID, created, prev
    )


def _dismiss_alice(created: str, passes=(R1, R2), timeline=(T1, T2)) -> list[Transform]:
    state = _reviews("set_field", [ALICE, "state"], passes=passes, value="DISMISSED")
    return state + _dismiss(ALICE_ID, created, "APPROVED", passes=timeline)


def _delivery(
    op: str,
    graphql: list[Any] | None,
    rest: list[Any] | None,
    reads: tuple[int, ...] = (0, 1),
    **kw: Any,
) -> list[Transform]:
    """Одно преобразование E2 (`graphql`) и/или E1 (`rest`) в чтениях доставки `reads`."""
    out: list[Transform] = []
    for read in reads:
        gql, rst = (E2, E1) if read == 0 else (E2B, E1B)
        if graphql is not None:
            out.append(_tf(op, [gql, *GQL_PULL, *graphql], **kw))
        if rest is not None:
            out.append(_tf(op, [rst, *REST_PULL, *rest], **kw))
    return out


def _merger(
    gql_login: str, gql_type: str, rest_login: str, rest_type: str, reads: tuple[int, ...] = (0, 1)
) -> list[Transform]:
    value = {"login": gql_login, "__typename": gql_type}
    out = _delivery("set_field", ["mergedBy"], None, reads, value=value)
    out += _delivery("set_field", None, ["merged_by", "login"], reads, value=rest_login)
    return out + _delivery("set_field", None, ["merged_by", "type"], reads, value=rest_type)


APP_MERGER = _merger("df-reviewer", "Bot", "df-reviewer[bot]", "Bot")


def _act(
    title: str,
    transforms: list[Transform],
    expect: Record,
    *,
    persona: bool = False,
    keep: int | None = None,
    base: list[str] | None = None,
    paths: list[str] | None = None,
    t: str | None = T,
    before: list[list[str]] | None = None,
    pr: int = 657,
) -> Record:
    """Вектор акта: персоны (если ожидание называет логины), преобразования, префикс."""
    all_tf = (PERSONA if persona else []) + transforms + (_keep(keep) if keep else [])
    return _vector(
        "act",
        title,
        base or [SEQ],
        all_tf,
        expect,
        repo=DEVTOOLS,
        pr=pr,
        paths=paths or [P],
        t=t,
        before=before or [],
    )


def _no(code: str) -> Record:
    return {"unavailable": code}


def _eff(**states: str) -> Record:
    return {"effective": states}


def _act_vectors() -> dict[str, Record]:
    v: dict[str, Record] = {}
    v.update(_act_positive())
    v.update(_act_delivery())
    v.update(_act_reviews())
    v.update(_act_actor())
    v.update(_act_paths())
    return v


def _act_positive() -> dict[str, Record]:
    live657 = {
        "effective": {"ai-prosto": "APPROVED"},
        "effective_commit": {"ai-prosto": HEAD},
        "delivery": {
            "merged": True,
            "merge_commit": MERGE,
            "merged_at": T,
            "merged_by_login": "andrei-shtanakov",
            "merged_by_type_hint": "User",
            "head_at_merge": HEAD,
            "in_base": True,
            "paths": [{"path": P, "blob_at_merge": BLOB, "blob_at_head": BLOB}],
        },
    }
    head636, blob636 = (
        "dfe8b0110b2b81ff32fddcf2bc4bb845a5dfb120",
        "27cb9e543a237672d9de07c90a4995cf4b2c5b09",
    )
    live636 = {
        "effective": {"ai-prosto": "APPROVED"},
        "effective_commit": {"ai-prosto": head636},
        "delivery": {
            "merged": True,
            "merge_commit": "1f350c74a4909454619b820d29cbaea244a68733",
            "merged_at": "2026-10-26T15:48:49Z",
            "merged_by_login": "ai-prosto",
            "merged_by_type_hint": "User",
            "head_at_merge": head636,
            "in_base": True,
            "paths": [{"path": "TODO.md", "blob_at_merge": blob636, "blob_at_head": blob636}],
        },
    }
    clean = {
        "effective": {"alice": "APPROVED"},
        "delivery": {
            "merged": True,
            "merged_by_login": "df-reviewer",
            "merged_by_type_hint": "Bot",
            "in_base": True,
        },
    }
    return {
        "live_pr657": _act(
            "Положительный двойник E1–E6: неизменённая живая последовательность #657 "
            "(merge-коммит, compare identical, один путь)",
            [],
            live657,
        ),
        "live_pr636": _act(
            "Положительный двойник E1–E6: неизменённая живая последовательность #636 "
            "(squash, compare behind)",
            [],
            live636,
            base=[SEQ636],
            pr=636,
            paths=["TODO.md"],
            t="2026-10-26T15:48:49Z",
        ),
        "not_merged": _act(
            "PR не смёржен (живой #606: REST merged=false с ненулевым тестовым "
            "merge_commit_sha) — waiting_delivery, ревью не читаются",
            [],
            {"waiting_delivery": True},
            base=[CLOSED606],
            pr=606,
            paths=["README.md"],
            t=None,
        ),
        "clean_approve_app_merge": _act(
            "Чистый акт: APPROVED Alice на голове, мерж App (REST df-reviewer[bot] / GraphQL "
            "df-reviewer, Bot)",
            APP_MERGER,
            clean,
            persona=True,
        ),
        "bot_login_two_forms": _act(
            "Две формы логина бота: REST df-reviewer[bot]/Bot и GraphQL df-reviewer/Bot "
            "сводятся к df-reviewer",
            APP_MERGER,
            {
                "effective": {"alice": "APPROVED"},
                "delivery": {"merged_by_login": "df-reviewer", "merged_by_type_hint": "Bot"},
            },
            persona=True,
        ),
    }


def _act_delivery() -> dict[str, Record]:
    over_limit: list[Transform] = []
    for i in range(50):
        rid = 5711800000 + i
        over_limit += _reviews(
            "append_node",
            [],
            passes=(R1,),
            node=_review(rid, "bob", "DISMISSED", "2026-10-30T11:50:00Z"),
        )
        over_limit += _dismiss(rid, "2026-10-30T11:55:00Z", "CHANGES_REQUESTED", passes=(T1,))
    over_limit += [_tf("page_split", [T1 + i, *TIMELINE_NODES], n=1) for i in range(50)]
    partial = {
        "data": _live(SEQ)["exchanges"][R1]["response"]["body"]["data"],
        "errors": _live(NEG_E2)["response"]["body"]["errors"],
    }
    return {
        "pagination_reviews_no_cursor": _act(
            "Неполная пагинация: страница ревью hasNextPage без endCursor",
            [
                _tf(
                    "set_field",
                    [R1, *GQL_PULL, "reviews", "pageInfo"],
                    value={"hasNextPage": True, "endCursor": None},
                )
            ],
            _no("pagination"),
            keep=R1 + 1,
        ),
        "pagination_timeline_over_limit": _act(
            "Неполная пагинация: таймлайн длиннее 50 страниц (50 отозванных ревью Bob, по "
            "узлу на страницу)",
            over_limit,
            _no("pagination"),
            keep=T1 + 50,
        ),
        "access_rest_403": _act(
            "Ошибка доступа: REST pulls/{n} отвечает 403",
            [_tf("set_field", [E1, "response", "status"], value=403)],
            _no("http"),
            keep=E1 + 1,
        ),
        "access_graphql_401": _act(
            "Ошибка доступа: GraphQL отвечает 401",
            [_tf("set_field", [E2, "response", "status"], value=401)],
            _no("http"),
            keep=E2 + 1,
        ),
        "head_rest_graphql_mismatch": _act(
            "Смена головы: REST head.sha ≠ GraphQL headRefOid в чтении доставки",
            _delivery("set_field", None, ["head", "sha"], (0,), value=OTHER),
            _no("delivery_mismatch"),
            keep=E1 + 1,
        ),
        "pass_head_differs_from_delivery": _act(
            "M0-F: headRefOid проходов ревью ≠ голове доставки",
            [_tf("set_field", [i, *GQL_PULL, "headRefOid"], value=OTHER) for i in (R1, R2)],
            _no("delivery_mismatch"),
            keep=FULL,
        ),
        "pass_base_differs_from_delivery": _act(
            "M0-F: baseRefName проходов ревью ≠ базе доставки",
            [_tf("set_field", [i, *GQL_PULL, "baseRefName"], value="main") for i in (R1, R2)],
            _no("delivery_mismatch"),
            keep=FULL,
        ),
        "delivery_changed_on_reread": _act(
            "Повторное чтение доставки после проходов даёт другую голову — unstable",
            _delivery("set_field", ["headRefOid"], None, (1,), value=OTHER)
            + _delivery("set_field", None, ["head", "sha"], (1,), value=OTHER),
            _no("unstable"),
            keep=FULL,
        ),
        "graphql_200_partial_errors_approved": _act(
            "200 + частичные data (APPROVED виден) + errors на странице ревью — graphql_errors",
            [_tf("set_field", [R1, "response", "body"], value=partial)],
            _no("graphql_errors"),
            keep=R1 + 1,
        ),
        "bot_login_mismatch": _act(
            "Несводимые формы логина мержера: REST other[bot] против GraphQL df-reviewer",
            _merger("df-reviewer", "Bot", "other[bot]", "Bot", reads=(0,)),
            _no("bot_login_mismatch"),
            keep=E1 + 1,
        ),
        "bot_login_type_mismatch": _act(
            "Мержер: тот же логин df-reviewer, но REST type User против GraphQL Bot — тип "
            "сверяется, bot_login_mismatch",
            _merger("df-reviewer", "Bot", "df-reviewer", "User", reads=(0,)),
            _no("bot_login_mismatch"),
            keep=E1 + 1,
        ),
        "merger_bot_suffix_with_user_type": _act(
            "Мержер: REST df-reviewer[bot] с type User — суффикс [bot] сводится только при "
            "type Bot, несведённый логин не установлен (§3.4.1) — actor_unresolved",
            _merger("df-reviewer", "User", "df-reviewer[bot]", "User", reads=(0,)),
            _no("actor_unresolved"),
            keep=E1 + 1,
        ),
        "merged_disagreement": _act(
            "GraphQL merged=false при REST merged=true — delivery_mismatch",
            _delivery("set_field", ["merged"], None, (0,), value=False),
            _no("delivery_mismatch"),
            keep=E1 + 1,
        ),
        "merged_event_commit_mismatch": _act(
            "MergedEvent таймлайна указывает не на мерж-коммит доставки — delivery_mismatch",
            _timeline("set_field", [0, "commit", "oid"], value=OTHER),
            _no("delivery_mismatch"),
            keep=FULL,
        ),
        "in_base_diverged": _act(
            "compare базы с мерж-коммитом diverged (производное значение живой формы E5) ⇒ "
            "in_base = false",
            [_tf("set_field", [E5, "response", "body", "status"], value="diverged")],
            {"effective": {"alice": "APPROVED"}, "delivery": {"merged": True, "in_base": False}},
            persona=True,
        ),
    }


def _act_reviews() -> dict[str, Record]:
    later = "2026-10-30T11:50:00Z"
    return {
        "dismiss_and_cr_between_passes": _act(
            "Между проходами (голова неизменна): отзыв APPROVED Alice и новое "
            "CHANGES_REQUESTED Bob, оба до T — unstable",
            _dismiss_alice("2026-10-30T12:05:00Z", passes=(R2,), timeline=(T2,))
            + _reviews(
                "append_node",
                [],
                passes=(R2,),
                node=_review(5711700002, "bob", "CHANGES_REQUESTED", "2026-10-30T12:00:00Z"),
            ),
            _no("unstable"),
            persona=True,
            keep=FULL,
        ),
        "dismissal_previous_state_changed_between_passes": _act(
            "Отзыв Alice до T в обоих проходах, но previous_state различается (APPROVED / "
            "CHANGES_REQUESTED) — unstable",
            _reviews("set_field", [ALICE, "state"], value="DISMISSED")
            + _dismiss(ALICE_ID, "2026-10-30T12:00:00Z", "APPROVED", passes=(T1,))
            + _dismiss(ALICE_ID, "2026-10-30T12:00:00Z", "CHANGES_REQUESTED", passes=(T2,)),
            _no("unstable"),
            persona=True,
            keep=FULL,
        ),
        "dismissed_after_preflight_before_merge": _act(
            "Отзыв после второго прохода предпроверки, но до мержа — чтение после мержа видит "
            "отзыв до T: DISMISSED",
            _dismiss_alice("2026-10-30T12:08:59Z"),
            _eff(alice="DISMISSED"),
            persona=True,
        ),
        "restore_approved_cr_dismissed": _act(
            "§5.3: APPROVED → CHANGES_REQUESTED → отзыв второго до T ⇒ DISMISSED (раннее "
            "APPROVED не восстанавливается)",
            _reviews("append_node", [], node=_review(5711700001, "alice", "DISMISSED", later))
            + _dismiss(5711700001, "2026-10-30T11:55:00Z", "CHANGES_REQUESTED"),
            _eff(alice="DISMISSED"),
            persona=True,
        ),
        "restore_approved_dismissed_after_t": _act(
            "§5.3: APPROVED отозвано после T ⇒ на T эффективное APPROVED",
            _dismiss_alice("2026-10-30T12:30:00Z"),
            _eff(alice="APPROVED"),
            persona=True,
        ),
        "dismissal_without_previous_state": _act(
            "Отзыв ревью Bob до T без previous_state при действительном APPROVED Alice — отказ "
            "чтения",
            _dismiss_bob(prev=None),
            _no("dismissal_without_previous_state"),
            persona=True,
            keep=FULL,
        ),
        "dismissal_without_previous_state_after_t": _act(
            "Отзыв ревью Bob после T без previous_state — тот же отказ чтения (безусловный)",
            _dismiss_bob("2026-10-30T12:30:00Z", prev=None),
            _no("dismissal_without_previous_state"),
            persona=True,
            keep=FULL,
        ),
        "dismissal_with_previous_state_twin": _act(
            "Двойник: тот же отзыв ревью Bob с previous_state CHANGES_REQUESTED",
            _dismiss_bob(),
            _eff(alice="APPROVED", bob="DISMISSED"),
            persona=True,
        ),
        "dismissal_normal_twin": _act(
            "Положительный двойник типа актора: обычный отзыв ревью Bob актором User "
            "(actor{login, __typename} заполнены)",
            _dismiss_bob(),
            _eff(alice="APPROVED", bob="DISMISSED"),
            persona=True,
        ),
        "login_approved_then_cr": _act(
            "Несколько ревью одного логина: APPROVED → CHANGES_REQUESTED ⇒ CHANGES_REQUESTED",
            _reviews(
                "append_node", [], node=_review(5711700003, "alice", "CHANGES_REQUESTED", later)
            ),
            _eff(alice="CHANGES_REQUESTED"),
            persona=True,
        ),
        "login_cr_then_approved": _act(
            "Несколько ревью одного логина: CHANGES_REQUESTED → APPROVED ⇒ APPROVED",
            _reviews("set_field", [ALICE, "state"], value="CHANGES_REQUESTED")
            + _reviews("append_node", [], node=_review(5711700003, "alice", "APPROVED", later)),
            _eff(alice="APPROVED"),
            persona=True,
        ),
        "login_approved_old_then_new": _act(
            "Несколько ревью одного логина: APPROVED (старый коммит) → APPROVED (голова) ⇒ "
            "APPROVED с commit_id нового",
            _reviews("set_field", [ALICE, "commit", "oid"], value=OTHER)
            + _reviews("append_node", [], node=_review(5711700003, "alice", "APPROVED", later)),
            {"effective": {"alice": "APPROVED"}, "effective_commit": {"alice": HEAD}},
            persona=True,
        ),
        "login_approved_then_commented": _act(
            "Несколько ревью одного логина: APPROVED → COMMENTED ⇒ APPROVED",
            _reviews("append_node", [], node=_review(5711700003, "alice", "COMMENTED", later)),
            _eff(alice="APPROVED"),
            persona=True,
        ),
        "dismissal_before_merged_at": _act(
            "Отзыв APPROVED Alice до merged_at ⇒ DISMISSED",
            _dismiss_alice("2026-10-30T11:59:00Z"),
            _eff(alice="DISMISSED"),
            persona=True,
        ),
        "dismissal_after_merged_at": _act(
            "Отзыв APPROVED Alice после merged_at ⇒ APPROVED",
            _dismiss_alice("2026-10-30T12:20:00Z"),
            _eff(alice="APPROVED"),
            persona=True,
        ),
        "post_merge_dismissal_between_passes": _act(
            "Отзыв после T появился между проходами (только во втором) — устойчиво, на T APPROVED",
            _dismiss_alice("2026-10-30T12:09:22Z", passes=(R2,), timeline=(T2,)),
            _eff(alice="APPROVED"),
            persona=True,
        ),
        "equal_stamp_dismissal_before": _act(
            "§8.2: отзыв с created_at = T, ключ в before ⇒ DISMISSED",
            _dismiss_alice(T),
            _eff(alice="DISMISSED"),
            persona=True,
            before=[["dismissal", str(ALICE_ID)]],
        ),
        "equal_stamp_dismissal_after": _act(
            "§8.2: отзыв с created_at = T, ключа нет в before ⇒ APPROVED",
            _dismiss_alice(T),
            _eff(alice="APPROVED"),
            persona=True,
        ),
        "equal_stamp_approve_before": _act(
            "§8.2: approve с submitted_at = T, ключ в before ⇒ APPROVED",
            _reviews("retime", [ALICE, "submittedAt"], ts=T),
            _eff(alice="APPROVED"),
            persona=True,
            before=[["review", str(ALICE_ID)]],
        ),
        "equal_stamp_approve_after": _act(
            "§8.2: approve с submitted_at = T, ключа нет в before ⇒ логина нет",
            _reviews("retime", [ALICE, "submittedAt"], ts=T),
            _eff(),
            persona=True,
        ),
        "pending_without_submitted_at": _act(
            "APPROVED + PENDING без submitted_at ⇒ APPROVED",
            _reviews("append_node", [], node=_review(5711700004, "alice", "PENDING", None)),
            _eff(alice="APPROVED"),
            persona=True,
        ),
        "submitted_without_submitted_at": _act(
            "Отправленное ревью Bob без submitted_at — отказ чтения",
            _reviews("set_field", [BOB, "submittedAt"], value=None),
            _no("missing_submitted_at"),
            persona=True,
            keep=FULL,
        ),
        "unknown_state": _act(
            "Неизвестный state ревью Bob",
            _reviews("set_field", [BOB, "state"], value="SUPERSEDED"),
            _no("unknown_state"),
            persona=True,
            keep=FULL,
        ),
        "missing_commit_id": _act(
            "Ревью Bob без commit",
            _reviews("set_field", [BOB, "commit"], value=None),
            _no("missing_commit_id"),
            persona=True,
            keep=T1 + 1,
        ),
        "dismissed_without_event": _act(
            "Ревью Bob в DISMISSED без события отзыва — отказ чтения",
            _reviews("set_field", [BOB, "state"], value="DISMISSED"),
            _no("dismissed_without_event"),
            persona=True,
            keep=FULL,
        ),
    }


def _act_actor() -> dict[str, Record]:
    def author(value: Any) -> list[Transform]:
        return _reviews("set_field", [BOB, "author"], value=value)

    no_typename = _timeline("drop_field", [1, "actor", "__typename"])
    return {
        "actor_unresolved_other_reviewer_null": _act(
            "Действительный APPROVED Alice + ревью автора null — actor_unresolved для всего PR",
            author(None),
            _no("actor_unresolved"),
            persona=True,
            keep=T1 + 1,
        ),
        "actor_unresolved_other_reviewer_ghost": _act(
            "Действительный APPROVED Alice + ревью автора ghost — actor_unresolved",
            author({"login": "ghost", "__typename": "User"}),
            _no("actor_unresolved"),
            persona=True,
            keep=T1 + 1,
        ),
        "actor_unresolved_mannequin": _act(
            "Действительный APPROVED Alice + ревью автора типа Mannequin — actor_unresolved",
            author({"login": "bob", "__typename": "Mannequin"}),
            _no("actor_unresolved"),
            persona=True,
            keep=T1 + 1,
        ),
        "actor_unresolved_dismissal_actor": _act(
            "Действительный APPROVED Alice + отзыв ревью Bob актором null — actor_unresolved",
            _dismiss_bob() + _timeline("set_field", [1, "actor"], value=None),
            _no("actor_unresolved"),
            persona=True,
            keep=T1 + 1,
        ),
        "dismissal_actor_no_typename": _act(
            "Двойник dismissal_normal_twin: актор отзыва без __typename — actor_unresolved",
            _dismiss_bob() + no_typename,
            _no("actor_unresolved"),
            persona=True,
            keep=T1 + 1,
        ),
        "actor_unresolved_merger": _act(
            "Мержер null в обоих источниках доставки — actor_unresolved",
            _delivery("set_field", ["mergedBy"], ["merged_by"], (0,), value=None),
            _no("actor_unresolved"),
            keep=E1 + 1,
        ),
    }


def _act_paths() -> dict[str, Record]:
    neg = _live(NEG_E6)
    three = [neg["request"]["variables"][f"m{i}"].split(":", 1)[1] for i in range(3)]
    blob = neg["response"]["body"]["data"]["repository"]["h0"]
    at = FULL  # индекс обмена `neg_e6` после обменов #657

    def as_blob(i: int) -> list[Transform]:
        return [_tf("set_field", [at, *BLOBS, f"{s}{i}"], value=blob) for s in "mh"]

    order = [{"op": "select", "keep": [E2, E1, E5, at]}]
    return {
        "paths_deleted_at_merge": _act(
            "Путь удалён в мерже: m0 = null ⇒ blob_at_merge = null",
            [_tf("set_field", [E6, *BLOBS, "m0"], value=None)],
            {
                "effective": {"alice": "APPROVED"},
                "delivery": {"paths": [{"path": P, "blob_at_merge": None, "blob_at_head": BLOB}]},
            },
            persona=True,
        ),
        "paths_three_negative": _act(
            "Трёхпутевой живой E6 (neg_e6): путь отсутствует на голове — path_absent_at_head "
            "(каталог заменён blob-ом пути 0, чтобы нарушение было одно)",
            as_blob(2) + order,
            _no("path_absent_at_head"),
            base=[SEQ, NEG_E6],
            paths=three,
        ),
        "paths_not_a_blob": _act(
            "Трёхпутевой живой E6 (neg_e6): путь — каталог — not_a_blob (отсутствующий путь "
            "заменён blob-ом пути 0)",
            as_blob(1) + order,
            _no("not_a_blob"),
            base=[SEQ, NEG_E6],
            paths=three,
        ),
    }


# === предпроверка: `read_open_review` над `closed606_review_pass` ×2 ==========================

PASS606 = "closed606_review_pass"
PASS603 = "pr603_review_pass"
O_R1, O_T1, O_R2, O_T2 = range(4)
T606 = "2026-10-12T18:00:00Z"
ALICE606 = 5493892414


def _open(
    title: str,
    base: str,
    transforms: list[Transform],
    expect: Record,
    *,
    pr: int = 606,
    t: str = T606,
) -> Record:
    return _vector(
        "open_review",
        title,
        [base, base],
        transforms,
        expect,
        repo=DEVTOOLS,
        pr=pr,
        t=t,
        before=[],
    )


def _open_vectors() -> dict[str, Record]:
    later = "2026-10-12T17:20:00Z"
    alice = D.dismissal_node(ALICE606, later, "APPROVED", OWNER, "User")
    cr = _review(
        5493999999, "bob", "CHANGES_REQUESTED", later, "a038c2156ac098dad4e1d0cba3ba936cc57940c8"
    )
    race = [
        _tf("set_field", [O_R2, *REVIEW_NODES, 0, "state"], value="DISMISSED"),
        *[_tf("append_node", [i, *REVIEW_NODES], node=cr) for i in (O_R1, O_R2)],
        *[_tf("append_node", [i, *TIMELINE_NODES], node=alice) for i in (O_T1, O_T2)],
        _tf("page_split", [O_R2, *REVIEW_NODES], n=1),
        _tf("page_split", [O_R1, *REVIEW_NODES], n=1),
    ]
    ghost = {"login": "ghost", "__typename": "User"}
    return {
        "open_review/clean": _open(
            "Предпроверка: один живой проход #606 (User APPROVED, Bot COMMENTED), повторённый "
            "вторым проходом",
            PASS606,
            [],
            _eff(**{"ai-prosto": "APPROVED"}),
        ),
        "open_review/head_changed": _open(
            "Предпроверка: headRefOid меняется между проходами — unstable",
            PASS606,
            [_tf("set_field", [O_R2, *GQL_PULL, "headRefOid"], value=HEAD)],
            _no("unstable"),
        ),
        "open_review/race_between_pages": _open(
            "Предпроверка: отзыв APPROVED и новое CHANGES_REQUESTED между страницами прохода "
            "1 при неизменной голове — unstable. Нарушений больше одного по природе сценария: "
            "разорванный проход 1 нецелен и сам по себе; вектор с одним нарушением — "
            "dismiss_and_cr_between_passes",
            PASS606,
            race,
            _no("unstable"),
        ),
        "open_review/head_changed_between_pages": _open(
            "Предпроверка: headRefOid меняется между страницами ревью прохода 1 — unstable",
            PASS606,
            [
                _tf("page_split", [O_R1, *REVIEW_NODES], n=1),
                _tf("set_field", [O_R1 + 1, *GQL_PULL, "headRefOid"], value=HEAD),
                *_keep(O_T1 + 2),
            ],
            _no("unstable"),
        ),
        "open_review/actor_unresolved_ghost": _open(
            "Предпроверка: ревью автора ghost — actor_unresolved",
            PASS606,
            [_tf("set_field", [i, *REVIEW_NODES, 1, "author"], value=ghost) for i in (O_R1, O_R2)]
            + _keep(O_T1 + 1),
            _no("actor_unresolved"),
        ),
        "open_review/review_on_non_head_commit": _open(
            "Ревью на коммитах ≠ headRefOid (один живой проход #603, повторённый вторым): "
            "эффективное APPROVED со своим commit_id",
            PASS603,
            [],
            {
                "effective": {"ai-prosto": "APPROVED"},
                "effective_commit": {"ai-prosto": "0172e10f0adecccd513027f785e29ee46d0c6b29"},
            },
            pr=603,
            t="2026-10-12T15:07:43Z",
        ),
    }


# === история источника политики и активация (спека §3.7) ======================================

POLICY = "andrei-shtanakov/approval-policy"
POLICY_FILES = ["policy/approvers.env", "policy/modes.yaml", "policy/actors.yaml"]
APPROVERS, MODES, ACTORS = POLICY_FILES
ROOT_SHA = "12d198fb4db9ed6fbb1c5b00af308eeb31e4ec22"  # корень approval-policy, прямой коммит
LIVE_APPROVERS_SHA256 = "098f56b6f2afe0b97950ddfb50cd0ed5f386728fa2e22f29855eae1c336a8e0f"
MERGE_657, SIDE_657, BASE_657 = MERGE, HEAD, OTHER
TREE_657 = "c9527aa44763f57b3a4c310809f92378486a7e4f"
BLOB_657 = "07b96dccd88cbc3e0582bc1a5f63dcd8f61fe55c"
MERGED_AT = "2026-10-01T10:00:00Z"
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


A, B, C, D_, S, R = (_sha(x) for x in "ABCDSR")
X1, X2 = _sha("X1"), _sha("X2")


@dataclass
class _History:
    """Обмены истории из живых шаблонов H1–H6: имена снимков + `set_field` по пути."""

    repo: str = POLICY
    names: list[str] = field(default_factory=list)
    transforms: list[Transform] = field(default_factory=list)

    def raw(self, name: str, **sets: Any) -> _History:
        """Снимок `name`; `sets` — `set_field` по пути `a__b__0` от корня обмена."""
        i = len(self.names)
        self.names.append(name)
        for dotted, value in sets.items():
            steps = [int(s) if s.isdigit() else s for s in dotted.split("__")]
            self.transforms.append(_tf("set_field", [i, *steps], value=value))
        return self

    def repository(self, default: str = "main") -> _History:
        return self.raw(
            "h1_repo_policy",
            request__path=f"repos/{self.repo}",
            response__body__default_branch=default,
        )

    def branch(self, head: str, name: str = "main") -> _History:
        return self.raw(
            "h2_branch_policy",
            request__path=f"repos/{self.repo}/branches/{name}",
            response__body__commit__sha=head,
        )

    def commit(self, sha: str, parents: list[str], tree: str) -> _History:
        template = _live("h3_commit_side_one_parent")["response"]["body"]["parents"][0]
        return self.raw(
            "h3_commit_side_one_parent",
            request__path=f"repos/{self.repo}/commits/{sha}",
            response__body__sha=sha,
            response__body__parents=[{**template, "sha": p} for p in parents],
            response__body__commit__tree__sha=tree,
        )

    def pulls(self, sha: str, *prs: Record) -> _History:
        """H4 из живого squash-ответа; один PR — подмена его полей (без копии тела)."""
        path = f"repos/{self.repo}/commits/{sha}/pulls?per_page=100"
        if len(prs) == 1:
            pr = prs[0]
            return self.raw(
                "h4_commit_pulls_squash",
                request__path=path,
                response__body__0__number=pr["number"],
                response__body__0__merge_commit_sha=pr.get("merge", sha),
                response__body__0__merged_at=pr.get("merged_at", MERGED_AT),
                response__body__0__base__ref=pr.get("base", "main"),
            )
        template = _live("h4_commit_pulls_squash")["response"]["body"][0]
        body = []
        for pr in prs:
            item = copy.deepcopy(template)
            item["number"] = pr["number"]
            item["merge_commit_sha"] = pr.get("merge", sha)
            item["merged_at"] = pr.get("merged_at", MERGED_AT)
            item["base"]["ref"] = pr.get("base", "main")
            body.append(item)
        return self.raw("h4_commit_pulls_squash", request__path=path, response__body=body)

    def tree(self, tree: str, files: dict[str, bytes], truncated: bool = False) -> _History:
        template = _live("h5_tree_recursive_policy")["response"]["body"]["tree"]
        entries = [copy.deepcopy(template[0])]  # README.md — посторонняя запись
        for path, data in files.items():
            entry = copy.deepcopy(template[2])
            entry.update(path=path, type="blob", sha=_oid(data))
            entries.append(entry)
        return self.raw(
            "h5_tree_recursive_policy",
            request__path=f"repos/{self.repo}/git/trees/{tree}?recursive=1",
            response__body__sha=tree,
            response__body__tree=entries,
            response__body__truncated=truncated,
        )

    def blob(self, data: bytes) -> _History:
        oid = _oid(data)
        return self.raw(
            "h6_blob_policy_approvers",
            request__path=f"repos/{self.repo}/git/blobs/{oid}",
            response__body__sha=oid,
            response__body__content=base64.b64encode(data).decode(),
        )

    def missing(self, path: str, status: int = 404) -> _History:
        """Отказ форджа на `path` — из живого снимка 422 (`h5_tree_absent_422`)."""
        return self.raw(
            "h5_tree_absent_422", request__path=f"repos/{self.repo}/{path}", response__status=status
        )


@dataclass
class _Link:
    """Звено first-parent: PR-мерж `pr` (None — без PR), файлы политики, боковой родитель."""

    sha: str
    parent: str
    pr: int | None
    files: dict[str, bytes]
    merged_at: str = MERGED_AT
    side: str | None = None

    @property
    def tree(self) -> str:
        return _sha(f"tree-{self.sha}")


def _full(h: _History, links: list[_Link]) -> _History:
    """Успешная история `links` (от якоря к голове) в порядке запросов §3.7: H1, H2, H3 от
    головы к якорю, все H4 от якоря к голове, затем все H5, H6 по активациям."""
    _chain(h, links)
    for link in links:
        h.pulls(link.sha, {"number": link.pr, "merged_at": link.merged_at})
    for link in links:
        h.tree(link.tree, link.files)
    previous = None
    for link in links:
        current = {p: _oid(d) for p, d in link.files.items()}
        if current != previous:
            for path in POLICY_FILES:
                if path in link.files:
                    h.blob(link.files[path])
        previous = current
    return h


def _chain(h: _History, links: list[_Link]) -> _History:
    """H1, H2 и цепочка H3 от головы к якорю."""
    h.repository().branch(links[-1].sha)
    for link in reversed(links):
        h.commit(link.sha, [link.parent] + ([link.side] if link.side else []), link.tree)
    return h


def _act_of(pr: int, sha: str, files: dict[str, bytes], merged_at: str = MERGED_AT) -> Record:
    return {
        "pr": pr,
        "merge_commit": sha,
        "merged_at": merged_at,
        "files_sha256": {p: _sha256(d) for p, d in files.items()},
    }


def _history(
    title: str,
    h: _History,
    expect: Record,
    *,
    anchor: str = A,
    until: str | None = None,
    branch: str = "main",
) -> Record:
    return _vector(
        "history",
        title,
        h.names,
        h.transforms,
        expect,
        repo=h.repo,
        branch=branch,
        anchor=anchor,
        until=until,
        files=POLICY_FILES,
    )


def _history_vectors() -> dict[str, Record]:
    v = _history_shape()
    v.update(_history_unavailable())
    v.update(_history_live())
    return {f"history/{k}": spec for k, spec in v.items()}


def _history_shape() -> dict[str, Record]:
    neutral = [
        _Link(A, R, 1, {APPROVERS: V1}),
        _Link(B, A, 2, {APPROVERS: V2}, merged_at="2026-10-12T08:00:00Z", side=S),
        _Link(C, B, 3, {APPROVERS: V2}),
        _Link(D_, C, 4, {APPROVERS: V2, ACTORS: ACTORS_V1}),
    ]
    multi = [_Link(A, R, 1, {}), _Link(X1, A, None, {}), _Link(X2, X1, 2, {})]
    revert = [
        _Link(A, R, 1, {APPROVERS: V1}),
        _Link(B, A, 2, {APPROVERS: V2}),
        _Link(C, B, 3, {APPROVERS: V1}),
    ]
    two = [_Link(A, R, 1, {}), _Link(B, A, None, {})]
    return {
        "merge_squash_neutral": _history(
            "Merge-коммит (PR 2, родители [A, S]) + squash + нейтральное звено (PR 3) ⇒ "
            "активации PR 1, 2, 4",
            _full(_History(), neutral),
            {
                "activations": [
                    _act_of(1, A, {APPROVERS: V1}),
                    _act_of(2, B, {APPROVERS: V2}, "2026-10-12T08:00:00Z"),
                    _act_of(4, D_, {APPROVERS: V2, ACTORS: ACTORS_V1}),
                ]
            },
        ),
        "rebase_multi_commit": _history(
            "Многокоммитный rebase-merge: промежуточный X1 ассоциирован с PR 2, чей "
            "merge_commit_sha — X2 ⇒ rebase_intermediate",
            _chain(_History(), multi).pulls(A, {"number": 1}).pulls(X1, {"number": 2, "merge": X2}),
            {"violation": "rebase_intermediate"},
        ),
        "rebase_multi_commit_unassociated": _history(
            "Многокоммитный rebase-merge, вторая гипотеза об API: у X1 нет ассоциаций ⇒ "
            "commit_without_pr",
            _chain(_History(), multi).pulls(A, {"number": 1}).pulls(X1),
            {"violation": "commit_without_pr"},
        ),
        "rebase_single_commit_accepted": _history(
            "Однокоммитный rebase-merge принят как активация — предел, не нарушение (не "
            "доказательство отсутствия rebase-merge)",
            _full(_History(), [_Link(A, R, 1, {APPROVERS: V1}), _Link(X1, A, 2, {APPROVERS: V2})]),
            {"activations": [_act_of(1, A, {APPROVERS: V1}), _act_of(2, X1, {APPROVERS: V2})]},
        ),
        "direct_push": _history(
            "Прямой push: звено без PR ⇒ commit_without_pr",
            _chain(_History(), two).pulls(A, {"number": 1}).pulls(B),
            {"violation": "commit_without_pr"},
        ),
        "anchor_not_reached": _history(
            "Цепочка прочитана до корня без якоря ⇒ anchor_not_reached",
            _History()
            .repository()
            .branch(C)
            .commit(C, [B], _sha("tC"))
            .commit(B, [R], _sha("tB"))
            .commit(R, [], _sha("tR")),
            {"violation": "anchor_not_reached"},
        ),
        "pr_base_mismatch": _history(
            "PR звена смёржен в другую ветку ⇒ pr_base_mismatch",
            _chain(_History(), [_Link(A, R, 1, {}), _Link(B, A, 2, {})])
            .pulls(A, {"number": 1})
            .pulls(B, {"number": 2, "base": "dev"}),
            {"violation": "pr_base_mismatch"},
        ),
        "revert_bytes": _history(
            "Возврат байтов X → Y → X ⇒ три активации",
            _full(_History(), revert),
            {
                "activations": [
                    _act_of(1, A, {APPROVERS: V1}),
                    _act_of(2, B, {APPROVERS: V2}),
                    _act_of(3, C, {APPROVERS: V1}),
                ]
            },
        ),
        "policy_file_absent": _history(
            "Файл политики установленно отсутствует в полном дереве ⇒ активация без ключа",
            _full(
                _History(),
                [
                    _Link(A, R, 1, {APPROVERS: V1}),
                    _Link(B, A, 2, {APPROVERS: V2}),
                    _Link(C, B, 3, {APPROVERS: V2, MODES: MODES_V1}),
                ],
            ),
            {
                "activations": [
                    _act_of(1, A, {APPROVERS: V1}),
                    _act_of(2, B, {APPROVERS: V2}),
                    _act_of(3, C, {APPROVERS: V2, MODES: MODES_V1}),
                ]
            },
        ),
    }


def _history_unavailable() -> dict[str, Record]:
    def anchored() -> _History:
        return _History().repository().branch(A).commit(A, [R], _sha("tA")).pulls(A, {"number": 1})

    return {
        "branch_not_default": _history(
            "default_branch master ≠ main ⇒ branch_not_default (не нарушение)",
            _History().repository("master"),
            {"unavailable": "branch_not_default"},
        ),
        "commit_404": _history(
            "404 на коммит звена ⇒ http",
            _History().repository().branch(B).commit(B, [A], _sha("tB")).missing(f"commits/{A}"),
            {"unavailable": "http"},
        ),
        "tree_404": _history(
            "Коммит прочитан, 404 на его дереве ⇒ http (не «файлов нет»)",
            anchored().missing(f"git/trees/{_sha('tA')}?recursive=1"),
            {"unavailable": "http"},
        ),
        "tree_truncated": _history(
            "Дерево truncated ⇒ tree_truncated",
            anchored().tree(_sha("tA"), {APPROVERS: V1}, truncated=True),
            {"unavailable": "tree_truncated"},
        ),
    }


def _history_live() -> dict[str, Record]:
    squash = _History()
    squash.raw("h1_repo_policy").raw("h2_branch_policy").raw("h3_commit_root_no_parents")
    squash.raw(
        "h4_commit_pulls_squash",
        request__path=f"repos/{POLICY}/commits/{ROOT_SHA}/pulls?per_page=100",
        response__body__0__merge_commit_sha=ROOT_SHA,
        response__body__0__base__ref="main",
    )
    squash.raw("h5_tree_recursive_policy").raw("h6_blob_policy_approvers")
    root = _History()
    for name in (
        "h1_repo_policy",
        "h2_branch_policy",
        "h3_commit_root_no_parents",
        "h4_commit_pulls_direct_push",
    ):
        root.raw(name)
    side = _History(repo=DEVTOOLS).repository("master")
    side.commit(MERGE_657, [SIDE_657], TREE_657).raw("h3_commit_side_one_parent")
    side.commit(BASE_657, [R], TREE_657).pulls(BASE_657, {"number": 600, "base": "master"})
    side.raw("h4_commit_pulls_side_commit")
    return {
        "live_root_tree_blob": _history(
            "Положительный двойник H1–H3, H5, H6: неизменённые снимки approval-policy (корень, "
            "полное дерево, блоб); H4 — squash-PR, связанный с корнем",
            squash,
            {
                "activations": [
                    {
                        "pr": 636,
                        "merge_commit": ROOT_SHA,
                        "merged_at": "2026-10-26T15:48:49Z",
                        "files_sha256": {APPROVERS: LIVE_APPROVERS_SHA256},
                    }
                ]
            },
            anchor=ROOT_SHA,
        ),
        "live_root_is_commit_without_pr": _history(
            "Неизменённые H1–H4 approval-policy: корень — прямой коммит без PR, якорем даёт "
            "commit_without_pr (bootstrap источника обязан быть PR-мержем)",
            root,
            {"violation": "commit_without_pr"},
            anchor=ROOT_SHA,
        ),
        "rebase_intermediate_live_association": _history(
            "Гипотеза А живьём (h4_commit_pulls_side_commit без изменений): боковой коммит #657 "
            "на first-parent ассоциирован с PR, чей merge_commit_sha другой ⇒ rebase_intermediate",
            side,
            {"violation": "rebase_intermediate"},
            anchor=BASE_657,
            until=MERGE_657,
            branch="master",
        ),
    }


def _activation(title: str, h: _History, merge_commit: str, expect: Record) -> Record:
    return _vector(
        "activation",
        title,
        h.names,
        h.transforms,
        expect,
        repo=h.repo,
        merge_commit=merge_commit,
        files=POLICY_FILES,
    )


def _activation_vectors() -> dict[str, Record]:
    ok = (
        _History()
        .commit(B, [A], _sha("tB"))
        .pulls(B, {"number": 2, "merged_at": "2026-10-12T08:00:00Z"})
    )
    ok.tree(_sha("tB"), {APPROVERS: V2, MODES: MODES_V1}).blob(V2).blob(MODES_V1)
    live = _History(repo=DEVTOOLS).raw("h3_commit_merge_two_parents")
    live.raw("h4_commit_pulls_merge_commit")
    live.raw(
        "h5_tree_recursive_policy",
        request__path=f"repos/{DEVTOOLS}/git/trees/{TREE_657}?recursive=1",
        response__body__sha=TREE_657,
    )
    live.raw("h6_blob_policy_approvers", request__path=f"repos/{DEVTOOLS}/git/blobs/{BLOB_657}")
    return {
        "activation/ok": _activation(
            "PR-мерж ⇒ Activation (approvers.env и modes.yaml)",
            ok,
            B,
            {"activation": _act_of(2, B, {APPROVERS: V2, MODES: MODES_V1}, "2026-10-12T08:00:00Z")},
        ),
        "activation/no_pr": _activation(
            "Коммит без своего PR-мержа ⇒ no_pr_for_commit",
            _History().commit(B, [A], _sha("tB")).pulls(B, {"number": 2, "merge": X2}),
            B,
            {"unavailable": "no_pr_for_commit"},
        ),
        "activation/live_merge_commit": _activation(
            "Положительный двойник H3/H4: неизменённые снимки merge-коммита #657 (два родителя) "
            "и его PR (H4 одностраничный, без Link)",
            live,
            MERGE_657,
            {
                "activation": {
                    "pr": 657,
                    "merge_commit": MERGE_657,
                    "merged_at": T,
                    "files_sha256": {APPROVERS: LIVE_APPROVERS_SHA256},
                }
            },
        ),
    }


# === запись ===================================================================================

BUILDERS: tuple[Callable[[], dict[str, Record]], ...] = (
    _act_vectors,
    _open_vectors,
    _history_vectors,
    _activation_vectors,
)


def build() -> dict[str, bytes]:
    """Все векторы: относительный путь файла → байты (канонический JSON)."""
    out: dict[str, bytes] = {}
    for builder in BUILDERS:
        for name, spec in builder().items():
            path = f"{name}.json"
            if path in out:
                raise ValueError(f"duplicate vector {path}")
            out[path] = _dump(spec)
    return dict(sorted(out.items()))


def _dump(record: Record) -> bytes:
    return (json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


SUBDIRS = frozenset({".", "open_review", "history", "activation"})


def check_out(out: Path) -> None:
    """`out` подменяется целиком: только каталог `vectors`, в котором нет ничего, кроме
    `*.json` в известных подкаталогах; иначе `ValueError` (не удалить чужие файлы)."""
    if out.name != "vectors":
        raise ValueError(f"--out must be a directory named 'vectors': {out}")
    if not out.exists():
        return
    if not out.is_dir():
        raise ValueError(f"--out is not a directory: {out}")
    for path in out.rglob("*"):
        rel = path.relative_to(out)
        if path.is_dir():
            ok = rel.as_posix() in SUBDIRS
        else:
            ok = path.suffix == ".json" and rel.parent.as_posix() in SUBDIRS
        if not ok:
            raise ValueError(f"--out holds a foreign entry, refusing to replace it: {path}")


def write(out: Path, manifest: Path) -> None:
    """Атомарно подменить каталог `out` векторами и записать манифест sha256."""
    check_out(out)
    files = build()
    tmp = out.with_name(f".{out.name}.tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    for path, data in files.items():
        target = tmp / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if out.exists():
        shutil.rmtree(out)
    tmp.rename(out)
    digests = {path: hashlib.sha256(data).hexdigest() for path, data in files.items()}
    manifest.write_bytes(_dump({"vectors": digests}))


def main(argv: list[str] | None = None) -> int:
    """CLI: `--out` — каталог векторов, `--manifest` — файл sha256 (по умолчанию рядом)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--manifest", type=Path, default=None)
    args = parser.parse_args(argv)
    out = args.out.resolve()
    manifest = args.manifest or out.parent / "MANIFEST.json"
    try:
        write(out, manifest.resolve())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
