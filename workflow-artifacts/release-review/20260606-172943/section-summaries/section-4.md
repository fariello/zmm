# Section 4 Summary — Docs, Specs, Examples

Documentation is largely accurate. Verified-correct: `--max-output-tokens`
default/0-cap, `list meetings --has KIND`, augmentation order, dual .txt/.json
output, progress-on-stderr, no false `pip install zmm` PyPI claim.

Issues:
- D1 (medium): the shipped `v0.1.0` tag precedes 7 feature commits that the
  CHANGELOG folds under `## [0.1.0]` "(post-tag)" subsections; README's release
  procedure references a nonexistent `[Unreleased]` section. The tag and the
  CHANGELOG disagree. Recommend reconciling docs (and adopting an [Unreleased]
  convention) — do NOT retag without permission.
- D2 (low): real flags missing from README (`--prompt-context/-person/-correction`,
  `--summarization-source`, `--only-cleaned-transcripts`, `--ignore-model-errors`).
- D3 (low): `--summarization-source` and `--only-cleaned-transcripts` have empty
  help text.
- D4 (low): README `list missing` choices omit `summary-json`/`transcripts`.

No documented-but-unimplemented features found.
