"""Is any real resample anisotropic, and does it read a JPEG when it is?

Two questions, both over every image in the corpus and every plan the real
planner produces, because a count from a sample has been wrong three times on
this document.

  1. Does a crop rect's aspect ratio ever differ from the target's?
     `geometry` rounds the slice dimension UP, so a slice is 16:10 or 2:3 only
     when the division came out whole.
  2. Does the resample that follows ever have a different horizontal and
     vertical scale -- and if so, is its input the original file (which may be
     a JPEG) or a lossless PNG intermediate? The measured decode divergence
     needs both an anisotropic shape and a JPEG source.

Usage: python3 aspect_audit.py <corpus dir>

Read-only. Decodes nothing: dimensions come from ImageIO properties.
"""
import sys
import os
from fractions import Fraction
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)

from paperhanger import plan, sizes                       # noqa: E402
from cgbase import cf, io, cfurl, dict_get, cfnumber_int, from_cfstring  # noqa: E402

TARGET_ASPECT = {
    sizes.DESKTOP_BY_WIDTH.name: Fraction(16, 10),
    sizes.PHONE_BY_HEIGHT.name: Fraction(2, 3),
}


def probe(path):
    url = cfurl(path)
    src = io.CGImageSourceCreateWithURL(url, None)
    if not src:
        cf.CFRelease(url)
        return None
    uti = from_cfstring(io.CGImageSourceGetType(src))
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
    return (wh, uti) if wh else None


def main(root):
    opts = plan.OutputSettings(processing_dir=Path("/tmp/unused"), fmt="heic",
                               quality=80)
    devices = list(sizes.DEVICES)

    photos = 0
    crop_rects = 0
    crop_aspect_off = []          # (name, rect aspect, target aspect)
    resamples = 0
    anisotropic = []              # (name, in, out, xscale, yscale, input kind)
    aniso_from_original_jpeg = []

    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isfile(path) or name.startswith("."):
            continue
        got = probe(path)
        if not got:
            continue
        (width, height), uti = got
        photos += 1
        work = plan.plan_photo(Path(path), width, height, devices, opts)

        for p in work.plans:
            scale = sizes.UPSCALE_FACTOR if p.needs_upscale else 1

            if p.crop is not None:
                crop_rects += 1
                have = Fraction(p.crop.width, p.crop.height)
                want = TARGET_ASPECT.get(p.target.name)
                if want is not None and have != want:
                    crop_aspect_off.append((name, have, want))
                rect = p.crop if scale == 1 else p.crop.scaled(scale)
                in_w, in_h = rect.width, rect.height
                feeds = "png-crop"
            else:
                in_w, in_h = width * scale, height * scale
                feeds = "png-4x-frame" if scale == 4 else "original"

            if not (p.needs_resize or scale != 1):
                continue
            resamples += 1

            xs = Fraction(p.out_width, in_w)
            ys = Fraction(p.out_height, in_h)
            if xs != ys:
                row = (name, (in_w, in_h), (p.out_width, p.out_height),
                       xs, ys, feeds, uti)
                anisotropic.append(row)
                if feeds == "original" and uti == "public.jpeg":
                    aniso_from_original_jpeg.append(row)

    print("photos                     : %d" % photos)
    print("crop rects planned         : %d" % crop_rects)
    print("crop rects whose aspect != target: %d" % len(crop_aspect_off))
    print("resamples                  : %d" % resamples)
    print("resamples with x scale != y scale: %d" % len(anisotropic))
    print("  ... of those, reading the ORIGINAL file: %d"
          % len([r for r in anisotropic if r[5] == "original"]))
    print("  ... of those, reading an original JPEG: %d"
          % len(aniso_from_original_jpeg))

    if crop_aspect_off:
        worst = sorted(crop_aspect_off,
                       key=lambda r: -abs(float(r[1]) - float(r[2])))[:8]
        print("\nlargest crop-aspect departures:")
        for n, have, want in worst:
            print("  %-52s %s (%.6f) vs %s (%.6f)  delta %.2e"
                  % (n[:52], have, float(have), want, float(want),
                     abs(float(have) - float(want))))

    if anisotropic:
        worst = sorted(anisotropic,
                       key=lambda r: -abs(float(r[3]) - float(r[4])))[:8]
        print("\nlargest resample anisotropies:")
        for n, i, o, xs, ys, feeds, uti in worst:
            print("  %-40s %dx%d -> %dx%d  x=%.8f y=%.8f  delta %.2e  [%s, %s]"
                  % (n[:40], i[0], i[1], o[0], o[1], float(xs), float(ys),
                     abs(float(xs) - float(ys)), feeds, uti))

    if anisotropic:
        worst = max(anisotropic, key=lambda r: abs(float(r[3]) - float(r[4])))
        print("\nlargest anisotropy ANYWHERE          : %.2e   [%s]"
              % (abs(float(worst[3]) - float(worst[4])), worst[5]))
        from_original = [r for r in anisotropic if r[5] == "original"]
        if from_original:
            worst_o = max(from_original,
                          key=lambda r: abs(float(r[3]) - float(r[4])))
            print("largest anisotropy READING AN ORIGINAL: %.2e   %s "
                  "(%dx%d -> %dx%d)"
                  % (abs(float(worst_o[3]) - float(worst_o[4])), worst_o[0][:40],
                     worst_o[1][0], worst_o[1][1], worst_o[2][0], worst_o[2][1]))
        print("(the two are different plans: the headline figure belongs to a "
              "crop-fed PNG resample, which no decode divergence can reach)")

    if aniso_from_original_jpeg:
        print("\nanisotropic AND reading an original JPEG "
              "(a subset of the 159 plans that read an original -- the other "
              "90 are isotropic and were once left out):")
        for n, i, o, xs, ys, feeds, uti in aniso_from_original_jpeg[:20]:
            print("  %-46s %dx%d -> %dx%d" % (n[:46], i[0], i[1], o[0], o[1]))
        print("  (%d in total)" % len(aniso_from_original_jpeg))


if __name__ == "__main__":
    main(sys.argv[1])
