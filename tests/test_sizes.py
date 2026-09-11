from paperhanger import sizes


def test_ratio_is_exactly_one_and_a_half():
    """Global constraint 2. The 1.5x cutoff is the floor; this is what makes
    'needs less than 1.5x' and 'is at or above the floor' one condition."""
    for target in sizes.ALL_TARGETS:
        assert target.ideal / target.floor == 1.5, target.name


def test_target_values():
    assert (sizes.DESKTOP_BY_WIDTH.ideal, sizes.DESKTOP_BY_WIDTH.floor) == (7680, 5120)
    assert (sizes.DESKTOP_BY_HEIGHT.ideal, sizes.DESKTOP_BY_HEIGHT.floor) == (4800, 3200)
    assert (sizes.PHONE_BY_HEIGHT.ideal, sizes.PHONE_BY_HEIGHT.floor) == (4320, 2880)
    assert (sizes.PHONE_BY_WIDTH.ideal, sizes.PHONE_BY_WIDTH.floor) == (2880, 1920)


def test_phone_frames_are_two_to_three():
    """Both phone axes describe the same frame: 2880x4320 ideal, 1920x2880 floor."""
    assert sizes.PHONE_BY_WIDTH.ideal * 3 == sizes.PHONE_BY_HEIGHT.ideal * 2
    assert sizes.PHONE_BY_WIDTH.floor * 3 == sizes.PHONE_BY_HEIGHT.floor * 2


def test_governing_dimension_follows_axis():
    assert sizes.governing_dimension(4000, 3000, sizes.DESKTOP_BY_WIDTH) == 4000
    assert sizes.governing_dimension(4000, 3000, sizes.DESKTOP_BY_HEIGHT) == 3000
