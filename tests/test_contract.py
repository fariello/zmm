"""Contract & rendering tests (P3-R3, R4, R5, T6, T8, T9)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import zoom_meeting_manager as zmm  # noqa: E402
from helpers import VALID_MODEL_OUTPUT  # noqa: E402


REPO = Path(__file__).resolve().parent.parent


# ----------------------------- Schema contract (P3-R3) ----------------------------- #

def test_schema_required_keys_match_code():
    schema = json.loads((REPO / "schemas" / "summary.json").read_text())
    schema_required = set(schema["properties"]["model_output"]["required"])
    assert set(zmm.SUMMARY_REQUIRED_KEYS) == schema_required, (
        "SUMMARY_REQUIRED_KEYS in code has drifted from schemas/summary.json"
    )


def test_renderer_consumes_all_required_keys():
    # The renderer should not crash on a fully-populated, schema-valid payload,
    # and should surface the headline fields.
    payload = {"meeting": {"title": "T", "datetime": "2026-01-15 09:00:00", "duration": "00:30:00",
                           "source_path": "/m.txt"},
               "model_output": VALID_MODEL_OUTPUT,
               "metadata": {"model": "gpt-4o", "prompt_label": "core"}}
    text = zmm.render_summary_text(payload)
    assert VALID_MODEL_OUTPUT["improved_title"] in text
    assert VALID_MODEL_OUTPUT["one_liner"] in text
    assert "Budget" in text  # detailed_notes heading present


# ----------------------------- render_summary_text (P3-T6) ----------------------------- #

def test_render_handles_missing_optional_fields():
    payload = {"meeting": {"title": "T"}, "model_output": {"high_level_summary": "Just a summary."},
               "metadata": {}}
    text = zmm.render_summary_text(payload)
    assert "Just a summary." in text  # doesn't crash, renders what it has


def test_render_detailed_notes_string():
    payload = {"meeting": {"title": "T"},
               "model_output": {"detailed_notes": "### Topic\n\nbody text here"},
               "metadata": {}}
    text = zmm.render_summary_text(payload)
    assert "### Topic" in text
    assert "body text here" in text


def test_render_detailed_notes_legacy_array():
    payload = {"meeting": {"title": "T"},
               "model_output": {"detailed_notes": [{"topic": "Legacy", "notes": "old format"}]},
               "metadata": {}}
    text = zmm.render_summary_text(payload)
    assert "Legacy" in text
    assert "old format" in text


def test_render_falls_back_to_original_title():
    payload = {"meeting": {"title": "Original Title"},
               "model_output": {},  # no improved_title
               "metadata": {}}
    text = zmm.render_summary_text(payload)
    assert "Original Title" in text


# ----------------------------- Filename contract (P3-R4, P3-R5) ----------------------------- #

def test_summary_model_from_filename_roundtrip():
    stem = "2026-01-15-09.00.00-Board-Retreat-meeting-saved-closed-caption"
    model = "its_direct/pt3-claude-sonnet"
    safe = model.replace("/", "--")
    fname = Path(f"{stem}.{safe}.summary.txt")
    recovered = zmm.summary_model_from_filename(fname, stem)
    assert recovered == model


def test_summary_model_from_filename_simple():
    stem = "2026-01-15-Meeting"
    fname = Path(f"{stem}.gpt-4o.summary.txt")
    assert zmm.summary_model_from_filename(fname, stem) == "gpt-4o"


def test_summary_model_from_filename_non_summary():
    assert zmm.summary_model_from_filename(Path("foo.txt"), "foo") is None


def test_expected_merged_name_contract():
    # The merged filename convention that inventory relies on for re-discovery.
    name = zmm.expected_merged_name("2026-01-24 09.00.00 Board Retreat")
    assert name.endswith("meeting-saved-closed-caption.txt")
    assert " " not in name


# ----------------------------- compute_problems / filter_missing (P3-T8) ----------------------------- #

def _rec(raw=False, merged=False, cleaned=False, summary=False, summary_json=False):
    r = zmm.MeetingRecord(id="x", title="T", meeting_date="2026-01-15")
    if raw:
        r.caption_path = "/cap.txt"
    if merged:
        r.merged_path = "/m.txt"
    if cleaned:
        r.cleaned_paths = ["/c.txt"]
    if summary:
        sj = "/s.json" if summary_json else None
        r.summaries = [zmm.SummaryRecord(path="/s.txt", model="gpt-4o", json_path=sj)]
    return r


def test_problems_missing_raw():
    assert "missing raw" in zmm.compute_problems(_rec(merged=True))


def test_problems_missing_merged():
    assert "missing merged" in zmm.compute_problems(_rec(raw=True))


def test_problems_missing_summary():
    probs = zmm.compute_problems(_rec(raw=True, merged=True))
    assert "missing summary" in probs


def test_problems_missing_summary_json():
    probs = zmm.compute_problems(_rec(raw=True, merged=True, summary=True, summary_json=False))
    assert "missing summary json" in probs


def test_problems_complete_record_clean():
    probs = zmm.compute_problems(_rec(raw=True, merged=True, summary=True, summary_json=True))
    assert probs == []


def test_filter_missing_categories():
    records = [
        _rec(merged=True),                       # missing raw
        _rec(raw=True),                          # missing merged
        _rec(raw=True, merged=True),             # missing summary
    ]
    for r in records:
        r.problems = zmm.compute_problems(r)
    assert len(zmm.filter_missing(records, "raw")) == 1
    assert len(zmm.filter_missing(records, "merged")) == 1
    # Both the merged-only record and the raw+merged record lack a summary.
    assert len(zmm.filter_missing(records, "summaries")) == 2
    assert len(zmm.filter_missing(records, "all")) == 3
    assert zmm.filter_missing(records, "bogus") == []


def test_filter_missing_summaries_excludes_summary_with_no_json():
    # Regression: a meeting that HAS a summary .txt but lacks the .json sidecar
    # must NOT be reported by `list missing-summaries` (it has a summary).
    rec = _rec(raw=True, merged=True, summary=True, summary_json=False)
    rec.problems = zmm.compute_problems(rec)
    assert "missing summary json" in rec.problems
    assert "missing summary" not in rec.problems
    assert zmm.filter_missing([rec], "summaries") == []
    # It is reported by the dedicated summary-json filter instead.
    assert zmm.filter_missing([rec], "summary-json") == [rec]


def test_filter_missing_summaries_includes_no_summary():
    rec = _rec(raw=True, merged=True, summary=False)
    rec.problems = zmm.compute_problems(rec)
    assert zmm.filter_missing([rec], "summaries") == [rec]


def test_filter_has_categories():
    bare = _rec(raw=True)                                          # raw only
    summarized = _rec(raw=True, merged=True, summary=True)         # summary, no json
    full = _rec(raw=True, merged=True, summary=True, summary_json=True)
    records = [bare, summarized, full]
    assert zmm.filter_has(records, "raw") == records
    assert zmm.filter_has(records, "merged") == [summarized, full]
    assert zmm.filter_has(records, "summary") == [summarized, full]
    assert zmm.filter_has(records, "summary-json") == [full]
    assert zmm.filter_has(records, "json") == [full]              # alias
    # No filter / unknown kind -> unchanged.
    assert zmm.filter_has(records, None) == records
    assert zmm.filter_has(records, "bogus") == records


# ----------------------------- build_prompt assembly (P3-T9) ----------------------------- #

class _A:
    """Minimal args stand-in for build_prompt."""
    prompt_layer = None
    prompt_context = None
    prompt_person = None
    prompt_correction = None
    no_context = False


def test_build_prompt_core_and_schema():
    text, label = zmm.build_prompt(zmm.Config(), _A(), "summary")
    # core meeting_generic + output schema both present
    assert "Output format" in text or "JSON" in text or "json" in text
    assert "core:" in label
    assert "meeting_generic" in label
    assert "output_structured_notes" in label


def test_build_prompt_no_context_flag():
    a = _A()
    a.no_context = True
    text, label = zmm.build_prompt(zmm.Config(), a, "summary")
    assert "+no-context" in label


def test_build_prompt_cleanup_task_no_schema():
    text, label = zmm.build_prompt(zmm.Config(), _A(), "cleanup")
    # cleanup task should use cleanup prompt, not the summary schema
    assert "output_structured_notes" not in label
