"""Every corpus source whose pixels differ between the two tools, and why.

Resizes all 894 to one shape, collects the sources whose pixels disagree, then
for each one separates the two possible causes:

  * a DECODE difference -- the same shape through a lossless PNG intermediate
    agrees, so the disagreement came from reading the original container;
  * a RESAMPLER difference -- it disagrees through the intermediate too, so
    the shape itself is in one of the divergent scale regions.

It also re-runs each at an aspect-preserving shape, which is what the
production planner mostly asks for.

Usage: python3 divergence_audit.py <corpus dir> [out_w] [out_h]

Read-only. Outputs go to a temporary directory removed at exit.
"""
import sys
import os
import atexit
import shutil
import tempfile
import subprocess
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SCRATCH = tempfile.mkdtemp(prefix="cg-preflight-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)

from cgbase import (cf, io, cfurl, dict_get, cfnumber_int,   # noqa: E402
                    from_cfstring)


def probe(path):
    url = cfurl(path)
    src = io.CGImageSourceCreateWithURL(url, None)
    if not src:
        cf.CFRelease(url)
        return None
    uti = from_cfstring(io.CGImageSourceGetType(src))
    props = io.CGImageSourceCopyPropertiesAtIndex(src, 0, None)
    wh = None
    if props:
        w = cfnumber_int(dict_get(props, "kCGImagePropertyPixelWidth"))
        h = cfnumber_int(dict_get(props, "kCGImagePropertyPixelHeight"))
        if w and h:
            wh = (w, h)
        cf.CFRelease(props)
    cf.CFRelease(src)
    cf.CFRelease(url)
    return (wh, uti) if wh else None


def raw(path):
    return subprocess.run(["magick", path, "-depth", "8", "RGB:-"],
                          capture_output=True).stdout


def compare(src, w, h):
    s_out = os.path.join(SCRATCH, "s.png")
    c_out = os.path.join(SCRATCH, "c.png")
    for p in (s_out, c_out):
        if os.path.exists(p):
            os.remove(p)
    subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(h), str(w),
                    src, "-s", "format", "png", "--out", s_out],
                   capture_output=True)
    subprocess.run([sys.executable, os.path.join(HERE, "resize.py"), src,
                    c_out, str(w), str(h), "3"], capture_output=True)
    if not (os.path.exists(s_out) and os.path.exists(c_out)):
        return None
    a, b = raw(s_out), raw(c_out)
    if a == b:
        return (0, 0)
    n = sum(1 for i in range(len(a)) if a[i] != b[i])
    mx = max(abs(a[i] - b[i]) for i in range(len(a)) if a[i] != b[i])
    return (n, mx)


def main(root, w, h):
    names = [n for n in sorted(os.listdir(root))
             if os.path.isfile(os.path.join(root, n)) and not n.startswith(".")]
    bad = []
    for n in names:
        p = os.path.join(root, n)
        got = probe(p)
        if not got:
            continue
        res = compare(p, w, h)
        if res and res[0]:
            bad.append((n, p, got[0], got[1], res))

    print("shape %dx%d, %d sources examined, %d diverge\n" % (w, h, len(names),
                                                              len(bad)))
    print("%-44s %-6s %-13s %-11s %10s %5s"
          % ("image", "kind", "source", "y scale", "bytes", "maxd"))
    for n, p, (sw, sh), uti, (cnt, mx) in bad:
        print("%-44s %-6s %-13s %-11.6f %10d %5d"
              % (n[:44], uti.split(".")[-1], "%dx%d" % (sw, sh), h / sh,
                 cnt, mx))

    print("\nsame shape through a lossless PNG intermediate "
          "(isolates decode from resampler):")
    decode, resampler = [], []
    for n, p, (sw, sh), uti, _ in bad:
        mid = os.path.join(SCRATCH, "mid.png")
        if os.path.exists(mid):
            os.remove(mid)
        # color-type=2 matters: without it magick palettises any source with
        # 256 colours or fewer, ImageIO decodes an Indexed colour space, and
        # CGBitmapContextCreate returns NULL -- see section 5. The comparison
        # then silently reports a failure as a difference.
        subprocess.run(["magick", p, "-strip", "-define", "png:color-type=2",
                        mid], capture_output=True)
        res = compare(mid, w, h)
        (decode if res and res[0] == 0 else resampler).append(n)
        print("  %-44s %s" % (n[:44],
                              "agrees -> DECODE difference" if res
                              and res[0] == 0
                              else "still differs -> RESAMPLER, %d bytes"
                              % (res[0] if res else -1)))

    print("\ndecode differences   : %d" % len(decode))
    print("resampler differences: %d" % len(resampler))

    print("\nthe same sources at an aspect-PRESERVING shape (half size):")
    for n, p, (sw, sh), uti, _ in bad:
        res = compare(p, sw // 2, sh // 2)
        print("  %-44s %dx%d -> %d bytes"
              % (n[:44], sw // 2, sh // 2, res[0] if res else -1))


if __name__ == "__main__":
    root = sys.argv[1]
    w = int(sys.argv[2]) if len(sys.argv) > 2 else 800
    h = int(sys.argv[3]) if len(sys.argv) > 3 else 600
    main(root, w, h)
