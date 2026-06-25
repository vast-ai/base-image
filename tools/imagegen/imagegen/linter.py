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
    ("L010", ERROR, "Each [program:NAME]: PROC_NAME + command=/opt/supervisor-scripts/NAME.sh; file stem is a program"),
    ("L011", ERROR, "Sourced utils appear as an ordered subsequence of the canonical order"),
    ("L012", ERROR, "Supervisor command-target scripts (opt/supervisor-scripts/*.sh, excl. utils/) are executable"),
    ("L020", ERROR, "torch-drift guard: a pre==post comparison wired to an exit on the same statement"),
    ("L021", ERROR, "No `--torch-backend auto` except inside a real sed substitution"),
    ("L022", WARN, "Prefer `uv pip install` over bare `pip install`"),
    ("L030", WARN, "A build-<name>.yml workflow exists (not universal)"),
    ("L040", ERROR, "No unfilled generator skeleton markers (CHANGEME / CHANGEPORT / >>> FILL)"),
    ("L050", ERROR, "Effective EXPOSE (own + inherited via FROM) covers exactly the baked PORTAL_CONFIG Caddy-front ports"),
    ("L051", ERROR, "EXPOSE never includes a port no entry proxies (loopback/internal or equal-port-only)"),
    ("L052", WARN, "Image bakes a default PORTAL_CONFIG (else it relies on the launch template)"),
    ("L060", ERROR, "A shipped derivative instance test (tests/<name>.d/*.sh) is executable, sources lib.sh, and has exactly one `test_pass`"),
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


def check_script_exec(img: Image) -> Iterable[Finding]:
    """L012 — supervisord `command=` target scripts must be executable. conf.d runs
    `command=/opt/supervisor-scripts/<x>.sh` directly (not `bash <x>.sh`), so a script
    without +x fails to launch. Scope = direct children of opt/supervisor-scripts/;
    utils/ are SOURCED (`. logging.sh`), not executed, and are exempt."""
    if not img.root:
        return
    sdir = img.root / "opt" / "supervisor-scripts"
    if not sdir.is_dir():
        return
    for script in sorted(sdir.glob("*.sh")):  # non-recursive: excludes utils/
        if not (script.stat().st_mode & 0o111):
            rel = str(script.relative_to(img.dir))
            yield Finding("L012", ERROR, img.name, rel, "supervisor command-target script is not executable (chmod +x)")


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


def check_skeleton(img: Image, repo: Path) -> Iterable[Finding]:
    """L040 — unfilled generator markers must not pass as 'clean'. So a scaffold can
    never be mistaken for a complete, buildable image. Scoped to the image's own files."""
    if img.cls == "base":
        return
    files = [img.dockerfile, img.dir / "README.md", img.dir / "README.template.md"]
    if img.root:
        files += [p for p in img.root.rglob("*") if p.is_file()]
    wf = repo / ".github" / "workflows" / f"build-{img.name}.yml"
    if wf.exists():
        files.append(wf)
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


# ---- PORTAL_CONFIG <-> EXPOSE (ADR 0002) ------------------------------------

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
    """L050/L051 — the EFFECTIVE exposed set (this image's own EXPOSE plus what it
    inherits via FROM) must cover exactly the baked PORTAL_CONFIG's Caddy-front
    ports, and the image must not itself EXPOSE a port nothing proxies. Fires only
    once an image declares its OWN EXPOSE, so it ships dormant until migration
    (ADR 0002). L051 is the fast advisory layer over the real bind smoke gate."""
    if img.cls == "base":
        return
    own = exposed_ports(img.text)
    if not own:
        return  # dormant: image hasn't opted in by adding its own EXPOSE yet
    raw, entries, rel = _baked_portal(img)
    if raw is None:
        yield Finding("L050", ERROR, img.name, "Dockerfile",
                      f"EXPOSE {sorted(own)} but no baked PORTAL_CONFIG default to validate "
                      "against (add a vast_boot.d env script with the `if [[ -z ]]` guarded default)")
        return
    if entries is None:
        yield Finding("L050", ERROR, img.name, rel, "baked PORTAL_CONFIG default is malformed (cannot parse)")
        return

    required = proxied_externals(entries)          # full Caddy-front set (no hardcoded allowlist)
    forbidden = forbidden_expose_ports(entries)
    effective = own | _inherited_exposed(img, repo)

    # completeness: every Caddy-front port must end up exposed (own or inherited) so
    # Vast maps it. Missing ones the base would provide hint "migrate the base first".
    missing = sorted(required - effective)
    if missing:
        yield Finding("L050", ERROR, img.name, "Dockerfile",
                      f"Caddy-front port(s) {missing} are in PORTAL_CONFIG but not exposed "
                      "(declare them here, or ensure an ancestor image EXPOSEs them)")
    # this image must not itself EXPOSE a non-front port. forbidden ones are the
    # security case (L051); other strays have no proxied entry at all (L050).
    bad = sorted(own & forbidden)
    if bad:
        yield Finding("L051", ERROR, img.name, "Dockerfile",
                      f"EXPOSE includes port(s) {bad} that no entry proxies (loopback/internal or "
                      "equal-port-only) — never EXPOSE a port without a Caddy auth front")
    stray = sorted(own - required - forbidden)
    if stray:
        yield Finding("L050", ERROR, img.name, "Dockerfile",
                      f"EXPOSE port(s) {stray} have no proxied PORTAL_CONFIG entry — Caddy would "
                      "not front them (remove, or add a proxied entry)")


def check_baked_portal_default(img: Image) -> Iterable[Finding]:
    """L052 (WARN) — image bakes no default PORTAL_CONFIG (relies on the launch
    template). Advisory during the ADR 0002 migration; flips to ERROR once every
    image self-describes."""
    if img.cls == "base":
        return
    raw, _, _ = _baked_portal(img)
    if raw is None:
        yield Finding("L052", WARN, img.name, "ROOT/etc/vast_boot.d",
                      "no baked PORTAL_CONFIG default (relies on the launch template)")


def check_instance_test_shape(img: Image) -> Iterable[Finding]:
    """L060 — shape-only check on a SHIPPED derivative instance test
    (ROOT/opt/instance-tools/tests/<name>.d/*.sh): it must be executable (the
    runner discovers only `-executable` scripts), source the shared tests/lib.sh,
    and have exactly one `test_pass` success terminal. This does NOT require any
    image to ship a test (no mandate) and cannot judge coverage — a static linter
    can only enforce that a test which DOES exist follows the harness contract, so
    a present-but-malformed test (silently never runs, or has no success path)
    can't masquerade as coverage."""
    if not img.root:
        return
    tests_dir = img.root / "opt" / "instance-tools" / "tests"
    if not tests_dir.is_dir():
        return
    for script in sorted(tests_dir.glob("*.d/*.sh")):
        rel = str(script.relative_to(img.dir))
        if not (script.stat().st_mode & 0o111):
            yield Finding("L060", ERROR, img.name, rel,
                          "instance test is not executable (runner discovers only -executable scripts; chmod +x)")
        code = "\n".join(l for l in script.read_text(encoding="utf-8", errors="replace").splitlines()
                         if not l.lstrip().startswith("#"))
        if not re.search(r"^\s*(?:\.|source)\s+.*lib\.sh", code, re.M):
            yield Finding("L060", ERROR, img.name, rel, "instance test does not source the shared tests/lib.sh")
        n = len(re.findall(r"(?<![\w.])test_pass\b", code))
        if n != 1:
            yield Finding("L060", ERROR, img.name, rel,
                          f"instance test must have exactly one `test_pass` success terminal (found {n})")


IMAGE_CHECKS: list[Callable[[Image], Iterable[Finding]]] = [
    check_labels, check_env_hash, check_copy_root, check_from_class,
    check_torch_guard, check_no_auto_backend, check_uv_pip,
    check_conf_triple, check_util_order, check_script_exec,
    check_baked_portal_default, check_instance_test_shape,
]


def _suppressed(img_name: str, f: Finding) -> bool:
    ex = EXCEPTIONS.get((img_name, f.code))
    return bool(ex and ex[1] in f.msg)


def lint_image(img: Image, repo: Path, *, apply_exceptions: bool = True) -> list[Finding]:
    out: list[Finding] = []
    for chk in IMAGE_CHECKS:
        out.extend(chk(img))
    out.extend(check_expose_portal(img, repo))
    out.extend(check_workflow(img, repo))
    out.extend(check_skeleton(img, repo))
    if apply_exceptions:
        out = [f for f in out if not _suppressed(img.name, f)]
    return out
