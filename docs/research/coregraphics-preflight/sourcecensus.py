"""What every corpus source carries, and what each tool writes for it.

Runs over the WHOLE corpus by default, not a sample. Three rounds of review on
this document have turned up a universal that a few dozen files supported and
several hundred contradicted, so this instrument exists to make the full count
as cheap as the guess.

For each source it records the container's metadata (ICC profile, and for JPEG
the JFIF/Exif/XMP/Photoshop APP segments), resizes it through both sips and
CoreGraphics, and records what ancillary chunks each output carries, whether
the pixels agree, and whether the whole files agree.

Usage: python3 sourcecensus.py <corpus dir> [limit] [out_w] [out_h] [--identical]

`--identical` also lists every source whose two outputs are byte-identical, which is
how the run-to-run stability of whole-file identity was checked: run it twice and diff
the lists.

`limit` caps the number of files (0 or absent means all of them). The default
shape, 800x600, is aspect-distorting for nearly every corpus image, which is
the condition the JPEG decode divergence needs.

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
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SCRATCH = tempfile.mkdtemp(prefix="cg-preflight-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)

from cgbase import raw_rgb, DecodeFailed                   # noqa: E402

CRITICAL = {"IHDR", "PLTE", "IDAT", "IEND"}


def jpeg_segments(path):
    """What a JPEG's APP segments carry, or None if it is not a JPEG."""
    with open(path, "rb") as fh:
        d = fh.read(1 << 21)
    if not d.startswith(b"\xff\xd8"):
        return None
    f = {"jfif": False, "exif": False, "xmp": False, "photoshop": False,
         "density": None}
    i = 2
    while i + 4 <= len(d) and d[i] == 0xFF:
        m = d[i + 1]
        if m in (0xD8, 0xD9):
            i += 2
            continue
        ln = struct.unpack(">H", d[i + 2:i + 4])[0]
        body = d[i + 4:i + 4 + max(0, ln - 2)]
        if m == 0xE0 and body.startswith(b"JFIF\x00"):
            f["jfif"] = True
            if len(body) >= 12:
                f["density"] = (body[7],) + struct.unpack(">HH", body[8:12])
        elif m == 0xE1 and body.startswith(b"Exif\x00\x00"):
            f["exif"] = True
        elif m == 0xE1 and body.startswith(b"http://ns.adobe.com/xap/"):
            f["xmp"] = True
        elif m == 0xED and body.startswith(b"Photoshop 3.0\x00"):
            f["photoshop"] = True
        if m == 0xDA:
            break
        i += 2 + ln
    return f


def _chunks(path):
    with open(path, "rb") as fh:
        d = fh.read()
    i, out = 8, []
    while i + 8 <= len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]
        t = d[i + 4:i + 8]
        out.append((t.decode("latin1"), d[i + 8:i + 8 + ln]))
        i += 12 + ln
        if t == b"IEND":
            break
    return out


def chunkset(path):
    return {t for t, _ in _chunks(path) if t not in CRITICAL}


def idat_sha(path):
    acc = b"".join(b for t, b in _chunks(path) if t == "IDAT")
    return hashlib.sha256(acc).hexdigest()


def ihdr(path):
    """(width, height, bit depth, colour type, interlace) of a PNG."""
    with open(path, "rb") as fh:
        head = fh.read(33)
    wide, high = struct.unpack(">II", head[16:24])
    return wide, high, head[24], head[25], head[28]


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def resize_both(src, w, h):
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
    return s_out, c_out


def main(root, limit, w, h, list_identical=False):
    names = [n for n in sorted(os.listdir(root))
             if os.path.isfile(os.path.join(root, n)) and not n.startswith(".")]
    if limit:
        names = names[:limit]

    cg_colour = collections.Counter()
    sips_extra = collections.Counter()
    cg_extra = collections.Counter()
    identical = []
    idat_differ = []
    pixels_differ = []
    idat_only = []
    failed = []
    cg_writes_more = []
    itxt_no_meta = []
    itxt_missing = []
    phys_rows = collections.Counter()
    conditional = {"files": 0, "match": 0}
    skipped = []
    examined = 0

    for n in names:
        src = os.path.join(root, n)
        got = resize_both(src, w, h)
        if got is None:
            skipped.append(n)
            continue
        s_out, c_out = got
        sc, cc = chunkset(s_out), chunkset(c_out)
        seg = jpeg_segments(src)          # None for non-JPEG

        colour = "iCCP" if "iCCP" in cc else ("sRGB" if "sRGB" in cc else "?")
        cg_colour["%s + %s" % (colour, "eXIf" if "eXIf" in cc else "no eXIf")] += 1

        if sc - cc:
            sips_extra[" ".join(sorted(sc - cc))] += 1
        if cc - sc:
            cg_extra[" ".join(sorted(cc - sc))] += 1
            cg_writes_more.append((n, " ".join(sorted(cc - sc))))

        # Two different questions, and the answers differ by ten files. The
        # IDAT stream is the compressed bytes; the decoded pixels are the
        # picture. A version of this instrument compared IDAT and called the
        # result "pixels differ", which is what put a container difference
        # into a row about interpolation.
        same_idat = idat_sha(s_out) == idat_sha(c_out)
        try:
            same_pixels = raw_rgb(s_out, w, h) == raw_rgb(c_out, w, h)
        except DecodeFailed as error:
            failed.append((n, str(error)))
            continue
        if not same_idat:
            idat_differ.append(n)
            if same_pixels:
                idat_only.append((n, ihdr(src) if src.endswith(".png") else None,
                                  ihdr(s_out), ihdr(c_out)))
        examined += 1
        if not same_pixels:
            pixels_differ.append(n)
        if sha(s_out) == sha(c_out):
            identical.append(n)

        if seg is not None:
            bare = not (seg["exif"] or seg["xmp"] or seg["density"]
                        or seg["photoshop"])
        else:
            # a PNG or other container: nothing ancillary in the SOURCE
            bare = not chunkset(src)
        if bare:
            conditional["files"] += 1
            conditional["match"] += sha(s_out) == sha(c_out)

        if seg is not None:
            has_meta = seg["exif"] or seg["xmp"]
            if "iTXt" in sc and not has_meta:
                itxt_no_meta.append((n, seg))
            if "iTXt" not in sc and has_meta:
                itxt_missing.append((n, seg))
            phys_rows[(seg["density"], "pHYs" in sc)] += 1

    print("corpus      : %s" % root)
    print("shape       : %dx%d" % (w, h))
    print("examined    : %d  (skipped %d: %s)"
          % (examined, len(skipped), ", ".join(skipped[:4])))
    print("\nwhat CoreGraphics writes (colour chunk + eXIf):")
    for k, v in cg_colour.most_common():
        print("    %-24s %d" % (k, v))
    print("\nchunks only sips wrote:")
    for k, v in sips_extra.most_common(8):
        print("    %-24s %d" % (k, v))
    print("chunks only CoreGraphics wrote:")
    for k, v in (cg_extra.most_common(8) or [("(none)", 0)]):
        print("    %-24s %d" % (k, v))
    if cg_writes_more:
        print("    first few: %s"
              % ", ".join(n for n, _ in cg_writes_more[:6]))
    print("\nwhole files identical : %d of %d" % (len(identical), examined))
    if list_identical:
        for n in identical:
            print("    identical: %s" % n)
    print("IDAT stream differs   : %d of %d" % (len(idat_differ), examined))
    print("decoded pixels differ : %d of %d" % (len(pixels_differ), examined))
    for n in pixels_differ:
        print("    %s" % n)
    if idat_only:
        print("\nIDAT differs but the decoded picture does not -- %d files, "
              "and why:" % len(idat_only))
        print("    %-40s %-22s %-22s" % ("image", "sips out", "cg out"))
        for n, src_h, s_h, c_h in idat_only:
            fmt = "ctype=%d depth=%d interlace=%d"
            print("    %-40s %-22s %-22s%s"
                  % (n[:40], fmt % (s_h[3], s_h[2], s_h[4]),
                     fmt % (c_h[3], c_h[2], c_h[4]),
                     "   source " + fmt % (src_h[3], src_h[2], src_h[4])
                     if src_h else ""))

    print("\npixel-differing sources, by container:")
    kinds = collections.Counter(
        os.path.splitext(n)[1].lower().lstrip(".") for n in pixels_differ)
    for k, v in sorted(kinds.items()):
        print("    %-6s %d" % (k, v))

    print("\niTXt counterexamples:")
    print("    carries neither Exif nor XMP yet gets iTXt : %d"
          % len(itxt_no_meta))
    for n, seg in itxt_no_meta[:6]:
        print("        %-50s photoshop=%s" % (n[:50], seg["photoshop"]))
    print("    carries Exif or XMP yet gets no iTXt       : %d"
          % len(itxt_missing))
    for n, _ in itxt_missing[:6]:
        print("        %s" % n[:50])

    print("\nthe conditional -- source carries nothing sips can synthesise from:")
    print("    %d such sources, %d produced byte-identical files"
          % (conditional["files"], conditional["match"]))

    if failed:
        print("\n%d FAILED to decode -- not agreements:" % len(failed))
        for n, why in failed:
            print("    %-40s %s" % (n[:40], why))

    print("\npHYs against JFIF density, over every JPEG "
          "(the whole table, not an excerpt):")
    for (dens, got), count in sorted(phys_rows.items(), key=lambda kv: str(kv[0])):
        print("    density %-18s pHYs=%-5s  %d files" % (str(dens), got, count))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = args[0]
    limit = int(args[1]) if len(args) > 1 else 0
    w = int(args[2]) if len(args) > 2 else 800
    h = int(args[3]) if len(args) > 3 else 600
    main(root, limit, w, h, "--identical" in sys.argv[1:])
