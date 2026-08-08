# Gate-ID Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `profiles/gate-catalog.yaml` — SSOT стабильных `gate_id` и каталог правил obligation (`@id:gate-id-catalog`), с активацией поля `obligation` на границе эмиссии verdicts.

**Architecture:** Governance data по образцу `roles.yaml` (version, шапка-политика, PR-only) + загрузчик `src/steward/gatecatalog.py` с fail-closed валидацией + три гарантии полноты (emitter-гейт на `status: active`, AST-тест по конструкторам `Finding(...)`, обратная сверка active-множества) + эмиттер пишет `obligation` из каталога. Дизайн D1–D7 утверждён владельцем 2026-08-08 с четырьмя поправками — они в Global Constraints.

**Tech Stack:** Python 3.12, PyYAML (уже в зависимостях steward — gate-check читает YAML), pytest, dataclasses; ruff line 100; pyrefly.

## Global Constraints (дизайн-решение владельца 2026-08-08, дословно)

- **applicable_roles**: поле ОТСУТСТВУЕТ → role-agnostic; непустой список → ограничение по ролям (каждый слаг обязан резолвиться в `profiles/roles.yaml`); пустой список `[]` → ошибка валидации (два канонических представления одного состояния запрещены). У `GC-APPROVAL-MISSING` поле опущено.
- **Lifecycle**: каждая запись несёт `status: active | declared | deprecated`. `active` — код может эмитить; `declared` — id зарезервирован принятой работой, эмиттера нет; `deprecated` — tombstone с обязательными `since` и (`replaced_by: <id>` XOR `replacement: none`). Исключения sync-теста — часть ДАННЫХ (статусы), не скрытый allowlist в тесте.
- **Состав v1**: 19 фактически эмитящихся id — `active`, `obligation: quality`; `GC-APPROVAL-MISSING` — `declared`, `obligation: approval`; **`GC-APPROVAL-ROLE` НЕ заводить** (граница с существующим `GC-GIT-ROLE` требует отдельного решения; ранняя резервация закрепила бы ложное раздвоение). Словарь `obligation: [quality, approval]`.
- **Три гарантии полноты (D5b в редакции владельца)**: (1) эмиттер перед записью обязан найти каждый `finding.rule_id` в каталоге со статусом `active`; неизвестный или `declared` id → `EmitError`, файл НЕ публикуется; (2) статический тест извлекает ТОЛЬКО строковый аргумент `rule_id` из конструкторов `Finding(...)` — НЕ сырой grep/AST по строкам `"GC-*"` (в коде есть префикс `GC-ARCH-`, не являющийся gate id — сырой скан даст ложный 20-й); динамический rule_id требует явного registry/обоснования; (3) обратная проверка: каждый `active` id достижим из кода; неиспользуемыми могут быть только `declared`/`deprecated`.
- **Стадии**: закрытый `stage_vocabulary: [authoring, release]` объявляется В ШАПКЕ gate-catalog (НЕ безоговорочная ссылка на arch-policy — это политика архитектурного гейта, каталог общий); отдельный тест: ключи `stages:` в `profiles/arch-policy.yaml` ⊆ `stage_vocabulary` каталога.
- **Активация obligation** — в этом workstream (вертикальный срез: каталог = authority на границе эмиссии); `tier/phase/risk_model_version/waiver_ref` — риск-интеграция, ВНЕ скоупа.
- **D7**: оба потребителя (dispatcher, Maestro WS-006) — inbox-issues по ADR-ECO-006 ПОСЛЕ мержа; «handoff заметкой» недостаточно.
- Правки контракта: `contracts/gate-verdicts/v1/SCHEMA.json` НЕ меняется (obligation объявлен); README контракта обновить (фраза «producer их пока не эмитит» устаревает для obligation) — консюмерские drift-advisory могут сработать, это ожидаемо и называется в PR.
- Репо-дисциплина steward: uv only; ruff line 100; `uv run pyrefly check` после правок; PR-only, Copilot-ревью; коммиты с трейлером `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Фактические якоря (проверены 2026-08-08)

- `Finding` — frozen dataclass `src/steward/gatecheck/checks.py:35`: `severity, rule_id, artifact, message`.
- Эмиттер — `src/steward/verdicts/emitter.py`: `EmitError` есть; `"gate_id": finding.rule_id` на строке ~111; файл переписывается целиком за прогон (ARCH-D4).
- 19 эмитящихся id (grep литералов в `src/steward/`): GC-ARCH-CONFORMANCE, GC-ARCH-EVIDENCE, GC-ARCH-SCHEMA, GC-BEH-COVERAGE, GC-BEH-TRACE, GC-CHECK-PLANNED, GC-COMPILE, GC-COMPLETENESS, GC-DUP, GC-GIT-BRANCH, GC-GIT-ROLE, GC-META, GC-STAGE, GC-STALE, GC-STALE-KEY, GC-STALE-UNPINNED, GC-TRACE, GC-TRACE-EMPTY, GC-UPSTREAM.
- `profiles/roles.yaml`: `slug_pattern: "^[a-z][a-z0-9-]{1,31}$"`, `roles: [{slug, display}, ...]` (product, architects, qa, tech-lead, stream-owner, owner).
- `profiles/arch-policy.yaml`: `stages:` → ключи `authoring`, `release`.

## File Structure

- Create: `profiles/gate-catalog.yaml` — данные каталога.
- Create: `src/steward/gatecatalog.py` — модель + загрузчик + валидация (один модуль, по образцу компактных загрузчиков steward).
- Modify: `src/steward/verdicts/emitter.py` — catalog-гейт + эмиссия `obligation`.
- Modify: `src/steward/gatecheck/cli.py` — прокинуть каталог в эмиттер (место вызова `emit_verdicts`).
- Modify: `contracts/gate-verdicts/v1/README.md` — строка про активацию obligation.
- Modify: `TODO.md` — `[x]` gate-id-catalog; новый пункт `approval-policy-enforcement`.
- Test: `tests/gatecatalog/test_loader.py`, `tests/gatecatalog/test_catalog_data.py`, `tests/gatecatalog/test_sync_with_code.py`, дополнения в `tests/verdicts/` (или где живут тесты эмиттера — свериться на месте).

---

### Task 1: Загрузчик каталога — модель и fail-closed валидация

**Files:**
- Create: `src/steward/gatecatalog.py`
- Test: `tests/gatecatalog/test_loader.py` (+ пустой `tests/gatecatalog/__init__.py`, если конвенция тестов steward его требует — свериться с соседними каталогами tests/)

**Interfaces:**
- Produces:
  - `class CatalogError(ValueError)` — единственный тип ошибок валидации, сообщение всегда называет gate_id/поле.
  - `@dataclass(frozen=True) GateEntry`: `gate_id: str`, `obligation: str`, `status: Literal["active","declared","deprecated"]`, `title: str | None = None`, `stages: tuple[str, ...] | None = None` (None = все стадии), `applicable_roles: tuple[str, ...] | None = None` (None = role-agnostic), `since: str | None = None`, `replaced_by: str | None = None`, `replacement_none: bool = False`.
  - `@dataclass(frozen=True) GateCatalog`: `version: int`, `obligation_vocabulary: tuple[str, ...]`, `stage_vocabulary: tuple[str, ...]`, `gates: dict[str, GateEntry]`; методы `active_ids() -> frozenset[str]`, `entry(gate_id: str) -> GateEntry | None`.
  - `load_catalog(catalog_path: Path, roles_path: Path) -> GateCatalog` — парсит YAML, валидирует, кидает `CatalogError`.
- Правила валидации (все — отдельные тесты): `version` — int ≥ 1; словари непустые; id матчит `^GC-[A-Z0-9]+(-[A-Z0-9]+)*$`; `obligation` ∈ obligation_vocabulary; `stages` (если есть) непусты и ⊆ stage_vocabulary; `applicable_roles` отсутствует → None, `[]` → CatalogError («пустой список запрещён — опусти поле»), непустой → каждый слаг ∈ roles.yaml; `status` ∈ enum; `deprecated` ⇒ `since` обязателен и ровно одно из `replaced_by: <существующий-в-каталоге id>` / `replacement: none`; не-deprecated ⇒ since/replaced_by/replacement запрещены; неизвестные ключи записи → CatalogError (закрытая форма, как additionalProperties: false).

- [ ] **Step 1: Тесты загрузчика** — `tests/gatecatalog/test_loader.py`; каждый кейс строит YAML-строку во временном файле хелпером:

```python
"""Загрузчик gate-catalog: fail-closed валидация каталога правил obligation.

Каждое правило валидации из дизайна (владелец, 2026-08-08) — отдельный тест;
особо: пустой applicable_roles — ОШИБКА, не синоним отсутствия (два
канонических представления одного состояния запрещены).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from steward.gatecatalog import CatalogError, load_catalog

ROLES = (
    'version: 1\nslug_pattern: "^[a-z][a-z0-9-]{1,31}$"\n'
    "roles:\n  - {slug: qa, display: QA}\n  - {slug: owner, display: Owner}\n"
)

HEADER = (
    "version: 1\n"
    "obligation_vocabulary: [quality, approval]\n"
    "stage_vocabulary: [authoring, release]\n"
)


def _load(tmp_path: Path, gates_yaml: str, header: str = HEADER):
    catalog = tmp_path / "gate-catalog.yaml"
    catalog.write_text(header + "gates:\n" + gates_yaml)
    roles = tmp_path / "roles.yaml"
    roles.write_text(ROLES)
    return load_catalog(catalog, roles)


def test_minimal_active_entry_loads(tmp_path):
    cat = _load(tmp_path, "  GC-TRACE:\n    obligation: quality\n    status: active\n")
    assert cat.version == 1
    assert cat.active_ids() == frozenset({"GC-TRACE"})
    entry = cat.entry("GC-TRACE")
    assert entry is not None
    assert entry.applicable_roles is None and entry.stages is None


def test_declared_entry_is_not_active(tmp_path):
    cat = _load(
        tmp_path,
        "  GC-APPROVAL-MISSING:\n    obligation: approval\n    status: declared\n",
    )
    assert cat.active_ids() == frozenset()
    assert cat.entry("GC-APPROVAL-MISSING").status == "declared"


def test_empty_applicable_roles_is_error_not_agnostic(tmp_path):
    with pytest.raises(CatalogError, match="applicable_roles"):
        _load(
            tmp_path,
            "  GC-GIT-ROLE:\n    obligation: quality\n    status: active\n"
            "    applicable_roles: []\n",
        )


def test_applicable_roles_must_resolve_in_roles_catalog(tmp_path):
    with pytest.raises(CatalogError, match="ghost"):
        _load(
            tmp_path,
            "  GC-GIT-ROLE:\n    obligation: quality\n    status: active\n"
            "    applicable_roles: [ghost]\n",
        )


def test_obligation_outside_vocabulary_rejected(tmp_path):
    with pytest.raises(CatalogError, match="obligation"):
        _load(tmp_path, "  GC-X:\n    obligation: risk\n    status: active\n")


def test_stages_outside_vocabulary_rejected(tmp_path):
    with pytest.raises(CatalogError, match="stages"):
        _load(
            tmp_path,
            "  GC-X:\n    obligation: quality\n    status: active\n"
            "    stages: [shipping]\n",
        )


def test_bad_gate_id_grammar_rejected(tmp_path):
    with pytest.raises(CatalogError, match="gate_id"):
        _load(tmp_path, "  gc-lower:\n    obligation: quality\n    status: active\n")


def test_deprecated_requires_since_and_exactly_one_replacement(tmp_path):
    with pytest.raises(CatalogError, match="deprecated"):
        _load(tmp_path, "  GC-OLD:\n    obligation: quality\n    status: deprecated\n")
    cat = _load(
        tmp_path,
        "  GC-NEW:\n    obligation: quality\n    status: active\n"
        "  GC-OLD:\n    obligation: quality\n    status: deprecated\n"
        '    since: "2026-08-08"\n    replaced_by: GC-NEW\n',
    )
    assert cat.entry("GC-OLD").replaced_by == "GC-NEW"
    with pytest.raises(CatalogError, match="replaced_by"):
        _load(
            tmp_path,
            "  GC-OLD:\n    obligation: quality\n    status: deprecated\n"
            '    since: "2026-08-08"\n    replaced_by: GC-GHOST\n',
        )


def test_unknown_entry_key_rejected(tmp_path):
    with pytest.raises(CatalogError, match="tier"):
        _load(
            tmp_path,
            "  GC-X:\n    obligation: quality\n    status: active\n    tier: high\n",
        )
```

- [ ] **Step 2: Прогнать — падают** (`ModuleNotFoundError: steward.gatecatalog`). Команда: `uv run pytest tests/gatecatalog/ -q`.
- [ ] **Step 3: Реализация `src/steward/gatecatalog.py`** — докстринг модуля обязан фиксировать политику стабильности (id не переименовывается/не переиспользуется; вывод из оборота = deprecated-tombstone; изменение состава = бамп version) и правило applicable_roles. Реализация прямая: `yaml.safe_load`, набор приватных `_check_*`, сборка frozen-датаклассов. Роли читать из roles.yaml (`{r["slug"] for r in data["roles"]}`).
- [ ] **Step 4: Прогнать — зелёные**; `uv run ruff format . && uv run ruff check .` и `uv run pyrefly check` чистые.
- [ ] **Step 5: Commit** — `feat(catalog): загрузчик gate-catalog с fail-closed валидацией`.

---

### Task 2: Данные каталога v1 + сверка с roles/arch-policy

**Files:**
- Create: `profiles/gate-catalog.yaml`
- Test: `tests/gatecatalog/test_catalog_data.py`

**Interfaces:**
- Consumes: `load_catalog` из Task 1.
- Produces: реальный каталог, который загружается без ошибок; константы путей в тестах: `PROFILES = Path(__file__).resolve().parents[2] / "profiles"`.

- [ ] **Step 1: Тесты по реальным данным**

```python
"""Реальный gate-catalog.yaml: состав v1 и сверка с соседними словарями."""

from __future__ import annotations

from pathlib import Path

import yaml

from steward.gatecatalog import load_catalog

PROFILES = Path(__file__).resolve().parents[2] / "profiles"

EXPECTED_ACTIVE = {
    "GC-ARCH-CONFORMANCE", "GC-ARCH-EVIDENCE", "GC-ARCH-SCHEMA",
    "GC-BEH-COVERAGE", "GC-BEH-TRACE", "GC-CHECK-PLANNED", "GC-COMPILE",
    "GC-COMPLETENESS", "GC-DUP", "GC-GIT-BRANCH", "GC-GIT-ROLE", "GC-META",
    "GC-STAGE", "GC-STALE", "GC-STALE-KEY", "GC-STALE-UNPINNED", "GC-TRACE",
    "GC-TRACE-EMPTY", "GC-UPSTREAM",
}


def _catalog():
    return load_catalog(PROFILES / "gate-catalog.yaml", PROFILES / "roles.yaml")


def test_v1_composition_19_active_quality_plus_declared_approval():
    cat = _catalog()
    assert cat.active_ids() == frozenset(EXPECTED_ACTIVE)
    for gate_id in EXPECTED_ACTIVE:
        assert cat.entry(gate_id).obligation == "quality"
    approval = cat.entry("GC-APPROVAL-MISSING")
    assert approval is not None
    assert approval.status == "declared"
    assert approval.obligation == "approval"
    # решение владельца: applicable_roles ОПУЩЕН (через allowed_approver_roles артефакта)
    assert approval.applicable_roles is None
    # GC-APPROVAL-ROLE не резервировать до отдельного boundary-решения
    assert cat.entry("GC-APPROVAL-ROLE") is None


def test_obligation_vocabulary_is_exactly_quality_approval():
    assert set(_catalog().obligation_vocabulary) == {"quality", "approval"}


def test_arch_policy_stage_keys_subset_of_stage_vocabulary():
    cat = _catalog()
    policy = yaml.safe_load((PROFILES / "arch-policy.yaml").read_text())
    assert set(policy["stages"]) <= set(cat.stage_vocabulary)
```

- [ ] **Step 2: Прогнать — падают** (файла нет).
- [ ] **Step 3: Написать `profiles/gate-catalog.yaml`** — шапка по образцу `roles.yaml`: политика стабильности (D3), правило applicable_roles, происхождение (дизайн-решение владельца 2026-08-08, резолюция `@id:oq-1-approval-evidence`, NB про два «OQ-1»), явное «GC-APPROVAL-ROLE отсутствует намеренно — граница с GC-GIT-ROLE требует отдельного решения». Тело: `version: 1`, оба словаря, 19 active-записей с однострочными `title` (брать из докстрингов/сообщений соответствующих проверок — точность важнее красоты) и `GC-APPROVAL-MISSING` (declared, approval, `stages: [release]`, `title: "required merge evidence is absent"`).
- [ ] **Step 4: Прогнать — зелёные.**
- [ ] **Step 5: Commit** — `feat(catalog): profiles/gate-catalog.yaml v1 — 19 active + GC-APPROVAL-MISSING declared`.

---

### Task 3: Sync-тест код ↔ каталог (по конструкторам Finding, не по строкам)

**Files:**
- Test: `tests/gatecatalog/test_sync_with_code.py`

**Interfaces:**
- Consumes: `load_catalog`, реальный каталог.
- Produces: `_extract_finding_rule_ids() -> set[str]` — внутренняя функция теста (не библиотека).

- [ ] **Step 1: Написать тест целиком** (это и тест, и реализация — задача тестовая):

```python
"""Sync код ↔ каталог: три гарантии полноты (дизайн-решение владельца).

НЕ сырой скан строк "GC-*" — в коде есть префикс "GC-ARCH-", не являющийся
gate id (ложный 20-й). Извлекаем ТОЛЬКО строковый аргумент rule_id из
конструкторов Finding(...): позиционный №2 (после severity) или keyword.
Динамический rule_id (не строковый литерал) — ошибка теста с требованием
явного registry: сегодня registry пуст намеренно.
"""

from __future__ import annotations

import ast
from pathlib import Path

from steward.gatecatalog import load_catalog

SRC = Path(__file__).resolve().parents[2] / "src" / "steward"
PROFILES = Path(__file__).resolve().parents[2] / "profiles"

# Явный registry динамических rule_id (решение владельца: динамика требует
# обоснования здесь, а не молчаливого пропуска). Пуст намеренно.
ALLOWED_DYNAMIC: dict[str, str] = {}  # "file.py:line" -> обоснование


def _extract_finding_rule_ids() -> set[str]:
    found: set[str] = set()
    dynamic: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "Finding"):
                continue
            arg = None
            if len(node.args) >= 2:
                arg = node.args[1]
            for kw in node.keywords:
                if kw.arg == "rule_id":
                    arg = kw.value
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.add(arg.value)
            else:
                key = f"{path.name}:{node.lineno}"
                if key not in ALLOWED_DYNAMIC:
                    dynamic.append(key)
    assert not dynamic, (
        f"динамический rule_id без записи в ALLOWED_DYNAMIC: {dynamic}"
    )
    return found


def test_every_emitted_rule_id_is_active_in_catalog():
    cat = load_catalog(PROFILES / "gate-catalog.yaml", PROFILES / "roles.yaml")
    emitted = _extract_finding_rule_ids()
    assert emitted, "экстрактор не нашёл ни одного Finding(...) — сломан сам тест"
    missing = emitted - cat.active_ids()
    assert not missing, f"код эмитит id вне active-каталога: {sorted(missing)}"


def test_every_active_id_is_reachable_from_code():
    cat = load_catalog(PROFILES / "gate-catalog.yaml", PROFILES / "roles.yaml")
    emitted = _extract_finding_rule_ids()
    dead = cat.active_ids() - emitted
    assert not dead, (
        f"active id недостижим из кода (переведи в declared/deprecated): {sorted(dead)}"
    )
```

- [ ] **Step 2: Прогнать.** Ожидание: оба зелёные (каталог Task 2 списан с фактического кода). Если экстрактор нашёл больше/меньше 19 — расхождение разбирается ЧЕСТНО: это находка о каталоге или об экстракторе, не повод подгонять тест.
- [ ] **Step 3: Мутационная самопроверка** (разово, руками): временно убрать один id из каталога → первый тест красный; временно добавить фейковый active → второй красный. Вернуть. В отчёте зафиксировать оба прогона.
- [ ] **Step 4: Commit** — `test(catalog): sync код↔каталог по конструкторам Finding + registry динамики`.

---

### Task 4: Эмиттер — catalog-гейт и активация obligation

**Files:**
- Modify: `src/steward/verdicts/emitter.py`
- Modify: `src/steward/gatecheck/cli.py` (место, где вызывается `emit_verdicts` — прокинуть загруженный каталог; путь к каталогу — рядом с профилями, тем же способом, каким cli находит `profiles/`)
- Modify: `contracts/gate-verdicts/v1/README.md`
- Test: дополнить существующие тесты эмиттера (найти их: `grep -rln emit_verdicts tests/`)

**Interfaces:**
- Consumes: `GateCatalog` из Task 1.
- Produces: `emit_verdicts(..., catalog: GateCatalog)` — новый ОБЯЗАТЕЛЬНЫЙ параметр (не Optional: эмиссия без каталога теперь бессмысленна; все вызовы обновляются в этом же диффе). Поведение: для каждого finding `entry = catalog.entry(f.rule_id)`; `entry is None` ИЛИ `entry.status != "active"` → `EmitError` с перечислением нарушивших id, файл НЕ публикуется (соблюсти существующую атомарность записи эмиттера — посмотреть, как он пишет, и не публиковать частичный файл); иначе в запись finding добавляется `"obligation": entry.obligation`.

- [ ] **Step 1: Тесты** (в стиле существующих тестов эмиттера — свериться на месте; смысловой набор):

```python
def test_unknown_rule_id_raises_emit_error_and_writes_nothing(...):
    # finding с rule_id "GC-GHOST" → pytest.raises(EmitError, match="GC-GHOST");
    # целевой файл отсутствует/не изменён

def test_declared_rule_id_is_refused_like_unknown(...):
    # rule_id "GC-APPROVAL-MISSING" (declared в реальном каталоге) → EmitError;
    # в сообщении есть слово "declared" — диагностика различает случаи

def test_active_finding_record_carries_obligation_from_catalog(...):
    # обычный прогон: каждая finding-строка в jsonl несёт "obligation": "quality"
    # (значение — из каталога, не захардкожено в эмиттере)

def test_emitted_file_validates_against_schema_v1(...):
    # если такой тест уже есть — он обязан остаться зелёным с obligation в записях
    # (поле объявлено в схеме); если нет — добавить jsonschema-проверку одной записи
```

- [ ] **Step 2: Прогнать — падают** (сигнатура/поведение).
- [ ] **Step 3: Реализация** — правка эмиттера минимальна: проверка каталога до сериализации, `"obligation": entry.obligation` рядом с `"gate_id": finding.rule_id`. В README контракта: абзац «`obligation` активирован каталогом `profiles/gate-catalog.yaml` (version 1, 2026-08-08): producer эмитит его для каждого finding; словарь — `quality | approval`. `tier`, `phase`, `risk_model_version`, `waiver_ref` остаются reserved (риск-интеграция).» — существующую фразу про «producer их пока не эмитит» скорректировать, НЕ переписывая остальной документ.
- [ ] **Step 4: Полная сюита** `uv run pytest -q` + ruff + pyrefly — чистые. Особо: dogfood-прогон `uv run gate-check --profile team spec/` и `--profile team-exp workstreams/WS-005-gate-verdicts/spec/` (как в CI) остаются зелёными.
- [ ] **Step 5: Commit** — `feat(verdicts): catalog-гейт эмиссии + obligation в findings`.

---

### Task 5: TODO.md — закрыть каталог, завести approval-policy-enforcement

**Files:**
- Modify: `TODO.md` (секция 3)

- [ ] **Step 1:** Пункт `@id:gate-id-catalog` → `[x]` + «PR этой ветки» + краткий состав (v1: 19 active/quality + GC-APPROVAL-MISSING declared/approval; словарь quality|approval; три гарантии полноты; obligation активирован на эмиссии). Строку не переносить с одной строки чекбокса.
- [ ] **Step 2:** Новый пункт (однострочный, теги на строке чекбокса):

```
- [ ] Approval policy enforcement: fact-provider merge/review-фактов + solo-compatible policy → эмит GC-APPROVAL-MISSING @owner:github:andrei-shtanakov @blocked_by:todo://steward/gate-id-catalog @id:approval-policy-enforcement
```

(блокер станет stale сразу после мержа этого PR — НАМЕРЕННО: fleet-plan-check и Robin теперь ловят «условие сработало», это их штатная работа; в контексте пункта так и написать.) Контекст пункта: резолюция `@id:oq-1-approval-evidence` (steward#49) — политика у steward, findings-only, типизированные human_merge/agent_merge внутри fact-provider'а; перевод `GC-APPROVAL-MISSING` declared→active — часть этой работы; `GC-APPROVAL-ROLE` можно вводить ТОЛЬКО вместе с отдельным принятым решением о границе GC-GIT-ROLE / GC-APPROVAL-MISSING / возможного GC-APPROVAL-ROLE.

Стоп — перечитай предыдущий абзац критически: блокер на только-что-закрытый пункт даст PF-BLOCKER-STALE немедленно, это НЕ «штатная работа», а мусорный сигнал в ежедневном прогоне. Правильно: пункт заводится БЕЗ `@blocked_by` (каталог уже доставлен к моменту мержа), зависимость фиксируется прозой в контексте. Именно так и сделать; этот абзац оставлен в плане как предупреждение исполнителю.

- [ ] **Step 3: Commit** — `docs(todo): закрыть gate-id-catalog, завести approval-policy-enforcement`.

---

### Task 6: Финал — сюита, PR, post-merge обязательства

- [ ] **Step 1:** `uv run pytest -q` (вся сюита) + `uv run ruff format . && uv run ruff check .` + `uv run pyrefly check` — чистые.
- [ ] **Step 2:** Push + `gh pr create`. Тело PR: состав v1, четыре поправки владельца как принятые ограничения, три гарантии полноты, активация obligation, предупреждение про возможные drift-advisory у консюмеров из-за README контракта, D7-обязательство (два inbox-issues после мержа).
- [ ] **Step 3:** Copilot-ревью отработать; мерж — человек.
- [ ] **Step 4 (после мержа, контроллером):** два inbox-issues по ADR-ECO-006 — dispatcher (`slug: vendor-gate-catalog`; вендорить пиненую копию каталога; их verification-rule получает словарь obligation) и Maestro (`slug: gate-catalog-for-ws006`; WS-006 M-1 пишет свой gate_verdicts.jsonl — записи обязаны ссылаться на канонические gate_id/obligation). В телах — `from: steward#gate-id-catalog`.

## Self-Review

- Все четыре поправки владельца отражены: applicable_roles (тест `test_empty_applicable_roles_is_error_not_agnostic`); lifecycle-статусы как данные (Task 1/2, declared у GC-APPROVAL-MISSING); GC-APPROVAL-ROLE отсутствует и это ЗАКРЕПЛЕНО тестом (`entry("GC-APPROVAL-ROLE") is None`) + правилом в шапке каталога и контексте нового пункта; sync по конструкторам Finding с registry динамики, эмиттер-гейт на active, обратная сверка (Task 3/4).
- Оба уточнения: stage_vocabulary в шапке каталога + тест подмножества arch-policy (Task 2); D7 — inbox-issues, шаг 4 Task 6.
- Активация ровно одного reserved-поля; схема не тронута; README-правка названа с последствием (drift-advisory).
- Ловушка исполнителю названа в Task 5 (блокер на закрытый пункт = мгновенный PF-BLOCKER-STALE).
- Типы согласованы: `GateEntry.status` Literal ↔ тесты; `emit_verdicts(catalog=...)` обязательный ↔ cli-правка в том же диффе.
