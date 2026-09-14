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
                    from_cfstring, raw_rgb, DecodeFailed)


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


class RunFailed(RuntimeError):
    """One of the two tools produced no output for a shape."""


def compare(src, w, h):
    """(differing bytes, max delta) for one shape, or raise.

    It raises rather than returning a sentinel because it used to return
    `None` when an output was missing, and neither caller distinguished that
    from agreement: the first pass dropped the file silently and the second
    filed it as a RESAMPLER difference. That is how
    `misty_forest_landscape_2028.jpg` was misclassified. A tool that does not
    run is not a tool that agrees.
    """
    s_out = os.path.join(SCRATCH, "s.png")
    c_out = os.path.join(SCRATCH, "c.png")
    for p in (s_out, c_out):
        if os.path.exists(p):
            os.remove(p)
    sips = subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(h),
                           str(w), src, "-s", "format", "png", "--out", s_out],
                          capture_output=True)
    cgr = subprocess.run([sys.executable, os.path.join(HERE, "resize.py"), src,
                          c_out, str(w), str(h), "3"], capture_output=True)
    for tag, path, done in (("sips", s_out, sips), ("cg", c_out, cgr)):
        if not (os.path.exists(path) and os.path.getsize(path)):
            raise RunFailed("%s produced no output for %s at %dx%d: rc=%d %s"
                            % (tag, src, w, h, done.returncode,
                               (done.stderr or b"").decode("utf-8",
                                                           "replace").strip()))
    a, b = raw_rgb(s_out, w, h), raw_rgb(c_out, w, h)
    if a == b:
        return (0, 0)
    n = sum(1 for i in range(len(a)) if a[i] != b[i])
    mx = max(abs(a[i] - b[i]) for i in range(len(a)) if a[i] != b[i])
    return (n, mx)


def png_ihdr(path):
    """(colour type, bit depth, interlace) for a PNG, else None."""
    with open(path, "rb") as fh:
        head = fh.read(33)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return head[25], head[24], head[28]


def main(root, w, h):
    names = [n for n in sorted(os.listdir(root))
             if os.path.isfile(os.path.join(root, n)) and not n.startswith(".")]
    bad, failed, unreadable = [], [], []
    for n in names:
        p = os.path.join(root, n)
        got = probe(p)
        if not got:
            unreadable.append(n)
            continue
        try:
            res = compare(p, w, h)
        except (RunFailed, DecodeFailed) as error:
            failed.append((n, str(error)))
            continue
        if res[0]:
            bad.append((n, p, got[0], got[1], res))

    examined = len(names) - len(unreadable) - len(failed)
    print("shape %dx%d, %d files, %d unreadable, %d FAILED to run, "
          "%d examined, %d diverge\n"
          % (w, h, len(names), len(unreadable), len(failed), examined,
             len(bad)))
    for n, why in failed:
        print("  FAILED %-44s %s" % (n[:44], why))
    print("%-44s %-6s %-13s %-11s %10s %5s"
          % ("image", "kind", "source", "y scale", "bytes", "maxd"))
    for n, p, (sw, sh), uti, (cnt, mx) in bad:
        print("%-44s %-6s %-13s %-11.6f %10d %5d"
              % (n[:44], uti.split(".")[-1], "%dx%d" % (sw, sh), h / sh,
                 cnt, mx))

    print("\nsame shape through a lossless PNG intermediate "
          "(isolates the container read from the resize):")
    decode, other, unattributable = [], [], []
    for n, p, (sw, sh), uti, _ in bad:
        mid = os.path.join(SCRATCH, "mid.png")
        if os.path.exists(mid):
            os.remove(mid)
        # color-type=2 matters: without it magick palettises any source with
        # 256 colours or fewer, ImageIO decodes an Indexed colour space, and
        # CGBitmapContextCreate returns NULL -- see section 5.
        subprocess.run(["magick", p, "-strip", "-define", "png:color-type=2",
                        mid], capture_output=True)
        ihdr = png_ihdr(p)
        if ihdr and ihdr[0] in (4, 6):
            # png:color-type=2 composites the alpha onto the background, so
            # the intermediate is a DIFFERENT PICTURE for these sources and
            # the test below cannot attribute anything. Say so rather than
            # printing a number about another image.
            unattributable.append(n)
            print("  %-44s alpha-bearing source (colour type %d): the "
                  "intermediate flattens it, NOT ATTRIBUTABLE"
                  % (n[:44], ihdr[0]))
            continue
        try:
            res = compare(mid, w, h)
        except (RunFailed, DecodeFailed) as error:
            failed.append((n, str(error)))
            print("  %-44s FAILED: %s" % (n[:44], error))
            continue
        if res[0] == 0:
            decode.append(n)
            print("  %-44s agrees -> DECODE difference" % n[:44])
        else:
            other.append(n)
            print("  %-44s still differs -> not a decode difference, "
                  "%d bytes" % (n[:44], res[0]))

    print("\ndecode differences        : %d" % len(decode))
    print("not decode differences    : %d" % len(other))
    print("not attributable (alpha)  : %d" % len(unattributable))

    print("\nthe same sources at an aspect-PRESERVING shape (half size):")
    for n, p, (sw, sh), uti, _ in bad:
        try:
            res = compare(p, sw // 2, sh // 2)
        except (RunFailed, DecodeFailed) as error:
            print("  %-44s FAILED: %s" % (n[:44], error))
            continue
        print("  %-44s %dx%d -> %d bytes"
              % (n[:44], sw // 2, sh // 2, res[0]))

    if failed:
        print("\n%d comparison(s) FAILED TO RUN -- these are not agreements"
              % len(failed))
        raise SystemExit(1)


if __name__ == "__main__":
    root = sys.argv[1]
    w = int(sys.argv[2]) if len(sys.argv) > 2 else 800
    h = int(sys.argv[3]) if len(sys.argv) > 3 else 600
    main(root, w, h)
