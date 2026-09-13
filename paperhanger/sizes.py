"""Target sizes and the constants the decision tree runs on. No I/O, no floats."""

from dataclasses import dataclass

DESKTOP = "desktop"
PHONE = "phone"
WIDTH = "width"
HEIGHT = "height"

UPSCALE_FACTOR = 4

# There is deliberately no MIN_ENLARGEMENT constant. Global constraint 2 --
# nothing is enlarged by less than 1.5x -- is not a number this module
# consults; it is a property of the table below, where ideal/floor is exactly
# 1.5 on all four device-axis pairs. So "needs less than 1.5x" and "is at or
# above the floor" are one comparison, which is what `bands.band_for` makes,
# and `test_ratio_is_exactly_one_and_a_half` is what keeps the table honest.
# A constant sat here for a while, defined, commented at length and referenced
# by nothing, reading as though something consulted it.


@dataclass(frozen=True)
class Target:
    device: str
    axis: str
    ideal: int
    floor: int

    @property
    def name(self) -> str:
        return f"{self.device}/{self.axis}"


DESKTOP_BY_WIDTH = Target(DESKTOP, WIDTH, 7680, 5120)
DESKTOP_BY_HEIGHT = Target(DESKTOP, HEIGHT, 4800, 3200)
PHONE_BY_HEIGHT = Target(PHONE, HEIGHT, 4320, 2880)
PHONE_BY_WIDTH = Target(PHONE, WIDTH, 2880, 1920)

ALL_TARGETS = (DESKTOP_BY_WIDTH, DESKTOP_BY_HEIGHT, PHONE_BY_HEIGHT, PHONE_BY_WIDTH)
DEVICES = (DESKTOP, PHONE)


def governing_dimension(width: int, height: int, target: Target) -> int:
    """The dimension the target sizes by."""
    return width if target.axis == WIDTH else height
