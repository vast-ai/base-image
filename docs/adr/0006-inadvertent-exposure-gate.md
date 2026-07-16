# ADR 0006 — Inadvertent-exposure gate: a fail-closed, allowlist-based public-port scan

- **Status:** Accepted (conditional — see Binding conditions). Ships ADVISORY first.
- **Date:** 2026-06-25
- **Decision owner:** Rob Ballantyne
- **Process:** discussion → critical review of the design (which found the first-draft
  protocol-probe approach fatally fail-open and the harness self-failing) → reshaped
  to the surviving design below.

## Context

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
   `test_skip`s with a loud message (TODO: a serverless-specific rule), rather than
   mass-false-failing.
4. **Honest about scope.** Green means "no un-allowlisted public listener observed at scan
   time." It is a QA-time best-effort point-in-time scan (runner runs post-provisioning
   but a late-binding service can still escape), not a runtime guarantee; the message and
   docs say so.

## Consequences

- **Positive:** finally realizes ADR 0002 condition 1 at runtime, universally (every image,
  every QA run), fail-closed; the layered allowlist makes "what we intend to expose"
  explicit, reviewed, and greppable, owned by whoever ships the service; raw TCP/UDP
  (VNC) is accommodated by declaration, not silently allowed.
- **Negative / accepted:** an explicit allowlist must be maintained (the convenience of
  auto-classifying was the fail-open hole); point-in-time scan can miss a late binder;
  the FAIL is advisory until a clean baseline earns the promotion.

## What would reverse this

- If the base floor cannot be made clean across all images (a real, un-allowlistable
  public HTTP exposure exists), STOP and fix the image — do not allowlist around it.
- If a runtime (not QA) bind defense is needed, this test does not provide it (it only
  runs under `INSTANCE_TEST=true`); that is a separate mechanism.
- If Caddy gains layer-4 (TCP) auth, the raw-TCP warn category narrows.
