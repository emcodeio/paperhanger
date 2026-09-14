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

from paperhanger import _cg
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
    slip past review."""
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(name.split(".")[0] == "ctypes" for name in names):
                offenders.append(path.name)
    assert sorted(set(offenders)) == ["_cg.py"]


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
