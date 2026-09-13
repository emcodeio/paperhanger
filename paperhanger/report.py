"""Rendering the plan for a human. Reads the same structures the executor runs.

The dry-run report and the executor consume one list of plans, so the report
cannot drift from what actually happens -- the usual failure mode of a
bolted-on dry-run flag.
"""

from . import bands, sizes

# Measured across a 33x range of image sizes: upscayl's wall clock is about
# 0.8 s per megapixel of OUTPUT, which is 16x the source pixels at 4x.
SECONDS_PER_OUTPUT_MEGAPIXEL = 0.8


def _remaining(work, done_outputs) -> list:
    """The plans of one photo this run would actually render."""
    return [p for p in work.plans if p.destination not in done_outputs]


def estimate_seconds(works, done_outputs=()) -> float:
    """Total upscaler time. Per PHOTO, because the frame is enlarged once.

    Charged over the plans still to render rather than all of them. The
    executor runs a cropped copy of the work -- `run_and_archive` builds
    `runnable` from the plans that survive skip-existing, and `needs_upscale`
    is derived from those -- so a photo whose one upscaling plan is already on
    disk starts no model run, while a photo with three of four outputs done
    still pays for its frame exactly once.

    The scoping is HERE rather than in the caller for the same reason the
    outputs count is. A caller that filtered whole photos and left this to sum
    over them could only ever round a half-finished photo to nothing or to
    everything, and there is no note in a docstring that makes that right.
    """
    done = set(done_outputs)
    total = 0.0
    for work in works:
        if any(p.needs_upscale for p in _remaining(work, done)):
            total += SECONDS_PER_OUTPUT_MEGAPIXEL * work.upscale_output_pixels / 1_000_000
    return total


def format_duration(seconds: float) -> str:
    """'~45 s', '~14 min', '~1.5 h'. Never '~1.0 h'.

    The last rule is `bands.drop_trailing_zero`, called rather than copied:
    this module held its own `_strip_trailing_zero` under a comment reading
    "same rule as bands.factor_token", which is the note written immediately
    before two copies of a rule drift apart.
    """
    if seconds < 90:
        return f"~{seconds:.0f} s"
    if seconds < 3600:
        return f"~{seconds / 60:.0f} min"
    return f"~{bands.drop_trailing_zero(f'{seconds / 3600:.1f}')} h"


def _action(target) -> str:
    """The band's effect, rendered once -- never duplicated with the caller's dims."""
    if target.band == bands.DOWNSCALE:
        return f"{target.out_width}x{target.out_height}  downscale"
    if target.band == bands.NATIVE:
        # NATIVE lands in below_target/ exactly like UPSCALE_ONLY -- see
        # plan.destination_dir and bands.BELOW_TARGET. Mark it the same way.
        return f"{target.out_width}x{target.out_height}  native  below_target"
    if target.band == bands.UPSCALE_REDUCE:
        return f"4x -> {target.out_width}x{target.out_height}"
    if target.band == bands.UPSCALE_ONLY:
        return f"4x -> {target.out_width}x{target.out_height}  below_target"
    raise ValueError(f"no report action for band {target.band}")


def render_photo(work, already_done: bool = False) -> str:
    name = work.source.name

    if already_done:
        return f" {name:<34} already done, skipping"

    if work.rejected_everywhere:
        # "both" only when both were actually asked. A `-d` run printing
        # "too small for both" about a photograph that would make a perfectly
        # good phone wallpaper states a conclusion the run never reached, and
        # states it about the user's own file -- which is also how they would
        # decide not to try it on a phone. Task 11 asked for this and the
        # implementation flattened it.
        where = "both" if len(work.rejected_devices) > 1 \
            else work.rejected_devices[0]
        return f" {name:<34} reject (too small for {where})"

    lines = []
    note = ""
    if work.rejected_devices:
        note = f"   ({', '.join(work.rejected_devices)} rejected)"

    crops = [p for p in work.plans if p.crop is not None]
    whole = [p for p in work.plans if p.crop is None]

    for target in whole:
        lines.append(
            f" {name:<34} {target.target.name:<15} "
            f"{target.governing} -> {_action(target)}"
            f"{note}"
        )
        note = ""

    by_device = {}
    for target in crops:
        by_device.setdefault(target.device, []).append(target)
    for device, targets in by_device.items():
        orientation = "horizontal" if device == sizes.DESKTOP else "vertical"
        lines.append(f" {name:<34} {device:<15} crop 3x {orientation}{note}")
        note = ""
        for target in targets:
            lines.append(
                f"   {target.position:<12} {target.governing} -> {_action(target)}"
            )
    return "\n".join(lines)


def _count(n: int, noun: str) -> str:
    """'1 output', '2 outputs'. Same pluralization for every counted noun."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def render_report(works, done_outputs=(), non_images: int = 0) -> str:
    """`done_outputs` is the set of DESTINATIONS already on disk -- not a count.

    A count could only ever inflate the header; it could never make the
    per-photo 'already done, skipping' line reachable. Task 12's CLI already
    computes this set to decide what to skip, so passing it here costs it
    nothing.

    Destinations rather than sources, which is the whole of the fix here. The
    header answers "what will THIS RUN do?", not "how much was there
    originally?" -- and a photo with three of its four outputs already
    present will do ONE of them. Given only the set of finished SOURCES that
    photo is not finished, so every one of its four plans was counted, and
    the header promised four outputs where one was coming. Which photos are
    wholly done is derivable from the destinations; the reverse is not, so
    this is the parameter that can answer both questions.
    """
    done = set(done_outputs)
    rejected = sum(1 for w in works if w.rejected_everywhere)
    outputs = sum(len(_remaining(w, done)) for w in works)
    # `w.plans and ...` because `all` over an empty sequence is True and a
    # photo rejected on every device has no plans at all: without the guard
    # it renders as "already done, skipping" while the header two lines up
    # counts it as rejected.
    finished = {w.source for w in works
                if w.plans and not _remaining(w, done)}
    parts = [
        f"{_count(len(works), 'image')}, {_count(outputs, 'output')}, {rejected} rejected, "
        f"{len(finished)} already done, {_count(non_images, 'non-image')} skipped, "
        f"{format_duration(estimate_seconds(works, done))}",
        "",
    ]
    for work in works:
        rendered = render_photo(work, already_done=work.source in finished)
        if rendered:
            parts.append(rendered)
    return "\n".join(parts)
