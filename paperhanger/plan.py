"""What to produce from one photo. Pure: no filesystem, no subprocesses.

The output is a FLAT list of OutputPlans grouped by source photo. Flat because
section 6 removed the recursion a tree would justify -- slices are planned
directly rather than re-classified, so nesting could never exceed one level.
Grouped by photo because two things are decided per photo rather than per
output: whether the upscaler runs, and where the original is archived.
"""

from dataclasses import dataclass, field
from pathlib import Path

from . import bands, formats, geometry, sizes
from .classify import CROP, classify


@dataclass(frozen=True)
class OutputSettings:
    processing_dir: Path
    fmt: str
    quality: int | None


@dataclass(frozen=True)
class OutputPlan:
    source: Path
    device: str
    target: sizes.Target
    band: int
    governing: int
    out_width: int
    out_height: int
    factor: float | None
    destination: Path
    fmt: str
    quality: int | None
    crop: geometry.Rect | None = None
    position: str | None = None

    @property
    def needs_upscale(self) -> bool:
        return self.band in bands.UPSCALING

    @property
    def needs_resize(self) -> bool:
        return self.band in bands.RESIZING

    @property
    def factor_token(self) -> str:
        return bands.factor_token(self.factor)


@dataclass
class PhotoWork:
    source: Path
    width: int
    height: int
    plans: list[OutputPlan] = field(default_factory=list)
    rejected_devices: list[str] = field(default_factory=list)

    @property
    def needs_upscale(self) -> bool:
        return any(p.needs_upscale for p in self.plans)

    @property
    def rejected_everywhere(self) -> bool:
        return not self.plans and bool(self.rejected_devices)

    @property
    def upscale_output_pixels(self) -> int:
        """Pixels in the 4x whole frame -- what the executor's cap compares."""
        return self.width * self.height * sizes.UPSCALE_FACTOR ** 2


def output_name(source: Path, position, device: str, out_width: int,
                out_height: int, factor, fmt: str) -> str:
    stem = source.stem if position is None else f"{source.stem}_{position}"
    token = bands.factor_token(factor)
    return f"{stem}_{device}_{out_width}x{out_height}_{token}.{formats.extension(fmt)}"


def destination_dir(processing_dir: Path, device: str, band: int) -> Path:
    base = processing_dir / f"to_sort_{device}"
    return base / "below_target" if band in bands.BELOW_TARGET else base


def _make_plan(source: Path, device: str, target: sizes.Target, band: int, *,
               governing: int, width: int, height: int, opts: OutputSettings,
               crop: geometry.Rect | None = None,
               position: str | None = None) -> OutputPlan:
    """Keyword-only from `governing` on, deliberately.

    Three of these parameters are interchangeable ints, and the transposition
    that matters is silent: on a width-axis target `governing` IS the width,
    so swapping the two produces exactly the right plan for every desktop
    slice and exactly the wrong one for every by-height plan -- correct output
    dimensions, wrong net factor, wrong filename. Nothing downstream measures
    the factor. Keyword-only makes that particular mistake unwritable rather
    than merely unlikely.
    """
    out_width, out_height = bands.output_size(width, height, target, band)
    factor = bands.net_factor(governing, target, band)
    name = output_name(source, position, device, out_width, out_height, factor, opts.fmt)
    return OutputPlan(
        source=source, device=device, target=target, band=band, governing=governing,
        out_width=out_width, out_height=out_height, factor=factor,
        destination=destination_dir(opts.processing_dir, device, band) / name,
        fmt=opts.fmt, quality=opts.quality, crop=crop, position=position,
    )


def plan_photo(source: Path, width: int, height: int,
               devices, opts: OutputSettings) -> PhotoWork:
    """Everything to produce from one photo, for the devices asked about.

    `devices` must hold no duplicates. Planned twice for one device a photo
    produces two identical destinations, and `find_collisions` would not see
    them -- it records which SOURCES claim each destination, and both claims
    come from this one. `cli.resolve_devices` is what guarantees it; this
    docstring is what makes that a contract rather than an accident of how
    `-d`/`-p`/`-b` happen to be spelled.

    The source's own format is NOT taken. It was, and nothing ever read it:
    the decision to normalize unconditionally on the upscale path (constraint
    5) removed the only consumer it could have had, since a format-based
    condition would never fire for any of the corpus's 118 non-sRGB files.
    `imaging.probe` still returns it -- `cli.scan` reports non-images with it
    -- but it stops here.
    """
    work = PhotoWork(source=source, width=width, height=height)

    for device in devices:
        outcome = classify(width, height, device)

        if outcome is not CROP:
            governing = sizes.governing_dimension(width, height, outcome)
            band = bands.band_for(governing, outcome)
            if band == bands.REJECT:
                work.rejected_devices.append(device)
                continue
            work.plans.append(_make_plan(
                source, device, outcome, band,
                governing=governing, width=width, height=height, opts=opts,
            ))
            continue

        # Crop path. The target is assigned by CONSTRUCTION, not re-derived from
        # the slice: a horizontal slice is desktop-by-width because that is what
        # cutting a 16:10 slice means. test_geometry proves the slices satisfy it.
        if device == sizes.DESKTOP:
            rects = geometry.horizontal_thirds(width, height)
            positions = geometry.HORIZONTAL_POSITIONS
            target = sizes.DESKTOP_BY_WIDTH
        else:
            rects = geometry.vertical_thirds(width, height)
            positions = geometry.VERTICAL_POSITIONS
            target = sizes.PHONE_BY_HEIGHT

        rejected_here = False
        planned_here = 0
        for rect, position in zip(rects, positions):
            governing = sizes.governing_dimension(rect.width, rect.height, target)
            band = bands.band_for(governing, target)
            if band == bands.REJECT:
                rejected_here = True
                continue
            planned_here += 1
            work.plans.append(_make_plan(
                source, device, target, band, governing=governing,
                width=rect.width, height=rect.height, opts=opts,
                crop=rect, position=position,
            ))
        # A device is turned down only when NOTHING was planned for it. As it
        # happens `rejected_here` already implies that: the three slices of
        # one device are all the same size, so they share a governing
        # dimension and therefore a band, and they reject together or not at
        # all. The count is kept because the guard that used to stand here --
        # `not any(p.device == device for p in work.plans)` -- searched a list
        # holding EVERY device's plans and was right only because
        # `plan_photo`'s devices never repeat. Counting this device's own
        # plans says what is meant, and keeps saying it if a third device or
        # an uneven slicing ever arrives.
        if rejected_here and planned_here == 0:
            work.rejected_devices.append(device)

    return work


def find_collisions(works) -> dict:
    """Output paths claimed by more than one source photo.

    Two sources sharing a stem across different extensions produce identical
    output names. Detected before any work is done, and fatal.

    SOURCES, deduplicated, because that is the question and that is what the
    error message prints. One photo claiming a destination twice is a
    different defect with a different fix -- it means `plan_photo` was handed
    the same device twice -- and `cli.resolve_devices` closes it where the
    list is built, which is why `plan_photo`'s docstring states it as a
    contract rather than leaving it for this function to discover.
    """
    claims: dict[Path, list[Path]] = {}
    for work in works:
        for p in work.plans:
            claims.setdefault(p.destination, [])
            if work.source not in claims[p.destination]:
                claims[p.destination].append(work.source)
    return {dest: sources for dest, sources in claims.items() if len(sources) > 1}
