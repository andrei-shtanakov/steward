"""Тесты steward.review_eval.corpus: схема кейса, валидация, реестр id.

Дизайн: docs/superpowers/specs/2026-09-14-review-eval-harness-design.md §5, §12.
"""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from steward.review_eval.corpus import (
    Case,
    CorpusError,
    append_registry,
    check_registry,
    corpus_digest,
    is_gold,
    load_case,
    load_corpus,
    registry_path,
    repo_slug,
)

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


def _valid_case(**overrides: Any) -> dict[str, Any]:
    """Минимальный валидный кейс (schema review-eval-case/v1) как dict."""
    case: dict[str, Any] = {
        "schema": "review-eval-case/v1",
        "case_id": "andrei-shtanakov.steward-155",
        "repo": "andrei-shtanakov/steward",
        "pr": 155,
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "class": "defective",
        "local_args": [],
        "expected_outcome": "verdict",
        "annotation": {
            "status": "adjudicated",
            "blocking_complete": True,
            "adjudicated_by": "github:andrei-shtanakov",
            "adjudicated_at": "2026-09-15",
            "source": "history-proxy",
        },
        "defects": [
            {
                "id": "D-andrei-shtanakov.steward-155-1",
                "severity": "major",
                "file": "scripts/review/local.sh",
                "line_hint": 644,
                "scenario": "PATH ставится и для codex-умолчания",
                "evidence": ["scripts/review/local.sh:644"],
                "match": {
                    "files": ["scripts/review/local.sh"],
                    "line_window": 40,
                    "keywords_any": ["PATH", "hijack"],
                },
            }
        ],
        "non_defects": [
            {
                "id": "NF-andrei-shtanakov.steward-155-1",
                "file": "docs/design.md",
                "line_hint": 151,
                "scenario": "ложная находка про формат",
                "match": {
                    "files": ["docs/design.md"],
                    "line_window": 20,
                    "keywords_any": ["формат"],
                },
            }
        ],
        "notes": "свободный текст разметчика",
    }
    case.update(overrides)
    return case


def _write_case(tmp_path: Path, data: dict[str, Any], name: str = "case.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _deep(data: dict[str, Any], *keys: str, value: Any) -> dict[str, Any]:
    """Глубокая копия data с точечной заменой data[keys[0]][keys[1]]... = value."""
    data = copy.deepcopy(data)
    node = data
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = value
    return data


# --------------------------------------------------------------------------
# load_case: успешный путь
# --------------------------------------------------------------------------


def test_load_case_valid(tmp_path: Path) -> None:
    path = _write_case(tmp_path, _valid_case())
    case = load_case(path)
    assert isinstance(case, Case)
    assert case.case_id == "andrei-shtanakov.steward-155"
    assert case.repo == "andrei-shtanakov/steward"
    assert case.pr == 155
    assert case.base_sha == BASE_SHA
    assert case.head_sha == HEAD_SHA
    assert case.cls == "defective"
    assert case.local_args == ()
    assert case.expected_outcome == "verdict"
    assert case.annotation.status == "adjudicated"
    assert case.annotation.blocking_complete is True
    assert case.annotation.adjudicated_by == "github:andrei-shtanakov"
    assert case.annotation.adjudicated_at == "2026-09-15"
    assert case.annotation.source == "history-proxy"
    assert len(case.defects) == 1
    defect = case.defects[0]
    assert defect.id == "D-andrei-shtanakov.steward-155-1"
    assert defect.severity == "major"
    assert defect.match.files == ("scripts/review/local.sh",)
    assert defect.match.line_window == 40
    assert defect.match.keywords_any == ("PATH", "hijack")
    assert len(case.non_defects) == 1
    non_defect = case.non_defects[0]
    assert non_defect.id == "NF-andrei-shtanakov.steward-155-1"
    assert case.notes == "свободный текст разметчика"


def test_load_case_defaults_local_args_defects_notes(tmp_path: Path) -> None:
    data = _valid_case()
    del data["local_args"]
    del data["defects"]
    del data["non_defects"]
    del data["notes"]
    data["class"] = "clean"
    data["annotation"]["status"] = "draft"
    data["annotation"]["adjudicated_by"] = None
    data["annotation"]["adjudicated_at"] = None
    path = _write_case(tmp_path, data)
    case = load_case(path)
    assert case.local_args == ()
    assert case.defects == ()
    assert case.non_defects == ()
    assert case.notes == ""


# --------------------------------------------------------------------------
# load_case: правила валидации верхнего уровня
# --------------------------------------------------------------------------


def test_top_level_must_be_mapping(tmp_path: Path) -> None:
    path = tmp_path / "case.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(CorpusError, match="mapping"):
        load_case(path)


def test_duplicate_top_level_key_rejected(tmp_path: Path) -> None:
    """Повторный ключ YAML — ошибка, а не «побеждает последний».

    `yaml.safe_load` молча оставляет последнее значение: второй `defects: []`
    стирал весь gold, и кейс оставался «валидным» — с нулём дефектов. Такую
    правку не видно ни в диффе глазами (ключ выглядит на месте), ни в
    проверках схемы.
    """
    path = tmp_path / "case.yaml"
    path.write_text(
        yaml.safe_dump(_valid_case(), allow_unicode=True, sort_keys=False) + "defects: []\n",
        encoding="utf-8",
    )

    with pytest.raises(CorpusError, match="повторный ключ 'defects'"):
        load_case(path)


def test_duplicate_nested_key_rejected(tmp_path: Path) -> None:
    """Повтор на любом уровне вложенности, не только верхнем."""
    path = tmp_path / "case.yaml"
    path.write_text(
        """schema: review-eval-case/v1
case_id: andrei-shtanakov.steward-155
repo: andrei-shtanakov/steward
pr: 155
base_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
head_sha: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
class: defective
expected_outcome: verdict
annotation:
  status: draft
  blocking_complete: false
  adjudicated_by: null
  adjudicated_at: null
  source: manual
defects:
  - id: D-andrei-shtanakov.steward-155-1
    severity: major
    severity: minor
    file: a.py
    line_hint: 1
    scenario: s
    evidence: ["a.py:1"]
    match: {files: ["a.py"], line_window: 5, keywords_any: ["x"]}
non_defects: []
notes: ""
""",
        encoding="utf-8",
    )

    with pytest.raises(CorpusError, match="повторный ключ 'severity'"):
        load_case(path)


def test_duplicate_key_error_names_the_line(tmp_path: Path) -> None:
    """Сообщение называет строку: иначе искать повтор в большом кейсе нечем."""
    path = tmp_path / "case.yaml"
    text = yaml.safe_dump(_valid_case(), allow_unicode=True, sort_keys=False) + "notes: ещё\n"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(CorpusError, match="строка") as excinfo:
        load_case(path)
    assert str(len(text.splitlines())) in str(excinfo.value)


def test_unknown_top_level_key_rejected(tmp_path: Path) -> None:
    data = _valid_case()
    data["surprise"] = "field"
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="surprise"):
        load_case(path)


def test_unknown_keys_of_mixed_types_still_corpus_error(tmp_path: Path) -> None:
    """Нестроковый лишний ключ (`1:`) рядом со строковым — всё равно CorpusError.

    YAML отдаёт ключи их собственными типами; сортировать str и int вместе
    нельзя, и без приведения к строке наружу уходил TypeError вместо
    штатной ошибки корпуса.
    """
    data = _valid_case()
    data["surprise"] = "field"
    data[1] = "int-key"
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="неизвестный ключ"):
        load_case(path)


@pytest.mark.parametrize(
    ("section", "where"),
    [("defects", "defects[0]"), ("non_defects", "non_defects[0]")],
)
def test_unknown_key_in_an_entry_rejected(tmp_path: Path, section: str, where: str) -> None:
    """Опечатка в имени поля записи — ошибка, а не молчаливое умолчание.

    `knd: file-missing` читался как отсутствие `kind`, то есть запись тихо
    становилась строчным дефектом: gold говорил не то, что написал разметчик,
    и заметить это было нечем.
    """
    data = _valid_case()
    data[section][0]["knd"] = "file-missing"
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="knd") as excinfo:
        load_case(path)
    assert where in str(excinfo.value)


def test_unknown_key_in_annotation_rejected(tmp_path: Path) -> None:
    """То же для `annotation`: опечатка в `blocking_complete` меняла бы знаменатели."""
    data = _valid_case()
    data["annotation"]["blocking_complte"] = True
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="blocking_complte") as excinfo:
        load_case(path)
    assert "annotation" in str(excinfo.value)


def test_unknown_key_in_match_rejected(tmp_path: Path) -> None:
    """И для `match`: опечатка в `line_window` молча вернула бы умолчание правила."""
    data = _valid_case()
    data["defects"][0]["match"]["line_windw"] = 10
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="line_windw") as excinfo:
        load_case(path)
    assert "match" in str(excinfo.value)


def test_schema_must_match(tmp_path: Path) -> None:
    data = _valid_case(schema="review-eval-case/v2")
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="schema"):
        load_case(path)


@pytest.mark.parametrize(
    "repo",
    ["/steward", "steward/", "a/b/c", "a b/c", "steward", "org/re po"],
    ids=["no-owner", "no-name", "three-parts", "space-in-owner", "no-slash", "space-in-name"],
)
def test_repo_must_be_owner_slash_name(tmp_path: Path, repo: str) -> None:
    """`repo` — ровно `owner/name` из безопасных символов (§5).

    Кэш строит из него имя каталога и URL (`cache.repo_cache_dir`), поэтому
    форма, которую пропускает корпус, но отвергает кэш, давала валидный кейс,
    на котором падает `corpus materialize`: ошибка всплывала на шаг позже и в
    другом месте, чем её причина.
    """
    # Пустая строка сюда не входит: её ловит более общее правило «непустая
    # строка» и говорит об этом своими словами.
    data = _valid_case(repo=repo)
    path = _write_case(tmp_path, data)
    # Именно про форму `repo`, а не про производный `case_id`: иначе тест
    # проходил бы на сообщении о `case_id` и правила `repo` не проверял.
    with pytest.raises(CorpusError, match="repo must look like"):
        load_case(path)


@pytest.mark.parametrize(
    ("repo", "slug"),
    [
        ("andrei-shtanakov/steward", "andrei-shtanakov.steward"),
        ("org/My_Repo.v2", "org.my_repo.v2"),
        ("org/UPPER", "org.upper"),
        ("org/my_repo", "org.my_repo"),
        ("org/my.repo", "org.my.repo"),
        ("org-a/service", "org-a.service"),
        ("org-b/service", "org-b.service"),
    ],
    ids=["plain", "mixed", "upper", "underscore", "dot", "org-a", "org-b"],
)
def test_repo_slug_is_owner_dot_name(repo: str, slug: str) -> None:
    """Слаг — `owner.name` в нижнем регистре, и он **инъективен**.

    Прежний слаг брал только имя и схлопывал `_`/`.` в `-`: `org-a/service` и
    `org-b/service` получали один слаг, как и `org/my_repo` с `org/my.repo`, —
    то есть кейсы разных репозиториев с одним номером PR сталкивались по
    `case_id` и по id дефектов. Владелец не может содержать `.` (логины
    GitHub — `[A-Za-z0-9-]`), поэтому первая точка разделяет части однозначно,
    а имена GitHub регистронезависимы, так что нижний регистр склеивает только
    одно и то же репо.
    """
    assert repo_slug(repo) == slug


def test_repo_slug_merges_only_case_differences() -> None:
    """`Org/Repo` и `org/repo` — одно репо, остальное — разные слаги."""
    assert repo_slug("Org/Repo") == repo_slug("org/repo") == "org.repo"
    assert repo_slug("org/my_repo") != repo_slug("org/my.repo")
    assert repo_slug("org-a/service") != repo_slug("org-b/service")


def test_cases_of_two_repos_with_the_same_pr_coexist(tmp_path: Path) -> None:
    """Два репозитория, один номер PR — два кейса в одном корпусе, без коллизий."""
    corpus_dir = tmp_path
    first = _valid_case(repo="org/my_repo", case_id="org.my_repo-7", pr=7)
    first["defects"][0]["id"] = "D-org.my_repo-7-1"
    first["non_defects"][0]["id"] = "NF-org.my_repo-7-1"
    second = _valid_case(repo="org/my.repo", case_id="org.my.repo-7", pr=7)
    second["defects"][0]["id"] = "D-org.my.repo-7-1"
    second["non_defects"][0]["id"] = "NF-org.my.repo-7-1"
    loaded = [
        load_case(_write_case(corpus_dir, first, "a.yaml")),
        load_case(_write_case(corpus_dir, second, "b.yaml")),
    ]
    append_registry(loaded, corpus_dir)

    cases = load_corpus(corpus_dir)

    assert [case.case_id for case in cases] == ["org.my.repo-7", "org.my_repo-7"]


@pytest.mark.parametrize(
    "repo",
    ["org.a/repo", "org_a/repo", "../evil", "a/..", "./x", "a/.", "not-a-repo", "org/re po"],
    ids=[
        "owner-dot",
        "owner-underscore",
        "traversal",
        "name-dotdot",
        "dot-slash",
        "name-dot",
        "no-slash",
        "space",
    ],
)
def test_repo_re_refuses_forms_github_cannot_have(repo: str) -> None:
    """Владелец — только `[A-Za-z0-9-]`: точка и подчёркивание в логине невозможны.

    Это и делает слаг инъективным: первая точка в слаге всегда разделяет
    владельца и имя.
    """
    with pytest.raises(CorpusError, match="repo must look like"):
        repo_slug(repo)


def test_defect_id_parses_with_a_slug_containing_dashes_and_digits() -> None:
    """Разбор id идёт **справа**: `pr` и `n` — последние два числа.

    Слаг сам содержит `-` и цифры (`foo-1`), поэтому левый разбор путал бы
    границу слага с номером PR.
    """
    data = _valid_case(repo="foo-1/repo", case_id="foo-1.repo-55", pr=55)
    data["defects"][0]["id"] = "D-foo-1.repo-55-2"
    data["non_defects"][0]["id"] = "NF-foo-1.repo-55-3"

    case = load_case(_write_case(Path(tempfile.mkdtemp()), data))

    assert case.defects[0].id == "D-foo-1.repo-55-2"
    assert case.non_defects[0].id == "NF-foo-1.repo-55-3"


def test_case_with_a_normalised_slug_is_valid(tmp_path: Path) -> None:
    """Кейс репо `org/My_Repo.v2` живёт под слагом `org.my_repo.v2` — и с дефектами."""
    data = _valid_case(repo="org/My_Repo.v2", case_id="org.my_repo.v2-7", pr=7)
    data["defects"][0]["id"] = "D-org.my_repo.v2-7-1"
    data["non_defects"][0]["id"] = "NF-org.my_repo.v2-7-1"

    case = load_case(_write_case(tmp_path, data))

    assert case.case_id == "org.my_repo.v2-7"
    assert [d.id for d in case.defects] == ["D-org.my_repo.v2-7-1"]


@pytest.mark.parametrize("pr", [0, -7])
def test_pr_number_must_be_positive(tmp_path: Path, pr: int) -> None:
    """Номер PR — целое ≥ 1 (как в approval-facts): ноль и отрицательные — ошибка."""
    data = _valid_case(pr=pr, case_id=f"andrei-shtanakov.steward-{pr}")
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="pr.*≥ 1"):
        load_case(path)


def test_case_id_must_match_repo_and_pr(tmp_path: Path) -> None:
    data = _valid_case(case_id="wrong-155")
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="case_id"):
        load_case(path)


@pytest.mark.parametrize("field_name", ["base_sha", "head_sha"])
def test_sha_must_be_40_lowercase_hex(tmp_path: Path, field_name: str) -> None:
    data = _valid_case(**{field_name: "not-a-sha"})
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match=field_name):
        load_case(path)


@pytest.mark.parametrize("field_name", ["base_sha", "head_sha"])
def test_sha_must_be_lowercase(tmp_path: Path, field_name: str) -> None:
    data = _valid_case(**{field_name: "A" * 40})
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match=field_name):
        load_case(path)


def test_base_and_head_sha_must_differ(tmp_path: Path) -> None:
    data = _valid_case(head_sha=BASE_SHA)
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="differ"):
        load_case(path)


def test_class_must_be_known(tmp_path: Path) -> None:
    data = _valid_case(**{"class": "huge"})
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="class"):
        load_case(path)


def test_large_without_local_args_or_guardrail_rejected(tmp_path: Path) -> None:
    data = _valid_case(**{"class": "large"}, local_args=[], expected_outcome="verdict")
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="large"):
        load_case(path)


def test_large_with_local_args_ok(tmp_path: Path) -> None:
    data = _valid_case(
        **{"class": "large"},
        local_args=["--max-diff-files", "60"],
        expected_outcome="verdict",
    )
    path = _write_case(tmp_path, data)
    case = load_case(path)
    assert case.cls == "large"
    assert case.local_args == ("--max-diff-files", "60")


def test_large_with_guardrail_rejection_and_no_local_args_ok(tmp_path: Path) -> None:
    data = _valid_case(**{"class": "large"}, local_args=[], expected_outcome="guardrail_rejection")
    path = _write_case(tmp_path, data)
    case = load_case(path)
    assert case.expected_outcome == "guardrail_rejection"


def test_local_args_cannot_override_the_measured_range(tmp_path: Path) -> None:
    """`local_args` не имеет права переопределять диапазон измерения.

    `local.sh` берёт **последний** `--head`, а раннер дописывает `local_args`
    после своих `--base A --head B`: кейс с `--head C` измерял бы A..C под gold
    диапазона A..B. Разрешён только список из потолков дифа.
    """
    data = _valid_case(**{"class": "large"}, local_args=["--head", "c" * 40])
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="local_args"):
        load_case(path)


@pytest.mark.parametrize(
    "local_args",
    [
        ["--max-diff-files"],
        ["--max-diff-files", "x"],
        ["--max-diff-files", "0"],
        ["--max-diff-files", "-1"],
        ["--max-diff-files", "60", "--max-diff-files", "70"],
        ["--max-diff-bytes", "900000", "--verbose"],
        ["--format", "text"],
    ],
    ids=[
        "flag-without-value",
        "value-not-an-int",
        "value-zero",
        "value-negative",
        "duplicate-flag",
        "odd-tail",
        "flag-not-allowed",
    ],
)
def test_local_args_allow_list(tmp_path: Path, local_args: list[str]) -> None:
    """Только `--max-diff-bytes`/`--max-diff-files` с положительным целым, по разу."""
    data = _valid_case(**{"class": "large"}, local_args=local_args)
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="local_args"):
        load_case(path)


def test_local_args_allowed_pair_ok(tmp_path: Path) -> None:
    """Оба потолка сразу — законная форма."""
    data = _valid_case(
        **{"class": "large"},
        local_args=["--max-diff-bytes", "900000", "--max-diff-files", "60"],
    )
    case = load_case(_write_case(tmp_path, data))
    assert case.local_args == ("--max-diff-bytes", "900000", "--max-diff-files", "60")


def test_local_args_only_for_large(tmp_path: Path) -> None:
    """Потолки дифа осмысленны только у `large`: у остальных классов — ошибка."""
    data = _valid_case(local_args=["--max-diff-files", "60"])
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="large"):
        load_case(path)


def test_expected_outcome_must_be_known(tmp_path: Path) -> None:
    data = _valid_case(expected_outcome="maybe")
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="expected_outcome"):
        load_case(path)


# --------------------------------------------------------------------------
# load_case: annotation
# --------------------------------------------------------------------------


def test_annotation_status_must_be_known(tmp_path: Path) -> None:
    data = _deep(_valid_case(), "annotation", "status", value="approved")
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="status"):
        load_case(path)


def test_annotation_adjudicated_requires_adjudicated_by(tmp_path: Path) -> None:
    data = _deep(_valid_case(), "annotation", "adjudicated_by", value=None)
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="adjudicated_by"):
        load_case(path)


def test_annotation_adjudicated_requires_adjudicated_at(tmp_path: Path) -> None:
    data = _deep(_valid_case(), "annotation", "adjudicated_at", value=None)
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="adjudicated_at"):
        load_case(path)


@pytest.mark.parametrize(
    "value",
    ["not-a-date", "20260915", "2026-9-15", "2026-02-30", "2026-09-15T00:00:00", "26-09-15"],
    ids=["prose", "compact", "short-parts", "impossible-day", "timestamp", "two-digit-year"],
)
def test_annotation_adjudicated_at_must_be_iso_date(tmp_path: Path, value: str) -> None:
    """Дата разметки — строго `YYYY-MM-DD`.

    `date.fromisoformat` в Python 3.11+ принимает и `20260915`, и `2026-W01-1`:
    поле провенанса читают люди и грепают скрипты, и две формы одной даты
    сделали бы из него две разные строки.
    """
    data = _deep(_valid_case(), "annotation", "adjudicated_at", value=value)
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="adjudicated_at"):
        load_case(path)


def test_annotation_adjudicated_at_accepts_the_canonical_form(tmp_path: Path) -> None:
    """Канонический вид принимается (и YAML-дата без кавычек тоже — она такая же)."""
    data = _deep(_valid_case(), "annotation", "adjudicated_at", value="2026-09-15")
    assert load_case(_write_case(tmp_path, data)).annotation.adjudicated_at == "2026-09-15"


def test_annotation_draft_does_not_require_adjudicated_fields(tmp_path: Path) -> None:
    data = _valid_case()
    data["annotation"]["status"] = "draft"
    data["annotation"]["adjudicated_by"] = None
    data["annotation"]["adjudicated_at"] = None
    path = _write_case(tmp_path, data)
    case = load_case(path)
    assert case.annotation.status == "draft"
    assert case.annotation.adjudicated_by is None
    assert case.annotation.adjudicated_at is None


def test_annotation_source_must_be_known(tmp_path: Path) -> None:
    data = _deep(_valid_case(), "annotation", "source", value="vibes")
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="source"):
        load_case(path)


# --------------------------------------------------------------------------
# load_case: defects / non_defects / match
# --------------------------------------------------------------------------


def test_defect_kind_file_missing_requires_a_file_level_hint(tmp_path: Path) -> None:
    """`kind: file-missing` — утверждение об отсутствии файла: строки у него нет.

    Поле нужно, чтобы gold мог сказать «этот дефект — отсутствующий файл»:
    без него верная находка `file-missing` не могла стать TP вовсе (матчер не
    сопоставляет её строчным дефектам, и она уходила в FP, а дефект — в
    пропуски).
    """
    data = _valid_case()
    data["defects"][0]["kind"] = "file-missing"
    data["defects"][0]["line_hint"] = 0

    case = load_case(_write_case(tmp_path, data))

    assert case.defects[0].kind == "file-missing"
    assert case.defects[0].line_hint == 0


def test_defect_kind_defaults_to_defect(tmp_path: Path) -> None:
    """Поле опционально: без него запись — обычный строчный дефект."""
    case = load_case(_write_case(tmp_path, _valid_case()))
    assert case.defects[0].kind == "defect"
    assert case.non_defects[0].kind == "defect"


@pytest.mark.parametrize("section", ["defects", "non_defects"])
def test_entry_kind_file_missing_rejects_a_line_hint(tmp_path: Path, section: str) -> None:
    """`file-missing` со строкой — противоречие: файла нет, а строка в нём есть."""
    data = _valid_case()
    data[section][0]["kind"] = "file-missing"
    data[section][0]["line_hint"] = 3
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="line_hint"):
        load_case(path)


@pytest.mark.parametrize("section", ["defects", "non_defects"])
def test_entry_kind_must_be_known(tmp_path: Path, section: str) -> None:
    """Набор `kind` закрыт — как у кита (`defect` | `file-missing`)."""
    data = _valid_case()
    data[section][0]["kind"] = "bogus"
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="kind"):
        load_case(path)


def test_non_defect_kind_file_missing_is_allowed(tmp_path: Path) -> None:
    """Ложное «файла нет» — законный non-defect (кит такое выдаёт)."""
    data = _valid_case()
    data["non_defects"][0]["kind"] = "file-missing"
    data["non_defects"][0]["line_hint"] = 0

    case = load_case(_write_case(tmp_path, data))

    assert case.non_defects[0].kind == "file-missing"


def test_kind_is_part_of_the_identity_core(tmp_path: Path) -> None:
    """Смена `kind` — другой дефект, а не правка: ядро идентичности его включает.

    Файл и сценарий те же, но «дефект в файле» и «файла нет» — разные
    утверждения, и оставлять им один id значило бы склеить в истории измерений
    два разных дефекта.
    """
    as_defect = tmp_path / "as-defect"
    as_missing = tmp_path / "as-missing"
    for directory, kind in ((as_defect, "defect"), (as_missing, "file-missing")):
        directory.mkdir()
        data = _valid_case()
        data["defects"][0]["kind"] = kind
        data["defects"][0]["line_hint"] = 0
        append_registry([load_case(_write_case(directory, data, "steward-155.yaml"))], directory)

    identity_of = {
        directory: _registry_line(directory, "D-andrei-shtanakov.steward-155-1").split()[2]
        for directory in (as_defect, as_missing)
    }
    assert identity_of[as_defect] != identity_of[as_missing]


def test_changing_kind_under_a_live_id_needs_an_acknowledgement(tmp_path: Path) -> None:
    """Следствие: перевести живой id из строчного дефекта в file-missing нельзя молча."""
    corpus_dir = tmp_path
    data = _valid_case()
    data["defects"][0]["line_hint"] = 0
    case = load_case(_write_case(corpus_dir, data, "steward-155.yaml"))
    append_registry([case], corpus_dir)

    flipped = copy.deepcopy(data)
    flipped["defects"][0]["kind"] = "file-missing"
    flipped_case = load_case(_write_case(corpus_dir, flipped, "steward-155.yaml"))

    with pytest.raises(CorpusError, match="другой дефект под живым id"):
        check_registry([flipped_case], corpus_dir)


def test_defect_severity_must_be_known(tmp_path: Path) -> None:
    data = _deep(_valid_case(), "defects", value=_valid_case()["defects"])
    data["defects"][0]["severity"] = "critical"
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="severity"):
        load_case(path)


def test_defect_id_must_match_pattern(tmp_path: Path) -> None:
    data = _valid_case()
    data["defects"][0]["id"] = "defect-1"
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="id"):
        load_case(path)


def test_defect_id_must_embed_the_case_repo_and_pr(tmp_path: Path) -> None:
    """Id обязан нести слаг репо и номер PR **своего** кейса: `D-foo-9-1` в
    `andrei-shtanakov.steward-161` — чужой id, а не просто непривычный (§5)."""
    data = _valid_case(case_id="andrei-shtanakov.steward-161", pr=161)
    data["defects"][0]["id"] = "D-foo-9-1"
    data["non_defects"][0]["id"] = "NF-andrei-shtanakov.steward-161-1"
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="D-andrei-shtanakov.steward-161-"):
        load_case(path)


def test_non_defect_id_must_embed_the_case_repo_and_pr(tmp_path: Path) -> None:
    """То же правило для `NF-`: слаг и PR — из кейса, не произвольные."""
    data = _valid_case(case_id="andrei-shtanakov.steward-161", pr=161)
    data["defects"][0]["id"] = "D-andrei-shtanakov.steward-161-1"
    data["non_defects"][0]["id"] = "NF-foo-9-1"
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="NF-andrei-shtanakov.steward-161-"):
        load_case(path)


def test_defect_evidence_must_be_non_empty(tmp_path: Path) -> None:
    """`evidence` дефекта — непустой список (T4): gold без ссылки на код нечем
    проверить, а разметчику нечего перечитывать."""
    data = _valid_case()
    data["defects"][0]["evidence"] = []
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="evidence"):
        load_case(path)


def test_non_defect_id_must_match_pattern(tmp_path: Path) -> None:
    data = _valid_case()
    data["non_defects"][0]["id"] = "D-andrei-shtanakov.steward-155-1"
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="id"):
        load_case(path)


def test_non_defect_forbids_severity_key(tmp_path: Path) -> None:
    data = _valid_case()
    data["non_defects"][0]["severity"] = "minor"
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="severity"):
        load_case(path)


def test_non_defect_forbids_evidence_key(tmp_path: Path) -> None:
    data = _valid_case()
    data["non_defects"][0]["evidence"] = ["a:1"]
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="evidence"):
        load_case(path)


def test_match_line_window_must_be_non_negative(tmp_path: Path) -> None:
    data = _valid_case()
    data["defects"][0]["match"]["line_window"] = -1
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="line_window"):
        load_case(path)


def test_match_files_must_be_non_empty(tmp_path: Path) -> None:
    data = _valid_case()
    data["defects"][0]["match"]["files"] = []
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="files"):
        load_case(path)


def test_match_keywords_any_must_be_non_empty(tmp_path: Path) -> None:
    data = _valid_case()
    data["defects"][0]["match"]["keywords_any"] = []
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="keywords_any"):
        load_case(path)


@pytest.mark.parametrize("keyword", ["", " ", "\t\n"], ids=["empty", "space", "tabs"])
def test_match_keyword_must_not_be_blank(tmp_path: Path, keyword: str) -> None:
    """Пустое ключевое слово матчит **всё**: `"" in haystack` истинно всегда.

    Такой дефект «находился» бы любой находкой в своём файле и окне, то есть
    молча завышал recall и precision. Правило схемы, а не гигиена.
    """
    data = _valid_case()
    data["defects"][0]["match"]["keywords_any"] = ["hijack", keyword]
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="keywords_any"):
        load_case(path)


@pytest.mark.parametrize("blank", ["  ", "\t", "\n"], ids=["spaces", "tab", "newline"])
def test_defect_scenario_must_not_be_blank(tmp_path: Path, blank: str) -> None:
    """Пробельный `scenario` — ground truth без содержания, и он значим.

    `scenario` входит в **ядро идентичности** записи и в стог ключевых слов
    матчера. Пробельная строка проходила как значение: запись получала
    идентичность, построенную на пустоте, и разметчик, читая кейс, не видел,
    о чём дефект. Отказ здесь дешевле молчаливого gold без смысла.
    """
    data = _valid_case()
    data["defects"][0]["scenario"] = blank
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="defects\\[0\\].*'scenario' must not be blank"):
        load_case(path)


def test_non_defect_scenario_must_not_be_blank(tmp_path: Path) -> None:
    """То же правило у non-defect: его `scenario` — тоже ground truth."""
    data = _valid_case()
    data["non_defects"][0]["scenario"] = " "
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="non_defects\\[0\\].*'scenario' must not be blank"):
        load_case(path)


def test_defect_evidence_item_must_not_be_blank(tmp_path: Path) -> None:
    """Пробельный элемент `evidence[]` — ссылка в никуда."""
    data = _valid_case()
    data["defects"][0]["evidence"] = ["scripts/review/local.sh:644", "  "]
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="evidence"):
        load_case(path)


def test_annotation_adjudicated_by_must_not_be_blank(tmp_path: Path) -> None:
    """Пробельный `adjudicated_by` — адъюдикация без адъюдикатора.

    Проверка не зависит от `status`: поле присутствует — значит объявлено, и
    пробельное значение выдаёт разметку за подтверждённую кем-то.
    """
    data = _valid_case()
    data["annotation"]["adjudicated_by"] = "   "
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="annotation.*'adjudicated_by' must not be blank"):
        load_case(path)


def test_match_keywords_must_be_unique_casefold(tmp_path: Path) -> None:
    """Повтор ключевого слова завышает вес ребра, не добавляя доказательства.

    Вес ребра — число попавших ключей, и `["Path", "path"]` даёт двойку там,
    где сказано одно слово: такой дефект побеждает равного конкурента и
    превращает неоднозначность в TP. Сравнение по `casefold`, потому что и
    матчинг ключей регистронезависим.
    """
    data = _valid_case()
    data["defects"][0]["match"]["keywords_any"] = ["Path", "hijack", "path"]
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="keywords_any.*повтор.*'path'"):
        load_case(path)


def test_match_files_must_be_unique_casefold(tmp_path: Path) -> None:
    """Повтор пути в `match.files` — та же подделка веса с другой стороны."""
    data = _valid_case()
    data["defects"][0]["match"]["files"] = [
        "scripts/review/local.sh",
        "scripts/review/Local.sh",
    ]
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="files.*повтор"):
        load_case(path)


def test_match_keywords_unique_check_ignores_surrounding_space(tmp_path: Path) -> None:
    """`\"PATH \"` и `\"PATH\"` — то же слово: сравнение после strip."""
    data = _valid_case()
    data["non_defects"][0]["match"]["keywords_any"] = ["формат", "формат "]
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="keywords_any.*повтор"):
        load_case(path)


def test_match_files_entry_must_not_be_blank(tmp_path: Path) -> None:
    """Пустой путь в `match.files` — то же самое с другой стороны правила."""
    data = _valid_case()
    data["defects"][0]["match"]["files"] = ["scripts/review/local.sh", "  "]
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="files"):
        load_case(path)


@pytest.mark.parametrize("section", ["defects", "non_defects"])
def test_entry_file_must_not_be_blank(tmp_path: Path, section: str) -> None:
    """`file` записи — адрес для разбирающего; пробелы адресом не являются."""
    data = _valid_case()
    data[section][0]["file"] = "   "
    path = _write_case(tmp_path, data)
    with pytest.raises(CorpusError, match="file"):
        load_case(path)


# --------------------------------------------------------------------------
# is_gold
# --------------------------------------------------------------------------


def test_is_gold_true_for_adjudicated(tmp_path: Path) -> None:
    case = load_case(_write_case(tmp_path, _valid_case()))
    assert is_gold(case) is True


def test_is_gold_false_for_draft(tmp_path: Path) -> None:
    data = _valid_case()
    data["annotation"]["status"] = "draft"
    data["annotation"]["adjudicated_by"] = None
    data["annotation"]["adjudicated_at"] = None
    case = load_case(_write_case(tmp_path, data))
    assert is_gold(case) is False


# --------------------------------------------------------------------------
# load_corpus: кросс-кейсовая уникальность + сортировка
# --------------------------------------------------------------------------


def _second_case() -> dict[str, Any]:
    data = _valid_case()
    data["case_id"] = "andrei-shtanakov.steward-100"
    data["pr"] = 100
    data["defects"][0]["id"] = "D-andrei-shtanakov.steward-100-1"
    data["non_defects"][0]["id"] = "NF-andrei-shtanakov.steward-100-1"
    return data


def _register(directory: Path, *cases_data: dict[str, Any]) -> None:
    """Загрузить кейсы напрямую (без check_registry) и дописать реестр под них.

    Передавать нужно **весь** состав корпуса за раз: `append_registry`
    определяет присутствие id по переданному списку и списывает отсутствующие
    надгробиями (§5).
    """
    from steward.review_eval import corpus as corpus_mod

    loaded = []
    for i, data in enumerate(cases_data):
        path = directory / f"tmp-register-{i}.yaml"
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        loaded.append(corpus_mod.load_case(path))
        path.unlink()
    append_registry(loaded, directory)


def test_load_corpus_sorted_by_case_id(tmp_path: Path) -> None:
    corpus_dir = tmp_path
    first = _valid_case()
    second = _second_case()
    _register(corpus_dir, first, second)
    _write_case(corpus_dir, first, "steward-155.yaml")
    _write_case(corpus_dir, second, "steward-100.yaml")
    cases = load_corpus(corpus_dir)
    assert [c.case_id for c in cases] == [
        "andrei-shtanakov.steward-100",
        "andrei-shtanakov.steward-155",
    ]


def test_load_corpus_refuses_a_missing_directory(tmp_path: Path) -> None:
    """Каталога нет — это ошибка пути, а не «корпус пуст».

    `glob` по несуществующему пути возвращает пусто, поэтому опечатка в
    `--corpus` читалась как валидный пустой корпус: команды отвечали нулём
    кейсов и кодом 0, то есть «мерить нечего» вместо «вы указали не туда».
    """
    missing = tmp_path / "corups"
    with pytest.raises(CorpusError, match="corups"):
        load_corpus(missing)


def test_load_corpus_refuses_a_file_instead_of_a_directory(tmp_path: Path) -> None:
    """Путь есть, но это файл — тоже не корпус."""
    path = _write_case(tmp_path, _valid_case(), "steward-155.yaml")
    with pytest.raises(CorpusError, match="steward-155.yaml"):
        load_corpus(path)


def test_load_corpus_on_an_empty_directory_is_valid(tmp_path: Path) -> None:
    """Существующий пустой каталог — законный пустой корпус (ещё ничего не разметили)."""
    assert load_corpus(tmp_path) == []


def test_load_corpus_duplicate_case_id_rejected(tmp_path: Path) -> None:
    corpus_dir = tmp_path
    data = _valid_case()
    dupe = copy.deepcopy(data)
    dupe["defects"][0]["id"] = "D-andrei-shtanakov.steward-155-2"
    dupe["non_defects"][0]["id"] = "NF-andrei-shtanakov.steward-155-2"
    _register(corpus_dir, data, dupe)
    _write_case(corpus_dir, data, "a.yaml")
    _write_case(corpus_dir, dupe, "b.yaml")
    with pytest.raises(CorpusError, match="case_id"):
        load_corpus(corpus_dir)


def test_load_corpus_duplicate_defect_id_across_cases_rejected(tmp_path: Path) -> None:
    corpus_dir = tmp_path
    first = _valid_case()
    second = _second_case()
    second["defects"][0]["id"] = first["defects"][0]["id"]  # столкновение id
    _register(corpus_dir, first)
    _write_case(corpus_dir, first, "steward-155.yaml")
    _write_case(corpus_dir, second, "steward-100.yaml")
    with pytest.raises(CorpusError, match="D-andrei-shtanakov.steward-155-1"):
        load_corpus(corpus_dir)


# --------------------------------------------------------------------------
# реестр id (_ids.txt)
# --------------------------------------------------------------------------


def test_registry_path(tmp_path: Path) -> None:
    assert registry_path(tmp_path) == tmp_path / "_ids.txt"


def test_load_corpus_missing_from_registry_is_error(tmp_path: Path) -> None:
    corpus_dir = tmp_path
    _write_case(corpus_dir, _valid_case(), "steward-155.yaml")
    # реестр не создан вовсе
    with pytest.raises(CorpusError, match="D-andrei-shtanakov.steward-155-1"):
        load_corpus(corpus_dir)


def test_check_registry_ok_after_append(tmp_path: Path) -> None:
    corpus_dir = tmp_path
    data = _valid_case()
    case = load_case(_write_case(corpus_dir, data, "steward-155.yaml"))
    append_registry([case], corpus_dir)
    check_registry([case], corpus_dir)  # не бросает


def test_registry_reused_id_with_different_content_is_error(tmp_path: Path) -> None:
    corpus_dir = tmp_path
    data = _valid_case()
    case = load_case(_write_case(corpus_dir, data, "steward-155.yaml"))
    append_registry([case], corpus_dir)

    changed = copy.deepcopy(data)
    changed["defects"][0]["scenario"] = "другой сценарий, тот же id"
    changed_case = load_case(_write_case(corpus_dir, changed, "steward-155.yaml"))
    with pytest.raises(CorpusError, match="D-andrei-shtanakov.steward-155-1"):
        check_registry([changed_case], corpus_dir)


def test_registry_reregistration_appends_a_second_line(tmp_path: Path) -> None:
    """Правка размеченного дефекта не «кирпичит» корпус: реестр last-wins по id.

    `check_registry` требует **явного акта** (сообщение называет `--register`),
    `append_registry` дописывает новую строку тем же id, и после этого корпус
    снова валиден.
    """
    corpus_dir = tmp_path
    data = _valid_case()
    case = load_case(_write_case(corpus_dir, data, "steward-155.yaml"))
    append_registry([case], corpus_dir)

    edited = copy.deepcopy(data)
    edited["defects"][0]["severity"] = "major"
    edited["defects"][0]["match"]["line_window"] = 10
    edited_case = load_case(_write_case(corpus_dir, edited, "steward-155.yaml"))

    with pytest.raises(CorpusError, match="--register"):
        check_registry([edited_case], corpus_dir)

    append_registry([edited_case], corpus_dir)
    check_registry([edited_case], corpus_dir)  # не бросает

    lines = [
        line
        for line in registry_path(corpus_dir).read_text(encoding="utf-8").splitlines()
        if line.startswith("D-andrei-shtanakov.steward-155-1 ")
    ]
    assert len(lines) == 2, "реестр append-only: прежняя строка остаётся рядом с новой"
    assert lines[0] != lines[1]


def _registry_line(corpus_dir: Path, entry_id: str) -> str:
    """Последняя строка реестра про `entry_id`."""
    return [
        line
        for line in registry_path(corpus_dir).read_text(encoding="utf-8").splitlines()
        if line.startswith(f"{entry_id} ")
    ][-1]


def test_registry_line_carries_content_and_identity(tmp_path: Path) -> None:
    """Строка реестра — `<id> <дайджест содержимого> <дайджест идентичности>`."""
    corpus_dir = tmp_path
    case = load_case(_write_case(corpus_dir, _valid_case(), "steward-155.yaml"))
    append_registry([case], corpus_dir)

    parts = _registry_line(corpus_dir, "D-andrei-shtanakov.steward-155-1").split()
    assert len(parts) == 3
    assert all(len(digest) == 64 for digest in parts[1:])
    assert parts[1] != parts[2]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("severity", "blocker"),
        ("line_hint", 700),
        ("evidence", ["scripts/review/local.sh:700"]),
    ],
    ids=["severity", "line_hint", "evidence"],
)
def test_registry_edit_keeping_identity_is_a_reregistration(
    tmp_path: Path, field: str, value: Any
) -> None:
    """Правка вокруг ядра идентичности — обычная перерегистрация (§5)."""
    corpus_dir = tmp_path
    data = _valid_case()
    case = load_case(_write_case(corpus_dir, data, "steward-155.yaml"))
    append_registry([case], corpus_dir)

    edited = copy.deepcopy(data)
    edited["defects"][0][field] = value
    edited_case = load_case(_write_case(corpus_dir, edited, "steward-155.yaml"))

    with pytest.raises(CorpusError, match="--register"):
        check_registry([edited_case], corpus_dir)
    append_registry([edited_case], corpus_dir)
    check_registry([edited_case], corpus_dir)  # не бросает


def test_registry_match_edit_keeping_identity_is_a_reregistration(tmp_path: Path) -> None:
    """Правка `match` (окно, ключевые слова) идентичность не меняет."""
    corpus_dir = tmp_path
    data = _valid_case()
    case = load_case(_write_case(corpus_dir, data, "steward-155.yaml"))
    append_registry([case], corpus_dir)

    edited = copy.deepcopy(data)
    edited["defects"][0]["match"]["line_window"] = 10
    edited["defects"][0]["match"]["keywords_any"] = ["PATH", "kit_dir"]
    edited_case = load_case(_write_case(corpus_dir, edited, "steward-155.yaml"))

    append_registry([edited_case], corpus_dir)
    check_registry([edited_case], corpus_dir)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scenario", "совсем другая история про другое место"),
        ("file", "scripts/review/checksum.sh"),
    ],
    ids=["scenario", "file"],
)
def test_registry_identity_change_needs_an_acknowledgement(
    tmp_path: Path, field: str, value: str
) -> None:
    """Подмена дефекта под живым id — не правка, а другой дефект (§5).

    «Дайджест изменился» покрывает и правку формулировки, и полную замену
    записи: во втором случае gold-набор меняется, а id остаётся, и история
    измерений по этому id становится историей двух разных дефектов. Ядро
    идентичности (`file` + `scenario`) отличает одно от другого.
    """
    corpus_dir = tmp_path
    data = _valid_case()
    case = load_case(_write_case(corpus_dir, data, "steward-155.yaml"))
    append_registry([case], corpus_dir)

    replaced = copy.deepcopy(data)
    replaced["defects"][0][field] = value
    replaced_case = load_case(_write_case(corpus_dir, replaced, "steward-155.yaml"))

    with pytest.raises(CorpusError, match="другой дефект под живым id"):
        check_registry([replaced_case], corpus_dir)
    with pytest.raises(CorpusError, match="--reidentify"):
        append_registry([replaced_case], corpus_dir)
    # Реестр не изменился: отказ до записи.
    assert len(_registry_line(corpus_dir, "D-andrei-shtanakov.steward-155-1").split()) == 3


def test_registry_identity_change_with_an_acknowledgement_is_recorded(tmp_path: Path) -> None:
    """С подтверждением смена идентичности проходит и **остаётся в файле**."""
    corpus_dir = tmp_path
    data = _valid_case()
    case = load_case(_write_case(corpus_dir, data, "steward-155.yaml"))
    append_registry([case], corpus_dir)

    replaced = copy.deepcopy(data)
    replaced["defects"][0]["scenario"] = "другой дефект, тот же id по решению разметчика"
    replaced_case = load_case(_write_case(corpus_dir, replaced, "steward-155.yaml"))

    append_registry(
        [replaced_case], corpus_dir, reidentify=frozenset({"D-andrei-shtanakov.steward-155-1"})
    )

    line = _registry_line(corpus_dir, "D-andrei-shtanakov.steward-155-1").split()
    assert len(line) == 4
    assert line[3] == "reidentified"
    check_registry([replaced_case], corpus_dir)  # не бросает


def _handwritten_registry(corpus_dir: Path, case: Case, *defect_lines: str) -> None:
    """Реестр, собранный руками: строка non-defect как есть, строки дефекта — свои."""
    nf_line = _registry_line(corpus_dir, "NF-andrei-shtanakov.steward-155-1")
    registry_path(corpus_dir).write_text(
        "\n".join([nf_line, *defect_lines]) + "\n", encoding="utf-8"
    )


def _registered_case(corpus_dir: Path) -> tuple[Case, str, str]:
    """Зарегистрированный кейс и настоящие дайджесты его дефекта."""
    case = load_case(_write_case(corpus_dir, _valid_case(), "steward-155.yaml"))
    append_registry([case], corpus_dir)
    parts = _registry_line(corpus_dir, "D-andrei-shtanakov.steward-155-1").split()
    return case, parts[1], parts[2]


def test_handwritten_identity_change_without_the_marker_is_refused(tmp_path: Path) -> None:
    """Смена ядра строкой из трёх полей — обход `--reidentify`, и он не проходит.

    `append_registry` без `--reidentify` смену ядра отвергает, но реестр —
    текстовый файл: дописать третьим полем другое ядро можно руками, и
    состояние last-wins принимало новую идентичность как текущую. После этого
    `check_registry` сверял кейс с **уже подменённым** ядром и молчал: подмена
    дефекта под живым id становилась законной правкой файла на одну строку.
    """
    corpus_dir = tmp_path
    case, content, identity = _registered_case(corpus_dir)
    _handwritten_registry(
        corpus_dir,
        case,
        f"D-andrei-shtanakov.steward-155-1 {content} {'e' * 64}",
        f"D-andrei-shtanakov.steward-155-1 {content} {identity}",
    )

    with pytest.raises(CorpusError, match="меняет идентичность.*без подтверждения reidentified"):
        check_registry([case], corpus_dir)
    with pytest.raises(CorpusError, match="строка 3"):
        check_registry([case], corpus_dir)


def test_handwritten_identity_change_with_the_marker_is_accepted(tmp_path: Path) -> None:
    """Та же смена с меткой `reidentified` — объявленное решение разметчика."""
    corpus_dir = tmp_path
    case, content, identity = _registered_case(corpus_dir)
    _handwritten_registry(
        corpus_dir,
        case,
        f"D-andrei-shtanakov.steward-155-1 {content} {'e' * 64}",
        f"D-andrei-shtanakov.steward-155-1 {content} {identity} reidentified",
    )

    check_registry([case], corpus_dir)  # не бросает


def test_handwritten_format_downgrade_is_refused(tmp_path: Path) -> None:
    """Двухполевая строка после трёхполевой — «забыть» ядро, чтобы обойти метку.

    Обход в два шага: сначала строка старого формата стирает известное ядро,
    затем трёхполевая с новым ядром проходит по правилу «прежнее ядро
    неизвестно». Понижение формата у живого id с известным ядром запрещено.
    """
    corpus_dir = tmp_path
    case, content, identity = _registered_case(corpus_dir)
    _handwritten_registry(
        corpus_dir,
        case,
        f"D-andrei-shtanakov.steward-155-1 {content} {'e' * 64}",
        f"D-andrei-shtanakov.steward-155-1 {content}",
        f"D-andrei-shtanakov.steward-155-1 {content} {identity}",
    )

    with pytest.raises(CorpusError, match="строка 3.*понижение формата"):
        check_registry([case], corpus_dir)


def test_handwritten_legacy_upgrade_with_changed_content_needs_the_marker(
    tmp_path: Path,
) -> None:
    """Legacy-строка → трёхполевая с другим содержимым и ядром — это смена дефекта.

    `append_registry` на таком переходе требует `--reidentify` (раунд 5);
    рукописная трёхполевая строка обходила бы это: прежнее ядро неизвестно,
    сравнивать «не с чем» — и новое ядро становилось текущим молча.
    Прежний content — единственный след прежней записи, и он изменился.
    """
    corpus_dir = tmp_path
    case, content, identity = _registered_case(corpus_dir)
    _handwritten_registry(
        corpus_dir,
        case,
        f"D-andrei-shtanakov.steward-155-1 {'c' * 64}",
        f"D-andrei-shtanakov.steward-155-1 {content} {identity}",
    )

    with pytest.raises(CorpusError, match="строка 3.*без подтверждения reidentified"):
        check_registry([case], corpus_dir)


def test_handwritten_legacy_to_legacy_with_changed_content_is_refused(tmp_path: Path) -> None:
    """Двухполевая строка с другим content после двухполевой — та же подмена.

    `append_registry` двухполевых строк не пишет никогда, а рукописная
    проходила: ядро неизвестно с обеих сторон, а `check_registry` сравнивал
    только content, который как раз и совпал с подменённой записью.
    """
    corpus_dir = tmp_path
    case, content, _identity = _registered_case(corpus_dir)
    _handwritten_registry(
        corpus_dir,
        case,
        f"D-andrei-shtanakov.steward-155-1 {'c' * 64}",
        f"D-andrei-shtanakov.steward-155-1 {content}",
    )

    with pytest.raises(CorpusError, match="строка 3.*без подтверждения reidentified"):
        check_registry([case], corpus_dir)


def test_handwritten_legacy_upgrade_with_same_content_needs_no_marker(tmp_path: Path) -> None:
    """Тот же content, дописано ядро — штатное дополнение legacy-строки."""
    corpus_dir = tmp_path
    case, content, identity = _registered_case(corpus_dir)
    _handwritten_registry(
        corpus_dir,
        case,
        f"D-andrei-shtanakov.steward-155-1 {content}",
        f"D-andrei-shtanakov.steward-155-1 {content} {identity}",
    )

    check_registry([case], corpus_dir)  # не бросает


def test_handwritten_content_change_needs_no_marker(tmp_path: Path) -> None:
    """Смена только содержимого при том же ядре — законная перерегистрация.

    Метку требует смена **ядра**: правка живой записи (severity, `match`,
    `line_window`) ядра не меняет, и требовать на неё `reidentified` значило бы
    обесценить метку.
    """
    corpus_dir = tmp_path
    case, content, identity = _registered_case(corpus_dir)
    _handwritten_registry(
        corpus_dir,
        case,
        f"D-andrei-shtanakov.steward-155-1 {'d' * 64} {identity}",
        f"D-andrei-shtanakov.steward-155-1 {content} {identity}",
    )

    check_registry([case], corpus_dir)  # не бросает


def test_registry_identity_ignores_path_respelling(tmp_path: Path) -> None:
    """`./a.sh` и `a.sh` — один и тот же файл: переписывание пути не смена дефекта."""
    corpus_dir = tmp_path
    data = _valid_case()
    case = load_case(_write_case(corpus_dir, data, "steward-155.yaml"))
    append_registry([case], corpus_dir)

    respelled = copy.deepcopy(data)
    respelled["defects"][0]["file"] = "./scripts//review/local.sh"
    respelled_case = load_case(_write_case(corpus_dir, respelled, "steward-155.yaml"))

    # Содержимое изменилось (перерегистрация), идентичность — нет.
    append_registry([respelled_case], corpus_dir)
    check_registry([respelled_case], corpus_dir)


def test_registry_upgrades_a_legacy_two_field_line(tmp_path: Path) -> None:
    """Строка старого формата (без идентичности) валидна и дописывается при `--register`.

    Реестры, заведённые до ядра идентичности, не объявляются повреждёнными:
    идентичность у них неизвестна, проверяется только содержимое, а первая же
    регистрация её фиксирует — и с этого момента подмена ловится.
    """
    corpus_dir = tmp_path
    case = load_case(_write_case(corpus_dir, _valid_case(), "steward-155.yaml"))
    append_registry([case], corpus_dir)
    legacy = "\n".join(
        " ".join(line.split()[:2])
        for line in registry_path(corpus_dir).read_text(encoding="utf-8").splitlines()
    )
    registry_path(corpus_dir).write_text(legacy + "\n", encoding="utf-8")

    check_registry([case], corpus_dir)  # старый формат валиден
    append_registry([case], corpus_dir)
    assert len(_registry_line(corpus_dir, "D-andrei-shtanakov.steward-155-1").split()) == 3

    replaced = _valid_case()
    replaced["defects"][0]["scenario"] = "другой дефект под тем же id"
    replaced_case = load_case(_write_case(corpus_dir, replaced, "steward-155.yaml"))
    with pytest.raises(CorpusError, match="другой дефект под живым id"):
        check_registry([replaced_case], corpus_dir)


def test_registry_reregistration_is_idempotent_after_the_edit(tmp_path: Path) -> None:
    """Повторный `--register` после перерегистрации ничего не дописывает."""
    corpus_dir = tmp_path
    data = _valid_case()
    case = load_case(_write_case(corpus_dir, data, "steward-155.yaml"))
    append_registry([case], corpus_dir)
    edited = copy.deepcopy(data)
    edited["defects"][0]["line_hint"] = 700
    edited_case = load_case(_write_case(corpus_dir, edited, "steward-155.yaml"))
    append_registry([edited_case], corpus_dir)

    before = registry_path(corpus_dir).read_text(encoding="utf-8")
    append_registry([edited_case], corpus_dir)

    assert registry_path(corpus_dir).read_text(encoding="utf-8") == before


def test_live_id_without_a_case_is_refused(tmp_path: Path) -> None:
    """Удалить YAML кейса и ничего не списать — нарушение: id остаётся живым.

    Проверка шла только в одну сторону («у каждого id кейса есть строка в
    реестре»), поэтому удаление файла оставляло id зарегистрированным и
    свободным одновременно: он никому не принадлежал, а вернуться мог с тем же
    содержимым — и все проверки молчали.
    """
    corpus_dir = tmp_path
    first = load_case(_write_case(corpus_dir, _valid_case(), "steward-155.yaml"))
    second = load_case(_write_case(corpus_dir, _second_case(), "steward-100.yaml"))
    append_registry([first, second], corpus_dir)

    (corpus_dir / "steward-100.yaml").unlink()

    with pytest.raises(CorpusError, match="зарегистрирован, но кейса с ним нет") as excinfo:
        load_corpus(corpus_dir)
    assert "D-andrei-shtanakov.steward-100-1" in str(excinfo.value)
    assert "--retire-deleted" in str(excinfo.value)


def test_corpus_validates_after_the_deleted_ids_are_retired(tmp_path: Path) -> None:
    """Списание закрывает вопрос: тот же корпус снова валиден."""
    corpus_dir = tmp_path
    first = load_case(_write_case(corpus_dir, _valid_case(), "steward-155.yaml"))
    second = load_case(_write_case(corpus_dir, _second_case(), "steward-100.yaml"))
    append_registry([first, second], corpus_dir)
    (corpus_dir / "steward-100.yaml").unlink()

    append_registry([first], corpus_dir, retire_deleted=True)

    assert [case.case_id for case in load_corpus(corpus_dir)] == ["andrei-shtanakov.steward-155"]


def _register_current(corpus_dir: Path, *remaining: Case, retire: bool = True) -> list[str]:
    """Смоделировать `corpus validate --register [--retire-deleted]` над корпусом."""
    return append_registry(remaining, corpus_dir, retire_deleted=retire)


def _two_case_corpus(corpus_dir: Path) -> tuple[Case, Case]:
    """Корпус из двух кейсов, оба зарегистрированы."""
    first = load_case(_write_case(corpus_dir, _valid_case(), "steward-155.yaml"))
    second = load_case(_write_case(corpus_dir, _second_case(), "steward-100.yaml"))
    append_registry([first, second], corpus_dir)
    return first, second


def test_registry_tombstones_an_id_removed_from_the_corpus(tmp_path: Path) -> None:
    """Удалённый из корпуса id списывается строкой `<id> deleted` (§5) — по флагу."""
    corpus_dir = tmp_path
    first, _second = _two_case_corpus(corpus_dir)

    (corpus_dir / "steward-100.yaml").unlink()
    retired = _register_current(corpus_dir, first)

    assert retired == ["D-andrei-shtanakov.steward-100-1", "NF-andrei-shtanakov.steward-100-1"]
    lines = registry_path(corpus_dir).read_text(encoding="utf-8").splitlines()
    assert "D-andrei-shtanakov.steward-100-1 deleted" in lines
    assert "NF-andrei-shtanakov.steward-100-1 deleted" in lines
    # Оставшийся кейс не задет: его id живы.
    check_registry([first], corpus_dir)


def test_registry_does_not_retire_without_the_flag(tmp_path: Path) -> None:
    """Без `retire_deleted` списания нет — только список того, что списалось бы.

    Списание необратимо, поэтому оно не может быть побочным эффектом обычной
    регистрации: неверный `--corpus` (или неполное рабочее дерево) иначе
    обнулял бы реестр молча.
    """
    corpus_dir = tmp_path
    first, _second = _two_case_corpus(corpus_dir)
    before = registry_path(corpus_dir).read_text(encoding="utf-8")

    (corpus_dir / "steward-100.yaml").unlink()
    would_retire = _register_current(corpus_dir, first, retire=False)

    assert would_retire == ["D-andrei-shtanakov.steward-100-1", "NF-andrei-shtanakov.steward-100-1"]
    assert registry_path(corpus_dir).read_text(encoding="utf-8") == before
    assert "deleted" not in before


def test_append_registry_refuses_to_retire_everything_on_an_empty_corpus(
    tmp_path: Path,
) -> None:
    """Пустой корпус при непустом реестре — почти наверняка неверный путь.

    Это состояние `eval/corpus/` в ветке-фундаменте (реестр есть, YAML-ы
    приходят другой частью сплита): списание всех id обнулило бы ground truth
    необратимо, поэтому отказ, а не «ничего не нашли — значит всё удалено».
    """
    corpus_dir = tmp_path
    _two_case_corpus(corpus_dir)
    before = registry_path(corpus_dir).read_text(encoding="utf-8")

    for retire in (True, False):
        with pytest.raises(CorpusError, match="корпус пуст") as excinfo:
            append_registry([], corpus_dir, retire_deleted=retire)
        # Совет в отказе обязан быть выполнимым: удаление файла сторож истории
        # отвергает, поэтому предлагается ручное надгробие, а не удаление.
        assert "deleted" in str(excinfo.value)
        assert "удалите реестр" not in str(excinfo.value)

    assert registry_path(corpus_dir).read_text(encoding="utf-8") == before


def test_append_registry_on_an_empty_corpus_without_a_registry_is_fine(tmp_path: Path) -> None:
    """Пустой каталог без реестра — не ошибка: списывать нечего."""
    assert append_registry([], tmp_path, retire_deleted=True) == []
    assert not registry_path(tmp_path).exists()


def test_append_registry_on_an_empty_corpus_with_only_tombstones_is_fine(
    tmp_path: Path,
) -> None:
    """Реестр из одних надгробий — живых id нет, отказывать не за что.

    Через сам `append_registry` в это состояние не попасть: списать **последний**
    живой id нельзя (для этого пришлось бы передать пустой корпус, а это отказ).
    Поэтому реестр здесь выложен файлом — он и есть формат.
    """
    registry_path(tmp_path).write_text(
        "D-andrei-shtanakov.steward-155-1 deleted\nNF-andrei-shtanakov.steward-155-1 deleted\n",
        encoding="utf-8",
    )

    assert append_registry([], tmp_path, retire_deleted=True) == []


def test_registry_refuses_a_different_defect_under_a_retired_id(tmp_path: Path) -> None:
    """Списанный id не достаётся другому дефекту — правило §5 «id не переиспользуется».

    Реестр last-wins по дайджесту не отличал «правку живой записи» от «id
    вернулся под другим дефектом»: достаточно было удалить дефект, дать новому
    его id и перерегистрировать. Надгробие делает разницу видимой файлу.
    """
    corpus_dir = tmp_path
    first, _second = _two_case_corpus(corpus_dir)
    (corpus_dir / "steward-100.yaml").unlink()
    _register_current(corpus_dir, first)

    другой = _second_case()
    другой["defects"][0]["scenario"] = "совсем другой дефект под тем же id"
    другой["defects"][0]["file"] = "scripts/review/checksum.sh"
    _write_case(corpus_dir, другой, "steward-100.yaml")

    with pytest.raises(CorpusError, match="списан"):
        load_corpus(corpus_dir)


def test_registry_refuses_the_same_defect_under_a_retired_id(tmp_path: Path) -> None:
    """Даже дословный возврат удалённой записи запрещён: id уже израсходован.

    Иначе правило зависело бы от содержимого, а оно может совпасть случайно —
    и «тот же дефект» по дайджесту неотличим от «другой дефект, похожий текст».
    """
    corpus_dir = tmp_path
    first, second = _two_case_corpus(corpus_dir)
    (corpus_dir / "steward-100.yaml").unlink()
    _register_current(corpus_dir, first)

    _write_case(corpus_dir, _second_case(), "steward-100.yaml")
    with pytest.raises(CorpusError, match="списан"):
        load_corpus(corpus_dir)

    # `--register` списанный id не возвращает: отказ остаётся отказом.
    _register_current(corpus_dir, first, second)
    with pytest.raises(CorpusError, match="списан"):
        load_corpus(corpus_dir)


def test_registry_line_after_a_tombstone_makes_the_registry_invalid(tmp_path: Path) -> None:
    """Надгробие **терминально**: строка после него — повреждённый реестр.

    Иначе запрет обходился бы правкой файла на одну строку: состояние
    last-wins воскрешало id, а `check_registry` смотрит только на последнее
    состояние. Терминальность делает воскрешение не «другим состоянием», а
    нарушением формата — и говорит это строкой файла.
    """
    corpus_dir = tmp_path
    case = load_case(_write_case(corpus_dir, _valid_case(), "steward-155.yaml"))
    append_registry([case], corpus_dir)
    lines = registry_path(corpus_dir).read_text(encoding="utf-8").splitlines()
    digest_line = next(
        item for item in lines if item.startswith("D-andrei-shtanakov.steward-155-1 ")
    )
    resurrected = [*lines, "D-andrei-shtanakov.steward-155-1 deleted", digest_line]
    registry_path(corpus_dir).write_text("\n".join(resurrected) + "\n", encoding="utf-8")

    with pytest.raises(CorpusError, match="после надгробия") as excinfo:
        load_corpus(corpus_dir)
    assert "D-andrei-shtanakov.steward-155-1" in str(excinfo.value)
    assert f"строка {len(resurrected)}" in str(excinfo.value)


@pytest.mark.parametrize(
    "line",
    [
        "D-andrei-shtanakov.steward-999-1 not-a-digest also-not-a-digest",
        "D-andrei-shtanakov.steward-999-1 " + "A" * 64,
        "D-andrei-shtanakov.steward-999-1 " + "a" * 63,
        "defect-1 " + "a" * 64,
        "D-andrei-shtanakov.steward-999-1 " + "a" * 64 + " " + "b" * 64 + " retired",
        "D-andrei-shtanakov.steward-999-1 " + "a" * 64 + " " + "b" * 64 + " reidentified extra",
        "D-andrei-shtanakov.steward-999-1 removed",
        "D-andrei-shtanakov.steward-999-1",
    ],
    ids=[
        "garbage-digests",
        "uppercase-digest",
        "short-digest",
        "bad-id",
        "unknown-marker",
        "too-many-fields",
        "unknown-two-field-marker",
        "one-field-only",
    ],
)
def test_registry_syntax_is_checked_on_every_line(tmp_path: Path, line: str) -> None:
    """Синтаксис реестра проверяется у **каждой** строки, а не только у нужных.

    Прежде строка про id, которого в корпусе нет, не сверялась ни с чем:
    мусор жил в файле, пока кто-нибудь не завёл такой id, — и тогда падало
    далеко от причины. Реестр либо целиком годен, либо нет.
    """
    corpus_dir = tmp_path
    case = load_case(_write_case(corpus_dir, _valid_case(), "steward-155.yaml"))
    append_registry([case], corpus_dir)
    written = len(registry_path(corpus_dir).read_text(encoding="utf-8").splitlines())
    with registry_path(corpus_dir).open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(corpus_dir)
    assert f"строка {written + 1}" in str(excinfo.value), str(excinfo.value)


def test_legacy_two_field_line_counts_as_live(tmp_path: Path) -> None:
    """Строка старого формата — тоже живой id: без кейса она нарушение.

    Синтаксис у неё законный (ядро идентичности неизвестно, и это допустимо),
    но «зарегистрирован» она значит наравне с трёхполевой: иначе достаточно
    было бы оставить legacy-строку, чтобы id висел живым без кейса.
    """
    corpus_dir = tmp_path
    case = load_case(_write_case(corpus_dir, _valid_case(), "steward-155.yaml"))
    append_registry([case], corpus_dir)
    with registry_path(corpus_dir).open("a", encoding="utf-8") as handle:
        handle.write("D-andrei-shtanakov.steward-999-1 " + "a" * 64 + "\n")

    with pytest.raises(CorpusError, match="зарегистрирован, но кейса с ним нет"):
        load_corpus(corpus_dir)


def _legacy_registry(corpus_dir: Path, case: Case) -> None:
    """Реестр, записанный до ядра идентичности: строки из двух полей."""
    append_registry([case], corpus_dir)
    legacy = "\n".join(
        " ".join(line.split()[:2])
        for line in registry_path(corpus_dir).read_text(encoding="utf-8").splitlines()
    )
    registry_path(corpus_dir).write_text(legacy + "\n", encoding="utf-8")


def test_legacy_line_with_changed_content_needs_an_acknowledgement(tmp_path: Path) -> None:
    """У строки старого формата ядро неизвестно — «та же ли запись» проверить нечем.

    Тихое дописывание ядра при изменившемся содержимом делало из строки
    старого формата дыру: подмену дефекта под живым id она пропускала без
    `--reidentify`, потому что сравнивать было не с чем. Правильный ответ —
    не «поверим», а «подтвердите».
    """
    corpus_dir = tmp_path
    data = _valid_case()
    case = load_case(_write_case(corpus_dir, data, "steward-155.yaml"))
    _legacy_registry(corpus_dir, case)

    changed = copy.deepcopy(data)
    changed["defects"][0]["scenario"] = "другой дефект под тем же id"
    changed_case = load_case(_write_case(corpus_dir, changed, "steward-155.yaml"))

    with pytest.raises(CorpusError, match="--reidentify"):
        check_registry([changed_case], corpus_dir)
    with pytest.raises(CorpusError, match="--reidentify"):
        append_registry([changed_case], corpus_dir)
    assert "reidentified" not in registry_path(corpus_dir).read_text(encoding="utf-8")


def test_legacy_line_with_changed_content_and_ack_is_recorded(tmp_path: Path) -> None:
    """С подтверждением строка старого формата обновляется — с меткой."""
    corpus_dir = tmp_path
    data = _valid_case()
    case = load_case(_write_case(corpus_dir, data, "steward-155.yaml"))
    _legacy_registry(corpus_dir, case)

    changed = copy.deepcopy(data)
    changed["defects"][0]["severity"] = "blocker"
    changed_case = load_case(_write_case(corpus_dir, changed, "steward-155.yaml"))

    append_registry(
        [changed_case], corpus_dir, reidentify=frozenset({"D-andrei-shtanakov.steward-155-1"})
    )

    assert (
        _registry_line(corpus_dir, "D-andrei-shtanakov.steward-155-1").split()[3] == "reidentified"
    )
    check_registry([changed_case], corpus_dir)  # не бросает


def test_registry_tolerates_a_repeated_tombstone(tmp_path: Path) -> None:
    """Два надгробия подряд — идемпотентность, а не порча: id уже списан."""
    corpus_dir = tmp_path
    case = load_case(_write_case(corpus_dir, _valid_case(), "steward-155.yaml"))
    append_registry([case], corpus_dir)
    with registry_path(corpus_dir).open("a", encoding="utf-8") as handle:
        handle.write(
            "D-andrei-shtanakov.steward-100-1 deleted\nD-andrei-shtanakov.steward-100-1 deleted\n"
        )

    # Живой кейс валиден; повторное надгробие ушедшего id не мешает.
    assert [item.case_id for item in load_corpus(corpus_dir)] == ["andrei-shtanakov.steward-155"]


def test_append_registry_never_revives_a_tombstoned_id(tmp_path: Path) -> None:
    """`--register` не дописывает дайджест списанному id — иначе он ломал бы файл.

    После правки «надгробие терминально» такая строка сделала бы реестр
    невалидным, а не просто обошла запрет: `append_registry` обязана её не
    писать, и это проверяется на файле, а не только по отказу проверки.
    """
    corpus_dir = tmp_path
    first, _second = _two_case_corpus(corpus_dir)
    (corpus_dir / "steward-100.yaml").unlink()
    _register_current(corpus_dir, first)

    second = load_case(_write_case(corpus_dir, _second_case(), "steward-100.yaml"))
    assert append_registry([first, second], corpus_dir, retire_deleted=True) == []

    lines = registry_path(corpus_dir).read_text(encoding="utf-8").splitlines()
    after_tombstone = lines[lines.index("D-andrei-shtanakov.steward-100-1 deleted") + 1 :]
    assert not any(item.startswith("D-andrei-shtanakov.steward-100-1 ") for item in after_tombstone)
    # Реестр остался читаемым (терминальность не нарушена).
    with pytest.raises(CorpusError, match="списан"):
        load_corpus(corpus_dir)


def test_registry_tombstone_is_written_once(tmp_path: Path) -> None:
    """Повторный `--register --retire-deleted` не копит надгробия."""
    corpus_dir = tmp_path
    first, _second = _two_case_corpus(corpus_dir)
    (corpus_dir / "steward-100.yaml").unlink()

    assert _register_current(corpus_dir, first) == [
        "D-andrei-shtanakov.steward-100-1",
        "NF-andrei-shtanakov.steward-100-1",
    ]
    once = registry_path(corpus_dir).read_text(encoding="utf-8")
    assert _register_current(corpus_dir, first) == []
    twice = registry_path(corpus_dir).read_text(encoding="utf-8")

    assert once == twice
    assert once.count("D-andrei-shtanakov.steward-100-1 deleted") == 1


def test_append_registry_is_idempotent(tmp_path: Path) -> None:
    corpus_dir = tmp_path
    data = _valid_case()
    case = load_case(_write_case(corpus_dir, data, "steward-155.yaml"))
    append_registry([case], corpus_dir)
    before = registry_path(corpus_dir).read_text(encoding="utf-8")
    append_registry([case], corpus_dir)
    after = registry_path(corpus_dir).read_text(encoding="utf-8")
    assert before == after


def test_load_corpus_end_to_end_with_registration(tmp_path: Path) -> None:
    corpus_dir = tmp_path
    first = _valid_case()
    second = _second_case()
    case1 = load_case(_write_case(corpus_dir, first, "steward-155.yaml"))
    case2 = load_case(_write_case(corpus_dir, second, "steward-100.yaml"))
    append_registry([case1, case2], corpus_dir)
    cases = load_corpus(corpus_dir)
    assert [c.case_id for c in cases] == [
        "andrei-shtanakov.steward-100",
        "andrei-shtanakov.steward-155",
    ]


# --------------------------------------------------------------------------
# реестр против HEAD: append-only проверяется git-ом, а не только доверием
# --------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    """git в каталоге теста; падение — сразу, это фикстура, а не предмет проверки."""
    import subprocess

    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout


def _tracked_corpus(tmp_path: Path) -> tuple[Path, Case]:
    """Корпус в git-репозитории с закоммиченными кейсом и реестром."""
    corpus_dir = tmp_path / "eval" / "corpus"
    corpus_dir.mkdir(parents=True)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    case = load_case(_write_case(corpus_dir, _valid_case(), "steward-155.yaml"))
    append_registry([case], corpus_dir)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "корпус и реестр")
    return corpus_dir, case


def test_registry_may_grow_beyond_head(tmp_path: Path) -> None:
    """Дописанные строки — норма: реестр append-only, а не неизменяемый."""
    corpus_dir, case = _tracked_corpus(tmp_path)
    second = _second_case()
    load_case(_write_case(corpus_dir, second, "steward-100.yaml"))

    append_registry([case, load_case(corpus_dir / "steward-100.yaml")], corpus_dir)

    assert [item.case_id for item in load_corpus(corpus_dir)] == [
        "andrei-shtanakov.steward-100",
        "andrei-shtanakov.steward-155",
    ]


def test_registry_with_a_deleted_committed_line_is_refused(tmp_path: Path) -> None:
    """Удалённая историческая строка — не append-only: id можно было бы вернуть.

    Файл сам себе не доказательство: гарантию даёт git (и ревью PR), поэтому
    рабочий файл обязан быть продолжением закоммиченного. Иначе достаточно
    стереть строку — и списанный или занятый id снова «свободен».
    """
    corpus_dir, _case = _tracked_corpus(tmp_path)
    lines = registry_path(corpus_dir).read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if not line.startswith("D-andrei-shtanakov.steward-155-1 ")]
    registry_path(corpus_dir).write_text("\n".join(kept) + "\n", encoding="utf-8")

    with pytest.raises(CorpusError, match="append-only"):
        load_corpus(corpus_dir)


def test_registry_with_an_altered_committed_line_is_refused(tmp_path: Path) -> None:
    """Подменённая историческая строка — тоже не append-only."""
    corpus_dir, _case = _tracked_corpus(tmp_path)
    text = registry_path(corpus_dir).read_text(encoding="utf-8")
    registry_path(corpus_dir).write_text(
        text.replace("D-andrei-shtanakov.steward-155-1", "D-andrei-shtanakov.steward-155-9"),
        "utf-8",
    )

    with pytest.raises(CorpusError, match="append-only"):
        load_corpus(corpus_dir)


def test_untracked_registry_is_not_checked_against_head(tmp_path: Path) -> None:
    """Реестр вне git — проверять нечем: правило best-effort, а не запрет."""
    corpus_dir = tmp_path / "eval" / "corpus"
    corpus_dir.mkdir(parents=True)
    _git(tmp_path, "init", "-q")
    case = load_case(_write_case(corpus_dir, _valid_case(), "steward-155.yaml"))
    append_registry([case], corpus_dir)  # не коммитим

    assert [item.case_id for item in load_corpus(corpus_dir)] == ["andrei-shtanakov.steward-155"]


def test_registry_outside_a_git_repo_is_not_checked(tmp_path: Path) -> None:
    """Не git-репозиторий вовсе — тот же best-effort (и так живут tmp-корпуса)."""
    case = load_case(_write_case(tmp_path, _valid_case(), "steward-155.yaml"))
    append_registry([case], tmp_path)

    assert [item.case_id for item in load_corpus(tmp_path)] == ["andrei-shtanakov.steward-155"]


def test_registry_with_a_line_deleted_by_a_commit_is_refused(tmp_path: Path) -> None:
    """Удалить строку **коммитом** — тот же обход, и он тоже не проходит.

    Сверка только с `HEAD` ловила правку рабочего файла, но не коммит, который
    строку убрал: на чистом чекауте такой реестр выглядел безупречно, а
    списанный id снова был свободен. Цепочку держит вся история файла, а не её
    последняя точка.
    """
    corpus_dir, first = _tracked_corpus(tmp_path)
    second = load_case(_write_case(corpus_dir, _second_case(), "steward-100.yaml"))
    append_registry([first, second], corpus_dir)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "второй кейс")
    parent = _git(tmp_path, "rev-parse", "HEAD").strip()

    # Коммит, удаляющий строки первого кейса: рабочее дерево чистое.
    lines = registry_path(corpus_dir).read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if "andrei-shtanakov.steward-155" not in line]
    registry_path(corpus_dir).write_text("\n".join(kept) + "\n", encoding="utf-8")
    (corpus_dir / "steward-155.yaml").unlink()
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "стёр историю реестра")
    child = _git(tmp_path, "rev-parse", "HEAD").strip()

    with pytest.raises(CorpusError, match="append-only") as excinfo:
        load_corpus(corpus_dir)
    message = str(excinfo.value)
    # Сообщение называет пару «коммит не продолжает родителя»: виновник и та
    # версия, которую он перестал продолжать.
    assert child[:12] in message, message
    assert parent[:12] in message, message
    assert "D-andrei-shtanakov.steward-155-1" in message


def test_registry_append_after_two_commits_is_fine(tmp_path: Path) -> None:
    """Честная доливка поверх двух коммитов проходит: продолжение, не правка."""
    corpus_dir, first = _tracked_corpus(tmp_path)
    second = load_case(_write_case(corpus_dir, _second_case(), "steward-100.yaml"))
    append_registry([first, second], corpus_dir)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "второй кейс")

    third = _valid_case(case_id="andrei-shtanakov.steward-161", pr=161)
    third["defects"][0]["id"] = "D-andrei-shtanakov.steward-161-1"
    third["non_defects"][0]["id"] = "NF-andrei-shtanakov.steward-161-1"
    load_case(_write_case(corpus_dir, third, "steward-161.yaml"))
    append_registry([first, second, load_case(corpus_dir / "steward-161.yaml")], corpus_dir)

    assert [item.case_id for item in load_corpus(corpus_dir)] == [
        "andrei-shtanakov.steward-100",
        "andrei-shtanakov.steward-155",
        "andrei-shtanakov.steward-161",
    ]


def _registry_commit(tmp_path: Path, corpus_dir: Path, lines: list[str], message: str) -> str:
    """Записать реестр перечисленными строками и закоммитить; вернуть sha."""
    registry_path(corpus_dir).write_text("\n".join(lines) + "\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", message)
    return _git(tmp_path, "rev-parse", "HEAD").strip()


def test_registry_delete_then_restore_is_refused(tmp_path: Path) -> None:
    """Удалить строку коммитом и вернуть её следующим — всё равно не append-only.

    Сверка каждой версии с **рабочим файлом** такую историю пропускала: в
    рабочем файле строка снова есть, и каждый снимок — её префикс. Но между
    коммитами id был свободен: списанный или занятый, он в тот момент никому не
    принадлежал, и «вернули дословно» проверить нечем — вернуть могли и другую
    запись с тем же текстом. Монотонность обязана держаться **по родителям**.
    """
    corpus_dir = _fresh_repo(tmp_path)
    line_x = "D-andrei-shtanakov.steward-155-1 " + "a" * 64
    line_y = "D-andrei-shtanakov.steward-100-1 " + "b" * 64

    parent = _registry_commit(tmp_path, corpus_dir, [line_x, line_y], "A: X и Y")
    child = _registry_commit(tmp_path, corpus_dir, [line_x], "B: Y удалён")
    _registry_commit(tmp_path, corpus_dir, [line_x, line_y], "C: Y возвращён дословно")

    with pytest.raises(CorpusError, match="append-only") as excinfo:
        load_corpus(corpus_dir)
    message = str(excinfo.value)
    assert child[:12] in message, message
    assert parent[:12] in message, message
    assert "D-andrei-shtanakov.steward-100-1" in message


def test_registry_linear_appends_over_three_commits_pass(tmp_path: Path) -> None:
    """Честная история из трёх коммитов-дописываний проходит."""
    corpus_dir = tmp_path / "eval" / "corpus"
    corpus_dir.mkdir(parents=True)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    # Строки — надгробия: кейсов в этом фикстурном корпусе нет, а живой id без
    # кейса теперь сам по себе нарушение (проверяется отдельными тестами).
    lines = ["D-andrei-shtanakov.steward-155-1 deleted"]
    _registry_commit(tmp_path, corpus_dir, lines, "A")
    lines.append("D-andrei-shtanakov.steward-100-1 deleted")
    _registry_commit(tmp_path, corpus_dir, lines, "B")
    lines.append("D-andrei-shtanakov.steward-161-1 deleted")
    _registry_commit(tmp_path, corpus_dir, lines, "C")

    assert load_corpus(corpus_dir) == []


def test_registry_first_commit_of_the_file_passes(tmp_path: Path) -> None:
    """Коммит, впервые добавляющий реестр, родителя с файлом не имеет — не нарушение."""
    corpus_dir = tmp_path / "eval" / "corpus"
    corpus_dir.mkdir(parents=True)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README.md").write_text("до реестра\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "коммит без реестра")

    _registry_commit(
        tmp_path, corpus_dir, ["D-andrei-shtanakov.steward-155-1 deleted"], "реестр появился"
    )

    assert load_corpus(corpus_dir) == []


def _fresh_repo(tmp_path: Path) -> Path:
    """Пустой git-репозиторий с каталогом корпуса внутри."""
    corpus_dir = tmp_path / "eval" / "corpus"
    corpus_dir.mkdir(parents=True)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    return corpus_dir


def test_registry_deleted_by_a_commit_is_refused(tmp_path: Path) -> None:
    """Удалить файл реестра целиком — то же нарушение, что стереть строку.

    Обход по родителям пропускал коммит, в котором файла нет (`show` там
    отказывает), и пересоздание после такого коммита тоже: id-пространство
    обнулялось целиком, а история выглядела как «файл появился впервые».
    """
    corpus_dir = _fresh_repo(tmp_path)
    line_x = "D-andrei-shtanakov.steward-155-1 " + "a" * 64
    _registry_commit(tmp_path, corpus_dir, [line_x], "A: реестр с X")

    registry_path(corpus_dir).unlink()
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "B: реестр удалён")
    deleted_in = _git(tmp_path, "rev-parse", "HEAD").strip()

    # C пересоздаёт файл — тот же id, но уже под другой записью.
    _registry_commit(
        tmp_path, corpus_dir, ["D-andrei-shtanakov.steward-155-1 " + "b" * 64], "C: реестр заново"
    )

    with pytest.raises(CorpusError, match="реестр удалён") as excinfo:
        load_corpus(corpus_dir)
    assert deleted_in[:12] in str(excinfo.value)


def test_untracked_recreation_after_a_deleting_commit_is_refused(tmp_path: Path) -> None:
    """Пересоздать реестр **неотслеживаемым** файлом — тот же обход, и он не проходит.

    Проверка начиналась с `ls-files`: для неотслеживаемого файла он отказывает,
    и функция возвращалась молча — историю не смотрели вовсе. Достаточно было
    удалить реестр коммитом и положить на его место новый файл, не добавляя в
    индекс: id-пространство обнулено, гейт зелёный.
    """
    corpus_dir = _fresh_repo(tmp_path)
    line_x = "D-andrei-shtanakov.steward-155-1 deleted"
    line_y = "D-andrei-shtanakov.steward-100-1 deleted"
    _registry_commit(tmp_path, corpus_dir, [line_x, line_y], "A: две строки")

    registry_path(corpus_dir).unlink()
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "B: реестр удалён")
    deleted_in = _git(tmp_path, "rev-parse", "HEAD").strip()

    # Файл снова на месте, но вне индекса — и с меньшим числом строк.
    registry_path(corpus_dir).write_text(line_x + "\n", encoding="utf-8")
    assert _git(tmp_path, "status", "--porcelain").strip().startswith("??")

    with pytest.raises(CorpusError, match="реестр удалён") as excinfo:
        load_corpus(corpus_dir)
    assert deleted_in[:12] in str(excinfo.value)


def test_untracked_registry_never_committed_is_fine(tmp_path: Path) -> None:
    """Реестр, которого в истории не было, — законный новый: коммиты рядом не важны."""
    corpus_dir = _fresh_repo(tmp_path)
    (tmp_path / "README.md").write_text("до реестра\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "коммит без реестра")

    case = load_case(_write_case(corpus_dir, _valid_case(), "steward-155.yaml"))
    append_registry([case], corpus_dir)  # файл остаётся неотслеживаемым

    assert [item.case_id for item in load_corpus(corpus_dir)] == ["andrei-shtanakov.steward-155"]


def test_registry_missing_in_the_worktree_but_present_in_history_is_refused(
    tmp_path: Path,
) -> None:
    """Файла нет в рабочем дереве, а в истории есть — не «пустой реестр».

    Пустой словарь на этом месте означал бы «ни одного id не зарегистрировано»:
    любой id снова свободен, и `--register` раздал бы их заново.
    """
    corpus_dir = _fresh_repo(tmp_path)
    _registry_commit(
        tmp_path, corpus_dir, ["D-andrei-shtanakov.steward-155-1 " + "a" * 64], "реестр"
    )
    registry_path(corpus_dir).unlink()

    with pytest.raises(CorpusError, match="восстановите файл"):
        load_corpus(corpus_dir)


def test_registry_that_never_existed_is_an_empty_registry(tmp_path: Path) -> None:
    """Реестра не было никогда — законный пустой: корпус ещё не размечали."""
    corpus_dir = _fresh_repo(tmp_path)
    (tmp_path / "README.md").write_text("без реестра\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "коммит без реестра")

    assert load_corpus(corpus_dir) == []


def test_registry_hidden_by_merge_simplification_is_refused(tmp_path: Path) -> None:
    """Версия, спрятанную упрощением истории у merge, проверка обязана увидеть.

    `git log -- <path>` по умолчанию упрощает историю: у merge-коммита, чьё
    дерево совпадает с первым родителем (TREESAME), вторая ветка не
    просматривается вовсе. Строку, закоммиченную на влитой стороне, можно было
    потерять разрешением мержа — и история выглядела бы нетронутой.
    """
    corpus_dir = tmp_path / "eval" / "corpus"
    corpus_dir.mkdir(parents=True)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    line_a = "D-andrei-shtanakov.steward-155-1 " + "a" * 64
    line_b = "D-andrei-shtanakov.steward-100-1 " + "b" * 64

    registry_path(corpus_dir).write_text(line_a + "\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "реестр: A")
    _git(tmp_path, "branch", "stale")

    registry_path(corpus_dir).write_text(f"{line_a}\n{line_b}\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "реестр: A+B")
    with_b = _git(tmp_path, "rev-parse", "HEAD").strip()

    # Мерж, чьё разрешение возвращает файл к версии A: два родителя, дерево
    # совпадает с первым — ровно случай, который упрощение истории скрывает.
    _git(tmp_path, "switch", "-q", "stale")
    # Мержим по sha, а не по имени ветки: имя ветки по умолчанию зависит от
    # настройки git на машине (`init.defaultBranch`), и тест от неё независим.
    _git(tmp_path, "merge", "--no-ff", "--no-commit", with_b)
    registry_path(corpus_dir).write_text(line_a + "\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "мерж, потерявший строку B")

    with pytest.raises(CorpusError, match="append-only") as excinfo:
        load_corpus(corpus_dir)
    message = str(excinfo.value)
    assert with_b[:12] in message, message
    assert "D-andrei-shtanakov.steward-100-1" in message


def _branching_registry(tmp_path: Path) -> tuple[Path, str, str, str, str, str]:
    """Реестр с двумя ветками: c1=[A], одна ветка добавила B, другая — C.

    Возвращает каталог корпуса, три строки и sha двух голов.
    """
    corpus_dir = _fresh_repo(tmp_path)
    line_a = "D-andrei-shtanakov.steward-155-1 deleted"
    line_b = "D-andrei-shtanakov.steward-100-1 deleted"
    line_c = "D-andrei-shtanakov.steward-101-1 deleted"

    _registry_commit(tmp_path, corpus_dir, [line_a], "c1: A")
    _git(tmp_path, "branch", "other")
    with_b = _registry_commit(tmp_path, corpus_dir, [line_a, line_b], "c2: A+B")
    _git(tmp_path, "switch", "-q", "other")
    with_c = _registry_commit(tmp_path, corpus_dir, [line_a, line_c], "c3: A+C")
    return corpus_dir, line_a, line_b, line_c, with_b, with_c


def _merge_with_resolution(tmp_path: Path, corpus_dir: Path, other: str, lines: list[str]) -> None:
    """Смержить `other` в текущую голову, разрешив реестр перечисленными строками.

    Мерж намеренно **не** проверяется на код возврата: обе стороны правили один
    файл, git отдаёт конфликт и ненулевой код, а разрешение — как раз предмет
    теста и записывается следующей строкой.
    """
    import subprocess

    subprocess.run(  # noqa: S603 — фикстура теста, argv фиксирован
        ["git", "-C", str(tmp_path), "merge", "--no-ff", "--no-commit", other],
        capture_output=True,
        text=True,
        check=False,
    )
    registry_path(corpus_dir).write_text("\n".join(lines) + "\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "мерж")


@pytest.mark.parametrize("order", [("a", "b", "c"), ("a", "c", "b")], ids=["a-b-c", "a-c-b"])
def test_lossless_merge_of_two_appends_is_append_only(
    tmp_path: Path, order: tuple[str, ...]
) -> None:
    """Мерж, сохранивший строки обеих сторон, — законное продолжение обоих родителей.

    Сверка «строка родителя на том же абсолютном индексе» отвергала любой такой
    мерж: у объединения [A,B,C] родитель [A,C] не совпадает по индексу 1, хотя
    ни одна его строка не потеряна. Реестр — append-only множество, а не
    файл с фиксированными номерами строк, поэтому правило — **упорядоченная
    подпоследовательность**, и порядок объединения (A,B,C или A,C,B) значения
    не имеет.
    """
    corpus_dir, line_a, line_b, line_c, with_b, _with_c = _branching_registry(tmp_path)
    by_key = {"a": line_a, "b": line_b, "c": line_c}

    _merge_with_resolution(tmp_path, corpus_dir, with_b, [by_key[key] for key in order])

    assert load_corpus(corpus_dir) == []


def test_merge_dropping_a_parent_line_is_still_refused(tmp_path: Path) -> None:
    """Подпоследовательность разрешает вставку, но не потерю: строка B обязана остаться."""
    corpus_dir, line_a, _line_b, line_c, with_b, _with_c = _branching_registry(tmp_path)

    _merge_with_resolution(tmp_path, corpus_dir, with_b, [line_a, line_c])

    with pytest.raises(CorpusError, match="append-only") as excinfo:
        load_corpus(corpus_dir)
    message = str(excinfo.value)
    assert with_b[:12] in message, message
    assert "D-andrei-shtanakov.steward-100-1" in message


def test_reordering_parent_lines_is_refused(tmp_path: Path) -> None:
    """Перестановка строк родителя — не подпоследовательность, значит нарушение.

    Порядок строк несёт смысл: файл last-wins, и последняя строка про id решает
    его состояние. Разрешить перестановку значило бы разрешить менять состояние
    id, не добавив ни одной строки.
    """
    corpus_dir = _fresh_repo(tmp_path)
    line_a = "D-andrei-shtanakov.steward-155-1 deleted"
    line_b = "D-andrei-shtanakov.steward-100-1 deleted"
    parent = _registry_commit(tmp_path, corpus_dir, [line_a, line_b], "A+B")
    _registry_commit(tmp_path, corpus_dir, [line_b, line_a], "B+A")

    with pytest.raises(CorpusError, match="append-only") as excinfo:
        load_corpus(corpus_dir)
    message = str(excinfo.value)
    assert parent[:12] in message, message
    # Названа первая строка родителя, не найденная по порядку: A нашлась
    # второй, и после неё B искать уже негде.
    assert "D-andrei-shtanakov.steward-100-1" in message


@pytest.mark.parametrize("current_last", [True, False], ids=["current-last", "stale-last"])
def test_merge_interleaving_lines_about_one_id_lets_the_child_order_decide(
    tmp_path: Path, current_last: bool
) -> None:
    """Мерж выбирает, какая строка про id окажется последней, — и это законно.

    Обе стороны дописали строку про один живой id: одна — устаревшее
    содержимое, другая — текущее. Любое чередование сохраняет строки обоих
    родителей, то есть append-only не нарушено; но файл **last-wins**, и
    разрешение мержа решает, какая строка считается актуальной. Если
    чередование поставило последней не ту, валидация требует явной
    перерегистрации (`--register`) — это и есть цена, которую платит оператор
    после мержа.
    """
    corpus_dir = _fresh_repo(tmp_path)
    case = load_case(_write_case(corpus_dir, _valid_case(), "steward-155.yaml"))
    append_registry([case], corpus_dir)
    entry_id = "D-andrei-shtanakov.steward-155-1"
    current = _registry_line(corpus_dir, entry_id)
    # Та же идентичность, другое содержимое: строка законна, но устарела.
    stale = f"{entry_id} {'d' * 64} {current.split()[2]}"
    base = [
        line
        for line in registry_path(corpus_dir).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    _registry_commit(tmp_path, corpus_dir, base, "c1: регистрация")
    _git(tmp_path, "branch", "other")
    with_stale = _registry_commit(tmp_path, corpus_dir, [*base, stale], "c2: устаревшая строка")
    _git(tmp_path, "switch", "-q", "other")
    _registry_commit(tmp_path, corpus_dir, [*base, current], "c3: текущее содержимое")

    order = [stale, current] if current_last else [current, stale]
    _merge_with_resolution(tmp_path, corpus_dir, with_stale, [*base, *order])

    if current_last:
        assert [item.case_id for item in load_corpus(corpus_dir)] == [
            "andrei-shtanakov.steward-155"
        ]
    else:
        with pytest.raises(CorpusError, match="зарегистрирован с другим содержимым"):
            load_corpus(corpus_dir)


def test_registry_head_check_survives_a_missing_git_binary(tmp_path: Path) -> None:
    """Нет бинаря git — проверка молча пропускается, корпус читается."""
    corpus_dir, _case = _tracked_corpus(tmp_path)

    assert load_corpus(corpus_dir, git="/nonexistent/git") != []


# --------------------------------------------------------------------------
# corpus_digest
# --------------------------------------------------------------------------


def test_corpus_digest_is_sha256_prefixed(tmp_path: Path) -> None:
    case = load_case(_write_case(tmp_path, _valid_case()))
    digest = corpus_digest([case])
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_corpus_digest_is_order_independent(tmp_path: Path) -> None:
    first = _valid_case()
    second = _second_case()
    case1 = load_case(_write_case(tmp_path, first, "a.yaml"))
    case2 = load_case(_write_case(tmp_path, second, "b.yaml"))
    assert corpus_digest([case1, case2]) == corpus_digest([case2, case1])


def test_corpus_digest_changes_with_content(tmp_path: Path) -> None:
    case = load_case(_write_case(tmp_path, _valid_case()))
    changed_data = _valid_case()
    changed_data["notes"] = "другой текст"
    changed_case = load_case(_write_case(tmp_path, changed_data, "b.yaml"))
    assert corpus_digest([case]) != corpus_digest([changed_case])
