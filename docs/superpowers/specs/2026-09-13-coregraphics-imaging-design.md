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
| 16-bit sources | preserved; the `sips` pad path drops them to 8-bit |
| 96 Mpx frame, 8000x12000 to 8000x4000 | 0.85 s against 1.35 s |
| 24 Mpx noisy frame, 6000x4000 to 6000x1333 | 1.68 s against 3.06 s |

Two consequences worth stating plainly.

**The quality defaults transfer unchanged.** `sips` is built on ImageIO, and quality 80 there is `kCGImageDestinationLossyCompressionQuality` 0.80 here — byte-for-byte, on both JPEG and HEIC. The reasoning behind the current defaults survives intact, including the finding that HEIC 85 produces a file byte-identical to 80, which reproduces through ImageIO as well. ImageIO can write `public.heic`, `public.avif`, `public.jpeg` and `public.png`, so every format the tool offers is available.

**The current pad path has an eighth silent defect, and it is ours.** It downconverts 16-bit sources to 8-bit. Every PNG and TIFF in the corpus is 8-bit, so nothing is affected today; it is latent rather than active. It belongs on the list regardless, because unlike the other seven it was introduced by our workaround rather than by Apple.

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

Its gate carries one deliberate exception. Old and new are byte-identical on the corpus, every file of which is 8-bit. They do not match on a 16-bit source, where the old path downconverts and the new one does not. That difference is intended, so it is pinned as a named exception with its justification rather than passing quietly.

**2. The resample**, at `kCGInterpolationHigh`. Proven byte-identical. The gate should be clean with no exceptions; a red gate means the binding is wrong, not the plan.

`kCGInterpolationDefault` measured identical to High on the case tested, but the implementation sets High explicitly. Relying on Default would make the output depend on whatever Apple decides Default means.

**3. The encode**, at the same quality number. Also proven byte-identical, same expectation.

Steps 2 and 3 are the two halves of one existing function. `resize_and_encode` keeps its name and its signature throughout; what moves in two steps is its internals, so that a red gate says which half is at fault. Splitting the public function is not part of this design.

**4. `normalize_to_srgb_png`**, becoming a colour-space conversion rather than a `--matchTo` invocation. Its contract is unchanged: the upscaler receives sRGB PNG.

**5. `probe`**, last and deliberately so. It has the widest blast radius of the five, because it decides what counts as an image at all, and both `bands.py` and `cli.py` depend on that answer. It also gets simpler: non-image detection stops parsing `sips` stdout and becomes "ImageIO returned nothing," which is an answer rather than a string match.

### 4.1 Outside `imaging.py`

`cli.py` pre-flights `/usr/bin/sips` and `doctor` reports on it. Both exist because a missing or quarantined `sips` would otherwise report an entire library as unreadable. With no external binary in the imaging path, that failure class stops existing, and those checks change shape rather than surviving unchanged. The README's troubleshooting entry for it goes too.

## 5. Verification

**The bar is byte-identical output**, and it is achievable rather than aspirational. Resize and encode were measured byte-identical during design, and crop is identical on every file in the corpus. The one known departure is crop on a 16-bit source, where the new path is correct and the old one is not; §4 pins it as an exception rather than lowering the bar to accommodate it.

**The old implementation survives the migration as a test-only reference.** That is what makes a differential bar workable without keeping two production paths. Each gate compares the new output against the `sips` reference over the 27-image sample; the reference is deleted at the final swap. Any difference either fails the build or is pinned as a named exception carrying its justification.

**Both tiers.** A tier-1 differential on generated fixtures so the comparison runs every commit, and the tier-2 run over real corpus images for the input that is actually messy: wide-gamut profiles, the file whose extension lies, the HEIC source.

**Parts of the existing suite test `sips`, not us.** The pad-workaround tests go with the workaround. The argument-order assertions — `-s format` silently dropped after `--padColor`, both axes via `--resampleHeightWidth` — describe a tool we would no longer call; they become tests of the new resize or they go. The contracts worth keeping are about our own behaviour: crop refuses a non-PNG name, crop refuses an out-of-bounds rect, resize hits both axes exactly.

**Mutation discipline applies to the bindings, with a different question.** For FFI the question is whether a test would notice a missing `CFRelease` or an unchecked NULL, neither of which changes output and both of which matter. A test that runs several hundred crops and watches resident memory is worth more here than another assertion about dimensions.

## 6. Measure before implementing

Four things are unknown. They are listed here rather than left to be discovered mid-implementation, because any of them could change the design.

**EXIF orientation.** Untested; no synthetic fixture could be constructed during design. The corpus carries no orientation tags on any of its 894 files, but photos straight off a phone routinely do. If `sips` applies orientation where ImageIO returns raw pixels, crop geometry diverges silently for those files. This needs a real measurement and an explicit decision about which behaviour is correct.

**Peak memory at 300 Mpx.** The pixel cap exists because whole-frame upscaling strains memory, and a bitmap context for a 300 Mpx resize allocates the entire bitmap. Whether the new path's peak is better or worse than the `sips` path should be known before committing, since it may move the cap.

**HEIC input** through `CGImageSourceCreateWithURL`. The corpus contains one HEIC source; only PNG and JPEG inputs were tested during design.

**Whether interpolation High stays exact** across aspect ratios, and on enlargement as well as reduction. One downscale was tested.

## 7. Documentation

§7 of the design spec records seven measured `sips` constraints. They stop describing our code, but they remain true statements about `sips`, and they are the reason this change exists. They move to `docs/research/` as findings rather than being deleted. The eighth, the 16-bit downconversion in the pad path, joins them.

## 8. Done

- All three gating tiers green.
- The differential clean over the 27-image sample, with only pinned exceptions.
- A tier-3 run against the real upscaler.
- `sips` referenced nowhere in `paperhanger/`.

Tier 4, the 894-image acceptance pass, remains the author's and gates nothing.
