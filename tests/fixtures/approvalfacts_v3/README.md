# Живые снимки ответов GitHub для `approval-facts/v3` (M0)

Каталог `live/` снят read-only 2026-10-30 скриптом `scripts/capture_approval_facts_v3_live.py`
(учётка `gh` по умолчанию; только `GET` и GraphQL-query; только **публичные**
`andrei-shtanakov/{devtools, approval-policy}` — steward публичен, снимков приватных репо в
нём нет; публичность каждого репо доказывается живым REST-снимком `pub_*.json` —
`"private": false`, `"visibility": "public"` — **до** любого другого обмена с ним). Обезличены: из заголовков оставлены строка статуса и `Link`; удалены все ключи
`email`, любые ключи с `token`, `signature`/`payload` коммитов и `files[].patch`.

Это **единственный** источник форм ответов (спека §3.8, R-M0-7): фикстуры и векторы
выводятся из этих файлов именованными преобразованиями.

Гарантии захвата (fail-closed): каждый обмен обязан совпасть с объявленным ожиданием —
код выхода `gh`, строка HTTP-статуса, разбираемое JSON-тело, GraphQL `errors` только там, где
они объявлены, и полнота (`pageInfo.hasNextPage == false`, нет `rel="next"`, дерево не
`truncated`; следующая страница объявлена только у P1) (`neg_e2_delivery_absent_pr`: 200 + `errors`; `h5_tree_absent_422`: 422).
Любое расхождение прерывает прогон до записи; файлы пишутся во временный каталог и
подменяют `live/` целиком, поэтому устаревшие снимки не переживают перезахват.
`live/MANIFEST.json` несёт sha256 каждого файла; `tests/approvalfacts/test_capture_live.py`
сверяет манифест с каталогом в обе стороны и байты.

Положительные наборы `pr657_act_sequence` (merge-коммит) и `pr636_act_sequence` (squash) —
полная живая последовательность обменов одного `read_act_facts` с одним реальным путём;
положительные векторы воспроизводят их **без преобразований**. `closed606_act_sequence` —
закрытый несмёрженный PR devtools #606 (REST несёт тестовый `merge_commit_sha`).
`pr603_review_pass` — ревью, поданные на коммиты, отличные от головы PR (`commit.oid ≠
headRefOid`: CHANGES_REQUESTED и APPROVED на прежних пушах devtools #603).
Трёхпутевой `neg_e6_blobs_present_absent_tree` — отдельный отрицательный снимок.

Не снято (нет примеров в разрешённых репо): `ReviewDismissedEvent`, App-мержер, открытый PR,
`compare: ahead|diverged`, удалённый пользователь, rebase-merge — см. Task 13 плана.

Перезапись: `uv run python scripts/capture_approval_facts_v3_live.py --out
tests/fixtures/approvalfacts_v3/live` — затем перегенерировать векторы
(`scripts/gen_approval_facts_v3_vectors.py`) и прогнать тесты.
