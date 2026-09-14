"""Locate the reduction divergence boundary by vertical scale.

Usage: python3 boundary.py <source> <sw> <sh> <out_width> <h_lo> <h_hi>
Holds the output width fixed and walks the output height, reporting the
vertical scale at which sips and CoreGraphics stop agreeing.
"""
import sys
import os
import atexit
import shutil
import tempfile
import subprocess
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
# Intermediates go to a temp directory, never beside this script: the repo is
# public and these files are derived from corpus photographs.
SCRATCH = tempfile.mkdtemp(prefix="cg-preflight-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
sys.path.insert(0, HERE)
from cgbase import raw_rgb                                   # noqa: E402


def compare(source, w, h):
    s_out = os.path.join(SCRATCH, "bd_s.png")
    c_out = os.path.join(SCRATCH, "bd_c.png")
    for p in (s_out, c_out):
        if os.path.exists(p):
            os.remove(p)
    subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(h), str(w),
                    source, "-s", "format", "png", "--out", s_out],
                   capture_output=True)
    subprocess.run([sys.executable, os.path.join(HERE, "resize.py"), source,
                    c_out, str(w), str(h), "3"], check=True)
    a, b = raw_rgb(s_out, w, h), raw_rgb(c_out, w, h)
    if a == b:
        return "SAME", 0, 0
    n = sum(1 for i in range(len(a)) if a[i] != b[i])
    mx = max(abs(a[i] - b[i]) for i in range(len(a)) if a[i] != b[i])
    return "DIFFER", n, mx


src, sw, sh = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
w = int(sys.argv[4])
lo, hi = int(sys.argv[5]), int(sys.argv[6])
print("source %s (%dx%d), output width held at %d (x scale %.6f)"
      % (src, sw, sh, w, w / sw))
print("%-12s %-12s %-10s %-8s %10s %6s"
      % ("out height", "y scale", "as a ratio", "verdict", "bytes", "maxd"))
for h in range(lo, hi + 1):
    v, n, mx = compare(src, w, h)
    print("%-12d %-12.6f %-10s %-8s %10d %6d"
          % (h, h / sh, str(Fraction(h, sh)), v, n, mx))
