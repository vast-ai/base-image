## GLiNER2 (this image)

The PyTorch image plus a preinstalled **GLiNER2** (upstream `fastino-ai/GLiNER2`) for
zero-shot entity extraction. Everything in base.md and pytorch.md applies unchanged
(torch is in `/venv/main`); this file covers what GLiNER adds. **One** service — get its
externally callable URL + token from the manifest (base.md §5, §9):
```
curl -s http://localhost:11111/capabilities/services   # direct_url + state
```

### gliner — programmatic entity extraction

A **FastAPI** server (`python server.py`), supervisor service **`gliner`**, internal
`0.0.0.0:8000`. This is the only service in the image.

**It is NOT OpenAI-compatible — do not call `/v1/chat/completions` or any `/v1/...`
route.** The extraction endpoint is **`POST /extract`**. The full schema is the source
of truth at **`/docs`** (the OpenAPI page on the same port).

**The label contract is the thing to get right.** GLiNER is *zero-shot*: the entity types
are not baked into the model and there is no default set. You pass them per request in
**`labels`**, a required list of strings, and you get back only those types. Inventing a
new type needs no retraining — just a different `labels` value.

```
curl -X POST http://<host>:8000/extract \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${GLINER_API_KEY}" \
  -d '{"text": "Apple CEO Tim Cook announced iPhone 15 in Cupertino for $999.",
       "labels": ["person", "company", "product", "location", "price"],
       "threshold": 0.3}'
```

Response is `{"entities": {<label>: [<string>, ...]}, "inference_time": <float>,
"device": "<device>"}`. **Every label you request comes back as a key**, and a label that
matched nothing is an **empty list** rather than a missing key — so test
`if not entities["price"]`, not `if "price" in entities`. An unknown or nonsensical label
is not an error either; it simply returns `[]`. `threshold` (default `0.3`) trades recall
for precision; lower it if short or unusual spans are being missed.

### Auth

`Authorization: Bearer $GLINER_API_KEY`, enforced on `/extract` only. **If
`GLINER_API_KEY` is unset the API is completely unauthenticated** — the server prints a
warning at startup and accepts every request. `/health` and `/` are never authenticated.

### Models & provisioning

Runs from `${WORKSPACE}/gliner` in `/venv/main`. The checkpoint is selected by
**`GLINER_MODEL`** (default `fastino/gliner2.5-base-v1`); `AutoExtractor` dispatches on
the checkpoint's architecture, so 2.5 checkpoints load as `BoundaryExtractor` and 2.0
checkpoints as `SpanExtractor` — both work, and pinning `GLINER_MODEL` to a 2.0
checkpoint is supported. A 2.0 checkpoint declares an external encoder and pulls a
**second** repository at load time, so it starts noticeably slower than a 2.5 one.

**Weights download on first startup**, so the first boot is slow — that is expected, not
a hang. Set `HF_TOKEN` if the Hub rate-limits anonymous downloads. The service **waits
for provisioning (`/.provisioning`) to finish before starting**, so during boot it may be
intentionally down — check that flag before assuming a fault.

### Two things that look like faults and are not

- At startup the DeBERTa-v3 encoder logs a `RuntimeWarning` that it rejected
  `attn_implementation='sdpa'` and fell back to `'eager'`. transformers has no SDPA path
  for this architecture. Expected; not an error.
- If the model **failed** to load, `GET /health` does not report unhealthy — it raises a
  500 from a response-validation error, because `device` is still unset. A 500 from
  `/health` therefore means "model never loaded", not "health check is broken". The
  startup log has the real cause.
