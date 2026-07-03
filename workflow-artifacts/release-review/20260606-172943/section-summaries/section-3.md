# Section 3 Summary — Tests & Regression

Health: **199 passed, 1 skipped** (skip = installed-metadata test in editable
checkout, expected). Total coverage **72%** (1716/2398). Recently-added features
are well covered at unit level (truncation, --max-output-tokens, cost helpers,
ProgressReporter 96%, summary_exists any/explicit, filter_has).

Gaps to close (high value, cheap):
- T5 (medium): cost/progress tests read machine-local `~/.config/opencode/
  opencode.json` → non-hermetic. Add autouse fixture neutralizing
  `_load_model_costs`.
- T1 (medium): `cmd_estimate` body never executed (5%).
- T2 (medium): `cmd_list --has` / `missing-summaries` not end-to-end (cmd_list 37%).
- T4 (medium): generated summary `.json` not validated against the schema.

Deferred (lower value): T3 extract paths, R1 cmd_summarize e2e truncation
(helper already tested).

Brittle test risk: ProgressReporter timing tests are time-dependent but use
durations/`ETA`/`elapsed` presence rather than exact values — acceptable.
