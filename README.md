# paperhanger

A paperhanger measures, trims, and hangs wallpaper. This tool does the same for images: it takes a folder of photos, decides whether each one suits a desktop or a phone, crops tall or wide images into thirds where that yields better wallpapers, upscales with a machine-learning model only when the source is too small, resizes to the exact target, and writes HEIC files ready to hang.

It runs on macOS and is designed to need almost nothing installed: the Upscayl ncnn command-line upscaler (a single self-contained binary plus one model file) and the `sips` tool that ships with macOS.

## Status

Implemented and tested. Thirteen of the fifteen planned tasks are complete, covering classification, cropping, execution, the CLI, and the report; the remaining two add the tier 3 real-upscaler test suite and this documentation pass. `paperhanger` is installable and runnable as described below. The 24-hour full-corpus run (tier 4, see Testing) has not yet been done and is not a gate on any of this.

## Install

    uv tool install .
    paperhanger setup     # downloads and verifies upscayl-bin + the model, ~60 MB
    paperhanger doctor    # confirms what was found

## Use

    paperhanger [-d | -p | -b] [--dry-run] [--format FMT] [--quality N]
                [--overwrite] [--allow-nested] [--processing-dir DIR] <file-or-dir>
    paperhanger setup      # download and verify upscayl-bin and its model
    paperhanger doctor     # report what is found, what is missing, versions

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

`--overwrite` and `--allow-nested` guard two different things and are not interchangeable. `--overwrite` turns off the skip-existing check, so a resumed run redoes work it had already finished. `--allow-nested` turns off the check that refuses to run against a directory that contains, is, or sits inside the processing directory. They were one `--force` flag in an earlier draft; that meant the flag needed to process the default nested layout (input directory above the processing directory) also disabled skip-existing, so resuming an interrupted import re-upscaled everything already done. They are separate flags now so that resuming a run and permitting a nested layout are two independent decisions.

**Originals are moved, not copied.** A successfully processed source image ends up in `originals/` (or `error/` if every device rejected it), not left where it was found. This is the reason the nesting guard exists: the author's own corpus at `~/Pictures/wallpaper` sits directly above the default processing directory, and the most ordinary invocation of this tool is one habit away from relocating hundreds of originals into a subdirectory of themselves — and having the next run find them there and do it again. The guard refuses that shape unless `--allow-nested` says it is intended. The archive move itself never overwrites: if a file of that name already exists in `originals/` or `error/`, the incoming one is renamed with a numeric suffix (`IMG_0042-2.jpg`) rather than replacing what is there, and the rename is reported.

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

A source image is archived to `originals/` once every plan for it has succeeded, been rejected, or was already done; it goes to `error/` instead if it was too small for every requested device. If some of its outputs succeeded and at least one failed, it is left exactly where it was found rather than archived at either destination — reported as `partial` — so a re-run can still find it.

`below_target/` holds two different kinds of file, and telling them apart matters: images that landed in it at their native resolution because they were already close enough to the ideal not to need the model (band 2 — real pixels, untouched, arguably the highest-fidelity output the whole run produces), and images the model enlarged 4x that still fell short of the ideal (band 4 — every pixel invented, and still not big enough). Nothing in the filename or the directory distinguishes which of the two a given file is; only the factor token does (see below).

Output name: `<basename>_<device>_<W>x<H>_<factor>.<ext>`, where crop slices carry their position in the basename so the three cannot collide:

```
cliffs_desktop_7680x4800_native.heic     band 1, real pixels, downscaled
lichen_desktop_6000x3750_native.heic     band 2, real pixels, untouched
rowan_desktop_7680x4800_1.6x.heic        band 3, enlarged 4x then reduced
sunset_top_desktop_7680x4800_2.6x.heic   band 3, a crop slice
moth_desktop_6400x4800_4x.heic           band 4, in below_target/
```

`<factor>` is the **net** enlargement in the finished file, to one decimal: `native` for bands 1 and 2, where the model never ran, and `I/d` for bands 3 and 4 — exactly `4x` in band 4, and between `1.5x` and `4x` in band 3. It exists because the dimensions in the filename cannot answer the question that matters when sorting output later: two files can share the exact same `WxH` while one came from a source with every pixel real and the other from a source four times too small. The factor token is the only place that distinction survives.

### Formats and quality

`--format {heic,jpeg,png,avif}`, default `heic`. `--quality N` overrides the default for the lossy formats; passing it with `png` is an error rather than a silent no-op.

| Format | Extension | Default quality |
|---|---|---|
| heic | `.heic` | 80 |
| jpeg | `.jpg` | 90 |
| avif | `.avif` | 85 |
| png | `.png` | lossless |

The defaults differ per encoder because each sits at that encoder's own quality/size knee, not at a shared number: HEIC's encoder quantizes such that 85 produces a file byte-identical to 80, so 80 is the real ceiling before 90 nearly doubles the size; JPEG's cost/benefit curve favors 90 over both 85 (worse fidelity for 5% less size) and 95 (10% more size for less improvement); AVIF's 85 lands in HEIC 80's quality neighborhood at about 19% smaller.

## Testing

Four tiers. Only the first three are part of "implemented" — the full-corpus run is the author's acceptance pass, deliberately outside the definition of done.

| Tier | What | Cost | When |
|---|---|---|---|
| 1 | generated fixtures, pure functions, stubbed upscaler | milliseconds | every commit |
| 2 | 27 real corpus images, stubbed upscaler | ~8 min | before every push |
| 3 | the same 27 images, real upscaler (21 model calls) | ~25 min | once, before calling the tool done |
| 4 | all 894 images in the real corpus | ~24 h | the author's acceptance pass, not a gate |

Fast loop, every commit:

    uv run pytest -m "not corpus"

Full run, before a push (this does **not** skip the slow tier — tier 2's `corpus`-marked tests run and take about 8 minutes):

    uv run pytest

Tier 2 tests are marked `corpus` and skip cleanly on a machine that has never seen `~/Pictures/wallpaper` — that corpus is read-only; nothing in this repo writes to it, and the images themselves are never committed (`tests/corpus_sample.txt` names 27 filenames, copied to scratch at test time). Tier 4 is not a pytest run at all: it is the author processing all 894 images once, on purpose, to judge things no assertion covers — whether the crops are worth keeping, whether HEIC 80 was the right call, whether `below_target/` is a useful distinction in practice.

## Lineage

The original scripts live in `~/.dotfiles/bin/shell_scripts/make_wallpaper/` and remain in use until paperhanger replaces them. See `legacy/README.md` for what each file was.

The legacy aliases map to paperhanger as follows, offered here as the intended replacement rather than installed by anything in this repo — editing `~/.dotfiles` to point them at `paperhanger` is left to the author:

| Alias | Maps to |
|---|---|
| `mkw` | `paperhanger -b` |
| `mkwdesk` | `paperhanger -d` |
| `mkwphone` | `paperhanger -p` |

See also:

- `docs/superpowers/specs/2026-09-11-paperhanger-design.md` — the full design spec these sections summarize.
- `docs/research/2026-09-11-replacing-pixelmator-and-imagemagick.md` — the research that established the upscaler choice, the `sips` findings, and the legacy behaviors this tool preserves or changes.

## License

MIT. See `LICENSE`.
