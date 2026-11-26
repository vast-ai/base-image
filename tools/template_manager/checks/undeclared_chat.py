"""The undeclared-chat guard: a template that does not declare chat must refuse it."""

from __future__ import annotations

from . import GUARD, OPENAI_ENGINE, STRUCTURAL, ResultName, TestType, _no_response, contract

UNDECLARED_CHAT_CHECK = "external/undeclared/chat"
# The statuses that mean "this route is not served here" — the contract's NOT_OFFERED.
CHAT_REFUSED = contract.NOT_OFFERED

# Probing a route the template does not declare can take the server down (seen live:
# one /v1/completions call killed a vLLM-Omni TTS server), and that must never cost the
# declared checks their result. So the guard runs after every other type, ComfyUI's
# included.
LAST = 1000


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


def _run(ctx):
    return [check_chat_refused(ctx.endpoint)]


TYPE = TestType(
    name="undeclared-chat",
    axis=GUARD,
    requires=("chat",),
    images=OPENAI_ENGINE,
    results=(ResultName(UNDECLARED_CHAT_CHECK, STRUCTURAL, OPENAI_ENGINE, True),),
    unrun=(UNDECLARED_CHAT_CHECK,),
    order=LAST,
    run=_run,
)
