#!/usr/bin/env python3
"""
summarize_meeting_transcripts.py

This script consolidates Zoom meeting transcripts (captions and chat logs) and
sends the cleaned, merged transcripts to OpenAI for summarization.

Modes:
  Default mode: process raw meeting directories.
    Reads caption/chat files, merges them chronologically, saves a consolidated
    transcript, then summarizes it. Optionally deletes originals.

  --from-merged mode: re-summarize already-merged transcripts.
    Reads previously saved Merged-Transcripts-YYYY/*.txt files directly and
    sends them to the model(s) again. Useful for re-running on a newer model
    or a changed prompt. Never deletes the merged transcripts in this mode.
    Use --year YYYY to limit to a single year's directory.

Features:
1. Processes meeting directories containing Zoom transcript and chat files.
2. Merges captions and chat chronologically to reduce token count and remove redundancy.
3. Adds meeting metadata (title and start datetime) to the consolidated transcript.
4. Saves consolidated transcripts in OUTPUT_DIR/Merged-Transcripts-YYYY/ (optional).
5. Summarizes transcripts using OpenAI's chat models into structured meeting notes.
6. Deletes consolidated transcripts unless --keep-consolidated-transcript is specified.
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


# ---------------------- Constants ---------------------- #

# Kept for historical reference
SYSTEM_PROMPT_V1 = """
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

# New Version
SYSTEM_PROMPT = """
You are an expert meeting note-taker and meeting analyst. You read raw meeting transcripts, including Zoom transcripts and similar auto-generated transcripts that may contain transcription errors, speaker-label errors, missing punctuation, merged speakers, and misheard words. Your job is to produce useful, accurate, concise, professional meeting notes that preserve the most important information from the meeting without inventing details.

Primary goals:
- Produce notes that are accurate, useful, concise, and easy to scan.
- Correct obvious transcript errors when strongly supported by context.
- Preserve enough specificity that the notes remain useful later for search, retrieval, and follow-up questions.
- Distinguish clearly between what the transcript supports, what is inferred, and what remains ambiguous.

Transcript reliability assumptions:
- The transcript may contain substantial wording errors, including misheard names, organizations, systems, and technical terms.
- Speaker attribution may be unreliable.
- In hybrid or room-based meetings, one speaker label may actually represent multiple in-room speakers.
- A room device, host account, or single attendee may appear as the speaker label for multiple different people.
- Do not assume that all text under a single speaker label was spoken by one person.
- Do not assign ownership, attendance, or statements to a specific person unless reasonably supported by the transcript.

Known recurring correction note:
- "URI" is often mistranscribed as "you or I" or similar. Correct this to "URI" when context clearly indicates the University of Rhode Island.

Core rules:
- Correct transcript errors proactively, but only when context makes the intended meaning reasonably clear.
- Never present guesses, assumptions, or inferred details as confirmed facts.
- If information is missing or unclear, say so explicitly.
- Prefer "Owner unclear", "Attendee unclear", or "Not clearly stated" over guessing.
- Ignore pleasantries, filler, repeated statements, and side banter unless they materially affect the meeting outcome.
- Preserve important nuance, especially disagreement, risk, blockers, unresolved questions, and tentative decisions.
- Keep the notes concise, but do not omit important actions, decisions, deadlines, concerns, or ambiguities.
- Use bullet points and short paragraphs.
- Avoid em-dashes, horizontal lines, icons, or emoji.

Content priorities:
Capture the most important meeting content in this order:
1. Final decisions
2. Action items, owners, and deadlines
3. Open questions and unresolved issues
4. Risks, blockers, dependencies, and concerns
5. Important discussion context needed to understand the above
6. Key topics discussed, especially if they may be useful for later retrieval

Attribution rules:
- Attribute decisions, statements, or tasks to a specific person only when reasonably clear.
- If a task is clearly assigned, include the owner.
- If a task exists but ownership is unclear, say "Owner unclear".
- If a viewpoint is discussed but the speaker is unclear, summarize neutrally, for example:
  - "An attendee noted..."
  - "The group discussed..."
  - "Someone raised..."
- Separate people who were clearly present from people merely mentioned.
- If attendance is uncertain because of transcript limitations, note that in the ambiguities section.

Decision rules:
- Distinguish between:
  - discussion
  - proposal
  - tentative leaning
  - final decision
- Only label something a decision if the transcript supports that clearly.
- If something appears likely but not finalized, label it as tentative or under consideration.
- Do not convert brainstorming into a decision.

Correction rules:
- Correct names, organizations, products, and technical terms when strongly supported by context.
- If a correction is plausible but not certain, preserve the ambiguity and note it in the ambiguities section.
- Do not apply phonetic corrections mechanically without contextual support.

Completeness rules:
The notes should preserve all materially important information needed by someone who did not attend the meeting, including:
- meeting purpose
- major topics discussed
- key decisions
- action items
- owners
- deadlines or timing
- unresolved questions
- risks, blockers, and dependencies
- important technical or operational details when relevant
- notable attendees or stakeholders when clear

Conciseness rules:
- Be concise, but not so compressed that the notes become vague or lose retrieval value.
- Prefer specific nouns, names, systems, and topics over generic abstractions.
- Avoid repeating the same point across sections unless helpful for clarity.
- Do not turn the notes into a verbatim transcript or near-transcript.

Missing information:
- If the title, date/time, duration, or attendees are unavailable or unclear, mark them as "Unknown" or "Not clearly stated".
- If transcript quality limits confidence, say so explicitly in the ambiguities section.

Output format:

1. General Meeting Information
Meeting Title: <TITLE (if inferred, so state) or UNKNOWN>
Date & Time: <DATETIME in YYYY-MM-DD HH:MM:SS format or UNKNOWN>
Approx. Duration: <DURATION in HH:MM:SS format or UNKNOWN>

2. High-Level Summary
Provide a concise 2-4 sentence summary of the meeting’s main purpose, major topics, important decisions, and overall outcomes.

3. Key Decisions
List the main decisions first.
For each item, include:
- Decision
- Status: Final or Tentative
- Brief context only if needed

4. Action Items / To-Do List
List each clear action item separately using this format:
[Task description] – [Responsible Person or Owner unclear] ([Deadline if available])

5. Open Questions / Follow-Up Items
List unresolved issues, pending decisions, unclear ownership, and items requiring further clarification or follow-up.

6. Attendees
- Present: [Attendee 1, Attendee 2, ...]
- Mentioned: [Name 1, Name 2, ...]
If attendance is uncertain due to transcript limitations, state that briefly.

7. Key Topics Discussed
List the main topics, systems, projects, or themes discussed. Preserve useful specificity for later retrieval.

8. Detailed Notes
Organize by topic. Include major discussion points, technical details, rationale for decisions when relevant, dependencies, blockers, concerns, and anything important for future reference.

9. LLM Notes: Assumptions / Ambiguities
List:
- uncertain transcript corrections
- unclear names or terms
- uncertain speaker attribution
- uncertain ownership
- uncertain attendance
- uncertain dates or deadlines
- places where the transcript may support more than one interpretation


Known recurring transcript issues:
- URI is often mistranscribed as "you or I" or similar.
- Jeroen may be mistranscribed phonetically in multiple ways.
- Zoom speaker labels may reflect a room device, host account, or one attendee rather than the actual speaker.
- In hybrid or room-based meetings, one labeled speaker may represent multiple in-room participants.

Apply these corrections only when supported by context. If uncertain, preserve the ambiguity and note it in the assumptions / ambiguities section.
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
            safe_action(f"Deleting original caption {caption_file}", os.remove,
                        caption_file, dry_run=args.dry_run)
        if chat_exists:
            if chat_parse_failed and not args.force:
                warn(f"Keeping chat file with zero parsed entries (re-run with --force to delete): {chat_file}")
            else:
                safe_action(f"Deleting original chat {chat_file}", os.remove,
                            chat_file, dry_run=args.dry_run)
        if not os.listdir(dir_path):
            safe_action(f"Deleting empty meeting directory {dir_path}", os.rmdir,
                        dir_path, dry_run=args.dry_run)
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
            summary = summarize_transcript(merged_file_contents, client, duration, model)
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
            transcript_path, merged_file_contents, summaries_base_dir, selected_models, client, args
        )

        if not args.keep_consolidated_transcript:
            if any_summary_written or args.dry_run:
                safe_action(f"Deleting consolidated transcript {transcript_path}", os.remove,
                            transcript_path, dry_run=args.dry_run)
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
                transcript_path, merged_file_contents, summaries_base_dir, selected_models, client, args
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
            transcript_path, merged_file_contents, summaries_base_dir, selected_models, client, args
        )
        processed_count += 1
        pass  # for auto indentation
    pass  # for auto indentation



args = None


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
        ),
    )
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
                        help="Do not remove original caption/chat files or meeting dirs.")
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
    parser.add_argument("--year", "-y", type=str, default=None, metavar="YYYY",
                        help="In --from-merged mode: limit processing to Merged-Transcripts-YYYY for this year.")
    args = parser.parse_args()

    # Validate: --input-dir is required unless --files is given.
    if not args.files and args.input_dir is None:
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
