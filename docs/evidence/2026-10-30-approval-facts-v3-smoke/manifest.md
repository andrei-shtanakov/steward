# Живой смоук чтения `approval-facts/v3` (M0 Task 13, 2026-10-31)

Чек-лист — строки таблицы спеки steward §8 с пометкой «смоук», которые **не** сняты живьём
30.10 (план, Task 13, пп. 1–9). Прогон только читает: REST `GET` и GraphQL-query, без
записей в GitHub. Цели — уже существующие PR и ветки публичных репо из §3.8. Новых целей
смоук не создавал. Неудача смоука — находка (`contradicts`), а не правка ожиданий
библиотеки.

## Пины и окружение

- steward: ветка `feat/approval-facts-v3-t13` от master `0c2748b6b4c6857dd444b8fb3479cfcf519e9fd2`
  (M0 Tasks 1–12 влиты). Прогон шёл по этому SHA плюс ещё не закоммиченный скрипт; его
  байты совпадают с закоммиченными, sha256 `scripts/approval_facts_v3_smoke.py` —
  `0b64064a543628de08a35f4287bd3a7955f2f99225a3b61f5f604f3531da0833`.
- Аутентифицированное чтение — `GhTransport(~/.config/review)`, учётка `ai-prosto`
  (сверено: `viewer.login` в отчёте). `gh` 2.101.0, Python 3.12.13.
- Анонимное чтение — `AnonTransport` скрипта: прямой HTTPS `GET` на `api.github.com`
  без каких-либо credentials. Это реализация протокола из `docs/approval-facts-v3.md`,
  раздел «собственный анонимный REST-транспорт».
- Время прогона: 2026-10-31T12:35:47Z.

## Команды

```sh
perl -e 'alarm 590; exec @ARGV' -- \
  uv run python scripts/approval_facts_v3_smoke.py --profile ~/.config/review > report.json
# exit 0 (код 1 был бы при любой строке contradicts)
```

Отчёт целиком — `report.json` рядом. Он содержит вердикты, номера PR, SHA, HTTP-статусы и
остатки лимита, без тел ответов. Оба репо публичные.

## Цели

| Репо | Что читалось |
|---|---|
| `andrei-shtanakov/devtools` (публичный) | обход всех PR (547 узлов, GraphQL `StewardV3SmokeScan`): отзывы, мержеры, состояния, авторы, PENDING; `compare` для пп. 4 и 8; ветка `feat/criteria-closure-v1-parity`; закрытый несмёрженный #606 |
| `andrei-shtanakov/approval-policy` (публичный) | H1–H6 анонимно, `read_policy_history` через анонимный транспорт |

Приватные стенды не читались (п. 1: запись не разрешена владельцем).

## Исход по чек-листу Task 13

| # | Пункт | Исход | Основание |
|---|---|---|---|
| 1 | `ReviewDismissedEvent`: форма, `previousReviewState`, число событий = числу ревью в `DISMISSED` | **not_verified** | запись на стенде не разрешена владельцем. В devtools отзывов нет: 547 PR, ни одного события и ни одного ревью в `DISMISSED` |
| 2 | App-мержер: REST `slug[bot]`/`Bot`, GraphQL `slug`/`Bot` | **not_verified** | среди 547 PR devtools нет мержа App: все мержеры — `User` |
| 3 | Открытый PR (`open`/`OPEN`, `read_open_review`) | **not_verified** | открытых PR в devtools на момент прогона нет (в approval-policy PR нет вовсе) |
| 4a | `compare` → `ahead` | **verified** | `compare/9da14cf…...aa4e7e3…` (родитель → голова `master`): HTTP 200, `status: "ahead"` |
| 4b | `compare` → `diverged` | **verified** | `compare/master...a038c21…` (голова несмёрженного #606): HTTP 200, `status: "diverged"` |
| 5 | Удалённый пользователь (`null`/`ghost`) | **not_verified** | среди авторов PR, авторов ревью и мержеров 547 PR нет ни `null`, ни `ghost`, ни типа вне `User`/`Bot` |
| 6 | Видимость чужого PENDING-ревью | **not_verified** | в обходе 0 PENDING-ревью. Без второй учётки нельзя отличить «чужих PENDING нет» от «чужие PENDING не видны» |
| 7 | Анонимный транспорт на `approval-policy`: H1–H6 без токена, остаток лимита | **verified** | H1–H6 — все HTTP 200 без токена. `read_policy_history(main, anchor=head 12d198f…)` → `HistoryViolation: commit_without_pr`, это не `Unavailable`. `x-ratelimit-remaining` 59 → 50 за 10 запросов |
| 8 | `compare` с `/` в имени базы (`%2F`) | **verified** | база `feat/criteria-closure-v1-parity`, голова `aa4e7e3…`. Закодированная (`feat%2Fcriteria-closure-v1-parity`) и незакодированная формы дают одинаковый ответ: HTTP 200, `("diverged", ahead_by 756, behind_by 2, merge_base 069d154…)`. Так через `gh`, и так через анонимный `urllib`, который шлёт `%2F` на провод как есть |
| 9 | Ассоциации промежуточного коммита rebase-merge, `merge_commit_sha` однокоммитного rebase-merge, ассоциации вне ветки по умолчанию | **not_verified** | вне смоука: стенд devtools, Task 18 №28 |

Итого: verified 4, not_verified 6, contradicts 0.

## Таблица спеки §8 — сводка по строкам

| Строка §8 | Статус после смоука |
|---|---|
| Форма ответов, отказы, пагинация, два прохода, срез T | векторы; живого подтверждения строка не требует |
| `previousReviewState` присутствует у отзывов | **не проверено** (п. 1); исход при `null` — отказ `dismissal_without_previous_state` |
| Полнота `REVIEW_DISMISSED_EVENT` | **не проверено** (п. 1); при пропуске — отказ `dismissed_without_event` |
| REST `head.sha` = GraphQL `headRefOid` | проверено живьём 30.10 (#657, #636) |
| `merged_by` App и User | User — проверено 30.10; App — **не проверено** (п. 2) |
| Удалённый пользователь: `null`/`ghost` | **не проверено** (п. 5); при любой форме — `actor_unresolved` |
| `MergedEvent.commit.oid` = `mergeCommit.oid` | проверено живьём 30.10 |
| `headRefOid`/`baseRefName` проходов = доставке | проверено живьём 30.10 |
| `compare` с веткой, закодированной `quote(safe="")` | **проверено живьём 31.10** (п. 8) |
| `compare` даёт `behind`/`identical` | проверено живьём 30.10; `ahead`/`diverged` — словарь форджа проверен живьём 31.10 (п. 4) |
| `commits/{sha}/pulls` на ветке по умолчанию | проверено живьём 30.10 |
| `commits/{sha}/pulls` для промежуточного коммита rebase-merge | **не проверено**, вне смоука (п. 9) |
| `commits/{sha}/pulls` вне ветки по умолчанию | **не проверено**, вне смоука (п. 9); ограничение О11 остаётся |
| `merge_commit_sha` однокоммитного rebase-merge | **не проверено**, вне смоука (п. 9) |
| `git/trees/{sha}?recursive=1` полон | проверено живьём 30.10 |
| Анонимный REST: доступность и остаток лимита | **проверено живьём 31.10** (п. 7) |
| Видимость PENDING-ревью чужому токену | **не проверено** (п. 6) |

## Оговорки к verified

- **П. 4** доказывает, что живой E5 отдаёт `ahead` и `diverged` из словаря §3.8. Сквозного
  `read_act_facts` с `in_base = false` нет: в разрешённых репо нет смёрженного PR, чей
  merge-коммит лежит вне базы. Сведение статуса к `in_base` покрыто векторами.
- **П. 7**: approval-policy по-прежнему состоит из одного корневого прямого коммита без PR
  (как в наблюдении §3.8). Поэтому `read_policy_history` останавливается на H4 с
  `commit_without_pr`, а H5 и H6 прочитаны отдельными анонимными запросами. Первый
  прогон того же дня (до финальной правки п. 8) дал те же исходы; лимит в нём упал
  59 → 48 за 12 запросов. Окно анонимного лимита — по IP, так что цифры между прогонами
  не складываются.

## Находки

`contradicts` нет. Гипотеза §3.2 «Пути REST» о `%2F` в сегменте ветки подтверждена:
заявка с базой `<x>/<y>` не превращается в `Unavailable: http`.
