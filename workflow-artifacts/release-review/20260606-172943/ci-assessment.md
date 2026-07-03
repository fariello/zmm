# CI Assessment

## Current state
`.github/workflows/ci.yml` exists:
- Triggers: push to master/main, pull_request.
- Job `test`: matrix Python 3.10–3.13 on ubuntu-latest.
- Steps: checkout → setup-python → `pip install --upgrade pip pytest pytest-cov tiktoken` → `python -m py_compile zoom_meeting_manager.py` → `pytest tests/ -v --cov`.
- No publish/deploy/upload steps (correct and safe).

## Findings
| ID | Severity | Issue |
|---|---|---|
| 20260606-172943-S6-CI1 | medium | Matrix omits Python 3.14, which is the version the repo is actually developed/run on (suite passes on 3.14, verified by lanes A/B/D). CI does not cover the runtime in use. |
| 20260606-172943-S6-CI2 | medium | Dependencies installed manually (`pip install pytest pytest-cov tiktoken`) rather than `pip install .[dev]`. The package itself is never installed, so packaging metadata, the `zmm` entry point, the data-package resolution (`prompts/`, `schemas/`), and the optional-`openai` install path are never exercised in CI. |
| 20260606-172943-S6-CI3 | low | CI never builds or installs the wheel; `test_packaging.py` skips its installed-metadata test in an editable/uninstalled checkout. Packaging regressions (e.g. data not shipped) could pass CI. |

## Recommended changes (low risk, no secrets, no publish)
1. Add `"3.14"` to the matrix and to pyproject classifiers (CI1).
2. Install via `pip install .[dev]` so tests run against the installed package and exercise the entry point and optional extras (CI2). Keep `tiktoken` covered by the base dep.
3. Add a separate, cheap `build` job: `python -m build` then `pip install dist/*.whl` in a fresh venv and `python -c "import zoom_meeting_manager; print(zoom_meeting_manager.__version__)"` + `zmm --version` to validate packaging end-to-end (CI3).

All recommendations use repository-native commands, add no new secrets, and do
not publish/deploy. They materially improve release readiness (catch packaging
and version-compat regressions). Implemented in Section 7 — see
`10-validation-results.md` and `07-commits.md`.

## Not recommended
- Adding linters/formatters (black/ruff/mypy) now: would introduce churn and new
  dev deps without clear request; the repo has no existing lint config. Recorded
  as optional future work, not implemented (avoid cosmetic churn per protocol).
