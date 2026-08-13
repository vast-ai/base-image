#!/bin/bash

# Generate the Jupyter certificate if run in SSH/Args Jupyter mode
sleep 2
# Regenerate when the key or the cert is missing OR the cert on disk is not a
# parseable certificate. The old condition required BOTH files to be absent, so
# an instance that once received a bad cert kept it for its whole life — /etc
# persists across stop/start, and nothing here ever looked at the contents.
_cert_usable() {
    [[ -s /etc/instance.crt ]] && openssl x509 -in /etc/instance.crt -noout >/dev/null 2>&1
}

if [[ "${generate_tls_cert}" = "true" ]] && { [[ ! -f /etc/instance.key ]] || ! _cert_usable; }; then
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
    # Fetch to a temp file, prove it parses, and only then install it.
    _signed=$(mktemp)
    if curl -fsS --retry 3 --retry-connrefused --retry-delay 2 --max-time 30 \
            --header 'Content-Type: application/octet-stream' \
            --data-binary @//etc/instance.csr \
            -X POST "https://console.vast.ai/api/v0/sign_cert/?instance_id=${CONTAINER_ID:-${VAST_CONTAINERLABEL#C.}}" \
            -o "$_signed" \
       && openssl x509 -in "$_signed" -noout >/dev/null 2>&1; then
        mv "$_signed" /etc/instance.crt
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
        echo "Warning: could not obtain a signed certificate; using a self-signed one"
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