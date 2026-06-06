"""Parser dispatch and option-handling tests (P3-T1, P3-R1, P3-R2)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import zoom_meeting_manager as zmm  # noqa: E402


def parse(argv):
    return zmm.build_parser().parse_args(argv)


# P3-T1: each command/subcommand dispatches to the expected handler.
@pytest.mark.parametrize("argv,func,extra", [
    (["list", "meetings"], "cmd_list", {"list_object": "meetings"}),
    (["list", "missing"], "cmd_list", {"list_object": "missing"}),
    (["list", "missing-merged"], "cmd_list", {"missing_kind": "merged"}),
    (["list", "missing-summaries"], "cmd_list", {"missing_kind": "summaries"}),
    (["list", "missing-raw"], "cmd_list", {"missing_kind": "raw"}),
    (["list", "models"], "cmd_list", {"list_object": "models"}),
    (["list", "prompts"], "cmd_list", {"list_object": "prompts"}),
    (["report", "status"], "cmd_report", {"report_object": "status"}),
    (["report", "counts"], "cmd_report", {"report_object": "counts"}),
    (["index"], "cmd_index", {}),
    (["migrate", "legacy"], "cmd_migrate", {}),
    (["write", "processing-json"], "cmd_write_json", {}),
    (["export", "aggregates"], "cmd_export", {}),
    (["init", "config"], "cmd_init", {}),
    (["show", "config"], "cmd_show", {}),
    (["show", "prompt"], "cmd_show_prompt", {}),
    (["estimate", "summarize"], "cmd_estimate", {"estimate_object": "summarize"}),
    (["extract", "search", "--regex", "x"], "cmd_extract", {"extract_object": "search"}),
    (["extract", "me", "items"], "cmd_extract", {"kind": "items"}),
    (["extract", "person", "items", "--person", "bob"], "cmd_extract", {"person": "bob"}),
    (["summarize", "raw"], "cmd_summarize", {"summarize_object": "raw"}),
    (["summarize", "merged"], "cmd_summarize", {"summarize_object": "merged"}),
    (["summarize", "files", "a.txt"], "cmd_summarize", {"summarize_object": "files"}),
    (["fix", "missing", "summaries"], "cmd_summarize", {"summarize_object": "merged"}),
    (["clean", "transcripts"], "cmd_clean", {}),
    (["clean", "diagnostics"], "cmd_clean_diagnostics", {}),
    (["delete", "raw"], "cmd_delete_raw", {}),
])
def test_dispatch(argv, func, extra):
    ns = parse(argv)
    assert ns.func is getattr(zmm, func)
    for k, v in extra.items():
        assert getattr(ns, k) == v


# P3-R2: migrate legacy / write processing-json route to the *correct* handlers,
# not the old cmd_index alias.
def test_migrate_routes_to_cmd_migrate():
    assert parse(["migrate", "legacy"]).func is zmm.cmd_migrate


def test_write_json_routes_to_cmd_write_json():
    assert parse(["write", "processing-json"]).func is zmm.cmd_write_json


# P3-R1: global options work BEFORE the subcommand (SUPPRESS-default fix).
def test_output_dir_before_subcommand():
    ns = parse(["--output-dir", "/tmp/x", "report", "status"])
    assert ns.output_dir == "/tmp/x"


def test_output_dir_after_subcommand():
    ns = parse(["report", "status", "--output-dir", "/tmp/y"])
    assert ns.output_dir == "/tmp/y"


def test_date_range_before_subcommand():
    ns = parse(["--date-range", "2026-06", "list", "meetings"])
    assert ns.date_range == "2026-06"


def test_format_default_is_table():
    ns = parse(["list", "meetings"])
    assert ns.format == "table"


# P3-T1: missing required subcommand exits non-zero (friendly error path).
def test_missing_subcommand_exits():
    with pytest.raises(SystemExit) as exc:
        parse(["list"])
    assert exc.value.code != 0


def test_unknown_command_exits():
    with pytest.raises(SystemExit):
        parse(["bogus"])


# P3-T1: invalid choice for show prompt --task
def test_show_prompt_task_invalid_choice():
    with pytest.raises(SystemExit):
        parse(["show", "prompt", "--task", "nope"])


def test_show_prompt_task_valid():
    ns = parse(["show", "prompt", "--task", "cleanup"])
    assert ns.task == "cleanup"


# list models flags
def test_list_models_provider_and_show_stale():
    ns = parse(["list", "models", "--provider", "uri", "--show-stale"])
    assert ns.provider == "uri"
    assert ns.show_stale is True


# summarize flags present
def test_summarize_flags():
    ns = parse(["summarize", "merged", "--no-context", "--resume", "--clobber", "--max", "5"])
    assert ns.no_context is True
    assert ns.resume is True
    assert ns.clobber is True
    assert ns.max == 5
