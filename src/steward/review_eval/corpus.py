"""Корпус review-eval: модель кейса (schema ``review-eval-case/v1``), валидация,
append-only реестр id дефектов/non-defects.

Единица ground truth — дефект с устойчивым id, а не текст исторического
комментария (D7 дизайна). Один YAML-файл — один кейс (один PR-диапазон);
``load_corpus`` собирает каталог, проверяет кросс-кейсовую уникальность id и
сверяет реестр ``_ids.txt`` с корпусом **в обе стороны**: у каждого id записи
есть строка в реестре, и у каждого живого id реестра есть запись в корпусе.

Id несут слаг репо ``owner.name`` (`repo_slug`) — он инъективен, поэтому кейсы
разных репозиториев с одним номером PR не сталкиваются.

Реестр — **append-only файл, last-wins по id**, со строками:

- ``<id> <sha256 содержимого> <sha256 ядра идентичности>`` — запись
  зарегистрирована. Дайджесты отвечают на разные вопросы: ядро (``kind``,
  нормализованный ``file``, ``scenario``) — «та же это запись», содержимое —
  «то же ли у неё наполнение»;
- ``<id> … reidentified`` — смена идентичности, подтверждённая разметчиком;
  строка из трёх полей с другим ядром, но без метки, делает реестр невалидным:
  иначе подмена дефекта под живым id сводилась бы к правке файла руками;
- ``<id> deleted`` — **надгробие**: id израсходован и больше не выдаётся;
  строка с дайджестом после надгробия делает реестр невалидным;
- ``<id> <sha256>`` — строка старого формата: ядро неизвестно, проверяется
  только содержимое. Совпало — первая же регистрация ядро дописывает;
  изменилось — сверять ядро не с чем, и нужен явный ``--reidentify``.

Так различаются случаи, неотличимые по одному дайджесту содержимого. Правка
живой записи (severity, ``line_window``, ``match``) — законна:
``check_registry`` требует **явного акта** и называет
``corpus validate --register``, а ``append_registry`` дописывает новую строку
тем же id, оставляя прежнюю историей. Подмена записи под живым id (сменились
``kind``, ``file`` или ``scenario``) — уже другой дефект: без ``--reidentify`` это
отказ, иначе история измерений по одному id стала бы историей двух разных
дефектов. А вот id, ушедший из корпуса, списывается надгробием — **только по явной
просьбе** (``append_registry(..., retire_deleted=True)``, в CLI
``--register --retire-deleted``): вернуть его нельзя ни под другим дефектом, ни
под тем же содержимым, поэтому необратимый шаг не делается побочным эффектом
регистрации. Правило §5 запрещает переиспользование id после удаления, и без
надгробия его нельзя было бы отличить от обычной перерегистрации.
Спека: ``docs/superpowers/specs/2026-09-14-review-eval-harness-design.md`` §5.

Валидация — вручную, без pydantic (по брифу задачи): каждое правило схемы —
свой явный код и своя тестируемая ошибка ``CorpusError``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "DELETED_MARKER",
    "ENTRY_KINDS",
    "FILE_MISSING_KIND",
    "REPO_RE",
    "repo_slug",
    "Annotation",
    "Case",
    "CorpusError",
    "Defect",
    "Match",
    "NonDefect",
    "append_registry",
    "check_registry",
    "corpus_digest",
    "is_gold",
    "load_case",
    "load_corpus",
    "registry_path",
]

SCHEMA = "review-eval-case/v1"

#: Метка надгробия в реестре id: `<id> deleted` означает, что id израсходован
#: и другой записи не достанется (§5 — id не переиспользуется после удаления).
DELETED_MARKER = "deleted"

#: Метка подтверждённой смены идентичности: `<id> <content> <identity> reidentified`.
#: Она ничего не разрешает сама (разрешает `append_registry(..., reidentify=…)`) —
#: она оставляет в файле след, что под живым id теперь другой дефект и это решение
#: разметчика, а не случайная правка.
REIDENTIFIED_MARKER = "reidentified"

_CLASSES = ("defective", "clean", "large")
_OUTCOMES = ("verdict", "guardrail_rejection")
_ANNOTATION_STATUSES = ("draft", "adjudicated")
_ANNOTATION_SOURCES = ("history-proxy", "manual")
_SEVERITIES = ("blocker", "major", "minor")

#: `kind` записи gold — тот же набор, что у находки кита. `file-missing` значит
#: «файла нет», и такой дефект не живёт в строке: `line_hint` обязан быть 0.
#: Матчер сопоставляет предсказание и gold **только при совпадении `kind`**
#: (§8 шаг 1): «дефект в файле» и «файла нет» — разные утверждения.
ENTRY_KINDS = ("defect", "file-missing")
FILE_MISSING_KIND = "file-missing"

#: Единственные допустимые флаги `local_args` — потолки дифа (§5). Список
#: закрыт намеренно: `local.sh` берёт **последний** `--head`, а раннер дописывает
#: `local_args` после своих `--base`/`--head`, поэтому свободная строка аргументов
#: позволила бы кейсу измерить чужой диапазон под своим gold. Значение каждого
#: флага — положительное целое, и каждый флаг встречается не более одного раза.
LOCAL_ARG_FLAGS: tuple[str, ...] = ("--max-diff-bytes", "--max-diff-files")

#: Форма `repo` — настоящие формы GitHub: владелец `[A-Za-z0-9-]` (логины не
#: содержат ``.`` и ``_``), имя `[A-Za-z0-9._-]`. Одна на весь пакет: из `repo`
#: строится и каталог bare-кэша, и GitHub-URL (`cache.repo_cache_dir`
#: импортирует этот же шаблон), поэтому расхождение правил давало кейс,
#: валидный для корпуса и негодный для кэша.
#:
#: Ограничение владельца — не косметика, а то, что делает слаг инъективным
#: (`repo_slug`): первая точка в `owner.name` всегда разделяет части. Имя
#: ``.``/``..`` запрещено отдельным look-ahead — путь кэша из двух компонент по
#: такому `repo` вывел бы за корень кэша.
REPO_RE = re.compile(r"^[A-Za-z0-9-]+/(?!\.\.?$)[A-Za-z0-9._-]+$")

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ISO_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_POSITIVE_INT_RE = re.compile(r"^[1-9][0-9]*$")

#: Дайджест в реестре id: 64 строчных hex (sha256).
_REGISTRY_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
#: Форма id записи: `D-<slug>-<pr>-<n>` / `NF-…`. Разбор идёт **справа**: `pr`
#: и `n` — последние два числа, а слаг сам содержит `-`, `.`, `_` и цифры
#: (`foo-1.repo`), поэтому левый разбор путал бы границу слага с номером PR.
_DEFECT_ID_RE = re.compile(r"^D-[a-z0-9][a-z0-9._-]*-[0-9]+-[0-9]+$")
_NON_DEFECT_ID_RE = re.compile(r"^NF-[a-z0-9][a-z0-9._-]*-[0-9]+-[0-9]+$")

#: Наборы ключей закрыты на **каждом** уровне кейса: опечатка в имени поля
#: иначе читается как его отсутствие, то есть запись молча получает умолчание
#: (`knd: file-missing` → обычный дефект), и gold говорит не то, что написал
#: разметчик. Заметить такое нечем: схема довольна, дифф выглядит нормально.
_TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "case_id",
        "repo",
        "pr",
        "base_sha",
        "head_sha",
        "class",
        "local_args",
        "expected_outcome",
        "annotation",
        "defects",
        "non_defects",
        "notes",
    }
)

_ANNOTATION_KEYS = frozenset(
    {"status", "blocking_complete", "adjudicated_by", "adjudicated_at", "source"}
)

_DEFECT_KEYS = frozenset(
    {"id", "severity", "file", "line_hint", "scenario", "evidence", "match", "kind"}
)

_NON_DEFECT_KEYS = frozenset({"id", "file", "line_hint", "scenario", "match", "kind"})

_MATCH_KEYS = frozenset({"files", "line_window", "keywords_any"})


class CorpusError(Exception):
    """Кейс, корпус или реестр id нарушает контракт ``review-eval-case/v1``."""


class _DuplicateYamlKey(yaml.constructor.ConstructorError):
    """Повторный ключ в отображении YAML.

    Отдельный тип, чтобы `load_case` назвал ключ и строку, а не пересказывал
    сообщение pyyaml. Сам повтор — не придирка к стилю: `yaml.safe_load`
    оставляет **последнее** значение молча, поэтому второй ``defects: []``
    стирал весь gold, а кейс оставался схемно валидным — с нулём дефектов.
    """

    def __init__(self, key: object, mark: yaml.Mark | None) -> None:
        self.key = key
        self.line = None if mark is None else mark.line + 1
        super().__init__(None, None, f"повторный ключ '{key}'", mark)


class _StrictLoader(yaml.SafeLoader):
    """`SafeLoader`, который отвергает повторный ключ на любом уровне."""


def _construct_mapping_without_duplicates(
    loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    """Собрать отображение, отказав на повторном ключе (см. `_DuplicateYamlKey`)."""
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, Hashable):
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found unhashable key",
                key_node.start_mark,
            )
        if key in mapping:
            raise _DuplicateYamlKey(key, key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_without_duplicates
)


@dataclass(frozen=True)
class Match:
    """Правило сопоставления предсказания находке (генератор рёбер матчера, D10)."""

    files: tuple[str, ...]
    line_window: int
    keywords_any: tuple[str, ...]


@dataclass(frozen=True)
class Defect:
    """Единица ground truth — подтверждённый дефект (D7).

    `kind` — `defect` (умолчание) либо `file-missing`; у второго `line_hint`
    равен 0, и сопоставляется он только с находкой того же `kind`.
    """

    id: str
    severity: str
    file: str
    line_hint: int
    scenario: str
    evidence: tuple[str, ...]
    match: Match
    kind: str = "defect"


@dataclass(frozen=True)
class NonDefect:
    """Историческая находка, признанная ложной разметчиком.

    `kind` — как у `Defect`: ложное «файла нет» кит выдаёт, и разметить его
    non-defect'ом законно.
    """

    id: str
    file: str
    line_hint: int
    scenario: str
    match: Match
    kind: str = "defect"


@dataclass(frozen=True)
class Annotation:
    """Полнота и провенанс разметки кейса (D8)."""

    status: str
    blocking_complete: bool
    adjudicated_by: str | None
    adjudicated_at: str | None
    source: str


@dataclass(frozen=True)
class Case:
    """Один кейс — один PR-диапазон, зафиксированный на ``base_sha``/``head_sha``."""

    case_id: str
    repo: str
    pr: int
    base_sha: str
    head_sha: str
    cls: str
    local_args: tuple[str, ...]
    expected_outcome: str
    annotation: Annotation
    defects: tuple[Defect, ...]
    non_defects: tuple[NonDefect, ...]
    notes: str


def load_case(path: Path) -> Case:
    """Загрузить и провалидировать один кейс. ``CorpusError`` на любом нарушении схемы.

    Читается **строгим** загрузчиком (`_StrictLoader`): повторный ключ на любом
    уровне — ошибка, а не «побеждает последний». Умолчание pyyaml здесь опасно
    тихо: второй ``defects: []`` стирал бы весь gold кейса, оставляя его
    формально валидным.
    """
    try:
        raw = yaml.load(path.read_bytes(), Loader=_StrictLoader)  # noqa: S506 — SafeLoader-подтип
    except OSError as exc:
        raise CorpusError(f"cannot read {path}: {exc}") from exc
    except _DuplicateYamlKey as exc:
        raise CorpusError(f"{path}: повторный ключ '{exc.key}' (строка {exc.line})") from exc
    except yaml.YAMLError as exc:
        raise CorpusError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CorpusError(f"{path}: top level must be a mapping")

    where = str(path)
    _require_keys(raw, _TOP_LEVEL_KEYS, where, what="верхнего уровня")

    schema = _str(raw, "schema", where)
    if schema != SCHEMA:
        raise CorpusError(f"{where}: schema must be '{SCHEMA}', got '{schema}'")

    repo = _str(raw, "repo", where)
    pr = _int(raw, "pr", where)
    case_id = _str(raw, "case_id", where)
    expected_case_id = f"{_repo_slug(repo, where)}-{pr}"
    if case_id != expected_case_id:
        raise CorpusError(
            f"{where}: case_id '{case_id}' must equal '{expected_case_id}' "
            f"(repo short name + '-' + pr)"
        )

    base_sha = _str(raw, "base_sha", where)
    head_sha = _str(raw, "head_sha", where)
    _check_sha(base_sha, "base_sha", where)
    _check_sha(head_sha, "head_sha", where)
    if base_sha == head_sha:
        raise CorpusError(f"{where}: base_sha and head_sha must differ")

    cls = _str(raw, "class", where)
    if cls not in _CLASSES:
        raise CorpusError(f"{where}: class must be one of {_CLASSES}, got '{cls}'")

    local_args = _parse_local_args(raw, where, cls=cls)

    expected_outcome = _str(raw, "expected_outcome", where)
    if expected_outcome not in _OUTCOMES:
        raise CorpusError(
            f"{where}: expected_outcome must be one of {_OUTCOMES}, got '{expected_outcome}'"
        )

    if cls == "large" and not local_args and expected_outcome != "guardrail_rejection":
        raise CorpusError(
            f"{where}: class 'large' requires non-empty local_args or "
            f"expected_outcome == 'guardrail_rejection'"
        )

    annotation = _parse_annotation(raw, where)

    # Id записи обязан нести слаг и PR **своего** кейса: `D-foo-9-1` в кейсе
    # `steward-161` формально похож на id, но указывает на чужой PR, и
    # глобальная уникальность такое столкновение не поймает.
    slug = _repo_slug(repo, where)
    defects = tuple(
        _parse_defect(item, i, where, slug=slug, pr=pr)
        for i, item in enumerate(_list(raw, "defects", where))
    )
    non_defects = tuple(
        _parse_non_defect(item, i, where, slug=slug, pr=pr)
        for i, item in enumerate(_list(raw, "non_defects", where))
    )

    notes = raw.get("notes", "")
    if not isinstance(notes, str):
        raise CorpusError(f"{where}: 'notes' must be a string")

    return Case(
        case_id=case_id,
        repo=repo,
        pr=pr,
        base_sha=base_sha,
        head_sha=head_sha,
        cls=cls,
        local_args=local_args,
        expected_outcome=expected_outcome,
        annotation=annotation,
        defects=defects,
        non_defects=non_defects,
        notes=notes,
    )


def load_corpus(directory: Path, *, git: str = "git") -> list[Case]:
    """Загрузить все ``*.yaml`` кейсы каталога.

    Проверяет уникальность ``case_id`` и id дефектов/non-defects по всему
    корпусу, затем сверяет их с реестром (``check_registry``). Возвращает
    кейсы, отсортированные по ``case_id``.

    Несуществующий путь (или путь не к каталогу) — `CorpusError`, а не пустой
    корпус: ``glob`` по опечатке в ``--corpus`` вернул бы пусто, и команда
    ответила бы «кейсов 0» с кодом 0 — то есть выдала бы ошибку пути за
    «мерить нечего». Существующий пустой каталог пустым корпусом остаётся:
    это законное начальное состояние.
    """
    if not directory.exists():
        raise CorpusError(f"каталога корпуса нет: {directory}")
    if not directory.is_dir():
        raise CorpusError(f"путь корпуса — не каталог: {directory}")
    paths = sorted(directory.glob("*.yaml"))
    loaded = [(p, load_case(p)) for p in paths]

    seen_case_ids: dict[str, Path] = {}
    for path, case in loaded:
        prior = seen_case_ids.get(case.case_id)
        if prior is not None:
            raise CorpusError(f"duplicate case_id '{case.case_id}': {prior} and {path}")
        seen_case_ids[case.case_id] = path

    seen_ids: dict[str, Path] = {}
    for path, case in loaded:
        for entry_id in (*(d.id for d in case.defects), *(n.id for n in case.non_defects)):
            prior = seen_ids.get(entry_id)
            if prior is not None:
                raise CorpusError(f"duplicate id '{entry_id}': {prior} and {path}")
            seen_ids[entry_id] = path

    cases = sorted((case for _, case in loaded), key=lambda c: c.case_id)
    check_registry(cases, directory, git=git)
    return cases


def corpus_digest(cases: Sequence[Case]) -> str:
    """``sha256:<hex>`` канонического JSON корпуса (порядок кейсов не важен)."""
    payload = [dataclasses.asdict(c) for c in sorted(cases, key=lambda c: c.case_id)]
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_gold(case: Case) -> bool:
    """Кейс входит в официальные метрики (D8): разметка завершена (не ``draft``)."""
    return case.annotation.status == "adjudicated"


def registry_path(directory: Path) -> Path:
    """Путь к append-only реестру id корпуса."""
    return directory / "_ids.txt"


def check_registry(cases: Sequence[Case], directory: Path, *, git: str = "git") -> None:
    """Каждый id дефекта/non-defect обязан быть в реестре с текущим дайджестом.

    Сверяется с **последней** строкой реестра для этого id (файл last-wins):

    - последняя строка — надгробие (``deleted``), а id снова в корпусе →
      ``CorpusError`` **независимо от содержимого**: id израсходован, нужен
      новый. Сравнивать тут дайджесты было бы подменой правила: совпадение
      текста не делает запись «той же» — она уже была удалена;
    - id в реестре нет → ``CorpusError``, называющая ``--register``;
    - **ядро идентичности** (`file` + `scenario`) не совпало → ``CorpusError``:
      под живым id оказался другой дефект. «Дайджест изменился» этого не
      отличал, а разница существенная: история измерений по такому id стала бы
      историей двух разных дефектов. Лечится новым id или явным
      подтверждением (`--register --reidentify <id>`);
    - ядро совпало, а дайджест содержимого нет → ``CorpusError``, называющая
      ``--register``: правка живой записи законна, но подтверждается явно.

    И **обратная сторона**: каждый живой (не списанный) id реестра обязан иметь
    запись в корпусе. Иначе удаление YAML-кейса оставляло id
    зарегистрированным и свободным одновременно — он никому не принадлежал, а
    вернуться мог с тем же содержимым, и все проверки молчали. Строка старого
    формата считается живой наравне с трёхполевой: иначе достаточно было бы
    оставить её, чтобы id висел без кейса.
    """
    registry = _read_registry(directory, git=git)
    present = {entry.id for case in cases for entry in (*case.defects, *case.non_defects)}
    orphans = sorted(
        entry_id
        for entry_id, state in registry.items()
        if not state.deleted and entry_id not in present
    )
    if orphans:
        raise CorpusError(
            f"{registry_path(directory)}: id '{orphans[0]}' зарегистрирован, но кейса с ним "
            f"нет — спишите его ('corpus validate --register --retire-deleted') или верните "
            f"кейс (всего таких id: {len(orphans)})"
        )
    for case in cases:
        for entry in (*case.defects, *case.non_defects):
            existing = registry.get(entry.id)
            if existing is not None and existing.deleted:
                raise CorpusError(
                    f"id '{entry.id}' списан (удалён из корпуса ранее) — заведите новый id; "
                    f"надгробие в {registry_path(directory)} снимать нельзя"
                )
            if existing is None:
                raise CorpusError(
                    f"id '{entry.id}' не зарегистрирован в {registry_path(directory)}; "
                    f"запустите 'review-eval corpus validate --register'"
                )
            identity = _identity_digest(entry)
            if existing.identity is not None and existing.identity != identity:
                raise CorpusError(_identity_conflict(entry.id))
            digest = _entry_digest(entry)
            if existing.identity is None and existing.content != digest:
                raise CorpusError(_legacy_conflict(entry.id))
            if existing.content != digest:
                raise CorpusError(
                    f"id '{entry.id}' зарегистрирован с другим содержимым "
                    f"(реестр: {existing.content}, сейчас: {digest}); тот же дефект — "
                    f"перерегистрируйте: 'review-eval corpus validate --register'; "
                    f"другой дефект — дайте ему новый id"
                )


def _legacy_conflict(entry_id: str) -> str:
    """Сообщение о строке старого формата с изменившимся содержимым.

    У такой строки ядра идентичности нет, поэтому «тот же это дефект или
    другой» проверить **нечем**: молчаливое дописывание ядра сделало бы из
    старой строки дыру в правиле §5. Ответ — не «поверим», а «подтвердите».
    """
    return (
        f"id '{entry_id}' зарегистрирован строкой старого формата (без ядра идентичности), "
        f"а содержимое изменилось — проверить, тот же ли это дефект, нечем; заведите новый id "
        f"или подтвердите: 'review-eval corpus validate --register --reidentify {entry_id}'"
    )


def _identity_conflict(entry_id: str) -> str:
    """Сообщение о подмене записи под живым id — один текст на проверку и запись."""
    return (
        f"это другой дефект под живым id '{entry_id}': заведите новый id или "
        f"подтвердите смену идентичности "
        f"('review-eval corpus validate --register --reidentify {entry_id}')"
    )


def append_registry(
    cases: Sequence[Case],
    directory: Path,
    *,
    retire_deleted: bool = False,
    reidentify: frozenset[str] = frozenset(),
    git: str = "git",
) -> list[str]:
    """Привести реестр в соответствие с корпусом; вернуть id к списанию.

    `cases` — **весь** корпус каталога (как его отдаёт `load_corpus`, как его
    собирает `corpus validate --register`): присутствие id определяется по
    этому списку, и подмножество выглядело бы как массовое удаление.

    Регистрация идёт всегда (дописыванием строк — файл append-only):

    1. новый id → строка ``<id> <content> <identity>``;
    2. живой id с изменившимся содержимым, но **тем же** ядром идентичности →
       новая строка (прежняя остаётся историей);
    3. строка старого формата (без ядра) и **то же** содержимое → новая строка
       с ядром: пока ядро неизвестно, подмену записи поймать нечем, и первая же
       регистрация это закрывает. Если же содержимое у такой строки изменилось,
       сверять ядро не с чем — это `CorpusError` с требованием `--reidentify`:
       иначе строка старого формата оставалась бы дырой в правиле §5.

    **Смена идентичности — только по `reidentify`.** Сменившееся ядро значит,
    что под живым id теперь другой дефект: без подтверждения это `CorpusError`
    (тот же текст, что у `check_registry`), с подтверждением — строка с меткой
    ``reidentified``, то есть решение остаётся в файле, а не только в голове
    разметчика.

    **Списание — только по `retire_deleted=True`.** Надгробие необратимо
    (`check_registry` после него отказывает навсегда), поэтому оно не может
    быть побочным эффектом обычной регистрации: неверный `--corpus` или
    неполное рабочее дерево иначе обнуляли бы ground truth молча. Без флага
    отсутствующие id не трогаются вовсе, а возвращённый список говорит, что
    списалось бы, — CLI печатает его и предлагает `--retire-deleted`.

    Возвращает отсортированный список id, у которых нет записи в корпусе и ещё
    нет надгробия: с флагом — только что списанных, без флага — кандидатов.
    `reidentify` — id, которым разметчик разрешил смену идентичности.

    Пустой `cases` при непустом (не считая надгробий) реестре — `CorpusError`
    независимо от флага: «в каталоге нет ни одного кейса» почти всегда значит
    неверный путь, а не «все дефекты удалены». Следствие, о котором стоит
    знать: если корпус действительно опустел, а живые id остались, командой их
    не списать — оператор либо возвращает кейсы, либо дописывает надгробия
    руками, и то и другое проходит ревью PR (и проверку append-only).

    Идемпотентна: совпавший дайджест и уже поставленное надгробие не
    дописываются. Списанный id **не возвращается**: даже если он снова в
    корпусе, новой строки с дайджестом он не получит. Это не только запрет по
    смыслу (правило §5 «id не переиспользуется»), но и требование формата:
    надгробие терминально, и такая строка сделала бы весь реестр невалидным
    (`_read_registry`).
    """
    registry = _read_registry(directory, git=git)
    present = {entry.id for case in cases for entry in (*case.defects, *case.non_defects)}
    live = sorted(entry_id for entry_id, last in registry.items() if not last.deleted)
    if not present and live:
        raise CorpusError(
            f"корпус пуст, а реестр — нет: списание всех id запрещено "
            f"(укажите каталог корпуса; последние живые id списываются вручную — "
            f"допишите строки '<id> deleted' под ревью PR, файл не удалять); "
            f"живых id в {registry_path(directory)}: {len(live)}"
        )
    retiring = [entry_id for entry_id in live if entry_id not in present]
    new_lines = [f"{entry_id} {DELETED_MARKER}" for entry_id in retiring] if retire_deleted else []
    for case in cases:
        for entry in (*case.defects, *case.non_defects):
            last = registry.get(entry.id)
            if last is not None and last.deleted:
                continue
            digest = _entry_digest(entry)
            identity = _identity_digest(entry)
            unverifiable = last is not None and last.identity is None and last.content != digest
            changed = last is not None and last.identity is not None and last.identity != identity
            if unverifiable or changed:
                if entry.id not in reidentify:
                    raise CorpusError(
                        _identity_conflict(entry.id) if changed else _legacy_conflict(entry.id)
                    )
                new_lines.append(f"{entry.id} {digest} {identity} {REIDENTIFIED_MARKER}")
                registry[entry.id] = _Registered(content=digest, identity=identity)
                continue
            if last is not None and last.content == digest and last.identity == identity:
                continue
            new_lines.append(f"{entry.id} {digest} {identity}")
            registry[entry.id] = _Registered(content=digest, identity=identity)
    if new_lines:
        path = registry_path(directory)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for line in new_lines:
                f.write(line + "\n")
    return retiring


# ---------------------------------------------------------------------------
# внутренние разборщики полей
# ---------------------------------------------------------------------------


def repo_slug(repo: str) -> str:
    """Слаг репо — ``owner.name`` в нижнем регистре (§5).

    Слаг должен быть **инъективным**: он входит в `case_id` и в id дефектов, и
    два репозитория с одним слагом столкнулись бы по id на одном номере PR.
    Прежнее правило (только имя, `_`/`.` схлопнуты в `-`) этого не давало:
    ``org-a/service`` и ``org-b/service`` получали один слаг, как и
    ``org/my_repo`` с ``org/my.repo``.

    Инъективность держится на двух фактах о GitHub: логин владельца не
    содержит ``.`` (алфавит `REPO_RE` — `[A-Za-z0-9-]`), поэтому **первая**
    точка в слаге всегда разделяет части; имена репозиториев
    регистронезависимы, поэтому нижний регистр склеивает только одно и то же
    репо.
    """
    if not REPO_RE.fullmatch(repo):
        raise CorpusError(
            f"repo must look like 'owner/name' ([A-Za-z0-9-]/[A-Za-z0-9._-]), got '{repo}'"
        )
    owner, _sep, name = repo.partition("/")
    return f"{owner.lower()}.{name.lower()}"


def _repo_slug(repo: str, where: str) -> str:
    """`repo_slug` с адресом кейса в сообщении об ошибке."""
    try:
        return repo_slug(repo)
    except CorpusError as error:
        raise CorpusError(f"{where}: {error}") from error


def _parse_local_args(raw: dict[str, Any], where: str, *, cls: str) -> tuple[str, ...]:
    """Разобрать `local_args` — закрытый список потолков дифа (§5).

    Аргументы кейса дописываются киту **после** `--base`/`--head` раннера, а
    `local.sh` берёт последний такой флаг: свободная строка позволила бы кейсу
    измерить диапазон A..C, оставшись под gold диапазона A..B. Поэтому здесь
    не «санитизация», а разрешительный список: пары
    ``<флаг из LOCAL_ARG_FLAGS> <положительное целое>``, каждый флаг не более
    одного раза.

    Непустые `local_args` вне `class: large` — тоже ошибка: потолок дифа имеет
    смысл только там, где диф крупный, и в остальных классах он менял бы
    измерение без причины.
    """
    values = _str_tuple(raw.get("local_args", []), "local_args", where)
    if not values:
        return ()
    if cls != "large":
        raise CorpusError(
            f"{where}: 'local_args' допустимы только у class 'large', got class '{cls}'"
        )
    if len(values) % 2:
        raise CorpusError(
            f"{where}: 'local_args' must be flag/value pairs from {LOCAL_ARG_FLAGS}, "
            f"got an odd number of items: {list(values)}"
        )
    seen: set[str] = set()
    for flag, value in zip(values[::2], values[1::2], strict=True):
        if flag not in LOCAL_ARG_FLAGS:
            raise CorpusError(
                f"{where}: 'local_args' flag '{flag}' is not allowed; "
                f"allowed flags: {LOCAL_ARG_FLAGS}"
            )
        if flag in seen:
            raise CorpusError(f"{where}: 'local_args' flag '{flag}' is given more than once")
        seen.add(flag)
        if not _POSITIVE_INT_RE.fullmatch(value):
            raise CorpusError(
                f"{where}: 'local_args' value for '{flag}' must be a positive int, got '{value}'"
            )
    return values


def _check_sha(value: str, field_name: str, where: str) -> None:
    if not _SHA_RE.fullmatch(value):
        raise CorpusError(f"{where}: {field_name} must be 40 lowercase hex chars, got '{value}'")


def _check_iso_date(value: str, field_name: str, where: str) -> None:
    """Дата строго ``YYYY-MM-DD``: сначала форма, потом существование даты.

    `date.fromisoformat` в Python 3.11+ принимает и ``20260915``, и
    ``2026-W01-1``: поле провенанса читают люди и грепают скрипты, и две формы
    одной даты сделали бы из него две разные строки.
    """
    if not _ISO_DATE_RE.fullmatch(value):
        raise CorpusError(f"{where}: {field_name} must be an ISO date 'YYYY-MM-DD', got '{value}'")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise CorpusError(
            f"{where}: {field_name} is not a real date (YYYY-MM-DD), got '{value}'"
        ) from exc


def _require_keys(
    mapping: dict[str, Any], allowed: frozenset[str], where: str, *, what: str
) -> None:
    """Отказать на ключе вне закрытого набора, назвав ключ и место.

    Одна проверка на все уровни кейса: неизвестный ключ — почти всегда опечатка
    в известном, а опечатка читается как «поля нет» и молча включает умолчание.
    """
    # Ключи приводятся к строке: YAML отдаёт `1:` как int, а str и int вместе
    # не сортируются — без приведения наружу уходил бы TypeError, не CorpusError.
    unknown = sorted(str(key) for key in mapping if key not in allowed)
    if unknown:
        raise CorpusError(
            f"{where}: неизвестный ключ '{unknown[0]}' в {what} "
            f"(допустимы: {', '.join(sorted(allowed))})"
        )


def _parse_annotation(raw: dict[str, Any], where: str) -> Annotation:
    ann = raw.get("annotation")
    if not isinstance(ann, dict):
        raise CorpusError(f"{where}: 'annotation' must be a mapping")
    sub_where = f"{where}: annotation"
    _require_keys(ann, _ANNOTATION_KEYS, sub_where, what="annotation")

    status = _str(ann, "status", sub_where)
    if status not in _ANNOTATION_STATUSES:
        raise CorpusError(
            f"{sub_where}: status must be one of {_ANNOTATION_STATUSES}, got '{status}'"
        )

    blocking_complete = ann.get("blocking_complete")
    if not isinstance(blocking_complete, bool):
        raise CorpusError(f"{sub_where}: 'blocking_complete' must be a bool")

    source = _str(ann, "source", sub_where)
    if source not in _ANNOTATION_SOURCES:
        raise CorpusError(
            f"{sub_where}: source must be one of {_ANNOTATION_SOURCES}, got '{source}'"
        )

    adjudicated_by = _optional_str(ann, "adjudicated_by", sub_where)
    adjudicated_at = _optional_str(ann, "adjudicated_at", sub_where)

    # Поле необязательно, но пробельное значение — не «его нет», а «есть, и
    # оно пустое»: разметка выдавалась бы за подтверждённую кем-то. Проверка не
    # зависит от `status`: объявлено — значит должно что-то называть.
    if adjudicated_by is not None and _is_blank(adjudicated_by):
        raise CorpusError(f"{sub_where}: 'adjudicated_by' must not be blank")

    if status == "adjudicated":
        if not adjudicated_by:
            raise CorpusError(f"{sub_where}: status 'adjudicated' requires adjudicated_by")
        if not adjudicated_at:
            raise CorpusError(f"{sub_where}: status 'adjudicated' requires adjudicated_at")
        _check_iso_date(adjudicated_at, "adjudicated_at", sub_where)

    return Annotation(
        status=status,
        blocking_complete=blocking_complete,
        adjudicated_by=adjudicated_by,
        adjudicated_at=adjudicated_at,
        source=source,
    )


def _parse_match(raw: object, where: str) -> Match:
    if not isinstance(raw, dict):
        raise CorpusError(f"{where}: 'match' must be a mapping")
    _require_keys(raw, _MATCH_KEYS, where, what="match")
    files = _str_tuple(
        raw.get("files"), "files", where, allow_empty=False, allow_blank=False, unique=True
    )
    line_window = raw.get("line_window")
    if not isinstance(line_window, int) or isinstance(line_window, bool) or line_window < 0:
        raise CorpusError(f"{where}: 'line_window' must be a non-negative int")
    keywords_any = _str_tuple(
        raw.get("keywords_any"),
        "keywords_any",
        where,
        allow_empty=False,
        allow_blank=False,
        unique=True,
    )
    return Match(files=files, line_window=line_window, keywords_any=keywords_any)


def _entry_id(
    item: dict[str, Any], sub_where: str, *, prefix: str, generic: re.Pattern[str]
) -> str:
    """Id записи: общая форма плюс обязательные слаг и PR своего кейса."""
    value = _str(item, "id", sub_where)
    if not generic.fullmatch(value) or not re.fullmatch(rf"{re.escape(prefix)}[0-9]+", value):
        raise CorpusError(f"{sub_where}: id '{value}' must match '{prefix}<n>'")
    return value


def _parse_defect(item: object, index: int, where: str, *, slug: str, pr: int) -> Defect:
    if not isinstance(item, dict):
        raise CorpusError(f"{where}: defects[{index}] must be a mapping")
    sub_where = f"{where}: defects[{index}]"
    _require_keys(item, _DEFECT_KEYS, sub_where, what=f"defects[{index}]")

    defect_id = _entry_id(item, sub_where, prefix=f"D-{slug}-{pr}-", generic=_DEFECT_ID_RE)
    severity = _str(item, "severity", sub_where)
    if severity not in _SEVERITIES:
        raise CorpusError(f"{sub_where}: severity must be one of {_SEVERITIES}, got '{severity}'")
    file_ = _non_blank_str(item, "file", sub_where)
    line_hint = _int(item, "line_hint", sub_where)
    scenario = _non_blank_str(item, "scenario", sub_where)
    evidence = _str_tuple(
        item.get("evidence"), "evidence", sub_where, allow_empty=False, allow_blank=False
    )
    match = _parse_match(item.get("match"), f"{sub_where}: match")
    kind = _parse_kind(item, line_hint, sub_where)

    return Defect(
        id=defect_id,
        severity=severity,
        file=file_,
        line_hint=line_hint,
        scenario=scenario,
        evidence=evidence,
        match=match,
        kind=kind,
    )


def _parse_kind(item: dict[str, Any], line_hint: int, sub_where: str) -> str:
    """`kind` записи: набор закрыт, а `file-missing` несовместим со строкой."""
    kind = item.get("kind", "defect")
    if kind not in ENTRY_KINDS:
        raise CorpusError(f"{sub_where}: kind must be one of {ENTRY_KINDS}, got '{kind}'")
    if kind == FILE_MISSING_KIND and line_hint != 0:
        raise CorpusError(
            f"{sub_where}: kind '{FILE_MISSING_KIND}' requires line_hint 0 "
            f"(файла нет — строки в нём тоже), got {line_hint}"
        )
    return kind


def _parse_non_defect(item: object, index: int, where: str, *, slug: str, pr: int) -> NonDefect:
    if not isinstance(item, dict):
        raise CorpusError(f"{where}: non_defects[{index}] must be a mapping")
    sub_where = f"{where}: non_defects[{index}]"

    # Сначала — про gold-поля отдельным сообщением: `severity`/`evidence` у
    # non-defect не «неизвестный ключ», а поле, которое здесь не имеет смысла.
    forbidden = sorted({"severity", "evidence"} & set(item))
    if forbidden:
        raise CorpusError(f"{sub_where}: must not carry {forbidden} (gold defect fields only)")
    _require_keys(item, _NON_DEFECT_KEYS, sub_where, what=f"non_defects[{index}]")

    non_defect_id = _entry_id(item, sub_where, prefix=f"NF-{slug}-{pr}-", generic=_NON_DEFECT_ID_RE)
    file_ = _non_blank_str(item, "file", sub_where)
    line_hint = _int(item, "line_hint", sub_where)
    scenario = _non_blank_str(item, "scenario", sub_where)
    match = _parse_match(item.get("match"), f"{sub_where}: match")
    kind = _parse_kind(item, line_hint, sub_where)

    return NonDefect(
        id=non_defect_id,
        file=file_,
        line_hint=line_hint,
        scenario=scenario,
        match=match,
        kind=kind,
    )


def _str(raw: dict[str, Any], key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise CorpusError(f"{where}: '{key}' must be a non-empty string")
    return value


def _optional_str(raw: dict[str, Any], key: str, where: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, date):
        # YAML резолвит нецитированную ISO-дату (2026-09-15) как datetime.date.
        return value.isoformat()
    if not isinstance(value, str):
        raise CorpusError(f"{where}: '{key}' must be a string or absent")
    return value


def _int(raw: dict[str, Any], key: str, where: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise CorpusError(f"{where}: '{key}' must be an int")
    return value


def _list(raw: dict[str, Any], key: str, where: str) -> list[Any]:
    value = raw.get(key, [])
    if not isinstance(value, list):
        raise CorpusError(f"{where}: '{key}' must be a list")
    return value


def _str_tuple(
    value: object,
    field_name: str,
    where: str,
    *,
    allow_empty: bool = True,
    allow_blank: bool = True,
    unique: bool = False,
) -> tuple[str, ...]:
    """Список строк; `allow_blank=False` запрещает пробельные элементы.

    Пустая строка — не значение, а его отсутствие, и в правилах сопоставления
    она опаснее отсутствия: `"" in haystack` истинно всегда, а
    `normalize_path("")` совпадает с таким же пустым путём с другой стороны.

    `unique=True` запрещает повтор после ``strip().casefold()``. Это не
    гигиена списка: вес ребра матчера — **число** попавших ключей, поэтому
    ``["Path", "path"]`` давало двойку там, где сказано одно слово, и такой
    дефект побеждал равного конкурента — неоднозначность превращалась в TP.
    Сравнение регистронезависимо, потому что и сам матчинг ключей такой.
    """
    if value is None:
        value = []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise CorpusError(f"{where}: '{field_name}' must be a list of strings")
    if not allow_empty and not value:
        raise CorpusError(f"{where}: '{field_name}' must be non-empty")
    if not allow_blank and any(_is_blank(item) for item in value):
        raise CorpusError(
            f"{where}: '{field_name}' must not contain blank items, got {list(value)}"
        )
    if unique:
        seen: set[str] = set()
        for item in value:
            key = item.strip().casefold()
            if key in seen:
                raise CorpusError(
                    f"{where}: '{field_name}' содержит повтор '{key}' "
                    f"(сравнение без учёта регистра и пробелов по краям) — "
                    f"повтор завышает вес ребра матчера, не добавляя доказательства"
                )
            seen.add(key)
    return tuple(value)


def _is_blank(value: str) -> bool:
    """Строка без непробельных символов."""
    return not value.strip()


def _non_blank_str(raw: dict[str, Any], key: str, where: str) -> str:
    """Непустая строка, в которой есть хоть один непробельный символ."""
    value = _str(raw, key, where)
    if _is_blank(value):
        raise CorpusError(f"{where}: '{key}' must not be blank")
    return value


def _entry_digest(entry: Defect | NonDefect) -> str:
    """sha256 канонического JSON одной записи (для реестра ``_ids.txt``)."""
    canonical = json.dumps(
        dataclasses.asdict(entry), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalized_file(value: str) -> str:
    """Минимальная нормализация написания пути — для ядра идентичности.

    Разделители к POSIX, схлопнутые повторы, снятые ``./`` и пробелы по краям:
    переписывание ``./scripts//a.sh`` → ``scripts/a.sh`` — правка написания, а
    не смена дефекта. Это **не** правило матчинга (`matcher.normalize_path`):
    матчер сравнивает пути находки и правила, а здесь решается вопрос «та же
    это запись, что зарегистрирована».
    """
    path = value.strip().replace("\\", "/")
    while "//" in path:
        path = path.replace("//", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def _identity_digest(entry: Defect | NonDefect) -> str:
    """sha256 **ядра идентичности**: `kind`, нормализованный `file`, `scenario`.

    Ядро отвечает на вопрос «это та же запись?», в отличие от `_entry_digest`,
    который отвечает «то же ли у неё содержимое». Правка severity, `line_hint`,
    `match` или `evidence` — уточнение известного дефекта; смена файла,
    сценария или `kind` — другой дефект, и оставлять ему прежний id нельзя
    молча: история измерений по этому id стала бы историей двух разных
    дефектов. `kind` в ядре именно поэтому: «дефект в файле» и «файла нет» —
    разные утверждения, а не разная формулировка одного.
    """
    core = {
        "kind": entry.kind,
        "file": _normalized_file(entry.file),
        "scenario": entry.scenario.strip(),
    }
    canonical = json.dumps(core, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _Registered:
    """Состояние id по последней строке реестра.

    `identity` — ``None`` у строки старого (двухполевого) формата: реестры,
    заведённые до ядра идентичности, не объявляются повреждёнными, у них
    проверяется только содержимое, а первая же регистрация фиксирует ядро.
    """

    content: str | None
    identity: str | None
    deleted: bool = False
    #: Строка несла метку `reidentified`, то есть смена ядра объявлена явно.
    reidentified: bool = False


def _read_registry(directory: Path, *, git: str = "git") -> dict[str, _Registered]:
    """Последнее состояние каждого id (§5). Формы строк:

    - ``<id> <content>`` — старый формат: содержимое известно, идентичность нет;
    - ``<id> <content> <identity>`` — текущий формат;
    - ``<id> <content> <identity> reidentified`` — смена идентичности, подтверждённая
      разметчиком;
    - ``<id> deleted`` — надгробие.

    Файл append-only, поэтому читается он целиком, а решает — **последняя**
    строка про id: она и есть текущее состояние.

    **Надгробие терминально.** Строка с дайджестом после ``<id> deleted``
    делает реестр невалидным (`CorpusError` с номером строки), а не «новым
    состоянием»: иначе запрет на переиспользование id обходился бы правкой
    файла на одну строку — состояние last-wins воскрешало бы id, и
    `check_registry`, который смотрит только на состояние, этого не заметил бы.
    Повторное надгробие терпится: списание идемпотентно.

    **Смена ядра объявляется меткой.** Строка из трёх полей, несущая другое
    ядро живого id, чьё прежнее ядро известно, делает реестр невалидным
    (`_unacknowledged_identity_change`): обойти `--reidentify` правкой файла
    на одну строку нельзя.
    """
    path = registry_path(directory)
    if not path.exists():
        _require_no_registry_history(path, git=git)
        return {}
    _require_append_only(path, git=git)
    registry: dict[str, _Registered] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entry_id, state = _parse_registry_line(stripped, path, lineno)
        previous = registry.get(entry_id)
        if previous is not None and previous.deleted and not state.deleted:
            raise CorpusError(
                f"{path}: реестр повреждён: строка после надгробия для '{entry_id}' "
                f"(строка {lineno}) — списанный id не возвращается, заведите новый"
            )
        if _forgets_known_identity(previous, state):
            raise CorpusError(
                f"{path}: строка {lineno} — понижение формата: двухполевая строка "
                f"после известного ядра '{entry_id}' стирала бы идентичность, "
                f"чтобы затем сменить её без reidentified"
            )
        if _unacknowledged_identity_change(previous, state):
            raise CorpusError(
                f"{path}: строка {lineno} меняет идентичность '{entry_id}' без "
                f"подтверждения reidentified — новый дефект получает новый id, "
                f"смена ядра подтверждается --reidentify"
            )
        registry[entry_id] = state
    return registry


def _forgets_known_identity(previous: _Registered | None, state: _Registered) -> bool:
    """Двухполевая строка после строки с известным ядром живого id.

    Обход метки в два шага: «забыть» ядро строкой старого формата, потом
    дописать трёхполевую с новым — второй шаг прошёл бы по правилу «прежнее
    ядро неизвестно». Старый формат допустим только пока ядро ещё не
    известно; надгробие — своё правило.
    """
    if previous is None or previous.deleted or state.deleted:
        return False
    return previous.identity is not None and state.identity is None


def _unacknowledged_identity_change(previous: _Registered | None, state: _Registered) -> bool:
    """Меняет ли строка известное ядро живого id, не объявив этого меткой.

    `append_registry` без `--reidentify` смену ядра отвергает, но реестр —
    текстовый файл: дописать третьим полем другое ядро можно руками, и
    состояние last-wins принимало его как текущее. Дальше `check_registry`
    сверял кейс с **уже подменённым** ядром и молчал, то есть подмена дефекта
    под живым id сводилась к правке файла на одну строку. Метка `reidentified`
    и есть то, что отличает решение разметчика от такой правки.

    Правило узкое намеренно:

    - прежнее состояние — надгробие → своё правило (строка после надгробия);
    - прежнее ядро неизвестно (строка старого, двухполевого формата) →
      сравнивать не с чем, остаётся правило раунда 5: первая же регистрация
      ядро дописывает, а изменившееся содержимое требует `--reidentify`;
    - новая строка ядра не несёт (двухполевая) при известном прежнем ядре →
      понижение формата, отдельное правило (`_forgets_known_identity`);
    - ядро то же, содержимое другое → законная перерегистрация правки, метки
      не требует: иначе метка обесценилась бы.
    """
    if previous is None or previous.deleted or state.deleted:
        return False
    if previous.identity is None or state.identity is None:
        return False
    return state.identity != previous.identity and not state.reidentified


def _require_append_only(path: Path, *, git: str) -> None:
    """Реестр обязан только расти — и в истории git, и в рабочем файле (§5).

    Реестр сам себе не доказательство: стерев строку, можно вернуть занятый или
    списанный id. Настоящая гарантия — git и ревью PR, и эта проверка её
    предъявляет двумя утверждениями:

    1. **каждая закоммиченная версия продолжает каждого своего родителя** —
       содержательные строки версии родителя стоят в версии потомка на тех же
       местах и без правок. Проверять снимки против рабочего файла было
       недостаточно: «удалить строку коммитом и вернуть её следующим» проходило
       (в рабочем файле она снова есть, и каждый снимок — его префикс), а между
       коммитами id был свободен;
    2. **рабочий файл продолжает ``HEAD``** — иначе правку ловило бы только
       ревью, а валидатор молчал.

    Строки-комментарии сравнением не участвуют: шапка файла — документация, её
    правят руками, и запрет на это лечил бы не ту болезнь.

    **Отслеживаемость файла ничего не решает.** Прежде проверка начиналась с
    `ls-files`, который для неотслеживаемого файла отказывает, — и функция
    молча возвращалась, не посмотрев историю. Достаточно было удалить реестр
    коммитом и положить на его место новый файл, не добавляя в индекс:
    id-пространство обнулено, гейт зелёный. Решает **история**: есть хоть один
    коммит по этому пути — проверяем; нет — законный новый реестр.

    Проверка **best-effort**: каталог не репозиторий, нет коммитов по файлу или
    самого бинаря git — молча пропускаем. Инструмент не обязан работать только
    внутри git-чекаута, а там, где git есть, он и есть источник истории.
    """
    directory = str(path.parent)
    full_name = _repo_relative_name(path, git=git)
    if full_name is None:
        return
    cache: dict[str, list[tuple[int, str]] | None] = {}

    def version(revision: str) -> list[tuple[int, str]] | None:
        """Содержательные строки файла в ревизии; ``None`` — файла там нет."""
        if revision not in cache:
            text = _git_text(git, directory, ["show", f"{revision}:{full_name}"])
            cache[revision] = None if text is None else _data_lines(text.splitlines())
        return cache[revision]

    _require_history_monotonic(path, version, git=git, full_name=full_name)

    head = version("HEAD")
    if head is None:
        return
    working = _data_lines(path.read_text(encoding="utf-8").splitlines())
    divergence = _first_divergence(head, working)
    if divergence is not None:
        lineno, line = divergence
        raise CorpusError(
            f"{path}: реестр не append-only: рабочий файл не продолжает HEAD "
            f"(строка {lineno}): '{line}' — историческую строку нельзя удалять, "
            f"править или переставлять"
        )


def _repo_relative_name(path: Path, *, git: str) -> str | None:
    """Путь файла от корня репозитория или ``None``, если git тут ни при чём.

    Считается через ``rev-parse --show-prefix``, а не ``ls-files``: последний
    отказывает для файла, **удалённого** в рабочем дереве, — то есть ровно в
    том случае, который и надо поймать.
    """
    prefix = _git_text(git, str(path.parent), ["rev-parse", "--show-prefix"])
    if prefix is None:
        return None
    return f"{prefix.strip()}{path.name}"


def _registry_history(
    path: Path, *, git: str, full_name: str | None = None
) -> list[list[str]] | None:
    """Строки ``git log --full-history --format='%H %P'`` для файла реестра.

    ``None`` — истории нет вовсе: нет git, нет коммитов или файл в них никогда
    не появлялся. `--full-history` обязателен: упрощение прячет сторону мержа,
    чьё разрешение совпало с первым родителем.
    """
    if full_name is None:
        full_name = _repo_relative_name(path, git=git)
    if full_name is None:
        return None
    # Pathspec — с магией `:(top)`: он резолвится от **текущего** каталога
    # (мы вызываем git из каталога корпуса), а `full_name` считается от корня
    # репозитория, и без магии лог оказался бы пустым — то есть проверка
    # молча выключилась бы.
    graph = _git_text(
        git,
        str(path.parent),
        ["log", "--full-history", "--format=%H %P", "--", f":(top){full_name}"],
    )
    if graph is None:
        return None
    rows = [row.split() for row in graph.splitlines() if row.split()]
    return rows or None


def _require_no_registry_history(path: Path, *, git: str) -> None:
    """Отсутствующий файл реестра законен только если его никогда и не было.

    Пустой словарь на месте удалённого реестра означал бы «ни одного id не
    зарегистрировано»: все id снова свободны, и `--register` раздал бы их
    заново. Поэтому «файла нет» сверяется с историей, а не принимается на веру.
    """
    rows = _registry_history(path, git=git)
    if rows is None:
        return
    raise CorpusError(
        f"{path}: реестр отсутствует в рабочем дереве, но есть в истории "
        f"({rows[0][0][:12]}) — восстановите файл"
    )


def _require_history_monotonic(
    path: Path,
    version: Callable[[str], list[tuple[int, str]] | None],
    *,
    git: str,
    full_name: str | None = None,
) -> None:
    """Каждая версия файла в истории обязана продолжать каждого своего родителя.

    Обход — по парам «коммит и его родители» (`git log --format='%H %P'`), а не
    по снимкам против рабочего файла: только так ловится удаление строки с
    последующим возвратом. `--full-history` обязателен — упрощение прячет
    сторону мержа, чьё разрешение совпало с первым родителем.

    Родитель, в котором файла нет (коммит его добавил), пропускается: это не
    нарушение, а отсутствие версии. Обратное — нарушение: файл есть у родителя
    и отсутствует у потомка значит реестр удалён целиком, а это обнуление
    id-пространства, а не «версии нет».
    """
    rows = _registry_history(path, git=git, full_name=full_name)
    if rows is None:
        return
    for revisions in rows:
        child, parents = revisions[0], revisions[1:]
        child_lines = version(child)
        for parent in parents:
            parent_lines = version(parent)
            if parent_lines is None:
                continue
            if child_lines is None:
                raise CorpusError(
                    f"{path}: реестр удалён в коммите {child[:12]} "
                    f"(родитель {parent[:12]}) — файл реестра нельзя удалять"
                )
            divergence = _first_divergence(parent_lines, child_lines)
            if divergence is None:
                continue
            lineno, line = divergence
            raise CorpusError(
                f"{path}: реестр не append-only в истории: коммит {child[:12]} "
                f"не продолжает родителя {parent[:12]} (строка {lineno}): '{line}' — "
                f"историческую строку нельзя удалять, править или переставлять"
            )


def _first_divergence(
    expected: Sequence[tuple[int, str]], actual: Sequence[tuple[int, str]]
) -> tuple[int, str] | None:
    """Первая строка `expected`, не найденная в `actual` **по порядку**.

    «Продолжает родителя» значит: все строки родителя лежат в потомке
    упорядоченной **подпоследовательностью**, каждая дословно. Сверка по
    абсолютному индексу (как было) отвергала любой мерж без потерь: у
    объединения ``[A, B, C]`` родитель ``[A, C]`` не совпадает по индексу 1,
    хотя ни одна его строка не потеряна. Реестр — append-only множество
    записей, а не файл с закреплёнными номерами строк.

    Что по-прежнему нарушение: удаление строки, правка строки и
    **перестановка** строк родителя. Порядок несёт смысл (файл last-wins:
    последняя строка про id решает его состояние), поэтому разрешить
    перестановку значило бы разрешить менять состояние id, не добавив ни одной
    строки.

    Скан жадный слева направо. Для дословных строк этого достаточно: если
    подпоследовательность существует, жадное сопоставление её находит — самое
    раннее вхождение каждой строки оставляет для остатка наибольший хвост.

    **Что подпоследовательность не проверяет.** Мерж, вставивший строки одной
    стороны между строками другой, законен по этому правилу, но файл
    last-wins: чередование решает, какая строка про id окажется последней, то
    есть какое состояние id считается текущим. Если разрешение мержа это
    изменило, оператор обязан перепрогнать
    ``review-eval corpus validate --register`` и посмотреть на результат:
    валидация назовёт расхождение сама (`check_registry` сверяет последнюю
    строку), но выбор строки — решение человека, разрешавшего мерж, и
    угадывать его тут нечем.
    """
    cursor = 0
    for lineno, line in expected:
        while cursor < len(actual) and actual[cursor][1] != line:
            cursor += 1
        if cursor == len(actual):
            return lineno, line
        cursor += 1
    return None


def _data_lines(lines: Sequence[str]) -> list[tuple[int, str]]:
    """Содержательные строки реестра с их номерами (без пустых и комментариев)."""
    return [
        (lineno, line.strip())
        for lineno, line in enumerate(lines, start=1)
        if line.strip() and not line.strip().startswith("#")
    ]


def _git_text(git: str, directory: str, args: list[str]) -> str | None:
    """stdout удачного вызова git или ``None`` (ненулевой код, нет бинаря)."""
    try:
        result = subprocess.run(  # noqa: S603 — argv фиксирован, без shell
            [git, "-C", directory, *args], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _parse_registry_line(stripped: str, path: Path, lineno: int) -> tuple[str, _Registered]:
    """Разобрать одну строку реестра в `(id, состояние)`; иначе — `CorpusError`.

    Синтаксис проверяется у **каждой** строки, а не только у тех, чей id есть в
    корпусе: иначе мусор жил бы в файле, пока кто-нибудь не завёл такой id, — и
    тогда падало бы далеко от причины. Реестр либо целиком годен, либо нет.
    """
    parts = stripped.split()
    if not parts:  # pragma: no cover — пустые строки отфильтрованы вызывающим
        raise CorpusError(f"{path}:{lineno}: пустая строка реестра (строка {lineno})")
    entry_id = parts[0]
    if not _DEFECT_ID_RE.fullmatch(entry_id) and not _NON_DEFECT_ID_RE.fullmatch(entry_id):
        raise CorpusError(
            f"{path}: id '{entry_id}' не по форме 'D-<repo>-<pr>-<n>'/'NF-…' (строка {lineno})"
        )
    if len(parts) == 2 and parts[1] == DELETED_MARKER:
        return entry_id, _Registered(content=None, identity=None, deleted=True)
    if len(parts) < 2:
        raise CorpusError(f"{path}: строка реестра без дайджеста (строка {lineno}): '{stripped}'")
    digests = parts[1:3] if len(parts) >= 3 else parts[1:2]
    bad = [value for value in digests if not _REGISTRY_DIGEST_RE.fullmatch(value)]
    if bad or len(parts) > 4 or (len(parts) == 4 and parts[3] != REIDENTIFIED_MARKER):
        raise CorpusError(
            f"{path}: строка реестра не по формату (строка {lineno}): '{stripped}'; "
            f"ожидается '<id> <sha256> [<sha256> [{REIDENTIFIED_MARKER}]]' "
            f"или '<id> {DELETED_MARKER}'"
        )
    if len(parts) == 2:
        return entry_id, _Registered(content=parts[1], identity=None)
    return entry_id, _Registered(content=parts[1], identity=parts[2], reidentified=len(parts) == 4)
