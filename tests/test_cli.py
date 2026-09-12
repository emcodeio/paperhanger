"""The command line: parsing, the guard, collisions, dry-run, and the batch.

Fixture sizes, all checked against section 5 before being written (global
constraint 13), and all on the phone path because its targets are smallest:

  2000x3000  phone band 2  native, no model run, one plan
             desktop band 3, three horizontal slices (used for -d only)
  480x720    phone band 4  4x -> 1920x2880, needs the model; desktop rejects it
  100x100    rejected on both devices

Flat fill throughout: the largest is 6 Mpx, well above the ~1 Mpx where
`noise=True` stops being affordable, and nothing here measures encoder
behaviour.
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from paperhanger import cli, execute, imaging, sizes, toolchain
from tests.pngwriter import write_png

# The same handful of sizes is wanted by two dozen tests, and the 6 Mpx one
# costs about a quarter second to generate. Generated once, written from bytes
# thereafter.
_PNG_CACHE: dict[tuple[int, int], bytes] = {}


def fixture(path, width: int, height: int) -> Path:
    key = (width, height)
    if key not in _PNG_CACHE:
        scratch = Path(tempfile.mkdtemp(prefix="paperhanger-fixture-"))
        try:
            _PNG_CACHE[key] = write_png(scratch / "f.png", width, height).read_bytes()
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    path = Path(path)
    path.write_bytes(_PNG_CACHE[key])
    return path


def run(argv, capsys):
    """(exit code, stdout). Everything the CLI says goes to stdout, argparse's
    complaints included -- a user reading a 24-hour run's progress should not
    have to merge two streams to find out why it stopped."""
    code = cli.main(argv)
    return code, capsys.readouterr().out


def summary_line(out: str) -> str:
    """The closing `done:` / `interrupted:` line, isolated.

    Asserted against by name rather than against the whole of stdout, because
    the report header printed above it carries its own "N rejected" and "N
    already done" -- so a summary that had stopped counting either would hide
    behind the header and the test would still be green. Found by mutation:
    `rejected = counts[execute.REJECTED]` survived until this existed.
    """
    prefixes = (cli.SUMMARY_DONE, cli.SUMMARY_INTERRUPTED)
    return next(line for line in out.splitlines() if line.startswith(prefixes))


def binary_line(out: str) -> str:
    """doctor's `upscayl-bin` line, isolated.

    Same class of hole as `summary_line`, and it was live: `_doctor` prints
    "(what `paperhanger setup` installs)" on the pinned-release line
    UNCONDITIONALLY, so `assert "paperhanger setup" in out` passed even with
    the entire NOT FOUND branch replaced by a bare "upscayl-bin: absent". And
    `code == 0` discriminates nothing -- doctor always exits 0.
    """
    return next(line for line in out.splitlines()
                if line.startswith(cli.BINARY_LABEL))


def error_block(out: str) -> str:
    """Everything the CLI said about the error, and nothing after it.

    `parser.format_usage()` lists every flag the parser has, `[--allow-nested]`
    among them, and some error paths print it. A guard message that had
    stopped naming the flag it exists to advertise would still leave the word
    in stdout, so the guard tests read this rather than the whole stream.
    """
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("error:"))
    for offset, line in enumerate(lines[start + 1:], start=start + 1):
        if line.startswith("usage:"):
            return "\n".join(lines[start:offset])
    return "\n".join(lines[start:])


@pytest.fixture
def inbox(tmp_path):
    directory = tmp_path / "in"
    directory.mkdir()
    return directory


@pytest.fixture
def no_upscaler(tmp_path, monkeypatch):
    """A machine where upscayl-bin is not installed."""
    monkeypatch.setenv("PAPERHANGER_UPSCAYL_BIN", str(tmp_path / "nowhere"))


# `ready_toolchain` lives in conftest.py: Tier 2 needs the same arrangement,
# and a second copy of a fixture that fakes half the toolchain is how the two
# halves drift apart.


DOOMED_WRAPPER = """#!/usr/bin/env python3
# The conftest fake, wrapped so one photo's enlargement always fails. Wrapped
# rather than copied: the real fake enforces the jpg/png/webp input rule the
# executor's normalize step exists for, and a second copy would drift from it.
#
# run_photo names each photo's workdir after the source stem and hands the
# model a file inside it, so the doomed photo is identifiable from -i alone.
import os
import subprocess
import sys
from pathlib import Path

args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
if Path(args["-i"]).parent.name == os.environ["PAPERHANGER_DOOMED"]:
    sys.stderr.write("fake upscayl: this photo always fails\\n")
    sys.exit(1)
sys.exit(subprocess.run([{real!r}] + sys.argv[1:]).returncode)
"""


@pytest.fixture
def doomed_upscaler(tmp_path, fake_upscaler, ready_toolchain, monkeypatch):
    real, _models = fake_upscaler
    wrapper = tmp_path / "doomed-upscayl-bin"
    wrapper.write_text(DOOMED_WRAPPER.format(real=str(real)))
    wrapper.chmod(0o755)
    monkeypatch.setenv("PAPERHANGER_UPSCAYL_BIN", str(wrapper))

    def doom(stem: str):
        monkeypatch.setenv("PAPERHANGER_DOOMED", stem)

    return doom


# ---------- dry run ----------

def test_dry_run_writes_nothing(inbox, tmp_path, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    processing = tmp_path / "processing"
    code, out = run(["--dry-run", "--processing-dir", str(processing),
                     str(inbox)], capsys)
    assert code == 0
    assert "1 image," in out
    assert not processing.exists(), "--dry-run must not create the processing tree"


def test_dry_run_names_the_outputs_it_would_write(inbox, tmp_path, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    code, out = run(["-p", "--dry-run", "--processing-dir", str(tmp_path / "p"),
                     str(inbox)], capsys)
    assert code == 0
    assert "lichen.png" in out
    assert "phone/height" in out
    assert "3000 -> 2000x3000  native  below_target" in out


def test_non_images_are_counted_not_planned(inbox, tmp_path, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    (inbox / "notes.txt").write_text("not an image")
    code, out = run(["--dry-run", "--processing-dir", str(tmp_path / "p"),
                     str(inbox)], capsys)
    assert code == 0
    assert "1 image," in out
    assert "1 non-image skipped" in out
    assert "notes.txt" not in out


def test_a_subdirectory_is_not_counted_as_a_non_image(inbox, tmp_path, capsys):
    """--allow-nested puts the processing tree inside the scan. It is a
    directory, so it is neither probed nor tallied as a skipped file."""
    fixture(inbox / "lichen.png", 2000, 3000)
    (inbox / "processing").mkdir()
    code, out = run(["--dry-run", "--allow-nested", "--processing-dir",
                     str(inbox / "processing"), str(inbox)], capsys)
    assert code == 0
    assert "0 non-images skipped" in out


def test_a_single_file_is_accepted(inbox, tmp_path, capsys):
    photo = fixture(inbox / "lichen.png", 2000, 3000)
    code, out = run(["--dry-run", "--processing-dir", str(tmp_path / "p"),
                     str(photo)], capsys)
    assert code == 0
    assert "1 image," in out


def test_a_finished_photo_is_reported_already_done(inbox, tmp_path, capsys):
    """`finished_outputs` is a SET OF DESTINATIONS, not a count: a count can
    only ever inflate the header, and it cannot tell a photo three-quarters
    done from one not started."""
    fixture(inbox / "lichen.png", 2000, 3000)
    processing = tmp_path / "p"
    destination = (processing / "to_sort_phone" / "below_target"
                   / "lichen_phone_2000x3000_native.heic")
    destination.parent.mkdir(parents=True)
    destination.touch()
    code, out = run(["-p", "--dry-run", "--processing-dir", str(processing),
                     str(inbox)], capsys)
    assert code == 0
    assert "1 already done" in out
    assert "already done, skipping" in out


def test_a_rejected_photo_is_not_reported_already_done(inbox, tmp_path, capsys):
    """A photo rejected on every device has no plans at all, so "nothing of
    it is left to render" is vacuously true -- and without the `w.plans and`
    guard in `render_report` it is called finished while the header two lines
    up counts it as rejected."""
    fixture(inbox / "tiny.png", 100, 100)
    code, out = run(["--dry-run", "--processing-dir", str(tmp_path / "p"),
                     str(inbox)], capsys)
    assert code == 0
    assert "1 rejected" in out
    assert "0 already done" in out
    assert "already done, skipping" not in out


def test_overwrite_empties_the_already_done_set(inbox, tmp_path, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    processing = tmp_path / "p"
    destination = (processing / "to_sort_phone" / "below_target"
                   / "lichen_phone_2000x3000_native.heic")
    destination.parent.mkdir(parents=True)
    destination.touch()
    code, out = run(["-p", "--dry-run", "--overwrite", "--processing-dir",
                     str(processing), str(inbox)], capsys)
    assert code == 0
    assert "0 already done" in out
    assert "1 output" in out


# ---------- devices ----------

def test_desktop_only(inbox, tmp_path, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    code, out = run(["-d", "--dry-run", "--processing-dir", str(tmp_path / "p"),
                     str(inbox)], capsys)
    assert code == 0
    assert "desktop" in out
    assert "phone" not in out


def test_phone_only(inbox, tmp_path, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    code, out = run(["-p", "--dry-run", "--processing-dir", str(tmp_path / "p"),
                     str(inbox)], capsys)
    assert code == 0
    assert "phone" in out
    assert "desktop" not in out


def test_both_is_the_default(inbox, tmp_path, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    _, defaulted = run(["--dry-run", "--processing-dir", str(tmp_path / "p"),
                        str(inbox)], capsys)
    _, explicit = run(["-b", "--dry-run", "--processing-dir", str(tmp_path / "p"),
                       str(inbox)], capsys)
    assert defaulted == explicit
    assert "desktop" in defaulted and "phone" in defaulted


def test_the_device_flags_are_mutually_exclusive(inbox, tmp_path, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    code, out = run(["-d", "-p", "--dry-run", "--processing-dir",
                     str(tmp_path / "p"), str(inbox)], capsys)
    assert code == 1
    assert "not allowed with" in out


def test_resolve_devices_deduplicates():
    """find_collisions dedupes the sources claiming one destination, so two
    copies of a device inside one photo read as one source and the
    self-collision is invisible. -d/-p/-b cannot make one; the list is
    deduplicated where it is built so nothing downstream has to care."""
    assert cli.resolve_devices([sizes.PHONE, sizes.PHONE]) == [sizes.PHONE]
    assert cli.resolve_devices(None) == list(sizes.DEVICES)
    assert cli.resolve_devices([sizes.PHONE, sizes.DESKTOP]) == [sizes.PHONE,
                                                                 sizes.DESKTOP]


# ---------- formats ----------

def test_quality_with_png_is_rejected(inbox, tmp_path, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    code, out = run(["--format", "png", "--quality", "90", "--dry-run",
                     "--processing-dir", str(tmp_path / "p"), str(inbox)], capsys)
    assert code == 1
    assert "lossless" in out


def test_quality_out_of_range_is_rejected(inbox, tmp_path, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    code, out = run(["--quality", "150", "--dry-run", "--processing-dir",
                     str(tmp_path / "p"), str(inbox)], capsys)
    assert code == 1
    assert "0-100" in out


@pytest.mark.parametrize("fmt,extension", [("heic", "heic"), ("jpeg", "jpg"),
                                           ("avif", "avif"), ("png", "png")])
def test_every_format_plans(inbox, tmp_path, capsys, fmt, extension):
    fixture(inbox / "lichen.png", 2000, 3000)
    code, out = run(["-p", "--format", fmt, "--dry-run", "--processing-dir",
                     str(tmp_path / "p"), str(inbox)], capsys)
    assert code == 0
    assert "1 output" in out
    assert cli.formats.extension(fmt) == extension


def test_an_unknown_format_is_a_usage_error(inbox, tmp_path, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    code, out = run(["--format", "bmp", "--dry-run", "--processing-dir",
                     str(tmp_path / "p"), str(inbox)], capsys)
    assert code == 1, "argparse's own exit 2 would read as 'some photos failed'"
    assert "bmp" in out


# ---------- usage ----------

def test_missing_path_is_a_usage_error(capsys):
    code, out = run([], capsys)
    assert code == 1
    assert "required" in out


def test_a_path_that_does_not_exist_is_a_usage_error(tmp_path, capsys):
    code, out = run(["--dry-run", str(tmp_path / "absent")], capsys)
    assert code == 1
    assert "does not exist" in out


def test_an_unknown_flag_is_a_usage_error(inbox, tmp_path, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    code, out = run(["--turbo", "--dry-run", "--processing-dir",
                     str(tmp_path / "p"), str(inbox)], capsys)
    assert code == 1
    assert "--turbo" in out


def test_a_subcommand_does_not_silently_ignore_its_arguments(capsys):
    code, out = run(["doctor", "/tmp/somewhere"], capsys)
    assert code == 1
    assert "doctor" in out


# ---------- the guard ----------

def test_guard_refuses_a_parent_of_the_processing_dir(tmp_path, capsys):
    pictures = tmp_path / "pictures"
    pictures.mkdir()
    fixture(pictures / "lichen.png", 2000, 3000)
    processing = pictures / "processing"
    code, out = run(["--dry-run", "--processing-dir", str(processing),
                     str(pictures)], capsys)
    assert code == 1
    assert "--allow-nested" in error_block(out)


def test_allow_nested_permits_it(tmp_path, capsys):
    pictures = tmp_path / "pictures"
    pictures.mkdir()
    fixture(pictures / "lichen.png", 2000, 3000)
    code, out = run(["--dry-run", "--allow-nested", "--processing-dir",
                     str(pictures / "processing"), str(pictures)], capsys)
    assert code == 0


def test_guard_refuses_the_processing_dir_itself(tmp_path, capsys):
    processing = tmp_path / "processing"
    processing.mkdir()
    fixture(processing / "lichen.png", 2000, 3000)
    code, out = run(["--dry-run", "--processing-dir", str(processing),
                     str(processing)], capsys)
    assert code == 1
    assert "--allow-nested" in error_block(out)


def test_guard_refuses_originals(tmp_path, capsys):
    processing = tmp_path / "processing"
    originals = processing / "originals"
    originals.mkdir(parents=True)
    fixture(originals / "lichen.png", 2000, 3000)
    code, out = run(["--dry-run", "--processing-dir", str(processing),
                     str(originals)], capsys)
    assert code == 1
    assert "--allow-nested" in error_block(out)


def test_guard_refuses_a_file_inside_the_processing_dir(tmp_path, capsys):
    processing = tmp_path / "processing"
    originals = processing / "originals"
    originals.mkdir(parents=True)
    photo = fixture(originals / "lichen.png", 2000, 3000)
    code, out = run(["--dry-run", "--processing-dir", str(processing),
                     str(photo)], capsys)
    assert code == 1
    assert "--allow-nested" in error_block(out)


def test_guard_allows_a_file_beside_the_processing_dir(tmp_path, capsys):
    """A single file names exactly one image; no walk can reach the outputs,
    so the nesting that condemns the DIRECTORY is harmless here."""
    pictures = tmp_path / "pictures"
    pictures.mkdir()
    photo = fixture(pictures / "lichen.png", 2000, 3000)
    code, out = run(["--dry-run", "--processing-dir", str(pictures / "processing"),
                     str(photo)], capsys)
    assert code == 0


def test_the_guard_resolves_symlinks(tmp_path, capsys):
    """`~/Pictures` is a symlink on some machines, and a guard comparing the
    unresolved strings would wave the nested layout straight through."""
    pictures = tmp_path / "pictures"
    pictures.mkdir()
    fixture(pictures / "lichen.png", 2000, 3000)
    link = tmp_path / "link"
    link.symlink_to(pictures)
    code, out = run(["--dry-run", "--processing-dir", str(pictures / "processing"),
                     str(link)], capsys)
    assert code == 1
    assert "--allow-nested" in error_block(out)


# ---------- collisions ----------

def test_collision_aborts_and_names_both_sources(inbox, tmp_path, capsys):
    seed = fixture(tmp_path / "seed.png", 2000, 3000)
    fixture(inbox / "city.png", 2000, 3000)
    imaging.resize_and_encode(seed, 2000, 3000, "jpeg", 90,
                              inbox / "city.jpg", resize=False)
    code, out = run(["-p", "--dry-run", "--processing-dir", str(tmp_path / "p"),
                     str(inbox)], capsys)
    assert code == 1
    assert "city.jpg" in out and "city.png" in out


def test_a_collision_is_found_before_the_toolchain_is_demanded(
        inbox, tmp_path, no_upscaler, capsys):
    """Both errors are fatal; only one of them is the user's actual problem."""
    seed = fixture(tmp_path / "seed.png", 480, 720)
    fixture(inbox / "city.png", 480, 720)
    imaging.resize_and_encode(seed, 480, 720, "jpeg", 90,
                              inbox / "city.jpg", resize=False)
    code, out = run(["-p", "--processing-dir", str(tmp_path / "p"),
                     str(inbox)], capsys)
    assert code == 1
    assert "city.jpg" in out and "city.png" in out
    assert "PAPERHANGER_UPSCAYL_BIN" not in out


# ---------- the toolchain check ----------

def test_a_missing_upscaler_fails_before_anything_is_processed(
        inbox, tmp_path, no_upscaler, capsys):
    fixture(inbox / "small.png", 480, 720)
    processing = tmp_path / "processing"
    code, out = run(["-p", "--processing-dir", str(processing), str(inbox)], capsys)
    assert code == 1
    assert "PAPERHANGER_UPSCAYL_BIN" in out
    assert "[1/1]" not in out, "a photo was processed before the binary was checked"
    assert not processing.exists()
    assert (inbox / "small.png").exists()


def test_no_upscaler_is_demanded_when_nothing_needs_one(
        inbox, tmp_path, no_upscaler, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    processing = tmp_path / "processing"
    code, out = run(["-p", "--format", "png", "--processing-dir", str(processing),
                     str(inbox)], capsys)
    assert code == 0
    assert (processing / "to_sort_phone" / "below_target"
            / "lichen_phone_2000x3000_native.png").exists()


def test_no_upscaler_is_demanded_when_its_outputs_already_exist(
        inbox, tmp_path, no_upscaler, capsys):
    """A resumed run whose remaining work is all band 1 or 2 needs no binary.
    Asking per PHOTO rather than per remaining PLAN stops it anyway."""
    fixture(inbox / "small.png", 480, 720)
    processing = tmp_path / "processing"
    destination = (processing / "to_sort_phone" / "below_target"
                   / "small_phone_1920x2880_4x.png")
    destination.parent.mkdir(parents=True)
    destination.touch()
    code, out = run(["-p", "--format", "png", "--processing-dir", str(processing),
                     str(inbox)], capsys)
    assert code == 0
    assert (processing / "originals" / "small.png").exists()


def test_overwrite_demands_the_upscaler_again(
        inbox, tmp_path, no_upscaler, capsys):
    fixture(inbox / "small.png", 480, 720)
    processing = tmp_path / "processing"
    destination = (processing / "to_sort_phone" / "below_target"
                   / "small_phone_1920x2880_4x.png")
    destination.parent.mkdir(parents=True)
    destination.touch()
    code, out = run(["-p", "--format", "png", "--overwrite", "--processing-dir",
                     str(processing), str(inbox)], capsys)
    assert code == 1
    assert "PAPERHANGER_UPSCAYL_BIN" in out


# ---------- the sips pre-flight ----------

@pytest.fixture
def no_sips(tmp_path, monkeypatch):
    """A machine whose sips is missing, quarantined, or not where it was."""
    monkeypatch.setattr(imaging, "SIPS", str(tmp_path / "no" / "such" / "sips"))


def test_a_missing_sips_stops_the_run_rather_than_emptying_it(
        inbox, tmp_path, no_sips, capsys):
    """One sentence instead of a lie about the user's whole library.

    `probe` returns None for anything it cannot measure and never raises,
    which is correct -- it is how a .DS_Store is told from a photograph. The
    cost is that a missing sips is indistinguishable from a directory of
    files that are none of them images.
    """
    fixture(inbox / "lichen.png", 2000, 3000)

    code, out = run([str(inbox), "--processing-dir", str(tmp_path / "proc")], capsys)

    assert code == cli.USAGE_ERROR
    assert imaging.SIPS in error_block(out)
    assert "0 images" not in out, \
        "the report header must not describe the photograph as unreadable"


def test_without_the_check_a_missing_sips_reports_the_library_as_unreadable(
        inbox, tmp_path, no_sips, monkeypatch, capsys):
    """Guards the guard: what the check above is preventing, measured.

    With `check_sips` neutered the run scans the directory, measures nothing,
    classifies the photograph as a non-image, plans no work, writes no file
    and exits 0 -- telling the user their photographs are corrupt. Without
    this test the assertion above would pass equally well against a pre-flight
    that refused every run for some unrelated reason.
    """
    fixture(inbox / "lichen.png", 2000, 3000)
    monkeypatch.setattr(cli, "check_sips", lambda: None)

    code, out = run([str(inbox), "--processing-dir", str(tmp_path / "proc")], capsys)

    assert code == cli.OK
    assert "0 images" in out and "1 non-image" in out
    assert not (tmp_path / "proc" / "to_sort_phone").exists()


def test_a_missing_sips_is_refused_before_the_directory_is_read(
        tmp_path, no_sips, capsys):
    """The complaint is about sips, not about the input.

    An unreadable directory and a missing sips both end the run at exit 1, and
    only one of them is the user's actual problem -- so the check runs first
    and the message says which.
    """
    inbox = tmp_path / "in"
    inbox.mkdir()
    fixture(inbox / "lichen.png", 2000, 3000)
    inbox.chmod(0o000)
    try:
        code, out = run([str(inbox), "--processing-dir", str(tmp_path / "proc")],
                        capsys)
    finally:
        inbox.chmod(0o755)

    assert code == cli.USAGE_ERROR
    assert imaging.SIPS in error_block(out)
    assert "cannot read" not in out


def test_doctor_and_the_run_read_the_same_sips(tmp_path, no_sips, capsys):
    """Both ask `sips_is_present`, so neither can start saying something the
    other does not. doctor reported this before `_run` did."""
    code, out = run(["doctor"], capsys)

    assert code == cli.OK
    assert "MISSING" in next(line for line in out.splitlines()
                             if line.startswith("sips"))
    assert cli.check_sips() is not None


# ---------- skip-existing, decided once ----------

def test_is_pending_answers_the_four_combinations(tmp_path):
    """An output on disk is skipped unless --overwrite says otherwise."""
    import types

    absent = types.SimpleNamespace(destination=tmp_path / "gone.heic")
    present = types.SimpleNamespace(destination=tmp_path / "there.heic")
    present.destination.write_text("an earlier run's output")

    assert execute.is_pending(absent) is True
    assert execute.is_pending(absent, overwrite=True) is True
    assert execute.is_pending(present) is False
    assert execute.is_pending(present, overwrite=True) is True


@pytest.mark.parametrize("verdict,done,needs_model,renders", [
    (True, False, True, True),
    (False, True, False, False),
])
def test_every_skip_existing_decision_goes_through_one_function(
        inbox, tmp_path, ready_toolchain, monkeypatch,
        verdict, done, needs_model, renders):
    """The report, the toolchain pre-flight and the executor, all one call.

    Skip-existing used to be spelled out three times: `already_done` decides
    what the dry-run PROMISES, `upscaler_is_needed` decides whether the run is
    allowed to start at all, and `run_and_archive` decides what it DOES. They
    agreed, but nothing made them: a change to one produced a dry-run that
    lied about its own run, which is the failure the plan-then-execute split
    exists to rule out.

    Replacing `is_pending` wholesale is what makes that checkable. All three
    must follow it, in both directions -- so this fails if any of them goes
    back to asking the filesystem for itself.
    """
    from paperhanger import plan

    source = fixture(inbox / "sunset.png", 480, 720)
    opts = plan.OutputSettings(tmp_path / "proc", "png", None)
    works = [plan.plan_photo(source, 480, 720, "png", [sizes.PHONE], opts)]
    assert works[0].needs_upscale

    monkeypatch.setattr(execute, "is_pending",
                        lambda target, overwrite=False: verdict)

    finished = cli.finished_outputs(works, False)
    assert all(p.destination in finished for p in works[0].plans) is done
    assert cli.upscaler_is_needed(works, False) is needs_model

    binary, models = ready_toolchain, ready_toolchain
    ctx = execute.Context(processing_dir=tmp_path / "proc",
                          workroot=tmp_path / "work",
                          upscayl=toolchain.find_upscayl(), models_dir=models)
    result = execute.run_and_archive(works[0], ctx)

    assert bool(result.written) is renders
    assert bool(result.skipped) is not renders


# ---------- setup and doctor ----------

def test_doctor_reports_without_failing(capsys):
    code, out = run(["doctor"], capsys)
    assert code == 0
    assert "upscayl" in out.lower()
    assert "sips" in out.lower()


def test_doctor_names_where_the_binary_came_from(tmp_path, monkeypatch, capsys):
    binary = tmp_path / "somebody-elses-upscayl"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setenv("PAPERHANGER_UPSCAYL_BIN", str(binary))
    code, out = run(["doctor"], capsys)
    assert code == 0
    assert str(binary) in binary_line(out)
    assert "PAPERHANGER_UPSCAYL_BIN" in binary_line(out)


def test_doctor_does_not_claim_the_found_binary_is_the_pinned_release(
        tmp_path, monkeypatch, capsys):
    """status()['release'] is the tag `setup` DOWNLOADS. A binary from PATH or
    from the env var can be any build, and nothing here reads its version."""
    binary = tmp_path / "somebody-elses-upscayl"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setenv("PAPERHANGER_UPSCAYL_BIN", str(binary))
    _, out = run(["doctor"], capsys)
    release_line = next(line for line in out.splitlines()
                        if toolchain.UPSCAYL_RELEASE in line)
    assert "pinned" in release_line.lower()
    assert str(binary) not in release_line


def test_doctor_says_when_the_binary_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PAPERHANGER_UPSCAYL_BIN", str(tmp_path / "nowhere"))
    code, out = run(["doctor"], capsys)
    assert code == 0
    assert "NOT FOUND" in binary_line(out)
    assert "paperhanger setup" in binary_line(out)


def test_setup_reports_a_toolchain_error(monkeypatch, capsys):
    def explode(*args, **kwargs):
        raise toolchain.ToolchainError("sha256 mismatch for the release zip")

    monkeypatch.setattr(cli.toolchain, "setup", explode)
    code, out = run(["setup"], capsys)
    assert code == 1
    assert "sha256 mismatch" in out


def test_setup_wraps_an_error_it_does_not_own(monkeypatch, capsys):
    """Extraction errors leave `toolchain.setup` raw -- BadZipFile, OSError,
    and since the chmod moved outside the install branch, PermissionError on a
    binary whose mode cannot be changed. A traceback is not an error message,
    and this is the first command the tool tells a new user to run."""
    def explode(*args, **kwargs):
        raise PermissionError(13, "Operation not permitted")

    monkeypatch.setattr(cli.toolchain, "setup", explode)
    code, out = run(["setup"], capsys)
    assert code == 1
    assert "PermissionError" in out
    assert "paperhanger doctor" in out


def test_setup_reports_success(monkeypatch, capsys):
    monkeypatch.setattr(cli.toolchain, "setup", lambda *a, **k: toolchain.status())
    code, out = run(["setup"], capsys)
    assert code == 0
    assert "ready" in out


# ---------- the run ----------

def test_end_to_end_run(inbox, tmp_path, ready_toolchain, capsys):
    fixture(inbox / "cliffs.png", 480, 720)
    processing = tmp_path / "processing"
    code, out = run(["-p", "--format", "png", "--processing-dir", str(processing),
                     str(inbox)], capsys)
    assert code == 0, out
    written = list((processing / "to_sort_phone" / "below_target").glob("*.png"))
    assert [p.name for p in written] == ["cliffs_phone_1920x2880_4x.png"]
    assert imaging.probe(written[0])[:2] == (1920, 2880)
    assert (processing / "originals" / "cliffs.png").exists()
    assert not (inbox / "cliffs.png").exists(), "originals are moved, not copied"
    assert "1 ok" in summary_line(out)


def test_the_run_leaves_no_scratch_behind(inbox, tmp_path, ready_toolchain, capsys):
    fixture(inbox / "cliffs.png", 480, 720)
    processing = tmp_path / "processing"
    code, _ = run(["-p", "--format", "png", "--processing-dir", str(processing),
                   str(inbox)], capsys)
    assert code == 0
    assert not (processing / cli.WORKROOT_NAME).exists()


def test_stray_partials_are_swept(inbox, tmp_path, no_upscaler, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    processing = tmp_path / "processing"
    stray = processing / "to_sort_phone" / "below_target" / "old.heic.partial"
    stray.parent.mkdir(parents=True)
    stray.touch()
    code, out = run(["-p", "--format", "png", "--processing-dir", str(processing),
                     str(inbox)], capsys)
    assert code == 0
    assert not stray.exists()
    assert "swept 1" in out


def test_an_existing_output_is_not_regenerated(inbox, tmp_path, no_upscaler, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    processing = tmp_path / "processing"
    destination = (processing / "to_sort_phone" / "below_target"
                   / "lichen_phone_2000x3000_native.png")
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"an earlier run's output")
    code, out = run(["-p", "--format", "png", "--processing-dir", str(processing),
                     str(inbox)], capsys)
    assert code == 0
    assert destination.read_bytes() == b"an earlier run's output"
    assert (processing / "originals" / "lichen.png").exists()
    assert "1 already done" in summary_line(out)


def test_overwrite_regenerates_it(inbox, tmp_path, no_upscaler, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    processing = tmp_path / "processing"
    destination = (processing / "to_sort_phone" / "below_target"
                   / "lichen_phone_2000x3000_native.png")
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"an earlier run's output")
    code, out = run(["-p", "--format", "png", "--overwrite", "--processing-dir",
                     str(processing), str(inbox)], capsys)
    assert code == 0
    assert imaging.probe(destination)[:2] == (2000, 3000)


def test_allow_nested_does_not_disable_skip_existing(tmp_path, no_upscaler, capsys):
    """They were one --force in an earlier draft: the flag required to process
    the default nested layout also turned off skip-existing, so resuming an
    interrupted 24-hour import re-upscaled everything already finished."""
    pictures = tmp_path / "pictures"
    pictures.mkdir()
    fixture(pictures / "lichen.png", 2000, 3000)
    processing = pictures / "processing"
    destination = (processing / "to_sort_phone" / "below_target"
                   / "lichen_phone_2000x3000_native.png")
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"an earlier run's output")
    code, out = run(["-p", "--format", "png", "--allow-nested", "--processing-dir",
                     str(processing), str(pictures)], capsys)
    assert code == 0
    assert destination.read_bytes() == b"an earlier run's output"


def test_a_rejected_photo_goes_to_error_and_the_run_succeeds(
        inbox, tmp_path, capsys):
    fixture(inbox / "tiny.png", 100, 100)
    processing = tmp_path / "processing"
    code, out = run(["--processing-dir", str(processing), str(inbox)], capsys)
    assert code == 0
    assert (processing / "error" / "tiny.png").exists()
    assert "1 rejected" in summary_line(out)


def test_a_rejected_photo_whose_archive_fails_is_still_counted_rejected(
        inbox, tmp_path, capsys):
    """Its outcome cannot be REJECTED, because the move it needed did not
    happen. Reading rejection from the outcome would lose the one fact that
    explains why the photo produced nothing, so the summary reads it from the
    plan instead and reports the photo under both headings.

    PARTIAL, not FAILED. Nothing about the photo failed: it was measured,
    classified and turned down, and only the move into error/ went wrong.
    FAILED describes a photo sips could not read, which is a different
    problem with a different fix. The line beside it names error/ rather than
    just "archive", which is the only thing left saying the photo was
    rejected rather than processed.
    """
    fixture(inbox / "tiny.png", 100, 100)
    processing = tmp_path / "processing"
    processing.mkdir()
    (processing / "error").write_text("a file where the directory should be")
    code, out = run(["--processing-dir", str(processing), str(inbox)], capsys)
    assert code == 2
    assert "1 rejected" in summary_line(out)
    assert "1 failed" in summary_line(out)
    assert "[1/1] tiny.png: partial" in out
    assert "could not move tiny.png to error/" in out
    assert (inbox / "tiny.png").exists(), "a failed archive leaves the source alone"


def test_a_photo_the_upscaler_chokes_on_does_not_end_the_batch(
        inbox, tmp_path, doomed_upscaler, capsys):
    """_upscale_whole_frame is called OUTSIDE run_photo's per-plan try, so its
    ImagingError travels straight through run_and_archive. Uncaught here, one
    bad photo ends a twenty-four-hour import at photo three."""
    doomed_upscaler("bad")
    fixture(inbox / "bad.png", 480, 720)
    fixture(inbox / "good.png", 480, 720)
    processing = tmp_path / "processing"
    code, out = run(["-p", "--format", "png", "--processing-dir", str(processing),
                     str(inbox)], capsys)
    assert code == 2
    assert (processing / "to_sort_phone" / "below_target"
            / "good_phone_1920x2880_4x.png").exists()
    assert (processing / "originals" / "good.png").exists()
    assert (inbox / "bad.png").exists(), "a failed photo stays where it was found"
    assert "bad.png" in out
    assert "1 ok" in summary_line(out) and "1 failed" in summary_line(out)


def test_a_failure_says_why_beside_the_photo_it_happened_to(
        inbox, tmp_path, doomed_upscaler, capsys):
    """A 24-hour import is read as a log. "why did photo 340 fail" is a
    question answered beside photo 340, not only in a recap at the end."""
    doomed_upscaler("bad")
    fixture(inbox / "bad.png", 480, 720)
    fixture(inbox / "good.png", 480, 720)
    code, out = run(["-p", "--format", "png", "--processing-dir",
                     str(tmp_path / "processing"), str(inbox)], capsys)
    assert code == 2
    lines = out.splitlines()
    progress = next(i for i, line in enumerate(lines) if "bad.png: failed" in line)
    assert "exited 1" in lines[progress + 1]
    assert lines.index("left in place for a re-run:") > progress


def test_an_unreadable_directory_is_a_usage_error(tmp_path, capsys):
    locked = tmp_path / "locked"
    locked.mkdir()
    fixture(locked / "lichen.png", 2000, 3000)
    locked.chmod(0o000)
    try:
        code, out = run(["--dry-run", "--processing-dir", str(tmp_path / "p"),
                         str(locked)], capsys)
    finally:
        locked.chmod(0o755)
    assert code == 1
    assert "cannot read" in out


def test_a_routing_bug_is_not_swallowed(inbox, tmp_path, no_upscaler, monkeypatch):
    """_upscale_one_plan raises ValueError for a cropless plan -- a fact about
    this codebase, not about the photo. Tallied as "one photo failed" it would
    look exactly like a corrupt JPEG and repeat, quietly, for every photo."""
    def routing_bug(work, ctx):
        raise ValueError("_upscale_one_plan requires a crop plan")

    monkeypatch.setattr(cli.execute, "run_and_archive", routing_bug)
    fixture(inbox / "lichen.png", 2000, 3000)
    with pytest.raises(ValueError, match="requires a crop plan"):
        cli.main(["-p", "--format", "png", "--processing-dir",
                  str(tmp_path / "processing"), str(inbox)])


def test_the_report_is_printed_before_the_work_starts(
        inbox, tmp_path, no_upscaler, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    code, out = run(["-p", "--format", "png", "--processing-dir",
                     str(tmp_path / "processing"), str(inbox)], capsys)
    assert code == 0
    assert out.index("1 image,") < out.index("[1/1]")


def test_progress_counts_every_photo(inbox, tmp_path, no_upscaler, capsys):
    for name in ("a.png", "b.png", "c.png"):
        fixture(inbox / name, 2000, 3000)
    code, out = run(["-p", "--format", "png", "--processing-dir",
                     str(tmp_path / "processing"), str(inbox)], capsys)
    assert code == 0
    for index in (1, 2, 3):
        assert f"[{index}/3]" in out
    assert "3 ok" in summary_line(out)


def test_cheap_photos_run_first(inbox, tmp_path, ready_toolchain, capsys):
    """A Ctrl-C at any point should leave as many wallpapers behind as
    possible, so the photos needing no model run go first."""
    fixture(inbox / "a_needs_the_model.png", 480, 720)
    fixture(inbox / "z_native.png", 2000, 3000)
    code, out = run(["-p", "--format", "png", "--processing-dir",
                     str(tmp_path / "processing"), str(inbox)], capsys)
    assert code == 0
    assert out.index("z_native.png:") < out.index("a_needs_the_model.png:")


# ---------- Ctrl-C ----------

def interrupt_on(monkeypatch, stem: str):
    """Raise KeyboardInterrupt when `run_and_archive` reaches this photo.

    The real thing arrives from the terminal at an arbitrary instant inside
    the executor. Raising it at the top of `run_and_archive` reproduces the
    part the CLI is responsible for -- everything finished stays finished,
    this photo and everything behind it does not start -- without pretending
    to reproduce the executor's own interrupt safety, which `run_photo`'s
    `finally` clauses own and test_execute covers.

    The named photo counts as INTERRUPTED PARTWAY rather than never started,
    even though nothing of it ran. That is deliberate and it is what the CLI
    can honestly say: the signal arrived while this photo was the one being
    worked on, and where inside it the signal landed is not something any
    caller of `run_and_archive` can know.
    """
    real = execute.run_and_archive

    def maybe_interrupt(work, ctx):
        if work.source.stem == stem:
            raise KeyboardInterrupt
        return real(work, ctx)

    monkeypatch.setattr(cli.execute, "run_and_archive", maybe_interrupt)


def test_ctrl_c_prints_a_summary_instead_of_a_traceback(
        inbox, tmp_path, no_upscaler, monkeypatch, capsys):
    """`cheapest_first` reasons about this moment by name, staging-then-rename
    exists to survive it, and the CLI is the one layer that turns any of that
    into something the user sees. A stack trace throws it away."""
    for name in ("a.png", "b.png", "c.png"):
        fixture(inbox / name, 2000, 3000)
    interrupt_on(monkeypatch, "b")
    processing = tmp_path / "processing"

    code, out = run(["-p", "--format", "png", "--processing-dir",
                     str(processing), str(inbox)], capsys)

    assert code == 130
    assert summary_line(out).startswith(cli.SUMMARY_INTERRUPTED)
    assert "1 ok" in summary_line(out)
    # b was in flight when the signal arrived; only c was never started.
    assert "1 interrupted partway" in summary_line(out)
    assert "1 never started" in summary_line(out)
    assert "partway through b.png" in out
    # What photo 1 produced is complete, and its original was archived.
    assert (processing / "to_sort_phone" / "below_target"
            / "a_phone_2000x3000_native.png").exists()
    assert (processing / "originals" / "a.png").exists()
    # b and c were never touched.
    assert (inbox / "b.png").exists() and (inbox / "c.png").exists()
    assert not (processing / cli.WORKROOT_NAME).exists()


def test_ctrl_c_names_what_finished(inbox, tmp_path, no_upscaler,
                                    monkeypatch, capsys):
    for name in ("a.png", "b.png", "c.png"):
        fixture(inbox / name, 2000, 3000)
    interrupt_on(monkeypatch, "c")
    code, out = run(["-p", "--format", "png", "--processing-dir",
                     str(tmp_path / "processing"), str(inbox)], capsys)
    assert code == 130
    assert "[1/3] a.png: ok" in out
    assert "[2/3] b.png: ok" in out
    assert "[3/3] c.png" not in out
    assert "2 ok" in summary_line(out)
    assert "1 interrupted partway" in summary_line(out)
    assert "0 never started" in summary_line(out)


def test_a_photo_the_signal_landed_inside_is_not_called_never_started(
        inbox, tmp_path, no_upscaler, monkeypatch, capsys):
    """The lie this replaces, stated as its own test.

    `_run_batch` lets KeyboardInterrupt propagate, so the photo it was working
    on produces no `PhotoResult` and used to fall into `len(works) -
    len(results)` -- "never started", about the one photo the run had
    definitely started, in the one line someone reads to work out what they
    just interrupted.

    Two photos and the interrupt on the second, so the old reading and the
    new one differ by the whole count: everything not finished used to be
    "1 never started", and the truth is that nothing was never started.
    """
    fixture(inbox / "a.png", 2000, 3000)
    fixture(inbox / "b.png", 2000, 3000)
    interrupt_on(monkeypatch, "b")

    code, out = run(["-p", "--format", "png", "--processing-dir",
                     str(tmp_path / "processing"), str(inbox)], capsys)

    assert code == 130
    assert "0 never started" in summary_line(out)
    assert "1 interrupted partway" in summary_line(out)
    assert "partway through b.png" in out


def test_a_finished_photo_is_never_counted_as_interrupted(tmp_path):
    """Guards the guard, and covers the wider half of the old lie.

    The record goes into `in_flight` before the photo starts and comes out
    once its result is in `results` -- one statement apart, and an interrupt
    landing between the two leaves the source in BOTH lists. The photo
    finished: its outputs are on disk and its original is archived. Counted
    from `in_flight` alone it would be reported as ok AND as interrupted,
    which is a worse lie than the "never started" this replaces, so the
    record is filtered against the results rather than trusted.

    Driven through `_summary` directly because the window is one statement
    wide and no fixture can land inside it on purpose.
    """
    from paperhanger import plan

    source = Path("/src/a.png")
    opts = plan.OutputSettings(tmp_path / "proc", "png", None)
    works = [plan.plan_photo(source, 2000, 3000, "png", [sizes.PHONE], opts)]
    results = [execute.PhotoResult(source=source, outcome=execute.OK)]

    line = cli._summary(works, results, interrupted=True, in_flight=[source])

    assert "1 ok" in line
    assert "interrupted partway" not in line
    assert "0 never started" in line
    assert cli._interrupted_photos(results, [source]) == []


def test_an_interrupted_run_does_not_count_rejections_it_never_reached(
        inbox, tmp_path, no_upscaler, monkeypatch, capsys):
    """`rejected_everywhere` is a property of the PLAN, true before any photo
    runs. Summed over every work rather than over the ones the run REACHED, a
    run interrupted on its first photo still reports a rejection -- a
    judgement about the user's file that nothing ever looked at.

    Interrupted on the first photo precisely so the two readings differ:
    `cheapest_first` sorts a photo with no plans to the front, so the rejected
    one is both the first candidate and, here, the one never examined.
    """
    fixture(inbox / "tiny.png", 100, 100)
    fixture(inbox / "lichen.png", 2000, 3000)
    interrupt_on(monkeypatch, "tiny")
    code, out = run(["-p", "--format", "png", "--processing-dir",
                     str(tmp_path / "processing"), str(inbox)], capsys)
    assert code == 130
    assert "0 ok" in summary_line(out)
    assert "0 rejected" in summary_line(out)
    assert "1 interrupted partway" in summary_line(out)
    assert "1 never started" in summary_line(out)
    assert "1 rejected" in out, "the report header still describes the plan"


def test_ctrl_c_during_the_scan_is_not_a_traceback(
        inbox, tmp_path, monkeypatch, capsys):
    """The scan probes every file in the directory -- ~18 ms each, so about
    sixteen seconds on the author's corpus, and the likeliest place outside
    the batch for an interrupt to land. Nothing has been written yet, but the
    module docstring promises four exit codes, and a bare traceback is none
    of them."""
    fixture(inbox / "a.png", 2000, 3000)
    fixture(inbox / "b.png", 2000, 3000)
    real = imaging.probe

    def probe(path):
        if Path(path).stem == "b":
            raise KeyboardInterrupt
        return real(path)

    monkeypatch.setattr(cli.imaging, "probe", probe)
    processing = tmp_path / "processing"
    code, out = run(["-p", "--processing-dir", str(processing), str(inbox)],
                    capsys)
    assert code == 130
    assert "before any photo was touched" in out
    assert not processing.exists()
    assert sorted(p.name for p in inbox.glob("*.png")) == ["a.png", "b.png"]


def test_a_second_ctrl_c_while_reporting_does_not_claim_nothing_happened(
        inbox, tmp_path, no_upscaler, monkeypatch, capsys):
    """`main`'s handler says no photo was touched. If it caught an interrupt
    that arrived while the summary was printing, it would say that over the
    top of the progress lines that prove otherwise."""
    fixture(inbox / "lichen.png", 2000, 3000)

    def boom(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_summary", boom)
    code, out = run(["-p", "--format", "png", "--processing-dir",
                     str(tmp_path / "processing"), str(inbox)], capsys)
    assert code == 130
    assert "[1/1] lichen.png: ok" in out
    assert "before any photo was touched" not in out


def test_an_interrupt_during_the_scratch_sweep_does_not_claim_nothing_happened(
        inbox, tmp_path, no_upscaler, monkeypatch, capsys):
    """`workroot.rmdir()` used to sit BETWEEN the two interrupt handlers.

    The batch's handler ends at the batch and the reporting handler began
    after the sweep, so this one statement was covered by neither -- and it
    is where a second Ctrl-C most plausibly lands, immediately after the
    first. The interrupt reached `main`, whose message says no photo was
    touched, over a screen of progress lines saying otherwise.
    """
    fixture(inbox / "a.png", 2000, 3000)

    def interrupt(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(Path, "rmdir", interrupt)

    code, out = run(["-p", "--format", "png", "--processing-dir",
                     str(tmp_path / "processing"), str(inbox)], capsys)

    assert code == 130
    assert "[1/1] a.png: ok" in out
    assert "before any photo was touched" not in out


def test_scratch_left_by_a_hard_kill_is_reported_rather_than_swallowed(
        inbox, tmp_path, no_upscaler, capsys):
    """A SIGKILL mid-upscale strands a 4x frame of several gigabytes.

    `sweep_partials` clears `*.partial` from the processing tree at startup
    and nothing sweeps `.work` at all, so the OSError from a non-empty rmdir
    being swallowed meant no later run would ever remove that frame OR
    mention it. Reported rather than swept: two runs against one processing
    directory would otherwise delete each other's live intermediates.
    """
    fixture(inbox / "a.png", 2000, 3000)
    processing = tmp_path / "processing"
    stranded = processing / cli.WORKROOT_NAME / "killed"
    stranded.mkdir(parents=True)
    (stranded / "frame_4x.png").write_text("several gigabytes, pretend")

    code, out = run(["-p", "--format", "png", "--processing-dir",
                     str(processing), str(inbox)], capsys)

    assert code == 0, "residue from an earlier run is a note, not a failure"
    assert cli.WORKROOT_NAME in out and "left in place" in out
    assert "killed" in out, "name what is in there"
    assert stranded.exists(), "and leave it where a human can find it"


def test_a_clean_run_says_nothing_about_its_scratch(
        inbox, tmp_path, no_upscaler, capsys):
    """Guards the guard: the note above must be reachable only by residue."""
    fixture(inbox / "a.png", 2000, 3000)
    processing = tmp_path / "processing"

    code, out = run(["-p", "--format", "png", "--processing-dir",
                     str(processing), str(inbox)], capsys)

    assert code == 0
    assert "left in place" not in out
    assert not (processing / cli.WORKROOT_NAME).exists()


def test_an_interrupted_setup_does_not_talk_about_photos(monkeypatch, capsys):
    """"before any photo was touched" is a non sequitur during `setup`.

    No photo was ever in play, and setup is the slowest thing here to be
    interrupted in -- it downloads about 60 MB. What the user needs to know
    is whether anything was left half-installed, which is a different
    sentence.
    """
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.toolchain, "setup", interrupted)

    code, out = run(["setup"], capsys)

    assert code == 130
    assert "photo" not in out
    assert "paperhanger setup" in out and "run it again" in out


def test_an_interrupted_run_still_talks_about_photos(
        inbox, tmp_path, monkeypatch, capsys):
    """Guards the guard: the batch's own message must not have gone with it."""
    fixture(inbox / "a.png", 2000, 3000)

    def interrupted(path):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.imaging, "probe", interrupted)

    code, out = run(["-p", "--processing-dir", str(tmp_path / "p"), str(inbox)],
                    capsys)

    assert code == 130
    assert "before any photo was touched" in out


def test_a_routing_bug_still_escapes_the_interrupt_handler(
        inbox, tmp_path, no_upscaler, monkeypatch):
    """The KeyboardInterrupt catch must not have widened into a bare except."""
    def routing_bug(work, ctx):
        raise ValueError("_upscale_one_plan requires a crop plan")

    monkeypatch.setattr(cli.execute, "run_and_archive", routing_bug)
    fixture(inbox / "lichen.png", 2000, 3000)
    with pytest.raises(ValueError, match="requires a crop plan"):
        cli.main(["-p", "--format", "png", "--processing-dir",
                  str(tmp_path / "processing"), str(inbox)])


# ---------- the writability pre-flight ----------

def test_an_uncreatable_processing_dir_fails_once(inbox, tmp_path, capsys):
    for name in ("a.png", "b.png", "c.png"):
        fixture(inbox / name, 2000, 3000)
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        code, out = run(["-p", "--format", "png", "--processing-dir",
                         str(locked / "processing"), str(inbox)], capsys)
    finally:
        locked.chmod(0o755)
    assert code == 1
    assert "cannot write to" in error_block(out)
    assert "[1/3]" not in out, "894 identical failures is what this prevents"


def test_an_existing_unwritable_processing_dir_fails_once(
        inbox, tmp_path, capsys):
    """The case `mkdir(exist_ok=True)` alone cannot see, and the common one:
    the directory exists because the FIRST run made it."""
    fixture(inbox / "a.png", 2000, 3000)
    processing = tmp_path / "processing"
    processing.mkdir()
    processing.chmod(0o555)
    try:
        code, out = run(["-p", "--format", "png", "--processing-dir",
                         str(processing), str(inbox)], capsys)
    finally:
        processing.chmod(0o755)
    assert code == 1
    assert "cannot write to" in error_block(out)
    assert "[1/1]" not in out


def test_the_writability_probe_leaves_nothing_behind(
        inbox, tmp_path, no_upscaler, capsys):
    fixture(inbox / "lichen.png", 2000, 3000)
    processing = tmp_path / "processing"
    code, _ = run(["-p", "--format", "png", "--processing-dir",
                   str(processing), str(inbox)], capsys)
    assert code == 0
    assert list(processing.glob(f"*{execute.PARTIAL_SUFFIX}")) == []


def test_a_stranded_probe_cannot_defeat_the_writability_check(
        inbox, tmp_path, capsys):
    """`Path.touch()` defaults to exist_ok=True, which short-circuits to
    `os.utime` -- and utime succeeds on a file you own even inside a directory
    you cannot write. A probe left behind by a run killed between the touch
    and the unlink would then wave the next run through, and `check_writable`
    runs before `sweep_partials`, so the sweep cannot rescue it.

    The stranded file is named by `cli.probe_path`, so this reproduces the
    inheritance rather than guessing at a spelling.
    """
    fixture(inbox / "lichen.png", 2000, 3000)
    processing = tmp_path / "processing"
    processing.mkdir()
    cli.probe_path(processing).touch()
    processing.chmod(0o555)
    try:
        code, out = run(["-p", "--format", "png", "--processing-dir",
                         str(processing), str(inbox)], capsys)
    finally:
        processing.chmod(0o755)
    assert code == 1
    assert "cannot write to" in error_block(out)
    assert "[1/1]" not in out


def test_a_stranded_probe_in_a_writable_directory_is_not_called_unwritable(
        inbox, tmp_path, capsys):
    """The message, not the refusal.

    `touch(exist_ok=False)` raises FileExistsError, and folding that into the
    general branch produced `cannot write to <dir>: [Errno 17] File exists` --
    a diagnosis flatly contradicted by the error printed beside it. The
    directory here is perfectly writable; what is taken is the probe's own
    name, stranded by a run with this same pid that was hard-killed between
    the touch and the unlink.
    """
    fixture(inbox / "lichen.png", 2000, 3000)
    processing = tmp_path / "processing"
    processing.mkdir()
    probe = cli.probe_path(processing)
    probe.touch()

    code, out = run(["-p", "--format", "png", "--processing-dir",
                     str(processing), str(inbox)], capsys)

    assert code == 1
    complaint = error_block(out)
    assert probe.name in complaint
    assert "cannot write to" not in complaint, \
        "the directory is writable; saying otherwise sends the user to check " \
        "permissions that were never the problem"
    # Cleared, so the sentence telling them to run again is true.
    assert not probe.exists()


def test_the_probe_name_is_unique_to_this_process(tmp_path):
    """A fresh name cannot inherit a stranded one, and `sweep_partials` can
    still tidy whatever a hard kill leaves behind."""
    name = cli.probe_path(tmp_path).name
    assert str(os.getpid()) in name
    assert name.endswith(execute.PARTIAL_SUFFIX)


def test_dry_run_does_not_probe_an_unwritable_target(inbox, tmp_path, capsys):
    """The pre-flight writes, so it sits after the --dry-run return. A
    dry run against a target it could never write to still reports."""
    fixture(inbox / "lichen.png", 2000, 3000)
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        code, out = run(["-p", "--dry-run", "--processing-dir",
                         str(locked / "processing"), str(inbox)], capsys)
    finally:
        locked.chmod(0o755)
    assert code == 0
    assert "1 image," in out
    assert not (locked / "processing").exists()


def test_an_empty_directory_is_not_an_error(inbox, tmp_path, capsys):
    code, out = run(["--processing-dir", str(tmp_path / "processing"),
                     str(inbox)], capsys)
    assert code == 0
    assert "0 images" in out
