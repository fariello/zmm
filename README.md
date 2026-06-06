# zmm — Zoom Meeting Manager

A CLI tool for managing Zoom meeting transcripts: inventory, merge, summarize, extract, report, and export.

## Features

- Discover and inventory raw Zoom meeting exports, merged transcripts, and summaries
- Summarize transcripts using OpenAI-compatible APIs (OpenAI, AWS Bedrock, etc.)
- Extract action items, statements, and mentions by person
- Search across all meeting content with regex
- Report on meeting inventory status and gaps
- Clean transcripts with LLM assistance
- Export aggregate files (yearly/monthly rollups)
- Layered prompt system with composable context/correction/person profiles
- Pretty table output with optional `vistab` integration

## Requirements

- Python 3.10+
- `tiktoken` (required — accurate token counting for estimates and limits)
- Optional: `openai` package (for model-backed commands like summarize, clean)
- Optional: `vistab` (for pretty terminal tables)

## Installation

```bash
git clone https://github.com/fariello/zmm.git
cd zmm
pip install -e .           # installs zmm command
# or just use directly:
./zmm --help
```

## Quick Start

```bash
# Initialize a config file
zmm init config

# Edit the config with your paths and API key
$EDITOR ~/.config/zoom_meeting_manager.cfg

# List available prompts
zmm list prompts

# See meeting inventory status
zmm list missing --input-dir /path/to/zoom --output-dir /path/to/output

# Report counts by year/month
zmm report counts --output-dir /path/to/output

# Summarize merged transcripts
zmm summarize merged --output-dir /path/to/output

# Extract items mentioning a person
zmm extract me items --output-dir /path/to/output

# Search across all transcripts
zmm extract search --regex "budget|staffing" --output-dir /path/to/output
```

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
| `zmm export aggregates [--period auto\|year\|month]` | Write rollup files |
| `zmm delete raw` | Move processed raw dirs to `to-delete/` |
| `zmm init config` | Generate a starter config file (interactive wizard) |

## Configuration

`zmm` reads an INI-style config file from (in order):

1. `--config PATH`
2. `./zoom_meeting_manager.cfg`
3. `<script_dir>/zoom_meeting_manager.cfg`
4. `~/.config/zoom_meeting_manager.cfg`
5. Legacy `summarize_zoom_transcripts.cfg` (fallback)

See `zoom_meeting_manager.cfg.example` for all options.

## Prompts

zmm uses a two-layer prompt system: **core prompts** (bundled) + **user augmentation** (personal).

### Core prompts (bundled, never edit these)

These ship with zmm in `prompts/` and provide the base instructions:

- `meeting_generic.txt` — core summarization rules
- `output_structured_notes.txt` — JSON output schema
- `cleanup_transcript.txt` — transcript cleaning instructions

### User augmentation (personal, auto-appended)

Place `.txt` files in `~/.config/zmm/prompts/` to add context that makes
summaries better for your specific situation. These are **appended** to the
core prompts — they never replace them.

Recognized augmentation files (in order):

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
# Edit them with your info, then verify:
zmm show config
```

### Overriding (rare)

To completely replace the core prompt for a specific run:

```bash
zmm summarize merged --prompt-layer my_custom_prompt
```

This skips the normal core+augmentation assembly and uses only the specified layers.

## Global Options

```
--config PATH         Config file path
--input-dir DIR       Raw meeting source directory
--output-dir DIR      Output directory
--date-range RANGE    Filter by date (YYYY, YYYY-MM, or range)
--match TEXT          Filter by filename substring
--max N              Limit number of items processed
--dry-run            Show what would happen without acting
--format FMT         Output format: table, json, csv
--plain              Disable vistab and colors
--color MODE         Color mode: auto, always, never
--yes                Skip confirmation prompts
--no-context         Don't send personal augmentation files to the model
--resume             Skip items completed in a prior interrupted run
--debug              Print diagnostic information
--version            Show version
```

If a bulk `summarize` or `clean` run is interrupted or has failures, zmm keeps
a journal under `<output-dir>/.zmm-journal/`. Re-run with `--resume` to retry
only the unfinished items.

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
These files may contain transcript content and are not auto-deleted; remove
them when no longer needed.

API keys are read from config or `~/.config/opencode/opencode.json` and are
redacted from error output. They are never written to summaries or logs.

## License

MIT License. See [LICENSE](LICENSE).
