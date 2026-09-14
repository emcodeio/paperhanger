# CoreGraphics preflight measurements

The design for replacing `sips` with CoreGraphics defers four questions to measurement
(§6 of `docs/superpowers/specs/2026-09-13-coregraphics-imaging-design.md`), because any
of them could change the design. This document answers all four with numbers, and adds a
fifth that nobody asked for and that matters more than three of the four.

**Summary.**

| Question | Verdict |
|---|---|
| 1. EXIF orientation | Clears the way. `sips` and ImageIO agree on geometry; they disagree on whether the tag survives into the output. |
| 2. Peak memory at 300 Mpx | Clears the way. CoreGraphics peaks 3% higher on the largest reduction and 2.1x higher on an identity resample, which is what 577 of 2734 production calls are. The pixel cap does not move. |
| 3. HEIC input | Clears the way. Both corpus HEICs decode, dimensions agree exactly. The corpus holds two, not one. |
| 4. Interpolation exactness | Clears the path production uses, and **changes the verification plan**. Vertical reduction is exact to 0.994792 and diverges from 0.995000. Enlargement is not reliably exact at any factor. 35 of 894 sources also diverge on a distorting shape, but none of the 69 real plans that could reach that does. |
| 5. (unasked) Destination colour space | **Changes the design.** Taking the bitmap context's colour space from the source, with `kCGImageAlphaNoneSkipLast`, renders ten real corpus images entirely black, silently. |

**A warning about this document.** Three consecutive reviews found a universal in it that a
few dozen files supported and several hundred contradicted. Every count here is now taken
over all 894 corpus images, and where a claim rests on a smaller sample it says so. Treat
any sentence of the form "always", "never" or "when and only when" that does not name its
population as unverified.

Everything below was measured on macOS 26.6.2 (build 25G83), arm64, 36 GiB. `python3` is
3.14.7 at `/run/current-system/sw/bin/python3`; `ctypes.util.find_library` resolves all
three system frameworks under it. `magick` is ImageMagick 7 from Homebrew, `exiftool`
13.55.

The instruments live in `docs/research/coregraphics-preflight/`, and its `README.md` names
and hashes every source, with the command that derives each fixture, so every number here
can be re-derived. They are measuring tools rather than deliverables — nothing in
`paperhanger/` imports them, and Task 1 writes the real bindings from the spec rather than
from these.

---

## 1. EXIF orientation

### Building a fixture

`sips` cannot write the tag. That makes two tools that cannot, across three attempts:
`magick -set exif:Orientation` and `magick -orient RightTop` both failed during design,
reading back empty and `Undefined`.

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

### What the gap is, measured rather than assumed

A pair of runs cannot show what the gap depends on, so both frame sizes were run five
times against two output sizes (`rss.py`, which repeats the pair and prints the spread).
Within a configuration the figures barely move — the widest spread over five runs is
1.3 MiB — so the differences below are signal.

| source frame | output | output as a bitmap | `sips` peak | CoreGraphics peak | difference |
|---|---|---|---|---|---|
| 75 Mpx | 1875 x 2500 | 19 MB | 365,510,656 B | 383,221,760 B | **+16.9 MiB** |
| 300 Mpx | 1875 x 2500 | 19 MB | 1,266,761,728 B | 1,283,964,928 B | **+16.4 MiB** |
| 75 Mpx | 3750 x 5000 | 75 MB | 520,519,680 B | 552,239,104 B | **+30.2 MiB** |
| 300 Mpx | 3750 x 5000 | 75 MB | 1,421,672,448 B | 1,452,982,272 B | **+29.9 MiB** |
| 300 Mpx | 7680 x 4800 | 147 MB | 1,621,819,392 B | 1,671,299,072 B | **+47.2 MiB** |
| 300 Mpx | 6144 x 6144 | 151 MB | 1,630,896,128 B | 1,681,276,928 B | **+48.0 MiB** |
| 7680 x 4800 | 6144 x 6144 | 151 MB | 528,351,232 B | 604,127,232 B | **+72.3 MiB** |
| 7680 x 4800 | 7680 x 4800 | 147 MB | 171,753,472 B | 478,658,560 B | **+292.7 MiB** |
| 10000 x 4780 | 10000 x 4780 | 191 MB | 349,093,888 B | 743,604,224 B | **+376.2 MiB** |

**Read the grid; do not fit a rule to it.** An earlier version of this section said the gap
tracks the output bitmap at about a fifth of it, which is a two-point fit that holds only
where the source dwarfs the output. The last three rows break it. Two outputs of the same
151 MB cost +48.0 MiB from a 300 Mpx source and +72.3 MiB from a source barely larger than
the output. And an identity resample — same dimensions in and out — costs **+292.7 MiB**
on a 147 MB frame and **+376.2 MiB** on a 191 MB one.

What the identity rows show is that `sips` holds fewer copies of the frame than
CoreGraphics does. Counted in whole frames — one 7680 x 4800 frame at 4 bytes a pixel is
140.6 MiB, one 10000 x 4780 frame is 182.4 MiB:

| identity resample | `sips` peak | in frames | CoreGraphics peak | in frames |
|---|---|---|---|---|
| 7680 x 4800 | 163.8 MiB | **1.17** | 456.5 MiB | **3.25** |
| 10000 x 4780 | 332.9 MiB | **1.83** | 709.2 MiB | **3.89** |

So `sips` holds one to two frames and CoreGraphics three to four. An earlier version of
this section said `sips` peaks "below one decoded copy, so it is streaming" — that was
arithmetic done carelessly, and 1.17 frames is not below one. The three to four on the
CoreGraphics side is consistent with the decoded source, the bitmap context and the image
`CGBitmapContextCreateImage` returns, but this measurement does not prove which allocations
they are, and Task 1 should not lean on that reading. Where the source is much larger than
the output the extra copies are small and the gap looks like a constant; where source and
output are the same size each one is a whole frame.

**This still clears the way, and the cap does not move — but for a stated reason rather
than a formula.** At the cap the resampler's real worst cases are:

- the largest reduction: a 300 Mpx frame down to the 7680 x 4800 desktop target, where
  CoreGraphics peaks at 1593.9 MiB against 1546.7 MiB, **3.1% more**;
- the largest identity resample the planner actually asks for, 10000 x 4780, where it
  peaks at 709.2 MiB against 332.9 MiB, **2.1x** — and 577 of the 2734 production
  resampler calls are identity resamples, so this is the common case, not a corner.

Both are far below what the machine has, and neither is near the ceiling the cap exists to
defend: the cap bounds the 4x frame at 300 Mpx, and the worst measured CoreGraphics peak
anywhere in this grid is 1.6 GiB. The cap keeps its value. But **Task 1 should not assume
the new path's memory is within tens of megabytes of the old one** — on the identity
resample it is double, and if a future change raises the cap or the target sizes, the
identity case is the one to measure first.

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

The compressed pixel stream and the file are not the same comparison, and on PNG they can
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

**The compressed pixel data is byte-identical — not merely the pixels, the exact deflate
stream — and the whole-file difference is one 32-byte `cHRM` chunk `sips` writes and we do
not.** 110,911 minus 110,867 is 44 bytes, which is that chunk plus its header and CRC.

**Whole-file identity is reachable, and what decides it is the source, not the shape.**
Everything below is counted over all 894 corpus images unless it says otherwise; read the
warning at the end of this subsection before trusting any sentence here that does not carry
a count.

**What ImageIO writes follows the source's colour space, and nothing else.** With a NULL
destination-properties dictionary it emits exactly one colour chunk plus a synthesised
`eXIf`, and which colour chunk depends on the space. Over the whole corpus there are two
outcomes and no third: **833 sources produce `sRGB` + `eXIf` and 61 produce `iCCP` + `eXIf`,
894 of 894.** The shape of it:

| source colour space | ImageIO writes | `sips` writes | whole file |
|---|---|---|---|
| sRGB, carried as an `iCCP` profile | `sRGB(1) eXIf(68)` | `sRGB(1) eXIf(68)` | **identical SHA-256** |
| Adobe RGB (1998), as an `iCCP` profile | `iCCP(281) eXIf(56)` | `iCCP(281) eXIf(56)` | **identical SHA-256** |
| untagged | `sRGB(1) eXIf(68)` | `sRGB(1) eXIf(68)` | **identical SHA-256** |

Measured with `chunkmap.py` on PNGs reduced to `IHDR`, `iCCP`, `IDAT`, `IEND` and nothing
else. So it is not "always `sRGB`": an sRGB profile is collapsed to the one-byte `sRGB`
chunk, a non-sRGB profile is carried through as `iCCP`, and even the synthesised `eXIf`
changes size with it. **ImageIO preserves the colour space and discards everything else.**

**CoreGraphics is not always the sparser of the two.** For **14 sources, all PNG**,
CoreGraphics writes an `sRGB` chunk that `sips` does not — `abstract_art_hand_face_black_1404.png`,
`blade_runner_2049_poster_7000.png`, `brush_circle_8107.png` and eleven more. A round-two
draft said the difference was "always by chunks only `sips` wrote"; it is not, and a
differential that assumes the new path is a subset of the old will mis-report these.

**What `sips` adds, it synthesises — it is not forwarding chunks the source carried.** A
JPEG has no PNG chunks to forward, and `sips` writes them anyway. Counted over all 894
sources at 800 x 600 (`sourcecensus.py`), the chunks only `sips` wrote:

```
pHYs                 341        cHRM pHYs               6
iTXt pHYs            337        cHRM gAMA               2
iTXt                  18        cHRM gAMA pHYs          1
cHRM gAMA iTXt pHYs   12        cHRM                    1
```

**What triggers `pHYs` and `iTXt` was not isolated, and two attempts to state it were
wrong.** Round two of this document claimed `iTXt` appears "when and only when the source
carries Exif or XMP", and `pHYs` for every source declaring a real density with JFIF units
0 the exception. Counted at full size rather than over 46 files, both fail:

| claimed rule | counterexamples over 894 |
|---|---|
| `iTXt` iff Exif or XMP | **41** — 3 sources carry neither and get one anyway (all three carry a Photoshop APP13 `8BIM` block), and 38 carry Exif or XMP and get none |
| no `pHYs` for JFIF units 0 | **151** of 276 units-0 sources do write it, 9 of them carrying the very `(0, 1, 1)` triple the rule was built on |
| no `pHYs` without JFIF | **93** sources with no JFIF segment at all get one |

Density is not the discriminator either way:

```
density (0, 1, 1)      pHYs=False  118 files      density (1, 72, 72)   pHYs=True  348 files
density (0, 1, 1)      pHYs=True     9 files      density (2, 28, 28)   pHYs=True   11 files
density (0, 72, 72)    pHYs=True   141 files      density None          pHYs=True   93 files
density (0, 72, 72)    pHYs=False    3 files      density None          pHYs=False  54 files
```

The honest statement is the count, not a rule: **`sips` wrote `pHYs` for 697 of 894 sources
and `iTXt` for 367**, both correlated with the source carrying metadata and neither
predicted by any single field tested here. The mechanism is still synthesis rather than
forwarding — a JPEG has no PNG chunks to forward — but which metadata `sips` consults, and
in what precedence, is not settled by this measurement.

**JFIF density does not predict identity**, and a round-one draft was wrong that every
corpus JPEG carries it: 694 of 841 do.

**This is the third round in which a universal about 894 files rested on a few dozen.**
Round one: "never produce identical PNG files, on any shape." Round two: "every corpus JPEG
carries JFIF density." Round three: the two rules above. Each was true of the sample and
false of the corpus. **No claim in this document of the form "always", "never" or "when and
only when" should be trusted unless it names the population it was counted over.** Where a
count is present — 833 plus 61 for the colour chunk, 894 of 894 for depth, 0 of 894 for
orientation — the claim is a count. Where it is absent, it is a guess.

The chain also does not converge. Handed a PNG carrying the `sRGB` chunk — which is exactly
what ImageIO writes — `sips` re-expresses it as `gAMA` plus `cHRM` and adds an `iTXt`, so a
CoreGraphics-written intermediate does not make the next stage agree.

**The conditional statement, with its count.** Whole files usually match when the source
carries nothing `sips` can synthesise from — no Exif, no XMP, no density, no Photoshop
block, and for a PNG no ancillary chunks at all. Over the corpus that describes **29
sources, of which 28 produce byte-identical files.** One does not, so this is a strong
tendency rather than a law, and the exception is why the tier-2 gate should not rest on it.

Where it does hold without exception so far is on generated fixtures:
`tests/pixels.write_png` emits `IHDR`, `IDAT`, `IEND` and nothing else. Confirmed across
four shapes including an enlargement:

```
$ python3 fileid.py fixture.png 1000x700 1600x1200 900x500 333x2000
  1000x700    sips 6d6f2147...  cg 6d6f2147...  IDENTICAL
  1600x1200   sips 0d4f29d5...  cg 0d4f29d5...  IDENTICAL
  900x500     sips dcdc2fd1...  cg dcdc2fd1...  IDENTICAL
  333x2000    sips 9a8c7c63...  cg 9a8c7c63...  IDENTICAL
```

They differ for most real corpus input: 369 of 841 corpus JPEGs carry Exif, 191 carry XMP,
694 declare a density. A tier-1 gate on generated fixtures may compare file hashes. A
tier-2 gate on corpus images must compare decoded pixels or the `IDAT` stream, or it will
fail on metadata and tell us nothing. The results below are reported on the pixel stream so
that both tiers read the same way.

### The shapes

Source: `photo.png`, SHA-256 `8d34cd43…`, a 2000 x 1500 sRGB crop cut from
`abstract_colorful_clouds_7117.jpg` (`36a717dc…`), so the pixels are photographic rather
than a smooth synthetic gradient that could hide a difference. Every source in this
section is named, hashed and given its derivation command in
`coregraphics-preflight/README.md`.

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
is a clean 2.88x on both axes and still differs.

**That table is one source's, and it does not generalise.** A 2000 x 1400 fixture diverges
at y = 1.3664 and y = 1.3571 — below every failing factor in the table above — while
passing at 1.50, and the 2000 x 1500 source passes at 1.40 and 1.50:

```
$ python3 sweep.py fixture.png 2000 1400 2880x1913 1000x1913 2880x1900 2880x2100
2880x1913    1.4400  1.3664  DIFFER         3855      2
1000x1913    0.5000  1.3664  DIFFER         1488      1
2880x1900    1.4400  1.3571  DIFFER        58778      2
2880x2100    1.4400  1.5000  SAME              0      0
```

So the divergent set depends on the source height as well as the scale, and the
vertical-only rule holds here too (the same y at x = 0.5 and x = 1.44 both fail). No
enlargement factor can be called safe on the strength of one source passing at it. No
further attempt was made to reverse-engineer the rule; production never enlarges through
this path.

### Reduction: where the boundary actually is

The first sweep varied both axes at once, which hid the boundary and put it in the wrong
place. Holding the output width fixed and walking the output height one pixel at a time
(`boundary.py`) puts it exactly, on a 7680 x 4800 source converted from a corpus HEIC:

```
$ python3 boundary.py nebula.png 7680 4800 3840 4770 4782
out height   y scale      as a ratio verdict       bytes   maxd
4770         0.993750     159/160    SAME              0      0
4771         0.993958     4771/4800  SAME              0      0
4772         0.994167     1193/1200  SAME              0      0
4773         0.994375     1591/1600  SAME              0      0
4774         0.994583     2387/2400  SAME              0      0
4775         0.994792     191/192    SAME              0      0
4776         0.995000     199/200    DIFFER        51321      1
4777         0.995208     4777/4800  DIFFER         4581      4
4778         0.995417     2389/2400  DIFFER         9646      9
4779         0.995625     1593/1600  DIFFER        12205      3
4780         0.995833     239/240    DIFFER        45288      1
4781         0.996042     4781/4800  DIFFER         4901      6
4782         0.996250     797/800    DIFFER        20321      4
```

**Exact to y = 0.994792. Divergent from y = 0.995000.** The same walk on an independent
synthetic source of the same height puts the boundary in the identical place:

```
$ python3 boundary.py fixture4800.png 3000 4800 1500 4773 4778
4775         0.994792     191/192    SAME              0      0
4776         0.995000     199/200    DIFFER        51747      1
4777         0.995208     4777/4800  DIFFER        10443     14
4778         0.995417     2389/2400  DIFFER        29818     27
```

**The sliver is vertical-only**, the same rule the enlargement region follows. Holding the
divergent height and sweeping the width changes nothing; putting the width deep inside the
sliver while the height stays below the boundary changes nothing either:

| shape | x scale | y scale | verdict |
|---|---|---|---|
| 1920 x 4776 | 0.2500 | 0.9950 | **DIFFER** |
| 3840 x 4776 | 0.5000 | 0.9950 | **DIFFER** |
| 7000 x 4776 | 0.9115 | 0.9950 | **DIFFER** |
| 7648 x 4776 | 0.9958 | 0.9950 | **DIFFER** |
| 7680 x 4776 | 1.0000 | 0.9950 | **DIFFER** |
| 7648 x 4000 | 0.9958 | 0.8333 | **SAME** |
| 7648 x 2400 | 0.9958 | 0.5000 | **SAME** |
| 7679 x 3600 | 0.9999 | 0.7500 | **SAME** |
| 3840 x 4800 | 0.5000 | 1.0000 | **SAME** |
| 7000 x 4800 | 0.9115 | 1.0000 | **SAME** |
| 7679 x 4800 | 0.9999 | 1.0000 | **SAME** |

Reporting one horizontal scale per row is what hid this. The earlier reduction sweep read
"7960 x 4461, 0.995, SAME" — that row's vertical scale is 4461/4484 = 0.994871, below the
boundary, and the two shapes it called divergent, 7990 x 4478 and 7999 x 4483, have
vertical scales of 0.998662 and 0.999777. Nothing in that sweep was wrong; it was too
coarse to see where the edge sat.

The last three rows matter for a different reason: a vertical scale of exactly 1.0 is
exact at every horizontal scale. 577 real plans resample with the height unchanged, and
they are all safe.

**So: vertical reduction is exact from 0.10x through 0.994792 and at identity, and
diverges between 0.995000 and 1.0.** The disagreement there is small — under 0.15% of
bytes in every shape measured, with a maximum channel delta of 27 — but it is real and
deterministic, not noise.

### Anisotropy, and the divergence that needs one

Some corpus sources resize to different pixels through the two tools. Counted over all 894
at 800 x 600 — a shape that distorts the aspect of nearly every corpus image — **25 differ
in decoded pixels**, the worst by 250,459 bytes at a maximum channel delta of 38
(`rocky_mountain_range_1569.jpg`). A round-two draft said pixels were identical in all 46
files it looked at; over the corpus they are not.

**35 differ in the compressed `IDAT` stream, and 25 in the pixels those streams decode to.**
The ten-file gap is not a pixel difference: it is the two tools choosing different PNG
colour types for the same image, so the streams differ while the picture does not. A gate
comparing `IDAT` will flag ten files a gate comparing decoded pixels will not, and neither
is wrong — they answer different questions. Pick one deliberately.

Splitting the 25 by cause, by re-running each through a lossless PNG intermediate
(`divergence_audit.py`):

- **22 are a JPEG decode difference.** Through the intermediate they agree exactly, so the
  disagreement came from reading the container, not from resampling.
- **3 are a resampler difference**, all PNG sources, all tiny — 12 bytes each at 800 x 600.

And the decode difference needs a distorting shape. At an aspect-preserving half-size
resize, **all 22 agree byte for byte**; only the 3 PNG sources still differ, by 12 to 36
bytes.

The obvious reassurance is that `paperhanger` crops to the target aspect before resizing,
so it never asks for a distorting shape. **That reassurance is false**, and it is worth
saying why before saying what rescues it.

`geometry.py` rounds the slice dimension **up**:

```python
slice_height = _ceil_div(width * 10, 16)      # horizontal thirds, 16:10
slice_width  = _ceil_div(height * 2, 3)       # vertical thirds, 2:3
```

A 16:10 slice is exactly 16:10 only when `width * 10` divides by 16; otherwise the rect is
a fraction of a pixel short and the resize that follows is very slightly anisotropic. The
docstring says as much — rounding up is deliberate, so the slice satisfies its own
classification — but the consequence for scale factors was never counted. `aspect_audit.py`
counts it, over every image and every plan the real planner produces:

```
photos                            : 894
crop rects planned                : 2562
crop rects whose aspect != target : 1086
resamples                         : 2734
resamples with x scale != y scale : 955
  ... of those, reading the ORIGINAL file : 69
  ... of those, reading an original JPEG  : 69
```

**1086 of 2562 crop rects are not the target's aspect, and 955 of 2734 resamples are
anisotropic.** The largest crop departure is `1284/803` against `8/5` — 1.599004 against
1.600000 — and the largest resample anisotropy is a horizontal scale of 0.93847656 against
a vertical 0.93831451, a difference of 1.6e-4. Small, but not zero, and "not zero" is the
condition.

So the answer to whether production ever asks for a distorting shape is **yes, 955 times**.

What rescues it is the second condition rather than the first. The divergence needs the
resampler to read the original container, and 886 of those 955 read a lossless PNG written
by the crop or by the upscaler. Only **69 read the original file**, all of them JPEG. Those
69 are the entire population at risk, and they can simply be run:

```
$ python3 at_risk.py ~/Pictures/wallpaper
plans that are anisotropic AND read an original JPEG: 69
...
0 of 69 diverge
```

**None of them diverges.** Every one of the 69 was run at its exact planned dimensions
against its real file, and all 69 matched byte for byte. The production anisotropies are
four orders of magnitude smaller than the ones that provoke the divergence at 800 x 600,
and they do not reach it.

**So Task 4's gate can be green on the corpus, and no image needs pinning for this.** But
the margin is the size of the anisotropy, not a structural guarantee, and two edits would
narrow it: changing `_ceil_div` to round differently, or changing `bands.output_size`, which
derives the second axis with `round(height * target.ideal / width)`. A test that asserts
these 69 shapes still agree is worth more than a comment saying they do.

### What this means for the design

**The path production actually uses is exact.** `execute._refuse_to_enlarge` raises before
every resampling render unless the source is at least as large as the target on both axes
— global constraint 1, that nothing but the ML model enlarges. So `resize_and_encode` is
only ever asked to reduce or to hold.

**Production does not reach the divergent sliver either.** That is measured rather than
argued: `plan_scales.py` runs the real planner, `plan.plan_photo`, over the measured
dimensions of all 894 corpus images and reconstructs the input to each resize the way
`execute.render` does, following the crop and the 4x upscale where they apply.

```
$ python3 docs/research/coregraphics-preflight/plan_scales.py ~/Pictures/wallpaper
photos planned        : 894
output plans          : 3441
plans that resample   : 2734
vertical scale  < 1   : 2157
vertical scale == 1   : 577
vertical scale  > 1   : 0
horizontal scale > 1  : 0

closest vertical reductions to identity:
  0.984627   7800x5204 -> 7680x5124   rock_arch_sunset_8323.jpg
  0.984615   7800x5200 -> 7680x5120   red_sand_dunes_waterfall_5253.jpg
  0.984615   7800x5200 -> 7680x5120   waterfall_reflection_forest_scene_1431.jpg
  0.984051   7804x5204 -> 7680x5121   misty_lake_scene_8929.jpg
  0.980467   7833x5222 -> 7680x5120   abstract_black_and_white_swirls_...jpg

boundary (largest exact vertical reduction measured): 0.994792
plans landing at or above the boundary: 0
```

No plan enlarges, on either axis — the guard is doing what it says. The closest any real
plan comes to identity is **0.984627**, a full percentage point below the 0.994792
boundary, and it resamples exactly:

```
$ python3 sweep.py frame4x.png 7800 5204 7680x5124
7680x5124    0.9846  0.9846  SAME              0      0
```

So the sliver is a **latent boundary, not a live risk**. It is worth a pinned test to
catch drift — a target table edited, or an upscale factor changed, could walk a plan into
it — but nothing in the corpus reaches it today, and the equivalence bar is reachable as
written.

Three things change.

**How the gate compares depends on the tier.** Tier 1, on `tests/pixels.write_png`
fixtures, may compare whole files: those sources carry nothing `sips` can synthesise from
and the two tools produce identical SHA-256. Tier 2, on corpus images, must compare decoded
pixels or the `IDAT` stream, because `sips` synthesises chunks ImageIO does not — and
because for 14 PNG sources CoreGraphics writes a chunk `sips` does not, so the difference
is not one-sided. See the top of this section.

**The tier-2 sample should include an anisotropic plan reading an original JPEG.** All 69
of them agree today, but they agree by a margin of about 1.6e-4 in the scale factors rather
than by construction, and both `geometry._ceil_div` and `bands.output_size` could widen
that with a one-line edit.

**The gate must not assert exactness on enlargement.** A fixture that enlarges will pass
or fail depending on where its vertical scale lands, and the safe factors differ from one
source height to the next, so no fixture can be called safe by picking a factor that
worked elsewhere. The brief's own acceptance criterion asks for "one enlargement", and
that shape tests a path `_refuse_to_enlarge` forbids. It is worth one pinned, documented
test that records the divergence exists — not a gate that has to stay green.

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

The design's §3 says `_cg.py` holds every line of `ctypes`. It does not say how the
destination bitmap is configured, and the construction the plan writes out is silently
wrong.

The site is **`_resize_to_png` in Task 4 of the implementation plan** — cited by function
name, because this document's own commits have already shifted these line numbers twice —
which
takes the colour space from the source and pairs it with `kCGImageAlphaNoneSkipLast`:

```python
colorspace = _cg.CGImageGetColorSpace(image)     # a Get: not ours to release
ctx = scope.own(_checked(
    _cg.CGBitmapContextCreate(None, out_width, out_height, 8, 0,
                              colorspace, kCGImageAlphaNoneSkipLast),
```

**Scope the fix to that call.** Task 6's `normalize_to_srgb_png` also
passes `kCGImageAlphaNoneSkipLast`, and is correct as written: it builds its context in a
colour space it creates itself with `CGColorSpaceCreateWithName("kCGColorSpaceSRGB")`,
which is RGB by construction and can never be handed a monochrome source space. Sweeping
it into the fix would be a change with no defect behind it.

That construction **succeeds on a grayscale source and renders it entirely black.** No
NULL, no error, no exception, a plausible file of exactly the right size:

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

### Why the planned gate would not have caught it

The differential in Task 4 is `test_resize_matches_sips_exactly`, parametrised over four
shapes:

```python
SHAPES = [
    (2000, 1400, 1000, 700),
    (800,  600,  1600, 1200),
    (2000, 1400, 900,  500),
    (1400, 2000, 500,  900),
]
```

Its sources come from a `photo_fixture` the plan does not define — no `photo_fixture`,
`png_fixture` or `gradient_fixture` exists in `tests/conftest.py` today, so whoever writes
Task 4 will build them on `tests/pixels.write_png`, which is the suite's only PNG writer.
That writer emits `IHDR` with colour type 2:

```python
+ _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
```

**8-bit RGB, always.** There is no way to ask it for a grayscale image. So every source the
gate ever compares decodes to an RGB colour space, `kCGImageAlphaNoneSkipLast` is the right
pairing for all of them, and the four shapes pass byte for byte while the ten grayscale
wallpapers come out black.

The four scale factors miss the divergent regions too — 0.5, 2.0, 0.45 and 0.357 are all
clear of both the reduction sliver and the enlargement factors measured above — so the
gate is green on every axis it looks at. That is precisely why this has to be fixed in
`_cg.py` rather than caught by the differential: **a gate whose fixtures cannot express
the failing input cannot fail.**

### What changes

**§3.2 should carry a rule**: the destination bitmap's alpha info is derived from the
source colour space model, monochrome and RGB are both supported, and anything else is
refused by name with an `ImagingError` rather than a NULL dereference.

**Task 4 needs a grayscale fixture.** Either `tests/pixels` grows a colour-type-0 writer,
or the tier-2 differential includes one of the ten named files above. Without one, the
same defect can return under a later edit and the suite will stay green.

**§5's mutation discipline should add the question it would have caught** — would a test
notice if every grayscale wallpaper came out black? — alongside the ones about `CFRelease`
and NULL.

---

## Incidental findings

**Every corpus file is 8-bit.** `depths: {8: 894}` across all 894. The design's eighth
`sips` defect, the 16-bit downconversion in the pad path, stays latent as §2 says.

**`sips -g orientation` cannot be trusted** to report an orientation tag that is present.
See §1.

**The corpus census in full**, for whatever later work wants it: 841 JPEG, 47 PNG, 3
WebP, 1 GIF, 2 HEIC; 884 RGB and 10 grayscale; all 8-bit; no orientation tag other than
identity; all 894 open through ImageIO.

**The planner never enlarges and never resamples a photo more than once.** 894 photos
produce 3441 output plans, of which 2734 resample: 2157 reduce vertically, 577 hold the
height exactly, and none enlarges on either axis. Numbers from `plan_scales.py`, which is
also the cheapest way to re-check any claim of the form "production never asks for X".

**Three fixtures the plan names do not exist.** `photo_fixture`, `png_fixture` and
`gradient_fixture` appear throughout Tasks 1 through 4; `tests/conftest.py` defines none
of them. The suite's only image writer is `tests/pixels.write_png`, which is RGB-only and
deterministic.
