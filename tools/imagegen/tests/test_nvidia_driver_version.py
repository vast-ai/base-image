"""The driver-version helper, the runtime check, and the first caller moved onto them.

ROOT/opt/instance-tools/bin/nvidia-driver-version answers "which NVIDIA driver is
this?" for every shell caller. It exists because the old answer, reading
/proc/driver/nvidia/version, depends on the container runtime rather than on the
GPU: under gVisor that file is absent while the GPU and nvidia-smi work, so a
reader got nothing and a test for the file concluded there was no GPU.

No container and no GPU needed: the helper's inputs are one file and one command.
Each test stages a copy of the shipped script with the /proc path pointed at a
fixture (asserting the substitution happened, so a renamed constant fails loudly
instead of silently testing the real /proc) and a stub nvidia-smi on PATH.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from imagegen.discover import find_repo_root

BIN = find_repo_root(Path(__file__).resolve().parent) / "ROOT/opt/instance-tools/bin"

PROPRIETARY = ("NVRM version: NVIDIA UNIX x86_64 Kernel Module  580.95.05  "
               "Tue Aug 26 21:13:01 UTC 2025\nGCC version:  gcc version 13.3.0\n")
OPEN_MODULE = ("NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  575.57.08  "
               "Release Build  (dvs-builder@U16-I3-B03-4-3)  Wed May 21 2025\n"
               "GCC version:  gcc version 12.3.0\n")
OPEN_AARCH64 = ("NVRM version: NVIDIA UNIX Open Kernel Module for aarch64  580.82.07  "
                "Release Build  (dvs-builder@U22-A23)  Mon Aug 18 2025\n")
PROPRIETARY_AARCH64 = "NVRM version: NVIDIA UNIX aarch64 Kernel Module  535.183.01  Sun May 12 2024\n"
# Legacy branches print a two-part version.
TWO_PART = "NVRM version: NVIDIA UNIX x86_64 Kernel Module  390.157  Wed Oct 12 09:19:07 UTC 2022\n"


def _toolbox(tmp_path: Path) -> Path:
    """Only the coreutils the scripts call, so a real nvidia-smi on the test host
    can never answer for a stub that is meant to be missing."""
    box = tmp_path / "toolbox"
    box.mkdir()
    for tool in ("head", "awk", "timeout", "sleep", "rm", "mkdir"):
        real = shutil.which(tool)
        assert real, f"{tool} is required to run these scripts"
        (box / tool).symlink_to(real)
    return box


def _stage(src: Path, dst: Path, subs: dict[str, str]) -> Path:
    text = src.read_text()
    for old, new in subs.items():
        assert text.count(old) == 1, f"seam {old!r} not found exactly once in {src.name}"
        text = text.replace(old, new)
    dst.write_text(text)
    dst.chmod(0o755)
    return dst


def _smi(bin_: Path, body: str) -> None:
    f = bin_ / "nvidia-smi"
    f.write_text("#!/bin/sh\n" + body)
    f.chmod(0o755)


@pytest.fixture
def ndv(tmp_path):
    """Return (run, proc, bin) for a staged helper; PATH is the stub dir plus a
    toolbox of coreutils, nothing else."""
    proc = tmp_path / "version"
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    script = _stage(BIN / "nvidia-driver-version", tmp_path / "nvidia-driver-version", {
        "_PROC_VERSION=/proc/driver/nvidia/version": f"_PROC_VERSION={proc}",
        "_SMI_TIMEOUT=10": "_SMI_TIMEOUT=2",
    })
    env = {"PATH": f"{bin_}:{_toolbox(tmp_path)}"}

    def run() -> subprocess.CompletedProcess:
        return subprocess.run([str(script)], capture_output=True, text=True,
                              env=env, timeout=30)
    return run, proc, bin_


def _answers(r: subprocess.CompletedProcess, version: str) -> None:
    assert r.returncode == 0, r
    assert r.stdout == version + "\n", r


def _refuses(r: subprocess.CompletedProcess) -> None:
    assert r.returncode != 0, r
    assert r.stdout == "", r


# --- /proc ------------------------------------------------------------------ #

@pytest.mark.parametrize("content,version", [
    (PROPRIETARY, "580.95.05"),
    (OPEN_MODULE, "575.57.08"),
    (OPEN_AARCH64, "580.82.07"),
    (PROPRIETARY_AARCH64, "535.183.01"),
    (TWO_PART, "390.157"),
])
def test_proc_formats_parse(ndv, content, version):
    run, proc, _ = ndv
    proc.write_text(content)
    _answers(run(), version)


def test_proc_wins_over_nvidia_smi(ndv):
    run, proc, bin_ = ndv
    proc.write_text(PROPRIETARY)
    _smi(bin_, "echo 111.11.11\n")
    _answers(run(), "580.95.05")


def test_unparseable_proc_falls_through(ndv):
    run, proc, bin_ = ndv
    proc.write_text("NVRM version: something we have never seen\n")
    _smi(bin_, 'case "$1" in --query-gpu=driver_version) echo 580.95.05;; *) exit 9;; esac\n')
    _answers(run(), "580.95.05")


# --- nvidia-smi (the gVisor path: no /proc file) ----------------------------- #

def test_no_proc_two_gpus_first_line_only(ndv):
    run, _, bin_ = ndv
    _smi(bin_, 'case "$1" in --query-gpu=driver_version) printf "580.95.05\\n575.57.08\\n";; '
               '*) exit 9;; esac\n')
    _answers(run(), "580.95.05")


def test_no_proc_whitespace_trimmed(ndv):
    run, _, bin_ = ndv
    _smi(bin_, 'printf "  580.95.05 \\r\\n"\n')
    _answers(run(), "580.95.05")


@pytest.mark.parametrize("body", [
    pytest.param(None, id="missing"),
    pytest.param("exit 6\n", id="failing"),
    pytest.param("echo 580.95.05; exit 6\n", id="failing-with-output"),
    pytest.param("", id="empty"),
    pytest.param("echo 'No devices were found'\n", id="not-a-version"),
])
def test_no_answer_prints_nothing_and_fails(ndv, body):
    run, _, bin_ = ndv
    if body is not None:
        _smi(bin_, body)
    _refuses(run())


def test_hanging_nvidia_smi_is_time_bounded(ndv):
    # sleep runs as a CHILD of the stub: a pipe or a kill aimed only at the stub
    # would leave it holding stdout and the helper waiting for the full sleep.
    run, _, bin_ = ndv
    _smi(bin_, "sleep 300\n")
    t0 = time.monotonic()
    r = run()
    assert time.monotonic() - t0 < 15
    _refuses(r)


# --- is-gvisor --------------------------------------------------------------- #

@pytest.mark.parametrize("kind,rc", [("dir", 0), ("absent", 1), ("file", 1)])
def test_is_gvisor(tmp_path, kind, rc):
    marker = tmp_path / "gvisor"
    if kind == "dir":
        marker.mkdir()
    elif kind == "file":
        marker.write_text("")
    script = _stage(BIN / "is-gvisor", tmp_path / "is-gvisor",
                    {"[[ -d /proc/gvisor ]]": f"[[ -d {marker} ]]"})
    r = subprocess.run([str(script)], capture_output=True, text=True)
    assert (r.returncode, r.stdout, r.stderr) == (rc, "", "")


# --- install-display-drivers, the first caller ------------------------------- #

@pytest.fixture
def idd(tmp_path):
    """install-display-drivers with the helper and its state dirs redirected.

    Only paths that end before any download are exercised: no nvidia-smi (CPU
    container), no version (must fail loudly), and a version matching the cached
    one (must use the helper's answer and touch nothing)."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    drivers = tmp_path / "nvidia-drivers"
    ldconf = tmp_path / "zz-nvidia-drivers.conf"
    helper = tmp_path / "helper"
    script = _stage(BIN / "install-display-drivers", tmp_path / "install-display-drivers", {
        'NVIDIA_DRIVERS_DIR="/opt/nvidia-drivers"': f'NVIDIA_DRIVERS_DIR="{drivers}"',
        'NVIDIA_DRIVERS_LDCONF="/etc/ld.so.conf.d/zz-nvidia-drivers.conf"':
            f'NVIDIA_DRIVERS_LDCONF="{ldconf}"',
        'NVIDIA_DRIVER_VERSION_BIN="/opt/instance-tools/bin/nvidia-driver-version"':
            f'NVIDIA_DRIVER_VERSION_BIN="{helper}"',
    })

    box = _toolbox(tmp_path)

    def run(*, smi: bool, helper_body: str) -> subprocess.CompletedProcess:
        helper.write_text("#!/bin/sh\n" + helper_body)
        helper.chmod(0o755)
        if smi:
            _smi(bin_, "exit 0\n")
        return subprocess.run([str(script)], capture_output=True, text=True,
                              env={"PATH": f"{bin_}:{box}"}, timeout=30)
    return run, drivers, ldconf


def test_install_display_drivers_cpu_container_is_quiet(idd):
    run, _, _ = idd
    r = run(smi=False, helper_body="exit 1\n")
    assert r.returncode == 0, r
    assert "nothing to do" in r.stdout


def test_install_display_drivers_fails_loudly_without_version(idd):
    run, drivers, _ = idd
    r = run(smi=True, helper_body="exit 1\n")
    assert r.returncode != 0, r
    assert "ERROR" in r.stderr and "driver version" in r.stderr, r
    assert not drivers.exists()


def test_install_display_drivers_uses_helper_version(idd):
    run, drivers, ldconf = idd
    drivers.mkdir()
    (drivers / ".driver-version").write_text("580.95.05\n")
    ldconf.write_text("x\n")
    r = run(smi=True, helper_body="echo 580.95.05\n")
    assert r.returncode == 0, r
    assert "580.95.05 libs already installed" in r.stdout, r
