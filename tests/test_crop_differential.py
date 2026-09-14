"""`crop` on CoreGraphics against the `sips` implementation it replaced.

The retained reference lives here, in `tests/`, and never in `paperhanger/`:
`_sips_crop_reference` is the old `crop` body, pad-and-shift branch and all,
copied verbatim the moment before it was deleted. Section 5 of the design
asks for exactly that -- a differential bar is only workable if the old
implementation survives as a test fixture rather than as a second production
path -- and it is deleted at the final swap.

TWO differences between the two implementations are real, and neither is a
regression:

  * A 16-BIT SOURCE. `sips` drops it to 8-bit and CoreGraphics keeps it.
    Pinned below, and asserted in both directions, so "the old one loses it"
    is a measurement rather than a claim.

    The design attributes this to the PAD, and calls it the one defect in the
    old path that was ours rather than Apple's. Measured here, that is wrong:
    on a 16-bit 200x200 PNG, `sips` returns 8 bits from the direct crop, from
    the crop at 0,0, from the pad alone and from a plain resample. Only a
    format convert with no pixel operation keeps 16. So the pad was not the
    cause, the defect was never ours, and the pin below names `sips` rather
    than the workaround.
  * A BASELINE JPEG OF MORE THAN 1,000,000 PIXELS. This one was not
    predicted, and it is not about cropping at all -- it is about DECODING.
    ImageIO decodes a REGION of such a file differently from the whole frame,
    and `sips` shows it as plainly as we do: `sips` cropping 400x300 at
    (40, 30) out of acrylic_7049.JPG differs from a plain `sips -s format
    png` of the same file in 212,413 of 360,000 samples, mean absolute error
    1.0592 out of 255, with no CoreGraphics anywhere in the measurement. The
    rect has to be strictly smaller than the frame -- a full-frame
    `--cropOffset 0 0` is byte-identical to a plain decode.
    Both implementations depart from the whole-frame decode, by comparable
    margins and not in the same direction. Measured on four corpus
    photographs at a 400x300 region, against each tool's own full decode:
    `sips` mean absolute error 1.038 / 0.314 / 1.383 / 0.351 out of 255,
    ours 0.934 / 0.332 / 1.256 / 0.417. Neither is the better decode; they
    are two region decodes of the same bytes.

    So BYTE-IDENTITY WAS NEVER REACHABLE on these files, and the design's
    bar could not have been met by a better binding. The reference is itself
    a region decode, so an implementation that matched the frame decode
    would sit FURTHER from `sips` than this one does.

    The corpus gate below measures it per file rather than tolerating it.
    Where the two sides differ, the control hands BOTH implementations the
    same already-decoded pixels -- `sips`' own PNG of the file -- and
    requires them to agree exactly. That says the crop is not what differs,
    which is the claim a crop differential is entitled to make. Anything the
    control cannot account for fails the gate.

Dimensions cannot settle any of this. The defect the whole migration exists
for -- `sips` cropping from the CENTRE when it is asked for the top -- returns
a region of exactly the requested size, so a size assertion passes against the
bug. `gradient_fixture` is what separates them: its value names its own source
row, so the pixel at (0, 0) of the output says which row came back.
"""

import subprocess
from pathlib import Path

import pytest

from paperhanger import imaging
from paperhanger.geometry import Rect
from tests import pixels
from tests.differential import compare

SIPS = "/usr/bin/sips"

# The one intended difference, pinned rather than tolerated.
#
# Every file in the corpus is 8-bit, so this never fires there -- it is
# pinned so that if it ever stops being the ONLY difference on such a file,
# the gate says so.
PINNED = {
    "16-bit source":
        "sips drops a 16-bit source to 8-bit on every path that touches "
        "pixels -- the direct crop, the crop at 0,0, the pad alone and a "
        "plain resample; only a format convert keeps 16. CoreGraphics "
        "preserves the depth.",
}

# The threshold measured for fact 8, in pixels. At it and below, a region
# decode of a baseline JPEG equals the whole-frame decode; above it, it does
# not. Both implementations turn at the same place.
#
# A ROUND DECIMAL NUMBER, not a power of two, which is not the guess anyone
# makes -- this constant said 1024 * 1024 until the space between 10**6 and
# 2**20 was sampled. 1000x1000 and 1250x800 are both exactly 1,000,000 and
# both agree; 1250x801 (1,001,250) and 1000x1002 (1,002,000) do not.
ONE_MILLION_PIXELS = 1_000_000


# ---------------------------------------------------------------------------
# The retained reference: `imaging.crop` as it stood before Task 3.
# ---------------------------------------------------------------------------

PAD_COLOUR = "FF00FF"


def _offset_is_ignored(rect, source_height: int) -> bool:
    """Whether sips would silently drop this --cropOffset -- imaging fact 6."""
    if rect.x != 0:
        return False
    return rect.y == 0 or rect.y + rect.height == source_height


def _crop_via_padding(source, rect, out_path, width: int, height: int) -> None:
    """Crop a rect sips would otherwise ignore, by moving it off the edge."""
    padded = Path(out_path).with_suffix(".padded.png")
    try:
        # -s format png FIRST: after --padColor it is silently dropped, and a
        # lossy pad bleeds magenta into the pixels we keep -- imaging fact 7.
        imaging._run([SIPS, "-s", "format", "png",
                      "-p", str(height + 2), str(width + 2),
                      "--padColor", PAD_COLOUR,
                      str(source), "--out", str(padded)], produces=padded)
        imaging._run([SIPS, "-s", "format", "png",
                      "-c", str(rect.height), str(rect.width),
                      "--cropOffset", str(rect.y + 1), str(rect.x + 1),
                      str(padded), "--out", str(out_path)], produces=out_path)
    finally:
        padded.unlink(missing_ok=True)


def _sips_crop_reference(source, rect, out_path) -> None:
    """The old `crop`, verbatim, minus the checks the new one still does.

    The suffix refusal and the bounds check are NOT copied. They are contract,
    not implementation: they survived the swap unchanged and are tested in
    `test_imaging.py` against the real `crop`. A second copy here would only
    drift.
    """
    out_path = Path(out_path)
    width, height, _ = imaging.probe(source)
    if _offset_is_ignored(rect, height):
        _crop_via_padding(source, rect, out_path, width, height)
        return
    imaging._run([SIPS, "-s", "format", "png",
                  "-c", str(rect.height), str(rect.width),
                  "--cropOffset", str(rect.y), str(rect.x),
                  str(source), "--out", str(out_path)], produces=out_path)


def _reference(rect):
    return lambda source, out_path: _sips_crop_reference(source, rect, out_path)


def _replacement(rect):
    return lambda source, out_path: imaging.crop(source, rect, out_path)


def _decode_to_png(source, out_path) -> Path:
    """`sips`' own plain decode of a file. The pixels every other step sees."""
    imaging._run([SIPS, "-s", "format", "png", str(source),
                  "--out", str(out_path)], produces=out_path)
    return Path(out_path)


# ---------------------------------------------------------------------------
# The two shapes sips gets wrong, asserted in PIXELS.
# ---------------------------------------------------------------------------


def _top_left_value(path) -> int:
    """The greyscale value at (0, 0): which SOURCE ROW came back."""
    _width, _height, rows = pixels.read_png_grey(path)
    return rows[0][0]


def _source_row_value(path, y: int) -> int:
    _width, _height, rows = pixels.read_png_grey(path)
    return rows[y][0]


def _bit_depth(path) -> int:
    return pixels.read_ihdr(path)[2]


def test_crop_at_origin_returns_the_top_region(tmp_path, gradient_fixture):
    """Origin 0,0 is the case sips gets wrong. Check PIXELS, not dimensions.

    sips returns the centred slice at the correct size, so a dimension
    assertion passes against the bug. Only the pixels distinguish them: the
    fixture's value names its own source row, so (0, 0) of a correct top crop
    is row 0 and (0, 0) of the centred one is row 100.
    """
    src = gradient_fixture(tmp_path / "g.png", 40, 300)
    out = tmp_path / "top.png"
    imaging.crop(src, Rect(x=0, y=0, width=40, height=100), out)
    assert _top_left_value(out) == _source_row_value(src, 0)


def test_crop_flush_to_the_bottom_returns_the_bottom_region(tmp_path,
                                                            gradient_fixture):
    """The second shape: flush with the bottom edge, where sips drops the crop
    entirely and hands back the whole source. Pixels again, because the size
    check DOES catch that one and would hide the centred case beside it."""
    src = gradient_fixture(tmp_path / "g.png", 40, 300)
    out = tmp_path / "bot.png"
    imaging.crop(src, Rect(x=0, y=200, width=40, height=100), out)
    assert _top_left_value(out) == _source_row_value(src, 200)


def test_the_gradient_would_notice_a_centred_crop(tmp_path, gradient_fixture):
    """Guards the two guards above: the reference really does return the wrong
    rows for the origin rect, so those assertions are not passing because
    every crop of this fixture happens to look alike."""
    src = gradient_fixture(tmp_path / "g.png", 40, 300)
    centred = tmp_path / "raw.png"
    subprocess.run([SIPS, "-s", "format", "png", "-c", "100", "40",
                    "--cropOffset", "0", "0", str(src), "--out", str(centred)],
                   check=True, capture_output=True)
    assert _top_left_value(centred) != _source_row_value(src, 0), (
        "sips now honours --cropOffset 0 0; fact 6 has changed and the "
        "reference's pad branch is no longer exercising what it claims to"
    )


def test_the_reference_still_needs_its_pad(tmp_path):
    """A canary on the defect the retained reference works around.

    It used to guard `imaging.crop`. It guards the reference now: if Apple
    ever fixes this, the pad branch stops being the thing the corpus gate
    thinks it is comparing against, and this says so.
    """
    marker = (240, 30, 200)
    rect = Rect(x=0, y=120, width=300, height=80)      # flush with the bottom
    source = pixels.write_marked_png(tmp_path / "m.png", 400, 200, rect,
                                     marker=marker)
    out = tmp_path / "raw.png"
    subprocess.run([SIPS, "-c", "80", "300", "--cropOffset", "120", "0",
                    str(source), "--out", str(out)],
                   check=True, capture_output=True)
    assert imaging.probe(out)[:2] == (400, 200), "sips now honours the offset"


# ---------------------------------------------------------------------------
# The pinned exception, asserted in both directions.
# ---------------------------------------------------------------------------


def test_a_sixteen_bit_source_keeps_its_depth(tmp_path, png16_fixture):
    """The pinned exception, asserted rather than assumed."""
    src = png16_fixture(tmp_path / "deep.png", 200, 200)
    assert _bit_depth(src) == 16
    out = tmp_path / "cut.png"
    imaging.crop(src, Rect(x=0, y=0, width=200, height=100), out)
    assert _bit_depth(out) == 16


@pytest.mark.parametrize("rect,branch", [
    (Rect(x=0, y=0, width=200, height=100), "the padded branch"),
    (Rect(x=20, y=40, width=160, height=100), "the direct branch"),
])
def test_the_reference_loses_the_depth_on_either_branch(tmp_path,
                                                        png16_fixture,
                                                        rect, branch):
    """The other half of the same pin. Without this, PINNED records a
    difference that might not exist, and the gate would be excusing nothing.

    BOTH branches, because the design says the pad is what loses the depth
    and the measurement says otherwise. Removing the pad from the reference
    does not make this test green -- which is how the misattribution was
    found: a mutation that switched the reference to its direct branch left
    this passing.
    """
    src = png16_fixture(tmp_path / "deep.png", 200, 200)
    out = tmp_path / "cut.png"
    _sips_crop_reference(src, rect, out)
    assert _bit_depth(out) == 8, (
        f"sips no longer downconverts on {branch}; "
        f"PINNED[{'16-bit source'!r}] has stopped being true"
    )


def test_the_harness_sees_the_pinned_difference(tmp_path, png16_fixture):
    """And the differential harness REPORTS it, rather than comparing the two
    at 8 bits and finding them equal. A pin nobody would have noticed is not
    a pin."""
    src = png16_fixture(tmp_path / "deep.png", 200, 200)
    rect = Rect(x=0, y=0, width=200, height=100)
    result = compare(_reference(rect), _replacement(rect), src, tmp_path)
    assert result is not None and "bits per channel" in result, result


# ---------------------------------------------------------------------------
# Fact 8: a region decode is not the whole-frame decode. BOTH sides show it.
# ---------------------------------------------------------------------------


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


def _baseline_jpeg(tmp_path, photo_fixture, width, height):
    """A baseline JPEG of exactly these dimensions, with detail in it.

    Detail is required. A flat region decodes to the same numbers under any
    IDCT or chroma upsampling, so a comparison of flat images would pass
    whatever the decoders did.
    """
    source = photo_fixture(tmp_path / f"n{width}x{height}.png", width, height)
    jpeg = tmp_path / f"n{width}x{height}.jpg"
    subprocess.run([SIPS, "-s", "format", "jpeg", str(source),
                    "--out", str(jpeg)], check=True, capture_output=True)
    return jpeg


def _sips_crop_to(jpeg, x, y, w, h, out_path):
    subprocess.run([SIPS, "-s", "format", "png", "-c", str(h), str(w),
                    "--cropOffset", str(y), str(x), str(jpeg),
                    "--out", str(out_path)], check=True, capture_output=True)
    return out_path


@pytest.mark.parametrize("width,height,expect_agreement", [
    (1000, 1000, True),     # 1,000,000 px exactly -- at the line
    (1250, 801, False),     # 1,001,250 px -- 1,250 px past it
])
@pytest.mark.parametrize("who", ["sips", "CoreGraphics"])
def test_a_region_decode_of_a_big_baseline_jpeg_is_not_the_frame_decode(
        tmp_path, photo_fixture, who, width, height, expect_agreement):
    """ImageIO decodes a REGION of a baseline JPEG differently from the whole
    frame, once the file passes a million pixels. Both sides show it.

    This is the difference that cost the corpus gate its clean run, and it is
    not about cropping: the region really is the one that was asked for, and
    only the last bit or two of each sample moves. It is parametrised over
    both implementations on purpose -- pinning it as a `sips` defect alone
    would be wrong, and would leave the reader thinking the replacement
    escaped it.

    The two sizes bracket the threshold within 1,250 pixels of area, and that
    tightness is the point: the constant read 1024 * 1024 until the space
    between 10**6 and 2**20 was sampled, and every measurement taken before
    that reproduced digit for digit while naming the wrong number. Measured
    over the 200x150 region, samples differing of 90,000:

        1000x1000  1,000,000 px   sips 0,      CoreGraphics 0
        1250x801   1,001,250 px   sips 39,868, CoreGraphics 86,423

    Noise is the worst case by a wide margin; on real corpus photographs the
    same comparison is a mean absolute error under 1.4 out of 255 for both.

    DO NOT DELETE THE `expect_agreement=True` CASE AS REDUNDANT COVERAGE.
    It is the backstop under the corpus gate, and it is the only test in this
    suite that makes an ABSOLUTE claim about a crop below the threshold --
    everything else about JPEG crops is relative, comparing the two
    implementations to each other.

    That matters because the corpus gate's control is conditional by
    construction: when the two sides differ it re-runs them against a decoded
    PNG, so a crop bug that fires ONLY on the un-decoded source is invisible
    to it. Measured, not imagined -- a one-row origin error keyed on
    `Path(source).suffix != ".png"` was injected against this suite:

        uv run pytest -m corpus -k crop     1 passed  (5m31s)  -- GREEN
        uv run pytest                       1 failed of 647    -- RED

    and the single failure is this test at `expect_agreement=True`. Nothing
    else in 647 tests notices. It catches what the control cannot because
    here the two implementations must agree with a frame decode they cannot
    both be wrong about, on a source that is not already a PNG. Remove it and
    that hole reopens silently.
    """
    jpeg = _baseline_jpeg(tmp_path, photo_fixture, width, height)
    whole = _decode_to_png(jpeg, tmp_path / "whole.png")
    x, y, w, h = 40, 30, 200, 150

    cropped = tmp_path / "cropped.png"
    if who == "sips":
        _sips_crop_to(jpeg, x, y, w, h, cropped)
    else:
        imaging.crop(jpeg, Rect(x=x, y=y, width=w, height=h), cropped)

    _, _, decoded_rows = pixels.read_png_rgb(whole)
    _, _, cropped_rows = pixels.read_png_rgb(cropped)
    count, largest = _samples_differing(
        cropped_rows, _region(decoded_rows, x, y, w, h))

    if expect_agreement:
        assert count == 0, (
            f"{width}x{height} is {width * height:,} px, not over "
            f"{ONE_MILLION_PIXELS:,}, where {who} was measured to agree with "
            f"the frame decode -- {count} samples differ, largest {largest}. "
            f"This is the backstop under the corpus gate, whose control "
            f"cannot see a crop bug that fires only on an un-decoded source; "
            f"read it as a crop failure before reading it as a threshold "
            f"that moved")
    else:
        assert count > 0, (
            f"{width}x{height} is {width * height:,} px, over "
            f"{ONE_MILLION_PIXELS:,}, where {who} was measured to disagree "
            f"with the frame decode. It now agrees, so fact 8 has changed and "
            f"the corpus gate's control is accounting for something else")


def test_the_two_crops_agree_once_the_decode_is_out_of_the_way(
        tmp_path, photo_fixture):
    """The tier-1 twin of the corpus control, and the claim the whole gate
    rests on: hand both implementations the SAME already-decoded pixels and
    they produce the same file. The crop is not what differs on a JPEG."""
    jpeg = _baseline_jpeg(tmp_path, photo_fixture, 1024, 1024)
    decoded = _decode_to_png(jpeg, tmp_path / "decoded.png")
    rect = Rect(x=40, y=30, width=200, height=150)

    assert compare(_reference(rect), _replacement(rect), jpeg,
                   tmp_path / "on-the-jpeg") is not None, (
        "the JPEG decode no longer separates them, so this test is not "
        "measuring what it claims to")
    assert compare(_reference(rect), _replacement(rect), decoded,
                   tmp_path / "on-the-decode") is None


# ---------------------------------------------------------------------------
# Tier 1 differential: generated fixtures, every commit.
# ---------------------------------------------------------------------------


def _fixture_cases(tmp_path, request):
    """One of each input shape the harness distinguishes, at one rect each."""
    return [
        ("origin, RGB", request.getfixturevalue("photo_fixture")(
            tmp_path / "rgb.png", 400, 300), Rect(0, 0, 400, 100)),
        ("flush bottom, RGB", request.getfixturevalue("photo_fixture")(
            tmp_path / "rgb2.png", 400, 300), Rect(0, 200, 400, 100)),
        ("interior, RGB", request.getfixturevalue("photo_fixture")(
            tmp_path / "rgb3.png", 400, 300), Rect(120, 90, 160, 120)),
        ("greyscale", request.getfixturevalue("gradient_fixture")(
            tmp_path / "grey.png", 400, 250), Rect(0, 0, 400, 100)),
        ("interlaced", request.getfixturevalue("interlaced_fixture")(
            tmp_path / "adam7.png", 400, 300, True), Rect(0, 0, 400, 100)),
    ]


def test_crop_matches_the_reference_on_generated_fixtures(tmp_path, request):
    """The tier-1 half of the gate: the same comparison, every commit.

    Lossless sources only. A JPEG here would measure fact 8 rather than the
    crop, and fact 8 has its own tests above.
    """
    differences = []
    for name, source, rect in _fixture_cases(tmp_path, request):
        result = compare(_reference(rect), _replacement(rect), source,
                         tmp_path / f"case-{name.replace(', ', '-')}")
        if result is not None:
            differences.append(f"{name}: {result}")
    assert not differences, "\n".join(differences)


def test_that_comparison_can_fail(tmp_path, photo_fixture):
    """Guards the gate. Six silent assertions have been caught in this project;
    a differential that compared an implementation against itself, or read a
    missing file as agreement, would be the seventh. Move the rect by one
    pixel on one side only and the harness must say so."""
    source = photo_fixture(tmp_path / "rgb.png", 400, 300)
    rect = Rect(0, 0, 400, 100)
    off_by_one = Rect(0, 1, 400, 100)
    result = compare(_reference(rect), _replacement(off_by_one), source,
                     tmp_path)
    assert result is not None and "pixels differ" in result, result


# ---------------------------------------------------------------------------
# Tier 2: the 27-image corpus sample.
# ---------------------------------------------------------------------------


def _corpus_rect(source):
    """The top third, full width: x == 0 and y == 0.

    The shape `sips` mis-crops, the shape `geometry.horizontal_thirds`
    produces for every real desktop plan, and the reason the pad existed. One
    rect rather than three because the control below costs a full decode of
    images up to 74 Mpx.
    """
    width, height, _ = imaging.probe(source)
    return Rect(x=0, y=0, width=width, height=height // 3)


@pytest.mark.corpus
def test_crop_matches_sips_over_the_sample(corpus_sample, tmp_path):
    """The gate, over real images: wide-gamut profiles, the file whose
    extension lies, the HEIC source, the greyscale JPEG, the GIF.

    A difference is not tolerated here, it is ACCOUNTED FOR. Where the two
    sides disagree, the control re-runs the reference against `sips`' own
    plain decode of the same file -- and our output has to equal that
    exactly. That turns fact 8 from an excuse into a measurement: it says our
    crop is the right region taken from the pixels the rest of the pipeline
    sees, and it leaves every other kind of difference failing.

    No corpus file is 16-bit, so PINNED never fires here. It is carried into
    the message so that a 16-bit file arriving in the sample is read as the
    known exception rather than as a new defect.
    """
    failures = []
    decoder_disagreements = []

    for source in sorted(Path(corpus_sample).iterdir()):
        if imaging.probe(source) is None:
            continue
        rect = _corpus_rect(source)
        room = tmp_path / f"cmp-{source.stem}"
        result = compare(_reference(rect), _replacement(rect), source, room)
        if result is None:
            continue

        decoded = _decode_to_png(source, room / "decoded.png")
        control = compare(_reference(rect), _replacement(rect), decoded,
                          room / "control")
        decoded.unlink(missing_ok=True)
        if control is None:
            decoder_disagreements.append(f"{source.name}: {result}")
        else:
            failures.append(f"{source.name}\n    on the file:  {result}\n"
                            f"    on its decode: {control}")

    assert not failures, (
        "crop diverged from the sips reference on files where sips agrees "
        "with its own decode, so the crop is what differs. Pinned exceptions "
        f"({', '.join(PINNED)}) do not cover these:\n" + "\n".join(failures))

    # Fact 8 must still be the thing being accounted for, and it must still
    # be there: an empty list would mean either that Apple fixed it -- in
    # which case the control above has quietly stopped doing anything -- or
    # that the comparison broke.
    assert decoder_disagreements, (
        "not one corpus file showed the sips decoder disagreement, which "
        "every baseline JPEG of a megapixel or more was measured to show. "
        "Either fact 8 is fixed or this gate has stopped comparing anything")


@pytest.mark.corpus
def test_the_corpus_control_is_not_vacuous(corpus_sample, tmp_path):
    """Guards the control. If `compare` against the decoded copy returned None
    for everything, the gate above would excuse any difference at all.

    Same shape as the control, with the replacement given a rect one row
    down. It must report a difference."""
    source = next(path for path in sorted(Path(corpus_sample).iterdir())
                  if imaging.probe(path) is not None)
    rect = _corpus_rect(source)
    decoded = _decode_to_png(source, tmp_path / "decoded.png")
    moved = Rect(x=rect.x, y=rect.y + 1, width=rect.width, height=rect.height)
    result = compare(_reference(rect), _replacement(moved), decoded, tmp_path)
    assert result is not None, (
        f"the control returned agreement for two different rects of "
        f"{source.name}; it cannot account for anything")
