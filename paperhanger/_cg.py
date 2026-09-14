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
  Monochrome     NoneSkipLast        accepted; output ALL BLACK
  Mono + alpha   NoneSkipLast        accepted; composited, NOT black
  Mono + alpha   kCGImageAlphaNone   accepted; composited, channel gone
  Mono + alpha   PremultipliedLast   alpha preserved
  RGB            kCGImageAlphaNone   NULL context
  RGB            NoneSkipLast        correct
  RGB + alpha    NoneSkipLast        opaque; source composited onto black
  RGB + alpha    PremultipliedLast   alpha preserved
  Indexed        every combination   NULL context
  32-bit float   every INTEGER one   NULL context, at 8, 16 and 32 bpc
  32-bit float   32 bpc + FloatComponents   accepted, both alpha values

"All black" is the OPAQUE grey case only. The same context given a
grey+alpha source returns a composited picture instead -- 0, 33, 66, 98
where the source says roughly 0, 64, 128, 191 at alpha 128 -- which is
wrong in a way that looks far more plausible than black does.

So a single hardcoded alpha value is wrong whichever one is picked: the
plan's `kCGImageAlphaNoneSkipLast` blackens the ten grayscale wallpapers
in the corpus and flattens the twelve alpha-bearing PNGs, and its obvious
counterpart `kCGImageAlphaNone` returns NULL for the other 884 files.
`bitmap_format` derives all three fields from the source instead, and
refuses a model or a depth it cannot serve by name -- see the constraint
that `_cg` never hands a NULL onward.

Note the asymmetry in the table, because it is the reason the failure is
silent rather than loud: RGB REJECTS the wrong value with a NULL, and
monochrome ACCEPTS it and renders the wrong picture.

The grayscale failure only fires when the draw SCALES. At 1:1 the same
bad pairing round-trips correctly, which is why it survived measurement
twice before Task 0 caught it. `_resample` does not draw at 1:1 at all
-- see the identity case there -- so the pairing is now only reached
where it is visible, and `tests/test_resize_differential.py` compares a
REDUCING resample against `sips` for each of the four rows a fixture can
produce: monochrome and RGB, each with and without alpha. The Indexed
row is a refusal rather than a picture and has its own test; nothing in
`tests/pixels.py` writes a 32-bit float PNG, so that row is measured in
the preflight instruments and nowhere else.

HALF OF THAT TABLE APPLIES WHERE THE DESTINATION IS OURS INSTEAD.
`normalize_to_srgb_png_file` converts INTO sRGB, so the colour model of
its destination is decided before the source is looked at and only the
alpha column is still a question -- `_rgb_alpha_info` is the rule both
callers share. The rest of the table inverts there: a colour model this
one refuses as a DESTINATION, indexed and CMYK included, is a perfectly
ordinary SOURCE for a draw into sRGB, and the depth is ours to pick
rather than the source's to keep.
"""

import ctypes
import ctypes.util
from ctypes import (CDLL, Structure, c_bool, c_char_p, c_double, c_int32,
                    c_long, c_uint32, c_void_p)
from pathlib import Path


class ImagingError(RuntimeError):
    """An imaging operation failed.

    Anything this layer refuses -- a file ImageIO will not decode, a colour
    space or bit depth no bitmap context accepts, a destination that cannot
    be written -- and, through `imaging`, an `upscayl-bin` invocation that
    did not succeed. `sips` used to be in that list; since Task 6 nothing
    here asks it to write, and `probe` reads without raising. This layer
    raises this and nothing else on purpose, so `execute` still catches ONE
    type per photo, reports that photo failed, and carries on with the run.

    It LIVES here rather than in `imaging` because `imaging` now imports this
    module: crop is a CoreGraphics call, so the arrow between the two
    modules reversed and the exception had to travel with it. Every existing
    caller keeps working -- `imaging` re-exports it, and `imaging.ImagingError`
    is the same class object as `_cg.ImagingError`, so an `except` on either
    spelling catches a raise from either layer.

    It subclasses RuntimeError for compatibility with callers that predate
    it. Tests should not use `pytest.raises(RuntimeError)` as a stand-in
    for an unrelated failure, because this satisfies it.
    """


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
kCGImageAlphaNoneSkipLast = 5
kCGImageAlphaNoneSkipFirst = 6
kCGBitmapAlphaInfoMask = 0x1F

# CGColorSpaceModel. Only the two this module can build a context for;
# every other value is refused through COLOUR_MODELS, which has the names.
kCGColorSpaceModelMonochrome = 0
kCGColorSpaceModelRGB = 1

# For error messages only: a model we refuse should say which one it was.
COLOUR_MODELS = {
    -1: "unknown", 0: "monochrome", 1: "RGB", 2: "CMYK", 3: "Lab",
    4: "DeviceN", 5: "indexed", 6: "pattern", 7: "XYZ",
}

# The alpha infos that mean "no alpha channel here". Everything else in the
# enum carries one, whether premultiplied, straight, or alpha-only.
_OPAQUE_ALPHA = frozenset(
    {kCGImageAlphaNone, kCGImageAlphaNoneSkipLast, kCGImageAlphaNoneSkipFirst})

# The name of the one colour space this module ever ASKS for rather than
# reads off a source. `normalize_to_srgb_png_file` converts into it.
#
# `in_dll` is right here and was wrong for the dictionary callbacks in
# `_quality_options`, and the difference is what the symbol IS. This one is
# a `const CFStringRef` -- a POINTER variable -- so reading it as a c_void_p
# reads the pointer: CFGetTypeID says CFString and the text is
# `kCGColorSpaceSRGB`. The callbacks are a STRUCT whose first word is a
# version number, so the same spelling there read 0 and handed
# CFDictionaryCreate a NULL table. Checked rather than assumed both times.
#
# Building the same CFString ourselves happens to work -- the constant's
# value IS the literal "kCGColorSpaceSRGB", and both spellings return the
# same CGColorSpace pointer -- but the literal is Apple's to change and the
# exported symbol is the documented form.
_SRGB_NAME = c_void_p.in_dll(_CG_LIB, "kCGColorSpaceSRGB")

# The four output formats, as the uniform type identifiers ImageIO wants.
# `formats.EXTENSIONS` has the same four keys and is the layer that decides
# which of them a run uses; this is only the translation, and a format that
# is not here is refused by name rather than reaching CoreFoundation as a
# CFString nothing recognises.
UTI = {"heic": "public.heic", "jpeg": "public.jpeg",
       "avif": "public.avif", "png": "public.png"}


class CGPoint(Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


class CGSize(Structure):
    _fields_ = [("width", c_double), ("height", c_double)]


class CGRect(Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


def _declare():
    """Every signature, in one place, so an argtypes mistake is findable.

    A function that is NOT declared here does not fail politely when called:
    ctypes defaults its arguments to C int, so a 64-bit handle is truncated
    to 32 bits and the process segfaults. Two probes written against this
    module died that way, on `CGImageGetBitsPerPixel` and
    `CGImageGetBitmapInfo`. Adding a call means adding it here first.
    """
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


def _rgb_alpha_info(image) -> int:
    """The alpha info an RGB destination must take for this source.

    Stated once because two callers need it and they arrive from opposite
    directions: `bitmap_format` derives an RGB destination when the SOURCE
    is RGB, and `normalize_to_srgb_png_file` builds one for every source
    there is, because sRGB is an RGB space whatever came in. The
    measurements are in `bitmap_format`; the short version is that
    NoneSkipLast composites an alpha-bearing source onto the context's
    black ground and drops the channel, which twelve corpus PNGs would
    notice and `sips` does not do.
    """
    return (kCGImageAlphaPremultipliedLast if has_alpha(image)
            else kCGImageAlphaNoneSkipLast)


def bitmap_format(image, path):
    """(colour space, bits per component, bitmap info) to receive `image`.

    The colour space comes from a Get and is therefore NOT owned; it is
    returned rather than re-fetched so that a caller cannot accidentally
    put it in a scope.

    Derived, never hardcoded -- the module docstring has the measurements.
    Three rules:

      * MONOCHROME takes PremultipliedLast when the source has an alpha
        channel and kCGImageAlphaNone when it does not. Both of the other
        pairings are ACCEPTED by CGBitmapContextCreate and both are
        wrong, in two different ways that depend on the source:

          NoneSkipLast, OPAQUE grey source -- the whole frame comes back
          black. Measured, a 150x200 gradient reduced to 75x100: column 0
          all zeros against sips' 0, 26, 52, 77.
          NoneSkipLast, grey+ALPHA source -- NOT black, composited.
          Same reduction: 0, 33, 66, 98 where the source says roughly
          0, 64, 128, 191 at alpha 128.
          AlphaNone, grey+ALPHA source -- composited too, channel gone:
          values 0, 6, 12, 18 at alpha 128 came back 0, 3, 6, 9.

        Ten of the 894 corpus photographs are monochrome and none of them
        carries alpha, which is why this branch has to be right by
        construction rather than by the corpus happening not to reach it.
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

    Bits per component follow the source: 8, or 16 for a deeper one.
    `sips` drops a 16-bit source to 8-bit on both its resample and its pad
    path; preserving it here is the same intended divergence §4 of the
    design already pins for crop. No corpus file is 16-bit, so that part is
    latent rather than live.

    Deeper than 16 is refused by name. A 32-bit float source decodes into
    a colour space that accepts no INTEGER context at all -- 8, 16 and 32
    bits per component all return NULL -- so the alternative is a failure
    whose message says only that a context could not be made, rather than
    one that says the depth is why. It is not that no context exists: at
    32 bits with kCGBitmapFloatComponents the same space accepts both
    PremultipliedLast and NoneSkipLast. Supporting that is a float
    pipeline end to end, which no corpus file asks for, so this refuses
    rather than pretending the depth away.
    """
    space = _checked(_CG_LIB.CGImageGetColorSpace(image),
                     "read the colour space", path)   # a Get: not ours
    model = _CG_LIB.CGColorSpaceGetModel(space)
    alpha = has_alpha(image)

    if model == kCGColorSpaceModelMonochrome:
        info = kCGImageAlphaPremultipliedLast if alpha else kCGImageAlphaNone
    elif model == kCGColorSpaceModelRGB:
        info = _rgb_alpha_info(image)
    else:
        raise ImagingError(
            f"cannot build a bitmap context for the "
            f"{COLOUR_MODELS.get(model, model)} colour space of {path}")

    source_bits = _CG_LIB.CGImageGetBitsPerComponent(image)
    if source_bits > 16:
        raise ImagingError(
            f"cannot build a bitmap context for the {source_bits}-bit "
            f"components of {path}")
    # 8 or 16; there is no legal integer format between them, and anything
    # shallower promotes to 8 without losing a value.
    bits = 16 if source_bits > 8 else 8
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


def _destination(scope, out_path, uti: str, what: str):
    return scope.own(_checked(
        _IO_LIB.CGImageDestinationCreateWithURL(
            _cfurl(scope, out_path), _cfstr(scope, uti), 1, None),
        f"create a {what} destination", out_path))


def _quality_options(scope, quality):
    """A CFDictionary carrying the lossy quality, or None for lossless.

    `sips -s formatOptions N` IS this key at N/100. Both tools are ImageIO
    underneath, and the equality is measured rather than assumed: on a
    400x300 noise PNG, `sips` and this agree byte for byte at jpeg 80 and
    90, heic 80, 85 and 90, and avif 85 -- including heic 85 reproducing
    heic 80's file exactly, which is the quantization `formats.py` records
    as the reason heic's default is 80. So the four defaults in
    `formats.DEFAULT_QUALITY` transfer with nothing re-derived.

    None means no key at all, which is not the same as 100: PNG carries no
    lossy quality, and an options dictionary holding one for it would be a
    number with no meaning riding along with every lossless write.

    THE CALLBACKS ARE PASSED BY ADDRESS, and the obvious alternative is a
    LATENT USE-AFTER-FREE. `kCFTypeDictionaryKeyCallBacks` is a STRUCT
    exported by CoreFoundation, not a pointer to one: its first word is the
    version, 0, followed by five function pointers (retain, release,
    copyDescription, equal, hash). So `c_void_p.in_dll(...)` reads that
    first word, `.value` comes back as literally `None`, and
    CFDictionaryCreate is handed a NULL callbacks table -- a dictionary
    that does not retain what you put in it. Measured with
    CFGetRetainCount on the CFNumber: `addressof` takes it 1 -> 2, the
    `in_dll` spelling leaves it at 1.

    It produces the right bytes anyway, at every format and quality, which
    is the whole problem. The only reason it works is that the `Scope`
    holds the CFNumber alive until after Finalize; anything that released
    it earlier, or any reordering of the scope, would have ImageIO reading
    freed memory for the quality. `addressof` is the address OF the struct,
    which is what the parameter wants, and then the dictionary owns its
    own reference.
    """
    if quality is None:
        return None
    key = c_void_p.in_dll(_IO_LIB, "kCGImageDestinationLossyCompressionQuality")
    value = c_double(quality / 100.0)
    number = scope.own(_checked(
        _CF.CFNumberCreate(None, kCFNumberDoubleType, ctypes.byref(value)),
        "build a quality number", quality))
    keys = (c_void_p * 1)(key.value)
    values = (c_void_p * 1)(number)
    return scope.own(_checked(
        _CF.CFDictionaryCreate(
            None, keys, values, 1,
            ctypes.addressof(c_void_p.in_dll(_CF, "kCFTypeDictionaryKeyCallBacks")),
            ctypes.addressof(c_void_p.in_dll(_CF, "kCFTypeDictionaryValueCallBacks"))),
        "build the destination options", quality))


def _write(scope, image, out_path, uti: str, what: str, options=None):
    """Encode `image` to `out_path` in `uti`, or raise.

    NO UNLINK-FIRST, unlike `imaging._run`'s `produces=`, but an unlink ON
    FAILURE, which is not the same thing and took two rounds to separate.

    Removing the destination BEFORE the write buys nothing. ImageIO
    truncates an existing destination and replaces a read-only one, and
    every bad-destination route measured -- a missing directory, a parent
    that is a file, a destination that is a directory, an unwritable
    directory -- fails at CGImageDestinationCreateWithURL, before any file
    is touched.

    Removing it AFTER a refused write is a different case and a real one.
    ImageIO writes the bytes at Finalize, so a Finalize that returns false
    leaves whatever was already at the destination exactly where it was:
    measured, a 62-byte stale file still present and still stale after the
    raise. That is an earlier run's output standing where this run's
    result should be, which is the failure `produces=` exists to prevent.

    The recovery unlink can itself fail, and in the very case that
    produced the false Finalize: a directory turned read-only mid-write
    refuses the delete too. Best effort, then -- the ImagingError is the
    report either way, and it says so when the old file is still there.

    NO METADATA TRAVELS. `CGImageDestinationAddImage` writes the image and
    the colour space the CGImage carries, and nothing else -- where `sips`
    copies the source's EXIF into the output. Measured on a 400x300 PNG
    tagged AdobeRGB1998 by `sips --matchTo`, which writes both an `iCCP`
    and an `eXIf` chunk; each variable separated by rebuilding the file
    with one chunk removed:

      source chunks        jpeg 90     heic 80             avif 85
      neither              identical   identical           identical
      iCCP only            identical   identical           identical
      eXIf only            identical*  127 bytes smaller   127 smaller
      both                 identical*  127 bytes smaller   127 smaller

    So the ICC profile is NOT the variable -- ImageIO writes the same
    `colr` box `sips` does, byte for byte, for AdobeRGB, Display P3, ROMM
    RGB and ITU-2020 alike. The EXIF is. In the HEIF family `sips` adds a
    second item (an `Exif` item, an `iref cdsc` pointing at the picture,
    and 66 bytes in `mdat`); the coded picture is identical, and ours is
    the same bytes without the metadata item.

    *THE JPEG COLUMN IS THAT FIXTURE'S PROPERTY, NOT JPEG'S, and an
    earlier version of this docstring read it as the container's. Both
    tools write an APP1 Exif segment on the way to JPEG; `sips` copies the
    source's IFD0 entries into it and ImageIO synthesises its own, so they
    agree only when the source's entries are what ImageIO would have
    written anyway -- which is exactly what `sips --matchTo` puts in an
    `eXIf` chunk. On a from-scratch source carrying one IFD0 entry `sips`'
    APP1 segment is 90 bytes against our 78, and at two entries 102
    against the same 78 -- whole segments, `FFE1` marker and length
    included, which is 2 more than the same figures quoted as payloads.
    So EXIF does not travel on ANY of the four formats, JPEG
    included, and an Orientation of 6 -- the one tag a viewer would see --
    is carried by `sips` and dropped by us.

    Both halves of all of that are pinned in
    `tests/test_encode_differential.py`.
    """
    dest = _destination(scope, out_path, uti, what)
    _IO_LIB.CGImageDestinationAddImage(dest, image, options)
    if not _IO_LIB.CGImageDestinationFinalize(dest):
        try:
            Path(out_path).unlink(missing_ok=True)
            left_behind = ""
        except OSError as exc:
            left_behind = (f"; an earlier file is still there and could not "
                           f"be removed ({exc.strerror})")
        raise ImagingError(f"could not write {out_path}{left_behind}")


def write_png(scope, image, out_path):
    """Encode `image` as PNG, or raise. The lossless half of `_write`.

    Its own name because `crop_to_file` writes PNG and nothing else -- the
    format is a property of that operation rather than a parameter of it,
    and `imaging.crop` refuses an out_path that says otherwise.
    """
    _write(scope, image, out_path, UTI["png"], "PNG")


def crop_to_file(source, x: int, y: int, width: int, height: int,
                 out_path) -> None:
    """Cut a rect out of `source` and write it as PNG.

    No workaround needed. CGImageCreateWithImageInRect takes its origin at
    the image's TOP LEFT and honours it, including an origin of 0,0 and a
    rect flush with the bottom edge -- which are exactly the two shapes
    `sips --cropOffset` silently mis-crops, and the reason the pad-and-shift
    pass this replaces existed at all. Measured on a 40x200 greyscale ramp
    whose value names its own source row: rect y=0 returns rows 0-49, y=150
    returns rows 150-199, y=75 returns rows 75-124.

    THE RECT IS NOT CLAMPED AND NOT REFUSED. CGImageCreateWithImageInRect
    INTERSECTS it with the image and hands back the overlap, at no error and
    no warning. Measured on a 400x200 source: 120x80 at x=350 comes back
    50x80, 120x80 at x=-10,y=-10 comes back 110x70, and 900x900 at the
    origin comes back as the whole 400x200. Only a rect with no overlap at
    all returns NULL. So a caller that got its arithmetic wrong gets a
    SMALLER PICTURE rather than a failure, and whatever resizes it next
    stretches the wrong region to the right dimensions.

    Two defences, and they are not the same one twice. `imaging.crop` refuses
    a rect that does not fit, because it is the layer holding both the rect
    and the measured source. The post-condition below is for the handles
    this function was given directly: it costs two Gets, and it turns that
    silent intersection into the one exception type this layer raises.
    """
    with Scope() as scope:
        image = load(scope, source)
        rect = CGRect(CGPoint(float(x), float(y)),
                      CGSize(float(width), float(height)))
        cut = scope.own(_checked(
            _CG_LIB.CGImageCreateWithImageInRect(image, rect),
            f"crop {width}x{height}+{x}+{y}", source), kind="image")

        got = dimensions(cut)
        if got != (width, height):
            raise ImagingError(
                f"crop {width}x{height}+{x}+{y} of {source} came back "
                f"{got[0]}x{got[1]}; the rect does not lie inside the image "
                f"and CoreGraphics returned the overlap"
            )

        write_png(scope, cut, out_path)


def normalize_to_srgb_png_file(source, out_path) -> None:
    """Convert `source` into sRGB and write it as PNG.

    THE DRAW IS THE CONVERSION, AND IT IS UNCONDITIONAL. `_resample` skips
    its draw when the source already has the requested dimensions, and that
    is correct THERE because `bitmap_context` builds the destination out of
    the source's own colour space, so the draw converts nothing and the skip
    deletes no work. Here the destination is a space of our choosing and the
    draw is the whole point. Nothing about this function resizes, so the
    dimensions ALWAYS match: a skip on them would fire on every source there
    is and stop converting, at no error, with a plausible PNG of the right
    size coming back. Measured against `sips --matchTo`, what that would
    cost on a 600x400 noise PNG: Adobe RGB 661,630 of 720,000 samples,
    largest difference 144; ROMM RGB 713,202 and 167; Display P3 675,884 and
    116. `tests/test_normalize_differential.py` watches the call and the
    pixels both.

    A skip on the SOURCE's colour space is refused for the same reason and
    would be more tempting -- 714 of the 895 corpus entries are tagged sRGB.
    The draw converts four things at once: colour space, colour MODEL, bit
    depth and alpha. Passing an already-sRGB source through would leave a
    monochrome source monochrome, an indexed one indexed and a 16-bit one at
    16 bits, none of which `sips --matchTo` does. "Tagged sRGB" is three
    different profile descriptions in that corpus and not one colour space
    object either.

    NO INTERPOLATION QUALITY IS SET, unlike `_resample`, which pins High by
    asserting the call because no output can tell it from Default. The draw
    here is 1:1 by construction, so nothing is interpolated: measured on a
    600x400 noise PNG, Default, None, Low and High each produce the same
    721,292 bytes. A call that cannot change the answer would be a line no
    test could check.

    THE DESTINATION IS EIGHT BITS PER COMPONENT, where `crop` and
    `resize_and_encode` keep a 16-bit source at 16. It is not an
    inconsistency: `sips --matchTo` drops it too, so this agrees with the
    reference, and the only reader of this file is upscayl-bin, which emits
    8-bit PNG -- a 16-bit intermediate would be discarded one step later at
    best. No corpus file is 16-bit.

    EVERY COLOUR MODEL IS ACCEPTED, including the ones `bitmap_format`
    refuses. Indexed, CMYK and Lab cannot be a DESTINATION; they are only a
    source here, and sRGB is the destination, so they convert like anything
    else. Measured against `sips` on a CMYK JPEG and a palettised PNG:
    identical pixels, identical colour type. Refusing them would be a
    regression on a path `sips` handled.

    IT COSTS NOTHING IN MEMORY, which is not obvious: this runs on the
    ORIGINAL on the whole-frame path, the largest image the pipeline ever
    holds, and it holds the decoded frame and a second bitmap of the same
    dimensions at once where `sips` had a process to itself. Peak RSS on
    green_leaf_closeup_2463.jpg (7680x5120), two runs each, `/usr/bin/time
    -l` per route: this 494.6 MiB, `sips --matchTo` 479.2. 21.5 MiB of ours
    is the Python interpreter and these bindings, which the `sips` route
    does not pay, so the imaging itself is 473.1 against 479.2 -- and one
    subprocess fewer per photo.
    """
    with Scope() as scope:
        image = load(scope, source)
        width, height = dimensions(image)
        space = scope.own(_checked(
            _CG_LIB.CGColorSpaceCreateWithName(_SRGB_NAME),
            "create the sRGB colour space", source))
        ctx = scope.own(_checked(
            _CG_LIB.CGBitmapContextCreate(None, width, height, 8, 0, space,
                                          _rgb_alpha_info(image)),
            f"create a {width}x{height} sRGB context", source), kind="context")
        _CG_LIB.CGContextDrawImage(
            ctx, CGRect(CGPoint(0.0, 0.0),
                        CGSize(float(width), float(height))), image)
        converted = scope.own(_checked(
            _CG_LIB.CGBitmapContextCreateImage(ctx),
            "read back the sRGB conversion", source), kind="image")
        write_png(scope, converted, out_path)


def _resample(scope, image, out_width: int, out_height: int, source):
    """`image` at exactly out_width x out_height, owned by `scope`.

    Both axes are always explicit; nothing here derives one from the other.

    INTERPOLATION IS SET TO HIGH BY NAME, and the test for it asserts the
    call rather than the output, because no output can tell High from
    Default. Measured on a 2000x1400 noise PNG at three target shapes:
    Default and High are byte-identical at all three, while None, Low and
    Medium each produce a different file. So a wrong constant would be
    caught by the differential -- except the one that is Default today and
    is Apple's to redefine tomorrow, which is the one the call pins.
    (On a greyscale ramp, Low and Medium match High too: a smooth source
    cannot tell interpolation levels apart, which is why the resample
    fixtures carry noise.)

    AT IDENTITY THE DRAW IS SKIPPED, and that is not an optimisation with a
    neutral output. `render` asks for the source's own dimensions on 577 of
    the 2734 resamples a full corpus run performs -- band 4 renders the 4x
    frame at scale 4, so `resize = needs_resize or scale != 1` computes True
    for a resample that changes nothing -- and those calls are where
    CoreGraphics is furthest from `sips` in both memory and pixels. Measured
    here, on this machine:

      * PIXELS. Over 63 corpus photographs at their own dimensions, the
        concatenated `IDAT` of the skip equals `sips`' on 63 of 63; the draw
        equals it on 11. The three files the preflight found diverging --
        green_leaf_closeup_2463, roadside_grass_2121,
        sunlight_through_leaves_8375, all 7680x5120 -- are three of the 52
        the draw alters, at 31.8-46.8% of bytes and a maximum channel delta
        of 61-97. So the draw INTRODUCES the divergence and skipping it
        removes it; there is nothing to pin.
      * ALPHA. The destination bitmap for an alpha-bearing source is
        PremultipliedLast and the encode has to undo the premultiply, so the
        draw loses to the round trip where the skip does not. All 400x300,
        as (samples differing of the total, largest difference), the skip
        byte-identical to `sips` in every row:

          RGBA at alpha 128      120,000 of 480,000    1
          grey+alpha at 128       60,400 of 240,000    1
          16-bit RGBA            179,600 of 480,000    1
          colour-key `tRNS`      120,000 of 480,000  255

        The last row is the one to read. A `tRNS` chunk on a truecolour PNG
        names a COLOUR as transparent, and ImageIO expands that to an alpha
        channel; the draw then composites the keyed pixels onto the
        context's black ground, so what was (255, 0, 255) under a
        transparent pixel comes back (0, 0, 0). Same colour type, same
        dimensions, a plausible file -- and the largest difference the draw
        produces anywhere. The skip is byte-identical to `sips`.
      * MEMORY. 7680x5120, peak RSS of a child process per route, three runs
        each: `sips` 332.1 MiB, the draw 499.2, the skip 351.0. The draw
        holds the decoded frame and a second bitmap of the same dimensions
        at once -- 7680 x 5120 x 4 is 157 MB of it -- where the skip holds
        one frame and lands within 6% of `sips`.

    THE CONDITION IS THE DIMENSIONS AND NOTHING ELSE, and that is true HERE
    for a reason that does not travel. `bitmap_context` builds the
    destination out of the SOURCE's own colour space, so the draw in this
    function converts nothing: skipping it deletes no work. Where a draw
    targets a colour space of our choosing it IS the conversion -- Task 6's
    `normalize_to_srgb_png` is exactly that -- and skipping it there would
    delete the conversion. Do not read this condition as a general licence.

    Two consequences of skipping, both measured and neither a defect:

      * EVERY SOURCE `bitmap_format` REFUSES now resizes at identity and
        raises at every other shape, because the identity path builds no
        context and `bitmap_format` is what refuses. That is the whole class
        -- indexed, CMYK, Lab, and anything above 16 bits per component --
        not the indexed case alone, and CMYK is the member that matters:
        four-channel JPEGs come out of print workflows, `sips` resampled one
        without complaint, and a photo folder is likelier to hold one than a
        palettised PNG. Measured on a CMYK JPEG (`sips --matchTo` the
        Generic CMYK profile, `sips -g space` confirming CMYK,
        `colour_model` agreeing): at identity the skip's `IDAT` is identical
        to `sips`', and at 200x150 the draw raises naming the colour space.
        So the asymmetry is between raising and succeeding correctly, not
        between two answers.

        Both members are latent in this corpus, and only our own classifier
        can say so: all 894 files through `colour_model` are 872 RGB, 12 RGB
        with alpha and 10 monochrome, every one at 8 bits per component,
        none unreadable. A `sips -g space` sweep could not establish it --
        `sips` reports a palettised PNG as `RGB`.
      * A source shallower than 8 bits per component still comes back at 8,
        because ImageIO's decode is what promotes it. The draw does the same
        thing; `sips` keeps the depth. Measured on a 1-bit greyscale PNG:
        draw and skip are byte-identical to each other and both differ from
        `sips` in depth alone. Skipping neither causes nor cures it.

    RETURNS THE SOURCE IMAGE ITSELF at identity, already owned by the
    scope its caller passed in. The caller must not release it separately,
    and must not assume the handle it gets back is a new one.
    """
    if dimensions(image) == (out_width, out_height):
        return image

    ctx = bitmap_context(scope, image, out_width, out_height, source)
    _CG_LIB.CGContextSetInterpolationQuality(ctx, kCGInterpolationHigh)
    _CG_LIB.CGContextDrawImage(
        ctx, CGRect(CGPoint(0.0, 0.0),
                    CGSize(float(out_width), float(out_height))), image)
    return scope.own(_checked(
        _CG_LIB.CGBitmapContextCreateImage(ctx),
        f"read back the {out_width}x{out_height} resample", source),
        kind="image")


def resize_and_encode_to_file(source, out_width: int, out_height: int,
                              fmt: str, quality, out_path,
                              resize: bool) -> None:
    """Resample if asked, then encode. One decode, one frame, no staging.

    The whole of `imaging.resize_and_encode`'s work, in one scope. Task 4
    left a lossless PNG between the CoreGraphics resample and the `sips`
    encode; the intermediate existed only because the two halves were in
    two processes, and with the encode here it is gone. It cost a full
    extra encode and decode of the frame, and it bought a lower peak.

    Measured on a 7680x5120 noise PNG reduced to 3840x2160 heic 80, two
    runs each, as the WHOLE-MACHINE high-water mark -- which is not the sum
    of two `ru_maxrss` figures, because that assumes both were reached at
    once. Each route's parent blocks while its child works, so the parent's
    CURRENT resident size at that moment is what adds:

      this, one pass          454.0 MiB   one process, nothing on disk
      Task 4, staged          385.0 MiB   its own resample peak, alone; by
                                          the time it spawns `sips` it has
                                          released to 59.2, so the child
                                          phase reaches only 194.2, and
                                          22.1 MB sits on disk
      pre-Task 4, one `sips`  427.0 MiB   18.6 resident in the parent while
                                          the child peaks at 408.4

    So fusing costs **6.3% over what shipped** and 17.9% over Task 4's
    shape, because the decoded frame, the resampled bitmap and the
    encoder's buffers are live together where the staged shape released
    the first two before `sips` started. What it buys is one process and
    no intermediate file. The comparison that counts is the first one --
    Task 4 existed for a single commit -- and an earlier version of this
    docstring led with the other, which flattered the change in one place
    by 46 MiB and damned it in another by 69.

    THE FORMAT IS REFUSED BY NAME. Handing an unknown one to `_cfstr`
    would build a CFString CoreFoundation is happy with and ImageIO is not,
    and `CGImageDestinationCreateWithURL` would return NULL -- an error
    reading "could not create a nosuchformat destination for <path>",
    which describes the symptom. `sips` used to exit 13 here; this is the
    same refusal with the format named as the cause.

    `quality` is the caller's, unchanged, and None for PNG -- see
    `_quality_options`. The value is not clamped or defaulted here:
    `formats.quality_for` is the layer that decides what a format's
    quality is, and a second opinion at this depth could only disagree
    with it silently.
    """
    if fmt not in UTI:
        raise ImagingError(
            f"cannot encode {out_path}: {fmt!r} is not an output format; "
            f"expected one of {', '.join(UTI)}")
    with Scope() as scope:
        image = load(scope, source)
        if resize:
            image = _resample(scope, image, out_width, out_height, source)
        _write(scope, image, out_path, UTI[fmt], fmt,
               _quality_options(scope, quality))
