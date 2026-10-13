"""Functional gate for ROOT/etc/vast_boot.d/55-tls-cert-gen.sh.

The predicate this script leans on is unit-tested in test_cert_usable.py. What
is tested HERE is the thing a predicate test cannot reach: the boot script's
behaviour ACROSS BOOTS.

Every defect this file has had was a multi-boot state machine, not a bad line:

  * a cert obtained once was kept for the life of the instance, however bad it
    was, because /etc persists across stop/start and nothing re-read it;
  * a console blip at first boot downgraded that instance to self-signed
    permanently;
  * the retry that fixed THAT re-generated an RSA keypair on every boot forever
    on a host with no egress, so every client had to re-accept a new
    certificate at every restart;
  * and validating the signed certificate by PARSE alone let a certificate for
    somebody else's key be installed, which the guard then rejected on the next
    boot — regenerating forever while printing "signed by the Vast console";
  * and a console certificate kept its address for a year after the machine's
    changed (ADR 0049), which only a later boot with a different address shows.

None of those is visible in a single execution. The harness runs the real script
repeatedly against a persistent /etc with a shimmed curl, so "does this
converge?" is an assertion rather than an argument.

A container, for the same reason test_cuda_driver_version.py uses one: the paths
are absolute and the state is the point. The alternative — a $VAST_CERT_DIR knob
— would put a test seam in the customer's TLS path to save a docker run.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HARNESS = Path(__file__).resolve().parent / "harness/tls-cert-gen-harness.sh"

BOOT = REPO / "ROOT/etc/vast_boot.d/55-tls-cert-gen.sh"
HELPER = REPO / "ROOT/opt/instance-tools/bin/cert-usable"

IMAGE = "ubuntu:24.04"
# ubuntu:24.04 ships no openssl. Failure of the install is made distinguishable
# from a real regression: swallowed, an archive blip arrives as a bare
# AssertionError and looks like the change under test broke TLS.
SETUP = (
    "{ apt-get update -qq && apt-get install -y -qq openssl ; } "
    "|| { echo 'HARNESS SETUP FAILED'; exit 99; }; "
    "bash /harness.sh"
)


# Docker-availability gate is shared across the three functional modules (a raw
# `raise RuntimeError` under CI aborted the WHOLE pytest session at collection).
from _docker_gate import assert_docker_present_under_ci, requires_docker  # noqa: E402


def test_docker_is_available_under_ci():
    """A SKIP IS NOT A PASS. One named red — not a session-wide collection abort —
    when CI is set and docker is missing; a clean skip otherwise."""
    assert_docker_present_under_ci()


@requires_docker
def test_cert_generation_converges_across_boots():
    assert HARNESS.is_file(), HARNESS
    args = ["docker", "run", "--rm", "-v", f"{HARNESS}:/harness.sh:ro"]
    # The helper goes to a staging path; the harness copies it into place so a
    # scenario can remove it. A bind mount at the real path could not be hidden.
    for src, dest in ((BOOT, "/etc/vast_boot.d/55-tls-cert-gen.sh"),
                      (HELPER, "/src-cert-usable")):
        assert src.is_file(), src
        args += ["-v", f"{src}:{dest}:ro"]

    for _ in (1, 2):                     # one retry: setup reaches the archive
        proc = subprocess.run(args + [IMAGE, "bash", "-c", SETUP],
                              capture_output=True, text=True, timeout=600)
        output = proc.stdout + proc.stderr
        setup_failed = proc.returncode == 99 or "HARNESS SETUP FAILED" in output
        if not setup_failed:
            break
    if setup_failed:
        pytest.fail(f"container setup failed twice (not a code regression):\n{output}")

    assert proc.returncode == 0, output
    assert "ALL SCENARIOS OK" in output, output


def test_the_qa_warning_loads_the_boot_scripts_address_predicate():
    """base/27-caddy-tls.sh loads `_cert_is_global_ipv4` alone from the shipped stage
    rather than keeping its own copy, so the two cannot disagree on which addresses
    count. If a rename or reformat broke that load, the warning would go quiet."""
    test27 = (REPO / "ROOT/opt/instance-tools/tests/base/27-caddy-tls.sh").read_text()
    load = "source <(sed -n '/^_cert_is_global_ipv4() {$/,/^}$/p' /etc/vast_boot.d/55-tls-cert-gen.sh)"
    assert load in test27
    probe = load.replace("/etc/vast_boot.d/55-tls-cert-gen.sh", str(BOOT)) + "\n" + (
        'for ip in 8.8.8.8 203.0.113.5 224.1.1.1 10.0.0.1; do '
        '_cert_is_global_ipv4 "$ip" && echo "$ip y" || echo "$ip n"; done')
    out = subprocess.run(["bash", "-c", probe], capture_output=True, text=True).stdout.split("\n")
    assert out[:4] == ["8.8.8.8 y", "203.0.113.5 n", "224.1.1.1 n", "10.0.0.1 n"], out
