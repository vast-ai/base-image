# ADR 0017 — Portal "not ready" interstitial: 200 for Cloudflare-tunnelled requests only

- **Status:** Accepted
- **Date:** 2026-07-24
- **Decision owner:** Rob Ballantyne

## Context

When Caddy is listening on an app's external port but the backing service has not
started yet, the `reverse_proxy` upstream connection fails and Caddy synthesises a
**502**. A per-site `handle_errors` block rewrites the request to a static
`502.html` loading page (spinner + "Check instance logs for progress") that polls
the URL and reloads once the backend answers. This is the standard "app is still
booting" experience for Caddy-fronted ports across the image family.

A CDN tunnel placed in front of the instance breaks it. The default tunnels the
image creates are **Cloudflare quick tunnels** (`trycloudflare.com`, one per
external port, launched by `tunnel_manager`). Cloudflare's edge replaces an origin
**5xx response body** with its own branded error page — for a tunnel origin that
502 renders as Cloudflare's "Host is down" / "502 Bad gateway" page. So the user
never sees our informative loader.

It is worse than cosmetic. `502.html`'s self-poll issued `fetch(HEAD)` **through
the same tunnel** and reloaded when the status was no longer 502. Once Cloudflare
is intercepting, that poll also receives Cloudflare's page (status ≠ 502), so it
reloads into the same Cloudflare page forever — the user is pinned on "Host is
down" and the auto-recovery never fires.

Constraints:

- **We only control the origin.** For a throwaway `trycloudflare.com` hostname the
  user has no Cloudflare dashboard, so zone-side settings (Custom Errors, "Origin
  Error Page Pass-thru") are not available. The fix must live in our Caddy config,
  which every image inherits via `portal-aio` — see the Caddy edge invariants in
  [docs/invariants.md](../invariants.md).
- **Cloudflare hijacks by status class, not body.** It substitutes on origin
  **5xx**; a **200** is passed through untouched, headers and all.
- **The 502 down-state is only a problem over a Cloudflare tunnel.** Direct port
  access and Vast's own proxy already pass the 502 loader through fine. So the
  down-state 5xx is a *useful* machine-readable "not ready" signal everywhere
  except the Cloudflare path, and should be preserved there.

## Options considered

- **A — Always serve the interstitial as HTTP 200 + marker.** One code path,
  robust everywhere. **Rejected:** it flips the down-state to `200` on *every*
  path, including direct access and Vast's own proxy. Any prober that reads the
  app-port status as "ready" (the Vast control plane, an Open-Button flow, a user's
  uptime monitor) would then see a booting app as up and a real outage as healthy.
  That readiness-signal change is fleet-wide and could not be cleared as safe.

- **B — Serve 200 only for Cloudflare-tunnelled requests; keep 502 otherwise
  (chosen).** Requests that arrive over a Cloudflare tunnel carry a `Cf-Ray`
  header. Match on it and serve the loader as 200 for those; every other path keeps
  the real 5xx. This confines the status change to exactly the traffic that needs
  it and leaves the "not ready" signal intact for every direct/proxy/monitoring
  consumer. Cost: a request-matched branch and a poll that copes with both a 200
  and a 5xx down-state. Accepted as worth it, since it removes the readiness bet
  entirely.

- **C — Tell users to configure Cloudflare (Custom Errors / pass-thru).** Rejected
  as a general fix: quick-tunnel users have no dashboard for a `trycloudflare.com`
  hostname.

- **D — Keep the 5xx, rely on a richer body / different 5xx code.** Rejected: a
  live repro confirmed even a full 6.5 KB origin 502 body is discarded and swapped
  for Cloudflare's page.

## Decision

Adopt **Option B**. The not-ready placeholder is served with a status chosen by
whether the request came over a Cloudflare tunnel:

- `@cf_tunnel header Cf-Ray *` matched at **site scope** sets a request var
  `not_ready_status` to `200`; the default is `502`.
- `handle_errors 502 503 504` serves `502.html` via `file_server { status
  {vars.not_ready_status} }`, always adding `X-Portal-Placeholder: true` and
  `Cache-Control: no-store`.
- The proxy blocks strip any upstream `X-Portal-Placeholder` (`header_down
  -X-Portal-Placeholder`) so only Caddy can set it.
- `502.html`'s poll reloads only when the marker is **absent AND** the status is
  `< 500` — so a marker-less down-state (a direct 5xx, or a stale page during an
  update) keeps waiting instead of reload-storming.

The block is emitted by `get_not_ready_handler_block()` in
`portal-aio/caddy_manager/caddy_config_manager.py`.

Two mechanics forced this shape and were verified on the real Caddy build
(`v2.11.4`, ai-dock fork) and upstream `v2.8.4`:

- **Request matchers do not discriminate inside `handle_errors`** on these builds
  (`handle @m { … }` runs unconditionally, and `expression`/`header` matchers all
  match). The request header *is* readable there via a placeholder, so the CF
  decision is made at site scope (where matchers work) and read back inside
  `handle_errors` as the `status` placeholder.
- **`handle_errors` catches only Caddy-generated errors**, not a proxied backend's
  own 5xx response — verified with a live upstream returning 503, which passed
  through untouched. So widening the matcher to `502 503 504` (to also cover
  "bound but not answering yet", e.g. during model load) does not swallow an app's
  legitimate 5xx.

## Binding conditions

- The generated block MUST make the served status a request var whose **direct
  default is a 5xx** and whose **Cloudflare-tunnel override is a non-5xx**, and MUST
  carry the `X-Portal-Placeholder` marker; `502.html` MUST gate on the marker's
  absence plus `status < 500`, not on the 502 status code.
- Enforced by `portal-aio/tests/test_caddy_config_manager.py` +
  `.github/workflows/portal-aio-tests.yml`:
  - a contract predicate over the generated block (rejects the legacy bare-502
    form, the always-200 form, and a 5xx tunnel override);
  - a **round-trip** assertion that the marker name the generator emits is the exact
    name the poll reads, and a **regex that pins the poll's reload condition** so a
    boolean inversion, a removed status guard, or a deleted `reload()` turn CI red;
  - a **`caddy validate`** step that loads a generated config (auth + noauth)
    through a real Caddy binary, so a syntactically illegal block cannot ship green.
  - Honesty note: the poll's runtime behaviour is *pinned structurally* (the exact
    condition string is asserted), not executed in a DOM. A headless/jsdom test that
    actually drives the poll would be strictly better and is a reasonable follow-up;
    it was not added to avoid introducing a Node toolchain into a Python/Bash CI.
  This is the portal-aio analogue of the imagegen linter — the correct subsystem for
  a Caddy invariant, since the imagegen linter only lints image definitions.
- Ships to the fleet via the portal release mechanism (ADR 0015): `portal-aio/VERSION`
  bumped to `v3.1.3`.

## Scope boundary — direct-bind apps are not covered

The fix lives entirely in the Caddy site block, which the generator emits **only
for proxied apps** (`external_port != internal_port`). An app that binds its own
external port directly has **no Caddy in front of it**: when it is still booting,
`cloudflared` gets connection-refused at the origin and Cloudflare serves its own
"Host is down" page (error 1033). There is no origin 5xx to convert, so this ADR
does nothing for that case.

The one that matters in practice is **launch-mode Jupyter**. In supervisor mode our
`ROOT/opt/supervisor-scripts/jupyter.sh` binds Jupyter on `127.0.0.1:18080` and
Caddy fronts `8080 -> 18080`, so a booting Jupyter *is* covered. But when `/.launch`
is present, that script deliberately refuses to start ("`/.launch` managing") and
the Vast-controller-generated `/.launch` runs Jupyter itself on `0.0.0.0:8080` with
its own TLS; `ROOT/etc/vast_boot.d/10-prep-env.sh` then rewrites the config to
`8080:8080` so Caddy stays off the port Vast owns. That path is not the base
image's to change — port 8080 is bound by a server-side launch script, not the
image — and the exposure is narrow: Jupyter has no model to load so the
connection-refused window is seconds, and the Cloudflare quick tunnel is a
*secondary* access path (launch mode's primary is Vast's own SSH reverse-proxy,
behind the "Open" button, with Vast's loading UX). Closing it would mean putting a
listener in front of a port the platform binds — a Vast launch/controller change,
not a base-image one. Documented here as a boundary, not fixed.

## Consequences

- The informative loader survives Cloudflare quick tunnels; the marker header
  passes through the tunnel on GET and HEAD, so the auto-reload poll works.
- **Direct access, Vast's own proxy, and any uptime/monitoring probe are
  unchanged** — a not-ready proxied port still answers 5xx for them, because they
  do not send `Cf-Ray`. The readiness-signal risk that sank Option A does not
  arise. In-repo consumers that key on the 502 (e.g.
  `ROOT/opt/instance-tools/tests/base/26-caddy-auth.sh`, which curls `127.0.0.1`
  directly) therefore keep working with no change.
- `handle_errors` now also covers 503/504, so a port that is bound-but-not-serving
  shows the loader over a tunnel instead of a hijacked gateway error.
- `Cache-Control: no-store` prevents a stale loader being served over the real app
  once it comes up.

## What would reverse this

- Cloudflare ceasing to replace origin 5xx bodies for tunnel origins (or exposing a
  quick-tunnel-side pass-through) — we could then restore true 503/`Retry-After`
  semantics on the tunnel path too.
- Cloudflare beginning to strip unknown response headers or interfere with a 200
  over a tunnel — the marker would stop arriving and the poll's `status < 500`
  fallback would carry it (reload, not loop), but the mechanism would need a rethink.

## Caveats / residual risk

- **Evidence is quick-tunnel-only (n=1 zone).** The "Cloudflare passes a 200 +
  headers through" premise was reproduced on a `trycloudflare.com` quick tunnel in
  Cloudflare's default-config zone, and matches Cloudflare's documented default
  origin-error-page behaviour. **Named tunnels** (`CF_TUNNEL_TOKEN`, the user's own
  zone) are a supported code path and were not re-tested: a user who enables zone
  security (Under Attack mode, Bot Fight, a WAF managed rule) can have a JS
  challenge injected into a 200 `text/html` response, which carries no marker →
  the poll would reload into the challenge. That is user-zone behaviour, not
  something the origin controls; the fix is still strictly better than the status
  quo there.

## Evidence

Reproduced on a live instance (portal `v3.1.2`, quick tunnels): a dedicated
`cloudflared` quick tunnel fronting an origin that returned our exact `502.html`.

- Origin **502** + full body → client received Cloudflare's "trycloudflare.com |
  502: Bad gateway" page; our loader text absent.
- Origin **200** + `X-Portal-Placeholder` → client received our loader unchanged;
  the marker and `Cache-Control: no-store` survived the tunnel on GET and HEAD.

The **generated** Option-B config was then validated and exercised on the real
Caddy build (`v2.11.4`) and upstream (`v2.8.4`): against a dead upstream, a request
carrying `Cf-Ray` received `200` + marker + loader, and a direct request received
`502` + marker + loader; a live upstream's own `503` passed through untouched.
