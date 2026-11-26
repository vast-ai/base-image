"""A local stand-in for a test instance, for the delivered-run tests.

`BoxRunner` replaces the `subprocess.run` that remote.RemoteSession calls: ssh-keygen
writes a key pair, and every ssh command is executed by bash on this machine. The
paths a real run uses on the box (the run root, the baked suite, supervisord's pid
file) are pointed into a temporary directory through delivered.Box, so nothing needs
root.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TESTS_ROOT = REPO / "ROOT/opt/instance-tools/tests"
SUITES_PY = REPO / "tools/imagegen/imagegen/suites.py"
INSTANCE = {"public_ipaddr": "203.0.113.7", "ports": {"22/tcp": [{"HostPort": "40022"}]}}


class FakeAPI:
    def __init__(self, instance=INSTANCE):
        self.calls, self.instance = [], instance

    def _request_safe(self, method, path, body=None, query=None):
        self.calls.append((method, path, body))
        if method == "POST":
            return {"success": True, "key": json.dumps({"id": 991})}
        return {"success": True}

    def get_instance(self, instance_id, safe=False):
        return self.instance


class BoxRunner:
    """`down` > 0 makes that many ssh commands fail as a dropped connection does."""

    def __init__(self, path=None):
        self.commands, self.control, self.down = [], [], 0
        self.env = {"PATH": path or os.environ["PATH"], "HOME": os.environ.get("HOME", "/")}

    def __call__(self, cmd, input=None, text=False, timeout=None, **kw):
        if cmd[0] == "ssh-keygen":
            key = cmd[cmd.index("-f") + 1]
            Path(key).write_text("PRIVATE")
            Path(key + ".pub").write_text("ssh-ed25519 AAAAPUBLIC vast-qa\n")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if "-O" in cmd:
            self.control.append(cmd)
            return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        self.commands.append(cmd)
        if self.down > 0:
            self.down -= 1
            refused = "ssh: connect to host 203.0.113.7 port 40022: Connection refused"
            return types.SimpleNamespace(returncode=255, stdout="" if text else b"",
                                         stderr=refused if text else refused.encode())
        return subprocess.run(["bash", "-c", cmd[-1]], input=input, capture_output=True,
                              text=text, timeout=timeout, env=self.env, cwd="/")


def write_tree(root: Path, files: dict[str, str]):
    """Files under `root`, each executable (the runner only discovers those)."""
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        p.chmod(0o755)


def make_repo(tmp_path: Path, base: dict[str, str], images: dict[str, dict] | None = None):
    """A minimal checkout: the real lib.sh and suites.py, a base suite, and any
    `images` as {path in the repo: {"dockerfile": text, "tests": {rel: body}}}."""
    repo = tmp_path / "repo"
    tests = repo / "ROOT/opt/instance-tools/tests"
    tests.mkdir(parents=True)
    (repo / "Dockerfile").write_text("FROM ubuntu:24.04\n")
    (repo / "external").mkdir()
    shutil.copy(TESTS_ROOT / "lib.sh", tests / "lib.sh")
    (repo / "tools/imagegen/imagegen").mkdir(parents=True)
    shutil.copy(SUITES_PY, repo / "tools/imagegen/imagegen/suites.py")
    write_tree(tests, base)
    for where, spec in (images or {}).items():
        d = repo / where
        d.mkdir(parents=True, exist_ok=True)
        (d / "Dockerfile").write_text(spec.get("dockerfile", "FROM vastai/base-image:x\n"))
        for overlay, files in spec.get("overlays", {}).items():
            write_tree(d / overlay / "opt/instance-tools/tests", files)
    return repo


def supervisord(tmp_path: Path, env: dict[str, str]):
    """A process whose command line names supervisord, with `env` as its environment,
    and the pid file a driver finds it by. The caller kills it."""
    proc = subprocess.Popen(["bash", "-c", "exec -a supervisord sleep 300"], env=env)
    pidfile = tmp_path / "supervisord.pid"
    pidfile.write_text(f"{proc.pid}\n")
    return proc, pidfile
