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

#: High-risk callables — process execution and raw network. A call to one of
#: these on the right-hand side of a top-level assignment is flagged even
#: though generic assignment calls are not: the noise trade stays
#: (``logger = logging.getLogger(__name__)`` is silent), but a
#: ``trigger = subprocess.run(...)`` shape must not pass as clean (Codex
#: gate on steward PR #108, round 3). Matched against the dotted call name
#: after resolving import aliases (``import subprocess as sp``,
#: ``from os import system``), so renaming does not dodge the list.
_HIGH_RISK_EXACT = frozenset({"os.system", "os.popen", "os.fork", "os.startfile", "pty.spawn"})
_HIGH_RISK_PREFIXES = (
    "os.exec",
    "os.spawn",
    "os.posix_spawn",
    "subprocess.",
    "socket.",
    "requests.",
    "httpx.",
    "aiohttp.",
    "urllib.",
    "urllib3.",
    "ctypes.",
)

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

    for path in sorted(_iter_python_files(target, unchecked)):
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
    # not_checked is sorted like findings/scanned: iterdir() order is
    # filesystem-dependent, and the JSON on stdout promises byte-stability.
    unchecked.sort(key=lambda u: (u["path"], u["reason"]))
    if findings:
        verdict = "failed"
    elif unchecked or not scanned:
        verdict = "not_checked"
    else:
        verdict = "clean"
    return ScanResult(verdict=verdict, findings=findings, scanned=scanned, not_checked=unchecked)


def _iter_python_files(target: Path, unchecked: list[dict[str, str]]):
    """Yield ``*.py`` files, skipping generated trees; symlinks go to ``unchecked``.

    Symlinks are never followed: a link can point outside the checkout, and
    the scan promises to judge the tree it was given, not whatever the
    link's author aimed it at. But "not followed" is "not scanned", and not
    scanned must never read as clean — a symlinked ``*.py`` or a symlinked
    directory (outside the generated-tree skip list) is recorded in
    ``unchecked``, so a mixed tree cannot come out ``clean`` while part of
    it was silently dropped (Codex gate on steward PR #108, round 3).

    A directory that cannot be traversed (permissions, I/O) is recorded in
    ``unchecked`` instead of crashing the scan: an unreadable subtree is
    exactly "not scanned" too (Codex gate on steward PR #108).
    """
    stack = [target]
    while stack:
        directory = stack.pop()
        try:
            entries = list(directory.iterdir())
        except OSError as exc:
            rel = directory.relative_to(target).as_posix()
            unchecked.append({"path": rel, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        for entry in entries:
            rel = entry.relative_to(target).as_posix()
            if entry.is_symlink():
                if entry.is_dir():
                    if entry.name not in _SKIP_DIRS:
                        unchecked.append(
                            {"path": rel, "reason": "symlinked directory — not followed"}
                        )
                elif entry.suffix == ".py":
                    unchecked.append({"path": rel, "reason": "symlinked module — not followed"})
                continue
            if entry.is_dir():
                if entry.name not in _SKIP_DIRS:
                    stack.append(entry)
            elif entry.suffix == ".py":
                yield entry


def _scan_module(tree: ast.Module, rel: str) -> list[ScanFinding]:
    aliases = _import_aliases(tree)
    findings = _scan_toplevel(tree.body, rel, aliases)
    findings.extend(_scan_net_literals(tree, rel))
    findings.extend(_scan_dynamic_exec(tree, rel))
    return findings


#: Top-level statements that never execute foreign code by themselves.
#: Assignments get their own branch: generic calls on the right-hand side
#: stay silent as a documented limitation (flagging every
#: ``logger = logging.getLogger(__name__)`` would bury the signal), but
#: high-risk process/network calls there are flagged — see _HIGH_RISK_*.
#: ``def``/``class`` are NOT blanket-benign — their headers (decorators,
#: defaults, bases, metaclass keywords) and a class *body* execute at
#: definition time, i.e. at import; they get their own handling below.
_BENIGN_TOPLEVEL = (
    ast.Import,
    ast.ImportFrom,
    ast.Pass,
)


def _scan_toplevel(body: list[ast.stmt], rel: str, aliases: dict[str, str]) -> list[ScanFinding]:
    """Flag executable top-level statements; recurse into if/try blocks.

    ``if``/``try`` are recursed with the same rules rather than allowed or
    flagged wholesale: ``try: import x`` fallbacks and ``if TYPE_CHECKING:``
    are everyday benign, while a call hidden under ``if sys.platform...``
    still runs at import time. Only the main guard's *body* is skipped — it
    runs on explicit invocation, not at import. Its ``else`` branch is the
    opposite: ``__name__ != "__main__"`` is precisely the import case, so it
    is scanned like any other block (Copilot review on steward PR #108). The
    ``if`` *condition* itself always evaluates at import, so a call inside
    any top-level test is flagged too (Codex gate on the same PR) — the main
    guard's shape (Name vs Constant compare) cannot contain one.
    """
    findings: list[ScanFinding] = []
    for stmt in body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # docstring / bare literal
        if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            # The right-hand side executes at import. Generic calls stay
            # silent (documented noise trade), but high-risk process/network
            # calls are flagged — `trigger = subprocess.run(...)` must not
            # pass as clean (Codex gate on steward PR #108, round 3).
            if stmt.value is not None:
                findings.extend(_flag_high_risk_calls(stmt.value, rel, aliases))
            continue
        if isinstance(stmt, _BENIGN_TOPLEVEL):
            continue
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # The header executes at definition time — decorators and default
            # values run at import (`def f(x=os.system(...))` fires before the
            # first call; Codex gate on steward PR #108). The body does not.
            findings.extend(_flag_header_calls(stmt, rel))
            continue
        if isinstance(stmt, ast.ClassDef):
            # A class BODY executes at import in full, so it is recursed with
            # the same rules; the header (decorators, bases, metaclass
            # keywords) evaluates then too.
            findings.extend(_flag_header_calls(stmt, rel))
            findings.extend(_scan_toplevel(stmt.body, rel, aliases))
            continue
        if isinstance(stmt, ast.If):
            findings.extend(_flag_calls_in_test(stmt.test, rel))
            if not _is_main_guard(stmt.test):
                findings.extend(_scan_toplevel(stmt.body, rel, aliases))
            findings.extend(_scan_toplevel(stmt.orelse, rel, aliases))
            continue
        if isinstance(stmt, ast.Try):
            for block in (stmt.body, *(h.body for h in stmt.handlers), stmt.orelse, stmt.finalbody):
                findings.extend(_scan_toplevel(block, rel, aliases))
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


def _flag_header_calls(
    stmt: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, rel: str
) -> list[ScanFinding]:
    """Flag calls in a def/class header — they run at definition time.

    Covered: decorators, argument defaults (positional and keyword-only),
    class bases and metaclass keywords. Annotations are deliberately NOT
    scanned: under ``from __future__ import annotations`` they never
    evaluate, and typing constructs would drown the signal — a documented
    limitation, same trade as top-level assignments.
    """
    header_exprs: list[ast.expr] = list(stmt.decorator_list)
    if isinstance(stmt, ast.ClassDef):
        header_exprs.extend(stmt.bases)
        header_exprs.extend(kw.value for kw in stmt.keywords)
        what = "class header"
    else:
        args = stmt.args
        header_exprs.extend(d for d in args.defaults if d is not None)
        header_exprs.extend(d for d in args.kw_defaults if d is not None)
        what = "def header (decorator or default value)"
    return [
        ScanFinding(
            check="SCAN-TOPLEVEL-EFFECT",
            path=rel,
            line=node.lineno,
            message=f"call in a top-level {what} executes at import time",
        )
        for expr in header_exprs
        for node in ast.walk(expr)
        if isinstance(node, ast.Call)
    ]


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """Local name → canonical dotted origin, from every import in the module.

    ``import subprocess as sp`` maps ``sp`` → ``subprocess``;
    ``from os import system as s`` maps ``s`` → ``os.system``. Relative
    imports are skipped — they name the tool's own modules, not stdlib risk
    surfaces.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                aliases[bound] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _dotted_name(func: ast.expr, aliases: dict[str, str]) -> str | None:
    """Dotted call target with the base name resolved through import aliases."""
    parts: list[str] = []
    node = func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    base = aliases.get(node.id, node.id)
    parts.append(base)
    return ".".join(reversed(parts))


def _is_high_risk(dotted: str) -> bool:
    return dotted in _HIGH_RISK_EXACT or dotted.startswith(_HIGH_RISK_PREFIXES)


def _flag_high_risk_calls(value: ast.expr, rel: str, aliases: dict[str, str]) -> list[ScanFinding]:
    """Flag high-risk process/network calls inside an import-time expression."""
    findings: list[ScanFinding] = []
    for node in ast.walk(value):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted_name(node.func, aliases)
        if dotted is not None and _is_high_risk(dotted):
            findings.append(
                ScanFinding(
                    check="SCAN-TOPLEVEL-EFFECT",
                    path=rel,
                    line=node.lineno,
                    message=(
                        f"high-risk call {dotted}() in a top-level assignment "
                        "executes at import time"
                    ),
                )
            )
    return findings


def _flag_calls_in_test(test: ast.expr, rel: str) -> list[ScanFinding]:
    """A call in a top-level ``if`` condition executes at import — flag it."""
    return [
        ScanFinding(
            check="SCAN-TOPLEVEL-EFFECT",
            path=rel,
            line=node.lineno,
            message="call in a top-level if condition executes at import time",
        )
        for node in ast.walk(test)
        if isinstance(node, ast.Call)
    ]


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
        if _dynamic_exec_arg_is_literal(node):
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


def _dynamic_exec_arg_is_literal(node: ast.Call) -> bool:
    """The code argument is a constant — first positional, or keyword form.

    ``compile()`` accepts ``source=`` as a keyword (``eval``/``exec`` take
    the code positionally-only, but the check is name-based and cannot know
    that); treating a constant keyword source as non-literal flagged
    ``compile(source='1+1', ...)`` for nothing (Copilot review on steward
    PR #108).
    """
    if node.args:
        return isinstance(node.args[0], ast.Constant)
    for kw in node.keywords:
        if kw.arg in ("source", "object"):
            return isinstance(kw.value, ast.Constant)
    return False


def _callable_name(func: ast.expr) -> str | None:
    """``exec`` as a bare name, or as an attribute of ``builtins`` ONLY.

    Returning ``func.attr`` for any attribute call flagged every
    ``re.compile(pattern)`` and ``template.eval(ctx)`` as dynamic exec —
    false positives that would block adoption for nothing (Copilot review
    and Codex gate on steward PR #108). An attribute form counts only when
    the receiver is literally ``builtins``/``__builtins__``.
    """
    if isinstance(func, ast.Name):
        return func.id
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in ("builtins", "__builtins__")
    ):
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
