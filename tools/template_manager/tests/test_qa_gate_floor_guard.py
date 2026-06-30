"""Guard: the QA gate's compute_cap-floor enforcement must stay enabled by default.

CON-1585 split the live-GPU QA gate (this tool) from the invariant linter into a
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
