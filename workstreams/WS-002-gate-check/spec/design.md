---
spec_stage: design
status: draft
version: 1
generated_by: claude@claude-opus-4-8
generated_at: 2026-07-05
source_prompt_version: sha256:pending
validation: pending
approved_by: null
approved_at: null
---

# WS-002: gate-check linter — Technical Design

## Design Principles

### DESIGN-201: Reuse SpecMeta, don't reimplement frontmatter
Читатель состояния артефакта — `split_frontmatter`/`SpecMeta` из spec-runner (`spec.py`), а не свой
парсер. `gate-check` расширяет модель полем `owner_role`. Обновление — по дисциплине вендоринга
(marker + re-copy), как `maestro/_vendor/obs.py`. Трасса: REQ-201, REQ-208.

### DESIGN-202: SpecGraph
Профиль → ориентированный граф: узел `{id, required, owner_role, upstream[], delegate?}`. При
загрузке — топологическая проверка (цикл → config error) и резолв upstream-ссылок. Артефакты
матчатся на узлы по `spec_stage`. Трасса: REQ-201.

### DESIGN-203: Check engine — чистые функции
Каждая проверка — `check(graph, states, git) -> list[Finding]`, где `Finding{severity, artifact,
message, rule_id}`. Проверки: completeness, traceability, upstream_approved, status_git, stale.
Чистые и независимо юнит-тестируемые. Агрегатор собирает findings → exit-код. Трасса: REQ-202..206.

### DESIGN-204: GitFacts adapter (ключ к детерминизму)
Абстракция `GitFacts`: `branch_of(path)`, `approvals(path) -> [(role, handle)]`,
`blob_hash(path, rev)`. Две реализации:
- **live** — `git` + `gh` (или API),
- **injected** — из `facts.json` для `--no-fs` CI.
Проверки зависят только от интерфейса → детерминизм в CI. Трасса: REQ-205, REQ-207.

### DESIGN-205: CLI и exit-коды
`gate-check --profile <name> [SPEC_DIR] [--no-fs facts.json] [--format text|json]`.
`0` чисто · `1` findings · `2` config error. CI: job на каждый PR + branch protection на папки
downstream-артефактов. Трасса: REQ-207.

### DESIGN-206: OSS-интеграция
Presence (completeness) — рулсет **repolinter**; ownership (status_git по ролям) — сверка через
**codeowners-validator**. `gate-check` вызывает их и добавляет то, чего у них нет: traceability,
stale-cascade, status↔git-approval. Трасса: REQ-209.

### DESIGN-207: Stale через approved-blob-hash
При аппруве downstream во frontmatter фиксируется хеш каждого upstream (`traces_to` + hash). Stale =
текущий `blob_hash(upstream)` ≠ зафиксированного. Переиспользует идею `source_prompt_version`
(sha256) spec-runner. Трасса: REQ-206.

## Data Model

```
SpecGraph { nodes: {id: Node}, edges: upstream }
Node      { id, required: bool, owner_role: str, upstream: [id], delegate: str? }
Artifact  { path, meta: SpecMeta+owner_role, traces_to: [id] }
Finding   { severity: error|warn, artifact, message, rule_id }
GitFacts  { branch_of(path), approvals(path), blob_hash(path, rev) }
```

## Component Layout

```
steward/gatecheck/
  graph.py        # SpecGraph, профиль-loader, топо-проверка (REQ-201)
  meta.py         # обёртка над vendored spec-runner SpecMeta + owner_role (DESIGN-201)
  checks.py       # 5 чистых проверок (REQ-202..206)
  git_facts.py    # live + injected адаптеры (DESIGN-204)
  oss.py          # мост в repolinter/codeowners-validator (REQ-209)
  cli.py          # аргументы, агрегатор, exit-коды (REQ-207)
tests/gatecheck/  # фикстуры бандлов + mock GitFacts
```

## Migration / Compatibility
Unmanaged-файлы (без frontmatter) проходят все проверки насквозь (REQ-208) — обратная
совместимость, как в spec-runner.
