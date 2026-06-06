"""Integration tests: write outputs, export, migrate/index, config precedence
(P3-T7, T10, T11, T12, R6)."""

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import zoom_meeting_manager as zmm  # noqa: E402
from helpers import VALID_MODEL_OUTPUT, make_meeting_tree  # noqa: E402


def _ns(**kw):
    defaults = dict(
        input_dir=None, output_dir=None, date_range=None, match=None, max=None,
        format="table", color="never", plain=True, dry_run=False, clobber=False,
        ignore_model_errors=False, yes=True, max_input_tokens=None, debug=False,
        no_context=False, resume=False, config=None,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


# ----------------------------- write_summary_outputs (P3-T7) ----------------------------- #

def test_write_summary_outputs_full(tmp_path):
    cfg = zmm.Config(output_dir=str(tmp_path))
    src = tmp_path / "src.txt"
    src.write_text("[Alice] 10:00:00: hi\n[Bob] 10:45:00: bye\n")
    rec = zmm.MeetingRecord(id="x", title="My Meeting", meeting_date="2026-03-09",
                            meeting_datetime="2026-03-09 09:00:00")
    zmm.write_summary_outputs(rec, str(src), VALID_MODEL_OUTPUT, "gpt-4o", "core:meeting_generic", cfg)
    sdir = tmp_path / "Summaries-2026"
    jpath = sdir / "src.gpt-4o.summary.json"
    tpath = sdir / "src.gpt-4o.summary.txt"
    assert jpath.is_file() and tpath.is_file()
    payload = json.loads(jpath.read_text())
    # meeting metadata computed by zmm
    assert payload["meeting"]["title"] == "My Meeting"
    assert payload["meeting"]["datetime"] == "2026-03-09 09:00:00"
    assert payload["meeting"]["duration"] == "00:45:00"  # from timestamps
    assert payload["meeting"]["source_path"] == str(src)
    # metadata fields
    assert payload["metadata"]["model"] == "gpt-4o"
    assert payload["metadata"]["prompt_label"] == "core:meeting_generic"
    assert payload["metadata"]["zmm_version"] == zmm.__version__
    assert payload["metadata"]["source_sha256"]
    # model output preserved verbatim
    assert payload["model_output"] == VALID_MODEL_OUTPUT


# ----------------------------- export aggregates (P3-T10) ----------------------------- #

def test_export_aggregates_year(tmp_path):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True, with_summary=True)
    cfg = zmm.Config(output_dir=str(output_dir))
    args = _ns(output_dir=str(output_dir), period="year")
    zmm.cmd_export(args, cfg)
    assert (output_dir / "2026-Meetings.txt").is_file()
    assert (output_dir / "2026-Transcripts.txt").is_file()
    assert (output_dir / "2026-Meeting-Summaries.txt").is_file()


def test_export_aggregates_month(tmp_path):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True)
    cfg = zmm.Config(output_dir=str(output_dir))
    args = _ns(output_dir=str(output_dir), period="month")
    zmm.cmd_export(args, cfg)
    assert (output_dir / "2026-01-Meetings.txt").is_file()


def test_export_aggregates_uses_config_default(tmp_path):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True)
    cfg = zmm.Config(output_dir=str(output_dir))
    cfg.aggregate_period = "year"
    args = _ns(output_dir=str(output_dir), period=None)  # falls back to cfg
    zmm.cmd_export(args, cfg)
    assert (output_dir / "2026-Meetings.txt").is_file()


# ----------------------------- index / write-json / migrate (P3-T11) ----------------------------- #

def test_index_writes_processing_json(tmp_path, capsys):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True)
    cfg = zmm.Config(output_dir=str(output_dir))
    args = _ns(output_dir=str(output_dir), rebuild=False)
    zmm.cmd_index(args, cfg)
    assert (output_dir / "2026-Meeting-Processing.json").is_file()


def test_index_rebuild_rewrites(tmp_path):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True)
    cfg = zmm.Config(output_dir=str(output_dir))
    # Pre-create a stale processing file
    stale = output_dir / "2026-Meeting-Processing.json"
    stale.write_text("STALE")
    args = _ns(output_dir=str(output_dir), rebuild=True)
    zmm.cmd_index(args, cfg)
    assert stale.read_text() != "STALE"  # rewritten
    assert json.loads(stale.read_text())  # valid JSON now


def test_write_json_message(tmp_path, capsys):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True)
    cfg = zmm.Config(output_dir=str(output_dir))
    zmm.cmd_write_json(_ns(output_dir=str(output_dir)), cfg)
    out = capsys.readouterr().out
    assert "processing JSON" in out


def test_migrate_reports_discovery(tmp_path, capsys):
    _, output_dir = make_meeting_tree(tmp_path, with_raw=False, with_merged=True, with_summary=True)
    cfg = zmm.Config(output_dir=str(output_dir))
    zmm.cmd_migrate(_ns(output_dir=str(output_dir)), cfg)
    out = capsys.readouterr().out
    assert "Discovered" in out
    assert "merged transcripts" in out


# ----------------------------- opencode fallback + precedence (P3-T12, R6) ----------------------------- #

def _write_opencode(tmp_path, monkeypatch, keyfile_val="ockey123", base_url="https://oc.example/v1"):
    oc = {
        "provider": {
            "myprov": {
                "name": "My Provider",
                "options": {"apiKey": keyfile_val, "baseURL": base_url},
                "models": {"prov/cheap": {"cost": {"input": 0.1, "output": 0.2}},
                           "prov/expensive": {"cost": {"input": 5.0, "output": 10.0}}},
            }
        }
    }
    ocfile = tmp_path / "opencode.json"
    ocfile.write_text(json.dumps(oc))
    monkeypatch.setattr(zmm, "OPENCODE_CONFIG", ocfile)
    return ocfile


def test_opencode_fallback_fills_missing(tmp_path, monkeypatch):
    _write_opencode(tmp_path, monkeypatch)
    cfg = zmm.Config()
    zmm._apply_opencode_fallback(cfg)
    assert cfg.api_key == "ockey123"
    assert cfg.base_url == "https://oc.example/v1"
    # cheapest model auto-selected for summary
    assert cfg.models.get("summary") == "prov/cheap"


def test_opencode_fallback_does_not_override_existing(tmp_path, monkeypatch):
    _write_opencode(tmp_path, monkeypatch)
    cfg = zmm.Config(api_key="explicit-key", base_url="https://explicit/v1")
    cfg.models["summary"] = "explicit-model"
    zmm._apply_opencode_fallback(cfg)
    # explicit values win (precedence: config > opencode)
    assert cfg.api_key == "explicit-key"
    assert cfg.base_url == "https://explicit/v1"
    assert cfg.models["summary"] == "explicit-model"


def test_config_precedence_cli_over_config(tmp_path, monkeypatch):
    # Simulate main()'s CLI->cfg copy: args.output_dir overrides cfg.output_dir
    cfg = zmm.Config(output_dir="/from/config")
    args = _ns(output_dir="/from/cli")
    # resolve_output_dir prefers args
    assert str(zmm.resolve_output_dir(args, cfg)) == "/from/cli"


def test_resolve_output_dir_falls_back_to_config():
    cfg = zmm.Config(output_dir="/from/config")
    args = _ns(output_dir=None)
    assert str(zmm.resolve_output_dir(args, cfg)) == "/from/config"


# ----------------------------- main() input validation (S2-B1) ----------------------------- #

def test_main_invalid_date_range_clean_error(tmp_path, monkeypatch, capsys):
    # 20260606-172943-S2-B1: a valid-format but out-of-range --date-range must
    # produce a clean ERROR (SystemExit) instead of an uncaught ValueError
    # traceback from deep inside a command handler.
    monkeypatch.setattr(sys, "argv",
                        ["zmm", "list", "meetings",
                         "--output-dir", str(tmp_path), "--date-range", "2026-13"])
    with pytest.raises(SystemExit) as exc:
        zmm.main()
    # SystemExit carries a string message (not a clean 0 exit).
    assert isinstance(exc.value.code, str)
    assert "invalid --date-range" in exc.value.code
    assert "2026-13" in exc.value.code
