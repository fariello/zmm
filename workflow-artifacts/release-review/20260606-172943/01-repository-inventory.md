# 01 Repository Inventory

## Current project state summary

`zmm` (Zoom Meeting Manager) is a single-file Python CLI that inventories Zoom
meeting artifacts on disk, merges raw caption/chat into transcripts, optionally
LLM-cleans transcripts, summarizes them via an OpenAI-compatible endpoint,
extracts/searches text locally, reports status, and writes aggregate rollups.
Version 0.1.0 (already tagged `v0.1.0` per session history). Actively hardened
over a "Parts 1–8" release series plus several post-tag fixes.

## Project type and scope

- Type: command-line tool (console_scripts entry `zmm = zoom_meeting_manager:main`).
- Language: Python, `requires-python >=3.10`. Dev/test on venv Python 3.14.4.
- Runtime deps: `tiktoken>=0.5` (required); `openai>=1.0` (optional `[api]` extra).
- Single module: `zoom_meeting_manager.py` (~3561 lines, 126 top-level def/class).
- Bundled data packages: `prompts/` (core + examples), `schemas/summary.json`.

## Public contract summary

- **CLI**: many subcommands — `list` (models/prompts/meetings/missing/...),
  `report` (status/counts), `index`, `migrate legacy`, `write processing-json`,
  `export aggregates`, `init config`, `show config|prompt`, `estimate`,
  `extract search|me|person`, `paths`, `summarize [files]`, `merge raw`,
  `fix missing summaries`, `clean transcripts|diagnostics`, `delete raw`.
- **Output formats**: table / json / csv (machine-parseable contract on stdout).
- **On-disk filename conventions**: `Merged-Transcripts-YYYY/`, `Summaries-YYYY/`,
  `Cleaned-Transcripts-YYYY/`, `*.summary.txt`, `*.summary.json`,
  `YYYY-Meeting-Processing.json` inventory, Diagnostics.
- **Serialized contracts**: `schemas/summary.json` (JSON Schema draft-07) for the
  summary `.json` sidecar; the per-year Processing JSON (schema_version 1).
- **Config**: INI-style config file (`zoom_meeting_manager.cfg.example`), plus
  reading model pricing/keys from `~/.config/opencode/opencode.json`.

## Artifact summary

| Path | Role |
|---|---|
| `zoom_meeting_manager.py` | Entire CLI implementation |
| `zmm` | Thin executable shim |
| `prompts/` | Core model prompts + example augmentation files (importable package) |
| `schemas/summary.json` | Summary output JSON Schema (draft-07) |
| `pyproject.toml` | Packaging (dynamic version, extras, package-data) |
| `MANIFEST.in` | sdist completeness |
| `README.md`, `CHANGELOG.md`, `TODO.md`, `LICENSE` | Docs / license (MIT) |
| `.github/workflows/ci.yml` | CI: matrix 3.10–3.13, py_compile + pytest+cov |
| `tests/` | 200 tests across 8 files + conftest/helpers |
| `deprecated/` | Old `summarize_zoom_transcripts.py` predecessor (tracked) |
| `plans/` | Implementation planning notes (tracked) |
| `sessions/`, `tmp/` | Untracked-ish scratch (sessions/ gitignored; tmp/ gitignored) |

## Test and validation inventory

- `pytest` (testpaths=tests). 200 tests collected; suite green at run start.
- CI runs `python -m py_compile` + `pytest --cov`.
- Files: test_commands (806), test_core (476), test_contract (222),
  test_integration (175), test_parser (120), test_packaging (64),
  conftest (93, fake OpenAI client), helpers (57).

## Documentation inventory

- `README.md` (~16KB): install, config, command table, flags, releasing.
- `CHANGELOG.md` (~12KB): Keep-a-Changelog style, 0.1.0 + post-tag sections.
- `TODO.md`: deferred work.
- `zoom_meeting_manager.cfg.example`: documented config template.

## Build / packaging / CI / release inventory

- setuptools build backend; dynamic version from `__version__`.
- Wheel/sdist ship prompts + schemas (data packages).
- CI present and reasonable; no publish/deploy steps (good).

## Recent changes (this session, pre-review)

- Post-tag fixes: summary_exists any-model semantics; truncation detection +
  `--max-output-tokens`; progress reporter (timestamp/elapsed/ETA/cost);
  output-inclusive cost estimate; `list meetings --has KIND`.

## Drift / inconsistencies (candidate IDs assigned in Section 1 register)

- See `03-findings-register.csv`. Notable initial observations:
  - `deprecated/summarize_zoom_transcripts.py` is tracked and shipped in repo
    but excluded from packaging (py-modules only) — DEP candidate.
  - `plans/` and `sessions/` tracked planning/scratch — DEP/maintainability.
  - CI matrix tops out at 3.13 though dev runs on 3.14 — compatibility note.

## Key ambiguities

- Whether `deprecated/` should remain in the repo for provenance or be removed.
- Whether the runbook's own `release-review/` + `.opencode/` should be committed
  (decision: do NOT commit; they are tooling, not project source — see decisions).

## Recommended next actions

- Proceed with parallel read-only audit lanes (code/security, tests, docs,
  packaging/schema/CI) given the clearly separable surfaces, then synthesize.
