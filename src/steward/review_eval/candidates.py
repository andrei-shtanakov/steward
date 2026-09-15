"""Кандидаты корпуса из истории ревью: чтение ревью ai-prosto на PR и черновик
кейса `review-eval-case/v1` (дизайн §5, D1).

Черновик — **прокси, а не ground truth**: историческая находка не доказана
верной тем, что её написал ревьюер. Поэтому всё, что здесь производится,
выходит с `annotation.status: draft` и `source: history-proxy`, а `draft` по
построению не входит ни в одну официальную метрику (`corpus.is_gold`).
Разметчик правит текст, severity и `match`, ставит `blocking_complete` и
переводит кейс в `adjudicated`.

`candidate_status` (`likely_tp` / `likely_fp` / `unknown`) — подсказка
разметчику (D1), и намеренно слабая: единственный доступный офлайн-сигнал —
были ли у PR коммиты **после** последнего ревью. Он кладётся в `notes`
текстом, а не отдельным полем: схема кейса закрыта (`corpus._TOP_LEVEL_KEYS`),
и новый ключ сломал бы её ради подсказки.

Сеть: только `gh api` на чтение (`fetch_*`). Разбор (`parse_findings`) и
сборка черновика (`draft_case`) — чистые функции, тестируются без сети.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import yaml

from steward.review_eval.corpus import FILE_MISSING_KIND, CorpusError
from steward.review_eval.corpus import repo_slug as corpus_repo_slug

__all__ = [
    "AI_PROSTO",
    "SKIPPED_SEVERITY",
    "CandidatesError",
    "Evidence",
    "ParsedFinding",
    "commits_after",
    "draft_case",
    "fetch_commits",
    "fetch_pr",
    "fetch_reviews",
    "keywords_from_title",
    "parse_findings",
    "render_case",
    "review_head",
]

#: Учётка, публикующая терминальные ревью (`devtools/review-pr.sh`).
AI_PROSTO = "ai-prosto"

_GH_TIMEOUT_S = 60.0

#: Маркер тела ревью: `head=` обязателен, `fp=` появился позже и опционален.
_MARKER_RE = re.compile(
    r"<!--\s*codex-terminal-review\s+head=(?P<head>[0-9a-f]{40})"
    r"(?:\s+fp=(?P<fp>[0-9a-f]{64}))?\s*-->"
)

#: Заголовок находки из `apply-threshold.sh` (формат markdown):
#: ``### [severity] title — `file:line` ``. Заголовок `.*` **жадный**: сам
#: заголовок находки часто содержит бэктики и тире, и разделителем считается
#: последнее « — `путь:строка`» строки, а не первое. Пустой title допустим:
#: схема требует от него только тип string, и рендер печатает
#: ``### [minor]  — `a.py:1``` — это пустое поле, а не порча формата
#: (ключевые слова тогда берутся из scenario/expected_result).
_HEADER_RE = re.compile(
    r"^### \[(?P<severity>[^\]]+)\] (?P<title>.*) — `(?P<file>[^`]+):(?P<line>\d+)`\s*$"
)

#: Префикс заголовка находки: `###` и настоящая severity в квадратных скобках.
#: Строка, попавшая под него, **обязана** разобраться `_HEADER_RE` целиком —
#: иначе блок находки потерялся бы молча (`parse_findings`).
_FINDING_HEADING_RE = re.compile(r"^### \[(?:blocker|major|minor|nit)\]")

_KIND_RE = re.compile(r"^- Тип: `(?P<kind>[^`]+)`")
#: Записи строки ``- Evidence:`` — по адресам, а не по разделителю: `reason`
#: модели может содержать ту же пару ``"; "``, которой рендер разделяет записи.
#: Поэтому конец `reason` определяется **следующим адресом** (нулевой
#: look-ahead) или концом строки.
_EVIDENCE_ITEM_RE = re.compile(
    r"`(?P<file>[^`]+):(?P<line>\d+)` — (?P<reason>.*?)(?=; `[^`]+:\d+` — |$)",
    re.S,
)

#: Поля блока находки: префикс строки → имя поля `ParsedFinding`.
_FIELD_PREFIXES: tuple[tuple[str, str], ...] = (
    ("- Сценарий: ", "scenario"),
    ("- Наблюдаемое: ", "observed_result"),
    ("- Ожидаемое: ", "expected_result"),
    ("- Evidence: ", "evidence"),
    ("- confidence: ", "confidence"),
)

#: «Evidence нет» в рендере порога.
_EVIDENCE_NONE = "—"

#: Строка, которую кит печатает вместо секции находок, когда их нуль. Она
#: идёт **после** `note` модели, а границы перед секцией находок в рендере нет,
#: поэтому её соседство с заголовком находки означает, что заголовок написала
#: модель (`parse_findings`).
_EMPTY_VERDICT_MARKER = "Находок нет."

#: Шапка формата кита — **подпись доверенного рендера**. Её печатает
#: `apply-threshold.sh` (в markdown с префиксом ``## ``, в plain без него), и
#: она отделяет тело, собранное китом, от произвольного текста ревью.
_KIT_HEADER = "Ревью Codex — независимый чек"

#: Severity, которую кит выдаёт, а корпус не знает: находка не становится
#: дефектом черновика, но и не теряется — её след уходит в `notes` (§5).
SKIPPED_SEVERITY = "nit"

#: Окно строк вокруг `line_hint` в черновом `match` (правится разметчиком).
DEFAULT_LINE_WINDOW = 40

#: Сколько самых длинных слов заголовка уходит в `match.keywords_any`.
_MAX_KEYWORDS = 5

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


class CandidatesError(Exception):
    """`gh` недоступен/упал, ответ не разбирается, или тело ревью не по формату кита."""


@dataclass(frozen=True)
class Evidence:
    """Одна evidence-запись находки: указатель плюс причина."""

    file: str
    line: int
    reason: str

    @property
    def ref(self) -> str:
        """``file:line`` — форма, в которой evidence живёт в кейсе корпуса."""
        return f"{self.file}:{self.line}"


@dataclass(frozen=True)
class ParsedFinding:
    """Находка, разобранная из тела ревью ai-prosto (формат `apply-threshold.sh`)."""

    severity: str
    title: str
    file: str
    line: int
    scenario: str
    observed_result: str
    expected_result: str
    evidence: tuple[Evidence, ...]
    confidence: str | None
    kind: str | None


# ---------------------------------------------------------------------------
# Сеть: чтение через `gh` (единственные сетевые функции модуля)
# ---------------------------------------------------------------------------


def fetch_reviews(repo: str, pr: int, *, gh: str = "gh") -> list[dict[str, Any]]:
    """Ревью PR: ``gh api repos/<repo>/pulls/<pr>/reviews``.

    Возвращает **все** ревью как есть, без фильтра по автору: кто из них
    ai-prosto, решает `draft_case` — так один сетевой ответ можно разобрать
    по-разному, не перезапрашивая.
    """
    payload = _gh_json(gh, f"repos/{repo}/pulls/{pr}/reviews", what="reviews")
    if not isinstance(payload, list):
        raise CandidatesError(f"{repo}#{pr}: reviews response is not a JSON array")
    return [item for item in payload if isinstance(item, dict)]


def fetch_pr(repo: str, pr: int, *, gh: str = "gh") -> dict[str, Any]:
    """Метаданные PR: ``gh api repos/<repo>/pulls/<pr>`` (`base.sha`, `head.sha`,
    `merge_commit_sha`)."""
    payload = _gh_json(gh, f"repos/{repo}/pulls/{pr}", what="pr")
    if not isinstance(payload, dict):
        raise CandidatesError(f"{repo}#{pr}: pr response is not a JSON object")
    return payload


def fetch_commits(repo: str, pr: int, *, gh: str = "gh") -> list[dict[str, Any]]:
    """Коммиты PR: ``gh api repos/<repo>/pulls/<pr>/commits`` (для `commits_after`)."""
    payload = _gh_json(gh, f"repos/{repo}/pulls/{pr}/commits", what="commits")
    if not isinstance(payload, list):
        raise CandidatesError(f"{repo}#{pr}: commits response is not a JSON array")
    return [item for item in payload if isinstance(item, dict)]


def commits_after(commits: Sequence[Mapping[str, Any]], since: str | None) -> list[str]:
    """Sha коммитов PR, закоммиченных строго позже `since` (ISO-8601 из ревью).

    Сравнение лексикографическое по ISO-8601 в UTC — так их и отдаёт GitHub
    (``2026-09-14T08:35:07Z``); парсить дату ради сравнения нечего.
    Пустой/отсутствующий `since` — «отсчитывать не от чего», пустой список.
    """
    if not since:
        return []
    result: list[str] = []
    for commit in commits:
        sha = commit.get("sha")
        date = _commit_date(commit)
        if isinstance(sha, str) and date is not None and date > since:
            result.append(sha)
    return result


# ---------------------------------------------------------------------------
# Разбор тела ревью
# ---------------------------------------------------------------------------


def parse_findings(body: str) -> list[ParsedFinding]:
    """Разобрать находки из тела ревью ai-prosto (рендер `apply-threshold.sh`).

    Началом находки считается строка полной формы рендера
    ``### [severity] title — `file:line` `` (`_HEADER_RE`): адрес в бэктиках
    кит дописывает всегда. Строка `###` без `[severity]` — обычный текст (так
    ревьюер оформляет сводку), и она пропускается.

    А вот строка **с настоящей severity**, не разобравшаяся целиком, —
    `CandidatesError`: это заголовок находки, который мы не поняли. Молчание
    тут дороже отказа: схемно годная находка с ``file: ""`` рендерится как
    ``### [major] t — `:10``` и под регулярку не подходит, так что блок просто
    исчезал — а черновик объявлял PR чистым при живой блокирующей находке.

    Каждая находка — блок от своего заголовка до следующего **заголовка
    находки**: проза между ними достаётся предыдущей находке как хвост
    (её поля разбираются по префиксам строк, лишнее игнорируется).

    Тело с маркером пустого вердикта (``Находок нет.``) **и** хотя бы одним
    заголовком находки — `CandidatesError`: два признака противоречат друг
    другу, и заголовок почти наверняка написала модель в `note`.

    **Пробельный путь** в заголовке или в записи evidence — тоже
    `CandidatesError`. Регулярки берут путь как «что угодно между бэктиками»,
    поэтому такая находка разбиралась, а падал потом `load_case` на
    `file must not be blank` — далеко от причины и уже после записи черновика.

    **Принятый предел.** Границы перед секцией находок кит не рендерит, а
    `note` модели печатается раньше находок и не экранируется. Поэтому note,
    оформленный как заголовок находки, в непустом вердикте от настоящей находки
    **неотличим**: черновик получит лишнюю находку, и снять её — работа
    разметчика. Закрывается это на стороне кита (маркер границы в рендере
    `apply-threshold.sh`), не здесь: пакет читает то, что кит напечатал, и
    придумать границу за него не может.
    """
    lines = body.splitlines()
    headers: list[tuple[int, re.Match[str]]] = []
    for index, line in enumerate(lines):
        match = _HEADER_RE.match(line)
        if match is not None:
            if _is_blank(match.group("file")):
                raise CandidatesError(
                    f"пробельный путь в заголовке находки: {line!r} — черновик не создаётся"
                )
            headers.append((index, match))
            continue
        if _FINDING_HEADING_RE.match(line):
            raise CandidatesError(
                f"не разобран заголовок находки: {line!r} — черновик не создаётся"
            )
    if headers and any(line.strip() == _EMPTY_VERDICT_MARKER for line in lines):
        raise CandidatesError(
            f"тело ревью неоднозначно: {_EMPTY_VERDICT_MARKER!r} рядом с заголовком "
            f"находки — вероятно, note имитирует находку"
        )
    findings: list[ParsedFinding] = []
    for position, (start, header) in enumerate(headers):
        end = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
        findings.append(_parse_block(header, lines[start + 1 : end]))
    return findings


def review_head(body: str) -> str | None:
    """`head=` из маркера тела ревью; ``None``, если маркера нет.

    Собираются **все** маркеры, а не первый: маркер дописывает кит, но тело
    ревью содержит и `note` модели — он рендерится раньше и не экранируется,
    так что «первый попавшийся» мог оказаться выбранным моделью. Повтор одного
    и того же SHA безобиден (голова одна); два разных SHA — неоднозначное
    тело, и `CandidatesError`: черновик по такому телу не создаётся вовсе.

    Принятый предел: если маркер в теле **один** и его написала модель, он и
    будет взят — по одному телу это неразличимо. Опорой служит не он, а
    последующие шаги: `head_sha` кейса материализуется (`corpus materialize`) и
    проходит разметку, где дерево смотрят глазами.
    """
    heads = {match.group("head") for match in _MARKER_RE.finditer(body)}
    if not heads:
        return None
    if len(heads) > 1:
        raise CandidatesError(
            "несколько маркеров head с разными SHA в теле ревью — "
            f"тело неоднозначно, кейс не генерируется: {sorted(heads)}"
        )
    return heads.pop()


# ---------------------------------------------------------------------------
# Черновик кейса
# ---------------------------------------------------------------------------


def draft_case(
    repo: str,
    pr: int,
    pr_meta: Mapping[str, Any],
    reviews: Sequence[Mapping[str, Any]],
    *,
    commits_after: Sequence[str],
) -> dict[str, Any]:
    """Черновик кейса `review-eval-case/v1` из PR и его ревью (§5).

    Находки берутся из **последнего** ревью ai-prosto: у более раннего ревью
    другой `head`, и его номера строк указывали бы не в то дерево, которое
    кейс пинует (`head_sha`). Число находок прежних заходов уходит в `notes`
    как след для разметчика.

    `head_sha` — из маркера последнего ревью (он называет ровно то дерево,
    которое ревьюер видел), с фолбэком на `head.sha` PR; `head.sha` требуется
    только при отсутствии маркера, `base.sha` — всегда. `class` — `defective`,
    если среди находок есть `blocker`/`major`, иначе `clean`; minor-находки всё
    равно попадают в `defects` со своей severity: предсказание на gold-minor —
    это FP, и без записи в кейсе оно выглядело бы неразмеченным.

    Тело обязано **предъявить формат кита** (`_require_kit_format`): подпись
    рендера плюс вердикт (находка или ``Находок нет.``). Без этого непустое
    ревью, в котором парсер не понял ничего, давало `class: clean` с пустым
    `defects[]` — PR объявлялся чистым по факту неразбора.

    Находки `severity: nit` кит выдавать вправе (его схема их допускает), а
    корпус — нет (`blocker|major|minor`). Такая находка **пропускается**, и её
    число с заголовками уходит в `notes`: падать всем черновиком из-за
    косметического пункта — терять весь PR, а пропускать молча — терять
    находку. Незнакомая severity (кит расширил набор) по-прежнему
    `CandidatesError`: это не пропуск, это решение разметчика.
    """
    slug = _repo_slug(repo)
    ai_reviews = _ai_prosto_reviews(reviews)
    if not ai_reviews:
        raise CandidatesError(f"{repo}#{pr}: нет ревью от {AI_PROSTO} — черновик не из чего делать")
    last = ai_reviews[-1]
    body = _body(last)

    parsed = parse_findings(body)
    _require_kit_format(body, parsed)
    skipped = [f for f in parsed if f.severity == SKIPPED_SEVERITY]
    findings = [f for f in parsed if f.severity != SKIPPED_SEVERITY]
    base_sha = _require_sha(_dig(pr_meta, "base", "sha"), f"{repo}#{pr}: base.sha")
    # Маркер спрашивается раньше fallback: он называет ровно то дерево, которое
    # видел ревьюер, и `head.sha` PR нужен только там, где маркера нет. Иначе
    # PR без `head.sha` в ответе API отказывал бы даже при готовом ответе в теле.
    marker_head = review_head(body)
    head_sha = (
        marker_head
        if marker_head is not None
        else _require_sha(_dig(pr_meta, "head", "sha"), f"{repo}#{pr}: head.sha")
    )

    defects = [
        _draft_defect(slug, pr, index, finding) for index, finding in enumerate(findings, start=1)
    ]
    blocking = [f for f in findings if f.severity in ("blocker", "major")]

    return {
        "schema": "review-eval-case/v1",
        "case_id": f"{slug}-{pr}",
        "repo": repo,
        "pr": pr,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "class": "defective" if blocking else "clean",
        "expected_outcome": "verdict",
        "annotation": {
            "status": "draft",
            "blocking_complete": False,
            "source": "history-proxy",
            "adjudicated_by": None,
            "adjudicated_at": None,
        },
        "defects": defects,
        "non_defects": [],
        "notes": _draft_notes(
            repo,
            pr,
            pr_meta,
            ai_reviews,
            findings,
            skipped=skipped,
            commits_after=commits_after,
            head_from_marker=marker_head is not None,
        ),
    }


class _CaseDumper(yaml.SafeDumper):
    """Дампер черновика: многострочный текст — литеральным блоком (``|``).

    Умолчание PyYAML сворачивает такую строку в двойные кавычки с ``\\n``, и
    `notes`/`scenario` превращаются в одну нечитаемую строку — а править их
    руками именно разметчику. Литеральный блок недопустим для строки с
    хвостовыми пробелами в строках, поэтому они снимаются.
    """


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    if "\n" in value:
        cleaned = "\n".join(line.rstrip() for line in value.splitlines())
        return dumper.represent_scalar("tag:yaml.org,2002:str", cleaned, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_CaseDumper.add_representer(str, _represent_str)


def render_case(case: Mapping[str, Any]) -> str:
    """YAML черновика: порядок ключей как в схеме (§5), юникод не экранируется."""
    return yaml.dump(
        dict(case),
        Dumper=_CaseDumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


# ---------------------------------------------------------------------------
# внутреннее
# ---------------------------------------------------------------------------


def _require_kit_format(body: str, parsed: Sequence[ParsedFinding]) -> None:
    """Тело обязано предъявить формат кита, иначе черновик не создаётся.

    Два независимых требования, и оба нужны:

    1. **подпись рендера** — строка `Ревью Codex — независимый чек` (с
       префиксом ``## `` или без: кит печатает markdown и plain). На ней
       держится право считать `### [severity] …` заголовком находки: всё, что
       подписи не несёт, могло быть написано кем угодно, включая модель в
       `note`;
    2. **вердикт** — хотя бы одна разобранная находка **или** строка
       `Находок нет.`. Иначе непустое ревью, в котором парсер не понял ничего,
       давало `class: clean` с пустым `defects[]`: PR объявлялся чистым по
       факту неразбора. Такой кейс не измерение, а испорченный gold — в
       метриках он даёт recall по нулю дефектов и хвалит ревьюера за молчание.

    Обратная сторона: формат кита сменится — генератор встанет. Это и есть
    замысел. Молчаливый `clean` дороже: он выглядит как данные.
    """
    lines = body.splitlines()
    has_header = any(line.strip() in (_KIT_HEADER, f"## {_KIT_HEADER}") for line in lines)
    has_verdict = bool(parsed) or any(line.strip() == _EMPTY_VERDICT_MARKER for line in lines)
    if not has_header or not has_verdict:
        raise CandidatesError(
            f"тело ревью не распознано: ни заголовка находки, ни "
            f"{_EMPTY_VERDICT_MARKER!r} — черновик не создаётся "
            f"(старый/усечённый формат?)"
        )


def _draft_defect(slug: str, pr: int, index: int, finding: ParsedFinding) -> dict[str, Any]:
    """Одна запись `defects[]` черновика: id, severity, `kind` и `match`.

    `kind: file-missing` переносится из находки вместе с `line_hint: 0`: матчер
    сопоставляет предсказание и gold только при совпадении `kind`, поэтому
    черновик без этого поля превращал бы верную находку «нужного файла нет» в
    заведомый FP. У обычной находки поле опускается — умолчание схемы
    (`defect`), и в YAML лишней строки не появляется.
    """
    evidence = [item.ref for item in finding.evidence] or [f"{finding.file}:{finding.line}"]
    candidate = "" if finding.confidence is None else f" (confidence: {finding.confidence})"
    file_missing = finding.kind == FILE_MISSING_KIND
    defect: dict[str, Any] = {
        "id": f"D-{slug}-{pr}-{index}",
        "severity": _draft_severity(finding.severity),
        "file": finding.file,
        "line_hint": 0 if file_missing else finding.line,
        "scenario": f"{finding.title}{candidate}. {finding.scenario}".strip(),
        "evidence": evidence,
        "match": {
            "files": [finding.file],
            "line_window": DEFAULT_LINE_WINDOW,
            "keywords_any": keywords_from_title(
                finding.title,
                fallback_text=f"{finding.scenario} {finding.expected_result}",
                fallback=finding.file,
            ),
        },
    }
    if file_missing:
        defect["kind"] = FILE_MISSING_KIND
    return defect


def _draft_severity(severity: str) -> str:
    """Severity кейса: значение кита, если оно из закрытого набора корпуса.

    Незнакомое значение (кит когда-нибудь расширит набор) не выдаётся за
    `minor`: `CandidatesError` заставит разметчика решить, а не унаследовать
    молча заниженный класс.
    """
    if severity not in ("blocker", "major", "minor"):
        raise CandidatesError(
            f"severity '{severity}' вне набора корпуса (blocker|major|minor) — разметьте вручную"
        )
    return severity


def keywords_from_title(title: str, *, fallback_text: str, fallback: str) -> list[str]:
    """До пяти самых длинных слов заголовка: casefold, только буквы (§5).

    Слова сортируются по убыванию длины (при равной — по первому появлению),
    результат хранит этот порядок: `match.keywords_any` — «любое из», порядок
    внутри не влияет на матчинг, но делает диф черновиков стабильным.

    Пустой результат недопустим (корпус требует непустой `keywords_any`),
    поэтому источники перебираются по очереди:

    1. `title` — заголовок находки;
    2. `fallback_text` — ``scenario`` и ``expected_result`` находки;
    3. `fallback` — слова пути файла, затем сам путь целиком.

    Шаг 2 добавлен не для полноты: матчер ищет ключи в стоге
    ``title scenario expected_result`` **предсказания**, и пути файла в этом
    стоге нет вовсе. Черновик с ключами из пути не совпадал даже с той
    находкой, из которой сделан, — верная находка становилась FP, а дефект
    уходил в пропуски. Шаг 3 остался последним рубежом: если ни заголовок, ни
    текст находки букв не содержат, ключи **не матчабельны** и такому
    черновику нужны ключевые слова, написанные разметчиком руками.
    """
    for source in (title, fallback_text, fallback):
        words = list(dict.fromkeys(word.casefold() for word in _WORD_RE.findall(source)))
        if words:
            ranked = sorted(
                ((-len(word), index, word) for index, word in enumerate(words)),
            )
            return [word for _, _, word in ranked[:_MAX_KEYWORDS]]
    return [fallback]


def _draft_notes(
    repo: str,
    pr: int,
    pr_meta: Mapping[str, Any],
    ai_reviews: Sequence[Mapping[str, Any]],
    findings: Sequence[ParsedFinding],
    *,
    skipped: Sequence[ParsedFinding] = (),
    commits_after: Sequence[str],
    head_from_marker: bool,
) -> str:
    """Свободный текст для разметчика: провенанс, подсказки, чего не хватает."""
    last = ai_reviews[-1]
    status = _candidate_status(commits_after)
    lines = [
        f"Черновик из истории ревью (source: history-proxy): {repo}#{pr}, "
        f"ревью {AI_PROSTO} id={last.get('id')} от {last.get('submitted_at')}.",
        f"Ревью {AI_PROSTO} на PR: {len(ai_reviews)}; находок в последнем: {len(findings)}.",
        f"head_sha взят {'из маркера ревью' if head_from_marker else 'из head.sha PR (маркера нет)'}"
        f"; merge_commit_sha: {pr_meta.get('merge_commit_sha')}.",
        f"Коммитов PR после последнего ревью: {len(commits_after)}"
        + (f" ({', '.join(sha[:12] for sha in commits_after)})" if commits_after else "")
        + ".",
    ]
    if findings:
        lines.append("candidate_status (подсказка D1, не ground truth):")
        lines.extend(
            f"  - D-{_repo_slug(repo)}-{pr}-{index}: {status} "
            f"[{finding.severity}] {finding.file}:{finding.line}"
            for index, finding in enumerate(findings, start=1)
        )
    if skipped:
        lines.append(
            f"пропущено {len(skipped)} находок severity {SKIPPED_SEVERITY} "
            f"(вне набора корпуса): " + "; ".join(f"{f.title} — {f.file}:{f.line}" for f in skipped)
        )
    lines.append(
        "Разметчику: проверить severity и evidence каждой находки, уточнить "
        "match.keywords_any/line_window, выставить annotation.blocking_complete "
        "и перевести status в adjudicated."
    )
    return "\n".join(lines)


def _candidate_status(commits_after: Sequence[str]) -> str:
    """Подсказка D1 по единственному офлайн-сигналу — коммитам после ревью.

    Коммиты после ревью — слабый довод «находку признали и правили»
    (`likely_tp`); их отсутствие не довод против (ревью пришло на готовый PR),
    поэтому `unknown`, а не `likely_fp`. `likely_fp` этот сигнал выдать не
    может вовсе — он остаётся для ручной разметки, и это честнее, чем
    красить находку ложной по отсутствию коммита.
    """
    return "likely_tp" if commits_after else "unknown"


def _parse_block(header: re.Match[str], body: Sequence[str]) -> ParsedFinding:
    """Разобрать один блок находки: разобранный заголовок плюс строки-поля.

    Заголовок приходит уже сопоставленным (`parse_findings` только по нему и
    находит начало блока): двух разных представлений «что такое заголовок» в
    модуле быть не должно.
    """
    fields: dict[str, str] = {}
    kind: str | None = None
    for line in body:
        kind_match = _KIND_RE.match(line)
        if kind_match is not None:
            kind = kind_match.group("kind")
            continue
        for prefix, name in _FIELD_PREFIXES:
            if line.startswith(prefix):
                fields[name] = line[len(prefix) :].strip()
                break

    confidence = fields.get("confidence")
    if confidence is not None:
        confidence = confidence.split("→")[0].strip() or None

    return ParsedFinding(
        severity=header.group("severity"),
        title=header.group("title").strip(),
        file=header.group("file"),
        line=int(header.group("line")),
        scenario=fields.get("scenario", ""),
        observed_result=fields.get("observed_result", ""),
        expected_result=fields.get("expected_result", ""),
        evidence=_parse_evidence(fields.get("evidence", "")),
        confidence=confidence,
        kind=kind,
    )


def _parse_evidence(text: str) -> tuple[Evidence, ...]:
    """Строка ``- Evidence:`` → записи; ``—`` значит «evidence нет».

    Разбор идёт **по адресам** (`_EVIDENCE_ITEM_RE`), а не дроблением по
    ``"; "``: `reason` модели может содержать ту же пару, и дробление рассыпало
    запись — в черновик вместо evidence уходил адрес заголовка, то есть
    терялось ровно то, чем находка доказывалась.

    Строка, разобранная **не целиком** (мусор перед первым адресом, между
    записями или после последней), — `CandidatesError`: то же правило, что для
    заголовка находки (раунд 13). Молчаливая потеря доказательства дороже
    отказа: черновик выглядел бы полным.
    """
    stripped = text.strip()
    if not stripped or stripped == _EVIDENCE_NONE:
        return ()
    matches = list(_EVIDENCE_ITEM_RE.finditer(stripped))
    if not _covers_everything(matches, stripped):
        raise CandidatesError(f"не разобрана строка evidence: {stripped!r} — черновик не создаётся")
    for match in matches:
        if _is_blank(match.group("file")):
            raise CandidatesError(
                f"пробельный путь в evidence: {stripped!r} — черновик не создаётся"
            )
    return tuple(
        Evidence(
            file=match.group("file"),
            line=int(match.group("line")),
            reason=match.group("reason").strip(),
        )
        for match in matches
    )


def _is_blank(value: str) -> bool:
    """Строка без непробельных символов.

    Путь из пробелов проходит `[^`]+` в регулярках, поэтому черновик получался,
    а `load_case` падал на `file must not be blank` — далеко от причины и уже
    после записи файла. Отказ ставится туда, где путь читается.
    """
    return not value.strip()


def _covers_everything(matches: Sequence[re.Match[str]], text: str) -> bool:
    """Покрывают ли совпадения всю строку, с ``"; "`` в стыках и без хвостов."""
    if not matches:
        return False
    if matches[0].start() != 0 or matches[-1].end() != len(text):
        return False
    return all(
        text[previous.end() : current.start()] == "; "
        for previous, current in zip(matches, matches[1:], strict=False)
    )


def _ai_prosto_reviews(reviews: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Ревью ai-prosto в порядке публикации (по `submitted_at`, затем `id`)."""
    selected = [
        review
        for review in reviews
        if _dig(review, "user", "login") == AI_PROSTO and _body(review).strip()
    ]
    return sorted(selected, key=lambda r: (str(r.get("submitted_at") or ""), _int_or_zero(r)))


def _body(review: Mapping[str, Any]) -> str:
    body = review.get("body")
    return body if isinstance(body, str) else ""


def _int_or_zero(review: Mapping[str, Any]) -> int:
    value = review.get("id")
    return value if isinstance(value, int) else 0


def _commit_date(commit: Mapping[str, Any]) -> str | None:
    """Дата коммита (`commit.committer.date`, фолбэк `commit.author.date`)."""
    for actor in ("committer", "author"):
        value = _dig(commit, "commit", actor, "date")
        if isinstance(value, str) and value:
            return value
    return None


def _dig(payload: Mapping[str, Any] | None, *keys: str) -> Any:
    """Вложенное значение по цепочке ключей или ``None`` на первом промахе."""
    current: Any = payload
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _require_sha(value: Any, what: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise CandidatesError(f"{what} не 40-hex sha: {value!r}")
    return value


def _repo_slug(repo: str) -> str:
    """Слаг репо — префикс `case_id` и id дефектов; правило одно с корпусом.

    Считает `corpus.repo_slug` (нижний регистр, всё вне `[a-z0-9-]` → ``-``):
    своя версия выдавала имя как есть, и для репо вида ``org/My_Repo`` черновик
    получал `case_id`/id, которые `load_case` тут же отвергал — то есть
    `corpus candidates` производил заведомо невалидный кейс.
    """
    try:
        return corpus_repo_slug(repo)
    except CorpusError as error:
        raise CandidatesError(str(error)) from error


def _gh_json(gh: str, endpoint: str, *, what: str) -> object:
    """``gh api --paginate <endpoint>`` → разобранный JSON.

    `--paginate` со `--slurp`: у PR бывает больше одной страницы ревью или
    коммитов, и вторая страница молча потерялась бы (`--slurp` склеивает
    страницы в один массив вместо конкатенации JSON-документов).
    """
    args = [gh, "api", "--paginate", "--slurp", endpoint]
    try:
        completed = subprocess.run(  # noqa: S603 — argv фиксирован, endpoint без shell
            args, capture_output=True, text=True, timeout=_GH_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CandidatesError(f"{what}: не удалось запустить '{gh} api {endpoint}': {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        raise CandidatesError(f"{what}: gh api {endpoint} завершился с ошибкой: {detail}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CandidatesError(f"{what}: ответ gh api {endpoint} не JSON: {exc}") from exc
    return _unslurp(payload)


def _unslurp(payload: object) -> object:
    """Снять обёртку `--slurp`: список страниц → одна страница либо их склейка.

    `gh api --paginate --slurp` всегда возвращает массив страниц. Одна
    страница-объект (ответ `pulls/<pr>`) — это `[{…}]`, страницы-массивы
    (`reviews`, `commits`) — `[[…], […]]`; склеиваем их в один массив, чтобы
    вызывающий видел ту же форму, что у не-пагинированного `gh api`.
    """
    if not isinstance(payload, list):
        return payload
    if len(payload) == 1 and isinstance(payload[0], dict):
        return payload[0]
    if all(isinstance(page, list) for page in payload):
        return [item for page in payload for item in page]
    return payload
