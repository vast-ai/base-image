"""Functional gate for ROOT/opt/instance-tools/bin/cuda-driver-version.

The helper decides which libcuda.so.1 the loader is allowed to answer from, so
the only honest test is one that puts real shared objects at real absolute paths
and rebuilds the loader cache. That needs a throwaway root, hence a container.

Why this is worth a container in an otherwise-fast suite: the boot script picks
the CUDA toolkit from this number, and the version of that resolution that lived
in bash (``LD_LIBRARY_PATH=<dir>``) failed OPEN — LD_LIBRARY_PATH is a search
hint, so naming a directory with no loadable libcuda.so.1 sent the loader on to
the ld.so cache, i.e. to a previous boot's forward-compat library. Nothing in a
pure-python test can catch that; it is a property of the dynamic loader.

Skips (loudly) where docker is unavailable — GitHub's ubuntu runners have it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HARNESS_DIR = Path(__file__).resolve().parent / "harness"

HELPER = REPO / "ROOT/opt/instance-tools/bin/cuda-driver-version"
BOOT = REPO / "ROOT/etc/vast_boot.d/05-configure-cuda.sh"
GPU_TEST = REPO / "ROOT/opt/instance-tools/tests/base/60-gpu-cuda.sh"
LIB = REPO / "ROOT/opt/instance-tools/tests/lib.sh"

IMAGE = "ubuntu:24.04"
SETUP = (
    "apt-get update -qq >/dev/null 2>&1 && "
    "apt-get install -y -qq gcc python3 >/dev/null 2>&1 && "
    "bash /harness.sh"
)


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(
        ["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0


requires_docker = pytest.mark.skipif(
    not _docker_available(),
    reason="docker unavailable — cannot fabricate libcuda.so.1 at absolute paths",
)


def _run_harness(harness: str, mounts: dict[Path, str]) -> str:
    """Run a harness in a throwaway container with the real files mounted."""
    path = HARNESS_DIR / harness
    assert path.is_file(), path
    args = ["docker", "run", "--rm", "-v", f"{path}:/harness.sh:ro"]
    for src, dest in mounts.items():
        assert src.is_file(), src
        args += ["-v", f"{src}:{dest}:ro"]
    proc = subprocess.run(
        args + [IMAGE, "bash", "-c", SETUP],
        capture_output=True, text=True, timeout=600,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output
    assert "ALL SCENARIOS OK" in output, output
    return output


@requires_docker
def test_cuda_driver_version_native_resolution():
    _run_harness(
        "cuda-driver-version-harness.sh",
        {HELPER: "/opt/instance-tools/bin/cuda-driver-version"},
    )


@requires_docker
def test_boot_and_gpu_test_agree():
    """The boot script and the test that gates it, run as a pair.

    Either alone can look correct while the pair is broken: the defect this
    guards is the two AGREEING on a wrong driver version — a boot that picks the
    wrong toolkit and a test that reads the same wrong value and calls it
    healthy. It also pins the abort path, which must change nothing and must be
    detected on purpose rather than by luck.
    """
    _run_harness(
        "cuda-boot-and-test-harness.sh",
        {
            HELPER: "/opt/instance-tools/bin/cuda-driver-version",
            BOOT: "/etc/vast_boot.d/05-configure-cuda.sh",
            GPU_TEST: "/opt/instance-tools/tests/base/60-gpu-cuda.sh",
            LIB: "/opt/instance-tools/tests/lib.sh",
        },
    )
