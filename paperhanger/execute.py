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

from pathlib import Path

from . import imaging

PARTIAL_SUFFIX = ".partial"


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


def render(target, source_image, scale: int, workdir) -> Path:
    """Produce `target.destination` from `source_image`.

    `scale` is 1 when source_image is the original and 4 when it is the
    enlarged whole frame; the crop rect is multiplied to match.

    A band 3 or 4 plan must never be rendered at scale 1 from its original:
    sips would be asked to ENLARGE, which is the one thing only the ML model
    may do. Those bands are rendered from the 4x frame at scale 4, or -- when
    the pixel cap forces a per-plan upscale -- from an already-enlarged region
    with `crop` cleared, which is a whole-image plan by then.
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
            imaging.crop(current, rect, cropped)
            intermediates.append(cropped)
            current = cropped

        # Never fused with the crop above: sips applies a resample against the
        # PRE-crop dimensions and silently returns the wrong size.
        imaging.resize_and_encode(
            current, target.out_width, target.out_height,
            target.fmt, target.quality, staged,
            # A band 2 or 4 plan needs no resize OF THE SOURCE, but a slice of
            # the 4x frame is 4x the planned size and must come back down.
            resize=target.needs_resize or scale != 1,
        )
        staged.replace(target.destination)
        return target.destination
    except BaseException:
        staged.unlink(missing_ok=True)
        target.destination.unlink(missing_ok=True)
        raise
    finally:
        for leftover in intermediates:
            leftover.unlink(missing_ok=True)
