# Task 4 report: Resample on CoreGraphics

**Status: DONE_WITH_CONCERNS** (the concerns are scope notes and one latent asymmetry,
listed at the end; nothing in them is a known defect in what shipped)

**Commits:**
`9f64921` — feat: resample through CoreGraphics, and the identity draw goes
(`paperhanger/_cg.py`, `paperhanger/imaging.py`, `tests/test_resize_differential.py`,
`tests/test_imaging.py`)
`d3e9739` — docs: what the identity skip is worth, in the README

This report is not committed: `.superpowers/` is git-ignored scratch.

`resize_and_encode` keeps its exact signature. The resample is
`_cg.resize_to_file`; the encode is still one `sips` per call. `execute.py` is
untouched.

---

## Part 1 — the skip measurement, which came first

**The question:** when the source's dimensions already equal the requested output
dimensions, is skipping the draw byte-identical to performing it — and what must the
condition be?

**The answer:** skipping is not merely equal, it is **closer to `sips` than drawing
is**, and the condition is **the dimensions on both axes and nothing else**. Every
number below is mine, run on this machine today, with the counts and the population
each was taken over.

### 1a. Three routes, one shape each, over eleven synthetic sources

Instrument: `sips --resampleHeightWidth H W -s format png` versus a
`bitmap_context` + `CGContextDrawImage` route versus a route that hands
`CGImageSourceCreateImageAtIndex`'s image straight to `write_png`. Compared at whole
file, at concatenated `IDAT`, and through `tests/differential._compare_png` (decoded
pixels, colour type, channel count, bit depth). No ImageMagick anywhere in the loop.
All 400x300 unless the row says otherwise; the reduce column is to half on both axes.

| source | identity: sips vs **draw** | identity: sips vs **skip** | reduce: sips vs draw |
|---|---|---|---|
| RGB 8-bit, noise | whole file identical | whole file identical | whole file identical |
| grey 8-bit, flat | whole file identical | whole file identical | whole file identical |
| grey 8-bit ramp, 150x200 | whole file identical | whole file identical | whole file identical |
| **RGBA, alpha 128** | **120,000 of 480,000 samples differ, every one by 1** | **whole file identical** | whole file identical |
| **grey+alpha, alpha 128** | **60,400 of 240,000 differ, every one by 1** | **whole file identical** | whole file identical |
| RGB 16-bit | whole file identical | whole file identical | sips wrote 8 bpc, ours 16 (pinned) |
| **indexed (PLTE)** | **ImagingError: no context for an indexed space** | **whole file identical** | ImagingError (sips writes type 2) |
| interlaced Adam7 | `IDAT` differs, pixels + format agree | identical to the draw's output | pixels + format agree |
| grey 1-bit | sips kept depth 1, ours 8 | sips kept depth 1, ours 8 | sips kept depth 1, ours 8 |
| grey 4-bit | whole file identical | whole file identical | whole file identical |
| Adobe RGB tagged | `IDAT` identical, file 361,511 vs 361,086 B | same, and identical to the draw | `IDAT` identical, 80,374 vs 79,949 B |

Two rows carry the finding. **Alpha:** the destination bitmap for an alpha-bearing
source is `PremultipliedLast`, so the encode has to undo the premultiply, and it comes
back a unit short on a quarter of the samples. **Indexed:** the draw cannot happen at
all, and the skip's output is byte-identical to `sips`'.

Where draw and skip agree, they agree exactly — the 1-bit row is the clearest case, and
it is worth stating as a negative result: **the skip neither causes nor cures the
sub-8-bit promotion.** ImageIO's decode is what promotes it, so the skip's output is
byte-identical to the draw's and both differ from `sips` in depth alone.

### 1b. The same question over real photographs

63 corpus files at their own dimensions, `IDAT` compared — the three the preflight
found diverging, plus the first 60 by name:

```
IDAT agreement with sips at identity:  skip 63/63,  draw 11/63
whole file identical (skip):            9/63   (the rest differ only in metadata)
```

The three known divergers — `green_leaf_closeup_2463.jpg`,
`roadside_grass_2121.jpg`, `sunlight_through_leaves_8375.jpg`, all 7680x5120 — are
three of the 52 the draw alters. **The draw introduces the divergence on 52 of 63 real
files, and the skip removes it on all of them.** This is a population, not a sample of
three: the preflight's `sips == skip on 120 of 120` reproduces here in a differently
drawn sample with a differently written instrument.

### 1c. Memory, which was the other half of the case

`green_leaf_closeup_2463.jpg`, 7680x5120 (39.3 Mpx), identity resample. Peak RSS of a
child process per route, `max(RUSAGE_SELF, RUSAGE_CHILDREN)` so the `sips` subprocess
is counted, minimum of three runs:

```
sips  332.1 MiB   [332, 332, 332]
draw  499.2 MiB   [499, 499, 499]      1.50x sips
skip  351.0 MiB   [351, 350, 351]      1.06x sips
```

Skipping removes 148.2 MiB of the 167.1 MiB gap. This is a smaller ratio than Task 0's
2.13x because it is a different and smaller frame — Task 0 measured the 10000x4780
band-4 frame, which I cannot produce without running the upscaler. The direction and
the mechanism reproduce: the draw holds the decoded frame and a whole second bitmap.

### 1d. What the condition must be — and why "dimensions equal" IS sufficient here

**The condition is `dimensions(image) == (out_width, out_height)`.** Not either axis:
both. Not the colour model, not the depth, not the alpha.

That is sufficient **in this function** for a reason that does not generalise, and the
brief was right to insist it be measured rather than assumed. `bitmap_context` builds
the destination out of the **source's own colour space**, fetched with
`CGImageGetColorSpace` off the image being drawn. So in `resize_to_file` the draw is
never a colour conversion: there is no conversion for skipping to delete. The Adobe RGB
row above is the measurement — `IDAT` identical through all three routes, so the
profile survives the skip exactly as it survives the draw.

Where a draw targets a colour space **of our choosing**, it IS the conversion, and
skipping it would delete that conversion. `normalize_to_srgb_png` (Task 6) is precisely
that draw, and the same condition applied there would be a silent behaviour change on
the 118 non-sRGB corpus files. The docstring in `resize_to_file` says so in those
words, and `_cg.py`'s module docstring now points at it from the other end.

The alpha normalisation is the case the brief flagged as "where the draw is also the
work". Measured, it is the opposite: the draw's alpha handling is what diverges, and
skipping it is what agrees with `sips`. That the draw also *refuses* indexed sources is
a real asymmetry the skip introduces — listed under concerns, and pinned by two tests.

### 1e. Interpolation, measured rather than taken on trust

The brief says Default measured identical to High. I re-ran it rather than cite it,
because the acceptance criterion turns on it. A 2000x1400 noise PNG drawn at all five
quality constants, SHA over the written file:

| target | groups of identical output |
|---|---|
| 1000x700 | **Default = High** \| None \| Low \| Medium |
| 900x500 | **Default = High** \| None \| Low \| Medium |
| 1600x1200 | **Default = High** \| None \| Low \| Medium |
| 400x300 greyscale ramp → 1000x700 | **Default = Low = High = Medium** \| None |

So three of the four wrong constants ARE visible in the output, and the differential
would catch them; the one that is invisible is Default, which is invisible precisely
because it is High today. That is the argument for asserting the call. The greyscale row
is a second finding worth keeping: a smooth source cannot tell interpolation levels
apart at all, which is why the resample fixtures carry noise.

---

## Part 2 — what shipped

### `paperhanger/_cg.py`

`resize_to_file(source, out_width, out_height, out_path)`. Loads, and:

* if `dimensions(image) == (out_width, out_height)`, writes the decoded image and
  returns — no context, no draw;
* otherwise `bitmap_context(...)` (never a hardcoded alpha info — see below),
  `CGContextSetInterpolationQuality(ctx, kCGInterpolationHigh)`,
  `CGContextDrawImage` over the full destination rect, `CGBitmapContextCreateImage`,
  `write_png`.

Every handle goes into the `Scope`; every NULL-capable call goes through `_checked`. No
new CoreGraphics symbol was needed — every function it calls was already in
`_declare()`, which is what keeps a ctypes truncation from becoming a segfault.

**The plan's `CGBitmapContextCreate` line was not pasted.** It hardcodes
`kCGImageAlphaNoneSkipLast`. Running exactly that as a mutation (M9 below) reproduces
all three of its failures in one test: a greyscale source comes back with 29,800 of
30,000 samples differing at a largest difference of **255** — the black frame — and the
alpha channel is **gone** from both the RGBA and the grey+alpha outputs. It fails at
exit 0 with a plausible file, which is why it is pinned by a test rather than trusted to
a comment.

### `paperhanger/imaging.py`

`resize_and_encode` keeps `(source, out_width, out_height, fmt, quality, out_path,
resize)`. On `resize=True` it clears the destination, resamples to a staged PNG, encodes
that with `sips -s format <fmt>` (`-s format` still ahead of the paths — fact 7), and
removes the staged file in a `finally`.

Three deliberate changes, each documented where it happens:

1. **The destination is cleared before the resample, not by `_run`.** `_run(produces=)`
   used to be what stopped a stale file from an earlier run standing in for output this
   run never produced. The resample can now fail before `_run` is reached, so the
   unlink moved up. M7 confirms a test goes red without it.
2. **The staged file's name ends in `.partial`.** It is the destination's whole name
   with `.resized.partial` appended — appended rather than substituted, so no caller's
   suffix is replaced and no other plan's output name can be produced. `.partial` is
   what `execute.sweep_partials` globs at the start of every run, so the one exit no
   `finally` covers — a kill between resample and encode — leaves a file the next run
   tidies instead of a 300 MB PNG standing in the output directory. The constant cannot
   be imported (`execute` imports `imaging`), so a test asserts the coupling.
3. **An unreadable source fails in ImageIO** rather than as a `sips` warning on a zero
   exit. Same exception type, same no-output-left-behind guarantee; different message.
   Two tests in `test_imaging.py` asserted `sips`' own stderr through the resizing
   branch and now assert it through the encoding branch, with two new tests covering the
   resizing branch's own two failure routes (open and decode).

### `README.md`

Three paragraphs in "Working around sips": the resample moved, what the identity
pass-through is and what it is worth (63/63 against 11/63, 351 against 499 MiB), and the
two changed behaviours.

---

## Part 3 — tests

`tests/test_resize_differential.py`, 23 tier-1 tests and 2 tier-2:

* **`test_resize_matches_sips_exactly`** and **`test_both_axes_land_exactly`** over five
  shapes: one reduction, one enlargement, one width-governed, one height-governed, and
  **one that changes a single axis** (2000x1400 → 1000x1400). The last is new and it
  earns its place: it is the only row where an identity condition written with `or`
  instead of `and` fails (M4).
* **`test_that_comparison_can_fail`** — the harness must report a 1000x699 output
  against a 1000x700 one.
* **`test_interpolation_is_high_not_default`** — monkeypatches
  `_cg._CG_LIB.CGContextSetInterpolationQuality` and asserts the call, because Default
  measured identical and no output can tell them apart.
* **`test_an_identity_resample_does_not_draw`** and
  **`test_a_resample_that_changes_a_single_axis_still_draws`** — watch
  `CGContextDrawImage` itself. For an opaque source a 1:1 draw and a skip write the same
  pixels, so no comparison of files can see which ran.
* **`test_every_colour_shape_matches_sips`** — seven colour shapes at identity, five of
  them reduced as well. **Both columns are needed and they catch different things:** the
  identity column measures the skip against `sips`; the reducing column is the only
  place the destination bitmap's alpha info is chosen, and the same wrong pairing
  round-trips correctly at 1:1. The two rows excluded from the reducing column have
  their own tests (indexed raises; 16-bit is the pinned depth divergence).
* **`test_drawing_at_identity_is_what_diverged`** — forces the draw by lying to the
  identity check and requires that it **disagree** with `sips`, at the exact sample
  counts measured above (120,000 and 60,400). If a future CoreGraphics makes the 1:1
  draw exact, this fails and says so, because the docstrings claim otherwise.
* **`test_an_indexed_source_is_refused_when_it_has_to_be_drawn`** — the asymmetry,
  stated rather than discovered.
* **`test_a_sixteen_bit_source_keeps_its_depth`** — and asserts `sips` still drops it,
  so the pinned exception stops being pinned if Apple changes.
* **Three staging tests** — gone on success, gone on a failing encode, and named so the
  executor's existing sweeper would catch a stray.
* **Tier 2**, `-m corpus -k resize`: all 27 coverage images at **two** shapes each —
  identity, and an anisotropic `w//3 x h//4` reduction, because 955 of the 2734
  production resamples have an x scale that differs from their y scale. Each room is
  removed as it is compared; an identity resample of the largest sample image is a
  9072x12096 PNG and four of those per file would leave gigabytes standing.

  Unlike the crop gate this needs **no decode control**. Fact 8 is a region-decode
  effect and a resample reads whole frames. I checked that claim rather than asserting
  it: the draw agrees with `sips` on decoded pixels for **27 of 27** sample images at
  the reducing shape, including the WebP named `.jpg`, the GIF, the HEIC and the
  interlaced PNG.

### Results

```
uv run pytest tests/test_resize_differential.py -v    23 passed, 2 deselected   15 s
uv run pytest                                        672 passed, 29 deselected  132 s
uv run pytest -m corpus -k resize                      2 passed                 103 s
```

647 → 672 is the 23 new differential tests plus the 2 new failure-route tests in
`test_imaging.py`.

### Mutations — eleven, each confirmed red, each reverted

`__pycache__` cleared and `PYTHONDONTWRITEBYTECODE=1` set for every run, because a
`.pyc` is invalidated on (whole-second mtime, size) and a same-size edit inside one
second reuses the previous mutation's bytecode.

| # | mutation | what went red |
|---|---|---|
| M1 | remove the `SetInterpolationQuality` call | `test_interpolation_is_high_not_default` |
| M2 | interpolation Default (0) instead of High | `test_interpolation_is_high_not_default` |
| M3 | never skip: always draw | `test_an_identity_resample_does_not_draw`, `test_every_colour_shape_matches_sips[identity]` |
| M4 | skip when **either** axis matches | `test_resize_matches_sips_exactly[2000-1400-1000-1400]`, `test_both_axes_land_exactly[same]`, `test_a_resample_that_changes_a_single_axis_still_draws` |
| M5 | always skip: never draw | 15 tests |
| M6 | rect axes swapped in the draw | 7 tests |
| M7 | destination not cleared before the resample | `test_a_stale_destination_cannot_stand_in_for_output` |
| M8 | staging file left behind | both staging tests |
| M9 | the plan's hardcoded `kCGImageAlphaNoneSkipLast` | `test_every_colour_shape_matches_sips[reduced]` (black greyscale, alpha gone twice), both `test_drawing_at_identity_is_what_diverged`, `test_a_sixteen_bit_source_keeps_its_depth` |
| M10 | derive the height from the width | 8 tests |
| M11 | the plan's `with_suffix('.resized.png')` staging name | `test_the_staging_name_is_one_the_executor_already_sweeps` |

M9's first red is worth quoting, because it is the failure the plan's code would have
shipped:

```
greyscale: pixels differ at (0, 1), grey channel -- sips 2, CoreGraphics 0;
           29800 of 30000 samples differ, largest difference 255
rgba:      sips wrote colour type 6 (4 channels), CoreGraphics wrote colour type 2
           -- the alpha channel is gone
grey+alpha: sips wrote colour type 4 (2 channels), CoreGraphics wrote colour type 0
           -- the alpha channel is gone
```

**A gap the first mutation round found, and closed.** The colour-shape test originally
ran at identity only, and M9 therefore did **not** go red on the black-frame case — the
identity path never builds a context, and the wrong pairing round-trips correctly at
1:1. Adding the reducing column is what made the headline defect fail a test. Recorded
because the same shape of hole is what the alpha-info rule exists to prevent, and it
survived my own first pass.

---

## Acceptance criteria

| criterion | verdict |
|---|---|
| exact signature kept | yes; `execute.py` untouched |
| both axes land exactly on shapes where one could be derived | yes, five shapes; M10 and M4 confirm |
| interpolation High, asserted by a test that fails if the call is removed | yes; M1 and M2 |
| byte-identical to `sips` on at least four shapes, no exceptions | yes — three of the five SHAPES rows and every non-pinned colour shape compare identical at the gate's bar; over real images, 27 of 27 at a reduction and 63 of 63 at identity |
| colour profile survives the resample | yes; Adobe RGB `IDAT` identical through draw and skip |

---

## Concerns

1. **An indexed source resizes at identity and raises at every other shape.** The skip
   builds no context, so `bitmap_format`'s refusal is not reached. The output is
   byte-identical to `sips`', so this is an asymmetry between raising and succeeding
   correctly, not between two answers — and no corpus file decodes as indexed. Both
   halves are pinned by tests. If a reviewer wants uniformity, the fix is to call
   `bitmap_format` for its side effect on the identity path, which would make the tool
   refuse a file it currently handles correctly. I did not do that.

2. **The 16-bit divergence widened by one path.** `sips --resampleHeightWidth` dropped a
   16-bit source to 8; we keep 16, exactly as crop now does. Latent — no corpus file is
   16-bit — and asserted on a fixture in both directions.

3. **Sub-8-bit sources come back at 8 bits** where `sips` keeps the depth. This is
   ImageIO's decode, identical through draw and skip, and no corpus file is below 8 bpc.
   Measured on a hand-built 1-bit greyscale PNG; not covered by a standing test, because
   `tests/pixels.py` has no writer for those depths and adding one to assert a
   divergence nothing depends on seemed the wrong trade. Named here instead.

4. **Peak memory is still 1.06x `sips` at identity and 1.50x when drawing.** The
   identity case is fixed; the 2157 resamples that really do reduce still hold a second
   bitmap. Nothing in Task 4's scope changes that, and the reducing case was never the
   one out of line.

5. **The staged intermediate is a second write of the frame.** Task 5 removes it with
   the `sips` encode. Until then a resizing call writes the frame twice, which costs
   wall time on the 39 Mpx sources and is the reason the tier-2 gate takes 103 s.

6. **`imaging.py` hardcodes `".partial"`** rather than importing
   `execute.PARTIAL_SUFFIX`, because `execute` imports `imaging` and the constant cannot
   travel the other way. A test asserts the two agree, so a change to either is caught,
   but the duplication is real.

---

# Round 1 — fixes

**Status: DONE.** One commit, `cbb8943`, on top of `9f64921` / `d3e9739`. Everything below was
re-measured here rather than taken from the review, because three of the six items put a
number or a behaviour into a docstring.

## should-fix — `paperhanger/_cg.py`, the refusal bullet widened to the class

The bullet named indexed; the class is **everything `bitmap_format` refuses** — indexed,
CMYK, Lab, and anything above 16 bits per component. All of them now resize at identity
and raise at every other shape, for the same reason: the identity path builds no context
and `bitmap_format` is what refuses.

CMYK measured rather than asserted. A CMYK JPEG built with `sips --matchTo` the Generic
CMYK profile, confirmed by `sips -g space` (`CMYK`, `samplesPerPixel: 4`) and by
`_cg.colour_model` (`CMYK`, 8 bpc):

| shape | `sips` | draw | skip |
|---|---|---|---|
| 400x300 identity | 361,148 B | **ImagingError: cannot build a bitmap context for the CMYK colour space** | `IDAT` identical to `sips`, 360,723 B |
| 200x150 | 77,136 B | same refusal | n/a |

So CMYK behaves exactly as the review described, and it is the member of the class that
matters: four-channel JPEGs come out of print workflows, and `sips` resampled one without
complaint. The docstring says so in those words.

**The corpus census, run through our own classifier.** All 894 files through
`_cg.colour_model`: **872 RGB, 12 RGB with alpha, 10 monochrome, every one at 8 bits per
component, 0 unreadable.** That reproduces the review's 884 RGB / 10 monochrome exactly.
The review's warning is also confirmed: `sips -g space` on a palettised PNG reports
`RGB`, so a `sips` sweep could not have established the absence of either case. Both the
census and that caveat are now in the docstring.

## minor — `paperhanger/imaging.py`, the cleanup made structural

The staged NAME is taken before the `try`; the staged FILE is written inside it. A
resample that raised with its output already written now has that output removed. The
comment says plainly that no current path in `_cg.resize_to_file` can do that — its last
statement is `write_png`, which unlinks its own destination on a refused Finalize — and
that the guarantee belongs to `resize_and_encode` rather than to the layer below.

New test: `test_the_staging_file_does_not_survive_a_failing_resample`, which substitutes
a resample that writes and then raises.

## minor — the test no longer reads the harness's prose

`test_drawing_at_identity_is_what_diverged` computed nothing; it looked for
`f"{differing} of"` in `_describe_pixels`' sentence. It now writes both outputs itself
and counts through a new reader, `pixels.read_png_samples`, asserting
`(samples differing, largest difference)` as a property of the two files. The reader
handles the four non-palettised colour types at 8 and 16 bits and refuses interlaced and
palettised input by name.

That reader was needed anyway for the `tRNS` test below — no existing reader in
`tests/pixels.py` can look inside an alpha-bearing output; both refuse everything but one
colour type each.

## minor — the stale count in this report

Part 2 said "(23/23 against 6/23)" where the shipped README and every measurement say
63/63 against 11/63. The README was right. Fixed.

## worth taking — the colour-key `tRNS` fixture and its tests

`pixels.write_colour_key_png` writes the third kind of PNG transparency: colour type 2,
no alpha channel, and a `tRNS` chunk naming one RGB triple transparent. Left half keyed
magenta, right half opaque, so a composite shows up as exactly half the frame.

Measured with that fixture at 400x300 identity:

```
sips  400x300 ct6  idat=1e4ced79476f  3135 B
draw  400x300 ct6  idat=30f34a82f8d5  3190 B
skip  400x300 ct6  idat=1e4ced79476f  3135 B      <- whole file identical to sips

sips vs draw:  120,000 of 480,000 samples differ, largest difference 255
               first at (0,0), red channel: sips 255, CoreGraphics 0
```

The review's number, reproduced with an independently built fixture. ImageIO expands the
key to an alpha channel, the draw composites the keyed pixels onto the context's black
ground, and magenta comes back black **at the same colour type and the same dimensions
as a correct answer**. It is the largest difference the draw produces anywhere, and it
was the one nothing guarded.

Two tests: the damage row in `DRAW_DAMAGE`, and
`test_the_colour_key_survives_the_pass_through`, which asserts not only that we equal
`sips` but that the first keyed pixel reads `[255, 0, 255, 0]` — so the test still means
something if `sips` itself ever starts compositing.

**16-bit RGBA**, the review's other case, also reproduces: **179,600 of 480,000 samples
by 1** on my fixture against the review's 180,238 on its own. That count is a property of
the fixture's content, not a constant — worth saying, since both numbers are correct for
their own input. It is recorded in the `resize_to_file` alpha table; I did not add a
writer for it, because `write_rgba_png` already reaches the same premultiply branch and a
sixth writer to restate it at a different depth would not pin anything new.

## Tests

```
uv run pytest tests/test_resize_differential.py -q     28 passed, 2 deselected   16 s
uv run pytest                                         678 passed, 29 deselected  134 s
uv run pytest -m corpus -k resize                       2 passed                 103 s
```

672 → 678: the colour-key damage row, the colour-key pass-through, the failing-resample
cleanup, and the refused-source pair going from one test to four (indexed and CMYK, each
refused when drawn and correct at identity).

### Mutations — four new, all red; the eleven from round 0 re-run and still red

| # | mutation | what went red |
|---|---|---|
| M12 | the resample back outside the `try` | `test_the_staging_file_does_not_survive_a_failing_resample` (+2 others, since the mutation resamples twice) |
| M13 | the colour-key fixture writes no `tRNS` chunk | the colour-key damage row and the pass-through test — so the chunk, not the picture, is what those tests measure |
| M14 | the CMYK fixture skips the conversion | both CMYK tests, through the fixture's own assertion |
| M15 | `read_png_samples` unfilters at bpp 1 | all three damage rows and the pass-through — the reader is doing real work, not returning the same numbers either way |

M9 (the plan's hardcoded `kCGImageAlphaNoneSkipLast`) now takes down **seven** tests
rather than four, the new ones being the colour-key row and both refusal tests.

## What I think is wrong, or worth flagging

Nothing in the review was wrong. Two refinements:

1. **The 16-bit RGBA count is fixture-dependent** (mine 179,600, the review's 180,238),
   so it should not be quoted as a constant the way the 8-bit RGBA and `tRNS` counts can
   be — those two come out at exactly 120,000 of 480,000 because the fixtures are flat
   and half-keyed respectively.
2. **M12 is not a clean revert.** The harness applies mutations as text substitutions, so
   "the resample back outside the `try`" duplicates the call rather than moving it, and
   two unrelated tests go red alongside the intended one. The covering test fails for the
   right reason; the other two reds are noise from the double resample.
