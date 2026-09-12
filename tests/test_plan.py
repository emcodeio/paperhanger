from pathlib import Path

import pytest

from paperhanger import bands, formats, plan, sizes


def settings(tmp_path, fmt="heic", quality=None):
    return plan.OutputSettings(
        processing_dir=tmp_path / "processing",
        fmt=fmt,
        quality=formats.quality_for(fmt, quality),
    )


# ---------- formats ----------

def test_quality_defaults():
    assert formats.quality_for("heic") == 80
    assert formats.quality_for("jpeg") == 90
    assert formats.quality_for("avif") == 85
    assert formats.quality_for("png") is None


def test_quality_with_png_is_an_error():
    with pytest.raises(ValueError, match="lossless"):
        formats.quality_for("png", 90)


def test_unknown_format():
    with pytest.raises(ValueError, match="unknown format"):
        formats.quality_for("tiff")
    with pytest.raises(ValueError, match="unknown format"):
        formats.extension("tiff")


def test_an_override_replaces_the_default():
    assert formats.quality_for("jpeg", 70) == 70
    assert formats.quality_for("heic", 0) == 0      # 0 is a value, not "unset"


def test_quality_out_of_range():
    with pytest.raises(ValueError, match="0-100"):
        formats.quality_for("jpeg", 101)


# ---------- whole-image plans ----------

def test_desktop_downscale_plan(tmp_path):
    work = plan.plan_photo(Path("/src/cliffs.jpg"), 9216, 6144, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    assert len(work.plans) == 1
    p = work.plans[0]
    assert p.band == bands.DOWNSCALE
    assert (p.out_width, p.out_height) == (7680, 5120)
    assert p.crop is None and p.position is None
    assert p.destination.name == "cliffs_desktop_7680x5120_native.heic"
    assert p.destination.parent == tmp_path / "processing" / "to_sort_desktop"
    assert not p.needs_upscale


def test_below_target_goes_to_the_subfolder(tmp_path):
    work = plan.plan_photo(Path("/src/lichen.jpg"), 6000, 3750, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    p = work.plans[0]
    assert p.band == bands.NATIVE
    assert (p.out_width, p.out_height) == (6000, 3750)
    assert p.destination.parent.name == "below_target"
    assert p.destination.name == "lichen_desktop_6000x3750_native.heic"


def test_upscale_reduce_plan_records_the_net_factor(tmp_path):
    """1920x1200 desktop-by-width: 1920 < 5120 floor, 1920*4 = 7680 >= ideal."""
    work = plan.plan_photo(Path("/src/wide.jpg"), 1920, 1200, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    p = work.plans[0]
    assert p.target is sizes.DESKTOP_BY_WIDTH
    assert p.band == bands.UPSCALE_REDUCE
    assert (p.out_width, p.out_height) == (7680, 4800)
    assert p.factor_token == "4x"           # 7680/1920 == 4.0
    assert p.needs_upscale and p.needs_resize
    # Upscaled but landing AT the ideal, so not below target.
    assert p.destination.parent.name == "to_sort_desktop"


def test_upscale_only_plan(tmp_path):
    work = plan.plan_photo(Path("/src/moth.png"), 1600, 1200, "png",
                           [sizes.DESKTOP], settings(tmp_path, "png"))
    p = work.plans[0]
    assert p.band == bands.UPSCALE_ONLY
    assert (p.out_width, p.out_height) == (6400, 4800)
    assert p.destination.name == "moth_desktop_6400x4800_4x.png"
    assert p.destination.parent.name == "below_target"
    assert p.needs_upscale and not p.needs_resize


# ---------- crop plans ----------

def test_desktop_crop_produces_three_plans_with_positions(tmp_path):
    work = plan.plan_photo(Path("/src/sunset.jpg"), 3000, 5000, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    assert len(work.plans) == 3
    assert [p.position for p in work.plans] == ["top", "middle", "bottom"]
    for p in work.plans:
        assert p.target is sizes.DESKTOP_BY_WIDTH   # assigned by construction
        assert p.governing == 3000                  # the slice width == source width
        assert p.crop is not None
    assert [p.destination.name for p in work.plans] == [
        "sunset_top_desktop_7680x4800_2.6x.heic",
        "sunset_middle_desktop_7680x4800_2.6x.heic",
        "sunset_bottom_desktop_7680x4800_2.6x.heic",
    ]


def test_phone_crop_produces_three_plans(tmp_path):
    work = plan.plan_photo(Path("/src/ocean.jpg"), 4000, 3000, "jpeg",
                           [sizes.PHONE], settings(tmp_path))
    assert [p.position for p in work.plans] == ["left", "center", "right"]
    for p in work.plans:
        assert p.target is sizes.PHONE_BY_HEIGHT
        assert p.governing == 3000                  # the slice height == source height


def test_both_devices_are_independent(tmp_path):
    """A 900x1400 image is too small for desktop but fine for phone. Global
    constraint: neither pass can abort the other."""
    work = plan.plan_photo(Path("/src/small.jpg"), 900, 1400, "jpeg",
                           list(sizes.DEVICES), settings(tmp_path))
    devices = {p.device for p in work.plans}
    assert devices == {sizes.PHONE}
    assert work.rejected_devices == [sizes.DESKTOP]


def test_rejected_on_every_device(tmp_path):
    work = plan.plan_photo(Path("/src/tiny.gif"), 200, 300, "gif",
                           list(sizes.DEVICES), settings(tmp_path))
    assert work.plans == []
    assert sorted(work.rejected_devices) == sorted(sizes.DEVICES)
    assert work.rejected_everywhere


def test_plans_are_flat(tmp_path):
    """No nesting. Spec section 3: a tree would only be justified by recursion,
    and slices are planned directly rather than re-classified.

    A SQUARE source, because that is the only shape both devices crop: desktop
    crops when w <= h and phone when w >= h. A portrait like 3000x5000 crops
    for desktop but takes the phone WIDTH path, giving 4 plans, not 6.
    """
    work = plan.plan_photo(Path("/src/square.jpg"), 4000, 4000, "jpeg",
                           list(sizes.DEVICES), settings(tmp_path))
    assert isinstance(work.plans, list)
    for p in work.plans:
        assert not hasattr(p, "children")
    assert len(work.plans) == 6   # three desktop slices, three phone slices
    assert [p.position for p in work.plans] == [
        "top", "middle", "bottom", "left", "center", "right"]


def test_portrait_crops_for_desktop_but_not_for_phone(tmp_path):
    """The asymmetry the test above depends on, pinned explicitly."""
    work = plan.plan_photo(Path("/src/sunset.jpg"), 3000, 5000, "jpeg",
                           list(sizes.DEVICES), settings(tmp_path))
    assert len(work.plans) == 4
    desktop = [p for p in work.plans if p.device == sizes.DESKTOP]
    phone = [p for p in work.plans if p.device == sizes.PHONE]
    assert len(desktop) == 3 and all(p.crop is not None for p in desktop)
    assert len(phone) == 1 and phone[0].crop is None
    assert phone[0].target is sizes.PHONE_BY_WIDTH


def test_needs_upscale_is_a_photo_level_question(tmp_path):
    """The upscaler runs once per photo, so the question is asked of the photo."""
    work = plan.plan_photo(Path("/src/sunset.jpg"), 3000, 5000, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    assert work.needs_upscale is True
    big = plan.plan_photo(Path("/src/huge.jpg"), 9216, 6144, "jpeg",
                          [sizes.DESKTOP], settings(tmp_path))
    assert big.needs_upscale is False


# ---------- collisions ----------

def test_collision_between_same_stem_different_extension(tmp_path):
    """Live in the corpus: cityscape_reflection_4592.jpg and .png are both
    1920x1046 and collide on all four of their outputs."""
    opts = settings(tmp_path)
    works = [
        plan.plan_photo(Path("/src/city.jpg"), 1920, 1046, "jpeg", list(sizes.DEVICES), opts),
        plan.plan_photo(Path("/src/city.png"), 1920, 1046, "png", list(sizes.DEVICES), opts),
    ]
    collisions = plan.find_collisions(works)
    assert len(collisions) == 4 == len(works[0].plans)   # every output collides
    for destination, sources in collisions.items():
        assert len(sources) == 2
        assert {s.suffix for s in sources} == {".jpg", ".png"}


def test_no_collision_between_different_stems(tmp_path):
    opts = settings(tmp_path)
    works = [
        plan.plan_photo(Path("/src/a.jpg"), 1920, 1046, "jpeg", list(sizes.DEVICES), opts),
        plan.plan_photo(Path("/src/b.jpg"), 1920, 1046, "jpeg", list(sizes.DEVICES), opts),
    ]
    assert plan.find_collisions(works) == {}


def test_plan_module_is_pure():
    """The decision layer never touches the world. Enforced by parsing the
    imports, not by matching strings: `from subprocess import run` would slip
    past a substring check for "import subprocess"."""
    import paperhanger.plan as module

    from tests.conftest import assert_pure_module

    assert_pure_module(module, allowed={
        "dataclasses", "pathlib", "bands", "formats", "geometry", "sizes", "classify",
    })
