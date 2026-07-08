"""`imagegen bump <name>` — re-resolve a pytorch-nested image's pinned base to the newest
dated tag for its OWN `(torch, cuda, py, variant)` tuple, updating both the Dockerfile `ARG`
default and every matching CI `base_image` matrix entry (an image may pin several — e.g. a
cuda-12.9 and a cuda-13.2 row; each re-resolves to its own newest date, torch/cuda unchanged).

Bump is standalone maintenance (ADR 0013), NOT part of the create skill. It adds no gate of
its own: a bumped pin is re-validated by the normal `build-<name>.yml` CI QA gate (ADR 0005)
when the change is built, and a published image cannot promote past a red gate.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import basetag
from .discover import discover, find_repo_root

# capture the tag up to the first whitespace/quote/comma so a quoted or trailing-punctuated
# ref doesn't swallow delimiters (an unparseable capture is simply left untouched).
_PYTORCH_REF = re.compile(r"vastai/pytorch:([^\s\"',]+)")


def bump(name: str, *, repo: Path | None = None, fetch=basetag.fetch_tags, log=print) -> int:
    repo = repo or find_repo_root(Path.cwd())
    img = next((i for i in discover(repo) if i.name == name), None)
    if img is None:
        log(f"error: no image named {name!r}")
        return 2
    if img.cls != "pytorch-nested":
        log(f"error: bump resolves the pytorch base; {name} is {img.cls} (ADR 0013 scope)")
        return 2

    tags = fetch(basetag.REPO)          # fetch once; re-resolve each pin against it

    def newest_for(old: str) -> str | None:
        bt = basetag.parse_tag(old)     # unparseable (e.g. CHANGEME) -> leave untouched
        if not bt:
            return None
        return basetag.select_latest(tags, torch=bt.torch, cuda=bt.cuda, py=bt.py, mini=bt.mini).raw

    changed = 0
    for path in (img.dir / "Dockerfile", repo / ".github/workflows" / f"build-{name}.yml"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")

        def _sub(m: re.Match) -> str:
            nonlocal changed
            old = m.group(1)
            new = newest_for(old)
            if new and new != old:
                changed += 1
                log(f"  {path.relative_to(repo)}: {old} -> {new}")
                return f"vastai/pytorch:{new}"
            return m.group(0)

        new_text = _PYTORCH_REF.sub(_sub, text)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")

    if changed == 0:
        log(f"{name}: already on the newest dated base for every pinned tuple — nothing to bump")
    else:
        log(f"{name}: bumped {changed} pin(s). Rebuild + push; the CI QA gate re-validates "
            "before promotion (ADR 0013).")
    return 0
