#!/usr/bin/env python3
"""The OpenAI API contract, checked from the client (ADR 0059 D5; ADR 0031).

Ported from the per-engine in-image copies (external/vllm, external/vllm-omni,
external/sglang, derivatives/llama-cpp `*.d/contract_check.py`). The check functions
are carried over VERBATIM from the vLLM copy, with their reasons: what changed is
where they run and what decides which of them run.

- WHERE: through the instance's public endpoint (Caddy and the instance token), the
  path a user's client takes, instead of the engine's own port inside the box.
- WHAT RUNS: keyed on the routes the template declares (OPENAI_ROUTES, resolved by
  external_checks.resolve_capabilities). The client knows what it is testing for, so
  a template that does not serve chat is never sent chat.
- ONE MODULE, ONE ENGINE TABLE: the four copies differed only in their ENGINE block
  and had already drifted outside it. Engine differences are now one table, keyed by
  image repository.

Not ported, deliberately:
  - bind-loopback: it reads listeners with `ss` on the box. It stays in the image's
    serving test (ADR 0059 D5).
  - cap:embeddings: discovery would probe an undeclared route. Embeddings is a
    declared route checked by external_checks.check_embeddings.

ADR 0031's refusals bind this file unchanged: every check is FORCED (token
arithmetic, max_tokens=1, a grammar, a named tool, a status code). None samples the
model.

OUTPUT, exit codes and the completion sentinel are as in the in-image checker:
VIOLATION / ADVISORY / ERROR / NA / DEVIATION machine lines, exit 0 clean,
1 violations, 2 could-not-decide, and `CONTRACT-COMPLETE <n>` last.
"""
from __future__ import annotations

import argparse
import http.client
import json
import shlex
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ── Engine table: the only engine-specific data in this file ──────────────────
#
# Keyed by image repository. `args_env` / `model_env` / `caps_env` name the template
# env variables the values come from. Deviations follow ADR 0031 decision 6a: a
# deviation that stops reproducing becomes a violation, so each entry expires by
# itself. SGLang's unknown-model deviation expired on SGLang v0.5.22 (it now refuses
# with 404) and is not carried over.
ENGINES = {
    "vastai/vllm": {
        "name": "vllm", "model_env": "VLLM_MODEL", "args_env": "VLLM_ARGS",
        "caps_env": "VLLM_EXPECT_CAPS", "context_flag": "--max-model-len",
        "tools_flag": "--enable-auto-tool-choice", "served_name_flag": "--served-model-name",
        "deviations": {},
    },
    "vastai/vllm-omni": {
        "name": "vllm-omni", "model_env": "VLLM_MODEL", "args_env": "VLLM_ARGS",
        "caps_env": "VLLM_EXPECT_CAPS", "context_flag": "--max-model-len",
        "tools_flag": "--enable-auto-tool-choice", "served_name_flag": "--served-model-name",
        "deviations": {},
    },
    "vastai/sglang": {
        "name": "sglang", "model_env": "SGLANG_MODEL", "args_env": "SGLANG_ARGS",
        "caps_env": "SGLANG_EXPECT_CAPS", "context_flag": "--context-length",
        # SGLang gates tool calling on a parser choice rather than an enable switch.
        "tools_flag": "--tool-call-parser", "served_name_flag": "--served-model-name",
        "deviations": {},
    },
    "vastai/llama-cpp": {
        "name": "llama", "model_env": "LLAMA_MODEL", "args_env": "LLAMA_ARGS",
        "caps_env": "LLAMA_EXPECT_CAPS", "context_flag": "--ctx-size",
        "tools_flag": "--jinja", "served_name_flag": "--alias",
        "deviations": {
            "error-unknown-model":
                "llama-server does not police the request's `model` field — it answers "
                "HTTP 200 and serves whatever was loaded. UPSTREAM: no flag changes it. "
                "BOUNDED: `identity` asserts /v1/models advertises EXACTLY --alias, so a "
                "client that reads the model list can always tell what it is talking to.",
            "error-context-overflow":
                "max_tokens beyond --ctx-size is accepted (HTTP 200) and silently clamped "
                "rather than refused. UPSTREAM. BOUNDED: `token-arithmetic` and "
                "`finish-reason` both pass, so the response reports what was actually "
                "produced and a caller can see it got fewer tokens than it asked for.",
        },
    },
}
ENGINE = ENGINES["vastai/vllm"]          # the engine of the current run (set_engine)


def set_engine(image: str) -> dict | None:
    """Select the engine row for an image (`repo` or `repo:tag`); None if unknown."""
    global ENGINE
    row = ENGINES.get(str(image or "").split(":", 1)[0])
    if row is not None:
        ENGINE = row
    return row


PROBE = [{"role": "user", "content": "contract probe"}]
NO_SUCH_MODEL = "__vast_contract_no_such_model__"
TOOL_NAME = "vast_contract_probe"



# ─────────────────────────────────────────────────────────────── arg parsing

def arg_values(tokens: list[str], name: str) -> list[str]:
    """Values given to `--name` in a vLLM arg string, `--name=v` and `--name v ...`.

    vLLM takes several of these as nargs='+' (`--served-model-name a b`), so this
    returns a LIST and collects until the next option. Returns [] when the flag is
    absent and [""] never — an option present with no value yields [].
    """
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == name:
            i += 1
            while i < len(tokens) and not tokens[i].startswith("-"):
                out.append(tokens[i])
                i += 1
            continue
        if tok.startswith(name + "="):
            out.append(tok[len(name) + 1:])
        i += 1
    return out


def arg_present(tokens: list[str], name: str) -> bool:
    return any(t == name or t.startswith(name + "=") for t in tokens)


def first(values: list[str], default: str = "") -> str:
    return values[0] if values else default


# ─────────────────────────────────────────────────────────────── reporting

class Report:
    """Human lines and machine lines are the same findings, emitted once each.

    The caller is a shell test that has to turn findings into fail_later labels, and
    a log a human reads when a cell goes red. Deriving one from the other by parsing
    prose is how log formats become APIs by accident, so both are written here.
    """

    def __init__(self, out=sys.stdout, deviations=None) -> None:
        self.out = out
        self.deviations = dict(ENGINE.get("deviations", {}) if deviations is None else deviations)
        self.violations: list[tuple[str, str]] = []
        self.advisories: list[tuple[str, str]] = []
        self.errors: list[tuple[str, str]] = []
        self.na: list[tuple[str, str]] = []
        self.deviated: list[tuple[str, str]] = []
        self.passed: list[str] = []

    def _emit(self, human: str, machine: str = "") -> None:
        print(human, file=self.out)
        if machine:
            print(machine, file=self.out)

    def ok(self, check: str, detail: str = "") -> None:
        # A declared deviation that PASSES is the declaration outliving the defect.
        # Reported as a violation so the exemption expires by itself: the engine
        # behaves, the gate says so, and someone deletes the line. Without this,
        # a deviation is just an exemption with better manners.
        if check in self.deviations:
            self.violation(check,
                           "declared a known deviation, but the engine now behaves "
                           "correctly — delete the entry from ENGINE['deviations'] "
                           f"(was: {self.deviations.pop(check)})")
            return
        self.passed.append(check)
        self._emit(f"    ok       {check}" + (f" — {detail}" if detail else ""))

    def violation(self, check: str, detail: str) -> None:
        why = self.deviations.get(check)
        if why:
            self.deviated.append((check, detail))
            self._emit(f"    DEVIATION {check} — {detail}\n             known and declared: {why}",
                       f"DEVIATION {check} {detail}")
            return
        self.violations.append((check, detail))
        self._emit(f"    VIOLATION {check} — {detail}", f"VIOLATION {check} {detail}")

    def advisory(self, check: str, detail: str) -> None:
        self.advisories.append((check, detail))
        self._emit(f"    advisory {check} — {detail}", f"ADVISORY {check} {detail}")

    def error(self, check: str, detail: str) -> None:
        self.errors.append((check, detail))
        self._emit(f"    ERROR    {check} — {detail}", f"ERROR {check} {detail}")

    def not_applicable(self, check: str, reason: str) -> None:
        self.na.append((check, reason))
        self._emit(f"    n/a      {check} — {reason}", f"NA {check} {reason}")

    def exit_code(self) -> int:
        if self.errors:
            return 2
        return 1 if self.violations else 0


# ─────────────────────────────────────────────────────────────── HTTP

DEADLINE_EXCEEDED = "deadline exceeded"

# OpenSSL's reasons when the peer answered the ClientHello with bytes that are not a
# TLS record — a plain-http server replying `400 Bad Request` to it. Only these show
# the port does not speak TLS, so only these move a request to http.
_NOT_TLS_REASONS = ("WRONG_VERSION_NUMBER", "RECORD_LAYER_FAILURE", "PACKET_LENGTH_TOO_LONG",
                    "UNKNOWN_PROTOCOL")


def _cause(exc: BaseException) -> BaseException:
    """The socket/TLS error urllib wrapped in a URLError (not an HTTPError: that is an
    answer), else the exception itself."""
    if (isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError)
            and isinstance(exc.reason, BaseException)):
        return exc.reason
    return exc


def not_tls(exc: BaseException) -> bool:
    """True when a failed https attempt shows the port is not speaking TLS.

    A TLS-layer error whose reason is a malformed record (wrong version number,
    record-layer failure, ...). An EOF, a timeout, a refused or reset connection is
    NOT this: those say nothing about the scheme, and answering them with plain http
    would send the token in the clear to a port that may well be TLS.
    """
    e = _cause(exc)
    if not isinstance(e, ssl.SSLError) or isinstance(
            e, (ssl.SSLEOFError, ssl.SSLZeroReturnError, ssl.SSLCertVerificationError)):
        return False
    reason = str(getattr(e, "reason", "") or "").upper()
    text = str(e).upper().replace(" ", "_")
    return reason in _NOT_TLS_REASONS or any(r in text for r in _NOT_TLS_REASONS)


class Deadline:
    """One time budget shared by every request of a phase. `clock` is injectable."""

    def __init__(self, seconds: float, clock=time.monotonic) -> None:
        self.seconds, self.clock = seconds, clock
        self.end = clock() + seconds

    def remaining(self) -> float:
        return self.end - self.clock()

    def expired(self) -> bool:
        return self.remaining() <= 0


class _Phase:
    """Whether the connection was established (TCP, and the TLS handshake for https)
    before a request failed. Before that, the request provably never left."""

    def __init__(self) -> None:
        self.connected = False


def _tracked(conn_class, phase: _Phase):
    def make(*a, **k):
        conn = conn_class(*a, **k)
        connect = conn.connect

        def tracked_connect():
            connect()
            phase.connected = True
        conn.connect = tracked_connect
        return conn
    return make


class _HTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, phase: _Phase) -> None:
        super().__init__()
        self.phase = phase

    def http_open(self, req):
        return self.do_open(_tracked(http.client.HTTPConnection, self.phase), req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, phase: _Phase, context) -> None:
        super().__init__(context=context)
        self.phase = phase

    def https_open(self, req):
        return self.do_open(_tracked(http.client.HTTPSConnection, self.phase), req,
                            context=self._context)


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str):
    u = urllib.parse.urlsplit(url)
    return u.scheme, (u.hostname or "").lower(), u.port or _DEFAULT_PORTS.get(u.scheme)


class _SameOriginRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to the same host and port, and never from https down
    to http. Anything else is refused: urllib then raises the 3xx as an HTTPError — an
    answer, pinned like any other — so the token never follows a redirect to another
    host or into the clear. Same-origin redirects (a trailing slash on Jupyter's
    contents API) are still followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old_scheme, old_host, old_port = _origin(req.full_url)
        new_scheme, new_host, new_port = _origin(urllib.parse.urljoin(req.full_url, newurl))
        if (old_scheme == "https" and new_scheme != "https") or (old_host, old_port) != (new_host, new_port):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Transport:
    """https-first HTTP to the instance's public endpoint, shared by Client (below) and
    external_checks.Endpoint. open() never raises.

    Caddy serves https with a self-signed certificate on some images and plain http on
    others, so https is tried first, without verification. The policy, each part
    pinned by tests:

    - FALLBACK only on a TLS-level failure that shows the port is not speaking TLS
      (not_tls). A timeout, a refused or reset connection, or any HTTP status over
      https never causes an http attempt.
    - PINNING only on an answer: once https returns any HTTP status (4xx/5xx
      included) the scheme is https for the life of the object; http is pinned only
      after http answered following a not-TLS failure. A failed attempt pins nothing.
      So once https has answered, the token is never sent over http by this object.
    - RETRIES (up to `retries`) only when the request provably never reached the
      server: the connection was never established (refused, DNS failure, connect or
      handshake timeout). Once connected, a failure — a read timeout, a reset after
      sending — is returned as no response and never re-sent: generation requests
      are not idempotent and are expensive.
    - DEADLINE: with a `deadline` (Deadline), each attempt's timeout is capped by the
      time remaining, and no attempt starts once it has passed.
    - REDIRECTS are followed only to the same host and port, never from https to
      http (_SameOriginRedirects); a refused one is returned as its 3xx status.

    `opener(request, timeout, phase)` replaces the network in tests; it must set
    phase.connected once the connection is up, as the real one does.
    """

    def __init__(self, base: str, token: str | None = None, timeout: float = 120.0, *,
                 retries: int = 0, sleep=time.sleep, deadline: Deadline | None = None,
                 opener=None) -> None:
        base = base.rstrip("/")
        if base.startswith(("http://", "https://")):
            self.bases = [base]
        else:
            self.bases = [f"https://{base}", f"http://{base}"]
        self.token = token
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep
        self.deadline = deadline
        self._opener = opener or self._urlopen
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE

    def _urlopen(self, req, timeout, phase):
        opener = urllib.request.build_opener(_HTTPHandler(phase), _HTTPSHandler(phase, self._ctx),
                                             _SameOriginRedirects())
        return opener.open(req, timeout=timeout)

    def open(self, path: str, data: bytes | None = None, headers: dict | None = None,
             timeout: float | None = None):
        """(response, None) for any HTTP answer — an HTTPError for a 4xx/5xx, read like
        a response — or (None, why) when there was no HTTP response at all."""
        last = "no attempt"
        for attempt in range(self.retries + 1):
            retry = False
            for base in list(self.bases):
                limit = timeout or self.timeout
                if self.deadline is not None:
                    left = self.deadline.remaining()
                    if left <= 0:
                        return None, (f"{DEADLINE_EXCEEDED} before {path} was sent"
                                      + (f" (last attempt: {last})" if attempt else ""))
                    limit = min(limit, left)
                hdrs = {"Content-Type": "application/json"}
                if self.token:
                    hdrs["Authorization"] = f"Bearer {self.token}"
                hdrs.update(headers or {})
                req = urllib.request.Request(f"{base}{path}", data=data, headers=hdrs,
                                             method="POST" if data is not None else "GET")
                phase = _Phase()
                try:
                    r = self._opener(req, limit, phase)
                except urllib.error.HTTPError as e:
                    self.bases = [base]                   # an answer: pin this scheme
                    return e, None
                except Exception as e:                            # noqa: BLE001
                    last = f"{type(e).__name__}: {e}"
                    if (base.startswith("https://") and len(self.bases) > 1
                            and not phase.connected and not_tls(e)):
                        continue                          # the port speaks plain http
                    retry = not phase.connected and not isinstance(_cause(e), ssl.SSLError)
                    break
                self.bases = [base]                       # an answer: pin this scheme
                return r, None
            if not retry or attempt >= self.retries:
                break
            pause = 2 * (attempt + 1)
            if self.deadline is not None and self.deadline.remaining() <= pause:
                return None, f"{DEADLINE_EXCEEDED} before {path} could be retried (last attempt: {last})"
            self.sleep(pause)
        return None, last


def _status(r) -> int:
    return r.status if hasattr(r, "status") else r.code


class Client(Transport):
    """The engine API through the instance's public endpoint (Caddy + token).

    Same interface as the in-image checker's Client — post / get / post_stream, each
    returning (status, body) and never raising — so the ported checks are unchanged.
    Status 0 means no HTTP response (unreachable, timed out, the body was lost, or
    the deadline passed). Scheme, retry and deadline policy: Transport.
    """

    def __init__(self, base: str, token: str | None = None, timeout: float = 120.0,
                 **kwargs) -> None:
        super().__init__(base, token, timeout, **kwargs)

    def _open(self, path, data=None, *, stream=False):
        return self.open(path, data)

    def post(self, path: str, payload, *, raw: bytes | None = None) -> tuple[int, str]:
        body = raw if raw is not None else json.dumps(payload).encode()
        r, err = self._open(path, body)
        if r is None:
            return 0, err
        try:
            with r:
                return _status(r), r.read().decode("utf-8", "replace")
        except Exception as e:                                    # noqa: BLE001
            return 0, f"{type(e).__name__} reading the response: {e}"

    def get(self, path: str) -> tuple[int, str]:
        r, err = self._open(path)
        if r is None:
            return 0, err
        try:
            with r:
                return _status(r), r.read().decode("utf-8", "replace")
        except Exception as e:                                    # noqa: BLE001
            return 0, f"{type(e).__name__} reading the response: {e}"

    def post_stream(self, path: str, payload) -> tuple[int, list[str]]:
        r, err = self._open(path, json.dumps(payload).encode(), stream=True)
        if r is None:
            return 0, [err]
        try:
            with r:
                status = _status(r)
                if status != 200:
                    return status, [r.read().decode("utf-8", "replace")]
                return status, [ln.decode("utf-8", "replace").rstrip("\r\n") for ln in r]
        except Exception as e:                                    # noqa: BLE001
            return 0, [f"{type(e).__name__} reading the stream: {e}"]


def as_json(body: str):
    try:
        return json.loads(body)
    except Exception:                                             # noqa: BLE001
        return None


def usage_of(doc) -> dict:
    return (doc or {}).get("usage") or {}


# ─────────────────────────────────────────────────────────────── contract tier

def check_identity(rep: Report, models_doc, expected: str) -> None:
    """The served id must EXACTLY equal what the template asked to be served.

    This was a WARN behind `any(want in mid or mid in want for mid in ids)` — a
    BIDIRECTIONAL substring test, which matches a base model served under an
    instruct name and vice versa. Exact equality, as a finding, per ADR 0031
    decision 2. `in ids` rather than `== ids[0]`: LoRA adapters and
    `--served-model-name` aliases legitimately add entries, and forbidding those
    would red a healthy image.
    """
    ids = [m.get("id", "") for m in (models_doc or {}).get("data", [])]
    if not ids:
        rep.error("identity", "/v1/models returned no model entries")
        return
    if expected in ids:
        rep.ok("identity", f"serving '{expected}' exactly")
        return
    rep.violation("identity",
                  f"served ids {ids} do not contain '{expected}' exactly — a model "
                  "other than the one requested is being served")


def check_token_arithmetic(rep: Report, cli: Client, model: str) -> dict:
    """max_tokens=1 forces the answer: exactly one completion token, stopped by length.

    Returns the usage dict so the round-trip check can reuse this request's
    prompt_tokens rather than paying for a second identical one.
    """
    status, body = cli.post("/v1/chat/completions", {
        "model": model, "messages": PROBE, "temperature": 0, "max_tokens": 1,
    })
    doc = as_json(body)
    if status != 200 or doc is None:
        rep.error("token-arithmetic", f"chat completion failed: HTTP {status} {body[:200]}")
        return {}
    usage = usage_of(doc)
    ct, pt, tt = usage.get("completion_tokens"), usage.get("prompt_tokens"), usage.get("total_tokens")
    if ct != 1:
        rep.violation("token-arithmetic",
                      f"max_tokens=1 produced completion_tokens={ct!r} — the server is "
                      "not honouring the cap, or is not accounting for what it emitted")
    elif not isinstance(pt, int) or pt <= 0:
        rep.violation("token-arithmetic", f"prompt_tokens={pt!r} for a non-empty prompt")
    elif tt != (pt or 0) + 1:
        rep.violation("token-arithmetic",
                      f"total_tokens={tt!r} != prompt_tokens({pt}) + completion_tokens(1)")
    else:
        rep.ok("token-arithmetic", f"prompt={pt} completion=1 total={tt}")

    finish = ((doc.get("choices") or [{}])[0]).get("finish_reason")
    # `length` is the only correct answer here: the cap, not the model, ended it.
    # A model that emits EOS as its first token would report `stop`, which is why
    # this is reported separately rather than folded into the arithmetic above —
    # it is the softer of the two claims.
    if finish == "length":
        rep.ok("finish-reason", "max_tokens=1 -> finish_reason=length")
    else:
        rep.violation("finish-reason",
                      f"max_tokens=1 gave finish_reason={finish!r}, expected 'length'")
    return usage


def _token_count(encoded) -> int | None:
    """Number of prompt tokens in whatever apply_chat_template handed back.

    `len()` on the raw return value is WRONG and was: newer transformers returns a
    BatchEncoding from `tokenize=True`, and len() of that is the number of KEYS —
    input_ids and attention_mask — so a 31-token Qwen prompt measured as 2 and the
    check reported "the served chat template is not the model's own" against a server
    that was entirely correct. Caught on the first serverless cell, while the
    assertion was still advisory, which is the whole reason ADR 0031 decision 7 lands
    new assertions that way.

    Handles the four shapes the API returns across versions: a flat list of ids, a
    batch of one (list of lists), a BatchEncoding/dict carrying input_ids, and an
    object exposing .input_ids. Returns None rather than guessing on anything else —
    a wrong count here is indistinguishable from a real defect.
    """
    ids = encoded
    if hasattr(ids, "input_ids"):
        ids = ids.input_ids
    elif isinstance(ids, dict):
        if "input_ids" not in ids:
            return None
        ids = ids["input_ids"]
    if not isinstance(ids, (list, tuple)) or not ids:
        return None
    first = ids[0]
    if isinstance(first, (list, tuple)):          # batch of one
        return len(first) if len(ids) == 1 else None
    if isinstance(first, int):
        return len(ids)
    return None


def check_prompt_roundtrip(rep: Report, args, tokens: list[str], usage: dict,
                           counter=None) -> None:
    """Apply the chat template on the box and assert the server counted the same.

    Exact, deterministic, needs no golden data, and works on any model — and it is
    the only check in this file that can see a SILENTLY REPLACED chat template or a
    BOS double-add, which is the one defect class in this family with real
    precedent. A local count of prompt_tokens+1 is the double-add signature.

    Everything that could make the two legitimately differ is discovered and turned
    into an n/a rather than a violation: `--chat-template` overrides the template
    the server uses, so the local one is a different function.

    `counter(ref, kwargs)` returns ("count", encoded), ("na", reason) when the box
    says the check does not apply (no chat template, no transformers there), or
    ("error", reason) when it could not be run (SSH, the remote script, the
    deadline). With no counter the check is n/a; it never runs on the client.
    """
    if not usage.get("prompt_tokens"):
        rep.not_applicable("prompt-roundtrip", "no prompt_tokens from the probe request")
        return
    if arg_present(tokens, "--chat-template") or arg_present(tokens, "--chat-template-content-format"):
        rep.not_applicable("prompt-roundtrip",
                           "--chat-template overrides the template the server applies, so "
                           "the local tokenizer's template is a different function")
        return
    ref = first(arg_values(tokens, "--tokenizer")) or args.model
    kwargs = {}
    dl = first(arg_values(tokens, "--download-dir"))
    if dl:
        kwargs["cache_dir"] = dl
    rev = first(arg_values(tokens, "--tokenizer-revision")) or first(arg_values(tokens, "--revision"))
    if rev:
        kwargs["revision"] = rev
    if arg_present(tokens, "--trust-remote-code"):
        kwargs["trust_remote_code"] = True

    # WHERE the tokenizer runs: on the box, through `counter` (remote.py: the model's
    # tokenizer is cached there, with the engine's transformers and its own
    # trust_remote_code). Never here: loading a model repo's tokenizer on the client
    # would execute that repo's code on our machines. No counter, no check.
    if counter is None:
        rep.not_applicable("prompt-roundtrip",
                           "no box to count on: the tokenizer is only ever run on the instance")
        return
    kind, value = counter(ref, kwargs)
    if kind != "count":
        (rep.error if kind == "error" else rep.not_applicable)("prompt-roundtrip", value)
        return
    local, n_local = value, _token_count(value)
    if n_local is None:
        rep.error("prompt-roundtrip",
                  f"apply_chat_template returned {type(local).__name__}, which this check "
                  "cannot count — refusing to guess a token count")
        return

    served = usage["prompt_tokens"]
    if n_local == served:
        rep.ok("prompt-roundtrip", f"local chat-template tokenization == prompt_tokens ({served})")
    elif n_local == served - 1:
        rep.violation("prompt-roundtrip",
                      f"server counted {served} prompt tokens, local template yields {n_local} "
                      "— one extra special token, the BOS double-add signature")
    else:
        rep.violation("prompt-roundtrip",
                      f"server counted {served} prompt tokens, local chat template yields "
                      f"{n_local} — the served chat template is not the model's own")


def check_completions_route(rep: Report, cli: Client, model: str) -> None:
    """/v1/completions, which the suite has never touched and the worker depends on.

    The serverless benchmark in vastai/serverless drives `/v1/completions`, not chat
    (see ADR 0031 decision 4). A deployment where only the chat route works looks
    entirely healthy to today's tests and produces no score.
    """
    status, body = cli.post("/v1/completions", {
        "model": model, "prompt": "contract probe", "temperature": 0, "max_tokens": 1,
    })
    doc = as_json(body)
    if status == 0:
        rep.error("completions-route", f"request could not be made: {body[:200]}")
        return
    if status != 200 or doc is None:
        rep.violation("completions-route",
                      f"/v1/completions returned HTTP {status} — the serverless worker's "
                      f"benchmark drives this route, not chat: {body[:200]}")
        return
    ct = usage_of(doc).get("completion_tokens")
    if ct != 1:
        rep.violation("completions-route", f"max_tokens=1 gave completion_tokens={ct!r}")
    else:
        rep.ok("completions-route", "/v1/completions honours max_tokens=1")


def check_context_overflow(rep: Report, cli: Client, model: str, models_doc, tokens: list[str]) -> None:
    """Asking for more tokens than the context holds must be a client error.

    The length comes from the server's own `/v1/models` where it publishes it, and
    from `--max-model-len` otherwise; with neither this is n/a rather than a guess,
    because a guessed context length turns this into a sampling assertion.
    """
    length = None
    for m in (models_doc or {}).get("data", []):
        if m.get("id") == model and isinstance(m.get("max_model_len"), int):
            length = m["max_model_len"]
            break
    if length is None:
        declared = first(arg_values(tokens, ENGINE["context_flag"]))
        if declared.isdigit():
            length = int(declared)
    if length is None:
        rep.not_applicable("error-context-overflow",
                           "neither /v1/models nor --max-model-len publishes a context length")
        return
    status, body = cli.post("/v1/chat/completions", {
        "model": model, "messages": PROBE, "temperature": 0, "max_tokens": length + 64,
    })
    if status == 0:
        rep.error("error-context-overflow", f"request could not be made: {body[:200]}")
    elif 400 <= status < 500:
        rep.ok("error-context-overflow", f"max_tokens > context ({length}) refused with HTTP {status}")
    else:
        rep.violation("error-context-overflow",
                      f"max_tokens={length + 64} against a {length}-token context gave "
                      f"HTTP {status}, expected a 4xx refusal")


def check_streaming(rep: Report, cli: Client, model: str) -> None:
    """SSE framing and terminator, plus usage when the client asks for it.

    Asserted: at least one `data:` chunk, a `data: [DONE]` terminator, and — because
    `stream_options.include_usage` is what the serverless worker uses to account for
    what it served — a usage block on the final chunk whose completion_tokens agrees
    with the same max_tokens=1 cap the non-streaming path honoured. Not asserted:
    how many chunks, or what is in them.
    """
    status, lines = cli.post_stream("/v1/chat/completions", {
        "model": model, "messages": PROBE, "temperature": 0, "max_tokens": 1,
        "stream": True, "stream_options": {"include_usage": True},
    })
    if status == 0:
        rep.error("streaming", f"request could not be made: {' '.join(lines)[:200]}")
        return
    if status != 200:
        rep.violation("streaming", f"stream request returned HTTP {status}: {' '.join(lines)[:200]}")
        return
    payloads = [ln[len("data:"):].strip() for ln in lines if ln.startswith("data:")]
    if not payloads:
        rep.violation("streaming", "no `data:` frames in the response — SSE framing is broken")
        return
    if "[DONE]" not in payloads:
        rep.violation("streaming",
                      "stream never sent `data: [DONE]` — a client reading to the "
                      "terminator hangs until its own timeout")
        return
    usages = [u for u in (as_json(p) for p in payloads if p != "[DONE]") if usage_of(u)]
    if not usages:
        rep.violation("streaming",
                      "stream_options.include_usage was set and no chunk carried a usage "
                      "block — the worker cannot account for what it served")
        return
    ct = usage_of(usages[-1]).get("completion_tokens")
    if ct != 1:
        rep.violation("streaming", f"streamed usage reports completion_tokens={ct!r} for max_tokens=1")
    else:
        rep.ok("streaming", f"{len(payloads)} frames, [DONE] terminated, usage present")


# ─────────────────────────────────────────────────────────── capability tiers

NOT_OFFERED = (400, 404, 422, 501)
# A probe's verdict when it could not decide: no HTTP response at all. Reported as an
# error, never as a capability that is absent or broken.
UNDECIDED = "undecided"


def cap_tools(cli: Client, model: str, tokens: list[str]):
    """Forced tool choice: the model has no say in whether a call is emitted."""
    if not arg_present(tokens, ENGINE["tools_flag"]):
        return None, f"{ENGINE['tools_flag']} not in {ENGINE['args_env']}"
    tool = {"type": "function", "function": {
        "name": TOOL_NAME, "description": "contract probe",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                       "required": ["city"]}}}
    status, body = cli.post("/v1/chat/completions", {
        "model": model, "messages": [{"role": "user", "content": "probe London"}],
        "temperature": 0, "max_tokens": 64, "tools": [tool],
        "tool_choice": {"type": "function", "function": {"name": TOOL_NAME}},
    })
    doc = as_json(body)
    if status == 0:
        return UNDECIDED, f"forced tool_choice request could not be made: {body[:200]}"
    if status != 200 or doc is None:
        return False, f"forced tool_choice returned HTTP {status}: {body[:200]}"
    calls = ((doc.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
    if not calls:
        return False, ("tool_choice named a function and no tool_calls came back — the "
                       "tool parser is not wired to the template")
    name = (calls[0].get("function") or {}).get("name")
    if name != TOOL_NAME:
        return False, f"tool_calls[0] is {name!r}, not the forced {TOOL_NAME!r}"
    raw = (calls[0].get("function") or {}).get("arguments")
    if not isinstance(as_json(raw or ""), dict):
        return False, f"tool call arguments are not a JSON object: {str(raw)[:120]}"
    return True, f"forced tool call returned {TOOL_NAME} with JSON arguments"


def cap_structured(cli: Client, model: str, _tokens: list[str]):
    """A grammar makes the OUTPUT decidable without judging the model."""
    schema = {"type": "object", "properties": {"n": {"type": "integer"}},
              "required": ["n"], "additionalProperties": False}
    status, body = cli.post("/v1/chat/completions", {
        "model": model, "messages": [{"role": "user", "content": "emit an integer"}],
        "temperature": 0, "max_tokens": 64,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "probe", "schema": schema, "strict": True}},
    })
    doc = as_json(body)
    if status == 0:
        return UNDECIDED, f"json_schema request could not be made: {body[:200]}"
    if status in NOT_OFFERED:
        return None, f"structured output not offered here (HTTP {status})"
    if status != 200 or doc is None:
        return False, f"json_schema request returned HTTP {status}: {body[:200]}"
    content = ((doc.get("choices") or [{}])[0].get("message") or {}).get("content")
    parsed = as_json(content or "")
    if not isinstance(parsed, dict):
        return False, f"grammar-constrained output did not parse as an object: {str(content)[:120]}"
    if not isinstance(parsed.get("n"), int):
        return False, f"output does not satisfy its own schema (n missing or not an integer): {parsed}"
    return True, "json_schema output parses and satisfies the schema"


CAPABILITIES = {
    "tools": cap_tools,
    "structured-output": cap_structured,
}


def run_capabilities(rep: Report, cli: Client, model: str, tokens: list[str],
                     declared: set[str]) -> None:
    """The declaration CONSTRAINS; it never selects (ADR 0031 decision 6).

                     discovered            not discovered
        declared     runs, required        VIOLATION — claimed and absent
        not declared runs, advisory        n/a, logged with the reason

    The asymmetry is the whole point: drift in the discovery layer can lose
    advisory coverage, loudly, but can never block a healthy image, and can never
    silently drop something a human declared.

    A probe that got no HTTP response (UNDECIDED) is an error either way: it decided
    nothing, so it is neither absent, broken, nor present.
    """
    unknown = declared - set(CAPABILITIES)
    if unknown:
        rep.error("capabilities",
                  f"{ENGINE['caps_env']} names unknown capabilities {sorted(unknown)} — "
                  f"known: {sorted(CAPABILITIES)}")
    for name, probe in sorted(CAPABILITIES.items()):
        try:
            verdict, detail = probe(cli, model, tokens)
        except Exception as e:                                    # noqa: BLE001
            rep.error(f"cap:{name}", f"probe raised {type(e).__name__}: {e}")
            continue
        if verdict == UNDECIDED:
            rep.error(f"cap:{name}", detail)
            continue
        if verdict is None:                       # not discovered on this box
            if name in declared:
                rep.violation(f"cap:{name}",
                              f"declared in {ENGINE['caps_env']} but not discovered — {detail}")
            else:
                rep.not_applicable(f"cap:{name}", detail)
        elif verdict:
            rep.ok(f"cap:{name}", detail + ("" if name in declared else " (advisory)"))
        elif name in declared:
            rep.violation(f"cap:{name}", detail)
        else:
            rep.advisory(f"cap:{name}", detail)


# ─────────────────────────────────────────────────────────── counting on the box

# Runs on the instance (remote.RemoteSession), with the first Python there that has
# transformers. It prints one JSON line: {"encoded": ...} (the template's token ids,
# as a list or {"input_ids": [...]}) or {"na": reason}. The verdict stays here.
REMOTE_COUNT_SCRIPT = r"""
import json, sys
cfg = json.loads(sys.argv[1] if len(sys.argv) > 1 else %(cfg)r)
try:
    from transformers import AutoTokenizer
except Exception as e:
    print(json.dumps({"na": f"transformers not importable on the box ({e})"})); sys.exit()
try:
    tok = AutoTokenizer.from_pretrained(cfg["ref"], **cfg["kwargs"])
except Exception as e:
    print(json.dumps({"na": f"could not load the tokenizer on the box ({type(e).__name__}: {e})"})); sys.exit()
if not getattr(tok, "chat_template", None):
    print(json.dumps({"na": "tokenizer declares no chat template"})); sys.exit()
enc = tok.apply_chat_template(cfg["probe"], add_generation_prompt=True, tokenize=True)
if hasattr(enc, "input_ids"):
    enc = {"input_ids": list(enc.input_ids)}
elif isinstance(enc, dict) and "input_ids" in enc:
    enc = {"input_ids": list(enc["input_ids"])}
print(json.dumps({"encoded": enc}, default=list))
"""

# The engine's interpreter is not at one path across images: use the first that has
# transformers, else report that none does.
REMOTE_PYTHON = ("sh -c 'for p in /venv/main/bin/python /opt/venv/bin/python /usr/bin/python3 "
                 "/usr/local/bin/python3 python3; do \"$p\" -c \"import transformers\" "
                 "2>/dev/null && exec \"$p\" -; done; "
                 "echo \"{\\\"na\\\": \\\"no python with transformers on the box\\\"}\"'")


def remote_counter(session):
    """A prompt-roundtrip counter that tokenizes on the instance via `session`
    (remote.RemoteSession). Returns ("count", encoded), ("na", reason) when the box
    reports the check does not apply, or ("error", reason) when it could not be run:
    no session, SSH refused or timed out, the script failed, or its answer is
    unreadable. The check was runnable, so not running it is never n/a."""
    def count(ref, kwargs):
        script = REMOTE_COUNT_SCRIPT % {"cfg": json.dumps(
            {"ref": ref, "kwargs": kwargs, "probe": PROBE})}
        try:
            ok, out = session.run(script, python=REMOTE_PYTHON)
        except Exception as e:                                    # noqa: BLE001
            return "error", f"could not count on the box: {type(e).__name__}: {e}"
        if not ok:
            return "error", f"could not count on the box: {out}"
        line = (out or "").strip().splitlines()[-1:] or [""]
        doc = as_json(line[0])
        if not isinstance(doc, dict):
            return "error", f"unreadable answer from the box: {line[0][:120]}"
        if "na" in doc:
            return "na", doc["na"]
        return "count", doc.get("encoded")
    return count


# ─────────────────────────────────────────────────────────── route keying

def probe_route(routes: set[str]):
    """(path, payload builder) for the error-path checks, on a route the template
    declares: chat, else completions, else embeddings; None when it declares none of
    them (an error probe must never be sent to an undeclared route)."""
    if "chat" in routes:
        return "/v1/chat/completions", lambda model: {
            "model": model, "messages": PROBE, "temperature": 0, "max_tokens": 1}
    if "completions" in routes:
        return "/v1/completions", lambda model: {
            "model": model, "prompt": "contract probe", "temperature": 0, "max_tokens": 1}
    if "embeddings" in routes:
        return "/v1/embeddings", lambda model: {"model": model, "input": ["contract probe"]}
    return None


def check_unknown_model_on(rep: Report, cli: Client, route) -> None:
    """check_unknown_model on the declared route (same verdicts)."""
    path, build = route
    payload = build(NO_SUCH_MODEL)
    status, body = cli.post(path, payload)
    if status == 0:
        rep.error("error-unknown-model", f"request could not be made: {body[:200]}")
    elif 400 <= status < 500:
        rep.ok("error-unknown-model", f"unknown model refused with HTTP {status} on {path}")
    elif status == 200:
        rep.violation("error-unknown-model",
                      f"a request for a nonexistent model was ANSWERED (HTTP 200) on {path} — "
                      "the server is serving something other than what was asked for")
    else:
        rep.violation("error-unknown-model",
                      f"unknown model gave HTTP {status} on {path}, expected a 4xx refusal")


def check_malformed_body_on(rep: Report, cli: Client, route) -> None:
    """check_malformed_body on the declared route (same verdicts)."""
    path, _ = route
    status, body = cli.post(path, None, raw=b"{")
    if status == 0:
        rep.error("error-malformed-body", f"request could not be made: {body[:200]}")
    elif 400 <= status < 500:
        rep.ok("error-malformed-body", f"malformed JSON refused with HTTP {status} on {path}")
    else:
        rep.violation("error-malformed-body",
                      f"a truncated JSON body gave HTTP {status} on {path}, expected a 4xx refusal")


# ─────────────────────────────────────────────────────────────── entry point

CHAT_ONLY = ("token-arithmetic", "finish-reason", "prompt-roundtrip", "error-context-overflow",
             "streaming", "cap:tools", "cap:structured-output")


def run_contract(rep: Report, cli: Client, *, model: str, engine_args: str,
                 expect_caps: str, routes: set[str], counter=None) -> int:
    """Run the contract for one deployment and return the exit code (via _finish)."""
    out = rep.out
    try:
        tokens = shlex.split(engine_args or "")
    except ValueError as e:
        rep.error("engine-args", f"{ENGINE['args_env']} is not parseable as a shell word list: {e}")
        return _finish(rep, out)

    status, body = cli.get("/v1/models")
    models_doc = as_json(body)
    if status != 200 or models_doc is None:
        rep.error("identity", f"/v1/models returned HTTP {status}: {body[:200]}")
        return _finish(rep, out)

    # `--served-model-name` is what the id becomes when it is given; vLLM uses the
    # FIRST as the id and the rest as aliases, so the first is what must come back.
    expected = first(arg_values(tokens, ENGINE["served_name_flag"]), model)
    if not expected:
        rep.error("identity", f"neither {ENGINE['model_env']} nor "
                              f"{ENGINE['served_name_flag']} names a model")
        return _finish(rep, out)
    check_identity(rep, models_doc, expected)
    ids = [m.get("id", "") for m in models_doc.get("data", [])]
    served = expected if expected in ids else (ids[0] if ids else expected)

    if "chat" in routes:
        usage = check_token_arithmetic(rep, cli, served)
        check_prompt_roundtrip(rep, argparse.Namespace(model=model), tokens, usage, counter)
        check_context_overflow(rep, cli, served, models_doc, tokens)
        check_streaming(rep, cli, served)
    else:
        for name in CHAT_ONLY[:5]:
            rep.not_applicable(name, "chat is not a declared route")
    if "completions" in routes:
        check_completions_route(rep, cli, served)
    else:
        rep.not_applicable("completions-route", "completions is not a declared route")
    route = probe_route(routes)
    if route:
        check_unknown_model_on(rep, cli, route)
        check_malformed_body_on(rep, cli, route)
    else:
        for name in ("error-unknown-model", "error-malformed-body"):
            rep.not_applicable(name, "no chat, completions or embeddings route is declared")

    declared = {c for c in (expect_caps or "").replace(",", " ").split() if c}
    if "chat" in routes:
        run_capabilities(rep, cli, served, tokens, declared)
    else:
        for name in sorted(CAPABILITIES):
            if f"cap:{name}" in declared or name in declared:
                rep.violation(f"cap:{name}", f"declared in {ENGINE['caps_env']} but chat "
                                             "is not a declared route")
            else:
                rep.not_applicable(f"cap:{name}", "chat is not a declared route")
    return _finish(rep, out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="OpenAI API contract checks from the client (ADR 0059 D5)")
    p.add_argument("--base-url", required=True,
                   help="host:port of the public API endpoint (scheme optional)")
    p.add_argument("--token", default=None, help="the instance token (Bearer)")
    p.add_argument("--engine", default="vastai/vllm", help="image repository: " + ", ".join(ENGINES))
    p.add_argument("--routes", default="chat,completions",
                   help="declared route capabilities (default: pyworker's chat,completions)")
    p.add_argument("--model", default="", help="the model the template asked to serve")
    p.add_argument("--engine-args", default="")
    p.add_argument("--expect-caps", default="",
                   help="comma/space separated capabilities that MUST be present")
    p.add_argument("--timeout", type=float, default=120.0)
    return p


def main(argv: list[str] | None = None, out=sys.stdout) -> int:
    args = build_parser().parse_args(argv)
    if set_engine(args.engine) is None:
        print(f"unknown engine {args.engine!r}; known: {', '.join(ENGINES)}", file=out)
        return 2
    rep = Report(out)
    cli = Client(args.base_url, args.token, args.timeout)
    routes = {r.strip() for r in args.routes.split(",") if r.strip()}
    return run_contract(rep, cli, model=args.model, engine_args=args.engine_args,
                        expect_caps=args.expect_caps, routes=routes)


def _finish(rep: Report, out) -> int:
    """Summary line, completion sentinel, exit code — the only way main() returns.

    The sentinel is the last line and the caller REQUIRES it. Every exit through
    main() is an explicit one that has already reported its reason; anything else —
    an unhandled exception, a killed interpreter, an OOM — exits without it. Without
    the sentinel a traceback exits 1, prints no VIOLATION line, and the caller reads
    "1 = violations, none listed" as nothing to report: a crash that passes. Silence
    is the failure mode that matters, so it is asserted rather than assumed.
    """
    total = (len(rep.passed) + len(rep.violations) + len(rep.advisories)
             + len(rep.na) + len(rep.errors) + len(rep.deviated))
    print(f"    summary: {len(rep.passed)} ok, {len(rep.violations)} violation(s), "
          f"{len(rep.deviated)} declared deviation(s), {len(rep.advisories)} advisory, "
          f"{len(rep.na)} n/a, {len(rep.errors)} error(s)", file=out)
    print(f"CONTRACT-COMPLETE {total}", file=out)
    return rep.exit_code()


if __name__ == "__main__":
    sys.exit(main())
