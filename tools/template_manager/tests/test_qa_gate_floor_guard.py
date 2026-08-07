"""Guard: the QA gate's compute_cap-floor enforcement must stay enabled by default.

This work split the live-GPU QA gate (this tool) from the invariant linter into a
separate PR that can land first. That makes the gate's *runtime* ``--require-floor``
the sole pre-merge guard that a QA template declares a usable ``compute_cap`` floor
(linter rule L050 is the earlier *static* catch and may merge later). If
``qa-gate.yml``'s ``require_floor`` default were silently flipped to false, or the
``--require-floor`` wiring removed, the gate could rent an unbounded/expensive box
against a floor-less template. Lock both down. (Surfaced by a critical review of the decouple; ADR 0005.)
"""
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
QA_GATE = REPO / ".github" / "workflows" / "qa-gate.yml"


def _workflow_call_inputs():
    data = yaml.safe_load(QA_GATE.read_text())
    # YAML 1.1 resolves a bare ``on:`` key to the boolean True.
    on = data.get("on", data.get(True))
    return on["workflow_call"]["inputs"]


def test_qa_gate_yml_present():
    assert QA_GATE.is_file(), f"missing {QA_GATE}"


def test_require_floor_defaults_true():
    spec = _workflow_call_inputs()["require_floor"]
    assert spec.get("default") is True, (
        "qa-gate.yml require_floor default must stay true — it is the sole "
        "pre-merge compute_cap-floor guard when the QA gate ships ahead of the linter"
    )


def test_require_floor_is_wired_to_the_cli():
    # A true default is inert if the arg is never passed: the test step must add
    # --require-floor when inputs.require_floor is true.
    text = QA_GATE.read_text()
    assert "--require-floor" in text, "qa-gate.yml no longer passes --require-floor"
    assert "inputs.require_floor" in text, (
        "qa-gate.yml --require-floor is not keyed on the require_floor input"
    )


# --- host-shape floors (added 2026-08-07 from a live incident) --------------

def test_base_qa_floors_out_port_restricted_hosts():
    """A proxied/VPN-fronted host pulls images pathologically slowly even when it
    ADVERTISES a fast link — so the floor is on port count, not bandwidth.

    Measured against 930 live offers matching the other base-qa floors: hosts with
    <8 forwardable ports still advertised a median 253 Mbps. A bandwidth floor
    would not have excluded them; this one does.
    """
    import yaml
    from pathlib import Path
    tmpl = yaml.safe_load(
        (Path(__file__).resolve().parents[3] / "templates/base-qa/template.yml").read_text())
    dpc = tmpl["extra_filters"]["direct_port_count"]
    assert "gte" in dpc, "direct_port_count must be a LOWER bound"
    assert "lte" not in dpc and "lt" not in dpc, (
        "an upper bound here would select FOR the restricted hosts this excludes")
    assert dpc["gte"] >= 32, f"floor {dpc['gte']} is inside the low-port tail"


def test_base_qa_bounds_the_loading_phase_for_a_short_workload():
    """The client's 40 min default is calibrated for a derivative pulling a 100 GB
    model. Base QA's whole test takes 2-8 min, so a generous cap means one slow
    host burns 40 min per launch attempt — bounded only by the job cap."""
    import yaml
    from pathlib import Path
    wf = yaml.safe_load(
        (Path(__file__).resolve().parents[3] / ".github/workflows/promote-base-image.yml").read_text())
    pt = int(wf["jobs"]["qa"]["with"]["poll_timeout"])
    assert 0 < pt <= 1800, f"poll_timeout {pt}s does not bound a minutes-long workload"


def test_poll_timeout_default_leaves_other_consumers_untouched():
    """qa-gate is shared with the live vLLM and ComfyUI gates, which legitimately
    need the long default. An empty default must mean 'unchanged'."""
    import yaml
    from pathlib import Path
    wf = yaml.safe_load(
        (Path(__file__).resolve().parents[3] / ".github/workflows/qa-gate.yml").read_text())
    assert wf[True]["workflow_call"]["inputs"]["poll_timeout"]["default"] == ""
