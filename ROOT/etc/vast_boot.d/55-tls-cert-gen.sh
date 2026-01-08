#!/bin/bash

# Generate the Jupyter certificate if run in SSH/Args Jupyter mode
sleep 2
if [[ "${generate_tls_cert}" = "true" ]] && [[ ! -f /etc/instance.key && ! -f /etc/instance.crt ]]; then
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

        openssl req -newkey rsa:2048 -subj "/C=US/ST=CA/CN=jupyter.vast.ai/" -nodes -sha256 -keyout /etc/instance.key -out /etc/instance.csr -config /etc/openssl-san.cnf
        curl --header 'Content-Type: application/octet-stream' --data-binary @//etc/instance.csr -X POST "https://console.vast.ai/api/v0/sign_cert/?instance_id=${CONTAINER_ID:-${VAST_CONTAINERLABEL#C.}}" > /etc/instance.crt;
    fi
fi

# If there is no key present we should ensure supervisor is aware
if [[ ! -f /etc/instance.key || ! -f /etc/instance.crt ]]; then
    export ENABLE_HTTPS=false
fi