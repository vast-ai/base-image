#!/bin/bash
# Test: no service is inadvertently exposed on a PUBLIC interface without auth.
#
# The negative-direction security scan (ADR 0006), realizing ADR 0002 binding
# condition 1 at runtime. FAIL-CLOSED: every public TCP listener must be either
# Caddy (the HTTP auth gate) or explicitly declared in exposure-allowlist/. The
# verdict is decided from (bind address, owning process, allowlist) — NOT by
# sniffing the protocol (a curl probe is fail-open: e.g. jupyter:8080 is HTTP but
# probes http=000). curl is used ONLY to enrich the message.
#
# SHADOW MODE (ADR 0028 binding condition 3). Two verdicts are computed on every
# run: the legacy one below, which still drives the exit code, and the ADR 0028 one
# from exposure_scan.py, which is printed and decides nothing. It stays that way for
# one full base promotion cycle across all twelve configs plus the comfyui and vLLM
# gates, then the new verdict takes over. This replaces "advisory forever", which
# ADR 0006's own amendment identified as a permanent green.
#
# WHAT THE LEGACY VERDICT GETS WRONG, and why it is still here: it treats an
# UNATTRIBUTABLE listener as a WARN, on the inference that a socket with no owning
# process belongs to the platform. That inference is false. `ss -p` resolves
# /proc/<pid>/fd/*, which needs PTRACE_MODE_READ_FSCREDS for another uid, and Vast
# containers do not grant CAP_SYS_PTRACE — measured, as root:
#     ls /proc/783/fd          -> OK
#     readlink /proc/783/fd/3  -> DENIED
# So every service that drops privileges is invisible, WARNs, and does not set the
# return code. On base-qa, where EXPOSURE_ENFORCE is true, the enforced gate passes
# on exactly the listeners it cannot see (ADR 0028).
#
# ADVISORY until a clean baseline is proven across all images (ADR 0006 cond 2):
# violations are reported but the test PASSES unless EXPOSURE_ENFORCE=true.
# TEST_TIMEOUT=180
source "$(dirname "$0")/../lib.sh"

is_serverless && test_skip "serverless: no Caddy gate; exposure model differs (ADR 0006 — serverless rule TODO)"

# `ss -ltnp` only attributes sockets the caller owns, so as a non-root user every
# root-owned listener degrades to an unattributable WARN and the scan reports a
# clean-looking green having decided nothing. For a fail-closed security check a
# silently degraded pass is the worst outcome — skip loudly instead. The automated
# path (runner.sh at boot) is root; this only affects `runner.sh --manual` over SSH.
[[ "$EUID" -eq 0 ]] || test_skip "exposure scan requires root (ss -p cannot attribute other users' sockets; a non-root scan would pass without deciding)"

ALLOW_DIR="$(cd "$(dirname "$0")/.." && pwd)/exposure-allowlist"

scan_out=$(python3 - "$ALLOW_DIR" <<'PY'
import subprocess, sys, os, glob, re

allow_dir = sys.argv[1] if len(sys.argv) > 1 else ""
allow = {}  # (port, proto) -> class

def resolve_port(spec):
    """A literal port, or env:NAME for a port the platform assigns at runtime.

    Vast assigns the public port for some services per-instance (syncthing's sync
    listener is tcp://0.0.0.0:$VAST_TCP_PORT_72299), so a static key can never
    match them. An unset or non-numeric env var resolves to nothing: the port is
    then NOT allowlisted, which is the fail-closed direction.
    """
    if spec.startswith("env:"):
        spec = os.environ.get(spec[4:], "")
    return spec if spec.isdigit() else None

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
                port_spec, proto = key.split("/", 1)
                port = resolve_port(port_spec)
                if port:
                    allow[(port, proto)] = cls
    except OSError:
        pass

def out(cmd):
    """Run a scanner and return stdout; a missing binary is a hard error.

    Returning "" on FileNotFoundError would make "the scanner isn't installed"
    indistinguishable from "nothing is listening" — a fail-OPEN hole in a
    fail-closed check. Exit 2 so the caller can tell tooling failure from a real
    violation (exit 1) and fail regardless of EXPOSURE_ENFORCE.
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout
    except (FileNotFoundError, PermissionError) as e:
        print(f"scanner unavailable: {cmd[0]}: {e}")
        sys.exit(2)

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
        print(f"WARN      {tag:<12} public, unattributable — NOT evidence of a platform "
              f"listener: ss -p cannot see another uid's socket without CAP_SYS_PTRACE, "
              f"so an in-container service that dropped privileges lands here too. "
              f"This WARN does not set the return code; see the ADR 0028 shadow verdict below")
        warns += 1
    elif proto == "udp":
        print(f"WARN      {tag:<12} public UDP ({proc}) — no global auth gate; declare in exposure-allowlist/ if intended")
        warns += 1
    else:
        code = probe(port)
        kind = f"unauthenticated HTTP (probe http={code})" if code != "000" else "non-HTTP TCP"
        print(f"VIOLATION {tag:<12} public ({proc}), not caddy/allowlisted — {kind}; bind loopback or declare in exposure-allowlist/")
        violations += 1

print(f"summary: {violations} violation(s), {warns} warn(s)")
sys.exit(1 if violations else 0)
PY
)
rc=$?
[[ -n "$scan_out" ]] && echo "$scan_out" | sed 's/^/  /'

# ── ADR 0028 verdict, shadow mode: printed, decides nothing yet ───────────────
# Deliberately does NOT touch rc. Binding condition 3 requires one full promotion
# cycle of visible-but-inert output before this becomes the verdict, so that the
# derivative fallout (linux-desktop's x11vnc, UnrealPixelStreaming's coturn) is
# budgeted from real runs rather than discovered by a red gate.
NEW_SCAN="$(dirname "$0")/exposure_scan.py"
if [[ -f "$NEW_SCAN" ]]; then
    echo "  ── ADR 0028 verdict (SHADOW — not gating) ──"
    new_out=$(python3 "$NEW_SCAN" "$ALLOW_DIR" 2>&1); new_rc=$?
    echo "$new_out" | sed 's/^/  /'
    echo "  shadow verdict: exit ${new_rc} (0=clean 1=violations 2=decided nothing) — NOT applied"
else
    echo "  WARN: exposure_scan.py missing — the ADR 0028 shadow verdict did not run"
fi

# rc: 0 = clean, 1 = violations found, 2 = the scan itself could not run.
# A tooling failure is never advisory — it means the check decided nothing, so it
# fails regardless of EXPOSURE_ENFORCE rather than reporting a pass it cannot back.
if [[ "$rc" -ge 2 ]]; then
    test_fail "exposure scan could not run (see above) — the check decided nothing; treat as unverified, not clean"
fi

# ADR 0006 cond 2: advisory until a clean baseline is demonstrated, then promote
# (EXPOSURE_ENFORCE=true, then flip the default). A hard fail on day one would red
# every image and break the baseline-stays-CLEAN protocol.
if [[ "${EXPOSURE_ENFORCE:-false}" == "true" && "$rc" -ne 0 ]]; then
    test_fail "inadvertent public exposure detected (see above) — a public port is neither behind Caddy nor allowlisted"
fi

# The summary line is the only thing most readers see, so it must never claim a
# clean scan when violations were reported. Advisory mode passes the test; it does
# not get to relabel the finding.
if [[ "$rc" -ne 0 ]]; then
    test_pass "exposure scan complete — VIOLATIONS REPORTED above, advisory only (EXPOSURE_ENFORCE=false; set true to gate)"
fi

test_pass "exposure scan complete (enforce=${EXPOSURE_ENFORCE:-false}; no public port outside Caddy/allowlist at scan time)"
