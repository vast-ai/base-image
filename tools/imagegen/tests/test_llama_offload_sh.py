"""Tests for llama.d/11-llama-offload.sh — the CUDA-offload assertion (L076, ADR 0033).

WHY THIS FILE EXISTS. This assertion is REQUIRED on every llama-cpp QA cell, so a
false red blocks promotion of the whole image and a false green certifies an image
serving from CPU. It has been wrong in both directions already, each time found only
by a live dispatch: it first gated on a llama.cpp log format upstream had removed, then
on `mib > 0` which a bare CUDA context satisfies, then on a VRAM floor that was never
derived because the model search failed — announcing it could not decide and passing
anyway.

The branch this file exists for is the one a live run CANNOT be relied on to reach.
On run 32846852617 nvidia-smi reported HOST pid 1041871 while the container saw 1010,
so pid attribution failed and the check fell back to summing compute-app memory. On the
very next dispatch all four cells drew unskewed hosts and took the strong path, so the
fallback never executed. Host class is not something a test should have to hope for.

Needs no GPU and no container: given (compute-app rows, pid, device memory, model size)
the verdict is deterministic, so stubbing nvidia-smi/pgrep/llama-server on PATH is
enough. Same approach as test_instance_test_lib_sh.py.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / \
    "derivatives/llama-cpp/ROOT/opt/instance-tools/tests/llama.d/11-llama-offload.sh"

# Only what the script actually calls. Each helper keeps the real contract: fail_later
# RECORDS, report_failures is what turns records into a failing exit (L062).
LIB = r'''#!/bin/bash
FAILURES=()
test_pass()  { echo "PASS: $*"; exit 0; }
test_fail()  { echo "FAIL: $*"; exit 1; }
test_skip()  { echo "SKIP: $*"; exit 77; }
fail_later() { FAILURES+=("$1"); echo "FAIL: $1: $2"; }
report_failures() { (( ${#FAILURES[@]} )) && { echo "FAILED: ${FAILURES[*]}"; exit 1; }; return 0; }
has_gpu() { return 0; }
assert_service_running() { echo "  service $1 running"; }
wait_for_url() { return 0; }
'''

DEVICES_OK = "Available devices:\n  CUDA0: NVIDIA GeForce RTX 3080 (9877 MiB, 8812 MiB free)"
DEVICES_NONE = "Available devices:\n  (none)"


def run(tmp_path, *, apps, pid, dev_used="845", devices=DEVICES_OK, model_mib=644):
    """Stage the script with stubbed nvidia-smi/pgrep/llama-server and run it."""
    tests = tmp_path / "tests"
    (tests / "llama.d").mkdir(parents=True)
    (tests / "lib.sh").write_text(LIB)
    target = tests / "llama.d" / "11-llama-offload.sh"
    target.write_text(SCRIPT.read_text())
    target.chmod(0o755)

    bin_ = tmp_path / "bin"
    bin_.mkdir()
    # --query-compute-apps and --query-gpu are distinguished the way the script calls them.
    (bin_ / "nvidia-smi").write_text(
        "#!/bin/bash\n"
        f'case "$*" in\n'
        f'  *compute-apps*) printf %b "{apps}"; [ -n "{apps}" ] && echo ;;\n'
        f'  *memory.used*)  echo "{dev_used}" ;;\n'
        "esac\nexit 0\n")
    (bin_ / "pgrep").write_text(f'#!/bin/bash\n[ -n "{pid}" ] && echo "{pid}"\nexit 0\n')
    (bin_ / "llama-server").write_text(f'#!/bin/bash\nprintf "%b\\n" "{devices}"\nexit 0\n')
    for f in bin_.iterdir():
        f.chmod(0o755)

    ws = tmp_path / "workspace"
    (ws / "llama.cpp").mkdir(parents=True)
    if model_mib:
        # Sparse: find -printf '%s' reports apparent size, so no real bytes are written.
        subprocess.run(["truncate", "-s", f"{model_mib}M",
                        str(ws / "llama.cpp" / "model.gguf")], check=True)

    env = dict(os.environ)
    env.update(PATH=f"{bin_}:{env['PATH']}", LLAMA_MODEL="test/model",
               WORKSPACE=str(ws), HOME=str(tmp_path / "home"))
    env.pop("LLAMA_ARGS", None)
    return subprocess.run(["bash", str(target)], capture_output=True, text=True, env=env)


def test_pid_matches_above_floor_passes(tmp_path):
    """The strong path: attribution succeeds and residency clears the model-derived floor."""
    r = run(tmp_path, apps="762, 770 MiB", pid="762")
    assert r.returncode == 0, r.stdout
    assert "holds 770 MiB" in r.stdout


def test_pid_namespace_skew_falls_back_to_the_total(tmp_path):
    """THE branch a live run cannot be relied on to reach. Measured on run 32846852617:
    nvidia-smi reported host pid 1041871 while the container saw 1010, on a two-GPU box
    where one process held 530 and 634 MiB. Attribution is impossible; residency is not,
    and 1164 MiB against a 322 MiB floor is real evidence."""
    r = run(tmp_path, apps="1041871, 530 MiB\\n1041871, 634 MiB", pid="1010")
    assert r.returncode == 0, r.stdout
    assert "attributed by TOTAL" in r.stdout
    assert "1164 MiB resident" in r.stdout


def test_skew_below_floor_still_fails(tmp_path):
    """The fallback must not become an escape hatch. Same unattributable shape, but no
    process holds enough for the weights — that is a context-only server either way."""
    r = run(tmp_path, apps="1041871, 208 MiB", pid="1010")
    assert r.returncode == 1, r.stdout
    assert "offload-below-floor" in r.stdout


def test_ngl_zero_fails_when_attributed(tmp_path):
    """The negative control, replayed: -ngl 0 held 208 MiB of bare CUDA context against a
    322 MiB floor on run 32841313361. `mib > 0` passed this; the floor must not."""
    r = run(tmp_path, apps="914, 208 MiB", pid="914")
    assert r.returncode == 1, r.stdout
    assert "offload-below-floor" in r.stdout


def test_no_compute_apps_but_device_loaded_warns(tmp_path):
    """Empty list beside a loaded device is NVML process enumeration being unavailable,
    not a CPU fallback. Warn — the device disagrees with the process table."""
    r = run(tmp_path, apps="", pid="762", dev_used="845")
    assert r.returncode == 0, r.stdout
    assert "enumeration is unavailable" in r.stdout


def test_no_compute_apps_and_idle_device_fails(tmp_path):
    """Empty list beside an idle device IS the silent CPU fallback."""
    r = run(tmp_path, apps="", pid="762", dev_used="3")
    assert r.returncode == 1, r.stdout
    assert "offload-no-gpu-process" in r.stdout


def test_non_numeric_memory_warns_rather_than_reporting_zero(tmp_path):
    """`[N/A]` on MIG/vGPU is the driver declining to answer. Reporting it as zeroed
    weights sends the reader to the wrong place."""
    r = run(tmp_path, apps="762, [N/A]", pid="762")
    assert r.returncode == 0, r.stdout
    assert "did not report a memory figure" in r.stdout


def test_missing_model_fails_rather_than_passing_unjudged(tmp_path):
    """The defect the negative control exposed: with no floor derivable the check
    announced it could not decide and passed anyway, silently restoring `mib > 0`."""
    r = run(tmp_path, apps="914, 208 MiB", pid="914", model_mib=0)
    assert r.returncode == 1, r.stdout
    assert "offload-no-floor" in r.stdout


def test_no_gpu_device_enumerated_fails(tmp_path):
    """Arm A: the backend cannot dlopen at all, so the image serves on CPU."""
    r = run(tmp_path, apps="", pid="", dev_used="3", devices=DEVICES_NONE)
    assert r.returncode == 1, r.stdout
    assert "offload-no-gpu-device" in r.stdout


def test_error_text_naming_a_cuda_bundle_path_is_not_a_device(tmp_path):
    """ADR 0033 names the bundles `x64-cuda12-portable`, and ggml prints the .so path on
    FAILURE into the same stream. An unanchored case-insensitive match would find
    "cuda12" there and report a GPU on the exact state arm A looks for."""
    r = run(tmp_path, apps="", pid="", dev_used="3",
            devices="failed to load backend from /opt/llama.cpp/x64-cuda12-portable/libggml-cuda.so\\nAvailable devices:\\n  (none)")
    assert r.returncode == 1, r.stdout
    assert "offload-no-gpu-device" in r.stdout
