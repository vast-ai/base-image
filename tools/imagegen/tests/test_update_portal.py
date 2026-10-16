"""Runs the shipped update-portal against a local archive (L104).

PORTAL_DOWNLOAD_URL used to skip the setup that the GitHub-release path did: curl wrote the
archive to "/", `-v` was ignored, and VERSION was overwritten with an empty string, which
makes every later first boot skip portal updates. These run the real script in a sandbox
(PORTAL_INSTALL_ROOT) through that override.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "ROOT/opt/instance-tools/bin/update-portal"

pytestmark = pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")


def archive(tmp_path: Path, version: str | None) -> Path:
    src = tmp_path / "src/portal-aio"
    src.mkdir(parents=True)
    (src / "requirements.txt").write_text("")
    (src / "marker").write_text("new portal\n")
    if version is not None:
        (src / "VERSION").write_text(version + "\n")
    out = tmp_path / "instance-portal.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        tar.add(src, arcname="portal-aio")
    return out


def install(tmp_path: Path, tarball: Path, *args: str) -> subprocess.CompletedProcess:
    root = tmp_path / "opt"
    venv_bin = root / "portal-aio/venv/bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    (venv_bin / "pip").write_text("#!/bin/sh\nexit 0\n")
    (venv_bin / "pip").chmod(0o755)
    env = {**os.environ, "PORTAL_INSTALL_ROOT": str(root),
           "PORTAL_DOWNLOAD_URL": f"file://{tarball}"}
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)


def test_download_url_installs_and_keeps_the_archives_version(tmp_path):
    r = install(tmp_path, archive(tmp_path, "v3.1.7"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "opt/portal-aio/marker").read_text() == "new portal\n"
    assert (tmp_path / "opt/portal-aio/VERSION").read_text().strip() == "v3.1.7"


def test_download_url_honours_a_requested_version(tmp_path):
    r = install(tmp_path, archive(tmp_path, "v3.1.7"), "-v", "3.1.8")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "opt/portal-aio/VERSION").read_text().strip() == "v3.1.8"


def test_an_install_that_would_leave_no_version_fails(tmp_path):
    r = install(tmp_path, archive(tmp_path, None))
    assert r.returncode != 0
    assert "no VERSION" in r.stdout + r.stderr
