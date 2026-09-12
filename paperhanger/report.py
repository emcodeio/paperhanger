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


def format_duration(seconds: float) -> str:
    if seconds < 90:
        return f"~{seconds:.0f} s"
    if seconds < 3600:
        return f"~{seconds / 60:.0f} min"
    return f"~{seconds / 3600:.1f} h"


def _action(target) -> str:
    if target.band == bands.DOWNSCALE:
        return "downscale"
    if target.band == bands.NATIVE:
        return "native"
    if target.band == bands.UPSCALE_REDUCE:
        return f"4x -> {target.out_width}x{target.out_height}"
    return f"4x only -> {target.out_width}x{target.out_height}  below_target"


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
        governing = target.governing
        lines.append(
            f" {name:<34} {target.target.name:<15} "
            f"{governing} -> {target.out_width}x{target.out_height}  {_action(target)}"
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
                f"   {target.position:<12} {target.governing} -> "
                f"{target.out_width}x{target.out_height}  {_action(target)}"
            )
    return "\n".join(lines)


def render_report(works, already_done: int = 0, non_images: int = 0) -> str:
    rejected = sum(1 for w in works if w.rejected_everywhere)
    outputs = sum(len(w.plans) for w in works)
    parts = [
        f"{len(works)} images, {outputs} outputs, {rejected} rejected, "
        f"{already_done} already done, {non_images} non-image skipped, "
        f"{format_duration(estimate_seconds(works))}",
        "",
    ]
    for work in works:
        rendered = render_photo(work)
        if rendered:
            parts.append(rendered)
    return "\n".join(parts)
