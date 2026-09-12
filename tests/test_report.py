from pathlib import Path

from paperhanger import formats, plan, report, sizes


def settings(tmp_path):
    return plan.OutputSettings(processing_dir=tmp_path / "processing", fmt="heic",
                               quality=formats.quality_for("heic"))


def test_estimate_counts_each_photo_once(tmp_path):
    """Three crop plans from one photo cost ONE whole-frame enlargement.

    1440x720 phone: three slices, band 4, so the upscaler runs once on the
    whole frame. 700x1800 on the desktop path would be band 5 -- rejected,
    zero plans, and the assertion below would never be reached.
    """
    work = plan.plan_photo(Path("/s/sunset.jpg"), 1440, 720, "jpeg",
                           [sizes.PHONE], settings(tmp_path))
    assert len(work.plans) == 3
    expected = 0.8 * 16 * 1440 * 720 / 1_000_000
    assert report.estimate_seconds([work]) == expected


def test_estimate_is_zero_without_upscaling(tmp_path):
    work = plan.plan_photo(Path("/s/big.jpg"), 9216, 6144, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    assert work.plans and not work.needs_upscale
    assert report.estimate_seconds([work]) == 0


def test_header_counts(tmp_path):
    opts = settings(tmp_path)
    works = [
        plan.plan_photo(Path("/s/a.jpg"), 9216, 6144, "jpeg", [sizes.DESKTOP], opts),
        plan.plan_photo(Path("/s/tiny.jpg"), 200, 300, "jpeg", list(sizes.DEVICES), opts),
    ]
    text = report.render_report(works, already_done=3, non_images=1)
    assert "2 images" in text
    assert "1 rejected" in text
    assert "3 already done" in text
    assert "1 non-image skipped" in text


def test_whole_image_line(tmp_path):
    work = plan.plan_photo(Path("/s/cliffs.jpg"), 8200, 5125, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    line = report.render_photo(work)
    assert "cliffs.jpg" in line
    assert "desktop/width" in line
    assert "8200 -> 7680" in line
    assert "downscale" in line
    assert "[1]" not in line, "band numbers are for the spec's reader, not the report"


def test_crop_lines_are_indented(tmp_path):
    work = plan.plan_photo(Path("/s/sunset.jpg"), 3000, 5000, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    text = report.render_photo(work)
    lines = text.splitlines()
    assert "crop 3x horizontal" in lines[0]
    assert len(lines) == 4
    for line, position in zip(lines[1:], ("top", "middle", "bottom")):
        assert line.startswith("   ")
        assert position in line
        assert "7680x4800" in line


def test_rejected_on_both_devices(tmp_path):
    work = plan.plan_photo(Path("/s/tiny.gif"), 200, 300, "gif",
                           list(sizes.DEVICES), settings(tmp_path))
    assert "reject (too small for both)" in report.render_photo(work)


def test_rejected_on_one_device_only(tmp_path):
    work = plan.plan_photo(Path("/s/small.jpg"), 900, 1400, "jpeg",
                           list(sizes.DEVICES), settings(tmp_path))
    line = report.render_photo(work)
    assert "desktop rejected" in line
    assert "phone" in line


def test_already_done_line(tmp_path):
    work = plan.plan_photo(Path("/s/done.jpg"), 9216, 6144, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    assert "already done, skipping" in report.render_photo(work, already_done=True)


def test_format_duration():
    assert report.format_duration(45) == "~45 s"
    assert report.format_duration(14 * 60) == "~14 min"
    assert report.format_duration(24 * 3600) == "~24.0 h"
