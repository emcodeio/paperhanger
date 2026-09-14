"""The normalize gate: CoreGraphics against `sips --matchTo sRGB`.

This function runs for EVERY source on the upscale path, not only for the
formats upscayl-bin cannot read. `upscayl-bin` emits PNG with no ICC chunk,
so without the conversion the encode step tags sRGB over numbers that were
never in sRGB. The corpus census behind that claim, `sips -g profile` over
all 895 entries of ~/Pictures/wallpaper: 714 sRGB IEC61966-2.1, 96 `c2`, 23
GIMP built-in sRGB, 21 untagged, 19 Adobe RGB (1998), 9 Generic Gray Gamma
2.2, 3 sRGB IEC61966-2-1 black scaled, 3 ProPhoto RGB, 2 Generic RGB, 2
sRGB, 1 Calibrated RGB Colorspace, 1 iMac, and one entry that is not an
image. Every one of them is a JPEG or a PNG, so a condition on the FORMAT
would fire for none of them.

THE DRAW IS THE CONVERSION, AND IT IS UNCONDITIONAL. Task 4's `_resample`
skips its draw when the source already has the requested dimensions, and
that skip is correct there because `bitmap_context` builds the destination
from the SOURCE's own colour space -- the draw converts nothing, so
skipping deletes no work. Here the destination is a colour space of our
choosing, so the draw IS the work. Normalization never resizes, which means
a dimensions-equal skip would fire on EVERY source and silently stop
converting. Measured, a 600x400 noise PNG at three wide-gamut profiles,
what such a skip costs against `sips`:

  Adobe RGB (1998)   661,630 of 720,000 samples differ, largest 144
  ROMM RGB           713,202 of 720,000 samples differ, largest 167
  Display P3         675,884 of 720,000 samples differ, largest 116

and, structurally, a greyscale source stays colour type 0 where `sips`
writes type 2, an indexed source stays type 3, and an alpha-bearing source
keeps a premultiplied round trip it should not have. Those structural rows
are UNTAGGED fixtures and they catch the skip anyway, because what they
have to convert is the colour model rather than the colour space.

So a skip is caught by a tagged source OR by a structural difference, and
exactly three fixture shapes catch it neither way: a plain untagged 8-bit
RGB image, the same image interlaced, and -- for a different reason -- a
CMYK JPEG, where the PNG encoder converts to sRGB by itself. The skip is
byte-identical to `sips` on those three. No assertion below that a skip
must fail rests on one of them, and
`test_an_untagged_source_cannot_catch_a_skip` states the blindness outright
rather than leaving it to be rediscovered.

The same two routes show up in the corpus sample, where 13 of the 27 images
catch the skip: `moss_with_pine_needles_5324.jpg` (ProPhoto RGB) by colour,
at 71,296,904 of 72,000,000 samples and a largest difference of 132, and
`katana_with_tag_2369.jpg` structurally, colour type 0 against `sips`'
type 2. The other 14 are sRGB or untagged photographs, which is the
population fact behind the fixture choice rather than a fixture artefact.

A SECOND SKIP IS REFUSED FOR THE SAME REASON and is more tempting, because
it would fire on 714 of the 895 corpus entries: "the source is already
sRGB, so pass it through". The draw does four things at once -- colour
space, colour MODEL, bit depth and alpha -- so a skip on the space alone
would stop converting greyscale to RGB, 16-bit to 8, and indexed to
truecolour. The three sRGB spellings in the census are not one colour space
object either.

WHICH BAR. `tests/differential.compare`: decoded pixels, plus the colour
type, channel count and bit depth that give those pixels their meaning.

WHERE THIS DIVERGES FROM `sips`, measured rather than assumed:

  * A 16-BIT SOURCE COMES BACK 8-BIT, which is what `sips --matchTo` does
    too -- so this is agreement, and it is the OPPOSITE of the choice
    `crop` and `resize_and_encode` make. It is not an inconsistency: the
    only reader of this file is upscayl-bin, which emits 8-bit PNG, so a
    16-bit intermediate would be discarded one step later at best and
    refused at worst. No corpus file is 16-bit (measured: all 894 images
    are 8 bits per component).
  * THE TWO ITU VIDEO PROFILES DISAGREE, and there we are right and `sips`
    is wrong. `ITU-2020.icc` and `ITU-709.icc` carry a parametric type-3
    rTRC -- g=2.22223, a=0.90967, b=0.09033, c=0.22223, d=0.08099, which is
    the BT.709 OETF. CoreGraphics follows it exactly and `sips` applies a
    pure gamma 2.4 instead. ImageMagick's LittleCMS, a third
    implementation, agrees with CoreGraphics to the byte on the neutral
    axis. `test_the_itu_profiles_follow_the_curve_in_the_profile` computes
    the expectation from the profile's own numbers rather than trusting
    any of the three. Zero corpus files carry either profile.

WHAT THE DRAW NO LONGER SETS. `_resample` pins
CGContextSetInterpolationQuality by asserting the CALL, because no output
can tell High from Default. There is no such call here and none is wanted:
the draw is 1:1 by construction, so nothing is interpolated. Measured on a
600x400 noise PNG, the four quality settings Default, None, Low and High
each produce the same 721,292 bytes.
"""

import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from paperhanger import _cg, imaging
from tests import pixels
from tests.differential import PNG_SIGNATURE, compare

# The profile `--matchTo` was pointed at, which is the line `imaging` shipped
# until Task 6. It lives here now because nothing in `paperhanger/` converts
# through a file any more.
SRGB_PROFILE = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"
PROFILE_DIR = Path("/System/Library/ColorSync/Profiles")

# None is the untagged case, which is not a formality: an untagged PNG is
# what `pixels.write_png` produces and what 21 corpus files are, and `sips`
# writes it out TAGGED sRGB rather than leaving it unmarked.
#
# "ProPhoto.icc" is what the plan named and no such file is installed; the
# ICC name for ProPhoto RGB is ROMM RGB, and `ROMM RGB.icc` is what the
# corpus's three ProPhoto files decode against. Naming the file that is not
# there would have skipped this row silently.
PROFILES = [None, "AdobeRGB1998.icc", "ROMM RGB.icc", "Display P3.icc",
            "Generic RGB Profile.icc"]

# The rows a skip would change. Untagged is deliberately not here.
WIDE = ["AdobeRGB1998.icc", "ROMM RGB.icc", "Display P3.icc"]


def _sips_normalize(source, out_path):
    """The reference: the invocation this project shipped until Task 6.

    `-s format png` ahead of the paths, which is imaging fact 7: placed
    after them it is silently dropped and the output keeps the source's
    format whatever the name says.
    """
    subprocess.run([imaging.SIPS, "--matchTo", SRGB_PROFILE, "-s", "format",
                    "png", str(source), "--out", str(out_path)],
                   check=True, capture_output=True)


def _passthrough(source, out_path):
    """What a dimensions-equal skip would do: decode, and write it back.

    Not a strawman. It is `_resample`'s identity branch applied here, and
    the reason this file exists is that it produces a plausible PNG at the
    right dimensions for every source in the corpus.
    """
    with _cg.Scope() as scope:
        image = _cg.load(scope, source)
        _cg.write_png(scope, image, out_path)


def _profile_name(path) -> str:
    out = subprocess.run(["/usr/bin/sips", "-g", "profile", str(path)],
                         capture_output=True, text=True)
    for line in out.stdout.splitlines():
        if "profile:" in line:
            return line.split("profile:", 1)[1].strip()
    return ""


def _tagged(base, profile, out_path):
    """`base` tagged with a named ColorSync profile, or a skip.

    The same `sips --matchTo` `tests/conftest.profiled_fixture` uses, and
    for the same reason: tagging a file with a real ICC profile needs a real
    colour-management implementation.
    """
    icc = PROFILE_DIR / profile
    if not icc.is_file():
        pytest.skip(f"colour profile not installed: {icc}")
    subprocess.run(["/usr/bin/sips", "--matchTo", str(icc), "-s", "format",
                    "png", str(base), "--out", str(out_path)],
                   check=True, capture_output=True)
    assert _profile_name(out_path), f"{out_path} came back untagged"
    return out_path


def _count_draws(monkeypatch):
    """Every CGContextDrawImage, as the rect it was given."""
    calls = []
    real = _cg._CG_LIB.CGContextDrawImage
    monkeypatch.setattr(_cg._CG_LIB, "CGContextDrawImage",
                        lambda ctx, rect, image: calls.append(rect)
                        or real(ctx, rect, image))
    return calls


# ---------------------------------------------------------------------------
# The differential, profile by profile.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_normalize_matches_the_matchto_reference(tmp_path, profiled_fixture,
                                                 profile):
    source = profiled_fixture(tmp_path / "s.png", 600, 400, profile)
    assert compare(_sips_normalize, imaging.normalize_to_srgb_png,
                   source, tmp_path) is None


@pytest.mark.parametrize("profile", PROFILES)
def test_the_output_is_tagged_srgb(tmp_path, profiled_fixture, profile):
    """The whole point: upscayl-bin strips the ICC chunk, so the numbers
    have to be sRGB before it sees them -- and the untagged row matters as
    much as the tagged ones, because an untagged file is not an sRGB file
    until something says so."""
    source = profiled_fixture(tmp_path / "s.png", 600, 400, profile)
    out = tmp_path / "n.png"
    imaging.normalize_to_srgb_png(source, out)
    assert "sRGB" in _profile_name(out), _profile_name(out)


@pytest.mark.parametrize("profile", WIDE)
def test_the_numbers_actually_move(tmp_path, profiled_fixture, profile):
    """The premise every test above rests on. A conversion that did nothing
    would satisfy both of them for an sRGB source and for an untagged one,
    so at least one row has to show the pixels changing."""
    source = profiled_fixture(tmp_path / "s.png", 600, 400, profile)
    out = tmp_path / "n.png"
    imaging.normalize_to_srgb_png(source, out)
    _w, _h, before = pixels.read_png_rgb(source)
    _w, _h, after = pixels.read_png_rgb(out)
    assert before != after, (
        f"{profile} in and sRGB out, and not one pixel changed")


# ---------------------------------------------------------------------------
# The skip that must not be introduced here.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile", WIDE)
def test_a_source_already_at_its_own_dimensions_is_still_converted(
        tmp_path, profiled_fixture, monkeypatch, profile):
    """Normalization never resizes, so EVERY call is the equal-dimensions
    case -- the one `_resample` skips.

    Two halves, because either alone can be satisfied by the wrong thing.
    The draw is watched where it happens, since a 1:1 draw and a skip
    produce the same file for an sRGB source; and the output is compared
    against `sips`, since watching a call says nothing about what it did.
    """
    source = profiled_fixture(tmp_path / "s.png", 600, 400, profile)
    calls = _count_draws(monkeypatch)
    out = tmp_path / "n.png"
    imaging.normalize_to_srgb_png(source, out)

    assert imaging.probe(source)[:2] == imaging.probe(out)[:2] == (600, 400), (
        "the premise of this test is that the dimensions match on both sides")
    assert len(calls) == 1, (
        "the source was already 600x400 and the draw was skipped; the draw "
        "IS the conversion here, so there is nothing left doing it")
    assert compare(_sips_normalize, imaging.normalize_to_srgb_png, source,
                   tmp_path) is None


@pytest.mark.parametrize("profile", WIDE)
def test_the_harness_would_catch_a_skip(tmp_path, profiled_fixture, profile):
    """Guards every test above. A skip produces a plausible PNG at the right
    dimensions with the right colour type, so the gate has to be able to
    tell one from a conversion -- measured at 661,630 to 713,202 of 720,000
    samples differing, largest difference 116 to 167, over these three
    profiles."""
    source = profiled_fixture(tmp_path / "s.png", 600, 400, profile)
    result = compare(_sips_normalize, _passthrough, source, tmp_path)
    assert result is not None, (
        f"the harness read a {profile} source written back unconverted as "
        f"equal to the sRGB conversion of it")


def test_an_untagged_source_cannot_catch_a_skip(tmp_path, png_fixture):
    """Why every test above is parametrised over PROFILES and not over
    shapes. An untagged source is already in the space the destination
    would be, so the skip and the conversion agree -- and the corpus is 21
    untagged files, 714 tagged sRGB and 96 `c2`, which is to say that most
    of it cannot see this defect either. This is the negative result that
    decides the fixtures, recorded so it is not rediscovered."""
    source = png_fixture(tmp_path / "s.png", 600, 400)
    assert compare(_sips_normalize, _passthrough, source, tmp_path) is None


# ---------------------------------------------------------------------------
# What the destination bitmap has to be, given that the space is ours.
# ---------------------------------------------------------------------------


ALPHA_SHAPES = [
    ("rgba", pixels.write_rgba_png),
    ("grey+alpha", pixels.write_grey_alpha_png),
    ("colour-key", pixels.write_colour_key_png),
]


@pytest.mark.parametrize("name,writer", ALPHA_SHAPES)
def test_the_alpha_channel_survives(tmp_path, name, writer):
    """`sips --matchTo` PRESERVES alpha -- measured on the corpus's
    `brush_circle_8107.png`, colour type 6 in and colour type 6 out -- and
    twelve corpus PNGs carry one. All twelve plan as band 3 or band 4, so
    all twelve reach this function and none of them reaches the resampler
    from its original.

    A destination built with `kCGImageAlphaNoneSkipLast`, which is what the
    plan wrote, composites them onto the context's black ground and writes
    back opaque colour type 2. The colour-key row is the one that shows
    what that costs: a `tRNS` chunk names a COLOUR transparent, ImageIO
    expands it to a channel, and the composite turns (255, 0, 255) into
    (0, 0, 0).
    """
    source = writer(tmp_path / f"{name}.png", 400, 300)
    assert compare(_sips_normalize, imaging.normalize_to_srgb_png,
                   source, tmp_path) is None
    out = tmp_path / "n.png"
    imaging.normalize_to_srgb_png(source, out)
    assert pixels.read_ihdr(out)[3] == 6, (
        f"a {name} source came back at colour type "
        f"{pixels.read_ihdr(out)[3]}; the alpha channel is gone and the "
        f"upscaler would be handed pixels composited onto black")


def test_a_greyscale_source_comes_back_rgb(tmp_path, gradient_fixture):
    """The destination is sRGB, which is an RGB space, so a monochrome
    source is converted rather than carried. `sips --matchTo` does the same
    -- 32 bpp RGB out of a Gray source -- and ten corpus photographs are
    monochrome, one of them in the coverage sample."""
    source = gradient_fixture(tmp_path / "g.png", 400, 300)
    assert pixels.read_ihdr(source)[3] == 0
    assert compare(_sips_normalize, imaging.normalize_to_srgb_png,
                   source, tmp_path) is None
    out = tmp_path / "n.png"
    imaging.normalize_to_srgb_png(source, out)
    assert pixels.read_ihdr(out)[3] == 2


def test_a_flat_greyscale_source_is_not_black(tmp_path, grayscale_fixture):
    """The failure that motivated `bitmap_format`, from the other side. An
    opaque grey source drawn into a MONOCHROME context with NoneSkipLast
    comes back entirely black; an sRGB context cannot reach that pairing,
    and this says so in pixels rather than by construction."""
    source = grayscale_fixture(tmp_path / "flat.png", 200, 150, value=128)
    out = tmp_path / "n.png"
    imaging.normalize_to_srgb_png(source, out)
    _w, _h, rows = pixels.read_png_rgb(out)
    assert set(rows[0]) != {(0, 0, 0)}, "the frame came back black"
    assert compare(_sips_normalize, imaging.normalize_to_srgb_png,
                   source, tmp_path) is None


def test_a_16_bit_source_comes_back_8_bit(tmp_path, png16_fixture):
    """The one place this pipeline narrows depth, deliberately.

    `crop` and `resize_and_encode` keep 16 bits where `sips` drops them;
    this function drops them, because `sips --matchTo` does and because the
    only reader of its output is upscayl-bin, which emits 8-bit PNG. A
    16-bit intermediate would be thrown away one step later at best. No
    corpus file is 16-bit.
    """
    source = png16_fixture(tmp_path / "s.png", 400, 300)
    assert pixels.read_ihdr(source)[2] == 16
    assert compare(_sips_normalize, imaging.normalize_to_srgb_png,
                   source, tmp_path) is None
    out = tmp_path / "n.png"
    imaging.normalize_to_srgb_png(source, out)
    assert pixels.read_ihdr(out)[2] == 8


def test_the_models_the_resampler_refuses_normalize_anyway(tmp_path,
                                                           cmyk_fixture):
    """The destination space is OURS, so the source's colour model is not a
    constraint here the way it is in `bitmap_context`.

    `bitmap_format` refuses indexed, CMYK, Lab and anything above 16 bits
    per component, because those cannot be a destination. They can all be a
    SOURCE of a draw into sRGB, and `sips --matchTo` handled them, so
    refusing them here would be a regression the corpus cannot see and a
    print-workflow CMYK JPEG can.
    """
    cmyk = cmyk_fixture(tmp_path / "c.jpg", 400, 300)
    assert compare(_sips_normalize, imaging.normalize_to_srgb_png,
                   cmyk, tmp_path) is None

    indexed = pixels.write_indexed_png(tmp_path / "i.png", 400, 300)
    assert compare(_sips_normalize, imaging.normalize_to_srgb_png,
                   indexed, tmp_path) is None

    # And the contrast is real: the same two files still fail a resample.
    with pytest.raises(imaging.ImagingError):
        imaging.resize_and_encode(cmyk, 200, 150, "png", None,
                                  tmp_path / "x.png", resize=True)


# ---------------------------------------------------------------------------
# Every container, out as PNG.
# ---------------------------------------------------------------------------


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
    assert compare(_sips_normalize, imaging.normalize_to_srgb_png,
                   holder, tmp_path) is None


def test_a_webp_named_jpg_comes_back_png(tmp_path, webp_fixture):
    """`snowy_forest_landscape_9522.jpg` really is a WebP, so the name on
    the file settles nothing. The conversion goes by the bytes."""
    source = webp_fixture(tmp_path / "s.jpg", 320, 240)
    out = tmp_path / "n.png"
    imaging.normalize_to_srgb_png(source, out)
    assert out.read_bytes()[:8] == PNG_SIGNATURE
    assert imaging.probe(out)[:2] == (320, 240)


# ---------------------------------------------------------------------------
# The one profile class where `sips` is the one that is wrong.
# ---------------------------------------------------------------------------


def _rtrc(icc: Path):
    """The (g, a, b, c, d) of an ICC profile's parametric type-3 red TRC.

    Parsed out of the file, so the expectation below is computed from what
    the profile SAYS rather than from what any of the three colour engines
    on this machine does with it.
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


@pytest.mark.parametrize("profile", ["ITU-2020.icc", "ITU-709.icc"])
def test_the_itu_profiles_follow_the_curve_in_the_profile(tmp_path, profile):
    """Where this diverges from `sips`, with an instrument that is neither.

    Both profiles carry the BT.709 OETF as a parametric type-3 rTRC.
    CoreGraphics follows it; `sips` applies a pure gamma 2.4 instead. The
    gap is not a constant and is largest in the shadows -- measured at the
    four levels below, both profiles alike, as (device value in the tagged
    file, what this writes, what `sips` writes):

      28 -> 44 against 15      74 -> 89 against 64
      135 -> 146 against 128   203 -> 208 against 200

    which is 29, 25, 18 and 8 levels out of 255. Over a 200x150 noise image
    the mean absolute difference between the two outputs is 20.83 for
    ITU-2020 and 16.73 for ITU-709. ImageMagick's LittleCMS agrees with
    CoreGraphics to the byte, and the arithmetic below agrees with both, so
    `sips` is the odd one out and byte-identity with it is not something to
    reach for here.

    Read the right-hand column of those four rows against the levels the
    loop asks for -- 16, 64, 128, 200 -- and `sips` is handing back the
    numbers the file was built from. Its two legs are mutual inverses, which
    is exactly why a round trip cannot arbitrate this and the profile's own
    parameters have to.

    No corpus file carries either profile: this is latent, and it is pinned
    so that a future attempt to close the ITU gap against `sips` has to
    argue with the profile rather than with a preference.

    Neutral values only, where the primaries cannot contribute: both
    profiles and sRGB are D65, so an equal-channel input maps to an
    equal-channel output whatever the matrix says.
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
        # for: the tagging ran through `sips` too.
        _w, _h, rows = pixels.read_png_rgb(tagged)
        device = rows[0][0][0] / 255.0
        expected = round(_to_srgb(_to_linear(device, curve)) * 255)

        ours = tmp_path / f"cg{level}.png"
        imaging.normalize_to_srgb_png(tagged, ours)
        _w, _h, got = pixels.read_png_rgb(ours)
        got = got[0][0][0]

        theirs = tmp_path / f"sips{level}.png"
        _sips_normalize(tagged, theirs)
        _w, _h, sips_rows = pixels.read_png_rgb(theirs)
        sips_value = sips_rows[0][0][0]

        assert abs(got - expected) <= 2, (
            f"{profile} at device {rows[0][0][0]}: the profile's own curve "
            f"gives {expected} and this produced {got}")
        assert abs(sips_value - expected) > 2, (
            f"{profile} at device {rows[0][0][0]}: `sips` produced "
            f"{sips_value} against the profile's {expected}, and this test "
            f"is only worth running while the two disagree")


# ---------------------------------------------------------------------------
# Failures. `_run(produces=...)` used to do all of this and no longer runs.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["missing", "directory", "not an image"])
def test_an_unreadable_source_raises_and_writes_nothing(tmp_path, kind):
    """It used to be `sips` exiting 0 with `not a valid file` on stderr, or
    exiting 13; it is now an ImagingError out of ImageIO naming the file.
    Same exception type, which is what `execute` catches per photo."""
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


def test_a_stale_destination_cannot_stand_in_for_output(tmp_path):
    """An earlier run's file must not survive a run that wrote nothing.

    This is what `_run(produces=...)` did for this function until Task 6 --
    it unlinked the destination before starting, so that a write which
    never happened could not be answered by a file that was already there.
    No subprocess runs now, so `normalize_to_srgb_png` clears it itself,
    exactly as `resize_and_encode` does.
    """
    out = tmp_path / "n.png"
    pixels.write_png(out, 64, 64)
    with pytest.raises(imaging.ImagingError):
        imaging.normalize_to_srgb_png(tmp_path / "nope.png", out)
    assert not out.exists(), (
        "an earlier run's output is still standing at the destination, "
        "where anything checking for a file would read it as this run's")


def test_an_unremovable_stale_output_still_raises_imaging_error(tmp_path):
    """The clearing unlink can itself fail, and a bare PermissionError is
    not this layer's exception. `execute` catches ImagingError per photo and
    carries on; anything else ends the run. The same shape Task 1 fixed in
    `write_png` and Task 5 in `resize_and_encode`."""
    room = tmp_path / "ro"
    room.mkdir()
    out = room / "n.png"
    pixels.write_png(out, 8, 8)
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


# ---------------------------------------------------------------------------
# Tier 2.
# ---------------------------------------------------------------------------


@pytest.mark.corpus
def test_normalize_matches_sips_over_the_sample(corpus_sample, tmp_path):
    """Real photographs, which is where the profiles are real too: the
    27-image sample carries one Adobe RGB, one ProPhoto RGB, one Generic
    RGB, two `c2`, one Generic Gray Gamma 2.2, one untagged RGBA PNG, a
    WebP named .jpg, a GIF and a HEIC.

    Each room goes as soon as it has been compared: normalization writes a
    full-size PNG per side, and the largest sample image is 9072x12096.
    """
    failures = []
    for source in sorted(Path(corpus_sample).iterdir()):
        if imaging.probe(source) is None:
            continue
        room = tmp_path / source.stem
        result = compare(_sips_normalize, imaging.normalize_to_srgb_png,
                         source, room)
        shutil.rmtree(room, ignore_errors=True)
        if result is not None:
            failures.append(result)
    assert not failures, "\n".join(failures)


@pytest.mark.corpus
def test_the_corpus_comparison_is_not_vacuous(corpus_sample, tmp_path):
    """Guards the gate above over the real files. The wide-gamut member is
    named rather than searched for: it is the one file in the sample whose
    conversion a skip would change, and a loop that found nothing to test
    would pass."""
    source = (Path(corpus_sample)
              / "red_tulips_with_mountain_background_4338.jpg")
    if not source.exists():
        pytest.skip(f"{source.name} is not in the sample")
    assert "Adobe RGB" in _profile_name(source), _profile_name(source)
    result = compare(_sips_normalize, _passthrough, source, tmp_path)
    assert result is not None, (
        f"{source.name}: the harness read an Adobe RGB file written back "
        f"unconverted as equal to the sRGB conversion of it")
