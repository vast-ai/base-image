# ADR 0027 — A behavioural contract, not a version pin, for the cloudflared binary

## Status

Accepted (conditional — see Binding conditions)

## Date

2026-08-17

## Context

`Dockerfile` fetches `cloudflared-linux-${TARGETARCH}` from GitHub's
`releases/latest`. What ships is therefore whatever Cloudflare published that
morning, and it changes on every base rebuild with no review step.

The question was forced by a customer escalation alleging that cloudflared caused
a DNS flood on a host. Investigation concluded it did not: the retry loop the
theory required does not exist in the source (three bounded retries; the reaper
never respawns), the arithmetic was three-plus orders of magnitude short, the
`argotunnel` timeout lines were a *symptom* of an exhausted state table rather
than its cause, and the host's own account was a natural experiment — a scanner
was killed and stayed dead, the container returned with cloudflared running, and
the flood did not resume. A counting resolver confirmed it independently: four
queries at tunnel creation, then none, including after blackholing an established
tunnel.

That left a real question the escalation had merely pointed at. The portal drives
cloudflared through three couplings, each of which an upstream release could break
independently:

1. quick-tunnel argv — `--no-autoupdate --no-tls-verify --url <target>`
2. daemon argv — `--no-autoupdate tunnel --metrics <hp> run --token <t>`
3. startup output — a line matching `QUICK_TUNNEL_URL_RE`

(3) has no compile-time signal at all. A changed announcement line leaves
`start()` blocking until its 30s timeout, per tunnel, on every instance.

`--no-autoupdate` was added regardless of the DNS question: cloudflared otherwise
replaces its own binary inside a running customer instance and restarts to do it,
which is not a thing that should happen unannounced on a rented GPU host.

## Options considered

### A. Pin the cloudflared version (rejected)

Determinism, and the obvious response to "an unreviewed binary ships daily".

Rejected because a pin is a knob someone has to turn on every base rebuild and
every portal release, and a version bumped only under time pressure is a version
nobody validates. It converts a continuous, visible exposure into a periodic,
invisible one, and it does not answer the actual question — whether the binary
still answers the way the portal drives it. A pinned-but-unvalidated binary can
break the portal exactly as thoroughly as an unpinned one; it just does so on the
day someone finally bumps it.

It also does not compose with the house convention: `syncthing` and `miniforge` in
the same Dockerfile are unpinned-latest, so pinning one of three would be a local
exception rather than a policy.

### B. Pin *and* contract-test (rejected as the primary, kept as the fallback)

Belt and braces. Rejected as the primary because the pin makes the contract test
nearly inert — it would validate the same bytes every run until someone bumps —
so the test would rot unnoticed, and its first real exercise would be the bump
itself, under time pressure. If the contract proves inadequate in practice, this
is where to retreat to.

### C. Behavioural contract at build time, unpinned binary (chosen)

Stop caring which version ships; assert that whatever ships still answers the way
the portal drives it. Same reasoning as the provisioner's `hf` CLI contract test,
and the same reason its requirements are bounded rather than pinned exact.

### D. Runtime check on the instance (rejected)

Assert the contract at boot instead of at build. Rejected: it moves the failure
from a build a human is watching to a customer instance at 3am, and the only
remedy available there is to log loudly. Detection belongs where the binary
enters, which is the build.

## Decision

**C, with the contract wired to a three-state signal.**

- `portal-aio/tests/test_cloudflared_contract.py` asserts all three couplings
  against the binary **extracted from the image just built** — not re-downloaded,
  because `releases/latest` can move between the build and the check.
- One cell per architecture (amd64, arm64 under QEMU/binfmt), and no more:
  amd64/arm64 are different release artifacts, but a cell per config × python
  would rate-limit itself and then report the rate limit as a defect.
- `--no-autoupdate` is asserted as **honoured**, not merely accepted, by reading
  cloudflared's echoed `Settings: map[...]` — the key is absent entirely when the
  flag is not passed, so "parsed" and "applied" are distinguishable.
- **Positive evidence is checked before negative evidence.** A tunnel that was
  demonstrably created gets its announcement asserted regardless of later
  transport noise.
- **The outcome is three-valued**, derived from the junit report by
  `classify_contract_run.py`: `verified` / `unverified` / `broken`, rendered as
  ✅ / ⚠️ / ❌ with a do-not-promote headline on `broken`.

## Binding conditions

These come from an adversarial review of the change and are load-bearing; the
decision is only sound while they hold.

1. **Skip must never render as pass.** The gate's normal degraded outcome is
   `skip` and pytest exits 0 on it — measured under a real per-IP rate limit:
   `3 passed, 3 skipped`, exit 0, where the passes introspect the portal's own
   argv and say nothing about the binary. The state must come from which live
   assertions *executed*, never from an exit code.
2. **Liveness comes from the marker**, carried into the junit report by an autouse
   fixture — never from a list of test names, which a rename would silently
   downgrade to "verified having verified nothing".
3. **`unverified` must not fail the build.** A Cloudflare outage reddening base
   builds is how a gate gets switched off. It must be visible (⚠️), not fatal.
4. **An upstream failure must not be reported as a tunnel defect.** The contract
   job is skipped when `merge-manifests` fails; `not-run` is kept distinct so a
   compile error never renders "the tunnel binary is UNVALIDATED".
5. **The per-PR suite must not spend the tunnel quota** (`-m "not live"`), or the
   build-time run that tests what actually ships arrives rate-limited.
6. **The architecture of the extracted binary must be asserted**, not printed, or
   a `--platform` resolution slip silently re-tests amd64 twice.

## Consequences

**Positive.** The binary that ships is the binary that is tested, on both
architectures, every build. A flag rename, a dropped flag, or a changed
announcement format is caught before promotion rather than by a customer. No
recurring version-bump chore, and no pin to go stale.

**Accepted negative.** Cloudflare's availability is coupled to the build's
headline: the inconclusive bucket is a closed enumeration, so an
`api.trycloudflare.com` 5xx or certificate error blocks images that are fine. The
alternative — widening the bucket — is precisely what let a real break through in
the first version, so the coupling is preferred to the blindness. If it fires in
practice, route those causes to `unverified` rather than widening the skip.

**Accepted negative.** `--no-autoupdate` removes the only mechanism keeping a
long-lived instance's cloudflared current, with no freshness floor and no alert.

**Accepted negative.** There is no skip floor: nothing fails when the gate has
been `unverified` for N consecutive builds. The ⚠️ makes that visible but not
enforceable; closing it needs run-history state the build workflow does not keep.

**Accepted negative.** Promotion does not read the contract result. It is a
separate human-gated dispatch, and the control is the notification plus the human
who reads it. Stated plainly because the wiring could be mistaken for an
automated block.

**Accepted negative.** The fetch still has no checksum or signature; a behavioural
contract passes just as happily on a substituted binary.

## What would reverse this

- The contract goes `unverified` often enough that nobody reads the ⚠️ — at which
  point the gate is decoration and option **B** (pin, with the contract as the
  bump-time check) becomes the honest choice.
- Cloudflare starts rate-limiting hard enough that the build-time run rarely
  verifies anything, making the tunnel spend pure cost.
- cloudflared gains a stable machine-readable startup output, which would make the
  announcement coupling (3) cheap to assert and might justify a runtime check too.
- Evidence that a substituted binary is a realistic threat here, which would make
  integrity verification the priority over behavioural assertion.
