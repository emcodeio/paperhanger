"""The five bands, and the output dimensions each one produces.

Two rules shape this module, both from spec section 5:

  * No image is ever enlarged except by the ML model. Nothing here upsamples.
  * No image is enlarged by less than 1.5x -- which is the floor, because
    ideal/floor is exactly 1.5 on all four targets. Band NATIVE is that case,
    and it resamples not at all.

Both output axes are computed here rather than derived by sips, because
--resampleWidth/--resampleHeight round the derived axis inconsistently and the
filename records the size. The plan defines the file; sips is told both numbers.
"""

from . import sizes

DOWNSCALE = 1        # d >= ideal            -> reduce to ideal
NATIVE = 2           # floor <= d < ideal    -> encode only, untouched
UPSCALE_REDUCE = 3   # d < floor, d*4 >= ideal -> 4x then reduce to ideal
UPSCALE_ONLY = 4     # d < floor, floor <= d*4 < ideal -> 4x, no resize
REJECT = 5           # d*4 < floor           -> too small for this device

UPSCALING = (UPSCALE_REDUCE, UPSCALE_ONLY)
BELOW_TARGET = (NATIVE, UPSCALE_ONLY)
RESIZING = (DOWNSCALE, UPSCALE_REDUCE)

NAMES = {
    DOWNSCALE: "downscale",
    NATIVE: "native",
    UPSCALE_REDUCE: "upscale+reduce",
    UPSCALE_ONLY: "upscale",
    REJECT: "reject",
}


def band_for(governing: int, target: sizes.Target) -> int:
    """Which band a governing dimension falls in. Conditions are disjoint."""
    if governing >= target.ideal:
        return DOWNSCALE
    if governing >= target.floor:
        return NATIVE
    quadrupled = governing * sizes.UPSCALE_FACTOR
    if quadrupled >= target.ideal:
        return UPSCALE_REDUCE
    if quadrupled >= target.floor:
        return UPSCALE_ONLY
    return REJECT


def output_size(width: int, height: int, target: sizes.Target, band: int):
    """Both output axes, exactly as the file will be written."""
    if band == NATIVE:
        return (width, height)
    if band == UPSCALE_ONLY:
        factor = sizes.UPSCALE_FACTOR
        return (width * factor, height * factor)
    if band in RESIZING:
        if target.axis == sizes.WIDTH:
            return (target.ideal, round(height * target.ideal / width))
        return (round(width * target.ideal / height), target.ideal)
    raise ValueError(f"no output size for band {band}")


def net_factor(governing: int, target: sizes.Target, band: int):
    """How much enlargement survives into the finished file, or None.

    Net rather than the model's own 4x: a photo enlarged 4x and then reduced
    carries less invented detail into the result than one left at 4x.
    """
    if band in (DOWNSCALE, NATIVE):
        return None
    if band == UPSCALE_ONLY:
        return float(sizes.UPSCALE_FACTOR)
    return target.ideal / governing


def factor_token(factor) -> str:
    """The filename token: 'native', '4x', '1.6x'. Never '4.0x'."""
    if factor is None:
        return "native"
    text = f"{factor:.1f}".rstrip("0").rstrip(".")
    return f"{text}x"
