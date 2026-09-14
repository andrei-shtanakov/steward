# Перематериализация `approval-facts/v2` под текущей политикой + охват WS-005 (2026-09-14)

Пункт `approval-facts-policy-digest-refresh` (TODO §10). Предмет — не код, а
**живое состояние** release-стадии догфуда WS-005: до этого прогона она была
красной по `GC-APPROVAL-MISSING` с причиной «observation was produced under a
DIFFERENT classification policy» (дайджест `sha256:7ad8ec72…` от 2026-08-21/23
против текущих байт `profiles/approval-policy.yaml`, изменённых 2026-08-31,
ADR-ECO-011 — `agent_merge_allowed: true`).

## Пины

- steward: ветка `fix/approval-facts-scope-ws005` от master `09c7926`; рабочее дерево — только правка `profiles/approval-facts-scope.yaml`
- политика: `profiles/approval-policy.yaml`, sha256 `4b6d40875b0efa9a3f522695e878538b0369b6a4d82a60a5f67001f5115026fd`
- источник фактов gate-check: `<repo-root>/.steward/approval_facts.jsonl` (локальный, gitignored; lease 24 ч)
- `gh` 2.98.0, аутентифицирован как `andrei-shtanakov`

## Шаг 1 — перематериализация со СТАРЫМ охватом (диагноз)

`uv run python scripts/collect_approval_facts.py --workspace-root ..` → `published: 1`,
заголовок получил текущий дайджест `sha256:4b6d40875b0efa9a3f522695e878538b0369b6a4d82a60a5f67001f5115026fd`. Release-стадия осталась
красной, но с другой причиной — на всех шести артефактах:
`merge 02840df3… / cde0a007… is outside the declared observation scope — actor is unknown regardless of the file's age`.
Это мержи PR #55 (2026-08-08) и #60 (2026-08-09), которыми аппрувнуты артефакты
бандла; в охвате A0 (`prs: [74, 75, 90, 92, 93, 94, 95]`) их не было.
Вывод: одной перематериализации мало, охват обязан покрывать сам догфуд.

## Шаг 2 — охват + перематериализация

Правка `profiles/approval-facts-scope.yaml`: `prs: [55, 60, 74, 75, 90, 92, 93, 94, 95]`
(с комментарием-доводом). Команда та же. Вывод:

```
published: 1
  andrei-shtanakov/steward: /Users/Andrei_Shtanakov/labs/all_ai_orchestrators/steward/.steward/approval_facts.jsonl
```

Заголовок опубликованного файла (копия — `approval_facts.jsonl` рядом):

```
{'generated_at': '2026-09-14T19:12:20Z', 'valid_until': '2026-09-15T19:12:20Z', 'policy_version': 1, 'policy_digest': 'sha256:4b6d40875b0efa9a3f522695e878538b0369b6a4d82a60a5f67001f5115026fd', 'complete': True, 'scope_sha256': 'sha256:398065b2723e2ca4f2e25e29b4201bb622393463b917783b87f4016c4410fd74'}
scope: [55, 60, 74, 75, 90, 92, 93, 94, 95]
```

## Шаг 3 — догфуд

`uv run gate-check --profile team-exp --stage release workstreams/WS-005-gate-verdicts/spec/`:

```
gate-check[live]: 0 error(s), 0 warning(s)
```

`uv run gate-check --profile team-exp workstreams/WS-005-gate-verdicts/spec/` (authoring): `gate-check[live]: 0 error(s), 0 warning(s)`

## Оговорка

Файл фактов — локальный артефакт с lease 24 ч: release-стадия зелёная только
пока host-local расписание A0 (`scripts/approval-facts-schedule.md`) продолжает
собирать факты. Это свойство Stage A0 по построению, не дефект данного шага.
