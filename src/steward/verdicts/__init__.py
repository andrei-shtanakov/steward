"""Verdicts emitter: serialize a gate-check run into gate_verdicts.jsonl (WS-A)."""

from steward.verdicts.emitter import EmitError, ProvenanceError, emit_verdicts

__all__ = ["EmitError", "ProvenanceError", "emit_verdicts"]
