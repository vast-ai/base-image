"""Tests for the nvreach LD_PRELOAD shim (ADR 0050).

On a Vast container given a subset of a host's GPUs, every `/dev/nvidiaN` exists
but the device cgroup refuses `open()` on the unallocated ones. pixelflux decides
reachability with `access(F_OK)`, so its multi-GPU NVENC filter never installs
and NVENC fails. The shim, preloaded into Selkies alone, answers `access(F_OK)`
on a GPU node the way `open()` would, until the pinned Selkies carries the
upstream fix (selkies-project/pixelflux#44).

The shim is built from the shipped source with the node prefix pointed at a
temp directory: a mode-000 file stands in for a cgroup-refused node, which only
an unprivileged user can be refused.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
COPIES = [
    REPO / "derivatives/linux-desktop/selkies/nvreach.c",
    REPO / "derivatives/pytorch/derivatives/aio-studio/selkies/nvreach.c",
]

pytestmark = pytest.mark.skipif(
    shutil.which("gcc") is None or os.geteuid() == 0,
    reason="needs gcc, and an unprivileged user so mode bits can refuse open()")


def test_every_desktop_image_ships_the_same_shim():
    assert len({p.read_bytes() for p in COPIES}) == 1


def test_access_reports_an_unopenable_gpu_node_absent_and_passes_everything_else(tmp_path):
    nodes = tmp_path / "dev"
    nodes.mkdir()
    (nodes / "nvidia0").write_text("")
    (nodes / "nvidia1").write_text("")
    (nodes / "nvidia1").chmod(0o000)
    (nodes / "nvidiactl").write_text("")
    (nodes / "nvidiactl").chmod(0o000)
    so = tmp_path / "nvreach.so"
    subprocess.run(["gcc", "-shared", "-fPIC", "-O2", "-Wall", "-Werror", "-Wextra",
                    f'-DNVREACH_PREFIX="{nodes}/nvidia"', "-o", str(so), str(COPIES[0]), "-ldl"],
                   check=True)
    probe = (
        "import ctypes, os, sys\n"
        "libc = ctypes.CDLL(None, use_errno=True)\n"
        "for p, m in [(a.split(':')[0], int(a.split(':')[1])) for a in sys.argv[1:]]:\n"
        "    r = libc.access(p.encode(), m)\n"
        "    print(p.rsplit('/', 1)[1], m, r, os.strerror(ctypes.get_errno()) if r else '')\n")
    args = [f"{nodes}/nvidia0:0", f"{nodes}/nvidia1:0", f"{nodes}/nvidia1:4",
            f"{nodes}/nvidiactl:0", f"{nodes}/nvidia7:0"]
    out = subprocess.run(["python3", "-c", probe, *args], capture_output=True, text=True,
                         env={**os.environ, "LD_PRELOAD": str(so)}, check=True).stdout.split("\n")
    assert out[:5] == [
        "nvidia0 0 0 ",                            # opens: reachable
        "nvidia1 0 -1 No such file or directory",  # exists, refuses open(): absent
        "nvidia1 4 -1 Permission denied",          # not F_OK: real access(), unchanged
        "nvidiactl 0 0 ",                          # not a GPU node: real access()
        "nvidia7 0 -1 No such file or directory",  # missing: still absent
    ]
