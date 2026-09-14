# Task 0 report: measure the four unknowns

**Status: DONE_WITH_CONCERNS**
**Commit: 47c5187** — `docs: preflight measurements for the CoreGraphics replacement`
**Findings: `docs/research/2026-09-13-coregraphics-preflight-measurements.md`** (493 lines)

Every acceptance criterion was closed by running the thing and capturing the output. No
criterion was closed by argument. The findings document carries the command that produced
each number.

---

## Acceptance criteria

| Criterion | Status |
|---|---|
| EXIF orientation fixture built; `sips -g pixelWidth/Height` and `CGImageGetWidth/Height` both recorded; agreement and adopted behaviour stated | met |
| Peak RSS captured for a 300 Mpx resize through both paths; which is higher and by how much stated | met, run twice (truecolor and grayscale) |
| A corpus HEIC loaded via `CGImageSourceCreateWithURL`, dimensions recorded against `sips -g` | met, both corpus HEICs |
| At least four resize shapes compared, covering downscale, enlargement, width-governed and height-governed; pass or fail per shape | met, 8 shapes in the headline table plus ~30 in boundary sweeps |
| Every number accompanied by its command | met |

`ls -1 docs/research/ | grep coregraphics-preflight` returns the file; the brief's grep
counts 29 matching lines.

---

## 1. EXIF orientation — they agree

`sips -s orientation 6` does not work: `Error: Cannot do --setProperty orientation on
file / Error 13`, and no output file is produced. This is the third tool to fail at
writing the tag, after `magick -set exif:Orientation` and `magick -orient RightTop`
during design.

**`exiftool -overwrite_original -Orientation=6 -n rot6.jpg` works.** No hand-written APP1
segment was needed. The tag was verified three ways: `exiftool -a -G1` reports it in
IFD0, `magick identify` reports `orient=RightTop exif=6`, and a walk of the JPEG segment
markers shows a real `FFE1` Exif block ahead of an `FFC0` declaring 600 x 1200 stored
pixels.

| reader | result |
|---|---|
| `sips -g pixelWidth -g pixelHeight` | 1200 x 600 |
| `CGImageGetWidth` / `CGImageGetHeight` | 1200 x 600 |

**Agree.** Neither path rotates; both operate on stored pixels. Crop geometry does not
diverge. I did not stop, and continued to the other three measurements.

I also ran the operational half, which the criterion does not require but which is what
the design was actually worried about: the same 600 x 300 resample through both paths is
pixel-identical, `AE 0 (0)`.

Two side findings, both in §1 of the document:

- **`sips -g orientation` reports `<nil>` for a tag ImageIO reads as 6.** It is not
  reading `kCGImagePropertyOrientation`. Do not use it to test for the tag.
- **`sips` copies the orientation tag into its output; ImageIO with a NULL
  destination-properties dictionary drops it.** Identical pixels, different rendering in
  any orientation-aware viewer. Task 1 has a decision to make. My read is that dropping
  it is correct — pixels that were never rotated should not carry an instruction to
  rotate them — but it is a behaviour change and belongs in the spec rather than in a
  commit message.

Corpus census confirms the design's premise: 894 files, `{None: 621, 1: 273}`, zero with
a rotating tag.

## 2. Peak memory — CoreGraphics is 30 MB higher, and that 30 MB is Python

The brief's generator command is wrong twice. `gradient:black-white` produces 16-bit
**grayscale**; adding `-type TrueColor` still palettizes (a two-stop gradient has under
256 values), and ImageIO decodes that as **Indexed**, on which `CGBitmapContextCreate`
returns NULL. `-define png:color-type=2` gets the truecolor frame the question is about.

| source | `sips` peak RSS | CoreGraphics peak RSS | difference |
|---|---|---|---|
| 300 Mpx truecolor | 1,421,705,216 B (1355.8 MiB) | 1,453,146,112 B (1385.8 MiB) | **+30.0 MiB, +2.2%** |
| 300 Mpx grayscale | 653,705,216 B (623.4 MiB) | 683,769,856 B (652.1 MiB) | **+28.7 MiB, +4.6%** |

Both outputs pixel-identical. Wall time within 2% (1.80 s against 1.84 s).

The gap is the same 30 MB at both pixel counts, so it is the interpreter and the loaded
frameworks, not imaging. Both paths materialise the whole decoded source. **The pixel cap
does not move.**

## 3. HEIC — loads, dimensions agree, and there are two of them

**The corpus holds two HEIC files, not the one the design and brief describe.** Both
measured.

| file | `sips -g` | `CGImageGetWidth/Height` |
|---|---|---|
| `purple_nebula_glow_0312_x.heic` | 7680 x 4800 | 7680 x 4800 |
| `koi_circle_2135_x.heic` | 5760 x 3240 | 5760 x 3240 |

Both decode to a single image, 8 bpc, sRGB, orientation 1. The census also found 3 WebP
and 1 GIF that the design does not mention; ImageIO opened all 894 files.

## 4. Interpolation — exact where it matters, not exact everywhere

Source: a 2000 x 1500 sRGB crop of a corpus JPEG, so photographic pixels rather than a
gradient that could hide a difference.

| shape | kind | verdict | AE |
|---|---|---|---|
| 1000 x 700 | downscale, both axes | **PASS** | 0 (0) |
| 3000 x 2100 | enlargement, both axes | **PASS** | 0 (0) |
| 800 x 1200 | portrait, height-governed | **PASS** | 0 (0) |
| 1600 x 400 | letterbox, width-governed | **PASS** | 0 (0) |
| 2000 x 1000 | width held, height reduced | **PASS** | 0 (0) |
| 4000 x 1500 | height held, width doubled | **PASS** | 0 (0) |
| 7680 x 5760 | desktop-scale enlargement | **PASS** | 0 (0) |
| 2880 x 4320 | phone target, portrait | **FAIL** | 1458 (0.000117) |

The failing shape is `PHONE_BY_WIDTH.ideal` x `PHONE_BY_HEIGHT.ideal` from
`paperhanger/sizes.py`. It was not chosen to break anything.

It is deterministic on both sides, structured (every ninth row, full width, mostly off by
one level, max delta 19), and not a wrong-constant problem: at that shape CoreGraphics
Default, Low, Medium and High all produce **the same** image and all differ from `sips`
identically. There is no constant that matches.

Boundary sweeps, roughly 30 more shapes:

- **Enlargement** fails at vertical scales 2.667, 2.88 and 3.7, and passes at 1.44, 1.92,
  2.0, 2.4, 2.5, 2.6, 3.0 and 3.84. The trigger tracks the vertical scale and ignores the
  horizontal one. It is not monotone, and uniform scaling does not rescue it.
- **Reduction** is exact at every scale from 0.10x to 0.995x and at identity, and fails
  only in a sliver just under 1.0 (0.9988x, 0.99988x), by 0.04–0.06% of bytes.

**The path production uses is exact.** `execute._refuse_to_enlarge` raises before every
resampling render unless the source is at least as large as the target on both axes, so
`resize_and_encode` only ever reduces. The equivalence bar is reachable.

Grayscale reductions are exact too, once §5 is respected.

## 5. Not asked, and it changes the design

**Building the destination bitmap from `CGImageGetColorSpace(source)` with
`kCGImageAlphaNoneSkipLast` — the construction the brief specifies — renders a grayscale
source entirely black. Silently.** The context is created, no error is raised, and a
plausible file of exactly the right size is written. At 300 Mpx: 18,712,500 of 18,750,000
bytes wrong, `PAE 65535 (1)`.

**Ten of the 894 corpus images are grayscale.** This is a live defect, not a latent one.
`normalize_to_srgb_png` would mask it — `sips --matchTo` converts grayscale to 32 bpp RGB
— but its own docstring says it runs only on the upscale path, so band 1 and band 2
renders of those ten files reach crop and resize with a Monochrome `CGImage` intact.

Deriving the alpha info from `CGColorSpaceGetModel` fixes it, and the grayscale output is
then byte-identical to `sips`. An Indexed source fails loudly (NULL) rather than
silently; no corpus file decodes Indexed, but generated fixtures do, as §2 found the hard
way.

§3.2 of the design needs a rule for this, and §5's mutation discipline needs the question
that would have caught it: would a test notice if every grayscale wallpaper came out
black?

---

## Concerns

**The verification bar as written cannot be met, on any shape.** `sips` and ImageIO never
produce identical PNG *files*. The compressed pixel stream is byte-identical — the exact
deflate stream, not merely the pixels — and the difference is container metadata: `sips`
writes `cHRM` where we write nothing, plus `gAMA`, `pHYs` and `iTXt` on real photographs.
On the smallest case the whole-file difference is exactly 44 bytes, one chunk. §5 of the
design says "the bar is byte-identical output"; Task 1 should read that as pixels, and
the gate should compare decoded pixels or `IDAT`, or every comparison will fail on
metadata and tell us nothing.

**The brief's enlargement criterion tests a path the tool forbids.** "One enlargement" is
in the acceptance criteria, and `_refuse_to_enlarge` raises on exactly that. Worth one
pinned test recording that the divergence exists, not a gate expected to stay green.

**Near-identity reductions need a decision now rather than later.** A source 0.1% larger
than its target is not hypothetical — band 1 is "measures at or above the ideal", and a
7690-wide slice resized to 7680 lands in the failing sliver. Either pin it with these
numbers or put such a shape in the differential sample deliberately.

**`test` is aliased in this shell** to `echo "Test Successful"`, so `test -f` always
"succeeds" and prints reassuring output. Confirmed: `type test` returns the alias. The
brief's Verify line uses `test -f`; I used the override given to me. Anything in this
plan that verifies with `test` is verifying nothing.

**The brief's Step 3 command does not produce a 300 Mpx colour image,** and the failure
mode is a NULL context rather than a wrong number, so it would have stopped Task 1 too.
Recorded in §2 of the findings with the working invocation.

**The corpus differs from the design's description in two places:** two HEIC files rather
than one, and 3 WebP plus 1 GIF that §4 does not name. All open through ImageIO.

## What I did not do

No subagents were dispatched. No production code was written. The instruments —
`cgbase.py`, `resize.py`, `probe.py`, `corpus_scan.py`, `decode_scan.py`, `interp_test.py`,
`sweep.py`, `dig.py`, `interp_levels.py` — live in the session scratchpad and were not
committed; Task 1 writes the real bindings from the spec.

Nothing was written into `~/Pictures/wallpaper`; files were copied out and `chmod u+w` was
applied to the copies. The corpus still holds 895 entries. `git status` after the commit
shows a clean tree, and the commit touches one file.

I did not fully reverse-engineer the enlargement trigger. It is bounded, mapped and
reproducible, and production never reaches it; further digging would have been
archaeology rather than measurement.

---

# Task 0 fix report: review round 1

**Status: DONE_WITH_CONCERNS**
**Commit: 83b19dc** — `docs: correct two overbroad preflight claims, and commit the instruments`

Every corrected figure was re-derived by running the measurement again, not edited in
place. Two of the review's own numbers did not reproduce; both are below under
"Where I disagree".

## Blocking

**`:267` — "never produce identical PNG files, on any shape" was false. Corrected.**
The reviewer is right and the claim is now conditional. `fileid.py` hashes both outputs
and lists each one's ancillary chunks. ImageIO with a NULL destination-properties
dictionary always writes the same minimal pair, `sRGB` plus a synthesised `eXIf`; `sips`
writes that pair and then forwards whatever the source carried. Identity follows from the
source:

| source | `sips` writes | whole file |
|---|---|---|
| `tests/pixels.write_png` fixture | `sRGB eXIf` | **identical SHA-256** |
| stripped PNG | `sRGB eXIf` | **identical SHA-256** |
| corpus JPEG | `sRGB eXIf pHYs iTXt` | differs |
| `magick`-made PNG | `gAMA cHRM eXIf pHYs iTXt` | differs |
| corpus PNG with an ICC profile | `iCCP eXIf pHYs` | differs |
| a PNG ImageIO itself wrote | `gAMA cHRM eXIf iTXt` | differs |

Confirmed identical across four shapes on a `write_png` fixture, including an
enlargement. The surviving half is kept, and the deflate-stream finding stands.

Two things the review did not mention, both now in the document. The **chain does not
converge**: handed a PNG carrying an `sRGB` chunk — which is exactly what ImageIO writes
— `sips` re-expresses it as `gAMA` plus `cHRM` and adds an `iTXt`, so a CoreGraphics
intermediate does not make the next stage agree. And the split is not PNG-versus-JPEG:
**every corpus JPEG** carries JFIF density, so a JPEG source produces `pHYs` and differs
too, even after `magick -strip`.

**`:15,379,382` — the reduction boundary was wrong. Corrected and re-derived.**
The reviewer is right. `boundary.py` holds the output width fixed and walks the output
height one pixel at a time, on a 7680x4800 source converted from a corpus HEIC:

```
4775   0.994792   191/192   SAME     0 bytes
4776   0.995000   199/200   DIFFER   51321 bytes, max delta 1
```

Identical boundary on an independent 3000x4800 synthetic source. **Exact to 0.994792,
divergent from 0.995000.** My "0.995 SAME" row really was 0.994871, as the review says.

## Should-fix

**`:340-382` — the sliver is vertical-only. Re-presented.** Holding the divergent height
and sweeping the width from 0.25 to 1.0 diverges at every width; putting the width deep
inside the sliver (0.9958, 0.9999) with the height below the boundary is exact at every
one. Reporting one horizontal scale per row is what hid it, and the document now says so.
A vertical scale of exactly 1.0 is also exact at every width, which matters because 577
real plans resample with the height unchanged.

**`:393` — "not hypothetical" overstated. Corrected, and re-derived rather than taken.**
`plan_scales.py` runs the real `plan.plan_photo` over all 894 corpus images and
reconstructs each resize input the way `execute.render` does, following the crop and the
4x upscale. No plan enlarges on either axis. The closest approach to identity is
**0.984627**, and that shape resamples exactly when run. Zero plans land at or above the
boundary. The sliver is now described as a latent boundary worth pinning against drift,
not a live risk.

**`:23-26,275,353` — sources unnamed, instruments uncommitted. Both fixed.** Thirteen
instruments now live in `docs/research/coregraphics-preflight/` with a `README.md` that
names and SHA-256-hashes seven corpus originals and gives the derivation command and
resulting hash for every fixture. `plan_scales.py` resolves the repository from its own
location and was re-run from the committed path to confirm it works there. The synthetic
fixtures are byte-reproducible — `tests/pixels.write_png` uses a fixed seed.

**`:418` — wrong location cited. Fixed and scoped.** The defect is at the plan's Task 4,
`_resize_to_png`, lines 826-827. The document now quotes that call and says explicitly
that Task 6's `normalize_to_srgb_png` at 1106-1107 is correct as written, because it
builds its context in a colour space it creates itself with
`CGColorSpaceCreateWithName("kCGColorSpaceSRGB")` and can never be handed a monochrome
source space. Sweeping it into the fix would be a change with no defect behind it.

**`:477-479` — why the gate misses it. Spelled out.** Task 4's differential is four shapes
over sources that trace back to `tests/pixels.write_png`, whose `IHDR` is colour type 2 —
8-bit RGB, always, with no way to ask for grayscale. Every source the gate compares
decodes RGB, so `kCGImageAlphaNoneSkipLast` is right for all of them and the four shapes
pass while ten wallpapers blacken. The four scale factors (0.5, 2.0, 0.45, 0.357) also
clear both divergent regions. A gate whose fixtures cannot express the failing input
cannot fail, which is why the fix belongs in `_cg.py`. The document now also asks Task 4
for a grayscale fixture so the defect cannot return silently.

**Minor `:264-265`** — the "real photograph" sentence is gone; the table labels that row
`magick`-made PNG. **Minor `:34`** — now "two tools that cannot, across three attempts",
and the report's "third tool" was the wrong count.

## Where I disagree

**The 75 Mpx memory figure did not reproduce, and the independence claim needs a
condition.** The review supplies +31.2 MiB at 75 Mpx and +31.4 MiB at 300 Mpx. Five runs
per configuration (`rss.py`, spread under 1.3 MiB, so this is signal):

| source | output | difference |
|---|---|---|
| 75 Mpx | 1875x2500 | **+16.9 MiB** |
| 300 Mpx | 1875x2500 | **+16.4 MiB** |
| 75 Mpx | 3750x5000 | **+30.2 MiB** |
| 300 Mpx | 3750x5000 | **+29.9 MiB** |

The gap tracks the **output bitmap**, not the source frame. The review's two figures are
reproducible if both runs held the output at 3750x5000, and read that way they show
independence from the source — which is true, and is the half that matters for the cap.
But stated without the output size they would license "the gap is ~31 MiB", and that is
wrong by nearly half at a smaller output. The document now carries the four-cell grid and
attributes the gap to roughly a fifth of the destination bitmap on top of a fixed ~12 MiB.
My original reasoning — "it is the Python interpreter" — was wrong, as the review says.

**My plan census differs from the review's 2383.** I count 3441 output plans, 2734 that
resample, 2157 reducing and 577 holding the height. The review's closest approach, 0.9805,
is my fifth-closest (0.980467); my closest is 0.984627. The conclusion is the same and
slightly stronger either way, and `plan_scales.py` is committed so the difference can be
settled by running it. I suspect a different definition of "resampling plan" — excluding
the 577 that hold the height would be one — rather than a disagreement about the corpus.

## Concerns carried forward

Unchanged from the first report: the enlargement criterion tests a path
`_refuse_to_enlarge` forbids; `test` is aliased in this shell; the brief's Step 3 command
does not produce a colour image; the corpus holds two HEICs plus 3 WebP and 1 GIF the
design does not name.

New, from this round: **three fixtures the plan names do not exist.** `photo_fixture`,
`png_fixture` and `gradient_fixture` appear throughout Tasks 1 through 4, and
`tests/conftest.py` defines none of them. The plan flags this for `png_fixture` at line
233 and says to check the real name — the real answer is that there is no such fixture at
all, and the suite's only image writer is `tests/pixels.write_png`.

---

# Task 0 fix report: review round 2

**Status: DONE**
**Commit: 3cd9bd6** — `docs: three preflight rules replaced with the measurements behind them`

All three claims were re-derived, not edited. The common fault is the one the review
names: a rule fitted to a handful of fixtures. Each is now a census or a grid, with the
instrument committed beside it.

## Should-fix

**`:286` — "ImageIO always writes `sRGB` and a synthesised `eXIf`" was false. Corrected.**
What it writes follows the source's **colour space**. Measured on PNGs cut down to `IHDR`,
`iCCP`, `IDAT`, `IEND` and nothing else (`chunkmap.py`):

| source colour space | ImageIO writes | `sips` writes | whole file |
|---|---|---|---|
| sRGB, as an `iCCP` profile | `sRGB(1) eXIf(68)` | `sRGB(1) eXIf(68)` | identical SHA-256 |
| Adobe RGB (1998), as `iCCP` | `iCCP(281) eXIf(56)` | `iCCP(281) eXIf(56)` | identical SHA-256 |
| untagged | `sRGB(1) eXIf(68)` | `sRGB(1) eXIf(68)` | identical SHA-256 |

An sRGB profile is collapsed to the one-byte `sRGB` chunk; a non-sRGB profile is carried
through as `iCCP`; the synthesised `eXIf` changes size with it, 68 against 56 bytes. The
rule is: ImageIO preserves the colour space and discards everything else.

**`:322` — both halves were wrong. Corrected, and the real driver measured.**
694 of 841 corpus JPEGs carry JFIF density, not all of them. And it does not predict
identity: on a 46-file spread, 9 matched whole-file and 6 of those 9 were carrying it.

The driver is synthesis, exactly as the review says — a JPEG has no PNG chunks to forward
and gets them anyway. In all 46 files the pixels were identical; 37 differed on chunks
only `sips` wrote (`pHYs`, `iTXt`, or both). Each has an exact trigger:

- **`iTXt` appears when and only when the source carries Exif or XMP.** 0 of 28 files with
  neither; 17 of 18 with Exif; 9 of 10 with XMP. `sips` builds an XMP packet from them.
- **`pHYs` appears when the source declares a real physical density.** The 6 JFIF-carrying
  files that produce none are precisely those whose APP0 units are 0 — an aspect ratio,
  not a density. Units 1 (72 dpi, 18 files) and 2 (118 dpcm, 2 files) all produce it.

**`:196-199` — the memory formula was a two-point fit. Dropped; grid reported.**
The review's figures reproduce: +48.0 MiB against its +49.5, and **+292.7 MiB** against its
+292.8 on a true identity resample. Nine cells now, and the last three break the old rule —
two outputs of the same 151 MB cost +48.0 MiB from a 300 Mpx source and +72.3 MiB from a
source barely larger than the output.

The identity rows also say why. `sips` peaks at 163.8 MiB on a 147 MB frame, *below* one
decoded copy, so it streams; CoreGraphics at 456.5 MiB is holding three — decoded source,
bitmap context, and the image `CGBitmapContextCreateImage` returns. Where the source
dwarfs the output that third copy is small and the gap looks constant; at identity it is
the whole frame.

I extended the grid past what the review asked for, to the two cases production actually
hits. Largest reduction at the cap, a 300 Mpx frame to the 7680x4800 desktop target:
1593.9 against 1546.7 MiB, **+3.1%** — the review's "3% at the cap", confirmed
independently. Largest identity resample the planner asks for, 10000x4780: 709.2 against
332.9 MiB, **2.1x**. `plan_scales.py` now reports that all 577 are true both-axes 1:1
resamples and names the largest. The cap holds; the document now warns Task 1 not to
assume the two paths are within tens of megabytes, because on the common case they are not.

## Minors

**README hashes.** Every `magick` derivation now passes `-strip` and all nine hashes
reproduce — verified by building twice and comparing. The README says why in place, so the
next person does not rediscover it. `-strip` removes the ICC profile as well as the
timestamps, so I re-ran the whole of section 4 on the stripped fixtures: `AE 1458.05`,
902257 differing bytes, 475 rows touched, max delta 19 — every number reproduced exactly,
since `-strip` touches no pixels. A side effect worth having: with the source stripped, the
seven passing shapes now match whole-file too, which is the corrected rule demonstrated on
a real photographic source rather than a synthetic one.

**Instrument scratch.** All seven writing instruments now allocate a
`tempfile.mkdtemp(prefix="cg-preflight-")` removed by `atexit`, and nothing is written
beside them. Re-ran each afterwards against its published numbers: boundary 51747, dig
902257, fileid `6d6f2147…`, sweep and interp_levels unchanged. `git status` is clean after
a full run, so the ignore rules in `d24dcce` are now a backstop rather than the only guard.

**Plan citations.** Now by function name — `_resize_to_png` in Task 4, `normalize_to_srgb_png`
in Task 6 — with a parenthetical saying why, since these numbers have moved twice.

## Nothing I think is wrong

Every figure in this round's review reproduced: +49.5/+48.0, +292.8/+292.7, 694 of 841,
"3% at the cap", and the `iCCP` behaviour. The 10-of-46 against my 9-of-46 is a sampling
stride difference, not a disagreement.

Your explanation of the 2383 is better than my guess and I was wrong about which subset
differed: band 4 has `needs_resize == False` but renders from the 4x frame at `scale == 4`,
so `render` computes `resize=True` and all 351 do call the resampler — at 1:1, which is
exactly the identity case that turns out to be the expensive one for CoreGraphics. Those
351 are part of the 577.

Carried forward unchanged: the enlargement criterion tests a path `_refuse_to_enlarge`
forbids; `test` is aliased in this shell; the corpus holds two HEICs plus 3 WebP and 1 GIF
the design does not name; and `photo_fixture`, `png_fixture` and `gradient_fixture` do not
exist in `tests/conftest.py`.

---

# Task 0 fix report: review round 3

**Status: DONE**
**Commit: f7c23ca** — `docs: count the universals over 894, and answer the anisotropy question`

## The instrument first

**`sourcecensus.py` crashed at full corpus size** on the WebP named `.jpg` — `facts[n]`
raised `KeyError` for any file `jpeg_segments` declined. Rewritten: it now runs the whole
corpus by default rather than a 46-file stride, treats a non-JPEG as "no segments" instead
of a missing key, counts Photoshop APP13 alongside JFIF/Exif/XMP, and reports the
conditional's hit rate. Findings 1 and 2 below were re-derived with it, not by hand.

## Should-fix

**`:330` — "pixels were identical in all 46" is false over the corpus. Corrected.**
Counted over all 894 at 800x600: **25 differ in decoded pixels**, worst 250,459 bytes at
delta 38 (`rocky_mountain_range_1569.jpg`). Split by cause with `divergence_audit.py`,
which re-runs each through a lossless PNG intermediate: **22 are a JPEG decode difference**
(they agree exactly through the intermediate) and **3 are resampler differences**, all PNG
sources, 12 bytes each. At an aspect-preserving half-size resize all 22 agree byte for
byte; only the 3 PNGs still differ. Your characterisation is confirmed in both halves.

One number of mine disagrees with itself and I have reported both: **35 sources differ in
the `IDAT` stream, 25 in the pixels it decodes to.** The ten-file gap is the two tools
choosing different PNG colour types for the same picture. A gate on `IDAT` flags ten files
a gate on decoded pixels does not; neither is wrong, and Task 4 should pick one knowingly.

**`:331` — "always by chunks only `sips` wrote" is false. Corrected.** **14 sources, all
PNG**, get an `sRGB` chunk from CoreGraphics that `sips` does not write. The difference is
not one-sided and a differential assuming the new path is a subset of the old will
mis-report them.

**`:208-211` — the streaming claim was arithmetically wrong. Replaced.** One 7680x4800
frame is 140.6 MiB; sips peaks at 163.8, which is 1.17 frames, not "below one". Restated as
a table in frames: sips **1.17** and **1.83**, CoreGraphics **3.25** and **3.89** — one to
two frames against three to four. The three-to-four reading is consistent with source plus
context plus the image `CGBitmapContextCreateImage` returns, but the measurement does not
prove which allocations those are, and the document now says so rather than asserting it.

## The aspect-ratio answer

**Production does ask for anisotropic resamples — 955 of them.** `aspect_audit.py`, over
every image and every plan the real planner produces:

```
crop rects planned                : 2562
crop rects whose aspect != target : 1086
resamples                         : 2734
resamples with x scale != y scale : 955
  ... of those, reading the ORIGINAL file : 69
  ... of those, reading an original JPEG  : 69
```

Your reasoning about `_ceil_div` is exactly right. A 16:10 slice is 16:10 only when
`width * 10` divides by 16, and `bands.output_size` then derives the second axis with
`round(height * ideal / width)`, so the scale factors differ. Largest crop departure
`1284/803` against `8/5` — 1.599004 against 1.600000. Largest resample anisotropy
x=0.93847656 against y=0.93831451, a difference of **1.6e-4**.

**What rescues it is the second condition, not the first.** 886 of the 955 read a lossless
PNG written by the crop or the upscaler, where both tools agree. Only **69 read an original
file, all JPEG** — the entire population at risk. `at_risk.py` runs all 69 at their exact
planned dimensions against their real files:

```
plans that are anisotropic AND read an original JPEG: 69
...
0 of 69 diverge
```

**So Task 4's gate can be green on the corpus and no image needs pinning for this.** The
production anisotropies are four orders of magnitude smaller than the 800x600 distortions
that provoke the divergence. But the margin is the size of the anisotropy rather than a
structural guarantee, and a one-line edit to `_ceil_div` or to `bands.output_size` would
widen it, so the document asks for a test over those 69 shapes rather than a comment.

## The pattern, written into the document

You are right that this is the finding. Round one: "never produce identical PNG files, on
any shape." Round two: "every corpus JPEG carries JFIF density." Round three: the `iTXt`
and `pHYs` triggers. Counted at full size:

| claimed rule | counterexamples over 894 |
|---|---|
| `iTXt` iff Exif or XMP | **41** — 3 carry neither and get one (all three carry a Photoshop APP13 `8BIM` block), 38 carry one and get none |
| no `pHYs` for JFIF units 0 | **151** of 276 units-0 sources write it, 9 carrying the very `(0,1,1)` triple the rule came from |
| no `pHYs` without JFIF | **93** sources with no JFIF segment get one |

Both rules are replaced by the counts — `sips` wrote `pHYs` for 697 of 894 and `iTXt` for
367 — with the mechanism (synthesis, not forwarding) kept and the trigger stated as
unresolved. The document now carries a warning under its summary table: treat any
"always", "never" or "when and only when" that does not name its population as unverified.

The one rule that survived the full count is the ImageIO colour-space rule — **833 `sRGB` +
`eXIf` and 61 `iCCP` + `eXIf`, 894 of 894** — and it now carries that count in place.

## Minors

**`:369`** — the conditional is **28 of 29**, not exceptionless, under my definition of a
bare source (no Exif, XMP, density or Photoshop block; for a PNG, no ancillary chunks).
Stated with the count and the exception. My population is 29 where yours was 126, so the
definitions differ; the instrument is committed and prints it either way.

**`README.md:67`** — corrected. `-strip` removes an ICC profile where there is one, but
`photo.png` has none: the `-colorspace sRGB` conversion ahead of it leaves no `iCCP` to
remove, so for that fixture `-strip` takes only timestamps.

**The 288 MB.** Deleted. They were pre-fix output from `rss.py`, written before the temp
directory change and left behind by it. Your `.gitignore` rule is doing what it should, but
agreed that invisible is worse than visible — `ls` in the instrument directory is clean now
and nothing writes there any more.

## One thing to flag

`divergence_audit.py` initially classified `misty_forest_landscape_2028.jpg` as a resampler
difference. It was not: `magick -strip` palettised it, ImageIO decoded an Indexed colour
space, and `CGBitmapContextCreate` returned NULL — the section 5 finding biting the
instrument that was measuring something else. Fixed by forcing `png:color-type=2` in the
intermediate, after which the file classifies as a decode difference like the other 21.
Worth noting because it is the second time Indexed has produced a wrong answer rather than
an error, and Task 1's `_cg.py` will meet it too.

## Nothing I think is wrong

Every figure in this review reproduced: 41 `iTXt` counterexamples, 151 and 93 for `pHYs`,
14 PNG sources, 250,459 bytes at delta 38, 22 decode differences, 1.17 frames, and the
`_ceil_div` reasoning. Carried forward: the enlargement criterion tests a path
`_refuse_to_enlarge` forbids; `test` is aliased in this shell; the corpus holds two HEICs
plus 3 WebP and 1 GIF the design does not name; `photo_fixture`, `png_fixture` and
`gradient_fixture` do not exist in `tests/conftest.py`.

---

# Task 0 fix report: review round 4

**Status: DONE**
**Commit: 5489302** — `docs: the at-risk population is the 159 plans that read an original, and 3 diverge`

Every figure below was re-derived by running the instruments, not transcribed from the
review. Where my number differs from the review's, both are stated.

## The instruments first, because they decide what the numbers mean

**`cgbase.raw_pixels` checks the length of what it decoded.** Every instrument compared
`magick ... RGB:-` stdout, and `magick` writes nothing to stdout when it cannot read a file,
so two failed decodes compared equal and printed SAME. The expected length is known from the
shape; a short or failed decode now raises `DecodeFailed`. One implementation, and
`sweep.py`, `boundary.py`, `dig.py`, `interp_test.py`, `interp_levels.py`,
`divergence_audit.py`, `sourcecensus.py` and `at_risk.py` all route through it. It takes a
channel set, which is what made the RGBA measurement below possible.

**`divergence_audit.compare()` raises instead of returning `None`.** The old sentinel read
as agreement in pass one and as a RESAMPLER difference in pass two — the mechanism behind
its own misclassification of `misty_forest_landscape_2028.jpg`. `main` now counts failures,
names them, excludes them from `examined`, and exits non-zero. Its second pass also refuses
to classify an alpha-bearing source, because `png:color-type=2` flattens the alpha and the
intermediate is then a different picture.

**`at_risk.py` counts only the jobs that ran** — `3 of 159 that RAN diverge (0 failed to
run, and a failure is not an agreement)` — and its population is now the mechanical one.
Four at a time, all 159 in 2 m 59 s.

**`sourcecensus.py` no longer calls an `IDAT` comparison "pixels differ".** It measures both
in one pass and prints the gap with its cause. It also prints every divergent name rather
than the first twelve, the whole `pHYs` table rather than an excerpt, and, with
`--identical`, the list behind "whole files identical".

## Blocking 1 — the 159, and the three that diverge

Confirmed independently; the counts match the review digit for digit. The predicate comes
from `render`: no crop rect, no upscale, needs a resize — with `scale == 1`,
`resize = needs_resize or scale != 1` is just `needs_resize`, so band 1 and nothing else.

```
plans whose resampler reads the ORIGINAL file: 159
  anisotropic : 69     isotropic : 90     of those, identity : 4
  source types: heic 1, jpeg 157, png 1   distinct images: 159

3 of 159 that RAN diverge (0 failed to run, and a failure is not an agreement)
  green_leaf_closeup_2463.jpg       identity  55159573 of 117964800 (46.8%), maxdelta 87
  roadside_grass_2121.jpg           identity  37495832 of 117964800 (31.8%), maxdelta 61
  sunlight_through_leaves_8375.jpg  identity  53868500 of 117964800 (45.7%), maxdelta 97
  ... all three agree exactly through a lossless PNG intermediate -> DECODE difference
```

All 69 anisotropic plans agree. Of the four identity plans that read an original, the three
JPEGs diverge and `purple_nebula_glow_0312_x.heic` agrees — 3 of 3 on original-JPEG
identity. `dig.py` at 7680 x 5120 adds what was not measured before: both tools are
deterministic, every row and column is touched, 45% of the differing bytes are off by one
and the tail reaches 87 — two JPEG decoders disagreeing, not a resampling phase error.

"No image needs pinning" is gone. The gate is stated as the mechanical one, and `at_risk.py`
is what runs it.

## Blocking 2 — the residual risk named the wrong trigger

Confirmed and re-derived. The document no longer says the decode difference needs a
distorting shape: these three need none, and all three agree at 800 x 600. The trigger is
the pair (source, scale), stated as unresolved rather than guessed at for a fourth time.

The arithmetic, re-derived from the 25 divergent files' own dimensions: the smallest
anisotropy that provokes a divergence at 800 x 600 is 0.014493 (`mountain_peaks_9739.jpg`
and `rocky_mountain_range_1569.jpg`, both 6900 x 4600) against the largest production
anisotropy of 1.62e-4 — 89.5x, and 872x against the largest in the divergent set (0.141270).
Two orders of magnitude at most, not four. `aspect_audit.py` now also prints the largest
anisotropy among the plans that read an original, 1.30e-4 (`green_leaves_1265.jpg`), beside
the 1.62e-4 headline, which belongs to a crop-fed PNG resample.

## The should-fix list

**3. Nine colour types and one interlace.** Confirmed, and the instrument now prints both
`IHDR`s. `autumn_leaves_8716.png` is the corpus's only Adam7 source — checked by reading all
47 PNG headers: 34 type 2 (one interlaced), 12 type 6, 1 type 0, no palettes.

**4. The 22/3 split.** Today's run over all 894: decode differences 22, not decode
differences 0, not attributable (alpha) 3. No demonstrated resampler difference anywhere in
the 25.

**5. The alpha channel.** New instrument `alpha_audit.py`, new §4 subsection. 12 of 47 PNG
sources are colour type 6; for all 12 `sips` writes type 6 and CoreGraphics writes type 2.
The differing bytes are the corner pixels and they are the source composited onto black —
255 x 182/255 = 182, and (7,8,9) x 189/255 = (5,6,7). Two things the review did not have: an
RGBA comparison fails on 4 of 12, not twelve; and all 12 plan as band 3 or 4, so none
reaches the resampler from its original — they reach CoreGraphics through
`normalize_to_srgb_png` (Task 6), where `sips --matchTo` preserves alpha (measured).

**6. "Four orders of magnitude."** 89x, above.

**7. Summary row 4.** Rewritten; it no longer imports the 35 into a row about interpolation.

**8. The warning.** Replaced with the review's formulation, and the §4 line making the same
claim is now scoped to the chunk censuses. The "577 real plans … are all safe" sentence is
gone, and so is "the planner never resamples a photo more than once", which was not measured
and is not true as written.

**9. The `pHYs` block.** Replaced by a four-row summary over JFIF units summing to all 841
JPEGs, with a sentence saying the old block was 8 of 24 rows.

**10. "Whole files identical."** Added — and it turned up a real finding, below.

**11, 12, 13.** The three instrument holes, above.

**14. README `-strip`.** Measured: 25 chunks removed from `photo.png` — `sRGB`, `gAMA`,
`eXIf` (4778 bytes), `bKGD`, `pHYs`, `tIME` and 19 `tEXt` — four of them timestamps. The
review counted 20 `tEXt`; separately it is 19 `tEXt` plus `tIME`, same 25.

**15. "All 69 matched byte for byte."** Gone with the subsection. The document now says once
that every verdict in §4 is a comparison of decoded 8-bit RGB.

## Two findings that were not on the list

**Skipping the draw fixes two of the three, not three.** The brief said all three problems
disappear together. Measured: a 1:1 `CGContextDrawImage` is a pixel no-op — on a 400 x 300
synthetic and on the 7680 x 5120 lossless frame of `green_leaf_closeup_2463.jpg` itself, the
drawn output equals the source pixels exactly. So skipping and drawing write the same
pixels, and the three divergences are a JPEG decode difference that skipping leaves
untouched. Skipping removes the 2.1x peak and 577 resamples that resample nothing; the three
still need pinning. The document says so, and says what remains unmeasured: whether skipping
is byte-identical to drawing in the finished file, what it does to grayscale and
alpha-bearing sources (where the draw is also the normalisation), and how it compares
against `sips`, which resamples at 1:1 rather than skipping.

**Neither tool writes the same file twice.** Chasing the 172-against-171 discrepancy:
`determinism.py` runs each tool twice over all 894 — sips disagrees with itself on 13 files,
CoreGraphics on 11, decoded pixels identical in all 24. Repeating one file until the second
ticks over locates it: the `iCCP` chunk, same length, decompressed profile differing at
exactly one offset, 35 — the ICC header's creation `dateTime`. Both tools stamp the current
second into the profile they write. So for the 61 sources whose output carries an `iCCP`,
whole-file identity is a coincidence of both writes landing in the same second, and a gate
that compares whole files must exclude them or mask that field. The tier-1 fixtures are
clear: `tests/pixels.write_png` carries no profile.

## Where I disagree with the review, with counts

- **"An RGBA comparison fails on all twelve."** It fails on 4 of 12. The structural claim
  behind it is right and stronger: 12 of 12 lose the channel.
- **"Today this bites the intermediates rather than the finished files."** The path claim is
  right — 12 of 12 are band 3 or 4 — but the consequence is understated: the upscaler would
  be handed pixels composited onto black, and `sips --matchTo` preserves alpha, so this is a
  behaviour change in Task 6 rather than a metadata difference.
- **"22 JPEG, 1 PNG" decode split.** The instrument attributes 22, all JPEG, and declines to
  attribute the three PNGs. `brush_circle_8107.png` does agree through the intermediate, but
  that intermediate is a flattened picture, so calling it a decode difference claims more
  than the test can support.
- **"Whole files identical: 171 of 894."** Not stable: 172, 171, 171, 172, 172 across five
  runs, and the cause is the ICC timestamp above.
- **The brief's "all three problems disappear together."** Two of three, measured.

## Carried forward, unchanged

The enlargement criterion tests a path `_refuse_to_enlarge` forbids; `test` is aliased in
this shell; the corpus holds two HEICs plus 3 WebP and 1 GIF the design does not name;
`photo_fixture`, `png_fixture` and `gradient_fixture` do not exist in `tests/conftest.py`.

## Hygiene

No subagents. No production code. Nothing written to `~/Pictures/wallpaper` — 895 entries,
nothing newer than `.DS_Store` at 14:56; every instrument writes to a `tempfile.mkdtemp` it
removes at exit. No image committed: commit 5489302 touches 15 files, all `.md` or `.py`. A
porcelain status over the instrument directory, ignored files included, shows only
`__pycache__/`.

---

# Task 0 fix report: review round 5

**Status: DONE**
**Commit: a37c9d4** — `docs: the draw introduces the divergence, and skipping it removes all three`

The round-4 report above states the wrong conclusion — that the three divergences are a
decode difference skipping would leave untouched. It stands as the record; this corrects it.

## Claim 2 — refuted, and re-derived here rather than taken

`skip_draw.py` (new) writes all three outputs for one shape — `sips`, the draw, and a skip
that hands `CGImageSourceCreateImageAtIndex`'s image straight to the destination — and
compares the concatenated `IDAT` streams with no `magick` in the loop. All three plans at
their real 7680 x 5120:

| plan | `sips` vs draw | `sips` vs skip | draw vs skip |
|---|---|---|---|
| `green_leaf_closeup_2463.jpg` | 46.8%, maxdelta 87 | **IDAT identical** | 46.8%, maxdelta 87 |
| `roadside_grass_2121.jpg` | 31.8%, maxdelta 61 | **IDAT identical** | 31.8%, maxdelta 61 |
| `sunlight_through_leaves_8375.jpg` | 45.7%, maxdelta 97 | **IDAT identical** | 45.7%, maxdelta 97 |
| `purple_nebula_glow_0312_x.heic` | identical | identical | identical |

Your numbers reproduce exactly, including the byte counts from the round-4 `at_risk` run.
**The draw introduces the divergence.** Skipping it removes the divergence, the 2.1x peak
and the 577 no-op resamples together, and nothing needs pinning if Task 4 skips.

Two things I added. **It is not a property of those three files**: two identity sweeps over
the corpus (`--sweep`, 60 sources from the start and 60 from offset 400, `IDAT` only) give
`sips` == skip on **120 of 120**, with the draw disagreeing with both on **89** of them. And
**the relabelling has to reach further than `at_risk:225`**: two of the 22 that diverge at
800 x 600, `rocky_mountain_range_1569.jpg` and `misty_forest_landscape_2028.jpg`, produce
identical `sips`/draw/skip output at their own dimensions — so those two decode identically
in both tools, and their 800 x 600 disagreement is not a decode difference either. The 22
are now described as "removed by re-encoding the source", in the document and in
`divergence_audit.py`'s own output.

## The five fixes

**1. `:1050`.** Replaced with the measurement. The section now says one choice in `_cg.py`
does make those three agree — not drawing them — carries the table above, and states that
skipping removes all three problems together. Summary row 4 and the tier-2 paragraph follow
it: "Task 4 skips the draw at identity, or it pins those three."

**2. `at_risk.py:225`.** The `DECODE difference` label is gone. It now prints "agrees once
the source is re-encoded as PNG (cause not isolated; run skip_draw.py)", and the module
docstring says why the test is confounded. `divergence_audit.py` got the same treatment:
its two categories are now "removed by re-encoding the source" and "survives re-encoding",
and today's run prints 22 / 0 / 3 under those names.

**3. What skipping does not license.** Two bullets in the document: the condition is the
dimensions and nothing else (2157 of 2734 resamples still reduce, and still draw); and where
the draw IS the conversion, skipping it deletes the conversion — it is what renders a
grayscale source through §5's fix and what composites the alpha of the 12 RGBA PNGs, so
Task 6's `normalize_to_srgb_png` must keep drawing. §5 carries the same warning in place, so
nobody reads "skip the draw" as retiring the alpha-info rule. Also recorded as unmeasured:
the whole file (the skip's output differs from `sips`'s by ~5.8 KB of metadata on
`green_leaf_closeup_2463.jpg`, 77,106,022 against 77,111,822, `IDAT` identical), and the
HEIC encoder — every comparison here is PNG to PNG.

**4. The new universal.** The warning now names this shape of error explicitly: a counted
claim generalised past its population, with "a 1:1 draw is a pixel no-op" — two PNG
fixtures, false on 3 of 3 real JPEGs — as the example, and "counting a sample does not make
it the population". The premise that excluded the other 2,575 resampler calls is gone; the
population is still the one `render` defines, and the untested half is now stated as
untested: whether a resample whose input is a pipeline-written PNG can diverge at a
production shape was not run.

**5. Minors.** Offset 35 is the seconds field's low byte and held over all 23 observations,
but a run pair straddling a minute boundary would move byte 33 as well — the document says
so rather than implying a mechanism. And it is **eleven** modules that import
`raw_pixels`/`raw_rgb`, not ten: `skip_draw` is the eleventh, added this round.

## Added, unasked

**Nothing observable predicts which sources the draw alters.** `probe.py` reports
`green_leaf_closeup_2463.jpg`, the lossless PNG cut from it, and a synthetic gradient
identically — 8 bpc, 32 bpp, `model=RGB name=kCGColorSpaceSRGB` — and the draw alters the
first and neither of the others. The JPEG container does not separate them either: 601 of
841 corpus JPEGs are baseline and 240 progressive, 553 carry the 4:2:0 sampling factors of
the three, and the two files that agree at identity sit on opposite sides of both splits.
Recorded as a negative result so the next person does not repeat the hunt.

## Where I disagree

Nothing in this round's review. Two refinements rather than disagreements: the draw-differs
behaviour is common (89 of 120) rather than a property of the three, so "the three need
pinning" was never really about those files; and the confounded-diagnostic relabel needed to
reach `divergence_audit.py` and the document's own description of the 22, not only
`at_risk.py:225`.

## Hygiene

No subagents. No production code. Nothing written to `~/Pictures/wallpaper`; every
instrument writes to a `tempfile.mkdtemp` it removes at exit. Commit `a37c9d4` touches five
files, all `.md` or `.py`, and adds one instrument.
