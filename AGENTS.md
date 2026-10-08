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

- `scripts/alternate_bulk_formats.py`: People-to-Civiq converter; direct CLI accepts
  input data/JSON output roots, force refresh and a UTC reference day. Safe YAML
  loading ignores pickle caches; generate into a fresh external destination.
- `scripts/state_names.py`: dependency-free state/territory labels, shared with
  legacy `scripts/utils.py`; conversion does not import the legacy utilities.
- `scripts/converter_safety.py`: strict SafeLoader rejects duplicate keys, all
  merges and recursive aliases; preflight rejects output symlinks and POSIX
  descriptor-relative fresh-inode replacement avoids following/truncating links.
- `scripts/test_alternate_bulk_formats.py`: synthetic normalization and real CLI
  contract tests using temporary copied checkouts, never production output.
- `schema.md`: source and generated JSON contracts.
- `README.md`: development and converter test instructions.

Converter errors never authorize publication; per-file writes are atomic, not
the entire dataset. Preserve source bytes/mtime and leave source snapshot binding
and independent validation to the build coordinator. Safety helper copies in the
People and national submodules intentionally keep each checkout self-contained.
