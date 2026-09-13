"""Decode every non-JPEG corpus file and report the CGImage colour space model."""
import sys
import os
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cgbase import (cf, cg, io, cfurl, from_cfstring, COLORSPACE_MODEL)

root = sys.argv[1]
want = tuple(a.lower() for a in sys.argv[2:]) or (".png", ".gif", ".webp",
                                                  ".heic")
models = collections.Counter()
flagged = []
for name in sorted(os.listdir(root)):
    if not name.lower().endswith(want):
        continue
    path = os.path.join(root, name)
    url = cfurl(path)
    src = io.CGImageSourceCreateWithURL(url, None)
    img = io.CGImageSourceCreateImageAtIndex(src, 0, None) if src else None
    if not img:
        models["<undecodable>"] += 1
        flagged.append((name, "undecodable"))
    else:
        space = cg.CGImageGetColorSpace(img)
        model = COLORSPACE_MODEL.get(cg.CGColorSpaceGetModel(space)) \
            if space else "<none>"
        bpp = cg.CGImageGetBitsPerPixel(img)
        models["%s/%dbpp" % (model, bpp)] += 1
        if model not in ("RGB",):
            flagged.append((name, "%s %dbpp" % (model, bpp)))
        cg.CGImageRelease(img)
    if src:
        cf.CFRelease(src)
    cf.CFRelease(url)

print("decoded colour space models: %s" % dict(models))
print("non-RGB after decode: %d" % len(flagged))
for n, m in flagged:
    print("  %s  %s" % (n, m))
