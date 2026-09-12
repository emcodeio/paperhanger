"""Crop rectangles for the two crop paths. Pure integer arithmetic.

Preserved from the legacy script, including that the three crops OVERLAP for
anything near square. They are three crops, not three disjoint thirds.

The slice dimension rounds UP. With truncation a 16:10 slice computes to a ratio
slightly above 1.6 and fails its own classification test; rounding up puts it at
or below 1.6 for every residue, so the slice satisfies the class the planner
assigns it and never needs re-deriving. Same for the 2:3 vertical slice.
"""

from dataclasses import dataclass

HORIZONTAL_POSITIONS = ("top", "middle", "bottom")
VERTICAL_POSITIONS = ("left", "center", "right")


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def scaled(self, factor: int) -> "Rect":
        """The same rectangle in an image enlarged by `factor`."""
        return Rect(
            x=self.x * factor,
            y=self.y * factor,
            width=self.width * factor,
            height=self.height * factor,
        )


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def horizontal_thirds(width: int, height: int):
    """Three 16:10 slices: top, middle, bottom. For the desktop crop path."""
    slice_height = _ceil_div(width * 10, 16)
    if slice_height > height:
        raise ValueError(f"{width}x{height} is too wide to slice horizontally")
    return (
        Rect(0, 0, width, slice_height),
        Rect(0, (height - slice_height) // 2, width, slice_height),
        Rect(0, height - slice_height, width, slice_height),
    )


def vertical_thirds(width: int, height: int):
    """Three 2:3 slices: left, center, right. For the phone crop path."""
    slice_width = _ceil_div(height * 2, 3)
    if slice_width > width:
        raise ValueError(f"{width}x{height} is too tall to slice vertically")
    return (
        Rect(0, 0, slice_width, height),
        Rect((width - slice_width) // 2, 0, slice_width, height),
        Rect(width - slice_width, 0, slice_width, height),
    )
