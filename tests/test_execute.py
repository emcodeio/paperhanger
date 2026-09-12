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
    """Replace the encoder with one that just writes the file it was given.

    Used by the tests about render's own plumbing -- staging, mkdir, cleanup --
    where a real sips run on a 6 Mpx fixture would add seconds and prove
    nothing. It still WRITES, so a render that forgot to create the
    destination directory fails here exactly as it would in production.
    """
    def encode(source, out_width, out_height, fmt, quality, out_path, resize):
        if seen is not None:
            seen.append((Path(source), out_width, out_height, fmt, quality,
                         Path(out_path), resize))
        Path(out_path).write_bytes(b"an encoded wallpaper")

    monkeypatch.setattr(imaging, "resize_and_encode", encode)


def stub_crop(monkeypatch, seen):
    def crop(source, rect, out_path):
        seen.append((Path(source), rect, Path(out_path)))
        Path(out_path).write_bytes(b"a cropped region")

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

def test_render_passes_the_plans_quality_to_the_encoder(tmp_path, monkeypatch):
    """OutputPlan.quality is asserted nowhere else in the suite: replacing
    `quality=opts.quality` with `quality=None` in the planner leaves every
    other test green, and heic's 80 is one of the three values constraint 12
    fixes by measurement. render is where quality reaches an encoder.

    The file is probed too, because staging through a `.partial` suffix is
    exactly the shape that made sips silently keep the wrong format before
    (imaging fact 7): it warns `Output file suffix should be heic` and exits 0
    either way.
    """
    source = write_png(tmp_path / "lichen.png", 2000, 3000)
    work = plan.plan_photo(source, 2000, 3000, "heic", [sizes.PHONE],
                           settings(tmp_path, "heic"))
    target = work.plans[0]
    assert target.quality == 80

    real = imaging.resize_and_encode
    seen = []

    def spy(source, out_width, out_height, fmt, quality, out_path, resize):
        seen.append((fmt, quality))
        real(source, out_width, out_height, fmt, quality, out_path, resize=resize)

    monkeypatch.setattr(imaging, "resize_and_encode", spy)
    execute.render(target, source, scale=1, workdir=tmp_path / "work")

    assert seen == [("heic", 80)]
    assert imaging.probe(target.destination) == (2000, 3000, "heic")


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


def test_crop_intermediates_are_png_and_one_per_plan(tmp_path, monkeypatch):
    """Three slices are cut from one frame into one workdir. imaging.crop
    always writes PNG bytes whatever the name says, so a name that is not .png
    would lie about its own contents; and named by the source alone the three
    would collide, which matters the moment anything holds two at once."""
    source = write_png(tmp_path / "ocean.png", 4000, 3000)
    work = plan.plan_photo(source, 4000, 3000, "png", [sizes.PHONE], settings(tmp_path))
    crops = []
    stub_crop(monkeypatch, crops)
    stub_encoder(monkeypatch)

    for target in work.plans:
        execute.render(target, source, scale=1, workdir=tmp_path / "work")

    paths = [out_path for _, _, out_path in crops]
    assert len(paths) == 3
    assert len(set(paths)) == 3, f"intermediates collide: {paths}"
    assert all(p.suffix == ".png" for p in paths), paths
