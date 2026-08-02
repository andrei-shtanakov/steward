---
spec_stage: behaviour-spec
status: draft
version: 1
owner_role: "@product,@qa"
generated_by: claude@claude-fable-5
generated_at: 2026-08-02
approved_by: null
approved_at: null
traces_to: [requirements]
structural_coverage:
  - fr: FR-02
    constraint: ARCH-C1
    obligation:
      detector: import
      expected_verdict: conformant
      owner_role: "@architects"
      release_gate: block
  - fr: FR-02
    constraint: ARCH-C3
    obligation:
      detector: manual-evidence
      evidence_target: review checklist "collector classifies, never computes" + BEH-02
      owner_role: "@architects"
      release_gate: block
---

# Behaviour Spec — governance-панель бандла (WS-005)

> BEH-сценарии capability; негативные ветки — из реальных классов багов (S1
> merge-gate «нечитаемое выглядит как чистое»). Все `checked_by` — `planned`:
> по двухстадийному гейту этого достаточно до compile-down; материализация —
> задачи workstream'ов (см. 40-decomposition). Структурная половина FR-02 —
> obligation-цепочки во frontmatter (ARCH-C1 import-detector, ARCH-C3
> manual-evidence: постоянный unknown, закрывается review-evidence + BEH-02).

#### BEH-01: Свежий чистый бандл — pass с происхождением `traces: [FR-01, FR-05]`
- **Given**: бандл, все артефакты approved; вердикт-файл валиден, header
  соответствует текущему HEAD и чистому дереву; findings отсутствуют
- **When**: оператор открывает панель
- **Then**: бандл = pass; по каждому артефакту виден статус; рядом —
  generated_at и source_commit из header
- **checked_by**: `status: planned` `kind: e2e` `owner: @qa` `target: dispatcher tests/governance_ui + фикстура verdicts_clean.jsonl (WS-C)`

#### BEH-02: Нет файла — no-data, не pass и не ошибка `traces: [FR-02, FR-01]`
- **Given**: бандл наблюдается; `gate_verdicts.jsonl` отсутствует
- **When**: collector собирает состояние
- **Then**: бандл = no-data; «вердиктов нет» без деградации остальных бандлов;
  состояние ≠ pass
- **checked_by**: `status: planned` `kind: integration` `owner: @qa` `target: dispatcher tests/governance test_missing_file (WS-B)`

#### BEH-03: Битая строка JSON — unreadable, не pass `traces: [FR-03]`
- **Given**: вердикт-файл, в котором строка N — невалидный JSON (negative)
- **When**: collector парсит файл
- **Then**: бандл = unreadable с причиной (строка/класс ошибки); ≠ pass, ≠ no-data
- **checked_by**: `status: planned` `kind: contract` `owner: @qa` `target: фикстура канона verdicts_malformed_line.jsonl (WS-A) + парсер-тест (WS-B)`

#### BEH-04: Неизвестная schema_version — unreadable, не pass `traces: [FR-03]`
- **Given**: header со schema_version, которую вендоренная схема не знает (negative)
- **When**: collector читает header
- **Then**: бандл = unreadable/unsupported с указанием версии; ≠ pass
- **checked_by**: `status: planned` `kind: contract` `owner: @qa` `target: фикстура verdicts_future_schema.jsonl (WS-A) + тест потребителя (WS-B)`

#### BEH-05: Расхождение происхождения — stale, не pass `traces: [FR-04]`
- **Given**: валидный файл, header.source_commit ≠ текущему состоянию spec/
  бандла (negative)
- **When**: collector классифицирует свежесть
- **Then**: бандл = stale с обоими коммитами; ≠ pass
- **checked_by**: `status: planned` `kind: integration` `owner: @qa` `target: dispatcher test_stale_source_commit + mock git-фактов (WS-B)`

#### BEH-06: Нерезолвимый артефакт — не clean `traces: [FR-03, FR-01]`
- **Given**: вердикт ссылается на артефакт-путь, которого в бандле нет (negative)
- **When**: collector резолвит записи
- **Then**: запись = unresolvable; бандл не pass, даже если остальные записи чистые
- **checked_by**: `status: planned` `kind: contract` `owner: @qa` `target: фикстура verdicts_dangling_artifact.jsonl (WS-A) + тест (WS-B)`

#### BEH-07: Findings — блокеры по артефактам `traces: [FR-01]`
- **Given**: gate-check завершился exit 1; файл содержит findings по двум артефактам
- **When**: оператор открывает панель
- **Then**: бандл = blocked; по каждому артефакту — его finding (id + сообщение);
  чистые артефакты показаны отдельно
- **checked_by**: `status: planned` `kind: integration` `owner: @qa` `target: фикстура verdicts_findings.jsonl (WS-A) + collector/UI-тест (WS-B/WS-C)`

#### BEH-08: Ошибка ввода-вывода — unreadable, не пропуск `traces: [FR-03, NFR-02]`
- **Given**: файл существует, но чтение падает: права/лок/усечение (error path)
- **When**: collector читает файл
- **Then**: бандл = unreadable с классом ошибки; ни pass, ни тихий пропуск
  бандла из списка
- **checked_by**: `status: planned` `kind: integration` `owner: @qa` `target: property-тест IO-классов test_io_errors (WS-B)`

#### BEH-09: Панель не мутирует governance-состояние `traces: [FR-01]`
- **Given**: работающая панель
- **When**: оператор взаимодействует с любым элементом governance-раздела
- **Then**: не существует маршрута, изменяющего артефакты, вердикты или
  git-состояние наблюдаемых репо
- **checked_by**: `status: planned` `kind: integration` `owner: @qa` `target: тест перечня маршрутов — только GET (WS-C); структурная половина — ARCH-C2`
