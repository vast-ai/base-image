"""Container RAM/CPU figures from cgroups, for the portal's /system-metrics.

Readers try cgroup v2, then v1, and treat nothing readable as "no limit known"
(the caller then falls back to process-level figures from psutil). No code here
assumes a cgroup version: runc hosts expose v2 (``memory.max``, ``cpu.max``),
the gVisor sandbox exposes v1 only (``memory/memory.limit_in_bytes``,
``cpu/cpu.cfs_quota_us``), and either may expose none at all.

Kept out of ``portal.py`` (and free of psutil) so it can be tested without the
portal's runtime dependencies. Paths are module attributes so tests can point
them at a fixture tree.
"""

from __future__ import annotations

import os
import time
from typing import Optional

CGROUP_ROOT = "/sys/fs/cgroup"
MEMINFO = "/proc/meminfo"
MOUNTS = "/proc/self/mounts"

# Filesystems whose statfs answers for the HOST's filesystem, not the instance's disk. Under
# gVisor with a host-served root (--overlay2=none) the root and bind mounts are 9p, and df shows
# the host's 1.8 TB instead of the instance's allocation.
_HOST_SERVED_FS = {"9p"}

# Values at or above this are the v1 "unlimited" sentinel (9223372036854771712)
# or close to it, never a real allocation.
_UNLIMITED_FLOOR = 10**15


def _path(rel: str) -> str:
    return os.path.join(CGROUP_ROOT, rel)


def _read(rel: str) -> Optional[str]:
    try:
        with open(_path(rel)) as f:
            return f.read().strip()
    except (OSError, ValueError):
        return None


def _read_int(rel: str) -> Optional[int]:
    value = _read(rel)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:  # "max", empty
        return None


def _mem_total() -> int:
    """MemTotal from /proc/meminfo in bytes, or 0 when unreadable."""
    try:
        with open(MEMINFO) as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def is_in_container() -> bool:
    """True when a cgroup memory controller is visible (v2 or v1)."""
    return os.path.exists(_path("memory/memory.limit_in_bytes")) or os.path.exists(
        _path("memory.max")
    )


def _is_real_limit(value: Optional[int], mem_total: int) -> bool:
    """A limit is real unless it is unset, the unlimited sentinel, or larger
    than MemTotal.

    A limit EQUAL to MemTotal is real: under gVisor /proc/meminfo reports the
    sandbox's memory limit as MemTotal, so the two are the same number.
    """
    if value is None or value <= 0 or value >= _UNLIMITED_FLOOR:
        return False
    if mem_total and value > mem_total:
        return False
    return True


def _v2_memory(mem_total: int) -> tuple[Optional[int], Optional[int]]:
    limit = None
    # memory.low (soft) first: Vast sets the user's allocation there and a
    # higher hard limit with burst headroom.
    for rel in ("memory.low", "memory.max"):
        value = _read_int(rel)
        if _is_real_limit(value, mem_total):
            limit = value
            break
    return limit, _read_int("memory.current")


def _v1_memory(mem_total: int) -> tuple[Optional[int], Optional[int]]:
    limit = None
    for rel in ("memory/memory.soft_limit_in_bytes", "memory/memory.limit_in_bytes"):
        value = _read_int(rel)
        if _is_real_limit(value, mem_total):
            limit = value
            break
    return limit, _read_int("memory/memory.usage_in_bytes")


def get_container_memory_limit() -> Optional[int]:
    """The container's RAM allocation in bytes, or None when no real limit is
    visible (v2 ``max``, the v1 unlimited sentinel, or no cgroup)."""
    mem_total = _mem_total()
    for reader in (_v2_memory, _v1_memory):
        limit, _ = reader(mem_total)
        if limit is not None:
            return limit
    return None


def get_container_memory_stats() -> Optional[dict]:
    """total/used/percent from one cgroup version, or None to fall back.

    Limit and usage always come from the same version so they describe the
    same group.
    """
    if not is_in_container():
        return None
    mem_total = _mem_total()
    for reader in (_v2_memory, _v1_memory):
        limit, usage = reader(mem_total)
        if limit is not None and usage is not None:
            return {"total": limit, "used": usage, "percent": (usage / limit) * 100}
    return None


# --- CPU ---------------------------------------------------------------------

# State for delta-based CPU measurement.
_cpu_prev: dict = {"timestamp": 0.0, "usage": 0, "percent": 0.0}

_V1_CPU_DIRS = ("cpu", "cpu,cpuacct")
_V1_CPUACCT_DIRS = ("cpuacct", "cpu,cpuacct")


def get_cpu_quota_cores() -> float:
    """CPU quota in fractional cores (e.g. 19.2), else os.cpu_count()."""
    parts = (_read("cpu.max") or "").split()
    if len(parts) == 2 and parts[0] != "max":
        try:
            return max(0.01, int(parts[0]) / int(parts[1]))
        except (ValueError, ZeroDivisionError):
            pass
    for d in _V1_CPU_DIRS:
        quota = _read_int(f"{d}/cpu.cfs_quota_us")
        if quota is None:
            continue
        if quota <= 0:  # -1 = no limit
            break
        period = _read_int(f"{d}/cpu.cfs_period_us")
        if period:
            return max(0.01, quota / period)
        break
    return float(os.cpu_count() or 1)


def _cpu_usage_ns() -> Optional[int]:
    stat = _read("cpu.stat")
    if stat:
        for line in stat.splitlines():
            if line.startswith("usage_usec"):
                try:
                    return int(line.split()[1]) * 1000
                except (ValueError, IndexError):
                    break
    for d in _V1_CPUACCT_DIRS:
        usage = _read_int(f"{d}/cpuacct.usage")
        if usage is not None:
            return usage
    return None


def get_container_cpu_usage() -> Optional[dict]:
    """CPU percent (of the quota) and core count, or None to fall back."""
    now = time.monotonic()
    usage_ns = _cpu_usage_ns()
    if usage_ns is None:
        return None

    # The quota, not os.cpu_count(): the host's core count is misleading inside
    # a container with a CPU quota.
    quota_cores = get_cpu_quota_cores()

    elapsed = now - _cpu_prev["timestamp"]
    if _cpu_prev["timestamp"] > 0 and elapsed > 0.5:
        delta_usage = usage_ns - _cpu_prev["usage"]
        percent = (delta_usage / (elapsed * 1e9)) * 100.0 / quota_cores
        _cpu_prev["percent"] = max(0.0, min(percent, 100.0))

    _cpu_prev["timestamp"] = now
    _cpu_prev["usage"] = usage_ns

    return {"percent": round(_cpu_prev["percent"], 1), "count": int(quota_cores)}


def _mount_fstype(path: str) -> Optional[str]:
    """Filesystem type of the mount holding `path` (the longest matching mount point)."""
    try:
        with open(MOUNTS) as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    path = os.path.realpath(path)
    best, fstype = "", None
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mnt = parts[1].replace("\\040", " ")
        if (path == mnt or path.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best):
            best, fstype = mnt, parts[2]
    return fstype


def disk_usage(path: str) -> dict:
    """total/used/percent for the filesystem holding `path`, or available=False with a reason
    when the numbers would describe the host's filesystem rather than this instance's disk."""
    fstype = _mount_fstype(path)
    if fstype in _HOST_SERVED_FS:
        return {"available": False, "total": None, "used": None, "percent": None,
                "reason": f"{path} is host-served ({fstype}); its size and usage are the host's, "
                          "not this instance's disk allocation"}
    st = os.statvfs(path)
    total = st.f_blocks * st.f_frsize
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    # Same arithmetic as psutil.disk_usage: percent of the space a non-root user can reach.
    avail = st.f_bavail * st.f_frsize
    reachable = used + avail
    return {"available": True, "total": total, "used": used,
            "percent": round(used / reachable * 100, 1) if reachable else 0.0}
