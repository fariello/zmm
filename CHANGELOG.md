# Changelog

All notable changes to zmm are documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Security & Reliability (Part-2 audit)
- Atomic writes for all output artifacts (summaries, transcripts, processing JSON)
  to prevent corruption on interruption.
- Request timeout on all model/API calls (default 600s).
- `summarize` now skips meetings that already have a summary for the model
  unless `--clobber`; both `summarize` and `clean` print an end-of-run summary
  (N done / skipped / failed).
- `--max` now bounds the auto-clean step in `summarize` (no longer cleans more
  than it summarizes).
- `--no-context` flag to keep personal augmentation files off the wire.
- API keys are redacted from model error output.
- `expand_env` no longer mangles literal keys containing `$`.
- User regex in `extract search` is validated with a clean error message.
- `--date-range` colon separator must be space-padded (won't split times).
- Warns when `~/.config/opencode/opencode.json` is malformed instead of silently ignoring.
- `--debug` flag for diagnostic output.
- Documented data egress, diagnostics retention, and key handling in README.

### Added
- `help` subcommand on every compound command (e.g. `zmm list help`).
- `zmm show config` — display active configuration, prompt sources, models, paths.
- `zmm show prompt [--task]` — display the full model directive with color-coded
  source annotations (core vs. user augmentation) plus token/cost estimate.
- `zmm delete raw` — move processed raw meeting directories to `to-delete/`.
- `zmm list models` queries all providers in opencode.json, shows per-provider
  tables with pricing, supports `--provider` filter and `--show-stale`.
- Core + user-augmentation prompt system: bundled core prompts plus optional
  `~/.config/zmm/prompts/*.txt` files (myself, work, people, corrections, style)
  that are auto-appended.
- New summary JSON schema (`schemas/summary.json`) with `improved_title`,
  `one_liner`, `key_takeaways`, and a markdown `detailed_notes` string.
- Lightweight, dependency-free validation of model summary output against the
  schema's required keys (warns + saves a diagnostic; non-fatal).
- Interactive `zmm init config` wizard with opencode.json detection, model
  listing, directory validation, and colored output.
- `auto_clean_before_summarize` config option.
- `aggregate_period` config default for `zmm export aggregates`.
- Progress indicator during inventory scans on slow mounts.
- Cost estimates in confirmation prompts and `zmm estimate`.
- CI workflow running pytest on Python 3.10–3.13.

### Changed
- `migrate legacy` now performs a real import/index of existing output (was a no-op alias).
- `write processing-json` has its own handler with correct messaging.
- `index --rebuild` now discards and rewrites existing processing JSON.
- `extract me|person <kind>` now filters by kind (actions/statements/items)
  using built-in keyword heuristics.
- Tables render without horizontal rules, use terminal width, truncate long titles.
- `corrections.txt` augmentation is skipped when summarizing already-cleaned transcripts.

### Fixed
- Inlined transcript helpers; removed runtime dependency on the deprecated module.
- Deduplicated raw/merged inventory records; stripped caption suffix from titles.
- Midnight-crossing transcripts now merge in correct chronological order.
- Global options (e.g. `--output-dir`) work before or after the subcommand.
- Replaced deprecated `datetime.utcnow()` with timezone-aware UTC timestamps.

### Removed
- Dead config fields: `source`, `no_temperature`, `include_all_model_summaries`,
  and the `[prompt_layers]` section.
- Unimplemented model-task references (`extraction`, `prioritization`, `validation`)
  and their `--*-model` flags.
- Unused bundled prompts `extract_items.txt` and `prioritize_items.txt`.
- Dead `SummaryRecord` fields.

## [0.1.0]

- Initial standalone `zmm` extracted from a larger repository.
- Core CLI: inventory, summarize, clean, report, export, extract.
- MIT licensed, packaged via pyproject.toml, README, tests.
