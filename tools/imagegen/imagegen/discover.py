"""Find image directories and classify them by path (path == FROM chain)."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

from .suites import image_dirs


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
    """Every image in the tree: the base image itself (class "base", limited checks),
    then external, pytorch-nested and the direct children of derivatives/ (which
    include the pytorch hub). The walk is suites.image_dirs, shared with the launcher."""
    return [_mk(d, cls) for d, cls in image_dirs(repo)]
