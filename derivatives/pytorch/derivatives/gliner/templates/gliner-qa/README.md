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

## What the gate asserts

`INSTANCE_TEST_REQUIRE_PASS` names the GPU trio plus this image's own suite,
`gliner.d/10-gliner-serving`. The trio alone would certify only that the rented box
has a working GPU and nothing about GLiNER itself — the L072 gap. The GLiNER suite
closes it by asserting the things a green `docker build` cannot see:

- the `gliner` supervisor service is running and port 18000 is listening
- `/health` reports `"gpu_available":true` — the server answers correctly on CPU, so
  a silent CPU fallback otherwise looks identical to success (ADR 0016)
- `/extract` returns real entities rather than a 200 with an empty map, which is what
  a model that never downloaded produces
- a wrong bearer token gets a 401, so a regression that drops auth cannot promote green
- the listener is on loopback, not public, so the API stays behind Caddy

Naming them in `INSTANCE_TEST_REQUIRE_PASS` is load-bearing: the trio self-skips when
nvidia-smi or libcuda never came up, and a skip reads as green (ADR 0019).

## Publishing

```bash
python tools/template_manager/create.py \
  derivatives/pytorch/derivatives/gliner/templates/ --api-key "$VAST_API_KEY"
```
