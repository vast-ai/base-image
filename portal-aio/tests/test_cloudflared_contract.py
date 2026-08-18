"""Contract gate for the cloudflared binary the portal drives.

WHY A CONTRACT, NOT A PINNED VERSION.

`Dockerfile` fetches `releases/latest`, so the cloudflared that ships is whatever
Cloudflare published that morning (observed: 2026.8.2, built the previous day).
Pinning would make that deterministic and would also make it a knob someone has
to turn on every base rebuild and every portal release — a version that only ever
gets bumped under time pressure is a version nobody validates. The alternative is
to stop caring which version ships and start asserting that whatever ships still
answers the way the portal drives it. Same reasoning as the provisioner's `hf`
CLI contract test, and the same reason its requirements are bounded rather than
pinned exact.

WHAT THE PORTAL ACTUALLY DEPENDS ON — three things, all of which a release could
break independently:

  1. quick tunnel argv   `--no-autoupdate --no-tls-verify --url <target>`
  2. daemon argv         `--no-autoupdate tunnel --metrics <hp> run --token <t>`
  3. startup output      a line matching QUICK_TUNNEL_URL_RE

(3) is the one that has no compile-time signal at all: cloudflared could rename a
flag and we would see it immediately, but a change to the announcement line would
leave `start()` blocking until its 30s timeout, per tunnel, on every instance —
which is precisely the shape of the customer-escalation symptom (visible, loud,
and easy to mistake for the cause of something else).

BLOCK vs INCONCLUSIVE. A usage error is a contract break and fails the build. Not
being able to reach Cloudflare is not: a transport failure carries none of the
usage markers, and turning a Cloudflare outage into a red base build is how a gate
gets routed around. The live half therefore skips loudly rather than failing when
the network is the problem — but a tunnel that DOES announce itself in a format
the portal cannot parse is a hard failure.

SKIP IS NOT PASS. The consequence of the paragraph above is that this file's
NORMAL degraded outcome — rate-limited, offline — is `pytest` exiting 0 with the
live assertions skipped, which is indistinguishable from success to anything
reading an exit code. That is how a gate becomes decoration for the second time.
So the live tests carry `@pytest.mark.live`, and the CI job classifies the run
into verified / unverified / broken from the junit report rather than from the
exit code. `-m "not live"` is also how a static PR run deselects them, so the
per-PR suite stops spending real tunnels on a binary that is not what ships.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tunnel_manager"))
import tunnel_manager as tm  # noqa: E402

# Usage markers. A transport/auth failure prints none of these.
_USAGE = re.compile(
    r"unknown flag|flag provided but not defined|incorrect usage|"
    r"unknown command|no such flag", re.I)

_LATEST = ("https://github.com/cloudflare/cloudflared/releases/latest/"
           "download/cloudflared-linux-amd64")

# THREE STATES, AND THE DEFAULT IS BLOCK.
#
# The first version of this classifier folded `failed to request quick Tunnel`
# into the rate-limit pattern. That string wraps several distinct causes — 429,
# 5xx, DNS, TLS *and a regression in cloudflared's own tunnel client* — so the
# one failure this gate exists to catch, a release that can no longer create
# tunnels, was being classified as "rate-limited" and skipped.
#
# It is NOT the universal wrapper for that path; an earlier version of this
# comment said it was, on reasoning rather than measurement. The rate-limit path
# actually emits a different wrapper entirely:
#
#   ERR Error unmarshaling QuickTunnel response: error code: 1015
#   failed to unmarshal quick Tunnel: invalid character 'e' looking for ...
#
# The discriminator is therefore the CAUSE, never the wrapper. Rate limiting and
# transport failure are named explicitly and are inconclusive; anything else that
# fails to produce a tunnel is a contract break and blocks.
_RATE_LIMITED = re.compile(
    r"\b429\b|too many requests|rate.?limit|quota exceeded|error code: 1015", re.I)

_TRANSPORT = re.compile(
    r"dial tcp|i/o timeout|no such host|connection refused|network is unreachable|"
    r"tls handshake|context deadline exceeded|temporary failure in name resolution", re.I)

# POSITIVE EVIDENCE, checked BEFORE any of the negative patterns above.
#
# cloudflared prints this the moment the API issues a tunnel, and it is a
# DIFFERENT string from the announcement format under test — so it can answer
# "did we get a tunnel?" without begging the question the gate asks. Real log
# from a run on a host where UDP/QUIC is blocked:
#
#   INF Requesting new quick Tunnel on trycloudflare.com...
#   INF |  Your quick Tunnel has been created! Visit it at ...
#   INF |  https://blog-devoted-pos-angela.trycloudflare.com
#   ...
#   ERR Failed to dial a quic connection error="failed to dial to edge with
#       quic: timeout: handshake did not complete in time"
#
# A working tunnel, with error lines after it. Ordering the negative patterns
# first — as this file originally did — lets late transport noise overrule a
# tunnel that was demonstrably created, turning both a success AND a genuine
# regression in QUICK_TUNNEL_URL_RE into a skip. Measured honestly: none of the
# _TRANSPORT patterns match the QUIC-fallback wording above, so this was a
# latent ordering fault rather than a live misfire — but `i/o timeout` and
# `context deadline exceeded` are ordinary Go network errors and the runner's
# network is not this host's.
_TUNNEL_ISSUED = re.compile(r"quick Tunnel has been created", re.I)


@pytest.fixture(autouse=True)
def _record_live_marker(request, record_property):
    """Carry the `live` marker into the junit report.

    `classify_contract_run.py` decides verified/unverified/broken from that
    report, and it must not do so from a restated list of test names: a rename
    would make a live test look offline, and the run would report `verified`
    having verified nothing. The marker is the single source of truth; this is
    the one line that gets it across the junit boundary.
    """
    if request.node.get_closest_marker("live"):
        record_property("live", "1")


@pytest.fixture(scope="module")
def cloudflared(tmp_path_factory) -> str:
    """The binary that would ship: the one in the image if present, else the
    same `releases/latest` the Dockerfile fetches."""
    # CLOUDFLARED_BIN env override: the build-time job extracts the binary from
    # the image it just built and points here, so the gate tests what shipped
    # rather than whatever releases/latest happens to be minutes later.
    override = os.environ.get("CLOUDFLARED_BIN")
    if override:
        assert os.access(override, os.X_OK), f"CLOUDFLARED_BIN={override} is not executable"
        return override
    inimage = tm.CLOUDFLARED_BIN
    if os.path.exists(inimage) and os.access(inimage, os.X_OK):
        return inimage
    dst = tmp_path_factory.mktemp("cf") / "cloudflared"
    try:
        urllib.request.urlretrieve(_LATEST, dst)
    except Exception as e:                       # network, not contract
        pytest.skip(f"could not fetch cloudflared ({type(e).__name__}: {e})")
    dst.chmod(0o755)
    return str(dst)


def _argv_for(coro_owner, *, daemon: bool) -> list:
    """Ask the SHIPPED module what argv it builds, rather than restating it here.

    Restating it is how a test ends up asserting its own copy: the portal could
    drop --no-autoupdate and a hardcoded expectation would keep passing.
    """
    import asyncio
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        raise RuntimeError("captured")

    orig = asyncio.create_subprocess_exec
    asyncio.create_subprocess_exec = fake_exec
    try:
        if daemon:
            obj = tm.CloudflareDaemon("dummy-token")
        else:
            obj = tm.QuickTunnel("http://127.0.0.1:18999")
        try:
            asyncio.get_event_loop().run_until_complete(obj.start())
        except Exception:
            pass
    finally:
        asyncio.create_subprocess_exec = orig
    return captured.get("argv", [])


def test_the_portal_passes_no_autoupdate_on_the_quick_tunnel():
    """cloudflared otherwise replaces its own binary inside a running customer
    instance and restarts to do it."""
    argv = _argv_for(tm.QuickTunnel, daemon=False)
    assert argv, "could not capture the quick-tunnel argv"
    assert "--no-autoupdate" in argv, f"quick tunnel argv lacks --no-autoupdate: {argv}"


def test_the_portal_passes_no_autoupdate_on_the_daemon():
    argv = _argv_for(tm.CloudflareDaemon, daemon=True)
    assert argv, "could not capture the daemon argv"
    assert "--no-autoupdate" in argv, f"daemon argv lacks --no-autoupdate: {argv}"


@pytest.mark.live
@pytest.mark.parametrize("daemon", [False, True], ids=["quick-tunnel", "daemon"])
def test_the_shipped_binary_accepts_the_argv_the_portal_builds(cloudflared, daemon):
    """Offline half — this one BLOCKS. Runs the portal's own argv against the
    real binary and fails only if the arguments are rejected AS ARGUMENTS."""
    argv = _argv_for(None, daemon=daemon)
    assert argv, "could not capture argv"
    argv = [cloudflared] + argv[1:]
    if daemon:                        # a bogus token must fail on AUTH, not usage
        argv = [a if a != "dummy-token" else "not-a-real-token" for a in argv]
    # A usage rejection is IMMEDIATE. A successful quick tunnel runs forever, so
    # a timeout here is evidence the arguments were accepted, not a failure —
    # reading a long-running success as a red is how this test would block a
    # perfectly good build.
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=25)
        out = (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode(errors="replace")
               + (e.stderr or b"").decode(errors="replace"))
    if not _USAGE.search(out) and (_RATE_LIMITED.search(out) or _TRANSPORT.search(out)):
        pytest.skip("rate-limited or unreachable; the arguments themselves were not rejected")
    m = _USAGE.search(out)
    assert not m, (
        f"the shipped cloudflared rejected the portal's arguments "
        f"({m.group(0)!r}) — the flag contract moved:\n{out[-1500:]}")


@pytest.mark.live
def test_a_live_quick_tunnel_still_announces_a_url_the_portal_can_parse(cloudflared, tmp_path):
    """Live half. A tunnel that announces itself in a format the portal cannot
    parse blocks `start()` until its 30s timeout, per tunnel, on every instance —
    with no other signal. Not reaching Cloudflare is a skip, not a failure."""
    srv = subprocess.Popen([sys.executable, "-m", "http.server", "18999",
                            "--bind", "127.0.0.1", "--directory", str(tmp_path)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        r = subprocess.run([cloudflared, "--no-autoupdate", "--no-tls-verify",
                            "--url", "http://127.0.0.1:18999"],
                           capture_output=True, text=True, timeout=90)
        out = (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode(errors="replace")
               + (e.stderr or b"").decode(errors="replace"))
    finally:
        srv.terminate()

    usage = _USAGE.search(out)
    assert not usage, f"cloudflared rejected the portal's arguments ({usage.group(0)!r})"

    # POSITIVE EVIDENCE FIRST. If Cloudflare issued a tunnel then the contract
    # question is live and gets answered, whatever else the log contains — a
    # tunnel is created before its edge connections are dialled, so transport
    # noise AFTER the announcement says nothing about whether we can parse it.
    # Checking the negative patterns first is what let this gate skip on the
    # very regression it exists to catch.
    if not _TUNNEL_ISSUED.search(out):
        rl = _RATE_LIMITED.search(out)
        if rl:
            pytest.skip(f"Cloudflare rate-limited quick-tunnel creation ({rl.group(0)!r}) "
                        "— it declined to issue a tunnel, which is not a contract break")
        tr = _TRANSPORT.search(out)
        if tr:
            pytest.skip(f"could not reach Cloudflare ({tr.group(0)!r}) — transport, not contract")

    # Deliberately NOT `if "trycloudflare.com" not in out: skip`. That keyed the
    # "did we get there" guard on the very literal the contract is about, so a
    # release that moved to a different domain would have skipped instead of
    # failing. Past this point cloudflared either announced a tunnel, or failed
    # to produce one for a reason we do not recognise. Both owe us a parseable
    # announcement, and neither is a reason to stay quiet.
    assert tm.QUICK_TUNNEL_URL_RE.search(out), (
        "cloudflared announced a tunnel but QUICK_TUNNEL_URL_RE did not match it. "
        "The portal would block for 30s per tunnel and start none.\n"
        + "\n".join(l for l in out.splitlines() if "trycloudflare.com" in l)[:800])


@pytest.mark.live
def test_no_autoupdate_is_HONOURED_not_merely_accepted(cloudflared):
    """Accepting a flag and acting on it are different things.

    The argv test above only proves cloudflared did not *reject* --no-autoupdate.
    A release that silently ignored it would pass that and still replace its own
    binary inside a customer instance. cloudflared echoes its resolved settings on
    startup, and the key is absent entirely when the flag is not passed:

        with:    Settings: map[ha-connections:1 no-autoupdate:true no-tls-verify:true ...]
        without: Settings: map[ha-connections:1 no-tls-verify:true ...]

    so the presence of `no-autoupdate:true` in that map is the flag being applied,
    not merely parsed.
    """
    try:
        r = subprocess.run([cloudflared, "--no-autoupdate", "--no-tls-verify",
                            "--url", "http://127.0.0.1:9"],
                           capture_output=True, text=True, timeout=25)
        out = (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode(errors="replace")
               + (e.stderr or b"").decode(errors="replace"))

    m = re.search(r"Settings: map\[([^\]]*)\]", out)
    if not m:
        pytest.skip("this build does not echo a Settings map; cannot prove the flag "
                    f"was applied:\n{out[-500:]}")
    assert "no-autoupdate:true" in m.group(1), (
        "cloudflared accepted --no-autoupdate but did not apply it — it would "
        f"still self-update inside a running instance. Settings: {m.group(1)}")
