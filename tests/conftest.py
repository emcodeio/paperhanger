import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Tests import `tests.pixels`; make the repo root importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paperhanger import toolchain                    # noqa: E402 - after sys.path
from tests import pixels                             # noqa: E402 - after sys.path

CORPUS = Path.home() / "Pictures" / "wallpaper"
SAMPLE_MANIFEST = Path(__file__).parent / "corpus_sample.txt"
COLORSYNC_PROFILES = Path("/System/Library/ColorSync/Profiles")


@pytest.fixture
def processing_dir(tmp_path):
    """A throwaway processing tree. Never the real one."""
    return tmp_path / "processing"


# ---------------------------------------------------------------------------
# Image fixtures.
#
# Eight factories, each `(path, width, height) -> path`, so a test says the
# SHAPE of input it needs rather than the writer call that produces it. They
# live here rather than in the one test file that first wanted them because
# six tasks of the CoreGraphics migration use them and a second copy would
# drift.
#
# Three of the eight are not conveniences. `grayscale_fixture` is the only way
# in this suite to construct the input that renders entirely black through a
# naively-built bitmap context; `gradient_fixture` is the only way to tell a
# correct crop from a centred one, since both come back at the right
# dimensions and only the pixels disagree; and `interlaced_fixture` is the
# only input that reaches the differential harness's Adam7 decoder, which no
# other fixture can exercise. The six gates that once depended on that
# decoder are retired; the harness is retained for the next migration, so the
# fixture is what keeps the decoder tested rather than merely present.
# ---------------------------------------------------------------------------


@pytest.fixture
def png_fixture():
    """A plain 8-bit RGB PNG at exactly the dimensions asked for."""
    def make(path, width, height):
        return pixels.write_png(path, width, height)
    return make


@pytest.fixture
def photo_fixture():
    """An RGB PNG with high-frequency detail in it.

    Noisy rather than flat because a flat colour resamples to the same
    answer under every algorithm, including a broken one: an implementation
    that dropped interpolation entirely, or scaled by the wrong factor and
    padded, would pass a comparison of flat images. Detail is what makes a
    resample comparison mean anything.
    """
    def make(path, width, height):
        return pixels.write_png(path, width, height, noise=True)
    return make


@pytest.fixture
def gradient_fixture():
    """A greyscale PNG whose value identifies the source ROW it came from.

    For crop, which is the operation where dimensions cannot settle it: the
    `sips` centred-crop defect returns a region of exactly the requested
    size from the wrong place, so only the contents say which one it is.
    """
    def make(path, width, height):
        return pixels.write_grey_png(path, width, height)
    return make


@pytest.fixture
def grayscale_fixture():
    """A FLAT greyscale PNG: IHDR colour type 0, one value everywhere.

    The input that comes back black. Flat on purpose -- a gradient that
    returned black and a gradient that returned the wrong rows are two
    different failures, and this fixture is for the first.
    """
    def make(path, width, height, value=128):
        return pixels.write_grey_png(path, width, height,
                                     top=value, bottom=value)
    return make


@pytest.fixture
def png16_fixture():
    """A 16-bit RGB PNG, the depth the `sips` path silently drops."""
    def make(path, width, height):
        return pixels.write_png16(path, width, height)
    return make


@pytest.fixture
def interlaced_fixture():
    """An Adam7 PNG carrying exactly `png_fixture`'s pixels.

    The differential harness has to decode interlacing and then say the
    pixels agree, because one corpus source is interlaced, `sips` preserves
    that and CoreGraphics does not. Pairing this with `png_fixture` at the
    same arguments is what makes "the container differs and the image does
    not" a thing a test can state.
    """
    def make(path, width, height, noise=False):
        return pixels.write_interlaced_png(path, width, height, noise=noise)
    return make


@pytest.fixture
def profiled_fixture(tmp_path):
    """An RGB PNG tagged with a named ColorSync profile, or untagged.

    Takes `(path, width, height, profile)`, where `profile` is a filename
    under /System/Library/ColorSync/Profiles and None returns the untagged
    base image for comparison.

    Shells out to `sips --matchTo` deliberately. Tagging a file with a real
    ICC profile needs a real colour-management implementation, this is
    test-only, and Global Constraint 1 governs `paperhanger/`, not `tests/`.
    When Task 8 removes `sips` from the project, this is the one test-side
    use that may remain -- it is noted here rather than deleted.
    """
    def make(path, width, height, profile):
        path = Path(path)
        if profile is None:
            return pixels.write_png(path, width, height, noise=True)
        icc = COLORSYNC_PROFILES / profile
        if not icc.is_file():
            pytest.skip(f"colour profile not installed: {icc}")
        base = pixels.write_png(tmp_path / f"untagged-{path.name}",
                                width, height, noise=True)
        subprocess.run(
            [sips_or_skip(), "--matchTo", str(icc), "-s", "format", "png",
             str(base), "--out", str(path)],
            check=True, capture_output=True,
        )
        return path
    return make


@pytest.fixture
def cmyk_fixture(tmp_path):
    """A four-channel CMYK JPEG: a colour model no bitmap context accepts.

    `bitmap_format` refuses indexed, CMYK, Lab and anything above 16 bits per
    component, and CMYK is the member of that class a real photo folder could
    plausibly hold -- four-channel JPEGs come out of print workflows, and
    `sips` resampled one without complaint. No corpus file is CMYK today, so
    this is the only way to reach that branch with a realistic input.

    Shells out to `sips --matchTo` for the same reason `profiled_fixture`
    does: making a genuinely CMYK file needs a real colour-management
    implementation, and nothing in the standard library has one. JPEG rather
    than PNG because PNG cannot carry CMYK at all.

    It then ASSERTS that what came back really is CMYK. A fixture that
    quietly produced RGB would leave every test using it passing while
    testing nothing, which is this project's signature failure.
    """
    def make(path, width, height):
        icc = COLORSYNC_PROFILES / "Generic CMYK Profile.icc"
        if not icc.is_file():
            pytest.skip(f"colour profile not installed: {icc}")
        path = Path(path)
        base = pixels.write_png(tmp_path / f"cmyk-source-{path.name}.png",
                                width, height, noise=True)
        subprocess.run(
            [sips_or_skip(), "--matchTo", str(icc), "-s", "format", "jpeg",
             str(base), "--out", str(path)],
            check=True, capture_output=True,
        )
        probe = subprocess.run(
            [sips_or_skip(), "-g", "space", "-g", "samplesPerPixel", str(path)],
            capture_output=True, text=True,
        )
        assert "CMYK" in probe.stdout and "samplesPerPixel: 4" in probe.stdout, (
            f"the CMYK fixture came back as {probe.stdout.strip()!r}; every "
            f"test using it would pass against an RGB file"
        )
        return path
    return make


@pytest.fixture
def webp_fixture(tmp_path):
    """A real WebP written under whatever name the caller gives it.

    Usually a `.jpg` one, because the corpus really contains such a file --
    `snowy_forest_landscape_9522.jpg` is a WebP -- and `probe` has to report
    the format that is in the bytes rather than the one in the name. The
    `webp:` prefix is what forces the encoder regardless of the suffix;
    without it `magick` picks the format from the extension and the fixture
    would quietly produce exactly the file it exists to rule out.

    Shells out to `magick` for the same reason `profiled_fixture` shells out
    to `sips`: it needs a real encoder, and nothing in the standard library
    writes WebP.
    """
    def make(path, width, height):
        if shutil.which("magick") is None:
            pytest.skip("ImageMagick (`magick`) is not installed")
        source = pixels.write_png(tmp_path / f"webp-source-{Path(path).name}.png",
                                  width, height, noise=True)
        subprocess.run(["magick", str(source), f"webp:{path}"],
                       check=True, capture_output=True)
        return Path(path)
    return make


SIPS = Path("/usr/bin/sips")


def sips_or_skip() -> str:
    """`/usr/bin/sips`, or skip. Callable from any fixture scope.

    THE TOOL NO LONGER USES IT, which is the whole reason for the guard: a
    suite for a tool that dropped a binary should not go red because Apple
    dropped it too. Every site here would otherwise raise
    `CalledProcessError` from `check=True`, or read an empty stdout, and
    report a missing dependency as a failing assertion about imaging.

    The `magick` sites already skip this way (`test_cg.py`,
    `webp_fixture`), and `sips` not doing so was an oversight rather than a
    decision -- `magick` is a package a machine may not have installed, and
    `sips` was on every Mac, which made the asymmetry easy to miss.

    A plain function as well as a fixture for the same reason
    `corpus_or_skip` is one: the module-scoped Tier 2 fixtures and the
    factory closures inside function-scoped fixtures both need it, and
    neither can depend on a function-scoped fixture. Returns the path as a
    string, so a call site reads `[sips_or_skip(), "-g", ...]`.

    It does NOT cover `FAKE_UPSCALER`, which runs `sips` in a child process
    of its own; `install_fake_upscaler` skips for it instead.
    """
    if not SIPS.exists():
        pytest.skip(f"{SIPS} is not present on this machine")
    return str(SIPS)


@pytest.fixture(scope="session")
def sips() -> str:
    """`/usr/bin/sips`, or skip. Test-side only; nothing in the tool runs it."""
    return sips_or_skip()


def corpus_or_skip() -> Path:
    """The corpus directory, or skip. Callable from any fixture scope.

    A plain function rather than only a fixture because the Tier 2 run is
    module-scoped -- 192 MB of photographs and five to six minutes of work,
    4:58 and 5:52 on two runs of 2026-09-14 -- and a module-scoped fixture
    cannot depend on a function-scoped one. It is no longer "minutes of
    sips": the 894-file `sips` cross-check is 2.4 s of that, and what costs
    the minutes is the sample run itself.
    """
    if not CORPUS.is_dir():
        pytest.skip(f"corpus not present at {CORPUS}")
    return CORPUS


@pytest.fixture
def corpus():
    """The read-only image corpus, or skip. Never written to."""
    return corpus_or_skip()


def sample_names() -> list:
    """The 27 coverage filenames, in manifest order."""
    return [line.strip() for line in SAMPLE_MANIFEST.read_text().splitlines()
            if line.strip() and not line.startswith("#")]


def copy_sample(corpus, into: Path) -> Path:
    """Copy the sample OUT of the corpus into `into`, or skip.

    Copied rather than used in place because a run MOVES its inputs: pointed
    at the corpus, one `paperhanger` invocation would relocate the author's
    photographs into a processing directory. Nothing in the test suite is
    allowed to write to `CORPUS`, and copying is the measure that makes that
    true rather than intended.

    Skips rather than fails when an image has been renamed or deleted: the
    sample is a list of filenames in a folder no test controls, and a machine
    missing one of them has nothing to say about this codebase.
    """
    into.mkdir(parents=True, exist_ok=True)
    missing = []
    for name in sample_names():
        source = Path(corpus) / name
        if not source.exists():
            missing.append(name)
            continue
        shutil.copy2(source, into / name)
    if missing:
        pytest.skip(f"{len(missing)} sample image(s) missing from the corpus: "
                    f"{', '.join(missing[:3])}")
    return into


@pytest.fixture(scope="module")
def corpus_sample(tmp_path_factory):
    """The 27 coverage images, copied out. READ ONLY, and shared.

    Module-scoped because copying 192 MB of photographs per test buys nothing:
    every test that takes this fixture probes and plans, and planning is pure
    arithmetic that writes nothing. Anything that RUNS the tool takes its own
    copy -- see `sample_run` in test_corpus_sample.py -- because a run empties
    the directory it was given.

    The teardown says so rather than trusting it. One test pointing the CLI at
    this directory would MOVE the originals out of it, and every test after it
    in the module would then fail for a reason that has nothing to do with
    what it was testing -- with the ordering deciding who gets blamed.
    """
    inbox = copy_sample(corpus_or_skip(),
                        tmp_path_factory.mktemp("corpus-sample") / "inbox")
    before = sorted(path.name for path in inbox.iterdir())
    yield inbox
    assert sorted(path.name for path in inbox.iterdir()) == before, (
        f"{inbox} is shared and read only, and something in this module "
        f"changed it -- a run was pointed at it, most likely. Take a copy."
    )


# Builtins that reach outside the process without importing anything. `open`
# is the one a pure module acquires by habit -- it needs no import, so the
# import check below cannot see it. The other three are how an import is
# smuggled past that check: `__import__('os').listdir(p)` passed the original
# helper unremarked.
IMPURE_BUILTINS = frozenset({"open", "__import__", "eval", "exec", "compile"})

# Method names that only ever mean I/O. Named individually rather than by
# denying their module, because `pathlib` IS allowed in the decision layer --
# for path ARITHMETIC. `/`, `.parent`, `.stem`, `.suffix`, `.name` and
# `.with_name` build a destination without asking a disk anything; every name
# below asks. `Path(p).exists()` is the specific violation spec section 3
# contemplated putting in the planner, and the import check waves it through
# because `pathlib` is on the allow-list.
#
# Matched on the attribute NAME alone, so it fires whatever the receiver is
# called. That is deliberate: an alias (`P = Path`), a parameter, or a value
# returned from elsewhere all read the same way at this layer, and a purity
# check that only recognised one spelling would be the string-matching
# mistake this helper was written to avoid.
#
# `replace` is NOT here, though `Path.replace` renames: `str.replace` is a
# pure operation a decision module may legitimately want, and a check that
# cried wolf would be turned off rather than fixed.
IMPURE_METHODS = frozenset({
    # pathlib, reading
    "exists", "is_file", "is_dir", "is_symlink", "is_mount", "samefile",
    "stat", "lstat", "owner", "group", "iterdir", "glob", "rglob", "walk",
    "read_text", "read_bytes", "readlink", "resolve", "absolute", "cwd",
    "home", "expanduser",
    # pathlib, writing
    "open", "write_text", "write_bytes", "mkdir", "rmdir", "touch", "unlink",
    "rename", "symlink_to", "hardlink_to", "chmod", "lchmod",
    # subprocess and os, for the same reason
    "run", "Popen", "call", "check_call", "check_output", "communicate",
    "system", "popen", "listdir", "scandir", "getcwd", "remove",
})


def _impure_calls(tree) -> set:
    """Names called in `tree` that can only mean the filesystem or a process.

    Calls, not bare attribute access: `p.name` and `p.stem` are arithmetic on
    a path object and must stay legal, while `p.exists()` is a stat. The
    distinction is the parenthesis, so that is what this looks for.
    """
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if isinstance(callee, ast.Name) and callee.id in IMPURE_BUILTINS:
            found.add(f"{callee.id}()")
        elif isinstance(callee, ast.Attribute) and callee.attr in IMPURE_METHODS:
            found.add(f".{callee.attr}()")
    return found


def assert_pure_module(module, allowed):
    """Assert a module neither imports nor CALLS its way out of the process.

    Two checks, because the import check alone proved to pass every realistic
    violation. Measured against synthetic modules: `from pathlib import Path`
    plus `Path(p).exists()` and `Path(p).read_text()` passed under `plan.py`'s
    own allow-list; builtin `open(p).read()` passed; `__import__('os')` passed.
    Only a stray `import subprocess` was caught. The import half is what the
    helper advertised; the call half is what makes the advertisement true.

      * IMPORTS. Parsed rather than string-matched: `from os import path`
        contains neither "import os" nor "from pathlib", so a substring check
        misses exactly the violations that matter. `allowed` is the set of
        top-level module names this module may import, relative imports
        included by their module name.
      * CALLS. `pathlib` is on the decision layer's allow-list for path
        arithmetic, so the import check cannot distinguish building a
        destination from stat-ing one. See IMPURE_METHODS and IMPURE_BUILTINS.

    Neither half is a sandbox and neither is claimed to be one. A module
    determined to reach the disk can still do it -- `getattr(Path(p),
    "exi" + "sts")()` defeats this in one line. What it catches is the
    accident: the ordinary, readable filesystem call written by someone who
    did not know this layer was meant to be pure.
    """
    source = Path(module.__file__).read_text()
    tree = ast.parse(source)

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

    impure = _impure_calls(tree)
    assert not impure, (
        f"{Path(module.__file__).name} calls {sorted(impure)}, which touches "
        f"the filesystem or a subprocess; this module is asserted pure"
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


def install_fake_upscaler(directory: Path, monkeypatch):
    """Write the stub into `directory` and point the env seam at it.

    A function as well as a fixture because the Tier 2 run is module-scoped
    and cannot take a function-scoped fixture. One copy of the stub, reachable
    from either scope: a second copy would drift from the format rule above,
    which is the only thing making the normalize step testable at all.

    Skips if `sips` is gone, because the stub resamples with it in a child
    process of its own where `sips_or_skip` cannot reach. Without this the
    stub would exit non-zero and every test that reaches the upscale path
    would report a failure about upscaling.
    """
    sips_or_skip()
    binary = directory / "fake-upscayl-bin"
    binary.write_text(FAKE_UPSCALER)
    binary.chmod(0o755)
    monkeypatch.setenv("PAPERHANGER_UPSCAYL_BIN", str(binary))
    models = directory / "models"
    models.mkdir(exist_ok=True)
    return binary, models


@pytest.fixture
def fake_upscaler(tmp_path, monkeypatch):
    """A 4x upscaler that costs milliseconds. Uses the same env-var seam the
    tool needs in production, so the executor is tested end to end."""
    return install_fake_upscaler(tmp_path, monkeypatch)


def install_fake_models(models: Path, monkeypatch) -> None:
    """Make `toolchain.ensure_ready` accept `models`, whatever is on disk.

    Only the MODEL half is faked, and only because it cannot be faked
    honestly: `toolchain.find_models` hashes both files against pinned
    SHA-256s, and the real pair is 60 MB that the test suite does not
    download. The binary is still resolved by the real `find_upscayl` through
    the same PAPERHANGER_UPSCAYL_BIN seam production uses, and whatever this
    returns has to reach `execute.Context` for the run to produce anything at
    all -- so the wiring under test is not the part being stubbed.
    """
    monkeypatch.setattr(toolchain, "ensure_ready",
                        lambda: (toolchain.find_upscayl(), models))


@pytest.fixture
def ready_toolchain(fake_upscaler, monkeypatch):
    """upscayl-bin installed, its model present and verified."""
    _binary, models = fake_upscaler
    install_fake_models(models, monkeypatch)
    return models
