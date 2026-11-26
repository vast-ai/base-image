"""Completions: the template serves /v1/completions through its public endpoint."""

from __future__ import annotations

from . import OPENAI_ENGINE, ROUTE, STRUCTURAL, ResultName, TestType, _no_response

COMPLETIONS_CHECK = "external/completions"


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


def _run(ctx):
    return [check_completions(ctx.endpoint)]


TYPE = TestType(
    name="completions",
    axis=ROUTE,
    requires=("completions",),
    images=OPENAI_ENGINE,
    results=(ResultName(COMPLETIONS_CHECK, STRUCTURAL, OPENAI_ENGINE, True),),
    unrun=(COMPLETIONS_CHECK,),
    order=30,
    run=_run,
)
