"""Assemble the capability manifest from declarative fragments + live state.

Data tiers (see the design plan):

* **Static fragments** — ``/etc/vast_capabilities.d/*.yaml`` baked into the image
  by the base and each derivative (``COPY ./ROOT /``). They declare tools, python
  environments, and the OpenAI-endpoint hints for the services a derivative runs.
* **Boot-resolved** — env-derived values that are stable for the session
  (instance identity, scheme, auth model, workspace).
* **Lazy / live** — services (from ``/etc/portal.yaml`` or ``PORTAL_CONFIG``),
  supervisor process states, GPU/metrics, provisioning status. The live portal
  endpoint injects ``processes`` and ``metrics`` it has already collected; the
  static snapshot omits them.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Iterable, Optional

import yaml

SCHEMA_VERSION = 1

FRAGMENTS_DIR = "/etc/vast_capabilities.d"
PORTAL_YAML = "/etc/portal.yaml"

# Env var conventions the provisioner understands, surfaced so an agent knows
# how to ask for more dependencies without reading our source.
PROVISIONING_ENV_CONVENTIONS = [
    "PROVISIONING_MANIFEST",
    "PROVISIONING_SCRIPT",
    "PROVISIONING_APT",
    "PROVISIONING_PIP",
    "PROVISIONING_CONDA",
    "PROVISIONING_GIT_REPOS",
    "PROVISIONING_DOWNLOADS",
    "PROVISIONING_POST_COMMANDS",
]


# --------------------------------------------------------------------------- #
# Sources                                                                     #
# --------------------------------------------------------------------------- #

def _scheme() -> str:
    return "https" if os.environ.get("ENABLE_HTTPS", "false").lower() == "true" else "http"


def _workspace_is_volume() -> bool:
    """True only when ${WORKSPACE} is a distinct mount (a host volume).

    Otherwise ${WORKSPACE} is ordinary container storage — it survives a
    stop/start but NOT a recycle/destroy. Agents must not assume it persists.
    """
    ws = os.environ.get("WORKSPACE", "/workspace")
    try:
        return os.path.isdir(ws) and os.stat("/").st_dev != os.stat(ws).st_dev
    except OSError:
        return False


def _ldconfig_libs() -> set:
    """Shared-library sonames visible to the dynamic linker (ldconfig cache +
    LD_LIBRARY_PATH) — used to detect which NVIDIA driver libs are present."""
    names: set = set()
    try:
        out = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            tok = line.strip().split(" ", 1)[0]
            if ".so" in tok:
                names.add(tok)
    except Exception:
        pass
    for d in os.environ.get("LD_LIBRARY_PATH", "").split(":"):
        if d and os.path.isdir(d):
            try:
                names.update(os.listdir(d))
            except OSError:
                pass
    return names


def _has_nvidia_vulkan_icd() -> bool:
    for d in ("/etc/vulkan/icd.d", "/usr/share/vulkan/icd.d", "/opt/nvidia-drivers/lib64"):
        try:
            if any("nvidia" in f.lower() and f.endswith(".json") for f in os.listdir(d)):
                return True
        except OSError:
            pass
    return False


def _gpu_render_caps() -> Optional[dict]:
    """Whether the NVIDIA graphics/render userspace libs are present.

    Some Vast hosts install only the *compute* driver, so CUDA works but the
    OpenGL/OptiX/EGL/Vulkan libs are missing (undetectable before renting).
    Returns None when no NVIDIA driver is present (nothing to report).
    """
    if not os.path.exists("/proc/driver/nvidia/version"):
        return None
    libs = _ldconfig_libs()
    caps: dict = {
        "gl": "libGLX_nvidia.so.0" in libs,
        "optix": "libnvoptix.so.1" in libs,
        "vulkan": _has_nvidia_vulkan_icd(),
    }
    if not all((caps["gl"], caps["optix"], caps["vulkan"])):
        caps["note"] = (
            "Some graphics/render libs are missing — common on Vast hosts that "
            "install only the compute driver (CUDA compute still works)."
        )
        caps["fix"] = "run 'install-display-drivers' to download+extract the matching driver libs"
    return caps


def _driver_version() -> Optional[str]:
    try:
        with open("/proc/driver/nvidia/version") as f:
            for tok in f.readline().split():
                if re.match(r"^\d+\.\d+(\.\d+)?$", tok):
                    return tok
    except Exception:
        pass
    return None


def _min_cuda_for_compute_cap(caps: list[str]) -> Optional[str]:
    """Minimum CUDA toolkit/wheel version a GPU of this compute capability needs.

    Blackwell (sm_10.0 / sm_12.0) and newer require CUDA >= 12.8 — older framework
    builds (e.g. torch cu124) lack kernels for the architecture and fail at runtime.
    Returns None when current/older wheels are fine (or caps unparseable).
    """
    try:
        if caps and max(float(c) for c in caps) >= 10.0:
            return "12.8"
    except ValueError:
        pass
    return None


def _parse_cuda_version_from_path(path: str) -> Optional[str]:
    """Extract a CUDA version from a path like '/usr/local/cuda-12.9'."""
    m = re.search(r"cuda-(\d+(?:\.\d+)?)", path or "")
    return m.group(1) if m else None


def _cuda_home() -> str:
    return os.environ.get("CUDA_HOME") or "/usr/local/cuda"


# CUDA components an agent commonly needs, mapped to the soname prefix that proves
# the lib is on the loader path. nvcc (a binary) + dev headers are probed directly.
# Detected precisely because images vary wildly: the 'mini'/runtime base ships only
# a curated subset (nvcc + cudart + nvrtc + cuFFT/NPP/nvJPEG/NCCL, but NOT cuBLAS/
# cuDNN/cuSPARSE/cuSOLVER/cuRAND); a full nvidia/cuda-devel-derived image ships all;
# the bare 'stock' image ships none. Never assume one exists because another does.
_CUDA_LIB_COMPONENTS = {
    "cudart": "libcudart.so",
    "nvrtc": "libnvrtc.so",
    "cublas": "libcublas.so",
    "cudnn": "libcudnn.so",
    "cufft": "libcufft.so",
    "cusparse": "libcusparse.so",
    "cusolver": "libcusolver.so",
    "curand": "libcurand.so",
    "nccl": "libnccl.so",
    "npp": "libnppc.so",
    "nvjpeg": "libnvjpeg.so",
}


def _cuda_components(libs: set) -> dict:
    """Precise inventory of which CUDA components are actually present.

    `libs` is the set of sonames on the loader path (from _ldconfig_libs()). An
    agent must not assume a library exists just because nvcc does, so each piece
    is reported individually; `dev_headers` indicates the cudart headers needed to
    compile. Reflects whatever the image actually carries (stock/mini/full).
    """
    home = _cuda_home()
    nvcc = shutil.which("nvcc") or (
        os.path.join(home, "bin", "nvcc")
        if os.path.exists(os.path.join(home, "bin", "nvcc")) else None
    )
    comps = {
        "nvcc": bool(nvcc),
        "dev_headers": os.path.exists(os.path.join(home, "include", "cuda_runtime.h")),
    }
    for name, prefix in _CUDA_LIB_COMPONENTS.items():
        comps[name] = any(l.startswith(prefix) for l in libs)
    return comps


def _cuda_installed_version() -> Optional[str]:
    """Version of the locally-installed CUDA components (e.g. '12.9'), if any.

    From the /usr/local/cuda -> cuda-X.Y symlink (version.json is often absent on
    the partial mini install), falling back to nvcc.
    """
    home = _cuda_home()
    if os.path.isdir(home):
        ver = _parse_cuda_version_from_path(os.path.realpath(home))
        if ver:
            return ver
    if shutil.which("nvcc"):
        try:
            out = subprocess.run(["nvcc", "--version"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"release (\d+\.\d+)", out)
            if m:
                return m.group(1)
        except Exception:
            pass
    return None


# The boot script (05-configure-cuda.sh) enables forward compat by registering the
# compat dir with the dynamic linker here (NOT via LD_LIBRARY_PATH).
_COMPAT_LDCONF = "/etc/ld.so.conf.d/0-compat-cuda.conf"


def _cuda_forward_compat() -> Optional[dict]:
    """CUDA forward-compatibility driver libs (the cuda-compat-* package), if present.

    These ship a userspace libcuda that lets a NEWER CUDA *major* version run on an
    OLDER host driver — and only on datacenter (Volta+) GPUs. This image AUTO-MANAGES
    them at boot: 05-configure-cuda.sh enables them (by writing the compat dir into
    ldconfig) only when an installed toolkit's CUDA major exceeds what the host driver
    supports AND cuInit succeeds; otherwise it leaves them off and the system relies on
    CUDA minor-version compatibility. So this is normally not something an agent touches.
    """
    compat = os.path.join(_cuda_home(), "compat")
    if not os.path.isdir(compat):
        return None
    ver = None
    try:
        for name in sorted(os.listdir(compat)):
            m = re.match(r"libcuda\.so\.(\d+\.\d+\.\d+)$", name)
            if m:
                ver = m.group(1)
                break
    except OSError:
        return None
    if not ver:
        return None
    # Enabled = boot script registered it with ldconfig, or a manual LD_LIBRARY_PATH.
    enabled = False
    try:
        if os.path.exists(_COMPAT_LDCONF) and "compat" in open(_COMPAT_LDCONF).read():
            enabled = True
    except OSError:
        pass
    if compat in os.environ.get("LD_LIBRARY_PATH", "").split(":"):
        enabled = True
    return {
        "available": True,
        "enabled": enabled,
        "path": compat,
        "compat_driver_version": ver,
        "host_driver_version": _driver_version(),
        "note": (
            "CUDA forward-compatibility libs. They let a NEWER CUDA MAJOR version run "
            "on an OLDER host driver, on datacenter (Volta+) GPUs only. This image "
            "auto-enables them at boot (via ldconfig) when needed and leaves them off "
            "otherwise — `enabled` is the current state. You normally never toggle "
            "this by hand; do NOT force them on when the host driver is new enough "
            "(set DISABLE_FORWARD_COMPAT=true at launch to opt out). For same-major "
            "mismatches (e.g. CUDA 12.x app on a 12.y driver) no compat libs are "
            "needed at all — see minor-version compatibility in the cuda note."
        ),
    }


def _cuda_info() -> Optional[dict]:
    """CUDA context for an agent: host driver version, the max CUDA the driver
    supports, the GPU compute capability, a precise inventory of which CUDA
    components are actually installed, and any forward-compatibility libs.

    Returns None without an NVIDIA driver. Critical because images vary: the
    'stock' image ships no CUDA at all (relies on the host-injected driver), the
    'mini'/runtime base ships a curated *subset* (nvcc + cudart but not cuBLAS/
    cuDNN), and full nvidia/cuda-devel-derived images ship everything. Installing
    the wrong CUDA/driver packages from the (configured) nvidia apt repo breaks CUDA.
    """
    if not os.path.exists("/proc/driver/nvidia/version"):
        return None
    libs = _ldconfig_libs()
    components = _cuda_components(libs)
    info: dict = {
        "driver_version": _driver_version(),
        "components": components,
    }
    version = _cuda_installed_version()
    if version:
        info["installed_version"] = version
    if os.path.isdir(_cuda_home()):
        info["cuda_home"] = _cuda_home()
    compat = _cuda_forward_compat()
    if compat:
        info["forward_compat"] = compat
    try:  # max CUDA the host driver supports (best-effort)
        out = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"CUDA Version:\s*([0-9.]+)", out)
        if m:
            info["driver_max_cuda"] = m.group(1)
    except Exception:
        pass
    try:  # GPU compute capability (e.g. "10.0"/"12.0" = Blackwell) — best-effort
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        caps = sorted({c.strip() for c in out.splitlines() if c.strip()})
        if caps:
            info["compute_capability"] = caps[0] if len(caps) == 1 else caps
            # Blackwell (sm_10.0/sm_12.0) and newer need CUDA >= 12.8 framework builds;
            # older cuXX wheels (e.g. torch cu124) lack kernels and fail at runtime.
            min_cuda = _min_cuda_for_compute_cap(caps)
            if min_cuda:
                info["min_cuda_for_wheels"] = min_cuda
    except Exception:
        pass
    present = [k for k, v in components.items() if v]
    info["note"] = (
        "CUDA compute works via the host-injected driver. "
        + ((f"A PARTIAL CUDA {version or ''} install is present (components: "
            + ", ".join(present) + "); this is NOT a full toolkit, so check the "
            "`components` map before assuming a library exists — missing ones "
            "(e.g. cuBLAS/cuDNN) must be added from the configured nvidia apt repo "
            "(cuda-<lib>-X-Y / libcudnn*) or come bundled with framework wheels. ")
           if any(components.values()) else
           "No CUDA components are installed; the nvidia apt repo is configured if "
           "you need nvcc/libs. ")
        + "COMPATIBILITY (agents get this wrong): the toolkit/wheel does NOT need to "
        "match the driver's exact CUDA version. CUDA minor-version compatibility means "
        "any CUDA 12.x build runs on any driver that supports CUDA 12.0+ (and 13.x on "
        "a 13.0+ driver) — so a 12.x wheel is fine here as long as driver_max_cuda is "
        ">= 12.0; do NOT 'upgrade the driver to match CUDA'. CAVEAT: this excludes PTX "
        "JIT — code shipped as PTX from a toolkit NEWER than the host driver fails "
        "with CUDA_ERROR_UNSUPPORTED_PTX_VERSION (222) since the driver owns the JIT "
        "(only bites when the host driver is older than the build; use native-cubin "
        "builds or forward compat). Forward compatibility "
        "(running a newer CUDA MAJOR on an older driver) is the separate cuda-compat "
        "mechanism, auto-managed at boot (see forward_compat). What DOES bind is the "
        "GPU architecture: compute_capability >= 10.0 (Blackwell) needs CUDA >= 12.8 "
        "wheels — an older build (e.g. torch cu124) installs cleanly but fails at "
        "runtime with 'no kernel image is available'. "
        + "When installing CUDA libs: NEVER install the NVIDIA driver from apt (it "
        "must match the host — avoid the 'cuda' metapackage and nvidia-driver-*/"
        "libcuda* packages); install only components <= driver_max_cuda, and prefer "
        "framework wheels that bundle their own CUDA runtime. See AGENTS.md."
    )
    return info


_PORT_RE = re.compile(r"^VAST_(TCP|UDP)_PORT_(\d+)$")


# A configured container port above the max bindable port (request convention:
# > 70000) is "self-mapped": the Vast engine picks a random valid external port and
# forwards it 1:1 to the SAME number inside the container, so the app's bind port
# equals its public port (it can thus advertise its own external port). Normal ports
# (<= 65535) NAT a different external number to the configured container port.
_SELF_MAP_MAX = 65535


def _open_ports() -> list[dict]:
    """Externally reachable ports, from the VAST_TCP/UDP_PORT_* env vars.

    These are fixed when the instance is created and cannot be added at runtime,
    so this is the definitive list of what an agent has to work with. Each entry
    maps an in-container port to the public host port that reaches it.
    """
    out = []
    for key, val in os.environ.items():
        m = _PORT_RE.match(key)
        # Ignore unset or non-numeric mapped ports (would yield invalid URLs).
        if not m or not val.isdigit():
            continue
        cp = int(m.group(2))
        entry = {
            "proto": m.group(1).lower(),
            "container_port": cp,
            "public_port": val,
        }
        if cp > _SELF_MAP_MAX:
            # Self-mapped: bind INSIDE the container to public_port, not container_port.
            entry["self_mapped"] = True
            entry["bind_port"] = int(val)
            entry["note"] = (
                f"Self-mapped port: bind your app inside the container to {val} — that "
                "is also its public port (so the app knows its own external port). "
                "Direct TCP forward, NOT behind the Caddy auth edge; add your own auth "
                "if it needs protecting."
            )
        out.append(entry)
    out.sort(key=lambda e: (e["proto"], e["container_port"]))
    return out


def _listening_ports() -> set:
    """TCP ports currently in LISTEN state (from /proc/net/tcp[6]).

    Used to tell an agent which open external ports are already occupied vs free
    to map its own app onto.
    """
    ports: set = set()
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path) as f:
                next(f, None)  # header
                for line in f:
                    parts = line.split()
                    if len(parts) < 4 or parts[3] != "0A":  # 0A = LISTEN
                        continue
                    try:
                        ports.add(int(parts[1].rsplit(":", 1)[1], 16))
                    except (ValueError, IndexError):
                        pass
        except OSError:
            pass
    return ports


def _credentials() -> dict:
    """Which credentials are configured — presence only, values never exposed."""
    def present(*names) -> bool:
        return any(os.environ.get(n) for n in names)
    rclone = os.path.isfile(os.path.expanduser("~/.config/rclone/rclone.conf")) \
        or bool(os.environ.get("RCLONE_CONFIG"))
    return {
        "huggingface": present("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"),  # gated model pulls
        "civitai": present("CIVITAI_TOKEN"),
        "rclone_configured": rclone,
        "note": "presence only; token values are not exposed",
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_portal_config(raw: str) -> list[dict]:
    """Parse a ``PORTAL_CONFIG`` string into service dicts.

    Mirrors ``caddy_config_manager`` so the static snapshot (which only has the
    env var, not the generated ``/etc/portal.yaml``) sees the same services.
    Format per entry: ``hostname:external_port:internal_port:open_path:name``.
    """
    services: list[dict] = []
    for entry in (raw or "").split("|"):
        entry = entry.strip()
        if not entry:
            continue
        try:
            hostname, ext_port, int_port, path, name = entry.split(":", 4)
            services.append({
                "name": name,
                "hostname": hostname,
                "external_port": int(ext_port),
                "internal_port": int(int_port),
                "open_path": path,
            })
        except ValueError:
            # Malformed entry — skip rather than fail the whole manifest.
            continue
    return services


def _read_portal_yaml() -> Optional[list[dict]]:
    """Read services from the generated ``/etc/portal.yaml`` (non-blocking)."""
    if not os.path.isfile(PORTAL_YAML):
        return None
    try:
        with open(PORTAL_YAML) as f:
            data = yaml.safe_load(f) or {}
        apps = data.get("applications", {}) or {}
        out = []
        for name, app in apps.items():
            out.append({
                "name": app.get("name", name),
                "hostname": app.get("hostname", "localhost"),
                "external_port": app.get("external_port"),
                "internal_port": app.get("internal_port"),
                "open_path": app.get("open_path", "/"),
            })
        return out
    except Exception:
        return None


def _services_source() -> list[dict]:
    """Prefer the generated portal.yaml; fall back to the PORTAL_CONFIG env."""
    svc = _read_portal_yaml()
    if svc is not None:
        return svc
    return parse_portal_config(os.environ.get("PORTAL_CONFIG", ""))


def load_fragments(fragments_dir: str = FRAGMENTS_DIR) -> dict:
    """Merge every ``*.yaml`` fragment (sorted) into one declaration block.

    Lists are concatenated; ``python_environments`` are merged by name so a
    derivative can add ``packages_of_interest`` to an env the base defined.
    """
    merged: dict = {
        "tools": [],
        "python_environments": [],
        "openai_endpoints": [],
        "image": {},
    }
    envs_by_name: dict[str, dict] = {}

    for path in sorted(glob.glob(os.path.join(fragments_dir, "*.yaml"))):
        try:
            with open(path) as f:
                frag = yaml.safe_load(f) or {}
        except Exception:
            continue
        if not isinstance(frag, dict):
            continue

        # Image identity: base sets universally-true fields (repo, README pointer);
        # a derivative's fragment overrides individual keys to add its specifics.
        img = frag.get("image")
        if isinstance(img, dict):
            merged["image"].update(img)
        for tool in frag.get("tools", []) or []:
            merged["tools"].append(tool)
        for ep in frag.get("openai_endpoints", []) or []:
            merged["openai_endpoints"].append(ep)
        for env in frag.get("python_environments", []) or []:
            name = env.get("name") or env.get("path")
            if not name:
                continue
            if name in envs_by_name:
                existing = envs_by_name[name]
                poi = list(dict.fromkeys(
                    (existing.get("packages_of_interest") or [])
                    + (env.get("packages_of_interest") or [])
                ))
                existing.update({k: v for k, v in env.items() if k != "packages_of_interest"})
                if poi:
                    existing["packages_of_interest"] = poi
            else:
                envs_by_name[name] = dict(env)

    merged["python_environments"] = list(envs_by_name.values())
    return merged


def _agent_guides() -> dict:
    """The agent-guide files baked into this image.

    Surfaced in the manifest (not just a bare directory path) so an agent that
    reads the JSON sees exactly which guides exist and is told to read them —
    including the per-image files (pytorch.md, comfyui.md, …) it would otherwise
    never discover. base.md is listed first; the rest follow sorted.
    """
    files = sorted(glob.glob("/etc/vast_agents/*.md"))
    base = "/etc/vast_agents/base.md"
    if base in files:
        files = [base] + [f for f in files if f != base]
    return {
        "combined": "/etc/vast-agents-guide.md",
        "dir": "/etc/vast_agents/",
        "files": files,
        "instruction": (
            "Read the combined guide (or every file in 'files') before acting on "
            "this instance. They are cumulative: the per-image guides document "
            "services and APIs this image adds that you will otherwise miss."
        ),
    }


def _detect_env_kind(path: str) -> Optional[str]:
    """Detect a python environment's kind by inspecting it on disk.

    `/venv/main` is a conda env in the base image but a plain uv-created venv in
    converted external images (see tools/convert-non-vast-image.sh), so the
    static fragment must not hardcode this — we resolve it at request time.
    """
    if not path:
        return None
    if os.path.isdir(os.path.join(path, "conda-meta")):
        return "conda"
    if os.path.isfile(os.path.join(path, "pyvenv.cfg")):
        return "venv"
    return None


def _probe_packages(venv_path: str, names: Iterable[str]) -> dict:
    """Return ``{name: version}`` for installed packages in ``venv_path``.

    Runs the target venv's own python so we see *that* environment's packages
    (the portal runs in a different venv). Best-effort, time-boxed; only called
    when the caller opts in via ``include=['packages']``.
    """
    names = [n for n in names if n]
    if not names:
        return {}
    py = os.path.join(venv_path, "bin", "python")
    if not os.path.isfile(py):
        return {}
    script = (
        "import json,sys\n"
        "from importlib.metadata import version, PackageNotFoundError\n"
        "out={}\n"
        "for n in sys.argv[1:]:\n"
        "    try: out[n]=version(n)\n"
        "    except PackageNotFoundError: out[n]=None\n"
        "    except Exception: out[n]=None\n"
        "print(json.dumps(out))\n"
    )
    try:
        res = subprocess.run(
            [py, "-c", script, *names],
            capture_output=True, text=True, timeout=20,
        )
        if res.returncode == 0:
            return json.loads(res.stdout)
    except Exception:
        pass
    return {}


# --------------------------------------------------------------------------- #
# Matching helpers                                                            #
# --------------------------------------------------------------------------- #

def _normalize(s: str) -> str:
    """Lowercase and strip separators so 'instance_portal' matches 'Instance Portal'."""
    return "".join(c for c in (s or "").lower() if c.isalnum())


def _launch_has_jupyter() -> bool:
    """True when /.launch selects Jupyter (Vast manages it, not supervisor)."""
    try:
        with open("/.launch") as f:
            return "jupyter" in f.read().lower()
    except Exception:
        return False


def _match_process(service: dict, processes: list[dict]) -> Optional[dict]:
    """Match a service to a supervisor process by name / search term.

    Uses the same substring convention as the provisioner's
    ``portal_search_term`` and ``exit_portal.sh``: a process whose name appears
    in (or contains) the service label, compared with separators normalised
    (``instance_portal`` <-> ``Instance Portal``).
    """
    label = _normalize(service.get("name"))
    if not label:
        return None
    for proc in processes:
        pname = _normalize(proc.get("name"))
        if not pname:
            continue
        if pname == label or pname in label or label in pname:
            return proc
    return None


def _auth_descriptor() -> dict:
    enabled = os.environ.get("ENABLE_AUTH", "true").lower() == "true"
    excluded = [
        p.strip() for p in os.environ.get("AUTH_EXCLUDE", "").split(",") if p.strip()
    ]
    label = os.environ.get("VAST_CONTAINERLABEL", "")
    return {
        "edge": "caddy",
        "enabled": enabled,
        # Token precedence: either is accepted by the Caddy edge.
        "token_env": ["OPEN_BUTTON_TOKEN", "WEB_PASSWORD"],
        "methods": ["bearer", "cookie", "query"],
        "bearer_header": "Authorization: Bearer <token>",
        "cookie_name": f"{label}_auth_token" if label else None,
        "query_param": "token",
        "excluded_ports": excluded,
        "note": (
            "localhost requests to the service's internal port bypass the edge "
            "and need no token; external requests go through Caddy and require one."
        ),
    }


# --------------------------------------------------------------------------- #
# Assembly                                                                    #
# --------------------------------------------------------------------------- #

def assemble(
    *,
    services: list[dict],
    fragments: Optional[dict] = None,
    processes: Optional[list[dict]] = None,
    metrics: Optional[dict] = None,
    gpu: Optional[str] = None,
    include: Iterable[str] = (),
) -> dict:
    """Compose the manifest. Pure: all live data is passed in.

    ``include`` may contain ``"packages"`` (probe package versions) and
    ``"metrics"`` (the caller-supplied ``metrics`` dict is attached).
    """
    include = set(include or ())
    fragments = fragments if fragments is not None else load_fragments()
    processes = processes or []

    scheme = _scheme()
    public_ip = os.environ.get("PUBLIC_IPADDR", "")

    # --- services ---
    ep_hints = {e.get("service"): e for e in fragments.get("openai_endpoints", []) if e.get("service")}
    svc_out: list[dict] = []
    openai_rollup: list[dict] = []
    for svc in services:
        ext = svc.get("external_port")
        open_path = svc.get("open_path", "/")
        mapped_port = os.environ.get(f"VAST_TCP_PORT_{ext}", "")
        entry = {
            "name": svc.get("name"),
            "hostname": svc.get("hostname", "localhost"),
            "internal_port": svc.get("internal_port"),
            "external_port": ext,
            "open_path": open_path,
            "mapped_port": mapped_port,
            "internal_url": f"http://localhost:{svc.get('internal_port')}",
            "direct_url": (
                f"{scheme}://{public_ip}:{mapped_port}{open_path}"
                if public_ip and mapped_port else None
            ),
        }
        proc = _match_process(svc, processes)
        entry["supervisor_process"] = proc.get("name") if proc else None
        entry["state"] = proc.get("state") if proc else "unknown"
        # Jupyter under /.launch is run by the Vast platform, not supervisor, so
        # it has no matching process — report that rather than a misleading "unknown".
        if entry["state"] == "unknown" and "jupyter" in _normalize(svc.get("name")) and _launch_has_jupyter():
            entry["state"] = "vast-managed"

        hint = ep_hints.get(svc.get("name"))
        if hint:
            base_path = hint.get("path", "/v1").rstrip("/")
            if entry["direct_url"]:
                base = f"{scheme}://{public_ip}:{mapped_port}{base_path}"
            else:
                base = None
            entry["openai_v1_base"] = base
            entry["capabilities"] = hint.get("capabilities", [])
            openai_rollup.append({
                "service": svc.get("name"),
                "base_url": base,
                "internal_base_url": f"http://localhost:{svc.get('internal_port')}{base_path}",
                "capabilities": hint.get("capabilities", []),
                "models_path": hint.get("models_path"),
                "auth": {"type": "bearer", "token_env": ["OPEN_BUTTON_TOKEN", "WEB_PASSWORD"]},
            })
        svc_out.append(entry)

    # --- python environments (+ optional package probing) ---
    py_envs = []
    for env in fragments.get("python_environments", []):
        env_out = dict(env)
        path = env.get("path")
        detected = _detect_env_kind(path)
        if detected:
            env_out["kind"] = detected
        # Keep packages_of_interest a stable list; put probed versions in a
        # separate package_versions map so the manifest shape doesn't change.
        poi = env.get("packages_of_interest") or []
        if "packages" in include and poi and path:
            env_out["package_versions"] = _probe_packages(path, poi)
        py_envs.append(env_out)

    # --- hardware ---
    hardware: dict = {"gpu": {"summary": gpu if gpu is not None else "unknown"}}
    render = _gpu_render_caps()
    if render is not None:
        hardware["gpu"]["render"] = render
    cuda = _cuda_info()
    if cuda is not None:
        hardware["gpu"]["cuda"] = cuda
    if "metrics" in include and metrics:
        if "gpu" in metrics:
            hardware["gpu"].update(metrics["gpu"])
        for key in ("cpu", "ram", "disk", "volume"):
            if key in metrics:
                hardware[key] = metrics[key]

    # --- provisioning ---
    prov_status = "none"
    if os.path.isfile("/.provisioning_complete"):
        prov_status = "complete"
    elif os.path.isfile("/.provisioning_failed"):
        prov_status = "failed"
    elif os.path.isfile("/.provisioning"):
        prov_status = "in_progress"

    image = dict(fragments.get("image") or {})
    image.setdefault("repo", "https://github.com/vast-ai/base-image")

    # Annotate open ports: occupied by a configured service, or actively
    # listening, => in_use; otherwise free for an agent to map its own app onto.
    listening = _listening_ports()
    svc_by_ext = {s["external_port"]: s["name"] for s in svc_out if s.get("external_port") is not None}
    open_ports = _open_ports()
    for p in open_ports:
        cp = p["container_port"]
        # Self-mapped ports are served by the app binding to public_port, not the
        # (unbindable) container_port — check the right one for occupancy.
        bind = p.get("bind_port", cp)
        svc_name = svc_by_ext.get(cp)
        p["in_use"] = (bind in listening) or (svc_name is not None)
        if svc_name:
            p["service"] = svc_name

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "image": image,
        "credentials": _credentials(),
        "instance": {
            "container_id": os.environ.get("CONTAINER_ID", ""),
            "containerlabel": os.environ.get("VAST_CONTAINERLABEL", ""),
            "workspace": os.environ.get("WORKSPACE", "/workspace"),
            # True only if ${WORKSPACE} is a real host volume (persists through
            # recycle/destroy). False = ordinary container storage, lost on
            # recycle/destroy — do not assume ${WORKSPACE} is durable.
            "workspace_is_volume": _workspace_is_volume(),
            "scheme": scheme,
            "public_ip": public_ip,
            # Fixed at instance creation; an agent can use these but cannot add
            # more. `in_use` flags which are occupied; pick a free one to expose
            # your own app.
            "open_ports": open_ports,
        },
        "hardware": hardware,
        "python_environments": py_envs,
        "tools": fragments.get("tools", []),
        "services": svc_out,
        "endpoints_openai": openai_rollup,
        "provisioning": {
            "status": prov_status,
            "how_to": (
                "Boot-time: set PROVISIONING_MANIFEST=<url> or the PROVISIONING_* "
                "env vars. Runtime: POST /capabilities/provision or run "
                "`provisioner <manifest.yaml>`."
            ),
            "env_conventions": PROVISIONING_ENV_CONVENTIONS,
            "log_file": "/var/log/portal/provisioning.log",
        },
        "auth": _auth_descriptor(),
        "discovery": {
            "capabilities_url": "/capabilities",
            "well_known_url": "/.well-known/vast-capabilities",
            "openapi_url": "/openapi.json",
            "snapshot_file": "/etc/vast_capabilities.json",
            "agents_guide": _agent_guides(),
        },
    }


def assemble_live(
    *,
    processes: Optional[list[dict]] = None,
    metrics: Optional[dict] = None,
    gpu: Optional[str] = None,
    include: Iterable[str] = (),
) -> dict:
    """Convenience for the portal: read services + fragments, attach live data."""
    return assemble(
        services=_services_source(),
        fragments=load_fragments(),
        processes=processes,
        metrics=metrics,
        gpu=gpu,
        include=include,
    )


def assemble_static() -> dict:
    """Static snapshot for the boot script / CLI — no supervisor or metrics.

    Service ``state`` is reported as ``unknown``; fetch ``/capabilities`` for
    live state. Safe to call without the portal app running.
    """
    return assemble(
        services=_services_source(),
        fragments=load_fragments(),
        processes=None,
        metrics=None,
        gpu=None,
        include=(),
    )


if __name__ == "__main__":
    # `python -m capabilities.manifest` prints the static snapshot.
    print(json.dumps(assemble_static(), indent=2))
