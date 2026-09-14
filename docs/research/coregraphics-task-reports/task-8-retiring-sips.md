# Task 8 report: `sips` is gone, and its findings move to research

## Status

**DONE_WITH_CONCERNS** — everything in the brief is implemented and all three tiers pass. The
concerns are all *findings about the brief and the rulings*, not unfinished work: three of the
items I was told to correct did not exist in the tree as described, and two of the four tests
the brief named as rescues were already covered, more strongly, by tests that were already
there. Details in "Findings that contradict the brief" below.

## Step 1: the differential's last run

Both halves, run before anything was deleted.

```
uv run pytest tests/test_crop_differential.py tests/test_resize_differential.py \
    tests/test_encode_differential.py tests/test_normalize_differential.py -v -m corpus

collected 124 items / 116 deselected / 8 selected
tests/test_crop_differential.py::test_crop_matches_sips_over_the_sample          PASSED
tests/test_crop_differential.py::test_the_corpus_control_is_not_vacuous          PASSED
tests/test_resize_differential.py::test_resize_matches_sips_over_the_sample      PASSED
tests/test_resize_differential.py::test_the_corpus_comparison_is_not_vacuous     PASSED
tests/test_encode_differential.py::test_encode_matches_sips_over_the_sample      PASSED
tests/test_encode_differential.py::test_the_corpus_comparison_is_not_vacuous     PASSED
tests/test_normalize_differential.py::test_normalize_matches_sips_over_the_sample PASSED
tests/test_normalize_differential.py::test_the_corpus_comparison_is_not_vacuous  PASSED
================ 8 passed, 116 deselected in 452.46s (0:07:32) =================
```

The brief's command deselects the 116 tier-1 comparisons, which are the bulk of the gate, so I
ran those too:

```
uv run pytest tests/test_crop_differential.py tests/test_resize_differential.py \
    tests/test_encode_differential.py tests/test_normalize_differential.py
====================== 116 passed, 8 deselected in 35.96s ======================
```

124 of 124 green at the moment of deletion.

## The classification, test by test

Every test in the four modules was read and classified. **124 tests: 54 deleted, 70 moved.**

| module | total | deleted | moved |
|---|---|---|---|
| `test_crop_differential.py` | 17 | 12 | 5 |
| `test_resize_differential.py` | 27 | 10 | 17 |
| `test_encode_differential.py` | 40 | 21 | 19 |
| `test_normalize_differential.py` | 40 | 11 | 29 |
| **total** | **124** | **54** | **70** |

All 8 corpus-marked tests are in the delete column, so **46 of the deletions are tier-1** and all
70 moves are tier-1.

### The arithmetic

```
tier 1 before   769
  - deleted      46   (116 tier-1 differential tests, minus the 70 moved)
  = 723
tier 1 after    723   (measured)

test_imaging.py   66 -> 118   (+52)
test_cg.py        63 ->  81   (+18)
                            = +70 moved, and no test moved twice
```

The moved tests are counted in both the "before" and "after" totals because they were already
collected under the old modules, so the only change to the total is the 46 deletions. An AST pass
over both destination files confirms **no duplicate top-level name**, so nothing was silently
shadowed on arrival.

### What went where, and why

**Deleted — about `sips`.** A test asserting what the old tool did, or that old and new agree,
has nothing left to say. That is: the four whole-corpus gates and their four vacuity guards; the
four tier-1 differential comparisons and their `test_that_comparison_can_fail` guards; every
`test_*_matches_sips*`; the reference's own canaries (`test_the_reference_still_needs_its_pad`,
`test_the_gradient_would_notice_a_centred_crop`,
`test_the_reference_loses_the_depth_on_either_branch`,
`test_the_harness_sees_the_pinned_difference`); the EXIF and ICC whole-file comparisons
(`test_an_icc_tagged_source_encodes_byte_identically`,
`test_the_source_exif_is_what_the_two_sides_disagree_about`,
`test_the_picture_under_the_metadata_is_identical`,
`test_a_baseline_source_stays_byte_identical`); and the four tests guarding
`_describe_encode`/`_heif_picture`/`_jpeg_*`, which were guards on the corpus gate's accounting
rules and go with the gate.

**Moved — about us.** 70 test instances, in reduced form where a test was half about each. The
notable ones, beyond the brief's four:

- `test_a_region_decode_of_a_big_baseline_jpeg_is_not_the_frame_decode` → `test_imaging.py`.
  **This is the single most important rescue in the task and the brief does not name it.** Its
  own docstring says so: it is the only test in the suite making an ABSOLUTE claim about a crop
  of a JPEG below the 1,000,000-pixel threshold, and it was the only thing in 647 tests that
  caught an injected one-row origin error keyed on `Path(source).suffix != ".png"` — the corpus
  gate went green on it. I dropped the `who="sips"` half and re-instrumented the rest with no
  `sips` anywhere: our own JPEG encoder writes the fixture and `_cg.load` + `_cg.write_png`
  supplies the whole-frame decode. Re-measured, it reproduces the phenomenon exactly — 1000×1000
  and 1250×800 (both 1,000,000 px) give 0 differing samples, 1250×801 (1,001,250 px) gives
  86,413 with a largest delta of 79, against the recorded 86,423 for CoreGraphics on a
  `sips`-written JPEG.
- `test_the_shipped_defaults_are_the_ones_that_were_measured` → `test_imaging.py`. The
  `heic 85 == heic 80` guard on `formats.py`'s documented reason for the default, as flagged.
- `test_forcing_the_draw_at_identity_is_what_the_skip_avoids` (3 rows) → `test_cg.py`. Was
  `test_drawing_at_identity_is_what_diverged`, which required the forced 1:1 draw to disagree
  with `sips` by a measured amount. Reframed against the **skip's own output**, which the retired
  differential had established is byte-identical to `sips`' for these fixtures. Re-measured, the
  three rows reproduce to the digit: rgba (120000, 1), grey+alpha (60400, 1), colour-key
  (120000, 255). So the claim the skip is a *correction* rather than an optimisation survives
  with no reference implementation at all.
- `test_a_source_no_context_accepts_is_refused_when_it_must_be_drawn` +
  `test_the_same_source_resizes_at_identity` (2 rows each) → `test_cg.py`. **CMYK coverage
  existed nowhere else** — `cmyk_fixture`'s only two consumers were this module and
  `test_normalize_differential.py`, so deleting both would have left it unused. The identity
  half was a pure `sips` comparison and is reframed as an absolute assertion (it writes a
  readable PNG at the source's dimensions rather than raising), which is what made the refusal an
  asymmetry rather than a second wrong answer.
- `test_our_jpeg_does_not_carry_the_source_exif` → `test_imaging.py`. Was
  `test_a_jpeg_loses_the_source_exif_too`. The `sips` half is gone; what remains is that our
  encoder drops the source's Orientation, with a positive control (plant a `0x0112` tag in our
  own synthesised APP1 and the reader finds it) so the `is None` cannot pass vacuously. Measured
  first: our JPEG does carry an APP1 Exif of its own, 78 bytes with one IFD0 entry, so the claim
  is "not the source's tags" rather than "no APP1".
- `test_the_itu_profiles_follow_the_curve_in_the_profile` (2 rows) → `test_imaging.py`. The
  BT.709 finding the ruling named as evidence to preserve. Only the "we match the profile's own
  parametric curve" half survives; the "and `sips` does not" assertion is gone. The expectation
  is still computed from the ICC file's own `rTRC`, so it is not a comparison between
  implementations.
- The two normalize failure tests collided by name with existing `test_imaging.py` tests for
  `resize_and_encode` and are renamed (`..._for_normalize_output`,
  `test_an_unremovable_stale_normalize_output_...`). Without the rename one would have silently
  shadowed the other and a test would have been lost rather than moved.

## Ruling 1: the `sips` grep, narrowed

**`grep -rn "sips" paperhanger/` does not return nothing, and the acceptance criterion as
literally written is not met.** Per the ruling, the criterion is read as *executable and
normative* references.

| | before | after |
|---|---|---|
| code references | **1** (`SIPS = "/usr/bin/sips"`, `imaging.py:184`) | **0** |
| prose references (docstrings, comments) | 119 | 91 |
| total | 120 | 91 |

`grep -rn "SIPS" paperhanger/` returns nothing. The one code reference is deleted, and with it
`imaging.py`'s seven-facts docstring (167 lines → 45).

The 91 remaining are provenance, in three classes:

- **`_cg.py` (53) and `imaging.py` (28)** — per-function measured comparisons: byte-identity
  results, memory figures, the BT.709 curve, the region-decode phenomenon. Kept per the ruling.
  `_cg.py` now opens with a header saying why the word appears in a project that does not run it.
- **`cli.py` (4)** — the deleted pre-flight and the absent `doctor` line, already written in past
  tense as an explanation of a deliberate absence. **Kept unchanged**; they are the
  historical-but-still-explanatory class.
- **`execute.py` (5) and `bands.py` (1)** — what is left after the rewrite below.

`imaging._run` and `produces=`, and `import subprocess`, are untouched. `upscale` is still their
only caller.

## Ruling 2: the stale live-reason comments

`execute.py` went from 12 mentions to 5, `bands.py` from 2 to 1. Nine comments judged in
`execute.py`, one docstring in `bands.py`. **No behaviour changed** — comments and docstrings
only.

Rewritten (stale live reason, present tense, keyed on a tool that is gone):

1. `_refuse_to_enlarge` — "`sips` is the one tool here that would happily do it". It is not, and
   the guard is not weaker for it: I measured the current resampler enlarging 100×80 to 400×320
   at no error, and said so in the comment.
2. `_verify_dimensions` — "Seven distinct ways sips returns plausible wrong output at exit 0 have
   now been measured … rather than trusting the eighth to announce itself". Retensed, and the
   class is named for the current code (an identity skip, a derived axis, a region the caller did
   not ask for) rather than implied to have left with the tool.
3. The two-checks list — "refuses to ask sips to enlarge … the only time sips could".
4. `render`'s "Never fused with the crop above: sips applies a resample against the PRE-crop
   dimensions" — the ruling's own example. Rewritten to say the separation is structural now
   (`_cg` offers no fused operation) *and* to keep the measured origin, which is research fact 2.
5. "a transient sips failure" → "a transient imaging failure".
6. `upscale_and_crop`'s ordering justification — "safe only because sips carries the source's
   profile into the crop untouched". Re-attributed to the current crop, which
   `test_imaging.test_crop_carries_the_source_profile_through` pins, with the 144-out-of-255
   measurement kept and the reason stated for both tools.
7-9. Three "a photo sips could not read" → "a photo the decoder could not read", in `_unfinished`
   and `run_and_archive`.
10. `bands.py`'s "computed here rather than derived by sips … sips is told both numbers". The
   underlying rule outlived the tool — the plan defines the filename, so both axes are computed
   in one place — and the `--resampleWidth` origin is kept as history.

Kept as correctly-tensed history: `execute.py`'s module docstring on the pad that used to double
peak disk, and the four `cli.py` comments.

**I found no comment whose claim is now wrong in a way that implies the code is wrong.**

## Ruling 3: the two Task 7 findings

### `pict` and `heif` — corrected, but the claim was not where the ruling said

There is no string `pict` anywhere in the repository, and never has been (`git log -S` finds
none). `_cg.py:205` is the last line of the `SOURCE_FORMATS` preamble, whose claim is *"camera
raw and the other formats no fixture here can produce would each be a guess"* — the same claim in
different words, so I took the ruling as applying to it and reproduced the measurement before
writing anything down:

| written as | old `sips -g format` | this table | dimensions |
|---|---|---|---|
| `pict` | `pict` | `unknown` | correct |
| `.heif` | `heif` | `unknown` | correct |

Both via the `magick` already used by the format tests. The comment now states the fallback is
reachable, names both rows, and records that only the format string moves — both probe to the
right dimensions, so accept/reject is unaffected and no production caller reads the name. One
nuance worth having: `magick`'s `heif:` and `heic:` coders produce the **same 424 bytes**, and
both tools answer differently for the two names, so the row is a HEIF file under a `.heif` name
whose UTI is not `public.heic`.

### The census, restated in fewer places

`imaging.py:292` now points at `CORPUS_FORMATS` in `tests/test_imaging.py` as the enforced copy
instead of restating `841 / 47 / 3 / 2 / 1` a third time. `_cg.py:206` and `README.md:178` are
left alone as instructed. `CORPUS_FORMATS` itself is untouched and unweakened.

**`cli.py:166` does not restate the census.** There is no `841` in `cli.py` — the string the
ruling's line number points at is the deleted-pre-flight comment, which cites the 894 *total*
("894 files that were none of them images"), which is correct and is not the format census. So
the census is restated in **three** prose places, not four, and I corrected the one of them I was
touching.

## `tests/test_differential.py` — measured, and the harness's tests are now `sips`-free

Two uses, and they are different:

1. `test_a_lossy_output_is_compared_whole` used `sips` purely as a JPEG encoder at two qualities.
   A pure stand-in. Replaced with `imaging.resize_and_encode`.
2. `test_a_real_sips_png_is_compared_through_its_pixels` was **not** a stand-in by intent: its
   docstring says it exists to test the harness against "output from the tool itself", because
   `sips` picks filters per scanline and writes chunks of its own. I measured whether our own
   encoder has the same shape before substituting, and it does — ImageIO's PNG of a 48×32 noise
   frame uses **all five filter types** and writes `sRGB` and `eXIf` chunks of its own. Replaced,
   and renamed `test_a_real_encoder_png_is_compared_through_its_pixels`. It is arguably a better
   test now: the encoder it meets is the one a future gate will actually be handed.

`tests/differential.py` (the harness) is kept per Step 2. Its `OLD = "sips"` / `NEW =
"CoreGraphics"` labels are kept — they are only strings, `test_differential.py` asserts on them,
and renaming them for a migration that does not exist yet would be guessing. The comment above
them now says so, and says the next migration should rename them for its own pair. Its docstring
`sips` mentions are measurement provenance (the 723-of-894 PNG finding, the ICC clock) and stay.

## Step 4: the facts, moved

`docs/research/2026-09-11-replacing-pixelmator-and-imagemagick.md` gains **section 12, "The seven
measured `sips` defects, and the eighth we added"**, carrying all seven with their measurements
verbatim, stated as findings about a tool rather than constraints on this code. Plus:

- **The numbering is preserved deliberately.** "fact 6" is referenced by name from
  `execute.py`, `bands.py` and a dozen test docstrings; renumbering or rewording would have meant
  25 edits and a vocabulary break. Section 12 says it is the canonical home and `imaging.py`'s new
  docstring says the same, so every existing reference resolves.
- **Two are not retired**, and the section says which: fact 4's post-condition (still guarding
  `upscayl-bin` through `_run(produces=...)`) and fact 8.
- **The eighth is the region decode**, not what the brief expected — see below.
- A subsection on the 16-bit downconversion and the misattribution, also below.
- A closing subsection saying where the rest of the evidence is (the per-function docstrings), that
  the gates are retired and their last run was green at 116 + 8, and that `sips` survives
  test-side as an independent oracle under `tests/`.

Pointers added from §1 (what was added when) and §5 (the recommendation table is true and is not
the whole story).

`imaging.py`'s module docstring is now 45 lines describing the module: five operations, one
subprocess, why the docstrings below carry measurements, and where the facts went.

**Spec §7** loses the seven constraints in favour of three paragraphs — what the block was, that
it is CoreGraphics now, why (naming the two constraints that shaped the code rather than merely
guarded it), and where the findings live. The per-image sequence block is rewritten too, because
it was argv that no longer runs and it showed the deleted pad path as live.

## Findings that contradict the brief

These are the concerns. None of them blocked the work.

1. **The eighth fact is not the pad path's 16-bit downconversion.** The brief's acceptance
   criterion names it as such. `imaging.py`'s actual eighth fact is the region-decode phenomenon,
   and the 16-bit attribution to the pad was **measured wrong during Task 3**: `sips` returned 8
   bits from the direct crop, the crop at 0,0, the pad alone *and* a plain resample, so the pad
   was never the cause and the defect was never ours. I recorded both — the eighth as it actually
   stands, and the 16-bit finding in its own subsection with the correction stated — rather than
   writing down the brief's version.
2. **Two of the brief's four named rescues were already covered, more strongly.**
   `test_crop_is_region_exact_on_and_off_the_bad_offsets` (`test_imaging.py`) already covers the
   origin and both flush-bottom rects with a whole-region marker assertion, and
   `test_crop_to_file_honours_the_origin_it_is_given` (`test_cg.py`) already covers y=0/200/120 in
   gradient pixels. `test_resize_hits_both_axes_exactly` with `BOTH_AXES` already covers
   both-axes-exactly on four shapes chosen where `sips`' own derivation disagreed with the
   planner. I moved all four anyway — the floor is a floor, they are cheap, and the gradient form
   asserts row identity where the marker form asserts region uniformity — but three of the five
   crop/resize rescues are **additive rather than recovered coverage**, and the `SHAPES` rows are
   only genuinely new for the single-axis case (which is the one that catches an identity
   condition written with `or`). Worth a reviewer's eye on whether the duplication is wanted.
3. **`_cg.py` has no `pict`/`heif` "could not be produced on this machine" claim**, and `cli.py`
   has no census. Both described above; both corrected in the place the claim actually lived, or
   reported as absent.
4. **Test-side `sips` prose is untouched and some of it is now stale.** `tests/test_execute.py`
   has 17 mentions, several of which are present-tense live reasons of exactly the class ruling 2
   addressed for `paperhanger/` (`:128` "at scale=1 would make sips ENLARGE the crop", `:170` "one
   sips call the resample is applied against the PRE-crop width"). Ruling 2 named `execute.py`,
   `bands.py` and `cli.py`, so I stayed inside that scope. `tests/conftest.py` (17),
   `tests/pixels.py` (5), `tests/test_cli.py` (18) and `tests/test_corpus_sample.py` (8) are a
   mix of sanctioned oracle use and provenance and look fine. A later wave could sweep
   `test_execute.py`.
5. **Dangling file references fixed beyond the brief's list.** Deleting four modules left
   references to them in `paperhanger/imaging.py` (×4), `paperhanger/_cg.py` (×3),
   `tests/differential.py`, `tests/test_imaging.py`, `README.md` and
   `docs/superpowers/specs/2026-09-13-coregraphics-imaging-design.md`. All repointed. The only
   remaining mentions are the two "these came out of" provenance comments in the destination
   files, which are history by design, and the plan file, which is a record of the plan.
6. **`CLAUDE.md`'s "What this is" never said the tool uses `sips`.** Step 5's premise is wrong.
   What it needed was the positive statement — the imaging is CoreGraphics/ImageIO in-process,
   one subprocess — plus a warning that the word still appears ~90 times in `paperhanger/` as
   provenance, so the next reader does not take it as unfinished work. That is what I wrote. The
   **Status** section's test counts were also stale (551 across three tiers) and are updated to
   what this run measured.

## The three tiers

```
uv run pytest                    723 passed, 27 deselected in 96.28s (0:01:36)
uv run pytest -m corpus           21 passed, 729 deselected in 343.02s (0:05:43)
uv run pytest -m real_upscaler     6 passed, 744 deselected in 2485.56s (0:41:25)
```

**Tier 3 was the real gate here and it is green on its first run against CoreGraphics.** All
six ran for real — `toolchain.ensure_ready()` resolves an installed `upscayl-bin` and models, so
nothing skipped — and I confirmed mid-run that the binary was being handed a `normalized.png`
produced by the CoreGraphics colour conversion, which is the pipeline joint this migration
touched. 41m25s rather than the brief's ~25m estimate; `test_sample_runs_with_the_real_binary`
alone is about 25 minutes of real 4x inference.

All three tiers were run after every edit in this task was in place. Tier 1 was also run twice
in between — once immediately after the deletions and moves, once after the docstring and
comment rewrites — to separate a test failure from a docstring that broke a module.

## Files

- Deleted: `tests/test_crop_differential.py`, `tests/test_resize_differential.py`,
  `tests/test_encode_differential.py`, `tests/test_normalize_differential.py`
- Modified: `paperhanger/imaging.py` (docstring, the `SIPS` constant, four cross-references,
  the census pointer), `paperhanger/_cg.py` (header note, the `pict`/`heif` correction, three
  cross-references), `paperhanger/execute.py` (nine comments), `paperhanger/bands.py` (one
  docstring), `tests/test_imaging.py` (+52 tests, imports, three `sips` uses repointed),
  `tests/test_cg.py` (+18 tests, imports), `tests/differential.py` (the `OLD`/`NEW` comment, one
  cross-reference), `tests/test_differential.py` (`sips` removed entirely),
  `docs/research/2026-09-11-replacing-pixelmator-and-imagemagick.md` (+section 12),
  `docs/superpowers/specs/2026-09-11-paperhanger-design.md` (§7),
  `docs/superpowers/specs/2026-09-13-coregraphics-imaging-design.md` (one cross-reference),
  `README.md` (two), `CLAUDE.md` (what-this-is, status)
- Untouched as instructed: `imaging._run` and `produces=`, `import subprocess`, `upscale`,
  `tests/differential.py`'s comparison logic, `CORPUS_FORMATS`, `_cg.py:206`, `README.md:178`
