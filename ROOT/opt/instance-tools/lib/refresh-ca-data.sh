#!/bin/bash
# Bring the CA data that does not follow the OS store on its own up to date, wherever it is
# in the image (ADR 0058). Build-time only; run it as the last step of any build that
# installs Python packages or interpreters, because each install can bring a stale copy back.
#
# 1. grpcio ships its own roots.pem and ignores SSL_CERT_FILE: link it to the OS bundle, the
#    way Debian does for certifi.
# 2. ensurepip bundles a pip wheel inside every interpreter, and `python -m venv` puts it in
#    each new venv. pip always loads its vendored certifi IN ADDITION to the system store, so
#    only a newer pip fixes it. Debian/Ubuntu interpreters have no _bundled directory (their
#    ensurepip uses the distro-patched wheel, which reads the OS store) and are skipped.
set -euo pipefail

find / -xdev -path '*/grpc/_cython/_credentials/roots.pem' -type f -not -path '/proc/*' \
    -exec ln -sf /etc/ssl/certs/ca-certificates.crt {} \;

PIP_VERSION=26.2.2
PIP_URL=https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl
PIP_SHA256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70
PIP_MIN_PYTHON=10              # pip 26.2.2: Requires-Python >=3.10

wheel="pip-${PIP_VERSION}-py3-none-any.whl"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
# The hash, not the TLS connection, is what makes this wheel trustworthy.
curl -fsSL --retry 5 --retry-all-errors -o "$work/$wheel" "$PIP_URL"
echo "${PIP_SHA256}  $work/$wheel" | sha256sum -c --quiet -

while IFS= read -r init; do
    dir=$(dirname "$init")
    [[ -d "$dir/_bundled" ]] || continue
    minor=$(sed -nE 's#.*/lib/python3\.([0-9]+)/ensurepip$#\1#p' <<< "$dir")
    if [[ -z "$minor" || "$minor" -lt "$PIP_MIN_PYTHON" ]]; then
        echo "refresh-ca-data: $dir is not Python >= 3.${PIP_MIN_PYTHON}; bundled pip left as is" >&2
        continue
    fi
    current=$(sed -nE 's/^_PIP_VERSION = "([^"]+)"$/\1/p' "$init")
    [[ -n "$current" ]] || { echo "refresh-ca-data: no _PIP_VERSION in $init" >&2; exit 1; }
    if [[ "$(printf '%s\n%s\n' "$current" "$PIP_VERSION" | sort -V | head -1)" == "$PIP_VERSION" ]]; then
        continue                                       # already at or above the floor
    fi
    rm -f "$dir"/_bundled/pip-*.whl
    cp "$work/$wheel" "$dir/_bundled/$wheel"
    sed -i -E "s/^_PIP_VERSION = \"[^\"]+\"$/_PIP_VERSION = \"${PIP_VERSION}\"/" "$init"
    echo "refresh-ca-data: $dir pip ${current} -> ${PIP_VERSION}"
done < <(find / -xdev -path '*/ensurepip/__init__.py' -not -path '/proc/*' 2>/dev/null)
