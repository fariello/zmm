"""Packaging / data-file shipping guards (P6-R4, P6-R5)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import zoom_meeting_manager as zmm  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


# P6-R4: the bundled data the tool depends on must resolve and exist.
def test_prompts_dir_resolves_and_has_core_prompts():
    assert zmm.PROMPTS_DIR.is_dir(), f"PROMPTS_DIR missing: {zmm.PROMPTS_DIR}"
    for core in ("meeting_generic", "output_structured_notes", "cleanup_transcript"):
        assert (zmm.PROMPTS_DIR / f"{core}.txt").is_file(), f"missing core prompt {core}"


def test_core_prompts_load():
    # These are required for summarize/clean to work at all.
    assert zmm.load_prompt("meeting_generic")
    assert zmm.load_prompt("output_structured_notes")
    assert zmm.load_prompt("cleanup_transcript")


def test_schemas_dir_resolves():
    assert zmm.SCHEMAS_DIR.is_dir(), f"SCHEMAS_DIR missing: {zmm.SCHEMAS_DIR}"
    assert (zmm.SCHEMAS_DIR / "summary.json").is_file()


def test_data_dirs_are_importable_packages():
    # __init__.py presence is what makes setuptools ship them in the wheel.
    assert (REPO / "prompts" / "__init__.py").is_file()
    assert (REPO / "prompts" / "examples" / "__init__.py").is_file()
    assert (REPO / "schemas" / "__init__.py").is_file()


def test_manifest_includes_data():
    manifest = (REPO / "MANIFEST.in").read_text()
    assert "recursive-include prompts" in manifest
    assert "recursive-include schemas" in manifest


# P6-R5: version single-source — pyproject reads from __version__ dynamically.
def test_pyproject_version_is_dynamic_from_module():
    pyproject = (REPO / "pyproject.toml").read_text()
    assert 'dynamic = ["version"]' in pyproject
    assert 'attr = "zoom_meeting_manager.__version__"' in pyproject
    # And there is no competing hardcoded version line.
    assert '\nversion = "' not in pyproject


def test_version_matches_installed_metadata_if_installed():
    # When zmm is installed (wheel/editable with metadata), the distribution
    # version must equal the module __version__.
    try:
        import importlib.metadata as md
        installed = md.version("zmm")
    except Exception:
        pytest.skip("zmm not installed with metadata in this environment")
    assert installed == zmm.__version__
