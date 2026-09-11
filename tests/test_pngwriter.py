import subprocess

from tests.pngwriter import write_png


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
