# Task 1 report: the binding layer

**Status:** DONE_WITH_CONCERNS — the code and tests are complete and green; the
concerns are all about what later tasks inherit, listed in §7.

**Commits**

| SHA | |
|---|---|
| `f2471cb` | test: the fixture set six later tasks assume, and the two shapes that break |
| `08e2717` | feat: the CoreGraphics binding layer, scoped and null-checked |

**Tests:** `576 passed, 25 deselected` (was 526; +30 in `tests/test_cg.py`,
+20 in `tests/test_pixels.py`). `uv run pytest tests/test_cg.py
tests/test_pixels.py -v` → 59 passed.

---

## 1. What was built

`paperhanger/_cg.py`, the only file in the project that imports `ctypes`,
asserted by a test that PARSES rather than greps (`from ctypes import CDLL`
contains no "import ctypes").

| | |
|---|---|
| `_checked(ptr, what, path)` | the single chokepoint; NULL and 0 both become `ImagingError` naming the step and the file |
| `Scope` | releases every owned handle in reverse order, on exception as well as success; `CFRelease` / `CGImageRelease` / `CGContextRelease` by kind |
| `_cfstr`, `_cfurl` | CFString and CFURL, surrogateescape-encoded so a non-UTF-8 filename leaves as `ImagingError` rather than `UnicodeEncodeError` |
| `load(scope, path)` | decode; `ImagingError` for a non-image, a missing file, or a directory |
| `dimensions(image)` | `(width, height)` |
| `colour_model(image, path)` | `"RGB"`, `"monochrome"`, `"indexed"`, … |
| `has_alpha(image)` | whether the decoded source carries a channel |
| `bitmap_format(image, path)` | `(colour space, bits per component, bitmap info)`, all three derived |
| `bitmap_context(scope, image, w, h, path)` | the one call every later task should use to get a drawing destination |
| `write_png(scope, image, out_path)` | the write primitive; unlinks first, checks `Finalize` |

## 2. Colour models: what the primitives do

Measured on this machine, 150x200 reduced to 75x100, encoded and read back.
The table on the left is what the plan's hardcoded `kCGImageAlphaNoneSkipLast`
produces; the right is what `bitmap_format` now chooses.

| source | plan's hardcoded value | this implementation |
|---|---|---|
| **Gray** (10 corpus files) | context created, **output entirely black** | `kCGImageAlphaNone`, byte-identical to `sips` |
| **RGB** (872 corpus files) | correct | `kCGImageAlphaNoneSkipLast` — unchanged |
| **RGBA** (12 corpus PNGs) | composited onto black, written back as opaque colour type 2 | `kCGImageAlphaPremultipliedLast`, channel preserved |
| **Indexed** (0 corpus files, but generated fixtures) | **NULL context** | `ImagingError` naming the colour space and the file |
| anything else (CMYK, Lab, …) | NULL or wrong | `ImagingError` naming the model |

Two findings worth carrying forward:

**`kCGImageAlphaNone` is not a universal fix.** It is a NULL context for an
RGB colour space. There is no single value that works; the choice has to be
per-model, which is why this is a derivation rather than a corrected constant.

**The grayscale failure only fires when the draw scales.** At 1:1 the bad
pairing round-trips correctly — my first probe drew at full size, got the
right gradient, and briefly appeared to contradict Task 0. Reducing to half
reproduced the measurement exactly: `[0, 0, 0, …]` against `sips`'
`[0, 26, 52, 77, …]`. This is almost certainly why the defect survived two
earlier rounds of measurement, and it is recorded in the module docstring so
a future reader who tests at 1:1 is not misled the same way.

Bits per component are derived too (8 or 16), so a 16-bit source is not
silently reduced. `sips` downconverts 16-bit on its **plain resample** as well
as its pad path — measured, and slightly wider than the design's §2 claim,
which attributes it to the pad path alone.

**Verified against real corpus sources**, read-only, nothing written there:

```
abstract_architecture_night_scene_3140.JPG  2048x2048  monochrome  ->  8bpc AlphaNone
black_and_white_landscape_2107.jpg          5120x2880  monochrome  ->  8bpc AlphaNone
death_riding_horse_with_creatures_3893.png  2539x3169  monochrome  ->  8bpc AlphaNone
brush_circle_8107.png                       2880x1800  RGB alpha   ->  8bpc PremultipliedLast
snowy_forest_landscape_9522.jpg (a WebP)    3840x2160  RGB         ->  8bpc NoneSkipLast
purple_nebula_glow_0312_x.heic              7680x4800  RGB         ->  8bpc NoneSkipLast
autumn_leaves_8716.png (interlaced)         2560x1600  RGB         ->  8bpc NoneSkipLast
```

## 3. The leak test, and what it caught

**The plan's version passes against an implementation that releases no image
at all.** I deleted `CGImageRelease` from `Scope.__exit__` and the test stayed
green. ImageIO decodes lazily, so an undecoded `CGImage` handle costs almost
nothing: 300 leaked ones came to **1.2 MB** over the clean baseline (5.6 MB
against 4.4 MB), far under any threshold worth setting. A test that only loads
and reads dimensions cannot see the thing it exists to see.

Two fixes, both measured:

**The loop has to draw.** Drawing materializes the bitmap, which is what
production does anyway. Over 300 iterations at 1200x900, with each release
removed in turn:

| | resident growth |
|---|---|
| every release present | **+5 MB** |
| `CGImageRelease` removed | **+2182 MB** |
| `CGContextRelease` removed | **+1243 MB** |
| `CFRelease` removed | **+2173 MB** |

All three confirmed red against the 50 MB threshold and then reverted. One
test now covers all three release kinds, which is why the separate
context-leak test I first wrote was dropped: it measured the same thing twice
and cost another 1.6 s.

**`ru_maxrss` is the wrong instrument.** It is a high-water mark that never
falls, so growth measured from it reads as zero once anything earlier in the
same process has peaked higher. This bit me directly: my first harness ran the
clean loop before the leaking one and reported the *clean* run at +2182 MB and
the *leaking* run at +1196 MB — the numbers inverted, because the second
measurement was reading the first one's peak. The test now measures current
RSS via `/bin/ps` (no `ctypes` outside `_cg.py`) and reports peak only in the
failure message. The reasoning is in the helper's docstring so nobody
"simplifies" it back.

## 4. Two other instruments that would have failed silently

**`test_an_indexed_source_is_refused_by_name` passed with the rule deleted.**
It asserted `"indexed" in str(exc.value)`; with the refusal removed, the NULL
context still raises `ImagingError`, and its message quotes the path — which
under pytest lives in a `tmp_path` directory named after the test, containing
the word "indexed". **The assertion was reading its own test name back.** Now
matched as the full phrase `"indexed colour space"`, confirmed red against the
mutation. This is the Task 0 lesson one layer down and I nearly shipped it.

**A probe of mine segfaulted on a use-after-free**, calling `has_alpha` on an
image after its `Scope` had exited. Not a library defect — the scope did
exactly what it promises — but worth recording as the failure mode callers
will hit: a handle read after its scope is a hard crash, not an exception, and
no amount of `_checked` helps.

## 5. Mutations run

| # | mutation | caught by | result |
|---|---|---|---|
| 1 | `CGImageRelease` removed | leak test | +2182 MB, red |
| 2 | `CGContextRelease` removed | leak test | +1243 MB, red |
| 3 | `CFRelease` removed | leak test | +2173 MB, red |
| 4 | alpha hardcoded to `NoneSkipLast` (the plan's line) | 3 tests | red, including "the whole frame came back black" |
| 5 | indexed refusal removed | indexed test | survived at first — see §4 — red after the fix |
| 6 | `OSError` guard in `write_png` removed | OS-error test | red, `NotADirectoryError` escaped the layer |

## 6. The fixtures (Step 3a)

Seven factories in `tests/conftest.py`, each `(path, width, height) -> path`,
each used by at least one test in `tests/test_cg.py`:
`png_fixture`, `photo_fixture`, `gradient_fixture`, `grayscale_fixture`,
`png16_fixture`, `profiled_fixture`, `webp_fixture`.

`tests/pixels.py` grew **four** writers, not the two the brief named. The
extra two are `write_rgba_png` (colour type 6) and `write_indexed_png`
(colour type 3), and they are there because I implemented a branch for each
and an untested branch in this layer is the whole problem: without them, the
RGBA rule and the Indexed refusal would be code nothing exercises. Both are
stdlib, deterministic, and need no external encoder — which is better than
the `magick` skip the alternative would have required. `read_ihdr` and
`read_png_grey` read the results back.

`write_grey_png`'s value rises linearly with the row, so `gradient_fixture`
identifies which SOURCE ROW came back — the property Task 3's crop tests
need. Above 256 rows the mapping stops being one-to-one; that is documented
in the writer.

## 7. Concerns

**1. Task 4 and Task 6 must call `bitmap_context`, not `CGBitmapContextCreate`.**
This is the one that matters. The plan's Task 4 and Task 6 code blocks both
construct a context inline with a hardcoded alpha value, and pasting either
one re-introduces the black-grayscale defect that this task exists to prevent.
`_resize_to_png` in Task 4 is the site the measurement document names. Task 6's
`normalize_to_srgb_png` builds its context in an sRGB space it creates itself,
so it is safe as written — but it should still go through `bitmap_context` for
uniformity, and note that **`sips --matchTo` preserves alpha** (measured on
`brush_circle_8107.png`), so Task 6 has a live decision about whether the
upscaler receives the alpha channel or pixels composited onto black.

**2. Task 4's test monkeypatches a name that no longer exists.** The plan
writes `monkeypatch.setattr(_cg._cg, "CGContextSetInterpolationQuality", …)`.
It is `_cg._CG_LIB` now.

**3. Task 3 defines `_write` and `_png_destination`, which already exist here.**
`write_png(scope, image, out_path, options=None)` is the same function with a
clearer name plus the unlink-first guard. Task 3 should call it rather than
define a second one.

**4. Deriving bits per component is a deliberate divergence I chose, and it
should be reviewed.** The alternative — hardcoding 8 — silently downconverts a
16-bit source, which is the eighth `sips` defect the design complains about;
reproducing it in the replacement seemed worse than diverging. No corpus file
is 16-bit, so it is latent either way, and Task 3's `PINNED` dictionary already
contemplates exactly this difference. But it means a 16-bit fixture in any
differential gate will diverge from `sips` **on the resample too**, not only on
crop, and the design's §2 attributes the downconversion to the pad path alone.
That sentence should widen.

**5. `Scope`'s injectable `release` replaces the real release rather than
running beside it.** That is the brief's semantics and five later tasks depend
on the name, so I kept it — but it means `test_scope_releases_on_exception`
genuinely leaks the handles it observes. It is a 64x64 image, so the cost is
nothing, and the alternative (calling both) would crash any test that hands the
scope an integer rather than a handle. Recording it rather than changing it.

**6. `write_png`'s unlink-first has no production caller yet.** It mirrors
`imaging._run(produces=)` and I believe it is right, but Task 5's
`resize_and_encode_to_file` builds its own destination and will need the same
guard explicitly.

**7. The corpus census in the design is one file out of date.** §6 says "the
corpus contains one HEIC source"; Task 0 found two, and also three WebP and one
GIF. Nothing here depends on it — both HEICs decode fine — but the design's
prose still says one.

## 8. Not done, deliberately

`upscale` untouched (Global Constraint 9). `imaging.py` untouched — nothing
imports `_cg` yet, so the import cycle the plan flags for Task 3 does not
exist and moving `ImagingError` early would be a change with no test behind it.
Tier 2 (corpus) and tier 3 (real upscaler) were not run; they are deselected by
default and this task adds no code they exercise, though the read-only spot
check in §2 used seven real corpus files.

---

# Fix round 1

**Commit:** `11a81db` — fix: three tests that passed against the behaviour they claimed to pin

**Covering tests:** `uv run pytest tests/test_cg.py tests/test_pixels.py
tests/test_imaging.py` → 131 passed. Full suite re-run once at the end: 586
passed, 25 deselected (was 576; +10).

## The three should-fixes

**`test_cg.py:414-418`, the Finalize branch.** Confirmed: the `ImagingError`
came from the destination `_checked`, and discarding
`CGImageDestinationFinalize`'s return value left all 30 tests green. I measured
four real routes to a false Finalize — a name over 255 bytes, a destination
that is a directory, a path under `/dev/null`, an unwritable directory — and
**all four fail earlier**, at `CGImageDestinationCreateWithURL`. Took both
halves of the option offered:

- `test_write_png_refuses_an_unwritable_destination` is now parametrised over
  all four routes and named for the check it actually reaches.
- `test_write_png_raises_when_finalize_refuses` forces the false return by
  monkeypatching `_cg._IO_LIB.CGImageDestinationFinalize`. Verified red against
  a version that discards the return value.

**`test_cg.py:429-437`, the unlink.** Confirmed, and it went further than the
finding: **the unlink changes no observable outcome at all**, so I removed it
rather than only renaming the test. Measured with and without, on a read-only
destination and on a stale file in a directory that becomes unwritable —
identical results both ways. In the second case the unlink was actively
*worse*: its own errno replaced `_checked`'s more specific "could not create a
PNG destination for …". The `OSError` guard went with it, since it only existed
to catch exceptions the unlink itself raised. The surviving test is renamed to
the outcome it pins, with a note that ImageIO truncates by itself.

**`_cg.py:322-323`, monochrome ignoring `has_alpha`.** Confirmed and
reproduced: source values 0, 6, 12, 18 at alpha 128 came back 0, 3, 6, 9 —
your `[0,3,5,8] → [0,1,3,4]` at a different gradient. Handled rather than
refused: **Gray + `PremultipliedLast` is a legal context and preserves the
channel**, writing colour type 4 back out, so refusing would have been
throwing away a working case. `tests/pixels.py` grows `write_grey_alpha_png`
(colour type 4) to make the branch reachable, and two tests cover it — one on
the constant, one on the colour type of a real drawn output. Both verified red
against the old `info = kCGImageAlphaNone`.

## Minors

| finding | done |
|---|---|
| `pytest.raises(RuntimeError)` also catches `ImagingError` | sentinel `_Boom`; also counts the four handles instead of asserting non-empty. Verified red against a fully broken `load` — it was green before |
| `kCGImageAlphaLast` unused | removed, and `kCGColorSpaceModelIndexed` with it — same defect, and removing one while leaving the other reads worse than either |
| `profiled_fixture` / `webp_fixture` outputs unasserted | `iCCP` present against an untagged base; `RIFF`/`WEBP` magic under the `.jpg` name |
| `imaging.py:76` docstring | rewritten; it also now warns against the `RuntimeError` trap above |
| `_cg.py:332-335` silent clamp above 16bpc | refused by name. Measured first: a 32-bit float source accepts **no** integer context — 8, 16 and 32 all return NULL — so the clamp produced a context failure that did not say the depth was why |

Left alone as instructed: the four unused declarations. Also added a note to
`_declare`'s docstring that an **undeclared** function is a segfault rather
than an error — ctypes defaults its arguments to C int and truncates the
handle. Two of my own probes died that way, on `CGImageGetBitsPerPixel` and
`CGImageGetBitmapInfo`, which is a trap Task 3 through Task 7 will each walk
toward.

## Corrections

**Mine, accepted:** mutation 4 failed four tests, not three. It now fails six,
with the two new monochrome-alpha tests.

**One correction to the review:** the module docstring's table said
`Monochrome + PremultipliedLast` "turns a grayscale PNG into a gray+alpha one,
changing the file's structure for no gain". That was measured on an opaque grey
source and generalised wrongly — for a grey source *with* alpha it is the
correct choice, which is the bug you found. The table now carries both rows,
and the asymmetry you identified is called out in the text: RGB **rejects** the
wrong value with a NULL, monochrome **accepts** it and renders the wrong
picture. That asymmetry is the whole reason this defect class is silent.

## Unchanged

Real corpus behaviour is identical after the fixes — the same seven files,
read-only, classify exactly as before: three monochrome to `AlphaNone`,
`brush_circle_8107.png` to `PremultipliedLast`, the HEIC, the interlaced PNG
and the WebP-named-`.jpg` to `NoneSkipLast`. Concerns 1, 2, 3, 4, 6 and 7 from
the original report still stand for later tasks; concern 5 (`write_png`'s
unlink) is retired, since the unlink is gone.

---

# Fix round 2

**Commit:** `2fa1c91` — fix: clear the destination when the write is refused, not before it

**Covering tests:** `uv run pytest tests/test_cg.py tests/test_pixels.py
tests/test_imaging.py` → 133 passed. Full suite once at the end: 588 passed, 25
deselected (was 586; +2).

## The stale-file path: yes, it is real

Reproduced before fixing. Two routes to a false `Finalize` with no
monkeypatching, both by sabotaging the parent directory between create and
finalize:

| route | recovery unlink | Finalize | destination after |
|---|---|---|---|
| parent removed | absent | False | gone (the directory went with it) |
| parent `chmod 0o500` | absent | False | **exists, still the stale bytes** |
| parent `chmod 0o500` | present, unguarded | — | **PermissionError, not ImagingError** |

So the finding holds exactly as described, and my round-1 reasoning was half
wrong in the half that mattered: I measured the *create* path, found the
pre-emptive delete bought nothing there, and generalised that to the whole
function without measuring the *write* path, where ImageIO actually puts the
bytes.

The third row is the wrinkle the prescription does not cover. A directory
turned read-only mid-write is what makes `Finalize` return false, and it
refuses the recovery unlink too — so a bare `unlink(missing_ok=True)` in that
branch replaces the `ImagingError` with a `PermissionError`, breaking the
one-exception-type contract in the middle of fixing something else. It is best
effort, and the message now says when the old file is still in place rather
than implying a clean destination.

Two tests, both verified red against the version without the recovery unlink:
`test_a_refused_write_does_not_leave_the_previous_file_standing` and
`test_a_refused_write_says_so_when_it_cannot_clear_the_destination`.

**Correction accepted:** my claim that all three replaced messages were worse
was overstated. `[Errno 20] Not a directory` does say more about the cause than
"could not create a PNG destination". The complaint that stands is
inconsistency, not lost information, and the report said more than the
measurement supported.

## The three small ones

| finding | done |
|---|---|
| `test_cg.py:511-513` docstring not pinned | one `assert "create a PNG destination" in str(exc.value)` across all four routes |
| `_cg.py:311-313` "renders the whole frame black" | measured: opaque grey → `0, 0, 0, 0`; grey+alpha → `0, 33, 66, 98`, composited and **not** black. Both the function docstring and the module table now carry both rows, with a line saying the composited result is wrong in a way that looks far more plausible than black |
| `_cg.py:35` float row | confirmed: every integer context is NULL at 8, 16 and 32 bpc, and 32 bpc **with `kCGBitmapFloatComponents`** accepts both `PremultipliedLast` and `NoneSkipLast`. The depth is still refused — supporting it means a float pipeline end to end — but the comment no longer claims no context exists |

## The pattern worth naming

That is three findings in two rounds of the same shape: a behaviour measured on
one source and written up as though it held for all of them. The grey+alpha
case has now caused two of them — first the `PremultipliedLast` note in round
1, then the "all black" claim in round 2 — because every convenient grey
fixture is opaque, and the alpha variant behaves differently in both
directions. The module docstring now states the measured source alongside each
result rather than the conclusion alone, which is the only thing that would
have caught any of the three at writing time.
