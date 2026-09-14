"""For one shape, compare every CG interpolation level against sips."""
import sys
import os
import atexit
import shutil
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
# Intermediates go to a temp directory, never beside this script: the repo is
# public and these files are derived from corpus photographs.
SCRATCH = tempfile.mkdtemp(prefix="cg-preflight-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
sys.path.insert(0, HERE)
from cgbase import raw_rgb                                   # noqa: E402
LEVELS = [(0, "Default"), (1, "None"), (2, "Low"), (3, "High"), (4, "Medium")]


src, w, h = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
s_out = os.path.join(SCRATCH, "lv_s.png")
subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(h), str(w), src,
                "-s", "format", "png", "--out", s_out], capture_output=True)
ref = raw_rgb(s_out, w, h)
print("shape %dx%d from %s" % (w, h, src))
for code, name in LEVELS:
    c_out = os.path.join(SCRATCH, "lv_c%d.png" % code)
    subprocess.run([sys.executable, os.path.join(HERE, "resize.py"), src,
                    c_out, str(w), str(h), str(code)], check=True)
    got = raw_rgb(c_out, w, h)
    if got == ref:
        print("  interp %d (%-7s): SAME as sips" % (code, name))
    else:
        n = sum(1 for i in range(len(ref)) if ref[i] != got[i])
        mx = max(abs(ref[i] - got[i]) for i in range(len(ref))
                 if ref[i] != got[i])
        print("  interp %d (%-7s): DIFFER  bytes=%d maxdelta=%d"
              % (code, name, n, mx))
