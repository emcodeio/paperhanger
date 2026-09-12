"""Output formats, their extensions, and their quality defaults.

Quality scales are not comparable across encoders, so each default is the knee
of its own curve, measured on a 7680x5120 high-frequency photograph:

  heic 80  -- 85 produces a BYTE-IDENTICAL file (Apple's encoder quantizes),
              and 90 nearly doubles the size for the next step up.
  jpeg 90  -- 85->90 costs 5% more size for better fidelity; 90->95 costs 10%
              more for less return.
  avif 85  -- lands in heic 80's quality neighbourhood at 19% smaller.
  png      -- lossless; there is no quality setting, and asking for one is an
              error rather than a silent no-op.
"""

EXTENSIONS = {"heic": "heic", "jpeg": "jpg", "avif": "avif", "png": "png"}
DEFAULT_QUALITY = {"heic": 80, "jpeg": 90, "avif": 85, "png": None}
FORMATS = tuple(EXTENSIONS)
LOSSLESS = ("png",)


def extension(fmt: str) -> str:
    try:
        return EXTENSIONS[fmt]
    except KeyError:
        # `from None` because the CLI prints this message to a user. Chained,
        # it arrives as "During handling of the above exception..." with a
        # bare KeyError above it -- an implementation detail of this lookup,
        # in the place a person is looking for what to type instead.
        raise ValueError(
            f"unknown format: {fmt!r}; expected one of {', '.join(FORMATS)}"
        ) from None


def quality_for(fmt: str, override=None):
    """The quality value to encode with, or None for a lossless format."""
    if fmt not in EXTENSIONS:
        raise ValueError(f"unknown format: {fmt!r}; expected one of {', '.join(FORMATS)}")
    if fmt in LOSSLESS:
        if override is not None:
            raise ValueError(f"{fmt} is lossless; --quality does not apply")
        return None
    if override is None:
        return DEFAULT_QUALITY[fmt]
    if not 0 <= override <= 100:
        raise ValueError(f"quality must be 0-100, got {override}")
    return override
