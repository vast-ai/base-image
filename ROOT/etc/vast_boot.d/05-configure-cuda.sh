#!/bin/bash

# CUDA Forward Compatibility: datacenter GPUs with Volta+ and compat libs present
# Consumer class GPUs can rely on minor version compatibility, but forward compatibility must be removed first

try_forward_compat() {
    local LATEST_CUDA="$1" MAX_CUDA="$2"

    [[ -z "$LATEST_CUDA" || -z "$MAX_CUDA" ]] && return 1
    
    [[ "${DISABLE_FORWARD_COMPAT:-false}" == "true" ]] && return 1
    awk "BEGIN {exit !($LATEST_CUDA > $MAX_CUDA)}" || return 1
    
    local COMPAT_DIR="/usr/local/cuda-${LATEST_CUDA}/compat"
    [[ -d "$COMPAT_DIR" ]] || return 1
    compgen -G "$COMPAT_DIR/libcuda.so.*" > /dev/null || return 1
    
    LD_LIBRARY_PATH="$COMPAT_DIR" python3 -c "
import sys, ctypes
sys.exit(0 if ctypes.CDLL('libcuda.so.1').cuInit(0) == 0 else 1)
" 2>/dev/null || return 1
    
    echo "$COMPAT_DIR" > /etc/ld.so.conf.d/0-compat-cuda.conf
    return 0
}

configure_cuda() {
    command -v nvidia-smi &> /dev/null || return 0

    # ── GATHER AND VALIDATE BEFORE MUTATING ANYTHING ────────────────────
    #
    # Everything below is destructive: it deletes every CUDA entry from
    # ld.so.conf.d and rebuilds the loader cache. Until 2026-08-12 that deletion
    # happened FIRST and the driver-version parse was validated after, with a
    # bare `return 1` on failure — so a boot where `nvidia-smi` did not print its
    # "CUDA Version: X.Y" banner left the container with NO system CUDA library
    # path at all, and nothing put it back.
    #
    # Not a rare parse quirk: this is the FIRST boot script, nothing waits for
    # the driver, and nvidia-smi is at its least reliable that early.
    #
    # Also near-invisible. torch ships its own CUDA libraries in the venv, so
    # torch, the GPU tests and CUDA compute all keep working; only something
    # linking the SYSTEM toolkit notices. It surfaced as torchcodec failing to
    # load libnppicc — three layers from the cause, and read at first as an
    # unrelated torchaudio failure on a flaky host.
    #
    # base/60-gpu-cuda now asserts the toolkit is reachable, so the broken state
    # can never again pass for healthy.
    # Driver API, not prose. See /opt/instance-tools/bin/cuda-driver-version for
    # why: driver 610 renamed nvidia-smi's "CUDA Version" field to "CUDA UMD
    # Version" and broke every scrape of it fleet-wide.
    #
    # PINNED TO THE NATIVE DRIVER, and that pin is load-bearing.
    #
    # cuDriverGetVersion reports whichever libcuda.so.1 the loader resolves. If a
    # PREVIOUS boot enabled forward compat it wrote /etc/ld.so.conf.d/0-compat-cuda.conf
    # (named "0-" so it wins), and /etc and the loader cache persist across a
    # stop/start — so an unpinned probe here would return the COMPAT version, not
    # the driver's own.
    #
    # That is not hypothetical: with the value inflated to the toolkit's own
    # version, try_forward_compat's "latest > max" test goes false, compat is NOT
    # re-enabled, and the cleanup below has already deleted the conf that was
    # making the instance work. A cross-major instance would come back from a
    # restart with a newer toolkit, an older driver and no compat — worse than the
    # bug this file was changed to fix, and invisible to CI because the QA gate
    # only ever boots an instance once.
    #
    # Reading it BEFORE the cleanup (which is what makes the abort path safe) is
    # therefore only correct with the compat path excluded explicitly. Same bypass
    # base/60-gpu-cuda uses to get its native reading.
    # THE PIN MUST FAIL CLOSED. LD_LIBRARY_PATH is a search HINT, not a pin: if
    # the directory named does not yield a loadable libcuda.so.1, the loader
    # carries on to the ld.so cache — which on a second boot is the previous
    # boot's compat entry, i.e. exactly the wrong answer, silently. So an empty
    # or unusable pin is treated as "unknown", never as "probe it anyway".
    #
    # Current-ABI directory first, then a general search: a host with 32-bit
    # driver libs mounted (NVIDIA_DRIVER_CAPABILITIES=all includes compat32)
    # has two libcuda.so.1, and `find | head -1` is readdir order — a coin flip
    # that a 64-bit python would skip, falling through to the cache.
    local _native_libcuda_dir="" _libcuda_path MAX_CUDA
    for _d in "/usr/lib/$(uname -m)-linux-gnu" /usr/lib64; do
        if [[ -e "$_d/libcuda.so.1" ]]; then _native_libcuda_dir="$_d"; break; fi
    done
    if [[ -z "$_native_libcuda_dir" ]]; then
        _libcuda_path=$(find /usr/lib /usr/lib64 -name 'libcuda.so.1' \
                          -not -path '*/cuda*/compat/*' -not -path '*/stubs/*' 2>/dev/null | head -1)
        [[ -n "$_libcuda_path" ]] && _native_libcuda_dir=$(dirname "$_libcuda_path")
    fi
    if [[ -z "$_native_libcuda_dir" || ! -e "$_native_libcuda_dir/libcuda.so.1" ]]; then
        echo "Error: no native libcuda.so.1 found — refusing to probe unpinned, because an"
        echo "       unpinned probe can silently return a previous boot's forward-compat"
        echo "       version. Leaving the existing CUDA configuration untouched."
        return 1
    fi
    MAX_CUDA=$(LD_LIBRARY_PATH="$_native_libcuda_dir" /opt/instance-tools/bin/cuda-driver-version 2>/dev/null || true)

    # Shape-checked, not merely non-empty: MAX_CUDA is interpolated unquoted into
    # three `awk "BEGIN {...}"` programs below, where a non-numeric value is an awk
    # syntax error that silently falls through to the wrong toolkit.
    if [[ ! "$MAX_CUDA" =~ ^[0-9]+\.[0-9]+$ ]]; then
        echo "Error: could not determine the driver CUDA version (got '${MAX_CUDA}')"
        echo "       — leaving the existing CUDA configuration untouched (nothing changed)."
        return 1
    fi

    # Clean up ALL cuda ldconfig entries - we'll add back only what we need
    rm -f /etc/ld.so.conf.d/*cuda*.conf

    for conf in /etc/ld.so.conf.d/*.conf; do
        [[ -f "$conf" ]] || continue
        if grep -q "cuda" "$conf" 2>/dev/null; then
            sed -i '\#cuda#d' "$conf"
            [[ ! -s "$conf" ]] && rm -f "$conf"
        fi
    done

    sed -i '\#cuda#d' /etc/ld.so.conf 2>/dev/null
    ldconfig

    if [[ -n "$LD_LIBRARY_PATH" ]]; then
        export LD_LIBRARY_PATH=$(echo "$LD_LIBRARY_PATH" | tr ':' '\n' | grep -vE '/cuda(/|-)' | paste -sd ':')
    fi
    [[ -z "$LD_LIBRARY_PATH" ]] && unset LD_LIBRARY_PATH

    # Gather host GPU info
    local GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1)
    local CC=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -n1)
    local DRIVER_VER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1)
    # MAX_CUDA was resolved and validated at the top of this function, BEFORE
    # anything destructive ran. Deliberately not re-read here.

    # Find all installed CUDA versions, sorted descending
    local CUDA_VERSIONS=()
    for dir in /usr/local/cuda-*/; do
        [[ -d "$dir" ]] || continue
        local ver=$(basename "$dir" | sed 's/cuda-//')
        [[ "$ver" =~ ^[0-9]+\.[0-9]+$ ]] && CUDA_VERSIONS+=("$ver")
    done
    readarray -t CUDA_VERSIONS < <(printf '%s\n' "${CUDA_VERSIONS[@]}" | sort -t. -k1,1nr -k2,2nr)

    [[ ${#CUDA_VERSIONS[@]} -eq 0 ]] && return 0

    local SELECTED_CUDA=""
    local FORWARD_COMPAT_ENABLED=false

    if try_forward_compat "${CUDA_VERSIONS[0]}" "$MAX_CUDA"; then
        SELECTED_CUDA="${CUDA_VERSIONS[0]}"
        FORWARD_COMPAT_ENABLED=true
        echo "CUDA forward compatibility enabled"
    fi

    # Fallback: find highest compatible CUDA version
    if [[ -z "$SELECTED_CUDA" ]]; then
        for ver in "${CUDA_VERSIONS[@]}"; do
            [[ -z $ver ]] && continue
            if awk "BEGIN {exit !($ver <= $MAX_CUDA)}"; then
                SELECTED_CUDA="$ver"
                break
            fi
        done

        # Final fallback to lowest available
        if [[ -z "$SELECTED_CUDA" ]]; then
            SELECTED_CUDA="${CUDA_VERSIONS[-1]}"
            echo "Warning: Driver reports CUDA $MAX_CUDA but no compatible toolkit found; using ${SELECTED_CUDA:-image default}"
        fi
    fi

    if [[ -n "$SELECTED_CUDA" ]]; then
        export CUDA_HOME="/usr/local/cuda"
        [[ "$PATH" != *"${CUDA_HOME}/bin"* ]] && export PATH="${CUDA_HOME}/bin:${PATH}"
        
        rm -f /usr/local/cuda
        ln -sf "/usr/local/cuda-${SELECTED_CUDA}" /usr/local/cuda

        echo "${CUDA_HOME}/lib64" > /etc/ld.so.conf.d/10-cuda.conf

        echo "CUDA $SELECTED_CUDA selected (GPU: $GPU_NAME, CC: $CC, Driver: $DRIVER_VER, Max CUDA: $MAX_CUDA, Forward Compat: $FORWARD_COMPAT_ENABLED)"
    fi

    ldconfig

    # Avoid missing cuda libs error (affects 12.4 amd64)
    if [[ ! -e /usr/lib/x86_64-linux-gnu/libcuda.so && -e /usr/lib/x86_64-linux-gnu/libcuda.so.1 ]]; then
        ln -s /usr/lib/x86_64-linux-gnu/libcuda.so.1 /usr/lib/x86_64-linux-gnu/libcuda.so
    fi
}

configure_cuda