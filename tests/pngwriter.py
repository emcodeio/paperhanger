"""Minimal PNG writer, stdlib only.

Exists so boundary tests can use images that differ by one pixel. Committing a
file per boundary would be absurd; generating them costs nothing.
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

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + _chunk(b"IEND", b"")
    )
    path = Path(path)
    path.write_bytes(data)
    return path
