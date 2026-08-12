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

# Breadcrumb for the abort paths below. Without it the safe abort is INVISIBLE:
# nothing is written, nothing is logged past this script's stdout, and the
# instance simply runs on the image-default toolkit — which base/60-gpu-cuda only
# notices by luck (it needs the image to ship an INDIRECT /usr/local/cuda for the
# symlink assertion to catch it). Detection should be designed, not accidental.
#
# Cleared at the top of every run, not just written on failure: /run is part of
# the container's own overlay here (docker does not tmpfs-mount it), so a stale
# breadcrumb from a previous boot would fail the QA test forever.
#
# The path is duplicated in base/60-gpu-cuda (the assertion) and in
# tools/imagegen/tests/harness/cuda-boot-and-test-harness.sh (which pins both
# ends against each other, so a divergence fails the suite rather than going
# quiet — the same "two copies" hazard L064 exists to forbid).
CUDA_CONFIG_FAILED_MARKER=/run/vast-cuda-config-failed

# Record a condition base/60-gpu-cuda must fail on. Not every caller aborts:
# a toolkit downgrade is recorded and the boot CONTINUES, because by then a
# fallback has already been selected and stopping would leave nothing configured.
cuda_config_record() {
    mkdir -p "$(dirname "$CUDA_CONFIG_FAILED_MARKER")" 2>/dev/null
    if ! printf '%s\n' "$1" > "$CUDA_CONFIG_FAILED_MARKER" 2>/dev/null; then
        # The whole point of the marker is that this state stops being invisible.
        # If it cannot be written, say so on stdout rather than silently reverting
        # to detection-by-luck.
        echo "Warning: could not write $CUDA_CONFIG_FAILED_MARKER — the condition below"
        echo "         will not be visible to base/60-gpu-cuda."
    fi
    echo "Error: $1"
}

cuda_config_failed() {
    cuda_config_record "$1"
    echo "       — leaving the existing CUDA configuration untouched (nothing changed)."
}

configure_cuda() {
    rm -f "$CUDA_CONFIG_FAILED_MARKER"
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
    # base/60-gpu-cuda now asserts the toolkit is reachable and reads the driver
    # version through the SAME helper and the same --native mode as this script,
    # so the test cannot silently agree with a boot that got it wrong.
    # Driver API, not prose. See /opt/instance-tools/bin/cuda-driver-version for
    # why: driver 610 renamed nvidia-smi's "CUDA Version" field to "CUDA UMD
    # Version" and broke every scrape of it fleet-wide.
    #
    # --native, and that is load-bearing.
    #
    # cuDriverGetVersion reports whichever libcuda.so.1 the loader resolves. If a
    # PREVIOUS boot enabled forward compat it wrote /etc/ld.so.conf.d/0-compat-cuda.conf
    # (named "0-" so it wins), and /etc and the loader cache persist across a
    # stop/start — so a plain probe here would return the COMPAT version, not the
    # driver's own.
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
    # therefore only correct with the compat libcuda excluded explicitly. The
    # helper does that by dlopening an absolute path and then confirming, from
    # /proc/self/maps, which file actually got mapped — and returns NOTHING if it
    # cannot prove the reading is native. Deliberately not open-coded here: the
    # earlier bash version of this (LD_LIBRARY_PATH=<dir>) was a search HINT, not
    # a pin, and failed open to the compat lib. Two copies of that heuristic is
    # how one defect ships twice.
    local MAX_CUDA
    MAX_CUDA=$(/opt/instance-tools/bin/cuda-driver-version --native 2>/dev/null || true)

    # Shape-checked, not merely non-empty: MAX_CUDA is interpolated unquoted into
    # three `awk "BEGIN {...}"` programs below, where a non-numeric value is an awk
    # syntax error that silently falls through to the wrong toolkit.
    if [[ ! "$MAX_CUDA" =~ ^[0-9]+\.[0-9]+$ ]]; then
        cuda_config_failed "could not determine the driver CUDA version (got '${MAX_CUDA}')"
        return 1
    fi

    # Was forward compat active when this boot started? Must be read BEFORE the
    # cleanup below deletes the evidence.
    #
    # try_forward_compat returns 1 for several unrelated reasons — not needed,
    # disabled, no compat libs shipped, or its cuInit probe failed — and the
    # selection below cannot tell them apart. The last one is not benign on a
    # RESTART: an instance that was running CUDA 13.0 through compat comes back on
    # 12.4 because a probe failed at boot stage 05, where nothing has waited for
    # the driver. Everything the customer compiled against libcudart.so.13 breaks,
    # and until this flag existed the log line read "correct fallback".
    #
    # First boot on a consumer GPU is the same code path and is NOT this: compat
    # was never active, so there is nothing to lose. The distinction is entirely
    # in whether the conf was there when we arrived.
    local COMPAT_WAS_ACTIVE=false
    [[ -f /etc/ld.so.conf.d/0-compat-cuda.conf ]] && COMPAT_WAS_ACTIVE=true

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
    elif [[ "$COMPAT_WAS_ACTIVE" == true \
            && "${DISABLE_FORWARD_COMPAT:-false}" != "true" ]] \
         && awk "BEGIN {exit !(${CUDA_VERSIONS[0]} > $MAX_CUDA)}"; then
        # Compat was carrying this instance when the boot started, it is still
        # needed (the newest toolkit still exceeds the driver's maximum), and it
        # could not be re-established. The fallback below will silently move the
        # instance to an older toolkit; record it so base/60-gpu-cuda fails
        # instead of printing "correct fallback".
        #
        # Excluded on purpose: DISABLE_FORWARD_COMPAT (the operator asked for
        # this), and the case where the toolkit no longer exceeds the driver
        # maximum (a driver upgrade made compat unnecessary — not a loss).
        cuda_config_record "forward compat was active on a previous boot and could not be \
re-enabled, but CUDA ${CUDA_VERSIONS[0]} still exceeds the driver maximum ${MAX_CUDA} — \
this boot falls back to an older toolkit, changing it under a running instance"
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