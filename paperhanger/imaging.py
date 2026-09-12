"""Every subprocess call the tool makes. The only module that touches images.

Four measured facts shape this file. Each fails SILENTLY if ignored:

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
"""

import subprocess
from pathlib import Path

SIPS = "/usr/bin/sips"
SRGB_PROFILE = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"


class ImagingError(RuntimeError):
    """A sips or upscayl-bin invocation failed."""


def _run(argv, timeout=1800, produces=None):
    """Run a command, raising ImagingError unless it succeeded.

    `produces` is the file the command was asked to write. Passing it turns a
    silent skip into an error -- see fact 4; a zero exit alone does not mean
    anything was written.
    """
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    tool = Path(argv[0]).name
    output = (proc.stderr or proc.stdout).strip()[:400]
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


def crop(source, rect, out_path) -> None:
    """Cut `rect` out of `source`. ALWAYS its own invocation -- see fact 2.

    Note the argument order sips wants: -c takes HEIGHT then WIDTH, and
    --cropOffset takes Y then X.
    """
    _run([SIPS, "-c", str(rect.height), str(rect.width),
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
