"""Every subprocess call the tool makes. The only module that touches images.

Seven measured facts shape this file. Each fails SILENTLY if ignored:

  1. sips -g pixelWidth exits 0 while printing `pixelWidth: <nil>` for text,
     empty and truncated files, and dies by signal on .DS_Store. Exit status is
     not a usable signal; stdout is.
  2. Crop must never be fused with a resample. `sips -c 1080 1920 --cropOffset
     0 500 --resampleWidth 960` on a 3840-wide source returns 480x270, not
     960x540 -- the resample is applied against the PRE-CROP width.
  3. Both output axes must be passed. --resampleWidth/--resampleHeight derive
     the other axis and round it inconsistently (2662x1663 at --resampleWidth
     7680 gives 7680x4798 where floor gives 4797), and a single hardcoded flag
     is simply wrong for the by-height plans.
  4. A write that sips SKIPS still exits 0. Given a path it cannot read -- one
     that does not exist, or a directory -- sips prints `Warning: <path> not a
     valid file - skipping` to stderr, exits 0, and writes no output file at
     all. That is fact 1 again on the write path: the exit status is not the
     signal. Every operation here therefore asserts its post-condition, that
     the file it was asked for actually exists afterwards. Checking the
     artifact rather than parsing the warning text also catches any other
     exit-0 no-op, whatever its wording. A corrupt file, an unwritable
     destination and an unknown format all exit 13 and are caught by the
     status check; this is only for the ones that do not.
  5. An out-of-bounds crop PADS WITH BLACK. sips neither clamps nor errors:
     a 400x200 source cropped at x=900,y=900 returns a 120x80 image that is
     entirely black, at exit 0, with the requested dimensions and a valid
     file. Neither the status check nor fact 4's post-condition can see it --
     the file exists and is exactly the size asked for. Only comparing the
     rect against the measured source catches it, which is why `crop` probes.
  6. sips SILENTLY IGNORES two --cropOffset shapes, both of which need X == 0,
     and the pure geometry layer produces both of them for real wallpapers:
       * `--cropOffset 0 0` -- the offset is dropped and sips does its DEFAULT
         CENTERED crop. Measured on 1600x1200: horizontal_thirds' TOP slice,
         asked for 1600x1000 at (0,0), comes back as the rows at y=100, which
         is the MIDDLE slice. vertical_thirds' LEFT slice, asked for 800x1200
         at (0,0), comes back as the band at x=400 -- the middle one again.
       * `--cropOffset <y> 0` with y + height == source height (flush with the
         bottom edge) -- the crop is dropped entirely and the FULL SOURCE
         comes back. Measured: horizontal_thirds' BOTTOM slice, asked for
         1600x1000 at (0,200), returns the whole 1600x1200 image.
     An x of 1 or more is correct at every y, and x == 0 is correct for every
     y strictly between those two. So two of the three desktop slices and one
     of the three phone slices were silently wrong. `crop` works around it by
     padding 1px on every side and cropping at +1, which makes x non-zero and
     the bottom edge non-flush; verified region-exact for all six slices.
  7. ARGUMENT ORDER decides whether `-s format` is honoured. Placed after
     --padColor it is silently dropped: a 2560x1600 JPEG padded with
     `-p H W --padColor FF00FF -s format png` comes back as JPEG, byte-identical
     in size to the same run with no -s format at all (3580120 B both), while
     moving -s format png in front yields PNG (11544706 B). sips warns `Output
     file suffix should be jpg` on stderr, which a zero exit discards.
     This is not cosmetic. A lossy padded intermediate puts the magenta pad
     inside the same 8x8 DCT blocks as the pixels being kept, so it bleeds into
     the crop. Measured on a uniform (20, 90, 40) region cropped out of a
     q90 JPEG: mean absolute per-channel error 63.14 at column 0 and 21.87 at
     column 1, against 0.33 in the interior, with column 0 shifted R +73.2,
     G -49.1, B +67.1 -- FF00FF's own signature, a quarter of the way to
     magenta on the outermost column. Forced to PNG the same columns measure
     0.00: a uniform region survives the round trip exactly, so the whole of
     that error is the pad. Invisible to any dimension or file-exists check.
     Both crop branches therefore put `-s format png` FIRST, and `crop`
     always emits a lossless intermediate.
     Note also that without -s format, sips keeps the SOURCE's format whatever
     the --out suffix says, so a .png filename proves nothing about the bytes.
"""

import subprocess
from pathlib import Path

SIPS = "/usr/bin/sips"
SRGB_PROFILE = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"


class ImagingError(RuntimeError):
    """An imaging operation failed.

    A sips or upscayl-bin invocation that did not succeed, and -- since the
    CoreGraphics binding layer arrived -- anything `_cg` refuses: a file
    ImageIO will not decode, a colour space or bit depth no bitmap context
    accepts, a destination that cannot be written. That layer raises this
    and nothing else on purpose, so `execute` still catches ONE type per
    photo, reports that photo failed, and carries on with the run.

    It subclasses RuntimeError for compatibility with callers that predate
    it. Tests should not use `pytest.raises(RuntimeError)` as a stand-in
    for an unrelated failure, because this satisfies it.
    """


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
    silent skip into an error -- see fact 4; a zero exit alone does not mean
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


def _properties(stdout: str) -> dict:
    values = {}
    for line in stdout.splitlines():
        key, sep, value = line.strip().partition(":")
        if sep:
            values[key.strip()] = value.strip()
    return values


def probe(path):
    """(width, height, format) for an image, or None for anything else.

    Never raises. Decides from stdout, not from the exit status -- see fact 1.
    """
    path = Path(path)
    try:
        proc = subprocess.run(
            [SIPS, "-g", "pixelWidth", "-g", "pixelHeight", "-g", "format", str(path)],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    # A negative returncode means sips was killed by a signal, which is what
    # .DS_Store does to it. There is nothing usable in stdout in that case,
    # but check explicitly so the reason is documented rather than incidental.
    if proc.returncode < 0:
        return None

    values = _properties(proc.stdout)
    try:
        width = int(values.get("pixelWidth", ""))
        height = int(values.get("pixelHeight", ""))
    except ValueError:
        return None          # '<nil>', absent, or not a number
    if width < 1 or height < 1:
        return None

    return (width, height, values.get("format", "unknown"))


def normalize_to_srgb_png(source, out_path) -> None:
    """Convert to PNG with the pixels forced into sRGB.

    Runs for EVERY source on the upscale path, not only unreadable formats.
    upscayl-bin emits PNG with no ICC chunk, so without this the encode step
    tags sRGB over unconverted numbers -- a wide-gamut round trip measures
    about 15 dB worse, an order of magnitude larger than the 33.6-36.0 dB
    spread the upscaler itself was chosen on. The corpus holds 19 Adobe RGB,
    3 ProPhoto RGB and 96 further non-sRGB profiles among 894 files, all JPEG
    or PNG, so a format-based condition would never fire for any of them.
    """
    _run([SIPS, "--matchTo", SRGB_PROFILE, "-s", "format", "png",
          str(source), "--out", str(out_path)], produces=out_path)


# The pad is always cropped away, but it is NOT harmless: on a lossy
# intermediate its DCT blocks straddle the seam and bleed into the outermost
# kept pixels -- see fact 7, which is why both branches force PNG.
PAD_COLOUR = "FF00FF"


def _offset_is_ignored(rect, source_height: int) -> bool:
    """Whether sips would silently drop this --cropOffset -- see fact 6.

    Both shapes need x == 0: the offset `0 0`, which falls back to a centered
    crop, and an offset flush with the bottom edge, which drops the crop
    altogether. Every other rect sips handles correctly.
    """
    if rect.x != 0:
        return False
    return rect.y == 0 or rect.y + rect.height == source_height


def _crop_via_padding(source, rect, out_path, width: int, height: int) -> None:
    """Crop a rect sips would otherwise ignore, by moving it off the edge.

    Padding one pixel on every side makes x == 1 and puts the bottom edge one
    row short of flush, which is outside both shapes in fact 6. sips centers
    an even pad, so the original lands at exactly (1, 1) and the rect shifts
    by the same one pixel. The padding is always cropped away; PAD_COLOUR only
    matters if this ever stops being true.

    Costs one extra full-image pass. Task 8 crops three slices from one frame
    and could pad that frame once instead of up to three times.
    """
    padded = Path(out_path).with_suffix(".padded.png")
    try:
        # -s format png FIRST: after --padColor it is silently dropped, and a
        # lossy pad bleeds magenta into the pixels we keep. See fact 7.
        _run([SIPS, "-s", "format", "png",
              "-p", str(height + 2), str(width + 2), "--padColor", PAD_COLOUR,
              str(source), "--out", str(padded)], produces=padded)
        _run([SIPS, "-s", "format", "png",
              "-c", str(rect.height), str(rect.width),
              "--cropOffset", str(rect.y + 1), str(rect.x + 1),
              str(padded), "--out", str(out_path)], produces=out_path)
    finally:
        padded.unlink(missing_ok=True)


def crop(source, rect, out_path) -> None:
    """Cut `rect` out of `source`. ALWAYS its own invocation -- see fact 2.

    Note the argument order sips wants: -c takes HEIGHT then WIDTH, and
    --cropOffset takes Y then X.

    The rect must lie entirely within the source -- see fact 5. This is the
    only layer that can check rather than assume it, because it is the only
    one holding both the rect and the image it will be cut from: the executor
    crops the 4x frame using rect.scaled(4), so an enlargement that comes back
    even a pixel short of exactly 4x overruns, and the user gets a black-edged
    wallpaper with nothing raising.

    Rects that sips would silently mis-crop go the long way round -- fact 6.

    ALWAYS writes PNG, whatever `out_path` is named, and REFUSES a name that
    says otherwise. A crop is an intermediate that something else will resize
    and encode, so spending a lossy generation on it would undo exactly what
    band 2 exists to protect -- measured at about 40.5 dB with a max channel
    error of 78 for a JPEG source.

    The suffix rule used to be a docstring asking callers to pass `.png`, and
    a docstring is not a check: handed `out.jpg` this writes PNG bytes into
    it, sips warns `Output file suffix should be jpg` on stderr, and the zero
    exit discards the warning. Every downstream reader then goes by the name.
    It is the mirror of the same trap on the way in -- without `-s format`
    sips keeps the SOURCE's format whatever the `--out` suffix says, so a
    `.png` name is no guarantee of PNG bytes either, which is why both
    branches pass `-s format png` explicitly.
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
            f"inside {width}x{height} -- sips would pad with black"
        )

    if _offset_is_ignored(rect, height):
        _crop_via_padding(source, rect, out_path, width, height)
        return

    _run([SIPS, "-s", "format", "png",
          "-c", str(rect.height), str(rect.width),
          "--cropOffset", str(rect.y), str(rect.x),
          str(source), "--out", str(out_path)], produces=out_path)


def resize_and_encode(source, out_width: int, out_height: int, fmt: str,
                      quality, out_path, resize: bool) -> None:
    """Resample (optionally) and encode, in one invocation.

    Both axes are always passed explicitly -- see fact 3. The caller computed
    them; this function does not derive anything.
    """
    argv = [SIPS]
    if resize:
        argv += ["--resampleHeightWidth", str(out_height), str(out_width)]
    argv += ["-s", "format", fmt]
    if quality is not None:
        argv += ["-s", "formatOptions", str(quality)]
    argv += [str(source), "--out", str(out_path)]
    _run(argv, produces=out_path)


def upscale(source_png, out_png, binary, models_dir,
            model: str = "upscayl-standard-4x") -> None:
    """Enlarge by exactly 4x. Input must be jpg, png or webp."""
    _run([str(binary), "-i", str(source_png), "-o", str(out_png),
          "-m", str(models_dir), "-n", model, "-s", "4"], produces=out_png)
