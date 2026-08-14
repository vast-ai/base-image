# Runbook — instance TLS certificates

The instance TLS pair lives at `/etc/instance.crt` and `/etc/instance.key`, both
persisted in the container overlay (they survive stop/start, lost only on
destroy). `ROOT/etc/vast_boot.d/55-tls-cert-gen.sh` generates them at boot, has
the Vast console sign the CSR, and self-signs if the console is unreachable. The
usability predicate is `/opt/instance-tools/bin/cert-usable` (exit `0` usable,
`3` matched-but-expired, `1` unusable). See ADR 0026.

## Symptom: a customer reports "HTTPS is broken" / browser shows a date error

An expired-but-matched pair is now **served** rather than replaced when the boot
script declined to regenerate (`generate_tls_cert` is not `true`, or the helper
is missing). The alternative is plaintext on the same public port carrying the
portal auth token in `?token=`, which is worse — but the browser still shows a
date error, and non-interactive clients (`curl` without `-k`) hard-fail.

Diagnose on the instance (Caddy logs only to stdout; there is no log file):

```
/opt/instance-tools/bin/cert-usable; echo $?     # 0 fine, 3 expired, 1 unusable
cat /etc/.instance-cert-selfsigned 2>/dev/null   # self-signed attempt count, if any
openssl x509 -in /etc/instance.crt -noout -enddate
```

**Check `generate_tls_cert` FIRST — the obvious fix makes it worse otherwise.**

```
grep -c 'generate_tls_cert.*true' /etc/environment; echo "env: ${generate_tls_cert:-unset}"
```

*If certificate generation is ENABLED*, regenerate and restart:

```
rm -f /etc/instance.crt /etc/instance.key /etc/.instance-cert-selfsigned
# then restart the instance (stop/start) so 55-tls-cert-gen.sh runs again
```

*If it is NOT enabled* — which is the common way to reach this state, and the
state the boot log's own message names — do **not** delete the pair. Nothing
regenerates it, the final guard sees no key, and `ENABLE_HTTPS=false`: you would
trade an expired-but-encrypting listener for plaintext on the public port with
the portal token in `?token=`, which is the downgrade ADR 0026 exists to
prevent. Either relaunch the instance with certificate generation enabled, or
self-sign in place against the existing key:

```
openssl x509 -req -in /etc/instance.csr -signkey /etc/instance.key \
    -days 365 -sha256 -out /etc/instance.crt && chmod 644 /etc/instance.crt
```

## Symptom: certificate is permanently self-signed after the cause cleared

`/etc/.instance-cert-selfsigned` is a persisted retry counter. Once it reaches
the limit (3), the boot script stops retrying the console and keeps the
self-signed cert for the instance's life — even after the original cause (blocked
egress to `console.vast.ai`, or a badly skewed host clock that made every
console-signed cert read as expired) has cleared. There is no automatic reset.

Fix — clear the marker and the pair, then restart:

```
rm -f /etc/.instance-cert-selfsigned /etc/instance.crt /etc/instance.key
# restart the instance
```

`base/27-caddy-tls.sh` already WARNs when the self-signed marker is present, so
the state is visible in a QA cell.

## Symptom: HTTPS is off entirely (plain HTTP)

`ENABLE_HTTPS=false` was exported by the boot script because there was no
servable pair: an empty/unreadable key, an unparseable cert, a missing helper, or
a syntactically broken helper (which exits `2`, and only `3` is tolerated). This
is fail-closed by design. Check the boot log for `is missing or not executable`
(broken image) and `cert-usable:` reasons, then regenerate as above.

## Rollback of a base promotion

Dispatch **Move Base Auto Tag** (`.github/workflows/move-base-auto-tag.yml`) to
point the `-auto` tag back at the previous production digest. This reaches only
**new** instances; instances already created keep the boot script they booted
with. Per-instance mitigation is the regenerate-and-restart above.
