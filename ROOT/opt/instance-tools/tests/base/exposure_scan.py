#!/usr/bin/env python3
"""The exposure verdict, computed from declaration rather than attribution (ADR 0028).

WHY THIS EXISTS AS A MODULE. The scan it replaces was a heredoc inside
`28-inadvertent-exposure.sh`, which meant its only test was a rented GPU. Its central
defect was invisible for months precisely because nothing could exercise it off-box:
`ss -p` attributes a socket by resolving `/proc/<pid>/fd/*`, which needs
PTRACE_MODE_READ_FSCREDS for another uid, and Vast containers do not grant
CAP_SYS_PTRACE. Measured on a live instance, as root:

    ls /proc/783/fd          -> OK
    readlink /proc/783/fd/3  -> DENIED      (what `ss -p` actually needs)

So every service that drops privileges was unattributable, took the
"platform/injected? — unattributable" branch, and was downgraded from VIOLATION to
WARN. WARNs do not set the return code. On base-qa, where EXPOSURE_ENFORCE is true,
**the enforced gate passed on exactly the listeners it could not see.**

THE FIX IS NOT MORE ATTRIBUTION. ADR 0028 binding condition 6 forbids reintroducing
attribution as a verdict input even if CAP_SYS_PTRACE is granted later: the lesson is
not that ptrace was missing, it is that a security verdict resting on a capability the
image does not control degrades silently. So the verdict is f(public bind,
declaration). Ownership is recovered from /proc/self/net/* and used only to make the
MESSAGE honest — naming a uid instead of blaming the platform.

SHADOW MODE. Binding condition 3: this ships computing both verdicts for one full base
promotion cycle, with the OLD one driving the exit code, before it decides anything.
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys

PUBLIC_V4 = "00000000"                      # 0.0.0.0
PUBLIC_V6 = {"0" * 32, "0" * 24 + "FFFF" + "00000000"}   # :: and ::ffff:0.0.0.0
TCP_LISTEN = "0A"


# ───────────────────────────────────────────────────────────── /proc/net parsing

def decode_addr(hex_addr: str) -> str:
    """Canonical text form of a /proc/net hex address.

    IPv4 is a single little-endian word; IPv6 is four of them. Returned upper-case
    and unchanged in width so the caller can compare against the wildcard constants
    without re-deriving endianness.
    """
    return hex_addr.upper()


def is_public(hex_addr: str) -> bool:
    """True if the socket is bound to a wildcard address rather than an interface.

    A wildcard bind is the whole question: it is what makes a service reachable from
    outside the container if the port is ever published. A loopback bind is not
    exposure however the port map is configured.
    """
    a = decode_addr(hex_addr)
    if len(a) == 8:
        return a == PUBLIC_V4
    return a in PUBLIC_V6


def parse_proc_net(text: str, want_listen: bool) -> list[dict]:
    """Rows of /proc/net/{tcp,tcp6,udp,udp6} as {addr, port, state, uid, inode}.

    Deliberately tolerant of trailing columns, which differ across kernels, and
    strict about the leading ten, which have not moved. `want_listen` filters TCP to
    state 0A; UDP has no listen state, so every row is a candidate.
    """
    rows = []
    for line in text.splitlines()[1:]:            # drop the header
        cols = line.split()
        if len(cols) < 10:
            continue
        local, state, uid, inode = cols[1], cols[3], cols[7], cols[9]
        if ":" not in local:
            continue
        addr, _, port_hex = local.rpartition(":")
        if want_listen and state.upper() != TCP_LISTEN:
            continue
        try:
            port = int(port_hex, 16)
        except ValueError:
            continue
        rows.append({"addr": addr, "port": port, "state": state.upper(),
                     "uid": int(uid) if uid.isdigit() else -1, "inode": inode})
    return rows


def read_proc_net(proto: str, root: str = "/proc/self/net") -> list[dict]:
    """Listening sockets for a protocol across its v4 and v6 tables.

    /proc/self/net rather than /proc/net: identical content, and it is the path that
    stays correct inside a network namespace.
    """
    rows = []
    for suffix in ("", "6"):
        path = os.path.join(root, proto + suffix)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                rows.extend(parse_proc_net(fh.read(), want_listen=(proto == "tcp")))
        except OSError:
            continue
    return rows


# ───────────────────────────────────────────────────────────── ownership (message only)

def inode_owners(pids_root: str = "/proc") -> dict[str, tuple[str, str]]:
    """socket inode -> (pid, comm), for every process whose fds we can actually read.

    This is the half that CANNOT work for other uids without CAP_SYS_PTRACE, and that
    is not a bug to route around — it is the reason the verdict must not depend on it.
    What it produces is a better MESSAGE: a socket we cannot attribute is reported
    with its uid and the reason it is invisible, rather than as the platform's fault.
    """
    owners: dict[str, tuple[str, str]] = {}
    for fd_dir in glob.glob(os.path.join(pids_root, "[0-9]*", "fd")):
        pid = fd_dir.split(os.sep)[-2]
        try:
            entries = os.listdir(fd_dir)
        except OSError:
            continue                     # the expected case for another uid
        comm = ""
        try:
            with open(os.path.join(pids_root, pid, "comm"), encoding="utf-8") as fh:
                comm = fh.read().strip()
        except OSError:
            pass
        for entry in entries:
            try:
                target = os.readlink(os.path.join(fd_dir, entry))
            except OSError:
                continue                 # DENIED without CAP_SYS_PTRACE
            if target.startswith("socket:["):
                owners.setdefault(target[8:-1], (pid, comm))
    return owners


def uid_name(uid: int) -> str:
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except Exception:                                             # noqa: BLE001
        return str(uid)


def describe_owner(row: dict, owners: dict[str, tuple[str, str]]) -> str:
    """The honest sentence about who holds this socket.

    Three cases, and the middle one is the whole point of ADR 0028: a non-root
    service is invisible to us BY DESIGN of the container's capabilities, and saying
    "platform/injected?" about it was a guess that happened to be wrong.
    """
    hit = owners.get(row["inode"])
    if hit:
        return f"{hit[1] or '?'} pid={hit[0]}"
    if row["uid"] != 0:
        return (f"owner=uid{row['uid']}({uid_name(row['uid'])}) ino={row['inode']} "
                "no visible process (non-root in-container service)")
    return (f"foreign: uid0 ino={row['inode']} no visible root process holds it")


# ───────────────────────────────────────────────────────────── allowlist grammar

class AllowEntry:
    """One resolved allowlist line, kept with its source so the table can name it."""

    def __init__(self, spec: str, proto: str, cls: str, note: str, source: str):
        self.spec, self.proto, self.cls, self.note, self.source = spec, proto, cls, note, source
        self.ports: set[int] = set()
        self.ephemeral = False
        self.unresolved = ""

    def matches(self, port: int, proto: str) -> bool:
        return proto == self.proto and port in self.ports


def _ephemeral_range(root: str = "/proc/sys/net/ipv4/ip_local_port_range") -> tuple[int, int]:
    try:
        with open(root, encoding="utf-8") as fh:
            lo, hi = fh.read().split()[:2]
            return int(lo), int(hi)
    except Exception:                                             # noqa: BLE001
        return (32768, 60999)                                     # kernel default


def resolve_spec(spec: str, env: dict) -> tuple[set[int], bool, str]:
    """Turn one port spec into concrete ports (ADR 0028 decision 7).

        1234              a literal
        env:VAR           a port the platform assigns per instance
        env:VAR|1234      env with a literal fallback — coturn binds
                          ${VAST_UDP_PORT_70000:-3478}, which `env:` alone
                          structurally cannot describe
        envlist:VAR       whitespace/comma list from one variable (AUTH_EXCLUDE)
        range:LO-HI       a contiguous range, for a service whose ports are
                          assigned within a window it was told to use
        ephemeral         the kernel's local port range, resolved at scan time

    Returns (ports, is_ephemeral, unresolved_reason). An empty set with a reason is
    the FAIL-CLOSED outcome: the entry allowlists nothing and says why, rather than
    silently widening or silently vanishing.
    """
    spec = spec.strip()
    if spec == "ephemeral":
        lo, hi = _ephemeral_range()
        return set(range(lo, hi + 1)), True, ""
    if spec.startswith("range:"):
        body = spec[6:]
        m = re.fullmatch(r"(\d+)-(\d+)", body)
        if not m:
            return set(), False, f"malformed range {body!r}"
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            return set(), False, f"inverted range {body!r}"
        return set(range(lo, hi + 1)), False, ""
    if spec.startswith("envlist:"):
        raw = env.get(spec[8:], "")
        ports = {int(p) for p in re.split(r"[,\s]+", raw) if p.isdigit()}
        return ports, False, "" if ports else f"{spec} resolved to nothing"
    if spec.startswith("env:"):
        body = spec[4:]
        var, _, fallback = body.partition("|")
        val = env.get(var, "")
        if val.isdigit():
            return {int(val)}, False, ""
        if fallback.isdigit():
            return {int(fallback)}, False, ""
        return set(), False, f"{spec} resolved to nothing"
    if spec.isdigit():
        return {int(spec)}, False, ""
    return set(), False, f"unparseable port spec {spec!r}"


# `internal` is the one class that makes a NEGATIVE claim, and it exists because an
# allowlist entry otherwise says nothing about reachability. The other classes mean
# "this may bind a public interface"; sshd and syncthing are also PUBLISHED, and that
# is correct — they authenticate. `internal` means "may bind wide inside the
# container, and must never appear in the published port map", which is the only
# honest way to declare a service that has no auth of its own and cannot be moved to
# loopback. Without it, declaring Ray's 121-port range would have silently permitted
# a template to publish an unauthenticated GCS to the internet, and the scan would
# have printed `ok`.
INTERNAL_CLASS = "internal"
VALID_CLASSES = {"raw", "self-auth-http", "harness", "platform", "transient", INTERNAL_CLASS}


def load_allowlist(allow_dir: str, env: dict) -> tuple[list[AllowEntry], list[str]]:
    """Parse every fragment; return entries and grammar complaints.

    Complaints are returned rather than raised: a bad line must be visible in the
    printed table AND must not take the whole scan down, or one typo becomes
    "the check decided nothing" for every image at once.
    """
    entries, problems = [], []
    for path in sorted(glob.glob(os.path.join(allow_dir, "*.conf"))):
        name = os.path.basename(path)
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError as e:
            problems.append(f"{name}: unreadable ({e})")
            continue
        for lineno, raw in enumerate(text.splitlines(), 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split(None, 2)
            key = parts[0]
            cls = parts[1] if len(parts) > 1 else "allowed"
            note = parts[2] if len(parts) > 2 else ""
            if "/" not in key:
                problems.append(f"{name}:{lineno}: no /proto in {key!r}")
                continue
            spec, proto = key.rsplit("/", 1)
            if proto not in ("tcp", "udp"):
                problems.append(f"{name}:{lineno}: unknown proto {proto!r}")
                continue
            if cls not in VALID_CLASSES:
                problems.append(f"{name}:{lineno}: unknown class {cls!r}")
                continue
            entry = AllowEntry(spec, proto, cls, note, f"{name}:{lineno}")
            entry.ports, entry.ephemeral, entry.unresolved = resolve_spec(spec, env)
            if entry.unresolved:
                problems.append(f"{name}:{lineno}: {entry.unresolved}")
            entries.append(entry)
    return entries, problems


# ───────────────────────────────────────────────────────────── Caddy, by behaviour

def caddyfile_ports(path: str = "/etc/Caddyfile") -> set[int]:
    """Ports Caddy DECLARES as site addresses.

    Half of the behavioural test. The other half is that the front actually
    challenges — declaring a site address proves configuration, not enforcement.
    """
    ports: set[int] = set()
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return ports
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or not line.endswith("{"):
            continue
        for tok in re.findall(r":(\d+)", line.split("{")[0]):
            ports.add(int(tok))
    return ports


def challenges(port: int, runner=subprocess.run) -> bool:
    """True iff an unauthenticated request to this port is refused with 401/403.

    ADR 0028 decision 4: a port passes as a Caddy front on BEHAVIOUR, not identity.
    The pid check it replaces only ever proved "caddy owns this socket" and never
    "this front actually gates" — and it carried a latent trap, since caddy is
    attributable today only because it runs as root, so an identity-based pass would
    flip every front to violation the day caddy drops privileges.
    """
    for scheme, extra in (("https", ["-k"]), ("http", [])):
        try:
            r = runner(["curl", "-s", "-o", "/dev/null", "-m", "5", "-w", "%{http_code}",
                        *extra, f"{scheme}://127.0.0.1:{port}/"],
                       capture_output=True, text=True)
        except Exception:                                         # noqa: BLE001
            continue
        if (r.stdout or "").strip() in ("401", "403"):
            return True
    return False


# ───────────────────────────────────────────────────────────── the verdict

def published_ports(env: dict) -> set[int]:
    """Ports the platform actually forwards, from the VAST_TCP_PORT_*/UDP map."""
    out: set[int] = set()
    for k, v in env.items():
        if k.startswith(("VAST_TCP_PORT_", "VAST_UDP_PORT_")) and str(v).isdigit():
            out.add(int(v))
        if k.startswith(("VAST_TCP_PORT_", "VAST_UDP_PORT_")):
            tail = k.rsplit("_", 1)[-1]
            if tail.isdigit():
                out.add(int(tail))
    return out


def classify(rows, entries, env, owners, caddy_ports, challenge=challenges):
    """One verdict per public listener. Returns (findings, decided_nothing_reason).

    Order matters and is not arbitrary: allowlist BEFORE caddy, because a declared
    port is a human statement and should not depend on a live probe; caddy second,
    because it is behavioural and can fail; everything else is a violation.
    """
    findings = []
    eph_lo, eph_hi = _ephemeral_range()
    pub = published_ports(env)
    # One verdict per (proto, port). A service bound on both v4 and v6 is two rows in
    # /proc and one fact about exposure; reporting it twice inflates the count a human
    # reads and would double-charge a violation.
    seen: set[tuple[str, int]] = set()
    for row in rows:
        if not is_public(row["addr"]):
            continue
        proto, port = row["proto"], row["port"]
        if (proto, port) in seen:
            continue
        seen.add((proto, port))
        who = describe_owner(row, owners)
        hit = next((e for e in entries if e.matches(port, proto)), None)
        if hit and hit.cls == INTERNAL_CLASS and port in pub:
            # The declaration is conditional and the condition just failed. An
            # `internal` service has no auth of its own — that is why it was allowed
            # to bind wide at all — so publishing it puts an unauthenticated service
            # on the internet. This must outrank the allowlist rather than defer to
            # it: the entry permits the BIND, never the publication.
            findings.append(("VIOLATION", proto, port,
                             f"declared `internal` in {hit.source} but the template PUBLISHES "
                             f"it (VAST_{proto.upper()}_PORT map) — an internal service has no "
                             f"auth of its own; unmap the port or put it behind Caddy — {who}"))
            continue
        if hit:
            findings.append(("ok", proto, port, f"allowlisted ({hit.cls}; {hit.source}) — {who}"))
            continue
        if proto == "tcp" and port in caddy_ports:
            if challenge(port):
                findings.append(("ok", proto, port, f"caddy front, challenged 401/403 — {who}"))
            else:
                findings.append(("VIOLATION", proto, port,
                                 f"declared a Caddy site address but did NOT challenge an "
                                 f"unauthenticated request — the gate is configured and not "
                                 f"enforcing — {who}"))
            continue
        if proto == "udp" and eph_lo <= port <= eph_hi and port not in pub:
            findings.append(("transient", proto, port,
                             f"ephemeral UDP, not in the published port map — {who}"))
            continue
        findings.append(("VIOLATION", proto, port,
                         f"public, neither declared nor a challenged Caddy front — {who}; "
                         "bind loopback or declare in exposure-allowlist/"))
    return findings


# ───────────────────────────────────────────────────────────── self-defence

def cross_check(rows, ss_text: str) -> str:
    """Non-empty if `ss` sees a listener the /proc parser did not (ADR 0028 cond 6).

    A parsing bug in the /proc layout fails silently OPEN, which is the one direction
    a fail-closed check must never fail. `ss` is used for the listener SET only — no
    -p, no -e — so this cross-check does not reintroduce the attribution dependency
    the ADR removes.
    """
    ours = {(r["proto"], r["port"]) for r in rows}
    missing = []
    for line in ss_text.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 5:
            continue
        m = re.match(r"^(.+):(\d+)$", cols[4] if cols[0] in ("tcp", "udp") else cols[3])
        if not m:
            continue
        proto = cols[0] if cols[0] in ("tcp", "udp") else "tcp"
        port = int(m.group(2))
        if m.group(1) in ("0.0.0.0", "*", "[::]", "::") and (proto, port) not in ours:
            missing.append(f"{proto}/{port}")
    return ("ss reports public listeners the /proc parser missed: "
            + ", ".join(sorted(set(missing)))) if missing else ""


def caddy_socket_check(rows, owners, runner=subprocess.run) -> str:
    """Non-empty if caddy is running but holds none of the sockets we found.

    The other self-defence arm: it means our socket enumeration is not seeing what
    the machine is actually doing, so the scan decided nothing.
    """
    try:
        r = runner(["pgrep", "-x", "caddy"], capture_output=True, text=True)
    except Exception:                                             # noqa: BLE001
        return ""
    pids = set((r.stdout or "").split())
    if not pids:
        return ""
    # Can we see into caddy AT ALL? If its fds are unreadable then this arm cannot
    # distinguish "the scan is blind to this machine" from "caddy runs as another
    # uid and we lack CAP_SYS_PTRACE" — and the second is a state decision 4
    # explicitly anticipates, since it hardens Caddy's pass precisely so caddy can
    # be dropped to an unprivileged uid one day. Tripping here would make that
    # hardening turn every scan into "decided nothing", which is a hard fail
    # regardless of EXPOSURE_ENFORCE. Only a pid we CAN read licenses a conclusion.
    readable = set()
    for pid in pids:
        try:
            os.listdir(os.path.join("/proc", pid, "fd"))
            readable.add(pid)
        except OSError:
            continue
    if not readable:
        return ""
    held = {owners.get(row["inode"], ("", ""))[0] for row in rows if row["inode"] in owners}
    if not (readable & held):
        return (f"caddy is running and readable (pid {','.join(sorted(readable))}) but holds "
                "none of the "
                "enumerated sockets — the scan is not seeing this machine's listeners")
    return ""


# ───────────────────────────────────────────────────────────── entry point

def gather(proc_root="/proc/self/net"):
    rows = []
    for proto in ("tcp", "udp"):
        for row in read_proc_net(proto, proc_root):
            row["proto"] = proto
            rows.append(row)
    return rows


def main(argv=None, out=sys.stdout, env=None) -> int:
    """Print the resolved allowlist, then a verdict per public listener.

    Exit: 0 clean, 1 violations, 2 the scan decided nothing. The caller keeps the
    2-is-never-advisory rule that ADR 0006 condition 6 already established.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    allow_dir = argv[0] if argv else ""
    env = dict(os.environ if env is None else env)

    entries, problems = load_allowlist(allow_dir, env)

    # The resolved table comes FIRST and always. ADR 0028 decision 7: an entry that
    # silently resolved to nothing must be visible here, not an inexplicable red
    # hours later in someone else's cell.
    print("  -- allowlist, resolved --", file=out)
    for e in entries:
        if e.unresolved:
            shown = f"RESOLVED TO NOTHING ({e.unresolved})"
        elif e.ephemeral:
            lo, hi = _ephemeral_range()
            shown = f"{lo}-{hi} (ephemeral, read at scan time)"
        elif len(e.ports) > 4:
            shown = f"{min(e.ports)}-{max(e.ports)} ({len(e.ports)} ports)"
        else:
            shown = ",".join(str(p) for p in sorted(e.ports)) or "(none)"
        print(f"     {e.source:<22} {e.spec}/{e.proto:<4} {e.cls:<15} -> {shown}", file=out)
    if not entries:
        print("     (no fragments found)", file=out)
    for p in problems:
        print(f"     GRAMMAR {p}", file=out)

    rows = gather()
    owners = inode_owners()

    try:
        ss_text = subprocess.run(["ss", "-ltun"], capture_output=True, text=True).stdout
    except Exception:                                             # noqa: BLE001
        ss_text = ""
    for reason in (cross_check(rows, ss_text), caddy_socket_check(rows, owners)):
        if reason:
            print(f"  SCAN UNDECIDED: {reason}", file=out)
            return 2

    findings = classify(rows, entries, env, owners, caddyfile_ports())
    violations = 0
    print("  -- listeners --", file=out)
    for verdict, proto, port, note in sorted(findings, key=lambda f: (f[1], f[2])):
        tag = f"{proto}/{port}"
        print(f"     {verdict:<10} {tag:<12} {note}", file=out)
        if verdict == "VIOLATION":
            violations += 1
    print(f"  summary: {violations} violation(s), {len(findings)} public listener(s)", file=out)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
