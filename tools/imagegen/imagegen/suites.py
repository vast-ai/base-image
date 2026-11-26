"""Which on-box test suites an image carries, resolved from the repository tree.

One rule, two callers: the linter (which checks that every test a gate requires
exists for the image the gate builds) and the launcher's bundle builder
(tools/template_manager/bundle.py, which sends exactly those suites to a test
instance). The launcher also runs from a copy of tools/template_manager beside a
checkout, without this package installed, so it loads this file by path: keep it to
the standard library and free of imports from the rest of the package.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

TESTS_SUBDIR = "opt/instance-tools/tests"
BASE_IMAGE = "base-image"
PYTORCH_DIR = "derivatives/pytorch"
PYTORCH_REF = "vastai/pytorch"


def image_dirs(repo: Path) -> Iterator[tuple[Path, str]]:
    """Every image directory in the tree with its class, in discovery order."""
    if (repo / "Dockerfile").is_file():
        yield repo, "base"
    for df in sorted((repo / "external").glob("*/Dockerfile")):
        yield df.parent, "external"
    for df in sorted((repo / PYTORCH_DIR / "derivatives").glob("*/Dockerfile")):
        yield df.parent, "pytorch-nested"
    for df in sorted((repo / "derivatives").glob("*/Dockerfile")):
        yield df.parent, "derivative"


def image_dir(repo: Path, name: str) -> Path | None:
    """The directory of the image called `name`, or None.

    The base image is found under BASE_IMAGE whatever the checkout is called: it is
    discovered under the checkout's directory name, which a clone is free to change.
    """
    if name == BASE_IMAGE and (repo / "Dockerfile").is_file():
        return repo
    for d, _ in image_dirs(repo):
        if d.name == name:
            return d
    return None


def tests_roots(image: Path) -> list[Path]:
    """The suite roots an image's own overlays ship, in build order: an overlay for a
    cached base stage (`ROOT_BASE`) is copied before `ROOT`, which lands on top."""
    roots = [p for p in image.glob(f"ROOT*/{TESTS_SUBDIR}") if p.is_dir()]
    return sorted(roots, key=lambda p: (p.parents[2].name == "ROOT", p.parents[2].name))


def layer_roots(repo: Path, image: Path | None) -> list[Path]:
    """Every suite root in the image's chain, in build order: base, then pytorch for
    an image built on it, then the image's own overlays. `image` None is base alone.

    A root is listed once, so base and pytorch resolve to their own chain.
    """
    images = [repo]
    if image is not None:
        df = image / "Dockerfile"
        if df.exists() and PYTORCH_REF in df.read_text(encoding="utf-8", errors="replace"):
            images.append(repo / PYTORCH_DIR)
        images.append(image)
    out: list[Path] = []
    for img in images:
        for root in tests_roots(img):
            if root not in out:
                out.append(root)
    return out


def suite_layers(repo: Path, image: Path | None) -> dict[str, list[Path]]:
    """{suite directory name: every directory that ships it, in build order}.

    A name can be shipped by several layers (an overlay that patches one base test
    still inherits the rest), so each name keeps every directory; a later one lands
    on top of an earlier one, file by file.
    """
    out: dict[str, list[Path]] = {}
    for root in layer_roots(repo, image):
        for suite in sorted(root.iterdir()):
            if suite.is_dir():
                out.setdefault(suite.name, []).append(suite)
    return out
