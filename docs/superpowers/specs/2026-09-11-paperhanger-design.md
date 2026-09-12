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
  plan.py        OutputPlan dataclass and the planner         pure
  imaging.py     sips and upscayl-bin subprocess wrappers     effects
  toolchain.py   locate / verify / download binary and model  effects
  execute.py     runs plans, calls imaging, files results     effects
  cli.py         argument parsing, dry-run report, progress
```

Installed with `uv tool install .`; developed with `uv run paperhanger`.

## 3. Architecture: plan, then execute

Measuring and deciding produce an immutable list of plans; a separate executor runs them.
Every decision is made before anything is written.

```
input path
  -> scan       candidate files, filtered by probing with sips (not by extension)
  -> measure    imaging.probe() -> (w, h, source_format)   one sips call per file
  -> classify   pure: device(s), axis, ideal, floor
  -> plan       pure: a flat list of OutputPlans, grouped by source photo. Crop
                slices are planned, not written, because
                slice dimensions follow arithmetically from source dimensions.
                Both output axes, the format, and the destination path are fixed
                here; so is the decision to skip a plan whose output already
                exists, which is the last filesystem-dependent choice and belongs
                on this side of the line.
  ---- every decision is now made; nothing has been written ----
  -> --dry-run? render the report and exit
  -> execute    per photo: normalize -> upscale the whole frame once
                per plan:  crop -> resize+encode -> file
                cheapest photos first, so an interrupted run leaves the most
                behind: 974 of the corpus's 3441 outputs need no upscaler at all
```

An `OutputPlan` carries the source path, the crop rectangle if any, the operations to run,
both output dimensions, the output path, format and quality, the band it came from, and the
net enlargement factor. Plans are grouped by source photo, because two things are decided
per photo rather than per output: whether the upscaler runs (section 7) and where the
original is archived (section 12).

The list is flat, not a tree. A tree would only be justified by recursion, and section 6
removed it — slices are planned directly rather than re-classified, so nesting could never
be more than one level deep. A flat list is also what makes the work sortable, which the
cheapest-first ordering needs.

This shape is chosen for three reasons. The decision tree becomes pure functions over
integers, so the whole rule set is testable as a table with no image files. The dry-run
report and the executor read the same structure, so the report cannot drift from what
actually happens. And because upscaling is slow, seeing what a batch will cost before
committing to it has real value: a full run over the author's 894-image corpus is
estimated at 24 hours.

## 4. Classification

Aspect-ratio thresholds are integer comparisons. No floats, no `bc`, no rounding.

| Branch | Test | Axis | Ideal | Floor |
|---|---|---|---|---|
| desktop, by width | `w > h and w*10 <= h*16` | width | 7680 | 5120 |
| desktop, by height | `w*10 > h*16` | height | 4800 | 3200 |
| desktop, crop | `w <= h` | — | — | — |
| phone, by height | `w < h and w*3 >= h*2` | height | 4320 | 2880 |
| phone, by width | `w*3 < h*2` | width | 2880 | 1920 |
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

The two phone numbers are therefore derived from different things, in this order:

**The floor comes from the hardware.** 2880 is the Pro panel's long edge plus parallax
headroom (~2884 needed), and 1920 is the matching value on the width axis, the two being a
2:3 frame. At or above that, an image fills the panel with room for Perspective Zoom to
move, so it is worth keeping as-is.

**The ideal is the floor times 1.5**, which is the enlargement rule in section 5, giving
4320 x 2880. Setting it this way makes `I/F` exactly 1.5 on all four device-axis pairs
rather than 1.5 on desktop and 1.33 on phone, so "needs less than 1.5x enlargement" and "is
at or above the floor" are one condition everywhere instead of nearly everywhere.

4320 is 1.65x the Pro panel's long edge — more headroom than any announced phone needs, and
detail no current iPhone will display. Two measurements justify carrying it anyway: it costs
**no additional upscaler time at all** (24.3 hours either way, because the model's cost
scales with the size of the source and not the target), and it moves 163 more outputs into
band 2, where they keep their real pixels instead of being reduced to 3840. The only price
is about 27% more pixels per phone file.

The legacy 7680 would have been 2.9x the panel, which iOS would simply downsample. That is
why those constants are not revived even though the ideal moved up.

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

**No image is enlarged by less than 1.5x.** An image already at or above its floor keeps its
real pixels rather than gaining invented ones. The cutoff needs no separate constant because
it *is* the floor: `I/F` is exactly 1.5 on all four device-axis pairs, by construction —
section 4.2 sets each phone ideal at its floor times 1.5 for precisely this reason. Band 2
is that case, and it does no resampling at all.

Each row's condition is exhaustive and disjoint, so no evaluation order is implied and each
band is testable in isolation.

Band 2 is also what makes the runtime tractable. Upscayl's cost scales with *source*
pixels, so the images needing the least enlargement cost the most to run: without band 2, a
full run over the author's corpus is 59 hours, 40% of it spent on images that are already
at or above their floor.
With it, 37 hours — and 24 once the upscaler runs once per photo rather than once per
output (section 7). 707 of the 3441 outputs are produced at native resolution, having been
resampled not at all.

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
exit, and a photo's enlarged frame is released as soon as the last plan drawing on it is
filed. Peak temp usage is therefore one photo's working set rather than the whole run's.
Retaining all 756 enlarged frames until exit would need about 114 GB; with about 28 GB free, that
fills the volume roughly a fifth of the way in and then spends twenty more hours recording
failures. The 300 Mpx fallback cap also bounds any single intermediate at about 311 MB — the
largest whole frame in the corpus would otherwise be 622 Mpx.

Per-image sequence, with the steps each band skips:

```
once per source photo, only if some plan of its needs the upscaler:
  |> normalize  convert to PNG and force the pixels into sRGB:
                sips --matchTo '/System/Library/ColorSync/Profiles/sRGB Profile.icc' \
                     -s format png
  |> upscale    the WHOLE frame, once:
                upscayl-bin -i in.png -o 4x.png -m <models> -n upscayl-standard-4x -s 4

then once per output plan:
  |> crop       slice plans only, and always its own invocation:
                sips -c H W --cropOffset Y X
                cut from the 4x frame for bands 3 and 4, from the source for bands
                1 and 2 — so the rectangle is scaled by 4 on the upscaled path
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

Five measured constraints produced that block. Each fails silently if ignored:

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
- **A write `sips` skips still exits 0.** Given a path it cannot read — one that does not
  exist, or a directory — `sips` prints `Warning: <path> not a valid file - skipping` to
  stderr, exits 0, and writes no output file at all. A corrupt file, an unwritable
  destination and an unknown format all exit 13 and are caught by the status check, but
  these are not, so the executor would carry on to the next stage against a file that was
  never written. Every operation therefore asserts its post-condition — that the file it
  asked for exists afterwards — and clears the destination first, so a stale file from an
  earlier run cannot stand in for output this run never produced.
- **Two `--cropOffset` shapes are silently ignored, and the geometry produces both.** Both
  need `x == 0`. `--cropOffset 0 0` drops the offset and `sips` falls back to its default
  *centered* crop; `--cropOffset <y> 0` with `y + height == source height` drops the crop
  entirely and returns the whole source. Measured on 1600x1200: `horizontal_thirds`' top
  slice, asked for 1600x1000 at (0,0), comes back as the rows at y=100 — the middle slice;
  its bottom slice, asked for 1600x1000 at (0,200), comes back as the full 1600x1200 image;
  and `vertical_thirds`' left slice, asked for 800x1200 at (0,0), comes back as the band at
  x=400. Two of the three desktop slices and one of the three phone slices, wrong, at
  exactly the requested size — so no dimension check can see it, and only comparing the
  returned pixels against the region asked for will. An `x` of 1 or more is correct at every
  `y`, and `x == 0` is correct for every `y` strictly between those two. `crop` works around
  it by padding one pixel on every side and cropping at +1, which costs one extra full-image
  pass on the affected slices; the executor could pad each 4x frame once instead.

A related trap bounds what `crop` may be asked for: **an out-of-bounds crop pads with
black** rather than clamping or failing. A 400x200 source cropped at x=900,y=900 returns a
120x80 image that is entirely black, at exit 0, as a valid file. `crop` therefore probes its
source and refuses a rect that does not fit — the post-condition cannot help, because the
file exists and is exactly the size asked for. This matters most on the upscale path, where
slices are cut with `rect.scaled(4)`: an enlargement even a pixel short of exactly 4x would
otherwise produce a black-edged wallpaper with nothing raising.

There is no copy-instead-of-encode shortcut for band 2. It would fire on 1 of 3441 corpus
plans, and for a slice triple it would bypass `crop` and emit three identical full frames.

**One upscale per source photo, not per output plan.** When any plan from a photo needs the
upscaler, the *whole frame* is enlarged once and every slice is cut from that result. The
alternative — enlarging each slice separately, which is what the legacy script did — enlarges
the same pixels two or three times, because the three slices are overlapping windows on one
photo. It also duplicates work across devices, since a photo cropped for desktop usually
needs its whole frame for the phone pass anyway.

Measured on the corpus: **2467 upscaler runs become 756, and 37.2 hours become 24.3.**
Enlarging the whole frame is cheaper than enlarging its slices for every photo in the corpus;
there is no case where per-slice wins.

This also makes the three siblings consistent. Today each slice's overlapping region is
enlarged independently and the results can differ slightly, so the three files being compared
at sorting time are not strictly comparable. Cut from one enlargement, the overlaps are
identical.

**Two qualifications.** Above a 300 Mpx cap on the enlarged frame, the photo falls back to
per-slice upscaling — but only when every plan that needs the upscaler is a *slice*. 92 of
the 756 whole-frame jobs exceed the cap.

The cap alone is not the condition, because falling back bounds nothing unless there is a
crop rect to be bounded by. A plan with no crop covers the whole photo, so enlarging "just
its region" enlarges the whole frame: the fallback's peak is then identical to the
whole-frame path's, for one model run per plan instead of one per photo. Measured on
1280x800 — a desktop whole-image plan plus three phone slices — both strategies peak at
16,384,000 px of 4x output, the fallback taking four runs to get there and the whole frame
one. Sweeping 400-20000 on both axes, 2878 of 7413 over-cap shapes have a cropless upscaling
plan. Those photos stay on the whole-frame path: there is no cheaper decomposition, and
attempting the frame is the only thing that can produce that output at all.

The second qualification: this rests on the assumption that enlarging then cutting equals
cutting then enlarging — true if the model has no whole-image context, which is how this
architecture works, but **not measured**, because `upscayl-bin` is not installed on the
author's machine. Section 13 gates it on a test.

**One process per upscale.** Directory mode would work, since the scale is always 4, but it
buys only process startup against a 10-30 second run and costs per-photo progress and failure
isolation.

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

Output name: `<basename>_<device>_<W>x<H>_<factor>.<ext>`, where crop slices carry their
position in the basename so the three cannot collide.

```
cliffs_desktop_7680x4800_native.heic     band 1, real pixels, downscaled
lichen_desktop_6000x3750_native.heic     band 2, real pixels, untouched
rowan_desktop_7680x4800_1.6x.heic        band 3, enlarged 4x then reduced
sunset_top_desktop_7680x4800_2.6x.heic   band 3, a crop slice
moth_desktop_6400x4800_4x.heic           band 4, in below_target/
```

`<factor>` is the **net** enlargement in the finished file, to one decimal: `native` for
bands 1 and 2, where the model never ran, and `I/d` for bands 3 and 4 — which is `4x` exactly
in band 4 and between `1.5x` and `4x` in band 3. Net rather than the model's own factor,
because a photo enlarged 4x and then reduced carries less invented detail into the result
than one left at 4x.

This exists because the dimensions alone cannot answer the question that matters at sorting
time. 475 of the corpus's desktop outputs are all exactly 7680x4800, produced from sources
ranging from 8000 px wide (every pixel real) to 1920 px wide (most pixels invented), and
they would otherwise be indistinguishable by name. `below_target/` is worse: it holds 707
untouched originals — the highest-fidelity output in the run — beside 351 that the model
enlarged and that still fell short.

The legacy script carried this information in the `_ml_res_lt_3x_` part of its filenames.
It was dropped when the tiers collapsed, and the dimensions that replaced it answer a
different question. Finder tags were considered as an alternative that keeps names short;
rejected because tags do not survive a copy to another volume and are invisible from a
terminal.

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

Four tiers, and **only the first three are part of "implemented".** The full-corpus run is
the author's acceptance pass, deliberately outside the definition of done:

| Tier | What | Cost | When |
|---|---|---|---|
| 1 | generated fixtures, pure functions, stubbed upscaler | milliseconds | every commit |
| 2 | 27 real corpus images, stubbed upscaler | seconds | every commit |
| 3 | the same 27 images, real upscaler | ~25 min | once, before calling it done |
| 4 | all 894 images | ~24 h | the author, when he chooses |

### 13.1 Tiers 1 and 2

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
- **The whole-frame upscale is gated on an equivalence test**, also marked and run once
  against the real binary: upscale a photo whole and cut a slice from the result; separately
  cut the same slice from the source and upscale that; the two must match within a small
  tolerance. Section 7's largest saving depends on this holding, and it is currently argued
  from the model's architecture rather than measured. If it fails, section 7 reverts to
  per-plan upscaling and the estimate returns to 37 hours.
### 13.2 The 27-image sample

The corpus at `~/Pictures/wallpaper` is a **read-only** asset. Every tier that uses it copies
files to scratch and passes `--processing-dir`; nothing writes to the corpus, and nothing
relies on the originals still being there afterward.

The images themselves are not committed — per `CLAUDE.md`, wallpapers stay out of this repo.
Instead `tests/corpus_sample.txt` holds 27 filenames, and the tests that need them copy from
the corpus at run time and **skip cleanly when it is absent**, so the suite still passes on a
machine that has never seen these photos.

The 27 were chosen to cover every coverage tag the corpus contains — 36 of them: each of the
six routing branches paired with each band it actually reaches, each input format, and each
colour-profile class. They produce 102 outputs and 19 upscaler runs. Specific reasons some
are in the list:

| Image | Why |
|---|---|
| `snowy_forest_landscape_9522.jpg` | **is actually a WebP.** A real file whose extension lies — the case section 12's probe rule exists for |
| `moss_with_pine_needles_5324.jpg` | ProPhoto RGB, the widest gamut present; the colour-conversion case in section 7 |
| `red_tulips_with_mountain_background_4338.jpg` | Adobe RGB |
| `katana_with_tag_2369.jpg` | greyscale profile |
| `dark_stones_7236.png`, `blade_runner_2049_concept_poseter_.webp` | no embedded profile at all |
| `green_grass_texture_3997.png` | 9072x12096; the only sample image that trips the 300 Mpx fallback |
| `purple_nebula_glow_0312_x.heic` | HEIC already at 7680x4800, so band 1 and 2 with no conversion |
| `foggy_forest_path_7098.JPG` | 620x1102; the only band 5 rejection in the sample |
| `cityscape_illustration_4562.gif` | the one GIF; readable by `sips`, not by `upscayl-bin` |
| `man_with_car_in_fog_1158.jpg` | 8392x4721, above the desktop ideal, so a pure downscale |

Tier 2 runs these through the real `sips` with the upscaler stubbed, which is where almost
all of the value is: it exercises format detection, colour conversion, crop geometry, naming
and filing against genuinely messy input in seconds. Tier 3 repeats it with the real binary
to confirm the two things a stub cannot check — that `upscayl-bin` accepts what we hand it,
and the whole-frame equivalence this section gates section 7 on.

### 13.3 What the full corpus run is for

Tier 4 is **not** a test and does not gate implementation. It is the author running the
finished tool over all 894 images once, on purpose, and looking at the results. Its value is
in the things no assertion covers — whether the crops are worth keeping, whether HEIC 80 was
the right call, whether `below_target/` is a useful distinction in practice. Pinning it to
"done" would mean a 24-hour wait on every change, and would still not answer those questions.

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
| 1, downscale to ideal | 267 |
| 2, native, no resampling | 707 |
| 3, 4x then downscale | 2116 |
| 4, 4x only | 351 |
| 5, reject | 165 |
| **outputs** | **3441** |
| **upscaler runs** (one per source photo) | **756** |
| **estimated upscale time** | **~24 hours** |

Per-plan upscaling would instead need 2467 runs and about 37.2 hours; enlarging each photo's
whole frame once accounts for the difference (section 7).

The multiplication from 894 inputs to 3441 outputs is the crop-into-thirds behavior, which
fires on 318 images in the desktop pass and 591 in the phone pass.

The 165 rejections are **output plans, not images**. No image in the corpus is rejected on
both its desktop and its phone pass, so on a full `-b` run `error/` receives nothing and
every source ends in `originals/`. `error/` earns its place only for `-d` or `-p` runs and
for sources smaller than anything in this corpus.
