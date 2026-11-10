"""The launcher's external chat check (ADR 0059, D1 / D7 step 3.1).

The check sends inference through the instance's public endpoint instead of the
engine's internal port. It runs alongside the in-image serving test and is ADVISORY,
so what these pin is:

  1. It only runs when it can say something new: an in-image serving test ran,
     the template chats, and the engine API port is mapped.
  2. Its bar is structural and matches the in-image test's: HTTP 200, an assistant
     message, at least one completion token (ADR 0031: no content matching).
  3. A broken endpoint FAILS it rather than passing quietly.

The chat check drives a real HTTP server, so the socket and auth path are exercised
rather than a mocked call.
"""

from __future__ import annotations

import io
import json
import sys
import os
import math
import struct
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import external_checks as ec


@pytest.fixture(autouse=True)
def _no_contract(request, monkeypatch):
    """These tests are about the capability checks; the contract (contract.py) has
    its own tests and is exercised here only by the tests marked `contract`."""
    if "contract" not in request.keywords:
        monkeypatch.setattr(ec, "check_contract", lambda *a, **k: [])

SERVING_PASSED = [{"name": "base/10-supervisor", "state": "passed"},
                  {"name": "vllm.d/10-vllm-serving", "state": "passed"}]
PORTAL = ("localhost:1111:11111:/:Instance Portal|localhost:7860:17860:/:Model UI|"
          "localhost:8000:18000:/docs:vLLM API|localhost:8080:18080:/:Jupyter")


# ---------------------------------------------------------------- helpers

def test_serving_test_ran_matches_each_engine_suite():
    for name in ("vllm.d/10-vllm-serving", "sglang.d/10-sglang-serving",
                 "llama.d/10-llama-serving", "vllm-omni.d/10-vllm-omni-serving"):
        assert ec.serving_test_ran([{"name": name, "state": "passed"}]) == name


def test_a_failed_serving_test_still_counts_as_ran():
    """Observed live: 10-vllm-serving failed on Ray dashboard log lines while vLLM
    answered 3/3. The external check must still run, or it hides a working engine
    behind an unrelated failure — the bundling it exists to undo."""
    assert ec.serving_test_ran([{"name": "vllm.d/10-vllm-serving", "state": "failed"}])


def test_a_skipped_or_absent_serving_test_does_not_count():
    assert ec.serving_test_ran([{"name": "vllm.d/10-vllm-serving", "state": "skipped"}]) is None
    assert ec.serving_test_ran([{"name": "base/10-supervisor", "state": "passed"}]) is None
    assert ec.serving_test_ran([{"name": "vllm.d/12-vllm-contract", "state": "passed"}]) is None


def test_public_api_port_reads_the_entry_for_the_engine_internal_port():
    assert ec.public_api_port(PORTAL) == 8000
    assert ec.public_api_port("localhost:9000:18000:/:API") == 9000


def test_public_api_port_defaults_when_portal_config_is_absent():
    assert ec.public_api_port(None) == ec.DEFAULT_PUBLIC_API_PORT
    assert ec.public_api_port("localhost:1111:11111:/:Instance Portal") == ec.DEFAULT_PUBLIC_API_PORT


def test_env_value_handles_quoted_and_bare_values():
    env = '-p 8000:8000 -e VLLM_MODEL=Qwen/Qwen3-0.6B -e VLLM_ARGS="--max-model-len 4096 --port 18000"'
    assert ec._env_value(env, "VLLM_MODEL") == "Qwen/Qwen3-0.6B"
    assert ec._env_value(env, "VLLM_ARGS") == "--max-model-len 4096 --port 18000"
    assert ec._env_value(env, "MISSING") is None


def test_chat_disabled_follows_the_template_and_the_override():
    assert ec.chat_disabled("-e VLLM_TEST_ENDPOINT=none")
    assert not ec.chat_disabled("-e VLLM_MODEL=x")
    assert ec.chat_disabled("-e VLLM_MODEL=x", ["VLLM_TEST_ENDPOINT=none"])
    assert not ec.chat_disabled("-e VLLM_TEST_ENDPOINT=none", ["VLLM_TEST_ENDPOINT=chat"])


# ---------------------------------------------------------------- when it runs

class _Unused:
    def __init__(self, *a, **k):
        raise AssertionError("no endpoint should be built when the check is skipped")


def _checks(out):
    """The results after the leading external/capabilities line(s)."""
    return [r for r in out if r["name"] != "external/capabilities"]


def _instance(port=40000):
    return {"public_ipaddr": "203.0.113.7 ", "ports": {"8000/tcp": [{"HostPort": str(port)}]}}


def test_skipped_when_the_images_serving_test_did_not_run():
    out = ec.run_external_checks(_instance(), {"image": "vastai/vllm", "env": ""},
                                 [{"name": "base/10-supervisor", "state": "passed"},
                                  {"name": "vllm.d/10-vllm-serving", "state": "skipped"}],
                                 endpoint_factory=_Unused)
    assert _checks(out)[0]["state"] == "skipped" and _checks(out)[0]["advisory"] is True


def test_an_image_without_a_serving_test_waits_for_the_engine_itself():
    """Seen live: vastai/comfy:v0.18.2 has no comfyui.d suite at all, so its in-image
    run never generated anything. The external checks are then the only checks, and
    must wait for readiness themselves instead of skipping."""
    calls = {"n": 0}

    def models(body=None):
        calls["n"] += 1
        return (503, "loading") if calls["n"] < 3 else (200, {"data": [{"id": "m"}]})

    ep = _Scripted({"/v1/models": models, "/v1/chat/completions": _chat_ok_any})
    slept = []
    out = ec.run_external_checks(_instance(), {"image": "vastai/vllm", "env": ""}, [{"name": "base/10-supervisor",
                                                             "state": "passed"}],
                                 endpoint_factory=_factory(ep), capabilities={"chat"},
                                 sleep=slept.append)
    assert {r["name"]: r["state"] for r in out}[ec.CHAT_CHECK] == "passed"
    assert len(slept) == 2


def test_an_engine_that_never_becomes_ready_fails_rather_than_skips():
    ep = _Scripted({"/v1/models": (503, "loading")})
    out = ec.run_external_checks(_instance(), {"image": "vastai/vllm", "env": ""}, [], endpoint_factory=_factory(ep),
                                 capabilities={"chat"}, ready_timeout=30, sleep=lambda s: None)
    assert _checks(out)[0]["state"] == "failed" and "not ready after 30s" in _checks(out)[0]["detail"]


def test_comfyui_without_a_serving_test_waits_for_wrapper_and_payloads():
    state = {"n": 0}

    class Jup(_FakeJupyter):
        def list_dir(self, path):
            state["n"] += 1
            return (sorted(self.files), None) if state["n"] > 1 else ([], None)

    class Wrap(_Both):
        def call(self, path, body=None):
            if path == "/health":
                return 200, {"status": "ok"}
            return _Both.call(self, path, body)

    out = ec.run_external_checks(
        _comfy_instance(), {"image": "vastai/vllm", "env": f'-e PORTAL_CONFIG="{COMFY_PORTAL}"'},
        [{"name": "base/10-supervisor", "state": "passed"},
         {"name": "pytorch.d/10-torch", "state": "passed"}],
        token="tok", endpoint_factory=lambda h, p, t: Wrap({"t2i": _result(("9", _png()))}),
        jupyter_factory=lambda h, p, t: Jup(_payload("t2i", T2I)),
        capabilities={"comfyui-workflows"}, sleep=lambda s: None)
    names = {r["name"]: r["state"] for r in out}
    assert names["external/comfyui/t2i/SaveImage#9/1"] == "passed"


def test_vllm_test_endpoint_none_without_openai_routes_is_a_declaration_error():
    """The in-image suite was told not to chat but nothing says what the template does
    serve: the fix is to declare OPENAI_ROUTES, so that is what the run reports."""
    caps, source, problems = ec.resolve_capabilities({"image": "vastai/vllm", "env": "-e VLLM_TEST_ENDPOINT=none"})
    assert "OPENAI_ROUTES unset" in source
    assert problems and "declare the routes" in problems[0]


def test_skipped_when_the_api_port_is_not_mapped():
    out = ec.run_external_checks({"public_ipaddr": "203.0.113.7", "ports": {}}, {"image": "vastai/vllm", "env": ""},
                                 SERVING_PASSED, endpoint_factory=_Unused)
    assert _checks(out)[0]["state"] == "skipped"


def test_runs_against_the_mapped_public_port_with_the_token():
    seen = {}

    class Recording:
        def __init__(self, host, port, token):
            seen.update(host=host, port=port, token=token)

        def call(self, path, body=None):
            if path == "/v1/models":
                return 200, {"data": [{"id": "m"}]}
            if path == "/v1/completions":
                return 200, {"choices": [{"text": " Paris"}], "usage": {"completion_tokens": 2}}
            return 200, {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                         "usage": {"completion_tokens": 3}}

    out = ec.run_external_checks(_instance(41234), {"image": "vastai/vllm", "env": f'-e PORTAL_CONFIG="{PORTAL}"'},
                                 SERVING_PASSED, token="tok", endpoint_factory=Recording)
    assert seen == {"host": "203.0.113.7", "port": "41234", "token": "tok"}
    states = {r["name"]: r["state"] for r in _checks(out)}
    assert states == {ec.CHAT_CHECK: "passed", ec.COMPLETIONS_CHECK: "passed"}
    assert all(r["advisory"] for r in out)


# ---------------------------------------------------------------- the chat check itself

def _serve(routes, expect_token="tok"):
    """A real HTTP server: routes maps path -> (status, body) or a callable(body)."""
    seen = []

    class H(BaseHTTPRequestHandler):
        def _reply(self, status, body):
            data = body if isinstance(body, (bytes, str)) else json.dumps(body)
            data = data.encode() if isinstance(data, str) else data
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle(self, body):
            seen.append((self.command, self.path, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != f"Bearer {expect_token}":
                return self._reply(401, {"error": "unauthorized"})
            route = routes.get(self.path)
            if route is None:
                return self._reply(404, {"detail": "Not Found"})
            status, payload = route(body) if callable(route) else route
            self._reply(status, payload)

        def do_GET(self):  # noqa: N802
            self._handle(None)

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            self._handle(json.loads(self.rfile.read(n) or b"null"))

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


@pytest.fixture
def plain_http(monkeypatch):
    """The fake server speaks plain http; skip the https attempt and retry sleeps."""
    orig = ec.Endpoint.__init__

    def init(self, host, port, token, timeout=5):
        orig(self, host, port, token, timeout=timeout)
        self.bases = [f"http://{host}:{port}"]

    monkeypatch.setattr(ec.Endpoint, "__init__", init)
    monkeypatch.setattr(ec, "CONNECT_RETRIES", 0)


MODELS = (200, {"object": "list", "data": [{"id": "Qwen/Qwen3-0.6B"}]})


def _chat_ok(body):
    assert body["model"] == "Qwen/Qwen3-0.6B" and body["temperature"] == 0
    return 200, {"choices": [{"finish_reason": "stop",
                              "message": {"role": "assistant", "content": "Hello."}}],
                 "usage": {"prompt_tokens": 9, "completion_tokens": 3}}


def _chat_ok_any(body):
    return 200, {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                 "usage": {"completion_tokens": 2}}


def test_a_healthy_endpoint_passes(plain_http):
    srv, seen = _serve({"/v1/models": MODELS, "/v1/chat/completions": _chat_ok})
    r = ec.check_chat(ec.Endpoint("127.0.0.1", srv.server_port, "tok"))
    srv.shutdown()
    assert r["state"] == "passed", r
    assert all(auth == "Bearer tok" for _, _, auth in seen)
    assert sum(1 for m, p, _ in seen if p == "/v1/chat/completions") == len(ec.CHAT_PROMPTS)


def test_a_wrong_token_fails(plain_http):
    srv, _ = _serve({"/v1/models": MODELS, "/v1/chat/completions": _chat_ok})
    r = ec.check_chat(ec.Endpoint("127.0.0.1", srv.server_port, "wrong"))
    srv.shutdown()
    assert r["state"] == "failed" and "401" in r["detail"]


def test_an_engine_without_chat_fails(plain_http):
    """The embedding-model shape: models answers, chat is 404."""
    srv, _ = _serve({"/v1/models": MODELS})
    r = ec.check_chat(ec.Endpoint("127.0.0.1", srv.server_port, "tok"))
    srv.shutdown()
    assert r["state"] == "failed" and "404" in r["detail"]


def test_a_chat_template_error_fails(plain_http):
    """The Qwen3-ASR-on-SGLang shape: chat answers 400."""
    srv, _ = _serve({"/v1/models": MODELS, "/v1/chat/completions": (400, {
        "object": "error", "message": "tokenizer.chat_template is not set"})})
    r = ec.check_chat(ec.Endpoint("127.0.0.1", srv.server_port, "tok"))
    srv.shutdown()
    assert r["state"] == "failed" and "400" in r["detail"]


def test_zero_completion_tokens_fails(plain_http):
    srv, _ = _serve({"/v1/models": MODELS, "/v1/chat/completions": (200, {
        "choices": [{"message": {"role": "assistant", "content": ""}}],
        "usage": {"completion_tokens": 0}})})
    r = ec.check_chat(ec.Endpoint("127.0.0.1", srv.server_port, "tok"))
    srv.shutdown()
    assert r["state"] == "failed" and "completion tokens" in r["detail"]


def test_no_models_listed_fails(plain_http):
    srv, _ = _serve({"/v1/models": (200, {"object": "list", "data": []})})
    r = ec.check_chat(ec.Endpoint("127.0.0.1", srv.server_port, "tok"))
    srv.shutdown()
    assert r["state"] == "failed" and "/v1/models" in r["detail"]


def test_nothing_listening_is_an_error_without_hanging(plain_http):
    """No HTTP response means the check could not run: error, not a failed template."""
    srv, _ = _serve({})
    port = srv.server_port
    srv.shutdown()
    srv.server_close()
    r = ec.check_chat(ec.Endpoint("127.0.0.1", port, "tok"))
    assert r["state"] == "error" and "no HTTP response" in r["detail"]


# ---------------------------------------------------------------- capabilities

def test_parse_capabilities():
    assert ec.parse_capabilities(None) == (None, [])
    assert ec.parse_capabilities("Chat, embeddings") == ({"chat", "embeddings"}, [])
    assert ec.parse_capabilities("chat,vison") == ({"chat", "vison"}, ["vison"])


class _Scripted:
    """An endpoint answering by path, recording calls."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def call(self, path, body=None):
        self.calls.append(path)
        route = self.routes.get(path, (404, {"detail": "Not Found"}))
        return route(body) if callable(route) else route


def _factory(ep):
    return lambda host, port, token: ep


def _vec(*xs):
    return {"embedding": list(xs)}


def _embed_ok(body):
    table = {ec.EMBED_TEXTS[0]: _vec(1.0, 0.1, 0.0), ec.EMBED_TEXTS[1]: _vec(0.9, 0.2, 0.1),
             ec.EMBED_TEXTS[2]: _vec(0.0, 0.1, 1.0)}
    data = [dict(table[t], index=i) for i, t in enumerate(body["input"])]
    return 200, {"object": "list", "data": data}


EMBED_ROUTES = {"/v1/models": (200, {"data": [{"id": "google/embeddinggemma-2"}]}),
                "/v1/embeddings": _embed_ok}


def _run(routes, caps):
    ep = _Scripted(routes)
    out = ec.run_external_checks(_instance(), {"image": "vastai/vllm", "env": ""}, SERVING_PASSED,
                                 endpoint_factory=_factory(ep), capabilities=caps)
    return {r["name"]: r for r in out}, ep


def test_an_embeddings_template_passes_and_chat_is_checked_as_refused():
    out, ep = _run(EMBED_ROUTES, {"embeddings"})
    assert out[ec.EMBEDDINGS_CHECK]["state"] == "passed"
    assert out[ec.EMBEDDINGS_CHECK]["kind"] == "structural"
    assert out[ec.EMBEDDINGS_ANSWER_CHECK]["state"] == "passed"
    assert out[ec.EMBEDDINGS_ANSWER_CHECK]["kind"] == "answer"
    assert out[ec.UNDECLARED_CHAT_CHECK]["state"] == "passed"     # 404 from the fake
    assert ec.CHAT_CHECK not in out
    assert all(r["advisory"] for r in out.values())


def test_capabilities_override_vllm_test_endpoint_none():
    """With a declaration, VLLM_TEST_ENDPOINT=none no longer turns the checks off."""
    ep = _Scripted(EMBED_ROUTES)
    out = ec.run_external_checks(_instance(), {"image": "vastai/vllm", "env": "-e VLLM_TEST_ENDPOINT=none"},
                                 SERVING_PASSED, endpoint_factory=_factory(ep),
                                 capabilities={"embeddings"})
    assert {r["name"] for r in out} >= {ec.EMBEDDINGS_CHECK, ec.UNDECLARED_CHAT_CHECK}


def test_chat_answered_when_not_declared_fails():
    """The both-ways rule: an engine that chats for an undeclared model fails."""
    routes = dict(EMBED_ROUTES)
    routes["/v1/chat/completions"] = (200, {"choices": [{"message": {"role": "assistant"}}]})
    out, _ = _run(routes, {"embeddings"})
    assert out[ec.UNDECLARED_CHAT_CHECK]["state"] == "failed"


def test_a_5xx_on_undeclared_chat_is_an_error_not_a_refusal():
    """A 5xx is the server breaking, which says nothing about whether chat is served."""
    routes = dict(EMBED_ROUTES)
    routes["/v1/chat/completions"] = (500, "boom")
    out, _ = _run(routes, {"embeddings"})
    assert out[ec.UNDECLARED_CHAT_CHECK]["state"] == "error"


def test_an_unknown_capability_is_an_error_not_a_silent_skip():
    out, _ = _run(EMBED_ROUTES, {"embeddings", "vison"})
    assert out["external/capabilities"]["state"] == "error"
    assert out[ec.EMBEDDINGS_CHECK]["state"] == "passed"


@pytest.mark.parametrize("bad,why", [
    ((404, {"detail": "Not Found"}), "HTTP 404"),
    ((200, {"data": [{"embedding": [1.0, 0.0]}]}), "expected 3"),
    ((200, {"data": [_vec(1.0, 0.0), _vec(1.0, 0.0), _vec(0.0, 0.0)]}), "all zeros"),
    ((200, {"data": [_vec(1.0, 0.0), _vec(1.0, 0.0, 1.0), _vec(0.1, 0.2)]}), "different dimensions"),
    ((200, {"data": [_vec(1.0, float("nan")), _vec(1.0, 0.0), _vec(0.1, 0.2)]}), "NaN"),
    ((200, {"data": [{"embedding": "abc"}, _vec(1.0), _vec(0.1)]}), "list of numbers"),
])
def test_broken_embeddings_fail_the_structural_check(bad, why):
    routes = dict(EMBED_ROUTES)
    routes["/v1/embeddings"] = bad
    out, _ = _run(routes, {"embeddings"})
    assert out[ec.EMBEDDINGS_CHECK]["state"] == "failed"
    assert why in out[ec.EMBEDDINGS_CHECK]["detail"]
    assert out[ec.EMBEDDINGS_ANSWER_CHECK]["state"] == "skipped"


def test_non_deterministic_embeddings_fail():
    calls = {"n": 0}

    def drifting(body):
        calls["n"] += 1
        if calls["n"] == 1:
            return _embed_ok(body)
        return 200, {"data": [dict(_vec(0.0, 1.0, 0.0), index=0)]}

    routes = dict(EMBED_ROUTES)
    routes["/v1/embeddings"] = drifting
    out, _ = _run(routes, {"embeddings"})
    assert out[ec.EMBEDDINGS_CHECK]["state"] == "failed"
    assert "embedded differently" in out[ec.EMBEDDINGS_CHECK]["detail"]


def test_a_paraphrase_that_ranks_below_noise_fails_the_answer_check_only():
    def inverted(body):
        table = {ec.EMBED_TEXTS[0]: _vec(1.0, 0.0, 0.0), ec.EMBED_TEXTS[1]: _vec(0.0, 0.0, 1.0),
                 ec.EMBED_TEXTS[2]: _vec(0.9, 0.1, 0.0)}
        return 200, {"data": [dict(table[t], index=i) for i, t in enumerate(body["input"])]}

    routes = dict(EMBED_ROUTES)
    routes["/v1/embeddings"] = inverted
    out, _ = _run(routes, {"embeddings"})
    assert out[ec.EMBEDDINGS_CHECK]["state"] == "passed"
    assert out[ec.EMBEDDINGS_ANSWER_CHECK]["state"] == "failed"


def test_a_chat_template_with_capabilities_runs_chat_not_refusal():
    routes = {"/v1/models": MODELS, "/v1/chat/completions": _chat_ok}
    out, _ = _run(routes, {"chat"})
    assert out[ec.CHAT_CHECK]["state"] == "passed" and ec.UNDECLARED_CHAT_CHECK not in out


# ---------------------------------------------------------------- ASR

def test_word_error_rate_normalises_digits_case_and_punctuation():
    ref = ec.ASR_REFERENCE
    assert ec.word_error_rate(ref, "hello from vast, the secret number is 7319") == 0.0
    assert ec.word_error_rate(ref, ref.upper()) == 0.0
    assert 0 < ec.word_error_rate(ref, "hello from bast the secret number is 7319") <= ec.ASR_MAX_WER
    assert ec.word_error_rate(ref, "thanks for watching") > ec.ASR_MAX_WER


def test_no_shell_wrappers_with_local_paths_are_shipped_with_the_probes():
    """The imported .sh wrappers only held one machine's paths; they are gone and
    nothing may bring them back."""
    probes = os.path.join(os.path.dirname(ec.__file__), "probes")
    assert [f for f in os.listdir(probes) if f.endswith(".sh")] == []
    assert any(f.endswith("_probe.py") for f in os.listdir(probes))


def test_the_probes_find_the_launcher_relative_to_themselves():
    """No probe may carry one machine's path: the launcher modules are found as the
    parent directory of probes/."""
    import re as _re
    probes = os.path.join(os.path.dirname(ec.__file__), "probes")
    for name in sorted(f for f in os.listdir(probes) if f.endswith(".py")):
        text = open(os.path.join(probes, name)).read()
        assert not _re.search(r"/home/|/Users/|/tmp/", text), name
        assert "os.path.dirname(os.path.dirname(os.path.abspath(__file__)))" in text, name
    assert os.path.exists(os.path.join(os.path.dirname(probes), "test_template.py"))


def test_the_committed_fixture_is_a_wav():
    with open(ec.ASR_FIXTURE, "rb") as f:
        head = f.read(12)
    assert head[:4] == b"RIFF" and head[8:12] == b"WAVE"


def _asr_routes(status=200, payload=None, seen=None):
    def transcribe(_):
        return status, payload if payload is not None else {"text": ec.ASR_REFERENCE}

    class Ep(_Scripted):
        def call_raw(self, path, data, content_type):
            if seen is not None:
                seen.update(path=path, ctype=content_type, data=data)
            self.calls.append(path)
            return transcribe(data)

    return Ep({"/v1/models": (200, {"data": [{"id": "Qwen/Qwen3-ASR-1.7B"}]})})


def _run_ep(ep, caps):
    out = ec.run_external_checks(_instance(), {"image": "vastai/vllm", "env": ""}, SERVING_PASSED,
                                 endpoint_factory=_factory(ep), capabilities=caps)
    return {r["name"]: r for r in out}


def test_a_correct_transcript_passes_both_checks_and_sends_multipart_wav():
    seen = {}
    out = _run_ep(_asr_routes(seen=seen), {"transcriptions", "chat"})
    assert out[ec.ASR_CHECK]["state"] == "passed"
    assert out[ec.ASR_ANSWER_CHECK]["state"] == "passed"
    assert seen["path"] == "/v1/audio/transcriptions"
    assert seen["ctype"].startswith("multipart/form-data; boundary=")
    assert b'name="model"' in seen["data"] and b"Qwen/Qwen3-ASR-1.7B" in seen["data"]
    assert b'filename="probe.wav"' in seen["data"] and b"RIFF" in seen["data"]


def test_a_wrong_transcript_fails_only_the_answer_check():
    out = _run_ep(_asr_routes(payload={"text": "Thanks for watching!"}), {"transcriptions"})
    assert out[ec.ASR_CHECK]["state"] == "passed"
    assert out[ec.ASR_ANSWER_CHECK]["state"] == "failed"


@pytest.mark.parametrize("status,payload,why", [
    (404, {"detail": "Not Found"}, "HTTP 404"),
    (400, {"message": "unsupported audio"}, "HTTP 400"),
    (200, {"text": "   "}, "no transcript"),
    (200, {"segments": []}, "no transcript"),
])
def test_a_broken_transcription_endpoint_fails_the_structural_check(status, payload, why):
    out = _run_ep(_asr_routes(status, payload), {"transcriptions"})
    assert out[ec.ASR_CHECK]["state"] == "failed" and why in out[ec.ASR_CHECK]["detail"]
    assert out[ec.ASR_ANSWER_CHECK]["state"] == "skipped"


def test_asr_only_template_must_refuse_chat():
    """The SGLang Qwen3-ASR shape: no chat template, chat answers 400 — correct when
    chat is not declared."""
    ep = _asr_routes()
    ep.routes["/v1/chat/completions"] = (400, {"message": "tokenizer.chat_template is not set"})
    out = _run_ep(ep, {"transcriptions"})
    assert out[ec.UNDECLARED_CHAT_CHECK]["state"] == "passed"


def test_multipart_upload_reaches_a_real_server(plain_http):
    """The raw-body path over a real socket, not just the scripted endpoint."""
    got = {}

    def transcribe(body):
        return 200, {"text": ec.ASR_REFERENCE}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            n = int(self.headers["Content-Length"])
            got["ctype"] = self.headers["Content-Type"]
            got["len"] = len(self.rfile.read(n))
            data = json.dumps({"text": ec.ASR_REFERENCE}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            data = json.dumps({"data": [{"id": "m"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out = ec.check_asr(ec.Endpoint("127.0.0.1", srv.server_port, "tok"))
    srv.shutdown()
    assert out[0]["state"] == "passed" and out[1]["state"] == "passed"
    assert got["ctype"].startswith("multipart/form-data") and got["len"] > 400_000


# ---------------------------------------------------------------- TTS

def _wav(seconds=3.0, amplitude=3000, rate=24000, width=2):
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(1)
    w.setsampwidth(width)
    w.setframerate(rate)
    n = int(seconds * rate)
    if width == 2:
        frames = b"".join(struct.pack("<h", int(amplitude * math.sin(i / 7))) for i in range(n))
    else:
        frames = bytes(n * width)
    w.writeframes(frames)
    w.close()
    return buf.getvalue()


def _tts_ep(status=200, data=None, seen=None):
    class Ep(_Scripted):
        def call_raw(self, path, body, content_type, binary=False):
            if seen is not None:
                seen.update(path=path, body=json.loads(body), binary=binary)
            self.calls.append(path)
            return status, _wav() if data is None else data

    return Ep({"/v1/models": (200, {"data": [{"id": "openbmb/VoxCPM2"}]})})


def test_good_speech_passes_both_checks_and_asks_for_wav():
    seen = {}
    out = _run_ep(_tts_ep(seen=seen), {"speech"})
    assert out[ec.TTS_CHECK]["state"] == "passed"
    assert out[ec.TTS_ANSWER_CHECK]["state"] == "passed"
    assert seen["path"] == "/v1/audio/speech" and seen["binary"] is True
    assert seen["body"]["response_format"] == "wav" and seen["body"]["model"] == "openbmb/VoxCPM2"


@pytest.mark.parametrize("status,data,why", [
    (404, b'{"detail":"Not Found"}', "HTTP 404"),
    (200, b'{"error":"oops"}', "not a WAV"),
    (200, b"RIFF\x00\x00\x00\x00WAVEjunk", "does not decode"),
    (200, None, None),          # placeholder replaced below: zero-frame WAV
])
def test_broken_speech_fails_the_structural_check(status, data, why):
    if data is None:
        data, why = _wav(seconds=0), "no audio frames"
    out = _run_ep(_tts_ep(status, data), {"speech"})
    assert out[ec.TTS_CHECK]["state"] == "failed" and why in out[ec.TTS_CHECK]["detail"]
    assert out[ec.TTS_ANSWER_CHECK]["state"] == "skipped"


def test_silence_fails_the_answer_check_only():
    out = _run_ep(_tts_ep(data=_wav(amplitude=0)), {"speech"})
    assert out[ec.TTS_CHECK]["state"] == "passed"
    assert out[ec.TTS_ANSWER_CHECK]["state"] == "failed" and "silent" in out[ec.TTS_ANSWER_CHECK]["detail"]


@pytest.mark.parametrize("seconds", [0.2, 60.0])
def test_an_implausible_length_fails_the_answer_check(seconds):
    out = _run_ep(_tts_ep(data=_wav(seconds=seconds)), {"speech"})
    assert out[ec.TTS_ANSWER_CHECK]["state"] == "failed"
    assert "implausible" in out[ec.TTS_ANSWER_CHECK]["detail"]


def test_chat_refusal_runs_after_the_declared_checks():
    """Probing an undeclared route can kill the server; it must go last."""
    ep = _tts_ep()
    _run_ep(ep, {"speech"})
    assert ep.calls.index("/v1/audio/speech") < ep.calls.index("/v1/chat/completions")


def test_binary_speech_survives_a_real_socket(plain_http):
    """The bytes path must not decode the WAV as text."""
    audio = _wav()

    class H(BaseHTTPRequestHandler):
        def _send(self, ctype, data):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            self._send("application/json", json.dumps({"data": [{"id": "m"}]}).encode())

        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            self._send("audio/wav", audio)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out = ec.check_tts(ec.Endpoint("127.0.0.1", srv.server_port, "tok"))
    srv.shutdown()
    assert out[0]["state"] == "passed" and out[1]["state"] == "passed", out


# ---------------------------------------------------------------- ComfyUI workflows

import base64
import zlib


def _png(w=96, h=80, pattern=True, color=6, filters=(0, 1, 2, 3, 4)):
    """A real 8-bit PNG, rows cycling through every filter type so the decoder's
    unfiltering is exercised, not just filter 0."""
    ch = {0: 1, 2: 3, 6: 4}[color]
    rows, prev = [], bytearray(w * ch)
    for y in range(h):
        line = bytearray()
        for x in range(w):
            v = ((x * 7 + y * 13) % 256) if pattern else 128
            line += bytes([v, (v * 3) % 256, 255 - v, 255][:ch])
        f = filters[y % len(filters)]
        out = bytearray()
        for i in range(len(line)):
            a = line[i - ch] if i >= ch else 0
            b, c = prev[i], prev[i - ch] if i >= ch else 0
            if f == 0:
                pred = 0
            elif f == 1:
                pred = a
            elif f == 2:
                pred = b
            elif f == 3:
                pred = (a + b) >> 1
            else:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
            out.append((line[i] - pred) & 255)
        rows.append(bytes([f]) + bytes(out))
        prev = line

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def test_png_decoder_reconstructs_every_filter_type():
    w, h, lum = ec._png_pixels(_png())
    assert (w, h) == (96, 80) and len(lum) > 100
    # Filter-0 row 0 is the unfiltered truth; decoded values must match it exactly.
    first = [((x * 7) % 256 + (x * 7 * 3) % 256 + 255 - (x * 7) % 256) / 3
             for x in range(0, 96, max(1, 96 // 64))]
    assert lum[:len(first)] == pytest.approx(first)


def test_a_patterned_png_passes_and_a_uniform_one_fails_the_content_check():
    s, a = ec.check_output_file("wf#1", _png())
    assert s["state"] == "passed" and a["state"] == "passed"
    s, a = ec.check_output_file("wf#1", _png(pattern=False))
    assert s["state"] == "passed" and a["state"] == "failed" and "blank or uniform" in a["detail"]


def test_greyscale_and_rgb_pngs_decode():
    for color in (0, 2):
        assert ec.check_output_file("wf#1", _png(color=color))[1]["state"] == "passed"


def test_a_tiny_image_fails_structural():
    s, a = ec.check_output_file("wf#1", _png(w=32, h=32))
    assert s["state"] == "failed" and "below" in s["detail"] and a["state"] == "skipped"


def _jpeg(w, h, body=b"\x00" * 50):
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9 + sof + body + b"\xff\xd9"


def test_jpeg_dimensions_and_the_compression_proxy():
    s, a = ec.check_output_file("wf#1", _jpeg(512, 512, body=bytes(range(256)) * 40))
    assert s["state"] == "passed" and "512x512" in s["detail"]
    assert a["state"] == "passed" and "compression proxy" in a["detail"]
    s, a = ec.check_output_file("wf#1", _jpeg(1024, 1024))
    assert a["state"] == "failed"            # ~0.0001 bytes/pixel: near-uniform


@pytest.mark.parametrize("data,kind", [
    (b"\x00\x00\x00\x20ftypisom" + b"\x00" * 100, "video"),
    (b"\x1aE\xdf\xa3" + b"\x00" * 100, "video"),
    (b"GIF89a" + b"\x00" * 100, "video"),
    (b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 100, "audio"),
    (b"ID3\x04" + b"\x00" * 100, "audio"),
])
def test_video_and_audio_pass_structural_and_say_content_is_not_written(data, kind):
    s, a = ec.check_output_file("wf#1", data)
    assert s["state"] == "passed" and kind in s["detail"]
    assert a["state"] == "skipped" and "not written yet" in a["detail"]


def test_an_unrecognised_output_fails():
    s, _ = ec.check_output_file("wf#1", b"<html>error</html>")
    assert s["state"] == "failed"


class _FakeJupyter:
    def __init__(self, files, big=None):
        self.files, self.big, self.reads = files, big or {}, []

    def list_dir(self, path):
        return sorted(self.files), None

    def read_text(self, path):
        self.reads.append(path)
        name = path.rsplit("/", 1)[1]
        return (json.dumps(self.files[name]), None) if name in self.files else (None, "HTTP 404")

    def read_bytes(self, path):
        return (self.big[path], None) if path in self.big else (None, "HTTP 404")


class _FakeWrapper:
    def __init__(self, results):
        self.results, self.posted = results, []

    def call_raw_with_headers(self, path, data, ctype, headers, timeout=None, binary=False):
        payload = json.loads(data)
        self.posted.append((path, payload["input"]["name"], headers, timeout))
        return self.results[payload["input"]["name"]]


class _FakeComfy:
    """ComfyUI /object_info: which classes are output nodes (custom packs included)."""

    def __init__(self, output_classes=("SaveImage", "PreviewImage", "SaveVideo", "SaveAudio",
                                       "VHS_VideoCombine", "SaveAnimatedWEBP"),
                 status=200):
        self.output_classes, self.status = output_classes, status

    def call(self, path, body=None):
        assert path == "/object_info"
        if self.status != 200:
            return self.status, "error"
        info = {c: {"output_node": True} for c in self.output_classes}
        info.update({"KSampler": {"output_node": False}, "CreateVideo": {"output_node": False}})
        return 200, info


def _wf(**nodes):
    """An API-format workflow: node_id -> (class_type, inputs)."""
    return {nid: {"class_type": cls, "inputs": inputs} for nid, (cls, inputs) in nodes.items()}


def _payload(name, workflow):
    return {f"{name}.json": {"input": {"name": name, "workflow_json": workflow}}}


def _result(*files):
    """files: (node_id, bytes) pairs, inlined as base64 the way the wrapper does."""
    out = [{"filename": f"f{i}", "node_id": nid, "data": base64.b64encode(data).decode()}
           for i, (nid, data) in enumerate(files)]
    return 200, {"status": "completed", "output": out, "timings": {"generation_ms": 1234}}


MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64 + b"hdlr" + b"\x00" * 8 + b"vide" + b"\x00" * 64
MP4_AUDIO = MP4 + b"hdlr" + b"\x00" * 8 + b"soun" + b"\x00" * 64
WAV = b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 100


def _check(workflows, results, comfy=None, big=None):
    files = {}
    for name, wf in workflows.items():
        files.update(_payload(name, wf))
    out = ec.check_comfyui_workflows(_FakeWrapper(results), _FakeJupyter(files, big),
                                     comfy=comfy or _FakeComfy(), timeout=900)
    return {r["name"]: r for r in out}


T2I = _wf(**{"3": ("KSampler", {}), "9": ("SaveImage", {"images": ["8", 0]})})


def test_every_workflow_runs_with_base64_outputs_requested():
    wrap = _FakeWrapper({"a": _result(("9", _png())), "b": _result(("9", _png()))})
    ec.check_comfyui_workflows(wrap, _FakeJupyter({**_payload("a", T2I), **_payload("b", T2I)}),
                               comfy=_FakeComfy(), timeout=900)
    assert [p[1] for p in wrap.posted] == ["a", "b"]
    assert all(p[2] == {"x-return-outputs-as-base64": "true"} and p[3] == 900 for p in wrap.posted)


def test_an_image_node_that_delivers_an_image_passes():
    out = _check({"t2i": T2I}, {"t2i": _result(("9", _png()))})
    assert out["external/comfyui/t2i"]["state"] == "passed"
    assert out["external/comfyui/t2i/SaveImage#9/1"]["state"] == "passed"
    assert out["external/comfyui/t2i/SaveImage#9/1:content"]["state"] == "passed"


def test_a_video_workflow_that_emits_an_image_fails():
    """The case file-signature typing missed: right kind of file is not enough."""
    wf = _wf(**{"20": ("SaveVideo", {"video": ["19", 0]})})
    out = _check({"t2v": wf}, {"t2v": _result(("20", _png()))})
    r = out["external/comfyui/t2v/SaveVideo#20/1"]
    assert r["state"] == "failed" and "expected video" in r["detail"] and "got image" in r["detail"]


def test_an_output_node_that_produced_nothing_fails():
    wf = _wf(**{"9": ("SaveImage", {}), "20": ("SaveVideo", {})})
    out = _check({"both": wf}, {"both": _result(("9", _png()))})
    assert out["external/comfyui/both/SaveImage#9/1"]["state"] == "passed"
    r = out["external/comfyui/both/SaveVideo#20"]
    assert r["state"] == "failed" and "produced no files" in r["detail"]


def test_a_preview_node_is_not_expected_to_return_files():
    wf = _wf(**{"9": ("SaveImage", {}), "10": ("PreviewImage", {})})
    out = _check({"t2i": wf}, {"t2i": _result(("9", _png()))})
    assert out["external/comfyui/t2i/PreviewImage#10"]["state"] == "skipped"


def test_video_fed_audio_must_carry_a_sound_track():
    """CreateVideo with audio wired, feeding SaveVideo — the LTX-style audio-video shape."""
    wf = _wf(**{"18": ("CreateVideo", {"images": ["17", 0], "audio": ["16", 0], "fps": 24}),
                "20": ("SaveVideo", {"video": ["18", 0]})})
    out = _check({"av": wf}, {"av": _result(("20", MP4))})
    r = out["external/comfyui/av/SaveVideo#20/1"]
    assert r["state"] == "failed" and "no sound track" in r["detail"]
    out = _check({"av": wf}, {"av": _result(("20", MP4_AUDIO))})
    assert out["external/comfyui/av/SaveVideo#20/1"]["state"] == "passed"
    assert "with audio" in out["external/comfyui/av/SaveVideo#20/1"]["detail"]


def test_video_without_audio_wired_does_not_require_a_sound_track():
    wf = _wf(**{"18": ("CreateVideo", {"images": ["17", 0], "fps": 24}),
                "20": ("SaveVideo", {"video": ["18", 0]})})
    out = _check({"v": wf}, {"v": _result(("20", MP4))})
    assert out["external/comfyui/v/SaveVideo#20/1"]["state"] == "passed"
    assert out["external/comfyui/v/SaveVideo#20/1:content"]["state"] == "skipped"


@pytest.mark.parametrize("fmt,data,ok", [
    ("video/h264-mp4", MP4, True),
    ("image/gif", b"GIF89a" + b"\x00" * 100, True),
    ("video/h264-mp4", _png(), False),
])
def test_vhs_video_combine_kind_comes_from_its_format_input(fmt, data, ok):
    wf = _wf(**{"30": ("VHS_VideoCombine", {"format": fmt, "images": ["29", 0]})})
    out = _check({"vhs": wf}, {"vhs": _result(("30", data))})
    assert (out["external/comfyui/vhs/VHS_VideoCombine#30/1"]["state"] == "passed") is ok


def test_vhs_with_audio_wired_requires_a_sound_track():
    wf = _wf(**{"30": ("VHS_VideoCombine", {"format": "video/h264-mp4", "images": ["29", 0],
                                             "audio": ["28", 0]})})
    out = _check({"vhs": wf}, {"vhs": _result(("30", MP4))})
    assert out["external/comfyui/vhs/VHS_VideoCombine#30/1"]["state"] == "failed"


def test_an_audio_node_must_deliver_audio():
    wf = _wf(**{"12": ("SaveAudio", {"audio": ["11", 0]})})
    assert _check({"tts": wf}, {"tts": _result(("12", WAV))})[
        "external/comfyui/tts/SaveAudio#12/1"]["state"] == "passed"
    assert _check({"tts": wf}, {"tts": _result(("12", _png()))})[
        "external/comfyui/tts/SaveAudio#12/1"]["state"] == "failed"


def test_an_unknown_custom_output_node_is_an_error_not_a_pass():
    """Discovered as an output node by /object_info but not in the table."""
    wf = _wf(**{"40": ("SomePack_SaveMesh", {})})
    out = _check({"mesh": wf}, {"mesh": _result(("40", b"ply\n"))},
                 comfy=_FakeComfy(output_classes=("SomePack_SaveMesh",)))
    r = out["external/comfyui/mesh/SomePack_SaveMesh#40"]
    assert r["state"] == "error" and "unclassified output node SomePack_SaveMesh" in r["detail"]


def test_without_object_info_the_table_is_used_and_the_gap_is_reported():
    out = _check({"t2i": T2I}, {"t2i": _result(("9", _png()))}, comfy=_FakeComfy(status=502))
    assert out["external/comfyui/object_info"]["state"] == "error"
    assert out["external/comfyui/t2i/SaveImage#9/1"]["state"] == "passed"


def test_files_from_a_node_that_is_not_an_output_node_are_reported():
    out = _check({"t2i": T2I}, {"t2i": _result(("9", _png()), ("3", _png()))})
    assert out["external/comfyui/t2i/#3"]["state"] == "error"


def test_one_failing_workflow_does_not_hide_the_others():
    out = _check({"a": T2I, "b": T2I},
                 {"a": (500, {"status": "failed", "message": "CUDA OOM"}), "b": _result(("9", _png()))})
    assert out["external/comfyui/a"]["state"] == "failed" and "CUDA OOM" in out["external/comfyui/a"]["detail"]
    assert out["external/comfyui/b"]["state"] == "passed"


def test_a_blank_generated_image_fails_its_content_check():
    out = _check({"t2i": T2I}, {"t2i": _result(("9", _png(pattern=False)))})
    assert out["external/comfyui/t2i/SaveImage#9/1"]["state"] == "passed"
    assert out["external/comfyui/t2i/SaveImage#9/1:content"]["state"] == "failed"


def test_a_video_too_large_to_inline_is_fetched_over_jupyter():
    wf = _wf(**{"20": ("SaveVideo", {"video": ["19", 0]})})
    res = (200, {"status": "completed", "output": [
        {"filename": "v.mp4", "node_id": "20", "local_path": "/workspace/output/v.mp4",
         "error": "file size exceeds OUTPUT_BASE64_MAX_BYTES"}]})
    out = _check({"i2v": wf}, {"i2v": res}, big={"workspace/output/v.mp4": MP4})
    assert out["external/comfyui/i2v/SaveVideo#20/1"]["state"] == "passed"


def test_a_workflow_with_no_output_node_fails():
    wf = _wf(**{"3": ("KSampler", {})})
    out = _check({"x": wf}, {"x": _result()})
    assert out["external/comfyui/x"]["state"] == "failed" and "no output node" in out["external/comfyui/x"]["detail"]


class _ModifierJupyter(_FakeJupyter):
    """Payloads plus the wrapper's built-in workflows directory."""

    def __init__(self, files, builtin):
        super().__init__(files)
        self.builtin = builtin

    def read_text(self, path):
        if path.startswith(ec.COMFY_MODIFIER_WORKFLOWS_DIR + "/"):
            name = path.rsplit("/", 1)[1]
            self.reads.append(path)
            return (json.dumps(self.builtin[name]), None) if name in self.builtin else (None, "HTTP 404")
        return super().read_text(path)


def _modifier_payload(name, modifier):
    return {f"{name}.json": {"input": {"name": name, "modifier": modifier,
                                       "modifications": {"prompt": "a sunset"}}}}


def test_a_modifier_payload_is_checked_against_its_base_workflow():
    """Seen live: the wrapper's example `text2image_modifier` carries no workflow,
    only `modifier: Text2Image`; its nodes come from workflows/text2image.json."""
    jup = _ModifierJupyter(_modifier_payload("t2i_mod", "Text2Image"), {"text2image.json": T2I})
    out = {r["name"]: r for r in ec.check_comfyui_workflows(
        _FakeWrapper({"t2i_mod": _result(("9", _png()))}), jup, comfy=_FakeComfy())}
    assert "opt/comfyui-api-wrapper/workflows/text2image.json" in jup.reads
    assert out["external/comfyui/t2i_mod/SaveImage#9/1"]["state"] == "passed"


def test_an_unresolvable_modifier_is_an_error_not_a_pass():
    jup = _ModifierJupyter(_modifier_payload("x", "Mystery"), {})
    out = ec.check_comfyui_workflows(_FakeWrapper({}), jup, comfy=_FakeComfy())
    assert out[0]["state"] == "error" and "cannot read its base workflow" in out[0]["detail"]


def test_no_payloads_is_a_failure_not_a_pass():
    out = ec.check_comfyui_workflows(_FakeWrapper({}), _FakeJupyter({}), comfy=_FakeComfy())
    assert out[0]["state"] == "failed" and "no workflow payloads" in out[0]["detail"]


class _Both(_FakeWrapper, _FakeComfy):
    """One fake that answers as the wrapper (generate) and as ComfyUI (object_info)."""

    def __init__(self, results):
        _FakeWrapper.__init__(self, results)
        _FakeComfy.__init__(self)


def _comfy_instance():
    return {"public_ipaddr": "203.0.113.7",
            "ports": {"8188/tcp": [{"HostPort": "40188"}], "8288/tcp": [{"HostPort": "40288"}],
                      "8080/tcp": [{"HostPort": "40080"}]}}


COMFY_PORTAL = ("localhost:1111:11111:/:Instance Portal|localhost:8188:18188:/:ComfyUI|"
                "localhost:8288:18288:/docs:API Wrapper|localhost:8080:18080:/:Jupyter")


def test_a_comfyui_template_uses_the_wrapper_and_jupyter_ports_and_skips_chat_refusal():
    seen = {}

    def ep_factory(host, port, token):
        seen.setdefault("ports", []).append(port)
        return _Both({"t2i": _result(("9", _png()))})

    def jup_factory(host, port, token):
        seen["jupyter"] = port
        return _FakeJupyter(_payload("t2i", T2I))

    out = ec.run_external_checks(
        _comfy_instance(), {"image": "vastai/vllm", "env": f'-e PORTAL_CONFIG="{COMFY_PORTAL}"'},
        [{"name": "comfyui.d/10-comfyui-serving", "state": "passed"}],
        token="tok", endpoint_factory=ep_factory, jupyter_factory=jup_factory,
        capabilities={"comfyui-workflows"})
    names = {r["name"] for r in out}
    assert seen == {"ports": ["40188", "40288"], "jupyter": "40080"}
    assert "external/comfyui/t2i" in names and ec.UNDECLARED_CHAT_CHECK not in names
    assert all(r["advisory"] for r in out)


def test_the_comfyui_serving_test_counts_as_ran():
    assert ec.serving_test_ran([{"name": "comfyui.d/10-comfyui-serving", "state": "failed"}])


# ---------------------------------------------------------------- capabilities from OPENAI_ROUTES

def test_openai_routes_map_to_route_named_capabilities():
    env = '-e OPENAI_ROUTES="/v1/audio/speech,/v1/embeddings" -e X=1'
    caps, source, problems = ec.resolve_capabilities({"image": "vastai/vllm", "env": env})
    assert caps == {"speech", "embeddings"} and source == "OPENAI_ROUTES" and not problems


def test_an_unknown_route_is_reported():
    caps, _, problems = ec.resolve_capabilities({"image": "vastai/vllm", "env": "-e OPENAI_ROUTES=/v1/chat/completions,/v1/nope"})
    assert caps == {"chat"} and problems == ["OPENAI_ROUTES names unknown route /v1/nope"]


def test_an_env_override_beats_the_template():
    caps, _, _ = ec.resolve_capabilities({"image": "vastai/vllm", "env": "-e OPENAI_ROUTES=/v1/embeddings"},
                                         ["OPENAI_ROUTES=/v1/audio/transcriptions"])
    assert caps == {"transcriptions"}


def test_unset_routes_mean_pyworkers_default():
    caps, source, problems = ec.resolve_capabilities({"image": "vastai/vllm", "env": "-e VLLM_MODEL=x"})
    assert caps == {"chat", "completions"} and "pyworker default" in source and not problems


def test_a_comfy_image_means_workflows():
    caps, source, _ = ec.resolve_capabilities({"image": "vastai/comfy", "env": ""})
    assert caps == {"comfyui-workflows"} and source == "ComfyUI image"
    caps, _, _ = ec.resolve_capabilities({"image": "x", "env": "-e PROVISIONING_COMFYUI_WORKFLOWS=a.json"})
    assert caps == {"comfyui-workflows"}


def test_the_command_line_override_wins_and_flags_typos():
    caps, source, problems = ec.resolve_capabilities(
        {"image": "vastai/vllm", "env": "-e OPENAI_ROUTES=/v1/embeddings"}, override="speech,specch")
    assert caps == {"speech", "specch"} and source == "--capabilities"
    assert problems == ["unknown capability specch"]


def test_every_run_starts_with_what_it_checked_and_why():
    out = ec.run_external_checks(_instance(), {"image": "vastai/vllm", "env": "-e OPENAI_ROUTES=/v1/embeddings"},
                                 SERVING_PASSED, endpoint_factory=_factory(_Scripted(EMBED_ROUTES)))
    assert out[0] == {"name": "external/capabilities", "state": "info", "advisory": True,
                      "detail": "embeddings (OPENAI_ROUTES)"}


def test_declared_routes_without_a_check_are_skipped_not_passed():
    out = ec.run_external_checks(
        _instance(), {"image": "vastai/vllm", "env": "-e OPENAI_ROUTES=/v1/embeddings,/v1/rerank,/v1/score"},
        SERVING_PASSED, endpoint_factory=_factory(_Scripted(EMBED_ROUTES)))
    states = {r["name"]: (r["state"], r["detail"]) for r in out}
    assert states["external/rerank"][0] == "skipped" and "no external check" in states["external/rerank"][1]
    assert states["external/score"][0] == "skipped"
    assert states[ec.EMBEDDINGS_CHECK][0] == "passed"


def test_the_undeclared_chat_guard_runs_for_a_non_chat_declaration():
    out = ec.run_external_checks(_instance(), {"image": "vastai/vllm", "env": "-e OPENAI_ROUTES=/v1/embeddings"},
                                 SERVING_PASSED, endpoint_factory=_factory(_Scripted(EMBED_ROUTES)))
    guard = {r["name"]: r for r in out}[ec.UNDECLARED_CHAT_CHECK]
    assert ec.UNDECLARED_CHAT_CHECK == "external/undeclared/chat" and guard["state"] == "passed"


def test_song_models_skip_the_speech_check_until_its_variant_exists():
    ep = _tts_ep()
    out = ec.run_external_checks(_instance(), {"image": "vastai/vllm", "env": "-e OPENAI_ROUTES=/v1/audio/speech -e SPEECH_KIND=music"},
                                 SERVING_PASSED, endpoint_factory=_factory(ep))
    r = {x["name"]: x for x in out}[ec.TTS_CHECK]
    assert r["state"] == "skipped" and "SPEECH_KIND=music" in r["detail"]
    assert "/v1/audio/speech" not in ep.calls


def test_completions_check_needs_text_and_tokens():
    good = _Scripted({"/v1/models": MODELS, "/v1/completions": (200, {"choices": [{"text": " Paris"}],
                                                                      "usage": {"completion_tokens": 2}})})
    assert ec.check_completions(good)["state"] == "passed"
    for bad in ((404, {"detail": "Not Found"}), (200, {"choices": []}),
                (200, {"choices": [{"text": ""}], "usage": {"completion_tokens": 0}})):
        ep = _Scripted({"/v1/models": MODELS, "/v1/completions": bad})
        assert ec.check_completions(ep)["state"] == "failed"


# ---------------------------------------------------------------- non-engine templates

@pytest.mark.parametrize("image", ["vastai/base-image", "vastai/pytorch:2.9.1-cuda-12.9",
                                   "vastai/openwebui", "vastai/linux-desktop", ""])
def test_a_non_engine_image_gets_no_external_checks(image):
    """A base-image, PyTorch or app template has no OpenAI engine: its in-image suite is
    the whole test. Applying pyworker's default here would wait up to 30 minutes for a
    /v1/models that never comes."""
    caps, source, problems = ec.resolve_capabilities({"image": image, "env": ""})
    assert caps == set() and "no OpenAI engine" in source and not problems


def test_a_non_engine_run_reports_only_what_it_checked():
    out = ec.run_external_checks(_instance(), {"image": "vastai/pytorch", "env": ""},
                                 [{"name": "base/10-supervisor", "state": "passed"}],
                                 endpoint_factory=_Unused, sleep=lambda s: (_ for _ in ()).throw(
                                     AssertionError("must not wait for an engine")))
    assert out == [{"name": "external/capabilities", "state": "info", "advisory": True,
                    "detail": "none (no OpenAI engine in image vastai/pytorch: no external checks)"}]


@pytest.mark.parametrize("image", ["vastai/vllm", "vastai/vllm-omni:v0.30.0-cuda-13.0",
                                   "vastai/sglang", "vastai/llama-cpp"])
def test_engine_images_keep_pyworkers_default(image):
    caps, _, _ = ec.resolve_capabilities({"image": image, "env": ""})
    assert caps == {"chat", "completions"}


def test_a_non_engine_image_can_still_declare_routes():
    caps, source, _ = ec.resolve_capabilities(
        {"image": "vastai/base-image", "env": "-e OPENAI_ROUTES=/v1/embeddings"})
    assert caps == {"embeddings"} and source == "OPENAI_ROUTES"


# ---------------------------------------------------------------- the contract, wired in

import contract as cc


class _ContractEngine:
    """A healthy engine as contract.Client sees it: get / post / post_stream."""

    def __init__(self, model="Qwen/Qwen3.5-0.8B", routes_seen=None):
        self.model, self.seen = model, routes_seen if routes_seen is not None else []

    def get(self, path):
        return 200, json.dumps({"data": [{"id": self.model, "max_model_len": 4096}]})

    def post(self, path, payload, *, raw=None):
        self.seen.append(path)
        if raw is not None or (payload or {}).get("model") == cc.NO_SUCH_MODEL:
            return 400, '{"error": "bad request"}'
        if (payload or {}).get("max_tokens", 0) > 4096:
            return 400, '{"error": "too long"}'
        if path == "/v1/completions":
            return 200, json.dumps({"choices": [{"text": "x", "finish_reason": "length"}],
                                    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}})
        if path == "/v1/embeddings":
            return 200, json.dumps({"data": [{"embedding": [0.1, 0.2]}]})
        return 200, json.dumps({"choices": [{"finish_reason": "length",
                                             "message": {"role": "assistant", "content": "x"}}],
                                "usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8}})

    def post_stream(self, path, payload):
        self.seen.append(path + " (stream)")
        return 200, ['data: {"choices": []}', 'data: {"usage": {"completion_tokens": 1}}', "data: [DONE]"]


@pytest.mark.contract
def test_an_engine_template_runs_the_contract_keyed_on_its_routes():
    seen = []
    env = '-e VLLM_MODEL=Qwen/Qwen3.5-0.8B -e VLLM_ARGS="--max-model-len 4096"'
    out = ec.run_external_checks(
        _instance(), {"image": "vastai/vllm", "env": env}, SERVING_PASSED,
        endpoint_factory=_factory(_Scripted({"/v1/models": MODELS})),
        contract_client_factory=lambda base, token: _ContractEngine(routes_seen=seen),
        capabilities="chat,completions")
    states = {r["name"]: r["state"] for r in out if r["name"].startswith("contract/")}
    for name in ("identity", "token-arithmetic", "finish-reason", "streaming",
                 "completions-route", "error-unknown-model", "error-malformed-body",
                 "error-context-overflow"):
        assert states.get(f"contract/{name}") == "passed", (name, states)


@pytest.mark.contract
def test_an_embeddings_template_gets_the_contract_without_chat():
    seen = []
    env = '-e VLLM_MODEL=Qwen/Qwen3.5-0.8B -e OPENAI_ROUTES=/v1/embeddings'
    out = ec.run_external_checks(
        _instance(), {"image": "vastai/vllm", "env": env}, SERVING_PASSED,
        endpoint_factory=_factory(_Scripted(EMBED_ROUTES)),
        contract_client_factory=lambda base, token: _ContractEngine(routes_seen=seen))
    contract_paths = [p for p in seen if "chat" in p]
    assert contract_paths == [], "the contract sent chat to an embeddings template"
    states = {r["name"]: r["state"] for r in out if r["name"].startswith("contract/")}
    assert states["contract/identity"] == "passed"
    assert states["contract/error-unknown-model"] == "passed"
    assert states["contract/token-arithmetic"] == "skipped"


@pytest.mark.contract
def test_a_violation_is_a_failed_contract_result_and_a_deviation_its_own_state():
    class Answers200(_ContractEngine):
        def post(self, path, payload, *, raw=None):
            if (payload or {}).get("model") == cc.NO_SUCH_MODEL:
                return 200, json.dumps({"choices": [{"message": {"role": "assistant"}}]})
            return super().post(path, payload, raw=raw)

    env = '-e LLAMA_MODEL=m -e LLAMA_ARGS="--ctx-size 4096 --alias Qwen/Qwen3.5-0.8B"'
    llama = ec.run_external_checks(
        _instance(), {"image": "vastai/llama-cpp", "env": env},
        [{"name": "llama.d/10-llama-serving", "state": "passed"}],
        endpoint_factory=_factory(_Scripted({"/v1/models": MODELS})),
        contract_client_factory=lambda b, t: Answers200(), capabilities="chat")
    assert {r["name"]: r["state"] for r in llama}["contract/error-unknown-model"] == "deviation"
    env = '-e VLLM_MODEL=Qwen/Qwen3.5-0.8B -e VLLM_ARGS="--max-model-len 4096"'
    vllm = ec.run_external_checks(
        _instance(), {"image": "vastai/vllm", "env": env}, SERVING_PASSED,
        endpoint_factory=_factory(_Scripted({"/v1/models": MODELS})),
        contract_client_factory=lambda b, t: Answers200(), capabilities="chat")
    assert {r["name"]: r["state"] for r in vllm}["contract/error-unknown-model"] == "failed"
    cc.set_engine("vastai/vllm")


@pytest.mark.contract
def test_a_non_engine_image_gets_no_contract():
    assert ec.check_contract("h", 1, "t", "vastai/pytorch", "", (), {"chat"},
                             client_factory=lambda b, t: (_ for _ in ()).throw(AssertionError)) == []


class _FakeRemote:
    """A remote.RemoteSession stand-in recording its lifecycle."""
    instances = []

    def __init__(self, answer='{"encoded": [0, 1, 2, 3, 4, 5, 6]}'):
        self.answer, self.entered, self.exited, self.scripts = answer, False, False, []
        _FakeRemote.instances.append(self)

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        self.exited = True
        return False

    def run(self, script, python="python3"):
        assert self.entered and not self.exited
        self.scripts.append(script)
        return True, self.answer


@pytest.mark.contract
def test_prompt_roundtrip_counts_on_the_box_and_the_session_is_closed():
    _FakeRemote.instances = []
    env = '-e VLLM_MODEL=Qwen/Qwen3.5-0.8B -e VLLM_ARGS="--max-model-len 4096"'
    out = ec.run_external_checks(
        _instance(), {"image": "vastai/vllm", "env": env}, SERVING_PASSED,
        endpoint_factory=_factory(_Scripted({"/v1/models": MODELS})),
        contract_client_factory=lambda b, t: _ContractEngine(),
        remote_factory=_FakeRemote, capabilities="chat")
    state = {r["name"]: r["state"] for r in out}["contract/prompt-roundtrip"]
    assert state == "passed"
    (session,) = _FakeRemote.instances
    assert session.entered and session.exited, "the ephemeral key must be removed"


@pytest.mark.contract
def test_no_session_is_opened_when_nothing_needs_the_box():
    _FakeRemote.instances = []
    env = '-e VLLM_MODEL=Qwen/Qwen3.5-0.8B -e OPENAI_ROUTES=/v1/embeddings'
    ec.run_external_checks(
        _instance(), {"image": "vastai/vllm", "env": env}, SERVING_PASSED,
        endpoint_factory=_factory(_Scripted(EMBED_ROUTES)),
        contract_client_factory=lambda b, t: _ContractEngine(), remote_factory=_FakeRemote)
    assert _FakeRemote.instances == [], "attached a key for a run that never needed the box"


# ---------------------------------------------------------------- transport (Endpoint)

import socket
import ssl
import urllib.error


def _silent_server():
    """A TCP port that completes connections (the kernel does that) and never
    answers. Nothing accepts while the client runs; _drain() accepts afterwards, so
    the connection count does not depend on a server thread keeping up."""
    lsock = socket.socket()
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(16)
    return lsock


def _drain(lsock):
    """Every connection the client made, with what it sent: accepted from the
    backlog after the client has returned, then closed. They are already queued
    when this runs, so a short accept timeout only ends the loop."""
    lsock.settimeout(0.2)
    got = []
    while True:
        try:
            conn, _ = lsock.accept()
        except OSError:                                    # backlog empty
            break
        conn.settimeout(1)
        try:
            got.append(conn.recv(65536))
        except OSError:
            got.append(b"")
        conn.close()
    lsock.close()
    return got


# The client timeout is far above a loopback connect (microseconds), so a slow,
# loaded machine cannot turn a connect into a timeout and change the retry count.
SILENT_TIMEOUT = 1.0


def test_a_post_that_timed_out_reading_is_not_resent_and_is_an_error():
    """The request reached the engine; generation is not idempotent and costs GPU
    time, so a read timeout is reported, never answered by sending it again."""
    lsock = _silent_server()
    port = lsock.getsockname()[1]
    ep = ec.Endpoint("127.0.0.1", port, "tok", timeout=SILENT_TIMEOUT, sleep=lambda s: None)
    ep.bases = [f"http://127.0.0.1:{port}"]                    # pinned: plain http
    assert ep.retries == ec.CONNECT_RETRIES > 0
    status, body = ep.call("/v1/chat/completions", {"model": "m"})
    got = _drain(lsock)
    assert status == 0 and "timed out" in body
    assert len(got) == 1, "the POST was sent again"
    assert got[0].startswith(b"POST /v1/chat/completions")
    r = ec.check_completions(_Scripted({"/v1/models": MODELS,
                                        "/v1/completions": (status, body)}))
    assert r["state"] == "error"


def test_an_https_port_that_never_answers_is_retried_over_https_and_never_http():
    """A handshake timeout: the request never left, so it is retried — over https.
    Nothing is ever sent in the clear, token included."""
    lsock = _silent_server()
    slept = []
    ep = ec.Endpoint("127.0.0.1", lsock.getsockname()[1], "tok", timeout=SILENT_TIMEOUT,
                     sleep=slept.append)
    status, _ = ep.call("/v1/models")
    got = _drain(lsock)
    assert status == 0
    assert len(got) == ec.CONNECT_RETRIES + 1 and len(slept) == ec.CONNECT_RETRIES
    assert all(data.startswith(b"\x16\x03") for data in got), got     # TLS ClientHellos
    assert all(not data.startswith((b"GET", b"POST")) for data in got), got
    assert not any(b"Bearer tok" in data for data in got)


def test_the_endpoint_falls_back_to_http_on_a_plain_http_port_and_keeps_it():
    srv, seen = _serve({"/v1/models": MODELS})
    ep = ec.Endpoint("127.0.0.1", srv.server_port, "tok", timeout=5)
    assert ep.call("/v1/models")[0] == 200
    assert ep.call("/v1/models")[0] == 200
    srv.shutdown()
    assert ep.bases == [f"http://127.0.0.1:{srv.server_port}"]
    # The TLS ClientHello reached the server once, as an unparseable request line;
    # every real request after it went over http with the token.
    assert [p for _, p, _ in seen] == ["/v1/models", "/v1/models"]


def test_the_endpoint_never_sends_http_after_https_answered():
    sent = []

    def net(req, timeout, phase):
        sent.append(req.full_url)
        phase.connected = True
        if len(sent) == 1:
            raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, io.BytesIO(b"no"))
        raise urllib.error.URLError(ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number"))

    ep = ec.Endpoint("203.0.113.7", 40000, "tok", opener=net, sleep=lambda s: None)
    assert ep.call("/v1/models")[0] == 401
    assert ep.call("/v1/chat/completions", {"m": 1})[0] == 0
    assert all(u.startswith("https://") for u in sent), sent



# ---------------------------------------------------------------- status 0 is an error

NO_RESPONSE = (0, "TimeoutError: timed out")


@pytest.mark.parametrize("check,routes", [
    (ec.check_chat, {"/v1/models": NO_RESPONSE}),
    (ec.check_chat, {"/v1/models": MODELS, "/v1/chat/completions": NO_RESPONSE}),
    (ec.check_completions, {"/v1/models": NO_RESPONSE}),
    (ec.check_completions, {"/v1/models": MODELS, "/v1/completions": NO_RESPONSE}),
    (ec.check_chat_refused, {"/v1/models": NO_RESPONSE}),
    (ec.check_chat_refused, {"/v1/models": MODELS, "/v1/chat/completions": NO_RESPONSE}),
], ids=["chat/models", "chat/chat", "completions/models", "completions/completions",
        "undeclared/models", "undeclared/chat"])
def test_no_response_is_an_error_never_a_failure(check, routes):
    r = check(_Scripted(routes))
    assert r["state"] == "error" and "no HTTP response" in r["detail"], r


def test_embeddings_with_no_response_is_an_error_at_every_request():
    for broken in range(3):
        calls = {"n": 0}

        def embed(body, broken=broken):
            calls["n"] += 1
            return NO_RESPONSE if calls["n"] == broken else _embed_ok(body)

        routes = {"/v1/models": NO_RESPONSE if broken == 0 else EMBED_ROUTES["/v1/models"],
                  "/v1/embeddings": embed}
        s, a = ec.check_embeddings(_Scripted(routes))
        assert s["state"] == "error" and a["state"] == "skipped", (broken, s)


def test_asr_with_no_response_is_an_error():
    s, a = ec.check_asr(_asr_routes(*NO_RESPONSE), audio=b"RIFF")
    assert s["state"] == "error" and a["state"] == "skipped"
    s, _ = ec.check_asr(_Scripted({"/v1/models": NO_RESPONSE}), audio=b"RIFF")
    assert s["state"] == "error"


def test_tts_with_no_response_is_an_error():
    s, a = ec.check_tts(_tts_ep(*NO_RESPONSE))
    assert s["state"] == "error" and a["state"] == "skipped"
    s, _ = ec.check_tts(_Scripted({"/v1/models": NO_RESPONSE}))
    assert s["state"] == "error"


def test_an_engine_that_never_answers_at_all_is_an_error_not_a_failure():
    """Caddy answers (502/503) while the engine starts; no HTTP response for the whole
    wait means the instance could not be reached, not that the engine is broken."""
    ep = _Scripted({"/v1/models": NO_RESPONSE})
    out = ec.run_external_checks(_instance(), {"image": "vastai/vllm", "env": ""}, [],
                                 endpoint_factory=_factory(ep), capabilities={"chat"},
                                 ready_timeout=30, sleep=lambda s: None)
    assert _checks(out)[0]["state"] == "error" and "no HTTP response" in _checks(out)[0]["detail"]


def test_a_comfy_generation_with_no_response_is_an_error():
    out = _check({"t2i": T2I}, {"t2i": (0, "TimeoutError: timed out")})
    assert out["external/comfyui/t2i"]["state"] == "error"


def test_jupyter_with_no_response_is_an_error():
    class Unreachable(_FakeJupyter):
        def list_dir(self, path):
            return None, ec._jupyter_error(0, "URLError: refused")

    out = ec.check_comfyui_workflows(_FakeWrapper({}), Unreachable({}), comfy=_FakeComfy())
    assert out[0]["state"] == "error"
    assert ec._jupyter_error(404, "nope") == "HTTP 404: nope"
    assert not isinstance(ec._jupyter_error(404, "nope"), ec.NoResponse)


# ---------------------------------------------------------------- the undeclared-chat guard

@pytest.mark.parametrize("status,state", [
    (400, "passed"), (404, "passed"), (422, "passed"), (501, "passed"),
    (200, "failed"), (201, "failed"),
    (401, "error"), (403, "error"), (405, "error"), (408, "error"), (409, "error"),
    (429, "error"), (500, "error"), (502, "error"), (503, "error"), (0, "error"),
])
def test_the_undeclared_chat_guard_accepts_only_a_not_served_refusal(status, state):
    """401/403 is an auth problem and 429 is the request not being judged — neither
    shows chat is refused. Only 400/404/422/501 mean "not served here"."""
    r = ec.check_chat_refused(_Scripted({"/v1/models": MODELS,
                                         "/v1/chat/completions": (status, "body")}))
    assert r["state"] == state, r


# ---------------------------------------------------------------- one overall deadline

class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_checks_left_when_the_deadline_passes_are_named_errors():
    clock = _Clock()

    def slow_chat(body):
        clock.t += 100
        return _chat_ok(body)

    ep = _Scripted({"/v1/models": MODELS, "/v1/chat/completions": slow_chat,
                    "/v1/embeddings": _embed_ok})
    out = ec.run_external_checks(_instance(), {"image": "vastai/vllm", "env": ""}, SERVING_PASSED,
                                 endpoint_factory=_factory(ep),
                                 capabilities={"chat", "completions", "embeddings"},
                                 deadline=150, clock=clock)
    states = {r["name"]: r for r in out}
    for name in (ec.COMPLETIONS_CHECK, ec.EMBEDDINGS_CHECK, ec.EMBEDDINGS_ANSWER_CHECK):
        assert states[name]["state"] == "error", name
        assert "deadline exceeded" in states[name]["detail"]
    assert "/v1/completions" not in ep.calls and "/v1/embeddings" not in ep.calls


def test_the_readiness_wait_stops_at_the_deadline():
    clock = _Clock()
    slept = []

    def sleep(s):
        slept.append(s)
        clock.t += s

    ep = _Scripted({"/v1/models": (503, "loading")})
    out = ec.run_external_checks(_instance(), {"image": "vastai/vllm", "env": ""}, [],
                                 endpoint_factory=_factory(ep), capabilities={"chat", "completions"},
                                 ready_timeout=1800, sleep=sleep, deadline=100, clock=clock)
    assert sum(slept) <= 100
    states = {r["name"]: r["state"] for r in _checks(out)}
    assert states == {"contract": "error", ec.CHAT_CHECK: "error", ec.COMPLETIONS_CHECK: "error"}


def test_ready_timeout_still_applies_inside_the_deadline():
    clock = _Clock()
    ep = _Scripted({"/v1/models": (503, "loading")})
    out = ec.run_external_checks(_instance(), {"image": "vastai/vllm", "env": ""}, [],
                                 endpoint_factory=_factory(ep), capabilities={"chat"},
                                 ready_timeout=30, sleep=lambda s: setattr(clock, "t", clock.t + s),
                                 deadline=1000, clock=clock)
    assert _checks(out)[0]["state"] == "failed" and "not ready after 30s" in _checks(out)[0]["detail"]


def test_each_request_timeout_is_capped_by_the_time_left():
    clock = _Clock()
    timeouts = []

    def net(req, timeout, phase):
        timeouts.append(timeout)
        clock.t += 10
        phase.connected = True
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b"{}"))

    ec.run_external_checks(_instance(), {"image": "vastai/vllm", "env": ""}, SERVING_PASSED,
                           endpoint_factory=lambda h, p, t: ec.Endpoint(h, p, t, opener=net),
                           capabilities={"chat"}, deadline=50, clock=clock)
    assert timeouts and timeouts[0] == 50 and all(t <= 50 for t in timeouts)


def test_comfy_workflows_left_at_the_deadline_are_named_errors():
    clock = _Clock()

    class Slow(_FakeWrapper):
        def call_raw_with_headers(self, *a, **k):
            clock.t += 100
            return super().call_raw_with_headers(*a, **k)

    files = {**_payload("a", T2I), **_payload("b", T2I)}
    out = {r["name"]: r for r in ec.check_comfyui_workflows(
        Slow({"a": _result(("9", _png())), "b": _result(("9", _png()))}), _FakeJupyter(files),
        comfy=_FakeComfy(), deadline=ec.contract.Deadline(50, clock))}
    assert out["external/comfyui/a"]["state"] == "passed"
    assert out["external/comfyui/b"]["state"] == "error"
    assert "deadline exceeded" in out["external/comfyui/b"]["detail"]


@pytest.mark.contract
def test_the_contract_client_gets_the_runs_deadline():
    clients, timeouts = [], []

    def unreachable(req, timeout, phase):
        timeouts.append(timeout)
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    def factory(base, token):
        clients.append(cc.Client(base, token, opener=unreachable))
        return clients[-1]

    clock = _Clock()
    out = ec.run_external_checks(
        _instance(), {"image": "vastai/vllm", "env": "-e VLLM_MODEL=m"}, SERVING_PASSED,
        endpoint_factory=_factory(_Scripted({"/v1/models": MODELS})),
        contract_client_factory=factory, capabilities="chat", deadline=60, clock=clock)
    (cli,) = clients
    assert cli.deadline is not None and cli.deadline.seconds == 60
    assert timeouts == [60]                       # 120s default, capped by the time left
    assert {r["name"]: r["state"] for r in out}["contract/identity"] == "error"


@pytest.mark.contract
def test_a_contract_the_deadline_left_no_time_for_is_a_named_error():
    clients = []
    clock = _Clock()

    def chat(body):
        clock.t += 1                               # never reached: the contract runs first
        return _chat_ok(body)

    out = ec.run_external_checks(
        _instance(), {"image": "vastai/vllm", "env": "-e VLLM_MODEL=m"}, SERVING_PASSED,
        endpoint_factory=_factory(_Scripted({"/v1/models": MODELS, "/v1/chat/completions": chat})),
        contract_client_factory=lambda b, t: clients.append(1),
        capabilities="chat", deadline=0, clock=clock)
    states = {r["name"]: r["state"] for r in out}
    assert clients == [] and states["contract"] == "error" and states[ec.CHAT_CHECK] == "error"


# ---------------------------------------------------------------- a refused redirect

@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_a_redirect_on_undeclared_chat_is_an_error_not_a_refusal(status):
    r = ec.check_chat_refused(_Scripted({"/v1/models": MODELS,
                                         "/v1/chat/completions": (status, "")}))
    assert r["state"] == "error", r


def test_a_refused_cross_origin_redirect_reaches_the_guard_as_an_error(plain_http):
    """Over a real socket: chat redirects to another port. The transport refuses to
    follow it (the token would go elsewhere) and the guard cannot call that a pass."""
    other, seen_other = _serve({"/v1/chat/completions": (200, {"choices": []})})

    class H(BaseHTTPRequestHandler):
        def _send(self, status, body=b"", location=None):
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            self._send(200, json.dumps(MODELS[1]).encode())

        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            # 302: urllib's stock handler would follow this one (as a GET), so only
            # the transport's own redirect rule keeps the token from the other port.
            self._send(302, location=f"http://127.0.0.1:{other.server_port}/v1/chat/completions")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    r = ec.check_chat_refused(ec.Endpoint("127.0.0.1", srv.server_port, "tok"))
    srv.shutdown()
    other.shutdown()
    assert r["state"] == "error" and "302" in r["detail"], r
    assert seen_other == []


# ---------------------------------------------------------------- a check that raises

def _boom(*a, **k):
    raise RuntimeError("unexpected shape")


def test_a_check_that_raises_is_one_named_error_and_the_rest_still_run(monkeypatch):
    monkeypatch.setattr(ec, "check_completions", _boom)
    routes = dict(EMBED_ROUTES)
    routes["/v1/chat/completions"] = _chat_ok_any
    out = _run_ep(_Scripted(routes), {"chat", "completions", "embeddings"})
    r = out[ec.COMPLETIONS_CHECK]
    assert r["state"] == "error" and "RuntimeError: unexpected shape" in r["detail"]
    assert out[ec.CHAT_CHECK]["state"] == "passed"
    assert out[ec.EMBEDDINGS_CHECK]["state"] == "passed"


def test_a_multi_result_check_that_raises_is_one_error_under_its_structural_name(monkeypatch):
    monkeypatch.setattr(ec, "check_embeddings", _boom)
    out = ec.run_external_checks(_instance(), {"image": "vastai/vllm", "env": ""}, SERVING_PASSED,
                                 endpoint_factory=_factory(_Scripted(EMBED_ROUTES)),
                                 capabilities={"embeddings"})
    names = [r["name"] for r in out if r["name"].startswith("external/embeddings")]
    assert names == [ec.EMBEDDINGS_CHECK]
    assert {r["name"]: r for r in out}[ec.UNDECLARED_CHAT_CHECK]["state"] == "passed"


def test_a_contract_that_raises_is_one_error_named_contract(monkeypatch):
    monkeypatch.setattr(ec, "check_contract", _boom)
    out = _run_ep(_Scripted({"/v1/models": MODELS, "/v1/chat/completions": _chat_ok}), {"chat"})
    assert out["contract"]["state"] == "error" and "RuntimeError" in out["contract"]["detail"]
    assert out[ec.CHAT_CHECK]["state"] == "passed"


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
def test_an_interrupt_still_stops_the_run(monkeypatch, exc):
    def stop(*a, **k):
        raise exc()
    monkeypatch.setattr(ec, "check_chat", stop)
    with pytest.raises(exc):
        _run_ep(_Scripted({"/v1/models": MODELS}), {"chat"})


def test_a_workflow_that_raises_is_one_error_and_the_others_still_run():
    class Flaky(_FakeWrapper):
        def call_raw_with_headers(self, path, data, ctype, headers, timeout=None, binary=False):
            if json.loads(data)["input"]["name"] == "a":
                raise ValueError("wrapper client broke")
            return super().call_raw_with_headers(path, data, ctype, headers, timeout, binary)

    files = {**_payload("a", T2I), **_payload("b", T2I)}
    out = ec.check_comfyui_workflows(Flaky({"b": _result(("9", _png()))}), _FakeJupyter(files),
                                     comfy=_FakeComfy())
    a = [r for r in out if r["name"].startswith("external/comfyui/a")]
    assert len(a) == 1 and a[0]["state"] == "error" and "ValueError" in a[0]["detail"]
    assert {r["name"]: r["state"] for r in out}["external/comfyui/b/SaveImage#9/1"] == "passed"


def test_a_workflow_that_raises_part_way_leaves_no_half_written_results():
    """Undecodable output data raises after the workflow's own result was written:
    the workflow must not read as passed beside its error."""
    bad = (200, {"status": "completed", "output": [{"filename": "f", "node_id": "9",
                                                     "data": "not base64!"}]})
    out = ec.check_comfyui_workflows(_FakeWrapper({"t2i": bad}), _FakeJupyter(_payload("t2i", T2I)),
                                     comfy=_FakeComfy())
    assert [(r["name"], r["state"]) for r in out] == [("external/comfyui/t2i", "error")]


def test_a_node_check_that_raises_is_one_error_and_the_other_nodes_still_run(monkeypatch):
    real = ec._check_node

    def check(wf, node_id, cls, kind, wants_audio, files):
        if node_id == "20":
            raise IndexError("odd file")
        return real(wf, node_id, cls, kind, wants_audio, files)
    monkeypatch.setattr(ec, "_check_node", check)
    wf = _wf(**{"9": ("SaveImage", {}), "20": ("SaveVideo", {})})
    out = _check({"both": wf}, {"both": _result(("9", _png()), ("20", MP4))})
    assert out["external/comfyui/both/SaveVideo#20"]["state"] == "error"
    assert out["external/comfyui/both/SaveImage#9/1"]["state"] == "passed"


def test_the_comfyui_step_that_raises_is_one_named_error():
    class Broken(_FakeJupyter):
        def list_dir(self, path):
            raise TypeError("bad listing")

    out = ec.run_external_checks(
        _comfy_instance(), {"image": "vastai/comfy", "env": f'-e PORTAL_CONFIG="{COMFY_PORTAL}"'},
        [{"name": "comfyui.d/10-comfyui-serving", "state": "passed"}], token="tok",
        endpoint_factory=lambda h, p, t: _Both({}), jupyter_factory=lambda h, p, t: Broken({}),
        capabilities={"comfyui-workflows"})
    r = {x["name"]: x for x in out}["external/comfyui"]
    assert r["state"] == "error" and "TypeError" in r["detail"]


# ---------------------------------------------------------------- prompt-roundtrip over SSH

class _UnreachableBox(_FakeRemote):
    def run(self, script, python="python3"):
        return False, "SSH not accepted after 12 attempts: Permission denied (publickey)"


@pytest.mark.contract
def test_an_ssh_failure_in_prompt_roundtrip_is_an_error_not_a_skip():
    _FakeRemote.instances = []
    env = '-e VLLM_MODEL=Qwen/Qwen3.5-0.8B -e VLLM_ARGS="--max-model-len 4096"'
    out = ec.run_external_checks(
        _instance(), {"image": "vastai/vllm", "env": env}, SERVING_PASSED,
        endpoint_factory=_factory(_Scripted({"/v1/models": MODELS})),
        contract_client_factory=lambda b, t: _ContractEngine(),
        remote_factory=_UnreachableBox, capabilities="chat")
    r = {x["name"]: x for x in out}["contract/prompt-roundtrip"]
    assert r["state"] == "error" and "SSH not accepted" in r["detail"]


@pytest.mark.contract
def test_without_a_remote_factory_prompt_roundtrip_is_not_applicable():
    env = '-e VLLM_MODEL=Qwen/Qwen3.5-0.8B -e VLLM_ARGS="--max-model-len 4096"'
    out = ec.run_external_checks(
        _instance(), {"image": "vastai/vllm", "env": env}, SERVING_PASSED,
        endpoint_factory=_factory(_Scripted({"/v1/models": MODELS})),
        contract_client_factory=lambda b, t: _ContractEngine(), capabilities="chat")
    assert {x["name"]: x["state"] for x in out}["contract/prompt-roundtrip"] == "skipped"


class _BoxWithDeadline(_FakeRemote):
    deadline = None

    def run(self, script, python="python3"):
        _BoxWithDeadline.seen = self.deadline
        return super().run(script, python)


@pytest.mark.contract
def test_the_ssh_session_gets_the_runs_deadline():
    _FakeRemote.instances = []
    env = '-e VLLM_MODEL=Qwen/Qwen3.5-0.8B -e VLLM_ARGS="--max-model-len 4096"'
    ec.run_external_checks(
        _instance(), {"image": "vastai/vllm", "env": env}, SERVING_PASSED,
        endpoint_factory=_factory(_Scripted({"/v1/models": MODELS})),
        contract_client_factory=lambda b, t: _ContractEngine(),
        remote_factory=_BoxWithDeadline, capabilities="chat", deadline=900)
    assert _BoxWithDeadline.seen is not None and _BoxWithDeadline.seen.seconds == 900


def test_the_real_session_stops_at_the_runs_deadline():
    """remote.RemoteSession honours the deadline the run hands it: once it has
    passed, nothing is attached and the reason names the deadline."""
    import remote
    clock = _Clock()
    dl = ec.contract.Deadline(10, clock)
    calls = []

    class API:
        def _request_safe(self, *a, **k):
            calls.append(a)
            return {"success": True, "key": '{"id": 1}'}

        def get_instance(self, *a, **k):
            return {}

    lazy = ec._LazySession(lambda: remote.RemoteSession(API(), 5555), dl)
    clock.t = 11
    ok, why = lazy.run("print(1)")
    lazy.close()
    assert not ok and "deadline exceeded" in why and calls == []


# ---------------------------------------------------------------- each engine's *_TEST_ENDPOINT

@pytest.mark.parametrize("image,variable", [
    ("vastai/vllm", "VLLM_TEST_ENDPOINT"), ("vastai/vllm-omni:v0.30.0", "VLLM_TEST_ENDPOINT"),
    ("vastai/sglang", "SGLANG_TEST_ENDPOINT"), ("vastai/llama-cpp:b10840", "LLAMA_TEST_ENDPOINT"),
])
def test_each_engine_s_test_endpoint_none_is_a_declaration_error(image, variable):
    """The same rule as vLLM's, read from the variable that engine's serving test
    reads: chat turned off in the image, nothing declared instead."""
    _, _, problems = ec.resolve_capabilities({"image": image, "env": f"-e {variable}=none"})
    assert problems and problems[0].startswith(f"{variable}=none")
    _, _, problems = ec.resolve_capabilities({"image": image, "env": ""}, [f"{variable}=none"])
    assert problems, "an --env override must count too"
    _, _, problems = ec.resolve_capabilities({"image": image, "env": f"-e {variable}=none"},
                                             [f"{variable}=chat"])
    assert not problems, "an --env override must win over the template"


def test_another_engine_s_variable_is_ignored():
    """VLLM_TEST_ENDPOINT does nothing in an SGLang image, so it is no declaration."""
    _, _, problems = ec.resolve_capabilities({"image": "vastai/sglang",
                                              "env": "-e VLLM_TEST_ENDPOINT=none"})
    assert not problems


# ---------------------------------------------------------------- --env overrides win

def test_an_env_override_of_speech_kind_wins_over_the_template():
    ep = _tts_ep()
    out = ec.run_external_checks(
        _instance(), {"image": "vastai/vllm", "env": "-e OPENAI_ROUTES=/v1/audio/speech"},
        SERVING_PASSED, env_overrides=["SPEECH_KIND=music"], endpoint_factory=_factory(ep))
    assert {x["name"]: x["state"] for x in out}[ec.TTS_CHECK] == "skipped"
    out = ec.run_external_checks(
        _instance(), {"image": "vastai/vllm",
                      "env": "-e OPENAI_ROUTES=/v1/audio/speech -e SPEECH_KIND=music"},
        SERVING_PASSED, env_overrides=["SPEECH_KIND=speech"], endpoint_factory=_factory(_tts_ep()))
    assert {x["name"]: x["state"] for x in out}[ec.TTS_CHECK] == "passed"


def test_an_env_override_of_portal_config_picks_the_api_port():
    seen = []

    def factory(host, port, token):
        seen.append(port)
        return _Scripted({"/v1/models": MODELS, "/v1/chat/completions": _chat_ok_any,
                          "/v1/completions": (200, {"choices": [{"text": "x"}],
                                                    "usage": {"completion_tokens": 1}})})

    inst = {"public_ipaddr": "203.0.113.7",
            "ports": {"8000/tcp": [{"HostPort": "40000"}], "9000/tcp": [{"HostPort": "49000"}]}}
    ec.run_external_checks(inst, {"image": "vastai/vllm", "env": f'-e PORTAL_CONFIG="{PORTAL}"'},
                           SERVING_PASSED, env_overrides=["PORTAL_CONFIG=localhost:9000:18000:/:API"],
                           endpoint_factory=factory)
    assert seen == ["49000"]


def test_an_env_override_of_the_comfyui_generate_timeout_is_used():
    wrap = _Both({"t2i": _result(("9", _png()))})
    ec.run_external_checks(
        _comfy_instance(), {"image": "vastai/comfy",
                            "env": f'-e PORTAL_CONFIG="{COMFY_PORTAL}" -e COMFYUI_GENERATE_TIMEOUT=600'},
        [{"name": "comfyui.d/10-comfyui-serving", "state": "passed"}], token="tok",
        env_overrides=["COMFYUI_GENERATE_TIMEOUT=2400"],
        endpoint_factory=lambda h, p, t: wrap,
        jupyter_factory=lambda h, p, t: _FakeJupyter(_payload("t2i", T2I)),
        capabilities={"comfyui-workflows"})
    assert [p[3] for p in wrap.posted] == [2400]


# ---------------------------------------------------------------- --capabilities at the door

def test_an_unknown_capability_fails_argument_handling_listing_the_valid_names(monkeypatch, capsys):
    import test_template as tt
    monkeypatch.setattr(sys, "argv", ["test_template.py", "abc123", "--capabilities", "chat,vison"])
    with pytest.raises(SystemExit) as e:
        tt.parse_args()
    assert e.value.code == tt.EXIT_CONFIG_ERROR
    captured = capsys.readouterr()
    assert "unknown capability vison" in captured.err
    for name in ec.KNOWN_CAPABILITIES:
        assert name in captured.err
    assert json.loads(captured.out)["state"] == "config_error"


def test_known_capabilities_pass_argument_handling_unchanged(monkeypatch):
    import test_template as tt
    monkeypatch.setattr(sys, "argv", ["test_template.py", "abc123", "--capabilities",
                                      "chat, embeddings"])
    assert tt.parse_args().capabilities == "chat, embeddings"


def test_an_unknown_capability_stops_the_launcher_before_any_api_call(monkeypatch):
    import test_template as tt
    monkeypatch.setattr(sys, "stderr", sys.stderr)          # main() wraps it; restore after
    monkeypatch.setattr(sys, "argv", ["test_template.py", "abc123", "--api-key", "k",
                                      "--capabilities", "specch"])
    monkeypatch.setattr(tt, "VastAPI", lambda *a, **k: pytest.fail("an API client was built"))
    with pytest.raises(SystemExit) as e:
        tt.main()
    assert e.value.code == tt.EXIT_CONFIG_ERROR


# ---------------------------------------------------------------- per-port defaults

@pytest.mark.parametrize("internal,public", [(18000, 8000), (18288, 8288), (18188, 8188),
                                             (17860, ec.DEFAULT_PUBLIC_API_PORT)])
def test_each_internal_port_has_its_own_default_public_port(internal, public):
    """Without a PORTAL_CONFIG entry for it, the wrapper and ComfyUI are looked for on
    their own conventional ports, not the engine's 8000."""
    assert ec.public_api_port(None, internal) == public
    assert ec.public_api_port("localhost:1111:11111:/:Instance Portal", internal) == public


def test_a_portal_config_entry_still_beats_the_default():
    assert ec.public_api_port("localhost:9288:18288:/:Wrapper", 18288) == 9288
    # Two entries for one internal port: the first one wins, as before.
    assert ec.public_api_port("localhost:9000:18000:/:A|localhost:9001:18000:/:B") == 9000


def test_a_comfyui_template_without_portal_config_finds_its_default_ports():
    seen = []

    def ep_factory(host, port, token):
        seen.append(port)
        return _Both({"t2i": _result(("9", _png()))})

    out = ec.run_external_checks(
        _comfy_instance(), {"image": "vastai/comfy", "env": ""},
        [{"name": "comfyui.d/10-comfyui-serving", "state": "passed"}], token="tok",
        endpoint_factory=ep_factory, jupyter_factory=lambda h, p, t: _FakeJupyter(_payload("t2i", T2I)),
        capabilities={"comfyui-workflows"})
    assert seen == ["40188", "40288"]
    assert {r["name"]: r["state"] for r in out}["external/comfyui/t2i/SaveImage#9/1"] == "passed"


# ---------------------------------------------------------------- the guard runs last of all

class _Ordered(_Both):
    """Engine and ComfyUI in one fake, recording the order of every request."""

    def __init__(self, results, log):
        super().__init__(results)
        self.log = log
        self.routes = {"/v1/models": (200, {"data": [{"id": "m"}]}),
                       "/v1/embeddings": _embed_ok}

    def call(self, path, body=None):
        self.log.append(path)
        if path == "/object_info":
            return _Both.call(self, path, body)
        route = self.routes.get(path, (404, {"detail": "Not Found"}))
        return route(body) if callable(route) else route

    def call_raw_with_headers(self, path, *a, **k):
        self.log.append(path)
        return super().call_raw_with_headers(path, *a, **k)


def _engine_and_comfy_instance():
    inst = _comfy_instance()
    inst["ports"]["8000/tcp"] = [{"HostPort": "40000"}]
    return inst


def test_the_undeclared_chat_guard_is_the_last_request_of_the_whole_phase():
    """Probing an undeclared route can take a server down; ComfyUI's workflows must
    have had their turn before it."""
    log = []
    ep = _Ordered({"t2i": _result(("9", _png()))}, log)
    out = ec.run_external_checks(
        _engine_and_comfy_instance(), {"image": "vastai/vllm", "env": f'-e PORTAL_CONFIG="{COMFY_PORTAL}"'},
        SERVING_PASSED, token="tok", endpoint_factory=lambda h, p, t: ep,
        jupyter_factory=lambda h, p, t: _FakeJupyter(_payload("t2i", T2I)),
        capabilities={"embeddings", "comfyui-workflows"})
    assert log[-1] == "/v1/chat/completions"
    assert log.index("/generate/sync") < log.index("/v1/chat/completions")
    assert out[-1]["name"] == ec.UNDECLARED_CHAT_CHECK and out[-1]["state"] == "passed"


def test_the_guard_still_runs_when_comfyui_is_not_ready():
    class NeverReady(_Ordered):
        def call(self, path, body=None):
            if path == "/health":
                self.log.append(path)
                return 503, "starting"
            return super().call(path, body)

    log = []
    out = ec.run_external_checks(
        _engine_and_comfy_instance(), {"image": "vastai/vllm", "env": f'-e PORTAL_CONFIG="{COMFY_PORTAL}"'},
        [], token="tok", endpoint_factory=lambda h, p, t: NeverReady({}, log),
        jupyter_factory=lambda h, p, t: _FakeJupyter(_payload("t2i", T2I)),
        capabilities={"embeddings", "comfyui-workflows"}, ready_timeout=30, sleep=lambda s: None)
    states = {r["name"]: r["state"] for r in out}
    assert states["external/comfyui"] == "failed"
    assert states[ec.UNDECLARED_CHAT_CHECK] == "passed" and out[-1]["name"] == ec.UNDECLARED_CHAT_CHECK


def test_an_env_override_of_provisioning_comfyui_workflows_wins():
    caps, source, _ = ec.resolve_capabilities({"image": "vastai/pytorch", "env": ""},
                                              ["PROVISIONING_COMFYUI_WORKFLOWS=a.json"])
    assert caps == {"comfyui-workflows"} and source == "ComfyUI image"
