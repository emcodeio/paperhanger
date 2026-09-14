# Preflight instruments

The measuring tools behind
`docs/research/2026-09-13-coregraphics-preflight-measurements.md`. They are not part of
the shipped tool and nothing in `paperhanger/` imports them. They exist so the numbers in
that document can be re-derived rather than taken on trust — Task 1 writes the real
bindings from the spec, not from these.

Run them with the project's own interpreter (`python3`, 3.14) from anywhere;
`plan_scales.py` resolves the repository from its own location.

| script | what it measures |
|---|---|
| `cgbase.py` | shared `ctypes` declarations for CoreFoundation, CoreGraphics and ImageIO |
| `probe.py` | dimensions, orientation, depth and colour space of a file through ImageIO |
| `resize.py` | one resize through `CGBitmapContextCreate` / `CGContextDrawImage` |
| `sweep.py` | pixel equality against `sips` across a list of shapes |
| `boundary.py` | walks the output height to locate a divergence boundary by vertical scale |
| `fileid.py` | whole-file SHA-256 identity, and which ancillary PNG chunks each tool writes |
| `interp_test.py` | the eight-shape table in section 4 |
| `interp_levels.py` | every interpolation constant against `sips` at one shape |
| `dig.py` | where differing pixels sit, and whether both tools are deterministic |
| `corpus_scan.py` | colour model, orientation, depth and UTI census, from properties only |
| `decode_scan.py` | colour space model after a real decode, for the palette question |
| `rss.py` | peak RSS of both paths, repeated, so the spread is visible |
| `chunkmap.py` | what each tool writes into a PNG, against what the source carried |
| `sourcecensus.py` | corpus JPEG metadata census, and what predicts whole-file identity |
| `aspect_audit.py` | whether any real crop rect or resample is anisotropic, over every plan |
| `at_risk.py` | runs the plans that could hit the decode divergence, at their real sizes |
| `divergence_audit.py` | every source whose pixels differ, split into decode against resampler |
| `plan_scales.py` | every resampling scale factor the real planner asks for over the corpus |

All of them read the corpus and never write to it, and every one that produces an
intermediate writes it to a temporary directory removed at exit, never beside itself:
the repository is public and several of these fixtures are cut from wallpapers.
`plan_scales.py` imports
`paperhanger.plan` and `paperhanger.sizes` and writes nothing at all.

## Sources

Nothing here ships an image. The corpus originals are named and hashed so a rerun can
confirm it is measuring the same pixels; the derived fixtures are given with the command
that produces them.

### Corpus originals (SHA-256)

```
547bdfbed82fc2d50f68b9d01cfba7a85e7a6398240f22bfc92b76b1b7a89136  abstract_black_and_white_art_6137.png
36a717dc945a154da832de089cd5db4966ddf5b799d3fe0e524d8a866de895e4  abstract_colorful_clouds_7117.jpg
83a053a06789a96890ea14f7e32769ffcafb5ef8cf60e78942161adb4cb88e96  abstract_colorful_swirl_4495.jpg
2382e4df122d5e3d4f0e7ef496f8fa0965886ea4ea8362c220d06540556643d8  black_and_white_landscape_2107.jpg
06af9f3c63db3ee5a69ed918fc7eda0feadec2e41f2520f1c29d285c7d14df5f  dark_stones_7236.png
b64a96704521c9341528bb73c5aad97c980c670a7ce82a9f133ed95e13bc494a  death_riding_horse_with_creatures_3893.png
72d2532a22e5a055f18f16326ac25a6112a19c60f9957ec01ad47090f6f27631  katana_with_tag_2369.jpg
6f74be10ce3ba62c1dc01ae0aafc8216d9f05b403e70a11d47bafef8a44edf6c  koi_circle_2135_x.heic
4e1c9e147a28f2fc8d22e77198d81bb2ffdfca10713ed82aa7824180fc7e4ea8  purple_nebula_glow_0312_x.heic
c23d3ed2c9c832adf9a33214420cf663cfe8445f4ff94337cb9865368a76be8d  red_tulips_with_mountain_background_4338.jpg
```

### Derived fixtures

Each line is the command, then the SHA-256 of what it produces. `$W` is
`~/Pictures/wallpaper`, which is read-only — every fixture is built in a scratch
directory from a copy.

**Every `magick` derivation here passes `-strip`, and that is not cosmetic.** Without it
`magick` stamps a `tIME` chunk and three `date:*` `tEXt` chunks carrying the current
clock, so the file hashes differently on every run and none of these lines could be
checked. An earlier version of this README omitted it and six of nine hashes were
unreproducible.

`-strip` also removes an ICC profile where the source has one, which changes what both
tools write into the output — see section 4 of the findings. It is not what happens to
`photo.png`: the `-colorspace sRGB` conversion ahead of it leaves no `iCCP` chunk to
remove, so for that fixture `-strip` takes only the timestamps. Either way it touches no
pixels, and every pixel-level number in section 4 was re-derived on these stripped
fixtures and reproduced exactly.

```
magick $W/abstract_colorful_clouds_7117.jpg -crop 2000x1500+100+100 +repage \
       -colorspace sRGB -depth 8 -type TrueColor -strip photo.png
8d34cd43a16166ddf22b2805ff1f395a682b010c5bee51736fb2eb7c7bd38c02  photo.png     2000x1500 sRGB

magick $W/katana_with_tag_2369.jpg -crop 1900x1000+10+40 +repage -depth 8 -strip photo_gray.png
458faece208f7b398b5f6fb036adf62fcb5456508a7bb83f9f3a0ec21b004633  photo_gray.png  1900x1000 Monochrome

magick $W/abstract_colorful_swirl_4495.jpg -colorspace sRGB -depth 8 -type TrueColor -strip big.png
f163ec78ddba00425519cac4e269cda05e0c808e07a85834653ad80062f85c68  big.png       8000x4484 sRGB

magick $W/purple_nebula_glow_0312_x.heic -colorspace sRGB -depth 8 \
       -define png:color-type=2 -strip nebula.png
c8c322e8cd8e52964ce825944db2a938217d527600496fd5decf81d287c7ce08  nebula.png    7680x4800 sRGB

magick -size 15000x20000 gradient:red-blue -depth 8 -define png:color-type=2 -strip huge_rgb.png
7f8b26e4038f5af75af187d67e28085a20d4d01a1d22239a27fb2490f5eba454  huge_rgb.png  300 Mpx truecolor

magick -size 7500x10000 gradient:red-blue -depth 8 -define png:color-type=2 -strip huge75.png
86d779ed71102049233d80ff0b17ed7f8c54a629371bea357c685af9cb35bfd8  huge75.png    75 Mpx truecolor

magick -size 15000x20000 gradient:black-white -strip huge.png
93280080ed9847ec1266c2cd49e97d43dbc53ab287f1f713567c5d79b512793f  huge.png      300 Mpx 16-bit grayscale

magick -size 1200x600 gradient:red-blue rot.jpg && cp rot.jpg rot6.jpg
exiftool -overwrite_original -Orientation=6 -n rot6.jpg
313402d87dc3fe292b6fb39260602c99f396b5496e385fa0b177a33af2acd249  rot6.jpg      1200x600, EXIF orientation 6
```

JPEG output needs no `-strip`: `magick` writes no timestamp into it, and `rot6.jpg`
reproduces as written.

The synthetic fixtures come from the project's own test writer, `tests/pixels.write_png`,
which is deterministic — a fixed seed and a fixed mix, so the bytes are reproducible:

```
from tests.pixels import write_png; write_png(path, w, h, noise=True)
f5256f6bceaf6024282697b4075a0b3468695a86a98e24b78d6d423d6713392e  fixture.png           2000x1400
abb50d86f7a074dc788b551167e35d8b533b8a2bc79859f35934fd27ceff0381  fixture4800.png       3000x4800
ea8e2d939bbabf5fc2d2074188a7ce9f3ab071648145f838b631f0835630ae51  frame4x.png           7800x5204
6401ce64f0cbe8865902a6c40c56bf68ab139cc146c1ebec43cebba08ad38641  biggest_identity.png  10000x4780
```
