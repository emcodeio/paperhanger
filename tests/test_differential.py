"""The harness six later gates are built on, tested against its own failures.

Grouped by the question each group answers:

  1. Can it fail at all? Byte-level differences on output that is not an image.
  2. Does it ignore what Global Constraint 10 says to ignore -- ancillary
     chunks, filter choices, compression level, interlacing?
  3. Does it catch what GC10 says to catch -- differing pixels, a dropped
     alpha channel, a greyscale source converted to RGB, a dropped bit depth?
  4. Can it be fooled into passing? Nothing written, an empty file, a stale
     output from an earlier call, a function that was never run.

Group 4 is the reason this file is long. Five silent assertions have shipped
in this project and been caught in review, two of them inside tests written
specifically to catch silent failure, so a gate that returns "no difference"
without having compared anything is the failure mode with the track record.
"""

import struct
import subprocess
import zlib

import pytest

from tests import pixels
from tests.differential import compare

SIPS = "/usr/bin/sips"


def _writes(content):
    def fn(source, out_path):
        out_path.write_bytes(content)
    return fn


def _copies(path):
    """Write the bytes of `path`, whatever `source` is."""
    def fn(source, out_path):
        out_path.write_bytes(path.read_bytes())
    return fn


def _writes_nothing(source, out_path):
    return None


# ---------------------------------------------------------------------------
# 1. It can fail: bytes, for output that is not an image.
# ---------------------------------------------------------------------------


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


def test_a_lossy_output_is_compared_whole(tmp_path, photo_fixture):
    """heic, jpeg and avif keep the whole-file bar. Only PNG moves to pixels.

    Encoded twice at the same quality the files are identical; one quality
    step apart they are not, and the report gives an offset into the file.
    """
    src = photo_fixture(tmp_path / "s.png", 120, 90)

    def encode(quality):
        def fn(source, out_path):
            subprocess.run([SIPS, "-s", "format", "jpeg", "-s", "formatOptions",
                            str(quality), str(source), "--out", str(out_path)],
                           check=True, capture_output=True)
        return fn

    assert compare(encode(90), encode(90), src, tmp_path, suffix=".jpg") is None
    result = compare(encode(90), encode(60), src, tmp_path, suffix=".jpg")
    assert result is not None
    assert "bytes" in result


def test_two_different_formats_are_reported(tmp_path, png_fixture):
    """A PNG against a JPEG is a difference, not an exception."""
    src = png_fixture(tmp_path / "a.png", 16, 16)
    png = src.read_bytes()
    result = compare(_writes(png), _writes(b"\xff\xd8\xff\xe0jpegish"),
                     src, tmp_path)
    assert result is not None
    assert "PNG" in result and "JPEG" in result


# ---------------------------------------------------------------------------
# 2. What it must NOT call a difference.
#
# 723 of the corpus's 894 files differ as whole files while their pixels
# agree, and 10 differ in IDAT while their pixels agree. A gate that fired on
# any of these would fire on most of the corpus.
# ---------------------------------------------------------------------------


def _parts(data):
    """(IHDR payload, decompressed scanline stream) from a PNG's bytes."""
    header, idat, offset = None, bytearray(), 8
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            header = payload
        elif kind == b"IDAT":
            idat += payload
        offset += 12 + length
    return header, zlib.decompress(bytes(idat))


def _png(header, raw, extra=b"", level=6):
    return (b"\x89PNG\r\n\x1a\n"
            + pixels._chunk(b"IHDR", header)
            + extra
            + pixels._chunk(b"IDAT", zlib.compress(raw, level))
            + pixels._chunk(b"IEND", b""))


def _rewrap(data, comment=b"paperhanger", level=9):
    """The same image, recompressed and carrying an extra ancillary chunk.

    Stands in for what `sips` does to every file it writes: chunks out of the
    source's EXIF and XMP that ImageIO never emits, and its own choice of
    deflate settings.
    """
    header, raw = _parts(data)
    return _png(header, raw, extra=pixels._chunk(b"tEXt", b"Comment\x00" + comment),
                level=level)


def test_ancillary_chunks_and_compression_are_not_a_difference(tmp_path,
                                                               photo_fixture):
    src = photo_fixture(tmp_path / "s.png", 60, 40)
    plain = src.read_bytes()
    rewrapped = _rewrap(plain)
    assert plain != rewrapped, "the fixture must actually differ as bytes"

    assert compare(_writes(plain), _writes(rewrapped), src, tmp_path) is None


def _predictor(a, b, c):
    """RFC 2083 section 6.6's PaethPredictor, transcribed here on purpose.

    `pixels._paeth` is what the harness DECODES with. Encoding with it too
    would make a wrong predictor cancel out: filter with it, unfilter with
    it, and the pixels come back whatever it computes. So this is a second
    reading of the specification rather than a call to the first one, and
    mutating `pixels._paeth` now turns the Paeth case red.
    """
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _refilter(path, filter_type):
    """The same pixels, every scanline written under one PNG filter.

    Real `sips` output is adaptively filtered, so the harness's unfilter path
    runs on every corpus comparison the fast path does not settle. This
    APPLIES the four filters rather than undoing them, and applies Paeth
    through its own predictor, so the test is not the decoder checking its
    own work.
    """
    width, height, rows = pixels.read_png_rgb(path)
    stride = width * 3
    stream = bytearray()
    previous = bytes(stride)
    for row in rows:
        line = bytes(channel for pixel in row for channel in pixel)
        stream.append(filter_type)
        if filter_type == 0:
            stream.extend(line)
            previous = line
            continue
        encoded = bytearray(stride)
        for i in range(stride):
            left = line[i - 3] if i >= 3 else 0
            up = previous[i]
            up_left = previous[i - 3] if i >= 3 else 0
            if filter_type == 1:
                encoded[i] = (line[i] - left) & 0xFF
            elif filter_type == 2:
                encoded[i] = (line[i] - up) & 0xFF
            elif filter_type == 3:
                encoded[i] = (line[i] - (left + up) // 2) & 0xFF
            else:
                encoded[i] = (line[i] - _predictor(left, up, up_left)) & 0xFF
        stream.extend(encoded)
        previous = line

    return _png(struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
                bytes(stream))


@pytest.mark.parametrize("filter_type,name",
                         [(1, "Sub"), (2, "Up"), (3, "Average"), (4, "Paeth")])
def test_the_scanline_filter_is_not_a_difference(tmp_path, photo_fixture,
                                                 filter_type, name):
    """Filter choice is an encoder's business; the pixels are the bar."""
    src = photo_fixture(tmp_path / "s.png", 33, 17)
    plain = src.read_bytes()
    filtered = _refilter(src, filter_type)
    assert plain != filtered, f"the {name} fixture must differ as bytes"

    assert compare(_writes(plain), _writes(filtered), src, tmp_path) is None


def test_interlacing_is_not_a_difference(tmp_path, photo_fixture,
                                         interlaced_fixture):
    """The Adam7 corpus source: `sips` preserves it, CoreGraphics does not.

    Its pixels are identical to ours, so it must not fire the gate -- and
    reaching that verdict means decoding all seven passes.
    """
    plain = photo_fixture(tmp_path / "plain.png", 41, 23)
    woven = interlaced_fixture(tmp_path / "woven.png", 41, 23, noise=True)
    assert plain.read_bytes() != woven.read_bytes()
    assert pixels.read_ihdr(woven)[4] == 1, "the fixture must be interlaced"

    assert compare(_copies(plain), _copies(woven), plain, tmp_path) is None


def test_the_interlace_flag_is_read_before_the_streams_are_trusted(tmp_path):
    """Identical IDAT, different layout, different image. Must be reported.

    The equal-stream short-circuit proves agreement only when both files
    agree on how the stream is laid out. At 1x8 the two layouts write the
    same 32 bytes -- four of Adam7's seven passes are empty at width 1, and
    the rest emit one pixel each -- and Adam7 reads them out as rows
    0, 4, 2, 6, 1, 3, 5, 7. So the streams coincide while the images do not,
    and a fast path that looked only at the bytes would report agreement.
    """
    rows = bytearray()
    for y in range(8):
        rows.append(0)                              # filter type 0 (None)
        rows.extend((y * 30, 255 - y * 30, 40))     # the row says which row
    header = struct.pack(">IIBBBBB", 1, 8, 8, 2, 0, 0, 0)
    plain = _png(header, bytes(rows))
    woven = _png(header[:12] + b"\x01", bytes(rows))

    assert _parts(plain)[1] == _parts(woven)[1], (
        "the fixture only tests the fast path if the streams are identical")

    result = compare(_writes(plain), _writes(woven), tmp_path / "a.png", tmp_path)
    assert result is not None, "the fast path returned agreement on two images"
    assert "(0, 1)" in result, result


def test_scanline_data_longer_than_the_header_allows_is_reported(tmp_path,
                                                                 png_fixture):
    """The other half of the size check: data too long, not too short.

    Too short is caught reading past the end. Too long is caught only by
    comparing what was consumed against what was there, and an implementation
    that appended a scanline would otherwise have it silently ignored.
    """
    src = png_fixture(tmp_path / "a.png", 16, 16)
    good = src.read_bytes()
    header, raw = _parts(good)
    overlong = _png(header, raw + bytes(1 + 16 * 3))

    result = compare(_writes(good), _writes(overlong), src, tmp_path)
    assert result is not None
    assert "would not decode" in result and "needs" in result


def test_an_interlaced_png_shorter_than_its_header_claims_is_reported(tmp_path):
    """And the same check on the Adam7 path, before it allocates a grid.

    The grid is sized from the header, which is the part of a malformed file
    that can claim anything; the scanline stream is the part that has to be
    there.
    """
    woven = pixels.write_interlaced_png(tmp_path / "woven.png", 24, 16,
                                        noise=True)
    header, raw = _parts(woven.read_bytes())
    truncated = _png(header, raw[:len(raw) // 2])

    result = compare(_writes(woven.read_bytes()), _writes(truncated),
                     tmp_path / "a.png", tmp_path)
    assert result is not None
    assert "Adam7" in result and "needs" in result


def test_a_real_sips_png_is_compared_through_its_pixels(tmp_path,
                                                        photo_fixture):
    """Against output from the tool itself, not only against generated files.

    `sips` picks its filters per scanline and writes chunks of its own, so
    this is the shape the corpus gate meets. Refiltering its output to a
    single filter type leaves a file that differs in most of its bytes and in
    none of its pixels.
    """
    src = photo_fixture(tmp_path / "s.png", 48, 32)
    written = tmp_path / "sips.png"
    subprocess.run([SIPS, "-s", "format", "png", str(src), "--out", str(written)],
                   check=True, capture_output=True)
    original = written.read_bytes()
    flattened = _refilter(written, 0)
    assert original != flattened

    assert compare(_writes(original), _writes(flattened), src, tmp_path) is None


# ---------------------------------------------------------------------------
# 3. What it must catch.
# ---------------------------------------------------------------------------


def test_a_single_differing_pixel_is_reported(tmp_path):
    """One pixel in 2,400, and the report says which one."""
    class Rect:
        x, y, width, height = 7, 5, 1, 1

    plain = pixels.write_png(tmp_path / "plain.png", 60, 40, colour=(10, 10, 10))
    marked = pixels.write_marked_png(tmp_path / "marked.png", 60, 40, Rect(),
                                     base=(10, 10, 10), marker=(240, 30, 200))

    result = compare(_copies(plain), _copies(marked), plain, tmp_path)
    assert result is not None
    assert "(7, 5)" in result
    assert "red channel" in result
    assert "10" in result and "240" in result


def test_a_dropped_alpha_channel_is_reported(tmp_path):
    """The trap GC10 names: identical RGB, and the alpha is gone.

    `kCGImageAlphaNoneSkipLast` does exactly this to all 12 alpha-bearing
    corpus PNGs. Every RGB value here agrees, so a comparison of RGB triples
    passes while the channel that made the image translucent is dropped.
    """
    rgba = pixels.write_rgba_png(tmp_path / "rgba.png", 30, 20,
                                 colour=(120, 140, 110), alpha=128)
    flattened = pixels.write_png(tmp_path / "rgb.png", 30, 20,
                                 colour=(120, 140, 110))

    result = compare(_copies(rgba), _copies(flattened), rgba, tmp_path)
    assert result is not None
    assert "alpha" in result
    assert "colour type 6" in result and "colour type 2" in result


def test_a_greyscale_source_returned_as_rgb_is_reported(tmp_path):
    """Same luminance, three channels where there was one. Still a difference."""
    grey = pixels.write_grey_png(tmp_path / "grey.png", 24, 16, top=128,
                                 bottom=128)
    as_rgb = pixels.write_png(tmp_path / "rgb.png", 24, 16,
                              colour=(128, 128, 128))

    result = compare(_copies(grey), _copies(as_rgb), grey, tmp_path)
    assert result is not None
    assert "1 channel)" in result and "3 channels)" in result


def test_a_dropped_bit_depth_is_reported(tmp_path):
    """The eighth defect, and the one this project introduced."""
    deep = pixels.write_png16(tmp_path / "deep.png", 20, 20)
    shallow = pixels.write_png(tmp_path / "shallow.png", 20, 20)

    result = compare(_copies(deep), _copies(shallow), deep, tmp_path)
    assert result is not None
    assert "16 bits per channel" in result and "8" in result


def test_differing_dimensions_are_reported(tmp_path, png_fixture):
    small = png_fixture(tmp_path / "small.png", 40, 30)
    large = png_fixture(tmp_path / "large.png", 40, 31)

    result = compare(_copies(small), _copies(large), small, tmp_path)
    assert result is not None
    assert "40x30" in result and "40x31" in result


def test_a_differing_palette_is_reported(tmp_path):
    """Indexed pixels are indices; equal indices into unequal palettes are
    not equal images."""
    one = pixels.write_indexed_png(tmp_path / "one.png", 20, 20,
                                   colours=[(200, 30, 40), (30, 200, 40)])
    two = pixels.write_indexed_png(tmp_path / "two.png", 20, 20,
                                   colours=[(200, 30, 40), (30, 200, 41)])

    result = compare(_copies(one), _copies(two), one, tmp_path)
    assert result is not None
    assert "palette" in result


def test_palette_entries_no_pixel_can_reach_are_not_a_difference(tmp_path):
    """A longer palette carrying the same colours is a container difference.

    Same indices, same entries for every index in use, and some spare
    entries on the end that nothing points at. Reporting that would be the
    ancillary-chunk mistake wearing a palette.
    """
    two = [(200, 30, 40), (30, 200, 40)]
    indexed = pixels.write_indexed_png(tmp_path / "indexed.png", 20, 20,
                                       colours=two)
    # write_indexed_png cycles its indices over the palette it is given, so
    # both files are built from this one index stream to keep them equal.
    header, raw = _parts(indexed.read_bytes())
    palette = b"".join(bytes(colour) for colour in two)
    spare = palette + bytes((1, 2, 3, 4, 5, 6))
    with_short = _png(header, raw, extra=pixels._chunk(b"PLTE", palette))
    with_long = _png(header, raw, extra=pixels._chunk(b"PLTE", spare))
    assert with_short != with_long

    assert compare(_writes(with_short), _writes(with_long),
                   tmp_path / "a.png", tmp_path) is None


def test_a_sixteen_bit_difference_is_reported_at_full_depth(tmp_path):
    """A difference of one in the low byte of a 16-bit sample still counts."""
    deep = pixels.write_png16(tmp_path / "deep.png", 16, 16)
    header, raw = _parts(deep.read_bytes())
    raw = bytearray(raw)
    raw[2] ^= 0x01          # the low byte of the first sample, past the filter
    nudged = _png(header, bytes(raw))

    result = compare(_copies(deep), _writes(nudged), deep, tmp_path)
    assert result is not None
    assert "(0, 0)" in result and "red channel" in result
    assert "largest difference 1" in result


# ---------------------------------------------------------------------------
# 4. It cannot be fooled into passing.
# ---------------------------------------------------------------------------


def test_an_implementation_that_writes_nothing_is_reported(tmp_path,
                                                           png_fixture):
    src = png_fixture(tmp_path / "a.png", 16, 16)
    result = compare(_writes(b"output"), _writes_nothing, src, tmp_path)
    assert result is not None
    assert "CoreGraphics wrote no file" in result


def test_neither_side_writing_anything_is_not_a_match(tmp_path, png_fixture):
    """Two absent files are equal, and that must not read as agreement."""
    src = png_fixture(tmp_path / "a.png", 16, 16)
    result = compare(_writes_nothing, _writes_nothing, src, tmp_path)
    assert result is not None
    assert "sips wrote no file" in result and "CoreGraphics wrote no file" in result


def test_two_empty_files_are_not_a_match(tmp_path, png_fixture):
    """Nor are two files of nothing."""
    src = png_fixture(tmp_path / "a.png", 16, 16)
    result = compare(_writes(b""), _writes(b""), src, tmp_path)
    assert result is not None
    assert "empty" in result


def test_an_earlier_call_s_output_is_not_reused(tmp_path, png_fixture):
    """The corpus gates call `compare` in a loop on one `tmp_path`.

    An implementation that wrote the fourth file and not the fifth would
    otherwise be compared against its own fourth output.
    """
    src = png_fixture(tmp_path / "a.png", 16, 16)
    assert compare(_writes(b"first"), _writes(b"first"), src, tmp_path) is None

    result = compare(_writes(b"first"), _writes_nothing, src, tmp_path)
    assert result is not None
    assert "no file" in result


def test_both_implementations_are_run_on_the_source(tmp_path, png_fixture):
    """Against the harness comparing one side with itself.

    A gate that ran the reference twice, or read the source back and called
    it a match, would return None for every input ever given to it.
    """
    src = png_fixture(tmp_path / "a.png", 16, 16)
    calls = []

    def record(label, content):
        def fn(source, out_path):
            calls.append((label, source, out_path))
            out_path.write_bytes(content)
        return fn

    assert compare(record("old", b"x"), record("new", b"x"), src, tmp_path) is None
    assert [label for label, _, _ in calls] == ["old", "new"]
    assert {source for _, source, _ in calls} == {src}
    old_out, new_out = (out for _, _, out in calls)
    assert old_out != new_out, "each side needs its own output path"
    assert old_out != src and new_out != src


def test_the_two_sides_write_into_separate_directories(tmp_path, png_fixture):
    """An implementation may write beside its output, and one did.

    `resize_and_encode` staged a sibling PNG until the encode moved to
    ImageIO, and `sips` writes temporary files of its own. Sharing a
    directory would let one side's leavings land on the other's, which is a
    difference the gate would never see -- and the cost of keeping them
    apart is one `mkdir`.
    """
    src = png_fixture(tmp_path / "a.png", 16, 16)
    seen = []

    def fn(source, out_path):
        seen.append(out_path.parent)
        out_path.write_bytes(b"x")

    compare(fn, fn, src, tmp_path)
    assert seen[0] != seen[1]


def test_an_undecodable_png_is_reported_not_raised(tmp_path, png_fixture):
    """A truncated PNG is the new implementation's defect, not the gate's."""
    src = png_fixture(tmp_path / "a.png", 16, 16)
    good = src.read_bytes()
    result = compare(_writes(good), _writes(b"\x89PNG\r\n\x1a\nrubbish"),
                     src, tmp_path)
    assert result is not None
    assert "CoreGraphics wrote a PNG this harness cannot read" in result


def test_a_png_whose_pixel_data_is_short_is_reported(tmp_path, png_fixture):
    """The channel-count check, reached from the other side.

    A header claiming three channels over data that holds one is how a
    wrongly built bitmap context would show up if it ever produced a file at
    all, and it must be a report rather than a passing comparison.
    """
    src = png_fixture(tmp_path / "a.png", 16, 16)
    good = src.read_bytes()
    grey = pixels.write_grey_png(tmp_path / "grey.png", 16, 16)
    header, raw = _parts(grey.read_bytes())
    header = bytearray(header)
    header[9] = 2               # colour type 0 -> 2; the scanlines are untouched
    lying = _png(bytes(header), raw)

    result = compare(_writes(good), _writes(lying), src, tmp_path)
    assert result is not None
    assert "cannot read" in result or "would not decode" in result
