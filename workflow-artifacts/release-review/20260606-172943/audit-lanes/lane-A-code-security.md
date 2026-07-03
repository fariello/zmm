# Audit Lane Report

## Lane

- Lane ID: A
- Lane name: Code/Security/Edge
- Run ID: 20260606-172943
- Section coverage: S2
- Read-only: yes
- Created: 2026-06-06

## Scope

Static, read-only audit of the single-module CLI implementation
`zoom_meeting_manager.py` (3561 lines) for code quality, correctness, security,
privacy, edge cases, error handling, resource handling, and concurrency/state.
Focus areas: argument parsing/merge (SUPPRESS), date-range parsing,
slugify/filename handling, token counting, summary selector logic
(`summary_exists`/`select_summarizable`), model-output JSON parsing
(`parse_json_response`, fenced code, truncation via `finish_reason`), cost
estimation (`_estimate_cost`/`_cost_rate`/`_project_output_tokens`/
`_estimate_total_cost`), `ProgressReporter`, `atomic_write_text`, and
resume/journal logic. Security/privacy: API key handling and scrubbing,
world-readable key warning, reading `opencode.json`, subprocess/shell use, path
traversal, unsafe deserialization, TLS/base_url, secret logging.

## Files and artifacts inspected

- `zoom_meeting_manager.py` (full read; primary target)
- `tests/conftest.py` (fake client/fixtures; confirmed tmp-only, no network)
- `pyproject.toml`, `MANIFEST.in` (packaging / deprecated-script shipping)
- `deprecated/summarize_zoom_transcripts.py`, `deprecated/*.cfg.example` (predecessor)
- Commands run (read-only):
  - `python -m py_compile zoom_meeting_manager.py` → compiles clean.
  - `python -m pytest tests/ -q` → 199 passed, 1 skipped (uses tmp_path + fake client; safe).
  - Targeted `python -c` static probes of pure functions (no network/disk writes):
    `slugify`, `clean_filename`, `parse_partial_date`, `parse_date_range`,
    `parse_json_response`, `render_summary_text`, `--max` slicing semantics.
  - `grep` scans for `shell=True`/`os.system`/`eval`/`exec`/`pickle`/`yaml`/bare-except.

## Candidate findings

| Candidate ID | Type | Severity | Title | Evidence | Affected files | Recommended action |
|---|---|---|---|---|---|---|
| A1 | E | med | `--date-range` raises uncaught `ValueError` on valid-format but out-of-range dates | `parse_partial_date` uses `calendar.monthrange`/`date()` which raise `ValueError` (e.g. `2026-13`, `2026-02-30`); `parse_date_range` is called from `get_records` (zoom_meeting_manager.py:3236), NOT as an argparse `type=` (flag declared without `type=` at :3258). Verified: `parse_date_range('2026-13')` → uncaught `ValueError`. Also `argparse.ArgumentTypeError` raised at :603/:618 is not special outside argparse → also surfaces as traceback. | zoom_meeting_manager.py:592-619, :3236, :3258 | Wrap month/day construction and catch `ValueError`, re-raise as a friendly `SystemExit`; or register `--date-range` with a validating `type=` and surface a clean message. |
| A2 | E | med | Non-object JSON model output crashes rendering with `AttributeError` | `parse_json_response` returns whatever `json.loads` yields; valid non-object JSON (array/number/string) returns a non-dict despite `-> dict` annotation (:1269-1276). `validate_summary_output` detects non-dict but only WARNS (non-fatal) (:1295-1300). `cmd_summarize` then calls `write_summary_outputs`→`render_summary_text`, which does `model_out.get(...)` → `AttributeError` on a list. Verified crash with `model_output=[1,2,3]`. | zoom_meeting_manager.py:1269-1276, :1287-1300, :2857-2869, :3046-3052 | Make non-dict model output fatal (skip item + save diagnostic) in `call_model_json` or `cmd_summarize`, or guard `render_summary_text` to coerce non-dict to `{}` / raise a handled error. |
| A3 | B | med | `cmd_clean` progress total/ETA inflated; `--max` truncates wrong list | `total = len(selected)` where `selected = records[:args.max or None]` includes records with no merged path / already-cleaned / resume-skipped (:2546-2548). `progress.finish_item()` only runs for processed items (:2575,:2582), so `done`/`total` diverge → wrong ETA & projected cost (:2371-2378). `--max` bounds raw `records`, not eligible files (contrast `cmd_summarize` which truncates `to_process` at :2822-2823). | zoom_meeting_manager.py:2535-2589 | Compute the eligible list first (records with summarizable merged path, minus resume/clobber skips), apply `--max` to that, and size `ProgressReporter` from it (mirror `select_summarizable`/`cmd_summarize`). |
| A4 | S/M | low-med | Config written by `init` may contain a literal API key with default (world-readable) perms; no warning | `cmd_init` can place a literal `api_key` into the generated config (:1851,:1928) and writes via `atomic_write_text` (:2031) which creates files with default umask (typically 0644). The world/group-readable WARNING only fires for `{file:}` opencode key files (:452-462), not for zmm config files holding a literal key. | zoom_meeting_manager.py:96-112, :446-465, :1851, :1918-1940, :2031 | After writing a config that contains a literal key, `chmod 0600` and/or print the same world-readable warning; or steer the wizard to prefer `{env:VAR}`/`{file:}` over literals. |
| A5 | S | low | `base_url` accepted without scheme/TLS validation (plaintext `http://` leaks key) | `base_url` from config/opencode is passed verbatim to `openai.OpenAI(base_url=...)` (:1121-1129). An `http://` endpoint sends the bearer API key in cleartext; no warning. | zoom_meeting_manager.py:1121-1129, :481, :505-508 | Warn (once) when `base_url` scheme is `http://` and host is not localhost. |
| A6 | M | low | `_error_hint` relies on fragile operator precedence and substring matching | `"model" in msg and "not" in msg` (:1338) mixes with `or` clauses; correct only because `and` binds tighter than `or`. A timeout msg like "the model did not respond" would be misclassified as "model not found". | zoom_meeting_manager.py:1323-1344 | Parenthesize the `model/not` condition explicitly and order checks so timeout/auth categories win; add a unit test per category. |
| A7 | B | low | `--max 0` silently means "no limit"; `--max -1` slices from the end | `records[: args.max or None]` → `0 or None` is `None` (full list); negative N yields `lst[:-N]`. `--max` has no `>=0` validation (:3264). Verified. | zoom_meeting_manager.py:3264, :2546, :1508-1510, :1564, :2822 | Validate `--max` is positive (argparse `type=` with check) and treat `0`/negative as an error or explicit "no limit" consistently. |
| A8 | P | low | Failed-response diagnostics persist transcript-derived PII unscrubbed | `save_diagnostic` writes raw model output verbatim to `Diagnostics/` (:1303-1311). Content is model output (no API key), but contains meeting/transcript-derived text that lingers on disk until `clean diagnostics`. | zoom_meeting_manager.py:1303-1311, :2594-2644 | Document retention; optionally note PII in the file header or restrict perms. Acceptable as user-owned data; low priority. |
| A9 | C | low | Module-global `_LAST_USAGE` couples cost accounting to single-threaded execution | `_extract_content` writes a module global `_LAST_USAGE` (:1172,:1201-1202) read later by `ProgressReporter.finish_item` (:2357). Correct for the current serial loops, but silently breaks if calls are ever parallelized. | zoom_meeting_manager.py:1169-1211, :2353-2367 | Return usage from the call path (or attach to the returned object) instead of a global; or add a comment/assert that the loop is serial. |
| A10 | C | low | Journal filename has 1-second resolution → same-second runs collide | `RunJournal.path` uses `%Y%m%dT%H%M%SZ` (:161-162). Two runs of the same operation starting in the same second write the same journal path; `load_resume_done` reads only the most recent (:194-199). | zoom_meeting_manager.py:158-186, :189-201 | Add a pid/random suffix to the journal filename to avoid collision. |
| A11 | M | low | Deprecated predecessor script present in repo (sdist not pruned) | `deprecated/summarize_zoom_transcripts.py` exists; not imported and not in wheel (`py-modules`/`packages.find` exclude it). `MANIFEST.in` prunes review/sessions/tests but not `deprecated/` (non-package py files are not auto-included in sdist, so risk is low). | deprecated/summarize_zoom_transcripts.py, pyproject.toml:40-54, MANIFEST.in | Add `prune deprecated` to `MANIFEST.in` for hygiene; confirm it is not packaged. |

## Candidate actions

| Candidate action | Source candidate IDs | Description | Risk | Validation idea |
|---|---|---|---|---|
| Harden date-range parsing | A1 | Catch `ValueError` from month/day construction and emit a clean `SystemExit`; consider validating at argparse boundary. | low (localized) | Unit test `--date-range 2026-13`, `2026-02-30`, `garbage` → friendly message, exit 2, no traceback. |
| Make non-object model output a handled failure | A2 | Treat non-dict `parse_json_response` result as a parse error (diagnostic + skip), or coerce in `render_summary_text`. | low-med | Test feeding fake client `"[1,2,3]"` / `"42"`; expect skip+diagnostic, not `AttributeError`. |
| Fix clean progress/total + `--max` scope | A3 | Build eligible list before sizing `ProgressReporter`; apply `--max` to eligible items. | low | Test `clean transcripts --max 1` with several merged files; assert total/ETA reflect 1 and only 1 processed. |
| Protect generated config secrets | A4, A5 | `chmod 0600` configs containing literal keys + warn; warn on `http://` base_url. | low | Test wizard with literal key → file mode 0600 + warning; http base_url → warning. |
| Tidy error-hint + `--max` validation | A6, A7 | Parenthesize hint logic; reject negative `--max`. | low | Per-category hint unit tests; `--max -1`/`--max 0` argparse tests. |
| Packaging/state hygiene | A9, A10, A11 | Comment/assert serial usage of `_LAST_USAGE`; pid suffix on journal; `prune deprecated`. | low | Inspect built sdist contents; simulate same-second journal runs. |

## Non-applicable checks

- Unsafe deserialization (`eval`/`exec`/`pickle`/`yaml.load`): none present; only `json.loads` on model output, which is expected. `configparser` used for config. N/A.
- Shell injection: only one `subprocess.run` (vistab) using a list argv, no `shell=True`, no user-controlled command name. N/A.
- SQL/ORM injection, deserialization of network payloads: no database or network deserialization beyond JSON. N/A.
- Bare `except:` swallowing: none found (all are `except Exception`/typed). The broad `except Exception` blocks are mostly intentional fallbacks (token encoder, optional openai import, vistab render, model-list probe) — noted but not flagged.

## Uncertainties

- A2 severity depends on real-world likelihood that a configured model returns valid-but-non-object JSON; with the schema prompt this is uncommon but possible (esp. smaller models). Needs synthesis to weigh against A1.
- A4/A5 severity depends on deployment: literal keys in config and `http://` gateways may be out of intended use. Confirm intended key-storage guidance (README points to `{env:}`/opencode.json).
- A11: confirm whether maintainers intend to ship `deprecated/`. The wheel excludes it; sdist inclusion is unlikely but unverified (no build run performed — read-only).
- `atomic_write_text` cross-device `os.replace` (tmp in same dir as target) is correct; no finding, but note output dirs on exotic mounts could still fail loudly (acceptable).

## Handoff notes

- Strongest correctness candidates: A1 (uncaught traceback on common user typo) and A2 (uncaught `AttributeError` on degenerate model output) — both narrow, test-backed fixes. A3 is a self-consistency/UX bug in `cmd_clean` (progress + `--max` scope) that diverges from the well-structured `cmd_summarize` path and is a good candidate to align.
- Security posture is generally good: no shell/eval/pickle, `_scrub_secret` redacts api_key + `sk-`/`Bearer` tokens in error output, errors go to stderr, `--no-context` keeps personal augmentation off the wire, and the world-readable warning exists for `{file:}` keys. Gaps are A4 (generated-config perms) and A5 (no TLS warning) — both low-effort hardening.
- Deduplicate with other lanes: A11 (deprecated script) likely overlaps a packaging/inventory lane; A8 (diagnostics PII) may overlap a privacy/data-retention lane; A4/A5 (key handling) may overlap a config/security lane.
- All findings are narrow, localized fixes — no rewrite warranted. Tests currently pass (199/1 skipped) and the module compiles; recommend adding regression tests alongside A1/A2/A3 fixes.
