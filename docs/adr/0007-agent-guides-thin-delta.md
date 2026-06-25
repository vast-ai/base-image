# ADR 0007 — Per-image agent guides carry the thin, non-derivable delta only

- **Status:** Accepted
- **Date:** 2026-06-25
- **Decision owner:** Rob Ballantyne
- **Process:** blind 3-lens panel (consumer marginal-utility / maintenance-drift /
  information-architecture) on the existing convention; surfaced raw; this records the
  adopted direction.

## Context

Every image ships a per-image markdown "agent guide" at `/etc/vast_agents/<name>.md`
(convention from CON-1556). The consumer is an AI agent (an LLM) that SSHes into a running
instance; at boot `80-capabilities-manifest.sh` concatenates `base.md` + every per-image
guide into `/etc/vast-agents-guide.md`, and the login banner orders the agent to read it.

An agent already has **three** information layers:
1. **`base.md`** — a 425-line shared guide (14 sections: services, reaching them, ports,
   exposing apps, OpenAI endpoints, GPU/CUDA, storage, env, provisioning, …), identical on
   every image.
2. **`/capabilities`** — a live, machine-readable manifest (`/capabilities/endpoints` gives
   each service's real `base_url`, capabilities, and auth; `/capabilities/services` the
   running services). Authoritative; reflects live state.
3. The per-image **prose guide** — hand-written, ~23 images, 4–82 lines, inconsistent
   depth, with an active campaign (CON-1556/1561/1567/1570…) to "flesh out" the short ones
   to match the long ones.

A blind panel assessed whether the per-image layer adds value (scores: utility 7/10,
maintenance 4/10, architecture 7/10). Convergent findings:
- The value is real but **concentrated in a small, non-derivable kernel**: app-specific
  *workflows* (e.g. load/list a model via the app's own internal API, where models persist,
  which loaders/formats are supported), *gotchas* (e.g. "do not pass `--listen` — it binds
  `0.0.0.0` and bypasses the Caddy auth edge"), and *negative facts* ("this image has no
  Model-UI/Ray, unlike sibling images"). Prose is the right vehicle for this.
- **~half of a fleshed-out guide is dead weight**: restated ports, endpoints, portal labels,
  and service names — which the guide *itself tells the agent to read from `/capabilities`* —
  plus per-section `base.md` cross-refs. This is duplicative and, worse, **drift-prone**: the
  apps are third-party projects rebuilt to track latest upstream, and **nothing validates the
  prose** (the linter's only guide-touching rule, L040, greps for unfilled `>>> FILL`
  markers). A stale line in a security-relevant guide actively misleads an LLM told "this file
  IS the operating guide" — worse than no guide.
- The **"flesh out to match the long guides" campaign optimizes the wrong axis**: more length
  → faster rot. The 4–5-line stubs are closer to the right shape than the 82-line guides.

## Decision

**Per-image agent guides carry only the non-derivable delta.** Each guide is:
- **one framing sentence** ("base image plus preinstalled X; base.md applies; this is the
  delta") + the one-line pointer to `/capabilities/endpoints` for live base_url/auth;
- **app-specific workflows** (model/job management via the app's own API or UI, where data
  persists, format/loader support);
- **gotchas & non-obvious invariants** (security flags that must not be set, env vars that
  replace-vs-append, boot-coupling traps);
- **negative facts** ("this image lacks the sibling's service X").

It must **NOT** restate ports, endpoint paths, portal labels, or service names that are
already in `/capabilities` / `PORTAL_CONFIG` — read them live. **Exception:** a loopback port
*is* kept when the agent must curl an **app-internal API that the manifest does not carry**
(e.g. oobabooga's `127.0.0.1:15000/v1/internal/model/*`) — there the number is load-bearing.

**Length is a non-goal; correctness of the delta is.** A correct guide may be 8 lines or 30.
The "flesh out to match the long ones" direction is **reversed**: the existing comprehensive
guides should be thinned toward the delta; the stub guides should be filled in **only** where
a real workflow/gotcha exists (and a guide that adds nothing beyond "a service appears in
`/capabilities`" needs no per-image file at all).

`derivatives/pytorch/derivatives/oobabooga` is the first exemplar (re-scoped 66 → ~30 lines).

## Binding conditions

1. **Enforce with a content-linter (Bug→Invariant protocol), not vigilance.** Add a `RULES`
   code: a guide must not hard-code a port / endpoint path / portal label / service name that
   is already declared in its capability fragment (`vast_capabilities.d/*.yaml`) or
   `PORTAL_CONFIG` — **except** a loopback port used for an app-internal API absent from the
   manifest. Ship it with a mutation test and regenerate `docs/lint-rules.md`; the baseline
   must stay CLEAN (so the existing guides are thinned first, or the rule lands WARN then
   promotes). This is the change that stops re-drift and is the highest-leverage single item.
2. **Redirect the flesh-out campaign.** Re-scope the existing ~22 guides to the delta and
   point CON-1556/1561/1567/1570 at thinning, not lengthening.

## Consequences

- **Positive:** smaller, higher-signal guides; far less drift surface; the LLM consumer reads
  the live manifest for "what is true now" and the prose for "what you'll get wrong if you act
  before checking"; the boilerplate header could later be *generated* from the capability YAML.
- **Accepted-negative:** the convenience-redundancy of an inline port (one fewer `/capabilities`
  call) is given up — deliberately, since that redundancy is the drift liability.

## What would reverse this

- If the consumer becomes a tool-using agent that reliably queries `/capabilities` first, even
  the framing boilerplate is overhead → shrink further to workflows + gotchas only.
- If a guide's gotcha/workflow content cannot be expressed without restating a manifest fact in
  a non-load-bearing way, revisit the linter exception list (don't just allowlist around it).
