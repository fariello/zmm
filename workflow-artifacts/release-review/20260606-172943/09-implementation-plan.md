# 09 Implementation Plan

## Scope summary
Implement safe, high-value, well-scoped fixes from Sections 1–6 that improve
correctness, robustness, security hygiene, test determinism/coverage, docs
accuracy, and CI/release readiness — without breaking public contracts.

## Non-goals
- No public flag renames (U1/U2 deferred to user).
- No module split (M2, tracked in TODO).
- No version bump, no retag, no push (push not permitted).
- No new runtime dependencies. No linters/formatters.
- No full runtime jsonschema validation (SCH1 deferred).

## Change batches

### Batch 1 — Correctness & robustness (code)
- X1 (B1): In `main()`, wrap `parse_date_range`/record loading so an invalid
  `--date-range` exits with a clean `ERROR:` message (SystemExit 2) instead of a
  traceback. Narrow: catch ValueError/ArgumentTypeError around the date parse.
- X2 (B2): In the summarize path, if model output is not a dict, treat it as a
  model error (save diagnostic + count failed) instead of crashing
  `render_summary_text`. Add an `isinstance(data, dict)` guard.
- X3 (B3): Size the clean `ProgressReporter` from the actually-processable set
  (build the worklist first, mirroring `cmd_summarize`).
- X6 (E1): Parenthesize the `_error_hint` not-found condition.
- X7 (M1): Add a comment documenting the `_LAST_USAGE` serial-execution assumption.
- Risk: low. Public behavior change: only error UX (cleaner messages) + bugfix.
- Tests: X15 (non-dict payload), date-range error test.
- Validation: `py_compile` + `pytest`.
- Commit: 1 commit referencing B1/B2/B3/E1/M1.

### Batch 2 — Security hygiene (code)
- X4 (S2): Warn once if `base_url` uses non-localhost `http://` (cleartext key).
- X5 (S1): When `init` writes a config containing a literal `api_key`, chmod 600
  the file and note it.
- Risk: low. Public behavior: adds a warning + tightens new-file perms.
- Validation: manual + `pytest` (init test if present).
- Commit: 1 commit referencing S1/S2.

### Batch 3 — Tests (determinism + coverage)
- X8 (T5): Autouse fixture in conftest neutralizing `_load_model_costs` (return
  {}) so cost/progress tests are hermetic; opt-in override where pricing is
  needed (existing tests already monkeypatch).
- X9 (T1/T2): End-to-end tests for `cmd_estimate` and `cmd_list --has` /
  `missing-summaries`.
- X10/X15 (T4/B2): Schema-conformance test for a generated payload (graceful if
  `jsonschema` missing) + non-dict model-output regression test.
- Risk: low (tests only). Validation: `pytest`.
- Commit: 1 commit referencing T1/T2/T4/T5/B2.

### Batch 4 — Docs (accuracy)
- X12 (D2/D3/D4): Add missing flags to README, add empty help strings, fix
  list-missing choices.
- X13 (D1): Reconcile release docs — adopt `[Unreleased]` convention in CHANGELOG
  + fix README release procedure reference. No retag.
- X14 (E3): README privacy note that Diagnostics may contain transcript content.
- Risk: low (docs). Validation: `zmm --help`, manual read.
- Commit: 1 commit referencing D1/D2/D3/D4/E3.

### Batch 5 — CI/packaging readiness
- X11 (CI1/CI2/CI3 + pyproject classifier): Add Python 3.14 to matrix +
  classifier; install via `.[dev]`; add a build+install smoke job. Keep no
  publish/secrets.
- Risk: low (CI yaml + classifier). Validation: local build + py_compile of yaml
  via a YAML parse; CI runs on push (not triggered here, no push).
- Commit: 1 commit referencing CI1/CI2/CI3.

## Deferred / wont-do
- E2 (--max validation), U1/U2 (renames), M2 (split), SCH1 (runtime jsonschema),
  T3 (extract tests), R1 (cmd_summarize e2e truncation), DEP1 removal (keep).

## Commit grouping
5 commits (one per batch), each referencing action IDs. No push.

## Validation method
After each batch: `python -m py_compile zoom_meeting_manager.py` and
`python -m pytest tests/ -q`. Final: full suite + a fresh `python -m build`.
