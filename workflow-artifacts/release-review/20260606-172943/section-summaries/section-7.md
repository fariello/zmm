# Section 7 Summary — Implementation

Implemented in 5 committed batches (local only; no push):

| Batch | Commit | Scope (action IDs) |
|---|---|---|
| 1 Correctness/robustness | bd310e4 | X1 (B1 date-range clean error), X2/X15 (B2 non-object JSON guard + tests), X3 (B3 clean progress sizing), X6 (E1 error-hint precedence), X7 (M1 comment) |
| 2 Security hygiene | 6504aab | X4 (S2 cleartext base_url warning), X5 (S1 init chmod 600) |
| 3 Tests | 3c3ed3f | X8 (T5 hermetic costs fixture), X9 (T1/T2 estimate+list e2e), X10 (T4 schema conformance + jsonschema dev dep) |
| 4 Docs | e62010e | X12 (D2/D3/D4 flags+help+choices), X13 (D1 [Unreleased] reconciliation), X14 (E3 privacy note) |
| 5 CI/packaging | efc4f99 | X11 (CI1 py3.14, CI2 .[dev] install, CI3 build+install smoke job) |

Intentionally unimplemented (deferred / wont_do): E2 (--max validation), U1/U2
(flag renames — user will revisit; public-surface change), M2 (module split —
tracked in TODO), SCH1 (full runtime jsonschema — best-effort by design),
T3 (extract tests), R1 (cmd_summarize e2e truncation — helper well-tested),
DEP1 (remove deprecated/ — keep for provenance; already excluded from build).

Tests: grew from 200 → 209 (was 199 passed/1 skipped at start; now 209 passed
when the package is installed, 208 passed/1 skipped in a bare checkout). No
public CLI/output/schema/filename contract was broken. The only public-facing
behavior changes are: cleaner error on bad --date-range, a fail-fast on
non-object model JSON (previously a crash), a cleartext-URL warning, init perms
tightening, and more accurate clean progress — all hardening.

Remaining risks: low. See `final-bug-security-audit.md`.
