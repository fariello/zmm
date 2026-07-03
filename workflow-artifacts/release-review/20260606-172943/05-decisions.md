# 05 Decisions and Assumptions

| ID | Decision / assumption | Rationale |
|---|---|---|
| 20260606-172943-S1-DEC1 | Do NOT commit `release-review/` or `.opencode/` (untracked). | They are the review tooling delivered via zip, not project source. The runbook says commit only files changed by this run that are project changes; these are scaffolding. Recorded as no-commit. |
| 20260606-172943-S1-DEC2 | `repository-review/` is gitignored, not committed. | Per runbook §139: do not commit run artifacts unless user asks. |
| 20260606-172943-S1-DEC3 | Use controlled parallel read-only audit lanes for S2–S6. | Repo has clearly separable surfaces (code, tests, docs, packaging/schema/CI); lanes improve breadth. Lanes are read-only, use candidate IDs only; main agent owns synthesis + official IDs. |
| 20260606-172943-S1-DEC4 | Treat v0.1.0 as the release under review; do not bump version during review unless a fix requires it. | Matches user's standing preference (no version bump) from prior session. |
| 20260606-172943-S1-DEC5 | Preserve all public CLI/output/schema/filename contracts. | Standing constraint; machine-parseable json/csv must remain stable. |

## Conflicts with protocol
None observed. Section files are consistent with `00-run-protocol.md`.

## Non-applicable judgments (initial)
- Deployment/server/auth surfaces: largely N/A (local CLI). Network surface is
  limited to the OpenAI-compatible client; assessed under security lane.
