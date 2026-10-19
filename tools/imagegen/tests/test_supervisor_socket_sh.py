"""Tests for the supervisord control-socket choice in
`etc/vast_boot.d/65-supervisor-launch.sh` and the keep-alive in `95-supervisor-wait.sh`.

supervisord binds a temporary name and hard-links it to its socket path. Where the
root filesystem refuses link() on a unix socket, the stage moves the socket to a
fresh directory under /dev/shm (an include plus a symlink at the usual path); where
nothing works it leaves supervisord unlaunched and the boot must stay up.

The functions are sourced from the shipped file in lib-only mode and driven against
temporary paths. A probe outcome is forced by redefining `_vast_sock_probe` for one
directory while the shipped probe answers for the others.
"""
from __future__ import annotations

import os
import shutil
import socket
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

REPO = Path(__file__).resolve().parents[3]
LAUNCH = REPO / "ROOT/etc/vast_boot.d/65-supervisor-launch.sh"
WAIT = REPO / "ROOT/etc/vast_boot.d/95-supervisor-wait.sh"


@pytest.fixture
def short_tmp() -> Iterator[Path]:
    """A unix socket path is limited to 107 bytes, and pytest's tmp_path can use
    most of that before the probe adds its own name."""
    d = Path(tempfile.mkdtemp(prefix="vss", dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def layout(short_tmp: Path) -> dict[str, Path]:
    tmp_path = short_tmp
    run = tmp_path / "run"
    confd = tmp_path / "conf.d"
    shm = tmp_path / "shm"
    for d in (run, confd, shm):
        d.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # A stand-in supervisord that records it was started.
    stub = bindir / "supervisord"
    stub.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > "{tmp_path}/launched"\n')
    stub.chmod(0o755)
    return {
        "tmp": tmp_path,
        "run": run,
        "sock": run / "supervisor.sock",
        "include": confd / "vast-supervisor-socket.conf",
        "shm_dir": shm / "vast-supervisor",
        "launched": tmp_path / "launched",
        "bin": bindir,
    }


def _boot(layout: dict[str, Path], fail: list[Path] = (), uid: int | None = None,
          pre: str = "") -> subprocess.CompletedProcess:
    """Source the stage lib-only and run `_vast_supervisor_launch`, with the probe
    forced to fail in each directory of `fail`. Prints `pid=<value|UNSET>` last."""
    fail_dirs = " ".join(f'"{d}"' for d in fail)
    script = f'''
_VAST_SUPERVISOR_LAUNCH_LIB_ONLY=1 . "{LAUNCH}"
eval "_real_$(declare -f _vast_sock_probe)"
_vast_sock_probe() {{
    local d
    for d in {fail_dirs}; do [[ "$1" == "$d" ]] && return 1; done
    _real__vast_sock_probe "$1"
}}
{pre}
_vast_supervisor_launch "{layout['sock']}" "{layout['include']}" "{layout['shm_dir']}" {uid if uid is not None else os.getuid()}
wait
echo "pid=${{supervisord_pid-UNSET}}"
'''
    env = {"PATH": f"{layout['bin']}:/usr/bin:/bin"}
    return subprocess.run(["/bin/bash", "-c", script], env=env, capture_output=True, text=True)


def _plant_stale_socket(path: Path) -> None:
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(path))
    s.close()


def test_probe_success_keeps_the_shipped_layout(layout: dict[str, Path]) -> None:
    """Where the socket directory supports a linked socket nothing changes: no
    include, no symlink, no /dev/shm directory, and supervisord is launched."""
    _plant_stale_socket(layout["sock"])
    layout["include"].write_text("[unix_http_server]\nfile=/stale\n")
    r = _boot(layout)
    assert r.returncode == 0, r.stderr
    assert not layout["sock"].exists() and not layout["sock"].is_symlink()
    assert not layout["include"].exists()
    assert not layout["shm_dir"].exists()
    assert f"control socket: {layout['sock']}" in r.stdout
    assert layout["launched"].read_text().split() == [
        "-n", "-u", "root", "-c", "/etc/supervisor/supervisord.conf"]
    assert "pid=UNSET" not in r.stdout


def test_fallback_then_success_restores_the_shipped_layout(layout: dict[str, Path]) -> None:
    """A boot that cannot link in the usual directory falls back to /dev/shm; the
    next boot that can removes the include and the symlink again."""
    r = _boot(layout, fail=[layout["run"]])
    assert r.returncode == 0, r.stderr
    fallback = layout["shm_dir"] / "supervisor.sock"
    assert layout["sock"].is_symlink() and os.readlink(layout["sock"]) == str(fallback)
    lines = [ln for ln in layout["include"].read_text().splitlines()
             if ln.strip() and not ln.startswith(";")]
    assert lines == ["[unix_http_server]", f"file={fallback}"]
    assert stat.S_IMODE(layout["shm_dir"].lstat().st_mode) == 0o700
    assert f"control socket: {fallback}" in r.stdout
    assert layout["launched"].exists()

    layout["launched"].unlink()
    r = _boot(layout)
    assert r.returncode == 0, r.stderr
    assert not layout["sock"].is_symlink() and not layout["sock"].exists()
    assert not layout["include"].exists()
    assert layout["launched"].exists()


@pytest.mark.parametrize("planted", ["directory", "symlink", "wrong-owner"])
def test_unsafe_fallback_directory_is_refused(layout: dict[str, Path], planted: str) -> None:
    """The fallback directory must be one this boot created, owned by the expected
    uid with mode 0700. Anything else fails the fallback, and with it the launch."""
    uid = os.getuid()
    elsewhere = layout["tmp"] / "elsewhere"
    elsewhere.mkdir(mode=0o755)
    if planted == "directory":
        layout["shm_dir"].mkdir(mode=0o700)
    elif planted == "symlink":
        layout["shm_dir"].symlink_to(elsewhere)
    else:
        uid += 1
    r = _boot(layout, fail=[layout["run"]], uid=uid)
    assert "pid=UNSET" in r.stdout
    assert not layout["include"].exists()
    assert not layout["sock"].exists() and not layout["sock"].is_symlink()
    assert not layout["launched"].exists()
    assert stat.S_IMODE(elsewhere.stat().st_mode) == 0o755
    assert list(elsewhere.iterdir()) == []


def test_no_location_leaves_supervisord_unlaunched(layout: dict[str, Path]) -> None:
    """Every probe failing: supervisord is not started, a supervisord_pid inherited
    from anywhere is cleared, the failure is logged, and the boot shell carries on."""
    r = _boot(layout, fail=[layout["run"], layout["shm_dir"]], pre="supervisord_pid=4242")
    assert r.returncode == 0, r.stderr
    assert "pid=UNSET" in r.stdout
    assert not layout["launched"].exists()
    assert "ERROR: supervisord is NOT started" in r.stdout
    assert not layout["include"].exists()
    assert not layout["sock"].is_symlink()


def _run_wait(pid_setup: str, timeout: float) -> subprocess.CompletedProcess:
    # rm is stubbed so the stage cannot touch the host's /.provisioning.
    script = f'rm() {{ :; }}\n{pid_setup}\n. "{WAIT}"\necho returned'
    return subprocess.run(["timeout", str(timeout), "/bin/bash", "-c", script],
                          capture_output=True, text=True)


def test_wait_stage_stays_up_without_supervisord() -> None:
    """The wait stage is the end of the container's main process. With no
    supervisord to wait on it must not return, or the container stops."""
    r = _run_wait("unset supervisord_pid", timeout=2)
    assert r.returncode == 124, r.stdout
    assert "returned" not in r.stdout


def test_wait_stage_still_returns_when_supervisord_exits() -> None:
    """With supervisord launched the wait is on it, as before: when it exits the
    stage returns."""
    r = _run_wait("sleep 0.2 & supervisord_pid=$!", timeout=10)
    assert r.returncode == 0
    assert "returned" in r.stdout


def _probe(directory: Path) -> int:
    script = f'_VAST_SUPERVISOR_LAUNCH_LIB_ONLY=1 . "{LAUNCH}"; _vast_sock_probe "{directory}"'
    return subprocess.run(["/bin/bash", "-c", script]).returncode


def test_probe_reports_success_and_leaves_nothing(short_tmp: Path) -> None:
    assert _probe(short_tmp) == 0
    assert list(short_tmp.iterdir()) == []


def test_probe_reports_failure(tmp_path: Path) -> None:
    assert _probe(tmp_path / "missing") != 0
