"""Find image directories and classify them by path (path == FROM chain)."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Image:
    name: str
    cls: str  # "derivative" | "pytorch-nested" | "external"
    dir: Path
    dockerfile: Path
    text: str
    root: Path | None  # the image's ROOT/ overlay, if present


def find_repo_root(start: Path) -> Path:
    """Walk up until we find the monorepo markers."""
    p = start.resolve()
    for cand in [p, *p.parents]:
        if (cand / "derivatives").is_dir() and (cand / "external").is_dir():
            return cand
    return start.resolve()


def _mk(d: Path, cls: str) -> Image:
    df = d / "Dockerfile"
    root = d / "ROOT"
    return Image(
        name=d.name,
        cls=cls,
        dir=d,
        dockerfile=df,
        text=df.read_text(encoding="utf-8", errors="replace"),
        root=root if root.is_dir() else None,
    )


def discover(repo: Path) -> list[Image]:
    images: list[Image] = []

    # the base image itself (root of the FROM chain) — class "base", limited checks
    if (repo / "Dockerfile").is_file():
        images.append(_mk(repo, "base"))

    nested_parent = repo / "derivatives" / "pytorch" / "derivatives"

    for df in sorted((repo / "external").glob("*/Dockerfile")):
        images.append(_mk(df.parent, "external"))

    for df in sorted(nested_parent.glob("*/Dockerfile")):
        images.append(_mk(df.parent, "pytorch-nested"))

    # direct children of derivatives/ (non-recursive) — includes the pytorch hub
    for df in sorted((repo / "derivatives").glob("*/Dockerfile")):
        images.append(_mk(df.parent, "derivative"))

    return images
