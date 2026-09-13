# The whole-frame equivalence gate

Spec section 7 upscales each photo **once**, whole, and cuts its slices out of the 4x
frame — rather than upscaling each slice separately. That is worth about thirteen hours
on a full-corpus run: roughly 24 hours instead of 37.

It rested on a claim that had never been measured: that enlarging a whole frame and then
cutting equals cutting first and then enlarging, "because the model carries no
whole-image context." This document is the measurement, and the record of two earlier
attempts that looked conclusive and were not. The numbers cited in spec sections 7 and 13
come from here.

**The finding: the two methods are interchangeable.** Whole-frame is very slightly and
very consistently closer to ground truth, by a margin nobody can see. The original
premise was also wrong — the model carries no *global* context but plenty of local
context, and a crop boundary is exactly where local context stops.

---

## 1. Why the obvious measurement cannot answer the question

The first instinct is to upscale a photo both ways and compare the two results to each
other. That number cannot settle anything, because **neither result is a correct
answer**. Both are guesses from the same model. Scoring one against the other measures
how far the model moves when its surroundings change — it has no opinion about which
output is better.

Worse, it can point the wrong way. A low score means the two disagree; a rule that
reverted the design on a low score would fire hardest exactly where the two methods
differ most, which is not the same as where either is wrong.

So both methods are scored against **original photograph pixels** instead.

## 2. Protocol

```
G   a ground-truth window, native pixels from a real corpus photograph
S   G downscaled 4x by sips -- the model's input
A   arm A, whole-then-cut:   upscale S whole, cut the slice from the 4x frame
B   arm B, cut-then-upscale: cut the slice from S, upscale that
T   the truth slice: the same slice cut from G at full resolution
```

`G` is exactly `4 x S` on both axes, so the factor of four is construction, not
coincidence. **One rect throughout:** `rect` cuts B's slice from `S`; `rect.scaled(4)`
cuts both A's slice from the 4x frame *and* T's slice from `G`. Every cut goes through
`imaging.crop` — never a bare `sips` crop, which silently returns the centred region for
a zero offset (see the `sips` defects in the research document that predates this one).

Measured with PSNR and DSSIM. Both, deliberately: the earlier research found this
upscaler scores 33.6 dB against ground truth while *inverting* the visual ranking, so a
PSNR-only answer would repeat in a new form the mistake this document exists to record.

Two window sizes were measured, and are reported separately rather than pooled, because
a smaller model input is a different regime.

## 3. Results — 2048x2988 window, 12 images, middle slice

`G` 2048x2988 → `S` 512x747. Rect `512x320+0+213` cuts B from S; `2048x1280+0+852` cuts A
from the 4x frame and T from G. PSNR higher is better; **DSSIM lower is better**.

| PSNR(A,T) | PSNR(B,T) | Δ | DSSIM(A,T) | DSSIM(B,T) | Δ | image |
|---|---|---|---|---|---|---|
| 22.16 | 22.14 | +0.02 | 0.29446 | 0.29584 | −0.00138 | abstract_blue_texture_4503.JPG |
| 19.58 | 19.57 | +0.01 | 0.23308 | 0.23380 | −0.00072 | aerial_view_waves_ocean_6671.jpg |
| 28.25 | 28.18 | +0.07 | 0.05968 | 0.06012 | −0.00044 | bokeh_nature_scene_7629.jpg |
| 29.11 | 29.17 | −0.05 | 0.12803 | 0.12783 | +0.00020 | foggy_forest_3113.jpg |
| 31.37 | 31.34 | +0.03 | 0.06459 | 0.06372 | +0.00087 | green_grass_texture_3997.png |
| 27.71 | 27.65 | +0.06 | 0.15226 | 0.15307 | −0.00081 | man_with_car_in_fog_1158.jpg |
| 28.02 | 28.01 | +0.01 | 0.14618 | 0.14640 | −0.00022 | moss_with_pine_needles_5324.jpg |
| 34.59 | 34.56 | +0.02 | 0.08129 | 0.08147 | −0.00018 | mountain_landscape_sunset_5677.jpg |
| 35.43 | 35.42 | +0.01 | 0.03122 | 0.03111 | +0.00011 | purple_nebula_glow_0312_x.heic |
| 27.83 | 27.81 | +0.02 | 0.23231 | 0.23286 | −0.00056 | saint_with_angel_painting_7850.jpg |
| 21.12 | 21.08 | +0.04 | 0.19482 | 0.19575 | −0.00094 | snowy_forest_6657.jpg |
| 30.23 | 30.09 | +0.14 | 0.04541 | 0.04722 | −0.00180 | snowy_mountain_4490.jpg |

Whole-frame closer on 11 of 12 by PSNR (median +0.02 dB) and 9 of 12 by DSSIM.

## 4. Results — 1440x2160 window, 16 images, both slices

The smaller window admits four more photographs, including the two that disagreed most
between the arms and were excluded by the larger window. It also adds the **top slice**,
whose crop origin is `0,0`.

| slice | PSNR(A,T) | PSNR(B,T) | Δ | DSSIM Δ | image |
|---|---|---|---|---|---|
| middle | 23.21 | 23.19 | +0.02 | −0.00202 | abstract_blue_texture_4503.JPG |
| top | 24.73 | 24.72 | +0.01 | −0.00033 | abstract_blue_texture_4503.JPG |
| middle | 19.10 | 19.06 | +0.04 | −0.00035 | aerial_view_waves_ocean_6671.jpg |
| top | 18.85 | 18.84 | +0.01 | −0.00064 | aerial_view_waves_ocean_6671.jpg |
| middle | 24.88 | 24.85 | +0.03 | −0.00081 | blade_runner_2049_concept_poseter_.webp |
| top | 28.05 | 28.02 | +0.02 | −0.00017 | blade_runner_2049_concept_poseter_.webp |
| middle | 26.95 | 26.96 | −0.01 | −0.00033 | bokeh_nature_scene_7629.jpg |
| top | 26.42 | 26.36 | +0.06 | −0.00039 | bokeh_nature_scene_7629.jpg |
| middle | 24.32 | 24.55 | **−0.24** | +0.00087 | dark_stones_7236.png |
| top | 25.79 | 25.78 | +0.01 | −0.00025 | dark_stones_7236.png |
| middle | 30.18 | 29.89 | +0.29 | −0.00006 | foggy_forest_3113.jpg |
| top | 31.61 | 31.60 | +0.02 | −0.00044 | foggy_forest_3113.jpg |
| middle | 31.41 | 31.31 | +0.10 | −0.00034 | green_grass_texture_3997.png |
| top | 32.24 | 32.22 | +0.01 | +0.00039 | green_grass_texture_3997.png |
| middle | 26.87 | 26.82 | +0.05 | −0.00114 | man_with_car_in_fog_1158.jpg |
| top | 35.80 | 35.51 | +0.29 | −0.00025 | man_with_car_in_fog_1158.jpg |
| middle | 27.33 | 27.28 | +0.05 | −0.00001 | moss_with_pine_needles_5324.jpg |
| top | 31.03 | 30.92 | +0.10 | −0.00054 | moss_with_pine_needles_5324.jpg |
| middle | 22.51 | 22.33 | **+0.18** | −0.00088 | **mountain_lake_reflection_4788.jpg** ← previously excluded |
| top | 20.88 | 20.68 | **+0.20** | −0.00653 | **mountain_lake_reflection_4788.jpg** ← previously excluded |
| middle | 35.89 | 35.87 | +0.02 | −0.00019 | mountain_landscape_sunset_5677.jpg |
| top | 40.09 | 40.05 | +0.03 | −0.00007 | mountain_landscape_sunset_5677.jpg |
| middle | 34.91 | 34.88 | +0.03 | −0.00004 | purple_nebula_glow_0312_x.heic |
| top | 35.55 | 35.54 | +0.01 | +0.00002 | purple_nebula_glow_0312_x.heic |
| middle | 27.75 | 27.73 | +0.02 | −0.00082 | saint_with_angel_painting_7850.jpg |
| top | 27.85 | 27.83 | +0.01 | −0.00024 | saint_with_angel_painting_7850.jpg |
| middle | 20.47 | 20.36 | +0.11 | −0.00280 | snowy_forest_6657.jpg |
| top | 26.48 | 26.06 | **+0.42** | −0.00217 | snowy_forest_6657.jpg |
| middle | 18.74 | 18.84 | **−0.10** | +0.00106 | **snowy_forest_landscape_9522.jpg** ← previously excluded |
| top | 18.39 | 18.40 | −0.01 | −0.00041 | **snowy_forest_landscape_9522.jpg** ← previously excluded |
| middle | 29.22 | 29.04 | +0.17 | −0.00278 | snowy_mountain_4490.jpg |
| top | 31.20 | 30.88 | +0.32 | −0.00186 | snowy_mountain_4490.jpg |

**The two previously-excluded photographs hid no reversal.**
`mountain_lake_reflection_4788` favours whole-frame on *both* slices, by margins above
the pooled median, with DSSIM agreeing — its top slice is the largest DSSIM margin
anywhere in the 56. `snowy_forest_landscape_9522` is the one photograph leaning the other
way, by 0.10 dB, and it is the hardest image in the set outright: 18.4 dB against truth,
the lowest of all 56. The photograph that disagreed most between the two methods is one
the model reconstructs badly *either way*.

## 5. Aggregate over all 56 comparisons

| | PSNR | DSSIM |
|---|---|---|
| whole-frame closer | **50 of 56** | **47 of 56** |
| pooled median delta | **+0.025 dB** | −0.00036 |
| extremes | −0.24 to +0.54 dB | |

Absolute reconstruction quality spans 18.39 to 40.09 dB against truth. The per-window
medians are +0.023 (2048) and +0.032 (1440) — same sign, same order, with the smaller
window's spread wider exactly as a smaller model input should be.

**A consistent sign across 50 of 56 is a real pattern. It is not a quality difference.**
A median of 0.025 dB is about a thousandth of the range these images span among
themselves. The likelier explanation is something systematic about larger model inputs
than anything about wallpapers.

## 6. Where the difference actually lives

Measured band by band down the widest-gap slice, 128-row bands of its 1280 rows:

| rows | MSE A vs T | MSE B vs T | A vs B, mean abs | B's deficit share |
|---|---|---|---|---|
| 0–127 | 9.0508 | 9.0508 | **0.000** | 0 |
| 128–255 | 7.6508 | 7.6508 | **0.000** | 0 |
| 256–383 | 5.6460 | 5.6460 | **0.000** | 0 |
| 384–511 | 3.6183 | 3.6183 | **0.000** | 0 |
| 512–639 | 4.2017 | 4.2017 | **0.000** | 0 |
| 640–767 | 6.6526 | 6.6526 | **0.000** | 0 |
| 768–895 | 11.7725 | 11.7748 | 0.053 | 0.0% |
| 896–1023 | 41.3930 | 41.4595 | 0.089 | 0.1% |
| 1024–1151 | 215.9454 | 222.3914 | 0.586 | 6.0% |
| **1152–1279** | **500.4259** | **601.4773** | **5.789** | **93.9%** |

Band-mean MSE reproduces the published PSNRs exactly — 29.07 dB for arm A, 28.52 for arm
B — so the table is consistent with the headline numbers rather than a separate
measurement that happens to agree.

The two outputs are **bit-identical for the first 800 rows**, mean *and* max difference
exactly zero. (The table's identical run stops at 767 because that is a band boundary,
not because row 768 differs.) The last 128 rows carry 93.9% of the deficit; the last 256
carry 99.9%. Divergence begins 480 output rows from the cut — 120 rows of model input, a
tile-scale distance — and is strictly zero beyond it.

The smaller window says the same thing more sharply: on its 900-row slice the arms are
bit-identical for 800 rows (88.9%), and the last 128 rows carry **100.0%** of the
deficit.

**This is a cut-edge effect and nothing else** — which favours the design, because in
production a whole-frame slice has frame context behind every cut edge and a per-slice
enlargement has none.

## 7. Two measurements that looked conclusive and were not

Both are recorded because each *passed* and each was wrong, and the shape recurs.

**The noise fixture.** The first gate measured 44.83 dB and declared a pass — on a
generated-noise source. This model annihilates noise: standard deviation collapses from
0.2898 to 0.0232, a 12.5-fold flattening, so the measurement compared two nearly uniform
images. The research document that predates this one had already quantified the same
inflation: *"PSNR vs Lanczos: 53 dB on a synthetic image, 40 dB on a real photo."* Run on
real photographs, the same procedure spans 33.43 to 52.61 dB — straddling both the pass
bar and the revert floor.

**The blind control.** A top-slice measurement was then added on the theory that its
`0,0` origin would catch an arm cutting the wrong region — the `sips` defect where a zero
offset silently returns the centred crop. It passed at 44.83 dB. It is blind: under that
bug *both* arms receive the centred crop, and those centres are exactly a factor of four
apart (2724 = 4 × 681), so the two slide onto the middle slice together and still agree.
It catches an off-by-one, which is all its own docstring claims.

A third error is worth recording for the same reason: a row-level check aimed at the
slice's *top* edge concluded the difference was not an edge effect. Arm A's top slice
begins at `+0+0` **of its own 4x frame**, so that edge is a real image boundary in both
arms — identical by construction, and incapable of showing a difference. The measurement
in section 6 is taken at the *cut* edge, and reverses the conclusion.

The common thread: three measurements, each aimed at something that could not answer the
question, each caught only by measuring again rather than by reading the code again.

## 8. Reproducing this

`tests/test_real_upscaler.py`, marked `real_upscaler` and deselected by default. It needs
`upscayl-bin` installed (`paperhanger setup`) and `magick` on PATH for the metrics —
test-only; removing ImageMagick from the *runtime* is a goal of this project, not from
the test bench.

```
uv run pytest -m real_upscaler -s
```

Difference maps for the widest-gap case are in `equivalence-difference-maps/`.
