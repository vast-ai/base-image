#!/bin/bash

# Generate the Jupyter certificate if run in SSH/Args Jupyter mode
sleep 2
# Regenerate when the key or the cert is missing OR the cert on disk is not a
# parseable certificate. The old condition required BOTH files to be absent, so
# an instance that once received a bad cert kept it for its whole life — /etc
# persists across stop/start, and nothing here ever looked at the contents.
_cert_usable() {
    [[ -s /etc/instance.crt ]] || return 1
    openssl x509 -in /etc/instance.crt -noout >/dev/null 2>&1 || return 1
    # Parseable is not the same as usable, and stopping at parseable would repeat
    # the shape of the bug this file fixes one layer down. An EXPIRED cert parses;
    # so does one whose key no longer matches, which is what a half-finished
    # regeneration leaves behind. Both make Caddy serve a listener nothing will
    # talk to, and neither would ever be replaced because the guard was happy.
    openssl x509 -in /etc/instance.crt -noout -checkend 0 >/dev/null 2>&1 || return 1
    [[ -s /etc/instance.key ]] || return 1
    # Compare PUBLIC KEYS, not moduli. `openssl rsa -modulus` is RSA-only: on a
    # valid EC keypair the cert yields "Modulus=No modulus for this public key
    # type" and the key yields nothing, so they compare unequal and a perfectly
    # good certificate is declared unusable — HTTPS silently off. We only ever
    # generate RSA, so this bit only an operator-supplied cert, which is exactly
    # the case nobody would have tested.
    local c k
    c=$(openssl x509 -in /etc/instance.crt -noout -pubkey 2>/dev/null \
        | openssl pkey -pubin -outform DER 2>/dev/null | sha256sum)
    k=$(openssl pkey -in /etc/instance.key -pubout -outform DER 2>/dev/null | sha256sum)
    [[ -n "$c" && "$c" == "$k" ]]
}

# Retry a self-signed fallback, but BOUNDEDLY. Re-entering on the marker alone
# meant a host that can never reach console.vast.ai (blocked egress — the QA
# selector already floors host reliability because such hosts get rented)
# generated a fresh RSA keypair and paid a 3-retry curl on EVERY boot, forever,
# and every client that had accepted the previous self-signed cert had to accept
# a new one each restart. After _CERT_RETRY_LIMIT attempts we keep what we have.
_CERT_RETRY_LIMIT=3
_cert_retry_due() {
    [[ -f /etc/.instance-cert-selfsigned ]] || return 1
    local n; n=$(head -1 /etc/.instance-cert-selfsigned 2>/dev/null)
    [[ "$n" =~ ^[0-9]+$ ]] || n=0
    (( n < _CERT_RETRY_LIMIT ))
}

if [[ "${generate_tls_cert}" = "true" ]] && { [[ ! -f /etc/instance.key ]] || ! _cert_usable \
     || _cert_retry_due; }; then
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
    _signed=$(mktemp)
    if curl -fsS --retry 3 --retry-connrefused --retry-delay 2 --max-time 30 \
            --header 'Content-Type: application/octet-stream' \
            --data-binary @//etc/instance.csr \
            -X POST "https://console.vast.ai/api/v0/sign_cert/?instance_id=${CONTAINER_ID:-${VAST_CONTAINERLABEL#C.}}" \
            -o "$_signed" \
       && openssl x509 -in "$_signed" -noout >/dev/null 2>&1; then
        mv "$_signed" /etc/instance.crt
        # mktemp gives 0600; the self-signed branch writes 0644 under the boot
        # shell's umask. The certificate is public data (the KEY is the secret),
        # so make the two paths agree rather than leaving the mode dependent on
        # which branch ran.
        chmod 644 /etc/instance.crt
        rm -f /etc/.instance-cert-selfsigned
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
        _n=$(head -1 /etc/.instance-cert-selfsigned 2>/dev/null)
        [[ "$_n" =~ ^[0-9]+$ ]] || _n=0
        echo $(( _n + 1 )) > /etc/.instance-cert-selfsigned
        echo "Warning: could not obtain a signed certificate; using a self-signed one"
        if (( _n + 1 < _CERT_RETRY_LIMIT )); then
            echo "         (attempt $(( _n + 1 ))/${_CERT_RETRY_LIMIT}; will retry next boot)"
        else
            echo "         (attempt $(( _n + 1 ))/${_CERT_RETRY_LIMIT}; giving up — keeping the self-signed cert)"
        fi
        openssl x509 -req -in /etc/instance.csr -signkey /etc/instance.key \
            -days 365 -sha256 -extensions v3_req -extfile /etc/openssl-san.cnf \
            -out /etc/instance.crt 2>/dev/null \
            || echo "Error: self-signed fallback failed; HTTPS will be disabled below"
    fi
fi

# If there is no USABLE key and cert, supervisor must know. Checking existence
# alone was the second half of the same defect: a zero-byte or HTML instance.crt
# satisfies -f, so HTTPS stayed enabled over a certificate nothing could read.
if [[ ! -s /etc/instance.key ]] || ! _cert_usable; then
    export ENABLE_HTTPS=false
fi