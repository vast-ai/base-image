"""Tests for `etc/vast_boot.d/13-run-dirs.sh`, which recreates the directories the
images need under /run (/run/user/1001 and /run/dbus) on a runtime that mounts a
fresh /run.

It must be a no-op where they are already right (runc), correct only the named
directory (never recursively), and never act through a symlink. The function is
sourced from the shipped file in lib-only mode and driven against a temporary tree
with the current uid/gid standing in for 1001:0.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
STAGE = REPO / "ROOT/etc/vast_boot.d/13-run-dirs.sh"


def _ensure(path: Path, mode: str = "0700") -> subprocess.CompletedProcess:
    script = (f'_VAST_RUN_DIRS_LIB_ONLY=1 . "{STAGE}"; '
              f'_vast_run_dir "{path}" {mode} {os.getuid()} {os.getgid()}')
    return subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True)


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.lstat().st_mode)


def test_creates_a_missing_directory(tmp_path: Path) -> None:
    target = tmp_path / "user" / "1001"
    r = _ensure(target)
    assert r.returncode == 0, r.stdout + r.stderr
    assert target.is_dir() and _mode(target) == 0o700
    assert (target.stat().st_uid, target.stat().st_gid) == (os.getuid(), os.getgid())


def test_second_run_is_a_no_op(tmp_path: Path) -> None:
    target = tmp_path / "user" / "1001"
    _ensure(target)
    before = target.stat()
    r = _ensure(target)
    assert r.returncode == 0 and r.stdout == ""
    after = target.stat()
    assert (after.st_mode, after.st_uid, after.st_gid, after.st_ctime_ns) == (
        before.st_mode, before.st_uid, before.st_gid, before.st_ctime_ns)


def test_existing_directory_corrected_without_touching_its_contents(tmp_path: Path) -> None:
    """Only the directory itself: a session already running in it keeps its files
    and modes, and a symlink inside is never followed."""
    target = tmp_path / "user" / "1001"
    target.mkdir(parents=True, mode=0o755)
    target.chmod(0o755)
    child = target / "child"
    child.mkdir(mode=0o755)
    child.chmod(0o755)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o755)
    (target / "link").symlink_to(outside)
    r = _ensure(target)
    assert r.returncode == 0
    assert _mode(target) == 0o700
    assert _mode(child) == 0o755
    assert _mode(outside) == 0o755
    assert os.readlink(target / "link") == str(outside)


def test_symlink_at_the_path_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o755)
    (tmp_path / "user").mkdir()
    target = tmp_path / "user" / "1001"
    target.symlink_to(outside)
    r = _ensure(target)
    assert r.returncode != 0 and "refusing" in r.stdout
    assert target.is_symlink() and _mode(outside) == 0o755


def test_symlink_at_the_parent_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "user").symlink_to(outside)
    r = _ensure(tmp_path / "user" / "1001")
    assert r.returncode != 0 and "refusing" in r.stdout
    assert list(outside.iterdir()) == []
