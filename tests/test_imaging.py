import shutil
import subprocess
from pathlib import Path

import pytest

from paperhanger import geometry, imaging
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


@pytest.mark.parametrize("rect,why", [
    (Rect(x=350, y=40, width=120, height=80), "overruns the right edge"),
    (Rect(x=900, y=900, width=120, height=80), "lies wholly outside"),
    (Rect(x=0, y=0, width=900, height=900), "is larger than the source"),
])
def test_an_out_of_bounds_crop_is_refused(tmp_path, rect, why):
    """Fact 5. sips does not clamp and does not error -- it PADS WITH BLACK.

    Measured on this 400x200 source: every rect below comes back at exit 0,
    with exactly the requested dimensions, as a valid file. The fact 4
    post-condition cannot help, because the file really is there. Only
    measuring the source and comparing catches it.
    """
    source = write_marked_png(tmp_path / "m.png", 400, 200,
                              Rect(x=250, y=40, width=120, height=80))
    out = tmp_path / "out.png"
    with pytest.raises(imaging.ImagingError) as caught:
        imaging.crop(source, rect, out)
    assert "400x200" in str(caught.value)
    assert not out.exists(), f"a rect that {why} still produced a file"


def test_the_bounds_guard_allows_a_rect_flush_with_the_edges(tmp_path):
    """The guard must use > and not >=. geometry.horizontal_thirds puts its
    bottom slice flush against the bottom edge and every slice flush against
    both side edges, so an off-by-one here would refuse every real crop."""
    marker = (240, 30, 200)
    flush = Rect(x=280, y=120, width=120, height=80)   # 280+120 == 400, 120+80 == 200
    source = write_marked_png(tmp_path / "m.png", 400, 200, flush, marker=marker)
    out = tmp_path / "out.png"
    imaging.crop(source, flush, out)
    assert imaging.probe(out)[:2] == (120, 80)
    _, _, rows = read_png_rgb(out)
    assert {pixel for row in rows for pixel in row} == {marker}


def _crop_lands_on_the_marker(tmp_path, source_w, source_h, rect, tag):
    """Put the marker exactly at `rect`, crop `rect`, and report whether the
    pixels that came back are the marked ones. Dimensions cannot answer this:
    sips returns the right SIZE from the wrong PLACE -- see fact 6."""
    marker = (240, 30, 200)
    source = write_marked_png(tmp_path / f"src_{tag}.png", source_w, source_h,
                              rect, marker=marker)
    out = tmp_path / f"out_{tag}.png"
    imaging.crop(source, rect, out)
    if imaging.probe(out)[:2] != (rect.width, rect.height):
        return False
    _, _, rows = read_png_rgb(out)
    return {pixel for row in rows for pixel in row} == {marker}


@pytest.mark.parametrize("slicer", ["horizontal_thirds", "vertical_thirds"])
def test_every_real_geometry_slice_crops_the_region_it_asked_for(tmp_path, slicer):
    """Fact 6, against the actual producer of rects rather than rects invented
    to suit the code. Two of the three horizontal slices and one of the three
    vertical ones sit on a --cropOffset shape sips silently ignores, so before
    the padding workaround this failed for slices 0 and 2 horizontally and
    slice 0 vertically -- each returning a plausible image of the right size
    from the wrong part of the picture."""
    width, height = 1600, 1200
    for index, rect in enumerate(getattr(geometry, slicer)(width, height)):
        assert _crop_lands_on_the_marker(tmp_path, width, height, rect,
                                         f"{slicer}{index}"), \
            f"{slicer}[{index}] {rect} cropped the wrong region"


@pytest.mark.parametrize("rect", [
    Rect(x=0, y=0, width=300, height=80),      # offset 0 0 -> centered crop
    Rect(x=0, y=120, width=300, height=80),    # flush bottom -> no crop at all
    Rect(x=0, y=120, width=400, height=80),    # flush bottom, full width
    Rect(x=0, y=40, width=300, height=80),     # the shape sips gets right
    Rect(x=1, y=120, width=300, height=80),    # x=1 rescues the flush case
])
def test_crop_is_region_exact_on_and_off_the_bad_offsets(tmp_path, rect):
    """The boundary either side of fact 6, so the workaround is shown to fix
    the broken shapes without disturbing the ones that already worked."""
    assert _crop_lands_on_the_marker(tmp_path, 400, 200, rect, "b")


def _edge_error(rows, colour, index):
    """Mean absolute per-channel error of column `index` against `colour`."""
    column = [row[index] for row in rows]
    return sum(abs(p[c] - colour[c]) for p in column for c in range(3)) / (len(column) * 3)


@pytest.mark.parametrize("rect", [
    Rect(x=0, y=0, width=400, height=300),      # padded branch (offset 0 0)
    Rect(x=0, y=300, width=400, height=300),    # padded branch (flush bottom)
    Rect(x=100, y=100, width=400, height=300),  # direct branch
])
def test_cropping_a_lossy_source_does_not_bleed_the_pad_in(tmp_path, rect):
    """Fact 7. Every other crop fixture here is a PNG, so the suite was blind to
    this: the pad step's `-s format png` sat after --padColor, where sips drops
    it, and the magenta pad shared 8x8 DCT blocks with the pixels being kept.

    Measured before the fix, on the offset-0-0 rect: column 0 mean absolute
    error 9.34 and column 1 8.20 against 1.43 in the interior, with column 0
    shifted R +4.5, G -7.5, B +15.3 -- FF00FF's own signature. The interior
    figure is ordinary JPEG noise and is the right thing to compare against,
    so the edges are required to be no worse than the middle.
    """
    # A UNIFORM source, deliberately: a marked one has a hard colour boundary
    # at the rect edge, and JPEG rings against that boundary in the source
    # itself, which is real compression noise rather than anything crop did.
    # Uniform green leaves the magenta pad as the only thing that could move
    # an edge pixel, and it is far from FF00FF in every channel.
    colour = (20, 90, 40)
    source_png = write_png(tmp_path / "m.png", 800, 600, colour=colour)
    source_jpg = tmp_path / "m.jpg"
    imaging.resize_and_encode(source_png, 800, 600, "jpeg", 90, source_jpg,
                              resize=False)

    out = tmp_path / "crop.png"
    imaging.crop(source_jpg, rect, out)
    _, _, rows = read_png_rgb(out)

    interior = _edge_error(rows, colour, rect.width // 2)
    for index in (0, 1, 2):
        assert _edge_error(rows, colour, index) <= interior + 2.0, (
            f"column {index} is further from {colour} than the interior; "
            "the pad is bleeding in"
        )


def test_crop_always_writes_png_even_from_a_lossy_source(tmp_path):
    """Fact 7's other half. Without `-s format` sips keeps the SOURCE's format
    whatever the --out suffix says, so before the fix crop wrote JPEG bytes into
    a file named .png and quietly spent a lossy generation on an intermediate.
    probe reads the content, not the name, which is the only way to see it."""
    marked = write_marked_png(tmp_path / "m.png", 800, 600,
                              Rect(x=0, y=0, width=400, height=300))
    source_jpg = tmp_path / "m.jpg"
    imaging.resize_and_encode(marked, 800, 600, "jpeg", 90, source_jpg,
                              resize=False)
    assert imaging.probe(source_jpg)[2] == "jpeg"

    for tag, rect in [("padded", Rect(x=0, y=0, width=400, height=300)),
                      ("direct", Rect(x=100, y=100, width=400, height=300))]:
        out = tmp_path / f"{tag}.png"
        imaging.crop(source_jpg, rect, out)
        assert imaging.probe(out)[2] == "png", f"{tag} branch did not write PNG"


def test_the_padded_intermediate_is_really_png(tmp_path, monkeypatch):
    """Guards the argument order directly. The intermediate is deleted in a
    finally, so catch it mid-flight: if `-s format png` ever drifts back behind
    --padColor this fails, rather than silently degrading edge pixels."""
    seen = {}
    real_run = imaging._run

    def spy(argv, **kwargs):
        result = real_run(argv, **kwargs)
        produces = kwargs.get("produces")
        if produces is not None and str(produces).endswith(".padded.png"):
            seen["format"] = imaging.probe(produces)[2]
        return result

    monkeypatch.setattr(imaging, "_run", spy)

    marked = write_marked_png(tmp_path / "m.png", 800, 600,
                              Rect(x=0, y=0, width=400, height=300))
    source_jpg = tmp_path / "m.jpg"
    imaging.resize_and_encode(marked, 800, 600, "jpeg", 90, source_jpg,
                              resize=False)
    imaging.crop(source_jpg, Rect(x=0, y=0, width=400, height=300),
                 tmp_path / "out.png")
    assert seen.get("format") == "png", f"padded intermediate was {seen}"


def test_raw_sips_still_has_the_bug_the_workaround_exists_for(tmp_path):
    """A canary on the defect itself, calling sips directly. If Apple ever
    fixes this, this test fails and the padding pass can be deleted -- which
    is worth knowing, because it costs a full extra pass per affected crop."""
    marker = (240, 30, 200)
    rect = Rect(x=0, y=120, width=300, height=80)      # flush with the bottom
    source = write_marked_png(tmp_path / "m.png", 400, 200, rect, marker=marker)
    out = tmp_path / "raw.png"
    subprocess.run(["/usr/bin/sips", "-c", "80", "300", "--cropOffset", "120", "0",
                    str(source), "--out", str(out)], check=True, capture_output=True)
    assert imaging.probe(out)[:2] == (400, 200), "sips now honours the offset"


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


def test_resize_false_does_not_resample_even_when_dimensions_differ(tmp_path):
    """resize=False must mean "do not resample", not "resample to whatever you
    were handed". The realistic bands 2 and 4 call passes the source's own
    dimensions, which cannot tell those two readings apart -- an always-resample
    bug would still return 1234x567 above. Passing dimensions that differ is
    what makes the flag observable."""
    source = write_png(tmp_path / "s.png", 800, 600, noise=True)
    out = tmp_path / "out.png"
    imaging.resize_and_encode(source, 400, 300, "png", None, out, resize=False)
    assert imaging.probe(out)[:2] == (800, 600)


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


def _captured_argv(monkeypatch):
    """Collect the argv of the next _run, instead of running it."""
    calls = []
    monkeypatch.setattr(imaging, "_run", lambda argv, **kwargs: calls.append(argv))
    return calls


def test_png_omits_format_options_entirely(tmp_path, monkeypatch):
    """png takes no quality, and sips will not tell you if you send one anyway.

    Measured: `sips -s format png -s formatOptions None` prints `Warning:
    Unknown format option None`, exits 0, and writes a perfectly valid PNG. So
    no assertion about the output file can distinguish omitting the flag from
    passing junk in it -- the argv is the only place this is observable, which
    is why this one test looks at the command rather than the result.
    """
    calls = _captured_argv(monkeypatch)
    source = write_png(tmp_path / "s.png", 32, 24)
    imaging.resize_and_encode(source, 32, 24, "png", None, tmp_path / "o.png",
                              resize=True)
    assert "formatOptions" not in calls[0]
    assert "None" not in calls[0]


def test_a_lossy_format_does_pass_its_quality(tmp_path, monkeypatch):
    """The other half of the above: omission must be specific to png."""
    calls = _captured_argv(monkeypatch)
    source = write_png(tmp_path / "s.png", 32, 24)
    imaging.resize_and_encode(source, 32, 24, "heic", 80, tmp_path / "o.heic",
                              resize=False)
    assert "formatOptions" in calls[0]
    assert calls[0][calls[0].index("formatOptions") + 1] == "80"


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
    check would wave it through. Uses resize_and_encode rather than crop
    because crop now rejects an unreadable source at its bounds probe, which
    would take a different path and leave the post-condition uncovered."""
    out = tmp_path / "out.png"
    with pytest.raises(imaging.ImagingError) as caught:
        imaging.resize_and_encode(tmp_path, 10, 10, "png", None, out,
                                  resize=False)
    assert "without writing" in str(caught.value)
    assert not out.exists()


def test_a_stale_destination_cannot_stand_in_for_output(tmp_path):
    """The post-condition asks "is the file there?", so a leftover file from an
    earlier run would answer yes for a run that wrote nothing. Clearing the
    destination first is what makes the question mean what it looks like."""
    missing = tmp_path / "nope.png"
    out = tmp_path / "out.png"
    write_png(out, 64, 64)                      # stale output from an earlier run
    with pytest.raises(imaging.ImagingError):
        imaging.resize_and_encode(missing, 10, 10, "png", None, out,
                                  resize=True)
    assert not out.exists(), "the stale file was left to be mistaken for output"


def test_a_missing_binary_raises_imaging_error(tmp_path):
    """Criterion 7 covers every failure, not only the ones that run. upscayl-bin
    absent is the realistic first-run case, and it lands in Task 9."""
    source = write_png(tmp_path / "s.png", 32, 24)
    with pytest.raises(imaging.ImagingError) as caught:
        imaging.upscale(source, tmp_path / "out.png",
                        tmp_path / "no-such-upscayl", tmp_path / "models")
    assert "could not be run" in str(caught.value)
    assert isinstance(caught.value.__cause__, FileNotFoundError)


def test_a_timeout_raises_imaging_error():
    """The other escapee. Uses _run directly: no sips invocation is slow enough
    to time out reliably, and inventing one would test the fixture."""
    with pytest.raises(imaging.ImagingError) as caught:
        imaging._run(["/bin/sleep", "5"], timeout=0.2)
    assert "timed out" in str(caught.value)
    assert isinstance(caught.value.__cause__, subprocess.TimeoutExpired)


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
