import ast
import sys
from pathlib import Path

import pytest

# Tests import `tests.pngwriter`; make the repo root importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CORPUS = Path.home() / "Pictures" / "wallpaper"


@pytest.fixture
def processing_dir(tmp_path):
    """A throwaway processing tree. Never the real one."""
    return tmp_path / "processing"


@pytest.fixture
def corpus():
    """The read-only image corpus, or skip. Never written to."""
    if not CORPUS.is_dir():
        pytest.skip(f"corpus not present at {CORPUS}")
    return CORPUS


def assert_pure_module(module, allowed):
    """Assert a module imports nothing outside `allowed`.

    Parses the source rather than matching strings: `from os import path`
    contains neither "import os" nor "from pathlib", so substring checks
    miss exactly the violations that matter. `allowed` is the set of
    top-level module names this module may import, relative imports
    included by their module name.
    """
    tree = ast.parse(Path(module.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:          # from os import path / from .classify import CROP
                imported.add(node.module.split(".")[0])
            else:                    # from . import sizes
                imported.update(alias.name.split(".")[0] for alias in node.names)
    forbidden = imported - set(allowed)
    assert not forbidden, (
        f"{Path(module.__file__).name} imports {sorted(forbidden)}; "
        f"only {sorted(allowed)} allowed"
    )


FAKE_UPSCALER = r"""#!/usr/bin/env python3
# Stands in for upscayl-bin: scales 4x with sips, in milliseconds.
#
# It MUST reject anything that is not jpg/png/webp, exactly as the real binary
# does. A sips-based fake reads HEIC happily, so without this check the
# normalize step -- and the HEIC and TIFF fixtures that exist to exercise it --
# would pass whether normalization works, is inverted, or is deleted outright.
#
# It also has to WRITE the file it was asked for. imaging.upscale passes
# `produces=`, which unlinks the destination first and raises if nothing
# appears afterwards, so a stand-in that merely records its arguments does not
# stand in for the real binary at all: it fails every test that reaches it.
import subprocess, sys
from pathlib import Path

args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
source, out = Path(args["-i"]), Path(args["-o"])

probe = subprocess.run(["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight",
                        "-g", "format", str(source)], capture_output=True, text=True)
values = {}
for line in probe.stdout.splitlines():
    key, sep, value = line.strip().partition(":")
    if sep:
        values[key.strip()] = value.strip()

if values.get("format") not in ("jpeg", "png", "webp"):
    sys.stderr.write(f"fake upscayl: unsupported input format "
                     f"{values.get('format')!r}\n")
    sys.exit(1)

width, height = int(values["pixelWidth"]), int(values["pixelHeight"])
# -s format png AFTER the resample, which is the order imaging.resize_and_encode
# proved sips honours; placing it first is what fact 7 forbids only for --padColor.
subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(height * 4),
                str(width * 4), "-s", "format", "png", str(source),
                "--out", str(out)], check=True, capture_output=True)
"""


@pytest.fixture
def fake_upscaler(tmp_path, monkeypatch):
    """A 4x upscaler that costs milliseconds. Uses the same env-var seam the
    tool needs in production, so the executor is tested end to end."""
    binary = tmp_path / "fake-upscayl-bin"
    binary.write_text(FAKE_UPSCALER)
    binary.chmod(0o755)
    monkeypatch.setenv("PAPERHANGER_UPSCAYL_BIN", str(binary))
    models = tmp_path / "models"
    models.mkdir()
    return binary, models
