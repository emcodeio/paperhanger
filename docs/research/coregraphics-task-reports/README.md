# Task reports from the CoreGraphics migration

The nine implementation reports from the branch that replaced `sips` with
CoreGraphics, one per task, kept as the provenance behind the measured numbers in
`paperhanger/_cg.py` and `paperhanger/imaging.py`.

They are here for one reason. Those two modules carry roughly forty measured figures
in their docstrings — byte-identity results, peak-memory comparisons, sample counts,
timings — and each is the justification for a specific construction. Most docstrings
name the fixture the number came from, which was the explicit lesson of Task 1. Some do
not. For those, this is where the command, the fixture and the machine state live.

These are working documents, not polished findings. They record what was tried, what
failed, and what was refuted — including claims the reports themselves later gave up.
Read them as evidence, not as conclusions. Where a report and the code disagree, the
code and its review are later.

## What is where

| File | The task | What it is worth reading for |
|---|---|---|
| `task-0-measure-the-unknowns.md` | Pre-flight | The four unknowns §6 of the spec listed, and what each measured. Largely superseded by `../2026-09-13-coregraphics-preflight-measurements.md`, which is the polished version with its instruments. |
| `task-1-the-binding-layer.md` | `_cg.py` | The scope object, the NULL contract, and the asymmetry the whole design rests on: `CGImageGetWidth(NULL)` is 0, `CGImageRelease(NULL)` is a no-op, and `CFRelease(NULL)` kills the process. |
| `task-2-the-differential-harness.md` | The gate | Why the comparison bar is decoded pixels plus colour type plus channel count, after four weaker formulations were each refuted by a count. |
| `task-3-crop.md` | `crop` | The 16-bit measurement table. `sips` drops 16 bits on the direct crop, the crop at `0,0`, the pad alone, pad-then-crop and a plain resample — which is what established the defect as Apple's rather than ours, against what the spec and the issue first claimed. |
| `task-4-resample.md` | The resample | The identity skip, the 577 no-op resamples it removed, and where drawing diverges from skipping. |
| `task-5-encode.md` | The encode | Quality-number equivalence with `sips` at every format, and the progressive-scan finding. |
| `task-6-colour-normalization.md` | The conversion | The BT.709 finding: `ITU-2020.icc` and `ITU-709.icc` carry the BT.709 OETF as a parametric rTRC, CoreGraphics follows it, and `sips` substitutes gamma 2.4. Derived from the profile's own parameters rather than from a comparison. |
| `task-7-probe.md` | `probe` | The format census over all 894 files, measured before any mapping was written, and the reason `SOURCE_FORMATS` is an explicit table rather than a rule: every rule tried breaks somewhere. |
| `task-8-retiring-sips.md` | The retirement | The classification of all 124 differential tests, 54 deleted and 70 moved, one by one. |

## What is not here

The task reviews, the fix-wave documents and the execution ledger were scratch and are
gone. What outlived them:

- `../2026-09-14-coregraphics-branch-review.md` — the whole-branch merge-readiness
  review, including the ownership audit of `_cg.py` and the mutation evidence for what
  the suite actually guards.
- `../2026-09-13-coregraphics-preflight-measurements.md` — Task 0's polished output,
  with its twenty-one instruments.
- `../2026-09-11-replacing-pixelmator-and-imagemagick.md` §12 — the seven `sips`
  defects and the eighth, as findings about the tools rather than constraints on ours.
- `../../superpowers/specs/2026-09-13-coregraphics-imaging-design.md` — the design,
  with §6's unknowns carrying their dated answers.
