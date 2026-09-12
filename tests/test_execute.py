from dataclasses import replace
from pathlib import Path

import pytest

from paperhanger import bands, execute, formats, geometry, imaging, plan, sizes
from tests.pngwriter import read_png_rgb, write_marked_png, write_png

MARKER = (240, 30, 200)
BASE = (10, 10, 10)


def settings(tmp_path, fmt="png"):
    return plan.OutputSettings(processing_dir=tmp_path / "processing", fmt=fmt,
                               quality=formats.quality_for(fmt))


def stub_encoder(monkeypatch, seen=None):
    """Replace the encoder with one that writes a real image at the size asked.

    Used by the tests about render's own plumbing -- staging, mkdir, cleanup --
    where a real sips run on a 6 Mpx fixture would add seconds and prove
    nothing. It still WRITES, so a render that forgot to create the destination
    directory fails here exactly as it would in production, and it writes the
    requested dimensions, because render measures its own output before
    publishing it. The stub therefore differs from the real encoder only in
    pixel content and container format.
    """
    def encode(source, out_width, out_height, fmt, quality, out_path, resize):
        if seen is not None:
            seen.append((Path(source), out_width, out_height, fmt, quality,
                         Path(out_path), resize))
        write_png(out_path, out_width, out_height)

    monkeypatch.setattr(imaging, "resize_and_encode", encode)


def stub_crop(monkeypatch, seen):
    """Replace the crop with one that writes a real image of the rect's size.

    Real, not junk bytes, because render measures what it is about to resample
    before resampling it -- a stub that wrote nothing readable would fail the
    no-enlargement guard rather than the behaviour under test.
    """
    def crop(source, rect, out_path):
        seen.append((Path(source), rect, Path(out_path)))
        write_png(out_path, rect.width, rect.height)

    monkeypatch.setattr(imaging, "crop", crop)


def patch_colours(tmp_path, image, rect, tag):
    """The distinct colours in a small patch of `image`.

    Decoding a 6 Mpx output in pure Python would cost seconds and hundreds of
    megabytes, so the region checks sample 4x4 corners instead. Both rects sit
    one pixel in from the edge, which keeps them clear of the two --cropOffset
    shapes imaging.crop has to pad around: this is a measuring instrument, not
    a test of crop.
    """
    patch = tmp_path / f"patch_{tag}.png"
    imaging.crop(image, rect, patch)
    _, _, rows = read_png_rgb(patch)
    return {pixel for row in rows for pixel in row}


def staged_path(target):
    return target.destination.with_name(target.destination.name
                                        + execute.PARTIAL_SUFFIX)


# ---------- whole-image plans ----------

def test_render_whole_image_plan(tmp_path):
    """3000x4500 on the phone path: h >= 4320 ideal, so band 1, reduced to
    2880x4320. The smallest band-1 fixture available -- desktop band 1 would
    need 7680 px on the governing axis."""
    source = write_png(tmp_path / "cliffs.png", 3000, 4500)
    work = plan.plan_photo(source, 3000, 4500, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    assert target.band == bands.DOWNSCALE
    assert (target.out_width, target.out_height) == (2880, 4320)

    execute.render(target, source, scale=1, workdir=tmp_path / "work")

    assert target.destination.exists()
    assert imaging.probe(target.destination)[:2] == (target.out_width,
                                                     target.out_height)


def test_render_native_plan_does_not_resample(tmp_path):
    """2000x3000 phone-by-height: 3000 is between the 2880 floor and the 4320
    ideal, so the pixels are untouched and only the container changes."""
    source = write_png(tmp_path / "lichen.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    assert target.band == bands.NATIVE
    execute.render(target, source, scale=1, workdir=tmp_path / "work")
    assert imaging.probe(target.destination)[:2] == (2000, 3000)


def test_render_creates_the_destination_directory(tmp_path, monkeypatch):
    """Nothing creates to_sort_phone/ before the first output lands in it."""
    source = write_png(tmp_path / "lichen.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    stub_encoder(monkeypatch)

    assert not target.destination.parent.exists()
    execute.render(target, source, scale=1, workdir=tmp_path / "work")
    assert target.destination.parent.is_dir()
    assert target.destination.exists()


# ---------- crop plans ----------

def test_render_crop_plan_cuts_the_right_region(tmp_path):
    """4000x3000 phone crop: three 2000x3000 slices, governing 3000 -> band 2,
    so they are cut and encoded at native size with no resampling.

    The marker covers exactly the middle slice, so the rendered file is all
    marker if and only if render cut that slice: the left slice reads base at
    its left corner and the right slice base at its right corner. Dimensions
    cannot tell the three apart -- all three are 2000x3000.

    scale=1 is only legitimate for bands 1 and 2. A band 3 or 4 plan rendered
    at scale=1 would make sips ENLARGE the crop, which global constraint 1
    forbids; those bands are rendered from the 4x frame, in Task 9.
    """
    work = plan.plan_photo(tmp_path / "ocean.png", 4000, 3000, "png",
                           [sizes.PHONE], settings(tmp_path))
    middle = work.plans[1]
    assert middle.position == "center" and middle.crop is not None
    assert middle.band == bands.NATIVE and not middle.needs_upscale
    # The marker goes exactly where the planner says the middle slice is.
    source = write_marked_png(tmp_path / "ocean.png", 4000, 3000, middle.crop,
                              base=BASE, marker=MARKER)

    execute.render(middle, source, scale=1, workdir=tmp_path / "work")

    assert imaging.probe(middle.destination)[:2] == (middle.out_width,
                                                     middle.out_height)
    near_left = geometry.Rect(1, 1, 4, 4)
    near_right = geometry.Rect(middle.out_width - 5, middle.out_height - 5, 4, 4)
    assert patch_colours(tmp_path, middle.destination, near_left, "ml") == {MARKER}
    assert patch_colours(tmp_path, middle.destination, near_right, "mr") == {MARKER}


def test_the_region_check_would_fail_on_a_neighbouring_slice(tmp_path):
    """Guards the guard: the LEFT slice of the same marked source must NOT
    come back all-marker, or the assertion above proves nothing."""
    work = plan.plan_photo(tmp_path / "ocean.png", 4000, 3000, "png",
                           [sizes.PHONE], settings(tmp_path))
    left, middle = work.plans[0], work.plans[1]
    source = write_marked_png(tmp_path / "ocean.png", 4000, 3000, middle.crop,
                              base=BASE, marker=MARKER)

    execute.render(left, source, scale=1, workdir=tmp_path / "work")

    assert patch_colours(tmp_path, left.destination,
                         geometry.Rect(1, 1, 4, 4), "ll") == {BASE}


def test_render_crops_before_it_resizes(tmp_path):
    """The fusion hazard, against live pixels.

    The middle slice of the 4000x3000 source, forced into a band that resizes:
    cut 2000x3000 out of the frame, then reduce that to 1000x1500. Fused into
    one sips call the resample is applied against the PRE-crop width of 4000,
    and the file comes back wrong at exit 0, looking like any other output.
    Measured on this fixture, cropping 2000x3000 at +1000+0 in the same call
    as the resample gives:

        --resampleHeightWidth 1500 1000   ->   500x1500   (what this code emits)
        --resampleWidth 1000              ->   500x750
        --resampleHeight 1500             ->  1000x1500   (right, by accident)

    Only crop-then-resample reaches 1000x1500 for the right reason. The last
    row is why constraint 4's both-axes rule is not merely about rounding: a
    single flag can mask the fusion bug as easily as expose it.
    """
    work = plan.plan_photo(tmp_path / "ocean.png", 4000, 3000, "png",
                           [sizes.PHONE], settings(tmp_path))
    middle = work.plans[1]
    resizing = replace(middle, band=bands.DOWNSCALE, out_width=1000, out_height=1500)
    assert resizing.needs_resize and resizing.crop == middle.crop
    source = write_marked_png(tmp_path / "ocean.png", 4000, 3000, middle.crop,
                              base=BASE, marker=MARKER)

    execute.render(resizing, source, scale=1, workdir=tmp_path / "work")

    assert imaging.probe(resizing.destination)[:2] == (1000, 1500), \
        "500x750 would mean the resample was fused with the crop"
    assert patch_colours(tmp_path, resizing.destination,
                         geometry.Rect(1, 1, 4, 4), "fused") == {MARKER}


def test_render_scales_the_crop_rect_for_an_upscaled_frame(tmp_path, monkeypatch):
    """When the input is the 4x whole frame, the slice sits at 4x offsets.

    1440x720 on the PHONE path: a crop whose governing dimension is 720, so
    720*4 == 2880 == the phone floor -> band 4, which needs the upscaler. The
    source is 1 Mpx and no real 4x frame is built, because both imaging calls
    are stubbed: this test is about the rect arithmetic, not about pixels.
    """
    source = write_png(tmp_path / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    assert target.band == bands.UPSCALE_ONLY
    assert target.crop is not None and target.needs_upscale

    crops, encodes = [], []
    stub_crop(monkeypatch, crops)
    stub_encoder(monkeypatch, encodes)

    frame = tmp_path / "frame.png"
    execute.render(target, frame, scale=4, workdir=tmp_path / "work")

    cropped_from, rect, cropped_to = crops[0]
    assert cropped_from == frame
    assert rect == target.crop.scaled(4)

    encoded_from, out_width, out_height, _, _, _, resize = encodes[0]
    assert encoded_from == cropped_to, "the encode must read the crop, not the frame"
    assert (out_width, out_height) == (target.out_width, target.out_height)
    assert resize is True, (
        "a slice of the 4x frame is 4x the planned size and must come back "
        "down, even in a band that does not resize the source"
    )


def test_a_scale_4_input_is_reduced_even_in_a_band_that_never_resizes(
        tmp_path, monkeypatch):
    """`resize = target.needs_resize or scale != 1`, against measured pixels.

    The `or scale != 1` half had exactly one guard: the stubbed
    `assert resize is True` in the test above. Deleting the clause passed
    every other assertion in the suite, because the two bands `run_photo`
    ever renders at scale 4 both hide it -- band 3 sets `needs_resize`
    anyway, and a band-4 slice of the 4x frame is already exactly its planned
    size, so not resampling it produces the right dimensions by accident.

    This is the case the clause is actually for: `render`'s contract takes
    ANY plan at scale 4, and a plan whose band does not resize, handed an
    input four times its planned size, must still come down. Without the
    clause the 1600x1200 frame is encoded untouched and `_verify_dimensions`
    refuses it as 1600x1200 against a plan that says 400x300 -- which is the
    mutation failing for the right reason, on a real file, rather than on a
    recorded flag.

    2 Mpx and nothing stubbed: this measures the file, not the argv.
    """
    frame = write_png(tmp_path / "frame_4x.png", 1600, 1200)
    work = plan.plan_photo(tmp_path / "moss.png", 2000, 3000, "png",
                           [sizes.PHONE], settings(tmp_path))
    native = work.plans[0]
    assert native.band == bands.NATIVE and not native.needs_resize

    # The same band, sized to what a 1600x1200 frame is 4x of.
    target = replace(native, out_width=400, out_height=300, crop=None)

    execute.render(target, frame, scale=4, workdir=tmp_path / "work")

    assert imaging.probe(target.destination)[:2] == (400, 300), \
        "1600x1200 would mean the 4x input was published without reducing it"


def test_render_passes_the_rect_through_unscaled_at_scale_one(tmp_path, monkeypatch):
    """The other half: the original's own coordinates are used untouched, and
    a band-2 plan at scale 1 resamples not at all."""
    source = write_png(tmp_path / "s.png", 4000, 3000)
    work = plan.plan_photo(source, 4000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    middle = work.plans[1]

    crops, encodes = [], []
    stub_crop(monkeypatch, crops)
    stub_encoder(monkeypatch, encodes)

    execute.render(middle, source, scale=1, workdir=tmp_path / "work")

    assert crops[0][1] == middle.crop
    assert encodes[0][-1] is False, "band 2 at scale 1 must not resample"


# ---------- quality ----------

@pytest.mark.parametrize("fmt,quality", [("heic", 80), ("jpeg", 90)])
def test_render_passes_the_plans_quality_to_the_encoder(tmp_path, monkeypatch,
                                                        fmt, quality):
    """OutputPlan.quality is asserted nowhere else in the suite: replacing
    `quality=opts.quality` with `quality=None` in the planner leaves every
    other test green. These are two of the three values constraint 12 fixes by
    measurement, and render is where quality reaches an encoder.

    Two formats at two different values, because one would equally well pin a
    hardcoded literal: render passing a constant 80 would satisfy the heic case
    and send `-s formatOptions 80` to every png plan, which sips accepts
    without a word.

    The file is probed too, because staging through a `.partial` suffix is
    exactly the shape that made sips silently keep the wrong format before
    (imaging fact 7): it warns `Output file suffix should be heic` and exits 0
    either way.
    """
    source = write_png(tmp_path / "lichen.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE],
                           settings(tmp_path, fmt))
    target = work.plans[0]
    assert target.quality == quality

    real = imaging.resize_and_encode
    seen = []

    def spy(source, out_width, out_height, fmt, quality, out_path, resize):
        seen.append((fmt, quality))
        real(source, out_width, out_height, fmt, quality, out_path, resize=resize)

    monkeypatch.setattr(imaging, "resize_and_encode", spy)
    execute.render(target, source, scale=1, workdir=tmp_path / "work")

    assert seen == [(fmt, quality)]
    assert imaging.probe(target.destination) == (2000, 3000, fmt)


# ---------- the no-enlargement guard ----------

def test_a_band_3_plan_rendered_from_its_original_is_refused(tmp_path):
    """Global constraint 1: no image is ever enlarged except by the ML model.

    1440x2160 on the phone path governs at 2160 -- below the 2880 floor, and
    2160*4 clears the 4320 ideal, so band 3: enlarge, then reduce to 2880x4320.
    Handed its own original at scale 1, sips would be asked to more than double
    it and would comply, at exit 0, with a file of exactly the planned size and
    invented detail no check downstream could question. It must raise instead.
    """
    source = write_png(tmp_path / "small.png", 1440, 2160)
    work = plan.plan_photo(source, 1440, 2160, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    assert target.band == bands.UPSCALE_REDUCE
    assert (target.out_width, target.out_height) == (2880, 4320)

    with pytest.raises(imaging.ImagingError) as caught:
        execute.render(target, source, scale=1, workdir=tmp_path / "work")
    assert "1440x2160" in str(caught.value) and "2880x4320" in str(caught.value)
    assert not target.destination.exists()
    assert list(target.destination.parent.glob("*.partial")) == []


def test_the_guard_allows_the_4x_frame_at_exactly_the_planned_size(tmp_path,
                                                                   monkeypatch):
    """Band 4 cut from the 4x frame lands EXACTLY on the planned size: the
    slice is 480x720, so 4x is 1920x2880 and the plan asks for 1920x2880. A
    guard written with > rather than >= would refuse the commonest legitimate
    render in the whole pipeline."""
    source = write_png(tmp_path / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    assert target.band == bands.UPSCALE_ONLY
    assert (target.out_width, target.out_height) == (1920, 2880)

    crops = []
    stub_crop(monkeypatch, crops)          # writes a real 1920x2880 image
    stub_encoder(monkeypatch)

    # The frame is never read: stub_crop answers for it.
    execute.render(target, tmp_path / "frame.png", scale=4, workdir=tmp_path / "work")
    assert target.destination.exists()


def test_the_guard_does_not_run_for_a_plan_that_never_resamples(tmp_path,
                                                                monkeypatch):
    """Band 2 encodes the pixels untouched, so there is nothing to measure and
    no probe to pay for."""
    source = write_png(tmp_path / "lichen.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    assert target.band == bands.NATIVE

    def refuse(*args, **kwargs):
        raise AssertionError("the guard measured a render that does not resample")

    monkeypatch.setattr(execute, "_refuse_to_enlarge", refuse)
    stub_encoder(monkeypatch)
    execute.render(target, source, scale=1, workdir=tmp_path / "work")


# ---------- the output post-condition ----------

def test_a_render_that_would_be_the_wrong_size_is_refused(tmp_path):
    """The hole the no-enlargement guard cannot see.

    A band 4 plan handed its own original at scale 1 crops the 480x720 slice
    and resamples nothing -- `needs_resize` is False and `scale` is 1, so the
    guard never runs. Before this check, render wrote
    `s_left_phone_1920x2880_4x.png` at 480x720 and returned: measured, at exit
    0, with no enlargement and therefore no breach of constraint 1, but a file
    whose own name asserts dimensions it does not have.
    """
    source = write_png(tmp_path / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    assert target.band == bands.UPSCALE_ONLY and not target.needs_resize
    assert target.destination.name == "s_left_phone_1920x2880_4x.png"

    with pytest.raises(imaging.ImagingError) as caught:
        execute.render(target, source, scale=1, workdir=tmp_path / "work")
    assert "480x720" in str(caught.value) and "1920x2880" in str(caught.value)


def test_a_wrong_sized_render_publishes_nothing(tmp_path):
    """Neither the destination nor a stray `.partial` survives the refusal."""
    source = write_png(tmp_path / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]

    with pytest.raises(imaging.ImagingError):
        execute.render(target, source, scale=1, workdir=tmp_path / "work")

    assert not target.destination.exists()
    assert list(target.destination.parent.glob("*.partial")) == []
    assert [p for p in (tmp_path / "work").rglob("*") if p.is_file()] == []


def test_a_wrong_sized_render_leaves_an_earlier_runs_output_standing(tmp_path):
    """Why the check runs on the staged file rather than after the rename.

    The rename would overwrite the earlier file first, so verifying afterwards
    means destroying a good wallpaper and then deleting its bad replacement --
    the user is left with nothing where they had something correct. Measuring
    before publishing costs the same probe on the same bytes and never puts a
    wrong file at the destination at all.
    """
    source = write_png(tmp_path / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    target.destination.parent.mkdir(parents=True)
    target.destination.write_bytes(b"an earlier run's wallpaper")

    with pytest.raises(imaging.ImagingError):
        execute.render(target, source, scale=1, workdir=tmp_path / "work")

    assert target.destination.read_bytes() == b"an earlier run's wallpaper"


def test_an_unreadable_output_is_refused(tmp_path, monkeypatch):
    """`imaging` asserts only that a file APPEARED -- a truncated or empty one
    satisfies that. Probing it is the only way to tell, and a file that cannot
    be measured must not be published either."""
    source = write_png(tmp_path / "s.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]

    monkeypatch.setattr(imaging, "resize_and_encode",
                        lambda src, w, h, fmt, q, out_path, resize:
                        Path(out_path).write_bytes(b"not an image at all"))

    with pytest.raises(imaging.ImagingError, match="not a readable image"):
        execute.render(target, source, scale=1, workdir=tmp_path / "work")
    assert not target.destination.exists()
    assert list(target.destination.parent.glob("*.partial")) == []


@pytest.mark.parametrize("fmt", formats.FORMATS)
def test_the_post_condition_can_measure_every_output_format(tmp_path, fmt):
    """It probes the STAGED file, whose name carries a second extension --
    `x.avif.partial`. If sips decided readability by suffix, every render of
    some format would fail at runtime while the suite stayed green, so each
    format is driven all the way through. 1920x2880 is the smallest phone
    fixture that lands in band 2: 2880 is exactly the floor.
    """
    source = write_png(tmp_path / "lichen.png", 1920, 2880)
    work = plan.plan_photo(source, 1920, 2880, "png", [sizes.PHONE],
                           settings(tmp_path, fmt))
    target = work.plans[0]
    assert target.band == bands.NATIVE

    written = execute.render(target, source, scale=1, workdir=tmp_path / "work")

    assert imaging.probe(written) == (1920, 2880, fmt)


def test_the_post_condition_passes_a_correct_render_through(tmp_path):
    """The normal path is unchanged: a plan whose output is the planned size
    publishes exactly as before."""
    source = write_png(tmp_path / "cliffs.png", 3000, 4500)
    work = plan.plan_photo(source, 3000, 4500, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]

    written = execute.render(target, source, scale=1, workdir=tmp_path / "work")

    assert written == target.destination
    assert imaging.probe(written)[:2] == (target.out_width, target.out_height)
    assert list(target.destination.parent.glob("*.partial")) == []


# ---------- staging ----------

def test_no_partial_survives_success(tmp_path):
    source = write_png(tmp_path / "s.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    execute.render(target, source, scale=1, workdir=tmp_path / "work")
    assert list(target.destination.parent.glob("*.partial")) == []


def test_the_output_is_staged_beside_its_destination(tmp_path, monkeypatch):
    """Staging in the DESTINATION directory is what makes the rename atomic
    whatever volume TMPDIR is on, and keeps a torn write out of the folder the
    user sorts from. A successful render leaves nothing to inspect afterwards,
    so this watches where the encoder was asked to write."""
    source = write_png(tmp_path / "s.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    seen = []
    stub_encoder(monkeypatch, seen)

    written = execute.render(target, source, scale=1, workdir=tmp_path / "work")

    assert seen[0][5] == staged_path(target)
    assert seen[0][5].parent == target.destination.parent
    assert written == target.destination


def test_failure_leaves_nothing_behind(tmp_path):
    """A source sips cannot read is a silent skip: exit 0, no output file.
    imaging's post-condition turns it into an ImagingError, and render must
    not leave a truncated or stale file where the sorter will see it."""
    source = write_png(tmp_path / "s.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    with pytest.raises(imaging.ImagingError):
        execute.render(target, tmp_path / "absent.png", scale=1,
                       workdir=tmp_path / "work")
    assert not target.destination.exists()
    assert list(target.destination.parent.glob("*.partial")) == []


def test_a_failure_leaves_an_earlier_runs_output_alone(tmp_path):
    """A failure must not destroy a wallpaper the user already had.

    `staged.replace` is atomic and the last fallible statement in render, so a
    failure never half-wrote the destination: there is nothing of this run's to
    clean up there. Anything at that path came from an earlier run, and the
    name encodes stem, position, device, both dimensions and the factor -- it
    is this same plan's output from this same source, which makes deleting it
    pure loss. This is the archive-never-overwrites rule in another costume.
    """
    source = write_png(tmp_path / "s.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    target.destination.parent.mkdir(parents=True)
    target.destination.write_bytes(b"an earlier run's wallpaper")

    with pytest.raises(imaging.ImagingError):
        execute.render(target, tmp_path / "absent.png", scale=1,
                       workdir=tmp_path / "work")

    assert target.destination.read_bytes() == b"an earlier run's wallpaper"
    assert list(target.destination.parent.glob("*.partial")) == []


def test_a_failure_after_staging_removes_the_staged_file(tmp_path, monkeypatch):
    """The encoder can succeed and the rename still fail -- a full volume, a
    destination turned read-only. The staged file must not survive that."""
    source = write_png(tmp_path / "s.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    stub_encoder(monkeypatch)

    def no_space(self, other):
        raise OSError("no space left on device")

    monkeypatch.setattr(Path, "replace", no_space)

    with pytest.raises(OSError):
        execute.render(target, source, scale=1, workdir=tmp_path / "work")
    assert not staged_path(target).exists()
    assert not target.destination.exists()


def test_sweep_partials(tmp_path):
    root = tmp_path / "processing" / "to_sort_desktop"
    root.mkdir(parents=True)
    (root / "a.heic.partial").write_bytes(b"junk")
    (root / "b.heic").write_bytes(b"real")
    assert execute.sweep_partials(tmp_path / "processing") == 1
    assert not (root / "a.heic.partial").exists()
    assert (root / "b.heic").exists()


def test_sweep_partials_reaches_every_subdirectory(tmp_path):
    processing = tmp_path / "processing"
    for folder in ("to_sort_desktop", "to_sort_phone/below_target"):
        (processing / folder).mkdir(parents=True)
        (processing / folder / "x.heic.partial").write_bytes(b"junk")
    assert execute.sweep_partials(processing) == 2
    assert list(processing.rglob("*.partial")) == []


def test_sweep_partials_on_a_missing_directory(tmp_path):
    """The first run has no processing tree yet; sweeping must not raise."""
    assert execute.sweep_partials(tmp_path / "never_created") == 0


# ---------- intermediates ----------

def test_render_removes_its_intermediates(tmp_path):
    """The left slice sits at (0, 0), the --cropOffset shape imaging.crop has
    to work around by padding, so this covers the padded intermediate as well.
    PNG intermediates run to tens of megabytes -- a 4x frame's more -- and
    retaining them to process exit would fill the volume a fifth of the way
    into a real run."""
    source = write_png(tmp_path / "ocean.png", 4000, 3000)
    work = plan.plan_photo(source, 4000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    workdir = tmp_path / "work"
    execute.render(work.plans[0], source, scale=1, workdir=workdir)
    leftovers = [p for p in workdir.rglob("*") if p.is_file()] if workdir.exists() else []
    assert leftovers == [], f"intermediates left behind: {leftovers}"


def test_intermediates_are_removed_on_failure_too(tmp_path, monkeypatch):
    """A run that dies on plan 200 of 2467 must not have kept plans 1-199's
    intermediates alive on the way there."""
    source = write_png(tmp_path / "ocean.png", 4000, 3000)
    work = plan.plan_photo(source, 4000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    workdir = tmp_path / "work"

    def explode(*args, **kwargs):
        raise imaging.ImagingError("sips exited 13: unrecognized format")

    monkeypatch.setattr(imaging, "resize_and_encode", explode)
    with pytest.raises(imaging.ImagingError):
        execute.render(work.plans[1], source, scale=1, workdir=workdir)
    assert [p for p in workdir.rglob("*") if p.is_file()] == []


def test_an_intermediate_left_by_a_failed_crop_is_removed_too(tmp_path, monkeypatch):
    """A crop can write its output and still fail -- sips killed mid-write, a
    post-condition that trips after the file appears. The cleanup must cover
    the file it was ASKED for, not only the one it was told about."""
    source = write_png(tmp_path / "ocean.png", 4000, 3000)
    work = plan.plan_photo(source, 4000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    workdir = tmp_path / "work"

    def crop_then_fail(src, rect, out_path):
        Path(out_path).write_bytes(b"half a cropped region")
        raise imaging.ImagingError("sips exited 13 after writing")

    monkeypatch.setattr(imaging, "crop", crop_then_fail)
    with pytest.raises(imaging.ImagingError):
        execute.render(work.plans[1], source, scale=1, workdir=workdir)
    assert [p for p in workdir.rglob("*") if p.is_file()] == []


def test_crop_intermediates_are_png_and_one_per_plan(tmp_path, monkeypatch):
    """Three slices are cut from one frame into one workdir. imaging.crop
    always writes PNG bytes whatever the name says, so an intermediate named
    after the OUTPUT format would lie about its own contents; and named by the
    source alone the three would collide, which matters the moment anything
    holds two at once.

    The outputs are heic here precisely so that `.png` cannot come for free
    from the destination's own suffix.
    """
    source = write_png(tmp_path / "ocean.png", 4000, 3000)
    work = plan.plan_photo(source, 4000, 3000, "png", [sizes.PHONE],
                           settings(tmp_path, "heic"))
    assert {p.destination.suffix for p in work.plans} == {".heic"}
    crops = []
    stub_crop(monkeypatch, crops)
    stub_encoder(monkeypatch)

    for target in work.plans:
        execute.render(target, source, scale=1, workdir=tmp_path / "work")

    paths = [out_path for _, _, out_path in crops]
    assert len(paths) == 3
    assert len(set(paths)) == 3, f"intermediates collide: {paths}"
    assert all(p.suffix == ".png" for p in paths), paths


# ---------- the per-photo upscale ----------

def context(tmp_path, fake_upscaler):
    binary, models = fake_upscaler
    return execute.Context(
        processing_dir=tmp_path / "processing",
        workroot=tmp_path / "work",
        upscayl=binary,
        models_dir=models,
    )


def spy_on(monkeypatch, name):
    """Record the first argument of every call to imaging.<name>, and still
    make the real call.

    Recording without calling is not an option on this path: every imaging
    operation asserts that the file it was asked for exists afterwards, so a
    stand-in that writes nothing raises instead of returning, and the test
    would be measuring its own stub rather than the executor.
    """
    calls = []
    real = getattr(imaging, name)

    def spy(*args, **kwargs):
        calls.append(args[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(imaging, name, spy)
    return calls


def test_one_upscale_for_three_crop_plans(tmp_path, fake_upscaler, monkeypatch):
    """The saving this whole design rests on: three overlapping slices are cut
    from ONE enlargement, not enlarged three times.

    1440x720 on the phone path: crop into three 480x720 slices whose governing
    dimension is 720, and 720*4 == 2880 == the phone floor, so band 4 -- the
    cheapest fixture that actually needs the upscaler. The 4x frame is
    5760x2880; the desktop equivalent would be 26 Mpx.
    """
    source = write_png(tmp_path / "sunset.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    assert len(work.plans) == 3 and work.needs_upscale

    calls = spy_on(monkeypatch, "upscale")

    written = execute.run_photo(work, context(tmp_path, fake_upscaler))

    assert len(calls) == 1, f"expected one upscale, got {len(calls)}"
    assert written == [target.destination for target in work.plans]
    for target in work.plans:
        assert imaging.probe(target.destination)[:2] == (target.out_width,
                                                         target.out_height)


def centre_colours(tmp_path, image, tag, size=8):
    """The distinct colours in a small patch at the CENTRE of `image`.

    `patch_colours` samples a corner, which is right for a crop cut straight
    from a source: the marker's edge is exactly the slice's edge, so a corner
    is the most sensitive place to look. It is the wrong place here. A 4x
    resample interpolates across the boundary between marker and base, so
    the outermost columns of a slice cut at that boundary are blends of the
    two and belong to neither. The centre of the slice is unambiguous, and
    the three slices of this fixture are disjoint, so the centre still tells
    them apart -- which is all the assertion needs.
    """
    _, _, width, height = (0, 0, *imaging.probe(image)[:2])
    rect = geometry.Rect((width - size) // 2, (height - size) // 2, size, size)
    patch = tmp_path / f"centre_{tag}.png"
    imaging.crop(image, rect, patch)
    _, _, rows = read_png_rgb(patch)
    return {pixel for row in rows for pixel in row}


def test_a_band_3_photo_renders_its_reduced_slices_end_to_end(tmp_path,
                                                              fake_upscaler):
    """Band 3, live pixels, source to published file. The band tier 1 skipped.

    2400x1200 on the phone path: three DISJOINT 800x1200 slices, governing
    1200, and 1200*4 == 4800 >= the 4320 ideal, so UPSCALE_REDUCE. Each slice
    comes out of the 9600x4800 frame as 3200x4800 and must then be reduced to
    2880x4320. Band 3 is the only band where that reduction does anything: a
    band-4 slice of the 4x frame is already exactly its planned size, so
    `resize` is a no-op there whether or not it is asked for.

    This test exists because a census of every successful publish in the fast
    suite found band 1 three times, band 2 thirty-seven, band 4 fifty-four,
    and band 3 NOT ONCE -- while band 3 is 2116 of the corpus's 3441 outputs,
    61% of a real run. Its only two tier-1 appearances both asserted a
    refusal. Tier 2 covers it and tier 2 is deselected from the fast loop and
    skips entirely on a machine without the author's photographs.

    Three things are measured that no stub can fake:

      * all three files measure 2880x4320, so the reduce after the crop
        actually ran and `_verify_dimensions` saw a real file;
      * the marker covers exactly the CENTRE slice, so the three outputs are
        told apart by pixel where they are identical by dimension. The left
        and right assertions are what make the centre one mean anything;
      * the LEFT slice sits at (0,0) in the frame, which is one of the two
        --cropOffset shapes sips silently ignores, so this is also the first
        time tier 1 pads a 4x FRAME rather than a source.

    It costs about six seconds of the fast suite's eighty, which is the price
    of the band the real corpus mostly consists of.
    """
    opts = settings(tmp_path)
    work = plan.plan_photo(tmp_path / "dunes.png", 2400, 1200, "png",
                           [sizes.PHONE], opts)
    left, centre, right = work.plans
    assert [p.band for p in work.plans] == [bands.UPSCALE_REDUCE] * 3
    assert [p.position for p in work.plans] == ["left", "center", "right"]
    assert all((p.out_width, p.out_height) == (2880, 4320) for p in work.plans)
    assert centre.crop == geometry.Rect(800, 0, 800, 1200)
    assert left.crop == geometry.Rect(0, 0, 800, 1200)

    source = write_marked_png(tmp_path / "dunes.png", 2400, 1200, centre.crop,
                              base=BASE, marker=MARKER)

    written = execute.run_photo(work, context(tmp_path, fake_upscaler))

    assert written == [p.destination for p in work.plans]
    for target in work.plans:
        assert imaging.probe(target.destination)[:2] == (2880, 4320), \
            "3200x4800 would mean the 4x slice was never reduced"

    assert centre_colours(tmp_path, centre.destination, "c") == {MARKER}
    assert centre_colours(tmp_path, left.destination, "l") == {BASE}
    assert centre_colours(tmp_path, right.destination, "r") == {BASE}

    # 4320/1200 = 3.6: the NET enlargement, not the model's 4x, and the one
    # thing in the filename that tells a band-3 output from a band-4 one.
    assert centre.destination.name == "dunes_center_phone_2880x4320_3.6x.png"


def test_no_upscale_when_nothing_needs_it(tmp_path, fake_upscaler, monkeypatch):
    source = write_png(tmp_path / "big.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    assert not work.needs_upscale

    calls = spy_on(monkeypatch, "upscale")
    execute.run_photo(work, context(tmp_path, fake_upscaler))

    assert calls == []
    assert imaging.probe(work.plans[0].destination)[:2] == (2000, 3000)


def test_normalize_always_runs_on_the_upscale_path(tmp_path, fake_upscaler,
                                                   monkeypatch):
    """Global constraint 5: every source, not only unreadable formats. A PNG
    source still needs its pixels forced into sRGB -- the corpus's 118
    non-sRGB files are all JPEG or PNG, so a format-based condition would never
    fire for any of them."""
    source = write_png(tmp_path / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))

    calls = spy_on(monkeypatch, "normalize_to_srgb_png")
    execute.run_photo(work, context(tmp_path, fake_upscaler))

    assert len(calls) == 1
    assert calls[0] == source


def test_the_upscaler_never_reads_the_original(tmp_path, fake_upscaler, monkeypatch):
    """Guards the guard above. Normalizing and then handing the ORIGINAL to
    the model satisfies 'normalize was called' while wasting the call, and the
    fake upscaler would accept a PNG source without complaint."""
    source = write_png(tmp_path / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))

    calls = spy_on(monkeypatch, "upscale")
    execute.run_photo(work, context(tmp_path, fake_upscaler))

    assert calls and source not in calls, f"the model was handed {calls}"


def test_pixel_cap_falls_back_to_per_plan(tmp_path, fake_upscaler, monkeypatch):
    source = write_png(tmp_path / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    # 1 Mpx rather than 1 px: the message names both numbers, and a cap of one
    # pixel would make it report a 0.0 Mpx cap.
    monkeypatch.setattr(execute, "UPSCALE_PIXEL_CAP", 1_000_000)

    calls = spy_on(monkeypatch, "upscale")
    messages = []
    ctx = context(tmp_path, fake_upscaler)
    ctx.log = messages.append
    execute.run_photo(work, ctx)

    assert len(calls) == 3, "the fallback upscales each plan separately"
    assert messages == [
        "  s.png: 16.6 Mpx exceeds the 1.0 Mpx cap; "
        "falling back to per-plan upscaling"
    ], "the fallback must be reported, not silent, and with its own numbers"
    for target in work.plans:
        assert imaging.probe(target.destination)[:2] == (target.out_width,
                                                         target.out_height)


def test_the_cap_is_measured_against_the_4x_output(tmp_path, fake_upscaler,
                                                   monkeypatch):
    """The cap is 300 Mpx of ENLARGEMENT, not of source. Compared against the
    source's own pixel count it would never fire at all: the largest whole-frame
    job in the corpus is 622 Mpx of output from 39 Mpx of input.

    16588801 is one pixel above this fixture's 1440*720*16, so a comparison
    against anything smaller than the 4x output leaves the cap unfired.
    """
    source = write_png(tmp_path / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    assert work.upscale_output_pixels == 16_588_800

    calls = spy_on(monkeypatch, "upscale")
    monkeypatch.setattr(execute, "UPSCALE_PIXEL_CAP", 16_588_800)
    execute.run_photo(work, context(tmp_path, fake_upscaler))
    assert len(calls) == 1, "exactly at the cap is not over it"

    calls.clear()
    monkeypatch.setattr(execute, "UPSCALE_PIXEL_CAP", 16_588_799)
    execute.run_photo(work, context(tmp_path, fake_upscaler))
    assert len(calls) == 3, "one pixel over the cap must fall back"


def test_over_the_cap_a_cropless_plan_keeps_the_whole_frame(tmp_path, fake_upscaler,
                                                            monkeypatch):
    """Being over the cap is necessary but not sufficient.

    1280x800 plans as one cropless desktop band 4 plus three phone slices. Its
    cropless plan's region IS the photo, so per-plan upscaling asks the model
    for exactly the frame the cap declined: both strategies peak at 16,384,000
    px of 4x output, the fallback taking four runs to get there against the
    whole frame's one. Falling back there costs three extra model runs -- 30 to
    90 seconds each -- and bounds nothing.
    """
    source = write_png(tmp_path / "s.png", 1280, 800)
    work = plan.plan_photo(source, 1280, 800, "png", list(sizes.DEVICES),
                           settings(tmp_path))
    upscaling = [p for p in work.plans if p.needs_upscale]
    assert len(upscaling) == 4
    assert sum(1 for p in upscaling if p.crop is None) == 1

    peak = max(p.out_width * p.out_height for p in upscaling)
    assert peak == work.upscale_output_pixels == 16_384_000, \
        "the fallback's largest single enlargement is the whole frame anyway"

    calls = spy_on(monkeypatch, "upscale")
    messages = []
    monkeypatch.setattr(execute, "UPSCALE_PIXEL_CAP", 1_000_000)
    ctx = context(tmp_path, fake_upscaler)
    ctx.log = messages.append
    execute.run_photo(work, ctx)

    assert len(calls) == 1, f"expected the whole frame, got {len(calls)} runs"
    assert messages == [
        "  s.png: 16.4 Mpx exceeds the 1.0 Mpx cap, but a plan needs the "
        "whole frame; upscaling it anyway"
    ], "declining to fall back is a strategy decision too, and must be reported"
    for target in work.plans:
        assert imaging.probe(target.destination)[:2] == (target.out_width,
                                                         target.out_height)


def test_the_cap_message_names_both_numbers_at_the_boundary(tmp_path, monkeypatch):
    """1300x14450 is 300,560,000 px of 4x output against the real 300 Mpx cap:
    over it, and a shape the corpus's own aspect ratios reach. Printed with
    integer division both numbers read 300, so the one line a twenty-four-hour
    run gives the user is `300 Mpx exceeds the 300 Mpx cap`.

    The fixture is 18.8 Mpx, so nothing is rendered -- the planner is pure and
    the two steps that would touch pixels are stubbed. This is about the
    sentence.
    """
    work = plan.plan_photo(Path("/src/tall.jpg"), 1300, 14450, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    assert work.upscale_output_pixels == 300_560_000
    assert all(p.crop is not None for p in work.plans if p.needs_upscale)

    monkeypatch.setattr(execute, "_upscale_one_plan",
                        lambda target, work, ctx, workdir: tmp_path / "absent.png")
    monkeypatch.setattr(execute, "render",
                        lambda target, source_image, scale, workdir: target.destination)
    messages = []
    execute.run_photo(work, execute.Context(
        processing_dir=tmp_path / "processing", workroot=tmp_path / "work",
        upscayl=tmp_path / "bin", models_dir=tmp_path / "models",
        log=messages.append,
    ))

    assert messages == [
        "  tall.jpg: 300.6 Mpx exceeds the 300.0 Mpx cap; "
        "falling back to per-plan upscaling"
    ]


def test_the_whole_frame_message_names_both_numbers_at_the_boundary(tmp_path,
                                                                    monkeypatch):
    """The other half of the boundary, against the real 300 Mpx cap.

    8000x2400 is desktop-by-height: governing 2400, below the 3200 floor and
    2400*4 clear of the 4800 ideal, so band 3 with no crop. 307.2 Mpx of 4x
    output, over the cap, and the fallback cannot help -- so it keeps the whole
    frame, which is the several-minute pause this line exists to explain.
    """
    work = plan.plan_photo(Path("/src/wide.jpg"), 8000, 2400, "jpeg",
                           [sizes.DESKTOP], settings(tmp_path))
    assert work.upscale_output_pixels == 307_200_000
    assert [p.crop for p in work.plans if p.needs_upscale] == [None]

    monkeypatch.setattr(execute, "_upscale_whole_frame",
                        lambda work, ctx, workdir: tmp_path / "absent.png")
    monkeypatch.setattr(execute, "render",
                        lambda target, source_image, scale, workdir: target.destination)
    messages = []
    execute.run_photo(work, execute.Context(
        processing_dir=tmp_path / "processing", workroot=tmp_path / "work",
        upscayl=tmp_path / "bin", models_dir=tmp_path / "models",
        log=messages.append,
    ))

    assert messages == [
        "  wide.jpg: 307.2 Mpx exceeds the 300.0 Mpx cap, but a plan needs "
        "the whole frame; upscaling it anyway"
    ]


def test_a_photo_under_the_cap_reports_no_strategy_at_all(tmp_path, fake_upscaler,
                                                          monkeypatch):
    """Guards both message tests. A log that fired for every upscaling photo
    would satisfy either assertion above while saying nothing, and the cap is
    the whole point of saying anything."""
    source = write_png(tmp_path / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    assert work.upscale_output_pixels < execute.UPSCALE_PIXEL_CAP

    messages = []
    ctx = context(tmp_path, fake_upscaler)
    ctx.log = messages.append
    execute.run_photo(work, ctx)

    assert messages == []


def test_upscale_one_plan_refuses_a_cropless_plan(tmp_path, fake_upscaler):
    """The invariant run_photo now relies on, made checkable.

    A cropless plan's region is the whole frame, so enlarging it "per plan"
    holds exactly the frame the cap declined and pays an extra model run for
    each sibling. run_photo will not route one here; this is what happens if
    that ever stops being true.

    1440x2160 on the phone path: one whole-image plan, governing 2160, below
    the 2880 floor and 2160*4 clear of the 4320 ideal, so band 3.
    """
    source = write_png(tmp_path / "s.png", 1440, 2160)
    work = plan.plan_photo(source, 1440, 2160, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    assert target.crop is None and target.band == bands.UPSCALE_REDUCE

    ctx = context(tmp_path, fake_upscaler)
    workdir = Path(ctx.workroot) / "one"
    workdir.mkdir(parents=True)

    with pytest.raises(ValueError, match="whole-frame path"):
        execute._upscale_one_plan(target, work, ctx, workdir)

    assert [p for p in workdir.iterdir()] == [], \
        "the refusal must come before anything is written"


def test_upscale_one_plan_leaves_only_the_enlargement(tmp_path, fake_upscaler):
    """The live path, called directly. The left slice sits at (0, 0), the
    --cropOffset shape imaging.crop pads around, so this covers the padded
    intermediate as well as the crop and the normalized copy -- three files
    that must all be gone, against one that must remain."""
    source = write_png(tmp_path / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    assert target.position == "left" and target.crop == geometry.Rect(0, 0, 480, 720)

    ctx = context(tmp_path, fake_upscaler)
    workdir = Path(ctx.workroot) / "one"
    workdir.mkdir(parents=True)

    enlarged = execute._upscale_one_plan(target, work, ctx, workdir)

    assert imaging.probe(enlarged)[:2] == (1920, 2880)
    assert [p.name for p in workdir.iterdir()] == [enlarged.name], \
        "only the enlargement may outlive the call"


def test_run_photo_takes_bands_1_and_2_from_the_original(tmp_path, fake_upscaler,
                                                         monkeypatch):
    """The 4x frame for bands 3 and 4, the ORIGINAL for bands 1 and 2.

    The smallest real photo that mixes the two is 1920x2880 -- desktop thirds
    in band 3, the phone whole image in band 2 -- whose 4x frame is 88 Mpx, so
    the mix is made by hand instead: the centre slice of the 1440x720 phone
    fixture is demoted to a band that resamples nothing. Taken from the frame
    at scale 4 it would come back 480x720 all the same, having been enlarged
    and then thrown away again, and every check in render would pass.
    """
    source = write_png(tmp_path / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    work.plans[1] = replace(work.plans[1], band=bands.NATIVE,
                            out_width=480, out_height=720)
    assert [p.needs_upscale for p in work.plans] == [True, False, True]

    seen = []
    real = execute.render

    def spy(target, source_image, scale, workdir):
        seen.append((target.position, Path(source_image), scale))
        return real(target, source_image, scale, workdir=workdir)

    monkeypatch.setattr(execute, "render", spy)
    execute.run_photo(work, context(tmp_path, fake_upscaler))

    read_from = {position: (image, scale) for position, image, scale in seen}
    assert read_from["center"] == (source, 1)
    assert read_from["left"][1] == 4 and read_from["right"][1] == 4
    assert read_from["left"][0] != source
    assert read_from["left"][0] == read_from["right"][0], \
        "both slices must be cut from the same frame"
    for target in work.plans:
        assert imaging.probe(target.destination)[:2] == (target.out_width,
                                                         target.out_height)


def test_enlarged_frame_is_deleted_after_the_last_plan(tmp_path, fake_upscaler):
    source = write_png(tmp_path / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    ctx = context(tmp_path, fake_upscaler)
    execute.run_photo(work, ctx)
    leftovers = [p for p in Path(ctx.workroot).rglob("*") if p.is_file()] \
        if Path(ctx.workroot).exists() else []
    assert leftovers == [], f"left behind: {leftovers}"


@pytest.mark.parametrize("strategy", ["whole frame", "per plan"])
def test_one_enlargement_is_alive_at_a_time(tmp_path, fake_upscaler, monkeypatch,
                                            strategy):
    """Peak occupancy, which is a different question from what survives.

    The workdir is swept when the photo finishes whatever happens, so every
    other test here passes with all three of the fallback's enlargements kept
    alive until then -- and three regions held at once gives back most of what
    the cap was imposed to save. This watches the workdir as each plan starts
    instead: one enlargement, the one about to be read.
    """
    source = write_png(tmp_path / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    if strategy == "per plan":
        monkeypatch.setattr(execute, "UPSCALE_PIXEL_CAP", 1)

    alive = []
    real = execute.render

    def spy(target, source_image, scale, workdir):
        alive.append(sorted(p.name for p in Path(workdir).iterdir() if p.is_file()))
        return real(target, source_image, scale, workdir=workdir)

    monkeypatch.setattr(execute, "render", spy)
    ctx = context(tmp_path, fake_upscaler)
    ctx.log = lambda message: None
    execute.run_photo(work, ctx)

    assert len(alive) == 3
    assert all(len(held) == 1 for held in alive), f"{strategy}: {alive}"


@pytest.mark.parametrize("strategy", ["whole frame", "per plan"])
def test_a_failed_plan_still_takes_the_frame_with_it(tmp_path, fake_upscaler,
                                                     monkeypatch, strategy):
    """A photo that dies mid-render must not leave its enlargement behind.

    The 4x frames are the largest intermediates the tool makes -- 114 GB across
    a full run against 28 GB free -- and a run that fails on photo 200 of 894
    has to survive the remaining 694. Both strategies are covered because the
    fallback allocates its frames somewhere else entirely.
    """
    source = write_png(tmp_path / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    if strategy == "per plan":
        monkeypatch.setattr(execute, "UPSCALE_PIXEL_CAP", 1)

    def explode(*args, **kwargs):
        raise imaging.ImagingError("sips exited 13: unrecognized format")

    monkeypatch.setattr(imaging, "resize_and_encode", explode)
    ctx = context(tmp_path, fake_upscaler)
    ctx.log = lambda message: None

    with pytest.raises(imaging.ImagingError):
        execute.run_photo(work, ctx)

    root = Path(ctx.workroot)
    leftovers = [p for p in root.rglob("*") if p.is_file()] if root.exists() else []
    assert leftovers == [], f"{strategy}: left behind {leftovers}"


def test_fake_upscaler_rejects_heic(tmp_path, fake_upscaler):
    """The stub must mirror the real binary's input restriction, or the
    normalize step is untested."""
    binary, models = fake_upscaler
    png = write_png(tmp_path / "s.png", 64, 64, noise=True)
    heic = tmp_path / "s.heic"
    imaging.resize_and_encode(png, 64, 64, "heic", 80, heic, resize=False)
    with pytest.raises(imaging.ImagingError):
        imaging.upscale(heic, tmp_path / "out.png", binary, models)


def test_fake_upscaler_actually_enlarges_by_four(tmp_path, fake_upscaler):
    """Guards every test above it. A stand-in that wrote a copy rather than an
    enlargement would make the crop of the 4x frame overrun, which imaging.crop
    raises on -- but a stand-in that wrote nothing at all, or the wrong size,
    should fail here rather than three tests away."""
    binary, models = fake_upscaler
    source = write_png(tmp_path / "s.png", 40, 24)
    out = tmp_path / "out.png"
    imaging.upscale(source, out, binary, models)
    assert imaging.probe(out) == (160, 96, "png")


# ---------- archival and outcomes ----------

def import_dir(tmp_path) -> Path:
    """The user's input directory, where a source sits until it is archived.

    These tests care where the original IS afterwards, so it has to start
    somewhere that is not the processing tree -- writing fixtures into
    tmp_path itself would make "did not move" and "moved" the same assertion.
    """
    inbox = tmp_path / "in"
    inbox.mkdir(parents=True, exist_ok=True)
    return inbox


def test_archive_moves_to_originals_on_success(tmp_path, fake_upscaler):
    source = write_png(import_dir(tmp_path) / "ok.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    ctx = context(tmp_path, fake_upscaler)
    result = execute.run_and_archive(work, ctx)
    assert result.outcome == execute.OK
    assert not source.exists()
    assert (ctx.processing_dir / "originals" / "ok.png").exists()


def test_rejected_everywhere_goes_to_error(tmp_path, fake_upscaler):
    source = write_png(import_dir(tmp_path) / "tiny.png", 200, 300, noise=True)
    work = plan.plan_photo(source, 200, 300, "png", list(sizes.DEVICES),
                           settings(tmp_path))
    ctx = context(tmp_path, fake_upscaler)
    result = execute.run_and_archive(work, ctx)
    assert result.outcome == execute.REJECTED
    assert (ctx.processing_dir / "error" / "tiny.png").exists()
    assert not source.exists()


def test_partial_failure_leaves_the_source_in_place(tmp_path, fake_upscaler, monkeypatch):
    """The data trap this row exists for: archiving on 'anything succeeded'
    would move the source out of the input directory while reporting success,
    and the missing wallpaper could never be recovered by a re-run, because the
    re-run would look in a directory the source had left."""
    source = write_png(import_dir(tmp_path) / "half.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    assert len(work.plans) == 3

    calls = {"n": 0}
    original = execute.render

    def flaky(target, image, scale, workdir):
        calls["n"] += 1
        if calls["n"] == 2:
            raise imaging.ImagingError("simulated upscaler crash")
        return original(target, image, scale, workdir)

    monkeypatch.setattr(execute, "render", flaky)
    ctx = context(tmp_path, fake_upscaler)
    result = execute.run_and_archive(work, ctx)

    assert result.outcome == execute.PARTIAL
    assert source.exists(), "a partial failure must not move the original"
    assert not (ctx.processing_dir / "originals" / "half.png").exists()
    assert len(result.failures) == 1


def test_a_partial_failure_still_renders_the_surviving_plans(tmp_path, fake_upscaler,
                                                             monkeypatch):
    """Guards the test above, which passes just as well if the first failure
    abandons the photo. Two of these three slices are perfectly renderable, and
    the source is staying put either way -- so giving up costs the user two
    wallpapers and throws away the enlargement the run has already paid for."""
    source = write_png(import_dir(tmp_path) / "half.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    left, center, right = work.plans

    calls = {"n": 0}
    original = execute.render

    def flaky(target, image, scale, workdir):
        calls["n"] += 1
        if calls["n"] == 2:
            raise imaging.ImagingError("simulated upscaler crash")
        return original(target, image, scale, workdir)

    monkeypatch.setattr(execute, "render", flaky)
    result = execute.run_and_archive(work, context(tmp_path, fake_upscaler))

    assert result.written == [left.destination, right.destination]
    assert not center.destination.exists()
    assert center.destination.name in result.failures[0]


def test_every_plan_failing_is_failed_not_partial(tmp_path, fake_upscaler, monkeypatch):
    """PARTIAL and FAILED archive identically -- not at all -- but a report
    that calls both 'partial' cannot tell a flaky slice from a photo sips will
    never read at all."""
    source = write_png(import_dir(tmp_path) / "doomed.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))

    def explode(*args, **kwargs):
        raise imaging.ImagingError("sips exited 13: unrecognized format")

    monkeypatch.setattr(execute, "render", explode)
    result = execute.run_and_archive(work, context(tmp_path, fake_upscaler))

    assert result.outcome == execute.FAILED
    assert result.written == [] and len(result.failures) == 3
    assert source.exists()


def test_a_routing_bug_aborts_rather_than_counting_as_a_failure(tmp_path, fake_upscaler,
                                                                monkeypatch):
    """_upscale_one_plan's precondition is about this codebase, not this photo.

    run_photo will not route a cropless plan there, so the only way to see the
    refusal is to break that routing -- which is what the stub stands in for.
    Tallied as "this photo failed", a logic error would become a silently
    skipped photo repeated across a twenty-four-hour run, with nothing louder
    to show for it than a line in a report read the next morning.
    """
    source = write_png(import_dir(tmp_path) / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    monkeypatch.setattr(execute, "UPSCALE_PIXEL_CAP", 1)

    def routed_wrong(target, work, ctx, workdir):
        raise ValueError("_upscale_one_plan requires a crop plan; a cropless "
                         "region is the whole frame and belongs on the "
                         "whole-frame path")

    monkeypatch.setattr(execute, "_upscale_one_plan", routed_wrong)
    ctx = context(tmp_path, fake_upscaler)
    ctx.log = lambda message: None

    with pytest.raises(ValueError, match="whole-frame path"):
        execute.run_and_archive(work, ctx)

    assert source.exists(), "an aborted photo keeps its original too"


def test_a_plan_neither_rendered_nor_reported_is_not_archived(tmp_path, fake_upscaler,
                                                              monkeypatch):
    """Archival is decided from run_photo's accounting, so the accounting is
    checked rather than trusted. A plan that quietly went missing would
    otherwise archive the original while its wallpaper does not exist, which is
    the one arrangement no re-run can repair."""
    source = write_png(import_dir(tmp_path) / "lost.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], settings(tmp_path))

    monkeypatch.setattr(execute, "run_photo", lambda work, ctx, failures=None: [])
    result = execute.run_and_archive(work, context(tmp_path, fake_upscaler))

    assert result.outcome == execute.FAILED
    assert result.failures and source.exists()


def test_archive_never_overwrites(tmp_path, fake_upscaler):
    """The only path that could destroy a user's sole copy of an original."""
    originals = (tmp_path / "processing" / "originals")
    originals.mkdir(parents=True)
    (originals / "IMG_0042.jpg").write_bytes(b"the first import's original")

    source = tmp_path / "in" / "IMG_0042.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    png = write_png(tmp_path / "seed.png", 2000, 3000)
    imaging.resize_and_encode(png, 2000, 3000, "jpeg", 90, source, resize=False)

    ctx = context(tmp_path, fake_upscaler)
    renamed = execute.archive(source, originals, log=ctx.log)
    assert renamed.name == "IMG_0042-2.jpg"
    assert (originals / "IMG_0042.jpg").read_bytes() == b"the first import's original"


def test_archive_keeps_counting_past_the_first_suffix(tmp_path):
    """A third import of the same name must not overwrite the second."""
    originals = tmp_path / "processing" / "originals"
    originals.mkdir(parents=True)
    (originals / "IMG_0042.jpg").write_bytes(b"first")
    (originals / "IMG_0042-2.jpg").write_bytes(b"second")

    source = tmp_path / "in" / "IMG_0042.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"third")

    renamed = execute.archive(source, originals, log=lambda message: None)
    assert renamed.name == "IMG_0042-3.jpg"
    assert (originals / "IMG_0042.jpg").read_bytes() == b"first"
    assert (originals / "IMG_0042-2.jpg").read_bytes() == b"second"


def test_archive_will_not_take_a_name_a_broken_symlink_holds(tmp_path):
    """`exists()` follows symlinks, so a dangling one reads as a free name and
    the move replaces it. Nothing here creates symlinks -- the user's input
    directory does, and the check is cheaper than the argument about whether
    it can happen."""
    originals = tmp_path / "processing" / "originals"
    originals.mkdir(parents=True)
    (originals / "IMG_0042.jpg").symlink_to(tmp_path / "gone.jpg")

    source = tmp_path / "in" / "IMG_0042.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"the real thing")

    renamed = execute.archive(source, originals, log=lambda message: None)
    assert renamed.name == "IMG_0042-2.jpg"
    assert (originals / "IMG_0042.jpg").is_symlink()


def test_archive_reports_the_rename(tmp_path):
    """A renamed original is the one archival event the user has to know
    about: it means two photos in the import shared a name, and nothing
    downstream records which one this file was."""
    originals = tmp_path / "processing" / "originals"
    originals.mkdir(parents=True)
    (originals / "a.png").write_bytes(b"earlier")
    source = write_png(import_dir(tmp_path) / "a.png", 8, 8)

    messages = []
    execute.archive(source, originals, log=messages.append)

    assert messages == ["  a.png: already in originals/, archived as a-2.png"]


def test_an_unrenamed_archive_says_nothing(tmp_path):
    """Guards the test above: a line printed for every archived photo would
    satisfy it while telling the user nothing."""
    source = write_png(import_dir(tmp_path) / "b.png", 8, 8)
    messages = []
    execute.archive(source, tmp_path / "processing" / "originals",
                    log=messages.append)
    assert messages == []


def test_existing_output_is_skipped(tmp_path, fake_upscaler):
    source = write_png(import_dir(tmp_path) / "done.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    target.destination.parent.mkdir(parents=True, exist_ok=True)
    target.destination.write_bytes(b"already here")

    ctx = context(tmp_path, fake_upscaler)
    result = execute.run_and_archive(work, ctx)
    assert result.outcome == execute.ALREADY_DONE
    assert target.destination.read_bytes() == b"already here"
    assert (ctx.processing_dir / "originals" / "done.png").exists()
    assert result.skipped == [target.destination]


def test_a_fully_skipped_photo_never_starts_the_upscaler(tmp_path, fake_upscaler,
                                                         monkeypatch):
    """The re-run case, and the reason skipping is decided before the photo is
    handed over: the enlargement costs minutes per photo, and a plan that is
    not going to be written does not need one."""
    source = write_png(import_dir(tmp_path) / "again.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    for target in work.plans:
        target.destination.parent.mkdir(parents=True, exist_ok=True)
        target.destination.write_bytes(b"from the last run")

    calls = spy_on(monkeypatch, "upscale")
    result = execute.run_and_archive(work, context(tmp_path, fake_upscaler))

    assert calls == [], "an already-finished photo must not be enlarged again"
    assert result.outcome == execute.ALREADY_DONE
    assert len(result.skipped) == 3


def test_a_half_done_photo_only_renders_what_is_missing(tmp_path, fake_upscaler):
    source = write_png(import_dir(tmp_path) / "resumed.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    left, center, right = work.plans
    left.destination.parent.mkdir(parents=True, exist_ok=True)
    left.destination.write_bytes(b"from the last run")

    result = execute.run_and_archive(work, context(tmp_path, fake_upscaler))

    assert result.outcome == execute.OK
    assert result.skipped == [left.destination]
    assert result.written == [center.destination, right.destination]
    assert left.destination.read_bytes() == b"from the last run"


def test_overwrite_re_renders(tmp_path, fake_upscaler):
    source = write_png(import_dir(tmp_path) / "again.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    target = work.plans[0]
    target.destination.parent.mkdir(parents=True, exist_ok=True)
    target.destination.write_bytes(b"stale")

    ctx = context(tmp_path, fake_upscaler)
    ctx.overwrite = True
    result = execute.run_and_archive(work, ctx)
    assert result.outcome == execute.OK
    assert target.destination.read_bytes() != b"stale"


def test_an_archive_that_cannot_move_reports_instead_of_ending_the_run(tmp_path,
                                                                      fake_upscaler):
    """The outputs are written and good; only the move failed. Reporting it
    leaves the source where a re-run will find it -- and where that re-run
    skips the finished outputs and tries the move again -- rather than ending a
    twenty-four-hour import on one photo's permissions."""
    source = write_png(import_dir(tmp_path) / "stuck.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    ctx = context(tmp_path, fake_upscaler)
    ctx.processing_dir.mkdir(parents=True, exist_ok=True)
    (ctx.processing_dir / "originals").write_bytes(b"not a directory")

    result = execute.run_and_archive(work, ctx)

    assert result.outcome == execute.PARTIAL
    assert result.archived_to is None
    assert source.exists()
    assert work.plans[0].destination.exists(), "the outputs were fine"
    assert "could not move stuck.png to originals/" in result.failures[0], \
        "the destination directory is what says whether this photo was rejected"


def test_a_rejected_photo_that_cannot_reach_error_is_partial_not_failed(
        tmp_path, fake_upscaler):
    """FAILED means sips will never read this photo. That is not what happened.

    A photo too small for every device has no outputs by definition, so
    `_unfinished`'s `written or skipped` test read it as a total failure --
    and since the outcome could no longer be REJECTED either, the rejection
    vanished from the outcome entirely. It was measured, classified and
    turned down; the only thing that went wrong was the move into error/,
    and a re-run retries exactly that.
    """
    source = write_png(import_dir(tmp_path) / "tiny.png", 100, 100)
    work = plan.plan_photo(source, 100, 100, "png", list(sizes.DEVICES),
                           settings(tmp_path))
    assert work.rejected_everywhere and not work.plans
    ctx = context(tmp_path, fake_upscaler)
    ctx.processing_dir.mkdir(parents=True, exist_ok=True)
    (ctx.processing_dir / "error").write_bytes(b"not a directory")

    result = execute.run_and_archive(work, ctx)

    assert result.outcome == execute.PARTIAL
    assert result.archived_to is None
    assert source.exists()
    assert "could not move tiny.png to error/" in result.failures[0]


def test_a_photo_that_produced_nothing_at_all_is_still_failed(tmp_path,
                                                              fake_upscaler):
    """Guards the guard: `rejected` must not have turned FAILED into dead code.

    A photo with plans, all of which failed, produced nothing and was not
    turned down -- and FAILED is the honest label, because it is the one that
    says a second run is unlikely to help.
    """
    source = write_png(import_dir(tmp_path) / "broken.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE],
                           settings(tmp_path))
    assert not work.rejected_everywhere
    source.write_bytes(b"not an image any more")

    result = execute.run_and_archive(work, context(tmp_path, fake_upscaler))

    assert result.outcome == execute.FAILED
    assert result.written == [] and result.skipped == []


def test_batch_runs_cheapest_first(tmp_path, fake_upscaler):
    """974 of the corpus's 3441 outputs need no upscaler; handing those over
    first maximizes what a Ctrl-C leaves behind."""
    cheap_src = write_png(import_dir(tmp_path) / "cheap.png", 2000, 3000)
    dear_src = write_png(import_dir(tmp_path) / "dear.png", 1440, 720)
    opts = settings(tmp_path)
    works = [
        plan.plan_photo(dear_src, 1440, 720, "png", [sizes.PHONE], opts),
        plan.plan_photo(cheap_src, 2000, 3000, "png", [sizes.PHONE], opts),
    ]
    order = [w.source.name for w in execute.cheapest_first(works)]
    assert order == ["cheap.png", "dear.png"]


def test_cheapest_first_orders_the_upscaling_photos_by_cost(tmp_path):
    """Cheap-first is not only about the two groups: an interrupted run gets
    further into the second group as well when the smallest frames go first.
    The photo needing no upscaler here is LARGER than both of the others, so
    ordering on source pixels alone would put it last."""
    opts = settings(tmp_path)
    works = [
        plan.plan_photo(tmp_path / "big_frame.png", 2000, 1000, "png",
                        [sizes.PHONE], opts),
        plan.plan_photo(tmp_path / "cheap.png", 4000, 6000, "png",
                        [sizes.PHONE], opts),
        plan.plan_photo(tmp_path / "small_frame.png", 1440, 720, "png",
                        [sizes.PHONE], opts),
    ]
    assert [w.needs_upscale for w in works] == [True, False, True]
    assert [w.source.stem for w in execute.cheapest_first(works)] == [
        "cheap", "small_frame", "big_frame"]


def test_a_failed_enlargement_is_not_held_while_the_next_plan_runs(tmp_path,
                                                                  fake_upscaler,
                                                                  monkeypatch):
    """Collecting failures changed who sweeps the workdir, and when.

    A photo that aborted on its first bad plan had its workdir swept
    immediately; one that carries on does not, so a model run that exits
    non-zero AFTER writing part of its output leaves that part behind for as
    long as the photo lasts. Two regions held at once is most of what the pixel
    cap was imposed to save, and the fallback path is exactly where the cap has
    already said memory is tight.
    """
    source = write_png(import_dir(tmp_path) / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    monkeypatch.setattr(execute, "UPSCALE_PIXEL_CAP", 1)

    real_upscale = imaging.upscale
    calls = {"n": 0}

    def flaky_upscale(source_png, out_png, binary, models_dir, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            Path(out_png).write_bytes(b"half an enlargement")
            raise imaging.ImagingError("upscayl-bin exited 1: out of memory")
        return real_upscale(source_png, out_png, binary, models_dir, *args, **kwargs)

    alive = []
    real_render = execute.render

    def watching_render(target, image, scale, workdir):
        alive.append(sorted(p.name for p in Path(workdir).iterdir() if p.is_file()))
        return real_render(target, image, scale, workdir)

    monkeypatch.setattr(imaging, "upscale", flaky_upscale)
    monkeypatch.setattr(execute, "render", watching_render)
    ctx = context(tmp_path, fake_upscaler)
    ctx.log = lambda message: None

    result = execute.run_and_archive(work, ctx)

    assert len(result.failures) == 1 and len(result.written) == 2
    assert all(len(held) == 1 for held in alive), \
        f"the failed plan's enlargement was still held: {alive}"
    root = Path(ctx.workroot)
    leftovers = [p for p in root.rglob("*") if p.is_file()] if root.exists() else []
    assert leftovers == [], f"left behind: {leftovers}"
    assert source.exists()


def refuse_to_rename(monkeypatch):
    """Make every rename fail with EXDEV, as one does across a volume boundary.

    The archive is the one move in the tool that can cross one: the input
    directory is wherever the user's photos landed, and the processing tree is
    wherever they configured it.

    Patched at `os.rename` rather than `Path.rename`, because that is the one
    seam both implementations of the move go through -- pathlib calls it, and
    so does shutil.move before falling back to copy-then-unlink. Patching the
    pathlib method would leave shutil renaming happily, and the tests below
    would then pass against the very code they exist to rule out. `os.replace`
    is deliberately left alone: staging a copy and renaming it in is the
    behaviour being tested.
    """
    def across_a_volume(src, dst, **kwargs):
        raise OSError(18, "Cross-device link", str(src), None, str(dst))

    monkeypatch.setattr("os.rename", across_a_volume)


def test_a_cross_volume_archive_still_moves_the_original(tmp_path, monkeypatch):
    source = write_png(import_dir(tmp_path) / "IMG_0042.png", 16, 16)
    kept = source.read_bytes()
    originals = tmp_path / "processing" / "originals"

    refuse_to_rename(monkeypatch)
    archived = execute.archive(source, originals, log=lambda message: None)

    assert archived.read_bytes() == kept
    assert not source.exists()
    assert [p.name for p in originals.iterdir()] == ["IMG_0042.png"], \
        "the staged copy must not outlive the move"


def test_a_failed_cross_volume_copy_leaves_no_file_at_the_archive_name(tmp_path,
                                                                      monkeypatch):
    """The finding this stages for. Copy-then-unlink across a volume plants a
    TRUNCATED file at the canonical name when the copy dies -- a full disk is
    the realistic way, with 28 GB free against 114 GB of frames. The good file
    is then archived as IMG_0042-2.png by the re-run, and a user pruning what
    look like duplicates by keeping the unsuffixed name loses the photo."""
    source = write_png(import_dir(tmp_path) / "IMG_0042.png", 16, 16)
    kept = source.read_bytes()
    originals = tmp_path / "processing" / "originals"

    refuse_to_rename(monkeypatch)

    def dies_mid_copy(src, dst, **kwargs):
        Path(dst).write_bytes(Path(src).read_bytes()[:32])
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(execute.shutil, "copy2", dies_mid_copy)

    with pytest.raises(OSError):
        execute.archive(source, originals, log=lambda message: None)

    assert source.read_bytes() == kept, "the original is the one thing that must survive"
    assert [p.name for p in originals.iterdir()] == [], \
        "neither the archive name nor a .partial may be left behind"


def test_a_failed_archive_copy_does_not_touch_an_earlier_original(tmp_path,
                                                                  monkeypatch):
    """The name collision and the failed copy at once, which is where a
    delete-on-failure would do the damage it was added to prevent."""
    originals = tmp_path / "processing" / "originals"
    originals.mkdir(parents=True)
    (originals / "IMG_0042.png").write_bytes(b"the first import's original")
    source = write_png(import_dir(tmp_path) / "IMG_0042.png", 16, 16)

    refuse_to_rename(monkeypatch)

    def dies_mid_copy(src, dst, **kwargs):
        Path(dst).write_bytes(b"half")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(execute.shutil, "copy2", dies_mid_copy)

    with pytest.raises(OSError):
        execute.archive(source, originals, log=lambda message: None)

    assert (originals / "IMG_0042.png").read_bytes() == b"the first import's original"
    assert [p.name for p in originals.iterdir()] == ["IMG_0042.png"]
    assert source.exists()


def test_a_second_photo_of_the_same_name_is_archived_beside_the_first(tmp_path,
                                                                     fake_upscaler):
    """The never-overwrite promise at the level a user meets it: not a helper
    called with two paths, but a whole photo run through the tool while an
    earlier import of the same name is already in originals/."""
    ctx = context(tmp_path, fake_upscaler)
    originals = ctx.processing_dir / "originals"
    originals.mkdir(parents=True)
    (originals / "IMG_0042.png").write_bytes(b"the first import's original")

    source = write_png(import_dir(tmp_path) / "IMG_0042.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], settings(tmp_path))

    messages = []
    ctx.log = messages.append
    result = execute.run_and_archive(work, ctx)

    assert result.outcome == execute.OK
    assert result.archived_to == originals / "IMG_0042-2.png"
    assert result.archived_to.exists() and not source.exists()
    assert (originals / "IMG_0042.png").read_bytes() == b"the first import's original"
    assert messages == [
        "  IMG_0042.png: already in originals/, archived as IMG_0042-2.png"]


def test_a_plan_in_both_lists_is_not_reported_as_a_negative(tmp_path, fake_upscaler,
                                                            monkeypatch):
    """The count can go either way, and `if unaccounted` is truthy for -1.

    A plan that is rendered AND reported failed is reachable: the per-plan
    cleanup runs in a `finally` after the destination has been appended, so an
    unlink that raises lands in the same tally. The outputs are on disk and
    good; what is wrong is the bookkeeping, and saying "-3 of 3 plans were
    neither rendered nor reported" describes nothing that happened.
    """
    source = write_png(import_dir(tmp_path) / "s.png", 1440, 720)
    work = plan.plan_photo(source, 1440, 720, "png", [sizes.PHONE], settings(tmp_path))
    monkeypatch.setattr(execute, "UPSCALE_PIXEL_CAP", 1)

    real_unlink = Path.unlink

    def busy(self, missing_ok=False):
        # Only the enlargement, and only once it is really there: imaging._run
        # unlinks the same path BEFORE the model writes it, and refusing that
        # would fail the plan instead of its cleanup.
        if self.name.startswith("up_") and self.exists():
            raise OSError(16, "Resource busy")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", busy)
    ctx = context(tmp_path, fake_upscaler)
    ctx.log = lambda message: None

    result = execute.run_and_archive(work, ctx)

    assert len(result.written) == 3 and all(p.exists() for p in result.written)
    assert result.outcome == execute.PARTIAL
    assert source.exists()
    assert any("both rendered and reported" in failure for failure in result.failures)
    assert not any("neither rendered nor reported" in failure
                   for failure in result.failures), result.failures
    assert not any("-" in failure.split(" of ")[0] for failure in result.failures
                   if " of " in failure), result.failures


def test_a_destination_reported_written_is_measured_on_disk(tmp_path, fake_upscaler,
                                                            monkeypatch):
    """The accounting check asks run_photo whether it kept count. This asks the
    filesystem. A destination comes back as written only after render measures
    it and renames it into place -- but that is render's property, not this
    function's, and this function is the one that moves the original."""
    source = write_png(import_dir(tmp_path) / "phantom.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], settings(tmp_path))

    monkeypatch.setattr(execute, "run_photo",
                        lambda work, ctx, failures=None: [work.plans[0].destination])
    ctx = context(tmp_path, fake_upscaler)
    result = execute.run_and_archive(work, ctx)

    assert result.outcome == execute.PARTIAL
    assert result.failures == [
        f"{work.plans[0].destination.name} was reported written but is not there"]
    assert source.exists()
    assert not (ctx.processing_dir / "originals" / "phantom.png").exists()


def test_a_photo_nothing_was_asked_of_is_not_archived(tmp_path, fake_upscaler):
    """No plans and no rejections either. Only an empty device list produces
    it, so this is about what the branch below would do with it: read "nothing
    to write, nothing rejected" as ALREADY_DONE and move an original for zero
    work."""
    source = write_png(import_dir(tmp_path) / "unasked.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [], settings(tmp_path))
    assert work.plans == [] and work.rejected_devices == []

    ctx = context(tmp_path, fake_upscaler)
    result = execute.run_and_archive(work, ctx)

    assert result.outcome == execute.FAILED
    assert result.failures and source.exists()
    assert not (ctx.processing_dir / "originals").exists()


def test_an_interrupt_as_the_workdir_is_created_leaves_nothing_behind(
        tmp_path, fake_upscaler, monkeypatch):
    """The signal arrives as `mkdir` returns.

    Created above the `try`, the workdir has no `finally` to remove it: the
    directory outlives the run, and the CLI's `workroot.rmdir()` then fails
    ENOTEMPTY and swallows it -- which turns that cleanup from a rule into a
    comment with a window in it. Microseconds wide, and the residue is one
    empty directory the next run reuses, so this is about the guarantee rather
    than the debris.
    """
    source = write_png(tmp_path / "lichen.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE],
                           settings(tmp_path))
    ctx = context(tmp_path, fake_upscaler)
    workdir = Path(ctx.workroot) / source.stem

    real_mkdir = Path.mkdir

    def mkdir_then_interrupt(self, *args, **kwargs):
        real_mkdir(self, *args, **kwargs)
        if self == workdir:
            raise KeyboardInterrupt

    monkeypatch.setattr(Path, "mkdir", mkdir_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        execute.run_photo(work, ctx)

    monkeypatch.undo()
    assert not workdir.exists(), (
        "the workdir outlived the interrupt that created it")
