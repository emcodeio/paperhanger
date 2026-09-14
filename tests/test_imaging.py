import ctypes
import shutil
import struct
import subprocess
import zlib
from concurrent.futures import ThreadPoolExecutor
from ctypes import c_void_p
from functools import partial
from pathlib import Path

import pytest

from paperhanger import _cg, cli, formats, geometry, imaging
from paperhanger.geometry import Rect
from tests import pixels
from tests.conftest import sips_or_skip
from tests.differential import PNG_SIGNATURE, _format_of
from tests.pixels import read_png_rgb, write_marked_png, write_png


# ---------- probe ----------
#
# `probe` decides WHAT COUNTS AS AN IMAGE. `cli.scan` separates photographs
# from junk with it, `crop` bounds-checks with it, `execute` uses it as a
# post-condition and the report's non-image count is its None answers. It is
# also the one operation in this module that writes no file, so the
# differential harness the other six replacements lean on cannot see a
# regression here at all: a format string that quietly became "public.jpeg"
# would break no output and every comparison. Hence the corpus format table
# below, and hence every string in `_cg.SOURCE_FORMATS` being measured.

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
def test_probe_rejects_non_images(tmp_path, name, content):
    """Junk is None, whatever its name says.

    These were the files `sips` exited 0 for while printing
    `pixelWidth: <nil>`, which is why the old probe read stdout instead of the
    exit status. ImageIO has no exit status to misread: it builds no image
    source for any of them, so the answer falls out of the decode. The two
    `.jpg`/`.png` rows are the ones that matter -- the extension is not
    evidence, and a probe that trusted it would pass the `.txt` row alone.
    """
    path = tmp_path / name
    path.write_bytes(content)
    assert imaging.probe(path) is None


def test_probe_rejects_a_truncated_jpeg(tmp_path):
    """A JPEG cut off before its dimensions are readable is not an image.

    The cutoff is verified against THIS fixture rather than assumed: the
    control below asserts the untruncated file probes correctly, so the test
    cannot pass by having produced something unreadable at both lengths --
    which is exactly how it would pass if `probe` started returning None for
    everything.

    100 bytes is short of the SOF marker for this generated image. Measured
    while moving `probe` to ImageIO, on a 400x300 fixture: the first 200 bytes
    give a source whose type is `public.jpeg` and whose decode still returns
    NULL, and `sips` agreed, printing no dimensions. A real photograph's
    larger header makes the threshold file-specific, which is why this
    truncates and measures one known file instead of naming a universal
    number.
    """
    good = write_png(tmp_path / "good.png", 400, 300, noise=True)
    jpeg = tmp_path / "full.jpg"
    imaging.resize_and_encode(good, 400, 300, "jpeg", 90, jpeg, resize=False)

    assert imaging.probe(jpeg) == (400, 300, "jpeg"), \
        "the control failed: the untruncated fixture must be readable, or " \
        "the truncation below proves nothing"

    truncated = tmp_path / "truncated.jpg"
    truncated.write_bytes(jpeg.read_bytes()[:100])
    assert imaging.probe(truncated) is None


def test_probe_declines_a_ds_store(tmp_path):
    """.DS_Store killed `sips` with a SIGNAL. ImageIO must just say no.

    Measured on the real corpus file: `sips -g pixelWidth` died on an uncaught
    NSInvalidArgumentException -- `object cannot be nil (key: typeIdentifier)`
    -- returncode -6, which is the whole reason the old probe checked
    `returncode < 0`. ImageIO builds a CGImageSource for the same bytes
    perfectly happily and then reports a count of 0, no type and no image at
    index 0, so the decode is what declines it.

    Synthetic bytes rather than the corpus file, so this runs on any machine;
    `test_probe_declines_the_real_ds_store` is the same assertion against the
    real one.
    """
    junk = tmp_path / ".DS_Store"
    junk.write_bytes(b"\x00\x00\x00\x01Bud1" + b"\x00" * 64)
    assert imaging.probe(junk) is None


def test_probe_declines_the_real_ds_store(tmp_path, corpus):
    """The synthetic bytes above, checked against the file that provoked all
    of this. Skips where the corpus is not present."""
    ds_store = corpus / ".DS_Store"
    if not ds_store.exists():
        pytest.skip("no .DS_Store in the corpus")
    local = tmp_path / "ds_store_copy"
    shutil.copy2(ds_store, local)
    assert imaging.probe(local) is None


def test_probe_reports_the_real_format_not_the_extension(tmp_path, webp_fixture):
    """The corpus really contains `snowy_forest_landscape_9522.jpg`, a WebP.

    Asserted as the POSITIVE string rather than `!= "jpeg"`: a probe that had
    started answering "unknown" for every WebP would satisfy the inequality
    and would be a regression, since `webp` is what the old probe returned for
    all three WebPs in the corpus, that mis-named one included.
    """
    src = webp_fixture(tmp_path / "lies.jpg", 300, 200)
    assert src.suffix == ".jpg"
    assert imaging.probe(src) == (300, 200, "webp")


def test_probe_reports_actual_format_not_extension(tmp_path, corpus):
    """The same file the fixture above stands in for. This is why
    normalize-or-not is decided from the probe, never from the suffix."""
    lying = corpus / "snowy_forest_landscape_9522.jpg"
    if not lying.exists():
        pytest.skip("the known extension-mismatch fixture is not present")
    local = tmp_path / lying.name
    shutil.copy2(lying, local)
    result = imaging.probe(local)
    assert result is not None
    assert result[2] == "webp"
    assert local.suffix == ".jpg"


def test_probe_never_raises(tmp_path):
    """The contract, stated as one test over every shape that has broken it.

    `cli.scan` calls this on every entry of a directory the user chose, so
    anything that raises here ends a run over someone's Pictures folder with
    a traceback instead of a wallpaper.
    """
    junk = tmp_path / ".DS_Store"
    junk.write_bytes(b"\x00\x00\x00\x01Bud1" + b"\x00" * 64)
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    for candidate in [tmp_path, tmp_path / "absent.png", junk, empty,
                      tmp_path / "no" / "such" / "parent" / "x.png"]:
        assert imaging.probe(candidate) is None


def test_probe_of_a_missing_file_is_none(tmp_path):
    assert imaging.probe(tmp_path / "nope.png") is None


def test_probe_of_a_directory_is_none(tmp_path):
    assert imaging.probe(tmp_path) is None


def test_probe_runs_no_subprocess(tmp_path, monkeypatch):
    """What the deleted `sips` pre-flight was guarding, from the other side.

    `cli` refused to start when `/usr/bin/sips` was missing, because a probe
    that could not spawn it returned None for every photograph and the run
    reported a whole library as unreadable. That check is gone, and this is
    the evidence that it can be: measuring spawns nothing, so there is no
    binary whose absence could empty a scan.

    Breaks every spawn on the machine for the duration rather than checking
    for one named binary. `imaging` no longer holds a path to the old tool at
    all, and a probe that had grown its own spelling of one would still be
    caught here.
    """
    def refuse(*args, **kwargs):
        raise AssertionError(f"probe spawned a subprocess: {args!r}")

    path = write_png(tmp_path / "a.png", 64, 48)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "check_output", refuse)

    assert imaging.probe(path) == (64, 48, "png")


# The census the `sips` probe returned for the whole corpus, recorded BEFORE
# the ImageIO one was written, by running the old body over all 894
# photographs. It is the only check in this project that can catch a format
# string changing, because `probe` writes no file for a differential to
# compare -- so it is pinned as counts rather than as a set, and a photograph
# whose format were read differently would move one count and fail.
CORPUS_FORMATS = {"jpeg": 841, "png": 47, "webp": 3, "heic": 2, "gif": 1}


@pytest.mark.corpus
def test_probe_reproduces_the_sips_format_census_over_the_whole_corpus(corpus):
    """Tier 2: all 894, against what `sips -g format` said for each.

    `cli.scan` is what the tool actually calls, so this goes through it rather
    than through `probe` directly -- the non-image count it returns is the
    other half of the answer, and `.DS_Store` is the one file that has to land
    there.
    """
    from collections import Counter

    images, non_images = cli.scan(corpus)

    assert Counter(fmt for _p, _w, _h, fmt in images) == CORPUS_FORMATS
    assert sum(CORPUS_FORMATS.values()) == 894
    assert non_images == 1, \
        "the corpus holds exactly one non-image, its .DS_Store"


def _sips_dimensions(sips: str, path):
    """`sips -g pixelWidth -g pixelHeight`, parsed. None for a non-image.

    Split out of the test below so it can be handed to a thread pool. The
    parse is the original one, unchanged: stdout rather than exit status,
    because `sips -g pixelWidth` exits 0 while printing `<nil>` and dies by
    signal on `.DS_Store` -- fact 1, and the reason the `except` is this
    broad.
    """
    proc = subprocess.run(
        [sips, "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
        capture_output=True, text=True)
    values = {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.strip().partition(":")
        if sep:
            values[key.strip()] = value.strip()
    try:
        return (int(values["pixelWidth"]), int(values["pixelHeight"]))
    except (KeyError, ValueError):
        return None                                # the .DS_Store, and only it


@pytest.mark.corpus
def test_probe_agrees_with_sips_on_every_corpus_dimension(corpus):
    """The other half of the triple, over the same 894 files.

    Runs the old `sips -g` parse here rather than trusting the recorded
    census, because dimensions are 894 pairs and a table of them in this file
    would be a transcription nobody could check. Zero disagreements was the
    measured result.

    EIGHT WORKERS, AND ONLY FOR THE SUBPROCESS. The pool spawns the same 894
    processes with the same arguments, and `pool.map` returns them in input
    order, so the comparison, its assertions and its failure message are the
    ones they were. Measured on this machine: the serial body 14.14 s with
    zero disagreements, this one 2.97 s -- the `sips` half alone goes 13.99 s
    to 2.41 s, 5.8x.

    THAT IS ELEVEN SECONDS, NOT SIX MINUTES, and the six was worth checking
    rather than inheriting. This test was recorded as costing "about six
    minutes, most of tier 2's runtime"; the whole of tier 2 is 4:59, of which
    this was 14 s. `sips -g pixelWidth` reads a header and does not decode,
    so 894 of them are cheap however they are spawned. The pool is still
    worth having -- it is strictly less wall clock for an identical
    comparison -- but it is not where tier 2's five minutes go.

    `imaging.probe` deliberately stays on the main thread. It is the thing
    under test and it is `ctypes` into ImageIO; running it exactly as the
    tool does keeps this a measurement of the probe rather than of the probe
    under concurrency. It costs almost nothing to leave there: `probe`
    returns a lazily-decoded image and never pulls the pixels, so all 894 of
    them are a fraction of the subprocess half.

    `~/Pictures/wallpaper` is read-only and irreplaceable. Every call here,
    in the pool and out of it, only reads.
    """
    sips = sips_or_skip()
    paths = sorted(p for p in Path(corpus).iterdir() if p.is_file())
    with ThreadPoolExecutor(max_workers=8) as pool:
        expectations = list(pool.map(partial(_sips_dimensions, sips), paths))

    disagreed = []
    for path, expected in zip(paths, expectations):
        measured = imaging.probe(path)
        got = None if measured is None else measured[:2]
        if got != expected:
            disagreed.append((path.name, expected, got))

    assert not disagreed, (f"{len(disagreed)} file(s) measured differently "
                           f"from sips: {disagreed[:5]}")


# ---------- operations ----------

def _profile(path):
    proc = subprocess.run([sips_or_skip(), "-g", "profile", str(path)],
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
    """A transposed rect silently crops the WRONG REGION at the right size, so
    dimensions alone cannot catch it -- a fully transposed implementation
    still returns 100x100 for a 100x100 request. Hence a marker and a real
    pixel check.

    The rect is deliberately non-square, which catches a swap of width and
    height, and deliberately off-centre, which catches a swap of x and y.
    Both swaps were live hazards: `sips` took -c HEIGHT WIDTH and
    --cropOffset Y X, and CGRect takes origin before size with x before y,
    so the argument order reversed at the swap and this is what checks it.
    """
    marker = (240, 30, 200)
    rect = Rect(x=250, y=40, width=120, height=80)
    source = write_marked_png(tmp_path / "marked.png", 400, 200, rect,
                              base=(10, 10, 10), marker=marker)

    out = tmp_path / "region.png"
    imaging.crop(source, rect, out)

    assert imaging.probe(out)[:2] == (120, 80)
    width, height, rows = read_png_rgb(out)
    assert (width, height) == (120, 80)
    found = {pixel for row in rows for pixel in row}
    assert found == {marker}, f"crop landed off target; saw {sorted(found)}"


def test_crop_marker_check_would_fail_on_a_wrong_region(tmp_path):
    """Guards the guard: proves the marker assertion above can actually fail,
    so it is not passing because every crop happens to look uniform."""
    marker = (240, 30, 200)
    rect = Rect(x=250, y=40, width=120, height=80)
    source = write_marked_png(tmp_path / "marked.png", 400, 200, rect,
                              base=(10, 10, 10), marker=marker)

    off_by_one = tmp_path / "off.png"
    imaging.crop(source, Rect(x=249, y=40, width=120, height=80), off_by_one)
    _, _, rows = read_png_rgb(off_by_one)
    assert {pixel for row in rows for pixel in row} != {marker}


@pytest.mark.parametrize("rect,why", [
    (Rect(x=350, y=40, width=120, height=80), "overruns the right edge"),
    (Rect(x=900, y=900, width=120, height=80), "lies wholly outside"),
    (Rect(x=0, y=0, width=900, height=900), "is larger than the source"),
])
def test_an_out_of_bounds_crop_is_refused(tmp_path, rect, why):
    """Neither implementation clamps and neither errors.

    `sips` PADDED WITH BLACK -- fact 5: every rect below came back at exit 0,
    with exactly the requested dimensions, as a valid file, so the fact 4
    post-condition could not help because the file really was there.
    CoreGraphics INTERSECTS instead and returns the overlap, which is a
    plausible file at the WRONG dimensions. Measured on this 400x200 source:
    120x80 at x=350 comes back 50x80 and 900x900 at the origin comes back as
    the whole 400x200. Only measuring the source and comparing catches
    either, which is why the guard survived the swap unchanged.
    """
    source = write_marked_png(tmp_path / "m.png", 400, 200,
                              Rect(x=250, y=40, width=120, height=80))
    out = tmp_path / "out.png"
    with pytest.raises(imaging.ImagingError) as caught:
        imaging.crop(source, rect, out)
    assert "400x200" in str(caught.value)
    assert not out.exists(), f"a rect that {why} still produced a file"


def test_the_bounds_guard_allows_a_rect_flush_with_the_edges(tmp_path):
    """The guard must use > and not >=. geometry.horizontal_thirds puts its
    bottom slice flush against the bottom edge and every slice flush against
    both side edges, so an off-by-one here would refuse every real crop."""
    marker = (240, 30, 200)
    flush = Rect(x=280, y=120, width=120, height=80)   # 280+120 == 400, 120+80 == 200
    source = write_marked_png(tmp_path / "m.png", 400, 200, flush, marker=marker)
    out = tmp_path / "out.png"
    imaging.crop(source, flush, out)
    assert imaging.probe(out)[:2] == (120, 80)
    _, _, rows = read_png_rgb(out)
    assert {pixel for row in rows for pixel in row} == {marker}


def _crop_lands_on_the_marker(tmp_path, source_w, source_h, rect, tag):
    """Put the marker exactly at `rect`, crop `rect`, and report whether the
    pixels that came back are the marked ones. Dimensions cannot answer this:
    sips returns the right SIZE from the wrong PLACE -- see fact 6."""
    marker = (240, 30, 200)
    source = write_marked_png(tmp_path / f"src_{tag}.png", source_w, source_h,
                              rect, marker=marker)
    out = tmp_path / f"out_{tag}.png"
    imaging.crop(source, rect, out)
    if imaging.probe(out)[:2] != (rect.width, rect.height):
        return False
    _, _, rows = read_png_rgb(out)
    return {pixel for row in rows for pixel in row} == {marker}


@pytest.mark.parametrize("slicer", ["horizontal_thirds", "vertical_thirds"])
def test_every_real_geometry_slice_crops_the_region_it_asked_for(tmp_path, slicer):
    """Fact 6, against the actual producer of rects rather than rects invented
    to suit the code. Two of the three horizontal slices and one of the three
    vertical ones sit on a --cropOffset shape sips silently ignores, so with a
    raw sips crop this fails for slices 0 and 2 horizontally and slice 0
    vertically -- each returning a plausible image of the right size from the
    wrong part of the picture. It passed under the pad and it passes under
    CGImageCreateWithImageInRect, which is the point of testing the rects the
    geometry really produces rather than the ones an implementation finds
    convenient."""
    width, height = 1600, 1200
    for index, rect in enumerate(getattr(geometry, slicer)(width, height)):
        assert _crop_lands_on_the_marker(tmp_path, width, height, rect,
                                         f"{slicer}{index}"), \
            f"{slicer}[{index}] {rect} cropped the wrong region"


@pytest.mark.parametrize("rect", [
    Rect(x=0, y=0, width=300, height=80),      # offset 0 0 -> centered crop
    Rect(x=0, y=120, width=300, height=80),    # flush bottom -> no crop at all
    Rect(x=0, y=120, width=400, height=80),    # flush bottom, full width
    Rect(x=0, y=40, width=300, height=80),     # the shape sips gets right
    Rect(x=1, y=120, width=300, height=80),    # x=1 rescued the flush case
])
def test_crop_is_region_exact_on_and_off_the_bad_offsets(tmp_path, rect):
    """The boundary either side of fact 6, kept because the boundary is what a
    replacement has to clear: the three shapes sips gets wrong must now be
    right, and the two it already got right must not have moved."""
    assert _crop_lands_on_the_marker(tmp_path, 400, 200, rect, "b")


def test_crop_always_writes_png_even_from_a_lossy_source(tmp_path):
    """A lossy source must not produce a lossy intermediate.

    This used to be fact 7's other half: without `-s format` sips kept the
    SOURCE's format whatever the --out suffix said, so crop wrote JPEG bytes
    into a file named .png and quietly spent a lossy generation. The trap is
    gone with the tool -- `_cg.crop_to_file` names `public.png` on the
    destination and can write nothing else -- but the CONTRACT is ours and
    outlives the implementation, so it stays asserted. probe reads the
    content, not the name, which is the only way to see it.
    """
    marked = write_marked_png(tmp_path / "m.png", 800, 600,
                              Rect(x=0, y=0, width=400, height=300))
    source_jpg = tmp_path / "m.jpg"
    imaging.resize_and_encode(marked, 800, 600, "jpeg", 90, source_jpg,
                              resize=False)
    assert imaging.probe(source_jpg)[2] == "jpeg"

    for tag, rect in [("at the origin", Rect(x=0, y=0, width=400, height=300)),
                      ("interior", Rect(x=100, y=100, width=400, height=300))]:
        out = tmp_path / f"{tag.replace(' ', '_')}.png"
        imaging.crop(source_jpg, rect, out)
        assert imaging.probe(out)[2] == "png", f"{tag} did not write PNG"


@pytest.mark.parametrize("name", ["out.jpg", "out.heic", "out"])
def test_crop_refuses_an_out_path_that_is_not_png(tmp_path, name):
    """The contract was a docstring asking callers to pass `.png`.

    Handed `out.jpg` this wrote PNG bytes into it: sips warns `Output file
    suffix should be jpg` on stderr, the zero exit discards the warning, the
    post-condition sees a file that exists, and everything downstream goes by
    the name. The one class of sips defect this module is entirely about --
    a plausible wrong result at exit 0 -- introduced by its own caller.
    """
    source = write_png(tmp_path / "s.png", 400, 300)

    with pytest.raises(imaging.ImagingError, match="PNG"):
        imaging.crop(source, Rect(x=10, y=10, width=100, height=80),
                     tmp_path / name)

    assert not (tmp_path / name).exists(), "and it refuses before writing"


def test_crop_still_accepts_the_name_every_caller_passes(tmp_path):
    """Guards the guard: the suffix check must not have turned into a ban on
    every name. `.PNG` too -- the check is about what the file claims to be,
    and a case-sensitive comparison would refuse a legitimate one."""
    source = write_png(tmp_path / "s.png", 400, 300)
    rect = Rect(x=10, y=10, width=100, height=80)

    for name in ("crop_sunset_top_phone_2880x4320_3.6x.png", "loud.PNG"):
        out = tmp_path / name
        imaging.crop(source, rect, out)
        assert imaging.probe(out)[:2] == (100, 80)


def test_crop_leaves_no_intermediate_behind(tmp_path):
    """The pad wrote `<out>.padded.png` beside the output and removed it in a
    finally. Nothing writes beside the output any more, and this says so: a
    stray file in a processing directory is one the executor never registered
    as an intermediate and therefore never deletes."""
    source = write_png(tmp_path / "s.png", 400, 300, noise=True)
    out = tmp_path / "out.png"
    imaging.crop(source, Rect(x=0, y=0, width=400, height=100), out)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.png", "s.png"]


def test_fusing_crop_and_resample_is_wrong(tmp_path):
    """Global constraint 3, proven rather than asserted. The fused call scales
    by the PRE-CROP width, so it returns a quarter of the requested size."""
    source = write_png(tmp_path / "wide.png", 3840, 2160, noise=True)

    fused = tmp_path / "fused.png"
    subprocess.run([sips_or_skip(), "-c", "1080", "1920", "--cropOffset", "0", "500",
                    "--resampleWidth", "960", str(source), "--out", str(fused)],
                   check=True, capture_output=True)
    assert imaging.probe(fused)[:2] == (480, 270)      # NOT 960x540

    stage = tmp_path / "stage.png"
    separate = tmp_path / "separate.png"
    imaging.crop(source, Rect(x=500, y=0, width=1920, height=1080), stage)
    imaging.resize_and_encode(stage, 960, 540, "png", None, separate, resize=True)
    assert imaging.probe(separate)[:2] == (960, 540)


# Global constraint 3, as a table: each row is a real plan shape whose two
# axes sips would NOT have derived from one another, so a single-flag
# implementation returns a different file. `dropped` names the flag the row
# rules out.
#
# The rows exist because the by-height case was not discriminating.
# `test_resize_hits_both_axes_exactly_by_height` used 3840x2160 -> 8533x4800,
# where --resampleWidth 8533 derives 4800 on the nose, so the mutation the
# by-width row catches walked straight through the by-height one. Measured
# against sips rather than reasoned about: its derived axis is neither
# floored nor rounded the way the planner's `round()` is.
BOTH_AXES = [
    # sips derives 4798 from the width; the plan says 4797 -- imaging fact 3.
    (2662, 1663, 7680, 4797, "--resampleWidth"),
    # A real desktop-by-width plan, 5119 by the planner's round(), 5118 by
    # sips' own derivation.
    (2048, 1365, 7680, 5119, "--resampleWidth"),
    # The other mutation, which needs a by-HEIGHT shape to show at all:
    # --resampleHeight 4800 derives 7673 where the plan says 7674.
    (1920, 1201, 7674, 4800, "--resampleHeight"),
    (2048, 1365, 7202, 4800, "--resampleHeight"),
]


@pytest.mark.parametrize("width,height,out_width,out_height,dropped", BOTH_AXES)
def test_resize_hits_both_axes_exactly(tmp_path, width, height, out_width,
                                       out_height, dropped):
    """The plan DEFINES the output size; sips is told both numbers."""
    source = write_png(tmp_path / "s.png", width, height)
    out = tmp_path / "out.png"
    imaging.resize_and_encode(source, out_width, out_height, "png", None, out,
                              resize=True)
    assert imaging.probe(out)[:2] == (out_width, out_height)


@pytest.mark.parametrize("width,height,out_width,out_height,dropped", BOTH_AXES)
def test_one_resample_flag_alone_would_miss_each_of_those(
        tmp_path, width, height, out_width, out_height, dropped):
    """Guards the guard, by running the mutation rather than describing it.

    Every row above has to be a shape where dropping `dropped` actually
    changes the file, or the row costs a second and proves nothing -- which
    is exactly what the old by-height test was doing. `sips` is invoked
    directly here, by its own path rather than through `imaging`, which holds
    no path to it any more: this is a measurement OF the tool, not of this
    codebase, and it is the same test-side use of it as `tests/pixels.py`'s
    dimension checks and `conftest.profiled_fixture`'s tagging.
    """
    source = write_png(tmp_path / "s.png", width, height)
    out = tmp_path / "one_flag.png"
    axis = str(out_width) if dropped == "--resampleWidth" else str(out_height)
    subprocess.run([sips_or_skip(), dropped, axis, "-s", "format", "png",
                    str(source), "--out", str(out)],
                   check=True, capture_output=True)

    assert imaging.probe(out)[:2] != (out_width, out_height), \
        f"{dropped} alone gives the right answer here, so the row above is " \
        f"not pinning constraint 3"


def test_encode_without_resizing_preserves_dimensions(tmp_path):
    source = write_png(tmp_path / "s.png", 1234, 567, noise=True)
    out = tmp_path / "out.heic"
    imaging.resize_and_encode(source, 1234, 567, "heic", 80, out, resize=False)
    assert imaging.probe(out)[:2] == (1234, 567)


def test_resize_false_does_not_resample_even_when_dimensions_differ(tmp_path):
    """resize=False must mean "do not resample", not "resample to whatever you
    were handed". The realistic bands 2 and 4 call passes the source's own
    dimensions, which cannot tell those two readings apart -- an always-resample
    bug would still return 1234x567 above. Passing dimensions that differ is
    what makes the flag observable."""
    source = write_png(tmp_path / "s.png", 800, 600, noise=True)
    out = tmp_path / "out.png"
    imaging.resize_and_encode(source, 400, 300, "png", None, out, resize=False)
    assert imaging.probe(out)[:2] == (800, 600)


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


# The two argv tests that stood here -- that `png` omits `-s formatOptions`
# and that a lossy format passes it -- went with the `sips` encode. The thing
# they pinned is unchanged and still unobservable in the output (ImageIO
# ignores a quality key for PNG exactly as `sips` ignored the flag), so it is
# still asserted at the call rather than in the file:
# `test_cg.test_no_options_dictionary_is_built_for_a_lossless_write` and its
# lossy counterpart watch the options argument of
# CGImageDestinationAddImage.


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


ADOBE_RGB_PROFILE = "/System/Library/ColorSync/Profiles/AdobeRGB1998.icc"


def _wide_gamut_png(tmp_path, name, width, height):
    """A local Adobe RGB fixture, built from a system profile.

    The only other colour test in the suite is corpus-marked and skips on any
    machine without the author's photographs, which is exactly the machine
    where a colour regression would go unnoticed.
    """
    flat = write_png(tmp_path / f"flat_{name}", width, height, noise=True)
    wide = tmp_path / name
    subprocess.run([sips_or_skip(), "--matchTo", ADOBE_RGB_PROFILE,
                    "-s", "format", "png", str(flat), "--out", str(wide)],
                   capture_output=True, check=True)
    assert "Adobe RGB" in (_profile(wide) or ""), _profile(wide)
    return wide


def test_crop_carries_the_source_profile_through(tmp_path):
    """The executor's per-plan fallback crops BEFORE it normalizes, which is
    only safe if the crop keeps the profile it was cut from.

    If sips dropped it, `--matchTo` afterwards would convert from an assumed
    sRGB -- which is to say, not convert at all -- and global constraint 5
    would be lost on that path alone, at exit 0, with the right dimensions and
    an sRGB tag over wide-gamut numbers.

    Both branches, because a rect sips would otherwise ignore goes the long way
    round through a padded intermediate and a second invocation.
    """
    wide = _wide_gamut_png(tmp_path, "wide.png", 400, 300)

    direct = tmp_path / "direct.png"
    imaging.crop(wide, Rect(x=100, y=50, width=200, height=150), direct)
    assert "Adobe RGB" in (_profile(direct) or ""), _profile(direct)

    padded = tmp_path / "padded.png"
    imaging.crop(wide, Rect(x=0, y=0, width=200, height=150), padded)
    assert "Adobe RGB" in (_profile(padded) or ""), _profile(padded)


def test_cropping_before_normalizing_is_the_same_picture(tmp_path):
    """The claim the fallback's ordering actually rests on, in pixels.

    A profile tag proves the metadata survived; this proves the conversion
    still happens and lands in the same place. Byte-identical, measured -- and
    not vacuously so: deleting the profile from the crop first makes sips
    report it as sRGB, turns `--matchTo` into a no-op, and leaves a single
    channel 144 out of 255 away from this result.
    """
    wide = _wide_gamut_png(tmp_path, "wide.png", 120, 90)
    rect = Rect(x=20, y=10, width=60, height=40)

    cropped = tmp_path / "cropped.png"
    imaging.crop(wide, rect, cropped)
    crop_first = tmp_path / "crop_first.png"
    imaging.normalize_to_srgb_png(cropped, crop_first)

    normalized = tmp_path / "normalized.png"
    imaging.normalize_to_srgb_png(wide, normalized)
    normalize_first = tmp_path / "normalize_first.png"
    imaging.crop(normalized, rect, normalize_first)

    _, _, one = read_png_rgb(crop_first)
    _, _, two = read_png_rgb(normalize_first)
    assert one == two

    # And the conversion is not a no-op on this fixture, or the equality above
    # would hold however badly the profile were handled.
    _, _, raw = read_png_rgb(cropped)
    assert raw != one


@pytest.mark.parametrize("resize", [False, True])
@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_an_unreadable_source_fails_in_the_binding_layer(tmp_path, kind,
                                                         resize):
    """Both branches now fail the same way, which they did not before.

    `sips` answered these two with fact 4 -- exit 0, a warning on stderr, and
    no file -- so the encode branch used to be caught by `_run`'s
    post-condition and the resample branch by ImageIO. With the encode on
    ImageIO too there is one answer: an ImagingError naming the path, before
    anything is written. The guarantee the executor depends on is what had to
    survive, and it is asserted here rather than the wording that changed:
    one exception type per photo, and no output left behind.

    A directory is the case an input-side `exists()` check waves through, and
    it is why the check is on the artifact rather than on the input.
    """
    source = tmp_path / "nope.png" if kind == "missing" else tmp_path
    out = tmp_path / "x.png"
    with pytest.raises(imaging.ImagingError) as caught:
        imaging.resize_and_encode(source, 100, 100, "png", None, out,
                                  resize=resize)
    assert "could not open" in str(caught.value)
    assert str(source) in str(caught.value)
    assert not out.exists()


def _stub_binary(path, body: str):
    """A shell script standing in for upscayl-bin, doing one wrong thing.

    `upscale` is the only `_run(produces=...)` caller left, so the two
    branches below cannot be reached with a real tool: the ones that used to
    reach them were `sips --matchTo` invocations, and Task 6 replaced the
    last of those with a CoreGraphics conversion. The failure SHAPES are
    real -- an exit 0 that wrote nothing, and a nonzero exit with a message
    on stderr -- and they belong to a binary this project does not control,
    which is the argument for still checking them.
    """
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


def test_a_stale_destination_cannot_stand_in_for_a_skipped_write(tmp_path):
    """`_run`'s own unlink-first, which nothing else reaches any more.

    The post-condition asks "is the file there?", and fact 4 is a tool
    answering a write it skipped with exit 0 and no file. Put an earlier
    run's output at the destination and the question answers yes for a run
    that wrote nothing -- so `_run` removes the destination before starting.

    Through `upscale` since Task 6, as the test this replaces said it should
    be: `normalize_to_srgb_png` used to be the `sips` call that still wrote,
    and it no longer runs a subprocess at all.
    """
    binary = _stub_binary(tmp_path / "stub-upscayl", "exit 0")
    source = write_png(tmp_path / "s.png", 32, 24)
    stale = tmp_path / "out.png"
    write_png(stale, 64, 64)

    with pytest.raises(imaging.ImagingError) as caught:
        imaging.upscale(source, stale, binary, tmp_path / "models")
    assert "exited 0 without writing" in str(caught.value)
    assert not stale.exists(), (
        "the tool skipped the write and an earlier run's file is still "
        "standing at the destination, which the post-condition reads as "
        "success")


def test_an_unremovable_stale_output_still_raises_imaging_error(tmp_path):
    """The unlink can fail, and a bare PermissionError is not this layer's.

    A stale output inside a directory turned read-only: the clearing unlink
    cannot remove it, and before this was guarded the OSError escaped
    `resize_and_encode` unchanged. `execute` catches ImagingError per photo
    and carries on; anything else ends the run. Task 1 fixed this shape in
    `write_png` and it arrived again here.
    """
    room = tmp_path / "ro"
    room.mkdir()
    out = room / "o.heic"
    write_png(out, 8, 8)                       # an earlier run's output
    source = write_png(tmp_path / "s.png", 64, 48, noise=True)
    room.chmod(0o500)
    try:
        with pytest.raises(imaging.ImagingError) as caught:
            imaging.resize_and_encode(source, 64, 48, "heic", 80, out,
                                      resize=False)
    finally:
        room.chmod(0o700)
    assert str(out) in str(caught.value)
    assert out.exists(), (
        "the premise: the stale file could not be removed, which is why the "
        "unlink had to raise something")

    # And it says WHY, which a bare `except OSError: pass` did not: that
    # version left the caller with "could not create a heic destination"
    # and no mention of the permission or of the file still standing there.
    # `_cg._write` composes the same sentence for its own recovery unlink.
    message = str(caught.value)
    assert "still there" in message, message
    assert "Permission denied" in message, message
    assert isinstance(caught.value.__cause__, imaging.ImagingError), (
        "the ImageIO failure is the cause; the unlink failure is the "
        "annotation on it")


def test_fact_4_still_has_a_live_caller(tmp_path):
    """An exit-0 no-op is still refused, on the one call that can produce one.

    Fact 4 was measured on `sips`: given a path it cannot read it exits 0,
    warns on stderr and writes nothing, so the exit status is not the signal
    and the artifact has to be checked. No `sips` invocation here writes any
    more -- Task 6 took the last one -- and the fact's ANSWER outlives its
    subject, because `upscale` runs a binary this project does not control
    and an exit-0 no-op from that one would be believed the same way.

    Nothing stale at the destination, unlike the test above: this is the
    post-condition on its own, with no file for it to have removed first.
    """
    binary = _stub_binary(tmp_path / "stub-upscayl", "exit 0")
    source = write_png(tmp_path / "s.png", 32, 24)
    out = tmp_path / "x.png"

    with pytest.raises(imaging.ImagingError) as caught:
        imaging.upscale(source, out, binary, tmp_path / "models")
    assert "exited 0 without writing" in str(caught.value)
    assert not out.exists()


def test_a_stale_destination_cannot_stand_in_for_output(tmp_path):
    """A leftover file from an earlier run must not be able to answer for a
    run that wrote nothing.

    `_run(produces=...)` used to clear the destination and no subprocess runs
    any more, so `resize_and_encode` clears it itself. Not the unlink-first
    `_cg._write` argues against -- that one is about destinations ImageIO
    refuses, which it refuses before touching a file. This is about a failure
    BEFORE the write, and an unreadable source is the whole of it.
    """
    missing = tmp_path / "nope.png"
    out = tmp_path / "out.png"
    write_png(out, 64, 64)                      # stale output from an earlier run
    for resize in (True, False):
        write_png(out, 64, 64)
        with pytest.raises(imaging.ImagingError):
            imaging.resize_and_encode(missing, 10, 10, "png", None, out,
                                      resize=resize)
        assert not out.exists(), (
            f"resize={resize} left the stale file to be mistaken for output")


def test_a_missing_binary_raises_imaging_error(tmp_path):
    """Criterion 7 covers every failure, not only the ones that run. upscayl-bin
    absent is the realistic first-run case, and it lands in Task 9."""
    source = write_png(tmp_path / "s.png", 32, 24)
    with pytest.raises(imaging.ImagingError) as caught:
        imaging.upscale(source, tmp_path / "out.png",
                        tmp_path / "no-such-upscayl", tmp_path / "models")
    assert "could not be run" in str(caught.value)
    assert isinstance(caught.value.__cause__, FileNotFoundError)


def test_a_timeout_raises_imaging_error():
    """The other escapee. Uses _run directly: no sips invocation is slow enough
    to time out reliably, and inventing one would test the fixture."""
    with pytest.raises(imaging.ImagingError) as caught:
        imaging._run(["/bin/sleep", "5"], timeout=0.2)
    assert "timed out" in str(caught.value)
    assert isinstance(caught.value.__cause__, subprocess.TimeoutExpired)


def test_a_nonzero_exit_raises_with_stderr(tmp_path):
    """The other branch of _run: a nonzero exit, reported with what the tool
    said about it.

    Through `upscale` since Task 6. It used to be a real `sips --matchTo` on
    a text file, which exits 13 with `Cannot extract image` on stderr; that
    invocation is gone, and the branch now belongs to upscayl-bin -- which
    the tier-1 suite does not have, hence a stub. The status and the message
    are what `execute` puts in its report for a failed photo, so both are
    asserted rather than only the exception type.
    """
    binary = _stub_binary(
        tmp_path / "stub-upscayl",
        'echo "stub: cannot extract image" >&2\nexit 13')
    source = write_png(tmp_path / "s.png", 32, 24)
    with pytest.raises(imaging.ImagingError) as caught:
        imaging.upscale(source, tmp_path / "x.png", binary, tmp_path / "models")
    message = str(caught.value)
    assert "exited 13" in message
    assert "cannot extract image" in message         # the tool's own stderr


@pytest.mark.parametrize("resize", [False, True])
def test_a_non_image_is_refused_on_both_branches(tmp_path, resize):
    """A file that exists and is not an image. ImageIO opens the source --
    `CGImageSourceCreateWithURL` succeeds for anything readable -- and fails
    at the decode, so this reaches a different `_checked` than the missing
    file above and must still be the one exception type."""
    junk = tmp_path / "note.txt"
    junk.write_bytes(b"this is not an image\n")
    out = tmp_path / "x.png"
    with pytest.raises(imaging.ImagingError) as caught:
        imaging.resize_and_encode(junk, 100, 100, "png", None, out,
                                  resize=resize)
    assert "could not decode" in str(caught.value)
    assert not out.exists()


# ---------- what the retired differentials asserted about US ----------
#
# `tests/test_crop_differential.py`, `test_resize_differential.py`,
# `test_encode_differential.py` and `test_normalize_differential.py` each held
# a copy of the `sips` implementation it replaced, and compared the two over
# fixtures and over the corpus sample. Every one of those comparisons was
# green on its last run -- 116 tier-1 and 8 corpus -- and all four modules are
# gone with the reference. What follows is the part of them that was never
# about `sips`: assertions about this codebase's own behaviour that happened
# to live beside the comparison.


def _grey_top_left(path) -> int:
    """The greyscale value at (0, 0): which SOURCE ROW came back."""
    _width, _height, rows = pixels.read_png_grey(path)
    return rows[0][0]


def _grey_source_row(path, y: int) -> int:
    _width, _height, rows = pixels.read_png_grey(path)
    return rows[y][0]


def test_crop_at_origin_returns_the_top_region(tmp_path, gradient_fixture):
    """Origin 0,0 is the case the old tool got wrong. Check PIXELS, not
    dimensions.

    It returned the centred slice at the correct size, so a dimension
    assertion passes against the bug. Only the pixels distinguish them: the
    fixture's value names its own source row, so (0, 0) of a correct top crop
    is row 0 and (0, 0) of the centred one is row 100.

    `test_crop_is_region_exact_on_and_off_the_bad_offsets` above covers the
    same rect with a marker and a whole-region assertion, which is stronger.
    This is the row-identity form of it, on a gradient, and it is cheap.
    """
    src = gradient_fixture(tmp_path / "g.png", 40, 300)
    out = tmp_path / "top.png"
    imaging.crop(src, Rect(x=0, y=0, width=40, height=100), out)
    assert _grey_top_left(out) == _grey_source_row(src, 0)


def test_crop_flush_to_the_bottom_returns_the_bottom_region(tmp_path,
                                                            gradient_fixture):
    """The second shape: flush with the bottom edge, where the old tool
    dropped the crop entirely and handed back the whole source. Pixels again,
    because the size check DOES catch that one and would hide the centred
    case beside it."""
    src = gradient_fixture(tmp_path / "g.png", 40, 300)
    out = tmp_path / "bot.png"
    imaging.crop(src, Rect(x=0, y=200, width=40, height=100), out)
    assert _grey_top_left(out) == _grey_source_row(src, 200)


def test_a_sixteen_bit_source_keeps_its_depth_through_the_crop(tmp_path,
                                                               png16_fixture):
    """Deliberate, and the same choice `resize_and_encode` makes.

    The old tool dropped a 16-bit source to 8 on every path that touched
    pixels -- the direct crop, the crop at 0,0, the pad alone and a plain
    resample; only a format convert kept 16. So the loss was never the pad's,
    which is what the design had attributed it to. No corpus file is 16-bit,
    so this is latent and asserted on a fixture.
    """
    src = png16_fixture(tmp_path / "deep.png", 200, 200)
    assert pixels.read_ihdr(src)[2] == 16
    out = tmp_path / "cut.png"
    imaging.crop(src, Rect(x=0, y=0, width=200, height=100), out)
    assert pixels.read_ihdr(out)[2] == 16


# The threshold measured for the region-decode phenomenon, in pixels. At it
# and below, a region decode of a baseline JPEG equals the whole-frame decode;
# above it, it does not.
#
# A ROUND DECIMAL NUMBER, not a power of two, which is not the guess anyone
# makes -- the constant this replaces read 1024 * 1024 until the space
# between 10**6 and 2**20 was sampled. 1000x1000 and 1250x800 are both
# exactly 1,000,000 and both agree; 1250x801 (1,001,250) and 1000x1002
# (1,002,000) do not.
ONE_MILLION_PIXELS = 1_000_000


def _region(rows, x, y, width, height):
    return [row[x:x + width] for row in rows[y:y + height]]


def _samples_differing(a, b):
    count = largest = 0
    for row_a, row_b in zip(a, b):
        for pixel_a, pixel_b in zip(row_a, row_b):
            for one, two in zip(pixel_a, pixel_b):
                if one != two:
                    count += 1
                    largest = max(largest, abs(one - two))
    return count, largest


def _frame_decode(source, out_path):
    """The whole frame, decoded and written out. No region anywhere in it."""
    with _cg.Scope() as scope:
        image = _cg.load(scope, source)
        _cg.write_png(scope, image, out_path)
    return Path(out_path)


@pytest.mark.parametrize("width,height,expect_agreement", [
    (1000, 1000, True),     # 1,000,000 px exactly -- at the line
    (1250, 801, False),     # 1,001,250 px -- 1,250 px past it
])
def test_a_region_decode_of_a_big_baseline_jpeg_is_not_the_frame_decode(
        tmp_path, photo_fixture, width, height, expect_agreement):
    """ImageIO decodes a REGION of a baseline JPEG differently from the whole
    frame, once the file passes a million pixels.

    This is not about cropping: the region really is the one that was asked
    for, and only the last bit or two of each sample moves. The old tool
    showed it just as plainly -- it is ImageIO underneath -- which is why
    byte-identity with it was never reachable on a real JPEG and why the
    retired crop differential had to account for the difference per file
    instead of tolerating it.

    The two sizes bracket the threshold within 1,250 pixels of area, and that
    tightness is the point: the constant read 1024 * 1024 until the space
    between 10**6 and 2**20 was sampled, and every measurement taken before
    that reproduced digit for digit while naming the wrong number. Measured
    over the 200x150 region, samples differing of 90,000:

        1000x1000  1,000,000 px   0
        1250x800   1,000,000 px   0
        1250x801   1,001,250 px   86,413, largest 79

    Noise is the worst case by a wide margin; on real corpus photographs the
    same comparison is a mean absolute error under 1.4 out of 255.

    DO NOT DELETE THE `expect_agreement=True` CASE AS REDUNDANT COVERAGE. It
    is the only test in this suite that makes an ABSOLUTE claim about a crop
    of a JPEG below the threshold, and it was the only thing that caught a
    one-row origin error keyed on `Path(source).suffix != ".png"` injected
    against the whole suite:

        uv run pytest -m corpus -k crop     2 passed  (5m39s)  -- GREEN
        uv run pytest                       1 failed of 647    -- RED

    and re-injected against the suite as it stands, in Task 8, where the
    numbers are 1 failed / 722 passed and the one failure is this test at
    `[1000-1000-True]`, 89,522 samples differing. Both runs are kept: the
    first is the original evidence at the suite size of the day, the second
    is the same mutation still being caught 76 tests later.

    The single failure was this test at `expect_agreement=True`. Nothing
    else in the suite noticed, because every other JPEG-crop assertion was
    relative -- comparing two implementations to each other -- and the corpus
    gate's control re-ran both against a decoded PNG, so a bug that fired
    only on an un-decoded source was invisible to it. Here the crop must
    agree with a frame decode of the same bytes, on a source that is not
    already a PNG.
    """
    source = photo_fixture(tmp_path / f"n{width}x{height}.png", width, height)
    # Detail is required. A flat region decodes to the same numbers under any
    # IDCT or chroma upsampling, so a comparison of flat images would pass
    # whatever the decoders did.
    jpeg = tmp_path / f"n{width}x{height}.jpg"
    imaging.resize_and_encode(source, width, height, "jpeg", 90, jpeg,
                              resize=False)
    whole = _frame_decode(jpeg, tmp_path / "whole.png")
    x, y, w, h = 40, 30, 200, 150

    cropped = tmp_path / "cropped.png"
    imaging.crop(jpeg, Rect(x=x, y=y, width=w, height=h), cropped)

    _, _, decoded_rows = pixels.read_png_rgb(whole)
    _, _, cropped_rows = pixels.read_png_rgb(cropped)
    count, largest = _samples_differing(
        cropped_rows, _region(decoded_rows, x, y, w, h))

    if expect_agreement:
        assert count == 0, (
            f"{width}x{height} is {width * height:,} px, not over "
            f"{ONE_MILLION_PIXELS:,}, where a region decode was measured to "
            f"agree with the frame decode -- {count} samples differ, largest "
            f"{largest}. Read it as a crop failure before reading it as a "
            f"threshold that moved")
    else:
        assert count > 0, (
            f"{width}x{height} is {width * height:,} px, over "
            f"{ONE_MILLION_PIXELS:,}, where a region decode was measured to "
            f"disagree with the frame decode. It now agrees, so the "
            f"phenomenon has changed and the research record is stale")


# One reduction, one enlargement, one width-governed, one height-governed,
# and one that changes a single axis. The last is not a duplicate of the
# others and not a duplicate of BOTH_AXES above: it is the only row where an
# identity condition written as "either axis matches" would wrongly hand the
# source straight through.
SHAPES = [
    (2000, 1400, 1000, 700),
    (800, 600, 1600, 1200),
    (2000, 1400, 900, 500),
    (1400, 2000, 500, 900),
    (2000, 1400, 1000, 1400),
]


@pytest.mark.parametrize("sw,sh,dw,dh", SHAPES)
def test_both_axes_land_exactly(tmp_path, photo_fixture, sw, sh, dw, dh):
    """Neither axis may be derived from the other. BOTH_AXES above pins the
    shapes where the old tool's own derivation disagreed with the planner's
    arithmetic; these pin the shape classes -- reduction, enlargement,
    width-governed, height-governed, single-axis."""
    source = photo_fixture(tmp_path / "s.png", sw, sh)
    out = tmp_path / "o.png"
    imaging.resize_and_encode(source, dw, dh, "png", None, out, resize=True)
    assert imaging.probe(out)[:2] == (dw, dh)


# ---------- the encode ----------


def test_the_quality_number_reaches_the_encoder(tmp_path, photo_fixture):
    """The quality is `kCGImageDestinationLossyCompressionQuality` at N/100,
    and byte-identity with the tool this replaced would also have held if
    BOTH sides ignored the number. The sizes have to move, monotonically,
    with it."""
    src = photo_fixture(tmp_path / "s.png", 400, 300)
    sizes = []
    for quality in (20, 60, 90):
        out = tmp_path / f"q{quality}.heic"
        imaging.resize_and_encode(src, 400, 300, "heic", quality, out,
                                  resize=False)
        sizes.append(out.stat().st_size)
    assert sizes[0] < sizes[1] < sizes[2], sizes


def test_the_shipped_defaults_are_the_ones_that_were_measured(tmp_path,
                                                              photo_fixture):
    """The numbers the CoreGraphics migration must not have changed, asserted
    where the encoder is.

    `formats.py` is where they live and nothing here touches it; the point of
    repeating them is that this is the file that would notice a quality
    mapping quietly rescaled, and it should fail naming the value.
    """
    assert formats.DEFAULT_QUALITY == {"heic": 80, "jpeg": 90, "avif": 85,
                                       "png": None}

    # And heic 85 really does reproduce heic 80's file, which is the
    # quantization the default rests on. Asserted rather than assumed,
    # because if Apple's encoder ever stops doing it the comment in
    # formats.py stops being true.
    src = photo_fixture(tmp_path / "s.png", 400, 300)
    at = {}
    for quality in (80, 85):
        out = tmp_path / f"h{quality}.heic"
        imaging.resize_and_encode(src, 400, 300, "heic", quality, out,
                                  resize=False)
        at[quality] = out.read_bytes()
    assert at[80] == at[85], (
        "heic 85 no longer reproduces heic 80's file, so formats.py's reason "
        "for defaulting to 80 no longer holds")


ENCODE_CASES = [("jpeg", 90), ("jpeg", 80), ("heic", 80), ("heic", 85),
                ("heic", 90), ("avif", 85), ("png", None)]


@pytest.mark.parametrize("fmt,quality", ENCODE_CASES)
def test_the_bytes_are_the_format_that_was_asked_for(tmp_path, photo_fixture,
                                                     fmt, quality):
    """Named by UTI to ImageIO, and read back out of the bytes.

    Not out of the extension: the old tool without `-s format` kept the
    source's format whatever the output was called, and the mistake this
    guards against -- a UTI that ImageIO maps somewhere else -- would produce
    exactly that shape of wrong answer.
    """
    src = photo_fixture(tmp_path / "s.png", 200, 150)
    out = tmp_path / f"o.{formats.extension(fmt)}"
    imaging.resize_and_encode(src, 200, 150, fmt, quality, out, resize=False)
    expected = {"heic": "HEIF", "jpeg": "JPEG", "avif": "AVIF", "png": "PNG"}
    assert _format_of(out.read_bytes()) == expected[fmt]
    assert imaging.probe(out)[:2] == (200, 150)


def test_an_unknown_format_is_refused_by_name(tmp_path, photo_fixture):
    """It used to be the old tool exiting 13.

    Without the explicit refusal this still raises -- `tiff` is not a UTI,
    CoreFoundation builds the string happily and ImageIO returns NULL for it
    -- and the message would say only that a destination could not be
    created, which describes the symptom. So what is asserted is the
    SENTENCE, not the exception: deleting the check has to fail here.
    `public.tiff` is a real UTI ImageIO can write, and `tiff` is what a
    caller would actually pass, which is why the refusal is against our own
    four names rather than against CoreFoundation's answer.
    """
    src = photo_fixture(tmp_path / "s.png", 100, 100)
    out = tmp_path / "o.tiff"
    with pytest.raises(imaging.ImagingError) as caught:
        imaging.resize_and_encode(src, 100, 100, "tiff", 80, out, resize=False)
    message = str(caught.value)
    assert "not an output format" in message, message
    assert "tiff" in message and "heic" in message
    assert not out.exists()


def test_png_takes_no_quality(tmp_path, photo_fixture):
    """Lossless means no quality key, not quality 100. That the key is absent
    rather than 1.0 is `test_cg.test_no_options_dictionary_is_built_for_a_
    lossless_write`, which watches the call; this is the output side."""
    src = photo_fixture(tmp_path / "s.png", 400, 300)
    out = tmp_path / "o.png"
    imaging.resize_and_encode(src, 400, 300, "png", None, out, resize=False)
    assert imaging.probe(out)[:2] == (400, 300)


def test_no_staging_file_is_left_behind(tmp_path, photo_fixture):
    """The resample and the encode share one pass, so there is no
    intermediate to leave behind and no `.partial` name to coordinate with
    `execute.sweep_partials`."""
    src = photo_fixture(tmp_path / "s.png", 400, 300)
    out = tmp_path / "out" / "o.heic"
    out.parent.mkdir()
    imaging.resize_and_encode(src, 200, 150, "heic", 80, out, resize=True)
    assert [p.name for p in out.parent.iterdir()] == ["o.heic"]
    assert not list(tmp_path.glob("*.resized*"))


def test_a_resizing_encode_lands_on_both_axes(tmp_path, photo_fixture):
    """The fused call still uses both of the caller's numbers.

    An anisotropic target, because a proportional one is the shape where a
    dropped axis is invisible.
    """
    src = photo_fixture(tmp_path / "s.png", 2000, 1400)
    out = tmp_path / "o.heic"
    imaging.resize_and_encode(src, 900, 500, "heic", 80, out, resize=True)
    assert imaging.probe(out)[:2] == (900, 500)


def test_a_failing_encode_leaves_no_output_behind(tmp_path, photo_fixture):
    """Including a stale file from an earlier run, which is why the
    destination is cleared before the work rather than after it."""
    out = tmp_path / "o.heic"
    photo_fixture(tmp_path / "stale.png", 8, 8).replace(out)
    stale = out.stat().st_size
    with pytest.raises(imaging.ImagingError):
        imaging.resize_and_encode(tmp_path / "nope.png", 100, 100, "heic", 80,
                                  out, resize=False)
    assert not out.exists(), (
        f"{stale} bytes of an earlier run's output are still standing where "
        f"this run was asked to write")


# ---------- what the encode does NOT carry ----------
#
# The old tool copied a source's EXIF into its output; ImageIO's
# `CGImageDestinationAddImage` writes the picture and its colour space and
# nothing else. Measured on the same 400x300 source with one chunk removed at
# a time, the difference is entirely the metadata: for HEIF it is a second
# item -- an `Exif` item, an `iref cdsc` pointing at the picture, 66 bytes in
# `mdat` -- and the coded picture underneath was byte-identical. The two
# tests below are the half of that measurement that is about OUR output.


def _png_chunks(data: bytes):
    """(kind, whole chunk including length and CRC) for each chunk."""
    out = []
    offset = 8
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        out.append((kind, data[offset:offset + 12 + length]))
        offset += 12 + length
        if kind == b"IEND":
            break
    return out


def _with_exif(path, entries):
    """A noise PNG carrying an `eXIf` chunk holding `entries` in IFD0.

    Built here rather than with a colour-management tool, which is the whole
    point: `sips --matchTo`'s own eXIf happens to hold exactly what ImageIO
    re-synthesises, so a fixture tagged that way cannot show a divergence
    that depends on the payload. `entries` are (tag, SHORT value) pairs --
    0x0112 is Orientation.
    """
    base = pixels.write_png(Path(path).with_suffix(".base.png"), 400, 300,
                            noise=True).read_bytes()
    ifd = struct.pack(">2sHIH", b"MM", 42, 8, len(entries))
    for tag, value in entries:
        ifd += struct.pack(">HHIHH", tag, 3, 1, value, 0)
    ifd += struct.pack(">I", 0)
    chunk = (struct.pack(">I", len(ifd)) + b"eXIf" + ifd
             + struct.pack(">I", zlib.crc32(b"eXIf" + ifd) & 0xFFFFFFFF))
    rebuilt = bytearray(PNG_SIGNATURE)
    for kind, raw in _png_chunks(base):
        rebuilt += raw
        if kind == b"IHDR":
            rebuilt += chunk
    path = Path(path)
    path.write_bytes(bytes(rebuilt))
    assert b"eXIf" in [kind for kind, _ in _png_chunks(path.read_bytes())]
    return path


def _jpeg_segments(data: bytes):
    """(marker byte, whole segment) up to the scan, then (0xDA, the rest)."""
    out = []
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            break
        marker = data[offset + 1]
        if marker == 0xDA:
            out.append((marker, data[offset:]))
            break
        length = struct.unpack(">H", data[offset + 2:offset + 4])[0]
        out.append((marker, data[offset:offset + 2 + length]))
        offset += 2 + length
    return out


def _orientation(data: bytes):
    """The EXIF Orientation in a JPEG's APP1, or None.

    IFD0 only: Orientation is defined there, and a value in a sub-IFD would
    not be the one a viewer rotates by.
    """
    for marker, payload in _jpeg_segments(data):
        if marker != 0xE1 or payload[4:10] != b"Exif\x00\x00":
            continue
        tiff = payload[10:]
        if len(tiff) < 8:
            return None
        endian = ">" if tiff[:2] == b"MM" else "<"
        first = struct.unpack(endian + "I", tiff[4:8])[0]
        if first + 2 > len(tiff):
            return None
        count = struct.unpack(endian + "H", tiff[first:first + 2])[0]
        for index in range(count):
            entry = first + 2 + index * 12
            if entry + 12 > len(tiff):
                break
            tag = struct.unpack(endian + "H", tiff[entry:entry + 2])[0]
            if tag == 0x0112:
                return struct.unpack(endian + "H", tiff[entry + 8:entry + 10])[0]
    return None


def _edit_segment(data: bytes, marker: int, offset_in_payload: int, value):
    """`data` with one byte of one segment's payload changed."""
    out = bytearray(data)
    cursor = 2
    while cursor + 4 <= len(out):
        if out[cursor] != 0xFF:
            break
        found = out[cursor + 1]
        if found == 0xDA:
            break
        length = struct.unpack(">H", out[cursor + 2:cursor + 4])[0]
        if found == marker:
            at = cursor + 4 + offset_in_payload
            out[at] = value(out[at]) if callable(value) else value
            return bytes(out)
        cursor += 2 + length
    raise AssertionError(f"no {marker:#04x} segment to edit")


def test_our_jpeg_does_not_carry_the_source_exif(tmp_path):
    """An Orientation of 6 means "rotate 90 degrees on display", so it is the
    one EXIF tag whose loss a viewer could SEE. The old tool carried it into
    the output; we do not.

    The corpus makes that harmless -- censused over all 894 images, 245 JPEGs
    and 12 PNGs carry Orientation 1, one carries the invalid 0, 128 carry
    EXIF with no Orientation tag, and NONE carries a rotating value -- but
    harmless because of the corpus is not the same as absent, and this is
    where it is written down.

    Our JPEG does carry an APP1 Exif segment of its own: ImageIO synthesises
    one holding a single IFD0 entry, an ExifIFD pointer, 78 bytes of segment
    against the 90 the old tool wrote for a one-entry source. So the claim is
    not "no APP1" but "not the SOURCE's tags", and the reader is held to a
    positive control below rather than trusted to be looking at anything.
    """
    source = _with_exif(tmp_path / "oriented.png", [(0x0112, 6)])
    out = tmp_path / "cg.jpg"
    imaging.resize_and_encode(source, 400, 300, "jpeg", 90, out, resize=False)
    data = out.read_bytes()

    assert any(marker == 0xE1 and payload[4:10] == b"Exif\x00\x00"
               for marker, payload in _jpeg_segments(data)), (
        "our JPEG carries no APP1 Exif at all, so the reader below has "
        "nothing to look at and this test would pass vacuously")
    assert _orientation(data) is None, (
        f"our JPEG carries Orientation {_orientation(data)}; something "
        f"started copying the source's EXIF")

    # The positive control: the same reader finds the tag once it is really
    # there. The synthesised IFD0's single entry starts 16 bytes into the
    # APP1 payload -- 6 for `Exif\0\0`, 8 for the TIFF header, 2 for the
    # entry count -- and its tag is the first two bytes of it.
    planted = _edit_segment(_edit_segment(data, 0xE1, 16, 0x01),
                            0xE1, 17, 0x12)
    assert _orientation(planted) is not None, (
        "the Orientation reader cannot find a tag that is there, so its "
        "answer of None above means nothing")


def test_a_progressive_source_comes_back_baseline(tmp_path, photo_fixture):
    """Intended, measured, and not free: about 10-21% more bytes.

    The old tool inherited a source JPEG's progressive scan and ImageIO
    writes baseline. The pictures are the same picture -- same dimensions,
    same sampling factors -- but the entropy-coded streams are not
    comparable, so this was the one lossy case where byte identity was never
    reachable. ImageIO will write progressive if asked -- the fixture below
    is that request, and asked for it on the four progressive corpus sources
    it produced the old tool's own entropy-coded scan byte for byte on four
    of four, 0.81 to 11.34 MB of it, two of the four whole files included. So
    what is pinned here is a DECISION, and one that costs nothing but bytes:
    the encode takes the frame from its source and nothing else.
    """
    base = photo_fixture(tmp_path / "s.png", 800, 600)
    source = _write_progressive_jpeg(base, tmp_path / "progressive.jpg")

    out = tmp_path / "cg.jpg"
    imaging.resize_and_encode(source, 800, 600, "jpeg", 90, out, resize=False)

    assert not _jpeg_is_progressive(out.read_bytes()), (
        "our JPEG came back progressive; ImageIO's default changed, or "
        "something started asking it for one")
    assert imaging.probe(out)[:2] == (800, 600)


def _jpeg_is_progressive(data: bytes) -> bool:
    """True for a SOF2 frame header, False for SOF0, for anything else."""
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            return False
        marker = data[offset + 1]
        if marker == 0xDA:
            return False
        if marker == 0xC2:
            return True
        if marker == 0xC0:
            return False
        offset += 2 + struct.unpack(">H", data[offset + 2:offset + 4])[0]
    return False


def _cfdict(scope, pairs):
    """A CFDictionary of already-CF values. Test-side, for the fixture below."""
    count = len(pairs)
    keys = (c_void_p * count)(*[key for key, _ in pairs])
    values = (c_void_p * count)(*[value for _, value in pairs])
    return scope.own(_cg._checked(_cg._CF.CFDictionaryCreate(
        None, keys, values, count,
        ctypes.addressof(c_void_p.in_dll(_cg._CF, "kCFTypeDictionaryKeyCallBacks")),
        ctypes.addressof(c_void_p.in_dll(_cg._CF, "kCFTypeDictionaryValueCallBacks"))),
        "build a test options dictionary", "the progressive fixture"))


def _write_progressive_jpeg(source, out_path):
    """A progressive JPEG, written through ImageIO because nothing else here
    can.

    The old tool took its output's scan structure from its INPUT, so no PNG
    fixture produced one. ImageIO writes one when asked, which is the same
    measurement that makes the implementation's baseline output a decision
    rather than a limit: this fixture and that claim are the same fact.

    ctypes in `tests/` is fine -- Global Constraint 1 governs `paperhanger/`,
    and `test_cg.py` already reaches into the library handles. What is not
    fine is a fixture that quietly comes back baseline, so it is asserted.
    """
    with _cg.Scope() as scope:
        image = _cg.load(scope, source)
        jfif = _cfdict(scope, [(
            c_void_p.in_dll(_cg._IO_LIB, "kCGImagePropertyJFIFIsProgressive").value,
            c_void_p.in_dll(_cg._CF, "kCFBooleanTrue").value)])
        options = _cfdict(scope, [(
            c_void_p.in_dll(_cg._IO_LIB, "kCGImagePropertyJFIFDictionary").value,
            jfif)])
        _cg._write(scope, image, out_path, _cg.UTI["jpeg"], "progressive JPEG",
                   options)
    out_path = Path(out_path)
    assert _jpeg_is_progressive(out_path.read_bytes()), (
        "the progressive fixture came back baseline; ImageIO stopped "
        "honouring {JFIF: {IsProgressive: true}}, and the test above would "
        "pass while measuring nothing")
    return out_path


# ---------- normalization to sRGB ----------
#
# This function runs for EVERY source on the upscale path, not only for the
# formats upscayl-bin cannot read. `upscayl-bin` emits PNG with no ICC chunk,
# so without the conversion the encode step tags sRGB over numbers that were
# never in sRGB. The corpus census behind that, over all 895 entries of
# ~/Pictures/wallpaper: 714 sRGB IEC61966-2.1, 96 `c2`, 23 GIMP built-in
# sRGB, 21 untagged, 19 Adobe RGB (1998), 9 Generic Gray Gamma 2.2, 3 sRGB
# IEC61966-2-1 black scaled, 3 ProPhoto RGB, 2 Generic RGB, 2 sRGB, 1
# Calibrated RGB Colorspace, 1 iMac, and one entry that is not an image.
# Every one of them is a JPEG or a PNG, so a condition on the FORMAT would
# fire for none of them.
#
# That the draw which does this conversion is never skipped is
# `test_cg.test_normalize_never_skips_the_draw`.

PROFILE_DIR = Path("/System/Library/ColorSync/Profiles")

# None is the untagged case, which is not a formality: an untagged PNG is
# what `pixels.write_png` produces and what 21 corpus files are, and it has
# to come out TAGGED sRGB rather than left unmarked.
#
# "ProPhoto.icc" is what the plan named and no such file is installed; the
# ICC name for ProPhoto RGB is ROMM RGB, and `ROMM RGB.icc` is what the
# corpus's three ProPhoto files decode against.
NORMALIZE_PROFILES = [None, "AdobeRGB1998.icc", "ROMM RGB.icc",
                      "Display P3.icc", "Generic RGB Profile.icc"]

# The rows where the conversion has somewhere to move the numbers to.
WIDE_PROFILES = ["AdobeRGB1998.icc", "ROMM RGB.icc", "Display P3.icc"]


@pytest.mark.parametrize("profile", NORMALIZE_PROFILES)
def test_the_normalized_output_is_tagged_srgb(tmp_path, profiled_fixture,
                                              profile):
    """The whole point: upscayl-bin strips the ICC chunk, so the numbers have
    to be sRGB before it sees them -- and the untagged row matters as much as
    the tagged ones, because an untagged file is not an sRGB file until
    something says so."""
    source = profiled_fixture(tmp_path / "s.png", 600, 400, profile)
    out = tmp_path / "n.png"
    imaging.normalize_to_srgb_png(source, out)
    assert "sRGB" in _profile(out), _profile(out)


@pytest.mark.parametrize("profile", WIDE_PROFILES)
def test_normalize_moves_the_numbers(tmp_path, profiled_fixture, profile):
    """The premise the test above rests on. A conversion that did nothing
    would satisfy it for an sRGB source and for an untagged one, so at least
    one row has to show the pixels changing."""
    source = profiled_fixture(tmp_path / "s.png", 600, 400, profile)
    out = tmp_path / "n.png"
    imaging.normalize_to_srgb_png(source, out)
    _w, _h, before = read_png_rgb(source)
    _w, _h, after = read_png_rgb(out)
    assert before != after, (
        f"{profile} in and sRGB out, and not one pixel changed")


ALPHA_SHAPES = [
    ("rgba", pixels.write_rgba_png),
    ("grey+alpha", pixels.write_grey_alpha_png),
    ("colour-key", pixels.write_colour_key_png),
]


@pytest.mark.parametrize("name,writer", ALPHA_SHAPES)
def test_the_alpha_channel_survives_normalization(tmp_path, name, writer):
    """Twelve corpus PNGs carry an alpha channel. All twelve plan as band 3
    or band 4, so all twelve reach this function and none of them reaches the
    resampler from its original.

    A destination built with `kCGImageAlphaNoneSkipLast`, which is what the
    plan wrote, composites them onto the context's black ground and writes
    back opaque colour type 2. The colour-key row is the one that shows what
    that costs: a `tRNS` chunk names a COLOUR transparent, ImageIO expands it
    to a channel, and the composite turns (255, 0, 255) into (0, 0, 0).
    """
    source = writer(tmp_path / f"{name}.png", 400, 300)
    out = tmp_path / "n.png"
    imaging.normalize_to_srgb_png(source, out)
    assert pixels.read_ihdr(out)[3] == 6, (
        f"a {name} source came back at colour type "
        f"{pixels.read_ihdr(out)[3]}; the alpha channel is gone and the "
        f"upscaler would be handed pixels composited onto black")


def test_a_greyscale_source_comes_back_rgb(tmp_path, gradient_fixture):
    """The destination is sRGB, which is an RGB space, so a monochrome source
    is converted rather than carried. Ten corpus photographs are monochrome,
    one of them in the coverage sample."""
    source = gradient_fixture(tmp_path / "g.png", 400, 300)
    assert pixels.read_ihdr(source)[3] == 0
    out = tmp_path / "n.png"
    imaging.normalize_to_srgb_png(source, out)
    assert pixels.read_ihdr(out)[3] == 2


def test_a_flat_greyscale_source_is_not_normalized_to_black(tmp_path,
                                                            grayscale_fixture):
    """The failure that motivated `bitmap_format`, from the other side. An
    opaque grey source drawn into a MONOCHROME context with NoneSkipLast
    comes back entirely black; an sRGB context cannot reach that pairing, and
    this says so in pixels rather than by construction."""
    source = grayscale_fixture(tmp_path / "flat.png", 200, 150, value=128)
    out = tmp_path / "n.png"
    imaging.normalize_to_srgb_png(source, out)
    _w, _h, rows = read_png_rgb(out)
    assert set(rows[0]) != {(0, 0, 0)}, "the frame came back black"


def test_a_16_bit_source_comes_back_8_bit_from_normalize(tmp_path,
                                                         png16_fixture):
    """The one place this pipeline narrows depth, deliberately.

    `crop` and `resize_and_encode` keep 16 bits; this function drops them,
    because the only reader of its output is upscayl-bin, which emits 8-bit
    PNG. A 16-bit intermediate would be thrown away one step later at best
    and refused at worst. No corpus file is 16-bit.
    """
    source = png16_fixture(tmp_path / "s.png", 400, 300)
    assert pixels.read_ihdr(source)[2] == 16
    out = tmp_path / "n.png"
    imaging.normalize_to_srgb_png(source, out)
    assert pixels.read_ihdr(out)[2] == 8


def test_the_models_the_resampler_refuses_normalize_anyway(tmp_path,
                                                           cmyk_fixture):
    """The destination space is OURS, so the source's colour model is not a
    constraint here the way it is in `bitmap_context`.

    `bitmap_format` refuses indexed, CMYK, Lab and anything above 16 bits per
    component, because those cannot be a destination. They can all be a
    SOURCE of a draw into sRGB, and the tool this replaced handled them, so
    refusing them here would be a regression the corpus cannot see and a
    print-workflow CMYK JPEG can.
    """
    cmyk = cmyk_fixture(tmp_path / "c.jpg", 400, 300)
    out = tmp_path / "c-n.png"
    imaging.normalize_to_srgb_png(cmyk, out)
    assert imaging.probe(out)[:2] == (400, 300)
    assert pixels.read_ihdr(out)[3] in (2, 6)

    indexed = pixels.write_indexed_png(tmp_path / "i.png", 400, 300)
    indexed_out = tmp_path / "i-n.png"
    imaging.normalize_to_srgb_png(indexed, indexed_out)
    assert pixels.read_ihdr(indexed_out)[3] == 2, (
        "an indexed source came back indexed; the draw into sRGB is what "
        "expands the palette to truecolour")

    # And the contrast is real: the same file still fails a resample.
    with pytest.raises(imaging.ImagingError):
        imaging.resize_and_encode(cmyk, 200, 150, "png", None,
                                  tmp_path / "x.png", resize=True)


@pytest.mark.parametrize("fmt,suffix,quality", [
    ("jpeg", ".jpg", 90), ("heic", ".heic", 80), ("avif", ".avif", 85),
])
def test_any_container_comes_back_png(tmp_path, png_fixture, fmt, suffix,
                                      quality):
    """HEIC is the row this function was originally written for: upscayl-bin
    takes jpg, png and webp only, so a HEIC source reaches the model only
    because this step has already rewritten it. The others are here because
    the rule is the container's, not HEIC's."""
    base = png_fixture(tmp_path / "b.png", 320, 240)
    holder = tmp_path / f"h{suffix}"
    imaging.resize_and_encode(base, 320, 240, fmt, quality, holder,
                              resize=False)
    out = tmp_path / "n.png"
    imaging.normalize_to_srgb_png(holder, out)
    assert out.read_bytes()[:8] == PNG_SIGNATURE
    assert imaging.probe(out)[:2] == (320, 240)


def test_a_webp_named_jpg_comes_back_png(tmp_path, webp_fixture):
    """`snowy_forest_landscape_9522.jpg` really is a WebP, so the name on the
    file settles nothing. The conversion goes by the bytes."""
    source = webp_fixture(tmp_path / "s.jpg", 320, 240)
    out = tmp_path / "n.png"
    imaging.normalize_to_srgb_png(source, out)
    assert out.read_bytes()[:8] == PNG_SIGNATURE
    assert imaging.probe(out)[:2] == (320, 240)


def _rtrc(icc: Path):
    """The (g, a, b, c, d) of an ICC profile's parametric type-3 red TRC.

    Parsed out of the file, so the expectation below is computed from what the
    profile SAYS rather than from what any colour engine on this machine does
    with it.
    """
    data = icc.read_bytes()
    count = struct.unpack(">I", data[128:132])[0]
    for i in range(count):
        sig, offset, size = struct.unpack(
            ">4sII", data[132 + i * 12:132 + i * 12 + 12])
        if sig != b"rTRC":
            continue
        blob = data[offset:offset + size]
        assert blob[:4] == b"para", blob[:4]
        assert struct.unpack(">H", blob[8:10])[0] == 3, "not a type-3 curve"
        return [v / 65536.0 for v in struct.unpack(">5i", blob[12:32])]
    raise AssertionError(f"{icc} carries no rTRC")


def _to_linear(value: float, curve) -> float:
    """One device value through a parametric type-3 curve. ICC.1:2010 10.16."""
    g, a, b, c, d = curve
    return (a * value + b) ** g if value >= d else c * value


def _to_srgb(linear: float) -> float:
    """Linear light to an sRGB device value. IEC 61966-2-1."""
    if linear <= 0.0031308:
        return 12.92 * linear
    return 1.055 * linear ** (1 / 2.4) - 0.055


def _tagged(base, profile, out_path):
    """`base` tagged with a named ColorSync profile.

    The same `sips --matchTo` `tests/conftest.profiled_fixture` uses, and for
    the same reason: tagging a file with a real ICC profile needs a real
    colour-management implementation, this is test-only, and Global
    Constraint 1 governs `paperhanger/`, not `tests/`.
    """
    icc = PROFILE_DIR / profile
    if not icc.is_file():
        pytest.skip(f"colour profile not installed: {icc}")
    subprocess.run([sips_or_skip(), "--matchTo", str(icc), "-s", "format",
                    "png", str(base), "--out", str(out_path)],
                   check=True, capture_output=True)
    assert _profile(out_path), f"{out_path} came back untagged"
    return out_path


@pytest.mark.parametrize("profile", ["ITU-2020.icc", "ITU-709.icc"])
def test_the_itu_profiles_follow_the_curve_in_the_profile(tmp_path, profile):
    """The one profile class where the tool this replaced was the wrong one.

    Both profiles carry the BT.709 OETF as a parametric type-3 rTRC --
    g=2.22223, a=0.90967, b=0.09033, c=0.22223, d=0.08099. CoreGraphics
    follows it exactly; `sips --matchTo` applied a pure gamma 2.4 instead.
    The gap is not a constant and is largest in the shadows -- measured at
    the four levels below, both profiles alike, as (device value in the
    tagged file, what this writes, what `sips` wrote):

      28 -> 44 against 15      74 -> 89 against 64
      135 -> 146 against 128   203 -> 208 against 200

    which is 29, 25, 18 and 8 levels out of 255. Over a 200x150 noise image
    the mean absolute difference between the two outputs was 20.83 for
    ITU-2020 and 16.73 for ITU-709. ImageMagick's LittleCMS, a third
    implementation, agrees with CoreGraphics to the byte on the neutral axis,
    and the arithmetic below agrees with both.

    Read the right-hand column of those four rows against the levels the loop
    asks for -- 16, 64, 128, 200 -- and `sips` was handing back the numbers
    the file was built from. Its two legs were mutual inverses, which is
    exactly why a round trip cannot arbitrate this and the profile's own
    parameters have to. So the expectation here is computed from the ICC file
    and not from any engine's output: nothing in this test is a comparison
    between two implementations.

    No corpus file carries either profile: this is latent, and it is pinned
    so that a future attempt to close the gap has to argue with the profile
    rather than with a preference.

    Neutral values only, where the primaries cannot contribute: both profiles
    and sRGB are D65, so an equal-channel input maps to an equal-channel
    output whatever the matrix says.
    """
    icc = PROFILE_DIR / profile
    if not icc.is_file():
        pytest.skip(f"colour profile not installed: {icc}")
    curve = _rtrc(icc)

    for level in (16, 64, 128, 200):
        flat = pixels.write_png(tmp_path / f"flat{level}.png", 8, 8,
                                colour=(level, level, level))
        tagged = _tagged(flat, profile, tmp_path / f"t{level}.png")

        # What is actually in the tagged file, rather than what it was asked
        # for: the tagging ran through a colour conversion too.
        _w, _h, rows = read_png_rgb(tagged)
        device = rows[0][0][0] / 255.0
        expected = round(_to_srgb(_to_linear(device, curve)) * 255)

        ours = tmp_path / f"cg{level}.png"
        imaging.normalize_to_srgb_png(tagged, ours)
        _w, _h, got = read_png_rgb(ours)
        got = got[0][0][0]

        assert abs(got - expected) <= 2, (
            f"{profile} at device {rows[0][0][0]}: the profile's own curve "
            f"gives {expected} and this produced {got}")


@pytest.mark.parametrize("kind", ["missing", "directory", "not an image"])
def test_an_unreadable_source_fails_normalization(tmp_path, kind):
    """It used to be a zero exit with `not a valid file` on stderr, or an
    exit 13; it is now an ImagingError out of ImageIO naming the file. Same
    exception type, which is what `execute` catches per photo."""
    if kind == "missing":
        source = tmp_path / "nope.png"
    elif kind == "directory":
        source = tmp_path
    else:
        source = tmp_path / "note.txt"
        source.write_bytes(b"this is not an image\n")

    out = tmp_path / "n.png"
    with pytest.raises(imaging.ImagingError) as caught:
        imaging.normalize_to_srgb_png(source, out)
    assert str(source) in str(caught.value)
    assert not out.exists()


def test_a_stale_destination_cannot_stand_in_for_normalize_output(tmp_path):
    """An earlier run's file must not survive a run that wrote nothing.

    This is what `_run(produces=...)` did for this function while it was a
    subprocess -- it unlinked the destination before starting, so that a
    write which never happened could not be answered by a file that was
    already there. No subprocess runs now, so `normalize_to_srgb_png` clears
    it itself, exactly as `resize_and_encode` does.
    """
    out = tmp_path / "n.png"
    write_png(out, 64, 64)
    with pytest.raises(imaging.ImagingError):
        imaging.normalize_to_srgb_png(tmp_path / "nope.png", out)
    assert not out.exists(), (
        "an earlier run's output is still standing at the destination, "
        "where anything checking for a file would read it as this run's")


def test_an_unremovable_stale_normalize_output_still_raises_imaging_error(
        tmp_path):
    """The clearing unlink can itself fail, and a bare PermissionError is not
    this layer's exception. `execute` catches ImagingError per photo and
    carries on; anything else ends the run. The same shape Task 1 fixed in
    `write_png` and Task 5 in `resize_and_encode`."""
    room = tmp_path / "ro"
    room.mkdir()
    out = room / "n.png"
    write_png(out, 8, 8)
    room.chmod(0o500)
    try:
        with pytest.raises(imaging.ImagingError) as caught:
            imaging.normalize_to_srgb_png(tmp_path / "nope.png", out)
    finally:
        room.chmod(0o700)

    message = str(caught.value)
    assert out.exists(), "the premise: the stale file could not be removed"
    assert "still there" in message, message
    assert "Permission denied" in message, message
