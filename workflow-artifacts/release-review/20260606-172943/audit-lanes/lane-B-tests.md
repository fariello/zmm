# Audit Lane Report

## Lane

- Lane ID: B
- Lane name: Tests & Regression
- Run ID: 20260606-172943
- Section coverage: S3 (Tests, fixtures, coverage, regression protection)
- Read-only: yes
- Created: 2026-06-06

## Scope

Audit of the `zmm` test suite under `tests/` against the single-module implementation
`zoom_meeting_manager.py` (~3561 lines / 2398 measured statements). Focus: suite health
(pass/skip/time), coverage of important code paths, presence/absence of regression tests
for recently added features, brittle or low-value tests, edge-case/contract/schema coverage,
and machine-local config dependencies that could make CI non-deterministic.

All commands were read-only. The suite uses `tmp_path` plus a fake OpenAI client
(`tests/conftest.py`) so it is offline and safe. No tracked file was modified except this report.

## Suite health (measured)

- Command: `python -m pytest tests/` (venv `/home/gfariello/venv/p3.14/bin/python`, pytest 9.0.3, Python 3.14.4)
- Result: **199 passed, 1 skipped, ~3.8–4.9s** (stable across repeated runs).
- The 1 skip: `tests/test_packaging.py:63` — "zmm not installed with metadata in this environment"
  (importlib.metadata not available for an editable/uninstalled checkout). Expected; not a defect.
- Coverage (`--cov=zoom_meeting_manager --cov-report=term-missing`): **TOTAL 1716/2398 = 72%**.

## Files and artifacts inspected

- Tests: `tests/conftest.py`, `tests/helpers.py`, `tests/test_commands.py` (806 L),
  `tests/test_core.py` (476 L), `tests/test_contract.py` (222 L),
  `tests/test_integration.py` (175 L), `tests/test_parser.py` (120 L),
  `tests/test_packaging.py` (64 L).
- Implementation focus functions in `zoom_meeting_manager.py`: cost
  (`_load_model_costs` 2212, `_estimate_cost` 2230, `_cost_rate` 2250,
  `_project_output_tokens` 2275, `_estimate_total_cost` 2287), `ProgressReporter` 2315,
  `cmd_estimate` 2391, `cmd_export` 2418, `cmd_extract` 2715, `summary_exists` 2882,
  `select_summarizable` 2913, truncation (`_completion_kwargs` 1146, `ModelTruncationError`
  1161, `_usage_from_response` 1175, `_extract_content` 1193), `parse_json_response` 1269,
  `parse_date_range` 606, `slugify` 634, `atomic_write_text` 96, `RunJournal`/`load_resume_done`
  149/189, `cmd_migrate` 1642, `cmd_init` 1745, `_cmd_list_models` 1434, `cmd_list` 1540.
- Schema: `schemas/summary.json`. Config dep: `~/.config/opencode/opencode.json` (present, 20KB, 99 models).

### Per-function coverage (computed from `coverage json`)

| Function | Lines | Coverage |
|---|---|---|
| `_extract_content` (truncation) | 1193–1211 | 8/8 (100%) |
| `_usage_from_response` | 1175–1190 | 11/11 (100%) |
| `_completion_kwargs` (--max-output-tokens) | 1146–1158 | 7/7 (100%) |
| `_estimate_total_cost` | 2287–2304 | 12/12 (100%) |
| `_project_output_tokens` | 2275–2284 | 3/3 (100%) |
| `ProgressReporter` | 2315–2388 | 54/56 (96%) |
| `summary_exists` | 2882–2905 | 10/10 (100%) |
| `select_summarizable` | 2913–2947 | 14/16 (87%) |
| `parse_date_range` | 606–620 | 11/11 (100%) |
| `slugify` | 634–639 | 4/4 (100%) |
| `_cost_rate` | 2250–2262 | 10/12 (83%) |
| `_estimate_cost` | 2230–2247 | 9/15 (60%) — `_estimate_cost` is itself only partly exercised |
| `cmd_export` | 2418–2510 | 64/70 (91%) |
| `cmd_migrate` | 1642–1662 | 12/14 (85%) |
| `RunJournal` | 149–187 | 18/22 (81%) |
| `load_resume_done` | 189–206 | 9/12 (75%) |
| `atomic_write_text` | 96–116 | 9/13 (69%) — error/replace path uncovered |
| `cmd_show` | 2036–2108 | 40/49 (81%) |
| **`cmd_extract`** | 2715–2804 | **38/76 (50%)** |
| **`cmd_list`** | 1540–1593 | **16/43 (37%)** — `--has` and `missing` end-to-end branches uncovered |
| **`cmd_estimate`** | 2391–2415 | **1/20 (5%)** — never executed, only parser-routed |
| **`_cmd_list_models`** | 1434–1497 | **1/50 (2%)** |
| **`_load_providers_from_opencode`** | 1411–1431 | **1/14 (7%)** |
| **`cmd_init`** | 1745–2033 | **0/149 (0%)** — interactive wizard, unexercised |

Recently-added features are well covered: truncation `finish_reason='length'`
(`test_commands.py:564,581`), `--max-output-tokens` send/respect/zero
(`:531,542,553`), output-inclusive cost estimate (`:636,645,653`),
`ProgressReporter` timing+cost (`:600,619`), `_usage_from_response` variants
(`:682`), any-vs-explicit-model `summary_exists` semantics
(`test_core.py:364,376,386`; `test_commands.py:770,790`), and `--has` via
`filter_has` unit tests (`test_contract.py:177`).

## Candidate findings

| Candidate ID | Type | Severity | Title | Evidence | Affected files | Recommended action |
|---|---|---|---|---|---|---|
| T1 | Test gap | Medium | `cmd_estimate` never executed (5% line cov) | `cmd_estimate` 2391–2415 = 1/20; only parser route at `test_parser.py:34` | `zoom_meeting_manager.py`, `tests/` | Add a test calling `cmd_estimate` on a temp tree with `_load_model_costs` mocked; assert table/json row has model, file count, projected output tokens, total cost (and `output_priced` "-" path). |
| T2 | Test gap | Medium | `cmd_list --has KIND` & `missing-summaries` not exercised end-to-end | `cmd_list` 1540–1593 = 16/43; `has_kind` branch (1581–1591) and `missing` branch (1565–1579) uncovered; only `filter_has`/`select_summarizable` unit-tested | `zoom_meeting_manager.py`, `tests/test_commands.py` | Add cmd_list tests with `list_object="meetings", has_kind="summary"` and `list_object="missing", missing_kind="summaries"` on a mixed tree (reuse `_two_meeting_mixed_tree`); assert correct rows + empty-notice text. |
| T3 | Test gap | Medium | `cmd_extract` only 50% covered | `cmd_extract` 2715–2804 = 38/76; only `person/actions`, `person/statements`, invalid-regex tested (`test_commands.py:177,209`) | `zoom_meeting_manager.py`, `tests/test_commands.py` | Add tests for `extract search --regex` happy path (matches across meetings), `extract me items`, and empty-result rendering for csv/json formats. |
| T4 | Test gap | Medium | Generated summary `.json` not validated against `schemas/summary.json` structurally | `test_commands.py:92–96` checks top-level keys + one field only; no structural/schema check; `validate_summary_output` is key-presence only (1287–1300) | `tests/test_commands.py`, `schemas/summary.json` | Add a test that loads `schemas/summary.json` and asserts the written payload’s `model_output` contains all `required` keys and that `meeting`/`metadata` shapes match; or run jsonschema if available (skip if not). Pairs with existing `test_schema_required_keys_match_code`. |
| T5 | Non-determinism risk | High | Cost/Progress code reads machine-local `~/.config/opencode/opencode.json`; partial-match logic makes results host-dependent | `_load_model_costs` reads real file (`OPENCODE_CONFIG` 416). Verified live: `_cost_rate('m','input')==0.13` from the real 99-model config because `_estimate_cost`/`_cost_rate` use substring match `model in key or key in model` (2237/2256). `test_progress_reporter_timing_fields` (`:600`) uses model `"no-such-model"` and asserts `"cost $" not in out` — passes today only because no real model substring-matches it. | `tests/test_commands.py:600`, `zoom_meeting_manager.py:2230,2250` | Mock `_load_model_costs` (or `_cost_rate`) in `test_progress_reporter_timing_fields` to return `{}` so the no-cost assertion is deterministic; add an autouse fixture that patches `_load_model_costs` to `{}` by default for the whole suite so any unmocked cost path is isolated from CI machine config. |
| T6 | Brittle test | Low | Substring-match pricing is itself untested and is fragile | `_estimate_cost` 2230 / `_cost_rate` 2250 partial-match (`model in key or key in model`) can mis-price short/ambiguous model names; no test pins this behavior | `zoom_meeting_manager.py`, `tests/` | Add a unit test pinning the partial-match contract (exact match preferred; document/limit substring matching) with a mocked cost table; flag the substring heuristic to Lane reviewing core logic. |
| T7 | Test gap | Low | `atomic_write_text` replace/error path uncovered (69%) | `atomic_write_text` 96–116 = 9/13; only basic write + overwrite tested (`test_core.py:347,356`) | `zoom_meeting_manager.py`, `tests/test_core.py` | Add a test for the temp-file cleanup-on-failure branch (e.g. force write failure) and confirm no stray `.tmp` left behind. |
| T8 | Test gap | Low | `parse_json_response` code-fence fallback path partly uncovered | `parse_json_response` 1269–1276 = 7/8 (line 1275 fence-extract miss) | `zoom_meeting_manager.py`, `tests/test_core.py` | Add a test passing ```` ```json {...} ``` ```` wrapped content and assert it parses; and a non-fenced invalid string re-raises. |
| R1 | Regression gap | Medium | No end-to-end truncation regression at `cmd_summarize` level | Truncation tested at `call_model_json` (`:564`) but not via `cmd_summarize` with `finish_reason="length"` (the user-visible path) | `tests/test_commands.py` | Add `cmd_summarize` test with `fake_client(finish_reason="length")` asserting SystemExit (or diagnostic when `ignore_model_errors`) and no `.summary.json` written — guards the wired-up path, not just the helper. |
| R2 | Regression gap | Low | ProgressReporter ETA/proj assertions are presence-only, not value-checked | `test_progress_reporter_timing_fields` asserts `"ETA" in out`; timing values are real-clock so can't be value-asserted; proj-cost formula (2376–2378) not numerically tested for `done < total` | `tests/test_commands.py:600,619` | Add a test with mocked costs and 2 items asserting the `proj $` value equals `cost/done*total` after the first finish (deterministic numeric check). |
| R3 | Regression gap | Low | `migrate legacy` only checks discovery text, not actual file moves/renames | `test_integration.py:115` asserts `"Discovered"`/`"merged transcripts"` strings only | `zoom_meeting_manager.py`, `tests/test_integration.py` | Add a test on a legacy-layout tree asserting files are relocated/renamed to expected merged names, with a dry-run variant asserting no changes. |

## Candidate actions (smallest high-value tests to add)

| Candidate action | Source candidate IDs | Description | Risk | Validation idea |
|---|---|---|---|---|
| Autouse `_load_model_costs={}` isolation fixture | T5, T6 | conftest autouse fixture patching `zmm._load_model_costs` to `{}` unless a test overrides; removes host-config leakage | Very low | Run suite with real `~/.config/opencode/opencode.json` removed AND present — already verified identical (199 pass) but fixture makes it guaranteed |
| Deterministic ProgressReporter no-cost test | T5, R2 | Mock costs to `{}` in timing test; add numeric proj-cost test with mocked rates | Very low | Assert exact `proj $X.XXXX` string |
| `cmd_estimate` execution test | T1 | Temp tree + mocked costs; assert row fields for summarize/clean/extract | Low | Compare token sum + projected output + total |
| `cmd_list --has` / `missing-summaries` e2e tests | T2 | Reuse `_two_meeting_mixed_tree`; assert filtered rows + count line + empty notice | Low | stdout contains expected titles only |
| Schema-conformance of written summary `.json` | T4 | Load `schemas/summary.json`, assert written `model_output` required keys present | Low | Reuses `VALID_MODEL_OUTPUT` fixture |
| End-to-end truncation at `cmd_summarize` | R1 | `fake_client(finish_reason="length")` → SystemExit / diagnostic, no `.json` written | Low | `glob("*.summary.json") == []` |
| `cmd_extract search` + empty-result rendering | T3 | regex match path + csv/json empty output | Low | json output `== "[]"` etc. |

## Non-applicable checks

- **Network/LLM live tests**: N/A by design — suite uses `FakeClient` (`conftest.py`), fully offline.
- **Concurrency/locking tests**: N/A — single-process CLI; no threading in scope.
- **`cmd_init` interactive wizard (1745–2033, 0%)**: largely N/A for automated coverage —
  it is an interactive `input()`-driven flow with live model probing; low value to mock fully.
  Noted as an intentional coverage gap, not a finding.
- **`_cmd_list_models`/`_load_providers_from_opencode` (2%/7%)**: depend on opencode provider
  config; partially N/A. Could be unit-tested with a synthetic config (low priority).

## Uncertainties

- The 72% total includes large intentionally-unexercised interactive/CLI-wiring blocks
  (`cmd_init` 149 lines, `_cmd_list_models` 50 lines, `main` entry 3543–3557). Excluding those,
  the *logic* coverage of summarize/clean/extract/cost/select paths is materially higher
  (most focus functions 80–100%). Main agent should decide whether `cmd_init`/`_cmd_list_models`
  warrant a coverage exclusion pragma or smoke tests.
- T5/T6 substring-match pricing (`model in key or key in model`) is a *correctness* concern as
  well as a test-determinism concern — flagged here from the test angle; the core-logic lane
  should confirm whether the substring heuristic is intended behavior or a latent mispricing bug.
- Whether `jsonschema` is an available/declared dependency is unconfirmed (not found in
  `pyproject.toml` quick scan); T4 should be written to skip gracefully if absent.

## Handoff notes

- Suite is **green and fast (199 passed / 1 skipped / ~4s)** and the *recently added* features
  this session (truncation detection, `--max-output-tokens`, ProgressReporter, output-inclusive
  cost, `--has` filter, any-model `summary_exists`) all have direct unit tests — regression
  protection for the new work is good at the unit level.
- Highest-priority item for the main agent: **T5** (deterministic isolation from the machine's
  `~/.config/opencode/opencode.json`). Today the suite passes with or without that file, but the
  `test_progress_reporter_timing_fields` no-cost assertion and any unmocked cost path are only
  incidentally safe; an autouse `_load_model_costs={}` fixture removes the CI/host fragility cheaply.
- Reconcile T6 with the core-logic lane: the substring price-matching is a shared concern
  (both a test-determinism and a possible mispricing bug).
- Coverage gaps worth closing are mostly at the **command-wiring** layer (T1 cmd_estimate 5%,
  T2 cmd_list `--has`/missing 37%, T3 cmd_extract 50%, R1 e2e truncation) — the underlying
  helpers are already tested, so these are thin, low-risk integration tests that guard the
  user-visible paths against silent breakage.
- Deduplicate T2 against any Lane covering CLI/UX, and T4 against any Lane covering schema.
