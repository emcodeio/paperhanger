"""Fixtures measured in pixels: a minimal PNG writer and reader, stdlib only.

Three things, which is why this is no longer called `pngwriter`. Writing was
the first of them and the file kept the name through the other two.

  * `write_png` exists so boundary tests can use images that differ by one
    pixel. Committing a file per boundary would be absurd; generating them
    costs nothing.
  * `write_marked_png` puts a solid block at a known rectangle, so a test can
    say which REGION it expects rather than only which size.
  * `read_png_rgb` reads the result back. Checking dimensions alone cannot
    tell a correct crop from one with its arguments transposed, and sips takes
    both of its crop arguments backwards from the usual convention (-c is
    HEIGHT WIDTH, --cropOffset is Y X), so that is the mistake most worth
    catching -- and the only way to catch it is to look at the pixels.
"""

import struct
import zlib
from pathlib import Path


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _png_bytes(width: int, height: int, rows: bytes) -> bytes:
    """Wrap already-filtered scanlines as an 8-bit RGB PNG."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(rows, 6))
        + _chunk(b"IEND", b"")
    )


def write_png(path, width: int, height: int, colour=(120, 140, 110), noise=False) -> Path:
    """Write an 8-bit RGB PNG of exactly width x height pixels."""
    if width < 1 or height < 1:
        raise ValueError("width and height must be >= 1")

    red, green, blue = colour
    rows = bytearray()
    seed = 0x9E3779B9
    for y in range(height):
        rows.append(0)  # filter type 0 (None) for this scanline
        if not noise:
            rows.extend(bytes((red, green, blue)) * width)
            continue
        for x in range(width):
            # xorshift-ish mix: deterministic, and varied enough that an
            # encoder has real high-frequency detail to work on.
            seed ^= (x * 0x85EBCA6B + y * 0xC2B2AE35) & 0xFFFFFFFF
            seed = (seed * 0x27D4EB2F + 0x165667B1) & 0xFFFFFFFF
            rows.append((red + (seed >> 8)) & 0xFF)
            rows.append((green + (seed >> 16)) & 0xFF)
            rows.append((blue + (seed >> 24)) & 0xFF)

    path = Path(path)
    path.write_bytes(_png_bytes(width, height, bytes(rows)))
    return path


def write_marked_png(path, width: int, height: int, rect,
                     base=(10, 10, 10), marker=(240, 30, 200)) -> Path:
    """A flat `base` image carrying a solid `marker` block at `rect`.

    `rect` is anything with x/y/width/height, so a geometry.Rect drops in.
    Both colours are deliberately far apart, so a crop that lands even one
    pixel off target reads back a colour that is not the marker.
    """
    if not (0 <= rect.x and 0 <= rect.y
            and rect.x + rect.width <= width
            and rect.y + rect.height <= height):
        raise ValueError(f"{rect} does not fit inside {width}x{height}")

    base_row = bytes(base) * width
    marked_row = (bytes(base) * rect.x
                  + bytes(marker) * rect.width
                  + bytes(base) * (width - rect.x - rect.width))
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter type 0 (None)
        rows.extend(marked_row if rect.y <= y < rect.y + rect.height else base_row)

    path = Path(path)
    path.write_bytes(_png_bytes(width, height, bytes(rows)))
    return path


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(filter_type: int, line: bytearray, previous: bytes, bpp: int) -> None:
    """Undo one scanline's filter, in place. See RFC 2083 section 6."""
    if filter_type == 0:
        return
    if filter_type not in (1, 2, 3, 4):
        raise ValueError(f"unknown PNG filter type {filter_type}")
    for i in range(len(line)):
        left = line[i - bpp] if i >= bpp else 0
        up = previous[i]
        up_left = previous[i - bpp] if i >= bpp else 0
        if filter_type == 1:
            line[i] = (line[i] + left) & 0xFF
        elif filter_type == 2:
            line[i] = (line[i] + up) & 0xFF
        elif filter_type == 3:
            line[i] = (line[i] + (left + up) // 2) & 0xFF
        else:
            line[i] = (line[i] + _paeth(left, up, up_left)) & 0xFF


def read_png_rgb(path):
    """Decode an 8-bit RGB PNG to (width, height, rows of (r, g, b) tuples).

    Only the one variant sips writes for these fixtures: 8-bit truecolour, no
    interlacing. Anything else raises rather than returning wrong pixels --
    sips does use adaptive filtering, so the unfilter above earns its keep.
    """
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")

    width = height = None
    compressed = bytearray()
    offset = 8
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(
                ">IIBBBBB", payload)
            if (depth, colour, interlace) != (8, 2, 0):
                raise ValueError(
                    f"{path}: expected 8-bit RGB non-interlaced, got depth "
                    f"{depth}, colour type {colour}, interlace {interlace}"
                )
        elif kind == b"IDAT":
            compressed += payload
        elif kind == b"IEND":
            break
        offset += 12 + length

    if width is None:
        raise ValueError(f"{path} has no IHDR")

    raw = zlib.decompress(bytes(compressed))
    stride = width * 3
    expected = height * (stride + 1)
    if len(raw) != expected:
        raise ValueError(f"{path}: got {len(raw)} bytes of pixel data, want {expected}")

    rows = []
    previous = bytes(stride)
    position = 0
    for _ in range(height):
        filter_type = raw[position]
        line = bytearray(raw[position + 1:position + 1 + stride])
        position += 1 + stride
        _unfilter(filter_type, line, previous, 3)
        rows.append([tuple(line[i:i + 3]) for i in range(0, stride, 3)])
        previous = bytes(line)
    return width, height, rows
