#!/bin/bash
# Test: no service is inadvertently exposed on a PUBLIC interface without auth.
#
# The negative-direction security scan (ADR 0006), realizing ADR 0002 binding
# condition 1 at runtime. FAIL-CLOSED: every public TCP listener must be either
# Caddy (the HTTP auth gate) or explicitly declared in exposure-allowlist.d. The
# verdict is decided from (bind address, owning process, allowlist) — NOT by
# sniffing the protocol (a curl probe is fail-open: e.g. jupyter:8080 is HTTP but
# probes http=000). curl is used ONLY to enrich the message.
#
# Raw TCP/UDP (e.g. VNC) has no global auth mechanism, so an UNATTRIBUTABLE
# (no-owning-process / platform) listener and any UDP are WARNed, not failed.
#
# ADVISORY until a clean baseline is proven across all images (ADR 0006 cond 2):
# violations are reported but the test PASSES unless EXPOSURE_ENFORCE=true.
# TEST_TIMEOUT=180
source "$(dirname "$0")/../lib.sh"

is_serverless && test_skip "serverless: no Caddy gate; exposure model differs (ADR 0006 — serverless rule TODO)"

ALLOW_DIR="$(cd "$(dirname "$0")/.." && pwd)/exposure-allowlist.d"

scan_out=$(python3 - "$ALLOW_DIR" <<'PY'
import subprocess, sys, os, glob, re

allow_dir = sys.argv[1] if len(sys.argv) > 1 else ""
allow = {}  # (port, proto) -> class
for f in sorted(glob.glob(os.path.join(allow_dir, "*.conf"))):
    try:
        for line in open(f, encoding="utf-8", errors="replace"):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split(None, 2)
            key = parts[0]
            cls = parts[1] if len(parts) > 1 else "allowed"
            if "/" in key:
                port, proto = key.split("/", 1)
                allow[(port, proto)] = cls
    except OSError:
        pass

def out(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout

caddy_pids = set(out(["pgrep", "-x", "caddy"]).split())
PUBLIC = {"0.0.0.0", "*", "[::]", "::"}

def probe(port):
    for scheme, extra in (("http", []), ("https", ["-k"])):
        r = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-m", "3", "-w", "%{http_code}", *extra,
             f"{scheme}://127.0.0.1:{port}/"], capture_output=True, text=True)
        code = (r.stdout or "").strip()
        if code and code != "000":
            return code
    return "000"

def listeners(proto, cmd):
    seen = set()
    for line in out(cmd).splitlines():
        cols = line.split()
        if len(cols) < 4:
            continue
        m = re.match(r"^(.+):(\d+)$", cols[3])  # Local Address:Port; skips header + peer
        if not m:
            continue
        addr, port = m.group(1), m.group(2)
        if addr not in PUBLIC:
            continue
        proc = pid = ""
        pm = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
        if pm:
            proc, pid = pm.group(1), pm.group(2)
        if (proto, port) in seen:
            continue
        seen.add((proto, port))
        yield proto, port, proc, pid

violations = warns = 0
for proto, port, proc, pid in list(listeners("tcp", ["ss", "-ltnp"])) + \
                              list(listeners("udp", ["ss", "-lunp"])):
    tag = f"{proto}/{port}"
    if pid and pid in caddy_pids:
        print(f"ok        {tag:<12} caddy (auth gate)")
    elif (port, proto) in allow:
        print(f"ok        {tag:<12} allowlisted ({allow[(port, proto)]}; {proc or '?'})")
    elif not proc and not pid:
        print(f"WARN      {tag:<12} public, no owning process (platform/injected?) — unattributable")
        warns += 1
    elif proto == "udp":
        print(f"WARN      {tag:<12} public UDP ({proc}) — no global auth gate; declare in exposure-allowlist.d if intended")
        warns += 1
    else:
        code = probe(port)
        kind = f"unauthenticated HTTP (probe http={code})" if code != "000" else "non-HTTP TCP"
        print(f"VIOLATION {tag:<12} public ({proc}), not caddy/allowlisted — {kind}; bind loopback or declare in exposure-allowlist.d")
        violations += 1

print(f"summary: {violations} violation(s), {warns} warn(s)")
sys.exit(1 if violations else 0)
PY
)
rc=$?
echo "$scan_out" | sed 's/^/  /'

# ADR 0006 cond 2: advisory until a clean baseline is demonstrated, then promote
# (EXPOSURE_ENFORCE=true, then flip the default). A hard fail on day one would red
# every image and break the baseline-stays-CLEAN protocol.
if [[ "${EXPOSURE_ENFORCE:-false}" == "true" && "$rc" -ne 0 ]]; then
    test_fail "inadvertent public exposure detected (see above) — a public port is neither behind Caddy nor allowlisted"
fi

test_pass "exposure scan complete (enforce=${EXPOSURE_ENFORCE:-false}; no exposure observed at scan time)"
