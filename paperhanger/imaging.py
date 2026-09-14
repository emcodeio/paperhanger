"""The only module that touches images. `_cg` for pixels, a subprocess for one
external binary, and nothing else.

Five operations, all of them thin: `probe` measures, `crop` cuts a rect,
`resize_and_encode` resamples and writes the output file in one pass,
`normalize_to_srgb_png` converts a source into the sRGB PNG the upscaler
needs, and `upscale` runs `upscayl-bin`. The first four are CoreGraphics
calls through `_cg`, in this process; only `upscale` spawns anything.

WHY THE SIGNATURES ARE UNREMARKABLE AND THE DOCSTRINGS ARE NOT. This module
replaced a set of `/usr/bin/sips` invocations, one operation at a time, each
behind an unchanged signature and each gated by a differential test that ran
the old implementation beside the new one over generated fixtures and over
the corpus. Those gates are gone -- they were retired once the last of them
had served its purpose, green, at 116 tier-1 comparisons and 8 over the
corpus sample. What the per-function docstrings below keep is the
MEASUREMENT each replacement was accepted on: which outputs were
byte-identical, which diverged and by how much, and which construction a
divergence forced. That is evidence, not history, and the design spec asks
for it to be preserved rather than deleted.

THE SEVEN FACTS THAT USED TO LIVE HERE, AND THE EIGHTH, ARE IN
`docs/research/2026-09-11-replacing-pixelmator-and-imagemagick.md`, under
"The seven measured `sips` defects, and the eighth we added". They are
statements about a tool this project no longer runs, so they are findings
rather than constraints on this code -- but the NUMBERING is unchanged and
still referenced by name from `execute.py`, from `bands.py` and from several
test docstrings, so "fact 6" resolves there and nowhere else.

Two of the eight are not retired by the move, because what they produced
outlives the tool that motivated them:

  * Fact 4's POST-CONDITION. `_run(produces=...)` still asserts that the
    file a command was asked for actually exists afterwards, and still
    clears the destination first so a stale file cannot answer for a write
    that never happened. `upscale` is its only caller now, and `upscayl-bin`
    is a binary nobody here controls: an exit-0 no-op from that one would be
    believed exactly the way `sips`' was.
  * Fact 8, the region decode, which was never a `sips` defect at all. It is
    ImageIO's, so it is still ours: a region decode of a baseline JPEG over
    1,000,000 pixels is not the whole-frame decode, and `crop` therefore
    produces pixels that a decode of the same file does not reproduce
    exactly. `tests/test_imaging.py` holds the threshold and the numbers.
"""

import subprocess
from pathlib import Path

from . import _cg
# Re-exported, not redefined. `_cg` is imported BY this module now, so the
# exception had to move down to break the cycle; this keeps
# `imaging.ImagingError` the name every existing caller already catches, and
# the same class object as `_cg.ImagingError`.
from ._cg import ImagingError                              # noqa: F401


def _tail(stream) -> str:
    """The useful end of a captured stream, however it was captured."""
    if not stream:
        return ""
    if isinstance(stream, bytes):
        stream = stream.decode("utf-8", "replace")
    return stream.strip()[:400]


def _run(argv, timeout=1800, produces=None):
    """Run a command, raising ImagingError unless it succeeded.

    `produces` is the file the command was asked to write. Passing it turns a
    silent skip into an error -- see research fact 4; a zero exit alone does
    not mean
    anything was written. The destination is deleted first, so that a stale
    file from an earlier run cannot stand in for output this run never
    produced. It must therefore name a file the command CREATES, never one of
    its own inputs.

    Everything that can go wrong leaves as ImagingError, including a missing
    binary and a timeout -- a first run with upscayl-bin not installed is a
    realistic case, and the caller should not have to catch three exception
    types to report one failure.
    """
    tool = Path(argv[0]).name
    if produces is not None:
        Path(produces).unlink(missing_ok=True)

    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ImagingError(
            f"{tool} timed out after {timeout}s: {_tail(exc.stderr)}"
        ) from exc
    except OSError as exc:
        # FileNotFoundError for a missing binary, PermissionError for one that
        # is not executable; both are OSError.
        raise ImagingError(f"{tool} could not be run: {exc}") from exc

    output = _tail(proc.stderr) or _tail(proc.stdout)
    if proc.returncode != 0:
        raise ImagingError(f"{tool} exited {proc.returncode}: {output}")
    if produces is not None and not Path(produces).exists():
        raise ImagingError(
            f"{tool} exited 0 without writing {produces}: {output}"
        )
    return proc.stdout


def _cleared(out_path) -> str:
    """Remove an earlier run's output; say so if it could not be removed.

    `_run(produces=...)` did this for every write in this module, and none of
    the three writes runs a subprocess any more. The reason is unchanged: a
    failure BEFORE anything is written -- an unreadable source above all --
    would otherwise leave an earlier run's file standing at the path this
    run was asked to produce, where anything checking for a file reads it as
    this run's.

    `resize_and_encode` and `normalize_to_srgb_png` call it. `crop` does
    NOT, and measured, it leaves a stale destination: an 8x8 PNG put at the
    out_path of a crop whose source cannot be read is still there, all 74
    bytes of it, after the raise. That is defence in depth rather than a
    live defect -- both callers in `execute` register the crop intermediate
    for deletion BEFORE calling, under a per-plan name inside a per-photo
    workdir, so no stale file of an earlier run can be at that path -- and
    it is recorded here rather than fixed on the way past, because `crop` is
    not this task's function.

    It is NOT the unlink-first `_cg._write` argues against. That one is
    about a destination ImageIO refuses, and ImageIO fails those before
    touching a file.

    AND IT IS GUARDED. A stale output inside a directory turned read-only
    raises PermissionError, which is not ImagingError and so breaks the
    one-exception-type-per-photo contract the executor is built on --
    measured, `[Errno 13] Permission denied` escaping `resize_and_encode`.
    Returns the sentence to append to whatever error follows, so the report
    says the old file survived rather than losing the reason; the empty
    string means there is nothing to add.
    """
    try:
        Path(out_path).unlink(missing_ok=True)
        return ""
    except OSError as exc:
        return (f"; an earlier file is still there and could not be "
                f"removed ({exc.strerror})")


def probe(path):
    """(width, height, format) for an image, or None for anything else.

    Never raises, for any input at all -- see `_cg.probe_file`, which is the
    whole of it now.

    NO SUBPROCESS. This was the last `sips` call in the tool, and the last
    one that could be missing: measuring is an in-process ImageIO call, so
    there is no longer a way for the imaging layer to answer "not an image"
    because a binary was absent rather than because the file was. `cli` had a
    pre-flight and a `doctor` line built entirely around that failure, and
    both are gone with it.

    What is preserved, deliberately and exactly, is the answer. Over all 894
    corpus photographs this returns the same dimensions as the `sips -g`
    parse it replaces -- zero disagreements -- and the same format string for
    every format the corpus holds, including `snowy_forest_landscape_9522.jpg`,
    which is a WebP under a `.jpg` name and was reported `webp` by both. The
    counts are not restated here: `CORPUS_FORMATS` in `tests/test_imaging.py`
    is the one place that census is ENFORCED rather than described, and a
    fifth copy of five numbers is a fifth thing to fall out of date.
    `_cg.SOURCE_FORMATS` is where the format strings themselves are pinned.

    Fact 1 above is retired as a description of THIS function and kept as a
    statement about `sips`. Its successor is narrower and stronger: there is
    no exit status to misread, because there is no process.
    """
    return _cg.probe_file(path)


def normalize_to_srgb_png(source, out_path) -> None:
    """Convert to PNG with the pixels forced into sRGB.

    Runs for EVERY source on the upscale path, not only unreadable formats.
    upscayl-bin emits PNG with no ICC chunk, so without this the encode step
    tags sRGB over unconverted numbers -- a wide-gamut round trip measures
    about 15 dB worse, an order of magnitude larger than the 33.6-36.0 dB
    spread the upscaler itself was chosen on. Of the corpus's 894 files, 19
    are Adobe RGB and 3 are ProPhoto RGB; a further 109 carry a profile that
    is not sRGB by name, `c2` being 96 of those, and 21 carry no profile at
    all. Every one of them is a JPEG or a PNG, so a condition on the FORMAT
    would fire for none of them -- which is the only thing these counts are
    here to establish. The full thirteen-bucket census is a comment beside
    the normalization tests in `tests/test_imaging.py`, and it is not
    restated here: this docstring quoted "96 further non-sRGB profiles" for
    two tasks, which is the `c2` bucket alone and not the remainder it
    read as.

    NO `sips`, and no `--matchTo`. `_cg.normalize_to_srgb_png_file` draws the
    source into an sRGB bitmap context, which is where every decision about
    the destination lives and what the divergences below are measured from.

    Four behaviours changed, all measured against the `--matchTo` reference
    while it was still retained beside this one. That comparison is retired;
    the absolute half of each row -- what THIS function produces -- is pinned
    in `tests/test_imaging.py`, and the draw it depends on in
    `tests/test_cg.py`:

      * A 16-BIT SOURCE COMES BACK 8-BIT, which is what `sips` did here too.
        This is the one place in the pipeline that narrows depth -- `crop`
        and `resize_and_encode` keep it -- because the only reader of this
        file is upscayl-bin, which emits 8-bit PNG.
      * EVERY COLOUR MODEL CONVERTS, including the indexed and CMYK sources
        a resample refuses. The destination space is ours, so the source's
        model is not a constraint here.
      * THE TWO ITU VIDEO PROFILES DIVERGE, and there this is right and
        `sips` is not: `ITU-2020.icc` and `ITU-709.icc` carry the BT.709
        OETF as a parametric rTRC, CoreGraphics follows it and `sips`
        applies a pure gamma 2.4 instead. On the neutral axis that is 29,
        25, 18 and 8 levels out of 255 at device 28, 74, 135 and 203 --
        largest in the shadows, not a constant -- and a mean absolute
        difference of 20.83 (ITU-2020) and 16.73 (ITU-709) over a noise
        image. LittleCMS agrees with CoreGraphics. No corpus file carries
        either profile.
      * AN UNREADABLE SOURCE FAILS DIFFERENTLY. It used to be `sips` exiting
        0 with `not a valid file` on stderr, or exiting 13; it is now an
        ImagingError out of ImageIO naming the file. Same exception type,
        same guarantee that no output is left behind -- which is what the
        clearing below is for, since `_run(produces=...)` no longer runs.
    """
    left_behind = _cleared(out_path)
    try:
        _cg.normalize_to_srgb_png_file(source, out_path)
    except ImagingError as exc:
        if not left_behind:
            raise
        raise ImagingError(f"{exc}{left_behind}") from exc


def crop(source, rect, out_path) -> None:
    """Cut `rect` out of `source`. Always writes PNG.

    One CoreGraphics call, and no branches. The pad-and-shift pass that used
    to stand between this and fact 6 is gone: CGImageCreateWithImageInRect
    honours an origin of 0,0 and a rect flush with the bottom edge, which are
    exactly the two shapes `sips --cropOffset` silently mis-crops. That pass
    cost a full rewrite of the image -- on the upscale path, of a frame
    already quadrupled -- to move a rect one pixel.

    The rect must lie entirely within the source, and this is the only layer
    that can check rather than assume it, because it is the only one holding
    both the rect and the image it will be cut from: the executor crops the
    4x frame using rect.scaled(4), so an enlargement that comes back even a
    pixel short of exactly 4x overruns.

    The check did not become decorative when `sips` left. It changed which
    silent wrong answer it prevents. `sips` PADDED an out-of-bounds crop with
    black at exit 0 -- fact 5. CoreGraphics INTERSECTS instead and returns the
    overlap: a 120x80 rect at x=350 of a 400x200 source comes back 50x80, and
    900x900 at the origin comes back as the whole 400x200. Either way the
    caller gets a plausible file and nothing raises, and `resize_and_encode`
    then stretches the wrong picture to the right dimensions.

    ALWAYS writes PNG, whatever `out_path` is named, and REFUSES a name that
    says otherwise. A crop is an intermediate that something else will resize
    and encode, so spending a lossy generation on it would undo exactly what
    band 2 exists to protect -- measured at about 40.5 dB with a max channel
    error of 78 for a JPEG source. The suffix rule is a check rather than a
    docstring asking callers to pass `.png`, because everything downstream
    goes by the name.

    Two behaviours changed with the implementation, both deliberately:

      * A 16-BIT SOURCE KEEPS ITS DEPTH. `sips` drops one to 8-bit on every
        path that touches pixels -- the direct crop, the crop at 0,0, the pad
        alone and a plain resample -- and only a format convert keeps 16.
        (The design attributes this to the pad and calls it the one defect
        that was ours rather than Apple's; measured, it is neither.) No
        corpus file is 16-bit, so this is latent.
      * A BASELINE JPEG OF A MEGAPIXEL OR MORE gives different pixels, because
        ImageIO decodes a REGION of one differently from the whole frame and
        the two implementations land in different places -- see fact 8.
        Neither is the better decode; the margin is a mean absolute error
        under 1.4 out of 255 on real photographs. Given the same decoded
        pixels the two agree exactly, which is what the corpus gate measures.
    """
    out_path = Path(out_path)
    if out_path.suffix.lower() != ".png":
        raise ImagingError(
            f"crop always writes PNG bytes; {out_path.name} names itself "
            f"{out_path.suffix or 'nothing'} and every reader downstream "
            f"would believe the name"
        )

    measured = probe(source)
    if measured is None:
        raise ImagingError(f"cannot crop {source}: not a readable image")
    width, height, _ = measured
    if (rect.x < 0 or rect.y < 0
            or rect.x + rect.width > width
            or rect.y + rect.height > height):
        raise ImagingError(
            f"crop {rect.width}x{rect.height}+{rect.x}+{rect.y} does not fit "
            f"inside {width}x{height} -- CoreGraphics would return the overlap"
        )

    _cg.crop_to_file(source, rect.x, rect.y, rect.width, rect.height, out_path)


def resize_and_encode(source, out_width: int, out_height: int, fmt: str,
                      quality, out_path, resize: bool) -> None:
    """Resample (optionally) and encode, in one pass.

    Both axes are always passed explicitly -- see fact 3. The caller computed
    them; this function does not derive anything. The signature is unchanged
    and `execute` is untouched; what changed is who does the work.

    NO `sips`, AND NO INTERMEDIATE. Task 4 moved the resample and left a
    lossless PNG between it and the `sips` encode; the staging file existed
    only because the two halves ran in two processes, and both halves are now
    one ImageIO destination. One decode, one frame in memory, one file
    written. It is not free: holding the frame, the resample and the encoder
    at once puts the whole-machine high-water mark 6.3% above the single
    `sips` call this replaces on a 7680x5120 reduction, 454.0 MiB against
    427.0. `_cg.resize_and_encode_to_file` has all three routes measured.

    THE DESTINATION IS CLEARED FIRST, which `_run(produces=...)` used to do
    and no longer can, because no subprocess runs. `_cleared` has the whole
    argument, including why the removal is guarded and why the guard carries
    its reason forward instead of swallowing it; `normalize_to_srgb_png`
    reached the same place in Task 6 and calls the same helper. A bare
    `except OSError: pass` here once left the caller with `could not create a
    heic destination for <path>` and no mention of the permission or of the
    earlier file still standing at that path.

    Five behaviours changed, all measured:

      * AN IDENTITY RESAMPLE IS A PASS-THROUGH. `_cg._resample` skips the
        draw when the source already has the requested dimensions, which is
        577 of the 2734 resamples a corpus run performs. It is also the case
        where drawing diverged from `sips` -- see that function for the
        counts.
      * A 16-BIT SOURCE KEEPS ITS DEPTH, where `sips --resampleHeightWidth`
        drops it to 8. The same intended divergence already pinned for
        `crop`, and no corpus file is 16-bit.
      * AN UNREADABLE SOURCE FAILS DIFFERENTLY. It used to be `sips` exiting
        0 with `not a valid file` on stderr, or exiting 13; it is now an
        ImagingError from ImageIO naming the file. Same exception type, same
        guarantee that no output is left behind.
      * THE SOURCE'S METADATA DOES NOT TRAVEL, ON ANY OF THE FOUR FORMATS.
        `sips` copies a source's EXIF into its output and ImageIO writes only
        the picture and its colour space, so a HEIC or AVIF encoded from an
        EXIF-bearing photograph comes back without the `Exif` item -- 21 of
        the 27 coverage images, at 127 to 2332 bytes each, with the coded
        picture identical in every one. JPEG is NOT exempt, though a fixture
        made it look so for a while: both tools write an APP1 Exif segment
        and only `sips` fills it from the source, so an Orientation of 6 --
        the one tag a viewer would see -- is carried by `sips` and dropped
        here. Nothing in the corpus carries a rotating orientation; the
        census and the dropped tag are in `tests/test_imaging.py`.
        The ICC profile is NOT affected: an ICC-tagged source with no EXIF
        encodes byte-identically to `sips` at heic 80, jpeg 90 and avif 85.
      * A PROGRESSIVE JPEG SOURCE COMES BACK BASELINE, which is the same
        change seen from the other side: `sips` inherits the source's scan
        structure, ImageIO writes its own, 10.2% to 21.0% larger on the four
        progressive images in the sample. Asked for progressive it produces
        `sips`' own entropy-coded scan byte for byte, so the picture is not
        merely equivalent and the choice is ours.
        `tests/test_imaging.py` pins the baseline output.
    """
    out_path = Path(out_path)
    left_behind = _cleared(out_path)
    try:
        _cg.resize_and_encode_to_file(source, out_width, out_height, fmt,
                                      quality, out_path, resize)
    except ImagingError as exc:
        if not left_behind:
            raise
        raise ImagingError(f"{exc}{left_behind}") from exc


def upscale(source_png, out_png, binary, models_dir,
            model: str = "upscayl-standard-4x") -> None:
    """Enlarge by exactly 4x. Input must be jpg, png or webp."""
    _run([str(binary), "-i", str(source_png), "-o", str(out_png),
          "-m", str(models_dir), "-n", model, "-s", "4"], produces=out_png)
