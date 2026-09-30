"""Shared logic for the library tools: config, naming, renaming, and add-only copying.

A *library* is where finished wallpapers live, in four folders (a primary and a secondary
folder per device). An *originals corpus* holds the source images a full rebuild starts
from; an optional *NAS* holds a second copy of the originals. A *batch* is a paperhanger
processing directory (`originals/`, `to_sort_{desktop,phone}/{,below_target/}`) that has
been named and curated and is ready to join the library.

Nothing here has a personal default. Paths come from the environment (or the caller):

    PAPERHANGER_LIBRARY          library root                              (required)
    PAPERHANGER_ORIGINALS        originals corpus                          (required)
    PAPERHANGER_LIBRARY_FOLDERS  four folders, comma-separated, in the order
                                 desktop, desktop-secondary, phone, phone-secondary
                                 (default: desktop/primary,desktop/secondary,phone/primary,phone/secondary)
    PAPERHANGER_NAS              host:/path of a second originals copy     (optional)
    PAPERHANGER_EXTRA_STEM_DIRS  more directories whose stems are taken    (optional, os.pathsep-separated)
    PAPERHANGER_ICLOUD_CHECK     "1" when the library lives in iCloud Drive: verify then reports
                                 whether it has finished syncing, and cleanup waits for it

Every copy is add-only: an existing name is a refusal, never an overwrite. Every copy is
followed by a hash check. Everything that talks to another machine goes through a `run`
parameter so it can be tested without one.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shlex
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from paperhanger import plan

POSITIONS = ("top", "middle", "bottom", "left", "center", "right")
ROLES = ("desktop", "desktop-secondary", "phone", "phone-secondary")
DEFAULT_FOLDERS = "desktop/primary,desktop/secondary,phone/primary,phone/secondary"

_OUTPUT_RE = re.compile(
    r"^(?P<stem>.+?)(?:_(?P<pos>" + "|".join(POSITIONS) + r"))?"
    r"_(?P<device>desktop|phone)_\d+x\d+_(?:native|\d+(?:\.\d)?x)\.heic$")
# The Pixelmator pipeline this tool replaced (legacy/make_wallpaper.zsh) named its outputs
# <stem>[_<pos>]_ml_res[_lt]_<N>x_<device>. Libraries built by it still hold such files.
_LEGACY_RE = re.compile(
    r"^(?P<stem>.+?)(?:_(?P<pos>" + "|".join(POSITIONS) + r"))?"
    r"_ml_res(?:_lt)?_\d+x_(?P<device>desktop|phone)(?: copy)?\.(?:heic|jpeg)$")
_STEM_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+){1,5}_\d{4}$")
_BANNED_WORD = re.compile(r"^(ml|res|desktop|phone|x|\d+x)$")


class ConfigError(Exception):
    """A required setting is missing or malformed."""


class BatchError(Exception):
    """A batch cannot be renamed or added as asked; nothing was changed."""


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


# --------------------------------------------------------------------------- config

@dataclass(frozen=True)
class Config:
    library: Path
    originals: Path
    folders: dict
    nas: tuple | None
    extra_stem_dirs: tuple
    icloud_check: bool = False


def load_config(env) -> Config:
    for key in ("PAPERHANGER_LIBRARY", "PAPERHANGER_ORIGINALS"):
        if not env.get(key, "").strip():
            raise ConfigError(f"{key} is not set")
    library = Path(env["PAPERHANGER_LIBRARY"]).expanduser()
    originals = Path(env["PAPERHANGER_ORIGINALS"]).expanduser()
    folders = [f.strip() for f in env.get("PAPERHANGER_LIBRARY_FOLDERS", DEFAULT_FOLDERS).split(",")]
    if len(folders) != 4 or not all(folders):
        raise ConfigError("PAPERHANGER_LIBRARY_FOLDERS needs four folders: "
                          "desktop, desktop-secondary, phone, phone-secondary")
    for f in folders:
        if Path(f).is_absolute() or ".." in Path(f).parts:
            raise ConfigError(f"library folder {f!r} must be relative to the library root")
    if len({Path(f) for f in folders}) != 4:
        raise ConfigError("PAPERHANGER_LIBRARY_FOLDERS names the same folder twice")
    nas = None
    if env.get("PAPERHANGER_NAS"):
        host, sep, path = env["PAPERHANGER_NAS"].partition(":")
        if not (host and sep and path.startswith("/")) or host.startswith("-"):
            raise ConfigError("PAPERHANGER_NAS must look like host:/absolute/path")
        nas = (host, path.rstrip("/") or "/")
    extra = tuple(Path(p).expanduser() for p in
                  env.get("PAPERHANGER_EXTRA_STEM_DIRS", "").split(os.pathsep) if p)
    return Config(library, originals, dict(zip(ROLES, folders)), nas, extra,
                  env.get("PAPERHANGER_ICLOUD_CHECK", "") == "1")


# --------------------------------------------------------------------------- names

def parse_output_name(name: str):
    """(stem, position, device) for a paperhanger output filename, else None."""
    m = _OUTPUT_RE.match(nfc(name))
    return (m["stem"], m["pos"], m["device"]) if m else None


def parse_legacy_output_name(name: str):
    """(stem, position, device) for a legacy Pixelmator-pipeline filename, else None."""
    m = _LEGACY_RE.match(nfc(name))
    return (m["stem"], m["pos"], m["device"]) if m else None


def stem_of_library_file(name: str) -> str:
    parsed = parse_output_name(name) or parse_legacy_output_name(name)
    return parsed[0] if parsed else nfc(name).rsplit(".", 1)[0]


def validate_stem(stem: str) -> list:
    problems = []
    if not _STEM_RE.match(stem):
        problems.append(f"{stem!r}: want 2-6 lowercase words and a 4-digit suffix, "
                        "joined by underscores")
    if any(_BANNED_WORD.match(w) for w in stem.split("_")[:-1]):
        problems.append(f"{stem!r}: contains a word that reads as part of an output name")
    return problems


def assign_suffixes(words: dict, taken: set, rand=secrets.randbelow, persisted=None,
                    attempts: int = 500) -> dict:
    """{original filename: "<words>_<NNNN>"}, every stem new to `taken` and to the batch.

    `persisted` (a table approved earlier) is kept wherever it is still free, so applying
    renames to exactly what was shown. No stem is handed out that would be a prefix of a
    stem already taken, so `<new>_...` never reads as a slice of something else.
    """
    persisted = persisted or {}
    taken = {t.casefold() for t in taken}
    table = {}

    def free(stem):
        s = stem.casefold()
        return s not in taken and not any(t.startswith(s + "_") for t in taken)

    for original in sorted(words):
        base = words[original].strip()
        prev = persisted.get(original)
        if prev and prev.rsplit("_", 1)[0] == base and free(prev):
            stem = prev
        else:
            for _ in range(attempts):
                stem = f"{base}_{rand(9000) + 1000}"
                if free(stem):
                    break
            else:
                raise BatchError(f"no free suffix for {base!r}")
        problems = validate_stem(stem)
        if problems:
            raise BatchError("; ".join(problems))
        taken.add(stem.casefold())
        table[original] = stem
    return table


def _names(directory: Path) -> list:
    try:
        return [n for n in os.listdir(directory) if not n.startswith((".", "@"))]
    except FileNotFoundError:
        return []


def nas_listing(cfg: Config, run=subprocess.run) -> list:
    host, path = cfg.nas
    r = run(["ssh", "-o", "BatchMode=yes", host, f"ls -1 {shlex.quote(path)}"],
            capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise BatchError(f"cannot list {host}:{path}: {r.stderr.strip()[-200:]}")
    return r.stdout.splitlines()


def library_stems(cfg: Config, nas_lister=None) -> set:
    """Every stem already in use, casefolded: library (both namings), originals, extras, NAS."""
    stems = set()
    for folder in cfg.folders.values():
        stems |= {stem_of_library_file(n) for n in _names(cfg.library / folder)}
    for directory in (cfg.originals, *cfg.extra_stem_dirs):
        stems |= {nfc(n).rsplit(".", 1)[0] for n in _names(directory)}
    if cfg.nas:
        lister = nas_lister or nas_listing
        stems |= {nfc(n).rsplit(".", 1)[0] for n in lister(cfg) if not n.startswith((".", "@"))}
    return {s.casefold() for s in stems}


# --------------------------------------------------------------------------- renaming

def build_moves(works, table: dict) -> list:
    """[(old path, new path)]: every output first, then every original.

    Names come from paperhanger's own `plan.output_name`, so the position, size and factor
    token are rebuilt exactly and only the stem changes.
    """
    outputs, originals = [], []
    for work in works:
        new_stem = table[work.source.name]
        for p in work.plans:
            if not p.destination.exists():
                raise BatchError(f"planned output is missing: {p.destination}")
            name = plan.output_name(work.source.with_stem(new_stem), p.position, p.device,
                                    p.out_width, p.out_height, p.factor, p.fmt)
            outputs.append((p.destination, p.destination.with_name(name)))
        originals.append((work.source, work.source.with_stem(new_stem)))
    moves = outputs + originals
    targets = [str(b).casefold() for _, b in moves]
    if len(set(targets)) != len(targets):
        raise BatchError("two renames would land on the same name")
    return moves


def apply_moves(moves: list, link=os.link, unlink=os.unlink) -> None:
    """Rename by link-then-unlink (an existing target fails instead of being replaced).
    Any failure undoes every step already taken, including a link whose unlink failed."""
    done = []                       # (src, dst, unlinked)
    try:
        for src, dst in moves:
            link(src, dst)
            done.append([src, dst, False])
            unlink(src)
            done[-1][2] = True
    except OSError as error:
        problems = []
        for src, dst, unlinked in reversed(done):
            try:
                if unlinked:
                    os.link(dst, src)
                os.unlink(dst)
            except OSError as undo:
                problems.append(f"{dst}: {undo}")
        detail = f"; could not undo {problems}" if problems else ""
        raise BatchError(f"rename failed and was rolled back: {error}{detail}") from error


# --------------------------------------------------------------------------- copying

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def files(directory: Path) -> list:
    return sorted(p for p in directory.iterdir() if p.is_file() and not p.name.startswith(".")) \
        if directory.is_dir() else []


def _clone(src: Path, dst: Path, run=subprocess.run) -> None:
    """APFS clone where possible; never overwrites. /bin/cp is BSD cp on macOS."""
    r = run(["/bin/cp", "-c", "-n", "-p", str(src), str(dst)], capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists() or sha256(src) != sha256(dst):
        raise BatchError(f"copy failed or differs: {src} -> {dst}")


def _place(pairs: list, where: str) -> int:
    """Copy each (src, dst). A destination holding the same bytes counts as done, so an
    interrupted copy can be re-run; one holding different bytes refuses the whole call."""
    clash = [str(d) for s, d in pairs if d.exists() and sha256(s) != sha256(d)]
    if clash:
        raise BatchError(f"a different file of the same name is already {where}: {clash[:5]}")
    todo = [(s, d) for s, d in pairs if not d.exists()]
    for _, d in todo:
        d.parent.mkdir(parents=True, exist_ok=True)
    for s, d in todo:
        partial = d.with_name(d.name + ".partial")     # a copy lands whole or not at all
        partial.unlink(missing_ok=True)
        _clone(s, partial)
        os.link(partial, d)
        partial.unlink()
    return len(todo)


def stage_review(batch: Path) -> int:
    """Copy every output into review/<role>/, flattening below_target/. Runs once."""
    review = batch / "review"
    if any(files(review / role) for role in ROLES):
        raise BatchError("review/ already holds files; stage-review runs once")
    for role in ROLES:
        (review / role).mkdir(parents=True, exist_ok=True)
    n = 0
    for device in ("desktop", "phone"):
        for d in (batch / f"to_sort_{device}", batch / f"to_sort_{device}" / "below_target"):
            for f in files(d):
                _clone(f, review / device / f.name)
                n += 1
    return n


def survivors(batch: Path, cfg: Config) -> list:
    return [(f, cfg.library / cfg.folders[role] / f.name)
            for role in ROLES for f in files(batch / "review" / role)]


def add_to_library(batch: Path, cfg: Config) -> int:
    """review/<role>/* -> library/<folder for role>/: add-only, hash-checked, resumable."""
    return _place(survivors(batch, cfg), "in the library")


def batch_originals(batch: Path) -> list:
    """Every file paperhanger archived. It decides what an image is by content, not by
    extension, so nothing here filters by extension: an original left out would be
    deleted by cleanup with no copy anywhere."""
    return files(batch / "originals")


def add_originals(batch: Path, cfg: Config) -> int:
    """batch/originals/* -> the originals corpus: add-only, resumable, and refusing
    any stem the corpus already uses under another extension."""
    src = batch_originals(batch)
    used = {nfc(p.stem).casefold(): p for p in files(cfg.originals)}
    clash = [p.name for p in src
             if nfc(p.stem).casefold() in used and used[nfc(p.stem).casefold()].name != p.name]
    if clash:
        raise BatchError(f"stems already in the originals corpus: {clash[:5]}")
    cfg.originals.mkdir(parents=True, exist_ok=True)
    return _place([(p, cfg.originals / p.name) for p in src], "in the originals corpus")


def _remote(host: str, command: str, run, timeout: int):
    r = run(["ssh", "-o", "BatchMode=yes", host, command],
            capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise BatchError(f"ssh {host} failed ({r.returncode}): {r.stderr.strip()[-200:]}")
    return r.stdout


def nas_hashes(cfg: Config, names: list, run=subprocess.run) -> dict:
    """{name: sha256} for those of `names` present on the NAS."""
    host, path = cfg.nas
    quoted = " ".join(shlex.quote(n) for n in names)
    out = _remote(host, f"cd {shlex.quote(path)} || exit 3; "
                        f"command -v sha256sum >/dev/null || exit 4; "
                        f"for f in {quoted} ; do if [ -e \"$f\" ]; then "
                        f"sha256sum -- \"$f\" || exit 5; fi; done", run, 600)
    hashes = {}
    for line in out.splitlines():
        digest, _, name = line.partition("  ")
        hashes[name] = digest
    return hashes


def add_to_nas(batch: Path, cfg: Config, run=subprocess.run) -> int:
    """batch/originals/* -> the NAS copy: see what is already there, copy the rest
    with `scp -O` (the legacy SCP protocol, for servers without SFTP), then compare sha256
    on the far side. A name already there with the same bytes counts as done; with
    different bytes it refuses before anything is copied. Every name and path is
    shell-quoted, since the remote side runs them through a shell."""
    if not cfg.nas:
        raise ConfigError("PAPERHANGER_NAS is not set")
    host, path = cfg.nas
    src = batch_originals(batch)
    local = {p.name: sha256(p) for p in src}
    present = nas_hashes(cfg, list(local), run=run)
    clash = [n for n, h in present.items() if local.get(n) != h]
    if clash:
        raise BatchError(f"a different file of the same name is already on the NAS: {clash[:5]}")
    todo = [p for p in src if p.name not in present]
    if todo:
        r = run(["scp", "-O", "-p", "-q", *map(str, todo), f"{host}:{shlex.quote(path)}/"],
                capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            raise BatchError(f"scp failed: {r.stderr.strip()[-200:]}")
    remote = nas_hashes(cfg, list(local), run=run)
    bad = [n for n, h in local.items() if remote.get(n) != h]
    if bad:
        raise BatchError(f"NAS copy differs or is missing: {bad[:5]}")
    return len(todo)


def upload_drained(run=subprocess.run):
    """True/False: has iCloud Drive finished syncing (macOS `brctl`)? None if unknowable."""
    try:
        r = run(["brctl", "status", "com.apple.CloudDocs"], capture_output=True, text=True,
                timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return "client:idle" in r.stdout and not any(
        k in r.stdout for k in ("needs-upload", "needs-sync-up", "pending-sync-up"))


# --------------------------------------------------------------------------- verify and cleanup

def verify(batch: Path, cfg: Config, run=subprocess.run) -> dict:
    """Every original in the corpus (and on the NAS, when configured) and every review
    survivor in the library, byte for byte."""
    local = {p.name: sha256(p) for p in batch_originals(batch)}
    errors = [f"original not in the corpus: {n}" for n, h in local.items()
              if not (cfg.originals / n).exists() or sha256(cfg.originals / n) != h]
    if cfg.nas:
        remote = nas_hashes(cfg, list(local), run=run)
        errors += [f"original not on the NAS: {n}" for n, h in local.items() if remote.get(n) != h]
    surv = survivors(batch, cfg)
    errors += [f"survivor not in the library: {d}" for s, d in surv
               if not d.exists() or sha256(s) != sha256(d)]
    idle = upload_drained(run=run) if cfg.icloud_check else None
    if cfg.icloud_check and idle is not True:
        errors.append("iCloud Drive has not finished syncing")
    return {"originals": len(local), "survivors": len(surv),
            "library_counts": {f: len(files(cfg.library / f)) for f in cfg.folders.values()},
            "corpus_count": len(files(cfg.originals)), "icloud_idle": idle, "errors": errors}


def _within(a: Path, b: Path) -> bool:
    a, b = a.resolve(), b.resolve()
    return a == b or b in a.parents


def cleanup(batch: Path, sources: list, cfg: Config, dry: bool = False, run=subprocess.run) -> dict:
    """Delete the batch's source, any empty extra drop folders, and the batch itself, but
    only when every file the source held is accounted for and verify passes.

    Checks, all before anything is deleted:
      - the first source still matches DIR/source.sha256 exactly (nothing added or changed
        since staging), and the manifest is not empty;
      - every original and every output in that manifest reached the batch, by content
        (renaming keeps the bytes);
      - every further source holds no files but .DS_Store;
      - no target is, contains or sits inside the library, the corpus or an extra stem
        directory, and none is or contains the home directory;
      - verify passes.
    The result is written beside the batch (DIR.cleanup.json), since the batch goes.
    """
    result: dict = {"errors": []}
    errors = result["errors"]
    targets = [Path(p) for p in (*sources, batch)]
    homes = [cfg.library, cfg.originals, *cfg.extra_stem_dirs]
    for t in targets:
        if t.is_symlink():
            errors.append(f"refusing to delete {t}: it is a symlink")
        elif any(_within(t, h) or _within(h, t) for h in homes) or _within(Path.home(), t):
            errors.append(f"refusing to delete {t}: it overlaps a library, corpus or home directory")
    for i, a in enumerate(targets):
        for b in targets[i + 1:]:
            if _within(a, b) or _within(b, a):
                errors.append(f"refusing overlapping targets: {a} and {b}")
    manifest = {}
    mpath = batch / "source.sha256"
    for line in (mpath.read_text().splitlines() if mpath.exists() else []):
        digest, _, rel = line.partition("  ")
        if rel:
            manifest[rel.removeprefix("./")] = digest
    if not manifest:
        errors.append("DIR/source.sha256 is missing or empty")
    if not errors:
        src = Path(sources[0])
        now = {str(p.relative_to(src)): sha256(p) for p in src.rglob("*")
               if p.is_file() and p.name != ".DS_Store"}
        if now != manifest:
            errors.append(f"{src} changed since staging; nothing deleted")
        kept_originals = {sha256(p) for p in batch_originals(batch)}
        kept_outputs = {sha256(p) for d in ("to_sort_desktop", "to_sort_phone")
                        for p in (batch / d).rglob("*") if p.is_file()}
        lost = [rel for rel, h in manifest.items()
                if h not in (kept_originals if rel.startswith("originals/") else kept_outputs)]
        if lost:
            errors.append(f"source files never reached the batch: {lost[:5]}")
        for extra in sources[1:]:
            held = [p for p in Path(extra).rglob("*") if p.is_file() and p.name != ".DS_Store"]
            if held:
                errors.append(f"{extra} holds files; only an empty drop folder may be removed")
    if not errors:
        checked = verify(batch, cfg, run=run)
        result.update({k: v for k, v in checked.items() if k != "errors"})
        errors += checked["errors"]
    try:
        if errors:
            errors.append("nothing deleted")
        elif dry:
            result["would_delete"] = [str(t) for t in targets]
        else:
            result["deleted"] = []
            for t in targets:
                try:
                    shutil.rmtree(t)
                    result["deleted"].append(str(t))
                except OSError as e:
                    errors.append(f"could not delete {t}: {e}; stopped here")
                    break
    finally:
        Path(f"{batch}.cleanup.json").write_text(
            __import__("json").dumps(result, ensure_ascii=False, indent=2))
    return result
