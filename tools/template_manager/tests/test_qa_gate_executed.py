"""Run the gate's shell, don't grep it.

A blind panel mutation-tested the guards added alongside these steps and found
three that pass while the behaviour they name is destroyed:

  * `>> /tmp/qa-suspect-hosts.txt` -> `>`  (only the last suspect survives)
  * inverting the exoneration test        (tells a human to de-verify healthy
                                           hosts, and clears genuinely bad ones)
  * deleting `HEADER_TEXT="${_cut}…"`     (header never shortened; the HTTP 400
                                           that lost a whole notification returns)

All three were greps for a string the same commit had just written. wfexec.py
exists precisely to end that pattern — its docstring records the gate being
disarmed twice with the suite green — and it was not used. These tests execute
the real `run:` blocks with stubbed externals and assert on what comes out.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wfexec  # noqa: E402

QA_GATE = wfexec.QA_GATE
NOTIFY = wfexec.REPO / ".github/workflows/notify-slack.yml"


def _run(script: str, workdir: Path, env: dict, stub_curl: bool = False):
    """bash -e the script with a PATH that can carry a stubbed curl."""
    workdir.mkdir(parents=True, exist_ok=True)
    bindir = workdir / "bin"
    bindir.mkdir(exist_ok=True)
    if stub_curl:
        # Capture the payload instead of POSTing it, and succeed, so the test
        # asserts on what WOULD be sent rather than on Slack's availability.
        (bindir / "curl").write_text(
            '#!/bin/bash\nfor a in "$@"; do prev=$a; done\n'
            'while [ $# -gt 0 ]; do [ "$1" = "--data" ] && { echo "$2" > "$CURL_BODY"; }; shift; done\n'
            'exit 0\n')
        (bindir / "curl").chmod(0o755)
    summary = workdir / "summary.md"
    summary.write_text("")
    out = workdir / "gh_out"
    out.write_text("")
    e = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(workdir),
         "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_OUTPUT": str(out), **env}
    r = subprocess.run(["bash", "-e", "-c", script], cwd=workdir,
                       capture_output=True, text=True, env=e)
    return r, summary.read_text(), out.read_text()


# ---- the exoneration branch (ADR 0029 binding condition 3) -------------------

REPORT = "Report suspect hosts"


def _report(workdir, suspects: str, final_code: str):
    script = wfexec.step_script(QA_GATE, "qa", REPORT)
    return _run(script, workdir, {"SUSPECTS": suspects, "FINAL_CODE": final_code,
                                  "QA_REPO": "pytorch", "QA_TAG": "qa-1-auto-cu126"})


def test_a_cell_that_passed_after_a_redraw_names_de_verification_candidates(tmp_path):
    r, summary, _ = _report(tmp_path, "35974|48034321|1|pytorch.d/40-nccl-init", "0")
    assert r.returncode == 0, r.stderr
    assert "De-verification candidates" in summary
    assert "NOT exonerated" not in summary
    assert "35974" in summary


def test_a_cell_that_NEVER_passed_must_not_name_de_verification_candidates(tmp_path):
    """The image is still a live suspect, so these hosts are not evidence.
    Inverting the branch would tell an operator to remove healthy third-party
    machines from the marketplace for a defect that is ours."""
    r, summary, _ = _report(tmp_path, "35974|48034321|1|pytorch.d/40-nccl-init", "1")
    assert r.returncode == 0, r.stderr
    assert "NOT exonerated" in summary, (
        "a cell that never passed must not present its hosts as at fault")
    assert "De-verification candidates" not in summary


def test_an_unknown_machine_is_not_offered_for_de_verification(tmp_path):
    """exit 3 (bad_instance) is emitted before any machine is bound, so the
    record is `unknown`. Presenting that as a candidate is noise at best and a
    wrong accusation at worst."""
    r, summary, _ = _report(tmp_path, "unknown|unknown|3|none", "0")
    assert r.returncode == 0, r.stderr
    assert "`unknown`" not in summary, "an unknown machine id must not be tabled"


def test_every_recorded_attempt_appears_not_just_the_last(tmp_path):
    """Kills the `>>` -> `>` mutation: one machine failing is a bad host, several
    DIFFERENT machines failing the same image is the pattern that says the image
    is at fault — which is the whole mitigation ADR 0029 rests on."""
    r, summary, _ = _report(
        tmp_path, "35974|1|1|a\n136916|2|1|a\n140359|3|1|a", "0")
    assert r.returncode == 0, r.stderr
    for m in ("35974", "136916", "140359"):
        assert m in summary, f"machine {m} missing from the report"


# ---- the Slack header clamp -------------------------------------------------

def _notify(workdir, headline: str, build_result="success"):
    script = wfexec.step_script(NOTIFY, "notify", "Notify Slack")
    body = workdir / "body.json"
    env = {"HEADLINE": headline, "BUILD_RESULT": build_result, "STATUS": "",
           "IMAGE_NAME": "PyTorch (Production)", "IMAGE_TAGS": "[]",
           "TRIGGER": "workflow_dispatch", "IMAGE_REF": "2026-08-18",
           "RUN_URL": "https://example/run/1", "CURL_BODY": str(body),
           "SLACK_WEBHOOK_URL": "https://hooks.example/x"}
    r, _, _ = _run(script, workdir, env, stub_curl=True)
    payload = json.loads(body.read_text()) if body.exists() else None
    return r, payload


def _blocks(payload):
    # Slack payload is {"attachments":[{"blocks":[...]}]}, not top-level blocks.
    return payload["attachments"][0]["blocks"]


def _header_of(payload):
    for b in _blocks(payload):
        if b.get("type") == "header":
            return b["text"]["text"]
    raise AssertionError("no header block")


LONG = ("PyTorch promoted — 70/70 artifacts QA'd on live GPUs (amd64, single GPU; "
        "mini exhaustively, auto+multi at default python). Tested: "
        + ",".join(f"2.13.0-cuda-12.6.3-py31{i}" for i in range(9)))


def test_a_long_headline_is_clamped_below_slacks_header_limit(tmp_path):
    """Slack's header block is plain_text with a hard 150-char limit and rejects
    the WHOLE post with HTTP 400 above it — losing the notification entirely,
    including whether anything shipped. Measured live at 527 chars."""
    r, payload = _notify(tmp_path, LONG)
    assert r.returncode == 0, r.stderr
    assert payload is not None, "no payload was sent"
    assert len(_header_of(payload)) <= 150, (
        f"header is {len(_header_of(payload))} chars; Slack rejects >150 with a 400")


def test_the_clamped_text_survives_in_the_body(tmp_path):
    """A headline long enough to clamp is carrying information; the body limit is
    3000, not 150. Dropping it would trade a lost message for a misleading one."""
    _, payload = _notify(tmp_path, LONG)
    blob = json.dumps(payload)
    assert "70/70 artifacts" in blob
    assert LONG[-30:] in blob, "the tail of the headline was discarded"


def test_the_clamp_does_not_eat_the_budget_on_a_space_free_tail(tmp_path):
    """The word-boundary trim must be conditional on the TRIMMED length. Testing
    the fixed-width cut is vacuously true, and a comma-joined tag tail then
    trimmed back into the prose prefix: 194 chars in, 27 out."""
    _, payload = _notify(tmp_path, LONG)
    assert len(_header_of(payload)) >= 100, (
        f"header collapsed to {len(_header_of(payload))} chars — the trim ate the budget")


def test_a_short_headline_is_left_alone(tmp_path):
    _, payload = _notify(tmp_path, "PyTorch promoted")
    assert "…" not in _header_of(payload)


# ---- the redraw loop itself --------------------------------------------------
#
# The step carries seven scalar `${{ }}` inputs, so wfexec.step_script refuses it.
# They are bound explicitly below and the result is asserted to contain no
# leftover expression — the script under test is the one CI runs, with its inputs
# supplied, rather than a paraphrase of it.

_BIND = {"inputs.label": "qa-test", "inputs.max_price": "1.00",
         "inputs.require_floor": "false", "inputs.retries": "2",
         "inputs.retry_delay": "0", "inputs.timeout": "60",
         "steps.create.outputs.hash": "deadbeef"}


def _loop_script(retries="2"):
    import yaml as _y
    wf = _y.safe_load(QA_GATE.read_text())
    step = [s for s in wf["jobs"]["qa"]["steps"] if s.get("id") == "test"][0]
    script = step["run"]
    for expr, val in {**_BIND, "inputs.retries": retries}.items():
        script = script.replace("${{ " + expr + " }}", val)
    assert "${{" not in script, "unresolved expression — bind it, never guess"
    return script


RED = ('{"state":"failed","got_result_event":true,"machine_id":%d,'
       '"tests":[{"name":"base/60-gpu-cuda","state":"failed"}],'
       '"stream_counts":{"passed":1,"failed":1,"skipped":0}}')


def _run_loop(tmp_path, exit_codes, machines):
    """Stub the client: one exit code + machine id per attempt, in order."""
    tm = tmp_path / "tm"
    tm.mkdir(parents=True, exist_ok=True)
    (tm / "counter").write_text("0")
    (tm / "test_template.py").write_text(
        "import sys, pathlib\n"
        f"codes = {list(exit_codes)!r}\nmachines = {list(machines)!r}\n"
        "c = pathlib.Path(__file__).with_name('counter')\n"
        "i = int(c.read_text()); c.write_text(str(i + 1))\n"
        "i = min(i, len(codes) - 1)\n"
        f"print({RED!r} % machines[i])\n"
        "sys.exit(codes[i])\n")
    # a fresh suspect file per run, as a real runner VM would have
    (tmp_path / "qa-suspect-hosts.txt").write_text("")
    script = _loop_script().replace("/tmp/qa-suspect-hosts.txt",
                                    str(tmp_path / "qa-suspect-hosts.txt"))
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    # `python` (the workflow calls it unqualified) and `sleep` are externals, and
    # stubbing an external is what wfexec already does for `crane`. Stubbing sleep
    # keeps the real backoff arithmetic under test while not spending it.
    (bindir / "python").write_text(f'#!/bin/bash\nexec {sys.executable} "$@"\n')
    (bindir / "python").chmod(0o755)
    (bindir / "sleep").write_text('#!/bin/bash\nexit 0\n')
    (bindir / "sleep").chmod(0o755)
    r, _, out = _run(script, tmp_path, {"TM": str(tm), "LOG_PATHS": "", "EXTRA_ENV": ""})
    outputs = dict(l.split("=", 1) for l in out.splitlines() if "=" in l and not l.startswith("suspect"))
    return r, outputs, (tmp_path / "qa-suspect-hosts.txt").read_text()


def test_an_exhausted_redraw_emits_the_REAL_code_so_a_red_still_BLOCKS(tmp_path):
    """The defect four reviewers found independently. CODE=2 is a loop flag, but
    it was emitted as the verdict — and exit 2 is EXIT_NO_OFFERS, which
    qa_verdict short-circuits to `inconclusive` before reading any test result.
    On a scheduled run that soft-passes and PROMOTES the broken image."""
    r, out, _ = _run_loop(tmp_path, [1, 1, 1], [35974, 136916, 140359])
    assert r.returncode == 0, r.stderr
    assert out["exit_code"] == "1", (
        f"emitted exit_code={out['exit_code']}; 2 means no_offers, which "
        f"soft-passes on the schedule path and promotes a failing image")


def test_every_attempt_is_recorded_not_just_the_last(tmp_path):
    """Kills `>>` -> `>`. One machine failing is a bad host; several DIFFERENT
    machines failing the same image is the pattern ADR 0029's whole mitigation
    rests on, and it only exists if every attempt is kept."""
    _, _, suspects = _run_loop(tmp_path, [1, 1, 1], [35974, 136916, 140359])
    for m in ("35974", "136916", "140359"):
        assert m in suspects, f"machine {m} was overwritten; only the last survived"


def test_a_pass_after_a_redraw_ends_the_loop_and_reports_success(tmp_path):
    r, out, suspects = _run_loop(tmp_path, [1, 0], [35974, 136916])
    assert out["exit_code"] == "0"
    assert "35974" in suspects and "136916" not in suspects


def test_config_error_is_never_redrawn(tmp_path):
    """Exit 4 is our own bug; retrying it would make a broken gate look healthy."""
    r, out, suspects = _run_loop(tmp_path, [4, 0], [35974, 136916])
    assert out["exit_code"] == "4", "config_error must not be retried"
    assert out["attempts"] == "1"


# A SHORT prose prefix followed by a long space-free tail. This is the shape that
# exposes a word-boundary trim guarded on the pre-trim length: the only space is
# near the start, so trimming to it discards almost the entire budget. The first
# version of the guard tested ${#_cut}, always 147 here, so it always trimmed.
TIGHT = "PyTorch promoted: " + ",".join(f"2.13.0-cuda-12.6.3-py31{i}" for i in range(12))


def test_a_short_prefix_with_a_space_free_tail_keeps_its_budget(tmp_path):
    _, payload = _notify(tmp_path, TIGHT)
    header = _header_of(payload)
    assert len(header) <= 150, "must still respect Slack's limit"
    assert len(header) >= 140, (
        f"header collapsed to {len(header)} chars — the word-boundary trim ate "
        f"the budget by trimming back to the only space, in the prefix")
