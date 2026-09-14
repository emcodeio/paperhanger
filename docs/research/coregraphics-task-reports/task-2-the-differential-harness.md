# Task 2 report: the differential harness

**Status: DONE_WITH_CONCERNS**
**Commits: `ff55615`, `6c03362`**
**Tests: 621 passing, 25 deselected (was 588); `tests/test_differential.py` 27 of them.**

The concerns are not about the harness. They are three things the harness turned up in the
documents around it — one of which changes what Tasks 3 to 6 should expect their corpus
gates to print — plus a trap in the mutation method this project's reviews use.

---

## 1. What `compare` compares

```python
compare(old_fn, new_fn, source, tmp_path, suffix=".png") -> str | None
```

Both functions take `(source, out_path)` and write that path. `None` means they agree.
Anything else is a sentence naming the file and the disagreement.

**The output format is decided by the BYTES each side wrote, never by the name on the
file.** A PNG on one side and a JPEG on the other is a reported difference, not an
exception.

### PNG: decoded pixels, with the channels that give them meaning

Compared, in this order, each short-circuiting with its own message:

| # | Compared | Message shape |
|---|---|---|
| 1 | width and height | `sips wrote 40x30, CoreGraphics wrote 40x31` |
| 2 | `IHDR` colour type, and the channel count it implies | `colour type 6 (truecolour with alpha, 4 channels)` against `colour type 2 (truecolour, 3 channels) -- the alpha channel is gone from CoreGraphics' output` |
| 3 | bit depth | `sips wrote 16 bits per channel, CoreGraphics wrote 8` |
| 4 | `PLTE`, then `tRNS` | `the palettes differ at entry 1 -- sips (30, 200, 40), CoreGraphics (30, 200, 41)` |
| 5 | every sample of every pixel, all channels | `pixels differ at (3839, 2549), blue channel -- sips 73, CoreGraphics 70; 1 of 29376000 samples differ, largest difference 3` |

Decoding is done in `tests/differential.py` with `zlib` and `struct` and nothing else. It is
never done through ImageIO: **a gate that decoded with the framework under test would agree
with itself about a bitmap it had built wrongly**, which is precisely the failure GC10's
alpha clause exists to catch.

Supported so the gate reports rather than raises: colour types 0, 2, 3, 4 and 6; bit depths
1, 2, 4, 8 and 16; Adam7 interlacing; all five scanline filters.

**Not treated as differences,** because the measurement says the pixels are what matter:
interlacing, scanline filter choice, deflate level, and every chunk outside `IHDR`, `PLTE`,
`tRNS` and `IDAT`. 723 of the corpus's 894 files differ as whole files while agreeing on
every pixel; a gate that fired on those would fire on most of the corpus.

### Lossy: whole files

heic, jpeg, avif and anything else unrecognised are compared byte for byte, reporting the
first differing offset and how many bytes differ, or the two lengths. This keeps the
acceptance criterion's `offset 2` and `2 bytes`/`4 bytes` wording intact.

### It cannot pass by doing nothing

- An output file that is **absent** is reported. Both absent is still reported — two missing
  files are equal, and that must not read as agreement.
- An output file that is **empty** is reported, on the same argument.
- Each side writes into **its own directory**, so a staging file (`resize_and_encode` leaves
  a `.resized.png` sibling) cannot be read as the other side's output.
- Both directories are **emptied on entry**, so a previous call's output cannot be mistaken
  for this call's. The corpus gates call `compare` in a loop on one `tmp_path`; without this
  an implementation that wrote the fourth file and not the fifth would be compared against
  its own fourth output and pass.

An exception raised by either implementation propagates. A gate that turned `ImagingError`
into a "difference" string would let a total failure read as a pinned exception.

### One performance decision, and why it is sound

Equal headers plus an equal **decompressed** `IDAT` stream implies equal pixels, so that
case returns `None` without unfiltering. It is one-way: it proves agreement, never
disagreement. Measured on `abandoned_building_overgrown_plants_5846.jpg` as a 3840x2550 PNG
(29.4 MB of scanlines, `sips` using Paeth on 2329 of 2550 rows):

| path | cost |
|---|---|
| equal streams (the fast path) | 0.19 s |
| full unfilter, one side | 5.6 s |
| `compare` end to end when the fast path cannot settle it | 5.9 s |
| the same, with one differing sample to locate | 6.0 s |

So a tier-2 sample of 27 images costs seconds where the streams agree and about 12 s per
image where they do not. **C1 in the mutation table confirms no test's pass depends on the
fast path**: removed entirely, all 27 still pass.

---

## 2. What I changed from the brief, and why

**The brief's `compare` body compares whole files.** GC10's final form forbids that. The
brief was written before Task 0's last two rounds; its Step 3 code is a sketch of the shape.
Its three tests survive unchanged — they write non-image bytes, so they exercise the
byte path, which still exists for lossy output.

**Added `suffix=".png"`.** The brief's harness writes `_old_out` with no extension. That
breaks Task 3 on contact: `imaging.crop` refuses a name that is not `.png`, by contract
(GC7). Task 5 must pass `suffix=".heic"` / `".jpg"` / `".avif"` — the plan's Task 5 sketch
does not, and will compare two files named `out.png` holding HEIC bytes if left as written.

**`tests/pixels.py` gained `write_interlaced_png`** and `write_png`'s pixel generator was
pulled out into `_rgb_rows` so both writers emit the same image. The refactor is byte
identical to the pre-refactor `write_png` on 30 shape/colour/noise combinations, checked
against `git show HEAD~1:tests/pixels.py`. Without an interlaced input, the seven-pass
decoder six later gates depend on would ship untested.

**`tests/conftest.py` gained `interlaced_fixture`** — an eighth factory, alongside the seven
from Task 1, which are used as they stand.

**`tests/test_pixels.py` gained an outside check on the Adam7 writer.** The writer and the
decoder are both mine, and two implementations of one misunderstanding agree with each other
perfectly. `sips` renders the interlaced file and the plain one to byte-identical JPEG at
quality 100, which they can only do if they decode to the same pixels. See mutation A1: it
is the only test that notices.

---

## 3. Mutations run

Every mutation was applied to the shipped code, the suite run, and the code restored. The
prediction was written before each run.

**Method note, and it cost me a wrong answer:** a `.pyc` is invalidated on (mtime **in whole
seconds**, size). Most of these mutations insert the same ten characters — `False and ` — so
two consecutive ones land at the same file size within the same second and **the second run
imports the first one's bytecode**. My first battery reported M10 as turning the bit-depth
test red, which is impossible; run alone it turns the dimensions test red, as predicted. The
final battery removes `tests/__pycache__` and sets `PYTHONDONTWRITEBYTECODE=1` before every
run. This project mutation-tests in every review, so the trap is worth knowing.

| # | Behaviour broken | Went red |
|---|---|---|
| M1 | colour type not compared | `a_dropped_alpha_channel`, `a_greyscale_source_returned_as_rgb` |
| M2 | **the GC10 trap itself**: colour type ignored *and* only the first three channels compared | `a_dropped_alpha_channel`, `a_greyscale_source_returned_as_rgb` |
| M3 | the new side never run (`old_fn` writes both outputs) | 16 tests |
| M4 | a missing or empty output reads as agreement | `an_implementation_that_writes_nothing`, `neither_side_writing_anything`, `two_empty_files`, `an_earlier_call_s_output_is_not_reused` |
| M5 | output directories created but not emptied | `an_earlier_call_s_output_is_not_reused` |
| M6 | Sub, Average and Paeth treated as no filter | the Sub/Average/Paeth parametrizations, `a_real_sips_png_is_compared_through_its_pixels` |
| M6b | the Up filter's fast path made wrong (`a - b`) | the Up parametrization, `a_real_sips_png_is_compared_through_its_pixels` |
| M7 | interlacing ignored (Adam7 read as ordinary rows) | `interlacing_is_not_a_difference` |
| M8 | a length difference reads as agreement | `a_length_difference_is_reported`, `a_lossy_output_is_compared_whole` |
| M9 | bit depth not compared | `a_dropped_bit_depth_is_reported` |
| M10 | dimensions not compared | `differing_dimensions_are_reported` (returns `None` — the rows `zip` short) |
| M11 | format taken from anything but the bytes | 7 tests |
| M13 | both scanline size checks removed | **nothing** — see below |
| M13b | both size checks *and* `pixels._unfilter`'s filter-type validation removed | `a_png_whose_pixel_data_is_short_is_reported` |
| M14 | both sides write into one directory | 16 tests |
| M15 | **the brief's harness**: PNG compared as whole files | 16 tests, including every "must not be a difference" case |
| M16 | palette not compared | `a_differing_palette_is_reported` |
| M17 | a 16-bit sample read as one byte | `a_sixteen_bit_difference_is_reported_at_full_depth` |
| A1 | the Adam7 pass table reversed (writer *and* decoder) | `interlaced_png_holds_the_plain_writer_s_image` only — the harness's own interlace test stays **green**, which is the argument for asking `sips` |
| A2 | one Adam7 pass given the wrong y step | both the above and `interlacing_is_not_a_difference` |
| C1 | the equal-stream fast path removed | nothing, as intended — no test's pass depends on the short-circuit |
| C2 | the Up filter's fast path removed (delegating to `pixels._unfilter`) | nothing, as intended — the two paths agree |

**M13 is the one that taught me something.** Removing both size checks turned nothing red:
the short-data test is caught by `pixels._unfilter` rejecting the out-of-range filter byte
read out of the overrun, not by either size check. The checks are not redundant with each
other (one catches data too short, the other data too long) and I kept both, but the test
is pinned by the ensemble, so M13b breaks the ensemble and it goes red. Reported rather than
papered over: no single mutation of the size checks makes that test fail.

---

## 4. Concerns

### 4.1 GC10's own counts were measured with the comparison GC10 forbids — and this changes what Tasks 3-6 will see

Task 0's report states it plainly: *"every verdict in §4 is a comparison of decoded 8-bit
RGB"* (`task-0-report.md:691`). So GC10's headline — 859 identical, 10 differing in `IDAT`
with pixels identical, 25 differing in pixels — is an **RGB-only** census, which is the
comparison GC10's own alpha clause rules out.

The consequence is concrete. `task-0-report.md:663`: *"12 of 47 PNG sources are colour type
6; for all 12 `sips` writes type 6 and CoreGraphics writes type 2."* GC10 files nine of those
under "pixels identical". **Under the harness GC10 mandates, every one of those files is a
reported difference** — a colour type and channel count mismatch — because that is exactly
what the clause asks for. So a Task 3 or Task 4 corpus gate should expect on the order of
nine to twelve differences that GC10's "25 differing in pixels" does not lead you to expect,
and they are real: the alpha is gone.

I did not soften the harness to match the count. The count is the thing that is wrong, and
the record already says what the fix is: all 12 are band 3 or 4 and reach CoreGraphics
through `normalize_to_srgb_png`, where `sips --matchTo` preserves alpha (measured, Task 0
finding 5). Task 6 making CoreGraphics write type 6 is what makes the gate green, and that
is a behaviour fix rather than a pin.

**Recommendation:** GC10 gains a sentence saying its three counts are an RGB-only census and
that the colour-type assertion it requires will add the twelve alpha-bearing PNGs to the
differing side until Task 6 lands.

### 4.2 "Lossy formats match at whole-file level" rests on the design spec, not on Task 0

The design spec's measured table says JPEG at 80/90 and HEIC at 80/85/90 are byte-identical.
Task 0 did not re-measure it at corpus scale and says so: *"the HEIC encoder — every
comparison here is PNG to PNG"* (`task-0-report.md:805`). Since GC10's central new finding
is that whole-file identity is clock-dependent through the ICC profile's creation
`dateTime`, whether that reaches the lossy path is worth knowing before Task 5 gates on it.

**I measured it** (across the second boundary, both directions):

| source | jpeg 90 | heic 80 | avif 85 | png |
|---|---|---|---|---|
| untagged `write_png` fixture | identical | identical | identical | identical |
| the same tagged `AdobeRGB1998.icc` via `--matchTo` | identical | identical | identical | identical |

`sips` copies a source's profile rather than re-stamping it, so the clock does not enter a
plain encode. The instability GC10 records comes from a profile a tool *synthesises*, and I
could not reproduce that either at tier 1 (`--matchTo sRGB` twice, 1.2 s apart, identical).
So whole-file comparison for lossy looks safe, but note what is still unmeasured: **`sips`
against ImageIO on a lossy encode of an ICC-tagged source**, which is Task 5's gate and
cannot be run until Task 5's encoder exists. If ImageIO serialises a generated profile where
`sips` copies the source's, that gate will differ by a chunk of ICC and possibly by a clock.

### 4.3 The plan still carries the superseded harness

`docs/superpowers/plans/2026-09-13-coregraphics-imaging.md:512-538` is the whole-file
`compare` body. It contradicts its own acceptance criterion four lines above it and GC10
thirty lines above that. I have not edited the plan — it is the shared artifact and this is
a review decision — but someone will read that code later. It wants a "superseded by the
implementation, see `tests/differential.py`" line, and Task 5's sketch wants the `suffix`
argument (4.4).

### 4.4 Two later sketches will not run as written

- **Task 5** (`plan:~940-960`): `compare(old, new, src, tmp_path)` for `heic`/`jpeg`/`avif`
  needs `suffix=".heic"` and so on, or both sides write `out.png` holding HEIC bytes. The
  comparison itself would still be correct — the format comes from the bytes — but the
  output is misnamed, and `probe`-style assertions downstream of it would be reading a lie.
- **Task 3** (`plan:~670`): fine as written *because* the default suffix is `.png`. Worth
  knowing the default is what makes it work, since `crop` refuses anything else.

### 4.5 `pixels.read_png_rgb` remains a trap for later tasks

It refuses anything but 8-bit, non-interlaced, colour type 2 — by design, and its docstring
says so. But it is the obvious thing to reach for in Tasks 3-6, and it raises on exactly the
shapes those tasks are about (type 6, 16-bit, Adam7). Later tasks should compare through
`tests/differential.compare`, and use `read_png_rgb` only for pixel-level assertions on
output already known to be plain RGB.

---

## 5. Files

| File | Change |
|---|---|
| `tests/differential.py` | new — `compare` and the standard-library PNG decoder behind it |
| `tests/test_differential.py` | new — 27 tests in four groups: it can fail; it ignores what GC10 says to ignore; it catches what GC10 says to catch; it cannot be fooled into passing |
| `tests/pixels.py` | `_rgb_rows` extracted from `write_png` (byte identical), `write_interlaced_png` and `ADAM7` added |
| `tests/conftest.py` | `interlaced_fixture` added, the eighth factory |
| `tests/test_pixels.py` | the Adam7 writer checked through `sips`, and added to the degenerate-size parametrization |

**Verify:**

```
uv run pytest tests/test_differential.py -v     # 27 passed
uv run pytest                                   # 621 passed, 25 deselected
```

---

# Fix round 1

**Status: DONE**
**Commit: `1b395d6`**
**Tests: 625 passing, 25 deselected (was 621); `tests/test_differential.py` 31 of them (was 27).**

Four new tests, each pinning exactly one thing, each confirmed by mutating that thing.

## The two should-fixes

**`differential.py:261` — the fast path trusted the stream without the flag that says how
to read it.** Fixed: `if a.raw == b.raw and a.interlace == b.interlace`. The comment above
it claimed a soundness the code did not have and now states the condition.

Covering test: **`test_the_interlace_flag_is_read_before_the_streams_are_trusted`**. A 1x8
RGB pair, plain against Adam7, built from one 32-byte stream — four of the seven passes are
empty at width 1 and the rest emit one pixel each, so the two layouts write the same bytes.
The test asserts the two decompressed streams are identical first, or it would not be
reaching the fast path at all. Mutation **F1** (the conjunct removed) turns it red and
nothing else; before the fix `compare()` returned `None` on rows `0,4,2,6,1,3,5,7` against
`0..7`.

**`pixels.py:376` — `_paeth` was on both sides of its own test.** `test_differential.py`'s
filter encoder called it to filter and the harness called it to unfilter, so any predictor
round-tripped. Fixed by giving the encoder `_predictor`, a second transcription of RFC 2083
section 6.6, rather than a call to the first one.

Covering test: **`test_the_scanline_filter_is_not_a_difference[4-Paeth]`**, which already
existed and could not fail. Three mutations now turn it red where all 621 previously stayed
green — **F2a** (always predict the left byte), **F2b** (the b and c branches swapped),
**F2c** (the tie-break loosened from `<=` to `<`). F2c is the one worth noting: it is the
subtlest wrong Paeth anyone writes, and it is now caught.

## The minors

| Minor | Done |
|---|---|
| `:258-260` comment overclaimed | rewritten with the 1x8 counter-example in it |
| `:359-363, 381-383` — M13's negative result | **closed.** `test_scanline_data_longer_than_the_header_allows_is_reported` pins the consumed check (mutation **F3**), and M13 — both size checks removed — now turns a test red where it turned none. The `IndexError` you noticed is the remaining third: with both checks gone one malformed input raises rather than reports. I did **not** widen the `except` to catch it, and that is a judgement I would like checked — see below. |
| `:423` `_deinterlace` allocated before validating | the seven passes' total length is now checked against the stream before the grid is allocated. `test_an_interlaced_png_shorter_than_its_header_claims_is_reported` pins it (mutation **F4**) |
| `:243-251` `PLTE` byte equality | palettes are compared over `min(len)` now. `test_palette_entries_no_pixel_can_reach_are_not_a_difference` pins the new behaviour (mutation **F5** — whole-byte equality restored — turns it red) and `test_a_differing_palette_is_reported` still pins the check itself (**F6**) |

## Regression check on the first round's battery

All 22 original mutations re-run against the fixed code. Every one still turns the same
tests red, and two improved: **M7** (interlacing ignored) now turns 3 red where it turned 1,
and **M13** turns 1 where it turned 0. Both controls still pass clean — **C1**, the fast
path removed entirely, and **C2**, the Up filter's fast path removed: 31 passed each time,
so no test's result depends on either optimisation.

## The one judgement I would like checked

I left `_compare_png`'s `except ValueError` narrow rather than widening it to
`(ValueError, IndexError, struct.error)`. Widening would make the gate *report* on any
malformed output instead of erroring, which sounds strictly better — but it would also
catch an `IndexError` raised by a bug in the harness itself and print it as "CoreGraphics
wrote a PNG this harness cannot read", attributing our defect to the implementation under
test. In a gate, a misattributed failure is worse than a loud one. With both size checks in
place the `IndexError` is unreachable; it is reachable only under mutation, which is where
you found it. Say the word and I will widen it.

## Correction accepted: §4.1's forward half is overtaken

Your GC10 amendment predicted nine to twelve unpredicted corpus reports from alpha loss in
Tasks 3-6. The evidence you have — Task 1's `bitmap_format` already alpha-aware, and a real
2880x1800 alpha-bearing corpus PNG resized through a bitmap context coming back colour type
6 on both sides with `compare()` returning `None` — settles it, and it is better evidence
than the inference I drew from `task-0-report.md:663`. **§4.1's backward half stands** (the
census was RGB-only, and nine type-6 sources sat in the "identical" bucket, so the count
cannot be read as a prediction for a colour-type-aware gate). **Its forward half does not**:
the harness's strictness is being satisfied by the implementation rather than needing pins.
That result is also the end-to-end evidence I could not produce myself, since Task 2 has no
CoreGraphics encode to compare against.
