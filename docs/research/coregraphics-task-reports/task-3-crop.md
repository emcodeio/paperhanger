# Task 3 report: crop on CoreGraphics

**Status: DONE_WITH_CONCERNS.** The swap is implemented, the pad workaround is
gone, the fast suite is green at 647 and the corpus tier passes. Two of the
plan's premises turned out to be false when measured, and both changed what the
gate could honestly assert. They are the concerns, and they are §4 and §5 below.

Commits: `658ba97` (implementation), `53904f4` (`_cg` and `imaging` tests),
`2fffcb9` (the differential module), `aed637e` (two stale docstrings).

---

## 1. What changed

| File | Change |
|---|---|
| `paperhanger/_cg.py` | `ImagingError` moved here; `crop_to_file` added |
| `paperhanger/imaging.py` | `crop` reimplemented; `PAD_COLOUR`, `_offset_is_ignored`, `_crop_via_padding` deleted; facts 6 and 7 rewritten; fact 8 added |
| `paperhanger/execute.py` | the peak-occupancy paragraph, which described the pad |
| `README.md` | the "Working around sips" section |
| `tests/test_cg.py` | `crop_to_file`: origin, the overlap hazard, the refusals, a leak loop |
| `tests/test_imaging.py` | pad tests retired, surviving contract tests re-justified |
| `tests/test_crop_differential.py` | new: the retained reference, the pins, both tiers |

`crop`'s signature, its PNG-only refusal and its bounds check are unchanged.

**The import cycle** was broken the way the plan preferred: `ImagingError` now
lives in `_cg.py` and `imaging.py` re-exports it. `imaging.ImagingError is
_cg.ImagingError` is the same class object, so every existing `except` keeps
working and `tests/test_cg.py`'s `from paperhanger.imaging import ImagingError`
did not need touching.

---

## 2. The origin, measured

`CGImageCreateWithImageInRect` takes its rect at the image's **top left**, which
is what makes the pad unnecessary. On a 40x200 greyscale ramp whose value names
its own source row:

| rect | first output row | last output row |
|---|---|---|
| y=0, h=50 | source row 0 | source row 49 |
| y=150, h=50 | source row 150 | source row 199 |
| y=75, h=50 | source row 75 | source row 124 |

Both of fact 6's shapes — the origin and the flush bottom edge — come back
correct with no workaround.

## 3. The bounds check kept its job, and gained a new reason

`sips` padded an out-of-bounds crop with black at exit 0. CoreGraphics does
something different and just as silent: it **intersects** the rect with the
image and returns the overlap. Measured on a 400x200 source:

| rect | `CGImageCreateWithImageInRect` returns |
|---|---|
| 120x80 at (350, 40) | a 50x80 image |
| 900x900 at (0, 0) | the whole 400x200 |
| 120x80 at (-10, -10) | a 110x70 image |
| 120x80 at (900, 900) | NULL (no overlap at all) |

So a rect a pixel too large comes back a pixel short, at no error, and
`resize_and_encode` then stretches the wrong region to the right dimensions —
the same class of failure the guard existed for, in a new costume. Both the
guard in `imaging.crop` and a post-condition inside `crop_to_file` are tested,
and the API's own behaviour is pinned so the post-condition is not an assertion
about something nobody showed could happen.

---

## 4. CONCERN: the corpus differential is not clean, and the reason is not crop

**The plan and the design both say crop is byte-identical on every corpus file.
It is not, and this was the largest surprise of the task.** Over the 27-image
sample at the top-third rect, the old and new implementations differ on 14 of
27 files.

The cause is not the crop. It is the decode:

**ImageIO decodes a *region* of a baseline JPEG differently from the whole
frame, once the file passes 1,000,000 pixels.** `sips` shows it as plainly as
we do. Measured with no CoreGraphics anywhere in it, on `acrylic_7049.JPG`
(1284x2778): `sips` cropping 400x300 at (40, 30) differs from a plain `sips -s
format png` of the same file in **212,413 of 360,000 samples, mean absolute
error 1.0592** out of 255. And `sips` cropping an already-decoded PNG is
byte-exact against a Python slice of that decode, so the crop machinery is fine
on both sides; only the JPEG decode moves.

*(Corrected in fix round 1 — this paragraph originally read "a `sips` crop that
removes nothing … 5,441,250 of 10,700,856 samples". That figure was real but
mislabelled: it came from the padded **reference**, which crops at +1 inside a
padded copy, not from a plain full-frame crop. A genuine full-frame
`--cropOffset 0 0` is byte-identical to a plain decode — re-measured — so the
rect has to be strictly smaller to provoke this at all.)*

The threshold is sharp, is area rather than dimension, and is **a round decimal
number rather than a power of two**, which is not the guess anyone makes. Samples
differing of 90,000 over a 200x150 region of synthetic noise:

| synthetic noise JPEG | pixels | `sips` | CoreGraphics |
|---|---|---|---|
| 1152x864 | 995,328 | 0 | 0 |
| 1024x976 | 999,424 | 0 | 0 |
| 1000x999 | 999,000 | 0 | 0 |
| **1000x1000** | **1,000,000** | **0** | **0** |
| **1250x800** | **1,000,000** | **0** | **0** |
| **1250x801** | **1,001,250** | **39,868** | **86,423** |
| 1000x1002 | 1,002,000 | 70,166 | 86,252 |
| 1008x1008 | 1,016,064 | 70,203 | 86,060 |
| 1024x1024 | 1,048,576 | 70,202 | 86,167 |

*(Also corrected in fix round 1. I originally reported the threshold as
1024×1024. Every figure I gave reproduces digit for digit — I simply never
sampled between 10⁶ and 2²⁰, and took the power of two as the natural
boundary. Two sizes of exactly 1,000,000 pixels agree; 1,001,250 does not.)*

Over the corpus sample it correlates with baseline-vs-progressive: every
progressive JPEG agrees, and every baseline JPEG over the threshold differs
except the greyscale one. Every PNG, GIF and WebP agrees.

*(Fix round 2 note: that exception is a property of the individual file, not of
greyscale. Across the full corpus four of the eight greyscale baseline JPEGs
over the threshold **do** differ. See fix round 2 §1 — I originally read
greyscale as immune and it is not.)*

**Neither side is the better decode.** Both depart from their own whole-frame
decode, by comparable margins and in different directions. Measured on four
corpus photographs at a 400x300 region, mean absolute error out of 255 against
each tool's own full decode:

| file | `sips` crop | CoreGraphics crop | the two against each other |
|---|---|---|---|
| acrylic_7049.JPG | 1.038 (max 33) | 0.934 (max 24) | 0.592 (max 24) |
| snowy_forest_6657.jpg | 0.314 (max 15) | 0.332 (max 12) | 0.038 (max 8) |
| mountain_lake_reflection_4788.jpg | 1.383 (max 48) | 1.256 (max 42) | 0.897 (max 25) |
| foggy_forest_3113.jpg | 0.351 (max 6) | 0.417 (max 6) | 0.119 (max 3) |

I did try to make ours match the frame decode: `kCGImageSourceShouldCache-
Immediately` on the decode makes no difference at all (byte-identical to the
lazy path). Getting the frame decode would mean materialising the whole bitmap
and cutting from that, which is a draw, and a draw is Task 4's business.

**This is live, not latent.** `execute._upscale_one_plan` crops `work.source`
directly, so real JPEG wallpapers go down this path, and every corpus photo is
over the threshold.

### The exact split over the 27

**Byte-identical, 13.** Every PNG (4), the GIF, both WebPs — including
`snowy_forest_landscape_9522.jpg`, the file whose extension lies — the
progressive JPEGs, `foggy_forest_path_7098.JPG`, and
`katana_with_tag_2369.jpg`.

*(Corrected in fix round 1.* I originally called `foggy_forest_path_7098.JPG`
the one baseline JPEG over the threshold that agrees anyway. It is 620x1102 =
**683,240 px, comfortably under** the threshold, so it is not an exception at
all. `katana_with_tag_2369.jpg` — 1920x1080 = 2,073,600 px, baseline,
single-component greyscale — does agree at the gate's rect, though not
everywhere: at an interior rect it differs in 2,294 of 345,600 samples, each
by 1.*)

*(**Corrected again in fix round 2, and this one was a wrong explanation
rather than a wrong number.** Round 1 closed that note with "the chroma
channels are where the effect lives", generalising from katana alone. That is
refuted — see fix round 2 §1. Chroma subsampling **amplifies** the effect; it
is not where it lives.)*

**Accounted for by the control, 14.** Thirteen baseline JPEGs and the HEIC.
Representative rows:

| file | samples differing | largest |
|---|---|---|
| `moss_with_pine_needles_5324.jpg` | 2,314,473 of 23,994,000 | 3 |
| `snowy_forest_6657.jpg` | 340,381 of 17,915,904 | 13 |
| `acrylic_7049.JPG` | 1,236,017 of 3,566,952 | 45 |
| `mountain_lake_reflection_4788.jpg` | 3,461,287 of 8,294,400 | 35 |
| `purple_nebula_glow_0312_x.heic` | **3,839 of 36,864,000** | 12 |

**Failures, 0.**

The HEIC is worth a separate look: 0.01% of samples, isolated rather than
spread, which does not look like the JPEG class at all. The control accounts
for it, so it is a decode difference and not a crop one, but if you want the
HEIC input question of §6 of the design answered properly, that row is where
to start.

### What I did with it, and what I did not

The brief says report an unexpected difference rather than pin it. I have
reported it, and I did **not** widen the tolerance. What the corpus gate does
instead is *account* for each difference by measurement:

> where the two sides differ, hand **both** implementations the same
> already-decoded pixels — `sips`' own PNG of the file — and require them to
> agree exactly.

That is a positive claim ("the crop is not what differs"), not an excuse, and
anything the control cannot account for still fails. The gate also fails if the
disagreement set is ever *empty*, because that would mean the control had
quietly stopped doing anything. `test_the_corpus_control_is_not_vacuous` proves
the control can report a difference, and a corpus-tier mutation confirms it.

**You may prefer the gate simply be red on these 14 files.** That is a
judgement about what the differential is for, and it is yours rather than mine;
the machinery to flip it is one `assert not decoder_disagreements`.

---

## 5. CONCERN: the 16-bit downconvert is not the pad's, and is not ours

The design says: *"The current pad path has an eighth silent defect, and it is
ours. It downconverts 16-bit sources to 8-bit … unlike the other seven it was
introduced by our workaround rather than by Apple."*

Measured on a 16-bit 200x200 PNG:

| `sips` operation | output depth |
|---|---|
| `-s format png` (plain convert) | **16** |
| `-c … --cropOffset 40 20` (direct crop) | 8 |
| `-c … --cropOffset 0 0` (the padded shape) | 8 |
| `-p 202 202 --padColor` (pad alone) | 8 |
| `--resampleHeightWidth` | 8 |

So `sips` drops the depth on every path that touches pixels. The pad is not the
cause, the defect was never ours, and removing the pad from the reference does
not restore 16 bits. I found this through mutation: `M12`, which switches the
retained reference to its direct branch, left the depth assertion green.

The pin survives — the difference between old and new is real and intended —
but its stated reason was wrong, so `PINNED` now names `sips` rather than the
workaround, and the assertion runs over **both** reference branches. The
research note at `docs/research/2026-09-13-coregraphics-preflight-measurements.md:1299`
and §2 of the design carry the same misattribution; I have not edited either,
since they are records rather than code.

---

## 6. Tests

### Retired

| test | why |
|---|---|
| `test_cropping_a_lossy_source_does_not_bleed_the_pad_in` | measures the magenta pad |
| `test_the_padded_intermediate_is_really_png` | guards `--padColor` argument order |
| `test_raw_sips_still_has_the_bug_the_workaround_exists_for` | moved to `test_crop_differential.py`, where the reference that needs it now lives |

### Kept, with their justification rewritten rather than their assertion

`test_crop_produces_the_exact_rect`, `test_crop_offsets_land_where_asked`,
`test_crop_marker_check_would_fail_on_a_wrong_region`,
`test_an_out_of_bounds_crop_is_refused`,
`test_the_bounds_guard_allows_a_rect_flush_with_the_edges`,
`test_every_real_geometry_slice_crops_the_region_it_asked_for`,
`test_crop_is_region_exact_on_and_off_the_bad_offsets`,
`test_crop_always_writes_png_even_from_a_lossy_source`,
`test_crop_refuses_an_out_path_that_is_not_png`,
`test_crop_still_accepts_the_name_every_caller_passes`,
`test_crop_carries_the_source_profile_through`,
`test_cropping_before_normalizing_is_the_same_picture`.

The profile one is worth noting: CoreGraphics carries the source colour space
into the cut image and ImageIO writes it, so a crop of an Adobe RGB (1998)
source still reports Adobe RGB (1998), and the crop-then-normalize ordering
`execute`'s fallback depends on still produces byte-identical pixels.

### New

`test_crop_leaves_no_intermediate_behind` replaces the pad tests with the
property that actually mattered about them: nothing is written beside the
output any more, which is what kept `<out>.padded.png` from being left in a
processing directory the executor never registered.

---

## 7. Mutations run

Every one went red. `PYTHONDONTWRITEBYTECODE=1` throughout.

| # | mutation | caught by |
|---|---|---|
| M1 | swap x and y in the CGPoint | `test_crop_to_file_honours_the_origin_it_is_given` [flush, interior], `test_crop_offsets_land_where_asked` |
| M2 | origin off by one row | 7 tests across all three files |
| M3 | bottom-left origin instead of top-left | `test_crop_to_file_honours_the_origin_it_is_given` ×3, `test_crop_at_origin_returns_the_top_region` |
| M4 | drop `crop_to_file`'s dimension post-condition | `test_crop_to_file_refuses_a_rect_that_does_not_fit` |
| M5 | never release the cut image | `test_repeated_crops_do_not_leak` |
| M6 | destination type `public.jpeg` | `test_crop_always_writes_png_even_from_a_lossy_source` |
| M7 | drop the `.png` suffix refusal | `test_crop_refuses_an_out_path_that_is_not_png` |
| M8 | bounds check uses `>=` | `test_the_bounds_guard_allows_a_rect_flush_with_the_edges`, `test_every_real_geometry_slice…` ×2 |
| M9 | drop the bounds check | `test_an_out_of_bounds_crop_is_refused` |
| M10 | swap width and height into `crop_to_file` | `test_crop_offsets_land_where_asked`, `test_crop_produces_the_exact_rect` |
| M11 | leave a stray file beside the output | `test_crop_leaves_no_intermediate_behind` |
| M12 | reference stops padding | `test_crop_matches_the_reference_on_generated_fixtures` — **and NOT the depth test, which is how §5 was found** |
| M13 | `compare` always returns `None` | `test_that_comparison_can_fail`, `test_the_harness_sees_the_pinned_difference`, `test_the_two_crops_agree_once_the_decode_is_out_of_the_way` |
| M14 | `crop` reverts to the `sips` direct call | `test_a_sixteen_bit_source_keeps_its_depth`, both origin tests, `test_every_real_geometry_slice…` ×2 |
| M15 / M16 | the megapixel threshold case, moved to the wrong side | `test_a_region_decode_of_a_big_baseline_jpeg_is_not_the_frame_decode`, both sides, both directions |
| C1 | corpus tier: origin off by one row | `test_crop_matches_sips_over_the_sample` |
| C2 | corpus tier: `compare` always returns `None` | `test_the_corpus_control_is_not_vacuous` |

M1 leaving the `y=0` case green is correct and not a gap: swapping x and y when
both are 0 is the identity, and the other two parameters catch it.

---

## 8. Verification

    uv run pytest tests/test_imaging.py tests/test_crop_differential.py   # 73 passed
    uv run pytest                                                         # 647 passed, 1m36s
    uv run pytest -m corpus -k crop                                       # 2 passed, 4m13s

The fast suite went 625 → 647: three pad tests removed, twenty-five added.
Commits: `658ba97`, `53904f4`, `2fffcb9`, `aed637e`.

---

## 9. Smaller things

* **Performance.** The pad pass is gone, and it was a full read-modify-write of
  the whole image — on the upscale path, of a frame already quadrupled. That
  work no longer happens. *(Corrected in fix round 1: I first wrote that the
  crop "is now in-process", which overstates it. `crop` still spawns one
  `sips` per call, through `probe`, for the bounds check. The subprocess count
  per padded crop went from three to one, not to zero, and reaches zero only
  when Task 7 moves `probe`.)*
* **`execute.py`'s peak-occupancy paragraph** claimed a peak of two frames plus
  a slice because of the padded copy. That copy no longer exists, and the
  paragraph now says so; the fix §7 of the spec proposed for it is moot.
* **README's test count** (526) was already stale against 625 before this task
  and is staler now. I left it, since chasing it belongs to whoever is
  maintaining that table.

---

# Fix round 1

Commit `a42e78a`. Four numbers wrong, one thing undocumented, no code
behaviour changed — `crop` and `crop_to_file` are byte-for-byte the same
functions they were.

## 1. The threshold is 1,000,000 pixels, not 1024×1024 — should-fix, fixed

Re-measured on both implementations, over a 200x150 region of synthetic noise,
samples differing of 90,000:

| size | pixels | `sips` | CoreGraphics |
|---|---|---|---|
| 1152x864 | 995,328 | 0 | 0 |
| 1024x976 | 999,424 | 0 | 0 |
| 1000x999 | 999,000 | 0 | 0 |
| 1000x1000 | **1,000,000** | 0 | 0 |
| 1250x800 | **1,000,000** | 0 | 0 |
| 1250x801 | **1,001,250** | 39,868 | 86,423 |
| 1000x1002 | 1,002,000 | 70,166 | 86,252 |
| 1008x1008 | 1,016,064 | 70,203 | 86,060 |
| 1024x1024 | 1,048,576 | 70,202 | 86,167 |

Strictly greater than a round million. Two different shapes of exactly
1,000,000 px agree; 1,001,250 does not. My earlier figures all reproduce — I
never sampled between 10⁶ and 2²⁰ and took the power of two as the boundary,
which is the guess the numbers invite and the one they do not support.

Fixed in `paperhanger/imaging.py` fact 8 (which now carries the table, and
says in as many words that the threshold is decimal rather than binary) and in
`tests/test_crop_differential.py`, where `MEGAPIXEL = 1024 * 1024` became
`ONE_MILLION_PIXELS = 1_000_000`. The constant was reaching the assertion
messages, so a failure would have named the wrong number to whoever read it.

**The parametrisation moved with it**, from `(1152, 864)` / `(1024, 1024)` —
which bracket the threshold by 53,000 pixels of area — to `(1000, 1000)` /
`(1250, 801)`, which bracket it by 1,250. The test now sits on the boundary
rather than near it.

## 2. Byte-identity was never reachable — recorded

The conclusion I did not draw, and it is the one that matters for reading §4:
the `sips` reference is *itself* a region decode, so an implementation that
matched the whole-frame decode would sit **further** from the reference than
this one does. The design's bar could not have been met by a better binding.
Written into fact 8 and into the test module's header, because "we failed to
hit byte-identity" and "byte-identity was unreachable and we landed as close
to the reference as anything could" are different sentences and only the
second is true.

## 3. The 16-bit misattribution, in both documents the next task reads — fixed

- `docs/superpowers/specs/2026-09-13-coregraphics-imaging-design.md:28` —
  the comparison table row.
- `docs/superpowers/specs/2026-09-13-coregraphics-imaging-design.md:36` —
  "an eighth silent defect, and it is ours". Now says `sips` does this on
  every path that touches pixels, that it is Apple's rather than ours, and
  carries a dated correction note saying what the paragraph used to claim.
- `docs/research/2026-09-13-coregraphics-preflight-measurements.md:1298-1299`
  — same correction, as a block quote, since a research note is a record and
  should show that it was amended rather than silently read differently.

Confirmed again, including the pad-then-crop pair the coordinator added: 8
bits out of the direct crop, the crop at 0,0, the pad alone, pad-then-crop,
and a plain resample; 16 only out of a format convert.

## 4. The sub-threshold case is the backstop, and now says so — the one that mattered

`test_a_region_decode_of_a_big_baseline_jpeg_is_not_the_frame_decode` at
`expect_agreement=True` looked like symmetry. It is the only test in the suite
making an **absolute** claim about a JPEG crop; every other JPEG assertion is
relative, comparing the two implementations to each other. The corpus gate's
control is conditional by construction — it only runs when the two sides
differ, and it re-runs them against a decoded PNG — so a crop bug that fires
only on an un-decoded source is invisible to it.

I injected the attack rather than taking it on report. A one-row origin error
keyed on `Path(source).suffix != ".png"`:

    uv run pytest -m corpus -k crop     1 passed  (5m31s)   GREEN
    uv run pytest                       1 failed of 647     RED

The single failure across 647 tests is this case. Both measurements are now in
its docstring under a `DO NOT DELETE … AS REDUNDANT COVERAGE` heading, with
the reason, so a later cleanup reading it as duplicate parametrisation has to
read the paragraph that says it is not.

Its failure message also now leads with "read this as a crop failure before
reading it as a threshold that moved", because the obvious reading of a red
sub-threshold case is the wrong one.

## 5. Three report minors — corrected in place, with the correction shown

* **The headline illustration did not reproduce.** "A `sips` crop that removes
  nothing … 5,441,250 of 10,700,856 samples" was real but mislabelled: it came
  from the padded *reference*, which crops at +1 inside a padded copy, not
  from a plain full-frame crop. Re-measured: a genuine `--cropOffset 0 0` over
  the whole frame is **byte-identical** to a plain decode. The rect must be
  strictly smaller. Replaced with 400x300 at (40, 30) of `acrylic_7049.JPG` —
  212,413 of 360,000 samples, MAE 1.0592 — which reproduces the coordinator's
  independent figure exactly.
* **`foggy_forest_path_7098.JPG` is not an exception.** 620x1102 = 683,240 px,
  under the threshold. The actual over-threshold baseline JPEG that agrees is
  `katana_with_tag_2369.jpg` (2,073,600 px) and the reason is that it is
  **single-component greyscale** — no chroma for the effect to live in. Not
  wholly immune: at an interior rect it differs in 2,294 of 345,600 samples,
  each by 1. Among *colour* baseline JPEGs over the threshold there is no
  exception.
* **"Now in-process" overstated it.** `crop` still spawns one `sips` per call
  through `probe`, for the bounds check. Three subprocesses per padded crop
  became one, not zero; zero arrives with Task 7.

## 6. Tests re-run

| command | result |
|---|---|
| `uv run pytest tests/test_crop_differential.py` | 15 passed |
| `uv run pytest tests/test_imaging.py tests/test_crop_differential.py` | 73 passed |
| `uv run pytest` | 647 passed, 1m34s |
| `uv run pytest -m corpus -k crop` | 2 passed |

Mutations re-run against the changed constants, all red:

| # | mutation | caught by |
|---|---|---|
| M15 | sub-threshold case moved past the line (1250x801 marked agreeing) | `…[sips-1250-801-True]`, `…[CoreGraphics-1250-801-True]` |
| M16 | over-threshold case moved onto the line (1000x1000 marked differing) | `…[sips-1000-1000-False]`, `…[CoreGraphics-1000-1000-False]` |
| M17 | one-row origin error keyed on the source suffix | `…[CoreGraphics-1000-1000-True]`, and nothing else in 647 |
| C3 | the same error, corpus tier | **nothing — green, which is the hole M17 exists to cover** |

C3 is reported as a pass on purpose: it is the measurement behind item 4, not
a gap left open.

## 7. Scope

No further reach into `execute.py`. It keeps the docstring-only edit from the
first round and nothing else. The two files edited outside the brief's list
this round are both documents — the design spec and a research note — and both
carry my correction rather than a silent rewrite.

---

# Fix round 2

Commit `10feb35`. One refuted explanation, one user-facing overstatement,
three stale document lines, one wrong number in a docstring. No behaviour
change again — `crop` and `crop_to_file` are untouched.

## 1. "The chroma channels are where the effect lives" — REFUTED, and it was mine

Round 1 closed the corpus-split note with that sentence. I generalised it from
one file — `katana_with_tag_2369.jpg`, greyscale and agreeing — without testing
it against anything else. It is wrong, and I re-measured rather than take the
refutation on report.

**Controlled synthetic, identical pixels, three encodings.** A 1250x801 noise
PNG (1,001,250 px, just over the threshold) encoded by `magick` at quality 85,
then cropped 200x150 at (40, 30) by each implementation and compared against
that implementation's own frame decode. Samples differing of 90,000:

| encoding | `sips` | max Δ | CoreGraphics | max Δ |
|---|---|---|---|---|
| 4:2:0 | 39,723 | 1 | 86,387 | 88 |
| 4:4:4 | 23,297 | 2 | 27,042 | 4 |
| greyscale | 0 | 0 | 2,190 | 1 |

And at 1000x1000 = exactly 1,000,000 px, every one of the three is 0 on both
sides.

**4:4:4 has no chroma subsampling and still differs. Greyscale has no chroma at
all and still differs.** So subsampling cannot be the seat of it. What it does
is amplify: 4:2:0 roughly triples the sample count against 4:4:4 and multiplies
the largest deviation about twentyfold (4 → 88 on our side). On a 4:2:0 file
the visible error is dominated by the chroma planes, which is what misled me —
dominating the error is not the same as causing it.

**Greyscale corpus counterexamples.** Through the suite's own harness, at each
file's own gate rect. I found **eight** greyscale (SOF `Nf=1`) baseline JPEGs
over the threshold in the full corpus, of which **four differ**:

| file | dimensions | result |
|---|---|---|
| `fallen_angel_drawing_2588.jpg` | 2690x3518 | 165,481 of 3,152,680, all ±1 |
| `surreal_staircase_whirlpool_3482.jpg` | 1920x1080 | 36,487 of 691,200, all ±1 |
| `black_hole_swirl_8566.jpg` | 1920x1200 | 25,492 of 768,000, all ±1 |
| `glitch_art_demon_face_6227.jpg` | 1600x900 | 6,429 of 480,000, all ±1 |
| `black_and_white_landscape_2107.jpg` | 5120x2880 | agrees |
| `black_flower_with_text_8365.jpg` | 1600x900 | agrees |
| `black_hex_000000_rgb_0_0_0_1142.jpg` | 1920x1080 | agrees (uniform black) |
| `katana_with_tag_2369.jpg` | 1920x1080 | agrees |

All four differences are accounted for by the gate's control. My count differs
from the coordinator's "three of five" — I make it four of eight, including
`fallen_angel_drawing_2588.jpg`, which was not on their list. The
discrepancy is in which files are counted as candidates, not in the
conclusion, which holds either way and is stronger at four of eight.

**The sharper fact, confirmed on real files.** On greyscale the ±1 residual is
**ours alone**. Measured at an interior rect on all four differing files — an
interior rect deliberately, so that fact 6's centred-crop defect is out of the
picture and `sips` takes its direct branch:

| file | `sips` crop vs frame | CoreGraphics crop vs frame |
|---|---|---|
| `surreal_staircase_whirlpool_3482.jpg` | **0** of 345,600 | 22,346, max 1 |
| `black_hole_swirl_8566.jpg` | **0** of 384,000 | 12,893, max 1 |
| `glitch_art_demon_face_6227.jpg` | **0** of 240,000 | 5,267, max 1 |
| `fallen_angel_drawing_2588.jpg` | **0** of 1,576,340 | 89,548, max 1 |

`sips` region crops match the frame decode exactly on every greyscale file
measured; CoreGraphics is the side that is off by one. That does not change
the gate — the control accounts for all of it and the amplitude is one level
out of 255 — but "both sides depart" is only true in general, and on greyscale
specifically it is ours that departs.

*(My first attempt at this table used the gate's own origin rect and reported
`sips` differing from the frame decode in 685,104 of 691,200 samples at max
255. That was fact 6, not fact 8 — a raw `--cropOffset 0 0` returns the
**centred** crop, so I was measuring a wrong region rather than a decode. Fixed
by moving to an interior rect. Noting it because the number was absurd enough
to be obviously wrong, and a less absurd one would not have been.)*

Written into `paperhanger/imaging.py` fact 8, which previously closed with
"Progressive JPEGs show none of it, and nor does the corpus's single-component
greyscale JPEG at the rect the gate uses" — true of that one file and
misleading about the class.

## 2. `README.md` — should-fix, fixed

"Crop no longer calls `sips` at all" was the same overstatement I softened in
report §9 last round, still standing in the user-facing document. It now says
the crop *itself* no longer goes through `sips`, that `crop` still spawns one
per call through `probe` for the bounds check, and that this is three
subprocesses per padded crop down to one rather than to zero. Also corrected
"a megapixel or more" → "more than a million pixels" there.

## 3. Three stale lines in the design spec — minors, fixed

* **`:124`** still read "the 16-bit downconversion in the pad path", surviving
  the correction I made at `:28` and `:36` of the same file. Now points at §2
  and adds Task 3's region-decode finding to the list of constraints moving to
  `docs/research/`.
* **`:80`** asserted "Old and new are byte-identical on the corpus". Corrected,
  with the reason it could not have been true of any binding.
* **`:100`** asserted the byte-identical bar is "achievable rather than
  aspirational" and that "crop is identical on every file in the corpus".
  Corrected, and narrowed to say the bar stands unchanged for the four
  operations still to move — it is crop specifically that has an unreachable
  case, not the design's approach.

Each carries a dated correction rather than a silent rewrite.

## 4. `tests/test_crop_differential.py` backstop docstring — minor, fixed

It recorded `uv run pytest -m corpus -k crop  1 passed`. That command selects
**two** tests. The 1 came from the narrower `-k test_crop_matches_sips_over_
the_sample` my mutation harness actually ran, mislabelled with the command a
reader would type. Re-ran the attack under the real command: **2 passed,
5m39s, green**. Docstring corrected to match.

## 5. Tests re-run

| command | result |
|---|---|
| `uv run pytest tests/test_crop_differential.py` | 15 passed |
| `uv run pytest tests/test_imaging.py tests/test_crop_differential.py` | 73 passed |
| `uv run pytest` | 647 passed |
| `uv run pytest -m corpus -k crop` | 2 passed |
| `uv run pytest -m corpus -k crop` under the C3 attack | 2 passed — green, the measurement behind §4 |

No mutation re-run was needed for code, because no code changed: this round
touched one docstring, one README paragraph, three spec lines and one module
docstring. The mutation battery from rounds 1 and 2 still describes the
current tests.
