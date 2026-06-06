# ZMM TODO

## Active

- [ ] Implement `auto_clean_before_summarize`: when true, automatically run cleanup model on merged transcripts before summarizing (if no cleaned version exists)
- [ ] Implement `aggregate_period` config default: use config value as fallback for `zmm export aggregates --period` (allow only auto, year, month)
- [ ] Remove dead config fields: `source`, `no_temperature`, `include_all_model_summaries`

## Future

- [ ] Per-task temperature control: `[temperature]` section with `summary` and `cleanup` keys; blank or "none" = don't send parameter (default for modern models)
- [ ] Source adapters beyond Zoom: generic_txt, teams, otter, vtt, srt
- [ ] Batch API support for bulk summarization (`--batch` flag using OpenAI batch API)
- [ ] `zmm validate` command for summary consistency checks
- [ ] pip install / PyPI packaging and console entry point
