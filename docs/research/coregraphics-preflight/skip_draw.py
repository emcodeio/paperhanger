"""At 1:1, is drawing the same as not drawing? And which one matches sips?

`render` asks the resampler for the source's own dimensions 577 times over the
corpus. A CoreGraphics implementation can serve that two ways: draw the image
into a bitmap context of the same size (what `resize.py` does), or hand the
decoded `CGImage` straight to the destination and never draw at all. This
instrument writes all three outputs for one shape --

    sips   /usr/bin/sips --resampleHeightWidth
    draw   CGBitmapContextCreate + CGContextDrawImage, then encode
    skip   CGImageSourceCreateImageAtIndex, then encode

-- and compares every pair, at the container level (`IHDR` and the concatenated
`IDAT` stream) and, unless --no-pixels is given, on decoded pixels.

The container comparison is the primary one and it runs with no `magick` in the
loop, because the question this settles was got wrong once already by a test
that could not distinguish its two hypotheses: routing a source through a
lossless PNG intermediate is exactly what makes the draw a no-op, so "they
agree through the intermediate" is what BOTH "the decode differs" and "the draw
differs" predict. This compares the three real outputs instead.

Usage: python3 skip_draw.py <source> [width height] [--no-pixels]
       python3 skip_draw.py <corpus dir> --sweep [--limit=N] [--max-pixels=M]

With no dimensions it uses the source's own, which is the identity case.
`--sweep` runs the identity case over a whole directory, comparing `IDAT` only,
and counts how often the draw is a no-op -- which is the question, because a
count of two fixtures was generalised into a rule once already.
Read-only. Outputs go to a temporary directory removed at exit.
"""
import sys
import os
import atexit
import shutil
import struct
import hashlib
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SCRATCH = tempfile.mkdtemp(prefix="cg-preflight-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)

from cgbase import (cf, cg, io, cfurl, cfstr, uti_for, dict_get,  # noqa: E402
                    cfnumber_int, raw_pixels, DecodeFailed)
from resize import resize                                        # noqa: E402


def dimensions(path):
    url = cfurl(path)
    src = io.CGImageSourceCreateWithURL(url, None)
    if not src:
        cf.CFRelease(url)
        raise SystemExit("cannot open %s" % path)
    props = io.CGImageSourceCopyPropertiesAtIndex(src, 0, None)
    wh = None
    if props:
        w = cfnumber_int(dict_get(props, "kCGImagePropertyPixelWidth"))
        h = cfnumber_int(dict_get(props, "kCGImagePropertyPixelHeight"))
        wh = (w, h)
        cf.CFRelease(props)
    cf.CFRelease(src)
    cf.CFRelease(url)
    return wh


def skip(src_path, dst_path):
    """Encode the decoded image without drawing it anywhere.

    This is the whole of what "skip the draw" means: no bitmap context, no
    `CGContextDrawImage`, no destination colour space of our choosing. The
    image ImageIO produced goes to the destination as it is.
    """
    src_url = cfurl(src_path)
    source = io.CGImageSourceCreateWithURL(src_url, None)
    if not source:
        raise SystemExit("cannot open source: %s" % src_path)
    image = io.CGImageSourceCreateImageAtIndex(source, 0, None)
    if not image:
        raise SystemExit("cannot decode source: %s" % src_path)
    dst_url = cfurl(dst_path)
    uti = cfstr(uti_for(dst_path))
    dest = io.CGImageDestinationCreateWithURL(dst_url, uti, 1, None)
    if not dest:
        raise SystemExit("CGImageDestinationCreateWithURL returned NULL")
    io.CGImageDestinationAddImage(dest, image, None)
    ok = io.CGImageDestinationFinalize(dest)
    cf.CFRelease(dest)
    cf.CFRelease(uti)
    cf.CFRelease(dst_url)
    cg.CGImageRelease(image)
    cf.CFRelease(source)
    cf.CFRelease(src_url)
    if not ok:
        raise SystemExit("CGImageDestinationFinalize failed")


def chunks(path):
    with open(path, "rb") as fh:
        d = fh.read()
    i, out = 8, []
    while i + 8 <= len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]
        t = d[i + 4:i + 8].decode("latin1")
        out.append((t, d[i + 8:i + 8 + ln]))
        i += 12 + ln
        if t == "IEND":
            break
    return out


def idat_sha(path):
    return hashlib.sha256(
        b"".join(p for t, p in chunks(path) if t == "IDAT")).hexdigest()


def ihdr(path):
    head = open(path, "rb").read(33)
    w, h = struct.unpack(">II", head[16:24])
    return "%dx%d ctype=%d depth=%d interlace=%d" % (w, h, head[25], head[24],
                                                     head[28])


def pixel_diff(a, b, w, h):
    ra, rb = raw_pixels(a, w, h), raw_pixels(b, w, h)
    if ra == rb:
        return 0, 0
    n = sum(1 for i in range(len(ra)) if ra[i] != rb[i])
    mx = max(abs(ra[i] - rb[i]) for i in range(len(ra)) if ra[i] != rb[i])
    return n, mx


def main(src, w, h, pixels=True):
    src_w, src_h = dimensions(src)
    out = {k: os.path.join(SCRATCH, "%s.png" % k)
           for k in ("sips", "draw", "skip")}
    done = subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(h),
                           str(w), src, "-s", "format", "png", "--out",
                           out["sips"]], capture_output=True)
    if not (os.path.exists(out["sips"]) and os.path.getsize(out["sips"])):
        raise SystemExit("sips wrote nothing: rc=%d %s"
                         % (done.returncode,
                            (done.stderr or b"").decode("utf-8", "replace")))
    resize(src, out["draw"], w, h, 3)
    if (w, h) == (src_w, src_h):
        skip(src, out["skip"])
    else:
        out.pop("skip")

    print("source %s  %dx%d -> %dx%d%s"
          % (os.path.basename(src), src_w, src_h, w, h,
             "   (identity)" if (w, h) == (src_w, src_h) else ""))
    for k, path in out.items():
        print("  %-5s %-34s IDAT %s  %d bytes on disk"
              % (k, ihdr(path), idat_sha(path)[:16], os.path.getsize(path)))

    keys = list(out)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            same_idat = idat_sha(out[a]) == idat_sha(out[b])
            line = "  %-4s vs %-4s : IDAT %s" % (a, b,
                                                 "SAME" if same_idat else "DIFFER")
            if pixels:
                try:
                    n, mx = pixel_diff(out[a], out[b], w, h)
                    line += ("   pixels %s" % "IDENTICAL" if n == 0 else
                             "   pixels differ %d of %d bytes (%.1f%%), maxdelta %d"
                             % (n, w * h * 3, 100.0 * n / (w * h * 3), mx))
                except DecodeFailed as error:
                    line += "   pixel compare FAILED: %s" % error
            print(line)
    return 0


def sweep(root, limit, max_pixels, offset=0):
    """The identity case over a directory: how often is the draw a no-op?

    `offset` exists because the first alphabetical slice of this corpus is a
    run of near-duplicates -- twenty `acrylic_*` frames at one size -- and a
    count taken there is a count of one camera roll, not of the corpus.
    """
    names = [n for n in sorted(os.listdir(root))
             if os.path.isfile(os.path.join(root, n)) and not n.startswith(".")]
    names = names[offset:]
    tally = {"all three same": 0, "draw differs from skip": 0,
             "sips differs from skip": 0, "skipped (too large)": 0,
             "failed": 0}
    rows = []
    done = 0
    for n in names:
        if limit and done >= limit:
            break
        src = os.path.join(root, n)
        try:
            w, h = dimensions(src)
        except SystemExit:
            tally["failed"] += 1
            continue
        if w * h > max_pixels:
            tally["skipped (too large)"] += 1
            continue
        out = {k: os.path.join(SCRATCH, "sw_%s.png" % k)
               for k in ("sips", "draw", "skip")}
        for path in out.values():
            if os.path.exists(path):
                os.remove(path)
        subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(h),
                        str(w), src, "-s", "format", "png", "--out",
                        out["sips"]], capture_output=True)
        try:
            resize(src, out["draw"], w, h, 3)
            skip(src, out["skip"])
        except SystemExit as error:
            tally["failed"] += 1
            rows.append((n, "FAILED: %s" % error))
            continue
        if not all(os.path.exists(p) and os.path.getsize(p)
                   for p in out.values()):
            tally["failed"] += 1
            rows.append((n, "FAILED: an output is missing or empty"))
            continue
        done += 1
        sh, dh, kh = (idat_sha(out["sips"]), idat_sha(out["draw"]),
                      idat_sha(out["skip"]))
        if sh == dh == kh:
            tally["all three same"] += 1
            continue
        if dh != kh:
            tally["draw differs from skip"] += 1
        if sh != kh:
            tally["sips differs from skip"] += 1
        rows.append((n, "%dx%d  sips==skip %s  draw==skip %s"
                     % (w, h, sh == kh, dh == kh)))

    print("identity draws run: %d (of %d files from offset %d)"
          % (done, len(names), offset))
    for k, v in tally.items():
        print("    %-24s %d" % (k, v))
    if rows:
        print("\nevery source that is not all-three-same:")
        for n, why in rows:
            print("    %-44s %s" % (n[:44], why))
    return 1 if tally["failed"] else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--sweep" in sys.argv[1:]:
        lim = next((int(a.split("=")[1]) for a in sys.argv[1:]
                    if a.startswith("--limit=")), 0)
        cap = next((int(a.split("=")[1]) for a in sys.argv[1:]
                    if a.startswith("--max-pixels=")), 20_000_000)
        off = next((int(a.split("=")[1]) for a in sys.argv[1:]
                    if a.startswith("--offset=")), 0)
        raise SystemExit(sweep(args[0], lim, cap, off))
    path = args[0]
    if len(args) >= 3:
        width, height = int(args[1]), int(args[2])
    else:
        width, height = dimensions(path)
    raise SystemExit(main(path, width, height,
                          pixels="--no-pixels" not in sys.argv[1:]))
