# ADR 0026 — One TLS cert-usability predicate, and validate before installing

- **Status:** Accepted
- **Date:** 2026-08-13

## Context

Every instance that runs Jupyter gets a TLS certificate at boot.
`ROOT/etc/vast_boot.d/55-tls-cert-gen.sh` generates a keypair, POSTs the CSR to
`console.vast.ai`, installs what comes back, and — if that fails — self-signs.
Three separate places then ask "is this pair usable?", and each had grown its own
answer:

| site | implementation |
|---|---|
| `55-tls-cert-gen.sh` | `sha256sum` of each side's DER public key |
| `portal-aio/caddy_manager/caddy_config_manager.py` | `openssl rsa -in KEY -check` |
| `ROOT/.../tests/base/27-caddy-tls.sh` | `openssl rsa -in KEY -check` |

The middle one is not a test. `validate_cert_and_key()` gates
`wait_for_valid_certs()`, which is what decides whether Caddy comes up with TLS.

**`openssl rsa` is the RSA-only entry point.** It cannot load an EC key at all,
so both `-check` callers reject a perfectly good EC keypair: the portal spends
`MAX_RETRIES x 5s` on it and then disables HTTPS, and the test hard-fails a
healthy image. We only ever *generate* RSA, so this could only be reached by an
operator supplying their own certificate — which is exactly the case with no
fixture behind it. Neither `-check` caller compares the certificate to the key at
all, so the converse also held: a mismatched pair, which is what a half-finished
regeneration leaves behind, passed both.

The digest form fails the other way, and the distinction matters:
`sha256sum` of empty input is `e3b0c442…`, a fixed non-empty string, so two
failed `openssl` invocations produce identical digests and the `[[ -n "$c" ]]`
guard — which checks the *digest*, not the key — reports a match. But in the
shipped script that expression sat below an `openssl x509 -noout` parse check,
and no certificate that clears that check goes on to fail `pkey -pubin`
(verified against RSA, EC, DSA, Ed25519 and a certificate with a deliberately
corrupted modulus). **The fail-open was therefore unreachable.** It was safe by
an accident of ordering, with an inert guard above it implying otherwise. A trap
for the next edit, not a fault that bit.

Two further defects in the same file, both about behaviour *across boots* rather
than any single line:

- The console-signed certificate was validated by PARSE alone before being
  installed. A console returning a well-formed certificate for somebody else's
  key passes that, gets installed, and clears the self-signed marker — and then
  the guard at the top finds the pair mismatched on the next boot and
  regenerates. Forever, printing "Instance certificate signed by the Vast
  console" every time, with HTTPS off.
- The self-signed retry counter was unbounded on the path where self-signing
  itself fails, because `! _cert_usable` re-enters regardless of the marker.

`55-tls-cert-gen.sh` had no test of any kind.

## Options considered

### A. Fix each site in place

Rejected. It is what produced three implementations in the first place, and the
next question ("is it expired?", "does it match?") would be answered in three
places again, or in two of them. The sites also disagree about severity — one
turns HTTPS off, one reds a release gate — so a divergence is invisible until
the two disagree about the same file.

### B. A shared bash library, sourced

Rejected, narrowly. `ROOT/opt/instance-tools/lib/` holds a Python package and
nothing else, so this would invent a sourcing convention for one function; and
the portal is Python, which cannot source it. Both callers would still need
different mechanisms.

### C. One executable helper in `bin/`, called by everyone (chosen)

`/opt/instance-tools/bin/cert-usable <crt> <key>`, exit 0/1 with a reason on
stderr. This is the shape the repo already uses for exactly this problem
(`cuda-driver-version`, ADR 0024): extensionless, absolute, callable from bash
and from Python, and enforced as the single implementation by a linter rule.

### D. Make the boot script testable with a `$VAST_CERT_DIR` override

Rejected for the *paths*. The defects here are multi-boot state machines, so
they need repeated execution against persistent `/etc` — but buying that with a
test seam in the customer's TLS path is the wrong trade when a throwaway
container root is free. Accepted, in effect, for the provisioner's state
directory (`PROVISIONER_STATE_DIR`), where the alternative was a test sharing
`/.provisioner_state` with the customer's real provisioning run.

## Decision

1. **One predicate.** `/opt/instance-tools/bin/cert-usable` compares the PEM
   SubjectPublicKeyInfo of the two sides directly — no hashing step in which
   emptiness can stop looking like emptiness — after checking that the
   certificate parses and has not expired. All three sites call it.
2. **Enforced, not just documented.** Linter rule **L066** fails any shipped
   script that open-codes the check, matching both RSA-only forms
   (`openssl rsa -check`, `-modulus` — the latter has no `rsa` token on the
   certificate side) and the hash-then-compare form, in shell and in Python
   argv-list syntax.
3. **Validate the signed certificate against the key we just generated**, with
   the same predicate the regeneration guard uses, before installing it. The two
   can then no longer disagree about the same file, which is what made the loop
   non-terminating.
4. **Clamp the retry counter** at the limit and read it with an explicit radix
   (`10#`), so a leading zero in an operator-edited marker cannot abort the boot
   with an octal error.
5. **Test it.** A unit suite against real RSA/EC/mismatched/expired/unreadable
   fixtures, and a container harness that boots the real script repeatedly
   against a persistent `/etc` with a shimmed `curl`.

## Binding conditions

1. **The container harness is the gate, not the unit suite.** Every defect this
   file has had was invisible in a single execution. A future change here must
   extend `tls-cert-gen-harness.sh`, not only `test_cert_usable.py`.
2. **Mutation-proven, and one gap was found that way.** Reverting the
   validate-before-install produces 8 harness failures; dropping the `10#` radix
   produces 1. Removing the counter clamp produced **none** until a scenario was
   added for it — the clamp is unreachable unless self-signing keeps failing, so
   the original scenario stopped incrementing on its own. Coverage claims here
   are only worth what a mutation says they are.
3. **`cert-usable` fails closed at both callers.** A missing helper means we
   cannot tell a certificate from an HTML error page; serving TLS over the
   latter is worse than serving none. Every image that ships either caller also
   ships the helper (same `COPY`), so this is a broken-image signal and both
   callers say so explicitly rather than degrading quietly.
4. **The unreachability finding is recorded, not quietly upgraded.** The digest
   form was a latent hazard. Describing it as a live fail-open would be a more
   satisfying story and a false one, and this file is the place that has to
   resist that.

## Consequences

Positive:

- An operator-supplied EC certificate works, at the boot script, the portal and
  the gate, instead of silently disabling HTTPS at two of the three.
- A mismatched or expired pair is now detected everywhere, where previously it
  was detected nowhere.
- The console-signing path terminates under every response we can construct: a
  good certificate, a certificate for another key, an HTML error page, and no
  response at all.
- `55-tls-cert-gen.sh` goes from zero tests to a 10-scenario multi-boot harness.

Accepted negatives:

- Three CI jobs now depend on docker being present, and the imagegen suite's
  runtime grows by minutes. The alternative was testing the paths and state that
  are the entire subject matter with neither paths nor state.
- `cert-usable` is a fourth process spawned per boot and per portal start.
  Irrelevant against a 2048-bit keygen on the same path.
- The portal now depends on a file outside `portal-aio/`. It already depended on
  `/etc/instance.crt`, so the coupling is to the image layout it already
  assumed, but a standalone install of the released portal will take the
  fail-closed branch and say so.

## What would reverse this

- A caller that genuinely needs a *different* answer — e.g. a service that can
  serve an expired certificate and should not be told it is unusable. That is a
  second predicate with a name, not a second copy of this one.
- The helper becoming a bottleneck on a path where a process spawn is not free.
- Evidence that failing closed on a missing helper is wrong for some deployment
  we support, in which case the condition above is what changes, explicitly.
