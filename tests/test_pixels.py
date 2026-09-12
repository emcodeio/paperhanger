import subprocess

import pytest

from tests.pixels import write_png


def _sips_dimensions(path):
    proc = subprocess.run(
        ["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
        capture_output=True, text=True, check=True,
    )
    values = {}
    for line in proc.stdout.splitlines():
        if ":" in line:
            key, _, value = line.strip().partition(":")
            values[key.strip()] = value.strip()
    return int(values["pixelWidth"]), int(values["pixelHeight"])


def test_writes_exact_dimensions(tmp_path):
    path = tmp_path / "odd.png"
    write_png(path, 1279, 800)
    assert _sips_dimensions(path) == (1279, 800)


def test_writes_one_pixel_wider(tmp_path):
    path = tmp_path / "even.png"
    write_png(path, 1280, 800)
    assert _sips_dimensions(path) == (1280, 800)


def test_tiny_image(tmp_path):
    path = tmp_path / "tiny.png"
    write_png(path, 1, 1)
    assert _sips_dimensions(path) == (1, 1)


def test_noise_is_deterministic(tmp_path):
    first, second = tmp_path / "a.png", tmp_path / "b.png"
    write_png(first, 64, 64, noise=True)
    write_png(second, 64, 64, noise=True)
    assert first.read_bytes() == second.read_bytes()


def test_noise_differs_from_flat(tmp_path):
    flat, noisy = tmp_path / "flat.png", tmp_path / "noisy.png"
    write_png(flat, 64, 64)
    write_png(noisy, 64, 64, noise=True)
    assert flat.read_bytes() != noisy.read_bytes()


@pytest.mark.parametrize("width,height", [(0, 10), (10, 0), (0, 0), (-1, 10)])
def test_a_degenerate_size_is_refused(tmp_path, width, height):
    """The guard nothing exercised, and the reason it is there: IHDR happily
    encodes a zero, so without it the writer produces a file that is a valid
    PNG by structure and that no decoder will read -- and the test asking for
    it fails somewhere else entirely, holding a fixture instead of a size."""
    path = tmp_path / "degenerate.png"

    with pytest.raises(ValueError, match=">= 1"):
        write_png(path, width, height)

    assert not path.exists(), "and it refuses before writing"
