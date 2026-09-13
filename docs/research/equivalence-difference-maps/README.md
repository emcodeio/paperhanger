# Difference maps from the equivalence gate

Where the two upscaling methods diverge from ground truth, for the photograph where they
differ most. Evidence for `2026-09-13-whole-frame-equivalence-gate.md`, which carries the
protocol and all the numbers.

Source photograph: `snowy_forest_6657.jpg`, top slice, at both measured window sizes.

```
<window>/whole_then_cut_minus_truth.png      arm A's error against the original pixels
<window>/cut_then_upscale_minus_truth.png    arm B's error against the same original
```

The ground-truth slice and both reconstructions are deliberately **not** here. They are
crops of a real photograph from the author's library, and `CLAUDE.md` keeps wallpaper
images out of this repository. These maps carry no recognizable photograph content.

## Reading them

**Brightness is not a magnitude.** The maps are auto-levelled so the differences are
visible at all — the real errors are a couple of levels out of 255, which would be an
almost black image if plotted honestly. Use them to see *where* the two methods diverge,
never *how much*. The magnitudes are in the gate document's band table.

What they show, and what the band table measures numerically: the two methods are
bit-identical across the first 800 rows, and essentially all of the divergence sits in
the last 128 rows — the edge against the cut. The 1440x2160 window shows it more starkly
than the 2048x2988 one.

## Scores for this case

| window | PSNR(A,T) | PSNR(B,T) | Δ | DSSIM(A,T) | DSSIM(B,T) |
|---|---|---|---|---|---|
| 2048x2988 | 30.23 | 30.09 | +0.14 | 0.04541 | 0.04722 |
| 1440x2160 | 26.48 | 26.06 | +0.42 | 0.08214 | 0.08431 |

Positive Δ means whole-then-cut is closer to the truth. Lower DSSIM is better. These are
the widest gaps in the whole experiment; the pooled median across all 56 comparisons is
+0.025 dB, which is why the conclusion is that the two are interchangeable.
