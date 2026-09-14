"""The only module in this project that touches ctypes.

Everything above it sees Python values and ImagingError. This is the one
layer that can crash the process rather than raise, which is why every
call whose result can be NULL goes through `_checked`, and why every
owned handle goes into a `Scope` rather than relying on a CFRelease at
some return path.

CoreFoundation ownership rule: anything from a function with Create or
Copy in its name is ours to release. Anything from a Get is not.

The three library handles are `_CF`, `_CG_LIB` and `_IO_LIB` rather than
the `_cf`/`_cg`/`_io` the plan wrote. This module is itself called `_cg`,
so a module-level `_cg` reads as `_cg._cg` from the outside and as a
shadowed name from the inside -- and Task 4's own test monkeypatches
`_cg._cg.CGContextSetInterpolationQuality`, which is the spelling that
motivated the change.

WHAT THE DESTINATION BITMAP MUST BE, AND WHY IT IS DERIVED

`CGBitmapContextCreate` does not accept an arbitrary (colour space, bits,
alpha) triple, and the ones it refuses are not the only ones that are
wrong. Measured on this machine, on a 150x200 gradient reduced to 75x100:

  source model   alpha info          result
  Monochrome     kCGImageAlphaNone   correct, byte-identical to sips
  Monochrome     NoneSkipLast        context created, output ALL BLACK
  RGB            kCGImageAlphaNone   NULL context
  RGB            NoneSkipLast        correct
  RGB + alpha    NoneSkipLast        opaque; source composited onto black
  RGB + alpha    PremultipliedLast   alpha preserved
  Indexed        every combination   NULL context

So a single hardcoded alpha value is wrong whichever one is picked: the
plan's `kCGImageAlphaNoneSkipLast` blackens the ten grayscale wallpapers
in the corpus and flattens the twelve alpha-bearing PNGs, and its obvious
counterpart `kCGImageAlphaNone` returns NULL for the other 884 files.
`_bitmap_format` derives all three fields from the source instead, and
refuses a model it cannot serve by name -- see the constraint that `_cg`
never hands a NULL onward.

The grayscale failure only fires when the draw SCALES. At 1:1 the same
bad pairing round-trips correctly, which is why it survived measurement
twice before Task 0 caught it.
"""

import ctypes
import ctypes.util
from ctypes import (CDLL, Structure, c_bool, c_char_p, c_double, c_int32,
                    c_long, c_uint32, c_void_p)
from pathlib import Path

from .imaging import ImagingError


def _framework(name: str) -> CDLL:
    """Load a system framework, or say which one is missing.

    `find_library` returning None would otherwise reach `CDLL(None)`, which
    on macOS succeeds and hands back the main executable -- every later
    lookup then fails as a bare AttributeError naming a CoreGraphics symbol,
    which describes the symptom and not the cause.
    """
    path = ctypes.util.find_library(name)
    if path is None:
        raise ImagingError(f"the {name} framework is not available")
    return CDLL(path)


_CF = _framework("CoreFoundation")
_CG_LIB = _framework("CoreGraphics")
_IO_LIB = _framework("ImageIO")

kCFStringEncodingUTF8 = 0x08000100
kCFURLPOSIXPathStyle = 0
kCFNumberDoubleType = 13
kCGInterpolationHigh = 3

# CGImageAlphaInfo. The low five bits of a CGBitmapInfo.
kCGImageAlphaNone = 0
kCGImageAlphaPremultipliedLast = 1
kCGImageAlphaLast = 3
kCGImageAlphaNoneSkipLast = 5
kCGImageAlphaNoneSkipFirst = 6
kCGBitmapAlphaInfoMask = 0x1F

# CGColorSpaceModel.
kCGColorSpaceModelMonochrome = 0
kCGColorSpaceModelRGB = 1
kCGColorSpaceModelIndexed = 5

# For error messages only: a model we refuse should say which one it was.
COLOUR_MODELS = {
    -1: "unknown", 0: "monochrome", 1: "RGB", 2: "CMYK", 3: "Lab",
    4: "DeviceN", 5: "indexed", 6: "pattern", 7: "XYZ",
}

# The alpha infos that mean "no alpha channel here". Everything else in the
# enum carries one, whether premultiplied, straight, or alpha-only.
_OPAQUE_ALPHA = frozenset(
    {kCGImageAlphaNone, kCGImageAlphaNoneSkipLast, kCGImageAlphaNoneSkipFirst})


class CGPoint(Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


class CGSize(Structure):
    _fields_ = [("width", c_double), ("height", c_double)]


class CGRect(Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


def _declare():
    """Every signature, in one place, so an argtypes mistake is findable."""
    _CF.CFStringCreateWithCString.restype = c_void_p
    _CF.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
    _CF.CFURLCreateWithFileSystemPath.restype = c_void_p
    _CF.CFURLCreateWithFileSystemPath.argtypes = [c_void_p, c_void_p, c_long,
                                                  c_bool]
    _CF.CFNumberCreate.restype = c_void_p
    _CF.CFNumberCreate.argtypes = [c_void_p, c_long, c_void_p]
    _CF.CFDictionaryCreate.restype = c_void_p
    _CF.CFDictionaryCreate.argtypes = [c_void_p, c_void_p, c_void_p, c_long,
                                       c_void_p, c_void_p]
    _CF.CFRelease.restype = None
    _CF.CFRelease.argtypes = [c_void_p]

    _IO_LIB.CGImageSourceCreateWithURL.restype = c_void_p
    _IO_LIB.CGImageSourceCreateWithURL.argtypes = [c_void_p, c_void_p]
    _IO_LIB.CGImageSourceCreateImageAtIndex.restype = c_void_p
    _IO_LIB.CGImageSourceCreateImageAtIndex.argtypes = [c_void_p, c_long,
                                                        c_void_p]
    _IO_LIB.CGImageDestinationCreateWithURL.restype = c_void_p
    _IO_LIB.CGImageDestinationCreateWithURL.argtypes = [c_void_p, c_void_p,
                                                        c_long, c_void_p]
    _IO_LIB.CGImageDestinationAddImage.restype = None
    _IO_LIB.CGImageDestinationAddImage.argtypes = [c_void_p, c_void_p, c_void_p]
    _IO_LIB.CGImageDestinationFinalize.restype = c_bool
    _IO_LIB.CGImageDestinationFinalize.argtypes = [c_void_p]

    _CG_LIB.CGImageGetWidth.restype = c_long
    _CG_LIB.CGImageGetWidth.argtypes = [c_void_p]
    _CG_LIB.CGImageGetHeight.restype = c_long
    _CG_LIB.CGImageGetHeight.argtypes = [c_void_p]
    _CG_LIB.CGImageGetBitsPerComponent.restype = c_long
    _CG_LIB.CGImageGetBitsPerComponent.argtypes = [c_void_p]
    _CG_LIB.CGImageGetAlphaInfo.restype = c_uint32
    _CG_LIB.CGImageGetAlphaInfo.argtypes = [c_void_p]
    _CG_LIB.CGImageGetColorSpace.restype = c_void_p
    _CG_LIB.CGImageGetColorSpace.argtypes = [c_void_p]
    _CG_LIB.CGImageCreateWithImageInRect.restype = c_void_p
    _CG_LIB.CGImageCreateWithImageInRect.argtypes = [c_void_p, CGRect]
    _CG_LIB.CGImageRelease.restype = None
    _CG_LIB.CGImageRelease.argtypes = [c_void_p]
    _CG_LIB.CGColorSpaceGetModel.restype = c_int32
    _CG_LIB.CGColorSpaceGetModel.argtypes = [c_void_p]
    _CG_LIB.CGColorSpaceCreateWithName.restype = c_void_p
    _CG_LIB.CGColorSpaceCreateWithName.argtypes = [c_void_p]
    _CG_LIB.CGBitmapContextCreate.restype = c_void_p
    _CG_LIB.CGBitmapContextCreate.argtypes = [c_void_p, c_long, c_long, c_long,
                                              c_long, c_void_p, c_uint32]
    _CG_LIB.CGBitmapContextCreateImage.restype = c_void_p
    _CG_LIB.CGBitmapContextCreateImage.argtypes = [c_void_p]
    _CG_LIB.CGContextSetInterpolationQuality.restype = None
    _CG_LIB.CGContextSetInterpolationQuality.argtypes = [c_void_p, c_int32]
    _CG_LIB.CGContextDrawImage.restype = None
    _CG_LIB.CGContextDrawImage.argtypes = [c_void_p, CGRect, c_void_p]
    _CG_LIB.CGContextRelease.restype = None
    _CG_LIB.CGContextRelease.argtypes = [c_void_p]


_declare()


def _checked(ptr, what: str, path):
    """Turn a NULL into an ImagingError. The single chokepoint.

    Nearly every FFI crash in a pipeline like this is a NULL from a failed
    load handed straight to the next function. Checking here keeps the
    executor's one-exception-type model true.
    """
    if not ptr:
        raise ImagingError(f"could not {what} for {path}")
    return ptr


class Scope:
    """Releases every handle put into it, on the way out either way.

    `release` is injectable so a test can observe that releasing happened
    without reaching into CoreFoundation. It REPLACES the real release
    rather than running beside it, which is what lets a test hand in
    integers that are not handles; the cost is that a scope built with an
    observer leaks whatever real handles it was also given, so use it on
    small images only.
    """

    def __init__(self, release=None):
        self._handles = []
        self._release = release

    def own(self, ptr, kind="cf"):
        self._handles.append((ptr, kind))
        return ptr

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for ptr, kind in reversed(self._handles):
            if self._release is not None:
                self._release(ptr)
            elif kind == "image":
                _CG_LIB.CGImageRelease(ptr)
            elif kind == "context":
                _CG_LIB.CGContextRelease(ptr)
            else:
                _CF.CFRelease(ptr)
        self._handles.clear()
        return False


def _cfstr(scope, s: str):
    """A CFString from a Python str.

    Encoded with surrogateescape because the strings that reach here are
    filenames as often as they are constants, and `str(path).encode()`
    raises UnicodeEncodeError for a filename whose bytes are not UTF-8.
    That would put a second exception type through this layer. Encoding
    the surrogates back to their original bytes instead leaves the
    decision to CoreFoundation, which returns NULL for them -- and NULL is
    an ImagingError naming the path, which is the contract.
    """
    return scope.own(_checked(
        _CF.CFStringCreateWithCString(None, s.encode("utf-8", "surrogateescape"),
                                      kCFStringEncodingUTF8),
        "build a CFString", s))


def _cfurl(scope, path):
    path = str(path)
    return scope.own(_checked(
        _CF.CFURLCreateWithFileSystemPath(None, _cfstr(scope, path),
                                          kCFURLPOSIXPathStyle, False),
        "build a CFURL", path))


def load(scope, path):
    """Decode an image. Raises ImagingError for anything not an image."""
    path = Path(path)
    source = scope.own(_checked(
        _IO_LIB.CGImageSourceCreateWithURL(_cfurl(scope, path), None),
        "open", path))
    return scope.own(_checked(
        _IO_LIB.CGImageSourceCreateImageAtIndex(source, 0, None),
        "decode", path), kind="image")


def dimensions(image):
    """(width, height) in pixels. Both are Gets; neither can be NULL."""
    return (_CG_LIB.CGImageGetWidth(image), _CG_LIB.CGImageGetHeight(image))


def colour_model(image, path="<image>") -> str:
    """The name of the source's colour model: 'RGB', 'monochrome', ...

    Reported rather than inferred, because the file extension does not
    carry it and the IHDR does not survive the decode: ImageIO expands a
    palettised PNG to RGB but hands a colour-type-0 PNG back as
    monochrome, and those two go to different bitmap formats.
    """
    space = _checked(_CG_LIB.CGImageGetColorSpace(image),
                     "read the colour space", path)   # a Get: not ours
    model = _CG_LIB.CGColorSpaceGetModel(space)
    return COLOUR_MODELS.get(model, str(model))


def has_alpha(image) -> bool:
    """Whether the decoded image carries an alpha channel."""
    alpha = _CG_LIB.CGImageGetAlphaInfo(image) & kCGBitmapAlphaInfoMask
    return alpha not in _OPAQUE_ALPHA


def bitmap_format(image, path):
    """(colour space, bits per component, bitmap info) to receive `image`.

    The colour space comes from a Get and is therefore NOT owned; it is
    returned rather than re-fetched so that a caller cannot accidentally
    put it in a scope.

    Derived, never hardcoded -- the module docstring has the measurements.
    Three rules:

      * MONOCHROME takes kCGImageAlphaNone. NoneSkipLast is accepted by
        CGBitmapContextCreate and renders the whole frame black on any
        scaling draw; PremultipliedLast is accepted too and turns a
        grayscale PNG into a gray+alpha one, changing the file's structure
        for no gain. Ten of the 894 corpus photographs are monochrome.
      * RGB takes PremultipliedLast when the source has an alpha channel
        and NoneSkipLast when it does not. NoneSkipLast on an
        alpha-bearing source composites it onto the context's black ground
        and drops the channel, which is what `sips` does not do and what
        twelve corpus PNGs would notice. kCGImageAlphaNone is not a legal
        RGB context format at all: it returns NULL.
      * ANYTHING ELSE is refused by name. Indexed is the one that reaches
        here in practice -- CGBitmapContextCreate returns NULL for it under
        every alpha setting, and a NULL travelling onward is what this
        layer exists to prevent.

    Bits per component follow the source too. `sips` drops a 16-bit source
    to 8-bit on both its resample and its pad path; preserving it here is
    the same intended divergence §4 of the design already pins for crop.
    No corpus file is 16-bit, so this is latent rather than live.
    """
    space = _checked(_CG_LIB.CGImageGetColorSpace(image),
                     "read the colour space", path)   # a Get: not ours
    model = _CG_LIB.CGColorSpaceGetModel(space)

    if model == kCGColorSpaceModelMonochrome:
        info = kCGImageAlphaNone
    elif model == kCGColorSpaceModelRGB:
        info = (kCGImageAlphaPremultipliedLast if has_alpha(image)
                else kCGImageAlphaNoneSkipLast)
    else:
        raise ImagingError(
            f"cannot build a bitmap context for the "
            f"{COLOUR_MODELS.get(model, model)} colour space of {path}")

    # 8 or 16; there is no legal integer format between them, and a source
    # deeper than 16 (a float TIFF) would need kCGBitmapFloatComponents,
    # which no corpus file asks for.
    bits = 16 if _CG_LIB.CGImageGetBitsPerComponent(image) > 8 else 8
    return space, bits, info


def bitmap_context(scope, image, width: int, height: int, path):
    """A drawing destination shaped like `image`'s own colour space.

    Every caller that draws goes through this rather than calling
    CGBitmapContextCreate itself, so that the three-way choice above is
    made once. bytesPerRow is 0 throughout: CoreGraphics picks the stride,
    and it is the only party that knows the alignment it wants.
    """
    space, bits, info = bitmap_format(image, path)
    return scope.own(_checked(
        _CG_LIB.CGBitmapContextCreate(None, int(width), int(height), bits, 0,
                                      space, info),
        f"create a {width}x{height} bitmap context", path), kind="context")


def _png_destination(scope, out_path):
    return scope.own(_checked(
        _IO_LIB.CGImageDestinationCreateWithURL(
            _cfurl(scope, out_path), _cfstr(scope, "public.png"), 1, None),
        "create a PNG destination", out_path))


def write_png(scope, image, out_path, options=None):
    """Encode `image` as PNG, or raise.

    The destination is unlinked first for the reason `imaging._run` takes a
    `produces=`: a stale file from an earlier run must not be able to stand
    in for output this run never produced. Finalize returning false is the
    other half -- ImageIO reports a refused write there and nowhere else,
    so an unchecked call is a silent success with no file behind it.
    """
    try:
        Path(out_path).unlink(missing_ok=True)
    except OSError as exc:
        # NotADirectoryError when a component of the path is a file,
        # PermissionError when the directory is not ours. Both are the
        # caller's problem and both must leave as the one exception type
        # this layer promises, not as a second kind for `execute` to catch.
        raise ImagingError(f"could not write {out_path}: {exc}") from exc
    dest = _png_destination(scope, out_path)
    _IO_LIB.CGImageDestinationAddImage(dest, image, options)
    if not _IO_LIB.CGImageDestinationFinalize(dest):
        raise ImagingError(f"could not write {out_path}")
