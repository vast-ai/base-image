#!/bin/bash
# Test: GPU, CUDA toolkit selection, and forward compatibility.
source "$(dirname "$0")/../lib.sh"

has_gpu || test_skip "no GPU detected"

# ── Basic GPU info ────────────────────────────────────────────────────

nvidia-smi &>/dev/null || test_fail "nvidia-smi failed"

gpu_name=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
compute_cap=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1)
driver_ver=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)

# nvidia-smi reports the *effective* CUDA version, which includes compat libs if
# loaded. Every compat check below needs the driver's NATIVE maximum instead, so
# it asks the helper for exactly that — the same call, in the same mode, that
# 05-configure-cuda.sh used to pick the toolkit.
#
# Deliberately not re-derived here. Both files used to open-code the bypass as
# `LD_LIBRARY_PATH=<dir>`, which is a search hint: name a directory with no
# loadable libcuda.so.1 and the loader carries on to the ld.so cache — i.e. to a
# previous boot's compat lib, silently, and this test would then AGREE with a
# boot that got it wrong. A mirrored heuristic is not an independent check.
# --native answers or it fails; it never guesses.
native_driver_cuda=$(/opt/instance-tools/bin/cuda-driver-version --native 2>/dev/null || true)
# Also grab the effective (possibly compat-enhanced) version for reference
effective_cuda=$(/opt/instance-tools/bin/cuda-driver-version 2>/dev/null || true)

echo "  GPU: ${gpu_name} (CC ${compute_cap}, Driver ${driver_ver})"
echo "  native driver CUDA: ${native_driver_cuda}, effective: ${effective_cuda}"

# UNKNOWN IS A FAILURE, not a quiet pass.
#
# Every forward-compat check below compares against native_driver_cuda. When it is
# empty, version_gt returns false (correctly — "unknown" must not assert that
# compat is needed), toolkit_needs_compat becomes 0, and the ENTIRE validation
# block is skipped. The test then prints
#
#   GPU/CUDA verified (selected: ..., native: , effective: )
#
# and exits 0 — which is verbatim the log line the driver-610 incident left
# behind. Without this assertion the change that hardened version_gt would have
# made that state quieter than it was before, not louder.
if [[ ! "$native_driver_cuda" =~ ^[0-9]+\.[0-9]+$ ]]; then
    fail_later "driver-cuda-version" \
        "could not determine the driver CUDA version (got '${native_driver_cuda}') — \
no libcuda could be proven native, so every compat check below is vacuous"
fi

# 05-configure-cuda.sh aborts SAFELY when it cannot determine the driver version:
# it changes nothing, which is right, but it also means the instance runs on the
# image-default toolkit with no compat and nothing chose it. That state used to
# be caught only by accident — the symlink assertion below notices it just when
# the image happens to ship an indirect /usr/local/cuda. The boot script now
# leaves a breadcrumb, so the abort is asserted directly rather than inferred.
if [[ -f /run/vast-cuda-config-failed ]]; then
    fail_later "cuda-config" \
        "05-configure-cuda.sh aborted this boot: $(head -1 /run/vast-cuda-config-failed) \
— the CUDA toolkit in use was never validated against this host's driver"
fi

# ── CUDA toolkit selection ────────────────────────────────────────────

# Collect installed CUDA versions (same logic as 05-configure-cuda.sh)
cuda_versions=()
for dir in /usr/local/cuda-*/; do
    [[ -d "$dir" ]] || continue
    ver=$(basename "$dir" | sed 's/cuda-//')
    [[ "$ver" =~ ^[0-9]+\.[0-9]+$ ]] && cuda_versions+=("$ver")
done

if [[ ${#cuda_versions[@]} -eq 0 ]]; then
    echo "  no CUDA toolkits installed (skip toolkit/compat checks)"
    python3 -c "import ctypes; ctypes.CDLL('libcuda.so.1')" 2>/dev/null \
        && echo "  python ctypes: libcuda.so.1 loadable" \
        || echo "  WARN: python cannot load libcuda.so.1"
    # EVERY exit path must report deferred failures first. This early exit is a
    # second `test_pass`, and anything fail_later recorded above (the
    # driver-version assertion) would be silently dropped here — the same bug
    # that made the first version of the reachability check print FAIL and exit
    # 0. Reached on the stock-* configs, which ship no CUDA toolkit at all.
    report_failures
    test_pass "GPU present, no CUDA toolkit installed"
fi

readarray -t cuda_versions < <(printf '%s\n' "${cuda_versions[@]}" | sort -t. -k1,1nr -k2,2nr)

# ── The CUDA toolkit's libraries must be REACHABLE, not merely present ──
#
# A toolkit on disk that the dynamic loader cannot find is invisible to every
# check below, because they all go through the driver API or through torch —
# and torch ships its OWN CUDA libraries in the venv, so it keeps working while
# the system toolkit is unreachable. The only casualties are the few things that
# link the system libs (torchcodec via libnppicc, for one), which surface much
# later and look like an unrelated application bug.
#
# That is not hypothetical: 05-configure-cuda.sh deleted every CUDA entry from
# ld.so.conf.d and then aborted before writing the replacement whenever it could
# not parse a CUDA version out of nvidia-smi. Instances booted with no system
# CUDA library path at all, and this test passed anyway. The verdict it produced
# — "GPU/CUDA verified (selected: /etc/alternatives/cuda, native: , effective: )"
# — was the only trace.
#
# Cheap and unambiguous: ask the loader, not the filesystem.
# Ask the loader directly: does its cache resolve ANY library out of the CUDA
# toolkit tree? That is precisely the property at stake, and it needs no guess
# about which soname to probe.
#
# (An earlier version probed a specific soname with `grep -F " libfoo.so"`. The
# leading space never matched, because `ldconfig -p` indents with a TAB — so it
# reported EVERY image as broken, healthy ones included. It was only caught by
# checking the passing direction as well as the failing one.)
_cuda_lib_dir_reachable=false
# Match a real TOOLKIT library, not merely any path under the tree. The bare
# "=> /usr/local/cuda" form was also satisfied by /usr/local/cuda/lib64/stubs
# (shipped by unsloth-studio and aio-studio) and by .../compat/ — neither of
# which contains libnppicc or any other toolkit runtime, i.e. both would report
# "reachable" in exactly the broken state this exists to catch.
if ldconfig -p 2>/dev/null | grep -qE "libcudart\.so[^ ]* .*=> +/usr/local/cuda"; then
    _cuda_lib_dir_reachable=true
fi

if ! $_cuda_lib_dir_reachable; then
    fail_later "cuda-libpath" \
        "a CUDA toolkit is installed but NONE of its libraries are on the loader path — \
ld.so.conf.d has no working CUDA entry (see 05-configure-cuda.sh). torch will still work \
from its bundled libs, so this stays invisible until something links the system toolkit."
else
    echo "  system CUDA libraries: reachable via ldconfig"
fi
latest_cuda="${cuda_versions[0]}"
echo "  installed CUDA: ${cuda_versions[*]} (latest: ${latest_cuda})"

# /usr/local/cuda must be a symlink (created by 05-configure-cuda.sh)
if [[ -L /usr/local/cuda ]]; then
    selected_cuda=$(readlink /usr/local/cuda | sed 's|.*/cuda-||')
    # The sed is a no-op when the symlink is INDIRECT (e.g. the distro's
    # /etc/alternatives/cuda), so selected_cuda can be a path rather than X.Y —
    # which is verbatim what the driver-610 incident logged. version_gt rejects
    # a non-numeric operand, so without this every cross-major comparison below
    # silently reads "correct fallback" and the three test_fail assertions
    # become unreachable. Guarding native_driver_cuda alone was not enough: this
    # is the value the incident actually produced.
    selected_cuda_is_version=true
    if [[ ! "$selected_cuda" =~ ^[0-9]+\.[0-9]+$ ]]; then
        selected_cuda_is_version=false
        fail_later "cuda-symlink" \
            "/usr/local/cuda does not resolve to a cuda-X.Y directory (got '${selected_cuda}') — \
05-configure-cuda.sh did not run to completion, so no toolkit selection was validated"
    fi
    cuda_target=$(readlink -f /usr/local/cuda)
    [[ -d "$cuda_target" ]] || test_fail "/usr/local/cuda symlink target does not exist: $cuda_target"
    echo "  selected CUDA: ${selected_cuda} (symlink → ${cuda_target})"
else
    test_fail "/usr/local/cuda is not a symlink (05-configure-cuda.sh should have created it)"
fi

# ── Forward compatibility validation ──────────────────────────────────

# Forward compat is attempted by 05-configure-cuda.sh when:
#   1. The latest installed CUDA toolkit > native driver CUDA max
#   2. Compat libs exist in cuda-X.Y/compat/
#   3. cuInit(0) succeeds when loaded from the compat dir (datacenter GPUs only)
#   4. DISABLE_FORWARD_COMPAT is not "true"
#
# IMPORTANT: Within the same CUDA major version, minor version compatibility
# is guaranteed by NVIDIA. A 12.8 driver can run 12.9 toolkit code without
# forward compat libs. Forward compat is only *required* across major versions.
#
# We test against native_driver_cuda (not effective_cuda) to detect whether
# forward compat was needed and correctly applied.

compat_conf="/etc/ld.so.conf.d/0-compat-cuda.conf"
compat_dir="/usr/local/cuda-${latest_cuda}/compat"
toolkit_needs_compat=$(version_gt "$latest_cuda" "$native_driver_cuda" && echo "1" || echo "0")
native_major=${native_driver_cuda%%.*}
latest_major=${latest_cuda%%.*}

if [[ "$toolkit_needs_compat" == "1" ]]; then
    if [[ "$native_major" == "$latest_major" ]]; then
        echo "  latest CUDA ${latest_cuda} > native driver max ${native_driver_cuda} (same major — minor compat ok)"
    else
        echo "  latest CUDA ${latest_cuda} > native driver max ${native_driver_cuda}: forward compat required (major version mismatch)"
    fi

    if [[ "${DISABLE_FORWARD_COMPAT:-false}" == "true" ]]; then
        echo "  DISABLE_FORWARD_COMPAT=true — forward compat intentionally disabled"
        if [[ "$native_major" == "$latest_major" ]]; then
            echo "  same major version (${native_major}) — minor compat sufficient"
        elif [[ "$selected_cuda_is_version" != true ]]; then
            # version_gt rejects a non-numeric operand, so "not greater" here would
            # be an artefact of the bad value, not a correct fallback. The
            # cuda-symlink failure above already carries the verdict; do not print
            # a reassurance on top of it.
            echo "  selected CUDA '${selected_cuda}' is not a version — see cuda-symlink above"
        elif ! version_gt "$selected_cuda" "$native_driver_cuda"; then
            echo "  selected CUDA ${selected_cuda} <= native max ${native_driver_cuda}: correct fallback"
        else
            test_fail "forward compat disabled but selected CUDA ${selected_cuda} > native max ${native_driver_cuda} (major version mismatch)"
        fi
    elif [[ -d "$compat_dir" ]] && compgen -G "$compat_dir/libcuda.so.*" > /dev/null; then
        # Compat libs exist — check if they were activated
        if [[ -f "$compat_conf" ]]; then
            compat_path=$(cat "$compat_conf")
            echo "  forward compat ENABLED: ${compat_path}"
            # Selected CUDA should be the latest (compat allows it)
            [[ "$selected_cuda" == "$latest_cuda" ]] \
                || echo "  WARN: compat enabled but selected CUDA ${selected_cuda} != latest ${latest_cuda}"
            # Verify compat libs are in the linker cache
            if ldconfig -p 2>/dev/null | grep -q "$compat_dir"; then
                echo "  compat libs in ldconfig cache"
            else
                test_fail "compat conf exists but libs not in ldconfig cache (ldconfig may not have run)"
            fi
            # Effective CUDA should now be >= latest toolkit
            if [[ "$effective_cuda" == "$latest_cuda" ]]; then
                echo "  effective CUDA ${effective_cuda} matches latest toolkit"
            else
                echo "  WARN: effective CUDA ${effective_cuda} != latest ${latest_cuda}"
            fi
            # Verify cuInit works through compat
            if LD_LIBRARY_PATH="$compat_dir" python3 -c "
import ctypes, sys
sys.exit(0 if ctypes.CDLL('libcuda.so.1').cuInit(0) == 0 else 1)
" 2>/dev/null; then
                echo "  cuInit succeeds via compat libs"
            else
                test_fail "forward compat enabled but cuInit fails"
            fi
        else
            # Compat libs exist but weren't activated — cuInit likely failed (consumer GPU)
            echo "  compat libs present at ${compat_dir} but not activated"
            echo "  (consumer GPU or cuInit failed — expected on non-datacenter hardware)"
            if [[ "$native_major" == "$latest_major" ]]; then
                echo "  same major version (${native_major}) — minor compat sufficient, no compat libs needed"
            elif [[ "$selected_cuda_is_version" != true ]]; then
                # version_gt rejects a non-numeric operand, so "not greater" here would
                # be an artefact of the bad value, not a correct fallback. The
                # cuda-symlink failure above already carries the verdict; do not print
                # a reassurance on top of it.
                echo "  selected CUDA '${selected_cuda}' is not a version — see cuda-symlink above"
            elif ! version_gt "$selected_cuda" "$native_driver_cuda"; then
                echo "  selected CUDA ${selected_cuda} <= native max ${native_driver_cuda}: correct fallback"
            else
                test_fail "compat not activated and major version mismatch: selected CUDA ${selected_cuda} > native max ${native_driver_cuda}"
            fi
        fi
    else
        # No compat libs at all
        echo "  no compat libs in ${compat_dir}"
        if [[ "$native_major" == "$latest_major" ]]; then
            echo "  same major version (${native_major}) — minor compat sufficient, no compat libs needed"
        elif [[ "$selected_cuda_is_version" != true ]]; then
            # version_gt rejects a non-numeric operand, so "not greater" here would
            # be an artefact of the bad value, not a correct fallback. The
            # cuda-symlink failure above already carries the verdict; do not print
            # a reassurance on top of it.
            echo "  selected CUDA '${selected_cuda}' is not a version — see cuda-symlink above"
        elif ! version_gt "$selected_cuda" "$native_driver_cuda"; then
            echo "  selected CUDA ${selected_cuda} <= native max ${native_driver_cuda}: correct fallback"
        else
            test_fail "no compat libs and major version mismatch: selected CUDA ${selected_cuda} > native max ${native_driver_cuda}"
        fi
    fi
else
    echo "  latest CUDA ${latest_cuda} <= native driver max ${native_driver_cuda}: no forward compat needed"
    if [[ -f "$compat_conf" ]]; then
        echo "  WARN: forward compat conf exists but shouldn't be needed"
    fi
fi

# ── Standard CUDA checks ─────────────────────────────────────────────

if command -v nvcc &>/dev/null; then
    echo "  nvcc: $(nvcc --version 2>&1 | grep -oP 'release \K[0-9.]+' || nvcc --version 2>&1 | tail -1)"
else
    echo "  WARN: nvcc not found in PATH"
fi

if [[ -f /etc/ld.so.conf.d/10-cuda.conf ]]; then
    echo "  10-cuda.conf: $(cat /etc/ld.so.conf.d/10-cuda.conf)"
else
    echo "  WARN: /etc/ld.so.conf.d/10-cuda.conf not present"
fi

if ldconfig -p 2>/dev/null | grep -q libcudart; then
    echo "  libcudart found in ldconfig"
else
    echo "  WARN: libcudart not in ldconfig cache"
fi

python3 -c "import ctypes; ctypes.CDLL('libcuda.so.1')" 2>/dev/null \
    && echo "  python ctypes: libcuda.so.1 loadable" \
    || echo "  WARN: python cannot load libcuda.so.1"

if [[ -f /etc/OpenCL/vendors/nvidia.icd ]]; then
    echo "  OpenCL ICD present"
else
    echo "  absent (ok): OpenCL ICD"
fi

# Deferred failures (fail_later) are DISCARDED unless this is called: the helper
# only records them, and test_pass exits 0 regardless. Adding the reachability
# check above without this line made it print a FAIL and still pass the test —
# caught by running it, not by reading it. Gated by L062.
report_failures

test_pass "GPU/CUDA verified (selected: ${selected_cuda}, native: ${native_driver_cuda}, effective: ${effective_cuda})"
