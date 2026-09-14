# Replacing Pixelmator Pro and ImageMagick in the wallpaper pipeline

Research handoff, 2026-09-11. Everything below was established by direct testing on the author's machine plus web research in a single Claude Code session, before any design work began.

## 1. Purpose and status

This document records what is known so that the design session does not have to rediscover it. It is **not a spec**. It states facts, measured results, and the questions that remain open. Design decisions belong in `docs/superpowers/specs/`.

Sections 2-11 are the original handoff of 2026-09-11 and are left as written. **Section 12 was added on 2026-09-14**, when `sips` was replaced by CoreGraphics and the measured defects came out of the code and into the record. Seven of them are `sips`' and are what motivated the move. The eighth was found during it, and is ImageIO's rather than `sips`' -- which means it is still ours, because ImageIO is what the tool calls now. Section 12 separates them.

## 2. What the legacy pipeline does

Source: `legacy/make_wallpaper.zsh` (current) and `legacy/notes.org` (its design notes).

**Input.** A file or a directory. Flags `-d` (desktop only), `-p` (phone only), `-b` (both, default).

**Classification by aspect ratio** (width / height, computed by `bc` at two decimal places):

| Device | Condition | Sized by | Max target | Min target |
|---|---|---|---|---|
| Desktop | ratio ≤ 1.60 and > 1 | width | 7680 | 5120 |
| Desktop | ratio > 1.60 | height | 4800 | 3200 |
| Desktop | ratio ≤ 1 | crop into three 16:10 slices (top, middle, bottom), recurse on each as desktop-only | | |
| Phone | ratio ≥ 0.66 and < 1 | height | 3840 | |
| Phone | ratio ≥ 0.66 and ≥ 1 | crop into three 2:3 slices (left, center, right), recurse on each as phone-only | | |
| Phone | ratio < 0.66 | width | 2560 | |

**Upscale tiers** (dim = the governing dimension of the source):

| Condition | Tier suffix | Output directory |
|---|---|---|
| dim × 3 ≥ max | `lt_3x` (upscale to exactly max) | `to_sort_<device>/` |
| dim × 3 ≥ min | `3x` (upscale to exactly dim × 3) | `to_sort_<device>/` |
| dim × 4.25 ≥ min | `4x` (upscale to exactly min) | `to_sort_<device>/4x_upscaled/` |
| otherwise | reject | original moved to `error/` |

Phone has no `3x` middle row; it goes straight from `lt_3x` to `4x`.

**Upscaling** is done by Pixelmator Pro through AppleScript: open, `resize image <dimension> <target> resolution 72 algorithm ml super resolution`, export HEIC with compression factor 85, close without saving.

**Output naming:** `<basename>_ml_res_<tier>_<device>.heic`. Crop slices are PNG named `<basename>_top|middle|bottom.png` or `_left|center|right.png` and are deleted after processing.

**Directory layout** under `~/Pictures/wallpaper/processing/`: `originals/`, `error/`, `to_sort_desktop/`, `to_sort_desktop/4x_upscaled/`, `to_sort_phone/`, `to_sort_phone/4x_upscaled/`. Successfully processed originals move to `originals/`.

**Rescue pass** (`legacy/check_error_images.zsh`): one target size per device, single 4.25× ceiling, output straight to `4x_upscaled/`, never moves inputs.

## 3. Behaviors to preserve vs. change

**Preserve** (the user considers these the tool's identity):
- Aspect-ratio thresholds and the width/height selection rule.
- Crop-into-thirds behavior and its recursion, including that crops are forced to a single device.
- The processing directory layout and the originals/error handling.
- Output as HEIC.

**Change** (agreed during research):
- **Source already ≥ target → resize only, never upscale.** The legacy downscale branch is commented out, so every image goes through Super Resolution. Observed cost: a 4912×7360 source spent 7 minutes producing a 19648×29440 upscale that was then shrunk to 3840 tall. This is the largest speed win available.
- **The `3x` tier is a Pixelmator artifact.** Its model happened to be 3×. The replacement upscaler is 4× native, so the tiers reduce to "upscale needed or not" plus the 4.25× rejection ceiling. Whether the suffix scheme survives is a design question.

**Flag for design** (behavior that exists today and may or may not be intended):
- With `-b`, an image rejected on the desktop pass is moved to `error/` and never gets a phone pass, even if it would have made a fine phone wallpaper.
- Aspect ratio is compared at two decimal places (`bc scale=2`), so 1.605 is treated as 1.60. An implementation quirk, not a requirement.
- Crop-slice cleanup relies on the slice first being moved to `originals/` and then deleted from there.
- Non-image files such as `.DS_Store` in the input directory are fed to ImageMagick and error out.

## 4. Upscaler replacement: Upscayl ncnn binary

**What it is.** `upscayl-bin`, the command-line backend of the Upscayl app: a C++ ncnn/Vulkan implementation of Real-ESRGAN. Repo `upscayl/upscayl-ncnn`, AGPL-3.0, last pushed 2026-04-11.

**Pinned artifact.**

| Item | Value |
|---|---|
| Release tag | `20251207-174704` |
| macOS zip | `https://github.com/upscayl/upscayl-ncnn/releases/download/20251207-174704/upscayl-bin-20251207-174704-macos.zip` |
| SHA-256 (zip) | `277419791281a56eae0c739c70120b974d7267cf7c2de8e86dc09798d4b314db` |
| Binary | universal Mach-O (x86_64 + arm64), 28 MB. `otool -L` shows only system frameworks (Metal, QuartzCore, CoreGraphics, Cocoa, IOKit, IOSurface, Foundation, AppKit, libc++, libSystem, libobjc). MoltenVK is statically linked. No Vulkan SDK or driver install needed. |

**Pinned model.** `upscayl-standard-4x` from `upscayl/upscayl` at `resources/models/`, commit `6cfaf45b2aae2847cba4f2313b57ca20a0ddd79c`.

| File | Size | SHA-256 |
|---|---|---|
| `upscayl-standard-4x.bin` | 33 MB | `713ee713b0353afaa27976f0563a64a5043bd70b9bd8936c2e26e25ebcdbcddf` |
| `upscayl-standard-4x.param` | 116 KB | `35330ececcea33b6c397a72548e788d5d53becee4734c50b7fada36e89f10a86` |

**Verified on this machine** (M3 Max, macOS 26): verbose log reports Vulkan device `[0 Apple M3 Max]` with fp16 support. Runs unzipped with no Gatekeeper intervention when invoked from a shell.

**Usage that matters.**

```
upscayl-bin -i <in> -o <out.png> -m <models dir> -n upscayl-standard-4x -s 4 [-f png|jpg|webp] [-t tile] [-v]
```

- `-i`/`-o` accept a file or a directory. Directory mode processes a whole batch in one process.
- Input formats: jpg, png, webp **only**. Anything else (HEIC, TIFF) must be converted first.
- Output: 8-bit PNG/JPEG/WebP regardless of input depth.

**Measured limits.**
- `-w <width>` and `-r <WxH>` (resize output) **did nothing** in testing; output stayed at the raw 4× size. Do the final resize with `sips`.
- `-s 2` and `-s 3` with the 4× model still run the full 4× network and then resize internally. Time saving was under 15 percent. There is no direct-to-target mode; a Real-ESRGAN network always emits input × native factor.
- `-z 2` (declare model scale) with the 4× model is ignored; the binary detects the scale from the param file.

**Model notes.**
- `upscayl-standard-4x` produced clean, natural results on photos. Chosen.
- `remacri-4x` hallucinated hair-like strands across grass. Avoid for photos.
- Upscayl ships only 4× photo models. Real-ESRGAN's `x2plus` is a genuine 2× photo model but is published in PyTorch format only. The one public ncnn conversion found (Hugging Face `bpawnzZ/Real-ESRGAN-x2plus-NCNN`) fails to load in this binary with `layer Shape not exists or registered`. Producing a working one means following Upscayl's Model Conversion Guide (chaiNNer, then rename `input` to `data` in the param). Only worth doing as a speed optimization for images needing < 2× enlargement.

**Timings on M3 Max** (wall clock, includes process start):

| Input | Model | Output | Time |
|---|---|---|---|
| 854×1280 JPEG | standard-4x | 3416×5120 | 10.8 s |
| 1280×853 JPEG | standard-4x | 5120×3412 | 15.7 s |
| 1920×1200 PNG | standard-4x | 7680×4800 | 32 s |
| 1280×1920 PNG, `-s 3` | remacri-4x | 3840×5760 | 23 s |
| 4912×7360 JPEG | standard-4x | 19648×29440 | 413 s (the wasted-work case) |

For comparison, Pixelmator Pro's ML Super Resolution on the 854×1280 image took 3.5 s warm and about 27 s including a cold app launch.

## 5. ImageMagick replacement: `sips`

`sips` (Scriptable Image Processing System) ships in `/usr/bin` on every Mac and covers all four jobs ImageMagick did. All verified on this machine.

| Job | Command shape | Result |
|---|---|---|
| Dimensions | `sips -g pixelWidth -g pixelHeight <file>` | Works. Reads JPEG, PNG, WebP, HEIC, TIFF, AVIF. |
| Offset crop | `sips -c <H> <W> --cropOffset <Y> <X> <in> --out <out>` | **Pixel-identical** to `magick -crop WxH+X+Y` on a lossless test (0 differing pixels). Argument order is height-then-width, and Y-offset before X-offset. |
| Resize | `sips --resampleHeight <H>` or `--resampleWidth <W>` | 7680→5120 wide in 1.2 s vs 9.7 s for ImageMagick Lanczos. Filter is not selectable (Core Graphics). PSNR vs Lanczos: 53 dB on a synthetic image, 40 dB on a real photo. Visually equivalent at 1:1, marginally softer. |
| HEIC export | `sips -s format heic -s formatOptions <0-100> <in> --out <out.heic>` | Works. `sips --formats` lists heic, avif, png, jpeg, tiff, jp2 as writable. The man page's format list is stale and omits HEIC. |

Caveats: one input per invocation; warns when the output extension does not match the format (harmless); the quality percent is on Apple's encoder scale (see section 7).

**How each of those four jobs actually fails is section 12**, written after the tool was replaced. Every entry in this table was true; none of it is the whole story, and the crop row in particular is pixel-identical to ImageMagick only on the offsets `sips` does not ignore.

**Alternative if in-process pixel work is wanted:** Pillow 12.3 + pillow-heif 1.7 install cleanly under `uv run --python 3.14` and handled every job (true Lanczos, bundled libheif). Cold start about 5 s. Not needed if `sips` is called via subprocess.

## 6. Quality evidence

**Protocol.** A 4912×7360 photograph (rowan berries, Unsplash, `anita-austvika-vNJgLXzON-Y`) was downscaled to 854×1280 JPEG to simulate a web download, then brought back to 3840 tall three ways. The original downscaled to 3840 served as ground truth.

| Method | Time | PSNR vs truth |
|---|---|---|
| Upscayl standard-4x, then `sips` to 3840 | 10.8 s | 33.6 dB |
| Pixelmator ML Super Resolution to 3840 | 3.5 s | 35.3 dB |
| Plain Lanczos resize | < 1 s | 36.0 dB |

**PSNR inverts the visual ranking.** It rewards blurry averages and penalizes any invented detail even when it looks right. Trust the crops.

**Visual comparison** (`2026-09-11-upscaler-comparison-rowan.jpg`, columns: ground truth, Pixelmator, Upscayl, Lanczos; rows: berries, leaf edges, lower cluster, bokeh; each cell a 640×480 crop at 1:1 from the 3840-tall outputs):

- **Berries.** Upscayl gives clean rounded surfaces and crisp calyxes, closest to truth on edges; loses fine skin texture (slightly waxy). Pixelmator sharper than Lanczos but retains JPEG blockiness.
- **Leaf edges.** Upscayl renders serrations and midribs cleanly. Pixelmator softer with blotchy flat areas.
- **Lower cluster.** Same pattern; dried berries plausible in Upscayl, noisier in Pixelmator.
- **Bokeh.** All four effectively identical. No ringing or invented texture in blur.

The user reviewed the grid and chose Upscayl as better than the current Pixelmator output.

Full-size artifacts from the test remain in `~/Downloads/upscale_test/` on the author's machine (not committed).

## 7. HEIC quality calibration

Encoder quality scales are not comparable across tools. Same 7680×4800 image:

| Encoder | Setting | File size |
|---|---|---|
| Pixelmator Pro | compression factor 85 | 2.3 MB |
| ImageMagick (libheif) | quality 85 | 20 MB |
| ImageMagick (libheif) | quality 50 | 2.1 MB |

`sips` on a 2562×3840 upscaled image: quality 60 → 300 KB, 75 → 390 KB, 85 → 430 KB. Suggested starting point is `sips` quality 75; the final value should be chosen by eye during design or a calibration task.

## 8. Environment facts

- Apple M3 Max, 36 GB, macOS 26 (Darwin 25.6.0).
- `/usr/bin/sips`, `/usr/bin/bc`, `/usr/bin/osascript` present.
- Python 3.14.7 and `uv` present (Nix-managed). `coremltools` has no native wheels for 3.14 yet, which rules out Core ML based upscalers without pinning an older Python.
- ImageMagick 7.1.2 installed via Homebrew with HEIC delegate. To be dropped as a dependency.
- Pixelmator Pro 4.x installed. Apple completed its acquisition in February 2025; still sold as a $49.99 one-time purchase; its AppleScript `resize image ... algorithm ml super resolution` and HEIC export still work. Kept as a fallback reference only.
- `~/Pictures/wallpaper/` did not exist on this machine at research time; end-to-end tests need a fresh tree.

## 9. Open design questions for brainstorming

1. **Language.** Python under `uv` calling `sips` and `upscayl-bin` via subprocess, versus a cleaned-up zsh script. Both need zero third-party packages.
2. **Fetching the binary and model.** About 60 MB total, not to be committed. A bootstrap script with pinned URLs and the SHA-256 values above, or a documented manual step? Where do they live (`vendor/`, `~/.local/share/paperhanger/`, next to the script)?
3. **2× model.** Skip for now (recommended), or convert `x2plus` to ncnn as a speed optimization.
4. **Output naming.** Keep `_ml_res_<tier>_<device>` or simplify now that tiers collapsed.
5. **HEIC quality value** and whether the resized-only (no upscale) path uses the same value.
6. **Rejection semantics.** Should a desktop rejection still abort the phone pass?
7. **Input normalization.** HEIC/TIFF sources need conversion to PNG before the upscaler; where does that happen and is the intermediate kept?
8. **Batch strategy.** One `upscayl-bin` process per image (simple) or directory mode (faster, but every image in the batch gets the same scale).
9. **Test fixtures.** Synthetic images generated at test time versus a few tiny real photos checked in.
10. **Aliases.** Whether `mkw`, `mkwproc`, `mkwdesk`, `mkwphone` get repointed or retired.

## 10. Recommended process

Open a new Claude Code session in this repo and proceed with the superpowers skills in order:

1. **brainstorming** — architectural path (new project). It reads this document and `legacy/`, asks one question at a time, proposes approaches, and writes the spec to `docs/superpowers/specs/`.
2. **writing-plans** — produces `docs/superpowers/plans/<date>-<name>.md` with code in every step.
3. **subagent-driven-development** — executes the plan in a worktree, one fresh subagent per task with spec and quality review after each. Model routing sends mechanical tasks to Sonnet.
4. **finishing-a-development-branch** — verifies and merges.

`.superpowers/` and `.worktrees/` are already git-ignored.

## 11. Sources

- Upscayl ncnn backend: https://github.com/upscayl/upscayl-ncnn and releases https://github.com/upscayl/upscayl-ncnn/releases
- Upscayl app and bundled models: https://github.com/upscayl/upscayl
- Upscayl Model Conversion Guide: https://github.com/upscayl/upscayl/wiki/Model-Conversion-Guide
- Best model for photography discussion: https://github.com/orgs/upscayl/discussions/945
- Real-ESRGAN: https://github.com/xinntao/Real-ESRGAN and model zoo https://github.com/xinntao/Real-ESRGAN/blob/master/docs/model_zoo.md
- Real-ESRGAN Apple GPU support issue (open): https://github.com/xinntao/Real-ESRGAN/issues/902
- 2× RealESRGAN_x2plus on OpenModelDB (PyTorch only): https://openmodeldb.info/models/2x-realesrgan-x2plus
- Broken ncnn conversion of x2plus: https://huggingface.co/bpawnzZ/Real-ESRGAN-x2plus-NCNN
- State of Vulkan on Apple, Jan 2026: https://www.lunarg.com/the-state-of-vulkan-on-apple-jan-2026/
- PiperSR (2× Core ML, Neural Engine): https://github.com/ModelPiper/PiperSR
- coremltools Python 3.14 wheels issue: https://github.com/apple/coremltools/issues/2695
- chaiNNer CLI: https://github.com/chaiNNer-org/chaiNNer/wiki/05--CLI
- sips man page: https://keith.github.io/xcode-man-pages/sips.1.html
- sips crop offsets: https://blog.smittytone.net/2021/06/02/crop-picture-files-with-confidence-and-pixel-precise-offsets-using-sips-and-imageprep/
- pillow-heif releases: https://github.com/bigcat88/pillow_heif/releases
- Pixelmator Pro acquisition: https://www.macrumors.com/2025/02/11/apple-completes-pixelmator-acquisition/
- Apple Creator Studio pricing: https://tidbits.com/2026/01/15/apple-bundles-pro-apps-into-new-creator-studio-subscription/

## 12. The seven measured `sips` defects, and the eighth we added

*Added 2026-09-14, when `sips` left the tool.* Section 5 above recommended `sips` and it was the right recommendation: it shipped on every Mac, it did all four jobs, and the pipeline it replaced was worse. What section 5 could not know is how each job fails. Eight things were measured over the implementation and the migration off it, and every one of them fails **silently** — a zero exit, a valid file, the requested dimensions, and the wrong picture.

They lived in `paperhanger/imaging.py`'s module docstring while that module ran `sips`, as constraints on the code. No code in the project runs `sips` any more, so they are findings about a tool rather than constraints on anything, and they belong here. **The numbering is unchanged and is still referenced by name** from `paperhanger/execute.py`, `paperhanger/bands.py` and several test docstrings, so "fact 6" resolves to this section and nowhere else.

Two of them are not retired by the move. Fact 4's post-condition outlived the tool that motivated it — `imaging._run(produces=...)` still asserts that a command wrote the file it was asked for, and `upscayl-bin` is now its only caller. And fact 8 was never a `sips` defect at all: it is ImageIO's, which means it is still ours.

### The seven

**1. `sips -g pixelWidth` exits 0 while printing `pixelWidth: <nil>`** for text, empty and truncated files, and dies by signal on `.DS_Store`. Exit status is not a usable signal; stdout is. Measured again while replacing it, on the real corpus `.DS_Store`: SIGABRT (returncode −6), on an uncaught `NSInvalidArgumentException` complaining that `typeIdentifier` cannot be nil. ImageIO in the same position builds a `CGImageSource` for the file quite happily and then reports a count of 0, no type, and no image at index 0 — so the replacement declines it by answering the question rather than by surviving the answer.

**2. Crop must never be fused with a resample.** `sips -c 1080 1920 --cropOffset 0 500 --resampleWidth 960` on a 3840-wide source returns 480×270, not 960×540 — the resample is applied against the **pre-crop** width.

**3. Both output axes must be passed.** `--resampleWidth`/`--resampleHeight` derive the other axis and round it inconsistently (2662×1663 at `--resampleWidth 7680` gives 7680×4798 where floor gives 4797), and a single hardcoded flag is simply wrong for the by-height plans.

**4. A write that `sips` SKIPS still exits 0.** Given a path it cannot read — one that does not exist, or a directory — `sips` prints `Warning: <path> not a valid file - skipping` to stderr, exits 0, and writes no output file at all. That is fact 1 again on the write path: the exit status is not the signal. Every operation therefore asserted its post-condition, that the file it was asked for actually existed afterwards. Checking the artifact rather than parsing the warning text also catches any other exit-0 no-op, whatever its wording. A corrupt file, an unwritable destination and an unknown format all exit 13 and were caught by the status check; this was only for the ones that did not.

*The post-condition survives the tool.* `upscale` runs a binary nobody here controls, and an exit-0 no-op from that one would be believed exactly the same way.

**5. An out-of-bounds crop PADS WITH BLACK.** `sips` neither clamped nor errored: a 400×200 source cropped at x=900, y=900 returned a 120×80 image that was entirely black, at exit 0, with the requested dimensions and a valid file. Neither the status check nor fact 4's post-condition can see that — the file exists and is exactly the size asked for. Only comparing the rect against the measured source catches it, which is why `crop` probes. `CGImageCreateWithImageInRect` behaves differently and no better on its own: it **intersects** with the image and hands back the overlap at no error, or NULL when there is no overlap at all, so the same bounds check is still what turns either into a refusal.

**6. `sips` SILENTLY IGNORES two `--cropOffset` shapes,** both of which need X == 0, and the pure geometry layer produces both of them for real wallpapers:

- `--cropOffset 0 0` — the offset is dropped and `sips` does its **default centred crop**. Measured on 1600×1200: `horizontal_thirds`' TOP slice, asked for 1600×1000 at (0, 0), came back as the rows at y=100, which is the MIDDLE slice. `vertical_thirds`' LEFT slice, asked for 800×1200 at (0, 0), came back as the band at x=400 — the middle one again.
- `--cropOffset <y> 0` with y + height == source height (flush with the bottom edge) — the crop is dropped entirely and the **full source** comes back. Measured: `horizontal_thirds`' BOTTOM slice, asked for 1600×1000 at (0, 200), returned the whole 1600×1200 image.

An x of 1 or more was correct at every y, and x == 0 was correct for every y strictly between those two. So two of the three desktop slices and one of the three phone slices were silently wrong. `crop` worked around it by padding 1px on every side and cropping at +1, which made x non-zero and the bottom edge non-flush. **This is the defect the whole migration exists for.** `CGImageCreateWithImageInRect` honours any origin, so the pad — a full extra rewrite of an image that on the upscale path had already been quadrupled — went with the workaround.

**7. ARGUMENT ORDER decides whether `-s format` is honoured.** Placed after `--padColor` it is silently dropped: a 2560×1600 JPEG padded with `-p H W --padColor FF00FF -s format png` came back as JPEG, byte-identical in size to the same run with no `-s format` at all (3580120 B both), while moving `-s format png` in front yielded PNG (11544706 B). `sips` warned `Output file suffix should be jpg` on stderr, which a zero exit discards.

This was not cosmetic. A lossy padded intermediate puts the magenta pad inside the same 8×8 DCT blocks as the pixels being kept, so it bleeds into the crop. Measured on a uniform (20, 90, 40) region cropped out of a q90 JPEG: mean absolute per-channel error 63.14 at column 0 and 21.87 at column 1, against 0.33 in the interior, with column 0 shifted R +73.2, G −49.1, B +67.1 — FF00FF's own signature, a quarter of the way to magenta on the outermost column. Forced to PNG the same columns measured 0.00: a uniform region survives the round trip exactly, so the whole of that error was the pad. Invisible to any dimension or file-exists check.

Note also that without `-s format`, `sips` kept the **source's** format whatever the `--out` suffix said, so a `.png` filename proved nothing about the bytes. That is why `crop` still REFUSES an `out_path` that names anything but `.png`: the name is what a reader downstream goes by. `upscale` is the one that goes by the name now, because `upscayl-bin` takes jpg, png and webp and nothing here can make it look inside first.

### The eighth, which is ours

**8. A REGION DECODE OF A BASELINE JPEG IS NOT THE WHOLE-FRAME DECODE,** once the file passes 1,000,000 pixels. **This is ImageIO, not `sips`** — `sips --cropOffset` and a plain `sips -s format png` disagree about the same file, and so do `_cg.crop_to_file` and `_cg.load`; both, by comparable margins, and not in the same direction. So it did not leave with the tool, and `paperhanger` still has it.

**THE THRESHOLD IS A ROUND DECIMAL NUMBER, NOT A POWER OF TWO,** which is not the guess anyone makes. Measured over a 200×150 region of synthetic noise JPEGs, as (sips, CoreGraphics) samples differing of 90,000:

| size | pixels | differing | size | pixels | differing |
|---|---|---|---|---|---|
| 1000×1000 | 1,000,000 | (0, 0) | 1000×999 | 999,000 | (0, 0) |
| 1250×800 | 1,000,000 | (0, 0) | 1024×976 | 999,424 | (0, 0) |
| 1250×801 | 1,001,250 | (39868, 86423) | 1152×864 | 995,328 | (0, 0) |
| 1000×1002 | 1,002,000 | (70166, 86252) | | | |
| 1024×1024 | 1,048,576 | (70202, 86167) | | | |

Exactly a million agrees; a million and change does not. Noise is the worst case by a wide margin. On four corpus photographs at a 400×300 region the mean absolute error against each tool's own frame decode is 1.038 / 0.314 / 1.383 / 0.351 out of 255 for `sips` and 0.934 / 0.332 / 1.256 / 0.417 for CoreGraphics. Progressive JPEGs show none of it.

**CHROMA SUBSAMPLING AMPLIFIES THIS; IT IS NOT WHERE IT LIVES.** Identical pixels at 1250×801, encoded three ways, samples differing of 90,000 over the 200×150 region as (sips, CoreGraphics) with the largest delta:

| encoding | differing | max |
|---|---|---|
| 4:2:0 | (39723, 86387) | 1 / 88 |
| 4:4:4 | (23297, 27042) | 2 / 4 |
| greyscale | (0, 2190) | 0 / 1 |

So it survives with no subsampling at all and survives with no chroma at all; 4:2:0 multiplies the count about threefold and the amplitude about twentyfold. Four of the eight greyscale baseline corpus JPEGs over the threshold differ at their own gate rect, every sample by 1. **On greyscale the residual is ours alone:** measured at an interior rect on all four, `sips` matches the frame decode exactly and CoreGraphics is the side that is off by one.

A crop that removes **nothing** is not enough to provoke it: a full-frame `--cropOffset 0 0` is byte-identical to a plain decode. The rect has to be strictly smaller. Measured on `acrylic_7049.JPG` (1284×2778), 400×300 at (40, 30): 212,413 of 360,000 samples differ, mean absolute error 1.0592.

So the old and new crops differ on most real JPEG sources, and **neither is the better decode**. Byte-identity was never reachable here, because the `sips` reference was itself a region decode — matching the frame decode would have moved the replacement *further* from it. It cost the crop differential its clean run over the corpus sample, and that gate accounted for it per file — given the same DECODED pixels the two implementations agreed exactly — rather than assuming it. The threshold and the absolute claim below it are now in `tests/test_imaging.py`, on our side alone.

### The 16-bit downconversion, and a misattribution corrected

`sips` dropped a 16-bit source to 8 bits. The design attributed that to the **pad** in `crop`, and called it the one defect in the old path that was ours rather than Apple's. **Measured while replacing `crop`, that is wrong.** On a 16-bit 200×200 PNG, `sips` returned 8 bits from the direct crop, from the crop at 0,0, from the pad alone, from the pad followed by the crop, and from a plain resample. Only a format convert with no pixel operation kept 16. So the pad was not the cause and the defect was never ours.

It is recorded here rather than as a ninth fact because nothing in the tool depended on it: no corpus file is 16-bit (measured — all 894 images are 8 bits per component), so it was latent throughout. `crop` and `resize_and_encode` now **keep** 16 bits where `sips` dropped them, and `normalize_to_srgb_png` deliberately does not: its only reader is `upscayl-bin`, which emits 8-bit PNG, so a 16-bit intermediate would be discarded one step later at best. All three choices are pinned on fixtures in `tests/test_imaging.py` and `tests/test_cg.py`.

### Where the rest of the evidence is

The eight above are the defects. The **measurements the replacement was accepted on** — which outputs were byte-identical to `sips` and which diverged, by how much, and what construction each divergence forced — are in the per-function docstrings of `paperhanger/_cg.py` and `paperhanger/imaging.py`, beside the code they explain. That is why the word `sips` still appears there in a project that does not run it.

The differential gates that produced them are gone. Their last run was green: 116 tier-1 comparisons over generated fixtures and 8 over the 27-image corpus sample, across crop, resample, encode and colour conversion. `tests/differential.py`, the harness they shared, is kept for the next migration that wants one.

`sips` remains installed on every Mac and is still used **test-side** as an independent oracle — `tests/conftest.py` tags fixtures with real ICC profiles through `sips --matchTo`, and `tests/test_cg.py`, `tests/test_imaging.py` and `tests/test_pixels.py` check dimensions, profiles and format names against it. Global Constraint 1 governs `paperhanger/`, not `tests/`, and an independent second implementation is worth more as a test instrument than consistency is.
