<!-- nav -->
[README](README.md) · [Changelog](CHANGELOG.md) · **TODO** · [License](LICENSE)

# ZMM TODO

## Active

(none — see Future)

## Future

- [ ] Per-task temperature control: `[temperature]` section with `summary` and `cleanup` keys; blank or "none" = don't send parameter (default for modern models)
- [ ] Source adapters beyond Zoom: generic_txt, teams, otter, vtt, srt
- [ ] Batch API support for bulk summarization (`--batch` flag using OpenAI batch API)
- [ ] `zmm validate` command for summary consistency checks
- [ ] pip install / PyPI packaging and console entry point
- [ ] (P5-M2) Split the single `zoom_meeting_manager.py` (~3100 lines) into a
      package (e.g. `zmm/config.py`, `zmm/inventory.py`, `zmm/prompts.py`,
      `zmm/model.py`, `zmm/commands.py`, `zmm/cli.py`). The file is well
      sectioned with banner comments that map cleanly to modules. Deferred:
      high churn / regression risk for a release-hardening pass; do as a
      dedicated refactor with the full test suite as a safety net.

---

[README](README.md) · [Changelog](CHANGELOG.md) · [Back to top](#zmm-todo) · [License](LICENSE)
