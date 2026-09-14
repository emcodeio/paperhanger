# Task 6 report: colour normalization on CoreGraphics

**Status: DONE_WITH_CONCERNS** — the work is complete and all three tiers are
green; the concerns at the end are findings for review, not unfinished work.

**Commits**

- `7b8ece7` feat: normalize through CoreGraphics, and the last sips write goes
- `bb01eac` docs: the colour conversion is CoreGraphics too, and sips only reads now

Files: `paperhanger/_cg.py`, `paperhanger/imaging.py`,
`tests/test_normalize_differential.py` (new),
`tests/test_imaging.py` (three tests rehomed), `README.md`.

---

## 1. What happens at equal dimensions, and the test that catches a skip

**The draw always runs.** There is no dimensions test in
`_cg.normalize_to_srgb_png_file` at all — the code path for "source is
600x400, destination is 600x400" is the same single path every other source
takes: load, build an sRGB context at the source's dimensions, draw, read
back, write PNG. Since normalization never resizes, equal dimensions is not
an edge case here, it is *the* case, on every photo in every run.

A skip would be silent. Measured, a 600x400 noise PNG tagged with each
profile, comparing a pass-through (decode, `write_png`, no draw) against the
`sips --matchTo` reference through `tests/differential.compare`:

| source profile | samples differing | largest difference |
|---|---|---|
| Adobe RGB (1998) | 661,630 of 720,000 | 144 |
| ROMM RGB (ProPhoto) | 713,202 of 720,000 | 167 |
| Display P3 | 675,884 of 720,000 | 116 |
| greyscale, no profile | colour type 0 vs `sips`' 2 | structural |
| grey+alpha | colour type 4 vs 6 | structural |
| indexed | colour type 3 vs 2 | structural |
| RGBA at alpha 128 | 240,000 of 960,000 | 1 |
| colour-key `tRNS` | 240,000 of 960,000 | **255** |
| **untagged RGB** | **none — agrees** | **0** |
| **interlaced untagged** | **none — agrees** | **0** |
| **CMYK JPEG** | **none — agrees** | **0** |

The last three rows are why the fixture choice matters more than the
assertion. They are also the whole of the blind set: **a skip is caught
either by a tagged source or by a structural difference**, and the rows
that catch it structurally — greyscale, indexed, alpha — are untagged
fixtures. The three that see nothing are a plain untagged 8-bit RGB image,
the same image interlaced, and a CMYK JPEG, the last because the PNG
encoder converts a CMYK `CGImage` to sRGB by itself. (The first edition of
that file's module docstring said "every test below that would catch a skip
uses a tagged source", which contradicted the structural row immediately
above it. Corrected in fix round 1 — see the appendix.)

**Which tests catch it** (all verified by mutation — M1, below):

1. `test_a_source_already_at_its_own_dimensions_is_still_converted`, three
   profiles. Two halves, deliberately: it monkeypatches
   `_cg._CG_LIB.CGContextDrawImage` and asserts exactly one draw happened
   (watching the call, because a 1:1 draw and a skip produce the same file
   for an sRGB source), it asserts `probe(source)[:2] == probe(out)[:2]` so
   the test states its own premise rather than assuming it, and it asserts
   the output still matches `sips` (watching the call says nothing about
   what the call did).
2. `test_normalize_matches_the_matchto_reference`, four of five rows — every
   row except untagged.
3. `test_the_numbers_actually_move` — the source's pixels and the output's
   differ. A skip fails it by definition.
4. `test_the_alpha_channel_survives`, `test_a_greyscale_source_comes_back_rgb`,
   `test_a_16_bit_source_comes_back_8_bit`,
   `test_the_models_the_resampler_refuses_normalize_anyway` — the four
   structural conversions a skip would also delete.
5. `test_the_harness_would_catch_a_skip` — the gate guard: the harness
   reading a pass-through as equal to a conversion would make all of the
   above vacuous, so it is asserted directly.
6. `test_an_untagged_source_cannot_catch_a_skip` — the negative result that
   decided the fixtures, recorded so it is not rediscovered.
7. Tier 2: `test_normalize_matches_sips_over_the_sample` fails under the
   skip mutation as well (verified; see M1). **13 of the 27 sample images
   catch it**, not the two wide-gamut ones I first named — measured by
   running the pass-through against the reference over the sample.
   `moss_with_pine_needles_5324.jpg` (ProPhoto RGB) differs on 71,296,904
   of 72,000,000 samples at a largest difference of 132;
   `katana_with_tag_2369.jpg` is caught structurally, colour type 0 against
   `sips`' type 2; ten more differ on colour by 1 to 88. The 14 blind ones
   are the sRGB and untagged photographs.
8. Pre-existing, not mine: `test_normalize_converts_to_srgb_png` and
   `test_cropping_before_normalizing_is_the_same_picture` in
   `tests/test_imaging.py` also fail under the skip.

A second skip is refused for the same reason and is more tempting, because
it would fire on 714 of 895 corpus entries: "the source is already sRGB, so
pass it through". The draw converts four things at once — colour space,
colour model, bit depth, alpha — so that skip would also stop converting
greyscale to RGB, indexed to truecolour and 16-bit to 8. "Tagged sRGB" is
three different profile descriptions in this corpus (`sRGB IEC61966-2.1`,
`GIMP built-in sRGB`, `sRGB IEC61966-2-1 black scaled`, plus 96 `c2`) and
not one colour space object. Both refusals are argued in the function's
docstring so the next reader does not have to rederive them.

## 2. Alpha: what the twelve corpus PNGs get

They keep their channel. The destination's alpha info is derived from the
source through `_rgb_alpha_info`: `kCGImageAlphaPremultipliedLast` when the
decoded image carries alpha, `kCGImageAlphaNoneSkipLast` when it does not.
That rule is now stated once and shared with `bitmap_format`'s RGB branch,
which had the same two lines inline.

Measured against `sips --matchTo`, which preserves alpha: RGBA, grey+alpha
and colour-key `tRNS` fixtures all come back **byte-identical in decoded
pixels, colour type, channel count and depth**, colour type 6 out of colour
type 6 and 4 alike.

The agreement is a property of the conversion, not of one fixture. Flat RGBA
at alpha 0, 1, 64, 128, 200, 254 and 255 all agree, and so does a
300x200 RGBA image whose colour *and* alpha vary per pixel. (This matters
because the premultiply round trip costs a unit of precision in
`_resample`'s 1:1 draw — there `sips` does not premultiply and we do; here
both sides do, so it cancels.)

The plan's `kCGImageAlphaNoneSkipLast` would have flattened all three
fixtures to opaque colour type 2, and the corpus census says all twelve
alpha-bearing PNGs plan as band 3 or band 4, so all twelve reach this
function. Mutation M2 is exactly the plan's line: it fails the three alpha
tests and nothing else in the suite. Those three tests are the whole
distance between the plan as written and twelve wallpapers composited onto
black.

## 3. Test summary and the corpus differential

```
uv run pytest tests/test_normalize_differential.py -v   38 passed, 2 deselected
uv run pytest                                          753 passed, 33 deselected
uv run pytest -m corpus -k normalize                      2 passed, 784 deselected
```

The fast suite was 715 before this task; 38 new tests, and no test lost.
The corpus gate compares all 27 coverage images: **27 of 27 agree** on
decoded pixels, colour type, channel count and depth, including the Adobe
RGB, ProPhoto RGB, Generic RGB, two `c2`, Generic Gray Gamma 2.2, untagged
RGBA PNG, WebP-named-`.jpg`, GIF and HEIC members. (I ran the same
comparison standalone before writing the gate, to see the per-file result
rather than only the assertion: 27 AGREE, 0 DIFFER.)

Three tests in `tests/test_imaging.py` were reachable only through this
function, because it was the last `sips` invocation in the package that
wrote a file. Two of them said in their own docstrings that Task 6 should
move them to `upscale` rather than delete them; all three now run against
`upscale` with a stub binary — an exit-0-with-no-output stub for fact 4's
post-condition and the destination clearing, and an exit-13-with-stderr stub
for `_run`'s status branch. Nothing was deleted.

### Measurements, and which files they came from

| measurement | source |
|---|---|
| profile census: 714 sRGB IEC61966-2.1, 96 `c2`, 23 GIMP built-in sRGB, 21 untagged, 19 Adobe RGB, 9 Generic Gray Gamma 2.2, 3 black-scaled sRGB, 3 ProPhoto RGB, 2 Generic RGB, 2 sRGB, 1 Calibrated RGB, 1 iMac, 1 non-image | `sips -g profile` over all 895 entries of `~/Pictures/wallpaper` |
| skip cost, the table in §1 | `tests/pixels.write_png(noise=True)` at 600x400, tagged with each system profile via `sips --matchTo` |
| alpha agreement across seven alphas and a varying-alpha image | `pixels.write_rgba_png` 300x200, plus a hand-built noisy RGBA |
| interpolation is irrelevant at 1:1 (Default, None, Low, High → the same 721,292 bytes) | one 600x400 noise PNG |
| 16-bit → 8-bit matches `sips` | `pixels.write_png16` 400x300 |
| every container out as PNG | `resize_and_encode` holders at jpeg 90, heic 80, avif 85, plus a real WebP via `magick` |
| ITU divergence, 29/25/18/8 levels at device 28/74/135/203; mean absolute difference 20.83 (2020) and 16.73 (709) over a noise image | flat 8x8 patches at 16/64/128/200 and a 200x150 noise PNG, tagged `ITU-2020.icc` / `ITU-709.icc` |
| peak RSS 494.6 MiB (this) vs 479.2 (`sips`), 21.5 of ours being the interpreter | `green_leaf_closeup_2463.jpg`, 7680x5120, `/usr/bin/time -l`, two runs per route |
| a failed `crop` leaves a stale destination, 74 bytes | an 8x8 PNG at the out_path of a crop whose source does not exist |
| corpus differential, 27 of 27 | the 27 files in `tests/corpus_sample.txt` |

Numbers that are properties of a fixture rather than of the phenomenon, said
so where they appear: the 721,292 bytes is one file's size, not a constant;
the 240,000-of-960,000 alpha rows are 400x300 fixtures; the `tRNS`
difference of 255 is that fixture's key colour against black, and is the
largest difference *available*, not an average.

## 4. Mutations run

Sixteen, all **red**. Every one was applied to the real source file, with
every `__pycache__` cleared and `PYTHONDONTWRITEBYTECODE=1` set, then
reverted; the harness refuses to run if the patch does not apply or matches
more than once. Test set per mutation:
`test_normalize_differential.py`, `test_imaging.py`,
`test_resize_differential.py`, `test_cg.py` (175 tests, 50s).

| # | mutation | red |
|---|---|---|
| M1 | skip the draw when dimensions match (**the trap**) | 25 tests, and the tier-2 corpus gate separately |
| M2 | hardcode `kCGImageAlphaNoneSkipLast` (the plan's line) | the 3 alpha tests, and only those |
| M3 | hardcode `kCGImageAlphaPremultipliedLast` | 21 |
| M4 | draw into the SOURCE's colour space (`bitmap_format`) | 22 |
| M5 | 16 bits per component always | 24 |
| M6 | bit depth from the source | 1 — `test_a_16_bit_source_comes_back_8_bit` |
| M7 | swap `_rgb_alpha_info`'s two branches | 37, including five resize-differential rows and four `test_cg` unit tests — the shared helper is covered from both sides |
| M8 | `CGColorSpaceCreateWithName` with `kCGColorSpaceGenericRGB` | 26 |
| M9 | halve the draw rect | 21 |
| M10 | no destination clearing in normalize | 2 |
| M11 | unguarded unlink in `_cleared` | 2 |
| M12 | drop the `left_behind` annotation | 1 |
| M13 | make the test file's `_passthrough` actually convert | the 3 `test_the_harness_would_catch_a_skip` rows |
| M14 | run the untagged negative result with a tagged fixture | `test_an_untagged_source_cannot_catch_a_skip` |
| M15 | write the conversion as JPEG instead of PNG | 26, including `test_a_webp_named_jpg_comes_back_png` |
| M16 | swallow the ImagingError in normalize | 5, including all three unreadable-source rows |

Every test in the new file is killed by at least one mutation. The two that
no *implementation* mutation can kill are the two gate guards — a test
asserting the harness can fail, and a test recording a negative result — so
they were mutated from the test side instead (M13, M14).

M1 under tier 2: `uv run pytest -m corpus -k normalize` fails
`test_normalize_matches_sips_over_the_sample` with the skip in place (and
takes 474s instead of 69s, because the pixel walk has differences to
report).

## 5. What the implementation chose, and why

- **The sRGB name comes from the exported symbol**,
  `c_void_p.in_dll(_CG_LIB, "kCGColorSpaceSRGB")`. Checked rather than
  assumed, given Task 5's callbacks-struct trap: `CFGetTypeID` on it returns
  `CFStringGetTypeID` and `CFStringGetCStringPtr` reads
  `kCGColorSpaceSRGB`, so this symbol is a `const CFStringRef` — a pointer
  variable — and `in_dll` reads the pointer correctly. The callbacks case
  differed because that symbol IS a struct whose first word is a version.
  Building the CFString by hand also works and returns the same
  `CGColorSpace` pointer; the exported symbol is used because the literal is
  Apple's to change.
- **Eight bits per component**, not the source's depth. `sips --matchTo`
  drops a 16-bit source to 8, so this agrees with the reference, and it is
  the opposite of the choice `crop` and `resize_and_encode` make. The reason
  it is not an inconsistency: the only reader of this file is `upscayl-bin`,
  which emits 8-bit PNG, so a 16-bit intermediate would be discarded one
  step later at best. No corpus file is 16-bit.
- **No `CGContextSetInterpolationQuality`.** The draw is 1:1 by
  construction, and measured, Default, None, Low and High produce the same
  bytes. `_resample` pins High by asserting the call because no output can
  distinguish it; a call here could not change the answer, and no test could
  check it. The plan's snippet set it.
- **No colour-model refusal.** `bitmap_format` refuses indexed, CMYK, Lab
  and >16 bpc because those cannot be a *destination*. Here they are only a
  source, so they convert: a CMYK JPEG and a palettised PNG both match
  `sips` exactly, and the same CMYK file still raises from a resample. The
  test asserts both halves of that asymmetry.
- **`SRGB_PROFILE` deleted** from `imaging.py` — nothing in the package
  passes an ICC profile to a subprocess any more. The differential test
  owns the path now, as `test_resize_differential.py` owns its own argv.
- **`_cleared`**, the guarded destination clearing, is factored out of
  `resize_and_encode` and shared, rather than a second copy of the same
  twelve lines. `resize_and_encode`'s tests pin its exact message and still
  pass unchanged.

## 6. The ITU finding, in full

The only place this diverges from `sips` on a real profile. It is not a
defect of ours and I am confident about the direction, having arbitrated it
three ways rather than trusting a round trip:

- `ITU-2020.icc` and `ITU-709.icc` carry a parametric **type-3** `rTRC`:
  g=2.22223, a=0.90967, b=0.09033, c=0.22223, d=0.08099 — the BT.709 OETF,
  read straight out of the profile's tag table.
- CoreGraphics decodes an image tagged with either one through exactly that
  curve. `sips` uses a pure gamma 2.4 instead. On the neutral axis, device
  28 in a 2020-tagged file: **the profile's curve gives 44, CoreGraphics
  gives 44, `sips` gives 15**. The gap is not a constant and is largest in
  the shadows -- 29, 25, 18 and 8 levels at device 28, 74, 135 and 203 --
  and over a 200x150 noise image the mean absolute difference between the
  two outputs is 20.83 for ITU-2020 and 16.73 for ITU-709.
- ImageMagick's LittleCMS — a third implementation, reading the same ICC
  data — agrees with CoreGraphics **to the byte** at every level tried, and
  disagrees with `sips` by the same margin. Arithmetic done by hand from the
  profile's five parameters agrees with both.
- A round trip alone would have said the opposite: `sips --matchTo` into the
  profile and back out returns to the original numbers, because both of its
  legs use the same non-ICC curve. That self-consistency is what makes the
  wrong answer look right, and it is why the test computes the expectation
  from the profile rather than comparing implementations.
- `test_the_itu_profiles_follow_the_curve_in_the_profile` pins it, including
  the assertion that `sips` still disagrees — so if Apple ever fixes
  `sips`, the test says so rather than silently becoming decorative.
- **Zero corpus files carry either profile**, so this is latent. It is
  pinned anyway, because a future attempt to close the gap against `sips`
  should have to argue with the ICC data.

## 7. Concerns

1. **`imaging.crop` leaves a stale destination after a failure.** Measured:
   an 8x8 PNG at the out_path of a crop whose source cannot be read is still
   there, all 74 bytes, after the raise. It is the gap `_cleared` closes for
   the other two writes, and Task 2 left it. I did **not** fix it — `crop`
   is not this task's function and a fix needs its own test — and it is not
   reachable in production: both `execute` call sites register the crop
   intermediate for deletion *before* calling, under a per-plan name inside
   a per-photo workdir. Recorded in `_cleared`'s docstring with the
   measurement. Reviewer's call whether to close it now.
2. **`execute._upscale_one_plan`'s docstring justifies its ordering with a
   claim about `sips`** — "the crop runs before the normalize because…
   `sips` carries the source's profile into the crop untouched" — and both
   halves of that pipeline are CoreGraphics now. The *behaviour* is still
   pinned in pixels by `test_crop_carries_the_source_profile_through` and
   `test_cropping_before_normalizing_is_the_same_picture`, both green, so
   this is stale prose rather than a stale premise. I left it alone because
   Global Constraint 4 says `execute.py` is not modified by Tasks 1–7; it
   wants a line in Task 8.
3. **The plan named a profile that does not exist.** `PROFILES =
   ["AdobeRGB1998.icc", "ProPhoto.icc", None]` — there is no `ProPhoto.icc`
   in `/System/Library/ColorSync/Profiles`, and `profiled_fixture` calls
   `pytest.skip` for a missing profile, so that row would have skipped
   silently and the ProPhoto acceptance criterion would have been met by a
   test that never ran. ProPhoto RGB's ICC name is ROMM RGB; `ROMM RGB.icc`
   is installed, is what the corpus's three ProPhoto files decode against,
   and is what the test uses. Two more profiles were added (Display P3,
   Generic RGB Profile) because the corpus holds files tagged with both.
4. **Byte-identity holds for every fixture and all 27 sample images, but
   "byte-identical" is the harness's bar, not the whole file.** Decoded
   pixels plus colour type, channel count and depth — whole PNG files are
   out because `sips` synthesises ancillary chunks from EXIF and because an
   ICC header carries a creation timestamp, so a whole-file gate would be
   partly measuring a clock. That is Global Constraint 10's decision, not
   this task's, but the acceptance criterion says "byte-identical" and this
   is what it means here.
5. **Tier 3 is still open** (the real-upscaler gate) and is unaffected by
   this task, except that `upscayl-bin` now receives a file produced by
   CoreGraphics rather than by `sips`. The two are pixel-identical on all 27
   sample images, so nothing about that gate's premise changes — but no real
   upscaler has run against this output on this machine.

---

# Appendix: fix round 1 (documentation only) — `d18a805`

Three numbers, all inside claims that were themselves correct. No code
changed; the behaviour under test is untouched. Each one re-measured here
before editing, because a number handed to me is not a number I measured.

**1. The untagged-blindness sentence contradicted the line above it.**
`tests/test_normalize_differential.py` said "every test below that would
catch a skip uses a tagged source" one line after listing greyscale,
indexed and alpha as *structural* catches — and those three are untagged
fixtures. The narrower claim is the true one and is now what the docstring
says: a skip is caught by a tagged source OR by a structural difference,
and exactly three shapes catch it neither way — plain untagged 8-bit RGB,
the same image interlaced, and a CMYK JPEG (that one because the PNG
encoder converts a CMYK `CGImage` to sRGB on its own). No assertion that a
skip must fail rests on any of the three, and
`test_an_untagged_source_cannot_catch_a_skip` states the blindness
outright. Report §1 corrected the same way.

**2. The worked example was inverted.** The report had "the profile's curve
gives 16, `sips` gives 44". Re-measured at device 28 in an ITU-2020 file:
the profile's own arithmetic **44**, CoreGraphics **44**, `sips` **15**.
`sips` is the outlier, which is what every other paragraph said. Fixed in
§6, and the four-row table in the test docstring now carries the whole set
so the direction cannot be misread again.

**3. The "15 to 18 levels" bound does not reproduce.** Measured at the four
levels the test itself uses, both ITU profiles alike, as (device value,
ours, `sips`'):

    28 -> 44 against 15    74 -> 89 against 64
    135 -> 146 against 128  203 -> 208 against 200

which is **29, 25, 18 and 8** — wrong at both ends, and the gap is largest
in the shadows rather than constant. Over a 200x150 noise image the mean
absolute difference between the two outputs is **20.83** (ITU-2020) and
**16.73** (ITU-709); those reproduce, and are now quoted alongside the
per-level numbers. Replaced in all three places: `README.md`,
`paperhanger/imaging.py`'s `normalize_to_srgb_png` docstring, and the
`test_the_itu_profiles_follow_the_curve_in_the_profile` docstring, plus the
measurement table in §3 of this report.

Where the old figures came from, since the failure is worth naming: 20.8
and 16.7 were mean absolute *round-trip* errors against the original
untagged base image, measured while arbitrating the direction of the
disagreement — a different quantity that happened to land near the right
one. "15 to 18" was an eyeball of two of the four rows. Neither was
re-derived when the paragraph was written. The numbers now in the tree all
come from one instrument that prints the level table and the image mean
together.

**Also corrected, not asked for.** The tier-2 claim that "one Adobe RGB and
one ProPhoto RGB" file is what lets the corpus gate catch a skip. Measured
by running the pass-through against the `--matchTo` reference over the 27
sample images: **13 of 27 catch it**, including `katana_with_tag_2369.jpg`
structurally (greyscale, colour type 0 against `sips`' type 2).
`moss_with_pine_needles_5324.jpg` differs on 71,296,904 of 72,000,000
samples at a largest difference of 132 — both figures identical to the
reviewer's, from my own instrument. The 14 blind images are the sRGB and
untagged photographs, which is the same population fact the fixture choice
rests on.

**Covering tests re-run** (documentation-only change, so these confirm the
docstrings still parse and the numbers in them still hold):

- `uv run pytest tests/test_normalize_differential.py -q` → 38 passed, 2
  deselected. `test_the_itu_profiles_follow_the_curve_in_the_profile`
  (both profiles) is the one that recomputes the expectation from the
  profile's five parameters, so the four-row table in its docstring is
  checked by the test it sits on.
- `uv run pytest -q` → 753 passed, 33 deselected.
- `uv run pytest -m corpus -k normalize -q` → 2 passed, and the 13-of-27
  figure came from the same comparison run standalone over the sample.

**Anything I think is wrong:** nothing in the three items — all three were
correct findings and all three reproduce. One note on the first: the fixed
sentence now asserts that exactly three fixture shapes are blind, which is
true of the fixtures this suite has rather than of all possible inputs; any
source already in sRGB with nothing structural to convert would join them.
The docstring says "fixture shapes" for that reason.
