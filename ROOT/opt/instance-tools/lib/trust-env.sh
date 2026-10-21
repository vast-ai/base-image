# Point OUR OWN Python, pip and uv clients at the OS CA store, which apt keeps current.
# Sourced by the services we own (portal, tunnel manager, provisioner, update-portal,
# model-ui) and nowhere else: set image-wide it would override users' certifi, conda's
# OpenSSL and private-CA setups (ADR 0058). A pinned certifi in a venv we ship goes stale
# the day a root is distrusted; the OS store moves with the next apt upgrade.
#
# Defaults only: a value the template or user already set wins. The provisioner runs user
# scripts, and this must not override their choices.
: "${SSL_CERT_FILE:=/etc/ssl/certs/ca-certificates.crt}"
: "${REQUESTS_CA_BUNDLE:=/etc/ssl/certs/ca-certificates.crt}"
: "${PIP_CERT:=/etc/ssl/certs/ca-certificates.crt}"
: "${UV_NATIVE_TLS:=1}"
export SSL_CERT_FILE REQUESTS_CA_BUNDLE PIP_CERT UV_NATIVE_TLS
