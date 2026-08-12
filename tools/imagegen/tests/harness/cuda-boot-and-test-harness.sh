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
int cuDriverGetVersion(int *v) { *v = FAKEVER; return 0; }
int cuInit(unsigned int f) { return 0; }
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
          /run/vast-cuda-config-failed /usr/local/cuda
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
if [[ $rc -eq 0 ]]; then echo "ALL SCENARIOS OK"; else echo "SCENARIOS FAILED"; fi
exit $rc
