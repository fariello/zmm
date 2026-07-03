# 10 Validation Results

All commands run with the project venv `/home/gfariello/venv/p3.14` (Python
3.14.4), repository-native.

## Test suite
| When | Command | Result |
|---|---|---|
| Run start | `pytest tests/ -q` | 199 passed, 1 skipped (~3.8s) |
| After Batch 1 | `pytest tests/ -q` | 202 passed, 1 skipped |
| After Batch 2 | `pytest tests/ -q` | 204 passed, 1 skipped |
| After Batch 3 | `pytest tests/ -q` | 208 passed, 1 skipped |
| After Batch 4 | `pytest tests/ -q` | 208 passed, 1 skipped |
| After Batch 5 / final | `pytest tests/ -q` | 209 passed (0 skipped when package installed) |

The 1 historical skip is `test_packaging.py`'s installed-metadata test, which
runs (passes) once the package has been built/installed in the environment.

## Compile
`python -m py_compile zoom_meeting_manager.py` → OK at every batch.

## Packaging build
`python -m build` → `zmm-0.1.0-py3-none-any.whl` + `zmm-0.1.0.tar.gz` built
successfully. Wheel contains `prompts/*.txt` (incl. `examples/*.example.txt`),
`schemas/summary.json` + `schemas/__init__.py`, LICENSE, README long_description.

## Install smoke (validates the new CI build job)
Fresh venv `pip install dist/*.whl`:
- `import zoom_meeting_manager; __version__` → `0.1.0`
- `zmm --version` → `zmm 0.1.0`
- `zmm --help` → exit 0

## Schema validation
`schemas/summary.json` is valid draft-07; a generated summary payload validates
against it with 0 errors (jsonschema 4.26.0). Now covered by an automated test.

## Manual checks
- `zmm summarize merged --help` shows the new `--summarization-source` /
  `--only-cleaned-transcripts` help text.
- `_warn_insecure_base_url` warns once for `http://example.com`, never for
  https/localhost/127.0.0.1.
- CI YAML parses (`yaml.safe_load`); pyproject parses (`tomllib`).

## Not run
- Live model calls (summarize/clean against a real endpoint): intentionally not
  run in the review (cost/network). Covered by offline tests with a fake client.
- CI workflow on GitHub: not triggered (no push permitted); validated locally.
