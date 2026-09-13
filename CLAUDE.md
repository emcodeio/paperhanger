# CLAUDE.md

## What this is

paperhanger turns a folder of images into desktop and phone wallpapers on macOS: measure, crop into thirds where useful, ML-upscale only when the source is too small, resize to the exact target, export HEIC. It replaces a set of zsh scripts that depended on Pixelmator Pro and ImageMagick.

## Status

Implemented and reviewed. `paperhanger/` holds the working tool (classification, cropping, execution, CLI, reporting) with a full test suite under `tests/`. All sixteen planned tasks (Task 0 through Task 15) are complete, Tier 3 included: 551 tests pass across all three tiers — 526 in tier 1, 19 against the real corpus, 6 against the real upscaler.

Tier 3's open question is settled. Whether enlarging a whole frame and then cutting it differs from cutting first was measured against ground-truth original pixels, not by comparing the two methods to each other — 56 comparisons over two window sizes and both slice positions. They are interchangeable, so section 7 keeps the whole-frame path and a full run stays near 24 hours rather than 37. Section 7 carries the numbers.

See `README.md` for install, usage, output layout, and the testing tiers. The full 894-image corpus run (tier 4) is the author's acceptance pass and is not a gate on "implemented".

## Read first

1. `docs/research/2026-09-11-replacing-pixelmator-and-imagemagick.md` — what was established before design began: the chosen upscaler and its limits, the `sips` findings, the legacy behaviors to preserve or change, test evidence with provenance hashes, and the open design questions.
2. `legacy/` — the scripts being replaced. Reference only. Never extend or "fix" them; the spec cites their behavior.

## Conventions

- Design specs go to `docs/superpowers/specs/`, implementation plans to `docs/superpowers/plans/`, per the superpowers skills.
- `.superpowers/` and `.worktrees/` are git-ignored scratch.
- Keep the wallpaper images themselves out of this repo. Test fixtures should be tiny.
