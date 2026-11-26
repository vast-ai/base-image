"""Chat: the template serves /v1/chat/completions through its public endpoint."""

from __future__ import annotations

from . import OPENAI_ENGINE, ROUTE, STRUCTURAL, ResultName, TestType, _no_response

CHAT_CHECK = "external/chat-inference"

CHAT_PROMPTS = (
    "Say hello in one sentence.",
    "What is 2+2? Reply with just the number.",
)


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


def _run(ctx):
    return [check_chat(ctx.endpoint)]


TYPE = TestType(
    name="chat",
    axis=ROUTE,
    requires=("chat",),
    images=OPENAI_ENGINE,
    results=(ResultName(CHAT_CHECK, STRUCTURAL, OPENAI_ENGINE, True),),
    unrun=(CHAT_CHECK,),
    order=20,
    run=_run,
)
