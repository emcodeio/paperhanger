# paperhanger

A paperhanger measures, trims, and hangs wallpaper. This tool does the same for images: it takes a folder of photos, decides whether each one suits a desktop or a phone, crops tall or wide images into thirds where that yields better wallpapers, upscales with a machine-learning model only when the source is too small, resizes to the exact target, and writes HEIC files ready to hang.

It replaces a set of zsh scripts that depended on Pixelmator Pro and ImageMagick. Neither is needed now.

## Requirements

macOS, and Python 3.14 or newer. The tool has no third-party runtime dependencies. It shells out to two programs:

- `sips`, which ships with macOS, for measuring and for the colour conversion on the upscale path. Cropping, resizing and encoding are CoreGraphics calls now.
- `upscayl-bin`, the Upscayl ncnn command-line upscaler, for 4x enlargement. One self-contained binary plus one model file, installed by `paperhanger setup`.

`magick` (ImageMagick) appears in the test suite for image comparison. It is not needed to run the tool.

## Install

    uv tool install .
    paperhanger setup     # downloads and verifies upscayl-bin + the model, ~60 MB
    paperhanger doctor    # confirms what was found

`setup` pins what it fetches. The release tag, the binary's archive, and both model files each have a recorded SHA-256, and the model comes from a pinned git commit rather than a branch. Downloads stage to a `.partial` file and are verified before being renamed into place. A digest mismatch aborts the install rather than falling back to whatever arrived.

## Use

    paperhanger [-d | -p | -b] [--dry-run] [--format FMT] [--quality N]
                [--overwrite] [--allow-nested] [--processing-dir DIR] <file-or-dir>
    paperhanger setup      # download and verify upscayl-bin and its model
    paperhanger doctor     # report what is found, what is missing, and the pinned release setup would install

| Flag | Meaning |
|---|---|
| `-d` | desktop only |
| `-p` | phone only |
| `-b` | both (default) |
| `--dry-run` | print the plan and exit; writes nothing |
| `--format {heic,jpeg,avif,png}` | output format, default `heic` |
| `--quality N` | 0-100; overrides the format's default quality. An error for `png`, which is lossless |
| `--overwrite` | regenerate outputs that already exist, instead of skipping them |
| `--allow-nested` | permit an input that contains, or sits inside, the processing directory |
| `--processing-dir DIR` | where outputs and archives go (default: `~/Pictures/wallpaper/processing`) |
| `<file-or-dir>` | an image file, or a directory of them (not recursive) |

Start with `--dry-run`. It prints every output the run would produce, the bands they fall in, and a time estimate, and it writes nothing.

### Two guards that are not interchangeable

`--overwrite` turns off the skip-existing check, so a resumed run redoes work it had already finished.

`--allow-nested` turns off a different guard (`check_guard` in `paperhanger/cli.py`), which refuses two overlapping shapes: a directory input that contains, is, or sits inside the processing directory, and a single file input that lives inside the processing tree. The second case catches one of the tool's own outputs, or an already-archived original, being fed back in.

Keeping them separate means resuming a run and permitting a nested layout are independent decisions. One flag covering both would force anyone with a nested layout to disable skip-existing too, which turns an interrupted import into a full re-upscale.

### Originals are moved, not copied

A successfully processed source image ends up in `originals/`, or `error/` if every device rejected it. It does not stay where it was found.

This is why the nesting guard exists. The author's corpus at `~/Pictures/wallpaper` sits directly above the default processing directory, so the most ordinary invocation of this tool is one habit away from relocating hundreds of originals into a subdirectory of themselves, and having the next run find them there and do it again.

The archive move never overwrites. If a file of that name already exists in `originals/` or `error/`, the incoming one is renamed with a numeric suffix (`IMG_0042-2.jpg`) and the rename is reported.

## How it decides

Each image is measured once, then routed by shape and size.

**Four targets.** Each pairs a device with the axis that sizes it, and carries an ideal and a floor:

| Target | Ideal | Floor |
|---|---|---|
| desktop by width | 7680 | 5120 |
| desktop by height | 4800 | 3200 |
| phone by height | 4320 | 2880 |
| phone by width | 2880 | 1920 |

Ideal divided by floor is exactly 1.5 on all four. That ratio is the rule that nothing is ever enlarged by less than 1.5x, expressed as a table rather than as a constant the code consults.

**Crops into thirds.** A portrait image asked for a desktop wallpaper, or a landscape image asked for a phone, cannot fill the frame without distortion. Rather than stretch it, paperhanger cuts three slices and treats each as its own candidate. Nothing is ever stretched or squashed.

**Five bands.** With a target chosen, the governing dimension `d` decides what happens:

| Band | Condition | What happens |
|---|---|---|
| 1 downscale | `d >= ideal` | reduce to the ideal size |
| 2 native | `floor <= d < ideal` | encode as-is, no resampling |
| 3 upscale+reduce | `d < floor`, `d*4 >= ideal` | enlarge 4x, reduce to the ideal |
| 4 upscale-only | `d*4` lands between floor and ideal | enlarge 4x, no reduction |
| 5 reject | `d*4 < floor` | too small for this device |

**No image is ever enlarged except by the model.** There is no non-AI upscaling anywhere in the tool, and a runtime check refuses any render that would enlarge without it.

**Each photo is upscaled once.** When several outputs from one source need the model, the whole frame is enlarged a single time and every slice is cut from that 4x frame. Over the author's 894-image corpus this is the difference between roughly 24 hours and 37. Above a 300 Mpx 4x frame the whole-frame path would strain memory, so those photos fall back to enlarging each slice separately.

Whether cutting from an enlarged frame differs from enlarging each cut was measured rather than assumed. Both were scored against original pixels across 56 comparisons: the two are interchangeable, so the design keeps the cheaper one. `docs/research/2026-09-13-whole-frame-equivalence-gate.md` has the protocol and the numbers.

## Output

```
~/Pictures/wallpaper/processing/        (--processing-dir overrides)
  originals/
  error/
  to_sort_desktop/
    below_target/
  to_sort_phone/
    below_target/
```

A source image is archived to `originals/` once every plan for it has succeeded, been rejected, or was already done. It goes to `error/` instead if it was too small for every requested device.

If some outputs succeeded and at least one failed, the source stays where it was found rather than being archived, and is reported as `partial`, so a re-run can still find it. The same holds if `sips` or `upscayl` failed on everything for that photo, or the run was killed before any of its plans finished. If rendering succeeded but the archive move itself failed, on a permissions error say, the source is again left in place: the outputs are already written and correct, and a re-run retries just the move.

### What `below_target/` means

It holds two different kinds of file, and telling them apart matters:

- **Band 2.** Images at their native resolution, already close enough to the ideal that the model was never needed. Real pixels, untouched, and the highest-fidelity output the run produces.
- **Band 4.** Images the model enlarged 4x that still fell short of the ideal. Every pixel invented, and still not big enough.

The dimensions and the directory cannot tell you which is which. Only the factor token in the filename can.

### Filenames

`<basename>_<device>_<W>x<H>_<factor>.<ext>`. Crop slices carry their position in the basename so the three cannot collide:

```
cliffs_desktop_7680x4800_native.heic     band 1, real pixels, downscaled
lichen_desktop_6000x3750_native.heic     band 2, real pixels, untouched
rowan_desktop_7680x4800_1.6x.heic        band 3, enlarged 4x then reduced
sunset_top_desktop_7680x4800_2.6x.heic   band 3, a crop slice
moth_desktop_6400x4800_4x.heic           band 4, in below_target/
```

`<factor>` is the net enlargement in the finished file, to one decimal. It reads `native` for bands 1 and 2, where the model never ran. In band 3 it is ideal-over-source, which falls between `1.5x` and `4x` because the 4x frame is reduced down to the ideal. In band 4 it is a flat `4x`: nothing is reduced afterward, so the 4x frame is the output.

The token exists because dimensions alone cannot answer the question that matters when sorting the output later. Two files can share the exact same `WxH` while one came from a source with every pixel real and the other from a source four times too small.

### Formats and quality

`--format {heic,jpeg,avif,png}`, default `heic`. `--quality N` overrides the default for the lossy formats. Passing it with `png` is an error rather than a silent no-op.

| Format | Extension | Default quality |
|---|---|---|
| heic | `.heic` | 80 |
| jpeg | `.jpg` | 90 |
| avif | `.avif` | 85 |
| png | `.png` | lossless |

The defaults differ per encoder because each sits at that encoder's own quality-to-size knee rather than at a shared number. HEIC's encoder quantizes such that 85 produces a file byte-identical to 80, so 80 is the real ceiling before 90 nearly doubles the size. JPEG's curve favours 90 over both 85 (worse fidelity for 5% less size) and 95 (10% more size for less improvement). AVIF at 85 lands in HEIC 80's quality neighbourhood at about 19% smaller.

## Working around sips

`sips` ships with macOS and needs no install, which is most of why it was here. It also fails silently in seven measured ways, each producing a plausible-looking wrong file at exit 0. They are documented with their measurements at the top of `paperhanger/imaging.py`, and they are why the imaging layer is moving to CoreGraphics one operation at a time.

**`--cropOffset 0 0` returns the centred crop, at the correct dimensions.** No error, no warning, nothing in the output to indicate it. Because the dimensions are right, no size check can catch it; only comparing the returned pixels against the region requested will. The workaround padded one pixel on every side and cropped at +1 — a full rewrite of an image that, on the upscale path, had already been quadrupled.

**The crop itself no longer goes through `sips`.** It is `CGImageCreateWithImageInRect`, which honours any origin, so the pad and its magenta bleed are gone. `crop` still spawns one `sips` per call — `probe`, for the bounds check — so this is three subprocesses per padded crop down to one, not down to zero; zero arrives when `probe` moves too. Two behaviours changed with it: a 16-bit source keeps its depth where `sips` dropped it to 8-bit, and a baseline JPEG of more than a million pixels gives slightly different pixels, because ImageIO decodes a *region* of one differently from the whole frame — a mean absolute error under 1.4 out of 255 on real photographs, in both the old implementation and the new.

Every crop in the tool routes through `imaging.crop` rather than calling an imaging API directly, which is what keeps the bounds check — still needed, since CoreGraphics silently returns the overlap where `sips` silently padded with black — from being forgotten at a new call site.

**The resample no longer goes through `sips` either.** It is a `CGContextDrawImage` at interpolation High into a bitmap built from the source's own colour space, and both output dimensions are always passed explicitly — `--resampleWidth` and `--resampleHeight` derive the other axis and round it inconsistently, which is the third of the seven defects.

**Nor does the encode, and the intermediate went with it.** Resample and encode are now one pass over one decoded frame: one `CGImageDestination`, no lossless PNG staged beside the output and read back — which for a 7680×5120 desktop slice was a ~110 MB file written and decoded for nothing. `-s formatOptions N` was always `kCGImageDestinationLossyCompressionQuality` at N/100, `sips` being ImageIO underneath, so the quality defaults are the same numbers producing the same bytes: measured byte-identical at JPEG 80 and 90, HEIC 80, 85 and 90, and AVIF 85, including HEIC 85 still reproducing HEIC 80's file exactly.

**A resample that changes nothing now does nothing.** A fifth of the resamples a full run performs — 577 of 2734 — ask for the dimensions the image already has, because a phone slice of an upscaled frame is rendered at scale 4 and the render asks for a resample either way. Those hand the decoded frame straight to the encoder rather than drawing it at 1:1, which is not merely faster: over 63 corpus photographs at their own dimensions the pass-through matches `sips`' output on 63 of 63 where the 1:1 draw matches on 11, and on a 7680x5120 frame it holds peak memory to 351 MiB against the draw's 499 (`sips` itself peaks at 332). The draw is what introduced the difference — an alpha-bearing source loses a unit of precision to the premultiply round trip — so skipping it removes a divergence rather than trading accuracy for speed.

Two behaviours changed with the resample. A 16-bit source keeps its depth, as with crop. And an unreadable source now fails inside ImageIO, naming the file, rather than as a `sips` warning on a zero exit — the same exception type, and still no output file left behind.

**Outputs no longer carry the source's EXIF.** `sips` copied it forward; `CGImageDestinationAddImage` writes the picture and its colour profile and nothing else. On 21 of the 27 coverage images the HEIC and AVIF outputs are 127 to 2332 bytes smaller than `sips`' for that reason alone, with the coded picture identical underneath. It applies to JPEG too, where both tools write an Exif segment and only `sips` fills it from the source. The ICC profile is unaffected — an ICC-tagged source with no EXIF encodes byte-identically, whatever the profile — so nothing about colour changes; what is gone is the camera metadata riding along inside a wallpaper. The tag that would have been visible is Orientation, and no image in the corpus carries a rotating one: of 894, 257 carry Orientation 1, one the invalid 0, 128 carry EXIF without the tag, and the rest carry none.

The same change makes a **progressive JPEG source come back baseline**, where `sips` inherited the source's scan: 10 to 21% more bytes on the four progressive images in the sample, and the same picture in the strongest sense — asked for progressive, ImageIO produces `sips`' own entropy-coded scan byte for byte on all four, two of them whole files included.

## Testing

Four tiers. Only the first three are part of "implemented". The full-corpus run is the author's acceptance pass, deliberately outside the definition of done.

| Tier | What | Cost | When |
|---|---|---|---|
| 1 | generated fixtures, pure functions, stubbed upscaler | ~90 s | every commit |
| 2 | 27 real corpus images, stubbed upscaler | ~8 min | before every push |
| 3 | the same 27 images, real upscaler (21 model calls) | ~25 min | once, before calling the tool done |
| 4 | all 894 images in the real corpus | ~24 h | the author's acceptance pass, not a gate |

Fast loop, every commit. Tier 1 alone, 526 tests in about 90 seconds:

    uv run pytest

That is the default. `pyproject.toml` sets `addopts = "-m 'not corpus and not real_upscaler'"`, so the bare command is the per-commit gate.

Before a push, tiers 1 and 2, about 8 minutes:

    uv run pytest -m "not real_upscaler"

Either slow tier on its own:

    uv run pytest -m corpus                    # tier 2, ~8 min
    uv run pytest -m real_upscaler             # tier 3, ~25 min, needs the real binary

**A `-m` on the command line replaces `addopts`. It does not AND with it.** That has two consequences:

- `uv run pytest -m "not corpus"` re-admits tier 3: 25 minutes, and a hard failure without `upscayl-bin` installed. Say `-m "not real_upscaler"` when what you mean is "everything but the slowest tier".
- `uv run pytest tests/test_corpus_sample.py` selects nothing. The default `-m` still applies and every test in that file carries the `corpus` marker, so pytest reports `no tests collected (19 deselected)`. Add `-m corpus` to run a slow-tier file by name.

Tier 2 tests are marked `corpus` and skip cleanly on a machine that has never seen `~/Pictures/wallpaper`. That corpus is read-only: nothing in this repo writes to it, and the images are never committed. `tests/corpus_sample.txt` names 27 filenames, copied to scratch at test time.

Tier 4 is not a pytest run at all. It is the author processing all 894 images once, on purpose, to judge what no assertion covers: whether the crops are worth keeping, whether HEIC 80 was the right call, whether `below_target/` is a useful distinction in practice.

## Troubleshooting

**`paperhanger doctor` reports the binary missing.** Run `paperhanger setup`. If it was installed and then quarantined by Gatekeeper, remove the quarantine attribute or re-run setup.

**Everything is reported as a non-image.** The tool detects images by parsing `sips` output rather than its exit status, so a missing or quarantined `sips` would produce this. `paperhanger doctor` checks for it; a run now refuses to start rather than report a whole library as unreadable.

**A run was interrupted.** Re-run the same command. Finished outputs are skipped, sources whose outputs are incomplete were left in place, and stray `.partial` files are swept at startup.

**An output is in `below_target/` and you expected full size.** Check the factor token in the filename. `native` means the source was already between the floor and the ideal and was left alone. `4x` means the model enlarged it as far as it goes and it still fell short.

## Status

All sixteen planned tasks are complete, reviewed, and green: 551 tests across the three gating tiers, 526 in tier 1, 19 against the real corpus, 6 driving the real upscaler.

One known optimisation is outstanding. The crop workaround pads once per slice where it could pad each 4x frame once, measured at 1.22 s against 0.58 s on a 6000x4000 source. It is a speedup, not a correctness gap, and it is tracked as a repo issue.

## Lineage

The original scripts live in `~/.dotfiles/bin/shell_scripts/make_wallpaper/` and remain in use until paperhanger replaces them. See `legacy/README.md` for what each file was.

The legacy aliases map to paperhanger as follows, offered as the intended replacement rather than installed by anything here. Editing `~/.dotfiles` to point them at `paperhanger` is left to the author:

| Alias | Maps to |
|---|---|
| `mkw` | `paperhanger -b` |
| `mkwdesk` | `paperhanger -d` |
| `mkwphone` | `paperhanger -p` |
| `mkwproc` | no successor |

`mkwproc` is left unmapped rather than guessed at. All four aliases pointed at the same script, so what is missing is not which program `mkwproc` invoked but the arguments it passed, and the alias bodies live in the dotfiles at `shell/old/aliases_shared.zsh` rather than in `legacy/`. The script parses only `getopts ":dpb"` (`legacy/make_wallpaper.zsh:383`), so `mkwproc` was not a fourth mode. It wrapped one of these three, and nothing kept here says which.

See also:

- `docs/superpowers/specs/2026-09-11-paperhanger-design.md` — the full design spec these sections summarize.
- `docs/research/2026-09-11-replacing-pixelmator-and-imagemagick.md` — the research that established the upscaler choice, the `sips` findings, and the legacy behaviors this tool preserves or changes.
- `docs/research/2026-09-13-whole-frame-equivalence-gate.md` — how the whole-frame upscaling decision was measured.
- `docs/research/2026-09-13-final-branch-review.md` — the merge-readiness review, and what remains thin.

## License

MIT. See `LICENSE`.
