"""What each tool writes into a PNG, against what the source actually carried.

For every source given, records the container's metadata (ICC profile, JFIF
density, EXIF, XMP, and any ancillary PNG chunks), then resizes it through both
sips and CoreGraphics and records the ancillary chunks each output carries and
whether the two files are byte-identical.

Usage: python3 chunkmap.py <out_w> <out_h> <source> [<source> ...]

Outputs are written to a temporary directory and removed; nothing is written
next to this script or into the corpus.
"""
import sys
import os
import struct
import hashlib
import shutil
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
CRITICAL = {"IHDR", "PLTE", "IDAT", "IEND"}


def png_chunks(path):
    """[(name, length)] for a PNG, or None if the file is not a PNG."""
    d = open(path, "rb").read(1 << 22)
    if not d.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    i, out = 8, []
    while i + 8 <= len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]
        t = d[i + 4:i + 8].decode("latin1")
        out.append((t, ln))
        i += 12 + ln
        if t == "IEND":
            break
    return out


def ancillary(path):
    ch = png_chunks(path) or []
    seen, out = set(), []
    for t, ln in ch:
        if t not in CRITICAL and t not in seen:
            seen.add(t)
            out.append("%s(%d)" % (t, ln))
    return out


def jpeg_markers(path):
    """{'jfif': bool, 'exif': bool, 'xmp': bool} for a JPEG, else None."""
    d = open(path, "rb").read(1 << 22)
    if not d.startswith(b"\xff\xd8"):
        return None
    found = {"jfif": False, "exif": False, "xmp": False}
    i = 2
    while i + 4 <= len(d) and d[i] == 0xFF:
        m = d[i + 1]
        if m in (0xD8, 0xD9):
            i += 2
            continue
        ln = struct.unpack(">H", d[i + 2:i + 4])[0]
        body = d[i + 4:i + 4 + min(ln, 64)]
        if m == 0xE0 and body.startswith(b"JFIF\x00"):
            found["jfif"] = True
        if m == 0xE1 and body.startswith(b"Exif\x00\x00"):
            found["exif"] = True
        if m == 0xE1 and body.startswith(b"http://ns.adobe.com/xap/"):
            found["xmp"] = True
        if m == 0xDA:
            break
        i += 2 + ln
    return found


def icc_name(path):
    r = subprocess.run(["exiftool", "-s3", "-ICC_Profile:ProfileDescription",
                        path], capture_output=True)
    return r.stdout.decode(errors="replace").strip() or "(none)"


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def idat_sha(path):
    d = open(path, "rb").read()
    i, acc = 8, b""
    while i < len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]
        t = d[i + 4:i + 8]
        if t == b"IDAT":
            acc += d[i + 8:i + 8 + ln]
        i += 12 + ln
        if t == b"IEND":
            break
    return hashlib.sha256(acc).hexdigest()


def main(w, h, sources):
    tmp = tempfile.mkdtemp(prefix="chunkmap-")
    matches = 0
    try:
        for src in sources:
            s_out = os.path.join(tmp, "s.png")
            c_out = os.path.join(tmp, "c.png")
            for p in (s_out, c_out):
                if os.path.exists(p):
                    os.remove(p)
            subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(h),
                            str(w), src, "-s", "format", "png", "--out",
                            s_out], capture_output=True)
            r = subprocess.run([sys.executable,
                                os.path.join(HERE, "resize.py"), src, c_out,
                                str(w), str(h), "3"], capture_output=True)
            if not os.path.exists(s_out) or not os.path.exists(c_out):
                print("%-52s SKIPPED (%s)"
                      % (os.path.basename(src),
                         r.stderr.decode(errors="replace").strip()[:60]))
                continue
            same = sha(s_out) == sha(c_out)
            matches += same
            jm = jpeg_markers(src)
            if jm is None:
                carried = " ".join(ancillary(src)) or "(none)"
                kind = "PNG"
            else:
                carried = " ".join(k for k, v in jm.items() if v) or "(none)"
                kind = "JPEG"
            print("%-52s %-5s icc=%-22s carries=%s"
                  % (os.path.basename(src), kind, icc_name(src)[:22], carried))
            print("      sips -> %s" % (" ".join(ancillary(s_out)) or "(none)"))
            print("      cg   -> %s" % (" ".join(ancillary(c_out)) or "(none)"))
            pix = idat_sha(s_out) == idat_sha(c_out)
            print("      whole file: %-9s  IDAT: %s"
                  % ("IDENTICAL" if same else "differs",
                     "identical" if pix else "DIFFER"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d of %d sources produced byte-identical files"
          % (matches, len(sources)))


if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3:])
