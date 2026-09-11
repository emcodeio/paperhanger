"""Target sizes and the constants the decision tree runs on. No I/O, no floats."""

from dataclasses import dataclass

DESKTOP = "desktop"
PHONE = "phone"
WIDTH = "width"
HEIGHT = "height"

UPSCALE_FACTOR = 4

# The one enlargement the upscaler is allowed to skip: anything already at or
# above its floor keeps its real pixels. ideal/floor IS that ratio, so there is
# no separate constant -- see test_ratio_is_exactly_one_and_a_half.
MIN_ENLARGEMENT = 1.5


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
