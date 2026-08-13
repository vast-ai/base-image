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

The digest form fails the other way: `sha256sum` of empty input is `e3b0c442…`,
a fixed non-empty string, so two failed `openssl` invocations produce identical
digests and the `[[ -n "$c" ]]` guard — which checks the *digest*, not the key —
reports a match.

**This ADR originally claimed that fail-open was unreachable. That claim was
false, and how it was reached matters more than the fact.** The argument was
that an `openssl x509 -noout` parse check sat above the comparison and no
certificate clearing it goes on to fail `pkey -pubin` — "verified against RSA,
EC, DSA, Ed25519 and a certificate with a deliberately corrupted modulus". The
modulus was the wrong thing to corrupt: any integer is a valid RSA modulus, so
that sample could not have falsified the claim it was cited for. Corrupt the
SPKI **algorithm OID** instead and the certificate parses (`rc=0`), passes
`-checkend 0` (`rc=0`), and yields no public key (`rc=1`) — so the digest
comparison is reached with an empty certificate side and, against an unreadable
key, returns USABLE.

What actually made it improbable in the shipped script is narrower and worth
naming precisely: `/etc/instance.key` is generated locally two lines earlier, so
the *key* side was rarely the one that failed. Not the ordering of the checks.

The correction is recorded here rather than quietly folded into the fix because
the reasoning error — verifying one path and generalising from it — produced
three of the defects this ADR exists to close.

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

`/opt/instance-tools/bin/cert-usable <crt> <key>`, exit 0/1/2 with a reason on
stderr. This is the shape the repo already uses for exactly this problem
(`cuda-driver-version`, ADR 0024): extensionless, absolute, and callable from
bash and from Python.

### D. Make the boot script testable with a `$VAST_CERT_DIR` override

Rejected for the *paths*. The defects here are multi-boot state machines, so
they need repeated execution against persistent `/etc` — but buying that with a
test seam in the customer's TLS path is the wrong trade when a throwaway
container root is free. Accepted, in effect, for the provisioner's state
directory (`PROVISIONER_STATE_DIR`), where the alternative was a test sharing
`/.provisioner_state` with the customer's real provisioning run.

## Decision

1. **One implementation, two policies.** `/opt/instance-tools/bin/cert-usable`
   compares the PEM SubjectPublicKeyInfo of the two sides directly — no hashing
   step in which emptiness can stop looking like emptiness — and reports
   **three** outcomes: `0` usable, `2` matched but expired, `1` unusable.

   The third code exists because "usable" is not one question, and collapsing it
   into a boolean turned a correct predicate into a downgrade. An expired but
   matched pair must be *regenerated* at boot, where a new keypair costs
   milliseconds — and *served* by the portal, whose only alternative is no TLS
   at all: the same public port in plaintext, carrying the portal auth token in
   `?token=`. An expired certificate still encrypts. Nothing repairs
   certificates outside a boot, so treating expiry as fatal at the portal would
   silently downgrade any long-lived instance whose certificate lapsed, the
   moment supervisor restarted caddy (`autorestart=unexpected`).

   `55-tls-cert-gen.sh` uses both readings, deliberately and in one file: strict
   for "should I regenerate?", tolerant for "can supervisor serve TLS with what
   is on disk?" — the latter being the portal's question, asked earlier.
   `base/27-caddy-tls.sh` stays strict: a QA cell has no plaintext dilemma, and
   an expired certificate there means the boot script declined to replace one it
   should have.
2. **Enforced as far as a linter honestly can.** Linter rule **L066** fails any
   shipped script using the shapes that have already shipped wrong — the
   RSA-only forms (`openssl rsa -check/-noout`, `-modulus`, the last having no
   `rsa` token on the certificate side) and the hash-then-compare form — in
   shell and in Python argv-list syntax, including across line breaks, since a
   formatter wrapping the portal's six-element list was enough to exempt the one
   caller that gates TLS. It is a blocklist of known-broken spellings, **not** a
   proof that no fourth implementation exists; a hand-rolled but correct SPKI
   comparison passes it. The rule text says so rather than implying otherwise,
   and `test_rules_text_cites_paths_that_exist` keeps the remediation it names
   from drifting out of the tree — which it already had, pointing at the
   sourced-library design option B rejects.
3. **Validate the signed certificate against the key we just generated**, with
   the same predicate the regeneration guard uses, before installing it. The two
   can then no longer disagree about the same file, which is what made the loop
   non-terminating.
4. **Clamp the retry counter** at the limit and read it with an explicit radix
   (`10#`), so a leading zero in an operator-edited marker cannot abort the boot
   with an octal error.
5. **Test it.** A unit suite against real RSA/EC/mismatched/expired/unreadable/
   unknown-algorithm fixtures, and a container harness that boots the real script
   repeatedly against a persistent `/etc` with a shimmed `curl`.
6. **A missing helper stops the boot script rather than warning and continuing.**
   See binding condition 3 — "warn and carry on" is not the conservative branch
   here, it is the unbounded key churn this ADR exists to end.

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
3. **The two callers must handle a missing helper DIFFERENTLY, because they are
   not shipped the same way.** This condition originally asserted that both
   arrive by `COPY` in the same Dockerfile. That is true of the boot script and
   false of the portal: `release-portal.yml` publishes `portal-aio` as a tarball
   and `first_boot/10-update-instance-portal.sh` untars it over `/opt/portal-aio`
   on any version mismatch. So a portal release lands on already-published
   derivatives and on customer images built `FROM` an older vast base, none of
   which carry `bin/cert-usable`.

   - **Boot script:** the helper is genuinely co-shipped, so absence is a broken
     image. It must **stop** — leave the on-disk pair alone and let the final
     guard disable HTTPS. Note that merely warning and continuing is *not* the
     conservative act here: without the predicate every check returns 127, so
     the regeneration guard is true on every boot and the script becomes the
     unbounded key-churn plus per-boot signing traffic it exists to end.
   - **Portal:** absence means an *older* image, not a broken one, and failing
     closed there disables HTTPS on instances whose certificate is fine. It
     falls back to the same SPKI comparison in-process. That is a second copy of
     the comparison and is accepted as the price of the release skew; it is
     minus the expiry check, which this caller tolerates anyway.
4. **A correction is recorded as a correction.** This ADR asserted the digest
   fail-open was unreachable; a review refuted it with a fixture, and the Context
   section now carries both the refutation and the flaw in the original
   reasoning. The condition is not "resist upgrading a latent hazard into a live
   one" — that framing pre-committed future reviewers to a conclusion, which is
   itself a defect in a document meant to be read fresh. The condition is:
   **when a claim here is shown false, fix the claim in place and say what the
   bad inference was**, because the inference (verify one path, generalise) has
   now produced three separate defects in this work.

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
  are the entire subject matter with neither paths nor state. Absent docker they
  skip locally and **fail** under `CI`, because a silent skip in a gate is the
  failure mode this repo already has a dedicated test to prevent.
- The portal carries a second copy of the SPKI comparison for the release-skew
  case (binding condition 3). L066 does not catch it, and would not catch a
  third: the rule blocks known-broken spellings, not re-implementation.
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
