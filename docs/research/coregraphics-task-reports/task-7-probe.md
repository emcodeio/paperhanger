# Task 7: Probe on ImageIO, and the CLI knock-on

**Status:** DONE_WITH_CONCERNS (the concerns are scope notes and one
deliberate redundancy, not defects — see the end)

**Commits:**
- `cf51bd6` feat: probe through ImageIO, and the last sips call goes
- `8ae6e60` docs: sips is gone from the tool, and a troubleshooting entry goes with it

---

## The format table, recorded before the mapping was written

Ran the old `sips -g`-parsing `probe` body over all 894 corpus photographs
plus `.DS_Store`, capturing the `CGImageSourceGetType` UTI for each file in
the same pass. Harness:
`scratchpad/baseline_probe.py` — self-contained, with the old probe body
copied in rather than imported, so it keeps measuring the OLD behaviour after
`imaging.py` was rewritten.

| old `probe` format | corpus count | ImageIO UTI | new `probe` format | reproduced |
|---|---|---|---|---|
| `jpeg` | 841 | `public.jpeg` | `jpeg` | yes |
| `png` | 47 | `public.png` | `png` | yes |
| `webp` | 3 | `org.webmproject.webp` | `webp` | yes |
| `heic` | 2 | `public.heic` | `heic` | yes |
| `gif` | 1 | `com.compuserve.gif` | `gif` | yes |

894 files, 894 accounted for. **Dimensions: 0 disagreements** across all 894 —
old `sips -g pixelWidth/pixelHeight` against the new `CGImageGetWidth/Height`.
**Non-images: 1 both ways** (`.DS_Store`).

These five are the strings I **verified**. The table in `_cg.SOURCE_FORMATS`
carries ten more, each also measured — a file written to that format, then put
through both `sips -g format` and `CGImageSourceGetType`
(`scratchpad/format_sweep.py`):

| `sips -g format` | ImageIO UTI |
|---|---|
| `tiff` | `public.tiff` |
| `avif` | `public.avif` |
| `jp2` | `public.jpeg-2000` |
| `pbm` | `public.pbm` |
| `bmp` | `com.microsoft.bmp` |
| `ico` | `com.microsoft.ico` |
| `dds` | `com.microsoft.dds` |
| `psd` | `com.adobe.photoshop-image` |
| `tga` | `com.truevision.tga-image` |
| `sgi` | `com.sgi.sgi-image` |

Nothing in the table is **inferred**. Formats I could not produce on this
machine (jxl, exr, camera raw, pict, qtif) are absent from it and report
`unknown`.

### The brief's suggested mapping rule is wrong, and so is the obvious repair

The brief's Step 3 said to map "by reading the CFString and stripping the
`public.` prefix". That produces `org.webmproject.webp` and
`com.compuserve.gif` — **two of the five corpus formats**, silently, with no
test failing and no file differing.

The obvious repair, taking the last dot-separated component, fixes those two
and then breaks on four more: `com.adobe.photoshop-image` → `photoshop-image`
(not `psd`), `com.truevision.tga-image` → `tga-image` (not `tga`),
`com.sgi.sgi-image` → `sgi-image` (not `sgi`), `public.jpeg-2000` →
`jpeg-2000` (not `jp2`). **Four of fifteen measured formats — a 27% error rate
on the rule.**

So `SOURCE_FORMATS` is an explicit table, and the fallback for an unlisted UTI
is `"unknown"` — which is what the old probe answered when `sips` printed no
`format:` line — rather than a name derived from the string. Extrapolating
either rule to a format nobody measured would be exactly the "rule measured on
one file and written for the population" failure. Both rules are pinned as
mutations (M2, M3) that must go red.

**No production caller reads the format string.** `cli.py:600` binds it as
`fmt` and never uses it; `execute.py` and `imaging.crop` bind it as `_`. Only
tests consume it. That is why `"unknown"` is affordable, and it is also why
this table had to be measured rather than reasoned about: nothing downstream
would have complained.

---

## `.DS_Store` and the lying extension

**`.DS_Store` → `None`.** Verified against the real corpus file and against
synthetic Bud1 bytes; both behave identically.

- Old: `sips` died by **signal 6 (SIGABRT)**, returncode `-6`, on an uncaught
  `NSInvalidArgumentException` — `*** -[__NSDictionaryM setObject:forKey:]:
  object cannot be nil (key: typeIdentifier)`. That is what `returncode < 0`
  existed for.
- New: ImageIO **does** build a `CGImageSource` for the file — this is the
  part I checked rather than assumed, and the brief's guess that "ImageIO
  should simply decline it" is half right. The source is created; it then
  reports `CGImageSourceGetCount` = 0, `CGImageSourceGetType` = NULL, and
  `CGImageSourceCreateImageAtIndex(0)` = NULL. **The decode is what declines
  it**, not the source creation. A probe that had gated only on
  `CGImageSourceCreateWithURL` would have accepted it.

**The lying extension → `webp`.** `snowy_forest_landscape_9522.jpg` is a WebP;
both old and new report `(3840, 2160, "webp")`. Pinned twice: against the real
corpus file (tier 2) and against `webp_fixture` written to a `.jpg` name
(tier 1, no corpus needed).

The tier-1 test asserts the **positive** string `webp`, not the brief's
`!= "jpeg"`. A probe that had started answering `"unknown"` for every WebP
would satisfy the inequality and would be a regression.

### Everything else checked for the never-raises contract

Old against new, agreeing on every one (`scratchpad/degenerate_sweep.py`,
`scratchpad/perms_check.py`):

| input | old | new |
|---|---|---|
| directory | None | None |
| missing path | None | None |
| dangling symlink | None | None |
| symlink loop | None | None |
| FIFO named `.png` | None | None |
| `chmod 000` PNG | None | None |
| zero-byte `.jpg` / `.png` | None | None |
| text named `.jpg` | None | None |
| PDF | None | None |
| JPEG truncated to 2% / 200 bytes | None | None |
| JPEG truncated to 50% / 90% | `(400,300,jpeg)` | same |
| JPEG with a mangled body | `(400,300,jpeg)` | same |

The FIFO is worth noting: a probe that opened and read the file would have
blocked forever. It does not.

---

## Decode, not properties — and it is cheaper, not dearer

The brief suggested `CGImageSourceCopyPropertiesAtIndex` might be needed. It
is not, and using it would have been worse on both axes I measured.

**Correctness.** The properties dictionary comes back **without pixel
dimensions** for a PDF and for a JPEG truncated to its first 200 bytes — both
of which have a valid source and a valid UTI. A properties-based probe needs a
second rule to reject those. The decode returns NULL for both, which is the old
probe's answer too.

**Cost.** `CGImageSourceCreateImageAtIndex` returns a **lazily decoded**
CGImage, so asking for its dimensions never pulls pixels through memory.
Measured on the largest corpus file, `green_grass_texture_3997.png`
(9072×12096, 101 MB on disk), peak RSS via `/usr/bin/time -l`:

| | peak RSS |
|---|---|
| interpreter + ctypes bindings, no imaging | 21.1 MiB |
| probe (source + decode + GetWidth/GetHeight) | **25.5 MiB** |
| same image, pixels forced through `CGDataProviderCopyData` | 1004.7 MiB |

A 40× margin. Timing over 25 corpus photographs: decode **0.021 s**,
properties 0.030 s, the `sips` subprocess 0.399 s. This matters because
`probe` runs roughly 4,300 times in a full corpus run (894 in the scan, plus
one per crop, per resizing render, and per published render).

That measurement is from **one file** — the largest. The claim it supports is
about the mechanism (laziness), which is general; the number is not.

---

## What changed in `doctor` and the pre-flight

**Deleted from `cli.py`:** `sips_is_present()`, `check_sips()`, the
`check_sips()` call in `_run` (which ran before the scan), and the
`sips : ... (ok/MISSING)` line from `_doctor()`.

Both existed for one failure: `probe` returns None for anything it cannot
measure, so a missing or quarantined `sips` was indistinguishable from a
directory of 894 files that were none of them images. The run printed
`0 images, 0 outputs, 0 rejected, 0 already done, 894 non-images skipped`,
wrote nothing and exited 0 — telling the user their whole library was
unreadable when what was missing was one tool.

With no external binary in the imaging path, **that failure class stops
existing** rather than being guarded against. A pre-flight for it would be a
branch that can never be taken, and the `doctor` line named a tool the tool no
longer uses. The reasoning is kept as a comment where each used to be, so the
deletion is legible rather than mysterious.

**`upscayl-bin` keeps everything.** `doctor` still reports the binary, its
provenance, the pinned release, the model and the models dir; `_run` still
demands both through `toolchain.ensure_ready()` before any render that needs
them. `upscale` still shells out and still asserts its post-condition.

**`doctor` output now:**

```
upscayl-bin    : /Users/eerickson/.local/share/paperhanger/bin/upscayl-bin  (installed by `paperhanger setup`)
pinned release : 20251207-174704  (what `paperhanger setup` installs)
model          : upscayl-standard-4x (ok)
models dir     : /Users/eerickson/.local/share/paperhanger/models
```

**README:** the troubleshooting entry "Everything is reported as a non-image"
is deleted; Requirements now says one shelled-out program, not two; the crop
section's "zero arrives when `probe` moves too" is resolved; a new section
records what preserving probe's answer required.

---

## Tests

| run | result |
|---|---|
| `uv run pytest tests/test_imaging.py tests/test_cli.py -v` | pass |
| `uv run pytest` (tier 1, fast) | **769 passed**, 35 deselected, 1:52 |
| `uv run pytest -m corpus` (tier 2) | **29 passed**, 773 deselected, 13:06 |
| `uv run paperhanger doctor` | exit 0, output above |

Fast suite was 753 before this task and 767 after the first pass; 769 after the
two tests added from mutation findings. Corpus tier was 27, now 29.

**Deleted** (4 tests + 1 fixture in `test_cli.py`): `no_sips`,
`test_a_missing_sips_stops_the_run_rather_than_emptying_it`,
`test_without_the_check_a_missing_sips_reports_the_library_as_unreadable`,
`test_a_missing_sips_is_refused_before_the_directory_is_read`,
`test_doctor_and_the_run_read_the_same_sips`. They pinned a check that no
longer exists; there was nothing left for them to assert. `test_imaging.py`'s
`test_probe_names_a_format_it_cannot_read_rather_than_crashing` also went — it
monkeypatched `imaging.subprocess.run` to fake `sips` stdout.

**Added, tier 1:** `test_probe_declines_a_ds_store`,
`test_probe_reports_the_real_format_not_the_extension`,
`test_probe_never_raises`, `test_probe_runs_no_subprocess`,
`test_probe_declines_the_real_ds_store`; in `test_cg.py`
`test_the_read_and_write_tables_agree_where_they_overlap`,
`test_no_two_utis_share_a_format_name`,
`test_a_uti_that_is_not_in_the_table_is_named_unknown`,
`test_a_null_type_is_named_unknown`,
`test_formats_outside_the_corpus_keep_the_names_sips_gave_them` (×5),
`test_a_pdf_is_not_an_image`, `test_probe_file_never_raises_where_load_does`,
`test_probe_file_answers_none_when_the_layer_below_raises`,
`test_a_null_image_measures_zero_rather_than_crashing`,
`test_every_framework_function_called_is_declared`; in `test_cli.py`
`test_a_scan_measures_photographs_with_every_subprocess_broken`,
`test_doctor_reports_the_upscaler_and_no_longer_reports_sips`,
`test_the_sips_preflight_is_gone_from_the_module`.

**Added, tier 2:**
`test_probe_reproduces_the_sips_format_census_over_the_whole_corpus` (the
counts table above, asserted through `cli.scan`) and
`test_probe_agrees_with_sips_on_every_corpus_dimension` (894 live `sips`
comparisons rather than a transcribed table).

Two tests carry an explicit **control assertion** so they cannot pass for the
wrong reason: the truncated-JPEG test asserts the untruncated fixture probes
correctly first, and the unknown-UTI test asserts `public.jpeg` still maps to
`jpeg` — without it, a `_pystr` that had stopped reading CFStrings entirely
would satisfy the "unknown" assertion.

`test_every_framework_function_called_is_declared` is new coverage the project
did not have: it parses `_cg.py` and fails if any `_CF`/`_CG_LIB`/`_IO_LIB`
attribute is called without a `restype`/`argtypes` in `_declare()`. An
undeclared call is a segfault, not an error.

---

## Mutations

Run with `__pycache__` purged before every invocation and
`PYTHONDONTWRITEBYTECODE=1` set, harness self-contained in the scratchpad,
files backed up by **copy** and restored in a `finally` rather than through
git (the stash and index are shared with other sessions).

| # | mutation | result |
|---|---|---|
| M1 | drop the webp row from `SOURCE_FORMATS` | **died** |
| M2 | `_format_name` by stripping the `public.` prefix | **died** |
| M3 | `_format_name` by the last dot-separated component | **died** |
| M4 | `probe_file` returns the raw UTI instead of the short name | **died** |
| M5 | `probe_file` drops the "no image" guard | *survived* |
| M6 | `probe_file` drops the "no source" guard | **died** |
| M7 | `probe_file` narrows its `except` to `ValueError` | **died** |
| M8 | `CGImageSourceGetType` loses its declaration | **died** |
| M9 | `imaging.probe` hardcodes the format string | **died** |
| M10 | the `sips` pre-flight comes back | **died** |
| M11 | the `sips` line comes back to `doctor` | **died** |
| M12 | `width < 1` loosened to `width < 0` | *survived* |
| M13 | `_pystr` returns a truncated buffer instead of None | *survived* |

10 of 13 died. M4 is the exact regression the brief warned about — a format
string quietly becoming `public.jpeg` — and it goes red in 0.08 s.

### The three survivors, and what chasing M5 turned up

M5 survived, and the reason is a real finding rather than a test gap.
Measuring instead of guessing:

- `CGImageGetWidth(NULL)` returns **0**, it does not crash.
- `CGImageRelease(NULL)` is a **no-op**.
- `CFRelease(NULL)` **kills the process** — measured, exit 133.

So `probe_file`'s two early returns are not the same kind of guard, though
they read alike:

- **`if not source` is necessary.** Without it the NULL enters the `Scope` as a
  CF handle and `CFRelease(NULL)` runs on the way out. M6 "died" with an
  *empty* summary line, which I followed up rather than accepting: re-running
  it alone gives **returncode 133, no stdout, no stderr** — pytest killed
  outright, not a test failing.
- **`if not image` is redundant and deliberately kept.** With it deleted,
  `CGImageRelease(NULL)` is harmless and `CGImageGetWidth(NULL)` is 0, so the
  `width < 1` check below answers None anyway. No behavioural test can
  distinguish the two versions, because both return None for every input that
  exists.

That also explains **M12**: `width < 1` is the backstop for both NULL paths as
well as for a degenerate image, and no input available on this machine
produces a 0-dimension image any other way. Rather than leave this as an
unexplained survivor, I added
`test_a_null_image_measures_zero_rather_than_crashing`, which pins the Apple
behaviour the redundancy rests on — if a macOS release ever changed it, that
test fails and the guard stops being optional. Both facts are now recorded in
`probe_file`'s docstring.

**M13** survives because `CFStringGetCString` does not fail for any UTI on
this machine (they are all short ASCII). The `return None` is a guard against a
buffer that turns out too small; the alternative to it is a silently truncated
string that could collide with a real format name. Unreachable today, cheap,
and documented as such.

---

## Concerns

1. **`imaging.SIPS` deliberately survives.** Five differential test modules
   (`test_crop_differential`, `test_encode_differential`,
   `test_resize_differential`, `test_normalize_differential`, and
   `test_imaging`'s profile helper) spell the path as `imaging.SIPS` and run
   the old tool as their reference. Deleting the constant would delete the
   comparisons that prove the six replacements right. **No production code
   path invokes it** — that is asserted by
   `test_a_scan_measures_photographs_with_every_subprocess_broken`, which
   breaks every spawn on the machine and still measures a photograph. Task 8's
   acceptance criterion `grep -rn "sips" paperhanger/` returns nothing is
   therefore **not yet satisfied**, by design; the constant and its comment are
   Task 8's to remove along with the harnesses.

2. **Formats outside the measured fifteen report `unknown` where the old probe
   would have reported a `sips` name.** Camera raw is the realistic case — a
   `.CR2` or `.NEF` in a Pictures folder would have got whatever `sips` called
   it and now gets `unknown`. It still probes as an **image** with correct
   dimensions, so nothing about the pipeline changes; only the unread string
   differs. I could not synthesize a raw file to measure, and inventing a row
   for one would be the guess this table exists to avoid.

3. **`probe` now accepts nothing the old one rejected and rejects nothing it
   accepted**, across 894 corpus files and 12 constructed degenerate inputs.
   I cannot prove that for inputs neither of us thought of; the differential
   harness genuinely cannot help here, and the census is the strongest
   substitute I found.

4. **The memory and timing figures are single-file measurements.**
   `green_grass_texture_3997.png` for RSS, 25 files for timing. The mechanism
   they demonstrate (lazy decode) is general; the numbers are that file's and
   that machine's, and the report and docstrings say so.

5. **Tier 3 (`-m real_upscaler`) was not run.** It is the outstanding gate for
   the whole branch and is unrelated to `probe`; nothing in this task touches
   the upscale path beyond leaving `upscale`'s `_run` and post-condition
   untouched.

6. **No `tools/` entry was created** for the four measurement harnesses. The
   repo has no `tools/TOOLS.md` convention, and these are one-off evidence
   scripts for this task, kept in the session scratchpad:
   `baseline_probe.py`, `format_sweep.py`, `degenerate_sweep.py`,
   `probe_memory.py`, `perms_check.py`, `null_image.py`, `null_release.py`,
   `mutate.py`, `verify_m6.py`. The claims they produced are recorded here and
   in the source; the scripts themselves are scratch.
