import shutil
import subprocess
from pathlib import Path

import pytest

from paperhanger import imaging
from paperhanger.geometry import Rect
from tests.pngwriter import read_png_rgb, write_marked_png, write_png


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


# ---------- operations ----------

def _profile(path):
    proc = subprocess.run(["/usr/bin/sips", "-g", "profile", str(path)],
                          capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        if "profile:" in line:
            return line.split("profile:", 1)[1].strip()
    return None


def test_crop_produces_the_exact_rect(tmp_path):
    source = write_png(tmp_path / "src.png", 1000, 800, noise=True)
    out = tmp_path / "cropped.png"
    imaging.crop(source, Rect(x=100, y=50, width=400, height=300), out)
    assert imaging.probe(out)[:2] == (400, 300)


def test_crop_offsets_land_where_asked(tmp_path):
    """sips takes -c HEIGHT WIDTH and --cropOffset Y X. Getting either order
    wrong silently crops the WRONG REGION at the right size, so dimensions
    alone cannot catch it -- a fully transposed implementation still returns
    100x100 for a 100x100 request. Hence a marker and a real pixel check.

    The rect is deliberately non-square, which catches a transposed -c, and
    deliberately off-centre, which catches a transposed --cropOffset.
    """
    marker = (240, 30, 200)
    rect = Rect(x=250, y=40, width=120, height=80)
    source = write_marked_png(tmp_path / "marked.png", 400, 200, rect,
                              base=(10, 10, 10), marker=marker)

    out = tmp_path / "region.png"
    imaging.crop(source, rect, out)

    assert imaging.probe(out)[:2] == (120, 80)
    width, height, rows = read_png_rgb(out)
    assert (width, height) == (120, 80)
    found = {pixel for row in rows for pixel in row}
    assert found == {marker}, f"crop landed off target; saw {sorted(found)}"


def test_crop_marker_check_would_fail_on_a_wrong_region(tmp_path):
    """Guards the guard: proves the marker assertion above can actually fail,
    so it is not passing because every crop happens to look uniform."""
    marker = (240, 30, 200)
    rect = Rect(x=250, y=40, width=120, height=80)
    source = write_marked_png(tmp_path / "marked.png", 400, 200, rect,
                              base=(10, 10, 10), marker=marker)

    off_by_one = tmp_path / "off.png"
    imaging.crop(source, Rect(x=249, y=40, width=120, height=80), off_by_one)
    _, _, rows = read_png_rgb(off_by_one)
    assert {pixel for row in rows for pixel in row} != {marker}


def test_fusing_crop_and_resample_is_wrong(tmp_path):
    """Global constraint 3, proven rather than asserted. The fused call scales
    by the PRE-CROP width, so it returns a quarter of the requested size."""
    source = write_png(tmp_path / "wide.png", 3840, 2160, noise=True)

    fused = tmp_path / "fused.png"
    subprocess.run(["/usr/bin/sips", "-c", "1080", "1920", "--cropOffset", "0", "500",
                    "--resampleWidth", "960", str(source), "--out", str(fused)],
                   check=True, capture_output=True)
    assert imaging.probe(fused)[:2] == (480, 270)      # NOT 960x540

    stage = tmp_path / "stage.png"
    separate = tmp_path / "separate.png"
    imaging.crop(source, Rect(x=500, y=0, width=1920, height=1080), stage)
    imaging.resize_and_encode(stage, 960, 540, "png", None, separate, resize=True)
    assert imaging.probe(separate)[:2] == (960, 540)


def test_resize_hits_both_axes_exactly_by_width(tmp_path):
    source = write_png(tmp_path / "s.png", 2662, 1663, noise=True)
    out = tmp_path / "out.png"
    imaging.resize_and_encode(source, 7680, 4797, "png", None, out, resize=True)
    assert imaging.probe(out)[:2] == (7680, 4797)


def test_resize_hits_both_axes_exactly_by_height(tmp_path):
    """A 3840x2160 desktop-by-height plan targets 4800 tall. A hardcoded
    --resampleWidth 4800 would give 4800x2700 -- below the 3200 floor."""
    source = write_png(tmp_path / "s.png", 3840, 2160, noise=True)
    out = tmp_path / "out.png"
    imaging.resize_and_encode(source, 8533, 4800, "png", None, out, resize=True)
    assert imaging.probe(out)[:2] == (8533, 4800)


def test_encode_without_resizing_preserves_dimensions(tmp_path):
    source = write_png(tmp_path / "s.png", 1234, 567, noise=True)
    out = tmp_path / "out.heic"
    imaging.resize_and_encode(source, 1234, 567, "heic", 80, out, resize=False)
    assert imaging.probe(out)[:2] == (1234, 567)


@pytest.mark.parametrize("fmt,quality,suffix", [
    ("heic", 80, ".heic"), ("jpeg", 90, ".jpg"),
    ("avif", 85, ".avif"), ("png", None, ".png"),
])
def test_every_output_format_writes(tmp_path, fmt, quality, suffix):
    source = write_png(tmp_path / "s.png", 320, 240, noise=True)
    out = tmp_path / f"out{suffix}"
    imaging.resize_and_encode(source, 320, 240, fmt, quality, out, resize=False)
    assert out.exists() and out.stat().st_size > 0
    assert imaging.probe(out)[:2] == (320, 240)


def test_normalize_converts_to_srgb_png(tmp_path, corpus):
    wide_gamut = corpus / "red_tulips_with_mountain_background_4338.jpg"
    if not wide_gamut.exists():
        pytest.skip("the Adobe RGB fixture is not present")
    local = tmp_path / wide_gamut.name
    shutil.copy2(wide_gamut, local)
    assert "Adobe RGB" in (_profile(local) or "")

    out = tmp_path / "normalized.png"
    imaging.normalize_to_srgb_png(local, out)
    assert imaging.probe(out)[2] == "png"
    assert "sRGB" in (_profile(out) or "")


def test_failure_raises_with_stderr(tmp_path):
    """Fact 4. sips does not fail here -- it exits 0, warns on stderr, and
    writes nothing. Measured: `sips -s format png missing.png --out x.png`
    exits 0 and no x.png appears. Only the post-condition catches it."""
    missing = tmp_path / "nope.png"
    out = tmp_path / "x.png"
    with pytest.raises(imaging.ImagingError) as caught:
        imaging.resize_and_encode(missing, 100, 100, "png", None,
                                  out, resize=True)
    assert "not a valid file" in str(caught.value)   # sips's own stderr
    assert not out.exists()


def test_a_directory_input_is_also_a_silent_skip(tmp_path):
    """The other exit-0 skip: a directory exists, so an input-side exists()
    check would wave it through. The post-condition is what catches both."""
    with pytest.raises(imaging.ImagingError):
        imaging.crop(tmp_path, Rect(x=0, y=0, width=10, height=10),
                     tmp_path / "out.png")


def test_a_nonzero_exit_raises_with_stderr(tmp_path):
    """The other branch of _run: a real non-image exits 13 rather than
    skipping, so the status check is still doing work."""
    junk = tmp_path / "note.txt"
    junk.write_bytes(b"this is not an image\n")
    with pytest.raises(imaging.ImagingError) as caught:
        imaging.resize_and_encode(junk, 100, 100, "png", None,
                                  tmp_path / "x.png", resize=True)
    message = str(caught.value)
    assert "exited 13" in message
    assert "Cannot extract image" in message         # sips's own stderr
