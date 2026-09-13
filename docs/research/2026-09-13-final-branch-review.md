# Merge-readiness review of the implementation branch

The whole-codebase review run after all sixteen tasks landed, plus what it found and
what was done about it. Kept because the mutation evidence is the strongest statement
available about what this test suite actually guards, and because the review's method
is worth repeating rather than re-inventing.

Verdict at the time: **merge-ready, no blocking findings.** Every should-fix below was
subsequently fixed; what remains open is listed at the end.

---

## Method

Every source file in `paperhanger/` read in full, plus the spec, the plan's global
constraints, `tests/conftest.py`, `tests/pngwriter.py`, and the substantive parts of
every test file. Then three things that reading cannot do:

1. **Twelve deliberate mutations** through a throwaway copy of the tree, one at a time,
   looking for assertions that survive plausible breakage.
2. **A census of which bands the fast suite actually renders**, by instrumenting
   `execute.render` to log every successful publish.
3. **A direct cost measurement** of the pad-and-shift crop workaround.

## What the mutations showed

All twelve were caught. Among them: archiving on failure (6 tests fail),
`_verify_dimensions` turned into a no-op (4), the `--cropOffset` workaround removed (7),
`--matchTo` dropped from the normalization step (2), and the 300 Mpx pixel cap disabled
(6). No vacuous assertion was found anywhere in the suite.

That is the useful form of the claim "the tests are good" — not a passing run, but a
list of specific breakages that a passing run would have caught.

## The finding reading could not have produced

**The fast suite rendered zero band-3 outputs.** The census came back band 1 ×3, band 2
×37, band 4 ×54, and `UPSCALE_REDUCE` **×0**.

Band 3 is 2116 of the corpus's 3441 outputs — 61% — and it is the *only* band where the
reduce-after-crop step does anything at all: a band-4 slice of the 4x frame is already
exactly the planned size, so `resize` is a dimensional no-op either way. The majority
path in production had never rendered a live pixel in the per-commit suite. Tier 2
covered it, but tier 2 is deselected by default and skips without the corpus.

Fixed by one fixture: 2400x1200 on the phone path, giving 800x1200 slices, band 3,
2880x4320 out of a 9600x4800 frame. The size matters — at the true band-3 minimum
(1080x1080) the thirds *overlap* by 540 px and the net factor is exactly 4.0, so a
minimal fixture would have tested neither the disjointness nor the reduce-after-crop.
It is also the first tier-1 test that pads a 4x **frame** rather than a source.

## Four invariants that rested on a single assertion each

Not false coverage — thin coverage. Each of these was one deleted test away from being
unguarded, and each is now pinned harder:

- **`execute.py` `resize = target.needs_resize or scale != 1`** — one stubbed assertion.
  Notably the band-3 fixture above does *not* cover it: band 3 sets `needs_resize`
  anyway, and for band 4 the clause is a dimensional no-op, so the two bands rendered at
  scale 4 both miss it. Pinned separately with the case it is actually for.
- **`execute.py` `_refuse_to_enlarge`** — global constraint 1's entire runtime
  enforcement. Now also covers a 4x frame that came back a pixel short, one axis at a
  time, because the guard is two comparisons and an `and` would pass both single-axis
  cases.
- **`imaging.py` `--resampleHeightWidth`** — global constraint 3, one assertion. Worse,
  the sibling by-height test did *not* fail under a `--resampleWidth`-only mutation,
  because 3840x2160 → 8533x4800 is a shape where `sips` derives 4800 from the width on
  the nose. Replaced with a four-row table of real plan shapes whose axes `sips` could
  not derive from one another, plus a second test running each mutation against `sips`
  directly so a row that stops discriminating fails rather than going quiet.
- **`report.py` pending count** — its one test used a fully-done photo, so a half-done
  miscount survived it. This needed a real change: `render_report` took the set of
  finished **sources**, so a photo with three of four outputs on disk counted four. It
  takes finished **destinations** now; which photos are wholly done derives from those,
  and the reverse never did.

## The purity boundary held, but the check did not

`tests/conftest.py`'s `assert_pure_module` inspected **imports only**. Proven against
synthetic modules: `from pathlib import Path` followed by `Path(p).exists()` and
`read_text()` passed; builtin `open()` passed; `__import__('os')` passed. Only
function-local `import subprocess` was caught. And only two of the six pure modules were
asserted at all.

The boundary did hold — all six modules were read and are genuinely pure — but the
mechanism would not have noticed if it stopped. It now parses calls as well as imports,
all four synthetic violations are caught, and seven modules are asserted.

## Other findings, all fixed

- **No `sips` pre-flight in `_run`.** If `/usr/bin/sips` were missing or quarantined,
  `probe` returns `None` for everything and a run over 894 photos would print
  `0 images, 894 non-images skipped` and exit 0 — telling the user their entire library
  is corrupt. `_doctor` had the check; `_run` did not.
- **Skip-existing re-derived three times** in the effect layer. One source of truth now,
  `execute.is_pending`, called by the report, the pre-flight and the executor alike.
- **An interrupted photo reported as "never started."** Broader than first thought: a
  photo whose plans all finished and whose original was archived microseconds before the
  signal was also counted that way. Nothing was lost; the count was wrong.
- Ten further user-facing items: a `PermissionError` escaping `sweep_partials`, `setup`
  chmodding on every run so a binary owned by another user makes the advertised fix
  command fail, a writability probe reporting `File exists` for a writable directory, a
  rejected photo whose failed move to `error/` lost the rejection, and a `300 Mpx
  exceeds the 300 Mpx cap` message at the boundary.

## What remains open

- **The pad-once-per-frame optimization.** The crop workaround pads per slice against the
  4x frame; it fires for 2 of 3 horizontal and 1 of 3 vertical slices, and scaling by 4
  preserves both bad shapes, so a photo cropped for both devices pads its whole frame
  three times. Measured 1.22 s against 0.58 s on a flat 6000x4000, and real frames are
  50–300 Mpx. Spec §7 already names the fix — pad each 4x frame once instead. Deferred to
  its own task because it modifies the workaround for the `--cropOffset 0 0` defect and
  needs its own tests. Tracked as a repo issue.
- **`execute.py`'s peak-occupancy comment** was falsified by this review — the padded copy
  is a second full-size frame alive at once — and corrected, but the underlying occupancy
  is what the optimization above would improve.
- **Crop fixture minimality.** Shrinking `test_execute.py`'s crop fixtures from 12 Mpx to
  the 8.3 Mpx floor would make three vertical thirds overlap where they are currently
  disjoint, and several region assertions depend on that separation. Left alone
  deliberately: small gain, real risk of weakening the assertions.

## Cross-module state

Interfaces did not drift across sixteen tasks — `PARTIAL_SUFFIX` is defined once and
reused, `OutputPlan` is consumed unchanged by three modules. One abstraction stopped
fitting and was removed: `PhotoWork.source_format`, whose only possible consumer
disappeared when normalization became unconditional. Spec §2's pure/effect table had
drifted from the code in both directions and was corrected.
