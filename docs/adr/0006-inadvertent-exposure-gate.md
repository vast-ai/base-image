# ADR 0006 — Inadvertent-exposure gate: a fail-closed, allowlist-based public-port scan

- **Status:** Accepted (conditional — see Binding conditions). Ships ADVISORY first.
- **Amended:** 2026-08-13 — condition 2's flip is taken for **base only**. See
  "Amendment: enforcing for base-qa" below.
- **Date:** 2026-06-25
- **Decision owner:** Rob Ballantyne
- **Process:** discussion → critical review of the design (which found the first-draft
  protocol-probe approach fatally fail-open and the harness self-failing) → reshaped
  to the surviving design below.

## Context

> **Note on references.** ADR 0002 (PORTAL_CONFIG / EXPOSE conventions) and the CI smoke
> tooling cited below are on a separate, not-yet-merged branch, so they are absent from
> `main` at the time this ADR lands — the numbering convention of [ADR 0008](0008-template-publish-tooling.md)
> (take the next free number; reconcile when the others merge) applies. So this ADR stands
> alone, the condition it realizes is restated here in full: *a service must not be
> reachable on a public interface without Caddy's auth gate in front, and that must be
> checked at runtime rather than asserted in review.*

ADR 0002 binding condition 1 mandated a runtime `ss -ltnp` smoke gate that fails if a
service is reachable on a public interface without Caddy's auth in front — but it was
never built, and is enforced nowhere today. The existing CI tool
(`tools/imagegen/smoke/bind-check.sh` + `portal_smoke.py`) checks the *positive*
direction (the ports we declared/EXPOSE are reachable) and is wired into no workflow.
The missing piece is the *negative* direction: **catch a service inadvertently exposed
publicly without passing the Caddy auth gate.**

Verified reality that shapes the design:

- The base test framework (`ROOT/opt/instance-tools/tests/runner.sh`) runs `base/*.sh`
  on **every** image at boot when `INSTANCE_TEST=true` (QA only — not on customer
  instances). So a base test gives universal coverage for free; this is a QA gate, not
  a runtime defense.
- **Caddy only protects HTTP.** Raw TCP/UDP services (e.g. VNC 5900) can be legitimately
  exposed and there is no global mechanism to auth them — they must be *flagged*, not
  failed.
- A **protocol probe is fail-open** and was rejected: on the live image, `jupyter` on
  `0.0.0.0:8080` is a real HTTP service yet a bare `curl` returns `http=000`. Deciding
  fail-vs-warn by probing would mislabel real exposures as benign — the opposite of what
  a security gate needs. The sound model (mirrors `portal_smoke.check_binds`) decides
  from `(public bind, owning process, declared intent)`, **protocol-independently**.
- Some public listeners are **Vast-injected / platform** and the image does not own them:
  `jupyter` on `:8080` (injected at runtime via `/.launch`; in Docker-entrypoint mode it
  instead sits behind Caddy), the test-results server on `:10199` (the harness itself,
  up only during a run), sshd `:22`, an unattributable `*:17022`, syncthing UDP. The image
  cannot put these behind Caddy, so they belong on a base allowlist.
- The repo's composable idioms (`vast_capabilities.d`, `vast_boot.d`, `conf.d`,
  `tests/*.d`) are the natural shape for a **layered allowlist**: base ships the floor,
  each image's `ROOT/` overlay adds its own.

## Decision

Add one base test, `ROOT/opt/instance-tools/tests/base/28-inadvertent-exposure.sh`, run
on every image, with a **fail-closed, allowlist-based** verdict:

1. Enumerate every **public** listener (`0.0.0.0` / `::` / `*`) from `ss -ltnp` (TCP) and
   `ss -lunp` (UDP), with owning process.
2. Verdict per listener (protocol-independent):
   - owning process is **caddy** → pass (the auth gate; its public fronts are legitimate);
   - port/proto in the **allowlist union** → pass (the declared-intent set);
   - **no owning process** (platform-injected) → **warn**, never fail — unattributable to
     the image;
   - **UDP** not allowlisted → **warn** — no global auth gate exists for it;
   - otherwise (public TCP, not caddy, not allowlisted) → **violation**.
   A `curl` probe runs **only to enrich the message** ("looks like unauthenticated HTTP"
   vs "non-HTTP raw") — it never changes the verdict.
3. **Layered allowlist** — `ROOT/opt/instance-tools/tests/exposure-allowlist.d/*.conf`,
   union of all fragments. Base ships `00-base.conf` (the platform/Vast-injected floor);
   derivatives & external images drop their own (`50-<name>.conf`, e.g. `5900/tcp raw VNC`).
   Format: `port/proto  class  note` (class ∈ raw | self-auth-http | harness | platform).
4. **Base floor (`00-base.conf`):** `22/tcp` (sshd), `8080/tcp` (jupyter, self-auth-http —
   Vast-injected via `/.launch`, cannot be forced behind Caddy), `10199/tcp` (the test
   harness), `21027/udp` (syncthing discovery). Caddy's own fronts are auto-passed, not
   listed. The unattributable `17022` is handled by the no-process warn rule.

## Binding conditions

Surviving conditions from the critical review. If any is refused, the decision is void.

1. **Fail-closed, never probe-to-downgrade.** The verdict is decided by
   `(bind, process, allowlist)`, not by whether `curl` confirms HTTP. Any public TCP that
   is not caddy and not allowlisted is a violation regardless of protocol; the probe only
   annotates the message. (A probe-decides design is fail-open — proven by `jupyter:8080`
   reading `http=000`.)
2. **Ships ADVISORY first.** Violations are reported (`echo` + summary) but the test
   PASSES until a clean baseline is demonstrated across all images. Promotion to hard
   FAIL is a deliberate later flip (`EXPOSURE_ENFORCE=true`, then change the default) —
   exactly how ADR 0002 staged L051 advisory→gate. A hard FAIL on day one would red every
   image and violate the "baseline stays CLEAN" protocol — and the harness's own
   `0.0.0.0:10199` listener is a live example of why (it is allowlisted, not failed).
3. **Serverless is handled explicitly.** No Caddy exists in serverless, so the gate
   `test_skip`s with a loud message rather than mass-false-failing. **The
   serverless-specific rule is deferred, and the deferral is a stated hole, not a
   tidy-up:** `SERVERLESS=true` publishes port 3000 with no Caddy auth in front of
   it, so serverless is the configuration where inadvertent exposure matters *most*
   and is the one this gate does not cover. Scoped as a named follow-up (see
   "Known gap: serverless is untested end-to-end" below).
4. **Honest about scope.** Green means "no un-allowlisted public listener observed at scan
   time." It is a QA-time best-effort point-in-time scan (runner runs post-provisioning
   but a late-binding service can still escape), not a runtime guarantee; the message and
   docs say so. Corollary, added after review: the summary line must never read as clean
   when violations were reported — advisory mode passes the *test*, it does not get to
   relabel the *finding*.
5. **The allowlist must be able to express every legitimate public listener, or the
   gate can never be promoted.** Some services bind a port Vast assigns per-instance
   (syncthing's sync listener), which no literal `port/proto` key can match — leaving a
   permanent false violation on the base floor and making condition 2's "clean baseline"
   unreachable. The format therefore supports `env:VAR/proto`, resolved at scan time,
   with an unset/non-numeric variable allowlisting nothing (fail-closed). Because the
   allowlist format and directory name are the contract every derivative writes fragments
   against, both were settled *before* rollout: fragments live in `exposure-allowlist/`
   (not `…​.d/`, which the runner globs as a derivative **test** directory — any `.sh`
   dropped there would be executed as a test).
6. **A scan that cannot run is not a pass.** If a scanner binary is missing the check
   exits 2 and FAILS regardless of `EXPOSURE_ENFORCE` — "the tool is absent" must never be
   indistinguishable from "nothing is listening." Likewise a non-root run skips loudly
   rather than reporting green, because `ss -p` cannot attribute other users' sockets and
   would silently degrade every root-owned listener to an unattributable warning.

## Consequences

- **Positive:** finally realizes ADR 0002 condition 1 at runtime, universally (every image,
  every QA run), fail-closed; the layered allowlist makes "what we intend to expose"
  explicit, reviewed, and greppable, owned by whoever ships the service; raw TCP/UDP
  (VNC) is accommodated by declaration, not silently allowed.
- **Negative / accepted:** an explicit allowlist must be maintained (the convenience of
  auto-classifying was the fail-open hole); point-in-time scan can miss a late binder;
  the FAIL is advisory until a clean baseline earns the promotion.

## Known gap: serverless is untested end-to-end (named follow-up, 2026-08-06)

Surfaced while enabling serverless on the vllm/sglang/llama/comfyui images. Recorded
here so it is a tracked hole rather than an assumption; the fix is a separate change
after [ADR 0019](0019-base-image-promotion-qa-gate.md) lands, because it needs 0019's
fail-not-skip contract to be enforceable at all.

The harness cannot currently observe serverless mode in either direction, and
skip-as-pass hides both halves:

- **Serverless-only tests never run.** `base/85-serverless-services.sh` and
  `base/86-serverless-pyworker.sh` open with `is_serverless || test_skip`, and no QA
  template sets `SERVERLESS=true` — not `vllm-qa`, not `comfyui-qa`. So in every CI QA
  run they skip, report green, and the serverless surface has **zero** automated
  coverage. What exists today is hand-driven verification via test templates.
- **Turning it on would hide the rest.** Under `SERVERLESS=true`, six of the 25 base
  tests self-skip — `20-portal`, `25/26/27-caddy-*`, `70-logging`, and **this gate** —
  so a serverless QA run would be a quarter skipped and still green, with the exposure
  scan absent from the configuration that most needs it (condition 3).

What the fix requires, when it is taken up: per-cell required-test sets (ADR 0019's W2
already needs these for the stock-vs-cuda split); a second QA cell for each
serverless-capable image launched with `SERVERLESS=true`, requiring `base/85` and
`base/86` to *pass*, not merely be present; and the serverless exposure rule itself —
port 3000 owned by pyworker is the legitimate case, anything else public is not.

## What would reverse this

- If the base floor cannot be made clean across all images (a real, un-allowlistable
  public HTTP exposure exists), STOP and fix the image — do not allowlist around it.
- If a runtime (not QA) bind defense is needed, this test does not provide it (it only
  runs under `INSTANCE_TEST=true`); that is a separate mechanism.
- If Caddy gains layer-4 (TCP) auth, the raw-TCP warn category narrows.


## Amendment: enforcing for base-qa (2026-08-13)

Condition 2 made the scan advisory "until a clean baseline is demonstrated across
all images", with promotion a deliberate later flip. This amendment takes that
flip for **`templates/base-qa/` only**; every other image keeps the advisory
default.

**Why base first.** base owns `exposure-allowlist/00-base.conf`, so its baseline
is the one that can be reasoned about without inheriting another image's
listeners. Leaving the scan advisory everywhere had a cost that grew with time:
the check ran on every gate cell, correctly identified violations, printed them,
and then reported `passed`. An advisory gate nobody promotes is a permanent green,
and the defect class it covers — an image inheriting an upstream `EXPOSE` and
binding `0.0.0.0` past the auth proxy — has already reached customers once.

**The evidence.** Three configs of the last successful promotion
(`cuda-11.8`, `cuda-12.6`, `cuda-13.3`) each reported
`summary: 0 violation(s), 3 warn(s)`. That is three of ten gated configs, from
one run — enough to show the baseline is not obviously dirty, not enough to call
it demonstrated across the matrix. The remaining configs are covered by the same
`ROOT/` overlay and the same allowlist, so the risk is concentrated in
config-specific listeners, of which there are none known.

**Accepted risk, and why it is bounded.** If a config does carry a violation, its
cell reds and its `-auto` tag holds — the safe direction, and the finding is real
rather than spurious. `EXPOSURE_ENFORCE` is read from the template at dispatch
time, not baked into the image, so disarming it is a one-line template revert and
a re-dispatch with **no rebuild**. That is the cheapest rollback of anything in
this release.

**What this does not do.** It does not flip the default for derivative or
external images, and it does not discharge condition 2 for them. Condition 4
still applies: a green scan is point-in-time, and a listener that binds after the
scan is not covered.
