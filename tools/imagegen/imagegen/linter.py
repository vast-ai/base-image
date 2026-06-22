"""Invariant checks. Scope = docs/invariants.md §1-2 (the verified gateable set).

Each check is a function (Image) -> Iterable[Finding]. ERROR findings gate;
WARN findings are advisory. Per-image exceptions (real, documented divergences)
are suppressed via EXCEPTIONS so the baseline stays clean and honest.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .discover import Image

ERROR = "ERROR"
WARN = "WARN"


@dataclass
class Finding:
    code: str
    severity: str
    image: str
    path: str
    msg: str


# Documented, verified exceptions (docs/invariants.md §2). (image_name, code) -> reason.
EXCEPTIONS: dict[tuple[str, str], str] = {
    ("aio-studio", "L004"): "builds on custom robatvastai/aio-studio:base-* (invariants §2)",
    ("aio-studio", "L020"): "uses per-app venvs, not a single /venv/main torch guard (invariants §2)",
}

CANONICAL_UTILS = ["logging", "cleanup_generic", "environment", "exit_serverless", "exit_portal"]


# ---- Dockerfile checks (all classes) ----------------------------------------

def check_labels(img: Image) -> Iterable[Finding]:
    """L001 — exactly 3 LABEL instructions."""
    n = len(re.findall(r"(?im)^\s*LABEL\b", img.text))
    if n != 3:
        yield Finding("L001", ERROR, img.name, "Dockerfile", f"expected exactly 3 LABEL lines, found {n}")


def check_env_hash(img: Image) -> Iterable[Finding]:
    """L002 — env-hash > /.env_hash trailer present."""
    if "env-hash > /.env_hash" not in img.text:
        yield Finding("L002", ERROR, img.name, "Dockerfile", "missing `env-hash > /.env_hash` build step")


def check_copy_root(img: Image) -> Iterable[Finding]:
    """L003 — COPY ./ROOT / present (trailing slash on ROOT tolerated)."""
    if not re.search(r"(?im)^\s*COPY\s+\./ROOT/?\s+/\s*$", img.text):
        yield Finding("L003", ERROR, img.name, "Dockerfile", "missing `COPY ./ROOT /`")


def check_from_class(img: Image) -> Iterable[Finding]:
    """L004 — FROM matches declared class."""
    t = img.text
    if img.cls == "derivative":
        # Base is usually pinned inline (vastai/base-image:...). The pytorch hub
        # instead injects it via build-arg (ARG VAST_BASE, no default) in CI —
        # the actual value is build-arg-verified, a linter blind spot (invariants §4).
        inline = "vastai/base-image" in t
        via_arg = bool(re.search(r"(?im)^\s*ARG\s+VAST_BASE\b", t) and re.search(r"\$\{?VAST_BASE\}?", t))
        if not (inline or via_arg):
            yield Finding("L004", ERROR, img.name, "Dockerfile", "derivative must derive from vastai/base-image")
    elif img.cls == "pytorch-nested":
        if "vastai/pytorch" not in t:
            yield Finding("L004", ERROR, img.name, "Dockerfile", "pytorch-nested must derive from vastai/pytorch")
    elif img.cls == "external":
        if "vast_base_image" not in t:
            yield Finding("L004", ERROR, img.name, "Dockerfile", "external must have a `FROM ... AS vast_base_image` stage")
        if "convert-non-vast-image.sh" not in t:
            yield Finding("L004", ERROR, img.name, "Dockerfile", "external must graft via convert-non-vast-image.sh")


# ---- pytorch-nested checks --------------------------------------------------

def check_torch_guard(img: Image) -> Iterable[Finding]:
    """L020 — torch ecosystem drift guard present."""
    if img.cls != "pytorch-nested":
        return
    if not ("torch_versions_pre" in img.text and "torch_versions_post" in img.text):
        yield Finding("L020", ERROR, img.name, "Dockerfile", "missing torch-drift guard (torch_versions_pre/post)")


def check_no_auto_backend(img: Image) -> Iterable[Finding]:
    """L021 — no surviving `--torch-backend auto` as an install argument."""
    if img.cls not in ("pytorch-nested", "external"):
        return
    for i, line in enumerate(img.text.splitlines(), 1):
        if re.search(r"--torch-backend[ =]auto", line) and "sed" not in line:
            yield Finding("L021", ERROR, img.name, "Dockerfile", f"line {i}: `--torch-backend auto` must be a concrete backend")


def check_uv_pip(img: Image) -> Iterable[Finding]:
    """L022 — prefer `uv pip`, not bare pip (advisory)."""
    if img.cls != "pytorch-nested":
        return
    for i, line in enumerate(img.text.splitlines(), 1):
        if re.search(r"(?<![\w/])pip\s+install\b", line) and not re.search(r"\buv\s+pip\s+install\b", line):
            yield Finding("L022", WARN, img.name, "Dockerfile", f"line {i}: bare `pip install` (prefer `uv pip install`)")


# ---- ROOT/ overlay checks (supervisor) --------------------------------------

def check_conf_triple(img: Image) -> Iterable[Finding]:
    """L010 — conf.d ↔ script ↔ program-name triple (invariants §1, strongest)."""
    if not img.root:
        return
    confd = img.root / "etc" / "supervisor" / "conf.d"
    if not confd.is_dir():
        return
    for conf in sorted(confd.glob("*.conf")):
        stem = conf.stem
        text = conf.read_text(encoding="utf-8", errors="replace")
        rel = str(conf.relative_to(img.dir))
        names = re.findall(r"(?im)^\s*\[program:([^\]]+)\]", text)
        if stem not in names:
            yield Finding("L010", ERROR, img.name, rel, f"[program:{stem}] not found (sections: {names or 'none'})")
        if not re.search(r'(?im)^\s*environment\s*=.*PROC_NAME', text):
            yield Finding("L010", ERROR, img.name, rel, 'missing `environment=PROC_NAME="%(program_name)s"`')
        m = re.search(r"(?im)^\s*command\s*=\s*(\S+)", text)
        if not m or not re.match(r"/opt/supervisor-scripts/\S+\.sh", m.group(1)):
            yield Finding("L010", ERROR, img.name, rel, "command must point to /opt/supervisor-scripts/<name>.sh")
        else:
            script = img.root / "opt" / "supervisor-scripts" / Path(m.group(1)).name
            if not script.exists():
                yield Finding("L010", WARN, img.name, rel, f"command target {Path(m.group(1)).name} not in this image's ROOT (may inherit from base)")


def check_util_order(img: Image) -> Iterable[Finding]:
    """L011 — sourced utils appear as an ordered subsequence of CANONICAL_UTILS."""
    if not img.root:
        return
    scripts_dir = img.root / "opt" / "supervisor-scripts"
    if not scripts_dir.is_dir():
        return
    for script in sorted(scripts_dir.glob("*.sh")):
        text = script.read_text(encoding="utf-8", errors="replace")
        rel = str(script.relative_to(img.dir))
        seq: list[tuple[int, str]] = []  # (canonical_index, name) in file order
        for line in text.splitlines():
            if "utils" not in line:
                continue
            # matches both `${utils}/name.sh` and `/opt/supervisor-scripts/utils/name.sh`
            for idx, name in enumerate(CANONICAL_UTILS):
                if re.search(rf"utils[^/]*/{re.escape(name)}\.sh", line):
                    seq.append((idx, name))
        last = -1
        for idx, name in seq:
            if idx < last:
                ordered = [n for _, n in seq]
                yield Finding("L011", ERROR, img.name, rel,
                              f"util source order violates canonical order at {name}: {ordered}")
                break
            last = idx


# ---- CI workflow checks (advisory in v1) ------------------------------------

def check_workflow(img: Image, repo: Path) -> Iterable[Finding]:
    """L030 — a build-<name>.yml workflow exists (advisory)."""
    wf = repo / ".github" / "workflows" / f"build-{img.name}.yml"
    if not wf.exists():
        yield Finding("L030", WARN, img.name, ".github/workflows", f"no build-{img.name}.yml (CI job-shape check deferred)")


# -----------------------------------------------------------------------------

# checks that only need the Image
IMAGE_CHECKS: list[Callable[[Image], Iterable[Finding]]] = [
    check_labels, check_env_hash, check_copy_root, check_from_class,
    check_torch_guard, check_no_auto_backend, check_uv_pip,
    check_conf_triple, check_util_order,
]


def lint_image(img: Image, repo: Path) -> list[Finding]:
    out: list[Finding] = []
    for chk in IMAGE_CHECKS:
        out.extend(chk(img))
    out.extend(check_workflow(img, repo))
    # drop documented exceptions
    return [f for f in out if (img.name, f.code) not in EXCEPTIONS]
