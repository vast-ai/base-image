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
which is precisely the shape of the CS-4551 symptom (visible, loud, and easy to
mistake for the cause of something else).

BLOCK vs INCONCLUSIVE. A usage error is a contract break and fails the build. Not
being able to reach Cloudflare is not: a transport failure carries none of the
usage markers, and turning a Cloudflare outage into a red base build is how a gate
gets routed around. The live half therefore skips loudly rather than failing when
the network is the problem — but a tunnel that DOES announce itself in a format
the portal cannot parse is a hard failure.
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

# Cloudflare RATE LIMITS quick-tunnel creation — tunnel_manager spaces its own
# startup tunnels 2s apart for exactly this reason. A 429 means Cloudflare
# declined to issue a tunnel, which says nothing about whether this binary still
# speaks the portal's contract. Treating it as a break would make the gate fail
# in bursts (a base rebuild is 12 configs) and, worse, fail more often the more
# we run it — the shape that gets a gate switched off.
_RATE_LIMITED = re.compile(
    r"\b429\b|too many requests|rate.?limit|quota exceeded|"
    r"failed to request quick tunnel", re.I)


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
    if _RATE_LIMITED.search(out) and not _USAGE.search(out):
        pytest.skip("Cloudflare rate-limited this attempt; the arguments were not rejected")
    m = _USAGE.search(out)
    assert not m, (
        f"the shipped cloudflared rejected the portal's arguments "
        f"({m.group(0)!r}) — the flag contract moved:\n{out[-1500:]}")


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

    rl = _RATE_LIMITED.search(out)
    if rl:
        pytest.skip(f"Cloudflare rate-limited quick-tunnel creation ({rl.group(0)!r}) "
                    "— declined to issue a tunnel, which is not a contract break")
    if "trycloudflare.com" not in out:
        pytest.skip("cloudflared never reached trycloudflare.com — transport, "
                    f"not contract:\n{out[-600:]}")

    assert tm.QUICK_TUNNEL_URL_RE.search(out), (
        "cloudflared announced a tunnel but QUICK_TUNNEL_URL_RE did not match it. "
        "The portal would block for 30s per tunnel and start none.\n"
        + "\n".join(l for l in out.splitlines() if "trycloudflare.com" in l)[:800])
