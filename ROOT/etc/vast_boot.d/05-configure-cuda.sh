#!/bin/bash

# CUDA Forward Compatibility: datacenter GPUs with Volta+ and compat libs present
# Consumer class GPUs can rely on minor version compatibility, but forward compatibility must be removed first

# Component-wise, NOT awk's float comparison.
#
# `awk "BEGIN {exit !(13.10 > 13.9)}"` is FALSE: awk reads both as decimals, so
# 13.10 is 13.1. Every comparison in this file feeds toolkit selection, so at the
# first double-digit minor the wrong toolkit would be chosen silently — the same
# failure class this file was rewritten to remove, waiting on a version bump.
# tools/template_manager already refuses double-digit minors for this reason;
# here the fix is to compare properly. Non-numeric input returns false rather
# than crashing (an unknown version must not assert anything).
cuda_version_gt() {
    local a b
    IFS=. read -r -a a <<< "${1:-}"
    IFS=. read -r -a b <<< "${2:-}"
    [[ "${a[0]:-x}" =~ ^[0-9]+$ && "${a[1]:-0}" =~ ^[0-9]+$ ]] || return 1
    [[ "${b[0]:-x}" =~ ^[0-9]+$ && "${b[1]:-0}" =~ ^[0-9]+$ ]] || return 1
    (( 10#${a[0]} != 10#${b[0]} )) && return $(( 10#${a[0]} > 10#${b[0]} ? 0 : 1 ))
    (( 10#${a[1]:-0} > 10#${b[1]:-0} )) && return 0
    return 1
}

# Where a successful forward-compat enable is remembered ACROSS boots.
#
# The obvious signal — /etc/ld.so.conf.d/0-compat-cuda.conf — cannot serve: the
# very next boot deletes it in the cleanup below, so a compat loss is visible for
# exactly one boot and every boot after that reports the degraded instance as
# healthy. /etc persists on the container's overlay, so a record written here
# survives as long as the instance does.
CUDA_COMPAT_ESTABLISHED_MARKER=/etc/vast-cuda-compat-established

try_forward_compat() {
    local LATEST_CUDA="$1" MAX_CUDA="$2"

    [[ -z "$LATEST_CUDA" || -z "$MAX_CUDA" ]] && return 1

    [[ "${DISABLE_FORWARD_COMPAT:-false}" == "true" ]] && return 1
    cuda_version_gt "$LATEST_CUDA" "$MAX_CUDA" || return 1

    local COMPAT_DIR="/usr/local/cuda-${LATEST_CUDA}/compat"
    [[ -d "$COMPAT_DIR" ]] || return 1
    compgen -G "$COMPAT_DIR/libcuda.so.*" > /dev/null || return 1
    
    # Retried, because this runs in the FIRST boot script and nothing before it
    # has waited for the driver. A device that is not ready yet fails cuInit and
    # would otherwise be indistinguishable from a consumer GPU that can never use
    # forward compat — permanently downgrading the toolkit over a transient fault.
    local _try
    for _try in 1 2 3; do
        if LD_LIBRARY_PATH="$COMPAT_DIR" python3 -c "
import sys, ctypes
sys.exit(0 if ctypes.CDLL('libcuda.so.1').cuInit(0) == 0 else 1)
" 2>/dev/null; then
            echo "$COMPAT_DIR" > /etc/ld.so.conf.d/0-compat-cuda.conf
            printf '%s\n' "$LATEST_CUDA" > "$CUDA_COMPAT_ESTABLISHED_MARKER" 2>/dev/null
            return 0
        fi
        [[ "$_try" -lt 3 ]] && sleep 2
    done
    return 1
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

    # Escape hatch. --native refuses rather than guessing, and a refusal aborts
    # CUDA configuration for the whole session — correct, but it leaves an
    # operator facing a host class we got wrong with nothing to do but rebuild.
    # An explicit override is shape-checked exactly like a probed value and
    # announced loudly, so it can never be mistaken for a reading.
    if [[ -n "${VAST_CUDA_MAX_OVERRIDE:-}" ]]; then
        echo "Notice: VAST_CUDA_MAX_OVERRIDE=${VAST_CUDA_MAX_OVERRIDE} overrides the probed"
        echo "        driver maximum (probe returned '${MAX_CUDA:-nothing}')."
        MAX_CUDA="$VAST_CUDA_MAX_OVERRIDE"
    fi

    # Shape-checked, not merely non-empty: MAX_CUDA is interpolated unquoted into
    # three `awk "BEGIN {...}"` programs below, where a non-numeric value is an awk
    # syntax error that silently falls through to the wrong toolkit.
    if [[ ! "$MAX_CUDA" =~ ^[0-9]+\.[0-9]+$ ]]; then
        cuda_config_failed "could not determine the driver CUDA version (got '${MAX_CUDA}')"
        return 1
    fi

    # Which toolkit forward compat was carrying, if it ever was. Read from the
    # durable record, not from 0-compat-cuda.conf, which the cleanup below is
    # about to delete.
    #
    # try_forward_compat returns 1 for several unrelated reasons — not needed,
    # disabled, no compat libs shipped, or its cuInit probe failed after retries —
    # and the selection below cannot tell them apart. The last one is not benign
    # on a RESTART: an instance that was running CUDA 13.0 through compat comes
    # back on 12.4, everything the customer compiled against libcudart.so.13
    # breaks, and until this record existed the log line read "correct fallback".
    #
    # First boot on a consumer GPU takes the same code path and is NOT this:
    # compat was never established, so there is nothing to lose.
    local COMPAT_ESTABLISHED_FOR=""
    [[ -f "$CUDA_COMPAT_ESTABLISHED_MARKER" ]] && \
        COMPAT_ESTABLISHED_FOR=$(head -1 "$CUDA_COMPAT_ESTABLISHED_MARKER" 2>/dev/null)

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
            if ! cuda_version_gt "$ver" "$MAX_CUDA"; then
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

    # A forward compat that this instance ONCE HAD and no longer has, where the
    # selection actually moved as a result. Both halves matter:
    #
    #   * keyed on the durable record, not on 0-compat-cuda.conf, so the condition
    #     is reported on every subsequent boot rather than only the first — the
    #     instance stays broken, so the signal must too;
    #   * keyed on the selection actually CHANGING, so a single-toolkit image (the
    #     shape every shipped config has) is not told it "fell back to an older
    #     toolkit" when there is no older toolkit and nothing moved. That state is
    #     already caught, correctly, by base/60-gpu-cuda's compat assertions.
    #
    # Not re-derived from try_forward_compat's conditions — comparing the outcome
    # to what was recorded needs none of them, and mirroring them here would be a
    # second copy to drift.
    if [[ -n "$COMPAT_ESTABLISHED_FOR" && "$FORWARD_COMPAT_ENABLED" == false \
          && "${DISABLE_FORWARD_COMPAT:-false}" != "true" \
          && -n "$SELECTED_CUDA" && "$SELECTED_CUDA" != "$COMPAT_ESTABLISHED_FOR" ]]; then
        cuda_config_record "forward compat previously carried CUDA ${COMPAT_ESTABLISHED_FOR} on \
this instance and could not be re-established; this boot selected ${SELECTED_CUDA} instead — the \
toolkit changed under a running instance, so anything built against the previous one will fail \
to load"
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