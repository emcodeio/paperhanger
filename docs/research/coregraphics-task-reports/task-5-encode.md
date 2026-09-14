# Task 5 report: the encode on ImageIO, and the last `sips` call in the output path

**Commits:** `c91e5b2` (feat: encode through ImageIO, and the staging file goes),
`85b3ef4` (docs: the encode is CoreGraphics too, and what the output stops carrying).

**Status: DONE_WITH_CONCERNS.** Everything the brief asked for is implemented, measured and
green. The concern is not in the implementation — it is that the measurement the brief asked
for came back in two halves, and the second half falsifies a premise Global Constraint 10
states about lossy output. Section 2 is that measurement. Sections 6 and 7 are what I think
it should change.

---

## 1. What shipped

`paperhanger/_cg.py`

- `UTI` — the four formats as uniform type identifiers.
- `_quality_options(scope, quality)` — a CFDictionary holding
  `kCGImageDestinationLossyCompressionQuality` at N/100, or `None` for lossless.
- `_destination(scope, out_path, uti, what)` — replaces `_png_destination`.
- `_write(scope, image, out_path, uti, what, options=None)` — the old `write_png` body,
  now format-agnostic. `write_png` is a two-line wrapper over it, because `crop_to_file`
  writes PNG as a property of the operation rather than as a parameter.
- `_resample(scope, image, out_width, out_height, source)` — Task 4's `resize_to_file`
  body, returning an owned image rather than writing a file. The identity skip is intact
  and is the first statement.
- `resize_and_encode_to_file(...)` — load, optionally resample, encode. One scope.
- `resize_to_file` is gone; it had no caller left. The plan anticipated this
  (`plan:1344`, "`resize_to_file` (refactored to `_resample` in Task 5)").

`paperhanger/imaging.py`

- `resize_and_encode` is now three lines: coerce the path, clear the destination, delegate.
  Signature unchanged, `execute.py` untouched.
- `STAGED_RESIZE_SUFFIX` is gone with the staging file.
- Facts 3 and 7 no longer describe calls in this file; facts 1 and 4 still do
  (`probe`, `normalize_to_srgb_png`, `upscale`). The module docstring says so.

`tests/test_encode_differential.py` — new, 34 tier-1 tests and 2 corpus tests.
`tests/differential.py`, `tests/test_differential.py`, `tests/test_imaging.py`,
`tests/test_resize_differential.py`, `README.md` — updated; section 5 has the details.

### Acceptance criteria

| Criterion | Where it is asserted |
|---|---|
| heic/jpeg/avif/png map to their UTIs | `test_the_bytes_are_the_format_that_was_asked_for`, read out of the bytes and not the extension |
| Quality N is `…LossyCompressionQuality` N/100, byte-identical to `sips -s formatOptions N` | `test_encode_matches_sips_byte_for_byte`, 7 rows |
| `quality=None` for PNG sets no key | `test_no_options_dictionary_is_built_for_a_lossless_write`, watching the `AddImage` argument |
| The staging file is gone | `test_no_staging_file_is_left_behind`, `test_nothing_intermediate_is_written_at_all` |
| Defaults unchanged: heic 80, jpeg 90, avif 85 | `test_the_shipped_defaults_are_the_ones_that_were_measured` |

---

## 2. The measurement nobody had made

The brief: *"What has never been measured is `sips` against ImageIO on a lossy encode of an
ICC-tagged source."* Measured, on a 400x300 noise PNG tagged through `sips --matchTo`.

**The first answer was that it fails.** Every tagged source differed on heic and avif, by
exactly 127 bytes, at every profile and every quality:

| source | jpeg 90 | jpeg 80 | heic 80 | heic 85 | heic 90 | avif 85 | png |
|---|---|---|---|---|---|---|---|
| untagged | ident | ident | ident | ident | ident | ident | ident |
| AdobeRGB1998 | ident | ident | **−127** | **−127** | **−127** | **−127** | −425 |
| Display P3 | ident | ident | **−127** | **−127** | **−127** | **−127** | −425 |
| Generic Gray Gamma 2.2 | ident | ident | **−127** | **−127** | **−127** | **−127** | −425 |
| ROMM RGB (ProPhoto) | ident | ident | **−127** | **−127** | **−127** | **−127** | −425 |
| ITU-2020 | ident | ident | **−127** | **−127** | **−127** | **−127** | −425 |

A constant 127 bytes across profiles of 536 to 4 528 bytes is not a profile difference, so I
took the containers apart. Both files carry the same `colr` box, same length, same bytes.
What `sips` has and we do not is a second item: `iinf` 56 bytes against 35, an `iref` of 26
bytes we do not write at all, `iloc` 44 against 30, and 66 more bytes of `mdat`.

**The confound was in the fixture.** `sips --matchTo` writes an `eXIf` chunk alongside the
`iCCP` one, so "ICC-tagged" and "carries metadata" arrived together. Rebuilding the same file
with one chunk removed at a time — whole chunks copied, CRC included, so nothing else moves:

| source chunks | jpeg 90 | heic 80 | avif 85 | png |
|---|---|---|---|---|
| neither | identical | identical | identical | identical |
| `iCCP` only | **identical** | **identical** | **identical** | identical |
| `eXIf` only | identical | −127 | −127 | −425 |
| both | identical | −127 | −127 | −425 |

**So the answer to the brief's question is yes, and the ICC profile is not the variable.**
An ICC-tagged source with no other metadata is byte-identical to `sips` at jpeg 90, heic 80
and avif 85, for AdobeRGB1998, Display P3, ROMM RGB and ITU-2020 alike. ImageIO serialises
the same profile `sips` copies. The clock GC10 worries about does not enter, and
`test_an_icc_tagged_source_encodes_byte_identically` is that measurement as a test.

The variable is EXIF. `sips` copies a source's EXIF into its output;
`CGImageDestinationAddImage` writes the picture and its colour space and nothing else. In the
HEIF family that is a whole extra item — `Exif`, plus an `iref cdsc` pointing at the picture —
and **the coded picture underneath is identical**: `sips`' `mdat` is 66 bytes of Exif item
followed by exactly our `mdat`, byte for byte, on every file I checked.

### Does whole-file comparison survive for lossy?

**At tier 1, yes, and it is now measured rather than assumed** — fixtures carry no metadata,
and the ICC row above is the case GC10's exemption was written for. `tests/differential.py`
is unchanged in behaviour; its docstring now records what the exemption rests on.

**Over real photographs, no.** Encoding each of the 27 coverage images at its own dimensions:

| format | byte-identical to `sips` | differing | picture identical under the difference |
|---|---|---|---|
| heic 80 | 6 of 27 | 21 | 21 of 21 |
| avif 85 | 6 of 27 | 21 | 21 of 21 |
| jpeg 90 | 10 of 27 | 17 | 13 of 17 |

The heic/avif differences are 127 to 2 332 bytes, every one of them `sips` carrying the
source's EXIF. The four JPEG files whose pictures differ are section 3.

So Global Constraint 10's sentence — *"heic, jpeg and avif encodes were measured
byte-identical during design"* — is true of the fixtures it was measured on and false of the
corpus. It was too narrow, and section 6 says what I think it should become. The corpus gate
in `test_encode_differential.py` therefore compares the coded picture and accounts for the
rest per file, which is what `test_crop_differential.py` already does for fact 8.

---

## 3. The second divergence: progressive JPEG

Four of the 27 have JPEG outputs whose entropy-coded streams cannot be compared at all:

| file | `sips` | ours | ours larger by |
|---|---|---|---|
| abstract_blue_texture_4503.JPG | 3 896 999 | 4 713 955 | 21.0% |
| bokeh_nature_scene_7629.jpg | 4 345 454 | 4 869 993 | 12.1% |
| mountain_landscape_sunset_5677.jpg | 11 337 145 | 12 654 502 | 11.6% |
| snowy_mountain_range_with_forest_2565.jpg | 809 714 | 892 425 | 10.2% |

All four sources are progressive (SOF2). `sips` writes SOF2; ImageIO writes SOF0. Every
baseline source agrees. Chroma subsampling is *not* the variable — a 4:2:0 source stays
4:2:0 on both sides; only the scan structure differs.

**ImageIO will write progressive if asked.** `{kCGImagePropertyJFIFDictionary:
{kCGImagePropertyJFIFIsProgressive: true}}` on snowy_mountain_range_with_forest_2565.jpg
produces 809 714 bytes — `sips`' figure exactly. So this is a decision, not a limit, and I
left it as a decision: matching `sips` means reading the source's SOF and conditionally
setting the flag, which is a source-dependent container choice this task was not asked to
make. `test_a_progressive_source_comes_back_baseline` pins it, and builds its progressive
fixture through that same option — the fixture and the claim are one measurement.

### One cause, probably

Both divergences read as `sips` using `CGImageDestinationAddImageFromSource` (or copying the
source's property dictionary), which carries EXIF, the JFIF dictionary and everything else
forward, where `AddImage` starts from the frame. I did not confirm that by disassembly and
am not claiming it; it is offered because it predicts both observations from one mechanism,
and because it is the shape of the fix if either is ever wanted.

---

## 4. What the divergence is worth in production

**EXIF cannot rotate anything in this corpus.** The one real risk of dropping EXIF is the
Orientation tag: a wallpaper whose tag said "rotate 90°" would have been displayed rotated
under `sips` and will not be now. Censused over all 894 corpus images by parsing IFD0
directly: 245 carry Orientation 1 (the identity), 1 carries Orientation 0 (not a valid value;
every reader treats it as unspecified), 123 carry EXIF with no Orientation tag, 472 JPEGs
carry no EXIF at all, 54 are not JPEG. **Zero carry an orientation that rotates.** And the
pipeline's geometry is computed from raw pixel dimensions throughout, so a rotating tag on
the output would have contradicted the plan that produced it rather than honouring it.

**Most of the pipeline had already stopped carrying EXIF.** Tasks 3 and 4 put crop and
resample on CoreGraphics, and both write PNGs through `write_png`, which has never written
metadata. So a cropped or resampled output lost its EXIF one and two tasks ago. What changes
here is the remaining path — no crop, no resample — where `sips` read the original photograph
and wrote the output directly.

**Nothing about colour changes.** The ICC profile travels with the CGImage and is written on
every path; that is the first row of section 2's second table, and the corpus gate confirms
it on 27 real files across three formats.

---

## 5. Tests

`uv run pytest tests/test_encode_differential.py -v` → **34 passed, 2 deselected**, 7.5 s.

The file is organised as: the quality mapping byte for byte (7 cases + a guard that the
comparison can fail + a guard that the number reaches the encoder at all); the ICC
measurement; the EXIF divergence, pinned from both sides; the picture under the metadata;
the container readers, guarded against vacuity; the format mapping and the refusal; the
options dictionary, watched at the call; one-pass and no-staging; the progressive
divergence; and the corpus gate.

Two guards are worth naming because they are the ones that would have let this file lie:

- `test_the_container_readers_are_not_vacuous` — `_heif_picture` and `_jpeg_scan` decide
  what the corpus gate reports, so a version of either returning `b""` would make that gate
  pass over any two files. It asserts both return something shorter than the file and
  nothing for garbage.
- `test_the_source_exif_is_what_the_two_sides_disagree_about` — asserts the difference
  **exists** with the chunk and **vanishes** without it, from the same tagged file. Only the
  pair makes it a controlled measurement rather than two files that happen to differ.

**Full fast suite: 709 passed, 31 deselected, 2:02.** Seven tests failed on first run and
each was a description that had stopped being true:

| test | what changed |
|---|---|
| `test_png_omits_format_options_entirely` | watched `_run`'s argv; no subprocess runs. Replaced by the `AddImage` watcher in the new file; a comment at its old site says where it went. |
| `test_a_lossy_format_does_pass_its_quality` | same |
| `test_failure_raises_with_stderr` | asserted `sips`' "not a valid file" on the encode branch. Both branches now fail identically in the binding layer, so it and `test_the_resize_branch_fails_in_the_binding_layer_instead` merged into one parametrized test over `resize` × (missing, directory). Fact 4 keeps live coverage through a new `test_fact_4_still_has_a_live_caller` on `normalize_to_srgb_png`. |
| `test_a_directory_input_is_also_a_silent_skip` | same |
| `test_a_nonzero_exit_raises_with_stderr` | `_run`'s exit-13 branch; retargeted to `normalize_to_srgb_png`, which still runs `sips`. |
| `test_the_staging_file_does_not_survive_a_failing_resample` | monkeypatched `_cg.resize_to_file`. Deleted with the staging file. |
| `test_the_staging_name_is_one_the_executor_already_sweeps` | the `PARTIAL_SUFFIX` coupling no longer exists. Deleted. |

Four staging tests came out of `test_resize_differential.py` in total; a comment at their old
site names the three tests that now hold what they were protecting.

**Corpus gate:** `uv run pytest -m corpus -k encode -v` → **2 passed, 738 deselected**, 44.8 s.

**Whole corpus tier:** `uv run pytest -m corpus` → **25 passed, 715 deselected, 13:09**. That
matters more than the encode gate alone, because `test_corpus_sample.py` runs the real tool
end to end over 27 photographs and is where a HEIC that `sips -g pixelWidth` could not read,
or a `_verify_dimensions` that stopped agreeing, would surface.

---

## 6. Mutations

Twelve, each applied to a single line, run, and reverted in a `finally`. `__pycache__` cleared
and `PYTHONDONTWRITEBYTECODE=1` on both sides of every run. **12 of 12 killed.**

| mutation | killed by |
|---|---|
| `quality / 100.0` → `/ 255.0` | 10 tests, every byte comparison |
| `quality / 100.0` → `round(…, 1)` | 4 tests — only avif 85 moves, which is why the CASES table carries a `…5` quality |
| `AddImage(dest, image, options)` → `…, None)` | 10 tests |
| `quality is None` → quality 100 | `test_no_options_dictionary_is_built_for_a_lossless_write` |
| heic → `public.jpeg` | 11 tests |
| avif → `public.heic` | 5 tests |
| the unknown-format refusal deleted | `test_an_unknown_format_is_refused_by_name` |
| `if resize:` → `if True:` | `test_resize_false_does_not_resample_even_when_dimensions_differ` |
| the identity skip removed | 5 tests in `test_resize_differential.py` |
| `out_path.unlink` removed | `test_a_failing_encode_leaves_no_output_behind`, `test_a_stale_destination_cannot_stand_in_for_output` |
| the Finalize result discarded | 39 tests |
| the encode reads the source, not the resample | 20 tests |

The refusal mutation is the one that changed a test. Deleting `if fmt not in UTI` still
raises — `tiff` is not a UTI, CoreFoundation builds the string, ImageIO returns NULL — and
the message still contains the word "tiff", so my first version of that test survived the
mutation. It now asserts the sentence ("is not an output format") rather than the exception.

---

## 7. Where I deviated from the brief

1. **`resize_and_encode` is three lines, not one.** The brief's Step 4 delegates directly. I
   kept `out_path.unlink(missing_ok=True)` first, because `_run(produces=...)` used to clear
   the destination on *both* branches and nothing else does now:
   `test_a_stale_destination_cannot_stand_in_for_output` pins it and dies without it. This is
   not the unlink `_cg._write` argues against — that one is about destinations ImageIO
   refuses, which it refuses before touching a file. This is about a failure *before* the
   write, an unreadable source above all.
2. **The dictionary callbacks are passed by address.** The brief's sketch is
   `c_void_p.in_dll(_cf, "kCFTypeDictionaryKeyCallBacks")`. That symbol is a *struct*, not a
   pointer to one, so `in_dll` reads its first field — the version, 0 — and hands
   CFDictionaryCreate a NULL callbacks table. I measured it: **the sketch's spelling produces
   byte-identical output** at heic 80, heic 40 and jpeg 90, because the scope holds the
   CFNumber alive past Finalize and the key is the framework's own constant. It is wrong in a
   way no output can show, which is why the docstring spells out both halves.
3. **`resize_to_file` is deleted rather than kept beside `_resample`.** The brief says "so
   both entry points share it"; after the change there is one entry point, and the plan's own
   type-consistency note expects the refactor.
4. **The corpus gate compares the coded picture, not whole files.** Section 2 is why.
5. **I updated the README.** Task 4 set that precedent (`d3e9739`), and three of its
   paragraphs described the encode as `sips`.

---

## 8. Concerns

1. **Global Constraint 10's lossy sentence is now too narrow, and it is the shared artifact
   rather than mine to edit.** It says whole-file comparison holds for heic, jpeg and avif.
   Measured over the coverage sample that is 6, 10 and 6 of 27. I would replace it with:
   *whole files for lossy where the source carries no metadata, which is every tier-1 fixture;
   over real sources the bar is the coded picture, because `sips` copies EXIF forward and
   inherits a progressive scan where `AddImage` writes only the frame.* I have edited
   `tests/differential.py`'s docstring to record the measurement, and left the constraint
   itself alone.
2. **Outputs lose the source's EXIF, and that is a product decision I made by implementing
   the brief.** Section 4 bounds the risk — no corpus file carries a rotating Orientation, and
   colour is unaffected — but "the wallpaper no longer carries the camera metadata" is a
   user-visible change that nobody explicitly signed off. If it should be preserved, the fix
   is `CGImageDestinationAddImageFromSource` on the no-resample path or copying the source's
   property dictionary, and both need thought about what a *resampled* frame should inherit
   (the source's `PixelWidth` would be a lie).
3. **`--format jpeg` on a progressive source now costs 10 to 21% more bytes.** Not the
   default format, same picture, and one nested dictionary away from matching `sips` if it
   matters.
4. **The corpus gate's accounting rule is more permissive than byte equality, by
   construction.** It accepts a HEIF pair when our `mdat` is a suffix of `sips`' and the extra
   bytes contain `Exif`. That is exactly the divergence measured, and
   `test_the_corpus_comparison_is_not_vacuous` shows the rule still rejects heic 40 against
   heic 80 — but it is a weaker bar than the resize gate's and should be read as one.
5. **The fused pass raises peak memory, which nobody asked about and Task 4 made a
   criterion.** Measured on a 7680x5120 noise PNG reduced to 3840x2160 heic 80, two runs
   each, peak RSS of whichever process does the work:

   | route | in-process | child | on disk |
   |---|---|---|---|
   | this task, one pass | **454.1 MiB** | — | — |
   | Task 4, staged | 385.0 MiB | 135.0 MiB `sips` | 22.1 MB PNG |
   | pre-Task 4, one `sips` | 18.6 MiB | 408.4 MiB `sips` | — |

   Fusing costs 69 MiB over Task 4's in-process peak and 46 over the `sips` this replaces,
   because the decoded frame, the resampled bitmap and the encoder's buffers are live
   together where the staged shape released the first two before `sips` started. It buys one
   process and no intermediate file. I think that trade is right at these sizes and it is
   recorded in `resize_and_encode_to_file`'s docstring, but it goes the wrong way and
   somebody should know which way before a frame larger than the corpus holds turns up.

---

# Round 2: the review's corrections

Six items. Two were claims I had stated wrongly, and both were cases of a **stronger** result
written as a weaker and false one. Two were guards. Two were the corpus gate. Everything
below is re-measured on this machine rather than taken from the review.

## R1. "Reproduces `sips`' file exactly" was false, and the truth is better (MEDIUM, fixed)

Measured, `{JFIF: {IsProgressive: true}}` against `sips -s format jpeg -s formatOptions 90`
on all four progressive coverage sources:

| source | `sips` | ours, asked for progressive | difference |
|---|---|---|---|
| bokeh_nature_scene_7629.jpg | 4 345 454 | 4 345 454 | **byte-identical** |
| mountain_landscape_sunset_5677.jpg | 11 337 145 | 11 337 145 | **byte-identical** |
| snowy_mountain_range_with_forest_2565.jpg | 809 714 | 809 714 | **2 bytes** |
| abstract_blue_texture_4503.JPG | 3 896 999 | 3 896 899 | **100 bytes** |

So my "reproduces `sips`' 809714-byte file exactly" was wrong by two bytes on the file I
named it for, and by a hundred on another. The two bytes are the APP0 JFIF density —
`0060 0060`, 96 dpi, carried forward against ImageIO's `0048 0048`, 72. The hundred are the
APP1 Exif segment: `sips` copies four IFD0 entries (0x00b0 bytes) where ImageIO synthesises
one (0x004c). Both are the metadata divergence again, not a coding difference.

**The claim worth making is the one I missed: the entropy-coded scan is byte-identical on
four of four**, 0.8 to 11.3 MB of it. That is what justifies pinning the divergence instead
of fixing it, and it is now the sentence in the module docstring and in
`test_a_progressive_source_comes_back_baseline`.

## R2. The JPEG-EXIF row was a fixture property stated as a container property (MEDIUM, fixed)

My comment said *"both tools drop a PNG's `eXIf` on the way to JPEG, so there is nothing to
diverge about."* False. Measured on sources built here with `zlib`/`struct`, no `sips` near
the fixture, carrying a synthesised IFD0:

| source IFD0 | `sips` APP1 | ours APP1 | whole files |
|---|---|---|---|
| Orientation 1 | 90 bytes | 78 bytes | differ |
| Orientation 6 | 90 bytes | 78 bytes | differ |
| Orientation 6 + ResolutionUnit | 102 bytes | 78 bytes | differ |

Both tools write an APP1 Exif; `sips` fills it from the source's IFD0 and ImageIO
synthesises its own. **EXIF does not travel on any of the four formats, JPEG included.** My
row passed only because `sips --matchTo`'s `eXIf` holds exactly what ImageIO re-synthesises —
the eighth instance of this project's signature failure, and mine.

Fixed in three places: the comment now says it is the fixture's property and gives the
numbers; `_cg._write`'s table carries a starred footnote saying the same; and a new test,
`test_a_jpeg_loses_the_source_exif_too`, builds a source carrying **Orientation 6** — the one
tag a viewer would see — and asserts that `sips` carries it, we do not, and the scans are
identical. That is the divergence stated as what it is.

## R3. `_run`'s unlink-first was untested (LOW, covered)

Confirmed: deleting `if produces is not None: Path(produces).unlink(missing_ok=True)` left the
whole fast suite green. It is not dead weight, though — `normalize_to_srgb_png` and `upscale`
still run `sips`, and fact 4 is `sips` answering a skipped write with exit 0 and no file, so a
stale destination satisfies the `exists()` post-condition. Measured: with the unlink, a
178-byte stale file is gone and the call raises. The guard was real and the coverage was
missing.

Covered by `test_a_stale_destination_cannot_stand_in_for_a_skipped_sips_write`, now the only
thing that kills that mutation. Its docstring says that when Task 6 moves
`normalize_to_srgb_png`, this test should follow `upscale` rather than be deleted.

## R4. The unlink raised `PermissionError`, not `ImagingError` (LOW, fixed)

Reproduced exactly: a stale output inside a directory chmod'd 0o500 gives
`PermissionError: [Errno 13] Permission denied` escaping `resize_and_encode`. `execute`
catches `ImagingError` per photo and carries on; anything else ends the run. This is Task 1's
`write_png` bug arriving in a second place and it takes the same answer — the unlink is now
`try`/`except OSError: pass`, best effort, because when the removal fails the write is about
to fail for the same reason and name the path itself. Covered by
`test_an_unremovable_stale_output_still_raises_imaging_error`.

## R5. The corpus gate's JPEG branch, and the hole my own guard found (fixed)

Two changes first. The unused `quality` parameter is gone from `_describe_encode` — it is what
let the vacuity test pass `80` for a file encoded at `40` and look like it was checking
something. And the JPEG branch no longer compares the scan alone: it compares **every segment
that is not an APPn**, so SOF, DQT and DHT are held to equality and only metadata is excused.
A changed quantization table with identical entropy data would have read as agreement before.

Then extending the vacuity guard to the JPEG branch **failed**, and the failure was real: the
first sample file alphabetically is `abstract_blue_texture_4503.JPG`, which is progressive, so
the progressive branch returned "accounted for" and swallowed a `jpeg 40` against a `jpeg 80`.
For a progressive source that branch excused *everything*.

What is still comparable there is DQT. Measured across all four progressive sources plus two
baseline ones: the quantization tables are **identical between `sips`' progressive output and
our baseline one at the same quality** (138 bytes each), and **differ at a different
quality**. So the branch now tolerates the scan and holds DQT to equality.

A frame-header check went in beside it and came out again: every dimension difference I could
construct already moves DQT (400x300 against 200x150 at quality 90 — 138 bytes either way,
different contents), so nothing reached it, and a mutation removing it left every gate green.
Deleted rather than left standing, which is what Task 1 did with the unlink that bought
nothing. `test_the_progressive_accounting_tolerates_only_the_scan` now covers both directions
of that branch at tier 1, where before only the corpus guard reached it.

## R6. The census was JPEG-only, and the arithmetic was wrong (fixed)

Two errors: I counted 54 non-JPEG where there are 53, because my total of 895 included a
dotfile for a corpus of 894 images; and I never looked for `eXIf` in the PNGs. Re-run over
whole files — the first pass read 64 KB and stopped at `IDAT`, and PNG allows `eXIf` after the
image data, which is where the ones I missed were:

| container | count | Orientation 1 | Orientation 0 | EXIF, no tag | no EXIF |
|---|---|---|---|---|---|
| JPEG | 841 | 245 | 1 | 123 | 472 |
| PNG | 47 | 12 | — | 5 | 30 |
| other | 6 | — | — | — | — |

894 files. The review's figure is confirmed: 17 of the 47 PNGs carry an `eXIf` chunk, 12 with
Orientation 1 and 5 with none. **Zero rotating orientations across all 894**, which was the
conclusion and survives.

## R7. The memory figure, corrected in both directions

The review is right that I compared against the wrong baseline, and fixing it needed a better
instrument: adding two `ru_maxrss` figures assumes both peaks happened at once. Each route's
parent blocks while its child works, so what adds is the parent's *current* resident size at
that moment. Measured, two runs each, 7680x5120 noise PNG to 3840x2160 heic 80:

| route | whole-machine high-water | how it is reached |
|---|---|---|
| this task, one pass | **454.0 MiB** | one process, nothing on disk |
| Task 4, staged | **385.0 MiB** | its own resample peak, alone — by the time it spawns `sips` it has released to 59.2, so the child phase reaches only 194.2 |
| pre-Task 4, one `sips` | **427.0 MiB** | 18.6 resident in the parent while the child peaks at 408.4 |

So fusing costs **6.3% over what shipped** and 17.9% over Task 4's shape. My report led with
the second, which is a comparison against a route that existed for one commit. Both docstrings
now lead with the first and say why. Note also that the naive sum-of-peaks reading of Task 4's
route (520 MiB) would have been wrong by 135: it never holds both at once.

## R8. The `in_dll` find, measured properly (docstring strengthened)

The review says my description of it was too kind, and it is right. Measured:
`kCFTypeDictionaryKeyCallBacks`' first word is `0x0` -- the version -- followed by five
function pointers, so `c_void_p.in_dll(...).value` comes back as literally `None` and
CFDictionaryCreate gets a NULL callbacks table. CFGetRetainCount on the CFNumber:

| spelling | retain count before -> after the insert |
|---|---|
| `ctypes.addressof(...)` | 1 -> **2** — the dictionary owns a reference |
| `c_void_p.in_dll(...)` (the plan's) | 1 -> **1** — it owns nothing |

So it is not merely "a dictionary that retains nothing", it is a **latent use-after-free**:
the dictionary holds an unretained pointer to the quality, and the only reason every format
and quality still comes out byte-identical is that the `Scope` keeps the CFNumber alive until
after Finalize. Any reordering of that scope, or any release of the number before the write,
and ImageIO reads freed memory for the quality. `_quality_options`' docstring now says that
in those terms rather than the softer version I wrote.

## What I think is still worth someone's attention

Nothing in the review looks wrong to me. Two notes:

- **My APP1 sizes are 2 bytes larger than the review's** (90 against 88, 102 against 100).
  Not a disagreement: I measured the whole segment including the `FFE1` marker, the review
  measured the payload. Same bytes.
- **R5 is the item I would look at again.** The corpus gate's JPEG branch now excuses the scan
  for a progressive source and checks DQT instead, which is a weaker bar than the HEIF
  branch's and the only place a real coding difference could hide on four of 27 files. It is
  guarded from both directions and it is the best bar I can construct without a progressive
  decoder, but it is the weakest assertion in the file and should be read that way.

## Verification, round 2

- `uv run pytest tests/test_encode_differential.py -q` → **37 passed, 2 deselected**, 7.8 s
- `uv run pytest tests/test_imaging.py -q` → **63 passed**, 18.4 s
- `uv run pytest -q` → **714 passed, 31 deselected**, 2:01
- `uv run pytest -m corpus -k encode -q` → **2 passed, 742 deselected**, 44.8 s
- `uv run pytest -m corpus -q` → **25 passed, 720 deselected**, 13:08

Nine further mutations, applied singly and reverted in a `finally`, `__pycache__` cleared
either side and `PYTHONDONTWRITEBYTECODE=1` throughout. **Eight killed, one deleted as
unreachable:**

| mutation | killed by |
|---|---|
| `_run`'s unlink-first removed | `test_a_stale_destination_cannot_stand_in_for_a_skipped_sips_write` (only) |
| `resize_and_encode`'s unlink unguarded again | `test_an_unremovable_stale_output_still_raises_imaging_error` (only) |
| `resize_and_encode`'s unlink removed | `test_a_stale_destination_cannot_stand_in_for_output`, `test_a_failing_encode_leaves_no_output_behind` |
| the JPEG rule compares whole files | `test_the_jpeg_accounting_separates_metadata_from_picture` |
| the JPEG rule compares nothing | same |
| the orientation reader always says None | `test_a_jpeg_loses_the_source_exif_too` |
| a progressive source excuses everything | `test_the_progressive_accounting_tolerates_only_the_scan` at tier 1, `test_the_corpus_comparison_is_not_vacuous` at tier 2 |
| the quantization-table reader returns nothing | `test_the_container_readers_are_not_vacuous`, `test_the_progressive_accounting_tolerates_only_the_scan` |
| the frame-header check removed | nothing — which is why it is gone |

---

# Round 3: the deletion I got wrong, and the fix with no test

Three items. The first two are the same failure in two costumes — a claim resting on evidence
from one class of case, generalised to all of them — and the first is the very thing I caught
in my own work last round, made in the other direction. I deleted a check because thirty-six
dimension comparisons could not reach it, which says nothing about the cases that can.

## R9. The frame-header check was unreachable as stated, not as a check (MEDIUM, restored)

Reproduced before restoring anything. Flipping component 0's sampling factor in our own
output — 4:2:0 to 4:4:4 and back — on two corpus progressive sources:

| source | sampling | DQT after the flip | gate's verdict |
|---|---|---|---|
| bokeh_nature_scene_7629.jpg | 0x22 → 0x11 | **identical** | `None` — accounted for |
| snowy_mountain_range_with_forest_2565.jpg | 0x11 → 0x22 | **identical** | `None` — accounted for |

So the progressive branch was calling a changed chroma sampling "the scan divergence". My
evidence for deleting the check was sound and irrelevant: every *dimension* difference moves
DQT, so DQT fires first and the frame check never runs — but dimensions were never its only
coverage.

Restored as an **SOF payload comparison**, which needed one thing checking first: the payload
must be well defined across the pair the branch exists to excuse. Measured on all four
progressive coverage sources — `sips` writes marker `0xC2`, we write `0xC0`, and the 15-byte
payload is **byte-identical on four of four**. So `_jpeg_frame` returns `payload[4:]` and the
marker is excluded by construction, not by luck. The payload carries the precision, both
dimensions, the component count and per component the sampling factors and table selector —
the picture's shape, caught directly rather than through DQT.

The residual is now named in the code rather than implied: DHT is genuinely incomparable
across the pair (197 bytes against our 183, plus a `DRI` segment `sips` does not write) and
the scan is incomparable by construction. What stands in for both is R1's measurement —
asked for progressive, ImageIO produces `sips`' own scan byte for byte on four of four,
0.81 to 11.34 MB of it — which is stronger evidence than a gate over two different scan
structures could ever be.

## R10. R5's central tightening had no test that failed without it (MEDIUM, fixed)

Confirmed: narrowing the rule back to scan equality left the fast suite at 714 and
`-m corpus -k encode` at 2 passed. `test_the_jpeg_accounting_separates_metadata_from_picture`
compares quality 90 against quality 60, which differs in the scan **and** in the tables, so it
passes under either rule — its docstring claimed a coverage the pair could not provide. The
hole I found was real and the fix was unfalsified, which is the worse of the two.

The pair that separates the rules differs in **exactly one place**: one byte inside a DQT
segment, scan untouched. Built by byte surgery rather than a second encode, because a
re-encode moves the tables and the scan together — which is precisely why the original pair
could not discriminate. `test_the_jpeg_rule_notices_a_quantization_table_difference` asserts
its own premise (scan identical, tables differ) before asserting the verdict, and it is the
sole killer of the narrowing mutation.

`_edit_segment` is the new helper: one byte of one segment's payload, everything else
untouched. Both new tests use it, and both assert the premise it is supposed to establish.

## R11. The guarded unlink dropped its cause (LOW, fixed)

Right, and it was a worse version of the bug it fixed. `except OSError: pass` left the caller
with `could not create a heic destination for <path>` and no mention of the permission or of
the earlier file still standing at that path — so the one-exception-type contract was restored
by throwing away the reason. `_cg._write` already had the shape: best effort on the removal,
and the message says the old file survived.

`resize_and_encode` now carries the `OSError`'s `strerror` and appends it to whatever
ImagingError the write raises, chaining the write's failure as `__cause__`. The message reads
`could not create a heic destination for <path>; an earlier file is still there and could not
be removed (Permission denied)`. `test_an_unremovable_stale_output_still_raises_imaging_error`
asserts all three parts and is the sole killer of the swallow-it-again mutation.

## R12. The DQT concern, closed by your sweep

Recorded because it is the answer to a question I raised and could not settle: over q1–q100, a
PNG source gives 96 distinct outputs against 96 distinct DQTs and a progressive-JPEG source 54
against 54, with **zero pairs where the bytes differ and the tables do not**. The collisions
that exist are collisions of the output itself — byte-identical files — so there is nothing for
DQT to miss. That closes concern 5 from round 1's report: DQT-to-equality discriminates
completely, and the weakest assertion in the file is now the DHT-and-scan residual above,
which R1's measurement covers from outside.

## The APP1 reconciliation

Both figures are right and they measure different things. **My 90 and 102 are whole segments,
`FFE1` marker and 2-byte length included; the reviewer's 88 and 100 are the payloads.** Every
instance in my code and docstrings means the whole segment, and the docstrings now say
"APP1 ... segment" where they give a number.

## Verification, round 3

- `uv run pytest tests/test_encode_differential.py -q` → **38 passed, 2 deselected**, 8.2 s
- `uv run pytest tests/test_imaging.py -q` → **63 passed**, 18.6 s
- `uv run pytest -q` → **715 passed, 31 deselected**, 2:01
- `uv run pytest -m corpus -k encode -q` → **2 passed, 744 deselected**, 44.5 s
- `uv run pytest -m corpus -q` → **25 passed, 721 deselected**, 13:02

Six mutations, **6 of 6 killed**, and the two that survived round 2 are each killed by exactly
one test now:

| mutation | killed by |
|---|---|
| the JPEG rule compares the scan alone again | `test_the_jpeg_rule_notices_a_quantization_table_difference` (only) |
| the frame-header check deleted again | `test_the_progressive_accounting_tolerates_only_the_scan` (only) |
| the frame-header reader returns nothing | same |
| the frame-header comparison includes the marker | same — the marker legitimately differs, so including it would fire on every progressive pair |
| the unlink failure is swallowed again | `test_an_unremovable_stale_output_still_raises_imaging_error` (only) |
| a progressive source excuses everything | same test, plus `test_the_corpus_comparison_is_not_vacuous` at tier 2 |

One process note: the shared scratchpad's mutation harness had been rewritten by another
session between rounds, and importing from it failed. The round-3 runner is self-contained —
a mutation harness that depends on a file another process can replace is a harness that can
report anything.

## What I think is wrong

Nothing in this round. The frame-header restoration is the item I would have caught myself if
I had asked "what else could this check see?" instead of "did my mutations reach it?", and
that question is now written into the branch's comment so the next person deleting it has to
answer it.
