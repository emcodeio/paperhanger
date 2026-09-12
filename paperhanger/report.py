"""Rendering the plan for a human. Reads the same structures the executor runs.

The dry-run report and the executor consume one list of plans, so the report
cannot drift from what actually happens -- the usual failure mode of a
bolted-on dry-run flag.
"""

from . import bands, sizes

# Measured across a 33x range of image sizes: upscayl's wall clock is about
# 0.8 s per megapixel of OUTPUT, which is 16x the source pixels at 4x.
SECONDS_PER_OUTPUT_MEGAPIXEL = 0.8


def estimate_seconds(works) -> float:
    """Total upscaler time. Per PHOTO, because the frame is enlarged once."""
    total = 0.0
    for work in works:
        if work.needs_upscale:
            total += SECONDS_PER_OUTPUT_MEGAPIXEL * work.upscale_output_pixels / 1_000_000
    return total


def _strip_trailing_zero(text: str) -> str:
    """'1.0' -> '1', '1.5' -> '1.5'. Same rule as bands.factor_token."""
    return text.rstrip("0").rstrip(".") if "." in text else text


def format_duration(seconds: float) -> str:
    if seconds < 90:
        return f"~{seconds:.0f} s"
    if seconds < 3600:
        return f"~{seconds / 60:.0f} min"
    return f"~{_strip_trailing_zero(f'{seconds / 3600:.1f}')} h"


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
        return f" {name:<34} reject (too small for both)"

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


def render_report(works, already_done=(), non_images: int = 0) -> str:
    """`already_done` is the set of source Paths already produced -- not a count.

    A count could only ever inflate the header; it could never make the
    per-photo 'already done, skipping' line reachable. Task 12's CLI already
    computes this set to decide what to skip, so passing it here costs it
    nothing.

    The header answers "what will THIS RUN do?", not "how much was there
    originally?" -- so the estimate and the outputs count are taken over the
    photos NOT already done. A photo whose outputs already exist costs
    nothing and produces nothing this run, however large its own upscale
    would have been.
    """
    done = set(already_done)
    pending = [w for w in works if w.source not in done]
    rejected = sum(1 for w in works if w.rejected_everywhere)
    outputs = sum(len(w.plans) for w in pending)
    parts = [
        f"{_count(len(works), 'image')}, {_count(outputs, 'output')}, {rejected} rejected, "
        f"{len(done)} already done, {_count(non_images, 'non-image')} skipped, "
        f"{format_duration(estimate_seconds(pending))}",
        "",
    ]
    for work in works:
        rendered = render_photo(work, already_done=work.source in done)
        if rendered:
            parts.append(rendered)
    return "\n".join(parts)
