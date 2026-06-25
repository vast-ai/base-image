# ADR 0003 — Install + run a HuggingFace Space on a live instance (portal feature)

- **Status:** Accepted (conditional — binding conditions from the final red-team; conditions 1 & 3 remain blocking, **revised 2026-06-24**)
- **Date:** 2026-06-24
- **Decision owner:** Rob Ballantyne
- **Process:** idea brief → red-team gate (reshaped: dropped nothing, surfaced reachability) →
  3 blind architects → 3-lens blind panel (feasibility / maintainability / risk) →
  synthesis → final red-team gate → **revision after two Vast-fact corrections, re-gated.**

## Revision (2026-06-24) — two corrections to the experts' Vast model, re-gated

Two premises the plan was gated on were wrong; both were re-examined by a red-team:

- **Overlay storage persists across stop/start** (lost only on DESTROY). `load_config()`
  reads `/etc/portal.yaml` when present and regenerates from the `PORTAL_CONFIG` env var
  only when ABSENT, so Space entries written to it **survive stop/start** (regenerated only
  on destroy, which correctly has no installed apps). → **Condition 2 downgraded** from
  "a separate persistent store is mandatory" to "persistent `/etc/portal.yaml` is a viable
  registry **with** four sub-conditions" (2A–2D below). No longer blocking.
- **Docker Spaces are handled by Dockerfile-as-RECIPE, not by building/running a
  container.** The `Dockerfile` is parsed and its instructions translated into provisioner
  steps replayed on the live instance (no DinD / podman / nested namespaces / host probe).
  → **Condition 6 (container-runtime feasibility) DROPPED.** Replaced by conditions 7–9.
  Coverage is partial (~half-to-two-thirds of real Docker Spaces; ~a third honestly
  refused — multi-stage / `COPY --from` / compiled artifacts / non-apt bases).

**Unchanged by the corrections:** conditions **1 (consent gates the capability, not the
UI)** and **3 (spare-port raw ingress)** remain **FATAL/blocking** — they are orthogonal to
persistence and Docker handling, and Dockerfile-as-recipe makes #1 *worse* (the
unauthenticated boot/`PROVISIONING_URL` path can now replay arbitrary `RUN` as root).

**Latent base bug found while re-gating** (independent of this feature): an empty
`/etc/portal.yaml` (created by `caddy.sh`'s `touch` when `PORTAL_CONFIG` is unset) makes
`caddy_config_manager.load_config()` do `yaml.safe_load('')['applications']` → `TypeError`
→ no Caddyfile → Caddy/portal front fails to start. `portal.py:169` already uses the safe
`.get('applications', {})`; the Caddy manager (line 18) should too. Fix under the
Bug→Invariant protocol separately.

## Context

Users find ready-to-run model demos on HuggingFace Spaces but running one on a rented
GPU today means hand-reverse-engineering its SDK/entrypoint/requirements into a
provisioning manifest or a Dockerfile. The idea: from the Instance Portal, point at a
Space (repo id/URL), and have it installed, launched loopback-bound, and surfaced as an
authenticated portal tab.

Verified reality that shapes the decision:

- **Most of the install machinery already exists.** The provisioner (`lib/provisioner`)
  clones git repos, installs apt/pip/conda, downloads HF models, and `register_services`
  writes a supervisor conf+script and `supervisorctl reread/update` — i.e. it already
  registers a new long-running service on a LIVE instance, idempotently. Its `Service`
  schema supports isolated venvs + env injection with **no schema change**. So the novel
  surface is narrow: a **HF-Space → provisioner-manifest adapter**.
- **Reachability is fixed at instance creation.** Caddy proxies an external port only if
  `external != internal` AND `VAST_TCP_PORT_<ext>` exists (set at creation; see
  [ADR 0002](0002-portal-config-and-expose-conventions.md)). There is no runtime
  "open a port" API. **Resolution: the instance template reserves a POOL of spare mapped
  external ports; the feature leases one per app.**
- **`/etc/portal.yaml` is a regenerable boot CACHE of the `PORTAL_CONFIG` env var**, not
  durable authored state (`caddy_config_manager.load_config`). A stop/start can rewrite
  it from env. The Caddy manager is one-shot (no live-reload); applying config =
  `supervisorctl restart caddy` (a ~1–2s bounce of all portal sessions).
- **No container runtime exists in any image today** (podman/buildah/CDI/slirp4netns are
  greenfield).
- The instance is **single-tenant, everything runs as root, no sandboxing**; it holds the
  user's `HF_TOKEN` and platform creds in env. `venv` is not a security boundary. The
  hard loopback-behind-Caddy invariant and two prior `0.0.0.0` exposure incidents
  ([invariants](../invariants.md) §4) are the security backdrop.

## Options considered

- **Option 1 — Thin manifest adapter on the provisioner (CHOSEN as the Phase-1 spine).**
  Pure functions (`detect`, `reconcile`, `space_to_manifest`, `ports`) emit a provisioner
  manifest; thin portal routes preview + submit it to the existing provisioner. Won
  feasibility (8) and maintainability (9): almost no unproven mechanism on the critical
  path, ~zero net-new toolchain, matches ADR 0002's "reuse + detect drift, don't
  reinvent" judgment. Risk lens rated it **3/10** (run-as-root RCE next to secrets) — the
  reason for the binding conditions and the Phase-2 commitment.
- **Option 2 — First-class portal app-lifecycle subsystem (rejected).** A new
  `app_lifecycle` package with a persistent `apps.json` store, free-port lease registry,
  full lifecycle, boot-reconcile. Rejected as the spine: it manufactures the exact
  persistent-state drift/GC burden ADR 0002 deliberately avoided (maintainability 5).
  **Grafted:** its **private-internal-port lease idea** (assign the internal bind from an
  unmapped 14000–14999 range so `ext != int` by construction) and its
  **validate-before-apply + rollback** for Caddy. But see binding condition 2 — a real
  (small) state store IS needed; "derive from portal.yaml" is unsound.
- **Option 3 — Everything-is-a-container (rejected for Phase 1; adopted as Phase 2).**
  Every Space → image → nested rootless podman container; the container boundary is the
  only resolution that makes Python Spaces actually safe and disarms the `0.0.0.0` footgun
  by construction (risk 6/10, the highest). Rejected now because it depends on nested
  unprivileged user-namespaces + rootless GPU **inside the already-unprivileged Vast
  container** — a host-gated capability that silently degrades to zero value on some
  hosts (feasibility 4, maintainability 2) and imports a large greenfield toolchain.
  **Adopted as the Phase-2 direction** (namespace/container isolation where the host
  supports it). NOTE (revision): Docker Spaces are NOT built/run as containers in Phase 1
  — see Dockerfile-as-recipe in the Decision. The container path is purely a Phase-2
  isolation question now, not a Docker-feasibility one.

## Decision

**Phased.** Ship **Option 1** as Phase 1, evolve toward **Option 3** isolation in Phase 2.

- **Phase 1 (always-works):** the thin manifest adapter. `detect` parses the README YAML
  and **refuses with reasons** (missing `sdk`/`app_file`, gated model, Docker unavailable)
  rather than guessing. `reconcile` reconciles the Space's torch pin to the instance CUDA.
  `space_to_manifest` emits a manifest: `git_repos` + an isolated `/venv/spaces/<name>`
  pip target + a `services` entry binding loopback. Python Spaces run **as root** (venv
  isolation only), accepted via explicit consent (see conditions). External port leased
  from the spare pool; **internal bind from the unmapped 14000–14999 range**. **Docker
  Spaces: the `Dockerfile` is parsed as a RECIPE** — `RUN apt`→`apt_packages`, `RUN pip`→
  isolated-venv pip (torch pins stripped per the repo convention), `ENV`/`WORKDIR`/`CMD`→
  the supervisor service, with a mandatory `0.0.0.0`→`127.0.0.1` launch rewrite — and
  replayed on the live instance via the provisioner. No container is built or run. Recipes
  that can't translate faithfully (multi-stage, `COPY --from`, compiled artifacts, non-apt
  bases) are **refused with reasons** (condition 7), never best-effort. No fake
  secret-scrub; the Space process simply isn't handed `HF_TOKEN` (the trusted provisioner
  still uses it for gated downloads).
- **Phase 2:** namespace/container isolation for all SDK types where the host supports it
  (closes the run-as-root hole and condition 3 structurally).

## Binding conditions

Surviving findings from the red-team, **as revised 2026-06-24**. Conditions **1 and 3 are
blocking** (the decision is void without them); the rest are required before the
corresponding surface ships.

1. **Gate the capability, not the UI.** [BLOCKING] The provisioner must refuse to
   auto-install-and-run a Space from the unauthenticated boot / `PROVISIONING_URL` path;
   running Space code as root requires an authenticated, consented portal action (a consent
   token set only by the authenticated UI and checked in the provisioner step type). Until
   this exists, the consent screen is decorative. Made more acute by condition 7 — the boot
   path would otherwise replay arbitrary Dockerfile `RUN` as root.
2. **Persistent `/etc/portal.yaml` as the registry — viable with four sub-conditions**
   [RESHAPEABLE; downgraded from blocking]. The overlay persists across stop/start, so
   merged-not-clobbered `portal.yaml` is a sound durable registry. But it requires:
   - **2A.** Harden `caddy_config_manager.load_config` to `.get('applications', {})` (and
     treat an empty file as absent); always write a well-formed `{'applications': {...}}`.
     (Also the standalone base bug above.)
   - **2B.** A **boot-time reconcile** that re-derives base entries (Instance Portal,
     Jupyter) from the current `PORTAL_CONFIG` env + `10-prep-env.sh` logic and merges the
     persisted Space entries on top — because once the file exists the env is otherwise
     ignored forever (a later template change would silently never apply).
   - **2C.** **Revalidate leased `external` ports against `VAST_TCP_PORT_*` on every boot** —
     host mappings can move across stop/start, silently dropping a persisted entry from the
     Caddy proxy gate (installed-but-unreachable with no error).
   - **2D.** Atomic write-temp-then-rename + a lock (the current `open('w')`+`yaml.dump` is
     non-atomic; a torn file resurrects 2A's crash).
3. **Close, or name, the spare-external-port ingress.** [BLOCKING] A root Space can bind a free
   *mapped* spare port directly → raw, unauthenticated, TLS-less public ingress (an
   abuse-host harm, not just secret exfil). Either make spare ports mapped **on lease**
   (so an unleased port has no `VAST_TCP_PORT` to abuse — a platform-side change), or get
   explicit product-owner sign-off naming "unauthenticated public ingress / abuse host" in
   the consent text as an accepted Phase-1 harm.
4. **`reconcile` compatibility = actual install + import in the isolated venv**, not a
   static pin check (invariants §4: torch success is runtime-only). Runtime failure →
   blocker, before committing to a multi-GB model download where possible.
5. **Adopt the existing detached-restart pattern** (`portal.py`'s self-restart) for the
   Caddy bounce so the triggering request can report commit/rollback; respect
   `UNSTOPPABLE_PROCESSES`. `caddy validate` before restart; keep the prior Caddyfile for
   rollback.
6. ~~Scope honesty: Docker SDK out for Phase 1 (no container runtime).~~ **DROPPED** — the
   Dockerfile-as-recipe revision removes the container-runtime dependency; Docker SDK is in
   scope for Phase 1 via recipe translation, subject to conditions 7–9.
7. **Dockerfile-as-recipe: hard refusal, not best-effort.** Refuse (with the offending
   instruction + reason) on multi-stage builds, `COPY --from`, compiled-artifact `RUN`s,
   non-apt bases, and any non-allowlisted instruction. A non-translatable Dockerfile must
   fail loudly at install, never silently produce a dead app. Pass A (the refusal
   classifier) is built and tested **first**, against a real corpus of HF Docker-Space
   Dockerfiles.
8. **Mandatory post-install bind/serve smoke check before a Space is recorded "installed."**
   Static translation is explicitly not a correctness gate (CLAUDE.md: static is fast, the
   real check is runtime). The service must be confirmed serving on its declared loopback
   port before the portal entry is committed — this is the real gate the recipe path leans
   on, given lossy translation.
9. **Replayed `RUN` is untrusted root code on the LIVE filesystem.** Unlike a container
   build (effects confined to an image layer), recipe `RUN`s mutate the real root FS where
   the user's secrets live — equal-or-worse containment than the Python-Space path. Treat
   it under the same consent/isolation regime; feed it into the Phase-2 threat model. Do
   not let "it's just a recipe" downgrade the threat assessment.

Additionally (not blocking, but recorded): the `ss -ltnp` loopback check is a
**functional/correctness** gate (did the app bind loopback on its declared port; poll,
don't snapshot) — it is **explicitly not** an adversarial security control. The
14000–14999 internal range must be a confirmed cross-team contract that the template
never maps, or its "not externally reachable" guarantee evaporates. Space names
(`owner/space`) must be sanitized before reaching `register_services` (regex-guarded).

## Consequences

- **Positive:** delivers the headline value (run a Space on your GPU) on every host in
  Phase 1; rides the tested provisioner (`register_services`, FileLock, idempotency) with
  minimal net-new surface; the private-internal-range lease kills the equal-port footgun
  and keeps a misbinding Space's intended port off the public map; torch reconciliation
  matches the repo's strip-and-repin convention.
- **Negative / accepted:** Phase 1 runs untrusted code as root on the user's single-tenant
  box (consent-gated, not isolated); condition 3's ingress hole persists until Phase 2 (or
  a lease-time port-mapping platform change); `/etc/portal.yaml` is the durable registry but
  needs the 2A–2D reconcile/hardening; Docker Spaces are **in scope via recipe** but ~a
  third are honestly refused; the Caddy restart bounces portal sessions.
  **The phasing risk is real:** Phase 1 ships the cheap dangerous half and defers the
  expensive safe half — Phase 2 must not be allowed to become Phase-never (see reversal).
  **Conditions 1 and 3 remain blocking and were NOT resolved by the 2026-06-24 corrections;
  the feature does not proceed to build until both have enforced (not UI-level) controls.**

## What would reverse this

- If condition 1 (capability gating) or condition 2 (real state) cannot be met, **stop** —
  the safety story and the lifecycle/port bookkeeping both collapse on the real codebase.
- If the platform cannot offer lease-time port mapping AND the product owner will not
  accept the named ingress harm (condition 3), Phase 1 should not ship run-as-root Spaces;
  hold for Phase-2 isolation.
- If Phase-2 isolation proves unbuildable on the host fleet (nested rootless GPU fails on
  most hosts — the empirical go/no-go Option 3 flagged), then the feature is permanently
  a run-untrusted-code-as-root tool; reassess whether the official portal should ship it
  at all versus leaving it a power-user provisioner manifest.
- If new-Space volume is low enough that a documented "here's the manifest to write"
  recipe suffices, the adapter may not warrant portal UI at all.
