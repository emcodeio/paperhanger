# CoreGraphics Imaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every `sips` invocation in `paperhanger/` with CoreGraphics and ImageIO calls made through Python's standard-library `ctypes`, eliminating the pad-and-shift crop workaround and the seven documented `sips` defects, with byte-identical output proven operation by operation.

**Architecture:** One new module, `paperhanger/_cg.py`, owns every line of `ctypes` and is the only code in the project that can crash the process. `imaging.py` keeps its five public functions and their exact contracts, reimplemented on top of `_cg` one operation at a time. Each swap is gated by a differential test comparing new output against the retained `sips` implementation, byte for byte, over real corpus images.

**Tech Stack:** Python 3.14, `ctypes` (stdlib), CoreFoundation / CoreGraphics / ImageIO system frameworks. No third-party runtime dependencies, unchanged.

**Spec:** `docs/superpowers/specs/2026-09-13-coregraphics-imaging-design.md`

## Global Constraints

1. **`ctypes` appears in exactly one file.** Only `paperhanger/_cg.py` may import `ctypes`. Any other module doing so is a defect.
2. **`_cg` never returns NULL and never passes NULL inward.** Every CoreFoundation or CoreGraphics call whose result can be NULL goes through `_checked()`, which raises `ImagingError` naming the file and the step.
3. **Every owned handle is released.** Every `Create`/`Copy` result goes into a `_Scope` that releases on exit, success or exception. No bare `CFRelease` at a return path.
4. **`imaging.py`'s public signatures do not change.** `probe`, `crop`, `resize_and_encode`, `normalize_to_srgb_png`, `upscale` keep their names, parameters and return types. `execute.py` is not modified by tasks 1-7.
5. **Resampling uses `kCGInterpolationHigh` explicitly**, never Default. Default measured identical but is Apple's to redefine.
6. **Quality numbers do not change.** `sips` quality 80 is `kCGImageDestinationLossyCompressionQuality` 0.80; the existing defaults transfer unchanged. Any task that alters a default value is wrong.
7. **`crop` always writes PNG and refuses a non-`.png` name.** Unchanged contract.
8. **`crop` keeps its bounds check.** `execute.py` crops the 4x frame with `rect.scaled(4)`; an enlargement a pixel short must raise, not produce a black edge.
9. **`upscale` stays a subprocess.** `upscayl-bin` is untouched.
10. **The differential bar is byte-identical PIXELS**, compared as decoded pixel data or the `IDAT` stream. This is not a perceptual threshold — identical pixels is stricter than any PSNR bound.

    File-level identity additionally holds for most sources and was measured on real corpus JPEGs, a grayscale corpus JPEG, and a 300 Mpx frame, all matching by SHA-256. Where whole files differ, the cause is characterized rather than mysterious: ancillary PNG chunks (`cHRM`, `pHYs`, `iTXt`) present in the SOURCE, which `sips` forwards and ImageIO drops. 47 corpus PNGs carry such chunks. A file-level difference is therefore explained by naming the chunks, never waved through as noise. A pixel difference either fails the build or is pinned as a named exception with its justification in the test itself.
11. **The corpus at `~/Pictures/wallpaper` is READ-ONLY.** Tests copy out and pass `--processing-dir`. No wallpaper image is ever committed.
12. **No non-AI enlargement.** Unchanged: nothing is enlarged except by the model.

**User decisions (already made):**
- "Replace sips entirely" — not crop alone, not crop and resize.
- "Null-check every call" — no subprocess worker; failures stay `ImagingError`.
- "Differential, byte-identical bar" — not a perceptual threshold.
- "Incremental swap behind the interface" — one operation at a time, gated per swap.

---

### Task 0: Measure the four unknowns

**Goal:** Produce measured answers for the four questions the spec defers, because any of them can change the design, and record them as a committed findings document.

> **USER-ORDERED GATE — NON-SKIPPABLE.** This task was requested by the user in the current conversation. It MUST NOT be closed by walking around it, by declaring it "verified inline", or by substituting a cheaper check. Close only after every item in `acceptanceCriteria` has been re-validated independently, with output captured.

**Files:**
- Create: `docs/research/2026-09-13-coregraphics-preflight-measurements.md`

**Acceptance Criteria:**
- [ ] EXIF orientation: a fixture with orientation 6 is built, and `sips -g pixelWidth/pixelHeight` and `CGImageGetWidth/Height` are both recorded for it. The document states whether they agree and which behaviour the implementation adopts.
- [ ] Peak memory: RSS is captured for a 300 Mpx resize through both paths, and the document states which is higher and by how much.
- [ ] HEIC input: a corpus HEIC is loaded via `CGImageSourceCreateWithURL` and its dimensions are recorded against `sips -g`.
- [ ] Interpolation exactness: at least four resize shapes are compared byte for byte, covering one downscale, one enlargement, one width-governed and one height-governed. The document records pass or fail per shape.
- [ ] Every number in the document is accompanied by the command that produced it.

**Verify:** `test -f docs/research/2026-09-13-coregraphics-preflight-measurements.md && grep -c "orientation\|RSS\|heic\|interpolation" docs/research/2026-09-13-coregraphics-preflight-measurements.md`

**Steps:**

- [ ] **Step 1: Build an EXIF orientation fixture**

`magick -set exif:Orientation` did not write the tag during design. Use `sips` itself, which can set it:

```bash
cd "$(mktemp -d)"
magick -size 1200x600 gradient:red-blue rot.jpg
/usr/bin/sips -s orientation 6 rot.jpg --out rot6.jpg
/usr/bin/sips -g orientation -g pixelWidth -g pixelHeight rot6.jpg
```

If `sips -s orientation` does not take either, fall back to writing the EXIF block by hand with a 20-line Python script using `struct`; a JPEG APP1 segment with orientation 6 is the minimum needed. Record whichever method worked.

- [ ] **Step 2: Read the same fixture through ImageIO**

```bash
python3 - <<'PY'
import ctypes, ctypes.util
from ctypes import c_void_p, c_long, c_bool, c_char_p, c_uint32
cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
io = ctypes.cdll.LoadLibrary(ctypes.util.find_library("ImageIO"))
cf.CFStringCreateWithCString.restype = c_void_p
cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
cf.CFURLCreateWithFileSystemPath.restype = c_void_p
cf.CFURLCreateWithFileSystemPath.argtypes = [c_void_p, c_void_p, c_long, c_bool]
io.CGImageSourceCreateWithURL.restype = c_void_p
io.CGImageSourceCreateWithURL.argtypes = [c_void_p, c_void_p]
io.CGImageSourceCreateImageAtIndex.restype = c_void_p
io.CGImageSourceCreateImageAtIndex.argtypes = [c_void_p, c_long, c_void_p]
cg.CGImageGetWidth.restype = c_long;  cg.CGImageGetWidth.argtypes = [c_void_p]
cg.CGImageGetHeight.restype = c_long; cg.CGImageGetHeight.argtypes = [c_void_p]
u = cf.CFURLCreateWithFileSystemPath(
        None, cf.CFStringCreateWithCString(None, b"rot6.jpg", 0x08000100), 0, False)
img = io.CGImageSourceCreateImageAtIndex(io.CGImageSourceCreateWithURL(u, None), 0, None)
print("ImageIO:", cg.CGImageGetWidth(img), "x", cg.CGImageGetHeight(img))
PY
```

**If the two disagree, stop and report before continuing to Task 1.** A disagreement means crop geometry diverges for any input carrying an orientation tag, and the design needs a decision about which behaviour is correct. The corpus has no such files, so the test suite cannot catch it.

- [ ] **Step 3: Measure peak memory at 300 Mpx through both paths**

```bash
magick -size 15000x20000 gradient:black-white huge.png      # 300 Mpx
/usr/bin/time -l /usr/bin/sips --resampleHeightWidth 5000 3750 huge.png \
    -s format png --out sips_big.png 2>&1 | grep "maximum resident"
/usr/bin/time -l python3 resize.py huge.png cg_big.png 3750 5000 3 2>&1 | grep "maximum resident"
```

`resize.py` is the design-time proof of concept; recreate it from the spec's §2 table or re-derive it. Record both RSS figures.

- [ ] **Step 4: Load a corpus HEIC through ImageIO**

```bash
cp ~/Pictures/wallpaper/purple_nebula_glow_0312_x.heic .
/usr/bin/sips -g pixelWidth -g pixelHeight purple_nebula_glow_0312_x.heic
# then the same ImageIO snippet from Step 2, pointed at the .heic
```

Record whether ImageIO decodes it and whether the dimensions agree.

- [ ] **Step 5: Test interpolation exactness across four shapes**

```bash
for shape in "1000 700" "3000 2100" "800 1200" "1600 400"; do
  set -- $shape
  /usr/bin/sips --resampleHeightWidth $2 $1 photo.png -s format png --out s.png >/dev/null 2>&1
  python3 resize.py photo.png c.png $1 $2 3
  echo "$1x$2 -> $(magick compare -metric AE c.png s.png null: 2>&1 | head -1)"
done
```

Every shape must report `0 (0)`. Record any that does not, with its dimensions.

- [ ] **Step 6: Write and commit the findings**

Write `docs/research/2026-09-13-coregraphics-preflight-measurements.md` with a section per question, each carrying the command and its output. State plainly, for each, whether it clears the way for Task 1 or changes the design.

```bash
git add docs/research/2026-09-13-coregraphics-preflight-measurements.md
git commit -m "docs: preflight measurements for the CoreGraphics replacement"
```

---

### Task 1: The binding layer

**Goal:** `paperhanger/_cg.py` — every `ctypes` declaration, scope-based memory management, a null-checking helper, and primitives to load an image and write one.

**Files:**
- Create: `paperhanger/_cg.py`
- Create: `tests/test_cg.py`

**Acceptance Criteria:**
- [ ] `load(path)` returns an opaque image handle inside a scope, or raises `ImagingError` for a non-image
- [ ] `_checked(ptr, what, path)` raises `ImagingError` naming both when handed NULL, and returns the pointer otherwise
- [ ] `Scope` releases every handle added to it, on exception as well as on success
- [ ] A test loads and releases 300 images and asserts RSS growth stays under 50 MB
- [ ] `ctypes` is imported in `_cg.py` and nowhere else in `paperhanger/`
- [ ] the seven fixtures named in Step 3a all exist in `tests/conftest.py` and are each used by at least one test
- [ ] `pixels.write_grey_png` emits IHDR colour type 0 and `pixels.write_png16` emits bit depth 16, each asserted from the IHDR bytes

**Verify:** `uv run pytest tests/test_cg.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cg.py
import resource
import pytest
from paperhanger import _cg
from paperhanger.imaging import ImagingError


def test_load_returns_dimensions(tmp_path, png_fixture):
    """A readable image loads and reports the dimensions sips would."""
    src = png_fixture(tmp_path / "a.png", 640, 480)
    with _cg.Scope() as scope:
        img = _cg.load(scope, src)
        assert _cg.dimensions(img) == (640, 480)


def test_load_raises_for_a_non_image(tmp_path):
    """A non-image raises ImagingError naming the path, never returns NULL."""
    bad = tmp_path / "notes.txt"
    bad.write_text("this is not an image")
    with _cg.Scope() as scope:
        with pytest.raises(ImagingError) as exc:
            _cg.load(scope, bad)
    assert "notes.txt" in str(exc.value)


def test_checked_raises_on_null():
    """_checked is the single chokepoint that converts NULL into ImagingError."""
    with pytest.raises(ImagingError) as exc:
        _cg._checked(None, "create the thing", "/some/path.png")
    assert "create the thing" in str(exc.value)
    assert "/some/path.png" in str(exc.value)


def test_checked_passes_a_live_pointer_through():
    assert _cg._checked(12345, "anything", "/p") == 12345


def test_scope_releases_on_exception(tmp_path, png_fixture):
    """A raise inside the scope must not leak the handles taken before it."""
    src = png_fixture(tmp_path / "a.png", 64, 64)
    released = []
    scope = _cg.Scope(release=released.append)
    with pytest.raises(RuntimeError):
        with scope:
            _cg.load(scope, src)
            raise RuntimeError("boom")
    assert released, "the scope released nothing when the body raised"


def test_repeated_loads_do_not_leak(tmp_path, png_fixture):
    """300 load/release cycles must not grow RSS.

    This is the test that catches a missing CFRelease, which changes no
    output and so is invisible to every other assertion in the suite. A
    leaked 96 Mpx CGImage is ~380 MB and a real run does 894 photos.
    """
    src = png_fixture(tmp_path / "big.png", 1200, 900)
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    for _ in range(300):
        with _cg.Scope() as scope:
            img = _cg.load(scope, src)
            _cg.dimensions(img)
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    growth_mb = (after - before) / (1024 * 1024)
    assert growth_mb < 50, f"RSS grew {growth_mb:.0f} MB over 300 loads"
```

**`png_fixture` does not exist, and neither do the others this plan names.** `tests/conftest.py` defines only `processing_dir`, `corpus`, `corpus_sample`, `fake_upscaler` and `ready_toolchain`. Building the fixture set is part of this task -- see Step 3a. Six later tasks depend on them, so they are built once, here.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cg.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'paperhanger._cg'`

- [ ] **Step 3a: Build the fixture set the rest of the plan assumes**

`tests/pixels.py` writes RGB 8-bit PNGs only: `write_png(path, width, height, colour=(120,140,110), noise=False)`, `write_marked_png`, `read_png_rgb`. Every other shape this plan's tests need has to be built.

Extend `tests/pixels.py` with two stdlib writers, matching its existing style. `write_grey_png(path, width, height, top=0, bottom=255)` emits colour type 0 at 8 bits — the shape that renders BLACK through a naively-built bitmap context, which ten corpus images are and which Task 0 measured coming back with all 4,665,600 pixels zero at exit 0. Nothing else in `tests/` can produce that input. `write_png16(path, width, height)` emits colour type 2 at bit depth 16 — the shape the old `sips` pad path silently downconverted, and the one pinned exception in Task 3's gate.

Then add seven fixtures to `tests/conftest.py`, each a factory taking `(path, width, height)`:

- `png_fixture` — `pixels.write_png`, a plain RGB PNG at exact dimensions.
- `photo_fixture` — `pixels.write_png(..., noise=True)`. Detailed content, so a resample has something to get wrong; a flat colour resamples identically under any algorithm and would pass a broken implementation.
- `gradient_fixture` — `pixels.write_grey_png`. The top-left pixel identifies which SOURCE ROW came back, which is the only way to tell a correct crop from `sips`' centred one, because both have the right dimensions.
- `grayscale_fixture` — `pixels.write_grey_png` with a flat value. Tasks 4 and 6 must not blacken it.
- `png16_fixture` — `pixels.write_png16`.
- `profiled_fixture` — takes `(path, width, height, profile)` and shells out to `sips --matchTo /System/Library/ColorSync/Profiles/<profile>`; `profile=None` returns the untagged base.
- `webp_fixture` — writes a WebP and names it `.jpg`, because the corpus really contains one (`snowy_forest_landscape_9522.jpg`) and `probe` must report the real format rather than the name. Shells out to `magick`.

The last two shell out deliberately: both need a real encoder, both are test-only, and Global Constraint 1 governs `paperhanger/`, not `tests/`. When Task 8 removes `sips` from the project, `profiled_fixture` is the one test-side use that may remain — note it there rather than deleting it.

Write a test per new writer in `tests/test_pixels.py` asserting the IHDR colour type and bit depth from the bytes, not merely that a file appeared.

- [ ] **Step 3: Write `_cg.py`**

```python
"""The only module in this project that touches ctypes.

Everything above it sees Python values and ImagingError. This is the one
layer that can crash the process rather than raise, which is why every
call whose result can be NULL goes through `_checked`, and why every
owned handle goes into a `Scope` rather than relying on a CFRelease at
some return path.

CoreFoundation ownership rule: anything from a function with Create or
Copy in its name is ours to release. Anything from a Get is not.
"""

import ctypes
import ctypes.util
from ctypes import (CDLL, Structure, c_bool, c_char_p, c_double, c_int32,
                    c_long, c_uint32, c_void_p)
from pathlib import Path

from .imaging import ImagingError

_cf = CDLL(ctypes.util.find_library("CoreFoundation"))
_cg = CDLL(ctypes.util.find_library("CoreGraphics"))
_io = CDLL(ctypes.util.find_library("ImageIO"))

kCFStringEncodingUTF8 = 0x08000100
kCFNumberDoubleType = 13
kCGInterpolationHigh = 3
kCGImageAlphaNoneSkipLast = 5


class CGPoint(Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


class CGSize(Structure):
    _fields_ = [("width", c_double), ("height", c_double)]


class CGRect(Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


def _declare():
    """Every signature, in one place, so an argtypes mistake is findable."""
    _cf.CFStringCreateWithCString.restype = c_void_p
    _cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
    _cf.CFURLCreateWithFileSystemPath.restype = c_void_p
    _cf.CFURLCreateWithFileSystemPath.argtypes = [c_void_p, c_void_p, c_long, c_bool]
    _cf.CFNumberCreate.restype = c_void_p
    _cf.CFNumberCreate.argtypes = [c_void_p, c_long, c_void_p]
    _cf.CFDictionaryCreate.restype = c_void_p
    _cf.CFDictionaryCreate.argtypes = [c_void_p, c_void_p, c_void_p, c_long,
                                       c_void_p, c_void_p]
    _cf.CFRelease.argtypes = [c_void_p]

    _io.CGImageSourceCreateWithURL.restype = c_void_p
    _io.CGImageSourceCreateWithURL.argtypes = [c_void_p, c_void_p]
    _io.CGImageSourceCreateImageAtIndex.restype = c_void_p
    _io.CGImageSourceCreateImageAtIndex.argtypes = [c_void_p, c_long, c_void_p]
    _io.CGImageDestinationCreateWithURL.restype = c_void_p
    _io.CGImageDestinationCreateWithURL.argtypes = [c_void_p, c_void_p, c_long, c_void_p]
    _io.CGImageDestinationAddImage.argtypes = [c_void_p, c_void_p, c_void_p]
    _io.CGImageDestinationFinalize.restype = c_bool
    _io.CGImageDestinationFinalize.argtypes = [c_void_p]

    _cg.CGImageGetWidth.restype = c_long
    _cg.CGImageGetWidth.argtypes = [c_void_p]
    _cg.CGImageGetHeight.restype = c_long
    _cg.CGImageGetHeight.argtypes = [c_void_p]
    _cg.CGImageGetColorSpace.restype = c_void_p
    _cg.CGImageGetColorSpace.argtypes = [c_void_p]
    _cg.CGImageCreateWithImageInRect.restype = c_void_p
    _cg.CGImageCreateWithImageInRect.argtypes = [c_void_p, CGRect]
    _cg.CGImageRelease.argtypes = [c_void_p]
    _cg.CGBitmapContextCreate.restype = c_void_p
    _cg.CGBitmapContextCreate.argtypes = [c_void_p, c_long, c_long, c_long,
                                          c_long, c_void_p, c_uint32]
    _cg.CGBitmapContextCreateImage.restype = c_void_p
    _cg.CGBitmapContextCreateImage.argtypes = [c_void_p]
    _cg.CGContextSetInterpolationQuality.argtypes = [c_void_p, c_int32]
    _cg.CGContextDrawImage.argtypes = [c_void_p, CGRect, c_void_p]
    _cg.CGContextRelease.argtypes = [c_void_p]
    _cg.CGColorSpaceCreateWithName.restype = c_void_p
    _cg.CGColorSpaceCreateWithName.argtypes = [c_void_p]


_declare()


def _checked(ptr, what: str, path):
    """Turn a NULL into an ImagingError. The single chokepoint.

    Nearly every FFI crash in a pipeline like this is a NULL from a failed
    load handed straight to the next function. Checking here keeps the
    executor's one-exception-type model true.
    """
    if not ptr:
        raise ImagingError(f"could not {what} for {path}")
    return ptr


class Scope:
    """Releases every handle put into it, on the way out either way.

    `release` is injectable so a test can observe that releasing happened
    without reaching into CoreFoundation.
    """

    def __init__(self, release=None):
        self._handles = []
        self._release = release

    def own(self, ptr, kind="cf"):
        self._handles.append((ptr, kind))
        return ptr

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for ptr, kind in reversed(self._handles):
            if self._release is not None:
                self._release(ptr)
            elif kind == "image":
                _cg.CGImageRelease(ptr)
            elif kind == "context":
                _cg.CGContextRelease(ptr)
            else:
                _cf.CFRelease(ptr)
        self._handles.clear()
        return False


def _cfstr(scope, s: str):
    return scope.own(_checked(
        _cf.CFStringCreateWithCString(None, s.encode(), kCFStringEncodingUTF8),
        "build a CFString", s))


def _cfurl(scope, path):
    path = str(path)
    return scope.own(_checked(
        _cf.CFURLCreateWithFileSystemPath(None, _cfstr(scope, path), 0, False),
        "build a CFURL", path))


def load(scope, path):
    """Decode an image. Raises ImagingError for anything not an image."""
    path = Path(path)
    source = scope.own(_checked(
        _io.CGImageSourceCreateWithURL(_cfurl(scope, path), None),
        "open", path))
    return scope.own(_checked(
        _io.CGImageSourceCreateImageAtIndex(source, 0, None),
        "decode", path), kind="image")


def dimensions(image):
    return (_cg.CGImageGetWidth(image), _cg.CGImageGetHeight(image))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cg.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Confirm the ctypes containment constraint**

```bash
grep -rln "import ctypes" paperhanger/
```
Expected: exactly `paperhanger/_cg.py`

- [ ] **Step 6: Commit**

```bash
git add paperhanger/_cg.py tests/test_cg.py
git commit -m "feat: the CoreGraphics binding layer, scoped and null-checked"
```

---

### Task 2: The differential harness

**Goal:** A test helper that runs an operation through both the `sips` implementation and the CoreGraphics one and compares the bytes, so every later swap has a gate before it lands.

**Files:**
- Create: `tests/differential.py`
- Create: `tests/test_differential.py`
- Modify: `tests/conftest.py`

**Acceptance Criteria:**
- [ ] `compare(old_fn, new_fn, source, tmp_path)` returns `None` when the outputs match and a described difference otherwise, comparing decoded pixels for PNG and whole files for lossy formats (see Global Constraint 10)
- [ ] A deliberately different pair of functions makes it report a difference, proving the harness can fail
- [ ] Identical functions make it report none
- [ ] The helper reports which bytes differ and at what offset, not just that they do
- [ ] Runs in tier 1 on generated fixtures

**Verify:** `uv run pytest tests/test_differential.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_differential.py
from tests.differential import compare


def _writes(content):
    def fn(source, out_path):
        out_path.write_bytes(content)
    return fn


def test_identical_outputs_report_no_difference(tmp_path, png_fixture):
    src = png_fixture(tmp_path / "a.png", 64, 64)
    assert compare(_writes(b"same"), _writes(b"same"), src, tmp_path) is None


def test_different_outputs_are_reported(tmp_path, png_fixture):
    """The harness must be able to fail, or it is decoration."""
    src = png_fixture(tmp_path / "a.png", 64, 64)
    result = compare(_writes(b"aaaa"), _writes(b"aaba"), src, tmp_path)
    assert result is not None
    assert "offset 2" in result


def test_a_length_difference_is_reported(tmp_path, png_fixture):
    src = png_fixture(tmp_path / "a.png", 64, 64)
    result = compare(_writes(b"aa"), _writes(b"aaaa"), src, tmp_path)
    assert result is not None
    assert "2 bytes" in result and "4 bytes" in result
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_differential.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'tests.differential'`

- [ ] **Step 3: Write the harness**

```python
# tests/differential.py
"""Compare a sips operation against its CoreGraphics replacement.

The bar is byte-identical. Where a difference is intended, the calling
test pins it as a named exception with its reason; nothing is waved
through for being small.
"""


def compare(old_fn, new_fn, source, tmp_path):
    """Run both, return None if the bytes match, else a description."""
    old_out = tmp_path / "_old_out"
    new_out = tmp_path / "_new_out"
    old_fn(source, old_out)
    new_fn(source, new_out)

    old = old_out.read_bytes()
    new = new_out.read_bytes()
    if old == new:
        return None

    if len(old) != len(new):
        return (f"{source.name}: sips wrote {len(old)} bytes, "
                f"CoreGraphics wrote {len(new)} bytes")

    for i, (a, b) in enumerate(zip(old, new)):
        if a != b:
            return (f"{source.name}: first difference at offset {i} "
                    f"(sips {a:#04x}, CoreGraphics {b:#04x}), "
                    f"{sum(x != y for x, y in zip(old, new))} bytes differ")
    return f"{source.name}: differs"
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_differential.py -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Commit**

```bash
git add tests/differential.py tests/test_differential.py
git commit -m "test: the differential harness that gates every swap"
```

---

### Task 3: Crop on CoreGraphics

**Goal:** Reimplement `imaging.crop` on `_cg`, deleting the pad-and-shift workaround, with a differential gate carrying one pinned exception for 16-bit sources.

**Files:**
- Modify: `paperhanger/_cg.py`
- Modify: `paperhanger/imaging.py` (replace `crop`; delete `_offset_is_ignored`, `_crop_via_padding`, `PAD_COLOUR`)
- Modify: `tests/test_imaging.py`
- Create: `tests/test_crop_differential.py`

**Acceptance Criteria:**
- [ ] `crop` at origin `0,0` returns the top region, verified against source pixels rather than dimensions
- [ ] `crop` flush with the bottom edge returns the bottom region
- [ ] `crop` still refuses a non-`.png` `out_path`
- [ ] `crop` still refuses a rect that does not fit inside the source
- [ ] `_offset_is_ignored`, `_crop_via_padding` and `PAD_COLOUR` no longer exist
- [ ] Differential over the 27-image sample is byte-identical, with the 16-bit case pinned as a named exception
- [ ] A 16-bit source crops to 16-bit output, where the old path produced 8-bit

**Verify:** `uv run pytest tests/test_imaging.py tests/test_crop_differential.py -v` → all pass

**Steps:**

- [ ] **Step 1: Add `crop_to_file` to `_cg.py`**

```python
def _png_destination(scope, out_path):
    return scope.own(_checked(
        _io.CGImageDestinationCreateWithURL(
            _cfurl(scope, out_path), _cfstr(scope, "public.png"), 1, None),
        "create a PNG destination", out_path))


def _write(scope, image, out_path, options=None):
    dest = _png_destination(scope, out_path)
    _io.CGImageDestinationAddImage(dest, image, options)
    if not _io.CGImageDestinationFinalize(dest):
        raise ImagingError(f"could not write {out_path}")


def crop_to_file(source, x: int, y: int, width: int, height: int, out_path):
    """Cut a rect out of `source` and write it as PNG.

    No workaround needed: CGImageCreateWithImageInRect honours an origin of
    0,0 and a flush bottom edge, which are exactly the two shapes sips
    silently mis-crops.
    """
    with Scope() as scope:
        image = load(scope, source)
        rect = CGRect(CGPoint(float(x), float(y)),
                      CGSize(float(width), float(height)))
        cut = scope.own(_checked(
            _cg.CGImageCreateWithImageInRect(image, rect),
            f"crop {width}x{height}+{x}+{y}", source), kind="image")
        _write(scope, cut, out_path)
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_crop_differential.py
import pytest
from paperhanger import imaging
from tests.differential import compare

# The one intended difference, pinned rather than tolerated.
#
# The sips path pads the image before cropping, and sips' pad step
# downconverts a 16-bit source to 8-bit. The CoreGraphics path preserves
# depth. Every file in the corpus is 8-bit, so this never fires there --
# it is pinned so that if it ever stops being the ONLY difference, the
# gate says so.
PINNED = {
    "16-bit source": "sips pad downconverts to 8-bit; CoreGraphics preserves depth",
}


def test_crop_at_origin_returns_the_top_region(tmp_path, gradient_fixture):
    """Origin 0,0 is the case sips gets wrong. Check PIXELS, not dimensions.

    sips returns the centred slice at the correct size, so a dimension
    assertion passes against the bug. Only the pixels distinguish them.
    """
    src = gradient_fixture(tmp_path / "g.png", 400, 300)
    out = tmp_path / "top.png"
    imaging.crop(src, _rect(0, 0, 400, 100), out)
    assert _top_left_value(out) == _source_row_value(src, 0)


def test_crop_flush_to_the_bottom_returns_the_bottom_region(tmp_path, gradient_fixture):
    src = gradient_fixture(tmp_path / "g.png", 400, 300)
    out = tmp_path / "bot.png"
    imaging.crop(src, _rect(0, 200, 400, 100), out)
    assert _top_left_value(out) == _source_row_value(src, 200)


def test_a_sixteen_bit_source_keeps_its_depth(tmp_path, png16_fixture):
    """The pinned exception, asserted rather than assumed."""
    src = png16_fixture(tmp_path / "deep.png", 200, 200)
    out = tmp_path / "cut.png"
    imaging.crop(src, _rect(0, 0, 200, 100), out)
    assert _bit_depth(out) == 16


@pytest.mark.corpus
def test_crop_matches_sips_over_the_sample(corpus_sample, tmp_path):
    """Byte-identical over real images, with only the pinned exception."""
    differences = []
    for src in corpus_sample:
        result = compare(_sips_crop_reference, _cg_crop, src, tmp_path)
        if result is not None:
            differences.append(result)
    assert not differences, (
        "crop diverged from sips on files that are not the pinned "
        f"exception:\n" + "\n".join(differences))
```

`_rect`, `_top_left_value`, `_source_row_value`, `_bit_depth`, `gradient_fixture`, `png16_fixture` and `_sips_crop_reference` are helpers this task creates. `_sips_crop_reference` is the *current* `crop` body, copied verbatim into the test module before `imaging.crop` is replaced — this is the retained reference the spec calls for, and it lives in tests, never in `paperhanger/`.

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_crop_differential.py -v`
Expected: FAIL — helpers undefined, and the origin test fails against the current padded implementation only if the pad is removed first. Confirm the failures name the missing helpers.

- [ ] **Step 4: Replace `crop` in `imaging.py`**

```python
def crop(source, rect, out_path) -> None:
    """Cut `rect` out of `source`. Always writes PNG.

    The rect must lie entirely within the source. This is the only layer
    that can check rather than assume it, because it is the only one
    holding both the rect and the image it will be cut from: the executor
    crops the 4x frame using rect.scaled(4), so an enlargement that comes
    back even a pixel short overruns, and the user gets a black-edged
    wallpaper with nothing raising.

    ALWAYS writes PNG, whatever `out_path` is named, and REFUSES a name
    that says otherwise. A crop is an intermediate that something else
    will resize and encode, so spending a lossy generation on it would
    undo exactly what band 2 exists to protect.

    There is no offset workaround here any more. CGImageCreateWithImageInRect
    honours an origin of 0,0 and a flush bottom edge, which are the two
    shapes sips silently mis-crops -- see the research note on why the pad
    existed and what it cost.
    """
    out_path = Path(out_path)
    if out_path.suffix.lower() != ".png":
        raise ImagingError(
            f"crop always writes PNG bytes; {out_path.name} names itself "
            f"{out_path.suffix or 'nothing'} and every reader downstream "
            f"would believe the name"
        )

    measured = probe(source)
    if measured is None:
        raise ImagingError(f"cannot crop {source}: not a readable image")
    width, height, _ = measured
    if (rect.x < 0 or rect.y < 0
            or rect.x + rect.width > width
            or rect.y + rect.height > height):
        raise ImagingError(
            f"crop {rect.width}x{rect.height}+{rect.x}+{rect.y} does not fit "
            f"inside {width}x{height}"
        )

    _cg.crop_to_file(source, rect.x, rect.y, rect.width, rect.height, out_path)
```

Then delete `_offset_is_ignored`, `_crop_via_padding` and `PAD_COLOUR`, and add `from . import _cg` at the top.

**Import cycle warning:** `_cg.py` imports `ImagingError` from `imaging.py`, and `imaging.py` now imports `_cg`. Break it by moving `ImagingError` into `_cg.py` and re-exporting it from `imaging.py`, or by importing `_cg` lazily inside the functions. Pick the first; it puts the exception next to the layer that raises most of them, and `from ._cg import ImagingError` in `imaging.py` keeps every existing caller working.

- [ ] **Step 5: Retire the sips-specific tests in `test_imaging.py`**

Delete the tests that assert pad behaviour: anything referencing `PAD_COLOUR`, `_crop_via_padding`, `_offset_is_ignored`, or the magenta bleed measurement. Keep every test asserting *our* contract: the non-PNG refusal, the out-of-bounds refusal, region exactness.

- [ ] **Step 6: Run the full fast suite**

Run: `uv run pytest`
Expected: PASS. Test count drops by however many pad tests were removed; note the new number.

- [ ] **Step 7: Run the differential over the corpus**

Run: `uv run pytest tests/test_crop_differential.py -m corpus -v`
Expected: PASS, no differences reported.

- [ ] **Step 8: Commit**

```bash
git add paperhanger/_cg.py paperhanger/imaging.py tests/
git commit -m "feat: crop through CoreGraphics, and the pad workaround goes"
```

---

### Task 4: Resample on CoreGraphics

**Goal:** Move the resize half of `resize_and_encode` to `_cg`, keeping the function's name and signature.

**Files:**
- Modify: `paperhanger/_cg.py`
- Modify: `paperhanger/imaging.py:289-303`
- Create: `tests/test_resize_differential.py`

**Acceptance Criteria:**
- [ ] `resize_and_encode` keeps its exact signature: `(source, out_width, out_height, fmt, quality, out_path, resize)`
- [ ] Output dimensions equal exactly the requested width and height, on shapes where one axis could be derived from the other
- [ ] Interpolation is set to `kCGInterpolationHigh` explicitly, asserted by a test that fails if the call is removed
- [ ] Differential against `sips --resampleHeightWidth` is byte-identical on at least four shapes, no exceptions
- [ ] Colour profile survives the resample

**Verify:** `uv run pytest tests/test_resize_differential.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_resize_differential.py
import pytest
from paperhanger import imaging
from tests.differential import compare

# One downscale, one enlargement, one width-governed, one height-governed.
# The last two matter because a shape where sips could derive the second
# axis from the first does not discriminate -- 3840x2160 -> 8533x4800 is
# such a shape, and it is why the old by-height test passed a mutation.
SHAPES = [
    (2000, 1400, 1000, 700),
    (800, 600, 1600, 1200),
    (2000, 1400, 900, 500),
    (1400, 2000, 500, 900),
]


@pytest.mark.parametrize("sw,sh,dw,dh", SHAPES)
def test_resize_matches_sips_exactly(tmp_path, photo_fixture, sw, sh, dw, dh):
    src = photo_fixture(tmp_path / f"s{sw}x{sh}.png", sw, sh)

    def old(source, out):
        _sips_resize_reference(source, dw, dh, out)

    def new(source, out):
        imaging.resize_and_encode(source, dw, dh, "png", None, out, resize=True)

    assert compare(old, new, src, tmp_path) is None


@pytest.mark.parametrize("sw,sh,dw,dh", SHAPES)
def test_both_axes_land_exactly(tmp_path, photo_fixture, sw, sh, dw, dh):
    """Neither axis may be derived from the other."""
    src = photo_fixture(tmp_path / "s.png", sw, sh)
    out = tmp_path / "o.png"
    imaging.resize_and_encode(src, dw, dh, "png", None, out, resize=True)
    assert imaging.probe(out)[:2] == (dw, dh)


def test_interpolation_is_high_not_default(tmp_path, photo_fixture, monkeypatch):
    """Remove the SetInterpolationQuality call and this must fail.

    Default measured identical to High, so a test comparing output cannot
    tell them apart. Assert the call instead.
    """
    from paperhanger import _cg
    seen = []
    real = _cg._cg.CGContextSetInterpolationQuality
    monkeypatch.setattr(_cg._cg, "CGContextSetInterpolationQuality",
                        lambda ctx, q: seen.append(q) or real(ctx, q))
    src = photo_fixture(tmp_path / "s.png", 400, 300)
    imaging.resize_and_encode(src, 200, 150, "png", None, tmp_path / "o.png",
                              resize=True)
    assert seen == [_cg.kCGInterpolationHigh]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_resize_differential.py -v`
Expected: FAIL on the interpolation test (the call does not exist yet) and on the differential if `resize_and_encode` still shells out.

- [ ] **Step 3: Add `resize_to_file` to `_cg.py`**

```python
def resize_to_file(source, out_width: int, out_height: int, out_path):
    """Resample to exactly out_width x out_height and write PNG.

    Both axes are always explicit. Interpolation is set to High by name:
    Default measured byte-identical to High, but Default is Apple's to
    redefine and ours would change with it.
    """
    with Scope() as scope:
        image = load(scope, source)
        colorspace = _cg.CGImageGetColorSpace(image)     # a Get: not ours to release
        ctx = scope.own(_checked(
            _cg.CGBitmapContextCreate(None, out_width, out_height, 8, 0,
                                      colorspace, kCGImageAlphaNoneSkipLast),
            f"create a {out_width}x{out_height} bitmap context", source),
            kind="context")
        _cg.CGContextSetInterpolationQuality(ctx, kCGInterpolationHigh)
        _cg.CGContextDrawImage(
            ctx, CGRect(CGPoint(0.0, 0.0),
                        CGSize(float(out_width), float(out_height))), image)
        scaled = scope.own(_checked(
            _cg.CGBitmapContextCreateImage(ctx),
            "read back the resized image", source), kind="image")
        _write(scope, scaled, out_path)
```

- [ ] **Step 4: Rewrite `resize_and_encode` to use it for the resize half**

```python
def resize_and_encode(source, out_width: int, out_height: int, fmt: str,
                      quality, out_path, resize: bool) -> None:
    """Resample (optionally) and encode.

    Both axes are always passed explicitly. The caller computed them; this
    function derives nothing.
    """
    if resize:
        staged = Path(out_path).with_suffix(".resized.png")
        _cg.resize_to_file(source, out_width, out_height, staged)
        source_for_encode = staged
    else:
        staged = None
        source_for_encode = source

    try:
        argv = [SIPS, "-s", "format", fmt]
        if quality is not None:
            argv += ["-s", "formatOptions", str(quality)]
        argv += [str(source_for_encode), "--out", str(out_path)]
        _run(argv, produces=out_path)
    finally:
        if staged is not None:
            Path(staged).unlink(missing_ok=True)
```

The encode still goes through `sips` here; Task 5 replaces it and removes the staging file.

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_resize_differential.py -v`
Expected: PASS, 9 tests

- [ ] **Step 6: Run the full fast suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add paperhanger/_cg.py paperhanger/imaging.py tests/test_resize_differential.py
git commit -m "feat: resample through CoreGraphics at interpolation High"
```

---

### Task 5: Encode on CoreGraphics

**Goal:** Move the encode half, removing the staging file Task 4 introduced, and with it the last `sips` call in the output path.

**Files:**
- Modify: `paperhanger/_cg.py`
- Modify: `paperhanger/imaging.py`
- Create: `tests/test_encode_differential.py`

**Acceptance Criteria:**
- [ ] `heic`, `jpeg`, `avif` and `png` all encode, mapped to `public.heic`, `public.jpeg`, `public.avif`, `public.png`
- [ ] Quality N maps to `kCGImageDestinationLossyCompressionQuality` N/100, byte-identical to `sips -s formatOptions N`
- [ ] `quality=None` for PNG produces a lossless file with no quality key set
- [ ] The intermediate staging file from Task 4 is gone
- [ ] Format defaults are unchanged: heic 80, jpeg 90, avif 85

**Verify:** `uv run pytest tests/test_encode_differential.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_encode_differential.py
import pytest
from paperhanger import imaging
from tests.differential import compare

CASES = [("jpeg", 90), ("jpeg", 80), ("heic", 80), ("heic", 90), ("avif", 85)]


@pytest.mark.parametrize("fmt,quality", CASES)
def test_encode_matches_sips_byte_for_byte(tmp_path, photo_fixture, fmt, quality):
    src = photo_fixture(tmp_path / "s.png", 800, 600)

    def old(source, out):
        _sips_encode_reference(source, fmt, quality, out)

    def new(source, out):
        imaging.resize_and_encode(source, 800, 600, fmt, quality, out, resize=False)

    assert compare(old, new, src, tmp_path) is None


def test_png_takes_no_quality(tmp_path, photo_fixture):
    """Lossless means no quality key, not quality 100."""
    src = photo_fixture(tmp_path / "s.png", 400, 300)
    out = tmp_path / "o.png"
    imaging.resize_and_encode(src, 400, 300, "png", None, out, resize=False)
    assert imaging.probe(out)[:2] == (400, 300)


def test_no_staging_file_is_left_behind(tmp_path, photo_fixture):
    src = photo_fixture(tmp_path / "s.png", 400, 300)
    out = tmp_path / "o.heic"
    imaging.resize_and_encode(src, 200, 150, "heic", 80, out, resize=True)
    assert not list(tmp_path.glob("*.resized.png"))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_encode_differential.py -v`
Expected: FAIL — `_sips_encode_reference` undefined; the staging test fails while Task 4's staging is still in place.

- [ ] **Step 3: Add encoding to `_cg.py`**

```python
UTI = {"heic": "public.heic", "jpeg": "public.jpeg",
       "avif": "public.avif", "png": "public.png"}


def _quality_options(scope, quality):
    """A CFDictionary carrying the lossy quality, or None for lossless.

    sips quality 80 is 0.80 here -- measured byte-identical on JPEG and
    HEIC -- so the existing defaults transfer with no re-derivation.
    """
    if quality is None:
        return None
    key = c_void_p.in_dll(_io, "kCGImageDestinationLossyCompressionQuality")
    value = c_double(quality / 100.0)
    number = scope.own(_checked(
        _cf.CFNumberCreate(None, kCFNumberDoubleType, ctypes.byref(value)),
        "build a quality number", quality))
    keys = (c_void_p * 1)(key)
    values = (c_void_p * 1)(number)
    return scope.own(_checked(
        _cf.CFDictionaryCreate(
            None, keys, values, 1,
            c_void_p.in_dll(_cf, "kCFTypeDictionaryKeyCallBacks"),
            c_void_p.in_dll(_cf, "kCFTypeDictionaryValueCallBacks")),
        "build the destination options", quality))


def resize_and_encode_to_file(source, out_width, out_height, fmt, quality,
                              out_path, resize: bool):
    """Resample if asked, then encode, without an intermediate file."""
    if fmt not in UTI:
        raise ImagingError(f"unknown output format {fmt!r}")
    with Scope() as scope:
        image = load(scope, source)
        if resize:
            image = _resample(scope, image, out_width, out_height, source)
        dest = scope.own(_checked(
            _io.CGImageDestinationCreateWithURL(
                _cfurl(scope, out_path), _cfstr(scope, UTI[fmt]), 1, None),
            f"create a {fmt} destination", out_path))
        _io.CGImageDestinationAddImage(dest, image,
                                       _quality_options(scope, quality))
        if not _io.CGImageDestinationFinalize(dest):
            raise ImagingError(f"could not write {out_path}")
```

Refactor Task 4's `resize_to_file` body into a `_resample(scope, image, w, h, source)` helper returning an owned image, so both entry points share it.

- [ ] **Step 4: Simplify `resize_and_encode` to one call**

```python
def resize_and_encode(source, out_width: int, out_height: int, fmt: str,
                      quality, out_path, resize: bool) -> None:
    """Resample (optionally) and encode, in one pass.

    Both axes are always passed explicitly. The caller computed them; this
    function derives nothing.
    """
    _cg.resize_and_encode_to_file(source, out_width, out_height, fmt,
                                  quality, out_path, resize)
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_encode_differential.py -v`
Expected: PASS, 7 tests

- [ ] **Step 6: Full fast suite, then commit**

```bash
uv run pytest
git add paperhanger/_cg.py paperhanger/imaging.py tests/test_encode_differential.py
git commit -m "feat: encode through ImageIO, staging file gone"
```

---

### Task 6: Colour normalization on CoreGraphics

**Goal:** Replace `normalize_to_srgb_png`'s `--matchTo` invocation with a CoreGraphics colour-space conversion, keeping its contract: the upscaler receives sRGB PNG.

**Files:**
- Modify: `paperhanger/_cg.py`
- Modify: `paperhanger/imaging.py:169-181`
- Create: `tests/test_normalize_differential.py`

**Acceptance Criteria:**
- [ ] An Adobe RGB source normalizes to sRGB, confirmed by reading the output's profile
- [ ] A ProPhoto RGB source normalizes to sRGB
- [ ] An untagged source is written as sRGB rather than left untagged
- [ ] Output is PNG regardless of input format
- [ ] Differential against the `--matchTo` reference is byte-identical

**Verify:** `uv run pytest tests/test_normalize_differential.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_normalize_differential.py
import pytest
from paperhanger import imaging
from tests.differential import compare

PROFILES = ["AdobeRGB1998.icc", "ProPhoto.icc", None]


@pytest.mark.parametrize("profile", PROFILES)
def test_normalize_matches_the_matchto_reference(tmp_path, profiled_fixture, profile):
    src = profiled_fixture(tmp_path / "s.png", 600, 400, profile)
    assert compare(_sips_normalize_reference, imaging.normalize_to_srgb_png,
                   src, tmp_path) is None


@pytest.mark.parametrize("profile", PROFILES)
def test_output_is_srgb(tmp_path, profiled_fixture, profile):
    """The whole point: the upscaler strips ICC, so the numbers must
    already be sRGB before it sees them."""
    src = profiled_fixture(tmp_path / "s.png", 600, 400, profile)
    out = tmp_path / "n.png"
    imaging.normalize_to_srgb_png(src, out)
    assert "sRGB" in _profile_name(out)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_normalize_differential.py -v`
Expected: FAIL — `_sips_normalize_reference` and `profiled_fixture` undefined.

- [ ] **Step 3: Add the conversion to `_cg.py`**

```python
def normalize_to_srgb_png_file(source, out_path):
    """Draw into an sRGB context, then write PNG.

    Runs for EVERY source on the upscale path. upscayl-bin emits PNG with
    no ICC chunk, so without this the encode step tags sRGB over
    unconverted numbers -- a wide-gamut round trip measures about 15 dB
    worse, an order of magnitude larger than the 33.6-36.0 dB spread the
    upscaler itself was chosen on.
    """
    with Scope() as scope:
        image = load(scope, source)
        width, height = dimensions(image)
        srgb = scope.own(_checked(
            _cg.CGColorSpaceCreateWithName(_cfstr(scope, "kCGColorSpaceSRGB")),
            "create the sRGB color space", source))
        ctx = scope.own(_checked(
            _cg.CGBitmapContextCreate(None, width, height, 8, 0, srgb,
                                      kCGImageAlphaNoneSkipLast),
            "create an sRGB context", source), kind="context")
        _cg.CGContextSetInterpolationQuality(ctx, kCGInterpolationHigh)
        _cg.CGContextDrawImage(
            ctx, CGRect(CGPoint(0.0, 0.0),
                        CGSize(float(width), float(height))), image)
        converted = scope.own(_checked(
            _cg.CGBitmapContextCreateImage(ctx),
            "read back the converted image", source), kind="image")
        _write(scope, converted, out_path)
```

`kCGColorSpaceSRGB` is a CFString constant; if `CGColorSpaceCreateWithName` returns NULL for the name built this way, read the exported symbol instead with `c_void_p.in_dll(_cg, "kCGColorSpaceSRGB")` and pass that. Try the exported symbol first — it is the documented form.

- [ ] **Step 4: Replace the body in `imaging.py`**

```python
def normalize_to_srgb_png(source, out_path) -> None:
    """Convert to PNG with the pixels forced into sRGB.

    Runs for EVERY source on the upscale path, not only unreadable
    formats. upscayl-bin emits PNG with no ICC chunk, so without this the
    encode step tags sRGB over unconverted numbers. The corpus holds 19
    Adobe RGB, 3 ProPhoto RGB and 96 further non-sRGB profiles among 894
    files, all JPEG or PNG, so a format-based condition would never fire
    for any of them.
    """
    _cg.normalize_to_srgb_png_file(source, out_path)
```

- [ ] **Step 5: Run, then commit**

```bash
uv run pytest tests/test_normalize_differential.py -v
uv run pytest
git add paperhanger/_cg.py paperhanger/imaging.py tests/test_normalize_differential.py
git commit -m "feat: sRGB normalization through CoreGraphics"
```

---

### Task 7: Probe on ImageIO, and the CLI knock-on

**Goal:** Replace `probe`'s `sips -g` parsing with an ImageIO load, and update the `sips` pre-flight and `doctor` checks that exist only because `sips` could be missing.

**Files:**
- Modify: `paperhanger/_cg.py`
- Modify: `paperhanger/imaging.py:137-166`
- Modify: `paperhanger/cli.py`
- Modify: `tests/test_imaging.py`, `tests/test_cli.py`
- Modify: `README.md`

**Acceptance Criteria:**
- [ ] `probe` returns `(width, height, format)` for an image and `None` for anything else, never raising
- [ ] `.DS_Store` returns `None` (it killed `sips` with a signal; ImageIO must simply decline it)
- [ ] A file whose extension lies about its format reports the real format
- [ ] `probe` never raises, for any input including a directory and a missing path
- [ ] The `sips` pre-flight in `_run` and the `sips` line in `doctor` are gone or repurposed, and the README troubleshooting entry for a missing `sips` goes with them
- [ ] `paperhanger doctor` still reports the upscaler binary and model

**Verify:** `uv run pytest tests/test_imaging.py tests/test_cli.py -v && uv run paperhanger doctor`

**Steps:**

- [ ] **Step 1: Write the failing tests**

```python
def test_probe_declines_a_ds_store(tmp_path):
    """.DS_Store killed sips with a signal. ImageIO must just say no."""
    junk = tmp_path / ".DS_Store"
    junk.write_bytes(b"\x00\x00\x00\x01Bud1" + b"\x00" * 64)
    assert imaging.probe(junk) is None


def test_probe_reports_the_real_format_not_the_extension(tmp_path, webp_fixture):
    """snowy_forest_landscape_9522.jpg is actually a WebP. The name lies."""
    src = webp_fixture(tmp_path / "lies.jpg", 300, 200)
    assert imaging.probe(src)[2] != "jpeg"


def test_probe_never_raises(tmp_path):
    for candidate in [tmp_path, tmp_path / "absent.png", tmp_path / ".DS_Store"]:
        assert imaging.probe(candidate) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_imaging.py -k probe -v`
Expected: the `.DS_Store` and format tests fail or pass for the wrong reason; confirm which before changing code.

- [ ] **Step 3: Add `probe_file` to `_cg.py`**

```python
def probe_file(path):
    """(width, height, format) or None. Never raises.

    Non-image detection is now a real answer rather than a string match:
    ImageIO either decodes it or it does not.
    """
    try:
        with Scope() as scope:
            url = _cfurl(scope, path)
            source = _io.CGImageSourceCreateWithURL(url, None)
            if not source:
                return None
            scope.own(source)
            image = _io.CGImageSourceCreateImageAtIndex(source, 0, None)
            if not image:
                return None
            scope.own(image, kind="image")
            width, height = dimensions(image)
            if width < 1 or height < 1:
                return None
            uti = _io.CGImageSourceGetType(source)     # a Get: not ours
            return (width, height, _format_name(scope, uti))
    except Exception:
        return None
```

Declare `CGImageSourceGetType` with `restype = c_void_p`. `_format_name` maps the UTI CFString back to the short names `probe` returned before (`jpeg`, `png`, `heic`, `webp`, `gif`, `tiff`) by reading the CFString and stripping the `public.` prefix; anything unrecognised becomes `"unknown"`, matching the old fallback.

**Check what the old `probe` returned for each corpus format before writing the mapping.** `bands.py` and the report may compare these strings; changing them silently would be a regression the differential cannot see, because `probe` writes no file.

- [ ] **Step 4: Replace `probe` and remove the sips pre-flight**

```python
def probe(path):
    """(width, height, format) for an image, or None for anything else.

    Never raises.
    """
    return _cg.probe_file(path)
```

Then in `cli.py`, remove the `/usr/bin/sips` existence pre-flight and the `sips` line from `doctor`. Keep everything about `upscayl-bin` and the model.

- [ ] **Step 5: Update the README**

Delete the troubleshooting entry "Everything is reported as a non-image", which described a missing `sips`. Update the Requirements section: `sips` is no longer shelled out to.

- [ ] **Step 6: Run everything, then commit**

```bash
uv run pytest
uv run paperhanger doctor
git add paperhanger/ tests/ README.md
git commit -m "feat: probe through ImageIO, and the sips preflight goes"
```

---

### Task 8: Remove the last of sips, and move the findings to research

**Goal:** Delete the retained `sips` reference implementations from the tests, confirm `sips` is referenced nowhere in `paperhanger/`, and move the seven measured `sips` constraints out of the spec and into the research record where they remain true.

**Files:**
- Modify: `tests/` (delete the `_sips_*_reference` helpers and the differential tests that used them)
- Modify: `docs/superpowers/specs/2026-09-11-paperhanger-design.md`
- Modify: `docs/research/2026-09-11-replacing-pixelmator-and-imagemagick.md`
- Modify: `paperhanger/imaging.py` (module docstring)
- Modify: `CLAUDE.md`

**Acceptance Criteria:**
- [ ] `grep -rn "sips" paperhanger/` returns nothing
- [ ] The differential tests and their `sips` references are deleted, having served their purpose
- [ ] The seven measured `sips` constraints live in `docs/research/`, stated as findings about `sips` rather than as constraints on our code
- [ ] The eighth, the pad path's 16-bit downconversion, is recorded with them
- [ ] `imaging.py`'s module docstring describes the CoreGraphics layer rather than seven `sips` facts
- [ ] All three tiers pass

**Verify:** `grep -rn "sips" paperhanger/ ; uv run pytest ; uv run pytest -m corpus`

**Steps:**

- [ ] **Step 1: Confirm the differential has done its job**

Run every differential test once more and record the output. These tests are about to be deleted; their last green run is the evidence that the replacement is faithful.

```bash
uv run pytest tests/test_crop_differential.py tests/test_resize_differential.py \
    tests/test_encode_differential.py tests/test_normalize_differential.py -v -m corpus
```

Paste the result into the commit message.

- [ ] **Step 2: Delete the reference implementations and their tests**

Remove `_sips_crop_reference`, `_sips_resize_reference`, `_sips_encode_reference`, `_sips_normalize_reference` and the differential test modules. Keep `tests/differential.py` — the harness is generic and the next migration will want it.

**Do not delete the behavioural tests those modules also contained.** The origin-crop test, the flush-bottom test, the both-axes-exactly tests and the interpolation-is-High test are assertions about our code and move to `tests/test_imaging.py`.

- [ ] **Step 3: Confirm sips is gone**

```bash
grep -rn "sips\|SIPS" paperhanger/
```
Expected: no output.

- [ ] **Step 4: Move the seven facts to research**

Append a section to `docs/research/2026-09-11-replacing-pixelmator-and-imagemagick.md` titled "The seven measured sips defects, and the eighth we added", carrying each fact with its measurement verbatim from `imaging.py`'s old docstring. Then rewrite `imaging.py`'s module docstring to describe what the module now is.

In `docs/superpowers/specs/2026-09-11-paperhanger-design.md` §7, replace the seven constraints with a short paragraph saying the imaging layer moved to CoreGraphics, why, and where the `sips` findings now live.

- [ ] **Step 5: Update CLAUDE.md's description**

The "What this is" section says the tool uses `sips`. Correct it.

- [ ] **Step 6: Run all three tiers**

```bash
uv run pytest                      # tier 1
uv run pytest -m corpus            # tier 2, ~8 min
uv run pytest -m real_upscaler     # tier 3, ~25 min
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: sips is gone, and its findings move to research"
```

---

## Self-Review

**Spec coverage.** §2 measured facts → Task 0 re-verifies the four unknowns; the rest are already evidence. §3 architecture → Task 1 (containment, scope, null-checks). §4 operations and order → Tasks 3-7 in the spec's order. §4.1 the CLI knock-on → Task 7. §5 verification → Task 2 builds the harness, every swap task uses it, Task 8 retires it. §6 measure-first → Task 0. §7 documentation → Task 8. §8 done criteria → Task 8's acceptance criteria. No gaps.

**Placeholder scan.** No TBD, no "add error handling", no "similar to Task N". Two steps name a fallback path rather than a single answer (the EXIF fixture in Task 0 Step 1, the sRGB constant in Task 6 Step 3); both state which to try first and what to do if it fails, which is a decision procedure rather than a placeholder.

**Type consistency.** `_cg` function names used consistently: `load`, `dimensions`, `crop_to_file`, `resize_to_file` (refactored to `_resample` in Task 5), `resize_and_encode_to_file`, `normalize_to_srgb_png_file`, `probe_file`. `Scope`, `_checked`, `_cfstr`, `_cfurl`, `_write` defined in Task 1 and used unchanged after. `imaging.py` public signatures are untouched throughout, as Global Constraint 4 requires.

**One risk the plan carries deliberately.** Task 3 introduces an import cycle between `imaging.py` and `_cg.py` and names the fix inline rather than restructuring in Task 1, because moving `ImagingError` before anything imports it would be a change with no test to justify it.
