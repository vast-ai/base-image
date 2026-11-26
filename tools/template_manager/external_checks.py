"""Checks the launcher runs from OUTSIDE the instance (ADR 0059, D1).

The in-image serving tests send inference to the engine's internal port. These send
it through the instance's public endpoint — Caddy, the instance token and the mapped
port, the path a user's client takes. They run once the in-image serving test has
run (passed or failed); an image with no serving test is waited on here instead.

Each kind of check is a test type: one module in the `checks` package, listed in
`checks.REGISTRY` (ADR 0068). This module resolves what a template declares, selects
the registered types that declaration calls for, opens the endpoints they need and
runs them in order. Its capability and result-name tables are derived from the
registry.

What is checked comes from the template's capabilities (resolve_capabilities), in
this order: an explicit --capabilities override; the template's OPENAI_ROUTES (an
--env override wins); a ComfyUI image or PROVISIONING_COMFYUI_WORKFLOWS, meaning
`comfyui-workflows`; an OpenAI engine image (vLLM, vLLM-Omni, SGLang, llama.cpp)
with nothing declared, meaning pyworker's default of completions and chat; anything
else gets no external checks. The first result is always `external/capabilities`,
saying which and why.

Features of the model behind the chat route (vision and the rest of checks.FEATURES)
are a second declaration: the template's <ENGINE>_EXPECT_CAPS, plus whatever the caller
adds with --features (ADR 0068). They add checks to the ones the routes select and never change the routes. A feature whose route the
template does not declare is an error; one with no check yet is skipped, never passed.

What runs, for an engine image: the OpenAI contract (contract.py, `contract/<check>`),
then one check per declared capability — chat, completions, embeddings,
transcriptions, speech — each structural, some with an answer check beside it
(embedding ranking, transcript accuracy, speech-like audio), then one check per
declared feature. A declared capability
with no check yet is skipped, never passed. `comfyui-workflows` runs every provisioned
workflow and checks every output node. When chat is NOT declared, the
`external/undeclared/chat` guard runs last of everything: chat must be refused.

States: passed, failed, skipped, info, and error — the check could not be run (no
HTTP response, an unexpected exception, the deadline) — plus the contract's
deviation and advisory. Every result is advisory: it never changes the run's state
or exit code. A QA gate can require one by name through its `require_external` input
(ADR 0059, D7 step 3).

One deadline (EXTERNAL_DEADLINE) covers the readiness wait, the contract and every
check; checks it leaves no time for are reported by name as errors.

Structural checks assert status codes, response shape and tokens produced (ADR
0031); answer checks are the model-library bar (ADR 0059, D4).
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse


def _load_checks():
    """The `checks` package beside this file. It is imported by name when this
    directory is on sys.path. When this file was loaded by path from elsewhere, or
    `checks` names some other package, it is loaded by path under a name of its own,
    so the types always come from the same tree as this file."""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checks")
    try:
        import checks
        if os.path.realpath(os.path.dirname(checks.__file__ or "")) == os.path.realpath(here):
            return checks
    except ImportError:
        pass
    import hashlib
    import importlib.util
    name = "_template_manager_checks_" + hashlib.sha256(here.encode()).hexdigest()[:12]
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(here, "__init__.py"), submodule_search_locations=[here])
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package                 # its modules import it by this name
    try:
        spec.loader.exec_module(package)
    except BaseException:
        for loaded in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
            del sys.modules[loaded]
        raise
    return package


checks = _load_checks()
contract = checks.contract
REGISTRY = checks.REGISTRY

# Everything the types define stays importable from this module under the name it has
# always had.
ResultName = checks.ResultName
STRUCTURAL, ANSWER = checks.STRUCTURAL, checks.ANSWER
ANY_IMAGE, OPENAI_ENGINE, CONTRACT, COMFYUI = (
    checks.ANY_IMAGE, checks.OPENAI_ENGINE, checks.CONTRACT, checks.COMFYUI)
_env_value, _env_with_overrides = checks._env_value, checks._env_with_overrides
_deadline_result, _guarded, _attach_deadline = (
    checks._deadline_result, checks._guarded, checks._attach_deadline)
_no_response, _no_response_pair = checks._no_response, checks._no_response_pair

_m = checks.chat
CHAT_CHECK, CHAT_PROMPTS, check_chat = _m.CHAT_CHECK, _m.CHAT_PROMPTS, _m.check_chat

_m = checks.completions
COMPLETIONS_CHECK, check_completions = _m.COMPLETIONS_CHECK, _m.check_completions

_m = checks.embeddings
EMBEDDINGS_CHECK, EMBEDDINGS_ANSWER_CHECK, EMBED_TEXTS = (
    _m.EMBEDDINGS_CHECK, _m.EMBEDDINGS_ANSWER_CHECK, _m.EMBED_TEXTS)
_cosine, _vectors, check_embeddings = _m._cosine, _m._vectors, _m.check_embeddings

_m = checks.transcriptions
ASR_CHECK, ASR_ANSWER_CHECK, ASR_FIXTURE, ASR_REFERENCE, ASR_MAX_WER = (
    _m.ASR_CHECK, _m.ASR_ANSWER_CHECK, _m.ASR_FIXTURE, _m.ASR_REFERENCE, _m.ASR_MAX_WER)
_DIGIT_WORDS, _words, word_error_rate, _multipart, check_asr = (
    _m._DIGIT_WORDS, _m._words, _m.word_error_rate, _m._multipart, _m.check_asr)

_m = checks.speech
TTS_CHECK, TTS_ANSWER_CHECK, TTS_TEXT, TTS_SECONDS_PER_WORD, TTS_MIN_RMS = (
    _m.TTS_CHECK, _m.TTS_ANSWER_CHECK, _m.TTS_TEXT, _m.TTS_SECONDS_PER_WORD, _m.TTS_MIN_RMS)
_wav_stats, check_tts = _m._wav_stats, _m.check_tts

_m = checks.undeclared_chat
UNDECLARED_CHAT_CHECK, CHAT_REFUSED, check_chat_refused = (
    _m.UNDECLARED_CHAT_CHECK, _m.CHAT_REFUSED, _m.check_chat_refused)

_m = checks.contract_step
CONTRACT_STEP, _LazySession, check_contract, _run_contract = (
    _m.CONTRACT_STEP, _m._LazySession, _m.check_contract, _m._run_contract)

_m = checks.comfyui
COMFYUI_CAPABILITY, COMFYUI_CHECK, COMFYUI_OBJECT_INFO = (
    _m.COMFYUI_CAPABILITY, _m.COMFYUI_CHECK, _m.COMFYUI_OBJECT_INFO)
COMFY_WRAPPER_INTERNAL_PORT, COMFY_INTERNAL_PORT, JUPYTER_PORT = (
    _m.COMFY_WRAPPER_INTERNAL_PORT, _m.COMFY_INTERNAL_PORT, _m.JUPYTER_PORT)
COMFY_PAYLOADS_DIR, COMFY_MODIFIER_WORKFLOWS_DIR, COMFY_GENERATE_TIMEOUT = (
    _m.COMFY_PAYLOADS_DIR, _m.COMFY_MODIFIER_WORKFLOWS_DIR, _m.COMFY_GENERATE_TIMEOUT)
IMAGE_MIN_STDDEV, IMAGE_MIN_BYTES_PER_PIXEL, IMAGE_MIN_SIDE = (
    _m.IMAGE_MIN_STDDEV, _m.IMAGE_MIN_BYTES_PER_PIXEL, _m.IMAGE_MIN_SIDE)
_OUTPUT_KINDS, _PREVIEW_PREFIX, _ACCEPTS = _m._OUTPUT_KINDS, _m._PREVIEW_PREFIX, _m._ACCEPTS
_png_pixels, _image_info, _media_kind, _file_kind, _mp4_has_audio = (
    _m._png_pixels, _m._image_info, _m._media_kind, _m._file_kind, _m._mp4_has_audio)
_vhs_kind, _is_link, _expects_audio_track = _m._vhs_kind, _m._is_link, _m._expects_audio_track
check_output_file, expected_outputs, comfy_output_node_classes = (
    _m.check_output_file, _m.expected_outputs, _m.comfy_output_node_classes)
NoResponse, _unreached_state = _m.NoResponse, _m._unreached_state
_check_node, _check_workflow, check_comfyui_workflows = (
    _m._check_node, _m._check_workflow, _m.check_comfyui_workflows)
del _m

# The names the launcher reports are prefixed so they can never collide with an
# in-image test name (`base/..`, `vllm.d/..`). RESULT_NAMES (below) lists them all.
# This one is the first result of every run: what was checked and why. Informational,
# not a check.
CAPABILITIES_RESULT = "external/capabilities"

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


def serving_test_ran(tests):
    """The in-image serving test that ran (passed or failed), or None."""
    for t in tests or ():
        if _SERVING_TEST.match(t.get("name", "")) and t.get("state") in ("passed", "failed"):
            return t["name"]
    return None


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
    return TEST_ENDPOINT_VARS.get(contract.image_repo(image), "VLLM_TEST_ENDPOINT")


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


# ---------------------------------------------------------------- capabilities

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
# Images that run an OpenAI-compatible engine. pyworker's default routes only make
# sense for these; any other image (base-image, PyTorch, desktops, apps) has no
# engine to check, and its in-image suite is the whole test.
OPENAI_ENGINE_IMAGES = ("vastai/vllm", "vastai/vllm-omni", "vastai/sglang", "vastai/llama-cpp")


def _in_order(names):
    """`names` as a tuple, each once, in first-seen order."""
    return tuple(dict.fromkeys(names))


# The three tables below are derived from the registry (ADR 0068).
# Capabilities that have an external check today; the rest are reported as declared
# with no check yet, never as passed.
CHECKED_CAPABILITIES = _in_order(c for t in REGISTRY if t.axis == checks.ROUTE for c in t.requires)
# The capabilities checked on the engine API. Declaring any of them brings in the
# contract and, when chat is not among them, the undeclared-chat guard.
_ENGINE_CAPABILITIES = _in_order(c for t in REGISTRY
                                 if t.axis == checks.ROUTE and t.images == OPENAI_ENGINE
                                 for c in t.requires)
# Unknown names are an error, not silently ignored: a typo must not turn a check off.
KNOWN_CAPABILITIES = _in_order((*ROUTE_CAPABILITIES.values(),
                                *(c for t in REGISTRY for c in t.requires)))

# Features of the model behind the chat route (ADR 0068). Routes say what a template
# serves; these say what its chat model accepts or returns, and no template variable
# holds them, so the caller names them at run time.
KNOWN_FEATURES = checks.FEATURES
FEATURES_SOURCE = "--features"
# The features that have a check today, by name.
_FEATURE_TYPES = {t.name: t for t in REGISTRY if t.axis == checks.FEATURE}


def feature_requires(feature):
    """The capabilities a feature needs declared before it can be checked."""
    t = _FEATURE_TYPES.get(feature)
    return tuple(t.requires) if t else (checks.FEATURE_ROUTE,)


# ---------------------------------------------------------------- Jupyter

def _jupyter_error(status, r):
    if status == 0:
        return NoResponse(f"no HTTP response ({str(r)[:160]})")
    return f"HTTP {status}: {str(r)[:160]}"


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


# ---------------------------------------------------- the names the launcher reports

# Every result name run_external_checks can report: its own three kinds of row, then
# each registered type's. qa-gate's `require_external` may name only a STRUCTURAL row
# that passes, produced for the gated image (lint L126, which reads this table);
# tests/test_result_names.py runs the launcher and fails if it reports a name, kind or
# pass this table does not allow, or never reports a row.
RESULT_NAMES = (
    ResultName(CAPABILITIES_RESULT, None, ANY_IMAGE, False),
    # A declared route with no external check yet: always skipped.
    *(ResultName(f"external/{cap}", None, ANY_IMAGE, False)
      for cap in KNOWN_CAPABILITIES if cap not in CHECKED_CAPABILITIES),
    # A declared feature with no check yet: skipped, or an error when its route is
    # not declared.
    *(ResultName(f"external/{feature}", None, ANY_IMAGE, False)
      for feature in KNOWN_FEATURES if feature not in _FEATURE_TYPES),
    *(row for t in REGISTRY for row in t.results),
)

_PLACEHOLDERS = {"<workflow>": r"[^/\s,]+", "<class>": r"[^/\s,#]*", "<node>": r"[^/\s,#]+",
                 "<n>": r"[0-9]+"}
_EXACT_NAMES = {r.shape: r for r in RESULT_NAMES if "<" not in r.shape}
_SHAPED_NAMES = [(re.compile("".join(_PLACEHOLDERS.get(part, re.escape(part))
                                     for part in re.split(r"(<[a-z]+>)", r.shape))), r)
                 for r in RESULT_NAMES if "<" in r.shape]


def result_name(name):
    """The RESULT_NAMES row a reported name belongs to, or None if the launcher never
    reports it."""
    if name in _EXACT_NAMES:
        return _EXACT_NAMES[name]
    return next((r for rx, r in _SHAPED_NAMES if rx.fullmatch(name)), None)


# What an image is an instance of, whatever namespace it was pushed to.
image_repo = contract.image_repo


def is_comfyui_image(image):
    return "/comfy" in str(image or "")


def produced_on(images, image):
    """True when a row whose `images` is this can be reported for `image` (repo or
    repo:tag)."""
    repo = contract.image_repo(image)
    return (images == ANY_IMAGE
            or (images == OPENAI_ENGINE and repo in OPENAI_ENGINE_IMAGES)
            or (images == CONTRACT and repo in contract.ENGINES)
            or (images == COMFYUI and is_comfyui_image(image)))


def parse_capabilities(value):
    """`--capabilities chat,embeddings` -> (set, unknown names). None means 'not given'."""
    if value is None:
        return None, []
    caps = {c.strip().lower() for c in value.split(",") if c.strip()}
    return caps, sorted(caps - set(KNOWN_CAPABILITIES))


def parse_features(value):
    """`--features vision,tools` -> (set of features, unknown names). None means none."""
    if value is None:
        return set(), []
    if not isinstance(value, str):                   # a set or list from code/tests
        value = ",".join(sorted(value))
    names = {n.strip().lower() for n in value.split(",") if n.strip()}
    return names & set(KNOWN_FEATURES), sorted(names - set(KNOWN_FEATURES))


def template_features(template, env_overrides=()):
    """({feature: source}, the names it leaves to the contract) from the template's
    <ENGINE>_EXPECT_CAPS. That variable is the template's declaration of what its model
    must be able to do (ADR 0068, decision 2); the contract reads the same value for
    the capabilities it probes itself, so those names are not features here."""
    row = contract.ENGINES.get(contract.image_repo(_image_name(template)))
    if not row:
        return {}
    raw = _env_with_overrides((template or {}).get("env", "") or "", env_overrides,
                              row["caps_env"]) or ""
    names = {n.lower() for n in raw.replace(",", " ").split()}
    return {n: row["caps_env"] for n in names & set(KNOWN_FEATURES) - set(contract.CAPABILITIES)}


def _features_text(sources):
    """`vision (VLLM_EXPECT_CAPS) + reasoning (--features)`: each source once, the
    template's first."""
    by_source = {}
    for feature in sorted(sources, key=lambda f: (sources[f] == FEATURES_SOURCE, f)):
        by_source.setdefault(sources[feature], []).append(feature)
    return " + ".join(f"{', '.join(names)} ({source})" for source, names in by_source.items())


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
    if (is_comfyui_image(image)
            or _env_with_overrides(env, env_overrides, "PROVISIONING_COMFYUI_WORKFLOWS") is not None):
        return {COMFYUI_CAPABILITY}, "ComfyUI image", []
    repo = contract.image_repo(image)
    if repo not in OPENAI_ENGINE_IMAGES:
        given = image.split(":", 1)[0]
        return set(), f"no OpenAI engine in image {given or '?'}: no external checks", []
    caps = {ROUTE_CAPABILITIES[r] for r in PYWORKER_DEFAULT_ROUTES}
    variable = endpoint_test_var(repo)
    if chat_disabled(env, env_overrides, variable):
        return caps, "OPENAI_ROUTES unset: pyworker default", [
            f"{variable}=none but OPENAI_ROUTES is unset: declare the routes this "
            "template serves (e.g. OPENAI_ROUTES=/v1/embeddings)"]
    return caps, "OPENAI_ROUTES unset: pyworker default", []


# ---------------------------------------------------------------- the run

def _on_engine_api(test_type):
    """True for a type that is run against the engine API (the rest are ComfyUI's)."""
    return test_type.images in (OPENAI_ENGINE, CONTRACT)


def select_types(capabilities, features=()):
    """The registered types a set of capabilities and features selects, in the order
    they run.

    A route type runs when every capability it requires is declared; a guard when
    none of them is; a feature type when its feature is named and every capability it
    requires is declared. A type on the engine API runs only when an engine route is
    declared at all."""
    capabilities, features = set(capabilities), set(features)
    engine = capabilities & set(_ENGINE_CAPABILITIES)

    def selected(t):
        if _on_engine_api(t) and not engine:
            return False
        if t.axis == checks.FEATURE and t.name not in features:
            return False
        if t.axis == checks.GUARD:
            return not capabilities & set(t.requires)
        return set(t.requires) <= capabilities

    return sorted((t for t in REGISTRY if selected(t)), key=lambda t: t.order)


# What opening an endpoint came to: its types run, its types are dropped (it said why),
# or the whole phase ends here.
_OPEN, _UNAVAILABLE, _STOP = "open", "unavailable", "stop"


def run_external_checks(instance, template, tests, env_overrides=(), token=None,
                        endpoint_factory=Endpoint, capabilities=None,
                        features=None, jupyter_factory=Jupyter, ready_timeout=READY_TIMEOUT,
                        sleep=time.sleep, contract_client_factory=None, remote_factory=None,
                        deadline=EXTERNAL_DEADLINE, clock=time.monotonic):
    """The advisory external checks for one run, as a list of result dicts.

    Each result is {"name", "state": passed|failed|skipped|error|info, "detail",
    "advisory": True, and "kind": structural|answer when it ran}. The first result is
    always `external/capabilities` (state info): what was checked and where that came
    from, and the same for any features named.

    `capabilities` is an explicit override (a comma string, from --capabilities);
    without it they are resolved from the template (resolve_capabilities). `features`
    names features of the chat model (a comma string, from --features); they add to the
    capabilities and never change them. Together they select the registered test
    types (select_types), which run in order.

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
    named, unknown_features = parse_features(features)
    problems = problems + [f"unknown feature {u}" for u in unknown_features]
    # Where each feature was declared: the template first, the caller for the rest.
    sources = {**{f: FEATURES_SOURCE for f in named},
               **template_features(template, env_overrides)}
    features = set(sources)
    info = {"name": CAPABILITIES_RESULT, "state": "info",
            "detail": f"{', '.join(sorted(caps)) or 'none'} ({source})"}
    if features:
        info["detail"] += f"; features: {_features_text(sources)}"
    results = [info]
    results += [{"name": CAPABILITIES_RESULT, "state": "error", "detail": p} for p in problems]
    # A feature whose route is not declared cannot be checked: an error under its own
    # name, and the rest of the run goes on.
    for feature in sorted(features):
        missing = [c for c in feature_requires(feature) if c not in caps]
        if missing:
            results.append({"name": f"external/{feature}", "state": "error",
                            "detail": f"declared ({sources[feature]}) but it needs "
                                      f"{', '.join(missing)}, which is not among the "
                                      f"template's capabilities ({source})"})
            features.discard(feature)

    # A feature the contract probes itself (tools) is handed to the contract as
    # declared, which makes its own result required: one check, not two (ADR 0068,
    # decision 4).
    to_contract = set()
    if contract.image_repo(_image_name(template)) in contract.ENGINES:
        to_contract = features & set(contract.CAPABILITIES)
        features -= to_contract

    engine_caps = caps & set(_ENGINE_CAPABILITIES)
    # The name a result about the phase as a whole is reported under.
    first = (CHAT_CHECK if "chat" in caps else
             UNDECLARED_CHAT_CHECK if engine_caps else COMFYUI_CHECK)

    # With a serving test in the image, the engine was shown to start once it has
    # run. Without one (old images), the checks wait for the engine themselves.
    own_readiness = not has_serving_test(tests)
    if not own_readiness and not serving_test_ran(tests):
        results.append({"name": first, "state": "skipped", "detail": "no in-image serving test ran"})
        return mark(results)

    for cap in sorted(caps - set(CHECKED_CAPABILITIES)):
        results.append({"name": f"external/{cap}", "state": "skipped",
                        "detail": f"declared ({source}); no external check for {cap} yet"})
    for feature in sorted(features - set(_FEATURE_TYPES)):
        results.append({"name": f"external/{feature}", "state": "skipped",
                        "detail": f"declared ({sources[feature]}); no external check for "
                                  f"{feature} yet"})

    ctx = checks.Context(
        template=template, env=env, env_overrides=env_overrides, host=host, token=token,
        capabilities=caps, engine_capabilities=engine_caps, deadline=dl,
        contract_client_factory=contract_client_factory, remote_factory=remote_factory,
        declared_caps=frozenset(to_contract))
    # (type, the names reported if the deadline leaves it no time), in run order. A type
    # with no names is not a check here (it reports a skip) and always runs.
    plan = [(t, t.names_if_not_run(ctx)) for t in select_types(caps, features)]

    def open_engine(remaining):
        """Find the engine API and, when the image has no serving test, wait for it."""
        port = host_port(instance or {}, public_api_port(portal))
        if not (host and port):
            results.append({"name": first, "state": "skipped",
                            "detail": "the engine API port is not mapped on this instance"})
            return _UNAVAILABLE
        ctx.engine_port = port
        ctx.endpoint = endpoint = _attach_deadline(endpoint_factory(host, port, token), dl)
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
                    results.extend(_deadline_result(n, dl) for _, names in remaining for n in names)
                else:
                    results.append({"name": first, "kind": "structural",
                                    "state": "error" if last.get("status") == 0 else "failed",
                                    "detail": f"engine not ready after {waited:g}s ({detail}); "
                                              f"the image has no serving test to wait on"})
                return _STOP
        return _OPEN

    def open_comfyui(names):
        """Find the API wrapper, Jupyter and ComfyUI and, when the image has no serving
        test, wait for them. The deadline is checked here, before anything is opened;
        the workflows check it again between workflows."""
        wport = host_port(instance or {}, public_api_port(portal, COMFY_WRAPPER_INTERNAL_PORT))
        jport = host_port(instance or {}, JUPYTER_PORT)
        if not (host and wport and jport):
            results.append({"name": COMFYUI_CHECK, "state": "skipped",
                            "detail": "the API wrapper or Jupyter port is not mapped"})
            return _UNAVAILABLE
        if dl.expired():
            results.extend(_deadline_result(n, dl) for n in names)
            return _UNAVAILABLE
        ctx.generate_timeout = int(_env_with_overrides(env, env_overrides, "COMFYUI_GENERATE_TIMEOUT")
                                   or COMFY_GENERATE_TIMEOUT)
        cport = host_port(instance or {}, public_api_port(portal, COMFY_INTERNAL_PORT))
        ctx.comfy = _attach_deadline(endpoint_factory(host, cport, token), dl) if cport else None
        ctx.wrapper = wrapper = _attach_deadline(endpoint_factory(host, wport, token), dl)
        ctx.jupyter = jupyter = _attach_deadline(jupyter_factory(host, jport, token), dl)
        if not own_readiness:
            return _OPEN
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
        if ready:
            return _OPEN
        if dl.expired():
            results.extend(_deadline_result(n, dl) for n in names)
        else:
            results.append({"name": COMFYUI_CHECK, "kind": "structural",
                            "state": "error" if last.get("unanswered") else "failed",
                            "detail": f"ComfyUI not ready after {waited:g}s ({detail}); "
                                      f"the image has no serving test to wait on"})
        return _UNAVAILABLE

    opened = {}                          # endpoint -> what opening it came to
    for i, (t, names) in enumerate(plan):
        on_engine = _on_engine_api(t)
        if on_engine not in opened:
            opened[on_engine] = open_engine(plan[i:]) if on_engine else open_comfyui(names)
            if opened[on_engine] == _STOP:
                return mark(results)
        if opened[on_engine] != _OPEN:
            continue
        if on_engine and names and dl.expired():
            results += [_deadline_result(n, dl) for n in names]
        else:
            results.extend(_guarded(names[0] if names else first, lambda: t.run(ctx)))
    return mark(results)
