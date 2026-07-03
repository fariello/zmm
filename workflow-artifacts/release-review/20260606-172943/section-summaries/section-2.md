# Section 2 Summary — Quality, Security, Edge Cases

Highest-priority fixes:
- B1 (medium): out-of-range `--date-range` (e.g. `2026-13`) raises an uncaught
  `ValueError` traceback — main() has no guard. Verified.
- B2 (medium): a valid-but-non-object model JSON (array/number) passes parsing,
  is only warned, then crashes `render_summary_text` with `AttributeError`.
  Verified.
- B3 (low): clean `ProgressReporter` is sized from records that later get
  skipped → inflated ETA/projected cost.

Security/privacy (low): S1 init may write a literal key at 0644; S2 `http://`
base_url sends key in cleartext with no warning; E3 Diagnostics persist
transcript-derived PII (document it).

Good posture: no `shell=True`/`os.system`/`eval`/`exec`/`pickle`/`yaml`; single
`subprocess.run` uses list argv; `_scrub_secret` redacts keys; `--no-context`
keeps PII off the wire; no bare excepts.

Edge/maint (low): E1 `_error_hint` operator precedence; E2 unvalidated `--max`;
M1 `_LAST_USAGE` global (serial-only). Deprecation: DEP1 deprecated/ (keep).
