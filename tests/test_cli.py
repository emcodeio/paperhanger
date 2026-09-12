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
    """The closing `done: ...` line, isolated.

    Asserted against by name rather than against the whole of stdout, because
    the report header printed above it carries its own "N rejected" and "N
    already done" -- so a summary that had stopped counting either would hide
    behind the header and the test would still be green. Found by mutation:
    `rejected = counts[execute.REJECTED]` survived until this existed.
    """
    return next(line for line in out.splitlines() if line.startswith("done:"))


@pytest.fixture
def inbox(tmp_path):
    directory = tmp_path / "in"
    directory.mkdir()
    return directory


@pytest.fixture
def no_upscaler(tmp_path, monkeypatch):
    """A machine where upscayl-bin is not installed."""
    monkeypatch.setenv("PAPERHANGER_UPSCAYL_BIN", str(tmp_path / "nowhere"))


@pytest.fixture
def ready_toolchain(fake_upscaler, monkeypatch):
    """upscayl-bin installed, its model present and verified.

    Only the MODEL half is faked, and only because it cannot be faked
    honestly: `toolchain.find_models` hashes both files against pinned
    SHA-256s, and the real pair is 60 MB that Tier 1 does not download. The
    binary is still resolved by the real `find_upscayl` through the same
    PAPERHANGER_UPSCAYL_BIN seam production uses, and whatever this returns
    has to reach `execute.Context` for the run to produce anything at all --
    so the wiring under test is not the part being stubbed.
    """
    _binary, models = fake_upscaler
    monkeypatch.setattr(cli.toolchain, "ensure_ready",
                        lambda: (toolchain.find_upscayl(), models))
    return models


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
    """`already_done` is a SET OF SOURCES, not a count: a count can only ever
    inflate the header, and `set(1)` raises."""
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
    """`all()` over an empty sequence is True, and a photo rejected on every
    device has no plans at all -- so the bare `all(p.destination.exists() ...)`
    calls it finished while the header counts it as rejected."""
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
    assert "--allow-nested" in out


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
    assert "--allow-nested" in out


def test_guard_refuses_originals(tmp_path, capsys):
    processing = tmp_path / "processing"
    originals = processing / "originals"
    originals.mkdir(parents=True)
    fixture(originals / "lichen.png", 2000, 3000)
    code, out = run(["--dry-run", "--processing-dir", str(processing),
                     str(originals)], capsys)
    assert code == 1
    assert "--allow-nested" in out


def test_guard_refuses_a_file_inside_the_processing_dir(tmp_path, capsys):
    processing = tmp_path / "processing"
    originals = processing / "originals"
    originals.mkdir(parents=True)
    photo = fixture(originals / "lichen.png", 2000, 3000)
    code, out = run(["--dry-run", "--processing-dir", str(processing),
                     str(photo)], capsys)
    assert code == 1
    assert "--allow-nested" in out


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
    assert "--allow-nested" in out


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
    assert str(binary) in out
    assert "PAPERHANGER_UPSCAYL_BIN" in out


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
    assert "paperhanger setup" in out


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
    """Its outcome is FAILED, because the move it needed did not happen.
    Reading rejection from the outcome would lose the one fact that explains
    why the photo produced nothing."""
    fixture(inbox / "tiny.png", 100, 100)
    processing = tmp_path / "processing"
    processing.mkdir()
    (processing / "error").write_text("a file where the directory should be")
    code, out = run(["--processing-dir", str(processing), str(inbox)], capsys)
    assert code == 2
    assert "1 rejected" in summary_line(out)
    assert "1 failed" in summary_line(out)
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


def test_an_empty_directory_is_not_an_error(inbox, tmp_path, capsys):
    code, out = run(["--processing-dir", str(tmp_path / "processing"),
                     str(inbox)], capsys)
    assert code == 0
    assert "0 images" in out
