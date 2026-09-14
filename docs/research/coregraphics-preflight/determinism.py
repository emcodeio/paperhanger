"""Does either tool write the same bytes twice for the same input?

Two runs of `sourcecensus.py` over the same corpus printed "whole files
identical: 172" and then "171", with every other line the same. Either a tool
is not deterministic, or whole-file identity is not a stable property to gate
on. This instrument answers it directly: for every source it runs each tool
TWICE at one shape and reports

  * any file where a tool disagrees with itself, whole file and decoded pixels;
  * any file whose sips-against-CoreGraphics identity differs between the two
    rounds -- the flip that moves the count.

Usage: python3 determinism.py <corpus dir> [out_w] [out_h]

Read-only. Outputs go to a temporary directory removed at exit.
"""
import sys
import os
import atexit
import shutil
import hashlib
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SCRATCH = tempfile.mkdtemp(prefix="cg-preflight-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)

from cgbase import raw_rgb, DecodeFailed                      # noqa: E402


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run_pair(src, w, h, tag):
    """One sips output and one CoreGraphics output, named by round."""
    s_out = os.path.join(SCRATCH, "s_%s.png" % tag)
    c_out = os.path.join(SCRATCH, "c_%s.png" % tag)
    for p in (s_out, c_out):
        if os.path.exists(p):
            os.remove(p)
    subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(h), str(w),
                    src, "-s", "format", "png", "--out", s_out],
                   capture_output=True)
    subprocess.run([sys.executable, os.path.join(HERE, "resize.py"), src,
                    c_out, str(w), str(h), "3"], capture_output=True)
    if not all(os.path.exists(p) and os.path.getsize(p) for p in (s_out, c_out)):
        return None
    return s_out, c_out


def main(root, w, h):
    names = [n for n in sorted(os.listdir(root))
             if os.path.isfile(os.path.join(root, n)) and not n.startswith(".")]
    sips_unstable, cg_unstable, flips, failed = [], [], [], []
    identical = [0, 0]
    for n in names:
        src = os.path.join(root, n)
        first = run_pair(src, w, h, "a")
        second = run_pair(src, w, h, "b")
        if first is None or second is None:
            failed.append(n)
            continue
        s1, c1 = first
        s2, c2 = second
        s1h, s2h, c1h, c2h = sha(s1), sha(s2), sha(c1), sha(c2)
        if s1h != s2h:
            try:
                pix = raw_rgb(s1, w, h) == raw_rgb(s2, w, h)
            except DecodeFailed:
                pix = None
            sips_unstable.append((n, pix))
        if c1h != c2h:
            try:
                pix = raw_rgb(c1, w, h) == raw_rgb(c2, w, h)
            except DecodeFailed:
                pix = None
            cg_unstable.append((n, pix))
        same = [s1h == c1h, s2h == c2h]
        identical[0] += same[0]
        identical[1] += same[1]
        if same[0] != same[1]:
            flips.append(n)

    print("corpus %s, shape %dx%d, %d files, %d failed to run"
          % (root, w, h, len(names), len(failed)))
    print("whole files identical, round 1 : %d" % identical[0])
    print("whole files identical, round 2 : %d" % identical[1])
    print("\nsips disagrees with itself     : %d" % len(sips_unstable))
    for n, pix in sips_unstable:
        print("    %-50s pixels same: %s" % (n[:50], pix))
    print("CoreGraphics disagrees with itself: %d" % len(cg_unstable))
    for n, pix in cg_unstable:
        print("    %-50s pixels same: %s" % (n[:50], pix))
    print("\nwhole-file identity flipped between rounds: %d" % len(flips))
    for n in flips:
        print("    %s" % n)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1],
                          int(sys.argv[2]) if len(sys.argv) > 2 else 800,
                          int(sys.argv[3]) if len(sys.argv) > 3 else 600))
