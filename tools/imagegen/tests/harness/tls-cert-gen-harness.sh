#!/bin/bash
# Drive the REAL /etc/vast_boot.d/55-tls-cert-gen.sh through its console-signing
# branches, in a throwaway root, with a shimmed `curl`.
#
# Why a container rather than a unit test: the script writes /etc/instance.key,
# /etc/instance.crt and /etc/.instance-cert-selfsigned at absolute paths and
# reads them back on the NEXT boot. The bugs here are all multi-boot state
# machines — "does this converge, or does it regenerate a keypair forever?" —
# so the test has to be able to boot it repeatedly against persistent /etc.
# Adding a $VAST_CERT_DIR knob to production boot code to avoid that would be
# putting a test seam in the customer's TLS path; a throwaway root is free.
#
# The shim is `curl`: each scenario writes /shim/mode, and the fake curl behaves
# accordingly. Nothing here reaches console.vast.ai.
set -u

# Refuse to run outside a container: this overwrites /etc/instance.{crt,key},
# installs a binary into /opt/instance-tools/bin and moves files around /etc. In
# CI it is inside `docker run --rm`; a developer running it directly would do all
# that to their host. `docker run` sets /.dockerenv.
[[ -f /.dockerenv || -n "${VAST_HARNESS_OK:-}" ]] \
    || { echo "refusing to run outside a container (set VAST_HARNESS_OK to override)"; exit 1; }

BOOT=/etc/vast_boot.d/55-tls-cert-gen.sh
FAIL=0

# The helper is mounted at /src-cert-usable and COPIED into place, not mounted
# there directly: scenario 14 has to be able to remove it, and a bind mount
# cannot be moved out of the way from inside the container.
mkdir -p /opt/instance-tools/bin
cp /src-cert-usable /opt/instance-tools/bin/cert-usable
chmod 755 /opt/instance-tools/bin/cert-usable

mkdir -p /shim
cat > /shim/curl <<'SHIM'
#!/bin/bash
# Args are the real script's; we only care about -o <file> and the mode.
out=""
while [[ $# -gt 0 ]]; do
    [[ "$1" == "-o" ]] && { out="$2"; shift 2; continue; }
    shift
done
mode=$(cat /shim/mode 2>/dev/null)
case "$mode" in
    unreachable)  exit 7 ;;                       # curl's "couldn't connect"
    html)         echo "<html>502</html>" > "$out"; exit 0 ;;
    wrong-key)    cat /shim/wrong.crt > "$out";    exit 0 ;;
    good)
        # Sign the CSR the script just wrote, with the key it just wrote —
        # this is what a healthy console does.
        openssl x509 -req -in /etc/instance.csr -signkey /etc/instance.key \
            -days 365 -sha256 2>/dev/null > "$out"
        exit 0 ;;
esac
exit 7
SHIM
chmod +x /shim/curl
export PATH=/shim:$PATH

# A well-formed certificate for a key that is NOT the instance key. This is the
# input that made parse-only validation non-terminating.
openssl req -newkey rsa:2048 -nodes -subj "/CN=someone-else" \
    -keyout /shim/wrong.key -x509 -days 365 -out /shim/wrong.crt 2>/dev/null

check() { # desc, expected, actual
    if [[ "$2" == "$3" ]]; then
        echo "  ok   $1"
    else
        echo "  FAIL $1: expected [$2] got [$3]"
        FAIL=1
    fi
}

reset_state() { rm -f /etc/instance.{crt,key,csr} /etc/.instance-cert-selfsigned; }

boot() { # mode -> runs one boot, echoes nothing
    echo "$1" > /shim/mode
    ( export generate_tls_cert=true CONTAINER_ID=1; source "$BOOT" ) >/dev/null 2>&1
}

# `source` so ENABLE_HTTPS is observable, the way 65-supervisor-launch sees it.
boot_https() { # mode -> prints the resulting ENABLE_HTTPS
    echo "$1" > /shim/mode
    ( export generate_tls_cert=true CONTAINER_ID=1
      ENABLE_HTTPS=true
      source "$BOOT" >/dev/null 2>&1
      echo "${ENABLE_HTTPS}" )
}

marker() { cat /etc/.instance-cert-selfsigned 2>/dev/null || echo "-"; }
keyfp() { openssl pkey -in /etc/instance.key -pubout 2>/dev/null | sha256sum | cut -c1-16; }
certfp() { openssl x509 -in /etc/instance.crt -noout -fingerprint 2>/dev/null | sha256sum | cut -c1-16; }

echo "=== 1. healthy console: sign, install, no marker ==="
reset_state
check "https enabled"      "true" "$(boot_https good)"
check "no self-signed marker" "-"  "$(marker)"
check "pair is usable"     "0"    "$(/opt/instance-tools/bin/cert-usable >/dev/null 2>&1; echo $?)"
fp1=$(keyfp)
echo "=== 2. a healthy pair is left ALONE on reboot (no key churn) ==="
check "https still enabled" "true" "$(boot_https good)"
check "key unchanged"       "$fp1" "$(keyfp)"

echo "=== 3. console returns a cert for someone else's key ==="
# THE NON-TERMINATING CASE. Parse-only validation installed this, cleared the
# marker, then found the pair mismatched next boot and regenerated — forever,
# announcing success every time while HTTPS was off.
reset_state
check "https enabled (self-signed fallback)" "true" "$(boot_https wrong-key)"
check "marked as self-signed"                "1"    "$(marker)"
check "pair is usable"  "0" "$(/opt/instance-tools/bin/cert-usable >/dev/null 2>&1; echo $?)"
check "cert is OURS, not the console's" "" \
      "$(openssl x509 -in /etc/instance.crt -noout -subject | grep -o someone-else)"

echo "=== 4. the retry is BOUNDED and then converges ==="
# Boots 2 and 3 retry; boot 4 must not, and the counter must not climb past 3.
boot wrong-key; check "attempt 2" "2" "$(marker)"
fp3=$(keyfp)
boot wrong-key; check "attempt 3" "3" "$(marker)"
fp4=$(keyfp)
check "boot 3 did regenerate the key (it was still retrying)" "differ" \
      "$([[ "$fp3" != "$fp4" ]] && echo differ || echo same)"
for i in 5 6 7 8; do boot wrong-key; done
check "counter clamped at the limit" "3" "$(marker)"
check "converged: no further key churn" "$fp4" "$(keyfp)"

echo "=== 5. console unreachable behaves the same way ==="
reset_state
check "https enabled" "true" "$(boot_https unreachable)"
check "marked"        "1"    "$(marker)"
for i in 2 3 4 5 6; do boot unreachable; done
check "clamped"       "3"    "$(marker)"

echo "=== 6. console returns an HTML error page ==="
reset_state
check "https enabled (not the HTML)" "true" "$(boot_https html)"
check "cert is a real certificate" "0" \
      "$(openssl x509 -in /etc/instance.crt -noout >/dev/null 2>&1; echo $?)"
check "marked" "1" "$(marker)"

echo "=== 7. a mismatched pair on disk is REPLACED, not kept ==="
# /etc persists across stop/start, so a bad pair used to last the instance's life.
reset_state
boot good
cp /shim/wrong.crt /etc/instance.crt          # simulate a half-finished regen
check "https enabled again" "true" "$(boot_https good)"
check "pair is usable" "0" "$(/opt/instance-tools/bin/cert-usable >/dev/null 2>&1; echo $?)"

echo "=== 8. an operator-mangled marker does not crash the arithmetic ==="
# `$(( 08 ))` is a fatal "value too great for base" error, not 8 — bash reads a
# leading zero as octal. _cert_attempts runs on EVERY boot via _cert_retry_due,
# so an unparseable count takes the whole boot script down, not just the retry.
reset_state
boot good
run_boot() { # mode -> all output, so an arithmetic error is visible
    echo "$1" > /shim/mode
    ( export generate_tls_cert=true CONTAINER_ID=1; source "$BOOT" ) 2>&1
}
# 08 is over the limit, so the retry is correctly NOT due — the point here is
# that reading it does not abort the boot.
echo "08" > /etc/.instance-cert-selfsigned
out=$(run_boot unreachable)
check "08 does not crash the boot"   "" "$(grep -o 'value too great for base' <<< "$out")"
check "08 counts as 8, so no retry"  "08" "$(marker)"
# 01 is under the limit, so this one goes all the way through the arithmetic.
echo "01" > /etc/.instance-cert-selfsigned
out=$(run_boot unreachable)
check "01 does not crash the boot"   "" "$(grep -o 'value too great for base' <<< "$out")"
check "01 counts as 1 and increments" "2" "$(marker)"

echo "=== 9. no usable pair => ENABLE_HTTPS=false ==="
# The one case where TLS must be switched OFF rather than served badly. Note
# what does NOT belong here: an empty or unreadable key is not this case, it is
# a recoverable one — the script regenerates and self-signs, and HTTPS stays up.
reset_state
check "not requested, none on disk" "false" "$(ENABLE_HTTPS=true; generate_tls_cert=false; \
    source "$BOOT" >/dev/null 2>&1; echo "$ENABLE_HTTPS")"

# And the genuinely broken case: requested, console unreachable, and the
# self-signed fallback itself fails. Shim openssl to break only that one call.
cat > /shim/openssl <<'SHIM'
#!/bin/bash
if [[ "$(cat /shim/break 2>/dev/null)" == "selfsign" && "$1" == "x509" && "$*" == *-req* ]]; then
    echo "openssl: forced failure" >&2; exit 1
fi
exec /usr/bin/openssl "$@"
SHIM
chmod +x /shim/openssl
# bash caches command lookups; openssl was resolved to /usr/bin many scenarios
# ago and the subshells inherit that hash table, so the shim would be ignored.
hash -r
reset_state
echo selfsign > /shim/break
out=$(run_boot unreachable)
check "reports the failure"  "Error: self-signed fallback failed; HTTPS will be disabled below" \
      "$(grep -o 'Error: self-signed fallback failed; HTTPS will be disabled below' <<< "$out")"
check "https disabled" "false" "$(boot_https unreachable)"

echo "=== 10. the counter is CLAMPED while the self-sign keeps failing ==="
# The clamp is unreachable in scenario 4: once a self-sign succeeds the pair is
# usable, the guard stops re-entering, and the counter stops on its own. It only
# matters in THIS state — self-sign broken, so `! _cert_usable` re-enters on
# every boot regardless of the marker. Unclamped the count climbs for the life
# of the instance and every message reads "attempt 47/3".
for i in 1 2 3 4 5; do run_boot unreachable >/dev/null; done
check "counter clamped at the limit" "3" "$(marker)"
out=$(run_boot unreachable)
check "and says so" "giving up" "$(grep -o 'giving up' <<< "$out")"
rm -f /shim/break /shim/openssl; hash -r

echo "=== 11. a present-but-unusable pair turns HTTPS OFF ==="
# The final guard was reverted to existence-only under mutation and every
# scenario still passed: 9 only ever tested states where the files were ABSENT,
# so `-f` and "usable" agreed. This is the state where they disagree, and it is
# the original bug — a zero-byte or HTML instance.crt satisfies -f, so HTTPS
# stayed enabled over a certificate nothing could read.
reset_state
boot good
cp /shim/wrong.crt /etc/instance.crt          # parses, unexpired, WRONG key
check "mismatched pair, generation off => https off" "false" \
      "$(ENABLE_HTTPS=true; generate_tls_cert=false; \
         source "$BOOT" >/dev/null 2>&1; echo "$ENABLE_HTTPS")"
echo "<html>502</html>" > /etc/instance.crt    # unparseable
check "HTML cert, generation off => https off" "false" \
      "$(ENABLE_HTTPS=true; generate_tls_cert=false; \
         source "$BOOT" >/dev/null 2>&1; echo "$ENABLE_HTTPS")"

echo "=== 12. an EXPIRED but matched pair is still SERVED ==="
# Exit code 3. The guard that decides whether to regenerate treats it as a
# reason to regenerate; the guard that decides whether supervisor serves TLS
# treats it as yes — because its alternative is plaintext on the same public
# port, and an expired certificate still encrypts. Mutating either guard to use
# the other's policy must be visible here.
reset_state
openssl req -newkey rsa:2048 -nodes -subj "/CN=t" \
    -keyout /etc/instance.key -out /tmp/e.csr 2>/dev/null
mkdir -p /tmp/ca/newcerts; : > /tmp/ca/index.txt; echo 1000 > /tmp/ca/serial
printf '[ca]\ndefault_ca=D\n[D]\ndir=/tmp/ca\ndatabase=$dir/index.txt\n'\
'new_certs_dir=$dir/newcerts\nserial=$dir/serial\ndefault_md=sha256\npolicy=p\n'\
'email_in_dn=no\nrand_serial=no\nunique_subject=no\n[p]\ncommonName=optional\n' > /tmp/ca.cnf
openssl req -newkey rsa:2048 -nodes -subj "/CN=ca" -keyout /tmp/ca.key \
    -x509 -days 3650 -out /tmp/ca.crt 2>/dev/null
openssl ca -batch -config /tmp/ca.cnf -cert /tmp/ca.crt -keyfile /tmp/ca.key \
    -startdate 20200101000000Z -enddate 20200102000000Z \
    -in /tmp/e.csr -out /etc/instance.crt >/dev/null 2>&1
check "helper reports expired-but-matched" "3" \
      "$(/opt/instance-tools/bin/cert-usable >/dev/null 2>&1; echo $?)"
check "generation off => https stays ON"  "true" \
      "$(ENABLE_HTTPS=true; generate_tls_cert=false; \
         source "$BOOT" >/dev/null 2>&1; echo "$ENABLE_HTTPS")"
check "generation on  => replaced with a fresh cert" "0" \
      "$(echo good > /shim/mode
         ( export generate_tls_cert=true CONTAINER_ID=1; source "$BOOT" ) >/dev/null 2>&1
         /opt/instance-tools/bin/cert-usable >/dev/null 2>&1; echo $?)"

echo "=== 13. the marker is CLEARED once the console succeeds ==="
# Dropping `rm -f "$_CERT_MARKER"` passed every scenario: none set up
# "marker present + console healthy". Without it the instance keeps retrying —
# a fresh keypair every boot until the counter reaches the limit — despite
# holding a properly signed certificate.
reset_state
boot unreachable                               # marker = 1
check "marked after a failure" "1" "$(marker)"
boot good                                      # console recovers
check "marker cleared on success" "-" "$(marker)"
fp=$(keyfp)
boot good
check "and no further key churn" "$fp" "$(keyfp)"

echo "=== 14. a MISSING helper stops, it does not churn keys ==="
# "Fails closed" was a warning followed by carrying on. With the helper absent
# every check returns 127, so the regeneration guard was true on every boot:
# a new keypair, a CSR and up to four console POSTs, forever, with even a good
# response rejected. Doing nothing is the conservative act here.
reset_state
boot good
before_key=$(keyfp)
before_crt=$(certfp)
mv /opt/instance-tools/bin/cert-usable /tmp/cert-usable-hidden
out=$(run_boot good)
# The SPECIFIC message, not a bare "Error:" — scenario 9 also emits an "Error:"
# line, so grep -o 'Error:' cannot discriminate a missing helper from any other.
check "says the helper is missing" "missing or not executable" \
      "$(grep -o 'missing or not executable' <<< "$out" | head -1)"
check "key NOT regenerated"      "$before_key" "$(keyfp)"
# IDENTITY, not parseability: a self-sign would leave a cert that still PARSES
# while replacing the pair. The fingerprint must be byte-for-byte the one on disk
# before the helper vanished.
check "existing cert UNCHANGED"  "$before_crt" "$(certfp)"
check "https disabled"           "false" "$(boot_https good)"
mv /tmp/cert-usable-hidden /opt/instance-tools/bin/cert-usable

echo "=== 15. a syntactically BROKEN helper (bash exit 2) fails CLOSED ==="
# The exit-code collision: bash's own exit status for a syntax error is 2, and
# the tolerated non-zero at the final guard is 3 (expired), NOT 2. So a truncated
# or corrupt cert-usable must turn HTTPS OFF, never be read as "expired, serve
# anyway" — a fail-open at the one TLS gate. Shim the helper to `exit 2` and the
# final guard must disable HTTPS.
reset_state
boot good                                      # a genuinely good pair on disk
cp /opt/instance-tools/bin/cert-usable /tmp/cert-usable-real
printf '#!/bin/bash\nif then\n' > /opt/instance-tools/bin/cert-usable   # bash syntax error => exit 2
chmod 755 /opt/instance-tools/bin/cert-usable
check "broken helper exits 2" "2" \
      "$(/opt/instance-tools/bin/cert-usable >/dev/null 2>&1; echo $?)"
check "broken helper => https OFF (fail closed)" "false" \
      "$(ENABLE_HTTPS=true; generate_tls_cert=false; \
         source "$BOOT" >/dev/null 2>&1; echo "$ENABLE_HTTPS")"
cp /tmp/cert-usable-real /opt/instance-tools/bin/cert-usable
rm -f /tmp/cert-usable-real

[[ $FAIL -eq 0 ]] && echo "ALL SCENARIOS OK" || echo "SCENARIOS FAILED"
exit $FAIL
