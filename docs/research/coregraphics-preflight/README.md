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
| `plan_scales.py` | every resampling scale factor the real planner asks for over the corpus |

All of them read the corpus and never write to it. `plan_scales.py` imports
`paperhanger.plan` and `paperhanger.sizes` and writes nothing at all.

## Sources

Nothing here ships an image. The corpus originals are named and hashed so a rerun can
confirm it is measuring the same pixels; the derived fixtures are given with the command
that produces them.

### Corpus originals (SHA-256)

```
36a717dc945a154da832de089cd5db4966ddf5b799d3fe0e524d8a866de895e4  abstract_colorful_clouds_7117.jpg
83a053a06789a96890ea14f7e32769ffcafb5ef8cf60e78942161adb4cb88e96  abstract_colorful_swirl_4495.jpg
2382e4df122d5e3d4f0e7ef496f8fa0965886ea4ea8362c220d06540556643d8  black_and_white_landscape_2107.jpg
b64a96704521c9341528bb73c5aad97c980c670a7ce82a9f133ed95e13bc494a  death_riding_horse_with_creatures_3893.png
72d2532a22e5a055f18f16326ac25a6112a19c60f9957ec01ad47090f6f27631  katana_with_tag_2369.jpg
6f74be10ce3ba62c1dc01ae0aafc8216d9f05b403e70a11d47bafef8a44edf6c  koi_circle_2135_x.heic
4e1c9e147a28f2fc8d22e77198d81bb2ffdfca10713ed82aa7824180fc7e4ea8  purple_nebula_glow_0312_x.heic
```

### Derived fixtures

Each line is the command, then the SHA-256 of what it produces. `$W` is
`~/Pictures/wallpaper`, which is read-only — every fixture is built in a scratch
directory from a copy.

```
magick $W/abstract_colorful_clouds_7117.jpg -crop 2000x1500+100+100 +repage \
       -colorspace sRGB -depth 8 -type TrueColor photo.png
45c17afb1c6259725fdfcdd5025a0abdf65c76be673630b26f0f0e82de5d61b7  photo.png     2000x1500 sRGB

magick $W/katana_with_tag_2369.jpg -crop 1900x1000+10+40 +repage -depth 8 photo_gray.png
50462dfd7034e3ac3fbd6582ac5698491a0ad6e4fcb2c474b411279b24d4357d  photo_gray.png  1900x1000 Monochrome

magick $W/abstract_colorful_swirl_4495.jpg -colorspace sRGB -depth 8 -type TrueColor big.png
430605614075d3a84337b9f476815b705fd14b08d3cfb281a025cd985222e87f  big.png       8000x4484 sRGB

magick $W/purple_nebula_glow_0312_x.heic -colorspace sRGB -depth 8 \
       -define png:color-type=2 -strip nebula.png
c8c322e8cd8e52964ce825944db2a938217d527600496fd5decf81d287c7ce08  nebula.png    7680x4800 sRGB

magick -size 15000x20000 gradient:red-blue -depth 8 -define png:color-type=2 huge_rgb.png
ca9445097861cf344f83cd4bfff69c5f72c00c07706c1345ec633c1eceda012d  huge_rgb.png  300 Mpx truecolor

magick -size 7500x10000 gradient:red-blue -depth 8 -define png:color-type=2 huge75.png
47d8ab855ce39ac897c7bc488f7707044f69d1747e2ef7fe13cc7dbafcc064ae  huge75.png    75 Mpx truecolor

magick -size 15000x20000 gradient:black-white huge.png
e7d8793dfe7d0a19f7c95c66a8af537f29e8f342f73bae67b870d925aab17d9b  huge.png      300 Mpx 16-bit grayscale

magick -size 1200x600 gradient:red-blue rot.jpg && cp rot.jpg rot6.jpg
exiftool -overwrite_original -Orientation=6 -n rot6.jpg
313402d87dc3fe292b6fb39260602c99f396b5496e385fa0b177a33af2acd249  rot6.jpg      1200x600, EXIF orientation 6
```

The synthetic fixtures come from the project's own test writer, `tests/pixels.write_png`,
which is deterministic — a fixed seed and a fixed mix, so the bytes are reproducible:

```
from tests.pixels import write_png; write_png(path, w, h, noise=True)
f5256f6bceaf6024282697b4075a0b3468695a86a98e24b78d6d423d6713392e  fixture.png      2000x1400
abb50d86f7a074dc788b551167e35d8b533b8a2bc79859f35934fd27ceff0381  fixture4800.png  3000x4800
ea8e2d939bbabf5fc2d2074188a7ce9f3ab071648145f838b631f0835630ae51  frame4x.png      7800x5204
```
