#!/bin/bash
# Replace the pip wheel that ensurepip bundles inside every interpreter we ship, so a venv a
# user creates with `python -m venv` starts with a pip that does not trust a distrusted CA
# (ADR 0058). pip always loads its vendored certifi IN ADDITION to the system store, so only a
# newer pip fixes it; the bundled wheel is as old as the interpreter. Build-time only.
#
# Debian/Ubuntu interpreters have no _bundled directory (their ensurepip uses the
# distro-patched wheel in /usr/share/python-wheels, which reads the OS store) and are skipped.
set -euo pipefail

PIP_VERSION=26.2.2
PIP_URL=https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl
PIP_SHA256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70

wheel="pip-${PIP_VERSION}-py3-none-any.whl"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
# The hash, not the TLS connection, is what makes this wheel trustworthy.
curl -fsSL --retry 5 --retry-all-errors -o "$work/$wheel" "$PIP_URL"
echo "${PIP_SHA256}  $work/$wheel" | sha256sum -c --quiet -

while IFS= read -r init; do
    dir=$(dirname "$init")
    [[ -d "$dir/_bundled" ]] || continue
    current=$(sed -nE 's/^_PIP_VERSION = "([^"]+)"$/\1/p' "$init")
    [[ -n "$current" ]] || { echo "refresh-bundled-pip: no _PIP_VERSION in $init" >&2; exit 1; }
    if [[ "$(printf '%s\n%s\n' "$current" "$PIP_VERSION" | sort -V | head -1)" == "$PIP_VERSION" ]]; then
        continue                                       # already at or above the floor
    fi
    rm -f "$dir"/_bundled/pip-*.whl
    cp "$work/$wheel" "$dir/_bundled/$wheel"
    sed -i -E "s/^_PIP_VERSION = \"[^\"]+\"$/_PIP_VERSION = \"${PIP_VERSION}\"/" "$init"
    echo "refresh-bundled-pip: $dir pip ${current} -> ${PIP_VERSION}"
done < <(find / -xdev -path '*/ensurepip/__init__.py' -not -path '/proc/*' 2>/dev/null)
