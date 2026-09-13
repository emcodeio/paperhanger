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

What that does NOT buy is a peak of one frame. `imaging.crop` pads the whole
image on the two --cropOffset shapes sips ignores (fact 6), so while a padded
crop runs there are two full-size copies of the 4x frame on disk at once, and
the pad fires for two of three horizontal slices and one of three vertical
ones. The peak is two frames plus one slice, not one frame -- measured, not
inferred. Spec section 7 names the fix, which is to pad each frame once
instead of once per slice; it is not implemented here.
"""

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace
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


def is_pending(target, overwrite: bool = False) -> bool:
    """Will this run render this plan? The ONE place skip-existing is decided.

    It used to be spelled out three times -- in the report's `already_done`,
    in the toolchain pre-flight's `upscaler_is_needed`, and in
    `run_and_archive` below. The first two decide what the dry-run PROMISES
    and the third decides what the run DOES, so three copies that agree today
    are a dry-run that lies tomorrow: exactly the drift the plan-then-execute
    split exists to rule out (spec section 3).

    Spec section 3 places this decision at plan time, and `plan.py` is the
    wrong home for it in the end: it is the one skip-existing question that
    has to ask the filesystem, and `plan.py` is asserted pure by
    `tests/conftest.assert_pure_module`. It lives here instead, where the
    effects already are, and everything that needs the answer asks this.
    """
    return overwrite or not target.destination.exists()


def sweep_partials(processing_dir) -> int:
    """Remove stray staging files from an interrupted earlier run.

    Never raises. It runs in the pre-flight, before any photo is touched, and
    a stray this process cannot remove -- one owned by another user, or on a
    volume gone read-only -- is not a reason to refuse a run that would
    otherwise finish. The count is of files actually removed, so a stray left
    behind is not reported as swept.
    """
    processing_dir = Path(processing_dir)
    if not processing_dir.is_dir():
        return 0
    removed = 0
    for stray in processing_dir.rglob(f"*{PARTIAL_SUFFIX}"):
        try:
            stray.unlink(missing_ok=True)
        except OSError:
            continue
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

    Two things this deliberately does not do:

      * It does not check the FORMAT, though the probe returns one. `crop`
        needs that check and makes it, because there a `.png` name over JPEG
        bytes spends a lossy generation on an intermediate that fact 7 is
        entirely about. Here the name comes from `formats.extension` and the
        encoder was told `-s format <fmt>` by the same plan, so there is no
        second source for the two to disagree between; checking it would test
        one line of `resize_and_encode` against itself.
      * It does not skip the non-resizing renders. The guard costs one probe
        and it is the resizing renders that pay for the RESAMPLE -- but the
        case above has no resample at all, so a post-condition that ran only
        where the resample did would miss exactly the hole it exists for.
        Every render published by this module is measured: about 3441 probes
        across the corpus, roughly a minute in twenty-four hours.
    """
    measured = imaging.probe(image)
    if measured is None:
        raise imaging.ImagingError(
            f"{target.destination.name}: encoded file is not a readable image"
        )
    width, height, _format = measured
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

    `workdir` is created here but NOT removed here, and that asymmetry is the
    point: one photo's three slices share it, so a render that swept the
    directory on its way out would take the sibling still using it. It
    belongs to `run_photo`, which makes it per photo and rmtree's it in a
    `finally`. What render owns is its own intermediates, and it unlinks
    exactly those.
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
    # Callable, not object: this is where the strategy decisions about an
    # over-cap photo are reported, and the CLI replaces it in tests to read
    # them back. `object` said nothing about what may be passed.
    log: Callable[[str], object] = print
    # Off by default, and read only by run_and_archive: an output already in
    # place is an earlier run's, produced from this same source by this same
    # plan, and re-making it costs an upscaler run to arrive at the same file.
    overwrite: bool = False


def _upscale_whole_frame(work, ctx, workdir) -> Path:
    """Normalize to sRGB PNG, then enlarge the whole frame once.

    The normalize is not conditional on the source format. upscayl-bin emits
    PNG with no ICC chunk, so an unconverted wide-gamut source comes back with
    sRGB asserted over numbers that were never in sRGB -- about 15 dB worse,
    an order of magnitude larger than the spread the upscaler was chosen on.
    """
    normalized = workdir / "normalized.png"
    enlarged = workdir / "frame_4x.png"
    imaging.normalize_to_srgb_png(work.source, normalized)
    try:
        imaging.upscale(normalized, enlarged, ctx.upscayl, ctx.models_dir)
    finally:
        # Dropped as soon as the model has read it: the normalized copy is as
        # large as the original PNG, and the frame it produced is sixteen
        # times that. In a `finally` to match `_upscale_one_plan`, which has
        # always cleaned up this way -- a failed model run left the normalized
        # copy behind here and did not there, which is the same intermediate
        # and the same reason for dropping it.
        normalized.unlink(missing_ok=True)
    return enlarged


def _upscale_one_plan(target, work, ctx, workdir) -> Path:
    """The fallback: enlarge just this plan's region.

    Only ever called for crop plans. run_photo falls back only when every
    upscaling plan has a crop, because a cropless plan's region IS the frame --
    enlarging it per-plan would hold exactly the frame the cap declined while
    paying an extra run for each sibling. The precondition below is that
    invariant made checkable rather than assumed.

    Cropping BEFORE the model rather than after is the whole point of the
    fallback: it is what keeps a photo over the cap from ever holding its whole
    4x frame.

    The crop runs before the normalize because it is cheaper to convert a third
    of an image than all of it. That ordering is safe only because sips carries
    the source's profile into the crop untouched, which is measured rather than
    assumed: strip the profile from the crop and sips reports the result as
    sRGB, `--matchTo` becomes a no-op, and the wide-gamut numbers survive into
    a file that claims to be sRGB -- 144 out of 255 on a single channel, at
    exit 0, with nothing to see in any dimension or file-exists check. Both
    orderings are pinned byte-identical in test_imaging.

    Every intermediate is named after the plan, because one photo's three
    slices share this workdir. The caller drops each enlargement before it asks
    for the next, so a shared name would work today; it would also mean that
    the first time it stops holding, one plan silently renders another plan's
    region at exactly the right size, and nothing would raise.
    """
    if target.crop is None:
        raise ValueError("_upscale_one_plan requires a crop plan; a cropless "
                         "region is the whole frame and belongs on the "
                         "whole-frame path")

    stem = target.destination.stem
    normalized = workdir / f"norm_{stem}.png"
    # .png because imaging.crop always writes PNG bytes whatever the name.
    cropped = workdir / f"pre_{stem}.png"
    try:
        imaging.crop(work.source, target.crop, cropped)
        imaging.normalize_to_srgb_png(cropped, normalized)
    finally:
        cropped.unlink(missing_ok=True)

    enlarged = workdir / f"up_{stem}.png"
    try:
        imaging.upscale(normalized, enlarged, ctx.upscayl, ctx.models_dir)
    except BaseException:
        # A failing model run can still have written part of its output, and
        # since run_photo went on to the next plan the workdir is no longer
        # swept before that plan's own enlargement arrives. Leaving this one
        # behind would hold two regions at once, which is most of what the
        # pixel cap was imposed to save.
        enlarged.unlink(missing_ok=True)
        raise
    finally:
        normalized.unlink(missing_ok=True)
    return enlarged


def run_photo(work, ctx, failures=None) -> list:
    """Render every plan of one photo. Returns the destinations written.

    `failures` is where per-plan errors go when the caller would rather have
    the rest of the photo than the first exception. Passed a list, a plan that
    raises is recorded and the remaining plans still run: the enlargement has
    been paid for already, the siblings are usually fine, and the original is
    staying where it is either way -- see run_and_archive, which decides that.
    Left as None, the first failure aborts the photo as it always did.

    Only ImagingError and OSError are collected, which is the whole of what a
    bad photo or a hostile filesystem can produce. Everything else -- the
    ValueError _upscale_one_plan raises for a cropless plan above all -- is
    about this codebase rather than this photo, and travels out of here
    unaltered. Tallied as "one photo failed", a routing bug would look exactly
    like a corrupt JPEG and would repeat, quietly, for every photo in a
    twenty-four-hour run.

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

    BOTH decisions about an over-cap photo are reported, because either one
    makes its timing unlike every other photo's and a run whose strategy
    changed without saying so is a run nobody can account for afterwards.
    Falling back says so; so does declining to, which is the slowest shape the
    tool has and would otherwise be an unexplained pause of several minutes in
    a twenty-four-hour import. This function is the only place that decision is
    made, so it is the only place that can report it without re-deriving it.
    """
    workdir = Path(ctx.workroot) / work.source.stem
    written = []
    frame = None
    over_cap = work.upscale_output_pixels > UPSCALE_PIXEL_CAP
    fall_back = over_cap and all(
        p.crop is not None for p in work.plans if p.needs_upscale)

    try:
        # INSIDE the try, so the `finally` below covers it. Created above it,
        # a signal arriving as `mkdir` returns leaves the directory with
        # nothing to remove it -- and the CLI's `workroot.rmdir()` then fails
        # ENOTEMPTY and is swallowed, so the residue outlives the run that
        # made it. Microseconds wide, but the CLI states this cleanup as a
        # guarantee, and a guarantee with a window in it is a comment that
        # lies rather than a rule.
        workdir.mkdir(parents=True, exist_ok=True)
        if work.needs_upscale and not fall_back:
            if over_cap:
                # Said BEFORE the model runs, because this is the slowest
                # photo shape the tool has -- up to 622 Mpx of output -- and
                # the line exists to explain a pause, not to record it
                # afterwards. Without it, deciding to keep the whole frame is
                # indistinguishable from a run that has stopped.
                ctx.log(
                    f"  {work.source.name}: "
                    f"{work.upscale_output_pixels / 1_000_000:.1f} Mpx "
                    f"exceeds the {UPSCALE_PIXEL_CAP / 1_000_000:.1f} Mpx cap, "
                    f"but a plan needs the whole frame; upscaling it anyway"
                )
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
            try:
                if not target.needs_upscale:
                    # Bands 1 and 2 keep their real pixels: the original, never
                    # the frame. Taken from the frame a band 2 plan would be
                    # enlarged and reduced back to the same size, and would
                    # still measure correct at every check render makes.
                    written.append(render(target, work.source, scale=1,
                                          workdir=workdir))
                elif frame is not None:
                    written.append(render(target, frame, scale=4, workdir=workdir))
                else:
                    enlarged = _upscale_one_plan(target, work, ctx, workdir)
                    try:
                        # The region is already cropped and already 4x, so
                        # render must not crop again: pass a cropless view of
                        # the plan. render then measures that region against
                        # the planned size, which is why no enlargement guard
                        # belongs here -- the guard exists to decide exactly
                        # this, from the pixels rather than the band.
                        written.append(render(replace(target, crop=None), enlarged,
                                              scale=1, workdir=workdir))
                    finally:
                        enlarged.unlink(missing_ok=True)
            except (imaging.ImagingError, OSError) as error:
                if failures is None:
                    raise
                failures.append(f"{target.destination.name}: {error}")
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


# What happened to one photo. Six values, because the interesting distinction
# is not success against failure: it is whether the ORIGINAL was dealt with.
# OK, ALREADY_DONE and REJECTED all end with the source moved out of the input
# directory; PARTIAL and FAILED both leave it exactly where it was found.
OK = "ok"
ALREADY_DONE = "already_done"
REJECTED = "rejected"
PARTIAL = "partial"
FAILED = "failed"
SKIPPED = "skipped"          # not an image; decided before a photo gets here


@dataclass
class PhotoResult:
    source: Path
    outcome: str
    written: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    archived_to: Path | None = None


def _taken(path: Path) -> bool:
    """Is this name in use? `exists()` alone is not the question.

    It follows symlinks, so a dangling one reads as a free name and the move
    replaces it. The tool does not make symlinks; the user's input directory
    might, and asking both costs one stat.
    """
    return path.exists() or path.is_symlink()


def _next_free_name(directory: Path, source: Path) -> Path:
    counter = 2
    while True:
        candidate = directory / f"{source.stem}-{counter}{source.suffix}"
        if not _taken(candidate):
            return candidate
        counter += 1


def _move(source: Path, target: Path) -> None:
    """Move `source` onto a free `target`, leaving nothing there if it fails.

    Every other writer in this project stages a `.partial` and renames, and
    unlinks only the partial it wrote itself. `shutil.move` was the one place
    that wrote straight to a final name, and it is the place where doing so
    costs the most: it falls back to copy-then-unlink across volumes, so a
    failure mid-copy -- a full disk is the realistic one, 28 GB free against
    114 GB of frames -- plants a TRUNCATED file at the canonical name. The good
    file is then archived as IMG_0042-2.jpg by the re-run, and a user pruning
    what look like duplicates by keeping the unsuffixed name loses the photo.
    That is one ordinary action away from exactly the loss archive exists to
    prevent.

    So: rename first, which is atomic and copies nothing when the two paths
    share a volume. Only when that is refused -- EXDEV and friends -- stage the
    copy beside the target and rename it in. A failure now unlinks the
    `.partial` this call wrote and nothing else, and no truncated file ever
    bears an archive name. `.partial` rather than some private suffix because
    sweep_partials already clears strays under the processing directory, which
    is where both archive directories live.

    It also settles what a failed `source.unlink()` means. After `replace`
    returns, the copy is complete by construction, so the source still being
    there is a duplicate rather than a truncation -- and the re-run archives it
    beside the copy instead of over it.
    """
    try:
        source.rename(target)
        return
    except OSError:
        pass
    staged = target.with_name(target.name + PARTIAL_SUFFIX)
    try:
        shutil.copy2(source, staged)
        staged.replace(target)
        source.unlink()
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def archive(source: Path, directory: Path, log=print) -> Path:
    """Move `source` into `directory`. NEVER overwrites.

    A second import containing another IMG_0042.jpg would otherwise replace the
    first silently. This is the only path in the design that could destroy a
    user's sole copy of an original, and the two imports need not even be in
    the same run -- originals/ accumulates across every run there has ever been.

    The rename is reported because nothing else records it. The file that ends
    up as IMG_0042-2.jpg is no longer identifiable as the source of the
    wallpapers named after IMG_0042, and this line is the user's only chance to
    notice that two photos in the import shared a name.
    """
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / source.name
    if _taken(target):
        target = _next_free_name(directory, source)
        log(f"  {source.name}: already in {directory.name}/, archived as {target.name}")
    _move(source, target)
    return target


def cheapest_first(works):
    """Photos needing no upscaler first, then by ascending upscaler cost.

    A twenty-four-hour import is interrupted at some point that is not the end,
    so the order decides what the user has when it is. 974 of the corpus's 3441
    outputs need no model run at all and take seconds each; 756 photos take the
    remaining 24.3 hours between them. Doing the free ones first means a Ctrl-C
    in the first few minutes still leaves most of a wallpaper set behind
    instead of one enlarged photo.

    Stable, so photos of equal cost keep the order they were discovered in.
    """
    return sorted(works, key=lambda w: (w.needs_upscale, w.upscale_output_pixels))


def _unfinished(result, rejected: bool = False) -> str:
    """PARTIAL if the photo got where it was going, FAILED if it did not.

    Both leave the source in place. They are told apart because a report that
    calls them the same thing cannot distinguish one flaky slice from a photo
    sips will never read at all, and only one of those is worth a second run.

    `rejected` is the third way of getting there. A photo too small for every
    device has no outputs by definition, so `written or skipped` reads it as
    a total failure -- but nothing about it failed. It was measured,
    classified and turned down, and the only thing that went wrong was the
    move into error/. Reporting that as FAILED describes a photo sips could
    not read, which is a different problem with a different fix, and it was
    the one outcome in which the report lost the rejection entirely.
    """
    return PARTIAL if (result.written or result.skipped or rejected) else FAILED


def run_and_archive(work, ctx) -> PhotoResult:
    """Render one photo's plans, then decide where its original goes.

    The decision is per PHOTO although the work is per plan, and it is the most
    data-sensitive one the tool makes: originals are moved, not copied. Three
    rules, in this order:

      * Any plan that FAILED leaves the source exactly where it was found.
        Archiving on "anything succeeded" is the trap this function exists to
        avoid -- one crashed slice would move the original out of the input
        directory while the run reported success, and the missing wallpaper
        could never be recovered by the re-run this design advertises, because
        the re-run would look in a directory the source had left.
      * Rejected on every device it was asked about, with nothing to render,
        is not a failure. The photo is simply too small for this machine's
        screens, and error/ is where a human can look at it.
      * Anything else produced its wallpapers, whether this run rendered them
        or an earlier one did, and the original goes to originals/.
    """
    result = PhotoResult(source=work.source, outcome=OK)

    if not work.plans and not work.rejected_devices:
        # Nothing was asked of this photo: no plans, and no device turned it
        # down either. Reachable only by planning against an empty device list,
        # which is a caller's mistake rather than the photo's -- but the branch
        # below would read "no outputs to write, nothing rejected" as
        # ALREADY_DONE and move the original for zero work, and that is the one
        # step here that cannot be walked back.
        result.failures.append(
            f"{work.source.name}: no plans and no rejections; nothing was asked of it")
        result.outcome = FAILED
        return result

    todo = []
    for target in work.plans:
        # Checked HERE rather than inside run_photo, because the upscaler runs
        # once per photo before any plan is rendered: a photo whose outputs are
        # all present would otherwise pay minutes for a frame nothing reads.
        # Through `is_pending`, which is the same call the CLI's report and
        # toolchain pre-flight make -- see its docstring.
        if is_pending(target, ctx.overwrite):
            todo.append(target)
        else:
            result.skipped.append(target.destination)

    if todo:
        # A cropped copy of the work, so run_photo sees only the plans that are
        # actually going to be rendered: needs_upscale is derived from them, and
        # a photo whose one upscaling plan is already done needs no model run.
        runnable = replace(work, plans=todo,
                           rejected_devices=list(work.rejected_devices))
        result.written = run_photo(runnable, ctx, failures=result.failures)
        # Every plan is rendered or reported, exactly once; this is that
        # invariant made checkable rather than assumed. A plan that went
        # missing without saying so would otherwise archive the original while
        # its wallpaper does not exist, and that is the one arrangement no
        # re-run repairs.
        unaccounted = len(todo) - len(result.written) - len(result.failures)
        if unaccounted > 0:
            result.failures.append(
                f"{unaccounted} of {len(todo)} plans were neither rendered nor reported"
            )
        elif unaccounted < 0:
            # The count can also go the other way, and a bare `if unaccounted`
            # would report that as "-1 of 3 plans were neither". A plan that is
            # in both lists has been rendered AND reported failed: reachable
            # when the cleanup after a successful render raises, which leaves
            # the output on disk and good. Reported anyway, because the
            # bookkeeping disagreeing with itself is not something to archive
            # an original on -- and the re-run skips the finished outputs.
            result.failures.append(
                f"{-unaccounted} of {len(todo)} plans were both rendered and reported failed"
            )
        # The count above checks run_photo's bookkeeping; this checks the disk.
        # A destination is returned as written only after render has measured
        # it and renamed it into place, but that is a property of render, not
        # of anything visible here, and archival is irreversible. Restricted to
        # the plans run_photo claims to have written: one that failed is named
        # in `failures` already, and one that is in neither list is counted
        # above, so nothing is reported twice.
        published = set(result.written)
        for target in todo:
            if target.destination in published and not target.destination.exists():
                result.failures.append(
                    f"{target.destination.name} was reported written but is not there"
                )

    if result.failures:
        result.outcome = _unfinished(result)
        return result

    if work.rejected_everywhere:
        result.outcome = REJECTED
        destination = ctx.processing_dir / "error"
    else:
        result.outcome = ALREADY_DONE if not result.written else OK
        destination = ctx.processing_dir / "originals"

    try:
        result.archived_to = archive(work.source, destination, ctx.log)
    except OSError as error:
        # The outputs are written and correct; only the move failed. Reported
        # rather than raised, because the source is still in place, the re-run
        # will skip the finished outputs and try the move again, and one
        # photo's permissions should not end an import with twenty hours left
        # to go. The outcome changes with it: a caller that saw OK here would
        # believe the original had been dealt with.
        #
        # The message names the DESTINATION directory, which is the only
        # thing left saying whether this photo was rejected or produced
        # wallpapers: the outcome below can no longer be REJECTED, since the
        # original did not reach error/. `rejected=` is what keeps it from
        # being called FAILED, which would describe a photo sips could not
        # read. Passed only here -- a rejected photo has no plans, so the
        # earlier `_unfinished` call is unreachable for one.
        result.failures.append(
            f"could not move {work.source.name} to {destination.name}/: {error}")
        result.outcome = _unfinished(result, rejected=work.rejected_everywhere)
    return result
