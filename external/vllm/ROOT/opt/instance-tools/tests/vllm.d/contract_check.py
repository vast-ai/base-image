#!/usr/bin/env python3
"""Deterministic assertions about the OpenAI surface this image serves (ADR 0031).

WHY THIS IS PYTHON AND NOT MORE BASH. Structured/tested logic is Python in this
repo (CLAUDE.md); `10-vllm-serving.sh` already reaches for a heredoc `python3 -c`
five times to read one field out of a JSON body, and every one of those is a
`2>/dev/null` that turns a parse failure into an empty string. The assertions here
are about response SHAPE, so they belong where shape is cheap to state — and where
they can be unit-tested off-box (tools/imagegen/tests/test_vllm_contract_check.py)
instead of only on a rented GPU.

WHAT IT REFUSES TO ASSERT is as much the point as what it asserts. ADR 0031
decision 1 binds this file: every check is FORCED — token arithmetic, `max_tokens=1`,
a grammar, a named tool, a status code, a socket address. None of it samples the
model. Specifically refused, and these refusals are binding:

  - content matching ("2+2" -> "4"): that is model competence, and the model is
    chosen per template. `10-vllm-serving.sh` already learned this the expensive
    way — it asked three prompts and now passes if ANY ONE of them emits a single
    token, which is what an assertion decays to when it was never decidable.
  - non-empty `content`: reasoning models legitimately return none.
  - bitwise determinism across two greedy requests: vLLM is not batch-invariant.
  - latency or tokens/sec thresholds: host-dependent, on a market where ADR 0029
    exists precisely because hosts vary.

OUTPUT is two streams in one. Human lines go to stdout for the run log; the same
findings are repeated as machine lines the caller greps:

    VIOLATION <check> <detail>     required — fails the test when enforcing
    ADVISORY  <check> <detail>     discovered but undeclared — reported, never red
    ERROR     <check> <detail>     the check could not DECIDE; never advisory
    NA        <check> <reason>     not applicable here, with the reason, always logged

Exit: 0 clean, 1 violations, 2 could-not-decide. A capability that is declared in
the caps env var but not discovered is a VIOLATION (claimed and absent); one that is
discovered but not declared runs ADVISORY. Discovery can never manufacture a red —
ADR 0031 decision 6.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request

# ── The ONLY engine-specific block in this file ──────────────────────────────
#
# vLLM, SGLang and llama.cpp all serve the same OpenAI surface, so every assertion
# below is engine-agnostic and the copies of this file differ ONLY here. That is
# deliberate: the decision was per-image copies rather than a shared library in base
# (a base-resident library reaches derivatives only when their pin moves, measured at
# 67 days), and the cost of a copy is drift. Confining the difference to one dict
# makes drift a one-screen diff instead of a hunt.
#
# DRIFT NOTE: nothing detects divergence between the copies. If you change anything
# outside this block, change it in every copy.
ENGINE = {
    "name": "vllm",
    "model_env": "VLLM_MODEL",
    "args_env": "VLLM_ARGS",
    "caps_env": "VLLM_EXPECT_CAPS",
    "default_port": 18000,
    # The flag that declares the context window, used to force a 4xx overflow.
    "context_flag": "--max-model-len",
    # Presence of this flag is how "the deployment offers tool calling" is DISCOVERED.
    "tools_flag": "--enable-auto-tool-choice",
    # Checks this engine is KNOWN to fail, name -> why. A declared deviation is
    # REPORTED and does not block.
    #
    # The asymmetry that stops this becoming the exemption cycle ADR 0031 was written
    # against: a deviation that STOPS reproducing is a VIOLATION. If the engine starts
    # behaving, the gate says so and the declaration gets deleted — an exemption here
    # expires by itself rather than accumulating. That is decision 6's
    # declared-but-not-discovered rule pointed the other way.
    #
    # A deviation is only admissible when the defect is UPSTREAM (we cannot fix it)
    # and BOUNDED by another assertion in this file (so the hazard is still covered).
    # Both halves must be stated in the reason.
    "deviations": {},
}

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

class Client:
    def __init__(self, base: str, timeout: float = 120.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout

    def post(self, path: str, payload, *, raw: bytes | None = None) -> tuple[int, str]:
        """POST and return (status, body). A transport failure is status 0.

        Never raises: every caller here is deciding a check, and an exception that
        escapes would abandon the checks AFTER it. A 0 tells the caller "could not
        decide", which is a different verdict from "the server said no".
        """
        body = raw if raw is not None else json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.base}{path}", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:                                    # noqa: BLE001
            return 0, f"{type(e).__name__}: {e}"

    def get(self, path: str) -> tuple[int, str]:
        try:
            with urllib.request.urlopen(f"{self.base}{path}", timeout=self.timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:                                    # noqa: BLE001
            return 0, f"{type(e).__name__}: {e}"

    def post_stream(self, path: str, payload) -> tuple[int, list[str]]:
        req = urllib.request.Request(
            f"{self.base}{path}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, [ln.decode("utf-8", "replace").rstrip("\r\n")
                                  for ln in r]
        except urllib.error.HTTPError as e:
            return e.code, [e.read().decode("utf-8", "replace")]
        except Exception as e:                                    # noqa: BLE001
            return 0, [f"{type(e).__name__}: {e}"]


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


def check_prompt_roundtrip(rep: Report, args, tokens: list[str], usage: dict) -> None:
    """Apply the chat template locally and assert the server counted the same.

    Exact, deterministic, needs no golden data, and works on any model — and it is
    the only check in this file that can see a SILENTLY REPLACED chat template or a
    BOS double-add, which is the one defect class in this family with real
    precedent. A local count of prompt_tokens+1 is the double-add signature.

    Everything that could make the two legitimately differ is discovered and turned
    into an n/a rather than a violation: `--chat-template` overrides the template
    the server uses, so the local one is a different function.
    """
    if not usage.get("prompt_tokens"):
        rep.not_applicable("prompt-roundtrip", "no prompt_tokens from the probe request")
        return
    if arg_present(tokens, "--chat-template") or arg_present(tokens, "--chat-template-content-format"):
        rep.not_applicable("prompt-roundtrip",
                           "--chat-template overrides the template the server applies, so "
                           "the local tokenizer's template is a different function")
        return
    try:
        from transformers import AutoTokenizer          # type: ignore
    except Exception as e:                                        # noqa: BLE001
        # Not a violation: the checker runs under whatever interpreter the caller
        # found. Loud n/a — ADR 0031 decision 6 accepts losing advisory coverage,
        # never silently.
        rep.not_applicable("prompt-roundtrip", f"transformers not importable here ({e})")
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
    try:
        tok = AutoTokenizer.from_pretrained(ref, **kwargs)
    except Exception as e:                                        # noqa: BLE001
        rep.not_applicable("prompt-roundtrip", f"could not load the tokenizer ({type(e).__name__}: {e})")
        return
    # No template means there is nothing to compare — the server is not applying one
    # either, so a count mismatch would say nothing about the image.
    if not getattr(tok, "chat_template", None):
        rep.not_applicable("prompt-roundtrip", "tokenizer declares no chat template")
        return
    try:
        local = tok.apply_chat_template(PROBE, add_generation_prompt=True, tokenize=True)
        n_local = _token_count(local)
    except Exception as e:                                        # noqa: BLE001
        rep.not_applicable("prompt-roundtrip", f"could not tokenize locally ({type(e).__name__}: {e})")
        return
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


def check_unknown_model(rep: Report, cli: Client) -> None:
    """A model that does not exist must be refused, not quietly substituted.

    Status class only, deliberately. ADR 0031's reversal clause says the answer to an
    upstream status-code change is to cut an assertion to status-code-only rather
    than widen the redraw rule, so this starts there: 4xx passes, and the exact code
    is reported for the log. The defect shapes are a 200 (something else answered)
    and a 5xx (the server fell over on a request it should have rejected).
    """
    status, body = cli.post("/v1/chat/completions", {
        "model": NO_SUCH_MODEL, "messages": PROBE, "temperature": 0, "max_tokens": 1,
    })
    if status == 0:
        rep.error("error-unknown-model", f"request could not be made: {body[:200]}")
    elif 400 <= status < 500:
        rep.ok("error-unknown-model", f"unknown model refused with HTTP {status}")
    elif status == 200:
        rep.violation("error-unknown-model",
                      "a request for a nonexistent model was ANSWERED (HTTP 200) — "
                      "the server is serving something other than what was asked for")
    else:
        rep.violation("error-unknown-model",
                      f"unknown model gave HTTP {status}, expected a 4xx refusal")


def check_malformed_body(rep: Report, cli: Client) -> None:
    status, body = cli.post("/v1/chat/completions", None, raw=b"{")
    if status == 0:
        rep.error("error-malformed-body", f"request could not be made: {body[:200]}")
    elif 400 <= status < 500:
        rep.ok("error-malformed-body", f"malformed JSON refused with HTTP {status}")
    else:
        rep.violation("error-malformed-body",
                      f"a truncated JSON body gave HTTP {status}, expected a 4xx refusal")


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


def check_bind(rep: Report, rep_port: int, tokens: list[str]) -> None:
    """The engine binds loopback; Caddy is what faces the network.

    A socket address, which ADR 0031 decision 1 admits as forced. This is not
    base/28's job: that scans for PUBLIC exposure against an allowlist, which is a
    different question from whether THIS engine's own listener is where the image
    says it is. `ss` output is the authority — the flag is only a hint, since an
    unset variable interpolated into `--host` yields an argument the server replaces
    with its own default (the shape L068 exists for).
    """
    host_flag = first(arg_values(tokens, "--host"), "<unset>")
    try:
        out = subprocess.run(["ss", "-tlnH"], capture_output=True, text=True, timeout=15).stdout
    except Exception as e:                                        # noqa: BLE001
        rep.error("bind-loopback", f"could not read listeners: {type(e).__name__}: {e}")
        return
    bound = [ln.split()[3] for ln in out.splitlines() if len(ln.split()) > 3
             and ln.split()[3].endswith(f":{rep_port}")]
    if not bound:
        rep.error("bind-loopback", f"nothing is listening on :{rep_port} to attribute")
        return
    wide = [a for a in bound if a.startswith(("0.0.0.0:", "*:", "[::]:"))]
    if wide:
        rep.violation("bind-loopback",
                      f"vLLM is bound to {wide} (--host {host_flag}) — the engine must bind "
                      "loopback and be reached through Caddy, which is what authenticates")
    else:
        rep.ok("bind-loopback", f"listening on {bound} (--host {host_flag})")


# ─────────────────────────────────────────────────────────── capability tiers

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
    if status in (400, 404, 422):
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


def cap_embeddings(cli: Client, model: str, _tokens: list[str]):
    """Shape only: N inputs -> N vectors of one non-zero width."""
    status, body = cli.post("/v1/embeddings", {"model": model, "input": ["alpha", "beta"]})
    doc = as_json(body)
    if status in (400, 404, 422):
        return None, f"this model does not serve embeddings (HTTP {status})"
    if status != 200 or doc is None:
        return False, f"/v1/embeddings returned HTTP {status}: {body[:200]}"
    data = doc.get("data") or []
    if len(data) != 2:
        return False, f"2 inputs returned {len(data)} embeddings"
    widths = {len(d.get("embedding") or []) for d in data}
    if len(widths) != 1 or 0 in widths:
        return False, f"embedding widths are {sorted(widths)} — expected one non-zero width"
    return True, f"2 inputs -> 2 vectors of width {widths.pop()}"


CAPABILITIES = {
    "tools": cap_tools,
    "structured-output": cap_structured,
    "embeddings": cap_embeddings,
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


# ─────────────────────────────────────────────────────────────── entry point

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="vLLM API contract checks (ADR 0031)")
    p.add_argument("--base-url", default=f"http://127.0.0.1:{ENGINE['default_port']}")
    p.add_argument("--port", type=int, default=ENGINE["default_port"],
                   help="the port to attribute listeners to (bind check)")
    p.add_argument("--model", default=os.environ.get(ENGINE["model_env"], ""),
                   help="the model the template asked to serve")
    p.add_argument("--engine-args", default=os.environ.get(ENGINE["args_env"], ""))
    p.add_argument("--expect-caps", default=os.environ.get(ENGINE["caps_env"], ""),
                   help="comma/space separated capabilities that MUST be present")
    p.add_argument("--timeout", type=float, default=120.0)
    return p


def main(argv: list[str] | None = None, out=sys.stdout) -> int:
    args = build_parser().parse_args(argv)
    rep = Report(out)
    try:
        tokens = shlex.split(args.engine_args)
    except ValueError as e:
        rep.error("engine-args", f"{ENGINE['args_env']} is not parseable as a shell word list: {e}")
        return _finish(rep, out)

    cli = Client(args.base_url, args.timeout)
    status, body = cli.get("/v1/models")
    models_doc = as_json(body)
    if status != 200 or models_doc is None:
        rep.error("identity", f"/v1/models returned HTTP {status}: {body[:200]}")
        return _finish(rep, out)

    # `--served-model-name` is what the id becomes when it is given; vLLM uses the
    # FIRST as the id and the rest as aliases, so the first is what must come back.
    expected = first(arg_values(tokens, "--served-model-name"), args.model)
    if not expected:
        rep.error("identity", f"neither {ENGINE['model_env']} nor --served-model-name names a model")
        return _finish(rep, out)

    check_identity(rep, models_doc, expected)

    ids = [m.get("id", "") for m in models_doc.get("data", [])]
    # Address the served id, not the requested one: if identity already failed, the
    # remaining checks should still exercise whatever IS being served rather than
    # 404 on every request and report ten failures for one defect.
    model = expected if expected in ids else (ids[0] if ids else expected)

    usage = check_token_arithmetic(rep, cli, model)
    check_prompt_roundtrip(rep, args, tokens, usage)
    check_completions_route(rep, cli, model)
    check_unknown_model(rep, cli)
    check_malformed_body(rep, cli)
    check_context_overflow(rep, cli, model, models_doc, tokens)
    check_streaming(rep, cli, model)
    check_bind(rep, args.port, tokens)

    declared = {c for c in args.expect_caps.replace(",", " ").split() if c}
    run_capabilities(rep, cli, model, tokens, declared)

    return _finish(rep, out)


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
