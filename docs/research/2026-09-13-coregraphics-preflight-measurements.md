# CoreGraphics preflight measurements

The design for replacing `sips` with CoreGraphics defers four questions to measurement
(§6 of `docs/superpowers/specs/2026-09-13-coregraphics-imaging-design.md`), because any
of them could change the design. This document answers all four with numbers, and adds a
fifth that nobody asked for and that matters more than three of the four.

**Summary.**

| Question | Verdict |
|---|---|
| 1. EXIF orientation | Clears the way. `sips` and ImageIO agree on geometry; they disagree on whether the tag survives into the output. |
| 2. Peak memory at 300 Mpx | Clears the way. CoreGraphics peaks 30 MB higher, which is the Python interpreter. The pixel cap does not move. |
| 3. HEIC input | Clears the way. Both corpus HEICs decode, dimensions agree exactly. The corpus holds two, not one. |
| 4. Interpolation exactness | Clears the path production uses, and **changes the verification plan**. Reduction is exact from 0.10x to 0.995x. Enlargement is not exact, and neither is a reduction within half a percent of identity. |
| 5. (unasked) Destination colour space | **Changes the design.** Taking the bitmap context's colour space from the source, with `kCGImageAlphaNoneSkipLast`, renders ten real corpus images entirely black, silently. |

Everything below was measured on macOS 26.6.2 (build 25G83), arm64, 36 GiB. `python3` is
3.14.7 at `/run/current-system/sw/bin/python3`; `ctypes.util.find_library` resolves all
three system frameworks under it. `magick` is ImageMagick 7 from Homebrew, `exiftool`
13.55.

The instruments — `resize.py`, a shared `cgbase.py` of `ctypes` declarations, and four
comparison scripts — were written for this measurement and deliberately not committed.
They are measuring tools, not deliverables, and Task 1 writes the real bindings from the
spec rather than from them.

---

## 1. EXIF orientation

### Building a fixture

`sips` cannot write the tag. This is the second tool that cannot: `magick -set
exif:Orientation` and `magick -orient RightTop` both failed during design, reading back
empty and `Undefined`.

```
$ magick -size 1200x600 gradient:red-blue rot.jpg
$ /usr/bin/sips -s orientation 6 rot.jpg --out rot6.jpg
Error: Cannot do --setProperty orientation on file
Error 13: an unknown error occurred
```

No file is produced; the subsequent read reports `rot6.jpg not a valid file - skipping`.

**`exiftool` writes it, and no hand-rolled APP1 segment was needed.** The next person
hitting this wall should start here:

```
$ cp rot.jpg rot6.jpg
$ exiftool -overwrite_original -Orientation=6 -n rot6.jpg
    1 image files updated
```

The tag is really there, in a real Exif APP1 block, not in a sidecar:

```
$ exiftool -a -G1 -Orientation -n rot6.jpg
[IFD0]          Orientation                     : 6
$ magick identify -format "%w x %h  orient=%[orientation]  exif=%[EXIF:Orientation]\n" rot6.jpg
1200 x 600  orient=RightTop  exif=6
```

A walk of the JPEG segment markers confirms `FFE1` (`Exif\0\0MM\0*`) ahead of the frame
header, and `FFC0` carrying `0x0258 0x04B0` — 600 rows by 1200 columns of stored pixels.

### The measurement

```
$ /usr/bin/sips -g orientation -g pixelWidth -g pixelHeight rot6.jpg
  orientation: <nil>
  pixelWidth: 1200
  pixelHeight: 600
```

```
$ python3 probe.py rot6.jpg
  properties PixelWidth x PixelHeight: 1200 x 600
  properties Orientation: 6
  CGImageGetWidth x CGImageGetHeight: 1200 x 600
```

`probe.py` calls `CGImageSourceCreateWithURL`, `CGImageSourceCopyPropertiesAtIndex` and
`CGImageSourceCreateImageAtIndex`, then `CGImageGetWidth` / `CGImageGetHeight`.

**They agree: 1200 x 600 both ways.** Neither path rotates. Crop geometry does not
diverge on an oriented file, and the implementation adopts the behaviour both already
have — operate on stored pixels, ignore the orientation tag.

Two things worth noting anyway.

**`sips -g orientation` reports `<nil>` for a tag ImageIO reads as 6.** `sips` is built
on ImageIO, so this is not a decode disagreement; `sips`'s `orientation` property is
reading something other than `kCGImagePropertyOrientation`. Do not use `sips -g
orientation` to decide whether a file carries the tag — it will say no when the answer
is yes.

**The two paths disagree about whether the tag survives the operation.** Same resample,
both to 600 x 300:

```
$ /usr/bin/sips --resampleHeightWidth 300 600 rot6.jpg -s format png --out rot6_sips.png
$ python3 resize.py rot6.jpg rot6_cg.png 600 300 3
$ magick compare -metric AE rot6_cg.png rot6_sips.png null:
0 (0)
$ exiftool -Orientation -n rot6_sips.png rot6_cg.png
======== rot6_sips.png
Orientation                     : 6
======== rot6_cg.png
    (nothing)
```

Pixels identical; `sips` copies the orientation tag into its output, ImageIO with a NULL
destination-properties dictionary does not. Since neither rotated the pixels, the two
outputs *display* differently in any orientation-aware viewer: the `sips` wallpaper is
shown rotated 90 degrees, ours is not. **Task 1 has a decision to make here**, and it is
a decision about correctness rather than compatibility — a wallpaper whose pixels were
never rotated should probably not carry a tag telling the compositor to rotate them.

### Does it ever fire?

No, on the corpus as it stands:

```
$ python3 corpus_scan.py ~/Pictures/wallpaper
files examined: 894
orientations : {None: 621, 1: 273}
files with orientation other than 1/absent: 0
```

The design's premise holds. 273 files carry an explicit identity orientation and 621
carry none, and not one carries a tag that would rotate anything. The test suite cannot
catch a regression here, which is why the fixture recipe above is written down.

---

## 2. Peak memory at 300 Mpx

The brief's generator command does not produce what it looks like it produces, twice
over. `magick -size 15000x20000 gradient:black-white huge.png` writes a **16-bit
grayscale** PNG, and adding `-type TrueColor` is not enough to get colour — a two-stop
gradient has under 256 distinct values, so ImageMagick palettizes it and ImageIO decodes
an **Indexed** colour space, on which `CGBitmapContextCreate` returns NULL. Forcing
`-define png:color-type=2` produces the truecolor 300 Mpx frame the question is about,
which is also what the upscaler emits in production.

### Truecolor, 15000 x 20000 to 3750 x 5000

```
$ /usr/bin/time -l /usr/bin/sips --resampleHeightWidth 5000 3750 huge_rgb.png \
      -s format png --out sips_big_rgb.png
        1.80 real
        1421705216  maximum resident set size

$ /usr/bin/time -l python3 resize.py huge_rgb.png cg_big_rgb.png 3750 5000 3
        1.84 real
        1453146112  maximum resident set size
```

| path | peak RSS | |
|---|---|---|
| `sips` | 1,421,705,216 B | 1355.8 MiB |
| CoreGraphics | 1,453,146,112 B | 1385.8 MiB |
| difference | +31,440,896 B | **+30.0 MiB, +2.2%** |

Outputs are pixel-identical (`cmp` over `magick ... RGB:-` for both).

### Grayscale, same frame size

```
$ /usr/bin/time -l /usr/bin/sips --resampleHeightWidth 5000 3750 huge.png \
      -s format png --out sips_big.png
        653705216  maximum resident set size
$ /usr/bin/time -l python3 resize.py huge.png cg_big_fixed.png 3750 5000 3
        683769856  maximum resident set size
```

623.4 MiB against 652.1 MiB — **+28.7 MiB, +4.6%**. Pixel-identical output.

**This clears the way and does not move the pixel cap.** The gap is 30 MB in both runs,
independent of the pixel count, which is the Python interpreter and the loaded
frameworks rather than anything about imaging. Both paths materialise the whole decoded
source — 300 Mpx at 4 bytes is 1.2 GB, and that is the bulk of both figures — plus a
75 MB destination bitmap. Neither streams. The cap exists for the same reason after this
change as before it, and at the same value.

Worth stating for Task 1: if the cap ever needs to rise, `CGImageSourceCreateThumbnail-
AtIndex` decodes to a bounded size without materialising the full frame, and has no
`sips` equivalent. That is an opportunity, not a requirement, and it would break byte
equivalence.

---

## 3. HEIC input

**The corpus holds two HEIC files, not the one the design and brief describe.**

```
$ python3 corpus_scan.py ~/Pictures/wallpaper
source UTIs  : {'public.jpeg': 841, 'public.png': 47, 'org.webmproject.webp': 3,
                'com.compuserve.gif': 1, 'public.heic': 2}
```

Both were measured.

```
$ /usr/bin/sips -g pixelWidth -g pixelHeight -g format -g space purple_nebula_glow_0312_x.heic
  pixelWidth: 7680
  pixelHeight: 4800
  format: heic
  space: RGB
$ python3 probe.py purple_nebula_glow_0312_x.heic
  source type (UTI): public.heic
  image count: 1
  properties PixelWidth x PixelHeight: 7680 x 4800
  CGImageGetWidth x CGImageGetHeight: 7680 x 4800
  bitsPerComponent: 8  bitsPerPixel: 32
  colorSpace: model=RGB name=kCGColorSpaceSRGB
```

| file | `sips -g` | `CGImageGetWidth/Height` |
|---|---|---|
| `purple_nebula_glow_0312_x.heic` | 7680 x 4800 | 7680 x 4800 |
| `koi_circle_2135_x.heic` | 5760 x 3240 | 5760 x 3240 |

**`CGImageSourceCreateWithURL` decodes both, dimensions agree exactly, and this clears
the way.** Both report a single image at index 0, 8 bits per component, sRGB, and an
explicit orientation of 1.

The census also turned up three WebP files and one GIF, which §4 of the design does not
mention. ImageIO opened all 894 files (`files ImageIO could not open: 0`), so they are
not a new problem, but they are inputs the design has not considered by name.

---

## 4. Interpolation exactness

### What "byte for byte" can mean, and why it matters here

The compressed pixel stream and the file are not the same comparison, and on PNG they
give different answers. For a 600 x 400 source reduced to 300 x 200:

```
$ shasum -a 256 smoke_cg.png smoke_sips.png
4a2fdf23...  smoke_cg.png
0e9b4cb7...  smoke_sips.png
```

Different files. But splitting both into PNG chunks and hashing each:

```
IDAT   cg=d060cdddc4ec8369 sips=d060cdddc4ec8369 same
IEND   same
IHDR   same
cHRM   cg=ABSENT           sips=b287d3e5cf693bc1 DIFFER
eXIf   same
sRGB   same
```

**The compressed pixel data is byte-identical — not merely the pixels, the exact
deflate stream — and the whole-file difference is one 32-byte `cHRM` chunk `sips` writes
and we do not.** 110,911 minus 110,867 is 44 bytes, which is that chunk plus its header
and CRC. On a real photograph `sips` adds more: `gAMA`, `cHRM`, `pHYs`, an `iTXt` block,
and a fuller `eXIf` than ImageIO synthesises.

So `sips` and CoreGraphics never produce identical PNG *files*, on any shape, including
every shape that passes below. The results are reported on the pixel stream, which is
what §5's differential bar can actually hold. **Task 1 should read §5's "byte-identical"
as being about pixels**, and the gate should compare decoded pixels or `IDAT`, not file
hashes — otherwise every comparison fails on metadata and tells us nothing.

### The shapes

Source: `photo.png`, a 2000 x 1500 sRGB crop cut from a corpus JPEG, so the pixels are
photographic rather than a smooth synthetic gradient that could hide a difference.

```
$ /usr/bin/sips --resampleHeightWidth $h $w photo.png -s format png --out s.png
$ python3 resize.py photo.png c.png $w $h 3
$ magick compare -metric AE c.png s.png null:
```

| shape | x scale | y scale | kind | pixels | AE |
|---|---|---|---|---|---|
| 1000 x 700 | 0.50 | 0.47 | downscale, both axes | **SAME** | 0 (0) |
| 3000 x 2100 | 1.50 | 1.40 | enlargement, both axes | **SAME** | 0 (0) |
| 800 x 1200 | 0.40 | 0.80 | portrait, height-governed | **SAME** | 0 (0) |
| 1600 x 400 | 0.80 | 0.27 | letterbox, width-governed | **SAME** | 0 (0) |
| 2000 x 1000 | 1.00 | 0.67 | width held, height reduced | **SAME** | 0 (0) |
| 4000 x 1500 | 2.00 | 1.00 | height held, width doubled | **SAME** | 0 (0) |
| 7680 x 5760 | 3.84 | 3.84 | desktop-scale enlargement | **SAME** | 0 (0) |
| 2880 x 4320 | 1.44 | 2.88 | phone target, portrait | **DIFFER** | 1458 (0.000117) |

Four shapes were asked for; eight were run, and the eighth fails. It is not a shape
picked to break things — 2880 x 4320 is `PHONE_BY_WIDTH.ideal` by `PHONE_BY_HEIGHT.ideal`
straight out of `paperhanger/sizes.py`.

### Characterising the failure

It is deterministic on both sides — two runs of each produced identical bytes — and
structured rather than noisy:

```
$ python3 dig.py photo.png 2880 4320
sips vs cg, run 1: 902257 differing bytes of 37324800
  max abs channel delta: 19
  distinct rows touched: 475  (min 4, max 4270) of 4320
  distinct cols touched: 2880  (min 0, max 2879) of 2880
  first rows: [4, 13, 22, 31, 40, 49, 58, 67, 76, 85, 94, 103]
  delta histogram: [(1, 765030), (2, 97823), (3, 22625), (4, 8608), (5, 3748), ...]
sips run1 == sips run2: True
cg   run1 == cg   run2: True
```

Every ninth row, full width, mostly off by one level. A vertical resampling phase
difference, not a different filter.

It is not a case of picking the wrong interpolation constant. At the failing shape,
every CoreGraphics quality except `None` produces *the same* image, and all of them
differ from `sips` by exactly the same amount:

```
$ python3 interp_levels.py photo.png 5760 4320
  interp 0 (Default): DIFFER  bytes=1803988 maxdelta=19
  interp 1 (None   ): DIFFER  bytes=30691131 maxdelta=54
  interp 2 (Low    ): DIFFER  bytes=1803988 maxdelta=19
  interp 3 (High   ): DIFFER  bytes=1803988 maxdelta=19
  interp 4 (Medium ): DIFFER  bytes=1803988 maxdelta=19
```

There is no constant that makes it match. `sips` is doing something at these scale
factors that `CGContextDrawImage` does not expose.

### Where the boundary is

Enlargement, sweeping the vertical scale (`sweep.py`, same commands as above):

| y scale | shapes tested | verdict |
|---|---|---|
| 1.44, 1.92, 2.00, 2.40, 2.47, 2.50, 2.53, 2.60 | 2880x2160, 1000x2880, 4000x3000, 2880x3600 … 2880x3900 | SAME |
| **2.667** | 2000x4000, 2880x4000, 4000x4000, 5333x4000 | **DIFFER**, up to 22% of bytes, max delta 24 |
| **2.879, 2.88** | 2880x4319, 2880x4320, 2881x4320, 5760x4320 | **DIFFER**, ~2.4% of bytes, max delta 19 |
| 3.00, 3.84 | 6000x4500, 7680x5760 | SAME |
| **3.70** | 4000x5550, 7400x5550 | **DIFFER**, ~1.1% of bytes, max delta 2 |

The trigger tracks the **vertical** scale and is indifferent to the horizontal one —
2.667 fails at x-scales of 1.0, 1.44, 2.0 and 2.667 alike. It is not monotone: 3.0 and
3.84 pass while 2.667, 2.88 and 3.7 fail. Uniform scaling does not rescue it; 5760 x 4320
is a clean 2.88x on both axes and still differs. No further attempt was made to
reverse-engineer the rule.

Reduction, on an 8000 x 4484 corpus photograph:

| shape | scale | verdict | detail |
|---|---|---|---|
| 800 x 448 | 0.10 | **SAME** | |
| 1920 x 1080 | 0.24 | **SAME** | |
| 2880 x 1614 | 0.36 | **SAME** | |
| 4000 x 2242 | 0.50 | **SAME** | |
| 5120 x 2869 | 0.64 | **SAME** | |
| 7680 x 4304 | 0.96 | **SAME** | |
| 7760 x 4349 | 0.97 | **SAME** | |
| 7920 x 4439 | 0.99 | **SAME** | |
| 7960 x 4461 | 0.995 | **SAME** | |
| **7990 x 4478** | **0.9988** | **DIFFER** | 61,378 of 107,337,660 bytes (0.06%), max delta 7 |
| **7999 x 4483** | **0.99988** | **DIFFER** | 37,734 of 107,578,551 bytes (0.04%), max delta 9 |
| 8000 x 4484 | 1.0 | **SAME** | identity |

**Reduction is exact everywhere except a sliver immediately below identity**, between
0.995x and 1.0x, where the disagreement is real but tiny.

### What this means for the design

**The path production actually uses is exact.** `execute._refuse_to_enlarge` raises
before every resampling render unless the source is at least as large as the target on
both axes — global constraint 1, that nothing but the ML model enlarges. So
`resize_and_encode` is only ever asked to reduce or to hold, and every reduction from
0.10x to 0.995x measured exact on photographic pixels. The equivalence bar is reachable.

Three things change.

**The gate must compare pixels, not files.** Whole-file PNG equality never holds; see
above.

**The gate must not assert exactness on enlargement.** A fixture that enlarges will pass
or fail depending on where its scale factor lands, with no rule to predict it. The
brief's own acceptance criterion asks for "one enlargement", and that shape tests a path
`_refuse_to_enlarge` forbids. It is worth one pinned, documented test that records the
divergence exists — not a gate that has to stay green.

**Near-identity reductions need a decision.** A source 0.1% larger than its target is
not hypothetical: band 1 is "measures at or above the ideal", and a 7690-wide slice
resized to 7680 sits squarely in the failing sliver. Either pin it as a named exception
with these numbers, or have the differential sample deliberately include such a shape so
the exception is visible rather than discovered later.

### Grayscale sources resize exactly

```
$ python3 sweep.py photo_gray.png 1900 1000 950x500 1520x800 1000x700 800x1200
950x500    0.5000  0.5000  SAME
1520x800   0.8000  0.8000  SAME
1000x700   0.5263  0.7000  SAME
800x1200   0.4211  1.2000  SAME
```

Once the bitmap context is built correctly — which is the next section.

---

## 5. The question nobody asked: the destination colour space

The design's §3 says `_cg.py` holds every line of `ctypes`. It does not yet say how the
destination bitmap is configured, and the obvious construction is silently wrong.

Taking the colour space from `CGImageGetColorSpace` on the source and pairing it with
`kCGImageAlphaNoneSkipLast` — the combination the brief specifies — **succeeds on a
grayscale source and renders it entirely black.** No NULL, no error, no exception, a
plausible file of exactly the right size:

```
$ magick -size 150x200 gradient:black-white tiny_gray.png
$ python3 resize.py tiny_gray.png out.png 75 100 3     # source model: Monochrome
$ # column 0, every tenth row:
cg    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
sips  [0, 26, 52, 77, 103, 129, 155, 180, 206, 232]
```

At 300 Mpx the same construction produced an image where 18,712,500 of 18,750,000 bytes
were wrong, with a peak absolute error of 255 — every pixel past the first few rows
black. `magick compare` reported `PAE 65535 (1)`, the maximum possible.

Choosing the alpha info from `CGColorSpaceGetModel` instead — `kCGImageAlphaNone` for a
monochrome space, `kCGImageAlphaNoneSkipLast` for RGB — fixes it completely, and the
grayscale output is then byte-identical to `sips`:

```
cg(fixed) col0 every 10th row: [0, 26, 52, 77, 103, 129, 155, 180, 206, 232]
sips      col0 every 10th row: [0, 26, 52, 77, 103, 129, 155, 180, 206, 232]
raw bytes equal: True
```

**This is live, not latent. Ten of the 894 corpus images are grayscale:**

```
$ python3 corpus_scan.py ~/Pictures/wallpaper
colour models: {'RGB': 884, 'Gray': 10}
depths       : {8: 894}
files with a non-RGB colour model: 10
  abstract_architecture_night_scene_3140.JPG   black_and_white_landscape_2107.jpg
  black_flower_with_text_8365.jpg              black_hex_000000_rgb_0_0_0_1142.jpg
  black_hole_swirl_8566.jpg                    death_riding_horse_with_creatures_3893.png
  fallen_angel_drawing_2588.jpg                glitch_art_demon_face_6227.jpg
  katana_with_tag_2369.jpg                     surreal_staircase_whirlpool_3482.jpg
```

`normalize_to_srgb_png` would have covered for this — `sips --matchTo` converts a
grayscale source to 32 bpp RGB — but its docstring says it "runs for EVERY source on the
upscale path", and only there. Band 1 and band 2 renders of these ten files reach crop
and resize with a Monochrome `CGImage` intact.

A third model fails loudly rather than silently, which is the good case: an **Indexed**
source makes `CGBitmapContextCreate` return NULL under both alpha settings. No corpus
file reaches that state — ImageIO expands PNG and GIF palettes to RGB on decode —

```
$ python3 decode_scan.py ~/Pictures/wallpaper .png .gif .webp .heic
decoded colour space models: {'RGB/32bpp': 51, 'Monochrome/8bpp': 1}
```

— but generated fixtures do, as §2 above found the hard way, so `_cg.py` should fail with
an `ImagingError` naming the colour space rather than a NULL dereference.

**This changes the design.** §3.2 should carry a rule: the destination bitmap's alpha
info is derived from the source colour space model, monochrome and RGB are both
supported, and anything else is refused by name. §5's mutation discipline should add the
question it would have caught — would a test notice if every grayscale wallpaper came
out black? — alongside the ones about `CFRelease` and NULL.

---

## Incidental findings

**Every corpus file is 8-bit.** `depths: {8: 894}` across all 894. The design's eighth
`sips` defect, the 16-bit downconversion in the pad path, stays latent as §2 says.

**`sips -g orientation` cannot be trusted** to report an orientation tag that is present.
See §1.

**The corpus census in full**, for whatever later work wants it: 841 JPEG, 47 PNG, 3
WebP, 1 GIF, 2 HEIC; 884 RGB and 10 grayscale; all 8-bit; no orientation tag other than
identity; all 894 open through ImageIO.
