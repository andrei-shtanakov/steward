"""Исчерпывающий инвентарь потребителей `approval-facts/v2` (R-M0-8, Task 11; M0-L).

Класс «продюсер переключён без всех потребителей» встречался трижды, поэтому перечень
потребителей файла `.steward/approval_facts.jsonl` и v2-кода — не проза в плане, а этот
тест: любой трекаемый файл с совпадением обязан стоять в `INVENTORY` с решением и с
набором разрешённых ему v2-импортов.

Распознавание — по классу, а не по экземплярам (ревью Task 11, I1). В `.py` импорт
находит `ast`: `import` и `from … import` любой формы (несколько имён из пакета, скобки,
`as`, `from steward import approvalfacts`, относительный импорт внутри `src/steward`,
`importlib.import_module("…")` с литералом), если он разрешается в `steward.approvalfacts`
или в его v2-модули `model`/`producer`/`reader`/`publish` (не в `.v3`). Текстовые
регулярные выражения остаются только для не-Python файлов и для литерала v2-header,
терпимого к пробелам.

Решения: `migrated` — переключён на v3 (или только текст), `kept-v2` — оставлен v2
намеренно (замороженный код/контракт v2 и его тесты, переиспользование v2 из v3,
исторические evidence и бэклог). У строки — множество разрешённых импортов
`"<модуль>:<имя>"` (`*` — модуль целиком); импорт вне множества — провал
(ревью Task 11, M1/M2). `None` — без ограничения: сам замороженный v2-код, его тесты и
не-Python файлы. Строка `kept-v2` без совпадения — устаревшая таблица; `migrated` может
совпадений не иметь вовсе (миграция текста их и убирает), но файл обязан существовать.
"""

import ast
import re
import subprocess
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

PACKAGE = "steward.approvalfacts"
V2_SUBMODULES = frozenset({"model", "producer", "reader", "publish"})
V2_MODULES = frozenset({PACKAGE, *(f"{PACKAGE}.{m}" for m in V2_SUBMODULES)})

#: Литерал header v2 — в любом файле, с любыми пробелами вокруг `:`.
V2_LITERAL = re.compile(r'"schema_version"\s*:\s*"2"')

#: Не-Python файлы: упоминания v2-модулей и их API, имени контракта и файла. Импорт
#: пакета — любой, кроме `v3`.
V2_TEXT = re.compile(
    r"steward\.approvalfacts\.(?:model|producer|reader|publish)\b"
    r"|from steward\.approvalfacts import (?!v3\b)"
    r"|import steward\.approvalfacts\b(?!\.v3)"
    r"|from steward import approvalfacts\b"
    r"|\bload_facts\b|\bdetect_legacy_v1\b|\bproducer\.materialize\b|\bclassify_results\b"
    r"|approval-facts/v2|approval_facts\.jsonl"
)

#: Область поиска — весь трекаемый репо; исключения перечислены здесь и только здесь,
#: каждое с причиной, и совпадают с таблицей Task 11.
EXCLUDED = {
    "docs/superpowers/": "спеки и планы описывают миграцию, не потребляют v2",
    "tests/fixtures/approvalfacts_v3/": "живые снимки v3",
    "contracts/approval-facts/v3/": "контракт v3 ссылается на v2 как на замороженный",
    "tests/approvalfacts/v3/test_v2_inventory.py": "сам этот тест",
}

_M = f"{PACKAGE}.model"
_P = f"{PACKAGE}.publish"
_R = f"{PACKAGE}.reader"


def _allow(module: str, *names: str) -> frozenset[str]:
    return frozenset(f"{module}:{name}" for name in names)


#: Мигрированный тест может писать v2-файл как отрицательную фикстуру (`Result` +
#: `build_header`/`publish`), но не читать его.
_V2_WRITER = _allow(_M, "RequestId", "Result") | _allow(_P, "build_header", "publish")

INVENTORY: dict[str, tuple[str, frozenset[str] | None]] = {
    # --- мигрированы: из v2 — только то, что переключение законно оставляет ---
    "src/steward/riskclassify/cli.py": (
        "migrated",
        _allow(_M, "RequestId")
        | _allow(
            _P,
            "ConfigError",
            "FACTS_RELPATH",
            "NotAGitRepository",
            "build_header",
            "publish",
            "remove_previous",
            "resolve_repo_root",
        ),
    ),
    "src/steward/gatecheck/approval.py": (
        "migrated",
        _allow(_M, "ActorType", "MAX_LEASE_SECONDS", "RequestId")
        | _allow(_P, "ConfigError")
        | _allow(_R, "UnreadableFacts", "detect_legacy_v1"),
    ),
    "src/steward/gatecheck/cli.py": (
        "migrated",
        _allow(_P, "ConfigError", "FACTS_RELPATH", "parse_origin"),
    ),
    "src/steward/gatecheck/candidate.py": ("migrated", frozenset()),
    "src/steward/gatecheck/git_facts.py": ("migrated", frozenset()),
    "scripts/collect_approval_facts.py": (
        "migrated",
        _allow(_P, "ConfigError", "parse_origin") | _allow(_R, "UnreadableFacts"),
    ),
    "scripts/approval-facts-schedule.md": ("migrated", frozenset()),
    "profiles/approval-facts-scope.yaml": ("migrated", frozenset()),
    ".github/codex/review-context.txt": ("migrated", frozenset()),
    "tests/collector/test_collect_approval_facts.py": (
        "migrated",
        _V2_WRITER | _allow(_M, "scope_digest"),
    ),
    "tests/gatecheck/test_approval_check.py": (
        "migrated",
        _V2_WRITER
        | _allow(_M, "Header", "ObservationState", "scope_digest")
        | _allow(_P, "ConfigError"),
    ),
    # подменяет `publish`/`remove_previous` модуля через `importlib.import_module`
    "tests/riskclassify/test_approval_facts_cli.py": ("migrated", _allow(_P, "*")),
    "tests/approvalfacts/v3/test_release_e2e.py": ("migrated", _V2_WRITER),
    # --- оставлены v2 намеренно ---
    "src/steward/approvalfacts/__init__.py": ("kept-v2", None),
    "src/steward/approvalfacts/model.py": ("kept-v2", None),
    "src/steward/approvalfacts/producer.py": ("kept-v2", None),
    "src/steward/approvalfacts/reader.py": ("kept-v2", None),
    "src/steward/approvalfacts/publish.py": ("kept-v2", None),
    # v3 переиспользует модель и `MechanicalFailure`, но не v2-продюсер
    "src/steward/approvalfacts/v3/release.py": (
        "kept-v2",
        _allow(_M, "ObservationState", "RequestId")
        | _allow(f"{PACKAGE}.producer", "MechanicalFailure"),
    ),
    # общие проверки v2-читателя — да, `load_facts` — никогда
    "src/steward/approvalfacts/v3/release_reader.py": (
        "kept-v2",
        _allow(_M, "ActorType", "Header", "ObservationState", "RequestId", "SCHEMA_VERSION")
        | _allow(
            _R,
            "UnreadableFacts",
            "_MERGE_SHA_RE",
            "check_bijection",
            "check_state_for_kind",
            "parse_header",
            "parse_request",
            "read_records",
        ),
    ),
    "tests/approvalfacts/test_model.py": ("kept-v2", None),
    "tests/approvalfacts/test_producer.py": ("kept-v2", None),
    "tests/approvalfacts/test_reader.py": ("kept-v2", None),
    "tests/approvalfacts/test_publish.py": ("kept-v2", None),
    "tests/contract/test_approval_facts_schema.py": ("kept-v2", None),
    "tests/approvalfacts/v3/test_release.py": ("kept-v2", None),
    "tests/approvalfacts/v3/test_release_reader.py": ("kept-v2", None),
    "contracts/approval-facts/v2/": ("kept-v2", None),
    "docs/evidence/2026-08-21-approval-facts-v2-migration/": ("kept-v2", None),
    "docs/evidence/2026-09-14-approval-facts-policy-refresh/": ("kept-v2", None),
    "eval/corpus/andrei-shtanakov.steward-161.yaml": ("kept-v2", None),
    "TODO.md": ("kept-v2", None),
}


def v2_imports(source: str, package: str | None = None) -> frozenset[str]:
    """v2-импорты исходника как `"<модуль>:<имя>"`; `*` — модуль целиком.

    `package` — пакет файла для относительных импортов (`None` — вне `src/steward`).
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(f"{a.name}:*" for a in node.names if a.name in V2_MODULES)
        elif isinstance(node, ast.ImportFrom):
            found.update(_from_import(node, package))
        elif isinstance(node, ast.Call):
            found.update(_import_module_call(node))
    return frozenset(found)


def _from_import(node: ast.ImportFrom, package: str | None) -> set[str]:
    module = node.module or ""
    if node.level:
        if package is None:
            return set()
        parts = package.split(".")
        parts = parts[: len(parts) - (node.level - 1)]
        module = ".".join([*parts, module] if module else parts)
    found: set[str] = set()
    for alias in node.names:
        if module == "steward" and alias.name == "approvalfacts":
            found.add(f"{PACKAGE}:*")
        elif module == PACKAGE and alias.name in V2_SUBMODULES:
            found.add(f"{PACKAGE}.{alias.name}:*")
        elif module in V2_MODULES and not (module == PACKAGE and alias.name == "v3"):
            found.add(f"{module}:{alias.name}")
    return found


def _import_module_call(node: ast.Call) -> set[str]:
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name != "import_module" or not node.args:
        return set()
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and arg.value in V2_MODULES:
        return {f"{arg.value}:*"}
    return set()


def _package_of(path: str) -> str | None:
    """Пакет файла под `src/` (для относительных импортов); вне `src/` — `None`."""
    parts = Path(path).with_suffix("").parts
    if parts[:1] != ("src",):
        return None
    # и у `pkg/__init__.py`, и у `pkg/mod.py` пакет — `pkg`
    return ".".join(parts[1:-1])


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


def _imports(path: str) -> frozenset[str]:
    text = _text(path)
    if text is None or not path.endswith(".py"):
        return frozenset()
    return v2_imports(text, _package_of(path))


def _module_of(path: str) -> str | None:
    """Модуль файла под `src/` (`pkg/__init__.py` — `pkg`); вне `src/` — `None`."""
    parts = Path(path).with_suffix("").parts
    if parts[:1] != ("src",):
        return None
    return ".".join(parts[1:-1] if parts[-1] == "__init__" else parts[1:])


def is_v2_consumer(path: str, text: str) -> bool:
    """Совпадение файла: сам v2-модуль, импорт по `ast` (`.py`) или текст (прочее);
    литерал — везде."""
    if V2_LITERAL.search(text) or _module_of(path) in V2_MODULES:
        return True
    if path.endswith(".py"):
        return bool(v2_imports(text, _package_of(path)))
    return bool(V2_TEXT.search(text))


def _hits(prefix: str = "") -> set[str]:
    """Трекаемые файлы под `prefix` с совпадением, без `EXCLUDED`."""
    found = set()
    for path in _tracked():
        if not path.startswith(prefix) or path.startswith(tuple(EXCLUDED)):
            continue
        text = _text(path)
        if text is not None and is_v2_consumer(path, text):
            found.add(path)
    return found


def _row(path: str) -> tuple[str, frozenset[str] | None] | None:
    for key, row in INVENTORY.items():
        if path == key or (key.endswith("/") and path.startswith(key)):
            return row
    return None


def test_no_unlisted_v2_consumer() -> None:
    unlisted = sorted(p for p in _hits() if _row(p) is None)
    assert unlisted == [], unlisted


def test_inventory_has_no_stale_rows() -> None:
    for key, (verdict, _) in INVENTORY.items():
        assert verdict in {"migrated", "kept-v2"}, key
        assert (ROOT / key).exists(), key
        if verdict == "kept-v2":
            assert _hits(key), f"{key}: kept-v2 without a v2 match — the table is stale"


def test_rows_import_only_their_allowed_v2_names() -> None:
    """M1/M2: мигрированный файл и живой v3-модуль берут из v2 только своё множество."""
    for path, (_, allowed) in INVENTORY.items():
        if allowed is None:
            continue
        extra = sorted(_imports(path) - allowed)
        assert extra == [], (path, extra)


def test_migrated_files_no_longer_use_v2_reader() -> None:
    for path, (verdict, _) in INVENTORY.items():
        if verdict != "migrated":
            continue
        text = _text(path)
        assert text is not None and not V2_LITERAL.search(text), path
        assert f"{_R}:load_facts" not in _imports(path), path


# --- отрицательные двойники: формы, которые прежний регэксп пропускал (ревью I1) --------


@pytest.mark.parametrize(
    "source",
    [
        "from steward.approvalfacts import ApprovalFactsV2, publish\n",
        "from steward.approvalfacts import build_header, publish\n",
        "from steward.approvalfacts import (\n    publish,\n)\n",
        "import steward.approvalfacts as af\naf.publish(p, h, r)\n",
        "from steward import approvalfacts\napprovalfacts.publish(p, h, r)\n",
        "from steward.approvalfacts.reader import load_facts as lf\n",
        "import importlib\nimportlib.import_module('steward.approvalfacts.producer')\n",
    ],
    ids=[
        "package_multi_name",
        "package_writer",
        "package_parenthesized",
        "import_as",
        "from_steward_import_package",
        "submodule_alias",
        "import_module_literal",
    ],
)
def test_import_forms_are_caught(source: str) -> None:
    assert v2_imports(source), source
    assert is_v2_consumer("src/steward/new_consumer.py", source)


def test_relative_import_inside_src_is_caught() -> None:
    """`from .. import publish` в `steward.approvalfacts.v3.*` — это v2-модуль `publish`."""
    package = _package_of("src/steward/approvalfacts/v3/new_consumer.py")
    assert v2_imports("from .. import publish\n", package) == {f"{_P}:*"}
    assert v2_imports("from ..reader import load_facts\n", package) == {f"{_R}:load_facts"}


@pytest.mark.parametrize(
    "header",
    ['{"kind":"header","schema_version":"2"}', '{"schema_version" : "2"}'],
    ids=["compact", "spaced"],
)
def test_v2_literal_is_whitespace_tolerant(header: str) -> None:
    assert is_v2_consumer("src/steward/new_consumer.py", f"h = {header!r}\n")
    assert is_v2_consumer("docs/new.md", header)


@pytest.mark.parametrize(
    "source",
    [
        "import steward.approvalfacts.v3\n",
        "from steward.approvalfacts import v3\n",
        "from steward.approvalfacts.v3.release import materialize_release\n",
        "from steward.review_eval.cache import materialize\n",
    ],
    ids=["import_v3", "from_package_import_v3", "v3_module", "foreign_materialize"],
)
def test_v3_and_foreign_imports_are_not_v2(source: str) -> None:
    assert v2_imports(source) == frozenset(), source
    assert not is_v2_consumer("src/steward/new_consumer.py", source)


def test_import_outside_the_row_set_is_reported() -> None:
    """Множество строки срабатывает: лишний v2-импорт в мигрированном файле виден."""
    _, allowed = INVENTORY["src/steward/gatecheck/cli.py"]
    assert allowed is not None
    probe = "from steward.approvalfacts.model import ApprovalFactsV2, RequestId\n"
    assert sorted(v2_imports(probe) - allowed) == [f"{_M}:ApprovalFactsV2", f"{_M}:RequestId"]
