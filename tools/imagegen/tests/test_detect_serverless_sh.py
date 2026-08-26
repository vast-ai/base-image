"""Tests for vast_boot.d/01-detect-serverless.sh — the runtime-mode decision (ADR 0034).

WHY THIS FILE EXISTS. This stage decides whether an image runs as a serverless worker,
and a wrong answer is expensive in a way that is hard to reverse: `exit_serverless.sh`
exits 0 and its units are `autorestart=unexpected` + `exitcodes=0`, so supervisord treats
the stop as intentional and never restarts caddy, the portal, jupyter, the tunnel
manager, syncthing, tensorboard, or any supervisor unit authored from a provisioning
manifest — for the life of the instance. There is no live gate for the DETECTION path
(the serverless QA cell declares `SERVERLESS=true` explicitly), so this is where the
decision table is pinned.

Needs no container: the verdict is a pure function of three environment variables. The
marker write targets /run and is asserted on a real instance by base/15-boot-markers.sh
instead — it degrades to a warning when unwritable, which is deliberate.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

STAGE = Path(__file__).resolve().parents[3] / "ROOT/etc/vast_boot.d/01-detect-serverless.sh"


def run(**env):
    """Source the stage the way boot_default.sh does and report what it decided.

    Sourced, not executed, and NOT inside a command substitution: the stage's whole job
    is to export into the boot shell and to set main()'s locals through dynamic scope,
    and a subshell would hide both. Getting that wrong is how the first draft of this
    harness reported a passing stage as doing nothing.
    """
    assigns = "".join(f"{k}={v!r}\n" for k, v in env.items() if v is not None)
    script = f"""
        unset SERVERLESS MASTER_TOKEN REPORT_ADDR VAST_SERVERLESS_DETECT
        {assigns}
        update_portal=true; update_vast_cli=true
        . {STAGE} >/dev/null 2>&1
        echo "SERVERLESS=${{SERVERLESS:-unset}}"
        echo "update_portal=${{update_portal}}"
        echo "update_vast_cli=${{update_vast_cli}}"
    """
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout
    return dict(l.split("=", 1) for l in out.strip().splitlines() if "=" in l)


def test_both_signals_infer_serverless():
    """The bridge's whole purpose: the autoscaler injects these, nothing injects
    SERVERLESS yet, and the image works it out."""
    r = run(MASTER_TOKEN="secret", REPORT_ADDR="https://autoscaler.example")
    assert r["SERVERLESS"] == "true"


def test_master_token_alone_does_NOT_activate():
    """Corroboration is required. A false positive darkens the instance permanently
    (exit_serverless.sh exits 0 under autorestart=unexpected, so supervisord never
    restarts those services), while a false negative just falls back to the template's
    own declaration — which 9 of 10 published autoscaler templates already carry. The
    asymmetry is the whole argument for requiring both."""
    r = run(MASTER_TOKEN="secret")
    assert r["SERVERLESS"] == "unset"


def test_report_addr_alone_does_NOT_activate():
    """Corroboration can raise an alarm; it must never enable the mode by itself. This
    shape is also the rename signature — autoscaler env on the box without the primary
    key — and the stage says so on stdout rather than silently doing nothing."""
    r = run(REPORT_ADDR="https://autoscaler.example")
    assert r["SERVERLESS"] == "unset"


def test_an_explicit_false_survives_the_inference():
    """THE case this design exists to protect. An inference from a proxy must not
    overrule a human who typed the value — and a false positive costs the operator every
    interactive service on the box, permanently, so the opt-out has to hold."""
    r = run(SERVERLESS="false", MASTER_TOKEN="secret", REPORT_ADDR="https://a")
    assert r["SERVERLESS"] == "false"


def test_an_explicit_true_is_left_alone():
    """9 of 10 published autoscaler templates already declare it. The stage must be a
    no-op there, which is also what makes it inert once the backend injects SERVERLESS."""
    r = run(SERVERLESS="true")
    assert r["SERVERLESS"] == "true"


def test_empty_is_not_a_declaration():
    """`-e SERVERLESS=` states nothing. Treating it as a declaration would let an empty
    template field silently disable detection on a real worker."""
    r = run(SERVERLESS="", MASTER_TOKEN="secret", REPORT_ADDR="https://a")
    assert r["SERVERLESS"] == "true"


def test_the_off_switch_beats_the_signal():
    """The rollback lever. Without it, backing out a wrong inference is a rebuild and
    re-promote of base plus every derivative."""
    r = run(MASTER_TOKEN="secret", REPORT_ADDR="https://a", VAST_SERVERLESS_DETECT="false")
    assert r["SERVERLESS"] == "unset"


def test_the_off_switch_accepts_off_as_well_as_false():
    r = run(MASTER_TOKEN="secret", REPORT_ADDR="https://a", VAST_SERVERLESS_DETECT="off")
    assert r["SERVERLESS"] == "unset"


def test_no_signal_means_no_inference():
    """An on-demand rental is the overwhelming majority of boots and must be untouched."""
    r = run()
    assert r["SERVERLESS"] == "unset"


@pytest.mark.parametrize("env,flags", [
    (dict(MASTER_TOKEN="secret", REPORT_ADDR="https://a"), "false"),
    (dict(SERVERLESS="true"), "false"),
    (dict(SERVERLESS="false", MASTER_TOKEN="secret", REPORT_ADDR="https://a"), "true"),
    (dict(), "true"),
])
def test_update_flags_follow_the_resolved_mode(env, flags):
    """The block moved out of boot_default.sh:71-75 must still fire, and it reaches
    main()'s locals only through dynamic scope. If anyone changes the boot loop from
    sourcing to execution this is the test that says so — along with three existing
    stages that would break the same way."""
    r = run(**env)
    assert r["update_portal"] == flags
    assert r["update_vast_cli"] == flags


def test_the_token_value_is_never_echoed():
    """MASTER_TOKEN is a credential. The stage may report its PRESENCE and must never
    report its value — stdout here lands in docker logs, which on a serverless worker is
    the only surface there is."""
    script = f"""
        unset SERVERLESS VAST_SERVERLESS_DETECT
        MASTER_TOKEN=SUPERSECRETVALUE
        REPORT_ADDR=https://a
        update_portal=true; update_vast_cli=true
        . {STAGE} 2>&1
    """
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert "SUPERSECRETVALUE" not in out.stdout + out.stderr
