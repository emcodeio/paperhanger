from pathlib import Path

import pytest

from paperhanger import bands, formats, plan, report, sizes


def settings(tmp_path):
    return plan.OutputSettings(processing_dir=tmp_path / "processing", fmt="heic",
                               quality=formats.quality_for("heic"))


def test_estimate_counts_each_photo_once(tmp_path):
    """Three crop plans from one photo cost ONE whole-frame enlargement.

    1440x720 phone: three slices, band 4, so the upscaler runs once on the
    whole frame. 700x1800 on the desktop path would be band 5 -- rejected,
    zero plans, and the assertion below would never be reached.

    This fixture's three vertical thirds happen to tile EXACTLY with no
    overlap (ceil(720*2/3) = 480, 480*3 = 1440), which is the special case,
    not the rule -- geometry.py's own docstring says the three crops overlap
    for anything near square. Because of that exact tiling, a per-plan
    summation using each plan's own output pixels, or each crop's own source
    pixels x16, lands on the SAME number as the correct per-photo charge and
    this fixture alone cannot tell them apart. See
    test_estimate_counts_each_photo_once_with_overlapping_crop below, which can.
    """
    work = plan.plan_photo(Path("/s/sunset.jpg"), 1440, 720, "jpeg",
                           [sizes.PHONE], settings(tmp_path))
    assert len(work.plans) == 3
    expected = 0.8 * 16 * 1440 * 720 / 1_000_000
    assert report.estimate_seconds([work]) == expected


def test_estimate_counts_each_photo_once_with_overlapping_crop(tmp_path):
    """A crop whose three slices OVERLAP, so per-plan summation cannot hide
    behind exact tiling the way the 1440x720 fixture above does.

    3000x5000 desktop: three 3000x1875 slices, band 3 (upscale+reduce), and
    the slices overlap because slice_height*3 > height. The correct estimate
    (once per photo, on the whole 3000x5000 frame) is 192.0 s. A per-plan
    implementation using each plan's own 7680x4800 output gives 88.4736 s;
    using each crop's own 3000x1875 source x16 gives 216.0 s. Both are wrong
    and both differ from 192.0, so this fixture catches what the tiling
    fixture cannot.
    """
    work = plan.plan_photo(Path("/s/cliffs.jpg"), 3000, 5000, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    assert len(work.plans) == 3
    expected = 0.8 * 16 * 3000 * 5000 / 1_000_000
    assert expected == 192.0
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
    already_done = {Path("/s/d1.jpg"), Path("/s/d2.jpg"), Path("/s/d3.jpg")}
    text = report.render_report(works, already_done=already_done, non_images=1)
    assert "2 images" in text
    assert "1 rejected" in text
    assert "3 already done" in text
    assert "1 non-image skipped" in text


def test_single_image_header_is_singular(tmp_path):
    work = plan.plan_photo(Path("/s/a.jpg"), 9216, 6144, "jpeg", [sizes.DESKTOP],
                           settings(tmp_path))
    text = report.render_report([work])
    assert "1 image," in text
    assert "1 images" not in text


def test_singular_output_and_non_image_nouns(tmp_path):
    """The image noun was singularized in round 1 but the others weren't, so
    a single-output header read '1 image, 1 outputs'. Same rule, every noun."""
    work = plan.plan_photo(Path("/s/a.jpg"), 9216, 6144, "jpeg", [sizes.DESKTOP],
                           settings(tmp_path))
    assert len(work.plans) == 1
    text = report.render_report([work], non_images=1)
    assert "1 output," in text
    assert "1 outputs" not in text
    assert "1 non-image skipped" in text
    assert "1 non-images skipped" not in text


def test_plural_output_and_non_image_nouns(tmp_path):
    opts = settings(tmp_path)
    work1 = plan.plan_photo(Path("/s/a.jpg"), 9216, 6144, "jpeg", [sizes.DESKTOP], opts)
    work2 = plan.plan_photo(Path("/s/b.jpg"), 9216, 6144, "jpeg", [sizes.DESKTOP], opts)
    assert len(work1.plans) == 1 and len(work2.plans) == 1
    text = report.render_report([work1, work2], non_images=2)
    assert "2 outputs" in text
    assert "2 non-images skipped" in text


def test_estimate_and_outputs_exclude_already_done(tmp_path):
    """The header must answer 'what will THIS RUN do', not 'how much was here
    originally' -- a photo already done costs nothing and produces nothing
    this run, however large its own upscale would have been.

    done_work (3000x5000 desktop, band 3) costs 192.0 s alone; pending_work
    (1440x720 phone, band 4) costs 13.27104 s alone. Summed they round to a
    different bucket ('~3 min' vs '~13 s'), so a version that still charges
    for done_work is distinguishable in the rendered text, not just in the
    raw float.
    """
    opts = settings(tmp_path)
    done_work = plan.plan_photo(Path("/s/heavy.jpg"), 3000, 5000, "jpeg",
                                [sizes.DESKTOP], opts)
    pending_work = plan.plan_photo(Path("/s/sunset.jpg"), 1440, 720, "jpeg",
                                   [sizes.PHONE], opts)
    assert len(done_work.plans) == 3
    assert len(pending_work.plans) == 3
    assert done_work.needs_upscale and pending_work.needs_upscale

    pending_only = report.estimate_seconds([pending_work])
    everything = report.estimate_seconds([done_work, pending_work])
    assert report.format_duration(pending_only) == "~13 s"
    assert report.format_duration(everything) == "~3 min"

    text = report.render_report([done_work, pending_work],
                                already_done={done_work.source})

    assert report.format_duration(pending_only) in text
    assert report.format_duration(everything) not in text
    assert "3 outputs" in text
    assert "6 outputs" not in text


def test_already_done_line_reachable_through_render_report(tmp_path):
    """AC 6 requires this line to come out of render_report, not just render_photo
    called directly -- render_report must be the thing that decides per photo."""
    work = plan.plan_photo(Path("/s/done.jpg"), 9216, 6144, "jpeg", [sizes.DESKTOP],
                           settings(tmp_path))
    text = report.render_report([work], already_done={Path("/s/done.jpg")})
    assert "already done, skipping" in text


def test_whole_image_line(tmp_path):
    work = plan.plan_photo(Path("/s/cliffs.jpg"), 8200, 5125, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    line = report.render_photo(work)
    assert "cliffs.jpg" in line
    assert "desktop/width" in line
    assert "8200 -> 7680" in line
    assert "downscale" in line
    assert "[1]" not in line, "band numbers are for the spec's reader, not the report"
    assert "rejected" not in line, "nothing was rejected -- no device should be named"
    assert line.count("7680") == 1, "the output size must not be printed twice"


def test_native_band_is_marked_below_target(tmp_path):
    """NATIVE lands in below_target/ exactly like UPSCALE_ONLY (bands.BELOW_TARGET
    and plan.destination_dir agree on this) but the action label used to mark
    only the UPSCALE_ONLY branch. 6000x4000 desktop is width-governed (6000),
    between floor (5120) and ideal (7680): NATIVE, not a crop."""
    work = plan.plan_photo(Path("/s/plain.jpg"), 6000, 4000, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    line = report.render_photo(work)
    assert "native" in line
    assert "below_target" in line


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
        assert line.count("7680x4800") == 1, "the output size must not be printed twice"


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


def test_action_raises_on_unknown_band(tmp_path):
    """_action must not silently mislabel an unrecognized band (REJECT, or a
    future sixth band) -- match bands.output_size's own ValueError rather than
    fall through to the last branch's text."""
    bogus = plan.OutputPlan(
        source=Path("/s/x.jpg"), device=sizes.DESKTOP, target=sizes.DESKTOP_BY_WIDTH,
        band=bands.REJECT, governing=100, out_width=1, out_height=1, factor=None,
        destination=Path("/x"), fmt="heic", quality=90,
    )
    with pytest.raises(ValueError):
        report._action(bogus)


def test_format_duration():
    assert report.format_duration(45) == "~45 s"
    assert report.format_duration(14 * 60) == "~14 min"
    assert report.format_duration(24 * 3600) == "~24 h"


def test_format_duration_boundaries():
    # Just below/at/above the s->min boundary (90 s).
    assert report.format_duration(89) == "~89 s"
    assert report.format_duration(90) == "~2 min"
    assert report.format_duration(91) == "~2 min"
    # Just below/at/above the min->h boundary (3600 s).
    assert report.format_duration(3599) == "~60 min"
    assert report.format_duration(3600) == "~1 h"
    assert report.format_duration(3601) == "~1 h"
    # A non-whole hour keeps its decimal; a whole one does not.
    assert report.format_duration(90 * 60) == "~1.5 h"
