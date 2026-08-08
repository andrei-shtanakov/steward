---
spec_stage: behaviour-spec
status: approved
version: 2
owner_role: product
reviewer_roles: [qa]
generated_by: claude@claude-fable-5
generated_at: 2026-08-02
approved_by: andrei-shtanakov
approved_at: 2026-08-02
upstream_hashes:
  requirements: 9ebae3b7c920d31d84fd9ca10d0ccb259cef93fb
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
      evidence_target: dispatcher:tests/test_governance_collector.py::test_dispatcher_never_imports_steward + review dispatcher#107
      owner_role: "@architects"
      release_gate: block
---

# Behaviour Spec — governance-панель бандла (WS-005)

> BEH-сценарии capability; негативные ветки — из реальных классов багов (S1
> merge-gate «нечитаемое выглядит как чистое»). Все `checked_by` —
> **materialized** (v2, 2026-08-02): цели существуют — фикстуры канона
> `contracts/gate-verdicts/v1` (steward #33) и тесты dispatcher #107/#109;
> кросс-репные ref — в форме `dispatcher:<path>[::<test>]`: без `::<test>`,
> когда проверкой является модуль целиком (live smoke); `::<test>` без
> повторения пути — сокращение внутри одного ref. Структурная половина
> FR-02 — obligation-цепочки во frontmatter (ARCH-C1 import-detector; ARCH-C3
> manual-evidence: вердикт `unknown` постоянен, а release-obligation
> исполняется review-evidence + BEH-02 — не меняя вердикта).

#### BEH-01: Свежий чистый бандл — pass с происхождением `traces: [FR-01, FR-05]`
- **Given**: бандл, все артефакты approved; вердикт-файл валиден, header
  соответствует текущему HEAD и чистому дереву; findings отсутствуют
- **When**: оператор открывает панель
- **Then**: бандл = pass; по каждому артефакту виден статус; рядом —
  generated_at и source_commit из header
- **checked_by**: `status: materialized` `kind: e2e` `owner: @qa` `ref: dispatcher:tests/test_governance_live_smoke.py + dispatcher:tests/test_governance_api.py::test_clean_fixture_in_a_real_repo_is_pass_with_provenance`

#### BEH-02: Нет файла — no-data, не pass и не ошибка `traces: [FR-02, FR-01]`
- **Given**: бандл наблюдается; `gate_verdicts.jsonl` отсутствует
- **When**: collector собирает состояние
- **Then**: бандл = no-data; «вердиктов нет» без деградации остальных бандлов;
  состояние ≠ pass
- **checked_by**: `status: materialized` `kind: integration` `owner: @qa` `ref: dispatcher:tests/test_governance_collector.py::test_missing_file_is_no_data_not_pass_and_not_an_error`

#### BEH-03: Битая строка JSON — unreadable, не pass `traces: [FR-03]`
- **Given**: вердикт-файл, в котором строка N — невалидный JSON (negative)
- **When**: collector парсит файл
- **Then**: бандл = unreadable с причиной (строка/класс ошибки); ≠ pass, ≠ no-data
- **checked_by**: `status: materialized` `kind: contract` `owner: @qa` `ref: contracts/gate-verdicts/v1/fixtures/malformed_line.jsonl + dispatcher:tests/test_governance_collector.py::test_malformed_line_is_unreadable_with_the_line_number`

#### BEH-04: Неизвестная schema_version — unreadable, не pass `traces: [FR-03]`
- **Given**: header со schema_version, которую вендоренная схема не знает (negative)
- **When**: collector читает header
- **Then**: бандл = unreadable/unsupported с указанием версии; ≠ pass
- **checked_by**: `status: materialized` `kind: contract` `owner: @qa` `ref: contracts/gate-verdicts/v1/fixtures/future_schema.jsonl + dispatcher:tests/test_governance_collector.py::test_future_schema_version_is_unreadable_naming_the_version`

#### BEH-05: Расхождение происхождения — stale, не pass `traces: [FR-04]`
- **Given**: валидный файл, header.source_commit ≠ текущему состоянию spec/
  бандла (negative)
- **When**: collector классифицирует свежесть
- **Then**: бандл = stale с обоими коммитами; ≠ pass
- **checked_by**: `status: materialized` `kind: integration` `owner: @qa` `ref: dispatcher:tests/test_governance_collector.py::test_source_commit_mismatch_is_stale_with_both_commits + ::test_real_git_facts_fresh_then_stale`

#### BEH-06: Нерезолвимый артефакт — не clean `traces: [FR-03, FR-01]`
- **Given**: вердикт ссылается на артефакт-путь, которого в бандле нет (negative)
- **When**: collector резолвит записи
- **Then**: запись = unresolvable; бандл не pass, даже если остальные записи чистые
- **checked_by**: `status: materialized` `kind: contract` `owner: @qa` `ref: contracts/gate-verdicts/v1/fixtures/dangling_artifact.jsonl + dispatcher:tests/test_governance_collector.py::test_dangling_finding_is_unresolvable_not_pass_not_blocked`

#### BEH-07: Findings — блокеры по артефактам `traces: [FR-01]`
- **Given**: gate-check завершился exit 1; файл содержит findings по двум артефактам
- **When**: оператор открывает панель
- **Then**: бандл = blocked; по каждому артефакту — его finding (id + сообщение);
  чистые артефакты показаны отдельно
- **checked_by**: `status: materialized` `kind: integration` `owner: @qa` `ref: dispatcher:tests/test_governance_collector.py::test_findings_classify_as_blocked_with_findings_exposed + dispatcher:tests/test_governance_api.py::test_findings_fixture_is_blocked_with_findings`

#### BEH-08: Ошибка ввода-вывода — unreadable, не пропуск `traces: [FR-03, NFR-02]`
- **Given**: файл существует, но чтение падает: права/лок/усечение (error path)
- **When**: collector читает файл
- **Then**: бандл = unreadable с классом ошибки; ни pass, ни тихий пропуск
  бандла из списка
- **checked_by**: `status: materialized` `kind: integration` `owner: @qa` `ref: dispatcher:tests/test_governance_collector.py::test_every_io_error_class_is_unreadable_never_pass_never_silent`

#### BEH-09: Панель не мутирует governance-состояние `traces: [FR-01]`
- **Given**: работающая панель
- **When**: оператор взаимодействует с любым элементом governance-раздела
- **Then**: не существует маршрута, изменяющего артефакты, вердикты или
  git-состояние наблюдаемых репо
- **checked_by**: `status: materialized` `kind: integration` `owner: @qa` `ref: dispatcher:tests/test_governance_api.py::test_governance_surface_is_get_only`
