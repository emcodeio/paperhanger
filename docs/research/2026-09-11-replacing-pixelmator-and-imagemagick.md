# Replacing Pixelmator Pro and ImageMagick in the wallpaper pipeline

Research handoff, 2026-09-11. Everything below was established by direct testing on the author's machine plus web research in a single Claude Code session, before any design work began.

## 1. Purpose and status

This document records what is known so that the design session does not have to rediscover it. It is **not a spec**. It states facts, measured results, and the questions that remain open. Design decisions belong in `docs/superpowers/specs/`.

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
