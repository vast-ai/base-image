"""Invariant checks. Scope = docs/invariants.md §1-2 (the verified gateable set).

Checks are instruction-aware (see dockerfile.py) so keywords in comments, across
continuations, or in the wrong position don't produce false passes. Each check is
a function (Image) -> Iterable[Finding]. ERROR gates; WARN is advisory.

Per-image exceptions (real, documented divergences) are SCOPED to a message
substring, not a whole check — so a *different* future break of the same code is
NOT silently suppressed (tested by test_no_stale_exceptions).
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .discover import Image
from .dockerfile import parse, stages, code_text, ini_sections, arg_defaults, resolve, parse_ref

_DOCKER_HUB = (None, "docker.io", "index.docker.io", "registry-1.docker.io")

ERROR = "ERROR"
WARN = "WARN"


@dataclass
class Finding:
    code: str
    severity: str
    image: str
    path: str
    msg: str


# Scoped, verified exceptions: (image, code) -> (reason, msg_substring_to_suppress).
EXCEPTIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("aio-studio", "L004"): ("builds on custom robatvastai/aio-studio:base-* (invariants §2)",
                             "must derive from vastai/pytorch"),
    ("aio-studio", "L020"): ("uses per-app venvs, not a single /venv/main guard (invariants §2)",
                             "torch-drift guard"),
}

CANONICAL_UTILS = ["logging", "cleanup_generic", "environment", "exit_serverless", "exit_portal"]
REQUIRED_LABEL_KEYS = ["org.opencontainers.image.source", "org.opencontainers.image.description", "maintainer"]


# ---- Dockerfile checks ------------------------------------------------------

def _label_keys(value: str) -> list[str]:
    # quote-aware: key="quoted value with = inside" | key=bareword
    return [k for k, _ in re.findall(r'([\w.]+)=("(?:[^"\\]|\\.)*"|\S+)', value)]


def check_labels(img: Image) -> Iterable[Finding]:
    """L001 — exactly 3 LABEL key=value pairs (any line layout) with the required keys."""
    labels = [i for i in parse(img.text) if i.cmd == "LABEL"]
    keys: list[str] = []
    for l in labels:
        keys += _label_keys(l.value)
    if len(keys) != 3:
        yield Finding("L001", ERROR, img.name, "Dockerfile", f"expected exactly 3 LABEL key=value pairs, found {len(keys)}")
    for key in REQUIRED_LABEL_KEYS:
        if key not in keys:
            yield Finding("L001", ERROR, img.name, "Dockerfile", f"missing required LABEL key: {key}")


def check_env_hash(img: Image) -> Iterable[Finding]:
    """L002 — env-hash > /.env_hash is the FINAL RUN (not commented, not stale)."""
    runs = [i for i in parse(img.text) if i.cmd == "RUN"]
    if not runs or "env-hash > /.env_hash" not in runs[-1].exec:
        yield Finding("L002", ERROR, img.name, "Dockerfile", "`env-hash > /.env_hash` must be the final RUN instruction")


def check_copy_root(img: Image) -> Iterable[Finding]:
    """L003 — a local `COPY ./ROOT /` (not the external --from copy)."""
    copies = [i for i in parse(img.text) if i.cmd == "COPY"]
    if not any(re.fullmatch(r"\./ROOT/?\s+/", c.value.strip()) for c in copies):
        yield Finding("L003", ERROR, img.name, "Dockerfile", "missing local `COPY ./ROOT /`")


def check_from_class(img: Image) -> Iterable[Finding]:
    """L004 — FROM matches declared class. Resolves the actual base ref via ARG
    defaults (not a global substring), so a decoy `vastai/...` elsewhere can't fool it."""
    instrs = parse(img.text)
    ct = code_text(instrs)
    defs = arg_defaults(instrs)
    sts = stages(instrs)

    def is_base(ref: str, repo: str) -> bool:
        reg, r, _ = parse_ref(resolve(ref, defs))
        return reg in _DOCKER_HUB and r == repo  # structural: registry + repo path, not substring

    if img.cls == "derivative":
        ok = any(is_base(ref, "vastai/base-image") for ref, _ in sts)
        if not ok:
            # base injected via build-arg (pytorch hub): ARG VAST_BASE w/ no default
            ok = (defs.get("VAST_BASE", "x") is None
                  and any(re.fullmatch(r"\$\{?VAST_BASE\}?", ref.strip()) for ref, _ in sts))
        if not ok:
            yield Finding("L004", ERROR, img.name, "Dockerfile", "derivative must derive from vastai/base-image")
    elif img.cls == "pytorch-nested":
        if not any(is_base(ref, "vastai/pytorch") for ref, _ in sts):
            yield Finding("L004", ERROR, img.name, "Dockerfile", "pytorch-nested must derive from vastai/pytorch")
    elif img.cls == "external":
        vast = [ref for ref, alias in sts if alias == "vast_base_image"]
        if not vast:
            yield Finding("L004", ERROR, img.name, "Dockerfile", "external must have a `FROM ... AS vast_base_image` stage")
        else:
            if sts[0][1] != "vast_base_image":
                yield Finding("L004", ERROR, img.name, "Dockerfile", "external stage order: vast_base_image must be the FIRST FROM")
            if not is_base(vast[0], "vastai/base-image"):
                yield Finding("L004", ERROR, img.name, "Dockerfile", "external vast_base_image stage must resolve to vastai/base-image")
        if "convert-non-vast-image.sh" not in ct:
            yield Finding("L004", ERROR, img.name, "Dockerfile", "external must graft via convert-non-vast-image.sh")


def check_torch_guard(img: Image) -> Iterable[Finding]:
    """L020 — torch-drift guard: the pre==post comparison must be wired to an exit
    on the SAME statement (a stray `exit 1` elsewhere, e.g. the REF guard, must not satisfy it)."""
    if img.cls != "pytorch-nested":
        return
    ct = code_text(parse(img.text))
    # a [[ ... ]] test mentioning BOTH pre and post (either order, = or !=) wired to an
    # exit via || or && on the same statement. A stray exit elsewhere does not satisfy it.
    wired = re.search(
        r"\[\[?.*?\$torch_versions_(?:pre|post).*?\$torch_versions_(?:pre|post).*?\]\]?"
        r"\s*(?:\|\||&&)\s*\{?[^}\n]*\bexit\b",
        ct,
    )
    if not wired:
        yield Finding("L020", ERROR, img.name, "Dockerfile",
                      "torch-drift guard not wired to exit on drift (pre==post comparison must `|| ... exit`)")


def check_no_auto_backend(img: Image) -> Iterable[Finding]:
    """L021 — no `--torch-backend auto` except inside a real sed substitution."""
    if img.cls not in ("pytorch-nested", "external"):
        return
    for line in code_text(parse(img.text)).splitlines():
        if re.search(r"--torch-backend[ =]auto", line) and not re.search(r"sed\b.*s[|/#@].*auto", line):
            yield Finding("L021", ERROR, img.name, "Dockerfile", "`--torch-backend auto` must be a concrete backend")


def check_uv_pip(img: Image) -> Iterable[Finding]:
    """L022 — prefer `uv pip` over bare pip (advisory)."""
    if img.cls != "pytorch-nested":
        return
    for line in code_text(parse(img.text)).splitlines():
        if re.search(r"(?<![\w/])pip\s+install\b", line) and not re.search(r"\buv\s+pip\s+install\b", line):
            yield Finding("L022", WARN, img.name, "Dockerfile", "bare `pip install` (prefer `uv pip install`)")


# ---- ROOT/ overlay checks (supervisor) --------------------------------------

def check_conf_triple(img: Image) -> Iterable[Finding]:
    """L010 — every [program:NAME] has PROC_NAME + command=/opt/.../NAME.sh; file stem is a program."""
    if not img.root:
        return
    confd = img.root / "etc" / "supervisor" / "conf.d"
    if not confd.is_dir():
        return
    for conf in sorted(confd.glob("*.conf")):
        rel = str(conf.relative_to(img.dir))
        secs = ini_sections(conf.read_text(encoding="utf-8", errors="replace"))
        programs = {name.split(":", 1)[1]: kv for name, kv in secs.items() if name.startswith("program:")}
        if conf.stem not in programs:
            yield Finding("L010", ERROR, img.name, rel, f"no [program:{conf.stem}] matching the file name (programs: {sorted(programs) or 'none'})")
        for pname, kv in programs.items():
            if "PROC_NAME" not in kv.get("environment", ""):
                yield Finding("L010", ERROR, img.name, rel, f"[program:{pname}] missing environment PROC_NAME")
            m = re.match(r"/opt/supervisor-scripts/(\S+)\.sh", kv.get("command", ""))
            if not m:
                yield Finding("L010", ERROR, img.name, rel, f"[program:{pname}] command must be /opt/supervisor-scripts/*.sh")
            elif m.group(1) != pname:
                yield Finding("L010", ERROR, img.name, rel, f"[program:{pname}] command targets {m.group(1)}.sh (basename != program name)")
            elif not (img.root / "opt" / "supervisor-scripts" / f"{pname}.sh").exists():
                yield Finding("L010", WARN, img.name, rel, f"{pname}.sh not in this image's ROOT (may inherit from base)")


def check_util_order(img: Image) -> Iterable[Finding]:
    """L011 — sourced utils appear as an ordered subsequence of CANONICAL_UTILS."""
    if not img.root:
        return
    sdir = img.root / "opt" / "supervisor-scripts"
    if not sdir.is_dir():
        return
    for script in sorted(sdir.glob("*.sh")):
        rel = str(script.relative_to(img.dir))
        seq: list[str] = []
        for line in script.read_text(encoding="utf-8", errors="replace").splitlines():
            if not re.match(r"\s*(\.|source)\s", line):  # only `source`/`.` lines
                continue
            for name in CANONICAL_UTILS:
                if re.search(rf"""(?:^|[/"'\s]){re.escape(name)}\.sh\b""", line):
                    seq.append(name)
        idxs = [CANONICAL_UTILS.index(n) for n in seq]
        if any(b < a for a, b in zip(idxs, idxs[1:])):
            yield Finding("L011", ERROR, img.name, rel, f"util source order violates canonical order: {seq}")


# ---- CI workflow (advisory) -------------------------------------------------

def check_workflow(img: Image, repo: Path) -> Iterable[Finding]:
    """L030 — a build-<name>.yml workflow exists (advisory; not all images have one)."""
    if img.cls == "base":
        return
    if not (repo / ".github" / "workflows" / f"build-{img.name}.yml").exists():
        yield Finding("L030", WARN, img.name, ".github/workflows", f"no build-{img.name}.yml (may build via a shared workflow)")


IMAGE_CHECKS: list[Callable[[Image], Iterable[Finding]]] = [
    check_labels, check_env_hash, check_copy_root, check_from_class,
    check_torch_guard, check_no_auto_backend, check_uv_pip,
    check_conf_triple, check_util_order,
]


def _suppressed(img_name: str, f: Finding) -> bool:
    ex = EXCEPTIONS.get((img_name, f.code))
    return bool(ex and ex[1] in f.msg)


def lint_image(img: Image, repo: Path, *, apply_exceptions: bool = True) -> list[Finding]:
    out: list[Finding] = []
    for chk in IMAGE_CHECKS:
        out.extend(chk(img))
    out.extend(check_workflow(img, repo))
    if apply_exceptions:
        out = [f for f in out if not _suppressed(img.name, f)]
    return out
