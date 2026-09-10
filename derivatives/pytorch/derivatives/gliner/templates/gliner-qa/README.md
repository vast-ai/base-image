# GLiNER2 QA template — zero-shot extraction smoke

A disposable, private template for exercising a freshly-built `vastai/gliner` image
on real hardware. It is **not** a user-facing template.

## What it launches

One supervisor service, `gliner`, running `server.py` (FastAPI + uvicorn) against
`GLINER_MODEL`. The service waits for `/.provisioning` to clear, then downloads the
checkpoint on first start — so the first boot is slower than subsequent ones by the
size of the model, and the API is not reachable until that finishes.

## The bind is deliberate

`server.py` defaults to `0.0.0.0:8000` with **optional** auth — `GLINER_API_KEY`
unset means every request is accepted. Published as-is that is an unauthenticated
extraction endpoint on the open internet. This template pins `GLINER_HOST=127.0.0.1`
and `GLINER_PORT=18000` so the only public path is through Caddy on `8000`, gated by
`OPEN_BUTTON_TOKEN`. Set `GLINER_API_KEY` at launch if you intend to reach it another
way; it is intentionally not committed here.

`PORTAL_CONFIG` is load-bearing, not decoration: `gliner.sh` sources
`exit_portal.sh "GLiNER API"`, which greps `/etc/portal.yaml` and, on no match,
writes a skip marker and exits 0 — which supervisord treats as intentional, so the
service never starts and the portal shows it as "not configured" rather than failed.
Renaming that portal entry silently disables the app.

## Smoke test

```bash
curl -s -X POST localhost:18000/extract \
  -H 'Content-Type: application/json' \
  -d '{"text":"Tim Cook announced the iPhone 15 in Cupertino for $999.",
       "labels":["person","company","product","location","price"]}'
```

Every requested label comes back as a key; a label that matched nothing is an empty
list, not a missing key. `threshold` (default `0.3`) trades recall for precision.

## Coverage gap

`INSTANCE_TEST_REQUIRE_PASS` names only the GPU trio, because this image ships no
`gliner.d/` instance-test suite. The gate therefore asserts that the rented box has a
working GPU and nothing about GLiNER itself. L072 does not fire — it is scoped to
images that ship an own suite — but the gap is real, and writing that suite is the
outstanding work before this template is worth wiring into `qa-gate.yml`.

## Publishing

```bash
python tools/template_manager/create.py \
  derivatives/pytorch/derivatives/gliner/templates/ --api-key "$VAST_API_KEY"
```
