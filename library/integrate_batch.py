"""Add a named, curated batch to the library, the originals corpus and the NAS.

    uv run python -m library.integrate_batch stage-review --batch DIR
    uv run python -m library.integrate_batch apply --batch DIR --to library|originals|nas [--dry-run]
    uv run python -m library.integrate_batch verify --batch DIR
    uv run python -m library.integrate_batch cleanup --batch DIR --source DIR [--source DIR ...] [--dry-run]

stage-review  copies every output into DIR/review/{desktop,desktop-secondary,phone,phone-secondary}/
              (below_target flattened). Curate there: delete what you don't want, move files
              that belong in a secondary folder.
apply         one destination per call, add-only, hash-checked:
                library    review/<role>/ -> the library folder configured for that role
                originals  DIR/originals/ -> PAPERHANGER_ORIGINALS
                nas        DIR/originals/ -> PAPERHANGER_NAS (pre-check, scp -O, sha256 there)
verify        every survivor is in the library and every original in the corpus (and on the
              NAS, when configured), byte for byte; with PAPERHANGER_ICLOUD_CHECK=1, also that
              iCloud Drive has finished syncing.
cleanup       deletes the first --source (the processing directory the batch was copied from),
              any further --source that is an empty drop folder, and DIR, only after: the
              source still matches DIR/source.sha256, every file in it reached DIR by content,
              no target overlaps the library, corpus or home, and verify passes. The record is
              written beside DIR as DIR.cleanup.json.

Config comes from the environment; see library/_library.py. Every mode prints JSON and
writes it to DIR/integrate-<mode>.json. Exit 0 ok, 1 refused or failed, 2 usage/config error.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from library import _library as lib


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m library.integrate_batch",
                                 description="Add a curated batch to a wallpaper library.")
    ap.add_argument("mode", choices=["stage-review", "apply", "verify", "cleanup"])
    ap.add_argument("--batch", type=Path, required=True)
    ap.add_argument("--to", choices=["library", "originals", "nas"])
    ap.add_argument("--source", type=Path, action="append", default=[])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        cfg = lib.load_config(os.environ)
    except lib.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    batch = args.batch
    if not batch.is_dir():
        print(f"error: no batch directory {batch}", file=sys.stderr)
        return 2

    result: dict = {"mode": args.mode + (f":{args.to}" if args.to else ""), "errors": []}
    try:
        if args.mode == "stage-review":
            result["staged"] = lib.stage_review(batch)
        elif args.mode == "apply":
            if not args.to:
                print("error: apply needs --to library|originals|nas", file=sys.stderr)
                return 2
            if args.dry_run:
                src = (lib.files(batch / "originals") if args.to != "library" else
                       [f for r in lib.ROLES for f in lib.files(batch / "review" / r)])
                result["to_add"] = len(src)
            else:
                result["added"] = {"library": lib.add_to_library, "originals": lib.add_originals,
                                   "nas": lib.add_to_nas}[args.to](batch, cfg)
        elif args.mode == "verify":
            result.update(lib.verify(batch, cfg))
        else:
            if not args.source:
                print("error: cleanup needs --source", file=sys.stderr)
                return 2
            result.update(lib.cleanup(batch, args.source, cfg, dry=args.dry_run))
    except (lib.BatchError, lib.ConfigError) as e:
        result["errors"].append(str(e))

    if args.dry_run:
        result["dry_run"] = True
    if batch.exists() and args.mode != "cleanup":
        (batch / f"integrate-{result['mode'].replace(':', '-')}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
