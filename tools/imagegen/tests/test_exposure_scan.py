"""Tests for base/exposure_scan.py — the ADR 0028 exposure verdict.

WHY THIS FILE EXISTS. The scan it replaces was a heredoc inside a shell test, so its
only test was a rented GPU — and its central defect survived months of green runs
because nothing could exercise it off-box. Every case here is a /proc fixture: no
container, no root, no sockets.

The two properties worth pinning, because both are one careless edit from reverting:

  1. The verdict does NOT depend on attribution. A listener we cannot attribute is a
     VIOLATION if undeclared, not a WARN. That downgrade is the exact defect ADR 0028
     was written for — on base-qa, where EXPOSURE_ENFORCE is true, it meant the
     enforced gate passed on every service that drops privileges.
  2. An allowlist entry that resolves to nothing allowlists NOTHING, loudly. The
     allowlist is now the sole pass path, so a silent resolution failure is either a
     false red or, worse, a silent hole.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MOD = (Path(__file__).resolve().parents[3]
       / "ROOT/opt/instance-tools/tests/base/exposure_scan.py")


def _load():
    spec = importlib.util.spec_from_file_location("exposure_scan", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


es = _load()

HDR = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
       "retrnsmt   uid  timeout inode\n")


def proc_line(port, addr="00000000", state="0A", uid=0, inode="1001"):
    return (f"   0: {addr}:{port:04X} 00000000:0000 {state} 00000000:00000000 "
            f"00:00000000 00000000 {uid:>5}        0 {inode} 1 0000 100 0 0 10\n")


def rows(*specs):
    out = []
    for proto, port, kw in specs:
        parsed = es.parse_proc_net(HDR + proc_line(port, **kw), want_listen=(proto == "tcp"))
        for r in parsed:
            r["proto"] = proto
            out.append(r)
    return out


def entry(spec, proto="tcp", cls="raw", env=None):
    e = es.AllowEntry(spec, proto, cls, "", "test.conf:1")
    e.ports, e.ephemeral, e.unresolved = es.resolve_spec(spec, env or {})
    return e


def verdicts(findings):
    return {(f[1], f[2]): f[0] for f in findings}


# ---------------------------------------------------------------- address decoding


def test_wildcard_v4_is_public_and_loopback_is_not():
    assert es.is_public("00000000")
    assert not es.is_public("0100007F")          # 127.0.0.1, little-endian


def test_wildcard_v6_and_v4_mapped_wildcard_are_public():
    assert es.is_public("0" * 32)                                  # ::
    assert es.is_public("0" * 24 + "FFFF" + "00000000")            # ::ffff:0.0.0.0
    assert not es.is_public("0" * 24 + "01000000")                 # ::1


def test_only_listening_tcp_counts():
    """An ESTABLISHED socket on a public address is a client connection, not exposure.
    Counting it would red every instance that talks to the internet."""
    assert es.parse_proc_net(HDR + proc_line(8000, state="01"), want_listen=True) == []
    assert len(es.parse_proc_net(HDR + proc_line(8000, state="0A"), want_listen=True)) == 1


# ---------------------------------------------------------------- THE regression


def test_an_unattributable_listener_is_a_violation_not_a_warn():
    """THE defect ADR 0028 exists for. The old scan downgraded this to WARN, WARNs do
    not set the return code, and base-qa enforces — so the gate passed on every
    service running as a non-root uid, which is most of them."""
    r = rows(("tcp", 9999, {"uid": 1000, "inode": "77"}))
    f = es.classify(r, [], {}, {}, set(), challenge=lambda p: False)
    assert verdicts(f)[("tcp", 9999)] == "VIOLATION"
    assert "uid1000" in f[0][3] and "no visible process" in f[0][3]


def test_a_uid0_socket_with_no_visible_process_is_named_foreign():
    """Decision 3: a narrow, named category rather than a catch-all — and still a
    violation unless declared."""
    r = rows(("tcp", 6443, {"uid": 0, "inode": "88"}))
    f = es.classify(r, [], {}, {}, set(), challenge=lambda p: False)
    assert verdicts(f)[("tcp", 6443)] == "VIOLATION"
    assert "foreign" in f[0][3]


def test_attribution_improves_the_message_and_never_the_verdict():
    """Ownership feeds the message, not the decision. An undeclared port owned by a
    process we CAN see is just as much a violation as one we cannot."""
    r = rows(("tcp", 7000, {"uid": 0, "inode": "99"}))
    f = es.classify(r, [], {}, {"99": ("42", "myapp")}, set(), challenge=lambda p: False)
    assert verdicts(f)[("tcp", 7000)] == "VIOLATION"
    assert "myapp pid=42" in f[0][3]


def test_a_loopback_listener_is_not_reported_at_all():
    r = rows(("tcp", 18000, {"addr": "0100007F"}))
    assert es.classify(r, [], {}, {}, set(), challenge=lambda p: False) == []


# ---------------------------------------------------------------- caddy, behaviourally


def test_a_caddy_port_that_challenges_passes():
    r = rows(("tcp", 1111, {}))
    f = es.classify(r, [], {}, {}, {1111}, challenge=lambda p: True)
    assert verdicts(f)[("tcp", 1111)] == "ok"


def test_a_caddy_port_that_does_not_challenge_is_a_violation():
    """Decision 4: declaring a site address proves configuration, not enforcement.
    The pid check this replaces only ever proved caddy owned the socket."""
    r = rows(("tcp", 1111, {}))
    f = es.classify(r, [], {}, {}, {1111}, challenge=lambda p: False)
    assert verdicts(f)[("tcp", 1111)] == "VIOLATION"
    assert "did NOT challenge" in f[0][3]


def test_caddy_identity_is_never_consulted():
    """A caddy-owned socket on a port Caddy does not declare gets no pass. Identity
    was the old test; it would flip every front to violation the day caddy drops to
    an unprivileged uid."""
    r = rows(("tcp", 4321, {"inode": "55"}))
    f = es.classify(r, [], {}, {"55": ("7", "caddy")}, set(), challenge=lambda p: True)
    assert verdicts(f)[("tcp", 4321)] == "VIOLATION"


# ---------------------------------------------------------------- allowlist grammar


def test_literal_and_range_and_env_specs_resolve():
    assert entry("8080").ports == {8080}
    assert entry("range:9000-9003").ports == {9000, 9001, 9002, 9003}
    assert entry("env:PORT", env={"PORT": "5555"}).ports == {5555}


def test_env_with_a_literal_fallback():
    """coturn binds ${VAST_UDP_PORT_70000:-3478}, which `env:` alone structurally
    cannot describe — the fallback is the bind, not a guess."""
    assert entry("env:MISSING|3478", env={}).ports == {3478}
    assert entry("env:SET|3478", env={"SET": "70000"}).ports == {70000}


def test_envlist_takes_several_ports_from_one_variable():
    assert entry("envlist:AUTH_EXCLUDE", env={"AUTH_EXCLUDE": "8000, 8188 9000"}).ports \
        == {8000, 8188, 9000}


def test_an_unresolvable_entry_allowlists_nothing_and_says_so():
    """FAIL-CLOSED, and loud. The allowlist is the sole pass path now, so an entry
    that quietly resolved to nothing is either an inexplicable red later or a hole."""
    e = entry("env:NOPE", env={})
    assert e.ports == set() and "resolved to nothing" in e.unresolved


def test_a_malformed_range_is_rejected_rather_than_guessed():
    for bad in ("range:9000", "range:abc-def", "range:900-800"):
        e = entry(bad)
        assert e.ports == set() and e.unresolved, bad


def test_a_declared_port_passes_without_any_probe():
    r = rows(("tcp", 5900, {"uid": 1000, "inode": "12"}))
    f = es.classify(r, [entry("5900")], {}, {}, set(),
                    challenge=lambda p: pytest.fail("declared ports must not be probed"))
    assert verdicts(f)[("tcp", 5900)] == "ok"


def test_grammar_problems_are_collected_not_raised(tmp_path):
    """One typo must not take the whole scan down — that would turn a bad line into
    'the check decided nothing' for every image at once."""
    (tmp_path / "50-x.conf").write_text(
        "9000/tcp raw fine\n"
        "nonsense\n"
        "8000/sctp raw bad proto\n"
        "7000/tcp wat unknown class\n")
    entries, problems = es.load_allowlist(str(tmp_path), {})
    assert len(entries) == 1 and entries[0].ports == {9000}
    assert len(problems) == 3


# ---------------------------------------------------------------- ephemeral UDP


def test_ephemeral_udp_outside_the_port_map_is_transient():
    r = rows(("udp", 40796, {"uid": 108, "inode": "31"}))
    f = es.classify(r, [], {}, {}, set(), challenge=lambda p: False)
    assert verdicts(f)[("udp", 40796)] == "transient"


def test_a_published_port_inside_the_ephemeral_range_is_still_judged():
    """Both predicates are required. A genuine fixed service can sit inside the
    ephemeral range, so the range test alone would suppress a real finding."""
    r = rows(("udp", 40796, {"uid": 0, "inode": "32"}))
    f = es.classify(r, [], {"VAST_UDP_PORT_40796": "40796"}, {}, set(),
                    challenge=lambda p: False)
    assert verdicts(f)[("udp", 40796)] == "VIOLATION"


# ---------------------------------------------------------------- self-defence


def test_a_listener_ss_sees_and_proc_missed_stops_the_scan():
    """A /proc parsing bug fails silently OPEN, the one direction a fail-closed check
    must never fail. The cross-check uses ss for the listener SET only — no -p, no -e
    — so it does not reintroduce the attribution dependency ADR 0028 removes."""
    r = rows(("tcp", 8000, {}))
    ss = ("Netid State Recv-Q Send-Q Local:Port Peer\n"
          "tcp   LISTEN 0 128 0.0.0.0:8000 0.0.0.0:*\n"
          "tcp   LISTEN 0 128 0.0.0.0:9999 0.0.0.0:*\n")
    assert "9999" in es.cross_check(r, ss)
    assert es.cross_check(r, ss.replace("0.0.0.0:9999 0.0.0.0:*\n", "")) == ""


def test_the_caddy_arm_stays_silent_when_caddy_is_unreadable(monkeypatch):
    """Decision 4 hardens Caddy's pass precisely so caddy can drop privileges one day.
    If this arm tripped on an unreadable caddy, that hardening would turn every scan
    into 'decided nothing' — a hard failure regardless of EXPOSURE_ENFORCE."""
    class R:
        stdout = "999999\n"
    monkeypatch.setattr(es.os, "listdir", lambda p: (_ for _ in ()).throw(OSError()))
    assert es.caddy_socket_check(rows(("tcp", 8000, {})), {}, runner=lambda *a, **k: R()) == ""


def test_no_caddy_running_is_not_a_failure():
    class R:
        stdout = ""
    assert es.caddy_socket_check([], {}, runner=lambda *a, **k: R()) == ""


# ---------------------------------------------------------------- `internal`


def test_an_internal_port_that_the_template_publishes_is_a_violation():
    """THE hole this class exists to close, and it was live for one commit.

    Declaring Ray's 121-port range made every one of those ports pass the scan
    unconditionally — including if a template mapped one. Ray's GCS has no auth of
    its own (that is why it was allowed to bind wide at all), so publishing 6379
    would put an unauthenticated cluster control plane on the internet and the scan
    would have printed `ok allowlisted (raw; 50-vllm.conf:22)`."""
    r = rows(("tcp", 6379, {"inode": "5"}))
    e = entry("range:6379-6499", cls="internal")
    f = es.classify(r, [e], {"VAST_TCP_PORT_6379": "40001"}, {}, set(),
                    challenge=lambda p: False)
    assert verdicts(f)[("tcp", 6379)] == "VIOLATION"
    assert "PUBLISHES" in f[0][3]


def test_an_internal_port_that_is_not_published_passes():
    r = rows(("tcp", 6379, {"inode": "5"}))
    e = entry("range:6379-6499", cls="internal")
    f = es.classify(r, [e], {}, {}, set(), challenge=lambda p: False)
    assert verdicts(f)[("tcp", 6379)] == "ok"


def test_the_publication_check_reads_both_halves_of_the_port_map():
    """Vast names the variable after the INTERNAL port and stores the external one,
    so either half proves publication. Reading only one would miss it."""
    e = entry("range:6379-6499", cls="internal")
    by_key = es.classify(rows(("tcp", 6390, {})), [e], {"VAST_TCP_PORT_6390": "1"}, {}, set(),
                         challenge=lambda p: False)
    by_val = es.classify(rows(("tcp", 6391, {})), [e], {"VAST_TCP_PORT_9": "6391"}, {}, set(),
                         challenge=lambda p: False)
    assert verdicts(by_key)[("tcp", 6390)] == "VIOLATION"
    assert verdicts(by_val)[("tcp", 6391)] == "VIOLATION"


def test_a_published_raw_port_is_still_fine():
    """sshd and syncthing are allowlisted AND published, and that is correct — they
    authenticate. `internal` is opt-in precisely so this stays true."""
    r = rows(("tcp", 22, {}))
    f = es.classify(r, [entry("22", cls="raw")], {"VAST_TCP_PORT_22": "40022"}, {}, set(),
                    challenge=lambda p: False)
    assert verdicts(f)[("tcp", 22)] == "ok"


def test_internal_is_a_recognised_class():
    assert es.INTERNAL_CLASS in es.VALID_CLASSES
