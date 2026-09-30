"""Route a whole-library run's outputs, and the old library's irreplaceable files, into a
new library layout, holding the uncertain ones for review. The instrument behind the
report's "Assembly" section.

    PAPERHANGER_LIBRARY=<the old library> PAPERHANGER_ORIGINALS=... \\
    uv run python docs/research/2026-09-28-tier-4-full-corpus-run/assemble.py \\
        --run-dir DIR --out DIR [--aliases FILE.json] [--apply | --promote]

Without a flag it decides everything and writes nothing. --apply copies (never moves)
into --out; --promote moves review survivors to their folders after a manual review.
--aliases is a JSON {new stem: old stem} kept outside the repository, for sources whose
filename changed between the old library and the new run (e.g. an encoding accident).

Routing, new outputs, keyed (stem, position, device):
    key in the old library                  -> its folder (primary or secondary by stem)
    side slice whose siblings were kept     -> dropped (listed; still in processing/)
    stem filed in a secondary folder        -> the secondary folder for that device
    old library has this stem+device        -> review/shape-change/<device>
    old library has this stem, other device -> review/new-device/<device>
    stem new to the library                 -> review/new-source/<device>
Old files whose key the run does not produce are carried over unchanged (orphan: no
source staged; device-rejected: the run made nothing for that device; shape-change),
one per key, .heic preferred. Old files the run does produce are superseded.
"""

import argparse
import collections
import json
import sys
from pathlib import Path
import os
import shutil

import _instruments as ins
from _instruments import lib

SIDE = {"left", "right", "top", "bottom"}
REVIEW_REASONS = ("shape-change", "new-device", "new-source")


def role(device: str, secondary: bool) -> str:
    return f"{device}-secondary" if secondary else device


def plan_routes(cfg, run_dir: Path, aliases: dict):
    old = ins.parse_library(cfg)
    secondary = ins.secondary_stems(old)
    old_by_key = ins.by_key(old)
    old_sd, old_stems = collections.defaultdict(set), set()
    for (stem, pos, dev) in old_by_key:
        old_sd[(stem, dev)].add(pos)
        old_stems.add(stem)

    processing = run_dir / "processing"
    works = [w for d in (processing / "originals", run_dir / "input", run_dir / "pilot-input")
             if d.is_dir() for w in ins.build_works(d, processing)[0]]
    plans = [p for w in works for p in w.plans]
    new_keys = {ins.new_key(p, aliases) for p in plans}
    new_sd, new_stems = collections.defaultdict(set), set()
    for (stem, pos, dev) in new_keys:
        new_sd[(stem, dev)].add(pos)
        new_stems.add(stem)

    new_routes = []
    for p in plans:
        stem, pos, dev = k = ins.new_key(p, aliases)
        sec = stem in secondary
        if k in old_by_key:
            new_routes.append((p, "matched", cfg.folders[role(dev, sec)]))
        elif pos in SIDE and (old_sd.get((stem, dev), set()) - {None}):
            new_routes.append((p, "dropped-pruned-sibling", None))
        elif sec:
            new_routes.append((p, "secondary-direct", cfg.folders[role(dev, True)]))
        elif (stem, dev) in old_sd:
            new_routes.append((p, "review-shape-change", f"review/shape-change/{dev}"))
        elif stem in old_stems:
            new_routes.append((p, "review-new-device", f"review/new-device/{dev}"))
        else:
            new_routes.append((p, "review-new-source", f"review/new-source/{dev}"))

    legacy_routes = []
    for k, recs in old_by_key.items():
        stem, pos, dev = k
        keep, *dupes = sorted(recs, key=lambda r: (r["ext"] != "heic", r["copy"], r["path"]))
        legacy_routes += [(d, "duplicate-twin", None) for d in dupes]
        if k in new_keys:
            legacy_routes.append((keep, "superseded", None))
            continue
        reason = ("carry-orphan" if stem not in new_stems else
                  "carry-device-rejected" if (stem, dev) not in new_sd else "carry-shape-change")
        folder = cfg.folders[role(dev, stem in secondary)]
        legacy_routes.append((keep, reason, folder))
    return new_routes, legacy_routes


def main() -> int:
    ap = argparse.ArgumentParser(description="Assemble a rebuilt library.")
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--aliases", type=Path, default=None)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true")
    g.add_argument("--promote", action="store_true")
    args = ap.parse_args()
    cfg = lib.load_config(os.environ)
    A, errors = args.out, []

    if args.promote:
        moved = collections.Counter()
        for reason in REVIEW_REASONS:
            for sub, r in (("desktop", "desktop"), ("phone", "phone"),
                           ("desktop-secondary", "desktop-secondary"),
                           ("phone-secondary", "phone-secondary")):
                for f in lib.files(A / "review" / reason / sub):
                    dest = A / cfg.folders[r] / f.name
                    if dest.exists():
                        errors.append(f"promote collision: {dest}")
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    f.rename(dest)
                    moved[cfg.folders[r]] += 1
        left = [f for f in (A / "review").rglob("*") if f.is_file() and not f.name.startswith(".")]
        errors += [f"review/ not empty: {len(left)} files"] if left else []
        result = {"mode": "promote", "promoted": dict(moved), "errors": errors}
    else:
        aliases = json.loads(args.aliases.read_text()) if args.aliases else {}
        aliases = {lib.nfc(k): lib.nfc(v) for k, v in aliases.items()}
        new_routes, legacy_routes = plan_routes(cfg, args.run_dir, aliases)
        counts = collections.Counter(d for *_, d in new_routes + legacy_routes if d)
        missing = [str(p.destination) for p, _, d in new_routes if d and not p.destination.exists()]
        if args.apply:
            for reason in REVIEW_REASONS:
                for sub in lib.ROLES:
                    (A / "review" / reason / sub).mkdir(parents=True, exist_ok=True)
            items = [(p.destination, d) for p, _, d in new_routes if d] + \
                    [(Path(r["path"]), d) for r, _, d in legacy_routes if d]
            for src, d in items:
                (A / d).mkdir(parents=True, exist_ok=True)
                if not src.exists() or src.stat().st_blocks == 0:
                    errors.append(f"missing or not downloaded: {src}")
                elif not (A / d / src.name).exists():
                    shutil.copy2(src, A / d / src.name)
        result = {"mode": "apply" if args.apply else "dry-run",
                  "counts": dict(sorted(counts.items())),
                  "new": dict(collections.Counter(r for _, r, _ in new_routes)),
                  "legacy": dict(collections.Counter(r for _, r, _ in legacy_routes)),
                  "missing_new": len(missing), "errors": errors}
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
