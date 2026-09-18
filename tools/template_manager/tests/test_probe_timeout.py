"""The probe deadline is sized by the work the TEMPLATE declares, not by a default
that assumes the maximum.

THE defect, 2026-09-18: a vLLM serverless QA cell sat for 4200s against an instance
whose test server never answered. That number is `PROV_TIMEOUT (3600) + TIMEOUT_HEADROOM
(600)`, and neither QA template declares `PROVISIONING_SCRIPT` or `PROV_TIMEOUT` — so
3600 was the default for a provisioning phase that does not exist on this cell. The wait
was inherited, not earned.

What it actually costs: the deadline is per ATTEMPT, so a cell that draws bad hosts
burns over an hour each before it can try another, on a rented GPU, while the run looks
alive. The failure it is waiting for is already decided.

The measurement that sizes the replacement, from the healthy cell in the same run
(35347701559): `Instance 51435903 running` at 13:15:43.67, `Streaming results from
.../test-status` at 13:15:44.03 — the server answered **0.36s** after the instance
reported running, because the boot sequence binds it before the client ever polls.
Nothing here needs minutes; PROBE_TIMEOUT_NO_PROVISIONING is 300s of pure headroom.

NOT a linter rule: this is tooling arithmetic rather than a property of an image or a
workflow, which is what imagegen lints. It is codified the way the promotion-headline
invariant is (test_promote_notification_truth.py) — an executed test that fails if the
sizing regresses.
"""
import test_template as tt


def _timeouts(prov=None):
    """The instance-timeout map main() builds from the template env."""
    return {"PROV_TIMEOUT": prov} if prov is not None else {}


def test_a_template_without_provisioning_waits_minutes_not_an_hour():
    """The QA templates declare no PROVISIONING_SCRIPT: the test server binds during
    boot, so the only thing being waited for is the boot sequence."""
    got = tt.compute_probe_timeout(_timeouts(), declares_provisioning=False)
    assert got <= 600, f"{got}s is not 'a few minutes' for a template that never provisions"
    assert got == tt.PROBE_TIMEOUT_NO_PROVISIONING


def test_a_provisioning_template_still_gets_its_full_window():
    """Provisioning runs BEFORE the test server binds, so where a template really does
    provision, the wait is earned and must not be cut."""
    got = tt.compute_probe_timeout(_timeouts(prov=3600), declares_provisioning=True)
    assert got == 3600 + tt.TIMEOUT_HEADROOM


def test_a_declared_provisioning_timeout_is_respected():
    """A template that declares a longer PROV_TIMEOUT has said what it needs."""
    got = tt.compute_probe_timeout(_timeouts(prov=7200), declares_provisioning=True)
    assert got == 7200 + tt.TIMEOUT_HEADROOM


def test_the_no_provisioning_window_is_not_used_when_provisioning_is_declared():
    """The two paths must not collapse into one: that is how the 4200s got here."""
    with_prov = tt.compute_probe_timeout(_timeouts(prov=3600), declares_provisioning=True)
    without = tt.compute_probe_timeout(_timeouts(prov=3600), declares_provisioning=False)
    assert with_prov > without


def test_the_unreachable_window_stays_short():
    """The other tier, unchanged: nothing but connection timeouts means the host is
    black-holing packets and no amount of waiting fixes it."""
    assert tt.NETWORK_PROBE_TIMEOUT <= 120
