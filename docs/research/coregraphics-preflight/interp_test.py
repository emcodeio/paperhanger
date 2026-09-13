"""Compare CoreGraphics resize against sips across shapes. Task 0 instrument.

Usage: python3 interp_test.py <source.png> <label>
Prints one line per shape: pixel equality, PNG whole-file equality, AE.
"""
import sys
import os
import atexit
import shutil
import tempfile
import subprocess
import hashlib
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
# Intermediates go to a temp directory, never beside this script: the repo is
# public and these files are derived from corpus photographs.
SCRATCH = tempfile.mkdtemp(prefix="cg-preflight-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
SHAPES = [
    (1000, 700, "downscale, both axes"),
    (3000, 2100, "enlargement, both axes"),
    (800, 1200, "portrait, height-governed"),
    (1600, 400, "letterbox, width-governed"),
    (2000, 1000, "width held, height halved"),
    (4000, 1500, "height held, width doubled"),
    (7680, 5760, "desktop-scale enlargement"),
    (2880, 4320, "phone target, portrait"),
]


def idat(path):
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


def chunk_names(path):
    d = open(path, "rb").read()
    i, out = 8, []
    while i < len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]
        t = d[i + 4:i + 8].decode("latin1")
        out.append(t)
        i += 12 + ln
        if t == "IEND":
            break
    return out


def raw(path):
    out = subprocess.run(["magick", path, "-depth", "8", "RGB:-"],
                         capture_output=True)
    return out.stdout


def main(source, label):
    print("source: %s  (%s)" % (source, label))
    print("%-14s %-28s %-9s %-9s %-9s %s"
          % ("shape", "kind", "pixels", "IDAT", "wholefile", "AE"))
    for w, h, kind in SHAPES:
        s_out = os.path.join(SCRATCH, "it_s.png")
        c_out = os.path.join(SCRATCH, "it_c.png")
        for p in (s_out, c_out):
            if os.path.exists(p):
                os.remove(p)
        subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(h),
                        str(w), source, "-s", "format", "png", "--out", s_out],
                       capture_output=True)
        subprocess.run([sys.executable, os.path.join(HERE, "resize.py"),
                        source, c_out, str(w), str(h), "3"], check=True)
        pix = "SAME" if raw(s_out) == raw(c_out) else "DIFFER"
        idt = "SAME" if idat(s_out) == idat(c_out) else "DIFFER"
        whole = "SAME" if (open(s_out, "rb").read()
                           == open(c_out, "rb").read()) else "DIFFER"
        ae = subprocess.run(["magick", "compare", "-metric", "AE", c_out,
                             s_out, "null:"], capture_output=True)
        ae_txt = (ae.stderr or b"").decode().strip().splitlines()[0] \
            if ae.stderr else "?"
        print("%-14s %-28s %-9s %-9s %-9s %s"
              % ("%dx%d" % (w, h), kind, pix, idt, whole, ae_txt))
    print("  sips chunks: %s" % " ".join(chunk_names(s_out)))
    print("  cg   chunks: %s" % " ".join(chunk_names(c_out)))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
