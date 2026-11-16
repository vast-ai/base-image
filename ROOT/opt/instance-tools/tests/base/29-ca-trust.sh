#!/bin/bash
# Test: no CA store in the image trusts a distrusted root (ADR 0058).
#
# Verifies the published chains through the compromised eMudhra intermediates
# (tests/ca-trust/chains, public certificates) against every CA bundle the image carries.
# A PASS only means something because:
# - a positive control runs first: all chains must verify against the published roots, or
#   this image's openssl cannot do the check and nothing below is trusted;
# - the check time is fixed (-attime, when the chains were captured), so leaf expiry can
#   never turn a bundle into a pass;
# - bundles are found by CONTENT, not by file name (a name list missed grpcio's roots.pem);
# - the OpenSSL directory store (/etc/ssl/certs) is checked as a store of its own;
# - Node is checked against node-trust-floor.txt: its fix blocks the intermediates by public
#   key, which no root listing shows;
# - pip wheels bundled for ensurepip, which no file scan can see into, are checked by version;
# - a bundle that cannot be read is a failure, never an "ok".
#
# TEST_TIMEOUT=900
source "$(dirname "$0")/../lib.sh"

FIXTURES="$(cd "$(dirname "$0")/.." && pwd)/ca-trust"
CAPTURED=1792497600   # 2026-10-20T12:00Z, when the chains were captured
UV_FLOOR=0.13.4       # uv compiles its roots in
NODE_FLOOR=/opt/instance-tools/lib/node-trust-floor.txt

# pip loads its vendored certifi in addition to any other store; the floor has one source.
PIP_FLOOR_FILE=/opt/instance-tools/lib/pip-trust-floor.env
[[ -r "$PIP_FLOOR_FILE" ]] && . "$PIP_FLOOR_FILE"
[[ -n "${PIP_FLOOR:-}" ]] || test_fail "no pip trust floor (${PIP_FLOOR_FILE})"
command -v openssl >/dev/null || test_fail "no openssl CLI: the chain checks cannot run"
[[ -d "$FIXTURES/chains" && -s "$FIXTURES/reference-roots.pem" ]] || test_fail "fixtures missing under ${FIXTURES}"

# Prints how many of the chains verify against the store named by "$@".
accepted() {
    local ok=0 c
    for c in "$FIXTURES"/chains/*.pem; do
        openssl verify -no-CAstore -attime "$CAPTURED" "$@" -untrusted "$c" "$c" >/dev/null 2>&1 && ok=$((ok + 1))
    done
    echo "$ok"
}
version_ge() { [[ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" == "$2" ]]; }

total=$(ls "$FIXTURES"/chains/*.pem | wc -l)
control=$(accepted -no-CApath -CAfile "$FIXTURES/reference-roots.pem")
[[ "$control" == "$total" ]] || test_fail "positive control verified ${control}/${total}: this openssl cannot run the check"
echo "  positive control: ${control}/${total} chains verify against the published roots"

# ── Standing exemptions (ADR 0058, amendment 2026-11-16) ─────────────────────────────────
# What the image can fix, it fixes at build. A row here is for a file it cannot, and every row
# has an entry in ADR 0058's "Standing exemptions" table saying why (linter L127). No row
# expires: an applied exemption is printed as an EXEMPT line on every run and counted on the
# PASS line, and the QA gate turns those lines into warnings.
#
#   id|checks|pattern|condition|reason
#   checks     which of: bundle pip-wheel node uv. The directory store cannot be exempted.
#   pattern    absolute path. `*` stops at a `/`. A trailing `/**` covers everything below,
#              and is accepted only on a row that has a condition. @UVPY@ is `uv python dir`.
#   condition  a function below, exempt_if_<condition>, that must also hold for the file.
EXEMPTIONS='nsight-private-python|bundle pip-wheel|/opt/nvidia/nsight-*/**|dpkg-owned|Python embedded in the NVIDIA Nsight tools from the CUDA base image; not on PATH and nothing of ours runs it
worker-bootstrap|bundle pip-wheel|@UVPY@/cpython-3.10.*/**|worker-bootstrap|Python 3.10 the serverless worker bootstrap installed at boot instead of using the one this image ships'

exempt_if_dpkg_owned() { dpkg-query -S "$1" >/dev/null 2>&1; }
# Only on a serverless instance, and only an interpreter that appeared after the build finished
# (newer than /.env_hash): a 3.10 the image itself ships is always checked.
exempt_if_worker_bootstrap() {
    is_serverless || return 1
    [[ -e /.env_hash && "$1" =~ ^(.*/cpython-3\.10\.[^/]+)/ ]] || return 1
    [[ "${BASH_REMATCH[1]}" -nt /.env_hash ]]
}

UVPY=$(uv python dir 2>/dev/null || true)
# pattern -> anchored regex in EX_RE; fails on a pattern this test refuses to hold.
exemption_regex() {   # $1 = pattern, $2 = condition
    local pat=$1 tail=""
    [[ "$pat" == /* || "$pat" == @UVPY@/* ]] || return 1
    if [[ "$pat" == *'/**' ]]; then
        [[ -n "$2" ]] || return 1
        pat=${pat%'/**'}; tail='/.+'
    fi
    [[ "$pat" != *'**'* ]] || return 1
    pat=$(sed -e 's/[][\.^$+?(){}|]/\\&/g' -e 's/\*/[^\/]*/g' <<< "$pat")
    EX_RE="^${pat//@UVPY@/$(sed -e 's/[][\.^$+?(){}|*]/\\&/g' <<< "$UVPY")}${tail}\$"
}
# A malformed row fails the test before anything is judged by it.
while IFS='|' read -r ex_id ex_checks ex_pat ex_cond ex_why ex_extra; do
    [[ -n "$ex_id" && -n "$ex_checks" && -n "$ex_cond" && -n "$ex_why" && -z "$ex_extra" ]] \
        || test_fail "malformed exemption row '${ex_id}'"
    for ex_c in $ex_checks; do
        [[ " bundle pip-wheel node uv " == *" ${ex_c} "* ]] || test_fail "exemption '${ex_id}': unknown check '${ex_c}'"
    done
    declare -F "exempt_if_${ex_cond//-/_}" >/dev/null || test_fail "exemption '${ex_id}': unknown condition '${ex_cond}'"
    exemption_regex "$ex_pat" "$ex_cond" || test_fail "exemption '${ex_id}': pattern '${ex_pat}' is not accepted"
done <<< "$EXEMPTIONS"

exempted=0
# exempt CHECK PATH: true when a row covers the file; then prints its EXEMPT line and counts it.
# Runs in the main shell (never in a command substitution), or the count would be lost.
exempt() {
    local id checks pat cond why
    while IFS='|' read -r id checks pat cond why; do
        [[ " ${checks} " == *" $1 "* ]] || continue
        [[ "$pat" != *@UVPY@* || -n "$UVPY" ]] || continue
        exemption_regex "$pat" "$cond" && [[ "$2" =~ $EX_RE ]] || continue
        "exempt_if_${cond//-/_}" "$2" || continue
        echo "EXEMPT: ${TEST_NAME}: ${id} $1 $2 [${why}]"
        exempted=$((exempted + 1))
        return 0
    done <<< "$EXEMPTIONS"
    return 1
}
# Debian/Ubuntu patch python3-certifi and python3-pip so where() returns the OS bundle; the
# cacert.pem beside it is then never read. Both legs are checked, or the file is judged as any other.
debian_reads_os_bundle() {
    local core
    core="$(dirname "$1")/core.py"
    [[ "$1" == */certifi/cacert.pem && -f "$core" ]] || return 1
    dpkg-query -S "$1" >/dev/null 2>&1 || return 1
    grep -qE "^DEBIAN_CA_CERTS_PATH = ['\"]/etc/ssl/certs/ca-certificates\.crt['\"]" "$core" \
        && grep -qE '^\s+return DEBIAN_CA_CERTS_PATH\s*$' "$core"
}
# True when virtualenv seeds this embedded wheel only into Pythons below the floor's minimum, read from its
# BUNDLE_SUPPORT table: no pip at the floor runs there, the same boundary lib/refresh-ca-data.sh skips.
seed_only_below_floor() {
    local pys py
    pys=$(awk -v whl="\"$(basename "$1")\"" '/^BUNDLE_SUPPORT/ { t = 1; next } t && /^}/ { exit }
        t && match($0, /^ *"3\.[0-9]+":/) { ver = substr($0, RSTART, RLENGTH); gsub(/[^0-9.]/, "", ver) }
        t && index($0, whl) && ver { print ver }' "$(dirname "$1")/__init__.py" 2>/dev/null)
    [[ -n "$pys" ]] || return 1
    for py in $pys; do (( ${py#3.} < PIP_FLOOR_MIN_PYTHON )) || return 1; done
    echo $pys
}
judge() {   # $1 = label, $2 = chains accepted, $3 = check name (empty: cannot be exempted)
    if (( $2 == 0 )); then
        echo "  ok    0/${total}  $1"
    elif [[ -n "${3:-}" ]] && debian_reads_os_bundle "$1"; then
        echo "  ok    $2/${total}  $1 [not read: Debian/Ubuntu's patched certifi returns the OS bundle]"
    elif [[ -n "${3:-}" ]] && exempt "$3" "$1"; then
        :
    else
        fail_later "ca-trust:$1" "accepts $2/${total} chains through compromised intermediates"
    fi
}

# 1. The OpenSSL directory store.
[[ -d /etc/ssl/certs ]] && judge "/etc/ssl/certs (directory store)" "$(accepted -no-CAfile -CApath /etc/ssl/certs)"

# 2. Every file carrying a CA bundle, by content.
bundles=0
while IFS= read -r f; do
    if [[ ! -r "$f" || ! -s "$f" ]]; then fail_later "ca-trust:$f" "bundle is unreadable"; continue; fi
    (( $(grep -c 'BEGIN CERTIFICATE' "$f" 2>/dev/null) >= 20 )) || continue
    bundles=$((bundles + 1))
    judge "$f" "$(accepted -no-CApath -CAfile "$f")" bundle
done < <(find / -xdev -type f \( -name '*.pem' -o -name '*.crt' -o -name '*.cer' -o -name '*.txt' -o -name '*bundle*' \) \
            -size +20k -not -path '/proc/*' -not -path '/sys/*' -not -path "${FIXTURES}/*" \
            -exec grep -l -m1 'BEGIN CERTIFICATE' {} + 2>/dev/null | sort)
(( bundles > 0 )) || fail_later "ca-trust:discovery" "found no CA bundle at all, so nothing was checked"
echo "  bundles checked by content: ${bundles}"

# 3. pip wheels bundled for ensurepip.
while IFS= read -r w; do
    v=$(basename "$w" | sed -E 's/^pip-([^-]+)-.*/\1/')
    if [[ "$w" == /usr/share/python-wheels/* ]]; then
        echo "  ok    pip ${v}  $w [not read as a trust store: Debian/Ubuntu's patched wheel reads the OS bundle]"
    elif version_ge "$v" "$PIP_FLOOR"; then
        echo "  ok    pip ${v}  $w"
    elif [[ "$w" == */virtualenv/seed/wheels/embed/* ]] && pys=$(seed_only_below_floor "$w"); then
        echo "  n/a   pip ${v}  $w [virtualenv seeds it only for Python ${pys}; no pip at the floor supports those]"
    elif exempt pip-wheel "$w" || exempt pip-wheel "$(readlink -f "$w")"; then   # a mirrored file is judged by its target
        :
    else
        fail_later "ca-trust:$w" "bundled pip ${v} < ${PIP_FLOOR}; every venv made from this interpreter trusts the distrusted CAs"
    fi
done < <(find / -xdev -name 'pip-*.whl' \( -path '*/ensurepip/_bundled/*' -o -path '/usr/share/python-wheels/*' \
            -o -path '*/virtualenv/seed/wheels/embed/*' \) 2>/dev/null | sort)

# 4. Node, every binary.
[[ -s /opt/nvm/nvm.sh ]] && . /opt/nvm/nvm.sh >/dev/null 2>&1
while IFS= read -r node; do
    "$node" -e 'process.exit(0)' 2>/dev/null || continue
    v=$("$node" -p 'process.versions.node')
    floor=$(awk -v m="${v%%.*}" '$1 == m {print $2}' "$NODE_FLOOR" 2>/dev/null)
    if [[ -n "$floor" ]] && version_ge "$v" "$floor"; then echo "  ok    node ${v}  $node"
    elif exempt node "$node"; then :
    else fail_later "ca-trust:$node" "Node ${v} is below the trust floor for its line (${floor:-no fixed release listed})"; fi
done < <( { type -ap node; find / -xdev -type f -name node -perm -u+x -not -path '/proc/*' 2>/dev/null; } \
            | xargs -r -n1 readlink -f | sort -u)

# 5. uv, every binary.
while IFS= read -r uv; do
    v=$("$uv" --version 2>/dev/null | awk '{print $2}')
    if [[ -n "$v" ]] && version_ge "$v" "$UV_FLOOR"; then echo "  ok    uv ${v}  $uv"
    elif exempt uv "$uv"; then :
    else fail_later "ca-trust:$uv" "uv ${v:-unknown} < ${UV_FLOOR} (compiled-in roots)"; fi
done < <( { type -ap uv; find / -xdev -type f -name uv -perm -u+x -not -path '/proc/*' 2>/dev/null; } \
            | xargs -r -n1 readlink -f | sort -u)

report_failures
if (( exempted > 0 )); then
    test_pass "no CA store accepts a chain through a distrusted CA; ${exempted} exemption(s) applied (EXEMPT lines above)"
fi
test_pass "no CA store accepts a chain through a distrusted CA; no exemptions applied"
