"""Running plans. The only module that moves or writes user files.

Two rules that are easy to get wrong and expensive to get wrong:

  * A plan's intermediates are deleted as soon as its output is in place, not
    at process exit. Retaining all 756 enlarged frames until exit would need
    about 114 GB against 28 GB free -- the run would fill the volume a fifth of
    the way in and then spend twenty more hours recording failures. The crop
    intermediates are no longer small either: imaging.crop always emits PNG, so
    a 3840x2550 slice is about 14 MB rather than a compressed megabyte.
  * Outputs are staged as <name>.partial IN THE DESTINATION DIRECTORY and
    renamed. Staging beside the destination makes the rename atomic whatever
    volume TMPDIR is on, and an interrupted run never leaves a truncated file
    where the sorter will see it.
"""

import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from . import imaging

PARTIAL_SUFFIX = ".partial"

# 300 Mpx of 4x output. Above this a whole-frame enlargement strains memory, so
# the photo falls back to enlarging each plan's region on its own. On the
# author's corpus 92 of 756 whole-frame jobs exceed it; the largest would
# otherwise be 622 Mpx.
#
# Being over the cap is necessary but NOT sufficient -- see run_photo. A plan
# with no crop rect covers the whole photo, so enlarging "just its region"
# enlarges the whole frame: the fallback's peak is then identical to the
# whole-frame path's, for N model runs instead of one. Measured on 1280x800,
# which is a desktop whole-image plan plus three phone slices: both strategies
# peak at 16,384,000 px of 4x output, the fallback taking four runs to get
# there and the whole frame one. Sweeping 400-20000 on both axes, 2878 of 7413
# over-cap shapes have a cropless upscaling plan.
UPSCALE_PIXEL_CAP = 300_000_000


def _refuse_to_enlarge(source, out_width: int, out_height: int) -> None:
    """Global constraint 1, enforced rather than documented.

    No image is ever enlarged except by the ML model, and `sips` is the one
    tool here that would happily do it -- silently, at exit 0, producing a
    plausible file of exactly the requested size. Nothing downstream can tell
    such a wallpaper from a real one.

    The check is the constraint restated: whatever is about to be resampled
    must be at least as large as the size asked for, on both axes. Equality is
    allowed, because band 4 cut from the 4x frame lands exactly on it. Every
    legitimate caller passes: band 1 measures at or above the ideal, band 3
    arrives via the 4x frame or an already-enlarged region, and band 2 does not
    resample at all, so this never runs for it.

    Costs one probe per resizing render -- about 52 seconds across the whole
    894-image corpus, against a class of failure this codebase has now found
    seven distinct instances of.
    """
    measured = imaging.probe(source)
    if measured is None:
        raise imaging.ImagingError(f"cannot resize {source}: not a readable image")
    width, height, _ = measured
    if width < out_width or height < out_height:
        raise imaging.ImagingError(
            f"refusing to enlarge {width}x{height} to {out_width}x{out_height}: "
            f"only the ML model may enlarge ({source})"
        )


def sweep_partials(processing_dir) -> int:
    """Remove stray staging files from an interrupted earlier run."""
    processing_dir = Path(processing_dir)
    if not processing_dir.is_dir():
        return 0
    removed = 0
    for stray in processing_dir.rglob(f"*{PARTIAL_SUFFIX}"):
        stray.unlink(missing_ok=True)
        removed += 1
    return removed


def _verify_dimensions(image, target) -> None:
    """Assert that what was encoded is the file the plan describes.

    `imaging` already asserts that each operation wrote SOMETHING -- a zero
    exit does not mean a file appeared. This is the other half: a file did
    appear, and it is the wrong picture. Seven distinct ways sips returns
    plausible wrong output at exit 0 have now been measured in this project,
    and render is the function that writes every user file, so it checks its
    own result rather than trusting the eighth to announce itself.

    It also covers a hole the no-enlargement guard cannot, because the guard
    only runs when something is being resampled: a band 4 plan handed its own
    original at scale 1 crops the slice, resamples nothing, and writes
    `s_left_phone_1920x2880_4x.png` at 480x720 -- measured. No enlargement, so
    constraint 1 is intact, but the name asserts dimensions the file does not
    have, and nothing downstream reads pixels to find out.
    """
    measured = imaging.probe(image)
    if measured is None:
        raise imaging.ImagingError(
            f"{target.destination.name}: encoded file is not a readable image"
        )
    width, height, _ = measured
    if (width, height) != (target.out_width, target.out_height):
        raise imaging.ImagingError(
            f"{target.destination.name} would be {width}x{height}, not the "
            f"{target.out_width}x{target.out_height} its plan and its own name say"
        )


def render(target, source_image, scale: int, workdir) -> Path:
    """Produce `target.destination` from `source_image`.

    `scale` is 1 when source_image is the original and 4 when it is the
    enlarged whole frame; the crop rect is multiplied to match.

    A band 3 or 4 plan must never be rendered at scale 1 from its original.
    Those bands are rendered from the 4x frame at scale 4, or -- when the pixel
    cap forces a per-plan upscale -- from an already-enlarged region with
    `crop` cleared, which is a whole-image plan by then.

    Two checks enforce that between them, rather than trusting the caller, and
    neither subsumes the other:

      * `_refuse_to_enlarge` measures the INPUT before any resample, and
        refuses to ask sips to enlarge -- global constraint 1. It runs only
        when something is being resampled, which is the only time sips could.
      * `_verify_dimensions` measures the OUTPUT before it is published, and
        refuses to hand over a file that is not the size its plan and its own
        filename claim. That covers the band 4 case the guard cannot see,
        where nothing is resampled and the slice is simply written at 1/4 the
        planned size.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    target.destination.parent.mkdir(parents=True, exist_ok=True)

    staged = target.destination.with_name(target.destination.name + PARTIAL_SUFFIX)
    intermediates = []
    try:
        current = Path(source_image)

        if target.crop is not None:
            rect = target.crop if scale == 1 else target.crop.scaled(scale)
            # .png because imaging.crop always writes PNG bytes, and named per
            # plan because one photo's three slices share this workdir.
            cropped = workdir / f"crop_{target.destination.stem}.png"
            # Registered BEFORE the call: a crop that writes its output and
            # then fails would otherwise leave that file behind, and unlinking
            # a path nothing wrote costs nothing.
            intermediates.append(cropped)
            imaging.crop(current, rect, cropped)
            current = cropped

        # A band 2 or 4 plan needs no resize OF THE SOURCE, but a slice of the
        # 4x frame is 4x the planned size and must come back down.
        resize = target.needs_resize or scale != 1
        if resize:
            _refuse_to_enlarge(current, target.out_width, target.out_height)

        # Never fused with the crop above: sips applies a resample against the
        # PRE-crop dimensions and silently returns the wrong size.
        imaging.resize_and_encode(
            current, target.out_width, target.out_height,
            target.fmt, target.quality, staged, resize=resize,
        )

        # Checked on the staged copy, BEFORE the rename. The rename is an
        # atomic metadata operation within one directory, so the staged file
        # and the destination are the same bytes and the same inode -- this
        # measures exactly what the user would get. Doing it first means a
        # wrong result never reaches the destination at all: no window in
        # which the sorter could see it, and, when an earlier run's output is
        # already there, that file is still standing afterwards rather than
        # replaced by a file this run then deletes.
        _verify_dimensions(staged, target)

        staged.replace(target.destination)
        return target.destination
    except BaseException:
        # Only the staged file. `staged.replace` is atomic and the last
        # fallible statement above, so a failure never half-wrote the
        # destination -- there is nothing of this run's to clean up there.
        # A file already at that path came from an EARLIER run, and the name
        # encodes stem, position, device, both dimensions and the factor, so
        # it is this same plan's output from this same source: correct, not
        # misleading. Deleting it would turn a transient sips failure into the
        # loss of a wallpaper the user already had.
        staged.unlink(missing_ok=True)
        raise
    finally:
        for leftover in intermediates:
            leftover.unlink(missing_ok=True)


@dataclass
class Context:
    processing_dir: Path
    workroot: Path
    upscayl: Path
    models_dir: Path
    log: object = print


def _upscale_whole_frame(work, ctx, workdir) -> Path:
    """Normalize to sRGB PNG, then enlarge the whole frame once.

    The normalize is not conditional on the source format. upscayl-bin emits
    PNG with no ICC chunk, so an unconverted wide-gamut source comes back with
    sRGB asserted over numbers that were never in sRGB -- about 15 dB worse,
    an order of magnitude larger than the spread the upscaler was chosen on.
    """
    normalized = workdir / "normalized.png"
    imaging.normalize_to_srgb_png(work.source, normalized)
    enlarged = workdir / "frame_4x.png"
    imaging.upscale(normalized, enlarged, ctx.upscayl, ctx.models_dir)
    # Dropped as soon as the model has read it: the normalized copy is as large
    # as the original PNG, and the frame it produced is sixteen times that.
    normalized.unlink(missing_ok=True)
    return enlarged


def _upscale_one_plan(target, work, ctx, workdir) -> Path:
    """The fallback: enlarge just this plan's region.

    Cropping BEFORE the model rather than after is the whole point of the
    fallback -- it is what keeps a photo over the cap from ever holding its
    whole 4x frame, and it is why run_photo only falls back when every plan
    that needs the model HAS a crop to be bounded by.

    The crop runs before the normalize because it is cheaper to convert a third
    of an image than all of it. That ordering is safe only because sips carries
    the source's profile into the crop untouched, which is measured rather than
    assumed: strip the profile from the crop and sips reports the result as
    sRGB, `--matchTo` becomes a no-op, and the wide-gamut numbers survive into
    a file that claims to be sRGB -- 144 out of 255 on a single channel, at
    exit 0, with nothing to see in any dimension or file-exists check. Both
    orderings are pinned byte-identical in test_imaging.

    The `crop is None` branch below is unreachable from run_photo, which now
    keeps such photos on the whole-frame path. It stays because this function
    means "enlarge just this plan's region" and a whole-image plan's region is
    a legitimate answer to that; the cap policy is what excludes it, and policy
    is the thing most likely to move.

    Every intermediate is named after the plan, because one photo's three
    slices share this workdir. The caller drops each enlargement before it asks
    for the next, so a shared name would work today; it would also mean that
    the first time it stops holding, one plan silently renders another plan's
    region at exactly the right size, and nothing would raise.
    """
    stem = target.destination.stem
    normalized = workdir / f"norm_{stem}.png"
    if target.crop is not None:
        # .png because imaging.crop always writes PNG bytes whatever the name.
        cropped = workdir / f"pre_{stem}.png"
        try:
            imaging.crop(work.source, target.crop, cropped)
            imaging.normalize_to_srgb_png(cropped, normalized)
        finally:
            cropped.unlink(missing_ok=True)
    else:
        imaging.normalize_to_srgb_png(work.source, normalized)

    enlarged = workdir / f"up_{stem}.png"
    try:
        imaging.upscale(normalized, enlarged, ctx.upscayl, ctx.models_dir)
    finally:
        normalized.unlink(missing_ok=True)
    return enlarged


def run_photo(work, ctx) -> list:
    """Render every plan of one photo. Returns the destinations written.

    The upscaler runs ONCE for the whole frame when anything needs it, and
    every slice is cut from that result. Enlarging each slice separately would
    re-enlarge the same pixels two or three times, because the three slices are
    overlapping windows on one photo -- and it would duplicate work across
    devices, since a photo cropped for desktop usually needs its whole frame
    for the phone pass anyway. Measured on the author's 894-image corpus: 2467
    upscaler runs and 37.2 hours become 756 runs and 24.3 hours.

    The pixel cap alone does not decide that. Falling back is worth doing only
    when it actually bounds the enlargement, and it bounds nothing unless every
    plan that needs the model has a crop rect to be bounded BY. A cropless
    plan's region is the whole photo, so the fallback asks the model for
    exactly the frame the cap declined -- same peak, one run per plan instead
    of one per photo. Over-cap photos with a cropless upscaling plan therefore
    keep the whole-frame path: there is no cheaper decomposition to fall back
    to, and attempting the frame is the only thing that can produce that output
    at all.

    When the fallback does apply it is reported rather than done quietly: it is
    the one thing that makes a photo's timing unlike every other photo's, and a
    run whose strategy changed without saying so is a run nobody can account
    for afterwards.
    """
    workdir = Path(ctx.workroot) / work.source.stem
    workdir.mkdir(parents=True, exist_ok=True)
    written = []
    frame = None
    over_cap = work.upscale_output_pixels > UPSCALE_PIXEL_CAP
    fall_back = over_cap and all(
        p.crop is not None for p in work.plans if p.needs_upscale)

    try:
        if work.needs_upscale and not fall_back:
            frame = _upscale_whole_frame(work, ctx, workdir)
        elif work.needs_upscale:
            # One decimal place: 300,560,000 px against this cap is over it,
            # and integer division prints that as "300 Mpx exceeds the 300 Mpx
            # cap". Reachable at 1300x14450, and this is the one line a
            # twenty-four-hour run gives the user about the change.
            ctx.log(
                f"  {work.source.name}: "
                f"{work.upscale_output_pixels / 1_000_000:.1f} Mpx "
                f"exceeds the {UPSCALE_PIXEL_CAP / 1_000_000:.1f} Mpx cap; "
                f"falling back to per-plan upscaling"
            )

        for target in work.plans:
            if not target.needs_upscale:
                # Bands 1 and 2 keep their real pixels: the original, never the
                # frame. Taken from the frame a band 2 plan would be enlarged
                # and reduced back to the same size, and would still measure
                # correct at every check render makes.
                written.append(render(target, work.source, scale=1, workdir=workdir))
                continue
            if frame is not None:
                written.append(render(target, frame, scale=4, workdir=workdir))
                continue
            enlarged = _upscale_one_plan(target, work, ctx, workdir)
            try:
                # The region is already cropped and already 4x, so render must
                # not crop again: pass a cropless view of the plan. render then
                # measures that region against the planned size, which is why
                # no enlargement guard belongs here -- the guard exists to
                # decide exactly this, from the pixels rather than the band.
                written.append(render(
                    replace(target, crop=None), enlarged, scale=1, workdir=workdir))
            finally:
                enlarged.unlink(missing_ok=True)
        return written
    finally:
        # Both, and on every path out. The frame is the largest thing the tool
        # makes -- sixteen times the pixels of the source, as PNG -- and holding
        # all 756 of a run's frames to process exit would want about 114 GB
        # against 28 GB free. rmtree covers the frame as well, but unlinking it
        # first keeps the one file that matters most from depending on the
        # sweep that follows.
        if frame is not None:
            frame.unlink(missing_ok=True)
        shutil.rmtree(workdir, ignore_errors=True)
