#!/bin/bash
# Functional harness for ROOT/opt/instance-tools/bin/cuda-driver-version.
#
# Runs INSIDE a throwaway container (see test_cuda_driver_version.py) because it
# fabricates libcuda.so.1 files at real absolute paths and rebuilds the loader
# cache — the only way to test a resolver whose entire job is "which file did the
# loader actually open".
#
# What it pins down, in both directions:
#   * --native must return the DRIVER's version even when a forward-compat
#     libcuda owns the ld.so cache (the stop/start case that would otherwise
#     disable compat on a cross-major instance);
#   * --native must return NOTHING rather than a compat or stub reading;
#   * --native must still work on hosts that mount driver libs outside the
#     conventional directories — refusing there would hold a promotion on a
#     healthy host;
#   * the default (effective) mode must keep reporting the compat reading, and
#     must keep its nvidia-smi text fallback, which --native must not have.
#
# Exit 0 if every scenario matches, 1 otherwise.
set -u

CDV=${CDV:-/opt/instance-tools/bin/cuda-driver-version}
ARCH_DIR="/usr/lib/$(uname -m)-linux-gnu"
DECOY_DIR="/usr/lib/decoy-linux-gnu"
COMPAT_DIR="/usr/local/cuda-13.0/compat"
STUBS_DIR="/usr/local/cuda-13.0/lib64/stubs"
LEGACY_DIR="/usr/local/nvidia/lib64"
rc=0

mkdir -p /build "$ARCH_DIR" "$DECOY_DIR" "$COMPAT_DIR" "$STUBS_DIR" "$LEGACY_DIR"
cat > /build/fake.c <<'EOF'
int cuDriverGetVersion(int *v) { *v = FAKEVER; return 0; }
EOF
# A library that LOADS but cannot answer — a partially-injected or placeholder
# driver stub. Calling the missing symbol raises in ctypes.
cat > /build/nosym.c <<'EOF'
int not_the_symbol_you_want(void) { return 0; }
EOF

mk() { gcc -shared -fPIC -DFAKEVER="$2" -o "$1" /build/fake.c; }
mk_nosym() { gcc -shared -fPIC -o "$1" /build/nosym.c; }

reset() {
    rm -f "$ARCH_DIR/libcuda.so.1" "$DECOY_DIR/libcuda.so.1" /usr/lib64/libcuda.so.1 \
          "$LEGACY_DIR/libcuda.so.1" "$COMPAT_DIR/libcuda.so.1" "$STUBS_DIR/libcuda.so.1" \
          "$ARCH_DIR"/libcuda.so.[0-9]* \
          /etc/ld.so.conf.d/0-compat-cuda.conf /etc/ld.so.conf.d/9-nvidia.conf \
          /usr/bin/nvidia-smi
    ldconfig
}

run() {  # run <label> <expected-native> <expected-default>
    local label="$1" want_n="$2" want_d="$3" got_n got_d rc_n rc_d status=OK
    got_n=$($CDV --native 2>/dev/null); rc_n=$?
    got_d=$($CDV 2>/dev/null); rc_d=$?
    [[ "$got_n" == "$want_n" ]] || status=FAIL
    [[ "$got_d" == "$want_d" ]] || status=FAIL
    # A refusal must be a refusal: no answer AND a non-zero exit.
    [[ -n "$want_n" || $rc_n -ne 0 ]] || status=FAIL
    printf '%-56s native=%-7s(rc=%d) default=%-7s(rc=%d)  want n=%-7s d=%-7s [%s]\n' \
        "$label" "${got_n:-<none>}" "$rc_n" "${got_d:-<none>}" "$rc_d" \
        "${want_n:-<none>}" "${want_d:-<none>}" "$status"
    [[ "$status" == OK ]] || rc=1
}

echo "=== S1: native present, stale forward-compat owns the ld.so cache ==="
reset
mk "$ARCH_DIR/libcuda.so.1" 12080
mk "$COMPAT_DIR/libcuda.so.1" 13030
echo "$COMPAT_DIR" > /etc/ld.so.conf.d/0-compat-cuda.conf
ldconfig
run "native beats the compat cache entry" "12.8" "13.3"

echo
echo "=== S2: + a second libcuda in another directory (compat32-style mount) ==="
mk "$DECOY_DIR/libcuda.so.1" 13030
run "an extra libcuda elsewhere cannot change the answer" "12.8" "13.3"

echo
echo "=== S3: driver libs ONLY outside the conventional dirs (legacy layout) ==="
reset
mk "$LEGACY_DIR/libcuda.so.1" 12080
echo "$LEGACY_DIR" > /etc/ld.so.conf.d/9-nvidia.conf
ldconfig
run "unusual layout still answers (no fail-closed regression)" "12.8" "12.8"

echo
echo "=== S4: ONLY a forward-compat libcuda is resolvable ==="
reset
mk "$COMPAT_DIR/libcuda.so.1" 13030
echo "$COMPAT_DIR" > /etc/ld.so.conf.d/0-compat-cuda.conf
ldconfig
run "refuses rather than reporting the compat version" "" "13.3"

echo
echo "=== S5: no libcuda at all, nvidia-smi answers (driver-610 spelling) ==="
reset
printf '#!/bin/sh\necho "| NVIDIA-SMI 610.57.04   KMD Version: 610.57.04   CUDA UMD Version: 13.3 |"\n' \
    > /usr/bin/nvidia-smi
chmod +x /usr/bin/nvidia-smi
run "text fallback serves default only, never --native" "" "13.3"

echo
echo "=== S6: only a link-time stub libcuda ==="
reset
mk "$STUBS_DIR/libcuda.so.1" 13030
echo "$STUBS_DIR" > /etc/ld.so.conf.d/0-compat-cuda.conf
ldconfig
run "a stub must not answer --native" "" "13.3"

echo
echo "=== S7: first candidate loads but has no cuDriverGetVersion ==="
# A candidate that fails to ANSWER must not abandon the search. An unguarded
# raise here killed the interpreter mid-loop, so a good library one directory
# over was never tried and the boot script aborted on a healthy host.
reset
mk_nosym "$ARCH_DIR/libcuda.so.1"
mk "$LEGACY_DIR/libcuda.so.1" 12080
echo "$LEGACY_DIR" > /etc/ld.so.conf.d/9-nvidia.conf
ldconfig
run "a dud candidate does not end the search" "12.8" "12.8"

echo
echo "=== S8: driver-version cross-check (nvidia-smi CSV query api) ==="
# Path shape is a convention, not a proof: a compat library copied into the arch
# directory passes the /compat/ test. When the filename carries a driver version,
# it is checked against the driver's own — a contradiction is a refusal.
reset
printf '#!/bin/sh\ncase "$1" in --query-gpu=driver_version) echo "610.57.04";; *) echo "| CUDA UMD Version: 13.3 |";; esac\n' \
    > /usr/bin/nvidia-smi
chmod +x /usr/bin/nvidia-smi
mk "$ARCH_DIR/libcuda.so.580.65.06" 13030          # a compat lib wearing a driver name
ln -sf "$ARCH_DIR/libcuda.so.580.65.06" "$ARCH_DIR/libcuda.so.1"
ldconfig
run "a lib claiming another driver version is refused" "" "13.3"

reset
printf '#!/bin/sh\ncase "$1" in --query-gpu=driver_version) echo "610.57.04";; *) echo "| CUDA UMD Version: 13.3 |";; esac\n' \
    > /usr/bin/nvidia-smi
chmod +x /usr/bin/nvidia-smi
mk "$ARCH_DIR/libcuda.so.610.57.04" 13030          # the real thing, as shipped
ln -sf "$ARCH_DIR/libcuda.so.610.57.04" "$ARCH_DIR/libcuda.so.1"
ldconfig
run "the real driver layout still answers" "13.3" "13.3"

echo
if [[ $rc -eq 0 ]]; then echo "ALL SCENARIOS OK"; else echo "SCENARIOS FAILED"; fi
exit $rc
