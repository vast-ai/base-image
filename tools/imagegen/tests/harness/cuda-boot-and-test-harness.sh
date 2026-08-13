#!/bin/bash
# Integration harness for the boot script and the test that gates it.
#
# Runs the REAL /etc/vast_boot.d/05-configure-cuda.sh and the REAL
# base/60-gpu-cuda.sh, in that order, inside a throwaway container with
# fabricated libcuda.so.1 files and a stub nvidia-smi — the pair, not either
# alone, because the defect this exists to prevent is the two DISAGREEING:
# a boot that configures the wrong toolkit and a test that reads the same wrong
# value and calls it healthy.
#
# Each scenario asserts the test's exit code AND (where it should fail) which
# check fired. Exit 0 if every scenario matches, 1 otherwise.
set -u

BOOT=/etc/vast_boot.d/05-configure-cuda.sh
TEST=/opt/instance-tools/tests/base/60-gpu-cuda.sh
ARCH_DIR="/usr/lib/$(uname -m)-linux-gnu"
COMPAT_13="/usr/local/cuda-13.0/compat"
rc=0

mkdir -p /build "$ARCH_DIR" "$COMPAT_13" /usr/local/cuda-12.4 /usr/local/cuda-13.0/lib64 /run
cat > /build/fake.c <<'EOF'
#include <stdlib.h>
int cuDriverGetVersion(int *v) { *v = FAKEVER; return 0; }
/* try_forward_compat probes the compat library with cuInit. FAKE_CUINIT_FAIL
   makes that probe fail the way a not-yet-ready device does at boot stage 05 —
   the case where "compat unavailable" is a transient fault, not a verdict. */
int cuInit(unsigned int f) { return getenv("FAKE_CUINIT_FAIL") ? 1 : 0; }
EOF
mk() { gcc -shared -fPIC -DFAKEVER="$2" -o "$1" /build/fake.c; }

# A stub nvidia-smi using the 610-era field name, so the text fallback is
# available but never the thing under test.
cat > /usr/bin/nvidia-smi <<'EOF'
#!/bin/bash
case "${1:-}" in
  --query-gpu=*) echo "STUB-GPU" ;;
  *) echo "| NVIDIA-SMI 610.57.04   KMD Version: 610.57.04   CUDA UMD Version: 13.3 |" ;;
esac
EOF
chmod +x /usr/bin/nvidia-smi

# Toolkit libraries, so the reachability assertion has something real to find.
: > /usr/local/cuda-12.4/lib64_marker
mkdir -p /usr/local/cuda-12.4/lib64
mk /usr/local/cuda-12.4/lib64/libcudart.so.12 12040
mk /usr/local/cuda-13.0/lib64/libcudart.so.13 13000

reset() {
    rm -f "$ARCH_DIR/libcuda.so.1" "$COMPAT_13/libcuda.so.1" \
          /etc/ld.so.conf.d/0-compat-cuda.conf /etc/ld.so.conf.d/10-cuda.conf \
          /run/vast-cuda-config-failed /etc/vast-cuda-compat-established /usr/local/cuda
    # The image default is an INDIRECT symlink whose target exists — which is
    # what makes the post-abort state look healthy to a shallow check.
    mkdir -p /etc/alternatives
    ln -sfn /usr/local/cuda-12.4 /etc/alternatives/cuda
    ln -sfn /etc/alternatives/cuda /usr/local/cuda
    ldconfig
}

scenario() {  # scenario <label> <expect-test-rc> <expect-substring-or-empty>
    local label="$1" want_rc="$2" want_sub="$3" boot_out test_out test_rc status=OK
    boot_out=$(bash "$BOOT" 2>&1)
    test_out=$(bash "$TEST" 2>&1); test_rc=$?
    [[ "$test_rc" == "$want_rc" ]] || status=FAIL
    if [[ -n "$want_sub" ]] && ! grep -qF "$want_sub" <<<"$test_out"; then status=FAIL; fi
    printf '%-50s test rc=%s (want %s)  [%s]\n' "$label" "$test_rc" "$want_rc" "$status"
    if [[ "$status" != OK ]]; then
        rc=1
        sed 's/^/    boot| /' <<<"$boot_out"
        sed 's/^/    test| /' <<<"$test_out"
    else
        grep -E "^(PASS|FAIL)" <<<"$test_out" | sed 's/^/    /'
    fi
}

echo "=== S1: healthy — driver 13.3, toolkits 12.4 and 13.0 ==="
reset
mk "$ARCH_DIR/libcuda.so.1" 13030
scenario "boot selects a toolkit, test agrees" 0 ""

echo
echo "=== S2: cross-major restart — driver 12.4, compat conf left by a prior boot ==="
# The regression that a compat-inflated reading causes: the boot script must read
# 12.4 (native), NOT 13.3 (compat), or it disables compat and ships 13.0 on a 12.4
# driver.
reset
mk "$ARCH_DIR/libcuda.so.1" 12040
mk "$COMPAT_13/libcuda.so.1" 13030
echo "$COMPAT_13" > /etc/ld.so.conf.d/0-compat-cuda.conf
ldconfig
bash "$BOOT" >/dev/null 2>&1
selected=$(readlink -f /usr/local/cuda)
if [[ "$selected" == /usr/local/cuda-13.0 && -f /etc/ld.so.conf.d/0-compat-cuda.conf ]]; then
    echo "boot kept compat enabled for 13.0 on a 12.4 driver        [OK]"
else
    echo "boot chose ${selected}, compat conf $( [[ -f /etc/ld.so.conf.d/0-compat-cuda.conf ]] && echo present || echo GONE )  [FAIL]"
    rc=1
fi

echo
echo "=== S3: no libcuda at all — boot must abort safely AND be caught ==="
reset
scenario "abort leaves a breadcrumb the test asserts" 1 "cuda-config"

echo
echo "=== S4: abort must not destroy the existing loader configuration ==="
reset
echo /usr/local/cuda-12.4/lib64 > /etc/ld.so.conf.d/10-cuda.conf
ldconfig
bash "$BOOT" >/dev/null 2>&1
if [[ -f /etc/ld.so.conf.d/10-cuda.conf ]] && ldconfig -p | grep -q "libcudart.so.12"; then
    echo "existing CUDA ld.so entry survived the abort              [OK]"
else
    echo "the abort deleted the loader configuration                [FAIL]"
    rc=1
fi

echo
echo "=== S5: toolkit present but unreachable — the original incident ==="
reset
mk "$ARCH_DIR/libcuda.so.1" 13030
bash "$BOOT" >/dev/null 2>&1
rm -f /etc/ld.so.conf.d/*cuda*.conf
ldconfig
out=$(bash "$TEST" 2>&1); trc=$?
if [[ $trc -eq 1 ]] && grep -qF "cuda-libpath" <<<"$out"; then
    echo "unreachable toolkit fails the test                        [OK]"
else
    echo "unreachable toolkit was NOT caught (rc=$trc)              [FAIL]"
    rc=1
    sed 's/^/    test| /' <<<"$out"
fi

echo
echo "=== S6: a stale breadcrumb must not outlive the boot that fixed it ==="
# /run is part of the container's own overlay (docker does not tmpfs-mount it),
# so a marker written by one bad boot would fail the QA gate forever after unless
# it is cleared at the START of every run. Nothing else pins that.
reset
mk "$ARCH_DIR/libcuda.so.1" 13030
echo "a previous boot failed" > /run/vast-cuda-config-failed
bash "$BOOT" >/dev/null 2>&1
out=$(bash "$TEST" 2>&1); trc=$?
if [[ ! -f /run/vast-cuda-config-failed && $trc -eq 0 ]]; then
    echo "healthy boot cleared the stale marker                     [OK]"
else
    echo "stale marker survived a healthy boot (test rc=$trc)       [FAIL]"
    rc=1
    sed 's/^/    test| /' <<<"$out"
fi

echo
echo "=== S7: compat active, still required, cannot be re-enabled ==="
# The regression a restart can cause: boot 1 runs CUDA 13.0 through forward
# compat; on boot 2 the compat cuInit probe fails, the fallback silently moves
# the instance to 12.4, and everything the customer built against libcudart.so.13
# breaks. It used to log "correct fallback" and exit 0.
reset
mk "$ARCH_DIR/libcuda.so.1" 12040
mk "$COMPAT_13/libcuda.so.1" 13030
bash "$BOOT" >/dev/null 2>&1
boot1=$(readlink -f /usr/local/cuda)
export FAKE_CUINIT_FAIL=1
boot2_out=$(bash "$BOOT" 2>&1)
boot2=$(readlink -f /usr/local/cuda)
test_out=$(bash "$TEST" 2>&1); trc=$?
unset FAKE_CUINIT_FAIL
if [[ "$boot1" == /usr/local/cuda-13.0 && "$boot2" == /usr/local/cuda-12.4 ]]; then
    if [[ $trc -eq 1 ]] && grep -qF "cuda-config" <<<"$test_out"; then
        echo "silent toolkit downgrade is recorded and fails         [OK]"
    else
        echo "downgrade 13.0 -> 12.4 went UNDETECTED (rc=$trc)       [FAIL]"
        rc=1
        sed 's/^/    boot2| /' <<<"$boot2_out"
        sed 's/^/    test | /' <<<"$test_out"
    fi
else
    echo "setup did not reproduce the downgrade ($boot1 -> $boot2)  [FAIL]"
    rc=1
fi

echo
echo "=== S8: compat active and STILL WORKING must stay silent ==="
# The other direction of S7, and the one that decides whether this is shippable:
# a normal datacenter restart re-enables compat, and nothing may fail.
reset
mk "$ARCH_DIR/libcuda.so.1" 12040
mk "$COMPAT_13/libcuda.so.1" 13030
bash "$BOOT" >/dev/null 2>&1
bash "$BOOT" >/dev/null 2>&1
selected=$(readlink -f /usr/local/cuda)
test_out=$(bash "$TEST" 2>&1); trc=$?
if [[ "$selected" == /usr/local/cuda-13.0 && $trc -eq 0 && ! -f /run/vast-cuda-config-failed ]]; then
    echo "a healthy compat restart is silent                        [OK]"
else
    echo "false positive on a healthy compat restart (rc=$trc)      [FAIL]"
    rc=1
    sed 's/^/    test| /' <<<"$test_out"
fi

echo
echo "=== S9: consumer GPU, compat never active — not a downgrade ==="
# Same fallback code path as S7 and it must NOT be flagged: compat was never
# carrying this instance, so nothing was lost. The distinction is entirely in
# whether compat was ever established here.
#
# Carries a POSITIVE control as well as the negative one. An assertion that is
# only "the marker is absent" passes in a botched run where the boot script was
# never even invoked — so this also requires that the boot did its job.
reset
mk "$ARCH_DIR/libcuda.so.1" 12040
mk "$COMPAT_13/libcuda.so.1" 13030
export FAKE_CUINIT_FAIL=1
bash "$BOOT" >/dev/null 2>&1; boot_rc=$?
unset FAKE_CUINIT_FAIL
if [[ "$(readlink -f /usr/local/cuda)" == /usr/local/cuda-12.4 && ! -f /run/vast-cuda-config-failed \
      && $boot_rc -eq 0 && -f /etc/ld.so.conf.d/10-cuda.conf ]]; then
    echo "first-boot fallback is not recorded as a regression       [OK]"
else
    echo "consumer-GPU first boot was flagged (boot rc=$boot_rc)    [FAIL]"
    rc=1
fi

echo
echo "=== S10: the downgrade stays reported on EVERY later boot ==="
# The condition outlives the boot that caused it, so the signal must too. Keyed
# on 0-compat-cuda.conf it did not: boot 2's own cleanup deletes that file, so
# boot 3 found nothing, went quiet, and printed "correct fallback" while the
# instance was still on the wrong toolkit.
reset
mk "$ARCH_DIR/libcuda.so.1" 12040
mk "$COMPAT_13/libcuda.so.1" 13030
bash "$BOOT" >/dev/null 2>&1                      # boot 1: compat carries 13.0
export FAKE_CUINIT_FAIL=1
bash "$BOOT" >/dev/null 2>&1                      # boot 2: compat lost
bash "$BOOT" >/dev/null 2>&1                      # boot 3: still lost
unset FAKE_CUINIT_FAIL
test_out=$(bash "$TEST" 2>&1); trc=$?
if [[ $trc -eq 1 ]] && grep -qF "cuda-config" <<<"$test_out"; then
    echo "third boot still fails, not just the second              [OK]"
else
    echo "the signal did not survive a second restart (rc=$trc)    [FAIL]"
    rc=1
    sed 's/^/    test| /' <<<"$test_out"
fi

echo
echo "=== S11: single-toolkit image — nothing moved, nothing claimed ==="
# The shape every shipped config actually has. Compat is lost, but with one
# toolkit installed the selection cannot change, so a message saying it "fell
# back to an older toolkit" would be false. That state is already caught by the
# compat assertions in base/60-gpu-cuda; this must not add a second, wrong one.
reset
rm -rf /usr/local/cuda-12.4
mk "$ARCH_DIR/libcuda.so.1" 12040
mk "$COMPAT_13/libcuda.so.1" 13030
bash "$BOOT" >/dev/null 2>&1
export FAKE_CUINIT_FAIL=1
boot2_out=$(bash "$BOOT" 2>&1)
unset FAKE_CUINIT_FAIL
mkdir -p /usr/local/cuda-12.4/lib64 && mk /usr/local/cuda-12.4/lib64/libcudart.so.12 12040
if [[ ! -f /run/vast-cuda-config-failed ]]; then
    echo "no false downgrade claim on a single-toolkit image        [OK]"
else
    echo "claimed a fallback that cannot have happened              [FAIL]"
    rc=1
    sed 's/^/    marker| /' /run/vast-cuda-config-failed
fi

echo
echo "=== S12: version comparison is not awk float arithmetic ==="
# 13.10 > 13.9 is FALSE under awk, which reads both as decimals. Every
# comparison here feeds toolkit selection, so the first double-digit minor would
# pick the wrong toolkit silently.
reset
rm -rf /usr/local/cuda-13.0 /usr/local/cuda-12.4
mkdir -p /usr/local/cuda-13.9/lib64 /usr/local/cuda-13.10/lib64
mk /usr/local/cuda-13.9/lib64/libcudart.so.13 13090
mk /usr/local/cuda-13.10/lib64/libcudart.so.13 13100
mk "$ARCH_DIR/libcuda.so.1" 13100                 # driver supports 13.10
bash "$BOOT" >/dev/null 2>&1
selected=$(readlink -f /usr/local/cuda)
rm -rf /usr/local/cuda-13.9 /usr/local/cuda-13.10
mkdir -p /usr/local/cuda-12.4/lib64 /usr/local/cuda-13.0/lib64
mk /usr/local/cuda-12.4/lib64/libcudart.so.12 12040
mk /usr/local/cuda-13.0/lib64/libcudart.so.13 13000
if [[ "$selected" == /usr/local/cuda-13.10 ]]; then
    echo "13.10 correctly outranks 13.9                             [OK]"
else
    echo "picked ${selected} — decimal comparison struck            [FAIL]"
    rc=1
fi

echo
if [[ $rc -eq 0 ]]; then echo "ALL SCENARIOS OK"; else echo "SCENARIOS FAILED"; fi
exit $rc
