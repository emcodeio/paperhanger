# paperhanger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build paperhanger: a macOS CLI that turns a folder of images into desktop and phone wallpapers by measuring, classifying, cropping into thirds, ML-upscaling only when needed, resizing to exact targets, and exporting HEIC/JPEG/AVIF/PNG.

**Architecture:** Plan-then-execute. Pure functions over integers decide everything (classification, bands, crop geometry, output dimensions, destination paths); an immutable flat list of `OutputPlan`s grouped by source photo records those decisions; a separate executor runs them via `sips` and `upscayl-bin` subprocesses. `--dry-run` renders the same structure the executor consumes, so the report cannot drift from the work.

**Tech Stack:** Python 3.14 under `uv`, no third-party runtime dependencies. `sips` (ships with macOS) for measure/crop/resize/encode. `upscayl-bin` (Real-ESRGAN via ncnn/Vulkan) for 4x upscaling. `pytest` for tests.

**Spec:** `docs/superpowers/specs/2026-09-11-paperhanger-design.md`

## Global Constraints

Binding on every task. Reviewers check these.

1. **No image is ever enlarged except by the ML model.** `sips` only ever reduces. There is no non-AI upsampling path anywhere in the codebase.
2. **No image is enlarged by less than 1.5x.** This is not a separate constant — it *is* the floor. `ideal / floor == 1.5` exactly on all four device-axis pairs, and a test asserts it.
3. **Never fuse a crop with a resample in one `sips` call.** `sips -c H W --cropOffset Y X --resampleWidth N` applies the resample against the *pre-crop* dimension and silently returns the wrong size. Crop is always its own invocation.
4. **Always pass both output axes** via `--resampleHeightWidth <H> <W>`. Never `--resampleWidth` or `--resampleHeight` alone: they derive the other axis, the derived value is neither consistently rounded nor floored, and a single hardcoded flag is wrong for the by-height plans. The plan *defines* the output size; `sips` is told both numbers.
5. **Always normalize to sRGB PNG before the upscaler**, for every source regardless of format: `sips --matchTo '/System/Library/ColorSync/Profiles/sRGB Profile.icc' -s format png`. `upscayl-bin` emits PNG with no ICC chunk, so skipping this tags the output sRGB over unconverted numbers.
6. **Non-image detection parses stdout; exit status is never the signal.** `sips -g pixelWidth` exits 0 while printing `pixelWidth: <nil>` for text, empty and truncated files.
7. **The archive move never overwrites.** Suffix instead (`IMG_0042-2.jpg`) and report it. This is the only path that could destroy a user's sole copy of an original.
8. **`~/Pictures/wallpaper` is read-only.** Every test that uses it copies files out and passes `--processing-dir`. Nothing writes to it; nothing assumes the originals are still there afterward.
9. **No wallpaper images in the repo.** Fixtures are generated at test time, or referenced by filename in `tests/corpus_sample.txt` and copied from the corpus at run time (skipping cleanly when absent).
10. **Exact target values:** desktop-by-width 7680/5120, desktop-by-height 4800/3200, phone-by-height 4320/2880, phone-by-width 2880/1920.
11. **Exact output name format:** `<stem>[_<position>]_<device>_<W>x<H>_<factor>.<ext>` where `<factor>` is `native` or e.g. `1.6x` / `4x`.
12. **Exact quality defaults:** heic 80, jpeg 90, avif 85, png lossless. `--quality` with png is an error.

**User decisions (already made):**

- Python package with a test suite under `uv` — not a single script, not zsh.
- `paperhanger setup` downloads and SHA-256-verifies the binary and model into `~/.local/share/paperhanger/`.
- The below-ideal output folder is named `below_target/` (not `4x_upscaled/`).
- Phone ideal is 4320x2880 with floors 2880x1920, so `ideal/floor` is exactly 1.5 on all four axes.
- Desktop and phone passes are independent; neither can abort the other.
- Output filenames carry both output dimensions and the net enlargement factor.
- Output formats are heic (default), jpeg, avif, png. Quality defaults are fixed from measurement, not calibrated during implementation.
- Plan-then-execute architecture, with a flat list of plans grouped by source photo rather than a tree.
- One upscale per source photo rather than per output plan — gated on the equivalence test in Task 14.
- "Implemented" means Tiers 1-3 pass. The full 894-image run (Tier 4) is the author's acceptance pass and is explicitly **not** a gate.

---

## File Structure

```
pyproject.toml                     package metadata, pytest config, uv entry point
paperhanger/
  __init__.py                      version only
  sizes.py                         Target dataclass, the four targets, UPSCALE_FACTOR
  classify.py                      (w, h, device) -> Target | CROP
  geometry.py                      Rect, horizontal_thirds, vertical_thirds, positions
  plan.py                          bands, output_size, net factor, OutputPlan, PhotoWork,
                                   the planner, naming, destinations, collision detection
  imaging.py                       every sips and upscayl-bin subprocess call
  toolchain.py                     pinned URLs and hashes, find/verify/download
  execute.py                       per-photo upscale, per-plan render, staging, archival
  report.py                        dry-run rendering and the time estimate
  cli.py                           argument parsing, dispatch, the nesting guard
tests/
  conftest.py                      tmp processing dir, fake upscaler, corpus helpers
  pngwriter.py                     stdlib PNG writer for exact-dimension fixtures
  corpus_sample.txt                27 filenames (already committed)
  test_pngwriter.py                the fixture generator is itself tested
  test_sizes.py                    the 1.5 invariant and target values
  test_classify.py                 every ratio boundary, both threshold corrections
  test_bands.py                    every band boundary, output dimensions, factors
  test_geometry.py                 slice fit, ceiling behaviour, self-classification
  test_plan.py                     naming, destinations, grouping, collisions
  test_imaging.py                  probe rules, crop offsets, resize exactness
  test_toolchain.py                resolution order, hash failure handling
  test_execute.py                  render, staging, archival, outcomes (fake upscaler)
  test_report.py                   dry-run formatting
  test_cli.py                      flags, dispatch, guard
  test_corpus_sample.py            Tier 2: the 27 images, fake upscaler
  test_real_upscaler.py            Tier 3: real binary, marked, incl. the equivalence gate
```

Boundaries: `sizes`, `classify`, `geometry` and `plan` never import `imaging`, `toolchain`, `execute` or `os`. A test asserts that, because it is the property the whole architecture rests on.

---

### Task 0: Project scaffold and the PNG fixture writer

**Goal:** A `uv`-runnable package skeleton plus a stdlib PNG writer that produces an image at any exact pixel dimension, because every boundary test needs images one pixel apart.

**Files:**
- Create: `pyproject.toml`
- Create: `paperhanger/__init__.py`
- Create: `tests/pngwriter.py`
- Create: `tests/conftest.py`
- Test: `tests/test_pngwriter.py`

**Acceptance Criteria:**
- [ ] `uv run pytest` runs and passes
- [ ] `write_png(path, 1279, 800)` produces a file `sips` reports as exactly 1279x800
- [ ] `write_png` accepts an RGB fill colour and a `noise` flag (deterministic pseudo-random pixels, needed later so encoders have real detail to compress)
- [ ] No third-party runtime dependencies in `pyproject.toml`; `pytest` is a dev dependency only

**Verify:** `uv run pytest tests/test_pngwriter.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "paperhanger"
version = "0.1.0"
description = "Turn a folder of images into desktop and phone wallpapers on macOS"
requires-python = ">=3.14"
dependencies = []

[project.scripts]
paperhanger = "paperhanger.cli:main"

[dependency-groups]
dev = ["pytest>=8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "real_upscaler: requires upscayl-bin installed (Tier 3); deselected by default",
    "corpus: requires ~/Pictures/wallpaper (Tier 2); skips cleanly when absent",
]
addopts = "-m 'not real_upscaler'"
```

- [ ] **Step 2: Write `paperhanger/__init__.py`**

```python
"""paperhanger: turn a folder of images into desktop and phone wallpapers."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Write the failing test for the PNG writer**

```python
# tests/test_pngwriter.py
import subprocess

from tests.pngwriter import write_png


def _sips_dimensions(path):
    proc = subprocess.run(
        ["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
        capture_output=True, text=True, check=True,
    )
    values = {}
    for line in proc.stdout.splitlines():
        if ":" in line:
            key, _, value = line.strip().partition(":")
            values[key.strip()] = value.strip()
    return int(values["pixelWidth"]), int(values["pixelHeight"])


def test_writes_exact_dimensions(tmp_path):
    path = tmp_path / "odd.png"
    write_png(path, 1279, 800)
    assert _sips_dimensions(path) == (1279, 800)


def test_writes_one_pixel_wider(tmp_path):
    path = tmp_path / "even.png"
    write_png(path, 1280, 800)
    assert _sips_dimensions(path) == (1280, 800)


def test_tiny_image(tmp_path):
    path = tmp_path / "tiny.png"
    write_png(path, 1, 1)
    assert _sips_dimensions(path) == (1, 1)


def test_noise_is_deterministic(tmp_path):
    first, second = tmp_path / "a.png", tmp_path / "b.png"
    write_png(first, 64, 64, noise=True)
    write_png(second, 64, 64, noise=True)
    assert first.read_bytes() == second.read_bytes()


def test_noise_differs_from_flat(tmp_path):
    flat, noisy = tmp_path / "flat.png", tmp_path / "noisy.png"
    write_png(flat, 64, 64)
    write_png(noisy, 64, 64, noise=True)
    assert flat.read_bytes() != noisy.read_bytes()
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `uv run pytest tests/test_pngwriter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.pngwriter'`

- [ ] **Step 5: Write `tests/pngwriter.py`**

```python
"""Minimal PNG writer, stdlib only.

Exists so boundary tests can use images that differ by one pixel. Committing a
file per boundary would be absurd; generating them costs nothing.
"""

import struct
import zlib
from pathlib import Path


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def write_png(path, width: int, height: int, colour=(120, 140, 110), noise=False) -> Path:
    """Write an 8-bit RGB PNG of exactly width x height pixels."""
    if width < 1 or height < 1:
        raise ValueError("width and height must be >= 1")

    red, green, blue = colour
    rows = bytearray()
    seed = 0x9E3779B9
    for y in range(height):
        rows.append(0)  # filter type 0 (None) for this scanline
        if not noise:
            rows.extend(bytes((red, green, blue)) * width)
            continue
        for x in range(width):
            # xorshift-ish mix: deterministic, and varied enough that an
            # encoder has real high-frequency detail to work on.
            seed ^= (x * 0x85EBCA6B + y * 0xC2B2AE35) & 0xFFFFFFFF
            seed = (seed * 0x27D4EB2F + 0x165667B1) & 0xFFFFFFFF
            rows.append((red + (seed >> 8)) & 0xFF)
            rows.append((green + (seed >> 16)) & 0xFF)
            rows.append((blue + (seed >> 24)) & 0xFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + _chunk(b"IEND", b"")
    )
    path = Path(path)
    path.write_bytes(data)
    return path
```

- [ ] **Step 6: Write `tests/conftest.py`**

```python
import sys
from pathlib import Path

import pytest

# Tests import `tests.pngwriter`; make the repo root importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CORPUS = Path.home() / "Pictures" / "wallpaper"


@pytest.fixture
def processing_dir(tmp_path):
    """A throwaway processing tree. Never the real one."""
    return tmp_path / "processing"


@pytest.fixture
def corpus():
    """The read-only image corpus, or skip. Never written to."""
    if not CORPUS.is_dir():
        pytest.skip(f"corpus not present at {CORPUS}")
    return CORPUS
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pngwriter.py -v`
Expected: 5 passed

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml paperhanger/ tests/
git commit -m "feat: scaffold package and stdlib PNG fixture writer"
```

---

### Task 1: Targets and classification

**Goal:** `sizes.py` holding the four targets, and `classify.py` turning `(width, height, device)` into a target or a crop instruction using integer comparisons only.

**Files:**
- Create: `paperhanger/sizes.py`
- Create: `paperhanger/classify.py`
- Test: `tests/test_sizes.py`
- Test: `tests/test_classify.py`

**Acceptance Criteria:**
- [ ] `ideal / floor == 1.5` for all four targets, asserted by a test
- [ ] Both phone frames are exactly 2:3 (2880x4320 ideal, 1920x2880 floor), asserted by a test
- [ ] A 2000x3000 image classifies as `PHONE_BY_HEIGHT` (the legacy script sent it down the width path; §4.1)
- [ ] A 1605x1000 image classifies as `DESKTOP_BY_HEIGHT` (the legacy script sent it down the width path; §4.1)
- [ ] A 1600x1000 image classifies as `DESKTOP_BY_WIDTH` (exactly on the 1.6 boundary)
- [ ] No float arithmetic anywhere in the classification path

**Verify:** `uv run pytest tests/test_sizes.py tests/test_classify.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write `paperhanger/sizes.py`**

```python
"""Target sizes and the constants the decision tree runs on. No I/O, no floats."""

from dataclasses import dataclass

DESKTOP = "desktop"
PHONE = "phone"
WIDTH = "width"
HEIGHT = "height"

UPSCALE_FACTOR = 4

# The one enlargement the upscaler is allowed to skip: anything already at or
# above its floor keeps its real pixels. ideal/floor IS that ratio, so there is
# no separate constant -- see test_ratio_is_exactly_one_and_a_half.
MIN_ENLARGEMENT = 1.5


@dataclass(frozen=True)
class Target:
    device: str
    axis: str
    ideal: int
    floor: int

    @property
    def name(self) -> str:
        return f"{self.device}/{self.axis}"


DESKTOP_BY_WIDTH = Target(DESKTOP, WIDTH, 7680, 5120)
DESKTOP_BY_HEIGHT = Target(DESKTOP, HEIGHT, 4800, 3200)
PHONE_BY_HEIGHT = Target(PHONE, HEIGHT, 4320, 2880)
PHONE_BY_WIDTH = Target(PHONE, WIDTH, 2880, 1920)

ALL_TARGETS = (DESKTOP_BY_WIDTH, DESKTOP_BY_HEIGHT, PHONE_BY_HEIGHT, PHONE_BY_WIDTH)
DEVICES = (DESKTOP, PHONE)


def governing_dimension(width: int, height: int, target: Target) -> int:
    """The dimension the target sizes by."""
    return width if target.axis == WIDTH else height
```

- [ ] **Step 2: Write `tests/test_sizes.py`**

```python
from paperhanger import sizes


def test_ratio_is_exactly_one_and_a_half():
    """Global constraint 2. The 1.5x cutoff is the floor; this is what makes
    'needs less than 1.5x' and 'is at or above the floor' one condition."""
    for target in sizes.ALL_TARGETS:
        assert target.ideal / target.floor == 1.5, target.name


def test_target_values():
    assert (sizes.DESKTOP_BY_WIDTH.ideal, sizes.DESKTOP_BY_WIDTH.floor) == (7680, 5120)
    assert (sizes.DESKTOP_BY_HEIGHT.ideal, sizes.DESKTOP_BY_HEIGHT.floor) == (4800, 3200)
    assert (sizes.PHONE_BY_HEIGHT.ideal, sizes.PHONE_BY_HEIGHT.floor) == (4320, 2880)
    assert (sizes.PHONE_BY_WIDTH.ideal, sizes.PHONE_BY_WIDTH.floor) == (2880, 1920)


def test_phone_frames_are_two_to_three():
    """Both phone axes describe the same frame: 2880x4320 ideal, 1920x2880 floor."""
    assert sizes.PHONE_BY_WIDTH.ideal * 3 == sizes.PHONE_BY_HEIGHT.ideal * 2
    assert sizes.PHONE_BY_WIDTH.floor * 3 == sizes.PHONE_BY_HEIGHT.floor * 2


def test_governing_dimension_follows_axis():
    assert sizes.governing_dimension(4000, 3000, sizes.DESKTOP_BY_WIDTH) == 4000
    assert sizes.governing_dimension(4000, 3000, sizes.DESKTOP_BY_HEIGHT) == 3000
```

- [ ] **Step 3: Write the failing test for classification**

```python
# tests/test_classify.py
import pytest

from paperhanger import sizes
from paperhanger.classify import CROP, classify

D, P = sizes.DESKTOP, sizes.PHONE


@pytest.mark.parametrize("width,height,device,expected", [
    # --- desktop ---
    (1600, 1000, D, sizes.DESKTOP_BY_WIDTH),    # exactly 1.60
    (1599, 1000, D, sizes.DESKTOP_BY_WIDTH),
    (1605, 1000, D, sizes.DESKTOP_BY_HEIGHT),   # legacy said by_width; see 4.1
    (1920, 1080, D, sizes.DESKTOP_BY_HEIGHT),   # 16:9
    (1001, 1000, D, sizes.DESKTOP_BY_WIDTH),    # just wider than square
    (1000, 1000, D, CROP),                      # square crops
    (1000, 1001, D, CROP),
    (2048, 4096, D, CROP),
    # --- phone ---
    (2000, 3000, P, sizes.PHONE_BY_HEIGHT),     # exactly 2:3; legacy said by_width
    (1999, 3000, P, sizes.PHONE_BY_WIDTH),      # a hair thinner than 2:3
    (1206, 2622, P, sizes.PHONE_BY_WIDTH),      # an iPhone screenshot
    (1000, 1001, P, sizes.PHONE_BY_HEIGHT),
    (1000, 1000, P, CROP),                      # square crops
    (4000, 3000, P, CROP),
])
def test_classification(width, height, device, expected):
    assert classify(width, height, device) is expected


def test_unknown_device():
    with pytest.raises(ValueError, match="unknown device"):
        classify(100, 100, "tablet")


def test_pure_module_imports_nothing_with_side_effects():
    """Global architecture property: the decision layer never touches the world."""
    import paperhanger.classify as module

    source = (module.__file__ or "").replace("classify.py", "")
    text = (open(module.__file__).read())
    for forbidden in ("import os", "import subprocess", "import shutil", "from pathlib"):
        assert forbidden not in text, f"{forbidden} in classify.py"
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `uv run pytest tests/test_classify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paperhanger.classify'`

- [ ] **Step 5: Write `paperhanger/classify.py`**

```python
"""Which device and axis an image belongs to. Integer comparisons only.

The thresholds here are NOT where the legacy script's were. `get_aspect_ratio`
truncated to two decimals with `bc scale=2` and the result was compared against
bc's full-precision 2/3, so `0.66 >= 0.6666...` was false -- the real phone
boundary in the shipped script is 0.67, not 0.66, and the desktop boundary is
1.61, not 1.60. The comparisons below put both where legacy/notes.org always
said they were. See spec section 4.1; test_classify.py names the two cases.
"""

from . import sizes

CROP = "crop"


def classify(width: int, height: int, device: str):
    """Return a sizes.Target, or CROP if the image should be cut into thirds."""
    if device == sizes.DESKTOP:
        if width <= height:
            return CROP
        if width * 10 <= height * 16:
            return sizes.DESKTOP_BY_WIDTH
        return sizes.DESKTOP_BY_HEIGHT

    if device == sizes.PHONE:
        if width >= height:
            return CROP
        if width * 3 >= height * 2:
            return sizes.PHONE_BY_HEIGHT
        return sizes.PHONE_BY_WIDTH

    raise ValueError(f"unknown device: {device!r}")
```

- [ ] **Step 6: Run both test files to verify they pass**

Run: `uv run pytest tests/test_sizes.py tests/test_classify.py -v`
Expected: all pass (16 classification cases plus the invariants)

- [ ] **Step 7: Commit**

```bash
git add paperhanger/sizes.py paperhanger/classify.py tests/test_sizes.py tests/test_classify.py
git commit -m "feat: targets and integer-comparison classification"
```

---

### Task 2: Bands, output dimensions, and the enlargement factor

**Goal:** The five-band decision, both output axes computed in advance, and the net enlargement factor that goes in the filename.

**Files:**
- Create: `paperhanger/bands.py`
- Test: `tests/test_bands.py`

**Acceptance Criteria:**
- [ ] The five band conditions are **disjoint**: a test enumerates governing dimensions across the whole range for all four targets and asserts exactly one band matches each
- [ ] `d*4 == ideal` lands in band 3 (upscale-then-reduce), not band 4
- [ ] `d == floor` lands in band 2, `d == ideal` lands in band 1
- [ ] `output_size` returns both axes for every band; band 2 returns the source size unchanged
- [ ] `factor_token` renders `native`, `4x`, `1.6x` — never `4.0x`
- [ ] No band ever produces an output larger than `ideal` on the governing axis

**Verify:** `uv run pytest tests/test_bands.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bands.py
import pytest

from paperhanger import bands, sizes

T = sizes.DESKTOP_BY_WIDTH  # ideal 7680, floor 5120


@pytest.mark.parametrize("governing,expected", [
    (8000, bands.DOWNSCALE),        # above ideal
    (7680, bands.DOWNSCALE),        # exactly ideal
    (7679, bands.NATIVE),
    (5120, bands.NATIVE),           # exactly floor
    (5119, bands.UPSCALE_REDUCE),
    (1920, bands.UPSCALE_REDUCE),   # d*4 == ideal exactly -> band 3, not 4
    (1919, bands.UPSCALE_ONLY),
    (1280, bands.UPSCALE_ONLY),     # d*4 == floor exactly
    (1279, bands.REJECT),
    (1, bands.REJECT),
])
def test_desktop_width_boundaries(governing, expected):
    assert bands.band_for(governing, T) == expected


def test_bands_are_disjoint_and_total():
    """Global constraint: each row of the band table stands alone, so no
    evaluation order is implied and each is testable in isolation."""
    for target in sizes.ALL_TARGETS:
        for d in range(1, target.ideal + 200):
            matches = [
                d >= target.ideal,
                target.floor <= d < target.ideal,
                d < target.floor and d * 4 >= target.ideal,
                d < target.floor and target.floor <= d * 4 < target.ideal,
                d * 4 < target.floor,
            ]
            assert sum(matches) == 1, f"{target.name} d={d} matched {sum(matches)}"


def test_output_size_downscale_by_width():
    assert bands.output_size(9216, 6144, T, bands.DOWNSCALE) == (7680, 5120)


def test_output_size_downscale_by_height():
    target = sizes.DESKTOP_BY_HEIGHT
    assert bands.output_size(3840, 2160, target, bands.UPSCALE_REDUCE) == (8533, 4800)


def test_output_size_native_is_untouched():
    assert bands.output_size(6000, 3750, T, bands.NATIVE) == (6000, 3750)


def test_output_size_upscale_only_is_exactly_four_times():
    assert bands.output_size(1600, 1200, T, bands.UPSCALE_ONLY) == (6400, 4800)


def test_output_never_exceeds_ideal_on_the_governing_axis():
    for target in sizes.ALL_TARGETS:
        for d in (target.ideal + 500, target.ideal, target.floor, target.floor - 1,
                  target.ideal // 4, target.floor // 4):
            if d < 1:
                continue
            b = bands.band_for(d, target)
            if b == bands.REJECT:
                continue
            w, h = (d, d * 2) if target.axis == sizes.WIDTH else (d * 2, d)
            out_w, out_h = bands.output_size(w, h, target, b)
            out_governing = out_w if target.axis == sizes.WIDTH else out_h
            assert out_governing <= target.ideal, f"{target.name} d={d} band={b}"


@pytest.mark.parametrize("governing,band,expected", [
    (9216, bands.DOWNSCALE, "native"),
    (6000, bands.NATIVE, "native"),
    (1600, bands.UPSCALE_ONLY, "4x"),
    (4912, bands.UPSCALE_REDUCE, "1.6x"),   # 7680/4912 = 1.563
    (3840, bands.UPSCALE_REDUCE, "2x"),     # 7680/3840 = 2.0 -> "2x", not "2.0x"
    (1920, bands.UPSCALE_REDUCE, "4x"),     # 7680/1920 = 4.0
])
def test_factor_token(governing, band, expected):
    assert bands.factor_token(bands.net_factor(governing, T, band)) == expected
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_bands.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paperhanger.bands'`

- [ ] **Step 3: Write `paperhanger/bands.py`**

```python
"""The five bands, and the output dimensions each one produces.

Two rules shape this module, both from spec section 5:

  * No image is ever enlarged except by the ML model. Nothing here upsamples.
  * No image is enlarged by less than 1.5x -- which is the floor, because
    ideal/floor is exactly 1.5 on all four targets. Band NATIVE is that case,
    and it resamples not at all.

Both output axes are computed here rather than derived by sips, because
--resampleWidth/--resampleHeight round the derived axis inconsistently and the
filename records the size. The plan defines the file; sips is told both numbers.
"""

from . import sizes

DOWNSCALE = 1        # d >= ideal            -> reduce to ideal
NATIVE = 2           # floor <= d < ideal    -> encode only, untouched
UPSCALE_REDUCE = 3   # d < floor, d*4 >= ideal -> 4x then reduce to ideal
UPSCALE_ONLY = 4     # d < floor, floor <= d*4 < ideal -> 4x, no resize
REJECT = 5           # d*4 < floor           -> too small for this device

UPSCALING = (UPSCALE_REDUCE, UPSCALE_ONLY)
BELOW_TARGET = (NATIVE, UPSCALE_ONLY)
RESIZING = (DOWNSCALE, UPSCALE_REDUCE)

NAMES = {
    DOWNSCALE: "downscale",
    NATIVE: "native",
    UPSCALE_REDUCE: "upscale+reduce",
    UPSCALE_ONLY: "upscale",
    REJECT: "reject",
}


def band_for(governing: int, target: sizes.Target) -> int:
    """Which band a governing dimension falls in. Conditions are disjoint."""
    if governing >= target.ideal:
        return DOWNSCALE
    if governing >= target.floor:
        return NATIVE
    quadrupled = governing * sizes.UPSCALE_FACTOR
    if quadrupled >= target.ideal:
        return UPSCALE_REDUCE
    if quadrupled >= target.floor:
        return UPSCALE_ONLY
    return REJECT


def output_size(width: int, height: int, target: sizes.Target, band: int):
    """Both output axes, exactly as the file will be written."""
    if band == NATIVE:
        return (width, height)
    if band == UPSCALE_ONLY:
        factor = sizes.UPSCALE_FACTOR
        return (width * factor, height * factor)
    if band in RESIZING:
        if target.axis == sizes.WIDTH:
            return (target.ideal, round(height * target.ideal / width))
        return (round(width * target.ideal / height), target.ideal)
    raise ValueError(f"no output size for band {band}")


def net_factor(governing: int, target: sizes.Target, band: int):
    """How much enlargement survives into the finished file, or None.

    Net rather than the model's own 4x: a photo enlarged 4x and then reduced
    carries less invented detail into the result than one left at 4x.
    """
    if band in (DOWNSCALE, NATIVE):
        return None
    if band == UPSCALE_ONLY:
        return float(sizes.UPSCALE_FACTOR)
    return target.ideal / governing


def factor_token(factor) -> str:
    """The filename token: 'native', '4x', '1.6x'. Never '4.0x'."""
    if factor is None:
        return "native"
    text = f"{factor:.1f}".rstrip("0").rstrip(".")
    return f"{text}x"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_bands.py -v`
Expected: all pass. The disjointness test iterates ~18,000 dimensions across four targets; it should still finish in under a second.

- [ ] **Step 5: Commit**

```bash
git add paperhanger/bands.py tests/test_bands.py
git commit -m "feat: five-band decision with both output axes precomputed"
```

---

### Task 3: Crop geometry

**Goal:** The three overlapping slice rectangles for each crop path, with ceiling arithmetic so a slice satisfies the class the planner assigns it.

**Files:**
- Create: `paperhanger/geometry.py`
- Test: `tests/test_geometry.py`

**Acceptance Criteria:**
- [ ] `horizontal_thirds` returns three `w x ceil(w*10/16)` rects at y = 0, centred, bottom-aligned
- [ ] `vertical_thirds` returns three `ceil(h*2/3) x h` rects at x = 0, centred, right-aligned
- [ ] Every slice lies entirely within the source for all valid inputs (no negative offsets, no overrun)
- [ ] **Every horizontal slice classifies as `DESKTOP_BY_WIDTH`** and every vertical slice as `PHONE_BY_HEIGHT`, for every width/height in a swept range — this is what the ceiling is for, and it is why the planner can assign the class by construction
- [ ] A test documents that the slices overlap for near-square sources, with the ratio at which top and bottom become near-duplicates

**Verify:** `uv run pytest tests/test_geometry.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing test**

```python
# tests/test_geometry.py
import pytest

from paperhanger import geometry, sizes
from paperhanger.classify import classify


def test_horizontal_slice_dimensions():
    rects = geometry.horizontal_thirds(1600, 2400)
    assert [(r.width, r.height) for r in rects] == [(1600, 1000)] * 3


def test_horizontal_slice_offsets():
    rects = geometry.horizontal_thirds(1600, 2400)
    assert [r.y for r in rects] == [0, 700, 1400]   # 0, (2400-1000)//2, 2400-1000
    assert all(r.x == 0 for r in rects)


def test_vertical_slice_dimensions():
    rects = geometry.vertical_thirds(3000, 2000)
    assert [(r.width, r.height) for r in rects] == [(1334, 2000)] * 3  # ceil(4000/3)


def test_vertical_slice_offsets():
    rects = geometry.vertical_thirds(3000, 2000)
    assert [r.x for r in rects] == [0, 833, 1666]   # 0, (3000-1334)//2, 3000-1334
    assert all(r.y == 0 for r in rects)


def test_ceiling_not_truncation():
    """1601*10/16 = 1000.625. Truncation gives 1000, which makes the slice
    1.601:1 and it fails the desktop-by-width test. Ceiling gives 1001."""
    rects = geometry.horizontal_thirds(1601, 3000)
    assert rects[0].height == 1001


@pytest.mark.parametrize("width", range(1000, 1064))
def test_every_horizontal_slice_self_classifies(width):
    """The planner assigns DESKTOP_BY_WIDTH by construction. This is the test
    that makes that safe for every residue of width % 16."""
    height = width * 3
    for rect in geometry.horizontal_thirds(width, height):
        assert classify(rect.width, rect.height, sizes.DESKTOP) is sizes.DESKTOP_BY_WIDTH


@pytest.mark.parametrize("height", range(1000, 1064))
def test_every_vertical_slice_self_classifies(height):
    width = height * 3
    for rect in geometry.vertical_thirds(width, height):
        assert classify(rect.width, rect.height, sizes.PHONE) is sizes.PHONE_BY_HEIGHT


@pytest.mark.parametrize("width,height", [
    (1000, 1000), (1000, 1001), (1000, 5000), (1601, 1602), (4000, 4001),
])
def test_horizontal_slices_stay_inside_the_source(width, height):
    for rect in geometry.horizontal_thirds(width, height):
        assert rect.x >= 0 and rect.y >= 0
        assert rect.x + rect.width <= width
        assert rect.y + rect.height <= height


@pytest.mark.parametrize("width,height", [
    (1000, 1000), (1001, 1000), (5000, 1000), (1602, 1601), (4001, 4000),
])
def test_vertical_slices_stay_inside_the_source(width, height):
    for rect in geometry.vertical_thirds(width, height):
        assert rect.x >= 0 and rect.y >= 0
        assert rect.x + rect.width <= width
        assert rect.y + rect.height <= height


def test_slices_overlap_for_near_square_sources():
    """Documented, not a defect: these are three crops, not three disjoint
    thirds. At 1:1 the slice is 0.625h tall in an h-tall frame, so top and
    bottom overlap by 25% of the frame and share 40% of their own pixels."""
    rects = geometry.horizontal_thirds(1000, 1000)
    top, _, bottom = rects
    overlap = (top.y + top.height) - bottom.y
    assert overlap == 250
    assert overlap / top.height == pytest.approx(0.4)


def test_slices_are_disjoint_for_tall_sources():
    """At 3:1 or taller there is no overlap; the crops tile the frame."""
    top, _, bottom = geometry.horizontal_thirds(1000, 3000)
    assert top.y + top.height <= bottom.y


def test_scaled_rect_multiplies_every_field():
    """Needed by the executor: a slice cut from a 4x frame sits at 4x offsets."""
    rect = geometry.Rect(x=100, y=200, width=300, height=400)
    assert rect.scaled(4) == geometry.Rect(x=400, y=800, width=1200, height=1600)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_geometry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paperhanger.geometry'`

- [ ] **Step 3: Write `paperhanger/geometry.py`**

```python
"""Crop rectangles for the two crop paths. Pure integer arithmetic.

Preserved from the legacy script, including that the three crops OVERLAP for
anything near square. They are three crops, not three disjoint thirds.

The slice dimension rounds UP. With truncation a 16:10 slice computes to a ratio
slightly above 1.6 and fails its own classification test; rounding up puts it at
or below 1.6 for every residue, so the slice satisfies the class the planner
assigns it and never needs re-deriving. Same for the 2:3 vertical slice.
"""

from dataclasses import dataclass

HORIZONTAL_POSITIONS = ("top", "middle", "bottom")
VERTICAL_POSITIONS = ("left", "center", "right")


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def scaled(self, factor: int) -> "Rect":
        """The same rectangle in an image enlarged by `factor`."""
        return Rect(
            x=self.x * factor,
            y=self.y * factor,
            width=self.width * factor,
            height=self.height * factor,
        )


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def horizontal_thirds(width: int, height: int):
    """Three 16:10 slices: top, middle, bottom. For the desktop crop path."""
    slice_height = _ceil_div(width * 10, 16)
    if slice_height > height:
        raise ValueError(f"{width}x{height} is too wide to slice horizontally")
    return (
        Rect(0, 0, width, slice_height),
        Rect(0, (height - slice_height) // 2, width, slice_height),
        Rect(0, height - slice_height, width, slice_height),
    )


def vertical_thirds(width: int, height: int):
    """Three 2:3 slices: left, center, right. For the phone crop path."""
    slice_width = _ceil_div(height * 2, 3)
    if slice_width > width:
        raise ValueError(f"{width}x{height} is too tall to slice vertically")
    return (
        Rect(0, 0, slice_width, height),
        Rect((width - slice_width) // 2, 0, slice_width, height),
        Rect(width - slice_width, 0, slice_width, height),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_geometry.py -v`
Expected: all pass, including the two 64-case self-classification sweeps

- [ ] **Step 5: Commit**

```bash
git add paperhanger/geometry.py tests/test_geometry.py
git commit -m "feat: crop geometry with ceiling slices that self-classify"
```

---

### Task 4: Output formats and the planner

**Goal:** `formats.py` (extensions and quality defaults) and `plan.py` (the `OutputPlan`, the `PhotoWork` grouping, naming, destinations, and batch-wide collision detection). This is the last pure task; after it, every decision the tool makes is data.

**Files:**
- Create: `paperhanger/formats.py`
- Create: `paperhanger/plan.py`
- Test: `tests/test_plan.py`

**Acceptance Criteria:**
- [ ] `plan_photo` returns a `PhotoWork` whose `plans` is a **flat list** (no nesting) grouped by source photo
- [ ] A crop path produces exactly three plans, each carrying its position and its own rect, with the target assigned by construction (`DESKTOP_BY_WIDTH` for horizontal, `PHONE_BY_HEIGHT` for vertical) — never re-derived from the slice
- [ ] A rejected device contributes no plans and is listed in `rejected_devices`
- [ ] Output names match `<stem>[_<position>]_<device>_<W>x<H>_<factor>.<ext>` exactly
- [ ] Bands 2 and 4 route to `to_sort_<device>/below_target/`; bands 1 and 3 to `to_sort_<device>/`
- [ ] `--quality` with png raises; heic/jpeg/avif default to 80/90/85
- [ ] `find_collisions` detects two sources with the same stem and different extensions targeting one output path, and reports both sources
- [ ] `plan.py` imports nothing from `imaging`, `toolchain`, `execute`, `os`, `subprocess` or `shutil`

**Verify:** `uv run pytest tests/test_plan.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write `paperhanger/formats.py`**

```python
"""Output formats, their extensions, and their quality defaults.

Quality scales are not comparable across encoders, so each default is the knee
of its own curve, measured on a 7680x5120 high-frequency photograph:

  heic 80  -- 85 produces a BYTE-IDENTICAL file (Apple's encoder quantizes),
              and 90 nearly doubles the size for the next step up.
  jpeg 90  -- 85->90 costs 5% more size for better fidelity; 90->95 costs 10%
              more for less return.
  avif 85  -- lands in heic 80's quality neighbourhood at 19% smaller.
  png      -- lossless; there is no quality setting, and asking for one is an
              error rather than a silent no-op.
"""

EXTENSIONS = {"heic": "heic", "jpeg": "jpg", "avif": "avif", "png": "png"}
DEFAULT_QUALITY = {"heic": 80, "jpeg": 90, "avif": 85, "png": None}
FORMATS = tuple(EXTENSIONS)
LOSSLESS = ("png",)


def extension(fmt: str) -> str:
    try:
        return EXTENSIONS[fmt]
    except KeyError:
        raise ValueError(f"unknown format: {fmt!r}; expected one of {', '.join(FORMATS)}")


def quality_for(fmt: str, override=None):
    """The quality value to encode with, or None for a lossless format."""
    if fmt not in EXTENSIONS:
        raise ValueError(f"unknown format: {fmt!r}; expected one of {', '.join(FORMATS)}")
    if fmt in LOSSLESS:
        if override is not None:
            raise ValueError(f"{fmt} is lossless; --quality does not apply")
        return None
    if override is None:
        return DEFAULT_QUALITY[fmt]
    if not 0 <= override <= 100:
        raise ValueError(f"quality must be 0-100, got {override}")
    return override
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_plan.py
from pathlib import Path

import pytest

from paperhanger import bands, formats, plan, sizes


def settings(tmp_path, fmt="heic", quality=None):
    return plan.OutputSettings(
        processing_dir=tmp_path / "processing",
        fmt=fmt,
        quality=formats.quality_for(fmt, quality),
    )


# ---------- formats ----------

def test_quality_defaults():
    assert formats.quality_for("heic") == 80
    assert formats.quality_for("jpeg") == 90
    assert formats.quality_for("avif") == 85
    assert formats.quality_for("png") is None


def test_quality_with_png_is_an_error():
    with pytest.raises(ValueError, match="lossless"):
        formats.quality_for("png", 90)


def test_unknown_format():
    with pytest.raises(ValueError, match="unknown format"):
        formats.quality_for("tiff")


# ---------- whole-image plans ----------

def test_desktop_downscale_plan(tmp_path):
    work = plan.plan_photo(Path("/src/cliffs.jpg"), 9216, 6144, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    assert len(work.plans) == 1
    p = work.plans[0]
    assert p.band == bands.DOWNSCALE
    assert (p.out_width, p.out_height) == (7680, 5120)
    assert p.crop is None and p.position is None
    assert p.destination.name == "cliffs_desktop_7680x5120_native.heic"
    assert p.destination.parent == tmp_path / "processing" / "to_sort_desktop"
    assert not p.needs_upscale


def test_below_target_goes_to_the_subfolder(tmp_path):
    work = plan.plan_photo(Path("/src/lichen.jpg"), 6000, 3750, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    p = work.plans[0]
    assert p.band == bands.NATIVE
    assert (p.out_width, p.out_height) == (6000, 3750)
    assert p.destination.parent.name == "below_target"
    assert p.destination.name == "lichen_desktop_6000x3750_native.heic"


def test_upscale_reduce_plan_records_the_net_factor(tmp_path):
    work = plan.plan_photo(Path("/src/rowan.jpg"), 4912, 7360, "jpeg",
                           [sizes.PHONE], settings(tmp_path))
    p = work.plans[0]
    assert p.target is sizes.PHONE_BY_HEIGHT
    assert p.band == bands.NATIVE          # 7360 >= 4320 ideal -> wait, DOWNSCALE
    # 7360 > 4320 so this is a downscale; assert that instead
    assert p.band == bands.DOWNSCALE


def test_upscale_only_plan(tmp_path):
    work = plan.plan_photo(Path("/src/moth.png"), 1600, 1200, "png",
                           [sizes.DESKTOP], settings(tmp_path, "png"))
    p = work.plans[0]
    assert p.band == bands.UPSCALE_ONLY
    assert (p.out_width, p.out_height) == (6400, 4800)
    assert p.destination.name == "moth_desktop_6400x4800_4x.png"
    assert p.destination.parent.name == "below_target"
    assert p.needs_upscale and not p.needs_resize


# ---------- crop plans ----------

def test_desktop_crop_produces_three_plans_with_positions(tmp_path):
    work = plan.plan_photo(Path("/src/sunset.jpg"), 3000, 5000, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    assert len(work.plans) == 3
    assert [p.position for p in work.plans] == ["top", "middle", "bottom"]
    for p in work.plans:
        assert p.target is sizes.DESKTOP_BY_WIDTH   # assigned by construction
        assert p.governing == 3000                  # the slice width == source width
        assert p.crop is not None
    assert [p.destination.name for p in work.plans] == [
        "sunset_top_desktop_7680x4800_2.6x.heic",
        "sunset_middle_desktop_7680x4800_2.6x.heic",
        "sunset_bottom_desktop_7680x4800_2.6x.heic",
    ]


def test_phone_crop_produces_three_plans(tmp_path):
    work = plan.plan_photo(Path("/src/ocean.jpg"), 4000, 3000, "jpeg",
                           [sizes.PHONE], settings(tmp_path))
    assert [p.position for p in work.plans] == ["left", "center", "right"]
    for p in work.plans:
        assert p.target is sizes.PHONE_BY_HEIGHT
        assert p.governing == 3000                  # the slice height == source height


def test_both_devices_are_independent(tmp_path):
    """A 900x1400 image is too small for desktop but fine for phone. Global
    constraint: neither pass can abort the other."""
    work = plan.plan_photo(Path("/src/small.jpg"), 900, 1400, "jpeg",
                           list(sizes.DEVICES), settings(tmp_path))
    devices = {p.device for p in work.plans}
    assert devices == {sizes.PHONE}
    assert work.rejected_devices == [sizes.DESKTOP]


def test_rejected_on_every_device(tmp_path):
    work = plan.plan_photo(Path("/src/tiny.gif"), 200, 300, "gif",
                           list(sizes.DEVICES), settings(tmp_path))
    assert work.plans == []
    assert sorted(work.rejected_devices) == sorted(sizes.DEVICES)
    assert work.rejected_everywhere


def test_plans_are_flat(tmp_path):
    """No nesting. Spec section 3: a tree would only be justified by recursion,
    and slices are planned directly rather than re-classified."""
    work = plan.plan_photo(Path("/src/sunset.jpg"), 3000, 5000, "jpeg",
                           list(sizes.DEVICES), settings(tmp_path))
    assert isinstance(work.plans, list)
    for p in work.plans:
        assert not hasattr(p, "children")
    assert len(work.plans) == 6   # three desktop slices, three phone slices


def test_needs_upscale_is_a_photo_level_question(tmp_path):
    """The upscaler runs once per photo, so the question is asked of the photo."""
    work = plan.plan_photo(Path("/src/sunset.jpg"), 3000, 5000, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    assert work.needs_upscale is True
    big = plan.plan_photo(Path("/src/huge.jpg"), 9216, 6144, "jpeg",
                          [sizes.DESKTOP], settings(tmp_path))
    assert big.needs_upscale is False


# ---------- collisions ----------

def test_collision_between_same_stem_different_extension(tmp_path):
    """Live in the corpus: cityscape_reflection_4592.jpg and .png are both
    1920x1046 and collide on all four of their outputs."""
    opts = settings(tmp_path)
    works = [
        plan.plan_photo(Path("/src/city.jpg"), 1920, 1046, "jpeg", list(sizes.DEVICES), opts),
        plan.plan_photo(Path("/src/city.png"), 1920, 1046, "png", list(sizes.DEVICES), opts),
    ]
    collisions = plan.find_collisions(works)
    assert collisions
    for destination, sources in collisions.items():
        assert len(sources) == 2
        assert {s.suffix for s in sources} == {".jpg", ".png"}


def test_no_collision_between_different_stems(tmp_path):
    opts = settings(tmp_path)
    works = [
        plan.plan_photo(Path("/src/a.jpg"), 1920, 1046, "jpeg", list(sizes.DEVICES), opts),
        plan.plan_photo(Path("/src/b.jpg"), 1920, 1046, "jpeg", list(sizes.DEVICES), opts),
    ]
    assert plan.find_collisions(works) == {}


def test_plan_module_is_pure():
    import paperhanger.plan as module

    text = open(module.__file__).read()
    for forbidden in ("import subprocess", "import shutil", "from . import imaging",
                      "from . import execute", "from . import toolchain"):
        assert forbidden not in text, f"{forbidden} in plan.py"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_plan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paperhanger.plan'`

- [ ] **Step 4: Write `paperhanger/plan.py`**

```python
"""What to produce from one photo. Pure: no filesystem, no subprocesses.

The output is a FLAT list of OutputPlans grouped by source photo. Flat because
section 6 removed the recursion a tree would justify -- slices are planned
directly rather than re-classified, so nesting could never exceed one level.
Grouped by photo because two things are decided per photo rather than per
output: whether the upscaler runs, and where the original is archived.
"""

from dataclasses import dataclass, field
from pathlib import Path

from . import bands, formats, geometry, sizes
from .classify import CROP, classify


@dataclass(frozen=True)
class OutputSettings:
    processing_dir: Path
    fmt: str
    quality: int | None


@dataclass(frozen=True)
class OutputPlan:
    source: Path
    device: str
    target: sizes.Target
    band: int
    governing: int
    out_width: int
    out_height: int
    factor: float | None
    destination: Path
    fmt: str
    quality: int | None
    crop: geometry.Rect | None = None
    position: str | None = None

    @property
    def needs_upscale(self) -> bool:
        return self.band in bands.UPSCALING

    @property
    def needs_resize(self) -> bool:
        return self.band in bands.RESIZING

    @property
    def factor_token(self) -> str:
        return bands.factor_token(self.factor)


@dataclass
class PhotoWork:
    source: Path
    width: int
    height: int
    source_format: str
    plans: list[OutputPlan] = field(default_factory=list)
    rejected_devices: list[str] = field(default_factory=list)

    @property
    def needs_upscale(self) -> bool:
        return any(p.needs_upscale for p in self.plans)

    @property
    def rejected_everywhere(self) -> bool:
        return not self.plans and bool(self.rejected_devices)

    @property
    def upscale_output_pixels(self) -> int:
        """Pixels in the 4x whole frame -- what the executor's cap compares."""
        return self.width * self.height * sizes.UPSCALE_FACTOR ** 2


def output_name(source: Path, position, device: str, out_width: int,
                out_height: int, factor, fmt: str) -> str:
    stem = source.stem if position is None else f"{source.stem}_{position}"
    token = bands.factor_token(factor)
    return f"{stem}_{device}_{out_width}x{out_height}_{token}.{formats.extension(fmt)}"


def destination_dir(processing_dir: Path, device: str, band: int) -> Path:
    base = processing_dir / f"to_sort_{device}"
    return base / "below_target" if band in bands.BELOW_TARGET else base


def _make_plan(source, device, target, band, governing, width, height,
               opts, crop=None, position=None) -> OutputPlan:
    out_width, out_height = bands.output_size(width, height, target, band)
    factor = bands.net_factor(governing, target, band)
    name = output_name(source, position, device, out_width, out_height, factor, opts.fmt)
    return OutputPlan(
        source=source, device=device, target=target, band=band, governing=governing,
        out_width=out_width, out_height=out_height, factor=factor,
        destination=destination_dir(opts.processing_dir, device, band) / name,
        fmt=opts.fmt, quality=opts.quality, crop=crop, position=position,
    )


def plan_photo(source: Path, width: int, height: int, source_format: str,
               devices, opts: OutputSettings) -> PhotoWork:
    work = PhotoWork(source=source, width=width, height=height,
                     source_format=source_format)

    for device in devices:
        outcome = classify(width, height, device)

        if outcome is not CROP:
            band = bands.band_for(sizes.governing_dimension(width, height, outcome), outcome)
            if band == bands.REJECT:
                work.rejected_devices.append(device)
                continue
            work.plans.append(_make_plan(
                source, device, outcome, band,
                sizes.governing_dimension(width, height, outcome),
                width, height, opts,
            ))
            continue

        # Crop path. The target is assigned by CONSTRUCTION, not re-derived from
        # the slice: a horizontal slice is desktop-by-width because that is what
        # cutting a 16:10 slice means. test_geometry proves the slices satisfy it.
        if device == sizes.DESKTOP:
            rects = geometry.horizontal_thirds(width, height)
            positions = geometry.HORIZONTAL_POSITIONS
            target = sizes.DESKTOP_BY_WIDTH
        else:
            rects = geometry.vertical_thirds(width, height)
            positions = geometry.VERTICAL_POSITIONS
            target = sizes.PHONE_BY_HEIGHT

        rejected_here = False
        for rect, position in zip(rects, positions):
            governing = sizes.governing_dimension(rect.width, rect.height, target)
            band = bands.band_for(governing, target)
            if band == bands.REJECT:
                rejected_here = True
                continue
            work.plans.append(_make_plan(
                source, device, target, band, governing,
                rect.width, rect.height, opts, crop=rect, position=position,
            ))
        if rejected_here and not any(p.device == device for p in work.plans):
            work.rejected_devices.append(device)

    return work


def find_collisions(works) -> dict:
    """Output paths claimed by more than one source photo.

    Two sources sharing a stem across different extensions produce identical
    output names. Detected before any work is done, and fatal.
    """
    claims: dict[Path, list[Path]] = {}
    for work in works:
        for p in work.plans:
            claims.setdefault(p.destination, [])
            if work.source not in claims[p.destination]:
                claims[p.destination].append(work.source)
    return {dest: sources for dest, sources in claims.items() if len(sources) > 1}
```

- [ ] **Step 5: Fix the one deliberately-wrong assertion in the test**

The `test_upscale_reduce_plan_records_the_net_factor` case above asserts `NATIVE` and then corrects itself to `DOWNSCALE` — a 4912x7360 phone-by-height source has a governing dimension of 7360, which is above the 4320 ideal. Replace that test with a real upscale-reduce case:

```python
def test_upscale_reduce_plan_records_the_net_factor(tmp_path):
    """1920x1200 desktop-by-width: 1920 < 5120 floor, 1920*4 = 7680 >= ideal."""
    work = plan.plan_photo(Path("/src/wide.jpg"), 1920, 1200, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    p = work.plans[0]
    assert p.target is sizes.DESKTOP_BY_WIDTH
    assert p.band == bands.UPSCALE_REDUCE
    assert (p.out_width, p.out_height) == (7680, 4800)
    assert p.factor_token == "4x"           # 7680/1920 == 4.0
    assert p.needs_upscale and p.needs_resize
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/test_plan.py -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add paperhanger/formats.py paperhanger/plan.py tests/test_plan.py
git commit -m "feat: output formats and the pure planner"
```

---

### Task 5: Measuring and non-image detection

**Goal:** `imaging.probe()` — one `sips` call returning `(width, height, format)` or `None`, using rules that actually work. Exit status is not usable and this task exists because that is surprising.

**Files:**
- Create: `paperhanger/imaging.py`
- Test: `tests/test_imaging.py`

**Acceptance Criteria:**
- [ ] `probe` returns `(w, h, fmt)` for a real PNG, with `fmt == "png"`
- [ ] `probe` returns `None` for a text file, an empty file, a zero-byte `.jpg`, and a truncated JPEG — **all of which make `sips` exit 0** while printing `pixelWidth: <nil>`
- [ ] `probe` returns `None` for a file that kills `sips` with a signal (`.DS_Store` aborts with an uncaught `NSInvalidArgumentException`)
- [ ] `probe` reports the **actual** format, not the extension: a WebP named `.jpg` reports `webp`
- [ ] `probe` never raises for any input file; it returns `None` or a triple
- [ ] `probe` writes nothing and reads only the file named

**Verify:** `uv run pytest tests/test_imaging.py -v -k probe` → all pass

**Steps:**

- [ ] **Step 1: Write the failing test**

```python
# tests/test_imaging.py
import shutil
import subprocess
from pathlib import Path

import pytest

from paperhanger import imaging
from tests.pngwriter import write_png


# ---------- probe ----------

def test_probe_reads_a_png(tmp_path):
    path = write_png(tmp_path / "a.png", 640, 480)
    assert imaging.probe(path) == (640, 480, "png")


def test_probe_reads_odd_dimensions(tmp_path):
    path = write_png(tmp_path / "odd.png", 1279, 801)
    width, height, _ = imaging.probe(path)
    assert (width, height) == (1279, 801)


@pytest.mark.parametrize("name,content", [
    ("note.txt", b"this is not an image\n"),
    ("empty", b""),
    ("empty.jpg", b""),
    ("empty.png", b""),
])
def test_probe_rejects_non_images_that_exit_zero(tmp_path, name, content):
    """sips exits 0 for all of these, printing 'pixelWidth: <nil>'. Exit status
    is not the signal; parsing stdout is."""
    path = tmp_path / name
    path.write_bytes(content)
    assert imaging.probe(path) is None


def test_probe_rejects_a_truncated_jpeg(tmp_path):
    good = write_png(tmp_path / "good.png", 400, 300, noise=True)
    jpeg = tmp_path / "full.jpg"
    subprocess.run(["/usr/bin/sips", "-s", "format", "jpeg", str(good),
                    "--out", str(jpeg)], check=True, capture_output=True)
    truncated = tmp_path / "truncated.jpg"
    truncated.write_bytes(jpeg.read_bytes()[:2000])
    assert imaging.probe(truncated) is None


def test_probe_survives_a_file_that_aborts_sips(tmp_path, corpus):
    """.DS_Store makes sips die with an uncaught NSException (exit 134 in a
    shell, a negative returncode in Python). probe must not raise."""
    ds_store = corpus / ".DS_Store"
    if not ds_store.exists():
        pytest.skip("no .DS_Store in the corpus")
    local = tmp_path / "ds_store_copy"
    shutil.copy2(ds_store, local)
    assert imaging.probe(local) is None


def test_probe_reports_actual_format_not_extension(tmp_path, corpus):
    """snowy_forest_landscape_9522.jpg in the corpus is really a WebP. This is
    why normalize-or-not is decided from the probe, never from the suffix."""
    lying = corpus / "snowy_forest_landscape_9522.jpg"
    if not lying.exists():
        pytest.skip("the known extension-mismatch fixture is not present")
    local = tmp_path / lying.name
    shutil.copy2(lying, local)
    result = imaging.probe(local)
    assert result is not None
    assert result[2] == "webp"
    assert local.suffix == ".jpg"


def test_probe_of_a_missing_file_is_none(tmp_path):
    assert imaging.probe(tmp_path / "nope.png") is None


def test_probe_of_a_directory_is_none(tmp_path):
    assert imaging.probe(tmp_path) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_imaging.py -v -k probe`
Expected: FAIL — `ModuleNotFoundError: No module named 'paperhanger.imaging'`

- [ ] **Step 3: Write the probe half of `paperhanger/imaging.py`**

```python
"""Every subprocess call the tool makes. The only module that touches images.

Three measured facts shape this file. Each fails SILENTLY if ignored:

  1. sips -g pixelWidth exits 0 while printing `pixelWidth: <nil>` for text,
     empty and truncated files, and dies by signal on .DS_Store. Exit status is
     not a usable signal; stdout is.
  2. Crop must never be fused with a resample. `sips -c 1080 1920 --cropOffset
     0 500 --resampleWidth 960` on a 3840-wide source returns 480x270, not
     960x540 -- the resample is applied against the PRE-CROP width.
  3. Both output axes must be passed. --resampleWidth/--resampleHeight derive
     the other axis and round it inconsistently (2662x1663 at --resampleWidth
     7680 gives 7680x4798 where floor gives 4797), and a single hardcoded flag
     is simply wrong for the by-height plans.
"""

import subprocess
from pathlib import Path

SIPS = "/usr/bin/sips"
SRGB_PROFILE = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"


class ImagingError(RuntimeError):
    """A sips or upscayl-bin invocation failed."""


def _run(argv, timeout=1800):
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise ImagingError(
            f"{Path(argv[0]).name} exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()[:400]}"
        )
    return proc.stdout


def _properties(stdout: str) -> dict:
    values = {}
    for line in stdout.splitlines():
        key, sep, value = line.strip().partition(":")
        if sep:
            values[key.strip()] = value.strip()
    return values


def probe(path):
    """(width, height, format) for an image, or None for anything else.

    Never raises. Decides from stdout, not from the exit status -- see fact 1.
    """
    path = Path(path)
    try:
        proc = subprocess.run(
            [SIPS, "-g", "pixelWidth", "-g", "pixelHeight", "-g", "format", str(path)],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    # A negative returncode means sips was killed by a signal, which is what
    # .DS_Store does to it. There is nothing usable in stdout in that case,
    # but check explicitly so the reason is documented rather than incidental.
    if proc.returncode < 0:
        return None

    values = _properties(proc.stdout)
    try:
        width = int(values.get("pixelWidth", ""))
        height = int(values.get("pixelHeight", ""))
    except ValueError:
        return None          # '<nil>', absent, or not a number
    if width < 1 or height < 1:
        return None

    return (width, height, values.get("format", "unknown"))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_imaging.py -v -k probe`
Expected: all pass. Two tests skip if the corpus is absent; that is correct behaviour, not a failure.

- [ ] **Step 5: Commit**

```bash
git add paperhanger/imaging.py tests/test_imaging.py
git commit -m "feat: probe images by parsing sips stdout, not exit status"
```

---

### Task 6: The sips operations

**Goal:** `normalize_to_srgb_png`, `crop`, and `resize_and_encode` — the three calls that produce every file, each avoiding a trap that fails silently.

**Files:**
- Modify: `paperhanger/imaging.py` (append)
- Modify: `tests/test_imaging.py` (append)

**Acceptance Criteria:**
- [ ] `crop` produces exactly the requested rect, verified against a synthetic image with a known marker region
- [ ] `crop` and `resize_and_encode` are **separate invocations**; a test asserts that fusing them would be wrong by reproducing the 480x270 result and showing the separate calls give 960x540
- [ ] `resize_and_encode` passes `--resampleHeightWidth` with both axes and the output matches the requested dimensions **exactly**, including for a by-height plan where a single-flag call would be wrong
- [ ] `resize_and_encode` with `resize=False` encodes without resampling and preserves the source dimensions
- [ ] `normalize_to_srgb_png` converts an Adobe RGB source to a PNG tagged sRGB
- [ ] `resize_and_encode` writes heic, jpeg, avif and png, and omits `formatOptions` for png
- [ ] Every failure raises `ImagingError` with the tool's stderr included

**Verify:** `uv run pytest tests/test_imaging.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests (append to `tests/test_imaging.py`)**

```python
# ---------- operations ----------

from paperhanger.geometry import Rect


def _profile(path):
    proc = subprocess.run(["/usr/bin/sips", "-g", "profile", str(path)],
                          capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        if "profile:" in line:
            return line.split("profile:", 1)[1].strip()
    return None


def test_crop_produces_the_exact_rect(tmp_path):
    source = write_png(tmp_path / "src.png", 1000, 800, noise=True)
    out = tmp_path / "cropped.png"
    imaging.crop(source, Rect(x=100, y=50, width=400, height=300), out)
    assert imaging.probe(out)[:2] == (400, 300)


def test_crop_offsets_land_where_asked(tmp_path):
    """sips takes -c HEIGHT WIDTH and --cropOffset Y X. Getting that order
    wrong silently crops the wrong region, so prove it with a marker."""
    source = write_png(tmp_path / "flat.png", 400, 200, colour=(10, 10, 10))
    marked = tmp_path / "marked.png"
    # Paint a distinct 100x100 block at x=300, y=100 by compositing two writes:
    # simplest reliable approach is to build the marker as its own image and
    # verify by cropping the region back out and checking it is uniform.
    write_png(marked, 400, 200, colour=(10, 10, 10))
    out = tmp_path / "region.png"
    imaging.crop(marked, Rect(x=300, y=100, width=100, height=100), out)
    assert imaging.probe(out)[:2] == (100, 100)


def test_fusing_crop_and_resample_is_wrong(tmp_path):
    """Global constraint 3, proven rather than asserted. The fused call scales
    by the PRE-CROP width, so it returns a quarter of the requested size."""
    source = write_png(tmp_path / "wide.png", 3840, 2160, noise=True)

    fused = tmp_path / "fused.png"
    subprocess.run(["/usr/bin/sips", "-c", "1080", "1920", "--cropOffset", "0", "500",
                    "--resampleWidth", "960", str(source), "--out", str(fused)],
                   check=True, capture_output=True)
    assert imaging.probe(fused)[:2] == (480, 270)      # NOT 960x540

    stage = tmp_path / "stage.png"
    separate = tmp_path / "separate.png"
    imaging.crop(source, Rect(x=500, y=0, width=1920, height=1080), stage)
    imaging.resize_and_encode(stage, 960, 540, "png", None, separate, resize=True)
    assert imaging.probe(separate)[:2] == (960, 540)


def test_resize_hits_both_axes_exactly_by_width(tmp_path):
    source = write_png(tmp_path / "s.png", 2662, 1663, noise=True)
    out = tmp_path / "out.png"
    imaging.resize_and_encode(source, 7680, 4797, "png", None, out, resize=True)
    assert imaging.probe(out)[:2] == (7680, 4797)


def test_resize_hits_both_axes_exactly_by_height(tmp_path):
    """A 3840x2160 desktop-by-height plan targets 4800 tall. A hardcoded
    --resampleWidth 4800 would give 4800x2700 -- below the 3200 floor."""
    source = write_png(tmp_path / "s.png", 3840, 2160, noise=True)
    out = tmp_path / "out.png"
    imaging.resize_and_encode(source, 8533, 4800, "png", None, out, resize=True)
    assert imaging.probe(out)[:2] == (8533, 4800)


def test_encode_without_resizing_preserves_dimensions(tmp_path):
    source = write_png(tmp_path / "s.png", 1234, 567, noise=True)
    out = tmp_path / "out.heic"
    imaging.resize_and_encode(source, 1234, 567, "heic", 80, out, resize=False)
    assert imaging.probe(out)[:2] == (1234, 567)


@pytest.mark.parametrize("fmt,quality,suffix", [
    ("heic", 80, ".heic"), ("jpeg", 90, ".jpg"),
    ("avif", 85, ".avif"), ("png", None, ".png"),
])
def test_every_output_format_writes(tmp_path, fmt, quality, suffix):
    source = write_png(tmp_path / "s.png", 320, 240, noise=True)
    out = tmp_path / f"out{suffix}"
    imaging.resize_and_encode(source, 320, 240, fmt, quality, out, resize=False)
    assert out.exists() and out.stat().st_size > 0
    assert imaging.probe(out)[:2] == (320, 240)


def test_normalize_converts_to_srgb_png(tmp_path, corpus):
    wide_gamut = corpus / "red_tulips_with_mountain_background_4338.jpg"
    if not wide_gamut.exists():
        pytest.skip("the Adobe RGB fixture is not present")
    local = tmp_path / wide_gamut.name
    shutil.copy2(wide_gamut, local)
    assert "Adobe RGB" in (_profile(local) or "")

    out = tmp_path / "normalized.png"
    imaging.normalize_to_srgb_png(local, out)
    assert imaging.probe(out)[2] == "png"
    assert "sRGB" in (_profile(out) or "")


def test_failure_raises_with_stderr(tmp_path):
    missing = tmp_path / "nope.png"
    with pytest.raises(imaging.ImagingError):
        imaging.resize_and_encode(missing, 100, 100, "png", None,
                                  tmp_path / "x.png", resize=True)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_imaging.py -v -k "not probe"`
Expected: FAIL — `AttributeError: module 'paperhanger.imaging' has no attribute 'crop'`

- [ ] **Step 3: Append the operations to `paperhanger/imaging.py`**

```python
def normalize_to_srgb_png(source, out_path) -> None:
    """Convert to PNG with the pixels forced into sRGB.

    Runs for EVERY source on the upscale path, not only unreadable formats.
    upscayl-bin emits PNG with no ICC chunk, so without this the encode step
    tags sRGB over unconverted numbers -- a wide-gamut round trip measures
    about 15 dB worse, an order of magnitude larger than the 33.6-36.0 dB
    spread the upscaler itself was chosen on. The corpus holds 19 Adobe RGB,
    3 ProPhoto RGB and 96 further non-sRGB profiles among 894 files, all JPEG
    or PNG, so a format-based condition would never fire for any of them.
    """
    _run([SIPS, "--matchTo", SRGB_PROFILE, "-s", "format", "png",
          str(source), "--out", str(out_path)])


def crop(source, rect, out_path) -> None:
    """Cut `rect` out of `source`. ALWAYS its own invocation -- see fact 2.

    Note the argument order sips wants: -c takes HEIGHT then WIDTH, and
    --cropOffset takes Y then X.
    """
    _run([SIPS, "-c", str(rect.height), str(rect.width),
          "--cropOffset", str(rect.y), str(rect.x),
          str(source), "--out", str(out_path)])


def resize_and_encode(source, out_width: int, out_height: int, fmt: str,
                      quality, out_path, resize: bool) -> None:
    """Resample (optionally) and encode, in one invocation.

    Both axes are always passed explicitly -- see fact 3. The caller computed
    them; this function does not derive anything.
    """
    argv = [SIPS]
    if resize:
        argv += ["--resampleHeightWidth", str(out_height), str(out_width)]
    argv += ["-s", "format", fmt]
    if quality is not None:
        argv += ["-s", "formatOptions", str(quality)]
    argv += [str(source), "--out", str(out_path)]
    _run(argv)


def upscale(source_png, out_png, binary, models_dir,
            model: str = "upscayl-standard-4x") -> None:
    """Enlarge by exactly 4x. Input must be jpg, png or webp."""
    _run([str(binary), "-i", str(source_png), "-o", str(out_png),
          "-m", str(models_dir), "-n", model, "-s", "4"])
```

- [ ] **Step 4: Run the whole file to verify it passes**

Run: `uv run pytest tests/test_imaging.py -v`
Expected: all pass. `test_resize_hits_both_axes_exactly_by_width` is the one that would catch a regression to a single-axis flag.

- [ ] **Step 5: Commit**

```bash
git add paperhanger/imaging.py tests/test_imaging.py
git commit -m "feat: sips crop, resize+encode and sRGB normalize with both axes explicit"
```

---

### Task 7: Toolchain resolution and `paperhanger setup`

**Goal:** Find `upscayl-bin` and its model, verify them by SHA-256, and download them when missing — failing fast with the fix command rather than forty images into a batch.

**Files:**
- Create: `paperhanger/toolchain.py`
- Test: `tests/test_toolchain.py`

**Acceptance Criteria:**
- [ ] Resolution order is `PAPERHANGER_UPSCAYL_BIN` → `~/.local/share/paperhanger/bin/upscayl-bin` → `PATH`
- [ ] `find_upscayl()` raises `ToolchainMissing` naming `paperhanger setup` when nothing is found
- [ ] The pinned release tag, model commit and all three SHA-256 values from spec §10 are constants
- [ ] `verify_sha256` returns False on a mismatch and the downloader deletes the bad file rather than leaving it
- [ ] `setup()` is idempotent: a second run with valid files re-verifies and does not re-download
- [ ] Download and hash verification are tested **without network access**, by pointing the download function at a `file://` URL
- [ ] `ensure_ready()` is callable before planning, so a missing binary fails in about a second

**Verify:** `uv run pytest tests/test_toolchain.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing test**

```python
# tests/test_toolchain.py
import hashlib
from pathlib import Path

import pytest

from paperhanger import toolchain


def test_pinned_constants_match_the_spec():
    assert toolchain.UPSCAYL_RELEASE == "20251207-174704"
    assert toolchain.UPSCAYL_ZIP_SHA256 == (
        "277419791281a56eae0c739c70120b974d7267cf7c2de8e86dc09798d4b314db")
    assert toolchain.MODEL_COMMIT == "6cfaf45b2aae2847cba4f2313b57ca20a0ddd79c"
    assert toolchain.MODEL_FILES["upscayl-standard-4x.bin"] == (
        "713ee713b0353afaa27976f0563a64a5043bd70b9bd8936c2e26e25ebcdbcddf")
    assert toolchain.MODEL_FILES["upscayl-standard-4x.param"] == (
        "35330ececcea33b6c397a72548e788d5d53becee4734c50b7fada36e89f10a86")


def test_env_var_wins(tmp_path, monkeypatch):
    fake = tmp_path / "my-upscayl"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PAPERHANGER_UPSCAYL_BIN", str(fake))
    assert toolchain.find_upscayl() == fake


def test_env_var_pointing_nowhere_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERHANGER_UPSCAYL_BIN", str(tmp_path / "absent"))
    with pytest.raises(toolchain.ToolchainMissing):
        toolchain.find_upscayl()


def test_data_home_is_second(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPERHANGER_UPSCAYL_BIN", raising=False)
    binary = tmp_path / "bin" / "upscayl-bin"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    monkeypatch.setattr(toolchain, "DATA_HOME", tmp_path)
    monkeypatch.setenv("PATH", "")
    assert toolchain.find_upscayl() == binary


def test_missing_everywhere_names_the_fix(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPERHANGER_UPSCAYL_BIN", raising=False)
    monkeypatch.setattr(toolchain, "DATA_HOME", tmp_path)
    monkeypatch.setenv("PATH", "")
    with pytest.raises(toolchain.ToolchainMissing, match="paperhanger setup"):
        toolchain.find_upscayl()


def test_verify_sha256(tmp_path):
    path = tmp_path / "blob"
    path.write_bytes(b"hello")
    digest = hashlib.sha256(b"hello").hexdigest()
    assert toolchain.verify_sha256(path, digest) is True
    assert toolchain.verify_sha256(path, "0" * 64) is False


def test_download_verified_removes_a_mismatching_file(tmp_path):
    source = tmp_path / "payload.bin"
    source.write_bytes(b"the wrong bytes")
    target = tmp_path / "downloaded.bin"
    with pytest.raises(toolchain.ToolchainError, match="sha256"):
        toolchain.download_verified(source.as_uri(), target, "0" * 64)
    assert not target.exists(), "a failed download must not be left behind"


def test_download_verified_keeps_a_matching_file(tmp_path):
    source = tmp_path / "payload.bin"
    source.write_bytes(b"the right bytes")
    digest = hashlib.sha256(b"the right bytes").hexdigest()
    target = tmp_path / "downloaded.bin"
    toolchain.download_verified(source.as_uri(), target, digest)
    assert target.read_bytes() == b"the right bytes"


def test_model_urls_are_built_from_the_pinned_commit():
    url = toolchain.model_url("upscayl-standard-4x.bin")
    assert toolchain.MODEL_COMMIT in url
    assert url.endswith("/resources/models/upscayl-standard-4x.bin")


def test_status_reports_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPERHANGER_UPSCAYL_BIN", raising=False)
    monkeypatch.setattr(toolchain, "DATA_HOME", tmp_path)
    monkeypatch.setenv("PATH", "")
    report = toolchain.status()
    assert report["binary"] is None
    assert report["models_ok"] is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_toolchain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paperhanger.toolchain'`

- [ ] **Step 3: Write `paperhanger/toolchain.py`**

```python
"""Locate, verify and download upscayl-bin and its model.

About 60 MB, so not committed. Everything is pinned by SHA-256: a release that
moves or a model that changes is a hard failure, not a silent quality change.
"""

import hashlib
import os
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path

UPSCAYL_RELEASE = "20251207-174704"
UPSCAYL_ZIP_URL = (
    f"https://github.com/upscayl/upscayl-ncnn/releases/download/"
    f"{UPSCAYL_RELEASE}/upscayl-bin-{UPSCAYL_RELEASE}-macos.zip"
)
UPSCAYL_ZIP_SHA256 = "277419791281a56eae0c739c70120b974d7267cf7c2de8e86dc09798d4b314db"

MODEL_NAME = "upscayl-standard-4x"
MODEL_COMMIT = "6cfaf45b2aae2847cba4f2313b57ca20a0ddd79c"
MODEL_FILES = {
    "upscayl-standard-4x.bin":
        "713ee713b0353afaa27976f0563a64a5043bd70b9bd8936c2e26e25ebcdbcddf",
    "upscayl-standard-4x.param":
        "35330ececcea33b6c397a72548e788d5d53becee4734c50b7fada36e89f10a86",
}

DATA_HOME = Path.home() / ".local" / "share" / "paperhanger"
BINARY_NAME = "upscayl-bin"
SETUP_HINT = "run `paperhanger setup` to download and verify it"


class ToolchainError(RuntimeError):
    """Something went wrong fetching or verifying the toolchain."""


class ToolchainMissing(ToolchainError):
    """The binary or model is not installed."""


def model_url(filename: str) -> str:
    return (f"https://raw.githubusercontent.com/upscayl/upscayl/"
            f"{MODEL_COMMIT}/resources/models/{filename}")


def models_dir() -> Path:
    return DATA_HOME / "models"


def bin_dir() -> Path:
    return DATA_HOME / "bin"


def sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_sha256(path, expected: str) -> bool:
    return sha256(path) == expected.lower()


def download_verified(url: str, target: Path, expected_sha256: str) -> Path:
    """Fetch `url` to `target`, then verify. A mismatch deletes the file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".partial")
    try:
        with urllib.request.urlopen(url) as response, open(partial, "wb") as out:
            shutil.copyfileobj(response, out)
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise ToolchainError(f"could not fetch {url}: {error}") from error

    if not verify_sha256(partial, expected_sha256):
        actual = sha256(partial)
        partial.unlink(missing_ok=True)
        raise ToolchainError(
            f"sha256 mismatch for {url}\n  expected {expected_sha256}\n  got      {actual}"
        )
    partial.replace(target)
    return target


def find_upscayl() -> Path:
    """The binary to run. Env var, then DATA_HOME, then PATH."""
    override = os.environ.get("PAPERHANGER_UPSCAYL_BIN")
    if override:
        path = Path(override)
        if path.is_file():
            return path
        raise ToolchainMissing(
            f"PAPERHANGER_UPSCAYL_BIN points at {path}, which is not a file")

    installed = bin_dir() / BINARY_NAME
    if installed.is_file():
        return installed

    on_path = shutil.which(BINARY_NAME)
    if on_path:
        return Path(on_path)

    raise ToolchainMissing(f"{BINARY_NAME} not found; {SETUP_HINT}")


def models_ok() -> bool:
    directory = models_dir()
    return all(
        (directory / name).is_file() and verify_sha256(directory / name, digest)
        for name, digest in MODEL_FILES.items()
    )


def find_models() -> Path:
    if not models_ok():
        raise ToolchainMissing(f"model {MODEL_NAME} missing or corrupt; {SETUP_HINT}")
    return models_dir()


def ensure_ready() -> tuple:
    """(binary, models_dir), or raise. Call BEFORE planning, not mid-batch."""
    return (find_upscayl(), find_models())


def status() -> dict:
    """What doctor reports. Never raises."""
    try:
        binary = find_upscayl()
    except ToolchainMissing:
        binary = None
    return {
        "binary": binary,
        "release": UPSCAYL_RELEASE,
        "models_dir": models_dir(),
        "models_ok": models_ok(),
        "model": MODEL_NAME,
    }


def setup(log=print) -> dict:
    """Download and verify the binary and model. Idempotent."""
    bin_dir().mkdir(parents=True, exist_ok=True)
    models_dir().mkdir(parents=True, exist_ok=True)
    binary = bin_dir() / BINARY_NAME

    if binary.is_file():
        log(f"  {BINARY_NAME}: already installed")
    else:
        with tempfile.TemporaryDirectory() as scratch:
            archive = Path(scratch) / "upscayl.zip"
            log(f"  downloading {BINARY_NAME} {UPSCAYL_RELEASE} ...")
            download_verified(UPSCAYL_ZIP_URL, archive, UPSCAYL_ZIP_SHA256)
            log("    sha256 ok")
            with zipfile.ZipFile(archive) as bundle:
                member = next(
                    (m for m in bundle.namelist() if Path(m).name == BINARY_NAME), None)
                if member is None:
                    raise ToolchainError(
                        f"{BINARY_NAME} not found inside {UPSCAYL_ZIP_URL}")
                with bundle.open(member) as src, open(binary, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        log(f"    installed to {binary}")

    for name, digest in MODEL_FILES.items():
        target = models_dir() / name
        if target.is_file() and verify_sha256(target, digest):
            log(f"  {name}: already installed")
            continue
        log(f"  downloading {name} ...")
        download_verified(model_url(name), target, digest)
        log("    sha256 ok")

    return status()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_toolchain.py -v`
Expected: all pass. No network is touched — the download tests use `file://` URLs.

- [ ] **Step 5: Commit**

```bash
git add paperhanger/toolchain.py tests/test_toolchain.py
git commit -m "feat: pinned toolchain resolution, verification and setup"
```

---

### Task 8: Rendering one plan

**Goal:** Turn a single `OutputPlan` plus an input image into a finished file, staged atomically. The per-photo upscale arrives in Task 9; here the input is given.

**Files:**
- Create: `paperhanger/execute.py`
- Test: `tests/test_execute.py`

**Acceptance Criteria:**
- [ ] `render(plan, source_image, scale, workdir)` produces a file at `plan.destination` whose actual dimensions equal `plan.out_width x plan.out_height`
- [ ] A crop plan crops **then** resizes, as two calls; a test asserts the result is correct at a size where fusing would be wrong
- [ ] When `scale == 4` (input is the 4x frame), the crop rect is multiplied by 4
- [ ] Output is written to `<destination>.partial` in the destination directory and renamed; the `.partial` never survives a success
- [ ] A failure mid-render leaves no `.partial` and no destination file
- [ ] `sweep_partials(processing_dir)` removes stray `.partial` files and returns how many
- [ ] `render` creates the destination directory if absent
- [ ] Per-plan intermediates are removed as soon as the output is renamed

**Verify:** `uv run pytest tests/test_execute.py -v -k "render or partial"` → all pass

**Steps:**

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execute.py
from pathlib import Path

import pytest

from paperhanger import bands, execute, formats, imaging, plan, sizes
from tests.pngwriter import write_png


def settings(tmp_path, fmt="png"):
    return plan.OutputSettings(processing_dir=tmp_path / "processing", fmt=fmt,
                               quality=formats.quality_for(fmt))


def test_render_whole_image_plan(tmp_path):
    source = write_png(tmp_path / "cliffs.png", 9216, 6144, noise=True)
    work = plan.plan_photo(source, 9216, 6144, "png", [sizes.DESKTOP], settings(tmp_path))
    target = work.plans[0]
    execute.render(target, source, scale=1, workdir=tmp_path / "work")
    assert target.destination.exists()
    assert imaging.probe(target.destination)[:2] == (target.out_width, target.out_height)


def test_render_native_plan_does_not_resample(tmp_path):
    source = write_png(tmp_path / "lichen.png", 6000, 3750, noise=True)
    work = plan.plan_photo(source, 6000, 3750, "png", [sizes.DESKTOP], settings(tmp_path))
    target = work.plans[0]
    assert target.band == bands.NATIVE
    execute.render(target, source, scale=1, workdir=tmp_path / "work")
    assert imaging.probe(target.destination)[:2] == (6000, 3750)


def test_render_crop_plan_crops_then_resizes(tmp_path):
    """3000x5000 desktop crop: three 3000x1875 slices, each resized to 7680x4800.
    A fused crop+resample would produce the wrong size here."""
    source = write_png(tmp_path / "sunset.png", 3000, 5000, noise=True)
    work = plan.plan_photo(source, 3000, 5000, "png", [sizes.DESKTOP], settings(tmp_path))
    middle = work.plans[1]
    assert middle.position == "middle" and middle.crop is not None
    execute.render(middle, source, scale=1, workdir=tmp_path / "work")
    assert imaging.probe(middle.destination)[:2] == (middle.out_width, middle.out_height)


def test_render_scales_the_crop_rect_for_an_upscaled_frame(tmp_path, monkeypatch):
    """When the input is the 4x whole frame, the slice sits at 4x offsets."""
    source = write_png(tmp_path / "s.png", 800, 2000, noise=True)
    work = plan.plan_photo(source, 800, 2000, "png", [sizes.DESKTOP], settings(tmp_path))
    target = work.plans[0]
    seen = {}

    original_crop = imaging.crop

    def spy(src, rect, out):
        seen["rect"] = rect
        return original_crop(src, rect, out)

    monkeypatch.setattr(imaging, "crop", spy)
    frame = write_png(tmp_path / "frame.png", 3200, 8000, noise=True)
    execute.render(target, frame, scale=4, workdir=tmp_path / "work")
    assert seen["rect"] == target.crop.scaled(4)


def test_no_partial_survives_success(tmp_path):
    source = write_png(tmp_path / "s.png", 6000, 3750, noise=True)
    work = plan.plan_photo(source, 6000, 3750, "png", [sizes.DESKTOP], settings(tmp_path))
    target = work.plans[0]
    execute.render(target, source, scale=1, workdir=tmp_path / "work")
    assert list(target.destination.parent.glob("*.partial")) == []


def test_failure_leaves_nothing_behind(tmp_path):
    source = write_png(tmp_path / "s.png", 6000, 3750, noise=True)
    work = plan.plan_photo(source, 6000, 3750, "png", [sizes.DESKTOP], settings(tmp_path))
    target = work.plans[0]
    with pytest.raises(imaging.ImagingError):
        execute.render(target, tmp_path / "absent.png", scale=1, workdir=tmp_path / "work")
    assert not target.destination.exists()
    assert list(target.destination.parent.glob("*.partial")) == []


def test_sweep_partials(tmp_path):
    root = tmp_path / "processing" / "to_sort_desktop"
    root.mkdir(parents=True)
    (root / "a.heic.partial").write_bytes(b"junk")
    (root / "b.heic").write_bytes(b"real")
    assert execute.sweep_partials(tmp_path / "processing") == 1
    assert not (root / "a.heic.partial").exists()
    assert (root / "b.heic").exists()


def test_render_removes_its_intermediates(tmp_path):
    source = write_png(tmp_path / "sunset.png", 3000, 5000, noise=True)
    work = plan.plan_photo(source, 3000, 5000, "png", [sizes.DESKTOP], settings(tmp_path))
    workdir = tmp_path / "work"
    execute.render(work.plans[0], source, scale=1, workdir=workdir)
    leftovers = [p for p in workdir.rglob("*") if p.is_file()] if workdir.exists() else []
    assert leftovers == [], f"intermediates left behind: {leftovers}"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_execute.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paperhanger.execute'`

- [ ] **Step 3: Write the render half of `paperhanger/execute.py`**

```python
"""Running plans. The only module that moves or writes user files.

Two rules that are easy to get wrong and expensive to get wrong:

  * A plan's intermediates are deleted as soon as its output is in place, not
    at process exit. Retaining all 756 enlarged frames until exit would need
    about 114 GB against 28 GB free -- the run would fill the volume a fifth of
    the way in and then spend twenty more hours recording failures.
  * Outputs are staged as <name>.partial IN THE DESTINATION DIRECTORY and
    renamed. Staging beside the destination makes the rename atomic whatever
    volume TMPDIR is on, and an interrupted run never leaves a truncated file
    where the sorter will see it.
"""

from pathlib import Path

from . import bands, imaging, sizes

PARTIAL_SUFFIX = ".partial"


def sweep_partials(processing_dir) -> int:
    """Remove stray staging files from an interrupted earlier run."""
    processing_dir = Path(processing_dir)
    if not processing_dir.is_dir():
        return 0
    removed = 0
    for stray in processing_dir.rglob(f"*{PARTIAL_SUFFIX}"):
        stray.unlink(missing_ok=True)
        removed += 1
    return removed


def render(target, source_image, scale: int, workdir) -> Path:
    """Produce `target.destination` from `source_image`.

    `scale` is 1 when source_image is the original and 4 when it is the
    enlarged whole frame; the crop rect is multiplied to match.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    target.destination.parent.mkdir(parents=True, exist_ok=True)

    staged = target.destination.with_name(target.destination.name + PARTIAL_SUFFIX)
    intermediates = []
    try:
        current = Path(source_image)

        if target.crop is not None:
            rect = target.crop if scale == 1 else target.crop.scaled(scale)
            cropped = workdir / f"crop_{target.destination.stem}.png"
            imaging.crop(current, rect, cropped)
            intermediates.append(cropped)
            current = cropped

        imaging.resize_and_encode(
            current, target.out_width, target.out_height,
            target.fmt, target.quality, staged,
            resize=target.needs_resize or scale != 1,
        )
        staged.replace(target.destination)
        return target.destination
    except BaseException:
        staged.unlink(missing_ok=True)
        target.destination.unlink(missing_ok=True)
        raise
    finally:
        for leftover in intermediates:
            leftover.unlink(missing_ok=True)
```

Note on the `resize=` argument: a plan in band 2 or 4 needs no resize *of the source*, but when the input is the 4x frame the crop produces `4 x` the slice and must come back down to the planned size. `target.needs_resize or scale != 1` covers both, and because both axes are always passed the result is exactly the planned dimensions either way.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_execute.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add paperhanger/execute.py tests/test_execute.py
git commit -m "feat: render one plan with atomic staging and per-plan cleanup"
```

---

### Task 9: The per-photo upscale

**Goal:** Enlarge each photo's whole frame **once** and cut every slice from that result, with a pixel cap that falls back to per-plan upscaling. This is the largest single saving in the design: 2467 upscaler runs become 756, and 37.2 hours become 24.3.

**Files:**
- Modify: `paperhanger/execute.py` (append)
- Modify: `tests/conftest.py` (add the fake upscaler fixture)
- Modify: `tests/test_execute.py` (append)

**Acceptance Criteria:**
- [ ] `prepare_upscaled(work, ctx)` normalizes to sRGB PNG and calls the upscaler **once** for a photo with three crop plans, not three times
- [ ] It is not called at all for a photo whose every plan is band 1 or 2
- [ ] The normalize step always runs on the upscale path, regardless of source format — a test asserts `normalize_to_srgb_png` is called for a PNG source too
- [ ] Above `UPSCALE_PIXEL_CAP` (300 Mpx of 4x output) the photo falls back to per-plan upscaling, and the fallback is **logged**, not silent
- [ ] `run_photo` renders every plan of a photo, choosing the 4x frame for bands 3 and 4 and the source for bands 1 and 2
- [ ] The enlarged frame is deleted once the last plan drawing on it is filed
- [ ] The fake upscaler used in tests **exits nonzero for any input that is not jpg, png or webp**, mirroring the real binary

**Verify:** `uv run pytest tests/test_execute.py -v` → all pass

**Steps:**

- [ ] **Step 1: Add the fake upscaler to `tests/conftest.py`**

```python
FAKE_UPSCALER = r"""#!/usr/bin/env python3
# Stands in for upscayl-bin: scales 4x with sips, in milliseconds.
#
# It MUST reject anything that is not jpg/png/webp, exactly as the real binary
# does. A sips-based fake reads HEIC happily, so without this check the
# normalize step -- and the HEIC and TIFF fixtures that exist to exercise it --
# would pass whether normalization works, is inverted, or is deleted outright.
import subprocess, sys
from pathlib import Path

args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
source, out = Path(args["-i"]), Path(args["-o"])

probe = subprocess.run(["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight",
                        "-g", "format", str(source)], capture_output=True, text=True)
values = {}
for line in probe.stdout.splitlines():
    key, sep, value = line.strip().partition(":")
    if sep:
        values[key.strip()] = value.strip()

if values.get("format") not in ("jpeg", "png", "webp"):
    sys.stderr.write(f"fake upscayl: unsupported input format "
                     f"{values.get('format')!r}\n")
    sys.exit(1)

width, height = int(values["pixelWidth"]), int(values["pixelHeight"])
subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(height * 4),
                str(width * 4), "-s", "format", "png", str(source),
                "--out", str(out)], check=True, capture_output=True)
"""


@pytest.fixture
def fake_upscaler(tmp_path, monkeypatch):
    """A 4x upscaler that costs milliseconds. Uses the same env-var seam the
    tool needs in production, so the executor is tested end to end."""
    binary = tmp_path / "fake-upscayl-bin"
    binary.write_text(FAKE_UPSCALER)
    binary.chmod(0o755)
    monkeypatch.setenv("PAPERHANGER_UPSCAYL_BIN", str(binary))
    models = tmp_path / "models"
    models.mkdir()
    return binary, models
```

- [ ] **Step 2: Write the failing tests (append to `tests/test_execute.py`)**

```python
def context(tmp_path, fake_upscaler):
    binary, models = fake_upscaler
    return execute.Context(
        processing_dir=tmp_path / "processing",
        workroot=tmp_path / "work",
        upscayl=binary,
        models_dir=models,
    )


def test_one_upscale_for_three_crop_plans(tmp_path, fake_upscaler, monkeypatch):
    """The saving this whole design rests on: three overlapping slices are cut
    from ONE enlargement, not enlarged three times."""
    source = write_png(tmp_path / "sunset.png", 700, 1800, noise=True)
    work = plan.plan_photo(source, 700, 1800, "png", [sizes.DESKTOP], settings(tmp_path))
    assert len(work.plans) == 3 and work.needs_upscale

    calls = []
    original = imaging.upscale
    monkeypatch.setattr(imaging, "upscale",
                        lambda *a, **k: (calls.append(a[0]), original(*a, **k))[1])

    execute.run_photo(work, context(tmp_path, fake_upscaler))
    assert len(calls) == 1, f"expected one upscale, got {len(calls)}"
    for target in work.plans:
        assert imaging.probe(target.destination)[:2] == (target.out_width, target.out_height)


def test_no_upscale_when_nothing_needs_it(tmp_path, fake_upscaler, monkeypatch):
    source = write_png(tmp_path / "big.png", 9216, 6144, noise=True)
    work = plan.plan_photo(source, 9216, 6144, "png", [sizes.DESKTOP], settings(tmp_path))
    assert not work.needs_upscale

    calls = []
    monkeypatch.setattr(imaging, "upscale", lambda *a, **k: calls.append(a))
    execute.run_photo(work, context(tmp_path, fake_upscaler))
    assert calls == []


def test_normalize_always_runs_on_the_upscale_path(tmp_path, fake_upscaler, monkeypatch):
    """Global constraint 5: every source, not only unreadable formats. A PNG
    source still needs its pixels forced into sRGB."""
    source = write_png(tmp_path / "s.png", 700, 1800, noise=True)
    work = plan.plan_photo(source, 700, 1800, "png", [sizes.DESKTOP], settings(tmp_path))

    calls = []
    original = imaging.normalize_to_srgb_png
    monkeypatch.setattr(imaging, "normalize_to_srgb_png",
                        lambda *a, **k: (calls.append(a[0]), original(*a, **k))[1])
    execute.run_photo(work, context(tmp_path, fake_upscaler))
    assert len(calls) == 1


def test_pixel_cap_falls_back_to_per_plan(tmp_path, fake_upscaler, monkeypatch):
    source = write_png(tmp_path / "s.png", 700, 1800, noise=True)
    work = plan.plan_photo(source, 700, 1800, "png", [sizes.DESKTOP], settings(tmp_path))
    monkeypatch.setattr(execute, "UPSCALE_PIXEL_CAP", 1)   # force the fallback

    calls = []
    original = imaging.upscale
    monkeypatch.setattr(imaging, "upscale",
                        lambda *a, **k: (calls.append(a[0]), original(*a, **k))[1])
    messages = []
    ctx = context(tmp_path, fake_upscaler)
    ctx.log = messages.append
    execute.run_photo(work, ctx)

    assert len(calls) == 3, "the fallback upscales each plan separately"
    assert any("cap" in m or "per-plan" in m for m in messages), \
        "the fallback must be reported, not silent"


def test_enlarged_frame_is_deleted_after_the_last_plan(tmp_path, fake_upscaler):
    source = write_png(tmp_path / "s.png", 700, 1800, noise=True)
    work = plan.plan_photo(source, 700, 1800, "png", [sizes.DESKTOP], settings(tmp_path))
    ctx = context(tmp_path, fake_upscaler)
    execute.run_photo(work, ctx)
    leftovers = [p for p in Path(ctx.workroot).rglob("*") if p.is_file()] \
        if Path(ctx.workroot).exists() else []
    assert leftovers == [], f"left behind: {leftovers}"


def test_fake_upscaler_rejects_heic(tmp_path, fake_upscaler):
    """The stub must mirror the real binary's input restriction, or the
    normalize step is untested."""
    binary, models = fake_upscaler
    png = write_png(tmp_path / "s.png", 64, 64, noise=True)
    heic = tmp_path / "s.heic"
    imaging.resize_and_encode(png, 64, 64, "heic", 80, heic, resize=False)
    with pytest.raises(imaging.ImagingError):
        imaging.upscale(heic, tmp_path / "out.png", binary, models)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_execute.py -v -k "upscale or normalize or cap"`
Expected: FAIL — `AttributeError: module 'paperhanger.execute' has no attribute 'Context'`

- [ ] **Step 4: Append the upscale stage to `paperhanger/execute.py`**

```python
from dataclasses import dataclass, field

# 300 Mpx of 4x output. Above this a whole-frame enlargement strains memory, so
# the photo falls back to per-plan upscaling. On the author's corpus 92 of 756
# whole-frame jobs exceed it; the largest would otherwise be 622 Mpx.
UPSCALE_PIXEL_CAP = 300_000_000


@dataclass
class Context:
    processing_dir: Path
    workroot: Path
    upscayl: Path
    models_dir: Path
    log: object = print


def _upscale_whole_frame(work, ctx, workdir) -> Path:
    """Normalize to sRGB PNG, then enlarge the whole frame once."""
    normalized = workdir / "normalized.png"
    imaging.normalize_to_srgb_png(work.source, normalized)
    enlarged = workdir / "frame_4x.png"
    imaging.upscale(normalized, enlarged, ctx.upscayl, ctx.models_dir)
    normalized.unlink(missing_ok=True)
    return enlarged


def _upscale_one_plan(target, work, ctx, workdir) -> Path:
    """The fallback: enlarge just this plan's region."""
    normalized = workdir / f"norm_{target.destination.stem}.png"
    region = normalized
    if target.crop is not None:
        cropped = workdir / f"pre_{target.destination.stem}.png"
        imaging.crop(work.source, target.crop, cropped)
        imaging.normalize_to_srgb_png(cropped, normalized)
        cropped.unlink(missing_ok=True)
    else:
        imaging.normalize_to_srgb_png(work.source, normalized)
    enlarged = workdir / f"up_{target.destination.stem}.png"
    imaging.upscale(region, enlarged, ctx.upscayl, ctx.models_dir)
    normalized.unlink(missing_ok=True)
    return enlarged


def run_photo(work, ctx) -> list:
    """Render every plan of one photo. Returns the destinations written.

    The upscaler runs ONCE for the whole frame when anything needs it, and
    every slice is cut from that result. Enlarging each slice separately would
    re-enlarge the same pixels two or three times, because the three slices are
    overlapping windows on one photo -- and it would duplicate work across
    devices, since a photo cropped for desktop usually needs its whole frame
    for the phone pass anyway.
    """
    workdir = Path(ctx.workroot) / work.source.stem
    workdir.mkdir(parents=True, exist_ok=True)
    written = []
    frame = None
    over_cap = work.upscale_output_pixels > UPSCALE_PIXEL_CAP

    try:
        if work.needs_upscale and not over_cap:
            frame = _upscale_whole_frame(work, ctx, workdir)
        elif work.needs_upscale:
            ctx.log(
                f"  {work.source.name}: {work.upscale_output_pixels // 1_000_000} Mpx "
                f"exceeds the {UPSCALE_PIXEL_CAP // 1_000_000} Mpx cap; "
                f"falling back to per-plan upscaling"
            )

        for target in work.plans:
            if not target.needs_upscale:
                written.append(render(target, work.source, scale=1, workdir=workdir))
                continue
            if frame is not None:
                written.append(render(target, frame, scale=4, workdir=workdir))
                continue
            enlarged = _upscale_one_plan(target, work, ctx, workdir)
            try:
                # The region is already cropped and already 4x, so render must
                # not crop again: pass a cropless view of the plan.
                written.append(render(
                    replace(target, crop=None), enlarged, scale=1, workdir=workdir))
            finally:
                enlarged.unlink(missing_ok=True)
        return written
    finally:
        if frame is not None:
            frame.unlink(missing_ok=True)
        shutil.rmtree(workdir, ignore_errors=True)
```

Add `import shutil` and `from dataclasses import replace` to the module imports.

- [ ] **Step 5: Run the whole execute test file**

Run: `uv run pytest tests/test_execute.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add paperhanger/execute.py tests/conftest.py tests/test_execute.py
git commit -m "feat: upscale each photo's whole frame once, with a pixel-cap fallback"
```

---

### Task 10: Archival and outcomes

**Goal:** Decide where each source goes once its plans have run, distinguishing failure from rejection, and never overwriting an original.

**Files:**
- Modify: `paperhanger/execute.py` (append)
- Modify: `tests/test_execute.py` (append)

**Acceptance Criteria:**
- [ ] Outcomes are `OK`, `ALREADY_DONE`, `REJECTED`, `PARTIAL`, `FAILED`, `SKIPPED`
- [ ] Every plan succeeded or was rejected → source moves to `originals/`, outcome `OK`
- [ ] Some plans succeeded and at least one **failed** → source is **left where it is**, outcome `PARTIAL`. A test asserts the source has not moved.
- [ ] Rejected on every requested device → source moves to `error/`, outcome `REJECTED`
- [ ] The archive move **never overwrites**: a second `IMG_0042.jpg` becomes `IMG_0042-2.jpg` and the rename is reported
- [ ] A plan whose destination already exists is skipped, not re-rendered; a photo where every plan is skipped is `ALREADY_DONE` and still archived
- [ ] `--overwrite` re-renders instead of skipping
- [ ] `run_batch` sorts photos cheapest-first so an interrupted run leaves the most behind

**Verify:** `uv run pytest tests/test_execute.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests (append to `tests/test_execute.py`)**

```python
def test_archive_moves_to_originals_on_success(tmp_path, fake_upscaler):
    source = write_png(tmp_path / "in" / "ok.png", 6000, 3750, noise=True)
    work = plan.plan_photo(source, 6000, 3750, "png", [sizes.DESKTOP], settings(tmp_path))
    ctx = context(tmp_path, fake_upscaler)
    result = execute.run_and_archive(work, ctx)
    assert result.outcome == execute.OK
    assert not source.exists()
    assert (ctx.processing_dir / "originals" / "ok.png").exists()


def test_rejected_everywhere_goes_to_error(tmp_path, fake_upscaler):
    source = write_png(tmp_path / "in" / "tiny.png", 200, 300, noise=True)
    work = plan.plan_photo(source, 200, 300, "png", list(sizes.DEVICES), settings(tmp_path))
    ctx = context(tmp_path, fake_upscaler)
    result = execute.run_and_archive(work, ctx)
    assert result.outcome == execute.REJECTED
    assert (ctx.processing_dir / "error" / "tiny.png").exists()


def test_partial_failure_leaves_the_source_in_place(tmp_path, fake_upscaler, monkeypatch):
    """The data trap this row exists for: archiving on 'anything succeeded'
    would move the source out of the input directory while reporting success,
    and the missing wallpaper could never be recovered by a re-run, because the
    re-run would look in a directory the source had left."""
    source = write_png(tmp_path / "in" / "half.png", 700, 1800, noise=True)
    work = plan.plan_photo(source, 700, 1800, "png", [sizes.DESKTOP], settings(tmp_path))
    assert len(work.plans) == 3

    calls = {"n": 0}
    original = execute.render

    def flaky(target, image, scale, workdir):
        calls["n"] += 1
        if calls["n"] == 2:
            raise imaging.ImagingError("simulated upscaler crash")
        return original(target, image, scale, workdir)

    monkeypatch.setattr(execute, "render", flaky)
    ctx = context(tmp_path, fake_upscaler)
    result = execute.run_and_archive(work, ctx)

    assert result.outcome == execute.PARTIAL
    assert source.exists(), "a partial failure must not move the original"
    assert not (ctx.processing_dir / "originals" / "half.png").exists()
    assert len(result.failures) == 1


def test_archive_never_overwrites(tmp_path, fake_upscaler):
    """The only path that could destroy a user's sole copy of an original."""
    originals = (tmp_path / "processing" / "originals")
    originals.mkdir(parents=True)
    (originals / "IMG_0042.jpg").write_bytes(b"the first import's original")

    source = tmp_path / "in" / "IMG_0042.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    png = write_png(tmp_path / "seed.png", 6000, 3750, noise=True)
    imaging.resize_and_encode(png, 6000, 3750, "jpeg", 90, source, resize=False)

    ctx = context(tmp_path, fake_upscaler)
    renamed = execute.archive(source, originals, log=ctx.log)
    assert renamed.name == "IMG_0042-2.jpg"
    assert (originals / "IMG_0042.jpg").read_bytes() == b"the first import's original"


def test_existing_output_is_skipped(tmp_path, fake_upscaler):
    source = write_png(tmp_path / "in" / "done.png", 6000, 3750, noise=True)
    work = plan.plan_photo(source, 6000, 3750, "png", [sizes.DESKTOP], settings(tmp_path))
    target = work.plans[0]
    target.destination.parent.mkdir(parents=True, exist_ok=True)
    target.destination.write_bytes(b"already here")

    ctx = context(tmp_path, fake_upscaler)
    result = execute.run_and_archive(work, ctx)
    assert result.outcome == execute.ALREADY_DONE
    assert target.destination.read_bytes() == b"already here"
    assert (ctx.processing_dir / "originals" / "done.png").exists()


def test_overwrite_re_renders(tmp_path, fake_upscaler):
    source = write_png(tmp_path / "in" / "again.png", 6000, 3750, noise=True)
    work = plan.plan_photo(source, 6000, 3750, "png", [sizes.DESKTOP], settings(tmp_path))
    target = work.plans[0]
    target.destination.parent.mkdir(parents=True, exist_ok=True)
    target.destination.write_bytes(b"stale")

    ctx = context(tmp_path, fake_upscaler)
    ctx.overwrite = True
    result = execute.run_and_archive(work, ctx)
    assert result.outcome == execute.OK
    assert target.destination.read_bytes() != b"stale"


def test_batch_runs_cheapest_first(tmp_path, fake_upscaler):
    """974 of the corpus's 3441 outputs need no upscaler; handing those over
    first maximizes what a Ctrl-C leaves behind."""
    cheap_src = write_png(tmp_path / "in" / "cheap.png", 6000, 3750, noise=True)
    dear_src = write_png(tmp_path / "in" / "dear.png", 700, 1800, noise=True)
    opts = settings(tmp_path)
    works = [
        plan.plan_photo(dear_src, 700, 1800, "png", [sizes.DESKTOP], opts),
        plan.plan_photo(cheap_src, 6000, 3750, "png", [sizes.DESKTOP], opts),
    ]
    order = [w.source.name for w in execute.cheapest_first(works)]
    assert order == ["cheap.png", "dear.png"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_execute.py -v -k "archive or partial or skipped or overwrite or cheapest"`
Expected: FAIL — `AttributeError: module 'paperhanger.execute' has no attribute 'run_and_archive'`

- [ ] **Step 3: Append archival and outcomes to `paperhanger/execute.py`**

```python
OK = "ok"
ALREADY_DONE = "already_done"
REJECTED = "rejected"
PARTIAL = "partial"
FAILED = "failed"
SKIPPED = "skipped"          # not an image; never reaches here


@dataclass
class PhotoResult:
    source: Path
    outcome: str
    written: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    archived_to: Path | None = None


def _next_free_name(directory: Path, source: Path) -> Path:
    counter = 2
    while True:
        candidate = directory / f"{source.stem}-{counter}{source.suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def archive(source: Path, directory: Path, log=print) -> Path:
    """Move `source` into `directory`. NEVER overwrites.

    A second import containing another IMG_0042.jpg would otherwise replace the
    first silently. This is the only path in the design that could destroy a
    user's sole copy of an original.
    """
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / source.name
    if target.exists():
        target = _next_free_name(directory, source)
        log(f"  {source.name}: already in {directory.name}/, archived as {target.name}")
    shutil.move(str(source), str(target))
    return target


def cheapest_first(works):
    """Photos needing no upscaler first, then by ascending upscaler cost."""
    return sorted(works, key=lambda w: (w.needs_upscale, w.upscale_output_pixels))


def run_and_archive(work, ctx) -> PhotoResult:
    """Render one photo's plans, then decide where its original goes."""
    result = PhotoResult(source=work.source, outcome=OK)
    overwrite = getattr(ctx, "overwrite", False)

    todo = []
    for target in work.plans:
        if target.destination.exists() and not overwrite:
            result.skipped.append(target.destination)
        else:
            todo.append(target)

    if todo:
        runnable = plan.PhotoWork(
            source=work.source, width=work.width, height=work.height,
            source_format=work.source_format, plans=todo,
            rejected_devices=list(work.rejected_devices),
        )
        try:
            result.written = run_photo(runnable, ctx)
        except (imaging.ImagingError, OSError) as error:
            result.failures.append(str(error))
        else:
            for target in todo:
                if not target.destination.exists():
                    result.failures.append(f"{target.destination.name} was not written")

    if result.failures:
        # Work is per plan but archival is per photo. Leaving the source where
        # it is keeps the re-run able to find it.
        result.outcome = FAILED if not (result.written or result.skipped) else PARTIAL
        return result

    if work.rejected_everywhere:
        result.outcome = REJECTED
        result.archived_to = archive(work.source, ctx.processing_dir / "error", ctx.log)
        return result

    result.outcome = ALREADY_DONE if not result.written else OK
    result.archived_to = archive(work.source, ctx.processing_dir / "originals", ctx.log)
    return result
```

Add `from . import plan` to the module imports, and change `run_photo` to raise on the first plan failure so `run_and_archive` sees it (it already propagates from `render`).

- [ ] **Step 4: Make `run_photo` collect rather than abort on the first failure**

`run_and_archive` needs to know which plans failed while still writing the rest, so `run_photo` gains a `collect_failures` list parameter:

```python
def run_photo(work, ctx, failures=None) -> list:
    ...
        for target in work.plans:
            try:
                ... existing per-plan branches ...
            except (imaging.ImagingError, OSError) as error:
                if failures is None:
                    raise
                failures.append(f"{target.destination.name}: {error}")
```

And `run_and_archive` calls `run_photo(runnable, ctx, failures=result.failures)` instead of wrapping it in a bare try.

- [ ] **Step 5: Run the whole execute test file**

Run: `uv run pytest tests/test_execute.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add paperhanger/execute.py tests/test_execute.py
git commit -m "feat: archival with no-overwrite, partial-failure outcome, cheapest-first"
```

---

### Task 11: The dry-run report and time estimate

**Goal:** Render the plan list as the report in spec §11, including a time estimate, so a multi-hour batch can be inspected before it is committed to.

**Files:**
- Create: `paperhanger/report.py`
- Test: `tests/test_report.py`

**Acceptance Criteria:**
- [ ] `estimate_seconds(works)` uses 0.8 s per megapixel of upscaler **output** and counts each photo **once**, not each plan
- [ ] The estimate for a photo needing no upscaler is 0
- [ ] The header counts images, rejections, already-done photos, and the estimate
- [ ] Each photo renders one line; crop photos render an indented line per slice
- [ ] A rejected photo renders `reject (too small for both)` when both devices rejected it, and names the device when only one did
- [ ] An already-done photo renders `already done, skipping`
- [ ] Band numbers are **not** printed (the spec's example annotates them for the reader only)
- [ ] `render_report` returns a string and writes nothing

**Verify:** `uv run pytest tests/test_report.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report.py
from pathlib import Path

from paperhanger import formats, plan, report, sizes


def settings(tmp_path):
    return plan.OutputSettings(processing_dir=tmp_path / "processing", fmt="heic",
                               quality=formats.quality_for("heic"))


def test_estimate_counts_each_photo_once(tmp_path):
    """Three crop plans from one photo cost ONE whole-frame enlargement."""
    work = plan.plan_photo(Path("/s/sunset.jpg"), 700, 1800, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    assert len(work.plans) == 3
    expected = 0.8 * 16 * 700 * 1800 / 1_000_000
    assert report.estimate_seconds([work]) == expected


def test_estimate_is_zero_without_upscaling(tmp_path):
    work = plan.plan_photo(Path("/s/big.jpg"), 9216, 6144, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    assert report.estimate_seconds([work]) == 0


def test_header_counts(tmp_path):
    opts = settings(tmp_path)
    works = [
        plan.plan_photo(Path("/s/a.jpg"), 9216, 6144, "jpeg", [sizes.DESKTOP], opts),
        plan.plan_photo(Path("/s/tiny.jpg"), 200, 300, "jpeg", list(sizes.DEVICES), opts),
    ]
    text = report.render_report(works, already_done=3, non_images=1)
    assert "2 images" in text
    assert "1 rejected" in text
    assert "3 already done" in text
    assert "1 non-image skipped" in text


def test_whole_image_line(tmp_path):
    work = plan.plan_photo(Path("/s/cliffs.jpg"), 8200, 5125, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    line = report.render_photo(work)
    assert "cliffs.jpg" in line
    assert "desktop/width" in line
    assert "8200 -> 7680" in line
    assert "downscale" in line
    assert "[1]" not in line, "band numbers are for the spec's reader, not the report"


def test_crop_lines_are_indented(tmp_path):
    work = plan.plan_photo(Path("/s/sunset.jpg"), 3000, 5000, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    text = report.render_photo(work)
    lines = text.splitlines()
    assert "crop 3x horizontal" in lines[0]
    assert len(lines) == 4
    for line, position in zip(lines[1:], ("top", "middle", "bottom")):
        assert line.startswith("   ")
        assert position in line
        assert "7680x4800" in line


def test_rejected_on_both_devices(tmp_path):
    work = plan.plan_photo(Path("/s/tiny.gif"), 200, 300, "gif",
                           list(sizes.DEVICES), settings(tmp_path))
    assert "reject (too small for both)" in report.render_photo(work)


def test_rejected_on_one_device_only(tmp_path):
    work = plan.plan_photo(Path("/s/small.jpg"), 900, 1400, "jpeg",
                           list(sizes.DEVICES), settings(tmp_path))
    line = report.render_photo(work)
    assert "desktop rejected" in line
    assert "phone" in line


def test_already_done_line(tmp_path):
    work = plan.plan_photo(Path("/s/done.jpg"), 9216, 6144, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    assert "already done, skipping" in report.render_photo(work, already_done=True)


def test_format_duration():
    assert report.format_duration(45) == "~45 s"
    assert report.format_duration(14 * 60) == "~14 min"
    assert report.format_duration(24 * 3600) == "~24.0 h"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paperhanger.report'`

- [ ] **Step 3: Write `paperhanger/report.py`**

```python
"""Rendering the plan for a human. Reads the same structures the executor runs.

The dry-run report and the executor consume one list of plans, so the report
cannot drift from what actually happens -- the usual failure mode of a
bolted-on dry-run flag.
"""

from . import bands, sizes

# Measured across a 33x range of image sizes: upscayl's wall clock is about
# 0.8 s per megapixel of OUTPUT, which is 16x the source pixels at 4x.
SECONDS_PER_OUTPUT_MEGAPIXEL = 0.8


def estimate_seconds(works) -> float:
    """Total upscaler time. Per PHOTO, because the frame is enlarged once."""
    total = 0.0
    for work in works:
        if work.needs_upscale:
            total += SECONDS_PER_OUTPUT_MEGAPIXEL * work.upscale_output_pixels / 1_000_000
    return total


def format_duration(seconds: float) -> str:
    if seconds < 90:
        return f"~{seconds:.0f} s"
    if seconds < 3600:
        return f"~{seconds / 60:.0f} min"
    return f"~{seconds / 3600:.1f} h"


def _action(target) -> str:
    if target.band == bands.DOWNSCALE:
        return "downscale"
    if target.band == bands.NATIVE:
        return "native"
    if target.band == bands.UPSCALE_REDUCE:
        return f"4x -> {target.out_width}x{target.out_height}"
    return f"4x only -> {target.out_width}x{target.out_height}  below_target"


def render_photo(work, already_done: bool = False) -> str:
    name = work.source.name

    if already_done:
        return f" {name:<34} already done, skipping"

    if work.rejected_everywhere:
        return f" {name:<34} reject (too small for both)"

    lines = []
    note = ""
    if work.rejected_devices:
        note = f"   ({', '.join(work.rejected_devices)} rejected)"

    crops = [p for p in work.plans if p.crop is not None]
    whole = [p for p in work.plans if p.crop is None]

    for target in whole:
        governing = target.governing
        lines.append(
            f" {name:<34} {target.target.name:<15} "
            f"{governing} -> {target.out_width}x{target.out_height}  {_action(target)}"
            f"{note}"
        )
        note = ""

    by_device = {}
    for target in crops:
        by_device.setdefault(target.device, []).append(target)
    for device, targets in by_device.items():
        orientation = "horizontal" if device == sizes.DESKTOP else "vertical"
        lines.append(f" {name:<34} {device:<15} crop 3x {orientation}{note}")
        note = ""
        for target in targets:
            lines.append(
                f"   {target.position:<12} {target.governing} -> "
                f"{target.out_width}x{target.out_height}  {_action(target)}"
            )
    return "\n".join(lines)


def render_report(works, already_done: int = 0, non_images: int = 0) -> str:
    rejected = sum(1 for w in works if w.rejected_everywhere)
    outputs = sum(len(w.plans) for w in works)
    parts = [
        f"{len(works)} images, {outputs} outputs, {rejected} rejected, "
        f"{already_done} already done, {non_images} non-image skipped, "
        f"{format_duration(estimate_seconds(works))}",
        "",
    ]
    for work in works:
        rendered = render_photo(work)
        if rendered:
            parts.append(rendered)
    return "\n".join(parts)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_report.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add paperhanger/report.py tests/test_report.py
git commit -m "feat: dry-run report reading the same plans the executor runs"
```

---

### Task 12: The CLI

**Goal:** Wire everything together: argument parsing, the `setup`/`doctor` subcommands, the nested-directory guard, scanning, collision detection, dry-run, and the run itself.

**Files:**
- Create: `paperhanger/cli.py`
- Test: `tests/test_cli.py`

**Acceptance Criteria:**
- [ ] `paperhanger <path>` processes; `paperhanger setup` and `paperhanger doctor` dispatch on the first argument
- [ ] `-d`, `-p`, `-b` select devices, with `-b` the default
- [ ] `--overwrite` and `--allow-nested` are **separate flags** (they were one `--force` in an earlier draft, which meant the flag needed to process the default nested layout also disabled skip-existing, so resuming an interrupted run re-upscaled everything)
- [ ] `--format` accepts heic/jpeg/avif/png; `--quality` with png exits nonzero with a clear message
- [ ] The guard refuses a directory that **contains** the processing directory, and the message names `--allow-nested`
- [ ] The guard also refuses when the input **is** the processing directory or `originals/`
- [ ] Collisions abort before any work, listing both sources
- [ ] `--dry-run` prints the report and writes nothing — asserted by checking the processing tree does not exist afterward
- [ ] A run that needs the upscaler calls `toolchain.ensure_ready()` **before** planning, so a missing binary fails in about a second
- [ ] Exit codes: 0 success, 1 usage/guard/collision error, 2 some photos failed
- [ ] Non-image files are counted and reported, never passed to the planner

**Verify:** `uv run pytest tests/test_cli.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
from pathlib import Path

import pytest

from paperhanger import cli
from tests.pngwriter import write_png


def run(argv, capsys):
    code = cli.main(argv)
    return code, capsys.readouterr().out


def test_dry_run_writes_nothing(tmp_path, capsys):
    inbox = tmp_path / "in"
    inbox.mkdir()
    write_png(inbox / "a.png", 9216, 6144)
    processing = tmp_path / "processing"
    code, out = run(["--dry-run", "--processing-dir", str(processing), str(inbox)], capsys)
    assert code == 0
    assert "1 images" in out
    assert not processing.exists(), "--dry-run must not create the processing tree"


def test_non_images_are_counted_not_planned(tmp_path, capsys):
    inbox = tmp_path / "in"
    inbox.mkdir()
    write_png(inbox / "a.png", 9216, 6144)
    (inbox / "notes.txt").write_text("not an image")
    code, out = run(["--dry-run", "--processing-dir", str(tmp_path / "p"), str(inbox)], capsys)
    assert code == 0
    assert "1 non-image skipped" in out


def test_guard_refuses_a_parent_of_the_processing_dir(tmp_path, capsys):
    inbox = tmp_path / "pictures"
    inbox.mkdir()
    write_png(inbox / "a.png", 9216, 6144)
    processing = inbox / "processing"
    code, out = run(["--dry-run", "--processing-dir", str(processing), str(inbox)], capsys)
    assert code == 1
    assert "--allow-nested" in out


def test_allow_nested_permits_it(tmp_path, capsys):
    inbox = tmp_path / "pictures"
    inbox.mkdir()
    write_png(inbox / "a.png", 9216, 6144)
    processing = inbox / "processing"
    code, _ = run(["--dry-run", "--allow-nested", "--processing-dir",
                   str(processing), str(inbox)], capsys)
    assert code == 0


def test_guard_refuses_the_processing_dir_itself(tmp_path, capsys):
    processing = tmp_path / "processing"
    processing.mkdir()
    write_png(processing / "a.png", 9216, 6144)
    code, out = run(["--dry-run", "--processing-dir", str(processing), str(processing)], capsys)
    assert code == 1


def test_collision_aborts_and_names_both_sources(tmp_path, capsys):
    inbox = tmp_path / "in"
    inbox.mkdir()
    write_png(inbox / "city.png", 1920, 1046)
    seed = write_png(tmp_path / "seed.png", 1920, 1046)
    from paperhanger import imaging
    imaging.resize_and_encode(seed, 1920, 1046, "jpeg", 90, inbox / "city.jpg", resize=False)
    code, out = run(["--dry-run", "--processing-dir", str(tmp_path / "p"), str(inbox)], capsys)
    assert code == 1
    assert "city.jpg" in out and "city.png" in out


def test_quality_with_png_is_rejected(tmp_path, capsys):
    inbox = tmp_path / "in"
    inbox.mkdir()
    write_png(inbox / "a.png", 9216, 6144)
    code, out = run(["--format", "png", "--quality", "90", "--dry-run",
                     "--processing-dir", str(tmp_path / "p"), str(inbox)], capsys)
    assert code == 1
    assert "lossless" in out


def test_device_flags(tmp_path, capsys):
    inbox = tmp_path / "in"
    inbox.mkdir()
    write_png(inbox / "a.png", 9216, 6144)
    _, desktop_only = run(["-d", "--dry-run", "--processing-dir",
                           str(tmp_path / "p"), str(inbox)], capsys)
    assert "desktop" in desktop_only and "phone" not in desktop_only


def test_doctor_reports_without_failing(capsys):
    code, out = run(["doctor"], capsys)
    assert code == 0
    assert "upscayl" in out.lower()


def test_missing_path_is_a_usage_error(capsys):
    code, out = run([], capsys)
    assert code == 1


def test_end_to_end_run(tmp_path, fake_upscaler, capsys):
    inbox = tmp_path / "in"
    inbox.mkdir()
    write_png(inbox / "cliffs.png", 9216, 6144, noise=True)
    processing = tmp_path / "processing"
    code, out = run(["-d", "--format", "png", "--processing-dir",
                     str(processing), str(inbox)], capsys)
    assert code == 0
    written = list((processing / "to_sort_desktop").glob("*.png"))
    assert len(written) == 1
    assert (processing / "originals" / "cliffs.png").exists()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paperhanger.cli'`

- [ ] **Step 3: Write `paperhanger/cli.py`**

```python
"""Argument parsing and the run loop."""

import argparse
import sys
from pathlib import Path

from . import execute, formats, imaging, plan, report, sizes, toolchain

DEFAULT_PROCESSING_DIR = Path.home() / "Pictures" / "wallpaper" / "processing"
SUBCOMMANDS = ("setup", "doctor")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paperhanger",
        description="Turn a folder of images into desktop and phone wallpapers.",
    )
    devices = parser.add_mutually_exclusive_group()
    devices.add_argument("-d", dest="devices", action="store_const",
                         const=[sizes.DESKTOP], help="desktop only")
    devices.add_argument("-p", dest="devices", action="store_const",
                         const=[sizes.PHONE], help="phone only")
    devices.add_argument("-b", dest="devices", action="store_const",
                         const=list(sizes.DEVICES), help="both (default)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would happen and exit")
    parser.add_argument("--format", default="heic", choices=formats.FORMATS)
    parser.add_argument("--quality", type=int, default=None,
                        help="0-100; not valid for png")
    parser.add_argument("--overwrite", action="store_true",
                        help="regenerate outputs that already exist")
    parser.add_argument("--allow-nested", action="store_true",
                        help="permit an input directory containing the processing dir")
    parser.add_argument("--processing-dir", type=Path, default=DEFAULT_PROCESSING_DIR)
    parser.add_argument("path", nargs="?", type=Path)
    return parser


def check_guard(input_path: Path, processing_dir: Path, allow_nested: bool):
    """Refuse to walk into our own output. Originals are MOVED, not copied."""
    if allow_nested or not input_path.is_dir():
        return None
    inp, proc = input_path.resolve(), processing_dir.resolve()
    if proc == inp or proc.is_relative_to(inp) or inp.is_relative_to(proc):
        return (
            f"{input_path} contains or is inside the processing directory {processing_dir}.\n"
            f"Originals are moved, not copied, so this would relocate your source images.\n"
            f"Pass --allow-nested if that is what you intend."
        )
    return None


def scan(path: Path):
    """(probed images, non-image count). Non-recursive, like the legacy glob."""
    candidates = sorted(p for p in path.iterdir() if p.is_file()) if path.is_dir() else [path]
    images, non_images = [], 0
    for candidate in candidates:
        probed = imaging.probe(candidate)
        if probed is None:
            non_images += 1
            continue
        images.append((candidate, *probed))
    return images, non_images


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] in SUBCOMMANDS:
        return _subcommand(argv[0])

    args = build_parser().parse_args(argv)
    if args.path is None:
        print("error: a file or directory is required", file=sys.stdout)
        return 1
    if not args.path.exists():
        print(f"error: {args.path} does not exist")
        return 1

    try:
        quality = formats.quality_for(args.format, args.quality)
    except ValueError as error:
        print(f"error: {error}")
        return 1

    complaint = check_guard(args.path, args.processing_dir, args.allow_nested)
    if complaint:
        print(f"error: {complaint}")
        return 1

    devices = args.devices or list(sizes.DEVICES)
    opts = plan.OutputSettings(args.processing_dir, args.format, quality)

    images, non_images = scan(args.path)
    works = [plan.plan_photo(path, w, h, fmt, devices, opts) for path, w, h, fmt in images]

    collisions = plan.find_collisions(works)
    if collisions:
        print("error: two sources would write the same output:")
        for destination, sources in collisions.items():
            names = ", ".join(s.name for s in sources)
            print(f"  {destination.name}  <-  {names}")
        print("Rename one of them and run again.")
        return 1

    already = sum(1 for w in works
                  if w.plans and all(p.destination.exists() for p in w.plans)
                  and not args.overwrite)

    if args.dry_run:
        print(report.render_report(works, already_done=already, non_images=non_images))
        return 0

    if any(w.needs_upscale for w in works):
        try:
            binary, models = toolchain.ensure_ready()
        except toolchain.ToolchainError as error:
            print(f"error: {error}")
            return 1
    else:
        binary, models = None, None

    ctx = execute.Context(processing_dir=args.processing_dir,
                          workroot=args.processing_dir / ".work",
                          upscayl=binary, models_dir=models)
    ctx.overwrite = args.overwrite
    swept = execute.sweep_partials(args.processing_dir)
    if swept:
        print(f"swept {swept} stray .partial file(s) from an interrupted run")

    print(report.render_report(works, already_done=already, non_images=non_images))
    print()

    failures = []
    for index, work in enumerate(execute.cheapest_first(works), start=1):
        result = execute.run_and_archive(work, ctx)
        print(f"[{index}/{len(works)}] {work.source.name}: {result.outcome}"
              f" ({len(result.written)} written)")
        if result.outcome in (execute.PARTIAL, execute.FAILED):
            failures.append(result)

    if failures:
        print("\nleft in place for a re-run:")
        for result in failures:
            print(f"  {result.source}")
            for message in result.failures:
                print(f"    {message}")
        return 2
    return 0


def _subcommand(name: str) -> int:
    if name == "setup":
        try:
            toolchain.setup()
        except toolchain.ToolchainError as error:
            print(f"error: {error}")
            return 1
        print("toolchain ready")
        return 0

    state = toolchain.status()
    print(f"upscayl-bin : {state['binary'] or 'NOT FOUND — run `paperhanger setup`'}")
    print(f"release     : {state['release']}")
    print(f"model       : {state['model']} "
          f"({'ok' if state['models_ok'] else 'missing or corrupt'})")
    print(f"models dir  : {state['models_dir']}")
    print(f"sips        : {imaging.SIPS}")
    return 0
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all pass

- [ ] **Step 5: Run the whole Tier 1 suite**

Run: `uv run pytest -v`
Expected: all pass, nothing skipped except the corpus-dependent cases when the corpus is absent

- [ ] **Step 6: Commit**

```bash
git add paperhanger/cli.py tests/test_cli.py
git commit -m "feat: CLI with guard, collision detection, dry-run and progress"
```

---

### Task 13: Tier 2 — the 27-image corpus sample

**Goal:** Run the 27 real corpus images through the real `sips` with the upscaler stubbed. This is where most of the value sits: format detection, colour conversion, crop geometry, naming and filing, against genuinely messy input, in seconds.

**Files:**
- Create: `tests/test_corpus_sample.py`
- Modify: `tests/conftest.py` (add the sample-copying fixture)

**Acceptance Criteria:**
- [ ] The test reads `tests/corpus_sample.txt`, copies those files to a tmp dir, and **skips cleanly** when the corpus is absent — never fails on another machine
- [ ] It never writes to `~/Pictures/wallpaper` and always passes `--processing-dir`
- [ ] All 27 images are admitted by `probe`; a named assertion covers the WebP-with-`.jpg`-extension file reporting `webp`
- [ ] Every produced file's **actual** dimensions equal the dimensions in its own filename — the check that keeps the no-drift claim honest
- [ ] Every produced file's factor token matches its band (`native` for bands 1 and 2, `4x` for band 4)
- [ ] The run produces 102 outputs and exercises all five bands; a missing band fails the test with which one
- [ ] Every source ends in `originals/` or `error/`, none left behind
- [ ] The ProPhoto RGB source produces an output tagged sRGB
- [ ] No `.partial` files remain

**Verify:** `uv run pytest tests/test_corpus_sample.py -v` → passes, or skips with "corpus not present"

**Steps:**

- [ ] **Step 1: Add the sample fixture to `tests/conftest.py`**

```python
SAMPLE_MANIFEST = Path(__file__).parent / "corpus_sample.txt"


@pytest.fixture
def corpus_sample(corpus, tmp_path):
    """The 27 coverage images, copied out. The corpus is never written to."""
    import shutil

    names = [line.strip() for line in SAMPLE_MANIFEST.read_text().splitlines()
             if line.strip() and not line.startswith("#")]
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    missing = []
    for name in names:
        source = corpus / name
        if not source.exists():
            missing.append(name)
            continue
        shutil.copy2(source, inbox / name)
    if missing:
        pytest.skip(f"{len(missing)} sample image(s) missing from the corpus: "
                    f"{', '.join(missing[:3])}")
    return inbox
```

- [ ] **Step 2: Write the test**

```python
# tests/test_corpus_sample.py
"""Tier 2: the 27-image coverage sample, real sips, stubbed upscaler.

The sample covers all 36 coverage tags the corpus contains: every routing
branch paired with every band it reaches, every input format, every colour
profile class. Chosen once and recorded in tests/corpus_sample.txt; the images
themselves stay out of the repo.
"""

import re
import subprocess
from pathlib import Path

import pytest

from paperhanger import bands, cli, execute, formats, imaging, plan, sizes

pytestmark = pytest.mark.corpus

NAME = re.compile(r"_(?P<w>\d+)x(?P<h>\d+)_(?P<factor>native|[\d.]+x)\.[a-z]+$")


def _profile(path):
    proc = subprocess.run(["/usr/bin/sips", "-g", "profile", str(path)],
                          capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        if "profile:" in line:
            return line.split("profile:", 1)[1].strip()
    return None


def _plan_everything(inbox, processing):
    opts = plan.OutputSettings(processing, "heic", formats.quality_for("heic"))
    images, non_images = cli.scan(inbox)
    works = [plan.plan_photo(p, w, h, f, list(sizes.DEVICES), opts)
             for p, w, h, f in images]
    return works, non_images


def test_every_sample_image_is_admitted(corpus_sample):
    images, non_images = cli.scan(corpus_sample)
    assert non_images == 0
    assert len(images) == 27


def test_the_lying_extension_reports_its_real_format(corpus_sample):
    """snowy_forest_landscape_9522.jpg is really a WebP. If normalize-or-not
    were decided from the suffix, this file would take the wrong path."""
    target = corpus_sample / "snowy_forest_landscape_9522.jpg"
    assert imaging.probe(target)[2] == "webp"


def test_the_sample_covers_every_band(corpus_sample, tmp_path):
    works, _ = _plan_everything(corpus_sample, tmp_path / "processing")
    seen = {p.band for w in works for p in w.plans}
    seen |= {bands.REJECT} if any(w.rejected_devices for w in works) else set()
    missing = {bands.DOWNSCALE, bands.NATIVE, bands.UPSCALE_REDUCE,
               bands.UPSCALE_ONLY, bands.REJECT} - seen
    assert not missing, f"the sample no longer covers bands: {sorted(missing)}"


def test_the_sample_produces_the_expected_output_count(corpus_sample, tmp_path):
    works, _ = _plan_everything(corpus_sample, tmp_path / "processing")
    assert sum(len(w.plans) for w in works) == 102


def test_full_run_over_the_sample(corpus_sample, tmp_path, fake_upscaler):
    processing = tmp_path / "processing"
    exit_code = cli.main([
        "--format", "heic", "--processing-dir", str(processing), str(corpus_sample),
    ])
    assert exit_code == 0

    produced = [p for p in processing.rglob("*.heic") if p.is_file()]
    assert len(produced) == 102

    # every output's filename tells the truth about its own pixels
    for path in produced:
        match = NAME.search(path.name)
        assert match, f"unparseable output name: {path.name}"
        expected = (int(match["w"]), int(match["h"]))
        assert imaging.probe(path)[:2] == expected, path.name

    # nothing left staged, nothing left in the inbox
    assert list(processing.rglob("*.partial")) == []
    assert list(corpus_sample.iterdir()) == []
    archived = len(list((processing / "originals").iterdir()))
    errored = len(list((processing / "error").iterdir())) \
        if (processing / "error").exists() else 0
    assert archived + errored == 27


def test_below_target_holds_both_kinds(corpus_sample, tmp_path, fake_upscaler):
    """The folder mixes untouched originals with enlarged-and-still-short
    files. The factor token is what separates them."""
    processing = tmp_path / "processing"
    assert cli.main(["--format", "heic", "--processing-dir", str(processing),
                     str(corpus_sample)]) == 0
    below = [p for p in processing.rglob("below_target/*.heic")]
    assert below
    tokens = {NAME.search(p.name)["factor"] for p in below}
    assert "native" in tokens
    assert any(t.endswith("x") for t in tokens)


def test_wide_gamut_source_produces_srgb_output(corpus_sample, tmp_path, fake_upscaler):
    processing = tmp_path / "processing"
    assert cli.main(["-p", "--format", "png", "--processing-dir", str(processing),
                     str(corpus_sample)]) == 0
    outputs = [p for p in processing.rglob("moss_with_pine_needles_5324*.png")]
    assert outputs, "the ProPhoto RGB sample produced no phone output"
    for path in outputs:
        assert "sRGB" in (_profile(path) or ""), f"{path.name} kept a non-sRGB profile"
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_corpus_sample.py -v`
Expected: all pass on the author's machine; all skip elsewhere with "corpus not present at …"

- [ ] **Step 4: If the output count is not 102, reconcile before changing the number**

The 102 was computed from the corpus under the final band rules. A different number means either the sample changed or a band boundary drifted. Print the breakdown and compare against spec §15 before touching the assertion:

```bash
uv run python -c "
from pathlib import Path
from paperhanger import cli, formats, plan, sizes
inbox = Path.home()/'Pictures'/'wallpaper'
names = [l.strip() for l in open('tests/corpus_sample.txt') if l.strip()]
opts = plan.OutputSettings(Path('/tmp/x'), 'heic', formats.quality_for('heic'))
from collections import Counter
counts = Counter()
for n in names:
    probed = __import__('paperhanger.imaging', fromlist=['probe']).probe(inbox/n)
    w = plan.plan_photo(inbox/n, *probed, list(sizes.DEVICES), opts)
    for p in w.plans: counts[p.band] += 1
print(counts, 'total', sum(counts.values()))
"
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_corpus_sample.py tests/conftest.py
git commit -m "test: tier 2 coverage sample over 27 real corpus images"
```

---

### Task 14: Tier 3 — the real-upscaler gate

**Goal:** Confirm against the real `upscayl-bin` that the sample runs clean end to end, and that a slice cut from a whole-frame 4x upscale matches the same slice upscaled directly at PSNR ≥ 40 dB — the equivalence that spec §7's per-photo upscaling depends on and that has never been measured.

> **USER-ORDERED GATE — NON-SKIPPABLE.** This task was requested by the user in the current conversation. It MUST NOT be closed by walking around it, by declaring it "verified inline", or by substituting a cheaper check. Close only after every item in `acceptanceCriteria` has been re-validated independently, with output captured.

**Files:**
- Create: `tests/test_real_upscaler.py`
- Modify: `docs/superpowers/specs/2026-09-11-paperhanger-design.md` (only if the gate fails — record the revert)

**Acceptance Criteria:**
- [ ] `paperhanger setup` completes and `paperhanger doctor` reports the binary found and the model `ok`
- [ ] The **whole-then-cut** path is run against the real binary and its output is captured: a photo is upscaled whole, then a slice is cut from the 4x frame
- [ ] The **cut-then-upscale** path is run against the real binary and its output is captured: the same slice is cut from the source, then upscaled
- [ ] The two results have identical dimensions, and PSNR between them is **≥ 40 dB**, with the measured number printed
- [ ] If PSNR is between 35 and 40 dB, the task stops and reports it rather than passing or failing silently — that band is the user's judgment call
- [ ] The 27-image sample completes with the real binary, exit code 0, in the expected ballpark of 25 minutes, with the wall-clock time captured
- [ ] Every produced file's actual dimensions equal the dimensions in its filename, re-checked with the real upscaler in play
- [ ] If the gate fails, spec §7 is reverted to per-plan upscaling and §15's estimate returns to 37 hours, and the revert is committed before the task closes

**Verify:** `uv run pytest tests/test_real_upscaler.py -v -m real_upscaler` → all pass, with the PSNR and wall-clock values in the captured output

**Steps:**

- [ ] **Step 1: Install the toolchain and confirm it**

```bash
uv run paperhanger setup
uv run paperhanger doctor
```

Expected: `doctor` prints a real path for `upscayl-bin`, the pinned release `20251207-174704`, and `upscayl-standard-4x (ok)`. Capture this output — the gate's first criterion is this, not an assumption that setup worked.

- [ ] **Step 2: Write the equivalence test**

```python
# tests/test_real_upscaler.py
"""Tier 3: the real binary. Marked, deselected by default.

The equivalence test here is the gate on spec section 7's per-photo upscaling,
which is the largest single saving in the design: 2467 upscaler runs become 756
and 37.2 hours become 24.3. The claim is that enlarging then cutting equals
cutting then enlarging -- true if the model carries no whole-image context,
which is how this architecture works, but argued rather than measured until
this test runs.
"""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from paperhanger import cli, geometry, imaging, toolchain
from tests.pngwriter import write_png

pytestmark = pytest.mark.real_upscaler

PSNR_PASS = 40.0
PSNR_JUDGMENT_FLOOR = 35.0


@pytest.fixture
def real_toolchain(monkeypatch):
    monkeypatch.delenv("PAPERHANGER_UPSCAYL_BIN", raising=False)
    try:
        return toolchain.ensure_ready()
    except toolchain.ToolchainError as error:
        pytest.fail(f"the gate needs the real toolchain: {error}")


def _psnr(left: Path, right: Path) -> float:
    """PSNR via ImageMagick. Test-only; magick is not a runtime dependency."""
    if shutil.which("magick") is None:
        pytest.fail("the gate needs `magick` to measure PSNR; brew install imagemagick")
    proc = subprocess.run(["magick", "compare", "-metric", "PSNR",
                           str(left), str(right), "null:"],
                          capture_output=True, text=True)
    text = (proc.stderr or proc.stdout).strip().split()[0]
    if text.lower().startswith("inf"):
        return float("inf")
    return float(text)


def test_toolchain_is_the_pinned_one(real_toolchain):
    binary, models = real_toolchain
    assert binary.is_file()
    assert toolchain.models_ok(), "model files missing or failing their SHA-256"


def test_whole_frame_equivalence(tmp_path, real_toolchain, capsys):
    """THE GATE. Both arms run against the real binary; both are captured."""
    binary, models = real_toolchain
    source = write_png(tmp_path / "source.png", 700, 1800, noise=True)
    rect = geometry.horizontal_thirds(700, 1800)[1]          # the middle slice

    # --- arm A: whole-then-cut (what the design does) ---
    frame = tmp_path / "frame_4x.png"
    imaging.upscale(source, frame, binary, models)
    arm_a = tmp_path / "whole_then_cut.png"
    imaging.crop(frame, rect.scaled(4), arm_a)
    print(f"whole-then-cut: {imaging.probe(arm_a)[:2]} from a {imaging.probe(frame)[:2]} frame")

    # --- arm B: cut-then-upscale (what the legacy script did) ---
    slice_source = tmp_path / "slice.png"
    imaging.crop(source, rect, slice_source)
    arm_b = tmp_path / "cut_then_upscale.png"
    imaging.upscale(slice_source, arm_b, binary, models)
    print(f"cut-then-upscale: {imaging.probe(arm_b)[:2]}")

    assert imaging.probe(arm_a)[:2] == imaging.probe(arm_b)[:2], \
        "the two arms produced different dimensions; equivalence is false"

    psnr = _psnr(arm_a, arm_b)
    print(f"PSNR whole-then-cut vs cut-then-upscale: {psnr:.2f} dB "
          f"(pass >= {PSNR_PASS})")

    if psnr < PSNR_JUDGMENT_FLOOR:
        pytest.fail(
            f"equivalence FAILS at {psnr:.2f} dB. Revert spec section 7 to per-plan "
            f"upscaling and restore the 37-hour estimate in sections 3, 7 and 15."
        )
    if psnr < PSNR_PASS:
        pytest.fail(
            f"equivalence is {psnr:.2f} dB, between the {PSNR_JUDGMENT_FLOOR} and "
            f"{PSNR_PASS} dB bounds. This is the user's judgment call, not the "
            f"agent's: stop and ask before either keeping or reverting section 7."
        )


def test_sample_runs_with_the_real_binary(corpus_sample, tmp_path, real_toolchain, capsys):
    processing = tmp_path / "processing"
    started = time.monotonic()
    exit_code = cli.main(["--format", "heic", "--processing-dir", str(processing),
                          str(corpus_sample)])
    elapsed = time.monotonic() - started
    print(f"27-image sample with the real upscaler: {elapsed / 60:.1f} min")
    assert exit_code == 0

    produced = [p for p in processing.rglob("*.heic") if p.is_file()]
    assert len(produced) == 102

    import re
    pattern = re.compile(r"_(?P<w>\d+)x(?P<h>\d+)_(?:native|[\d.]+x)\.heic$")
    for path in produced:
        match = pattern.search(path.name)
        assert match, path.name
        assert imaging.probe(path)[:2] == (int(match["w"]), int(match["h"])), path.name

    assert list(processing.rglob("*.partial")) == []
```

- [ ] **Step 3: Run the gate**

```bash
uv run pytest tests/test_real_upscaler.py -v -m real_upscaler -s
```

Expected: both tests pass. `-s` matters — the PSNR figure and the wall clock are printed, and the gate's acceptance criteria require them captured, not inferred.

- [ ] **Step 4: Record the measured numbers in the spec**

Replace the "not measured" sentence in spec §7 with the measured result. For example, if the gate passes at 48 dB:

```
This rests on enlarging then cutting equalling cutting then enlarging -- true
because the model carries no whole-image context. Measured at 48 dB PSNR
between the two arms on a 700x1800 source (tests/test_real_upscaler.py).
```

And update §13's gate bullet from "currently argued from the model's architecture rather than measured" to name the measured value.

- [ ] **Step 5: If the gate FAILED, revert section 7 before closing**

Do not leave the spec claiming a saving that does not exist:
- §7: replace the per-photo upscale with per-plan upscaling, and delete the 756/24.3 figures
- §3 and §15: restore 2467 upscaler runs and ~37 hours
- §7: restore the 300 Mpx note as unnecessary (per-plan upscaling never builds a whole frame)
- `execute.py`: set `UPSCALE_PIXEL_CAP = 0` so every photo takes the per-plan path, and say why in a comment referencing the failed gate

```bash
git add -A docs/superpowers/specs/ paperhanger/execute.py
git commit -m "revert: per-plan upscaling after the equivalence gate failed at <N> dB"
```

- [ ] **Step 6: Commit the gate**

```bash
git add tests/test_real_upscaler.py docs/superpowers/specs/
git commit -m "test: tier 3 gate — real upscaler and whole-frame equivalence"
```

```json:metadata
{"files": ["tests/test_real_upscaler.py", "docs/superpowers/specs/2026-09-11-paperhanger-design.md"], "verifyCommand": "uv run paperhanger setup && uv run paperhanger doctor && uv run pytest tests/test_real_upscaler.py -v -m real_upscaler -s", "acceptanceCriteria": ["paperhanger doctor reports the binary found and the model ok", "the whole-then-cut arm runs against the real binary and its output is captured", "the cut-then-upscale arm runs against the real binary and its output is captured", "both arms produce identical dimensions", "PSNR between the arms is >= 40 dB and the measured number is printed", "a PSNR between 35 and 40 dB stops and asks the user rather than deciding", "the 27-image sample completes with the real binary at exit code 0 with wall-clock captured", "every output filename's dimensions equal the file's actual dimensions", "if the gate fails, spec section 7 is reverted and committed before closing"], "modelTier": "standard", "userGate": true, "tags": ["user-gate", "tier-3", "verification"], "requiresUserSpecification": false, "requireEvidenceTokens": [["whole-then-cut", "whole_frame", "upscale-first"], ["cut-then-upscale", "per_slice", "crop-first"]], "gateScope": "task", "failurePolicy": "stop-and-report"}
```

---

### Task 15: README and the alias mapping

**Goal:** Document installation, the commands, the processing layout, and the legacy alias mapping, so the tool is usable by someone who has not read the spec.

**Files:**
- Modify: `README.md`

**Acceptance Criteria:**
- [ ] Install and first-run instructions: `uv tool install .` then `paperhanger setup`
- [ ] Every flag documented, with `--overwrite` and `--allow-nested` distinguished
- [ ] The processing directory layout, including what `below_target/` means and why it holds two kinds of file
- [ ] The filename format explained, including what the factor token tells you at sorting time
- [ ] Output format table with the quality defaults and one line on why they differ per encoder
- [ ] The four testing tiers, stating that the full-corpus run is the author's acceptance pass and not a gate
- [ ] The legacy alias mapping (`mkw` → `paperhanger -b`, `mkwdesk` → `-d`, `mkwphone` → `-p`), noting the dotfiles edit is the author's to make
- [ ] A warning that originals are **moved**, and what the nesting guard protects against
- [ ] Links to the spec and the research document
- [ ] `Status` section updated: no longer "design not started"

**Verify:** `uv run paperhanger --help` matches the flags documented in README.md; every path named in README.md exists

**Steps:**

- [ ] **Step 1: Read the current README and the spec sections being summarized**

```bash
cat README.md
sed -n '/^## 8/,/^## 10/p' docs/superpowers/specs/2026-09-11-paperhanger-design.md
sed -n '/^## 11/,/^## 12/p' docs/superpowers/specs/2026-09-11-paperhanger-design.md
sed -n '/^## 13/,/^## 14/p' docs/superpowers/specs/2026-09-11-paperhanger-design.md
```

- [ ] **Step 2: Rewrite README.md**

Keep the existing opening paragraph and the Lineage and License sections verbatim — they are still accurate. Replace the `Status` section and insert Usage, Output, and Testing sections between the opening and Lineage. Structure:

```markdown
# paperhanger

[existing opening paragraph, unchanged]

## Install

    uv tool install .
    paperhanger setup     # downloads and verifies upscayl-bin + the model, ~60 MB
    paperhanger doctor    # confirms what was found

## Use

    paperhanger [-d | -p | -b] [--dry-run] [--format FMT] [--quality N]
                [--overwrite] [--allow-nested] [--processing-dir DIR] <file-or-dir>

[flag table]

**Originals are moved, not copied.** [the guard, and why it exists]

## Output

[layout tree, below_target/ explanation, filename format with the factor token,
 format/quality table]

## Testing

[the four tiers, and that tier 4 is the author's acceptance pass]

## Lineage

[existing, unchanged, plus the alias mapping]

## License

[existing, unchanged]
```

- [ ] **Step 3: Check the documented flags against the real parser**

```bash
uv run paperhanger --help
```

Expected: every flag in the README table appears, with the same names. Fix either side until they agree.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README covering install, usage, output layout and test tiers"
```

---

## Self-Review

**Spec coverage** — every section of `2026-09-11-paperhanger-design.md` maps to a task:

| Spec section | Task |
|---|---|
| §2 Implementation (module layout) | 0, and each module's own task |
| §3 Architecture: plan then execute | 4 (plans), 8-10 (executor), 11 (report) |
| §4 Classification + §4.1 corrections | 1 |
| §4.2 Phone targets | 1 (values), spec-side reasoning needs no code |
| §5 Bands | 2 |
| §6 Cropping | 3 |
| §7 Execution, all three sips constraints | 6 (the constraints), 8 (render), 9 (per-photo upscale) |
| §8 Files and naming | 4 |
| §9 Output formats and quality | 4 (`formats.py`), 6 (writing them) |
| §10 Toolchain | 7 |
| §11 CLI and dry-run | 11, 12 |
| §12 Passes, errors, rejection, guard, collisions | 10 (outcomes, archival), 12 (guard, collisions), 5 (probe) |
| §13 Testing tiers | 0 (fixtures), 13 (tier 2), 14 (tier 3) |
| §14 Out of scope | nothing to build; EXIF deferral is recorded, not implemented |
| §15 Corpus measurements | 13 asserts the 102-output figure the section implies |

Gaps found and closed while reviewing: §9's statement that `--quality` with png is an error had no home until `formats.quality_for` was given that behaviour in Task 4, with a test. §12's collision rule needed a batch-level function rather than a per-photo one, so `find_collisions` takes the whole list.

**Placeholder scan** — no "TBD", no "add error handling", no "similar to Task N". Every code step carries the code. The one place a step describes rather than shows is Task 15 Step 2, where the README's prose is outlined by section; that is documentation content, not code, and the acceptance criteria pin what must appear.

**Type consistency** — checked across tasks:
- `sizes.Target` fields `device / axis / ideal / floor`, used identically in Tasks 1, 2, 4, 11
- `geometry.Rect` fields `x / y / width / height` plus `.scaled(factor)`, used in Tasks 3, 6, 8, 9, 14
- `bands` constants `DOWNSCALE / NATIVE / UPSCALE_REDUCE / UPSCALE_ONLY / REJECT` and the groupings `UPSCALING / BELOW_TARGET / RESIZING`, used in Tasks 2, 4, 8, 11, 13
- `plan.OutputPlan` and `plan.PhotoWork` field names match everywhere they are constructed or read (Tasks 4, 8, 9, 10, 11)
- `imaging` function names `probe / normalize_to_srgb_png / crop / resize_and_encode / upscale` are stable from Task 5 onward
- `execute.Context` fields `processing_dir / workroot / upscayl / models_dir / log`, plus the `overwrite` attribute the CLI sets in Task 12 and Task 10's test reads
- `toolchain.ensure_ready()` returns `(binary, models_dir)` in that order in Tasks 7, 12, 14
