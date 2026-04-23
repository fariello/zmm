#!/usr/bin/env python3
"""
summarize_meeting_transcripts.py

This script consolidates Zoom meeting transcripts (captions and chat logs) and
sends the cleaned, merged transcripts to OpenAI for summarization.

Modes:
  Default mode: process raw meeting directories.
    Reads caption/chat files, merges them chronologically, saves a consolidated
    transcript, then summarizes it. Optionally moves originals to to-delete/.

  --from-merged mode: re-summarize already-merged transcripts.
    Reads previously saved Merged-Transcripts-YYYY/*.txt files directly and
    sends them to the model(s) again. Useful for re-running on a newer model
    or a changed prompt. Never moves the merged transcripts in this mode.
    Use --year YYYY to limit to a single year's directory.

Features:
1. Processes meeting directories containing Zoom transcript and chat files.
2. Merges captions and chat chronologically to reduce token count and remove redundancy.
3. Adds meeting metadata (title and start datetime) to the consolidated transcript.
4. Saves consolidated transcripts in OUTPUT_DIR/Merged-Transcripts-YYYY/ (optional).
5. Summarizes transcripts using OpenAI's chat models into structured meeting notes.
6. Moves consolidated transcripts to OUTPUT_DIR/to-delete/ (unless --keep-consolidated-transcript).
7. Cleans up empty meeting directories after processing.
8. Offers dry-run mode, max file count, and clobber protection.

Note: Sets temperature to 0.2 for non-reasoning models for improved precision.
"""

import os
import re
import shutil
import argparse
import logging
import unicodedata
from typing import IO, Tuple
from contextlib import contextmanager
from datetime import datetime, timedelta, date
from termcolor import colored
import openai


# ---------------------- Prompt Loading ---------------------- #

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


def list_available_prompts() -> list:
    """
    Return a sorted list of prompt names available in PROMPTS_DIR.

    Each name corresponds to a .txt file in the prompts/ directory
    alongside the script. The returned names have the .txt extension stripped.
    """
    if not os.path.isdir(PROMPTS_DIR):
        return []
    return sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(PROMPTS_DIR)
        if f.endswith(".txt")
    )


def load_prompt(name_or_path: str) -> str:
    """
    Load a system prompt by name or explicit file path.

    Resolution rules:
      1. If name_or_path looks like a path (starts with "/", ".", or contains
         a path separator), treat it as a direct file path.
      2. Otherwise look up PROMPTS_DIR/<name_or_path>.txt.

    Args:
        name_or_path: Prompt name (e.g. "default", "interview") or a path
                      to a .txt file (absolute or relative to CWD).

    Returns:
        The prompt text, stripped of leading/trailing whitespace.

    Raises:
        SystemExit(2): If the file cannot be found or read, with an
                       actionable error message listing available prompts.
    """
    if (
        os.path.isabs(name_or_path)
        or name_or_path.startswith(".")
        or os.sep in name_or_path
    ):
        prompt_path = name_or_path
    else:
        prompt_path = os.path.join(PROMPTS_DIR, f"{name_or_path}.txt")

    if not os.path.isfile(prompt_path):
        available = list_available_prompts()
        avail_str = ", ".join(available) if available else "(none found)"
        print(f"ERROR: Prompt file not found: {prompt_path}")
        print(f"Available prompts in {PROMPTS_DIR}/: {avail_str}")
        print(f"You may also pass an absolute or relative path to a .txt file.")
        raise SystemExit(2)

    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError as exc:
        print(f"ERROR: Could not read prompt file '{prompt_path}': {exc}")
        raise SystemExit(2)


def resolve_prompt(args) -> str:
    """
    Resolve the final system prompt from CLI arguments.

    Resolution order:
      1. If --prompt-string is given, use that text directly.
      2. Otherwise load the prompt named by --prompt (default: 'default').
      3. If --add is given, append it to the resolved prompt.

    A human-readable label is stored on args.prompt_label so that it can
    be recorded in summary file headers and log messages without needing
    to pass it through the call stack separately.

    Args:
        args: Parsed CLI arguments.

    Returns:
        The final prompt text to send as the system message.
    """
    if args.prompt_string:
        text = args.prompt_string
        label = "<prompt-string>"
    else:
        text = load_prompt(args.prompt)
        label = args.prompt

    if args.add:
        text = text + "\n\n" + args.add
        label += " (+add)"

    args.prompt_label = label  # store for logging and summary headers
    return text


# ---------------------- Utility Functions ---------------------- #



def log_and_print(preface: str, color: str, message: str, level: str = "info"):
    """
    Logs and prints a message with a colored preface.

    Args:
        preface (str): A label to prefix the message with (e.g., "INFO:", "WARNING:")
        color (str): Color for the console output (e.g., "green", "red").
        message (str): The actual message text.
        level (str): Logging level (info, warning, error, debug).
    """
    colored_preface = colored(preface, color, attrs=["bold"])
    full_message_console = f"{colored_preface} {message}"
    full_message_plain = f"{preface} {message}"

    print(full_message_console)

    if level == "info":
        logging.info(full_message_plain)
    elif level == "warning":
        logging.warning(full_message_plain)
    elif level == "error":
        logging.error(full_message_plain)
    elif level == "debug":
        logging.debug(full_message_plain)
    else:
        logging.info(full_message_plain)
        pass  # for auto indentation
    pass  # for auto indentation


def debug(msg: str) -> None:
    global args
    if not args.debug:
        return
    return log_and_print("DEBUG:   ", "cyan", msg, "debug")


def info(msg: str) -> None:
    return log_and_print("INFO:    ", "green", msg, "info")


def warn(msg: str) -> None:
    return log_and_print("WARNING: ", "yellow", msg, "warning")


def dry(msg: str) -> None:
    return log_and_print("DRY-RUN: ", "cyan", msg, "info")


def safe_action(action_desc: str, action_func=None, *args, dry_run=False, **kwargs):
    """
    Executes an action safely with dry-run support and logging.

    Args:
        action_desc (str): A description of the action being performed.
        action_func (callable or None): The function to call to execute the action.
        *args: Positional arguments to pass to the function.
        dry_run (bool): If True, do not execute, only log.
        **kwargs: Keyword arguments to pass to the function.
    """
    if dry_run:
        dry(action_desc)
        return
    if action_func:
        info(action_desc)
        return action_func(*args, **kwargs)
    pass  # for auto indentation


@contextmanager
def safe_write(path: str) -> IO[str]:
    """
    Context manager for safely writing to a file. If the target file exists,
    it is backed up to the next available numbered extension (.001, .002, etc.)
    before writing a new file.

    Args:
        path (str): Path to the file to write.

    Yields:
        IO[str]: File object opened for writing in UTF-8.

    Example:
        >>> with safe_write("foo.txt") as out:
        ...     out.write("Hello World")
    """
    if os.path.exists(path):
        base, ext = os.path.splitext(path)
        counter = 1
        while True:
            backup_name = f"{base}.{counter:03d}{ext}"
            if not os.path.exists(backup_name):
                info(f"Backing up {path} to {backup_name}.")
                shutil.move(path, backup_name)
                break
            counter += 1

    info(f"✏️ ➜📄 Writing {path}.")
    f = open(path, "w", encoding="utf-8")
    try:
        yield f
    finally:
        f.close()
        pass  # for auto-indentation
    pass  # for auto-indentation


def safe_trash(path: str, trash_dir: str, dry_run: bool = False) -> None:
    """
    Move a file or directory to a trash staging area instead of deleting it.

    The item is moved to trash_dir/<basename>. If an item with that name
    already exists in trash_dir (from a previous run), a numeric suffix
    (.001, .002, ...) is appended to avoid collisions.

    Args:
        path:      Absolute path to the file or directory to move.
        trash_dir: Destination staging directory (created if needed).
        dry_run:   If True, log the action but do not move anything.
    """
    basename = os.path.basename(path.rstrip(os.sep))
    dest = os.path.join(trash_dir, basename)
    if os.path.exists(dest):
        stem, ext = os.path.splitext(basename)
        counter = 1
        while True:
            dest = os.path.join(trash_dir, f"{stem}.{counter:03d}{ext}")
            if not os.path.exists(dest):
                break
            counter += 1
    if dry_run:
        dry(f"Would move to to-delete: {path} -> {dest}")
        return
    os.makedirs(trash_dir, exist_ok=True)
    info(f"Moving to to-delete: {path} -> {dest}")
    shutil.move(path, dest)


def clean_filename(filename: str) -> str:
    """
    Cleans a filename by replacing any sequence of characters that are not
    letters, digits, or dots with a single dash. Collapses multiple dashes
    and trims leading/trailing dashes or underscores.

    Args:
        filename (str): Original filename.

    Returns:
        str: Cleaned filename.
    """
    name, ext = os.path.splitext(filename)
    name = unicodedata.normalize("NFKD", name)
    # Replace any run of characters that are NOT A-Z, a-z, 0-9, or dot with '-'
    name = re.sub(r"[^A-Za-z0-9.]+", "-", name)
    # Collapse multiple dashes
    name = re.sub(r"-+", "-", name)
    # Trim leading/trailing dashes or underscores
    name = name.strip("-_")
    return name + ext


def read_openai_api_key() -> str:
    """
    Reads the OpenAI API key from ~/.config/openai.cfg.

    Returns:
        str: The API key.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the api_key is not found in the config file.
    """
    config_path = os.path.expanduser("~/.config/openai.cfg")
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"OpenAI config file not found at {config_path}")
    info(f"🔍 Reading config file: '{config_path}'")
    with open(config_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("api_key"):
                _, key = line.split("=", 1)
                return key.strip()
            pass  # for auto indentation
        pass  # for auto indentation
    raise ValueError(f"No 'api_key' found in {config_path}")


def compute_duration(transcript_text: str) -> str:
    """
    Estimates meeting duration by scanning timestamps in speaker lines.

    Args:
        transcript_text (str): Consolidated transcript text.

    Returns:
        str: Duration in HH:MM:SS or "Unknown".
    """
    timestamps = []
    pattern = re.compile(r"\[.*?\] (\d{2}:\d{2}:\d{2}):")

    for line in transcript_text.splitlines():
        match = pattern.search(line)
        if match:
            timestamps.append(match.group(1))
            pass  # for auto indentation
        pass  # for auto indentation

    if len(timestamps) < 2:
        return "Unknown"
    try:
        fmt = "%H:%M:%S"
        start = datetime.strptime(timestamps[0], fmt)
        end = datetime.strptime(timestamps[-1], fmt)
        if end < start:
            end += timedelta(days=1)
        duration = end - start
        return str(duration)
    except Exception:
        return "Unknown"
    pass  # for auto indentation


# ---------------------- Transcript Consolidation ---------------------- #

def parse_chat_file(chat_file_path: str) -> list[tuple[str, str, str]]:
    """
    Parses Zoom chat file lines into (timestamp, speaker, message).

    Handles two known Zoom chat file formats:

    Old format (timestamp is HH:MM:SS only, message inline):
        12:34:56 From Alice to Everyone: Hello there

    New format (timestamp is YYYY-MM-DD HH:MM:SS, message on following indented line):
        2026-04-21 15:02:29 From Alice to Everyone:
                Hello there

    Both formats are accepted. Blank lines between entries are ignored.
    Non-header lines with content are treated as message continuation.

    Only entries addressed "to Everyone" are kept; private chats are discarded.

    Args:
        chat_file_path (str): Path to chat file.

    Returns:
        (line_count, list[tuple]): Total lines read and parsed (timestamp, speaker, message) entries.
    """
    chat_entries = []
    # Accept an optional "YYYY-MM-DD " date prefix before the HH:MM:SS time.
    # The message text may be empty on the header line (new format puts it on
    # the next indented line instead).
    pattern = re.compile(
        r'^(?:\d{4}-\d{2}-\d{2} )?'    # optional date prefix  (new format)
        r'(\d{2}:\d{2}:\d{2}) '         # HH:MM:SS timestamp    (captured group 1)
        r'From (.*?) to Everyone:\s*'   # speaker               (captured group 2)
        r'(.*)'                          # optional inline msg   (captured group 3)
    )
    current_timestamp = None
    current_speaker = None
    current_message = ''

    info(f"🔍 Reading chat file: '{chat_file_path}'")
    line_no = 0
    with open(chat_file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line_no += 1
            line = line.rstrip()
            match = pattern.match(line)
            if match:
                # Save the previous entry before starting a new one.
                if current_timestamp:
                    chat_entries.append((current_timestamp, current_speaker, current_message.strip()))
                current_timestamp, current_speaker, current_message = match.groups()
            else:
                # Blank lines between entries are fine; non-blank continuation
                # lines (indented message body in new format) are appended.
                stripped = line.strip()
                if current_timestamp and stripped:
                    current_message += (' ' if current_message else '') + stripped

        if current_timestamp:
            chat_entries.append((current_timestamp, current_speaker, current_message.strip()))

    return line_no, chat_entries


def merge_captions_and_chat(caption_content: str, chat_entries: list[tuple[str, str, str]]) -> str:
    """
    Merge captions and chat chronologically and collapse back-to-back lines
    from the *same speaker* and *same source* into a single line.
    The first timestamp in a run is retained.

    Supports both caption formats:
      A) Header + following lines:
         [Speaker] HH:MM:SS
         text...
         text...
      B) Single-line entries:
         [Speaker] HH:MM:SS: text...

    Chat entries are tagged as source='chat' and *never* merged with captions.
    """
    events = []

    # Regex A: header-only line (start of a multi-line caption block)
    header_only_re = re.compile(r"^\[(.+?)\]\s+(\d{2}:\d{2}:\d{2})\s*$")

    # Regex B: single-line caption (speaker, timestamp, colon, then inline text)
    single_line_re = re.compile(r"^\[(.+?)\]\s+(\d{2}:\d{2}:\d{2}):\s*(.*\S)?\s*$")

    # --- Parse captions into events ---
    current_speaker = None
    current_timestamp = None
    current_text_parts = []  # accumulate lines for header-only blocks

    for raw_line in caption_content.splitlines():
        line = raw_line.rstrip()

        # Try single-line first (more specific)
        m_single = single_line_re.match(line)
        if m_single:
            # If we were accumulating a previous header-only block, flush it first
            if current_speaker is not None and current_text_parts:
                events.append({
                    "timestamp": current_timestamp,
                    "speaker": current_speaker,
                    "text": " ".join(t.strip() for t in current_text_parts if t.strip()),
                    "source": "caption",
                })
                current_speaker = None
                current_timestamp = None
                current_text_parts = []

            spk, ts, inline_text = m_single.groups()
            inline_text = (inline_text or "").strip()

            # Emit this single-line caption as its own event;
            # later we'll merge adjacent caption events from the same speaker.
            events.append({
                "timestamp": ts,
                "speaker": spk,
                "text": inline_text,
                "source": "caption",
            })
            continue

        # Then try header-only
        m_hdr = header_only_re.match(line)
        if m_hdr:
            # Flush any previous header-only block
            if current_speaker is not None and current_text_parts:
                events.append({
                    "timestamp": current_timestamp,
                    "speaker": current_speaker,
                    "text": " ".join(t.strip() for t in current_text_parts if t.strip()),
                    "source": "caption",
                })

            current_speaker, current_timestamp = m_hdr.groups()
            current_text_parts = []
            continue

        # Otherwise, it's a body line for an active header-only block
        if current_speaker is not None:
            if line.strip():  # ignore empty/whitespace-only lines
                current_text_parts.append(line)
        # If there's no active block and it's not single-line, we silently ignore
        # (this covers stray lines before the first header, if any)

    # Flush any trailing header-only block
    if current_speaker is not None and current_text_parts:
        events.append({
            "timestamp": current_timestamp,
            "speaker": current_speaker,
            "text": " ".join(t.strip() for t in current_text_parts if t.strip()),
            "source": "caption",
        })

    # --- Add chat entries (kept as separate source so they never merge with captions) ---
    for ts, speaker, msg in chat_entries:
        events.append({
            "timestamp": ts,
            "speaker": speaker,
            "text": f"[IN CHAT]: {msg}",
            "source": "chat",
        })

    # --- Sort chronologically ---
    def to_dt(ts: str):
        return datetime.strptime(ts, "%H:%M:%S")
    events.sort(key=lambda e: to_dt(e["timestamp"]))

    # --- Merge only adjacent events with same speaker AND same source ---
    merged_events = []
    for e in events:
        if not merged_events:
            merged_events.append(e)
            continue

        last = merged_events[-1]
        if e["speaker"] == last["speaker"] and e["source"] == last["source"]:
            # Same speaker & same source -> merge text, keep the first timestamp
            if e["text"]:
                # Add a space between pieces to avoid collisions
                last["text"] = (last["text"] + " " + e["text"]).strip()
        else:
            merged_events.append(e)

    # --- Render ---
    lines = [f'[{e["speaker"]}] {e["timestamp"]}: {e["text"].strip()}' for e in merged_events]
    return "\n".join(lines) + "\n"



# ---------------------- Summarization ---------------------- #

def summarize_transcript(content: str, client, duration: str, model: str, system_prompt: str) -> str:
    """
    Sends transcript to OpenAI for summarization.

    Args:
        content (str): Transcript content.
        client: OpenAI client.
        duration (str): Approximate meeting duration.
        model (str): Model name.
        system_prompt (str): The system prompt text to use.

    Returns:
        str: Summarized meeting notes.
    """
    preface = f"Approximate Duration: {duration}\n\n"
    content_with_duration = preface + content

    if model == "o3-mini" or model == "o4-mini":
        # o3-mini and o4-mini do not do temperature settings
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_with_duration}
            ]
        )
        # Extract and strip text from the first choice.
        summary: str = response.choices[0].message.content.strip()
        debug(f"Summary 1: {summary}")
        return summary
    else:
        # For other models, set the temperature to 0.2 so that the results don't
        # meander or make up stuff while still providing some flexibility. I
        # have not tested whether 0.1 would be better.
        response = client.chat.completions.create(
            model=model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_with_duration}
            ]
        )
        # Extract and strip content from the first choice message.
        summary: str = response.choices[0].message.content.strip()
        debug(f"Summary 2: {summary}")
        return summary


# ---------------------- Processing Functions ---------------------- #

def process_meeting_dir(dir_path: str, dir_name: str, args) -> Tuple[str, str, str]:
    """
    Processes a single meeting directory:
    - Reads captions and chat.
    - Merges into consolidated transcript.
    - Adds meeting metadata.
    - Saves transcript into OUTPUT_DIR/Transcripts.

    Args:
        dir_path (str): Path to meeting directory.
        dir_name (str): Name of the meeting directory.
        transcripts_dir (str): Directory to save consolidated transcripts.
        args: Parsed arguments.

    Returns:
        Tuple[str, str]: A tuple containing:
            - The path to the consolidated transcript file, or None if skipped.
            - The meeting year, or None if skipped.
            - The contents of the merged file or None
    """
    caption_file = os.path.join(dir_path, "meeting_saved_closed_caption.txt")
    if os.path.isfile(os.path.join(dir_path, "meeting_saved_chat.txt")):
        chat_file = os.path.join(dir_path, "meeting_saved_chat.txt")
    elif os.path.isfile(os.path.join(dir_path, "meeting_saved_new_chat.txt")):
        chat_file = os.path.join(dir_path, "meeting_saved_new_chat.txt")
    else:
        chat_file = False
        pass  # for auto indentation

    caption_exists = os.path.isfile(caption_file)
    chat_exists = os.path.isfile(chat_file) if chat_file else False
    if not caption_exists and not chat_exists:
        warn(f"No caption/chat in {dir_name}. Skipping.")
        return None, None, None

    info("╞══════════════════════════════════════════════════════════════════════════════╡")
    info(f"Summarizing: {dir_path}")
    # Extract meeting metadata from directory name if possible
    meeting_title = dir_name
    meeting_datetime = "Unknown"
    meta_match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2}) (.+)$", dir_name)
    if meta_match:
        meeting_datetime, meeting_title = meta_match.groups()
        meeting_year = meeting_datetime.split("-")[0]
    else:
        meeting_year = str(date.today().year)  # fallback to current year
        warn(f"Could not parse meeting date from directory '{dir_name}'. Using current year {meeting_year}.")
        pass  # for auto indentation

    caption_content = ""
    if caption_exists:
        info(f"🔍 Reading captions file: '{caption_file}'")
        with open(caption_file, "r", encoding="utf-8") as f:
            caption_content = f.read()
            line_no = len(caption_content.splitlines())
            debug(f"Read {line_no:,d} caption lines")
            pass  # for auto indentation
        pass  # for auto indentation

    chat_entries = []
    chat_parse_failed = False  # True when file had content but zero entries parsed
    if chat_exists:
        chat_lines, chat_entries = parse_chat_file(chat_file)
        matching_lines = len(chat_entries)
        debug(f"Read {chat_lines:,d} lines. Found {matching_lines:,d} matching chat entries.")
        if matching_lines == 0:
            chat_parse_failed = True
            warn(f"No matching chat lines found ({chat_lines:,d} lines in file). This is strange.")
            if not args.force:
                warn(f"Chat file will NOT be deleted (use --force to override): {chat_file}")
        else:
            debug(f"Lines: {chat_entries}")
            pass  # for auto indentation
    else:
        debug(f"No chat file found.")
        pass  # for auto indentation
    pass  # for auto indentation

    merged_body = merge_captions_and_chat(caption_content, chat_entries).strip()

    if not merged_body:
        warn(f"Empty merged transcript for {dir_name}. Skipping.")
        return None, None, merged_body

    # Prepend meeting metadata
    merged_file_contents = f"Meeting Title: {meeting_title}\nMeeting Start Datetime: {meeting_datetime}\n\n{merged_body}"

    transcript_filename = f"{dir_name} meeting_saved_closed_caption.txt"
    if not args.no_clean_names:
        transcript_filename = clean_filename(transcript_filename)
        pass  # for auto indentation

    merged_dir = os.path.join(args.output_dir, f"Merged-Transcripts-{meeting_year}")
    if not args.dry_run:
        os.makedirs(merged_dir, exist_ok=True)
        pass  # for auto indentation
    transcript_path = os.path.join(merged_dir, transcript_filename)

    if os.path.isfile(transcript_path) and not args.clobber:
        log_and_print("SKIPPING:", "yellow", f"{transcript_filename} exists. Use --clobber to overwrite.")
        return None, None, merged_body

    if os.path.isfile(transcript_path) and args.clobber:
        timestamp_str = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        backup_path = transcript_path + f".backup.{timestamp_str}"
        safe_action(f"Backing up existing {transcript_filename} to {backup_path}",
                    shutil.move, transcript_path, backup_path, dry_run=args.dry_run)

    safe_action(f"Saving consolidated transcript to {transcript_path}",
                action_func=None, dry_run=args.dry_run)

    debug(f"Writing {len(merged_file_contents.splitlines()):,d} lines to merged transcripts file.")
    if not args.dry_run:
        with safe_write(transcript_path) as out:
            out.write(merged_file_contents)
            pass  # for auto indentation
        pass  # for auto indentation

    # Cleanup originals and empty directory
    if args.keep_originals:
        if caption_exists:
            info(f"Keeping original caption {caption_file}")
        if chat_exists:
            info(f"Keeping original chat {chat_file}")
            pass  # for auto indentation
        pass  # for auto indentation
    else:
        if caption_exists:
            safe_trash(caption_file, os.path.join(args.output_dir, "to-delete"), dry_run=args.dry_run)
        if chat_exists:
            if chat_parse_failed and not args.force:
                warn(f"Keeping chat file with zero parsed entries (re-run with --force to move): {chat_file}")
            else:
                safe_trash(chat_file, os.path.join(args.output_dir, "to-delete"), dry_run=args.dry_run)
        if not os.listdir(dir_path):
            safe_trash(dir_path, os.path.join(args.output_dir, "to-delete"), dry_run=args.dry_run)
            pass  # for auto indentation
        pass  # for auto indentation

    return transcript_path, meeting_year, merged_file_contents


def make_summary_filename(base_filename: str, model: str) -> str:
    """
    Derive a summary output filename from a merged transcript filename and model name.

    Strips known Zoom caption suffixes and appends '<model>.summary.txt'.
    Falls back to replacing '.txt' for non-standard names.

    Args:
        base_filename: Basename of the merged transcript file.
        model: Model name string (e.g. 'o4-mini', 'gpt-4o').

    Returns:
        Summary filename string.
    """
    for suffix in (
        "-meeting_saved_closed_caption.txt",
        " meeting_saved_closed_caption.txt",
        "meeting_saved_closed_caption.txt",
    ):
        if suffix in base_filename:
            return base_filename.replace(suffix, f".{model}.summary.txt")
    # Fallback for non-standard or already-cleaned filenames.
    return base_filename.replace(".txt", f".{model}.summary.txt")


def summarize_one_transcript(
    transcript_path: str,
    merged_file_contents: str,
    summaries_base_dir: str,
    selected_models: list,
    client,
    args,
    system_prompt: str,
) -> bool:
    """
    Run all selected models against a single merged transcript and write summaries.

    Args:
        transcript_path: Path to the merged transcript file (used for display/logging).
        merged_file_contents: Full text of the merged transcript.
        summaries_base_dir: Directory where summary files will be written.
        selected_models: List of model name strings.
        client: OpenAI client instance.
        args: Parsed arguments.
        system_prompt: The system prompt text to send to the model.

    Returns:
        True if at least one summary was successfully written, False otherwise.
    """
    duration = compute_duration(merged_file_contents)
    base_filename = os.path.basename(transcript_path)
    dir_label = os.path.basename(os.path.dirname(transcript_path)) or base_filename
    any_written = False

    for model in selected_models:
        info(f"Using: {model}")

        summary_filename = make_summary_filename(base_filename, model)
        summary_path = os.path.join(summaries_base_dir, summary_filename)

        if os.path.isfile(summary_path) and not args.clobber:
            log_and_print("SKIPPING:", "yellow",
                          f"{summary_filename} already exists. Use --clobber to overwrite.")
            continue

        try:
            summary = summarize_transcript(merged_file_contents, client, duration, model, system_prompt)
        except openai.APIError as exc:
            warn(f"OpenAI API error for model '{model}' on '{dir_label}': {exc}. Skipping this model.")
            continue
        except Exception as exc:
            warn(f"Unexpected error summarizing '{dir_label}' with model '{model}': {exc}. Skipping this model.")
            continue

        if not args.dry_run:
            os.makedirs(summaries_base_dir, exist_ok=True)

        safe_action(f"Saving summary to {summary_path}", action_func=None, dry_run=args.dry_run)
        if not args.dry_run:
            with safe_write(summary_path) as out:
                out.write(f"Meeting Notes Summary Generated by LLM from {base_filename}\n")
                out.write(f"Prompt: {getattr(args, 'prompt_label', args.prompt)}\n")
                out.write("Note: The AI has attempted to correct transcription errors.\n\n")
                out.write(summary)
                out.write(f"\n\nEnd of Meeting Notes from {base_filename}\n\n\n")
            any_written = True
        pass  # for auto indentation

    return any_written


def process_and_summarize_all(args, selected_models):
    """
    Default mode: process raw meeting directories, consolidate transcripts,
    and run OpenAI summarization for each.

    Args:
        args: Parsed arguments.
        selected_models (list): List of model names.
    """
    if not args.dry_run:
        os.makedirs(args.output_dir, exist_ok=True)
        pass  # for auto indentation

    api_key = read_openai_api_key()
    client = openai.OpenAI(api_key=api_key)
    system_prompt = resolve_prompt(args)
    info(f"Using prompt: {args.prompt_label}")

    processed_count = 0

    for dir_name in sorted(os.listdir(args.input_dir)):
        if args.max is not None and processed_count >= args.max:
            info(f"Reached max limit {args.max}. Stopping.")
            break

        dir_path = os.path.join(args.input_dir, dir_name)
        if not os.path.isdir(dir_path):
            continue

        transcript_path, meeting_year, merged_file_contents = process_meeting_dir(dir_path, dir_name, args)
        if transcript_path is None:
            warn(f"Skipping: '{os.path.basename(dir_path)}'")
            continue

        summaries_base_dir = os.path.join(args.output_dir, f"Summaries-{meeting_year}")

        any_summary_written = summarize_one_transcript(
            transcript_path, merged_file_contents, summaries_base_dir, selected_models, client, args,
            system_prompt=system_prompt,
        )

        if not args.keep_consolidated_transcript:
            if any_summary_written or args.dry_run:
                safe_trash(transcript_path,
                           os.path.join(args.output_dir, "to-delete"),
                           dry_run=args.dry_run)
            else:
                warn(f"Keeping consolidated transcript because no summaries were produced: {transcript_path}")
            pass  # for auto indentation
        processed_count += 1
        pass  # for auto indentation
    pass  # for auto indentation


def year_from_path(path: str) -> str:
    """
    Derive a 4-digit year string from a merged transcript path.

    Checks the parent directory name first (Merged-Transcripts-YYYY),
    then falls back to a leading YYYY- prefix in the filename itself.
    Returns the current year as a last resort.

    Args:
        path: Absolute or relative path to a merged transcript file.

    Returns:
        Four-character year string.
    """
    parent = os.path.basename(os.path.dirname(os.path.abspath(path)))
    m = re.match(r"Merged-Transcripts-(\d{4})$", parent)
    if m:
        return m.group(1)
    m = re.match(r"(\d{4})-", os.path.basename(path))
    if m:
        return m.group(1)
    return str(date.today().year)


def process_and_summarize_from_merged(args, selected_models):
    """
    Re-run mode (--from-merged): summarize already-merged transcript files.

    Reads previously saved Merged-Transcripts-YYYY/*.txt files from --input-dir
    and sends them to the selected model(s). Summaries are written to
    --output-dir/Summaries-YYYY/ using the same naming convention as default mode.

    The merged transcript files are NEVER deleted in this mode.

    Args:
        args: Parsed arguments (args.from_merged must be True).
        selected_models (list): List of model names.
    """
    api_key = read_openai_api_key()
    client = openai.OpenAI(api_key=api_key)
    system_prompt = resolve_prompt(args)
    info(f"Using prompt: {args.prompt_label}")

    # Discover Merged-Transcripts-YYYY directories, optionally filtered by --year.
    all_entries = sorted(os.listdir(args.input_dir))
    merged_dirs = []
    for entry in all_entries:
        if not entry.startswith("Merged-Transcripts-"):
            continue
        year = entry[len("Merged-Transcripts-"):]
        if args.year and year != args.year:
            continue
        full_path = os.path.join(args.input_dir, entry)
        if os.path.isdir(full_path):
            merged_dirs.append((year, full_path))

    if not merged_dirs:
        if args.year:
            warn(f"No Merged-Transcripts-{args.year} directory found under {args.input_dir}.")
        else:
            warn(f"No Merged-Transcripts-YYYY directories found under {args.input_dir}.")
        return

    processed_count = 0

    for year, merged_dir in merged_dirs:
        info(f"Scanning merged transcripts for year {year}: {merged_dir}")
        summaries_base_dir = os.path.join(args.output_dir, f"Summaries-{year}")

        transcript_files = sorted(
            p for p in os.listdir(merged_dir)
            if p.endswith(".txt")
            and not p.endswith(".summary.txt")
            and (not args.match or args.match.lower() in p.lower())
        )

        if not transcript_files:
            warn(f"No merged transcript files found in {merged_dir}.")
            continue

        for filename in transcript_files:
            if args.max is not None and processed_count >= args.max:
                info(f"Reached max limit {args.max}. Stopping.")
                return

            transcript_path = os.path.join(merged_dir, filename)
            info("╞══════════════════════════════════════════════════════════════════════════════╡")
            info(f"Re-summarizing: {transcript_path}")

            try:
                with open(transcript_path, "r", encoding="utf-8") as f:
                    merged_file_contents = f.read()
            except OSError as exc:
                warn(f"Could not read '{transcript_path}': {exc}. Skipping.")
                continue

            if not merged_file_contents.strip():
                warn(f"Empty transcript file: {transcript_path}. Skipping.")
                continue

            summarize_one_transcript(
                transcript_path, merged_file_contents, summaries_base_dir, selected_models, client, args,
                system_prompt=system_prompt,
            )
            processed_count += 1
            pass  # for auto indentation
        pass  # for auto indentation
    pass  # for auto indentation


def process_specific_files(args, selected_models):
    """
    --files mode: summarize one or more specific merged transcript files.

    Each file path is taken directly from args.files. The year for the
    output Summaries-YYYY/ directory is derived from the file's parent
    directory name (Merged-Transcripts-YYYY) or from a YYYY- prefix in
    the filename itself.

    Merged transcript files are never deleted in this mode.

    Args:
        args: Parsed arguments (args.files must be non-empty).
        selected_models (list): List of model names.
    """
    api_key = read_openai_api_key()
    client = openai.OpenAI(api_key=api_key)
    system_prompt = resolve_prompt(args)
    info(f"Using prompt: {args.prompt_label}")

    processed_count = 0
    for transcript_path in args.files:
        if args.max is not None and processed_count >= args.max:
            info(f"Reached max limit {args.max}. Stopping.")
            break

        if not os.path.isfile(transcript_path):
            warn(f"File not found: '{transcript_path}'. Skipping.")
            continue
        if not transcript_path.endswith(".txt"):
            warn(f"Unexpected extension (expected .txt): '{transcript_path}'. Skipping.")
            continue

        year = year_from_path(transcript_path)
        summaries_base_dir = os.path.join(args.output_dir, f"Summaries-{year}")

        info("╞══════════════════════════════════════════════════════════════════════════════╡")
        info(f"Re-summarizing: {transcript_path}")

        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                merged_file_contents = f.read()
        except OSError as exc:
            warn(f"Could not read '{transcript_path}': {exc}. Skipping.")
            continue

        if not merged_file_contents.strip():
            warn(f"Empty transcript file: {transcript_path}. Skipping.")
            continue

        summarize_one_transcript(
            transcript_path, merged_file_contents, summaries_base_dir, selected_models, client, args,
            system_prompt=system_prompt,
        )
        processed_count += 1
        pass  # for auto indentation
    pass  # for auto indentation


def report_status(args):
    """
    --status mode: scan input and output directories and report the processing
    state of every meeting directory without making any API calls.

    For each meeting directory found in --input-dir the report shows:
      - Whether raw caption / chat files are present
      - Whether a merged transcript has been generated in the output dir
      - Which summary files (by model) exist in the output dir

    Args:
        args: Parsed arguments. Requires args.input_dir and args.output_dir.
    """
    SEP = "─" * 78

    def check(val):
        return colored("✓", "green", attrs=["bold"]) if val else colored("✗", "red")

    print()
    print(colored("Meeting Status Report", "cyan", attrs=["bold"]))
    print(SEP)
    print(f"Input dir:  {args.input_dir}")
    print(f"Output dir: {args.output_dir}")
    print()

    dir_names = sorted(
        d for d in os.listdir(args.input_dir)
        if os.path.isdir(os.path.join(args.input_dir, d))
    )

    if not dir_names:
        print("No meeting directories found.")
        return

    counts = {"total": 0, "raw": 0, "merged": 0, "summarized": 0, "fully_done": 0}

    for dir_name in dir_names:
        counts["total"] += 1
        dir_path = os.path.join(args.input_dir, dir_name)

        # --- Raw files ---
        caption_file = os.path.join(dir_path, "meeting_saved_closed_caption.txt")
        caption_exists = os.path.isfile(caption_file)

        chat_file_path = None
        for chat_name in ("meeting_saved_chat.txt", "meeting_saved_new_chat.txt"):
            candidate = os.path.join(dir_path, chat_name)
            if os.path.isfile(candidate):
                chat_file_path = candidate
                break
        chat_exists = chat_file_path is not None

        if caption_exists or chat_exists:
            counts["raw"] += 1

        # --- Merged transcript ---
        meta_match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2}) (.+)$", dir_name)
        meeting_year = meta_match.group(1).split("-")[0] if meta_match else str(date.today().year)

        transcript_filename = f"{dir_name} meeting_saved_closed_caption.txt"
        if not args.no_clean_names:
            transcript_filename = clean_filename(transcript_filename)

        merged_dir = os.path.join(args.output_dir, f"Merged-Transcripts-{meeting_year}")
        transcript_path = os.path.join(merged_dir, transcript_filename)
        merged_exists = os.path.isfile(transcript_path)
        if merged_exists:
            counts["merged"] += 1

        # --- Summary files ---
        summaries_dir = os.path.join(args.output_dir, f"Summaries-{meeting_year}")
        summary_files = []
        if os.path.isdir(summaries_dir):
            stem = os.path.splitext(transcript_filename)[0]
            summary_files = sorted(
                f for f in os.listdir(summaries_dir)
                if f.startswith(stem) and f.endswith(".summary.txt")
            )
        if summary_files:
            counts["summarized"] += 1
        if merged_exists and summary_files:
            counts["fully_done"] += 1

        # --- Print row ---
        print(f"  {colored(dir_name, 'white', attrs=['bold'])}")
        print(f"    Caption:   {check(caption_exists)}")
        print(f"    Chat:      {check(chat_exists)}")
        print(f"    Merged:    {check(merged_exists)}", end="")
        if merged_exists:
            print(f"  ({transcript_filename})")
        else:
            print()
        if summary_files:
            # Extract model name: strip stem + leading dot, then .summary.txt
            for sf in summary_files:
                model_name = sf[len(stem):].lstrip(".").replace(".summary.txt", "")
                print(f"    Summary:   {check(True)}  {model_name}  ({sf})")
        else:
            print(f"    Summary:   {check(False)}  (none)")
        print()

    # --- Totals ---
    print(SEP)
    not_started = counts["total"] - counts["raw"] - (counts["merged"] - counts["raw"] if counts["merged"] > counts["raw"] else 0)
    print(f"Total meetings:     {counts['total']:>4}")
    print(f"  Have raw files:   {counts['raw']:>4}  (caption or chat present in input dir)")
    print(f"  Have merged:      {counts['merged']:>4}  (consolidated transcript generated)")
    print(f"  Have summaries:   {counts['summarized']:>4}  (at least one summary file found)")
    print()




def parse_args():
    global args
    parser = argparse.ArgumentParser(
        description="Consolidate and summarize Zoom meeting transcripts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Default: merge raw meeting dirs and summarize\n"
            "  %(prog)s --input-dir /zoom/meetings --output-dir /zoom/output --o4-mini\n"
            "\n"
            "  # Re-summarize all previously merged transcripts with a new model\n"
            "  %(prog)s --from-merged --input-dir /zoom/output --output-dir /zoom/output --4o\n"
            "\n"
            "  # Re-summarize only 2025 transcripts\n"
            "  %(prog)s --from-merged --year 2025 --input-dir /zoom/output --output-dir /zoom/output --o4-mini\n"
            "\n"
            "  # Re-summarize transcripts matching a name fragment\n"
            "  %(prog)s --from-merged --match 'PMO Team' --input-dir /zoom/output --output-dir /zoom/output --4o\n"
            "\n"
            "  # Re-summarize one or a few specific files\n"
            "  %(prog)s --files /zoom/output/Merged-Transcripts-2026/2026-04-21-PMO-Team.txt --output-dir /zoom/output --4o\n"
            "\n"
            "  # Use a custom prompt (name from prompts/ dir, or a file path)\n"
            "  %(prog)s --prompt interview --files /zoom/output/Merged-Transcripts-2026/2026-04-21-Search.txt --output-dir /zoom/output --o4-mini\n"
            "\n"
            "  # Specify a prompt as a literal string\n"
            "  %(prog)s --prompt-string 'Summarize this meeting in 3 bullet points.' --files /zoom/output/... --output-dir /zoom/output\n"
            "\n"
            "  # Add extra instructions to the default prompt\n"
            "  %(prog)s --add 'Focus especially on budget and staffing decisions.' --from-merged --input-dir /zoom/output --output-dir /zoom/output\n"
            "\n"
            "  # List available prompts\n"
            "  %(prog)s --list-prompts\n"
        ),
    )
    parser.add_argument("--add", type=str, default=None, metavar="TEXT",
                        help="Append TEXT to the prompt before sending to the model. "
                             "Works with --prompt (file-based), --prompt-string, or the default "
                             "prompt. Useful for adding one-off context or focus instructions "
                             "without creating a new prompt file. "
                             "Example: --add 'Focus especially on budget decisions.'")
    parser.add_argument("--4o", dest="_4o", action="store_true", help="Run with gpt-4o model.")
    parser.add_argument("--4o-mini", dest="_4o_mini", action="store_true", help="Use gpt-4o-mini model.")
    parser.add_argument("--clobber", "-c", action="store_true", help="Overwrite existing summary files (will still make backups).")
    parser.add_argument("--debug", "-d", action="store_true", help="Show debugging info.")
    parser.add_argument("--dry-run", "-D", action="store_true", help="Show actions without making changes.")
    parser.add_argument("--files", nargs="+", metavar="FILE", default=None,
                        help="Re-summarize specific merged transcript file(s). "
                             "Provide one or more paths. --input-dir is not required in this mode; "
                             "the output year is derived from each file's parent directory name "
                             "(Merged-Transcripts-YYYY) or its YYYY- filename prefix.")
    parser.add_argument("--force", "-f", action="store_true",
                        help="Allow deletion of chat files that produced zero parsed entries. "
                             "Without this flag, such files are kept to prevent accidental data loss.")
    parser.add_argument("--from-merged", action="store_true",
                        help="Re-run mode: read already-merged transcripts from "
                             "INPUT_DIR/Merged-Transcripts-YYYY/ and re-summarize them. "
                             "Merged transcript files are never deleted in this mode. "
                             "Use --year to limit to a single year; --match to filter by name.")
    parser.add_argument("--input-dir", "-i", type=str, default=None,
                        help="In default mode: directory containing raw meeting directories (required). "
                             "In --from-merged mode: directory containing Merged-Transcripts-YYYY/ subdirs "
                             "(typically the same as --output-dir from a prior run). "
                             "Not required when --files is used.")
    parser.add_argument("--keep-consolidated-transcript", "-k", action="store_true",
                        help="Keep consolidated transcripts in OUTPUT_DIR/Merged-Transcripts-YYYY/. "
                             "Ignored in --from-merged and --files modes (merged files are always kept).")
    parser.add_argument("--keep-originals", "-K", action="store_true",
                        help="Do not move original caption/chat files or meeting dirs to to-delete/.")
    parser.add_argument("--list-prompts", action="store_true",
                        help="List the available prompt names from the prompts/ directory and exit.")
    parser.add_argument("--match", type=str, default=None, metavar="PATTERN",
                        help="In --from-merged mode: only process transcript files whose filename "
                             "contains PATTERN (case-insensitive substring match). "
                             "Example: --match 'PMO Team'")
    parser.add_argument("--max", "-m", type=int, default=None,
                        help="Maximum number of transcript files to process.")
    parser.add_argument("--no-clean-names", "-n", action="store_true", help="Do not clean filenames.")
    parser.add_argument("--o3-mini", dest="o3_mini", action="store_true", help="Use o3-mini model.")
    parser.add_argument("--o4-mini", dest="o4_mini", action="store_true", help="Use o4-mini model (default if no model specified).")
    parser.add_argument("--output-dir", "-o", type=str, required=True,
                        help="Directory to save summaries (and merged transcripts in default mode).")
    parser.add_argument("--prompt", "-p", type=str, default="default",
                        help="Prompt to use for summarization. Accepts a name from the prompts/ "
                             "directory (e.g. 'default', 'v1', 'interview') or an absolute/relative "
                             "path to a .txt file. Use --list-prompts to see available names. "
                             "Ignored if --prompt-string is given. Default: 'default'.")
    parser.add_argument("--prompt-string", type=str, default=None, metavar="TEXT",
                        help="Use TEXT directly as the system prompt instead of loading from a file. "
                             "Overrides --prompt. Combine with --add to append additional instructions.")
    parser.add_argument("--status", action="store_true",
                        help="Report the processing status of every meeting in --input-dir: "
                             "which meetings have raw caption/chat files, merged transcripts, "
                             "and summary files. No API calls are made.")
    parser.add_argument("--year", "-y", type=str, default=None, metavar="YYYY",
                        help="In --from-merged mode: limit processing to Merged-Transcripts-YYYY for this year.")
    args = parser.parse_args()

    # Handle --list-prompts immediately (before other validation).
    if args.list_prompts:
        available = list_available_prompts()
        if available:
            print(f"Available prompts in {PROMPTS_DIR}/:")
            for name in available:
                print(f"  {name}")
        else:
            print(f"No prompts found in {PROMPTS_DIR}/")
        raise SystemExit(0)

    # Validate: --prompt-string and --prompt are mutually exclusive.
    if args.prompt_string and args.prompt != "default":
        parser.error("--prompt-string and --prompt are mutually exclusive. "
                     "Use one or the other (or use --add to append to a file-based prompt).")

    # Validate: --input-dir is required unless --files or --status is given.
    if not args.files and not args.status and args.input_dir is None:
        parser.error("--input-dir is required unless --files is specified.")

    selected_models = []
    if args._4o:
        selected_models.append("gpt-4o")
    if args._4o_mini:
        selected_models.append("gpt-4o-mini")
    if args.o3_mini:
        selected_models.append("o3-mini")
    if args.o4_mini:
        selected_models.append("o4-mini")

    if not selected_models:
        selected_models = ["o4-mini"]

    return args, selected_models


def setup_logging(output_dir):
    """
    Sets up logging to console and to a file in the output directory.
    """
    os.makedirs(output_dir, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    log_path = os.path.join(output_dir, "summarize_meeting_transcripts.log")
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s: %(message)s"))
    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)
    # Suppress duplicate INFO messages to console
    root_logger.handlers[0].setLevel(logging.WARNING)
    info(f"Logging to {log_path}")
    pass  # for auto indentation


def main():
    """
    Main entry point. Parses arguments, sets up logging,
    and dispatches to the appropriate processing mode.
    """
    args, selected_models = parse_args()
    if args.status:
        report_status(args)
        return
    setup_logging(args.output_dir)
    if args.files:
        process_specific_files(args, selected_models)
    elif args.from_merged:
        process_and_summarize_from_merged(args, selected_models)
    else:
        process_and_summarize_all(args, selected_models)
    pass  # for auto indentation


if __name__ == "__main__":
    main()
