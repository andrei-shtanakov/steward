# Approval Policy Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `@id:approval-policy-enforcement` — fact-provider merge-provenance/actor-фактов + solo-compatible policy → эмит `GC-APPROVAL-MISSING` (declared→active), c generalized `--stage` и D6-бандлом контракта.

**Architecture:** Локальный git доказывает ТОЛЬКО provenance (first-parent-введение текущего blob через merge на default branch); identity merge-актора приходит ТОЛЬКО из авторитетного typed facts-файла (материализуется `steward approval-facts` через GitHub `mergedBy`); классификация — закрытые allowlist'ы (human/agent), всё прочее unknown; unknown НЕ удовлетворяет release-policy. GC-GIT-ROLE переводится на честный «unavailable»-контур (approvals: None ≠ пустой список). Дизайн утверждён владельцем 2026-08-08 с переработкой D1–D2 — его формулировки в Global Constraints дословно.

**Tech Stack:** Python 3.12, PyYAML, pytest, dataclasses; ruff line 100; pyrefly; gh CLI только в материализаторе.

## Global Constraints (решение владельца 2026-08-08, дословные требования)

- **Локальный git НЕ доказывает актора.** `git log --merges` доказывает наличие merge-коммита, не identity: author/committer принадлежат коммиту и задаются при создании; GitHub публикует каноническое `PullRequest.mergedBy` отдельным полем именно поэтому. Live-local без актора возвращает **unknown, не «human»**.
- **MergeProvenance (локальный провайдер)**: `sha`, `subject`, `current_blob_sha`, `merge_method="merge_commit"`, `actor=None`, `actor_source="unavailable"`. Доказывает: текущий blob артефакта появился через merge на **first-parent** default branch; SHA/subject этого merge; отсутствие прямого изменения после. Поиск — НЕ просто `git log --merges -- <path>`, а first-parent-введение текущего blob с проверками: коммит на default branch; у коммита два родителя; изменение пути относительно ПЕРВОГО родителя даёт текущий blob. Subject может дать PR number — подсказка, не authority. Squash/rebase merge может не оставить merge-коммита — провайдер проверяет это как **policy precondition**, не принимает за универсальное свойство GitHub.
- **Классификация актора — закрытая, default-human ОТКЛОНЁН** («всё, что не похоже на bot — human» = fail-open): точное совпадение с `human_identities` allowlist → human_merge; точное совпадение с `agent_identities` ИЛИ GitHub actor type Bot → agent_merge; **всё остальное → unknown**. Email-паттерны — только вспомогательный ОТРИЦАТЕЛЬНЫЙ сигнал, не доказательство human. agent_merge disabled by default (ADR-ECO-004) ⇒ в v1 agent_merge не удовлетворяет policy.
- **Канонический actor** приходит из: GitHub `mergedBy` через CI fact-provider (материализатор), либо заранее материализованного typed facts-файла.
- **Facts-схема различает четыре состояния** (нельзя сводить к пустому списку — разная операционная семантика): evidence **absent** / evidence **unavailable** / found + actor **unknown** / found + actor **human|agent**.
- **Solo-compatible policy v1** (presence + verified type, БЕЗ ролей): current artifact на default branch; есть merge provenance текущего blob; `mergedBy` доказан и классифицирован как **allowlisted human**. Роль не проверяется (полоса GC-GIT-ROLE; DEC-007 mapping не готов).
- **Конфликт с GC-GIT-ROLE решается в этом workstream**: отделить `merge_evidence()` от старого `approvals()`; отсутствие role-mapping НЕ считается доказанным role violation; GC-GIT-ROLE запускается только при наличии авторитетных role-facts (протокол: `approvals() -> tuple | None`, None = unavailable ⇒ check пропускается; пустой tuple = авторитетное «аппрувов нет» ⇒ finding легитимен). Live отдаёт None.
- **D4(а) — generalized stage**: новый канонический `--stage authoring|release`; `--arch-stage` — deprecated alias на один совместимый цикл; **оба флага с разными значениями → configuration error (exit 2)**; внутри architecture И approval checks получают одно нормализованное stage. Approval-check активен только в `release`.
- **D5**: `GC-APPROVAL-MISSING` declared→active; **каталог version 1→2** (собственная политика каталога — изменение статуса = бамп); sync-тест подтверждает достижимость через Finding-литерал.
- **D6 — контрактный бандл включён**: fixtures с `obligation` во всех finding-записях; актуальное описание `obligation` в SCHEMA.json (stale-фраза «Reserved… TODO gate-id-catalog»); README; producer-тесты. Manifest/fingerprint на стороне steward НЕТ (проверено: contracts/gate-verdicts/v1 = fixtures/ + README + SCHEMA.json) — пины у консюмеров; это осознанный contract drift, потребители перепинуют через уже заведённые dispatcher#125 / maestro#160.
- **D7 — вне скоупа**: role matching; GC-APPROVAL-ROLE (⚠️ в TODO: только с отдельным boundary-решением); PR-review evidence; положительные записи в gate-verdicts/v1.
- Дисциплина: uv only; ruff line 100; pyrefly; dogfood team+team-exp; PR-only; трейлер `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Фактические якоря (проверены 2026-08-08)

- `LiveGitFacts` (`src/steward/gatecheck/git_facts.py:197`): докстринг «approvals are never available» — сейчас возвращает пустоту, из-за чего GC-GIT-ROLE в live даёт ложные `got: none`.
- `Approval(handle, role)`; `GitFacts` Protocol: `on_default_branch`, `approvals`, `blob_hash`, `is_ancestor`, `changed_paths_since`.
- `--arch-stage`: `cli.py:148-150` (Option), `cli.py:175` (`load_arch_policy(profile_path.parent / "arch-policy.yaml", arch_stage)`); упоминания в `tests/gatecheck/test_cli.py`.
- Каталог: `profiles/gate-catalog.yaml` v1; `GC-APPROVAL-MISSING` declared / approval / stages [release]; загрузчик `src/steward/gatecatalog.py`.
- Эмиттер: `verdicts/emitter.py` — catalog-гейт (declared → EmitError) + `"obligation"` в записях.
- Контракт: `contracts/gate-verdicts/v1/{SCHEMA.json,README.md,fixtures/}`; в SCHEMA описание obligation несёт stale-фразу; fixtures findings без obligation.
- CI (`.github/workflows/ci.yml`): gate-check вызывается БЕЗ stage-флагов (authoring по умолчанию) — approval-check в CI не активируется, release-прогон — ручной/dogfood.

## File Structure

- Modify: `src/steward/gatecheck/cli.py` — `--stage` + alias + конфликт-детект; прокладка stage в approval-check.
- Modify: `src/steward/gatecheck/git_facts.py` — `approvals() -> tuple[Approval, ...] | None`; `MergeProvenance`; `merge_provenance(path)` в протоколе и обеих реализациях; facts.json v2 (actor-факты).
- Create: `src/steward/gatecheck/approval.py` — классификация актора + policy-check `check_approval_evidence(...)`.
- Create: `src/steward/approvalfacts/__init__.py` (или модуль в CLI-пакете по конвенции steward) — `steward approval-facts` материализатор (gh `mergedBy` → typed facts JSON).
- Create: `profiles/approval-policy.yaml` — закрытые allowlist'ы `human_identities` / `agent_identities`.
- Modify: `src/steward/gatecheck/checks.py` — GC-GIT-ROLE unavailable-контур.
- Modify: `profiles/gate-catalog.yaml` — GC-APPROVAL-MISSING → active, version 2.
- Modify: `contracts/gate-verdicts/v1/{SCHEMA.json,README.md,fixtures/*}` — D6-бандл.
- Modify: `TODO.md` — закрыть пункт; зафиксировать GC-GIT-ROLE-контур в контексте `gc-git-role-authorization`.
- Tests: `tests/gatecheck/test_stage_flag.py`, `tests/gatecheck/test_merge_provenance.py`, `tests/gatecheck/test_approval_check.py`, `tests/approvalfacts/test_materializer.py`, правки существующих.

---

### Task 1: Generalized `--stage` (D4а)

**Files:**
- Modify: `src/steward/gatecheck/cli.py`
- Test: `tests/gatecheck/test_stage_flag.py`

**Interfaces:**
- Produces: `_resolve_stage(stage: str | None, arch_stage: str | None) -> str` — нормализация: заданы оба и различаются → `_fail_config` («--stage and --arch-stage conflict»); задан любой один → его значение; не задан ни один → `"authoring"`. `--arch-stage` получает help-пометку `[deprecated alias of --stage]`. Значения валидируются против `{"authoring","release"}` → иначе config error. Внутри cli далее используется ТОЛЬКО нормализованный `stage` (в т.ч. в `load_arch_policy(...)` вместо `arch_stage`).

- [ ] **Step 1: Тесты** — новый файл, в стиле `tests/gatecheck/test_cli.py` (typer runner):

```python
def test_stage_flag_selects_release(...):            # --stage release ведёт себя как прежний --arch-stage release
def test_arch_stage_alias_still_works(...):          # --arch-stage release — прежнее поведение, warning-пометка в help
def test_conflicting_stage_flags_exit_2(...):        # --stage authoring --arch-stage release → exit 2, "conflict" в выводе
def test_equal_duplicate_flags_allowed(...):         # --stage release --arch-stage release → ок (не конфликт)
def test_invalid_stage_value_exit_2(...):            # --stage shipping → exit 2
```

(конкретные вызовы CLI скопировать из существующих тестов `--arch-stage` в `test_cli.py`, поменяв флаги; существующие тесты `--arch-stage` НЕ удалять — они и есть alias-контур совместимого цикла.)

- [ ] **Step 2: падают** → **Step 3: реализация** → **Step 4: вся сюита + ruff + pyrefly** → **Step 5: Commit** `feat(cli): канонический --stage, --arch-stage как deprecated alias (конфликт = config error)`.

---

### Task 2: GC-GIT-ROLE — честный unavailable-контур

**Files:**
- Modify: `src/steward/gatecheck/git_facts.py` (протокол + обе реализации)
- Modify: `src/steward/gatecheck/checks.py` (GC-GIT-ROLE)
- Test: правки в существующих тестах checks/cli, где задействованы approvals

**Interfaces:**
- Produces: `GitFacts.approvals(path) -> tuple[Approval, ...] | None` — **None = факты аппрувов недоступны** (нет авторитетного источника), пустой tuple = авторитетное «аппрувов нет». `LiveGitFacts.approvals` → `None` (вместо прежней пустоты; докстринг обновить: «approvals came from an authoritative injected source or are unavailable»). `InjectedGitFacts` — как раньше (наличие ключа в facts.json = авторитетность; artifact без ключа → пустой tuple, КАК СЕЙЧАС — не менять семантику инжектed-прогонов). GC-GIT-ROLE: `approvals is None` → check пропускается для артефакта (без finding — отсутствие role-mapping не является доказанным violation, решение владельца).

- [ ] **Step 1: Тесты**: live-путь: approved-артефакт, approvals→None → НЕТ GC-GIT-ROLE finding; injected-путь: пустой tuple → finding остаётся (регресс-защита прежней семантики); injected с правильной ролью → чисто.
- [ ] **Step 2–5**: падают → реализация → вся сюита (существующие тесты, ожидающие live-`got: none`, скорректировать — это ЦЕЛЕВОЕ изменение поведения, назвать в коммите) + dogfood → Commit `fix(gatecheck): GC-GIT-ROLE только при авторитетных role-facts (approvals: None = unavailable)`.

---

### Task 3: MergeProvenance — локальный провайдер (без актора)

**Files:**
- Modify: `src/steward/gatecheck/git_facts.py`
- Test: `tests/gatecheck/test_merge_provenance.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class MergeProvenance:
    sha: str
    subject: str
    current_blob_sha: str
    merge_method: str          # v1: "merge_commit"
    actor: str | None          # локальный провайдер: ВСЕГДА None
    actor_source: str          # локальный провайдер: ВСЕГДА "unavailable"
```

`GitFacts.merge_provenance(path) -> MergeProvenance | None` (None = provenance **absent**: текущий blob не введён merge-коммитом на first-parent default branch — включая squash/rebase-случай и прямые коммиты). `LiveGitFacts` реализует поиском: перечислить `git rev-list --first-parent <default>` merge-коммиты (`--merges`), для кандидата проверить (а) два родителя; (б) blob пути в коммите == текущий blob артефакта; (в) blob в ПЕРВОМ родителе отличается/отсутствует (введение произошло этим merge); (г) после этого merge на first-parent цепочке путь не менялся (иначе прямое изменение после — provenance absent). `InjectedGitFacts` — из facts.json v2 (Task 4 определяет ключ; здесь — поле реализации с fallback FactsError при отсутствии секции, по образцу `ancestors`).

- [ ] **Step 1: Тесты** — фикстурный git-репо (tmp, по паттерну `tests/gatecatalog`-фикстур соседей): (1) артефакт введён настоящим merge-коммитом (создать ветку, изменить файл, `git merge --no-ff`) → provenance найден, sha/subject/blob корректны, `actor is None`, `actor_source == "unavailable"`; (2) squash-подобная история (правка прямым коммитом в default) → None; (3) merge есть, но ПОСЛЕ него путь правлен прямым коммитом → None; (4) blob в первом родителе тот же (merge не вводил текущий blob) → этот кандидат пропущен.
- [ ] **Step 2–5**: падают → реализация → сюита/линтеры → Commit `feat(gatecheck): MergeProvenance — first-parent-введение blob, актор принципиально недоступен локально`.

---

### Task 4: Actor-факты — политика, классификация, материализатор

**Files:**
- Create: `profiles/approval-policy.yaml`
- Create: `src/steward/gatecheck/approval.py` (классификация + типы)
- Create: `src/steward/approvalfacts.py` + подключение `steward approval-facts` в существующий CLI-пакет `steward` (посмотреть, как зарегистрированы `risk-classify`/`waivers-check`, сделать так же)
- Modify: `src/steward/gatecheck/git_facts.py` — facts.json v2: секция actor-фактов
- Test: `tests/approvalfacts/test_materializer.py`, `tests/gatecheck/test_approval_classify.py`

**Interfaces:**
- `profiles/approval-policy.yaml`:

```yaml
# Закрытая классификация merge-акторов (решение владельца 2026-08-08).
# Точное совпадение, никаких default-human: неизвестный актор = unknown,
# unknown НЕ удовлетворяет release-policy. agent_merge disabled by default
# (ADR-ECO-004): в v1 agent тоже не удовлетворяет policy.
version: 1
human_identities:
  - github:andrei-shtanakov
agent_identities:
  - github:dependabot[bot]
```

- `approval.py`:

```python
ActorType = Literal["human", "agent", "unknown"]

@dataclass(frozen=True)
class ActorFact:
    identity: str          # "github:<login>"
    actor_type_hint: str   # "User" | "Bot" | ... (из GitHub), подсказка

def classify_actor(identity: str | None, hint: str | None, policy: ApprovalPolicy) -> ActorType:
    # identity None -> "unknown"
    # identity in policy.human_identities -> "human"
    # identity in policy.agent_identities OR hint == "Bot" -> "agent"
    # иначе -> "unknown"

def load_approval_policy(path: Path) -> ApprovalPolicy  # fail-closed по образцу gatecatalog
```

- Материализатор `steward approval-facts --repo <owner/name> --out <file>` (запускается там, где есть gh): для набора merge-SHA (вход: `--merge-sha <sha>` многократно, либо `--prs <n,...>`) спрашивает GitHub GraphQL `PullRequest.mergedBy { login, __typename }` и пишет typed facts JSON:

```json
{"schema": "approval-facts/v1",
 "actors": {"<merge_sha>": {"identity": "github:andrei-shtanakov", "type_hint": "User"}}}
```

gh отсутствует/недоступен/PR не найден → **честный exit ≠ 0 с различимой диагностикой** (unavailable — не пустой файл!). Тесты — gh через monkeypatch единственной точки `_gh` (паттерн devtools-сенсора).
- facts.json v2 / отдельный файл: gate-check получает `--approval-facts <file>` (опционально); при наличии — merge_provenance актора обогащается: `actor=identity, actor_source="github:mergedBy"`. Отсутствие файла = actor unavailable (не absent!). Четыре состояния из D5 разведены структурно: provenance None = **absent**; provenance есть + файла нет = **unavailable**; файл есть, sha нет в actors или classify→unknown = **unknown**; classify→human/agent = **human|agent**.

- [ ] **Steps**: тесты классификации (все 4 исхода + default-human ОТСУТСТВУЕТ: незнакомый identity с hint User → unknown) и материализатора (создание файла; gh-сбой → exit≠0; Bot hint → agent) → падают → реализация → сюита/линтеры → Commit `feat(approval): закрытая классификация акторов + материализатор approval-facts (gh mergedBy)`.

---

### Task 5: GC-APPROVAL-MISSING — check + активация в каталоге

**Files:**
- Modify: `src/steward/gatecheck/approval.py` (`check_approval_evidence`), `cli.py` (вызов при stage==release, прокладка `--approval-facts`), `profiles/gate-catalog.yaml` (active + version 2)
- Test: `tests/gatecheck/test_approval_check.py`, правки `tests/gatecatalog/test_catalog_data.py` (EXPECTED_ACTIVE += GC-APPROVAL-MISSING; declared-тест — снять/инвертировать)

**Interfaces:**
- `check_approval_evidence(artifacts, git: GitFacts, policy, actor_facts, stage) -> list[Finding]` — только при `stage == "release"`; для каждого approved-артефакта, присутствующего на default branch: provenance absent → `Finding("error", "GC-APPROVAL-MISSING", path, "required merge evidence is absent: no first-parent merge provenance for the current blob")`; actor unavailable → «…merge provenance found (sha …) but merge actor facts are unavailable — materialize with `steward approval-facts`»; actor unknown → «…merge actor '…' is not in the closed classification (unknown)»; actor agent → «…agent_merge is disabled by policy (ADR-ECO-004)»; human → находок нет. Четыре различимых сообщения — операционная семантика D5.

- [ ] **Steps**: тесты всех пяти исходов (+ stage==authoring → check не запускается вовсе; + артефакт НЕ на default branch → вне охвата) → падают → реализация + каталог v2 (`status: active`, `version: 2`) → sync-тест сам зазеленеет (Finding-литерал появился) — если нет, разбирать честно → сюита/линтеры/dogfood (authoring: без изменений; release-прогон по spec/ БЕЗ approval-facts обязан дать GC-APPROVAL-MISSING «unavailable»-класса — это ОЖИДАЕМО и есть доказательство работы; зафиксировать в отчёте, не глушить) → Commit `feat(gatecheck): GC-APPROVAL-MISSING активен на release — presence + verified allowlisted human`.

---

### Task 6: D6 — контрактный бандл

**Files:**
- Modify: `contracts/gate-verdicts/v1/SCHEMA.json` (ТОЛЬКО description-строка obligation), `contracts/gate-verdicts/v1/README.md`, `contracts/gate-verdicts/v1/fixtures/*.jsonl` (finding-записи получают `obligation`), producer-тесты при необходимости.

- [ ] **Step 1**: fixtures: каждой finding-записи добавить `"obligation"` со значением из каталога по её gate_id (у negative-фикстур с фейковыми id — "quality" как generic, если фикстура не про obligation; сверить с тем, как fixtures используются тестами, и НЕ сломать их назначение — например, `future_schema.jsonl` может требовать нетронутости: разобрать по одному и в отчёте объяснить каждое решение).
- [ ] **Step 2**: SCHEMA.json: заменить stale-описание obligation на актуальное («Emitted by the producer since gate-catalog v1; vocabulary owned by profiles/gate-catalog.yaml»), БЕЗ структурных изменений (то же место, только description).
- [ ] **Step 3**: README: секция obligation — упомянуть активацию GC-APPROVAL-MISSING и словарь.
- [ ] **Step 4**: вся сюита (producer-тесты + jsonschema-валидация фикстур) → Commit `contracts(gate-verdicts): D6-бандл — obligation в fixtures, актуальные SCHEMA-описание и README`.

---

### Task 7: TODO + документация

- [ ] `TODO.md`: `[x]` `approval-policy-enforcement` + «PR этой ветки» + краткое резюме (provenance локально / актор из mergedBy / закрытая классификация / unknown не проходит release / GC-GIT-ROLE unavailable-контур / --stage). В контексте пункта `gc-git-role-authorization` (§1) дописать: «2026-08-08: GC-GIT-ROLE запускается только при авторитетных role-facts (approvals: None = unavailable, live всегда None) — ложные got:none в live сняты; полный fix — после DEC-007 mapping». Проверить строку-чекбокс: теги на одной строке.
- [ ] Commit `docs(todo): закрыть approval-policy-enforcement; контур GC-GIT-ROLE зафиксирован`.

---

### Task 8: Финал

- [ ] Вся сюита + ruff + pyrefly; dogfood: authoring team+team-exp (без изменений поведения), release-прогон spec/ с материализованными approval-facts на 2-3 реальных merge-SHA (`steward approval-facts` живьём через gh) — GC-APPROVAL-MISSING отсутствует для артефактов с human-evidence; зафиксировать вывод как acceptance.
- [ ] Push + PR: состав, дословные ограничения владельца, предупреждение о contract drift (D6) с указанием dispatcher#125/maestro#160 как канала перепина, GC-GIT-ROLE поведенческое изменение названо явно.
- [ ] Copilot; мерж — человек.

## Self-Review

- Все переработки владельца учтены: локальный провайдер НЕ выдаёт актора (поля actor=None/actor_source=unavailable зашиты в интерфейс Task 3); default-human отсутствует (classify: незнакомый → unknown, тест обязателен); четыре состояния D5 разведены структурно и текстами findings (Task 5); GC-GIT-ROLE контур — в этом же workstream (Task 2); --stage по D4(а) с конфликт-детектом (Task 1); D6-бандл включён (Task 6); D7-границы повторены.
- Ловушка исполнителю названа: release-dogfood БЕЗ facts обязан краснеть «unavailable» — это доказательство работы, не провал (Task 5).
- Порядок задач: stage-флаг первым (Task 5 зависит), контур GIT-ROLE до approval-check (иначе двойные findings в тестах), provenance до классификации.
