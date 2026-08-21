"""Tests for external/vllm/.../vllm.d/contract_check.py — the ADR 0031 assertions.

WHY THIS FILE EXISTS. Every assertion in the checker can only FIRE on a rented GPU
box serving a real model, which is the most expensive place in this project to
discover that a check was inverted. The checks themselves are pure functions of an
HTTP response, so the response is what gets faked here: no GPU, no vLLM, no network.

What it is really pinning is the pair of properties ADR 0031 made binding, because
both are one careless edit away from silently reverting:

  1. Identity is EXACT. The bug being fixed was
     `any(want in mid or mid in want for mid in ids)` — a bidirectional substring
     test that passes a base model served under an instruct name. A regression here
     looks like a stricter-sounding comparison that still matches a prefix.
  2. Discovery cannot manufacture a red (decision 6). A capability that fails while
     UNDECLARED must leave the exit code at 0. Get that backwards and every image
     that happens to enable a feature starts blocking its own promote.
"""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

MOD_PATH = (Path(__file__).resolve().parents[3]
            / "external/vllm/ROOT/opt/instance-tools/tests/vllm.d/contract_check.py")


def _load():
    spec = importlib.util.spec_from_file_location("contract_check", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cc = _load()

MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
ARGS = "--enforce-eager --max-model-len 4096 --host 127.0.0.1 --port 18000"


def models(*ids, max_len=4096):
    return {"object": "list",
            "data": [{"id": i, "object": "model", "max_model_len": max_len} for i in ids]}


def chat(prompt=7, completion=1, finish="length", content="x", extra=None):
    doc = {"choices": [{"finish_reason": finish,
                        "message": dict({"role": "assistant", "content": content},
                                        **(extra or {}))}],
           "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                     "total_tokens": prompt + completion}}
    return doc


class FakeClient:
    """Scripted responses by path, with per-path overrides for the error probes.

    `post` dispatches on the request BODY as well as the path, because three of the
    contract checks differ only by what they send to /v1/chat/completions.
    """

    def __init__(self, *, models_doc=None, chat_doc=None, chat_status=200,
                 completions_doc=None, completions_status=200,
                 unknown_status=404, malformed_status=400, overflow_status=400,
                 stream_status=200, stream_lines=None,
                 embeddings=None, embeddings_status=200,
                 tools_doc=None, tools_status=200,
                 structured_doc=None, structured_status=200):
        self.models_doc = models_doc if models_doc is not None else models(MODEL)
        self.chat_doc = chat_doc if chat_doc is not None else chat()
        self.chat_status = chat_status
        self.completions_doc = completions_doc if completions_doc is not None else chat()
        self.completions_status = completions_status
        self.unknown_status = unknown_status
        self.malformed_status = malformed_status
        self.overflow_status = overflow_status
        self.stream_status = stream_status
        self.stream_lines = stream_lines
        self.embeddings = embeddings
        self.embeddings_status = embeddings_status
        self.tools_doc = tools_doc
        self.tools_status = tools_status
        self.structured_doc = structured_doc
        self.structured_status = structured_status
        self.seen = []

    # -- transport ---------------------------------------------------------
    def get(self, path):
        assert path == "/v1/models"
        return 200, json.dumps(self.models_doc)

    def post(self, path, payload, *, raw=None):
        self.seen.append((path, payload, raw))
        if raw is not None:
            return self.malformed_status, "{"
        if path == "/v1/embeddings":
            if self.embeddings_status != 200:
                return self.embeddings_status, "{}"
            return 200, json.dumps({"data": self.embeddings})
        if path == "/v1/completions":
            return self.completions_status, json.dumps(self.completions_doc)
        if payload.get("model") == cc.NO_SUCH_MODEL:
            return self.unknown_status, '{"error": "not found"}'
        if payload.get("tools"):
            if self.tools_status != 200:
                return self.tools_status, "{}"
            return 200, json.dumps(self.tools_doc)
        if payload.get("response_format"):
            if self.structured_status != 200:
                return self.structured_status, "{}"
            return 200, json.dumps(self.structured_doc)
        if payload.get("max_tokens", 0) > 4096:
            return self.overflow_status, '{"error": "too long"}'
        return self.chat_status, json.dumps(self.chat_doc)

    def post_stream(self, path, payload):
        if self.stream_lines is not None:
            return self.stream_status, self.stream_lines
        usage = {"usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8}}
        return self.stream_status, ["data: " + json.dumps({"choices": [{"delta": {"content": "x"}}]}),
                                    "", "data: " + json.dumps(usage), "", "data: [DONE]"]


@pytest.fixture(autouse=True)
def _loopback(monkeypatch):
    """Default every test to a healthy loopback bind; the bind test overrides it."""
    def fake_run(cmd, **kw):
        class R:
            stdout = "LISTEN 0 128 127.0.0.1:18000 0.0.0.0:*\n"
        return R()
    monkeypatch.setattr(cc.subprocess, "run", fake_run)


def run(client, *, expect_caps="", model=MODEL, args=ARGS):
    out = io.StringIO()
    monkey = cc.Client
    cc.Client = lambda *a, **k: client                       # noqa: ARG005
    try:
        # `--opt=value`, matching the wrapper: argparse reads a value starting with
        # `-` as an option unless it contains a space, so `--vllm-args --enforce-eager`
        # dies on a usage error. The tests send what the shell sends.
        code = cc.main([f"--model={model}", f"--vllm-args={args}",
                        f"--expect-caps={expect_caps}"], out=out)
    finally:
        cc.Client = monkey
    return code, out.getvalue()


def findings(text, kind):
    return [ln for ln in text.splitlines() if ln.startswith(kind + " ")]


# ---------------------------------------------------------------- arg parsing


def test_arg_values_handles_both_spellings_and_nargs():
    toks = ["--served-model-name", "alpha", "beta", "--port=18000", "--enforce-eager"]
    assert cc.arg_values(toks, "--served-model-name") == ["alpha", "beta"]
    assert cc.arg_values(toks, "--port") == ["18000"]
    assert cc.arg_values(toks, "--download-dir") == []
    assert cc.arg_present(toks, "--enforce-eager")
    assert not cc.arg_present(toks, "--trust-remote-code")


def test_served_model_name_overrides_vllm_model():
    """vLLM uses the FIRST --served-model-name as the id; the rest are aliases.

    A template that renames the model must be judged against the NAME IT PUBLISHED,
    not against VLLM_MODEL, or every renaming template reds on identity."""
    c = FakeClient(models_doc=models("my-alias"))
    code, text = run(c, args=ARGS + " --served-model-name my-alias other")
    assert not findings(text, "VIOLATION"), text
    assert code == 0


# ---------------------------------------------------------------- identity


def test_identity_exact_match_passes():
    code, text = run(FakeClient())
    assert not findings(text, "VIOLATION"), text
    assert code == 0


def test_identity_rejects_the_substring_match_the_old_check_accepted():
    """THE regression. The replaced check was
        any(want in mid or mid in want for mid in ids)
    so serving the BASE model while the template asked for -Instruct passed as a
    WARN. Exactness is the entire assertion; a prefix must be a violation."""
    c = FakeClient(models_doc=models("Qwen/Qwen2.5-0.5B"))
    code, text = run(c)
    assert any("identity" in f for f in findings(text, "VIOLATION")), text
    assert code == 1


def test_identity_tolerates_extra_served_ids():
    """LoRA adapters and --served-model-name aliases legitimately add entries.
    `in ids`, not `== ids[0]`: forbidding extras would red a healthy image."""
    c = FakeClient(models_doc=models("some-lora", MODEL))
    code, text = run(c)
    assert not [f for f in findings(text, "VIOLATION") if "identity" in f], text


def test_remaining_checks_address_the_served_model_after_an_identity_failure():
    """One defect, one finding. If identity fails and the checks below kept asking
    for the model that is NOT there, every one of them 404s and the log reports ten
    failures for a single cause."""
    c = FakeClient(models_doc=models("something-else"))
    code, text = run(c)
    viol = findings(text, "VIOLATION")
    assert len(viol) == 1 and "identity" in viol[0], text
    assert any(p[1] and p[1].get("model") == "something-else"
               for p in c.seen if p[0] == "/v1/chat/completions" and p[1]), c.seen


# ---------------------------------------------------------------- token arithmetic


def test_max_tokens_cap_must_be_honoured():
    c = FakeClient(chat_doc=chat(completion=17))
    code, text = run(c)
    assert any("token-arithmetic" in f for f in findings(text, "VIOLATION")), text
    assert code == 1


def test_total_tokens_must_be_the_sum():
    c = FakeClient(chat_doc={"choices": [{"finish_reason": "length",
                                          "message": {"content": "x"}}],
                             "usage": {"prompt_tokens": 7, "completion_tokens": 1,
                                       "total_tokens": 99}})
    code, text = run(c)
    assert any("token-arithmetic" in f for f in findings(text, "VIOLATION")), text


def test_finish_reason_is_reported_separately_from_the_arithmetic():
    """The softer of the two claims: a model emitting EOS first reports `stop`.
    Folding it into the arithmetic would make one message cover two defects."""
    c = FakeClient(chat_doc=chat(finish="stop"))
    code, text = run(c)
    viol = findings(text, "VIOLATION")
    assert any("finish-reason" in f for f in viol), text
    assert not any("token-arithmetic" in f for f in viol), text


def test_a_dead_chat_endpoint_is_an_error_not_a_violation():
    """`could not decide` and `decided no` are different verdicts, and only the
    first is exempt from the advisory ramp. Exit 2 dominates exit 1."""
    c = FakeClient(chat_status=503)
    code, text = run(c)
    assert findings(text, "ERROR"), text
    assert code == 2


# ---------------------------------------------------------------- routes / errors


def test_completions_route_is_asserted_because_the_worker_uses_it():
    """The serverless benchmark drives /v1/completions, which the suite has never
    touched. A chat-only deployment looks healthy today and produces no score."""
    c = FakeClient(completions_status=404)
    code, text = run(c)
    assert any("completions-route" in f for f in findings(text, "VIOLATION")), text


def test_answering_a_request_for_a_nonexistent_model_is_a_violation():
    """The defect shape is a 200: something other than what was asked for replied."""
    c = FakeClient(unknown_status=200)
    code, text = run(c)
    assert any("error-unknown-model" in f for f in findings(text, "VIOLATION")), text


def test_any_4xx_refusal_of_an_unknown_model_is_accepted():
    """Status CLASS only. ADR 0031's reversal clause says the answer to an upstream
    status change is a weaker assertion, not a wider redraw rule — so this starts
    weak on purpose and 400 must pass as readily as 404."""
    for status in (400, 404, 422):
        code, text = run(FakeClient(unknown_status=status))
        assert not [f for f in findings(text, "VIOLATION") if "unknown-model" in f], status


def test_malformed_body_must_be_refused():
    c = FakeClient(malformed_status=200)
    code, text = run(c)
    assert any("error-malformed-body" in f for f in findings(text, "VIOLATION")), text


def test_context_overflow_uses_the_servers_own_published_length():
    c = FakeClient(overflow_status=200)
    code, text = run(c)
    assert any("error-context-overflow" in f for f in findings(text, "VIOLATION")), text


def test_context_overflow_is_na_when_no_length_is_published():
    """A guessed context length turns a forced assertion into a sampled one."""
    c = FakeClient(models_doc={"data": [{"id": MODEL}]})
    code, text = run(c, args="--enforce-eager --host 127.0.0.1")
    assert any("error-context-overflow" in f for f in findings(text, "NA")), text


# ---------------------------------------------------------------- streaming


def test_stream_without_a_done_terminator_is_a_violation():
    """A client reading to the terminator hangs until its own timeout — the failure
    a chunk-counting assertion would not see."""
    c = FakeClient(stream_lines=["data: " + json.dumps({"choices": [{"delta": {}}]})])
    code, text = run(c)
    assert any("streaming" in f for f in findings(text, "VIOLATION")), text


def test_stream_must_carry_usage_when_include_usage_was_set():
    c = FakeClient(stream_lines=["data: " + json.dumps({"choices": [{"delta": {}}]}),
                                 "data: [DONE]"])
    code, text = run(c)
    assert any("usage" in f for f in findings(text, "VIOLATION")), text


def test_healthy_stream_passes():
    code, text = run(FakeClient())
    assert not [f for f in findings(text, "VIOLATION") if "streaming" in f], text


# ---------------------------------------------------------------- bind


def test_wildcard_bind_is_a_violation(monkeypatch):
    """The engine binds loopback and Caddy is what authenticates. A 0.0.0.0 listener
    on the engine port is the unauthenticated-exposure shape."""
    class R:
        stdout = "LISTEN 0 128 0.0.0.0:18000 0.0.0.0:*\n"
    monkeypatch.setattr(cc.subprocess, "run", lambda *a, **k: R())
    code, text = run(FakeClient())
    assert any("bind-loopback" in f for f in findings(text, "VIOLATION")), text


def test_nothing_listening_is_an_error_not_a_violation(monkeypatch):
    class R:
        stdout = ""
    monkeypatch.setattr(cc.subprocess, "run", lambda *a, **k: R())
    code, text = run(FakeClient())
    assert any("bind-loopback" in f for f in findings(text, "ERROR")), text


# ---------------------------------------------------------------- capabilities


def test_undeclared_capability_failure_is_advisory_and_cannot_red():
    """THE asymmetry of ADR 0031 decision 6. Discovery must never manufacture a red:
    a capability the box offers and the template never claimed is reported and the
    exit code stays 0."""
    c = FakeClient(tools_doc={"choices": [{"message": {"tool_calls": []}}]})
    code, text = run(c, args=ARGS + " --enable-auto-tool-choice")
    assert any("cap:tools" in f for f in findings(text, "ADVISORY")), text
    assert not findings(text, "VIOLATION"), text
    assert code == 0


def test_declared_capability_failure_is_a_violation():
    c = FakeClient(tools_doc={"choices": [{"message": {"tool_calls": []}}]})
    code, text = run(c, args=ARGS + " --enable-auto-tool-choice", expect_caps="tools")
    assert any("cap:tools" in f for f in findings(text, "VIOLATION")), text
    assert code == 1


def test_declared_but_undiscovered_capability_is_a_violation():
    """Claimed and absent. This is the direction option C failed in: without it, an
    author who declares a capability the image stopped offering gets a green gate."""
    code, text = run(FakeClient(), expect_caps="tools")     # no --enable-auto-tool-choice
    assert any("cap:tools" in f and "not discovered" in f
               for f in findings(text, "VIOLATION")), text


def test_undiscovered_and_undeclared_capability_is_logged_with_its_reason():
    """n/a is still evidence: a silent absence is how coverage disappears unnoticed."""
    code, text = run(FakeClient())
    assert any("cap:tools" in f for f in findings(text, "NA")), text
    assert code == 0


def test_forced_tool_call_must_return_the_named_function():
    c = FakeClient(tools_doc={"choices": [{"message": {"tool_calls": [
        {"function": {"name": "something_else", "arguments": "{}"}}]}}]})
    code, text = run(c, args=ARGS + " --enable-auto-tool-choice", expect_caps="tools")
    assert any("cap:tools" in f for f in findings(text, "VIOLATION")), text


def test_forced_tool_call_arguments_must_parse_as_json():
    c = FakeClient(tools_doc={"choices": [{"message": {"tool_calls": [
        {"function": {"name": cc.TOOL_NAME, "arguments": "not json"}}]}}]})
    code, text = run(c, args=ARGS + " --enable-auto-tool-choice", expect_caps="tools")
    assert any("cap:tools" in f for f in findings(text, "VIOLATION")), text


def test_healthy_forced_tool_call_passes():
    c = FakeClient(tools_doc={"choices": [{"message": {"tool_calls": [
        {"function": {"name": cc.TOOL_NAME, "arguments": '{"city": "London"}'}}]}}]})
    code, text = run(c, args=ARGS + " --enable-auto-tool-choice", expect_caps="tools")
    assert not findings(text, "VIOLATION"), text
    assert code == 0


def test_grammar_constrained_output_must_satisfy_its_own_schema():
    """The grammar is what makes this decidable without judging the model: output
    that does not match the schema it was constrained by is a backend defect."""
    c = FakeClient(structured_doc={"choices": [{"message": {"content": '{"n": "seven"}'}}]})
    code, text = run(c, expect_caps="structured-output")
    assert any("cap:structured-output" in f for f in findings(text, "VIOLATION")), text


def test_structured_output_refused_is_not_discovered_rather_than_broken():
    c = FakeClient(structured_status=400)
    code, text = run(c)
    assert any("cap:structured-output" in f for f in findings(text, "NA")), text
    assert code == 0


def test_embeddings_shape_only():
    c = FakeClient(embeddings=[{"embedding": [0.1, 0.2]}, {"embedding": [0.3]}])
    code, text = run(c, expect_caps="embeddings")
    assert any("cap:embeddings" in f for f in findings(text, "VIOLATION")), text


def test_embeddings_healthy_passes():
    c = FakeClient(embeddings=[{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}])
    code, text = run(c, expect_caps="embeddings")
    assert not findings(text, "VIOLATION"), text


def test_unknown_declared_capability_is_an_error():
    """A typo in VLLM_EXPECT_CAPS must not read as a satisfied declaration."""
    code, text = run(FakeClient(), expect_caps="toolz")
    assert any("capabilities" in f for f in findings(text, "ERROR")), text
    assert code == 2


# ---------------------------------------------------------------- exit codes


def test_unparseable_vllm_args_stops_before_asserting_anything():
    code, text = run(FakeClient(), args='--host "unterminated')
    assert any("vllm-args" in f for f in findings(text, "ERROR")), text
    assert code == 2


def test_errors_dominate_violations_in_the_exit_code():
    """The caller treats 2 as never-advisory. A run with both must not report 1 and
    be waved through by the ramp."""
    c = FakeClient(models_doc=models("something-else"), embeddings_status=500)
    code, text = run(c, expect_caps="embeddings")
    assert findings(text, "VIOLATION") and code in (1, 2)
    c2 = FakeClient(chat_status=500, models_doc=models("something-else"))
    code2, _ = run(c2)
    assert code2 == 2


def test_prompt_roundtrip_is_na_when_a_chat_template_is_overridden():
    """--chat-template makes the server apply a DIFFERENT function from the model's
    own, so a local count is not comparable. n/a, not a violation."""
    code, text = run(FakeClient(), args=ARGS + " --chat-template /opt/tpl.jinja")
    assert any("prompt-roundtrip" in f for f in findings(text, "NA")), text


# ---------------------------------------------------------------- completion marker


def test_every_exit_path_prints_the_completion_marker():
    """The caller keys on this line to tell a crash from a finding.

    An unhandled Python exception exits 1 — the SAME code as "violations found". A
    caller that trusted the code alone would see rc=1, find no VIOLATION lines to
    report, and pass. So the marker is printed only on an explicit exit, and every
    explicit exit must print it, including the two early ones that abandon the run
    before any assertion is made."""
    healthy, _ = run(FakeClient()), None
    for label, kwargs in [("healthy", {}),
                          ("bad args", {"args": '--host "unterminated'}),
                          ("no model named", {"model": "", "args": "--enforce-eager"})]:
        _, text = run(FakeClient(), **kwargs)
        marker = [ln for ln in text.splitlines() if ln.startswith("CONTRACT-COMPLETE ")]
        assert len(marker) == 1, f"{label}: {text}"
        assert marker[0] == text.rstrip().splitlines()[-1], f"{label}: marker must be last"


def test_models_endpoint_down_still_marks_completion():
    """The one early return that is a legitimate verdict rather than a crash: the
    server did not answer /v1/models, which is an ERROR, and the run stops there."""
    class Down(FakeClient):
        def get(self, path):
            return 503, "upstream down"
    code, text = run(Down())
    assert code == 2 and "CONTRACT-COMPLETE" in text, text
