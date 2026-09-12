"""Locate, verify and download upscayl-bin and its model.

About 60 MB, so not committed. Everything is pinned by SHA-256: a release that
moves or a model that changes is a hard failure, not a silent quality change.

Nothing here ever leaves a half-written artifact where a finished one belongs.
A future run treats the presence of the binary as proof it is installed, so a
truncated download or an interrupted unzip would be believed rather than
noticed -- and would surface hours later as a baffling upscaler error. Both the
download and the unzip therefore write to a `.partial` beside the destination
and rename onto it only once the bytes are complete and, for downloads, hashed.
"""

import hashlib
import os
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path

UPSCAYL_RELEASE = "20251207-174704"
UPSCAYL_ZIP_URL = (
    f"https://github.com/upscayl/upscayl-ncnn/releases/download/"
    f"{UPSCAYL_RELEASE}/upscayl-bin-{UPSCAYL_RELEASE}-macos.zip"
)
UPSCAYL_ZIP_SHA256 = "277419791281a56eae0c739c70120b974d7267cf7c2de8e86dc09798d4b314db"

MODEL_NAME = "upscayl-standard-4x"
MODEL_COMMIT = "6cfaf45b2aae2847cba4f2313b57ca20a0ddd79c"
MODEL_FILES = {
    "upscayl-standard-4x.bin":
        "713ee713b0353afaa27976f0563a64a5043bd70b9bd8936c2e26e25ebcdbcddf",
    "upscayl-standard-4x.param":
        "35330ececcea33b6c397a72548e788d5d53becee4734c50b7fada36e89f10a86",
}

DATA_HOME = Path.home() / ".local" / "share" / "paperhanger"
BINARY_NAME = "upscayl-bin"
SETUP_HINT = "run `paperhanger setup` to download and verify it"


class ToolchainError(RuntimeError):
    """Something went wrong fetching or verifying the toolchain."""


class ToolchainMissing(ToolchainError):
    """The binary or model is not installed."""


def model_url(filename: str) -> str:
    return (f"https://raw.githubusercontent.com/upscayl/upscayl/"
            f"{MODEL_COMMIT}/resources/models/{filename}")


def models_dir() -> Path:
    return DATA_HOME / "models"


def bin_dir() -> Path:
    return DATA_HOME / "bin"


def sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_sha256(path, expected: str) -> bool:
    return sha256(path) == expected.lower()


def download_verified(url: str, target: Path, expected_sha256: str) -> Path:
    """Fetch `url` to `target`, then verify. A mismatch deletes the file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".partial")
    try:
        with urllib.request.urlopen(url) as response, open(partial, "wb") as out:
            shutil.copyfileobj(response, out)
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise ToolchainError(f"could not fetch {url}: {error}") from error

    if not verify_sha256(partial, expected_sha256):
        actual = sha256(partial)
        partial.unlink(missing_ok=True)
        raise ToolchainError(
            f"sha256 mismatch for {url}\n  expected {expected_sha256}\n  got      {actual}"
        )
    partial.replace(target)
    return target


def find_upscayl() -> Path:
    """The binary to run. Env var, then DATA_HOME, then PATH."""
    override = os.environ.get("PAPERHANGER_UPSCAYL_BIN")
    if override:
        path = Path(override)
        if path.is_file():
            return path
        raise ToolchainMissing(
            f"PAPERHANGER_UPSCAYL_BIN points at {path}, which is not a file")

    installed = bin_dir() / BINARY_NAME
    if installed.is_file():
        return installed

    on_path = shutil.which(BINARY_NAME)
    if on_path:
        return Path(on_path)

    raise ToolchainMissing(f"{BINARY_NAME} not found; {SETUP_HINT}")


def models_ok() -> bool:
    """Every model present and hashing to its pinned value. Never raises.

    A model that cannot be read is not ok, so the unreadable case answers False
    rather than escaping: `status` is what doctor reports with, and a bare
    PermissionError out of `ensure_ready` would replace the one message a
    first-run user needs -- the one naming `paperhanger setup`. Catching around
    the hash also closes the window between `is_file` and the `open` inside it.
    """
    directory = models_dir()
    for name, digest in MODEL_FILES.items():
        path = directory / name
        try:
            if not path.is_file() or not verify_sha256(path, digest):
                return False
        except OSError:
            return False
    return True


def find_models() -> Path:
    if not models_ok():
        raise ToolchainMissing(f"model {MODEL_NAME} missing or corrupt; {SETUP_HINT}")
    return models_dir()


def ensure_ready() -> tuple:
    """(binary, models_dir), or raise. Call BEFORE planning, not mid-batch."""
    return (find_upscayl(), find_models())


def status() -> dict:
    """What doctor reports. Never raises."""
    try:
        binary = find_upscayl()
    except ToolchainMissing:
        binary = None
    return {
        "binary": binary,
        "release": UPSCAYL_RELEASE,
        "models_dir": models_dir(),
        "models_ok": models_ok(),
        "model": MODEL_NAME,
    }


def setup(log=print) -> dict:
    """Download and verify the binary and model. Idempotent."""
    bin_dir().mkdir(parents=True, exist_ok=True)
    models_dir().mkdir(parents=True, exist_ok=True)
    binary = bin_dir() / BINARY_NAME

    if binary.is_file():
        log(f"  {BINARY_NAME}: already installed")
    else:
        with tempfile.TemporaryDirectory() as scratch:
            archive = Path(scratch) / "upscayl.zip"
            log(f"  downloading {BINARY_NAME} {UPSCAYL_RELEASE} ...")
            download_verified(UPSCAYL_ZIP_URL, archive, UPSCAYL_ZIP_SHA256)
            log("    sha256 ok")
            with zipfile.ZipFile(archive) as bundle:
                member = next(
                    (m for m in bundle.namelist() if Path(m).name == BINARY_NAME), None)
                if member is None:
                    raise ToolchainError(
                        f"{BINARY_NAME} not found inside {UPSCAYL_ZIP_URL}")
                # Unzip beside the destination and rename onto it, so an
                # interrupted extraction leaves no half-binary that the next
                # run would mistake for an installed one.
                partial = binary.with_name(binary.name + ".partial")
                try:
                    with bundle.open(member) as src, open(partial, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    partial.replace(binary)
                finally:
                    partial.unlink(missing_ok=True)
        log(f"    installed to {binary}")

    # Outside the branch: a binary that lost its +x bit is repaired by the
    # command we tell people to run, instead of reporting "already installed"
    # and failing later with a PermissionError out of the upscaler.
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    for name, digest in MODEL_FILES.items():
        target = models_dir() / name
        if target.is_file() and verify_sha256(target, digest):
            log(f"  {name}: already installed")
            continue
        log(f"  downloading {name} ...")
        download_verified(model_url(name), target, digest)
        log("    sha256 ok")

    return status()
