"""Shared helpers for the tier-4 instruments. Not part of the tool.

Resolves the repository from its own location, so the instruments run with the
project interpreter from anywhere: `uv run python docs/research/<this dir>/<tool>.py`.
"""

import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library import _library as lib                      # noqa: E402
from paperhanger import cli, execute, plan, report, sizes  # noqa: E402


def build_works(input_dir: Path, processing_dir: Path):
    """paperhanger's plan for a directory, exactly as `paperhanger -b` builds it."""
    opts = plan.OutputSettings(processing_dir, "heic", 80)
    images, non_images = cli.scan(input_dir)
    return [plan.plan_photo(s, w, h, list(sizes.DEVICES), opts) for s, w, h, _ in images], non_images


def is_fallback(work) -> bool:
    """Over the whole-frame cap: enlarged once per plan instead of once per photo."""
    return work.needs_upscale and work.upscale_output_pixels > execute.UPSCALE_PIXEL_CAP


def model_seconds(work) -> float:
    """Upscaler seconds for one photo, charging the per-plan fallback honestly.

    `report.estimate_seconds` charges every photo one whole 4x frame. A photo over the
    cap runs the model once per upscaling plan, on that plan's crop (or the whole frame
    when it has none), and the three slices overlap.
    """
    if not work.needs_upscale:
        return 0.0
    if not is_fallback(work):
        return report.estimate_seconds([work])
    k = sizes.UPSCALE_FACTOR ** 2
    px = sum((p.crop.width * p.crop.height if p.crop else work.width * work.height) * k
             for p in work.plans if p.needs_upscale)
    return report.SECONDS_PER_OUTPUT_MEGAPIXEL * px / 1_000_000


def new_key(p, aliases=None):
    stem = lib.nfc(p.source.stem)
    return ((aliases or {}).get(stem, stem), p.position, p.device)


def parse_library(cfg: lib.Config) -> list:
    """Every file in the (pre-rebuild) library: {path, role, stem, pos, device, ext, copy}.

    Names outside both grammars are taken as full-frame outputs of the stem they spell,
    on the folder's device.
    """
    out = []
    for role, folder in cfg.folders.items():
        for path in lib.files(cfg.library / folder):
            name = lib.nfc(path.name)
            parsed = lib.parse_legacy_output_name(name) or lib.parse_output_name(name)
            stem, pos, device = parsed or (name.rsplit(".", 1)[0], None, role.split("-")[0])
            out.append({"path": str(path), "role": role, "stem": stem, "pos": pos,
                        "device": device, "ext": name.rsplit(".", 1)[-1],
                        "copy": name.endswith(" copy.jpeg") or " copy." in name})
    return out


def secondary_stems(records) -> set:
    """Stems filed in the desktop-secondary folder; their outputs go to secondary folders."""
    return {r["stem"] for r in records if r["role"] == "desktop-secondary"}


def by_key(records) -> dict:
    d = collections.defaultdict(list)
    for r in records:
        d[(r["stem"], r["pos"], r["device"])].append(r)
    return d
