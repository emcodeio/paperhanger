"""Fixtures measured in pixels: a minimal PNG writer and reader, stdlib only.

Writing and reading, which is why this is no longer called `pngwriter`.
Writing was the first of them and the file kept the name through the rest.

  * `write_png` exists so boundary tests can use images that differ by one
    pixel. Committing a file per boundary would be absurd; generating them
    costs nothing.
  * `write_marked_png` puts a solid block at a known rectangle, so a test can
    say which REGION it expects rather than only which size.
  * `write_grey_png`, `write_png16`, `write_rgba_png`,
    `write_grey_alpha_png` and `write_indexed_png` emit the shapes the
    first two cannot: colour types 0, 6, 4 and 3, and bit depth 16. Each
    exists because the imaging layer
    builds a different destination bitmap for it, and hands back a wrong
    file -- black, flattened, or NULL -- when it is not told them apart.
    See each writer's docstring for which.
  * `write_interlaced_png` emits `write_png`'s pixels in Adam7 order, the one
    container property the differential harness must decode through rather
    than report -- one corpus source is interlaced and its pixels match ours
    exactly.
  * `read_ihdr` reads a header back, which is where several of them differ:
    a greyscale source that returns as colour type 2 has been converted,
    and no comparison of RGB values would say so.
  * `read_png_grey` is `read_png_rgb` for a monochrome result.
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


def _png_bytes(width: int, height: int, rows: bytes,
               depth: int = 8, colour_type: int = 2) -> bytes:
    """Wrap already-filtered scanlines as a PNG.

    `depth` and `colour_type` go straight into IHDR and default to the 8-bit
    truecolour pair the first three writers here emit. They are parameters
    because CoreGraphics chooses the shape of its destination bitmap from the
    DECODED colour space, and a writer that can only produce colour type 2
    cannot express the two inputs where that choice goes wrong -- see
    `write_grey_png` and `write_png16`.
    """
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR",
                 struct.pack(">IIBBBBB", width, height, depth, colour_type,
                             0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(rows, 6))
        + _chunk(b"IEND", b"")
    )


def _rgb_rows(width: int, height: int, colour, noise: bool) -> list:
    """The pixel grid, one `bytes` of RGB triples per row.

    Separate from `write_png` so `write_interlaced_png` can lay out the SAME
    pixels in Adam7 order. The pair is only worth having if the two files are
    provably the same image in two containers, and sharing the generator is
    what makes that provable rather than asserted.
    """
    red, green, blue = colour
    rows = []
    seed = 0x9E3779B9
    for y in range(height):
        if not noise:
            rows.append(bytes((red, green, blue)) * width)
            continue
        row = bytearray()
        for x in range(width):
            # xorshift-ish mix: deterministic, and varied enough that an
            # encoder has real high-frequency detail to work on.
            seed ^= (x * 0x85EBCA6B + y * 0xC2B2AE35) & 0xFFFFFFFF
            seed = (seed * 0x27D4EB2F + 0x165667B1) & 0xFFFFFFFF
            row.append((red + (seed >> 8)) & 0xFF)
            row.append((green + (seed >> 16)) & 0xFF)
            row.append((blue + (seed >> 24)) & 0xFF)
        rows.append(bytes(row))
    return rows


def write_png(path, width: int, height: int, colour=(120, 140, 110), noise=False) -> Path:
    """Write an 8-bit RGB PNG of exactly width x height pixels."""
    if width < 1 or height < 1:
        raise ValueError("width and height must be >= 1")

    rows = bytearray()
    for row in _rgb_rows(width, height, colour, noise):
        rows.append(0)  # filter type 0 (None) for this scanline
        rows.extend(row)

    path = Path(path)
    path.write_bytes(_png_bytes(width, height, bytes(rows)))
    return path


# Adam7, as (x origin, y origin, x step, y step) per pass. RFC 2083 section 2.6.
ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
         (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


def write_interlaced_png(path, width: int, height: int,
                         colour=(120, 140, 110), noise=False) -> Path:
    """The SAME pixels as `write_png`, laid out in Adam7 interlaced order.

    One corpus source is interlaced, `sips` preserves that and CoreGraphics
    does not, and the two files' pixels are identical -- it is one of the ten
    that differ in `IDAT` while agreeing on every pixel. So the differential
    harness has to decode Adam7 and treat interlacing as a container property
    rather than a difference, and this is the only input in the suite that
    reaches that code. Without it the seven-pass decoder in
    `tests/differential.py` would go into six later gates untested.

    Takes the same arguments as `write_png` and shares its pixel generator,
    so the pair differs in nothing but the layout.
    """
    if width < 1 or height < 1:
        raise ValueError("width and height must be >= 1")

    grid = _rgb_rows(width, height, colour, noise)
    stream = bytearray()
    for x_origin, y_origin, x_step, y_step in ADAM7:
        columns = range(x_origin, width, x_step)
        if not columns:
            continue          # this pass holds no pixels at this width
        for y in range(y_origin, height, y_step):
            stream.append(0)  # filter type 0 (None) for this scanline
            row = grid[y]
            for x in columns:
                stream.extend(row[x * 3:x * 3 + 3])

    path = Path(path)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 1))
        + _chunk(b"IDAT", zlib.compress(bytes(stream), 6))
        + _chunk(b"IEND", b"")
    )
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


def write_grey_png(path, width: int, height: int, top=0, bottom=255) -> Path:
    """An 8-bit GREYSCALE PNG: IHDR colour type 0, one channel per pixel.

    Nothing else in `tests/` can produce this input, and it is the one that
    breaks. ImageIO decodes a colour-type-0 PNG to a Monochrome colour space,
    and a destination bitmap built the obvious way -- the source's colour
    space paired with kCGImageAlphaNoneSkipLast -- then renders the entire
    frame black at exit 0, with no NULL and no error. Measured on a real
    corpus photo: 4,665,600 pixels, all zero. Ten of the 894 wallpapers are
    greyscale, so this is live rather than latent.

    The value runs linearly from `top` at row 0 to `bottom` at the last row,
    which makes the pixel at (0, y) say which SOURCE ROW it came from. That
    is what separates a correct crop from a centred one: both come back at
    the right dimensions, and only the contents disagree. Pass top == bottom
    for a flat grey, which is what a test about colour MODELS wants, since a
    gradient that came back black and a gradient that came back shifted are
    different failures.

    Above 256 rows the row-to-value map stops being one to one -- 8 bits hold
    256 values and no more -- so a test that needs to identify an exact row
    keeps the image shorter than that, or reads a band rather than a row.
    """
    if width < 1 or height < 1:
        raise ValueError("width and height must be >= 1")
    for name, value in (("top", top), ("bottom", bottom)):
        if not 0 <= value <= 255:
            raise ValueError(f"{name} must be within 0-255, got {value}")

    span = height - 1
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter type 0 (None) for this scanline
        value = top if span == 0 else round(top + (bottom - top) * y / span)
        rows.extend(bytes((value,)) * width)

    path = Path(path)
    path.write_bytes(_png_bytes(width, height, bytes(rows),
                                depth=8, colour_type=0))
    return path


def write_png16(path, width: int, height: int) -> Path:
    """A 16-bit RGB PNG: IHDR colour type 2, bit depth 16.

    The depth `sips` silently throws away. Its pad path downconverts to
    8-bit, which is the eighth defect in the design's list and the one this
    project introduced rather than inherited; measured here, its plain
    resample downconverts too. CoreGraphics keeps 16 bits, so this fixture is
    the shape behind the one pinned exception in the crop gate.

    Every component is chosen so that its low byte differs from its high
    byte. A downconvert to 8 bits and back is then visible in the numbers: an
    8-bit round trip can only produce values of the form 0xVV * 257.
    """
    if width < 1 or height < 1:
        raise ValueError("width and height must be >= 1")

    x_span, y_span = max(width - 1, 1), max(height - 1, 1)
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter type 0 (None)
        for x in range(width):
            red = (x * 65535) // x_span
            green = (y * 65535) // y_span
            blue = 65535 - ((x + y) * 65535) // (x_span + y_span)
            rows.extend(struct.pack(">HHH", red, green, blue))

    path = Path(path)
    path.write_bytes(_png_bytes(width, height, bytes(rows),
                                depth=16, colour_type=2))
    return path


def write_rgba_png(path, width: int, height: int,
                   colour=(120, 140, 110), alpha=128) -> Path:
    """An 8-bit RGBA PNG: IHDR colour type 6, four channels per pixel.

    Twelve of the corpus's 47 PNGs are this shape. A destination bitmap
    built with kCGImageAlphaNoneSkipLast composites them onto its own black
    ground and writes back opaque colour type 2 -- structurally a different
    file, and not one a pixel comparison of RGB channels would flag, since
    eight of the twelve are opaque enough to agree anyway.

    `alpha` is partial by default because a fully opaque RGBA source cannot
    distinguish a path that preserved the channel from one that flattened
    it: both come back with the same colours.
    """
    if width < 1 or height < 1:
        raise ValueError("width and height must be >= 1")
    if not 0 <= alpha <= 255:
        raise ValueError(f"alpha must be within 0-255, got {alpha}")

    pixel = bytes(tuple(colour) + (alpha,))
    rows = bytearray()
    for _ in range(height):
        rows.append(0)  # filter type 0 (None)
        rows.extend(pixel * width)

    path = Path(path)
    path.write_bytes(_png_bytes(width, height, bytes(rows),
                                depth=8, colour_type=6))
    return path


def write_grey_alpha_png(path, width: int, height: int,
                         top=0, bottom=255, alpha=128) -> Path:
    """A greyscale-with-alpha PNG: IHDR colour type 4, two channels.

    No corpus file is this shape, and that is the argument FOR having it
    rather than against. A monochrome bitmap context built with
    kCGImageAlphaNone accepts a grey+alpha source and composites it onto
    black -- measured, values 0, 6, 12, 18 at alpha 128 coming back
    0, 3, 6, 9 -- so without this writer the branch that has to get that
    right could not be reached by any test, and an untestable branch that
    silently composites is the exact shape the binding layer exists to
    prevent.

    `alpha` is partial by default for the same reason as in
    `write_rgba_png`: an opaque source cannot tell a preserved channel from
    a flattened one.
    """
    if width < 1 or height < 1:
        raise ValueError("width and height must be >= 1")
    for name, value in (("top", top), ("bottom", bottom), ("alpha", alpha)):
        if not 0 <= value <= 255:
            raise ValueError(f"{name} must be within 0-255, got {value}")

    span = height - 1
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter type 0 (None)
        value = top if span == 0 else round(top + (bottom - top) * y / span)
        rows.extend(bytes((value, alpha)) * width)

    path = Path(path)
    path.write_bytes(_png_bytes(width, height, bytes(rows),
                                depth=8, colour_type=4))
    return path


def write_indexed_png(path, width: int, height: int, colours=None) -> Path:
    """A palettised PNG: IHDR colour type 3, one index per pixel plus PLTE.

    ImageIO decodes this to an INDEXED colour space rather than expanding it
    to RGB, and `CGBitmapContextCreate` returns NULL for an indexed space
    under every alpha setting there is. No corpus file decodes this way, but
    a generated one does, and it has already bitten two instruments that
    were measuring something else. It is here so the binding layer's refusal
    is tested rather than assumed.
    """
    if width < 1 or height < 1:
        raise ValueError("width and height must be >= 1")
    colours = colours or [(200, 30, 40), (30, 200, 40), (30, 40, 200),
                          (240, 240, 40)]
    if not 1 <= len(colours) <= 256:
        raise ValueError(f"a palette holds 1-256 colours, got {len(colours)}")

    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter type 0 (None)
        rows.extend(bytes((x + y) % len(colours) for x in range(width)))

    palette = b"".join(bytes(colour) for colour in colours)
    path = Path(path)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0))
        + _chunk(b"PLTE", palette)
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + _chunk(b"IEND", b"")
    )
    return path


def read_ihdr(path):
    """(width, height, bit depth, colour type, interlace) from the header.

    Read from the bytes rather than asked of a decoder, because what these
    two new writers have to get right IS the header: a file that decodes to
    the right pixels through a 24-bit path proves nothing about whether it is
    greyscale or 16-bit on disk, and that is the whole point of both.
    """
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    if data[12:16] != b"IHDR":
        raise ValueError(f"{path} does not open with IHDR")
    width, height, depth, colour, _, _, interlace = struct.unpack(
        ">IIBBBBB", data[16:29])
    return width, height, depth, colour, interlace


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


def read_png_grey(path):
    """Decode an 8-bit GREYSCALE PNG to (width, height, rows of ints).

    The counterpart to `read_png_rgb` for what a monochrome source produces.
    It refuses anything but colour type 0 rather than converting, because
    that is the assertion worth making: a greyscale source that came back as
    colour type 2 has been through a conversion nobody asked for, and
    silently accepting it is how the difference goes unnoticed.
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
            if (depth, colour, interlace) != (8, 0, 0):
                raise ValueError(
                    f"{path}: expected 8-bit greyscale non-interlaced, got "
                    f"depth {depth}, colour type {colour}, interlace "
                    f"{interlace}"
                )
        elif kind == b"IDAT":
            compressed += payload
        elif kind == b"IEND":
            break
        offset += 12 + length

    if width is None:
        raise ValueError(f"{path} has no IHDR")

    raw = zlib.decompress(bytes(compressed))
    expected = height * (width + 1)
    if len(raw) != expected:
        raise ValueError(f"{path}: got {len(raw)} bytes of pixel data, want {expected}")

    rows = []
    previous = bytes(width)
    position = 0
    for _ in range(height):
        filter_type = raw[position]
        line = bytearray(raw[position + 1:position + 1 + width])
        position += 1 + width
        _unfilter(filter_type, line, previous, 1)
        rows.append(list(line))
        previous = bytes(line)
    return width, height, rows
