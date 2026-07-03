# Final Bug / Security Sanity Audit

Post-implementation review of changes made during run 20260606-172943. Scope is
the run's diff (`git diff 7600230 HEAD`), not a full Section 2 repeat.

## New / modified code paths reviewed

1. **`main()` date-range guard (X1/B1).** Wraps only `parse_date_range`; raises
   `SystemExit` with a clean message. No double-validation side effects (the
   handler re-parses the now-known-valid value). Safe.
2. **`parse_json_response` non-object rejection (X2/B2).** Raises `ValueError`
   for non-dict; `call_model_json` already catches it, saves a diagnostic, and
   reports via `_error_hint` (which groups `ValueError` with JSON errors). The
   fenced-code fallback still works. Verified by unit + e2e tests. Safe.
3. **`cmd_clean` worklist refactor (X3/B3) + dry-run mkdir fix.** Behavior
   preserved: same skip rules, same outputs. ProgressReporter/ETA/`--max` now
   apply to processed items. `out_dir.mkdir` moved into the write loop so dry
   run no longer creates empty directories (a pre-existing minor issue, now
   fixed). `merged_path = rec.merged_path or ""` is safe — worklist guarantees a
   real path. Verified by clean tests (write/skip/dry-run). Safe.
4. **`_warn_insecure_base_url` (X4/S2).** Pure stderr warning; once-only guard;
   https/localhost/127.0.0.1/::1/.local exempt; wrapped in try/except. No effect
   on request behavior. Verified by test. Safe.
5. **`init` chmod 600 (X5/S1).** Only chmods when a literal key is embedded
   (not `{env:..}`/`$VAR`). `os.chmod` wrapped in try/except with a clear
   warning fallback. Tightening perms cannot expose data. Safe.
6. **`_error_hint` precedence (X6/E1).** Pure parenthesization; no behavior
   change except correct grouping. Safe.

## Configuration / CI / packaging / tests / docs reviewed

- **CI (X11):** installs via `-e .[dev]`, adds 3.14, adds a build+install smoke
  job. No publish/deploy/secrets. Validated locally end-to-end (build → install
  in fresh venv → import/--version/--help). YAML parses. Safe.
- **pyproject:** added 3.14 classifier and `jsonschema>=4.0` to `[dev]` only
  (not a runtime dep). Parsed with tomllib. Safe.
- **Tests:** autouse hermetic `_load_model_costs` fixture removes machine-local
  dependence; new tests are offline (fake client, tmp_path). No network. Safe.
- **Docs (X12/X13/X14):** README/CHANGELOG/help text only. The CHANGELOG
  `[Unreleased]` restructure does not change any tag; content is preserved and
  relocated. Safe.

## File handling / path / subprocess / network / secrets

- No new subprocess, shell, eval, pickle, or yaml usage introduced.
- No new network calls. The only network-adjacent change (S2) adds a warning.
- `_scrub_secret` unchanged; the new warning prints the base_url (not the key).
- `urllib.parse` added (stdlib).
- Path handling: `out_path.parent.mkdir` is scoped to the resolved output dir;
  no traversal introduced.

## Unresolved HIGH/CRITICAL findings
None. No HIGH or CRITICAL findings were identified in the run. All medium
findings (B1, B2, T1, T2, T4, T5, D1, CI1, CI2) were implemented.

## Residual risk
- Low. Deferred items (E2 --max validation, U1/U2 flag renames, M2 module split,
  SCH1 full runtime schema validation, T3/R1 extra tests) are non-blocking and
  recorded with rationale.
- `_cost_rate` substring matching remains loose (a single-letter model name can
  match an unrelated configured model); affects only cost *display*, not
  behavior, and tests are now hermetic. Recorded as a future refinement (no new
  ID needed beyond the note here) — not a release blocker.

## Final validation
- `pytest`: 209 passed, 0 skipped (installed) / 208 passed + 1 skipped (bare
  checkout). `py_compile`: OK. `python -m build`: wheel+sdist OK, prompts +
  schemas present. See `10-validation-results.md`.

## Does the final release recommendation change?
No. Changes are hardening/robustness/test/doc/CI improvements with no public
contract breakage. Recommendation: GO for `master`; the next tagged release
should fold `[Unreleased]` into a dated section (and add 3.14 wheels coverage,
already in CI).
