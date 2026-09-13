"""Report what ImageIO/CoreGraphics sees in a file. Task 0 measuring instrument.

Usage: python3 probe.py <path> [...]
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cgbase import (cf, cg, io, cfurl, from_cfstring, dict_get, cfnumber_int,
                    COLORSPACE_MODEL)


def probe(path):
    print("file: %s" % path)
    url = cfurl(path)
    src = io.CGImageSourceCreateWithURL(url, None)
    if not src:
        print("  CGImageSourceCreateWithURL: NULL (not decodable)")
        cf.CFRelease(url)
        return
    print("  source type (UTI): %s" % from_cfstring(io.CGImageSourceGetType(src)))
    print("  image count: %d" % io.CGImageSourceGetCount(src))

    props = io.CGImageSourceCopyPropertiesAtIndex(src, 0, None)
    if props:
        pw = cfnumber_int(dict_get(props, "kCGImagePropertyPixelWidth"))
        ph = cfnumber_int(dict_get(props, "kCGImagePropertyPixelHeight"))
        orient = cfnumber_int(dict_get(props, "kCGImagePropertyOrientation"))
        depth = cfnumber_int(dict_get(props, "kCGImagePropertyDepth"))
        print("  properties PixelWidth x PixelHeight: %s x %s" % (pw, ph))
        print("  properties Orientation: %s" % orient)
        print("  properties Depth: %s" % depth)
        cf.CFRelease(props)
    else:
        print("  CGImageSourceCopyPropertiesAtIndex: NULL")

    img = io.CGImageSourceCreateImageAtIndex(src, 0, None)
    if not img:
        print("  CGImageSourceCreateImageAtIndex: NULL (no decoded image)")
    else:
        w = cg.CGImageGetWidth(img)
        h = cg.CGImageGetHeight(img)
        print("  CGImageGetWidth x CGImageGetHeight: %d x %d" % (w, h))
        print("  bitsPerComponent: %d  bitsPerPixel: %d"
              % (cg.CGImageGetBitsPerComponent(img),
                 cg.CGImageGetBitsPerPixel(img)))
        space = cg.CGImageGetColorSpace(img)
        if space:
            name = cg.CGColorSpaceCopyName(space)
            print("  colorSpace: model=%s name=%s"
                  % (COLORSPACE_MODEL.get(cg.CGColorSpaceGetModel(space)),
                     from_cfstring(name)))
            if name:
                cf.CFRelease(name)
        cg.CGImageRelease(img)
    cf.CFRelease(src)
    cf.CFRelease(url)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        probe(p)
