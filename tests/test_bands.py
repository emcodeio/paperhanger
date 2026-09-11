import pytest

from paperhanger import bands, sizes

T = sizes.DESKTOP_BY_WIDTH  # ideal 7680, floor 5120


@pytest.mark.parametrize("governing,expected", [
    (8000, bands.DOWNSCALE),        # above ideal
    (7680, bands.DOWNSCALE),        # exactly ideal
    (7679, bands.NATIVE),
    (5120, bands.NATIVE),           # exactly floor
    (5119, bands.UPSCALE_REDUCE),
    (1920, bands.UPSCALE_REDUCE),   # d*4 == ideal exactly -> band 3, not 4
    (1919, bands.UPSCALE_ONLY),
    (1280, bands.UPSCALE_ONLY),     # d*4 == floor exactly
    (1279, bands.REJECT),
    (1, bands.REJECT),
])
def test_desktop_width_boundaries(governing, expected):
    assert bands.band_for(governing, T) == expected


def test_bands_are_disjoint_and_total():
    """Global constraint: each row of the band table stands alone, so no
    evaluation order is implied and each is testable in isolation."""
    for target in sizes.ALL_TARGETS:
        for d in range(1, target.ideal + 200):
            matches = [
                d >= target.ideal,
                target.floor <= d < target.ideal,
                d < target.floor and d * 4 >= target.ideal,
                d < target.floor and target.floor <= d * 4 < target.ideal,
                d * 4 < target.floor,
            ]
            assert sum(matches) == 1, f"{target.name} d={d} matched {sum(matches)}"


def test_output_size_downscale_by_width():
    assert bands.output_size(9216, 6144, T, bands.DOWNSCALE) == (7680, 5120)


def test_output_size_downscale_by_height():
    target = sizes.DESKTOP_BY_HEIGHT
    assert bands.output_size(3840, 2160, target, bands.UPSCALE_REDUCE) == (8533, 4800)


def test_output_size_native_is_untouched():
    assert bands.output_size(6000, 3750, T, bands.NATIVE) == (6000, 3750)


def test_output_size_upscale_only_is_exactly_four_times():
    assert bands.output_size(1600, 1200, T, bands.UPSCALE_ONLY) == (6400, 4800)


def test_output_never_exceeds_ideal_on_the_governing_axis():
    for target in sizes.ALL_TARGETS:
        for d in (target.ideal + 500, target.ideal, target.floor, target.floor - 1,
                  target.ideal // 4, target.floor // 4):
            if d < 1:
                continue
            b = bands.band_for(d, target)
            if b == bands.REJECT:
                continue
            w, h = (d, d * 2) if target.axis == sizes.WIDTH else (d * 2, d)
            out_w, out_h = bands.output_size(w, h, target, b)
            out_governing = out_w if target.axis == sizes.WIDTH else out_h
            assert out_governing <= target.ideal, f"{target.name} d={d} band={b}"


@pytest.mark.parametrize("governing,band,expected", [
    (9216, bands.DOWNSCALE, "native"),
    (6000, bands.NATIVE, "native"),
    (1600, bands.UPSCALE_ONLY, "4x"),
    (4912, bands.UPSCALE_REDUCE, "1.6x"),   # 7680/4912 = 1.563
    (3840, bands.UPSCALE_REDUCE, "2x"),     # 7680/3840 = 2.0 -> "2x", not "2.0x"
    (1920, bands.UPSCALE_REDUCE, "4x"),     # 7680/1920 = 4.0
])
def test_factor_token(governing, band, expected):
    assert bands.factor_token(bands.net_factor(governing, T, band)) == expected
