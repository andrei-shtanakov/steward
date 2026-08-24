"""Pre-adoption scan of an external tool checkout (steward#104).

Deterministic, offline, AST-only: no code from the target is ever imported
or executed — the whole point is to look BEFORE the first ``import``. The
requirement comes from a live incident (ai-repos-research, 2026-08-23): a
cloned repository carried a fileless loader with a hardcoded C2 address and
a module-top-level trigger that would have fired on first import.

Three checks, three finding codes:

- ``SCAN-TOPLEVEL-EFFECT`` — executable statements at module top level,
  outside ``if __name__ == "__main__"`` guards and def/class bodies. The
  statement allowlist is closed (imports, defs, assignments, docstrings,
  ``if``/``try`` recursed with the same rules); anything unknown is flagged
  — fail-closed, an unrecognized construct is a side effect until proven
  otherwise.
- ``SCAN-NET-LITERAL`` — hardcoded network endpoints in string literals:
  URL schemes (http/https/ws/wss/ftp) and valid dotted-quad IPv4 addresses.
  Docstrings are exempt (documentation links are not endpoints); configs are
  out of scope by construction — only ``*.py`` files are scanned.
- ``SCAN-DYNAMIC-EXEC`` — ``exec``/``eval``/``compile`` over a non-literal
  argument. A literal argument is not flagged (it is code review's job, not
  a wire-data risk). When the same module also imports network-capable
  modules, the finding says so — that combination is the incident shape.

Verdict is three-valued and "not scanned" is never "clean":

- ``clean``   — every file parsed and scanned, zero findings;
- ``failed``  — at least one finding (unparsable files may coexist);
- ``not_checked`` — no findings, but nothing was actually proven: either
  some files could not be parsed, or the target has no Python files at all.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["AdoptionScanError", "ScanFinding", "ScanResult", "scan_tree"]

_URL_RE = re.compile(r"\b(?:https?|wss?|ftp)://\S+", re.IGNORECASE)
_IPV4_RE = re.compile(r"\b((?:\d{1,3}\.){3}\d{1,3})(?::\d{1,5})?\b")

#: Modules whose import marks the file as network-capable for the
#: SCAN-DYNAMIC-EXEC message. Top-level names only — ``urllib.request``
#: counts as ``urllib``.
_NET_MODULES = frozenset(
    {
        "aiohttp",
        "ftplib",
        "http",
        "httpx",
        "pycurl",
        "requests",
        "smtplib",
        "socket",
        "telnetlib",
        "urllib",
        "urllib3",
        "websocket",
        "websockets",
    }
)

_DYNAMIC_EXEC_NAMES = frozenset({"exec", "eval", "compile"})

#: Directories never descended into. ``.git`` is not the tool's code; the
#: rest are standard generated/vendored-interpreter trees whose contents the
#: adopter does not import as the tool.
_SKIP_DIRS = frozenset({".git", "__pycache__", ".venv", "venv", ".tox", "node_modules"})


class AdoptionScanError(Exception):
    """Target is unusable as a scan input (missing / not a directory)."""


@dataclass(frozen=True)
class ScanFinding:
    """One deterministic finding, addressable to a file and line."""

    check: str
    path: str
    line: int
    message: str


@dataclass(frozen=True)
class ScanResult:
    """Scan outcome: three-valued verdict plus everything behind it."""

    verdict: str  # "clean" | "failed" | "not_checked"
    findings: list[ScanFinding] = field(default_factory=list)
    scanned: list[str] = field(default_factory=list)
    not_checked: list[dict[str, str]] = field(default_factory=list)


def scan_tree(target: Path) -> ScanResult:
    """Scan every ``*.py`` file under ``target``; never import anything.

    Deterministic: files are visited in sorted relative-path order, findings
    are emitted in (path, line, check) order.
    """
    if not target.exists():
        raise AdoptionScanError(f"target does not exist: {target}")
    if not target.is_dir():
        raise AdoptionScanError(f"target is not a directory: {target}")

    findings: list[ScanFinding] = []
    scanned: list[str] = []
    unchecked: list[dict[str, str]] = []

    for path in sorted(_iter_python_files(target)):
        rel = path.relative_to(target).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=rel)
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
            unchecked.append({"path": rel, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        findings.extend(_scan_module(tree, rel))
        scanned.append(rel)

    findings.sort(key=lambda f: (f.path, f.line, f.check))
    if findings:
        verdict = "failed"
    elif unchecked or not scanned:
        verdict = "not_checked"
    else:
        verdict = "clean"
    return ScanResult(verdict=verdict, findings=findings, scanned=scanned, not_checked=unchecked)


def _iter_python_files(target: Path):
    """Yield ``*.py`` files, skipping generated trees and symlinks.

    Symlinks (files and directories) are skipped, not followed: a link can
    point outside the checkout, and the scan promises to judge the tree it
    was given, not whatever the link's author aimed it at.
    """
    stack = [target]
    while stack:
        directory = stack.pop()
        for entry in directory.iterdir():
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in _SKIP_DIRS:
                    stack.append(entry)
            elif entry.suffix == ".py":
                yield entry


def _scan_module(tree: ast.Module, rel: str) -> list[ScanFinding]:
    findings = _scan_toplevel(tree.body, rel)
    findings.extend(_scan_net_literals(tree, rel))
    findings.extend(_scan_dynamic_exec(tree, rel))
    return findings


#: Top-level statements that never execute foreign code by themselves.
#: Assignments are allowed as a documented limitation: flagging every
#: ``logger = logging.getLogger(__name__)`` would bury the signal the scan
#: exists for (a bare top-level call — the incident's trigger shape).
_BENIGN_TOPLEVEL = (
    ast.Import,
    ast.ImportFrom,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.Pass,
)


def _scan_toplevel(body: list[ast.stmt], rel: str) -> list[ScanFinding]:
    """Flag executable top-level statements; recurse into if/try blocks.

    ``if``/``try`` are recursed with the same rules rather than allowed or
    flagged wholesale: ``try: import x`` fallbacks and ``if TYPE_CHECKING:``
    are everyday benign, while a call hidden under ``if sys.platform...``
    still runs at import time. The main guard is skipped entirely — its body
    runs only on explicit invocation, which is not "at first import".
    """
    findings: list[ScanFinding] = []
    for stmt in body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # docstring / bare literal
        if isinstance(stmt, _BENIGN_TOPLEVEL):
            continue
        if isinstance(stmt, ast.If):
            if _is_main_guard(stmt.test):
                continue
            findings.extend(_scan_toplevel(stmt.body, rel))
            findings.extend(_scan_toplevel(stmt.orelse, rel))
            continue
        if isinstance(stmt, ast.Try):
            for block in (stmt.body, *(h.body for h in stmt.handlers), stmt.orelse, stmt.finalbody):
                findings.extend(_scan_toplevel(block, rel))
            continue
        findings.append(
            ScanFinding(
                check="SCAN-TOPLEVEL-EFFECT",
                path=rel,
                line=stmt.lineno,
                message=(
                    f"top-level {type(stmt).__name__} executes at import time"
                    " (outside functions and the __main__ guard)"
                ),
            )
        )
    return findings


def _is_main_guard(test: ast.expr) -> bool:
    """Match ``__name__ == "__main__"`` in either orientation."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    sides = (test.left, test.comparators[0])
    names = [s.id for s in sides if isinstance(s, ast.Name)]
    literals = [s.value for s in sides if isinstance(s, ast.Constant)]
    return names == ["__name__"] and literals == ["__main__"]


def _scan_net_literals(tree: ast.Module, rel: str) -> list[ScanFinding]:
    docstring_nodes = _docstring_nodes(tree)
    findings: list[ScanFinding] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if node in docstring_nodes:
            continue
        text = node.value
        url = _URL_RE.search(text)
        if url:
            findings.append(
                ScanFinding(
                    check="SCAN-NET-LITERAL",
                    path=rel,
                    line=node.lineno,
                    message=f"hardcoded network endpoint in code: {url.group(0)!r}",
                )
            )
            continue
        ip = _IPV4_RE.search(text)
        if ip and _is_valid_ipv4(ip.group(1)):
            findings.append(
                ScanFinding(
                    check="SCAN-NET-LITERAL",
                    path=rel,
                    line=node.lineno,
                    message=f"hardcoded IPv4 address in code: {ip.group(0)!r}",
                )
            )
    return findings


def _is_valid_ipv4(dotted: str) -> bool:
    """All four octets in 0..255 — rejects lookalikes such as version strings."""
    return all(part.isdigit() and int(part) <= 255 for part in dotted.split("."))


def _docstring_nodes(tree: ast.Module) -> set[ast.expr]:
    """Constant nodes that are docstrings of the module, classes or functions."""
    nodes: set[ast.expr] = set()
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = scope.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            nodes.add(body[0].value)
    return nodes


def _scan_dynamic_exec(tree: ast.Module, rel: str) -> list[ScanFinding]:
    net_imports = sorted(_imported_net_modules(tree))
    findings: list[ScanFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callable_name(node.func)
        if name not in _DYNAMIC_EXEC_NAMES:
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            continue  # literal code object: reviewable by eye, not wire data
        suffix = (
            f"; module imports network-capable modules: {', '.join(net_imports)}"
            if net_imports
            else ""
        )
        findings.append(
            ScanFinding(
                check="SCAN-DYNAMIC-EXEC",
                path=rel,
                line=node.lineno,
                message=f"{name}() over a non-literal argument{suffix}",
            )
        )
    return findings


def _callable_name(func: ast.expr) -> str | None:
    """``exec`` both as a bare name and as ``builtins.exec``."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _imported_net_modules(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _NET_MODULES:
                    found.add(top)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            top = node.module.split(".")[0]
            if top in _NET_MODULES:
                found.add(top)
    return found
