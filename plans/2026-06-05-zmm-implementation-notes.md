# ZMM Implementation Notes

Date: 2026-06-05

This file accumulates questions, suggestions, implementation comments, and deferred items while implementing `zoom_meeting_manager.py` / `zmm`.

## Running Notes

- Starting with a pragmatic implementation that reuses proven parsing/merge helpers from `summarize_zoom_transcripts.py` where possible.
- The new script will be file-oriented and inventory-first. The filesystem remains the source of truth; JSON metadata is rebuildable.
- `vistab` is optional. The implementation should detect it dynamically and fall back to an internal table renderer.
- Some IPD features are broad enough to require phased implementation. The first complete pass should prioritize safe local inventory/list/report/index behavior, then model-backed features.

## Deferred / Watch Items

- Confirm preferred package/install approach for a real console entry point later. For now, `zoom/zmm` can be an executable wrapper.
- Decide whether JSON repair should call a model or only do local cleanup. Initial implementation should fail clearly and save diagnostics.
- Additional source adapters beyond Zoom are architectural placeholders unless a sample export is available.
