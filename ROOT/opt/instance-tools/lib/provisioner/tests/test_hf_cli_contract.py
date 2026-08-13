"""The `hf` CLI is a third party we invoke by argv. Pin that contract.

`downloaders/huggingface.py` shells out to `hf download`. Every other test in
this directory patches `run_cmd`, so the command list is asserted nowhere and
reaches the real CLI nowhere — which is exactly how huggingface_hub 1.18.0
broke every single-file download in the field: it made `--local-dir` together
with `--cache-dir` a hard error, our argv passed both, and the mocked tests
stayed green through the whole incident.

Two layers here, and they fail for different reasons:

* the argv the module builds is asserted directly (no network, no CLI needed),
  so re-adding a removed flag fails at PR time;
* the argv is then handed to the REAL `hf` binary, and we fail only if it is
  rejected as *arguments*.

The second is host-independent by construction, which is what makes it safe to
run anywhere. Measured, with the endpoint pointed at a closed port:

    real argv                        rc=1, no usage text
    --local-dir + --cache-dir        rc=1, "Cannot use both ..."
    --nonexistent-flag               rc=2, "Usage:" / "No such option"

No egress produces a transport error, which carries no usage text, which passes.
Note the return code alone cannot separate the first two — the stderr markers
are doing the work, so do not "simplify" this to a returncode check.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import pytest

from provisioner.downloaders import huggingface as hf_mod
from provisioner.schema import RetrySettings

# Anything here means the CLI rejected our ARGUMENTS. A 404, an auth failure or
# a dead endpoint produces none of them.
USAGE_MARKERS = (
    "no such option",
    "unrecognized arguments",
    "cannot use both",
    "got unexpected extra argument",
    "usage:",
    "invalid value for",
)

# A loopback HTTP server stands in for the Hub: every request 404s, which is a
# transport-level failure carrying no usage text — deterministically "this host
# cannot reach HuggingFace", without needing the network to actually be down.
#
# Pointing at a CLOSED port works too and costs 24 SECONDS per invocation,
# because the client retries a connection refusal with backoff. A server that
# answers 404 fails in 0.65s. Measured: 23962ms vs 654ms, identical
# discrimination.
#
# Also measured and rejected: HF_HUB_OFFLINE=1 is instant but fails BEFORE the
# CLI validates --local-dir/--cache-dir, so the bad combination produces no usage
# marker at all and the detector goes blind to the defect it exists for.
# HF_HUB_ETAG_TIMEOUT / HF_HUB_DOWNLOAD_TIMEOUT do not govern this path.
_SRV_PORT = 18974


@pytest.fixture(scope="module")
def no_egress_env():
    srv = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(_SRV_PORT), "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{_SRV_PORT}/", timeout=0.2)
            break
        except urllib.error.HTTPError:
            break                      # answering at all is what we need
        except Exception:
            time.sleep(0.1)
    try:
        yield {
            **os.environ,
            "HF_ENDPOINT": f"http://127.0.0.1:{_SRV_PORT}",
            "HF_HUB_OFFLINE": "0",
        }
    finally:
        srv.terminate()
        srv.wait(timeout=10)


def _captured_argv(monkeypatch, fn, *args, **kwargs):
    """Run a downloader with run_cmd stubbed, returning the argv it built."""
    seen: list[list[str]] = []

    def fake_run_cmd(cmd, **_):
        seen.append(list(cmd))
        raise subprocess.CalledProcessError(1, cmd)   # stop after the first attempt

    monkeypatch.setattr(hf_mod, "run_cmd", fake_run_cmd)
    try:
        fn(*args, **kwargs)
    except Exception:
        pass
    return seen


RETRY_ONCE = RetrySettings(max_attempts=1, initial_delay=0, backoff_multiplier=1)


def test_single_file_argv_does_not_pass_both_local_dir_and_cache_dir(monkeypatch):
    """THE regression. 1.18.0 made this combination a hard error."""
    with tempfile.TemporaryDirectory() as d:
        argv = _captured_argv(
            monkeypatch, hf_mod._download_file,
            "org/repo", "main", "f.bin", os.path.join(d, "f.bin"), RETRY_ONCE,
        )
    assert argv, "no hf command was built"
    for cmd in argv:
        assert not ("--local-dir" in cmd and "--cache-dir" in cmd), \
            f"argv passes both --local-dir and --cache-dir: {cmd}"


def test_single_file_argv_shape(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        argv = _captured_argv(
            monkeypatch, hf_mod._download_file,
            "org/repo", "abc123", "sub/f.bin", os.path.join(d, "f.bin"), RETRY_ONCE,
        )
    cmd = argv[0]
    assert cmd[:2] == ["hf", "download"]
    assert "org/repo" in cmd and "sub/f.bin" in cmd
    assert "--revision" in cmd and cmd[cmd.index("--revision") + 1] == "abc123"


def test_repo_argv_shape(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        argv = _captured_argv(monkeypatch, hf_mod._download_repo, "org/repo", d, RETRY_ONCE)
    cmd = argv[0]
    assert cmd[:3] == ["hf", "download", "org/repo"]
    assert "--local-dir" in cmd


# ---------------------------------------------------------------------------
# Against the real CLI. Skips if `hf` is absent so the suite still runs in a
# bare checkout; CI installs it, and the image ships it in the provisioner venv.

requires_hf = pytest.mark.skipif(
    shutil.which("hf") is None,
    reason="hf CLI not installed — the argv assertions above still ran",
)


def _usage_errors(cmd: list[str], env: dict) -> list[str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
    blob = (p.stdout + p.stderr).lower()
    return [m for m in USAGE_MARKERS if m in blob]


@requires_hf
def test_the_real_cli_accepts_the_argv_we_build(monkeypatch, no_egress_env):
    """The contract itself: our arguments must not be rejected as arguments.

    This is also the false-positive check, which is why there is no separate one:
    it runs against a dead endpoint and a repo that does not exist, so it is
    exactly the "host has no egress" case. It passing IS the evidence that a
    transport failure is not misread as a contract break.
    """
    with tempfile.TemporaryDirectory() as d:
        argv = _captured_argv(
            monkeypatch, hf_mod._download_file,
            "vast-nonexistent-org/vast-nonexistent-repo", "main", "f.bin",
            os.path.join(d, "f.bin"), RETRY_ONCE,
        )
    hits = _usage_errors(argv[0], no_egress_env)
    assert not hits, (
        f"the hf CLI rejected our arguments ({hits}) — the flag contract moved "
        f"under us. argv was: {argv[0]}"
    )


@requires_hf
def test_the_detector_itself_fires_on_a_known_bad_flag(no_egress_env):
    """Guard the guard. If this stops firing, the test above passes vacuously
    and the contract is unpinned again without anyone noticing."""
    hits = _usage_errors(["hf", "download", "org/repo", "f.bin", "--vast-not-a-flag"], no_egress_env)
    assert hits, "usage-error detection is broken — a bogus flag produced no marker"
