"""A redraw must not land on the machine that just failed.

Observed live on 2026-08-19 (run 32236327488): a QA cell failed, redrew, and got
the SAME host — the one outcome the redraw exists to avoid.

The cause is a lifetime mismatch that reads as correct. `launch_with_retry` keeps
a `machine_blacklist`, but it is a local set inside ONE process, and the gate's
redraw spawns a fresh `test_template.py` per attempt. So the blacklist is empty
every time. Worse than a coin flip: offers are searched cheapest-first, and the
cheapest offer has not changed between attempts, so the same box is the LIKELY
pick rather than a possible one.
"""
import sys
from pathlib import Path

import pytest

TM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TM))
import test_template as tt  # noqa: E402


class _FakeApi:
    """Records which offers were attempted; every launch 'fails' to force a walk."""
    def __init__(self):
        self.attempted = []

    def create_instance(self, offer_id, payload):
        self.attempted.append(offer_id)
        return {"success": False, "msg": "forced failure"}


def _offers():
    # Two offers on the machine that failed, one on a different machine. The bad
    # machine is cheapest, so a naive search returns it first — exactly the shape
    # that made the live redraw re-pick the same host.
    return [
        {"id": 101, "machine_id": 35974, "gpu_name": "RTX 3070", "num_gpus": 1},
        {"id": 102, "machine_id": 35974, "gpu_name": "RTX 3070", "num_gpus": 1},
        {"id": 103, "machine_id": 37922, "gpu_name": "RTX 3090", "num_gpus": 1},
    ]


def _run(exclude):
    api = _FakeApi()
    tt.launch_with_retry(api, _offers(), lambda o: {}, 16, 5,
                         lambda _i: None, lambda *a, **k: None,
                         exclude_machines=exclude)
    return api.attempted


def test_an_excluded_machine_is_never_attempted():
    attempted = _run(exclude=[35974])
    assert 101 not in attempted and 102 not in attempted, (
        f"offers on the excluded machine were attempted: {attempted}")
    assert 103 in attempted, "the healthy machine should still be drawn"


def test_without_the_exclusion_the_same_machine_is_drawn_first():
    """The control. This is the live bug: nothing stops the cheapest — and
    already-failed — box from being picked again."""
    attempted = _run(exclude=[])
    assert attempted and attempted[0] == 101, (
        "offers are searched cheapest-first, so the previously-failed machine is "
        "the first pick; if this ever stops being true the exclusion is still "
        "correct but this test no longer demonstrates why it is needed")


def test_excluding_every_machine_exhausts_rather_than_falling_back():
    """Fail closed: if every candidate is excluded there is nothing safe to draw,
    and silently ignoring the exclusion would defeat it."""
    attempted = _run(exclude=[35974, 37922])
    assert attempted == [], f"excluded machines were attempted anyway: {attempted}"


def test_the_flag_accumulates_across_several_failed_machines():
    """A cell can fail on more than one box before it passes. Assert the flag's
    OWN action, not merely that the string appears somewhere in argparse."""
    src = (TM / "test_template.py").read_text()
    block = src.split('"--exclude-machine"')[1][:300]
    assert 'action="append"' in block, (
        "--exclude-machine must be repeatable; with store, only the last machine "
        "is excluded and the earlier ones are drawn again")
    assert "exclude_machines=args.exclude_machines" in src, (
        "the parsed flag must reach launch_with_retry")


def test_the_gate_actually_PASSES_the_exclusions_to_the_client():
    """Building the list is not the same as using it. The first version of this
    guard only checked that '--exclude-machine' appeared in the file — which it
    still does, in the loop that builds the array, even when the array is never
    handed to the client."""
    gate = (TM.parents[1] / ".github" / "workflows" / "qa-gate.yml").read_text()
    assert "--exclude-machine" in gate, "the gate never builds an exclusion list"
    invocation = gate.split('test_template.py"')[1][:600]
    assert '"${EXCL[@]}"' in invocation, (
        "the exclusion array is built but never passed on the command line, so "
        "the redraw can re-pick the box it just failed on")
    assert "qa-suspect-hosts.txt" in gate.split("--exclude-machine")[0][-900:], (
        "the exclusions must be built from the recorded failures, not a fresh list")


# ---- a host fault is not a market shortage ----------------------------------

def _gate() -> str:
    return (TM.parents[1] / ".github" / "workflows" / "qa-gate.yml").read_text()


def test_a_host_fault_is_not_reported_as_no_offers():
    """Observed 2026-08-19 in run 32236327488.

    Making the redraw reuse exit code 2 gave that code two meanings — the
    client's genuine `no_offers`, and our own "a host failed, draw again". The
    wait branch only knew the first, so a cell that drew two GPU-less boxes in a
    row reported `no offers matched the floors` twice. cu130 had 452 distinct
    machines available at the time; nothing was short. The message sent the
    reader to raise the floors, which would have been the wrong fix.
    """
    g = _gate()
    assert '_RETRY_REASON="host"' in g, (
        "the redraw branch must MARK itself as a host fault; merely having the "
        "variable is not enough — if it stays 'no_offers' the message is wrong "
        "again, which is the exact bug this guards")
    assert '_RETRY_REASON="no_offers"' in g, (
        "the default must remain the client's own no-offers meaning")
    assert 'host fault; drawing another machine' in g, (
        "a host-fault redraw must say so rather than blaming the market")
    assert 'no offers matched the floors' in g, (
        "the genuine market-shortage message must survive")


def test_a_host_fault_does_not_wait_for_market_capacity():
    """The two reasons want opposite waits. A thin market needs capacity to
    appear, so the backoff IS the point. A bad host needs a different box, which
    is available right now and already excluded — waiting the market backoff cost
    about seven minutes per redraw for nothing, multiplied across 70 cells."""
    g = _gate()
    host_branch = g.split('if [ "$_RETRY_REASON" = "host" ]')[1][:700]
    assert "inputs.retry_delay" not in host_branch, (
        "the host-fault path must not use the market backoff")
    assert "RANDOM %" in host_branch, (
        "keep a small jitter so a matrix does not re-enter the single-key QA "
        "account in lockstep (ADR 0005 cond 6)")


# ---- the loop flag must never become the verdict ----------------------------

def test_an_exhausted_redraw_reports_the_REAL_exit_code_not_the_loop_flag():
    """The redraw sets CODE=2 to mean "go round again". Exit 2 is also
    EXIT_NO_OFFERS, which `qa_verdict.classify` short-circuits to `inconclusive`
    BEFORE it looks at any test result — and on the scheduled comfyui/vllm gates
    `inconclusive` soft-passes and promotes UNGATED.

    So emitting the loop flag as the verdict turns a real, reproducible test
    failure into an automatic production promotion, twice a day, announced as
    "no live test ran". `retries` defaults to 0, so those two pipelines never even
    redraw — they got the aliasing with none of the benefit.
    """
    g = _gate()
    assert "_REAL_CODE=$CODE" in g, (
        "the client's real exit code is not preserved before the loop rewrites it")
    assert 'echo "exit_code=${_REAL_CODE}"' in g, (
        "the loop-control flag is emitted as the verdict; a real test failure "
        "becomes EXIT_NO_OFFERS and soft-passes on the scheduled gates")
    assert 'echo "exit_code=${CODE}"' not in g, (
        "CODE is the loop flag, not a verdict")


def test_a_real_failure_classifies_as_block_not_inconclusive():
    """The end-to-end property, driven through the real classifier rather than
    asserted about the YAML."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("qa_verdict", TM / "qa_verdict.py")
    qv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qv)
    raw = {"state": "failed", "got_result_event": True,
           "tests": [{"name": "base/60-gpu-cuda", "state": "failed"}],
           "stream_counts": {"passed": 10, "failed": 1, "skipped": 0}}
    req = qv.parse_required("base/60-gpu-cuda")
    assert qv.classify(1, raw, req)[0] == "block", "a real failure must block"
    assert qv.classify(5, raw, req)[0] == "block", "an instance error must block"
    # The bug, pinned: if the gate ever emits 2 for this payload the verdict flips.
    assert qv.classify(2, raw, req)[0] == "inconclusive", (
        "exit 2 means no-box-obtained; this is exactly why the loop flag must "
        "never be emitted as the verdict")
