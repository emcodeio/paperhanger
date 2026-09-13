"""Read-only scan of the corpus through ImageIO properties (no decode).

Usage: python3 corpus_scan.py <dir>
Reports colour model, EXIF orientation, depth and UTI counts.
"""
import sys
import os
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cgbase import cf, io, cfurl, from_cfstring, dict_get, cfnumber_int

root = sys.argv[1]
models = collections.Counter()
orients = collections.Counter()
depths = collections.Counter()
utis = collections.Counter()
oriented = []
nonrgb = []
unreadable = []

names = sorted(os.listdir(root))
for name in names:
    path = os.path.join(root, name)
    if not os.path.isfile(path) or name.startswith("."):
        continue
    url = cfurl(path)
    src = io.CGImageSourceCreateWithURL(url, None)
    if not src:
        unreadable.append(name)
        cf.CFRelease(url)
        continue
    utis[from_cfstring(io.CGImageSourceGetType(src))] += 1
    props = io.CGImageSourceCopyPropertiesAtIndex(src, 0, None)
    if props:
        model = from_cfstring(dict_get(props, "kCGImagePropertyColorModel"))
        orient = cfnumber_int(dict_get(props, "kCGImagePropertyOrientation"))
        depth = cfnumber_int(dict_get(props, "kCGImagePropertyDepth"))
        models[model] += 1
        orients[orient] += 1
        depths[depth] += 1
        if orient not in (None, 1):
            oriented.append((name, orient))
        if model != "RGB":
            nonrgb.append((name, model))
        cf.CFRelease(props)
    cf.CFRelease(src)
    cf.CFRelease(url)

total = sum(models.values())
print("files examined: %d" % total)
print("colour models: %s" % dict(models))
print("orientations : %s" % dict(orients))
print("depths       : %s" % dict(depths))
print("source UTIs  : %s" % dict(utis))
print("files with orientation other than 1/absent: %d %s"
      % (len(oriented), oriented[:10]))
print("files with a non-RGB colour model: %d %s" % (len(nonrgb), nonrgb[:10]))
print("files ImageIO could not open: %d %s" % (len(unreadable), unreadable[:10]))
