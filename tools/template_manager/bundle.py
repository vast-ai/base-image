"""The bundle of on-box checks a delivered run sends to the instance (ADR 0068).

    driver.sh                the on-box driver (onbox/driver.sh)
    lib.sh                   the checks' helper library
    base/NN-name.sh          the base suite
    <suite>.d/...            one directory per suite the image inherits
    exposure-allowlist/...   data the checks read beside the suites
    RUN                      this run: run id, bundle digest, client commit, dirty flag, image
    MANIFEST                 "sha256  path" for every file above except RUN

The layout is the baked one, so no check changes. Which suites an image carries is
decided by the linter's own resolution (imagegen/suites.py in the checkout the bundle
is built from), loaded by path: the launcher also runs from a copy of this directory
beside a checkout, without the imagegen package installed.

The digest is the sha256 of MANIFEST, and MANIFEST leaves RUN out, so the digest is a
function of the checks alone: two runs from one commit carry the same digest. RUN is
verified on the box by its own hash.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DRIVER = HERE / "onbox" / "driver.sh"
SUITES_MODULE = "tools/imagegen/imagegen/suites.py"
DRIVER_NAME, LIBRARY, RUN_FILE, MANIFEST_FILE = "driver.sh", "lib.sh", "RUN", "MANIFEST"
BASE_SUITE = "base"
UNKNOWN = "unknown"


class BundleError(Exception):
    """The bundle cannot be built: a configuration error, found before renting."""


def default_repo():
    """The checkout this tool sits in (tools/template_manager/../..), or None when it
    is a copy outside one."""
    repo = HERE.parent.parent
    return repo if (repo / SUITES_MODULE).is_file() else None


def _load_suites(repo: Path):
    """imagegen/suites.py from `repo`, by path, under a name of its own."""
    path = repo / SUITES_MODULE
    if not path.is_file():
        raise BundleError(f"{repo} is not a base-image checkout: {SUITES_MODULE} is missing")
    name = "_template_manager_suites_" + hashlib.sha256(str(path).encode()).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git_state(repo: Path, paths):
    """(commit, dirty) for the bundle's sources: dirty is True, False, or "unknown"
    when git cannot say (no checkout, or a source outside it)."""
    try:
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        if sha.returncode != 0:
            return UNKNOWN, UNKNOWN
        status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--",
                                 *[str(p) for p in paths]],
                                capture_output=True, text=True, timeout=10)
        return sha.stdout.strip(), (bool(status.stdout.strip()) if status.returncode == 0
                                    else UNKNOWN)
    except Exception:                                             # noqa: BLE001
        return UNKNOWN, UNKNOWN


def image_stem(image: str) -> str:
    """`vastai/pytorch:tag` -> `pytorch`: the image's directory name in the tree."""
    return str(image or "").rsplit("/", 1)[-1].split(":")[0].split("@")[0]


@dataclass
class Bundle:
    files: dict            # {path in the bundle: (bytes, mode)}
    manifest: bytes
    digest: str
    commit: str
    dirty: object          # True, False or "unknown"
    image: str
    suites: list

    def run_descriptor(self, run_id: str) -> bytes:
        dirty = self.dirty if isinstance(self.dirty, str) else str(self.dirty).lower()
        return (f"run_id={run_id}\nbundle_digest={self.digest}\n"
                f"client_commit={self.commit}\nclient_dirty={dirty}\n"
                f"image={self.image}\nsuites={' '.join(self.suites)}\n").encode()

    def paths(self):
        """Every file the box must hold after extraction, and nothing else: what the
        manifest lists, the manifest and the run descriptor."""
        listed = [ln.split("  ", 1)[1] for ln in self.manifest.decode().splitlines()]
        return sorted([*listed, RUN_FILE, MANIFEST_FILE])

    def tar(self, run_id: str) -> bytes:
        """The archive for one run. Ordering, owner, modes and timestamps are fixed,
        so the same content gives the same bytes."""
        entries = {p: v for p, v in self.files.items()}
        entries[RUN_FILE] = (self.run_descriptor(run_id), 0o644)
        entries[MANIFEST_FILE] = (self.manifest, 0o644)
        dirs = sorted({str(d) for p in entries for d in Path(p).parents if str(d) != "."})
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as tar:
            for d in dirs:
                info = tarfile.TarInfo(d)
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                tar.addfile(info)
            for path in sorted(entries):
                data, mode = entries[path]
                info = tarfile.TarInfo(path)
                info.size, info.mode = len(data), mode
                tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()


def build(repo, image, *, driver=DRIVER, git_state=_git_state) -> Bundle:
    """The bundle for `image` (a template's image reference) from the checkout `repo`.
    Raises BundleError when the image's suites cannot be resolved or are empty."""
    if repo is None:
        raise BundleError("no base-image checkout to take the on-box checks from: "
                          "pass --repo")
    repo, driver = Path(repo).resolve(), Path(driver)
    suites = _load_suites(repo)
    stem = image_stem(image)
    image_dir = suites.image_dir_for_published(repo, stem)
    if image_dir is None:
        raise BundleError(f"no QA gate publishes {stem!r} and no image directory is named "
                          f"{stem!r} in {repo}: the suites for image {image!r} cannot be "
                          f"resolved")

    files: dict = {}

    def add(rel: str, src: Path):
        if src.is_symlink():
            raise BundleError(f"{src} is a symbolic link: a bundle carries regular files only")
        files[rel] = (src.read_bytes(), 0o755 if src.stat().st_mode & 0o111 else 0o644)

    if not driver.is_file():
        raise BundleError(f"the on-box driver is missing: {driver}")
    add(DRIVER_NAME, driver)
    roots = suites.layer_roots(repo, image_dir)
    for root in roots:
        if (root / LIBRARY).is_file():
            add(LIBRARY, root / LIBRARY)
    # A later layer lands on top of an earlier one file by file, as its COPY does.
    layers = suites.suite_layers(repo, image_dir)
    for name, dirs in sorted(layers.items()):
        for d in dirs:
            for src in sorted(d.rglob("*")):
                rel = src.relative_to(d)
                if "__pycache__" in rel.parts or src.is_dir():
                    continue
                add(f"{name}/{rel.as_posix()}", src)

    if LIBRARY not in files:
        raise BundleError(f"no {LIBRARY} in any suite root of {repo}")
    if not any(p.startswith(f"{BASE_SUITE}/") and p.endswith(".sh") and mode & 0o111
               for p, (_, mode) in files.items()):
        raise BundleError(f"the {BASE_SUITE}/ suite in {repo} has no executable check: "
                          f"nothing would be run")

    manifest = "".join(f"{hashlib.sha256(files[p][0]).hexdigest()}  {p}\n"
                       for p in sorted(files)).encode()
    commit, dirty = git_state(repo, [*roots, driver])
    return Bundle(files=files, manifest=manifest,
                  digest=hashlib.sha256(manifest).hexdigest(), commit=commit, dirty=dirty,
                  image=str(image),
                  suites=sorted(n for n in layers
                                if n == BASE_SUITE or n.endswith(".d")))
