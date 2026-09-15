"""Тесты `review_eval.candidates`: разбор тел ревью ai-prosto и черновик кейса.

Сети нет: `fetch_*` проверяются через подставной `gh` на `PATH` (скрипт,
печатающий заранее заготовленный JSON), остальное — чистые функции.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
import yaml

from steward.review_eval.candidates import (
    CandidatesError,
    ParsedFinding,
    commits_after,
    draft_case,
    fetch_commits,
    fetch_pr,
    fetch_reviews,
    keywords_from_title,
    parse_findings,
    render_case,
    review_head,
)
from steward.review_eval.corpus import append_registry, load_case
from steward.review_eval.matcher import Prediction, match

HEAD = "15fe2fe48111deed95aa17089adb99c894f32a2f"
BASE = "496e1b2eae15b21c6fb2238a8644eb67a40ccb17"
FP = "e72c3225b5d63a528bcbf101de94e43a7352fab5361b2ed9bf2d2e47eb28fa7f"

BODY = f"""## Codex CLI review — терминальный прогон

- PR: andrei-shtanakov/steward#155, проревьюирован head `{HEAD}`

## Ревью Codex — независимый чек

Сверх дифа читал полный scripts/review/local.sh.

### [major] `PATH="$kit_dir:$PATH"` ставится и для codex-умолчания — `scripts/review/local.sh:644`
- Сценарий: Ревьюер запускается с расширенным PATH.
- Наблюдаемое: подставной `codex` из каталога кита побеждает настоящий.
- Ожидаемое: PATH не расширяется вовсе.
- Evidence: `scripts/review/local.sh:644` — PATH расширяется; \
`scripts/review/checksum.sh:157` — член не запинован
- confidence: high → БЛОКИРУЕТ

### [minor] тест запускает НАСТОЯЩИЙ `claude` — `tests/review/test_harness_claude.py:203`
- Тип: `file-missing` — находка утверждает, что файла нет; проверяется по дереву
- Сценарий: Машина, где `jq` и `claude` в одном каталоге.
- Наблюдаемое: реальный вызов claude CLI.
- Ожидаемое: стенд гарантирует отсутствие claude в PATH.
- Evidence: —
- confidence: medium → не блокирует по severity


_Порог: красным делают только `blocker`/`major` с `confidence: high`._

<!-- codex-terminal-review head={HEAD} fp={FP} -->
"""

#: Шапка формата кита: подпись доверенного рендера (`apply-threshold.sh`).
#: Без неё `draft_case` тело не признаёт, поэтому фикстуры её несут.
KIT_HEADER = "## Ревью Codex — независимый чек"

PR_META = {
    "base": {"sha": BASE},
    "head": {"sha": HEAD},
    "merge_commit_sha": "a2d7e719564d414fbf04684f5bd7802013a4f681",
}

REVIEWS = [
    {
        "id": 5195557580,
        "user": {"login": "ai-prosto"},
        "state": "APPROVED",
        "submitted_at": "2026-09-14T08:35:07Z",
        "body": BODY,
    },
    {
        "id": 5195587879,
        "user": {"login": "copilot-pull-request-reviewer[bot]"},
        "state": "COMMENTED",
        "submitted_at": "2026-09-14T08:38:06Z",
        "body": "### [blocker] copilot говорит — `a.py:1`\n- Сценарий: x\n",
    },
]


# ---------------------------------------------------------------------------
# parse_findings
# ---------------------------------------------------------------------------


def test_parse_findings_reads_every_field_of_the_kit_format() -> None:
    findings = parse_findings(BODY)
    assert [f.severity for f in findings] == ["major", "minor"]

    first = findings[0]
    assert first.title == '`PATH="$kit_dir:$PATH"` ставится и для codex-умолчания'
    assert first.file == "scripts/review/local.sh"
    assert first.line == 644
    assert first.scenario == "Ревьюер запускается с расширенным PATH."
    assert first.observed_result == "подставной `codex` из каталога кита побеждает настоящий."
    assert first.expected_result == "PATH не расширяется вовсе."
    assert first.confidence == "high"
    assert first.kind is None
    assert [(e.file, e.line) for e in first.evidence] == [
        ("scripts/review/local.sh", 644),
        ("scripts/review/checksum.sh", 157),
    ]
    assert first.evidence[0].reason == "PATH расширяется"
    assert first.evidence[0].ref == "scripts/review/local.sh:644"


def test_parse_findings_keeps_kind_and_empty_evidence() -> None:
    second = parse_findings(BODY)[1]
    assert second.kind == "file-missing"
    assert second.evidence == ()
    assert second.confidence == "medium"


def test_parse_findings_on_a_body_without_findings_is_empty() -> None:
    assert parse_findings("## Ревью Codex — независимый чек\n\nНаходок нет.\n") == []


def test_parse_findings_ignores_a_heading_without_a_severity_prefix() -> None:
    """`###` без `[severity]` — обычная проза ревьюера, а не находка."""
    assert parse_findings("### Сводка ревьюера\n\nНаходок нет.\n") == []
    assert parse_findings("### [заметка] сводка\n\nтекст\n") == []


def test_parse_findings_reads_a_real_finding_after_a_prose_heading() -> None:
    """Проза с `###`-заголовком не съедает следующую настоящую находку."""
    body = (
        "### Сводка: что смотрел\n"
        "\n"
        "Читал весь файл, не только диф.\n"
        "\n"
        "### [minor] настоящая находка — `a.py:7`\n"
        "- Сценарий: s\n"
        "- confidence: medium → не блокирует\n"
    )

    findings = parse_findings(body)

    assert len(findings) == 1
    assert (findings[0].severity, findings[0].file, findings[0].line) == ("minor", "a.py", 7)
    assert findings[0].title == "настоящая находка"


@pytest.mark.parametrize(
    "heading",
    [
        "### [major] находка без адреса",
        "### [major] пустой файл — `:10`",
        "### [blocker] адрес без строки — `a.py`",
        "### [nit] адрес не в бэктиках — a.py:10",
    ],
    ids=["no-address", "empty-file", "no-line", "no-backticks"],
)
def test_parse_findings_refuses_an_unparsed_finding_heading(heading: str) -> None:
    """Заголовок с настоящей severity, но не по формату рендера — отказ.

    Схемно годная находка с `file: ""` рендерится как ``### [major] t — `:10```
    и под регулярку заголовка не подходила: блок **молча терялся**, и черновик
    объявлял PR чистым (`class: clean`) при живой блокирующей находке. Молчание
    тут хуже отказа: пропущенную находку никто не заметит.
    """
    body = f"{heading}\n- Сценарий: s\n"

    with pytest.raises(CandidatesError, match="не разобран заголовок находки"):
        parse_findings(body)


def test_draft_case_refuses_a_body_with_an_unparsed_finding() -> None:
    """Черновик с потерянным блоком находки не создаётся."""
    body = (
        "### [major] пустой файл — `:10`\n- Сценарий: s\n"
        f"\n<!-- codex-terminal-review head={HEAD} -->\n"
    )
    reviews = [{**REVIEWS[0], "body": body}]

    with pytest.raises(CandidatesError, match="не разобран заголовок находки"):
        draft_case("andrei-shtanakov/steward", 155, PR_META, reviews, commits_after=[])


@pytest.mark.parametrize("path_text", [" ", "  ", "\t"], ids=["space", "spaces", "tab"])
def test_parse_findings_refuses_a_blank_file_in_the_heading(path_text: str) -> None:
    """Пробельный путь в заголовке — находка, которую корпус всё равно отвергнет.

    `_HEADER_RE` берёт путь как «что угодно между бэктиками», поэтому
    ``### [major] t — ` :7` `` разбирался, а `load_case` потом падал на
    `file must not be blank` — далеко от причины и уже после записи черновика.
    """
    body = f"### [major] t — `{path_text}:7`\n- Сценарий: s\n"

    with pytest.raises(CandidatesError, match="пробельный путь в заголовке находки"):
        parse_findings(body)


def test_parse_findings_refuses_a_blank_file_in_evidence() -> None:
    """То же правило у записи evidence: ссылка на пробельный путь не ссылка."""
    body = "### [major] t — `a.py:7`\n- Сценарий: s\n- Evidence: ` :7` — причина\n"

    with pytest.raises(CandidatesError, match="пробельный путь в evidence"):
        parse_findings(body)


def test_parse_findings_keeps_a_reason_containing_a_semicolon() -> None:
    """`reason` с «; » не теряется: evidence разбирается по адресам, не по разделителю.

    Дробление строки по каждому `"; "` рассыпало запись, чей `reason` содержит
    ту же пару, — и черновик тихо подставлял вместо evidence адрес заголовка,
    то есть терял ровно то, чем находка доказывалась.
    """
    body = (
        "### [major] t — `a.py:7`\n"
        "- Сценарий: s\n"
        "- Evidence: `a.py:7` — сначала одно; дополнительная проверка ниже\n"
        "- confidence: high → БЛОКИРУЕТ\n"
    )

    evidence = parse_findings(body)[0].evidence

    assert len(evidence) == 1
    assert evidence[0].file == "a.py"
    assert evidence[0].line == 7
    assert evidence[0].reason == "сначала одно; дополнительная проверка ниже"


def test_parse_findings_splits_two_items_when_the_first_reason_has_a_semicolon() -> None:
    """Две записи разбираются обе, хотя в первом `reason` есть «; »."""
    body = (
        "### [major] t — `a.py:7`\n"
        "- Сценарий: s\n"
        "- Evidence: `a.py:7` — первая причина; и её продолжение; "
        "`b.py:9` — вторая причина\n"
    )

    evidence = parse_findings(body)[0].evidence

    assert [(item.file, item.line) for item in evidence] == [("a.py", 7), ("b.py", 9)]
    assert evidence[0].reason == "первая причина; и её продолжение"
    assert evidence[1].reason == "вторая причина"


@pytest.mark.parametrize(
    "line",
    [
        "мусор перед адресом `a.py:7` — причина",
        "`a.py:7` причина без тире",
        "`a.py` — адрес без строки",
    ],
    ids=["prefix-garbage", "no-dash", "no-line"],
)
def test_parse_findings_refuses_an_unparsed_evidence_line(line: str) -> None:
    """Строка evidence, разобранная не целиком, — отказ, а не молчаливая потеря."""
    body = f"### [major] t — `a.py:7`\n- Сценарий: s\n- Evidence: {line}\n"

    with pytest.raises(CandidatesError, match="не разобрана строка evidence"):
        parse_findings(body)


def test_parse_findings_keeps_the_no_evidence_dash() -> None:
    """`—` по-прежнему значит «evidence нет», а не негодную строку."""
    body = "### [minor] t — `a.py:7`\n- Сценарий: s\n- Evidence: —\n"

    assert parse_findings(body)[0].evidence == ()


def test_parse_findings_refuses_a_body_where_a_note_mimics_a_finding() -> None:
    """`Находок нет.` рядом с заголовком находки — неоднозначное тело, отказ.

    Строку `Находок нет.` печатает сам кит, когда находок нуль, и печатает её
    **после** `note` модели. Границы перед секцией находок в рендере нет,
    поэтому note, оформленный как заголовок находки, для парсера неотличим от
    настоящей находки. Совпадение двух признаков — пустой вердикт и найденный
    заголовок — значит, что тело собрано не китом, и черновик по нему не
    создаётся.
    """
    body = (
        "## Ревью Codex — независимый чек\n\n"
        "### [major] выдуманная находка — `src/a.py:7`\n"
        "- Сценарий: s\n\n"
        "Находок нет.\n"
    )

    with pytest.raises(CandidatesError, match="тело ревью неоднозначно"):
        parse_findings(body)


def test_parse_findings_accepts_the_empty_marker_without_any_finding() -> None:
    """Пустой вердикт без заголовков находок — обычное тело, а не неоднозначность."""
    assert parse_findings("## Ревью Codex — независимый чек\n\nНаходок нет.\n") == []


def test_parse_findings_accepts_a_normal_body_with_findings() -> None:
    """Тело кита с находками разбирается как прежде: маркера пустоты в нём нет."""
    assert len(parse_findings(BODY)) == 2


def test_review_head_reads_the_marker_and_tolerates_its_absence() -> None:
    assert review_head(BODY) == HEAD
    assert review_head("тело без маркера") is None
    assert review_head(f"<!-- codex-terminal-review head={HEAD} -->") == HEAD


def test_review_head_accepts_a_repeated_identical_marker() -> None:
    """Один и тот же маркер дважды — не двусмысленность: голова одна."""
    marker = f"<!-- codex-terminal-review head={HEAD} -->"
    assert review_head(f"{marker}\nтекст\n{marker}") == HEAD


def test_review_head_refuses_two_markers_with_different_shas() -> None:
    """Два маркера с разными SHA — тело неоднозначно, кейс не генерируется.

    Маркер пишет кит, но тело ревью содержит и `note` модели, который
    рендерится **раньше** доверенного маркера и не экранируется: взяв первый
    попавшийся, парсер пинил бы кейс на дерево, выбранное моделью.
    """
    other = "c" * 40
    body = (
        f"<!-- codex-terminal-review head={other} -->\n"
        f"текст\n"
        f"<!-- codex-terminal-review head={HEAD} -->\n"
    )

    with pytest.raises(CandidatesError, match="несколько маркеров head"):
        review_head(body)


def test_draft_case_refuses_an_ambiguous_marker(tmp_path: Path) -> None:
    """Черновик по неоднозначному телу не создаётся вовсе."""
    other = "c" * 40
    body = (
        f"{KIT_HEADER}\n\n"
        "### [major] настоящая — `a.py:7`\n- Сценарий: s\n"
        f"\n<!-- codex-terminal-review head={other} -->\n"
        f"<!-- codex-terminal-review head={HEAD} -->\n"
    )
    reviews = [{**REVIEWS[0], "body": body}]

    with pytest.raises(CandidatesError, match="несколько маркеров head"):
        draft_case("andrei-shtanakov/steward", 155, PR_META, reviews, commits_after=[])


# ---------------------------------------------------------------------------
# keywords_from_title
# ---------------------------------------------------------------------------


def test_keywords_are_the_longest_letter_words_casefolded() -> None:
    keywords = keywords_from_title(
        "Запись манифеста `dir/` и pathspec-магия — отказ",
        fallback_text="не важно, пока заголовок даёт слова",
        fallback="x.sh",
    )
    assert keywords == ["манифеста", "pathspec", "запись", "магия", "отказ"]


def test_keywords_fall_back_to_the_haystack_text_before_the_path() -> None:
    """Заголовок без букв → слова `scenario`/`expected_result`, а не путь файла.

    Матчер ищет ключевые слова в ``title scenario expected_result`` находки;
    путь файла в этот стог не входит вовсе, поэтому черновик с ключами из пути
    не мог совпасть даже с той находкой, из которой он сделан.
    """
    keywords = keywords_from_title(
        "1 2 3",
        fallback_text="PATH расширяется каталогом кита. Ожидалось: не расширяется",
        fallback="scripts/review/local.sh",
    )
    assert keywords == ["расширяется", "каталогом", "ожидалось", "path", "кита"]


def test_keywords_fall_back_to_the_path_when_no_text_has_words() -> None:
    """Ни в заголовке, ни в стоге букв нет — остаётся путь, как прежде."""
    assert keywords_from_title(
        "1 2 3", fallback_text="42 7", fallback="scripts/review/local.sh"
    ) == [
        "scripts",
        "review",
        "local",
        "sh",
    ]
    assert keywords_from_title("", fallback_text="", fallback="42") == ["42"]


# ---------------------------------------------------------------------------
# draft_case
# ---------------------------------------------------------------------------


def test_draft_case_matches_the_expected_structure() -> None:
    case = draft_case("andrei-shtanakov/steward", 155, PR_META, REVIEWS, commits_after=[])

    assert case["schema"] == "review-eval-case/v1"
    assert case["case_id"] == "andrei-shtanakov.steward-155"
    assert case["base_sha"] == BASE
    assert case["head_sha"] == HEAD
    assert case["class"] == "defective"  # есть major
    assert case["expected_outcome"] == "verdict"
    assert case["annotation"] == {
        "status": "draft",
        "blocking_complete": False,
        "source": "history-proxy",
        "adjudicated_by": None,
        "adjudicated_at": None,
    }
    assert case["non_defects"] == []

    first, second = case["defects"]
    assert first["id"] == "D-andrei-shtanakov.steward-155-1"
    assert first["severity"] == "major"
    assert first["file"] == "scripts/review/local.sh"
    assert first["line_hint"] == 644
    assert first["evidence"] == [
        "scripts/review/local.sh:644",
        "scripts/review/checksum.sh:157",
    ]
    assert first["match"]["files"] == ["scripts/review/local.sh"]
    assert first["match"]["line_window"] == 40
    assert "pathspec" not in first["match"]["keywords_any"]
    assert first["match"]["keywords_any"]

    # Находка без evidence всё равно получает непустой evidence: указатель
    # заголовка — корпус требует непустой список.
    assert second["id"] == "D-andrei-shtanakov.steward-155-2"
    assert second["severity"] == "minor"
    assert second["evidence"] == ["tests/review/test_harness_claude.py:203"]
    # `kind` находки переносится в gold, и у file-missing строки нет: gold
    # обязан уметь сказать «этот дефект — отсутствующий файл», иначе верная
    # находка такого рода не может стать TP (матчер сопоставляет по `kind`).
    assert second["kind"] == "file-missing"
    assert second["line_hint"] == 0
    # У обычной находки поле опускается: умолчание схемы — `defect`.
    assert "kind" not in first


def test_draft_case_records_candidate_status_in_notes_not_in_a_new_key() -> None:
    case = draft_case("andrei-shtanakov/steward", 155, PR_META, REVIEWS, commits_after=["a" * 40])
    assert "candidate_status" not in case
    assert "likely_tp" in case["notes"]
    assert "a" * 12 in case["notes"]

    quiet = draft_case("andrei-shtanakov/steward", 155, PR_META, REVIEWS, commits_after=[])
    assert "unknown" in quiet["notes"]
    assert "likely_tp" not in quiet["notes"]


def test_draft_case_without_blocking_findings_is_clean() -> None:
    body = BODY.replace("### [major]", "### [minor]")
    reviews = [{**REVIEWS[0], "body": body}]
    case = draft_case("andrei-shtanakov/steward", 155, PR_META, reviews, commits_after=[])
    assert case["class"] == "clean"
    assert [d["severity"] for d in case["defects"]] == ["minor", "minor"]


def test_draft_case_uses_the_last_ai_prosto_review_and_its_marker() -> None:
    later_head = "b" * 40
    reviews = [
        {**REVIEWS[0], "id": 1, "submitted_at": "2026-09-13T00:00:00Z"},
        {
            "id": 2,
            "user": {"login": "ai-prosto"},
            "submitted_at": "2026-09-14T10:00:00Z",
            "body": (
                f"{KIT_HEADER}\n\n"
                "### [blocker] поздняя находка — `a.py:7`\n"
                "- Сценарий: s\n- Наблюдаемое: o\n- Ожидаемое: e\n"
                "- Evidence: `a.py:7` — r\n- confidence: high → БЛОКИРУЕТ\n"
                f"\n<!-- codex-terminal-review head={later_head} -->\n"
            ),
        },
    ]
    case = draft_case("andrei-shtanakov/steward", 155, PR_META, reviews, commits_after=[])
    assert case["head_sha"] == later_head
    assert [d["id"] for d in case["defects"]] == ["D-andrei-shtanakov.steward-155-1"]
    assert "Ревью ai-prosto на PR: 2" in case["notes"]


def test_draft_case_falls_back_to_pr_head_when_the_marker_is_absent() -> None:
    body = f"{KIT_HEADER}\n\n### [minor] t — `a.py:1`\n- Сценарий: s\n"
    reviews = [{**REVIEWS[0], "body": body}]
    case = draft_case("andrei-shtanakov/steward", 155, PR_META, reviews, commits_after=[])
    assert case["head_sha"] == HEAD
    assert "из head.sha PR (маркера нет)" in case["notes"]


def test_draft_case_refuses_a_body_it_does_not_recognise() -> None:
    """Тело без признаков формата кита — отказ, а не «чистый» кейс.

    Непустое ревью ai-prosto, в котором парсер не нашёл ни находки, ни строки
    `Находок нет.`, давало `class: clean` с пустым `defects[]`: PR объявлялся
    чистым по факту **неразбора**. Такой кейс — не измерение, а испорченный
    gold: в метриках он даёт recall по нулю дефектов и хвалит ревьюера за
    молчание.
    """
    reviews = [{**REVIEWS[0], "body": "Проза ревьюера без находок и без шапки кита.\n"}]

    with pytest.raises(CandidatesError, match="тело ревью не распознано"):
        draft_case("andrei-shtanakov/steward", 155, PR_META, reviews, commits_after=[])


def test_draft_case_accepts_an_empty_verdict_with_the_kit_header() -> None:
    """Шапка кита плюс `Находок нет.` — законный чистый черновик."""
    body = f"{KIT_HEADER}\n\nНаходок нет.\n\n<!-- codex-terminal-review head={HEAD} -->\n"
    reviews = [{**REVIEWS[0], "body": body}]

    case = draft_case("andrei-shtanakov/steward", 155, PR_META, reviews, commits_after=[])

    assert case["class"] == "clean"
    assert case["defects"] == []


def test_draft_case_refuses_findings_without_the_kit_header() -> None:
    """Находки без шапки кита — тело не от доверенного рендера, отказ.

    Шапку печатает `apply-threshold.sh`; всё, что её не несёт, могло быть
    написано кем угодно, в том числе моделью в `note`. Признак формата — не
    придирка: на нём держится право считать заголовки находками.
    """
    body = (
        f"### [major] t — `a.py:7`\n- Сценарий: s\n\n<!-- codex-terminal-review head={HEAD} -->\n"
    )
    reviews = [{**REVIEWS[0], "body": body}]

    with pytest.raises(CandidatesError, match="тело ревью не распознано"):
        draft_case("andrei-shtanakov/steward", 155, PR_META, reviews, commits_after=[])


def test_draft_case_refuses_a_pr_without_an_ai_prosto_review() -> None:
    with pytest.raises(CandidatesError, match="нет ревью"):
        draft_case("andrei-shtanakov/steward", 155, PR_META, [REVIEWS[1]], commits_after=[])


def test_draft_case_skips_nit_findings_and_says_so_in_notes() -> None:
    """`nit` кит разрешает, корпус — нет: находка пропускается, след остаётся.

    Прежде она валила весь черновик (`severity вне набора корпуса`), то есть
    один косметический пункт ревью лишал разметчика всего PR. Пропуск без
    записи был бы не лучше — находка исчезла бы молча, — поэтому число и
    заголовки уходят в `notes`.
    """
    body = (
        f"{KIT_HEADER}\n\n"
        "### [major] настоящая — `a.py:7`\n"
        "- Сценарий: s\n- confidence: high → БЛОКИРУЕТ\n"
        "\n"
        "### [nit] лишний пробел — `b.py:3`\n"
        "- Сценарий: s2\n- confidence: low → не блокирует\n"
        f"\n<!-- codex-terminal-review head={HEAD} -->\n"
    )
    reviews = [{**REVIEWS[0], "body": body}]

    case = draft_case("andrei-shtanakov/steward", 155, PR_META, reviews, commits_after=[])

    assert [d["id"] for d in case["defects"]] == ["D-andrei-shtanakov.steward-155-1"]
    assert [d["severity"] for d in case["defects"]] == ["major"]
    assert case["class"] == "defective"
    assert "пропущено 1 находок severity nit" in case["notes"]
    assert "лишний пробел" in case["notes"]


def test_draft_case_with_only_nit_findings_is_clean_and_empty() -> None:
    """Только `nit` — валидный чистый черновик, а не отказ."""
    body = (
        f"{KIT_HEADER}\n\n"
        "### [nit] мелочь — `b.py:3`\n- Сценарий: s\n"
        f"\n<!-- codex-terminal-review head={HEAD} -->\n"
    )
    reviews = [{**REVIEWS[0], "body": body}]

    case = draft_case("andrei-shtanakov/steward", 155, PR_META, reviews, commits_after=[])

    assert case["defects"] == []
    assert case["class"] == "clean"
    assert "пропущено 1 находок severity nit" in case["notes"]


def test_draft_case_refuses_an_unknown_severity_instead_of_downgrading() -> None:
    body = f"{KIT_HEADER}\n\n### [critical] t — `a.py:1`\n- Сценарий: s\n"
    reviews = [{**REVIEWS[0], "body": body}]
    with pytest.raises(CandidatesError, match="вне набора корпуса"):
        draft_case("andrei-shtanakov/steward", 155, PR_META, reviews, commits_after=[])


def test_draft_case_uses_the_marker_when_pr_meta_has_no_head() -> None:
    """Маркер спрашивается **раньше** fallback: без `head.sha` PR черновик жив.

    `head.sha` нужен только там, где маркера нет. Требовать его всегда значило
    бы отказывать на PR, у которого ответ уже есть в теле ревью — а именно
    маркер и называет то дерево, которое ревьюер видел.
    """
    case = draft_case(
        "andrei-shtanakov/steward",
        155,
        {"base": {"sha": BASE}, "merge_commit_sha": None},
        REVIEWS,
        commits_after=[],
    )

    assert case["head_sha"] == HEAD
    assert "из маркера ревью" in case["notes"]


def test_draft_case_refuses_a_body_without_a_marker_and_without_head_sha() -> None:
    """Ни маркера, ни `head.sha` — пинить кейс нечем, отказ."""
    body = f"{KIT_HEADER}\n\n### [minor] t — `a.py:1`\n- Сценарий: s\n"
    reviews = [{**REVIEWS[0], "body": body}]

    with pytest.raises(CandidatesError, match="head.sha"):
        draft_case(
            "andrei-shtanakov/steward", 155, {"base": {"sha": BASE}}, reviews, commits_after=[]
        )


def test_draft_case_refuses_a_pr_meta_without_shas() -> None:
    with pytest.raises(CandidatesError, match="base.sha"):
        draft_case(
            "andrei-shtanakov/steward", 155, {"head": {"sha": HEAD}}, REVIEWS, commits_after=[]
        )


# ---------------------------------------------------------------------------
# render_case: черновик обязан проходить валидацию корпуса
# ---------------------------------------------------------------------------


def test_rendered_draft_loads_back_as_a_valid_case(tmp_path: Path) -> None:
    case = draft_case("andrei-shtanakov/steward", 155, PR_META, REVIEWS, commits_after=[])
    text = render_case(case)
    assert "annotation:" in text
    assert text.index("case_id") < text.index("base_sha")  # порядок ключей схемы

    path = tmp_path / "steward-155.yaml"
    path.write_text(text, encoding="utf-8")
    loaded = load_case(path)
    assert loaded.case_id == "andrei-shtanakov.steward-155"
    assert loaded.annotation.status == "draft"
    assert [d.id for d in loaded.defects] == [
        "D-andrei-shtanakov.steward-155-1",
        "D-andrei-shtanakov.steward-155-2",
    ]

    append_registry([loaded], tmp_path)
    assert "D-andrei-shtanakov.steward-155-1" in (tmp_path / "_ids.txt").read_text(encoding="utf-8")

    # Инвариант: что `draft_case` выдал, то `load_case` обязан принять. Он и
    # закрывает класс находок «черновик создан, а корпус его не берёт».
    again = tmp_path / "again.yaml"
    again.write_text(
        render_case(
            draft_case("andrei-shtanakov/steward", 155, PR_META, REVIEWS, commits_after=[])
        ),
        encoding="utf-8",
    )
    assert load_case(again).case_id == "andrei-shtanakov.steward-155"


def test_draft_case_for_a_repo_needing_normalisation_round_trips(tmp_path: Path) -> None:
    """Черновик репо `org/My_Repo.v2` проходит валидацию корпуса.

    Алфавит id — `[a-z0-9-]`, а `repo` допускает верхний регистр, `_` и `.`:
    без нормализации `draft_case` выдавал `case_id`/id со слагом `My_Repo.v2`,
    и собственный же `load_case` их отвергал — черновик такого репо нельзя
    было завести вовсе.
    """
    case = draft_case("org/My_Repo.v2", 7, PR_META, REVIEWS, commits_after=[])

    assert case["case_id"] == "org.my_repo.v2-7"
    assert [d["id"] for d in case["defects"]] == [
        "D-org.my_repo.v2-7-1",
        "D-org.my_repo.v2-7-2",
    ]

    path = tmp_path / f"{case['case_id']}.yaml"
    path.write_text(render_case(case), encoding="utf-8")
    loaded = load_case(path)

    assert loaded.repo == "org/My_Repo.v2"
    assert [d.id for d in loaded.defects] == ["D-org.my_repo.v2-7-1", "D-org.my_repo.v2-7-2"]


def test_draft_defect_matches_its_own_finding_when_the_title_has_no_words(
    tmp_path: Path,
) -> None:
    """Сквозная проверка: черновик находки с заголовком без букв даёт TP на ней же.

    Прежде `keywords_any` брались из пути файла, которого в стоге матчера нет,
    и черновик систематически не совпадал со своим же источником: верная
    находка превращалась в FP, а дефект — в пропуск.

    Путь и текст здесь намеренно на разных алфавитах: совпадение ключа со
    стогом — подстрочное, и короткое слово пути (`a` из `src/a.py`) нашлось бы
    внутри латинского слова текста, сделав проверку зелёной по случайности.
    """
    body = (
        f"{KIT_HEADER}\n\n"
        "### [major] 123 — `src/zzz.py:10`\n"
        "- Сценарий: Ревьюер запускается с расширенным путём поиска\n"
        "- Наблюдаемое: побеждает подставной исполняемый файл\n"
        "- Ожидаемое: Путь не расширяется вовсе\n"
        "- Evidence: `src/zzz.py:10` — путь расширяется\n"
        "- confidence: high → БЛОКИРУЕТ\n"
        f"\n<!-- codex-terminal-review head={HEAD} -->\n"
    )
    reviews = [
        {
            "id": 1,
            "user": {"login": "ai-prosto"},
            "state": "APPROVED",
            "submitted_at": "2026-09-15T08:00:00Z",
            "body": body,
        }
    ]

    case = draft_case("andrei-shtanakov/steward", 155, PR_META, reviews, commits_after=[])
    case["annotation"]["status"] = "adjudicated"
    case["annotation"]["adjudicated_by"] = "andrei-shtanakov"
    case["annotation"]["adjudicated_at"] = "2026-09-15"

    path = tmp_path / "steward-155.yaml"
    path.write_text(render_case(case), encoding="utf-8")
    loaded = load_case(path)

    assert loaded.defects[0].match.keywords_any == (
        "запускается",
        "расширенным",
        "расширяется",
        "ревьюер",
        "поиска",
    )

    prediction = Prediction(
        index=0,
        finding={
            "kind": "defect",
            "file": "src/zzz.py",
            "line": 10,
            "title": "123",
            "scenario": "Ревьюер запускается с расширенным путём поиска",
            "expected_result": "Путь не расширяется вовсе",
            "evidence": [{"file": "src/zzz.py", "line": 10, "reason": "путь расширяется"}],
        },
    )

    result = match([prediction], loaded.defects, loaded.non_defects)

    assert result.assigned == {0: "D-andrei-shtanakov.steward-155-1"}
    assert result.unlabeled == ()
    assert result.unmatched_defects == ()


def test_render_case_keeps_unicode_readable() -> None:
    case = draft_case("andrei-shtanakov/steward", 155, PR_META, REVIEWS, commits_after=[])
    text = render_case(case)
    assert "\\u" not in text
    assert yaml.safe_load(text)["case_id"] == "andrei-shtanakov.steward-155"


# ---------------------------------------------------------------------------
# commits_after
# ---------------------------------------------------------------------------


def test_commits_after_keeps_only_commits_later_than_the_review() -> None:
    commits = [
        {"sha": "1" * 40, "commit": {"committer": {"date": "2026-09-14T08:00:00Z"}}},
        {"sha": "2" * 40, "commit": {"committer": {"date": "2026-09-14T09:00:00Z"}}},
        {"sha": "3" * 40, "commit": {"author": {"date": "2026-09-14T10:00:00Z"}}},
        {"sha": "4" * 40, "commit": {}},
    ]
    assert commits_after(commits, "2026-09-14T08:35:07Z") == ["2" * 40, "3" * 40]
    assert commits_after(commits, None) == []


# ---------------------------------------------------------------------------
# fetch_*: подставной `gh`
# ---------------------------------------------------------------------------


def _stub_gh(tmp_path: Path, responses: dict[str, object]) -> Path:
    """Скрипт-подмена `gh`, отдающий заготовленный JSON по endpoint-у.

    Форма ответа — как у настоящего `gh api --paginate --slurp`: массив
    страниц. Так тест проверяет и снятие этой обёртки.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    data = bin_dir / "responses.json"
    data.write_text(json.dumps(responses), encoding="utf-8")
    script = bin_dir / "gh"
    script.write_text(
        "#!/bin/sh\n"
        'endpoint="$4"\n'
        f'exec python3 -c \'import json,sys; d=json.load(open("{data}"));'
        " e=sys.argv[1];"
        ' sys.exit("unexpected endpoint: " + e) if e not in d else None;'
        ' print(json.dumps(d[e]))\' "$endpoint"\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_fetch_reviews_and_pr_unwrap_the_paginated_slurp(tmp_path: Path) -> None:
    gh = _stub_gh(
        tmp_path,
        {
            "repos/andrei-shtanakov/steward/pulls/155": [PR_META],
            "repos/andrei-shtanakov/steward/pulls/155/reviews": [[REVIEWS[0]], [REVIEWS[1]]],
            "repos/andrei-shtanakov/steward/pulls/155/commits": [[]],
        },
    )
    assert fetch_pr("andrei-shtanakov/steward", 155, gh=str(gh))["base"]["sha"] == BASE
    reviews = fetch_reviews("andrei-shtanakov/steward", 155, gh=str(gh))
    assert [r["id"] for r in reviews] == [REVIEWS[0]["id"], REVIEWS[1]["id"]]
    assert fetch_commits("andrei-shtanakov/steward", 155, gh=str(gh)) == []


def test_fetch_reviews_reports_a_failing_gh(tmp_path: Path) -> None:
    script = tmp_path / "gh-broken"
    script.write_text("#!/bin/sh\necho 'gh: HTTP 404' >&2\nexit 1\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    with pytest.raises(CandidatesError, match="HTTP 404"):
        fetch_reviews("andrei-shtanakov/steward", 155, gh=str(script))


def test_fetch_reviews_reports_a_missing_gh() -> None:
    with pytest.raises(CandidatesError, match="не удалось запустить"):
        fetch_reviews("andrei-shtanakov/steward", 155, gh="gh-does-not-exist-anywhere")


def test_fetch_reviews_reports_non_json_output(tmp_path: Path) -> None:
    script = tmp_path / "gh-noise"
    script.write_text("#!/bin/sh\necho not json\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    with pytest.raises(CandidatesError, match="не JSON"):
        fetch_reviews("andrei-shtanakov/steward", 155, gh=str(script))


def test_fetch_is_the_only_network_surface() -> None:
    """Разбор и сборка черновика не зовут `gh` вовсе: `PATH` пуст."""
    saved = os.environ.get("PATH", "")
    os.environ["PATH"] = ""
    try:
        findings = parse_findings(BODY)
        case = draft_case("andrei-shtanakov/steward", 155, PR_META, REVIEWS, commits_after=[])
    finally:
        os.environ["PATH"] = saved
    assert isinstance(findings[0], ParsedFinding)
    assert case["case_id"] == "andrei-shtanakov.steward-155"
