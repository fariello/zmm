# Deprecation Candidates

| ID | Candidate | Classification | Evidence | Decision |
|---|---|---|---|---|
| 20260606-172943-S6-DEP1 | `deprecated/summarize_zoom_transcripts.py` + `.cfg.example` | Probably keep (provenance) | Git-tracked predecessor; NOT imported by `zoom_meeting_manager.py`; lane D confirmed it is excluded from both wheel and sdist. | Keep for history; no action. It is harmless and not shipped. Could be removed in a future cleanup if desired. |
| (note) | `plans/*.md` | Probably keep | Historical implementation planning notes; tracked. Not referenced by code. | Keep; low clutter. Marked historical in docs already (lane C). |
| (note) | `sessions/` | N/A | Gitignored scratch (session log). | No action. |
| (note) | `tmp/tmp.cfg` | N/A | `tmp/` is gitignored. | No action. |
| (note) | Legacy config fallback (`zoom_meeting_manager.cfg`) | Keep | Code still supports a legacy config path/key; documented in README. Removing would break existing users. | Keep (public contract). |

No code paths were found to be unreachable or superseded within the shipped
module. The single-module structure has no dead exports. The `--prompt-context/
-person/-correction` flags are redundant (collapse candidate U1) but not
deprecated — they work and are documented behavior; deferred to the user.
