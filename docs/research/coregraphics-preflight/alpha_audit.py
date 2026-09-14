"""What each tool does with an alpha channel, over every PNG in the corpus.

`resize.py` builds its bitmap context with `kCGImageAlphaNoneSkipLast`, which
is three channels and a padding byte: whatever alpha the source carried is
composited away and the result is opaque RGB. `sips` keeps RGBA. So for an
alpha-bearing source the two tools do not produce the same KIND of file, and a
comparison of decoded RGB can pass while the alpha channel is silently gone.

This instrument says, per PNG source: what colour type each tool wrote, and
whether the pixels agree read as RGB and read as RGBA. It exists because a
review round attributed a twelve-byte difference in these files to the
resampler; the twelve bytes are four corner pixels, and they are an alpha
artefact.

Usage: python3 alpha_audit.py <corpus dir> [out_w] [out_h]

Read-only. Outputs go to a temporary directory removed at exit.
"""
import sys
import os
import atexit
import shutil
import struct
import pathlib
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)
SCRATCH = tempfile.mkdtemp(prefix="cg-preflight-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)

from paperhanger import bands, plan, sizes                    # noqa: E402
from cgbase import raw_pixels, DecodeFailed                  # noqa: E402

CTYPE = {0: "gray", 2: "RGB", 3: "indexed", 4: "gray+A", 6: "RGBA"}


def ihdr(path):
    """(width, height, depth, colour type, interlace), or None if not a PNG."""
    with open(path, "rb") as fh:
        head = fh.read(33)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    wide, high = struct.unpack(">II", head[16:24])
    return wide, high, head[24], head[25], head[28]


def both(src, w, h):
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
    for path in (s_out, c_out):
        if not (os.path.exists(path) and os.path.getsize(path)):
            raise RuntimeError("no output for %s at %dx%d" % (src, w, h))
    return s_out, c_out


def corners(buf, w, h, n):
    """The four corner pixels, as tuples of n channels."""
    out = []
    for index in (0, w - 1, (h - 1) * w, h * w - 1):
        out.append(tuple(buf[index * n:index * n + n]))
    return out


def main(root, w, h):
    names = [n for n in sorted(os.listdir(root))
             if n.lower().endswith(".png") and not n.startswith(".")]
    print("PNG sources: %d, resized to %dx%d\n" % (len(names), w, h))
    print("%-44s %-8s %-9s %-9s %-7s %-7s"
          % ("image", "source", "sips out", "cg out", "RGB", "RGBA"))
    alpha_sources, rgb_same_rgba_differ = 0, []
    for n in names:
        src = os.path.join(root, n)
        got = ihdr(src)
        if got is None:
            continue
        s_out, c_out = both(src, w, h)
        s_h, c_h = ihdr(s_out), ihdr(c_out)
        rgb = raw_pixels(s_out, w, h) == raw_pixels(c_out, w, h)
        try:
            rgba = (raw_pixels(s_out, w, h, "RGBA")
                    == raw_pixels(c_out, w, h, "RGBA"))
            rgba_txt = "same" if rgba else "DIFFER"
        except DecodeFailed as error:
            rgba, rgba_txt = None, "FAILED"
            print("  RGBA decode failed: %s" % error)
        if got[3] in (4, 6):
            alpha_sources += 1
        if rgb and rgba is False:
            rgb_same_rgba_differ.append(n)
        print("%-44s %-8s %-9s %-9s %-7s %-7s"
              % (n[:44], CTYPE.get(got[3], got[3]),
                 CTYPE.get(s_h[3], s_h[3]), CTYPE.get(c_h[3], c_h[3]),
                 "same" if rgb else "DIFFER", rgba_txt))
        if not rgb:
            a = raw_pixels(s_out, w, h)
            b = raw_pixels(c_out, w, h)
            diffs = [i for i in range(len(a)) if a[i] != b[i]]
            rows = sorted({(i // 3) // w for i in diffs})
            cols = sorted({(i // 3) % w for i in diffs})
            print("    %d differing RGB bytes, rows %s, cols %s"
                  % (len(diffs), rows[:6], cols[:6]))
            try:
                sa = raw_pixels(s_out, w, h, "RGBA")
                print("    corners sips RGBA %s" % (corners(sa, w, h, 4),))
            except DecodeFailed:
                pass
            print("    corners sips RGB  %s" % (corners(a, w, h, 3),))
            print("    corners cg   RGB  %s" % (corners(b, w, h, 3),))

    print("\nwhat the planner asks of each alpha-bearing source "
          "(does production draw it at all?):")
    opts = plan.OutputSettings(processing_dir=pathlib.Path("/tmp/unused"),
                               fmt="heic",
                               quality=80)
    for n in names:
        src = os.path.join(root, n)
        got = ihdr(src)
        if got is None or got[3] not in (4, 6):
            continue
        work = plan.plan_photo(pathlib.Path(src), got[0], got[1],
                               list(sizes.DEVICES), opts)
        kinds = {}
        for pl in work.plans:
            key = "band %d %s%s" % (pl.band, bands.NAMES[pl.band],
                                    ", cropped" if pl.crop is not None else "")
            kinds[key] = kinds.get(key, 0) + 1
        print("    %-44s %dx%d  %s"
              % (n[:44], got[0], got[1],
                 "; ".join("%s x%d" % (k, v) for k, v in sorted(kinds.items()))
                 or "no plans (rejected)"))

    print("\nalpha-bearing sources (colour type 4 or 6): %d of %d"
          % (alpha_sources, len(names)))
    print("RGB agrees but RGBA differs                : %d%s"
          % (len(rgb_same_rgba_differ),
             ("  " + ", ".join(n[:36] for n in rgb_same_rgba_differ[:6])
              if rgb_same_rgba_differ else "")))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 800,
         int(sys.argv[3]) if len(sys.argv) > 3 else 600)
