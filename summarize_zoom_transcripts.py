#!/usr/bin/env python3
"""
summarize_meeting_transcripts.py

This script consolidates Zoom meeting transcripts (captions and chat logs) and
sends the cleaned, merged transcripts to OpenAI for summarization.

Features:
1. Processes meeting directories containing Zoom transcript and chat files.
2. Merges captions and chat chronologically to reduce token count and remove redundancy.
3. Adds meeting metadata (title and start datetime) to the consolidated transcript.
4. Saves consolidated transcripts in OUTPUT_DIR/Transcripts (optional filename cleaning).
5. Summarizes transcripts using OpenAI's chat models into structured meeting notes.
6. Deletes consolidated transcripts unless --keep is specified.
7. Cleans up empty meeting directories after processing.
8. Offers dry-run mode, max file count, and clobber protection.

Note: Sets temperature to 0.2 for non reasoning models for improved precision and accuracy.
"""

import os
import re
import shutil
import argparse
import logging
import unicodedata
from typing import IO
from contextlib import contextmanager
from datetime import datetime, timedelta, date
from termcolor import colored
import openai
from typing import Tuple


# ---------------------- Constants ---------------------- #

SYSTEM_PROMPT = """
You are an expert note-taker who reads meeting transcripts (such as those from Zoom). You recognize that transcripts may contain substantial errors and are responsible for correcting these based on contextual clues within the transcript itself. You produce professional, concise, actionable, and structured meeting notes, capturing all essential details clearly. Note: URI is often mistranscribed as "you or I" or something similar.
Guidelines:
• Correct transcript errors proactively, prioritizing context, clarity, and coherence.
• Clearly state when you make assumptions due to missing or unclear information.
• Organize notes using clear section headers for readability.
• Write in a professional, concise, and approachable tone.
• Avoid unnecessary jargon or clearly define it when relevant.
• Ignore pleasantries and ancillary banter.
• Use bullet points and short paragraphs to enhance clarity.
• Avoid em-dashes, horizontal lines, icons, or emoji.
Output Format (Sections):
1. General Meeting Information
Meeting Title: <TITLE>
Date & Time: <DATETIME>
Approx. Duration: <DURATION>
2. High-Level Summary
Provide a concise summary of the meeting’s main purpose, topics, decisions, and/or outcomes (approximately 2-4 sentences).
3. Action Items / To-Do List
Clearly list each task/action item mentioned:
• Task description.
• Responsible person if available.
• Deadline if available.
Format example:
[Action item/task description] – [Responsible Person] ([Deadline])
4. Attendees
• Present: [Attendee 1, Attendee 2, ...]
• Mentioned: [Name 1, Name 2, ...]
5. Detailed Notes
Include major decisions, plans, technical matters, concerns.
6. LLM Notes: Assumptions / Ambiguities
List any assumptions or ambiguous items here.
"""

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
    return log_and_print("DEBUG:   ", "cyan", msg, "info")


def info(msg: str) -> None:
    return log_and_print("INFO:    ", "green", msg, "info")


def warn(msg: str) -> None:
    return log_and_print("WARNING: ", "yellow", msg, "info")


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

    Args:
        chat_file_path (str): Path to chat file.

    Returns:
        list[tuple]: Parsed chat entries.
    """
    chat_entries = []
    # Only keep chat entries to everyone. Do not keep private chats.
    pattern = re.compile(r'^(\d{2}:\d{2}:\d{2}) From (.*?) to Everyone:\s*(.*)')
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
                if current_timestamp:
                    chat_entries.append((current_timestamp, current_speaker, current_message.strip()))
                current_timestamp, current_speaker, current_message = match.groups()
            else:
                if current_timestamp:
                    current_message += ' ' + line.strip()

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


# def merge_captions_and_chat(caption_content: str, chat_entries: list[tuple[str, str, str]]) -> str:
#     """
#     Merges captions and chat messages into a chronological transcript.

#     Args:
#         caption_content (str): Caption file content.
#         chat_entries (list): Parsed chat entries.

#     Returns:
#         str: Merged transcript.
#     """
#     events = []
#     time_format = "%H:%M:%S"

#     current_speaker = None
#     current_block = []
#     for line in caption_content.splitlines():
#         match = re.match(r"^\[(.+)\] (\d{2}:\d{2}:\d{2})$", line)
#         if match:
#             if current_block:
#                 timestamp, text = current_block[0]
#                 events.append((timestamp, f"[{current_speaker}] {timestamp}: {text.strip()}"))
#                 current_block = []
#             current_speaker, timestamp = match.groups()
#             current_block.append((timestamp, ""))
#         else:
#             if current_block:
#                 timestamp, text = current_block[-1]
#                 current_block[-1] = (timestamp, text + " " + line.strip())

#     if current_block:
#         timestamp, text = current_block[0]
#         events.append((timestamp, f"[{current_speaker}] {timestamp}: {text.strip()}"))

#     for timestamp, speaker, message in chat_entries:
#         events.append((timestamp, f"[{speaker}] {timestamp}: [IN CHAT]: {message}"))

#     events.sort(key=lambda x: datetime.strptime(x[0], time_format))
#     merged_content = [e[1] for e in events]

#     return "\n".join(merged_content) + "\n"


# ---------------------- Summarization ---------------------- #

def summarize_transcript(content: str, client, duration: str, model: str) -> str:
    """
    Sends transcript to OpenAI for summarization.

    Args:
        content (str): Transcript content.
        client: OpenAI client.
        duration (str): Approximate meeting duration.
        model (str): Model name.

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
                {"role": "system", "content": SYSTEM_PROMPT},
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
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content_with_duration}
            ]
        )
        # Extract and strip content from the first choice message.
        summary: str = response.choices[0].message.content.strip()
        debug(f"Summary 2: {summary}")
        return summary
    debug(f"Summary 3: {summary}")
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
    meta_match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}.\d{2}.\d{2}) (.+)$", dir_name)
    if meta_match:
        meeting_datetime, meeting_title = meta_match.groups()
        meeting_date_str = meta_match.group(1)
        meeting_year = meeting_date_str.split("-")[0]
    else:
        warn(f"Could not parse meeting date from directory '{dir_name}'. Using current year {meeting_year}.")
        meeting_year = str(date.today().year)  # fallback to current year
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
    if chat_exists:
        chat_lines, chat_entries = parse_chat_file(chat_file)
        matching_lines = len(chat_entries)
        debug(f"Read {chat_lines:,d} lines. Found {matching_lines:,d} matching chat entries.")
        if matching_lines == 0:
            warn(f"No matching chat lines found ({chat_lines:,d} lines in file). This is strange.")
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
            safe_action(f"Deleting original caption {caption_file}", os.remove,
                        caption_file, dry_run=args.dry_run)
        if chat_exists:
            safe_action(f"Deleting original chat {chat_file}", os.remove,
                        chat_file, dry_run=args.dry_run)
        if not os.listdir(dir_path):
            safe_action(f"Deleting empty meeting directory {dir_path}", os.rmdir,
                        dir_path, dry_run=args.dry_run)
            pass  # for auto indentation
        pass  # for auto indentation

    return transcript_path, meeting_year, merged_file_contents


def process_and_summarize_all(args, selected_models):
    """
    Processes all meeting directories, consolidates transcripts,
    and runs OpenAI summarization for each.

    Args:
        args: Parsed arguments.
        selected_models (list): List of model names.
    """
    if not args.dry_run:
        os.makedirs(args.output_dir, exist_ok=True)
        pass  # for auto indentation

    api_key = read_openai_api_key()
    client = openai.OpenAI(api_key=api_key)

    processed_count = 0

    for dir_name in os.listdir(args.input_dir):
        dir_path = os.path.join(args.input_dir, dir_name)
        if not os.path.isdir(dir_path):
            continue

        transcript_path, meeting_year, merged_file_contents = process_meeting_dir(dir_path, dir_name, args)
        if transcript_path is None:
            warn(f"Skipping: '{os.path.basename(dir_path)}'")
            continue

        summaries_base_dir = os.path.join(args.output_dir, f"Summaries-{meeting_year}")

        if args.max is not None and processed_count >= args.max:
            info(f"Reached max limit {args.max}. Stopping.")
            break

        duration = compute_duration(merged_file_contents)

        for model in selected_models:
            info(f"Using: {model}")

            # Extract just the base filename (not the full path).
            # This ensures our string replacements don't accidentally modify directory names.
            # Example:
            #   transcript_path = "/output/Merged-Transcripts-2025/2025-07-31 Meeting meeting_saved_closed_caption.txt"
            #   base_filename = "2025-07-31 Meeting meeting_saved_closed_caption.txt"
            base_filename = os.path.basename(transcript_path)

            # We now safely perform replacements on the filename only.
            # The goal is to produce a summary filename like:
            #   "2025-07-31 Meeting.o4-mini.summary.txt"
            #
            # We explicitly check for different variants of the caption suffix because
            # filenames might contain either a dash, a space, or no separator before
            # "meeting_saved_closed_caption.txt".
            #
            # Using base_filename avoids a subtle bug where `.replace()` could modify
            # parts of the directory path if that pattern appeared there by coincidence.
            if "-meeting_saved_closed_caption.txt" in base_filename:
                summary_filename = base_filename.replace(
                    "-meeting_saved_closed_caption.txt", f".{model}.summary.txt"
                )
            elif " meeting_saved_closed_caption.txt" in base_filename:
                summary_filename = base_filename.replace(
                    " meeting_saved_closed_caption.txt", f".{model}.summary.txt"
                )
            elif "meeting_saved_closed_caption.txt" in base_filename:
                summary_filename = base_filename.replace(
                    "meeting_saved_closed_caption.txt", f".{model}.summary.txt"
                )
            else:
                # Fallback: if none of the expected patterns are found, replace ".txt"
                # with ".{model}.summary.txt". This ensures we still generate a summary file.
                summary_filename = base_filename.replace(
                    ".txt", f".{model}.summary.txt"
                )

            # Join the safe filename with the summaries directory for the correct path.
            summary_path = os.path.join(summaries_base_dir, summary_filename)

            summary = summarize_transcript(merged_file_contents, client, duration, model)
            if not args.dry_run:
                os.makedirs(summaries_base_dir, exist_ok=True)
                pass  # for auto indentation
            summary_path = os.path.join(summaries_base_dir, os.path.basename(summary_filename))

            safe_action(f"Saving summary to {summary_path}", action_func=None, dry_run=args.dry_run)
            if not args.dry_run:
                with safe_write(summary_path) as out:
                    out.write(f"Meeting Notes Summary Generated by LLM from {os.path.basename(transcript_path)}\n")
                    out.write("Note: The AI has attempted to correct transcription errors.\n\n")
                    out.write(summary)
                    out.write(f"\n\nEnd of Meeting Notes from {os.path.basename(transcript_path)}\n\n\n")
                    pass  # for auto indentation
                pass  # for auto indentation
            pass  # for auto indentation

        if not args.keep_consolidated_transcript:
            safe_action(f"Deleting consolidated transcript {transcript_path}", os.remove,
                        transcript_path, dry_run=args.dry_run)
            pass  # for auto indentation
        processed_count += 1
        pass  # for auto indentation
    pass  # for auto indentation


# ---------------------- Main ---------------------- #

args = None


def parse_args():
    global args
    parser = argparse.ArgumentParser(description="Consolidate and summarize Zoom meeting transcripts.")
    parser.add_argument("--4o", dest="_4o", action="store_true", help="Run with gpt-4o model")
    parser.add_argument("--4o-mini", dest="_4o_mini", action="store_true", help="Use gpt-4o-mini model (default).")
    parser.add_argument("--clobber", "-c", action="store_true", help="Overwrite existing files (will still make backups).")
    parser.add_argument("--debug", "-d", action="store_true", help="Show debugging info.")
    parser.add_argument("--dry-run", "-D", action="store_true", help="Show actions without making changes.")
    parser.add_argument("--input-dir", "-i", type=str, required=True, help="Directory containing meeting directories.")
    parser.add_argument("--keep-consolidated-transcript", "-k", action="store_true", help="Keep consolidated transcripts in OUTPUT_DIR/Transcripts.")
    parser.add_argument("--keep-originals", "-K", action="store_true", help="Do not remove original caption/chat files or meeting dirs.")
    parser.add_argument("--max", "-m", type=int, default=None, help="Maximum number of meetings to process.")
    parser.add_argument("--no-clean-names", "-n", action="store_true", help="Do not clean filenames.")
    parser.add_argument("--o3-mini", dest="o3_mini", action="store_true", help="Use o3-mini model.")
    parser.add_argument("--o4-mini", dest="o4_mini", action="store_true", help="Use o4-mini model.")
    parser.add_argument("--output-dir", "-o", type=str, required=True, help="Directory to save outputs.")
    args = parser.parse_args()

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
    and processes/summarizes all meetings.
    """
    args, selected_models = parse_args()
    setup_logging(args.output_dir)
    process_and_summarize_all(args, selected_models)
    pass  # for auto indentation


if __name__ == "__main__":
    main()
