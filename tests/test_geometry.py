import pytest

from paperhanger import geometry, sizes
from paperhanger.classify import classify


def test_horizontal_slice_dimensions():
    rects = geometry.horizontal_thirds(1600, 2400)
    assert [(r.width, r.height) for r in rects] == [(1600, 1000)] * 3


def test_horizontal_slice_offsets():
    rects = geometry.horizontal_thirds(1600, 2400)
    assert [r.y for r in rects] == [0, 700, 1400]   # 0, (2400-1000)//2, 2400-1000
    assert all(r.x == 0 for r in rects)


def test_vertical_slice_dimensions():
    rects = geometry.vertical_thirds(3000, 2000)
    assert [(r.width, r.height) for r in rects] == [(1334, 2000)] * 3  # ceil(4000/3)


def test_vertical_slice_offsets():
    rects = geometry.vertical_thirds(3000, 2000)
    assert [r.x for r in rects] == [0, 833, 1666]   # 0, (3000-1334)//2, 3000-1334
    assert all(r.y == 0 for r in rects)


def test_ceiling_not_truncation():
    """1601*10/16 = 1000.625. Truncation gives 1000, which makes the slice
    1.601:1 and it fails the desktop-by-width test. Ceiling gives 1001."""
    rects = geometry.horizontal_thirds(1601, 3000)
    assert rects[0].height == 1001


@pytest.mark.parametrize("width", range(1000, 1064))
def test_every_horizontal_slice_self_classifies(width):
    """The planner assigns DESKTOP_BY_WIDTH by construction. This is the test
    that makes that safe for every residue of width % 16."""
    height = width * 3
    for rect in geometry.horizontal_thirds(width, height):
        assert classify(rect.width, rect.height, sizes.DESKTOP) is sizes.DESKTOP_BY_WIDTH


@pytest.mark.parametrize("height", range(1000, 1064))
def test_every_vertical_slice_self_classifies(height):
    width = height * 3
    for rect in geometry.vertical_thirds(width, height):
        assert classify(rect.width, rect.height, sizes.PHONE) is sizes.PHONE_BY_HEIGHT


@pytest.mark.parametrize("width,height", [
    (1000, 1000), (1000, 1001), (1000, 5000), (1601, 1602), (4000, 4001),
])
def test_horizontal_slices_stay_inside_the_source(width, height):
    for rect in geometry.horizontal_thirds(width, height):
        assert rect.x >= 0 and rect.y >= 0
        assert rect.x + rect.width <= width
        assert rect.y + rect.height <= height


@pytest.mark.parametrize("width,height", [
    (1000, 1000), (1001, 1000), (5000, 1000), (1602, 1601), (4001, 4000),
])
def test_vertical_slices_stay_inside_the_source(width, height):
    for rect in geometry.vertical_thirds(width, height):
        assert rect.x >= 0 and rect.y >= 0
        assert rect.x + rect.width <= width
        assert rect.y + rect.height <= height


def test_slices_overlap_for_near_square_sources():
    """Documented, not a defect: these are three crops, not three disjoint
    thirds. At 1:1 the slice is 0.625h tall in an h-tall frame, so top and
    bottom overlap by 25% of the frame and share 40% of their own pixels."""
    rects = geometry.horizontal_thirds(1000, 1000)
    top, _, bottom = rects
    overlap = (top.y + top.height) - bottom.y
    assert overlap == 250
    assert overlap / top.height == pytest.approx(0.4)


def test_slices_are_disjoint_for_tall_sources():
    """At 3:1 or taller there is no overlap; the crops tile the frame."""
    top, _, bottom = geometry.horizontal_thirds(1000, 3000)
    assert top.y + top.height <= bottom.y


def test_scaled_rect_multiplies_every_field():
    """Needed by the executor: a slice cut from a 4x frame sits at 4x offsets."""
    rect = geometry.Rect(x=100, y=200, width=300, height=400)
    assert rect.scaled(4) == geometry.Rect(x=400, y=800, width=1200, height=1600)
