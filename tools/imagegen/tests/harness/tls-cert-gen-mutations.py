#!/usr/bin/env python3
"""Mutation check for the ADR 0049 refresh in 55-tls-cert-gen.sh.

Breaks one guard at a time in a copy of the boot script and runs the container
harness against it. Every mutation must turn at least one scenario red; a
survivor means that guard is untested. Not collected by pytest (it runs the
harness once per mutation, in docker): run it by hand after changing the
refresh, as ADR 0026 binding condition 2 and ADR 0049 binding condition 2 ask.

    python3 tools/imagegen/tests/harness/tls-cert-gen-mutations.py
"""
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
BOOT = REPO / "ROOT/etc/vast_boot.d/55-tls-cert-gen.sh"
HARNESS = Path(__file__).resolve().parent / "tls-cert-gen-harness.sh"
HELPER = REPO / "ROOT/opt/instance-tools/bin/cert-usable"
SETUP = ("{ apt-get update -qq && apt-get install -y -qq openssl ; } >/dev/null 2>&1; "
         "bash /harness.sh")

GET = 'curl -fsS --max-time 5 -A "$_CERT_UA" -K -'
SAN_CHECK = '\\\n       && grep -qxF "$live" <<< "$(_cert_ipv4_sans "$signed")"; then'
MUTATIONS = [  # (guard, original, mutant)
    ("provenance check",
     '    [[ -s "$_CERT_CONSOLE" && "$(_cert_fingerprint)" == "$(cat "$_CERT_CONSOLE")" ]] || return 0\n', ""),
    ("an empty marker is not ours", '[[ -s "$_CERT_CONSOLE" && ', "[[ "),
    ("Jupyter direct mode skipped", "    if [[ -f /.launch ]] && grep -q '/api/v0/sign_cert/' /.launch; then return 0; fi\n", ""),
    ("only direct mode skipped", "grep -q '/api/v0/sign_cert/' /.launch", "grep -qi jupyter /.launch"),
    ("off switch", '[[ "${off,,}" != "false" ]]', "[[ true ]]"),
    ("off switch ignores case", '"${off,,}"', '"${off}"'),
    ("no API key, no read", '[[ -n "${CONTAINER_API_KEY:-}" && -n "$id" ]]', '[[ -n "$id" ]]'),
    ("key on stdin", GET, GET + ' -H "Authorization: Bearer $CONTAINER_API_KEY"'),
    ("address read not retried", GET, GET.replace("--max-time 5", "--retry 3 --max-time 5")),
    ("address read identifies itself", GET, GET.replace(' -A "$_CERT_UA"', "")),
    ("signing request identifies itself", '--retry-delay 5 --max-time 30 -A "$_CERT_UA"', "--retry-delay 5 --max-time 30"),
    ("signing request retried", "--retry 2 --retry-delay 5 ", ""),
    ("live address must be global", '    if ! _cert_is_global_ipv4 "$live"; then', '    if [[ -z "$live" ]]; then'),
    ("octets in range", "    (( a <= 255 && b <= 255 && c <= 255 && d <= 255 )) || return 1\n", ""),
    ("0/8 10/8 127/8 224+ not global", "    (( a == 0 || a == 10 || a == 127 || a >= 224 )) && return 1\n", ""),
    ("100.64/10 not global", "    (( a == 100 && b >= 64 && b <= 127 )) && return 1\n", ""),
    ("169.254/16 not global", "    (( a == 169 && b == 254 )) && return 1\n", ""),
    ("172.16/12 not global", "    (( a == 172 && b >= 16 && b <= 31 )) && return 1\n", ""),
    ("192.168/16 192.0.0/24 192.0.2/24 not global", "    (( a == 192 && (b == 168 || (b == 0 && (c == 0 || c == 2))) )) && return 1\n", ""),
    ("198.18/15 198.51.100/24 not global", "    (( a == 198 && (b == 18 || b == 19 || (b == 51 && c == 100)) )) && return 1\n", ""),
    ("203.0.113/24 not global", "    (( a == 203 && b == 0 && c == 113 )) && return 1\n", ""),
    ("a match signs nothing", '    if grep -qxF "$live" <<< "$sans"; then', "    if false; then"),
    ("a match is exact", 'grep -qxF "$live" <<< "$sans"', 'grep -qF "$live" <<< "$sans"'),
    ("per-address bound", "if (( 10#$tries >= _CERT_RETRY_LIMIT )); then", "if false; then"),
    ("a new address resets the budget",
     '[[ "$tries" =~ ^[0-9]+$ && "$last" == "$live" ]] || tries=0', '[[ "$tries" =~ ^[0-9]+$ ]] || tries=0'),
    ("only an answer counts", "    if (( rc != 0 )); then", "    if false; then"),
    ("existing key kept", "openssl req -new -key /etc/instance.key -subj",
     "openssl req -newkey rsa:2048 -nodes -keyout /etc/instance.key -subj"),
    ("new certificate matches the key",
     '    if _cert_usable "$signed" /etc/instance.key 2>/dev/null \\\n       && grep', "    if grep"),
    ("new certificate names the address", SAN_CHECK, "; then"),
    ("failed request keeps the pair",
     '        rm -f "$signed"\n        echo "Certificate IP refresh: SAN ${sans//$\'\\n\'/,}, live ${live}; signing request failed',
     '        mv "$signed" /etc/instance.crt\n        echo "Certificate IP refresh: SAN ${sans//$\'\\n\'/,}, live ${live}; signing request failed'),
    ("wrong answer keeps the pair", '    rm -f "$signed"\n    n=$(( 10#$tries + 1 ))',
     '    mv "$signed" /etc/instance.crt\n    n=$(( 10#$tries + 1 ))'),
    ("provenance follows a refresh", '        _cert_fingerprint > "$_CERT_CONSOLE"\n        rm -f "$_CERT_IP_MARKER"',
     '        rm -f "$_CERT_IP_MARKER"'),
    ("success clears the count", '        rm -f "$_CERT_IP_MARKER"\n        echo', "        echo"),
    ("provenance written on first install",
     '        _cert_fingerprint > "$_CERT_CONSOLE"\n        echo "Instance certificate signed by the Vast console"',
     '        echo "Instance certificate signed by the Vast console"'),
    ("refresh needs generate_tls_cert",
     'if [[ "$_CERT_HELPER_OK" = true ]] && [[ "${generate_tls_cert}" = "true" ]]; then\n    _cert_ip_refresh',
     'if [[ "$_CERT_HELPER_OK" = true ]]; then\n    _cert_ip_refresh'),
]


def run(boot: Path) -> str:
    args = ["docker", "run", "--rm", "-v", f"{HARNESS}:/harness.sh:ro",
            "-v", f"{boot}:/etc/vast_boot.d/55-tls-cert-gen.sh:ro",
            "-v", f"{HELPER}:/src-cert-usable:ro", "ubuntu:24.04", "bash", "-c", SETUP]
    return subprocess.run(args, capture_output=True, text=True).stdout


def main() -> int:
    source = BOOT.read_text()
    bad = [name for name, old, _ in MUTATIONS if source.count(old) != 1]
    if bad:
        print("patterns no longer match the boot script (update this file):", *bad, sep="\n  ")
        return 2
    if "ALL SCENARIOS OK" not in run(BOOT):
        print("baseline does not pass; fix that first")
        return 2
    tmp = Path(tempfile.mkdtemp())

    def one(i_m):
        i, (name, old, new) = i_m
        mutant = tmp / f"55-{i}.sh"
        mutant.write_text(source.replace(old, new))
        out = run(mutant)
        fails = [line.strip() for line in out.splitlines() if "FAIL " in line]
        return name, "ALL SCENARIOS OK" in out, fails

    survivors = 0
    with ThreadPoolExecutor(6) as pool:
        for name, survived, fails in pool.map(one, enumerate(MUTATIONS)):
            survivors += survived
            print(f"{'SURVIVED' if survived else 'killed  '} {name}"
                  + ("" if survived else f"  ({fails[0][:90]})"))
    print(f"{len(MUTATIONS) - survivors}/{len(MUTATIONS)} killed")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
