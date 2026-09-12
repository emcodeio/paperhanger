"""Tier 2: the 27-image coverage sample, real sips, stubbed upscaler.

The sample was chosen to cover every branch-and-band pair the corpus reaches,
all 23 of them, and all five of its input formats. Two of those three axes are
ASSERTED here rather than trusted: `test_the_sample_covers_every_band` and
`test_every_input_format_in_the_sample_is_read` both fail if the sample stops
covering what it claims.

Colour profile is the axis this does not cover, and the docstring used to say
it did. 7 of the corpus's 12 profile classes are represented, nothing asserts
even that, and the two profile tests below name their own files rather than
working from a census -- so a sample that lost its last ProPhoto image would
fail those two by name and say nothing about coverage. "Every coverage tag"
was two thirds true and read as three thirds.

Chosen once and recorded in `tests/corpus_sample.txt`; the images themselves
stay out of the repo, and every test here skips cleanly on a machine that has
never seen them.

This is the first tier where the whole tool meets input nobody generated. Tier
1 builds its fixtures, so it can only ever ask questions someone thought to
build a fixture for. Here a `.jpg` is really a WebP, a GIF has to reach a
binary that cannot read GIFs, a photograph carries ProPhoto RGB, and the crop
geometry runs against 27 real aspect ratios instead of the handful anybody
would think to type out.

What it costs, and what that buys: one run of the tool over 27 photographs,
about seven minutes of real sips. The stub replaces the ML model and nothing
else -- every crop, resample, colour conversion and encode below is the real
thing, on real pixels, at full size. That is also why the run is a MODULE
fixture: it is one run, asked eleven different questions.

What it cannot see, and where to look instead:

  * Whether a slice holds the RIGHT pixels. Only one test here compares image
    content at all, and it compares whole files rather than regions. Tier 1's
    marked fixtures are what prove a crop lands where it was asked to.
  * Whether `probe` is telling the truth. It is both the measurement and the
    thing measured, so a probe that misreported every file would agree with
    itself all the way through. Tier 1 measures it against PNGs of known size.
  * Anything about encoder quality: `-s formatOptions 80` is checked where it
    is passed, not in the bytes that come back.
  * Any flag but `--format heic` over both devices. One run is what seven
    minutes buys; the flags are Tier 1's.
"""

import contextlib
import hashlib
import io
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# `conftest`, not `tests.conftest`. pytest imports the file as a TOP-LEVEL
# module, so the dotted spelling builds a second module object holding a second
# copy of every constant in it -- and then patching `conftest.CORPUS` to prove
# these tests skip without the corpus patches a copy nothing reads. Measured:
# the run went ahead regardless.
import conftest
from conftest import (copy_sample, corpus_or_skip, install_fake_models,
                      install_fake_upscaler, sample_names)

from paperhanger import bands, cli, execute, formats, imaging, plan, sizes

pytestmark = pytest.mark.corpus

SAMPLE_SIZE = 27
EXPECTED_OUTPUTS = 102

# Recomputed against the CURRENT targets (phone 4320/2880). The earlier
# 3840/2560 targets gave the same total by moving four photos from band 1 into
# band 2, which is exactly why the total alone is not the assertion.
EXPECTED_BANDS = {
    bands.DOWNSCALE: 18,
    bands.NATIVE: 20,
    bands.UPSCALE_REDUCE: 46,
    bands.UPSCALE_ONLY: 18,
}

# Band 5 produces no file, so it is only ever visible as a device turned down.
# Both of these are too narrow for a desktop even after the model: 620 and
# 1242 pixels wide, against a 5120 floor that 4x cannot reach.
REJECTED_ON_DESKTOP = ("foggy_forest_path_7098.JPG",
                       "galaxy_pattern_dark_tones_2480.JPG")

LYING_EXTENSION = "snowy_forest_landscape_9522.jpg"      # really a WebP
WIDE_GAMUT_UPSCALED = "red_tulips_with_mountain_background_4338"   # Adobe RGB
WIDE_GAMUT_UNTOUCHED = "moss_with_pine_needles_5324"              # ProPhoto RGB
OVER_CAP = "bokeh_nature_scene_7629.jpg"
BOTH_SIDES_OF_THE_BAND_BOUNDARY = "katana_with_tag_2369"          # 1920x1080

# 5482x8085, sliced into three 16:10 desktop thirds that are band 2: cut
# straight from the original with no model run anywhere near them, so a
# difference between them is a fact about `imaging.crop` and nothing else.
DETAILED_CROP = ("snowy_mountain_4490", "desktop")

NAME = re.compile(r"_(?P<w>\d+)x(?P<h>\d+)_(?P<factor>native|[\d.]+x)\.[a-z]+$")


def _profile(path):
    proc = subprocess.run(["/usr/bin/sips", "-g", "profile", str(path)],
                          capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        if "profile:" in line:
            return line.split("profile:", 1)[1].strip()
    return None


def _plan_everything(inbox, processing):
    """Plan the whole sample exactly as `cli` would, writing nothing."""
    opts = plan.OutputSettings(processing, "heic", formats.quality_for("heic"))
    images, non_images = cli.scan(inbox)
    works = [plan.plan_photo(p, w, h, list(sizes.DEVICES), opts)
             for p, w, h, f in images]
    return works, non_images


# Where a finished run is allowed to have put something. Everything else under
# the processing directory is an output, wherever it landed.
ARCHIVE_DIRS = ("originals", "error")


def _outputs(processing: Path) -> list:
    """Every file the run FILED as a wallpaper, and nothing else.

    The archive trees are excluded by NAME, not by scoping the glob to
    `to_sort_*`. Both spellings agree on a correct run, and they part company
    on exactly the failure worth catching: an output written somewhere the
    sorter will never look. Globbed at `to_sort_*` such a file is invisible to
    every assertion in this module, including the set-equality one that exists
    to catch strays -- it would report a clean run while a wallpaper sat in
    the processing root.

    Excluded by name rather than by extension, because
    `purple_nebula_glow_0312_x.heic` is a HEIC SOURCE: a bare
    `processing.rglob("*.heic")` sweeps the archived original up beside the
    102 wallpapers and answers 103 -- measured, not imagined. `.work` is
    swept by the CLI and gone by the time anything reads this; if it is not,
    that is a finding and this will show it.
    """
    return sorted(
        p for p in processing.rglob("*")
        if p.is_file()
        and not any(part in ARCHIVE_DIRS or part == cli.WORKROOT_NAME
                    for part in p.relative_to(processing).parts)
    )


def _summary_line(stdout: str) -> str:
    """The closing line, isolated by prefix and then asserted whole.

    Never searched for in all of stdout: the report header above it carries
    its own "N rejected" and "N already done", so a summary that had stopped
    counting either would hide behind the header's copy of the words. That
    defect shipped through sixty green tests in task 12 and was found by
    mutation rather than by a test.
    """
    lines = [line for line in stdout.splitlines()
             if line.startswith(cli.SUMMARY_DONE)]
    assert len(lines) == 1, f"expected one summary line, got {lines}"
    return lines[0]


@dataclass
class SampleRun:
    inbox: Path
    processing: Path
    exit_code: int
    stdout: str
    works: list = field(default_factory=list)


@pytest.fixture(scope="module")
def sample_run(tmp_path_factory, corpus_sample):
    """One real run over the sample. Module-scoped: it costs seven minutes.

    Its own copy of the sample, not the shared `corpus_sample` directory
    itself, because a run MOVES the originals it was given and would empty a
    directory the planning tests still need. But copied FROM that directory
    rather than from the corpus a second time: 192 MB was being read out of
    `~/Pictures/wallpaper` twice per module run for two identical results,
    and the corpus is the one tree nothing here is allowed to touch more than
    it must. `corpus_sample` has already skipped for a missing image by the
    time this runs, so the second read had nothing left to discover either.

    The stub is the conftest one, which refuses anything that is not jpg, png
    or webp exactly as `upscayl-bin` does. That is what makes this run a test
    of the normalize step rather than a test around it: the sample's GIF and
    its HEIC both reach the model, and they reach it only because
    `_upscale_whole_frame` converted them to PNG first. Drop that conversion
    and this fixture fails outright.
    """
    root = tmp_path_factory.mktemp("corpus-sample-run")
    inbox = copy_sample(corpus_sample, root / "inbox")
    processing = root / "processing"

    # Planned BEFORE the run, because the run empties the inbox. These are the
    # destinations the tool committed to; the tests below compare them against
    # what is actually on disk afterwards.
    works, _non_images = _plan_everything(inbox, processing)

    with pytest.MonkeyPatch.context() as patch:
        _binary, models = install_fake_upscaler(root, patch)
        install_fake_models(models, patch)
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            exit_code = cli.main(["--format", "heic",
                                  "--processing-dir", str(processing),
                                  str(inbox)])

    stdout = captured.getvalue()
    # Asserted HERE, not only in the one test that reads `exit_code`. This
    # fixture is module-scoped and a dozen tests take it, so a run that failed
    # arrives as a dozen confusing downstream failures -- missing outputs,
    # originals still in the inbox, a summary that does not parse -- none of
    # which names the reason. Failing in the fixture gives one failure with
    # the run's own output attached to it.
    assert exit_code == cli.OK, (
        f"the sample run exited {exit_code}; every test below is reading the "
        f"wreckage of it rather than testing anything.\n{stdout}"
    )

    return SampleRun(inbox=inbox, processing=processing, exit_code=exit_code,
                     stdout=stdout, works=works)


# ---------- the gate, on a machine that has never seen the photographs ----------
#
# Both of these run everywhere, corpus or no corpus. Every other test in this
# file is gated on 192 MB of the author's photographs, which are deliberately
# not in the repo, so "skips cleanly elsewhere" is the property the whole file
# rests on -- and the only other way to check it is to hide the corpus, which
# is the one thing no test here is allowed to do.

def test_an_absent_corpus_skips_rather_than_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(conftest, "CORPUS", tmp_path / "never-seen")
    with pytest.raises(pytest.skip.Exception, match="corpus not present"):
        corpus_or_skip()


def test_a_sample_image_that_has_gone_missing_skips_rather_than_fails(tmp_path):
    """The manifest names files in a folder no test controls. One renamed
    photograph is a fact about that folder, not about this codebase."""
    with pytest.raises(pytest.skip.Exception, match="missing from the corpus"):
        copy_sample(tmp_path / "empty-corpus", tmp_path / "inbox")


# ---------- what the planner makes of the sample ----------

def test_every_sample_image_is_admitted(corpus_sample):
    images, non_images = cli.scan(corpus_sample)
    assert non_images == 0
    assert len(images) == SAMPLE_SIZE


def test_every_input_format_in_the_sample_is_read(corpus_sample):
    """Five formats, and two of them the model cannot read. The GIF and the
    HEIC are in the sample because `sips` reads both and `upscayl-bin` reads
    neither -- the normalize step is the only reason they get wallpapers."""
    images, _ = cli.scan(corpus_sample)
    assert {fmt for _p, _w, _h, fmt in images} == {"jpeg", "png", "webp",
                                                   "gif", "heic"}


def test_the_lying_extension_reports_its_real_format(corpus_sample):
    """snowy_forest_landscape_9522.jpg is really a WebP. If normalize-or-not
    were decided from the suffix, this file would take the wrong path."""
    target = corpus_sample / LYING_EXTENSION
    assert imaging.probe(target)[2] == "webp"


def test_the_sample_covers_every_band(corpus_sample, tmp_path):
    works, _ = _plan_everything(corpus_sample, tmp_path / "processing")
    seen = {p.band for w in works for p in w.plans}
    seen |= {bands.REJECT} if any(w.rejected_devices for w in works) else set()
    missing = {bands.DOWNSCALE, bands.NATIVE, bands.UPSCALE_REDUCE,
               bands.UPSCALE_ONLY, bands.REJECT} - seen
    assert not missing, (f"the sample no longer covers bands: "
                         f"{sorted(bands.NAMES[b] for b in missing)}")


def test_the_sample_produces_the_expected_outputs_per_band(corpus_sample, tmp_path):
    """The count AND its shape. 102 was reached under two different sets of
    phone targets -- the change moved four photos from band 1 to band 2 and
    both produce outputs -- so a total on its own would have waved a moved
    boundary straight through."""
    works, _ = _plan_everything(corpus_sample, tmp_path / "processing")
    counts = Counter(p.band for w in works for p in w.plans)
    assert dict(counts) == EXPECTED_BANDS
    assert sum(counts.values()) == EXPECTED_OUTPUTS


def test_band_5_turns_down_exactly_two_photos_and_abandons_neither(corpus_sample,
                                                                   tmp_path):
    """Rejection is per DEVICE. Both of these are too narrow for a desktop
    even at 4x, and both still get a phone wallpaper -- so nothing in this
    sample is rejected everywhere, and `error/` stays empty."""
    works, _ = _plan_everything(corpus_sample, tmp_path / "processing")
    rejected = {w.source.name: w.rejected_devices
                for w in works if w.rejected_devices}
    assert rejected == {name: [sizes.DESKTOP] for name in REJECTED_ON_DESKTOP}
    assert not any(w.rejected_everywhere for w in works)


# ---------- and what the tool then does with it ----------

def test_the_run_finishes_with_every_photo_accounted_for(sample_run):
    assert sample_run.exit_code == cli.OK
    assert _summary_line(sample_run.stdout) == (
        f"{cli.SUMMARY_DONE} {SAMPLE_SIZE} ok, 0 already done, "
        f"0 rejected, 0 failed")


def test_every_planned_output_is_on_disk_and_nothing_else_is(sample_run):
    """Set equality, not a count. It pins the name, the directory and the
    absence of strays at once: a plan filed under the wrong device, a
    `below_target` decision inverted, or a second file nobody planned all show
    up here as a difference rather than as a total that happens to match."""
    planned = {p.destination for w in sample_run.works for p in w.plans}
    assert len(planned) == EXPECTED_OUTPUTS
    assert set(_outputs(sample_run.processing)) == planned


def test_every_output_measures_what_its_own_name_says(sample_run):
    """The check that keeps section 3's no-drift claim honest.

    Every output's filename carries the dimensions its plan committed to
    before anything ran. Measuring the file and comparing is what catches the
    ways sips returns a plausible wrong picture at exit 0 -- a derived axis
    rounded the other way, a crop silently ignored, a resample applied against
    pre-crop dimensions. All 102, because the interesting failures are the
    ones that hit one aspect ratio.
    """
    outputs = _outputs(sample_run.processing)
    assert len(outputs) == EXPECTED_OUTPUTS
    wrong = []
    for path in outputs:
        match = NAME.search(path.name)
        assert match, f"unparseable output name: {path.name}"
        measured = imaging.probe(path)
        assert measured is not None, f"{path.name} is not a readable image"
        if measured[:2] != (int(match["w"]), int(match["h"])):
            wrong.append(f"{path.name} measures {measured[0]}x{measured[1]}")
    assert not wrong, "\n".join(wrong)


def test_below_target_holds_both_kinds(sample_run):
    """The folder mixes untouched originals with enlarged-and-still-short
    files, and the factor token is what separates them. Asserted as the whole
    set: `below_target` takes bands 2 and 4 and nothing else, so a fractional
    factor turning up here is a band 3 output that has been misfiled."""
    below = [p for p in _outputs(sample_run.processing)
             if p.parent.name == "below_target"]
    assert below
    assert {NAME.search(p.name)["factor"] for p in below} == {"native", "4x"}


def test_a_4x_token_does_not_by_itself_mean_below_target(sample_run):
    """katana_with_tag_2369.jpg puts both sides of the band 3/4 boundary in
    one photograph, and gives all four outputs the same `4x` token.

    1920x1080. Its desktop plan is band 4 -- 1080 quadruples to 4320, short of
    the 4800 ideal -- so it is filed below_target. Its three phone slices are
    band 3: the same 1080 quadruples to exactly the 4320 phone ideal, which is
    the reduce-afterwards side of the boundary, so they are full-size
    wallpapers filed as such. The token is 4x for all four either way.

    Reading the token as a band -- the obvious shortcut, and the one the test
    above could have been written with -- would misfile three of them.
    """
    katana = [p for p in _outputs(sample_run.processing)
              if p.name.startswith(BOTH_SIDES_OF_THE_BAND_BOUNDARY)]
    assert {NAME.search(p.name)["factor"] for p in katana} == {"4x"}

    # By NAME, not by count. "one below and three not" is satisfied just as
    # well by the filing being inverted per device -- the desktop plan full
    # size and one phone slice in below_target -- which is the exact misfiling
    # this test exists to rule out.
    stem = BOTH_SIDES_OF_THE_BAND_BOUNDARY
    below = sorted(p.name for p in katana if p.parent.name == "below_target")
    full_size = sorted(p.name for p in katana if p.parent.name != "below_target")

    assert below == [f"{stem}_desktop_7680x4320_4x.heic"], below
    assert full_size == [
        f"{stem}_center_phone_2880x4320_4x.heic",
        f"{stem}_left_phone_2880x4320_4x.heic",
        f"{stem}_right_phone_2880x4320_4x.heic",
    ], full_size


def test_the_three_slices_of_one_photo_are_three_different_pictures(sample_run):
    """Fact 6, against a real photograph instead of a generated fixture.

    `sips` silently ignores two --cropOffset shapes, and this trio produces
    both of them: the top slice asks for (0, 0), which `sips` drops in favour
    of its own CENTERED crop -- so an unrepaired top slice comes back as the
    middle one, right size, right format, exit 0 -- and the bottom slice sits
    flush with the bottom edge, which `sips` drops altogether. `imaging.crop`
    pads its way around both, and nothing downstream can tell whether the
    workaround fired: the dimensions are correct in every version of this.

    Band 2, so the slices are cut straight from the original and no model run
    stands between the crop and the file on disk.

    Not asserted across all 25 trios in the run, tempting as that is.
    katana_with_tag_2369.jpg is a blade on a pure black field: its left and
    right phone thirds are byte-identical outputs from two correct crops of
    two identical regions -- 12,441,600 pixels, every one of them zero,
    measured. A rule that a photo's slices always differ calls that a bug.
    """
    stem, device = DETAILED_CROP
    slices = [p for p in _outputs(sample_run.processing)
              if p.name.startswith(stem) and f"_{device}_" in p.name]
    assert len(slices) == 3, [p.name for p in slices]
    digests = {hashlib.sha256(p.read_bytes()).hexdigest() for p in slices}
    assert len(digests) == 3, \
        f"two of {[p.name for p in slices]} are the same picture"


def test_every_source_is_filed_and_none_left_behind(sample_run):
    processing = sample_run.processing
    assert list(sample_run.inbox.iterdir()) == []
    archived = sorted(p.name for p in (processing / "originals").iterdir())
    error_dir = processing / "error"
    errored = sorted(p.name for p in error_dir.iterdir()) \
        if error_dir.exists() else []
    # Names, not a count: `archive` renames onto a free name rather than
    # overwrite, so a count of 27 would survive a collision that silently
    # renamed somebody's photograph.
    assert errored == [], "nothing in this sample is rejected on every device"
    assert archived == sorted(sample_names())


def test_nothing_is_left_staged_or_scratched(sample_run):
    """Staging discipline, across a realistic run rather than one fixture.
    Every output is written as `<name>.partial` in its destination directory
    and renamed in; the scratch root is removed when the last photo's workdir
    is swept."""
    processing = sample_run.processing
    assert list(processing.rglob(f"*{execute.PARTIAL_SUFFIX}")) == []
    assert not (processing / cli.WORKROOT_NAME).exists()


def test_the_over_cap_photo_falls_back_to_per_plan_upscaling(sample_run):
    """bokeh_nature_scene_7629.jpg is the sample's over-cap photo: 4000x6000
    is 384 Mpx of 4x output against a 300 Mpx cap, and every plan of its that
    needs the model has a crop rect to be bounded by, so the executor slices
    before it enlarges. Spec 13.2 names green_grass_texture_3997.png here,
    which is wrong -- that photo is band 1 on both devices and never asks for
    the model at all.

    Checked through stdout because that line is the only account a long run
    gives of a photo whose timing is unlike every other photo's.
    """
    lines = [line for line in sample_run.stdout.splitlines()
             if "falling back to per-plan upscaling" in line]
    assert len(lines) == 1, lines
    assert OVER_CAP in lines[0]


def test_the_upscale_path_converts_a_wide_gamut_source_to_srgb(sample_run):
    """red_tulips_with_mountain_background_4338.jpg is Adobe RGB and lands in
    band 3 on every plan, so all four of its outputs come back through the
    model -- which emits PNG with no ICC chunk. Without `--matchTo` the encode
    would tag sRGB over numbers that were never in sRGB, at exit 0, with
    correct dimensions and nothing to see.

    This is the sample's colour-conversion case. It is NOT
    moss_with_pine_needles_5324.jpg, the ProPhoto RGB photograph the spec
    names: that one is band 2 on both devices under these targets, so it never
    reaches the path that converts. See the test below.
    """
    outputs = [p for p in _outputs(sample_run.processing)
               if p.name.startswith(WIDE_GAMUT_UPSCALED)]
    assert len(outputs) == 4, [p.name for p in outputs]
    for path in outputs:
        assert "sRGB" in (_profile(path) or ""), \
            f"{path.name} kept a non-sRGB profile"


def test_an_untouched_source_keeps_the_profile_it_arrived_with(sample_run):
    """moss_with_pine_needles_5324.jpg is ProPhoto RGB and band 2 everywhere:
    its pixels are cropped and encoded, never resampled and never sent to the
    model. Its outputs are still ProPhoto RGB -- measured.

    That follows from where `normalize_to_srgb_png` is called. It runs on the
    upscale path and only there, because the reason it exists is that
    `upscayl-bin` strips the profile; bands 1 and 2 keep both the numbers and
    the tag that describes them, so the file is colour-correct without it.

    Pinned as an observation, not defended as a requirement. If wallpapers are
    wanted in sRGB whatever their band, this is the test that should fail
    first and the decision belongs in the spec, which currently says only that
    the UPSCALE path must convert.
    """
    outputs = [p for p in _outputs(sample_run.processing)
               if p.name.startswith(WIDE_GAMUT_UNTOUCHED)]
    assert len(outputs) == 4, [p.name for p in outputs]
    assert {NAME.search(p.name)["factor"] for p in outputs} == {"native"}
    for path in outputs:
        assert "ProPhoto" in (_profile(path) or ""), \
            f"{path.name} no longer carries its source profile"
