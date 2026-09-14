# Review-kit eval harness — измеримый eval ревьюера (дизайн)

Дата: 2026-09-14. Статус: дизайн согласован владельцем (диалог 2026-09-14,
решения D1–D12 ниже); реализация — по плану `docs/plans/` после ревью этой
спеки. Пункты TODO: `review-kit-eval-harness` (§10a, P2 roadmap) и, как
потребитель, `review-kit-model-selection` (P3). Базовые документы:
`docs/plans/2026-08-23-review-kit-quality-roadmap.md` §P2/P3,
`2026-08-21-codex-review-kit-design.md` (кит), `2026-09-14-review-kit-harness-layer-design.md`
(харнесс-слой: `REVIEW_HARNESS`/`REVIEW_MODEL`, `harness-claude`).

## 1. Задача

Roadmap P2: до дальнейшей настройки кита — измеримый eval на 10–20 прошлых PR
(с реальными дефектами, чистых, крупных), с метриками precision блокирующих
находок, recall известных major/blocker, ложных блокировок, доли находок без
проверяемого evidence, стоимости и длительности. Для merge-гейта precision
важнее полноты. P3: выбор модели и reasoning-уровня — только по такому eval,
минимум на двух моделях × двух уровнях.

Сегодня ничего из этого измерить нельзя: `local.sh` не сохраняет JSON вердикта
(`$work` удаляется trap-ом), `harness-claude` выбрасывает конверт claude вместе
с `usage`/`total_cost_usd`, а исторические ревью существуют только как
markdown-тела PR-ревью ai-prosto (30 на steward, десятки по потребителям).

## 2. Решения владельца (2026-09-14)

- **D1. Ground truth — вручную подтверждённый gold-набор.** Прокси из истории
  PR (находка → фикс-коммит; влит без правки; дефект починен позже) используется
  **только** для генерации кандидатов на разметку. «Влит без правки» не
  доказывает ложность находки, «нет последующего фикса» — не доказывает
  отсутствие пропуска. Официальные метрики — только по gold.
- **D2. Харнесс живёт в steward**, Python, не входит в вендоримый кит.
  Корпус пинует как минимум `repo`, `pr`, `base_sha`, `head_sha`, дайджесты
  prompt/schema/threshold. GitHub — источник материала, не ground truth и не
  воспроизводимый снапшот.
- **D3. Каждый кейс запускается в отдельном detached worktree на историческом
  `head_sha`.** `local.sh --head <sha>` меняет диапазон дифа, но не рабочее
  дерево, которое читает модель; запуск из текущего чекаута исказил бы eval.
- **D4. Расширение кита через env.** `REVIEW_VERDICT_OUT` обрабатывает
  `local.sh`: атомарно сохраняет вердикт после успешного вызова ревьюера и
  **до** `apply-threshold.sh` — результат доступен и при коде 1, и при отказе
  валидации порога. `REVIEW_USAGE_OUT` обрабатывает поддерживающий адаптер:
  атомарно пишет нормализованный JSON с `usage`, `total_cost_usd` и provider
  duration. Пустой путь в любой переменной — код 2; без переменных поведение и
  fingerprint побайтно прежние; пути sidecar-файлов в отпечаток не входят.
- **D5. Wall-clock для всех харнессов меряет Python-раннер монотонными часами.**
  `duration_ms` claude — дополнительная provider-метрика, не замена.
- **D6. Для codex стоимость — явно `unavailable`, не 0.** Сравнивать стоимость
  можно только там, где провайдер её сообщает.
- **D7. Единица ground truth — дефект с устойчивым ID, сценарием и evidence**,
  не текст исторического комментария: разные формулировки одной находки
  сопоставляются без искусственного падения precision.
- **D8. Полнота разметки — явное поле**, не булев `gold`:
  `annotation.status: draft|adjudicated`, `annotation.blocking_complete:
  true|false`. Recall и false-block rate считаются только по кейсам с
  `blocking_complete: true` (перечисленные дефекты исчерпывающи, а не просто
  известны).
- **D9. Неразмеченное предсказание не считается FP окончательно.** Строгая
  метрика называется `precision_lower_bound`; официальный `precision`
  публикуется только после разбора adjudication-queue; до этого у прогона
  статус `pending_adjudication`.
- **D10. Матчер — детерминированное взаимно-однозначное сопоставление**, не
  независимый greedy: один prediction ↔ максимум один defect; один defect
  получает максимум один TP; неоднозначность — в adjudication; повторные
  находки на один defect: первая — TP, остальные — `duplicate` и FP в
  finding-level precision; версия алгоритма и дайджест правил матчинга — в
  `run.json`. `files + line_window + keywords_any` — генератор рёбер, не
  окончательный арбитр.
- **D11. Recall учитывает классификацию предсказания.** `blocking_recall` —
  gold major/blocker найден **как** major/blocker с `confidence: high`;
  `detection_recall_any_severity` — дефект замечен, возможно недооценён.
  Автоматическая проверка evidence называется `resolvable_evidence_rate` (файл
  существует на head, строка допустима); семантическую поддержку сценария
  определяет ручная разметка. Эксплуатационные метрики обязательны:
  `completion_rate`, `valid_verdict_rate`, конфигурационные и механические
  отказы раздельно, `duplicate_rate`, число кейсов и дефектов в каждом
  знаменателе — иначе вариант, падающий на сложных кейсах, покажет искусственно
  высокий precision среди оставшихся.
- **D12. Стоимость записывается и для неуспешных прогонов**: `harness-claude`
  сохраняет `REVIEW_USAGE_OUT` сразу после получения разбираемого
  provider-конверта — до проверки `subtype` и `structured_output`. Повторения и
  парное сравнение заложены в архитектуру: `--repetitions N`, `repetition_id`,
  сырые результаты, доверительные интервалы / paired bootstrap; по умолчанию
  `N=1` как дорогой smoke.
- Детали раннера: `class: large` обязан либо нести явные `local_args` с
  потолками, либо ожидаемый исход `guardrail_rejection` — иначе кейс завершится
  кодом 2 и ничего не измерит. Сетевой контракт: `corpus materialize` —
  единственная сетевая команда (в т.ч. fallback на GitHub); `run` **всегда
  офлайн** и работает только с готовым кэшем (объекта нет → код 2), флага
  `--offline` нет. В метаданных прогона — commit/дайджесты кита, версии
  `claude`, `codex`, `git`.
- **D13. Reasoning-уровень — отдельная переменная `REVIEW_EFFORT`**, не часть
  `REVIEW_MODEL`: unset → флаг не добавляется; `""` → код 2; непустой
  `REVIEW_CMD` побеждает и игнорирует её вместе с harness/model; claude —
  `harness-claude --effort X` → внутри `claude -p --effort "$effort"`; codex —
  `codex exec … -c model_reasoning_effort=X`; эффективная строка с effort
  входит в `review_cmd`, поэтому отпечаток меняется; usage/run metadata
  сохраняют запрошенный effort. (Claude CLI 2.1.270 поддерживает `--effort`;
  для Codex официальный контракт — `model_reasoning_effort`, `codex exec`
  принимает inline `-c key=value`.)

## 3. Подходы (рассмотрены, выбран A)

- **A. Корпус в YAML + Python-раннер в steward.** Выбран.
- B. Eval как pytest-кейсы. Отклонён: платные прогоны в сьюте, метрики — не
  assert, CI не должен звать модель.
- C. Раннер в devtools поверх `review-pr.sh`. Отклонён: меряет слой публикации,
  а не кит; кросс-репо; требует живой PR, а кейс — исторический SHA.

## 4. Размещение кода (уточнение к D2)

Код — пакет **`src/steward/review_eval/`** с console-script `review-eval`
(`[project.scripts]`), а не `tools/review_eval/`: `hatchling` пакует только
`src/steward`, корень импортов pyrefly — `src/`, и пакет вне `src` повторил бы
проблему `tests.*` (missing-import; прецедент — фикс-раунд 2026-09-14 в
`test_local.py`) и не получил бы точки входа. Требование D2 сохранено по сути:
инструмент продюсера, в вендоримый кит (`scripts/review/*`) не входит и никем
не вендорится; зависимости — стандартная библиотека + уже имеющиеся (`typer`,
`pyyaml`); никакой обратной зависимости `steward.gatecheck` → `review_eval`.
Данные корпуса и прогонов — вне пакета: `eval/corpus/` (в git) и `eval/runs/`
(git-ignored, кроме явно закоммиченных evidence-копий в `docs/evidence/`).

## 5. Корпус

`eval/corpus/<repo>-<pr>.yaml`, один кейс на PR-диапазон:

```yaml
schema: review-eval-case/v1
case_id: steward-155                 # <repo>-<pr>, уникален в корпусе
repo: andrei-shtanakov/steward
pr: 155
base_sha: 496e1b2…                   # 40 hex; merge-base диапазона ревью
head_sha: 15fe2fe…                   # 40 hex; ревьюируемая голова
class: defective | clean | large
local_args: ["--max-diff-files", "60"]      # только для large (D-детали)
expected_outcome: verdict | guardrail_rejection   # large без local_args → guardrail_rejection
annotation:
  status: draft | adjudicated
  blocking_complete: true | false    # D8
  adjudicated_by: github:andrei-shtanakov
  adjudicated_at: 2026-09-15
  source: history-proxy | manual     # откуда черновик
defects:                              # D7 — единица ground truth
  - id: D-steward-155-1               # устойчивый; никогда не переиспользуется
    severity: major                   # gold-severity: blocker | major | minor
    file: scripts/review/local.sh
    line_hint: 644
    scenario: "PATH=\"$kit_dir:$PATH\" ставится и для codex-умолчания …"
    evidence: ["scripts/review/local.sh:644", "scripts/review/checksum.sh:157"]
    match:
      files: [scripts/review/local.sh]        # alias-пути допустимы
      line_window: 40                         # ± строк от line_hint
      keywords_any: [PATH, подмен, hijack, kit_dir]
non_defects:                          # исторические находки, признанные ложными
  - id: NF-steward-155-1
    file: docs/superpowers/specs/2026-09-14-review-kit-harness-layer-design.md
    line_hint: 151
    scenario: "…"
    match: {files: […], line_window: 20, keywords_any: […]}
notes: "свободный текст разметчика"
```

Правила: `case_id` уникален, `defects[].id`/`non_defects[].id` уникальны
глобально и **не переиспользуются** после удаления (append-only реестр
`eval/corpus/_ids.txt` — тест сторожит); `base_sha`/`head_sha` — полные 40 hex;
кейс с `annotation.status: draft` не входит ни в одну официальную метрику;
`blocking_complete: false` исключает кейс из знаменателей recall и
false-block, но не из precision.

**Пины кита — на прогон, не на кейс.** Объект измерения — текущий кит steward
(prompt, schema, `apply-threshold.sh`, `local.sh`, `collect-context.sh`,
`harness-claude`); их дайджесты и commit пишутся в `run.json` (§7). Кейс пинует
только состояние кода (`base_sha`, `head_sha`). Манифест контекста
`review-context.txt` читается из **base кейса**, как в бою.

**Материализация** (`review-eval corpus materialize`): для каждого кейса
гарантирует локальную доступность объектов `base_sha`/`head_sha` в
`eval/cache/<repo>.git` — **полный bare-клон** (`git clone --bare`, без
`--shared`/`--reference`: кэш не должен зависеть от `.git` соседнего чекаута,
который может быть перепакован или удалён). Источник — локальный чекаут
соседа `../<repo>` (быстро, без сети); если объекта там нет — `git fetch` из
GitHub URL. Это единственный сетевой шаг во всём инструменте; `run` сети не
касается никогда и отказывает кодом 2, если объекта нет в кэше. Соседние
чекауты не модифицируются никогда (полирепо-правило): клон и fetch читают
их, worktree создаются от кэша.

**Кандидаты из истории** (`review-eval corpus candidates --repo R --pr N`):
читает ревью ai-prosto на PR (тела + маркер `head=…`), парсит находки
(severity/confidence/file/line/title/scenario/evidence — по формату
`apply-threshold.sh`), смотрит коммиты PR после ревью, размечает
`candidate_status: likely_tp | likely_fp | unknown` (D1 — только подсказка),
пишет черновик кейса с `annotation.status: draft`, `source: history-proxy`,
дефекты с временными `id` вида `D-<repo>-<pr>-<n>` и `match` из файла/строки/
ключевых слов заголовка. Разметчик правит и переводит в `adjudicated`.

## 6. Раннер

`review-eval run --corpus eval/corpus --variant claude:claude-opus-5
--variant codex:gpt-5.4:high [--repetitions N] [--cases id,…]
--out eval/runs/<run_id>` — всегда офлайн (§5). `run_id` = `<UTC ts>-<short hash of variants+corpus digest>`.

Для каждого кейса × варианта × повторения:

1. **Изоляция (D3):** `git worktree add --detach <scratch>/<case>/<rep>
   <head_sha>` из `eval/cache/<repo>.git`; worktree удаляется после кейса
   (`git worktree remove --force`), при `--keep-worktrees` — остаётся для
   разбора. Внутри worktree нет `.steward/`, нет хуков.
2. **Кит под измерением:** `REVIEW_KIT_DIR=<steward>/scripts/review`,
   `REVIEW_PROMPT=<steward>/.github/codex/review-prompt.md`,
   `REVIEW_SCHEMA=<steward>/.github/codex/review-schema.json` — из чекаута
   steward, где запущен раннер (его commit/дайджесты — в `run.json`).
   `REVIEW_CONTEXT_MANIFEST` не задаётся: берётся манифест репо кейса из base.
3. **Вариант:** `--variant <harness>:<model>[:<effort>]` → `REVIEW_HARNESS`,
   `REVIEW_MODEL`, `REVIEW_EFFORT` (D13; без сегмента effort переменная не
   задаётся). `REVIEW_CMD` не используется и не должен присутствовать в
   окружении раннера (оверрайд целиком — не измеряемый путь; раннер вычищает
   его из наследуемого env). Запрошенный effort пишется в `result.json` и
   `run.json` как `requested_effort`.
4. **Sidecar-артефакты (D4):** `REVIEW_VERDICT_OUT=<out>/cases/<case>/<rep>/verdict.json`,
   `REVIEW_USAGE_OUT=<out>/cases/<case>/<rep>/usage.json`.
5. **Вызов:** `sh "$REVIEW_KIT_DIR/local.sh" --base <base_sha> --head <head_sha>
   --format text [local_args…]` с cwd = worktree; stdout/stderr — в
   `stdout.txt`/`stderr.txt`; код выхода и wall-clock (`time.monotonic()`
   вокруг `subprocess.run`, D5) — в `result.json`.
6. **Классификация исхода:** `verdict` (код 0/1 и `verdict.json` валиден по
   схеме), `guardrail_rejection` (код 2 с текстом потолка дифа — ожидаемо для
   `large` без `local_args`), `config_failure` (прочий код 2),
   `mechanical_failure` (код 3), `invalid_verdict` (код 0/1, но `verdict.json`
   нет или не проходит схему). Ожидаемый исход из кейса сверяется; несовпадение
   — `unexpected_outcome`, метрики качества по такому прогону не считаются.
7. **Стоимость:** из `usage.json` (D12 — файл есть и при неуспехе); для
   харнессов без sidecar — `cost_status: unavailable` (D6); значения никогда не
   подставляются нулём.

Последовательный запуск по умолчанию (`--jobs 1`): параллельные `claude`
уже убивали фоновые задачи по памяти 2026-09-14; `--jobs N` — явный opt-in.
Кейсы независимы, прогон идемпотентен по `(case, variant, rep)`: повторный
запуск с тем же `--out` пропускает готовые `result.json` (`--rerun` — заново).

## 7. Правки кита (D4, D6, D12)

**`local.sh` — `REVIEW_VERDICT_OUT`.** Объявлена и пуста → код 2 «задан
пустым» (прецедент `--context ""`). Непуста → после успешного вызова
ревьюера и проверки «вердикт не пуст», **до** `apply-threshold.sh`: атомарная
копия `$work/verdict.json` → `$REVIEW_VERDICT_OUT` (tmp в каталоге цели +
`mv`; каталог создаётся `mkdir -p`); сбой копии — код 2 с причиной (это
запрошенный артефакт, потерять его молча нельзя). Отпечаток
`--fingerprint-only` не меняется: путь не входит в состав компонент. Тест:
код 1 (major в вердикте) — файл сохранён; отказ порога (битый вердикт, код 2
из `apply-threshold.sh`) — файл сохранён; fingerprint с переменной и без —
равны.

**`harness-claude` — `REVIEW_USAGE_OUT`.** Объявлена и пуста → код 2.
Непуста → сразу после того, как конверт claude **разобран как JSON** (до
проверки `subtype`/`is_error`/`structured_output`, D12), атомарно пишется
нормализованный sidecar:

```json
{"schema": "review-usage/v1", "provider": "claude", "model": "claude-opus-5",
 "requested_effort": null,
 "usage": {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
 "total_cost_usd": 0.0, "provider_duration_ms": 0,
 "outcome": "success" | "error"}
```

Поля берутся из конверта (`usage.*`, `total_cost_usd`, `duration_ms`);
отсутствующее числовое поле — `null`, не 0. Неразбираемый конверт (битый
JSON, ненулевой код claude без JSON) — sidecar с `outcome: "error"` и
`usage: null`, чтобы прогон не потерял факт попытки. Все проверки конверта и
коды выхода адаптера — прежние. Codex: `codex exec` sidecar не пишет;
раннер фиксирует `cost_status: unavailable` (D6).

**`local.sh` и `harness-claude` — `REVIEW_EFFORT` (D13).** В резолве
харнесс-слоя (спека 2026-09-14 §4): `REVIEW_EFFORT` объявлена и пуста → код
2 «задан пустым»; непустой `REVIEW_CMD` — оверрайд целиком, effort
игнорируется вместе с harness/model; `codex` → `review_cmd="codex exec[ -m M]
-c model_reasoning_effort=E"`; `claude` → `review_cmd="harness-claude --model
M --effort E"`; адаптер принимает `--effort <e>` и передаёт `claude -p
--effort "$effort"` (без флага — как сегодня). Строка с effort входит в
`review_cmd`, значит и в отпечаток (другой reasoning — другой ревьюер,
наследовать нельзя); `--print-review-cmd` печатает её же. Sidecar usage
несёт `requested_effort` (`null`, если не задан). Значения effort кит не
валидирует (провайдер отвергнет своё) — только непустоту.

Все три правки — вендоримый кит: уезжают по флоту следующей волной
(`review-kit-next-wave`) вместе с #154; для потребителей без переменных
поведение и отпечаток побайтно прежние.

## 8. Матчер (D7, D10)

Вход: предсказания варианта по кейсу (`verdict.json.findings`, каждая с
`kind`, `severity`, `confidence`, `file`, `line`, `title`, `scenario`,
`evidence[]`) и gold (`defects[]`, `non_defects[]`).

1. **Рёбра-кандидаты**: prediction ↔ defect, если файл совпадает с одним из
   `match.files` (после нормализации путей), `|line − line_hint| ≤
   line_window`, и хотя бы одно из `keywords_any` встречается (регистронезависимо)
   в `title`/`scenario`/`expected_result`. Вес ребра = (число совпавших
   keywords, пересечение файлов evidence с `defect.evidence`, −|Δline|).
2. **Назначение — консервативное взаимно-лучшее (mutual-best), без greedy и
   без tie-break по порядку**: назначается только ребро, которое является
   **уникальным** лучшим (строго максимальный вес) и для своего prediction, и
   для своего defect; назначенная пара удаляется из графа, шаг повторяется до
   неподвижной точки. Порядок предсказаний в вердикте используется только при
   отображении, никогда в семантике — результат инвариантен к перестановке
   входа (тест §12). Веса — целочисленные кортежи, без плавающей точки.
3. **Неоднозначность**: всё, что осталось после неподвижной точки и имеет ≥ 1
   ребро (компонента с несколькими допустимыми назначениями, равные веса с
   обеих сторон), **не** назначается автоматически — уходит в
   `adjudication-queue.md` целой компонентой (все prediction и defect с их
   рёбрами); в строгих метриках такие prediction считаются неразмеченными
   (D9), а defect — не найденными (что занижает recall до разбора очереди и
   помечается в отчёте).
4. **Дубликаты**: prediction с ребром к уже назначенному defect (после шага 2)
   — `duplicate`: не TP, FP в finding-level precision, учитывается в
   `duplicate_rate`.
5. **Non-defects**: prediction, совпавший (по тем же правилам) с `non_defects`
   — `known_fp` (FP, но не в очередь). Остальное неназначенное — `unlabeled`
   (в очередь; FP только в `precision_lower_bound`).
6. **Версия**: `matcher_version: 1` и `matcher_rules_digest` (sha256
   канонического JSON параметров: нормализация путей, весовая функция,
   правила равенства) — в `run.json`. Смена правил → новая версия; отчёты с
   разными версиями не сравниваются без пометки.

## 9. Метрики (D8, D9, D11)

Считаются по варианту; каждый показатель публикуется с знаменателем
(`n_cases`, `n_defects`, `n_predictions`). «Блокирующее предсказание» —
**ровно** предикат `blocking` из `apply-threshold.sh` (`BLOCKING_DEF`), не его
пересказ: `severity ∈ {blocker, major}` **и** `confidence == "high"` **и**
непустые после удаления всех пробельных символов `file`, `scenario`,
`observed_result` **и** хотя бы один элемент `evidence` с непустыми (по тому
же правилу) `file` и `reason`. `kind` в блокировку **не** входит
(`file-missing` блокирует так же), `line` не проверяется (0 — легитимный
указатель уровня файла). Python-предикат `is_blocking(finding)` живёт в
`review_eval/threshold.py` и закреплён **контрактным тестом против настоящего
`apply-threshold.sh`**: таблица вердиктов (каждое поле по отдельности пустое /
пробельное / отсутствующее, `kind: file-missing`, `line: 0`, evidence с
пустым `reason` или `file`, confidence medium) прогоняется через скрипт, и
код выхода 0/1 обязан совпасть с предикатом на каждой строке таблицы.

**TP для блокирующей precision** — блокирующее предсказание, назначенное
матчером gold-дефекту с `severity ∈ {blocker, major}`. Предсказание,
назначенное gold-`minor` (модель завысила класс), — **FP** на блокирующем
пороге: оно красит гейт там, где gold красить не велит; `false_block_rate`
фиксирует ту же ошибку на уровне кейса, precision — на уровне находки, и
считать её TP значило бы завышать precision.

Качество (только `annotation.status: adjudicated`):

- `precision_lower_bound` — TP-блокирующие / все блокирующие (unlabeled и
  duplicate — как FP). Всегда доступна.
- `precision` — то же, но unlabeled исключены из знаменателя; публикуется
  только если очередь adjudication пуста для этого прогона, иначе у прогона
  `status: pending_adjudication` и поле отсутствует (не `null` вместо числа
  в отчёте — а явная пометка).
- `blocking_recall` — gold major/blocker, найденные блокирующим предсказанием
  (severity major/blocker **и** confidence high) / gold major+blocker; только
  кейсы с `blocking_complete: true`.
- `detection_recall_any_severity` — gold major/blocker, найденные любым
  предсказанием любой severity / gold major+blocker; те же кейсы.
- `false_block_rate` — кейсы, где кит вернул 1 при отсутствии gold
  major/blocker (включая `class: clean`) / кейсы с `blocking_complete: true` и
  исходом `verdict`.
- `resolvable_evidence_rate` — evidence-записи, у которых файл существует в
  worktree на head **и** строка ≤ числа строк файла / все evidence-записи
  блокирующих предсказаний. Семантика — ручная разметка, не здесь.

Эксплуатационные (по всем adjudicated-кейсам, независимо от исхода):

- `completion_rate` — исходы `verdict` (или ожидаемый `guardrail_rejection`) /
  все прогоны; `valid_verdict_rate` — валидные `verdict.json` / прогоны с
  кодом 0/1; `config_failure_rate`, `mechanical_failure_rate`,
  `unexpected_outcome_count` — раздельно; `duplicate_rate` — duplicate /
  все предсказания.
- Стоимость: `cost_usd_total`, `cost_usd_mean_per_case`, `cost_unavailable_cases`
  — суммируется по **всем** прогонам с sidecar, включая неуспешные (D12); при
  хотя бы одном `unavailable` в варианте средняя помечается как неполная.
- Длительность: `wall_clock_s_mean/median/p90` (раннер, D5) и отдельно
  `provider_duration_ms_mean` там, где есть.

Повторения (D12): каждое повторение — отдельный `result.json` с
`repetition_id`; метрики считаются по каждому повторению и агрегируются
(mean ± bootstrap CI 95 %, 1000 ресемплов по кейсам). `review-eval compare
<run_a> <run_b>` — парное сравнение по общим кейсам (paired bootstrap
разницы precision_lower_bound / blocking_recall / false_block_rate / cost);
при `N=1` выводится точечная разница с пометкой «без CI».

## 10. Артефакты

```
eval/runs/<run_id>/
  run.json          # kit: {commit, prompt_sha256, schema_sha256, threshold_sha256,
                    #       local_sh_sha256, collect_context_sha256, harness_claude_sha256},
                    # tools: {claude, codex, git} версии; variants; corpus_digest;
                    # matcher_version, matcher_rules_digest; started/finished; jobs
  cases/<case_id>/<variant>/<rep>/{result.json, verdict.json, usage.json, stdout.txt, stderr.txt}
  metrics.json      # по вариантам, с знаменателями и статусом
  report.md         # таблица вариантов + список кейсов с исходами
  adjudication-queue.md   # unlabeled/ambiguous предсказания: кейс, вариант, находка, кандидаты
```

`eval/runs/` — git-ignored; прогон, который становится evidence решения
(P3), копируется в `docs/evidence/<дата>-review-eval-<тема>/` целиком
(включая `verdict.json`, но без worktree).

## 11. CLI

```
review-eval corpus validate [--corpus DIR]            # схема, id, статусы; код 2 при нарушении
review-eval corpus candidates --repo R --pr N [...]   # черновик кейса из истории (сеть)
review-eval corpus materialize [--corpus DIR]         # bare-кэш объектов (сеть)
review-eval run --corpus DIR --variant H:M[:E] [...] --out DIR [--repetitions N] [--jobs N] [--cases …] [--keep-worktrees] [--rerun]   # всегда офлайн
review-eval metrics <run_dir>                          # пересчёт metrics.json/report.md (после разметки очереди)
review-eval compare <run_a> <run_b>                    # парное сравнение
```

Коды выхода: 0 — успех; 1 — прогон завершён, но есть `unexpected_outcome`
или `pending_adjudication` (информационно для CI-подобных вызовов); 2 —
конфигурация (корпус невалиден, объект не материализован в кэше, вариант не
поддержан китом); 3 — механический сбой самого раннера.

## 12. Тесты (`tests/review_eval/`, без вызова модели)

- Корпус: валидная схема; дубликат `case_id`/`defect.id` → 2; переиспользование
  удалённого id (реестр `_ids.txt`) → 2; `draft` не входит в метрики;
  `blocking_complete: false` исключён из recall/false-block, но не из precision;
  `large` без `local_args` и без `expected_outcome: guardrail_rejection` → 2.
- Матчер (таблица): перефразированная находка → тот же дефект (TP);
  другой файл → нет; две находки на один дефект → 1 TP + 1 duplicate; равные
  веса → adjudication, не назначение; цепочка «A лучший для d1, но d1 лучший
  для B» → ни одной автоматической пары, компонента в очередь;
  инвариантность к перестановке предсказаний и дефектов (property-тест на
  всех перестановках малых входов); non_defect → known_fp;
  `matcher_rules_digest` меняется при смене параметров.
- Порог: контрактный тест `is_blocking` против настоящего
  `scripts/review/apply-threshold.sh` на таблице вердиктов (§9); TP только с
  gold major/blocker — предсказание на gold-minor даёт FP.
- Метрики: рукотворные наборы с известными значениями для каждой формулы и
  знаменателя; `pending_adjudication` скрывает `precision`; `cost_unavailable`
  не даёт 0; bootstrap на детерминированном seed.
- Раннер: подставной `local.sh` в `REVIEW_KIT_DIR`, пишущий заданный
  `verdict.json`/`usage.json` по путям из env и завершающийся заданным кодом →
  сбор артефактов, классификация исходов, wall-clock > 0, `unavailable` при
  отсутствии sidecar, идемпотентность повторного запуска, объект вне кэша →
  2 без обращения к сети (подставной `git`, падающий на `fetch`), `REVIEW_CMD`
  из окружения вычищен, `requested_effort` записан, изоляция (кейс на
  историческом SHA видит исторический файл:
  фикстурный репо с двумя коммитами, стаб проверяет содержимое worktree).
- Кит: `REVIEW_VERDICT_OUT` — сохранён при коде 1 и при отказе порога; пустой
  → 2; fingerprint равен с переменной и без; `REVIEW_USAGE_OUT` — записан при
  успехе и при `is_error`/битом конверте (D12); пустой → 2; поля `null`, не 0;
  `REVIEW_EFFORT` — таблица резолва через `--print-review-cmd` и равенство
  отпечатков с явным `REVIEW_CMD` (`codex exec -c model_reasoning_effort=high`,
  `harness-claude --model M --effort high`), пустой → 2, `REVIEW_CMD` побеждает,
  адаптер передаёт `--effort` в argv `claude` (подставной `claude`).

## 13. Стартовый корпус (кандидаты; gold — разметка владельца)

Из steward (ревью ai-prosto): #155 (три подтверждённых major и один minor —
`defective`, `blocking_complete` вероятно `true`), #157 (два major, один
minor), #159 (minor); чистые — выборка из 17 «находок нет» (#152, #153, #156,
#158, #160, #161 и др.); крупные — #155 целиком как `large` с `local_args`.
Из потребителей — по 1–2 PR с известными major из dispatcher, spec-runner,
kapelle (spec-runner#491 — известный `[minor]`, ставший steward#154). Цель
первого gold-набора — 12–16 кейсов, разметка `adjudicated` до первого
официального отчёта; до неё `review-eval run` печатает «gold-кейсов нет,
метрики не публикуются».

## 14. Результат реализации

- `src/steward/review_eval/` (corpus, cache, runner, matcher, metrics, report,
  cli) + `review-eval` в `[project.scripts]`; `eval/corpus/`, `eval/corpus/_ids.txt`,
  `.gitignore`: `eval/runs/`, `eval/cache/`.
- Правки кита: `local.sh` (`REVIEW_VERDICT_OUT`, `REVIEW_EFFORT`),
  `harness-claude` (`REVIEW_USAGE_OUT`, `--effort`); README «Харнесс
  ревьюера» — три переменные; спека харнесс-слоя §4/§5/§6 — effort в резолве
  и в таблице отпечатка, sidecar usage.
- Тесты §12. Черновики кейсов §13 (`draft`).
- `docs/review-eval.md` — как размечать кейс, как читать отчёт, как
  проводить P3-сравнение.
- TODO: `review-kit-eval-harness` закрывается по **живому** первому прогону на
  gold ≥ 10 кейсов (evidence в `docs/evidence/`), не по тестам;
  `review-kit-verdict-corpus` — переоценить: для терминального канала корпус
  уже на GitHub, `corpus candidates` его читает; открытый вопрос «кто пишет из
  CI» остаётся только для CI-канала.

## 15. Границы

Не делается здесь: сам выбор модели (P3 — отдельный пункт по результатам);
семантическая оценка evidence автоматикой; параллельные прогоны по умолчанию;
запись чего-либо в соседние репо; ре-вендор кита (следующая волна).
