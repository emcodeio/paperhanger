"""Compare a `sips` operation against its CoreGraphics replacement.

`compare(old_fn, new_fn, source, tmp_path)` runs both implementations over the
same source and returns `None` when they agree, or a sentence saying exactly
how they do not. Six later gates are built on it, so what it compares is a
decision rather than a detail. Global Constraint 10 records the measurement
behind that decision; the short version, over all 894 corpus images at a
common shape -- 859 identical, 10 differing in `IDAT` while every pixel
agrees, 25 differing in pixels:

  * **Whole files are out, for PNG.** 723 of 894 differ, because `sips`
    synthesises PNG ancillary chunks out of a source's EXIF and XMP that
    ImageIO does not emit. Worse, whole-file identity is not even a stable
    property: both tools disagree with THEMSELVES across runs, because an ICC
    profile header carries a creation `dateTime` stamped to the current
    second. A gate on whole PNG files would be partly measuring a clock.
  * **`IDAT` is out as the bar.** It fires on ten files whose pixels are
    identical: nine where `sips` writes colour type 6 against our type 2, and
    one Adam7 interlaced source `sips` preserves and CoreGraphics does not.
  * **Decoded pixels are the bar** -- with the trap that an RGB-only
    comparison hides an alpha drop, which `kCGImageAlphaNoneSkipLast` does to
    all 12 alpha-bearing corpus PNGs while leaving the RGB channels intact.
    So the colour type, the channel count and the bit depth are asserted
    alongside the pixels. A gate that passed while the alpha went would be
    worse than no gate.

Lossy output keeps the whole-file comparison, and Task 5 measured what that
rests on rather than inheriting it. It holds for everything this harness is
handed at tier 1: an untagged fixture and an ICC-tagged one are byte-identical
to `sips` at jpeg 80 and 90, heic 80, 85 and 90, and avif 85, whatever the
profile. It does NOT hold over real photographs, and the reason is not the
ICC clock that rules whole files out for PNG -- it is that `sips` copies a
source's EXIF into its output and `CGImageDestinationAddImage` writes only
the picture. 21 of the 27 coverage images differ for heic and avif on that
alone, with the coded picture identical underneath; four progressive JPEG
sources differ because `sips` inherits the progressive scan and ImageIO
writes baseline. So a caller comparing lossy output over the corpus has to
account for the container, which the retired encode differential did per
file; this harness stays the whole-file bar it was, and every fixture that
reaches it meets that bar.

Two things the harness deliberately does NOT treat as differences, because
the measurement says the pixels are what matter: interlacing, and anything
outside `IHDR`, `PLTE`, `tRNS` and `IDAT`.

Decoding is done here, in the standard library, and never through ImageIO.
A gate that decoded with the framework under test would agree with itself
about a bitmap it had built wrongly.

Usage notes for the tasks that call it:

  * `old_fn` and `new_fn` take `(source, out_path)` and write that path.
  * `suffix` names the output extension, `.png` by default. An encode gate
    passes the format it is testing -- `suffix=".heic"` -- both because
    `crop` refuses a name that is not `.png` and because the extension is
    what a later reader will believe.
  * The two implementations write into separate directories, so a staging
    file one of them leaves behind cannot be read as the other's output, and
    both directories are emptied on entry, so a previous call's output cannot
    be mistaken for this call's.
"""

import shutil
import struct
import zlib
from pathlib import Path
from typing import NamedTuple

from tests import pixels

# What the two sides are called in every message. The harness is symmetric;
# the names are not, because a caller passes the implementation being
# replaced first and the replacement second. They still read `sips` and
# `CoreGraphics` because that is the migration these words were measured
# against and the only caller left is this harness's own tests; the next
# migration to use it should rename them for its own pair.
OLD = "sips"
NEW = "CoreGraphics"

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Channel count per PNG colour type. Indexed is one channel on disk -- an
# index -- which is why the palette is compared alongside the pixels.
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
CHANNEL_NAMES = {
    0: ("grey",),
    2: ("red", "green", "blue"),
    3: ("index",),
    4: ("grey", "alpha"),
    6: ("red", "green", "blue", "alpha"),
}
COLOUR_NAMES = {0: "greyscale", 2: "truecolour", 3: "indexed",
                4: "greyscale with alpha", 6: "truecolour with alpha"}


class _Png(NamedTuple):
    """A PNG reduced to the parts a pixel comparison depends on."""
    width: int
    height: int
    depth: int
    colour_type: int
    interlace: int
    palette: bytes
    transparency: bytes
    raw: bytes            # the decompressed, still-filtered scanline stream

    @property
    def channels(self) -> int:
        return CHANNELS[self.colour_type]

    @property
    def sample_size(self) -> int:
        """Bytes per sample once unpacked: 2 at depth 16, otherwise 1."""
        return 2 if self.depth == 16 else 1


def compare(old_fn, new_fn, source, tmp_path, suffix=".png"):
    """Run both implementations over `source`; describe any difference.

    Returns `None` only when the two outputs agree on every pixel -- and, for
    PNG, on the colour type, the channel count and the bit depth that give
    those pixels their meaning. Anything else comes back as a sentence naming
    the file and the disagreement.

    Never returns `None` because nothing was written: an implementation that
    produces no file, or an empty one, is reported. A gate that reads silence
    as agreement is the failure this project keeps finding.
    """
    source = Path(source)
    old_out = _fresh(Path(tmp_path) / "_differential" / "old") / f"out{suffix}"
    new_out = _fresh(Path(tmp_path) / "_differential" / "new") / f"out{suffix}"

    old_fn(source, old_out)
    new_fn(source, new_out)

    nothing = _nothing_written(source, old_out, new_out)
    if nothing is not None:
        return nothing

    old, new = old_out.read_bytes(), new_out.read_bytes()
    old_format, new_format = _format_of(old), _format_of(new)
    if old_format != new_format:
        return (f"{source.name}: {OLD} wrote {old_format}, "
                f"{NEW} wrote {new_format}")
    if old_format == "PNG":
        return _compare_png(source, old, new)
    return _compare_bytes(source, old, new)


# ---------------------------------------------------------------------------
# Did either side actually write anything?
# ---------------------------------------------------------------------------


def _fresh(directory: Path) -> Path:
    """An empty directory. Emptied, not merely created.

    `compare` is called in a loop over a corpus sample with one `tmp_path`
    for the whole loop. Without this, an implementation that wrote nothing on
    the fourth file would be compared against its own output from the third,
    and the gate would pass.
    """
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    return directory


def _nothing_written(source: Path, old_out: Path, new_out: Path):
    """Report an output that is absent or empty. Both absent is still a report."""
    faults = []
    for label, path in ((OLD, old_out), (NEW, new_out)):
        if not path.exists():
            faults.append(f"{label} wrote no file at {path.name}")
        elif path.stat().st_size == 0:
            faults.append(f"{label} wrote an empty file")
    if not faults:
        return None
    return f"{source.name}: " + ", and ".join(faults)


def _format_of(data: bytes) -> str:
    """What the BYTES say the file is. The name on it is not evidence."""
    if data[:8] == PNG_SIGNATURE:
        return "PNG"
    if data[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if data[4:8] == b"ftyp":
        brand = data[8:12].decode("ascii", "replace")
        if brand in ("avif", "avis"):
            return "AVIF"
        if brand in ("heic", "heix", "hevc", "mif1", "msf1"):
            return "HEIF"
        return f"ISO media, brand {brand}"
    return "bytes in no format this harness recognises"


# ---------------------------------------------------------------------------
# Whole files, for everything that is not a PNG.
# ---------------------------------------------------------------------------


def _compare_bytes(source: Path, old: bytes, new: bytes):
    if old == new:
        return None
    if len(old) != len(new):
        return (f"{source.name}: {OLD} wrote {len(old)} bytes, "
                f"{NEW} wrote {len(new)} bytes")

    differing = sum(1 for a, b in zip(old, new) if a != b)
    offset = next(i for i, (a, b) in enumerate(zip(old, new)) if a != b)
    return (f"{source.name}: first difference at offset {offset} "
            f"({OLD} {old[offset]:#04x}, {NEW} {new[offset]:#04x}), "
            f"{differing} of {len(old)} bytes differ")


# ---------------------------------------------------------------------------
# Decoded pixels, for PNG.
# ---------------------------------------------------------------------------


def _compare_png(source: Path, old: bytes, new: bytes):
    parsed = {}
    for label, data in ((OLD, old), (NEW, new)):
        try:
            parsed[label] = _read_png(data)
        except ValueError as exc:
            parsed[label] = str(exc)

    unreadable = [f"{label} wrote a PNG this harness cannot read: {value}"
                  for label, value in parsed.items()
                  if isinstance(value, str)]
    if unreadable:
        return f"{source.name}: " + ", and ".join(unreadable)

    a, b = parsed[OLD], parsed[NEW]

    if (a.width, a.height) != (b.width, b.height):
        return (f"{source.name}: {OLD} wrote {a.width}x{a.height}, "
                f"{NEW} wrote {b.width}x{b.height}")

    if a.colour_type != b.colour_type:
        lost = ""
        if a.colour_type in (4, 6) and b.colour_type not in (4, 6):
            lost = f" -- the alpha channel is gone from {NEW}'s output"
        elif b.colour_type in (4, 6) and a.colour_type not in (4, 6):
            lost = f" -- {NEW} added an alpha channel {OLD} did not write"
        return (f"{source.name}: {OLD} wrote colour type {a.colour_type} "
                f"({COLOUR_NAMES[a.colour_type]}, {_channels(a)}), "
                f"{NEW} wrote colour type {b.colour_type} "
                f"({COLOUR_NAMES[b.colour_type]}, {_channels(b)})"
                f"{lost}")

    if a.depth != b.depth:
        return (f"{source.name}: {OLD} wrote {a.depth} bits per channel, "
                f"{NEW} wrote {b.depth}")

    # Only over the entries both palettes have. A palette longer than the
    # other's by entries beyond its end is a container difference, not an
    # image one: no index in the shorter file can reach them, so reporting it
    # would be the ancillary-chunk mistake in another costume. (An index that
    # overruns its own palette would be a malformed file, and this harness
    # does not validate that; nothing in the pipeline writes indexed PNG.)
    shared = min(len(a.palette), len(b.palette))
    if a.palette[:shared] != b.palette[:shared]:
        entry = _first_differing_index(a.palette[:shared], b.palette[:shared]) // 3
        return (f"{source.name}: the palettes differ at entry {entry} -- "
                f"{OLD} {tuple(a.palette[entry * 3:entry * 3 + 3])}, "
                f"{NEW} {tuple(b.palette[entry * 3:entry * 3 + 3])}")

    if a.transparency != b.transparency:
        return (f"{source.name}: the tRNS chunks differ -- {OLD} wrote "
                f"{len(a.transparency)} bytes, {NEW} wrote "
                f"{len(b.transparency)}")

    # Equal filtered scanline streams mean equal pixels ONLY when the two
    # files agree on how the stream is laid out, and the interlace flag is
    # what decides that. A 1x8 image writes the same 32 bytes either way, and
    # Adam7 reads them out as rows 0, 4, 2, 6, 1, 3, 5, 7 -- so without the
    # second conjunct this returns agreement on two genuinely different
    # images. It is a memcmp against a decode measured in seconds per frame,
    # and one-way: it proves agreement, never disagreement.
    if a.raw == b.raw and a.interlace == b.interlace:
        return None

    try:
        old_rows = _pixel_rows(a)
        new_rows = _pixel_rows(b)
    except ValueError as exc:
        return f"{source.name}: the pixel data would not decode: {exc}"

    return _describe_pixels(source, a, old_rows, new_rows)


def _channels(png: _Png) -> str:
    return f"{png.channels} channel" + ("" if png.channels == 1 else "s")


def _first_differing_index(a: bytes, b: bytes) -> int:
    return next(i for i, (x, y) in enumerate(zip(a, b)) if x != y)


def _read_png(data: bytes) -> _Png:
    """The header, palette, transparency and decompressed scanlines.

    Ancillary chunks are skipped on purpose: `sips` writes several that
    ImageIO does not, and Global Constraint 10 is the measurement saying so.
    """
    if data[:8] != PNG_SIGNATURE:
        raise ValueError("it does not open with the PNG signature")

    header = None
    palette = b""
    transparency = b""
    idat = bytearray()
    offset = 8
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        if len(payload) != length:
            raise ValueError(f"the {kind.decode('ascii', 'replace')} chunk "
                             f"is truncated")
        if kind == b"IHDR":
            header = _read_ihdr(payload)
        elif kind == b"PLTE":
            palette = payload
        elif kind == b"tRNS":
            transparency = payload
        elif kind == b"IDAT":
            idat += payload
        elif kind == b"IEND":
            break
        offset += 12 + length

    if header is None:
        raise ValueError("it has no IHDR")
    if not idat:
        raise ValueError("it has no IDAT")
    if header[3] == 3 and not palette:
        raise ValueError("it is indexed and has no PLTE")
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ValueError(f"the pixel data would not decompress: {exc}") from exc

    width, height, depth, colour_type, interlace = header
    return _Png(width, height, depth, colour_type, interlace,
                palette, transparency, raw)


def _read_ihdr(payload: bytes):
    if len(payload) != 13:
        raise ValueError(f"IHDR is {len(payload)} bytes, not 13")
    (width, height, depth, colour_type,
     compression, filtering, interlace) = struct.unpack(">IIBBBBB", payload)
    if width < 1 or height < 1:
        raise ValueError(f"IHDR says {width}x{height}")
    if colour_type not in CHANNELS:
        raise ValueError(f"colour type {colour_type} is not a PNG colour type")
    if depth not in (1, 2, 4, 8, 16):
        raise ValueError(f"bit depth {depth} is not a PNG bit depth")
    if compression != 0 or filtering != 0:
        raise ValueError(f"compression method {compression}, filter method "
                         f"{filtering}; PNG defines only 0 for both")
    if interlace not in (0, 1):
        raise ValueError(f"interlace method {interlace} is neither none nor "
                         f"Adam7")
    return width, height, depth, colour_type, interlace


def _pixel_rows(png: _Png) -> list:
    """The image as one `bytes` of unpacked samples per row.

    `sample_size` bytes per sample throughout, so a comparison is a `bytes`
    comparison and a differing index maps back to a pixel by arithmetic.
    Sub-byte depths are unpacked to a byte each, keeping their own values.
    """
    if png.interlace == 0:
        rows, consumed = _scanlines(png.raw, 0, png.width, png.height, png)
        if consumed != len(png.raw):
            raise ValueError(
                f"it carries {len(png.raw)} bytes of scanline data, and a "
                f"{png.width}x{png.height} image at {png.channels} channels "
                f"and {png.depth} bits needs {consumed}")
        return rows
    return _deinterlace(png)


def _scanlines(raw: bytes, offset: int, width: int, height: int, png: _Png):
    """Unfilter `height` scanlines of `width` pixels. Returns (rows, offset)."""
    if width == 0 or height == 0:
        return [], offset

    stride = (width * png.channels * png.depth + 7) // 8
    # Filtering works on whole bytes, and on the byte a whole pixel back --
    # one byte where a pixel is smaller than that.
    step = max(1, (png.channels * png.depth + 7) // 8)

    rows = []
    previous = bytes(stride)
    for _ in range(height):
        if offset + 1 + stride > len(raw):
            raise ValueError(f"the scanline data stops {offset + 1 + stride - len(raw)} "
                             f"bytes short")
        filter_type = raw[offset]
        line = bytearray(raw[offset + 1:offset + 1 + stride])
        offset += 1 + stride
        if filter_type == 2:
            # Up, by far the most common on a photograph, and the one case
            # worth taking out of the per-byte loop.
            line = bytearray((a + b) & 0xFF for a, b in zip(line, previous))
        elif filter_type != 0:
            # None needs nothing; Sub, Average and Paeth all depend on the
            # byte to their left and cannot leave the loop.
            pixels._unfilter(filter_type, line, previous, step)
        previous = bytes(line)
        rows.append(_unpack(bytes(line), width, png))
    return rows, offset


def _unpack(line: bytes, width: int, png: _Png) -> bytes:
    """One byte per sample below depth 8; at 8 and 16 the bytes already are."""
    if png.depth >= 8:
        return line
    count = width * png.channels
    mask = (1 << png.depth) - 1
    per_byte = 8 // png.depth
    out = bytearray(count)
    for i in range(count):
        byte = line[i // per_byte]
        shift = 8 - png.depth * (i % per_byte + 1)
        out[i] = (byte >> shift) & mask
    return bytes(out)


def _deinterlace(png: _Png) -> list:
    """Reassemble the seven Adam7 passes into ordinary rows.

    `sips` preserves an interlaced source and CoreGraphics does not, and the
    pixels are identical either way -- so this exists to make interlacing a
    non-difference rather than a reported one.
    """
    passes = [(range(x_origin, png.width, x_step),
               range(y_origin, png.height, y_step))
              for x_origin, y_origin, x_step, y_step in pixels.ADAM7]

    # Before allocating anything. The grid is sized from the HEADER, and a
    # header is the part of a malformed file that can claim any size it
    # likes; the scanline stream is the part that has to be there.
    required = sum(
        len(lines) * (1 + (len(columns) * png.channels * png.depth + 7) // 8)
        for columns, lines in passes if len(columns) and len(lines))
    if required != len(png.raw):
        raise ValueError(f"it carries {len(png.raw)} bytes of scanline data, "
                         f"and a {png.width}x{png.height} Adam7 image at "
                         f"{png.channels} channels and {png.depth} bits needs "
                         f"{required}")

    pixel = png.channels * png.sample_size
    grid = [bytearray(png.width * pixel) for _ in range(png.height)]
    offset = 0
    for columns, lines in passes:
        rows, offset = _scanlines(png.raw, offset, len(columns), len(lines), png)
        for row, y in zip(rows, lines):
            for index, x in enumerate(columns):
                grid[y][x * pixel:(x + 1) * pixel] = \
                    row[index * pixel:(index + 1) * pixel]
    if offset != len(png.raw):
        raise ValueError(f"{len(png.raw) - offset} bytes of scanline data are "
                         f"left over after the seven Adam7 passes")
    return [bytes(row) for row in grid]


def _describe_pixels(source: Path, png: _Png, old_rows: list, new_rows: list):
    """Where the pixels first differ, how many do, and by how much."""
    size = png.sample_size
    names = CHANNEL_NAMES[png.colour_type]
    first = None
    differing = 0
    largest = 0

    for y, (a, b) in enumerate(zip(old_rows, new_rows)):
        if a == b:
            continue
        for i in range(0, len(a), size):
            before = int.from_bytes(a[i:i + size], "big")
            after = int.from_bytes(b[i:i + size], "big")
            if before == after:
                continue
            differing += 1
            largest = max(largest, abs(before - after))
            if first is None:
                sample = i // size
                first = (sample // png.channels, y,
                         names[sample % png.channels], before, after)

    if first is None:
        # Equal rows and unequal streams: filtering or compression differ and
        # the pixels do not. Callers get the same answer as a byte-for-byte
        # match, which is the whole point of comparing pixels.
        return None

    x, y, channel, before, after = first
    total = png.width * png.height * png.channels
    return (f"{source.name}: pixels differ at ({x}, {y}), {channel} channel "
            f"-- {OLD} {before}, {NEW} {after}; {differing} of {total} "
            f"samples differ, largest difference {largest}")
