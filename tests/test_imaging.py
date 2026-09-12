import shutil
import subprocess
from pathlib import Path

import pytest

from paperhanger import imaging
from tests.pngwriter import write_png


# ---------- probe ----------

def test_probe_reads_a_png(tmp_path):
    path = write_png(tmp_path / "a.png", 640, 480)
    assert imaging.probe(path) == (640, 480, "png")


def test_probe_reads_odd_dimensions(tmp_path):
    path = write_png(tmp_path / "odd.png", 1279, 801)
    width, height, _ = imaging.probe(path)
    assert (width, height) == (1279, 801)


@pytest.mark.parametrize("name,content", [
    ("note.txt", b"this is not an image\n"),
    ("empty", b""),
    ("empty.jpg", b""),
    ("empty.png", b""),
])
def test_probe_rejects_non_images_that_exit_zero(tmp_path, name, content):
    """sips exits 0 for all of these, printing 'pixelWidth: <nil>'. Exit status
    is not the signal; parsing stdout is."""
    path = tmp_path / name
    path.write_bytes(content)
    assert imaging.probe(path) is None


def test_probe_rejects_a_truncated_jpeg(tmp_path):
    # For this synthetic image, sips's SOF0 marker lands at byte 156 but sips
    # still needs data past it (measured: dims come back on this machine
    # somewhere between 700-800 bytes in, not at the marker) before it will
    # report dimensions. 100 bytes is comfortably short of that on this
    # generated fixture -- a real photo's larger header made 2000 bytes the
    # right cutoff in the brief's corpus, but is not universal, so this test
    # picks a cutoff verified against the actual fixture it truncates.
    good = write_png(tmp_path / "good.png", 400, 300, noise=True)
    jpeg = tmp_path / "full.jpg"
    subprocess.run(["/usr/bin/sips", "-s", "format", "jpeg", str(good),
                    "--out", str(jpeg)], check=True, capture_output=True)
    truncated = tmp_path / "truncated.jpg"
    truncated.write_bytes(jpeg.read_bytes()[:100])
    assert imaging.probe(truncated) is None


def test_probe_survives_a_file_that_aborts_sips(tmp_path, corpus):
    """.DS_Store makes sips die with an uncaught NSException (exit 134 in a
    shell, a negative returncode in Python). probe must not raise."""
    ds_store = corpus / ".DS_Store"
    if not ds_store.exists():
        pytest.skip("no .DS_Store in the corpus")
    local = tmp_path / "ds_store_copy"
    shutil.copy2(ds_store, local)
    assert imaging.probe(local) is None


def test_probe_reports_actual_format_not_extension(tmp_path, corpus):
    """snowy_forest_landscape_9522.jpg in the corpus is really a WebP. This is
    why normalize-or-not is decided from the probe, never from the suffix."""
    lying = corpus / "snowy_forest_landscape_9522.jpg"
    if not lying.exists():
        pytest.skip("the known extension-mismatch fixture is not present")
    local = tmp_path / lying.name
    shutil.copy2(lying, local)
    result = imaging.probe(local)
    assert result is not None
    assert result[2] == "webp"
    assert local.suffix == ".jpg"


def test_probe_of_a_missing_file_is_none(tmp_path):
    assert imaging.probe(tmp_path / "nope.png") is None


def test_probe_of_a_directory_is_none(tmp_path):
    assert imaging.probe(tmp_path) is None
