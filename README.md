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

| Command | Description |
|---------|-------------|
| `zmm list prompts` | List available prompt files |
| `zmm list models` | List available models from the API |
| `zmm list meetings` | List all discovered meetings |
| `zmm list missing` | Overview table of missing artifacts |
| `zmm report status` | Detailed status report |
| `zmm report counts` | Aggregate counts by year/month |
| `zmm index` | Build/rebuild inventory metadata |
| `zmm estimate summarize` | Estimate tokens before summarizing |
| `zmm summarize raw` | Merge and summarize raw meetings |
| `zmm summarize merged` | Summarize existing merged transcripts |
| `zmm summarize files FILE...` | Summarize specific files |
| `zmm fix missing summaries` | Summarize only where summaries are missing |
| `zmm clean transcripts` | LLM-clean merged transcripts |
| `zmm extract search` | Regex search across transcripts |
| `zmm extract me items` | Extract items for configured person |
| `zmm extract person items` | Extract items for a named person |
| `zmm export aggregates` | Write yearly/monthly rollup files |
| `zmm init config` | Generate a starter config file |

## Configuration

`zmm` reads an INI-style config file from (in order):

1. `--config PATH`
2. `./zoom_meeting_manager.cfg`
3. `<script_dir>/zoom_meeting_manager.cfg`
4. `~/.config/zoom_meeting_manager.cfg`
5. Legacy `summarize_zoom_transcripts.cfg` (fallback)

See `zoom_meeting_manager.cfg.example` for all options.

## Prompts

Prompts live in the `prompts/` directory and are composable layers:

- **Generic**: `meeting_generic.txt`, `output_structured_notes.txt`
- **Task-specific**: `cleanup_transcript.txt`, `extract_items.txt`, `prioritize_items.txt`
- **Context**: `contexts/` — organization-specific background
- **People**: `people/` — person profiles for extraction
- **Corrections**: `corrections/` — known transcript error patterns

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
--version            Show version
```

## License

MIT License. See [LICENSE](LICENSE).
