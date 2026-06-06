# ZMM Implementation Notes

> **Historical document.** This file is a point-in-time design/planning artifact from 2026-06-05, written before zmm was extracted into its own repository. It references the old `zoom/`-prefixed paths, the `summarize_zoom_transcripts.py` script, and some prompts/config that have since been removed or relocated. It is kept for history and does **not** describe current behavior. For current docs see [../README.md](../README.md) and [../CHANGELOG.md](../CHANGELOG.md).


Date: 2026-06-05

This file accumulates questions, suggestions, implementation comments, and deferred items while implementing `zoom_meeting_manager.py` / `zmm`.

## Running Notes

- Starting with a pragmatic implementation that reuses proven parsing/merge helpers from `summarize_zoom_transcripts.py` where possible.
- The new script will be file-oriented and inventory-first. The filesystem remains the source of truth; JSON metadata is rebuildable.
- `vistab` is optional. The implementation should detect it dynamically and fall back to an internal table renderer.
- Some IPD features are broad enough to require phased implementation. The first complete pass should prioritize safe local inventory/list/report/index behavior, then model-backed features.
- Implemented `zoom/zoom_meeting_manager.py` and executable `zoom/zmm` wrapper.
- Implemented pretty table rendering for list/report commands with `vistab` support and plain/json/csv fallbacks.
- Implemented local inventory discovery for raw Zoom dirs, merged transcripts, cleaned transcripts, summaries, and summary JSON sidecars.
- Implemented `list`, `report`, `index`, `write processing-json`, `migrate legacy`, `estimate`, `extract`, `summarize`, `fix missing summaries`, `export aggregates`, `clean transcripts`, and `init config` command surfaces.
- Implemented model-call fatal error behavior, rough model operation token estimates, `--yes`, `--max-input-tokens`, and invalid JSON diagnostics.
- Implemented strict JSON summary output path and rendered `.summary.txt` output for new summaries.
- Implemented configurable prompt layers and added cleanup/extraction/prioritization prompt files.

## Deferred / Watch Items

- Confirm preferred package/install approach for a real console entry point later. For now, `zoom/zmm` can be an executable wrapper.
- JSON repair currently saves diagnostics and fails clearly; it does not perform a second repair model call.
- Additional source adapters beyond Zoom remain architectural placeholders until sample exports are available.
- The new manager reuses some legacy parsing helpers rather than fully moving all parsing code into source-adapter classes. That is acceptable for the first pass, but future cleanup should isolate adapter code.
- `vistab` input is CSV-based; JSON table output is handled internally.
- Cost estimates are rough token estimates only and do not yet multiply by provider/model pricing.
