"""base/10-supervisor.sh off-box: the no-environment client check finds supervisorctl on PATH.

External images install supervisor in their own venv and expose it through a shim on PATH,
not in /usr/local/bin. The staged test points its socket at a temporary directory and finds
a supervisorctl stub only through PATH; the stub records the environment it was given.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TESTS = REPO / "ROOT/opt/instance-tools/tests"
T10 = TESTS / "base/10-supervisor.sh"


def _run_t10(tmp_path: Path) -> tuple[subprocess.CompletedProcess, Path]:
    run = tmp_path / "run"
    run.mkdir()
    sock = run / "supervisor.sock"
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(sock))
    s.close()
    tree = tmp_path / "tests"
    (tree / "base").mkdir(parents=True)
    shutil.copy(TESTS / "lib.sh", tree / "lib.sh")
    text = T10.read_text()
    assert text.count("SOCK=/var/run/supervisor.sock") == 1
    (tree / "base/10.sh").write_text(text.replace("SOCK=/var/run/supervisor.sock", f"SOCK={sock}"))
    shim = tmp_path / "sys-venv/shim"
    shim.mkdir(parents=True)
    seen = tmp_path / "envs"
    (shim / "supervisorctl").write_text(f'#!/bin/bash\nenv | wc -l >> {seen}\nexit 0\n')
    (shim / "supervisorctl").chmod(0o755)
    env = dict(os.environ, PATH=f"{shim}:{os.environ['PATH']}", TEST_NAME="base/10-supervisor")
    return subprocess.run(["bash", str(tree / "base/10.sh")], capture_output=True, text=True,
                          env=env, timeout=120), seen


def test_the_no_environment_check_uses_the_supervisorctl_on_path(tmp_path):
    p, seen = _run_t10(tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    # The last call is the env -i one: bash itself sets PWD, SHLVL and _, nothing else.
    assert int(seen.read_text().split()[-1]) <= 3
