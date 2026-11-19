"""Unit tests for the capability manifest assembly (portal-aio/capabilities).

Run from the portal-aio directory (or repo root) with pytest; the module is put
on sys.path below so `capabilities` imports without installation.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capabilities import manifest
from capabilities.models import OpenAIEndpoint, ServiceInfo


# --- parse_portal_config ---------------------------------------------------- #

def test_parse_portal_config_basic():
    svc = manifest.parse_portal_config(
        "localhost:8000:18000:/docs:vLLM API|localhost:1111:11111:/:Instance Portal"
    )
    assert [s["name"] for s in svc] == ["vLLM API", "Instance Portal"]
    assert svc[0] == {
        "name": "vLLM API", "hostname": "localhost",
        "external_port": 8000, "internal_port": 18000, "open_path": "/docs",
    }


def test_parse_portal_config_skips_malformed_and_empty():
    svc = manifest.parse_portal_config("garbage|localhost:8000:18000:/:Good|")
    assert [s["name"] for s in svc] == ["Good"]
    assert manifest.parse_portal_config("") == []


# --- _detect_env_kind ------------------------------------------------------- #

def test_detect_env_kind_conda(tmp_path):
    (tmp_path / "conda-meta").mkdir()
    assert manifest._detect_env_kind(str(tmp_path)) == "conda"


def test_detect_env_kind_venv(tmp_path):
    (tmp_path / "pyvenv.cfg").write_text("home = /usr")
    assert manifest._detect_env_kind(str(tmp_path)) == "venv"


def test_detect_env_kind_none(tmp_path):
    assert manifest._detect_env_kind(str(tmp_path)) is None
    assert manifest._detect_env_kind("") is None


# --- _match_process (separator-insensitive) --------------------------------- #

def test_match_process_normalizes_separators():
    procs = [{"name": "instance_portal", "state": "RUNNING"}]
    assert manifest._match_process({"name": "Instance Portal"}, procs)["state"] == "RUNNING"


def test_match_process_substring_and_miss():
    procs = [{"name": "vllm", "state": "RUNNING"}]
    assert manifest._match_process({"name": "vLLM API"}, procs)["name"] == "vllm"
    assert manifest._match_process({"name": "Jupyter"}, procs) is None


# --- _open_ports ------------------------------------------------------------ #

def test_open_ports(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("VAST_TCP_PORT_", "VAST_UDP_PORT_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VAST_TCP_PORT_8000", "40080")
    monkeypatch.setenv("VAST_TCP_PORT_1111", "9956")
    monkeypatch.setenv("VAST_UDP_PORT_5000", "55000")
    monkeypatch.setenv("VAST_TCP_PORT_9999", "notaport")  # non-numeric value -> ignored
    ports = manifest._open_ports()
    assert {"proto": "tcp", "container_port": 1111, "public_port": "9956"} in ports
    assert {"proto": "tcp", "container_port": 8000, "public_port": "40080"} in ports
    assert {"proto": "udp", "container_port": 5000, "public_port": "55000"} in ports
    assert all(p["public_port"].isdigit() for p in ports)            # non-numeric value excluded
    assert all(p["container_port"] != 9999 for p in ports)
    assert ports == sorted(ports, key=lambda e: (e["proto"], e["container_port"]))


# --- load_fragments (merge semantics) --------------------------------------- #

def test_load_fragments_merges(tmp_path):
    (tmp_path / "10-base.yaml").write_text(
        "tools:\n  - name: git\n"
        "python_environments:\n  - name: main\n    path: /venv/main\n    packages_of_interest: [torch]\n"
        "openai_endpoints:\n  - service: A\n    path: /v1\n"
    )
    (tmp_path / "30-x.yaml").write_text(
        "tools:\n  - name: vllm\n"
        "python_environments:\n  - name: main\n    packages_of_interest: [vllm]\n"
        "openai_endpoints:\n  - service: B\n    path: /v1\n"
    )
    f = manifest.load_fragments(str(tmp_path))
    assert [t["name"] for t in f["tools"]] == ["git", "vllm"]
    assert {e["service"] for e in f["openai_endpoints"]} == {"A", "B"}
    envs = {e["name"]: e for e in f["python_environments"]}
    assert envs["main"]["packages_of_interest"] == ["torch", "vllm"]  # unioned


# --- assemble --------------------------------------------------------------- #

@pytest.fixture
def net_env(monkeypatch):
    monkeypatch.setenv("PUBLIC_IPADDR", "203.0.113.7")
    monkeypatch.setenv("VAST_TCP_PORT_8000", "40080")
    monkeypatch.delenv("ENABLE_HTTPS", raising=False)  # default -> http


def test_assemble_service_and_openai(net_env):
    frag = {
        "tools": [], "python_environments": [],
        "openai_endpoints": [{"service": "vLLM API", "path": "/v1",
                              "capabilities": ["chat"], "models_path": "/v1/models"}],
    }
    svc = [{"name": "vLLM API", "hostname": "localhost",
            "external_port": 8000, "internal_port": 18000, "open_path": "/docs"}]
    m = manifest.assemble(services=svc, fragments=frag,
                          processes=[{"name": "vllm", "state": "RUNNING"}])
    s = m["services"][0]
    assert s["mapped_port"] == "40080"
    assert s["direct_url"] == "http://203.0.113.7:40080/docs"
    assert s["state"] == "RUNNING"
    assert s["openai_v1_base"] == "http://203.0.113.7:40080/v1"
    assert m["endpoints_openai"][0]["base_url"] == "http://203.0.113.7:40080/v1"


def test_workspace_is_volume_reported(monkeypatch):
    # An ordinary directory (same filesystem as /) is not a volume.
    monkeypatch.setenv("WORKSPACE", "/tmp")
    m = manifest.assemble(services=[],
                          fragments={"tools": [], "python_environments": [], "openai_endpoints": []})
    assert m["instance"]["workspace_is_volume"] is False
    assert manifest._workspace_is_volume() is False


def test_image_identity_merge_and_default(tmp_path):
    # Base sets repo/readme; a derivative fragment overrides name/readme (per-key).
    (tmp_path / "10-base.yaml").write_text(
        "image:\n  repo: https://github.com/vast-ai/base-image\n  readme: REPO_README\n")
    (tmp_path / "30-x.yaml").write_text(
        "image:\n  name: PyTorch\n  readme: PYTORCH_README\n")
    frags = manifest.load_fragments(str(tmp_path))
    assert frags["image"] == {"repo": "https://github.com/vast-ai/base-image",
                              "name": "PyTorch", "readme": "PYTORCH_README"}
    m = manifest.assemble(services=[], fragments=frags)
    assert m["image"]["name"] == "PyTorch"
    # repo always present even with no fragments
    m2 = manifest.assemble(services=[], fragments={"tools": [], "python_environments": [], "openai_endpoints": []})
    assert m2["image"]["repo"] == "https://github.com/vast-ai/base-image"


def test_listening_ports_is_set():
    assert isinstance(manifest._listening_ports(), set)


def test_open_ports_self_mapped(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("VAST_TCP_PORT_", "VAST_UDP_PORT_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VAST_TCP_PORT_8080", "11725")   # normal: NAT to a different number
    monkeypatch.setenv("VAST_TCP_PORT_72299", "13058")  # >70000: self-mapped 1:1
    ports = {p["container_port"]: p for p in manifest._open_ports()}
    assert "self_mapped" not in ports[8080] and "bind_port" not in ports[8080]
    sm = ports[72299]
    assert sm["self_mapped"] is True
    assert sm["bind_port"] == 13058 and sm["public_port"] == "13058"
    assert "13058" in sm["note"]


def test_open_ports_self_mapped_in_use_checks_bind_port(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("VAST_TCP_PORT_", "VAST_UDP_PORT_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VAST_TCP_PORT_72299", "13058")
    # App listens on the bind_port (13058), NOT the container_port (72299).
    monkeypatch.setattr(manifest, "_listening_ports", lambda: {13058})
    m = manifest.assemble(services=[],
                          fragments={"tools": [], "python_environments": [], "openai_endpoints": []})
    p = {x["container_port"]: x for x in m["instance"]["open_ports"]}[72299]
    assert p["in_use"] is True            # detected via bind_port, not container_port


def test_open_ports_in_use_annotation(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("VAST_TCP_PORT_", "VAST_UDP_PORT_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VAST_TCP_PORT_8000", "40080")    # taken by a service
    monkeypatch.setenv("VAST_TCP_PORT_10100", "41100")   # spare
    monkeypatch.setattr(manifest, "_listening_ports", lambda: set())
    svc = [{"name": "vLLM API", "hostname": "localhost",
            "external_port": 8000, "internal_port": 18000, "open_path": "/"}]
    m = manifest.assemble(services=svc,
                          fragments={"tools": [], "python_environments": [], "openai_endpoints": []})
    ports = {p["container_port"]: p for p in m["instance"]["open_ports"]}
    assert ports[8000]["in_use"] is True and ports[8000]["service"] == "vLLM API"
    assert ports[10100]["in_use"] is False and "service" not in ports[10100]


def test_credentials_presence(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.setenv("CIVITAI_TOKEN", "x")
    c = manifest._credentials()
    assert c["huggingface"] is False and c["civitai"] is True and "note" in c
    m = manifest.assemble(services=[],
                          fragments={"tools": [], "python_environments": [], "openai_endpoints": []})
    assert "credentials" in m


def test_ldconfig_libs_is_set():
    assert isinstance(manifest._ldconfig_libs(), set)


# --- GPU presence / driver version ------------------------------------------ #
# Under gVisor the kernel module's /proc version file is absent while the GPU and
# nvidia-smi work, so neither presence nor the version may depend on that file.
# Every test pins the three sources the manifest may consult (the helper, the
# control node, nvidia-smi on PATH) so the host running the suite cannot leak in.

FAKE_SMI = """#!/bin/sh
case "$1" in
  --query-gpu=driver_version) printf '%s' "$SMI_DRIVER" ;;
  --query-gpu=compute_cap) printf '8.9\\n' ;;
  *) exit 9 ;;
esac
"""


@pytest.fixture
def gpu_env(tmp_path, monkeypatch):
    """No helper, no control node, an empty PATH; tests add what they need."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    monkeypatch.setattr(manifest, "_DRIVER_VERSION_HELPER", str(tmp_path / "no-helper"))
    monkeypatch.setattr(manifest, "_NVIDIACTL", str(tmp_path / "no-nvidiactl"))
    monkeypatch.setenv("PATH", str(bin_))

    def add(name, body):
        f = bin_ / name
        f.write_text(body)
        f.chmod(0o755)
        return f
    return add


def test_no_gpu_when_nothing_answers(gpu_env):
    assert manifest._driver_version() is None
    assert manifest._has_nvidia_gpu() is False
    assert manifest._gpu_render_caps() is None
    assert manifest._cuda_info() is None


def test_driver_version_from_helper(gpu_env, tmp_path, monkeypatch):
    helper = gpu_env("nvidia-driver-version", "#!/bin/sh\necho 580.95.05\n")
    monkeypatch.setattr(manifest, "_DRIVER_VERSION_HELPER", str(helper))
    gpu_env("nvidia-smi", "#!/bin/sh\necho 1.2.3\n")   # must not be consulted
    assert manifest._driver_version() == "580.95.05"


def test_driver_version_helper_found_on_path(gpu_env):
    gpu_env("nvidia-driver-version", "#!/bin/sh\necho 575.57.08\n")
    assert manifest._driver_version() == "575.57.08"


def test_driver_version_helper_silent_means_none(gpu_env, monkeypatch):
    # The helper already tried nvidia-smi; a refusal is the answer.
    helper = gpu_env("nvidia-driver-version", "#!/bin/sh\nexit 1\n")
    monkeypatch.setattr(manifest, "_DRIVER_VERSION_HELPER", str(helper))
    assert manifest._driver_version() is None


def test_driver_version_fallback_without_helper(gpu_env, monkeypatch):
    # An image built before the helper existed, running the latest portal.
    # Two GPUs: first line only, whitespace trimmed.
    gpu_env("nvidia-smi", FAKE_SMI)
    monkeypatch.setenv("SMI_DRIVER", " 580.95.05 \n580.95.05\n")
    assert manifest._driver_version() == "580.95.05"


@pytest.mark.parametrize("smi", [
    "#!/bin/sh\nexit 6\n",                                   # failing
    "#!/bin/sh\necho 580.95.05; exit 6\n",                   # failing despite output
    "#!/bin/sh\n",                                            # empty
    "#!/bin/sh\necho 'No devices were found'\n",             # not a version
])
def test_driver_version_fallback_refuses(gpu_env, smi):
    gpu_env("nvidia-smi", smi)
    assert manifest._driver_version() is None


def test_nvidiactl_alone_means_gpu(gpu_env, tmp_path, monkeypatch):
    ctl = tmp_path / "nvidiactl"
    ctl.write_text("")
    monkeypatch.setattr(manifest, "_NVIDIACTL", str(ctl))
    assert manifest._has_nvidia_gpu() is True
    assert manifest._gpu_render_caps() is not None


def test_gpu_section_reported_without_proc_file(gpu_env, monkeypatch):
    # The gVisor shape: no /proc version file, no helper, nvidia-smi answers.
    gpu_env("nvidia-smi", FAKE_SMI)
    monkeypatch.setenv("SMI_DRIVER", "580.95.05\n")
    m = manifest.assemble(services=[],
                          fragments={"tools": [], "python_environments": [], "openai_endpoints": []})
    gpu = m["hardware"]["gpu"]
    assert "render" in gpu
    assert gpu["cuda"]["driver_version"] == "580.95.05"
    assert gpu["cuda"]["compute_capability"] == "8.9"


def test_min_cuda_for_compute_cap():
    # Blackwell and newer (>= 10.0) need CUDA 12.8 wheels; older cards are fine.
    assert manifest._min_cuda_for_compute_cap(["12.0"]) == "12.8"   # RTX 50-series
    assert manifest._min_cuda_for_compute_cap(["10.0"]) == "12.8"   # B200
    assert manifest._min_cuda_for_compute_cap(["9.0"]) is None      # Hopper
    assert manifest._min_cuda_for_compute_cap(["8.6", "9.0"]) is None
    assert manifest._min_cuda_for_compute_cap(["8.0", "12.0"]) == "12.8"  # mixed -> max wins
    assert manifest._min_cuda_for_compute_cap([]) is None
    assert manifest._min_cuda_for_compute_cap(["weird"]) is None


def test_parse_cuda_version_from_path():
    assert manifest._parse_cuda_version_from_path("/usr/local/cuda-12.9") == "12.9"
    assert manifest._parse_cuda_version_from_path("/usr/local/cuda-13") == "13"
    assert manifest._parse_cuda_version_from_path("/usr/local/cuda") is None
    assert manifest._parse_cuda_version_from_path("") is None


def test_cuda_components_precise(tmp_path, monkeypatch):
    # Simulate the mini base: nvcc + headers + a subset of libs, NOT cuBLAS/cuDNN.
    home = tmp_path / "cuda"
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "nvcc").write_text("")
    (home / "include").mkdir()
    (home / "include" / "cuda_runtime.h").write_text("")
    monkeypatch.setenv("CUDA_HOME", str(home))
    monkeypatch.setenv("PATH", str(home / "bin") + ":" + os.environ.get("PATH", ""))
    libs = {"libcudart.so.12", "libnvrtc.so.12", "libcufft.so.11",
            "libnppc.so.12", "libnvjpeg.so.12", "libnccl.so.2"}
    c = manifest._cuda_components(libs)
    assert c["nvcc"] is True and c["dev_headers"] is True
    assert c["cudart"] is True and c["nvrtc"] is True and c["cufft"] is True
    assert c["npp"] is True and c["nvjpeg"] is True and c["nccl"] is True
    assert c["cublas"] is False and c["cudnn"] is False      # the key correction
    assert c["cusparse"] is False and c["cusolver"] is False and c["curand"] is False


def test_cuda_components_stock_empty(monkeypatch):
    monkeypatch.setenv("CUDA_HOME", "/nonexistent-cuda")
    monkeypatch.setattr(manifest.shutil, "which", lambda _: None)
    c = manifest._cuda_components(set())
    assert c["nvcc"] is False and c["dev_headers"] is False
    assert all(v is False for v in c.values())


def test_cuda_forward_compat_present_but_disabled(tmp_path, monkeypatch):
    # Compat libs present but the boot script did NOT enable them (no ldconfig
    # conf, empty LD_LIBRARY_PATH) -> available True, enabled False (the common
    # case: host driver already new enough).
    home = tmp_path / "cuda"
    (home / "compat").mkdir(parents=True)
    (home / "compat" / "libcuda.so.575.57.08").write_text("")
    (home / "compat" / "libcuda.so.1").symlink_to("libcuda.so.575.57.08")
    monkeypatch.setenv("CUDA_HOME", str(home))
    monkeypatch.setenv("LD_LIBRARY_PATH", "")
    monkeypatch.setattr(manifest, "_driver_version", lambda: "580.95.05")
    monkeypatch.setattr(manifest, "_COMPAT_LDCONF", str(tmp_path / "no-such.conf"))
    fc = manifest._cuda_forward_compat()
    assert fc["available"] is True
    assert fc["enabled"] is False
    assert fc["compat_driver_version"] == "575.57.08"
    assert fc["host_driver_version"] == "580.95.05"
    assert fc["path"].endswith("/compat")


def test_cuda_forward_compat_enabled_via_ldconfig(tmp_path, monkeypatch):
    # Boot script wrote the compat dir into ldconfig -> enabled True.
    home = tmp_path / "cuda"
    (home / "compat").mkdir(parents=True)
    (home / "compat" / "libcuda.so.560.35.03").write_text("")
    conf = tmp_path / "0-compat-cuda.conf"
    conf.write_text(str(home / "compat") + "\n")
    monkeypatch.setenv("CUDA_HOME", str(home))
    monkeypatch.setenv("LD_LIBRARY_PATH", "")
    monkeypatch.setattr(manifest, "_driver_version", lambda: "535.10.01")
    monkeypatch.setattr(manifest, "_COMPAT_LDCONF", str(conf))
    fc = manifest._cuda_forward_compat()
    assert fc["enabled"] is True


def test_cuda_forward_compat_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("CUDA_HOME", str(tmp_path / "nope"))
    assert manifest._cuda_forward_compat() is None


def test_assemble_null_direct_url_without_public_ip(monkeypatch):
    monkeypatch.delenv("PUBLIC_IPADDR", raising=False)
    monkeypatch.setenv("VAST_TCP_PORT_8000", "40080")
    svc = [{"name": "X", "hostname": "localhost",
            "external_port": 8000, "internal_port": 18000, "open_path": "/"}]
    m = manifest.assemble(services=svc,
                          fragments={"tools": [], "python_environments": [], "openai_endpoints": []})
    assert m["services"][0]["direct_url"] is None


def test_package_versions_shape(tmp_path):
    # include=packages keeps packages_of_interest a list, adds package_versions dict
    (tmp_path / "pyvenv.cfg").write_text("home=/usr")
    frag = {"tools": [], "openai_endpoints": [],
            "python_environments": [{"name": "main", "path": str(tmp_path),
                                     "packages_of_interest": ["pip"]}]}
    m = manifest.assemble(services=[], fragments=frag, include=["packages"])
    e = m["python_environments"][0]
    assert e["packages_of_interest"] == ["pip"]
    assert isinstance(e.get("package_versions"), dict)


# --- model parity guard ----------------------------------------------------- #
# Routes serialise these through response_model, so any assembled key missing
# from the model would be silently dropped. Catch that here.

def test_assembled_keys_covered_by_models(net_env):
    frag = {"tools": [], "python_environments": [],
            "openai_endpoints": [{"service": "S", "path": "/v1",
                                  "capabilities": ["chat"], "models_path": "/v1/models"}]}
    svc = [{"name": "S", "hostname": "localhost",
            "external_port": 8000, "internal_port": 18000, "open_path": "/"}]
    m = manifest.assemble(services=svc, fragments=frag, processes=[])
    assert set(m["services"][0]).issubset(set(ServiceInfo.model_fields))
    assert set(m["endpoints_openai"][0]).issubset(set(OpenAIEndpoint.model_fields))


# --- listener visibility: tcp6/udp6 tables absent (gVisor) ------------------- #

def _proc_net(tmp_path, monkeypatch, tables):
    d = tmp_path / "net"
    d.mkdir()
    header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid\n"
    for name in tables:
        (d / name).write_text(header)
    monkeypatch.setattr(manifest, "PROC_NET", str(d))


def _ports(monkeypatch, *env):
    for key in [k for k in os.environ if k.startswith(("VAST_TCP_PORT_", "VAST_UDP_PORT_"))]:
        monkeypatch.delenv(key)
    for key in env:
        monkeypatch.setenv(key, "40000")
    m = manifest.assemble(services=[], fragments={}, processes=[])
    return {(p["proto"], p["container_port"]): p for p in m["instance"]["open_ports"]}


def test_tcp6_absent_ipv6_listener_reported_in_use(tmp_path, monkeypatch):
    import socket
    _proc_net(tmp_path, monkeypatch, ["tcp", "udp"])
    try:
        srv = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        srv.bind(("::1", 0))
    except OSError:
        pytest.skip("no IPv6 loopback on this host")
    with srv:
        srv.listen(1)
        busy = srv.getsockname()[1]
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free = s.getsockname()[1]
        ports = _ports(monkeypatch, f"VAST_TCP_PORT_{busy}", f"VAST_TCP_PORT_{free}")
    assert ports[("tcp", busy)]["in_use"] is True
    assert ports[("tcp", free)]["in_use"] is False


def test_tcp6_present_never_probes(tmp_path, monkeypatch):
    _proc_net(tmp_path, monkeypatch, ["tcp", "tcp6", "udp", "udp6"])

    def boom(port):
        raise AssertionError("probed although tcp6 exists")

    monkeypatch.setattr(manifest, "_tcp_port_accepts", boom)
    ports = _ports(monkeypatch, "VAST_TCP_PORT_8080", "VAST_UDP_PORT_9000")
    assert ports[("tcp", 8080)]["in_use"] is False
    assert "in_use_verified" not in ports[("udp", 9000)]


def test_udp6_absent_free_udp_is_unverified(tmp_path, monkeypatch):
    _proc_net(tmp_path, monkeypatch, ["tcp", "udp"])
    monkeypatch.setattr(manifest, "_tcp_port_accepts", lambda port: False)
    ports = _ports(monkeypatch, "VAST_TCP_PORT_8080", "VAST_UDP_PORT_9000")
    assert ports[("udp", 9000)]["in_use"] is False
    assert ports[("udp", 9000)]["in_use_verified"] is False
    assert "in_use_verified" not in ports[("tcp", 8080)]


# --- supervisord control socket -------------------------------------------- #

def test_supervisor_socket_plain(tmp_path, monkeypatch):
    import socket
    sock = tmp_path / "supervisor.sock"
    with socket.socket(socket.AF_UNIX) as s:
        s.bind(str(sock))
        monkeypatch.setattr(manifest, "SUPERVISOR_SOCK", str(sock))
        m = manifest.assemble(services=[], fragments={}, processes=[])
    assert m["supervisor"]["control_socket"] == {
        "path": str(sock), "present": True, "fallback": False, "target": None,
    }


def test_supervisor_socket_fallback_symlink(tmp_path, monkeypatch):
    import socket
    real = tmp_path / "shm" / "supervisor.sock"
    real.parent.mkdir()
    link = tmp_path / "supervisor.sock"
    link.symlink_to(real)
    with socket.socket(socket.AF_UNIX) as s:
        s.bind(str(real))
        monkeypatch.setattr(manifest, "SUPERVISOR_SOCK", str(link))
        m = manifest.assemble(services=[], fragments={}, processes=[])
    assert m["supervisor"]["control_socket"] == {
        "path": str(link), "present": True, "fallback": True, "target": str(real),
    }
