# Живые снимки ответов GitHub для `approval-facts/v3` (M0)

Сняты read-only 2026-10-30 скриптом `scripts/capture_approval_facts_v3_live.py`
(учётка `gh` по умолчанию; только `GET` и GraphQL-query; только
`andrei-shtanakov/{devtools, approval-policy, df-roles-probe-private}`).
Обезличены: из заголовков оставлены строка статуса и `Link`; удалены все ключи `email`,
любые ключи с `token`, `signature`/`payload` коммитов и `files[].patch`.

Это **единственный** источник форм ответов (спека §3.8, R-M0-7): фикстуры и векторы
выводятся из этих файлов именованными преобразованиями. Перечень снимков и строк таблицы —
`MANIFEST.json` и §3.8 спеки `docs/superpowers/specs/2026-10-30-approval-facts-v3-design.md`.

Положительные наборы `pr657_act_sequence` (merge-коммит) и `pr636_act_sequence` (squash) —
полная живая последовательность обменов одного `read_act_facts` с одним реальным путём;
положительные векторы воспроизводят их **без преобразований**. Трёхпутевой
`neg_e6_blobs_present_absent_tree` — отдельный отрицательный снимок.

Не снято (нет примеров в разрешённых репо): `ReviewDismissedEvent`, App-мержер, открытый PR,
`compare: ahead|diverged`, удалённый пользователь, rebase-merge — см. Task 13 плана.

Перезапись: `uv run python scripts/capture_approval_facts_v3_live.py --out
tests/fixtures/approvalfacts_v3/live` — затем сверить, что векторы перегенерированы
(`scripts/gen_approval_facts_v3_vectors.py`) и тесты зелёные.
