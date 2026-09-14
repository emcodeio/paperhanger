"""Resize an image through CoreGraphics. Task 0 measuring instrument.

Usage: python3 resize.py <src> <dst> <width> <height> [interp]

interp: 0 Default, 1 None, 2 Low, 3 High, 4 Medium (default 3).
Destination format is taken from the destination extension.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cgbase import (cf, cg, io, cfurl, cfstr, uti_for, CGRect, CGPoint,
                    CGSize, kCGImageAlphaNoneSkipLast)


def resize(src_path, dst_path, width, height, interp=3):
    src_url = cfurl(src_path)
    source = io.CGImageSourceCreateWithURL(src_url, None)
    if not source:
        raise SystemExit("cannot open source: %s" % src_path)
    image = io.CGImageSourceCreateImageAtIndex(source, 0, None)
    if not image:
        raise SystemExit("cannot decode source: %s" % src_path)

    space = cg.CGImageGetColorSpace(image)
    # A Monochrome colour space with kCGImageAlphaNoneSkipLast is accepted by
    # CGBitmapContextCreate and then renders all black. Pick the alpha info
    # from the colour space model, not blindly.
    model = cg.CGColorSpaceGetModel(space)
    info = kCGImageAlphaNoneSkipLast if model == 1 else 0  # 0 = AlphaNone
    if os.environ.get("FORCE_SKIPLAST"):
        info = kCGImageAlphaNoneSkipLast
    ctx = cg.CGBitmapContextCreate(None, width, height, 8, 0, space, info)
    if not ctx:
        raise SystemExit("CGBitmapContextCreate returned NULL")
    cg.CGContextSetInterpolationQuality(ctx, interp)
    rect = CGRect(CGPoint(0.0, 0.0), CGSize(float(width), float(height)))
    cg.CGContextDrawImage(ctx, rect, image)
    out = cg.CGBitmapContextCreateImage(ctx)
    if not out:
        raise SystemExit("CGBitmapContextCreateImage returned NULL")

    dst_url = cfurl(dst_path)
    uti = cfstr(uti_for(dst_path))
    dest = io.CGImageDestinationCreateWithURL(dst_url, uti, 1, None)
    if not dest:
        raise SystemExit("CGImageDestinationCreateWithURL returned NULL")
    io.CGImageDestinationAddImage(dest, out, None)
    ok = io.CGImageDestinationFinalize(dest)

    cf.CFRelease(dest)
    cf.CFRelease(uti)
    cf.CFRelease(dst_url)
    cg.CGImageRelease(out)
    cg.CGContextRelease(ctx)
    cg.CGImageRelease(image)
    cf.CFRelease(source)
    cf.CFRelease(src_url)
    if not ok:
        raise SystemExit("CGImageDestinationFinalize failed")


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 4:
        raise SystemExit(__doc__)
    resize(a[0], a[1], int(a[2]), int(a[3]), int(a[4]) if len(a) > 4 else 3)
