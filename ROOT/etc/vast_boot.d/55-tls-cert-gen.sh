#!/bin/bash

# Generate the Jupyter certificate if run in SSH/Args Jupyter mode
sleep 2

# Regenerate when the key or the cert is missing OR the pair on disk is not
# usable. The old condition required BOTH files to be absent, so an instance that
# once received a bad cert kept it for its whole life — /etc persists across
# stop/start, and nothing here ever looked at the contents.
#
# "Usable" is ONE predicate, shared with base/27-caddy-tls.sh and the portal's
# caddy_config_manager, and it lives in the helper rather than here. (The portal
# also carries an in-process fallback copy for OLDER images that predate the
# helper — ADR 0026 binding condition 3 — so "one implementation" means one per
# co-shipped artifact, not zero copies anywhere.) Three hand-rolled copies of
# this question had grown three different answers — two of which reject a valid
# EC keypair and turn HTTPS off (see the helper's own header, and linter rule
# L066). Do not re-implement it.
# EXIT CODE 3 (matched but expired) is a regenerate HERE, unlike at the portal:
# at boot a fresh keypair costs milliseconds and the console will sign it, so
# there is no reason to keep a lapsed certificate. `! _cert_usable` therefore
# means "0 is fine, anything else is not", which is the strict reading and the
# right default for every caller that is not a TLS front door.
#
# stderr is NOT swallowed. The helper's whole output is the reason a pair was
# rejected ("does not match", "has expired"), and the boot log is the only place
# an operator will look.
_CERT_USABLE=/opt/instance-tools/bin/cert-usable
_cert_usable() { "$_CERT_USABLE" "${1:-/etc/instance.crt}" "${2:-/etc/instance.key}"; }

# A MISSING HELPER MUST STOP, NOT PROCEED.
#
# Printing a warning and carrying on looked like failing closed and was not.
# With the helper absent every `_cert_usable` returns 127, so the guard below is
# true on EVERY boot: a fresh 2048-bit keypair, a CSR, up to four POSTs to
# console.vast.ai, a self-sign — forever, and a perfectly good console response
# is rejected too, because the same broken predicate validates it. That is the
# unbounded key churn this file exists to end, re-entered through a different
# door, and fleet-wide it is sustained load on the signing endpoint.
#
# So: leave whatever is on disk alone, and let the final guard turn HTTPS off.
# Doing nothing is the conservative act here; regenerating is not.
# SANITY, NOT PRESENCE. `-x` alone asks whether a file is there, and a helper
# that is present but BROKEN — truncated, a partial layer, a bad HOTFIX_SCRIPT, a
# future edit with a syntax error — passes that gate and then fails every
# predicate it is asked. The regeneration guard below is one of those, so the
# instance regenerates a keypair and POSTs a CSR on EVERY boot, forever: the
# exact churn described above, re-entered through the one door the presence
# check leaves open. Measured before this probe existed: five boots, five
# different keys.
#
# The probe is the helper's own contract, on inputs that cannot exist: two empty
# paths must be rejected as unusable (exit 1) with a `cert-usable:` reason. A
# broken interpreter cannot produce that pair — bash's own syntax-error exit is
# 2 and it prints no such prefix — so anything else routes into the same
# leave-it-alone path as a missing helper.
_CERT_HELPER_OK=true
_cert_helper_sane() {
    local out rc
    out=$("$_CERT_USABLE" /nonexistent-crt /nonexistent-key 2>&1); rc=$?
    (( rc == 1 )) && [[ "$out" == cert-usable:* ]]
}
if [[ ! -x "$_CERT_USABLE" ]]; then
    _CERT_HELPER_OK=false
    echo "Error: ${_CERT_USABLE} is missing or not executable; cannot validate" >&2
    echo "       the instance certificate. Leaving the existing pair untouched" >&2
    echo "       and disabling HTTPS. This is a broken image, not a host fault." >&2
elif ! _cert_helper_sane; then
    _CERT_HELPER_OK=false
    echo "Error: ${_CERT_USABLE} is present but does not answer its own contract;" >&2
    echo "       treating it as missing. Leaving the existing pair untouched and" >&2
    echo "       disabling HTTPS. This is a broken image, not a host fault." >&2
fi

# Retry a self-signed fallback, but BOUNDEDLY. Re-entering on the marker alone
# meant a host that can never reach console.vast.ai (blocked egress — the QA
# selector already floors host reliability because such hosts get rented)
# generated a fresh RSA keypair and paid a 3-retry curl on EVERY boot, forever,
# and every client that had accepted the previous self-signed cert had to accept
# a new one each restart. After _CERT_RETRY_LIMIT attempts we keep what we have.
_CERT_RETRY_LIMIT=3
_CERT_MARKER=/etc/.instance-cert-selfsigned
# Read the attempt count with an EXPLICIT RADIX. `$(( 08 + 1 ))` is a fatal
# "value too great for base" error, not a 9: bash reads a leading zero as octal.
# Nothing this file writes has a leading zero, but the marker is a plain file in
# /etc that an operator is invited (by its own comment) to look at and reset.
_cert_attempts() {
    local n; n=$(head -1 "$_CERT_MARKER" 2>/dev/null)
    [[ "$n" =~ ^[0-9]+$ ]] || n=0
    echo $(( 10#$n ))
}
_cert_retry_due() {
    [[ -f "$_CERT_MARKER" ]] || return 1
    (( $(_cert_attempts) < _CERT_RETRY_LIMIT ))
}

if [[ "$_CERT_HELPER_OK" = true ]] && [[ "${generate_tls_cert}" = "true" ]] \
   && { [[ ! -f /etc/instance.key ]] || ! _cert_usable || _cert_retry_due; }; then
    # This guard protects the CONFIG only. It used to wrap the signing too, so a
    # boot that had decided the cert needed replacing did nothing at all when
    # openssl-san.cnf was already present — which is every boot after the first.
    if [ ! -f /etc/openssl-san.cnf ] || ! grep -qi vast /etc/openssl-san.cnf; then
        echo "Generating certificates"
        echo '[req]' > /etc/openssl-san.cnf;
        echo 'default_bits       = 2048' >> /etc/openssl-san.cnf;
        echo 'distinguished_name = req_distinguished_name' >> /etc/openssl-san.cnf;
        echo 'req_extensions     = v3_req' >> /etc/openssl-san.cnf;

        echo '[req_distinguished_name]' >> /etc/openssl-san.cnf;
        echo 'countryName         = US' >> /etc/openssl-san.cnf;
        echo 'stateOrProvinceName = CA' >> /etc/openssl-san.cnf;
        echo 'organizationName    = Vast.ai Inc.' >> /etc/openssl-san.cnf;
        echo 'commonName          = vast.ai' >> /etc/openssl-san.cnf;

        echo '[v3_req]' >> /etc/openssl-san.cnf;
        echo 'basicConstraints = CA:FALSE' >> /etc/openssl-san.cnf;
        echo 'keyUsage         = nonRepudiation, digitalSignature, keyEncipherment' >> /etc/openssl-san.cnf;
        echo 'subjectAltName   = @alt_names' >> /etc/openssl-san.cnf;

        echo '[alt_names]' >> /etc/openssl-san.cnf;
        echo 'IP.1   = 0.0.0.0' >> /etc/openssl-san.cnf;
    fi

    openssl req -newkey rsa:2048 -subj "/C=US/ST=CA/CN=jupyter.vast.ai/" -nodes -sha256 -keyout /etc/instance.key -out /etc/instance.csr -config /etc/openssl-san.cnf

    # VALIDATE BEFORE INSTALLING.
    #
    # This used to redirect curl's stdout straight into /etc/instance.crt with
    # no -f, no status check and no retry. Any response body — a 5xx page, an
    # HTML error, a JSON fault — became "the certificate", and `>` created the
    # file even when curl wrote nothing at all. The guard below then only asked
    # whether the file EXISTED, so ENABLE_HTTPS stayed true and Caddy served a
    # TLS listener with an unusable certificate: broken HTTPS on the customer's
    # Jupyter, for the duration of the instance, caused by someone else's bad
    # afternoon.
    #
    # Fetch to a temp file, prove it is usable, and only then install it.
    #
    # AGAINST THE KEY WE JUST GENERATED, not merely parseable. Parse-only was the
    # first version of this check and it is a NON-TERMINATING loop: a console that
    # returns a well-formed certificate for some other key passes the parse, gets
    # installed, and clears the marker — then the guard at the top finds the pair
    # mismatched on the next boot and regenerates, forever, printing
    # "signed by the Vast console" every time while HTTPS is off. Validating with
    # the same predicate the guard uses is what closes it: the two can no longer
    # disagree about the same file.
    _signed=$(mktemp)
    if curl -fsS --retry 3 --retry-connrefused --retry-delay 2 --max-time 30 \
            --header 'Content-Type: application/octet-stream' \
            --data-binary @//etc/instance.csr \
            -X POST "https://console.vast.ai/api/v0/sign_cert/?instance_id=${CONTAINER_ID:-${VAST_CONTAINERLABEL#C.}}" \
            -o "$_signed" \
       && _cert_usable "$_signed" /etc/instance.key; then
        mv "$_signed" /etc/instance.crt
        # mktemp gives 0600; the self-signed branch writes 0644 under the boot
        # shell's umask. The certificate is public data (the KEY is the secret),
        # so make the two paths agree rather than leaving the mode dependent on
        # which branch ran.
        chmod 644 /etc/instance.crt
        rm -f "$_CERT_MARKER"
        echo "Instance certificate signed by the Vast console"
    else
        # SELF-SIGN RATHER THAN GO WITHOUT.
        #
        # Deliberately not "leave no cert and set ENABLE_HTTPS=false": TLS would
        # silently disappear on a console blip, and base/27-caddy-tls asserts the
        # cert is ALWAYS present — a missing one is a hard failure there, so that
        # fallback would still red the gate while ALSO downgrading the customer.
        # A self-signed cert keeps HTTPS working (with a browser warning, which
        # is what a signed-by-Vast cert for IP 0.0.0.0 largely gets anyway) and
        # keeps the assertion true.
        rm -f "$_signed"
        # Marked, for two reasons. It makes the state greppable for an operator
        # ("why is my Jupyter cert self-signed?"), and the regeneration guard at
        # the top uses it to RETRY the signing on the next boot — otherwise one
        # console blip at first boot downgrades that instance permanently, which
        # is the same stickiness this file exists to end.
        #
        # CLAMPED at the limit rather than left to grow. The counter only stops
        # the loop while the self-sign below SUCCEEDS — if it fails we have no
        # usable pair, the guard at the top re-enters on every boot regardless of
        # the marker, and an uncapped counter climbs for the life of the instance
        # while every message still reads "attempt N/3". Clamping keeps the file
        # bounded and the message honest about which state we are actually in.
        _n=$(( $(_cert_attempts) + 1 ))
        (( _n > _CERT_RETRY_LIMIT )) && _n=$_CERT_RETRY_LIMIT
        echo "$_n" > "$_CERT_MARKER"
        echo "Warning: could not obtain a signed certificate; using a self-signed one"
        if (( _n < _CERT_RETRY_LIMIT )); then
            echo "         (attempt ${_n}/${_CERT_RETRY_LIMIT}; will retry next boot)"
        else
            echo "         (attempt ${_n}/${_CERT_RETRY_LIMIT}; giving up — keeping the self-signed cert)"
        fi
        openssl x509 -req -in /etc/instance.csr -signkey /etc/instance.key \
            -days 365 -sha256 -extensions v3_req -extfile /etc/openssl-san.cnf \
            -out /etc/instance.crt 2>/dev/null \
            || echo "Error: self-signed fallback failed; HTTPS will be disabled below"
    fi
fi

# If there is no SERVABLE key and cert, supervisor must know. Checking existence
# alone was the second half of the same defect: a zero-byte or HTML instance.crt
# satisfies -f, so HTTPS stayed enabled over a certificate nothing could read.
#
# NOTE THE DIFFERENT POLICY FROM THE GUARD ABOVE, and that it is deliberate.
# That guard asks "should I regenerate?" and takes the strict reading. This one
# asks "can supervisor serve TLS with what is on disk?" — the same question
# caddy_config_manager asks — and an expired-but-matched pair (exit 3) answers
# yes. Its alternative is not a better certificate, it is plaintext on the same
# public port with the portal token in ?token=, and an expired certificate still
# encrypts. Reaching here with an expired pair means the block above declined to
# regenerate (generate_tls_cert is not true, or the helper is missing), so there
# is no fresher certificate on offer either way.
#
# Any OTHER non-zero code — 1 (unusable), 127 (helper gone), or bash's own 2
# from a syntactically broken helper — turns HTTPS off. 3 is the ONLY tolerated
# non-zero, which is why the helper reports expiry as 3 and not 2: a broken
# helper must fail closed here, never read as "expired, serve anyway".
#
# `_cert_rc=0; _cert_usable || _cert_rc=$?` rather than `_cert_usable; _cert_rc=$?`
# so a future `set -e` (or a BOOT_SCRIPT override that sets it) cannot abort the
# boot on this one unguarded non-zero exit before supervisor is even launched.
_cert_rc=0; _cert_usable || _cert_rc=$?
if [[ ! -s /etc/instance.key ]] || { (( _cert_rc != 0 )) && (( _cert_rc != 3 )); }; then
    export ENABLE_HTTPS=false
elif (( _cert_rc == 3 )); then
    # Serving an expired-but-matched pair. Say why HTTPS shows a date error and
    # what fixes it, matching the portal's own message — the tolerant branch
    # otherwise prints only cert-usable's stderr, and the customer-visible effect
    # ("my browser says the certificate expired") has no explanation in the log.
    echo "Instance certificate has EXPIRED but still matches its key; serving TLS" >&2
    echo "with it rather than plaintext. Restarting the instance regenerates it" >&2
    echo "when certificate generation (generate_tls_cert) is enabled — which, if" >&2
    echo "you are seeing this, it is not. See docs/runbooks/tls-certificates.md" >&2
    echo "in github.com/vast-ai/base-image." >&2
fi
