"""The resample gate: CoreGraphics against `sips --resampleHeightWidth`.

Three questions, and the third is the one that changed the implementation.

WHICH SHAPES. Every row of SHAPES is a shape where `sips` could NOT have
derived the second axis from the first, because a shape where it could does
not discriminate: 3840x2160 -> 8533x4800 is such a shape, and it is why the
old by-height test in test_imaging.py passed a mutation that dropped an axis.
One reduction, one enlargement, one width-governed, one height-governed, and
one that holds a single axis -- the last exists so that an identity condition
written with `or` instead of `and` fails here rather than in production.

WHICH BAR. `tests/differential.compare` decides: decoded pixels, plus the
colour type, channel count and bit depth that give those pixels their
meaning. Not whole files -- `sips` synthesises PNG ancillary chunks ImageIO
does not emit, and both tools stamp the current second into an ICC profile
header, so a whole-file gate would be partly measuring a clock. Global
Constraint 10 has the counts.

WHAT HAPPENS AT IDENTITY, which is a fifth of the production resamples and
was where the two implementations were furthest apart. `render` asks for the
source's own dimensions 577 times in a 2734-resample corpus run, because band
4 renders the 4x frame at scale 4 and `resize = needs_resize or scale != 1`
computes True for a resample that changes nothing. Drawing those at 1:1 is
not a no-op:

  * over 63 corpus photographs at their own dimensions, the skip's `IDAT`
    equals `sips`' on 63 of 63 and the draw's on 11 of 63;
  * a 400x300 RGBA source at alpha 128 comes back from the draw differing
    from `sips` on 120,000 of 480,000 samples, every one by 1, because the
    destination bitmap is premultiplied and the encode has to undo it;
  * peak RSS on a 7680x5120 frame: `sips` 332.1 MiB, the draw 499.2, the
    skip 351.0.

So the implementation skips the draw when the dimensions already match, and
the tests below pin both halves of that: that it skips, and that skipping is
what makes the identity cases agree. The condition is the dimensions and
nothing else, which is true HERE because the destination bitmap is built out
of the source's own colour space -- the draw converts nothing. It is NOT true
of a draw into a colour space of our choosing: `normalize_to_srgb_png` is
exactly that, and skipping it there would delete the conversion.
"""

import shutil
import subprocess
from functools import partial
from pathlib import Path

import pytest

from paperhanger import _cg, execute, imaging
from tests import pixels
from tests.differential import compare

# One reduction, one enlargement, one width-governed, one height-governed,
# and one that changes a single axis. The last is not a duplicate: it is the
# only row where an identity test written as "either axis matches" would
# wrongly hand the source straight through.
SHAPES = [
    (2000, 1400, 1000, 700),
    (800, 600, 1600, 1200),
    (2000, 1400, 900, 500),
    (1400, 2000, 500, 900),
    (2000, 1400, 1000, 1400),
]


def _sips_resize(source, out_width, out_height, out_path):
    """The reference: what this project shipped before Task 4.

    Both axes explicit -- imaging fact 3 -- and `-s format` ahead of the
    paths, which is fact 7: placed later it is silently dropped and the
    output keeps the source's format whatever the name says.
    """
    subprocess.run(
        [imaging.SIPS, "--resampleHeightWidth", str(out_height), str(out_width),
         "-s", "format", "png", str(source), "--out", str(out_path)],
        check=True, capture_output=True,
    )


def _reference(out_width, out_height):
    def old(source, out_path):
        _sips_resize(source, out_width, out_height, out_path)
    return old


def _replacement(out_width, out_height):
    def new(source, out_path):
        imaging.resize_and_encode(source, out_width, out_height, "png", None,
                                  out_path, resize=True)
    return new


# ---------------------------------------------------------------------------
# The shapes, resampled.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sw,sh,dw,dh", SHAPES)
def test_resize_matches_sips_exactly(tmp_path, photo_fixture, sw, sh, dw, dh):
    """Noisy source on purpose: a flat colour resamples to the same answer
    under every algorithm, including one that dropped interpolation."""
    source = photo_fixture(tmp_path / f"s{sw}x{sh}.png", sw, sh)
    assert compare(_reference(dw, dh), _replacement(dw, dh), source,
                   tmp_path) is None


@pytest.mark.parametrize("sw,sh,dw,dh", SHAPES)
def test_both_axes_land_exactly(tmp_path, photo_fixture, sw, sh, dw, dh):
    """Neither axis may be derived from the other."""
    source = photo_fixture(tmp_path / "s.png", sw, sh)
    out = tmp_path / "o.png"
    imaging.resize_and_encode(source, dw, dh, "png", None, out, resize=True)
    assert imaging.probe(out)[:2] == (dw, dh)


def test_that_comparison_can_fail(tmp_path, photo_fixture):
    """Guards the gate. Seven silent assertions have been caught in this
    project; a differential that read agreement out of two runs of the same
    thing would be the eighth. One pixel of difference in the target height
    is the smallest thing the harness must still report."""
    source = photo_fixture(tmp_path / "s.png", 2000, 1400)
    result = compare(_reference(1000, 700), _replacement(1000, 699), source,
                     tmp_path)
    assert result is not None, "the harness accepted a 1000x699 resample as " \
                               "equal to a 1000x700 one"


# ---------------------------------------------------------------------------
# Interpolation. Asserted as a CALL, because no output can tell it apart.
# ---------------------------------------------------------------------------


def test_interpolation_is_high_not_default(tmp_path, photo_fixture,
                                           monkeypatch):
    """Remove the SetInterpolationQuality call and this must fail.

    Default measured identical to High on every shape tried, so a comparison
    of outputs cannot distinguish them -- and Default is Apple's to redefine
    while High is a name. Assert the call.
    """
    seen = []
    real = _cg._CG_LIB.CGContextSetInterpolationQuality
    monkeypatch.setattr(_cg._CG_LIB, "CGContextSetInterpolationQuality",
                        lambda ctx, q: seen.append(q) or real(ctx, q))
    source = photo_fixture(tmp_path / "s.png", 400, 300)
    imaging.resize_and_encode(source, 200, 150, "png", None, tmp_path / "o.png",
                              resize=True)
    assert seen == [_cg.kCGInterpolationHigh]


# ---------------------------------------------------------------------------
# Identity: the 577 resamples that resample nothing.
# ---------------------------------------------------------------------------


def _count_draws(monkeypatch):
    calls = []
    real = _cg._CG_LIB.CGContextDrawImage
    monkeypatch.setattr(_cg._CG_LIB, "CGContextDrawImage",
                        lambda ctx, rect, image: calls.append(rect)
                        or real(ctx, rect, image))
    return calls


def test_an_identity_resample_does_not_draw(tmp_path, photo_fixture,
                                            monkeypatch):
    """The skip, observed where it happens rather than inferred from output.

    A 1:1 draw and a skip produce the same PIXELS for a source with no alpha,
    so for most inputs no comparison of files can see which one ran. This
    watches the call.
    """
    calls = _count_draws(monkeypatch)
    source = photo_fixture(tmp_path / "s.png", 400, 300)
    imaging.resize_and_encode(source, 400, 300, "png", None, tmp_path / "o.png",
                              resize=True)
    assert calls == [], "the source was already 400x300 and it was drawn anyway"
    assert imaging.probe(tmp_path / "o.png")[:2] == (400, 300)


def test_a_resample_that_changes_a_single_axis_still_draws(tmp_path,
                                                           photo_fixture,
                                                           monkeypatch):
    """The other half, and the reason the condition is `and`, not `or`.

    Without this, an identity test written as "either axis already matches"
    passes every test above and hands 2000x1400 back for a 1000x1400 plan.
    """
    calls = _count_draws(monkeypatch)
    source = photo_fixture(tmp_path / "s.png", 400, 300)
    imaging.resize_and_encode(source, 400, 150, "png", None, tmp_path / "o.png",
                              resize=True)
    assert len(calls) == 1, "one axis changed and nothing was drawn"


# Every colour shape the binding layer distinguishes, as (name, writer,
# runs at identity, runs reduced). Both columns are needed and they catch
# different things: the destination bitmap's alpha info is only chosen on
# the REDUCING path, where a hardcoded `kCGImageAlphaNoneSkipLast` renders
# an opaque greyscale source entirely black -- and the same wrong pairing
# round-trips correctly at 1:1, which is how it survived measurement twice.
# The identity column is where the skip is measured against `sips`.
#
# Indexed reduces to an ImagingError rather than an image, and 16-bit keeps
# its depth where `sips` drops it; each has its own test below saying so.
#
# The two writers that take a `noise` flag are given it: a flat colour
# resamples to the same answer under every algorithm, so a flat row would
# pass against an implementation that interpolated wrongly. The greyscale
# writer needs no flag -- its default is a ramp down the frame.
COLOUR_SHAPES = [
    ("rgb", partial(pixels.write_png, noise=True), True, True),
    ("greyscale", pixels.write_grey_png, True, True),
    ("rgba", pixels.write_rgba_png, True, True),
    ("grey+alpha", pixels.write_grey_alpha_png, True, True),
    ("interlaced", partial(pixels.write_interlaced_png, noise=True),
     True, True),
    ("16-bit", pixels.write_png16, True, False),
    ("indexed", pixels.write_indexed_png, True, False),
]


@pytest.mark.parametrize("out_width,out_height,column", [
    (400, 300, "identity"), (200, 150, "reduced"),
])
def test_every_colour_shape_matches_sips(tmp_path, out_width, out_height,
                                         column):
    """All seven at 1:1 and all five that reduce, so a failing shape is named.

    The interlaced row is the one that does not come back in the same
    container: `sips` preserves Adam7 and ImageIO does not. The harness
    decodes both and compares pixels, which is the bar Global Constraint 10
    settled on for exactly this reason.
    """
    differences = []
    for name, writer, at_identity, reduced in COLOUR_SHAPES:
        if not (at_identity if column == "identity" else reduced):
            continue
        stem = name.replace("+", "-")
        source = writer(tmp_path / f"{stem}.png", 400, 300)
        result = compare(_reference(out_width, out_height),
                         _replacement(out_width, out_height), source,
                         tmp_path / f"{column}-{stem}")
        if result is not None:
            differences.append(f"{name}: {result}")
    assert not differences, "\n".join(differences)


@pytest.mark.parametrize("name,writer,differing", [
    ("rgba", pixels.write_rgba_png, 120000),
    ("grey+alpha", pixels.write_grey_alpha_png, 60400),
])
def test_drawing_at_identity_is_what_diverged(tmp_path, monkeypatch, name,
                                              writer, differing):
    """The measurement the skip rests on, run as a test.

    Forces the draw by lying to the identity check -- the target dimensions
    are unchanged, so this is the same 1:1 draw the implementation used to
    perform -- and requires that it DISAGREE with `sips`. If a future
    CoreGraphics makes the 1:1 draw exact, this test fails and the skip
    becomes an optimisation rather than a correction; that is worth being
    told about, because the docstrings claim otherwise.

    Alpha-bearing sources only: the draw's damage here is the premultiply
    round trip, and an opaque source has none.
    """
    monkeypatch.setattr(_cg, "dimensions", lambda image: (-1, -1))
    source = writer(tmp_path / f"{name.replace('+', '-')}.png", 400, 300)
    result = compare(_reference(400, 300), _replacement(400, 300), source,
                     tmp_path)
    assert result is not None, (
        "a 1:1 draw agreed with sips on an alpha-bearing source, so the "
        "skip is no longer correcting anything")
    assert f"{differing} of" in result, result


def test_an_indexed_source_is_refused_when_it_has_to_be_drawn(tmp_path):
    """The asymmetry the skip introduces, stated rather than discovered.

    `CGBitmapContextCreate` returns NULL for an indexed colour space under
    every alpha setting, so a resample that draws refuses one. A resample
    that does not draw has no context to build and writes the file -- which
    the test above measures as byte-identical to `sips`. Both halves are
    pinned so that neither reads as an accident.
    """
    source = pixels.write_indexed_png(tmp_path / "idx.png", 400, 300)
    with pytest.raises(imaging.ImagingError) as caught:
        imaging.resize_and_encode(source, 200, 150, "png", None,
                                  tmp_path / "o.png", resize=True)
    assert "indexed" in str(caught.value)


# ---------------------------------------------------------------------------
# The pinned divergence, and the staging file.
# ---------------------------------------------------------------------------


def test_a_sixteen_bit_source_keeps_its_depth(tmp_path, png16_fixture):
    """Intended, and the same divergence §4 pins for crop.

    `sips --resampleHeightWidth` drops a 16-bit source to 8; CoreGraphics
    keeps it. No corpus file is 16-bit, so this is latent -- which is why it
    is asserted on a fixture rather than left to the corpus gate.
    """
    source = png16_fixture(tmp_path / "deep.png", 400, 300)
    out = tmp_path / "o.png"
    imaging.resize_and_encode(source, 200, 150, "png", None, out, resize=True)
    assert pixels.read_ihdr(out)[2] == 16

    reference = tmp_path / "sips.png"
    _sips_resize(source, 200, 150, reference)
    assert pixels.read_ihdr(reference)[2] == 8, (
        "sips kept 16 bits, so the pinned exception no longer describes it")


def test_the_staging_file_does_not_survive_the_call(tmp_path, photo_fixture):
    """It lands in a directory the executor later scans for outputs."""
    source = photo_fixture(tmp_path / "s.png", 400, 300)
    out = tmp_path / "out" / "o.heic"
    out.parent.mkdir()
    imaging.resize_and_encode(source, 200, 150, "heic", 80, out, resize=True)
    assert [p.name for p in out.parent.iterdir()] == ["o.heic"]


def test_the_staging_name_is_one_the_executor_already_sweeps(tmp_path,
                                                             photo_fixture,
                                                             monkeypatch):
    """The exit no `finally` covers: a kill between resample and encode.

    `execute.sweep_partials` globs `*{PARTIAL_SUFFIX}` over the processing
    tree at the start of every run, and the staged resample lands inside that
    tree. Ending its name with the same suffix is what makes an interrupted
    run's leftovers get tidied instead of standing in the output directory as
    a PNG the size of the frame. The two modules cannot share the constant --
    `execute` imports `imaging` -- so this test is the coupling.
    """
    seen = []
    real = _cg.resize_to_file
    monkeypatch.setattr(_cg, "resize_to_file",
                        lambda source, w, h, out: seen.append(Path(out))
                        or real(source, w, h, out))
    source = photo_fixture(tmp_path / "s.png", 400, 300)
    imaging.resize_and_encode(source, 200, 150, "png", None, tmp_path / "o.png",
                              resize=True)
    assert seen, "nothing was staged"
    assert seen[0].name.endswith(execute.PARTIAL_SUFFIX), seen[0].name
    assert seen[0].name.startswith("o.png"), (
        f"{seen[0].name} does not append to its destination's name, so it "
        f"could collide with another plan's output")


def test_the_staging_file_does_not_survive_a_failing_encode(tmp_path,
                                                            photo_fixture):
    """The finally, not the happy path. `sips` exits 13 on an unknown format,
    and the intermediate must not be left behind for the executor to find."""
    source = photo_fixture(tmp_path / "s.png", 400, 300)
    out = tmp_path / "out" / "o.nope"
    out.parent.mkdir()
    with pytest.raises(imaging.ImagingError):
        imaging.resize_and_encode(source, 200, 150, "nosuchformat", None, out,
                                  resize=True)
    assert list(out.parent.iterdir()) == []


# ---------------------------------------------------------------------------
# Tier 2: the 27-image corpus sample.
# ---------------------------------------------------------------------------


@pytest.mark.corpus
def test_resize_matches_sips_over_the_sample(corpus_sample, tmp_path):
    """Both shapes over real images: the identity resample and a reduction.

    The identity half is the one that changed. The reduction is anisotropic
    on purpose -- w//3 by h//4 -- because production really does ask for
    those: 955 of the 2734 corpus resamples have an x scale that differs from
    their y scale, out of `geometry`'s ceiling division.

    Unlike the crop gate this needs no decode control. Fact 8 is a REGION
    decode effect and a resample reads whole frames, so a difference here is
    the resample's own.

    Each room is removed as soon as it has been compared. An identity
    resample of the largest sample image is a 9072x12096 PNG, and four of
    those per file -- two shapes, two implementations -- would leave several
    gigabytes standing in the temporary directory until the run ended.
    """
    failures = []
    for source in sorted(Path(corpus_sample).iterdir()):
        measured = imaging.probe(source)
        if measured is None:
            continue
        width, height, _ = measured
        shapes = [("identity", width, height),
                  ("reduced", max(width // 3, 1), max(height // 4, 1))]
        for label, out_width, out_height in shapes:
            room = tmp_path / f"{label}-{source.stem}"
            result = compare(_reference(out_width, out_height),
                             _replacement(out_width, out_height), source, room)
            shutil.rmtree(room, ignore_errors=True)
            if result is not None:
                failures.append(f"{source.name} [{label} "
                                f"{out_width}x{out_height}]: {result}")
    assert not failures, "\n".join(failures)


@pytest.mark.corpus
def test_the_corpus_comparison_is_not_vacuous(corpus_sample, tmp_path):
    """Guards the gate above over the real files, where the outputs are
    hundreds of times larger than a fixture's and a harness that gave up
    quietly would look exactly like agreement."""
    source = next(path for path in sorted(Path(corpus_sample).iterdir())
                  if imaging.probe(path) is not None)
    width, height, _ = imaging.probe(source)
    result = compare(_reference(width // 3, height // 4),
                     _replacement(width // 3, max(height // 4 - 1, 1)),
                     source, tmp_path)
    assert result is not None, (
        f"{source.name}: the harness read two different resamples as equal")
