# Живая приёмка I4 — агентский мерж стал отличим (2026-08-20)

Предмет проверки — не диф, а **кто окажется в `PullRequest.mergedBy`**. До этого прогона
выполнение I4 было утверждением о коде; здесь оно становится фактом с форджа.

## Пины

- steward: master `957b340` на момент финального прогона (workflow из PR #74 + фикс PR #76)
- workflow: `.github/workflows/merge-broker.yml`, действие `actions/create-github-app-token`
  пинуется по SHA `bcd2ba49218906704ab6c1aa796996da409d3eb1` (v3.2.0)
- личность: GitHub App `merge-broker`, App ID `4649696`, bot-аккаунт id `318665382`
- ruleset `Default Branch Restriction` (18760464): App внесён в bypass `Integration:4649696:always`,
  ruleset обновлён 2026-08-20T15:23:03+04:00
- предмет мержа: PR #75 — одна строка в `TODO.md`, изменение намеренно неавторитетное

## Результат

| | PR #74 (человек) | PR #75 (агент) |
|---|---|---|
| merge commit | `e04b0c9` | `2214579` |
| `merged_by.login` | `andrei-shtanakov` | `merge-broker[bot]` |
| `merged_by.type` | `User` | `Bot` |
| GraphQL `mergedBy.__typename` | `User` | `Bot` |
| `mergedAt` | — | 2026-08-20T11:30:08Z |

`approval-facts.json` — вывод `steward approval-facts --repo andrei-shtanakov/steward
--prs 75,74`, то есть классификация **собственным fact-провайдером steward**, а не пересказ
ответа GitHub: два мержа различены по `identity` и `type_hint`.

## Четыре прогона, три из них красные

1. `32358374337` — токен выпущен (**App установлен** — до этого нигде не подтверждалось),
   семь предусловий прошли, GitHub отказал: App не был в bypass.
2. `32363600466` — уже с bypass, тот же отказ. Причина оказалась не в bypass:
   **`gh pr merge` проверяет `mergeStateStatus` на своей стороне** и при `BLOCKED`
   отказывает, не обращаясь к merge-эндпоинту, — bypass App'а не проверялся ни разу.
   Сообщение «the base branch policy prohibits the merge» принадлежит gh, не GitHub;
   подсказки про `--auto`/`--admin` в тексте это и выдали. Починка — PR #76:
   прямой `PUT /pulls/{n}/merge` с `sha` вместо `--match-head-commit`.
3. `32364028210` — `ОТКАЗ: mergeable=UNKNOWN`. Мерж PR #76 сдвинул master, GitHub сбросил
   вычисленную mergeability и не успел пересчитать. Отказ **правильный**: неизвестность не
   является разрешением. Повторяемо после любого сдвига base-ветки — см. follow-up в TODO.
4. `32364093143` — success.

## Что доказано и что нет

Доказана **различимость**: агентский мерж и человеческий различаются каноническим
источником (`PullRequest.mergedBy`), и различает их steward, а не глаз читателя.
Это снимает препятствие, зафиксированное замером prograph-vault PR #78.

**I4 целиком этим не выполнен.** По ADR-ECO-004 I4 — «Revocation needs a detection loop»:
нужен наблюдатель, видящий агентские мержи. Различимость это его предпосылка, а не он сам;
наблюдатель — `todo://dispatcher/agent-merge-observability`, и он не сделан. D1 остаётся
выключенным, `agent_merge_allowed: false`.

Два дефекта, найденные этим прогоном и не выводимые из кода, — в TODO как follow-up:
строка личности в фактах (`github:merge-broker`, **без** `[bot]`) и одноразовость запуска
при `mergeable=UNKNOWN`.
