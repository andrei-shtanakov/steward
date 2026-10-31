"""Исчерпывающий инвентарь потребителей `approval-facts/v2` (R-M0-8, Task 11; ruling M0-L).

Класс «продюсер переключён без всех потребителей» встречался трижды, поэтому перечень
потребителей файла `.steward/approval_facts.jsonl` и v2-кода — не проза в плане, а этот
тест: любой трекаемый файл с совпадением обязан стоять в `INVENTORY` с решением, а
«мигрированный» — больше не читать v2.

Решения: `migrated` — переключён на v3 (или только текст); `kept-v2` — оставлен v2
намеренно (замороженный код/контракт v2 и его тесты, переиспользование v2 из v3,
исторические evidence и бэклог). Строка `kept-v2` без совпадения — устаревшая таблица;
`migrated` может совпадений не иметь вовсе (миграция текста их и убирает), но файл обязан
существовать.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

#: Импорт/вызов v2-модулей, v2-читателя и продюсера, литерал v2-header, имя контракта и
#: файла. Импорт пакета — только в точной форме: `from steward.approvalfacts import v3` —
#: не потребитель v2. `materialize` — только как атрибут `producer`: импорт из продюсера
#: ловит путь модуля, а голое имя — посторонняя функция `steward.review_eval.cache`.
V2_PATTERNS = re.compile(
    r"steward\.approvalfacts\.(?:model|producer|reader|publish)\b"
    r"|from steward\.approvalfacts import (?:model|producer|reader|publish)\b"
    r"|\bload_facts\b|\bdetect_legacy_v1\b|\bproducer\.materialize\b|\bclassify_results\b"
    r'|"schema_version": "2"|approval-facts/v2|approval_facts\.jsonl'
)

#: Признаки чтения v2 в «мигрированном» файле.
V2_READER = re.compile(r"\bload_facts\b|approvalfacts\.producer|\"schema_version\": \"2\"")

#: Область поиска — весь трекаемый репо; исключения перечислены здесь и только здесь,
#: каждое с причиной, и совпадают с таблицей Task 11.
EXCLUDED = {
    "docs/superpowers/": "спеки и планы описывают миграцию, не потребляют v2",
    "tests/fixtures/approvalfacts_v3/": "живые снимки v3",
    "contracts/approval-facts/v3/": "контракт v3 ссылается на v2 как на замороженный",
    "tests/approvalfacts/v3/test_v2_inventory.py": "сам этот тест",
}

INVENTORY = {
    # --- мигрированы ---
    "src/steward/riskclassify/cli.py": "migrated",
    "src/steward/gatecheck/approval.py": "migrated",
    "src/steward/gatecheck/cli.py": "migrated",
    "src/steward/gatecheck/candidate.py": "migrated",
    "src/steward/gatecheck/git_facts.py": "migrated",
    "scripts/collect_approval_facts.py": "migrated",
    "scripts/approval-facts-schedule.md": "migrated",
    "profiles/approval-facts-scope.yaml": "migrated",
    ".github/codex/review-context.txt": "migrated",
    "tests/collector/test_collect_approval_facts.py": "migrated",
    "tests/gatecheck/test_approval_check.py": "migrated",
    "tests/riskclassify/test_approval_facts_cli.py": "migrated",
    "tests/approvalfacts/v3/test_release_e2e.py": "migrated",
    # --- оставлены v2 намеренно ---
    "src/steward/approvalfacts/__init__.py": "kept-v2",
    "src/steward/approvalfacts/model.py": "kept-v2",
    "src/steward/approvalfacts/producer.py": "kept-v2",
    "src/steward/approvalfacts/reader.py": "kept-v2",
    "src/steward/approvalfacts/publish.py": "kept-v2",
    "src/steward/approvalfacts/v3/release.py": "kept-v2",
    "src/steward/approvalfacts/v3/release_reader.py": "kept-v2",
    "tests/approvalfacts/test_model.py": "kept-v2",
    "tests/approvalfacts/test_producer.py": "kept-v2",
    "tests/approvalfacts/test_reader.py": "kept-v2",
    "tests/approvalfacts/test_publish.py": "kept-v2",
    "tests/contract/test_approval_facts_schema.py": "kept-v2",
    "tests/approvalfacts/v3/test_release.py": "kept-v2",
    "tests/approvalfacts/v3/test_release_reader.py": "kept-v2",
    "contracts/approval-facts/v2/": "kept-v2",
    "docs/evidence/2026-08-21-approval-facts-v2-migration/": "kept-v2",
    "docs/evidence/2026-09-14-approval-facts-policy-refresh/": "kept-v2",
    "eval/corpus/andrei-shtanakov.steward-161.yaml": "kept-v2",
    "TODO.md": "kept-v2",
}


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True
    ).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def _text(path: str) -> str | None:
    try:
        return (ROOT / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _hits(prefix: str = "") -> set[str]:
    """Трекаемые файлы под `prefix` с совпадением `V2_PATTERNS`, без `EXCLUDED`."""
    found = set()
    for path in _tracked():
        if not path.startswith(prefix) or path.startswith(tuple(EXCLUDED)):
            continue
        text = _text(path)
        if text is not None and V2_PATTERNS.search(text):
            found.add(path)
    return found


def _listed(path: str) -> bool:
    return any(path == key or (key.endswith("/") and path.startswith(key)) for key in INVENTORY)


def test_no_unlisted_v2_consumer() -> None:
    unlisted = sorted(p for p in _hits() if not _listed(p))
    assert unlisted == [], unlisted


def test_inventory_has_no_stale_rows() -> None:
    for key, verdict in INVENTORY.items():
        assert verdict in {"migrated", "kept-v2"}, key
        assert (ROOT / key).exists(), key
        if verdict == "kept-v2":
            assert _hits(key), f"{key}: kept-v2 without a v2 match — the table is stale"


def test_migrated_files_no_longer_use_v2_reader() -> None:
    for path, verdict in INVENTORY.items():
        if verdict == "migrated":
            text = _text(path)
            assert text is not None and not V2_READER.search(text), path
