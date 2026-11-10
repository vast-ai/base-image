"""bind-loopback, as each engine image SHIPS it (L119).

Each engine's in-image contract_check.py is loaded from the image tree and its own
check_bind runs against fake `ss -tlnH` tables. The engine must be bound to a
loopback address — 127.0.0.0/8 or ::1 — and nothing else: Caddy is what faces the
network and authenticates. A wildcard or a routable interface (the docker bridge,
reachable by co-tenant containers) is a violation; no listener is an error.
"""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SUITES = {
    "vllm": "external/vllm/ROOT/opt/instance-tools/tests/vllm.d/contract_check.py",
    "vllm-omni": "external/vllm-omni/ROOT/opt/instance-tools/tests/vllm-omni.d/contract_check.py",
    "sglang": "external/sglang/ROOT/opt/instance-tools/tests/sglang.d/contract_check.py",
    "llama": "derivatives/llama-cpp/ROOT/opt/instance-tools/tests/llama.d/contract_check.py",
}


def _load(engine):
    spec = importlib.util.spec_from_file_location(f"cc_{engine.replace('-', '_')}",
                                                  REPO / SUITES[engine])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MODULES = {e: _load(e) for e in SUITES}


def _verdict(engine, table, monkeypatch, port=18000):
    mod = MODULES[engine]

    class R:
        stdout = table
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: R())
    rep = mod.Report(io.StringIO(), deviations={})
    mod.check_bind(rep, port, ["--host", "127.0.0.1"])
    if any(c == "bind-loopback" for c, _ in rep.violations):
        return "violation"
    if any(c == "bind-loopback" for c, _ in rep.errors):
        return "error"
    assert "bind-loopback" in rep.passed
    return "ok"


def _ss(*addrs):
    return "".join(f"LISTEN 0 4096 {a} 0.0.0.0:*\n" for a in addrs)


TABLES = [
    ("loopback-v4", _ss("127.0.0.1:18000"), "ok"),
    ("loopback-v4-other", _ss("127.0.0.53:18000"), "ok"),
    ("loopback-v6", _ss("[::1]:18000"), "ok"),
    ("both-loopbacks", _ss("127.0.0.1:18000", "[::1]:18000"), "ok"),
    ("wildcard-v4", _ss("0.0.0.0:18000"), "violation"),
    ("wildcard-v6", _ss("[::]:18000"), "violation"),
    ("wildcard-star", _ss("*:18000"), "violation"),
    ("bridge", _ss("172.17.0.2:18000"), "violation"),
    ("loopback-and-bridge", _ss("127.0.0.1:18000", "172.17.0.2:18000"), "violation"),
    ("not-listening", "", "error"),
    ("other-port-only", _ss("127.0.0.1:18001", "0.0.0.0:8000"), "error"),
]


@pytest.mark.parametrize("engine", sorted(SUITES))
@pytest.mark.parametrize("name,table,expected", TABLES, ids=[t[0] for t in TABLES])
def test_every_engine_decides_bind_loopback_the_same_way(engine, name, table, expected,
                                                         monkeypatch):
    assert _verdict(engine, table, monkeypatch) == expected
