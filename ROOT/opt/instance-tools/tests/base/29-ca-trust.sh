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

# Exact paths, each with its reason and review date; the same list applies to every check below. Debian/Ubuntu patch certifi.where() and
# pip's vendored copy to return the OS bundle, so those files are never read.
ALLOW='/usr/lib/python3/dist-packages/certifi/cacert.pem|Debian/Ubuntu certifi reads the OS bundle|2099-12-31
/usr/lib/python3/dist-packages/pip/_vendor/certifi/cacert.pem|Debian/Ubuntu pip reads the OS bundle|2099-12-31
*/botocore/cacert.pem|upstream botocore bundle; botocore uses certifi when importable|2026-12-31
/opt/nvidia/nsight-*/*|private Python of the NVIDIA Nsight tools from the CUDA base image; not on PATH, left to NVIDIA|2026-12-31
/opt/sglang/bin/uv|upstream sglang engine environment uv; ours in /opt/instance-tools/bin runs first, left to upstream|2026-12-31
/opt/sglang/lib/python3*/site-packages/virtualenv/seed/wheels/embed/*|upstream sglang engine environment virtualenv seed wheels; left to upstream|2026-12-31
/usr/local/lib/python3.12/dist-packages/virtualenv/seed/wheels/embed/pip-26.2.1-py3-none-any.whl|upstream vllm-omni engine interpreter virtualenv seed wheel; left to upstream|2026-12-31
/usr/local/lib/python3.12/site-packages/playwright/driver/node|upstream open-webui Playwright driver Node; left to upstream|2026-12-31'
allowed() {
    local pat why exp
    while IFS='|' read -r pat why exp; do
        [[ -n "$pat" && "$1" == $pat ]] || continue
        [[ "$(date +%F)" > "$exp" ]] && { echo "EXPIRED ${exp}: ${why}"; return 1; }
        echo "$why"; return 0
    done <<< "$ALLOW"
    # The serverless worker bootstrap (start_server.sh) installs a uv-managed Python 3.10 at boot, after this
    # image's CA refresh ran, and that interpreter carries pip 23.0.1. Allowed until the bootstrap is fixed, and
    # only for an interpreter created after the build finished (newer than /.env_hash), so a 3.10 the image
    # itself ships is still checked.
    # Where uv installs it: the base uses /.uv/python_install, externals /opt/uv/python, sglang /opt/python.
    if [[ "$1" =~ ^(/opt/uv/python|/\.uv/python_install|/opt/python)/cpython-3\.10\.[0-9]+-[^/]+ && -e /.env_hash && "${BASH_REMATCH[0]}" -nt /.env_hash ]]; then
        why="managed Python 3.10 installed at boot by the serverless worker bootstrap; remove when it is fixed"
        [[ "$(date +%F)" > 2026-11-05 ]] && { echo "EXPIRED 2026-11-05: ${why}"; return 1; }
        echo "$why"; return 0
    fi
    return 1
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
judge() {   # $1 = label, $2 = chains accepted
    local why
    if (( $2 == 0 )); then
        echo "  ok    0/${total}  $1"
    elif why=$(allowed "$1"); then
        echo "  allow $2/${total}  $1 [${why}]"
    else
        fail_later "ca-trust:$1" "accepts $2/${total} chains through compromised intermediates${why:+ (${why})}"
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
    judge "$f" "$(accepted -no-CApath -CAfile "$f")"
done < <(find / -xdev -type f \( -name '*.pem' -o -name '*.crt' -o -name '*.cer' -o -name '*.txt' -o -name '*bundle*' \) \
            -size +20k -not -path '/proc/*' -not -path '/sys/*' -not -path "${FIXTURES}/*" \
            -exec grep -l -m1 'BEGIN CERTIFICATE' {} + 2>/dev/null | sort)
(( bundles > 0 )) || fail_later "ca-trust:discovery" "found no CA bundle at all, so nothing was checked"
echo "  bundles checked by content: ${bundles}"

# 3. pip wheels bundled for ensurepip.
while IFS= read -r w; do
    v=$(basename "$w" | sed -E 's/^pip-([^-]+)-.*/\1/')
    if [[ "$w" == /usr/share/python-wheels/* ]]; then
        echo "  allow pip ${v}  $w [Debian/Ubuntu patched wheel reads the OS bundle]"
    elif version_ge "$v" "$PIP_FLOOR"; then
        echo "  ok    pip ${v}  $w"
    elif [[ "$w" == */virtualenv/seed/wheels/embed/* ]] && pys=$(seed_only_below_floor "$w"); then
        echo "  allow pip ${v}  $w [virtualenv seeds it only for Python ${pys}; no pip at the floor supports those]"
    elif why=$(allowed "$w") || why=$(allowed "$(readlink -f "$w")"); then   # a mirrored file is judged by its target
        echo "  allow pip ${v}  $w [${why}]"
    else
        fail_later "ca-trust:$w" "bundled pip ${v} < ${PIP_FLOOR}; every venv made from this interpreter trusts the distrusted CAs${why:+ (${why})}"
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
    elif why=$(allowed "$node"); then echo "  allow node ${v}  $node [${why}]"
    else fail_later "ca-trust:$node" "Node ${v} is below the trust floor for its line (${floor:-no fixed release listed})"; fi
done < <( { type -ap node; find / -xdev -type f -name node -perm -u+x -not -path '/proc/*' 2>/dev/null; } \
            | xargs -r -n1 readlink -f | sort -u)

# 5. uv, every binary.
while IFS= read -r uv; do
    v=$("$uv" --version 2>/dev/null | awk '{print $2}')
    if [[ -n "$v" ]] && version_ge "$v" "$UV_FLOOR"; then echo "  ok    uv ${v}  $uv"
    elif why=$(allowed "$uv"); then echo "  allow uv ${v}  $uv [${why}]"
    else fail_later "ca-trust:$uv" "uv ${v:-unknown} < ${UV_FLOOR} (compiled-in roots)"; fi
done < <( { type -ap uv; find / -xdev -type f -name uv -perm -u+x -not -path '/proc/*' 2>/dev/null; } \
            | xargs -r -n1 readlink -f | sort -u)

report_failures
test_pass "no CA store accepts a chain through a distrusted CA"
