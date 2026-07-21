"""Resolve a concrete, latest-dated `vastai/pytorch` base tag from DockerHub (ADR 0013).

The tag grammar is fixed:
    <torch>-cu<wheel>-cuda-<toolkit>-[mini-]py<py>-<YYYY-MM-DD>
Only **index** tags (no `-amd64`/`-arm64` suffix) are considered — that is what `FROM`
resolves. Resolution keys on `(torch, cuda-toolkit, py, variant)` — the deliberate, safe
choice — and floats only the **date**, picking the newest. The pure parse/select functions
are offline and unit-tested; only `fetch_tags()` touches the network. Every failure path is
loud (raises) so a caller never silently pins an older/wrong base.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

REPO = "vastai/pytorch"

# Fixed grammar (ADR 0013 treats the scheme as stable). Anchored + index-only (no arch suffix).
_TAG_RE = re.compile(
    r"^(?P<torch>\d+\.\d+\.\d+)-cu(?P<wheel>\d+)-cuda-(?P<cuda>\d+\.\d+)-"
    r"(?P<mini>mini-)?py(?P<py>\d+)-(?P<date>\d{4}-\d{2}-\d{2})$"
)


@dataclass(frozen=True)
class BaseTag:
    raw: str          # the tag portion only, e.g. "2.10.0-cu128-cuda-12.9-mini-py312-2026-06-15"
    torch: str
    wheel: str        # the torch cuda-wheel build, e.g. "128" (travels with the toolkit)
    cuda: str         # the toolkit version, e.g. "12.9"
    mini: bool
    py: str           # e.g. "312"
    date: str         # ISO YYYY-MM-DD


def parse_tag(tag: str) -> BaseTag | None:
    """Parse a bare tag (no `repo:` prefix) against the scheme; None if it doesn't match."""
    m = _TAG_RE.match(tag.strip())
    if not m:
        return None
    return BaseTag(raw=tag.strip(), torch=m["torch"], wheel=m["wheel"], cuda=m["cuda"],
                   mini=bool(m["mini"]), py=m["py"], date=m["date"])


def select_latest(tags, *, torch: str, cuda: str, py: str, mini: bool = True) -> BaseTag:
    """Pure: newest-dated index tag in `tags` matching the tuple. Raises LookupError if none."""
    tags = list(tags)
    cands = [
        bt for t in tags
        if (bt := parse_tag(t)) and bt.torch == torch and bt.cuda == cuda
        and bt.py == py and bt.mini == mini
    ]
    if not cands:
        raise LookupError(
            f"no {REPO} index tag matches torch={torch} cuda={cuda} py={py} "
            f"variant={'mini' if mini else 'full'} (checked {len(tags)} tags)"
        )
    return max(cands, key=lambda b: b.date)   # ISO date -> lexical max == newest


def fetch_tags(repo: str = REPO, *, page_size: int = 100, max_pages: int = 30) -> list[str]:
    """All tag names for `repo` via the anonymous DockerHub v2 API (paginated). Raises on failure."""
    names: list[str] = []
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags/?page_size={page_size}"
    for _ in range(max_pages):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:   # noqa: S310 (fixed https host)
                data = json.load(r)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise RuntimeError(f"DockerHub tag fetch failed for {repo}: {e}") from e
        names.extend(t["name"] for t in data.get("results", []))
        url = data.get("next")
        if not url:
            break
    if not names:
        raise RuntimeError(f"DockerHub returned no tags for {repo}")
    return names


def resolve(*, torch: str, cuda: str, py: str = "312", mini: bool = True,
            repo: str = REPO, fetch=fetch_tags) -> str:
    """Newest-dated concrete `repo:<tag>` for the tuple. `fetch` is injectable for offline tests."""
    bt = select_latest(fetch(repo), torch=torch, cuda=cuda, py=py, mini=mini)
    return f"{repo}:{bt.raw}"
