# Tier 4: the first whole-library run

*The run: 2026-09-26 to 28. The library work that followed: to 2026-09-30. Tool at `0136930`. One M3 Max, 36 GB.*

Tier 4 is the author processing the entire corpus once, on purpose, to judge what no
assertion covers (README, "Testing"). Until this run it had never happened: the longest
thing the code had done was the 27-image sample. This report records what the run did,
what it revealed about the tool, and how its output became the author's wallpaper library.
Every figure is an aggregate. The raw logs (per-file results, the dry-run listing, the
routing report) are kept privately, because they name the author's files.

The instruments are in [`2026-09-28-tier-4-full-corpus-run/`](2026-09-28-tier-4-full-corpus-run/).
The launcher and the tools for later batches graduated into `library/`.

## What ran

The corpus was cloned into a staging directory and paperhanger was pointed at the copy,
never at the corpus itself. paperhanger *moves* each finished source into
`processing/originals/`, and the corpus sits directly above the default processing
directory; see "Originals are moved, not copied". Staged: 897 sources. That was the 894
corpus files minus one of a `.jpg`/`.png` pair with the same stem (such a pair makes two
sources write the same output, which is a refusal), plus four originals recovered from the
old pipeline's working folder.

Dry run: **897 images → 3,453 outputs, 0 rejected, estimate 24.5 h.** 55 sources are too
narrow for a desktop and produce phone outputs only.

## Pilot, then the run

**Pilot.** 16 photos chosen to cover:
- every band;
- both crop directions;
- every source format in the corpus, including a WebP under a `.jpg` name;
- the largest source;
- the largest whole 4x frame under the cap;
- one photo over the 300 Mpx cap, which takes the per-plan fallback;
- the awkward filenames.

It produced 61 outputs in **20 min 45 s against an estimate of about 23 min**, with exit 0 and every output at its planned size. A side-by-side page of old outputs against new (true 1:1) was the go/no-go gate.

**Full run.** 881 photos → 3,392 outputs, launched in tmux under `library/run_paperhanger.sh`.

| | |
|---|---|
| Wall clock | **20 h 43 m 29 s** |
| paperhanger's own estimate | 24.1 h, so real/estimate **×0.86** |
| Result | `done: 881 ok, 0 already done, 0 rejected, 0 failed`, exit 0 |
| Archive collisions (`-N` renames) | 0 |
| `.partial` files, leftover `.work/` | 0, removed |
| Output size | 5.9 GB for 3,453 outputs (the Pixelmator library it replaced: 5.3 GB) |
| Free disk | never fell by more than 13 GB; memory pressure normal throughout |

**Verification.**
- Every one of the 3,453 planned outputs exists at exactly its planned size, measured with paperhanger's own ImageIO probe.
- No unexpected files.
- All 897 sources are archived.
- A random 50 decode cleanly under ImageMagick.

## What the run revealed

**The estimator is pessimistic overall, but for two opposite reasons.**
- `report.estimate_seconds` charges every photo one whole 4x frame. It therefore **under-charges the 92 photos over the whole-frame cap**, which run the model once per plan on overlapping slices: 939 model calls where the estimate assumes about 755.
- At the same time, the 0.8 s-per-output-megapixel constant is slow for this machine. Against an estimate that charges the fallback honestly (plus 5 s per photo for decode and encode), the run finished at **×0.76**, a figure that held from 20% of the way in to the end.
- Net: the published 24 h is about 16% high here (24.1 / 20.72). Candidate follow-up, not filed: charge the fallback per plan, and let the constant be measured per machine.

**Watching a day-long run needs things the tool doesn't provide.** Each item below was found while preparing the run and is now handled in `library/run_paperhanger.sh`:
- **Output is buffered.** `print` without a flush, piped through `tee`, reaches the log in kilobyte bursts, so a watcher sees nothing for long stretches, and a hard kill loses the tail. `PYTHONUNBUFFERED=1` fixes it.
- **`tee` swallows the exit code.** `paperhanger … | tee log; echo $?` reports `tee`'s status. The real code has to be captured inside the pipeline.
- **Only Ctrl-C stops it cleanly.** SIGHUP is not handled, so closing the terminal or killing the tmux session ends paperhanger abruptly and leaves `.work/` behind. Ctrl-C runs its cleanup and prints the summary.
- **`caffeinate -u` lasts 5 seconds** unless given a timeout; `-ims -w <pid>` holds the machine awake for exactly the life of the run.

Candidate follow-ups, not filed: flush progress lines; handle SIGHUP like SIGINT.

**Nothing in the tool failed.**
- No photo failed, and there were no archive collisions after hundreds of moves. Every output has its planned dimensions (a header read for all 3,453), and a random 50 decode completely.
- The corpus's formats and dimensions still agree with `sips` on every file, including the 48 added since the census.
- The census test now compares against `sips` live instead of a pinned table, so the corpus can keep growing.

## From outputs to a library

The run's outputs were matched against the old library. Both are keyed by (source stem,
crop position, device); the old pipeline's filename grammar is parsed from `legacy/`'s
naming.

| Outcome | Outputs |
|---|---|
| Same key as an old file: replaces it | 3,029 |
| Side slice whose siblings the author had kept but not this one: dropped by that earlier curation | 100 |
| Routed by the old library's folder for that source | 25 |
| Held for review: a device the old library never had for that source (248), a new source (48), a changed crop shape (3) | 299 (all kept) |

The old library's files that the run could not reproduce were carried over unchanged, 133 in all:
- 44 whose source was no longer in the corpus;
- 86 whose source is too narrow for a desktop under the new rules;
- 3 whose crop shape changed.

3,029 were superseded, and 4 were duplicate encodings of another file. The assembled library (3,486 files) replaced the old one **add first, remove second, files only**. The folders themselves were never renamed, because the desktop's picture rotation tracks the folder, not its path. The old set was deleted only after a checksum-verified copy existed, and after the rotation was confirmed on the new files.

A first batch of 44 new downloads then went through the `library/` tools:
- library-style names: the words were proposed and approved, and the suffix was assigned and checked by `rename_batch`;
- a manual review;
- add-only copies to the library, the originals corpus and its second copy, each hash-checked.

It added 176 outputs. **The library now holds 3,662 wallpapers.** The corpus holds 942 originals: the 894, the 44 new photos and the 4 recovered ones. 941 were processed (one of the `.jpg`/`.png` pair was left out); 44 of the carried-over files come from sources no longer in the corpus.

## What tier 4 settles

The questions README's Testing section says only tier 4 can answer:
- **Whether the crops are worth keeping.** Yes, as far as this run can tell: every output held back for review, 299 of them, was kept. The 100 declined side slices were not judged here; they follow the author's earlier curation of the old library.
- **Whether HEIC 80 was the right call.** The run's 3,453 outputs take 5.9 GB where the 3,166 Pixelmator files they were measured against took 5.3 GB: 11% more space for 9% more files, which is acceptable.
- **Whether `below_target/` is useful in practice.** It is a useful label, but not a useful folder: its outputs joined the rotation like everything else, so the distinction lives in the filename's factor token.
