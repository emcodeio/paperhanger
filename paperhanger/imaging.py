"""Every subprocess call the tool makes. The only module that touches images.

Three measured facts shape this file. Each fails SILENTLY if ignored:

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
"""

import subprocess
from pathlib import Path

SIPS = "/usr/bin/sips"
SRGB_PROFILE = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"


class ImagingError(RuntimeError):
    """A sips or upscayl-bin invocation failed."""


def _run(argv, timeout=1800):
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise ImagingError(
            f"{Path(argv[0]).name} exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()[:400]}"
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
