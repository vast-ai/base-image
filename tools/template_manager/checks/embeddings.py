"""Embeddings: the template serves /v1/embeddings through its public endpoint."""

from __future__ import annotations

from . import (ANSWER, OPENAI_ENGINE, ROUTE, STRUCTURAL, ResultName, TestType,
               _no_response_pair)

EMBEDDINGS_CHECK = "external/embeddings"
EMBEDDINGS_ANSWER_CHECK = "external/embeddings-ranking"

EMBED_TEXTS = (
    "The cat sat on the warm windowsill in the sun.",
    "A kitten was lying on a sunny window ledge.",
    "Quarterly revenue grew because of strong GPU rental demand.",
)


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


def _run(ctx):
    return check_embeddings(ctx.endpoint)


TYPE = TestType(
    name="embeddings",
    axis=ROUTE,
    requires=("embeddings",),
    images=OPENAI_ENGINE,
    results=(ResultName(EMBEDDINGS_CHECK, STRUCTURAL, OPENAI_ENGINE, True),
             ResultName(EMBEDDINGS_ANSWER_CHECK, ANSWER, OPENAI_ENGINE, True)),
    unrun=(EMBEDDINGS_CHECK, EMBEDDINGS_ANSWER_CHECK),
    order=40,
    run=_run,
)
