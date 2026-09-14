# Replacing sips with CoreGraphics

**Status:** design approved, not yet implemented. Tracked as issue #3, which supersedes #2.

## 1. Why

`paperhanger` shells out to `sips` for every imaging operation. `sips` ships with macOS and needs no install, which is most of why it was chosen, and the research document that predates the design records the trade: it fails silently in seven measured ways, each producing a plausible-looking wrong file at exit 0.

One of those seven forces an extra full-image pass on every affected crop. `--cropOffset` crops from the centre unless at least one coordinate is 1 or more. Our slices are full-width horizontal thirds, so the x offset is necessarily 0, and the top and flush-bottom slices are always wrong without a workaround. `imaging.crop` pads one pixel on every side and crops at +1, which costs a complete rewrite of the image — after the upscaler has quadrupled it in each direction.

`CGImageCreateWithImageInRect` does that crop at any origin, correctly, with no workaround. It is reachable from Python's standard-library `ctypes` against the system frameworks: no third-party package, no new binary, nothing to install. Measuring it turned up that the same is true of resize and encode, and that both are byte-identical to what `sips` produces today.

So the change is not "work around the seventh defect more cheaply." It is "stop calling the tool that has seven of them."

## 2. What was measured

All figures below were measured on macOS 26.6.2 during design. Nothing here is inferred.

| Operation | CoreGraphics against `sips` |
|---|---|
| crop at origin `0,0` | correct; `sips` returns the centred region |
| crop flush to the bottom edge | correct; `sips` returns the full source |
| crop pixels against an independent reference | identical, absolute error 0 |
| resize, interpolation High | **byte-identical** |
| encode JPEG at quality 80 and 90 | **byte-identical** |
| encode HEIC at quality 80, 85 and 90 | **byte-identical** |
| Adobe RGB and sRGB profiles | preserved; the `sips` pad path converts |
| 16-bit sources | preserved; `sips` drops them to 8-bit on every path that touches pixels |
| 96 Mpx frame, 8000x12000 to 8000x4000 | 0.85 s against 1.35 s |
| 24 Mpx noisy frame, 6000x4000 to 6000x1333 | 1.68 s against 3.06 s |

Two consequences worth stating plainly.

**The quality defaults transfer unchanged.** `sips` is built on ImageIO, and quality 80 there is `kCGImageDestinationLossyCompressionQuality` 0.80 here — byte-for-byte, on both JPEG and HEIC. The reasoning behind the current defaults survives intact, including the finding that HEIC 85 produces a file byte-identical to 80, which reproduces through ImageIO as well. ImageIO can write `public.heic`, `public.avif`, `public.jpeg` and `public.png`, so every format the tool offers is available.

**`sips` has an eighth silent defect: it downconverts 16-bit sources to 8-bit.** Every PNG and TIFF in the corpus is 8-bit, so nothing is affected today; it is latent rather than active.

*Corrected during Task 3.* This paragraph used to say the defect was the pad path's, and therefore ours rather than Apple's. Measured on a 16-bit 200x200 PNG, `sips` returns 8 bits from the direct crop, from the crop at 0,0, from the pad alone, from the pad-then-crop pair, and from a plain resample; only a format convert with no pixel operation keeps 16. So it belongs with the other seven: Apple's, on every path that touches pixels, and removing the workaround does not remove it. It was found by mutation — switching the retained reference to its direct branch left the depth assertion green, which it should not have done if the pad were the cause.

### 2.1 Alternatives ruled out

**A different `sips` invocation.** The behaviour is publicly documented rather than a local discovery: `cropOffset` needs at least one coordinate of 1 or more, or the crop comes from the centre. Since the slices span the full width, x is always 0. No flag avoids it.

**Image Events**, the AppleScript image application built into macOS. Its `crop` command takes only target dimensions and always centres — it has no offset parameter at all. Strictly worse than `sips`.

**The `sips` JavaScript API** (`sips -j`, an HTML Canvas backed by CoreGraphics, present since Big Sur). It cropped all three slices correctly, including both bug cases, which made it briefly the obvious answer. It is colour-managed: it tags every output Display P3 and shifts pixels by about 1%, even from sRGB or untagged input, and a `{colorSpace: "srgb"}` argument is accepted and ignored. Not a lossless cut.

## 3. Architecture

**One module owns every line of `ctypes`.** A new `paperhanger/_cg.py` holds the bindings. Nothing else in the project imports `ctypes`.

This gives the codebase a third layer beneath the two it has. The pure decision layer computes in integers and touches no filesystem; the effect layer performs operations and reports failures as `ImagingError`; and now the binding layer, which is the only code that can take the process down. Containing the risky part in one reviewable file is the point.

`imaging.py` keeps its public functions and their contracts exactly as they are. `execute.py` does not change, including its two `crop` call sites.

### 3.1 Memory

Every CoreFoundation `Create` or `Copy` returns an object we own and must release. This is not bookkeeping. A leaked 96 Mpx `CGImage` is roughly 380 MB, and a full run processes 894 photos, so a leak invisible in a unit test exhausts memory partway through a twenty-four hour run.

Every owned handle goes into a scope object that releases on exit, whether the operation succeeded or raised. No release is left to a `return` path remembering to call it.

### 3.2 Failure

`_cg` never returns NULL to its caller and never passes NULL into a CoreGraphics function. Every call goes through a checked helper that turns a NULL into an `ImagingError` naming the file and the step.

That contract is what preserves the executor's existing model: one exception type, caught per photo, the run continues and the photo is reported failed. Most FFI crashes come from a NULL from a failed load being handed to the next function, so checking every return converts nearly all of them back into the failure the executor already handles. The crashes that remain are bugs in our bindings, which is what the tests are for.

### 3.3 What does not change

`upscale` stays a subprocess call to `upscayl-bin` with the same failure handling. Replacing `sips` does not mean removing subprocesses; it means removing one tool whose quirks are documented seven times over.

## 4. The operations, and the order they move

Each operation is swapped behind the unchanged `imaging.py` interface, one at a time, with a differential gate after each. The order is chosen so that a red gate localises to the operation that just moved.

**1. `crop`.** The only operation where the new code is more correct rather than merely identical, and the one with the measured defect. Its body collapses from two branches to one: `_offset_is_ignored`, `_crop_via_padding` and `PAD_COLOUR` all disappear, and with them the magenta pad and its bleed.

The bounds check stays. It is the guard that has to survive, because `execute.py` crops the 4x frame using `rect.scaled(4)`, and an enlargement that comes back a pixel short would overrun and produce a black-edged wallpaper with nothing raising.

Its gate carries one deliberate exception, on a 16-bit source, where `sips` downconverts and the new path does not. That difference is intended, so it is pinned as a named exception with its justification rather than passing quietly.

*Corrected during Task 3.* This paragraph opened "Old and new are byte-identical on the corpus, every file of which is 8-bit." **They are not, and no binding could have made them so.** ImageIO decodes a *region* of a baseline JPEG differently from the whole frame above 1,000,000 pixels, and `sips` shows it as plainly as CoreGraphics does — so the retained reference is itself a region decode, and an implementation matching the frame decode would sit *further* from it. 14 of the 27 sample images differ. The gate accounted for each difference rather than tolerating it: given the same decoded pixels the two implementations agreed exactly. That gate is retired with the reference; what survives it is the threshold and the absolute claim below it, in `tests/test_imaging.py`, and the measurements in `docs/research/2026-09-11-replacing-pixelmator-and-imagemagick.md` section 12. See §5.

**2. The resample**, at `kCGInterpolationHigh`. Proven byte-identical. The gate should be clean with no exceptions; a red gate means the binding is wrong, not the plan.

`kCGInterpolationDefault` measured identical to High on the case tested, but the implementation sets High explicitly. Relying on Default would make the output depend on whatever Apple decides Default means.

**3. The encode**, at the same quality number. Also proven byte-identical, same expectation.

Steps 2 and 3 are the two halves of one existing function. `resize_and_encode` keeps its name and its signature throughout; what moves in two steps is its internals, so that a red gate says which half is at fault. Splitting the public function is not part of this design.

**4. `normalize_to_srgb_png`**, becoming a colour-space conversion rather than a `--matchTo` invocation. Its contract is unchanged: the upscaler receives sRGB PNG.

**5. `probe`**, last and deliberately so. It has the widest blast radius of the five, because it decides what counts as an image at all, and both `bands.py` and `cli.py` depend on that answer. It also gets simpler: non-image detection stops parsing `sips` stdout and becomes "ImageIO returned nothing," which is an answer rather than a string match.

### 4.1 Outside `imaging.py`

`cli.py` pre-flights `/usr/bin/sips` and `doctor` reports on it. Both exist because a missing or quarantined `sips` would otherwise report an entire library as unreadable. With no external binary in the imaging path, that failure class stops existing, and those checks change shape rather than surviving unchanged. The README's troubleshooting entry for it goes too.

## 5. Verification

**The bar is byte-identical output** wherever byte-identity is reachable. Resize and encode were measured byte-identical during design. The known departures for crop are a 16-bit source, where the new path is correct and the old one is not, and §4's region decode; §4 pins the first as an exception rather than lowering the bar to accommodate it.

*Corrected during Task 3.* This paragraph said the bar was "achievable rather than aspirational" and that "crop is identical on every file in the corpus". For crop that is false on any baseline JPEG over 1,000,000 pixels, which is most of the corpus — **and it is false for a reason no implementation choice could remove**, since the `sips` reference is itself a region decode and differs from `sips`' own frame decode by the same kind of margin. Both sides depart; neither is the better decode. Task 3's gate therefore asks a question that *is* answerable — hand both implementations the same decoded pixels and require exact agreement — and it fails on anything that control cannot account for. The bar stands unchanged for the four operations still to move.

*Corrected during Task 5.* The lossy rule was "whole files, because that was measured to hold", and the measurement was too narrow. It holds where the source carries no metadata of its own: an ICC-tagged source with **no EXIF** encodes byte-identically whatever the profile -- AdobeRGB1998, Display P3, ROMM RGB, ITU-2020 and Generic Gray all match at jpeg 90, heic 80, avif 85 and png -- and the profile header's creation clock does not enter, because `sips` copies a profile rather than re-stamping it. The constant that exposed the confound is worth keeping: every tagged source differed by *exactly* 127 bytes across profiles of 536 to 4528 bytes, which no profile difference can produce. The variable was an `eXIf` chunk the fixture's own `sips --matchTo` had written.

Over real photographs whole files do **not** match: heic 6 of 27, avif 6 of 27, jpeg 10 of 27. Every difference is metadata and the **coded picture is identical in 21 of 21** -- `sips`' `mdat` is 66 bytes of Exif item followed by exactly ours. So for real sources the bar is the coded picture (the `mdat` payload for HEIF, the entropy-coded scan for JPEG), with the remaining bytes accounted for per file rather than tolerated. `sips` copies the source's metadata forward, inherits its progressive scan, and on JPEG carries its IFD0 tags into the Exif APP1 where ImageIO writes only what it derives.

**The old implementation survives the migration as a test-only reference.** That is what makes a differential bar workable without keeping two production paths. Each gate compares the new output against the `sips` reference over the 27-image sample; the reference is deleted at the final swap. Any difference either fails the build or is pinned as a named exception carrying its justification.

**Both tiers.** A tier-1 differential on generated fixtures so the comparison runs every commit, and the tier-2 run over real corpus images for the input that is actually messy: wide-gamut profiles, the file whose extension lies, the HEIC source.

**Parts of the existing suite test `sips`, not us.** The pad-workaround tests go with the workaround. The argument-order assertions — `-s format` silently dropped after `--padColor`, both axes via `--resampleHeightWidth` — describe a tool we would no longer call; they become tests of the new resize or they go. The contracts worth keeping are about our own behaviour: crop refuses a non-PNG name, crop refuses an out-of-bounds rect, resize hits both axes exactly.

**Mutation discipline applies to the bindings, with a different question.** For FFI the question is whether a test would notice a missing `CFRelease` or an unchecked NULL, neither of which changes output and both of which matter. A test that runs several hundred crops and watches resident memory is worth more here than another assertion about dimensions.

## 6. Measure before implementing

Four things are unknown. They are listed here rather than left to be discovered mid-implementation, because any of them could change the design.

*All four resolved; recorded 2026-09-14.* Task 0's preflight was written to answer exactly these, and each was then pinned by the task that depended on it. **None of them changed the design.** The four statements below are kept as written — what was unknown at design time is part of the record — with each resolution appended, and **two of the stated facts were refuted by the measurements**. The refutations are marked where they occur rather than edited away.

**EXIF orientation.** Untested; no synthetic fixture could be constructed during design. The corpus carries no orientation tags on any of its 894 files, but photos straight off a phone routinely do. If `sips` applies orientation where ImageIO returns raw pixels, crop geometry diverges silently for those files. This needs a real measurement and an explicit decision about which behaviour is correct.

> *Resolved by Task 0's preflight and Task 5's census.* `sips` and ImageIO agree — both return 1200x600 for an orientation-bearing source — so crop geometry does not diverge and no decision was needed.
>
> **The stated fact is false.** Measured over the corpus: **257 files carry Orientation 1**, one carries an invalid 0, 128 carry EXIF with no Orientation tag, and the rest carry none. What is true is the narrower claim the design actually needed: **none of the 894 carries a ROTATING orientation.** Orientation 1 means "as stored", so the pixels and the geometry are the same either way. `README.md` carries the breakdown.

**Peak memory at 300 Mpx.** The pixel cap exists because whole-frame upscaling strains memory, and a bitmap context for a 300 Mpx resize allocates the entire bitmap. Whether the new path's peak is better or worse than the `sips` path should be known before committing, since it may move the cap.

> *Resolved by Task 0's preflight: **the cap does not move.*** CoreGraphics' peak RSS sits a constant ~30 MiB above the `sips` route at both pixel counts measured, and constant means it is the Python interpreter and the `ctypes` bindings rather than the imaging. Confirmed again per operation as each moved, on a 7680x5120 frame: the resample skip 351.0 MiB against `sips`' 332.1 and the whole-frame draw's 499.2, and `normalize_to_srgb_png` 494.6 against `sips --matchTo`'s 479.2. The numbers live in the docstrings of the functions they were measured on.

**HEIC input** through `CGImageSourceCreateWithURL`. The corpus contains one HEIC source; only PNG and JPEG inputs were tested during design.

> *Resolved by Task 0's preflight and Task 6's corpus differential.* It loads, the dimensions agree, and the encode side was measured byte-for-byte.
>
> **The stated fact is false: the corpus contains TWO HEIC sources, not one.** Task 0 found the second. It is pinned as `"heic": 2` in `CORPUS_FORMATS` (`tests/test_imaging.py`), which a tier-2 test enforces as counts over all 894 files, and reflected in `_cg.SOURCE_FORMATS` and `README.md`.

**Whether interpolation High stays exact** across aspect ratios, and on enlargement as well as reduction. One downscale was tested.

> *Resolved by Task 0's preflight, its round-3 computation, and Task 4 — and this is the one of the four whose answer was NO.* 7 of 8 shapes matched; 2880x4320 failed at a mean absolute error of 1458, and that shape is a phone target out of `sizes.py`. It was then isolated: the divergence is a **JPEG decode** difference and not a resampler one — both tools agree exactly through a lossless PNG intermediate — and it appears only at aspect-DISTORTING shapes. 22 of 894 corpus sources diverge, up to 250,459 differing bytes at a delta of 38, and all 22 match at four aspect-preserving scales.
>
> Whether production ever asks for an anisotropic resample was then **computed rather than assumed**, over all 894 images and every plan the real planner produces. It does: 1086 of 2562 crop rects are not the target's aspect and 955 of 2734 resamples have an x scale different from their y scale, caused by `geometry.py`'s ceiling division. What rescues it is the second condition — 886 of those 955 read a lossless PNG intermediate, and all 69 that read an original JPEG diverge **zero** times at their planned dimensions. The residual risk is the size of the anisotropy (1.6e-4) rather than a structural guarantee, which is why it is written down here. Task 4 then held the identity-skip condition under attack across thirteen shapes without finding an input where the draw does work the skip omits.

## 7. Documentation

§7 of the design spec records seven measured `sips` constraints. They stop describing our code, but they remain true statements about `sips`, and they are the reason this change exists. They move to `docs/research/` as findings rather than being deleted. The 16-bit downconversion joins them — `sips` does it on every path that touches pixels, as §2 records, not the pad path this line used to name. Task 3 added a further one, the region decode above 1,000,000 pixels, which is ImageIO's rather than `sips`' and applies to both implementations.

## 8. Done

- All three gating tiers green.
- The differential clean over the 27-image sample, with only pinned exceptions.

  > *Substituted during Task 3; recorded here 2026-09-14.* **Not met as written, deliberately.** 14 of the 27 differed on crop, for the reason §5 records: the `sips` reference is itself a region decode, so both sides depart from a frame decode and neither is the better answer. Pinning 14 exceptions would have recorded a divergence rather than tested anything, and it would have made the gate weaker the more it fired. What replaced it is stronger: hand both implementations the **same decoded pixels** and require exact agreement, with any file the control cannot account for failing the gate. §5 carries the correction; it belongs here too, because as written this bullet reads as a criterion that was missed rather than one that was replaced.

- A tier-3 run against the real upscaler.
- `sips` referenced nowhere in `paperhanger/`.

  > *Narrowed by ruling during Task 8; recorded here 2026-09-14.* **Literally unmet:** about ninety prose mentions remain — 92, counted case-insensitively on 2026-09-14. The criterion means **executable and normative** references: code that runs `sips`, or a docstring that states its behaviour as a constraint on ours rather than as a measurement of it. Of those there was exactly one left when Task 8 began, and it is gone. Nothing in the package spawns anything but `upscayl-bin` — `imaging._run` has one caller — and `grep -rnw SIPS paperhanger/` is empty.
  >
  > The measurement provenance was kept on purpose, which is the other half of the ruling. Every remaining mention is the comparison a construction was accepted on: which outputs were byte-identical, which diverged and by how much, and therefore why the code is shaped the way it is rather than the obvious way. Deleting them would leave the code looking arbitrary and the next reader re-deriving them. The defects themselves moved to `docs/research/` per §7, where they are findings about a tool rather than constraints on this one.

Tier 4, the 894-image acceptance pass, remains the author's and gates nothing.
