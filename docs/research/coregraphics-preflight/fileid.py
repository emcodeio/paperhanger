"""Whole-file PNG identity: sips output against CoreGraphics output.

Usage: python3 fileid.py <source> <w>x<h> [<w>x<h> ...]
Reports SHA-256 of both outputs, plus the ancillary chunks each carries.
"""
import sys
import os
import atexit
import shutil
import tempfile
import struct
import hashlib
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
# Intermediates go to a temp directory, never beside this script: the repo is
# public and these files are derived from corpus photographs.
SCRATCH = tempfile.mkdtemp(prefix="cg-preflight-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
CRITICAL = {"IHDR", "PLTE", "IDAT", "IEND"}


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def chunks(path):
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


def ancillary(path):
    seen = []
    for t in chunks(path):
        if t not in CRITICAL and t not in seen:
            seen.append(t)
    return seen


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


def main(source, shapes):
    src_anc = ancillary(source) if source.lower().endswith(".png") else \
        ["(not a PNG source)"]
    print("source: %s" % source)
    print("  sha256: %s" % sha(source))
    print("  source ancillary chunks: %s" % (" ".join(src_anc) or "(none)"))
    for w, h in shapes:
        s_out = os.path.join(SCRATCH, "fi_s.png")
        c_out = os.path.join(SCRATCH, "fi_c.png")
        for p in (s_out, c_out):
            if os.path.exists(p):
                os.remove(p)
        subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(h),
                        str(w), source, "-s", "format", "png", "--out", s_out],
                       capture_output=True)
        subprocess.run([sys.executable, os.path.join(HERE, "resize.py"),
                        source, c_out, str(w), str(h), "3"], check=True)
        ss, cs = sha(s_out), sha(c_out)
        print("  %dx%d" % (w, h))
        print("    sips sha256 : %s" % ss)
        print("    cg   sha256 : %s" % cs)
        print("    whole file  : %s" % ("IDENTICAL" if ss == cs else "differ"))
        print("    IDAT        : %s"
              % ("identical" if idat_sha(s_out) == idat_sha(c_out) else "DIFFER"))
        if ss != cs:
            print("    sips ancillary: %s" % " ".join(ancillary(s_out)))
            print("    cg   ancillary: %s" % " ".join(ancillary(c_out)))


if __name__ == "__main__":
    shapes = [tuple(int(x) for x in a.split("x")) for a in sys.argv[2:]]
    main(sys.argv[1], shapes)
