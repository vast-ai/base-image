"""Make an external image's /venv/main see the engine's packages as installed (ADR 0048).

An external image's engine lives in its own interpreter's site-packages. uv does not count
packages a venv only INHERITS (include-system-site-packages) as installed, so every
`uv pip install` into an inheriting /venv/main re-resolved as if the engine were absent and
laid a second torch over it. Instead /venv/main is a plain venv whose site-packages mirrors
the engine's, file by file: every directory is real and every file is a symlink to the
engine's copy. uv and pip then see the whole stack as installed, and anything they install,
upgrade or remove lands as real files in the venv -- which is what $WORKSPACE sync carries.

Three rules carry the design, each measured before it was written down:

- No directory is ever a symlink. uv and pip install and uninstall per file path, so a
  directory link sends their writes into the engine's tree.
- Ownership is recorded, not inferred. A project is the farm's exactly when its metadata
  file (METADATA, or PKG-INFO for an egg-info) is a symlink; Debian ships .dist-info
  directories with no RECORD, so RECORD cannot be the marker. Anything with real metadata
  belongs to the venv, and the farm never links into it.
- The farm is built once, into the image. After first boot /venv/main is the user's: no
  boot stage refreshes, prunes or re-links it, so an uninstall stays uninstalled.
"""

from __future__ import annotations

import csv
import json
import os
import re
import stat
import struct
import subprocess
from dataclasses import dataclass, field
from email.parser import HeaderParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

MANIFEST = ".vast-venv-farm.json"
METADATA_SUFFIXES = (".dist-info", ".egg-info")

# The query run by the ENGINE's interpreter: where it imports from, and what it is built on.
# site.getsitepackages() is the interpreter's own answer, in its own order, for a system
# python (Debian lists /usr/local/lib/pythonX.Y/dist-packages, /usr/lib/python3/dist-packages)
# and for a venv engine (only the venv's site-packages) alike.
_ENGINE_QUERY = r"""
import json, os, site, sys
dirs = []
for p in site.getsitepackages():
    r = os.path.realpath(p)
    if os.path.isdir(r) and r not in dirs:
        dirs.append(r)
print(json.dumps({
    "base": os.path.realpath(getattr(sys, "_base_executable", sys.executable)),
    "version": "%d.%d" % sys.version_info[:2],
    "sources": dirs,
}))
"""


class FarmError(Exception):
    pass


def normalize(name: str) -> str:
    """PEP 503 project-name normalisation, so `Foo_Bar` and `foo-bar` are one project."""
    return re.sub(r"[-_.]+", "-", name).lower()


def engine_info(python: str) -> dict:
    out = subprocess.run([python, "-c", _ENGINE_QUERY], check=True, capture_output=True,
                         text=True).stdout
    return json.loads(out)


def venv_site(venv: Path) -> Path:
    found = sorted((venv / "lib").glob("python3*/site-packages"))
    if len(found) != 1:
        raise FarmError(f"{venv}: expected one lib/python3*/site-packages, found {len(found)}")
    return found[0]


# ---------------------------------------------------------------------------------------
# Distribution metadata


@dataclass
class Dist:
    name: str               # normalised project name
    version: str
    meta: str               # the metadata entry's name, relative to its site dir
    files: List[str]        # paths it installed, relative to the site dir (may start "..")
    entry_points: str       # entry_points.txt contents, "" if none


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_meta_headers(meta: Path) -> Tuple[str, str]:
    """(Name, Version) from a .dist-info or .egg-info -- which may be a directory or a file."""
    if meta.is_dir():
        text = _read_text(meta / "METADATA") or _read_text(meta / "PKG-INFO")
    else:
        text = _read_text(meta)
    headers = HeaderParser().parsestr(text)
    name = headers.get("Name") or meta.name.split("-")[0]
    return normalize(name), headers.get("Version") or ""


def _record_files(site: Path, meta: Path) -> Optional[List[str]]:
    """Files a distribution installed, relative to `site`. None when it keeps no list."""
    if not meta.is_dir():
        return None
    record = meta / "RECORD"
    if record.exists():
        with open(record, newline="", encoding="utf-8", errors="replace") as fh:
            return [row[0] for row in csv.reader(fh) if row and row[0]]
    installed = meta / "installed-files.txt"          # a pip-installed egg-info
    if installed.exists():
        rel = meta.relative_to(site)
        return [os.path.normpath(os.path.join(str(rel), line.strip()))
                for line in _read_text(installed).splitlines() if line.strip()]
    return None


def _top_level_files(site: Path, meta: Path) -> List[str]:
    """Fallback for an egg-info with no file list (Debian's apt packages): the trees its
    top_level.txt names, plus the metadata itself."""
    files: List[str] = []
    tops = _read_text(meta / "top_level.txt").split() if meta.is_dir() else []
    for top in tops:
        for cand in (site / top, site / f"{top}.py"):
            if cand.is_file():
                files.append(cand.name)
            elif cand.is_dir():
                files.extend(str(p.relative_to(site)) for p in _walk_files(cand))
        files.extend(p.name for p in site.glob(f"{top}.*.so") if p.is_file())
    return files


def _preference(site: Path, dist: "Dist") -> Tuple[int, int]:
    meta = site / dist.meta
    return (int(dist.meta.endswith(".dist-info")), int((meta / "RECORD").exists()))


def read_dists(site: Path) -> Dict[str, List[Dist]]:
    """Every metadata entry in `site`, grouped by normalised project name, the entry to link
    first. A site can hold two for one project -- Debian ships `cryptography-41.0.7.dist-info`
    AND `cryptography.egg-info` -- and every one must be accounted for, or the unchosen one
    leaks in as a second installed copy."""
    dists: Dict[str, List[Dist]] = {}
    for meta in sorted(site.iterdir()):
        if not meta.name.endswith(METADATA_SUFFIXES):
            continue
        name, version = _read_meta_headers(meta)
        files = _record_files(site, meta)
        if files is None:
            files = _top_level_files(site, meta)
        files = files + [str(p.relative_to(site)) for p in _walk_files(meta)] \
            if meta.is_dir() else files + [meta.name]
        eps = _read_text(meta / "entry_points.txt") if meta.is_dir() else ""
        dists.setdefault(name, []).append(Dist(name, version, meta.name, sorted(set(files)), eps))
    for name in dists:
        dists[name].sort(key=lambda d: _preference(site, d), reverse=True)
    return dists


def _walk_files(root: Path) -> Iterable[Path]:
    """Every non-directory under root, following directory symlinks once (cycle-guarded)."""
    seen: Set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        real = os.path.realpath(dirpath)
        if real in seen:
            dirnames[:] = []
            continue
        seen.add(real)
        for f in filenames:
            yield Path(dirpath) / f


def is_farmed(meta: Path) -> bool:
    """True when a metadata entry was linked in by the farm rather than installed."""
    if meta.is_symlink():
        return True
    if meta.is_dir():
        return any((meta / f).is_symlink() for f in ("METADATA", "PKG-INFO"))
    return False


def _walk_rel(root: Path, prefix: int) -> Iterable[str]:
    """Like _walk_files, as paths relative to the directory whose length is `prefix`."""
    seen: Set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        real = os.path.realpath(dirpath)
        if real in seen:
            dirnames[:] = []
            continue
        seen.add(real)
        base = dirpath[prefix:]
        for f in filenames:
            yield os.path.join(base, f) if base else f


def owned_projects(site: Path) -> Set[str]:
    """Projects the venv itself owns: metadata that is real, not a farm link."""
    return {_read_meta_headers(m)[0] for m in site.iterdir()
            if m.name.endswith(METADATA_SUFFIXES) and not is_farmed(m)}


# ---------------------------------------------------------------------------------------
# Building


@dataclass
class Result:
    projects: Dict[str, dict] = field(default_factory=dict)
    skipped: Dict[str, str] = field(default_factory=dict)
    links: int = 0
    dirs: int = 0
    residual: int = 0
    scripts: List[str] = field(default_factory=list)


def _inside(rel: str) -> bool:
    return not os.path.normpath(rel).startswith("..") and not os.path.isabs(rel)


class _Farm:
    def __init__(self, dst: Path):
        self.dst = dst
        self.created: Set[Path] = set()      # directories the farm made
        self.foreign: Set[Path] = set()      # pre-existing directories: not ours to fill
        self.result = Result()

    def _ensure_dir(self, rel_dir: str) -> bool:
        """Create dst/rel_dir as real directories. False if any component is not ours to
        write into: a symlink (never write through one), or a real directory the venv made."""
        if rel_dir in ("", ".") or self.dst / rel_dir in self.created:
            return True
        cur = self.dst
        for part in Path(rel_dir).parts:
            cur = cur / part
            if cur.is_symlink():
                return False
            if cur.exists():
                if not cur.is_dir():
                    return False
                continue
            cur.mkdir()
            self.created.add(cur)
            self.result.dirs += 1
        return True

    def _writable_parent(self, rel: str) -> bool:
        """A file may go into a directory the farm created, or one that does not exist yet.
        A pre-existing directory belongs to whatever the venv already installed there."""
        if (self.dst / rel).parent in self.created:
            return True
        cur = self.dst
        for part in Path(rel).parent.parts:
            cur = cur / part
            if cur in self.created:
                continue
            if cur in self.foreign:
                return False
            if os.path.lexists(cur):
                self.foreign.add(cur)
                return False
            return True                       # nothing below here exists yet
        return True

    def link(self, src_site: Path, rel: str) -> bool:
        src = os.path.join(src_site, rel)
        dst = os.path.join(self.dst, rel)
        if os.path.lexists(dst) or not (os.path.isfile(src) or os.path.islink(src)):
            return False
        parent = os.path.dirname(rel)
        if not self._writable_parent(rel) or not self._ensure_dir(parent):
            return False
        os.symlink(src, dst)
        self.result.links += 1
        return True


def build(venv: Path, sources: List[Path], engine_python: str = "",
          venv_python: str = "") -> Result:
    """Mirror `sources` (in priority order) into `venv`'s site-packages.

    Projects the venv already owns (its seed packages) are left alone, and so is every file
    of theirs; the first source providing a project wins. Files no distribution lists
    (`__pycache__`, Debian's apt modules) are linked only into directories the farm made.
    """
    dst = venv_site(venv)
    farm = _Farm(dst)
    res = farm.result
    owned = owned_projects(dst)
    blocked: Set[str] = set()                 # paths belonging to projects we did not link
    claimed: Set[Tuple[int, str]] = set()     # (source index, path) any dist lists
    chosen: Dict[str, Tuple[int, Dist]] = {}

    per_source = [read_dists(s) for s in sources]
    for i, dists in enumerate(per_source):
        for name, entries in dists.items():
            for dist in entries:
                for f in dist.files:
                    claimed.add((i, os.path.normpath(f)))
            for k, dist in enumerate(entries):
                if name in owned:
                    res.skipped[name] = "owned by the venv"
                elif name in chosen:
                    res.skipped.setdefault(name, f"shadowed by {sources[chosen[name][0]]}")
                else:
                    chosen[name] = (i, dist)
                    continue
                # every copy we do not link is blocked, so none leaks in later
                blocked.update(os.path.normpath(f) for f in dist.files)

    for name, (i, dist) in sorted(chosen.items()):
        n = 0
        for f in dist.files:
            rel = os.path.normpath(f)
            if _inside(rel) and farm.link(sources[i], rel):
                n += 1
        res.projects[name] = {"version": dist.version, "source": str(sources[i]),
                              "meta": dist.meta, "links": n}

    # Residual: files no distribution lists. Top-level names that belong to a blocked
    # project are skipped wholesale, so a venv-owned package never gains the engine's pyc.
    blocked_tops = {p.split(os.sep, 1)[0] for p in blocked if _inside(p)}
    for i, src in enumerate(sources):
        prefix = len(str(src)) + 1
        for rel in _walk_rel(src, prefix):
            top = rel.split(os.sep, 1)[0]
            if (i, rel) in claimed or top in blocked_tops or rel in blocked:
                continue
            if farm.link(src, rel):
                res.residual += 1

    if venv_python:
        res.scripts = write_console_scripts(venv, venv_python,
                                            [d for _, d in chosen.values()])
    write_manifest(venv, engine_python, sources, res)
    return res


# ---------------------------------------------------------------------------------------
# Console scripts: the engine's launchers, re-pointed at the venv


_EP = re.compile(r"^\s*([^=\s]+)\s*=\s*([\w.]+)\s*:\s*([\w.]+)")


def _console_entries(entry_points: str) -> List[Tuple[str, str, str]]:
    out, section = [], ""
    for line in entry_points.splitlines():
        s = line.strip()
        if s.startswith("["):
            section = s.strip("[]").strip()
            continue
        if section in ("console_scripts", "gui_scripts"):
            m = _EP.match(s)
            if m:
                out.append(m.groups())
    return out


def launcher(venv_python: str, module: str, attr: str) -> str:
    head, _, rest = attr.partition(".")
    call = f"{head}.{rest}" if rest else head
    return (f"#!{venv_python}\n"
            "# -*- coding: utf-8 -*-\n"
            "# Written by venv-farm (ADR 0048): the engine's entry point, run by /venv/main.\n"
            "import sys\n"
            f"from {module} import {head}\n"
            "if __name__ == \"__main__\":\n"
            f"    sys.exit({call}())\n")


def write_console_scripts(venv: Path, venv_python: str, dists: List[Dist]) -> List[str]:
    """One launcher per console script of every farmed project, so the engine's commands
    run through /venv/main and see what is installed there. An existing file is never
    replaced. The path is the one each project's RECORD names (`../../../bin/<name>`), so
    uninstalling the project removes its launcher too."""
    bindir = venv / "bin"
    written = []
    for dist in dists:
        for name, module, attr in _console_entries(dist.entry_points):
            target = bindir / name
            if os.path.lexists(target) or "/" in name:
                continue
            target.write_text(launcher(venv_python, module, attr))
            target.chmod(0o755)
            written.append(name)
    return sorted(written)


def write_manifest(venv: Path, engine_python: str, sources: List[Path], res: Result) -> None:
    data = {
        "adr": "0048",
        "engine_python": engine_python,
        "sources": [str(s) for s in sources],
        "links": res.links,
        "residual_links": res.residual,
        "dirs": res.dirs,
        "projects": res.projects,
        "skipped": res.skipped,
        "scripts": res.scripts,
    }
    (venv / MANIFEST).write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------------------
# Verifying (read-only)


def _pyvenv(venv: Path) -> Dict[str, str]:
    cfg = {}
    for line in _read_text(venv / "pyvenv.cfg").splitlines():
        k, sep, v = line.partition("=")
        if sep:
            cfg[k.strip()] = v.strip()
    return cfg


def structural_problems(venv: Path) -> List[str]:
    """What must hold on disk, whatever the engine is. Read-only."""
    problems = []
    if _pyvenv(venv).get("include-system-site-packages", "").lower() != "false":
        problems.append("pyvenv.cfg: include-system-site-packages must be false "
                        "(uv cannot see inherited packages)")
    if not (venv / MANIFEST).exists():
        problems.append(f"{MANIFEST} is missing: /venv/main was not built by venv-farm")
    site = venv_site(venv)
    for dirpath, dirnames, filenames in os.walk(site):
        for d in dirnames:
            p = Path(dirpath) / d
            if p.is_symlink():
                problems.append(f"directory symlink {p} -> {os.readlink(p)} "
                                "(an install would write through it into the engine's tree)")
        for f in filenames:
            p = Path(dirpath) / f
            if p.is_symlink() and not p.exists():
                problems.append(f"dangling link {p} -> {os.readlink(p)}")
    seen: Dict[str, str] = {}
    for meta in sorted(site.iterdir()):
        if meta.name.endswith(METADATA_SUFFIXES):
            name = _read_meta_headers(meta)[0]
            if name in seen:
                problems.append(f"two installed copies of {name}: {seen[name]} and {meta.name}")
            seen[name] = meta.name
    return problems


def origin_problems(venv: Path) -> List[str]:
    """Shared libraries resolve $ORIGIN relative to the LINK, not its target. A library
    whose RUNPATH/RPATH leaves site-packages resolves from the engine's copy but not from
    the farm's, and fails only when loaded. Report every such entry."""
    site = venv_site(venv)
    problems = []
    for path in _walk_files(site):
        if not path.is_symlink() or ".so" not in path.name:
            continue
        target = Path(os.path.realpath(path))
        for entry in elf_origin_paths(target):
            from_src = Path(os.path.normpath(entry.replace("$ORIGIN", str(target.parent))))
            from_link = Path(os.path.normpath(entry.replace("$ORIGIN", str(path.parent))))
            if from_src.exists() and not from_link.exists():
                problems.append(f"{path}: RPATH {entry} resolves from the engine's copy "
                                f"({from_src}) but not from the farm ({from_link})")
    return problems


def elf_origin_paths(path: Path) -> List[str]:
    """$ORIGIN-relative entries of an ELF64 little-endian RUNPATH/RPATH. [] if not ELF."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(64)
            if head[:4] != b"\x7fELF" or head[4] != 2 or head[5] != 1:
                return []
            phoff, = struct.unpack_from("<Q", head, 32)
            phentsize, phnum = struct.unpack_from("<HH", head, 54)
            fh.seek(phoff)
            phdrs = fh.read(phentsize * phnum)
            loads, dyn = [], None
            for k in range(phnum):
                p_type, _, p_offset, p_vaddr, _, p_filesz, _, _ = struct.unpack_from(
                    "<IIQQQQQQ", phdrs, k * phentsize)
                if p_type == 1:
                    loads.append((p_vaddr, p_offset, p_filesz))
                elif p_type == 2:
                    dyn = (p_offset, p_filesz)
            if dyn is None:
                return []
            fh.seek(dyn[0])
            raw = fh.read(dyn[1])
            strtab, paths = None, []
            for k in range(0, len(raw) - 15, 16):
                tag, val = struct.unpack_from("<qQ", raw, k)
                if tag == 0:
                    break
                if tag == 5:
                    strtab = val
                elif tag in (15, 29):
                    paths.append(val)
            if strtab is None or not paths:
                return []
            off = next((o + strtab - v for v, o, sz in loads if v <= strtab < v + sz), None)
            if off is None:
                return []
            out = []
            for p in paths:
                fh.seek(off + p)
                s = fh.read(4096).split(b"\0", 1)[0].decode("utf-8", "replace")
                out.extend(e for e in s.split(":") if "$ORIGIN" in e or "${ORIGIN}" in e)
            return [e.replace("${ORIGIN}", "$ORIGIN") for e in out]
    except (OSError, struct.error):
        return []


def engine_problems(venv: Path, imports: List[str]) -> List[str]:
    """The engine imports through the venv from the engine's files, and uv sees the engine
    and its torch as installed."""
    problems = []
    py = str(venv / "bin" / "python")
    for mod in imports:
        code = ("import importlib, os, sys\n"
                f"m = importlib.import_module({mod!r})\n"
                "f = getattr(m, '__file__', None) or list(m.__path__)[0]\n"
                "print(os.path.realpath(f))")
        r = subprocess.run([py, "-c", code], capture_output=True, text=True)
        if r.returncode != 0:
            tail = (r.stderr.strip().splitlines() or ["(no output)"])[-1]
            problems.append(f"`import {mod}` through {py} fails: {tail}")
            continue
        real = r.stdout.strip().splitlines()[-1]
        if real.startswith(os.path.realpath(venv) + os.sep):
            problems.append(f"`import {mod}` resolves to a copy inside the venv ({real}), "
                            "not the engine's files")
    site = venv_site(venv)
    dists = read_dists(site)
    wanted = [n for n in ("torch",) if n in dists]
    wanted += [normalize(m.split(".")[0]) for m in imports if normalize(m.split(".")[0]) in dists]
    for name in dict.fromkeys(wanted):
        spec = f"{name}=={dists[name][0].version}"
        r = subprocess.run(["uv", "pip", "install", "--dry-run", "--python", py, spec],
                           capture_output=True, text=True)
        text = r.stdout + r.stderr
        # The question is whether uv sees THIS package as installed. An explicit request
        # also makes uv re-check the package's own pins, and an upstream can ship a stack
        # that breaks them (sglang: torch pins nvidia-nccl-cu13==2.29.7, the image carries
        # 2.30.7), so other planned changes are reported, not failed -- uv plans the same
        # against the engine's own environment.
        planned = [l.strip() for l in text.splitlines() if re.match(r"^\s*[-+] \S+==", l)]
        own = [l for l in planned if normalize(l[2:].split("==")[0]) == name]
        if r.returncode != 0 or own:
            problems.append(f"uv does not see {spec} as installed in the venv: "
                            f"{'; '.join(own) or (text.strip().splitlines() or [r.returncode])[-1]}")
        elif planned:
            print(f"venv-farm: note: `uv pip install {spec}` would also change "
                  f"{', '.join(planned)} (the engine's own pins; the same plan as against "
                  f"its own environment)")
    return problems
