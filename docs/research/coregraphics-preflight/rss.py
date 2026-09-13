"""Repeat the peak-RSS comparison so the spread is visible, not assumed.

Usage: python3 rss.py <source.png> <out_w> <out_h> <runs>
"""
import sys
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PAT = re.compile(r"(\d+)\s+maximum resident set size")


def peak(argv):
    r = subprocess.run(["/usr/bin/time", "-l"] + argv, capture_output=True)
    m = PAT.search(r.stderr.decode())
    return int(m.group(1)) if m else None


src, w, h, runs = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), \
    int(sys.argv[4])
sips_out = os.path.join(HERE, "rss_s.png")
cg_out = os.path.join(HERE, "rss_c.png")

sips_runs, cg_runs = [], []
for _ in range(runs):
    sips_runs.append(peak(["/usr/bin/sips", "--resampleHeightWidth", str(h),
                           str(w), src, "-s", "format", "png", "--out",
                           sips_out]))
    cg_runs.append(peak([sys.executable, os.path.join(HERE, "resize.py"), src,
                         cg_out, str(w), str(h), "3"]))

MIB = 1024 * 1024
print("source %s -> %dx%d, %d runs each" % (src, w, h, runs))
print("  sips peak RSS (B): %s" % sips_runs)
print("  cg   peak RSS (B): %s" % cg_runs)
print("  sips min %d (%.1f MiB)  max %d (%.1f MiB)"
      % (min(sips_runs), min(sips_runs) / MIB, max(sips_runs),
         max(sips_runs) / MIB))
print("  cg   min %d (%.1f MiB)  max %d (%.1f MiB)"
      % (min(cg_runs), min(cg_runs) / MIB, max(cg_runs), max(cg_runs) / MIB))
print("  difference of minima: %+d B (%+.1f MiB)"
      % (min(cg_runs) - min(sips_runs), (min(cg_runs) - min(sips_runs)) / MIB))
