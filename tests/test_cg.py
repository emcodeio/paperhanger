"""The binding layer: what it loads, what it refuses, and what it releases.

Two of the properties here are invisible to every other kind of assertion,
and both of them are why this file exists rather than leaving `_cg` to be
tested through the operations built on it:

  * A MISSING RELEASE changes no output. Dimensions are right, pixels are
    right, files appear. A leaked 96 Mpx CGImage is about 380 MB and a real
    run processes 894 photographs, so the failure it produces is a run that
    dies partway through for reasons that look like the machine's.
  * A WRONG DESTINATION BITMAP is silent in a different way: a greyscale
    source drawn through a context built the obvious way comes back entirely
    black, at exit 0, at exactly the right dimensions.

Neither is caught by asking whether the thing worked. The first is measured
in resident memory and the second in pixel values, and each has a test below
that was confirmed to go red before it was allowed to go green.
"""

import ast
import os
import resource
import shutil
import subprocess
from pathlib import Path

import pytest

from paperhanger import _cg, imaging
from paperhanger.imaging import ImagingError
from tests import pixels

PACKAGE = Path(_cg.__file__).parent


# --------------------------------------------------------------------------
# Loading, and refusing.
# --------------------------------------------------------------------------


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


def test_load_raises_for_a_file_that_is_not_there(tmp_path):
    """The missing-file case, which reaches a different call than a bad one."""
    with _cg.Scope() as scope:
        with pytest.raises(ImagingError) as exc:
            _cg.load(scope, tmp_path / "absent.png")
    assert "absent.png" in str(exc.value)


def test_load_reads_the_bytes_not_the_extension(tmp_path, webp_fixture):
    """A WebP named .jpg, which the corpus really contains.

    `snowy_forest_landscape_9522.jpg` is a WebP. Anything that decided format
    from the name would be wrong about it, so the binding layer has to be
    right about it before `probe` can be.
    """
    src = webp_fixture(tmp_path / "lying.jpg", 320, 200)
    header = src.read_bytes()[:12]
    assert header[:4] == b"RIFF" and header[8:12] == b"WEBP", (
        f"the fixture did not write a WebP, it wrote {header!r} -- and a "
        f"fixture that quietly produced a JPEG would make this test pass "
        f"while proving nothing"
    )
    assert src.suffix == ".jpg", "and the name still lies, which is the point"
    with _cg.Scope() as scope:
        assert _cg.dimensions(_cg.load(scope, src)) == (320, 200)


def test_load_keeps_a_tagged_profile_in_an_rgb_space(tmp_path, profiled_fixture):
    """A wide-gamut source is still RGB, so it still gets an RGB bitmap.

    The fixture's own claim is asserted first. `profiled_fixture` shells
    out to `sips --matchTo`, and if that ever stopped tagging the file the
    test would go on passing on an untagged PNG while appearing to cover
    the wide-gamut case -- the corpus holds 19 Adobe RGB and 3 ProPhoto
    sources, so the case is real.
    """
    src = profiled_fixture(tmp_path / "adobe.png", 120, 80,
                           "AdobeRGB1998.icc")
    untagged = profiled_fixture(tmp_path / "plain.png", 120, 80, None)
    assert b"iCCP" in src.read_bytes(), "the fixture tagged nothing"
    assert b"iCCP" not in untagged.read_bytes(), "and the base is untagged"

    with _cg.Scope() as scope:
        image = _cg.load(scope, src)
        assert _cg.colour_model(image, src) == "RGB"
        assert _cg.dimensions(image) == (120, 80)
        _space, bits, info = _cg.bitmap_format(image, src)
    assert (bits, info) == (8, _cg.kCGImageAlphaNoneSkipLast)


# --------------------------------------------------------------------------
# _checked: the single chokepoint.
# --------------------------------------------------------------------------


def test_checked_raises_on_null():
    """_checked is the single chokepoint that converts NULL into ImagingError."""
    with pytest.raises(ImagingError) as exc:
        _cg._checked(None, "create the thing", "/some/path.png")
    assert "create the thing" in str(exc.value)
    assert "/some/path.png" in str(exc.value)


def test_checked_passes_a_live_pointer_through():
    assert _cg._checked(12345, "anything", "/p") == 12345


def test_checked_treats_a_zero_pointer_as_null():
    """ctypes hands back 0 rather than None for a NULL c_void_p restype."""
    with pytest.raises(ImagingError):
        _cg._checked(0, "create the thing", "/p")


# --------------------------------------------------------------------------
# Scope: releasing, on both paths.
# --------------------------------------------------------------------------


class _Boom(Exception):
    """A sentinel no production code raises.

    The plan's version of the test below raised and caught `RuntimeError`.
    `ImagingError` SUBCLASSES RuntimeError, so that version stayed green
    with `load` completely broken: the `pytest.raises` was satisfied by the
    failure of the call the test meant to succeed, and `released` was then
    non-empty from the CFString and CFURL alone.
    """


def test_scope_releases_on_exception(tmp_path, png_fixture):
    """A raise inside the scope must not leak the handles taken before it."""
    src = png_fixture(tmp_path / "a.png", 64, 64)
    released = []
    scope = _cg.Scope(release=released.append)
    with pytest.raises(_Boom):
        with scope:
            _cg.load(scope, src)
            raise _Boom("boom")
    assert len(released) == 4, (
        "the scope released {} handles when the body raised, not the four "
        "a load takes".format(len(released)))


def test_scope_releases_every_handle_a_load_takes(tmp_path, png_fixture):
    """Four of them, and the two easy to forget are not the image.

    `load` owns a CFString for the path, a CFURL built from it, the image
    source, and the image. Counting them is what stops a later edit from
    dropping one of the two CoreFoundation objects, which are the handles
    nothing downstream ever mentions again.
    """
    src = png_fixture(tmp_path / "a.png", 64, 64)
    released = []
    with _cg.Scope(release=released.append) as scope:
        _cg.load(scope, src)
    assert len(released) == 4, (
        f"CFString, CFURL, image source, image -- saw {len(released)}")
    assert len(set(released)) == 4, "and four distinct pointers"


def test_scope_releases_in_reverse_order():
    """Last acquired, first released: a handle may hold an earlier one."""
    released = []
    with _cg.Scope(release=released.append) as scope:
        scope.own(1)
        scope.own(2)
        scope.own(3)
    assert released == [3, 2, 1]


def test_scope_forgets_what_it_released():
    """Or a re-entered scope releases the same pointer twice, which crashes."""
    released = []
    scope = _cg.Scope(release=released.append)
    with scope:
        scope.own(7)
    with scope:
        scope.own(8)
    assert released == [7, 8]


# --------------------------------------------------------------------------
# The leak test.
# --------------------------------------------------------------------------


def _draw(scope, image, source, width, height):
    """Resample `image` into a context of its own colour model, and read back.

    Three owned handles in one call -- the context, the image it produces,
    and the image and CF objects `load` already took -- which is what makes
    it the right loop body for a leak test.
    """
    ctx = _cg.bitmap_context(scope, image, width, height, source)
    _cg._CG_LIB.CGContextSetInterpolationQuality(ctx, _cg.kCGInterpolationHigh)
    _cg._CG_LIB.CGContextDrawImage(
        ctx,
        _cg.CGRect(_cg.CGPoint(0.0, 0.0), _cg.CGSize(float(width), float(height))),
        image)
    return scope.own(_cg._checked(_cg._CG_LIB.CGBitmapContextCreateImage(ctx),
                                  "read back the bitmap", source),
                     kind="image")


def _rss_mb():
    """CURRENT resident size in MB, which is not what ru_maxrss reports.

    `resource.getrusage(...).ru_maxrss` is a high-water mark that never
    falls, so growth measured from it reads as zero once anything earlier in
    the same process has peaked higher -- another test, a corpus fixture, a
    previous leak check. Measured: the leaking loop below grows the peak by
    2186 MB in a fresh process and by 1196 MB when it runs second, and the
    second figure is the instrument reporting the earlier test rather than
    this one. Current RSS has no such memory.

    `ps` rather than a library call because reading current RSS on macOS
    otherwise means `task_info`, and ctypes lives in exactly one file.
    """
    out = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(os.getpid())],
                         capture_output=True, text=True, check=True).stdout
    return int(out.strip()) / 1024


def test_repeated_loads_do_not_leak(tmp_path, photo_fixture):
    """300 acquire/release cycles must not grow resident memory.

    This is the test that catches a missing CFRelease, which changes no
    output and so is invisible to every other assertion in the suite. A
    leaked 96 Mpx CGImage is ~380 MB and a real run does 894 photos.

    THE LOOP HAS TO DRAW. The version of this test that only loads and reads
    dimensions passes against an implementation that releases no image at
    all: ImageIO decodes lazily, so an undecoded CGImage handle costs almost
    nothing and 300 leaked ones came to 1.2 MB -- under any threshold worth
    setting. Drawing materializes the bitmap, which is what production does
    anyway, and a leaked handle then holds the pixels. Measured over these
    300 iterations at 1200x900, with each release removed in turn:

        every release present            +5 MB
        CGImageRelease removed        +2182 MB
        CGContextRelease removed      +1243 MB
        CFRelease removed             +2173 MB

    The image is noisy for the same reason: a flat PNG's pixels compress to
    nothing, and what is leaked should be a real frame.
    """
    src = photo_fixture(tmp_path / "big.png", 1200, 900)
    before = _rss_mb()
    for _ in range(300):
        with _cg.Scope() as scope:
            image = _cg.load(scope, src)
            _draw(scope, image, src, 1200, 900)
    growth = _rss_mb() - before
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
    assert growth < 50, (
        f"resident memory grew {growth:.0f} MB over 300 load/draw cycles "
        f"(peak now {peak:.0f} MB) -- something is not being released"
    )


# --------------------------------------------------------------------------
# The destination bitmap, which is where the silent wrong answers live.
# --------------------------------------------------------------------------


def test_colour_model_reports_monochrome_for_a_grey_png(tmp_path,
                                                        grayscale_fixture):
    src = grayscale_fixture(tmp_path / "grey.png", 64, 64)
    with _cg.Scope() as scope:
        assert _cg.colour_model(_cg.load(scope, src), src) == "monochrome"


def test_colour_model_reports_rgb_for_a_colour_png(tmp_path, png_fixture):
    src = png_fixture(tmp_path / "rgb.png", 64, 64)
    with _cg.Scope() as scope:
        assert _cg.colour_model(_cg.load(scope, src), src) == "RGB"


def test_a_monochrome_source_gets_an_alpha_none_context(tmp_path,
                                                        grayscale_fixture):
    """kCGImageAlphaNone, and nothing else.

    NoneSkipLast is ACCEPTED here -- no NULL, no error -- and renders the
    frame black on any scaling draw. PremultipliedLast is accepted too and
    turns the file into gray+alpha. Only AlphaNone is right, and only the
    constant says which one was chosen.
    """
    src = grayscale_fixture(tmp_path / "grey.png", 64, 64)
    with _cg.Scope() as scope:
        _space, bits, info = _cg.bitmap_format(_cg.load(scope, src), src)
    assert info == _cg.kCGImageAlphaNone
    assert bits == 8


def test_an_opaque_rgb_source_gets_skip_last(tmp_path, png_fixture):
    """And NOT AlphaNone, which is a NULL context for an RGB space."""
    src = png_fixture(tmp_path / "rgb.png", 64, 64)
    with _cg.Scope() as scope:
        _space, bits, info = _cg.bitmap_format(_cg.load(scope, src), src)
    assert info == _cg.kCGImageAlphaNoneSkipLast
    assert bits == 8


def test_an_alpha_bearing_source_gets_a_premultiplied_context(tmp_path):
    """Twelve corpus PNGs carry alpha, and all twelve reach a context.

    With SkipLast they would be composited onto the context's black ground
    and written back as opaque colour type 2 -- structurally a different
    file, and one that a comparison of RGB channels would call equal for
    eight of the twelve.
    """
    src = pixels.write_rgba_png(tmp_path / "alpha.png", 64, 64, alpha=128)
    with _cg.Scope() as scope:
        image = _cg.load(scope, src)
        assert _cg.has_alpha(image)
        _space, _bits, info = _cg.bitmap_format(image, src)
    assert info == _cg.kCGImageAlphaPremultipliedLast


def test_a_monochrome_source_with_alpha_keeps_its_channel(tmp_path):
    """The branch the corpus cannot reach, which is why it needs a fixture.

    A grey+alpha source through an AlphaNone context is ACCEPTED and
    composited onto the context's black ground: measured, values 0, 6, 12,
    18 at alpha 128 coming back 0, 3, 6, 9. No corpus file is colour type
    4 and no test could construct one until `write_grey_alpha_png` existed,
    so this silently did the wrong thing with nothing able to say so.
    """
    src = pixels.write_grey_alpha_png(tmp_path / "greyalpha.png", 64, 64,
                                      alpha=128)
    with _cg.Scope() as scope:
        image = _cg.load(scope, src)
        assert _cg.colour_model(image, src) == "monochrome"
        assert _cg.has_alpha(image), "a colour type 4 source carries alpha"
        _space, _bits, info = _cg.bitmap_format(image, src)
    assert info == _cg.kCGImageAlphaPremultipliedLast


def test_a_monochrome_source_with_alpha_is_not_composited_onto_black(
        tmp_path):
    """And the same thing measured in pixels rather than in a constant."""
    src = pixels.write_grey_alpha_png(tmp_path / "greyalpha.png", 60, 80,
                                      top=40, bottom=200, alpha=128)
    out = tmp_path / "half.png"
    with _cg.Scope() as scope:
        image = _cg.load(scope, src)
        _cg.write_png(scope, _draw(scope, image, src, 30, 40), out)

    _width, _height, _depth, colour, _ = pixels.read_ihdr(out)
    assert colour == 4, (
        f"colour type {colour}: the alpha channel was dropped, and the grey "
        f"values will have been composited onto black"
    )


def test_a_source_deeper_than_16_bits_is_refused_by_name(tmp_path):
    """A 32-bit float source accepts NO integer context -- 8, 16 and 32 all
    return NULL -- so the depth is named rather than left as a context that
    could not be made for reasons unstated."""
    if shutil.which("magick") is None:
        pytest.skip("ImageMagick (`magick`) is not installed")
    src = tmp_path / "float32.tiff"
    subprocess.run(
        ["magick", "-size", "40x40", "gradient:red-blue", "-depth", "32",
         "-define", "quantum:format=floating-point", str(src)],
        check=True, capture_output=True)
    with _cg.Scope() as scope:
        image = _cg.load(scope, src)
        with pytest.raises(ImagingError) as exc:
            _cg.bitmap_format(image, src)
    assert "32-bit components" in str(exc.value)
    assert "float32.tiff" in str(exc.value)


def test_an_indexed_source_is_refused_by_name(tmp_path):
    """The model where CGBitmapContextCreate returns NULL under every setting.

    Diagnosed here, by the name of the colour space, rather than left to
    surface as a failed context whose message says only that a context could
    not be made. Both are safe -- `_checked` catches the NULL either way --
    but only one of them tells the reader what to do about it.

    The phrase is matched in full on purpose. `"indexed" in str(exc.value)`
    passed with this rule DELETED, because pytest's tmp_path is named after
    the test and the message quotes the path: the assertion was reading its
    own test name back.
    """
    src = pixels.write_indexed_png(tmp_path / "paletted.png", 64, 64)
    with _cg.Scope() as scope:
        image = _cg.load(scope, src)
        assert _cg.colour_model(image, src) == "indexed"
        with pytest.raises(ImagingError) as exc:
            _cg.bitmap_context(scope, image, 32, 32, src)
    assert "indexed colour space" in str(exc.value)
    assert "paletted.png" in str(exc.value)


def test_a_16_bit_source_keeps_its_depth(tmp_path, png16_fixture):
    """sips drops it to 8; CoreGraphics does not have to.

    The intended divergence the crop gate pins. No corpus file is 16-bit, so
    this is latent rather than live -- it is asserted so that a later edit
    hardcoding 8 says so here instead of in a wallpaper.
    """
    src = png16_fixture(tmp_path / "deep.png", 64, 64)
    with _cg.Scope() as scope:
        _space, bits, info = _cg.bitmap_format(_cg.load(scope, src), src)
    assert bits == 16
    assert info == _cg.kCGImageAlphaNoneSkipLast


def test_bitmap_context_is_not_null_for_any_model_it_accepts(
        tmp_path, png_fixture, grayscale_fixture, png16_fixture):
    """The half of the contract that is about NOT raising.

    A rule that refused everything would pass every test above.
    """
    sources = [png_fixture(tmp_path / "rgb.png", 40, 40),
               grayscale_fixture(tmp_path / "grey.png", 40, 40),
               png16_fixture(tmp_path / "deep.png", 40, 40),
               pixels.write_rgba_png(tmp_path / "alpha.png", 40, 40),
               pixels.write_grey_alpha_png(tmp_path / "greya.png", 40, 40)]
    for src in sources:
        with _cg.Scope() as scope:
            image = _cg.load(scope, src)
            assert _cg.bitmap_context(scope, image, 20, 20, src), src.name


# --------------------------------------------------------------------------
# End to end through the primitives: load, draw, write.
# --------------------------------------------------------------------------


def test_a_greyscale_source_does_not_come_back_black(tmp_path,
                                                     gradient_fixture):
    """The regression this whole derivation exists for.

    Ten of the 894 corpus photographs are greyscale, and every reducing
    render of them builds a context. Measured against the naive pairing on a
    real one: 4,665,600 pixels, all zero, at exit 0. A scaling draw is what
    fires it -- at 1:1 the bad pairing round-trips correctly, which is how it
    survived two earlier rounds of measurement.
    """
    src = gradient_fixture(tmp_path / "gradient.png", 150, 200)
    out = tmp_path / "half.png"
    with _cg.Scope() as scope:
        image = _cg.load(scope, src)
        _cg.write_png(scope, _draw(scope, image, src, 75, 100), out)

    width, height, depth, colour, _ = pixels.read_ihdr(out)
    assert (width, height) == (75, 100)
    assert colour == 0, "a greyscale source stays greyscale"
    assert depth == 8

    _, _, rows = pixels.read_png_grey(out)
    column = [row[0] for row in rows]
    assert set(column) != {0}, "the whole frame came back black"
    assert column[0] < column[50] < column[-1], (
        "and it is the source's gradient, in the source's order"
    )


def test_an_rgb_source_survives_the_same_path(tmp_path, photo_fixture):
    """The 884 files the greyscale rule must not break."""
    src = photo_fixture(tmp_path / "photo.png", 160, 120)
    out = tmp_path / "small.png"
    with _cg.Scope() as scope:
        image = _cg.load(scope, src)
        _cg.write_png(scope, _draw(scope, image, src, 80, 60), out)

    width, height, _depth, colour, _ = pixels.read_ihdr(out)
    assert (width, height) == (80, 60)
    assert colour == 2
    _, _, rows = pixels.read_png_rgb(out)
    assert len({pixel for row in rows for pixel in row}) > 1, "not flat"


def test_write_png_writes_a_readable_file(tmp_path, png_fixture):
    src = png_fixture(tmp_path / "in.png", 48, 32)
    out = tmp_path / "out.png"
    with _cg.Scope() as scope:
        _cg.write_png(scope, _cg.load(scope, src), out)
    assert pixels.read_ihdr(out)[:2] == (48, 32)


@pytest.mark.parametrize("kind", ["missing directory", "parent is a file",
                                  "destination is a directory",
                                  "unwritable directory"])
def test_write_png_refuses_an_unwritable_destination(tmp_path, png_fixture,
                                                     kind):
    """Every route a bad destination can take leaves as ImagingError.

    All four fail at CGImageDestinationCreateWithURL, NOT at Finalize --
    measured, and the reason the Finalize branch needs the separate test
    below rather than being reachable from here. Parametrised because the
    earlier single-case version pinned only the first of them, and the
    "parent is a file" route in particular used to escape as a bare
    NotADirectoryError from an unlink this function no longer does.
    """
    src = png_fixture(tmp_path / "in.png", 16, 16)
    if kind == "missing directory":
        out = tmp_path / "no-such-dir" / "out.png"
    elif kind == "parent is a file":
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        out = blocker / "out.png"
    elif kind == "destination is a directory":
        out = tmp_path / "adir"
        out.mkdir()
    else:
        parent = tmp_path / "ro"
        parent.mkdir()
        parent.chmod(0o500)
        out = parent / "out.png"

    try:
        with _cg.Scope() as scope:
            with pytest.raises(ImagingError) as exc:
                _cg.write_png(scope, _cg.load(scope, src), out)
    finally:
        if kind == "unwritable directory":
            out.parent.chmod(0o700)
    assert "out.png" in str(exc.value) or str(out) in str(exc.value)
    assert "create a PNG destination" in str(exc.value), (
        "the docstring's claim, asserted: all four of these fail at "
        "CGImageDestinationCreateWithURL rather than at Finalize, which is "
        "why the Finalize branch needs a test of its own"
    )


def test_write_png_raises_when_finalize_refuses(tmp_path, png_fixture,
                                                monkeypatch):
    """The branch no bad path reaches, pinned the only way it can be.

    ImageIO reports a refused write in `CGImageDestinationFinalize`'s
    return value and nowhere else, so discarding it is a silent success
    with no file behind it. Four real failure routes were measured and
    every one of them fails earlier, at the destination `_checked` -- so
    the false return is forced here. Without this test, deleting the `if
    not ...` leaves the whole file green.
    """
    src = png_fixture(tmp_path / "in.png", 16, 16)
    out = tmp_path / "out.png"
    monkeypatch.setattr(_cg._IO_LIB, "CGImageDestinationFinalize",
                        lambda dest: False)
    with _cg.Scope() as scope:
        with pytest.raises(ImagingError) as exc:
            _cg.write_png(scope, _cg.load(scope, src), out)
    assert str(out) in str(exc.value)


def test_a_refused_write_does_not_leave_the_previous_file_standing(
        tmp_path, png_fixture, monkeypatch):
    """The case the unlink-first argument got backwards.

    ImageIO writes the bytes at Finalize, so removing the destination
    BEFORE the write buys nothing -- every bad-destination route fails at
    create, before any file is touched. Removing it AFTER a refused write
    is the case that matters: without it the stale file survives the raise,
    measured at 62 bytes still present and still stale, which is an earlier
    run's output standing where this run's result should be.
    """
    src = png_fixture(tmp_path / "in.png", 48, 32)
    out = tmp_path / "out.png"
    png_fixture(out, 8, 8)
    stale = out.read_bytes()

    monkeypatch.setattr(_cg._IO_LIB, "CGImageDestinationFinalize",
                        lambda dest: False)
    with _cg.Scope() as scope:
        with pytest.raises(ImagingError):
            _cg.write_png(scope, _cg.load(scope, src), out)

    assert not out.exists(), (
        f"the write was refused and {out.name} is still there with "
        f"{len(stale)} bytes of the previous run's output in it"
    )


def test_a_refused_write_says_so_when_it_cannot_clear_the_destination(
        tmp_path, png_fixture, monkeypatch):
    """The recovery unlink can fail in the case that caused the failure.

    A directory turned read-only mid-write is exactly what makes Finalize
    return false, and it refuses the delete too. Best effort, then -- but
    the error has to say the old file is still there rather than implying
    a clean destination.
    """
    src = png_fixture(tmp_path / "in.png", 48, 32)
    parent = tmp_path / "dir"
    parent.mkdir()
    out = parent / "out.png"
    png_fixture(out, 8, 8)

    def refuse_and_lock(dest):
        parent.chmod(0o500)          # what made the write fail, mid-write
        return False

    monkeypatch.setattr(_cg._IO_LIB, "CGImageDestinationFinalize",
                        refuse_and_lock)
    try:
        with _cg.Scope() as scope:
            with pytest.raises(ImagingError) as exc:
                _cg.write_png(scope, _cg.load(scope, src), out)
    finally:
        parent.chmod(0o700)

    assert out.exists(), "the premise: the unlink could not remove it"
    assert "still there" in str(exc.value)


def test_write_png_replaces_whatever_was_at_the_destination(tmp_path,
                                                            png_fixture):
    """The destination ends up holding THIS run's output.

    This does not pin an unlink-first, and an earlier version of it was
    named as though it did: ImageIO truncates an existing destination by
    itself, so removing the unlink left the test green and the unlink was
    dropped. What is pinned is the outcome -- an 8x8 file at the
    destination does not survive a 48x32 write.
    """
    src = png_fixture(tmp_path / "in.png", 48, 32)
    out = tmp_path / "out.png"
    png_fixture(out, 8, 8)                      # the stale file
    with _cg.Scope() as scope:
        _cg.write_png(scope, _cg.load(scope, src), out)
    assert pixels.read_ihdr(out)[:2] == (48, 32), "the old file survived"


# --------------------------------------------------------------------------
# crop_to_file, and the silent wrong answer it has to stop.
# --------------------------------------------------------------------------


def _grey_at(path, x, y):
    _width, _height, rows = pixels.read_png_grey(path)
    return rows[y][x]


@pytest.mark.parametrize("y,label", [
    (0, "the origin, where sips does a centred crop instead"),
    (200, "flush with the bottom, where sips drops the crop entirely"),
    (120, "an interior row, which sips gets right"),
])
def test_crop_to_file_honours_the_origin_it_is_given(tmp_path, gradient_fixture,
                                                     y, label):
    """CGImageCreateWithImageInRect takes its rect at the image's TOP LEFT.

    Checked in PIXELS, because the size is the same whichever region comes
    back and the fixture's value names its own source row. A top-left origin
    and a bottom-left one both return 40x50 here and only the contents say
    which. 300 rows over a 0..255 ramp keeps the map monotonic, so the first
    output row is compared against the source row it should be.
    """
    src = gradient_fixture(tmp_path / "ramp.png", 40, 300)
    out = tmp_path / "cut.png"
    _cg.crop_to_file(src, 0, y, 40, 50, out)
    assert pixels.read_ihdr(out)[:2] == (40, 50)
    assert _grey_at(out, 0, 0) == _grey_at(src, 0, y), label
    assert _grey_at(out, 0, 49) == _grey_at(src, 0, y + 49), label


@pytest.mark.parametrize("rect,expected", [
    ((350, 40, 120, 80), (50, 80)),      # overruns the right edge
    ((0, 0, 900, 900), (400, 200)),      # larger than the source
    ((-10, -10, 120, 80), (110, 70)),    # starts outside
])
def test_the_raw_call_returns_the_overlap_rather_than_failing(
        tmp_path, photo_fixture, rect, expected):
    """The hazard the post-condition exists for, measured on the API itself.

    CGImageCreateWithImageInRect neither clamps to the rect nor refuses it:
    it INTERSECTS with the image and hands back the overlap, at no error.
    Without this test the post-condition below would be an assertion about
    something nobody had shown could happen.
    """
    src = photo_fixture(tmp_path / "s.png", 400, 200)
    x, y, width, height = rect
    with _cg.Scope() as scope:
        image = _cg.load(scope, src)
        cut = _cg._CG_LIB.CGImageCreateWithImageInRect(
            image, _cg.CGRect(_cg.CGPoint(float(x), float(y)),
                              _cg.CGSize(float(width), float(height))))
        assert cut, "the rect overlaps the image, so this is not the NULL case"
        scope.own(cut, kind="image")
        assert _cg.dimensions(cut) == expected


@pytest.mark.parametrize("rect", [
    (350, 40, 120, 80), (0, 0, 900, 900), (-10, -10, 120, 80),
])
def test_crop_to_file_refuses_a_rect_that_does_not_fit(tmp_path, photo_fixture,
                                                       rect):
    """And turns that overlap into the one exception type this layer raises,
    writing nothing. `imaging.crop` checks first and is the guard that
    matters; this is the one for a caller holding the handles directly."""
    src = photo_fixture(tmp_path / "s.png", 400, 200)
    out = tmp_path / "cut.png"
    with pytest.raises(ImagingError, match="does not lie inside"):
        _cg.crop_to_file(src, *rect, out)
    assert not out.exists()


def test_crop_to_file_refuses_a_rect_that_misses_the_image_entirely(
        tmp_path, photo_fixture):
    """The NULL branch, which is a different one: no overlap at all and
    CGImageCreateWithImageInRect returns NULL rather than an empty image."""
    src = photo_fixture(tmp_path / "s.png", 400, 200)
    out = tmp_path / "cut.png"
    with pytest.raises(ImagingError, match="could not crop"):
        _cg.crop_to_file(src, 900, 900, 120, 80, out)
    assert not out.exists()


def test_repeated_crops_do_not_leak(tmp_path, photo_fixture):
    """The crop's own release cycle: `load` takes three handles and the cut
    image is a fourth, and a cut image holds the decoded pixels of the region
    it was written from. Same instrument and same threshold as
    `test_repeated_loads_do_not_leak`; confirmed to go red at +1170 MB with
    `Scope.__exit__`'s CGImageRelease branch removed.
    """
    src = photo_fixture(tmp_path / "big.png", 1200, 900)
    out = tmp_path / "cut.png"
    before = _rss_mb()
    for _ in range(300):
        _cg.crop_to_file(src, 0, 0, 1200, 300, out)
    growth = _rss_mb() - before
    assert growth < 50, (
        f"resident memory grew {growth:.0f} MB over 300 crops -- something "
        f"is not being released")


# --------------------------------------------------------------------------
# Global Constraint 1.
# --------------------------------------------------------------------------


def test_ctypes_is_confined_to_this_one_module():
    """Parsed, not grepped: `from ctypes import CDLL` contains no "import
    ctypes", so a substring search misses exactly the spelling that would
    slip past review.

    An `import` statement is not the only way in, so the dynamic spellings
    are refused too. `__import__("ctypes")` and
    `importlib.import_module("ctypes")` are import statements the parser
    sees as ordinary calls, and the argument can be computed, so neither can
    be resolved here at all -- they are refused outright rather than
    inspected. `conftest.IMPURE_BUILTINS` names `__import__` for exactly
    this reason in the purity check, where `__import__('os').listdir(p)` is
    what passed an earlier version of that helper unremarked.
    """
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "__import__"):
                offenders.append(path.name)
            if any(name.split(".")[0] in {"ctypes", "importlib"}
                   for name in names):
                offenders.append(path.name)
    assert sorted(set(offenders)) == ["_cg.py"], (
        "ctypes must reach the package through `_cg.py` alone, and no module "
        "may import it by a route this check cannot follow (`__import__`, "
        "`importlib`)")


def test_the_binding_layer_raises_only_imaging_error(tmp_path):
    """Every failure leaves as ImagingError, which is what lets the executor
    catch one type per photo and carry on with the run."""
    cases = [
        lambda scope: _cg.load(scope, tmp_path / "nope.png"),
        lambda scope: _cg.load(scope, _not_an_image(tmp_path)),
        lambda scope: _cg.bitmap_context(
            scope, _cg.load(scope, pixels.write_indexed_png(
                tmp_path / "p.png", 8, 8)), 4, 4, tmp_path / "p.png"),
    ]
    for case in cases:
        with _cg.Scope() as scope:
            with pytest.raises(ImagingError):
                case(scope)


def _not_an_image(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("nope")
    return path


def test_sips_agrees_about_the_dimensions(tmp_path, photo_fixture):
    """The binding layer against the tool it replaces, on the one question
    both answer today. Cheap, and it would catch a width/height transposition
    in the argtypes that every other test here would read straight past."""
    src = photo_fixture(tmp_path / "wide.png", 320, 180)
    proc = subprocess.run(
        ["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", str(src)],
        capture_output=True, text=True, check=True)
    values = {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.strip().partition(":")
        if sep:
            values[key.strip()] = value.strip()
    with _cg.Scope() as scope:
        got = _cg.dimensions(_cg.load(scope, src))
    assert got == (int(values["pixelWidth"]), int(values["pixelHeight"]))


# --------------------------------------------------------------------------
# Naming the format. The one answer no differential can check.
# --------------------------------------------------------------------------
#
# `probe_file` writes no file, so nothing downstream can be compared to catch
# a format string that changed. Every row of `_cg.SOURCE_FORMATS` was measured
# -- a file written to that format, then put through BOTH `sips -g format` and
# `CGImageSourceGetType` -- and these are the tests that keep it that way.


def test_the_read_and_write_tables_agree_where_they_overlap():
    """`UTI` names the four formats we WRITE, `SOURCE_FORMATS` everything we
    might READ. They are separate dictionaries in opposite directions, so
    nothing but this stops them drifting apart -- and a drift would mean the
    tool could encode a HEIC it would then decline to call a HEIC."""
    for name, uti in _cg.UTI.items():
        assert _cg.SOURCE_FORMATS.get(uti) == name, (
            f"{uti} is written as {name!r} but read back as "
            f"{_cg.SOURCE_FORMATS.get(uti)!r}")


def test_no_two_utis_share_a_format_name():
    """The table is read one way and written the other; a duplicated name
    would make the round trip above pass while losing a format."""
    names = list(_cg.SOURCE_FORMATS.values())
    assert len(names) == len(set(names))


def test_a_uti_that_is_not_in_the_table_is_named_unknown():
    """The fallback, with its own control beside it.

    `unknown` is what the old probe answered when `sips` printed no format
    line, and it is deliberately NOT a name derived from the UTI string: four
    of the fifteen measured rows -- psd, tga, sgi and jp2 -- have a UTI whose
    last component is not their name, so deriving one for an unmeasured
    format would be a guess dressed as an answer.

    The `public.jpeg` control is what stops this passing for the wrong reason.
    Without it a `_pystr` that had stopped reading CFStrings at all would
    return None for everything, every lookup would miss, and the assertion
    below would be satisfied by a completely broken reader.
    """
    with _cg.Scope() as scope:
        assert _cg._format_name(_cg._cfstr(scope, "public.jpeg")) == "jpeg"
        assert _cg._format_name(
            _cg._cfstr(scope, "com.example.no-such-format")) == "unknown"


def test_a_null_type_is_named_unknown():
    """`CGImageSourceGetType` is a Get and can return NULL. `_format_name`
    takes the pointer straight from it, so NULL has to be a name and not a
    crash inside the one function documented never to raise."""
    assert _cg._format_name(None) == "unknown"


@pytest.mark.parametrize("fmt,expected", [
    ("tiff", "tiff"),
    ("bmp", "bmp"),
    ("gif", "gif"),
    ("psd", "psd"),
    ("tga", "tga"),
])
def test_formats_outside_the_corpus_keep_the_names_sips_gave_them(
        tmp_path, fmt, expected):
    """Five rows of the table that no corpus photograph exercises.

    `psd` and `tga` are here for a reason: their UTIs are
    `com.adobe.photoshop-image` and `com.truevision.tga-image`, so they are
    two of the four rows where taking the last component of the UTI would
    produce the wrong name. A table replaced by that string operation passes
    every corpus test and fails these.

    The expected names are `sips -g format`'s own output for the same files,
    asserted here rather than quoted, so a disagreement shows up as a failure
    rather than as a stale comment.
    """
    if shutil.which("magick") is None:
        pytest.skip("ImageMagick (`magick`) is not installed")
    source = pixels.write_png(tmp_path / "seed.png", 64, 48)
    target = tmp_path / f"sample.{fmt}"
    subprocess.run(["magick", str(source), f"{fmt}:{target}"],
                   check=True, capture_output=True)

    probed = _cg.probe_file(target)
    assert probed == (64, 48, expected)

    proc = subprocess.run(["/usr/bin/sips", "-g", "format", str(target)],
                          capture_output=True, text=True, check=True)
    assert proc.stdout.strip().endswith(expected), (
        f"sips calls this {proc.stdout.strip()!r}, the table calls it "
        f"{expected!r}")


def test_a_pdf_is_not_an_image(tmp_path):
    """The case that decided against reading the header properties instead.

    A PDF gives a perfectly good CGImageSource -- type `com.adobe.pdf`, count
    1 -- whose properties dictionary carries no pixel dimensions at all, so a
    properties-based probe needs a second rule to reject it. The decode simply
    returns NULL, which is also what the old `sips` probe did with one.
    """
    if shutil.which("magick") is None:
        pytest.skip("ImageMagick (`magick`) is not installed")
    source = pixels.write_png(tmp_path / "seed.png", 64, 48)
    pdf = tmp_path / "doc.pdf"
    subprocess.run(["magick", str(source), f"pdf:{pdf}"],
                   check=True, capture_output=True)
    assert pdf.exists() and pdf.stat().st_size > 0
    assert _cg.probe_file(pdf) is None


def test_probe_file_never_raises_where_load_does(tmp_path):
    """The two entry points differ on purpose, and this is the line.

    `load` raises ImagingError for anything it cannot decode -- that is how
    every operation reports a bad source. `probe_file` answers None for the
    same input, because it is the function that decides whether a file is a
    photograph at all. A `probe_file` that started raising would end a scan
    over the user's Pictures folder on its first .DS_Store.
    """
    junk = tmp_path / "note.txt"
    junk.write_text("not an image")

    with pytest.raises(ImagingError):
        with _cg.Scope() as scope:
            _cg.load(scope, junk)

    assert _cg.probe_file(junk) is None


def test_probe_file_answers_none_when_the_layer_below_raises(tmp_path,
                                                             monkeypatch):
    """The `except` in `probe_file`, which no input here can reach.

    It is there for `_cfstr`, which raises ImagingError when CoreFoundation
    refuses to build a CFString from a filename -- a name whose bytes are not
    valid UTF-8. APFS will not store such a name, so that case cannot be
    constructed on this machine and the raise is injected instead.

    Injected at `_cfurl` rather than tested through a file, because the point
    is the contract and not the trigger: `probe_file` answers None for
    anything at all, and a narrower `except` would let a future raise from
    this layer escape into `cli.scan`.
    """
    def boom(*args, **kwargs):
        raise ImagingError("could not build a CFString for a filename")

    source = pixels.write_png(tmp_path / "fine.png", 32, 24)
    assert _cg.probe_file(source) == (32, 24, "png"), \
        "the control failed: this file must probe before the raise is injected"

    monkeypatch.setattr(_cg, "_cfurl", boom)
    assert _cg.probe_file(source) is None


def test_a_null_image_measures_zero_rather_than_crashing():
    """What `probe_file`'s `width < 1` check is actually catching.

    Found by mutation: deleting `probe_file`'s `if not image: return None`
    leaves every test green, because a NULL CGImage measures 0x0 and the
    width check answers None anyway. That redundancy is deliberate and it
    rests on this behaviour, which is Apple's and not ours -- so it is pinned
    here. If a macOS release ever made this crash, or return something other
    than zero, this fails and the guard above it stops being optional.

    Contrast `CFRelease(NULL)`, which kills the process outright (measured:
    exit 133). That is why `probe_file`'s OTHER early return, the one for a
    NULL image source, is not redundant at all -- and it is not tested here,
    because a test for it would take the test runner down with it.
    """
    assert _cg.dimensions(None) == (0, 0)


def test_every_framework_function_called_is_declared():
    """An undeclared call is a SEGFAULT, not an error.

    ctypes defaults an undeclared function's arguments to C int, so a 64-bit
    handle is truncated to 32 bits and the process dies. `_declare` exists to
    stop that and is only as good as the discipline of adding to it -- which
    nothing checked until now. Two probes written against this module died
    exactly this way, on `CGImageGetBitsPerPixel` and `CGImageGetBitmapInfo`.

    BOTH HALVES ARE REQUIRED, and they are tracked as two sets because an
    earlier version of this test accepted EITHER one. `argtypes` is the half
    the two crashes above were about -- a handle passed in, truncated to C
    int. `restype` truncates a pointer coming BACK the same way, and a
    truncated handle is indistinguishable from a real one until something
    dereferences it. Measured on this file: with
    `_CG_LIB.CGImageGetWidth.argtypes` deleted and the either-or check in
    place, this test passed in 0.06 s and
    `test_load_returns_dimensions` then took pytest down with
    SIGSEGV at exit 139 -- no test report, no traceback. Turning that into
    one named failure is the whole of what this test is for, so accepting
    half a declaration disabled half of it.

    Reads the source rather than the running module, because a missing
    declaration is invisible at runtime until the call that crashes.
    """
    tree = ast.parse((PACKAGE / "_cg.py").read_text())
    libraries = {"_CF", "_CG_LIB", "_IO_LIB"}

    restypes, argtypes, used = set(), set(), set()
    for node in ast.walk(tree):
        # A declaration reads `_LIB.symbol.restype = ...`, so the library
        # attribute is itself the value of another attribute access.
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id in libraries
                and node.attr in {"restype", "argtypes"}):
            target = restypes if node.attr == "restype" else argtypes
            target.add(f"{node.value.value.id}.{node.value.attr}")
        elif (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in libraries):
            used.add(f"{node.value.id}.{node.attr}")

    assert restypes and argtypes, \
        "found no declarations at all; this test is not reading _cg"
    undeclared = sorted(
        "{} (missing {})".format(symbol, " and ".join(
            half for half, names in (("restype", restypes),
                                     ("argtypes", argtypes))
            if symbol not in names))
        for symbol in used - (restypes & argtypes))
    assert not undeclared, (
        f"called without BOTH a restype and an argtypes in _declare(): "
        f"{undeclared} -- either half missing segfaults")


# --------------------------------------------------------------------------
# The draw: what it is set to, when it is skipped, and what skipping avoids.
#
# These came out of `tests/test_resize_differential.py` and
# `tests/test_normalize_differential.py` when the retained `sips` references
# were retired. Everything those modules asserted ABOUT `sips`, or about the
# two implementations agreeing, went with them. What is here is what they
# asserted about this layer's own construction, and the subject is the
# context and the draw rather than the public function that drives them.
# --------------------------------------------------------------------------


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

    `render` asks for the source's own dimensions 577 times in a
    2734-resample corpus run, because band 4 renders the 4x frame at scale 4
    and `resize = needs_resize or scale != 1` computes True for a resample
    that changes nothing. A 1:1 draw and a skip produce the same PIXELS for a
    source with no alpha, so for most inputs no comparison of files can see
    which one ran. This watches the call.
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


def _sample_difference(one, two):
    """(samples differing, largest difference) between two PNG files.

    Computed from the decoded samples, and raising if the two files are not
    the same shape, because a count over mismatched layouts would be a number
    that means nothing.
    """
    width, height, depth, colour, first = pixels.read_png_samples(one)
    other_shape = pixels.read_png_samples(two)
    if (width, height, depth, colour) != other_shape[:4]:
        raise AssertionError(
            f"{one.name} is {width}x{height} depth {depth} type {colour}, "
            f"{two.name} is {other_shape[0]}x{other_shape[1]} depth "
            f"{other_shape[2]} type {other_shape[3]}")
    second = other_shape[4]
    deltas = [abs(a - b) for a, b in zip(first, second) if a != b]
    return len(deltas), (max(deltas) if deltas else 0)


# What a 1:1 draw costs, per source, as (samples differing, largest
# difference) out of the whole frame. Measured on this machine against the
# skip's own output, which is the same file `sips --resampleHeightWidth`
# produced for every one of these fixtures -- the retired differential
# measured these three rows against `sips` and got these same numbers.
DRAW_DAMAGE = [
    # The premultiply round trip: alpha 128 in, one unit out.
    ("rgba", pixels.write_rgba_png, 120000, 480000, 1),
    ("grey+alpha", pixels.write_grey_alpha_png, 60400, 240000, 1),
    # And the one that is not a rounding error. A `tRNS` colour key is
    # expanded to alpha on decode, the draw composites the keyed pixels onto
    # the context's black ground, and magenta comes back black at the same
    # colour type and dimensions as a correct answer.
    ("colour-key", pixels.write_colour_key_png, 120000, 480000, 255),
]


@pytest.mark.parametrize("name,writer,differing,total,largest", DRAW_DAMAGE)
def test_forcing_the_draw_at_identity_is_what_the_skip_avoids(
        tmp_path, monkeypatch, name, writer, differing, total, largest):
    """The measurement the skip rests on, run as a test.

    The skip is not an optimisation, it is a CORRECTION, and this is what
    says so: force the draw by lying to the identity check -- the target
    dimensions are unchanged, so this is the same 1:1 draw the
    implementation used to perform -- and require that it DIVERGE from the
    skip by the measured amount. If a future CoreGraphics makes the 1:1 draw
    exact, this fails and the skip becomes an optimisation after all; that is
    worth being told about, because the docstrings claim otherwise.

    Alpha-bearing sources only, in all three senses the format has: a real
    alpha channel, a greyscale one, and a colour key. The draw's damage is to
    the alpha handling, and an opaque source has none.
    """
    source = writer(tmp_path / f"{name}.png", 400, 300)
    skipped = tmp_path / "skipped.png"
    imaging.resize_and_encode(source, 400, 300, "png", None, skipped,
                              resize=True)

    monkeypatch.setattr(_cg, "dimensions", lambda image: (-1, -1))
    drawn = tmp_path / "drawn.png"
    imaging.resize_and_encode(source, 400, 300, "png", None, drawn,
                              resize=True)

    assert _sample_difference(skipped, drawn) == (differing, largest), (
        f"a 1:1 draw of a {name} source no longer diverges from the skip the "
        f"way every docstring about the skip says it does")
    assert differing < total, "the row claims the whole frame differs"


def test_the_colour_key_survives_the_identity_pass(tmp_path):
    """The other side of the row above, and the one that matters.

    The keyed pixels must still be magenta at the end of a real call. The
    forced draw above says what the skip is avoiding; this says what the
    skip preserves, in absolute values rather than as a difference.
    """
    source = pixels.write_colour_key_png(tmp_path / "keyed.png", 400, 300)
    out = tmp_path / "o.png"
    imaging.resize_and_encode(source, 400, 300, "png", None, out, resize=True)
    _, _, _, colour, samples = pixels.read_png_samples(out)
    assert colour == 6, "ImageIO expands a colour key to an alpha channel"
    assert samples[:4] == [255, 0, 255, 0], (
        f"the keyed pixel came back {samples[:4]} rather than magenta at "
        f"alpha 0 -- it was composited")


# The two members of "what `bitmap_format` refuses" a fixture can build, as
# (name, how to build one, what the refusal must say). Lab and >16bpc are the
# other members; nothing here can write either.
REFUSED_SOURCES = [
    ("indexed",
     lambda room, cmyk: pixels.write_indexed_png(room / "idx.png", 400, 300),
     "indexed"),
    ("CMYK", lambda room, cmyk: cmyk(room / "cmyk.jpg", 400, 300), "CMYK"),
]


@pytest.mark.parametrize("name,build,refused", REFUSED_SOURCES)
def test_a_source_no_context_accepts_is_refused_when_it_must_be_drawn(
        tmp_path, cmyk_fixture, name, build, refused):
    """The asymmetry the skip introduces, as a class rather than one case.

    `bitmap_format` refuses indexed, CMYK, Lab and anything above 16 bits
    per component, and the identity path never asks it -- so those sources
    resize at 1:1 and raise at every other shape. CMYK is the member that
    could turn up in a real folder: four-channel JPEGs come out of print
    workflows, and the tool this replaced resampled one without complaint.

    Both halves are pinned, here and in the identity test below, so that
    neither reads as an accident.
    """
    source = build(tmp_path, cmyk_fixture)
    with pytest.raises(ImagingError) as caught:
        imaging.resize_and_encode(source, 200, 150, "png", None,
                                  tmp_path / "o.png", resize=True)
    assert refused in str(caught.value)


@pytest.mark.parametrize("name,build,refused", REFUSED_SOURCES)
def test_the_same_source_resizes_at_identity(tmp_path, cmyk_fixture, name,
                                             build, refused):
    """And comes back as a readable PNG at the source's own dimensions, which
    is why the refusal above is an asymmetry rather than a second wrong
    answer. The identity branch never builds a destination bitmap, so the
    colour model it would refuse is not consulted."""
    source = build(tmp_path, cmyk_fixture)
    out = tmp_path / "o.png"
    imaging.resize_and_encode(source, 400, 300, "png", None, out, resize=True)
    assert imaging.probe(out)[:2] == (400, 300)


def test_a_sixteen_bit_source_keeps_its_depth_through_the_resample(
        tmp_path, png16_fixture):
    """Deliberate, and the same choice `crop` makes.

    The tool this replaced dropped a 16-bit source to 8 on every path that
    touched pixels; CoreGraphics keeps the depth. No corpus file is 16-bit,
    so this is latent -- which is why it is asserted on a fixture rather
    than left to a corpus run to notice.
    """
    source = png16_fixture(tmp_path / "deep.png", 400, 300)
    out = tmp_path / "o.png"
    imaging.resize_and_encode(source, 200, 150, "png", None, out, resize=True)
    assert pixels.read_ihdr(out)[2] == 16


# --------------------------------------------------------------------------
# The encode's options dictionary, and the one destination per call.
# --------------------------------------------------------------------------


def _captured_options(monkeypatch):
    """The options argument of every CGImageDestinationAddImage call."""
    seen = []
    real = _cg._IO_LIB.CGImageDestinationAddImage

    def record(dest, image, options):
        seen.append(options)
        return real(dest, image, options)

    monkeypatch.setattr(_cg._IO_LIB, "CGImageDestinationAddImage", record)
    return seen


def test_no_options_dictionary_is_built_for_a_lossless_write(tmp_path,
                                                             photo_fixture,
                                                             monkeypatch):
    """Lossless means no quality key, not quality 100.

    The argv test this replaced could see `-s formatOptions` was absent.
    Here the equivalent is observable only at the call: ImageIO ignores a
    quality key for PNG, so no output can tell "no dictionary" from "a
    dictionary holding 1.0". Watch the argument.
    """
    seen = _captured_options(monkeypatch)
    src = photo_fixture(tmp_path / "s.png", 64, 48)
    imaging.resize_and_encode(src, 64, 48, "png", None, tmp_path / "o.png",
                              resize=False)
    assert seen == [None], seen


def test_a_lossy_format_does_build_one(tmp_path, photo_fixture, monkeypatch):
    """The other half: omission must be specific to a quality of None."""
    seen = _captured_options(monkeypatch)
    src = photo_fixture(tmp_path / "s.png", 64, 48)
    imaging.resize_and_encode(src, 64, 48, "heic", 80, tmp_path / "o.heic",
                              resize=False)
    assert len(seen) == 1 and seen[0], seen


def test_nothing_intermediate_is_written_at_all(tmp_path, photo_fixture,
                                                monkeypatch):
    """Exactly ONE destination is created for a resizing encode, which is the
    statement a `finally` that tidied up could not fake.

    The shape before the resample and the encode shared a pass was two -- a
    PNG for the resample and the real output -- and a cleanup that unlinked
    the first would leave this test green if it only counted files at the end.
    """
    created = []
    real = _cg._IO_LIB.CGImageDestinationCreateWithURL

    def record(url, uti, count, options):
        created.append(uti)
        return real(url, uti, count, options)

    monkeypatch.setattr(_cg._IO_LIB, "CGImageDestinationCreateWithURL", record)
    src = photo_fixture(tmp_path / "s.png", 400, 300)
    imaging.resize_and_encode(src, 200, 150, "heic", 80, tmp_path / "o.heic",
                              resize=True)
    assert len(created) == 1, f"{len(created)} destinations for one encode"


# --------------------------------------------------------------------------
# Normalization's draw, which is never skipped.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["AdobeRGB1998.icc", "ROMM RGB.icc",
                                     "Display P3.icc"])
def test_normalize_never_skips_the_draw(tmp_path, profiled_fixture,
                                        monkeypatch, profile):
    """`_resample` skips its draw when the dimensions already match, and that
    is correct there because `bitmap_context` builds the destination from the
    SOURCE's own colour space -- the draw converts nothing, so skipping
    deletes no work. Here the destination is a colour space of OUR choosing,
    so the draw IS the work.

    Normalization never resizes, which means every call is the
    equal-dimensions case -- the one `_resample` skips. A skip here would
    fire on every source in the corpus and silently stop converting, and it
    would produce a plausible PNG at the right dimensions while doing it.
    Measured on a 600x400 noise PNG at these three profiles, what such a skip
    costs: 661,630 / 713,202 / 675,884 of 720,000 samples differing, largest
    difference 144 / 167 / 116.

    The draw is watched here because a 1:1 draw and a skip produce the same
    file for a source already in sRGB. What the draw ACHIEVES is
    `test_imaging.test_normalize_moves_the_numbers`, which reads the pixels.
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
