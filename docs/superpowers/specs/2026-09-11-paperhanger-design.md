# paperhanger design

Design spec, 2026-09-11. Supersedes the open questions in
`docs/research/2026-09-11-replacing-pixelmator-and-imagemagick.md`, which remains the
record of how the upscaler and `sips` were chosen. Where this document and the research
document disagree, this one wins.

## 1. What it does

paperhanger turns a folder of images into desktop and phone wallpapers on macOS. It
measures each image, classifies it by aspect ratio, crops tall or wide images into thirds
where that yields better wallpapers, upscales with an ML model only when the image is too
small to use as-is, resizes to an exact target, and writes HEIC (or JPEG, AVIF, PNG).

It replaces `legacy/make_wallpaper.zsh`, dropping its dependencies on Pixelmator Pro and
ImageMagick in favor of `upscayl-bin` and `sips`.

## 2. Implementation

Python 3.14 under `uv`, as a package with a test suite. No third-party runtime
dependencies: `sips` ships with macOS and `upscayl-bin` is invoked as a subprocess.

```
paperhanger/
  sizes.py       thresholds and the ideal/floor table         pure
  classify.py    (w, h) -> device, axis, ideal, floor         pure
  plan.py        Plan dataclasses and the recursive planner   pure
  imaging.py     sips and upscayl-bin subprocess wrappers     effects
  toolchain.py   locate / verify / download binary and model  effects
  execute.py     walks a Plan, calls imaging, files results   effects
  cli.py         argument parsing, dry-run report, progress
```

Installed with `uv tool install .`; developed with `uv run paperhanger`.

## 3. Architecture: plan, then execute

Measuring and deciding produce an immutable `Plan`; a separate executor runs it. Every
decision is made before anything is written.

```
input path
  -> scan       candidate files, filtered by probing with sips (not by extension)
  -> measure    imaging.probe() -> (w, h, source_format)   one sips call per file
  -> classify   pure: device(s), axis, ideal, floor
  -> plan       pure: a Plan tree. Crop slices are planned, not written, because
                slice dimensions follow arithmetically from source dimensions.
                Both output axes, the format, and the destination path are fixed
                here; so is the decision to skip a plan whose output already
                exists, which is the last filesystem-dependent choice and belongs
                on this side of the line.
  ---- every decision is now made; nothing has been written ----
  -> --dry-run? render the report and exit
  -> execute    per plan: normalize -> crop -> upscale -> resize+encode -> file
                cheapest plans first, so an interrupted run leaves the most behind:
                974 of the corpus's 3441 outputs need no upscaler at all
```

A `Plan` leaf carries the source path, the operations to run, the output path, the output
format and quality, and the reason it exists. A `CropPlan` carries three slice rectangles
and three child plans.

This shape is chosen for three reasons. The decision tree becomes pure functions over
integers, so the whole rule set is testable as a table with no image files. The dry-run
report and the executor read the same structure, so the report cannot drift from what
actually happens. And because upscaling is slow, seeing what a batch will cost before
committing to it has real value: a full run over the author's 894-image corpus is
estimated at 37 hours.

## 4. Classification

Aspect-ratio thresholds are integer comparisons. No floats, no `bc`, no rounding.

| Branch | Test | Axis | Ideal | Floor |
|---|---|---|---|---|
| desktop, by width | `w > h and w*10 <= h*16` | width | 7680 | 5120 |
| desktop, by height | `w*10 > h*16` | height | 4800 | 3200 |
| desktop, crop | `w <= h` | — | — | — |
| phone, by height | `w < h and w*3 >= h*2` | height | 3840 | 2880 |
| phone, by width | `w*3 < h*2` | width | 2560 | 1920 |
| phone, crop | `w >= h` | — | — | — |

Each set of three is exhaustive and disjoint.

### 4.1 Deliberate changes to the legacy thresholds

The legacy thresholds are not where they appear to be. `get_aspect_ratio` truncates to two
decimals with `bc scale=2` and the result is then compared against `bc`'s full-precision
`2/3`, so `0.66 >= 0.6666...` is false. The real phone boundary in the shipped script is
**0.67**, not 0.66, and a 2000x3000 image takes the width path rather than the height path.
The same truncation puts the real desktop boundary at **1.61**, not 1.60.

The integer tests above put both boundaries where `legacy/notes.org` always said they were.
This is a deliberate correction, not an accident, and carries named tests.

### 4.2 Phone targets

The legacy script declares `MAX_PHONE_WIDTH=5120` and `MAX_PHONE_HEIGHT=7680` and never
uses them; only the `MIN_PHONE_*` values are wired up as targets. Those constants are not
revived. Measured against current hardware:

- iPhone 18 Pro is 2622x1206 at 460 ppi, pixel-identical to the 17 Pro and 16 Pro.
- Apple has held ~460 ppi since the iPhone 12 (2020). Pro long-edge resolution has moved
  only with physical panel size: 2436 -> 2532 -> 2556 -> 2622, about 8% in eight years.
- Perspective Zoom renders wallpaper at roughly 1.1x and crops ~100-200 px per edge. It is
  still in use: iOS 26's Spatial Scenes require it to be enabled.

An ideal of 3840 is therefore 1.46x the Pro panel's long edge, covering a future panel a
third larger with parallax headroom intact. 7680 would be 2.9x the panel, which iOS would
simply downsample. The floor of 2880 is the panel plus parallax headroom (~2884), and 1920
is the matching value on the width axis.

## 5. Bands

Given the governing dimension `d`, ideal `I`, and floor `F`:

| | Condition | Action | Destination |
|---|---|---|---|
| 1 | `d >= I` | downscale to `I` | `to_sort_<device>/` |
| 2 | `F <= d < I` | encode only, native size, no resampling | `below_target/` |
| 3 | `d < F` and `d*4 >= I` | 4x upscale, then downscale to `I` | `to_sort_<device>/` |
| 4 | `d < F` and `F <= d*4 < I` | 4x upscale, no resize | `below_target/` |
| 5 | `d*4 < F` | reject | `error/` |

Two rules govern this table:

**No image is ever enlarged except by the ML model.** There is no non-AI upsampling path.
`sips` only ever reduces.

**No image is enlarged by less than `I/F`.** An image already at or above its floor keeps
its real pixels rather than gaining invented ones. The cutoff needs no separate constant
because it *is* the floor: `I/F` is exactly 1.5 on both desktop axes and 1.33 on both phone
axes. Band 2 is that case, and it does no resampling at all.

The phone ratio is 1.33 rather than 1.5 because the phone floors in section 4.2 are derived
from panel size plus parallax headroom, not chosen as an enlargement ratio. A phone source
just under its floor is therefore enlarged by about 1.33x. Making phone a true 1.5x cutoff
would require a height floor of 2560, which is below the iPhone 18 Pro panel's 2622 long
edge; the panel-derived floor wins and the ratio follows from it.

Each row's condition is exhaustive and disjoint, so no evaluation order is implied and each
band is testable in isolation.

Band 2 is also what makes the runtime tractable. Upscayl's cost scales with *source*
pixels, so the images needing the least enlargement cost the most to run: without band 2, a
full run over the author's corpus is 59 hours, 40% of it spent on images that are already
at or above their floor.
With it, 37 hours, and 544 of the 3441 outputs are produced at native resolution having
been resampled not at all.

The legacy `lt_3x` / `3x` / `4x` tiers and the 4.25x acceptance ceiling do not survive.
They were artifacts of Pixelmator's 3x model and of its ability to resize to an arbitrary
target; `upscayl-bin` always emits exactly 4x. The legacy `4x_upscaled/` directory is
renamed `below_target/` and now means "did not reach the ideal size", whether because the
source was already close (band 2) or because 4x was not enough (band 4).

## 6. Cropping

Preserved from the legacy script, including that the three crops **overlap** for anything
near square. They are three crops, not three disjoint thirds.

- **Horizontal** (desktop, `w <= h`): slice is `w x -(-w*10 // 16)`, at y = `0`,
  `(h - slice_h)//2`, `h - slice_h`. Each slice is planned as desktop-by-width with `d = w`.
- **Vertical** (phone, `w >= h`): slice is `-(-h*2 // 3) x h`, at x = `0`,
  `(w - slice_w)//2`, `w - slice_w`. Each slice is planned as phone-by-height with `d = h`.

The slice dimension rounds **up**, not down. With truncation a 16:10 slice computes to a
ratio slightly above 1.6 and fails its own classification test; rounding up puts it at or
below 1.6 for every residue, so the slice satisfies the class the planner assigns it. Same
for the 2:3 vertical slice and the phone-by-height test.

The middle slice is centered as `(h - slice_h)//2` rather than legacy's
`h/2 - slice_h/2`, which is off by one pixel on odd dimensions.

**Slices are planned, not re-classified.** The legacy script wrote slices to disk and
recursed into the classifier, which re-derived the device from the slice's ratio. That
works only by luck: a 16:10 slice computes to 1.6016 and survives the desktop-width test
solely because of two-decimal rounding. Under exact comparison it would fall through to the
height path. The planner knows a horizontal slice is desktop-by-width by construction, so it
builds the child plan directly.

Crop plans force a single device, as in the legacy script.

## 7. Execution

**All intermediates live in a per-run temp directory**, created with `mkdtemp` and removed
on exit including on crash or interrupt. Nothing paperhanger writes ever lands beside the
source images. This is a deliberate departure: the legacy script wrote slices into the input
directory and deleted them from `originals/` afterward, orphaning any slice that got
rejected.

**A plan's intermediates are deleted as soon as its output is in place**, not at process
exit. Peak temp usage is therefore one plan's working set rather than the whole run's. The
largest single 4x intermediate in the corpus is about 300 MB as PNG; retaining all 2467
upscaler outputs until exit would need roughly 169 GB. With 27 GiB free, deferring cleanup
to exit fills the volume around the 400th upscale and then spends thirty more hours
recording failures.

Per-image sequence, with the steps each band skips:

```
source
  |> normalize  whenever an upscale is needed, not only for unreadable formats:
                sips --matchTo '/System/Library/ColorSync/Profiles/sRGB Profile.icc' \
                     -s format png
  |> crop       slice plans only, and always its own invocation:
                sips -c H W --cropOffset Y X
  |> upscale    bands 3 and 4 only:
                upscayl-bin -i in.png -o 4x.png -m <models> -n upscayl-standard-4x -s 4
  |> resize     bands 1 and 3 only
  +  encode     resize and encode are one invocation, with BOTH axes explicit:
                sips --resampleHeightWidth <H> <W> \
                     -s format heic -s formatOptions 80 in --out out.heic
  |> file       to_sort_<device>/ or to_sort_<device>/below_target/
```

A **whole-image** plan in band 1 or 2 touches `sips` exactly once, source to output, with
no temp file; `sips` reads HEIC natively, so no conversion is needed there. Slice plans
always need at least two invocations, and 672 of the corpus's 974 band-1 and band-2 plans
are slices.

Three measured constraints produced that block. Each fails silently if ignored:

- **Crop must never be fused with a resample.** `sips -c 1080 1920 --cropOffset 0 500
  --resampleWidth 960` on a 3840-wide source returns 480x270, not 960x540: the resample is
  applied against the pre-crop width. No error, no warning.
- **Both output axes must be given explicitly.** `--resampleWidth` and `--resampleHeight`
  each derive the other axis, so a single hardcoded flag is wrong for half the plans:
  `--resampleWidth 4800` on a 3840x2160 desktop-by-height source yields 4800x2700, whose
  governing dimension of 2700 is below the 3200 floor the band just certified. The derived
  axis is also neither consistently rounded nor floored — 2662x1663 at `--resampleWidth
  7680` gives 7680x4798 where floor gives 4797 — so a filename computed in advance would
  disagree with the bytes. Computing both axes in `sizes.py` and passing
  `--resampleHeightWidth` makes the plan *define* the output rather than predict it, which
  is what section 3's no-drift claim requires.
- **The upscale path must normalize color.** `upscayl-bin` emits 8-bit PNG with no ICC
  chunk, so encoding its output tags sRGB over unconverted numbers. The corpus holds 19
  Adobe RGB, 3 ProPhoto RGB, and 96 further non-sRGB profiles among 894 files, and a
  wide-gamut round trip without `--matchTo` measures about 15 dB worse than with it — an
  order of magnitude larger than the 33.6-to-36.0 dB spread the upscaler itself was chosen
  on. Normalizing only unreadable formats would never fire for any of them, since all are
  JPEG or PNG.

There is no copy-instead-of-encode shortcut for band 2. It would fire on 1 of 3441 corpus
plans, and for a slice triple it would bypass `crop` and emit three identical full frames.

**One upscayl process per image.** Directory mode would work, since the scale is always 4,
but it buys only process startup against a 10-30 second run and costs per-image progress and
failure isolation.

**Outputs are staged as `<name>.partial` in the destination folder and renamed into place.**
Staging beside the destination rather than in the temp tree makes the rename atomic whatever
volume `TMPDIR` lives on, and an interrupted run never leaves a truncated file where the
sorter will see it. Stray `.partial` files are swept at startup. A plan whose output already
exists is marked done at plan time and skipped; `--overwrite` forces regeneration. This
makes re-running on the same folder both safe and resumable, which matters when a bulk
import is measured in hours.

## 8. Files and naming

```
~/Pictures/wallpaper/processing/        (--processing-dir overrides)
  originals/
  error/
  to_sort_desktop/
    below_target/
  to_sort_phone/
    below_target/
```

Output name: `<basename>_<device>_<W>x<H>.<ext>`, where crop slices carry their position in
the basename so the three cannot collide.

```
rowan_desktop_7680x4800.heic
sunset_top_desktop_7680x4800.heic
sunset_middle_desktop_7680x4800.heic
moth_desktop_6400x4800.heic        (in below_target/)
```

## 9. Output formats

`--format {heic,jpeg,png,avif}`, default `heic`. `--quality N` overrides the default for
the lossy formats; passing it with `png` is an error rather than a silent no-op.

| Format | Extension | Default quality |
|---|---|---|
| heic | `.heic` | 80 |
| jpeg | `.jpg` | 90 |
| avif | `.avif` | 85 |
| png | `.png` | lossless |

Measured on a 7680x5120 high-frequency photograph (39 Mpx, tree bark), DSSIM lower is better:

| | q70 | q75 | q80 | q85 | q90 | q95 |
|---|---|---|---|---|---|---|
| heic | 2.53 MB / 778 | 2.88 / 707 | 3.25 / 645 | identical to 80 | 5.85 / 383 | 11.46 |
| jpeg | 4.77 | 5.32 / 595 | 5.69 / 547 | 7.09 / 475 | 7.48 / 452 | 8.20 / 401 |
| avif | 1.21 / 1239 | 1.52 / 1068 | 1.96 / 898 | 2.64 / 716 | 3.87 / 523 | 6.27 |
| png | — | — | — | — | — | lossless, 40.8 MB |

Each default sits at its own encoder's knee, leaning toward quality. HEIC 85 produces a
byte-identical file to 80 — Apple's encoder quantizes — so 80 is the real ceiling before 90
nearly doubles the size. JPEG 85 to 90 costs 5% more size for better fidelity while 90 to 95
costs 10% for less return. AVIF 85 lands in HEIC 80's quality neighborhood at 19% smaller.
AVIF encoding is hardware-accelerated: 1.2 s for a 39 Mpx image.

## 10. Toolchain

`upscayl-bin` and its model are about 60 MB and are not committed. `paperhanger setup`
downloads and verifies them into `~/.local/share/paperhanger/`.

```
~/.local/share/paperhanger/
  bin/upscayl-bin
  models/upscayl-standard-4x.bin
  models/upscayl-standard-4x.param
```

Resolution order: `PAPERHANGER_UPSCAYL_BIN` -> `~/.local/share/paperhanger/bin/` -> `PATH`.

Pinned artifacts, with SHA-256 verification on download:

| Item | Value |
|---|---|
| Release tag | `20251207-174704` |
| macOS zip SHA-256 | `277419791281a56eae0c739c70120b974d7267cf7c2de8e86dc09798d4b314db` |
| Model source | `upscayl/upscayl` at commit `6cfaf45b2aae2847cba4f2313b57ca20a0ddd79c` |
| `.bin` SHA-256 | `713ee713b0353afaa27976f0563a64a5043bd70b9bd8936c2e26e25ebcdbcddf` |
| `.param` SHA-256 | `35330ececcea33b6c397a72548e788d5d53becee4734c50b7fada36e89f10a86` |

Any run that would need the upscaler checks for it before planning, so a missing binary
fails in a second with the command to fix it, not forty images into a batch.

## 11. CLI

```
paperhanger [-d|-p|-b] [--dry-run] [--format FMT] [--quality N]
            [--overwrite] [--allow-nested] [--processing-dir DIR] <file-or-directory>
paperhanger setup      download and verify upscayl-bin and the model
paperhanger doctor     report what is found, what is missing, versions
```

`-d`, `-p`, `-b` are preserved from the legacy script, `-b` remaining the default. A bare
path processes; `setup` and `doctor` dispatch on the first argument.

`--overwrite` and `--allow-nested` are deliberately separate. They were one `--force` in an
earlier draft, which meant the flag required to process the default nested layout also
disabled the skip-existing check, so resuming an interrupted run would re-upscale
everything already done.

Dry-run report:

```
$ paperhanger --dry-run ~/Downloads/wallpapers

47 images, 6 rejected, 12 already done, ~14 min

 cliffs.jpg     1.50  desktop/width  8200 -> 7680   downscale        [1]
 lichen.jpg     1.50  desktop/width  6000 native     below_target      [2]
 rowan.jpg      1.50  desktop/width  4912 -> 4x -> 7680                [3]
 sunset.jpg     0.72  desktop        crop 3x horizontal
   sunset_top      1.60  3000 -> 4x -> 7680x4800                       [3]
   sunset_middle   1.60  3000 -> 4x -> 7680x4800                       [3]
   sunset_bottom   1.60  3000 -> 4x -> 7680x4800                       [3]
 moth.png       1.33  desktop/width  1600 -> 4x -> 6400  below_target  [4]
 tiny.gif       1.00  reject (too small for both)                      [5]
 done.jpg       1.60  desktop/width  already done, skipping
```

Bracketed numbers are the bands from section 5; the real report does not print them.

The time estimate uses ~0.8 s per megapixel of upscaler output, derived from the research
document's timings, which hold within about 20% across a 33x range of image sizes.

## 12. Passes, errors, and rejection

**Desktop and phone passes are independent.** Neither can abort the other. A 900x1400 image
too small for desktop still produces a phone wallpaper. This corrects legacy behavior where
an early `return 1` skipped both the remaining pass and the move to `originals/`.

Failure and rejection mean different things and do not share a destination:

| Situation | Original goes | Reported as |
|---|---|---|
| Too small for every requested device | `error/` | rejected |
| Every planned output succeeded or was rejected | `originals/` | ok |
| Every planned output was skipped as already done | `originals/` | already done |
| **Some outputs succeeded, at least one failed** | **left where it is** | **partial, at exit** |
| Not an image (`.DS_Store`, a `.txt`) | left alone | skipped, counted |
| upscayl or sips failed on everything, or the run was killed | left where it is | failed, at exit |

Archival is per source image, but work is per output plan, and one image yields up to six.
The partial row exists because the obvious rule — archive if anything succeeded — is a data
trap: a single crashed phone upscale would move the source out of the input directory while
reporting success, and the missing wallpaper could never be recovered by the re-run that
section 7 advertises, because the re-run would look in a directory the source had left.

**The archive move never overwrites.** If the name already exists in `originals/` or
`error/`, the archived copy is suffixed (`IMG_0042-2.jpg`) and the rename is reported. This
is the only path in the design that could otherwise destroy a user's sole copy of an
original: a second import containing another `IMG_0042.jpg` would replace the first
silently.

**The planner aborts on colliding output paths.** Two sources sharing a stem across
different extensions produce identical output names — `cityscape_reflection_4592.jpg` and
`cityscape_reflection_4592.png` are both in the corpus, both 1920x1046, and collide on all
four of their outputs. Detected at plan time, before any work is done.

Non-images are detected by probing with `sips`, not by extension — but **exit status is not
the signal**. A file is an image only if the probe's stdout carries `pixelWidth:` and
`pixelHeight:` lines that parse as positive integers. Measured: `sips -g pixelWidth` exits 0
while printing `pixelWidth: <nil>` for a text file, an empty file, and a truncated JPEG, and
on `.DS_Store` it dies with an uncaught `NSInvalidArgumentException` and exit 134. A probe
killed by a signal is likewise not an image, and probe stderr is discarded. On the author's
894-file corpus this admits every image, including `.webp`, `.heic`, `.gif`, and uppercase
`.JPG`, and excludes only `.DS_Store`.

**Guard:** paperhanger refuses to process a directory that contains its own processing
directory, unless `--allow-nested` is given. The author's image corpus lives at
`~/Pictures/wallpaper`, directly above the default processing directory, and originals are
moved rather than copied.

## 13. Testing

- `test_classify`, `test_plan` — table-driven, no filesystem. Every band boundary gets an
  exact pair (`d*4 == I`, `d*4 == F`, `d == I`, `d == F`) and every ratio boundary gets one
  (`w*10 == h*16`, `w*3 == h*2`, `w == h`). `d*4 == I` must land in band 3, not band 4. The two threshold corrections in section 4.1
  carry named tests so the change is visible rather than incidental.
- **Fixtures are generated, not committed.** A small stdlib PNG writer (zlib plus struct,
  no dependencies) produces an image at any exact dimension. The interesting cases are
  1279 versus 1280 wide; committing a file per boundary would be absurd. HEIC and TIFF
  fixtures for the normalization path come from converting a generated PNG with `sips`.
- `test_execute` stubs the upscaler by pointing `PAPERHANGER_UPSCAYL_BIN` at a fake that
  scales 4x with `sips`. The environment override needed in production doubles as the test
  seam, so the executor is tested end to end in milliseconds rather than minutes. **The fake
  must exit nonzero on any input that is not jpg, png, or webp**, mirroring the real binary.
  A `sips`-based fake reads HEIC happily, so without that check the normalize step — and the
  HEIC and TIFF fixtures that exist to exercise it — would pass whether normalization works,
  is inverted, or is deleted outright.
- One test asserts that the dimensions in every output filename equal the dimensions `sips`
  reports for that file. This is the check that keeps section 3's no-drift claim honest.
- One real-binary end-to-end test, marked and skipped by default.
- The 894-image corpus at `~/Pictures/wallpaper` is a **read-only** validation asset.
  Validation runs copy a subset to scratch and always pass `--processing-dir`.

## 14. Out of scope

- **A 2x ncnn model.** Real-ESRGAN's `x2plus` is published in PyTorch only and the one
  public ncnn conversion fails to load. Producing a working one means going through
  Upscayl's model conversion guide. Band 2 removes most of the motivation. Revisit only if
  the 4x path proves too slow in practice.
- **Dotfiles aliases.** `mkw`, `mkwdesk`, and `mkwphone` map onto `paperhanger -b/-d/-p`,
  but they live in another repository. The README will note the mapping.
- **EXIF orientation.** `sips -g` reports stored dimensions and returns `<nil>` for
  `-g orientation` even when the tag is present, and it propagates the tag through resample
  and encode. A source tagged Orientation 6 is therefore classified on the wrong axis,
  cropped in the wrong space, and displays rotated. This is a deliberate deferral, not an
  oversight: all 272 tagged files in the corpus are Orientation 1, so the bug is latent.
  Revisit when a rotated source first appears.
- **The legacy rescue pass.** `check_error_images.zsh` exists because the legacy script
  discarded images a second pass could save. Independent device passes and band 4 cover
  those cases, so there is nothing left for it to rescue.

## 15. Corpus measurements

Recorded for provenance. A full `-b` run over the 894 readable images at
`~/Pictures/wallpaper`, under the bands in section 5:

| Band | Count |
|---|---|
| 1, downscale to ideal | 430 |
| 2, native, no resampling | 544 |
| 3, 4x then downscale | 2143 |
| 4, 4x only | 324 |
| 5, reject | 165 |
| **outputs** | **3441** |
| **upscaler runs** | **2467** |
| **estimated upscale time** | **~37 hours** |

The multiplication from 894 inputs to 3441 outputs is the crop-into-thirds behavior, which
fires on 318 images in the desktop pass and 591 in the phone pass.

The 165 rejections are **output plans, not images**. No image in the corpus is rejected on
both its desktop and its phone pass, so on a full `-b` run `error/` receives nothing and
every source ends in `originals/`. `error/` earns its place only for `-d` or `-p` runs and
for sources smaller than anything in this corpus.
