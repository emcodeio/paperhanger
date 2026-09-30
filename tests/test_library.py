"""Tier 1 tests for library/: the tools that add a finished batch to a wallpaper library.

Everything here runs offline against temp trees. The NAS and iCloud-sync paths are
exercised through the `run` seam, so the tests assert the commands rather than
executing them. Stems are synthetic; no real library filename appears.
"""

import itertools
import os
from pathlib import Path

import pytest

from library import _library as lib
from paperhanger import cli, plan, sizes
from tests import pixels


def _opts(processing: Path) -> plan.OutputSettings:
    return plan.OutputSettings(processing, "heic", 80)


# --------------------------------------------------------------------------- names

# Shapes chosen to reach every band and both crop directions on both devices.
SHAPES = [(8000, 5000), (6000, 3750), (3000, 1900), (1400, 875), (900, 560),
          (5000, 8000), (2000, 3200), (640, 1024), (12000, 3000), (1200, 4800)]


def test_parse_output_name_round_trips_every_planned_name(tmp_path):
    seen_tokens, seen_positions = set(), set()
    for (w, h), stem in zip(SHAPES, itertools.cycle(["alpha_beta_1234", "gamma_top_5678"])):
        work = plan.plan_photo(Path(f"{stem}.jpg"), w, h, list(sizes.DEVICES), _opts(tmp_path))
        for p in work.plans:
            parsed = lib.parse_output_name(p.destination.name)
            assert parsed == (stem, p.position, p.device), p.destination.name
            seen_tokens.add(p.factor_token)
            seen_positions.add(p.position)
    assert "native" in seen_tokens and "4x" in seen_tokens
    assert any(t not in ("native", "4x") for t in seen_tokens)   # a fractional factor
    assert {"top", "middle", "bottom", "left", "center", "right", None} <= seen_positions


def test_parse_output_name_rejects_non_output_names():
    for name in ["photo.jpg", "photo_desktop.heic", "photo_desktop_100x100_native.png",
                 "photo_tablet_100x100_native.heic"]:
        assert lib.parse_output_name(name) is None


def test_parse_legacy_output_name_reads_the_old_pipeline_grammar():
    assert lib.parse_legacy_output_name("alpha_beta_1234_ml_res_lt_3x_desktop.heic") == \
        ("alpha_beta_1234", None, "desktop")
    assert lib.parse_legacy_output_name("alpha_beta_1234_left_ml_res_4x_phone.heic") == \
        ("alpha_beta_1234", "left", "phone")
    assert lib.parse_legacy_output_name("alpha_beta_1234_desktop_7680x4800_2x.heic") is None


def test_stem_of_library_file_handles_both_namings_and_plain_files():
    assert lib.stem_of_library_file("a_b_1234_top_desktop_7680x4800_1.6x.heic") == "a_b_1234"
    assert lib.stem_of_library_file("a_b_1234_middle_ml_res_3x_desktop.heic") == "a_b_1234"
    assert lib.stem_of_library_file("a_b_1234_x.heic") == "a_b_1234_x"


@pytest.mark.parametrize("stem", ["red_leaf_1234", "a1_b2_c3_d4_e5_f6_0001", "moon_eclipse_9999"])
def test_validate_stem_accepts_library_style(stem):
    assert lib.validate_stem(stem) == []


@pytest.mark.parametrize("stem", ["red_leaf_", "Red_leaf_1234", "red_leaf_x_1234",
                                  "red_leaf_123", "red_1234", "a_b_c_d_e_f_g_1234",
                                  "red_desktop_1234", "red_phone_1234", "red_4x_1234",
                                  "red_ml_res_1234", "red leaf_1234"])
def test_validate_stem_rejects_what_would_confuse_parsing(stem):
    assert lib.validate_stem(stem) != []


def _counter(*values):
    it = iter(values)
    return lambda _n: next(it)


def test_assign_suffixes_skips_taken_stems_and_prefix_clashes():
    taken = {"red_leaf_2000", "blue_sky_3001_x"}          # 3001 would prefix a taken stem
    table = lib.assign_suffixes({"a.jpg": "red_leaf", "b.jpg": "blue_sky"}, taken,
                                rand=_counter(1000, 1001, 2001, 2002))
    assert table == {"a.jpg": "red_leaf_2001", "b.jpg": "blue_sky_3002"}


def test_assign_suffixes_never_hands_out_a_stem_twice_in_one_batch():
    table = lib.assign_suffixes({"a.jpg": "red_leaf", "b.jpg": "red_leaf"}, set(),
                                rand=_counter(0, 0, 1))
    assert table == {"a.jpg": "red_leaf_1000", "b.jpg": "red_leaf_1001"}


def test_assign_suffixes_keeps_an_approved_table():
    persisted = {"a.jpg": "red_leaf_4321"}
    table = lib.assign_suffixes({"a.jpg": "red_leaf"}, set(), rand=_counter(), persisted=persisted)
    assert table == persisted


def test_assign_suffixes_refuses_invalid_words():
    with pytest.raises(lib.BatchError):
        lib.assign_suffixes({"a.jpg": "Red_Leaf"}, set(), rand=_counter(0))


# --------------------------------------------------------------------------- config

def test_load_config_requires_a_library_and_originals():
    with pytest.raises(lib.ConfigError):
        lib.load_config({})


def test_load_config_defaults_to_neutral_folder_names(tmp_path):
    cfg = lib.load_config({"PAPERHANGER_LIBRARY": str(tmp_path / "lib"),
                           "PAPERHANGER_ORIGINALS": str(tmp_path / "orig")})
    assert cfg.folders == {"desktop": "desktop/primary", "desktop-secondary": "desktop/secondary",
                           "phone": "phone/primary", "phone-secondary": "phone/secondary"}
    assert cfg.nas is None


def test_load_config_reads_folders_and_nas(tmp_path):
    cfg = lib.load_config({"PAPERHANGER_LIBRARY": str(tmp_path),
                           "PAPERHANGER_ORIGINALS": str(tmp_path),
                           "PAPERHANGER_LIBRARY_FOLDERS": "d/a,d/b,p,p/b",
                           "PAPERHANGER_NAS": "box:/share/wallpaper"})
    assert cfg.folders["phone"] == "p" and cfg.folders["desktop-secondary"] == "d/b"
    assert cfg.nas == ("box", "/share/wallpaper")


def test_load_config_refuses_a_malformed_folder_list(tmp_path):
    with pytest.raises(lib.ConfigError):
        lib.load_config({"PAPERHANGER_LIBRARY": str(tmp_path), "PAPERHANGER_ORIGINALS": str(tmp_path),
                         "PAPERHANGER_LIBRARY_FOLDERS": "only,three,here"})


def test_library_stems_unions_every_namespace_casefolded(tmp_path):
    lib_root, orig, extra = tmp_path / "lib", tmp_path / "orig", tmp_path / "extra"
    (lib_root / "desktop/primary").mkdir(parents=True)
    (lib_root / "phone/primary").mkdir(parents=True)
    (lib_root / "desktop/primary/Aa_bb_1111_desktop_7680x4800_native.heic").touch()
    (lib_root / "phone/primary/cc_dd_2222_left_ml_res_4x_phone.heic").touch()
    orig.mkdir(); (orig / "ee_ff_3333.jpg").touch(); (orig / ".DS_Store").touch()
    extra.mkdir(); (extra / "gg_hh_4444.png").touch()
    cfg = lib.load_config({"PAPERHANGER_LIBRARY": str(lib_root), "PAPERHANGER_ORIGINALS": str(orig),
                           "PAPERHANGER_EXTRA_STEM_DIRS": str(extra),
                           "PAPERHANGER_NAS": "box:/share"})
    stems = lib.library_stems(cfg, nas_lister=lambda _cfg: ["ii_jj_5555.jpg", "@meta"])
    assert stems == {"aa_bb_1111", "cc_dd_2222", "ee_ff_3333", "gg_hh_4444", "ii_jj_5555"}


# --------------------------------------------------------------------------- renaming

def _batch(tmp_path):
    """A batch dir laid out the way paperhanger leaves it, outputs as placeholder bytes."""
    batch = tmp_path / "batch"
    (batch / "originals").mkdir(parents=True)
    pixels.write_png(batch / "originals" / "Some-Photo-Ab12.png", 1400, 875)
    pixels.write_png(batch / "originals" / "IMG_0001.png", 700, 1120)
    images, _ = cli.scan(batch / "originals")
    works = [plan.plan_photo(s, w, h, list(sizes.DEVICES), _opts(batch)) for s, w, h, _ in images]
    for w in works:
        for p in w.plans:
            p.destination.parent.mkdir(parents=True, exist_ok=True)
            p.destination.write_bytes(p.destination.name.encode())
    return batch, works


def test_build_moves_renames_outputs_keeping_every_factor_token(tmp_path):
    batch, works = _batch(tmp_path)
    table = {"Some-Photo-Ab12.png": "red_leaf_1234", "IMG_0001.png": "blue_sky_5678"}
    moves = lib.build_moves(works, table)
    outputs = [(a, b) for a, b in moves if a.suffix == ".heic"]
    assert len(outputs) == sum(len(w.plans) for w in works)
    for old, new in outputs:
        old_stem = lib.parse_output_name(old.name)[0]
        assert new.name == old.name.replace(old_stem, table[f"{old_stem}.png"], 1)
        assert new.parent == old.parent
    assert all(a.suffix == ".png" for a, _ in moves[-2:])
    assert all(a.suffix == ".heic" for a, _ in moves[:-2])


def test_build_moves_refuses_a_missing_output(tmp_path):
    batch, works = _batch(tmp_path)
    works[0].plans[0].destination.unlink()
    with pytest.raises(lib.BatchError):
        lib.build_moves(works, {w.source.name: f"a_b_{1000 + i}" for i, w in enumerate(works)})


def test_apply_moves_rolls_back_everything_when_a_link_fails(tmp_path):
    batch, works = _batch(tmp_path)
    table = {"Some-Photo-Ab12.png": "red_leaf_1234", "IMG_0001.png": "blue_sky_5678"}
    moves = lib.build_moves(works, table)
    before = sorted(p.name for p in batch.rglob("*") if p.is_file())
    calls = itertools.count()

    def flaky_link(src, dst):
        if next(calls) == 3:
            raise OSError("disk full")
        os.link(src, dst)

    with pytest.raises(lib.BatchError):
        lib.apply_moves(moves, link=flaky_link)
    assert sorted(p.name for p in batch.rglob("*") if p.is_file()) == before


def test_apply_moves_refuses_to_overwrite(tmp_path):
    batch, works = _batch(tmp_path)
    moves = lib.build_moves(works, {"Some-Photo-Ab12.png": "red_leaf_1234",
                                    "IMG_0001.png": "blue_sky_5678"})
    moves[0][1].write_bytes(b"already here")
    with pytest.raises(lib.BatchError):
        lib.apply_moves(moves)
    assert moves[0][1].read_bytes() == b"already here"


# --------------------------------------------------------------------------- integrating

def _cfg(tmp_path, nas=True):
    env = {"PAPERHANGER_LIBRARY": str(tmp_path / "library"),
           "PAPERHANGER_ORIGINALS": str(tmp_path / "corpus")}
    if nas:
        env["PAPERHANGER_NAS"] = "box:/share/wallpaper"
    (tmp_path / "corpus").mkdir(exist_ok=True)
    return lib.load_config(env)


def test_stage_review_flattens_below_target_into_device_folders(tmp_path):
    batch, works = _batch(tmp_path)
    staged = lib.stage_review(batch)
    names = {p.name for p in (batch / "review" / "desktop").iterdir()}
    names |= {p.name for p in (batch / "review" / "phone").iterdir()}
    assert staged == len(names) == sum(len(w.plans) for w in works)
    assert (batch / "review" / "desktop-secondary").is_dir()
    with pytest.raises(lib.BatchError):
        lib.stage_review(batch)                                   # runs once


def test_add_originals_copies_then_resumes_and_refuses_a_stem_clash(tmp_path):
    batch, _ = _batch(tmp_path)
    cfg = _cfg(tmp_path)
    assert lib.add_originals(batch, cfg) == 2
    assert {p.name for p in cfg.originals.iterdir()} == {"Some-Photo-Ab12.png", "IMG_0001.png"}
    assert lib.add_originals(batch, cfg) == 0                     # identical: already done
    (cfg.originals / "IMG_0001.png").rename(cfg.originals / "IMG_0001.jpg")
    with pytest.raises(lib.BatchError):                            # same stem, other file
        lib.add_originals(batch, cfg)


def test_add_to_library_routes_review_folders_and_hash_checks(tmp_path):
    batch, _ = _batch(tmp_path)
    lib.stage_review(batch)
    one = next((batch / "review" / "phone").iterdir())
    one.rename(batch / "review" / "phone-secondary" / one.name)
    cfg = _cfg(tmp_path)
    added = lib.add_to_library(batch, cfg)
    assert (cfg.library / "phone/secondary" / one.name).exists()
    assert added == sum(1 for _ in (batch / "review").rglob("*.heic"))


def test_add_to_nas_checks_then_copies_then_verifies_through_the_run_seam(tmp_path):
    batch, _ = _batch(tmp_path)
    cfg = _cfg(tmp_path)
    names = sorted(p.name for p in (batch / "originals").iterdir())
    hashes = {n: lib.sha256(batch / "originals" / n) for n in names}
    calls = []

    class Done:
        def __init__(self, out=""):
            self.returncode, self.stdout, self.stderr = 0, out, ""

    copied = []

    def run(argv, **_kw):
        calls.append(argv)
        if argv[0] == "scp":
            copied.append(True)
        if argv[0] == "ssh" and "sha256sum" in argv[-1] and copied:
            return Done("".join(f"{hashes[n]}  {n}\n" for n in names))
        return Done()

    lib.add_to_nas(batch, cfg, run=run)
    kinds = [c[0] for c in calls]
    assert kinds.index("scp") > 0 and kinds[0] == "ssh"          # pre-check before copy
    scp = next(c for c in calls if c[0] == "scp")
    assert "-O" in scp and scp[-1] == "box:/share/wallpaper/"


def test_add_to_nas_refuses_when_a_name_already_exists(tmp_path):
    batch, _ = _batch(tmp_path)
    cfg = _cfg(tmp_path)

    class Found:
        returncode, stdout, stderr = 0, "IMG_0001.png\n", ""

    with pytest.raises(lib.BatchError):
        lib.add_to_nas(batch, cfg, run=lambda argv, **kw: Found())


def test_add_to_nas_without_a_configured_nas_is_a_config_error(tmp_path):
    batch, _ = _batch(tmp_path)
    with pytest.raises(lib.ConfigError):
        lib.add_to_nas(batch, _cfg(tmp_path, nas=False), run=None)


# --------------------------------------------------------------------------- the CLIs

import json
import subprocess
import sys

REPO = Path(__file__).resolve().parent.parent


def _cli(module, *args, env_extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PAPERHANGER_")}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", f"library.{module}", *args], cwd=REPO,
                          capture_output=True, text=True, env=env, timeout=120)


def test_rename_batch_cli_dry_run_then_apply(tmp_path):
    batch, works = _batch(tmp_path)
    (batch / "words.json").write_text(json.dumps(
        {"Some-Photo-Ab12.png": "red_leaf", "IMG_0001.png": "blue_sky"}))
    env = {"PAPERHANGER_LIBRARY": str(tmp_path / "library"),
           "PAPERHANGER_ORIGINALS": str(tmp_path / "corpus")}
    dry = _cli("rename_batch", "--batch", str(batch), "--words", str(batch / "words.json"),
               env_extra=env)
    assert dry.returncode == 0, dry.stderr
    table = {r["old"]: r["new"] for r in json.loads(dry.stdout)["table"]}
    assert set(table) == {"Some-Photo-Ab12.png", "IMG_0001.png"}
    assert (batch / "originals" / "IMG_0001.png").exists()            # dry run renamed nothing
    applied = _cli("rename_batch", "--batch", str(batch), "--words", str(batch / "words.json"),
                   "--apply", env_extra=env)
    assert applied.returncode == 0, applied.stderr
    assert {p.stem for p in (batch / "originals").iterdir()} == set(table.values())


def test_integrate_batch_cli_stage_review_and_originals(tmp_path):
    batch, works = _batch(tmp_path)
    env = {"PAPERHANGER_LIBRARY": str(tmp_path / "library"),
           "PAPERHANGER_ORIGINALS": str(tmp_path / "corpus")}
    r = _cli("integrate_batch", "stage-review", "--batch", str(batch), env_extra=env)
    assert r.returncode == 0 and json.loads(r.stdout)["staged"] == sum(len(w.plans) for w in works)
    r = _cli("integrate_batch", "apply", "--batch", str(batch), "--to", "originals",
             "--dry-run", env_extra=env)
    assert r.returncode == 0 and json.loads(r.stdout)["to_add"] == 2
    assert not (tmp_path / "corpus").exists() or not any((tmp_path / "corpus").iterdir())


def test_integrate_batch_cli_without_config_is_a_usage_error(tmp_path):
    batch, _ = _batch(tmp_path)
    r = _cli("integrate_batch", "stage-review", "--batch", str(batch))
    assert r.returncode == 2 and "PAPERHANGER_LIBRARY" in r.stderr


def test_verify_outputs_cli_finds_every_planned_output(tmp_path):
    batch, works = _batch(tmp_path)
    works[0].plans[0].destination.unlink()
    r = _cli("verify_outputs", "--sources", str(batch / "originals"), "--processing", str(batch))
    report = json.loads(r.stdout)
    assert r.returncode == 1 and len(report["missing"]) == 1


# --------------------------------------------------------------------------- review fixes

import hashlib
import shlex


class _Done:
    def __init__(self, out="", rc=0, err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_assign_suffixes_redraws_when_an_approved_stem_is_no_longer_free():
    table = lib.assign_suffixes({"a.jpg": "red_leaf"}, {"red_leaf_4321"}, rand=_counter(10),
                                persisted={"a.jpg": "red_leaf_4321"})
    assert table == {"a.jpg": "red_leaf_1010"}


def test_assign_suffixes_redraws_when_the_words_changed():
    table = lib.assign_suffixes({"a.jpg": "blue_leaf"}, set(), rand=_counter(10),
                                persisted={"a.jpg": "red_leaf_4321"})
    assert table == {"a.jpg": "blue_leaf_1010"}


@pytest.mark.parametrize("key", ["PAPERHANGER_LIBRARY", "PAPERHANGER_ORIGINALS"])
def test_load_config_refuses_an_empty_path(tmp_path, key):
    env = {"PAPERHANGER_LIBRARY": str(tmp_path), "PAPERHANGER_ORIGINALS": str(tmp_path)}
    env[key] = ""
    with pytest.raises(lib.ConfigError):
        lib.load_config(env)


@pytest.mark.parametrize("folders", ["/abs,d/b,p,p/b", "d/a,../out,p,p/b", "d/a,d/b,,p/b"])
def test_load_config_refuses_folders_that_escape_the_root(tmp_path, folders):
    with pytest.raises(lib.ConfigError):
        lib.load_config({"PAPERHANGER_LIBRARY": str(tmp_path), "PAPERHANGER_ORIGINALS": str(tmp_path),
                         "PAPERHANGER_LIBRARY_FOLDERS": folders})


def test_apply_moves_leaves_no_stray_link_when_an_unlink_fails(tmp_path):
    batch, works = _batch(tmp_path)
    moves = lib.build_moves(works, {"Some-Photo-Ab12.png": "red_leaf_1234",
                                    "IMG_0001.png": "blue_sky_5678"})
    before = sorted(p.name for p in batch.rglob("*") if p.is_file())
    calls = itertools.count()

    def flaky_unlink(path):
        if next(calls) == 2:
            raise OSError("busy")
        os.unlink(path)

    with pytest.raises(lib.BatchError):
        lib.apply_moves(moves, unlink=flaky_unlink)
    assert sorted(p.name for p in batch.rglob("*") if p.is_file()) == before


def test_clone_refuses_a_copy_whose_bytes_differ(tmp_path):
    src, dst = tmp_path / "a.bin", tmp_path / "b.bin"
    src.write_bytes(b"one")

    def bad_cp(argv, **_kw):
        Path(argv[-1]).write_bytes(b"two")
        return _Done()

    with pytest.raises(lib.BatchError):
        lib._clone(src, dst, run=bad_cp)


def test_add_to_library_refuses_a_different_file_of_the_same_name(tmp_path):
    batch, _ = _batch(tmp_path)
    lib.stage_review(batch)
    cfg = _cfg(tmp_path)
    one = next((batch / "review" / "desktop").iterdir())
    (cfg.library / "desktop/primary").mkdir(parents=True)
    (cfg.library / "desktop/primary" / one.name).write_bytes(b"someone else")
    with pytest.raises(lib.BatchError):
        lib.add_to_library(batch, cfg)


def test_add_to_library_resumes_over_identical_files(tmp_path):
    batch, _ = _batch(tmp_path)
    lib.stage_review(batch)
    cfg = _cfg(tmp_path)
    total = lib.add_to_library(batch, cfg)
    victim = next((cfg.library / "phone/primary").iterdir())
    victim.unlink()                                   # as if the first run stopped short
    assert lib.add_to_library(batch, cfg) == 1
    assert victim.exists() and total > 1


def test_add_originals_carries_every_archived_file_not_only_known_extensions(tmp_path):
    batch, _ = _batch(tmp_path)
    (batch / "originals" / "scan-0002.psd").write_bytes(b"paperhanger judged this an image")
    cfg = _cfg(tmp_path)
    assert lib.add_originals(batch, cfg) == 3
    assert (cfg.originals / "scan-0002.psd").exists()


def test_nas_commands_quote_every_name_and_path(tmp_path):
    batch = tmp_path / "batch"
    (batch / "originals").mkdir(parents=True)
    pixels.write_png(batch / "originals" / "it's $(x) here.png", 64, 48)
    cfg = lib.load_config({"PAPERHANGER_LIBRARY": str(tmp_path / "l"),
                           "PAPERHANGER_ORIGINALS": str(tmp_path / "o"),
                           "PAPERHANGER_NAS": "box:/share/wall paper"})
    digest = lib.sha256(batch / "originals" / "it's $(x) here.png")
    remote, copied = [], []

    def run(argv, **_kw):
        if argv[0] == "scp":
            copied.append(argv)
        if argv[0] == "ssh":
            remote.append(argv[-1])
            if "sha256sum" in argv[-1] and copied:
                return _Done(f"{digest}  it's $(x) here.png\n")
        return _Done()

    lib.add_to_nas(batch, cfg, run=run)
    for command in remote:
        words = shlex.split(command)
        assert "/share/wall paper" in words and "it's $(x) here.png" in words
    assert shlex.split(copied[0][-1].split(":", 1)[1]) == ["/share/wall paper/"]


def test_nas_hashes_raises_when_ssh_fails(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(lib.BatchError):
        lib.nas_hashes(cfg, ["a.jpg"], run=lambda argv, **kw: _Done(rc=255, err="no route"))


def test_add_to_nas_refuses_a_wrong_remote_digest(tmp_path):
    batch, _ = _batch(tmp_path)
    cfg = _cfg(tmp_path)
    names = sorted(p.name for p in (batch / "originals").iterdir())

    def run(argv, **_kw):
        if argv[0] == "ssh" and "sha256sum" in argv[-1]:
            return _Done("".join(f"{'0' * 64}  {n}\n" for n in names))
        return _Done()

    with pytest.raises(lib.BatchError):
        lib.add_to_nas(batch, cfg, run=run)


def test_upload_drained_is_none_without_brctl():
    def missing(argv, **_kw):
        raise FileNotFoundError(argv[0])
    assert lib.upload_drained(run=missing) is None


# --- verify and cleanup: the destructive path


def _staged(tmp_path):
    """A source dir, its manifest, and a batch copied from it and fully integrated."""
    source, works = _batch(tmp_path)                           # paperhanger's output
    batch = tmp_path / "staged"
    subprocess.run(["/bin/cp", "-R", str(source), str(batch)], check=True)
    lines = sorted(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(source)}"
                   for p in source.rglob("*") if p.is_file())
    (batch / "source.sha256").write_text("\n".join(lines) + "\n")
    cfg = _cfg(tmp_path, nas=False)
    lib.stage_review(batch)
    lib.add_to_library(batch, cfg)
    lib.add_originals(batch, cfg)
    return source, batch, cfg


def test_verify_passes_on_an_integrated_batch(tmp_path):
    _, batch, cfg = _staged(tmp_path)
    assert lib.verify(batch, cfg)["errors"] == []


def test_verify_catches_a_survivor_missing_from_the_library(tmp_path):
    _, batch, cfg = _staged(tmp_path)
    next((cfg.library / "desktop/primary").iterdir()).unlink()
    assert lib.verify(batch, cfg)["errors"]


def test_cleanup_deletes_exactly_the_source_and_the_batch(tmp_path):
    source, batch, cfg = _staged(tmp_path)
    result = lib.cleanup(batch, [source], cfg)
    assert result["errors"] == [] and not source.exists() and not batch.exists()
    assert (tmp_path / "staged.cleanup.json").exists()           # the record outlives the batch
    assert any(cfg.originals.iterdir()) and any((cfg.library / "desktop/primary").iterdir())


def test_cleanup_dry_run_deletes_nothing(tmp_path):
    source, batch, cfg = _staged(tmp_path)
    result = lib.cleanup(batch, [source], cfg, dry=True)
    assert result["errors"] == [] and source.exists() and batch.exists()


def test_cleanup_refuses_when_the_source_changed(tmp_path):
    source, batch, cfg = _staged(tmp_path)
    (source / "originals" / "late-arrival.png").write_bytes(b"x")
    assert lib.cleanup(batch, [source], cfg)["errors"] and source.exists()


def test_cleanup_refuses_when_a_source_file_never_reached_the_batch(tmp_path):
    source, batch, cfg = _staged(tmp_path)
    dropped = next((batch / "originals").iterdir())
    dropped.unlink()                                   # the staging copy lost a file
    (cfg.originals / dropped.name).unlink()
    assert lib.cleanup(batch, [source], cfg)["errors"] and source.exists()


def test_cleanup_refuses_when_verify_fails(tmp_path):
    source, batch, cfg = _staged(tmp_path)
    next(cfg.originals.iterdir()).unlink()
    assert lib.cleanup(batch, [source], cfg)["errors"] and source.exists()


def test_cleanup_refuses_an_extra_source_that_holds_files(tmp_path):
    source, batch, cfg = _staged(tmp_path)
    drop = tmp_path / "drop"
    drop.mkdir()
    (drop / "new.jpg").write_bytes(b"x")
    assert lib.cleanup(batch, [source, drop], cfg)["errors"] and drop.exists()


def test_cleanup_allows_an_empty_extra_source(tmp_path):
    source, batch, cfg = _staged(tmp_path)
    drop = tmp_path / "drop"
    drop.mkdir()
    (drop / ".DS_Store").write_bytes(b"")
    assert lib.cleanup(batch, [source, drop], cfg)["errors"] == [] and not drop.exists()


def test_cleanup_refuses_to_delete_a_configured_home(tmp_path):
    source, batch, cfg = _staged(tmp_path)
    assert lib.cleanup(batch, [source, cfg.originals], cfg)["errors"]
    assert cfg.originals.exists()


def test_cleanup_refuses_an_empty_manifest(tmp_path):
    source, batch, cfg = _staged(tmp_path)
    (batch / "source.sha256").write_text("")
    assert lib.cleanup(batch, [source], cfg)["errors"] and source.exists()


# --------------------------------------------------------------------------- re-review fixes

def test_originals_of_any_type_are_carried_and_cleanup_proves_it(tmp_path):
    source, batch, cfg = _staged(tmp_path)
    odd = b"not a png, but paperhanger moved it here"
    (source / "originals" / "scan-0001.avif").write_bytes(odd)
    (batch / "originals" / "scan-0001.avif").write_bytes(odd)
    lines = sorted(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(source)}"
                   for p in source.rglob("*") if p.is_file())
    (batch / "source.sha256").write_text("\n".join(lines) + "\n")
    assert lib.cleanup(batch, [source], cfg)["errors"], "an original not yet in the corpus"
    assert lib.add_originals(batch, cfg) == 1                      # the .avif, now
    assert (cfg.originals / "scan-0001.avif").read_bytes() == odd
    assert lib.cleanup(batch, [source], cfg)["errors"] == []


def test_cleanup_requires_each_manifest_original_among_the_batch_originals(tmp_path):
    source, batch, cfg = _staged(tmp_path)
    one = next((batch / "originals").iterdir())
    (batch / "to_sort_desktop" / "stray.bin").write_bytes(one.read_bytes())  # same bytes elsewhere
    one.unlink()
    (cfg.originals / one.name).unlink()
    assert lib.cleanup(batch, [source], cfg)["errors"] and source.exists()


def test_nas_hash_script_fails_loudly_rather_than_reporting_absence(tmp_path):
    batch, _ = _batch(tmp_path)
    cfg = _cfg(tmp_path)
    seen = []

    def run(argv, **_kw):
        seen.append(argv[-1])
        return _Done()

    lib.nas_hashes(cfg, ["a.jpg"], run=run)
    script = seen[0]
    assert "|| exit" in script and "command -v sha256sum" in script
    assert not script.rstrip().endswith("true")


def test_cleanup_refuses_targets_that_overlap_each_other(tmp_path):
    source, batch, cfg = _staged(tmp_path)
    nested = source / "originals"
    result = lib.cleanup(batch, [source, nested], cfg)
    assert result["errors"] and source.exists() and batch.exists()


def test_cleanup_refuses_a_symlinked_target(tmp_path):
    source, batch, cfg = _staged(tmp_path)
    link = tmp_path / "drop-link"
    empty = tmp_path / "empty"
    empty.mkdir()
    link.symlink_to(empty)
    assert lib.cleanup(batch, [source, link], cfg)["errors"] and empty.exists()


def test_cleanup_records_a_failure_partway_through_deletion(tmp_path, monkeypatch):
    source, batch, cfg = _staged(tmp_path)
    real = lib.shutil.rmtree

    def flaky(path, *a, **kw):
        if Path(path) == batch:
            raise PermissionError("locked")
        return real(path, *a, **kw)

    monkeypatch.setattr(lib.shutil, "rmtree", flaky)
    result = lib.cleanup(batch, [source], cfg)
    assert result["errors"] and not source.exists() and batch.exists()
    record = json.loads((tmp_path / "staged.cleanup.json").read_text())
    assert any("locked" in e for e in record["errors"])


def test_place_resumes_past_a_leftover_partial(tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"x" * 1000)
    dst = tmp_path / "out" / "a.bin"
    dst.parent.mkdir()
    (dst.parent / "a.bin.partial").write_bytes(b"x" * 10)          # an interrupted copy
    assert lib._place([(src, dst)], "here") == 1
    assert dst.read_bytes() == src.read_bytes()
    assert not (dst.parent / "a.bin.partial").exists()


@pytest.mark.parametrize("nas", ["-oProxyCommand=x:/share", "box:"])
def test_load_config_refuses_a_bad_nas(tmp_path, nas):
    with pytest.raises(lib.ConfigError):
        lib.load_config({"PAPERHANGER_LIBRARY": str(tmp_path), "PAPERHANGER_ORIGINALS": str(tmp_path),
                         "PAPERHANGER_NAS": nas})


def test_load_config_keeps_a_root_nas_path(tmp_path):
    cfg = lib.load_config({"PAPERHANGER_LIBRARY": str(tmp_path), "PAPERHANGER_ORIGINALS": str(tmp_path),
                           "PAPERHANGER_NAS": "box:/"})
    assert cfg.nas == ("box", "/")


def test_load_config_refuses_duplicate_folders(tmp_path):
    with pytest.raises(lib.ConfigError):
        lib.load_config({"PAPERHANGER_LIBRARY": str(tmp_path), "PAPERHANGER_ORIGINALS": str(tmp_path),
                         "PAPERHANGER_LIBRARY_FOLDERS": "d,d,p,q"})
