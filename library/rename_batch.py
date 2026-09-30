"""Give a batch library-style names: approved words plus an unused 4-digit suffix.

    uv run python -m library.rename_batch --batch DIR --words DIR/words.json [--apply]

`words.json` maps every original's filename to its content words, e.g.
{"IMG_0001.jpg": "red_maple_leaves"}. The suffix is assigned here, never by whoever wrote
the words, checked against every stem the library already uses (both output namings, the
originals corpus, PAPERHANGER_EXTRA_STEM_DIRS and the NAS), and saved to `names.json` beside
`words.json` so that `--apply` renames to exactly the table the dry run printed.

The dry run renames nothing. `--apply` renames every output and then every original,
through paperhanger's own `plan.output_name`, so each output keeps its position, size and
factor token; a failure part-way undoes every rename.

Prints JSON: {"mode", "photos", "outputs", "table": [{"old", "new"}], "moves"}.
Exit 0 on success, 1 when the batch cannot be renamed, 2 on a usage or config error.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from library import _library as lib
from paperhanger import cli, plan, sizes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m library.rename_batch",
                                 description="Library-style names for a paperhanger batch.")
    ap.add_argument("--batch", type=Path, required=True)
    ap.add_argument("--words", type=Path, required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    try:
        cfg = lib.load_config(os.environ)
    except lib.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    batch = args.batch
    words = {lib.nfc(k): v for k, v in json.loads(args.words.read_text()).items()}
    images, _ = cli.scan(batch / "originals")
    opts = plan.OutputSettings(batch, "heic", None)
    works = [plan.plan_photo(s, w, h, list(sizes.DEVICES), opts) for s, w, h, _ in images]
    names = {lib.nfc(w.source.name) for w in works}
    if set(words) != names:
        print(f"error: words.json must name every original exactly once "
              f"(missing {sorted(names - set(words))}, extra {sorted(set(words) - names)})",
              file=sys.stderr)
        return 2

    names_path = args.words.with_name("names.json")
    persisted = json.loads(names_path.read_text()) if names_path.exists() else {}
    try:
        taken = lib.library_stems(cfg) | {lib.nfc(Path(n).stem).casefold() for n in names}
        table = lib.assign_suffixes(words, taken, persisted=persisted)
        by_name = {lib.nfc(w.source.name): w for w in works}
        moves = lib.build_moves(works, {by_name[k].source.name: v for k, v in table.items()})
        existing = [str(b) for _, b in moves if b.exists()]
        if existing:
            raise lib.BatchError(f"rename targets already exist: {existing[:5]}")
        names_path.write_text(json.dumps(table, ensure_ascii=False, indent=2))
        if args.apply:
            (batch / "renames.json").write_text(
                json.dumps([[str(a), str(b)] for a, b in moves], indent=1))
            lib.apply_moves(moves)
    except lib.BatchError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    json.dump({"mode": "apply" if args.apply else "dry-run", "photos": len(table),
               "outputs": sum(len(w.plans) for w in works),
               "table": [{"old": o, "new": n} for o, n in sorted(table.items())],
               "moves": len(moves)}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
