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
    ("L056", ERROR, "An image that source-builds Unsloth Studio's llama.cpp (`unsloth studio setup`) MUST carry a real post-build file-existence assertion for the CUDA backend (`test -f …libggml-cuda.so`; a bare mention of the name does not count) — setup.sh gates -DGGML_CUDA=ON on a runtime GPU probe absent in `docker build`, so without the assert it silently ships a CPU-only binary and every inference runs on CPU (ADR 0016)"),
    ("L057", ERROR, "A gating QA template declares env.INSTANCE_TEST_REQUIRE_PASS naming the tests that must have PASSED — without it a self-skipping test (the GPU trio skips when nvidia-smi/libcuda is absent) reports the suite green and the gate certifies an image it never exercised (ADR 0019)"),
    ("L058", ERROR, "A QA template that declares recommended_disk_space also declares a disk_space floor in extra_filters at least that large — recommended_disk_space is only the REQUEST for overlayfs space (the image is stored separately and not charged to the instance), so without a matching search floor the client rents a box that cannot satisfy the request and only learns after launch, burning a bounded launch attempt (ADR 0019)"),
    ("L059", ERROR, "Every test named in a gating QA template's INSTANCE_TEST_REQUIRE_PASS contains at least one real test_fail/fail_later CALL (a mention in a comment does not count) — L057 makes the template name the tests that must pass, and this closes the next hole down: a named test with no failure path reports `passed` on every box, so requiring it asserts nothing beyond the script reaching its test_pass, and the gate reads as coverage while certifying nothing (ADR 0019)"),
    ("L065", ERROR, "Every shipped instance test (ROOT/opt/instance-tools/tests/**/*.sh, and the same path under derivatives/external overlays) is executable — runner.sh discovers tests with `find … -executable`, so a 0644 test is not skipped, not reported missing, and emits no line at all: it silently does not exist. base/11-instance-metadata.sh and base/12-provisioning.sh shipped 0644 from their first commit and had therefore never run once, which also meant lib.sh's instance_field() always returned empty and nothing ever waited for provisioning to finish. Same failure and same fix as L051 for supervisor scripts"),
    ("L060", ERROR, "No credential-shaped secret committed in docs/adr/** — this repo is public; sensitive specifics live in the internal tracker, not the ADR (ADR 0012)"),
    ("L061", ERROR, "No internal tracker ticket id (CON-/HOST-/CLN-…) in any public-repo file — it leaks the internal tracker and dangles for external readers; the internal issue links to the ADR/commit, not the reverse (ADR 0012)"),
    ("L062", ERROR, "A shipped test that defers a failure MUST report it before every exit that does not fail — `fail_later` (and `http_check`, which calls it internally) only RECORDS a failure; `report_failures` is what turns the record into a failing test. Reaching test_pass or test_skip with one pending prints `FAIL: ...` and then exits 0 (or 77), silently discarding it — the exact skip-as-pass shape the QA gate exists to close. Presence is not enough and neither is textual order: a `report_failures` that runs only inside a conditional does not clear a failure recorded outside it (found while adding the CUDA-libpath check to base/60-gpu-cuda, twice: once for the missing report, once for an early exit that discarded it)"),
    ("L063", ERROR, "No shipped script parses nvidia-smi's human-readable table for the driver's CUDA version — use /opt/instance-tools/bin/cuda-driver-version, which asks the driver via cuDriverGetVersion. Driver branch 610 renamed that field from `CUDA Version:` to `CUDA UMD Version:`, so every scrape returned empty on every 610 host at once; in 05-configure-cuda.sh the empty value aborted AFTER the CUDA ld.so.conf entries had already been deleted, leaving instances with no system CUDA library path (invisible, because torch uses its own bundled libs)"),
    ("L064", ERROR, "No shipped script open-codes the native-libcuda bypass (an `LD_LIBRARY_PATH=<dir>` wrapper around cuda-driver-version, or its own search for libcuda.so.1 to feed one) — call `/opt/instance-tools/bin/cuda-driver-version --native`, which dlopens an absolute path and then confirms from /proc/self/maps which file was actually mapped. LD_LIBRARY_PATH is a search HINT, not a pin: name a directory with no loadable libcuda.so.1 and the loader carries on to the ld.so cache, i.e. to a previous boot's forward-compat library — a probe that fails OPEN to precisely the wrong answer. The same six lines lived in both 05-configure-cuda.sh and base/60-gpu-cuda, so the test agreed with the boot script instead of checking it"),
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

    Applies to the base image too (ADR 0019): base gained its own QA template, and
    the floor rule is exactly as load-bearing there. Base's ``img.dir`` is the repo
    root and the scan is rooted at ``<img.dir>/templates``, so this picks up
    ``templates/base-qa/`` and nothing else.
    """
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

    Applies to the base image too (ADR 0019) — see the note in check_template_floor.
    """
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


# Tests a gating QA template must demand actually ran. The GPU trio is the case that
# motivated the rule: all three open with `has_gpu || test_skip`, so on a box whose
# driver or CUDA userland never came up they skip, the suite reports green, and the
# gate certifies an image it never exercised.
_REQUIRED_GPU_TESTS = ("base/60-gpu-cuda", "base/61-cuda-compute", "base/62-gpu-libraries")


def check_template_require_pass(img: Image, repo: Path) -> Iterable[Finding]:
    """L057 — a gating QA template declares env.INSTANCE_TEST_REQUIRE_PASS (ADR 0019).

    Scoped to the base image's QA template for now. comfyui-qa and vllm-qa have the
    same hole, but widening the rule to them is a separate change that has to
    re-validate two live, currently-passing gates — a linter rule is not the place to
    quietly turn those red. Widen once each has been re-validated.

    Format only: this asserts the declaration exists and covers the GPU trio. Whether
    the tests then pass is the runner's job (INSTANCE_TEST_REQUIRE_PASS) and CI's
    (qa_verdict) — the two enforcement layers this declaration feeds.
    """
    if img.cls != "base":
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
            if not isinstance(entry, dict):
                continue
            env = entry.get("env")
            declared = env.get("INSTANCE_TEST_REQUIRE_PASS", "") if isinstance(env, dict) else ""
            names = set(str(declared).replace(",", " ").split())
            if not names:
                yield Finding("L057", ERROR, img.name, rel,
                              "no env.INSTANCE_TEST_REQUIRE_PASS — a self-skipping test would "
                              "report the suite green and the gate would certify an untested image")
                continue
            missing = [t for t in _REQUIRED_GPU_TESTS if t not in names]
            if missing:
                yield Finding("L057", ERROR, img.name, rel,
                              "env.INSTANCE_TEST_REQUIRE_PASS omits " + ", ".join(missing) +
                              " — these skip themselves when the GPU/driver is unavailable")



def _has_failure_path(text: str) -> bool:
    """True if the script can actually FAIL, not merely mention failing.

    Counts invocations at a command position only. A bare mention does not count
    — `62-gpu-libraries.sh` carried the comment

        # FAILURES and fail_later/report_failures come from lib.sh

    and no call, so any rule matching the substring would have been satisfied by
    the comment describing the machinery the file never used. Same trap L056
    documents for the libggml-cuda assertion.
    """
    for raw in text.splitlines():
        line = _strip_comment(raw)
        for fn in ("test_fail", "fail_later"):
            if _line_calls(line, fn):
                return True
    return False


def check_instance_tests_executable(img: Image, repo: Path) -> Iterable[Finding]:
    """L065 — a shipped instance test must be executable.

    `runner.sh` discovers with `find "${TESTS_DIR}/base" -name '*.sh' -executable`,
    and the Dockerfile ships the overlay with a bare `COPY ./ROOT/ /`, which
    preserves mode. So a test committed 0644 is not collected — and unlike a
    skip or a missing required test, it produces NO output whatsoever. It does
    not appear in the run, in the counts, or in the results JSON. The only way
    to notice is to compare a directory listing against the collected list.

    That is not hypothetical: `base/11-instance-metadata.sh` and
    `base/12-provisioning.sh` were 0644 from their introducing commit and had
    never executed. Two consequences ran unnoticed for the whole of that period —
    `lib.sh`'s `instance_field()` reads a metadata file only test 11 writes, so it
    could only ever have returned empty (it has no callers today, which is the
    only reason nothing broke); and `runner.sh`'s
    "no blind provisioning wait — 12-provisioning.sh handles monitoring" was
    false, so tests that document themselves as running after provisioning were
    racing it.

    Repo-level (like L060/L061): the tests are shipped by whichever image owns
    the overlay, and a mode is a property of the file, not of a build.
    """
    if img.cls != "base":
        return
    roots = [repo / "ROOT/opt/instance-tools/tests"]
    roots += sorted((repo / "derivatives").glob("*/ROOT/opt/instance-tools/tests"))
    roots += sorted((repo / "derivatives").glob("*/derivatives/*/ROOT/opt/instance-tools/tests"))
    roots += sorted((repo / "external").glob("*/ROOT/opt/instance-tools/tests"))
    for root in roots:
        if not root.is_dir():
            continue
        for sh in sorted(root.rglob("*.sh")):
            # lib.sh is SOURCED by every test, never executed. runner.sh is
            # executed and is deliberately NOT exempt: exempting the whole tests
            # root (the first version of this rule) would have left the one file
            # whose losing +x disables the entire suite ungated.
            if sh.name == "lib.sh":
                continue
            if not (sh.stat().st_mode & 0o111):
                try:
                    rel = str(sh.relative_to(repo))
                except ValueError:
                    rel = sh.name
                yield Finding("L065", ERROR, img.name, rel,
                              "instance test is not executable (chmod +x) — runner.sh "
                              "collects with `find -executable`, so it would never run "
                              "and would report nothing at all")


def check_required_tests_can_fail(img: Image, repo: Path) -> Iterable[Finding]:
    """L059 — a test named in INSTANCE_TEST_REQUIRE_PASS must be able to fail.

    L057 makes a gating template NAME the tests that must pass. This closes the
    next hole down: a named test that contains no failure path at all reports
    `passed` on every box, so requiring it asserts nothing beyond the fact that
    the script ran to its `test_pass`. The gate then reads as coverage while
    certifying nothing — the same skip-as-pass shape as an unnamed self-skipping
    test, one level lower and harder to see (ADR 0019).

    Not hypothetical: `base/62-gpu-libraries.sh` was named in base-qa's
    require-pass set while every one of its branches was an `echo`/`WARN`. Under
    the gate it asserted exactly `has_gpu`, which 60 and 61 already assert.

    Scoped to base, following L057's precedent: comfyui-qa and vllm-qa are live
    gates, and turning them red from a linter rule is a separate change that must
    re-validate each first.

    Deliberately weak by design: this asserts a failure path EXISTS, not that it
    is a good one. A test that can fail for the wrong reasons is a review problem;
    a test that cannot fail at all is a structural one, and only the second is
    decidable by reading the file.
    """
    if img.cls != "base":
        return
    tdir = img.dir / "templates"
    if not tdir.is_dir():
        return
    import yaml  # lazy — only template-bearing images
    # A required test may live in the base overlay OR in a derivative's own tests
    # dir. Looking only in ROOT/ would silently skip every derivative test — the
    # rule would report clean precisely where the newest tests are.
    test_roots = [repo / "ROOT/opt/instance-tools/tests"]
    test_roots += sorted((repo / "derivatives").glob("*/ROOT/opt/instance-tools/tests"))

    def _find(name: str) -> Path | None:
        for root in test_roots:
            p = root / f"{name}.sh"
            if p.is_file():
                return p
            # derivatives name their dir `<img>.d` but declare `<img>/<test>`
            head, _, tail = name.partition("/")
            if tail:
                p = root / f"{head}.d" / f"{tail}.sh"
                if p.is_file():
                    return p
        return None

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
            if not isinstance(entry, dict):
                continue
            env = entry.get("env")
            declared = env.get("INSTANCE_TEST_REQUIRE_PASS", "") if isinstance(env, dict) else ""
            for name in str(declared).replace(",", " ").split():
                path = _find(name)
                if path is None:
                    # Not resolvable in this repo at all — an external image's
                    # test, or a typo. The runner fails closed on a required test
                    # missing from the image, which is the right layer for that;
                    # this rule only judges files it can actually read.
                    continue
                if not _has_failure_path(path.read_text(encoding="utf-8", errors="replace")):
                    yield Finding("L059", ERROR, img.name, rel,
                                  f"required test {name} contains no test_fail/fail_later "
                                  "call — it cannot fail, so naming it in "
                                  "INSTANCE_TEST_REQUIRE_PASS asserts nothing")


def _line_calls(line: str, fn: str) -> bool:
    """True if `fn` is invoked at a command position on THIS line.

    `{` counts: `finish() { report_failures; test_pass "ok"; }` is a call, and
    omitting it made the rule report a *correct* helper as never calling
    report_failures.

    `)` counts too, for `case` arms — `a) fail_later "x" "y" ;;`. Omitting it did
    worse than miss the call: with no deferring call seen anywhere, the whole
    file was treated as non-deferring and skipped, taking the never-reports check
    with it. A blind spot that silently exempts the file is not a blind spot, it
    is a hole.
    """
    return bool(re.search(rf"(^|[;&|{{()]|\b(?:then|else|do|;)\s)\s*{fn}\b", line))


def _line_calls_unconditionally(line: str, fn: str) -> bool:
    """True if `fn` runs whenever this line is reached.

    `report_failures` only clears a pending failure if it is not itself guarded:
    `[[ -n "$Q" ]] && report_failures` and `... ; then report_failures` run only
    sometimes, so treating them as a clear is how the rule certifies the very bug
    it exists to catch. A statement STARTING with the name is unguarded; one
    reached through `&&`, `||` or `then` is not — so the split is on `;`, `{` and
    `}`, which end a statement, and never on `&&`/`||`, which do not.

    Block structure (a `report_failures` inside an if that may not run) is a
    separate question, handled by the frame stack in _scan_shell_flow.
    """
    for stmt in re.split(r"[;{}]", line):
        if re.match(rf"\s*{fn}\b", stmt):
            return True
    return False


def _blank_quoted(line: str) -> str:
    """Replace quoted spans with spaces, preserving length.

    Structure detection must not read shell keywords out of string literals:
    `grep -qE "a|done"` and `echo "; fi"` are data, not control flow, and a
    close keyword found inside one suppresses a frame that should have been
    pushed. Length is preserved so reported columns stay meaningful.
    """
    out = []
    quote = None
    escaped = False
    for ch in line:
        if quote:
            out.append(" " if ch != quote or escaped else ch)
            if escaped:
                escaped = False
            elif ch == "\\" and quote == '"':
                escaped = True
            elif ch == quote:
                quote = None
                out[-1] = ch
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        else:
            out.append(ch)
    return "".join(out)


def _calls(text: str, fn: str) -> bool:
    """True if `fn` is INVOKED at a command position (not merely mentioned)."""
    for raw in text.splitlines():
        if _line_calls(_strip_comment(raw), fn):
            return True
    return False


# Exits that DISCARD pending deferred failures: both leave the runner with a
# non-failing status (test_pass exits 0, test_skip exits 77), so reaching either
# with a recorded failure throws it away. test_fail is not here — it exits
# non-zero, so the failure is not lost.
_DISCARDING_EXITS = ("test_pass", "test_skip")
_DEFERRING_CALLS = ("fail_later", "http_check")

_FN_DEF = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*\{")
_BLOCK_OPEN = re.compile(r"^\s*(if|for|while|until|case)\b")
_BLOCK_CLOSE = re.compile(r"^\s*(fi|done|esac)\b")
# `elif` restarts an alternative but does NOT mean the chain is exhaustive —
# an if/elif with no else can fall through all of them, so only a literal `else`
# may suppress the fall-through arm at close.
_BLOCK_ALT = re.compile(r"^\s*(else|elif)\b")
_BLOCK_ELSE = re.compile(r"^\s*else\b")
# A block that opens and closes on ONE line (`if Z; then fin; fi`) is a statement,
# not a frame. Pushing a frame for it leaks: nothing ever pops it, so every later
# close pops the wrong frame and the merge is silently wrong from there on.
#
# Anchored to a real statement boundary — `;`, `&&`, `||` or line start. A bare
# `|` must NOT count: `grep -qE "a|done"` contains `|done`, and treating that as
# a close suppressed the frame push for the whole block, making every conditional
# inside it read as unconditional. That turned this fix into the very
# false-negative L062 exists to prevent.
_BLOCK_CLOSE_ANYWHERE = re.compile(r"(^|;|&&|\|\|)\s*(fi|done|esac)\b")


def _walk(lines, pending, fns):
    """Frame-aware walk over a list of already-stripped shell lines.

    Shared by the file-level scan and by function-body analysis so a guard
    closed at one level cannot stay open at the other — which is exactly how the
    guarded-helper hole survived being fixed at the call site.

    Returns (first-bad-exit-index, ever_deferred, ever_reported, pending-at-end).
    The index is 1-based into `lines`, so a caller passing a filtered list must
    map it back to the real file line itself.
    """
    ever_deferred = False
    ever_reported = False
    stack: list[dict] = []
    bad_exit = None

    for n, line in enumerate(lines, 1):
        if _BLOCK_CLOSE.match(line) and stack:
            frame = stack.pop()
            frame["arms"].append(pending)
            if not frame["has_else"]:
                frame["arms"].append(frame["entry"])
            pending = any(frame["arms"])
            continue
        if _BLOCK_ALT.match(line) and stack:
            stack[-1]["arms"].append(pending)
            # Only a literal `else` makes the chain exhaustive. An `elif` with no
            # `else` after it can fall through every arm, so the entry state must
            # still be merged at close.
            if _BLOCK_ELSE.match(line):
                stack[-1]["has_else"] = True
            pending = stack[-1]["entry"]
            # `elif cond; then` may itself call things; fall through to the
            # call handling below rather than `continue`.
        elif _BLOCK_OPEN.match(line) and not _BLOCK_CLOSE_ANYWHERE.search(line):
            stack.append({"entry": pending, "arms": [], "has_else": False})

        for name, info in fns.items():
            if not _line_calls(line, name):
                continue
            if info["defers"]:
                pending = True
                ever_deferred = True
            # A helper only CLEARS when the call itself is unguarded — the same
            # rule as an inline report_failures. `if Z; then fin; fi` runs `fin`
            # only sometimes, so it cannot be trusted to have reported.
            if info["clears"] and _line_calls_unconditionally(line, name):
                ever_reported = True
                pending = False
            elif info["reports"]:
                # It reports, but not in a way that can be trusted to have run —
                # enough to satisfy "this file does call report_failures", not
                # enough to clear.
                ever_reported = True
            if info["exits"] and pending and bad_exit is None:
                bad_exit = n
            elif info["exits_dirty"] and info["defers"] and bad_exit is None:
                # The helper discards its OWN deferred failure, regardless of
                # what was pending at the call site.
                bad_exit = n

        if any(_line_calls(line, d) for d in _DEFERRING_CALLS):
            pending = True
            ever_deferred = True
        if _line_calls(line, "report_failures"):
            ever_reported = True
            if _line_calls_unconditionally(line, "report_failures"):
                pending = False
        if pending and bad_exit is None and any(
            _line_calls(line, e) for e in _DISCARDING_EXITS
        ):
            bad_exit = n

    return bad_exit, ever_deferred, ever_reported, pending


def _scan_shell_flow(text: str):
    """Walk a shell test, tracking whether a deferred failure is pending.

    Returns (line-number-of-first-discarding-exit-while-pending, ever_deferred,
    ever_reported).

    This is a control-flow approximation, not a bash parser, and it is
    deliberately asymmetric: it is CONSERVATIVE about what clears a pending
    failure (only an unguarded `report_failures`) and GENEROUS about what
    defers one (any branch that could run). The alternative — a linear walk —
    got both directions wrong: a `report_failures` inside an untaken `if`
    cleared the state for the rest of the file (the rule certifying its own
    bug), while a `test_pass` in the `else` arm of the branch that deferred was
    reported as a defect on correct code.

    Branch handling: entering `if`/`for`/`while`/`case` snapshots the pending
    state; `else`/`elif` restarts the alternative from that snapshot; closing
    merges every arm by OR, including the fall-through arm when there is no
    `else` (a loop body or an unelsed `if` may not run at all).

    Function bodies are analysed by the SAME walk and their effect applied at
    the CALL site, so `finish() { report_failures; test_pass "ok"; }` reads as
    "clears, then exits" wherever `finish` is invoked — while
    `fin() { if x; then report_failures; fi; }` does not clear, because the
    frame merge inside the body says it might not have run. Analysing bodies
    with a flat presence scan left exactly that hole one level down from the
    call-site guard.

    Known limit: a `fail_later` inside a subshell or a pipeline loses its record
    at RUNTIME (the array is written in a child), which this cannot see — that
    is a different defect needing a different check.
    """
    lines = [_blank_quoted(_strip_comment(raw)) for raw in text.splitlines()]

    # Pass 1: function bodies. Brace-depth counted from the definition line.
    fns: dict[str, dict] = {}
    body_lines: set[int] = set()
    i = 0
    while i < len(lines):
        m = _FN_DEF.match(lines[i])
        if not m:
            i += 1
            continue
        # End the body at a closing brace in column 0 (house style, and what
        # every shipped test uses) OR when brace depth returns to zero,
        # whichever comes first. Either alone is brittle: an indented `}` never
        # matches the first, and an unbalanced brace inside a string or regex
        # (`grep -E '[{]'`) breaks the second — and a body that never ends
        # swallows the rest of the file, taking its report_failures with it.
        depth = lines[i].count("{") - lines[i].count("}")
        body = [lines[i]]
        body_lines.add(i)
        j = i
        while depth > 0 and j + 1 < len(lines):
            j += 1
            body.append(lines[j])
            body_lines.add(j)
            if lines[j].startswith("}"):
                break
            depth += lines[j].count("{") - lines[j].count("}")
        # The body goes through the same walk, twice, because two different
        # questions are being asked and each needs its own starting state:
        #   * seeded PENDING, does the body clear it? Only an unconditional
        #     report does; a report inside an unelsed `if` leaves the merge
        #     pending, which is the correct answer.
        #   * seeded CLEAN, does the body reach a discarding exit with its own
        #     deferral outstanding? That is a defect wherever it is called.
        # Order matters for both, which a presence scan cannot express:
        # `bad() { fail_later x y; test_pass ok; report_failures; }` contains a
        # report but discards the failure before reaching it.
        _, _, _, pending_after = _walk(body, True, fns)
        dirty_exit, body_defers, body_reports, _ = _walk(body, False, fns)
        fns[m.group(1)] = {
            "clears": not pending_after,
            "reports": body_reports,
            "exits": any(_line_calls(b, e) for b in body for e in _DISCARDING_EXITS),
            "exits_dirty": dirty_exit is not None,
            "defers": body_defers,
        }
        i = j + 1

    # Function bodies are analysed above and skipped here, so the walk sees only
    # top-level flow. Real file line numbers are carried alongside, because a
    # finding that points at the wrong line is a finding nobody can act on.
    outer, outer_lineno = [], []
    for k, ln in enumerate(lines):
        if k not in body_lines:
            outer.append(ln)
            outer_lineno.append(k + 1)
    bad_exit, ever_deferred, ever_reported, _ = _walk(outer, False, fns)
    if bad_exit is not None:
        bad_exit = outer_lineno[bad_exit - 1]
    return bad_exit, ever_deferred, ever_reported


_SMI_TEXT_PARSE = re.compile(r"CUDA[A-Za-z ]*Version[:\\]")

def _strip_comment(line: str) -> str:
    """Drop a trailing shell comment without mangling code.

    Splitting on the first `#` is wrong often enough to matter: it truncates
    `${varname#"${prefix}"}` mid-expansion (which unbalanced the brace count in
    26-caddy-auth.sh and swallowed the rest of the file, hiding its
    report_failures) and `sed 's#a#b#'`. In bash a `#` starts a comment only at
    the start of a word and only outside quotes.
    """
    out: list[str] = []
    quote = None
    escaped = False
    for ch in line:
        if quote:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\" and quote == '"':
                escaped = True
            elif ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "#" and (not out or out[-1] in " \t"):
            break
        else:
            out.append(ch)
    return "".join(out)


_CUDA_HELPER = "cuda-driver-version"
# `LD_LIBRARY_PATH=<anything> ... cuda-driver-version` — the fail-open wrapper.
_LDPATH_PROBE = re.compile(r"LD_LIBRARY_PATH=.*" + _CUDA_HELPER)
# A hand-rolled hunt for the driver library, which only ever feeds such a wrapper.
# The exact SONAME, deliberately: probing for `libcuda.so.*` is how a script asks
# "are there compat libs in this directory" — a legitimate, different question
# that 05-configure-cuda.sh and base/60-gpu-cuda both ask.
_LIBCUDA_SEARCH = re.compile(r"\b(?:find|ls|compgen|glob|rglob|iglob)\b[^\n]*libcuda\.so\.1\b")


def _shipped_scripts(repo: Path):
    """Yield (path, repo-relative-path, [(lineno, code-without-comment)]) for
    every script that ships INSIDE an image.

    Extension is not the boundary: 10 of the 12 tools in
    ROOT/opt/instance-tools/bin are extensionless by convention, including the
    CUDA helper itself, so an `*.sh`/`*.py` glob silently exempts the directory
    most likely to re-introduce these bugs. Derivative and external overlays
    ship into images too and are included for the same reason.
    """
    roots = [repo / "ROOT", repo / "portal-aio"]
    roots += sorted((repo / "derivatives").glob("*/ROOT"))
    roots += sorted((repo / "derivatives").glob("*/derivatives/*/ROOT"))
    roots += sorted((repo / "external").glob("*/ROOT"))
    for root in roots:
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*")):
            if not f.is_file() or f.is_symlink():
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue                       # binary or unreadable: not a script
            if f.suffix not in (".sh", ".py") and not text.startswith("#!"):
                continue
            lines = [(n, _strip_comment(raw))
                     for n, raw in enumerate(text.splitlines(), 1)]
            try:
                rel = str(f.relative_to(repo))
            except ValueError:
                rel = f.name
            yield f, rel, lines


def check_fail_later_is_reported(img: Image, repo: Path) -> Iterable[Finding]:
    """L062 — a test that defers a failure must also report it.

    `fail_later` only RECORDS a failure; `report_failures` is what turns the
    record into a failing test. Without it the test prints `FAIL: ...` and then
    exits 0 via `test_pass`, so the suite goes green with a visible failure in
    the log — the skip-as-pass shape the gate exists to close, wearing a
    different hat.

    Found the honest way: adding the CUDA-libpath check to base/60-gpu-cuda with
    `fail_later` produced exactly that. It printed FAIL and passed. Only running
    it caught that; reading it did not.

    Scoped to base, following L057/L059. Every image's tests are read from the
    base overlay plus any derivative tests dir, and the check runs once per
    repo rather than per template — a shipped test is wrong regardless of which
    template happens to name it.
    """
    if img.cls != "base":
        return
    roots = [repo / "ROOT/opt/instance-tools/tests"]
    roots += sorted((repo / "derivatives").glob("*/ROOT/opt/instance-tools/tests"))
    for root in roots:
        if not root.is_dir():
            continue
        for sub in sorted(root.iterdir()):
            if not sub.is_dir() or (sub.name != "base" and not sub.name.endswith(".d")):
                continue
            for f in sorted(sub.glob("*.sh")):
                text = f.read_text(encoding="utf-8", errors="replace")
                # ORDER MATTERS, not mere presence. `report_failures` somewhere in
                # the file is not enough: an EARLIER test_pass/test_skip exits
                # without failing and throws the deferred failures away. That is
                # not hypothetical — the driver-version assertion added to
                # base/60-gpu-cuda was discarded on its "no CUDA toolkit installed"
                # early exit while this rule, in its presence-only form, certified
                # the file as compliant.
                #
                # http_check (lib.sh) calls fail_later INTERNALLY, so a test using
                # only http_check defers failures without ever naming fail_later.
                # Gating on the literal name alone would let the most common
                # deferring shape through — which it did, until it was added to
                # the deferring set.
                bad_exit, ever_deferred, ever_reported = _scan_shell_flow(text)
                if not ever_deferred:
                    continue
                if bad_exit is not None:
                    try:
                        rel = str(f.relative_to(repo))
                    except ValueError:
                        rel = f.name
                    yield Finding("L062", ERROR, img.name, f"{rel}:{bad_exit}",
                                  "test_pass/test_skip exits without failing here while a "
                                  "deferred failure is pending — call report_failures "
                                  "before every exit path")
                    continue
                if not ever_reported:
                    try:
                        rel = str(f.relative_to(repo))
                    except ValueError:
                        rel = f.name
                    yield Finding("L062", ERROR, img.name, rel,
                                  "calls fail_later but never report_failures — the deferred "
                                  "failure is discarded and the test exits 0 via test_pass")


def check_no_nvidia_smi_text_parse(img: Image, repo: Path) -> Iterable[Finding]:
    """L063 — do not scrape nvidia-smi's table for the driver CUDA version.

    NVIDIA renamed the field in driver 610 ("CUDA Version" -> "CUDA UMD
    Version"). Every scrape of it returned empty on every 610 host
    simultaneously — a deterministic fleet-wide break, not a flaky box. The
    stable answer is cuDriverGetVersion via libcuda, wrapped by
    /opt/instance-tools/bin/cuda-driver-version.

    Scoped to base (which owns these scripts) and to shipped runtime code: the
    helper itself is exempt, as is any comment explaining the history.
    """
    if img.cls != "base":
        return
    for f, rel, lines in _shipped_scripts(repo):
        if f.name == _CUDA_HELPER:
            continue                           # the one sanctioned implementation
        for n, line in lines:
            if _SMI_TEXT_PARSE.search(line):
                yield Finding("L063", ERROR, img.name, f"{rel}:{n}",
                              "parses nvidia-smi's table for the CUDA version — "
                              "use /opt/instance-tools/bin/cuda-driver-version instead")


def check_no_open_coded_native_libcuda(img: Image, repo: Path) -> Iterable[Finding]:
    """L064 — do not re-derive the native libcuda; ask the helper.

    `cuDriverGetVersion` reports whichever libcuda.so.1 the loader resolved, so
    any code deciding *whether forward compat is needed* must exclude a compat
    library explicitly. Both callers used to do that themselves, in bash:

        _libcuda_path=$(find /usr/lib -name 'libcuda.so.1' ... | head -1)
        LD_LIBRARY_PATH="$(dirname "$_libcuda_path")" cuda-driver-version

    which fails OPEN. LD_LIBRARY_PATH is a search hint: if the named directory
    yields nothing loadable the loader continues to the ld.so cache — on a
    second boot, the previous boot's `0-compat-cuda.conf`. The two copies also
    drifted, so base/60-gpu-cuda agreed with the boot script rather than
    checking it, and a review had to catch by hand what a rule should catch.

    `cuda-driver-version --native` dlopens an absolute path (a name containing
    "/" performs no search at all) and then verifies from /proc/self/maps which
    file was actually mapped, refusing rather than guessing. One implementation,
    tested in both directions by tools/imagegen/tests/test_cuda_driver_version.py.
    """
    if img.cls != "base":
        return
    for f, rel, lines in _shipped_scripts(repo):
        if f.name == _CUDA_HELPER:
            continue                           # the one sanctioned implementation
        for n, line in lines:
            if _LDPATH_PROBE.search(line):
                yield Finding("L064", ERROR, img.name, f"{rel}:{n}",
                              "wraps cuda-driver-version in LD_LIBRARY_PATH — that is a "
                              "search hint, not a pin, and falls through to a compat "
                              "libcuda; use `cuda-driver-version --native`")
            elif _LIBCUDA_SEARCH.search(line):
                yield Finding("L064", ERROR, img.name, f"{rel}:{n}",
                              "searches for libcuda.so.1 to pick a native driver library — "
                              "use `cuda-driver-version --native`, which resolves it once "
                              "and verifies what the loader actually mapped")


def check_template_disk_floor(img: Image, repo: Path) -> Iterable[Finding]:
    """L058 — a QA template's disk floor must FILTER offers, not just size the request.

    NOT about image size. On Vast the container image is stored separately and is not
    charged to the instance; `recommended_disk_space` requests the additional
    (overlayfs) space for what the instance writes. A large -devel image therefore
    does not need a large disk request, and an offer with less disk than the image is
    not thereby unable to run it.

    The failure this prevents is mechanical: the client requests that disk at create
    time and rejects any instance that comes up with less than the full request
    (DISK_TOLERANCE). With no matching search floor, offers are never filtered on
    available disk, so the client rents a box that cannot satisfy the request and
    discovers it only after launch — spending one of its bounded launch attempts.

    Measured on the base-qa filters when this rule was written: 31 of 773 admitted
    offers (4%) had less disk than the request, the smallest having 9 GB.

    Format AND adequacy. A floor below the request is the subtle case: it looks like
    the axis is covered while silently readmitting exactly those offers.

    Scoped to the base image for now, on the same reasoning as L057: comfyui-qa (40 GB)
    and vllm-qa (32 GB) have the identical hole, but widening the rule to
    them turns two live, currently-passing gates red and changes which hosts they
    select — a linter rule is not the place to do that quietly. Widen once each has
    been re-validated.
    """
    if img.cls != "base":
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
            if not isinstance(entry, dict):
                continue
            want = entry.get("recommended_disk_space")
            if not _is_number(want):
                continue                   # nothing declared, nothing to enforce
            ef = entry.get("extra_filters")
            spec = ef.get("disk_space") if isinstance(ef, dict) else None
            floor = None
            if isinstance(spec, dict):
                for op in ("gte", "gt", "eq"):
                    if op in spec and _is_number(spec[op]):
                        floor = float(spec[op])
                        break
            elif _is_number(spec):
                floor = float(spec)
            if floor is None:
                yield Finding("L058", ERROR, img.name, rel,
                              f"recommended_disk_space={want} GB but extra_filters has no "
                              f"numeric disk_space floor — offers are not filtered on disk, so a "
                              f"box that cannot satisfy the request is selectable and only "
                              f"fails after launch")
            elif floor < float(want):
                yield Finding("L058", ERROR, img.name, rel,
                              f"extra_filters.disk_space floor {floor} GB is below "
                              f"recommended_disk_space={want} GB — it admits boxes that cannot "
                              f"satisfy what the client then requests")


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


def check_llama_cuda_assert(img: Image) -> Iterable[Finding]:
    """L056 — an image that source-builds Unsloth Studio's bundled llama.cpp via
    `unsloth studio setup` MUST also assert the CUDA backend artifact exists.

    The studio's setup.sh gates `-DGGML_CUDA=ON` on a RUNTIME GPU probe
    (`nvidia-smi -L` / `/proc/driver/nvidia/gpus`). Inside `docker build` there is
    no GPU, so the probe fails and the build silently falls through to a CPU-only
    llama.cpp (only `libggml-cpu-*.so`, no `libggml-cuda.so`). Nothing fails, so the
    image ships and every runtime inference offloads to CPU. The fix forces the CUDA
    build; the durable guard is a post-build `test -f …/libggml-cuda.so` that fails
    the build when the backend is missing. Detected instruction-aware on `code_text`,
    so a commented-out example does not satisfy the requirement.

    Trigger: a real RUN invokes `unsloth studio setup`. Requirement: a real
    file-existence assertion on the artifact — `test -f …libggml-cuda.so` or a
    `[ -f …libggml-cuda.so ]` / `[[ -f …libggml-cuda.so ]]` test. A bare mention
    of the filename (e.g. `echo libggml-cuda.so`) does NOT satisfy it: it would
    leave a CPU-only build lint-clean, defeating the rule's purpose."""
    code = code_text(parse(img.text))
    if not re.search(r"\bunsloth\s+studio\s+setup\b", code):
        return
    # Require an actual `-f` existence test on the artifact, not just the substring.
    if not re.search(r"(?:\btest|\[\[?)\s+-f\s+\S*libggml-cuda\.so", code):
        yield Finding("L056", ERROR, img.name, "Dockerfile",
                      "source-builds Unsloth Studio's llama.cpp (`unsloth studio setup`) but has no "
                      "post-build existence assertion for the CUDA backend — the GPU-less docker build "
                      "silently produces a CPU-only binary; add a `test -f …/libggml-cuda.so || exit 1` "
                      "guard (a bare mention of the filename does not count) (ADR 0016)")


IMAGE_CHECKS: list[Callable[[Image], Iterable[Finding]]] = [
    check_labels, check_env_hash, check_copy_root, check_from_class, check_base_pin,
    check_torch_guard, check_no_auto_backend, check_uv_pip,
    check_conf_triple, check_util_order, check_supervisor_executable,
    check_external_env, check_llama_cuda_assert,
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
    out.extend(check_template_require_pass(img, repo))
    out.extend(check_instance_tests_executable(img, repo))
    out.extend(check_required_tests_can_fail(img, repo))
    out.extend(check_fail_later_is_reported(img, repo))
    out.extend(check_no_nvidia_smi_text_parse(img, repo))
    out.extend(check_no_open_coded_native_libcuda(img, repo))
    out.extend(check_template_disk_floor(img, repo))
    out.extend(check_launch_link_placeholder(img))
    out.extend(check_no_baked_weights(img))
    if apply_exceptions:
        out = [f for f in out if not _suppressed(img.name, f)]
    return out
