"""Tier 3: the real binary. Marked, deselected by default.

The equivalence test here is the gate on spec section 7's per-photo upscaling,
which is the largest single saving in the design: 2467 upscaler runs become 756
and 37.2 hours become 24.3. The claim is that enlarging then cutting equals
cutting then enlarging -- true if the model carries no whole-image context,
which is how this architecture works, but argued rather than measured until
this test runs.

Both arms cut through `imaging.crop`, never through a bare `sips` call. That is
not a style preference: `sips --cropOffset 0 0` silently returns the CENTERED
slice at exactly the right dimensions (imaging.py fact 6), and the top slice of
a three-way split sits at precisely (0, 0). An arm cut with raw `sips` would
compare the model against our own crop bug and fail the gate for a reason that
has nothing to do with upscaling.

Both arms also derive their slice from ONE rect. Arm A scales it by 4 and cuts
from the 4x frame; arm B cuts it unscaled from the source and enlarges that.
Computing the two rectangles independently would measure our arithmetic instead
of the model.
"""

import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

# `conftest`, not `tests.conftest` -- pytest imports it as a top-level module,
# and the dotted spelling builds a second module object holding a second copy
# of every constant in it. See the note in test_corpus_sample.py.
from conftest import copy_sample, corpus_or_skip

from paperhanger import cli, execute, geometry, imaging, toolchain
from tests.pngwriter import write_png

pytestmark = pytest.mark.real_upscaler

PSNR_PASS = 40.0
PSNR_JUDGMENT_FLOOR = 35.0

SOURCE_WIDTH, SOURCE_HEIGHT = 700, 1800
EXPECTED_OUTPUTS = 102

# Every wallpaper's name ends in the dimensions its plan committed to, then the
# factor token. Task 8's naming, re-checked here with the real model in play.
NAME = re.compile(r"_(?P<w>\d+)x(?P<h>\d+)_(?:native|[\d.]+x)\.heic$")


@pytest.fixture
def real_toolchain(monkeypatch):
    monkeypatch.delenv("PAPERHANGER_UPSCAYL_BIN", raising=False)
    try:
        return toolchain.ensure_ready()
    except toolchain.ToolchainError as error:
        pytest.fail(f"the gate needs the real toolchain: {error}")


def _psnr(left: Path, right: Path) -> float:
    """PSNR via ImageMagick. Test-only; magick is not a runtime dependency.

    `compare` exits 1 whenever the images differ at all, so the status is not
    read; the metric goes to stderr either way. Identical inputs report 120,
    not `inf`, on ImageMagick 7.1.2 -- the `inf` branch is kept because older
    builds do print it.
    """
    if shutil.which("magick") is None:
        pytest.fail("the gate needs `magick` to measure PSNR; brew install imagemagick")
    proc = subprocess.run(["magick", "compare", "-metric", "PSNR",
                           str(left), str(right), "null:"],
                          capture_output=True, text=True)
    output = (proc.stderr or proc.stdout).strip()
    text = output.split()[0] if output else ""
    if text.lower().startswith("inf"):
        return float("inf")
    try:
        return float(text)
    except ValueError:
        pytest.fail(f"magick compare printed no metric: {output!r}")


def _both_arms(tmp_path, binary, models, which: int, label: str):
    """Run both arms over slice `which` of a horizontal three-way split.

    Returns (psnr, dimensions). Everything the gate has to show for itself is
    printed here, with the arm labels spelled out, so the evidence is quotable
    rather than paraphrased.
    """
    source = write_png(tmp_path / f"source_{label}.png",
                       SOURCE_WIDTH, SOURCE_HEIGHT, noise=True)

    # ONE rect. Arm A uses rect.scaled(4), arm B uses rect itself; there is no
    # second derivation anywhere in this function.
    rect = geometry.horizontal_thirds(SOURCE_WIDTH, SOURCE_HEIGHT)[which]
    scaled = rect.scaled(4)
    print(f"[{label}] shared rect {rect.width}x{rect.height}+{rect.x}+{rect.y} "
          f"in {SOURCE_WIDTH}x{SOURCE_HEIGHT}; arm A cuts "
          f"{scaled.width}x{scaled.height}+{scaled.x}+{scaled.y} from the 4x frame")

    # --- arm A: whole-then-cut (what the design does) ---
    frame = tmp_path / f"frame_4x_{label}.png"
    imaging.upscale(source, frame, binary, models)
    arm_a = tmp_path / f"whole_then_cut_{label}.png"
    imaging.crop(frame, scaled, arm_a)
    print(f"whole-then-cut [{label}]: {imaging.probe(arm_a)[:2]} "
          f"from a {imaging.probe(frame)[:2]} frame")

    # --- arm B: cut-then-upscale (what the legacy script did) ---
    slice_source = tmp_path / f"slice_{label}.png"
    imaging.crop(source, rect, slice_source)
    arm_b = tmp_path / f"cut_then_upscale_{label}.png"
    imaging.upscale(slice_source, arm_b, binary, models)
    print(f"cut-then-upscale [{label}]: {imaging.probe(arm_b)[:2]} "
          f"from a {imaging.probe(slice_source)[:2]} slice")

    assert imaging.probe(arm_a)[:2] == imaging.probe(arm_b)[:2], \
        "the two arms produced different dimensions; equivalence is false"

    psnr = _psnr(arm_a, arm_b)
    print(f"PSNR whole-then-cut vs cut-then-upscale [{label}]: {psnr:.2f} dB "
          f"(pass >= {PSNR_PASS})")
    return psnr, imaging.probe(arm_a)[:2]


def test_toolchain_is_the_pinned_one(real_toolchain):
    binary, models = real_toolchain
    assert binary.is_file()
    assert toolchain.models_ok(), "model files missing or failing their SHA-256"


def test_whole_frame_equivalence(tmp_path, real_toolchain, capsys):
    """THE GATE. Both arms run against the real binary; both are captured."""
    binary, models = real_toolchain
    psnr, _dimensions = _both_arms(tmp_path, binary, models, 1, "middle-slice")

    if psnr < PSNR_JUDGMENT_FLOOR:
        pytest.fail(
            f"equivalence FAILS at {psnr:.2f} dB. Revert spec section 7 to per-plan "
            f"upscaling and restore the 37-hour estimate in sections 3, 7 and 15."
        )
    if psnr < PSNR_PASS:
        pytest.fail(
            f"equivalence is {psnr:.2f} dB, between the {PSNR_JUDGMENT_FLOOR} and "
            f"{PSNR_PASS} dB bounds. This is the user's judgment call, not the "
            f"agent's: stop and ask before either keeping or reverting section 7."
        )


def test_whole_frame_equivalence_at_the_slice_sips_mis_crops(tmp_path,
                                                             real_toolchain,
                                                             capsys):
    """The same measurement on the TOP slice, where both arms take the pad path.

    Supplementary to the gate above, which uses the middle slice: that rect has
    a non-zero y, so neither arm exercises `crop`'s pad-and-shift workaround.
    The top slice sits at (0, 0) in the source AND at (0, 0) in the 4x frame,
    so both arms go the long way round -- pad one pixel on every side, cut at
    +1 -- and this is the case where an off-by-one in that workaround would
    show up as a low PSNR that had nothing to do with the model.

    A third of every desktop slice triple in production is this rect, so it is
    worth knowing the number for it. The gate's decision still rests on the
    middle-slice measurement above; this one only has to clear the judgment
    floor for the two to be telling the same story.
    """
    binary, models = real_toolchain
    psnr, _dimensions = _both_arms(tmp_path, binary, models, 0, "top-slice")
    assert psnr >= PSNR_JUDGMENT_FLOOR, (
        f"the padded top slice measures {psnr:.2f} dB against the middle slice's "
        f"own result; suspect `imaging.crop`'s workaround before the model"
    )


def _outputs(processing: Path) -> list:
    """Every file the run FILED as a wallpaper, and nothing else.

    Scoped to the `to_sort_` trees rather than globbed over the whole
    processing directory. The sample's HEIC source is archived under
    `originals/` keeping its own name, so a `processing.rglob("*.heic")` sweeps
    up `purple_nebula_glow_0312_x.heic` beside the 102 wallpapers and answers
    103 -- and then asks the dimension check to parse a filename that carries
    no dimensions. Same scoping and same reason as test_corpus_sample._outputs.
    """
    return sorted(p for p in processing.glob("to_sort_*/**/*") if p.is_file())


def test_sample_runs_with_the_real_binary(tmp_path, real_toolchain, capsys):
    """The 27-image sample, end to end, with nothing stubbed.

    Takes its OWN copy of the sample rather than the module-scoped
    `corpus_sample` fixture: a run MOVES its inputs into `originals/`, and that
    fixture is shared, read-only, and asserts in teardown that nothing emptied
    it. Same reason `sample_run` in test_corpus_sample.py copies.
    """
    inbox = copy_sample(corpus_or_skip(), tmp_path / "inbox")
    processing = tmp_path / "processing"

    started = time.monotonic()
    exit_code = cli.main(["--format", "heic", "--processing-dir", str(processing),
                          str(inbox)])
    elapsed = time.monotonic() - started
    print(f"27-image sample with the real upscaler: {elapsed / 60:.1f} min")
    assert exit_code == 0

    produced = _outputs(processing)
    print(f"outputs filed: {len(produced)}")
    assert len(produced) == EXPECTED_OUTPUTS

    mismatched = []
    for path in produced:
        match = NAME.search(path.name)
        assert match, path.name
        measured = imaging.probe(path)
        assert measured is not None, f"{path.name} is not a readable image"
        if measured[:2] != (int(match["w"]), int(match["h"])):
            mismatched.append(f"{path.name} measures {measured[0]}x{measured[1]}")
    print(f"filename/dimension mismatches: {len(mismatched)} of {len(produced)}")
    assert not mismatched, "\n".join(mismatched)

    assert list(processing.rglob(f"*{execute.PARTIAL_SUFFIX}")) == []
