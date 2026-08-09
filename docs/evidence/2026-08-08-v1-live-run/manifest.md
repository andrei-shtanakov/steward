# V1 live gated run — pins manifest (started 2026-08-08)

- steward: master `c2414f7fd48f6a335293fc4bf2e2edaa6906c659` (clean: 0 dirty files), commands run as `uv run --project <steward>` from steward checkout root (profile sibling anchoring)
- spec-runner: fresh clone git@github.com:andrei-shtanakov/spec-runner.git @ tag v2.21.0 = `13e7667b31a53e8dd08ac34e0749aeb1d0bfdfd5`, `uv sync`; `spec-runner --version` → 2.21.0; invoked as `uv run --project /Users/Andrei_Shtanakov/labs/all_ai_orchestrators/_cowork_output/v1-live-run-2026-08-08/spec-runner-v2.21.0 spec-runner` from project dir
- LLM backend: claude CLI 2.1.226 (Claude Code) at /Users/Andrei_Shtanakov/.local/bin/claude; model pinned in spec-runner.config.yaml: claude_model=sonnet, review_model=sonnet, skip_permissions=true (full config committed in project)
- doctor probe: verdict=ready, exit 0 in 31.0s, cost $0.19 (raw/doctor.json)
- project: /Users/Andrei_Shtanakov/labs/all_ai_orchestrators/_cowork_output/v1-live-run-2026-08-08/project (git init -b master), spec profile: lite (spec-runner default) / steward profiles/lite.yaml
- KNOWN HYPOTHESIS to measure (owner, pre-run): spec-runner stage "tasks" vs steward lite node "task" → expected GC-STAGE + no profile owner_role for tasks.md; recorded as result, stand NOT fixed
- discovery pre-run: spec-runner v2.21.0 has SPEC_META_CONTRACT=2 with first-class owner_role (steward §2 revendor trigger fired) — friction/follow-up, not part of this run
