"""Checks the launcher runs from OUTSIDE the instance (ADR 0059, D1).

The in-image serving tests send inference to the engine's internal port. These send
it through the instance's public endpoint — Caddy, the instance token and the mapped
port, the path a user's client takes. They run once the in-image serving test has
run (passed or failed); an image with no serving test is waited on here instead.

What is checked comes from the template's capabilities (resolve_capabilities), in
this order: an explicit --capabilities override; the template's OPENAI_ROUTES (an
--env override wins); a ComfyUI image or PROVISIONING_COMFYUI_WORKFLOWS, meaning
`comfyui-workflows`; an OpenAI engine image (vLLM, vLLM-Omni, SGLang, llama.cpp)
with nothing declared, meaning pyworker's default of completions and chat; anything
else gets no external checks. The first result is always `external/capabilities`,
saying which and why.

What runs, for an engine image: the OpenAI contract (contract.py, `contract/<check>`),
then one check per declared capability — chat, completions, embeddings,
transcriptions, speech — each structural, some with an answer check beside it
(embedding ranking, transcript accuracy, speech-like audio). A declared capability
with no check yet is skipped, never passed. `comfyui-workflows` runs every provisioned
workflow and checks every output node. When chat is NOT declared, the
`external/undeclared/chat` guard runs last of everything: chat must be refused.

States: passed, failed, skipped, info, and error — the check could not be run (no
HTTP response, an unexpected exception, the deadline) — plus the contract's
deviation and advisory. Every result is advisory: it never changes the run's state
or exit code until `require_tests` names it (ADR 0059, D7 step 3).

One deadline (EXTERNAL_DEADLINE) covers the readiness wait, the contract and every
check; checks it leaves no time for are reported by name as errors.

Structural checks assert status codes, response shape and tokens produced (ADR
0031); answer checks are the model-library bar (ADR 0059, D4).
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import struct
import urllib.parse
import wave
import zlib

try:                                    # a sibling module; loaded by path when needed
    import contract
except ImportError:                     # pragma: no cover - imported by path from elsewhere
    import importlib.util as _ilu
    _cspec = _ilu.spec_from_file_location(
        "contract", os.path.join(os.path.dirname(os.path.abspath(__file__)), "contract.py"))
    contract = _ilu.module_from_spec(_cspec)
    _cspec.loader.exec_module(contract)
import re
import uuid
import time
import urllib.error

# The check names the launcher reports. Prefixed so they can never collide with an
# in-image test name (`base/..`, `vllm.d/..`).
CHAT_CHECK = "external/chat-inference"

# The in-image serving test. The external check runs once one of these has RUN,
# passed or failed: that test bundles readiness, log scans, port exposure and
# inference, so it can fail on a log line while the engine serves correctly — the
# case a side-by-side comparison exists to see. If it never ran (skipped, or the
# suite died before it), the engine was never shown to start.
_SERVING_TEST = re.compile(r"^(vllm|vllm-omni|sglang|llama|comfyui)\.d/10-[a-z-]+-serving$")

# The engine's internal port, and the public (Caddy) port fronting it when
# PORTAL_CONFIG does not say otherwise.
ENGINE_INTERNAL_PORT = 18000
DEFAULT_PUBLIC_API_PORT = 8000
# The public port Caddy fronts each internal port on by convention, used when
# PORTAL_CONFIG has no entry for that internal port: the engine API, the ComfyUI API
# wrapper and ComfyUI itself. Any other internal port falls back to
# DEFAULT_PUBLIC_API_PORT, as every port did before this table.
DEFAULT_PUBLIC_PORTS = {18000: 8000, 18288: 8288, 18188: 8188}

CHAT_PROMPTS = (
    "Say hello in one sentence.",
    "What is 2+2? Reply with just the number.",
)
REQUEST_TIMEOUT = 300
CONNECT_RETRIES = 2


READY_TIMEOUT = 1800
READY_POLL = 15
# One overall budget for the external phase: readiness wait, every check and the
# contract. It bounds a run whose instance hangs; the in-image suite's own budget
# (test_template.DEFAULT_TEST_TIMEOUT) is the same two hours.
EXTERNAL_DEADLINE = 7200


def has_serving_test(tests):
    """True when the image ships an engine serving test at all (any state). Old
    images do not — vastai/comfy:v0.18.2 has no comfyui.d suite — and then the
    external checks are the only checks the template gets."""
    return any(_SERVING_TEST.match(t.get("name", "")) for t in tests or ())


def wait_ready(probe, timeout=READY_TIMEOUT, poll=READY_POLL, sleep=time.sleep, deadline=None):
    """Poll probe() -> (ready, detail) until ready or timeout. (ready, detail, waited_s).

    With a `deadline` (contract.Deadline) it also stops when that passes, and never
    sleeps past it."""
    waited, detail = 0, "not polled"
    while True:
        ready, detail = probe()
        if ready or waited >= timeout or (deadline is not None and deadline.expired()):
            return ready, detail, waited
        pause = poll if deadline is None else min(poll, deadline.remaining())
        sleep(pause)
        waited += pause


def _deadline_result(name, deadline):
    """A check the overall deadline left no time for: not run, so `error`."""
    return {"name": name, "state": "error",
            "detail": f"{contract.DEADLINE_EXCEEDED}: the external checks' overall deadline "
                      f"({deadline.seconds:g}s) passed before this check ran"}


def _guarded(name, step):
    """step()'s results; an unexpected exception becomes ONE `error` result named
    `name`, so one broken check never costs the others their result. Only Exception
    is caught: KeyboardInterrupt and SystemExit still stop the run."""
    try:
        return step()
    except Exception as e:                                        # noqa: BLE001
        return [{"name": name, "state": "error",
                 "detail": f"the check raised {type(e).__name__}: {str(e)[:300]}"}]


def _attach_deadline(client, deadline):
    """Give a transport the run's deadline. Test fakes without one are left alone."""
    if hasattr(client, "deadline"):
        client.deadline = deadline
    return client


def serving_test_ran(tests):
    """The in-image serving test that ran (passed or failed), or None."""
    for t in tests or ():
        if _SERVING_TEST.match(t.get("name", "")) and t.get("state") in ("passed", "failed"):
            return t["name"]
    return None


def _env_value(env_str, name):
    """Value of `-e NAME=...` in a template env string (quoted or bare)."""
    m = re.search(rf'(?:^|\s)-e\s+{re.escape(name)}=("([^"]*)"|\'([^\']*)\'|(\S+))', env_str or "")
    if not m:
        return None
    return next(g for g in m.groups()[1:] if g is not None)


# The variable each engine image's in-image serving test reads to skip its chat
# inference (`chat` or `none`).
TEST_ENDPOINT_VARS = {
    "vastai/vllm": "VLLM_TEST_ENDPOINT",
    "vastai/vllm-omni": "VLLM_TEST_ENDPOINT",
    "vastai/sglang": "SGLANG_TEST_ENDPOINT",
    "vastai/llama-cpp": "LLAMA_TEST_ENDPOINT",
}


def endpoint_test_var(image):
    """The *_TEST_ENDPOINT variable an image's serving test reads (vLLM's by default)."""
    return TEST_ENDPOINT_VARS.get(str(image or "").split(":", 1)[0], "VLLM_TEST_ENDPOINT")


def chat_disabled(template_env, env_overrides=(), variable="VLLM_TEST_ENDPOINT"):
    """True when the template says it does not chat (`variable`=none, the engine's
    *_TEST_ENDPOINT; an --env override beats the template).

    Mirrors the in-image serving test, so the two run on the same templates while
    they are compared. Capability-keyed selection replaces this (ADR 0059, D2).
    """
    value = _env_with_overrides(template_env, env_overrides, variable)
    return (value or "chat").strip().lower() == "none"


def public_api_port(portal_config, internal_port=ENGINE_INTERNAL_PORT):
    """The container port Caddy serves the engine API on, from PORTAL_CONFIG.

    PORTAL_CONFIG entries are `host:public_port:internal_port:path:name`, joined by
    `|`. The first entry whose internal port matches gives the public port. With no
    such entry (or none parseable), the default is DEFAULT_PUBLIC_PORTS for that
    internal port — 8000 for the engine, 8288 for the ComfyUI wrapper, 8188 for
    ComfyUI — and DEFAULT_PUBLIC_API_PORT for any other.
    """
    for entry in (portal_config or "").split("|"):
        parts = entry.split(":")
        if len(parts) >= 3 and parts[2].strip() == str(internal_port):
            try:
                return int(parts[1])
            except ValueError:
                continue
    return DEFAULT_PUBLIC_PORTS.get(int(internal_port), DEFAULT_PUBLIC_API_PORT)


def host_port(instance, container_port):
    """The host port Docker mapped `container_port/tcp` to, or None."""
    mappings = (instance.get("ports") or {}).get(f"{container_port}/tcp")
    if isinstance(mappings, list) and mappings:
        return mappings[0].get("HostPort")
    return None


def _no_response(name, what, body, **extra):
    """The result for a request that got no HTTP response at all (status 0:
    unreachable, timed out, the deadline passed). The check could not be run, so it
    is `error` — never a pass, and never a failure of the template."""
    return {"name": name, "state": "error", **extra,
            "detail": f"{what}: no HTTP response ({str(body)[:200]})"}


def _no_response_pair(structural, answer, what, body):
    """_no_response for a structural check and the answer check that depends on it."""
    return [_no_response(structural, what, body, kind="structural"),
            {"name": answer, "state": "skipped", "kind": "answer",
             "detail": "structural check could not run"}]


class Endpoint(contract.Transport):
    """The engine API as a user's client reaches it: public IP, mapped port, token.

    The transport is contract.Transport: https first (Caddy may serve TLS with a
    self-signed certificate), http only when the port shows it does not speak TLS,
    the scheme pinned once it answers, connection failures retried CONNECT_RETRIES
    times and nothing re-sent once the request may have been delivered, and each
    request's timeout capped by `deadline` when one is set.
    """

    def __init__(self, host, port, token, timeout=REQUEST_TIMEOUT, *, sleep=time.sleep,
                 deadline=None, opener=None):
        super().__init__(f"{host}:{port}", token, timeout, retries=CONNECT_RETRIES,
                         sleep=sleep, deadline=deadline, opener=opener)
        self.extra_headers = {}

    def call(self, path, body=None):
        """(status, parsed-or-text) for a JSON request (GET when body is None).
        status 0 means no HTTP response at all (unreachable, timed out, the response
        lost mid-body, or the deadline passed)."""
        data = json.dumps(body).encode() if body is not None else None
        return self.call_raw(path, data, "application/json")

    def call_raw_with_auth(self, path, authorization):
        """GET with a custom Authorization header (Jupyter uses `token <t>`)."""
        return self.call_raw_with_headers(path, None, "application/json",
                                          {"Authorization": authorization})

    def call_raw_with_headers(self, path, data, content_type, headers, timeout=None,
                              binary=False):
        """call_raw with extra headers and an optional per-request timeout."""
        saved = (self.extra_headers, self.timeout)
        self.extra_headers = headers
        if timeout:
            self.timeout = timeout
        try:
            return self.call_raw(path, data, content_type, binary=binary)
        finally:
            self.extra_headers, self.timeout = saved

    def call_raw(self, path, data, content_type, binary=False):
        """(status, parsed-or-text) for a request with a prebuilt body (POST when
        data is not None), e.g. multipart/form-data. With binary=True a successful
        response body is returned as bytes, unparsed (audio, images)."""
        r, err = self.open(path, data, {"Content-Type": content_type, **self.extra_headers})
        if r is None:
            return 0, err
        try:
            with r:
                raw = r.read()
        except Exception as e:  # noqa: BLE001 — the body was lost: no usable response
            return 0, f"{type(e).__name__} reading the response: {e}"
        if isinstance(r, urllib.error.HTTPError):
            return r.code, raw.decode(errors="replace")[:500]
        if binary:
            return r.status, raw
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw.decode(errors="replace")


def check_chat(endpoint):
    """Structural chat check through the public endpoint.

    Passes when /v1/models names a model and every prompt returns HTTP 200 with a
    choice, an assistant message, and at least one completion token — the same bar
    as the in-image serving test ("produced completion tokens").
    """
    detail = []
    status, models = endpoint.call("/v1/models")
    if status == 0:
        return _no_response(CHAT_CHECK, "/v1/models", models)
    if status != 200 or not isinstance(models, dict) or not models.get("data"):
        return {"name": CHAT_CHECK, "state": "failed",
                "detail": f"/v1/models returned HTTP {status}: {str(models)[:200]}"}
    model = models["data"][0].get("id")
    detail.append(f"model {model}")
    for prompt in CHAT_PROMPTS:
        status, r = endpoint.call("/v1/chat/completions", {
            "model": model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 32, "temperature": 0})
        if status == 0:
            return _no_response(CHAT_CHECK, "chat", r)
        if status != 200 or not isinstance(r, dict):
            return {"name": CHAT_CHECK, "state": "failed",
                    "detail": f"chat returned HTTP {status}: {str(r)[:200]}"}
        choices = r.get("choices") or []
        msg = choices[0].get("message") if choices else None
        tokens = (r.get("usage") or {}).get("completion_tokens") or 0
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            return {"name": CHAT_CHECK, "state": "failed",
                    "detail": f"no assistant message in the response: {str(r)[:200]}"}
        if tokens < 1:
            return {"name": CHAT_CHECK, "state": "failed",
                    "detail": f"no completion tokens produced (usage: {r.get('usage')})"}
        detail.append(f"{tokens} tokens")
    return {"name": CHAT_CHECK, "state": "passed", "detail": ", ".join(detail)}


def check_completions(endpoint):
    """Structural /v1/completions check: HTTP 200, a text choice, completion tokens.
    pyworker's default benchmark drives this route, so a template that serves it must
    answer it."""
    status, models = endpoint.call("/v1/models")
    if status == 0:
        return _no_response(COMPLETIONS_CHECK, "/v1/models", models)
    if status != 200 or not isinstance(models, dict) or not models.get("data"):
        return {"name": COMPLETIONS_CHECK, "state": "failed",
                "detail": f"/v1/models returned HTTP {status}: {str(models)[:200]}"}
    model = models["data"][0].get("id")
    status, r = endpoint.call("/v1/completions", {"model": model, "prompt": "The capital of France is",
                                                  "max_tokens": 8, "temperature": 0})
    if status == 0:
        return _no_response(COMPLETIONS_CHECK, "completions", r)
    if status != 200 or not isinstance(r, dict):
        return {"name": COMPLETIONS_CHECK, "state": "failed",
                "detail": f"completions returned HTTP {status}: {str(r)[:200]}"}
    choices = r.get("choices") or []
    tokens = (r.get("usage") or {}).get("completion_tokens") or 0
    if not choices or not isinstance(choices[0].get("text"), str):
        return {"name": COMPLETIONS_CHECK, "state": "failed",
                "detail": f"no text choice in the response: {str(r)[:200]}"}
    if tokens < 1:
        return {"name": COMPLETIONS_CHECK, "state": "failed",
                "detail": f"no completion tokens produced (usage: {r.get('usage')})"}
    return {"name": COMPLETIONS_CHECK, "state": "passed", "detail": f"model {model}, {tokens} tokens"}


# ---------------------------------------------------------------- non-chat checks

UNDECLARED_CHAT_CHECK = "external/undeclared/chat"
# The statuses that mean "this route is not served here" — the contract's NOT_OFFERED.
CHAT_REFUSED = contract.NOT_OFFERED
COMPLETIONS_CHECK = "external/completions"
EMBEDDINGS_CHECK = "external/embeddings"
EMBEDDINGS_ANSWER_CHECK = "external/embeddings-ranking"

# Capabilities are the routes a template serves, named after them (ADR 0059, D2).
# They come from the same declaration pyworker reads, OPENAI_ROUTES, so serverless and
# the tests can never disagree about what a template is. Routes and names mirror
# pyworker's workers/openai/routes.py.
ROUTE_CAPABILITIES = {
    "/v1/chat/completions": "chat",
    "/v1/completions": "completions",
    "/v1/embeddings": "embeddings",
    "/v1/audio/transcriptions": "transcriptions",
    "/v1/audio/speech": "speech",
    "/v1/chat/completions/batch": "chat-batch",
    "/v1/rerank": "rerank",
    "/v1/score": "score",
    "/v1/audio/speech/batch": "speech-batch",
    "/v1/audio/generate": "audio-generate",
    "/v1/audio/translations": "translations",
    "/v1/images/generations": "images",
    "/v1/images/edits": "image-edits",
    "/v1/videos/sync": "videos",
}
# pyworker serves these when OPENAI_ROUTES is unset (workers/openai/core.py DEFAULT_ROUTES).
PYWORKER_DEFAULT_ROUTES = ("/v1/completions", "/v1/chat/completions")
COMFYUI_CAPABILITY = "comfyui-workflows"
# Images that run an OpenAI-compatible engine. pyworker's default routes only make
# sense for these; any other image (base-image, PyTorch, desktops, apps) has no
# engine to check, and its in-image suite is the whole test.
OPENAI_ENGINE_IMAGES = ("vastai/vllm", "vastai/vllm-omni", "vastai/sglang", "vastai/llama-cpp")
# Unknown names are an error, not silently ignored: a typo must not turn a check off.
KNOWN_CAPABILITIES = tuple(ROUTE_CAPABILITIES.values()) + (COMFYUI_CAPABILITY,)
# Capabilities that have an external check today; the rest are reported as declared
# with no check yet, never as passed.
CHECKED_CAPABILITIES = ("chat", "completions", "embeddings", "transcriptions", "speech",
                        COMFYUI_CAPABILITY)

ASR_CHECK = "external/transcriptions"
ASR_ANSWER_CHECK = "external/transcriptions-accuracy"
# Speech we generated ourselves (VoxCPM2), committed with the probes.
ASR_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "probes", "fixtures", "voxcpm2-speech.wav")
ASR_REFERENCE = "Hello from Vast. The secret number is seven three one nine."
# Word error rate at or under this passes. The clip is short and clean, so a correct
# model is at or near 0; 0.25 allows two wrong words in this eleven-word sentence.
ASR_MAX_WER = 0.25

TTS_CHECK = "external/speech"
TTS_ANSWER_CHECK = "external/speech-audio"
TTS_TEXT = "Hello from Vast. This is a short test of text to speech."
# Speech runs at roughly 2.5 words per second; accept a wide band around that.
TTS_SECONDS_PER_WORD = (0.15, 1.5)
# 16-bit PCM RMS below this is silence or near-silence.
TTS_MIN_RMS = 100

EMBED_TEXTS = (
    "The cat sat on the warm windowsill in the sun.",
    "A kitten was lying on a sunny window ledge.",
    "Quarterly revenue grew because of strong GPU rental demand.",
)


def check_chat_refused(endpoint):
    """A template that does NOT declare chat must refuse it (ADR 0059, D4).

    Keeps the signal a plain skip loses: an engine that answers chat for a model
    that cannot chat is serving something other than what was declared. Structural:
    only a refusal that means "not served here" passes (CHAT_REFUSED: 400, 404, 422,
    501); a 2xx fails (chat is served though undeclared). Anything else says nothing
    about chat and is an error: 401/403 is an auth problem, 408/429 and the like are
    the request not being judged, a 5xx is the server breaking, and no response
    (status 0) is the check not running.
    """
    status, models = endpoint.call("/v1/models")
    if status == 0:
        return _no_response(UNDECLARED_CHAT_CHECK, "/v1/models", models, kind="structural")
    if status != 200 or not isinstance(models, dict) or not models.get("data"):
        return {"name": UNDECLARED_CHAT_CHECK, "state": "failed", "kind": "structural",
                "detail": f"/v1/models returned HTTP {status}: {str(models)[:200]}"}
    model = models["data"][0].get("id")
    status, r = endpoint.call("/v1/chat/completions", {
        "model": model, "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 1, "temperature": 0})
    if status == 0:
        return _no_response(UNDECLARED_CHAT_CHECK, "chat", r, kind="structural")
    if status in CHAT_REFUSED:
        return {"name": UNDECLARED_CHAT_CHECK, "state": "passed", "kind": "structural",
                "detail": f"chat refused with HTTP {status}"}
    if 200 <= status < 300:
        return {"name": UNDECLARED_CHAT_CHECK, "state": "failed", "kind": "structural",
                "detail": f"chat is not declared but returned HTTP {status}: {str(r)[:160]}"}
    return {"name": UNDECLARED_CHAT_CHECK, "state": "error", "kind": "structural",
            "detail": f"chat returned HTTP {status}, which is not a refusal "
                      f"({', '.join(map(str, CHAT_REFUSED))}): the guard could not decide: "
                      f"{str(r)[:160]}"}


def _cosine(a, b):
    num = sum(x * y for x, y in zip(a, b))
    den = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return num / den if den else 0.0


def _vectors(r, n):
    """The n embedding vectors in an OpenAI embeddings response, or an error string."""
    if not isinstance(r, dict):
        return f"not a JSON object: {str(r)[:160]}"
    data = r.get("data")
    if not isinstance(data, list) or len(data) != n:
        return f"expected {n} embeddings, got {len(data) if isinstance(data, list) else data!r}"
    vecs = []
    for d in sorted(data, key=lambda d: d.get("index", 0)):
        v = d.get("embedding") if isinstance(d, dict) else None
        if not isinstance(v, list) or not v or not all(isinstance(x, (int, float)) for x in v):
            return "an embedding is not a non-empty list of numbers"
        if any(x != x or x in (float("inf"), float("-inf")) for x in v):
            return "an embedding contains NaN or infinity"
        if not any(v):
            return "an embedding is all zeros"
        vecs.append(v)
    if len({len(v) for v in vecs}) != 1:
        return f"embeddings have different dimensions: {sorted({len(v) for v in vecs})}"
    return vecs


def check_embeddings(endpoint):
    """Embeddings checks: one structural, one answer check (ADR 0059, D4).

    Structural (can gate): HTTP 200, one vector per input, numbers only, a single
    dimension, no NaN/zero vectors, and the same input embeds to the same vector.
    Answer (model-library approval): a paraphrase ranks above an unrelated sentence.
    """
    def fail(detail):
        return [{"name": EMBEDDINGS_CHECK, "state": "failed", "kind": "structural",
                 "detail": detail},
                {"name": EMBEDDINGS_ANSWER_CHECK, "state": "skipped", "kind": "answer",
                 "detail": "structural check failed"}]

    def unanswered(what, body):
        return _no_response_pair(EMBEDDINGS_CHECK, EMBEDDINGS_ANSWER_CHECK, what, body)

    status, models = endpoint.call("/v1/models")
    if status == 0:
        return unanswered("/v1/models", models)
    if status != 200 or not isinstance(models, dict) or not models.get("data"):
        return fail(f"/v1/models returned HTTP {status}: {str(models)[:200]}")
    model = models["data"][0].get("id")
    status, r = endpoint.call("/v1/embeddings", {"model": model, "input": list(EMBED_TEXTS)})
    if status == 0:
        return unanswered("/v1/embeddings", r)
    if status != 200:
        return fail(f"/v1/embeddings returned HTTP {status}: {str(r)[:200]}")
    vecs = _vectors(r, len(EMBED_TEXTS))
    if isinstance(vecs, str):
        return fail(vecs)
    status, again = endpoint.call("/v1/embeddings", {"model": model, "input": [EMBED_TEXTS[0]]})
    if status == 0:
        return unanswered("/v1/embeddings (repeat request)", again)
    again_vecs = _vectors(again, 1) if status == 200 else f"HTTP {status}"
    if isinstance(again_vecs, str):
        return fail(f"repeat request: {again_vecs}")
    same = _cosine(vecs[0], again_vecs[0])
    if same < 0.999:
        return fail(f"the same input embedded differently (cosine {same:.4f})")
    dim = len(vecs[0])
    near, far = _cosine(vecs[0], vecs[1]), _cosine(vecs[0], vecs[2])
    return [{"name": EMBEDDINGS_CHECK, "state": "passed", "kind": "structural",
             "detail": f"model {model}, dim {dim}, repeat cosine {same:.4f}"},
            {"name": EMBEDDINGS_ANSWER_CHECK, "kind": "answer",
             "state": "passed" if near > far else "failed",
             "detail": f"paraphrase {near:.3f} vs unrelated {far:.3f}"}]


_DIGIT_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
                "nine")


def _words(text):
    """Lowercase words, punctuation dropped, each digit spoken as a word, so "7319"
    and "seven three one nine" compare equal."""
    text = re.sub(r"\d", lambda m: f" {_DIGIT_WORDS[int(m.group())]} ", text.lower())
    return re.findall(r"[a-z']+", text)


def word_error_rate(reference, hypothesis):
    """Word-level edit distance divided by the reference length."""
    ref, hyp = _words(reference), _words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


def _multipart(fields, file_field, filename, file_bytes, file_type):
    boundary = uuid.uuid4().hex
    parts = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
             .encode() for k, v in fields.items()]
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
                  f"filename=\"{filename}\"\r\nContent-Type: {file_type}\r\n\r\n").encode()
                 + file_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def check_asr(endpoint, audio=None, reference=ASR_REFERENCE):
    """Speech-to-text checks: one structural, one answer check (ADR 0059, D4).

    Structural (can gate): /v1/audio/transcriptions accepts a multipart WAV and
    returns HTTP 200 with a non-empty `text`.
    Answer (model-library approval): the transcript matches the reference sentence
    with a word error rate at or under ASR_MAX_WER.
    """
    def fail(detail):
        return [{"name": ASR_CHECK, "state": "failed", "kind": "structural", "detail": detail},
                {"name": ASR_ANSWER_CHECK, "state": "skipped", "kind": "answer",
                 "detail": "structural check failed"}]

    if audio is None:
        with open(ASR_FIXTURE, "rb") as f:
            audio = f.read()
    status, models = endpoint.call("/v1/models")
    if status == 0:
        return _no_response_pair(ASR_CHECK, ASR_ANSWER_CHECK, "/v1/models", models)
    if status != 200 or not isinstance(models, dict) or not models.get("data"):
        return fail(f"/v1/models returned HTTP {status}: {str(models)[:200]}")
    model = models["data"][0].get("id")
    body, ctype = _multipart({"model": model}, "file", "probe.wav", audio, "audio/wav")
    status, r = endpoint.call_raw("/v1/audio/transcriptions", body, ctype)
    if status == 0:
        return _no_response_pair(ASR_CHECK, ASR_ANSWER_CHECK, "/v1/audio/transcriptions", r)
    if status != 200:
        return fail(f"/v1/audio/transcriptions returned HTTP {status}: {str(r)[:200]}")
    text = r.get("text") if isinstance(r, dict) else None
    if not isinstance(text, str) or not text.strip():
        return fail(f"no transcript text in the response: {str(r)[:200]}")
    wer = word_error_rate(reference, text)
    return [{"name": ASR_CHECK, "state": "passed", "kind": "structural",
             "detail": f"model {model}, {len(text)} chars"},
            {"name": ASR_ANSWER_CHECK, "kind": "answer",
             "state": "passed" if wer <= ASR_MAX_WER else "failed",
             "detail": f"WER {wer:.2f} (max {ASR_MAX_WER}): {text.strip()[:120]!r}"}]


def _wav_stats(data):
    """(sample_rate, seconds, rms or None) for a PCM WAV, or an error string."""
    if not isinstance(data, (bytes, bytearray)) or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        head = data[:16] if isinstance(data, (bytes, bytearray)) else str(data)[:60]
        return f"not a WAV file (starts {head!r})"
    try:
        w = wave.open(io.BytesIO(data))
        n, rate, width = w.getnframes(), w.getframerate(), w.getsampwidth()
        frames = w.readframes(n)
    except (wave.Error, EOFError) as e:
        return f"WAV does not decode: {e}"
    if n == 0 or rate == 0:
        return "WAV has no audio frames"
    rms = None
    if width == 2:
        count = len(frames) // 2
        samples = struct.unpack(f"<{count}h", frames[: count * 2])
        rms = math.sqrt(sum(x * x for x in samples) / max(count, 1))
    return rate, n / rate, rms


def check_tts(endpoint, text=TTS_TEXT):
    """Text-to-speech checks: one structural, one answer check (ADR 0059, D4).

    Structural (can gate): /v1/audio/speech returns HTTP 200 with a WAV that decodes
    and holds audio frames.
    Answer (model-library approval): the audio is speech-like — not silent, and a
    plausible length for the text. A round trip through speech recognition is the
    stronger check and needs an ASR endpoint; it is not done here.
    """
    def fail(detail):
        return [{"name": TTS_CHECK, "state": "failed", "kind": "structural", "detail": detail},
                {"name": TTS_ANSWER_CHECK, "state": "skipped", "kind": "answer",
                 "detail": "structural check failed"}]

    status, models = endpoint.call("/v1/models")
    if status == 0:
        return _no_response_pair(TTS_CHECK, TTS_ANSWER_CHECK, "/v1/models", models)
    if status != 200 or not isinstance(models, dict) or not models.get("data"):
        return fail(f"/v1/models returned HTTP {status}: {str(models)[:200]}")
    model = models["data"][0].get("id")
    body = json.dumps({"model": model, "input": text, "response_format": "wav"}).encode()
    status, data = endpoint.call_raw("/v1/audio/speech", body, "application/json", binary=True)
    if status == 0:
        return _no_response_pair(TTS_CHECK, TTS_ANSWER_CHECK, "/v1/audio/speech", data)
    if status != 200:
        return fail(f"/v1/audio/speech returned HTTP {status}: {str(data)[:200]}")
    stats = _wav_stats(data)
    if isinstance(stats, str):
        return fail(stats)
    rate, seconds, rms = stats
    words = len(text.split())
    lo, hi = words * TTS_SECONDS_PER_WORD[0], words * TTS_SECONDS_PER_WORD[1]
    problems = []
    if not lo <= seconds <= hi:
        problems.append(f"{seconds:.1f}s is implausible for {words} words ({lo:.1f}-{hi:.1f}s)")
    if rms is not None and rms < TTS_MIN_RMS:
        problems.append(f"near-silent (rms {rms:.0f} < {TTS_MIN_RMS})")
    level = f"rms {rms:.0f}" if rms is not None else "rms n/a (not 16-bit PCM)"
    return [{"name": TTS_CHECK, "state": "passed", "kind": "structural",
             "detail": f"model {model}, WAV {rate} Hz, {seconds:.1f}s"},
            {"name": TTS_ANSWER_CHECK, "kind": "answer",
             "state": "failed" if problems else "passed",
             "detail": "; ".join(problems) if problems else f"{seconds:.1f}s for {words} words, {level}"}]


# ---------------------------------------------------------------- ComfyUI workflows

COMFY_WRAPPER_INTERNAL_PORT = 18288
COMFY_PAYLOADS_DIR = "opt/comfyui-api-wrapper/payloads"
# A `modifier` payload names one of the wrapper's built-in workflows instead of
# carrying one; the wrapper's convention is modifier `Text2Image` ->
# modifiers/text2image.py -> WORKFLOW_JSON "workflows/text2image.json". Modifications
# change inputs (prompt, checkpoint), not which output nodes exist.
COMFY_MODIFIER_WORKFLOWS_DIR = "opt/comfyui-api-wrapper/workflows"
JUPYTER_PORT = 8080
COMFY_GENERATE_TIMEOUT = 1800
# Standard deviation of 0-255 luminance below this is a blank or uniform image.
IMAGE_MIN_STDDEV = 4.0
# Compressed bytes per pixel below this means a near-uniform image (non-PNG formats).
IMAGE_MIN_BYTES_PER_PIXEL = 0.01
IMAGE_MIN_SIDE = 64


def _png_pixels(data):
    """(width, height, luminance samples) for an 8-bit non-interlaced PNG, or None
    when the PNG uses a layout this decoder does not handle."""
    pos, chunks, ihdr = 8, [], None
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif ctype == b"IDAT":
            chunks.append(body)
        elif ctype == b"IEND":
            break
        pos += 12 + length
    if not ihdr:
        return None
    w, h, depth, color, _, _, interlace = ihdr
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
    if depth != 8 or interlace or channels is None:
        return None
    raw = zlib.decompress(b"".join(chunks))
    stride, bpp = w * channels, channels
    prev, lum, off = bytearray(stride), [], 0
    step = max(1, h // 64)                       # sample rows; enough for variance
    for y in range(h):
        ftype, line = raw[off], bytearray(raw[off + 1:off + 1 + stride])
        off += 1 + stride
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], prev[i - bpp] if i >= bpp else 0
            if ftype == 1:
                line[i] = (line[i] + a) & 255
            elif ftype == 2:
                line[i] = (line[i] + b) & 255
            elif ftype == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif ftype == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        if y % step == 0:
            for x in range(0, w, max(1, w // 64)):
                px = line[x * channels:x * channels + min(channels, 3)]
                lum.append(sum(px) / len(px))
        prev = line
    return w, h, lum


def _image_info(data):
    """(format, width, height) from an image header, or None if not an image."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return "png", w, h
    if data[:3] == b"\xff\xd8\xff":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker, seglen = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return "jpeg", w, h
            i += 2 + seglen
        return "jpeg", 0, 0
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        if data[12:16] == b"VP8X":
            w = 1 + int.from_bytes(data[24:27], "little")
            h = 1 + int.from_bytes(data[27:30], "little")
            return "webp", w, h
        if data[12:16] == b"VP8 ":
            w, h = struct.unpack("<HH", data[26:30])
            return "webp", w & 0x3FFF, h & 0x3FFF
        if data[12:16] == b"VP8L":
            b = data[21:25]
            w = 1 + (((b[1] & 0x3F) << 8) | b[0])
            h = 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
            return "webp", w, h
        return "webp", 0, 0
    return None


def _media_kind(data):
    """'video', 'audio' or None from a container signature."""
    if data[4:8] == b"ftyp" or data[:4] == b"\x1aE\xdf\xa3" or data[:4] == b"GIF8":
        return "video"
    if (data[:4] == b"RIFF" and data[8:12] == b"WAVE") or data[:3] == b"ID3" or data[:4] == b"fLaC" \
            or data[:4] == b"OggS":
        return "audio"
    return None


def check_output_file(name, data):
    """(structural result, answer result) for one workflow output file."""
    prefix = f"external/comfyui/{name}"
    info = _image_info(data)
    if info:
        fmt, w, h = info
        if min(w, h) < IMAGE_MIN_SIDE:
            return ({"name": prefix, "state": "failed", "kind": "structural",
                     "detail": f"{fmt} is {w}x{h}, below {IMAGE_MIN_SIDE}px"},
                    {"name": prefix + ":content", "state": "skipped", "kind": "answer",
                     "detail": "structural check failed"})
        structural = {"name": prefix, "state": "passed", "kind": "structural",
                      "detail": f"{fmt} {w}x{h}, {len(data)} bytes"}
        pixels = _png_pixels(data) if fmt == "png" else None
        if pixels:
            lum = pixels[2]
            mean = sum(lum) / len(lum)
            sd = (sum((v - mean) ** 2 for v in lum) / len(lum)) ** 0.5
            ok = sd >= IMAGE_MIN_STDDEV
            detail = f"luminance stddev {sd:.1f} (min {IMAGE_MIN_STDDEV})"
        else:
            bpp = len(data) / (w * h)
            ok = bpp >= IMAGE_MIN_BYTES_PER_PIXEL
            detail = f"{bpp:.3f} bytes/pixel (min {IMAGE_MIN_BYTES_PER_PIXEL}; compression proxy)"
        return structural, {"name": prefix + ":content", "kind": "answer",
                            "state": "passed" if ok else "failed",
                            "detail": detail if ok else "blank or uniform image: " + detail}
    kind = _media_kind(data)
    if kind:
        return ({"name": prefix, "state": "passed", "kind": "structural",
                 "detail": f"{kind} container, {len(data)} bytes"},
                {"name": prefix + ":content", "state": "skipped", "kind": "answer",
                 "detail": f"{kind} content checks are not written yet"})
    return ({"name": prefix, "state": "failed", "kind": "structural",
             "detail": f"not a recognised image, video or audio file (starts {data[:12]!r})"},
            {"name": prefix + ":content", "state": "skipped", "kind": "answer",
             "detail": "structural check failed"})


COMFY_INTERNAL_PORT = 18188

# What each known output node writes. Output nodes are DISCOVERED from ComfyUI's own
# /object_info (`output_node: true`), so a node missing here is never silently
# passed: it is reported as unclassified, by class name, so the table can grow.
_OUTPUT_KINDS = {
    "SaveImage": "image",
    "SaveImageWebsocket": "image",
    "SaveAnimatedWEBP": "animation",
    "SaveAnimatedPNG": "animation",
    "SaveVideo": "video",
    "SaveWEBM": "video",
    "SaveAudio": "audio",
    "SaveAudioMP3": "audio",
    "SaveAudioOpus": "audio",
    "SaveGLB": "3d",
}
# Nodes whose output the wrapper deliberately does not return (temp/preview files).
_PREVIEW_PREFIX = "Preview"


def _vhs_kind(inputs):
    """VideoHelperSuite's VHS_VideoCombine picks its container from `format`."""
    fmt = str(inputs.get("format", ""))
    if fmt.startswith("video/"):
        return "video"
    if fmt.startswith("image/"):
        return "animation"
    return None


def _is_link(value):
    """In API-format workflows an input wired to another node is [node_id, slot]."""
    return isinstance(value, list) and len(value) == 2 and isinstance(value[1], int)


def _expects_audio_track(workflow, node):
    """True when a video save node is fed audio: an `audio` input wired on the node
    itself (VHS_VideoCombine) or on the node feeding its video (CreateVideo)."""
    inputs = node.get("inputs") or {}
    if _is_link(inputs.get("audio")):
        return True
    for key in ("video", "images"):
        src = inputs.get(key)
        if _is_link(src):
            upstream = workflow.get(str(src[0])) or {}
            if _is_link((upstream.get("inputs") or {}).get("audio")):
                return True
    return False


def expected_outputs(workflow, output_node_classes):
    """{node_id: (class_type, kind, wants_audio)} for every output node in an
    API-format workflow. kind is image|animation|video|audio|3d|preview|unclassified.

    `output_node_classes` is the set of class names ComfyUI reports as output
    nodes, or None when /object_info could not be read (then only the known table
    is used, and the result says so).
    """
    out = {}
    for node_id, node in (workflow or {}).items():
        if not isinstance(node, dict):
            continue
        cls = node.get("class_type", "")
        known = cls in _OUTPUT_KINDS or cls == "VHS_VideoCombine" or cls.startswith(_PREVIEW_PREFIX)
        is_output = (cls in output_node_classes) if output_node_classes is not None else known
        if not is_output:
            continue
        if cls.startswith(_PREVIEW_PREFIX):
            kind = "preview"
        elif cls == "VHS_VideoCombine":
            kind = _vhs_kind(node.get("inputs") or {}) or "unclassified"
        else:
            kind = _OUTPUT_KINDS.get(cls, "unclassified")
        wants_audio = kind == "video" and _expects_audio_track(workflow, node)
        out[str(node_id)] = (cls, kind, wants_audio)
    return out


def _mp4_has_audio(data):
    """An MP4/MOV with a sound track carries an `hdlr` box with handler type `soun`."""
    return re.search(rb"hdlr.{8}soun", data, re.S) is not None


def _file_kind(data):
    """What a file actually is, from its signature."""
    info = _image_info(data)
    if info:
        return "image"
    if data[:4] == b"glTF":
        return "3d"
    return _media_kind(data)


# Which file kinds satisfy each expected node kind.
_ACCEPTS = {"image": {"image"}, "animation": {"image", "video"}, "video": {"video"},
            "audio": {"audio"}, "3d": {"3d"}}


class NoResponse(str):
    """An error string that means no HTTP response at all (status 0), so the caller
    reports `error` (could not check) rather than `failed`."""


def _jupyter_error(status, r):
    if status == 0:
        return NoResponse(f"no HTTP response ({str(r)[:160]})")
    return f"HTTP {status}: {str(r)[:160]}"


def _unreached_state(err):
    """`error` when a Jupyter read got no HTTP response, else `failed`."""
    return "error" if isinstance(err, NoResponse) else "failed"


class Jupyter:
    """Read-only file access through the instance's public Jupyter contents API.
    Errors are strings; a NoResponse one means the request got no HTTP response."""

    def __init__(self, host, port, token, timeout=REQUEST_TIMEOUT):
        self.ep = Endpoint(host, port, token, timeout=timeout)

    @property
    def deadline(self):
        return self.ep.deadline

    @deadline.setter
    def deadline(self, value):
        self.ep.deadline = value

    def _get(self, path, query):
        # Jupyter takes `token <t>`; reuse Endpoint's transport with that header.
        status, r = self.ep.call_raw_with_auth(
            f"/api/contents/{urllib.parse.quote(path)}?{query}", f"token {self.ep.token}")
        return status, r

    def list_dir(self, path):
        status, r = self._get(path, "content=1&type=directory")
        if status != 200 or not isinstance(r, dict):
            return None, _jupyter_error(status, r)
        return sorted(e["name"] for e in r.get("content") or [] if e.get("type") == "file"), None

    def read_text(self, path):
        status, r = self._get(path, "content=1&type=file&format=text")
        if status != 200 or not isinstance(r, dict):
            return None, _jupyter_error(status, r)
        return r.get("content"), None

    def read_bytes(self, path):
        status, r = self._get(path, "content=1&type=file&format=base64")
        if status != 200 or not isinstance(r, dict):
            return None, _jupyter_error(status, r)
        return base64.b64decode(r.get("content") or ""), None


def comfy_output_node_classes(comfy):
    """The set of output-node class names from ComfyUI's /object_info, or None."""
    if comfy is None:
        return None
    status, info = comfy.call("/object_info")
    if status != 200 or not isinstance(info, dict):
        return None
    return {name for name, spec in info.items()
            if isinstance(spec, dict) and spec.get("output_node")}


def _check_node(wf, node_id, cls, kind, wants_audio, files):
    """Results for one expected output node and the files it produced."""
    name = f"external/comfyui/{wf}/{cls}#{node_id}"
    if kind == "preview":
        return [{"name": name, "state": "skipped", "kind": "structural",
                 "detail": "preview node: the wrapper does not return preview files"}]
    if kind == "unclassified":
        return [{"name": name, "state": "error", "kind": "structural",
                 "detail": f"unclassified output node {cls}: add it to the known output "
                           f"kinds so its output can be checked"}]
    if not files:
        return [{"name": name, "state": "failed", "kind": "structural",
                 "detail": f"expected {kind} output, the node produced no files"}]
    results = []
    for i, (label, data, err) in enumerate(files, 1):
        fname = f"{name}/{i}"
        if not data:
            results.append({"name": fname, "state": _unreached_state(err), "kind": "structural",
                            "detail": f"{label}: output bytes unavailable: {err}"})
            continue
        got = _file_kind(data)
        if got not in _ACCEPTS[kind]:
            results.append({"name": fname, "state": "failed", "kind": "structural",
                            "detail": f"{label}: expected {kind}, got "
                                      f"{got or 'an unrecognised file'} ({len(data)} bytes)"})
            continue
        if wants_audio:
            if data[4:8] == b"ftyp" and not _mp4_has_audio(data):
                results.append({"name": fname, "state": "failed", "kind": "structural",
                                "detail": f"{label}: the node is fed audio but the video has "
                                          f"no sound track"})
                continue
        structural, content = check_output_file(f"{wf}/{cls}#{node_id}/{i}", data)
        structural["name"], content["name"] = fname, fname + ":content"
        if structural["state"] == "passed":
            structural["detail"] = f"{label}: {kind}" + (
                " with audio" if wants_audio else "") + f"; {structural['detail']}"
        results.extend([structural, content])
    return results


def _check_workflow(name, wf, wrapper, jupyter, output_classes, timeout, results):
    """Run one provisioned workflow and check its outputs, appending to `results`."""
    text, err = jupyter.read_text(f"{COMFY_PAYLOADS_DIR}/{name}")
    try:
        payload = json.loads(text) if text is not None else None
    except ValueError as e:
        payload, err = None, f"not valid JSON: {e}"
    if payload is None:
        results.append({"name": f"external/comfyui/{wf}", "state": _unreached_state(err),
                        "kind": "structural", "detail": f"cannot read payload: {err}"})
        return
    inp = payload.get("input") or {}
    workflow = inp.get("workflow_json") or {}
    if not workflow and inp.get("modifier"):
        base = f"{COMFY_MODIFIER_WORKFLOWS_DIR}/{str(inp['modifier']).lower()}.json"
        text, err = jupyter.read_text(base)
        try:
            workflow = json.loads(text) if text is not None else None
        except ValueError as e:
            workflow, err = None, f"not valid JSON: {e}"
        if not workflow:
            results.append({"name": f"external/comfyui/{wf}", "state": "error",
                            "kind": "structural",
                            "detail": f"modifier {inp['modifier']!r}: cannot read its base "
                                      f"workflow {base} ({err}), so its output nodes are "
                                      f"unknown"})
            return
    expected = expected_outputs(workflow, output_classes)
    status, r = wrapper.call_raw_with_headers(
        "/generate/sync", json.dumps(payload).encode(), "application/json",
        {"x-return-outputs-as-base64": "true"}, timeout=timeout)
    if status == 0:
        results.append(_no_response(f"external/comfyui/{wf}", "/generate/sync", r,
                                    kind="structural"))
        return
    if status != 200 or not isinstance(r, dict) or r.get("status") != "completed":
        msg = r.get("message") if isinstance(r, dict) else r
        results.append({"name": f"external/comfyui/{wf}", "state": "failed",
                        "kind": "structural",
                        "detail": f"HTTP {status}, status="
                                  f"{r.get('status') if isinstance(r, dict) else '?'}: "
                                  f"{str(msg)[:200]}"})
        return
    outputs = r.get("output") or []
    if not expected:
        results.append({"name": f"external/comfyui/{wf}", "state": "failed",
                        "kind": "structural",
                        "detail": "the workflow has no output node to verify"})
        return
    results.append({"name": f"external/comfyui/{wf}", "state": "passed", "kind": "structural",
                    "detail": f"completed; {len(outputs)} file(s) from "
                              f"{len(expected)} output node(s); generation "
                              f"{(r.get('timings') or {}).get('generation_ms', '?')} ms"})
    by_node = {}
    for o in outputs:
        data, ferr = None, None
        if o.get("data"):
            data = base64.b64decode(o["data"])
        elif o.get("local_path"):                # too large to inline (video)
            data, ferr = jupyter.read_bytes(o["local_path"].lstrip("/"))
        else:
            ferr = o.get("error") or "no data and no local_path"
        by_node.setdefault(str(o.get("node_id")), []).append(
            (o.get("filename", "?"), data, ferr))
    for node_id, (cls, kind, wants_audio) in sorted(expected.items()):
        files = by_node.pop(node_id, [])
        results.extend(_guarded(
            f"external/comfyui/{wf}/{cls}#{node_id}",
            lambda: _check_node(wf, node_id, cls, kind, wants_audio, files)))
    for node_id, files in sorted(by_node.items()):
        results.append({"name": f"external/comfyui/{wf}/#{node_id}", "state": "error",
                        "kind": "structural",
                        "detail": f"{len(files)} file(s) from node {node_id}, which is not a "
                                  f"recognised output node in this workflow"})


def check_comfyui_workflows(wrapper, jupyter, comfy=None, timeout=COMFY_GENERATE_TIMEOUT,
                            deadline=None):
    """Run EVERY provisioned workflow through the public API wrapper and check that
    every output node delivered what it is built to produce (ADR 0059: if we ship a
    workflow, we verify it).

    Output nodes are discovered from ComfyUI's /object_info, so custom packs are
    covered; their expected kind comes from a known table (unclassified nodes are an
    error, never a pass). Each node must deliver at least one file of its kind; a
    video node fed audio must carry a sound track. Image content is checked for
    blank or uniform output; video, audio and 3D content checks are not written yet.

    A request that gets no HTTP response is an `error` (not checked), not a failure.
    Once `deadline` (contract.Deadline) has passed, each workflow not yet run is
    reported by name as an `error`. A workflow, or one node's check, that raises
    unexpectedly is one `error` result under its own name; the rest still run.
    """
    names, err = jupyter.list_dir(COMFY_PAYLOADS_DIR)
    if names is None:
        return [{"name": "external/comfyui", "state": _unreached_state(err), "kind": "structural",
                 "detail": f"cannot list {COMFY_PAYLOADS_DIR}: {err}"}]
    names = [n for n in names if n.endswith(".json")]
    if not names:
        return [{"name": "external/comfyui", "state": "failed", "kind": "structural",
                 "detail": f"no workflow payloads in {COMFY_PAYLOADS_DIR}"}]
    output_classes = comfy_output_node_classes(comfy)
    results = []
    if output_classes is None:
        results.append({"name": "external/comfyui/object_info", "state": "error",
                        "kind": "structural",
                        "detail": "ComfyUI /object_info unavailable: output nodes found from "
                                  "the known table only, so custom output nodes may be missed"})
    for name in names:
        wf = name[:-5]
        if deadline is not None and deadline.expired():
            results.append(_deadline_result(f"external/comfyui/{wf}", deadline))
            continue
        mark = len(results)
        try:
            _check_workflow(name, wf, wrapper, jupyter, output_classes, timeout, results)
        except Exception as e:                                    # noqa: BLE001
            # One result for the workflow, not a half-written set of them.
            del results[mark:]
            results.append({"name": f"external/comfyui/{wf}", "state": "error",
                            "kind": "structural",
                            "detail": f"the check raised {type(e).__name__}: {str(e)[:300]}"})
    return results


def parse_capabilities(value):
    """`--capabilities chat,embeddings` -> (set, unknown names). None means 'not given'."""
    if value is None:
        return None, []
    caps = {c.strip().lower() for c in value.split(",") if c.strip()}
    return caps, sorted(caps - set(KNOWN_CAPABILITIES))


def _image_name(template):
    return str((template or {}).get("image") or "")


def resolve_capabilities(template, env_overrides=(), override=None):
    """(capabilities, source, problems) for a template.

    In order: an explicit --capabilities override; the template's OPENAI_ROUTES (the
    declaration pyworker serves from); a ComfyUI image; otherwise pyworker's default
    (completions and chat). `problems` lists declaration errors to report — unknown
    routes, or a template that disables the in-image chat test without declaring what
    it serves instead.
    """
    if override is not None:
        if not isinstance(override, str):            # a set or list from code/tests
            override = ",".join(sorted(override))
        caps, unknown = parse_capabilities(override)
        return caps, "--capabilities", [f"unknown capability {u}" for u in unknown]
    env = (template or {}).get("env", "") or ""
    routes = _env_value(env, "OPENAI_ROUTES")
    for kv in env_overrides or ():
        k, _, v = kv.partition("=")
        if k == "OPENAI_ROUTES":
            routes = v
    if routes and routes.strip():
        caps, problems = set(), []
        for r in (x.strip() for x in routes.split(",") if x.strip()):
            if r in ROUTE_CAPABILITIES:
                caps.add(ROUTE_CAPABILITIES[r])
            else:
                problems.append(f"OPENAI_ROUTES names unknown route {r}")
        return caps, "OPENAI_ROUTES", problems
    image = _image_name(template)
    if ("/comfy" in image
            or _env_with_overrides(env, env_overrides, "PROVISIONING_COMFYUI_WORKFLOWS") is not None):
        return {COMFYUI_CAPABILITY}, "ComfyUI image", []
    repo = image.split(":", 1)[0]
    if repo not in OPENAI_ENGINE_IMAGES:
        return set(), f"no OpenAI engine in image {repo or '?'}: no external checks", []
    caps = {ROUTE_CAPABILITIES[r] for r in PYWORKER_DEFAULT_ROUTES}
    variable = endpoint_test_var(repo)
    if chat_disabled(env, env_overrides, variable):
        return caps, "OPENAI_ROUTES unset: pyworker default", [
            f"{variable}=none but OPENAI_ROUTES is unset: declare the routes this "
            "template serves (e.g. OPENAI_ROUTES=/v1/embeddings)"]
    return caps, "OPENAI_ROUTES unset: pyworker default", []


_ENGINE_CAPABILITIES = ("chat", "completions", "embeddings", "transcriptions", "speech")


def _env_with_overrides(env, env_overrides, name):
    """`-e NAME=...` from the template env, with a later --env NAME=... winning."""
    value = _env_value(env, name)
    for kv in env_overrides or ():
        k, _, v = kv.partition("=")
        if k == name:
            value = v
    return value


class _LazySession:
    """Opens a remote.RemoteSession only if a check actually needs the box, and
    closes it (detaching and deleting the ephemeral key) as soon as the contract is
    done — the key exists for those seconds only. The run's deadline, when given, is
    handed to the session so SSH cannot run past it."""

    def __init__(self, factory, deadline=None):
        self.factory, self.session, self.deadline = factory, None, deadline

    def run(self, script, python="python3"):
        if self.session is None:
            session = self.factory()
            if self.deadline is not None:
                _attach_deadline(session, self.deadline)
            self.session = session.__enter__()
        return self.session.run(script, python=python)

    def close(self):
        if self.session is not None:
            self.session.__exit__(None, None, None)
            self.session = None


# Report findings -> result states. A declared deviation is reported, never blocking
# (ADR 0031 decision 6a), so it gets its own state rather than passing or failing.
def check_contract(host, port, token, image, env, env_overrides, routes,
                   client_factory=None, remote_factory=None, deadline=None):
    """The OpenAI contract (contract.py, ported from the in-image checkers) for an
    engine image, keyed on the declared routes. Results are `contract/<check>`.
    `deadline` (contract.Deadline) caps every contract request's timeout and the SSH
    session's connect retries and remote script."""
    row = contract.set_engine(image)
    if row is None:
        return []
    out = io.StringIO()
    rep = contract.Report(out)
    cli = _attach_deadline((client_factory or contract.Client)(f"{host}:{port}", token), deadline)
    lazy = _LazySession(remote_factory, deadline) if remote_factory else None
    try:
        _run_contract(rep, cli, row, env, env_overrides, routes,
                      contract.remote_counter(lazy) if lazy else None)
    finally:
        if lazy:
            lazy.close()
    results = [{"name": f"contract/{c}", "state": "passed", "kind": "structural", "detail": ""}
               for c in rep.passed]
    for bucket, state in ((rep.violations, "failed"), (rep.deviated, "deviation"),
                          (rep.advisories, "advisory"), (rep.errors, "error"),
                          (rep.na, "skipped")):
        results += [{"name": f"contract/{c}", "state": state, "kind": "structural", "detail": d}
                    for c, d in bucket]
    return results


def _run_contract(rep, cli, row, env, env_overrides, routes, counter):
    contract.run_contract(
        rep, cli,
        model=_env_with_overrides(env, env_overrides, row["model_env"]) or "",
        engine_args=_env_with_overrides(env, env_overrides, row["args_env"]) or "",
        expect_caps=_env_with_overrides(env, env_overrides, row["caps_env"]) or "",
        routes=set(routes), counter=counter)



def run_external_checks(instance, template, tests, env_overrides=(), token=None,
                        endpoint_factory=Endpoint, capabilities=None,
                        jupyter_factory=Jupyter, ready_timeout=READY_TIMEOUT,
                        sleep=time.sleep, contract_client_factory=None, remote_factory=None,
                        deadline=EXTERNAL_DEADLINE, clock=time.monotonic):
    """The advisory external checks for one run, as a list of result dicts.

    Each result is {"name", "state": passed|failed|skipped|error|info, "detail",
    "advisory": True, and "kind": structural|answer when it ran}. The first result is
    always `external/capabilities` (state info): what was checked and where that came
    from.

    `capabilities` is an explicit override (a comma string, from --capabilities);
    without it they are resolved from the template (resolve_capabilities).

    `deadline` is ONE budget in seconds (measured on `clock`) for the whole phase:
    the readiness wait (itself bounded by `ready_timeout`), every check and the
    contract. Each request's timeout is capped by the time left. Once it has passed,
    every check not yet run is reported by name as an `error` (never dropped, never
    passed) and the function returns.

    A check that raises unexpectedly becomes one `error` result under its own name
    (the contract step is `contract`) and the remaining checks still run.
    """
    def mark(results):
        for r in results:
            r["advisory"] = True
            if r.get("state") in ("passed", "failed"):
                r.setdefault("kind", "structural")
        return results

    dl = contract.Deadline(deadline, clock)
    env = (template or {}).get("env", "") or ""
    portal = _env_with_overrides(env, env_overrides, "PORTAL_CONFIG")
    host = (instance or {}).get("public_ipaddr", "").strip()
    caps, source, problems = resolve_capabilities(template, env_overrides, capabilities)
    results = [{"name": "external/capabilities", "state": "info",
                "detail": f"{', '.join(sorted(caps)) or 'none'} ({source})"}]
    results += [{"name": "external/capabilities", "state": "error", "detail": p} for p in problems]

    engine_caps = caps & set(_ENGINE_CAPABILITIES)
    guard = None                     # the undeclared-chat guard, run after everything
    first = (CHAT_CHECK if "chat" in caps else
             UNDECLARED_CHAT_CHECK if engine_caps else "external/comfyui")

    # With a serving test in the image, the engine was shown to start once it has
    # run. Without one (old images), the checks wait for the engine themselves.
    own_readiness = not has_serving_test(tests)
    if not own_readiness and not serving_test_ran(tests):
        results.append({"name": first, "state": "skipped", "detail": "no in-image serving test ran"})
        return mark(results)

    for cap in sorted(caps - set(CHECKED_CAPABILITIES)):
        results.append({"name": f"external/{cap}", "state": "skipped",
                        "detail": f"declared ({source}); no external check for {cap} yet"})

    if engine_caps:
        port = host_port(instance or {}, public_api_port(portal))
        if not (host and port):
            results.append({"name": first, "state": "skipped",
                            "detail": "the engine API port is not mapped on this instance"})
        else:
            endpoint = _attach_deadline(endpoint_factory(host, port, token), dl)
            # (names reported if the deadline leaves no time for the step, step). A step
            # with no names is not a check (a skip) and always runs.
            steps = [(("contract",), lambda: check_contract(
                host, port, token, _image_name(template), env, env_overrides,
                caps & set(_ENGINE_CAPABILITIES), client_factory=contract_client_factory,
                remote_factory=remote_factory, deadline=dl))]
            if "chat" in caps:
                steps.append(((CHAT_CHECK,), lambda: [check_chat(endpoint)]))
            if "completions" in caps:
                steps.append(((COMPLETIONS_CHECK,), lambda: [check_completions(endpoint)]))
            if "embeddings" in caps:
                steps.append(((EMBEDDINGS_CHECK, EMBEDDINGS_ANSWER_CHECK),
                              lambda: check_embeddings(endpoint)))
            if "transcriptions" in caps:
                steps.append(((ASR_CHECK, ASR_ANSWER_CHECK), lambda: check_asr(endpoint)))
            if "speech" in caps:
                if (_env_with_overrides(env, env_overrides, "SPEECH_KIND")
                        or "speech").strip().lower() == "music":
                    steps.append(((), lambda: [{
                        "name": TTS_CHECK, "state": "skipped",
                        "detail": "SPEECH_KIND=music: the song variant of the speech "
                                  "check is not written yet"}]))
                else:
                    steps.append(((TTS_CHECK, TTS_ANSWER_CHECK), lambda: check_tts(endpoint)))
            # Last of the WHOLE phase, deliberately, after ComfyUI too: probing a route
            # the template does not declare can take the server down (seen live: one
            # /v1/completions call killed a vLLM-Omni TTS server), and that must never
            # cost the declared checks their result.
            if "chat" not in caps:
                guard = lambda: [check_chat_refused(endpoint)]  # noqa: E731

            if own_readiness:
                last = {}

                def models_up():
                    st, m = endpoint.call("/v1/models")
                    last["status"] = st
                    if st == 0:
                        return False, f"/v1/models: no HTTP response ({str(m)[:120]})"
                    return st == 200 and isinstance(m, dict) and bool(m.get("data")), f"/v1/models HTTP {st}"
                ready, detail, waited = wait_ready(models_up, timeout=ready_timeout, sleep=sleep,
                                                   deadline=dl)
                if not ready:
                    if dl.expired():
                        results += [_deadline_result(n, dl) for names, _ in steps for n in names]
                        if COMFYUI_CAPABILITY in caps:
                            results.append(_deadline_result("external/comfyui", dl))
                        if guard:
                            results.append(_deadline_result(UNDECLARED_CHAT_CHECK, dl))
                        return mark(results)
                    results.append({"name": first, "kind": "structural",
                                    "state": "error" if last.get("status") == 0 else "failed",
                                    "detail": f"engine not ready after {waited:g}s ({detail}); "
                                              f"the image has no serving test to wait on"})
                    return mark(results)
            for names, step in steps:
                if names and dl.expired():
                    results += [_deadline_result(n, dl) for n in names]
                else:
                    results.extend(_guarded(names[0] if names else first, step))

    if COMFYUI_CAPABILITY in caps:
        wport = host_port(instance or {}, public_api_port(portal, COMFY_WRAPPER_INTERNAL_PORT))
        jport = host_port(instance or {}, JUPYTER_PORT)
        if not (host and wport and jport):
            results.append({"name": "external/comfyui", "state": "skipped",
                            "detail": "the API wrapper or Jupyter port is not mapped"})
        elif dl.expired():
            results.append(_deadline_result("external/comfyui", dl))
        else:
            timeout = int(_env_with_overrides(env, env_overrides, "COMFYUI_GENERATE_TIMEOUT")
                          or COMFY_GENERATE_TIMEOUT)
            cport = host_port(instance or {}, public_api_port(portal, COMFY_INTERNAL_PORT))
            comfy = _attach_deadline(endpoint_factory(host, cport, token), dl) if cport else None
            wrapper = _attach_deadline(endpoint_factory(host, wport, token), dl)
            jupyter = _attach_deadline(jupyter_factory(host, jport, token), dl)
            if own_readiness:
                last = {}

                def comfy_up():
                    st, body = wrapper.call("/health")
                    last["unanswered"] = st == 0
                    if st == 0:
                        return False, f"wrapper /health: no HTTP response ({str(body)[:120]})"
                    if st != 200:
                        return False, f"wrapper /health HTTP {st}"
                    names, err = jupyter.list_dir(COMFY_PAYLOADS_DIR)
                    last["unanswered"] = isinstance(err, NoResponse)
                    if not names:
                        return False, f"no payloads yet ({err or 'empty'})"
                    return True, "ready"
                ready, detail, waited = wait_ready(comfy_up, timeout=ready_timeout, sleep=sleep,
                                                   deadline=dl)
            else:
                ready = True
            if not ready and dl.expired():
                results.append(_deadline_result("external/comfyui", dl))
            elif not ready:
                results.append({"name": "external/comfyui", "kind": "structural",
                                "state": "error" if last.get("unanswered") else "failed",
                                "detail": f"ComfyUI not ready after {waited:g}s ({detail}); "
                                          f"the image has no serving test to wait on"})
            else:
                results.extend(_guarded("external/comfyui", lambda: check_comfyui_workflows(
                    wrapper, jupyter, comfy=comfy, timeout=timeout, deadline=dl)))

    if guard:
        if dl.expired():
            results.append(_deadline_result(UNDECLARED_CHAT_CHECK, dl))
        else:
            results.extend(_guarded(UNDECLARED_CHAT_CHECK, guard))
    return mark(results)
