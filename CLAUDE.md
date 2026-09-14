# CLAUDE.md

## What this is

paperhanger turns a folder of images into desktop and phone wallpapers on macOS: measure, crop into thirds where useful, ML-upscale only when the source is too small, resize to the exact target, export HEIC. It replaces a set of zsh scripts that depended on Pixelmator Pro and ImageMagick.

**All the imaging is CoreGraphics and ImageIO, called in-process through `ctypes`.** `paperhanger/_cg.py` is the only module that touches `ctypes`; `paperhanger/imaging.py` is the only one that touches images. The tool spawns exactly one subprocess, `upscayl-bin`, and nothing else. It used to drive `/usr/bin/sips` for measuring, cropping, resampling, encoding and colour conversion, and that is gone — but the word still appears about ninety times in `paperhanger/`, always as the measurement a construction was accepted on and never as something the code runs. The **seven** defects that motivated the move are `docs/research/2026-09-11-replacing-pixelmator-and-imagemagick.md` section 12, and their numbering ("fact 6") is referenced from the code. That section lists an eighth, which motivated nothing: it was found *during* the migration, in Task 3, and it turned out never to have been a `sips` defect at all — it is ImageIO's, which means it is still ours. Two tasks were spent correcting that attribution, so do not re-merge the eighth into the seven.

## Status

Implemented and reviewed. `paperhanger/` holds the working tool (classification, cropping, execution, CLI, reporting) with a full test suite under `tests/`. All sixteen planned tasks (Task 0 through Task 15) are complete, Tier 3 included, and the imaging layer has since been moved off `sips` onto CoreGraphics in a further eight tasks. **750 tests pass across all three tiers — 723 in tier 1 (~1m40s), 21 against the real corpus (~6m), 6 against the real upscaler (~41m).**

The differential gates that carried that migration are retired: each operation was accepted on a comparison against the `sips` implementation it replaced, over generated fixtures and over the 27-image sample, and the last run of all four was green at 116 tier-1 comparisons plus 8 over the corpus. Those modules are deleted; the 70 assertions in them that were about *our* behaviour rather than about `sips` moved into `tests/test_imaging.py` and `tests/test_cg.py`.

Tier 3's open question is settled. Whether enlarging a whole frame and then cutting it differs from cutting first was measured against ground-truth original pixels, not by comparing the two methods to each other — 56 comparisons over two window sizes and both slice positions. They are interchangeable, so section 7 keeps the whole-frame path and a full run stays near 24 hours rather than 37. Section 7 carries the numbers.

See `README.md` for install, usage, output layout, and the testing tiers. The full 894-image corpus run (tier 4) is the author's acceptance pass and is not a gate on "implemented".

## Read first

1. `docs/research/2026-09-11-replacing-pixelmator-and-imagemagick.md` — what was established before design began: the chosen upscaler and its limits, the `sips` findings, the legacy behaviors to preserve or change, test evidence with provenance hashes, and the open design questions.
2. `legacy/` — the scripts being replaced. Reference only. Never extend or "fix" them; the spec cites their behavior.

## Conventions

- Design specs go to `docs/superpowers/specs/`, implementation plans to `docs/superpowers/plans/`, per the superpowers skills.
- `.superpowers/` and `.worktrees/` are git-ignored scratch.
- Keep the wallpaper images themselves out of this repo. Test fixtures should be tiny.
