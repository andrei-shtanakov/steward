"""Compile-down emitters (WS-004, REQ-005, DEC-005): delegation, not reimplementation.

steward compiles governance artifacts *down* through existing boundaries: the
``decomposition`` artifact renders to a Maestro ``project.yaml``, and each
workstream delegates its leaf spec to spec-runner authoring. steward does not
own either target format — Maestro owns ``project.yaml``, spec-runner owns
``tasks.md`` — so the emitters produce the consumer's shape and validate only
what the consumers demonstrably do not (see ``emitter-contract-check.md``:
Maestro ``validate --no-fs`` misses dangling ``depends_on`` links).
"""
