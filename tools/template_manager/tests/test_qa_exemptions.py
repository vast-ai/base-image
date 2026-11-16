"""CA-trust exemptions reach the gate as warnings and never touch the verdict (ADR 0058).

A QA cell that passes because base/29-ca-trust applied an exemption used to read exactly
like a clean pass. The path under test: test output line -> StreamTracker -> --raw
payload -> qa_exemptions.py -> annotation, run summary, Slack output.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

import qa_exemptions
from qa_verdict import EXIT_PASSED, classify
from test_template import StreamTracker

QA_GATE = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "qa-gate.yml"
RUN = "─── Running: {} ───"

# Lines as images built before 2026-11 print them (29-ca-trust.sh, dated list).
LEGACY_BLOCK = [
    RUN.format("base/29-ca-trust"),
    "ok    0/4  /etc/ssl/certs/ca-certificates.crt",
    "allow 2/4  /usr/lib/python3/dist-packages/certifi/cacert.pem [Debian/Ubuntu certifi reads the OS bundle]",
    "allow 2/4  /venv/main/lib/python3.12/site-packages/botocore/cacert.pem "
    "[upstream botocore bundle; botocore uses certifi when importable]",
    "allow pip 25.0.1  /usr/share/python-wheels/pip-25.0.1-py3-none-any.whl "
    "[Debian/Ubuntu patched wheel reads the OS bundle]",
    "allow pip 23.0.1  /.uv/python_install/cpython-3.10.22-linux-x86_64-gnu/lib/python3.10/"
    "ensurepip/_bundled/pip-23.0.1-py3-none-any.whl "
    "[managed Python 3.10 installed at boot by the serverless worker bootstrap; remove when it is fixed]",
    "allow uv 0.9.1  /opt/sglang/bin/uv [upstream sglang engine environment uv; ours in "
    "/opt/instance-tools/bin runs first, left to upstream]",
    "PASS: base/29-ca-trust: no CA store accepts a chain through a distrusted CA",
    "→ PASSED (4s)",
]


def feed(lines):
    t = StreamTracker()
    for ln in lines:
        t.feed(ln.strip())
    return t


def test_legacy_allow_lines_are_lifted_and_patched_distro_files_are_not():
    t = feed(LEGACY_BLOCK)
    got = {(e["id"], e["check"], e["path"].rsplit("/", 1)[-1]) for e in t.exemptions}
    assert got == {
        ("legacy", "bundle", "cacert.pem"),
        ("worker-bootstrap", "pip-wheel", "pip-23.0.1-py3-none-any.whl"),
        ("legacy", "uv", "uv"),
    }
    assert all(e["legacy"] for e in t.exemptions)
    # Lifting the lines must not cost the test its recorded state.
    assert t.tests == [{"name": "base/29-ca-trust", "state": "passed"}]


def test_an_allow_line_outside_the_ca_trust_block_is_not_an_exemption():
    t = feed([RUN.format("base/40-other"), "allow 2/4  /x/cacert.pem [some reason]"])
    assert t.exemptions == []


def test_current_format_is_lifted_from_any_test_and_counted_against_the_pass_line():
    t = feed([
        RUN.format("base/29-ca-trust"),
        "EXEMPT: base/29-ca-trust: nsight-private-python bundle "
        "/opt/nvidia/nsight-compute/2026.1/host/python/lib/certifi/cacert.pem [NVIDIA private Python]",
        "PASS: base/29-ca-trust: no CA store accepts a distrusted chain; 1 exemption(s) applied (EXEMPT lines above)",
        "→ PASSED (4s)",
        RUN.format("llama.d/20-serverless-pyworker"),
        "EXEMPT: llama.d/20-serverless-pyworker: worker-bootstrap pip-wheel /.uv/python_install/x [boot install]",
    ])
    assert [(e["test"], e["id"], e["legacy"]) for e in t.exemptions] == [
        ("base/29-ca-trust", "nsight-private-python", False),
        ("llama.d/20-serverless-pyworker", "worker-bootstrap", False),
    ]
    assert t.exemptions_declared == {"base/29-ca-trust": 1}
    s = qa_exemptions.summarise({"exemptions": t.exemptions, "exemptions_declared": t.exemptions_declared})
    assert s["incomplete"] == [] and s["alert"] is True and s["legacy"] is False


def test_a_replayed_stream_does_not_double_count():
    """A reconnect replays the log from the start."""
    t = feed(LEGACY_BLOCK[:-2] + LEGACY_BLOCK)
    assert len(t.exemptions) == 3


def test_a_count_the_lines_do_not_match_is_reported_incomplete():
    s = qa_exemptions.summarise({"exemptions": [], "exemptions_declared": {"base/29-ca-trust": 2}})
    assert s["incomplete"] == ["base/29-ca-trust states 2, 0 line(s) received"]
    assert "exemption report incomplete" in qa_exemptions.warning_line("r:t", s)


def test_no_exemptions_means_no_warning_and_no_summary():
    s = qa_exemptions.summarise({"tests": [], "exemptions": [], "exemptions_declared": {"base/29-ca-trust": 0}})
    assert qa_exemptions.warning_line("r:t", s) is None
    assert qa_exemptions.summary_markdown("r:t", s) == ""
    assert qa_exemptions.summarise(None)["rows"] == []      # no payload at all: same answer


def test_text_from_the_image_cannot_start_a_second_workflow_command():
    """Paths and reasons come from inside the instance under test."""
    hostile = {"test": "base/29-ca-trust", "id": "legacy\n::error::forged", "check": "bundle",
               "path": "/x\n::error::forged", "reason": "a|b\r\n::set-output name=alert::true"}
    s = qa_exemptions.summarise({"exemptions": [hostile]})
    line = qa_exemptions.warning_line("repo:tag%0A", s)
    assert "\n" not in line and "\r" not in line and "legacy%0A::error::forged" in line
    assert line.startswith("::warning title=CA-trust exemptions::repo:tag%250A: ")
    table_rows = [ln for ln in qa_exemptions.summary_markdown("r:t", s).splitlines() if ln.startswith("| legacy")]
    assert len(table_rows) == 1 and "a\\|b" in table_rows[0]


def test_main_writes_the_alert_output_and_summary_and_exits_zero(tmp_path, capsys):
    t = feed(LEGACY_BLOCK)
    raw = tmp_path / "raw.json"
    raw.write_text("narration\n" + json.dumps({"state": "passed", "exemptions": t.exemptions}) + "\n")
    out, summ = tmp_path / "out", tmp_path / "summary.md"
    rc = qa_exemptions.main(["--raw", str(raw), "--cell", "llama-cpp:b1-cuda-12.9-amd64",
                             "--github-output", str(out), "--step-summary", str(summ)])
    assert rc == 0
    assert out.read_text() == "alert=true\n"
    printed = capsys.readouterr().out
    assert "3 CA-trust exemption(s) applied (legacy x2, worker-bootstrap x1)" in printed
    assert "2027-01-01" in printed and "2027-01-01" in summ.read_text()
    # A missing payload (the client died before printing) still exits 0 and alerts nothing.
    assert qa_exemptions.main(["--raw", str(tmp_path / "absent"), "--cell", "c",
                               "--github-output", str(out)]) == 0
    assert out.read_text().endswith("alert=false\n")


def test_exemptions_do_not_change_the_verdict():
    base = {"state": "passed", "got_result_event": True, "exit_code": 0,
            "tests": [{"name": "base/29-ca-trust", "state": "passed"}],
            "stream_counts": {"passed": 1, "failed": 0, "skipped": 0}}
    with_ex = dict(base, exemptions=feed(LEGACY_BLOCK).exemptions, exemptions_declared={"base/29-ca-trust": 9})
    assert classify(EXIT_PASSED, with_ex, ["base/29-ca-trust"]) == classify(EXIT_PASSED, base, ["base/29-ca-trust"])


def test_gate_reports_after_the_verdict_even_on_a_block_and_wires_the_alert():
    wf = yaml.safe_load(QA_GATE.read_text())
    steps = wf["jobs"]["qa"]["steps"]
    ids = [s.get("id") for s in steps]
    step = steps[ids.index("exemptions")]
    assert ids.index("exemptions") > ids.index("verdict")
    assert step["if"].startswith("always()") and "qa_exemptions.py" in step["run"]
    assert "${{" not in step["run"]             # inputs reach the shell through env, not interpolation
    assert wf["jobs"]["qa"]["outputs"]["exemption_alert"] == "${{ steps.exemptions.outputs.alert }}"
    notify = wf["jobs"]["notify-exemption"]
    assert notify["needs"] == "qa"
    assert "needs.qa.outputs.exemption_alert == 'true'" in notify["if"] and "always()" in notify["if"]
