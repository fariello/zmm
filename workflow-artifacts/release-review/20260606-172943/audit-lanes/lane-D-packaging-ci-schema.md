# Audit Lane Report

## Lane

- Lane ID: D
- Lane name: Packaging / Build / CI / Versioning / Compatibility / Schema (Section 6)
- Run ID: 20260606-172943
- Section coverage: S6
- Read-only: yes
- Created: 2026-06-06

## Scope

Lane D reviews release-readiness of the `zmm` CLI: packaging correctness
(pyproject, MANIFEST.in, wheel/sdist contents), versioning/changelog,
GitHub Actions CI, public compatibility contracts (CLI flags, json/csv,
on-disk filename conventions), and schema/data-contract validation of
`schemas/summary.json` against the code's generated summary output.

A real build was performed into a temp dir (`/tmp/zmm-build-check`, since
removed) using the project venv (`/home/gfariello/venv/p3.14/bin/python -m
build`, build 1.5.0, Python 3.14.4). No install, publish, or network
operations were performed. `jsonschema` 4.26.0 was importable in the venv and
was used to validate the schema and a golden payload. All temp files created
were cleaned up.

## Files and artifacts inspected

- `pyproject.toml` (build config, dynamic version, packages.find, package-data)
- `MANIFEST.in`
- `.github/workflows/ci.yml`
- `schemas/summary.json`, `schemas/__init__.py`
- `prompts/` (+ `__init__.py`, `examples/` + `examples/__init__.py`)
- `zoom_meeting_manager.py` (`__version__` L11; `_find_data_dir`/importlib.resources
  L44-69; `SUMMARY_REQUIRED_KEYS` L1280-1284; `validate_summary_output` L1287-1300;
  `write_summary_outputs` L3004-3043; `write_processing_json` schema_version L3172-3187;
  json/csv render L330-354; filename conventions L2902/3006/3011/3179)
- `CHANGELOG.md`, `README.md` "Releasing" (L383-401)
- `deprecated/` (git-tracked; build exclusion verified)
- `tests/test_packaging.py`, `tests/test_contract.py`, `tests/helpers.py`
- Built artifacts: `zmm-0.1.0-py3-none-any.whl`, `zmm-0.1.0.tar.gz` (temp build)
- Commands: `python -m build`, `python -m zipfile -l`, `tar tzf`, jsonschema
  `Draft7Validator`, full `pytest` on 3.14

### Built-artifact file listing (key lines)

Wheel `zmm-0.1.0-py3-none-any.whl` (`python -m zipfile -l`):

```
zoom_meeting_manager.py
prompts/__init__.py
prompts/cleanup_transcript.txt
prompts/meeting_generic.txt
prompts/output_structured_notes.txt
prompts/examples/__init__.py
prompts/examples/corrections.example.txt
prompts/examples/myself.example.txt
prompts/examples/people.example.txt
prompts/examples/style.example.txt
prompts/examples/work.example.txt
schemas/__init__.py
schemas/summary.json
zmm-0.1.0.dist-info/licenses/LICENSE
zmm-0.1.0.dist-info/METADATA
zmm-0.1.0.dist-info/entry_points.txt
zmm-0.1.0.dist-info/top_level.txt
zmm-0.1.0.dist-info/RECORD
```

Sdist `zmm-0.1.0.tar.gz` (`tar tzf`, key lines):

```
zmm-0.1.0/CHANGELOG.md
zmm-0.1.0/LICENSE
zmm-0.1.0/MANIFEST.in
zmm-0.1.0/PKG-INFO
zmm-0.1.0/README.md
zmm-0.1.0/prompts/...(all .txt incl examples/)...
zmm-0.1.0/schemas/__init__.py
zmm-0.1.0/schemas/summary.json
zmm-0.1.0/pyproject.toml
zmm-0.1.0/zoom_meeting_manager.cfg.example
zmm-0.1.0/zoom_meeting_manager.py
```

Wheel METADATA: `Version: 0.1.0`, `Requires-Python: >=3.10`,
`Description-Content-Type: text/markdown` (README is long_description),
`License-File: LICENSE`, `Development Status :: 3 - Alpha`,
classifiers 3.10–3.13, `tiktoken>=0.5` required, extras `api`/`dev` correct,
entry point `zmm = zoom_meeting_manager:main`.

`deprecated/` is git-tracked but is **absent** from both wheel and sdist
(correctly excluded — single-module + packages.find scope).

## Candidate findings

| Candidate ID | Type | Severity | Title | Evidence | Affected files | Recommended action |
|---|---|---|---|---|---|---|
| P1 | P | Info/Pass | Wheel & sdist correctly ship all bundled data + LICENSE | Wheel listing (above) contains `prompts/*.txt` incl. `prompts/examples/*.example.txt`, `schemas/summary.json`, `dist-info/licenses/LICENSE`; sdist contains LICENSE/README/CHANGELOG/cfg.example | pyproject.toml, MANIFEST.in | None — packaging is correct. Record as a passing check. |
| CI1 | CI | Low | CI matrix omits Python 3.14 (the repo dev runtime) | `.github/workflows/ci.yml:13` matrix `["3.10","3.11","3.12","3.13"]`; dev venv is 3.14.4; full suite **199 passed, 1 skipped** on 3.14 (verified this lane) | .github/workflows/ci.yml, pyproject.toml:24-31 | Add `"3.14"` to matrix and a `Programming Language :: Python :: 3.14` classifier. Low risk; already green locally. |
| CI2 | CI | Low | CI installs deps manually, not from the package (`.[dev]`) | `ci.yml:21` `pip install --upgrade pip pytest pytest-cov tiktoken` — bypasses pyproject; never installs `zmm` itself | .github/workflows/ci.yml | Switch to `pip install .[dev]` (or `-e .[dev]`). Catches packaging/entry-point regressions, exercises the importlib.resources install path, and keeps dep versions single-sourced. |
| CI3 | CI | Low | CI does not build or import-check the wheel | `ci.yml` has compile + pytest only; no `python -m build`; `test_version_matches_installed_metadata_if_installed` (test_packaging.py:56-64) is **skipped** when not installed | .github/workflows/ci.yml, tests/test_packaging.py | Add a build job: `python -m build` + `python -m zipfile -l dist/*.whl` and/or `pip install dist/*.whl` then `zmm --version`. Would assert data files ship and the installed-metadata test runs (not skips). |
| CI4 | CI | Info/Pass | No publish/deploy/network steps in CI | `ci.yml` full file (25 lines): checkout, setup-python, pip install, py_compile, pytest only | .github/workflows/ci.yml | None — correct for a pre-publish project. |
| SCH1 | SCH | Info/Pass | summary.json is valid draft-07 and code output conforms | `jsonschema.Draft7Validator.check_schema` passed; golden full payload (meeting+model_output+metadata as written by `write_summary_outputs` L3034-3038) validated with **0 errors**, incl. `priority: null` case | schemas/summary.json, zoom_meeting_manager.py:3004-3043 | None — schema/data contract is sound. |
| SCH2 | SCH | Info/Pass | SUMMARY_REQUIRED_KEYS matches schema model_output.required | `zoom_meeting_manager.py:1280-1284` list == `schema.properties.model_output.required` (schemas/summary.json:34); guarded by `tests/test_contract.py:20-25` | zoom_meeting_manager.py, schemas/summary.json, tests/test_contract.py | None — no drift; drift-guard test exists. |
| SCH3 | SCH | Low | Runtime validation is key-presence only, not schema-based; external/user JSON is never schema-validated | `validate_summary_output` (L1287-1300) only checks top-level key presence of `SUMMARY_REQUIRED_KEYS` against the `model_output` dict (called at L2864 on raw model `data`); ignores types, enums, nested `required` (decisions/action_items), and `maxLength`. No `jsonschema` use anywhere in code (grep). | zoom_meeting_manager.py:1287-1300, 2864 | Acceptable for a dependency-free non-fatal warning. Optionally note in docs that validation is best-effort key-presence. Full jsonschema validation would require adding `jsonschema` as an (optional) dep — not recommended for 0.1.0. |
| SCH4 | SCH | Low | No CI/test check validates schema syntax or a golden sample against the schema | No jsonschema in deps; `test_contract.py:20-25` only compares required-key lists, not full validation | tests/, .github/workflows/ci.yml | Add a dev-only test (jsonschema is in `[dev]`? — it is NOT; see DEP1) that runs `Draft7Validator.check_schema` + validates `helpers.VALID_MODEL_OUTPUT` wrapped payload. Cheap forward-compat guard. |
| DEP1 | DEP | Low | `jsonschema` not declared in `[dev]` extra although useful for schema tests | pyproject.toml:35 `dev = ["openai>=1.0","pytest>=7.0","pytest-cov>=4.0","build>=1.0"]` — no jsonschema; it happened to be importable in this venv | pyproject.toml | If SCH4 is adopted, add `jsonschema>=4.0` to `[dev]`. Otherwise no action. |
| O1 | O | Info | schema_version / forward-compat fields present and consistent | Processing JSON `schema_version: 1` (L3181); summary metadata carries `zmm_version` (L3031) and `created_at`; on-disk names stable (`Merged-Transcripts-{year}` L2673, `Summaries-{year}` L2902/3006, `*.summary.json` L3011, `{year}-Meeting-Processing.json` L3179) and asserted across tests | zoom_meeting_manager.py, tests/ | None — versioning/compat fields are in place. |
| O2 | O | Info | README "Releasing" mentions `python -m build` but `[Unreleased]` workflow note is stale | README.md:391 says "Move CHANGELOG [Unreleased] into a dated section", but CHANGELOG has no `[Unreleased]` heading (already dated `[0.1.0] - 2026-06-06`, L10) | README.md:383-401, CHANGELOG.md:10 | Minor doc nit: add an `[Unreleased]` section convention or soften the wording. Cosmetic. |

## Candidate actions

| Candidate action | Source candidate IDs | Description | Risk | Validation idea |
|---|---|---|---|---|
| Add Python 3.14 to CI matrix + classifier | CI1 | Append `"3.14"` to `ci.yml` matrix and add `Programming Language :: Python :: 3.14` classifier | Very low — suite already passes on 3.14 (verified) | Re-run pytest on 3.14 (done: 199 passed, 1 skipped) |
| Install package from pyproject in CI | CI2 | Replace manual `pip install ... pytest pytest-cov tiktoken` with `pip install .[dev]` | Low — may surface latent packaging issues (intended) | Confirm `zmm --version` works post-install; tiktoken/openai resolve via extras |
| Add build + installed-import job to CI | CI3 | Add a job running `python -m build` and installing the wheel; assert data files present and `zmm --version` | Low | Listing matches the verified wheel contents above; installed-metadata test stops skipping |
| Add schema-validation test (dev) | SCH4, DEP1 | Add a pytest using `jsonschema` to check schema syntax + validate a golden payload; declare `jsonschema` in `[dev]` | Low | Mirrors this lane's successful validation (0 errors) |
| Doc/clarity tidy | SCH3, O2 | Note that runtime summary validation is best-effort key-presence; align README releasing text with CHANGELOG `[Unreleased]` convention | Negligible | Doc review |

## Non-applicable checks

- **Publish/deploy/secrets review**: no publish workflow exists (CI4); twine
  upload is documented only as an optional manual maintainer step
  (README.md:399-400). Nothing to audit for leaked credentials.
- **Native/compiled extension packaging**: pure-Python single module; n/a.
- **Lockfile / pinned transitive deps**: project uses loose floors
  (`tiktoken>=0.5`, etc.); appropriate for a library/CLI, not pinned — n/a as a defect.
- **Multiple-package namespace conflicts**: single module + two data packages;
  `top_level.txt` = `prompts/schemas/zoom_meeting_manager`. Data-package names
  `prompts` and `schemas` are generic top-level names that could in theory
  collide with other installed distributions sharing those names — noted as a
  theoretical concern, not observed here.

## Uncertainties

- **`prompts`/`schemas` as top-level wheel packages** (top_level.txt) use very
  generic names. This is intentional (importlib.resources `resources.files("prompts")`)
  and works, but two unrelated installed packages both named `prompts`/`schemas`
  would conflict in site-packages. Not a defect for current scope; flag for
  main-agent judgment on whether to namespace (e.g. `zmm_prompts`) pre-1.0.
- **SCH3 severity**: whether best-effort key-presence validation is "good
  enough" for the data contract is a product call. The schema is richer than
  the runtime check; downstream consumers relying on enums/types are not
  guarded at write time. Left to synthesis.

## Handoff notes

- **Packaging: PASS.** A real build produced a wheel + sdist that contain all
  required runtime data (`prompts/*.txt` incl. `examples/`, `schemas/summary.json`),
  the LICENSE, and README as Markdown long_description. `deprecated/` is tracked
  in git but correctly excluded from artifacts. Version is single-sourced
  (`__version__ = "0.1.0"` → dynamic), consistent in METADATA, classifiers say
  Alpha, requires-python `>=3.10`. No packaging defects found.
- **Schema: PASS, no drift.** `schemas/summary.json` is valid draft-07; the
  exact payload `write_summary_outputs` emits validates with 0 errors;
  `SUMMARY_REQUIRED_KEYS` == schema `model_output.required` and is drift-guarded
  by `test_contract.py`. Only soft items: runtime validation is key-presence
  only (SCH3) and there's no CI schema/golden-sample check (SCH4/DEP1).
- **CI: low-risk improvements only** — add 3.14 (verified green: 199 passed,
  1 skipped), install from `.[dev]` instead of manual deps, and add a
  build/install job. No publish steps exist (correct).
- For dedup: P1/SCH1/SCH2/CI4/O1 are passing-state confirmations (may fold into
  a "release readiness OK" note). Actionable candidates are CI1, CI2, CI3, SCH3,
  SCH4/DEP1, and cosmetic O2.
- All temp build artifacts under `/tmp/zmm-build-check` and `/tmp/zmm-build-src`
  were removed. No tracked files were modified by this lane.
