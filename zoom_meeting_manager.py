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
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    import openai
except Exception:  # pragma: no cover - command modes without API do not need it
    openai = None

SCRIPT_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = SCRIPT_DIR / "prompts"
LEGACY_CONFIG = "summarize_zoom_transcripts.cfg"
CONFIG_NAME = "zoom_meeting_manager.cfg"

CHECK = "✓"
CROSS = "✗"
WARN = "!"
NA = "-"


# ----------------------------- Data Model ----------------------------- #


@dataclass
class SummaryRecord:
    path: str
    model: str | None = None
    prompt_label: str | None = None
    created_at: str | None = None
    source_transcript_sha256: str | None = None
    summary_sha256: str | None = None
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

    @property
    def has_raw(self) -> bool:
        return bool(self.caption_path or self.chat_path)

    @property
    def has_merged(self) -> bool:
        return bool(self.merged_path and Path(self.merged_path).is_file())

    @property
    def has_cleaned(self) -> bool:
        return any(Path(p).is_file() for p in self.cleaned_paths)

    @property
    def has_summary(self) -> bool:
        return any(Path(s.path).is_file() for s in self.summaries)

    @property
    def has_summary_json(self) -> bool:
        return any(s.json_path and Path(s.json_path).is_file() for s in self.summaries)


@dataclass
class Config:
    config_path: str | None = None
    input_dir: str | None = None
    output_dir: str | None = None
    source: str = "zoom"
    api_key: str | None = None
    base_url: str | None = None
    no_temperature: bool = False
    models: dict[str, str] = field(default_factory=dict)
    prompts: dict[str, str] = field(default_factory=dict)
    prompt_layers: list[str] = field(default_factory=list)
    prompt_contexts: list[str] = field(default_factory=list)
    prompt_people: list[str] = field(default_factory=list)
    prompt_corrections: list[str] = field(default_factory=list)
    default_person: str | None = None
    people: dict[str, dict[str, Any]] = field(default_factory=dict)
    summarization_source: str = "cleaned_if_available"
    auto_clean_before_summarize: bool = False
    write_processing_json: bool = True
    aggregate_period: str = "auto"
    include_all_model_summaries: bool = True
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


def render_table(headers: list[str], rows: list[list[Any]], *, fmt: str, color: bool, plain: bool = False) -> None:
    if fmt == "json":
        print(json.dumps([dict(zip(headers, row)) for row in rows], indent=2, ensure_ascii=False))
        return
    if fmt == "csv":
        writer = csv.writer(sys.stdout)
        writer.writerow(headers)
        writer.writerows(rows)
        return

    if not plain and shutil.which("vistab"):
        try:
            import io
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(headers)
            writer.writerows(rows)
            subprocess.run(["vistab"], input=buf.getvalue(), text=True, check=True)
            return
        except Exception:
            pass

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
    return os.path.expandvars(value)


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
    except Exception:
        return None


def _resolve_opencode_key(value: str) -> str | None:
    """Resolve {file:path} references used in opencode.json API keys."""
    m = re.fullmatch(r"\{file:(.*)\}", value.strip())
    if m:
        key_path = Path(m.group(1)).expanduser()
        if key_path.is_file():
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
        if parser.has_section("source"):
            cfg.source = parser.get("source", "type", fallback=cfg.source)
        if parser.has_section("api"):
            cfg.base_url = parser.get("api", "base_url", fallback=None) or None
            cfg.api_key = expand_env(parser.get("api", "api_key", fallback=None))
            cfg.no_temperature = parser.getboolean("api", "no_temperature", fallback=False)
        if parser.has_section("models"):
            cfg.models = {k: v for k, v in parser.items("models") if v.strip()}
        if parser.has_section("prompts"):
            cfg.prompts = {k: v for k, v in parser.items("prompts") if v.strip()}
        if parser.has_section("prompt_layers"):
            cfg.prompt_layers = split_list(parser.get("prompt_layers", "layers", fallback=""))
            cfg.prompt_contexts = split_list(parser.get("prompt_layers", "contexts", fallback=""))
            cfg.prompt_people = split_list(parser.get("prompt_layers", "people", fallback=""))
            cfg.prompt_corrections = split_list(parser.get("prompt_layers", "corrections", fallback=""))
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
            cfg.include_all_model_summaries = parser.getboolean("output", "include_all_model_summaries", fallback=True)
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
        cfg.no_temperature = raw.get("no_temperature", "").lower() in ("true", "yes", "1")
        if raw.get("default_model"):
            cfg.models["summary"] = raw["default_model"]
        cfg.prompt_layers = split_list(raw.get("prompt_layers"))
        cfg.prompt_contexts = split_list(raw.get("prompt_contexts"))
        cfg.prompt_people = split_list(raw.get("prompt_people"))
        cfg.prompt_corrections = split_list(raw.get("prompt_corrections"))
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
    parts = re.split(r"\s+(?:to)\s+|\.\.|:", value.strip(), maxsplit=1)
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

    def to_dt(ts: str) -> datetime:
        return datetime.strptime(ts, "%H:%M:%S")
    events.sort(key=lambda e: to_dt(e["timestamp"]))

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


def discover_inventory(input_dir: str | None, output_dir: str, *, start: date | None = None, end: date | None = None, match: str | None = None) -> list[MeetingRecord]:
    output = Path(output_dir)
    records: dict[str, MeetingRecord] = {}
    match_lower = match.lower() if match else None

    if input_dir and Path(input_dir).is_dir():
        for child in sorted(Path(input_dir).iterdir()):
            if not child.is_dir():
                continue
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

    for merged_dir in sorted(output.glob("Merged-Transcripts-*")):
        if not merged_dir.is_dir():
            continue
        year = merged_dir.name.replace("Merged-Transcripts-", "")
        for path in sorted(merged_dir.glob("*.txt")):
            if path.name.endswith(".summary.txt"):
                continue
            meeting_date = parse_date_from_name(path.name)
            if not in_range(meeting_date, start, end):
                continue
            if match_lower and match_lower not in path.name.lower():
                continue
            title = re.sub(r"^\d{4}-\d{2}-\d{2}[- ]?\d{0,2}\.?\d{0,2}\.?\d{0,2}[- ]?", "", path.stem)
            title = title.replace("meeting_saved_closed_caption", "").replace("-", " ").strip() or path.stem
            key = f"{meeting_date or year}-{slugify(title)}"
            rec = records.get(key)
            if not rec:
                rec = MeetingRecord(id=key, title=title, meeting_date=meeting_date, merged_path=str(path), expected_merged_path=str(path))
                records[key] = rec
            rec.merged_path = str(path)
            rec.expected_merged_path = rec.expected_merged_path or str(path)

    for cleaned_dir in sorted(output.glob("Cleaned-Transcripts-*")):
        if not cleaned_dir.is_dir():
            continue
        for path in sorted(cleaned_dir.glob("*.txt")):
            meeting_date = parse_date_from_name(path.name)
            if not in_range(meeting_date, start, end):
                continue
            best = find_record_for_file(records, path)
            if best and str(path) not in best.cleaned_paths:
                best.cleaned_paths.append(str(path))

    for summary_dir in sorted(output.glob("Summaries-*")):
        if not summary_dir.is_dir():
            continue
        for path in sorted(summary_dir.glob("*.summary.txt")):
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
                summary_sha256=sha256_file(str(path)),
                json_path=json_path if Path(json_path).is_file() else None,
            ))

    for rec in records.values():
        rec.problems = compute_problems(rec)
    return sorted(records.values(), key=lambda r: (r.meeting_date or "9999-99-99", r.title.lower()))


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


def load_prompt(name: str) -> str:
    path = Path(name).expanduser()
    if not path.is_file():
        path = PROMPTS_DIR / f"{name}.txt"
    if not path.is_file():
        raise SystemExit(f"ERROR: Prompt layer not found: {name}")
    return path.read_text(encoding="utf-8").strip()


def build_prompt(cfg: Config, args: argparse.Namespace, task: str = "summary") -> tuple[str, str]:
    layers = []
    layers.extend(getattr(args, "prompt_layer", None) or [])
    layers.extend(getattr(args, "prompt_context", None) or [])
    layers.extend(getattr(args, "prompt_person", None) or [])
    layers.extend(getattr(args, "prompt_correction", None) or [])
    if not layers:
        layers = cfg.prompt_layers + cfg.prompt_contexts + cfg.prompt_people + cfg.prompt_corrections
    if not layers and cfg.prompts.get(task):
        layers = [cfg.prompts[task]]
    if not layers:
        layers = ["meeting_generic", "output_structured_notes"]
    text = "\n\n".join(f"## Prompt Layer: {layer}\n\n{load_prompt(layer)}" for layer in layers)
    return text, "layers:" + "+".join(layers)


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
    kwargs = {"api_key": cfg.api_key}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return openai.OpenAI(**kwargs)


def call_model_json(cfg: Config, args: argparse.Namespace, *, model: str, messages: list[dict[str, str]], operation: str, label: str) -> dict[str, Any]:
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


def save_diagnostic(kind: str, label: str, model: str, content: str, cfg: Config, args: argparse.Namespace) -> Path:
    year = parse_date_from_name(label) or str(date.today().year)
    safe_label = slugify(Path(label).stem or label)[:120]
    safe_model = model.replace("/", "--")
    diag_dir = Path(cfg.output_dir or args.output_dir or ".") / "Diagnostics" / year[:4]
    diag_dir.mkdir(parents=True, exist_ok=True)
    path = diag_dir / f"{safe_label}.{safe_model}.{kind}.txt"
    path.write_text(content, encoding="utf-8")
    return path


def print_model_error(exc: Exception, *, model: str, operation: str, label: str, cfg: Config) -> None:
    print("ERROR: model/API call failed", file=sys.stderr)
    print(f"  Operation: {operation}", file=sys.stderr)
    print(f"  Model:     {model}", file=sys.stderr)
    print(f"  Item:      {label}", file=sys.stderr)
    print(f"  Endpoint:  {cfg.base_url or 'default OpenAI endpoint'}", file=sys.stderr)
    print(f"  Error:     {type(exc).__name__}: {exc}", file=sys.stderr)
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


def rows_overview(records: list[MeetingRecord], color: bool) -> list[list[Any]]:
    return [[
        r.meeting_date or "",
        r.title,
        mark(r.has_raw, color),
        mark(r.has_merged, color),
        mark(r.has_cleaned, color) if r.cleaned_paths else mark(None, color),
        mark(r.has_summary, color),
        mark(r.has_summary_json, color),
        warn_mark(color) if r.problems else mark(True, color),
        ", ".join(r.problems),
    ] for r in records]


def cmd_list(args: argparse.Namespace, cfg: Config) -> None:
    color = supports_color(args.color or cfg.color)
    if args.list_object == "prompts":
        rows = [[str(p.relative_to(PROMPTS_DIR).with_suffix(""))] for p in sorted(PROMPTS_DIR.rglob("*.txt"))]
        render_table(["Prompt"], rows, fmt=args.format, color=color, plain=args.plain)
        return
    if args.list_object == "models":
        cfg_api = load_config(args.config, require_api=True)
        client = client_for(cfg_api)
        try:
            models = sorted(m.id for m in client.models.list().data)
        except Exception as exc:
            print_model_error(exc, model="(list models)", operation="list models", label="models", cfg=cfg_api)
            raise SystemExit(1)
        render_table(["Model"], [[m] for m in models], fmt=args.format, color=color, plain=args.plain)
        return
    records = get_records(args, cfg)
    if args.list_object == "missing":
        kind = args.missing_kind or "all"
        records = filter_missing(records, kind)
        render_table(["Date", "Title", "Raw", "Merged", "Cleaned", "Summary", "JSON", "State", "Problems"], rows_overview(records, color), fmt=args.format, color=color, plain=args.plain)
        return
    if args.list_object == "meetings":
        render_table(["Date", "Title", "Raw", "Merged", "Summary", "Problems"], [[r.meeting_date or "", r.title, mark(r.has_raw, color), mark(r.has_merged, color), mark(r.has_summary, color), ", ".join(r.problems)] for r in records], fmt=args.format, color=color, plain=args.plain)
        return


def cmd_report(args: argparse.Namespace, cfg: Config) -> None:
    records = get_records(args, cfg)
    color = supports_color(args.color or cfg.color)
    if args.report_object == "status":
        render_table(["Date", "Title", "Raw", "Merged", "Cleaned", "Summary", "JSON", "Problems"], rows_overview(records, color), fmt=args.format, color=color, plain=args.plain)
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
    render_table(["Period", "Meetings", "Raw", "Merged", "Cleaned", "Summaries", "With Problems"], rows, fmt=args.format, color=color, plain=args.plain)


def cmd_index(args: argparse.Namespace, cfg: Config) -> None:
    records = get_records(args, cfg)
    write_processing_json(records, cfg, args)
    print(f"Indexed {len(records)} meeting records.")


def _ask(prompt: str, default: str = "") -> str:
    """Ask a question interactively, returning the answer or default."""
    suffix = f" [{default}]: " if default else ": "
    try:
        answer = input(prompt + suffix).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        answer = ""
    return answer or default


def cmd_init(args: argparse.Namespace, cfg: Config) -> None:
    target = Path(args.output or Path.home() / ".config" / CONFIG_NAME).expanduser()
    if target.exists() and not args.clobber:
        raise SystemExit(f"ERROR: Config exists: {target}. Use --clobber to overwrite.")
    target.parent.mkdir(parents=True, exist_ok=True)

    # Defaults
    input_dir = ""
    output_dir = ""
    base_url = ""
    api_key = "{env:OPENAI_API_KEY}"
    summary_model = "o4-mini"
    display_name = ""
    aliases = ""

    # Interactive wizard when stdin is a terminal
    if sys.stdin.isatty() and not getattr(args, "yes", False):
        print("zmm config wizard\n")
        input_dir = _ask("Raw Zoom meeting directory (input_dir)", input_dir)
        output_dir = _ask("Output directory (output_dir)", output_dir)
        base_url = _ask("API base URL (blank for OpenAI default)", base_url)
        api_key = _ask("API key or env reference", api_key)
        summary_model = _ask("Default summary model", summary_model)
        display_name = _ask("Your display name (for extraction)", display_name)
        aliases = _ask("Your name aliases (comma-separated)", aliases)
        print()

    text = f"""# zoom_meeting_manager.cfg

[paths]
input_dir = {input_dir}
output_dir = {output_dir}

[source]
type = zoom

[api]
base_url = {base_url}
api_key = {api_key}
no_temperature = false

[models]
summary = {summary_model}
cleanup =
extraction =
prioritization =
validation =

[prompts]
summary = meeting_generic
cleanup = cleanup_transcript
extraction = extract_items
prioritization = prioritize_items

[prompt_layers]
layers = meeting_generic, output_structured_notes
contexts =
people =
corrections =

[user]
default_person = me

[person.me]
display_name = {display_name}
aliases = {aliases}
speaker_regexes =
assignment_verbs = can you, could you, please, need you to, follow up, send, review, schedule, draft, prepare, own, take, circle back
commitment_patterns = I will, I'll, I can, I'll take, I'll follow up, I'll send, I'll review, I'll schedule, let me, I need to, I'll own
statement_patterns = I think, I recommend, my concern, I agree, I disagree, we should, we need, the issue is, the risk is, my preference
prompt_context =

[transcripts]
summarization_source = cleaned_if_available
auto_clean_before_summarize = false

[output]
write_processing_json = true
aggregate_period = auto
include_all_model_summaries = true
"""
    target.write_text(text, encoding="utf-8")
    print(f"Wrote {target}")
    print("Next: edit paths/API settings, then run `zmm index --rebuild` and `zmm list missing`.")


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
    chars = sum(Path(f).stat().st_size for f in files)
    tokens = chars // 4

    # Resolve model name for cost lookup
    model_key = {"summarize": "summary", "clean": "cleanup", "extract": "extraction"}.get(model_task, "summary")
    model = cfg.models.get(model_key) or "o4-mini"
    cost_str = _estimate_cost(tokens, model, "input") or "n/a"

    headers = ["Operation", "Model", "Files", "Approx Input Tokens", "Est. Input Cost"]
    rows = [[model_task, model, len(files), tokens, cost_str]]
    render_table(headers, rows, fmt=args.format, color=supports_color(args.color or cfg.color), plain=args.plain)


def cmd_export(args: argparse.Namespace, cfg: Config) -> None:
    records = get_records(args, cfg)
    output = Path(cfg.output_dir or args.output_dir or ".")
    groups: dict[str, list[MeetingRecord]] = {}
    for rec in records:
        if args.period == "month":
            prefix = (rec.meeting_date or "unknown")[:7]
        elif args.period == "range" and args.date_range:
            start, end = parse_date_range(args.date_range)
            prefix = f"{start.isoformat()}-to-{end.isoformat()}" if start and end else "range"
        else:
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
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    print_file_report(path, len(records))


def write_transcripts_rollup(path: Path, records: list[MeetingRecord]) -> None:
    parts = []
    for rec in records:
        source = rec.cleaned_paths[-1] if rec.cleaned_paths else rec.merged_path
        if not source or not Path(source).is_file():
            continue
        parts.append(section_header(rec, source) + Path(source).read_text(encoding="utf-8", errors="replace"))
    path.write_text("\n\n".join(parts) + ("\n" if parts else ""), encoding="utf-8")
    print_file_report(path, len(parts))


def write_summaries_rollup(path: Path, records: list[MeetingRecord]) -> None:
    parts = []
    for rec in records:
        for summary in rec.summaries:
            if Path(summary.path).is_file():
                parts.append(section_header(rec, summary.path) + Path(summary.path).read_text(encoding="utf-8", errors="replace"))
    path.write_text("\n\n".join(parts) + ("\n" if parts else ""), encoding="utf-8")
    print_file_report(path, len(parts))


def section_header(rec: MeetingRecord, source: str) -> str:
    return "=" * 80 + f"\nMeeting: {rec.title}\nDate: {rec.meeting_date or 'Unknown'}\nSource: {source}\n" + "=" * 80 + "\n\n"


def print_file_report(path: Path, count: int) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []
    size = path.stat().st_size if path.is_file() else 0
    print(f"Wrote {path} ({count} items, {len(lines)} lines, {size} bytes)")


def cmd_clean(args: argparse.Namespace, cfg: Config) -> None:
    records = get_records(args, cfg)
    model = get_model(cfg, args, "cleanup")
    prompt = load_prompt(getattr(args, "cleanup_prompt", None) or cfg.prompts.get("cleanup") or "cleanup_transcript")
    client = client_for(cfg)
    files = [r.merged_path for r in records if r.merged_path and Path(r.merged_path).is_file()]
    confirm_model_operation(args, cfg, "clean", files)
    for rec in records[: args.max or None]:
        if not rec.merged_path or not Path(rec.merged_path).is_file():
            continue
        year = (rec.meeting_date or str(date.today()))[:4]
        out_dir = Path(cfg.output_dir or args.output_dir or ".") / f"Cleaned-Transcripts-{year}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{Path(rec.merged_path).stem}.{model.replace('/', '--')}.cleaned.txt"
        if out_path.exists() and not args.clobber:
            print(f"Skipping existing cleaned transcript: {out_path}")
            continue
        if args.dry_run:
            print(f"Would clean {rec.merged_path} with {model} -> {out_path}")
            continue
        text = Path(rec.merged_path).read_text(encoding="utf-8", errors="replace")
        try:
            response = client.chat.completions.create(model=model, messages=[{"role": "system", "content": prompt}, {"role": "user", "content": text}])
            cleaned = response.choices[0].message.content or ""
        except Exception as exc:
            print_model_error(exc, model=model, operation="clean transcript", label=rec.merged_path, cfg=cfg)
            if args.ignore_model_errors:
                continue
            raise SystemExit(1)
        out_path.write_text(cleaned.strip() + "\n", encoding="utf-8")
        print(f"Wrote {out_path}")


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
        if rec.chat_path:
            _line_count, chat_entries = parse_chat_file(rec.chat_path)
        merged_body = merge_captions_and_chat(caption_content, chat_entries).strip()
        if not merged_body:
            continue
        meeting_dt = rec.meeting_datetime or "Unknown"
        merged = f"Meeting Title: {rec.title}\nMeeting Start Datetime: {meeting_dt}\n\n{merged_body}\n"
        year = (rec.meeting_date or str(date.today()))[:4]
        out_dir = Path(cfg.output_dir or args.output_dir or ".") / f"Merged-Transcripts-{year}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = Path(rec.expected_merged_path or out_dir / expected_merged_name(dir_path.name))
        out_path.write_text(merged, encoding="utf-8")
        rec.merged_path = str(out_path)
        changed = True
        print(f"Wrote {out_path}")
    return get_records(args, cfg) if changed else records


def cmd_extract(args: argparse.Namespace, cfg: Config) -> None:
    records = get_records(args, cfg)
    person_id = getattr(args, "person", None) or cfg.default_person or "me"
    profile = cfg.people.get(person_id, {})
    aliases = profile.get("aliases") or []
    if isinstance(aliases, str):
        aliases = split_list(aliases)
    if args.extract_object == "search":
        pattern = args.regex or args.match
        if not pattern:
            raise SystemExit("ERROR: extract search requires --regex or --match.")
    else:
        terms = aliases or [profile.get("display_name") or person_id]
        pattern = "|".join(re.escape(t) for t in terms if t)
    regex = re.compile(pattern, re.IGNORECASE)
    rows = []
    for r in records:
        sources = [r.merged_path] + r.cleaned_paths + [s.path for s in r.summaries]
        for source in [s for s in sources if s and Path(s).is_file()]:
            lines = Path(source).read_text(encoding="utf-8", errors="replace").splitlines()
            for idx, line in enumerate(lines, start=1):
                if regex.search(line):
                    rows.append([r.meeting_date or "", r.title, Path(source).name, idx, line[:180]])
    render_table(["Date", "Title", "Source", "Line", "Text"], rows, fmt=args.format, color=supports_color(args.color or cfg.color), plain=args.plain)


def cmd_summarize(args: argparse.Namespace, cfg: Config) -> None:
    records = merge_raw_records(args, cfg) if args.summarize_object == "raw" else get_records(args, cfg)
    if args.summarize_object == "files":
        records = records_from_files(args.files)
    model = get_model(cfg, args, "summary")
    prompt, prompt_label = build_prompt(cfg, args, "summary")
    planned_sources = [choose_summary_source(r, cfg, args) for r in records]
    confirm_model_operation(args, cfg, "summarize", [s for s in planned_sources if s])
    for rec in records[: args.max or None]:
        source = choose_summary_source(rec, cfg, args)
        if not source:
            continue
        if args.dry_run:
            print(f"Would summarize {source} with {model}")
            continue
        text = Path(source).read_text(encoding="utf-8", errors="replace")
        schema_prompt = prompt + "\n\nReturn strict JSON only using the requested meeting summary schema."
        data = call_model_json(cfg, args, model=model, operation="summarize", label=source, messages=[{"role": "system", "content": schema_prompt}, {"role": "user", "content": text}])
        if not data:
            continue
        write_summary_outputs(rec, source, data, model, prompt_label, cfg)


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
        title = path.stem
        records.append(MeetingRecord(id=f"{day or 'unknown'}-{slugify(title)}", title=title, meeting_date=day, merged_path=str(path)))
    return records


def write_summary_outputs(rec: MeetingRecord, source: str, data: dict[str, Any], model: str, prompt_label: str, cfg: Config) -> None:
    year = (rec.meeting_date or str(date.today()))[:4]
    out_dir = Path(cfg.output_dir or ".") / f"Summaries-{year}"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_model = model.replace("/", "--")
    stem = Path(source).stem
    txt_path = out_dir / f"{stem}.{safe_model}.summary.txt"
    json_path = out_dir / f"{stem}.{safe_model}.summary.json"
    metadata = {
        "model": model,
        "prompt_label": prompt_label,
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "source_path": source,
        "source_sha256": sha256_file(source),
    }
    payload = {"metadata": metadata, "summary": data}
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    txt_path.write_text(render_summary_text(data, metadata), encoding="utf-8")
    print(f"Wrote {txt_path}")
    print(f"Wrote {json_path}")


def render_summary_text(data: dict[str, Any], metadata: dict[str, Any]) -> str:
    summary = data.get("summary", data)
    meeting = summary.get("meeting", {}) if isinstance(summary, dict) else {}
    lines = [
        "Meeting Notes Summary Generated by LLM",
        f"Model: {metadata.get('model')}",
        f"Prompt: {metadata.get('prompt_label')}",
        f"Source: {metadata.get('source_path')}",
        "",
        "1. General Meeting Information",
        f"Meeting Title: {meeting.get('title') or summary.get('title') or 'UNKNOWN'}",
        f"Date & Time: {meeting.get('datetime') or 'UNKNOWN'}",
        f"Approx. Duration: {meeting.get('duration') or 'UNKNOWN'}",
        "",
        "2. High-Level Summary",
        str(summary.get("high_level_summary", "")),
        "",
        "3. Key Decisions",
    ]
    for item in summary.get("decisions", []) or []:
        lines.append(f"- {item.get('decision', item)}")
    lines.extend(["", "4. Action Items / To-Do List"])
    for item in summary.get("action_items", []) or []:
        if isinstance(item, dict):
            lines.append(f"- Task: {item.get('task', '')}")
            lines.append(f"  Owner: {item.get('owner', 'Owner unclear')}")
            lines.append(f"  Requested by: {item.get('requested_by', 'Unknown')}")
            lines.append(f"  Deadline: {item.get('deadline', 'None stated')}")
            lines.append(f"  Confidence: {item.get('confidence', 'unknown')}")
        else:
            lines.append(f"- {item}")
    lines.extend(["", "5. Open Questions / Follow-Up Items"])
    for item in summary.get("open_questions", []) or []:
        lines.append(f"- {item}")
    lines.extend(["", "6. Key Topics Discussed"])
    for item in summary.get("key_topics", []) or []:
        lines.append(f"- {item}")
    lines.extend(["", "7. LLM Notes: Assumptions / Ambiguities"])
    notes = summary.get("llm_notes", {})
    if isinstance(notes, dict):
        for key, values in notes.items():
            for value in values or []:
                lines.append(f"- {key}: {value}")
    return "\n".join(lines).strip() + "\n"


def write_processing_json(records: list[MeetingRecord], cfg: Config, args: argparse.Namespace) -> None:
    output = Path(cfg.output_dir or args.output_dir or ".")
    by_year: dict[str, list[MeetingRecord]] = {}
    for rec in records:
        year = (rec.meeting_date or "unknown")[:4]
        by_year.setdefault(year, []).append(rec)
    for year, vals in by_year.items():
        path = output / f"{year}-Meeting-Processing.json"
        payload = {
            "schema_version": 1,
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "period": {"label": year},
            "meetings": [asdict(v) for v in vals],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {path}")


def confirm_model_operation(args: argparse.Namespace, cfg: Config, operation: str, files: list[str]) -> None:
    if args.dry_run:
        return
    total_bytes = sum(Path(f).stat().st_size for f in files if Path(f).is_file())
    approx_tokens = total_bytes // 4
    print(f"Model operation: {operation}; files={len(files)}; approx input tokens={approx_tokens}")
    max_tokens = getattr(args, "max_input_tokens", None)
    if max_tokens is not None and approx_tokens > max_tokens:
        raise SystemExit(f"ERROR: estimated input tokens {approx_tokens} exceed --max-input-tokens {max_tokens}")
    if getattr(args, "yes", False) or not cfg.confirm_model_calls or not sys.stdin.isatty():
        return
    answer = input("Proceed with model call(s)? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        raise SystemExit("Cancelled.")


def get_records(args: argparse.Namespace, cfg: Config) -> list[MeetingRecord]:
    start, end = parse_date_range(getattr(args, "date_range", None))
    input_dir = args.input_dir or cfg.input_dir
    output_dir = args.output_dir or cfg.output_dir
    if not output_dir:
        raise SystemExit("ERROR: --output-dir or [paths] output_dir is required.")
    return discover_inventory(input_dir, output_dir, start=start, end=end, match=getattr(args, "match", None))[: getattr(args, "max", None) or None]


# ----------------------------- Parser ----------------------------- #


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config")
    parser.add_argument("--input-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--date-range")
    parser.add_argument("--match")
    parser.add_argument("--max", type=int)
    parser.add_argument("--format", choices=("table", "json", "csv"), default="table")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--plain", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--clobber", action="store_true")
    parser.add_argument("--ignore-model-errors", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Do not prompt before model-backed operations.")
    parser.add_argument("--max-input-tokens", type=int)


def add_model_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", nargs="+")
    parser.add_argument("--summary-model")
    parser.add_argument("--cleanup-model")
    parser.add_argument("--extraction-model")
    parser.add_argument("--prioritization-model")
    parser.add_argument("--prompt-layer", action="append")
    parser.add_argument("--prompt-context", action="append")
    parser.add_argument("--prompt-person", action="append")
    parser.add_argument("--prompt-correction", action="append")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zmm", description="Manage Zoom meeting transcripts, summaries, reports, and extraction.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    add_common(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list")
    add_common(p_list)
    list_sub = p_list.add_subparsers(dest="list_object", required=True)
    for name in ("models", "prompts", "meetings"):
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
    add_common(p_report)
    report_sub = p_report.add_subparsers(dest="report_object", required=True)
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
    add_common(p_migrate)
    migrate_sub = p_migrate.add_subparsers(dest="migrate_object", required=True)
    p_legacy = migrate_sub.add_parser("legacy")
    add_common(p_legacy)
    p_legacy.set_defaults(func=cmd_index)

    p_write = sub.add_parser("write")
    add_common(p_write)
    write_sub = p_write.add_subparsers(dest="write_object", required=True)
    p_write_json = write_sub.add_parser("processing-json")
    add_common(p_write_json)
    p_write_json.set_defaults(func=cmd_index)

    p_export = sub.add_parser("export")
    add_common(p_export)
    export_sub = p_export.add_subparsers(dest="export_object", required=True)
    p_agg = export_sub.add_parser("aggregates")
    add_common(p_agg)
    p_agg.add_argument("--period", choices=("auto", "year", "month", "range"), default="auto")
    p_agg.set_defaults(func=cmd_export)

    p_init = sub.add_parser("init")
    init_sub = p_init.add_subparsers(dest="init_object", required=True)
    p_init_cfg = init_sub.add_parser("config")
    p_init_cfg.add_argument("--output")
    p_init_cfg.add_argument("--clobber", action="store_true")
    p_init_cfg.set_defaults(func=cmd_init)

    p_est = sub.add_parser("estimate")
    add_common(p_est)
    est_sub = p_est.add_subparsers(dest="estimate_object", required=True)
    for name in ("summarize", "clean", "extract"):
        sp = est_sub.add_parser(name)
        add_common(sp)
        sp.add_argument("--person")
        sp.set_defaults(func=cmd_estimate, estimate_object=name)

    p_extract = sub.add_parser("extract")
    add_common(p_extract)
    ext_sub = p_extract.add_subparsers(dest="extract_object", required=True)
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
    add_common(p_sum)
    add_model_options(p_sum)
    sum_sub = p_sum.add_subparsers(dest="summarize_object", required=True)
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
    add_common(p_fix)
    fix_sub = p_fix.add_subparsers(dest="fix_object", required=True)
    p_fix_missing = fix_sub.add_parser("missing")
    missing_sub = p_fix_missing.add_subparsers(dest="missing_object", required=True)
    p_fix_sum = missing_sub.add_parser("summaries")
    add_common(p_fix_sum)
    add_model_options(p_fix_sum)
    p_fix_sum.set_defaults(func=cmd_summarize, summarize_object="merged")

    p_clean = sub.add_parser("clean")
    add_common(p_clean)
    clean_sub = p_clean.add_subparsers(dest="clean_object", required=True)
    p_clean_transcripts = clean_sub.add_parser("transcripts")
    add_common(p_clean_transcripts)
    add_model_options(p_clean_transcripts)
    p_clean_transcripts.add_argument("--cleanup-prompt")
    p_clean_transcripts.set_defaults(func=cmd_clean)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    cfg = load_config(getattr(args, "config", None), require_api=args.command in ("summarize", "fix"))
    for attr in ("input_dir", "output_dir"):
        val = getattr(args, attr, None)
        if val:
            setattr(cfg, attr, val)
    args.func(args, cfg)


if __name__ == "__main__":
    main()
