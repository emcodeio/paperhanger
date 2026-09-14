"""Every resampling scale factor paperhanger would actually ask for, over the corpus.

Runs the real planner (paperhanger.plan.plan_photo) against the measured
dimensions of every corpus image, reconstructs the input to each resize the way
execute.render does, and reports the distribution of vertical and horizontal
scale factors. Read-only: no image is written or modified.

Usage: python3 plan_scales.py <corpus dir>
"""
import sys
import os
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)

from paperhanger import bands, plan, sizes                # noqa: E402
from cgbase import cf, io, cfurl, dict_get, cfnumber_int  # noqa: E402

BOUNDARY = 4775 / 4800  # largest vertical reduction measured exact


def dimensions(path):
    url = cfurl(path)
    src = io.CGImageSourceCreateWithURL(url, None)
    if not src:
        cf.CFRelease(url)
        return None
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
    return wh


root = sys.argv[1]
opts = plan.OutputSettings(processing_dir=Path("/tmp/unused"), fmt="heic",
                           quality=80)
devices = list(sizes.DEVICES)

resamples = []   # (name, in_w, in_h, out_w, out_h, feeds, band)
photos = 0
plans = 0

for name in sorted(os.listdir(root)):
    path = os.path.join(root, name)
    if not os.path.isfile(path) or name.startswith("."):
        continue
    wh = dimensions(path)
    if not wh:
        continue
    width, height = wh
    photos += 1
    work = plan.plan_photo(Path(path), width, height, devices, opts)
    for p in work.plans:
        plans += 1
        scale = sizes.UPSCALE_FACTOR if p.needs_upscale else 1
        if p.crop is not None:
            rect = p.crop if scale == 1 else p.crop.scaled(scale)
            in_w, in_h = rect.width, rect.height
            feeds = "png-crop"
        else:
            in_w, in_h = width * scale, height * scale
            feeds = "png-4x-frame" if scale == 4 else "original"
        # `render`: resize = target.needs_resize or scale != 1. The second
        # half is why band 4 resamples at all -- it needs no resize OF THE
        # SOURCE, but it is handed the 4x frame, and 4x of a slice that is
        # already the planned size is the planned size. Hence an identity
        # draw.
        resizes = p.needs_resize or scale != 1
        if not resizes:
            continue
        resamples.append((name, in_w, in_h, p.out_width, p.out_height, feeds,
                          p.band))

print("photos planned        : %d" % photos)
print("output plans          : %d" % plans)
print("plans that resample   : %d" % len(resamples))

ys = [(r[4] / r[2], r) for r in resamples]
xs = [(r[3] / r[1], r) for r in resamples]

y_red = [(s, r) for s, r in ys if s < 1.0]
y_enl = [(s, r) for s, r in ys if s > 1.0]
y_one = [(s, r) for s, r in ys if s == 1.0]

print("vertical scale  < 1   : %d" % len(y_red))
print("vertical scale == 1   : %d" % len(y_one))
identity = [r for r in resamples if r[3] == r[1] and r[4] == r[2]]
print("BOTH axes 1:1 (identity resample): %d" % len(identity))
if identity:
    big = max(identity, key=lambda r: r[1] * r[2])
    print("  largest identity resample: %dx%d (%.0f MB as a bitmap)  %s"
          % (big[1], big[2], big[1] * big[2] * 4 / 1e6, big[0]))
    by_feed = {}
    for r in identity:
        key = (r[5], r[6])
        by_feed[key] = by_feed.get(key, 0) + 1
    print("  where the identity draw's input comes from:")
    for (feeds, band), count in sorted(by_feed.items()):
        print("    %-14s band %d (%-14s) : %d"
              % (feeds, band, bands.NAMES[band], count))
    from_original = [r for r in identity if r[5] == "original"]
    if from_original:
        print("  the identity draws that read an ORIGINAL file:")
        for r in sorted(from_original):
            print("    %-52s %dx%d" % (r[0][:52], r[1], r[2]))
print("vertical scale  > 1   : %d" % len(y_enl))
print("horizontal scale > 1  : %d" % len([1 for s, _ in xs if s > 1.0]))

if y_red:
    y_red.sort(key=lambda t: -t[0])
    print("\nclosest vertical reductions to identity:")
    for s, r in y_red[:8]:
        print("  %.6f   %dx%d -> %dx%d   %s"
              % (s, r[1], r[2], r[3], r[4], r[0]))
    print("\nboundary (largest exact vertical reduction measured): %.6f"
          % BOUNDARY)
    inside = [(s, r) for s, r in y_red if s >= BOUNDARY]
    print("plans landing at or above the boundary: %d" % len(inside))
    for s, r in inside[:20]:
        print("  %.6f   %dx%d -> %dx%d   %s"
              % (s, r[1], r[2], r[3], r[4], r[0]))
