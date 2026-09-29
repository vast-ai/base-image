"""Make an external image's /venv/main see the engine's packages as installed (ADR 0048).

uv does not count packages a venv only INHERITS as installed, so every `uv pip install`
into an inheriting /venv/main re-resolved as if the engine were absent and laid a second
torch over it. Instead /venv/main is a plain venv whose site-packages mirrors the engine's,
file by file: every directory is real and every file is a symlink to the engine's copy.
uv and pip see the whole stack as installed, and whatever they install, upgrade or remove
lands as real files in the venv, which is what $WORKSPACE sync carries.

- No directory is ever a symlink: uv and pip install and uninstall per file path, so a
  directory link would send their writes into the engine's tree.
- The mirror is built once, into the image. After first boot /venv/main is the user's.
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from email.parser import HeaderParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

MANIFEST = ".vast-venv-mirror.json"
METADATA_SUFFIXES = (".dist-info", ".egg-info")

# Run by the ENGINE's interpreter: the site directories it imports from, in import order.
# sys.path, not site.getsitepackages(): for a venv engine that inherits system packages the
# latter lists only the venv's own directory.
_ENGINE_QUERY = r"""
import json, os, sys
dirs = []
for p in sys.path:
    r = os.path.realpath(p) if p else ""
    if os.path.basename(r) in ("site-packages", "dist-packages") and os.path.isdir(r) \
            and r not in dirs:
        dirs.append(r)
print(json.dumps({
    "base": os.path.realpath(getattr(sys, "_base_executable", sys.executable)),
    "version": "%d.%d" % sys.version_info[:2],
    "sources": dirs,
}))
"""


class MirrorError(Exception):
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
        raise MirrorError(f"{venv}: expected one lib/python3*/site-packages, found {len(found)}")
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
    has_record: bool


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_meta_headers(meta: Path) -> Tuple[str, str]:
    """(Name, Version) from a .dist-info or .egg-info -- which may be a directory or a file."""
    text = (_read_text(meta / "METADATA") or _read_text(meta / "PKG-INFO")) if meta.is_dir() \
        else _read_text(meta)
    headers = HeaderParser().parsestr(text)
    return normalize(headers.get("Name") or meta.name.split("-")[0]), headers.get("Version") or ""


def _walk_rel(root: Path, prefix: int) -> Iterable[str]:
    """Every non-directory under root, relative to the directory whose path length is
    `prefix`, following directory symlinks once (cycle-guarded)."""
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


def _listed_files(site: Path, meta: Path, name: str) -> Tuple[List[str], bool]:
    """Files a distribution installed, relative to `site`, and whether it keeps a RECORD.
    Debian ships dist-info and egg-info without a file list: top_level.txt names its trees,
    or, when there is none (blinker, pyparsing), the project's own name does. A wrong guess
    names a tree that does not exist, so it lists nothing rather than too much."""
    prefix = len(str(site)) + 1
    if meta.is_dir() and (meta / "RECORD").exists():
        with open(meta / "RECORD", newline="", encoding="utf-8", errors="replace") as fh:
            return [row[0] for row in csv.reader(fh) if row and row[0]], True
    files: List[str] = []
    tops = _read_text(meta / "top_level.txt").split() if meta.is_dir() else []
    for top in tops or [name.replace("-", "_")]:
        tree = site / top
        if tree.is_dir():
            files.extend(_walk_rel(tree, prefix))
        files.extend(p.name for p in site.glob(f"{top}.*") if p.is_file()
                     and not p.name.endswith(METADATA_SUFFIXES))
    return files, False


def read_dists(site: Path) -> Dict[str, List[Dist]]:
    """Every metadata entry in `site`, grouped by normalised project name, dist-info first.
    A site can hold two for one project: Debian ships `cryptography-41.0.7.dist-info` AND
    `cryptography.egg-info` for the same tree."""
    prefix = len(str(site)) + 1
    dists: Dict[str, List[Dist]] = {}
    # a dist-info before an egg-info describing the same project, whatever the case of its name
    for meta in sorted(site.iterdir(), key=lambda m: (not m.name.endswith(".dist-info"), m.name)):
        if not meta.name.endswith(METADATA_SUFFIXES):
            continue
        name, version = _read_meta_headers(meta)
        files, has_record = _listed_files(site, meta, name)
        own = list(_walk_rel(meta, prefix)) if meta.is_dir() else [meta.name]
        eps = _read_text(meta / "entry_points.txt") if meta.is_dir() else ""
        dists.setdefault(name, []).append(
            Dist(name, version, meta.name, sorted(set(files + own)), eps, has_record))
    return dists


# ---------------------------------------------------------------------------------------
# Building


@dataclass
class Result:
    projects: Dict[str, str] = field(default_factory=dict)     # name -> version mirrored
    skipped: Dict[str, str] = field(default_factory=dict)
    links: int = 0
    scripts: List[str] = field(default_factory=list)


class _Mirror:
    """A file NO mirrored project lists (__pycache__, Debian's metadata-less trees) goes only
    into a directory created for the same source: otherwise Debian's orphan pkg_resources/
    filled the one made for /usr/local's setuptools, and uninstalling setuptools left an
    importable empty package behind. A file a project lists may share a directory another
    source created -- which is right for a namespace package such as nvidia/, and makes a
    lower source's file importable in a regular package the engine would not see it in (no
    supported image has such a pair)."""

    def __init__(self, dst: Path):
        self.dst = str(dst)
        self.owner: Dict[str, int] = {self.dst: -1}   # directory -> source that created it

    def _dir_owner(self, d: str) -> Optional[int]:
        if d in self.owner:
            return self.owner[d]
        return -1 if os.path.lexists(d) else None     # a file stands here: never a directory

    def link(self, src_site: Path, i: int, rel: str, listed: bool) -> bool:
        dst = os.path.join(self.dst, rel)
        src = os.path.join(src_site, rel)
        if os.path.lexists(dst) or not (os.path.isfile(src) or os.path.islink(src)):
            return False
        parent, missing = os.path.dirname(dst), []
        while self._dir_owner(parent) is None:
            missing.append(parent)
            parent = os.path.dirname(parent)
        owner = self.owner.get(parent, -1)            # the nearest directory that exists
        if parent != self.dst and (owner == -1 or (not listed and owner != i)):
            return False
        for d in reversed(missing):
            os.mkdir(d)
            self.owner[d] = i
        os.symlink(src, dst)
        return True


def build(venv: Path, sources: List[Path], engine_python: str = "",
          venv_python: str = "") -> Result:
    """Mirror `sources` (highest priority first) into `venv`'s site-packages, once, on a
    fresh venv. The first source providing a project wins."""
    dst = venv_site(venv)
    held = [e.name for e in dst.iterdir() if e.name != "__pycache__"      # uv venv's own
            and (e.is_dir() or e.name.endswith(METADATA_SUFFIXES))]
    if held:
        raise MirrorError(f"{dst} is not fresh ({', '.join(sorted(held)[:5])}): the mirror is "
                          "built once, before anything is installed into the venv")
    mirror, res = _Mirror(dst), Result()
    chosen: Dict[str, Tuple[int, Dist]] = {}
    per_source = [read_dists(s) for s in sources]

    for i, (src, dists) in enumerate(zip(sources, per_source)):
        blocked: Set[str] = set()
        for name, entries in dists.items():
            if name in chosen:
                res.skipped.setdefault(name, f"shadowed by {sources[chosen[name][0]]}")
                for d in entries:                   # none of this copy may leak in
                    blocked.update(os.path.normpath(f) for f in d.files)
                continue
            chosen[name] = (i, entries[0])
            for d in entries[1:]:                   # the same tree, described twice:
                blocked.update(f for f in d.files   # block only the extra metadata
                               if f.split(os.sep, 1)[0] == d.meta)
        listed = set()
        for name, (k, dist) in sorted(chosen.items()):
            if k != i:
                continue
            for f in dist.files:
                rel = os.path.normpath(f)
                if rel not in blocked:          # a path outside site-packages is refused by link()
                    listed.add(rel)
                    res.links += mirror.link(src, i, rel, listed=True)
            res.projects[name] = dist.version
        for rel in _walk_rel(src, len(str(src)) + 1):
            if rel not in listed and rel not in blocked:
                res.links += mirror.link(src, i, rel, listed=False)

    claims: Dict[str, int] = {}
    for _, dist in chosen.values():
        for f in set(dist.files):
            claims[os.path.normpath(f)] = claims.get(os.path.normpath(f), 0) + 1
    for name, (i, dist) in chosen.items():
        if not dist.has_record and (dst / dist.meta).is_dir():
            _write_record(dst, dist, sources[i], claims)
    if venv_python:
        res.scripts = write_console_scripts(venv, venv_python, [d for _, d in chosen.values()])
    (venv / MANIFEST).write_text(json.dumps(
        {"adr": "0048", "engine_python": engine_python, "sources": [str(s) for s in sources],
         "projects": res.projects, "skipped": res.skipped}, indent=1, sort_keys=True) + "\n")
    return res


def _write_record(dst: Path, dist: Dist, src: Path, claims: Dict[str, int]) -> None:
    """Debian's metadata carries no RECORD, so pip refuses to uninstall it and uv leaves it
    beside a replacement. Write one only when it can be complete and this project's alone:
    the links made from its own source for files no other mirrored project lists
    (lazr.uri and lazr.restfulclient both name lazr/). Otherwise write none, and pip keeps
    refusing, loudly -- a partial RECORD made pip report a clean uninstall of blinker while
    `import blinker` still worked."""
    rows = []
    for f in dist.files:
        rel = os.path.normpath(f)
        p = dst / rel
        if rel.split(os.sep, 1)[0] == dist.meta:
            continue
        if claims.get(rel, 0) != 1:
            return
        if p.is_symlink() and os.readlink(p).startswith(str(src) + os.sep):
            rows.append(rel)
    if not rows:
        return
    for dirpath, _, filenames in os.walk(dst / dist.meta):
        rows.extend(os.path.relpath(os.path.join(dirpath, n), dst) for n in filenames)
    record = dst / dist.meta / "RECORD"
    rows.append(os.path.relpath(record, dst))
    with open(record, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([r, "", ""] for r in sorted(set(rows)))


# ---------------------------------------------------------------------------------------
# Console scripts: the engine's launchers, re-pointed at the venv


_EP = re.compile(r"^\s*([^=\s]+)\s*=\s*([\w.]+)\s*:\s*([\w.]+)")


def _console_entries(entry_points: str) -> List[Tuple[str, str, str]]:
    out, section = [], ""
    for line in entry_points.splitlines():
        s = line.strip()
        if s.startswith("["):
            section = s.strip("[]").strip()
        elif section in ("console_scripts", "gui_scripts") and (m := _EP.match(s)):
            out.append(m.groups())        # a bare `name = module` entry is not a launcher
    return out


def launcher(venv_python: str, module: str, attr: str) -> str:
    head, _, rest = attr.partition(".")
    call = f"{head}.{rest}" if rest else head
    return (f"#!{venv_python}\n"
            "# Written by venv-mirror (ADR 0048): the engine's entry point, run by /venv/main.\n"
            "import sys\n"
            f"from {module} import {head}\n"
            "if __name__ == \"__main__\":\n"
            f"    sys.exit({call}())\n")


def write_console_scripts(venv: Path, venv_python: str, dists: List[Dist]) -> List[str]:
    """One launcher per console script of every mirrored project, so the engine's commands
    run through /venv/main and see what is installed there (sglang's own launcher ran
    #!/opt/sglang/bin/python3). An existing file is never replaced. The path is the one each
    project's RECORD names, so uninstalling the project removes its launcher too."""
    written = []
    for dist in dists:
        for name, module, attr in _console_entries(dist.entry_points):
            target = venv / "bin" / name
            if os.path.lexists(target) or "/" in name:
                continue
            target.write_text(launcher(venv_python, module, attr))
            target.chmod(0o755)
            written.append(name)
    return sorted(written)


# ---------------------------------------------------------------------------------------
# Verifying (read-only)


def structural_problems(venv: Path) -> List[str]:
    """What must hold on disk, whatever the engine is."""
    problems = []
    cfg = dict(l.split("=", 1) for l in _read_text(venv / "pyvenv.cfg").splitlines() if "=" in l)
    if {k.strip(): v.strip() for k, v in cfg.items()}.get("include-system-site-packages", "") \
            .lower() != "false":
        problems.append("pyvenv.cfg: include-system-site-packages must be false "
                        "(uv cannot see inherited packages)")
    if not (venv / MANIFEST).exists():
        problems.append(f"{MANIFEST} is missing: /venv/main was not built by venv-mirror")
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


def shadow_problems(venv: Path) -> List[str]:
    """Build time only: a mirrored project an install replaced before the image was finished,
    the build-time shadow ADR 0048 exists to prevent. After first boot a user may replace
    anything, so the instance test does not run this."""
    mirrored = json.loads(_read_text(venv / MANIFEST) or "{}").get("projects", {})
    problems = []
    for meta in venv_site(venv).iterdir():
        if meta.name.endswith(METADATA_SUFFIXES):
            name, version = _read_meta_headers(meta)
            if name in mirrored and version != mirrored[name]:
                problems.append(f"{name} {version} was installed over the engine's "
                                f"{mirrored[name]} during the build: the engine now runs the copy")
    return problems


def engine_problems(venv: Path, imports: List[str]) -> List[str]:
    """Each declared module imports through the venv from the engine's files, and uv plans no
    reinstall of torch or of the engine's project."""
    problems = []
    py = str(venv / "bin" / "python")
    for mod in imports:
        code = ("import importlib, os\n"
                f"m = importlib.import_module({mod!r})\n"
                "f = getattr(m, '__file__', None) or list(m.__path__)[0]\n"
                "print(os.path.realpath(f))")
        r = subprocess.run([py, "-c", code], capture_output=True, text=True)
        if r.returncode != 0:
            tail = (r.stderr.strip().splitlines() or ["(no output)"])[-1]
            problems.append(f"`import {mod}` through {py} fails: {tail}")
        elif r.stdout.strip().splitlines()[-1].startswith(os.path.realpath(venv) + os.sep):
            problems.append(f"`import {mod}` resolves to a copy inside the venv, "
                            "not the engine's files")
    dists = read_dists(venv_site(venv))
    names = [n for n in ["torch"] + [normalize(m.split(".")[0]) for m in imports] if n in dists]
    for name in dict.fromkeys(names):
        spec = f"{name}=={dists[name][0].version}"
        r = subprocess.run(["uv", "pip", "install", "--dry-run", "--python", py, spec],
                           capture_output=True, text=True)
        text = r.stdout + r.stderr
        # An explicit request also makes uv re-check the package's own pins, and an upstream
        # can ship a stack that breaks them (sglang: torch pins nvidia-nccl-cu13==2.29.7, the
        # image carries 2.30.7). Only a reinstall of THIS package means uv cannot see it.
        planned = [l.strip() for l in text.splitlines() if re.match(r"^\s*[-+] \S+==", l)]
        own = [l for l in planned if normalize(l[2:].split("==")[0]) == name]
        if r.returncode != 0 or own:
            problems.append(f"uv does not see {spec} as installed in the venv: "
                            f"{'; '.join(own) or (text.strip().splitlines() or [r.returncode])[-1]}")
        elif planned:
            print(f"venv-mirror: note: `uv pip install {spec}` would also change "
                  f"{', '.join(planned)} (the engine's own pins)")
    return problems
