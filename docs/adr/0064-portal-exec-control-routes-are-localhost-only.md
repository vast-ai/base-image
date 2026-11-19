# ADR 0064 — The portal's exec-capable control routes are localhost-only

Status: Proposed
Date: 2026-11-19

## Context

The Instance Portal (portal-aio) runs a FastAPI control app on `127.0.0.1:11111`,
fronted by Caddy on the public per-instance edge (external `:1111`). A single
per-instance token (`OPEN_BUTTON_TOKEN`, equal to the Jupyter token) authenticates
every public route: Caddy accepts it as `?token=`, a cookie, or a bearer header.

Two of the portal's routes do more than read state — they execute:

- `POST /capabilities/provision` applies a declarative manifest (apt/pip/git/
  write_files/services/post_commands/…) and spawns a process.
- `POST /supervisor/process/{name}/start|stop|restart` controls supervised
  processes.

These are *container-native* capabilities: inside the container they grant no
privilege the renter does not already have. The platform's own model is
root-by-design — the platform builds an intermediate image that installs and
launches Jupyter `--allow-root`, and a token holder already has a root shell one
click away via the Jupyter terminal (and via SSH in every launch mode except
entrypoint). Provisioning is strictly `≤` that shell. So the control routes are
not a privilege-escalation vector on their own machine.

They are, however, **exec-capable routes exposed on the public internet behind a
single shared token**. The token is the platform's boundary, not base-image's, but
base-image owns the Caddy edge and can narrow what that edge serves. Reducing the
remotely reachable exec surface — without removing any capability the renter has
locally — is a cheap, in-our-lane hardening: the declarative provisioner is meant
to be driven over the network with the token, but the *UI-polled* control surface
and the exec routes do not all need to be reachable from the public edge.

The decision is which of these routes to refuse at the public edge, how to refuse
them so the refusal actually holds in Caddy, and how to keep that refusal from
being trivially bypassed by the portal's own features.

### Forces

- **Caddy directive ordering is not source order.** The Caddyfile adapter sorts
  `handle` ahead of `route` regardless of where they appear in the file. A `route`
  deny placed "first" in the source still loses to a catch-all `handle` — which is
  exactly the shape of the no-auth config (`generate_noauth_config` emits a
  catch-all `handle { reverse_proxy }`). A deny that compiles *after* the proxy is
  dead.
- **The portal can tunnel itself.** The portal exposes a free-form quick-tunnel
  feature: `POST /start-quick-tunnel/{target}` hands a caller-supplied target to
  `cloudflared`, which exposes that local target on a public `trycloudflare.com`
  URL. The control-route deny lives on the `:1111` edge only; a quick tunnel
  pointed at `127.0.0.1:11111` reaches the FastAPI app *directly*, around the deny.
- **The Services UI polls remotely.** The portal's Services tab polls read-only
  supervisor status (`GET /supervisor/processes`) over the public edge. A deny
  scoped to all of `/supervisor/*` would break that tab for remote users.
- **Rollout is gradual.** A VERSION bump cuts a portal release that instances pull
  only at first boot (gated by the first-boot marker, ADR 0062). Running instances
  are unaffected until they are recreated; this control protects new boots, it is
  not a fleet-wide retroactive fix.

This change was run through a blind expert panel (security-auditor, code-reviewer,
test-engineer, red-team). The panel is what turned up the ordering bug, the
quick-tunnel bypass, and the over-broad `/supervisor/*` matcher — the first cut of
the control had all three. Their surviving concerns are recorded below.

## Options considered

### A. Remove the exec routes from base-image entirely
Delete the provision endpoint and the supervisor exec routes.
**Rejected.** The declarative provisioner is a deliberate product capability —
container-native automations that are not easily ported to other platforms — and
is meant to be driven over the network with the token. Removing it destroys a
feature to close a surface that is already `≤` the renter's own shell. It also does
nothing the platform's token boundary isn't already responsible for.

### B. Add a second, stronger auth factor to the exec routes
Require a separate secret or a signed request for provision/supervisor.
**Rejected (for now).** The serverless path already proves the pattern (Legion
signs with an Ed25519 key in KMS; workers verify with the public key). But the
interactive portal has no key-distribution channel to the renter that is stronger
than the token they already hold, and inventing one for routes that are `≤` a
local root shell is disproportionate. Worth revisiting only if these routes ever
grant more than the local shell does.

### C. `route`-based deny, placed first in the source (the first cut)
Emit a `route @control { respond 403 }` as the first block in the portal site.
**Rejected — it does not hold.** Because `handle` sorts before `route`, the deny
compiles *after* the catch-all `handle` in no-auth mode and never fires there. The
unit tests asserted source-string order, so they stayed green while the control
was bypassed. This is the trap the panel caught: a security control whose test
checks the source text, not the compiled handler order, is decoration.

### D. Edge deny as a `handle`, exec-scoped, + close the self-tunnel bypass (chosen)
1. Emit the control-route deny as a matched **`handle`** (not `route`) so it shares
   the sort class of the no-auth catch-all and compiles ahead of the proxy in
   *both* auth modes.
2. Scope the matcher to the exec routes only — `/capabilities/provision`,
   `/capabilities/provision/*`, `/supervisor/process/*` — so read-only
   `/supervisor/processes` and `/capabilities/services` still proxy and the
   Services UI keeps working remotely.
3. Close the self-tunnel bypass in `tunnel_manager`: refuse any quick-tunnel target
   that resolves to the portal's own loopback control port (`:11111` on localhost,
   `127.0.0.0/8`, `::1`, or `0.0.0.0`/unspecified), at the single create chokepoint,
   before any `cloudflared` spawns. Every other target is untouched, so the
   free-form quick-tunnel feature still works for a renter's own services.
4. Test the compiled handler order against a real `caddy` binary in both auth
   modes, not the source string.

## Decision

Adopt **Option D**. The portal's exec-capable control routes
(`/capabilities/provision[...]`, `/supervisor/process/*`) are refused at the public
Caddy edge with a `handle`-based 403 deny that compiles ahead of the reverse proxy
in both the authed and no-auth configs, and the portal's own quick-tunnel feature
is prevented from re-exposing those routes by refusing tunnels aimed at the
portal's loopback control port. In-container access to `127.0.0.1:11111` is
unchanged: the provisioner and supervisor controls remain fully usable locally and
from any process inside the container.

## Binding conditions

These are the panel's surviving concerns; the decision depends on all four holding.

1. **The deny must be a `handle`, proven by compiled order.** The deny's
   correctness rests entirely on Caddy sorting it ahead of the proxy. This is
   non-obvious and reversible by a well-meaning refactor to `route`. It must be
   guarded by a test that adapts the Caddyfile to JSON with a real `caddy` binary
   and asserts the 403 handler precedes `reverse_proxy`, **parametrized over both
   `ENABLE_AUTH=true` and `ENABLE_AUTH=false`**. A source-string assertion does not
   satisfy this condition.
2. **The matcher stays exec-scoped.** Read-only status (`/supervisor/processes`)
   and `/capabilities/services` must continue to proxy, or the Services UI breaks
   for remote users. Broadening the matcher to `/supervisor/*` reintroduces that
   regression and is forbidden without re-review.
3. **The self-tunnel guard is part of the control, not an optional follow-up.** The
   edge deny is bypassable via the portal's own quick tunnel until tunnels to the
   loopback control port are refused. The control is only complete with both halves
   shipped together.
4. **The guard refuses by loopback interface + control port, not by a single
   string.** It must cover every spelling of local (`localhost`, `127.0.0.0/8`,
   `::1`, unspecified) with or without a scheme, and must leave public
   `<host>:11111` and all non-control targets alone.

## Consequences

**Positive**
- The two exec-capable routes are no longer reachable from the public internet,
  on new boots, in either auth mode — closing a remotely-reachable exec surface
  without removing any capability the renter has locally.
- The refusal is validated against the real Caddy binary, so the "handle sorts
  before route" fact is encoded in a test, not a comment.
- The quick-tunnel feature keeps working for its intended use (a renter exposing
  their own service) but can no longer be turned against the portal's own edge.

**Accepted negative / limits**
- **Edge-only, by design.** Anyone with the token still reaches
  `127.0.0.1:11111` from inside the container (Jupyter terminal, SSH). This is
  intentional: the control routes are `≤` a local root shell, which the platform
  grants by design. This ADR narrows the *remote* surface, it does not claim the
  routes are privileged.
- **Not a fleet-wide fix.** Running instances pick up the change only when
  recreated (first-boot gate, ADR 0062). The platform's token boundary remains the
  primary control for existing instances.
- **Other internal ports are out of scope.** A token holder can still quick-tunnel
  other Caddy-fronted internal app ports (e.g. a proxied app's internal port),
  exposing their *own* instance's app without the token edge. That is the renter's
  prerogative on their own box and is not what this control protects; the guard is
  deliberately narrow to the portal's control port so it does not break the
  free-form feature.
- **No-auth mode still means an open portal.** The deny fires in no-auth mode, but
  a renter who set `ENABLE_AUTH=false` already has an unauthenticated portal; this
  control does not make that configuration safe, it only ensures the exec routes
  are not *additionally* reachable there.

## What would reverse this

- The control routes stop being exec-capable, or gain a privilege a local shell
  does not already have (then Option B — a real second factor — becomes necessary
  rather than disproportionate).
- The platform moves the token boundary (e.g. per-route scoping, signed requests),
  making an edge deny redundant or better expressed at that layer.
- Provisioning moves to a signed-request model like serverless (Ed25519/KMS), at
  which point the route is self-authenticating and need not be localhost-only.
- Caddy changes its directive-ordering semantics such that `handle`/`route` sort
  class no longer determines precedence (the compiled-order test is what would
  catch this, and it would force a redesign of how the deny is emitted).
