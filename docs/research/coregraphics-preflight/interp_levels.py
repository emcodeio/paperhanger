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
LEVELS = [(0, "Default"), (1, "None"), (2, "Low"), (3, "High"), (4, "Medium")]


def raw(path):
    return subprocess.run(["magick", path, "-depth", "8", "RGB:-"],
                          capture_output=True).stdout


src, w, h = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
s_out = os.path.join(SCRATCH, "lv_s.png")
subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(h), str(w), src,
                "-s", "format", "png", "--out", s_out], capture_output=True)
ref = raw(s_out)
print("shape %dx%d from %s" % (w, h, src))
for code, name in LEVELS:
    c_out = os.path.join(SCRATCH, "lv_c%d.png" % code)
    subprocess.run([sys.executable, os.path.join(HERE, "resize.py"), src,
                    c_out, str(w), str(h), str(code)], check=True)
    got = raw(c_out)
    if got == ref:
        print("  interp %d (%-7s): SAME as sips" % (code, name))
    else:
        n = sum(1 for i in range(len(ref)) if ref[i] != got[i])
        mx = max(abs(ref[i] - got[i]) for i in range(len(ref))
                 if ref[i] != got[i])
        print("  interp %d (%-7s): DIFFER  bytes=%d maxdelta=%d"
              % (code, name, n, mx))
