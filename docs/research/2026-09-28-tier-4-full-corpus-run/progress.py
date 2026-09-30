"""Where a live paperhanger run stands, in estimated seconds rather than photos.

    uv run python docs/research/2026-09-28-tier-4-full-corpus-run/progress.py \\
        --log RUN_DIR/run.log --input RUN_DIR/input --originals RUN_DIR/processing/originals

paperhanger runs the photos that need no model first (`execute.cheapest_first`), so the
share of photos done says little about the share of time done. This rebuilds the plan,
charges each photo its model cost (fallbacks included) plus a flat 5 s, and reports the
estimated seconds done against the total, the real/estimated rate so far, an ETA,
failures, how long the log has been silent, free disk and memory pressure. Reads only.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import _instruments as ins
from _instruments import execute, lib

LINE = re.compile(r"^\[(\d+)/(\d+)\] (.+): (\w+) \((\d+) written\)$")
START = re.compile(r"^START (\S+) ")
EXIT = re.compile(r"^EXIT=(\d+)")
OVERHEAD_S = 5.0


def pressure() -> str:
    try:
        out = subprocess.run(["memory_pressure", "-Q"], capture_output=True, text=True,
                             timeout=10).stdout
        m = re.search(r"free percentage:\s*(\d+)%", out)
        free = int(m.group(1)) if m else None
        return "unknown" if free is None else "critical" if free < 10 else "warn" if free < 20 else "normal"
    except Exception:
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description="Progress of a live paperhanger run.")
    ap.add_argument("--log", type=Path, required=True)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--originals", type=Path, required=True)
    args = ap.parse_args()

    text = args.log.read_text(errors="replace").splitlines()
    starts = [i for i, l in enumerate(text) if START.match(l)]
    run = text[starts[-1]:] if starts else text
    started = datetime.fromisoformat(START.match(run[0])[1]).timestamp() if starts else None

    finished, failures, exit_code, total = [], [], None, None
    for i, line in enumerate(run):
        if m := LINE.match(line):
            total = int(m[2])
            finished.append(lib.nfc(m[3]))
            if m[4] != "ok":
                failures.append(line)
                failures += [l.strip() for l in run[i + 1:i + 4] if l.startswith("    ")]
        elif m := EXIT.match(line):
            exit_code = int(m[1])

    processing = args.originals.parent
    works = [w for d in (args.input, args.originals) if d.is_dir()
             for w in ins.build_works(d, processing)[0]]
    by_name = {lib.nfc(w.source.name): w for w in works}
    done = set(finished)
    remaining = [w for w in execute.cheapest_first(works)
                 if lib.nfc(w.source.name) not in done and w.source.parent == args.input]
    est = lambda w: ins.model_seconds(w) + OVERHEAD_S
    done_est = sum(est(by_name[n]) for n in finished if n in by_name)
    rem_est = sum(est(w) for w in remaining)
    total_est = done_est + rem_est
    now = time.time()
    elapsed = now - started if started else 0.0
    rate = elapsed / done_est if done_est > 60 else None
    current = remaining[0] if remaining and exit_code is None else None
    log_age = now - args.log.stat().st_mtime
    json.dump({
        "done_photos": len(finished), "total_photos": total or len(finished) + len(remaining),
        "done_est_s": round(done_est), "total_est_s": round(total_est),
        "pct_time": round(100 * done_est / total_est, 1) if total_est else 100.0,
        "elapsed_s": round(elapsed), "rate": round(rate, 3) if rate else None,
        "eta_s": round(rem_est * rate) if rate else None,
        "current": current.source.name if current else None,
        "current_est_s": round(est(current)) if current else 0, "log_age_s": round(log_age),
        "stalled": bool(current) and log_age > 3 * max(est(current), 120) + 600,
        "failures": failures, "exit": exit_code,
        "free_gb": round(shutil.disk_usage(args.log.parent).free / 1e9, 1),
        "memory_pressure": pressure(),
    }, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
