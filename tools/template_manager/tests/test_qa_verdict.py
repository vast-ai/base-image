"""qa_verdict: the CI-side half of the fail-not-skip contract (ADR 0019).

The mutation that matters: a payload identical to a real PASS except one required
GPU test is `skipped` must BLOCK. Without that, a QA template whose
INSTANCE_TEST_REQUIRE_PASS went missing would certify an image whose CUDA
userland never loaded — the in-instance gate and this one fail differently, and
this file pins the outer one.
"""

from __future__ import annotations

import json

import pytest

from qa_verdict import (
    BLOCK, INCONCLUSIVE, PASS,
    EXIT_BAD_INSTANCE, EXIT_CONFIG_ERROR, EXIT_FAILED, EXIT_INSTANCE_ERROR,
    EXIT_INTERRUPTED, EXIT_NO_OFFERS, EXIT_PASSED,
    classify, main, parse_required,
)

GPU_TRIO = ["base/60-gpu-cuda", "base/61-cuda-compute", "base/62-gpu-libraries"]


def raw_pass(tests=None, got_result_event=True, state="passed"):
    """A payload shaped like a real passing run."""
    tests = tests if tests is not None else [{"name": n, "state": "passed"} for n in GPU_TRIO]
    return {
        "state": state,
        "got_result_event": got_result_event,
        "exit_code": 0,
        "tests": tests,
        "stream_counts": {"passed": len(tests), "failed": 0, "skipped": 0},
    }


# --- the baseline it must not break ---------------------------------------

def test_clean_pass_with_all_required_tests():
    verdict, reason = classify(EXIT_PASSED, raw_pass(), GPU_TRIO)
    assert verdict == PASS, reason


def test_clean_pass_with_no_requirements():
    verdict, _ = classify(EXIT_PASSED, raw_pass(), [])
    assert verdict == PASS


# --- THE mutation ----------------------------------------------------------

def test_skipped_required_gpu_test_blocks():
    """Identical to a real PASS except base/60-gpu-cuda skipped."""
    tests = [{"name": n, "state": "skipped" if n == "base/60-gpu-cuda" else "passed"}
             for n in GPU_TRIO]
    verdict, reason = classify(EXIT_PASSED, raw_pass(tests), GPU_TRIO)
    assert verdict == BLOCK
    assert "base/60-gpu-cuda=skipped" in reason


def test_required_test_absent_from_payload_blocks():
    """The image does not ship it, or it never ran — the claim is unsupported."""
    tests = [{"name": n, "state": "passed"} for n in GPU_TRIO if n != "base/62-gpu-libraries"]
    verdict, reason = classify(EXIT_PASSED, raw_pass(tests), GPU_TRIO)
    assert verdict == BLOCK
    assert "absent" in reason and "base/62-gpu-libraries" in reason


def test_required_test_failed_blocks():
    tests = [{"name": n, "state": "failed" if n == "base/61-cuda-compute" else "passed"}
             for n in GPU_TRIO]
    verdict, reason = classify(EXIT_PASSED, raw_pass(tests), GPU_TRIO)
    assert verdict == BLOCK
    assert "base/61-cuda-compute=failed" in reason


def test_empty_tests_list_with_requirements_blocks():
    """A payload carrying no per-test data cannot satisfy a requirement."""
    verdict, _ = classify(EXIT_PASSED, raw_pass([]), GPU_TRIO)
    assert verdict == BLOCK


# --- ADR 0005 cond 2 -------------------------------------------------------

def test_passed_without_result_event_blocks():
    verdict, reason = classify(EXIT_PASSED, raw_pass(got_result_event=False), [])
    assert verdict == BLOCK
    assert "result event" in reason


def test_exit_zero_but_state_not_passed_blocks():
    verdict, _ = classify(EXIT_PASSED, raw_pass(state="running"), [])
    assert verdict == BLOCK


# --- exit-code mapping -----------------------------------------------------

@pytest.mark.parametrize("code", [EXIT_FAILED, EXIT_INSTANCE_ERROR])
def test_real_failures_block(code):
    assert classify(code, {"state": "failed"}, [])[0] == BLOCK


def test_config_error_blocks_and_names_the_harness():
    verdict, reason = classify(EXIT_CONFIG_ERROR, {"state": "config_error"}, [])
    assert verdict == BLOCK
    assert "do not promote" in reason


def test_interrupt_is_not_a_pass():
    assert classify(EXIT_INTERRUPTED, {"state": "interrupted"}, [])[0] == BLOCK


@pytest.mark.parametrize("code", [EXIT_NO_OFFERS, EXIT_BAD_INSTANCE])
def test_infra_outcomes_are_inconclusive_not_block(code):
    """Distinct from BLOCK: nothing was learned about the artifact. The caller
    decides whether to retry or hold — this tool does not set policy."""
    assert classify(code, {"state": "no_offers"}, GPU_TRIO)[0] == INCONCLUSIVE


def test_unknown_exit_code_blocks():
    assert classify(99, {"state": "?"}, [])[0] == BLOCK


def test_infra_outcome_ignores_required_tests():
    """No box means no test results; requirements must not turn it into a BLOCK."""
    assert classify(EXIT_NO_OFFERS, {"state": "no_offers", "tests": []}, GPU_TRIO)[0] == INCONCLUSIVE


# --- parsing ---------------------------------------------------------------

@pytest.mark.parametrize("spec,expected", [
    ("a b", ["a", "b"]),
    ("a,b", ["a", "b"]),
    ("a, b", ["a", "b"]),
    ("  a   b  ", ["a", "b"]),
    ("", []),
    (None, []),
])
def test_parse_required(spec, expected):
    assert parse_required(spec) == expected


# --- CLI, including the polluted-stdout case that once flipped a real PASS --

def _parse_github_output(path):
    """Read GitHub's key=value / key<<DELIM heredoc output format."""
    out, lines = {}, path.read_text().splitlines()
    i = 0
    while i < len(lines):
        key, _, val = lines[i].partition("=")
        if "<<" in lines[i].split("=", 1)[0] or (not _ and "<<" in lines[i]):
            key, _, delim = lines[i].partition("<<")
            body = []
            i += 1
            while i < len(lines) and lines[i] != delim:
                body.append(lines[i]); i += 1
            out[key] = "\n".join(body)
        else:
            out[key] = val
        i += 1
    return out


def _run_cli(capsys, tmp_path, exit_code, payload_text, require=""):
    """The verdict now leaves via --github-output, never via stdout: a reason
    legitimately contains spaces, ';' and '()', and the caller used to `eval`
    stdout — which made a PASSING cell a bash syntax error under `set -e`."""
    p = tmp_path / "raw.json"
    p.write_text(payload_text)
    gho = tmp_path / "gh-output"
    gho.touch()
    main(["--exit-code", str(exit_code), "--raw", str(p), "--require-tests", require,
          "--github-output", str(gho)])
    capsys.readouterr()
    return _parse_github_output(gho)


def test_cli_takes_the_last_json_line_not_the_whole_file(capsys, tmp_path):
    """Narration bleeding into stdout must not turn a PASS into a BLOCK."""
    text = "some narration\nprogress: 50%\n" + json.dumps(raw_pass()) + "\n"
    res = _run_cli(capsys, tmp_path, 0, text, " ".join(GPU_TRIO))
    assert res["verdict"] == PASS


def test_cli_exit_zero_with_unparseable_raw_blocks(capsys, tmp_path):
    res = _run_cli(capsys, tmp_path, 0, "not json at all\n")
    assert res["verdict"] == BLOCK
    assert "no parseable" in res["reason"]


def test_cli_missing_raw_file_on_exit_zero_blocks(capsys, tmp_path):
    gho = tmp_path / "gh-output"; gho.touch()
    main(["--exit-code", "0", "--raw", "/nonexistent/raw.json", "--github-output", str(gho)])
    capsys.readouterr()
    assert _parse_github_output(gho)["verdict"] == BLOCK


def test_cli_always_exits_zero_so_a_verdict_is_not_confused_with_a_crash(capsys, tmp_path):
    p = tmp_path / "raw.json"
    p.write_text(json.dumps(raw_pass([])))
    gho = tmp_path / "gh-output"; gho.touch()
    assert main(["--exit-code", "0", "--raw", str(p), "--require-tests", "base/60-gpu-cuda",
                 "--github-output", str(gho)]) == 0
    capsys.readouterr()
    assert _parse_github_output(gho)["verdict"] == BLOCK


def test_a_reason_with_shell_metacharacters_survives_the_output_file(capsys, tmp_path):
    """The regression that broke every QA cell: the reason for a PASS contains
    '(' and ')', and the workflow used to `eval` it. Nothing about the transport
    may re-introduce shell interpretation."""
    p = tmp_path / "raw.json"
    p.write_text(json.dumps(raw_pass()))
    gho = tmp_path / "gh-output"; gho.touch()
    main(["--exit-code", "0", "--raw", str(p), "--require-tests", " ".join(GPU_TRIO),
          "--github-output", str(gho)])
    capsys.readouterr()
    res = _parse_github_output(gho)
    assert res["verdict"] == PASS
    assert "(" in res["reason"] and ")" in res["reason"], (
        "the very payload that broke bash must still round-trip intact")
