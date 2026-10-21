#!/bin/bash
# Fail unless the OS CA store has dropped every root in distrusted-roots.txt (ADR 0058).
# Run at build time right after ca-certificates is upgraded, before anything else is fetched
# over TLS. Checks both the per-root file and the bundle CONTENT, so a distro that names a
# root differently, or a store that was never regenerated, cannot pass by absence.
set -euo pipefail

list=/opt/instance-tools/lib/distrusted-roots.txt
bundle=/etc/ssl/certs/ca-certificates.crt
[[ -s "$bundle" ]] || { echo "assert-distrust: no OS bundle at $bundle" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
awk -v d="$work" '/BEGIN CERTIFICATE/{n++} n{print > (d "/" n ".pem")}' "$bundle"
present=$(for f in "$work"/*.pem; do openssl x509 -in "$f" -noout -fingerprint -sha256; done \
          | sed -E 's/.*=//; s/://g' | tr 'a-f' 'A-F')
[[ $(wc -l <<< "$present") -ge 50 ]] || { echo "assert-distrust: could not read $bundle" >&2; exit 1; }

bad=0
while IFS='|' read -r name fp; do
    [[ -z "$name" || "$name" == \#* ]] && continue
    if [[ -e "/etc/ssl/certs/${name}.pem" ]] || grep -qx "$fp" <<< "$present"; then
        echo "assert-distrust: ${name} is still trusted by the OS store" >&2
        bad=1
    fi
done < "$list"
exit "$bad"
