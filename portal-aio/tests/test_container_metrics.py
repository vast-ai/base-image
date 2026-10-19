"""cgroup readers behind /system-metrics, against three fixture trees.

runc hosts expose cgroup v2; the gVisor sandbox exposes v1 only, with
pids.max = max and /proc/meminfo's MemTotal equal to the memory limit; and a
sandbox may expose no cgroup filesystem at all.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capabilities import container_metrics as cm

GiB = 1024**3
V1_UNLIMITED = "9223372036854771712"


def _tree(root, files: dict):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content + "\n")


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Point the module at an empty cgroup root and a MemTotal of our choosing."""
    cg = tmp_path / "cgroup"
    cg.mkdir()
    meminfo = tmp_path / "meminfo"
    monkeypatch.setattr(cm, "CGROUP_ROOT", str(cg))
    monkeypatch.setattr(cm, "MEMINFO", str(meminfo))
    monkeypatch.setattr(cm, "_cpu_prev", {"timestamp": 0.0, "usage": 0, "percent": 0.0})

    def build(files: dict, mem_total: int):
        _tree(cg, files)
        meminfo.write_text(f"MemTotal:       {mem_total // 1024} kB\nMemFree:        1024 kB\n")
        return cg

    return build


# --- v2 host (runc) ---------------------------------------------------------- #

def test_v2_host_prefers_memory_low(fake):
    fake({
        "cgroup.controllers": "cpuset cpu io memory pids",
        "memory.low": str(16 * GiB),
        "memory.max": str(48 * GiB),
        "memory.current": str(4 * GiB),
        "cpu.max": "1200000 100000",
        "cpu.stat": "usage_usec 5000000\nuser_usec 4000000",
        "pids.max": "2816",
    }, mem_total=512 * GiB)
    assert cm.is_in_container()
    stats = cm.get_container_memory_stats()
    assert stats == {"total": 16 * GiB, "used": 4 * GiB, "percent": 25.0}
    assert cm.get_cpu_quota_cores() == 12.0
    assert cm.get_container_cpu_usage() == {"percent": 0.0, "count": 12}


def test_v2_host_unlimited_falls_back(fake):
    fake({
        "memory.low": "0",
        "memory.max": "max",
        "memory.current": str(4 * GiB),
        "cpu.max": "max 100000",
    }, mem_total=512 * GiB)
    assert cm.get_container_memory_limit() is None
    assert cm.get_container_memory_stats() is None
    assert cm.get_cpu_quota_cores() == float(os.cpu_count() or 1)


# --- v1 sandbox (gVisor) ----------------------------------------------------- #

GVISOR_V1 = {
    "memory/memory.limit_in_bytes": str(64 * GiB),
    "memory/memory.soft_limit_in_bytes": V1_UNLIMITED,
    "memory/memory.usage_in_bytes": str(8 * GiB),
    "cpu/cpu.cfs_quota_us": "3100000",
    "cpu/cpu.cfs_period_us": "100000",
    "cpuacct/cpuacct.usage": "123456789",
    "pids/pids.max": "max",
}


def test_v1_sandbox_limit_equal_to_memtotal_is_real(fake):
    # MemTotal == the limit under gVisor; that limit must not be rejected.
    fake(GVISOR_V1, mem_total=64 * GiB)
    assert cm.is_in_container()
    assert cm.get_container_memory_limit() == 64 * GiB
    assert cm.get_container_memory_stats() == {
        "total": 64 * GiB, "used": 8 * GiB, "percent": 12.5,
    }


def test_v1_sandbox_cpu_quota_and_usage(fake):
    fake(GVISOR_V1, mem_total=64 * GiB)
    assert cm.get_cpu_quota_cores() == 31.0
    assert cm.get_container_cpu_usage() == {"percent": 0.0, "count": 31}


def test_v1_limit_above_memtotal_is_not_real(fake):
    files = dict(GVISOR_V1)
    files["memory/memory.limit_in_bytes"] = str(128 * GiB)
    fake(files, mem_total=64 * GiB)
    assert cm.get_container_memory_limit() is None
    assert cm.get_container_memory_stats() is None


# --- no cgroup filesystem ---------------------------------------------------- #

def test_no_cgroup_falls_back_cleanly(fake, monkeypatch, tmp_path):
    fake({}, mem_total=64 * GiB)
    monkeypatch.setattr(cm, "CGROUP_ROOT", str(tmp_path / "absent"))
    assert not cm.is_in_container()
    assert cm.get_container_memory_limit() is None
    assert cm.get_container_memory_stats() is None
    assert cm.get_container_cpu_usage() is None
    assert cm.get_cpu_quota_cores() == float(os.cpu_count() or 1)
