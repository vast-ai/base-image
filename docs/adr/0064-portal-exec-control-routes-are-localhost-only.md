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
locally — is a cheap, in-our-lane hardening: the declarative provisioner's value is
local, container-native automation (run at boot or from an in-container script or
agent), which does not depend on the exec routes being reachable from the public
edge. The token-triggered *remote* path is the surface we are choosing to close.

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
test-engineer, red-team) twice. The first panel turned up the ordering bug, the
quick-tunnel bypass, and the over-broad `/supervisor/*` matcher. The second panel,
on the reworked edge-deny-plus-tunnel-guard design, showed that a target-spelling
tunnel guard is bypassable by DNS-to-loopback / IPv4-mapped IPv6 / trailing-dot
hostnames, and that the compiled-order proof was silently skipping in CI — which is
why the authoritative control moved to the asset (Option D). Their surviving
concerns are recorded in the binding conditions below.

## Options considered

### A. Remove the exec routes from base-image entirely
Delete the provision endpoint and the supervisor exec routes.
**Rejected.** The declarative provisioner is a deliberate product capability —
container-native automations that are not easily ported to other platforms.
Deleting the route destroys that capability *locally as well as remotely*, which is
not the goal: the value is the local automation (run at boot, or from a script or
agent inside the container), and that does not require the route to be reachable
off-box. The decision below keeps local provisioning fully working and gives up
only the *remote-triggered* path — a narrower cut than deletion, and one that
closes a public exec surface the platform's token boundary would otherwise have to
carry alone. (Remote, token-triggered provisioning was a possible use; it is
consciously surrendered here — see Consequences — not removed by accident.)

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

### D. Enforce locality at the asset, with edge + tunnel refusals in front (chosen)
A second blind panel on the first implementation of this option showed that an
edge deny plus a *target-spelling* tunnel guard is not enough: the guard matched
literal spellings of loopback, but `cloudflared` resolves the target at dial time,
so a public DNS name that resolves to `127.0.0.1` (`*.nip.io`, `localtest.me`), an
IPv4-mapped IPv6 literal (`::ffff:127.0.0.1`), or a trailing-dot FQDN would pass
the guard and still reach the control port — defeating the "every spelling of
local" promise. Enumerating spellings is the wrong primitive. The chosen shape
enforces locality where it can be judged authoritatively — at the asset — and keeps
the cheaper refusals in front of it:

1. **Authoritative: the portal app refuses the exec routes for any request that
   arrived through a proxy.** `/capabilities/provision` and
   `/supervisor/process/*` depend on `control_access.require_local_caller`, which
   returns 403 when the request carries any forwarding header
   (`X-Forwarded-*`, `X-Real-IP`, `Forwarded`). The Caddy edge stamps
   `X-Forwarded-Proto`/`X-Real-IP` (and appends `X-Forwarded-For`/`Host`) on every
   request to `:11111`; a `cloudflared` tunnel stamps the same family; a genuine
   in-container caller stamps none. This judges *provenance*, not target spelling,
   so it closes the edge, every tunnel hostname that resolves to loopback, and any
   Caddy path-normalization evasion in one place — independent of how the request
   was routed.
2. **Edge deny (early refusal):** emit the control-route deny as a matched
   **`handle`** (not `route`) so it shares the sort class of the no-auth catch-all
   and compiles ahead of the proxy in *both* auth modes. Scope the matcher to the
   exec routes only — `/capabilities/provision`, `/capabilities/provision/*`,
   `/supervisor/process/*` — so read-only `/supervisor/processes` and
   `/capabilities/services` still proxy and the Services UI keeps working remotely.
3. **Tunnel guard (early refusal):** refuse the obvious literal spellings of a
   quick-tunnel target aimed at the loopback control port at the single create
   chokepoint, before any `cloudflared` spawns, so a plainly-local target does not
   flash a public URL. This is explicitly an optimisation, *not* the boundary —
   completeness is item 1's job.
4. Test the compiled handler order against a real `caddy` binary in both auth
   modes (not the source string), and run that test in CI with the binary present
   so the proof is not silently skipped.

## Decision

Adopt **Option D**. The portal's exec-capable control routes
(`/capabilities/provision[...]`, `/supervisor/process/*`) are made localhost-only
by enforcing locality at the portal app itself — the routes refuse any request that
arrived through a proxy (edge or tunnel) — with the Caddy `handle`-based edge deny
and the tunnel-target guard kept in front as early refusals. In-container access to
`127.0.0.1:11111` is unchanged: the provisioner and supervisor controls remain
fully usable locally and from any process inside the container. This is a
defence-in-depth control that removes the *remote* exec surface; it is not a claim
that the routes are privileged (they are `≤` a local root shell the renter already
holds — see Consequences).

## Binding conditions

These are the panel's surviving concerns; the decision depends on all four holding.

1. **The deny must be a `handle`, proven by compiled order, and that proof must
   run.** The edge deny's correctness rests on Caddy sorting it ahead of the proxy
   — non-obvious and reversible by a well-meaning refactor to `route`. It must be
   guarded by a test that adapts the Caddyfile to JSON with a real `caddy` binary
   and asserts the 403 handler precedes `reverse_proxy`, **parametrized over both
   `ENABLE_AUTH=true` and `ENABLE_AUTH=false`**. A source-string assertion does not
   satisfy this condition. The test must also *execute in CI* (caddy on PATH at
   pytest time; `CI_REQUIRE_CADDY` makes it fail rather than skip) — a proof that
   silently skips is the same false-green trap as a source-string assertion.
2. **The matcher stays exec-scoped.** Read-only status (`/supervisor/processes`)
   and `/capabilities/services` must continue to proxy, or the Services UI breaks
   for remote users. Broadening the matcher to `/supervisor/*` reintroduces that
   regression and is forbidden without re-review.
3. **Locality is enforced at the asset, not only at the edge.** The exec routes
   must refuse requests that arrived through a proxy
   (`control_access.require_local_caller`). The edge deny and the tunnel guard are
   early refusals in front of it; the app-side check is what makes the control hold
   against a tunnel to loopback by *any* hostname spelling. If a change weakens the
   app-side check, the edge/tunnel refusals alone do **not** satisfy this ADR.
4. **Completeness is judged by provenance, not by enumerating spellings of
   "local".** The authoritative check must refuse every request carrying a
   forwarding header; the tunnel-target guard is explicitly *not* required to be
   complete (it may miss DNS-to-loopback, IPv4-mapped IPv6, trailing-dot forms —
   those are caught by condition 3). Do not re-introduce a spelling-enumeration
   guard as if it were the boundary.

## Consequences

**Positive**
- The two exec-capable routes are no longer reachable from the public internet,
  on new boots, in either auth mode — closing a remotely-reachable exec surface
  without removing any capability the renter has locally.
- Because locality is enforced at the asset (by request provenance, not target
  spelling), the control holds against a tunnel to loopback by any hostname, and
  against Caddy path-normalization evasions — not just the literal `:11111` target.
- The compiled-order proof is validated against the real Caddy binary and runs in
  CI (fails rather than skips without it), so the "handle sorts before route" fact
  is enforced on every PR, not merely asserted in a comment.
- The quick-tunnel feature keeps working for its intended use (a renter exposing
  their own service) but can no longer be turned against the portal's own edge.

**Accepted negative / limits**
- **Remote, token-triggered provisioning is given up.** A caller could previously
  `POST /capabilities/provision` from off-box with the token; that path now 403s.
  This is deliberate (no in-repo consumer uses it; provisioning runs locally at
  boot or from an in-container script/agent). Anyone who wants to script
  provisioning against a remote instance must do so over SSH / the local shell,
  not the portal HTTP route. If a product need for authenticated remote
  provisioning appears, it is Option B (a signed request), not a relaxation here.
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
