# Merge-readiness review of the CoreGraphics branch

The whole-branch review run after all eight tasks landed. Kept for the same reason as
its predecessor for the implementation branch: the mutation evidence is the strongest
statement available about what this suite actually guards, and the ownership audit of
`_cg.py` is the only line-by-line record of how the binding layer manages CoreFoundation
lifetimes.

Verdict at the time: **merge-ready with fixes.** No critical findings. The three
important ones and nine of the thirteen minors were fixed in the pass that followed
(commits `16a3744` through `4574ea1`), and a scoped re-review confirmed each. Two more
were fixed after that re-review. The rest were adjudicated as not worth changing; the
"parked findings" section below says which and why.

**Range** `103dcfa..e93779f` — 42 commits, 45 files, +11,903 / -667
**Reviewer** senior code review, session level
**Date** 2026-09-14

Paths beginning `.superpowers/` refer to the plan's scratch workspace, which was
ignored by version control and is gone. Everything it cited that outlived the branch is
in this directory or in `docs/superpowers/`.

## What I verified, and how

Everything below that says "verified" or "measured" was run in this worktree. Nothing
moved HEAD, the index, or branch state; every mutation was restored and the restore
proved by md5 plus `git status`.

| Check | Result |
|---|---|
| Tier 1 at head | **723 passed, 27 deselected, 93s** — matches the ledger |
| `ctypes` containment | `paperhanger/_cg.py` only, confirmed by grep over the package |
| `execute.py` behaviour unchanged | docstring-stripped AST md5 **identical** base↔head (`eb7acb89…`) |
| `bands.py` behaviour unchanged | docstring-stripped AST md5 **identical** (`8a9ed552…`) |
| No wallpaper image committed | 42 commits add only `.py` (39), `.md` (8), `.json` (1), `.gitignore` (1) |
| Corpus untouched | **895 entries** after every run; every use is a read or `shutil.copy2` out |
| `CFRelease(NULL)` | **exit 133, no output, no traceback** — reproduced independently |
| `CGImageRelease(NULL)` / `CGContextRelease(NULL)` | no-ops, reproduced |
| `CGImageGetWidth(NULL)` | returns 0, reproduced |
| Tree state at finish | clean, HEAD `e93779f`, both mutated files back to baseline md5 |

Eight mutations, each applied with `__pycache__` cleared and `PYTHONDONTWRITEBYTECODE=1`:

| # | Mutation | Tests red |
|---|---|---|
| M1 | `_rgb_alpha_info` hardcoded to `NoneSkipLast` (the plan's bug) | **6** |
| M2 | monochrome branch hardcoded to `NoneSkipLast` | **5** |
| M3 | identity skip in `_resample` disabled | **7** |
| M4 | `imaging.crop` bounds check disabled | **2** |
| M5 | `crop_to_file` post-condition disabled | **3** |
| M6 | both crop guards disabled | **6** (two independent tests; neither masks the other) |
| M7 | `CFRelease` removed from `Scope.__exit__` | **2** (+810 MB against a 50 MB threshold) |
| M8 | one `argtypes` declaration removed | **0 in the guard** → SIGSEGV downstream |

---

## Ownership audit of `_cg.py`

I walked all 14 acquisition sites against their releases and their raise paths.

**Every acquisition is guarded before it is owned.** `_checked` runs *inside* the
`scope.own(...)` argument list, so a NULL raises before it can enter the scope
(`_cfstr` 356, `_cfurl` 372, `load` 381/384, `bitmap_context` 673, `_destination` 679,
`_quality_options` 719/724, `normalize_to_srgb_png_file` 921/925/931, `_resample`
1042). The two sites that do *not* use `_checked` — `probe_file` 520 and 524 — use
explicit `if not …: return None` before `own`. There is no path from a NULL to a
release today.

**Kinds are right.** `image` → `CGImageRelease`, `context` → `CGContextRelease`,
everything else → `CFRelease`. `CGColorSpaceCreateWithName`'s result is owned as `cf`
(`:921`), which is correct — `CGColorSpaceRelease` is `CFRelease` for a non-NULL space.

**Release order is reverse acquisition** (`_cg.py:372-378`), so an image read back from
a context releases before the context, and a context before the colour space. This is
the safe order and it is not accidental — `test_scope_releases_in_reverse_order`
(`test_cg.py:179`) pins it.

**Raise paths release.** `crop_to_file`'s post-condition raise (`:851`), `_write`'s
Finalize raise (`:807`), `bitmap_format`'s two refusals (`:641`, `:653`) and every
`_checked` all fire inside a `with Scope()`, so `__exit__` runs.
`test_scope_releases_on_exception` (`test_cg.py:148`) pins the mechanism.

**No double ownership.** `_resample`'s identity path returns the caller's own image
without re-owning it (`:1032-1033`), documented at `:1024-1026`, and
`resize_and_encode_to_file:1099` rebinds rather than owning. The non-identity path owns
a second, genuinely new handle. Correct in both directions.

## Lifetime beyond ownership

The known bug of this shape (`c_void_p.in_dll` on the CFDictionary callbacks) is fixed
correctly at `_cg.py:726-727`. I checked the fix rather than trusting it: `in_dll`
constructs an instance that *views* the library's static memory rather than copying it,
so `addressof` of the temporary is the symbol's own address and stays valid after the
temporary is collected. The docstring's retain-count evidence (1→2 with `addressof`,
stuck at 1 with `in_dll`) is the right diagnostic and is recorded.

I looked for others of the shape and found none that bites:

- `keys`/`values` arrays at `:722-723` are locals; `CFDictionaryCreate` copies them
  under the retain callbacks during the call. Correct.
- `c_double` at `:718` — `CFNumberCreate` copies the value. Correct.
- `_SRGB_NAME` (`:174`) is a `const CFStringRef`, i.e. a pointer *variable*, so reading
  it as `c_void_p` reads the pointer. This is the case where `in_dll` is right, and the
  docstring says why it differs from the callbacks case. Correct, and checked rather
  than assumed both times — which is the reason the distinction is written down.
- `bitmap_format` returns the source's colour space as a Get (`:637`). It stays alive
  because the image that owns it is in the scope, and the context retains it anyway.
- The options dictionary outlives `Finalize` because it is owned by the same scope as
  the write (`:1103`). The docstring at `:707-711` names this dependency explicitly,
  which is the right thing to have written down.
- `create_string_buffer` in `_pystr` (`:437`) is local to the call.

## Bitmap format derivation

The derivation at `_cg.py:634-660` covers what it claims. Monochrome and RGB each
branch on `has_alpha`; everything else is refused by name with the model spelled out;
`>16` bpc is refused by name; `<8` promotes to 8. `kCGImageAlphaOnly` (7) is handled
by construction rather than by luck: `has_alpha` returns True for it, and an alpha-only
CGImage has a NULL colour space, so `_checked` at `:637` raises before the branch is
reached.

Both branches are pinned by absolute tests, which I verified survived the deletion
wave — M1 turns 6 red, M2 turns 5 red. That matters because Task 6 recorded the
`NoneSkipLast` mutation as failing "those three tests AND NOTHING ELSE", and those
three lived in `test_normalize_differential.py`, which Task 8 deleted. The coverage was
rehomed successfully.

One bounded observation: a **16-bit half-float** source would give `bitsPerComponent
16` with `kCGBitmapFloatComponents` set, so it takes the `bits = 16` integer path
rather than the `>16` refusal. Either the context is accepted and the output is a valid
16-bit integer conversion, or it returns NULL and `_checked` raises. There is no
silent-wrong path, so this is a completeness note, not a defect, and no corpus file is
close to it.

---

## Strengths

- **`paperhanger/_cg.py:349-381` — the `Scope`.** The architecture's central claim
  rests on this class and it earns it: reverse-order release, kind-tagged, fires on the
  raise path, and an injectable `release` that lets a test observe releasing without
  reaching into CoreFoundation. I audited every acquisition against it and found no
  leak and no unguarded release.
- **`paperhanger/_cg.py:337-347` — `_checked` as a single chokepoint**, and it is
  genuinely single. No call site bypasses it except the two in `probe_file` that guard
  by hand for a documented reason.
- **`paperhanger/_cg.py:452-500` — `probe_file`'s "the two early returns are not the
  same kind of guard".** I reproduced all three NULL behaviours it rests on. This is
  the best piece of writing in the branch: it explains *why* one redundant guard stays
  and one is structural, and it names the measurement for each. The `except Exception`
  at `:534` is justified by a contract three callers depend on, and the justification
  is at the point of use.
- **`paperhanger/_cg.py:726-727` — the `addressof` fix**, with the retain-count
  evidence. A latent use-after-free that produced byte-identical output at every format
  and quality, found by reading the struct rather than running a comparison.
- **`paperhanger/_cg.py:1032` — the identity skip**, and the paragraph at `:1010-1016`
  refusing to generalise it ("Do not read this condition as a general licence"), paired
  with `normalize_to_srgb_png_file:857-869` arguing the *opposite* case for the same
  condition. Two functions, one condition, opposite answers, both reasoned. That is the
  hardest kind of comment to write and the easiest to get wrong.
- **`tests/test_cg.py:242-278` — the leak test.** The only test in 723 that can see a
  missing release; verified red under M7 at +810 MB against a 50 MB threshold. The
  docstring records why the naive version passed (ImageIO decodes lazily, so 300 leaked
  undecoded handles cost 1.2 MB) and why the instrument had to change from `ru_maxrss`
  to current RSS. Both are lessons that would otherwise have to be relearned.
- **`tests/test_cg.py:756-771` — AST-parsed `ctypes` containment.** Parsed rather than
  grepped, for the stated reason that `from ctypes import CDLL` contains no "import
  ctypes". Independently confirmed.
- **`tests/test_imaging.py:1000-1071` — the region-decode backstop.** The only absolute
  claim about a sub-threshold JPEG crop, the only test that caught an injected origin
  bug the corpus gate passed green, and its DO-NOT-DELETE header carries both
  measurements. Earned.
- **`tests/test_imaging.py:277-302` and `:374-396` — marker-based region exactness**,
  run against the rects `geometry.horizontal_thirds` actually produces rather than
  rects invented to suit the code. Non-square and off-centre deliberately, to catch a
  width/height swap and an x/y swap separately, with `:304` proving the marker
  assertion can fail.
- **`tests/conftest.py:243-294` — corpus discipline.** Copy-out, with a module teardown
  that *asserts* the shared inbox is unchanged rather than trusting it, and a docstring
  explaining that a run MOVES its inputs. This is why the constraint holds rather than
  being intended.
- **`paperhanger/cli.py:166-190` — the removed pre-flight replaced by a comment
  explaining the failure class that stopped existing**, rather than deleted silently.
  The distinction between "a guard we no longer need" and "a failure that can no longer
  happen" is the right one and is stated.
- **`README.md:207` census arithmetic.** I checked it: 245+12 = 257 Orientation 1,
  123+5 = 128 EXIF-without-tag, 1 invalid, remainder 508. Correctly aggregated from the
  per-format census. Restated in the one place a reader needs it and deliberately *not*
  restated in `imaging.probe`'s docstring, which says so.

---

## Issues

### Critical

None. Nothing I found changes a pixel of output or can take the process down on any
reachable path.

### Important

**I1. `tests/test_cg.py:1004-1020` — the segfault guard accepts `restype` OR
`argtypes`, so half of the trap it exists to close is open.**

`declared.add(...)` at `:1011` fires on either attribute, so a function with a
`restype` and no `argtypes` counts as declared. Demonstrated: I removed
`_CG_LIB.CGImageGetWidth.argtypes = [c_void_p]` and

```
pytest tests/test_cg.py::test_every_framework_function_called_is_declared
  → 1 passed in 0.06s
pytest tests/test_cg.py -k load_returns_dimensions
  → exit 139 (SIGSEGV), pytest killed, no test report
```

Both halves matter. Missing `argtypes` truncates a 64-bit handle to C `int`; missing
`restype` truncates a pointer *return* the same way. The guard's whole value is
turning "pytest dies with no traceback" into "one named test says which symbol", and
`argtypes` is the half the docstring's own two example crashes were about.

*Fix:* track two sets and require membership in both.

```python
restypes, argtypes_, used = set(), set(), set()
...
target = restypes if node.attr == "restype" else argtypes_
target.add(f"{node.value.value.id}.{node.value.attr}")
...
undeclared = used - (restypes & argtypes_)
```

**I2. `paperhanger/_cg.py:364-366` — `Scope.own` accepts NULL, and the resulting
`CFRelease(NULL)` is the least diagnosable failure in the project.**

Verified independently: `Scope().own(None)` inside a `with` block exits **133 with no
stdout, no stderr and no traceback**. `CGImageRelease(NULL)` and
`CGContextRelease(NULL)` are no-ops, `CGImageGetWidth(NULL)` returns 0 — so the CF
family is the sole outlier and the whole class is prevented only by per-call-site
discipline, which I had to audit by hand to establish.

The ledger parked this as MEDIUM. I would raise it, for a reason specific to this
branch's own thesis: the argument for `_cg.py` existing is that the risky part is
contained in one file where it can be made structurally safe, and this is the one place
where safety is still a matter of remembering. The counter-argument — that a crash is
louder than a silent no-op — fails here, because the crash *is* silent.

*Fix:* one line, breaking no test.

```python
def own(self, ptr, kind="cf"):
    if not ptr:
        return ptr          # CFRelease(NULL) kills the process; see probe_file
    self._handles.append((ptr, kind))
    return ptr
```

With every call site already `_checked`, this is pure depth. If a future call site
forgets `_checked`, the outcome becomes "whatever CoreGraphics does with NULL" —
usually a NULL return caught by the next `_checked` — instead of exit 133.

**I3. `tests/differential.py:1-8` and `tests/test_differential.py:1-17` — 1,068 lines
whose stated purpose no longer exists, and whose docstrings assert a live role.**

`differential.py` opens "Compare a `sips` operation against its CoreGraphics
replacement" and "Six later gates are built on it, so what it compares is a decision
rather than a detail." `test_differential.py` opens "The harness six later gates are
built on." **Zero gates remain.** The only consumers at head are
`test_differential.py:26` (the harness's own 561-line test file, which supplies one
side with `imaging.resize_and_encode`) and `test_imaging.py:14`, which imports two
constants, `PNG_SIGNATURE` and `_format_of`.

This is not dead code in the coverage sense — the harness's own tests exercise it
thoroughly, and they are good tests. It is dead *purpose*, and the docstrings are
false in the present tense. Task 8 deleted the four differential modules and the
`_sips_*_reference` functions but kept the scaffold and its self-tests, so the
subtraction stopped one layer short. It matters here and not elsewhere because this is
the one place in the branch where the documentation-truth standard the work holds
itself to is violated at the level of a module's entire stated role, and a future
reader will go looking for six gates.

*Fix, either:* (a) rewrite both docstrings to say what the module is now — a
standard-library PNG/lossy comparison utility retained for future differentials, with
the GC10 measurements as its rationale rather than its charter; or (b) delete both
files and move `PNG_SIGNATURE` and `_format_of` into `tests/pixels.py`, which already
holds the decoders. (a) is cheap and keeps genuinely good infrastructure; (b) is the
honest end of Task 8. Resolving this also resolves the three parked Task 2 minors,
which all live inside it.

### Minor

**m1. `paperhanger/_cg.py:716` — the one asymmetry in the NULL discipline.** `key =
c_void_p.in_dll(...)` is not `_checked`, and `in_dll` raises `ValueError` rather than
`ImagingError` if a symbol is absent. The colour-space path has a backstop
(`CGColorSpaceCreateWithName(NULL)` returns NULL → `_checked`); this path does not,
because `CFDictionaryCreate` with a NULL key under `CFRetain` callbacks would crash.
Unreachable in practice — the symbols are linked or the module would not have imported
— but it is the only place the contract at `:87` ("never passes NULL into a
CoreGraphics function") rests on the linker rather than on a check.

**m2. `paperhanger/_cg.py:977` — the 16-bit RGBA row is not reproducible from this
repo, in a table whose other three rows are.** No writer in `tests/pixels.py` emits
16-bit RGBA, and the ledger records three fixtures giving three counts (179,600 /
180,238 / 179,449). The sentence "The last row is the one to read" sits three lines
below. This is the project's signature failure mode — a measurement on one fixture
written as a property of the phenomenon — presented as settled evidence inside the
table that justifies the identity skip. One clause fixes it: name the fixture, or say
the count is fixture-dependent the way the ledger does.

**m3. `tests/test_cg.py:774-787` — a universal asserted on three cases.** The docstring
says "Every failure leaves as ImagingError"; the body checks a missing file, a text
file and an indexed source, all of which route through `_checked` or `bitmap_format`.
`execute.py:483` catches `(imaging.ImagingError, OSError)` only, so a third type ends
the run rather than the photo — which is what makes this the seam's guard. The code
holds (I traced the alternatives: `in_dll`'s ValueError per m1, and a `TypeError` from
a non-numeric `quality` that `formats.quality_for` prevents). It is the test's name and
docstring that overclaim.

**m4. `CLAUDE.md:7` — "The eight defects that motivated the move" is contradicted by
the section it cites.** Research §12 says of fact 8: "it was never a `sips` defect at
all: it is ImageIO's, which means it is still ours", and it was found during Task 3,
after the move began. Seven motivated the move. The ledger parked this; I would fix it,
because CLAUDE.md is the first file every future session reads and this is the exact
misattribution two tasks were spent correcting in the specs.

**m5. `README.md:150` — the heading "## Working around sips" now titles a section whose
own second sentence says sips "is now gone from the tool entirely."** The section
describes replacing it, not working around it.

**m6. `README.md:152` — "sips survives only test-side, as an independent oracle for
dimensions, profiles and format names" is narrower than the facts.** It is also a
fixture builder (`conftest.py:142`, `:177` tag ICC profiles through `--matchTo`), a
defect demonstrator (`test_imaging.py:479`, `:542`), and the fake upscaler's resampler
(`conftest.py:437`). Six test modules call `/usr/bin/sips`. Three roles, not one.

**m7. tier 1 is not hermetic with respect to the binary the product dropped.** Every
`/usr/bin/sips` call site uses `check=True` with no `shutil.which` guard, unlike the
`magick` sites (`test_cg.py:889`, `:915`), which all skip. If Apple removes `sips`, a
suite for a tool that does not use it goes red on `CalledProcessError`. Low urgency,
trivial fix: a session-scoped `sips_or_skip` mirroring `corpus_or_skip`.

**m8. `paperhanger/imaging.py:184-186` — a restated census that does not decompose.**
"19 Adobe RGB, 3 ProPhoto RGB and 96 further non-sRGB profiles among 894 files": past
AdobeRGB and ProPhoto, the census at `tests/test_imaging.py:1488` has 96 `c2` + 9
Generic Gray + 2 Generic RGB + 1 Calibrated RGB + 1 iMac = 109 profiled, plus 21
untagged. 96 is the `c2` bucket alone. `probe`'s docstring 40 lines earlier refuses to
restate the census for precisely this reason ("a fifth copy of five numbers is a fifth
thing to fall out of date"); this one restates a differently-bucketed version of it.
Point at `CORPUS_FORMATS`' neighbour instead, or use the census's own buckets.

**m9. `tests/test_imaging.py:1032` — stale evidence count.** The DO-NOT-DELETE block
quotes "1 failed of 647"; the suite is 723 and Task 8 re-verified the same injection at
1 failed / 722 passed. Update it or date it — the rest of the branch's numbers are
scrupulous about this.

**m10. `tests/test_cg.py:756-771` — the containment check reads `ast.Import` and
`ast.ImportFrom` only,** so `__import__("ctypes")` evades it. The pattern is already
known in-repo: `conftest.IMPURE_BUILTINS` names `__import__` for exactly this reason
in the purity check. Adding `__import__`/`importlib` to the walk is a few lines.

**m11. `tests/test_imaging.py:219-220` — the tier-2 dimension test spawns 894 serial
`sips` processes** (~6 min, most of tier 2's runtime) to re-cross-check a probe the
branch already proved agrees on all 894. A thread pool gives the identical comparison.
(Ledger parked; agreed as recorded.)

**m12. `paperhanger/_cg.py:66` — "The grayscale failure only fires when the draw
SCALES."** Measured, per the ledger, it fires on *reduction* and not on enlargement.
The defect is fixed so nothing depends on it, but this project reduces in band 1 and in
band 3's reduce step, so the sentence misdescribes when it would have bitten. One word.

**m13. provenance for the docstring measurements lives in git-ignored scratch.**
`.gitignore:5` excludes `.superpowers/`, so the task reports that hold the commands,
fixtures and machine state behind roughly forty numbers in `_cg.py` and `imaging.py`
will not exist after merge. Largely mitigated — most docstrings name their own fixture
inline, which was Task 1's explicit lesson — but worth knowing that the audit trail for
the ones that do not is about to disappear. If any of those numbers matter beyond this
branch, `docs/research/` is where they survive.

---

## The parked findings

### Worse than recorded — I would raise these

- **`_cg.py:338` `Scope.own` (recorded "minor parked (MEDIUM)")** → **Important**. See
  I2. Verified exit 133 with no output at all. The one-line fix is right, and the
  reason to do it is the branch's own argument for why `_cg.py` exists.
- **`test_cg.py:987` AST declaration check accepts restype OR argtypes (recorded plain
  minor)** → **Important**. See I1. I demonstrated the hole and its consequence
  (SIGSEGV, exit 139, no test report). It is the structural defence against the only
  documented process-death class in the file, and half of it is open. Recorded as a
  four-line fix; it is, and it should be taken.
- **`_cg.py:560` (now `:977`) 16-bit RGBA not reproducible from the repo (recorded
  minor)** → stays Minor, but **fix rather than park**. See m2. The number is correct;
  the presentation re-commits the error thirteen other findings corrected, in the
  evidence table for the identity skip.
- **`CLAUDE.md:7` "eight defects" (recorded minor)** → stays Minor, but **fix rather
  than park**. See m4. It contradicts the section it points at, in the file every future
  session reads first.

### I would drop these as not worth fixing

- **`conftest.py:104` — no test resamples an interlaced source, so "our resample drops
  Adam7 where sips kept it" is unasserted.** The ledger calls this "the one real
  coverage cost of the deletion wave". I disagree that it is a cost. Interlacing is a
  container property of a PNG intermediate whose only readers are `upscayl-bin` and our
  own decoders; neither cares, and a regression in either direction is invisible
  downstream. A test here would pin a property with no consumer. Drop.
- **Task 7 M13 (`_pystr` drops `return None`).** Closable in three lines with the
  injection style already in the file, and I would take it if someone is in there
  anyway — but the branch it guards can only turn "unknown" into a slightly different
  "unknown" for a format string the ledger itself established no production caller
  reads. Not a merge gate.
- **`_cg.py:205/206` — SOURCE_FORMATS covers 15 of 62 readable types.** Drop. The
  docstring already says the fallback is reachable, says exactly what it costs (the
  format string, which nothing reads), and says extending it would mean measuring
  rather than guessing. That is the correct resting state, not an open finding.
- **Task 2's three harness minors** (`differential.py:454-456` unreachable leftover,
  `:386` sizing before the bounds check, `:243-255` `min(len)`). Do not fix in place —
  resolve them by resolving **I3**. Fixing them commits to keeping a module whose
  purpose is gone.
- **`test_differential.py:71,303` — new coupling to `imaging.resize_and_encode`.**
  Subsumed by I3.
- **`execute.py:750-753` — dangling "so the" at a line end.** Drop. Whitespace.
- **Task 0 — "residual prose precision in the measurements document after five
  rounds."** Drop. Five rounds is enough; the four questions are answered and
  independently verified, and the ruling that closed it was right.
- **`test_execute.py`'s 17 untouched `sips` mentions.** Drop, with one caveat: these are
  comments justifying live code by a tool the tool no longer has, which is the class
  Task 8's ruling 2 covered for `execute.py`, `bands.py` and `cli.py`. Nothing breaks;
  it is inconsistency in a test file. Fold into the next pass that touches it.
- **`research doc:291` omits the pad-then-crop row**, **`test_imaging.py:1010-1016`
  docstring table lists three sizes where the parametrization runs two**,
  **`conftest.py:223` still calls a tier-2 run "seven minutes of sips"**. All three are
  real and all three are one-line edits in prose. Take them in a doc pass with m4–m9;
  none is a merge gate on its own.

### Accurate as recorded, nothing to add

Task 1's two deferred minors: the "scale direction" one survives and I have restated it
as m12. The "BOTH of the other pairings … three sub-bullets" one reads correctly at head
— two pairings shown in three measured cases, and the text says "in two different ways".
Closed.

---

## The thin spots

The tests that are all that stands between a defect and a wrong wallpaper, named, with
what I measured about each.

1. **`tests/test_imaging.py:323` `test_an_out_of_bounds_crop_is_refused` + `tests/test_cg.py:710` `test_crop_to_file_refuses_a_rect_that_does_not_fit`** — and this
   pair is the single most important correction to the spec's own risk model. The spec
   says the bounds check "is the guard that has to survive… an enlargement that comes
   back a pixel short would overrun and produce a black-edged wallpaper with nothing
   raising." **That is no longer the failure mode.** With the bounds check deleted (M4)
   all three out-of-bounds rects still raise `ImagingError` — via `crop_to_file`'s
   post-condition at `_cg.py:850-856`, or via `_checked` when there is no overlap at
   all. The defence is genuinely two-deep now, and I confirmed the two do not mask each
   other in the suite: M4 → 2 red, M5 → 3 red, both gone (M6) → 6 red across two
   independent tests. What deleting the bounds check costs is the *message* ("does not
   fit inside 400x200" becomes "came back 400x200"), not the refusal. Worth updating
   the spec: under `sips` the class was "silent wrong file", and under CoreGraphics it
   is "raises, twice".
2. **`tests/test_imaging.py:1000` `test_a_region_decode_of_a_big_baseline_jpeg_is_not_the_frame_decode[1000-1000-True]`** —
   genuinely singular, and the DO-NOT-DELETE header is earned rather than defensive.
   The only absolute claim about a sub-threshold JPEG crop; the only test that caught an
   origin bug keyed on `suffix != ".png"` that the whole tier-2 corpus gate passed green
   in 5m39s. Every other JPEG-crop assertion was relative, and the corpus gate's own
   control re-decoded both sides, so a bug that fired only on an un-decoded source was
   invisible to it by construction.
3. **`tests/test_cg.py:1166` `test_the_colour_key_survives_the_identity_pass`** — the
   absolute form of the worst pixel defect the branch found: a `tRNS` chunk names a
   colour transparent, ImageIO expands it to a channel, and a 1:1 draw composites the
   keyed pixels onto black, so (255, 0, 255) returns (0, 0, 0) at the right dimensions
   and the right colour type. Largest difference the draw produces anywhere, delta 255.
   With the resize differential deleted, this plus
   `test_forcing_the_draw_at_identity_is_what_the_skip_avoids[colour-key]` are the whole
   defence. Verified: M3 turns both red.
4. **`tests/test_imaging.py:1549` `test_the_alpha_channel_survives_normalization` (3
   rows) + `tests/test_cg.py:322` `test_an_alpha_bearing_source_gets_a_premultiplied_context`** —
   the only guards on `_rgb_alpha_info`, which both `bitmap_format`'s RGB branch and
   `normalize_to_srgb_png_file` share. Twelve corpus PNGs go through this path, all of
   them band 3 or 4, so all twelve reach it on a real run. Verified: M1 turns 6 red.
5. **`tests/test_cg.py:242` `test_repeated_loads_do_not_leak` and `:733` `test_repeated_crops_do_not_leak`** —
   the only two tests in 723 that can see a missing release. Nothing else notices,
   because a leak changes no output. Verified both red under M7 at +810 MB against a
   50 MB threshold. These are irreplaceable and the loop body must keep drawing.
6. **`tests/test_cg.py:1036` `test_interpolation_is_high_not_default`** — asserts the
   *call*, not the output, because no output distinguishes High from Default today. It
   is the only thing pinning resample quality against Apple redefining Default, and by
   the nature of the problem it can never be more than a call assertion.
7. **`tests/test_cg.py:987` `test_every_framework_function_called_is_declared`** — a
   thin spot because it is half-open (I1), not because it is alone.

---

## Issues with the plan and spec, rather than the implementation

**S1. Spec §6 "Measure before implementing" is stale, and two of its stated facts were
refuted by the branch's own measurements.** All four unknowns were answered — EXIF
orientation by Task 5's census, peak memory by the preflight (the cap does not move),
HEIC input by Task 6's corpus differential, interpolation across shapes by Task 4 — yet
§6 still opens "Four things are unknown", with no dated resolution, in a document where
§2, §4 and §5 all carry `*Corrected during Task N*` notes. Worse, two of its claims are
wrong as written:

- "The corpus carries no orientation tags on any of its 894 files" — measured, 257
  carry Orientation 1 and one carries an invalid 0. What is true is the narrower and
  sufficient claim: **none rotates**.
- "The corpus contains one HEIC source" — measured **2**, per `_cg.SOURCE_FORMATS:236`,
  `CORPUS_FORMATS` at `test_imaging.py:203`, and `README.md:178`.

The design's conclusions survive both; the stated facts do not. Give §6 the same dated
resolution treatment the rest of the document gets.

**S2. Spec §8's Done list has two bullets its own execution reinterpreted, neither
carrying the correction.**

- "The differential clean over the 27-image sample, with only pinned exceptions" was
  **not met as written**: Task 3's crop gate had 14 of 27 differing, and rather than
  pinning them it built a stronger construction — hand both implementations the same
  decoded pixels and require exact agreement. §5 records that as a dated correction; §8
  does not.
- "`sips` referenced nowhere in `paperhanger/`" is **literally unmet** — I count 91
  prose mentions — and was narrowed by ruling to executable and normative references,
  of which there are now zero (`grep -rnw SIPS paperhanger/` is empty). The ledger
  states the literal criterion as unmet, which is honest; the spec should say so too.

Both bullets are defensible as executed. The issue is that the document that is the
binding authority now understates what was achieved on one and overstates it on the
other, in a branch whose recurring lesson is that a claim and its evidence must match.

**S3. GitHub issue #3's body.** The ledger flags that it still carries the refuted claim
that the 16-bit downconversion was the pad path's — ours rather than Apple's — which
Task 3 measured to be false on five separate `sips` paths. Both design specs carry the
dated correction; the issue does not. The repo is public and the issue closes on merge,
so the public record keeps the wrong attribution unless the body is edited first. I
could not verify this directly: the GitHub MCP server failed to connect this session
(`Authorization header is badly formatted`), so this is relayed from the ledger rather
than confirmed.

---

## Assessment

**Ready to merge: with fixes.**

The layering claim holds under inspection and under test — `ctypes` is confined to one
file, no CoreGraphics handle escapes it, `execute.py` and `bands.py` are provably
unchanged, and every failure I could construct arrives as `ImagingError`. The ownership
and NULL audits came back clean on every reachable path, the bitmap-format derivation
covers what it claims, and the eight mutations I ran show the 124-item subtraction cost
no coverage that matters: the plan's own alpha bug, the monochrome black-frame bug, the
identity skip, both crop guards and a missing `CFRelease` are each still killed by
absolute assertions rather than by comparisons with a retired tool.

The two Important code items are each a handful of lines in the file whose entire
purpose is to contain process-death risk, and both concern a failure mode with no
traceback — `Scope.own`'s NULL and the half-open `argtypes` guard. Take those before
merging. I3 is documentation-or-deletion and can go either way. Everything else is
prose that a single doc pass closes.
