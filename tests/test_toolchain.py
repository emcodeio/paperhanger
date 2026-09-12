import hashlib
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from paperhanger import toolchain


def test_pinned_constants_match_the_spec():
    assert toolchain.UPSCAYL_RELEASE == "20251207-174704"
    assert toolchain.UPSCAYL_ZIP_SHA256 == (
        "277419791281a56eae0c739c70120b974d7267cf7c2de8e86dc09798d4b314db")
    assert toolchain.MODEL_COMMIT == "6cfaf45b2aae2847cba4f2313b57ca20a0ddd79c"
    assert toolchain.MODEL_FILES["upscayl-standard-4x.bin"] == (
        "713ee713b0353afaa27976f0563a64a5043bd70b9bd8936c2e26e25ebcdbcddf")
    assert toolchain.MODEL_FILES["upscayl-standard-4x.param"] == (
        "35330ececcea33b6c397a72548e788d5d53becee4734c50b7fada36e89f10a86")


def test_env_var_wins(tmp_path, monkeypatch):
    fake = tmp_path / "my-upscayl"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PAPERHANGER_UPSCAYL_BIN", str(fake))
    assert toolchain.find_upscayl() == fake


def test_env_var_pointing_nowhere_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERHANGER_UPSCAYL_BIN", str(tmp_path / "absent"))
    with pytest.raises(toolchain.ToolchainMissing):
        toolchain.find_upscayl()


def test_data_home_is_second(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPERHANGER_UPSCAYL_BIN", raising=False)
    binary = tmp_path / "bin" / "upscayl-bin"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    monkeypatch.setattr(toolchain, "DATA_HOME", tmp_path)
    monkeypatch.setenv("PATH", "")
    assert toolchain.find_upscayl() == binary


def test_missing_everywhere_names_the_fix(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPERHANGER_UPSCAYL_BIN", raising=False)
    monkeypatch.setattr(toolchain, "DATA_HOME", tmp_path)
    monkeypatch.setenv("PATH", "")
    with pytest.raises(toolchain.ToolchainMissing, match="paperhanger setup"):
        toolchain.find_upscayl()


def test_verify_sha256(tmp_path):
    path = tmp_path / "blob"
    path.write_bytes(b"hello")
    digest = hashlib.sha256(b"hello").hexdigest()
    assert toolchain.verify_sha256(path, digest) is True
    assert toolchain.verify_sha256(path, "0" * 64) is False


def test_download_verified_removes_a_mismatching_file(tmp_path):
    source = tmp_path / "payload.bin"
    source.write_bytes(b"the wrong bytes")
    target = tmp_path / "downloaded.bin"
    with pytest.raises(toolchain.ToolchainError, match="sha256"):
        toolchain.download_verified(source.as_uri(), target, "0" * 64)
    assert not target.exists(), "a failed download must not be left behind"


def test_download_verified_keeps_a_matching_file(tmp_path):
    source = tmp_path / "payload.bin"
    source.write_bytes(b"the right bytes")
    digest = hashlib.sha256(b"the right bytes").hexdigest()
    target = tmp_path / "downloaded.bin"
    toolchain.download_verified(source.as_uri(), target, digest)
    assert target.read_bytes() == b"the right bytes"


def test_model_urls_are_built_from_the_pinned_commit():
    url = toolchain.model_url("upscayl-standard-4x.bin")
    assert toolchain.MODEL_COMMIT in url
    assert url.endswith("/resources/models/upscayl-standard-4x.bin")


def test_status_reports_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPERHANGER_UPSCAYL_BIN", raising=False)
    monkeypatch.setattr(toolchain, "DATA_HOME", tmp_path)
    monkeypatch.setenv("PATH", "")
    report = toolchain.status()
    assert report["binary"] is None
    assert report["models_ok"] is False


def test_path_is_the_last_resort(tmp_path, monkeypatch):
    """Neither the env var nor DATA_HOME, so the third leg has to answer."""
    monkeypatch.delenv("PAPERHANGER_UPSCAYL_BIN", raising=False)
    monkeypatch.setattr(toolchain, "DATA_HOME", tmp_path / "empty")
    elsewhere = tmp_path / "somewhere" / "bin"
    elsewhere.mkdir(parents=True)
    binary = elsewhere / "upscayl-bin"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(elsewhere))
    assert toolchain.find_upscayl() == binary


def test_models_ok_is_false_for_a_present_but_truncated_model(tmp_path, monkeypatch):
    """Existence is not enough: a half-written model reads as installed."""
    monkeypatch.setattr(toolchain, "DATA_HOME", tmp_path)
    models = tmp_path / "models"
    models.mkdir()
    for name in toolchain.MODEL_FILES:
        (models / name).write_bytes(b"truncated")
    assert toolchain.models_ok() is False
    with pytest.raises(toolchain.ToolchainMissing, match="paperhanger setup"):
        toolchain.find_models()


def test_ensure_ready_raises_when_the_binary_is_missing(tmp_path, monkeypatch):
    """The check Task 12 runs before planning, so a batch fails in a second."""
    monkeypatch.delenv("PAPERHANGER_UPSCAYL_BIN", raising=False)
    monkeypatch.setattr(toolchain, "DATA_HOME", tmp_path)
    monkeypatch.setenv("PATH", "")
    with pytest.raises(toolchain.ToolchainMissing):
        toolchain.ensure_ready()


@pytest.fixture
def offline_upstream(tmp_path, monkeypatch):
    """Every pinned URL repointed at a local file, so `setup` runs with no network.

    `urllib` serves `file://` URLs, so the real download, the real hashing and
    the real unzip all run; only the host changes.
    """
    upstream = tmp_path / "upstream"
    upstream.mkdir()

    archive = upstream / "upscayl-macos.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            f"upscayl-bin-{toolchain.UPSCAYL_RELEASE}-macos/upscayl-bin",
            "#!/bin/sh\nexit 0\n",
        )
    monkeypatch.setattr(toolchain, "UPSCAYL_ZIP_URL", archive.as_uri())
    monkeypatch.setattr(toolchain, "UPSCAYL_ZIP_SHA256",
                        hashlib.sha256(archive.read_bytes()).hexdigest())

    blobs = {}
    for name in toolchain.MODEL_FILES:
        blob = upstream / name
        blob.write_bytes(f"weights for {name}".encode())
        blobs[name] = blob
    monkeypatch.setattr(toolchain, "MODEL_FILES", {
        name: hashlib.sha256(blob.read_bytes()).hexdigest()
        for name, blob in blobs.items()
    })
    monkeypatch.setattr(toolchain, "model_url", lambda name: blobs[name].as_uri())

    monkeypatch.delenv("PAPERHANGER_UPSCAYL_BIN", raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(toolchain, "DATA_HOME", tmp_path / "data")
    return tmp_path / "data"


def test_setup_installs_everything_then_re_verifies_without_downloading(
        offline_upstream, monkeypatch):
    fetched = []
    real_download = toolchain.download_verified

    def counting(url, target, digest):
        fetched.append(Path(url).name)
        return real_download(url, target, digest)

    monkeypatch.setattr(toolchain, "download_verified", counting)

    first = toolchain.setup(log=lambda *_: None)
    assert len(fetched) == 3, f"expected the zip and both model files, got {fetched}"
    assert first["models_ok"] is True

    binary = offline_upstream / "bin" / "upscayl-bin"
    assert first["binary"] == binary
    assert os.access(binary, os.X_OK), "the extracted binary has to be executable"
    assert not list(offline_upstream.rglob("*.partial")), "no scratch files left behind"

    fetched.clear()
    second = toolchain.setup(log=lambda *_: None)
    assert fetched == [], "a second run must re-verify, not re-download"
    assert second == first


def test_setup_replaces_a_model_that_went_bad(offline_upstream):
    toolchain.setup(log=lambda *_: None)
    corrupted = offline_upstream / "models" / "upscayl-standard-4x.bin"
    corrupted.write_bytes(b"truncated")
    assert toolchain.models_ok() is False

    assert toolchain.setup(log=lambda *_: None)["models_ok"] is True


def test_an_interrupted_unzip_leaves_no_half_binary(offline_upstream, monkeypatch):
    """Otherwise the next run sees a file, believes it, and fails at exec time."""
    real_copy = shutil.copyfileobj

    def die_while_extracting(source, destination, *args, **kwargs):
        if "upscayl-bin" in str(getattr(destination, "name", "")):
            raise OSError("interrupted")
        return real_copy(source, destination, *args, **kwargs)

    monkeypatch.setattr(toolchain.shutil, "copyfileobj", die_while_extracting)
    with pytest.raises(OSError):
        toolchain.setup(log=lambda *_: None)

    installed = offline_upstream / "bin"
    assert not (installed / "upscayl-bin").exists()
    assert not list(installed.glob("*.partial"))


def test_setup_rejects_an_archive_without_the_binary(offline_upstream, monkeypatch,
                                                     tmp_path):
    empty = tmp_path / "upstream" / "empty.zip"
    with zipfile.ZipFile(empty, "w") as bundle:
        bundle.writestr("README.md", "no binary here\n")
    monkeypatch.setattr(toolchain, "UPSCAYL_ZIP_URL", empty.as_uri())
    monkeypatch.setattr(toolchain, "UPSCAYL_ZIP_SHA256",
                        hashlib.sha256(empty.read_bytes()).hexdigest())

    with pytest.raises(toolchain.ToolchainError, match="upscayl-bin"):
        toolchain.setup(log=lambda *_: None)
    assert not (offline_upstream / "bin" / "upscayl-bin").exists()
