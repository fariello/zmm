# zmm — Zoom Meeting Manager

A CLI tool for managing Zoom meeting transcripts: inventory, merge, summarize, extract, report, and export.

> Status: alpha (0.1.0). See [CHANGELOG.md](CHANGELOG.md) for changes.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Input Layout](#input-layout)
- [Output Layout](#output-layout)
- [Commands](#commands)
- [Configuration](#configuration)
- [Prompts](#prompts)
- [Person Profiles and Extraction](#person-profiles-and-extraction)
- [Global Options](#global-options)
- [Resuming Interrupted Runs](#resuming-interrupted-runs)
- [Summary Output Schema](#summary-output-schema)
- [Data & Privacy](#data--privacy)
- [License](#license)

## Features

- Discover and inventory raw Zoom meeting exports, merged transcripts, cleaned transcripts, and summaries
- Summarize transcripts using OpenAI-compatible endpoints
- Clean transcripts with LLM assistance
- Extract action items, statements, and mentions by person (local regex search)
- Search across all meeting content with regex
- Report on meeting inventory status and gaps
- Export aggregate files (yearly/monthly rollups)
- Core + user-augmentation prompt system (personal context appended to bundled prompts)
- Pretty table output with optional `vistab` integration

## Requirements

- Python 3.10+
- `tiktoken` (required — accurate token counting for estimates and limits)
- Optional: `openai` package (for model-backed commands like summarize, clean)
- Optional: `vistab` (for pretty terminal tables)

zmm speaks the OpenAI chat-completions protocol. It works with the OpenAI API
directly or with any OpenAI-compatible endpoint (set `base_url`), including
self-hosted gateways and proxies that expose that protocol.

## Installation

```bash
git clone https://github.com/fariello/zmm.git
cd zmm
pip install -e .           # core install (adds the `zmm` command)
pip install -e .[api]      # + OpenAI client for summarize/clean
pip install -e .[dev]      # + openai, pytest, pytest-cov for contributors
# or run directly without installing:
./zmm --help
```

The `./zmm` wrapper is a POSIX shell script. On Windows, use the installed
`zmm` console script (created by `pip install`) or run
`python zoom_meeting_manager.py ...` directly.

## Quick Start

```bash
# 1. Generate a config file (interactive wizard — prompts for paths, model, etc.)
zmm init config            # writes ~/.config/zoom_meeting_manager.cfg by default

# 2. Verify what zmm will use (config sources, models, prompt layers)
zmm show config

# 3. (optional) Inspect the exact prompt that will be sent to the model
zmm show prompt

# 4. See your meeting inventory and what's missing
zmm list missing --input-dir /path/to/zoom --output-dir /path/to/output

# 5. Report counts by year/month
zmm report counts --output-dir /path/to/output

# 6. Summarize merged transcripts
zmm summarize merged --output-dir /path/to/output

# 7. Extract items mentioning you (requires [person.me] in config — see below)
zmm extract me items --output-dir /path/to/output

# 8. Search across all transcripts
zmm extract search --regex "budget|staffing" --output-dir /path/to/output
```

## Input Layout

`--input-dir` should point at the directory where Zoom saves recordings. zmm
expects one folder per meeting, named with Zoom's default convention:

```
<input-dir>/
  2026-01-24 09.00.00 Board Retreat/
    meeting_saved_closed_caption.txt     # captions (required for merge)
    meeting_saved_chat.txt               # chat (optional; merged in chronologically)
  2026-01-25 10.00.00 Budget Meeting/
    meeting_saved_closed_caption.txt
```

The folder name must start with `YYYY-MM-DD HH.MM.SS ` followed by the meeting
title. zmm parses the date/time/title from that name.

## Output Layout

`--output-dir` is where zmm writes everything. It creates these automatically:

```
<output-dir>/
  Merged-Transcripts-YYYY/            # merged caption+chat, one file per meeting
    2026-01-24-...-meeting-saved-closed-caption.txt
  Cleaned-Transcripts-YYYY/           # LLM-cleaned transcripts (zmm clean)
    <stem>.<model>.cleaned.txt
  Summaries-YYYY/                     # LLM summaries (zmm summarize)
    <stem>.<model>.summary.txt        #   human-readable
    <stem>.<model>.summary.json       #   structured (see schema)
  Diagnostics/YYYY/                   # raw failed model responses (zmm clean diagnostics removes)
  <prefix>-Meetings.txt               # aggregate rollups (zmm export aggregates)
  <prefix>-Transcripts.txt
  <prefix>-Meeting-Summaries.txt
  YYYY-Meeting-Processing.json        # inventory metadata (zmm index / write processing-json)
  .zmm-journal/                       # run journals for --resume
  to-delete/                          # raw dirs moved by zmm delete raw
```

The `<model>` segment encodes the model id with `/` replaced by `--`
(e.g. `its_direct--pt3-claude-sonnet`). Inventory re-discovers summaries by
parsing the model back out of these names, so the convention is a stable contract.

## Commands

Every compound command also accepts `help` (e.g. `zmm list help`).

### Inventory & reporting (read-only, no model calls)

| Command | Description |
|---------|-------------|
| `zmm list meetings` | List all discovered meetings |
| `zmm list missing [merged\|summaries\|raw]` | Overview table of missing artifacts |
| `zmm list missing-merged` / `-summaries` / `-raw` | Focused missing-item lists |
| `zmm list models [--provider NAME] [--show-stale]` | List models per provider with pricing |
| `zmm list prompts` | List core + augmentation + example prompts |
| `zmm report status` | Per-meeting status table |
| `zmm report counts [--by year\|month\|both]` | Aggregate counts |
| `zmm index [--rebuild]` | Write inventory metadata (`--rebuild` discards existing) |
| `zmm write processing-json` | Write inventory metadata explicitly |
| `zmm migrate legacy` | Import/index pre-existing on-disk output |
| `zmm estimate summarize\|clean\|extract` | Estimate tokens and cost before model calls |

### Inspection

| Command | Description |
|---------|-------------|
| `zmm show config` | Show active configuration and sources |
| `zmm show prompt [--task summary\|cleanup]` | Show the full model directive with source annotations |

### Transcript preparation

| Command | Description |
|---------|-------------|
| `zmm merge raw` | Merge raw caption+chat into transcripts (local, no model) |

### Model-backed operations

| Command | Description |
|---------|-------------|
| `zmm summarize raw` | Merge and summarize raw meetings |
| `zmm summarize merged` | Summarize existing merged transcripts |
| `zmm summarize files FILE...` | Summarize specific files |
| `zmm fix missing summaries` | Summarize only where summaries are missing |
| `zmm clean transcripts` | LLM-clean merged transcripts |
| `zmm clean diagnostics [--older-than DAYS]` | Delete saved diagnostic files |

### Extraction (local regex search, no model calls)

| Command | Description |
|---------|-------------|
| `zmm extract search --regex PATTERN` | Regex search across transcripts |
| `zmm extract me actions\|statements\|items` | Find lines mentioning you, filtered by kind |
| `zmm extract person actions\|statements\|items --person ID` | Same for a named person profile |

### Output management

| Command | Description |
|---------|-------------|
| `zmm paths [--kind raw\|merged\|cleaned\|summary\|summary-json\|all]` | Print artifact file paths, one per line (pipe-friendly) |
| `zmm export aggregates [--period auto\|year\|month]` | Write rollup files |
| `zmm delete raw` | Move processed raw dirs to `to-delete/` |
| `zmm init config` | Generate a starter config file (interactive wizard) |

## Configuration

`zmm` resolves settings with this precedence (highest first):

1. Command-line flags (e.g. `--output-dir`, `--summary-model`)
2. A zmm config file (see search order below)
3. `~/.config/opencode/opencode.json` (used for API key, base URL, and model
   list/pricing when not set in a zmm config)
4. Built-in defaults

The zmm config file is searched for in this order:

1. `--config PATH`
2. `./zoom_meeting_manager.cfg`
3. `<script_dir>/zoom_meeting_manager.cfg`
4. `~/.config/zoom_meeting_manager.cfg`
5. Legacy `summarize_zoom_transcripts.cfg` (compatibility fallback)

If no zmm config file is found, zmm still works as long as
`~/.config/opencode/opencode.json` provides credentials.

See [`zoom_meeting_manager.cfg.example`](zoom_meeting_manager.cfg.example) for
all options with inline documentation.

## Prompts

zmm uses a two-layer prompt system: **core prompts** (bundled) + **user augmentation** (personal).

### Core prompts (bundled, never edit these)

These ship with zmm in `prompts/` and provide the base instructions:

- `meeting_generic.txt` — core summarization rules
- `output_structured_notes.txt` — JSON output schema instructions
- `cleanup_transcript.txt` — transcript cleaning instructions

### User augmentation (personal, auto-appended)

Place `.txt` files in `~/.config/zmm/prompts/` to add context that makes
summaries better for your specific situation. These are **appended** to the
core prompts — they never replace them.

Recognized augmentation files (appended in this order):

| File | Purpose |
|------|---------|
| `myself.txt` | Who you are: name, aliases, title, role |
| `work.txt` | Organization context: company, teams, acronyms |
| `people.txt` | Common participants: names, titles, relationships |
| `corrections.txt` | Known transcript errors to fix |
| `style.txt` | Style preferences for summary prose (optional) |

Any additional `.txt` files in that directory are also appended.

See `prompts/examples/` for starter templates you can copy and customize.

```bash
# Set up your augmentation files
cp prompts/examples/myself.example.txt ~/.config/zmm/prompts/myself.txt
cp prompts/examples/work.example.txt ~/.config/zmm/prompts/work.txt
# Edit them with your info, then verify what gets sent:
zmm show prompt
```

`corrections.txt` is automatically skipped when summarizing an
already-cleaned transcript (the corrections were applied during cleaning).

### Overriding (rare)

To completely replace the core+augmentation assembly for a run, pass an
explicit prompt layer by name (a bundled prompt name without `.txt`, or a
file you created):

```bash
zmm summarize merged --prompt-layer meeting_generic
```

This skips the normal assembly and uses only the specified layer(s).

## Person Profiles and Extraction

`zmm extract me` and `zmm extract person` perform a **local regex search**
(no model calls) over your transcripts, summaries, and cleaned transcripts.
They need a person profile in your **config file**, which is separate from the
`myself.txt` prompt augmentation:

| Mechanism | Location | Used by | Purpose |
|-----------|----------|---------|---------|
| `[person.me]` | config file | `zmm extract me` | name/aliases for the local regex search |
| `myself.txt` | `~/.config/zmm/prompts/` | `summarize` / `clean` | context sent to the model |

Configure the profile in your config file:

```ini
[user]
default_person = me

[person.me]
display_name = Jane Smith
aliases = Jane, Jayne, J. Smith
```

Then:

```bash
zmm extract me actions     # lines where you are assigned/commit to tasks
zmm extract me statements  # notable statements you made
zmm extract me items       # both
```

`actions` vs `statements` are distinguished by built-in keyword heuristics
(this is a fast local filter, not an LLM classification).

## Global Options

```
--config PATH           Config file path
--input-dir DIR         Raw meeting source directory
--output-dir DIR        Output directory
--date-range RANGE      Filter by date: YYYY, YYYY-MM, YYYY-MM-DD,
                        or a range ('2026-01 to 2026-03', '2026-01-01..2026-01-31')
--match TEXT            Filter by filename substring (case-insensitive)
--max N                 Limit number of items processed
--max-input-tokens N    Abort if estimated input tokens exceed N
--dry-run               Show what would happen without acting
--format FMT            Output format: table, json, csv
--plain                 Disable vistab and colors
--color MODE            Color mode: auto, always, never
--yes                   Skip confirmation prompts
--no-context            Don't send personal augmentation files to the model
--resume                Skip items completed in a prior interrupted run
--debug                 Print diagnostic information
--version               Show version
```

## Resuming Interrupted Runs

If a bulk `summarize` or `clean` run is interrupted or has failures, zmm keeps
a journal under `<output-dir>/.zmm-journal/`. Re-run with `--resume` to retry
only the unfinished items. (Already-written summaries are also skipped by
default unless you pass `--clobber`.)

## Summary Output Schema

Each summary is written as both a human-readable `.summary.txt` and a
structured `.summary.json`. The JSON contract is documented in
[`schemas/summary.json`](schemas/summary.json):

- `meeting` — title, datetime, duration, source path (populated by zmm from the filesystem)
- `model_output` — improved_title, one_liner, high_level_summary, key_takeaways,
  decisions, action_items, open_questions, key_topics, attendees,
  detailed_notes, llm_notes (generated by the model)
- `metadata` — model, prompt label, timestamp, source hash, zmm version

## Data & Privacy

Model-backed commands (`summarize`, `clean`) send data to the configured API
endpoint. Be aware of what leaves your machine:

- **Transcript content** — the full merged/cleaned transcript is sent as the
  user message.
- **Personal context** — files in `~/.config/zmm/prompts/` (your name, role,
  organization, colleagues, etc.) are sent as part of the system prompt.
  Use `--no-context` to suppress them for a given run.
- **Endpoint** — data goes to whatever `base_url` is configured (or the
  default OpenAI endpoint). Point zmm only at endpoints you trust with
  meeting content.

zmm prompts for confirmation before bulk model operations and shows an
estimated token count and cost. Use `--dry-run` to preview without sending
anything.

**Diagnostics retention**: when a model returns invalid JSON or a schema
warning, zmm writes the raw response to `<output-dir>/Diagnostics/YYYY/`.
These files may contain transcript content and are not auto-deleted. Remove
them with `zmm clean diagnostics` when no longer needed.

API keys are read from config or `~/.config/opencode/opencode.json` and are
redacted from error output. They are never written to summaries or logs.

## License

MIT License. See [LICENSE](LICENSE).
