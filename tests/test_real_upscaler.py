"""Tier 3: the real binary. Marked, deselected by default.

The measurement here is the evidence for spec section 7's per-photo upscaling,
which is the largest single saving in the design: 2467 upscaler runs become 756
and 37.2 hours become 24.3. The claim is that enlarging then cutting equals
cutting then enlarging -- true if the model carries no whole-image context,
which is how this architecture works.

WHAT CHANGED, AND WHY IT MATTERS. The first version of this file measured one
number, 44.83 dB, on a `write_png(noise=True)` fixture, and called the question
settled. It was not. This model annihilates generated noise -- pixel standard
deviation 0.2898 collapses to 0.0232, a 12.5x flattening -- so that number was
measured between two nearly uniform images and says almost nothing about
photographs. The repo's own research document had already quantified the same
inflation from the other direction: "PSNR vs Lanczos: 53 dB on a synthetic
image, 40 dB on a real photo."

So the source is now real photographs, and the output is a DISTRIBUTION rather
than a draw. Equivalence-under-cropping is a property of the model over
content; one number cannot characterise it, and the spread below is wide enough
that which photograph you happen to pick decides the verdict.

THE ARMS-AGAINST-EACH-OTHER MEASUREMENT DOES NOT DECIDE ANYTHING, and there is
deliberately no pass assertion on its PSNR. The old single-threshold rule is the
wrong shape for a content-dependent property. Worse, scoring arm A against arm B
cannot say which is BETTER: arm B is not truth, it is a second guess from the
same model, so the number measures how far the model's answer moves when the
input is cropped and nothing else. `old_rule_band` classifies those measurements
under the retired rule so they can still be read against it, and that is all it
does.

WHAT DOES DECIDE IT is `test_both_arms_against_ground_truth`, at the bottom of
this file: both arms scored against ORIGINAL photograph pixels, using the
protocol the research document used to choose this upscaler. That test also
asserts no verdict -- it reports, and a human rules -- but its numbers are the
ones that mean something, because they have a truth to be right or wrong about.
It scores PSNR and DSSIM both, because the research document warns that "PSNR
inverts the visual ranking" for this upscaler.

What IS still asserted, because these are invariants rather than judgments:

  * Both arms produce identical dimensions, for every image.
  * Every image in the selection actually yields a measurement.

Two mechanical points that a careless harness gets wrong, both of which would
measure our own bugs rather than the model:

  * Both arms cut through `imaging.crop`, never a bare `sips` call.
    `sips --cropOffset 0 0` silently returns the CENTERED slice at exactly the
    right dimensions (imaging.py fact 6), and the top slice of a three-way
    split sits at precisely (0, 0).
  * Both arms derive their slice from ONE rect. Arm A scales it by 4 and cuts
    from the 4x frame; arm B cuts it unscaled from the source and enlarges.
    Computing the two rectangles independently would measure our arithmetic.

Note what the top-slice probe below does NOT establish, since the first version
of this file claimed otherwise. It cannot catch an arm cutting the wrong
REGION: under that bug both arms receive `sips`' centred crop, and those
centres are exactly a factor of four apart (2724 = 4 x 681), so the two slide
onto the middle slice together and still agree. It catches a misregistration --
an off-by-one in the pad-and-shift workaround -- and nothing more.
"""

import os
import re
import shutil
import statistics
import subprocess
import time
from pathlib import Path

import pytest

# `conftest`, not `tests.conftest` -- pytest imports it as a top-level module,
# and the dotted spelling builds a second module object holding a second copy
# of every constant in it. See the note in test_corpus_sample.py.
from conftest import CORPUS, copy_sample, corpus_or_skip, sample_names

from paperhanger import cli, execute, geometry, imaging, toolchain

# The retired rule's two bounds. Kept as constants because the numbers below
# are reported against them, NOT because this file applies them.
PSNR_PASS = 40.0
PSNR_JUDGMENT_FLOOR = 35.0

BAND_BELOW_FLOOR = "below the 35 dB floor"
BAND_JUDGMENT = "in the 35-40 dB judgment band"
BAND_AT_OR_ABOVE_BAR = "at or above the 40 dB bar"

# Every window cut for the measurement is exactly this, so the model sees the
# same input shape for every photograph and the numbers are comparable. It is
# also the shape the retired gate used, which keeps the old figure readable
# against the new ones.
WINDOW_WIDTH, WINDOW_HEIGHT = 700, 1800

EXPECTED_OUTPUTS = 102

NAME = re.compile(r"_(?P<w>\d+)x(?P<h>\d+)_(?:native|[\d.]+x)\.heic$")

# Where the worst case is written for a human to look at. Defaults under the
# run's processing directory; the env var exists so a reviewer can send the
# artifacts somewhere that outlives a pytest tmp tree.
ARTIFACTS_ENV = "PAPERHANGER_EQUIVALENCE_ARTIFACTS"

# The pad-and-shift probe. One photograph, measured at the TOP slice as well as
# the middle one, so the two numbers are comparable within a single image
# instead of across content. Chosen for being flat and mid-toned, where a
# one-pixel misregistration shows up as a large PSNR drop rather than hiding in
# texture.
PAD_PATH_PROBE = "mountain_lake_reflection_4788.jpg"


def selection() -> list:
    """The images measured, by a rule rather than by hand.

    Every name in `tests/corpus_sample.txt` whose NATIVE pixels can host a
    700x1800 window, in manifest order. That is 18 of the 27, and the nine it
    drops are dropped for being too small to cut the window from, not for
    anything about their content.

    A rule rather than a hand-picked dozen on purpose. The reviewer's twelve
    and my eighteen should not differ by which images either of us found
    interesting, and a selection I curated is one I could have curated toward
    the answer I wanted. The sample was already built for coverage -- every
    routing branch, every band, every input format, every colour-profile class
    -- so taking all of it that fits inherits most of that spread instead of
    re-deriving it. Not all of it: the height rule drops the Adobe RGB
    photograph, so the profile spread narrows (see `_window`). The content
    classes it does cover, and where they come from:

      fine detail / heavy texture  green_grass_texture_3997.png,
                                   moss_with_pine_needles_5324.jpg,
                                   snowy_forest_6657.jpg, foggy_forest_3113.jpg,
                                   saint_with_angel_painting_7850.jpg,
                                   dark_stones_7236.png
      flat sky / water / fog       mountain_lake_reflection_4788.jpg,
                                   aerial_view_waves_ocean_6671.jpg,
                                   purple_nebula_glow_0312_x.heic,
                                   mountain_landscape_sunset_5677.jpg,
                                   man_with_car_in_fog_1158.jpg,
                                   snowy_mountain_4490.jpg
      dark, low-amplitude          galaxy_pattern_dark_tones_2480.JPG,
                                   blade_runner_2049_concept_poseter_.webp
      smooth gradient / abstract   abstract_blue_texture_4503.JPG,
                                   acrylic_7049.JPG
      defocused blur               bokeh_nature_scene_7629.jpg
      lying extension (WebP)       snowy_forest_landscape_9522.jpg

    Skips rather than fails on a renamed photograph, exactly as `copy_sample`
    does: the manifest names files in a folder no test controls.
    """
    chosen = []
    for name in sample_names():
        measured = imaging.probe(CORPUS / name)
        if measured is None:
            continue
        width, height, _fmt = measured
        if width >= WINDOW_WIDTH and height >= WINDOW_HEIGHT:
            chosen.append(name)
    return chosen


def old_rule_band(psnr: float) -> str:
    """Classify a measurement under the RETIRED single-threshold rule.

    Reporting only. Nothing in this file branches on the result, and the
    judgment band is not a verdict this code is entitled to reach -- it is the
    band whose whole point was that a human decides. The boundaries are closed
    below and open above, matching the `<` comparisons the retired gate used:
    exactly 35.0 is in the judgment band, exactly 40.0 is above the bar.
    """
    if psnr < PSNR_JUDGMENT_FLOOR:
        return BAND_BELOW_FLOOR
    if psnr < PSNR_PASS:
        return BAND_JUDGMENT
    return BAND_AT_OR_ABOVE_BAR


def test_the_retired_rule_classifies_its_own_boundaries():
    """The judgment band, driven by injection rather than by an upscale.

    This branch decided whether a human got consulted, and across the whole of
    task 14 it was never once executed -- the only measurement that ever
    reached it was 44.83 dB. Unmarked, so it runs in tier 1 on every commit:
    it needs no binary, no model and no photographs, and a boundary that
    silently inverted would otherwise be found by nobody.
    """
    assert old_rule_band(0.0) == BAND_BELOW_FLOOR
    assert old_rule_band(34.99) == BAND_BELOW_FLOOR
    assert old_rule_band(34.17) == BAND_BELOW_FLOOR      # the reviewer's worst

    assert old_rule_band(35.0) == BAND_JUDGMENT          # closed below
    assert old_rule_band(37.5) == BAND_JUDGMENT
    assert old_rule_band(39.99) == BAND_JUDGMENT

    assert old_rule_band(40.0) == BAND_AT_OR_ABOVE_BAR   # open above
    assert old_rule_band(44.83) == BAND_AT_OR_ABOVE_BAR  # the retired figure
    assert old_rule_band(float("inf")) == BAND_AT_OR_ABOVE_BAR


@pytest.fixture
def real_toolchain(monkeypatch):
    monkeypatch.delenv("PAPERHANGER_UPSCAYL_BIN", raising=False)
    try:
        return toolchain.ensure_ready()
    except toolchain.ToolchainError as error:
        pytest.fail(f"the gate needs the real toolchain: {error}")


def _compare(left: Path, right: Path, metric: str = "PSNR") -> float:
    """One ImageMagick metric. Test-only; magick is not a runtime dependency.

    `compare` exits 1 whenever the images differ at all, so the status is not
    read; the metric goes to stderr either way.

    It prints two numbers, `raw (normalized)`, and which one is wanted depends
    on the metric. For PSNR the leading figure is the decibel value everything
    here is quoted in. For DSSIM the leading figure is an unnormalized sum --
    312.764 for a pair whose actual DSSIM is 0.00477 -- so the parenthesised
    value is the one on the conventional 0-to-1 scale, where 0 is identical and
    larger is more different. Taking the wrong one silently reports a number
    about 65000x too large, with no error anywhere.
    """
    if shutil.which("magick") is None:
        pytest.fail("this measurement needs `magick`; brew install imagemagick")
    proc = subprocess.run(["magick", "compare", "-metric", metric,
                           str(left), str(right), "null:"],
                          capture_output=True, text=True)
    output = (proc.stderr or proc.stdout).strip()
    fields = output.split()
    text = fields[0] if fields else ""
    if metric == "DSSIM":
        # `0 (0)` for identical input, so the parenthesis is not always present.
        text = fields[1].strip("()") if len(fields) > 1 else text
    if text.lower().startswith("inf"):
        return float("inf")
    try:
        return float(text)
    except ValueError:
        pytest.fail(f"magick compare printed no {metric}: {output!r}")


def _psnr(left: Path, right: Path) -> float:
    """PSNR in dB. Identical inputs report 120, not `inf`, on ImageMagick 7.1.2."""
    return _compare(left, right, "PSNR")


def _window(source: Path, out_png: Path) -> Path:
    """A centred WINDOW_WIDTH x WINDOW_HEIGHT window, normalized as production would.

    Centred because the placement has to be decided by a rule; any rule would
    do, and this one needs no per-photograph judgment.

    Normalized because `execute.py` normalizes once per source photo before it
    enlarges anything, so a window that skipped it would be measuring a path
    the tool does not take. It also makes the comparison profile-neutral, which
    matters less here than the selection's spread first suggested: the 1800-row
    height rule drops the sample's Adobe RGB photograph, so what is left is 14
    sRGB, 2 carrying no profile at all, 1 ProPhoto RGB
    (moss_with_pine_needles_5324.jpg) and 1 tagged `c2`
    (bokeh_nature_scene_7629.jpg). Measured with `sips -g profile`, not assumed.
    """
    measured = imaging.probe(source)
    assert measured is not None, f"{source.name} is not a readable image"
    width, height, _fmt = measured
    rect = geometry.Rect((width - WINDOW_WIDTH) // 2, (height - WINDOW_HEIGHT) // 2,
                         WINDOW_WIDTH, WINDOW_HEIGHT)
    cut = out_png.with_name(out_png.name + ".cut.png")
    try:
        imaging.crop(source, rect, cut)
        imaging.normalize_to_srgb_png(cut, out_png)
    finally:
        cut.unlink(missing_ok=True)
    return out_png


def _both_arms(window: Path, binary, models, which: int, label: str, workdir: Path):
    """Run both arms over slice `which` of a horizontal three-way split.

    Returns (psnr, dimensions, arm_a_path, arm_b_path). The 4x frame and the
    intermediate slice are deleted before returning -- the frame alone is about
    60 MB, and eighteen of them retained would be a gigabyte of temp for no
    reason. The two arm outputs are kept so the caller can preserve the worst
    pair.
    """
    # ONE rect. Arm A uses rect.scaled(4), arm B uses rect itself; there is no
    # second derivation anywhere in this function.
    rect = geometry.horizontal_thirds(WINDOW_WIDTH, WINDOW_HEIGHT)[which]
    scaled = rect.scaled(4)

    # --- arm A: whole-then-cut (what the design does) ---
    frame = workdir / f"frame_4x_{label}.png"
    arm_a = workdir / f"whole_then_cut_{label}.png"
    try:
        imaging.upscale(window, frame, binary, models)
        imaging.crop(frame, scaled, arm_a)
    finally:
        frame.unlink(missing_ok=True)

    # --- arm B: cut-then-upscale (what the legacy script did) ---
    slice_source = workdir / f"slice_{label}.png"
    arm_b = workdir / f"cut_then_upscale_{label}.png"
    try:
        imaging.crop(window, rect, slice_source)
        imaging.upscale(slice_source, arm_b, binary, models)
    finally:
        slice_source.unlink(missing_ok=True)

    dimensions_a = imaging.probe(arm_a)[:2]
    dimensions_b = imaging.probe(arm_b)[:2]
    assert dimensions_a == dimensions_b, (
        f"{label}: whole-then-cut measured {dimensions_a} and cut-then-upscale "
        f"{dimensions_b}; the arms are not comparable"
    )
    return _psnr(arm_a, arm_b), dimensions_a, arm_a, arm_b


@pytest.mark.real_upscaler
def test_toolchain_is_the_pinned_one(real_toolchain):
    binary, _models = real_toolchain
    assert binary.is_file()
    assert toolchain.models_ok(), "model files missing or failing their SHA-256"


@pytest.mark.real_upscaler
def test_whole_frame_equivalence_over_real_photographs(tmp_path, real_toolchain):
    """THE MEASUREMENT. Both arms, every selected photograph, no verdict.

    Prints a per-image table and then min / median / max with the counts in
    each band of the retired rule. Asserts only the two invariants: the arms
    agree on dimensions, and every image yields a number.
    """
    binary, models = real_toolchain
    # The corpus check FIRST. `selection()` probes files in the corpus, so on a
    # machine that has never seen it the count comes back zero and a length
    # assertion placed above this line turns the documented clean skip into a
    # failure about a folder no test controls.
    inbox = copy_sample(corpus_or_skip(), tmp_path / "inbox")
    names = selection()
    assert len(names) >= 12, (
        f"only {len(names)} of the sample can host a {WINDOW_WIDTH}x{WINDOW_HEIGHT} "
        f"window; the measurement wants at least twelve"
    )

    processing = tmp_path / "processing"
    artifacts = Path(os.environ.get(ARTIFACTS_ENV,
                                    processing / "equivalence_worst_case"))
    artifacts.mkdir(parents=True, exist_ok=True)
    workdir = tmp_path / "work"
    workdir.mkdir()

    rect = geometry.horizontal_thirds(WINDOW_WIDTH, WINDOW_HEIGHT)[1]
    scaled = rect.scaled(4)
    print(f"\nwindow {WINDOW_WIDTH}x{WINDOW_HEIGHT}, centred, normalized to sRGB PNG")
    print(f"shared rect {rect.width}x{rect.height}+{rect.x}+{rect.y}; "
          f"arm A cuts {scaled.width}x{scaled.height}+{scaled.x}+{scaled.y} "
          f"from the 4x frame")
    print(f"{len(names)} images, middle slice, "
          f"whole-then-cut vs cut-then-upscale\n")
    print(f"{'PSNR dB':>9}  {'dimensions':>12}  image")

    results = []
    worst = None
    started = time.monotonic()
    for name in names:
        window = _window(inbox / name, workdir / f"window_{name}.png")
        try:
            psnr, dimensions, arm_a, arm_b = _both_arms(
                window, binary, models, 1, "middle", workdir)
        finally:
            window.unlink(missing_ok=True)
        results.append((name, psnr, dimensions))
        print(f"{psnr:9.2f}  {dimensions[0]:>5}x{dimensions[1]:<6}  {name}")

        if worst is None or psnr < worst[1]:
            worst = (name, psnr)
            _preserve(arm_a, arm_b, artifacts, name)
        arm_a.unlink(missing_ok=True)
        arm_b.unlink(missing_ok=True)
    elapsed = time.monotonic() - started

    values = [psnr for _n, psnr, _d in results]
    counts = {BAND_BELOW_FLOOR: 0, BAND_JUDGMENT: 0, BAND_AT_OR_ABOVE_BAR: 0}
    for value in values:
        counts[old_rule_band(value)] += 1

    print(f"\nmin    {min(values):.2f} dB  ({worst[0]})")
    print(f"median {statistics.median(values):.2f} dB")
    print(f"max    {max(values):.2f} dB")
    print(f"\nagainst the RETIRED single-threshold rule, for reference only:")
    for band in (BAND_BELOW_FLOOR, BAND_JUDGMENT, BAND_AT_OR_ABOVE_BAR):
        print(f"  {counts[band]:>2} of {len(values)} {band}")
    print(f"\nNO VERDICT. The pass criterion for a content-dependent property "
          f"is unsettled\nand is not this test's to choose. Worst case written "
          f"to:\n  {artifacts}")
    print(f"\n{len(names)} images measured in {elapsed / 60:.1f} min")

    # The only assertions. Everything above is measurement.
    assert len(results) == len(names)
    assert {d for _n, _p, d in results} == {(WINDOW_WIDTH * 4, rect.height * 4)}


def _preserve(arm_a: Path, arm_b: Path, artifacts: Path, name: str) -> None:
    """Keep the running worst pair, plus a difference image a human can read.

    Overwrites: only the worst case is wanted, and holding every pair would be
    about half a gigabyte. The difference is auto-levelled because the raw one
    is black -- these images agree to within a couple of levels almost
    everywhere, and the question a reviewer is being asked is WHERE they
    disagree and whether it is structured, which an unamplified difference
    cannot show.
    """
    for existing in artifacts.glob("worst_*"):
        existing.unlink(missing_ok=True)
    stem = Path(name).stem
    shutil.copy2(arm_a, artifacts / f"worst_{stem}_whole_then_cut.png")
    shutil.copy2(arm_b, artifacts / f"worst_{stem}_cut_then_upscale.png")
    (artifacts / f"worst_{stem}_README.txt").write_text(
        f"Lowest-scoring image of the equivalence measurement: {name}\n\n"
        f"  worst_{stem}_whole_then_cut.png    arm A: the 700x1800 window was\n"
        f"                                     enlarged 4x whole, then the middle\n"
        f"                                     slice was cut from the result\n"
        f"  worst_{stem}_cut_then_upscale.png  arm B: the middle slice was cut\n"
        f"                                     from the window first, then enlarged\n"
        f"  worst_{stem}_difference.png        the two, differenced and\n"
        f"                                     AUTO-LEVELLED -- the amplification is\n"
        f"                                     large, so brightness here is not a\n"
        f"                                     magnitude, only a location\n\n"
        f"The design takes arm A. The question is whether arm A and arm B are\n"
        f"interchangeable on real photographs. PSNR says they are less alike than\n"
        f"a synthetic fixture suggested; the research document warns that PSNR\n"
        f"inverts the visual ranking for this upscaler, so look at the crops.\n"
    )
    subprocess.run(
        ["magick", str(arm_a), str(arm_b), "-compose", "difference",
         "-composite", "-auto-level", str(artifacts / f"worst_{stem}_difference.png")],
        capture_output=True, text=True,
    )


@pytest.mark.real_upscaler
def test_the_pad_and_shift_workaround_does_not_shift_the_region(tmp_path,
                                                                real_toolchain):
    """The top slice, on the one photograph, against its own middle slice.

    The top slice sits at (0, 0) in the window AND at (0, 0) in the 4x frame,
    which is the `--cropOffset 0 0` shape `sips` silently replaces with its own
    centred crop, so both arms take `crop`'s pad-and-shift path here. The
    middle slice takes the direct path on both arms. Same photograph, same
    window, so the two numbers differ only by which path cut them.

    What this catches is a MISREGISTRATION: the review measured the same pair
    offset by one pixel at 26.15 dB against 34.17 aligned, so a workaround that
    shifted a region would show up as a collapse rather than a wobble. What it
    does NOT catch is an arm cutting the wrong region -- both arms would get
    `sips`' centred crop, and those centres are exactly 4x apart, so they slide
    onto the middle slice together and still agree. The first version of this
    file claimed otherwise and was wrong.

    No threshold. Both numbers are printed and the comparison is the reader's.
    """
    binary, models = real_toolchain
    if PAD_PATH_PROBE not in sample_names():
        pytest.skip(f"{PAD_PATH_PROBE} is no longer in the sample manifest")
    source = CORPUS / PAD_PATH_PROBE
    if not source.exists():
        pytest.skip(f"{PAD_PATH_PROBE} is missing from the corpus")

    workdir = tmp_path / "work"
    workdir.mkdir()
    shutil.copy2(source, workdir / PAD_PATH_PROBE)
    window = _window(workdir / PAD_PATH_PROBE, workdir / "window.png")

    top_psnr, top_dimensions, top_a, top_b = _both_arms(
        window, binary, models, 0, "top", workdir)
    middle_psnr, middle_dimensions, mid_a, mid_b = _both_arms(
        window, binary, models, 1, "middle", workdir)

    print(f"\n{PAD_PATH_PROBE}, one window, two slice positions:")
    print(f"  top slice    (0,0), BOTH arms take crop's pad-and-shift path: "
          f"{top_psnr:.2f} dB at {top_dimensions[0]}x{top_dimensions[1]}")
    print(f"  middle slice      , BOTH arms take the direct path:           "
          f"{middle_psnr:.2f} dB at {middle_dimensions[0]}x{middle_dimensions[1]}")
    print(f"  a one-pixel shift would read about 26 dB (measured by review)")

    for path in (top_a, top_b, mid_a, mid_b):
        path.unlink(missing_ok=True)

    assert top_dimensions == middle_dimensions


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


@pytest.mark.real_upscaler
def test_sample_runs_with_the_real_binary(tmp_path, real_toolchain):
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


# ---------------------------------------------------------------------------
# The ground-truth experiment.
#
# Everything above measures the two arms against EACH OTHER, which cannot say
# which one is better -- arm B is not truth, it is a second guess from the same
# model. This measures both arms against real photograph pixels instead, using
# the protocol the research document used to choose this upscaler: take truth
# at native resolution, shrink it to make the model's input, enlarge it back,
# and score what comes out against what was there.
#
#   G   a ground-truth window, ORIGINAL pixels at native resolution
#   S   G downscaled 4x by sips -- the model's input, a genuine reduction of G
#   A   arm A, whole-then-cut:  upscale S whole, cut the slice from the 4x frame
#   B   arm B, cut-then-upscale: cut the slice from S, upscale that
#   T   the truth slice: the same slice cut from G at full resolution
#
# One rect throughout. `rect` cuts B's slice out of S; `rect.scaled(4)` cuts
# both A's slice out of the 4x frame and T's slice out of G. The factor of four
# is construction, not coincidence: G is exactly 4x S on both axes. Every cut
# goes through `imaging.crop`, never a bare `sips` call -- see the module
# docstring for why that distinction decides whether this measures the model or
# our own crop bug.
#
# Scored both ways on purpose. PSNR because it is the protocol's metric and
# comparable to the research document's numbers; DSSIM because the research
# document explicitly warns that "PSNR inverts the visual ranking" for this
# upscaler, and answering a perceptual question with PSNR alone would repeat
# the mistake that cost this task two rounds.
# ---------------------------------------------------------------------------

# The ground-truth window, and the model input it reduces to. Chosen by a sweep
# rather than by taste: over window ratios from 1.25 to 3.2, this is the
# LARGEST window at least twelve of the sample's photographs can supply from
# native pixels. Both G axes are divisible by four so that S = G/4 is exact,
# and the middle third of S scales to 2048x1280 -- 16:10 to the digit, which is
# a real desktop wallpaper shape rather than an approximation of one.
GT_WINDOW_WIDTH, GT_WINDOW_HEIGHT = 2048, 2988
GT_SOURCE_WIDTH, GT_SOURCE_HEIGHT = 512, 747

GT_ARTIFACTS_ENV = "PAPERHANGER_GROUND_TRUTH_ARTIFACTS"


def ground_truth_selection() -> list:
    """The twelve, by the same shape of rule as `selection()`.

    Every name in `tests/corpus_sample.txt` whose NATIVE pixels can host the
    2048x2988 ground-truth window, in manifest order. Exactly twelve qualify,
    which is what pins the window size: one pixel larger on either axis and it
    would be eleven.

    Note what the size rule costs, because it is not nothing. The two
    photographs that scored worst in the arms-against-each-other measurement --
    `snowy_forest_landscape_9522.jpg` at 33.43 dB and
    `mountain_lake_reflection_4788.jpg` at 35.19 -- are both 3840x2160, and a
    2988-row window does not fit in 2160 rows. So this experiment cannot speak
    to either of them. A 1440x2160 window would admit sixteen images including
    both, at the cost of a 360x540 model input; that trade was decided the
    other way, toward the largest window, and the exclusion is a real limit on
    what these twelve can conclude.
    """
    chosen = []
    for name in sample_names():
        measured = imaging.probe(CORPUS / name)
        if measured is None:
            continue
        width, height, _fmt = measured
        if width >= GT_WINDOW_WIDTH and height >= GT_WINDOW_HEIGHT:
            chosen.append(name)
    return chosen


def _ground_truth_window(source: Path, out_png: Path) -> Path:
    """A centred 2048x2988 window of ORIGINAL pixels, normalized to sRGB PNG.

    Normalized here, once, before anything derives from it: S, both arms and T
    all descend from this file, so the colour conversion happens on the truth
    and never between the truth and the thing being compared to it. That is
    also the order `execute.py` uses -- normalize the source, then enlarge.
    """
    measured = imaging.probe(source)
    assert measured is not None, f"{source.name} is not a readable image"
    width, height, _fmt = measured
    rect = geometry.Rect((width - GT_WINDOW_WIDTH) // 2,
                         (height - GT_WINDOW_HEIGHT) // 2,
                         GT_WINDOW_WIDTH, GT_WINDOW_HEIGHT)
    cut = out_png.with_name(out_png.name + ".cut.png")
    try:
        imaging.crop(source, rect, cut)
        imaging.normalize_to_srgb_png(cut, out_png)
    finally:
        cut.unlink(missing_ok=True)
    return out_png


def _against_truth(window: Path, binary, models, workdir: Path):
    """Build S, A, B and T from one ground-truth window and score both arms.

    Returns a dict of the four scores plus the three paths worth keeping.
    """
    # S: the model's input, a real 4x reduction of the truth. Both axes passed
    # explicitly -- imaging fact 3; a single --resample flag derives the other
    # axis and rounds it inconsistently.
    small = workdir / "small_source.png"
    imaging.resize_and_encode(window, GT_SOURCE_WIDTH, GT_SOURCE_HEIGHT,
                              "png", None, small, resize=True)

    # ONE rect. `rect` cuts from S; `scaled` cuts from the 4x frame AND from G.
    rect = geometry.horizontal_thirds(GT_SOURCE_WIDTH, GT_SOURCE_HEIGHT)[1]
    scaled = rect.scaled(4)

    # T: truth, cut from the original pixels. Never a model output.
    truth = workdir / "truth_slice.png"
    imaging.crop(window, scaled, truth)

    # A: whole-then-cut.
    frame = workdir / "frame_4x.png"
    arm_a = workdir / "arm_a_whole_then_cut.png"
    try:
        imaging.upscale(small, frame, binary, models)
        imaging.crop(frame, scaled, arm_a)
    finally:
        frame.unlink(missing_ok=True)

    # B: cut-then-upscale.
    small_slice = workdir / "small_slice.png"
    arm_b = workdir / "arm_b_cut_then_upscale.png"
    try:
        imaging.crop(small, rect, small_slice)
        imaging.upscale(small_slice, arm_b, binary, models)
    finally:
        small_slice.unlink(missing_ok=True)
        small.unlink(missing_ok=True)

    dimensions = {imaging.probe(p)[:2] for p in (arm_a, arm_b, truth)}
    assert dimensions == {(GT_WINDOW_WIDTH, scaled.height)}, (
        f"A, B and T must be the same shape to be comparable; got {dimensions}"
    )

    return {
        "psnr_a": _compare(arm_a, truth, "PSNR"),
        "psnr_b": _compare(arm_b, truth, "PSNR"),
        "dssim_a": _compare(arm_a, truth, "DSSIM"),
        "dssim_b": _compare(arm_b, truth, "DSSIM"),
        "arm_a": arm_a,
        "arm_b": arm_b,
        "truth": truth,
        "dimensions": (GT_WINDOW_WIDTH, scaled.height),
    }


@pytest.mark.real_upscaler
def test_both_arms_against_ground_truth(tmp_path, real_toolchain):
    """THE GATE, in its settled form: which arm is closer to the truth.

    Asserts the invariants only -- A, B and T share a shape, and every image
    yields four scores. Which arm wins, and whether the gap means anything, is
    reported rather than adjudicated here; `paperhanger/execute.py` is not
    touched either way.
    """
    binary, models = real_toolchain
    inbox = copy_sample(corpus_or_skip(), tmp_path / "inbox")
    names = ground_truth_selection()
    assert len(names) >= 12, (
        f"only {len(names)} of the sample can supply a "
        f"{GT_WINDOW_WIDTH}x{GT_WINDOW_HEIGHT} ground-truth window"
    )

    processing = tmp_path / "processing"
    artifacts = Path(os.environ.get(GT_ARTIFACTS_ENV,
                                    processing / "ground_truth_worst_case"))
    artifacts.mkdir(parents=True, exist_ok=True)
    workdir = tmp_path / "gt_work"
    workdir.mkdir()

    rect = geometry.horizontal_thirds(GT_SOURCE_WIDTH, GT_SOURCE_HEIGHT)[1]
    scaled = rect.scaled(4)
    print(f"\nground truth G {GT_WINDOW_WIDTH}x{GT_WINDOW_HEIGHT} (native pixels, "
          f"centred, normalized to sRGB PNG)")
    print(f"model input  S {GT_SOURCE_WIDTH}x{GT_SOURCE_HEIGHT} (G downscaled 4x by sips)")
    print(f"one rect: {rect.width}x{rect.height}+{rect.x}+{rect.y} cuts B from S; "
          f"{scaled.width}x{scaled.height}+{scaled.x}+{scaled.y} cuts A from the "
          f"4x frame and T from G")
    print(f"{len(names)} images. PSNR higher is better; DSSIM LOWER is better.\n")
    header = (f"{'PSNR(A,T)':>10} {'PSNR(B,T)':>10} {'delta':>8}   "
              f"{'DSSIM(A,T)':>11} {'DSSIM(B,T)':>11} {'delta':>10}   image")
    print(header)

    rows = []
    started = time.monotonic()
    for name in names:
        window = _ground_truth_window(inbox / name, workdir / f"gt_{name}.png")
        try:
            scored = _against_truth(window, binary, models, workdir)
        finally:
            window.unlink(missing_ok=True)

        psnr_delta = scored["psnr_a"] - scored["psnr_b"]
        dssim_delta = scored["dssim_a"] - scored["dssim_b"]
        rows.append((name, scored["psnr_a"], scored["psnr_b"], psnr_delta,
                     scored["dssim_a"], scored["dssim_b"], dssim_delta,
                     scored["dimensions"]))
        print(f"{scored['psnr_a']:10.2f} {scored['psnr_b']:10.2f} "
              f"{psnr_delta:+8.2f}   {scored['dssim_a']:11.5f} "
              f"{scored['dssim_b']:11.5f} {dssim_delta:+10.5f}   {name}")

        if abs(psnr_delta) >= max(abs(r[3]) for r in rows):
            _preserve_ground_truth(scored, artifacts, name, psnr_delta)
        for key in ("arm_a", "arm_b", "truth"):
            scored[key].unlink(missing_ok=True)
    elapsed = time.monotonic() - started

    psnr_deltas = [r[3] for r in rows]
    dssim_deltas = [r[6] for r in rows]
    a_wins_psnr = sum(1 for d in psnr_deltas if d > 0)
    a_wins_dssim = sum(1 for d in dssim_deltas if d < 0)   # lower DSSIM is better
    widest = max(rows, key=lambda r: abs(r[3]))

    print(f"\n{'-' * 78}")
    print(f"PSNR  : arm A closer to truth on {a_wins_psnr} of {len(rows)}; "
          f"delta min {min(psnr_deltas):+.2f}, median "
          f"{statistics.median(psnr_deltas):+.2f}, max {max(psnr_deltas):+.2f} dB")
    print(f"DSSIM : arm A closer to truth on {a_wins_dssim} of {len(rows)}; "
          f"delta min {min(dssim_deltas):+.5f}, median "
          f"{statistics.median(dssim_deltas):+.5f}, max {max(dssim_deltas):+.5f}")
    print(f"widest PSNR gap: {widest[0]} at {widest[3]:+.2f} dB")
    print(f"visual evidence written to:\n  {artifacts}")
    print(f"\n{len(names)} images measured in {elapsed / 60:.1f} min")

    # The only assertions. Everything above is measurement.
    assert len(rows) == len(names)
    assert {r[7] for r in rows} == {(GT_WINDOW_WIDTH, scaled.height)}


def _preserve_ground_truth(scored, artifacts: Path, name: str, psnr_delta: float) -> None:
    """Keep the running widest-gap case: both arms, the truth, both differences.

    The differences are auto-levelled, so brightness locates a disagreement
    rather than measuring one -- same caveat as the other worst-case directory.
    """
    for existing in artifacts.glob("gt_*"):
        existing.unlink(missing_ok=True)
    stem = Path(name).stem
    shutil.copy2(scored["arm_a"], artifacts / f"gt_{stem}_A_whole_then_cut.png")
    shutil.copy2(scored["arm_b"], artifacts / f"gt_{stem}_B_cut_then_upscale.png")
    shutil.copy2(scored["truth"], artifacts / f"gt_{stem}_T_ground_truth.png")
    for label, arm in (("A", scored["arm_a"]), ("B", scored["arm_b"])):
        subprocess.run(
            ["magick", str(arm), str(scored["truth"]), "-compose", "difference",
             "-composite", "-auto-level",
             str(artifacts / f"gt_{stem}_{label}_minus_truth.png")],
            capture_output=True, text=True,
        )
    (artifacts / f"gt_{stem}_README.txt").write_text(
        f"Widest PSNR gap between the two arms measured against ground truth.\n\n"
        f"  image           {name}\n"
        f"  PSNR(A, truth)  {scored['psnr_a']:.2f} dB\n"
        f"  PSNR(B, truth)  {scored['psnr_b']:.2f} dB\n"
        f"  delta           {psnr_delta:+.2f} dB (positive means arm A is closer)\n"
        f"  DSSIM(A, truth) {scored['dssim_a']:.5f}   (lower is better)\n"
        f"  DSSIM(B, truth) {scored['dssim_b']:.5f}\n\n"
        f"  gt_{stem}_T_ground_truth.png      the truth: original pixels, no model\n"
        f"  gt_{stem}_A_whole_then_cut.png    what the design does\n"
        f"  gt_{stem}_B_cut_then_upscale.png  what the legacy script did\n"
        f"  gt_{stem}_A_minus_truth.png       arm A's error, AUTO-LEVELLED\n"
        f"  gt_{stem}_B_minus_truth.png       arm B's error, AUTO-LEVELLED\n\n"
        f"The amplification on the two difference images is large and is applied\n"
        f"to each independently, so compare WHERE they are bright, not how bright.\n"
        f"The research document warns that PSNR inverts the visual ranking for this\n"
        f"upscaler, so the three pictures are the evidence and the numbers are the\n"
        f"summary, not the other way round.\n"
    )
