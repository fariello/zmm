# Schema Validation

## Schemas / data contracts discovered
1. `schemas/summary.json` — JSON Schema (draft-07) for the summary `.json`
   sidecar (`meeting` + `model_output` + implicit `metadata`).
2. Per-year `YYYY-Meeting-Processing.json` inventory — implicit serialized
   contract (`schema_version: 1`, `period`, `meetings[]`). No JSON Schema file;
   versioned via `schema_version`.
3. The summary `.json` payload written by `write_summary_outputs` (meeting +
   model_output + metadata).
4. JSON/CSV stdout output of `list`/`report`/`estimate`/`paths` — machine-
   parseable public contract (no schema file; column-name contract).

## Validation performed (lane D, jsonschema 4.26.0 available in venv)
- `schemas/summary.json` is **valid draft-07** (loads + compiles).
- A representative payload emitted by `write_summary_outputs` validates against
  the schema with **0 errors**, including `action_items[].priority: null`.
- `SUMMARY_REQUIRED_KEYS` (code) == `model_output.required` (schema). This is
  guarded by an existing drift test in `tests/test_contract.py`.

## Findings
| ID | Severity | Issue | Status |
|---|---|---|---|
| 20260606-172943-S6-SCH1 | low | Runtime validation (`validate_summary_output`) is key-presence only — it does not check types, enums, lengths, or nested required fields, and does not use the schema. External/user-supplied JSON is never schema-validated. | Deferred (best-effort by design; adding jsonschema to the runtime path would add a dependency). |
| 20260606-172943-S3-T4 | medium | No automated test validates a generated payload against `schemas/summary.json` (only key-presence). Schema drift could go unnoticed. | Planned for S7 — add a conformance test (use `jsonschema` if importable, else type+key fallback). |

## Compatibility risks
- None found. Both serialized contracts carry version markers
  (`schema_version`, `zmm_version`) and recent changes did not alter required
  keys or filename conventions. The `meeting`/`metadata` sections are produced
  by zmm (not the model), so they are stable.

## CI opportunity
- A schema + golden-sample validation step could be added to CI, but it would
  require adding `jsonschema` to the `[dev]` extra. Recorded; implemented as a
  test that degrades gracefully if `jsonschema` is absent (no new hard dep).

## Final status (updated in Section 8)
- See `final-bug-security-audit.md` and `10-validation-results.md`.
