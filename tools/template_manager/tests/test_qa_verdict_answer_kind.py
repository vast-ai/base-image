"""Answer checks never gate image promotion (ADR 0059 D4), even under the opt-in."""
import qa_verdict as qv


def _r(name, state, kind):
    return {"name": name, "state": state, "kind": kind, "advisory": True}


def test_a_failed_answer_check_is_listed_and_does_not_block():
    blocks, summary = qv.classify_external([
        _r("external/transcriptions", "passed", "structural"),
        _r("external/transcriptions-accuracy", "failed", "answer")])
    assert not blocks
    assert "external/transcriptions-accuracy=failed" in summary
    assert "answer checks" in summary


def test_an_answer_check_error_does_not_block_either():
    blocks, summary = qv.classify_external([_r("external/speech-audio", "error", "answer")])
    assert not blocks and "external/speech-audio=error" in summary


def test_a_failed_structural_check_still_blocks():
    blocks, _ = qv.classify_external([_r("external/transcriptions", "failed", "structural")])
    assert blocks


def test_a_failure_with_no_kind_blocks():
    blocks, _ = qv.classify_external([{"name": "external/x", "state": "failed"}])
    assert blocks


def test_a_required_answer_check_that_failed_still_blocks():
    # Lint L126 refuses this at rest; if one gets through, required means required.
    blocks, _ = qv.classify_external(
        [_r("external/speech-audio", "failed", "answer")], required=["external/speech-audio"])
    assert blocks
