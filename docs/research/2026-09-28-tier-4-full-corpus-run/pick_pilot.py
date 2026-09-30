"""Choose a pilot that covers every band, crop direction, format and edge case.

    PAPERHANGER_LIBRARY=... PAPERHANGER_ORIGINALS=... \\
    uv run python docs/research/2026-09-28-tier-4-full-corpus-run/pick_pilot.py \\
        --input DIR [--named FILE.json] [--min-compare 11]

Selection, in order: any photos named in `--named` (a JSON {filename: reason} kept
outside the repository, for edge cases the author knows about: the largest file, a
mislabelled format, an awkward name); then one photo per category below; then typical
photos for the side-by-side comparison, meaning every output has a counterpart in the
existing library and the model cost sits near the median. Prints JSON; moves nothing.
"""

import argparse
import collections
import json
import os
import sys
from pathlib import Path

import _instruments as ins
from _instruments import lib
from paperhanger import bands, sizes


def main() -> int:
    ap = argparse.ArgumentParser(description="Choose a tier-4 pilot.")
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--processing", type=Path, default=None)
    ap.add_argument("--named", type=Path, default=None)
    ap.add_argument("--min-compare", type=int, default=8)
    args = ap.parse_args()
    cfg = lib.load_config(os.environ)

    works, _ = ins.build_works(args.input, args.processing or args.input.parent / "processing")
    old = ins.parse_library(cfg)
    old_keys = set(ins.by_key(old))
    secondary = ins.secondary_stems(old)
    stems = collections.Counter(lib.nfc(w.source.stem).casefold() for w in works)
    if any(n > 1 for n in stems.values()):
        sys.exit(f"sources share a stem: {[s for s, n in stems.items() if n > 1]}")

    by_name = {lib.nfc(w.source.name): w for w in works}
    chosen = collections.OrderedDict()
    named = json.loads(args.named.read_text()) if args.named else {}
    for name, reason in named.items():
        chosen.setdefault(lib.nfc(name), []).append(reason)

    def matches(w):
        return sum(ins.new_key(p) in old_keys for p in w.plans)

    ranked = sorted(works, key=lambda w: (-min(matches(w), 1), ins.model_seconds(w), w.source.name))

    def has(w, band=None, crop=None, device=None):
        return any((band is None or p.band == band) and (crop is None or (p.crop is not None) == crop)
                   and (device is None or p.device == device) for p in w.plans)

    def first(pred, reason):
        for n in chosen:
            if n in by_name and pred(by_name[n]):
                chosen[n].append(reason)
                return
        for w in ranked:
            if pred(w):
                chosen.setdefault(lib.nfc(w.source.name), []).append(reason)
                return

    ext = lambda w: w.source.suffix.lower()
    first(lambda w: ext(w) == ".gif", "gif source")
    first(lambda w: ext(w) == ".heic", "heic source")
    first(lambda w: ext(w) == ".webp", "webp source")
    first(lambda w: ext(w) == ".png" and w.needs_upscale, "png source, upscaled")
    first(lambda w: max(w.width, w.height) == max(max(x.width, x.height) for x in works),
          "largest source")
    first(ins.is_fallback, "over the whole-frame cap: per-plan fallback")
    first(lambda w: w.needs_upscale and not ins.is_fallback(w)
          and w.upscale_output_pixels == max(x.upscale_output_pixels for x in works
                                             if x.needs_upscale and not ins.is_fallback(x)),
          "largest whole 4x frame under the cap")
    first(lambda w: has(w, band=bands.NATIVE, crop=False), "band 2 native, full frame")
    first(lambda w: has(w, band=bands.NATIVE, crop=True), "band 2 native, crop slices")
    first(lambda w: has(w, band=bands.UPSCALE_REDUCE, crop=False), "band 3, full frame")
    first(lambda w: has(w, band=bands.UPSCALE_REDUCE, crop=True, device=sizes.DESKTOP),
          "band 3, horizontal crop")
    first(lambda w: has(w, band=bands.UPSCALE_REDUCE, crop=True, device=sizes.PHONE),
          "band 3, vertical crop")
    first(lambda w: has(w, band=bands.UPSCALE_ONLY), "band 4, 4x below target")
    first(lambda w: sizes.DESKTOP in w.rejected_devices, "desktop rejected")
    first(lambda w: lib.nfc(w.source.stem) in secondary, "source filed outside the primary folder")

    compare = [n for n in chosen if n in by_name and by_name[n].needs_upscale and matches(by_name[n])]
    upscaled = sorted(ins.model_seconds(w) for w in works if w.needs_upscale)
    median = upscaled[len(upscaled) // 2] if upscaled else 0.0
    typical = sorted((w for w in works if w.plans and matches(w) == len(w.plans)),
                     key=lambda w: (abs(ins.model_seconds(w) - median), w.source.name))
    for w in typical:
        if len(compare) >= args.min_compare:
            break
        n = lib.nfc(w.source.name)
        if n not in chosen and w.needs_upscale and not ins.is_fallback(w):
            chosen[n] = ["typical, has a library counterpart (comparison)"]
            compare.append(n)

    photos = [{"source": str(by_name[n].source), "name": n, "reasons": r,
               "est_seconds": round(ins.model_seconds(by_name[n]), 1),
               "fallback": ins.is_fallback(by_name[n]), "outputs": len(by_name[n].plans),
               "library_matches": matches(by_name[n])} for n, r in chosen.items() if n in by_name]
    json.dump({"photos": photos, "outputs": sum(p["outputs"] for p in photos),
               "total_est_seconds": round(sum(p["est_seconds"] for p in photos), 1)},
              sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
