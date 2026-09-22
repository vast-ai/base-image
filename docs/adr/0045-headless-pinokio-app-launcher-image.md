# ADR 0045 — A headless-Pinokio app-launcher image with a slot-pool proxy

- **Status:** Proposed (conditional — see Binding conditions; not built)
- **Date:** 2026-09-22
- **Decision owner:** Rob Ballantyne

## Context

Pinokio is an open-source one-click installer/launcher for community AI apps
(image/video/audio/LLM front-ends). It is desktop-oriented (an Electron shell over a
Node server, `pinokiod`), so on a rented GPU it is not usable as shipped. The ask: run
Pinokio headless behind the image family's existing portal (Caddy + token auth) so a
user installs and launches catalogue apps from a browser, and the apps become reachable
**without the user choosing or configuring ports** — Vast external ports are fixed at
instance creation and cannot be added at runtime.

This decision was reached through a full design review: an idea gate, a live feasibility
trial on a Blackwell GPU box, three competing routing designs, a blind three-lens judge
panel, a synthesis, and a final adversarial gate. The working record (trial data,
designs, judge scoring, the gate) is under `docs/forge/2026-09-21-pinokio-dynamic-app-proxy/`.

Relevant prior art: ADR 0028 (exposure verdict = f(public bind, declaration); every public
listener is Caddy or declared), ADR 0042/0043 (an image owns the Caddy Host/Origin
localhost-rewrite for its ports), ADR 0005 (live-GPU QA gate), ADR 0017 (not-ready is a
non-5xx interstitial). The portal's Caddy admin API is disabled (a container process could
otherwise stop the proxy), so route changes cannot use a live Caddy reload.

Two facts from the trial frame the whole decision:
- **Value is unproven.** 6 of 10 sampled catalogue apps already ship here as QA-gated
  curated images; the tail only Pinokio adds was 2 of 4 working, at page-load only; there
  is no demand signal. So this ships **experimental**, not as a curated-image replacement.
- **`pinokiod` is unsafe to expose as-is.** It binds all interfaces with no auth, and it
  has an HTTP route that runs an OS command from a request (verified live: an
  unauthenticated loopback request executed as root). Portal token auth stops strangers
  but not a cross-site request from a browser holding the auth cookie, and every port on
  the instance IP is same-site. Exposing `pinokiod` therefore requires a cross-site guard,
  not just the token. (Specifics are held in the internal note and a private upstream
  report, not in this public repo — ADR 0012.)

## Options considered

Routing (how apps reach the browser from one or few fixed ports):

- **A — one port, a hostname per app via wildcard DNS (sslip.io/nip.io).** The only design
  with unlimited concurrent apps each on a real origin. Rejected for now: depends on
  third-party DNS in the critical path; the wildcard hosts are not on the Public Suffix
  List, so all of `sslip.io` is same-site (no CSRF protection, cross-app cookie setting);
  a TLS warning per app host; its default "passthrough" mode was never measured. Viable
  only with a Vast-owned PSL domain + wildcard cert, which this repo cannot deliver.
  Recorded as the preferred **future** shape if that domain appears.
- **B — one port, one "active" app at a time, switched by the user.** Meets "one port"
  literally. Rejected by all three judges: switching tears down the previous app's tabs and
  live streams; stale tabs POST into whichever app is now active; all apps share one browser
  origin (cross-app storage/cookie bleed). Structural, not fixable by config.
- **C — a small pool of pre-requested ports, one app per port at its root path** (chosen).
  Each app is isolated in its own process and its own origin; the mechanism is exactly the
  two-hop, root-path, separate-port path the trial measured working; it needs no portal or
  base change and is directly verifiable by a live-GPU QA cell. Costs: a hard ceiling of N
  apps (N=4) with manual eviction, and a different public port per app.

The panel ranked C > A > B unanimously across feasibility, product value, and risk.

## Decision

Build a new derivative image `derivatives/pinokio/` (FROM base, not pytorch — Pinokio brings
its own toolchain), scaffolded with `imagegen`, implementing routing option C at **reduced
scope**, shipped **experimental**. No change to the base image, the portal, or `ROOT/`
conventions; nothing ships through base promotion.

- **Topology.** The portal (unchanged, static, never restarted by this image) fronts three
  kinds of loopback listener the image owns: a **guard** in front of `pinokiod`; a **slot
  directory** service; and **N=4 per-slot relay** Caddies, each proxying one running app at
  `/`. All second-tier listeners bind 127.0.0.1, run `admin off`, carry
  `trusted_proxies static 127.0.0.1/32` (so the portal's `X-Forwarded-Proto/Host` survive
  the second hop), sit below Pinokio's port allocator range and clear of known fixed app
  ports, and start before `pinokiod`. Nothing uses Caddy admin port 2019. `PORTAL_CONFIG`
  declares the slot entries; the template maps how many are live (the portal generator skips
  entries with no mapped port), so N is a template value.
- **`pinokiod` is never exposed without the guard.** `pinokiod` is patched at build to bind
  loopback (assertion fails the build if absent) and started with its own proxy and peer
  discovery disabled. The guard is a **fetch-metadata cross-site filter** that lets
  Pinokio's own same-origin UI through and rejects cross-site requests to the
  command-capable API. Per the final gate it is **default-deny with an explicit
  Origin/Referer fallback for requests that carry no `Sec-Fetch` metadata, covering GET as
  well as unsafe methods**; any request-form blocklist is belt-only, never relied on for
  completeness.
- **Per-app Host/Origin policy is opt-in.** Relays send the browser Origin unchanged by
  default; a per-app policy table (seeded from the trial) enables the localhost-Origin
  rewrite where an app needs it (e.g. ComfyUI, Wan2GP), with a user toggle in the directory.
  Rewriting is opt-in because it suppresses the app's own cross-site defence.
- **Discovery/lifecycle.** A `slotd` service attributes listening sockets to apps by process
  tree (all root — sound per ADR 0028), marks an app ready only on a live socket **and** an
  HTTP probe (Pinokio's own ready signal is untrustworthy), pins each slot target as
  (pid, socket-inode), keeps slots sticky per app, and **actively refuses to slot / warns on
  any non-loopback bind**. Port collisions and "more apps than N" surface as plain directory
  states, not routing bugs. The directory page is the supported way to reach an app; the
  in-Pinokio open-link rewrite is cut from v1.
- **Install gaps** handled in the image: a `pkexec` stand-in so Pinokio's `sudo` steps run
  headless as root (recorded as a root-equivalent surface, adding no marginal privilege over
  `pinokiod` itself); Node pinned to 20 / npm 10.
- **Cut from v1** (revisit on evidence): app loopback-coercion via LD_PRELOAD/NODE_OPTIONS
  (untested, can break GPU-distributed binds; containment holds via port mapping + the active
  stray-bind refusal above); the AI-bundle prewarm; the open-link injection.

## Binding conditions

The decision is void if any of these is refused or fails.

1. **Two pre-build checks pass on a live box, before building past the guard/relay:**
   - **P1 — `pinokiod` behind the portal+guard hop.** The trial reached `pinokiod` over an
     SSH tunnel, never through the portal. Confirm its UI (pages, fetches, WebSockets) works
     through the guard **including on a client that sends no `Sec-Fetch` metadata**, that a
     cross-site GET to a state-changing endpoint **not in any blocklist** is blocked, and the
     portal's actual `X-Forwarded-Host` value is the one the guard's WebSocket Origin check
     assumes. Both directions, header-absent included, or the guard is not proven.
   - **P2 — first-generate path.** The trial tested page load only. Run a real generation on
     at least one Gradio app (FramePack/TTS/Forge) **and a real ComfyUI node-graph run**
     through a slot relay, confirming the POST + streaming path (some generate POSTs carry
     Origin and can 403 where load did not).
2. **`pinokiod` is never reachable except through the guard**, and the guard is default-deny.
   The build asserts the loopback bind; a boot check stops `pinokiod` if it is ever seen
   off-loopback (fail-safe: a bump that moves the bind hard-fails the image rather than
   exposing it).
3. **The ADR 0028 exposure gate stays green** with apps running; the portal Caddy is never
   restarted by this image; nothing in the image writes `/etc/portal.yaml`.
4. **A concrete retirement checkpoint exists**: a named owner, a date, and a usage threshold
   below which the experimental image is removed — recorded in the template/README, not left
   implicit.
5. **The upstream `pinokiod` command-execution finding is reported privately** to the
   maintainers before or alongside release; no exploit detail enters this public repo
   (ADR 0012). A monthly `pinokiod`-pin bump re-verifies the patch anchors and re-runs P1;
   this upkeep is release-blocking, with a named owner.

## Consequences

- A user can install and launch Pinokio catalogue apps from the browser behind the existing
  token edge, with up to N=4 running concurrently, each isolated, and a second app does not
  break the first (the QA cell asserts a running app's WebSocket survives another app
  starting).
- New image-scoped linter codes (mutation-tested) and a live-GPU QA cell gate the image's
  own layer: loopback bind, disabled Pinokio proxy/peer, guard default-deny + UI-still-works,
  slot isolation, no `/etc/portal.yaml` writes, exposure scan green with apps running.
- Accepted negatives: N=4 ceiling with manual eviction; a public port (and possibly a TLS
  click-through) per app; the catalogue is unpinned and **outside** QA (stated in the
  template) — an app can break after release and users may blame the Vast image; monthly
  upstream-tracking upkeep; the `pkexec` shim as a recorded root-equivalent surface; value is
  unproven, hence experimental + usage-tracked.

## What would reverse this

- The pre-build checks (P1/P2) show the guard cannot both pass Pinokio's UI and block the
  cross-site command path, or that catalogue apps' generate paths do not work through the
  relay — build stops at the check.
- Usage stays below the retirement threshold by the checkpoint date — the image is removed.
- Vast provides a PSL-listed wildcard domain + wildcard cert — reopen **option A**, which
  gives unlimited apps on one port each with a real origin, and supersede this ADR.
- Upstream Pinokio adds its own authenticated, same-origin-guarded server mode — much of the
  guard/patch layer is deleted.
