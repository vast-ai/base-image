# ADR 0042 — Wan2GP adds its port to Caddy's localhost rewrite at boot

- **Status:** Accepted (amended 2026-09-17: extended to aio-studio). Mechanism superseded by
  ADR 0043: the per-app `05-wan2gp-env.sh` is now the generic `05-caddy-localhost-ports.sh`.
  The diagnosis, rejected alternatives and consequences here still apply.
- **Date:** 2026-09-17
- **Decision owner:** Rob Ballantyne
- **Related:** ADR 0017 (portal behaviour behind a CDN tunnel)
- **Enforced by:** `tools/imagegen/tests/test_caddy_localhost_ports_sh.py` (since ADR 0043)

## Context

Wan2GP's upstream v13 (2026-09-13) added a same-origin check to the middleware in front
of both its Gradio app and its Deepy assistant:

```python
def same_origin(connection):
    origin = connection.headers.get("origin")
    scheme = {"ws": "http", "wss": "https"}.get(connection.url.scheme, connection.url.scheme)
    return origin is None or origin == f"{scheme}://{connection.url.netloc}"
```

Every WebSocket, and every request that is not GET, HEAD or OPTIONS, must pass it. Behind
our Caddy the app sees `Host: localhost:7860`, because the proxy forwards
`{upstream_hostport}`. The browser sends its real address as the Origin, so the check
can never pass.

What users saw was a persistent "Connection to server lost. Reconnecting…" banner.
Deepy's WebSocket is closed at the handshake, and its client retries every 1.5 s. The
same check also rejected Gradio's `POST /gradio_api/queue/join` with 403 "Cross-origin
request rejected." That POST is how a generation is submitted. Images built before
v13 were not affected. The image builds Wan2GP's `main` at build time, so the first
build after the upstream change was broken.

Measured on a live instance, through Caddy, with the portal cookie:

| Request | Origin sent to the app | Result |
|---|---|---|
| Deepy WebSocket | the browser's public origin | 403 |
| Deepy WebSocket | `http://localhost:7860` | 101 |
| `POST /gradio_api/queue/join` | the browser's public origin | 403 |
| Heartbeat SSE (GET) | any | streams normally |

The portal already has a way to fix this: `CADDY_HEADER_UP_LOCALHOST`. It holds `true`
or a comma-separated list of ports. For each listed port, Caddy sends
`Host: localhost:<port>` and `Origin: {forwarded_protocol}://localhost:<port>`.

`forwarded_protocol` is `https` behind Cloudflare. That still passes, because uvicorn
trusts `X-Forwarded-Proto` from loopback. Checked from both `127.0.0.1` and `[::1]`, with
both schemes. The variable fixed the live instance end to end. No image set it before.

Wan2GP has no setting for running behind a trusted proxy. The only related flags are
`--auth`, `--https-port` and the MCP OAuth ones.

## Options considered

1. **A per-image boot stage that ensures our port is listed** — chosen. Boot stages run
   in order, and `05-wan2gp-env.sh` runs before `10-prep-env.sh`, which snapshots the
   environment into `/etc/environment` on first boot. `caddy.sh` sources that file on
   every start. Existing images already use this `05-<name>-env.sh` pattern to set
   defaults (vllm, sglang, llama-cpp and others). The stage reads `WAN2GP_PORT`, so it
   follows a port override. It keeps whatever the template listed, needs no portal
   change, and is one file to delete once upstream is fixed.
2. **`ENV CADDY_HEADER_UP_LOCALHOST=7860` in the Dockerfile** — rejected. It hardcodes a
   port the image lets the template change (`WAN2GP_PORT`). A template that sets the
   variable for another app replaces the value outright, and the fix silently
   disappears.
3. **Set it in the template only** — rejected as the sole mechanism. Our launch
   templates are managed outside this repo. A template a user builds from the image
   would silently break, and the image cannot notice. A template may still set it
   explicitly; the stage then leaves it unchanged.
4. **A generic portal mechanism** — deferred, not rejected. `caddy_config_manager.py`
   would merge an image-declared requirement, keyed by application name so it follows
   the port in `PORTAL_CONFIG`, with the template value. That is the cleaner long-term
   shape: one mechanism instead of per-image bash. It changes the shared portal, which
   every image runs and CI releases (ADR 0015). One image does not justify that. It
   becomes worth building when a second image needs the rewrite. aio-studio will, once
   its Wan2GP pin moves past v13, and it runs several apps.
5. **Pin Wan2GP to the last pre-v13 commit** — rejected as the fix. It restores service
   but freezes the image on an old upstream. It remains the fallback if the rewrite ever
   stops being enough.
6. **Patch `same_origin()` at build time to honour `X-Forwarded-Host`** — rejected. A
   source patch against an upstream that the image tracks at HEAD breaks without warning
   on the next refactor. It belongs upstream, where it would also be the right fix.
7. **Stop forwarding `{upstream_hostport}` as Host for every app** — rejected. It is a
   portal-wide change for one app, and on its own it is insufficient: the scheme still
   differs whenever the browser is on https and the app is not.

## Decision

`derivatives/pytorch/derivatives/wan2gp/ROOT/etc/vast_boot.d/05-wan2gp-env.sh` ensures
`WAN2GP_PORT` (default 7860) is in `CADDY_HEADER_UP_LOCALHOST`:

- unset or empty → set to the port;
- `true`, in any case → left alone, since it already covers every port;
- a list without the port → the port is appended, and existing entries are kept;
- a list that already contains the port → left alone.

A list is read the way `caddy_config_manager.py` reads it: split on commas, entries
stripped, and an exact match only, so `17860` does not count as `7860`. There is
deliberately no opt-out value. `false` is an ordinary list to Caddy, so the port is
appended to it too. Without the rewrite, the app cannot accept a browser request
through the proxy at all.

## Binding conditions

1. **`true` is never appended to.** Caddy treats `true` as "all ports" only when it is
   the whole value. `true,7860` is a list and would drop the rewrite from every other
   app on the instance.
2. **The stage's parsing matches the generator's.** The test asserts the generator still
   splits on commas, compares `true` case-insensitively and matches on list membership.
   If the generator changes, this stage changes with it.
3. **The stage leaves nothing behind in the boot shell.** Boot stages are sourced, so
   working variables are unset. The test asserts this.

## Consequences

- Deepy's WebSocket and Gradio's POSTs work through the portal again, with the image
  building upstream HEAD as before.
- Wan2GP's own cross-site check is overridden, automatically and for every user. The
  rewrite is unconditional, so a genuine cross-origin request is also presented to the
  app as same-origin. Behind Caddy the check could never pass, so the practical choice
  was between a broken app and an app without that check. What remains:
  - Requests from other sites are still stopped by the portal. Its auth cookie is
    `SameSite=lax`, and bearer and token auth are not ambient.
  - Pages on the same host address are not stopped. Cookies are not scoped by port, and
    `SameSite` ignores the port, so a page served on any other port of the same address
    is same-site and carries the portal cookie. That includes another app on the
    instance (for example an HTML file opened through Jupyter). On direct `IP:port`
    access it also includes other instances on the same Vast host. Wan2GP's origin
    check was the only thing that would have refused such a page, and this decision
    accepts losing that check. The cookie already reached those pages before this
    change; what is new is that Wan2GP no longer refuses them. Separate
    `trycloudflare.com` hostnames are cross-site, because that domain is on the Public
    Suffix List.
  - If a user enables Wan2GP's own `--auth`, its session cookie is `SameSite=strict`.
    That cookie has the same port limitation.
  - A rewrite that translates only same-origin requests would keep the check meaningful.
    It is a portal-wide change and is tracked for the portal as a follow-up.
- The value is written to `/etc/environment` on first boot only. After that the user's
  edits win, but changing `WAN2GP_PORT` in `/etc/environment` after first boot does not
  update the list. The README documents the variable.
- The live QA gate would not have caught this. There is no wan2gp QA template, and no
  existing check sends a WebSocket or POST through Caddy with a browser Origin. Adding
  one is follow-up work.

## What would reverse this

- Upstream Wan2GP honours forwarded headers from a trusted proxy, or drops the check.
  Delete the stage.
- A second image needs the same rewrite. Build option 4 and move Wan2GP onto it. (This
  condition was met by aio-studio; see the amendment below for why the stage was copied
  instead.)
- The portal adopts a same-origin-only Origin translation as its default for every port.
  This stage then becomes redundant and should be deleted, together with the per-template
  `CADDY_HEADER_UP_LOCALHOST` entries.
- Note on the premise: Caddy already sends `Host: localhost:<port>`, because it proxies
  to `localhost`. The variable's effective change is therefore the Origin rewrite alone.
- Caddy's handling of `CADDY_HEADER_UP_LOCALHOST` changes, for example if the rewrite
  stops setting Origin. The test's generator assertion fails first. Otherwise fall back
  to option 5.

## Amendment (2026-09-17) — aio-studio ships the same stage

aio-studio bundles Wan2GP too, and is already affected. Its Dockerfile's
`WAN2GP_REF=8675eab` is only a local-build default. CI resolves Wan2GP's latest commit
and passes it in, the same as the standalone image. The scheduled build of 2026-09-15
resolved `d710430`, which contains the origin check. That build passed QA and was
promoted as `2026-09-15` and `latest`. Wan2GP is `autostart=false` in this image and
the QA route test does not start it, so nothing exercised it. The `2026-09-07` tag
predates the check (`362c346`). The same stage is therefore added here:
`derivatives/pytorch/derivatives/aio-studio/ROOT/etc/vast_boot.d/05-wan2gp-env.sh`.

- **Port.** aio-studio launches Wan2GP on `${WAN2GP_PORT:-17861}`, not 7860, so the copy's
  default is 17861. The rewrite list holds the INTERNAL port (the third field of a
  `PORTAL_CONFIG` entry), which is the port the app listens on. The template already
  routes `localhost:7861:17861:/:Wan2GP`.
- **Latent defect found on the way.** The image's own default `PORTAL_CONFIG`, used only
  when a template sets none, had `localhost:7861:7861:/:Wan2GP`. It has done so since the
  image was added, while the launcher always used 17861. With equal external and internal
  ports, the generator does not proxy that entry at all, and nothing listens on 7861, so
  Wan2GP was unreachable under the image default. The entry now reads
  `7861:17861`, and the READMEs' internal-port and `WAN2GP_PORT` rows now say 17861. The
  aio-studio route test checks only that a `Wan2GP` label exists, which is why this
  passed QA.
- **Reversal condition met, and deliberately not acted on yet.** "A second image needs
  the same rewrite" was the trigger for option 4, the generic portal mechanism. The owner
  chose to copy the stage now and track option 4 as portal backlog, where it is
  specified as a same-origin-only rewrite rather than a per-port declaration. The
  accepted cost is two copies of the same bash.
- **What keeps the copies honest.** The test runs every row against both images. It
  asserts the copies differ only in the default port, that each default equals the
  port the image's launcher uses, and that any image-default `PORTAL_CONFIG` entry for
  Wan2GP routes to that port.
