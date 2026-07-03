# Audit Lane Report

## Lane

- Lane ID: C
- Lane name: Documentation/Specs/Examples/Help Text (S4) + Feature/Usability/Maintainability (S5)
- Run ID: 20260606-172943
- Section coverage: S4 + S5
- Read-only: yes
- Created: 2026-06-06 18:19 EDT

## Scope

Read-only audit of the `zmm` CLI at `/home/gfariello/VC/zmm`.

- Section 4: documentation, specs, examples, and help text. README command/flag
  tables vs the actual argparse parser (`build_parser`, lines 3348-3538);
  install/config/first-run accuracy; CHANGELOG structure and post-tag coherence;
  `prompts/examples/` references and conventions; outdated/aspirational claims and
  missing limitations.
- Section 5: feature completeness, CLI usability, operator experience, and
  maintainability/onboarding.

No tracked files were modified. Only this report was written. `--help`-style
commands were run (no network, no disk writes).

## Files and artifacts inspected

- `README.md` (full, 409 lines)
- `CHANGELOG.md` (full, 225 lines)
- `TODO.md`, `pyproject.toml`, `MANIFEST.in`
- `zoom_meeting_manager.cfg.example` (125 lines)
- `schemas/summary.json` (204 lines)
- `prompts/` (core: `meeting_generic.txt`, `output_structured_notes.txt`,
  `cleanup_transcript.txt`; `prompts/examples/*.example.txt`)
- `plans/*.md` (historical markers), `deprecated/`
- `zoom_meeting_manager.py` — parser (`add_common` 3248-3291, `add_model_options`
  3325-3346, `build_parser` 3348-3538), config resolution (416-585),
  augmentation (1000-1103), prompt assembly (1055-1140), summary output
  (3004-3043), progress reporter (2315-2389), confirmation/cost
  (2203-2208, 3190-3218)
- Commands run: `zmm --help`, `zmm summarize merged --help`
- Git: `git tag -l` (v0.1.0), `git log v0.1.0..HEAD` (7 post-tag commits)

## Candidate findings

| Candidate ID | Type | Severity | Title | Evidence | Affected files | Recommended action |
|---|---|---|---|---|---|---|
| D1 | D | medium | Post-tag features documented under the released `[0.1.0]` heading; tag `v0.1.0` predates them | `v0.1.0` = commit `7f11db6`; 7 commits after tag add `list meetings --has`, projected output cost, rich progress, truncation detection (`git log v0.1.0..HEAD`). CHANGELOG.md:66-92 folds these into `## [0.1.0] - 2026-06-06` via "(post-tag)" subsections. README.md:391 release procedure references an `[Unreleased]` section that does not exist | CHANGELOG.md, git tag v0.1.0 | Decide: re-cut tag to include post-tag commits, OR move "(post-tag)" subsections into an `[Unreleased]`/`[0.1.1]` section so the shipped tag and the CHANGELOG agree |
| D2 | D | low | Several implemented flags are undocumented in README | `--prompt-context`/`--prompt-person`/`--prompt-correction` (parser 3334-3339), `--summarization-source` and `--only-cleaned-transcripts` (parser 3490-3491), `--ignore-model-errors` (parser 3278) appear in `zmm summarize merged --help` but are absent from README Global Options (README.md:316-336) and the Prompts section (README.md:268-278, which documents only `--prompt-layer`) | README.md, zoom_meeting_manager.py:3278,3334-3339,3490-3491 | Document or intentionally hide. At minimum add `--clobber`, `--ignore-model-errors` to Global Options and note `--summarization-source` next to the config key |
| D3 | D | low | `--summarization-source` and `--only-cleaned-transcripts` have empty help text | `zmm summarize merged --help` shows both flags with no description; parser 3490-3491 passes no `help=` | zoom_meeting_manager.py:3490-3491 | Add `help=` strings (e.g. mirror cfg.example lines 94-99 for source modes) |
| D4 | D | low | README `list missing` positional choices incomplete | README.md:149 documents `list missing [merged\|summaries\|raw]`; parser choices are `all,merged,summaries,summary-json,raw,transcripts` (zoom_meeting_manager.py:3379) | README.md | Add `summary-json` and the `transcripts` alias (and `all` default) to the doc |
| D5 | D | low | cfg.example config search order omits the legacy fallback paths | cfg.example lines 5-9 list 4 paths; code searches 6 incl. `summarize_zoom_transcripts.cfg` legacy fallback (zoom_meeting_manager.py:422-428). README.md:211-217 documents the legacy fallback; cfg.example does not | zoom_meeting_manager.cfg.example | Add the legacy-config fallback note (or omit from both for simplicity; README already covers it) |
| D6 | D | info | "pip install zmm" / PyPI not yet available — README correctly uses git clone + `pip install -e .` | README.md:54-62 installs from a git clone; TODO.md:16 lists "pip install / PyPI packaging" as Future; pyproject Development Status = Alpha (pyproject.toml:20). README.md:399-400 mentions twine upload as optional | README.md, TODO.md | No change required; confirm no doc implies `pip install zmm` from PyPI works today. (Verified none does.) |
| D7 | D | info | Verified-accurate doc claims (no action) | `--max-output-tokens` default 16000/0=no-cap matches DEFAULT and `--help` (README.md:325-326 vs parser 3284-3288); `list meetings --has KIND` choices match (README.md:148 vs parser 3370-3374); augmentation order `myself,work,people,corrections,style` matches (README.md:243-251 vs code 1002); both `.txt`+`.json` always written (README.md:347 vs code 3040-3041); progress on stderr (README §Operator/CHANGELOG vs code 2319,2328); plans marked historical (plans/*.md headers) | README.md, CHANGELOG.md, zoom_meeting_manager.py | None |
| U1 | U | low | Redundant prompt-layer flags (`--prompt-context/-person/-correction`) behave identically to `--prompt-layer` | All four are appended into one `explicit_layers` list with no distinct handling (zoom_meeting_manager.py:1071-1074, 2125-2128) | zoom_meeting_manager.py | Collapse to a single `--prompt-layer` (repeatable). NOTE: flagged DEFERRED by a prior session; record as usability candidate only, not release-blocking |
| U2 | U | low | Flag-name clarity candidates (prior-session renames, DEFERRED) | `--clobber`→`--overwrite`, `--show-stale`→`--show-unavailable`, `--summarization-source`→`--source` (parser 3276, 3362, 3490). cfg key is `summarization_source` while flag and config differ in nothing but verbosity | zoom_meeting_manager.py | Record as nice-to-have renames for a future minor; explicitly DEFERRED per scope. Not required for release |
| U3 | U | info | Help/error UX is strong | Friendly subcommand errors with `Try: <prog> help` (zoom_meeting_manager.py:3296-3309); failure-specific "Next:" hints (1354, CHANGELOG 62-64); top-level `--help` one-liners present (verified); confirmation prompt with projected cost and `(input only)` fallback (3203-3208) | zoom_meeting_manager.py | None |
| F1 | F | info | Feature set matches stated purpose; documented-but-missing features not found | All README command-table entries resolve to real subparsers (cross-checked README.md:145-199 against parser 3355-3536). `merge raw`, `paths`, `fix missing summaries`, `clean diagnostics`, `migrate legacy`, `--resume` journals all implemented | zoom_meeting_manager.py | None — no aspirational/unimplemented command found in docs |
| M1 | M | low | Single ~3.5k-line module (3561 lines) | Whole CLI in `zoom_meeting_manager.py`; tracked as P5-M2 split in TODO.md:17-22 with rationale (deferred for release-hardening) | zoom_meeting_manager.py, TODO.md | Accept for this release; revisit P5-M2 as a dedicated refactor with the test suite as a net (already tracked) |
| DEP1 | DEP | info | `deprecated/` retains old `summarize_zoom_transcripts.{py,cfg.example}`; legacy `.cfg` still a supported config fallback | deprecated/ dir; legacy fallback at zoom_meeting_manager.py:426-428 | deprecated/, zoom_meeting_manager.py | No action; confirm `deprecated/` is excluded from the wheel/sdist (MANIFEST.in prunes tests/sessions/repository-review but does not list `deprecated/`; `py-modules`+`packages.find` should exclude it — verify in a build audit lane) |

## Candidate actions

| Candidate action | Source candidate IDs | Description | Risk | Validation idea |
|---|---|---|---|---|
| Reconcile CHANGELOG vs tag | D1 | Either re-cut `v0.1.0` to include the 7 post-tag commits, or split post-tag entries into `[Unreleased]`/`[0.1.1]`. Make the release procedure (README.md:391) reflect reality | low (docs/tag only) | `git log v0.1.0..HEAD` should be empty after re-tag, OR CHANGELOG has a section that maps to post-tag commits |
| Document or hide undocumented flags | D2, D3, U1 | Add Global Options entries for `--clobber`/`--ignore-model-errors`; document or collapse `--prompt-context/-person/-correction`; add help text + README note for `--summarization-source`/`--only-cleaned-transcripts` | low | `zmm summarize merged --help` flags all appear somewhere in README; no empty `help=` |
| Fix small README/cfg accuracy gaps | D4, D5 | Complete `list missing` choices in README; add legacy-config note to cfg.example | low | Doc choices == parser choices (3379); cfg.example search order == code (422-428) |
| Track maintainability split | M1 | Keep P5-M2 package split in backlog; not for this release | n/a | TODO.md already tracks it |

## Non-applicable checks

- Network/model-backed runs (`summarize`, `clean`, `list models` live API): out of
  scope for read-only lane; not executed.
- File-writing commands (`init config`, `export`, `index`, `migrate`,
  `clean diagnostics`, `delete raw`): not executed (would write disk).
- Build/wheel-content verification (whether `deprecated/` ships): noted under DEP1
  for a packaging/build lane; not run here (`python -m build` writes disk).
- Pricing/cost numeric correctness: covered by code/security and test lanes.

## Uncertainties

- D1 severity depends on release intent: if the team plans to ship from current
  HEAD and move/re-cut the tag, the CHANGELOG is fine as-is and D1 drops to low/info.
  If `v0.1.0` is immutable/already published, the CHANGELOG mis-attributes features
  to a release that lacks them — needs main-agent decision.
- D2/U1: whether `--prompt-context/-person/-correction` are intentionally kept as
  forward-compat aliases (then document) or should be removed/collapsed (usability).
  They are currently functional but indistinguishable from `--prompt-layer`.
- DEP1: confirm via a build lane whether `deprecated/` is excluded from the
  distribution; not verifiable read-only without building.

## Handoff notes

- Top doc issue to reconcile: **D1** (post-tag features under `[0.1.0]`; tag predates
  7 feature commits; README release steps reference a non-existent `[Unreleased]`).
  This intersects the Releasing section and likely a packaging/release lane — please
  dedupe with any release-process finding from other lanes.
- D2/D3/D4/D5 are low-effort doc fixes; bundle into one docs action.
- U1/U2 are usability candidates explicitly **DEFERRED** per lane scope — record but
  do not mark release-blocking.
- M1 (single-module) is already tracked (P5-M2); confirm it stays out-of-scope for
  this release.
- No documented-but-unimplemented commands or broken example commands were found;
  README command/flag tables otherwise match the parser (see D7 verified list).
- Severity classification for S5 items: F1/U3/M1 = nice-to-have-later or
  out-of-scope; D1 = strongly-recommended-soon (release coherence); D2-D5 =
  strongly-recommended-soon (cheap doc accuracy); U1/U2 = nice-to-have-later
  (deferred).
