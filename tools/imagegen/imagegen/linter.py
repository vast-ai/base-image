"""Invariant checks. Scope = docs/invariants.md §1-2 (the verified gateable set).

Checks are instruction-aware (see dockerfile.py) so keywords in comments, across
continuations, or in the wrong position don't produce false passes. Each check is
a function (Image) -> Iterable[Finding]. ERROR gates; WARN is advisory.

Per-image exceptions (real, documented divergences) are SCOPED to a message
substring, not a whole check — so a *different* future break of the same code is
NOT silently suppressed (tested by test_no_stale_exceptions).
"""
from __future__ import annotations
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .discover import Image
from .dockerfile import parse, stages, code_text, ini_sections, arg_defaults, resolve, parse_ref
from .portal import (
    extract_baked_default, parse_portal_config,
    proxied_externals, forbidden_expose_ports,
)
from .portal_smoke import exposed_ports

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
    # Provisional (2026-07-06): comfyui bakes one small default SD-1.5 checkpoint for the
    # out-of-box / QA first-run. Deviation from invariants §6 (no baked weights), tracked for
    # migration to runtime provisioning — not an endorsement of baking. See ADR 0011 discussion.
    ("comfyui", "L053"): ("bakes one small default SD-1.5 checkpoint for out-of-box/QA — "
                          "provisioning migration tracked (2026-07-06)",
                          "baked model weights"),
}

CANONICAL_UTILS = ["logging", "cleanup_generic", "environment", "exit_serverless", "exit_portal"]
REQUIRED_LABEL_KEYS = ["org.opencontainers.image.source", "org.opencontainers.image.description", "maintainer"]

# Authoritative rule catalog — the single source of truth. docs/lint-rules.md is
# GENERATED from this (see rules_markdown / `imagegen rules`), and a test fails if
# they drift, so the docs can never silently disagree with the enforced checks.
RULES: list[tuple[str, str, str]] = [
    ("L001", ERROR, "Exactly 3 LABEL key=value pairs, including the required keys"),
    ("L002", ERROR, "`env-hash > /.env_hash` is the final RUN (executed shell, not heredoc data)"),
    ("L003", ERROR, "A local `COPY ./ROOT /` is present"),
    ("L004", ERROR, "FROM matches the declared class — structural base identity (registry+repo), incl. external stage order"),
    ("L005", ERROR, "App base FROM is a CONCRETE pin — a dated tag or a digest, not `latest` and not untagged — so a rebuild can't jump to an untested base (pytorch-nested & derivative; ADR 0013). base-image/pytorch may float"),
    ("L010", ERROR, "Each [program:NAME]: PROC_NAME + command=/opt/supervisor-scripts/NAME.sh; file stem is a program"),
    ("L011", ERROR, "Sourced utils appear as an ordered subsequence of the canonical order"),
    ("L020", ERROR, "torch-drift guard: a pre==post comparison wired to an exit on the same statement"),
    ("L021", ERROR, "No `--torch-backend auto` except inside a real sed substitution"),
    ("L022", WARN, "Prefer `uv pip install` over bare `pip install`"),
    ("L030", WARN, "A build-<name>.yml workflow exists (not universal)"),
    ("L040", ERROR, "No unfilled generator skeleton markers (CHANGEME / CHANGEPORT / >>> FILL)"),
    ("L041", ERROR, "No hardcoded staging namespace in a new image's committed files — reference the DOCKERHUB_NAMESPACE_STAGING secret"),
    ("L050", ERROR, "A shipped template.yml declares a compute_cap floor in extra_filters (ADR 0005)"),
    ("L051", ERROR, "Supervisor launch scripts (ROOT/opt/supervisor-scripts/*.sh) are executable — the .conf execs them directly"),
    ("L052", ERROR, "A shipped templates/*/README.md launch link uses the <<LAUNCH_LINK>> placeholder, not a hardcoded cloud.vast.ai ref link (ADR 0011)"),
    ("L053", ERROR, "No baked model weights in a Dockerfile RUN — models arrive at runtime via provisioning / <APP>_MODEL (invariants §6)"),
    ("L054", ERROR, "A template's VRAM floor, IF set, uses a valid key (gpu_ram / gpu_total_ram, MB) with a numeric value — presence is optional (multi-model hosts omit it; qa supplies it)"),
    ("L055", ERROR, "External images set ENV TCLLIBPATH=/usr/lib/tcltk/default (they FROM upstream, not our base, so don't inherit it) — else the pty helper's unbuffer/Expect fails and the app launch dies at boot"),
    ("L056", ERROR, "Effective EXPOSE (own + inherited via FROM) covers exactly the baked PORTAL_CONFIG Caddy-front ports — fires only once an image declares its own EXPOSE (ADR 0002)"),
    ("L057", ERROR, "EXPOSE never includes a port no PORTAL_CONFIG entry proxies (loopback/internal or equal-port-only) — never expose a port without a Caddy auth front (ADR 0002)"),
    ("L058", WARN, "Image bakes a default PORTAL_CONFIG (else it relies on the launch template) — advisory during the ADR 0002 migration"),
    ("L060", ERROR, "No credential-shaped secret committed in docs/adr/** — this repo is public; sensitive specifics live in the internal tracker, not the ADR (ADR 0012)"),
    ("L061", ERROR, "No internal tracker ticket id (CON-/HOST-/CLN-…) in any public-repo file — it leaks the internal tracker and dangles for external readers; the internal issue links to the ADR/commit, not the reverse (ADR 0012)"),
]


def rules_markdown() -> str:
    """Render docs/lint-rules.md from RULES. Regenerate with `imagegen rules`."""
    lines = [
        "# Lint rules (generated)",
        "",
        "> Generated from `tools/imagegen/imagegen/linter.py` (`RULES`). Do not edit by",
        "> hand — run `imagegen rules > docs/lint-rules.md`. This is the authoritative",
        "> rule list; `CONTRIBUTING.md` / `.github/AGENTS.md` must not contradict it.",
        "",
        "| Code | Severity | Rule |",
        "|---|---|---|",
    ]
    lines += [f"| {code} | {sev} | {summary} |" for code, sev, summary in RULES]
    return "\n".join(lines) + "\n"


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


_FLOATING_BASE_TAGS = {None, "latest"}   # a rebuild onto these is not reproducible


def check_base_pin(img: Image) -> Iterable[Finding]:
    """L005 — a pytorch-nested/derivative image must pin a CONCRETE base FROM: a dated tag or a
    digest, never `latest` and never untagged. A floating base lets a rebuild silently land on a
    base this image was never tested against (ADR 0013). `base-image`/`pytorch` may float; app
    images may not. The scaffold's `CHANGEME` is L040's job, not this one. A base injected via a
    defaultless build-arg (the CI multi-cuda pattern) has no in-Dockerfile tag to check, so it is
    skipped — its concrete tag lives in the CI matrix. `@vastai-automatic-tag` is a template-tag
    token, not a valid Dockerfile `FROM`, so it is not this check's surface."""
    if img.cls not in ("pytorch-nested", "derivative"):
        return
    want = "vastai/pytorch" if img.cls == "pytorch-nested" else "vastai/base-image"
    instrs = parse(img.text)
    defs = arg_defaults(instrs)
    for ref, _alias in stages(instrs):
        resolved = resolve(ref, defs)
        reg, repo, tag = parse_ref(resolved)
        if reg in _DOCKER_HUB and repo == want:
            if "@" in resolved:      # a digest pin (repo@sha256:…) is the strongest concrete pin
                return
            if tag in _FLOATING_BASE_TAGS:
                yield Finding("L005", ERROR, img.name, "Dockerfile",
                              f"base FROM must be a concrete pin, not {tag or 'untagged'} — pin a "
                              "dated tag (`imagegen resolve-base` / `imagegen bump`); a floating "
                              "base rebuilds onto an untested image (ADR 0013)")
            return   # the base stage is checked; done


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


# A model-weight file fetched into the image, or an explicit model download, inside a RUN.
# .bin is intentionally excluded (too many non-model .bin files → false positives).
_WEIGHT_FILE = re.compile(r"\.(?:safetensors|gguf|ckpt|pth|onnx)\b")
_MODEL_DOWNLOAD = re.compile(
    r"\bhf\s+download\b|\bhuggingface-cli\s+download\b|\bhf_hub_download\s*\(|\bsnapshot_download\s*\(")


def check_no_baked_weights(img: Image) -> Iterable[Finding]:
    """L053 — model weights must NOT be baked into the image (invariants §6). They arrive at
    runtime via provisioning (a `provisioning_scripts/<name>.sh` / `PROVISIONING_SCRIPT`) or the
    app's own on-start download driven by an `<APP>_MODEL` env — because the *tenant* triggers
    the download, the weight licence stays theirs and the image stays small and rebuildable.

    Instruction-aware (operates on `code_text`, so COMMENTED example downloads don't fire).
    Detects, inside a real RUN: `hf download` / `huggingface-cli download` / `hf_hub_download(` /
    `snapshot_download(`, or a `wget`/`curl` of a model-weight file. Small non-model assets
    (tokenizer/config, a UI's bundled icons) are out of scope — only the weight extensions match."""
    if img.cls == "base":
        return
    for line in code_text(parse(img.text)).splitlines():
        reason = None
        if _MODEL_DOWNLOAD.search(line):
            reason = "a model download (hf/huggingface-cli/snapshot_download)"
        elif re.search(r"\b(?:wget|curl)\b", line) and _WEIGHT_FILE.search(line):
            reason = "a wget/curl of a model-weight file"
        if reason:
            yield Finding("L053", ERROR, img.name, "Dockerfile",
                          f"baked model weights — {reason}; models must arrive at runtime via "
                          f"provisioning / <APP>_MODEL, not the image layer (invariants §6)")
            return  # one finding per image is enough


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


_SKELETON_MARKERS = ("CHANGEME", "CHANGEPORT", ">>> FILL")


def _image_files(img: Image, repo: Path) -> list:
    """The image's own committed files that skeleton/leak checks scan: Dockerfile, both
    READMEs, everything under ROOT/, the QA template(s) under templates/ (which sit OUTSIDE
    ROOT/, so they must be added explicitly — else a scaffolded QA template's markers slip
    past L040), and the build workflow."""
    files = [img.dockerfile, img.dir / "README.md", img.dir / "README.template.md"]
    if img.root:
        files += [p for p in img.root.rglob("*") if p.is_file()]
    tdir = img.dir / "templates"
    if tdir.is_dir():
        files += [p for p in tdir.rglob("*") if p.is_file()]
    wf = repo / ".github" / "workflows" / f"build-{img.name}.yml"
    if wf.exists():
        files.append(wf)
    return files


def check_skeleton(img: Image, repo: Path) -> Iterable[Finding]:
    """L040 — unfilled generator markers must not pass as 'clean'. So a scaffold can
    never be mistaken for a complete, buildable image. Scoped to the image's own files."""
    if img.cls == "base":
        return
    files = _image_files(img, repo)
    for p in files:
        if not p.is_file():
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if any(mk in t for mk in _SKELETON_MARKERS):
            try:
                rel = str(p.relative_to(img.dir))
            except ValueError:
                rel = str(p.name)
            yield Finding("L040", ERROR, img.name, rel,
                          "unfilled skeleton marker (CHANGEME / >>> FILL) — complete before build")


# Images whose base image legitimately lives on the staging account, so a staging
# namespace in their Dockerfile is expected, not a leak (invariants §2). aio-studio
# builds FROM a custom staging base and already carries L004/L020 exceptions for the
# same reason. Grandfathered here rather than via EXCEPTIONS because L041 only emits its
# ERROR when the namespace env is set, which would make the msg-scoped exception read as
# "stale" on an env-unset run (test_no_stale_exceptions).
_L041_GRANDFATHERED = frozenset({"aio-studio"})


def check_no_hardcoded_staging_namespace(img: Image, repo: Path) -> Iterable[Finding]:
    """L041 — a new image's committed files must not hardcode the staging Docker Hub
    namespace; reference the ``DOCKERHUB_NAMESPACE_STAGING`` secret so the account stays
    single-sourced. This is NOT about secrecy — a namespace is a public identifier, and
    the prod namespace is the product users pull — it's about not adding new coupling that
    a future rename would have to chase, and keeping scaffolds honest.

    The namespace to match is supplied via the ``DOCKERHUB_NAMESPACE_STAGING`` env var at
    lint time, so the literal never lives in this source. Unset -> a single WARN (never a
    silent skip, so a run with the check disabled is visible); CI sets it from the secret,
    so the gate is real there. Scoped to the image's own files (same set as L040), so
    legacy repo-root scripts that predate this rule don't fail CI. The prod namespace is
    deliberately NOT matched (it's the public product)."""
    if img.cls == "base":
        return
    if img.name in _L041_GRANDFATHERED:
        return
    ns = os.environ.get("DOCKERHUB_NAMESPACE_STAGING", "").strip()
    if not ns:
        yield Finding("L041", WARN, img.name, "-",
                      "L041 not enforced: DOCKERHUB_NAMESPACE_STAGING unset (CI sets it from the secret)")
        return
    pat = re.compile(r"(?<![\w./-])" + re.escape(ns) + r"/")
    files = _image_files(img, repo)
    for p in files:
        if not p.is_file():
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if pat.search(t):
            try:
                rel = str(p.relative_to(img.dir))
            except ValueError:
                rel = str(p.name)
            yield Finding("L041", ERROR, img.name, rel,
                          "hardcoded staging namespace — reference ${{ secrets.DOCKERHUB_NAMESPACE_STAGING }} "
                          "/ $DOCKERHUB_NAMESPACE_STAGING instead")


def _is_number(v) -> bool:
    """True if v is a usable numeric floor (not a bool, not None, not unparseable)."""
    if isinstance(v, bool) or v is None:
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        try:
            float(v)
            return True
        except ValueError:
            return False
    return False


def _has_compute_cap_floor(entry) -> bool:
    """True if a parsed template entry declares a *usable* compute_cap floor.

    The value must be parseable as a number — a key-only ``{compute_cap: {gte: null}}``
    or a non-numeric value passes neither this check nor the tester's
    ``_required_floor``, so it must NOT satisfy L050 (else it lints clean but
    drives selection into the floor-less fallback). Accepts ``{gte|gt|eq: N}`` or a
    bare scalar ``{compute_cap: N}`` (mirrors test_template._required_floor).
    """
    if not isinstance(entry, dict):
        return False
    ef = entry.get("extra_filters")
    if not isinstance(ef, dict):
        return False
    spec = ef.get("compute_cap")
    if isinstance(spec, dict):
        return any(op in spec and _is_number(spec[op]) for op in ("gte", "gt", "eq"))
    return _is_number(spec)


def check_template_floor(img: Image, repo: Path) -> Iterable[Finding]:
    """L050 — a shipped template.yml must declare a compute_cap floor (ADR 0005).

    The live-GPU QA gate selects the smallest viable box at or above the template's
    compute_cap floor; without one there is nothing to select against (selection
    would fall back to a random GPU generation). Only fires for images that ship a
    ``templates/`` dir — not every image has one.
    """
    if img.cls == "base":
        return
    tdir = img.dir / "templates"
    if not tdir.is_dir():
        return
    import yaml  # lazy: only template-bearing images need the YAML dep
    for tpl in sorted(tdir.rglob("template.yml")):
        try:
            rel = str(tpl.relative_to(img.dir))
        except ValueError:
            rel = tpl.name
        try:
            data = yaml.safe_load(tpl.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            yield Finding("L050", ERROR, img.name, rel, f"template.yml is not valid YAML: {e}")
            continue
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            if not _has_compute_cap_floor(entry):
                yield Finding("L050", ERROR, img.name, rel,
                              "must declare a compute_cap floor in extra_filters "
                              "(e.g. extra_filters: {compute_cap: {gte: 700}}) — ADR 0005")
                break  # one finding per file is enough


_VALID_VRAM_KEYS = ("gpu_ram", "gpu_total_ram")
_VRAM_TYPO_KEYS = ("vram", "gpu_vram", "gpu_mem", "gpu_memory", "gpu_ram_gb",
                   "gpu_ram_mb", "gpu_ram_total", "total_ram", "min_vram")


def _vram_findings(entry, img_name: str, rel: str) -> Iterable[Finding]:
    if not isinstance(entry, dict):
        return
    ef = entry.get("extra_filters")
    if not isinstance(ef, dict):
        return
    for bad in _VRAM_TYPO_KEYS:
        if bad in ef:
            yield Finding("L054", ERROR, img_name, rel,
                          f"extra_filters.{bad} is not a Vast filter — a VRAM floor is "
                          f"`gpu_ram` (per-GPU MB) or `gpu_total_ram` (total MB)")
    for key in _VALID_VRAM_KEYS:
        if key not in ef:
            continue
        spec = ef[key]
        ok = (isinstance(spec, dict) and any(op in spec and _is_number(spec[op])
                                             for op in ("gte", "gt", "eq"))) or _is_number(spec)
        if not ok:
            yield Finding("L054", ERROR, img_name, rel,
                          f"extra_filters.{key} needs a numeric floor (MB), e.g. "
                          f"{{{key}: {{gte: 24000}}}} — a key-only floor selects nothing")


def check_template_vram(img: Image, repo: Path) -> Iterable[Finding]:
    """L054 — a template's VRAM floor, IF present, must use a valid key (gpu_ram / gpu_total_ram,
    in MB) with a numeric value. Presence is OPTIONAL and by judgment: a single-fixed-model image
    SHOULD set it sized to its model; a model-agnostic host leaves it unset and the qa gate
    supplies a floor at rent time (ADR 0010 amendment). The linter validates FORMAT only — a
    misspelled key or a key-only floor lints falsely clean but selects nothing at rent time.
    """
    if img.cls == "base":
        return
    tdir = img.dir / "templates"
    if not tdir.is_dir():
        return
    import yaml  # lazy — only template-bearing images
    for tpl in sorted(tdir.rglob("template.yml")):
        try:
            rel = str(tpl.relative_to(img.dir))
        except ValueError:
            rel = tpl.name
        try:
            data = yaml.safe_load(tpl.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue                       # invalid YAML is L050's report, not ours
        for entry in (data if isinstance(data, list) else [data]):
            yield from _vram_findings(entry, img.name, rel)


def check_supervisor_executable(img: Image) -> Iterable[Finding]:
    """L051 — supervisor launch scripts must be executable. The generated .conf runs
    `command=/opt/supervisor-scripts/<name>.sh` directly (no interpreter prefix), so a
    non-executable script makes supervisor fail the program on launch — a fatal the static
    scaffold otherwise hides (the generator wrote the file 0644). Checks the on-disk mode,
    which git tracks (100755). Only the top-level launch scripts; sourced `utils/` are
    excluded (glob is non-recursive)."""
    if img.cls == "base" or not img.root:
        return
    sdir = img.root / "opt" / "supervisor-scripts"
    if not sdir.is_dir():
        return
    for sh in sorted(sdir.glob("*.sh")):
        if not (sh.stat().st_mode & 0o111):
            try:
                rel = str(sh.relative_to(img.dir))
            except ValueError:
                rel = sh.name
            yield Finding("L051", ERROR, img.name, rel,
                          "supervisor script is not executable (chmod +x); the .conf execs it directly")


# A hardcoded Vast launch link carries a referral id (ref_id / creator_id) — the exact
# anti-pattern L052 forbids in a co-located recommended-template README.
_HARDCODED_LAUNCH_LINK = re.compile(r"cloud\.vast\.ai[^)\s]*[?&](?:ref_id|creator_id)=")


def check_launch_link_placeholder(img: Image) -> Iterable[Finding]:
    """L052 — a shipped recommended-template README (``templates/*/README.md``) must express
    its Vast launch link as the ``<<LAUNCH_LINK>>`` placeholder, which ``create.py`` substitutes
    with the publisher's referral URL at publish time — NOT a hardcoded
    ``cloud.vast.ai/?ref_id=…`` link. A baked ref link nails one account's referral id into
    every published template and silently diverges from the publish tooling (ADR 0011).

    Scoped to ``templates/*/README.md`` — the co-located file ``create.py`` actually injects.
    The legacy root ``README.template.md`` (which the tooling never consumed) is intentionally
    out of scope, so this rule bites exactly where publishing happens and the baseline stays
    clean during the migration."""
    if img.cls == "base":
        return
    tdir = img.dir / "templates"
    if not tdir.is_dir():
        return
    for readme in sorted(tdir.rglob("README.md")):
        try:
            text = readme.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if _HARDCODED_LAUNCH_LINK.search(text):
            try:
                rel = str(readme.relative_to(img.dir))
            except ValueError:
                rel = readme.name
            yield Finding("L052", ERROR, img.name, rel,
                          "hardcoded cloud.vast.ai launch link — use the <<LAUNCH_LINK>> "
                          "placeholder (create.py substitutes the referral URL at publish) — ADR 0011")


def check_external_env(img: Image) -> Iterable[Finding]:
    """L055 — an external image FROMs the upstream, so it does NOT inherit the base image's ENV
    and must set `TCLLIBPATH=/usr/lib/tcltk/default` itself, or the base pty helper's `unbuffer`
    (Tcl/Expect) fails early in boot ("can't find package Expect") and the launch cascade dies.
    llama-factory scaffolded without it and died on a live box; every working external (incl.
    vllm-omni, which omits the /opt/sys-venv/shim PATH entry — that one is runtime-provided by
    vast_boot.d/10-prep-env.sh, so NOT gated here) sets it. Fix surface: the Dockerfile ENV.
    External only."""
    if img.cls != "external":
        return
    env = " ".join(i.value for i in parse(img.text) if i.cmd == "ENV")
    # must be set to the canonical path — a wrong value (e.g. /tmp) still breaks unbuffer/Expect,
    # so a bare `TCLLIBPATH` substring is not enough to lint clean.
    if not re.search(r"TCLLIBPATH\s*=\s*/usr/lib/tcltk/default(\b|$)", env):
        yield Finding("L055", ERROR, img.name, "Dockerfile",
                      "external image must set ENV TCLLIBPATH=/usr/lib/tcltk/default — it does not "
                      "inherit the base ENV, and the pty helper's unbuffer/Expect needs it or the "
                      "app launch dies at boot")


# ---- Portal / EXPOSE conventions (ADR 0002) — ships dormant until an image adds its own EXPOSE

def _baked_portal(img: Image):
    """Find the image's baked default PORTAL_CONFIG (the `if [[ -z ]]`-guarded
    literal in a vast_boot.d env script) and parse it. Returns (raw, entries, rel)
    where raw is None if the image bakes no default. Note: this is the AT-REST
    baked default — the runtime bind smoke gate (smoke/bind-check.sh) validates the
    rendered, post-mutation config; this static check is its fast advisory layer."""
    if not img.root:
        return None, [], None
    bootd = img.root / "etc" / "vast_boot.d"
    if not bootd.is_dir():
        return None, [], None
    for sh in sorted(bootd.glob("*.sh")):
        raw = extract_baked_default(sh.read_text(encoding="utf-8", errors="replace"))
        if raw is not None:
            rel = str(sh.relative_to(img.dir))
            try:
                return raw, parse_portal_config(raw), rel
            except ValueError:
                return raw, None, rel  # malformed — signalled by entries is None
    return None, [], None


def _ancestor_dockerfiles(img: Image, repo: Path) -> list[Path]:
    """In-repo Dockerfiles whose EXPOSE this image inherits via FROM.

    EXPOSE is cumulative through `FROM` (but NOT through `COPY`). derivative ←
    base; pytorch-nested ← pytorch ← base. external is `FROM <upstream>` and grafts
    the base via COPY, so it inherits NO base EXPOSE — it must declare its own
    Caddy-front ports in full (incl. the Instance Portal). base has no in-repo parent."""
    if img.cls == "pytorch-nested":
        return [repo / "derivatives" / "pytorch" / "Dockerfile", repo / "Dockerfile"]
    if img.cls == "derivative":
        return [repo / "Dockerfile"]
    return []  # external, base


def _inherited_exposed(img: Image, repo: Path) -> set[int]:
    ports: set[int] = set()
    for df in _ancestor_dockerfiles(img, repo):
        if df.is_file():
            ports |= exposed_ports(df.read_text(encoding="utf-8", errors="replace"))
    return ports


def check_expose_portal(img: Image, repo: Path) -> Iterable[Finding]:
    """L056/L057 — the EFFECTIVE exposed set (this image's own EXPOSE plus what it
    inherits via FROM) must cover exactly the baked PORTAL_CONFIG's Caddy-front
    ports, and the image must not itself EXPOSE a port nothing proxies. Fires only
    once an image declares its OWN EXPOSE, so it ships dormant until migration
    (ADR 0002). L057 is the fast advisory layer over the real bind smoke gate."""
    if img.cls == "base":
        return
    own = exposed_ports(img.text)
    if not own:
        return  # dormant: image hasn't opted in by adding its own EXPOSE yet
    raw, entries, rel = _baked_portal(img)
    if raw is None:
        yield Finding("L056", ERROR, img.name, "Dockerfile",
                      f"EXPOSE {sorted(own)} but no baked PORTAL_CONFIG default to validate "
                      "against (add a vast_boot.d env script with the `if [[ -z ]]` guarded default)")
        return
    if entries is None:
        yield Finding("L056", ERROR, img.name, rel, "baked PORTAL_CONFIG default is malformed (cannot parse)")
        return

    required = proxied_externals(entries)          # full Caddy-front set (no hardcoded allowlist)
    forbidden = forbidden_expose_ports(entries)
    effective = own | _inherited_exposed(img, repo)

    # completeness: every Caddy-front port must end up exposed (own or inherited) so
    # Vast maps it. Missing ones the base would provide hint "migrate the base first".
    missing = sorted(required - effective)
    if missing:
        yield Finding("L056", ERROR, img.name, "Dockerfile",
                      f"Caddy-front port(s) {missing} are in PORTAL_CONFIG but not exposed "
                      "(declare them here, or ensure an ancestor image EXPOSEs them)")
    # this image must not itself EXPOSE a non-front port. forbidden ones are the
    # security case (L057); other strays have no proxied entry at all (L056).
    bad = sorted(own & forbidden)
    if bad:
        yield Finding("L057", ERROR, img.name, "Dockerfile",
                      f"EXPOSE includes port(s) {bad} that no entry proxies (loopback/internal or "
                      "equal-port-only) — never EXPOSE a port without a Caddy auth front")
    stray = sorted(own - required - forbidden)
    if stray:
        yield Finding("L056", ERROR, img.name, "Dockerfile",
                      f"EXPOSE port(s) {stray} have no proxied PORTAL_CONFIG entry — Caddy would "
                      "not front them (remove, or add a proxied entry)")


def check_baked_portal_default(img: Image) -> Iterable[Finding]:
    """L058 (WARN) — image bakes no default PORTAL_CONFIG (relies on the launch
    template). Advisory during the ADR 0002 migration; flips to ERROR once every
    image self-describes."""
    if img.cls == "base":
        return
    raw, _, _ = _baked_portal(img)
    if raw is None:
        yield Finding("L058", WARN, img.name, "ROOT/etc/vast_boot.d",
                      "no baked PORTAL_CONFIG default (relies on the launch template)")


IMAGE_CHECKS: list[Callable[[Image], Iterable[Finding]]] = [
    check_labels, check_env_hash, check_copy_root, check_from_class, check_base_pin,
    check_torch_guard, check_no_auto_backend, check_uv_pip,
    check_conf_triple, check_util_order, check_supervisor_executable,
    check_external_env, check_baked_portal_default,
]


# ---- Repo-level checks (not tied to a single image) -------------------------

# ADR 0012: docs/adr/** is world-readable. Detect high-signal *credential shapes*
# only — near-zero false positives. The words "token"/"key"/"secret" in prose are
# NOT flagged; only a credential-shaped VALUE is. Sensitive specifics belong in the
# linked Jira issue, not the public ADR.
_ENV_IDENT = re.compile(r"^[A-Z][A-Z0-9_]*$")            # e.g. VAST_API_KEY (a reference, not a value)
_SECRET_PLACEHOLDER = re.compile(
    r"^(?:x{3,}|\*{3,}|<.*>|\$\{.*\}|\$[A-Za-z]|change|changeme|redacted|example|"
    r"placeholder|your[_-]|none|null|true|false)", re.I)
_SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----", "private key block"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key id"),
    (r"\bgh[posru]_[A-Za-z0-9]{36,}\b", "GitHub token"),
    (r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b", "Slack token"),
    (r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", "JWT"),
    # a secret-named field assigned a literal high-entropy value (see value guard below)
    (r"(?i)\b(?:api[_-]?key|secret|passwd|password|access[_-]?key|auth[_-]?token)\b"
     r"\s*[:=]\s*[\"']?([A-Za-z0-9+/_\-]{20,})[\"']?", "credential assignment"),
]


def check_adr_secrets(repo: Path) -> Iterable[Finding]:
    """L060 — no committed credential-shaped secret in docs/adr/** (a public repo). ADR 0012:
    the ADR carries the decision + rationale; the sensitive specific lives in the linked Jira
    issue. Prose mentions of 'token'/'key'/'secret' are fine — only a credential-shaped value fires."""
    adr_dir = repo / "docs" / "adr"
    if not adr_dir.is_dir():
        return
    for md in sorted(adr_dir.glob("*.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        for pat, label in _SECRET_PATTERNS:
            for m in re.finditer(pat, text):
                if m.groups():   # generic assignment: exclude env-var references and placeholders
                    val = m.group(1)
                    if _ENV_IDENT.match(val) or _SECRET_PLACEHOLDER.match(val):
                        continue
                line = text.count("\n", 0, m.start()) + 1
                yield Finding("L060", ERROR, "(repo)", f"{md.relative_to(repo)}:{line}",
                              f"credential-shaped secret in a public ADR ({label}) — move the "
                              "specific to the linked Jira issue (ADR 0012)")
                break   # one finding per pattern per file is enough signal


# ADR 0012: base-image is public, so an internal Jira ticket id (a Vast-internal
# project key) must not appear in any repo file — it leaks the tracker's structure and
# is a dangling reference to a private system for external readers. Explicit prefix set
# (not a generic `[A-Z]{2,5}-\d+`) to keep false positives at zero — extend as new
# internal projects appear. The internal issue links to the public ADR/commit; the
# public repo never names the ticket.
_INTERNAL_TRACKERS = ("CON", "HOST", "CLN")
_TICKET_RE = re.compile(r"\b(?:" + "|".join(_INTERNAL_TRACKERS) + r")-[0-9]{1,6}\b")
_TICKET_SCAN_EXT = {".md", ".yml", ".yaml", ".py", ".sh", ".txt", ".toml", ".cfg", ".ini", ".json"}
_TICKET_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
                     ".mypy_cache", ".pytest_cache", ".ruff_cache"}


def check_internal_ticket_ids(repo: Path) -> Iterable[Finding]:
    """L061 — no internal tracker ticket id (CON-/HOST-/CLN-…) anywhere in this public repo.
    Scans the working tree (text files + Dockerfiles), including the first-party external/
    wrapper images. See ADR 0012."""
    for path in sorted(repo.rglob("*")):
        rel = path.relative_to(repo)
        if any(part in _TICKET_SKIP_DIRS for part in rel.parts):
            continue
        if not path.is_file() or (path.suffix not in _TICKET_SCAN_EXT and path.name != "Dockerfile"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = _TICKET_RE.search(text)
        if m:
            line = text.count("\n", 0, m.start()) + 1
            yield Finding("L061", ERROR, "(repo)", f"{rel}:{line}",
                          f"internal ticket id {m.group(0)!r} in a public file — remove it; the "
                          "internal tracker links to the ADR/commit, not the reverse (ADR 0012)")


REPO_CHECKS: list[Callable[[Path], Iterable[Finding]]] = [check_adr_secrets, check_internal_ticket_ids]


def lint_repo(repo: Path) -> list[Finding]:
    """Run repo-level checks (not per-image). Called once by the CLI alongside the image sweep."""
    out: list[Finding] = []
    for chk in REPO_CHECKS:
        out.extend(chk(repo))
    return out


def _suppressed(img_name: str, f: Finding) -> bool:
    ex = EXCEPTIONS.get((img_name, f.code))
    return bool(ex and ex[1] in f.msg)


def lint_image(img: Image, repo: Path, *, apply_exceptions: bool = True) -> list[Finding]:
    out: list[Finding] = []
    for chk in IMAGE_CHECKS:
        out.extend(chk(img))
    out.extend(check_workflow(img, repo))
    out.extend(check_skeleton(img, repo))
    out.extend(check_no_hardcoded_staging_namespace(img, repo))
    out.extend(check_template_floor(img, repo))
    out.extend(check_template_vram(img, repo))
    out.extend(check_launch_link_placeholder(img))
    out.extend(check_no_baked_weights(img))
    out.extend(check_expose_portal(img, repo))
    if apply_exceptions:
        out = [f for f in out if not _suppressed(img.name, f)]
    return out
