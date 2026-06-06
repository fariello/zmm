#!/usr/bin/env python3
"""
zoom_meeting_manager.py — zmm

Inventory, report, repair, summarize, and extract useful information from
meeting transcripts. The first source adapter supports Zoom exports.
"""

from __future__ import annotations

__version__ = "0.1.0"

import argparse
import calendar
import configparser
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

try:
    import openai
except Exception:  # pragma: no cover - command modes without API do not need it
    openai = None

try:
    import tiktoken
except Exception:  # pragma: no cover - fall back to heuristic if unavailable
    tiktoken = None

SCRIPT_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = SCRIPT_DIR / "prompts"
USER_PROMPTS_DIR = Path.home() / ".config" / "zmm" / "prompts"
LEGACY_CONFIG = "summarize_zoom_transcripts.cfg"
CONFIG_NAME = "zoom_meeting_manager.cfg"

# Default request timeout (seconds) for model/API calls.
DEFAULT_API_TIMEOUT = 600

CHECK = "✓"
CROSS = "✗"
WARN = "!"
NA = "-"


_DEBUG = False


def debug(msg: str) -> None:
    if _DEBUG:
        print(f"DEBUG: {msg}", file=sys.stderr)


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write text atomically: write to a temp file in the same dir, then replace.

    Prevents truncated/corrupt output if the process is interrupted mid-write.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    try:
        tmp.write_text(content, encoding=encoding)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


_TOKEN_ENCODER = None


def _get_token_encoder():
    global _TOKEN_ENCODER
    if _TOKEN_ENCODER is None and tiktoken is not None:
        try:
            # cl100k_base covers GPT-4/4o-class tokenization; a reasonable
            # cross-model approximation for non-OpenAI models too.
            _TOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _TOKEN_ENCODER = None
    return _TOKEN_ENCODER


def count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken when available, else a 4-char heuristic."""
    enc = _get_token_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return len(text) // 4


def count_tokens_in_file(path: str | Path) -> int:
    """Token count for a file's contents (0 if unreadable)."""
    try:
        return count_tokens(Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return 0


class RunJournal:
    """Records progress of a bulk model operation so an interrupted run can be
    inspected and resumed. Written under <output-dir>/.zmm-journal/.

    A journal tracks per-source outcome (done/failed). On a later run with
    --resume, sources already marked done in the most recent journal for the
    same operation are skipped even with --clobber.
    """

    def __init__(self, output_dir: Path, operation: str) -> None:
        self.dir = Path(output_dir) / ".zmm-journal"
        self.operation = operation
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = self.dir / f"{operation}-{ts}.json"
        self.entries: dict[str, str] = {}  # source -> "done"|"failed"
        self.started = ts

    def mark(self, source: str, status: str) -> None:
        self.entries[source] = status
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "operation": self.operation,
                "started": self.started,
                "updated": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                "entries": self.entries,
            }
            atomic_write_text(self.path, json.dumps(payload, indent=2))
        except OSError:
            pass

    def finish(self) -> None:
        # Successful clean completion: remove the journal (nothing to resume).
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass


def load_resume_done(output_dir: Path, operation: str) -> set[str]:
    """Return the set of sources marked 'done' in the most recent journal for op."""
    jdir = Path(output_dir) / ".zmm-journal"
    if not jdir.is_dir():
        return set()
    journals = sorted(jdir.glob(f"{operation}-*.json"))
    if not journals:
        return set()
    try:
        data = json.loads(journals[-1].read_text(encoding="utf-8"))
        return {s for s, st in (data.get("entries") or {}).items() if st == "done"}
    except Exception:
        return set()


# ----------------------------- Data Model ----------------------------- #


@dataclass
class SummaryRecord:
    path: str
    model: str | None = None
    json_path: str | None = None


@dataclass
class MeetingRecord:
    id: str
    title: str
    meeting_date: str | None = None
    meeting_datetime: str | None = None
    raw_dir: str | None = None
    caption_path: str | None = None
    chat_path: str | None = None
    merged_path: str | None = None
    cleaned_paths: list[str] = field(default_factory=list)
    summaries: list[SummaryRecord] = field(default_factory=list)
    expected_merged_path: str | None = None
    problems: list[str] = field(default_factory=list)

    # NOTE: these properties trust paths set during inventory discovery, which
    # only assigns a path after confirming the file exists (via glob/iterdir).
    # This avoids re-stat()ing every file on each property access — important
    # on slow network mounts where rendering accesses them many times.

    @property
    def has_raw(self) -> bool:
        return bool(self.caption_path or self.chat_path)

    @property
    def has_merged(self) -> bool:
        return bool(self.merged_path)

    @property
    def has_cleaned(self) -> bool:
        return bool(self.cleaned_paths)

    @property
    def has_summary(self) -> bool:
        return bool(self.summaries)

    @property
    def has_summary_json(self) -> bool:
        return any(s.json_path for s in self.summaries)


@dataclass
class Config:
    config_path: str | None = None
    input_dir: str | None = None
    output_dir: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    models: dict[str, str] = field(default_factory=dict)
    prompts: dict[str, str] = field(default_factory=dict)

    default_person: str | None = None
    people: dict[str, dict[str, Any]] = field(default_factory=dict)
    summarization_source: str = "cleaned_if_available"
    auto_clean_before_summarize: bool = False
    write_processing_json: bool = True
    aggregate_period: str = "auto"
    confirm_model_calls: bool = True
    color: str = "auto"


# ----------------------------- Formatting ----------------------------- #


def supports_color(mode: str) -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    return sys.stdout.isatty()


def colorize(text: str, color: str, enabled: bool) -> str:
    if not enabled:
        return text
    codes = {"red": "31", "green": "32", "yellow": "33", "cyan": "36", "dim": "2"}
    code = codes.get(color)
    if not code:
        return text
    return f"\033[{code}m{text}\033[0m"


def mark(value: bool | None, color: bool) -> str:
    if value is True:
        return colorize(CHECK, "green", color)
    if value is False:
        return colorize(CROSS, "red", color)
    return colorize(NA, "dim", color)


def warn_mark(color: bool) -> str:
    return colorize(WARN, "yellow", color)


def _truncate_titles(headers: list[str], rows: list[list[Any]], max_title: int = 80) -> list[list[Any]]:
    """Truncate 'Title' column values to max_title characters."""
    try:
        idx = [h.lower() for h in headers].index("title")
    except ValueError:
        return rows
    result = []
    for row in rows:
        row = list(row)
        if idx < len(row):
            val = str(row[idx])
            if len(val) > max_title:
                row[idx] = val[:max_title - 1] + "…"
        result.append(row)
    return result


def render_table(headers: list[str], rows: list[list[Any]], *, fmt: str, color: bool, plain: bool = False) -> None:
    if fmt == "json":
        print(json.dumps([dict(zip(headers, row)) for row in rows], indent=2, ensure_ascii=False))
        return
    if fmt == "csv":
        writer = csv.writer(sys.stdout)
        writer.writerow(headers)
        writer.writerows(rows)
        return

    # Truncate long titles for table display
    rows = _truncate_titles(headers, rows)

    if not plain and shutil.which("vistab"):
        try:
            import io
            # Determine terminal width for vistab
            try:
                term_width = os.get_terminal_size().columns - 1
            except OSError:
                term_width = 120
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(headers)
            writer.writerows(rows)
            # Build alignment string: 'l' for text cols, 'c' for short/status cols
            align = ""
            for h in headers:
                if h.lower() in ("date", "title", "prompt", "source", "model", "operation", "period", "text"):
                    align += "l"
                else:
                    align += "c"
            cmd = ["vistab", "-w", str(term_width), "-X"]
            if align:
                cmd.extend(["-a", align])
            subprocess.run(cmd, input=buf.getvalue(), text=True, check=True)
            return
        except Exception as exc:
            debug(f"vistab rendering failed, falling back to plain table: {exc}")

    if not plain and not shutil.which("vistab") and sys.stdout.isatty():
        print(colorize("Tip: install vistab for nicer tables.", "dim", color), file=sys.stderr)

    clean_rows = [[str(cell) for cell in row] for row in rows]
    widths = [len(h) for h in headers]
    ansi_re = re.compile(r"\x1b\[[0-9;]*m")
    for row in clean_rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(ansi_re.sub("", cell)))

    def pad(cell: str, width: int) -> str:
        visible = len(ansi_re.sub("", cell))
        return cell + " " * max(0, width - visible)

    print("  ".join(pad(h, widths[idx]) for idx, h in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in clean_rows:
        print("  ".join(pad(cell, widths[idx]) for idx, cell in enumerate(row)))


# ----------------------------- Config ----------------------------- #


def split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in re.split(r"[,\n]", value) if part.strip()]


def expand_env(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    m = re.fullmatch(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}", value)
    if m:
        return os.environ.get(m.group(1))
    # Only run shell-style expansion when the value actually references a
    # variable ($VAR / ${VAR}); otherwise return literal so keys containing
    # a '$' are not silently mangled.
    if "$" in value:
        return os.path.expandvars(value)
    return value


OPENCODE_CONFIG = Path.home() / ".config" / "opencode" / "opencode.json"


def config_search_paths(explicit: str | None) -> list[Path]:
    if explicit:
        return [Path(explicit).expanduser()]
    return [
        Path.cwd() / CONFIG_NAME,
        SCRIPT_DIR / CONFIG_NAME,
        Path.home() / ".config" / CONFIG_NAME,
        Path.cwd() / LEGACY_CONFIG,
        SCRIPT_DIR / LEGACY_CONFIG,
        Path.home() / ".config" / LEGACY_CONFIG,
    ]


def _load_opencode_config() -> dict[str, Any] | None:
    """Load ~/.config/opencode/opencode.json if it exists. Returns parsed JSON or None."""
    if not OPENCODE_CONFIG.is_file():
        return None
    try:
        return json.loads(OPENCODE_CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"WARNING: {OPENCODE_CONFIG} is not valid JSON ({exc}); ignoring it.", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"WARNING: could not read {OPENCODE_CONFIG}: {exc}; ignoring it.", file=sys.stderr)
        return None


def _resolve_opencode_key(value: str) -> str | None:
    """Resolve {file:path} references used in opencode.json API keys."""
    m = re.fullmatch(r"\{file:(.*)\}", value.strip())
    if m:
        key_path = Path(m.group(1)).expanduser()
        if key_path.is_file():
            # Warn if the key file is group/world-readable (POSIX).
            try:
                mode = key_path.stat().st_mode
                if mode & 0o077:
                    print(
                        f"WARNING: API key file {key_path} is group/world-readable "
                        f"(mode {oct(mode & 0o777)}). Consider: chmod 600 {key_path}",
                        file=sys.stderr,
                    )
            except OSError:
                pass
            return key_path.read_text(encoding="utf-8").strip()
        return None
    return value


def _apply_opencode_fallback(cfg: Config) -> None:
    """Fill missing cfg fields from opencode.json (api_key, base_url, models)."""
    oc = _load_opencode_config()
    if not oc:
        return

    providers = oc.get("provider") or {}

    # Determine which provider to use — prefer one matching cfg.base_url, else first with an apiKey
    matched_provider = None
    for pname, pinfo in providers.items():
        opts = pinfo.get("options") or {}
        provider_url = opts.get("baseURL", "")
        if cfg.base_url and provider_url and cfg.base_url.rstrip("/") == provider_url.rstrip("/"):
            matched_provider = pinfo
            break
    if not matched_provider:
        # Fall back to first provider with an apiKey
        for pname, pinfo in providers.items():
            opts = pinfo.get("options") or {}
            if opts.get("apiKey"):
                matched_provider = pinfo
                break

    if not matched_provider:
        return

    opts = matched_provider.get("options") or {}

    # Fill API key if missing
    if not cfg.api_key:
        raw_key = opts.get("apiKey", "")
        resolved = _resolve_opencode_key(raw_key)
        if resolved:
            cfg.api_key = resolved

    # Fill base URL if missing
    if not cfg.base_url:
        url = opts.get("baseURL")
        if url:
            cfg.base_url = url

    # Fill models if empty — pick first available model from provider as summary default
    if not cfg.models.get("summary"):
        provider_models = matched_provider.get("models") or {}
        if provider_models:
            # Pick cheapest model by input cost as a reasonable default
            cheapest = min(provider_models.items(), key=lambda kv: (kv[1].get("cost") or {}).get("input", 999))
            cfg.models.setdefault("summary", cheapest[0])


def load_config(path: str | None, *, require_api: bool = False) -> Config:
    cfg = Config()
    found = None
    for candidate in config_search_paths(path):
        if candidate.is_file():
            found = candidate
            break
    if not found:
        # No zmm config — try opencode.json as sole source
        _apply_opencode_fallback(cfg)
        if require_api and not cfg.api_key:
            searched = "\n  ".join(str(p) for p in config_search_paths(path))
            raise SystemExit(f"ERROR: No config file found. Searched:\n  {searched}\nAlso checked: {OPENCODE_CONFIG}")
        return cfg

    cfg.config_path = str(found)
    text = found.read_text(encoding="utf-8")
    parser = configparser.ConfigParser()
    if re.search(r"^\s*\[", text, re.MULTILINE):
        parser.read_string(text)
        if parser.has_section("paths"):
            cfg.input_dir = parser.get("paths", "input_dir", fallback=None) or None
            cfg.output_dir = parser.get("paths", "output_dir", fallback=None) or None
        if parser.has_section("api"):
            cfg.base_url = parser.get("api", "base_url", fallback=None) or None
            cfg.api_key = expand_env(parser.get("api", "api_key", fallback=None))
        if parser.has_section("models"):
            cfg.models = {k: v for k, v in parser.items("models") if v.strip()}
        if parser.has_section("prompts"):
            cfg.prompts = {k: v for k, v in parser.items("prompts") if v.strip()}

        if parser.has_section("user"):
            cfg.default_person = parser.get("user", "default_person", fallback=None) or None
        for section in parser.sections():
            if section.startswith("person."):
                pid = section.split(".", 1)[1]
                cfg.people[pid] = {k: split_list(v) if k.endswith("s") else v for k, v in parser.items(section)}
        if parser.has_section("transcripts"):
            cfg.summarization_source = parser.get("transcripts", "summarization_source", fallback=cfg.summarization_source)
            cfg.auto_clean_before_summarize = parser.getboolean("transcripts", "auto_clean_before_summarize", fallback=False)
        if parser.has_section("output"):
            cfg.write_processing_json = parser.getboolean("output", "write_processing_json", fallback=True)
            cfg.aggregate_period = parser.get("output", "aggregate_period", fallback=cfg.aggregate_period)
            cfg.confirm_model_calls = parser.getboolean("output", "confirm_model_calls", fallback=True)
            cfg.color = parser.get("output", "color", fallback=cfg.color)
    else:
        raw = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            raw[key.strip()] = val.strip()
        cfg.base_url = raw.get("base_url") or None
        cfg.api_key = expand_env(raw.get("api_key"))
        if raw.get("default_model"):
            cfg.models["summary"] = raw["default_model"]

        if raw.get("person_name") or raw.get("person_aliases"):
            cfg.default_person = "me"
            cfg.people["me"] = {"display_name": raw.get("person_name", "Me"), "aliases": split_list(raw.get("person_aliases"))}

    # Fallback: fill missing api_key/base_url/models from opencode.json
    _apply_opencode_fallback(cfg)

    if require_api and not cfg.api_key:
        raise SystemExit(f"ERROR: No API key found in {found or 'any config'}. Set api_key, use {{env:VAR}} syntax, or configure ~/.config/opencode/opencode.json.")
    return cfg


# ----------------------------- Dates and Paths ----------------------------- #


def parse_partial_date(value: str, *, is_end: bool = False) -> date:
    value = value.strip()
    if re.fullmatch(r"\d{4}", value):
        year = int(value)
        return date(year, 12, 31) if is_end else date(year, 1, 1)
    if re.fullmatch(r"\d{4}-\d{2}", value):
        year, month = map(int, value.split("-"))
        day = calendar.monthrange(year, month)[1] if is_end else 1
        return date(year, month, day)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return datetime.strptime(value, "%Y-%m-%d").date()
    raise argparse.ArgumentTypeError(f"Invalid date/date range component: {value}")


def parse_date_range(value: str | None) -> tuple[date | None, date | None]:
    if not value:
        return None, None
    # Range separators: " to ", "..", or " : " (colon must be space-padded so it
    # doesn't split times like 10:00:00). Dates here are date-only, but the
    # padded-colon rule keeps the parser robust.
    parts = re.split(r"\s+to\s+|\.\.|\s+:\s+", value.strip(), maxsplit=1)
    if len(parts) == 1:
        return parse_partial_date(parts[0]), parse_partial_date(parts[0], is_end=True)
    start = parse_partial_date(parts[0])
    end = parse_partial_date(parts[1], is_end=True)
    if start > end:
        raise argparse.ArgumentTypeError(f"Date range starts after it ends: {value}")
    return start, end


def in_range(value: str | None, start: date | None, end: date | None) -> bool:
    if not start and not end:
        return True
    if not value:
        return False
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return False
    return (start is None or d >= start) and (end is None or d <= end)


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "meeting"


def parse_raw_dir_name(name: str) -> tuple[str | None, str | None, str]:
    m = re.match(r"^(\d{4}-\d{2}-\d{2}) (\d{2}\.\d{2}\.\d{2}) (.+)$", name)
    if not m:
        return None, None, name
    day, time, title = m.groups()
    return day, f"{day} {time}", title


def parse_date_from_name(name: str) -> str | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", name)
    return m.group(1) if m else None


def sha256_file(path: str | None) -> str | None:
    if not path or not Path(path).is_file():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------- Transcript Helpers ----------------------------- #


def clean_filename(filename: str) -> str:
    """Sanitize a filename: replace non-alphanumeric runs with dashes, collapse, trim."""
    name, ext = os.path.splitext(filename)
    name = unicodedata.normalize("NFKD", name)
    name = re.sub(r"[^A-Za-z0-9.]+", "-", name)
    name = re.sub(r"-+", "-", name)
    name = name.strip("-_")
    return name + ext


def parse_chat_file(chat_file_path: str) -> tuple[int, list[tuple[str, str, str]]]:
    """Parse a Zoom chat file into (line_count, [(timestamp, speaker, message)])."""
    chat_entries: list[tuple[str, str, str]] = []
    pattern = re.compile(
        r'^(?:\d{4}-\d{2}-\d{2} )?'
        r'(\d{2}:\d{2}:\d{2}) '
        r'From (.*?) to Everyone:\s*'
        r'(.*)'
    )
    current_timestamp: str | None = None
    current_speaker: str | None = None
    current_message = ''
    line_no = 0

    with open(chat_file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line_no += 1
            line = line.rstrip()
            m = pattern.match(line)
            if m:
                if current_timestamp:
                    chat_entries.append((current_timestamp, current_speaker or "", current_message.strip()))
                current_timestamp, current_speaker, current_message = m.groups()
            else:
                stripped = line.strip()
                if current_timestamp and stripped:
                    current_message += (' ' if current_message else '') + stripped

        if current_timestamp:
            chat_entries.append((current_timestamp, current_speaker or "", current_message.strip()))

    return line_no, chat_entries


def merge_captions_and_chat(caption_content: str, chat_entries: list[tuple[str, str, str]]) -> str:
    """Merge captions and chat chronologically, collapsing adjacent same-speaker lines."""
    events: list[dict[str, str]] = []

    header_only_re = re.compile(r"^\[(.+?)\]\s+(\d{2}:\d{2}:\d{2})\s*$")
    single_line_re = re.compile(r"^\[(.+?)\]\s+(\d{2}:\d{2}:\d{2}):\s*(.*\S)?\s*$")

    current_speaker: str | None = None
    current_timestamp: str | None = None
    current_text_parts: list[str] = []

    for raw_line in caption_content.splitlines():
        line = raw_line.rstrip()

        m_single = single_line_re.match(line)
        if m_single:
            if current_speaker is not None and current_text_parts:
                events.append({"timestamp": current_timestamp or "", "speaker": current_speaker, "text": " ".join(t.strip() for t in current_text_parts if t.strip()), "source": "caption"})
                current_speaker = None
                current_timestamp = None
                current_text_parts = []
            spk, ts, inline_text = m_single.groups()
            events.append({"timestamp": ts, "speaker": spk, "text": (inline_text or "").strip(), "source": "caption"})
            continue

        m_hdr = header_only_re.match(line)
        if m_hdr:
            if current_speaker is not None and current_text_parts:
                events.append({"timestamp": current_timestamp or "", "speaker": current_speaker, "text": " ".join(t.strip() for t in current_text_parts if t.strip()), "source": "caption"})
            current_speaker, current_timestamp = m_hdr.groups()
            current_text_parts = []
            continue

        if current_speaker is not None and line.strip():
            current_text_parts.append(line)

    if current_speaker is not None and current_text_parts:
        events.append({"timestamp": current_timestamp or "", "speaker": current_speaker, "text": " ".join(t.strip() for t in current_text_parts if t.strip()), "source": "caption"})

    for ts, speaker, msg in chat_entries:
        events.append({"timestamp": ts, "speaker": speaker, "text": f"[IN CHAT]: {msg}", "source": "chat"})

    # Compute a rollover-aware absolute time (in seconds) for each event so that
    # meetings crossing midnight sort correctly. Each source stream is recorded
    # in chronological order; whenever its clock goes backwards we add 24h.
    def _seconds(ts: str) -> int:
        try:
            t = datetime.strptime(ts, "%H:%M:%S")
            return t.hour * 3600 + t.minute * 60 + t.second
        except Exception:
            return 0

    day_offset: dict[str, int] = {}
    prev_secs: dict[str, int] = {}
    for e in events:
        src = e["source"]
        secs = _seconds(e["timestamp"])
        if src in prev_secs and secs < prev_secs[src]:
            day_offset[src] = day_offset.get(src, 0) + 86400
        prev_secs[src] = secs
        e["_sortkey"] = str(secs + day_offset.get(src, 0)).zfill(12)

    events.sort(key=lambda e: e["_sortkey"])

    merged_events: list[dict[str, str]] = []
    for e in events:
        if not merged_events:
            merged_events.append(e)
            continue
        last = merged_events[-1]
        if e["speaker"] == last["speaker"] and e["source"] == last["source"]:
            if e["text"]:
                last["text"] = (last["text"] + " " + e["text"]).strip()
        else:
            merged_events.append(e)

    lines = [f'[{e["speaker"]}] {e["timestamp"]}: {e["text"].strip()}' for e in merged_events]
    return "\n".join(lines) + "\n"


# ----------------------------- Inventory ----------------------------- #


def summary_model_from_filename(path: Path, stem: str) -> str | None:
    name = path.name
    if not name.endswith(".summary.txt"):
        return None
    prefix = stem + "."
    if name.startswith(prefix):
        return name[len(prefix): -len(".summary.txt")].replace("--", "/")
    m = re.search(r"\.([^.]*)\.summary\.txt$", name)
    return m.group(1).replace("--", "/") if m else None


def expected_merged_name(raw_name: str) -> str:
    return clean_filename(f"{raw_name} meeting_saved_closed_caption.txt")


class _Progress:
    """Simple stderr progress indicator for slow directory scans."""

    def __init__(self) -> None:
        self._count = 0
        self._interactive = sys.stderr.isatty()
        self._phase = ""

    def phase(self, label: str) -> None:
        self._phase = label
        if self._interactive:
            sys.stderr.write(f"\r\033[K  scanning {label}...")
            sys.stderr.flush()

    def tick(self) -> None:
        self._count += 1
        if self._interactive and self._count % 20 == 0:
            sys.stderr.write(f"\r\033[K  scanning {self._phase}... ({self._count} items)")
            sys.stderr.flush()

    def done(self, total: int) -> None:
        if self._interactive:
            sys.stderr.write(f"\r\033[K  found {total} meetings\n")
            sys.stderr.flush()


def discover_inventory(input_dir: str | None, output_dir: str, *, start: date | None = None, end: date | None = None, match: str | None = None) -> list[MeetingRecord]:
    output = Path(output_dir)
    records: dict[str, MeetingRecord] = {}
    match_lower = match.lower() if match else None
    prog = _Progress()

    if input_dir and Path(input_dir).is_dir():
        prog.phase("raw meetings")
        for child in sorted(Path(input_dir).iterdir()):
            if not child.is_dir():
                continue
            prog.tick()
            meeting_date, meeting_dt, title = parse_raw_dir_name(child.name)
            if not in_range(meeting_date, start, end):
                continue
            if match_lower and match_lower not in child.name.lower():
                continue
            caption = child / "meeting_saved_closed_caption.txt"
            chat = child / "meeting_saved_chat.txt"
            new_chat = child / "meeting_saved_new_chat.txt"
            if not caption.is_file() and not chat.is_file() and not new_chat.is_file():
                continue
            year = meeting_date[:4] if meeting_date else str(date.today().year)
            merged_name = expected_merged_name(child.name)
            key = f"{meeting_date or 'unknown'}-{slugify(title)}"
            records[key] = MeetingRecord(
                id=key,
                title=title,
                meeting_date=meeting_date,
                meeting_datetime=meeting_dt,
                raw_dir=str(child),
                caption_path=str(caption) if caption.is_file() else None,
                chat_path=str(chat if chat.is_file() else new_chat) if (chat.is_file() or new_chat.is_file()) else None,
                expected_merged_path=str(output / f"Merged-Transcripts-{year}" / merged_name),
            )

    # Build a lookup from expected_merged_path → record key for matching
    expected_to_key: dict[str, str] = {}
    for key, rec in records.items():
        if rec.expected_merged_path:
            expected_to_key[str(Path(rec.expected_merged_path).resolve())] = key

    for merged_dir in sorted(output.glob("Merged-Transcripts-*")):
        if not merged_dir.is_dir():
            continue
        prog.phase(merged_dir.name)
        year = merged_dir.name.replace("Merged-Transcripts-", "")
        for path in sorted(merged_dir.glob("*.txt")):
            prog.tick()
            if path.name.endswith(".summary.txt"):
                continue
            meeting_date = parse_date_from_name(path.name)
            if not in_range(meeting_date, start, end):
                continue
            if match_lower and match_lower not in path.name.lower():
                continue

            # Try to match to an existing raw record by expected path (exact)
            resolved = str(path.resolve())
            existing_key = expected_to_key.get(resolved)
            if existing_key and existing_key in records:
                records[existing_key].merged_path = str(path)
                continue

            # Extract title: strip date+time prefix and "meeting-saved-closed-caption" suffix
            title = re.sub(r"^\d{4}-\d{2}-\d{2}[- ]?\d{0,2}\.?\d{0,2}\.?\d{0,2}[- ]?", "", path.stem)
            # Remove the common caption suffix (with dashes or underscores)
            title = re.sub(r"[-_ ]?meeting[-_ ]saved[-_ ]closed[-_ ]caption$", "", title, flags=re.IGNORECASE)
            title = title.replace("-", " ").strip() or path.stem
            key = f"{meeting_date or year}-{slugify(title)}"
            rec = records.get(key)
            # Fuzzy fallback: if no exact key match, link to a raw record that has
            # the same date and no merged transcript yet (handles legacy files whose
            # on-disk name diverges from clean_filename's slugging).
            if not rec:
                for cand in records.values():
                    if (cand.raw_dir and not cand.merged_path
                            and cand.meeting_date == meeting_date
                            and slugify(cand.title) == slugify(title)):
                        rec = cand
                        break
            if not rec:
                rec = MeetingRecord(id=key, title=title, meeting_date=meeting_date, merged_path=str(path), expected_merged_path=str(path))
                records[key] = rec
            rec.merged_path = str(path)
            rec.expected_merged_path = rec.expected_merged_path or str(path)

    for cleaned_dir in sorted(output.glob("Cleaned-Transcripts-*")):
        if not cleaned_dir.is_dir():
            continue
        prog.phase(cleaned_dir.name)
        for path in sorted(cleaned_dir.glob("*.txt")):
            prog.tick()
            meeting_date = parse_date_from_name(path.name)
            if not in_range(meeting_date, start, end):
                continue
            best = find_record_for_file(records, path)
            if best and str(path) not in best.cleaned_paths:
                best.cleaned_paths.append(str(path))

    for summary_dir in sorted(output.glob("Summaries-*")):
        if not summary_dir.is_dir():
            continue
        prog.phase(summary_dir.name)
        for path in sorted(summary_dir.glob("*.summary.txt")):
            prog.tick()
            meeting_date = parse_date_from_name(path.name)
            if not in_range(meeting_date, start, end):
                continue
            best = find_record_for_file(records, path)
            if not best:
                title = path.stem.replace(".summary", "")
                key = f"{meeting_date or 'unknown'}-{slugify(title)}"
                best = MeetingRecord(id=key, title=title, meeting_date=meeting_date)
                records[key] = best
            stem = Path(best.merged_path or best.expected_merged_path or path.name).stem
            json_path = str(path.with_suffix(".json"))
            best.summaries.append(SummaryRecord(
                path=str(path),
                model=summary_model_from_filename(path, stem),
                json_path=json_path if Path(json_path).is_file() else None,
            ))

    for rec in records.values():
        rec.problems = compute_problems(rec)
    result = sorted(records.values(), key=lambda r: (r.meeting_date or "9999-99-99", r.title.lower()))
    prog.done(len(result))
    return result


def find_record_for_file(records: dict[str, MeetingRecord], path: Path) -> MeetingRecord | None:
    stem = path.name
    stem_no_model = re.sub(r"\.[^.]+\.summary\.txt$", "", stem)
    stem_no_model = stem_no_model.replace(".summary.txt", "")
    for rec in records.values():
        candidates = [rec.merged_path, rec.expected_merged_path, rec.raw_dir]
        if any(p and Path(p).stem in stem_no_model for p in candidates):
            return rec
    date_part = parse_date_from_name(path.name)
    if date_part:
        same_day = [r for r in records.values() if r.meeting_date == date_part]
        if len(same_day) == 1:
            return same_day[0]
    return None


def compute_problems(rec: MeetingRecord) -> list[str]:
    problems = []
    if not rec.has_raw:
        problems.append("missing raw")
    if rec.has_raw and not rec.has_merged:
        problems.append("missing merged")
    if (rec.has_merged or rec.has_cleaned) and not rec.has_summary:
        problems.append("missing summary")
    if rec.has_summary and not rec.has_summary_json:
        problems.append("missing summary json")
    return problems


# ----------------------------- Prompt/Model ----------------------------- #

# Augmentation files in ~/.config/zmm/prompts/ are auto-appended in this order.
# Only files that exist are included. These augment, never replace, the core prompt.
AUGMENTATION_FILES = ["myself", "work", "people", "corrections", "style"]


def resolve_prompt_path(name: str) -> Path | None:
    """Find a prompt file by name, searching bundled prompts directory."""
    path = Path(name).expanduser()
    if path.is_file():
        return path
    candidate = PROMPTS_DIR / f"{name}.txt"
    if candidate.is_file():
        return candidate
    return None


def load_prompt(name: str) -> str:
    path = resolve_prompt_path(name)
    if not path:
        raise SystemExit(f"ERROR: Prompt not found: {name}\n  Searched: {PROMPTS_DIR}")
    return path.read_text(encoding="utf-8").strip()


def discover_augmentation_files() -> list[tuple[str, Path]]:
    """Find active user augmentation files in ~/.config/zmm/prompts/."""
    found: list[tuple[str, Path]] = []
    if not USER_PROMPTS_DIR.is_dir():
        return found
    # Check well-known names in defined order
    for name in AUGMENTATION_FILES:
        path = USER_PROMPTS_DIR / f"{name}.txt"
        if path.is_file():
            found.append((name, path))
    # Also pick up any other .txt files not in the well-known list (sorted)
    for path in sorted(USER_PROMPTS_DIR.glob("*.txt")):
        name = path.stem
        if name not in AUGMENTATION_FILES and not name.endswith(".example"):
            found.append((name, path))
    return found


def _load_augmentation_content(path: Path) -> str:
    """Load an augmentation file, stripping leading # comment lines."""
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return ""
    lines = content.splitlines()
    non_comment = []
    for line in lines:
        if line.startswith("#") and not non_comment:
            continue  # skip leading comments
        non_comment.append(line)
    return "\n".join(non_comment).strip()


def build_prompt(cfg: Config, args: argparse.Namespace, task: str = "summary", *, skip_corrections: bool = False) -> tuple[str, str]:
    """Build the full system prompt: core instructions + schema + user augmentation.

    Assembly order (always):
      1. Core task prompt (meeting_generic.txt or explicit --prompt override)
      2. Output schema prompt (output_structured_notes.txt)
      3. User augmentation files from ~/.config/zmm/prompts/ (auto-appended)

    If skip_corrections=True, the corrections.txt augmentation is omitted
    (used when summarizing an already-cleaned transcript).

    The only way to fully replace the core prompt is --prompt-string or
    explicit --prompt-layer flags, which skip the default assembly.
    """
    # Check for explicit layer overrides (power-user escape hatch)
    explicit_layers: list[str] = []
    explicit_layers.extend(getattr(args, "prompt_layer", None) or [])
    explicit_layers.extend(getattr(args, "prompt_context", None) or [])
    explicit_layers.extend(getattr(args, "prompt_person", None) or [])
    explicit_layers.extend(getattr(args, "prompt_correction", None) or [])

    if explicit_layers:
        # Explicit layers replace the default assembly entirely
        text = "\n\n".join(f"## {layer}\n\n{load_prompt(layer)}" for layer in explicit_layers)
        return text, "explicit:" + "+".join(explicit_layers)

    # Normal assembly: core + schema + augmentation
    parts: list[str] = []
    label_parts: list[str] = []

    # 1. Core task prompt
    core_name = cfg.prompts.get(task) or "meeting_generic"
    parts.append(load_prompt(core_name))
    label_parts.append(core_name)

    # 2. Output schema (for summary tasks)
    if task == "summary":
        parts.append(load_prompt("output_structured_notes"))
        label_parts.append("output_structured_notes")

    # 3. User augmentation (auto-appended from ~/.config/zmm/prompts/)
    #    Skipped entirely when --no-context is set (keeps personal context off the wire).
    if not getattr(args, "no_context", False):
        aug_files = discover_augmentation_files()
        for name, path in aug_files:
            if skip_corrections and name == "corrections":
                label_parts.append("+corrections(skipped:cleaned)")
                continue
            clean = _load_augmentation_content(path)
            if clean:
                parts.append(f"## Additional context: {name}\n\n{clean}")
                label_parts.append(f"+{name}")
    else:
        label_parts.append("+no-context")

    text = "\n\n".join(parts)
    return text, "core:" + "+".join(label_parts)


def get_model(cfg: Config, args: argparse.Namespace, task: str) -> str:
    explicit = getattr(args, f"{task}_model", None) or getattr(args, "model", None)
    if isinstance(explicit, list):
        explicit = explicit[0] if explicit else None
    return explicit or cfg.models.get(task) or cfg.models.get("summary") or "o4-mini"


def client_for(cfg: Config):
    if openai is None:
        raise SystemExit("ERROR: openai package is not installed; model-backed commands cannot run.")
    if not cfg.api_key:
        raise SystemExit("ERROR: Missing API key. Configure [api] api_key or legacy api_key.")
    kwargs: dict[str, Any] = {"api_key": cfg.api_key, "timeout": DEFAULT_API_TIMEOUT}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return openai.OpenAI(**kwargs)


def chat_messages(system: str, user: str) -> Any:
    """Build an OpenAI chat-completions message list.

    Cast to Any at this single boundary so the openai SDK's strict
    ChatCompletionMessageParam typing doesn't propagate plain-dict noise
    throughout the codebase. Shapes are correct at runtime.
    """
    return cast(Any, [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])


def call_model_json(cfg: Config, args: argparse.Namespace, *, model: str, messages: Any, operation: str, label: str) -> dict[str, Any]:
    client = client_for(cfg)
    try:
        response = client.chat.completions.create(model=model, messages=messages)
        content = (response.choices[0].message.content or "").strip()
        try:
            return parse_json_response(content)
        except Exception as exc:
            diag = save_diagnostic("invalid-response", label, model, content, cfg, args)
            print_model_error(exc, model=model, operation=f"{operation} (parse JSON)", label=f"{label}; saved {diag}", cfg=cfg)
            if getattr(args, "ignore_model_errors", False):
                return {}
            raise SystemExit(1)
    except Exception as exc:
        print_model_error(exc, model=model, operation=operation, label=label, cfg=cfg)
        if getattr(args, "ignore_model_errors", False):
            return {}
        raise SystemExit(1)


def parse_json_response(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
        if m:
            return json.loads(m.group(1))
        raise


# Required top-level keys in the model_output, per schemas/summary.json.
SUMMARY_REQUIRED_KEYS = [
    "improved_title", "one_liner", "high_level_summary", "key_takeaways",
    "decisions", "action_items", "open_questions", "key_topics",
    "attendees", "detailed_notes", "llm_notes",
]


def validate_summary_output(data: dict[str, Any], *, label: str) -> list[str]:
    """Lightweight, dependency-free validation of model summary output.

    Checks for required top-level keys per schemas/summary.json. Returns a
    list of warning strings (empty if valid). Does not raise — summaries
    render best-effort even if some fields are missing.
    """
    warnings: list[str] = []
    if not isinstance(data, dict):
        return [f"{label}: model output is not a JSON object"]
    missing = [k for k in SUMMARY_REQUIRED_KEYS if k not in data]
    if missing:
        warnings.append(f"{label}: model output missing fields: {', '.join(missing)}")
    return warnings


def save_diagnostic(kind: str, label: str, model: str, content: str, cfg: Config, args: argparse.Namespace) -> Path:
    year = parse_date_from_name(label) or str(date.today().year)
    safe_label = slugify(Path(label).stem or label)[:120]
    safe_model = model.replace("/", "--")
    diag_dir = resolve_output_dir(args, cfg, required=False) / "Diagnostics" / year[:4]
    diag_dir.mkdir(parents=True, exist_ok=True)
    path = diag_dir / f"{safe_label}.{safe_model}.{kind}.txt"
    atomic_write_text(path, content)
    return path


def _scrub_secret(text: str, cfg: Config) -> str:
    """Redact the configured API key (and obvious key-like tokens) from text."""
    if cfg.api_key and cfg.api_key in text:
        text = text.replace(cfg.api_key, "***REDACTED***")
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "***REDACTED***", text)
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._-]{8,}", r"\1***REDACTED***", text)
    return text


def print_model_error(exc: Exception, *, model: str, operation: str, label: str, cfg: Config) -> None:
    print("ERROR: model/API call failed", file=sys.stderr)
    print(f"  Operation: {operation}", file=sys.stderr)
    print(f"  Model:     {model}", file=sys.stderr)
    print(f"  Item:      {label}", file=sys.stderr)
    print(f"  Endpoint:  {cfg.base_url or 'default OpenAI endpoint'}", file=sys.stderr)
    print(f"  Error:     {_scrub_secret(f'{type(exc).__name__}: {exc}', cfg)}", file=sys.stderr)
    print("  Next:      Check API key, endpoint, model name, network access, and provider status.", file=sys.stderr)


# ----------------------------- Commands ----------------------------- #


def filter_missing(records: list[MeetingRecord], kind: str | None) -> list[MeetingRecord]:
    if kind in (None, "all"):
        return [r for r in records if r.problems]
    if kind in ("merged", "transcripts"):
        return [r for r in records if "missing merged" in r.problems]
    if kind == "summaries":
        return [r for r in records if "missing summary" in r.problems or "missing summary json" in r.problems]
    if kind == "raw":
        return [r for r in records if "missing raw" in r.problems]
    return []


OVERVIEW_HEADERS = ["Date", "Title", "Raw", "Mer-\nged", "Clean", "Sum-\nmary", "JSON"]


def rows_overview(records: list[MeetingRecord], color: bool) -> list[list[Any]]:
    return [[
        r.meeting_date or "",
        r.title,
        mark(r.has_raw, color),
        mark(r.has_merged, color),
        mark(r.has_cleaned, color) if r.has_merged else mark(None, color),
        mark(r.has_summary, color),
        mark(r.has_summary_json, color),
    ] for r in records]


def _load_providers_from_opencode() -> list[dict[str, Any]]:
    """Load all providers from opencode.json with their models, URLs, and keys."""
    oc = _load_opencode_config()
    if not oc:
        return []
    result = []
    for pname, pinfo in (oc.get("provider") or {}).items():
        opts = pinfo.get("options") or {}
        key_raw = opts.get("apiKey", "")
        resolved_key = _resolve_opencode_key(key_raw) if key_raw else None
        models = {}
        for model_id, model_info in (pinfo.get("models") or {}).items():
            models[model_id] = model_info.get("cost") or {}
        result.append({
            "id": pname,
            "name": pinfo.get("name") or pname,
            "base_url": opts.get("baseURL"),
            "api_key": resolved_key,
            "models": models,
        })
    return result


def _cmd_list_models(args: argparse.Namespace, cfg: Config, color: bool, provider_filter: str | None) -> None:
    """List models from all available providers."""
    providers = _load_providers_from_opencode()
    any_output = False

    # Filter providers if requested
    if provider_filter:
        providers = [p for p in providers if provider_filter.lower() in p["id"].lower() or provider_filter.lower() in p["name"].lower()]
        if not providers:
            print(f"No provider matching '{provider_filter}'. Available: {', '.join(p['id'] for p in _load_providers_from_opencode())}", file=sys.stderr)
            raise SystemExit(1)

    for provider in providers:
        pname = provider["name"]
        base_url = provider.get("base_url")
        api_key = provider.get("api_key")
        config_models: dict[str, dict[str, float]] = provider.get("models", {})

        # Try live API query for this provider
        api_models: set[str] = set()
        api_ok = False
        if api_key and openai is not None:
            try:
                kwargs: dict[str, Any] = {"api_key": api_key, "timeout": DEFAULT_API_TIMEOUT}
                if base_url:
                    kwargs["base_url"] = base_url
                client = openai.OpenAI(**kwargs)
                api_models = set(m.id for m in client.models.list().data)
                api_ok = True
            except Exception as exc:
                print(f"  \033[33m! {pname}: API unreachable ({type(exc).__name__})\033[0m", file=sys.stderr)

        def _cost_str(model_id: str, direction: str) -> str:
            cost = config_models.get(model_id, {})
            val = cost.get(direction)
            return f"${val:.2f}" if val else ""

        if api_ok and api_models:
            # Show API models with pricing where available
            all_models = sorted(api_models)
            rows = [[m, _cost_str(m, "input"), _cost_str(m, "output")] for m in all_models]
            print(f"\n  \033[1m{pname}\033[0m ({base_url or 'default endpoint'}) — {len(api_models)} models\n")
            render_table(["Model", "Cost(in)", "Cost(out)"], rows, fmt=args.format, color=color, plain=args.plain)
            any_output = True

            # Flag config-only models (not returned by API) — hidden unless --show-stale
            config_only = sorted(set(config_models.keys()) - api_models)
            if config_only:
                if getattr(args, "show_stale", False):
                    rows_stale = [[m, _cost_str(m, "input"), _cost_str(m, "output")] for m in config_only]
                    print(f"\n  \033[33m{pname}: config-only (not returned by API — may be deprecated):\033[0m\n")
                    render_table(["Model", "Cost(in)", "Cost(out)"], rows_stale, fmt=args.format, color=color, plain=args.plain)
                else:
                    print(f"\n  \033[2m{pname}: {len(config_only)} config-only model(s) hidden (use --show-stale to show).\033[0m")
        elif config_models:
            # No API access — show config models
            all_models = sorted(config_models.keys())
            rows = [[m, _cost_str(m, "input"), _cost_str(m, "output")] for m in all_models]
            suffix = " (API unreachable)" if api_key else " (no API key)"
            print(f"\n  \033[1m{pname}\033[0m{suffix} — {len(config_models)} models from opencode.json\n")
            render_table(["Model", "Cost(in)", "Cost(out)"], rows, fmt=args.format, color=color, plain=args.plain)
            any_output = True

    if not any_output:
        print("No models found. Check opencode.json and API credentials.", file=sys.stderr)
        raise SystemExit(1)


def cmd_list(args: argparse.Namespace, cfg: Config) -> None:
    color = supports_color(args.color or cfg.color)
    if args.list_object == "prompts":
        rows: list[list[Any]] = []
        # Bundled core prompts
        for p in sorted(PROMPTS_DIR.glob("*.txt")):
            name = p.stem
            rows.append([name, "core", str(p)])
        # User augmentation files
        for name, path in discover_augmentation_files():
            rows.append([name, "augmentation", str(path)])
        # Bundled examples
        examples_dir = PROMPTS_DIR / "examples"
        if examples_dir.is_dir():
            for p in sorted(examples_dir.rglob("*.txt")):
                name = str(p.relative_to(examples_dir).with_suffix(""))
                rows.append([name, "example", str(p)])
        render_table(["Prompt", "Role", "Path"], rows, fmt=args.format, color=color, plain=args.plain)
        return
    if args.list_object == "models":
        provider_filter = getattr(args, "provider", None)
        _cmd_list_models(args, cfg, color, provider_filter)
        return
    records = get_records(args, cfg)
    limit = getattr(args, "max", None) or None
    if args.list_object == "missing":
        kind = args.missing_kind or "all"
        records = filter_missing(records, kind)
        render_table(OVERVIEW_HEADERS, rows_overview(records[:limit], color), fmt=args.format, color=color, plain=args.plain)
        return
    if args.list_object == "meetings":
        render_table(OVERVIEW_HEADERS, rows_overview(records[:limit], color), fmt=args.format, color=color, plain=args.plain)
        return


def cmd_report(args: argparse.Namespace, cfg: Config) -> None:
    records = get_records(args, cfg)
    color = supports_color(args.color or cfg.color)
    if args.report_object == "status":
        render_table(OVERVIEW_HEADERS, rows_overview(records, color), fmt=args.format, color=color, plain=args.plain)
        return
    groups: dict[str, list[MeetingRecord]] = {}
    for r in records:
        if args.by == "year":
            key = (r.meeting_date or "Unknown")[:4]
        elif args.by == "month":
            key = (r.meeting_date or "Unknown")[:7]
        else:
            key = (r.meeting_date or "Unknown")[:4]
            groups.setdefault(key, []).append(r)
            key = (r.meeting_date or "Unknown")[:7]
        groups.setdefault(key, []).append(r)
    rows = []
    for key in sorted(groups):
        vals = groups[key]
        rows.append([key, len(vals), sum(r.has_raw for r in vals), sum(r.has_merged for r in vals), sum(r.has_cleaned for r in vals), sum(r.has_summary for r in vals), sum(bool(r.problems) for r in vals)])
    render_table(["Period", "Meetings", "Raw", "Mer-\nged", "Clean", "Sum-\nmary", "Issues"], rows, fmt=args.format, color=color, plain=args.plain)


def cmd_index(args: argparse.Namespace, cfg: Config) -> None:
    records = get_records(args, cfg)
    rebuild = getattr(args, "rebuild", False)
    if rebuild:
        # Force a fresh rewrite of all processing JSON, ignoring any existing files.
        output = resolve_output_dir(args, cfg, required=False)
        for existing in output.glob("*-Meeting-Processing.json"):
            try:
                existing.unlink()
            except OSError:
                pass
    write_processing_json(records, cfg, args)
    print(f"Indexed {len(records)} meeting records{' (rebuilt)' if rebuild else ''}.")


def cmd_write_json(args: argparse.Namespace, cfg: Config) -> None:
    records = get_records(args, cfg)
    write_processing_json(records, cfg, args)
    print(f"Wrote processing JSON for {len(records)} meeting records.")


def cmd_migrate(args: argparse.Namespace, cfg: Config) -> None:
    """Migrate/import existing on-disk output into zmm metadata.

    Discovers existing merged transcripts, cleaned transcripts, and summaries,
    infers model names from summary filenames, and writes processing JSON so
    that pre-existing output is tracked by zmm.
    """
    records = get_records(args, cfg)
    summary_count = sum(len(r.summaries) for r in records)
    cleaned_count = sum(len(r.cleaned_paths) for r in records)
    merged_count = sum(1 for r in records if r.has_merged)
    print(f"Discovered {len(records)} meetings:")
    print(f"  {merged_count} with merged transcripts")
    print(f"  {cleaned_count} cleaned transcripts")
    print(f"  {summary_count} summaries (models inferred from filenames)")
    if args.dry_run:
        print("Dry run: no metadata written.")
        return
    write_processing_json(records, cfg, args)
    print(f"Wrote processing JSON. Migration complete.")


def _ask(prompt: str, default: str = "") -> str:
    """Ask a question interactively, returning the answer or default."""
    suffix = f" \033[36m[{default}]\033[0m: " if default else ": "
    try:
        answer = input(prompt + suffix).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        answer = ""
    return answer or default


def _ask_yn(prompt: str, default: bool = True) -> bool:
    """Ask a yes/no question."""
    hint = "Y/n" if default else "y/N"
    suffix = f" \033[36m[{hint}]\033[0m: "
    try:
        answer = input(prompt + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    if not answer:
        return default
    return answer.startswith("y")


def _w_bold(text: str) -> str:
    return f"\033[1m{text}\033[0m"


def _w_section(title: str) -> None:
    print(f"\n\033[1;34m{'─' * 3} {title} {'─' * (36 - len(title))}\033[0m")


def _w_info(text: str) -> None:
    print(f"  \033[37m{text}\033[0m")


def _w_example(text: str) -> None:
    print(f"    \033[36m{text}\033[0m")


def _w_ok(text: str) -> None:
    print(f"  \033[32m✓ {text}\033[0m")


def _w_warn(text: str) -> None:
    print(f"  \033[33m! {text}\033[0m")


def _check_dir(label: str, path_str: str) -> str:
    """Validate a directory path — warn and offer to create if it doesn't exist."""
    if not path_str:
        return path_str
    p = Path(path_str).expanduser()
    if p.is_dir():
        _w_ok(f"{label} exists: {p}")
    else:
        _w_warn(f"{label} does not exist: {p}")
        if _ask_yn(f"    Create {p}?", default=True):
            try:
                p.mkdir(parents=True, exist_ok=True)
                _w_ok(f"Created {p}")
            except OSError as e:
                _w_warn(f"Could not create: {e}")
    return path_str


def _list_models_live(base_url: str | None, api_key: str | None) -> list[str] | None:
    """Try to fetch model list from the API. Returns sorted list or None on failure."""
    if not api_key or openai is None:
        return None
    try:
        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": DEFAULT_API_TIMEOUT}
        if base_url:
            kwargs["base_url"] = base_url
        client = openai.OpenAI(**kwargs)
        return sorted(m.id for m in client.models.list().data)
    except Exception:
        return None


def cmd_init(args: argparse.Namespace, cfg: Config) -> None:
    target = Path(args.output or Path.home() / ".config" / CONFIG_NAME).expanduser()
    if target.exists() and not args.clobber:
        raise SystemExit(f"ERROR: Config exists: {target}. Use --clobber to overwrite.")
    target.parent.mkdir(parents=True, exist_ok=True)

    # Probe opencode.json for defaults
    oc = _load_opencode_config()
    oc_api_key = ""
    oc_api_key_resolved: str | None = None
    oc_base_url = ""
    oc_models: list[str] = []
    oc_provider_name = ""
    if oc:
        providers = oc.get("provider") or {}
        for pname, pinfo in providers.items():
            opts = pinfo.get("options") or {}
            key_raw = opts.get("apiKey", "")
            resolved = _resolve_opencode_key(key_raw) if key_raw else None
            if resolved:
                oc_api_key = "(from opencode.json)"
                oc_api_key_resolved = resolved
                oc_base_url = opts.get("baseURL", "")
                oc_provider_name = pinfo.get("name") or pname
                oc_models = sorted((pinfo.get("models") or {}).keys())
                break

    # Defaults
    input_dir = ""
    output_dir = ""
    base_url = oc_base_url
    api_key = "{env:OPENAI_API_KEY}" if not oc_api_key else ""
    summary_model = ""
    display_name = ""
    aliases = ""

    # Interactive wizard when stdin is a terminal
    if sys.stdin.isatty() and not getattr(args, "yes", False):
        print()
        print(_w_bold("  zmm config wizard"))
        print(f"  \033[34m{'━' * 38}\033[0m")
        print()

        # Opencode.json detection
        if oc:
            _w_ok(f"Detected: ~/.config/opencode/opencode.json")
            _w_info(f"Provider: {_w_bold(oc_provider_name)}")
            _w_info(f"API key:  {_w_bold('configured')}")
            if oc_base_url:
                _w_info(f"Base URL: {oc_base_url}")
            _w_info(f"Models:   {len(oc_models)} available")
            print()
            _w_info("zmm uses opencode.json for API access automatically.")
            _w_info("Only set values below if you want to " + _w_bold("override") + " it.")
        else:
            _w_warn("No ~/.config/opencode/opencode.json found.")
            _w_info("You'll need to provide API credentials below.")
        print()

        # Input dir
        _w_section("Input Directory")
        _w_info("Where Zoom stores raw meeting recordings.")
        _w_info("Each meeting is a folder like:")
        _w_example("2026-06-04 15.29.53 Meeting Title/")
        _w_info("containing:")
        _w_example("meeting_saved_closed_caption.txt")
        _w_example("meeting_saved_chat.txt")
        print()
        input_dir = _ask("  input_dir", input_dir)
        _check_dir("input_dir", input_dir)
        print()

        # Output dir
        _w_section("Output Directory")
        _w_info("Where zmm writes processed output. Creates subdirectories:")
        _w_example("Merged-Transcripts-YYYY/   (merged caption+chat)")
        _w_example("Summaries-YYYY/            (LLM-generated summaries)")
        _w_example("Cleaned-Transcripts-YYYY/  (LLM-cleaned transcripts)")
        print()
        output_dir = _ask("  output_dir", output_dir)
        _check_dir("output_dir", output_dir)
        print()

        # API settings
        if oc:
            _w_section("API Settings (optional)")
            _w_info("opencode.json provides API access. Leave blank to use it.")
            _w_info("Only set a base_url if you want a " + _w_bold("different") + " endpoint.")
        else:
            _w_section("API Settings")
            _w_info("zmm needs an OpenAI-compatible API for summarization.")
            _w_info("You can use OpenAI directly, or a compatible gateway.")
        print()
        _w_info("base_url examples:")
        _w_example("(blank)                          → OpenAI default")
        _w_example("https://api.openai.com/v1        → explicit OpenAI")
        _w_example("https://llmgw.example.com/v1     → custom gateway")
        print()
        base_url = _ask("  base_url", base_url)
        print()

        if not oc:
            _w_info("api_key: Your API key, or an env var reference.")
            _w_info("Use {env:VAR_NAME} to read from environment at runtime.")
            _w_example("{env:OPENAI_API_KEY}")
            _w_example("sk-abc123...")
            print()
            api_key = _ask("  api_key", api_key)
            print()
        else:
            api_key = ""

        # Model — offer to list models
        _w_section("Summary Model")
        _w_info("The LLM model used for meeting summarization.")
        print()

        # Try to show available models
        available_models: list[str] = []
        if oc_models:
            available_models = oc_models
        else:
            # Try a live API call if we have credentials
            live_key = oc_api_key_resolved or (api_key if not api_key.startswith("{") else None)
            live_url = base_url or oc_base_url or None
            if live_key:
                _w_info("Fetching available models from API...")
                live = _list_models_live(live_url, live_key)
                if live:
                    available_models = live
                    _w_ok(f"Found {len(live)} models.")
                else:
                    _w_warn("Could not fetch models. You can set one manually.")

        if available_models:
            if _ask_yn("  List available models?", default=True):
                print()
                for i, m in enumerate(available_models):
                    print(f"    \033[36m{m}\033[0m")
                    if i >= 24:
                        remaining = len(available_models) - 25
                        if remaining > 0:
                            _w_info(f"... and {remaining} more")
                        break
            print()
            _w_info("Leave blank to let zmm auto-select the cheapest model.")
        else:
            _w_info("Examples:")
            _w_example("gpt-4o")
            _w_example("o4-mini")
            _w_example("its_direct/pt2-qwen3-coder-next-us")
        print()
        summary_model = _ask("  summary_model", summary_model)
        print()

        # Person
        _w_section("Person Profile")
        _w_info("zmm can extract action items and statements mentioning you.")
        _w_info("Your display name and aliases help it find relevant content.")
        print()
        _w_info("display_name: Your full name as it appears in meeting notes.")
        _w_example("Jane Smith")
        print()
        display_name = _ask("  display_name", display_name)
        print()
        _w_info("aliases: Other names/spellings in transcripts (comma-separated).")
        _w_info("Include common Zoom misspellings of your name.")
        _w_example("Jane, Jan, J. Smith, Jayne")
        print()
        aliases = _ask("  aliases", aliases)
        print()

    # Build config text — omit API section if opencode.json provides it and user didn't override
    api_section = ""
    if api_key or (base_url and base_url != oc_base_url) or not oc:
        api_section = f"""
# ─── API ─────────────────────────────────────────────────────────────────────
[api]

# OpenAI-compatible endpoint. Leave blank for OpenAI default.
# Leave entire section blank if ~/.config/opencode/opencode.json provides credentials.
base_url = {base_url}

# API key. Supports: literal, {{env:VAR_NAME}}, $VAR, ${{VAR}}
api_key = {api_key}

"""
    else:
        api_section = """
# ─── API ─────────────────────────────────────────────────────────────────────
# Using ~/.config/opencode/opencode.json for API credentials.
# Uncomment and set values here only to override:
# [api]
# base_url =
# api_key =

"""

    text = f"""# zoom_meeting_manager.cfg
#
# Configuration for zmm (Zoom Meeting Manager).
# Generated by: zmm init config
#
# Values here override ~/.config/opencode/opencode.json.
# Blank fields fall through to opencode.json or built-in defaults.
#
# Docs: zmm show config    (see active configuration)
#       zmm show prompt    (see full model directive)
#       zmm list prompts   (see available prompts)


# ─── Paths ───────────────────────────────────────────────────────────────────
[paths]

# Where Zoom stores raw meeting recordings.
# Each meeting is a folder like: "2026-06-04 15.29.53 Meeting Title/"
# containing meeting_saved_closed_caption.txt and/or meeting_saved_chat.txt
input_dir = {input_dir}

# Where zmm writes processed output (creates subdirectories automatically):
#   Merged-Transcripts-YYYY/   — merged caption+chat files
#   Summaries-YYYY/            — LLM summaries (.txt + .json)
#   Cleaned-Transcripts-YYYY/  — LLM-cleaned transcripts
#   to-delete/                 — raw dirs moved by 'zmm delete raw'
output_dir = {output_dir}

{api_section}
# ─── Models ──────────────────────────────────────────────────────────────────
[models]

# Blank = fall back to opencode.json's cheapest model.
# CLI overrides: --summary-model, --cleanup-model
#
# summary: used by zmm summarize, zmm fix missing summaries
#          (needs a capable model — must produce structured JSON)
# cleanup: used by zmm clean transcripts
#          (simpler task — can use a cheaper/faster model)
summary = {summary_model}
cleanup =


# ─── User Identity (for 'zmm extract me') ────────────────────────────────────
[user]

# Which [person.NAME] profile to use for 'zmm extract me' commands.
# This drives the local regex search — it does NOT affect the model prompt.
# For model context about yourself, use ~/.config/zmm/prompts/myself.txt
default_person = me

[person.me]

# Your name and aliases are used by 'zmm extract me' to regex-search
# transcripts for mentions of you. This is a local search, no model involved.
display_name = {display_name}
aliases = {aliases}


# ─── Transcripts ─────────────────────────────────────────────────────────────
[transcripts]

# Which transcript version to summarize when both cleaned and merged exist:
#   cleaned_if_available — use cleaned if it exists, else merged (default)
#   cleaned              — prefer cleaned, skip meetings without one
#   required_cleaned     — require cleaned, error if missing
#   merged               — always use canonical merged transcript
summarization_source = cleaned_if_available

# Auto-run LLM cleanup before summarizing. Usually false — run
# 'zmm clean transcripts' as a separate step first.
auto_clean_before_summarize = false


# ─── Output ──────────────────────────────────────────────────────────────────
[output]

# Write Processing-YYYY.json metadata after state-changing commands.
write_processing_json = true

# Default grouping for 'zmm export aggregates': auto, year, month
aggregate_period = auto

# Prompt for confirmation before bulk model calls (false = same as --yes).
confirm_model_calls = true

# Terminal color mode: auto, always, never
color = auto
"""
    atomic_write_text(target, text)
    print(f"Wrote {target}")
    print("Next: edit paths/API settings, then run `zmm index --rebuild` and `zmm list missing`.")


def cmd_show(args: argparse.Namespace, cfg: Config) -> None:
    """Show active configuration: config files, prompt directories, models, API endpoint."""
    lines: list[str] = []

    # Config file
    lines.append("Config file:")
    if cfg.config_path:
        lines.append(f"  {cfg.config_path}")
    else:
        lines.append("  (none found — using opencode.json fallback)")

    # Opencode.json
    lines.append("")
    lines.append("Opencode config:")
    if OPENCODE_CONFIG.is_file():
        lines.append(f"  {OPENCODE_CONFIG} (found)")
    else:
        lines.append(f"  {OPENCODE_CONFIG} (not found)")

    # Prompts
    lines.append("")
    lines.append("Core prompts:")
    core_name = cfg.prompts.get("summary") or "meeting_generic"
    lines.append(f"  task prompt:   {core_name}  ({PROMPTS_DIR / f'{core_name}.txt'})")
    lines.append(f"  output schema: output_structured_notes  ({PROMPTS_DIR / 'output_structured_notes.txt'})")

    # Augmentation
    lines.append("")
    lines.append("User augmentation (auto-appended from ~/.config/zmm/prompts/):")
    aug_files = discover_augmentation_files()
    if aug_files:
        for name, path in aug_files:
            lines.append(f"  {name}.txt")
    else:
        lines.append("  (none found)")
    if not USER_PROMPTS_DIR.is_dir():
        lines.append(f"  Directory does not exist: {USER_PROMPTS_DIR}")
    else:
        lines.append(f"  Directory: {USER_PROMPTS_DIR}")

    # API
    lines.append("")
    lines.append("API:")
    lines.append(f"  base_url: {cfg.base_url or '(default OpenAI endpoint)'}")
    lines.append(f"  api_key:  {'configured' if cfg.api_key else 'NOT SET'}")

    # Models
    lines.append("")
    lines.append("Models:")
    for task in ("summary", "cleanup"):
        model = cfg.models.get(task)
        lines.append(f"  {task}: {model or '(not set)'}")

    # Paths
    lines.append("")
    lines.append("Paths:")
    lines.append(f"  input_dir:  {cfg.input_dir or '(not set)'}")
    lines.append(f"  output_dir: {cfg.output_dir or '(not set)'}")

    # Person
    lines.append("")
    lines.append("Person profile:")
    if cfg.default_person and cfg.people.get(cfg.default_person):
        person = cfg.people[cfg.default_person]
        lines.append(f"  default_person: {cfg.default_person}")
        lines.append(f"  display_name:   {person.get('display_name', '(not set)')}")
        aliases = person.get("aliases", [])
        lines.append(f"  aliases:        {', '.join(aliases) if aliases else '(not set)'}")
    else:
        lines.append(f"  default_person: {cfg.default_person or '(not set)'}")

    print("\n".join(lines))


def cmd_show_prompt(args: argparse.Namespace, cfg: Config) -> None:
    """Show the complete prompt/directive that would be sent to the model, with source annotations."""
    task = getattr(args, "task", None) or "summary"
    color = sys.stdout.isatty()

    # ANSI codes
    BOLD = "\033[1m" if color else ""
    RESET = "\033[0m" if color else ""
    BLUE = "\033[1;34m" if color else ""  # core sections
    GREEN = "\033[1;32m" if color else ""  # user augmentation
    CYAN = "\033[36m" if color else ""  # file paths
    DIM = "\033[2m" if color else ""

    # Check for explicit layer overrides
    explicit_layers: list[str] = []
    explicit_layers.extend(getattr(args, "prompt_layer", None) or [])
    explicit_layers.extend(getattr(args, "prompt_context", None) or [])
    explicit_layers.extend(getattr(args, "prompt_person", None) or [])
    explicit_layers.extend(getattr(args, "prompt_correction", None) or [])

    if explicit_layers:
        print(f"{BOLD}Prompt mode:{RESET} explicit override (--prompt-layer)")
        print(f"{DIM}{'─' * 60}{RESET}")
        for layer in explicit_layers:
            path = resolve_prompt_path(layer)
            print(f"\n{BLUE}━━━ [{layer}] ━━━{RESET}")
            print(f"{CYAN}  Source: {path}{RESET}")
            print(f"{DIM}{'─' * 60}{RESET}")
            print(load_prompt(layer))
        return

    # Normal assembly
    print(f"{BOLD}Prompt assembly for task: {task}{RESET}")
    print(f"{DIM}This is the complete directive sent to the model as the system message.{RESET}")
    print()

    # 1. Core task prompt
    core_name = cfg.prompts.get(task) or "meeting_generic"
    core_path = resolve_prompt_path(core_name)
    print(f"{BLUE}━━━ CORE: {core_name} ━━━{RESET}")
    print(f"{CYAN}  Source: {core_path}{RESET}")
    print(f"{BLUE}  Role:   Core task instructions (bundled with zmm){RESET}")
    print(f"{DIM}{'─' * 60}{RESET}")
    print(load_prompt(core_name))
    print()

    # 2. Output schema
    if task == "summary":
        schema_path = resolve_prompt_path("output_structured_notes")
        print(f"{BLUE}━━━ CORE: output_structured_notes ━━━{RESET}")
        print(f"{CYAN}  Source: {schema_path}{RESET}")
        print(f"{BLUE}  Role:   JSON output schema (bundled with zmm){RESET}")
        print(f"{DIM}{'─' * 60}{RESET}")
        print(load_prompt("output_structured_notes"))
        print()

    # 3. User augmentation
    aug_files = discover_augmentation_files()
    if aug_files:
        for name, path in aug_files:
            clean = _load_augmentation_content(path)
            if not clean:
                continue
            print(f"{GREEN}━━━ USER: {name} ━━━{RESET}")
            print(f"{CYAN}  Source: {path}{RESET}")
            print(f"{GREEN}  Role:   Personal augmentation (auto-appended){RESET}")
            if name == "corrections":
                print(f"{DIM}  Note:   Skipped when summarizing cleaned transcripts{RESET}")
            print(f"{DIM}{'─' * 60}{RESET}")
            print(f"## Additional context: {name}\n")
            print(clean)
            print()
    else:
        print(f"{DIM}(No user augmentation files found in {USER_PROMPTS_DIR}){RESET}")
        print()

    # Summary
    total_parts = 1 + (1 if task == "summary" else 0) + len(aug_files)
    print(f"{DIM}{'─' * 60}{RESET}")
    print(f"{BOLD}Total sections: {total_parts}{RESET}  "
          f"({BLUE}core{RESET}: {1 + (1 if task == 'summary' else 0)}, "
          f"{GREEN}user{RESET}: {len(aug_files)})")

    # Token/cost estimate for the system prompt
    full_prompt, _ = build_prompt(cfg, args, task)
    prompt_chars = len(full_prompt)
    prompt_tokens = count_tokens(full_prompt)
    model = get_model(cfg, args, task)
    cost_str = _estimate_cost(prompt_tokens, model, "input")

    print()
    print(f"{BOLD}System prompt size:{RESET}")
    print(f"  Characters:  {prompt_chars:,}")
    print(f"  Est. tokens: {prompt_tokens:,}")
    print(f"  Model:       {model}")
    if cost_str:
        print(f"  Est. cost:   {cost_str} per call (system message only)")
    else:
        print(f"  Est. cost:   (no pricing available for {model})")
    print(f"{DIM}  Note: providers with prompt caching discount repeated system messages.{RESET}")


def _load_model_costs() -> dict[str, dict[str, float]]:
    """Load per-model cost data from ~/.config/opencode/opencode.json if available."""
    costs: dict[str, dict[str, float]] = {}
    oc = _load_opencode_config()
    if not oc:
        return costs
    for provider_info in (oc.get("provider") or {}).values():
        for model_id, model_info in (provider_info.get("models") or {}).items():
            cost = model_info.get("cost")
            if cost and isinstance(cost, dict):
                costs[model_id] = cost
                # Also index by short name (without provider prefix)
                if "/" in model_id:
                    short = model_id.rsplit("/", 1)[-1]
                    costs.setdefault(short, cost)
    return costs


def _estimate_cost(tokens: int, model: str, direction: str = "input") -> str | None:
    """Return formatted cost string or None if pricing unavailable."""
    costs = _load_model_costs()
    model_cost = costs.get(model)
    if not model_cost:
        # Try partial match
        for key, val in costs.items():
            if model in key or key in model:
                model_cost = val
                break
    if not model_cost:
        return None
    rate = model_cost.get(direction, 0)
    if not rate:
        return None
    # Costs are per million tokens
    dollar = (tokens / 1_000_000) * rate
    return f"${dollar:.4f}"


def cmd_estimate(args: argparse.Namespace, cfg: Config) -> None:
    records = get_records(args, cfg)
    candidates: list[str | None] = []
    model_task = args.estimate_object
    if model_task == "summarize":
        candidates = [r.cleaned_paths[-1] if (cfg.summarization_source != "merged" and r.cleaned_paths) else r.merged_path for r in records]
    elif model_task == "clean":
        candidates = [r.merged_path for r in records]
    elif model_task == "extract":
        candidates = [s.json_path or s.path for r in records for s in r.summaries]
    files = [f for f in candidates if f and Path(f).is_file()]
    tokens = sum(count_tokens_in_file(f) for f in files)

    # Resolve model name for cost lookup (extract is local-only; uses summary model as reference)
    model_key = {"summarize": "summary", "clean": "cleanup"}.get(model_task, "summary")
    model = cfg.models.get(model_key) or cfg.models.get("summary") or "o4-mini"
    cost_str = _estimate_cost(tokens, model, "input") or "n/a"

    headers = ["Operation", "Model", "Files", "Approx Input Tokens", "Est. Input Cost"]
    rows = [[model_task, model, len(files), tokens, cost_str]]
    render_table(headers, rows, fmt=args.format, color=supports_color(args.color or cfg.color), plain=args.plain)


def cmd_export(args: argparse.Namespace, cfg: Config) -> None:
    records = get_records(args, cfg)
    output = resolve_output_dir(args, cfg, required=False)
    period = getattr(args, "period", None) or cfg.aggregate_period or "auto"
    groups: dict[str, list[MeetingRecord]] = {}
    for rec in records:
        if period == "month":
            prefix = (rec.meeting_date or "unknown")[:7]
        elif period == "auto":
            # Auto: group by year, but also by month within each year
            prefix = (rec.meeting_date or "unknown")[:4]
            groups.setdefault(prefix, []).append(rec)
            prefix = (rec.meeting_date or "unknown")[:7]
        else:
            # year
            prefix = (rec.meeting_date or "unknown")[:4]
        groups.setdefault(prefix, []).append(rec)

    for prefix, vals in groups.items():
        write_meetings_rollup(output / f"{prefix}-Meetings.txt", vals)
        write_transcripts_rollup(output / f"{prefix}-Transcripts.txt", vals)
        write_summaries_rollup(output / f"{prefix}-Meeting-Summaries.txt", vals)
    if cfg.write_processing_json:
        write_processing_json(records, cfg, args)


def write_meetings_rollup(path: Path, records: list[MeetingRecord]) -> None:
    lines = []
    for rec in records:
        lines.append(f"{rec.meeting_date or 'Unknown'}\t{rec.title}\tmerged={rec.merged_path or ''}\tsummaries={len(rec.summaries)}\tproblems={', '.join(rec.problems)}")
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))
    print_file_report(path, len(records))


def write_transcripts_rollup(path: Path, records: list[MeetingRecord]) -> None:
    parts = []
    for rec in records:
        source = rec.cleaned_paths[-1] if rec.cleaned_paths else rec.merged_path
        if not source or not Path(source).is_file():
            continue
        parts.append(section_header(rec, source) + Path(source).read_text(encoding="utf-8", errors="replace"))
    atomic_write_text(path, "\n\n".join(parts) + ("\n" if parts else ""))
    print_file_report(path, len(parts))


def write_summaries_rollup(path: Path, records: list[MeetingRecord]) -> None:
    parts = []
    for rec in records:
        for summary in rec.summaries:
            if Path(summary.path).is_file():
                parts.append(section_header(rec, summary.path) + Path(summary.path).read_text(encoding="utf-8", errors="replace"))
    atomic_write_text(path, "\n\n".join(parts) + ("\n" if parts else ""))
    print_file_report(path, len(parts))


def section_header(rec: MeetingRecord, source: str) -> str:
    return "=" * 80 + f"\nMeeting: {rec.title}\nDate: {rec.meeting_date or 'Unknown'}\nSource: {source}\n" + "=" * 80 + "\n\n"


def print_file_report(path: Path, count: int) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []
    size = path.stat().st_size if path.is_file() else 0
    print(f"Wrote {path} ({count} items, {len(lines)} lines, {size} bytes)")


def cmd_delete_raw(args: argparse.Namespace, cfg: Config) -> None:
    """Move raw meeting directories to to-delete/ when a merged transcript exists."""
    records = get_records(args, cfg)
    output = resolve_output_dir(args, cfg, required=False)
    trash_dir = output / "to-delete"
    candidates = [r for r in records if r.has_raw and r.has_merged and r.raw_dir]

    if not candidates:
        print("No raw directories eligible for deletion (all either missing merged or no raw).")
        return

    print(f"  {len(candidates)} raw directories have merged transcripts and can be removed:")
    print()
    for r in candidates[:args.max or len(candidates)]:
        if args.dry_run:
            print(f"  Would move: {r.raw_dir}")
        else:
            print(f"  {r.meeting_date}  {r.title}")
    print()

    if args.dry_run:
        print(f"  Destination: {trash_dir}/")
        return

    # Confirm
    if not getattr(args, "yes", False) and sys.stdin.isatty():
        print(f"  Destination: {trash_dir}/")
        answer = input("  Move these directories? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            raise SystemExit("Cancelled.")

    trash_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for r in candidates[:args.max or len(candidates)]:
        src = Path(r.raw_dir)  # type: ignore[arg-type]
        dest = trash_dir / src.name
        if dest.exists():
            # Append a numeric suffix to avoid collision
            i = 1
            while dest.exists():
                dest = trash_dir / f"{src.name}.{i}"
                i += 1
        try:
            shutil.move(str(src), str(dest))
            moved += 1
            print(f"  \033[32m✓\033[0m {src.name}")
        except OSError as e:
            print(f"  \033[33m!\033[0m {src.name}: {e}", file=sys.stderr)

    print(f"\n  Moved {moved} directories to {trash_dir}/")


def cmd_clean(args: argparse.Namespace, cfg: Config) -> None:
    records = get_records(args, cfg)
    model = get_model(cfg, args, "cleanup")
    prompt = load_prompt(getattr(args, "cleanup_prompt", None) or cfg.prompts.get("cleanup") or "cleanup_transcript")
    client = client_for(cfg)
    files = [r.merged_path for r in records if r.merged_path and Path(r.merged_path).is_file()]
    confirm_model_operation(args, cfg, "clean", files, model)
    output = resolve_output_dir(args, cfg, required=False)
    resume_done = load_resume_done(output, "clean") if getattr(args, "resume", False) else set()
    journal = None if args.dry_run else RunJournal(output, "clean")
    n_done = n_skipped = n_failed = 0
    for rec in records[: args.max or None]:
        if not rec.merged_path or not Path(rec.merged_path).is_file():
            n_skipped += 1
            continue
        if rec.merged_path in resume_done:
            print(f"Resume: skipping already-cleaned {rec.title}")
            n_skipped += 1
            continue
        year = (rec.meeting_date or str(date.today()))[:4]
        out_dir = output / f"Cleaned-Transcripts-{year}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{Path(rec.merged_path).stem}.{model.replace('/', '--')}.cleaned.txt"
        if out_path.exists() and not args.clobber:
            print(f"Skipping existing cleaned transcript: {out_path}")
            n_skipped += 1
            continue
        if args.dry_run:
            print(f"Would clean {rec.merged_path} with {model} -> {out_path}")
            continue
        text = Path(rec.merged_path).read_text(encoding="utf-8", errors="replace")
        try:
            response = client.chat.completions.create(model=model, messages=chat_messages(prompt, text))
            cleaned = response.choices[0].message.content or ""
        except Exception as exc:
            print_model_error(exc, model=model, operation="clean transcript", label=rec.merged_path, cfg=cfg)
            n_failed += 1
            if journal:
                journal.mark(rec.merged_path, "failed")
            if args.ignore_model_errors:
                continue
            raise SystemExit(1)
        atomic_write_text(out_path, cleaned.strip() + "\n")
        print(f"Wrote {out_path}")
        n_done += 1
        if journal:
            journal.mark(rec.merged_path, "done")
    if not args.dry_run:
        if journal and n_failed == 0:
            journal.finish()
        print(f"\nDone: {n_done} cleaned, {n_skipped} skipped, {n_failed} failed.")
        if n_failed:
            print(f"  {n_failed} failed — re-run with --resume to retry only the unfinished items.")


def cmd_clean_diagnostics(args: argparse.Namespace, cfg: Config) -> None:
    """Delete diagnostic files (raw failed model responses) from the output dir."""
    output = resolve_output_dir(args, cfg, required=False)
    diag_root = output / "Diagnostics"
    if not diag_root.is_dir():
        print("No Diagnostics directory found; nothing to clean.")
        return
    cutoff = None
    older = getattr(args, "older_than", None)
    if older is not None:
        cutoff = datetime.now(timezone.utc).timestamp() - older * 86400
    targets = []
    for path in sorted(diag_root.rglob("*")):
        if not path.is_file():
            continue
        if cutoff is not None and path.stat().st_mtime > cutoff:
            continue
        targets.append(path)
    if not targets:
        print("No diagnostic files match the criteria.")
        return
    total_bytes = sum(p.stat().st_size for p in targets)
    print(f"  {len(targets)} diagnostic file(s), {total_bytes:,} bytes:")
    for p in targets[: args.max or len(targets)]:
        print(f"  {p.relative_to(output)}")
    if args.dry_run:
        print("\nDry run: nothing deleted.")
        return
    if not getattr(args, "yes", False) and sys.stdin.isatty():
        answer = input("  Delete these files? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            raise SystemExit("Cancelled.")
    deleted = 0
    for p in targets:
        try:
            p.unlink()
            deleted += 1
        except OSError as e:
            print(f"  ! could not delete {p}: {e}", file=sys.stderr)
    # Remove now-empty year/Diagnostics dirs.
    for d in sorted(diag_root.rglob("*"), reverse=True):
        if d.is_dir():
            try:
                d.rmdir()
            except OSError:
                pass
    try:
        diag_root.rmdir()
    except OSError:
        pass
    print(f"\nDeleted {deleted} diagnostic file(s).")


def merge_raw_records(args: argparse.Namespace, cfg: Config) -> list[MeetingRecord]:
    records = get_records(args, cfg)
    changed = False
    for rec in records:
        if not rec.has_raw or rec.has_merged:
            continue
        if not rec.raw_dir:
            continue
        if args.dry_run:
            print(f"Would merge raw meeting: {rec.raw_dir}")
            continue
        dir_path = Path(rec.raw_dir)
        caption_content = Path(rec.caption_path).read_text(encoding="utf-8", errors="replace") if rec.caption_path else ""
        chat_entries: list[tuple[str, str, str]] = []
        if rec.chat_path and Path(rec.chat_path).is_file():
            chat_line_count, chat_entries = parse_chat_file(rec.chat_path)
            if chat_line_count > 0 and not chat_entries:
                print(f"  \033[33m!\033[0m Chat file has {chat_line_count} lines but 0 public entries: {rec.chat_path}")
                print(f"    (May contain only private messages, or format not recognized. Merging captions only.)")
        merged_body = merge_captions_and_chat(caption_content, chat_entries).strip()
        if not merged_body:
            continue
        meeting_dt = rec.meeting_datetime or "Unknown"
        merged = f"Meeting Title: {rec.title}\nMeeting Start Datetime: {meeting_dt}\n\n{merged_body}\n"
        year = (rec.meeting_date or str(date.today()))[:4]
        out_dir = resolve_output_dir(args, cfg, required=False) / f"Merged-Transcripts-{year}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = Path(rec.expected_merged_path or out_dir / expected_merged_name(dir_path.name))
        atomic_write_text(out_path, merged)
        rec.merged_path = str(out_path)
        changed = True
        print(f"Wrote {out_path}")
    return get_records(args, cfg) if changed else records


# Built-in keyword sets used to bias person extraction by kind.
# These are heuristic line-level filters for the local (non-LLM) search.
_ACTION_KEYWORDS = [
    "will", "i'll", "going to", "need to", "should", "must", "todo", "to-do",
    "action item", "follow up", "follow-up", "send", "review", "schedule",
    "draft", "prepare", "by friday", "by monday", "by next", "deadline", "due",
    "assign", "owner", "responsible", "take care of", "circle back",
]
_STATEMENT_KEYWORDS = [
    "i think", "i believe", "i recommend", "my concern", "i agree", "i disagree",
    "we should", "we need", "the issue is", "the risk is", "my preference",
    "in my opinion", "i feel", "i suggest", "i propose", "i'd argue",
]


def cmd_extract(args: argparse.Namespace, cfg: Config) -> None:
    records = get_records(args, cfg)
    person_id = getattr(args, "person", None) or cfg.default_person or "me"
    profile = cfg.people.get(person_id, {})
    aliases = profile.get("aliases") or []
    if isinstance(aliases, str):
        aliases = split_list(aliases)

    kind = getattr(args, "kind", None)  # actions | statements | items (None for search)

    if args.extract_object == "search":
        pattern = args.regex or args.match
        if not pattern:
            raise SystemExit("ERROR: extract search requires --regex or --match.")
        person_regex = None
        kind_regex = None
    else:
        terms = aliases or [profile.get("display_name") or person_id]
        if not any(terms):
            raise SystemExit(f"ERROR: no name/aliases configured for person '{person_id}'. Set [person.{person_id}] in config.")
        pattern = "|".join(re.escape(t) for t in terms if t)
        person_regex = re.compile(pattern, re.IGNORECASE)
        # Build kind-specific keyword filter
        kw: list[str] = []
        if kind == "actions":
            kw = _ACTION_KEYWORDS
        elif kind == "statements":
            kw = _STATEMENT_KEYWORDS
        elif kind == "items":
            kw = _ACTION_KEYWORDS + _STATEMENT_KEYWORDS
        kind_regex = re.compile("|".join(re.escape(k) for k in kw), re.IGNORECASE) if kw else None

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise SystemExit(f"ERROR: invalid search pattern: {exc}")
    rows = []
    for r in records:
        sources = [r.merged_path] + r.cleaned_paths + [s.path for s in r.summaries]
        for source in [s for s in sources if s and Path(s).is_file()]:
            lines = Path(source).read_text(encoding="utf-8", errors="replace").splitlines()
            for idx, line in enumerate(lines, start=1):
                if not regex.search(line):
                    continue
                # For person extraction with a kind filter, require a kind keyword too
                if kind_regex is not None and not kind_regex.search(line):
                    continue
                rows.append([r.meeting_date or "", r.title, Path(source).name, idx, line[:180]])
    render_table(["Date", "Title", "Source", "Line", "Text"], rows, fmt=args.format, color=supports_color(args.color or cfg.color), plain=args.plain)


def _auto_clean_if_needed(records: list[MeetingRecord], args: argparse.Namespace, cfg: Config) -> list[MeetingRecord]:
    """Auto-clean merged transcripts that don't have cleaned versions yet."""
    to_clean = [r for r in records if r.has_merged and not r.has_cleaned]
    if not to_clean:
        return records
    cleanup_model = get_model(cfg, args, "cleanup")
    prompt = load_prompt(cfg.prompts.get("cleanup") or "cleanup_transcript")
    # Build augmentation including corrections (cleanup is the primary consumer)
    aug_prompt, _ = build_prompt(cfg, args, "cleanup")
    full_prompt = prompt + "\n\n" + aug_prompt if aug_prompt != prompt else prompt
    client = client_for(cfg)
    files = [r.merged_path for r in to_clean if r.merged_path]
    print(f"  Auto-cleaning {len(files)} transcripts before summarization...")
    confirm_model_operation(args, cfg, "auto-clean", files, cleanup_model)
    for rec in to_clean:
        if not rec.merged_path or not Path(rec.merged_path).is_file():
            continue
        if args.dry_run:
            print(f"  Would auto-clean {rec.merged_path} with {cleanup_model}")
            continue
        year = (rec.meeting_date or str(date.today()))[:4]
        out_dir = resolve_output_dir(args, cfg, required=False) / f"Cleaned-Transcripts-{year}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{Path(rec.merged_path).stem}.{cleanup_model.replace('/', '--')}.cleaned.txt"
        if out_path.exists():
            rec.cleaned_paths.append(str(out_path))
            continue
        text = Path(rec.merged_path).read_text(encoding="utf-8", errors="replace")
        try:
            response = client.chat.completions.create(model=cleanup_model, messages=chat_messages(full_prompt, text))
            cleaned = response.choices[0].message.content or ""
        except Exception as exc:
            print_model_error(exc, model=cleanup_model, operation="auto-clean", label=rec.merged_path, cfg=cfg)
            if args.ignore_model_errors:
                continue
            raise SystemExit(1)
        atomic_write_text(out_path, cleaned.strip() + "\n")
        rec.cleaned_paths.append(str(out_path))
        print(f"  Wrote {out_path}")
    return records


def cmd_summarize(args: argparse.Namespace, cfg: Config) -> None:
    records = merge_raw_records(args, cfg) if args.summarize_object == "raw" else get_records(args, cfg)
    if args.summarize_object == "files":
        records = records_from_files(args.files)
    # Apply --max up front so merge/auto-clean/summarize all operate on the
    # same bounded set (avoids paying for cleanup on records we won't summarize).
    if args.max:
        records = records[: args.max]
    # Auto-clean before summarizing if configured
    if cfg.auto_clean_before_summarize:
        records = _auto_clean_if_needed(records, args, cfg)
    model = get_model(cfg, args, "summary")
    planned_sources = [choose_summary_source(r, cfg, args) for r in records]
    confirm_model_operation(args, cfg, "summarize", [s for s in planned_sources if s], model)
    # Group by source type (cleaned vs merged) so the system prompt stays
    # stable across consecutive calls — maximizes provider prefix caching,
    # since corrections.txt is included for merged but skipped for cleaned.
    def _is_cleaned(rec: MeetingRecord) -> bool:
        s = choose_summary_source(rec, cfg, args)
        return bool(s) and any(s == cp for cp in rec.cleaned_paths)
    records = sorted(records, key=_is_cleaned)
    output = resolve_output_dir(args, cfg, required=False)
    resume_done = load_resume_done(output, "summarize") if getattr(args, "resume", False) else set()
    journal = None if args.dry_run else RunJournal(output, "summarize")
    n_done = n_skipped = n_failed = 0
    for rec in records:
        source = choose_summary_source(rec, cfg, args)
        if not source:
            n_skipped += 1
            continue
        # Resume: skip sources completed in a prior interrupted run
        if source in resume_done:
            print(f"Resume: skipping already-completed {rec.title}")
            n_skipped += 1
            continue
        # Skip if a summary for this model already exists, unless --clobber
        if not args.clobber and summary_exists(rec, source, model, cfg):
            print(f"Skipping existing summary: {rec.title} [{model}] (use --clobber to overwrite)")
            n_skipped += 1
            continue
        if args.dry_run:
            print(f"Would summarize {source} with {model}")
            continue
        # Skip corrections augmentation if source is an already-cleaned transcript
        is_cleaned = any(source == cp for cp in rec.cleaned_paths)
        prompt, prompt_label = build_prompt(cfg, args, "summary", skip_corrections=is_cleaned)
        text = Path(source).read_text(encoding="utf-8", errors="replace")
        data = call_model_json(cfg, args, model=model, operation="summarize", label=source, messages=chat_messages(prompt, text))
        if not data:
            n_failed += 1
            if journal:
                journal.mark(source, "failed")
            continue
        # Validate against the summary schema (non-fatal: warn, save diagnostic, still render)
        warnings = validate_summary_output(data, label=Path(source).name)
        if warnings:
            for w in warnings:
                print(f"  \033[33m! {w}\033[0m", file=sys.stderr)
            save_diagnostic("schema-warnings", source, model, "\n".join(warnings), cfg, args)
        write_summary_outputs(rec, source, data, model, prompt_label, cfg)
        n_done += 1
        if journal:
            journal.mark(source, "done")
    if not args.dry_run:
        # Keep journal only if something failed (so --resume can use it).
        if journal and n_failed == 0:
            journal.finish()
        print(f"\nDone: {n_done} summarized, {n_skipped} skipped, {n_failed} failed.")
        if n_failed:
            print(f"  {n_failed} failed — re-run with --resume to retry only the unfinished items.")


def summary_exists(rec: MeetingRecord, source: str, model: str, cfg: Config) -> bool:
    """Return True if a summary file for this source+model already exists."""
    year = (rec.meeting_date or str(date.today()))[:4]
    out_dir = Path(cfg.output_dir or ".") / f"Summaries-{year}"
    safe_model = model.replace("/", "--")
    stem = Path(source).stem
    return (out_dir / f"{stem}.{safe_model}.summary.json").is_file()


def choose_summary_source(rec: MeetingRecord, cfg: Config, args: argparse.Namespace) -> str | None:
    source_pref = getattr(args, "summarization_source", None) or cfg.summarization_source
    if getattr(args, "only_cleaned_transcripts", False):
        source_pref = "required_cleaned"
    if source_pref in ("cleaned", "required_cleaned"):
        if rec.cleaned_paths:
            return rec.cleaned_paths[-1]
        if source_pref == "required_cleaned":
            print(f"Skipping {rec.title}: no cleaned transcript", file=sys.stderr)
            return None
    if source_pref == "cleaned_if_available" and rec.cleaned_paths:
        return rec.cleaned_paths[-1]
    return rec.merged_path


def records_from_files(files: list[str]) -> list[MeetingRecord]:
    records = []
    for file in files:
        path = Path(file)
        if not path.is_file():
            print(f"WARNING: file not found: {file}", file=sys.stderr)
            continue
        day = parse_date_from_name(path.name)
        if not day:
            # No parseable date in the filename: fall back to the file's
            # modification date so output lands in a real Summaries-YYYY/
            # directory rather than Summaries-unknown/.
            day = date.fromtimestamp(path.stat().st_mtime).isoformat()
            print(f"WARNING: no date in filename '{path.name}'; using file mtime date {day}.", file=sys.stderr)
        title = path.stem
        records.append(MeetingRecord(id=f"{day}-{slugify(title)}", title=title, meeting_date=day, merged_path=str(path)))
    return records


def compute_duration(transcript_text: str) -> str | None:
    """Estimate meeting duration from first/last timestamps in transcript."""
    timestamps = re.findall(r"\]\s+(\d{2}:\d{2}:\d{2})", transcript_text)
    if len(timestamps) < 2:
        return None
    try:
        fmt = "%H:%M:%S"
        start = datetime.strptime(timestamps[0], fmt)
        end = datetime.strptime(timestamps[-1], fmt)
        if end < start:
            # Crossed midnight
            end += timedelta(days=1)
        delta = end - start
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    except Exception:
        return None


def write_summary_outputs(rec: MeetingRecord, source: str, model_data: dict[str, Any], model: str, prompt_label: str, cfg: Config) -> None:
    year = (rec.meeting_date or str(date.today()))[:4]
    out_dir = Path(cfg.output_dir or ".") / f"Summaries-{year}"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_model = model.replace("/", "--")
    stem = Path(source).stem
    txt_path = out_dir / f"{stem}.{safe_model}.summary.txt"
    json_path = out_dir / f"{stem}.{safe_model}.summary.json"

    # Compute meeting metadata from filesystem (not from model)
    transcript_text = Path(source).read_text(encoding="utf-8", errors="replace") if Path(source).is_file() else ""
    meeting_dt = rec.meeting_datetime
    if not meeting_dt and rec.meeting_date:
        meeting_dt = rec.meeting_date

    meeting_section = {
        "title": rec.title,
        "datetime": meeting_dt,
        "duration": compute_duration(transcript_text),
        "source_path": source,
    }

    metadata = {
        "model": model,
        "prompt_label": prompt_label,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_sha256": sha256_file(source),
        "zmm_version": __version__,
    }

    payload = {
        "meeting": meeting_section,
        "model_output": model_data,
        "metadata": metadata,
    }

    atomic_write_text(json_path, json.dumps(payload, indent=2, ensure_ascii=False))
    atomic_write_text(txt_path, render_summary_text(payload))
    print(f"Wrote {txt_path}")
    print(f"Wrote {json_path}")


def render_summary_text(payload: dict[str, Any]) -> str:
    """Render a human-readable .summary.txt from the full payload."""
    meeting = payload.get("meeting", {})
    model_out = payload.get("model_output", payload.get("summary", payload))
    metadata = payload.get("metadata", {})

    improved_title = model_out.get("improved_title") or meeting.get("title") or "UNKNOWN"
    one_liner = model_out.get("one_liner", "")

    lines = [
        f"# {improved_title}",
        "",
        f"Original Title: {meeting.get('title') or 'UNKNOWN'}",
        f"Date & Time:    {meeting.get('datetime') or 'UNKNOWN'}",
        f"Duration:       {meeting.get('duration') or 'UNKNOWN'}",
        f"Model:          {metadata.get('model', 'UNKNOWN')}",
        f"Prompt:         {metadata.get('prompt_label', 'UNKNOWN')}",
        "",
    ]

    if one_liner:
        lines.extend([f"One-liner: {one_liner}", ""])

    lines.extend(["## Summary", "", str(model_out.get("high_level_summary", "")), ""])

    # Key takeaways
    takeaways = model_out.get("key_takeaways", [])
    if takeaways:
        lines.append("## Key Takeaways")
        lines.append("")
        for item in takeaways:
            lines.append(f"- {item}")
        lines.append("")

    # Decisions
    decisions = model_out.get("decisions", []) or []
    if decisions:
        lines.append("## Decisions")
        lines.append("")
        for item in decisions:
            if isinstance(item, dict):
                status = f" [{item.get('status', '?')}]" if item.get("status") else ""
                lines.append(f"- {item.get('decision', item)}{status}")
            else:
                lines.append(f"- {item}")
        lines.append("")

    # Action items
    actions = model_out.get("action_items", []) or []
    if actions:
        lines.append("## Action Items")
        lines.append("")
        for item in actions:
            if isinstance(item, dict):
                owner = item.get("owner") or "Owner unclear"
                deadline = item.get("deadline") or "No deadline"
                lines.append(f"- {item.get('task', '')} [{owner}] ({deadline})")
            else:
                lines.append(f"- {item}")
        lines.append("")

    # Open questions
    questions = model_out.get("open_questions", []) or []
    if questions:
        lines.append("## Open Questions")
        lines.append("")
        for item in questions:
            lines.append(f"- {item}")
        lines.append("")

    # Attendees
    attendees = model_out.get("attendees", {})
    if attendees:
        lines.append("## Attendees")
        lines.append("")
        present = attendees.get("present", [])
        mentioned = attendees.get("mentioned", [])
        if present:
            lines.append(f"Present: {', '.join(present)}")
        if mentioned:
            lines.append(f"Mentioned: {', '.join(mentioned)}")
        lines.append("")

    # Key topics
    topics = model_out.get("key_topics", []) or []
    if topics:
        lines.append("## Key Topics")
        lines.append("")
        for item in topics:
            lines.append(f"- {item}")
        lines.append("")

    # Detailed notes — either a markdown string or legacy array of {topic, notes}
    detailed = model_out.get("detailed_notes", "")
    if detailed:
        lines.append("## Detailed Notes")
        lines.append("")
        if isinstance(detailed, str):
            lines.append(detailed)
        elif isinstance(detailed, list):
            # Legacy format: array of {topic, notes} objects
            for section in detailed:
                if isinstance(section, dict):
                    lines.append(f"### {section.get('topic', 'Notes')}")
                    lines.append("")
                    lines.append(section.get("notes", ""))
                    lines.append("")
                else:
                    lines.append(str(section))
                    lines.append("")

    # LLM notes
    notes = model_out.get("llm_notes", {})
    if notes and isinstance(notes, dict):
        has_any = any(notes.get(k) for k in notes)
        if has_any:
            lines.append("## LLM Notes")
            lines.append("")
            for key, values in notes.items():
                for value in values or []:
                    lines.append(f"- {key.replace('_', ' ')}: {value}")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def write_processing_json(records: list[MeetingRecord], cfg: Config, args: argparse.Namespace) -> None:
    output = resolve_output_dir(args, cfg, required=False)
    by_year: dict[str, list[MeetingRecord]] = {}
    for rec in records:
        year = (rec.meeting_date or "unknown")[:4]
        by_year.setdefault(year, []).append(rec)
    for year, vals in by_year.items():
        path = output / f"{year}-Meeting-Processing.json"
        payload = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "period": {"label": year},
            "meetings": [asdict(v) for v in vals],
        }
        atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"Wrote {path}")


def confirm_model_operation(args: argparse.Namespace, cfg: Config, operation: str, files: list[str], model: str = "") -> None:
    if args.dry_run:
        return
    approx_tokens = sum(count_tokens_in_file(f) for f in files if Path(f).is_file())
    cost_str = _estimate_cost(approx_tokens, model, "input") if model else None

    print()
    print(f"  \033[1mOperation:\033[0m  {operation}")
    print(f"  \033[1mModel:\033[0m      {model or '(default)'}")
    print(f"  \033[1mFiles:\033[0m      {len(files)}")
    print(f"  \033[1mEst. tokens:\033[0m {approx_tokens:,}")
    if cost_str:
        print(f"  \033[1mEst. cost:\033[0m   {cost_str} (input only)")
    print()

    max_tokens = getattr(args, "max_input_tokens", None)
    if max_tokens is not None and approx_tokens > max_tokens:
        raise SystemExit(f"ERROR: estimated input tokens {approx_tokens:,} exceed --max-input-tokens {max_tokens:,}")
    if getattr(args, "yes", False) or not cfg.confirm_model_calls or not sys.stdin.isatty():
        return
    answer = input("  Proceed? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        raise SystemExit("Cancelled.")


def resolve_output_dir(args: argparse.Namespace, cfg: Config, *, required: bool = True) -> Path:
    """Single source of truth for the output directory.

    main() copies CLI --output-dir into cfg, but this also checks args directly
    so the resolution is consistent across all commands.
    """
    output_dir = getattr(args, "output_dir", None) or cfg.output_dir
    if not output_dir:
        if required:
            raise SystemExit("ERROR: --output-dir or [paths] output_dir is required.")
        return Path(".")
    return Path(output_dir)


def get_records(args: argparse.Namespace, cfg: Config) -> list[MeetingRecord]:
    start, end = parse_date_range(getattr(args, "date_range", None))
    input_dir = args.input_dir or cfg.input_dir
    output_dir = str(resolve_output_dir(args, cfg))
    # NOTE: --max is intentionally NOT applied here. Each command applies it
    # where it is semantically meaningful (rows displayed or items processed),
    # avoiding double truncation.
    return discover_inventory(input_dir, output_dir, start=start, end=end, match=getattr(args, "match", None))


# ----------------------------- Parser ----------------------------- #


def add_common(parser: argparse.ArgumentParser, *, is_root: bool = False) -> None:
    # For non-root parsers, use SUPPRESS so defaults don't overwrite values
    # parsed by the top-level parser.
    D = None if is_root else argparse.SUPPRESS
    parser.add_argument("--config", metavar="PATH", default=D,
                        help="Path to zmm config file (default: auto-discovered).")
    parser.add_argument("--input-dir", metavar="DIR", default=D,
                        help="Directory containing raw Zoom meeting folders.")
    parser.add_argument("--output-dir", metavar="DIR", default=D,
                        help="Directory containing Merged-Transcripts-YYYY/, Summaries-YYYY/, etc.")
    parser.add_argument("--date-range", metavar="RANGE", default=D,
                        help="Filter by meeting date. Accepts: YYYY, YYYY-MM, "
                             "YYYY-MM-DD, or ranges like '2026-01 to 2026-03', "
                             "'2026-01-01..2026-01-31'.")
    parser.add_argument("--match", metavar="TEXT", default=D,
                        help="Filter meetings whose filename contains TEXT (case-insensitive substring).")
    parser.add_argument("--max", type=int, metavar="N", default=D,
                        help="Limit the number of items processed or displayed.")
    parser.add_argument("--format", choices=("table", "json", "csv"),
                        default="table" if is_root else D,
                        help="Output format (default: table).")
    parser.add_argument("--color", choices=("auto", "always", "never"),
                        default="auto" if is_root else D,
                        help="Color output mode (default: auto, uses color when stdout is a terminal).")
    parser.add_argument("--plain", action="store_true", default=D if not is_root else False,
                        help="Disable vistab table rendering; use plain aligned text.")
    parser.add_argument("--dry-run", action="store_true", default=D if not is_root else False,
                        help="Show what would be done without writing files or calling models.")
    parser.add_argument("--clobber", action="store_true", default=D if not is_root else False,
                        help="Overwrite existing output files (summaries, cleaned transcripts, etc.).")
    parser.add_argument("--ignore-model-errors", action="store_true", default=D if not is_root else False,
                        help="Continue on model/API errors instead of exiting (warn and skip).")
    parser.add_argument("--yes", action="store_true", default=D if not is_root else False,
                        help="Skip confirmation prompts before model-backed bulk operations.")
    parser.add_argument("--max-input-tokens", type=int, metavar="N", default=D,
                        help="Abort if estimated input tokens exceed this limit.")
    parser.add_argument("--debug", action="store_true", default=D if not is_root else False,
                        help="Print diagnostic information (timings, per-item outcomes, internal errors).")


class _SubcommandParser(argparse.ArgumentParser):
    """ArgumentParser subclass with friendlier error messages for subcommands."""

    def error(self, message: str) -> None:  # type: ignore[override]
        if "required" in message and ("argument" in message or "the following" in message):
            # Missing subcommand
            prog = self.prog
            sys.stderr.write(f"Try: {prog} help\n")
            raise SystemExit(2)
        if "invalid choice" in message:
            # Unknown subcommand — check if it's "help"
            # (handled below via _add_help_subcommand, but just in case)
            prog = self.prog
            sys.stderr.write(f"{prog}: {message}\n")
            sys.stderr.write(f"Try: {prog} help\n")
            raise SystemExit(2)
        super().error(message)


def _add_help_subcommand(sub_action: argparse._SubParsersAction, parent: argparse.ArgumentParser) -> None:
    """Register 'help' as a subcommand that prints the parent's help."""

    class _HelpAction(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            parent.print_help()
            raise SystemExit(0)

    help_parser = sub_action.add_parser("help", add_help=False)
    help_parser.add_argument("_help", nargs="?", action=_HelpAction, default=None)
    help_parser.set_defaults(func=lambda *_: (parent.print_help(), sys.exit(0)))


def add_model_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", nargs="+", metavar="MODEL",
                        help="Model(s) to use (overrides config default for this task).")
    parser.add_argument("--summary-model", metavar="MODEL",
                        help="Model for summarization (overrides [models] summary).")
    parser.add_argument("--cleanup-model", metavar="MODEL",
                        help="Model for transcript cleanup (overrides [models] cleanup).")
    parser.add_argument("--prompt-layer", action="append", metavar="NAME",
                        help="Add a prompt layer by name (can be repeated).")
    parser.add_argument("--prompt-context", action="append", metavar="NAME",
                        help="Add a context prompt layer (e.g. contexts/uri).")
    parser.add_argument("--prompt-person", action="append", metavar="NAME",
                        help="Add a person prompt layer (e.g. people/gabriel).")
    parser.add_argument("--prompt-correction", action="append", metavar="NAME",
                        help="Add a corrections prompt layer (e.g. corrections/uri).")
    parser.add_argument("--no-context", action="store_true",
                        help="Do not send personal augmentation files (~/.config/zmm/prompts/) "
                             "to the model. Keeps names, org details, etc. off the wire.")
    parser.add_argument("--resume", action="store_true",
                        help="Skip items already completed in the most recent interrupted run "
                             "of this operation (uses the run journal in <output-dir>/.zmm-journal/).")


def build_parser() -> argparse.ArgumentParser:
    parser = _SubcommandParser(prog="zmm", description="Manage Zoom meeting transcripts, summaries, reports, and extraction.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    add_common(parser, is_root=True)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_SubcommandParser)
    _add_help_subcommand(sub, parser)

    p_list = sub.add_parser("list")
    list_sub = p_list.add_subparsers(dest="list_object", required=True, parser_class=_SubcommandParser)
    _add_help_subcommand(list_sub, p_list)
    p_models = list_sub.add_parser("models")
    add_common(p_models)
    p_models.add_argument("--provider", metavar="NAME",
                          help="Filter by provider name (e.g. 'uri', 'openai', 'google').")
    p_models.add_argument("--show-stale", action="store_true",
                          help="Also show config-only models not returned by the live API (may be deprecated).")
    p_models.set_defaults(func=cmd_list, list_object="models")
    for name in ("prompts", "meetings"):
        sp = list_sub.add_parser(name)
        add_common(sp)
        sp.set_defaults(func=cmd_list, list_object=name)
    p_missing = list_sub.add_parser("missing")
    add_common(p_missing)
    p_missing.add_argument("missing_kind", nargs="?", choices=("all", "merged", "summaries", "raw", "transcripts"), default="all")
    p_missing.set_defaults(func=cmd_list, list_object="missing")
    # Also register "list missing merged" etc. as top-level list subcommands for IPD compliance
    for mk, alias in [("merged", "transcripts")]:
        sp = list_sub.add_parser(f"missing-{mk}", aliases=[f"missing-{alias}"] if alias else [])
        add_common(sp)
        sp.set_defaults(func=cmd_list, list_object="missing", missing_kind=mk)
    for mk in ("summaries", "raw"):
        sp = list_sub.add_parser(f"missing-{mk}")
        add_common(sp)
        sp.set_defaults(func=cmd_list, list_object="missing", missing_kind=mk)

    p_report = sub.add_parser("report")
    report_sub = p_report.add_subparsers(dest="report_object", required=True, parser_class=_SubcommandParser)
    _add_help_subcommand(report_sub, p_report)
    p_status = report_sub.add_parser("status")
    add_common(p_status)
    p_status.set_defaults(func=cmd_report, report_object="status")
    p_counts = report_sub.add_parser("counts")
    add_common(p_counts)
    p_counts.add_argument("--by", choices=("year", "month", "both"), default="both")
    p_counts.set_defaults(func=cmd_report, report_object="counts")

    p_index = sub.add_parser("index")
    add_common(p_index)
    p_index.add_argument("--rebuild", action="store_true")
    p_index.set_defaults(func=cmd_index)

    p_migrate = sub.add_parser("migrate")
    migrate_sub = p_migrate.add_subparsers(dest="migrate_object", required=True, parser_class=_SubcommandParser)
    _add_help_subcommand(migrate_sub, p_migrate)
    p_legacy = migrate_sub.add_parser("legacy")
    add_common(p_legacy)
    p_legacy.set_defaults(func=cmd_migrate)

    p_write = sub.add_parser("write")
    write_sub = p_write.add_subparsers(dest="write_object", required=True, parser_class=_SubcommandParser)
    _add_help_subcommand(write_sub, p_write)
    p_write_json = write_sub.add_parser("processing-json")
    add_common(p_write_json)
    p_write_json.set_defaults(func=cmd_write_json)

    p_export = sub.add_parser("export")
    export_sub = p_export.add_subparsers(dest="export_object", required=True, parser_class=_SubcommandParser)
    _add_help_subcommand(export_sub, p_export)
    p_agg = export_sub.add_parser("aggregates")
    add_common(p_agg)
    p_agg.add_argument("--period", choices=("auto", "year", "month"), default=None,
                       help="Grouping period for aggregates (default: from config, or 'auto').")
    p_agg.set_defaults(func=cmd_export)

    p_init = sub.add_parser("init")
    init_sub = p_init.add_subparsers(dest="init_object", required=True, parser_class=_SubcommandParser)
    _add_help_subcommand(init_sub, p_init)
    p_init_cfg = init_sub.add_parser("config")
    p_init_cfg.add_argument("--output")
    p_init_cfg.add_argument("--clobber", action="store_true")
    p_init_cfg.set_defaults(func=cmd_init)

    p_show = sub.add_parser("show")
    show_sub = p_show.add_subparsers(dest="show_object", required=True, parser_class=_SubcommandParser)
    _add_help_subcommand(show_sub, p_show)
    p_show_cfg = show_sub.add_parser("config")
    add_common(p_show_cfg)
    p_show_cfg.set_defaults(func=cmd_show)
    p_show_prompt = show_sub.add_parser("prompt")
    add_common(p_show_prompt)
    p_show_prompt.add_argument("--task", choices=("summary", "cleanup"), default="summary",
                              help="Which task's prompt to show (default: summary).")
    p_show_prompt.set_defaults(func=cmd_show_prompt)

    p_est = sub.add_parser("estimate")
    est_sub = p_est.add_subparsers(dest="estimate_object", required=True, parser_class=_SubcommandParser)
    _add_help_subcommand(est_sub, p_est)
    for name in ("summarize", "clean", "extract"):
        sp = est_sub.add_parser(name)
        add_common(sp)
        sp.add_argument("--person")
        sp.set_defaults(func=cmd_estimate, estimate_object=name)

    p_extract = sub.add_parser("extract")
    ext_sub = p_extract.add_subparsers(dest="extract_object", required=True, parser_class=_SubcommandParser)
    _add_help_subcommand(ext_sub, p_extract)
    p_search = ext_sub.add_parser("search")
    add_common(p_search)
    p_search.add_argument("--regex")
    p_search.set_defaults(func=cmd_extract, extract_object="search")
    p_me = ext_sub.add_parser("me")
    add_common(p_me)
    p_me.add_argument("kind", choices=("actions", "statements", "items"))
    p_me.set_defaults(func=cmd_extract, extract_object="person")
    p_person = ext_sub.add_parser("person")
    add_common(p_person)
    p_person.add_argument("kind", choices=("actions", "statements", "items"))
    p_person.add_argument("--person", required=True)
    p_person.set_defaults(func=cmd_extract, extract_object="person")

    p_sum = sub.add_parser("summarize")
    sum_sub = p_sum.add_subparsers(dest="summarize_object", required=True, parser_class=_SubcommandParser)
    _add_help_subcommand(sum_sub, p_sum)
    for name in ("raw", "merged"):
        sp = sum_sub.add_parser(name)
        add_common(sp)
        add_model_options(sp)
        sp.add_argument("--summarization-source", choices=("cleaned_if_available", "cleaned", "required_cleaned", "merged"))
        sp.add_argument("--only-cleaned-transcripts", action="store_true")
        sp.set_defaults(func=cmd_summarize, summarize_object=name)
    p_files = sum_sub.add_parser("files")
    add_common(p_files)
    add_model_options(p_files)
    p_files.add_argument("files", nargs="+")
    p_files.set_defaults(func=cmd_summarize, summarize_object="files")

    p_fix = sub.add_parser("fix")
    fix_sub = p_fix.add_subparsers(dest="fix_object", required=True, parser_class=_SubcommandParser)
    _add_help_subcommand(fix_sub, p_fix)
    p_fix_missing = fix_sub.add_parser("missing")
    missing_sub = p_fix_missing.add_subparsers(dest="missing_object", required=True, parser_class=_SubcommandParser)
    _add_help_subcommand(missing_sub, p_fix_missing)
    p_fix_sum = missing_sub.add_parser("summaries")
    add_common(p_fix_sum)
    add_model_options(p_fix_sum)
    p_fix_sum.set_defaults(func=cmd_summarize, summarize_object="merged")

    p_clean = sub.add_parser("clean")
    clean_sub = p_clean.add_subparsers(dest="clean_object", required=True, parser_class=_SubcommandParser)
    _add_help_subcommand(clean_sub, p_clean)
    p_clean_transcripts = clean_sub.add_parser("transcripts")
    add_common(p_clean_transcripts)
    add_model_options(p_clean_transcripts)
    p_clean_transcripts.add_argument("--cleanup-prompt")
    p_clean_transcripts.set_defaults(func=cmd_clean)
    p_clean_diag = clean_sub.add_parser("diagnostics")
    add_common(p_clean_diag)
    p_clean_diag.add_argument("--older-than", type=int, metavar="DAYS",
                              help="Only delete diagnostics older than DAYS days.")
    p_clean_diag.set_defaults(func=cmd_clean_diagnostics)

    p_delete = sub.add_parser("delete")
    delete_sub = p_delete.add_subparsers(dest="delete_object", required=True, parser_class=_SubcommandParser)
    _add_help_subcommand(delete_sub, p_delete)
    p_delete_raw = delete_sub.add_parser("raw")
    add_common(p_delete_raw)
    p_delete_raw.set_defaults(func=cmd_delete_raw)

    return parser


def main() -> None:
    global _DEBUG
    parser = build_parser()
    args = parser.parse_args()
    _DEBUG = bool(getattr(args, "debug", False))
    cfg = load_config(getattr(args, "config", None), require_api=args.command in ("summarize", "fix"))
    # CLI overrides config for paths
    for attr in ("input_dir", "output_dir"):
        val = getattr(args, attr, None)
        if val:
            setattr(cfg, attr, val)
    # Ensure args has these attributes for command handlers that read them
    if not hasattr(args, "input_dir"):
        args.input_dir = None
    if not hasattr(args, "output_dir"):
        args.output_dir = None
    args.func(args, cfg)


if __name__ == "__main__":
    main()
