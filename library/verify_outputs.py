"""Check that every output paperhanger planned for some sources exists at its planned size.

    uv run python -m library.verify_outputs --sources DIR --processing DIR [--names A B ...]

The plan is rebuilt from the sources (after a run, `<processing>/originals`), and every
destination is measured with paperhanger's own ImageIO probe, not Spotlight, which lags on
freshly written files. Files in `to_sort_*` that no plan names are reported as unexpected.

Prints JSON {"checked", "ok", "missing", "empty", "wrong_size", "unexpected"};
exit 0 when nothing is missing, empty, the wrong size or unexpected, else 1.
"""

import argparse
import json
import sys
from pathlib import Path

from library import _library as lib
from paperhanger import cli, imaging, plan, sizes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m library.verify_outputs",
                                 description="Verify planned outputs exist at their planned size.")
    ap.add_argument("--sources", type=Path, required=True)
    ap.add_argument("--processing", type=Path, required=True)
    ap.add_argument("--names", nargs="*", default=None)
    args = ap.parse_args(argv)

    images, _ = cli.scan(args.sources)
    opts = plan.OutputSettings(args.processing, "heic", None)
    works = [plan.plan_photo(s, w, h, list(sizes.DEVICES), opts) for s, w, h, _ in images]
    if args.names:
        wanted = {lib.nfc(n) for n in args.names}
        works = [w for w in works if lib.nfc(w.source.name) in wanted]

    res = {"checked": 0, "ok": 0, "missing": [], "empty": [], "wrong_size": [], "unexpected": []}
    planned = set()
    for w in works:
        for p in w.plans:
            res["checked"] += 1
            dest = p.destination
            planned.add(dest)
            if not dest.exists():
                res["missing"].append(str(dest))
            elif dest.stat().st_size == 0:
                res["empty"].append(str(dest))
            else:
                probed = imaging.probe(dest)
                measured = list(probed[:2]) if probed else None
                if measured != [p.out_width, p.out_height]:
                    res["wrong_size"].append({"path": str(dest), "measured": measured,
                                              "planned": [p.out_width, p.out_height]})
                else:
                    res["ok"] += 1
    if not args.names:
        for d in ("to_sort_desktop", "to_sort_phone"):
            res["unexpected"] += [str(f) for f in (args.processing / d).rglob("*.heic")
                                  if f not in planned]

    json.dump(res, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if any(res[k] for k in ("missing", "empty", "wrong_size", "unexpected")) else 0


if __name__ == "__main__":
    sys.exit(main())
