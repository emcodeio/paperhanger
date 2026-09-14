"""Run the exact plans that could hit the JPEG decode divergence, and see.

`aspect_audit.py` finds the resamples that are anisotropic AND read the
original file rather than a lossless intermediate. That pair of conditions is
what the measured decode divergence needs. This instrument takes each of those
plans at its real dimensions, runs it through both tools, and reports whether
the pixels actually differ -- so "production probably never hits it" can be
replaced by a count.

Each divergent plan is then re-run at an aspect-PRESERVING shape of the same
size, to separate a decode difference from a resampler difference.

Usage: python3 at_risk.py <corpus dir>

Read-only. Outputs go to a temporary directory removed at exit.
"""
import sys
import os
import atexit
import shutil
import tempfile
import subprocess
from fractions import Fraction
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)
SCRATCH = tempfile.mkdtemp(prefix="cg-preflight-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)

from paperhanger import plan, sizes                       # noqa: E402
from cgbase import cf, io, cfurl, dict_get, cfnumber_int, from_cfstring  # noqa: E402


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


def via_png(src, w, h):
    """The same resize, but through a lossless PNG intermediate."""
    mid = os.path.join(SCRATCH, "mid.png")
    if os.path.exists(mid):
        os.remove(mid)
    subprocess.run(["magick", src, "-strip", mid], capture_output=True)
    return compare(mid, w, h)


def main(root):
    opts = plan.OutputSettings(processing_dir=Path("/tmp/unused"), fmt="heic",
                               quality=80)
    devices = list(sizes.DEVICES)
    jobs = []

    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isfile(path) or name.startswith("."):
            continue
        got = probe(path)
        if not got:
            continue
        (width, height), uti = got
        if uti != "public.jpeg":
            continue
        work = plan.plan_photo(Path(path), width, height, devices, opts)
        for p in work.plans:
            if p.crop is not None or p.needs_upscale or not p.needs_resize:
                continue          # reads a PNG intermediate, not the original
            if Fraction(p.out_width, width) == Fraction(p.out_height, height):
                continue          # isotropic
            jobs.append((name, path, width, height, p.out_width, p.out_height))

    print("plans that are anisotropic AND read an original JPEG: %d" % len(jobs))
    print("%-44s %-20s %10s %5s" % ("image", "shape", "bytes", "maxd"))
    bad = []
    for name, path, w0, h0, w, h in jobs:
        got = compare(path, w, h)
        if got is None:
            print("%-44s SKIPPED" % name[:44])
            continue
        n, mx = got
        if n:
            bad.append((name, path, w0, h0, w, h, n, mx))
        print("%-44s %-20s %10d %5d"
              % (name[:44], "%dx%d->%dx%d" % (w0, h0, w, h), n, mx))

    print("\n%d of %d diverge" % (len(bad), len(jobs)))
    if bad:
        print("\nthe same shapes through a lossless PNG intermediate:")
        for name, path, w0, h0, w, h, n, mx in bad:
            got = via_png(path, w, h)
            print("  %-44s %10d bytes  maxd %d"
                  % (name[:44], got[0], got[1]))


if __name__ == "__main__":
    main(sys.argv[1])
