"""Run every plan that resamples straight from an original file, and see.

The population is defined by `execute.render`, not by a theory about what
provokes a divergence. `render` hands `resize_and_encode` the original file
when the plan has no crop rect (nothing was written to an intermediate) and no
upscale (so `scale` is 1 and the source is `work.source`), and it resamples
when `needs_resize` -- band 1. Every other plan reads a PNG the crop or the
upscaler wrote, and on those two tools that disagree about JPEG decoding
cannot disagree.

An earlier version of this instrument ran a subset of that population: the
plans that were ALSO anisotropic, on the theory that a distorting shape is
what provokes the divergence. That was 69 of 159, and the 90 it left out
contain all three of the divergences in the corpus. The theory was wrong and
the predicate is now mechanical: if the resampler reads the original, run it.

Each divergent plan is then re-run through a lossless PNG intermediate, which
separates a container-read difference from a resize difference.

Usage: python3 at_risk.py <corpus dir> [--list] [--jobs N]

Read-only. Outputs go to a temporary directory removed at exit.
"""
import sys
import os
import atexit
import shutil
import tempfile
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)
SCRATCH = tempfile.mkdtemp(prefix="cg-preflight-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)

from paperhanger import plan, sizes                       # noqa: E402
from cgbase import (cf, io, cfurl, dict_get, cfnumber_int,  # noqa: E402
                    from_cfstring, raw_rgb, DecodeFailed)

_local = threading.local()


class RunFailed(RuntimeError):
    """One of the two tools produced no output for a shape."""


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


def _slot():
    """A scratch name per thread, so parallel jobs cannot overwrite each other."""
    if not hasattr(_local, "tag"):
        _local.tag = "t%d" % threading.get_ident()
    return _local.tag


def compare(src, w, h):
    """(differing bytes, max delta) for one shape, or raise.

    It raises rather than returning a sentinel. A missing output is not an
    agreement, and the summary line below counts only jobs that ran.
    """
    tag = _slot()
    s_out = os.path.join(SCRATCH, "s_%s.png" % tag)
    c_out = os.path.join(SCRATCH, "c_%s.png" % tag)
    for p in (s_out, c_out):
        if os.path.exists(p):
            os.remove(p)
    sips = subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(h),
                           str(w), src, "-s", "format", "png", "--out", s_out],
                          capture_output=True)
    cgr = subprocess.run([sys.executable, os.path.join(HERE, "resize.py"), src,
                          c_out, str(w), str(h), "3"], capture_output=True)
    for name, path, done in (("sips", s_out, sips), ("cg", c_out, cgr)):
        if not (os.path.exists(path) and os.path.getsize(path)):
            raise RunFailed("%s produced no output for %s at %dx%d: rc=%d %s"
                            % (name, src, w, h, done.returncode,
                               (done.stderr or b"").decode("utf-8",
                                                           "replace").strip()))
    a, b = raw_rgb(s_out, w, h), raw_rgb(c_out, w, h)
    if a == b:
        return (0, 0)
    n = sum(1 for i in range(len(a)) if a[i] != b[i])
    mx = max(abs(a[i] - b[i]) for i in range(len(a)) if a[i] != b[i])
    return (n, mx)


def via_png(src, w, h):
    """The same resize, but through a lossless PNG intermediate."""
    mid = os.path.join(SCRATCH, "mid_%s.png" % _slot())
    if os.path.exists(mid):
        os.remove(mid)
    subprocess.run(["magick", src, "-strip", "-define", "png:color-type=2",
                    mid], capture_output=True)
    return compare(mid, w, h)


def collect(root):
    """Every plan whose resampler input is the original file."""
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
        work = plan.plan_photo(Path(path), width, height, devices, opts)
        for p in work.plans:
            # `render`: current is the original when crop is None; scale is 1
            # when the plan needs no upscale, so `resize` is `needs_resize`.
            if p.crop is not None or p.needs_upscale or not p.needs_resize:
                continue
            xs = Fraction(p.out_width, width)
            ys = Fraction(p.out_height, height)
            jobs.append({
                "name": name, "path": path, "src": (width, height),
                "out": (p.out_width, p.out_height), "uti": uti,
                "kind": "isotropic" if xs == ys else "anisotropic",
                "identity": (p.out_width, p.out_height) == (width, height),
                "aniso": abs(float(xs) - float(ys)),
                "device": p.device,
            })
    return jobs


def main(root, list_only=False, workers=4):
    jobs = collect(root)
    kinds = {}
    for j in jobs:
        kinds[j["kind"]] = kinds.get(j["kind"], 0) + 1
    print("plans whose resampler reads the ORIGINAL file: %d" % len(jobs))
    print("  anisotropic : %d" % kinds.get("anisotropic", 0))
    print("  isotropic   : %d" % kinds.get("isotropic", 0))
    print("  of those, identity (out == source on both axes): %d"
          % sum(1 for j in jobs if j["identity"]))
    by_type = {}
    for j in jobs:
        by_type[j["uti"]] = by_type.get(j["uti"], 0) + 1
    print("  source types: %s"
          % ", ".join("%s %d" % (k.split(".")[-1], v)
                      for k, v in sorted(by_type.items())))
    print("  distinct images: %d" % len({j["name"] for j in jobs}))
    if list_only:
        for j in sorted(jobs, key=lambda j: j["name"]):
            print("  %-46s %dx%d -> %dx%d  %s%s"
                  % (j["name"][:46], j["src"][0], j["src"][1], j["out"][0],
                     j["out"][1], j["kind"],
                     ", identity" if j["identity"] else ""))
        return 0

    def run(j):
        try:
            return j, compare(j["path"], j["out"][0], j["out"][1]), None
        except (RunFailed, DecodeFailed) as error:
            return j, None, str(error)

    print("\n%-46s %-22s %-12s %10s %5s"
          % ("image", "shape", "kind", "bytes", "maxd"))
    bad, failed, ran = [], [], 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for j, got, error in pool.map(run, jobs):
            shape = "%dx%d->%dx%d" % (j["src"][0], j["src"][1], j["out"][0],
                                      j["out"][1])
            if error is not None:
                failed.append((j, error))
                print("%-46s %-22s FAILED TO RUN: %s"
                      % (j["name"][:46], shape, error))
                continue
            ran += 1
            n, mx = got
            if n:
                bad.append((j, n, mx))
            print("%-46s %-22s %-12s %10d %5d"
                  % (j["name"][:46], shape,
                     j["kind"] + (", identity" if j["identity"] else ""),
                     n, mx))

    print("\n%d of %d that RAN diverge (%d failed to run, and a failure is "
          "not an agreement)" % (len(bad), ran, len(failed)))
    if bad:
        print("\nthe divergent plans, by kind:")
        for j, n, mx in bad:
            print("  %-46s %-12s %d bytes of %d (%.1f%%), maxdelta %d"
                  % (j["name"][:46],
                     j["kind"] + (", identity" if j["identity"] else ""), n,
                     j["out"][0] * j["out"][1] * 3,
                     100.0 * n / (j["out"][0] * j["out"][1] * 3), mx))
        print("\nthe same shapes through a lossless PNG intermediate:")
        for j, n, mx in bad:
            try:
                got = via_png(j["path"], j["out"][0], j["out"][1])
            except (RunFailed, DecodeFailed) as error:
                print("  %-46s FAILED: %s" % (j["name"][:46], error))
                continue
            print("  %-46s %10d bytes  maxd %d  -> %s"
                  % (j["name"][:46], got[0], got[1],
                     "DECODE difference" if got[0] == 0
                     else "not a decode difference"))
    return 1 if failed else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    n_workers = 4
    for f in flags:
        if f.startswith("--jobs="):
            n_workers = int(f.split("=", 1)[1])
    raise SystemExit(main(args[0], list_only="--list" in flags,
                          workers=n_workers))
