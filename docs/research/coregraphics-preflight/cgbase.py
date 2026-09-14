"""Minimal ctypes bindings to CoreFoundation / CoreGraphics / ImageIO.

Measuring instrument for paperhanger Task 0. Not a deliverable.
"""
import ctypes
import ctypes.util
import subprocess
from ctypes import (c_void_p, c_long, c_ulong, c_bool, c_char_p, c_uint32,
                    c_int32, c_double, c_size_t, byref, Structure)

cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
io = ctypes.cdll.LoadLibrary(ctypes.util.find_library("ImageIO"))

kCFStringEncodingUTF8 = 0x08000100

cf.CFStringCreateWithCString.restype = c_void_p
cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
cf.CFURLCreateWithFileSystemPath.restype = c_void_p
cf.CFURLCreateWithFileSystemPath.argtypes = [c_void_p, c_void_p, c_long, c_bool]
cf.CFRelease.argtypes = [c_void_p]
cf.CFDictionaryGetValue.restype = c_void_p
cf.CFDictionaryGetValue.argtypes = [c_void_p, c_void_p]
cf.CFNumberGetValue.restype = c_bool
cf.CFNumberGetValue.argtypes = [c_void_p, c_long, c_void_p]
cf.CFStringGetCString.restype = c_bool
cf.CFStringGetCString.argtypes = [c_void_p, c_char_p, c_long, c_uint32]

io.CGImageSourceCreateWithURL.restype = c_void_p
io.CGImageSourceCreateWithURL.argtypes = [c_void_p, c_void_p]
io.CGImageSourceCreateImageAtIndex.restype = c_void_p
io.CGImageSourceCreateImageAtIndex.argtypes = [c_void_p, c_size_t, c_void_p]
io.CGImageSourceCopyPropertiesAtIndex.restype = c_void_p
io.CGImageSourceCopyPropertiesAtIndex.argtypes = [c_void_p, c_size_t, c_void_p]
io.CGImageSourceGetType.restype = c_void_p
io.CGImageSourceGetType.argtypes = [c_void_p]
io.CGImageSourceGetCount.restype = c_size_t
io.CGImageSourceGetCount.argtypes = [c_void_p]
io.CGImageDestinationCreateWithURL.restype = c_void_p
io.CGImageDestinationCreateWithURL.argtypes = [c_void_p, c_void_p, c_size_t,
                                               c_void_p]
io.CGImageDestinationAddImage.argtypes = [c_void_p, c_void_p, c_void_p]
io.CGImageDestinationFinalize.restype = c_bool
io.CGImageDestinationFinalize.argtypes = [c_void_p]

cg.CGImageGetWidth.restype = c_size_t
cg.CGImageGetWidth.argtypes = [c_void_p]
cg.CGImageGetHeight.restype = c_size_t
cg.CGImageGetHeight.argtypes = [c_void_p]
cg.CGImageGetBitsPerComponent.restype = c_size_t
cg.CGImageGetBitsPerComponent.argtypes = [c_void_p]
cg.CGImageGetBitsPerPixel.restype = c_size_t
cg.CGImageGetBitsPerPixel.argtypes = [c_void_p]
cg.CGImageGetColorSpace.restype = c_void_p
cg.CGImageGetColorSpace.argtypes = [c_void_p]
cg.CGImageRelease.argtypes = [c_void_p]
cg.CGColorSpaceGetModel.restype = c_int32
cg.CGColorSpaceGetModel.argtypes = [c_void_p]
cg.CGColorSpaceCopyName.restype = c_void_p
cg.CGColorSpaceCopyName.argtypes = [c_void_p]
cg.CGColorSpaceCreateDeviceRGB.restype = c_void_p
cg.CGBitmapContextCreate.restype = c_void_p
cg.CGBitmapContextCreate.argtypes = [c_void_p, c_size_t, c_size_t, c_size_t,
                                     c_size_t, c_void_p, c_uint32]
cg.CGBitmapContextCreateImage.restype = c_void_p
cg.CGBitmapContextCreateImage.argtypes = [c_void_p]
cg.CGContextSetInterpolationQuality.argtypes = [c_void_p, c_int32]
cg.CGContextRelease.argtypes = [c_void_p]


class CGPoint(Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


class CGSize(Structure):
    _fields_ = [("width", c_double), ("height", c_double)]


class CGRect(Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


cg.CGContextDrawImage.argtypes = [c_void_p, CGRect, c_void_p]

kCGImageAlphaNoneSkipLast = 5
INTERP = {"default": 0, "none": 1, "low": 2, "high": 3, "medium": 4}
COLORSPACE_MODEL = {-1: "Unknown", 0: "Monochrome", 1: "RGB", 2: "CMYK",
                    3: "Lab", 4: "DeviceN", 5: "Indexed", 6: "Pattern",
                    7: "XYZ"}


class DecodeFailed(RuntimeError):
    """A decode that produced the wrong number of bytes, or no bytes."""


def raw_pixels(path, width, height, channels="RGB"):
    """Decode to raw 8-bit samples, and refuse to return anything else.

    Every comparison in these instruments is `raw_pixels(a) == raw_pixels(b)`,
    so a decode that fails must not come back as bytes that can compare equal
    to another failure. `magick` writes nothing to stdout when it cannot read
    a file, and two empty buffers are equal, which reads as SAME -- agreement
    reported where there was no measurement at all. The expected length is
    known from the shape, so check it: this raises rather than returning
    short.
    """
    done = subprocess.run(["magick", path, "-depth", "8", "%s:-" % channels],
                          capture_output=True)
    want = width * height * len(channels)
    if done.returncode != 0 or len(done.stdout) != want:
        raise DecodeFailed(
            "magick %s: rc=%d, %d bytes, expected %d (%dx%d %s)%s"
            % (path, done.returncode, len(done.stdout), want, width, height,
               channels,
               ": " + done.stderr.decode("utf-8", "replace").strip()
               if done.stderr else ""))
    return done.stdout


def raw_rgb(path, width, height):
    """The three-channel case, which is what most of these instruments want."""
    return raw_pixels(path, width, height, "RGB")


def cfstr(s):
    return cf.CFStringCreateWithCString(None, s.encode("utf-8"),
                                        kCFStringEncodingUTF8)


def cfurl(path):
    p = cfstr(path)
    u = cf.CFURLCreateWithFileSystemPath(None, p, 0, False)
    cf.CFRelease(p)
    return u


def from_cfstring(ref):
    if not ref:
        return None
    buf = ctypes.create_string_buffer(1024)
    if cf.CFStringGetCString(ref, buf, 1024, kCFStringEncodingUTF8):
        return buf.value.decode("utf-8")
    return None


def dict_get(d, key_name, module=io):
    """Look up a dictionary value by an exported CFString constant name."""
    try:
        key = c_void_p.in_dll(module, key_name)
    except ValueError:
        return None
    return cf.CFDictionaryGetValue(d, key)


def cfnumber_int(ref):
    if not ref:
        return None
    out = c_long(0)
    # kCFNumberLongType == 10
    if cf.CFNumberGetValue(ref, 10, byref(out)):
        return out.value
    return None


def uti_for(path):
    ext = path.rsplit(".", 1)[-1].lower()
    return {"png": "public.png", "jpg": "public.jpeg", "jpeg": "public.jpeg",
            "heic": "public.heic", "tif": "public.tiff",
            "tiff": "public.tiff", "avif": "public.avif"}[ext]
