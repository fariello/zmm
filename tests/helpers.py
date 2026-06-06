"""Importable test helpers (data + tree builders)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# A complete, schema-valid model_output payload for summary tests.
VALID_MODEL_OUTPUT = {
    "improved_title": "Q3 Budget and Staffing Review",
    "one_liner": "Reviewed the Q3 budget and approved two new engineering hires.",
    "high_level_summary": "The team reviewed Q3 budget proposals and agreed on hiring.",
    "key_takeaways": ["Budget approved", "Two hires planned"],
    "decisions": [{"decision": "Approve budget", "status": "final", "owner": "Alice", "confidence": "high"}],
    "action_items": [{"task": "Post job reqs", "owner": "Bob", "requested_by": "Alice",
                      "deadline": "2026-07-01", "priority": "high", "confidence": "high"}],
    "open_questions": ["When does onboarding start?"],
    "key_topics": ["budget", "staffing"],
    "attendees": {"present": ["Alice", "Bob"], "mentioned": ["Carol"], "uncertain": []},
    "detailed_notes": "### Budget\n\nAlice presented the Q3 numbers.\n\n### Staffing\n\nTwo hires approved.",
    "llm_notes": {"assumptions": [], "uncertain_corrections": [], "uncertain_attribution": []},
}


def make_meeting_tree(tmp_path, *, with_raw=True, with_merged=False,
                      with_cleaned=False, with_summary=False,
                      date_str="2026-01-15", title="Test Meeting",
                      model="gpt-4o"):
    """Create an input/output meeting fixture tree; return (input_dir, output_dir)."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    year = date_str[:4]
    stem = f"{date_str}-09.00.00-{title.replace(' ', '-')}-meeting-saved-closed-caption"

    if with_raw:
        d = input_dir / f"{date_str} 09.00.00 {title}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "meeting_saved_closed_caption.txt").write_text("[Alice] 10:00:00: hello\n")
    if with_merged:
        md = output_dir / f"Merged-Transcripts-{year}"
        md.mkdir(parents=True, exist_ok=True)
        (md / f"{stem}.txt").write_text("[Alice] 10:00:00: hello world\n")
    if with_cleaned:
        cd = output_dir / f"Cleaned-Transcripts-{year}"
        cd.mkdir(parents=True, exist_ok=True)
        safe = model.replace("/", "--")
        (cd / f"{stem}.{safe}.cleaned.txt").write_text("[Alice] 10:00:00: hello world (cleaned)\n")
    if with_summary:
        sd = output_dir / f"Summaries-{year}"
        sd.mkdir(parents=True, exist_ok=True)
        safe = model.replace("/", "--")
        (sd / f"{stem}.{safe}.summary.txt").write_text("summary text")
        (sd / f"{stem}.{safe}.summary.json").write_text(json.dumps({"model_output": VALID_MODEL_OUTPUT}))
    return input_dir, output_dir
