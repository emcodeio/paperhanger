import struct
import subprocess
import zlib

import pytest

from tests.conftest import sips_or_skip
from tests.pixels import (read_ihdr, read_png_grey, write_grey_alpha_png,
                          write_grey_png, write_indexed_png,
                          write_interlaced_png, write_png, write_png16,
                          write_rgba_png)


def _sips_dimensions(path):
    proc = subprocess.run(
        [sips_or_skip(), "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
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


# --------------------------------------------------------------------------
# The four colour models, asserted from the IHDR bytes.
#
# From the bytes and not from a decoder, because a decoder is exactly what
# hides the difference: every one of these files decodes to plausible pixels
# through a 24-bit path, and what the imaging layer keys on is the colour
# model it is handed. A writer that quietly emitted colour type 2 for all of
# them would pass any pixel comparison and would make the fixtures useless
# for the one job they exist to do.
# --------------------------------------------------------------------------


def test_grey_png_is_colour_type_0(tmp_path):
    path = write_grey_png(tmp_path / "grey.png", 40, 30)
    width, height, depth, colour, interlace = read_ihdr(path)
    assert (width, height) == (40, 30)
    assert colour == 0, "greyscale is IHDR colour type 0"
    assert depth == 8
    assert interlace == 0
    assert _sips_dimensions(path) == (40, 30), "and a decoder agrees"


def test_grey_png_value_identifies_the_source_row(tmp_path):
    """The property the crop tests depend on: row y says it is row y."""
    path = write_grey_png(tmp_path / "gradient.png", 8, 101, top=0, bottom=200)
    _, _, rows = read_png_grey(path)
    assert rows[0][0] == 0
    assert rows[-1][0] == 200
    assert rows[50][0] == 100
    assert all(len(set(row)) == 1 for row in rows), "flat across each row"
    assert rows[0][0] < rows[1][0] < rows[2][0], "and strictly rising down"


def test_grey_png_can_be_flat(tmp_path):
    path = write_grey_png(tmp_path / "flat.png", 16, 16, top=77, bottom=77)
    _, _, rows = read_png_grey(path)
    assert {value for row in rows for value in row} == {77}


def test_png16_is_bit_depth_16(tmp_path):
    path = write_png16(tmp_path / "deep.png", 40, 30)
    width, height, depth, colour, interlace = read_ihdr(path)
    assert (width, height) == (40, 30)
    assert depth == 16, "the depth sips throws away"
    assert colour == 2, "truecolour, so the depth is the only difference"
    assert interlace == 0
    assert _sips_dimensions(path) == (40, 30)


def test_png16_carries_values_an_8_bit_round_trip_would_lose(tmp_path):
    """Otherwise a downconvert is undetectable and the fixture proves nothing.

    An 8-bit value re-expanded to 16 bits is always `v * 257`, which has
    equal high and low bytes. A fixture whose components all looked like that
    would survive a downconvert unchanged.
    """
    path = write_png16(tmp_path / "deep.png", 64, 64)
    data = path.read_bytes()
    assert read_ihdr(path)[2] == 16, "16-bit, or the rest proves nothing"
    # Every component of every pixel, as 16-bit words, from the one IDAT.
    start = data.index(b"IDAT") + 4
    length = struct.unpack(">I", data[start - 8:start - 4])[0]
    raw = zlib.decompress(data[start:start + length])
    stride = 64 * 6
    words = []
    for y in range(64):
        line = raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)]
        words += list(struct.unpack(f">{stride // 2}H", line))
    lossy = [w for w in words if (w >> 8) != (w & 0xFF)]
    assert len(lossy) > len(words) // 2, (
        "most components must differ in their two bytes, or an 8-bit "
        "round trip would be invisible"
    )


def test_rgba_png_is_colour_type_6(tmp_path):
    path = write_rgba_png(tmp_path / "alpha.png", 40, 30, alpha=128)
    width, height, depth, colour, interlace = read_ihdr(path)
    assert (width, height) == (40, 30)
    assert colour == 6, "truecolour with alpha"
    assert depth == 8
    assert interlace == 0
    assert _sips_dimensions(path) == (40, 30)


def test_grey_alpha_png_is_colour_type_4(tmp_path):
    path = write_grey_alpha_png(tmp_path / "greyalpha.png", 40, 30, alpha=128)
    width, height, depth, colour, interlace = read_ihdr(path)
    assert (width, height) == (40, 30)
    assert colour == 4, "greyscale with alpha"
    assert depth == 8
    assert interlace == 0
    assert _sips_dimensions(path) == (40, 30)


def test_indexed_png_is_colour_type_3(tmp_path):
    path = write_indexed_png(tmp_path / "paletted.png", 40, 30)
    width, height, depth, colour, interlace = read_ihdr(path)
    assert (width, height) == (40, 30)
    assert colour == 3, "palettised"
    assert depth == 8
    assert interlace == 0
    assert b"PLTE" in path.read_bytes(), "a palette PNG needs its palette"
    assert _sips_dimensions(path) == (40, 30)


@pytest.mark.parametrize("width,height", [(41, 23), (1, 1), (17, 5)])
def test_interlaced_png_holds_the_plain_writer_s_image(tmp_path, width, height):
    """Adam7, checked by something that is not our own Adam7 decoder.

    `tests/differential.py` decodes interlaced PNG and this module writes it.
    Both are ours, and two implementations of one misunderstanding agree with
    each other perfectly, so the assertion goes through `sips`: rendered to
    JPEG at the same quality, the interlaced file and the plain one must come
    out byte for byte the same, which they can only do if they decode to the
    same pixels.

    1x1 and 17x5 are here because most of the seven passes hold no pixels at
    those sizes, and a pass loop that emitted a scanline anyway would write a
    file no decoder could read.
    """
    plain = write_png(tmp_path / "plain.png", width, height, noise=True)
    woven = write_interlaced_png(tmp_path / "woven.png", width, height, noise=True)
    assert read_ihdr(woven) == (width, height, 8, 2, 1)
    assert plain.read_bytes() != woven.read_bytes()

    rendered = []
    for source in (plain, woven):
        out = tmp_path / f"{source.stem}.jpg"
        subprocess.run([sips_or_skip(), "-s", "format", "jpeg", "-s",
                        "formatOptions", "100", str(source), "--out", str(out)],
                       check=True, capture_output=True)
        rendered.append(out.read_bytes())
    assert rendered[0] == rendered[1], (
        "sips decoded the interlaced file to different pixels from the plain "
        "one, so the Adam7 layout is wrong")


def test_read_png_grey_refuses_an_rgb_png(tmp_path):
    """Converting silently is what the reader exists not to do."""
    path = write_png(tmp_path / "rgb.png", 8, 8)
    with pytest.raises(ValueError, match="expected 8-bit greyscale"):
        read_png_grey(path)


@pytest.mark.parametrize("writer", [write_grey_png, write_png16,
                                    write_rgba_png, write_grey_alpha_png,
                                    write_indexed_png, write_interlaced_png])
@pytest.mark.parametrize("width,height", [(0, 10), (10, 0), (-1, 10)])
def test_the_new_writers_refuse_a_degenerate_size(tmp_path, writer, width, height):
    path = tmp_path / "degenerate.png"
    with pytest.raises(ValueError, match=">= 1"):
        writer(path, width, height)
    assert not path.exists()


@pytest.mark.parametrize("width,height", [(0, 10), (10, 0), (0, 0), (-1, 10)])
def test_a_degenerate_size_is_refused(tmp_path, width, height):
    """The guard nothing exercised, and the reason it is there: IHDR happily
    encodes a zero, so without it the writer produces a file that is a valid
    PNG by structure and that no decoder will read -- and the test asking for
    it fails somewhere else entirely, holding a fixture instead of a size."""
    path = tmp_path / "degenerate.png"

    with pytest.raises(ValueError, match=">= 1"):
        write_png(path, width, height)

    assert not path.exists(), "and it refuses before writing"
