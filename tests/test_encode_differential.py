"""The encode gate: ImageIO against `sips -s format F -s formatOptions N`.

THE QUALITY NUMBERS TRANSFER, and that is the first thing here. `sips` is
ImageIO underneath and `-s formatOptions N` is
`kCGImageDestinationLossyCompressionQuality` at N/100, so the four defaults in
`formats.DEFAULT_QUALITY` are not re-derived, re-tuned or improved: they are
asserted to produce the same bytes they did before. heic 85 reproducing heic
80's file exactly comes through with them, which is the quantization that made
80 the default in the first place.

WHOLE FILES ARE THE BAR HERE, unlike the PNG gates, and this file is where the
measurement behind that finally covers the case it was written for. Global
Constraint 10 keeps whole-file comparison for lossy because the design spec
measured jpeg 80/90 and heic 80/85/90 byte-identical; Task 2 added that `sips`
copies a source's ICC profile rather than re-stamping it, so the profile's
creation `dateTime` -- the clock that rules whole-file comparison out for PNG
-- does not enter. What nobody could measure until this task existed was
`sips` AGAINST ImageIO on a lossy encode of an ICC-tagged source. Measured
now, and it splits in two:

  * AN ICC PROFILE CHANGES NOTHING. A 400x300 noise PNG tagged AdobeRGB1998,
    Display P3, ROMM RGB or ITU-2020 encodes byte-identically to `sips` at
    jpeg 90, heic 80 and avif 85. ImageIO writes the same `colr` box, byte for
    byte, whatever the profile is. The whole-file bar survives.
  * EXIF CHANGES EVERYTHING, and it was the confound hiding inside the tagged
    fixture: `sips --matchTo` writes an `eXIf` chunk alongside the `iCCP` one,
    so "tagged" and "carries metadata" arrived together. Separated by
    rebuilding the file with one chunk removed at a time, on the same 400x300
    source:

      source chunks    jpeg 90     heic 80             avif 85     png
      neither          identical   identical           identical   identical
      iCCP only        identical   identical           identical   identical
      eXIf only        identical   127 bytes smaller   127 smaller 425 smaller
      both             identical   127 bytes smaller   127 smaller 425 smaller

    `CGImageDestinationAddImage` writes the picture and its colour space and
    nothing else; `sips` copies the source's EXIF into the output. In the HEIF
    family that is a second item -- an `Exif` item, an `iref cdsc` pointing at
    the picture, 66 bytes in `mdat` -- and the coded picture underneath is
    identical. So the two sides agree on the PICTURE and differ on what rides
    with it.

So whole-file comparison survives at tier 1, where the fixtures carry no
metadata, and is the bar for every test above the corpus section. It does NOT
survive over real photographs: 21 of the 27 coverage images differ for heic
and avif, all of them by metadata alone. The corpus gate at the bottom
compares the coded picture and accounts for the rest per file, which is what
the crop gate already does for fact 8.

THE PROGRESSIVE JPEG IS THE SECOND DIVERGENCE, and it has the same shape and
probably the same cause. `sips` inherits a source JPEG's progressive scan;
ImageIO writes baseline. Four of the 27 coverage images are progressive
sources, and on them our JPEG is 10.2% to 21.0% larger with an entropy-coded
stream that cannot be compared to `sips`' at all -- a different scan structure
is a different file, not a worse one.

ASKED FOR PROGRESSIVE, ImageIO produces `sips`' OWN SCAN. Measured on all four,
`{JFIF: {IsProgressive: true}}` against `sips -s format jpeg -s formatOptions
90`:

  bokeh_nature_scene_7629.jpg                4345454    byte-identical
  mountain_landscape_sunset_5677.jpg        11337145    byte-identical
  snowy_mountain_range_with_forest_2565.jpg   809714    2 bytes differ
  abstract_blue_texture_4503.JPG            3896999    100 bytes shorter

and the entropy-coded scan -- everything from `SOS` on, 0.8 to 11.3 MB of it
-- is byte-identical on **four of four**. That is the claim worth making, and
an earlier draft of this file made a weaker and wronger one ("reproduces
sips' file exactly"), which is false by two bytes on the third row and by a
hundred on the fourth. The two bytes are the JFIF density, 96 dpi carried
forward against ImageIO's 72; the hundred are the APP1 Exif segment, `sips`
copying four IFD0 entries where ImageIO synthesises one. Both are the same
metadata divergence as above, not a coding difference. So the picture is not
merely equivalent, it is the same bits, and the choice between the two scan
structures is ours to make rather than a limit we ran into. It is pinned
below as the decision it is.
"""

import ctypes
import shutil
import struct
import subprocess
import zlib
from ctypes import c_void_p
from pathlib import Path

import pytest

from paperhanger import _cg, formats, imaging
from tests import pixels
from tests.differential import PNG_SIGNATURE, _format_of, compare

# Every (format, quality) pair the pipeline can produce, plus the two extra
# qualities that make the mapping observable. heic 85 is here because it must
# come back as heic 80's file byte for byte -- `formats.py` records that as
# the reason the default is 80, and a quality mapping that silently did
# nothing would produce the same coincidence.
CASES = [("jpeg", 90), ("jpeg", 80), ("heic", 80), ("heic", 85), ("heic", 90),
         ("avif", 85), ("png", None)]

# Four ICC profiles, spanning wider-than-sRGB, a display gamut, ProPhoto's
# ROMM primaries and Rec. 2020. Named as files under /System/Library/
# ColorSync/Profiles, which `profiled_fixture` skips on if absent.
PROFILES = ["AdobeRGB1998.icc", "Display P3.icc", "ROMM RGB.icc",
            "ITU-2020.icc"]

# The lossy three at their shipped defaults, for the tests that are about a
# source's properties rather than about the quality number.
AT_DEFAULTS = [("heic", 80), ("jpeg", 90), ("avif", 85)]


def _sips_encode_reference(source, fmt, quality, out_path):
    """What this project shipped before Task 5.

    `-s format` ahead of the paths -- imaging fact 7; placed later it is
    silently dropped and the output keeps the source's format whatever the
    name says. `formatOptions` omitted entirely for a lossless format, which
    is the same distinction `_quality_options` makes with None.
    """
    argv = [imaging.SIPS, "-s", "format", fmt]
    if quality is not None:
        argv += ["-s", "formatOptions", str(quality)]
    argv += [str(source), "--out", str(out_path)]
    subprocess.run(argv, check=True, capture_output=True)


def _reference(fmt, quality):
    def old(source, out_path):
        _sips_encode_reference(source, fmt, quality, out_path)
    return old


def _replacement(fmt, quality, width, height, resize=False):
    def new(source, out_path):
        imaging.resize_and_encode(source, width, height, fmt, quality,
                                  out_path, resize=resize)
    return new


def _suffix(fmt):
    """The extension the format's bytes must be written under.

    `formats.extension` rather than a second table: the harness names its
    output by this, and everything downstream of a real run goes by the name.
    Passing the wrong one writes HEIC bytes into `out.png`.
    """
    return "." + formats.extension(fmt)


# ---------------------------------------------------------------------------
# PNG chunk surgery, to separate "tagged" from "carries metadata".
# ---------------------------------------------------------------------------


def _chunks(data: bytes):
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


def _without(source, out_path, drop):
    """`source` rewritten without the named chunks.

    Whole chunks are copied, CRC included, so nothing is recomputed and the
    only difference between the two files is the chunk that is gone. That is
    what makes the pair of comparisons below a controlled measurement rather
    than two files that happen to differ.
    """
    kept = [raw for kind, raw in _chunks(Path(source).read_bytes())
            if kind not in drop]
    out_path = Path(out_path)
    out_path.write_bytes(PNG_SIGNATURE + b"".join(kept))
    return out_path


def _kinds(path):
    return [kind.decode("ascii", "replace")
            for kind, _ in _chunks(Path(path).read_bytes())]


# ---------------------------------------------------------------------------
# The quality mapping, byte for byte.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt,quality", CASES)
def test_encode_matches_sips_byte_for_byte(tmp_path, photo_fixture, fmt,
                                           quality):
    """N/100 is the whole of the mapping, and this is what says so.

    Noisy source: a flat colour compresses to almost nothing and would agree
    under a quality that had been dropped on the floor.
    """
    src = photo_fixture(tmp_path / "s.png", 800, 600)
    assert compare(_reference(fmt, quality), _replacement(fmt, quality, 800, 600),
                   src, tmp_path, suffix=_suffix(fmt)) is None


def test_the_comparison_can_fail(tmp_path, photo_fixture):
    """Guards the gate above. Two encodes of the same source at different
    qualities must be reported as different, or every row of CASES is
    measuring that `sips` agrees with itself."""
    src = photo_fixture(tmp_path / "s.png", 400, 300)
    result = compare(_reference("heic", 80), _replacement("heic", 40, 400, 300),
                     src, tmp_path, suffix=".heic")
    assert result is not None, "the harness read heic 40 as equal to heic 80"


def test_the_quality_number_reaches_the_encoder(tmp_path, photo_fixture):
    """Byte-identity against `sips` would also hold if BOTH sides ignored the
    number. The sizes have to move, monotonically, with it."""
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
    """The numbers this task must not change, asserted where the encoder is.

    `formats.py` is where they live and nothing here touches it; the point of
    repeating them is that this file is the one that would notice a quality
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


# ---------------------------------------------------------------------------
# The measurement nobody had made: a lossy encode of an ICC-tagged source.
# ---------------------------------------------------------------------------


def test_an_icc_tagged_source_encodes_byte_identically(tmp_path,
                                                       profiled_fixture):
    """Four profiles, three lossy formats, whole files.

    This is what Global Constraint 10's lossy exemption was resting on and
    could not check. The EXIF chunk `sips --matchTo` writes alongside the
    profile is removed first, because it is a second variable and it is the
    one that actually moves the bytes -- the test below measures that half.
    """
    differences = []
    for profile in PROFILES:
        tagged = profiled_fixture(tmp_path / f"{profile}.png", 400, 300, profile)
        source = _without(tagged, tmp_path / f"icc-only-{profile}.png",
                          drop=(b"eXIf",))
        assert "iCCP" in _kinds(source), (
            f"{profile} did not survive the chunk surgery; the test would "
            f"be measuring an untagged file")
        assert "eXIf" not in _kinds(source)
        for fmt, quality in AT_DEFAULTS:
            result = compare(_reference(fmt, quality),
                             _replacement(fmt, quality, 400, 300), source,
                             tmp_path / f"{profile}-{fmt}", suffix=_suffix(fmt))
            if result is not None:
                differences.append(f"{profile} at {fmt} {quality}: {result}")
    assert not differences, "\n".join(differences)


@pytest.mark.parametrize("fmt,quality", AT_DEFAULTS)
def test_the_source_exif_is_what_the_two_sides_disagree_about(tmp_path,
                                                              profiled_fixture,
                                                              fmt, quality):
    """The other half, pinned rather than worked around.

    `sips` copies the source's EXIF into its output and ImageIO does not, so
    this asserts that the difference EXISTS for a source carrying one and
    vanishes when the chunk is removed -- from the same tagged file, so the
    ICC profile is held constant across the two comparisons. Without the
    second half this could pass against an implementation that differed for
    some other reason entirely.
    """
    tagged = profiled_fixture(tmp_path / "tagged.png", 400, 300,
                              "AdobeRGB1998.icc")
    assert "eXIf" in _kinds(tagged), (
        "sips --matchTo no longer writes an eXIf chunk, so this fixture no "
        "longer carries the metadata the test is about")

    with_exif = compare(_reference(fmt, quality),
                        _replacement(fmt, quality, 400, 300), tagged,
                        tmp_path / "with", suffix=_suffix(fmt))
    stripped = _without(tagged, tmp_path / "stripped.png", drop=(b"eXIf",))
    without_exif = compare(_reference(fmt, quality),
                           _replacement(fmt, quality, 400, 300), stripped,
                           tmp_path / "without", suffix=_suffix(fmt))

    assert without_exif is None, (
        f"the same source without its eXIf chunk still differs: {without_exif}")
    if fmt == "jpeg":
        # THIS ROW IS A PROPERTY OF THE FIXTURE, NOT OF JPEG, and an earlier
        # draft asserted it as though it were the container's ("both tools
        # drop a PNG's eXIf on the way to JPEG"). They do not. Measured, both
        # write an APP1 Exif segment, and `sips` copies the source's IFD0
        # entries into it where ImageIO synthesises its own: on a
        # from-scratch source carrying one IFD0 entry, `sips`' APP1 is 90
        # bytes against our 78, and at two entries 102 against the same 78.
        # The files agree HERE only because `sips --matchTo` writes an eXIf
        # holding exactly what ImageIO would have synthesised anyway. The
        # test below builds a source that does not, and the JPEGs differ.
        assert with_exif is None, (
            f"sips --matchTo's own eXIf stopped round-tripping through JPEG: "
            f"{with_exif}")
    else:
        assert with_exif is not None, (
            f"a {fmt} encode of an EXIF-bearing source matched sips byte for "
            f"byte; sips has stopped copying the metadata, or we have started")


def _with_exif(path, entries):
    """A noise PNG carrying an `eXIf` chunk holding `entries` in IFD0.

    Built here rather than through `sips --matchTo`, which is the whole
    point: that fixture's EXIF happens to be what ImageIO re-synthesises, so
    it cannot show a divergence that depends on the payload. `entries` are
    (tag, SHORT value) pairs -- 0x0112 is Orientation.
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
    for kind, raw in _chunks(base):
        rebuilt += raw
        if kind == b"IHDR":
            rebuilt += chunk
    path = Path(path)
    path.write_bytes(bytes(rebuilt))
    assert "eXIf" in _kinds(path)
    return path


def test_a_jpeg_loses_the_source_exif_too(tmp_path):
    """JPEG is not exempt; the fixture above only made it look exempt.

    An Orientation of 6 means "rotate 90 degrees on display", so this is the
    one EXIF tag whose loss a viewer could SEE. `sips` carries it into the
    output; we do not. The corpus makes that harmless -- censused over all
    894 images, 245 JPEGs and 12 PNGs carry Orientation 1, one carries the
    invalid 0, 128 carry EXIF with no Orientation tag, and NONE carries a
    rotating value -- but harmless because of the corpus is not the same as
    absent, and this is where it is written down.

    The scan is asserted identical alongside, because that is what makes the
    difference metadata rather than a different picture.
    """
    source = _with_exif(tmp_path / "oriented.png", [(0x0112, 6)])
    old_out = tmp_path / "sips.jpg"
    new_out = tmp_path / "cg.jpg"
    _sips_encode_reference(source, "jpeg", 90, old_out)
    imaging.resize_and_encode(source, 400, 300, "jpeg", 90, new_out,
                              resize=False)

    old, new = old_out.read_bytes(), new_out.read_bytes()
    assert old != new, (
        "sips stopped carrying the source's Orientation into its JPEG, so "
        "there is no divergence here to pin")
    assert _jpeg_scan(old) == _jpeg_scan(new), (
        "the pictures differ, which would make this a coding difference "
        "rather than a metadata one")
    assert _orientation(old) == 6, "the premise: sips carries the tag"
    assert _orientation(new) is None, (
        f"our JPEG carries Orientation {_orientation(new)}; something started "
        f"copying the source's EXIF")


@pytest.mark.parametrize("fmt,quality", [("heic", 80), ("avif", 85)])
def test_the_picture_under_the_metadata_is_identical(tmp_path,
                                                     profiled_fixture, fmt,
                                                     quality):
    """What the divergence above is worth: nothing, to the picture.

    `sips`' `mdat` is the Exif item followed by exactly our `mdat`. So the
    coded frame is the same bytes and the difference is entirely the item
    riding alongside it -- which is what makes dropping it a decision about
    metadata rather than a change to the wallpaper.
    """
    source = profiled_fixture(tmp_path / "tagged.png", 400, 300,
                              "AdobeRGB1998.icc")
    old_out = tmp_path / f"sips{_suffix(fmt)}"
    new_out = tmp_path / f"cg{_suffix(fmt)}"
    _sips_encode_reference(source, fmt, quality, old_out)
    imaging.resize_and_encode(source, 400, 300, fmt, quality, new_out,
                              resize=False)

    old_picture = _heif_picture(old_out.read_bytes())
    new_picture = _heif_picture(new_out.read_bytes())
    assert new_picture, "no mdat in our output"
    assert old_picture.endswith(new_picture), (
        f"the coded picture differs: sips {len(old_picture)} bytes, ours "
        f"{len(new_picture)}")
    extra = old_picture[:len(old_picture) - len(new_picture)]
    assert b"Exif" in extra, (
        f"sips' extra {len(extra)} bytes are not the Exif item: {extra[:40]!r}")


# ---------------------------------------------------------------------------
# Containers, read out of the bytes.
# ---------------------------------------------------------------------------


def _boxes(data: bytes):
    """Top-level ISO base media boxes, as (kind, offset, size, header size)."""
    out = []
    offset = 0
    while offset + 8 <= len(data):
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8].decode("ascii", "replace")
        header = 8
        if size == 1:
            size = struct.unpack(">Q", data[offset + 8:offset + 16])[0]
            header = 16
        elif size == 0:
            # "to the end of the file", which is how ImageIO writes mdat.
            size = len(data) - offset
        if size < header:
            break
        out.append((kind, offset, size, header))
        offset += size
    return out


def _heif_picture(data: bytes) -> bytes:
    """The `mdat` payload: the coded picture, plus any item stored beside it."""
    for kind, offset, size, header in _boxes(data):
        if kind == "mdat":
            return data[offset + header:offset + size]
    return b""


def _jpeg_scan(data: bytes) -> bytes:
    """Everything from the start-of-scan marker on: the entropy-coded image."""
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            return b""
        marker = data[offset + 1]
        if marker == 0xDA:
            return data[offset:]
        offset += 2 + struct.unpack(">H", data[offset + 2:offset + 4])[0]
    return b""


def _jpeg_segments(data: bytes):
    """(marker byte, whole segment) up to the scan, then (0xDA, the rest).

    The whole segment, header included, so a comparison over these is a
    comparison over every byte of the file.
    """
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


def _jpeg_without_metadata(data: bytes) -> bytes:
    """Every segment but the APPn ones, concatenated. Empty for a non-JPEG.

    APP0 through APP15 are where JFIF density, Exif and an ICC profile live,
    and they are what the two encoders disagree about. Everything else -- the
    frame header, the quantization and Huffman tables, the scan -- decides
    the picture, so comparing this is comparing the picture and its coding
    parameters without the metadata riding alongside.
    """
    segments = _jpeg_segments(data)
    if not segments:
        return b""
    return b"".join(payload for marker, payload in segments
                    if not 0xE0 <= marker <= 0xEF)


def _jpeg_tables(data: bytes) -> bytes:
    """The DQT segments: the quantization tables, which are the quality.

    They survive the progressive/baseline divergence -- measured identical
    between `sips`' progressive output and our baseline one at the same
    quality on all four progressive coverage images, 138 bytes each, and
    different at a different quality on all six tried. So they are what the
    corpus gate can still hold a progressive source to once the scans have
    become incomparable.
    """
    return b"".join(payload for marker, payload in _jpeg_segments(data)
                    if marker == 0xDB)


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


def test_the_container_readers_are_not_vacuous(tmp_path, photo_fixture):
    """The two helpers above decide what the corpus gate below reports, so a
    version of either that returned an empty string for everything would make
    that gate pass over any pair of files at all."""
    src = photo_fixture(tmp_path / "s.png", 400, 300)
    heic = tmp_path / "o.heic"
    jpg = tmp_path / "o.jpg"
    imaging.resize_and_encode(src, 400, 300, "heic", 80, heic, resize=False)
    imaging.resize_and_encode(src, 400, 300, "jpeg", 90, jpg, resize=False)

    picture = _heif_picture(heic.read_bytes())
    assert 0 < len(picture) < heic.stat().st_size
    data = jpg.read_bytes()
    scan = _jpeg_scan(data)
    assert 0 < len(scan) < jpg.stat().st_size
    body = _jpeg_without_metadata(data)
    assert len(scan) < len(body) < len(data), (
        "the body must hold more than the scan and less than the file, or it "
        "is not excluding the APPn segments")
    assert 0 < len(_jpeg_tables(data)) < len(body)
    assert _heif_picture(b"not a container at all") == b""
    assert _jpeg_scan(b"\xff\xd8not a jpeg") == b""
    assert _jpeg_without_metadata(b"\xff\xd8not a jpeg") == b""
    assert _jpeg_tables(b"\xff\xd8not a jpeg") == b""


# ---------------------------------------------------------------------------
# The formats, and the one that takes no quality.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt,quality", CASES)
def test_the_bytes_are_the_format_that_was_asked_for(tmp_path, photo_fixture,
                                                     fmt, quality):
    """Named by UTI to ImageIO, and read back out of the bytes.

    Not out of the extension: `sips` without `-s format` keeps the source's
    format whatever the output is called, and the mistake this guards against
    -- a UTI that ImageIO maps somewhere else -- would produce exactly that
    shape of wrong answer.
    """
    src = photo_fixture(tmp_path / "s.png", 200, 150)
    out = tmp_path / f"o{_suffix(fmt)}"
    imaging.resize_and_encode(src, 200, 150, fmt, quality, out, resize=False)
    expected = {"heic": "HEIF", "jpeg": "JPEG", "avif": "AVIF", "png": "PNG"}
    assert _format_of(out.read_bytes()) == expected[fmt]
    assert imaging.probe(out)[:2] == (200, 150)


def test_an_unknown_format_is_refused_by_name(tmp_path, photo_fixture):
    """It used to be `sips` exiting 13.

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


def _captured_options(monkeypatch):
    """The options argument of every CGImageDestinationAddImage call."""
    seen = []
    real = _cg._IO_LIB.CGImageDestinationAddImage

    def record(dest, image, options):
        seen.append(options)
        return real(dest, image, options)

    monkeypatch.setattr(_cg._IO_LIB, "CGImageDestinationAddImage", record)
    return seen


def test_png_takes_no_quality(tmp_path, photo_fixture):
    """Lossless means no quality key, not quality 100."""
    src = photo_fixture(tmp_path / "s.png", 400, 300)
    out = tmp_path / "o.png"
    imaging.resize_and_encode(src, 400, 300, "png", None, out, resize=False)
    assert imaging.probe(out)[:2] == (400, 300)


def test_no_options_dictionary_is_built_for_a_lossless_write(tmp_path,
                                                             photo_fixture,
                                                             monkeypatch):
    """The argv test this replaces could see `-s formatOptions` was absent.

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


# ---------------------------------------------------------------------------
# One pass. The staging file Task 4 introduced is gone.
# ---------------------------------------------------------------------------


def test_no_staging_file_is_left_behind(tmp_path, photo_fixture):
    src = photo_fixture(tmp_path / "s.png", 400, 300)
    out = tmp_path / "out" / "o.heic"
    out.parent.mkdir()
    imaging.resize_and_encode(src, 200, 150, "heic", 80, out, resize=True)
    assert [p.name for p in out.parent.iterdir()] == ["o.heic"]
    assert not list(tmp_path.glob("*.resized*"))


def test_nothing_intermediate_is_written_at_all(tmp_path, photo_fixture,
                                                monkeypatch):
    """The stronger statement, and the one a `finally` that tidied up could
    not fake: exactly ONE destination is created for the whole call.

    Task 4's shape was two -- a PNG for the resample and the real output --
    and a cleanup that unlinked the first would leave this test green if it
    only counted files at the end.
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


def test_a_resizing_encode_lands_on_both_axes(tmp_path, photo_fixture):
    """The fused call still uses both of the caller's numbers -- fact 3.

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


# ---------------------------------------------------------------------------
# The pinned divergence: a progressive JPEG source.
# ---------------------------------------------------------------------------


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
    """A progressive JPEG, written through ImageIO because nothing else can.

    `sips` takes its output's scan structure from its INPUT, so no PNG
    fixture produces one -- every JPEG `sips` writes from this suite's
    fixtures is baseline, and the divergence below is only visible with a
    progressive source. ImageIO writes one when asked, which is the same
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
        "honouring {JFIF: {IsProgressive: true}}, and the test below would "
        "pass while measuring nothing")
    return out_path


def test_a_progressive_source_comes_back_baseline(tmp_path, photo_fixture):
    """Intended, measured, and not free: about 10-21% more bytes.

    `sips` inherits a source JPEG's progressive scan and ImageIO writes
    baseline. The pictures are the same picture -- same dimensions, same
    sampling factors -- but the entropy-coded streams are not comparable, so
    this is the one lossy case where byte identity was never reachable.
    ImageIO will write progressive if asked for it -- the fixture above is
    that request, and asked for it on the four progressive corpus sources it
    produces `sips`' own entropy-coded scan byte for byte, two of the four
    whole files included. So what is pinned here is a decision, and one that
    costs nothing but bytes: the encode takes the frame from its source and
    nothing else.
    """
    base = photo_fixture(tmp_path / "s.png", 800, 600)
    source = _write_progressive_jpeg(base, tmp_path / "progressive.jpg")

    old_out = tmp_path / "sips.jpg"
    new_out = tmp_path / "cg.jpg"
    _sips_encode_reference(source, "jpeg", 90, old_out)
    imaging.resize_and_encode(source, 800, 600, "jpeg", 90, new_out,
                              resize=False)

    assert _jpeg_is_progressive(old_out.read_bytes()), (
        "sips stopped inheriting the source's progressive scan, so the "
        "divergence this test pins no longer exists")
    assert not _jpeg_is_progressive(new_out.read_bytes()), (
        "our JPEG came back progressive; ImageIO's default changed, or "
        "something started asking it for one")
    assert imaging.probe(new_out)[:2] == imaging.probe(old_out)[:2]


def test_a_baseline_source_stays_byte_identical(tmp_path, photo_fixture):
    """The other side of it: the divergence is the source's scan structure
    and nothing else, so a baseline JPEG source still agrees exactly."""
    base = photo_fixture(tmp_path / "s.png", 400, 300)
    source = tmp_path / "baseline.jpg"
    _sips_encode_reference(base, "jpeg", 90, source)
    if _jpeg_is_progressive(source.read_bytes()):
        pytest.skip("the baseline fixture came back progressive")
    assert compare(_reference("jpeg", 90), _replacement("jpeg", 90, 400, 300),
                   source, tmp_path, suffix=".jpg") is None


# ---------------------------------------------------------------------------
# Tier 2: the 27-image corpus sample.
# ---------------------------------------------------------------------------


def _describe_encode(source, fmt, old_out, new_out):
    """None when the two encodes agree, or a sentence saying how they do not.

    WHOLE FILES FIRST, because that is the bar and 6 of the 27 meet it. When
    they differ, the difference has to be ATTRIBUTABLE, and the two branches
    hold each other to the same standard -- an earlier version did not, and
    the JPEG one waved through any header difference at all once the scans
    matched, which would have accepted a changed quantization table as
    agreement:

      * HEIF: our `mdat` must be a suffix of `sips`', and the bytes in front
        of it must be the `Exif` item.
      * JPEG: every segment that is not an APPn must be identical, the scan
        included. An APPn is metadata -- JFIF density, Exif, the colour
        profile -- and a difference there is the divergence being measured;
        a difference in SOF, DQT or DHT is a different picture and is
        reported.

    A progressive source is the one case where no comparison of the scan is
    possible, since a different scan structure is a different stream, and it
    is reported as the known divergence rather than passed over in silence.
    """
    old, new = old_out.read_bytes(), new_out.read_bytes()
    if old == new:
        return None

    if fmt in ("heic", "avif"):
        old_picture, new_picture = _heif_picture(old), _heif_picture(new)
        if not new_picture or not old_picture.endswith(new_picture):
            return (f"the coded picture differs: sips {len(old_picture)} "
                    f"bytes of mdat, ours {len(new_picture)}")
        extra = len(old_picture) - len(new_picture)
        if b"Exif" not in old_picture[:extra]:
            return (f"sips' extra {extra} bytes of mdat are not an Exif item")
        return None

    old_body = _jpeg_without_metadata(old)
    new_body = _jpeg_without_metadata(new)
    if old_body and old_body == new_body:
        return None
    if _jpeg_is_progressive(Path(source).read_bytes()):
        if not (_jpeg_is_progressive(old) and not _jpeg_is_progressive(new)):
            return ("a progressive source, but not the pinned divergence: "
                    f"sips progressive={_jpeg_is_progressive(old)}, ours "
                    f"progressive={_jpeg_is_progressive(new)}")
        # The scans are incomparable and that is the whole of what is
        # tolerated. The quantization tables are not: DQT survives the
        # scan-structure difference -- measured identical between `sips`'
        # progressive output and our baseline one at the same quality on all
        # four progressive coverage images -- and it moves with both the
        # quality and the dimensions, so both are still caught here rather
        # than swallowed by the word "progressive". The corpus vacuity guard
        # goes through this branch, which is how the hole was found.
        #
        # A frame-header check stood here too and came out again: every
        # dimension difference I could construct already moves DQT (400x300
        # against 200x150 at quality 90 gives 138 bytes of tables either way
        # and different contents), so nothing reached it. A mutation removing
        # it left every gate green, which is what a check that is not there
        # looks like.
        if _jpeg_tables(old) != _jpeg_tables(new):
            return ("a progressive source, and the quantization tables "
                    f"differ as well: sips {len(_jpeg_tables(old))} bytes, "
                    f"ours {len(_jpeg_tables(new))} -- the scan structure "
                    f"does not account for that")
        return None
    if _jpeg_scan(old) != _jpeg_scan(new):
        return (f"the entropy-coded scan differs: sips {len(_jpeg_scan(old))} "
                f"bytes, ours {len(_jpeg_scan(new))}, and the source is not "
                f"progressive")
    return (f"the scans agree and something outside the APPn segments does "
            f"not: sips markers {[hex(m) for m, _ in _jpeg_segments(old)]}, "
            f"ours {[hex(m) for m, _ in _jpeg_segments(new)]}")


def test_the_jpeg_accounting_separates_metadata_from_picture(tmp_path,
                                                             photo_fixture):
    """Both directions of the rule the corpus gate leans on, at tier 1.

    It must ACCEPT a pair differing only in an APPn segment -- that is the
    divergence being measured -- and REJECT one whose coding parameters
    differ, which is what an earlier version did not: it compared the scan
    alone, so a changed quantization table with the same entropy data would
    have read as agreement.
    """
    src = photo_fixture(tmp_path / "s.png", 400, 300)
    ninety = tmp_path / "q90.jpg"
    sixty = tmp_path / "q60.jpg"
    imaging.resize_and_encode(src, 400, 300, "jpeg", 90, ninety, resize=False)
    imaging.resize_and_encode(src, 400, 300, "jpeg", 60, sixty, resize=False)

    assert _describe_encode(src, "jpeg", ninety, sixty) is not None, (
        "two different qualities were accounted for as agreement")

    # The same file with its APP0 removed: metadata gone, picture untouched.
    stripped = tmp_path / "no-app0.jpg"
    data = ninety.read_bytes()
    stripped.write_bytes(data[:2] + b"".join(
        payload for marker, payload in _jpeg_segments(data) if marker != 0xE0))
    assert stripped.read_bytes() != data, "the APP0 removal did nothing"
    assert _describe_encode(src, "jpeg", ninety, stripped) is None, (
        "a difference confined to an APPn segment was reported as a picture "
        "difference")


def test_the_progressive_accounting_tolerates_only_the_scan(tmp_path,
                                                            photo_fixture):
    """Both halves of the branch that excuses a progressive source.

    It must ACCOUNT for the scan-structure divergence and nothing else, so
    both directions are here at tier 1 rather than only in the corpus guard:
    the same shape at the same quality is excused, and a different shape is
    not. DQT is what catches the second, because the tables move with the
    dimensions as well as the quality -- 400x300 against 200x150 at quality
    90 gives 138 bytes of tables either way and different contents.
    """
    base = photo_fixture(tmp_path / "s.png", 400, 300)
    source = _write_progressive_jpeg(base, tmp_path / "p.jpg")
    old_out = tmp_path / "sips.jpg"
    _sips_encode_reference(source, "jpeg", 90, old_out)

    same = tmp_path / "same.jpg"
    imaging.resize_and_encode(source, 400, 300, "jpeg", 90, same, resize=False)
    assert _describe_encode(source, "jpeg", old_out, same) is None, (
        "the scan-structure divergence is what this branch exists to excuse")

    smaller = tmp_path / "smaller.jpg"
    imaging.resize_and_encode(source, 200, 150, "jpeg", 90, smaller,
                              resize=True)
    result = _describe_encode(source, "jpeg", old_out, smaller)
    assert result is not None, (
        "a 200x150 encode was accounted for as a 400x300 one because the "
        "source happened to be progressive")
    assert "quantization tables" in result, result


@pytest.mark.corpus
def test_encode_matches_sips_over_the_sample(corpus_sample, tmp_path):
    """The three lossy formats over real photographs, at their defaults.

    Whole-file identity is NOT the outcome here and the docstring at the top
    of this file says why: 21 of 27 carry EXIF that `sips` copies forward.
    What must hold on every file is that the coded picture is the same and
    the difference is accounted for -- metadata for HEIF, the progressive
    scan for the four progressive JPEG sources.

    Each output is removed as soon as it has been compared: the largest
    sample image is 9072x12096, and six encodes of it standing at once is
    over a hundred megabytes for no reason.
    """
    failures = []
    compared = 0
    for source in sorted(Path(corpus_sample).iterdir()):
        measured = imaging.probe(source)
        if measured is None:
            continue
        width, height, _ = measured
        for fmt, quality in AT_DEFAULTS:
            room = tmp_path / f"{fmt}-{source.stem}"
            room.mkdir(parents=True, exist_ok=True)
            old_out = room / f"sips{_suffix(fmt)}"
            new_out = room / f"cg{_suffix(fmt)}"
            try:
                _sips_encode_reference(source, fmt, quality, old_out)
                imaging.resize_and_encode(source, width, height, fmt, quality,
                                          new_out, resize=False)
                compared += 1
                result = _describe_encode(source, fmt, old_out, new_out)
            finally:
                shutil.rmtree(room, ignore_errors=True)
            if result is not None:
                failures.append(f"{source.name} [{fmt} {quality}]: {result}")
    assert compared, "nothing was compared"
    assert not failures, "\n".join(failures)


@pytest.mark.corpus
def test_the_corpus_comparison_is_not_vacuous(corpus_sample, tmp_path):
    """Guards the gate above over the real files, where the outputs are
    hundreds of times larger than a fixture's and an accounting rule that
    accepted everything would look exactly like agreement.

    Both branches, because they are two different rules and only one of them
    used to be guarded. `_describe_encode` takes no `quality` -- an earlier
    version did and never read it, which made the call above LOOK like it
    was checking the pair against a quality it was not.
    """
    source = next(path for path in sorted(Path(corpus_sample).iterdir())
                  if imaging.probe(path) is not None)
    width, height, _ = imaging.probe(source)
    for fmt, suffix in (("heic", ".heic"), ("jpeg", ".jpg")):
        old_out = tmp_path / f"sips{suffix}"
        new_out = tmp_path / f"cg{suffix}"
        _sips_encode_reference(source, fmt, 80, old_out)
        imaging.resize_and_encode(source, width, height, fmt, 40, new_out,
                                  resize=False)
        assert _describe_encode(source, fmt, old_out, new_out) is not None, (
            f"{source.name}: {fmt} 40 was accounted for as {fmt} 80")
