"""Argument parsing and the run loop. The only module a user ever touches.

Everything it says goes to STDOUT, argparse's complaints included. A
twenty-four-hour import is read as one stream, usually through `tee`, and a
failure that arrives on a different file descriptor than the progress it
interrupted is a failure nobody can place in time.

Four exit codes and no more:

  0    finished, everything the run was asked for is done
  1    the run never started -- usage, the guard, a collision, an unwritable
       processing directory, a missing toolchain
  2    the run started and some photos failed
  130  Ctrl-C, the shell's convention for SIGINT

argparse's own exit 2 for a bad flag is intercepted for exactly that reason:
it would otherwise be indistinguishable from a batch that ran and lost photos.
130 rather than 2 for an interrupt because the two say different things -- an
interrupted run has photos it never looked at, and calling that "some photos
failed" would misdescribe both halves of it.
"""

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from . import execute, formats, imaging, plan, report, sizes, toolchain

DEFAULT_PROCESSING_DIR = Path.home() / "Pictures" / "wallpaper" / "processing"
SUBCOMMANDS = ("setup", "doctor")

# Under the processing directory rather than TMPDIR: intermediates are large
# (a 4x frame is sixteen times the source's pixels, as PNG), and the volume
# the user chose for the outputs is the one with room for them. Leading dot so
# the sorter's eye skips it.
WORKROOT_NAME = ".work"

OK = 0
USAGE_ERROR = 1
SOME_FAILED = 2
INTERRUPTED = 130

# The two ways the closing line can begin. Named because the tests isolate
# that line by prefix rather than searching the whole of stdout -- the report
# header above it carries its own "N rejected" and "N already done", so a
# summary that stopped counting either would hide behind it.
SUMMARY_DONE = "done:"
SUMMARY_INTERRUPTED = "interrupted:"

# doctor's first line, likewise isolated by the tests: the pinned-release line
# names `paperhanger setup` unconditionally, so a NOT FOUND branch that had
# dropped the hint would still leave the words somewhere in stdout.
BINARY_LABEL = "upscayl-bin"

UPSCALER_ENV = "PAPERHANGER_UPSCAYL_BIN"


class UsageError(Exception):
    """argparse's complaint, raised rather than exiting the process."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):                      # noqa: D102 - argparse hook
        raise UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="paperhanger",
        description="Turn a folder of images into desktop and phone wallpapers.",
        epilog="paperhanger setup   download and verify upscayl-bin and its model\n"
               "paperhanger doctor  report what is found and what is missing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    devices = parser.add_mutually_exclusive_group()
    devices.add_argument("-d", dest="devices", action="store_const",
                         const=[sizes.DESKTOP], help="desktop only")
    devices.add_argument("-p", dest="devices", action="store_const",
                         const=[sizes.PHONE], help="phone only")
    devices.add_argument("-b", dest="devices", action="store_const",
                         const=list(sizes.DEVICES), help="both (default)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would happen and exit, writing nothing")
    parser.add_argument("--format", default="heic", choices=formats.FORMATS,
                        help="output format (default: heic)")
    parser.add_argument("--quality", type=int, default=None,
                        help="0-100; not valid for png, which is lossless")
    # Deliberately NOT the same flag as --allow-nested. They were one --force
    # in an earlier draft, which meant the flag required to process the default
    # nested layout also disabled skip-existing: resuming an interrupted import
    # then re-upscaled every photo it had already finished.
    parser.add_argument("--overwrite", action="store_true",
                        help="regenerate outputs that already exist")
    parser.add_argument("--allow-nested", action="store_true",
                        help="permit an input that contains or sits inside "
                             "the processing directory")
    parser.add_argument("--processing-dir", type=Path,
                        default=DEFAULT_PROCESSING_DIR,
                        help=f"where outputs and archives go "
                             f"(default: {DEFAULT_PROCESSING_DIR})")
    parser.add_argument("path", nargs="?", type=Path,
                        help="an image file, or a directory of them")
    return parser


def check_guard(input_path: Path, processing_dir: Path, allow_nested: bool):
    """Refuse to walk into our own output. Originals are MOVED, not copied.

    The author's corpus lives at `~/Pictures/wallpaper`, directly above the
    default processing directory, so the most ordinary command the tool has is
    one habit away from relocating 894 originals into a subdirectory of
    themselves -- and the second run would then find them there and do it
    again.

    A DIRECTORY is refused whenever it overlaps the processing tree in either
    direction: it contains it, it is it, or it sits inside it. Both paths are
    resolved first, because `~/Pictures` is a symlink on some machines and a
    string comparison would wave the nested layout straight through.

    A single FILE is refused only when it lives INSIDE the processing tree,
    where it is one of our own outputs or an already-archived original. A file
    names exactly one image and no walk can reach the outputs from it, so
    `paperhanger ~/Pictures/wallpaper/cliffs.jpg` stays legal even though its
    parent contains the processing directory.
    """
    if allow_nested:
        return None

    inp, proc = input_path.resolve(), processing_dir.resolve()
    if inp.is_dir():
        # is_relative_to is true of a path against itself, so "is the
        # processing directory" needs no separate test.
        overlapping = proc.is_relative_to(inp) or inp.is_relative_to(proc)
        shape = f"{input_path} contains or is inside"
    else:
        overlapping = inp.parent.is_relative_to(proc)
        shape = f"{input_path} is inside"
    if not overlapping:
        return None

    return (
        f"{shape} the processing directory {processing_dir}.\n"
        f"  Originals are MOVED, not copied, so this would relocate your "
        f"source images.\n"
        f"  Pass --allow-nested if that is what you intend, or point "
        f"--processing-dir\n"
        f"  somewhere else."
    )


def resolve_devices(chosen) -> list:
    """The devices to plan for: what was asked, deduplicated, in order.

    `plan.find_collisions` records the SOURCES claiming each destination and
    ignores a repeat, so one photo planned twice for the same device produces
    two identical destinations that the collision check cannot see -- and a
    report that prints the photo's outputs twice. `-d`/`-p`/`-b` cannot
    produce such a list today; deduplicating where the list is built is what
    lets everything downstream go on assuming they never will.
    """
    return list(dict.fromkeys(chosen if chosen else list(sizes.DEVICES)))


def scan(path: Path):
    """(probed images, non-image count). Non-recursive, like the legacy glob.

    Subdirectories are neither probed nor counted. Under --allow-nested the
    processing tree is one of them, and reporting it as a skipped non-image
    would be a lie in the only summary the user reads.
    """
    candidates = sorted(p for p in path.iterdir() if p.is_file()) \
        if path.is_dir() else [path]
    images, non_images = [], 0
    for candidate in candidates:
        probed = imaging.probe(candidate)
        if probed is None:
            non_images += 1
            continue
        images.append((candidate, *probed))
    return images, non_images


def already_done(works, overwrite: bool) -> set:
    """The sources whose every output is already on disk.

    A SET of source paths, not a count: `report.render_report` needs to know
    WHICH photos to mark, and a count could only ever inflate the header.

    `work.plans and ...` rather than the bare `all(...)`, because `all` over an
    empty sequence is True and a photo rejected on every device has no plans at
    all. Without the guard such a photo renders as "already done, skipping"
    while the header two lines above counts it as rejected.
    """
    if overwrite:
        return set()
    return {work.source for work in works
            if work.plans and all(p.destination.exists() for p in work.plans)}


def upscaler_is_needed(works, overwrite: bool) -> bool:
    """Will anything this run actually renders ask for the model?

    Asked per PLAN and after the skip-existing check the executor will make,
    not per photo: a resumed import whose remaining outputs are all band 1 or
    2 needs no binary, and demanding one would stop a run that was going to
    finish without it.
    """
    return any(p.needs_upscale and (overwrite or not p.destination.exists())
               for work in works for p in work.plans)


def probe_path(processing_dir: Path) -> Path:
    """The one file `check_writable` writes. Unique to this process.

    A fixed name is defeatable. `Path.touch()` defaults to `exist_ok=True`,
    which short-circuits to `os.utime` when the file is already there, and
    `utime` succeeds on a file you own even inside a directory you cannot
    write -- so a probe stranded by a run killed between the touch and the
    unlink would let the NEXT run's pre-flight pass a directory it cannot
    write to. `check_writable` runs before `sweep_partials`, so the sweep
    cannot rescue it either.

    The pid makes a fresh name that cannot inherit a stranded one, and the
    `.partial` suffix keeps `sweep_partials` able to tidy it.
    """
    return processing_dir / (f".paperhanger-write-test-{os.getpid()}"
                             f"{execute.PARTIAL_SUFFIX}")


def check_writable(processing_dir: Path):
    """Fail once rather than 894 times. Returns a complaint, or None.

    Every writer downstream creates its own destination directory as it goes,
    so an unwritable processing directory is not discovered until the first
    render -- and then again, identically, for every photo queued behind it.
    On the author's corpus that is 894 `Permission denied` lines where one
    would do, and the rest of the pre-flight is careful to fail once.

    `mkdir(exist_ok=True)` alone does not answer the question. It succeeds on
    a directory that already exists and cannot be written to, which is the
    shape this takes on the SECOND run against a volume that has gone
    read-only -- and the second run is the common one, because the first is
    what creates the directory. So the check also writes the kind of file the
    tool writes, and removes it.

    `exist_ok=False` on the touch, and a per-process name from `probe_path`,
    because `Path.touch()`'s default is the hole: it short-circuits to
    `os.utime` on an existing file, and `utime` succeeds inside a directory
    you cannot write. Either measure alone would close the demonstrated case;
    both together leave no version of it. If the two ever do collide -- a
    hard-killed earlier run, the same pid, the same directory -- the check
    refuses and names the file, which is the right way round for a tool that
    moves originals.

    `.partial`, deliberately: `sweep_partials` already clears strays by that
    suffix from exactly this tree, so a crash between the create and the
    unlink leaves nothing a later run will not tidy on its own.

    `os.access` was the other option and is the wrong one. It answers from the
    real uid and ignores ACLs, so on macOS it can refuse a directory that
    would have worked -- and refusing a run that was going to succeed is worse
    than the repetition this exists to prevent.
    """
    probe = probe_path(processing_dir)
    try:
        processing_dir.mkdir(parents=True, exist_ok=True)
        probe.touch(exist_ok=False)
    except OSError as error:
        return f"cannot write to {processing_dir}: {error}"
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
    return None


def _report_collisions(collisions) -> None:
    print("error: two sources would write the same output:")
    for destination, sources in collisions.items():
        print(f"  {destination.name}  <-  {', '.join(s.name for s in sources)}")
    print("Rename one of them and run again.")


def _summary(works, results, interrupted: bool = False) -> str:
    """One line naming where every photo ended up.

    Rejection is read from `work.rejected_everywhere`, not from the outcome: a
    rejected photo whose move to error/ fails reports FAILED, and inferring
    rejection from outcomes would drop it from the count that explains why it
    produced nothing. It is reported under both headings, which is what
    happened to it.

    Counted over the photos the run actually REACHED, not over every photo it
    planned. An interrupted run has photos it never looked at, and summing
    `rejected_everywhere` across all of them would report images as rejected
    that nothing ever examined -- a claim about the user's files that this run
    did not earn. For a completed run the two are the same set.
    """
    counts = Counter(result.outcome for result in results)
    reached = {result.source for result in results}
    rejected = sum(1 for work in works
                   if work.rejected_everywhere and work.source in reached)
    failed = counts[execute.PARTIAL] + counts[execute.FAILED]
    tally = (f"{counts[execute.OK]} ok, "
             f"{counts[execute.ALREADY_DONE]} already done, "
             f"{rejected} rejected, {failed} failed")
    if interrupted:
        return (f"{SUMMARY_INTERRUPTED} {tally}, "
                f"{len(works) - len(results)} never started")
    return f"{SUMMARY_DONE} {tally}"


def _run_batch(works, ctx, results) -> None:
    """Every photo, cheapest first. One photo's disaster is never the run's.

    Results are appended to the caller's list rather than returned, so that a
    `KeyboardInterrupt` -- which this function deliberately does not catch --
    leaves the caller holding everything finished before it arrived. A
    returned list would be lost with the exception, and the closing summary
    would have nothing to report at the one moment a user most needs it.

    `run_and_archive` does not catch everything a photo can do. The
    whole-frame and per-plan enlargements happen OUTSIDE `run_photo`'s
    per-plan try -- they have to, because one enlargement serves every plan --
    so a model run that fails travels straight out through `run_and_archive`
    and would otherwise end a twenty-four-hour import at photo three, with
    nothing said about the twenty hours of work still queued behind it.

    ImagingError and OSError only. `ValueError` -- which `_upscale_one_plan`
    raises when it is handed a cropless plan -- is about this codebase rather
    than this photo. Recorded as "one photo failed" it would look exactly like
    a corrupt JPEG and would repeat, quietly, for every photo in the run.

    The photo is recorded FAILED rather than PARTIAL, and the `skipped` list
    `run_and_archive` had built is lost with the exception. That is the
    honest label either way: the only step outside the per-plan try is the
    whole-frame enlargement, which happens before any plan of that photo is
    rendered, so nothing this run produced for it is on disk and its original
    is still where it was found.

    Reasons are printed where they happen, not only in the recap at the end.
    A twenty-four-hour import is read as a log, and the answer to "why did
    photo 340 fail" belongs beside photo 340.
    """
    total = len(works)
    for index, work in enumerate(execute.cheapest_first(works), start=1):
        try:
            result = execute.run_and_archive(work, ctx)
        except (imaging.ImagingError, OSError) as error:
            result = execute.PhotoResult(
                source=work.source, outcome=execute.FAILED,
                failures=[f"{work.source.name}: {error}"],
            )
        results.append(result)
        print(f"[{index}/{total}] {work.source.name}: {result.outcome}"
              f" ({len(result.written)} written)")
        for message in result.failures:
            print(f"    {message}")


def main(argv=None) -> int:
    """The entry point. Exit codes are the module docstring's four, all of them.

    A `KeyboardInterrupt` from anywhere outside the batch is caught here.
    `_run` protects the batch itself AND everything it prints afterwards, so
    reaching this handler means no photo was ever started -- which is what
    lets the message say so. The two places slow enough to be interrupted in
    are the scan, which probes every file in the directory, and the
    toolchain's model hashing; neither has written anything.
    """
    try:
        return _run(list(sys.argv[1:] if argv is None else argv))
    except KeyboardInterrupt:
        print()
        print(f"{SUMMARY_INTERRUPTED} before any photo was touched")
        return INTERRUPTED


def _run(argv) -> int:
    if argv and argv[0] in SUBCOMMANDS:
        if len(argv) > 1:
            print(f"error: `paperhanger {argv[0]}` takes no arguments; "
                  f"got {' '.join(argv[1:])}")
            return USAGE_ERROR
        return _setup() if argv[0] == "setup" else _doctor()

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except UsageError as error:
        print(f"error: {error}")
        print(parser.format_usage().rstrip())
        return USAGE_ERROR

    if args.path is None:
        print("error: a file or directory is required")
        print(parser.format_usage().rstrip())
        return USAGE_ERROR
    if not args.path.exists():
        print(f"error: {args.path} does not exist")
        return USAGE_ERROR

    try:
        quality = formats.quality_for(args.format, args.quality)
    except ValueError as error:
        print(f"error: {error}")
        return USAGE_ERROR

    complaint = check_guard(args.path, args.processing_dir, args.allow_nested)
    if complaint:
        print(f"error: {complaint}")
        return USAGE_ERROR

    devices = resolve_devices(args.devices)
    opts = plan.OutputSettings(args.processing_dir, args.format, quality)

    try:
        images, non_images = scan(args.path)
    except OSError as error:
        # A directory the user cannot read is the realistic one. `probe` never
        # raises, so nothing else in here can -- but a traceback is not an
        # error message, and this is the first thing the tool does with the
        # path it was given.
        print(f"error: cannot read {args.path}: {error}")
        return USAGE_ERROR
    works = [plan.plan_photo(source, width, height, fmt, devices, opts)
             for source, width, height, fmt in images]

    # Before anything is written, and before the toolchain is demanded: both
    # are fatal, and only one of them is the user's actual problem.
    collisions = plan.find_collisions(works)
    if collisions:
        _report_collisions(collisions)
        return USAGE_ERROR

    done = already_done(works, args.overwrite)
    rendered_report = report.render_report(works, already_done=done,
                                           non_images=non_images)

    if args.dry_run:
        print(rendered_report)
        return OK

    # Checked here, before a single photo is touched. Planning is pure integer
    # arithmetic over dimensions the scan already probed, so a machine without
    # the binary finds out as soon as the scan ends rather than forty images
    # into the batch.
    binary = models = None
    if upscaler_is_needed(works, args.overwrite):
        try:
            binary, models = toolchain.ensure_ready()
        except toolchain.ToolchainError as error:
            print(f"error: {error}")
            return USAGE_ERROR

    # The last of the pre-flight and the first thing that writes, in that
    # order on purpose: every check that can refuse the run has now refused
    # it, so a run that never starts leaves the filesystem exactly as it found
    # it -- the same property --dry-run has, for the same reason.
    complaint = check_writable(args.processing_dir)
    if complaint:
        print(f"error: {complaint}")
        return USAGE_ERROR

    workroot = args.processing_dir / WORKROOT_NAME
    ctx = execute.Context(processing_dir=args.processing_dir, workroot=workroot,
                          upscayl=binary, models_dir=models,
                          overwrite=args.overwrite)

    swept = execute.sweep_partials(args.processing_dir)
    if swept:
        print(f"swept {swept} stray {execute.PARTIAL_SUFFIX} file(s) "
              f"from an interrupted run")

    print(rendered_report)
    print()

    # Ctrl-C is a first-class way to end a twenty-four-hour import, not an
    # accident, and the rest of this codebase is built around that instant:
    # outputs are staged and renamed so none is ever half-written, the
    # original is archived only after every plan of its photo is in place, and
    # `cheapest_first` orders the run so that an interrupt at any moment
    # leaves as many finished wallpapers behind as possible. The CLI is the
    # one layer that turns all of that into something the user can see. A
    # stack trace here would throw it away at the exact moment it pays off --
    # after twenty hours, with five hundred progress lines to scroll back
    # through to find out what they got.
    results = []
    interrupted = False
    try:
        _run_batch(works, ctx, results)
    except KeyboardInterrupt:
        interrupted = True

    # Our own scratch root, empty once every photo's workdir has been swept --
    # including on the interrupt, because `run_photo` sweeps in a `finally`
    # and KeyboardInterrupt runs those like any other exception. rmdir rather
    # than rmtree: anything still in there is unexpected, and worth leaving
    # where a human can find it.
    try:
        workroot.rmdir()
    except OSError:
        pass

    # A second Ctrl-C can land in here, and this block catches it rather than
    # leaving it to `main` -- whose message says no photo was touched, while
    # the progress lines already on screen say otherwise. This does not make
    # the window smaller; it stops the outer handler describing it wrongly.
    failed = []
    try:
        print()
        print(_summary(works, results, interrupted=interrupted))

        failed = [r for r in results
                  if r.outcome in (execute.PARTIAL, execute.FAILED)]
        if failed:
            # Paths only. Each one's reasons were printed beside it as it
            # happened; this is the list to feed back in, not a second copy
            # of the diagnosis.
            print()
            print("left in place for a re-run:")
            for result in failed:
                print(f"  {result.source}")
    except KeyboardInterrupt:
        interrupted = True

    # The interrupt outranks the failures it may have travelled with: the run
    # did not finish, and 2 would describe it as one that did and lost photos.
    # The failures are still listed above either way.
    if interrupted:
        return INTERRUPTED
    return SOME_FAILED if failed else OK


def _setup() -> int:
    try:
        toolchain.setup()
    except toolchain.ToolchainError as error:
        print(f"error: {error}")
        return USAGE_ERROR
    except Exception as error:                     # noqa: BLE001 - deliberate
        # A catch-all, because `toolchain.setup` wraps what it DOWNLOADS and
        # nothing else. The extraction raises BadZipFile on a torn archive and
        # OSError on a full disk, and the chmod that repairs a binary which
        # lost its +x bit raises PermissionError on one whose mode cannot be
        # changed -- all straight out of the stdlib, all a traceback in the
        # user's face on the very first command this tool tells them to run.
        print(f"error: setup failed: {type(error).__name__}: {error}")
        print("run `paperhanger doctor` to see what is installed")
        return USAGE_ERROR
    print("toolchain ready")
    return OK


def _binary_source(binary: Path) -> str:
    """Where `find_upscayl` got this binary. Its VERSION is never claimed."""
    override = os.environ.get(UPSCALER_ENV)
    if override and Path(override) == binary:
        return f"from {UPSCALER_ENV}"
    if binary == toolchain.bin_dir() / toolchain.BINARY_NAME:
        return "installed by `paperhanger setup`"
    return "found on PATH"


def _doctor() -> int:
    state = toolchain.status()
    binary = state["binary"]
    # Binary and provenance on ONE line. Two lines read no better, and this
    # way everything doctor claims about the binary can be isolated by prefix
    # -- which the tests do, because `paperhanger setup` is named on the
    # pinned-release line below whatever this branch prints.
    if binary is None:
        print(f"{BINARY_LABEL}    : NOT FOUND -- run `paperhanger setup`")
    else:
        print(f"{BINARY_LABEL}    : {binary}  ({_binary_source(binary)})")
    # The release is the tag `paperhanger setup` DOWNLOADS -- it is not read
    # from the binary above. A binary from PATH or from the environment
    # override can be any build, and upscayl-bin reports no version, so
    # printing "release: X" beside it would assert something never checked.
    print(f"pinned release : {state['release']}  "
          f"(what `paperhanger setup` installs)")
    print(f"model          : {state['model']} "
          f"({'ok' if state['models_ok'] else 'missing or corrupt'})")
    print(f"models dir     : {state['models_dir']}")
    print(f"sips           : {imaging.SIPS} "
          f"({'ok' if Path(imaging.SIPS).exists() else 'MISSING'})")
    return OK


if __name__ == "__main__":                         # pragma: no cover
    sys.exit(main())
