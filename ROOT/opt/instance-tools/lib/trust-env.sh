# Point OUR OWN Python, pip and uv clients at the OS CA store, which apt keeps current.
# Sourced by the services we own (portal, tunnel manager, provisioner, update-portal,
# model-ui) and nowhere else: set image-wide it would override users' certifi, conda's
# OpenSSL and private-CA setups (ADR 0058). A pinned certifi in a venv we ship goes stale
# the day a root is distrusted; the OS store moves with the next apt upgrade.
#
# Defaults only: a value the template or user already set wins. The provisioner runs user
# scripts, and this must not override their choices.
#
# No PIP_CERT: pip always loads its vendored certifi IN ADDITION to any bundle it is given, so
# for pip only a current pip helps (the image ships one, and refresh-ca-data.sh fixes the
# copies ensurepip hands to new venvs).
: "${SSL_CERT_FILE:=/etc/ssl/certs/ca-certificates.crt}"
: "${REQUESTS_CA_BUNDLE:=/etc/ssl/certs/ca-certificates.crt}"
: "${UV_SYSTEM_CERTS:=true}"
export SSL_CERT_FILE REQUESTS_CA_BUNDLE UV_SYSTEM_CERTS
