# Tier-4 instruments

The tools behind `docs/research/2026-09-28-tier-4-full-corpus-run.md`. They are not part of
the shipped tool, and nothing in `paperhanger/` or `library/` imports them. They are kept so
that the report's numbers can be traced back to how they were produced, the way
`coregraphics-preflight/` does for its document.

Run them with the project's interpreter from the repository root, e.g.
`uv run python docs/research/2026-09-28-tier-4-full-corpus-run/progress.py --help`.
`_instruments.py` resolves the repository from its own location. Library paths come from the
same environment settings as `library/` (see `library/_library.py`). No path, filename or
host is written into these files, and the raw run logs are not in the repository.

| script | what it did in the run |
|---|---|
| `_instruments.py` | shared: builds paperhanger's plan in-process, charges the per-plan fallback honestly (`model_seconds`), reads an existing library under either naming |
| `pick_pilot.py` | chose the 16-photo pilot: one photo per band, crop direction, source format and edge case, plus typical photos for the comparison. Author-known edge cases come from an uncommitted `--named` file |
| `compare_page.py` | built the pilot's old-against-new comparison pages (one pair per page, true 1:1, scroll synced by fraction, A/B flip) |
| `progress.py` | reported a live run in estimated seconds rather than photos, because paperhanger runs the no-model photos first |
| `watch_run.sh` | turned `progress.py` into event lines for a watcher: 10% milestones, failures, stalls, low disk, exit |
| `assemble.py` | routed the run's outputs, and the old library's irreplaceable files, into the new layout, holding uncertain ones for review. Filename aliases come from an uncommitted `--aliases` file |

The launcher the run used, and the tools that add later batches to the library, graduated
into `library/`.
