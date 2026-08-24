"""Hash chain over ``gate_verdicts.jsonl`` lines — tamper-evident ledger (steward#105).

Every record after line 1 carries ``prev_hash`` — the SHA-256 (hex) of the
previous line's exact UTF-8 bytes, newline excluded. Line 1 (the header) is
the chain anchor and never carries the field: nothing precedes it, and the
contract already pins line 1 to ``kind: header``.

What the chain proves and what it honestly does not:

- a substituted, edited or reordered line **inside** the file breaks the next
  record's ``prev_hash`` — machine-detectable without any external state;
- a file with no ``prev_hash`` anywhere is **legacy** — valid by the additive
  rule of the contract (old files predate the field), never "broken";
- a truncated *tail* or a wholesale rewrite with recomputed hashes is NOT
  detected by the chain alone — that needs an external anchor (a published
  chain head), which is out of scope here and said so in the contract README.

Verification is a reader concern, so it lives apart from the emitter and is
usable over any file, including one produced by an older steward.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

__all__ = ["ChainReport", "line_hash", "serialize_chained", "verify_chain"]

_PREV_HASH_KEY = "prev_hash"


def line_hash(line: str) -> str:
    """SHA-256 hex of one serialized JSONL line (UTF-8 bytes, no newline)."""
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def serialize_chained(records: list[dict]) -> str:
    """Serialize records as JSONL, chaining each line to its predecessor.

    The producer half: record N (N >= 2) gets ``prev_hash`` of the exact
    bytes of line N-1 *as written* — hashing happens after serialization, so
    the check re-runs byte-for-byte on the file. Records are not mutated.
    """
    lines: list[str] = []
    for record in records:
        chained = dict(record)
        if lines:
            chained[_PREV_HASH_KEY] = line_hash(lines[-1])
        else:
            chained.pop(_PREV_HASH_KEY, None)  # line 1 is the anchor
        lines.append(json.dumps(chained, ensure_ascii=False))
    return "".join(line + "\n" for line in lines)


@dataclass(frozen=True)
class ChainReport:
    """Outcome of verifying one file's hash chain."""

    status: str  # "chained" | "legacy" | "broken"
    lines: int
    chained_from: int | None = None  # 1-based line where the chain starts
    broken_line: int | None = None  # 1-based line where verification failed
    reason: str | None = None


def verify_chain(text: str | bytes) -> ChainReport:
    """Verify the hash chain of a verdicts file's raw content.

    Pass the file's RAW BYTES (``path.read_bytes()``) or a string that never
    went through newline translation. ``Path.read_text()`` is the trap: its
    universal-newlines mode rewrites ``\\r\\n`` back to ``\\n`` before this
    function sees the content, silently un-breaking a CRLF-rewritten ledger
    (Codex gate on steward PR #109, round 4). Bytes input closes that hole
    at the API level — ``bytes.decode()`` performs no newline translation.
    Undecodable bytes are ``broken``: an unreadable file never verifies.

    The additive rule of the contract, verbatim: a legacy tail without the
    field is valid **up to the first record that carries it**; from that
    record on, every line must be parseable JSON, must carry ``prev_hash``,
    and the hash must match the previous raw line. ``prev_hash`` on line 1
    is broken by definition — nothing precedes it.

    Lines are split on ``\\n`` exactly, NOT ``splitlines()``: that helper
    normalizes every Unicode line boundary (``\\r``, U+2028, …), which breaks
    byte-for-byte fidelity in both directions — a CRLF-converted file would
    verify as intact though its bytes changed, and a legitimate raw U+2028
    inside a JSON string (legal there, and ``ensure_ascii=False`` writes it
    raw) would split one record in two (Copilot review on steward PR #109).
    An LF→CRLF conversion therefore verifies as *broken* — an owner ruling,
    not an accident: the producer only ever writes LF, so a CRLF ledger did
    not come out of the producer, and a tamper-evident check has no notion
    of a "benign" byte rewrite (the opposite reading was proposed by the
    Codex gate on the same PR and rejected; the contract README pins the
    line definition).

    Every line must parse as JSON, chained or not: a file with an
    unparseable line must never verify as valid — "legacy" is a statement
    about a *readable* pre-chain file, not a fallback for corrupt input
    (Codex gate on steward PR #109, accepted).

    Line 1 must be a ``kind: header`` record with ``schema_version == "1"``
    — the contract makes the header mandatory and pins the version as const,
    and a consumer must classify any other version as unsupported, never as
    a valid ledger (Codex gate, rounds 3-4, accepted). Deeper schema
    validity stays the reader's job; the verifier checks only what the chain
    and the file's own self-identification require.
    """
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            return ChainReport(
                status="broken",
                lines=0,
                broken_line=1,
                reason=f"not valid UTF-8 — the file cannot verify: {exc}",
            )
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # the trailing newline of the last record, not an empty line
    if not lines:
        return ChainReport(
            status="broken",
            lines=0,
            broken_line=1,
            reason="empty file — the mandatory line-1 header record is missing",
        )
    parsed: list[object] = []
    for index, line in enumerate(lines):
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            return ChainReport(
                status="broken",
                lines=len(lines),
                broken_line=index + 1,
                reason="unparseable line — the file cannot verify as chained or legacy",
            )
    first = parsed[0]
    if not (isinstance(first, dict) and first.get("kind") == "header"):
        return ChainReport(
            status="broken",
            lines=len(lines),
            broken_line=1,
            reason="line 1 is not a 'kind: header' record — the contract requires the header",
        )
    if first.get("schema_version") != "1":
        return ChainReport(
            status="broken",
            lines=len(lines),
            broken_line=1,
            reason=(
                f"unsupported schema_version {first.get('schema_version')!r} — "
                "a v1 verifier must never report such a file as valid"
            ),
        )
    start: int | None = None  # 0-based index of the first chained record
    for index, record in enumerate(parsed):
        if isinstance(record, dict) and _PREV_HASH_KEY in record:
            start = index
            break
    if start is None:
        return ChainReport(status="legacy", lines=len(lines))
    if start == 0:
        return ChainReport(
            status="broken",
            lines=len(lines),
            broken_line=1,
            reason="prev_hash on line 1 — nothing precedes the anchor",
        )
    for index in range(start, len(lines)):
        number = index + 1
        record = parsed[index]  # every line already proved parseable above
        if not isinstance(record, dict) or _PREV_HASH_KEY not in record:
            return ChainReport(
                status="broken",
                lines=len(lines),
                chained_from=start + 1,
                broken_line=number,
                reason="record without prev_hash after the chain started",
            )
        expected = line_hash(lines[index - 1])
        if record[_PREV_HASH_KEY] != expected:
            return ChainReport(
                status="broken",
                lines=len(lines),
                chained_from=start + 1,
                broken_line=number,
                reason=(
                    f"prev_hash mismatch: expected {expected}, recorded {record[_PREV_HASH_KEY]!r}"
                ),
            )
    return ChainReport(status="chained", lines=len(lines), chained_from=start + 1)
