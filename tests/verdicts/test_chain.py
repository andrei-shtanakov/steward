"""Tests for the gate_verdicts.jsonl hash chain (steward#105)."""

import json
from pathlib import Path

from typer.testing import CliRunner

from steward.riskclassify.cli import app
from steward.verdicts.chain import line_hash, serialize_chained, verify_chain

runner = CliRunner()

RECORDS = [
    {"kind": "header", "schema_version": "1", "profile": "lite"},
    {"kind": "artifact", "path": "a.md"},
    {"kind": "finding", "gate_id": "GC-STALE", "artifact": "a.md"},
]


def test_serialize_chains_every_record_after_the_first() -> None:
    lines = serialize_chained(RECORDS).splitlines()
    assert "prev_hash" not in json.loads(lines[0])
    for i in (1, 2):
        assert json.loads(lines[i])["prev_hash"] == line_hash(lines[i - 1])


def test_serialize_does_not_mutate_input_records() -> None:
    serialize_chained(RECORDS)
    assert all("prev_hash" not in r for r in RECORDS)


def test_roundtrip_verifies_as_chained() -> None:
    report = verify_chain(serialize_chained(RECORDS))
    assert report.status == "chained"
    assert report.chained_from == 2
    assert report.lines == 3


def test_legacy_file_without_field_is_valid() -> None:
    text = "".join(json.dumps(r) + "\n" for r in RECORDS)
    report = verify_chain(text)
    assert report.status == "legacy"
    assert report.broken_line is None


def test_tampered_middle_line_is_detected() -> None:
    lines = serialize_chained(RECORDS).splitlines()
    tampered = json.loads(lines[1])
    tampered["path"] = "b.md"  # edit without recomputing the successor's hash
    lines[1] = json.dumps(tampered, ensure_ascii=False)
    report = verify_chain("".join(line + "\n" for line in lines))
    assert report.status == "broken"
    assert report.broken_line == 3
    assert "mismatch" in (report.reason or "")


def test_substituted_line_with_forged_own_hash_is_detected() -> None:
    # Подмена строки, несущей ВНЕШНЕ корректное поле prev_hash: подделыватель
    # скопировал старое значение, но байты предыдущей строки уже другие.
    lines = serialize_chained(RECORDS).splitlines()
    head = json.loads(lines[0])
    head["profile"] = "team"
    lines[0] = json.dumps(head, ensure_ascii=False)
    report = verify_chain("".join(line + "\n" for line in lines))
    assert report.status == "broken"
    assert report.broken_line == 2


def test_record_dropping_out_of_chain_is_detected() -> None:
    lines = serialize_chained(RECORDS).splitlines()
    naked = json.loads(lines[2])
    del naked["prev_hash"]
    lines[2] = json.dumps(naked, ensure_ascii=False)
    report = verify_chain("".join(line + "\n" for line in lines))
    assert report.status == "broken"
    assert report.broken_line == 3
    assert "without prev_hash" in (report.reason or "")


def test_unparseable_line_inside_chain_is_broken() -> None:
    lines = serialize_chained(RECORDS).splitlines()
    lines[2] = '{"kind": "finding", broken'
    report = verify_chain("".join(line + "\n" for line in lines))
    assert report.status == "broken"
    assert report.broken_line == 3
    assert "unparseable" in (report.reason or "")


def test_prev_hash_on_line_1_is_broken() -> None:
    first = dict(RECORDS[0], prev_hash="0" * 64)
    text = json.dumps(first) + "\n"
    report = verify_chain(text)
    assert report.status == "broken"
    assert report.broken_line == 1


def test_empty_file_is_legacy() -> None:
    assert verify_chain("").status == "legacy"


def test_cli_chained_exit_0(tmp_path: Path) -> None:
    target = tmp_path / "gate_verdicts.jsonl"
    target.write_text(serialize_chained(RECORDS), encoding="utf-8")
    result = runner.invoke(app, ["verdicts-verify", str(target)])
    assert result.exit_code == 0
    assert "chained" in result.stdout


def test_cli_legacy_exit_0(tmp_path: Path) -> None:
    target = tmp_path / "gate_verdicts.jsonl"
    target.write_text("".join(json.dumps(r) + "\n" for r in RECORDS), encoding="utf-8")
    result = runner.invoke(app, ["verdicts-verify", str(target)])
    assert result.exit_code == 0
    assert "legacy" in result.stdout


def test_cli_broken_exit_1(tmp_path: Path) -> None:
    lines = serialize_chained(RECORDS).splitlines()
    tampered = json.loads(lines[1])
    tampered["path"] = "b.md"
    lines[1] = json.dumps(tampered, ensure_ascii=False)
    target = tmp_path / "gate_verdicts.jsonl"
    target.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    result = runner.invoke(app, ["verdicts-verify", str(target)])
    assert result.exit_code == 1
    assert "broken" in result.stdout


def test_cli_missing_file_exit_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["verdicts-verify", str(tmp_path / "нет.jsonl")])
    assert result.exit_code == 2


def test_crlf_conversion_is_detected_as_broken() -> None:
    # Copilot review on PR #109: splitlines() would normalize away the \r and
    # verify a CRLF-converted file as intact though its bytes changed.
    # Owner ruling (Codex gate proposed the opposite and was rejected): the
    # producer only ever writes LF, so a CRLF ledger did not come out of the
    # producer — a tamper-evident check has no notion of a benign byte
    # rewrite. The contract README pins the line definition.
    text = serialize_chained(RECORDS).replace("\n", "\r\n")
    report = verify_chain(text)
    assert report.status == "broken"
    assert report.broken_line == 2


def test_raw_u2028_inside_a_message_stays_one_record() -> None:
    # U+2028 is legal raw inside a JSON string and ensure_ascii=False writes
    # it raw; splitlines() would have split the record in two.
    records = [dict(RECORDS[0]), {"kind": "finding", "message": "до после"}]
    text = serialize_chained(records)
    report = verify_chain(text)
    assert report.status == "chained"
    assert report.lines == 2


def test_cli_non_utf8_file_exit_2(tmp_path: Path) -> None:
    target = tmp_path / "gate_verdicts.jsonl"
    target.write_bytes(b'\xff\xfe{"kind": "header"}\n')
    result = runner.invoke(app, ["verdicts-verify", str(target)])
    assert result.exit_code == 2


def test_unparseable_line_in_legacy_file_is_broken_not_legacy() -> None:
    # Codex gate on PR #109 (accepted): "legacy" is a statement about a
    # readable pre-chain file, not a fallback for corrupt input — a file
    # with an unparseable line must never verify as valid.
    text = json.dumps(RECORDS[0]) + "\n" + '{"kind": "artifact", broken\n'
    report = verify_chain(text)
    assert report.status == "broken"
    assert report.broken_line == 2
    assert "unparseable" in (report.reason or "")


def test_cli_malformed_legacy_file_exit_1(tmp_path: Path) -> None:
    target = tmp_path / "gate_verdicts.jsonl"
    target.write_text(json.dumps(RECORDS[0]) + "\n" + "not json\n", encoding="utf-8")
    result = runner.invoke(app, ["verdicts-verify", str(target)])
    assert result.exit_code == 1
    assert "broken" in result.stdout
