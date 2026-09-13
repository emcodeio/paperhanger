"""Sweep resize shapes, reporting pixel equality of CoreGraphics against sips."""
import sys
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def raw(path):
    return subprocess.run(["magick", path, "-depth", "8", "RGB:-"],
                          capture_output=True).stdout


def compare(source, w, h):
    s_out = os.path.join(HERE, "sw_s.png")
    c_out = os.path.join(HERE, "sw_c.png")
    for p in (s_out, c_out):
        if os.path.exists(p):
            os.remove(p)
    subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(h), str(w),
                    source, "-s", "format", "png", "--out", s_out],
                   capture_output=True)
    subprocess.run([sys.executable, os.path.join(HERE, "resize.py"), source,
                    c_out, str(w), str(h), "3"], check=True)
    a, b = raw(s_out), raw(c_out)
    if a == b:
        return "SAME", 0, 0
    n = sum(1 for i in range(len(a)) if a[i] != b[i])
    mx = max(abs(a[i] - b[i]) for i in range(len(a)) if a[i] != b[i])
    return "DIFFER", n, mx


def main(source, sw, sh, shapes):
    print("source %s (%dx%d)" % (source, sw, sh))
    print("%-12s %-7s %-7s %-8s %10s %6s"
          % ("shape", "xscale", "yscale", "verdict", "bytes", "maxd"))
    for w, h in shapes:
        v, n, mx = compare(source, w, h)
        print("%-12s %-7.4f %-7.4f %-8s %10d %6d"
              % ("%dx%d" % (w, h), w / sw, h / sh, v, n, mx))


if __name__ == "__main__":
    src, sw, sh = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    shapes = [tuple(int(x) for x in a.split("x")) for a in sys.argv[4:]]
    main(src, sw, sh, shapes)
