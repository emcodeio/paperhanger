import ast
import shutil
import sys
from pathlib import Path

import pytest

# Tests import `tests.pngwriter`; make the repo root importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paperhanger import toolchain                    # noqa: E402 - after sys.path

CORPUS = Path.home() / "Pictures" / "wallpaper"
SAMPLE_MANIFEST = Path(__file__).parent / "corpus_sample.txt"


@pytest.fixture
def processing_dir(tmp_path):
    """A throwaway processing tree. Never the real one."""
    return tmp_path / "processing"


def corpus_or_skip() -> Path:
    """The corpus directory, or skip. Callable from any fixture scope.

    A plain function rather than only a fixture because the Tier 2 run is
    module-scoped -- 192 MB of photographs and seven minutes of sips -- and a
    module-scoped fixture cannot depend on a function-scoped one.
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
    """
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
