"""Which device and axis an image belongs to. Integer comparisons only.

The thresholds here are NOT where the legacy script's were. `get_aspect_ratio`
truncated to two decimals with `bc scale=2` and the result was compared against
bc's full-precision 2/3, so `0.66 >= 0.6666...` was false -- the real phone
boundary in the shipped script is 0.67, not 0.66, and the desktop boundary is
1.61, not 1.60. The comparisons below put both where legacy/notes.org always
said they were. See spec section 4.1; test_classify.py names the two cases.
"""

from . import sizes

CROP = "crop"


def classify(width: int, height: int, device: str):
    """Return a sizes.Target, or CROP if the image should be cut into thirds."""
    if device == sizes.DESKTOP:
        if width <= height:
            return CROP
        if width * 10 <= height * 16:
            return sizes.DESKTOP_BY_WIDTH
        return sizes.DESKTOP_BY_HEIGHT

    if device == sizes.PHONE:
        if width >= height:
            return CROP
        if width * 3 >= height * 2:
            return sizes.PHONE_BY_HEIGHT
        return sizes.PHONE_BY_WIDTH

    raise ValueError(f"unknown device: {device!r}")
