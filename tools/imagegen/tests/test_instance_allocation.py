"""Boot-time allocation record (ADR 0055): the helper and the stage that gates it.

`bin/instance-allocation` reads the instance record once and writes the RAM and disk
allocation for the portal; `14-instance-allocation.sh` runs it only under gVisor. The helper
runs against a local HTTP server standing in for the instance API; the stage runs as a staged
copy with its fixed paths pointed at stubs (each substitution asserted).
"""
from __future__ import annotations

import http.server
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HELPER = REPO / "ROOT/opt/instance-tools/bin/instance-allocation"
STAGE = REPO / "ROOT/etc/vast_boot.d/14-instance-allocation.sh"

# The instance record from a live runc boot: a 1-GPU share (0.25) of a 4-GPU machine, 60 GB disk.
RECORD = {"cpu_ram": 128683, "gpu_frac": 0.25, "disk_space": 60.0, "num_gpus": 1,
          "extra_env": [["SECRET_TOKEN", "do-not-store"]]}


def _helper():
    loader = importlib.machinery.SourceFileLoader("instance_allocation", str(HELPER))
    spec = importlib.util.spec_from_loader("instance_allocation", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_figures_from_a_real_instance_record():
    figures = _helper().allocation(RECORD)
    assert figures == {"disk_bytes": 60 * 1024**3, "ram_bytes": 32_170_750_000}


@pytest.mark.parametrize("record", [
    {"cpu_ram": 128683},                        # no GPU share
    {"cpu_ram": 128683, "gpu_frac": 0},
    {"cpu_ram": 128683, "gpu_frac": 1.5},       # not a share
    {"cpu_ram": True, "gpu_frac": 0.25},
    {"cpu_ram": "128683", "gpu_frac": 0.25},
])
def test_no_ram_figure_without_a_usable_share(record):
    assert "ram_bytes" not in _helper().allocation(record)


class _API(http.server.BaseHTTPRequestHandler):
    status, body, seen = 200, {"instances": RECORD}, []

    def do_GET(self):
        type(self).seen.append((self.path, self.headers.get("Authorization")))
        payload = json.dumps(type(self).body).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def api(tmp_path, monkeypatch):
    _API.status, _API.body, _API.seen = 200, {"instances": RECORD}, []
    server = http.server.HTTPServer(("127.0.0.1", 0), _API)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    mod = _helper()
    monkeypatch.setattr(mod, "API", f"http://127.0.0.1:{server.server_port}/instances/")
    monkeypatch.setattr(mod, "OUT", tmp_path / "vast-allocation.json")
    monkeypatch.setenv("CONTAINER_ID", "C.58087446")
    monkeypatch.setenv("CONTAINER_API_KEY", "container-key")
    yield mod
    server.shutdown()


def test_records_the_allocation_and_nothing_else(api, capsys):
    assert api.main() == 0
    assert _API.seen == [("/instances/58087446/", "Bearer container-key")]
    assert json.loads(api.OUT.read_text()) == {"disk_bytes": 60 * 1024**3, "ram_bytes": 32_170_750_000}
    assert oct(api.OUT.stat().st_mode & 0o777) == "0o644"
    out = capsys.readouterr().out
    assert "do-not-store" not in api.OUT.read_text() + out and "container-key" not in out


def test_a_failed_request_keeps_the_earlier_record(api):
    api.OUT.write_text('{"ram_bytes": 1}')
    _API.status = 429
    assert api.main() == 0
    assert api.OUT.read_text() == '{"ram_bytes": 1}'


def test_no_key_means_no_request(api, monkeypatch):
    monkeypatch.delenv("CONTAINER_API_KEY")
    assert api.main() == 0
    assert _API.seen == [] and not api.OUT.exists()


# ---- the stage: under gVisor only ----

def _run_stage(tmp_path: Path, gvisor: bool) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ran = tmp_path / "helper-ran"
    (bin_dir / "is-gvisor").write_text(f"#!/bin/bash\nexit {0 if gvisor else 1}\n")
    (bin_dir / "instance-allocation").write_text(f"#!/bin/bash\ntouch {ran}\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    text = STAGE.read_text()
    assert text.count("/opt/instance-tools/bin/") == 2
    staged = tmp_path / "stage.sh"
    staged.write_text(text.replace("/opt/instance-tools/bin/", f"{bin_dir}/"))
    # Sourced, as the boot does; `wait` lets the backgrounded helper finish.
    subprocess.run(["bash", "-c", f". {staged}; wait"], check=True, capture_output=True)
    return ran


def test_stage_runs_the_helper_under_gvisor(tmp_path):
    assert _run_stage(tmp_path, gvisor=True).exists()


def test_stage_does_nothing_under_runc(tmp_path):
    assert not _run_stage(tmp_path, gvisor=False).exists()
