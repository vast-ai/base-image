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
