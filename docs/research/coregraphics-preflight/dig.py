"""Characterise a resize divergence: where the differing pixels sit and by how much."""
import sys
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def raw(path, w, h):
    out = subprocess.run(["magick", path, "-depth", "8", "RGB:-"],
                         capture_output=True)
    d = out.stdout
    assert len(d) == w * h * 3, (len(d), w * h * 3)
    return d


def run(source, w, h, tag):
    s_out = os.path.join(HERE, "dig_s_%s.png" % tag)
    c_out = os.path.join(HERE, "dig_c_%s.png" % tag)
    subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(h), str(w),
                    source, "-s", "format", "png", "--out", s_out],
                   capture_output=True)
    subprocess.run([sys.executable, os.path.join(HERE, "resize.py"), source,
                    c_out, str(w), str(h), "3"], check=True)
    return raw(s_out, w, h), raw(c_out, w, h)


def report(a, b, w, h, label):
    diffs = [i for i in range(len(a)) if a[i] != b[i]]
    print("%s: %d differing bytes of %d" % (label, len(diffs), len(a)))
    if not diffs:
        return
    mx = max(abs(a[i] - b[i]) for i in diffs)
    rows = sorted({(i // 3) // w for i in diffs})
    cols = sorted({(i // 3) % w for i in diffs})
    print("  max abs channel delta: %d" % mx)
    print("  distinct rows touched: %d  (min %d, max %d) of %d"
          % (len(rows), rows[0], rows[-1], h))
    print("  distinct cols touched: %d  (min %d, max %d) of %d"
          % (len(cols), cols[0], cols[-1], w))
    print("  first rows: %s" % rows[:12])
    print("  last  rows: %s" % rows[-12:])
    hist = {}
    for i in diffs:
        d = abs(a[i] - b[i])
        hist[d] = hist.get(d, 0) + 1
    print("  delta histogram: %s"
          % sorted(hist.items(), key=lambda kv: -kv[1])[:8])


if __name__ == "__main__":
    src = sys.argv[1]
    w, h = int(sys.argv[2]), int(sys.argv[3])
    s1, c1 = run(src, w, h, "a")
    report(s1, c1, w, h, "sips vs cg, run 1")
    s2, c2 = run(src, w, h, "b")
    report(s2, c2, w, h, "sips vs cg, run 2")
    print("sips run1 == sips run2:", s1 == s2)
    print("cg   run1 == cg   run2:", c1 == c2)
    print("cg run1 == sips run2  :", c1 == s2)
