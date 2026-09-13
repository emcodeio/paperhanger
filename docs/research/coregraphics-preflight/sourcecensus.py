"""What corpus sources carry, and what predicts whole-file identity.

Two passes. First a header-only census of every JPEG in the corpus: which
carry a JFIF APP0, an Exif APP1, an XMP APP1. Then, over a sample, an actual
resize through both tools, so the census facts can be correlated against
whether the two outputs are byte-identical.

Usage: python3 sourcecensus.py <corpus dir> [sample size]

Outputs go to a temporary directory and are removed. Nothing is written into
the corpus or next to this script.
"""
import sys
import os
import struct
import hashlib
import shutil
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def markers(path):
    d = open(path, "rb").read(1 << 21)
    if not d.startswith(b"\xff\xd8"):
        return None
    f = {"jfif": False, "exif": False, "xmp": False}
    i = 2
    while i + 4 <= len(d) and d[i] == 0xFF:
        m = d[i + 1]
        if m in (0xD8, 0xD9):
            i += 2
            continue
        ln = struct.unpack(">H", d[i + 2:i + 4])[0]
        body = d[i + 4:i + 4 + min(ln, 64)]
        if m == 0xE0 and body.startswith(b"JFIF\x00"):
            f["jfif"] = True
        elif m == 0xE1 and body.startswith(b"Exif\x00\x00"):
            f["exif"] = True
        elif m == 0xE1 and body.startswith(b"http://ns.adobe.com/xap/"):
            f["xmp"] = True
        if m == 0xDA:
            break
        i += 2 + ln
    return f


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _chunks(path):
    d = open(path, "rb").read()
    i, out = 8, []
    while i < len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]
        t = d[i + 4:i + 8]
        out.append((t.decode("latin1"), d[i + 8:i + 8 + ln]))
        i += 12 + ln
        if t == b"IEND":
            break
    return out


def chunkset(path):
    return {t for t, _ in _chunks(path)
            if t not in ("IHDR", "PLTE", "IDAT", "IEND")}


def idat_sha(path):
    acc = b"".join(b for t, b in _chunks(path) if t == "IDAT")
    return hashlib.sha256(acc).hexdigest()


root = sys.argv[1]
sample_size = int(sys.argv[2]) if len(sys.argv) > 2 else 46

names = [n for n in sorted(os.listdir(root))
         if n.lower().endswith((".jpg", ".jpeg")) and not n.startswith(".")]

counts = {"jfif": 0, "exif": 0, "xmp": 0, "total": 0, "bare": 0}
facts = {}
for n in names:
    f = markers(os.path.join(root, n))
    if f is None:
        continue
    facts[n] = f
    counts["total"] += 1
    for k in ("jfif", "exif", "xmp"):
        counts[k] += f[k]
    if not any(f.values()):
        counts["bare"] += 1

print("JPEG census over %s" % root)
print("  JPEGs examined     : %d" % counts["total"])
print("  carry JFIF density : %d" % counts["jfif"])
print("  carry Exif         : %d" % counts["exif"])
print("  carry XMP          : %d" % counts["xmp"])
print("  carry none of them : %d" % counts["bare"])

step = max(1, len(names) // sample_size)
sample = names[::step][:sample_size]
print("\nidentity over a %d-file spread (every %dth JPEG), resized to 800x600"
      % (len(sample), step))

tmp = tempfile.mkdtemp(prefix="sourcecensus-")
rows = []
try:
    for n in sample:
        src = os.path.join(root, n)
        s_out = os.path.join(tmp, "s.png")
        c_out = os.path.join(tmp, "c.png")
        for p in (s_out, c_out):
            if os.path.exists(p):
                os.remove(p)
        subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", "600", "800",
                        src, "-s", "format", "png", "--out", s_out],
                       capture_output=True)
        subprocess.run([sys.executable, os.path.join(HERE, "resize.py"), src,
                        c_out, "800", "600", "3"], capture_output=True)
        if not (os.path.exists(s_out) and os.path.exists(c_out)):
            continue
        rows.append((facts[n], sha(s_out) == sha(c_out), n,
                     chunkset(s_out), chunkset(c_out),
                     idat_sha(s_out) == idat_sha(c_out)))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n  what differs, per file that differs:")
seen = {}
for f, same, n, sc, cc, pix in rows:
    if same:
        continue
    extra = " ".join(sorted(sc - cc)) or "(none)"
    missing = " ".join(sorted(cc - sc)) or "(none)"
    key = (extra, missing, pix)
    seen.setdefault(key, []).append(n)
for (extra, missing, pix), names_ in sorted(seen.items(),
                                            key=lambda kv: -len(kv[1])):
    print("    %2d files: sips writes extra [%s], cg writes extra [%s], "
          "pixels %s" % (len(names_), extra, missing,
                         "identical" if pix else "DIFFER"))

print("\n  what makes sips write each chunk (counts over the spread):")
print("    %-26s %-8s %-8s" % ("source carries", "pHYs", "iTXt"))
for label, pred in (
        ("nothing", lambda f: not any(f.values())),
        ("JFIF only", lambda f: f["jfif"] and not f["exif"] and not f["xmp"]),
        ("Exif (any)", lambda f: f["exif"]),
        ("XMP (any)", lambda f: f["xmp"]),
        ("no Exif, no XMP", lambda f: not f["exif"] and not f["xmp"])):
    grp = [r for r in rows if pred(r[0])]
    if not grp:
        continue
    print("    %-26s %-8s %-8s  (%d files)"
          % (label,
             "%d/%d" % (sum("pHYs" in r[3] for r in grp), len(grp)),
             "%d/%d" % (sum("iTXt" in r[3] for r in grp), len(grp)),
             len(grp)))

match = [r for r in rows if r[1]]
print("  identical: %d of %d" % (len(match), len(rows)))


def split(key):
    yes = [r for r in rows if r[0][key]]
    no = [r for r in rows if not r[0][key]]
    print("  %-5s present: %2d files, %2d identical | absent: %2d files, "
          "%2d identical"
          % (key, len(yes), sum(r[1] for r in yes), len(no),
             sum(r[1] for r in no)))


for k in ("jfif", "exif", "xmp"):
    split(k)

bare = [r for r in rows if not any(r[0].values())]
print("  no Exif and no XMP : %2d files, %2d identical"
      % (len([r for r in rows if not r[0]["exif"] and not r[0]["xmp"]]),
         sum(r[1] for r in rows if not r[0]["exif"] and not r[0]["xmp"])))
print("  carrying nothing   : %2d files, %2d identical"
      % (len(bare), sum(r[1] for r in bare)))
