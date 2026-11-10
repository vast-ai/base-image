"""Tests for tools/template_manager/contract.py — the OpenAI contract, run from the client.

Ported from tools/imagegen/tests/test_vllm_contract_check.py together with the
checker (ADR 0059 D5). The check functions are verbatim ports, so these tests are
the original ones, run against the client-side module; what is new is pinned at the
end: the engine table, keying on declared routes, and the transport through Caddy.

The original's reason still stands: every assertion can only FIRE on a rented GPU
box, the most expensive place to discover a check was inverted, and the checks are
pure functions of an HTTP response — so the response is what gets faked here.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

import contract as cc

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
        # A real engine refuses an unknown model on any route; the error probes now
        # run on whichever route the template declares, so the fake does the same.
        if payload.get("model") == cc.NO_SUCH_MODEL:
            return self.unknown_status, '{"error": "not found"}'
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


def run(client, *, expect_caps="", model=MODEL, args=ARGS, engine="vastai/vllm",
        routes="chat,completions"):
    out = io.StringIO()
    monkey = cc.Client
    cc.Client = lambda *a, **k: client                       # noqa: ARG005
    try:
        # `--opt=value`, matching the wrapper: argparse reads a value starting with
        # `-` as an option unless it contains a space, so `--vllm-args --enforce-eager`
        # dies on a usage error. The tests send what the shell sends.
        code = cc.main(["--base-url=203.0.113.7:40000", f"--engine={engine}",
                        f"--routes={routes}", f"--model={model}", f"--engine-args={args}",
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


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_5xx_on_a_malformed_body_is_a_violation_not_a_refusal(status):
    """A server that crashes on truncated JSON has not refused it. Kills the mutant
    that turns the not-a-4xx branch into a pass."""
    c = FakeClient(malformed_status=status)
    code, text = run(c)
    assert any("error-malformed-body" in f and f"HTTP {status}" in f
               for f in findings(text, "VIOLATION")), text
    assert not any("error-malformed-body" in ln for ln in text.splitlines() if " ok " in ln)
    assert code == 1


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


def test_embeddings_is_a_route_not_a_contract_capability():
    """Not ported on purpose: discovering embeddings meant probing /v1/embeddings on a
    chat template, an undeclared route. Embeddings is a declared route (OPENAI_ROUTES),
    checked by external_checks.check_embeddings; naming it in *_EXPECT_CAPS is now an
    unknown capability, said out loud rather than silently ignored."""
    assert "embeddings" not in cc.CAPABILITIES
    c = FakeClient()
    code, text = run(c, expect_caps="embeddings")
    assert any("capabilities" in f and "embeddings" in f for f in findings(text, "ERROR")), text
    assert not any(p == "/v1/embeddings" for p, _, _ in c.seen), "probed an undeclared route"


def test_unknown_declared_capability_is_an_error():
    """A typo in VLLM_EXPECT_CAPS must not read as a satisfied declaration."""
    code, text = run(FakeClient(), expect_caps="toolz")
    assert any("capabilities" in f for f in findings(text, "ERROR")), text
    assert code == 2


# ---------------------------------------------------------------- exit codes


def test_unparseable_engine_args_stops_before_asserting_anything():
    code, text = run(FakeClient(), args='--host "unterminated')
    assert any("engine-args" in f for f in findings(text, "ERROR")), text
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


# ---------------------------------------------------------------- token counting


class _BatchEncoding(dict):
    """What newer transformers returns from apply_chat_template(tokenize=True).

    The shape that caused the false positive: it is dict-like with TWO keys, so
    `len()` reports 2 regardless of how many tokens the prompt actually has.
    """
    def __init__(self, ids):
        super().__init__(input_ids=ids, attention_mask=[1] * len(ids))
        self.input_ids = ids


def test_token_count_reads_input_ids_not_the_container_length():
    """THE bug, measured live: a 31-token Qwen prompt counted as 2 — the number of
    KEYS in the BatchEncoding — and the check reported 'the served chat template is
    not the model's own' against a server that was completely correct."""
    ids = list(range(31))
    assert len(_BatchEncoding(ids)) == 2          # the trap, pinned
    assert cc._token_count(_BatchEncoding(ids)) == 31


def test_token_count_handles_a_flat_list():
    assert cc._token_count([5, 6, 7]) == 3


def test_token_count_handles_a_batch_of_one():
    assert cc._token_count([[5, 6, 7]]) == 3


def test_token_count_handles_a_plain_dict():
    assert cc._token_count({"input_ids": [1, 2, 3, 4], "attention_mask": [1, 1, 1, 1]}) == 4


def test_token_count_refuses_to_guess():
    """Returning a wrong number is indistinguishable from a real defect, so an
    unrecognised shape must decide nothing rather than decide wrongly."""
    for bad in (None, "31 tokens", {}, {"attention_mask": [1]}, [], [[1], [2]], [object()]):
        assert cc._token_count(bad) is None, bad


# ---------------------------------------------------------------- declared deviations


def run_dev(client, deviations, **kw):
    """Drive main() with a deviation set, the way an ENGINE block would supply one."""
    out = io.StringIO()
    real_report, real_client = cc.Report, cc.Client
    cc.Client = lambda *a, **k: client                            # noqa: ARG005
    cc.Report = lambda o=out, d=None: real_report(o, deviations)  # noqa: ARG005
    try:
        code = cc.main(["--base-url=203.0.113.7:40000", f"--model={kw.get('model', MODEL)}",
                        f"--vllm-args={kw.get('args', ARGS)}".replace("--vllm-args", "--engine-args"),
                        "--expect-caps="], out=out)
    finally:
        cc.Report, cc.Client = real_report, real_client
    return code, out.getvalue()


def test_a_declared_deviation_is_reported_and_does_not_block():
    """SGLang answers HTTP 200 for a model that does not exist, where vLLM returns
    404. Upstream, unfixable here, and BOUNDED — `identity` still asserts /v1/models
    advertises exactly the requested model, so a client can always tell what it is
    talking to."""
    c = FakeClient(unknown_status=200)
    code, text = run_dev(c, {"error-unknown-model": "SGLang does not police `model`"})
    assert findings(text, "DEVIATION"), text
    assert not findings(text, "VIOLATION"), text
    assert code == 0


def test_a_deviation_that_stops_reproducing_becomes_a_violation():
    """THE property that stops this being an exemption cycle. If the engine starts
    behaving, the declaration has outlived the defect and the gate must say so —
    otherwise a deviation is an exemption with better manners, and it accumulates
    exactly the way ADR 0031 says option B's opt-outs did."""
    c = FakeClient(unknown_status=404)                    # engine now behaves
    code, text = run_dev(c, {"error-unknown-model": "SGLang does not police `model`"})
    viol = findings(text, "VIOLATION")
    assert any("error-unknown-model" in v and "delete the entry" in v for v in viol), text
    assert code == 1


def test_an_undeclared_failure_is_still_a_violation():
    """A deviation set must not soften anything it does not name."""
    c = FakeClient(malformed_status=200)
    code, text = run_dev(c, {"error-unknown-model": "unrelated"})
    assert any("error-malformed-body" in v for v in findings(text, "VIOLATION")), text
    assert code == 1


def test_no_deviations_declared_leaves_behaviour_unchanged():
    c = FakeClient(unknown_status=200)
    code, text = run_dev(c, {})
    assert any("error-unknown-model" in v for v in findings(text, "VIOLATION")), text
    assert code == 1


def test_a_501_means_the_capability_is_not_offered_not_broken():
    """A 501 is the engine answering the discovery question with a clean no — seen on
    llama-server, which answers an unsupported feature with 501 not_supported_error.
    Reporting it as a capability that failed manufactures a finding out of a no."""
    c = FakeClient(structured_status=501)
    code, text = run(c)
    assert any("cap:structured-output" in f for f in findings(text, "NA")), text
    assert not any("cap:structured-output" in f for f in findings(text, "ADVISORY")), text
    assert not any("cap:structured-output" in f for f in findings(text, "VIOLATION")), text


# ================================================================ new in the port
# The checks above are verbatim ports. What follows pins what the port changed:
# one engine table, keying on the template's declared routes, and the transport.

import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


@pytest.mark.parametrize("image,name,ctx", [
    ("vastai/vllm", "vllm", "--max-model-len"),
    ("vastai/vllm:v0.32.0-cuda-13.0", "vllm", "--max-model-len"),
    ("vastai/vllm-omni", "vllm-omni", "--max-model-len"),
    ("vastai/sglang", "sglang", "--context-length"),
    ("vastai/llama-cpp", "llama", "--ctx-size"),
])
def test_the_engine_row_is_chosen_from_the_image(image, name, ctx):
    row = cc.set_engine(image)
    assert row["name"] == name and row["context_flag"] == ctx
    cc.set_engine("vastai/vllm")


def test_an_unknown_engine_is_refused():
    out = io.StringIO()
    assert cc.main(["--base-url=x:1", "--engine=vastai/pytorch"], out=out) == 2
    assert "unknown engine" in out.getvalue()


def test_sglang_carries_no_unknown_model_deviation_any_more():
    """It expired on SGLang v0.5.22, which refuses with 404."""
    assert "error-unknown-model" not in cc.ENGINES["vastai/sglang"]["deviations"]
    code, text = run(FakeClient(unknown_status=404), engine="vastai/sglang")
    assert any("error-unknown-model" in ln for ln in text.splitlines() if "ok" in ln), text
    cc.set_engine("vastai/vllm")


def test_llama_keeps_its_two_deviations():
    dev = cc.ENGINES["vastai/llama-cpp"]["deviations"]
    assert set(dev) == {"error-unknown-model", "error-context-overflow"}
    code, text = run(FakeClient(unknown_status=200, overflow_status=200), engine="vastai/llama-cpp",
                     args="--ctx-size 4096 --alias " + MODEL)
    assert {f.split()[1] for f in findings(text, "DEVIATION")} == set(dev), text
    assert code == 0
    cc.set_engine("vastai/vllm")


def test_llama_identity_reads_alias():
    c = FakeClient(models_doc=models("my-alias"))
    code, text = run(c, engine="vastai/llama-cpp", args="--ctx-size 4096 --alias my-alias")
    assert any("identity" in ln and "my-alias" in ln for ln in text.splitlines() if " ok " in ln), text
    cc.set_engine("vastai/vllm")


def _paths(c):
    return [p for p, _, _ in c.seen]


def test_a_non_chat_template_is_never_sent_chat():
    """The point of moving the contract to the client: it knows what it tests for."""
    c = FakeClient(embeddings=[{"embedding": [0.1]}])
    code, text = run(c, routes="embeddings")
    assert "/v1/chat/completions" not in _paths(c), _paths(c)
    for name in ("token-arithmetic", "finish-reason", "streaming", "completions-route",
                 "cap:tools", "cap:structured-output"):
        assert any(name in f for f in findings(text, "NA")), (name, text)
    assert code == 0


def test_error_probes_use_a_declared_route():
    c = FakeClient(embeddings_status=404)
    code, text = run(c, routes="embeddings")
    unknown = [(p, pl) for p, pl, raw in c.seen if pl and pl.get("model") == cc.NO_SUCH_MODEL]
    assert unknown and unknown[0][0] == "/v1/embeddings"
    assert any(p == "/v1/embeddings" and raw == b"{" for p, _, raw in c.seen)


def test_completions_only_template_probes_completions():
    c = FakeClient()
    code, text = run(c, routes="completions")
    assert "/v1/chat/completions" not in _paths(c)
    assert any(p == "/v1/completions" for p in _paths(c))


def test_no_declared_engine_route_means_the_error_probes_are_na():
    c = FakeClient()
    code, text = run(c, routes="speech")
    assert _paths(c) == []
    assert any("error-unknown-model" in f for f in findings(text, "NA")), text


def test_a_declared_tool_capability_without_chat_is_a_violation():
    code, text = run(FakeClient(), routes="embeddings", expect_caps="tools")
    assert any("cap:tools" in f for f in findings(text, "VIOLATION")), text


# ---------------------------------------------------------------- transport

def _serve(handler):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_the_client_sends_the_token_and_falls_back_to_http():
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen["auth"] = self.headers.get("Authorization")
            data = json.dumps(models(MODEL)).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = _serve(H)
    cli = cc.Client(f"127.0.0.1:{srv.server_port}", "tok", timeout=5)
    status, body = cli.get("/v1/models")
    srv.shutdown()
    assert status == 200 and json.loads(body)["data"][0]["id"] == MODEL
    assert seen["auth"] == "Bearer tok"
    assert cli.bases == [f"http://127.0.0.1:{srv.server_port}"]   # the scheme that answered


def test_the_client_streams_sse_lines_and_reports_http_errors():
    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/bad":
                body = b'{"error":"nope"}'
                self.send_response(400)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = b'data: {"x":1}\n\ndata: [DONE]\n\n'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = _serve(H)
    cli = cc.Client(f"http://127.0.0.1:{srv.server_port}", None, timeout=5)
    status, lines = cli.post_stream("/s", {"a": 1})
    assert status == 200 and "data: [DONE]" in lines
    assert cli.post("/bad", {"a": 1}) == (400, '{"error":"nope"}')
    srv.shutdown()


def test_nothing_listening_is_status_zero_not_an_exception():
    srv = _serve(BaseHTTPRequestHandler)
    port = srv.server_port
    srv.shutdown()
    srv.server_close()
    status, body = cc.Client(f"127.0.0.1:{port}", None, timeout=2).get("/v1/models")
    assert status == 0 and body


# ---------------------------------------------------------------- transport policy
# contract.Transport is shared with external_checks.Endpoint. A scripted network
# (`opener`) stands in for the socket so each failure shape can be produced exactly:
# before the connection is up (the request provably never left) or after it.

import socket
import urllib.error


class _Resp(io.BytesIO):
    def __init__(self, status, body=b"{}"):
        super().__init__(body)
        self.status = status


class _Net:
    """Each request takes the next scripted outcome:
    ("answer", status) | ("before", exc) | ("after", exc) | ("lost", status)."""

    def __init__(self, *script):
        self.script, self.sent = list(script), []

    def __call__(self, req, timeout, phase):
        self.sent.append((req.get_method(), req.full_url, req.get_header("Authorization"), timeout))
        kind, what = self.script.pop(0)
        if kind == "before":
            raise what
        phase.connected = True
        if kind == "after":
            raise what
        if kind == "lost":                         # status line came, body did not
            class Lost(_Resp):
                def read(self, *a):
                    raise ConnectionResetError(104, "Connection reset by peer")
            return Lost(what)
        if what >= 400:
            raise urllib.error.HTTPError(req.full_url, what, "err", {}, io.BytesIO(b'{"e": 1}'))
        return _Resp(what, b'{"ok": true}')


def _not_tls():
    return urllib.error.URLError(ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number"))


def _client(net, **kw):
    return cc.Client("203.0.113.7:40000", "tok", timeout=120, opener=net, **kw)


def _schemes(net):
    return [url.split(":", 1)[0] for _, url, _, _ in net.sent]


def test_a_not_tls_failure_falls_back_to_http_and_pins_it():
    net = _Net(("before", _not_tls()), ("answer", 200), ("answer", 200))
    cli = _client(net)
    assert cli.get("/v1/models")[0] == 200
    assert cli.get("/v1/models")[0] == 200
    assert _schemes(net) == ["https", "http", "http"]
    assert cli.bases == ["http://203.0.113.7:40000"]


@pytest.mark.parametrize("exc", [
    urllib.error.URLError(TimeoutError("_ssl.c:983: The handshake operation timed out")),
    urllib.error.URLError(TimeoutError("timed out")),
    urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
    urllib.error.URLError(ssl.SSLEOFError(8, "EOF occurred in violation of protocol")),
    urllib.error.URLError(socket.gaierror(-2, "Name or service not known")),
], ids=["handshake-timeout", "connect-timeout", "refused", "tls-eof", "dns"])
def test_a_failure_that_is_not_a_tls_mismatch_never_tries_http(exc):
    """A timeout or a refused connection says nothing about the scheme; answering it
    with http would send the token in the clear to what may be a TLS port."""
    net = _Net(("before", exc))
    status, body = _client(net).get("/v1/models")
    assert status == 0 and body
    assert _schemes(net) == ["https"]


@pytest.mark.parametrize("exc", [TimeoutError("timed out"),
                                 ConnectionResetError(104, "Connection reset by peer")],
                         ids=["read-timeout", "reset-after-send"])
def test_a_failure_after_connecting_never_tries_http(exc):
    net = _Net(("after", exc))
    assert _client(net).get("/v1/models")[0] == 0
    assert _schemes(net) == ["https"]


@pytest.mark.parametrize("status", [401, 404, 500, 503])
def test_an_http_error_status_over_https_is_an_answer_and_pins_https(status):
    net = _Net(("answer", status))
    cli = _client(net)
    assert cli.get("/v1/models")[0] == status
    assert _schemes(net) == ["https"] and cli.bases == ["https://203.0.113.7:40000"]


def test_the_token_never_goes_over_http_once_https_has_answered():
    """The invariant, stated directly: after any https response, every later request
    from that object is https — even if a later failure looks like a TLS mismatch."""
    net = _Net(("answer", 401), ("before", _not_tls()),
               ("before", urllib.error.URLError(ConnectionRefusedError(111, "refused"))),
               ("after", TimeoutError("timed out")), ("answer", 200))
    cli = _client(net)
    for _ in range(5):
        cli.post("/v1/chat/completions", {"x": 1})
    assert _schemes(net) == ["https"] * 5
    assert all(auth == "Bearer tok" for _, _, auth, _ in net.sent)


def test_a_failed_attempt_pins_nothing():
    """https refused once (an instance still starting) must not stop the scheme probe
    on the next request; and http that failed after a TLS mismatch is not pinned."""
    refused = urllib.error.URLError(ConnectionRefusedError(111, "refused"))
    net = _Net(("before", refused),
               ("before", _not_tls()), ("before", refused),
               ("before", _not_tls()), ("answer", 200))
    cli = _client(net)
    assert cli.get("/v1/models")[0] == 0
    assert cli.get("/v1/models")[0] == 0
    assert len(cli.bases) == 2
    assert cli.get("/v1/models")[0] == 200
    assert _schemes(net) == ["https", "https", "http", "https", "http"]
    assert cli.bases == ["http://203.0.113.7:40000"]


@pytest.mark.parametrize("exc", [
    urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
    urllib.error.URLError(socket.gaierror(-2, "Name or service not known")),
    urllib.error.URLError(TimeoutError("timed out")),
], ids=["refused", "dns", "connect-timeout"])
def test_a_request_that_never_left_is_retried(exc):
    slept = []
    net = _Net(("before", exc), ("before", exc), ("answer", 200))
    cli = _client(net, retries=2, sleep=slept.append)
    assert cli.post("/v1/chat/completions", {"x": 1})[0] == 200
    assert len(net.sent) == 3 and slept == [2, 4]


@pytest.mark.parametrize("exc", [TimeoutError("timed out"),
                                 ConnectionResetError(104, "Connection reset by peer"),
                                 urllib.error.URLError(BrokenPipeError(32, "Broken pipe"))],
                         ids=["read-timeout", "reset-after-send", "send-failed"])
def test_a_post_that_may_have_been_delivered_is_never_resent(exc):
    """Generation is not idempotent and is expensive: once the connection was up the
    request may have been processed, so it is reported, not re-sent."""
    net = _Net(("after", exc), ("answer", 200), ("answer", 200))
    cli = _client(net, retries=2, sleep=lambda s: None)
    status, body = cli.post("/v1/chat/completions", {"x": 1})
    assert status == 0 and body
    assert len(net.sent) == 1


def test_a_response_whose_body_is_lost_is_status_zero_not_an_exception():
    net = _Net(("lost", 200))
    status, body = _client(net).post("/v1/completions", {"x": 1})
    assert status == 0 and "reading the response" in body


class _Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def test_each_attempt_timeout_is_capped_by_the_deadline():
    clock = _Clock()
    net = _Net(("answer", 200))
    cli = _client(net, deadline=cc.Deadline(30, clock))
    clock.t = 20
    cli.get("/v1/models")
    assert net.sent[0][3] == pytest.approx(10)


def test_no_request_starts_once_the_deadline_has_passed():
    clock = _Clock()
    net = _Net()
    cli = _client(net, deadline=cc.Deadline(30, clock))
    clock.t = 31
    status, body = cli.post("/v1/chat/completions", {"x": 1})
    assert status == 0 and cc.DEADLINE_EXCEEDED in body and net.sent == []


def test_a_retry_never_sleeps_past_the_deadline():
    clock = _Clock()
    refused = urllib.error.URLError(ConnectionRefusedError(111, "refused"))
    net = _Net(("before", refused), ("before", refused))
    slept = []
    cli = _client(net, retries=5, sleep=slept.append, deadline=cc.Deadline(1, clock))
    status, body = cli.get("/v1/models")
    assert status == 0 and cc.DEADLINE_EXCEEDED in body
    assert slept == [] and len(net.sent) == 1


def test_not_tls_recognises_the_real_openssl_error():
    """The shape urllib raises against a plain-http port, reproduced over a socket."""
    srv = _serve(BaseHTTPRequestHandler)
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    try:
        import urllib.request
        urllib.request.urlopen(f"https://127.0.0.1:{srv.server_port}/", timeout=5, context=ctx)
    except Exception as e:                                        # noqa: BLE001
        err = e
    srv.shutdown()
    assert cc.not_tls(err), repr(err)
    assert not cc.not_tls(urllib.error.URLError(TimeoutError("timed out")))
    assert not cc.not_tls(urllib.error.HTTPError("u", 400, "bad", {}, None))


# ---------------------------------------------------------------- redirects

import shutil
import subprocess
import urllib.request


def _redirecting(routes, seen):
    """A handler: routes maps path -> ("redirect", location) | ("ok", body)."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append((self.path, self.headers.get("Authorization")))
            kind, value = routes.get(self.path, ("ok", b"{}"))
            if kind == "redirect":
                self.send_response(302)
                self.send_header("Location", value)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(value)))
            self.end_headers()
            self.wfile.write(value)

        def log_message(self, *a):
            pass
    return H


def test_a_same_origin_redirect_is_still_followed():
    """Jupyter's contents API may redirect on a trailing slash: same host, same port."""
    seen = []
    srv = _serve(_redirecting({"/a": ("redirect", "/b"), "/b": ("ok", b'{"ok": 1}')}, seen))
    status, body = cc.Client(f"http://127.0.0.1:{srv.server_port}", "tok", timeout=5).get("/a")
    srv.shutdown()
    assert status == 200 and json.loads(body) == {"ok": 1}
    assert seen == [("/a", "Bearer tok"), ("/b", "Bearer tok")]


@pytest.mark.parametrize("target", ["port", "host"])
def test_a_redirect_to_another_host_or_port_is_refused_and_returned_as_its_status(target):
    seen_a, seen_b = [], []
    routes = {}
    other = _serve(_redirecting({}, seen_b))
    srv = _serve(_redirecting(routes, seen_a))
    # Another port on the same host, or the same port under another host name.
    routes["/a"] = ("redirect", f"http://127.0.0.1:{other.server_port}/x" if target == "port"
                    else f"http://localhost:{srv.server_port}/x")
    cli = cc.Client(f"http://127.0.0.1:{srv.server_port}", "tok", timeout=5)
    status, _ = cli.get("/a")
    srv.shutdown()
    other.shutdown()
    assert status == 302
    assert seen_a == [("/a", "Bearer tok")] and seen_b == []
    assert cli.bases == [f"http://127.0.0.1:{srv.server_port}"]   # an answer: pinned


def test_the_redirect_rule_refuses_https_to_http_even_on_the_same_host_and_port():
    handler = cc._SameOriginRedirects()
    req = urllib.request.Request("https://203.0.113.7:40000/v1/models",
                                 headers={"Authorization": "Bearer tok"})
    assert handler.redirect_request(req, None, 302, "Found", {},
                                    "http://203.0.113.7:40000/v1/models") is None
    assert handler.redirect_request(req, None, 302, "Found", {},
                                    "https://203.0.113.7:40001/v1/models") is None
    assert handler.redirect_request(req, None, 302, "Found", {},
                                    "https://203.0.113.7:40000/v1/models/") is not None


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl to make a test cert")
def test_an_https_redirect_to_http_is_refused_and_nothing_reaches_the_http_target(tmp_path):
    """Over a real TLS socket: https answers with a redirect into the clear. The 3xx
    is the answer (https pinned) and the plain-http target never sees the token."""
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=127.0.0.1", "-keyout", str(key), "-out", str(cert)],
                   check=True, capture_output=True)
    seen_http, seen_https = [], []
    plain = _serve(_redirecting({}, seen_http))
    tls = HTTPServer(("127.0.0.1", 0), _redirecting(
        {"/v1/models": ("redirect", f"http://127.0.0.1:{plain.server_port}/v1/models")}, seen_https))
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(cert, key)
    tls.socket = sctx.wrap_socket(tls.socket, server_side=True)
    threading.Thread(target=tls.serve_forever, daemon=True).start()
    cli = cc.Client(f"127.0.0.1:{tls.server_port}", "tok", timeout=5)
    status, _ = cli.get("/v1/models")
    tls.shutdown()
    plain.shutdown()
    assert status == 302 and seen_https == [("/v1/models", "Bearer tok")]
    assert seen_http == []
    assert cli.bases == [f"https://127.0.0.1:{tls.server_port}"]


# ---------------------------------------------------------------- no response is an error

def test_completions_route_with_no_response_is_an_error_not_a_violation():
    code, text = run(FakeClient(completions_status=0))
    assert any("completions-route" in f for f in findings(text, "ERROR")), text
    assert not any("completions-route" in f for f in findings(text, "VIOLATION")), text


def test_streaming_with_no_response_is_an_error_not_a_violation():
    code, text = run(FakeClient(stream_status=0, stream_lines=["TimeoutError: timed out"]))
    assert any("streaming" in f for f in findings(text, "ERROR")), text
    assert not any("streaming" in f for f in findings(text, "VIOLATION")), text


@pytest.mark.parametrize("declared", ["", "tools"])
def test_a_tools_probe_with_no_response_is_an_error_declared_or_not(declared):
    c = FakeClient(tools_status=0)
    code, text = run(c, args=ARGS + " --enable-auto-tool-choice", expect_caps=declared)
    assert any("cap:tools" in f for f in findings(text, "ERROR")), text
    assert not any("cap:tools" in f for f in findings(text, "VIOLATION") + findings(text, "ADVISORY"))
    assert not any("cap:tools" in ln for ln in text.splitlines() if " ok " in ln)


@pytest.mark.parametrize("declared", ["", "structured-output"])
def test_a_structured_output_probe_with_no_response_is_an_error_declared_or_not(declared):
    code, text = run(FakeClient(structured_status=0), expect_caps=declared)
    assert any("cap:structured-output" in f for f in findings(text, "ERROR")), text
    assert not any("cap:structured-output" in f
                   for f in findings(text, "VIOLATION") + findings(text, "ADVISORY") + findings(text, "NA"))


# ---------------------------------------------------------------- counting on the box

class _Session:
    def __init__(self, answer, ok=True):
        self.answer, self.ok, self.scripts = answer, ok, []

    def run(self, script, python="python3"):
        self.scripts.append((script, python))
        return self.ok, self.answer


def _roundtrip(counter, prompt_tokens=7, args=ARGS):
    out = io.StringIO()
    rep = cc.Report(out)
    import argparse as _ap
    cc.check_prompt_roundtrip(rep, _ap.Namespace(model=MODEL), cc.shlex.split(args),
                              {"prompt_tokens": prompt_tokens}, counter)
    return out.getvalue()


def test_a_matching_count_from_the_box_passes():
    s = _Session(json.dumps({"encoded": {"input_ids": list(range(7))}}) + "\n")
    text = _roundtrip(cc.remote_counter(s))
    assert " ok " in text and "prompt-roundtrip" in text, text
    script, python = s.scripts[0]
    assert MODEL in script and "apply_chat_template" in script
    assert python == cc.REMOTE_PYTHON


def test_the_bos_double_add_is_caught_from_the_box():
    s = _Session(json.dumps({"encoded": list(range(6))}))
    assert any("BOS double-add" in ln for ln in findings(_roundtrip(cc.remote_counter(s)), "VIOLATION"))


def test_the_engines_own_tokenizer_flags_reach_the_box():
    s = _Session(json.dumps({"encoded": list(range(7))}))
    _roundtrip(cc.remote_counter(s), args=ARGS + " --tokenizer org/tok --revision abc "
                                              "--download-dir /workspace/models --trust-remote-code")
    cfg = s.scripts[0][0]
    for needle in ("org/tok", "abc", "/workspace/models", "trust_remote_code"):
        assert needle in cfg, needle


def test_an_unreachable_box_is_an_error_never_a_pass_or_na():
    """The check was runnable — a session was configured — and could not be run."""
    text = _roundtrip(cc.remote_counter(_Session("SSH not accepted", ok=False)))
    assert any("prompt-roundtrip" in f and "could not count on the box" in f
               for f in findings(text, "ERROR")), text


def test_a_box_without_transformers_is_na():
    text = _roundtrip(cc.remote_counter(_Session('{"na": "no python with transformers on the box"}')))
    assert any("no python with transformers" in f for f in findings(text, "NA")), text


def test_an_unreadable_answer_is_an_error():
    text = _roundtrip(cc.remote_counter(_Session("garbage")))
    assert any("unreadable answer" in f for f in findings(text, "ERROR")), text


def test_a_session_that_raises_is_an_error_never_a_pass():
    class Raising(_Session):
        def run(self, script, python="python3"):
            raise OSError("ssh binary vanished")
    text = _roundtrip(cc.remote_counter(Raising("")))
    assert any("prompt-roundtrip" in f and "OSError" in f for f in findings(text, "ERROR")), text


def test_without_a_box_the_check_is_na_and_no_tokenizer_is_loaded_here(monkeypatch):
    """The client must never load a model repo's tokenizer: that executes the repo's
    code (trust_remote_code) on our machine. No counter means not applicable."""
    import sys
    import types
    loaded = []
    fake = types.ModuleType("transformers")

    class AutoTokenizer:
        @staticmethod
        def from_pretrained(ref, **kw):
            loaded.append((ref, kw))
            raise AssertionError("a tokenizer was loaded on the client")
    fake.AutoTokenizer = AutoTokenizer
    monkeypatch.setitem(sys.modules, "transformers", fake)
    text = _roundtrip(None, args=ARGS + " --trust-remote-code")
    assert loaded == []
    assert any("prompt-roundtrip" in f and "only ever run on the instance" in f
               for f in findings(text, "NA")), text


# ---------------------------------------------------------------- the script that runs on the box

import sys as _sys

_STUB_TRANSFORMERS = '''
import json, os
MODE = os.environ.get("STUB_MODE", "list")


class _Encoding(dict):
    """Newer transformers: a BatchEncoding with .input_ids and two keys."""
    def __init__(self, ids):
        super().__init__(input_ids=ids, attention_mask=[1] * len(ids))
        self.input_ids = ids


class _Tok:
    chat_template = None if MODE == "no-template" else "{{ messages }}"

    def apply_chat_template(self, messages, add_generation_prompt, tokenize):
        assert add_generation_prompt is True and tokenize is True
        assert messages == [{"role": "user", "content": "contract probe"}]
        ids = list(range(7))
        return _Encoding(ids) if MODE == "encoding" else ids


class AutoTokenizer:
    @staticmethod
    def from_pretrained(ref, **kwargs):
        with open(os.environ["STUB_RECORD"], "w") as f:
            json.dump({"ref": ref, "kwargs": kwargs}, f)
        if MODE == "load-fails":
            raise OSError("no such repo")
        return _Tok()
'''


class _LocalBox:
    """A session that runs the remote script HERE, with a stub transformers, the way
    remote.RemoteSession runs it on the box: the script on stdin to `python -`."""

    def __init__(self, tmp_path, mode):
        (tmp_path / "transformers.py").write_text(_STUB_TRANSFORMERS)
        self.env = {"PYTHONPATH": str(tmp_path), "STUB_MODE": mode,
                    "STUB_RECORD": str(tmp_path / "record.json"), "PATH": "/usr/bin:/bin"}
        self.record = tmp_path / "record.json"
        self.cwd = tmp_path

    def run(self, script, python="python3"):
        r = subprocess.run([_sys.executable, "-"], input=script, capture_output=True,
                           text=True, env=self.env, cwd=self.cwd, timeout=60)
        return r.returncode == 0, r.stdout if r.returncode == 0 else r.stderr


@pytest.mark.parametrize("mode", ["list", "encoding"])
def test_the_remote_script_really_counts_and_its_answer_is_read(tmp_path, mode):
    box = _LocalBox(tmp_path, mode)
    text = _roundtrip(cc.remote_counter(box), prompt_tokens=7,
                      args=ARGS + " --tokenizer org/tok --revision abc --trust-remote-code")
    assert any("prompt-roundtrip" in ln for ln in text.splitlines() if " ok " in ln), text
    seen = json.loads(box.record.read_text())
    assert seen == {"ref": "org/tok", "kwargs": {"revision": "abc", "trust_remote_code": True}}


def test_the_remote_script_reports_a_double_bos_it_really_counted(tmp_path):
    text = _roundtrip(cc.remote_counter(_LocalBox(tmp_path, "list")), prompt_tokens=8)
    assert any("BOS double-add" in f for f in findings(text, "VIOLATION")), text


@pytest.mark.parametrize("mode,why", [("no-template", "declares no chat template"),
                                      ("load-fails", "could not load the tokenizer on the box")])
def test_the_remote_script_says_when_the_check_does_not_apply(tmp_path, mode, why):
    text = _roundtrip(cc.remote_counter(_LocalBox(tmp_path, mode)))
    assert any("prompt-roundtrip" in f and why in f for f in findings(text, "NA")), text


def test_the_remote_script_without_transformers_says_so(tmp_path):
    box = _LocalBox(tmp_path, "list")
    (tmp_path / "transformers.py").write_text("raise ImportError('not installed')\n")
    text = _roundtrip(cc.remote_counter(box))
    assert any("transformers not importable on the box" in f for f in findings(text, "NA")), text
