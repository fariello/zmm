"""Command-level tests using the fake OpenAI client (P3-T2..T5, T7, T16)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import zoom_meeting_manager as zmm  # noqa: E402

from helpers import VALID_MODEL_OUTPUT, make_meeting_tree  # noqa: E402


def _ns(**kw):
    """Build an argparse-like Namespace with sensible defaults for command fns."""
    import argparse
    defaults = dict(
        input_dir=None, output_dir=None, date_range=None, match=None, max=None,
        format="table", color="never", plain=True, dry_run=False, clobber=False,
        ignore_model_errors=False, yes=True, max_input_tokens=None, debug=False,
        no_context=False, resume=False, config=None,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


# ----------------------------- choose_summary_source (P3-T5) ----------------------------- #

def _rec(merged=None, cleaned=None):
    r = zmm.MeetingRecord(id="x", title="T", meeting_date="2026-01-15")
    r.merged_path = merged
    r.cleaned_paths = list(cleaned or [])
    return r


def test_source_cleaned_if_available_prefers_cleaned():
    rec = _rec(merged="/m.txt", cleaned=["/c.txt"])
    assert zmm.choose_summary_source(rec, zmm.Config(), _ns(summarization_source="cleaned_if_available")) == "/c.txt"


def test_source_cleaned_if_available_falls_back_to_merged():
    rec = _rec(merged="/m.txt", cleaned=[])
    assert zmm.choose_summary_source(rec, zmm.Config(), _ns(summarization_source="cleaned_if_available")) == "/m.txt"


def test_source_merged_forces_merged_even_with_cleaned():
    rec = _rec(merged="/m.txt", cleaned=["/c.txt"])
    assert zmm.choose_summary_source(rec, zmm.Config(), _ns(summarization_source="merged")) == "/m.txt"


def test_source_required_cleaned_skips_when_missing():
    rec = _rec(merged="/m.txt", cleaned=[])
    assert zmm.choose_summary_source(rec, zmm.Config(), _ns(summarization_source="required_cleaned")) is None


def test_source_required_cleaned_uses_cleaned():
    rec = _rec(merged="/m.txt", cleaned=["/c.txt"])
    assert zmm.choose_summary_source(rec, zmm.Config(), _ns(summarization_source="required_cleaned")) == "/c.txt"


def test_source_only_cleaned_flag_forces_required():
    rec = _rec(merged="/m.txt", cleaned=[])
    assert zmm.choose_summary_source(rec, zmm.Config(), _ns(summarization_source="cleaned_if_available", only_cleaned_transcripts=True)) is None


def test_source_cleaned_mode_uses_last_cleaned():
    rec = _rec(merged="/m.txt", cleaned=["/c1.txt", "/c2.txt"])
    assert zmm.choose_summary_source(rec, zmm.Config(), _ns(summarization_source="cleaned")) == "/c2.txt"


# ----------------------------- cmd_summarize (P3-T2, P3-T7) ----------------------------- #

def _summary_cfg(output_dir, model="gpt-4o"):
    cfg = zmm.Config(output_dir=str(output_dir))
    cfg.models["summary"] = model
    return cfg


def test_cmd_summarize_writes_outputs(tmp_path, fake_client):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True)
    client = fake_client(json.dumps(VALID_MODEL_OUTPUT))
    cfg = _summary_cfg(output_dir)
    args = _ns(output_dir=str(output_dir), summarize_object="merged",
               summarization_source="merged")
    zmm.cmd_summarize(args, cfg)
    assert client.calls, "model should have been called"
    summaries = list((output_dir / "Summaries-2026").glob("*.summary.json"))
    txts = list((output_dir / "Summaries-2026").glob("*.summary.txt"))
    assert len(summaries) == 1
    assert len(txts) == 1
    payload = json.loads(summaries[0].read_text())
    # P3-T7: meeting metadata is computed by zmm, model_output from the model
    assert "meeting" in payload and "model_output" in payload and "metadata" in payload
    assert payload["model_output"]["improved_title"] == VALID_MODEL_OUTPUT["improved_title"]
    assert payload["metadata"]["model"] == "gpt-4o"


def test_cmd_summarize_skips_existing(tmp_path, fake_client):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True, with_summary=True)
    client = fake_client(json.dumps(VALID_MODEL_OUTPUT))
    cfg = _summary_cfg(output_dir)
    args = _ns(output_dir=str(output_dir), summarize_object="merged",
               summarization_source="merged", clobber=False)
    zmm.cmd_summarize(args, cfg)
    assert client.calls == [], "should skip when summary already exists"


def test_cmd_summarize_clobber_overwrites(tmp_path, fake_client):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True, with_summary=True)
    client = fake_client(json.dumps(VALID_MODEL_OUTPUT))
    cfg = _summary_cfg(output_dir)
    args = _ns(output_dir=str(output_dir), summarize_object="merged",
               summarization_source="merged", clobber=True)
    zmm.cmd_summarize(args, cfg)
    assert len(client.calls) == 1, "clobber should re-summarize"


def test_cmd_summarize_dry_run_no_calls(tmp_path, fake_client):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True)
    client = fake_client(json.dumps(VALID_MODEL_OUTPUT))
    cfg = _summary_cfg(output_dir)
    args = _ns(output_dir=str(output_dir), summarize_object="merged",
               summarization_source="merged", dry_run=True)
    zmm.cmd_summarize(args, cfg)
    assert client.calls == []
    assert not (output_dir / "Summaries-2026").exists()


def test_cmd_summarize_invalid_json_saves_diagnostic(tmp_path, fake_client):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True)
    fake_client("this is not json")
    cfg = _summary_cfg(output_dir)
    args = _ns(output_dir=str(output_dir), summarize_object="merged",
               summarization_source="merged", ignore_model_errors=True)
    zmm.cmd_summarize(args, cfg)
    diags = list((output_dir / "Diagnostics").rglob("*.invalid-response.txt"))
    assert diags, "invalid model JSON should produce a diagnostic"


# ----------------------------- cmd_clean (P3-T3) ----------------------------- #

def test_cmd_clean_writes_cleaned(tmp_path, fake_client):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True)
    client = fake_client("cleaned transcript body")
    cfg = zmm.Config(output_dir=str(output_dir))
    cfg.models["cleanup"] = "gpt-4o"
    args = _ns(output_dir=str(output_dir), cleanup_prompt=None)
    zmm.cmd_clean(args, cfg)
    cleaned = list((output_dir / "Cleaned-Transcripts-2026").glob("*.cleaned.txt"))
    assert len(cleaned) == 1
    assert "cleaned transcript body" in cleaned[0].read_text()


def test_cmd_clean_skips_existing(tmp_path, fake_client):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True, with_cleaned=True)
    client = fake_client("new body")
    cfg = zmm.Config(output_dir=str(output_dir))
    cfg.models["cleanup"] = "gpt-4o"
    args = _ns(output_dir=str(output_dir), cleanup_prompt=None, clobber=False)
    zmm.cmd_clean(args, cfg)
    assert client.calls == []


def test_cmd_clean_dry_run(tmp_path, fake_client):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True)
    client = fake_client("body")
    cfg = zmm.Config(output_dir=str(output_dir))
    cfg.models["cleanup"] = "gpt-4o"
    args = _ns(output_dir=str(output_dir), cleanup_prompt=None, dry_run=True)
    zmm.cmd_clean(args, cfg)
    assert client.calls == []


# ----------------------------- cmd_extract kind filtering (P3-T4) ----------------------------- #

def test_extract_kind_filtering_distinct(tmp_path, capsys):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True)
    # Overwrite merged transcript with lines that match different kinds.
    merged = list((output_dir / "Merged-Transcripts-2026").glob("*.txt"))[0]
    # Lines crafted so each matches exactly one kind:
    #  - action line: contains "send" (action) but no statement keyword
    #  - statement line: contains "i think" (statement) but no action keyword
    merged.write_text(
        "[Alice] 10:00:00: Alice will send the report\n"
        "[Alice] 10:01:00: Alice I think the design is elegant\n"
        "[Alice] 10:02:00: Alice unrelated chit chat\n"
    )
    cfg = zmm.Config(output_dir=str(output_dir))
    cfg.default_person = "me"
    cfg.people["me"] = {"display_name": "Alice", "aliases": ["Alice"]}

    args_actions = _ns(output_dir=str(output_dir), extract_object="person",
                       kind="actions", person="me", regex=None, format="csv", plain=True)
    zmm.cmd_extract(args_actions, cfg)
    actions_out = capsys.readouterr().out

    args_stmts = _ns(output_dir=str(output_dir), extract_object="person",
                     kind="statements", person="me", regex=None, format="csv", plain=True)
    zmm.cmd_extract(args_stmts, cfg)
    stmts_out = capsys.readouterr().out

    assert "send the report" in actions_out
    assert "send the report" not in stmts_out
    assert "design is elegant" in stmts_out
    assert "design is elegant" not in actions_out


def test_extract_search_invalid_regex(tmp_path):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True)
    cfg = zmm.Config(output_dir=str(output_dir))
    args = _ns(output_dir=str(output_dir), extract_object="search", regex="[", match=None)
    with pytest.raises(SystemExit):
        zmm.cmd_extract(args, cfg)


# ----------------------------- get_records (P3-T16) ----------------------------- #

def test_get_records_requires_output_dir():
    cfg = zmm.Config()
    args = _ns()
    with pytest.raises(SystemExit):
        zmm.get_records(args, cfg)


def test_get_records_date_range_filters(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True)
    for d in ("2026-01-10", "2026-06-10"):
        md = input_dir / f"{d} 09.00.00 Meeting {d}"
        md.mkdir(parents=True)
        (md / "meeting_saved_closed_caption.txt").write_text("[A] 10:00:00: hi")
    cfg = zmm.Config(input_dir=str(input_dir), output_dir=str(output_dir))
    args = _ns(input_dir=str(input_dir), output_dir=str(output_dir), date_range="2026-06")
    recs = zmm.get_records(args, cfg)
    assert len(recs) == 1
    assert recs[0].meeting_date == "2026-06-10"


def test_get_records_match_filters(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True)
    for title in ("Budget Sync", "Staffing Review"):
        md = input_dir / f"2026-01-10 09.00.00 {title}"
        md.mkdir(parents=True)
        (md / "meeting_saved_closed_caption.txt").write_text("[A] 10:00:00: hi")
    cfg = zmm.Config(input_dir=str(input_dir), output_dir=str(output_dir))
    args = _ns(input_dir=str(input_dir), output_dir=str(output_dir), match="budget")
    recs = zmm.get_records(args, cfg)
    assert len(recs) == 1
    assert "Budget" in recs[0].title


# ----------------------------- cmd_delete_raw (P3-T13) ----------------------------- #

def test_delete_raw_moves_to_trash(tmp_path, monkeypatch):
    input_dir, output_dir = make_meeting_tree(tmp_path, with_raw=True, with_merged=True)
    cfg = zmm.Config(input_dir=str(input_dir), output_dir=str(output_dir))
    args = _ns(input_dir=str(input_dir), output_dir=str(output_dir), yes=True)
    zmm.cmd_delete_raw(args, cfg)
    trash = output_dir / "to-delete"
    moved = list(trash.iterdir()) if trash.exists() else []
    assert len(moved) == 1
    # original raw dir is gone
    assert not any(input_dir.iterdir())


def test_delete_raw_dry_run_keeps(tmp_path):
    input_dir, output_dir = make_meeting_tree(tmp_path, with_raw=True, with_merged=True)
    cfg = zmm.Config(input_dir=str(input_dir), output_dir=str(output_dir))
    args = _ns(input_dir=str(input_dir), output_dir=str(output_dir), dry_run=True)
    zmm.cmd_delete_raw(args, cfg)
    assert not (output_dir / "to-delete").exists()
    assert any(input_dir.iterdir())  # raw still there


def test_delete_raw_skips_without_merged(tmp_path, capsys):
    input_dir, output_dir = make_meeting_tree(tmp_path, with_raw=True, with_merged=False)
    cfg = zmm.Config(input_dir=str(input_dir), output_dir=str(output_dir))
    args = _ns(input_dir=str(input_dir), output_dir=str(output_dir), yes=True)
    zmm.cmd_delete_raw(args, cfg)
    out = capsys.readouterr().out
    assert "No raw directories eligible" in out


# ----------------------------- cmd_clean_diagnostics (P3-T14) ----------------------------- #

def test_clean_diagnostics_deletes(tmp_path):
    output_dir = tmp_path / "output"
    diag = output_dir / "Diagnostics" / "2026"
    diag.mkdir(parents=True)
    (diag / "x.invalid-response.txt").write_text("bad")
    cfg = zmm.Config(output_dir=str(output_dir))
    args = _ns(output_dir=str(output_dir), yes=True, older_than=None)
    zmm.cmd_clean_diagnostics(args, cfg)
    assert not (output_dir / "Diagnostics").exists()


def test_clean_diagnostics_dry_run(tmp_path):
    output_dir = tmp_path / "output"
    diag = output_dir / "Diagnostics" / "2026"
    diag.mkdir(parents=True)
    f = diag / "x.invalid-response.txt"
    f.write_text("bad")
    cfg = zmm.Config(output_dir=str(output_dir))
    args = _ns(output_dir=str(output_dir), dry_run=True, older_than=None)
    zmm.cmd_clean_diagnostics(args, cfg)
    assert f.exists()


def test_clean_diagnostics_none(tmp_path, capsys):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    cfg = zmm.Config(output_dir=str(output_dir))
    args = _ns(output_dir=str(output_dir), older_than=None)
    zmm.cmd_clean_diagnostics(args, cfg)
    out = capsys.readouterr().out
    assert "nothing to clean" in out.lower() or "No Diagnostics" in out


# ----------------------------- cmd_show / cmd_show_prompt ----------------------------- #

def test_cmd_show_config_runs(tmp_path, capsys):
    cfg = zmm.Config(output_dir=str(tmp_path), api_key="k", base_url="https://x/v1")
    cfg.models["summary"] = "gpt-4o"
    args = _ns(output_dir=str(tmp_path), config=None)
    zmm.cmd_show(args, cfg)
    out = capsys.readouterr().out
    assert "Core prompts" in out
    assert "gpt-4o" in out


def test_cmd_show_prompt_runs(tmp_path, capsys, monkeypatch):
    # Avoid pulling in the user's real augmentation files.
    monkeypatch.setattr(zmm, "USER_PROMPTS_DIR", tmp_path / "noprompts")
    cfg = zmm.Config(output_dir=str(tmp_path))
    cfg.models["summary"] = "gpt-4o"
    args = _ns(output_dir=str(tmp_path), task="summary",
               prompt_layer=None, prompt_context=None, prompt_person=None,
               prompt_correction=None)
    zmm.cmd_show_prompt(args, cfg)
    out = capsys.readouterr().out
    assert "CORE" in out
    assert "System prompt size" in out


# ----------------------------- rows_overview (P3-T8 render side) ----------------------------- #

def test_rows_overview_marks(tmp_path):
    r = zmm.MeetingRecord(id="x", title="T", meeting_date="2026-01-15")
    r.caption_path = "/cap.txt"
    r.merged_path = "/m.txt"
    rows = zmm.rows_overview([r], color=False)
    assert len(rows) == 1
    assert rows[0][0] == "2026-01-15"
    assert rows[0][1] == "T"


# ----------------------------- P5: merge command ----------------------------- #

def test_cmd_merge_creates_merged(tmp_path):
    input_dir, output_dir = make_meeting_tree(tmp_path, with_raw=True, with_merged=False)
    cfg = zmm.Config(input_dir=str(input_dir), output_dir=str(output_dir))
    args = _ns(input_dir=str(input_dir), output_dir=str(output_dir))
    zmm.cmd_merge(args, cfg)
    merged = list((output_dir / "Merged-Transcripts-2026").glob("*.txt"))
    assert len(merged) == 1


def test_cmd_merge_no_model_call(tmp_path, fake_client):
    input_dir, output_dir = make_meeting_tree(tmp_path, with_raw=True, with_merged=False)
    client = fake_client("should not be called")
    cfg = zmm.Config(input_dir=str(input_dir), output_dir=str(output_dir))
    args = _ns(input_dir=str(input_dir), output_dir=str(output_dir))
    zmm.cmd_merge(args, cfg)
    assert client.calls == []  # merge is local-only


def test_cmd_merge_nothing_to_do(tmp_path, capsys):
    input_dir, output_dir = make_meeting_tree(tmp_path, with_raw=True, with_merged=True)
    cfg = zmm.Config(input_dir=str(input_dir), output_dir=str(output_dir))
    args = _ns(input_dir=str(input_dir), output_dir=str(output_dir))
    zmm.cmd_merge(args, cfg)
    out = capsys.readouterr().out
    assert "No raw meetings to merge" in out


def test_cmd_merge_dry_run(tmp_path):
    input_dir, output_dir = make_meeting_tree(tmp_path, with_raw=True, with_merged=False)
    cfg = zmm.Config(input_dir=str(input_dir), output_dir=str(output_dir))
    args = _ns(input_dir=str(input_dir), output_dir=str(output_dir), dry_run=True)
    zmm.cmd_merge(args, cfg)
    assert not (output_dir / "Merged-Transcripts-2026").exists()


# ----------------------------- P5: empty-result notices ----------------------------- #

def test_list_meetings_empty_notice(tmp_path, capsys):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    cfg = zmm.Config(output_dir=str(output_dir))
    args = _ns(output_dir=str(output_dir), list_object="meetings")
    zmm.cmd_list(args, cfg)
    out = capsys.readouterr().out
    assert "No meetings found" in out


def test_list_meetings_empty_json_still_valid(tmp_path, capsys):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    cfg = zmm.Config(output_dir=str(output_dir))
    args = _ns(output_dir=str(output_dir), list_object="meetings", format="json")
    zmm.cmd_list(args, cfg)
    out = capsys.readouterr().out
    assert out.strip() == "[]"  # machine format unaffected


def test_report_status_empty_notice(tmp_path, capsys):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    cfg = zmm.Config(output_dir=str(output_dir))
    args = _ns(output_dir=str(output_dir), report_object="status")
    zmm.cmd_report(args, cfg)
    out = capsys.readouterr().out
    assert "No meetings found" in out


# ----------------------------- P5: input-dir warning ----------------------------- #

def test_discover_inventory_warns_bad_input_dir(tmp_path, capsys):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    zmm.discover_inventory("/no/such/input/dir", str(output_dir))
    err = capsys.readouterr().err
    assert "does not exist" in err


# ----------------------------- P5: progress output ----------------------------- #

def test_summarize_shows_progress(tmp_path, fake_client, capsys):
    import json as _json
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True)
    fake_client(_json.dumps(VALID_MODEL_OUTPUT))
    cfg = _summary_cfg(output_dir)
    args = _ns(output_dir=str(output_dir), summarize_object="merged", summarization_source="merged")
    zmm.cmd_summarize(args, cfg)
    captured = capsys.readouterr()
    # Progress now goes to stderr (keeps stdout json/csv clean) and includes
    # a [idx/total] counter plus timing/elapsed fields.
    assert "[1/1] Summarizing" in captured.err
    assert "elapsed" in captured.err


# ----------------------------- P5-F2: paths command ----------------------------- #

def test_cmd_paths_merged(tmp_path, capsys):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True)
    cfg = zmm.Config(output_dir=str(output_dir))
    args = _ns(output_dir=str(output_dir), kind="merged")
    zmm.cmd_paths(args, cfg)
    out = capsys.readouterr().out
    assert "Merged-Transcripts-2026" in out
    assert out.strip().endswith(".txt")


def test_cmd_paths_summary(tmp_path, capsys):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True, with_summary=True)
    cfg = zmm.Config(output_dir=str(output_dir))
    args = _ns(output_dir=str(output_dir), kind="summary")
    zmm.cmd_paths(args, cfg)
    out = capsys.readouterr().out
    assert ".summary.txt" in out


def test_cmd_paths_all_lists_multiple(tmp_path, capsys):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=True, with_merged=True, with_summary=True)
    cfg = zmm.Config(input_dir=str(tmp_path / "input"), output_dir=str(output_dir))
    args = _ns(input_dir=str(tmp_path / "input"), output_dir=str(output_dir), kind="all")
    zmm.cmd_paths(args, cfg)
    out = capsys.readouterr().out
    # raw caption + merged + summary all present
    assert "meeting_saved_closed_caption.txt" in out
    assert "Merged-Transcripts-2026" in out
    assert ".summary.txt" in out


def test_cmd_paths_empty_notice(tmp_path, capsys):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    cfg = zmm.Config(output_dir=str(output_dir))
    args = _ns(output_dir=str(output_dir), kind="merged")
    zmm.cmd_paths(args, cfg)
    err = capsys.readouterr().err
    assert "No matching paths" in err


# ----------------------------- P5-M1: call_model_text helper ----------------------------- #

def test_call_model_text_returns_content(fake_client):
    import argparse
    client = fake_client("cleaned body")
    cfg = zmm.Config(api_key="k")
    args = argparse.Namespace(ignore_model_errors=False)
    out = zmm.call_model_text(cfg, args, client=client, model="m",
                              messages=zmm.chat_messages("sys", "usr"),
                              operation="clean", label="x")
    assert out == "cleaned body"


def test_call_model_text_ignore_errors_returns_none(monkeypatch):
    import argparse

    class Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("api down")
    cfg = zmm.Config(api_key="k")
    args = argparse.Namespace(ignore_model_errors=True)
    out = zmm.call_model_text(cfg, args, client=Boom(), model="m",
                              messages=zmm.chat_messages("s", "u"),
                              operation="clean", label="x")
    assert out is None


# ----------------------------- truncation detection (finish_reason='length') ----------------------------- #

def test_completion_sends_default_max_output_tokens(fake_client):
    import argparse
    client = fake_client("{}")
    cfg = zmm.Config(api_key="k")
    args = argparse.Namespace(ignore_model_errors=False)
    zmm.call_model_json(cfg, args, model="m",
                        messages=zmm.chat_messages("s", "u"),
                        operation="summarize", label="x")
    assert client.calls[-1]["kwargs"].get("max_tokens") == zmm.DEFAULT_MAX_OUTPUT_TOKENS


def test_completion_respects_max_output_tokens_arg(fake_client):
    import argparse
    client = fake_client("{}")
    cfg = zmm.Config(api_key="k")
    args = argparse.Namespace(ignore_model_errors=False, max_output_tokens=32000)
    zmm.call_model_json(cfg, args, model="m",
                        messages=zmm.chat_messages("s", "u"),
                        operation="summarize", label="x")
    assert client.calls[-1]["kwargs"].get("max_tokens") == 32000


def test_completion_no_cap_when_zero(fake_client):
    import argparse
    client = fake_client("{}")
    cfg = zmm.Config(api_key="k")
    args = argparse.Namespace(ignore_model_errors=False, max_output_tokens=0)
    zmm.call_model_json(cfg, args, model="m",
                        messages=zmm.chat_messages("s", "u"),
                        operation="summarize", label="x")
    assert "max_tokens" not in client.calls[-1]["kwargs"]


def test_truncated_response_detected_and_specific_error(fake_client, capsys):
    import argparse
    # Valid-looking partial JSON, but provider says it stopped on length.
    client = fake_client('{"improved_title": "partial', finish_reason="length")
    cfg = zmm.Config(api_key="k")
    args = argparse.Namespace(ignore_model_errors=False)
    with pytest.raises(SystemExit):
        zmm.call_model_json(cfg, args, model="m",
                            messages=zmm.chat_messages("s", "u"),
                            operation="summarize", label="x")
    err = capsys.readouterr().err
    assert "truncated" in err
    assert "--max-output-tokens" in err  # actionable
    # Must NOT fall back to the generic auth/network advice.
    assert "Check API key" not in err


def test_truncated_response_ignore_errors_returns_empty(fake_client):
    import argparse
    client = fake_client("{", finish_reason="length")
    cfg = zmm.Config(api_key="k")
    args = argparse.Namespace(ignore_model_errors=True)
    out = zmm.call_model_json(cfg, args, model="m",
                              messages=zmm.chat_messages("s", "u"),
                              operation="summarize", label="x")
    assert out == {}


def test_error_hint_categories():
    assert "output limit" in zmm._error_hint(zmm.ModelTruncationError("x"))
    assert "not valid JSON" in zmm._error_hint(json.JSONDecodeError("m", "doc", 0))
    assert "Authentication" in zmm._error_hint(RuntimeError("401 Unauthorized"))


# ----------------------------- ProgressReporter (timing + cost) ----------------------------- #

def test_progress_reporter_timing_fields(capsys):
    import io
    buf = io.StringIO()
    pr = zmm.ProgressReporter(2, verb="Summarizing", model="no-such-model", stream=buf)
    pr.start_item(1, "Alpha")
    pr.finish_item(ok=True)
    pr.start_item(2, "Beta")   # second start should show an ETA (we have 1 done)
    pr.finish_item(ok=True)
    pr.summary()
    out = buf.getvalue()
    assert "[1/2] Summarizing Alpha" in out
    assert "[2/2] Summarizing Beta" in out
    assert "ETA" in out          # appears on the 2nd start and on finish lines
    assert "elapsed" in out
    assert "Time:" in out
    # No pricing for this fake model -> no cost fields.
    assert "cost $" not in out


def test_progress_reporter_cost_from_usage(monkeypatch):
    import io
    # $2/M input, $6/M output for our test model.
    monkeypatch.setattr(zmm, "_load_model_costs",
                        lambda: {"m": {"input": 2.0, "output": 6.0}})
    buf = io.StringIO()
    pr = zmm.ProgressReporter(1, verb="Summarizing", model="m", stream=buf)
    pr.start_item(1, "Alpha")
    # Simulate usage recorded by _extract_content: 1,000,000 in + 1,000,000 out.
    monkeypatch.setattr(zmm, "_LAST_USAGE", (1_000_000, 1_000_000))
    pr.finish_item(ok=True)
    pr.summary()
    out = buf.getvalue()
    assert "cost $8.0000" in out          # 1M*$2 + 1M*$6
    assert "1,000,000 in + 1,000,000 out tokens" in out


def test_usage_from_response_variants():
    class U1:  # OpenAI naming
        prompt_tokens = 10
        completion_tokens = 5
    class U2:  # alternative naming
        input_tokens = 7
        output_tokens = 3
    class R:
        def __init__(self, u): self.usage = u
    assert zmm._usage_from_response(R(U1())) == (10, 5)
    assert zmm._usage_from_response(R(U2())) == (7, 3)
    assert zmm._usage_from_response(R(None)) == (0, 0)


# ----------------------------- P7-X1: actionable openai-missing error ----------------------------- #

def test_client_for_missing_openai_actionable(monkeypatch):
    monkeypatch.setattr(zmm, "openai", None)
    cfg = zmm.Config(api_key="k")
    with pytest.raises(SystemExit) as exc:
        zmm.client_for(cfg)
    msg = str(exc.value)
    assert "openai" in msg
    assert "pip install" in msg  # tells the user how to fix it


# ----------------------------- selector consistency (list == fix) ----------------------------- #

def _two_meeting_mixed_tree(tmp_path, model="gpt-4o"):
    """Build an output dir with TWO 2026 meetings: one already summarized, one not."""
    output_dir = tmp_path / "output"
    md = output_dir / "Merged-Transcripts-2026"
    md.mkdir(parents=True)
    safe = model.replace("/", "--")
    # Meeting A: merged + summary (should NOT need summarizing)
    stemA = "2026-01-10-09.00.00-Has-Summary-meeting-saved-closed-caption"
    (md / f"{stemA}.txt").write_text("[A] 10:00:00: a\n")
    sd = output_dir / "Summaries-2026"
    sd.mkdir(parents=True)
    (sd / f"{stemA}.{safe}.summary.json").write_text("{}")
    (sd / f"{stemA}.{safe}.summary.txt").write_text("done")
    # Meeting B: merged only (SHOULD need summarizing)
    stemB = "2026-02-20-09.00.00-Needs-Summary-meeting-saved-closed-caption"
    (md / f"{stemB}.txt").write_text("[A] 10:00:00: b\n")
    return output_dir


def test_select_summarizable_excludes_already_summarized(tmp_path):
    output_dir = _two_meeting_mixed_tree(tmp_path)
    cfg = zmm.Config(output_dir=str(output_dir))
    cfg.models["summary"] = "gpt-4o"
    args = _ns(output_dir=str(output_dir), summarization_source="merged")
    records = zmm.get_records(args, cfg)
    to_process, n_skipped = zmm.select_summarizable(records, args, cfg, "gpt-4o")
    titles = sorted(r.title for r, _ in to_process)
    assert titles == ["Needs Summary"]
    assert n_skipped == 1  # the already-summarized one


def test_list_missing_summaries_matches_fix_selection(tmp_path):
    output_dir = _two_meeting_mixed_tree(tmp_path)
    cfg = zmm.Config(output_dir=str(output_dir))
    cfg.models["summary"] = "gpt-4o"
    args = _ns(output_dir=str(output_dir), summarization_source="merged")
    records = zmm.get_records(args, cfg)
    # What 'fix' would process:
    fix_set, _ = zmm.select_summarizable(records, args, cfg, "gpt-4o")
    fix_titles = sorted(r.title for r, _ in fix_set)
    # What 'list missing-summaries' shows (same selector path):
    model = zmm.get_model(cfg, args, "summary")
    list_set, _ = zmm.select_summarizable(records, args, cfg, model)
    list_titles = sorted(r.title for r, _ in list_set)
    assert fix_titles == list_titles == ["Needs Summary"]


def test_select_summarizable_clobber_includes_all(tmp_path):
    output_dir = _two_meeting_mixed_tree(tmp_path)
    cfg = zmm.Config(output_dir=str(output_dir))
    cfg.models["summary"] = "gpt-4o"
    args = _ns(output_dir=str(output_dir), summarization_source="merged", clobber=True)
    records = zmm.get_records(args, cfg)
    to_process, n_skipped = zmm.select_summarizable(records, args, cfg, "gpt-4o")
    assert len(to_process) == 2  # clobber re-does the summarized one too
    assert n_skipped == 0


# ----------------------------- any-model vs explicit-model selection ----------------------------- #

def test_select_default_skips_any_existing_model(tmp_path):
    # A meeting summarized by o4-mini is "done" by default, even though the
    # configured model is different.
    output_dir = tmp_path / "output"
    md = output_dir / "Merged-Transcripts-2026"
    md.mkdir(parents=True)
    stem = "2026-01-10-09.00.00-M-meeting-saved-closed-caption"
    (md / f"{stem}.txt").write_text("[A] 10:00:00: hi\n")
    sd = output_dir / "Summaries-2026"
    sd.mkdir(parents=True)
    (sd / f"{stem}.o4-mini.summary.txt").write_text("notes")  # only .txt, no .json
    cfg = zmm.Config(output_dir=str(output_dir))
    cfg.models["summary"] = "claude-x"
    args = _ns(output_dir=str(output_dir), summarization_source="merged")
    records = zmm.get_records(args, cfg)
    to_process, n_skipped = zmm.select_summarizable(records, args, cfg, "claude-x")
    assert to_process == []           # default: any summary counts
    assert n_skipped == 1


def test_select_explicit_model_backfills(tmp_path):
    output_dir = tmp_path / "output"
    md = output_dir / "Merged-Transcripts-2026"
    md.mkdir(parents=True)
    stem = "2026-01-10-09.00.00-M-meeting-saved-closed-caption"
    (md / f"{stem}.txt").write_text("[A] 10:00:00: hi\n")
    sd = output_dir / "Summaries-2026"
    sd.mkdir(parents=True)
    (sd / f"{stem}.o4-mini.summary.txt").write_text("notes")
    cfg = zmm.Config(output_dir=str(output_dir))
    args = _ns(output_dir=str(output_dir), summarization_source="merged",
               summary_model="claude-x")
    records = zmm.get_records(args, cfg)
    # Explicit model: o4-mini summary does NOT satisfy a claude request.
    to_process, n_skipped = zmm.select_summarizable(records, args, cfg, "claude-x")
    assert len(to_process) == 1
    assert n_skipped == 0
