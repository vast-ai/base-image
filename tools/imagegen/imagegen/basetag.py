"""Resolve a concrete, latest-dated `vastai/pytorch` base tag from DockerHub (ADR 0013).

The tag grammar is fixed:
    <torch>-cu<wheel>-cuda-<toolkit>-[mini-]py<py>-<YYYY-MM-DD>
Only **index** tags (no `-amd64`/`-arm64` suffix) are considered — that is what `FROM`
resolves. Resolution keys on `(torch, cuda-toolkit, py, variant, wheel)` and floats only the **date**,
picking the newest. The wheel is part of that key because one toolkit can carry two torch
cuda-wheel builds; omitting it where it matters raises rather than guesses (L096). The pure parse/select functions
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


def select_latest(tags, *, torch: str, cuda: str, py: str, mini: bool = True,
                  wheel: str | None = None) -> BaseTag:
    """Pure: newest-dated index tag in `tags` matching the tuple. Raises LookupError if none.

    `wheel` is the torch cuda-wheel build the tag carries ("130" in `-cu130-`). It is part of
    the identity of a base, NOT a detail of the toolkit: `configs/pytorch.json` publishes two
    wheels under one toolkit (cu130 and cu132 both at `cuda-13.2-mini`), so
    (torch, cuda, py, variant) alone can match two different published images. Leaving the
    choice to `max(..., key=date)` hands it to whatever order the registry listed them in,
    and `bump` re-resolving from the same tuple could then swap the torch CUDA build under a
    stack of kernels compiled against the old one.

    So: when `wheel` is given it is matched; when it is omitted and the remaining coordinates
    still admit more than one wheel, this RAISES rather than picking. Silence is the failure
    mode this argument exists to remove (gated by L096).
    """
    tags = list(tags)
    cands = [
        bt for t in tags
        if (bt := parse_tag(t)) and bt.torch == torch and bt.cuda == cuda
        and bt.py == py and bt.mini == mini
        and (wheel is None or bt.wheel == wheel)
    ]
    if not cands:
        raise LookupError(
            f"no {REPO} index tag matches torch={torch} cuda={cuda} py={py} "
            f"variant={'mini' if mini else 'full'}"
            f"{f' wheel=cu{wheel}' if wheel else ''} (checked {len(tags)} tags)"
        )
    seen = sorted({b.wheel for b in cands})
    if wheel is None and len(seen) > 1:
        raise LookupError(
            f"ambiguous base: torch={torch} cuda={cuda} py={py} "
            f"variant={'mini' if mini else 'full'} matches {len(seen)} torch cuda-wheels "
            f"({', '.join('cu' + w for w in seen)}). Pass wheel= to choose — resolving this "
            f"silently would let the pick fall to registry listing order (ADR 0013, L096)"
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
            wheel: str | None = None, repo: str = REPO, fetch=fetch_tags) -> str:
    """Newest-dated concrete `repo:<tag>` for the tuple. `fetch` is injectable for offline tests."""
    bt = select_latest(fetch(repo), torch=torch, cuda=cuda, py=py, mini=mini, wheel=wheel)
    return f"{repo}:{bt.raw}"
