# People — Agent Guide

## Repository guidance

Read `README.md` and `schema.md` before changing the converter contract. Preserve
unrelated work and stable identifiers. Do not regenerate production output or
hand-edit person data without explicit approval; synthetic test fixtures belong
in temporary destinations.

## Test-value review before handoff

- Whenever new tests are added, the primary coordinator must invoke
  `TestValueReviewer` before handoff. Include affected setup/helpers, production
  behavior and existing tests; inspect uncommitted and nonignored untracked tests.
- Agent definition: `.opencode/agent/subagents/code/test-value-reviewer.md`.
  It is auto-discovered; no metadata registry is required here.
- The reviewer reports **Keep / Modify / Remove**, plus unresolved cases, without
  changing files. Supply an explicit base: “Review this branch's tests with
  TestValueReviewer, base=<approved-base-ref>.” Inputs are prompts, not commands.
- Read-only shell/Git inspection requires approval. Do not run tests during
  static review without separate explicit approval; review is not proof of a pass.
- No review ledger whatsoever: do not read, create, repair, validate or update
  one. No review fingerprints, dependency inventories, caches or persistence.
- If delegation is unavailable, request coordinator review and report it pending.
  Installation alone is not a completed review. Stop on validation failure and
  request approval before correcting anything.
- Quit and restart OpenCode after installing or changing agent configuration.

## Relevant files

- `scripts/alternate_bulk_formats.py`: People-to-Civiq converter.
- `scripts/test_alternate_bulk_formats.py`: synthetic converter contract tests.
- `schema.md`: source and generated JSON contracts.
- `README.md`: development and converter test instructions.
