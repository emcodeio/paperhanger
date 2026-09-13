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
    """Each row of the band table stands alone, so no evaluation order is
    implied and each is testable in isolation.

    This calls band_for and checks its ANSWER against the five predicates.
    An earlier version rebuilt the predicates inline and never called the
    function, which proved only that the arithmetic agreed with itself.
    """
    predicates = {
        bands.DOWNSCALE:      lambda d, t: d >= t.ideal,
        bands.NATIVE:         lambda d, t: t.floor <= d < t.ideal,
        bands.UPSCALE_REDUCE: lambda d, t: d < t.floor and d * 4 >= t.ideal,
        bands.UPSCALE_ONLY:   lambda d, t: d < t.floor and t.floor <= d * 4 < t.ideal,
        bands.REJECT:         lambda d, t: d * 4 < t.floor,
    }
    for target in sizes.ALL_TARGETS:
        for d in range(1, target.ideal + 200):
            answer = bands.band_for(d, target)
            holding = [b for b, p in predicates.items() if p(d, target)]
            assert holding == [answer], (
                f"{target.name} d={d}: band_for said {answer}, "
                f"predicates say {holding}"
            )
            # while we are here: no band may exceed the ideal on the
            # governing axis, or sips would be enlarging (constraint 1)
            if answer != bands.REJECT:
                w, h = (d, d * 2) if target.axis == sizes.WIDTH else (d * 2, d)
                out_w, out_h = bands.output_size(w, h, target, answer)
                governing = out_w if target.axis == sizes.WIDTH else out_h
                assert governing <= target.ideal, (
                    f"{target.name} d={d} band={answer} -> {governing} > {target.ideal}"
                )


def test_output_size_downscale_by_width():
    assert bands.output_size(9216, 6144, T, bands.DOWNSCALE) == (7680, 5120)


def test_output_size_downscale_by_height():
    target = sizes.DESKTOP_BY_HEIGHT
    assert bands.output_size(3840, 2160, target, bands.UPSCALE_REDUCE) == (8533, 4800)


def test_output_size_native_is_untouched():
    assert bands.output_size(6000, 3750, T, bands.NATIVE) == (6000, 3750)


def test_output_size_upscale_only_is_exactly_four_times():
    assert bands.output_size(1600, 1200, T, bands.UPSCALE_ONLY) == (6400, 4800)


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


def test_net_factor_refuses_a_rejected_band():
    """Mirrors output_size's guard. A caller that forgets to filter REJECT
    should fail loudly, not receive a meaningless factor."""
    with pytest.raises(ValueError, match="rejected"):
        bands.net_factor(100, T, bands.REJECT)


def test_bands_module_is_pure():
    """Nothing in the band table may reach outside the process.

    Asserted here rather than assumed: until this existed the suite checked
    only `classify.py` and `plan.py`, so an `import subprocess` appearing in
    this module tomorrow would have gone unremarked by every test in the tier
    -- even though the spec lists all six decision modules as pure.
    """
    import paperhanger.bands as module

    from tests.conftest import assert_pure_module

    assert_pure_module(module, allowed={"sizes"})
