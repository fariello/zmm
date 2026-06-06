# Improve `summarize_zoom_transcripts.py` Implementation Plan

> **Historical document.** This file is a point-in-time design/planning artifact from 2026-06-05, written before zmm was extracted into its own repository. It references the old `zoom/`-prefixed paths, the `summarize_zoom_transcripts.py` script, and some prompts/config that have since been removed or relocated. It is kept for history and does **not** describe current behavior. For current docs see [../README.md](../README.md) and [../CHANGELOG.md](../CHANGELOG.md).


Date: 2026-06-05

Script: `zoom/summarize_zoom_transcripts.py`

## TL;DR

The script should be improved by re-architecting it around a shared meeting inventory and processing-data layer, then adding strict model-error behavior, date-range filtering, repair/fix modes, gap-listing modes, reporting modes, annual/monthly/range aggregate export files, and searchable extracted artifacts.

The key implementation decision is to avoid treating these as one-off additions. The current script can be re-architected as needed. First introduce a reusable `MeetingRecord` inventory builder and per-period JSON metadata files that understand raw meeting dirs, merged transcripts, summaries, dates, canonical stems, model provenance, and output paths. Every list/report/fix/export/extraction mode should operate on that shared data model.

## Current State Observed

The script currently supports:

- Default mode: scan raw Zoom meeting directories under `--input-dir`, merge captions/chat into `Merged-Transcripts-YYYY/`, summarize, and optionally move originals/merged files to `to-delete/`.
- `--from-merged`: summarize already merged transcripts under `Merged-Transcripts-YYYY/`.
- `--files`: summarize specific merged transcript files.
- `--status`: report raw/merged/summary status for raw meeting directories.
- `--year`: only limits `--from-merged` discovery to one `Merged-Transcripts-YYYY` directory.
- `--match`: filename substring filter for `--from-merged`.
- Existing summaries are skipped unless `--clobber` is provided.

Important current implementation points:

- Model calls happen in `summarize_transcript()`.
- Model-call exceptions are caught in `summarize_one_transcript()` and currently logged as warnings, then skipped.
- `--list-models` also catches API errors and falls back to a static model list.
- Raw meeting dates are parsed from directory names matching `YYYY-MM-DD HH.MM.SS <title>`.
- Merged transcript years are inferred from `Merged-Transcripts-YYYY/` or leading `YYYY-` filename prefix.
- Summary files are expected under `Summaries-YYYY/` and are matched by filename stem plus `*.summary.txt`.
- Merged transcript filenames are created from the raw directory name plus `meeting_saved_closed_caption.txt`, optionally cleaned.

## Requested Improvements

1. If there is any error calling the model, print a detailed error and exit. Continue only when `--ignore-model-errors` is provided.
2. Add an argument that fixes summaries where transcripts exist without summaries.
3. Add a report showing how many transcripts/summaries exist by month and/or year.
4. Add ability to list items with raw transcripts but no merged transcript.
5. Add ability to list items with merged transcripts but no summaries.
6. Add date-range filtering for lists, reports, info, and actions. Supported inputs should include `YYYY`, `YYYY-MM`, `YYYY-MM to YYYY-MM`, and full ISO date ranges.
7. Add ability to recreate aggregate files by year/month/range, including:
   - `2026-Meeting-Summaries.txt`
   - `2026-Meetings.txt`
   - `2026-Transcripts.txt`
   - `YYYY-MM-...` variants
   - range variants such as `2026-01-24-to-2026-01-30-Transcripts.txt`
8. Rename/reframe the tool as `zoom_meeting_manager.py`, with `zmm` as a convenient alias/entrypoint.
9. Move toward a subcommand interface such as `zmm list missing transcripts` instead of an ever-growing flat flag interface.
10. Make frequently used behavior configurable so models, prompts, aliases, extraction profiles, directories, and output preferences do not need to be specified every run.

## Design Goals

- Fail closed on model errors by default.
- Make gap detection reliable and reusable.
- Avoid duplicate directory/file/date matching logic.
- Keep existing default behavior compatible unless a new mode/flag is used.
- Make date filtering consistent across status, lists, fix actions, from-merged processing, files mode, and aggregate export.
- Keep destructive behavior explicit and dry-run-compatible.
- Prefer simple stdlib-only implementation. The existing script already uses third-party `openai` and `termcolor`; do not add more dependencies.
- Re-architect the script where necessary rather than layering fragile mode-specific patches on top of the current flow.
- Preserve model provenance for every generated summary.
- Make all read-only `--list-*` modes do exactly one thing: list matching items and exit without model calls, file writes, or config side effects unless config is strictly needed for local metadata interpretation.
- Make end-user error messages actionable: explain what failed, why it likely failed, what input caused it, and what command/config change might fix it.
- Prefer a command/subcommand UX for the new script: `zmm list missing transcripts`, `zmm fix missing summaries`, `zmm report counts`, `zmm export aggregates`, etc.
- Support separate default models for separate jobs: summary, transcript cleanup, extraction, prioritization, and possibly validation.

## Proposed CLI Changes

### Model Error Handling

Add:

```text
--ignore-model-errors
```

Default behavior:

- Any exception raised by `client.chat.completions.create(...)` exits the program with non-zero status.
- The error output should include operation, model name, transcript path or meeting label, exception class, exception message, base URL if configured, prompt label, whether temperature retry was attempted, and suggested next step where obvious.

With `--ignore-model-errors`:

- Preserve old behavior: warn and continue to the next model/transcript.
- At end of run, print a compact model-error summary and ideally exit non-zero if no summaries were produced. This should be decided during implementation.

Settled behavior:

- Model/list API failures are fatal by default whenever the user requested an API-backed operation.
- `zmm list models` should list models and exit. If it cannot do that, it should fail with a useful actionable error instead of silently falling back to static guesses.
- `--ignore-model-errors` applies to summarization/cleanup/action-extraction model calls. It should not make `--list-models` pretend it succeeded.

### Fix Missing Summaries

New command:

```text
zmm fix missing summaries
```

Behavior:

- Finds merged transcripts without summaries and summarizes only those.
- Should operate on `Merged-Transcripts-YYYY/*.txt`, not raw directories.
- Should honor configured summary model/prompt defaults plus CLI overrides such as `--summary-model`, `--model`, `--prompt`, `--match`, `--max`, `--dry-run`, `--clobber`, and `--date-range`.
- Should not overwrite existing summaries unless `--clobber` is supplied.
- Should be equivalent to a filtered merged-transcript summarization mode, but clearer and safer.

Settled behavior:

- If `--model` is supplied, “missing summaries” means missing for at least one selected model.
- If `--model gpt-4o o4-mini` and only the `gpt-4o` summary exists, fix should create only the missing `o4-mini` summary.
- If `--model` / `--summary-model` is omitted for `zmm fix missing summaries`, load the configured summary model and fix summaries for that model.

### Gap Lists

New commands:

```text
zmm list missing transcripts
zmm list missing summaries
```

Behavior:

- `zmm list missing transcripts`: list raw meeting directories that have caption/chat files but no expected merged transcript.
- `zmm list missing summaries`: list merged transcripts that have no corresponding summary for the selected model(s), or no summaries at all if no model is selected.
- Both should honor date range, `--match`, `--max`, and `--no-clean-names` where relevant.
- Both should be read-only and make no API calls.

Settled behavior:

- All `zmm list ...` modes list and exit.
- List modes should never call a model and should never do any processing beyond local inventory discovery/filtering.
- `zmm list missing summaries` should not load config just to discover the default model. If no model filter is provided, it lists merged transcripts with no summary files at all. If a model filter is supplied, it lists merged transcripts missing summaries for the supplied model(s).

### Inventory/Status Report

New commands/options:

```text
zmm report status
zmm report counts --by {year,month,both}
```

Recommendation:

- Keep legacy `--status` as compatibility behavior that maps to `zmm report status`.
- Use `zmm report counts` for aggregate counts.
- Default `--by both` because the user asked “by month and/or year.”

Report should include at least:

- raw meetings with caption/chat
- merged transcripts
- summary files
- meetings with any summary
- merged transcripts missing summaries
- raw meetings missing merged transcripts

Suggested output:

```text
Meeting Inventory Report
Date filter: 2026-01-01..2026-12-31

By year:
Year  Raw  Merged  SummaryFiles  MeetingsWithSummary  MissingMerged  MissingSummary
2026   42      40            37                   35              2               5

By month:
Month    Raw  Merged  SummaryFiles  MeetingsWithSummary  MissingMerged  MissingSummary
2026-01    8       8             7                    7              0               1
2026-02    6       5             5                    5              1               0
```

### Date Range Filtering

Add one primary flag:

```text
--date-range RANGE
```

Accepted forms:

```text
2026
2026-01
2026-01 to 2026-03
2026-01-24 to 2026-01-30
2026-01-24..2026-01-30
2026-01-24:2026-01-30
```

Optional convenience aliases:

```text
--since YYYY-MM-DD
--until YYYY-MM-DD
```

Recommendation:

- Implement only `--date-range` first to keep the CLI small.
- Accept separators `to`, `..`, and `:`.
- Normalize internally to inclusive `date` bounds: `(start_date, end_date)`.

Expansion rules:

- `YYYY` means `YYYY-01-01` through `YYYY-12-31`.
- `YYYY-MM` means first through last day of that month.
- `YYYY-MM to YYYY-MM` means first day of first month through last day of second month.
- `YYYY-MM-DD to YYYY-MM-DD` means exact inclusive range.
- Mixed precision may be allowed, e.g. `2026-01 to 2026-01-30`; if allowed, left side expands to first day and right side expands to exact day.

Validation:

- Invalid dates should be parser errors with examples.
- Start after end should be a parser error.
- Date filtering should use meeting date, not filesystem mtime.

Where date filter must apply:

- `--status`
- `--report`
- `--list-missing-merged`
- `--list-missing-summaries`
- `--fix-missing-summaries`
- `--from-merged`
- `--files`
- default raw processing mode
- aggregate export mode

### Aggregate Export/Recreate Files

Add:

```text
--recreate-aggregate-files
--aggregate-period {year,month,range,auto}
```

Recommendation:

- Use `--recreate-aggregate-files` as the action flag.
- Use `--date-range` to select content.
- Use `--aggregate-period auto` by default.

Auto naming rules:

- If `--date-range 2026`, prefix is `2026`.
- If `--date-range 2026-01`, prefix is `2026-01`.
- If exact range is supplied, prefix is `YYYY-MM-DD-to-YYYY-MM-DD`.
- If no `--date-range` is supplied and `--aggregate-period year`, write one set per detected year.
- If no `--date-range` is supplied and `--aggregate-period month`, write one set per detected month.

Files to generate:

```text
<prefix>-Meeting-Summaries.txt
<prefix>-Meetings.txt
<prefix>-Transcripts.txt
```

Suggested content:

- `<prefix>-Meeting-Summaries.txt`: concatenated summary files, sorted by meeting date, with clear separators and source path headers.
- `<prefix>-Meetings.txt`: meeting index only: date, title, raw dir path, merged path, summaries present/missing.
- `<prefix>-Transcripts.txt`: concatenated merged transcripts, sorted by meeting date, with clear separators and source path headers.

Overwrite behavior:

- “Recreate” implies overwrite, but current script uses `safe_write()` backup behavior.
- Recommendation: use `safe_write()` so existing aggregate files are backed up to `.001`, `.002`, etc.
- Honor `--dry-run` by printing what would be generated without writing.

## Internal Implementation Plan

### Phase 1: Add Shared Date Parsing

Add helpers:

```python
def parse_partial_date(value: str, is_end: bool = False) -> date:
    ...

def parse_date_range(value: str | None) -> tuple[date | None, date | None]:
    ...

def date_in_range(value: date | None, start: date | None, end: date | None) -> bool:
    ...
```

Notes:

- Use stdlib only: `datetime.strptime`, `calendar.monthrange`.
- If a meeting has no parseable date, default behavior should be to exclude it when a date range is provided and include it when no date range is provided.
- Print a warning for undated records excluded by date filter if `--debug` is active.

### Phase 2: Introduce Inventory Data Model

Add a small dataclass:

```python
@dataclass
class MeetingRecord:
    meeting_date: date | None
    meeting_datetime: str | None
    title: str
    key: str
    raw_dir: str | None
    caption_path: str | None
    chat_path: str | None
    merged_path: str | None
    summary_paths: list[str]
    summaries: list[SummaryRecord]
    expected_merged_path: str | None
    expected_summary_paths: dict[str, str]
    cleaned_transcript_paths: list[str]
    extraction_paths: dict[str, str]

@dataclass
class SummaryRecord:
    path: str
    model: str | None
    prompt_label: str | None
    created_at: str | None
    source_transcript_sha256: str | None
    summary_sha256: str | None
```

Add helper functions:

```python
def parse_meeting_dir_name(dir_name: str) -> tuple[date | None, str | None, str]
def expected_merged_filename(dir_name: str, args) -> str
def merged_stem_from_path(path: str) -> str
def date_from_merged_path(path: str) -> date | None
def discover_raw_records(args, selected_models: list[str] | None = None) -> list[MeetingRecord]
def discover_merged_records(args, selected_models: list[str] | None = None) -> list[MeetingRecord]
def build_inventory(args, selected_models: list[str] | None = None) -> list[MeetingRecord]
```

Important:

- Do not rely only on raw meeting dirs. Some already-processed meetings will only exist as merged transcripts after originals were moved to `to-delete/`.
- Inventory should union raw-derived records and merged-derived records by canonical key.
- Canonical key should preferably be date + cleaned title/stem, not full path, to match raw and merged versions.
- Summary detection should account for sanitized model names (`/` -> `--`) using existing `make_summary_filename()`.
- Summary detection should preserve model provenance. New summaries should write explicit model/prompt metadata; older summaries can be inferred from filename when needed.
- The inventory layer should be the canonical internal interface for status, reports, missing lists, fix modes, aggregate exports, JSON metadata, and extraction features.

### Phase 3: Make Model Errors Fatal by Default

Add:

```python
class ModelCallError(RuntimeError):
    ...
```

Change `summarize_transcript()`:

- It may still catch `openai.BadRequestError` specifically for temperature rejection and retry without temperature.
- Any failure of the retry must be treated as a fatal model error unless `--ignore-model-errors` is set higher up.
- Consider detecting only temperature-parameter BadRequest, not all BadRequest errors. Current code retries any BadRequest, which may hide real prompt/model/config errors.

Change `summarize_one_transcript()`:

- On model error, call `format_model_error(...)` and either:
  - raise `SystemExit(1)` by default, or
  - warn and continue if `args.ignore_model_errors`.
- Keep file I/O errors separate from model errors. The user's request specifically says model errors; non-model errors can remain skip/warn unless we decide otherwise.

Add helper:

```python
def print_model_error(exc, *, model, transcript_path, base_url, prompt_label, operation, temperature_retry):
    ...
```

### Phase 4: Wire Date Filtering into Existing Modes

Add parsed range to args after parsing:

```python
args.date_start, args.date_end = parse_date_range(args.date_range)
```

Default raw processing:

- Before processing a raw meeting directory, parse date from `dir_name` and skip if outside range.

`--from-merged`:

- Replace `--year`-only filtering with date filtering.
- Preserve `--year` as compatibility alias for `--date-range YYYY`.
- If both `--year` and `--date-range` are supplied, parser should error unless they describe the same range. Recommendation: error to keep behavior clear.

`--files`:

- Infer date from filename/header; skip files outside date range.

`--status`:

- Use inventory and filter records before printing.

### Phase 5: Add List/Report Modes

In `main()`, before loading config/model unless a mode actually needs API access:

- `--status`, `--report`, `--list-missing-merged`, `--list-missing-summaries`, and `--recreate-aggregate-files` should not require API config.
- For `--list-missing-summaries`, if `--model` is supplied, use those model names as local filename/metadata filters. If no `--model` is supplied, list merged transcripts with no summary files at all.
- All `--list-*` modes must list and exit. They should not summarize, clean, extract, write files, call APIs, or silently continue into another mode.

Implement:

```python
def list_missing_merged(args):
    ...

def list_missing_summaries(args, selected_models: list[str] | None):
    ...

def report_inventory(args, selected_models: list[str] | None):
    ...
```

Output should be stable and grep-friendly:

```text
2026-01-24  Board Retreat  raw=/... merged_missing expected=/...
```

### Phase 6: Add Fix Missing Summaries

Implement as a wrapper around `summarize_one_transcript()` using inventory:

```python
def fix_missing_summaries(args, selected_models, config):
    records = build_inventory(args, selected_models)
    for record in records:
        if not record.merged_path:
            continue
        missing_models = ...
        summarize_one_transcript(..., selected_models=missing_models, ...)
```

Behavior details:

- Requires `--output-dir`.
- Uses `--input-dir` as the root containing `Merged-Transcripts-YYYY/` if provided; otherwise use `--output-dir`.
- Honors `--dry-run`.
- Honors `--max`.
- Does not touch raw files or merged files.
- Never moves merged transcripts.

### Phase 7: Add Aggregate File Generation

Implement:

```python
def recreate_aggregate_files(args, selected_models: list[str] | None):
    records = build_inventory(args, selected_models)
    groups = group_records_for_aggregate(records, args)
    for prefix, group in groups.items():
        write_meetings_file(prefix, group, args)
        write_transcripts_file(prefix, group, args)
        write_summaries_file(prefix, group, args)
        write_processing_json(prefix, group, args)
```

Sorting:

- Sort by `meeting_date`, then title, then path.

File separators:

```text
================================================================================
Meeting: <title>
Date: <date>
Merged transcript: <path>
Summary files: <paths>
================================================================================

<content>
```

Summary selection:

- If selected models are supplied, include only those summary files.
- If no selected models are supplied, include all summary files.
- Preserve existing summary text exactly; do not re-summarize.

Settled behavior:

- `Meeting-Summaries.txt` should include multiple model summaries per meeting when multiple exist, with model-labeled subsections, unless `--model` filters them.

### Processing Metadata JSON

Add per-period JSON metadata files, starting with one file per year:

```text
<YYYY>-Meeting-Processing.json
```

Optional month/range variants may also be generated when aggregate files are generated for those scopes:

```text
<YYYY-MM>-Meeting-Processing.json
<YYYY-MM-DD-to-YYYY-MM-DD>-Meeting-Processing.json
```

Purpose:

- Track which model created each summary.
- Track prompt name/string provenance where feasible.
- Track source transcript paths and output paths.
- Track processing status without repeatedly deriving everything from filenames.
- Support future reporting and repair modes reliably.

Suggested schema:

```json
{
  "schema_version": 1,
  "generated_at": "2026-06-05T00:00:00Z",
  "period": {
    "label": "2026",
    "start_date": "2026-01-01",
    "end_date": "2026-12-31"
  },
  "meetings": [
    {
      "id": "2026-01-24-09-00-00-board-retreat",
      "date": "2026-01-24",
      "datetime": "2026-01-24 09.00.00",
      "title": "Board Retreat",
      "raw_dir": "/path/to/raw/meeting",
      "caption_path": "/path/to/meeting_saved_closed_caption.txt",
      "chat_path": "/path/to/meeting_saved_chat.txt",
      "merged_transcript_path": "/path/to/Merged-Transcripts-2026/...txt",
      "summaries": [
        {
          "path": "/path/to/Summaries-2026/...gpt-4o.summary.txt",
          "model": "gpt-4o",
          "prompt_label": "default",
          "created_at": "2026-06-05T00:00:00Z",
          "source_transcript_sha256": "...",
          "summary_sha256": "..."
        }
      ],
      "extractions": {
        "gabriel_action_items_path": "/path/to/...Gabriel-Action-Items.txt",
        "gabriel_statements_path": "/path/to/...Gabriel-Statements.txt"
      },
      "status": {
        "has_raw_transcript": true,
        "has_merged_transcript": true,
        "has_any_summary": true,
        "missing_summary_models": []
      }
    }
  ]
}
```

Implementation notes:

- Add a summary header field for model and prompt metadata when writing summary files. Existing summary files can be indexed by filename model inference until regenerated.
- Include file hashes so the JSON can detect stale summaries if a merged transcript changes later.
- Do not require JSON to be the source of truth initially. It should be regenerated from disk inventory plus enriched with metadata known during this run.
- Regenerate affected processing JSON files automatically after state-changing commands by default.
- Also provide `zmm write processing-json` to rebuild metadata explicitly without summarizing or modifying transcripts/summaries.
- If automatic JSON writing fails after successful transcript/summary generation, print a detailed warning by default and include the recovery command. Consider a future `--strict-metadata` mode to make metadata-write failures fatal.

Recommended behavior:

- After `summarize`, `fix missing summaries`, `clean transcripts`, `extract ...`, and `export aggregates`, rewrite the affected yearly JSON files.
- For month/range outputs, write period JSON when the command explicitly targets that period.
- Keep JSON derived from filesystem inventory plus known in-memory run metadata, not as the only source of truth.

Why this approach:

- Automatic updates keep reports and later commands useful without requiring the user to remember bookkeeping.
- Explicit rebuild gives an easy repair path after manual file moves or old data imports.
- Treating the filesystem as source of truth avoids corrupt/stale JSON breaking the whole tool.

Why not other options:

- Manual-only JSON regeneration is easy to forget and makes reports stale.
- JSON-as-primary-database is overkill and fragile for a file-oriented workflow.
- Fatal metadata-write failures after successful expensive LLM calls would be frustrating; warnings plus a repair command are more user-friendly.

### Script Naming and Command Interface

Settled direction:

- New primary script: `zoom_meeting_manager.py`.
- Convenience alias/entrypoint: `zmm`.
- Keep `summarize_zoom_transcripts.py` as a thin compatibility wrapper for at least one transition period.
- The wrapper should delegate to the new implementation and can print a deprecation note when appropriate.

The current name, `summarize_zoom_transcripts.py`, no longer fits once the tool can inventory, repair, report, aggregate, extract action items, clean transcripts, and manage metadata.

Use subcommands rather than continuing to grow a flat flag surface.

Examples:

```text
zoom_meeting_manager.py list missing transcripts
zoom_meeting_manager.py list missing summaries
zoom_meeting_manager.py fix missing summaries
zoom_meeting_manager.py report counts --date-range 2026
zoom_meeting_manager.py export aggregates --date-range 2026-01
zoom_meeting_manager.py extract gabriel items --date-range 2026
zoom_meeting_manager.py extract search --regex "budget|staffing|contract"

zmm list missing transcripts
zmm list missing summaries
zmm fix missing summaries
zmm report counts --date-range 2026
zmm export aggregates --date-range 2026-01
zmm extract gabriel items --date-range 2026
zmm extract search --regex "budget|staffing|contract"
```

Subcommand structure:

```text
zmm list missing transcripts
zmm list missing
zmm list missing merged
zmm list missing summaries
zmm list missing raw
zmm list models
zmm report status
zmm report counts
zmm fix missing summaries
zmm summarize raw
zmm summarize merged
zmm summarize files FILE [FILE ...]
zmm export aggregates
zmm write processing-json
zmm extract gabriel actions
zmm extract gabriel statements
zmm extract gabriel items
zmm extract me actions
zmm extract me statements
zmm extract me items
zmm extract person actions --person PERSON
zmm extract person statements --person PERSON
zmm extract person items --person PERSON
zmm extract search --regex PATTERN
zmm clean transcripts
zmm index
zmm index --rebuild
zmm migrate legacy
zmm estimate summarize
zmm estimate clean
zmm estimate extract --person PERSON
zmm init config
```

Implementation notes:

- Use `argparse` subparsers in the new implementation.
- Keep compatibility flags in `summarize_zoom_transcripts.py` only as wrapper behavior.
- The `zmm` alias can be a small shell wrapper, symlink, or installed console script later. In-repo, a tiny executable wrapper named `zmm` can call `zoom_meeting_manager.py`.
- New documentation should lead with the subcommand interface, not the legacy flags.

### Gabriel/Gabriele Action and Statement Extraction

Add an extraction feature for content specifically useful to Gabriel/Gabriele Fariello:

```text
zmm extract gabriel actions
zmm extract gabriel statements
zmm extract gabriel items
zmm extract search --regex PATTERN
```

Possible behavior:

- `zmm extract gabriel actions`: produce items where someone asked Gabriel/Gabriele to do something, assigned work to him, or where he accepted/committed to a task.
- `zmm extract gabriel statements`: produce notable statements made by Gabriel/Gabriele, including decisions, commitments, concerns, constraints, preferences, and factual claims.
- `zmm extract gabriel items`: convenience mode that produces both.
- `zmm extract search`: produce useful candidate snippets from custom literal/regex searches.

Possible output files:

```text
<prefix>-Gabriel-Action-Items.txt
<prefix>-Gabriel-Statements.txt
<prefix>-Gabriel-Items.json
```

Suggested JSON item schema:

```json
{
  "type": "action_item",
  "meeting_id": "2026-01-24-09-00-00-board-retreat",
  "date": "2026-01-24",
  "speaker": "Jane Doe",
  "target": "Gabriel Fariello",
  "text": "Gabriel will send the revised budget by Friday.",
  "due_date": "2026-01-30",
  "confidence": "high",
  "source": {
    "transcript_path": "/path/to/transcript.txt",
    "line_start": 123,
    "line_end": 126
  }
}
```

Implementation options:

- Heuristic-only extraction first: regex/search around speaker labels and names. Cheap, private, fast, but less accurate.
- LLM extraction: more accurate, can infer implied tasks and normalize due dates, but costs money and sends data to a model.
- Hybrid recommendation: implement local search/listing first, then optional LLM extraction with explicit `--use-model` or selected extraction model.

Important search names:

- Gabriel
- Gabriele
- Gabriel Fariello
- Gabriele Fariello
- likely transcript variants/misrecognitions should be configurable later.

Recommended extraction mechanism:

1. Build inventory for the selected date range.
2. Prefer existing summaries as the first extraction source because they are smaller and already structured.
3. Parse summary sections using stable headings from the existing prompt, especially `Action Items / To-Do List`, `Key Decisions`, `Open Questions / Follow-Up Items`, and `Detailed Notes`.
4. Identify candidate items using configured person aliases, speaker regexes, task verbs, and user-provided regex/search patterns.
5. For each candidate from a summary, optionally locate supporting transcript context by scanning the merged transcript for nearby names, task terms, quoted phrases, or topic keywords.
6. For transcript-only extraction, scan merged transcripts directly and collect bounded windows around matches rather than sending entire transcripts to an LLM.
7. Deduplicate candidates across summaries/transcripts by normalized text, meeting ID, date, and approximate source line.
8. If LLM refinement is enabled, send only candidate packets and their bounded context windows to the extraction model.
9. Ask the extraction model to classify each candidate as action item, statement, decision, question, blocker, or irrelevant.
10. Ask the prioritization model, which may be the same as the extraction model, to rank action items by urgency, importance, deadline, and confidence.

Candidate packet sent to LLM:

```json
{
  "meeting_id": "2026-01-24-09-00-00-board-retreat",
  "date": "2026-01-24",
  "title": "Board Retreat",
  "candidate_source": "summary",
  "candidate_reason": "matched person alias and action section",
  "candidate_text": "Gabriel will send the revised budget by Friday.",
  "summary_section": "Action Items / To-Do List",
  "transcript_context": [
    {"line": 118, "text": "Jane: Gabriel, can you send the revised budget by Friday?"},
    {"line": 119, "text": "Gabriel: Yes, I can do that."}
  ]
}
```

LLM extraction output should be strict JSON:

```json
{
  "items": [
    {
      "type": "action_item",
      "person": "Gabriel Fariello",
      "task": "Send the revised budget.",
      "requested_by": "Jane Doe",
      "due_date": "2026-01-30",
      "priority": "high",
      "confidence": "high",
      "reasoning_brief": "Requester explicitly asked Gabriel and Gabriel accepted.",
      "source_lines": [118, 119]
    }
  ]
}
```

Search/regex mode:

```text
zmm extract search --regex "budget|staffing|contract" --date-range 2026
zmm extract search --person gabriel --regex "follow up|send|review|schedule"
zmm extract gabriel items --llm-refine
zmm extract gabriel items --llm-refine --prioritize
```

Search mode should support:

- literal search
- case-insensitive regex search
- person profile filtering from config
- source selection: summaries, transcripts, cleaned transcripts, or all
- context windows before/after each match
- optional LLM refinement on only the candidate windows

Recommended default Gabriel/Gabriele extraction profile:

- Person aliases: `Gabriel`, `Gabriele`, `Gabriel Fariello`, `Gabriele Fariello`, plus configurable variants.
- First-person speaker matches when the speaker label matches the Gabriel/Gabriele profile.
- Assignment/request patterns near person aliases: `Gabriel.*(can you|could you|please|will you|would you|need you to|follow up|send|review|schedule|draft|prepare|own|take|circle back)`.
- Commitment patterns from Gabriel/Gabriele-labeled speech: `(I will|I'll|I can|I'll take|I'll follow up|I'll send|I'll review|I'll schedule|let me|I need to|I'll own)`.
- Ownership patterns: `(owner|owned by|assigned to|responsible for|action item).*Gabriel` and `Gabriel.*(owner|responsible|assigned)`.
- Deadline patterns in the same candidate window: `(by|before|due|deadline|next week|tomorrow|Friday|end of week|EOW|COB)`.
- Statement patterns from Gabriel/Gabriele-labeled speech for decisions/preferences/constraints: `(I think|I recommend|my concern|I don't think|I agree|I disagree|we should|we need|the issue is|the risk is|my preference)`.

Why this profile:

- It combines person identity, task/commitment language, and bounded context, which gives better precision than name-only matching.
- It catches both cases where someone assigns Gabriel a task and cases where Gabriel accepts or volunteers for a task.
- It supports statement extraction separately from action extraction.
- It is configurable, so other users can replace the person profile and verbs without code changes.

Why not other options:

- Name-only search is too noisy for mentions and misses tasks implied by speaker context.
- Verb-only search catches too many unrelated tasks owned by other people.
- Sending whole transcripts to an LLM is expensive, slower, less private, and unnecessary when summaries/structured JSON already identify most candidates.
- A huge hard-coded regex list will become brittle; keep defaults modest and make profiles configurable.

Prompt changes to make extraction easier:

- For the new `zmm` pipeline, prefer strict JSON-only model responses for summary generation.
- The script should validate the JSON, then render both human-readable summary text and machine-readable sidecar JSON.
- This means normal extraction can read generated JSON directly without going back to an LLM.
- Do not require old summaries to have structured JSON; extraction must still work from current summary headings and transcripts as a fallback.

Recommended summary-generation flow:

```text
merged/cleaned transcript -> summary model returns strict JSON -> script validates JSON -> script writes .summary.txt and .summary.json
```

Recommended summary JSON should include:

```json
{
  "meeting": {
    "title": "...",
    "datetime": "YYYY-MM-DD HH:MM:SS or null",
    "duration": "HH:MM:SS or null",
    "attendees_present": [],
    "attendees_mentioned": [],
    "ambiguities": []
  },
  "high_level_summary": "...",
  "decisions": [
    {
      "decision": "...",
      "status": "final|tentative",
      "owner": "... or null",
      "confidence": "high|medium|low",
      "source_refs": []
    }
  ],
  "action_items": [
    {
      "task": "...",
      "owner": "... or Owner unclear",
      "requested_by": "... or null",
      "deadline": "YYYY-MM-DD or null",
      "priority_hint": "high|medium|low|null",
      "confidence": "high|medium|low",
      "source_refs": []
    }
  ],
  "open_questions": [],
  "risks_blockers_dependencies": [],
  "key_topics": [],
  "detailed_notes": [],
  "person_specific_items": {
    "gabriel": {
      "actions": [],
      "statements": [],
      "mentions": []
    }
  },
  "llm_notes": {
    "assumptions": [],
    "uncertain_corrections": [],
    "uncertain_speaker_attribution": []
  }
}
```

Legacy/minimal prompt adjustment for compatibility:

```text
For each action item, use this exact format:
- Task: <task>
  Owner: <owner or Owner unclear>
  Requested by: <person or Unknown>
  Deadline: <deadline or None stated>
  Confidence: <high|medium|low>
```

Why this approach:

- JSON-only model output is easier and safer for the script to validate than parsing prose.
- The script can render consistent Markdown/text summaries from structured data.
- Extraction, aggregate exports, processing JSON, and search indexes can all reuse the same structured summary data.
- It avoids a second LLM call for normal Gabriel/Gabriele item extraction.

Why not other options:

- Markdown-only summaries are pleasant for humans but fragile for programmatic extraction.
- Markdown with an embedded JSON block is better, but still requires extracting/repairing mixed-format output.
- Text designed to be parsed into JSON is usually the worst of both worlds: less readable than good prose and less reliable than strict JSON.
- JSON-only files without rendered text are less user-friendly, so the script should render `.summary.txt` from validated JSON.

### Configuration File

The new tool should move commonly repeated choices into a config file so routine commands do not need repeated model, prompt, directory, alias, and output arguments.

Config search order:

```text
1. --config PATH
2. ./zoom_meeting_manager.cfg
3. <script_dir>/zoom_meeting_manager.cfg
4. ~/.config/zoom_meeting_manager.cfg
5. existing summarize_zoom_transcripts.cfg as compatibility fallback
```

Recommended format:

- Use stdlib `configparser` INI format.
- It is easier to organize than the current flat `key = value` file.
- It avoids adding dependencies.

Example:

```ini
[paths]
input_dir = /path/to/zoom/raw
output_dir = /path/to/zoom/output

[api]
base_url = https://llmgw.its.uri.edu/v1
api_key = {env:OPENAI_API_KEY}
no_temperature = true

[models]
summary = its_direct/pt1-nova-2-lite-us
cleanup = its_direct/pt1-qwen3-32b-us
extraction = its_direct/pt1-qwen3-32b-us
prioritization = its_direct/pt1-qwen3-32b-us
validation =

[prompts]
summary = default
cleanup = cleanup_transcript
extraction = extract_action_items
prioritization = prioritize_gabriel_items

[person.gabriel]
display_name = Gabriel Fariello
aliases = Gabriel, Gabriele, Gabriel Fariello, Gabriele Fariello
speaker_regexes = ^Gabriel\b, ^Gabriele\b, ^G\. Fariello\b
search_regexes = Gabriel|Gabriele|Fariello

[extraction]
default_person = gabriel
candidate_window_before = 6
candidate_window_after = 10
prefer_summaries_first = true
llm_refine = false
max_candidate_chars_per_meeting = 12000

[output]
write_processing_json = true
aggregate_period = auto
include_all_model_summaries = true

[transcripts]
summarization_source = cleaned_if_available
auto_clean_before_summarize = false
```

Model usage:

- `models.summary` is used for summary generation and `fix missing summaries`.
- `models.cleanup` is used for LLM transcript cleanup.
- `models.extraction` is used to classify/extract action items and statements from candidate snippets.
- `models.prioritization` is used to rank extracted action items by importance/urgency.
- `models.validation` can later be used for optional consistency checks.

Transcript source behavior:

- `transcripts.summarization_source = cleaned_if_available` should be the default.
- If a cleaned transcript exists and matches the current source hash, summarization uses it.
- If no cleaned transcript exists, summarization uses the canonical merged transcript.
- `transcripts.auto_clean_before_summarize = false` should be the default initially, so summarization does not unexpectedly add an extra LLM cleanup call.
- Users can override via config or command line.
- The tool should support reprocessing only cleaned transcripts so existing cleaned transcript artifacts can be summarized again without re-cleaning or touching canonical merged transcripts.

Suggested CLI overrides:

```text
--summarization-source cleaned_if_available|cleaned|required_cleaned|merged
--auto-clean-before-summarize
--no-auto-clean-before-summarize
--only-cleaned-transcripts
```

Command-line overrides:

- `--model` should remain as a shorthand for summary model in summarization/fix commands.
- Add task-specific overrides where needed: `--summary-model`, `--cleanup-model`, `--extraction-model`, `--prioritization-model`.
- CLI overrides config. Config overrides built-in defaults.

Compatibility:

- Existing `summarize_zoom_transcripts.cfg` should still be read as a fallback for `api_key`, `base_url`, `default_model`, and `no_temperature`.
- New config should write/read richer sections but not force immediate migration.

### Prompt Architecture and Generic Usability

The current prompt contains a useful mix of generic Zoom-meeting instructions and Gabriel/URI-specific background. The new system should separate those layers so other users can get the same benefit without editing code or rewriting the whole prompt.

Prompt layers:

```text
1. Generic meeting-analysis prompt
2. Output schema prompt
3. Organization/project context prompt
4. Person profile prompt
5. Recurring transcript correction prompt
6. Command-specific prompt additions
```

Recommended prompt files:

```text
prompts/
  meeting_generic.txt
  summary_schema.json
  cleanup_transcript.txt
  extract_items.txt
  prioritize_items.txt
  contexts/
    uri.txt
    generic_example.txt
  people/
    gabriel.txt
```

Config example:

```ini
[prompts]
generic_meeting = meeting_generic
summary_schema = summary_schema.json
cleanup = cleanup_transcript
extraction = extract_items
prioritization = prioritize_items

[context]
organization = uri
project =
recurring_corrections = uri_common_terms

[person.gabriel]
prompt_context = people/gabriel.txt
```

Benefits:

- Generic users can use the tool with no Gabriel/URI-specific content.
- Gabriel can keep URI-specific corrections and personal extraction settings in config/context files.
- Prompts become easier to test and evolve independently.
- The same core summary schema can work across organizations.

Recommended built-in generic behavior:

- Ship a generic meeting-analysis prompt that assumes unreliable Zoom transcripts but no specific organization.
- Ship an example context file showing how to add organization-specific corrections like acronyms, names, recurring mistranscriptions, and stakeholder roles.
- Keep Gabriel-specific profile/config local or clearly optional.

Potential future feature:

- `zmm init config` could generate a starter config and ask the user for their name, aliases, organization, common acronyms, preferred models, and default directories.

### Generic User Model

The new system should not be hard-coded as Gabriel's meeting tool. It should support Gabriel's workflow through configuration while remaining generic for other users.

Design principle:

- The tool should understand configurable people, organizations, prompt contexts, transcript sources, and output preferences.
- Gabriel-specific behavior should be expressed as a default/local config profile, not as code or required prompt content.

Generic user-facing concepts:

- `person`: a configured person profile.
- `me`: an alias to the user's default person profile.
- `organization`: optional context profile.
- `project`: optional context profile.
- `source`: transcript source adapter.
- `profile`: a named bundle of config defaults.

Example config:

```ini
[user]
default_person = gabriel
default_profile = uri

[person.gabriel]
display_name = Gabriel Fariello
aliases = Gabriel, Gabriele, Gabriel Fariello, Gabriele Fariello
prompt_context = people/gabriel

[profile.uri]
organization_context = contexts/uri
corrections = corrections/uri
```

Generic commands:

```text
zmm extract person actions --person gabriel
zmm extract person statements --person gabriel
zmm extract person items --person gabriel
zmm extract me actions
zmm extract me statements
zmm extract me items
```

Recommendation:

- Keep `zmm extract gabriel ...` as an optional alias only if useful locally.
- Document generic `person` and `me` commands as the primary interface.

### Source Adapters

The architecture should support multiple transcript sources, even if the first implementation only supports Zoom.

Initial source:

```text
source = zoom
```

Potential future sources:

```text
source = generic_txt
source = vtt
source = srt
source = teams
source = otter
source = google_meet
```

Source adapter responsibilities:

- Discover candidate meeting files/directories.
- Parse source metadata such as meeting title, date/time, participants, captions, chat, and timestamps.
- Produce canonical internal meeting records.
- Produce canonical merged transcript text.
- Preserve source paths and source-specific metadata in processing JSON.

Suggested interface:

```python
class SourceAdapter:
    source_name: str

    def discover(self, input_dir: str) -> list[SourceMeeting]:
        ...

    def parse(self, source_meeting: SourceMeeting) -> ParsedMeeting:
        ...

    def merge(self, parsed_meeting: ParsedMeeting) -> MergedTranscript:
        ...
```

Why this matters:

- Keeps Zoom-specific filename assumptions out of the core inventory/report/export/extraction logic.
- Makes the tool useful to people with Teams, Otter, VTT, SRT, or generic transcript files later.
- Makes testing easier because generic fixtures can be adapter-specific.

### Config Wizard / `zmm init config`

`zmm init config` should be treated as an important user-friendly feature, not a distant nice-to-have.

Purpose:

- Help a new user create a working config without reading the entire README or editing many settings by hand.
- Make generic setup easy while still supporting Gabriel's URI-specific workflow.

Suggested command:

```text
zmm init config
zmm init config --profile uri
zmm init config --output ~/.config/zoom_meeting_manager.cfg
```

Questions to ask:

- Where are raw meeting exports stored?
- Where should outputs be written?
- Which transcript source should be used? Default: `zoom`.
- Which API endpoint should be used?
- Which API key environment variable should be referenced?
- Which model should summarize meetings?
- Which model should clean transcripts, if any?
- Which model should extract/prioritize action items, if any?
- Should cleanup be enabled?
- Should summarization prefer cleaned transcripts when available?
- What is your name?
- What aliases or transcript variants should identify you?
- What organization/project context should be included?
- What recurring transcript corrections should be known?

Output:

- Write a config file.
- Optionally create starter context/person prompt files.
- Print next commands, for example `zmm report counts`, `zmm summarize raw --dry-run`, and `zmm list missing summaries`.

Recommendation:

- Include `zmm init config` in the first `zoom_meeting_manager.py` implementation if feasible.
- If not feasible, include a complete example config and keep the code structured so the wizard can be added soon after.

### Person Profiles and `me` Alias

Person profiles should be a first-class concept.

Why:

- The user wants automatic extraction of things assigned to them and things they said.
- Other users should be able to get the same benefit without editing code.
- Multiple people may be tracked for the same meeting archive.

Config model:

```ini
[user]
default_person = gabriel

[person.gabriel]
display_name = Gabriel Fariello
aliases = Gabriel, Gabriele, Gabriel Fariello, Gabriele Fariello
speaker_regexes = ^Gabriel\b, ^Gabriele\b, ^G\. Fariello\b
assignment_verbs = can you, could you, please, need you to, follow up, send, review, schedule, draft, prepare, own, take, circle back
commitment_patterns = I will, I'll, I can, I'll take, I'll follow up, I'll send, I'll review, I'll schedule, let me, I need to, I'll own
statement_patterns = I think, I recommend, my concern, I agree, I disagree, we should, we need, the issue is, the risk is, my preference
prompt_context = people/gabriel
```

Commands:

```text
zmm extract me actions
zmm extract me statements
zmm extract me items
zmm extract person actions --person gabriel
zmm extract person statements --person gabriel
zmm extract person items --person gabriel
```

Summary JSON:

Prefer generic person-specific arrays over hard-coded keys:

```json
"person_specific_items": [
  {
    "person_id": "gabriel",
    "display_name": "Gabriel Fariello",
    "actions": [],
    "statements": [],
    "mentions": []
  }
]
```

Recommendation:

- Generate person-specific summary items for configured people, especially the default `me` profile.
- Avoid hard-coded `gabriel` keys in schemas.

### Index and Migration Mode

The new system needs an explicit way to index or migrate existing output files.

Why:

- Existing merged transcripts and summaries already exist.
- Old summaries may not have structured JSON sidecars.
- Model names may only be inferable from filenames.
- Processing JSON should be rebuildable from disk at any time.

Commands:

```text
zmm index
zmm index --rebuild
zmm migrate legacy
```

Behavior:

- Discover raw meeting directories.
- Discover merged transcripts.
- Discover cleaned transcripts.
- Discover summaries.
- Infer models from summary filenames where possible.
- Hash raw/merged/cleaned/summary files.
- Identify stale summaries where source transcript hashes no longer match.
- Identify orphaned summaries with no merged transcript.
- Identify merged transcripts without summaries.
- Write or refresh processing JSON.

Recommendation:

- `zmm index` should be safe, local, and read-only except for writing metadata.
- `zmm migrate legacy` may do optional renames or sidecar generation, but should default to dry-run or require confirmation.

### Cost, Privacy, and Confirmation Controls

Model-backed bulk operations should make cost/privacy visible and controllable.

Commands:

```text
zmm estimate summarize --date-range 2026
zmm estimate clean --date-range 2026-01
zmm estimate extract --person me
```

Options:

```text
--yes
--no-confirm
--max-cost AMOUNT
--max-input-tokens N
--max-output-tokens N
--show-payload-preview
--save-payloads DIR
```

Behavior:

- Before model-backed bulk operations, estimate files, meetings, input tokens, output tokens, and cost when model pricing is available.
- Show what categories of data will be sent: transcript text, cleaned transcript text, summary JSON, candidate snippets, person context, organization context.
- Confirm before sending unless `--yes` or config disables confirmation.
- Abort if estimated cost exceeds `--max-cost`.
- For extraction refinement, send only candidate snippets and bounded context windows, not full transcripts, unless explicitly requested.

Why this matters:

- Meeting transcripts may contain sensitive information.
- Bulk operations can unexpectedly cost money.
- Users should understand what leaves their machine.

### Structured JSON Validation and Repair

Strict JSON output is the right target, but model responses can fail.

Required behavior:

- Validate model JSON against the expected schema.
- If invalid, print a useful error that identifies the meeting, model, prompt, and parse/schema failure.
- Optionally attempt one JSON repair call or local cleanup pass.
- If repair fails, exit non-zero unless `--ignore-model-errors` is provided.
- Save the invalid raw model response to a diagnostic file when safe and useful.

Suggested files:

```text
Diagnostics/YYYY/<meeting>.<model>.summary.invalid-response.txt
Diagnostics/YYYY/<meeting>.<model>.summary.schema-errors.txt
```

Recommendation:

- Start with JSON parse validation and required top-level fields.
- Add full JSON Schema validation later if needed, using stdlib-friendly checks first to avoid dependencies.

### Command Naming Precision

Some friendly command names can be ambiguous. The system should prefer precise canonical commands and optionally support aliases.

Precise canonical commands:

```text
zmm list missing
zmm list missing merged
zmm list missing summaries
zmm list missing raw
```

Friendly aliases:

```text
zmm list missing transcripts -> alias for zmm list missing merged
```

`zmm list missing` behavior:

- Show a single overview table across matching meetings/items.
- Include checks and Xs for key artifacts.
- Use ANSI colors for quick scanning.
- Use `vistab` for table rendering when available.
- If `vistab` is not available, fall back to a plain aligned table and print a short suggestion to install it.
- Respect `--date-range`, `--match`, `--max`, source filters, and configured output/input dirs.

All `zmm list ...` commands should use the same pretty-printing system, not just missing-item lists.

This includes at least:

- `zmm list missing`
- `zmm list missing merged`
- `zmm list missing summaries`
- `zmm list missing raw`
- `zmm list missing transcripts` alias
- `zmm list models`
- `zmm list prompts`
- future list commands such as `zmm list meetings`, `zmm list summaries`, `zmm list cleaned`, `zmm list actions`, and `zmm list people`

The specific commands should show focused pretty tables rather than plain line lists.

Suggested focused tables:

```text
zmm list missing merged

Date        Title                         Raw  Expected Merged Path                         Problem
2026-01-25  Budget Meeting                ✓    /.../Merged-Transcripts-2026/...txt          missing merged
```

```text
zmm list missing summaries

Date        Title                         Merged  Cleaned  Expected Model       Summary Path                         Problem
2026-01-25  Budget Meeting                ✓       -        gpt-4o               /.../Summaries-2026/...summary.txt   missing summary
```

```text
zmm list missing raw

Date        Title                         Indexed  Merged  Summary  Raw Path                              Problem
2026-01-25  Budget Meeting                ✓        ✓       ✓        /.../raw/...                          missing raw source
```

Rendering should be centralized in a table-rendering helper so report/list/status commands are consistent.

Shared list rendering requirements:

- Pretty table by default.
- `vistab` when available.
- ANSI colors by default when stdout is interactive.
- Plain aligned fallback when `vistab` is unavailable.
- Friendly `vistab` install suggestion when unavailable and stdout is interactive.
- `--plain` to force no external table renderer and minimal styling.
- `--format table|json|csv` for machine-readable use.
- `--color auto|always|never` for color control.
- Stable column names for scripting.

Suggested columns:

```text
Date        Title                         Raw  Merged  Cleaned  Summary  JSON  Actions  Problems
2026-01-24  Board Retreat                 ✓    ✓       ✗        ✓        ✓     ✗        missing cleaned, missing actions
2026-01-25  Budget Meeting                ✓    ✗       ✗        ✗        ✗     ✗        missing merged, missing summary
```

Recommended symbols/colors:

- Green `✓` for present/current.
- Red `✗` for missing.
- Yellow `!` for stale, ambiguous, or needs attention.
- Dim/gray `-` for not applicable.

Suggested `vistab` integration:

- Detect with `shutil.which("vistab")`.
- If present, render rows through `vistab`.
- If absent, use internal plain-table fallback.
- If absent and stdout is interactive, print: `Tip: install vistab for nicer tables: <install command or project link>`.
- Do not make `vistab` a hard dependency.

Example commands:

```text
zmm list missing
zmm list missing --date-range 2026
zmm list missing --date-range 2026-01 --match budget
zmm list missing --no-color
zmm list missing --plain
```

Related output controls:

```text
--color auto|always|never
--plain
--format table|json|csv
```

Definitions:

- `missing raw`: a meeting/index item exists but raw source transcript files are absent.
- `missing merged`: raw source transcript/chat exists but canonical merged transcript is absent.
- `missing summaries`: merged or cleaned transcript exists but expected summary is absent.

Recommendation:

- Documentation should use precise names first.
- Aliases can exist for discoverability, but command output should explain exactly what was checked.
- All list commands should be formatted tables by default, with `vistab`/ANSI color support and plain/json/csv fallbacks.

### Optional LLM Transcript Cleanup

Question: Does it make sense to have an additional LLM call to clean up transcripts before making them merged?

Answer: Maybe, but it should not happen before chronological merge. The safer sequence is:

1. Parse captions and chat.
2. Merge chronologically into the canonical merged transcript.
3. Optionally run cleanup on the merged transcript to produce a separate cleaned transcript artifact.
4. Summarize from the cleaned transcript by default when a current cleaned transcript exists; otherwise use the canonical merged transcript unless config/CLI requires cleaned input.

Recommended flags:

```text
--clean-transcripts-with-model
--cleaned-transcript-source {merged,raw}
--summarize-cleaned-transcripts
--summarization-source cleaned_if_available|cleaned|required_cleaned|merged
--cleanup-model MODEL
--cleanup-prompt PROMPT
```

Recommended outputs:

```text
Cleaned-Transcripts-YYYY/<meeting>.<model>.cleaned.txt
```

Important safeguards:

- Never replace the canonical merged transcript with LLM-cleaned text.
- Store cleaned transcript as a derivative artifact with model/prompt metadata.
- Make the cleanup prompt conservative: fix obvious transcription errors, punctuation, speaker labels, and formatting; do not summarize; do not omit material; mark uncertain corrections.
- Add hashes linking cleaned transcript to the source merged transcript.
- Add cost/error handling exactly like summarization.

Recommendation:

- Defer LLM cleanup until after the inventory/report/fix foundation is working.
- It is useful, but it increases cost, latency, and risk of silent content alteration.

Detailed cleanup design:

- Cleanup should use `models.cleanup` from config unless `--cleanup-model` is provided.
- Summarization should use `models.summary` unless `--summary-model` / `--model` is provided.
- Summarization source should default to `cleaned_if_available`, with config and CLI override.
- `required_cleaned` should fail if no current cleaned transcript exists. This is useful when the user wants to ensure all summaries come from cleaned inputs.
- `merged` should force canonical merged transcripts even if cleaned transcripts exist.
- Cleanup output should include a metadata header and JSON metadata entry recording cleanup model, prompt, source transcript hash, cleaned transcript hash, and creation time.
- Cleanup should operate on merged transcripts by default because chronological merge removes duplicated caption/chat structure first.
- Cleanup should be idempotent: if a cleaned transcript already exists for the same source hash, model, and prompt, skip unless clobber/force is requested.
- Cleanup should preserve line references when possible. If exact line preservation is impossible, store source chunk identifiers.
- Cleanup should be chunked for long transcripts, with overlap between chunks to avoid losing context at boundaries.
- Chunked cleanup should be followed by a stitching pass that removes duplicated overlap and checks for missing sections.
- The script should report token/cost estimates before cleanup when possible.

Cleanup prompt guardrails:

- Fix punctuation, capitalization, obvious transcription errors, repeated fragments, and speaker-label formatting.
- Preserve all substantive content.
- Do not summarize.
- Do not omit decisions, action items, questions, concerns, or disagreements.
- Do not invent names, owners, dates, or facts.
- Mark uncertain corrections inline or in a short notes section.
- Preserve timestamps/source line markers if provided.

Suggested cleanup flow:

```text
zmm clean transcripts --date-range 2026-01 --cleanup-model MODEL
zmm summarize merged --date-range 2026-01
zmm summarize merged --date-range 2026-01 --summarize-cleaned-transcripts
```

## Parser/Dispatch Design

The new `zoom_meeting_manager.py` should use `argparse` subparsers rather than more top-level boolean flags.

Top-level shared options:

```text
--config PATH
--input-dir DIR
--output-dir DIR
--date-range RANGE
--match TEXT
--regex PATTERN
--max N
--dry-run
--debug
--clobber
--ignore-model-errors
```

Task-specific model overrides:

```text
--summary-model MODEL
--cleanup-model MODEL
--extraction-model MODEL
--prioritization-model MODEL
```

Primary subcommands:

```text
zmm list missing transcripts
zmm list missing
zmm list missing summaries
zmm list models
zmm report status
zmm report counts
zmm fix missing summaries
zmm summarize raw
zmm summarize merged
zmm summarize files FILE [FILE ...]
zmm export aggregates
zmm write processing-json
zmm extract gabriel actions
zmm extract gabriel statements
zmm extract gabriel items
zmm extract search
zmm clean transcripts
```

Legacy compatibility:

- `summarize_zoom_transcripts.py --from-merged ...` should continue to work initially.
- The compatibility wrapper can translate legacy flags into the new internal command model.
- New capabilities should be documented using the `zmm <verb> <object>` style.

Dispatch order in `main()`:

1. Parse args and date range.
2. Build inventory for local modes when needed.
3. Handle local/read-only modes and exit: `list prompts`, `report status`, `report counts`, `list missing transcripts`, `list missing summaries`.
4. Handle local/write modes and exit when selected: `export aggregates`, `write processing-json`, heuristic-only extraction modes.
5. Load config only when needed for API-backed operations: `list models`, summarization, LLM cleanup, LLM extraction, or default model resolution for action modes.
6. Handle `list models`; if the API call fails, print an actionable fatal error and exit non-zero.
7. Resolve selected models.
8. Dispatch model-using modes.

Model-using modes:

- `fix missing summaries`
- `clean transcripts`
- LLM-backed Gabriel/Gabriele extraction
- `summarize files`
- `summarize merged`
- `summarize raw`

## Testing Plan

Manual fixture tree under `/tmp/opencode` or a repo-local ignored scratch directory:

```text
input/
  2026-01-24 09.00.00 Board Retreat/
    meeting_saved_closed_caption.txt
  2026-01-25 10.00.00 Budget Meeting/
    meeting_saved_closed_caption.txt
output/
  Merged-Transcripts-2026/
    2026-01-24-09.00.00-Board-Retreat-meeting_saved_closed_caption.txt
  Summaries-2026/
    2026-01-24-09.00.00-Board-Retreat.o4-mini.summary.txt
```

Test cases:

- `zmm report counts --date-range 2026` includes 2026 records only.
- `zmm report counts --date-range 2026-01` includes January 2026 only.
- `zmm report counts --date-range '2026-01-24 to 2026-01-30'` includes inclusive range.
- Invalid `--date-range` exits with parser error.
- `zmm list missing transcripts` finds raw dirs without merged files.
- `zmm list missing` renders an overview table with colored checks/Xs and uses `vistab` when available.
- `zmm list missing summaries` finds merged files without summary files.
- `zmm report counts --by both` reports correct counts.
- `zmm fix missing summaries --dry-run` reports intended API calls without writing.
- Model API exception exits by default with detailed error.
- Model API exception continues with `--ignore-model-errors`.
- Aggregate files are written with expected names and sorted content.
- Processing JSON records model, prompt, source, summary path, and hashes.
- `zmm list models` failure exits non-zero with useful diagnostics.
- `zmm extract gabriel actions` and `zmm extract gabriel statements` produce searchable text/JSON outputs.
- `zmm extract search --regex PATTERN` produces bounded source-context matches.
- LLM transcript cleanup writes derivative cleaned transcript files and never overwrites canonical merged transcripts.

If adding automated tests is too much for this script, at minimum add a `--dry-run`-based manual test checklist in comments or a plan note.

## Risks and Edge Cases

- Filename matching may be fragile because cleaned raw-dir names must match merged transcript names exactly. The inventory layer should centralize this logic.
- Existing summary matching by prefix may overmatch similarly named meetings. Prefer expected summary filenames for selected models when possible.
- Some meetings may have summaries but no merged transcript if files were manually moved. The report should count these separately or warn.
- Some merged transcripts may not contain parseable dates in filename or parent dir. They should be included in no-date-range reports and excluded when a date range is active.
- `--year` currently means only `--from-merged`. Replacing it with general date filtering could surprise users. Keep `--year` as an alias initially, but document that `--date-range` is preferred.
- `--list-models` currently falls back on model-list API failure. This should change: if the user requested model listing and the script cannot list models, it should fail with a clear actionable error.
- “Transcripts” could mean raw captions/chats or merged transcripts. For aggregate `YYYY-Transcripts.txt`, recommendation is merged transcripts, because they are the cleaned canonical transcript used for summaries.
- Aggregate files can become very large. Consider printing byte/line counts after writing.
- If multiple summaries per meeting exist, aggregate summary files can be noisy. Allow `--model` to filter.
- LLM transcript cleanup may accidentally alter meaning. Keep canonical merged transcripts immutable and treat cleaned transcripts as derivative artifacts.
- Action-item extraction could create false positives around ambiguous speaker names or transcript errors. Include source lines and confidence where possible.
- The script uses `termcolor` and OpenAI SDK despite docstring goals. Do not try to solve dependency cleanup as part of this change unless requested.

## Recommended Implementation Sequence

1. Create `zoom_meeting_manager.py` as the new implementation and keep `summarize_zoom_transcripts.py` as a compatibility wrapper.
2. Add the `zmm` alias/wrapper.
3. Add config loading with sections for paths, API, task-specific models, prompts, person profiles, extraction, and output defaults.
4. Add subcommand parsing with `argparse` subparsers.
5. Add date-range parsing helpers and CLI validation.
6. Add inventory data model plus summary/model provenance records.
7. Add processing JSON write/regeneration support.
8. Refactor legacy `--status` behavior to use inventory.
9. Add fatal API/model error behavior plus `--ignore-model-errors`; make `list models` fatal when it cannot list models.
10. Add `list missing` as an overview table with colored checks/Xs and `vistab` support; add precise missing-item commands: `list missing merged`, `list missing summaries`, and `list missing raw`; keep `list missing transcripts` as a friendly alias if desired.
11. Add `report status` and `report counts` from inventory.
12. Add date filtering to raw, merged, and file summarization paths.
13. Add `fix missing summaries` using inventory and existing summarization function.
14. Add `index`, `index --rebuild`, and legacy migration support.
15. Add cost/privacy estimate commands and confirmation controls for model-backed operations.
16. Add structured JSON validation and diagnostic output for invalid model responses.
17. Add aggregate file recreation, including processing JSON refresh.
18. Add generic person-profile extraction: `extract me ...` and `extract person ...`.
19. Add Gabriel/Gabriele config profile as a local/default profile, not a hard-coded command dependency.
20. Add configurable search/regex extraction.
21. Add optional LLM refinement/prioritization for extracted candidate items.
22. Add optional LLM transcript cleanup as a later phase.
23. Add source adapter abstraction; implement Zoom first.
24. Add `zmm init config` or, if deferred, keep the config code structured to support it soon.
25. Update prompts, documentation, argparse examples, and wrapper/deprecation text.
26. Run manual fixture tests and at least one dry-run against real directories.

## Settled Decisions

1. The script may be re-architected as needed; these features should not be implemented as one-off patches.
2. Track which model was used to create each summary.
3. Add JSON processing data, initially by year, with model/prompt/source/output metadata.
4. `--fix-missing-summaries` should use the configured default model when `--model` is omitted.
5. `--list-*` modes must list and exit without model calls.
6. `YYYY-Transcripts.txt` should contain merged transcripts, not raw Zoom captions/chats.
7. Aggregate summary files should include multiple model summaries unless filtered by `--model`.
8. Undated records are included when no date filter is active and excluded when a date filter is active.
9. `--list-models` should fail loudly and actionably when it cannot list models.
10. Exact expected summary filenames should be preferred over broad prefix matching where possible.
11. Aggregate files should report byte/line counts after writing.
12. Date filtering uses meeting date, not filesystem modification time.
13. Aggregate files should be written to the `--output-dir` root.
14. New primary script is `zoom_meeting_manager.py`.
15. Add `zmm` as an alias/entrypoint.
16. Use subcommands such as `zmm list missing transcripts` and `zmm fix missing summaries`.
17. Implement the new architecture in the new script, with the old script as compatibility wrapper.
18. Config should cover anything commonly repeated: paths, models, prompts, person aliases, extraction behavior, and output defaults.
19. Different tasks may use different models: summary, cleanup, extraction, prioritization, and validation.
20. Gabriel/Gabriele extraction should happen automatically through a configured person profile, and users should also be able to run custom search/regex extraction.
21. Cleaned transcripts should be derivative artifacts and should not replace canonical merged transcripts.
22. The new summary pipeline should ask the LLM for strict JSON and have the script render human-readable summary text plus sidecar JSON.
23. Normal extraction should read structured summary JSON first and avoid a second LLM call unless optional refinement/prioritization is requested.
24. Default Gabriel/Gabriele extraction should use a moderate configurable profile combining aliases, assignment/request patterns, commitment patterns, ownership patterns, deadline patterns, and statement patterns.
25. Summarization should default to `cleaned_if_available`, with config and CLI overrides to force cleaned, require cleaned, or force merged transcripts.
26. Processing JSON should regenerate automatically after state-changing commands and also support explicit `zmm write processing-json` rebuilds.
27. Prompt design should be layered into generic meeting instructions, output schema, organization/project context, person profiles, recurring corrections, and command-specific additions.
28. Gabriel/URI-specific background should live in configurable context/profile files, not in the generic core prompt.
29. Person names and aliases must be configurable, not hard-coded to Gabriel/Gabriele.
30. The recommended default person extraction profile should be implemented.
31. Summary generation should use strict JSON internally and render human-readable text from validated JSON.
32. Summarization should support reprocessing only cleaned transcripts, including existing cleaned transcripts.
33. Prompt layers should be implemented now in the existing script as a stepping stone toward `zoom_meeting_manager.py`.
34. Person-specific commands should be generic (`me` / `person`) rather than hard-coded to Gabriel.
35. `me` should resolve from config.
36. The architecture should support source adapters, with Zoom as the first adapter.
37. `zmm init config` is important for user-friendliness and should be included early if feasible.
38. Existing outputs need an index/migration path.
39. Model-backed bulk operations need explicit cost/privacy estimates and confirmation controls.
40. Strict JSON model responses need validation, diagnostics, and a repair/failure path.
41. Use precise command names such as `list missing merged`, with friendly aliases only where useful.
42. `zmm list missing` should show a colored overview table of artifact presence/missingness, using `vistab` if available and a plain fallback otherwise.

## Remaining Open Questions Before Coding

1. Should config be allowed to make optional LLM extraction refinement automatic, or should LLM refinement always require an explicit CLI flag?
2. Should summary JSON include a `person_specific_items` section for every configured person profile by default, or only for the default person?
3. What exact cleanup prompt should be used for LLM transcript cleanup?
4. Should metadata-write failures after successful LLM calls remain warnings with repair instructions, or should there be a default strict mode?
5. Should `zmm init config` be part of the first implementation, or deferred until the main workflow is stable?
6. Which non-Zoom source adapter should be implemented second, if any: generic text, VTT, SRT, Teams, Otter, or Google Meet?
7. Should model response repair use a second LLM call, local repair only, or both depending on config?

## Implementation Closeout Report

At the end of implementation, generate a concise report for the user.

The report should include:

- What was implemented.
- What changed from the IPD, if anything.
- Commands added or changed.
- Config keys added or changed.
- Prompt files added or changed.
- Output files/directories added or changed.
- Backward-compatibility notes for `summarize_zoom_transcripts.py`.
- Verification performed, including command outputs or summarized results.
- Known limitations or deferred items.
- Any manual migration/indexing steps the user should run.
- Suggested next actions.

Suggested next actions may include:

- Run `zmm init config` or review the generated config.
- Run `zmm index --rebuild` to inventory existing outputs.
- Run `zmm list missing` to inspect gaps.
- Run `zmm estimate summarize` or `zmm estimate clean` before model-backed bulk operations.
- Run a small dry-run over one month before processing all years.
- Review prompt/context/person files for organization-specific assumptions.
- Commit the changes once verified.
