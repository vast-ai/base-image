# ADR 0049 — A console-signed instance certificate follows the machine's address

- **Status:** Proposed
- **Date:** 2026-10-01
- **Decision owner:** Rob Ballantyne
- Amends: [ADR 0026](0026-one-tls-cert-usability-predicate.md) — "a usable pair is
  left alone on reboot" gains one exception, made outside the predicate

## Context

`ROOT/etc/vast_boot.d/55-tls-cert-gen.sh` asks the console to sign a CSR and
installs the result as `/etc/instance.crt`. The console decides the certificate's
IP SAN; the CSR's own SAN is ignored. After that, the pair is replaced only when
`cert-usable` rejects it, and `cert-usable` checks parsing, key match and expiry.
It never looks at the SAN (ADR 0026). `/etc` survives stop/start, so a console
certificate is kept for its full 365 days.

That is correct while the SAN is the address clients dial. Two things make it
wrong:

1. **The console signed for the wrong address.** For a period the signer named
   the caller's egress address instead of the machine's public address, which
   differ on NAT'd hosts. That is fixed on the signing side. Certificates issued
   in that period remain on disk and pass every check the image makes.
2. **The machine's address changes after issuance.** The certificate keeps the
   old address until it expires.

A client that trusts the Vast root and checks the address gets a hard
name-mismatch error in both cases. That includes the serverless SDK, which loads
the Vast root and verifies by IP, and any browser where the user installed the
root to stop the warning. A client that does not trust the root sees an
untrusted-certificate warning either way, so the defect is invisible to it.

Facts the design rests on:

- **`PUBLIC_IPADDR` cannot be the comparison point.** The platform sets it when
  the container is created and never refreshes it. Separately,
  `10-prep-env.sh` snapshots the environment into `/etc/environment` once and
  re-sources that snapshot on every boot, so even a refreshed value would be
  masked by the image's own snapshot.
- **The live address is available.** `GET /api/v0/instances/$CONTAINER_ID/` with
  the instance's own `CONTAINER_API_KEY` returns `public_ipaddr`, read from the
  same machine record the signer uses. That key is optional in practice
  (`base/11-instance-metadata.sh` warns when it is absent).
- **Jupyter launch mode already re-signs.** In that mode the platform's launch
  command generates a key and requests a certificate on every container start,
  before the boot stages run. Stage 55 still runs there and only repairs a pair
  the platform left unusable.
- **No image change reaches an existing instance's boot script.** Stop/start
  reuses the container. The portal tarball update runs only on first boot.
  Derivatives pin dated base tags. So whatever is decided here affects instances
  created from images built after it. Certificates already issued in the cause-1
  period are fixed by the manual remedy in
  [docs/runbooks/tls-certificates.md](../runbooks/tls-certificates.md), not by
  this change.

## Options considered

### A. Compare the SAN with the live address in the boot script, and re-sign on a mismatch (chosen, hardened)

The question needs the environment (the live address), not only the files, so
it belongs in the boot script, not the predicate. As first proposed it had four
defects, each fixed by a binding part of the decision:

- **It could loop.** If the signer and the API ever disagree, every boot
  re-signs. That is the unbounded churn ADR 0026 ended, through a new door.
  Fixed by a per-address attempt bound.
- **It could downgrade.** The current regeneration path overwrites the key
  before it POSTs. A refresh that hits a rate limit would leave a mismatched
  pair, fall into the self-sign branch and replace a CA-signed certificate with
  a self-signed one. Fixed by re-signing with the existing key into a temp file
  and keeping the old pair on any failure.
- **It needed to know which certificates are the console's.** Without that it
  would re-sign customer certificates and churn the self-signed fallback, whose
  SAN is `0.0.0.0`. Fixed by a provenance marker.
- **IPv6 text forms differ.** openssl prints IPv6 SANs expanded and in upper
  case, the API returns the compressed form, so a string compare never matches.
  Fixed by comparing IPv4 only.

### B. Re-sign on every boot, keeping the existing key

Rejected. It is the simplest code, and the platform already does per-boot
signing in Jupyter launch mode. But:

- `generate_tls_cert` defaults to true, so B would replace a customer-supplied
  pair with a Vast-signed one on the next restart.
- It sends a signing request from every instance on every boot. The signer's
  rate limit is per caller address, which every container on a host shares, so a
  host reboot becomes a burst of rejections.
- Gating it on "console-signed" needs the same machinery as A, and then all it
  saves is one read-only GET.
- It reopens the per-boot signing traffic ADR 0026 deliberately ended.

### C. A new "wrong address" exit code in `cert-usable`

Rejected. `caddy_config_manager.validate_cert_and_key()` serves only on exit 0,
or on 3 with the `has expired` sentinel. Every portal already released would
read a new code as unusable and turn HTTPS off: plaintext on the public port,
carrying the portal token in `?token=`. That is the downgrade ADR 0026 exists to
prevent. It would also make a pure, offline predicate depend on the network.
ADR 0026's own "What would reverse this" settles the shape: a caller needing a
different answer gets a separately named check, not a new code.

### D. No code change; document the manual remedy only

Rejected as the whole answer, kept as part of it. It has no regression risk, and
it is the only thing that reaches certificates already issued (see Context).
But a machine address change is a recurring event, not a one-off, and D
re-accepts "a certificate obtained once is kept however wrong it is", the defect
ADR 0026 named.

### Rejected variants of A

- **Recognise console certificates by verifying them against a Vast root shipped
  in the image.** Rejected. A pinned root couples every published image to one
  CA. If the CA rotates, every image's check goes stale at once, and the only
  fix is rebuilding all of them.
- **Recognise them by local shape** (issuer differs from subject, an IP SAN, no
  DNS SAN). Rejected. A customer certificate carrying only an IP address passes
  it and would be replaced.
- **Generate a fresh key on refresh**, staged in temp files and moved into
  place. Rejected. Keeping the key is the smaller change, means the key never
  churns, and touches only the certificate file.
- **Normalise IPv4 and IPv6 in Python.** Rejected. The address column is IPv4 in
  practice, and comparing IPv4 only (skipping anything else) avoids a new
  interpreter in this boot stage.
- **Exactly one attempt per address.** Rejected in favour of a small cap. The
  refresh fires at the moment a host's containers boot together after an address
  change, which is when the signer's shared rate limit rejects requests. One
  attempt would leave a rejected instance wrong until the next address change.
- **Skip the whole of stage 55 in Jupyter launch mode.** Rejected. The
  platform's own certificate step writes the response without checking it, so a
  failed request can leave an error page where the certificate should be. Stage
  55's existing repair path is the backstop for that. Only the new refresh is
  skipped in that mode.
- **Check only every Nth boot, or once the certificate is old enough.**
  Rejected for now; it delays the fix for a real address change. It is the
  fallback if the per-boot read proves to be unacceptable load (see "What would
  reverse this").

## Decision

1. **A separately named refresh step in `55-tls-cert-gen.sh`**, after the
   existing regeneration block and before the final `ENABLE_HTTPS` guard.
   `cert-usable` is unchanged: no network access, no new exit code. The final
   guard is unchanged: a wrong-address certificate still encrypts and is never a
   reason to turn HTTPS off.

2. **A provenance marker, `/etc/.instance-cert-console`.** Whenever stage 55
   installs a console-signed certificate, by the existing path or by a refresh,
   it records that certificate's SHA-256 fingerprint here. The self-sign branch
   removes it. The refresh acts only when the certificate on disk matches the
   recorded fingerprint. So it never touches:
   - a customer-supplied pair (no marker, or a fingerprint that does not match);
   - the self-signed fallback (marker removed when it was written);
   - a certificate the platform wrote in Jupyter launch mode (not installed by
     stage 55);
   - a certificate installed by an image built before this change (no marker).
     That last case is covered by the manual remedy, as Context explains.

3. **Preconditions.** The refresh runs only when all of these hold. Otherwise it
   skips and logs one line saying which one failed.
   - The helper passed its sanity probe (`_CERT_HELPER_OK`).
   - `generate_tls_cert` is `true`.
   - Not Jupyter launch mode: `/.launch` exists and contains `jupyter`. This is
     judged from `/.launch` alone. `JUPYTER_OVERRIDE` does not change it, because
     the platform's certificate step runs in that mode either way.
   - `CONTAINER_API_KEY` and the container id are set.
   - The off switch is not set (decision 9).
   - `cert-usable` exits 0 on the pair on disk.
   - The certificate's fingerprint matches the provenance marker.

4. **One read of the live address.** `GET /api/v0/instances/<id>/`, a single
   attempt with a short limit (`--max-time 5`, no retry). The API key goes to
   curl through a header read from stdin or a 0600 file, never in argv (where any
   process can read it), and never under `set -x`. Only `public_ipaddr` is
   extracted. The response body is never logged, because it can carry the
   tenant's environment. Any failure (no response, non-200, a null or empty
   value, anything that is not an IPv4 literal) means "unknown": keep the pair,
   change no marker.

5. **The comparison.** The certificate's IPv4 SAN entries are read with
   `openssl x509 -noout -ext subjectAltName`. If the live address is among them,
   nothing happens. If the certificate has no IPv4 SAN, the refresh skips and
   logs. Only IPv4 is compared.

6. **A bound per address.** `/etc/.instance-cert-ip-refresh` records the live
   address last attempted and an attempt count. A refresh runs only while the
   count for that address is below `_CERT_RETRY_LIMIT` (3, the existing
   self-sign retry limit). The count is written before the request, so every
   attempt counts, whatever its outcome. A new live address resets it. If the
   signer keeps naming an address the API does not report, the instance makes at
   most three signing requests for that address and then stops.

7. **Re-sign with the existing key, keep the old pair on any failure.**
   - Build a CSR from `/etc/instance.key` (`openssl req -new -key`) into a temp
     file.
   - Wait a few random seconds, so containers booting together on one host
     spread out against the shared rate limit.
   - POST once, with no curl retries, to the same signing endpoint, into a temp
     file.
   - Install it only if `cert-usable` exits 0 against the existing key **and**
     the live address is among its IPv4 SANs. Then `mv` it over
     `/etc/instance.crt`, `chmod 644`, and update the provenance marker.
   - On any failure, discard the temp files. The old pair stays, the self-signed
     marker is untouched, and nothing self-signs.

8. **One log line per decision**, naming the old SAN, the live address and the
   outcome: matched, refreshed, skipped and why, failed and why, bound reached.
   It never includes the key or the response body.

9. **An off switch.** `CERT_IP_REFRESH=false` disables the refresh without
   disabling certificate generation. `generate_tls_cert` / `--no-cert-gen` is
   too coarse for that job.

10. **A QA warning, not a failure.** `base/27-caddy-tls.sh` warns when the
    certificate carries the provenance marker but its SAN does not contain the
    live address. It skips when the address cannot be read. It warns rather than
    fails because the address the console signs is the console's behaviour, not
    the image's. It would have surfaced cause 1 as a warning on a QA cell instead
    of a customer report.

11. **`cert-usable` stays network-free.** A unit test asserts the helper invokes
    no network tool, so option C cannot creep in later.

## Binding conditions

1. **The container harness is the gate** (ADR 0026, binding condition 1). The
   curl shim in `tls-cert-gen-harness.sh` must dispatch on URL (the instance GET
   versus the signing POST) and sign with a fake CA that writes an IP SAN. Today
   its `good` mode self-signs with no SAN, so a console certificate and the
   self-signed fallback cannot be told apart there. The scenarios:
   - Live address in the SAN: no POST, pair unchanged.
   - Live address differs: one POST, key unchanged, new SAN, and stable on the
     next boot.
   - Signer keeps returning the old address: three POSTs for that address, then
     none, key unchanged throughout.
   - API unreachable, 401, 404/410, null or empty, IPv6, or garbage: no POST,
     pair byte-identical.
   - Signing request rejected (429, or no response): old pair kept, self-signed
     marker absent, HTTPS on.
   - Customer pair (no provenance marker), including one with only an IP SAN:
     no GET, no POST, byte-identical.
   - Self-signed fallback: no GET. The existing retry behaviour is unchanged.
   - Jupyter launch mode (`/.launch` containing `jupyter`, with and without
     `JUPYTER_OVERRIDE`): no GET.
   - `CONTAINER_API_KEY` unset, `generate_tls_cert=false`, or
     `CERT_IP_REFRESH=false`: no GET.
2. **Every guard is mutation-proven** (ADR 0026, binding condition 2). Each
   mutation must turn a named scenario red:
   - removing the provenance check;
   - removing the per-address bound;
   - removing the SAN check before install;
   - removing key retention;
   - removing keep-on-failure;
   - removing the Jupyter-mode skip.
3. **The signing fix is confirmed live first.** Before base is promoted, a fresh
   signing request on a live NAT'd host must return a SAN equal to the API's
   `public_ipaddr`, and the instance key must get a 200 from the instance GET. If
   the two can still disagree, decision 6 bounds the damage, but the feature
   would do nothing useful.
4. **Base is promoted from the branch before merge**, per the repo's promotion
   rule. Derivatives receive it only through their next base-pin bump.
5. **The runbook carries the remedy for certificates already issued.** The
   remedy is to remove `/etc/instance.crt`, `/etc/instance.key` and
   `/etc/.instance-cert-selfsigned`, then restart. The runbook must say to check
   `generate_tls_cert` first: with it off, removing the pair leaves no
   certificate and HTTPS turns off. In Jupyter launch mode a restart alone
   suffices.
6. **`docs/invariants.md` is updated with the build.** Under "One TLS
   cert-usability predicate" it records:
   - environment-dependent checks (the address) belong to the boot script, never
     to `cert-usable`;
   - the refresh touches only provenance-marked pairs and never removes a
     working pair;
   - refresh attempts are bounded per observed address;
   - `PUBLIC_IPADDR` is a creation-time snapshot, re-applied by `10-prep-env.sh`
     on every boot.

   L066 is unchanged. SAN extraction is the "generic openssl use stays legal"
   case its rule text already names.

## Consequences

Positive:

- A console certificate on an instance from a new image follows a machine
  address change, at the next boot, without operator action.
- A certificate signed for the wrong address is repaired on such an instance the
  next time it boots after the signing side is corrected.
- Customer certificates, the self-signed fallback and the platform's Jupyter-mode
  certificates are untouched by construction, not by heuristic.
- Every failure leaves TLS exactly as it was.

Accepted negatives:

- **New boot-time console traffic.** Every boot of an instance holding a
  provenance-marked certificate makes one read-only GET. The rate limit on that
  endpoint is per instance, unlike the signer's. Signing traffic stays bounded
  at no more than three requests per address change.
- **A boot-time dependency on the v0 instance endpoint.** The v0 instance API
  family is being deprecated for new accounts. Today that applies only to the
  bulk list, and no v1 single-instance route exists yet. If the GET starts
  returning 404 or 410, the refresh silently becomes a no-op, which is safe but
  loses the feature. The harness covers that mode, so it fails visibly in tests
  rather than in the fleet.
- **A change during uptime waits for the next boot.** Stage 55 runs before
  supervisor, and nothing re-checks a running instance.
- **Hosts with dynamic addresses re-sign on every change.** That is correct and
  bounded by real events, but it shows as a new certificate in the logs. The
  runbook says so.
- **Existing instances are not reached.** This ADR does not fix certificates
  already issued in the cause-1 period, on any image built before it.

## What would reverse this

- **The signer and the API persistently disagree** about the address. The bound
  keeps that harmless, but the feature becomes dead weight and should be
  removed.
- **The per-boot GET proves unacceptable load.** Then the check moves to every
  Nth boot, or to certificates past a certain age, as a recorded amendment.
- **The single-instance v0 GET is removed.** Then the refresh moves to its
  successor route, or is withdrawn.
- **The platform takes over re-signing in every launch mode**, as it already
  does in Jupyter launch mode. Then this step duplicates platform-owned work and
  should be deleted, for the same reason it is skipped in Jupyter mode today.
