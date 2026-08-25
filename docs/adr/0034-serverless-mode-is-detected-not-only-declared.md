# ADR 0034 — serverless mode is detected from the platform, not only declared by the template

- **Status:** Accepted
- **Date:** 2026-08-25
- **Decision owner:** Rob Ballantyne

## Context

`SERVERLESS=true` switches an image's whole runtime mode. It is read in more places
than is obvious, and the list matters because it is the blast radius of getting the
value wrong:

- `boot_default.sh:72` — skips the portal and vast-cli updates (cold-start cost).
- `utils/exit_serverless.sh` — sourced by `caddy.sh`, `instance_portal.sh`,
  `jupyter.sh`, `syncthing.sh`, `tensorboard.sh`, `tunnel_manager.sh`, the engine
  images' `model-ui.sh`, and comfyui's `comfyui.sh` / `api-wrapper.sh`. Each exits
  immediately.
- `lib/provisioner/schema.py:92` — `skip_on_serverless: bool = True`, so a service
  registered from a **provisioning manifest** inherits the same exit convention unless
  its manifest opts out. The widest consumer, and the one least likely to be remembered.

  *Keep this boundary straight, because collapsing it misdirects debugging.* Supervisor
  is the only runtime orchestrator. The provisioner is a declarative YAML installer that
  runs at provision time and, among other things, **authors** supervisor units —
  `lib/provisioner/supervisor.py` generates a startup script and a `.conf` "matching the
  conventions used by existing services in this image", and that generated script sources
  the *same* `utils/exit_serverless.sh` as the image's hand-written ones. One mechanism,
  two authors — not two mechanisms. A stopped service is a supervisor fact; which author
  wrote its unit only tells you where to change it. Saying "the provisioner stops
  services" sends the next reader to the wrong component.
- `pyworker.sh:12` — the autoscaler worker only starts in serverless mode.
- `utils/exit_portal.sh:9`, and the test predicates `lib.sh` `is_serverless()`,
  `base/85`, `base/28`, and the four `*.d/20-serverless-pyworker.sh`.

Today that flag is set by hand in every serverless template. Measured against the
published autoscaler templates rather than assumed: **9 of 10 set it** — six as
`export SERVERLESS=true` in `onstart`, three as template env. The `onstart` form
exports *before* `entrypoint.sh &`, so our boot inherits it and `boot_default.sh:72`
sees the correct value. The fleet is coherent today.

So this ADR is not introducing serverless mode. It is removing a manual step that a
template author must remember, on a platform that already knows the answer.

**The signal.** Vast's autoscaler injects `MASTER_TOKEN` into every worker container.
`REPORT_ADDR` travels the same path but conditionally, so it cannot be the primary key.
The platform-side fix — the backend injecting `SERVERLESS` at instance-create, gated on
`endpoint_id` — is the correct long-term answer and is not built. This is a bridge to
it, and must be built as one.

### A finding this ADR investigated and refuted

A design review argued the dominant risk was a fleet flip: that no template sets
`SERVERLESS`, so today's workers run with the portal up, and the first base promotion
carrying detection would stop six services on every existing worker at once. **That is
wrong**, and it is recorded because it is the kind of claim that gets re-raised. Its
evidence was this repo's own `template.yml` files — which are QA templates — generalised
to the production fleet. The published autoscaler templates are not in this repo, and
9 of 10 already set the flag. There is no fleet flip.

## Options considered

**A. A base boot stage `ROOT/etc/vast_boot.d/01-detect-serverless.sh`, with
`boot_default.sh:71-75` moved into it. — CHOSEN.** Boot stages are *sourced* inside
`main()`, so they read its locals; the repo already relies on this at
`46-user-propagate-ssh-keys.sh:4`, `10-prep-env.sh:48` and `37-sync-environment.sh:5`.
So a stage can own both the detection and the update-flag decision, and the decision
stops living in two files. `01-`, not `05-`, because seven derivative and external
images already ship `05-*-env.sh` into the same directory.

**B. A named function inside `boot_default.sh main()` before line 71.** Smallest diff
and unambiguously earliest. **Rejected:** `boot_default.sh` is 83 lines of pure
orchestration with no policy, and it is the file a customer replaces wholesale via
`BOOT_SCRIPT`/`boot_custom.sh` — in practice a copy-and-tweak of this file. A serverless
power user is exactly who has one, and they would silently lose detection. It also gives
a deliberately temporary mechanism no natural way to die: expiry becomes a surgical edit
to the file everything depends on rather than `git rm`.

**C. Infer to a separate `SERVERLESS_INFERRED`, consumed only by `pyworker.sh`.**
The safest option by a wide margin: a false positive would cost "a worker started that
should not have" instead of "the instance is dark". **Rejected because it does not do
the job.** Shedding the interactive surface is the stated purpose, not a side effect —
cold start is the product on a serverless worker. An inference that starts the worker
and leaves six services running delivers none of the benefit. Recorded because the
rejection is a deliberate acceptance of blast radius, not a disagreement about the risk.

**D. Wait for backend injection.** Correct, authoritative, covers third-party images,
and not built. **Rejected on timing**, and this ADR's expiry exists so that when D lands
this mechanism is deleted rather than kept.

**E. Detect, and also normalise other environment (`PORTAL_CONFIG` was proposed).**
**Rejected unanimously by review, and the tracing is worth keeping.** Under serverless,
`caddy.sh` sources `exit_serverless.sh` *before* it reads `PORTAL_CONFIG`, so
`/etc/portal.yaml` and the Caddyfile are never generated and `base/15-boot-markers.sh`
already expects their absence. The one live consumer is
`portal-aio/capabilities/manifest.py:505`, which falls back to the `PORTAL_CONFIG` env
precisely when `/etc/portal.yaml` is missing — so a serverless instance advertises six
services that were deliberately stopped. That is a real defect in the wrong layer: fix
it in `manifest.py`, where the manifest can report the mode and the worker port instead.
Rewriting `PORTAL_CONFIG` at boot would add a third writer to a variable two already
fight over (the Vast controller strips Jupyter, `10-prep-env.sh:20` puts it back), and
`10-prep-env.sh` then freezes whichever won into `/etc/environment` permanently. The
detector's blast radius stays at exactly one variable, so a false positive is one flag
to unset rather than a config the customer cannot reconstruct.

## Decision

A new base boot stage `01-detect-serverless.sh`:

1. If `SERVERLESS` is already set to a non-empty value, **do nothing**. The inference
   never overrides a declaration.
2. Otherwise, if `MASTER_TOKEN` is present and non-empty, `export SERVERLESS=true`.
3. Write a provenance marker either way, and echo one line to stdout.
4. Carry `boot_default.sh:71-75`'s update-flag block into the same stage, below the
   detection, so the mode and its first consequence sit in one reviewable place.

**Precedence: only-if-unset, and the apparent conflict with the backend's rule is not
real.** The planned backend injection sets `SERVERLESS` *after* `get_extra_env()` so it
overrides a user value — defensible, because the backend reads `endpoint_id` and knows.
This is an inference from a proxy. Its false-positive cost is a customer-visible outage
on an instance where the operator explicitly typed `SERVERLESS=false` to prevent exactly
that. An inference must not overrule an explicit human declaration: it is the
lower-confidence signal with the larger blast radius. And when both mechanisms exist,
only-if-unset makes this one **automatically inert** — the backend value is in the
container env before PID 1, so the stage sees it set and does nothing. Override
semantics would buy nothing there and cost the opt-out here.

Empty counts as unset (`-z`): a template emitting `-e SERVERLESS=` is not a declaration.
Never write `SERVERLESS=false` on a negative verdict — every consumer tests
`,, == "true"`, so `false` and unset are identical in behaviour, and writing it pollutes
the `/etc/environment` snapshot with a decision nobody made.

## Binding conditions

1. **`VAST_SERVERLESS_DETECT` must exist as a runtime off switch**, read from the
   template env, defaulting to on. Without it, backing out a bad inference is a rebuild
   and re-promote of base plus 26 derivatives — weeks, during which the affected images
   are the ones customers rent. This repo's own rule, at `tests/lib.sh:44`: *"baked here
   can only be corrected by rebuilding and re-promoting every image. Behind a variable,
   a wrong budget is a template edit instead."* `VAST_CUDA_MAX_OVERRIDE`,
   `VAST_CPU_THREAD_CEILING` and `EXPOSURE_ENFORCE` are the existing precedents. It also
   lets the platform disable the bridge from their side the day backend injection ships.

2. **The detector EXPORTS only. It must never write `/etc/environment` itself.**
   That single rule is what makes `/etc/environment` the per-instance escape hatch, and
   it works because of stage ordering: the detector runs at stage 01, `10-prep-env.sh:48`
   sources `/etc/environment` at stage 10, so **a user's edit to that file overrides the
   detector** for every destructive consumer — `exit_serverless.sh`, `pyworker.sh`, and
   the manifest-authored units. `10-prep-env.sh:36` already persists the first-boot value
   via its existing snapshot; the detector needs to do nothing further.

   *A rejected alternative, recorded because it is the intuitive one and it is wrong.*
   Review proposed a managed block recomputed every boot (the
   `12-cpu-thread-limits.sh:81-175` pattern) on the grounds that `/etc/environment` is
   written once per instance and therefore latches. The latching is real — demonstrated:
   with a stale `SERVERLESS="true"` in the file, `boot_default.sh:72` reads `false` from
   the fresh container env while every service reads `true` after line 48 re-sources it,
   two values inside one boot. But re-deciding every boot **cannot help**, because
   `endpt_id` is written only at instance-create and nothing attaches an existing
   instance to an endpoint afterwards: **an instance's serverless-ness is immutable for
   its lifetime**, so a second decision can only ever repeat the first. What it would do
   instead is overwrite a user's `/etc/environment` fix on every boot — removing the
   escape hatch in the name of protecting it. The carve-out review proposed for this
   ("an explicit assignment outside the block always wins") is subtle, easy to get wrong,
   and unnecessary once the mode is recognised as immutable.

   One residual to accept rather than engineer away: the update-flag block moved from
   `boot_default.sh:71-75` runs at stage 01 and therefore uses the DETECTED value, not
   the later `/etc/environment` value. A user who edits the file gets their services back
   but still skips a portal update that boot. Cosmetic.

   *Why this is the right rule and not merely a convenient one.* `/etc/environment`
   behaving this way is DELIBERATE, and it encodes an ownership boundary: the platform
   seeds the environment at first boot, and from then on **the user owns the container**
   — an edit to that file prevails, by design (`10-prep-env.sh:47`: "We can now edit
   environment variables in a running instance"). Export-only is therefore the detector
   correctly participating in first-boot seeding and then getting out of the way, rather
   than a happy accident of stage ordering.

   The same model explains `docs/invariants.md:127` — "a variable removed from the managed
   set must be `unset`, not merely stopped being written". That is not a leak workaround;
   it is the OBLIGATION the ownership boundary creates. An image that stops managing a
   variable must actively retract it, precisely because it may not rewrite the user's file
   wholesale. `migrate-unset-xet` (ADR 0025) is the shape of that retraction, and any
   future retraction of `SERVERLESS` by this mechanism would owe the same.

3. **A provenance marker, written on both outcomes.** `verdict=declared|detected|none`,
   which key was present, and the resolved value — never the token's value. Echo one line
   to stdout, since docker logs is the only surface a serverless instance has. Surface
   `verdict` in `80-capabilities-manifest.sh`. Writing it on the *negative* verdict is
   the point: "detection ran and declined" versus "this image predates detection" is the
   distinction an on-call needs, and only a marker provides it. Follows the
   `CUDA_CONFIG_FAILED_MARKER` pattern in `05-configure-cuda.sh`, whose comment reads
   "Detection should be designed, not accidental".

4. **Add a QA cell; do not swap the existing one.** Swapping the serverless cell's
   `SERVERLESS=true` for `MASTER_TOKEN` silently disables two enforcement mechanisms:
   `linter.py:715` (L073, which requires the QA template to map the worker port) keys on
   the literal `SERVERLESS` in the caller's `extra_env`, and
   `test_template.py:1392` `detect_serverless()` does the same to decide whether to send
   `OPEN_BUTTON_TOKEN=1`. That function's own docstring records that this exact hole was
   already found once and "worked by coincidence rather than by rule". Swapping would
   also delete all coverage of the explicit path, which is what 9 of 10 production
   templates use. Keep that cell; add a detection cell beside it, and teach
   `detect_serverless()` about the new signal with a test alongside the existing six.

5. **An expiry, and a linter rule that enforces it.** The mechanism is a bridge to
   backend injection. Carry an `EXPIRES:` date in the file and a new `RULES` code that
   WARNs while it exists and ERRORs once the date passes, forcing deletion or a reviewed
   extension. The property that makes a date defensible here: **expiry degrades to
   today's behaviour**, not to a broken one — if nobody revalidates, the file is deleted
   and templates go back to setting the flag by hand, which is what 9 of 10 already do.
   ADR 0031 decision 6a's *semantics* are the model (a deviation that stops reproducing
   becomes a violation, so it expires on evidence); its machinery is a per-engine QA
   checker and is the wrong home. State plainly that 6a's BOUNDED condition is **not**
   met — no other assertion covers this hazard — and that the hard date is the
   compensating control.

## Consequences

**Positive.**
- A template author cannot forget the flag, and a third-party or customer template gets
  the mode right without knowing the convention exists.
- The mode decision and its first consequence stop living in two files.
- When backend injection lands, this becomes inert with no coordination between teams.

**Accepted negatives.**
- **A false positive darkens the instance, permanently.** `exit_serverless.sh` exits
  **0**, and those units are `autorestart=unexpected` + `exitcodes=0`, so supervisord
  treats the stop as intentional and **never restarts them for the life of the
  instance**. Portal, Caddy, Jupyter, tunnel manager, syncthing, tensorboard, and every
  manifest-registered service stay down — those units default into the same convention;
  the customer sees a running instance whose
  Open button does nothing. `MASTER_TOKEN` is unprefixed and generic, so any template,
  provisioning script or `$WORKSPACE/.env` carrying that name triggers it. Conditions 1
  and 3 are the whole mitigation.
- **It silently reduces exposure coverage.** `base/28-inadvertent-exposure.sh` skips
  under serverless (ADR 0006 condition 3). So a false positive closes the Caddy auth
  gate and the scan that would notice it at the same moment. That makes a wrong
  inference a security-posture change, not only a UX one.
- **A second uncontracted upstream dependency in the boot path of every image.** ADR 0031
  decision 5 already accepts one (`pyworker@main`) and requires it be made legible. If
  the autoscaler renames or namespaces `MASTER_TOKEN`, detection stops silently and
  nothing in this repo fails. The marker's `verdict` field is what makes that visible as
  a fleet-level number going to zero.
- The image now infers a fact about the platform. Authority for the mode moves from
  declaration to inference, and there is no channel for the platform to say "no" other
  than condition 1's off switch.

## What would reverse this

- **Backend injection lands.** Delete the file; the precedence rule has already made it
  inert.
- **A false positive is observed in production.** The blast radius is severe enough that
  one real occurrence should retire the mechanism rather than add heuristics to it.
- **The autoscaler sets `MASTER_TOKEN` on non-endpoint instances.** The signal is then
  dead and the file must be deleted, not patched.
- **Evidence that `MASTER_TOKEN` is not universal across workers.** The premise is an
  assertion about a system this repo does not build and cannot test against; a written
  contract from the autoscaler team on the name, the injection guarantee and the
  change-announcement path would strengthen it, and its absence is the reason for the
  expiry.
