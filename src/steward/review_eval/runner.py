"""Раннер review-eval: изолированный прогон настоящего кита по кейсу корпуса.

Один прогон — это тройка `(кейс, вариант, повторение)`
(`docs/superpowers/specs/2026-09-14-review-eval-harness-design.md` §6):

1. **Изоляция (D3).** Кит запускается в detached worktree на историческом
   `head_sha` из bare-кэша (`cache.worktree`), а не в текущем чекауте: модель
   читает рабочее дерево, и запуск «отсюда» измерял бы не тот материал.
2. **Кит под измерением.** `REVIEW_KIT_DIR`/`REVIEW_PROMPT`/`REVIEW_SCHEMA` —
   из чекаута steward, где запущен раннер; его commit и дайджесты попадают в
   `run.json` (`kit_under_test`), потому что «какой кит мерили» — часть факта.
3. **Вариант (D13).** `REVIEW_HARNESS`/`REVIEW_MODEL`/`REVIEW_EFFORT`;
   `REVIEW_EFFORT` не задаётся вовсе, если у варианта нет сегмента effort.
   Из наследуемого окружения вычищается весь `REVIEW_*` — в первую очередь
   `REVIEW_CMD` (оверрайд целиком — не измеряемый путь), но и
   `REVIEW_CONTEXT_MANIFEST` тоже: манифест берётся из репо кейса, — а вместе
   с ним весь `GIT_*`, который увёл бы git кита из worktree в чужое репо.
4. **Sidecar-артефакты (D4/D12).** `REVIEW_VERDICT_OUT`/`REVIEW_USAGE_OUT`
   указывают в каталог повторения. Вердикт кит сохраняет **до** порога,
   поэтому «sidecar есть» = «ревьюер отработал» (`reviewer_ran`).
5. **Wall-clock (D5)** мерит раннер монотонными часами — для всех харнессов
   одинаково; `provider_duration_ms` из usage — дополнительная метрика.
6. **Исход** (`classify`) выводится из кода выхода **и** наличия/валидности
   sidecar: отказ порога (код 2 при сохранённом вердикте) — ошибка модели
   (`invalid_verdict`), а не конфигурации.
7. **Стоимость (D6).** `cost_status: available` только если usage-sidecar
   есть и несёт числовой `total_cost_usd`; нулём стоимость не подставляется.

Сети раннер не касается никогда (сетевой контракт §5/D12): объектов нет в
кэше → `RunnerError`, а не `git fetch`. Пишет только внутрь `out_dir`
(артефакты и `out_dir/scratch` под worktree).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from steward.review_eval.cache import CacheError, has_object, repo_cache_dir, worktree
from steward.review_eval.corpus import Case, corpus_digest
from steward.review_eval.matcher import MATCHER_VERSION, rules_digest
from steward.review_eval.threshold import is_schema_valid_verdict

__all__ = [
    "HARNESSES",
    "KitUnderTest",
    "RunManifest",
    "RunResult",
    "RunnerError",
    "Variant",
    "EMPTY_RANGE_OUTCOME",
    "classify",
    "kit_under_test",
    "load_results",
    "parse_variant",
    "provider_env_fingerprint",
    "provider_env_names",
    "scrubbed_git_env",
    "run_all",
    "run_case",
    "utc_now",
    "variant_label",
]

#: Харнессы, которые кит умеет резолвить (спека харнесс-слоя §4).
HARNESSES: tuple[str, ...] = ("codex", "claude")

#: Подстрока отказа гардрейла `build-prompt.sh` — единственный признак,
#: отличающий ожидаемый отказ `large` от ошибки конфигурации (обе — код 2).
GUARDRAIL_MARKER = "диф больше поддерживаемого"

#: Исход «в диапазоне кейса нечего ревьюировать»: `base_sha` и `head_sha` —
#: разные коммиты с **одинаковым деревом** (правка и её откат). Кит на пустом
#: дифе выходит кодом 0 и sidecar не пишет, поэтому такой прогон
#: классифицировался как `mechanical_failure` — негодный **кейс** выглядел
#: сбоем инструмента. Проверяется до вызова ревьюера: платить за прогон,
#: которому нечего ревьюировать, незачем.
EMPTY_RANGE_OUTCOME = "empty_range"

#: Все исходы прогона: `classify` плюс `empty_range`, который ставится в обход
#: классификатора (кит не запускался). Загрузчик результатов сверяет `outcome`
#: с этим множеством — значение вне него значит чужой или битый result.json.
OUTCOMES: frozenset[str] = frozenset(
    {
        "verdict",
        "guardrail_rejection",
        "invalid_verdict",
        "config_failure",
        "mechanical_failure",
        EMPTY_RANGE_OUTCOME,
    }
)

#: Форма значения `REVIEW_MODEL`/`REVIEW_EFFORT`, которую валидирует сам кит:
#: одно слово, потому что значения интерполируются в `review_cmd`, а он по
#: контракту разбивается по словам.
#:
#: **Слеша здесь нет намеренно.** Метка варианта (`variant_label`) — имя
#: каталога артефактов, а `--rerun` этот каталог `rmtree`-ит. `codex:x/../../../outside`
#: раньше проходил разбор, уводил запись за пределы `--out` и позволял удалить
#: чужое дерево. Двоеточие в классе безвредно (сегменты разделены им же, внутрь
#: оно не попадает) и оставлено, чтобы класс совпадал с объявленным в спеке.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:@+-]+$")

#: Компоненты пути, которые нельзя допускать в метку варианта ни под каким
#: видом: точка и две точки — это «здесь» и «уровнем выше», а не имена.
_PATH_TRAVERSAL = frozenset({".", ".."})

#: Приставки имён provider-переменных окружения, чьи **имена** идут в `run.json`
#: (§10): к какому аккаунту и через какой прокси ушёл вызов — часть факта
#: «чем мерили», наравне с дайджестами кита. Сравнение без учёта регистра;
#: записывается имя как есть. Значения не пишутся никогда — это ключи.
_PROVIDER_ENV_PREFIXES: tuple[str, ...] = ("ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_")

#: Переменные прокси целиком (приставки у них нет): маршрут вызова — тоже часть
#: окружения, от которой зависит, куда ушёл запрос.
_PROVIDER_ENV_NAMES: frozenset[str] = frozenset(
    {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY"}
)

#: Подстроки имён, по которым переменная считается секретом: её **значение** не
#: попадает ни в манифест, ни в отпечаток. Сравнение без учёта регистра.
_SECRET_ENV_MARKERS: tuple[str, ...] = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
)

#: Приставки переменных, вычищаемых из наследуемого окружения (§6.3). `REVIEW_*`
#: подменяет измеряемый путь (`REVIEW_CMD` — целиком, `REVIEW_CONTEXT_MANIFEST`
#: и потолки — частично). `GIT_*` опаснее незаметно: `GIT_DIR`/`GIT_WORK_TREE`/
#: `GIT_INDEX_FILE`/`GIT_OBJECT_DIRECTORY` увели бы git кита в чужое репо, пока
#: файлы читаются из worktree, — диф и вердикт разошлись бы с материалом кейса.
_SCRUBBED_ENV_PREFIXES: tuple[str, ...] = ("REVIEW_", "GIT_")

#: Приставки аргументов кита, которые кейс не вправе передавать: они меняют
#: **что** измеряется, а не сколько. `local.sh` берёт последний `--base`/`--head`,
#: поэтому дописанный после раннера `--head C` увёл бы измерение на чужой
#: диапазон, оставив gold прежним; `--format`/`--fingerprint-only`/
#: `--print-review-cmd` меняют режим вывода (исход стал бы неклассифицируемым), а
#: `--fetch`/`--remote` тянули бы сеть в офлайн-прогон. Схема корпуса такие
#: `local_args` не пропускает — это вторая линия обороны для `Case`, собранных в коде.
_FORBIDDEN_LOCAL_ARG_PREFIXES: tuple[str, ...] = (
    "--base",
    "--head",
    "--format",
    "--fetch",
    "--remote",
    "--fingerprint-only",
    "--print-review-cmd",
)

_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")

#: Файлы кита, чьи дайджесты идут в `run.json` (§10): всё, что влияет на
#: поведение ревьюера, — промпт, схема, порог, оркестратор, сбор контекста,
#: сборка промпта, claude-адаптер.
_KIT_FILES: tuple[tuple[str, str], ...] = (
    ("threshold_sha256", "apply-threshold.sh"),
    ("local_sh_sha256", "local.sh"),
    ("collect_context_sha256", "collect-context.sh"),
    ("harness_claude_sha256", "harness-claude"),
    ("build_prompt_sha256", "build-prompt.sh"),
)

_TOOL_TIMEOUT_S = 30.0


class RunnerError(Exception):
    """Прогон невозможен: кита нет на месте, объект не в кэше, worktree не создан."""


@dataclass(frozen=True)
class Variant:
    """Измеряемый вариант ревьюера: харнесс, модель и (опционально) reasoning-уровень."""

    harness: str
    model: str
    effort: str | None


def parse_variant(text: str) -> Variant:
    """Разобрать ``<harness>:<model>[:<effort>]`` (D13).

    Отсутствующий сегмент effort — это «переменная не задаётся», а не пустое
    значение (пустое кит отвергает кодом 2). `ValueError` называет причину.

    Класс символов модели и effort — `[A-Za-z0-9._:@+-]`, **без слеша**, и ни
    один сегмент не может быть `.` или `..`: метка варианта идёт в путь
    артефактов, а `--rerun` этот путь удаляет. Вторая линия обороны — `_inside`
    перед каждой записью, потому что `Variant` собирают и в коде.
    """
    if not text or text.strip() != text:
        raise ValueError(f"variant must be '<harness>:<model>[:<effort>]', got '{text}'")
    parts = text.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(
            f"variant must have 2 or 3 ':'-separated segments, got {len(parts)} in '{text}'"
        )
    harness, model = parts[0], parts[1]
    effort = parts[2] if len(parts) == 3 else None
    if harness not in HARNESSES:
        raise ValueError(f"unsupported harness '{harness}', expected one of {HARNESSES}")
    if not _TOKEN_RE.fullmatch(model):
        raise ValueError(f"model must be one word of [A-Za-z0-9._:@+-], got '{model}'")
    if effort is not None and not _TOKEN_RE.fullmatch(effort):
        raise ValueError(f"effort must be one word of [A-Za-z0-9._:@+-], got '{effort}'")
    for name, value in (("model", model), ("effort", effort)):
        if value in _PATH_TRAVERSAL:
            raise ValueError(f"{name} must not be a path component like '.' or '..', got '{value}'")
    return Variant(harness=harness, model=model, effort=effort)


def _require_run_path(out_dir: Path, path: Path, *, what: str) -> Path:
    """Путь внутри `--out`, ни один компонент которого не симлинк; вернуть `resolve()`.

    `_require_inside` сравнивает **результат** `resolve()`, и этого мало для
    каталогов, которые раннер удаляет: симлинк внутри `scratch/`, указывающий
    наружу, резолвится в свою цель, и `rmtree` уносит чужое дерево — путь при
    этом «внутри» не был никогда. Поэтому вторая проверка: ни одного симлинка
    на участке от `out_dir` до самого пути. Симлинк в каталоге прогона не
    бывает законным — раннер создаёт там только настоящие каталоги.

    Возвращается `resolve()`: вызывающему нужен именно он (worktree и `rmtree`
    работают по абсолютному пути), а проверять надо до этого.
    """
    _require_no_symlinks(out_dir, path, what=what)
    return _require_inside(out_dir, path, what=what).resolve()


def _require_no_symlinks(root: Path, path: Path, *, what: str) -> None:
    """Отказать, если между `root` и `path` есть симлинк.

    Обе стороны нормализуются **лексически** (`os.path.normpath`, без следования
    по ссылкам): `--out` вида `sub/../run` иначе не совпадал с уже разрешённым
    корнем, `relative_to` падал, и проверка молча прекращалась — а `rmtree`
    уходил через симлинк-префикс.
    """
    root = Path(os.path.normpath(os.path.abspath(root)))
    absolute = Path(os.path.normpath(os.path.abspath(path)))
    current = root
    try:
        tail = absolute.relative_to(root)
    except ValueError:
        raise RunnerError(
            f"{what}: {absolute} вне каталога прогона {root} — операция не выполняется"
        ) from None
    for part in tail.parts:
        current = current / part
        if current.is_symlink():
            raise RunnerError(
                f"{what}: {current} — символическая ссылка внутри каталога прогона; "
                "раннер такие пути не создаёт и удалять по ним отказывается"
            )


def _require_inside(out_dir: Path, path: Path, *, what: str) -> Path:
    """Отказать, если `path` ведёт за пределы `out_dir`; вернуть путь.

    Проверяется **до** любого создания каталога, записи и `rmtree`: путь,
    ушедший наружу, нельзя «почти создать». Метка варианта собирается из
    `Variant`, а `Variant` бывает собран в коде мимо `parse_variant`, поэтому
    запрет слешей в разборе — не единственная защита, а первая.

    Сравнение по `resolve()`: символическая ссылка внутри `--out` тоже увела бы
    запись наружу, а `is_relative_to` по нормализованным путям это ловит.
    """
    resolved = path.resolve()
    root = out_dir.resolve()
    if not resolved.is_relative_to(root):
        raise RunnerError(
            f"{what}: путь {resolved} вне каталога прогона {root} — запись не выполняется"
        )
    return path


def variant_label(variant: Variant) -> str:
    """Каноническая строка варианта — она же имя каталога в артефактах (§10).

    Метка обязана разбираться обратно в тот же вариант: `Variant`, собранный в
    коде в обход `parse_variant`, иначе пронёс бы в имя каталога слеш или
    пробел — лишний уровень пути, который поиск результатов не увидит.
    """
    if variant.effort is None:
        label = f"{variant.harness}:{variant.model}"
    else:
        label = f"{variant.harness}:{variant.model}:{variant.effort}"
    try:
        parsed = parse_variant(label)
    except ValueError as exc:
        raise RunnerError(f"метка варианта негодна как имя каталога: {exc}") from exc
    if parsed != variant:
        raise RunnerError(f"метка варианта '{label}' не разбирается обратно в вариант")
    return label


@dataclass(frozen=True)
class KitUnderTest:
    """Кит под измерением: пути, commit чекаута и дайджесты значимых файлов."""

    kit_dir: Path
    prompt: Path
    schema: Path
    commit: str
    digests: dict[str, str]
    #: Права на исполнение пинуемых файлов кита (`<ключ>_executable`). Это
    #: такой же факт про кит, как дайджест: `chmod -x harness-claude`
    #: содержимого не меняет, а прогон вариантом claude ломает — и прежде
    #: провенанс этого не видел вовсе.
    executables: dict[str, bool] = dataclasses.field(default_factory=dict)


def kit_under_test(
    steward_root: Path, *, git: str = "git", harnesses: Sequence[str] = ()
) -> KitUnderTest:
    """Собрать факты о ките из чекаута steward (§6.2, §10).

    Дайджест — «сырой» hex без префикса алгоритма: алгоритм назван в ключе
    (`prompt_sha256`), в отличие от `corpus_digest`/`rules_digest`, где он
    назван в значении. Отсутствие любого файла кита — `RunnerError`
    (конфигурация, код 2 на CLI): мерить нечего.

    `commit` — провенанс, а не доказательство: если чекаут не git-репо,
    значение ``unavailable``, но содержимое кита всё равно закреплено
    дайджестами.

    **Права на исполнение** снимаются рядом с дайджестами (`executables`):
    `chmod -x` содержимого не меняет, поэтому дайджест такой кит пропускал, а
    прогон падал `config_failure` под тем же манифестом — сбой конфигурации
    выглядел свойством варианта.

    `harnesses` — какие харнессы собираются мерить. Если среди них `claude`, а
    `harness-claude` не исполняем, кит негоден **сразу**: платить за прогоны,
    обречённые на отказ, незачем.
    """
    root = steward_root.resolve()
    kit_dir = root / "scripts" / "review"
    prompt = root / ".github" / "codex" / "review-prompt.md"
    schema = root / ".github" / "codex" / "review-schema.json"

    digest_sources: list[tuple[str, Path]] = [
        ("prompt_sha256", prompt),
        ("schema_sha256", schema),
        *[(key, kit_dir / name) for key, name in _KIT_FILES],
    ]
    missing = [str(path) for _, path in digest_sources if not path.is_file()]
    if missing:
        raise RunnerError("kit under test is incomplete, missing: " + ", ".join(missing))

    digests = {key: _sha256_file(path) for key, path in digest_sources}
    executables = {
        f"{key.removesuffix('_sha256')}_executable": _is_executable(path)
        for key, path in digest_sources
    }
    if "claude" in harnesses and not executables.get("harness_claude_executable", False):
        raise RunnerError(
            f"{kit_dir / 'harness-claude'}: harness-claude не исполняем — кит негоден "
            "для варианта claude (chmod +x)"
        )
    return KitUnderTest(
        kit_dir=kit_dir,
        prompt=prompt,
        schema=schema,
        commit=_head_commit(root, git=git),
        digests=digests,
        executables=executables,
    )


def _is_executable(path: Path) -> bool:
    """Может ли **текущий** пользователь исполнить файл (`os.access(X_OK)`).

    `local.sh` проверяет `-x` для пользователя прогона; битовая маска
    `mode & 0o111` врала бы на файле с одним чужим execute-битом.
    """
    return os.access(path, os.X_OK)


@dataclass(frozen=True)
class RunResult:
    """Результат одного прогона `(кейс, вариант, повторение)` — он же ``result.json``.

    Пути к артефактам — относительные к `out_dir` и в POSIX-форме: прогон
    целиком копируется в `docs/evidence/` (§10), и абсолютный путь машины
    автора там был бы мусором.

    `wall_clock_s` мерит **только** вызов кита: таймер открывается прямо перед
    `local.sh` и закрывается сразу после. Время предпроверки диапазона лежит
    отдельно, в `precheck_s`, — смешивать их значило бы приписывать модели
    работу раннера. У исхода `empty_range` ревьюер не вызывался вовсе, поэтому
    `wall_clock_s` там ноль, а `precheck_s` заполнен.

    `teardown_error` — сбой уборки worktree **после** завершённого прогона:
    сам прогон валиден (исход и артефакты на месте), но каталог scratch утёк.
    Не исход: метрики по такому прогону считаются как обычно.
    """

    case_id: str
    variant: str
    repetition_id: int
    exit_code: int
    outcome: str
    reviewer_ran: bool
    wall_clock_s: float
    verdict_path: str | None
    usage_path: str | None
    cost_status: str
    requested_effort: str | None
    stdout_path: str
    stderr_path: str
    unexpected: bool
    teardown_error: str | None = None
    #: Время **предпроверки** раннера (диапазон пуст или нет) — не время
    #: ревьюера. Прежде оно лежало внутри `wall_clock_s`, то есть в метрику
    #: длительности модели попадали вызовы git.
    precheck_s: float = 0.0


def classify(exit_code: int, sidecar_present: bool, verdict_valid: bool, stderr: str) -> str:
    """Исход прогона по коду выхода, sidecar-у и тексту stderr (§6.6).

    Порядок проверок существенен. Код 2 многозначен: это и ожидаемый отказ
    гардрейла (по тексту потолка дифа), и отказ порога при уже полученном
    вердикте (`invalid_verdict` — ошибка модели: правила
    ``apply-threshold.sh`` строже схемы), и ошибка конфигурации. Код 0/1 без
    sidecar — нарушение контракта кита (вердикт обещан до порога), т.е.
    механический сбой, а не «ревью без находок».

    Гардрейл требует **отсутствия** sidecar: потолок дифа отказывает до вызова
    ревьюера, поэтому сохранённый вердикт при коде 2 гардрейлом быть не может,
    даже если текст потолка почему-то оказался в stderr.

    **Код 2 с сохранённым вердиктом делится по схеме.** Вердикт годен —
    `config_failure`: sidecar пишется до порога, значит порог отказал по своей
    причине (нет `jq`, негодный аргумент), и списывать это на модель нельзя.
    Вердикт негоден — `invalid_verdict`: ровно то, за что порог и отказывает.
    Прежде оба случая шли в `invalid_verdict`, то есть сбой инструмента
    выглядел ошибкой модели. Текст stderr здесь не читается: гадать по нему,
    какая из причин кода 2 сработала, — ровно та хрупкость, от которой
    классификация уходит к фактам про sidecar.
    """
    if exit_code in (0, 1) and sidecar_present and verdict_valid:
        return "verdict"
    if exit_code == 2 and not sidecar_present and GUARDRAIL_MARKER in stderr:
        return "guardrail_rejection"
    if exit_code == 2 and sidecar_present:
        # Sidecar пишется **до** порога, поэтому «код 2 при годном вердикте»
        # значит, что отказал сам порог по своей причине (нет `jq`, негодный
        # аргумент) — это конфигурация, а не ошибка модели. Негодный по схеме
        # вердикт при том же коде — ровно отказ порога, `invalid_verdict`.
        return "config_failure" if verdict_valid else "invalid_verdict"
    if exit_code in (0, 1) and sidecar_present:
        return "invalid_verdict"
    if exit_code == 2:
        return "config_failure"
    return "mechanical_failure"


def run_case(
    case: Case,
    variant: Variant,
    rep: int,
    *,
    kit: KitUnderTest,
    cache_root: Path,
    out_dir: Path,
    env_base: Mapping[str, str],
    keep_worktrees: bool = False,
    git: str = "git",
    sh: str = "sh",
) -> RunResult:
    """Прогнать кит по кейсу в изолированном worktree и записать ``result.json``.

    Офлайн по контракту: если `base_sha`/`head_sha` нет в бare-кэше —
    `RunnerError` (материализация с сетью — отдельная команда корпуса), никаких
    `git fetch` отсюда не бывает.

    Артефакты (`stdout.txt`, `stderr.txt`, `result.json`) записываются **до**
    снятия worktree: прогон уже оплачен, и сбой уборки (`CacheError` из
    `cache.worktree`) не имеет права его потерять — он записывается в
    `teardown_error`, а не бросается наружу. Утёкший каталог scratch назван в
    этом поле; следующий прогон по той же тройке его сам и подчистит.
    """
    label = variant_label(variant)
    _require_safe_local_args(case)
    out_dir = Path(os.path.abspath(out_dir))
    git, env_base = pin_git(git, env_base)
    # Собственные git-вызовы раннера идут без `GIT_*` окружения процесса:
    # унаследованный `GIT_DIR` увёл бы их в чужое репо (см. `scrubbed_git_env`).
    git_env = scrubbed_git_env(env_base)
    missing = [
        sha
        for sha in (case.head_sha, case.base_sha)
        if not has_object(cache_root, case.repo, sha, git=git, env=git_env)
    ]
    if missing:
        raise RunnerError(
            f"{case.case_id}: object(s) {', '.join(f'{case.repo}@{sha}' for sha in missing)} "
            f"not in the cache at {cache_root}; run 'review-eval corpus materialize' first "
            "(run is offline)"
        )

    triple = f"{case.case_id}/{label}/{rep}"
    rep_dir = _require_run_path(
        out_dir, out_dir / "cases" / case.case_id / label / str(rep), what=triple
    )
    rep_dir.mkdir(parents=True, exist_ok=True)
    # `resolve()` у листа артефакта был дырой: симлинк, положенный на место
    # `verdict.json`, резолвился в свою цель — `_unlink` уносил **её**, а кит
    # потом писал вердикт туда же, за пределы прогона. `rep_dir` уже абсолютен
    # и проверен на симлинки, поэтому лист достаточно присоединить и проверить.
    verdict_out = _require_artifact(rep_dir, "verdict.json", what=triple)
    usage_out = _require_artifact(rep_dir, "usage.json", what=triple)
    stdout_file = _require_artifact(rep_dir, "stdout.txt", what=triple)
    stderr_file = _require_artifact(rep_dir, "stderr.txt", what=triple)
    result_file = _require_artifact(rep_dir, "result.json", what=triple)
    # Sidecar-ы прошлого прогона этой тройки — не факты нового: кит их тоже
    # инвалидирует, но раннер не вправе зависеть от этого при `--rerun`.
    _unlink(verdict_out)
    _unlink(usage_out)

    env = _run_env(kit, variant, env_base, verdict_out=verdict_out, usage_out=usage_out)
    command = [
        sh,
        str(kit.kit_dir / "local.sh"),
        "--base",
        case.base_sha,
        "--head",
        case.head_sha,
        "--format",
        "text",
        *case.local_args,
    ]

    dest = _require_run_path(
        out_dir,
        out_dir / "scratch" / case.case_id / label / str(rep),
        what=f"scratch {case.case_id}/{label}/{rep}",
    )
    _clear_scratch(cache_root, case.repo, dest, git=git, env=git_env)

    result: RunResult | None = None
    teardown_error: str | None = None
    try:
        with worktree(
            cache_root,
            case.repo,
            case.head_sha,
            dest,
            keep=keep_worktrees,
            git=git,
            env=git_env,
        ):
            precheck_started = time.monotonic()
            empty_range = _range_is_empty(dest, case, git=git, env=git_env)
            precheck_s = time.monotonic() - precheck_started
            if empty_range:
                # Ревьюер не зовётся вовсе: диапазон пуст, измерять нечего.
                # stdout/stderr пишутся пустыми, чтобы набор артефактов тройки
                # не зависел от исхода (отчёт читает пути безусловно).
                stdout_file.write_text("", encoding="utf-8")
                stderr_file.write_text("", encoding="utf-8")
                result = RunResult(
                    case_id=case.case_id,
                    variant=label,
                    repetition_id=rep,
                    # Не код кита: кит не запускался. Ноль здесь — «нет кода»,
                    # и читать его как «чисто» не даёт `reviewer_ran: false`.
                    exit_code=0,
                    outcome=EMPTY_RANGE_OUTCOME,
                    reviewer_ran=False,
                    # Ревьюер не вызывался — его времени нет. Время
                    # предпроверки лежит в `precheck_s`, а не здесь.
                    wall_clock_s=0.0,
                    verdict_path=None,
                    usage_path=None,
                    cost_status="unavailable",
                    requested_effort=variant.effort,
                    stdout_path=_relative(stdout_file, out_dir),
                    stderr_path=_relative(stderr_file, out_dir),
                    unexpected=EMPTY_RANGE_OUTCOME != case.expected_outcome,
                    precheck_s=precheck_s,
                )
                _write_json(result_file, dataclasses.asdict(result))
                return result
            # Таймер открывается прямо перед вызовом кита и закрывается сразу
            # после: `wall_clock_s` — длительность **ревьюера**, а не раннера.
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    command,
                    cwd=dest,
                    env=dict(env),
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError as error:
                raise RunnerError(f"{case.case_id}: cannot run '{sh} local.sh': {error}") from error
            wall_clock_s = time.monotonic() - started

            stdout_file.write_text(completed.stdout, encoding="utf-8")
            stderr_file.write_text(completed.stderr, encoding="utf-8")

            sidecar_present = _is_non_empty(verdict_out)
            verdict_valid = sidecar_present and _verdict_is_valid(verdict_out)
            outcome = classify(
                completed.returncode, sidecar_present, verdict_valid, completed.stderr
            )
            usage_present = _is_non_empty(usage_out)

            result = RunResult(
                case_id=case.case_id,
                variant=label,
                repetition_id=rep,
                exit_code=completed.returncode,
                outcome=outcome,
                reviewer_ran=sidecar_present,
                wall_clock_s=wall_clock_s,
                verdict_path=_relative(verdict_out, out_dir) if sidecar_present else None,
                usage_path=_relative(usage_out, out_dir) if usage_present else None,
                cost_status="available"
                if usage_present and _has_cost(usage_out)
                else "unavailable",
                requested_effort=variant.effort,
                stdout_path=_relative(stdout_file, out_dir),
                stderr_path=_relative(stderr_file, out_dir),
                unexpected=outcome != case.expected_outcome,
                precheck_s=precheck_s,
            )
            _write_json(result_file, dataclasses.asdict(result))
    except CacheError as error:
        if result is None:
            raise RunnerError(
                f"{case.case_id}: worktree for {case.head_sha} failed: {error}"
            ) from error
        teardown_error = f"{error}"

    if result is None:  # pragma: no cover — сюда приводит только исключение выше
        raise RunnerError(f"{case.case_id}: run produced no result")
    if teardown_error is not None:
        result = dataclasses.replace(result, teardown_error=teardown_error)
        _write_json(result_file, dataclasses.asdict(result))
    return result


@dataclass(frozen=True)
class RunManifest:
    """Метаданные прогона — они же ``run.json`` (§10, D12).

    `provider_env_names` — **имена** provider-переменных окружения прогона
    (`provider_env_names`), без значений: через месяц по манифесту должно быть
    видно не только «какой кит», но и «в каком окружении» — аккаунт и прокси
    меняют результат так же молча, как правка промпта.

    `cases` — отсортированные `case_id` прогона рядом с `corpus_digest`.
    Дайджест отвечает «тот ли корпус», список — «какие кейсы измеряли», и без
    него результат чужого кейса в каталоге читался как свой: `load_results`
    сверить его было не с чем, а доливка другого набора проходила молча.

    Список описывает **прогон**, а не последнюю выборку: `--cases
    <подмножество>` его не сжимает и не перезаписывает. Он же служит границей —
    запрошенный набор обязан в него входить (равенство допустимо, выход за
    пределы — отказ), и `load_results` по нему отвергает результат чужого
    кейса.
    """

    run_id: str
    kit: dict[str, object]
    tools: dict[str, str]
    variants: list[dict[str, str | None]]
    corpus_digest: str
    matcher_version: int
    matcher_rules_digest: str
    started: str
    finished: str
    jobs: int
    repetitions: int
    provider_env_names: list[str] = dataclasses.field(default_factory=list)
    cases: list[str] = dataclasses.field(default_factory=list)
    provider_env_fingerprint: str = ""


def provider_env_names(env: Mapping[str, str]) -> list[str]:
    """Отсортированные **имена** provider-переменных окружения (§10).

    Имя попадает в список, если начинается с одной из `_PROVIDER_ENV_PREFIXES`
    или совпадает с одной из `_PROVIDER_ENV_NAMES` — сравнение без учёта
    регистра (`https_proxy` встречается чаще `HTTPS_PROXY`), в список идёт
    имя как есть. Значения не возвращаются: манифест — публикуемый артефакт
    (§10, копируется в `docs/evidence/`), и ключ в нём был бы утечкой.
    """
    return sorted(
        name
        for name in env
        if name.upper().startswith(_PROVIDER_ENV_PREFIXES) or name.upper() in _PROVIDER_ENV_NAMES
    )


def provider_env_fingerprint(env: Mapping[str, str]) -> str:
    """sha256 значений provider-переменных, **кроме секретных** (§10).

    Имена в манифесте уже есть (`provider_env_names`), но их мало: смена
    значения `HTTPS_PROXY` или переменной, выбирающей аккаунт, оставляла набор
    имён прежним — и половина прогона могла уйти другим маршрутом под одним
    манифестом. Отпечаток закрывает именно значения.

    **Что хэшируется:** строки ``имя=значение`` тех provider-переменных, чьё
    имя не выглядит секретом, отсортированные по имени.

    **Что не хэшируется и почему:** значения переменных, в имени которых есть
    `KEY`/`TOKEN`/`SECRET`/`PASSWORD`/`CREDENTIAL` (без учёта регистра). Такие
    переменные участвуют только именем — оно и так в `provider_env_names`.
    Причина не в «осторожности»: манифест публикуется (§10, копируется в
    `docs/evidence/`), а дайджест секрета вскрывается словарём — ключи
    провайдеров имеют узнаваемый формат и ограниченную энтропию. Соль в том же
    файле не помогла бы: она публикуется вместе с дайджестом.

    **Принятый предел:** смена аккаунта при том же имени переменной остаётся
    невидимой для дрейфа. Ловит её только человек; альтернатива — либо утечка,
    либо хранение соли вне артефакта, то есть отдельный секрет-менеджмент,
    которого у харнесса нет.
    """
    # Канонический JSON, не `name=value` через перевод строки: перевод строки
    # внутри значения делал две разные пары неотличимыми от одной.
    pairs = [
        [name, _redact_userinfo(env[name])]
        for name in provider_env_names(env)
        if not _is_secret_env_name(name)
    ]
    canonical = json.dumps(pairs, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _redact_userinfo(value: str) -> str:
    """URL без `user:pass@`: логин и пароль прокси в материал отпечатка не входят.

    Значение прокси «несекретно» только по имени переменной; `http://alice:pw@host`
    в детерминированном дайджесте публикуемого манифеста стал бы оракулом для
    перебора пароля. Хост и порт остаются — это и есть маршрут.
    """
    if "://" not in value:
        return value
    parts = urlsplit(value)
    if "@" not in parts.netloc:
        return value
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def _is_secret_env_name(name: str) -> bool:
    """Похоже ли имя переменной на секрет (тогда значение не хэшируется)."""
    upper = name.upper()
    return any(marker in upper for marker in _SECRET_ENV_MARKERS)


def run_all(
    cases: Sequence[Case],
    variants: Sequence[Variant],
    *,
    repetitions: int,
    out_dir: Path,
    kit: KitUnderTest,
    cache_root: Path,
    jobs: int = 1,
    rerun: bool = False,
    keep_worktrees: bool = False,
    env_base: Mapping[str, str] | None = None,
    corpus_digest_override: str | None = None,
    git: str = "git",
    sh: str = "sh",
) -> RunManifest:
    """Прогнать все тройки `(кейс, вариант, повторение)` и записать ``run.json``.

    `corpus_digest_override` — дайджест **всего** корпуса, когда `cases` —
    подмножество (`review-eval run --cases …`): `run.json` обязан назвать
    корпус, из которого выбирали, а не выборку, иначе два прогона разных
    подмножеств одного корпуса выглядели бы прогонами разных корпусов.
    По умолчанию считается по переданным `cases`.

    **`repetitions` только растут — и `--rerun` от этого не освобождает.**
    Меньшее число перезаписывало бы `run.json`, оставляя на диске `result.json`
    старших повторений: `--rerun` сносит **выбранные** тройки, то есть 1..N
    нового N, а каталоги N+1.. остаются — и оказываются вне манифеста. Выход
    один: новый `--out`.

    **Менять `repetitions` разрешено только запросом по полному набору кейсов
    манифеста.** Доливка выборки с другим числом давала манифест, объявляющий
    старшие повторения у **всех** кейсов, тогда как появлялись они лишь у
    выбранных, — и метрики публиковали такой прогон как `ok`. Число повторений
    описывает прогон целиком.

    **Результаты без манифеста — отказ,** и `--rerun` спасает только целиком:
    он допустим лишь когда сброс накрывает **все** существующие результаты
    (после него в каталоге не осталось бы ничего чужого). Частичный перезапуск
    переизмерил бы часть, а `run.json` объявил бы своими и остальные — чужие
    результаты получили бы чужой провенанс.

    **Результаты без манифеста — отказ.** Если в каталоге есть `result.json`, а
    `run.json` отсутствует или не читается, провенанс этих результатов
    неизвестен: прежде отсутствующий манифест читался как «каталог пуст», и
    раннер писал свой `run.json`, пропуская готовые тройки как свои — чужие
    результаты получали чужой провенанс. Выход один: `--rerun` по **полному**
    набору кейсов (он и переизмеряет всё) либо новый `--out`.

    Идемпотентно по тройке: готовый `result.json` пропускается без `rerun`.
    Доливка в тот же `--out` остаётся тем же прогоном: `run_id` и `started`
    берутся из прежнего `run.json`, обновляется только `finished`.
    Последовательно по умолчанию (`jobs=1`): параллельные вызовы модели уже
    убивали фоновые задачи по памяти (§6). `run.json` пишется дважды — в начале
    с `finished: null`, в конце целиком: прогон, оборвавшийся на середине,
    оставляет честный манифест, а не пустой каталог.
    """
    if not cases:
        raise RunnerError("cases must not be empty: без кейсов измерять нечего")
    if not variants:
        raise RunnerError("variants must not be empty: без варианта измерять нечего")
    if repetitions < 1:
        raise RunnerError(f"repetitions must be >= 1, got {repetitions}")
    if jobs < 1:
        raise RunnerError(f"jobs must be >= 1, got {jobs}")
    for case in cases:
        _require_safe_local_args(case)
    # Относительный `--out` (документированный `eval/runs/<id>`) сравнивался бы
    # с абсолютными целями сброса лексически и делал все результаты «остатком».
    out_dir = Path(os.path.abspath(out_dir))
    git, env_base = pin_git(git, env_base)
    git_env = scrubbed_git_env(env_base)
    _require_objects(cases, cache_root, git=git, env=git_env)

    environment: Mapping[str, str] = env_base
    labels = [variant_label(v) for v in variants]
    duplicated = sorted({label for label in labels if labels.count(label) > 1})
    if duplicated:
        # Метка — имя каталога артефактов, поэтому дубликат писал бы вердикт и
        # результат по одному пути из двух задач (при `--jobs > 1` —
        # одновременно), а счёт прогонов вырос бы вдвое без второго измерения.
        raise RunnerError(
            f"варианты повторяются: {', '.join(duplicated)} — одна тройка была бы "
            "оплачена дважды и писала бы артефакты по одному пути"
        )
    variant_records: list[dict[str, str | None]] = [
        {
            "label": variant_label(v),
            "harness": v.harness,
            "model": v.model,
            "requested_effort": v.effort,
        }
        for v in variants
    ]
    digest = corpus_digest(cases) if corpus_digest_override is None else corpus_digest_override
    case_ids = sorted(case.case_id for case in cases)
    kit_payload: dict[str, object] = {"commit": kit.commit, **kit.digests, **kit.executables}
    if any(v.harness == "claude" for v in variants) and (
        kit.executables.get("harness_claude_executable") is False
    ):
        # Тот же отказ, что в `kit_under_test`, но здесь известны варианты:
        # `KitUnderTest` собирают и в коде, не называя харнессов.
        raise RunnerError(
            f"{kit.kit_dir / 'harness-claude'}: harness-claude не исполняем — кит негоден "
            "для варианта claude (chmod +x)"
        )
    tools = _tool_versions(git_env, git=git)
    env_names = provider_env_names(environment)
    env_fingerprint = provider_env_fingerprint(environment)
    out_dir.mkdir(parents=True, exist_ok=True)
    previous = _previous_manifest(out_dir)
    if previous is None and not rerun and _remaining_results(out_dir):
        raise RunnerError(
            f"в {out_dir} есть результаты, но run.json отсутствует/нечитаем — "
            "провенанс неизвестен: --rerun всего каталога или новый --out"
        )
    stored_reps = previous.get("repetitions") if previous is not None else None
    if isinstance(stored_reps, int) and repetitions < stored_reps:
        raise RunnerError(
            f"{out_dir / 'run.json'}: уменьшение repetitions запрещено "
            f"(в манифесте {stored_reps}, запрошено {repetitions}) — результаты "
            "старших повторений остались бы вне манифеста и без него: новый --out"
        )
    # Список кейсов манифеста описывает **прогон**, а не последнюю выборку:
    # `--cases <подмножество>` его не сжимает, иначе следующая доливка полным
    # набором оказалась бы «выходом за манифест», а результаты кейсов вне
    # выборки — «необъявленными» для `load_results`.
    manifest_cases = _manifest_cases(previous) or case_ids
    if (
        isinstance(stored_reps, int)
        and repetitions != stored_reps
        and set(case_ids) != set(manifest_cases)
    ):
        missing = sorted(set(manifest_cases) - set(case_ids))
        raise RunnerError(
            f"{out_dir / 'run.json'}: менять repetitions ({stored_reps} → "
            f"{repetitions}) можно только запросом по полному набору кейсов "
            f"манифеста — не запрошены: {', '.join(missing) or '—'}; иначе старшие "
            "повторения появятся лишь у части кейсов, а манифест объявит их у всех"
        )
    run_id = _resolve_run_id(out_dir, previous, labels, digest)
    drift = _provenance_drift(
        previous,
        kit_payload,
        digest,
        variant_records,
        env_names,
        case_ids,
        tools,
        env_fingerprint,
    )
    if drift and not rerun:
        raise RunnerError(
            f"{out_dir / 'run.json'}: доливка невозможна — провенанс прогона "
            f"разошёлся с текущим ({', '.join(drift)}); передайте --rerun, "
            "чтобы начать прогон заново, или выберите другой --out"
        )
    if rerun:
        # Порядок важен: сначала **посчитать** всё, потом отказать, и только
        # потом удалять. Прежде `_reset_results` сносил выбранные тройки до
        # проверки остатка, и команда, завершившаяся отказом, успевала
        # уничтожить оплаченные результаты.
        targets = _reset_targets(out_dir, cases, variants, repetitions)
        leftover = [
            path
            for path in _remaining_results(out_dir)
            if not any(path.is_relative_to(target) for target in targets)
        ]
        if previous is None and leftover:
            # Провенанс остающихся результатов неизвестен: записать свой
            # `run.json` значит выдать чужие результаты за свои. Частичный
            # `--rerun` тут проходил, потому что отказ стоял под `if not rerun`.
            raise RunnerError(
                f"{out_dir}: результаты без манифеста: перезапуск только всего "
                f"каталога — --cases со всеми кейсами и всеми вариантами, или новый "
                f"--out (вне выборки осталось бы {len(leftover)})"
            )
        if drift and leftover:
            raise RunnerError(
                f"{out_dir}: провенанс разошёлся ({', '.join(drift)}), а в каталоге "
                f"остались результаты прошлого прогона ({len(leftover)}) вне текущей "
                "выборки — начните прогон в другом --out, чтобы не смешивать"
            )
        _reset_results(
            out_dir,
            targets,
            [case.repo for case in cases],
            cache_root=cache_root,
            git=git,
            env=git_env,
        )
    # `started` сбрасывается только когда переизмеряется **всё**: при частичном
    # `--rerun` остаются результаты прежнего прогона, и новая дата
    # датировала бы их позже измерения.
    fresh_start = rerun and not leftover
    started = utc_now() if fresh_start else (_previous_string(previous, "started") or utc_now())

    payload: dict[str, object] = {
        "run_id": run_id,
        "kit": kit_payload,
        "tools": tools,
        "variants": variant_records,
        "corpus_digest": digest,
        "cases": manifest_cases,
        "matcher_version": MATCHER_VERSION,
        "matcher_rules_digest": rules_digest(),
        "started": started,
        "finished": None,
        "jobs": jobs,
        "repetitions": repetitions,
        "provider_env_names": env_names,
        "provider_env_fingerprint": env_fingerprint,
    }
    _write_json(out_dir / "run.json", payload)

    pending = [
        (case, variant, rep)
        for case in cases
        for variant in variants
        for rep in range(1, repetitions + 1)
        if rerun or not _result_exists(out_dir, case, variant, rep)
    ]

    def execute(task: tuple[Case, Variant, int]) -> RunResult:
        case, variant, rep = task
        return run_case(
            case,
            variant,
            rep,
            kit=kit,
            cache_root=cache_root,
            out_dir=out_dir,
            env_base=environment,
            keep_worktrees=keep_worktrees,
            git=git,
            sh=sh,
        )

    results: list[RunResult] = []
    if jobs == 1:
        for task in pending:
            results.append(execute(task))
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            for future in [pool.submit(execute, task) for task in pending]:
                results.append(future.result())

    if not keep_worktrees and all(item.teardown_error is None for item in results):
        _remove_empty_scratch(out_dir / "scratch")

    # `finished` — только когда всё произведение манифеста на диске: возобновление
    # подмножества иначе закрывало бы прогон без результатов остальных кейсов,
    # и тот же `load_results` такой каталог отверг бы.
    labels = [variant_label(v) for v in variants]
    unfilled = [
        f"{case_id}/{label}/{rep}"
        for case_id in manifest_cases
        for label in labels
        for rep in range(1, repetitions + 1)
        if not (out_dir / "cases" / case_id / label / str(rep) / "result.json").is_file()
    ]
    if unfilled:
        raise RunnerError(
            f"{out_dir}: прогон не полон — нет результатов {', '.join(unfilled[:5])}"
            f"{' …' if len(unfilled) > 5 else ''}; run.json остаётся незакрытым "
            "(finished: null) — возобновите прогон полным набором кейсов"
        )

    manifest = RunManifest(
        run_id=run_id,
        kit=kit_payload,
        tools=tools,
        variants=variant_records,
        corpus_digest=digest,
        matcher_version=MATCHER_VERSION,
        matcher_rules_digest=rules_digest(),
        started=started,
        finished=utc_now(),
        jobs=jobs,
        repetitions=repetitions,
        provider_env_names=env_names,
        provider_env_fingerprint=env_fingerprint,
        cases=manifest_cases,
    )
    _write_json(out_dir / "run.json", dataclasses.asdict(manifest))
    return manifest


def _remove_empty_scratch(scratch: Path) -> None:
    """Убрать дерево `scratch/`, если в нём не осталось файлов.

    После чистого прогона там остаются только пустые каталоги — мусор, который
    попадёт в копию прогона в `docs/evidence/`. Непустое дерево не трогаем: это
    утёкший worktree (`teardown_error`, `--keep-worktrees`, обрыв), и удалять
    его молча — значит терять след.
    """
    if not scratch.is_dir():
        return
    if any(not path.is_dir() for path in scratch.rglob("*")):
        return
    shutil.rmtree(scratch, ignore_errors=True)


def load_results(out_dir: Path) -> list[RunResult]:
    """Все записанные `result.json` прогона, отсортированные по тройке.

    Читает `cases/<case>/<variant>/<rep>/result.json` — единственный источник
    исходов прогона (в т.ч. доложенных прошлыми запусками в тот же `--out`).
    Порядок детерминирован: отчёт и правило кода выхода CLI не должны зависеть
    от обхода файловой системы. Битый или неполный `result.json` — `RunnerError`
    с именем файла: молча потерять исход прогона нельзя.

    **Путь сверяется с содержимым** (`_require_path_matches_payload`), и каждая
    тройка обязана встретиться ровно один раз (`_require_unique`): артефакт
    называет тройку дважды — деревом каталогов и полями внутри, — и оба
    утверждения должны совпадать.

    Симлинк на месте `result.json` или любого каталога выше — `RunnerError`
    (`_require_result_file`): содержимое пришло бы извне прогона.

    **Незакрытый манифест — тоже отказ** (`_require_finished`): полный набор
    результатов при ``finished: null`` значит, что раннер оборвался между
    последней тройкой и финальной записью, а не что прогон готов.

    **Результаты без манифеста — отказ.** Прежде они просто возвращались, и
    метрики считались по прогону, о котором артефакты не говорят ни кита, ни
    корпуса, ни варианта, ни окружения. Пустой (или новый) каталог — пустой
    список, как прежде: там и результатов нет.

    **Результат вне манифеста — тоже `RunnerError`.** Каталог прогона может
    нести остаток прогона с другим `repetitions`, другим набором вариантов или
    другими кейсами. Тихо включить такой результат в метрики нельзя (числа
    посчитаны по необъявленному прогону), тихо выбросить — тоже: исход оплачен,
    и его исчезновение надо объяснять, а не скрывать. Сверяются повторение,
    метка варианта и `case_id` — манифест перечисляет все три. Манифеста нет
    вовсе — сверять не с чем, читаем как есть (сам этот случай ловит
    `run_all`).
    """
    out_dir = Path(os.path.abspath(out_dir))
    cases_dir = out_dir / "cases"
    manifest = _previous_manifest(out_dir)
    results: list[RunResult] = []
    seen: dict[tuple[str, str, int], Path] = {}
    for path in sorted(cases_dir.rglob("result.json")) if cases_dir.exists() else []:
        # Только каноническая глубина `cases/<case>/<variant>/<rep>/result.json`:
        # вложенная копия (`…/<rep>/backup/result.json`) — необъявленный
        # оплаченный исход, и молча пропустить его шаблон `*/*/*` как раз и позволял.
        if len(path.relative_to(cases_dir).parts) != 4:
            raise RunnerError(
                f"{path}: result.json вне канонического пути "
                f"cases/<case>/<variant>/<rep>/result.json — необъявленный результат"
            )
        _require_result_file(out_dir, path)
        result = _result_from_file(path)
        _require_path_matches_payload(result, path, cases_dir=cases_dir)
        _require_unique(result, path, seen)
        _require_in_manifest(result, manifest, path, out_dir=out_dir)
        results.append(result)
    if manifest is None and results:
        raise RunnerError(
            f"{out_dir}: результаты без манифеста — провенанс неизвестен: чем мерили "
            "(кит, корпус, вариант, окружение) сказать нечем; --rerun всего каталога "
            "или новый --out"
        )
    _require_finished(manifest, out_dir=out_dir)
    _require_complete(results, manifest, out_dir=out_dir)
    return sorted(results, key=lambda item: (item.case_id, item.variant, item.repetition_id))


def _require_path_matches_payload(result: RunResult, path: Path, *, cases_dir: Path) -> None:
    """Путь обязан утверждать ту же тройку, что и содержимое файла.

    Артефакт называет тройку дважды: деревом каталогов
    (`cases/<case_id>/<variant>/<rep>/result.json`) и полями внутри. Прежде
    читались только поля, поэтому файл, положенный в каталог чужого кейса,
    входил в прогон по своему содержимому — и два утверждения о том, что
    измерено, расходились молча. Расхождение — `RunnerError`: чинить надо
    каталог, а не выбирать, какому из двух верить.
    """
    case_dir, variant_dir, rep_dir = path.relative_to(cases_dir).parts[:3]
    rep = int(rep_dir) if rep_dir.isdigit() and not rep_dir.startswith("0") else None
    if (case_dir, variant_dir, rep) != (result.case_id, result.variant, result.repetition_id):
        raise RunnerError(
            f"{path}: путь не совпадает с содержимым result.json — "
            f"по пути {case_dir}/{variant_dir}/{rep_dir}, в файле "
            f"{result.case_id}/{result.variant}/{result.repetition_id}"
        )


def _require_unique(result: RunResult, path: Path, seen: dict[tuple[str, str, int], Path]) -> None:
    """Каждая тройка встречается ровно один раз.

    Копия результата под другим именем каталога удвоила бы прогон в
    знаменателях, а какая из двух копий «настоящая» — вопрос без ответа.
    Сообщение называет **оба** пути: выбирать за оператора нечего.
    """
    key = (result.case_id, result.variant, result.repetition_id)
    first = seen.get(key)
    if first is not None:
        raise RunnerError(
            f"{path}: тройка {result.case_id}/{result.variant}/{result.repetition_id} "
            f"встречается дважды — она же в {first}; уберите лишнюю копию"
        )
    seen[key] = path


def _require_finished(manifest: dict[str, object] | None, *, out_dir: Path) -> None:
    """Отказать, если манифест не закрыт (`finished` пуст или отсутствует).

    `run.json` пишется дважды: в начале с ``finished: null``, в конце целиком.
    Обрыв между последней тройкой и финальной записью оставляет полный набор
    `result.json` при незакрытом манифесте — и каталог читался как готовый
    прогон, хотя раннер до конца не дошёл: не убран `scratch`, не записано
    `finished`, а значит неизвестно, что ещё он собирался сделать. Полнота
    результатов «завершённости» не доказывает.
    """
    if manifest is None:
        return
    finished = manifest.get("finished")
    if not isinstance(finished, str) or not finished:
        raise RunnerError(
            f"{out_dir / 'run.json'}: прогон не завершён (finished отсутствует): "
            "дождитесь завершения или возобновите run"
        )


def _require_complete(
    results: Sequence[RunResult],
    manifest: dict[str, object] | None,
    *,
    out_dir: Path,
) -> None:
    """Отказать, если манифест объявляет тройки, которых на диске нет.

    Манифест объявляет **произведение** «кейсы × варианты × 1..repetitions».
    Отсутствующий `result.json` значит одно из двух: прогон ещё идёт или он
    оборвался. В обоих случаях считать метрики нельзя, и `status: ok` по
    половине прогона выглядел бы измерением — прогон в процессе просто не
    отчётен.

    Манифеста нет или он не объявляет состав (старый формат) — сверять не с
    чем; сам этот случай ловит `run_all`.
    """
    if manifest is None:
        return
    cases = _manifest_cases(manifest)
    variants = manifest.get("variants")
    repetitions = manifest.get("repetitions")
    labels = (
        [record.get("label") for record in variants if isinstance(record, Mapping)]
        if isinstance(variants, list)
        else []
    )
    if not cases or not labels or not isinstance(repetitions, int) or repetitions < 1:
        return
    present = {(item.case_id, item.variant, item.repetition_id) for item in results}
    missing = [
        f"{case_id}/{label}/{rep}"
        for case_id in cases
        for label in labels
        for rep in range(1, repetitions + 1)
        if (case_id, label, rep) not in present
    ]
    if missing:
        shown = ", ".join(missing[:10]) + (" …" if len(missing) > 10 else "")
        raise RunnerError(
            f"{out_dir}: прогон неполон — манифест объявляет тройки, которых нет "
            f"({len(missing)}): {shown}; прогон ещё идёт или оборвался, метрики по "
            "нему не считаются (доливка тем же --out либо новый --out)"
        )


def _require_in_manifest(
    result: RunResult,
    manifest: dict[str, object] | None,
    path: Path,
    *,
    out_dir: Path,
) -> None:
    """Отказать, если `run.json` такого прогона не объявляет."""
    if manifest is None:
        return
    repetitions = manifest.get("repetitions")
    if isinstance(repetitions, int) and result.repetition_id > repetitions:
        raise RunnerError(
            f"{path}: repetition {result.repetition_id} вне манифеста "
            f"{out_dir / 'run.json'} (repetitions={repetitions}) — результат "
            "необъявленного прогона: --rerun всего каталога или новый --out"
        )
    declared = manifest.get("cases")
    if isinstance(declared, list) and result.case_id not in declared:
        raise RunnerError(
            f"{path}: кейс '{result.case_id}' вне манифеста {out_dir / 'run.json'} — "
            "результат необъявленного прогона: --rerun всего каталога или новый --out"
        )
    variants = manifest.get("variants")
    if isinstance(variants, list):
        labels = {record.get("label") for record in variants if isinstance(record, Mapping)}
        if result.variant not in labels:
            raise RunnerError(
                f"{path}: вариант '{result.variant}' вне манифеста "
                f"{out_dir / 'run.json'} — результат необъявленного прогона: "
                "--rerun всего каталога или новый --out"
            )


def _provenance_drift(
    previous: dict[str, object] | None,
    kit: Mapping[str, object],
    digest: str,
    variants: Sequence[Mapping[str, str | None]],
    env_names: Sequence[str],
    case_ids: Sequence[str],
    tools: Mapping[str, str],
    env_fingerprint: str,
) -> list[str]:
    """Поля, в которых прежний `run.json` расходится с текущим прогоном.

    Сравнивается то, что отвечает на вопрос **что измерено**: кит (commit и
    каждый дайджест), корпус, матчер, набор вариантов, набор кейсов, набор
    имён provider-переменных и **версии инструментов** (`tools`). `jobs` и
    `repetitions` не сравниваются: это объём работы, а не её объект.

    Из `tools` сверяются **только клиенты, которыми пользуются варианты
    прогона** (`_used_clients`): харнесс `codex` → версия `codex`, харнесс
    `claude` → версия `claude`. Они и есть измеряемый ревьюер снаружи кита, и
    доливка после их обновления давала половину результатов от прежней версии
    под манифестом от новой. Обновили используемый клиент — новый `--out` либо
    полный `--rerun`.

    **`git` сверяется всегда.** Он не «обстоятельство»: `local.sh` выбирает
    алгоритм подготовки дифа по возможностям git (`check-attr --source`), то
    есть обновление git меняет **вход ревьюера** — ровно то, что провенанс
    обязан фиксировать. Один раунд он из сверки выводился как «версия на
    машине»; это была ошибка, и она исправлена.

    Неиспользуемые клиенты ревьюера пишутся в `run.json` для протокола, но в
    сверку не входят: появление на машине клиента, которым прогон не
    пользуется, к измеренному отношения не имеет.

    Переход используемого клиента между `unavailable` и настоящей версией —
    дрейф наравне с обновлением: прогон, где ревьюер записан недоступным, и
    прогон, где он есть, — разные измерения.

    Набор кейсов сравнивается на **вхождение**, а не на равенство: выборка
    внутри списка манифеста законна (`--cases <подмножество>` — штатный
    сценарий, для него и существует `corpus_digest_override`), выход за него —
    нет. Прогон по A и прогон по A+B разные измерения, и один `run.json` их не
    описывает, поэтому надмножество отвергается наравне с чужим кейсом.
    Равенство требовать нельзя: переизмерить один кейс существующего прогона
    значило бы заводить новый `--out` и платить за весь корпус.

    Сам список манифеста от выборки не меняется (см. `run_all`): он описывает
    прогон целиком, а не последнюю выборку.

    Окружение попало сюда не как «обстоятельство»: имена provider-переменных
    отвечают, к какому аккаунту и через какой прокси ушёл вызов. Прежде
    повторение 2 с другим набором доливалось молча, а `run.json`
    перезаписывался новым набором — файл утверждал, что весь прогон шёл через
    одно окружение, хотя половина шла через другое.

    Сравниваются **имена и отпечаток значений**
    (`provider_env_fingerprint`): одних имён мало — смена значения
    `HTTPS_PROXY` набор имён не меняет. Значения секретных по имени переменных
    в отпечаток не входят, поэтому смена ключа при том же имени дрейфом не
    считается; почему так — в докстринге `provider_env_fingerprint`.
    """
    if previous is None:
        return []
    drift: list[str] = []
    stored_kit = previous.get("kit")
    stored_kit = stored_kit if isinstance(stored_kit, Mapping) else {}
    for key in sorted(set(kit) | set(stored_kit)):
        if stored_kit.get(key) != kit.get(key):
            drift.append(f"kit.{key}")
    stored_tools = previous.get("tools")
    stored_tools = stored_tools if isinstance(stored_tools, Mapping) else {}
    for name in sorted(_used_clients(previous.get("variants"), variants) | {"git"}):
        was, now = stored_tools.get(name), tools.get(name)
        if was != now:
            drift.append(f"tools.{name} (было: {was}; стало: {now})")
    if previous.get("corpus_digest") != digest:
        drift.append("corpus_digest")
    if previous.get("matcher_version") != MATCHER_VERSION:
        drift.append("matcher_version")
    if previous.get("matcher_rules_digest") != rules_digest():
        drift.append("matcher_rules_digest")
    if previous.get("variants") != [dict(record) for record in variants]:
        drift.append("variants")
    stored_cases = _manifest_cases(previous)
    outside = sorted(set(case_ids) - set(stored_cases)) if stored_cases else []
    if outside:
        drift.append(
            f"кейсы вне манифеста прогона: {', '.join(outside)} "
            f"(манифест называет: {', '.join(stored_cases)})"
        )
    stored_env = previous.get("provider_env_names")
    stored_env = list(stored_env) if isinstance(stored_env, list) else []
    if stored_env != list(env_names):
        drift.append(
            f"provider_env_names (было: {', '.join(str(n) for n in stored_env) or '—'}; "
            f"стало: {', '.join(env_names) or '—'})"
        )
    stored_fingerprint = previous.get("provider_env_fingerprint")
    if not isinstance(stored_fingerprint, str):
        # Поля нет или оно не строка — провенанс окружения неизвестен, и
        # «совпало» сказать нельзя: смена значения прокси прошла бы незамеченной.
        drift.append("provider_env_fingerprint (в манифесте отсутствует — окружение неизвестно)")
    elif stored_fingerprint != env_fingerprint:
        # Имена совпали, значения — нет: смена прокси или аккаунта, выбранного
        # несекретной переменной. Сами значения в сообщении не показываются —
        # в манифесте их нет и быть не должно.
        drift.append(
            f"provider_env_fingerprint (было: {stored_fingerprint[:12]}; "
            f"стало: {env_fingerprint[:12]})"
        )
    return drift


def _used_clients(
    stored_variants: object, variants: Sequence[Mapping[str, str | None]]
) -> set[str]:
    """Имена CLI, которыми пользуются варианты прогона (по их харнессам).

    Берётся **объединение** записанных и запрошенных вариантов: если набор
    вариантов сам разошёлся, это отдельный пункт дрейфа, и прятать за ним
    смену версии клиента было бы неверно.
    """
    used: set[str] = set()
    records: list[object] = list(variants)
    if isinstance(stored_variants, list):
        records.extend(stored_variants)
    for record in records:
        if not isinstance(record, Mapping):
            continue
        harness = record.get("harness")
        if isinstance(harness, str) and harness in HARNESSES:
            used.add(harness)
    return used


def _reset_targets(
    out_dir: Path,
    cases: Sequence[Case],
    variants: Sequence[Variant],
    repetitions: int,
) -> list[Path]:
    """Каталоги троек, которые снесёт `--rerun` — **посчитанные, но не тронутые**.

    Отдельная функция, потому что порядок в `run_all` обязан быть «посчитать →
    отказать → удалить»: список нужен и для проверки остатка (что останется
    после задуманного сброса), и для самого сброса.

    Каждый путь проверяется `_require_inside`: метка варианта попадает в него
    как есть, и уход за пределы `--out` должен кончаться отказом, а не
    `rmtree` наружу.
    """
    targets: list[Path] = []
    for case in cases:
        for variant in variants:
            label = variant_label(variant)
            for rep in range(1, repetitions + 1):
                # `_require_run_path`, не `_require_inside`: цель удаляется, и
                # симлинк-префикс (`cases/A -> archive`) резолвился бы внутрь
                # `--out`, а `rmtree` уносил бы чужой каталог.
                targets.append(
                    _require_run_path(
                        out_dir,
                        out_dir / "cases" / case.case_id / label / str(rep),
                        what=f"--rerun {case.case_id}/{label}/{rep}",
                    )
                )
    return targets


def _reset_results(
    out_dir: Path,
    targets: Sequence[Path],
    repos: Sequence[str],
    *,
    cache_root: Path,
    git: str,
    env: Mapping[str, str],
) -> None:
    """`--rerun`: снести посчитанные `_reset_targets` каталоги и `scratch/`.

    Сносятся **только выбранные** тройки: результат прогона оплачен, и
    `--rerun --cases <подмножество>` не имеет права удалять чужие. Смесь
    провенансов это не открывает — при расхождении провенанса вызывающий
    отказывается **до** вызова этой функции, если что-то остаётся
    (см. `run_all`).

    После удаления каталогов worktree обязателен `worktree prune`: иначе
    `worktree add` упрётся в прежнюю регистрацию (тот же контракт, что у
    `_clear_scratch`).
    """
    # Scratch чистится только у выбранных троек: при `keep_worktrees` деревья
    # невыбранных кейсов оставлены для разбора, и сносить весь `scratch/`
    # значило бы отнять их у частичного `--rerun`.
    cases_root = out_dir / "cases"
    for target in targets:
        scratch = _require_run_path(
            out_dir,
            out_dir / "scratch" / target.relative_to(cases_root.resolve()),
            what="--rerun scratch",
        )
        if scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)
    # prune — **до** удаления результатов и с вычищенным окружением: с
    # унаследованным `GIT_DIR` он молча падал, регистрация worktree оставалась,
    # и следующий `worktree add` отказывал уже после потери оплаченного результата.
    for repo in dict.fromkeys(repos):
        cache = repo_cache_dir(cache_root, repo)
        if not cache.exists():
            continue
        pruned = subprocess.run(
            [git, "-C", str(cache), "worktree", "prune"],
            capture_output=True,
            text=True,
            check=False,
            env=dict(env),
        )
        if pruned.returncode != 0:
            raise RunnerError(
                f"{cache}: worktree prune не удался (код {pruned.returncode}): "
                f"{pruned.stderr.strip()} — результаты не тронуты"
            )
    for target in targets:
        shutil.rmtree(target, ignore_errors=True)


def _remaining_results(out_dir: Path) -> list[Path]:
    """Оставшиеся в каталоге `result.json` — след прогона, который здесь уже был.

    Каждый путь проходит `_require_result_file`: симлинк на месте результата
    отвергается **раньше** любых решений про провенанс и сброс. Иначе
    подложенная ссылка попадала бы в счёт остатка, то есть участвовала бы в
    рассуждении о том, чей это каталог.
    """
    return [
        _require_result_file(out_dir, path)
        for path in sorted((out_dir / "cases").glob("*/*/*/result.json"))
    ]


def _require_safe_local_args(case: Case) -> None:
    """Отказать, если `local_args` кейса подменяют измеряемое, а не его объём.

    Отказ до создания worktree и до вызова кита: прогон с подменённым
    диапазоном не «частично валиден», он измеряет не тот материал, и такой
    результат опаснее отсутствующего — он выглядит как нормальный.
    """
    forbidden = [
        argument
        for argument in case.local_args
        if argument.startswith(_FORBIDDEN_LOCAL_ARG_PREFIXES)
    ]
    if forbidden:
        raise RunnerError(
            f"{case.case_id}: local_args подменяют измерение и запрещены: "
            f"{', '.join(forbidden)} (диапазон задаёт раннер по base_sha/head_sha)"
        )


def _require_objects(
    cases: Sequence[Case], cache_root: Path, *, git: str, env: Mapping[str, str] | None = None
) -> None:
    """Fail fast: все `base_sha`/`head_sha` выбранных кейсов уже в bare-кэше.

    Проверка до первого прогона, а не по ходу: иначе оплаченные прогоны
    первых кейсов обрывались бы на непокрытом кейсе в середине очереди.
    """
    missing = [
        f"{case.repo}@{sha} ({case.case_id})"
        for case in cases
        for sha in dict.fromkeys((case.base_sha, case.head_sha))
        if not has_object(cache_root, case.repo, sha, git=git, env=env)
    ]
    if missing:
        raise RunnerError(
            f"objects not in the cache at {cache_root}: {', '.join(missing)}; "
            "run 'review-eval corpus materialize' first (run is offline)"
        )


def _result_from_file(path: Path) -> RunResult:
    """`RunResult` из `result.json`; лишние ключи игнорируются, нехватка — ошибка."""
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise RunnerError(f"{path}: result.json is not a JSON object")
    fields = dataclasses.fields(RunResult)
    missing = [
        field.name
        for field in fields
        if field.name not in payload and field.default is dataclasses.MISSING
    ]
    if missing:
        raise RunnerError(f"{path}: result.json is missing field(s): {', '.join(missing)}")
    known = {field.name: payload[field.name] for field in fields if field.name in payload}
    numeric = ("repetition_id", "exit_code", "wall_clock_s", "precheck_s")
    bad = [
        name
        for name in numeric
        if name in known
        and (not isinstance(known[name], (int, float)) or isinstance(known[name], bool))
    ]
    if bad:
        raise RunnerError(f"{path}: result.json field(s) must be numeric: {', '.join(bad)}")
    # Остальные типы тоже проверяются: dataclass во время исполнения их не
    # сверяет, а строка "false" истинна — `reviewer_ran`/`unexpected` врали бы.
    text = ("case_id", "variant", "outcome", "cost_status", "stdout_path", "stderr_path")
    optional_text = ("verdict_path", "usage_path", "requested_effort", "teardown_error")
    flags = ("reviewer_ran", "unexpected")
    wrong = [name for name in text if not isinstance(known.get(name), str)]
    wrong += [
        name
        for name in optional_text
        if name in known and not isinstance(known[name], (str, type(None)))
    ]
    wrong += [name for name in flags if not isinstance(known.get(name), bool)]
    if wrong:
        raise RunnerError(f"{path}: result.json field(s) of the wrong type: {', '.join(wrong)}")
    if known["outcome"] not in OUTCOMES:
        raise RunnerError(
            f"{path}: result.json outcome '{known['outcome']}' вне известных исходов "
            f"({', '.join(sorted(OUTCOMES))})"
        )
    return RunResult(**known)


def _unlink(path: Path) -> None:
    """Удалить файл, если он есть (остаток прошлого прогона этой тройки).

    Симлинк не удаляется, а отвергается: путь вида «ссылка наружу» на месте
    артефакта — не остаток прошлого прогона, а подмена, и `unlink` по нему
    унёс бы чужой файл. Вторая линия обороны к `_require_artifact`: она
    проверяет лист перед созданием, эта — перед удалением.
    """
    if path.is_symlink():
        raise RunnerError(
            f"{path}: символическая ссылка на месте артефакта прогона — "
            "раннер такие пути не создаёт и удалять по ним отказывается"
        )
    path.unlink(missing_ok=True)


def _require_artifact(rep_dir: Path, name: Path | str, *, what: str) -> Path:
    """Путь артефакта внутри каталога тройки; симлинк — `RunnerError`.

    `rep_dir` уже проверен `_require_run_path` (внутри `--out`, без симлинков
    по всему участку), поэтому остаётся сам лист: `verdict.json` и остальные
    файлы раннер создаёт сам, и ссылка на их месте означает подмену.
    Отказ — **до** удаления и записи: путь, уводящий наружу, нельзя «почти
    создать».
    """
    path = rep_dir / name
    if path.is_symlink():
        raise RunnerError(
            f"{what}: {path} — символическая ссылка на месте артефакта прогона; "
            "раннер такие пути не создаёт и писать по ним отказывается"
        )
    return path


def pin_git(git: str, env_base: Mapping[str, str] | None) -> tuple[str, dict[str, str]]:
    """Один и тот же git у раннера и у кита.

    `local.sh` зовёт голое `git` из PATH окружения прогона, а раннер — бинарь из
    параметра `git`; версия в манифесте писалась с параметра, а диф строил
    PATH-бинарь (и по его возможностям кит выбирает обработку generated-файлов).
    Поэтому: `git` резолвится против PATH прогона (явный путь — как есть), его
    каталог ставится в начало PATH, и всё дальше — own-вызовы, версии, кит —
    работает с этим одним бинарём.
    """
    source: Mapping[str, str] = os.environ if env_base is None else env_base
    if os.sep in git or (os.altsep and os.altsep in git):
        resolved = os.path.abspath(git)
        if not os.access(resolved, os.X_OK):
            raise RunnerError(f"--git {git}: бинарь не найден или не исполняем")
        if os.path.basename(resolved) != "git":
            # Кит зовёт голое `git`: бинарь с другим именем в его PATH не найдётся.
            # Обёртка `git` в отдельном каталоге делает его видимым под нужным именем.
            resolved = _git_alias(resolved)
    else:
        found = shutil.which(git, path=source.get("PATH"))
        if found is None:
            raise RunnerError(f"--git {git}: не найден в PATH окружения прогона")
        resolved = os.path.abspath(found)
    env = dict(source)
    gitdir = os.path.dirname(resolved)
    path = env.get("PATH", "")
    if path.split(os.pathsep)[0] != gitdir:
        env["PATH"] = gitdir + (os.pathsep + path if path else "")
    return resolved, env


def _git_alias(binary: str) -> str:
    """Обёртка с именем `git`, исполняющая `binary`: кит найдёт её по имени в PATH.

    Каталог — временный на процесс (по пути бинаря он детерминирован, чтобы
    повторные вызовы не плодили обёрток); символические ссылки не используются —
    внутри каталога прогона они запрещены, а здесь просто не нужны.
    """
    digest = hashlib.sha256(binary.encode("utf-8")).hexdigest()[:16]
    alias_dir = Path(tempfile.gettempdir()) / f"review-eval-git-{digest}"
    alias_dir.mkdir(mode=0o700, exist_ok=True)
    alias = alias_dir / "git"
    body = f'#!/bin/sh\nexec "{binary}" "$@"\n'
    if not alias.exists() or alias.read_text(encoding="utf-8") != body:
        alias.write_text(body, encoding="utf-8")
        alias.chmod(0o700)
    return str(alias)


def scrubbed_git_env(env_base: Mapping[str, str] | None) -> dict[str, str]:
    """Окружение для **собственных** git-вызовов раннера: без `GIT_*`.

    Вычищалось только окружение кита, а `has_object`, `worktree`, проверка
    диапазона и опрос версий наследовали `GIT_*` процесса. Унаследованный
    `GIT_DIR`/`GIT_WORK_TREE` увёл бы их в чужое репо: кэш выглядел бы
    непокрытым, worktree создавался бы не от кэша, а `git --version` — от
    другого чекаута. Прогон падал бы на машине, где переменная просто
    выставлена.

    `REVIEW_*` здесь **не** вычищается: на git они не влияют, а на кита идёт
    отдельное окружение (`_run_env`), где вычищено и то, и другое.
    """
    source = os.environ if env_base is None else env_base
    return {key: value for key, value in source.items() if not key.startswith("GIT_")}


def _run_env(
    kit: KitUnderTest,
    variant: Variant,
    env_base: Mapping[str, str],
    *,
    verdict_out: Path,
    usage_out: Path,
) -> dict[str, str]:
    """Окружение прогона: наследуемое без `REVIEW_*`/`GIT_*` плюс наши переменные.

    Вычищаются приставки целиком, а не только `REVIEW_CMD`: любая
    унаследованная `REVIEW_*` (модель, манифест контекста, потолки) или `GIT_*`
    (`GIT_DIR`, `GIT_WORK_TREE`, …) подменила бы измеряемый путь молча (§6.3).
    Остальное окружение наследуется как есть — `PATH`, `HOME`, `TMPDIR`, локаль
    киту нужны.
    """
    env = {
        key: value for key, value in env_base.items() if not key.startswith(_SCRUBBED_ENV_PREFIXES)
    }
    env["REVIEW_KIT_DIR"] = str(kit.kit_dir)
    env["REVIEW_PROMPT"] = str(kit.prompt)
    env["REVIEW_SCHEMA"] = str(kit.schema)
    env["REVIEW_HARNESS"] = variant.harness
    env["REVIEW_MODEL"] = variant.model
    if variant.effort is not None:
        env["REVIEW_EFFORT"] = variant.effort
    env["REVIEW_VERDICT_OUT"] = str(verdict_out)
    env["REVIEW_USAGE_OUT"] = str(usage_out)
    return env


def _range_is_empty(
    worktree_dir: Path, case: Case, *, git: str, env: Mapping[str, str] | None = None
) -> bool:
    """Пуст ли диф диапазона ревью — тот же диапазон, что считает кит.

    `merge-base(base, head)..head`: именно его берёт `local.sh`, поэтому
    проверять что-то другое значило бы отвечать не на тот вопрос. Считается в
    worktree кейса, офлайн — объекты уже в bare-кэше.

    Сбой git (нет бинаря, битый кэш) — **не** «диапазон пуст»: возвращается
    `False`, и прогон идёт обычным путём, где сбой проявится честно. Молча
    объявить кейс негодным из-за неудачного вызова git было бы хуже: он
    исчез бы из метрик качества.
    """
    merge_base = _git_stdout(
        git, worktree_dir, ["merge-base", case.base_sha, case.head_sha], env=env
    )
    if merge_base is None:
        return False
    completed = _git_run(
        git, worktree_dir, ["diff", "--quiet", f"{merge_base}..{case.head_sha}"], env=env
    )
    return completed is not None and completed.returncode == 0


def _git_run(
    git: str, cwd: Path, args: Sequence[str], *, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str] | None:
    """Вызов git в каталоге; ``None`` — бинаря нет или его не удалось запустить."""
    try:
        return subprocess.run(  # noqa: S603 — argv фиксирован, без shell
            [git, "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            check=False,
            env=None if env is None else dict(env),
        )
    except OSError:
        return None


def _git_stdout(
    git: str, cwd: Path, args: Sequence[str], *, env: Mapping[str, str] | None = None
) -> str | None:
    """stdout удачного вызова git без пробелов по краям, иначе ``None``."""
    completed = _git_run(git, cwd, args, env=env)
    if completed is None or completed.returncode != 0:
        return None
    text = completed.stdout.strip()
    return text or None


def _clear_scratch(
    cache_root: Path, repo: str, dest: Path, *, git: str, env: Mapping[str, str]
) -> None:
    """Убрать остаток worktree по пути `dest` (после `--keep-worktrees` или обрыва).

    Только каталог внутри `out_dir/scratch`, который раннер сам и создаёт;
    после удаления обязателен `worktree prune` — с вычищенным окружением и с
    проверкой кода: с унаследованным `GIT_DIR` он молча падал, регистрация
    оставалась, и следующий `worktree add` отказывал.
    """
    if not dest.exists():
        return
    shutil.rmtree(dest)
    cache = repo_cache_dir(cache_root, repo)
    if not cache.exists():
        return
    pruned = subprocess.run(
        [git, "-C", str(cache), "worktree", "prune"],
        capture_output=True,
        text=True,
        check=False,
        env=dict(env),
    )
    if pruned.returncode != 0:
        raise RunnerError(
            f"{cache}: worktree prune не удался (код {pruned.returncode}): {pruned.stderr.strip()}"
        )


def _verdict_is_valid(path: Path) -> bool:
    """Годен ли sidecar как вердикт — по общему определению `threshold`.

    Правило одно на пакет (`is_schema_valid_verdict`): структура **плюс** схема
    каждой находки. Это класс настоящего кита: `apply-threshold.sh` отвергает
    схемно негодный вердикт кодом 2, поэтому исход `verdict` такому sidecar-у
    не положен — он `invalid_verdict`, ошибка модели. Прежде раннер проверял
    только структуру, и находка без обязательного `title` попадала в метрики
    качества: TP/FP считались по вердикту, по которому гейт решения не выносил.
    """
    return is_schema_valid_verdict(_read_json(path))


def _has_cost(path: Path) -> bool:
    """Несёт ли usage-sidecar числовой `total_cost_usd` (D6: `null` — не 0)."""
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return False
    cost = payload.get("total_cost_usd")
    return isinstance(cost, (int, float)) and not isinstance(cost, bool)


def _reject_non_json_constant(token: str) -> object:
    """`Infinity`/`NaN` — расширение Python, не JSON: jq порога их не разбирает."""
    raise ValueError(f"не-JSON константа {token}")


def _read_json(path: Path) -> object:
    """JSON из файла или ``None``, если файла нет / он не разбирается.

    Строго JSON: литералы ``Infinity``/``NaN`` отвергаются, как их отвергает
    jq в `apply-threshold.sh` — иначе sidecar с ``line: Infinity`` считался
    бы схемно годным, а код 2 порога приписывался бы конфигурации.
    """
    try:
        return json.loads(
            path.read_text(encoding="utf-8"), parse_constant=_reject_non_json_constant
        )
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _is_non_empty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _relative(path: Path, out_dir: Path) -> str:
    """Путь относительно `out_dir` в POSIX-форме (артефакты переносимы, §10)."""
    return path.resolve().relative_to(out_dir.resolve()).as_posix()


def _write_json(path: Path, payload: object) -> None:
    """Записать JSON **атомарно**: временный файл рядом плюс ``os.replace``.

    `run.json` перезаписывается дважды (в начале с ``finished: null``, в конце
    целиком), а `result.json` — единственный источник исхода прогона. Обрыв
    посреди `write_text` оставил бы обрезанный файл, то есть прогон без
    манифеста или без исхода; ``os.replace`` в пределах одного каталога либо
    заменяет файл целиком, либо не заменяет вовсе.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2)
    tmp = path.with_name(f".{path.name}.tmp")
    # Оба пути — и цель, и временный файл рядом — обязаны быть настоящими
    # файлами: `write_text` по симлинку пишет в его цель, а `os.replace`
    # заменил бы саму ссылку, оставив цель испорченной.
    for candidate in (path, tmp):
        if candidate.is_symlink():
            raise RunnerError(
                f"{candidate}: символическая ссылка на месте файла прогона — "
                "раннер такие пути не создаёт и писать по ним отказывается"
            )
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _result_exists(out_dir: Path, case: Case, variant: Variant, rep: int) -> bool:
    """Есть ли **настоящий** `result.json` этой тройки (тогда прогон пропускается).

    По ссылке идти нельзя ни в какую сторону: симлинк на месте `result.json`
    выглядел готовым прогоном, тройка пропускалась, и в метрики попадал файл
    извне прогона. Прогнать поверх ссылки тоже нельзя (`_require_result_file`
    отказывает), поэтому здесь отказ, а не «считаем, что результата нет».
    """
    result = _require_result_file(
        out_dir,
        out_dir / "cases" / case.case_id / variant_label(variant) / str(rep) / "result.json",
    )
    return result.is_file()


def _require_result_file(out_dir: Path, path: Path) -> Path:
    """Путь `result.json` без симлинков на участке от `--out`; иначе `RunnerError`.

    «Готовый результат» — обычный файл, созданный раннером. Ссылка на его месте
    (или на месте любого каталога выше) означает, что содержимое пришло извне
    прогона, а по такому пути нельзя ни читать исход, ни пропускать тройку.
    """
    _require_no_symlinks(out_dir.resolve(), path, what=f"{path.name} прогона")
    if path.is_symlink():
        raise RunnerError(
            f"{path}: символическая ссылка на месте результата прогона — "
            "раннер такие пути не создаёт и доверять им отказывается"
        )
    return path


#: Обязательные поля `run.json` и их типы: разбираемый, но неполный манифест —
#: не манифест. Иначе проверки состава и провенанса «отключались» бы на
#: отсутствующем поле и результаты читались бы без объявления.
_MANIFEST_REQUIRED: tuple[tuple[str, type | tuple[type, ...]], ...] = (
    ("run_id", str),
    ("kit", dict),
    ("tools", dict),
    ("variants", list),
    ("corpus_digest", str),
    ("started", str),
    ("repetitions", int),
    ("provider_env_names", list),
    ("cases", list),
    ("provider_env_fingerprint", str),
    ("matcher_version", int),
    ("matcher_rules_digest", str),
)

#: Списки состава прогона не бывают пустыми: `cases: []` при результатах —
#: манифест, не объявляющий ни одного из них, а не «проверять нечего».
_MANIFEST_NON_EMPTY: tuple[str, ...] = ("cases", "variants")


def _previous_manifest(out_dir: Path) -> dict[str, object] | None:
    """Прежний `run.json` этого каталога, если он есть и разбирается.

    Разбираемый объект без обязательного поля (или с полем не того типа) —
    `RunnerError`, а не «манифеста нет»: иначе `{"finished": …}` пропускал бы
    результаты через все проверки.
    """
    path = out_dir / "run.json"
    if path.is_symlink():
        # До любого чтения и тем более до сброса: по ссылке манифест не читается,
        # а «ошибка чтения = манифеста нет» пускала полный --rerun в удаление.
        raise RunnerError(
            f"{path}: символическая ссылка на месте run.json — раннер такие пути "
            "не создаёт; уберите ссылку или начните новый --out"
        )
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return None
    for field, kind in _MANIFEST_REQUIRED:
        value = payload.get(field)
        if not isinstance(value, kind) or isinstance(value, bool):
            raise RunnerError(
                f"{path}: манифест повреждён — нет поля '{field}' нужного типа; "
                "результаты без объявления не читаются (новый --out или восстановите run.json)"
            )
    for field in _MANIFEST_NON_EMPTY:
        if not payload.get(field):
            raise RunnerError(
                f"{path}: манифест повреждён — поле '{field}' пусто; прогон без состава "
                "не объявляет ни одного результата (новый --out или восстановите run.json)"
            )
    for field in ("repetitions", "jobs"):
        if isinstance(payload.get(field), int) and payload[field] < 1:
            raise RunnerError(
                f"{path}: манифест повреждён — {field} < 1; завершённый прогон объявляет "
                "хотя бы одну тройку (новый --out или восстановите run.json)"
            )
    variants = payload["variants"]
    if not all(
        isinstance(record, Mapping) and isinstance(record.get("label"), str) and record["label"]
        for record in variants
    ):
        # Негодный элемент молча отфильтровывался, список меток пустел, и проверка
        # полноты отключалась — пустой каталог читался как завершённый прогон.
        raise RunnerError(
            f"{path}: манифест повреждён — элемент variants без строковой label; "
            "результаты без объявления не читаются (новый --out или восстановите run.json)"
        )
    return payload


def _manifest_cases(previous: dict[str, object] | None) -> list[str]:
    """Список кейсов прежнего манифеста; пустой — его там нет или он не список."""
    if previous is None:
        return []
    stored = previous.get("cases")
    return [str(item) for item in stored] if isinstance(stored, list) else []


def _previous_string(previous: dict[str, object] | None, key: str) -> str | None:
    """Непустая строка из прежнего манифеста или ``None``."""
    if previous is None:
        return None
    value = previous.get(key)
    return value if isinstance(value, str) and value else None


def _resolve_run_id(
    out_dir: Path,
    previous: dict[str, object] | None,
    labels: Sequence[str],
    digest: str,
) -> str:
    """`run_id` прогона: имя каталога, затем прежний `run.json`, затем новый.

    Идемпотентный повтор в тот же `--out` обязан остаться **тем же** прогоном:
    новый `run_id` при доливке результатов сделал бы `run.json` и уже
    записанные артефакты разными прогонами.
    """
    if _RUN_ID_RE.fullmatch(out_dir.name):
        return out_dir.name
    return _previous_string(previous, "run_id") or _new_run_id(labels, digest)


def _new_run_id(labels: Sequence[str], digest: str) -> str:
    """``<UTC ts>-<8 hex>``: метка времени плюс отпечаток вариантов и корпуса (§6)."""
    material = "\n".join([*labels, digest]).encode("utf-8")
    short = hashlib.sha256(material).hexdigest()[:8]
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{short}"


def utc_now() -> str:
    """Текущее время UTC в форме, которой пользуются артефакты прогона (§10).

    Публичная: ту же метку ставит `cli` в `recomputed_with`, и два формата
    времени в одном `metrics.json` читались бы как два разных источника.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tool_versions(env: Mapping[str, str], *, git: str) -> dict[str, str]:
    """Версии `claude`, `codex`, `git` — ``unavailable``, если бинаря нет или он упал.

    Резолвятся и запускаются **в окружении прогона** (`env`), а не процесса:
    раннер сам собирает это окружение (§6.3) и в нём же ищет ревьюера, поэтому
    `PATH` процесса дал бы версию не того бинаря, который вызывался, — то есть
    провенанс, расходящийся с измерением.
    """
    return {
        "claude": _tool_version("claude", env),
        "codex": _tool_version("codex", env),
        "git": _tool_version(git, env),
    }


def _tool_version(binary: str, env: Mapping[str, str]) -> str:
    if shutil.which(binary, path=env.get("PATH")) is None:
        return "unavailable"
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_TOOL_TIMEOUT_S,
            env=dict(env),
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    if result.returncode != 0:
        return "unavailable"
    first_line = result.stdout.strip().splitlines()
    return first_line[0].strip() if first_line else "unavailable"


def _head_commit(root: Path, *, git: str) -> str:
    """Commit чекаута или ``unavailable``, если это не git-репо.

    Невозможность **запустить** `git` — другое дело: `OSError` значит неверный
    `--git`, и молчаливое ``unavailable`` скрыло бы ошибку конфигурации за
    провенансом, которого нет.
    """
    try:
        result = subprocess.run(
            [git, "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            env=scrubbed_git_env(None),
        )
    except OSError as error:
        raise RunnerError(f"cannot run '{git}': {error}") from error
    if result.returncode != 0:
        return "unavailable"
    return result.stdout.strip() or "unavailable"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
