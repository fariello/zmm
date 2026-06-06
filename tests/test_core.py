"""Tests for zoom_meeting_manager core utilities."""

import sys
import os
import tempfile
from pathlib import Path
from datetime import date

# Ensure the project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import zoom_meeting_manager as zmm


# ----------------------------- clean_filename ----------------------------- #


def test_clean_filename_basic():
    assert zmm.clean_filename("hello world.txt") == "hello-world.txt"


def test_clean_filename_collapses_dashes():
    assert zmm.clean_filename("a---b   c.txt") == "a-b-c.txt"


def test_clean_filename_strips_edges():
    assert zmm.clean_filename("--test--.txt") == "test.txt"


def test_clean_filename_unicode():
    # NFKD normalization decomposes 'é' -> 'e' + combining accent, then the
    # accent (non-alphanumeric) is stripped, yielding "cafe-meeting.txt".
    assert zmm.clean_filename("café meeting.txt") == "cafe-meeting.txt"


def test_clean_filename_preserves_dots_in_name():
    assert zmm.clean_filename("v1.2.3 release notes.md") == "v1.2.3-release-notes.md"


# ----------------------------- Date Parsing ----------------------------- #


def test_parse_date_from_name_full():
    assert zmm.parse_date_from_name("2026-01-24 09.00.00 Board Retreat") == "2026-01-24"


def test_parse_date_from_name_dashed():
    assert zmm.parse_date_from_name("2026-01-24-09.00.00-Board-Retreat") == "2026-01-24"


def test_parse_date_from_name_none():
    assert zmm.parse_date_from_name("random-file-no-date") is None


def test_parse_date_range_year():
    start, end = zmm.parse_date_range("2026")
    assert start == date(2026, 1, 1)
    assert end == date(2026, 12, 31)


def test_parse_date_range_month():
    start, end = zmm.parse_date_range("2026-02")
    assert start == date(2026, 2, 1)
    assert end == date(2026, 2, 28)


def test_parse_date_range_explicit():
    start, end = zmm.parse_date_range("2026-01-10 to 2026-01-20")
    assert start == date(2026, 1, 10)
    assert end == date(2026, 1, 20)


def test_parse_date_range_dotdot():
    start, end = zmm.parse_date_range("2026-03-01..2026-03-15")
    assert start == date(2026, 3, 1)
    assert end == date(2026, 3, 15)


# ----------------------------- Config Loading ----------------------------- #


def test_load_config_ini_style():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".cfg", delete=False) as f:
        f.write("[paths]\ninput_dir = /tmp/input\noutput_dir = /tmp/output\n\n")
        f.write("[api]\nbase_url = https://example.com/v1\napi_key = test-key-123\n\n")
        f.write("[models]\nsummary = gpt-4o\n")
        f.name
    try:
        cfg = zmm.load_config(f.name, require_api=False)
        assert cfg.input_dir == "/tmp/input"
        assert cfg.output_dir == "/tmp/output"
        assert cfg.base_url == "https://example.com/v1"
        assert cfg.api_key == "test-key-123"
        assert cfg.models.get("summary") == "gpt-4o"
    finally:
        os.unlink(f.name)


def test_load_config_legacy_flat():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".cfg", delete=False) as f:
        f.write("api_key = flat-key-456\nbase_url = https://flat.example.com/v1\ndefault_model = o4-mini\n")
        f.name
    try:
        cfg = zmm.load_config(f.name, require_api=False)
        assert cfg.api_key == "flat-key-456"
        assert cfg.base_url == "https://flat.example.com/v1"
        assert cfg.models.get("summary") == "o4-mini"
    finally:
        os.unlink(f.name)


# ----------------------------- Table Rendering ----------------------------- #


def test_render_table_plain(capsys):
    zmm.render_table(["Name", "Count"], [["alpha", 3], ["beta", 10]], fmt="table", plain=True, color=False)
    out = capsys.readouterr().out
    assert "Name" in out
    assert "alpha" in out
    assert "beta" in out


def test_render_table_json(capsys):
    zmm.render_table(["Name", "Count"], [["alpha", 3]], fmt="json", plain=True, color=False)
    import json
    out = capsys.readouterr().out
    data = json.loads(out)
    assert len(data) == 1
    assert data[0]["Name"] == "alpha"


def test_render_table_csv(capsys):
    zmm.render_table(["Name", "Count"], [["alpha", 3]], fmt="csv", plain=True, color=False)
    out = capsys.readouterr().out
    assert "Name,Count" in out
    assert "alpha,3" in out


# ----------------------------- Transcript Helpers ----------------------------- #


def test_parse_chat_file_old_format():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("12:34:56 From Alice to Everyone: Hello there\n")
        f.write("12:35:00 From Bob to Everyone: Hi Alice\n")
    try:
        line_count, entries = zmm.parse_chat_file(f.name)
        assert line_count == 2
        assert len(entries) == 2
        assert entries[0] == ("12:34:56", "Alice", "Hello there")
        assert entries[1] == ("12:35:00", "Bob", "Hi Alice")
    finally:
        os.unlink(f.name)


def test_parse_chat_file_new_format():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("2026-04-21 15:02:29 From Alice to Everyone:\n")
        f.write("        Hello there\n")
        f.write("2026-04-21 15:03:00 From Bob to Everyone:\n")
        f.write("        Hi Alice\n")
    try:
        line_count, entries = zmm.parse_chat_file(f.name)
        assert line_count == 4
        assert len(entries) == 2
        assert entries[0][2] == "Hello there"
    finally:
        os.unlink(f.name)


def test_merge_captions_and_chat_basic():
    captions = "[Alice] 10:00:00: Good morning everyone\n[Bob] 10:00:05: Thanks for joining\n"
    chat = [("10:00:03", "Carol", "Running 2 min late")]
    result = zmm.merge_captions_and_chat(captions, chat)
    assert "[Alice]" in result
    assert "[Bob]" in result
    assert "[IN CHAT]" in result
    assert "Carol" in result


def test_merge_captions_collapses_same_speaker():
    captions = "[Alice] 10:00:00: First line\n[Alice] 10:00:01: Second line\n"
    result = zmm.merge_captions_and_chat(captions, [])
    # Same speaker should be merged into one line
    assert result.count("[Alice]") == 1
    assert "First line" in result
    assert "Second line" in result


def test_merge_captions_crossing_midnight_order():
    # A meeting that starts before midnight and continues after should keep
    # chronological order (not sort 00:xx before 23:xx).
    captions = (
        "[Alice] 23:58:00: before midnight\n"
        "[Bob] 00:02:00: after midnight\n"
    )
    result = zmm.merge_captions_and_chat(captions, [])
    assert result.index("before midnight") < result.index("after midnight")


def test_merge_no_sortkey_leak():
    captions = "[Alice] 10:00:00: hello\n"
    result = zmm.merge_captions_and_chat(captions, [])
    assert "_sortkey" not in result


# ----------------------------- Summary Validation ----------------------------- #


def test_validate_summary_output_complete():
    data = {k: "x" for k in zmm.SUMMARY_REQUIRED_KEYS}
    assert zmm.validate_summary_output(data, label="m") == []


def test_validate_summary_output_missing_fields():
    data = {"improved_title": "x", "one_liner": "y"}
    warnings = zmm.validate_summary_output(data, label="meeting.txt")
    assert len(warnings) == 1
    assert "missing fields" in warnings[0]
    assert "high_level_summary" in warnings[0]


def test_validate_summary_output_not_dict():
    warnings = zmm.validate_summary_output([], label="m")  # type: ignore[arg-type]
    assert warnings and "not a JSON object" in warnings[0]


# ----------------------------- Inventory ----------------------------- #


def test_expected_merged_name():
    result = zmm.expected_merged_name("2026-01-24 09.00.00 Board Retreat")
    assert "Board-Retreat" in result
    assert "meeting-saved-closed-caption.txt" in result
    assert " " not in result


def test_discover_inventory_empty(tmp_path):
    records = zmm.discover_inventory(str(tmp_path / "input"), str(tmp_path / "output"))
    assert records == []


def test_discover_inventory_finds_raw(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    meeting_dir = input_dir / "2026-01-24 09.00.00 Board Retreat"
    meeting_dir.mkdir(parents=True)
    (meeting_dir / "meeting_saved_closed_caption.txt").write_text("test caption")
    output_dir.mkdir(parents=True)

    records = zmm.discover_inventory(str(input_dir), str(output_dir))
    assert len(records) == 1
    assert records[0].title == "Board Retreat"
    assert records[0].has_raw


# ----------------------------- CLI ----------------------------- #


def test_version_flag(capsys):
    import subprocess
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / "zoom_meeting_manager.py"), "--version"],
        capture_output=True, text=True
    )
    assert "0.1.0" in result.stdout


def test_help_exits_zero():
    import subprocess
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / "zoom_meeting_manager.py"), "--help"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "zmm" in result.stdout


# ----------------------------- P2 audit coverage ----------------------------- #

import pytest
import argparse


# P2-T1: date range error cases
def test_parse_date_range_start_after_end():
    with pytest.raises(argparse.ArgumentTypeError):
        zmm.parse_date_range("2026-03 to 2026-01")


def test_parse_date_range_invalid_component():
    with pytest.raises(argparse.ArgumentTypeError):
        zmm.parse_date_range("notadate")


def test_parse_date_range_padded_colon_separator():
    start, end = zmm.parse_date_range("2026-01 : 2026-02")
    assert start == date(2026, 1, 1)
    assert end == date(2026, 2, 28)


def test_parse_date_range_does_not_split_on_unpadded_colon():
    # A bare colon (as in a time) must not be treated as a range separator.
    # "2026" with a trailing time-like token should fail cleanly, not silently split.
    with pytest.raises(argparse.ArgumentTypeError):
        zmm.parse_date_range("2026-01-01:bogus")


# P2-T4: expand_env
def test_expand_env_literal_with_dollar_preserved(monkeypatch):
    # A literal key with no var reference is returned unchanged even with '$'.
    assert zmm.expand_env("sk-abc$def") == "sk-abc$def"


def test_expand_env_env_ref(monkeypatch):
    monkeypatch.setenv("ZMM_TEST_KEY", "secret123")
    assert zmm.expand_env("{env:ZMM_TEST_KEY}") == "secret123"


def test_expand_env_shell_var(monkeypatch):
    monkeypatch.setenv("ZMM_TEST_KEY", "secret123")
    assert zmm.expand_env("${ZMM_TEST_KEY}") == "secret123"


# P2-S3: secret scrubbing
def test_scrub_secret_redacts_configured_key():
    cfg = zmm.Config(api_key="sk-supersecretvalue123")
    out = zmm._scrub_secret("error using sk-supersecretvalue123 now", cfg)
    assert "sk-supersecretvalue123" not in out
    assert "REDACTED" in out


def test_scrub_secret_redacts_skstyle_token():
    cfg = zmm.Config()
    out = zmm._scrub_secret("leaked sk-ABCDEFGH12345678 here", cfg)
    assert "sk-ABCDEFGH12345678" not in out


# P2-T7: extract kind filtering keyword sets differ
def test_extract_keyword_sets_differ():
    assert zmm._ACTION_KEYWORDS != zmm._STATEMENT_KEYWORDS
    assert "i'll" in zmm._ACTION_KEYWORDS
    assert "i think" in zmm._STATEMENT_KEYWORDS


# P2-E1: atomic_write_text
def test_atomic_write_text(tmp_path):
    target = tmp_path / "sub" / "out.txt"
    zmm.atomic_write_text(target, "hello world")
    assert target.read_text() == "hello world"
    # no temp files left behind
    leftovers = list((tmp_path / "sub").glob(".*tmp*"))
    assert leftovers == []


def test_atomic_write_text_overwrite(tmp_path):
    target = tmp_path / "out.txt"
    zmm.atomic_write_text(target, "first")
    zmm.atomic_write_text(target, "second")
    assert target.read_text() == "second"


# P2-T2: summary_exists
def test_summary_exists(tmp_path):
    cfg = zmm.Config(output_dir=str(tmp_path))
    rec = zmm.MeetingRecord(id="x", title="Test", meeting_date="2026-01-15")
    source = str(tmp_path / "2026-01-15-Test.txt")
    assert not zmm.summary_exists(rec, source, "gpt-4o", cfg)
    sdir = tmp_path / "Summaries-2026"
    sdir.mkdir()
    (sdir / "2026-01-15-Test.gpt-4o.summary.json").write_text("{}")
    assert zmm.summary_exists(rec, source, "gpt-4o", cfg)


# P2-T6: inventory dedup links raw + merged for legacy-named files
def test_discover_inventory_links_raw_and_merged(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    meeting = input_dir / "2026-01-24 09.00.00 Board Retreat"
    meeting.mkdir(parents=True)
    (meeting / "meeting_saved_closed_caption.txt").write_text("[A] 10:00:00: hi")
    merged_dir = output_dir / "Merged-Transcripts-2026"
    merged_dir.mkdir(parents=True)
    # Matches expected_merged_name for the raw dir
    name = zmm.expected_merged_name("2026-01-24 09.00.00 Board Retreat")
    (merged_dir / name).write_text("merged body")
    records = zmm.discover_inventory(str(input_dir), str(output_dir))
    assert len(records) == 1
    assert records[0].has_raw
    assert records[0].has_merged


# P2-T5: opencode key resolution
def test_resolve_opencode_key_literal():
    assert zmm._resolve_opencode_key("sk-literal") == "sk-literal"


def test_resolve_opencode_key_file(tmp_path):
    keyfile = tmp_path / "k.key"
    keyfile.write_text("filekey123\n")
    assert zmm._resolve_opencode_key(f"{{file:{keyfile}}}") == "filekey123"


def test_resolve_opencode_key_missing_file(tmp_path):
    assert zmm._resolve_opencode_key(f"{{file:{tmp_path}/nope.key}}") is None


# ----------------------------- Part-2 follow-up coverage ----------------------------- #

def test_count_tokens_uses_tiktoken():
    # With tiktoken installed, encoding should be stable and reasonable.
    n = zmm.count_tokens("Hello world, this is a test of tokenization.")
    assert n > 0
    # Same input is deterministic
    assert n == zmm.count_tokens("Hello world, this is a test of tokenization.")
    # A longer string yields more tokens
    assert zmm.count_tokens("a " * 100) > n


def test_count_tokens_in_file(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("some words here for counting")
    assert zmm.count_tokens_in_file(f) > 0
    assert zmm.count_tokens_in_file(tmp_path / "missing.txt") == 0


def test_records_from_files_no_date_uses_mtime(tmp_path):
    f = tmp_path / "no-date-title.txt"
    f.write_text("[A] 10:00:00: hi")
    recs = zmm.records_from_files([str(f)])
    assert len(recs) == 1
    # meeting_date should be a real ISO date (from mtime), not 'unknown'
    assert recs[0].meeting_date is not None
    assert recs[0].meeting_date[:2] == "20"
    assert "unknown" not in recs[0].id


def test_run_journal_roundtrip(tmp_path):
    j = zmm.RunJournal(tmp_path, "summarize")
    j.mark("/path/a.txt", "done")
    j.mark("/path/b.txt", "failed")
    done = zmm.load_resume_done(tmp_path, "summarize")
    assert done == {"/path/a.txt"}


def test_run_journal_finish_removes(tmp_path):
    j = zmm.RunJournal(tmp_path, "clean")
    j.mark("/x.txt", "done")
    assert j.path.exists()
    j.finish()
    assert not j.path.exists()


def test_load_resume_done_no_journal(tmp_path):
    assert zmm.load_resume_done(tmp_path, "summarize") == set()
