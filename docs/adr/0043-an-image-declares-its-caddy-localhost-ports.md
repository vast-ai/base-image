# ADR 0043 — An image declares its Caddy localhost-rewrite ports

- **Status:** Accepted
- **Date:** 2026-09-17
- **Decision owner:** Rob Ballantyne
- **Supersedes:** the per-app stage of ADR 0042 (`05-wan2gp-env.sh`). ADR 0042's
  diagnosis, rejected alternatives and security consequences still stand.
- **Enforced by:** `tools/imagegen/tests/test_caddy_localhost_ports_sh.py`

## Context

ADR 0042 fixed Wan2GP behind Caddy with a boot stage that adds Wan2GP's port to
`CADDY_HEADER_UP_LOCALHOST`. That variable makes Caddy send `Origin:
{forwarded_protocol}://localhost:<port>` to the listed internal ports. Caddy already sends
`Host: localhost:<port>` to every app, because it proxies to `localhost`. An app that
requires `Origin == scheme://Host` works behind the portal only with this rewrite.

Three facts made a per-app stage the wrong unit:

- **More than one app needs it.** The ACE Step UI (port 3000, upstream `ace-step-ui`'s
  Vite dev server) has needed the rewrite since before ADR 0042. Nothing in this repo
  supplied it. The launch templates for the standalone ace-step image and for aio-studio
  each set `CADDY_HEADER_UP_LOCALHOST: '3000'`. A template built from either image
  without that line silently loses it.

  The check is not Vite's. Vite proxies `/api` and the other backend paths to the Node
  backend on 3001 with `changeOrigin`, which rewrites Host but not Origin. The backend's
  `cors` origin callback then accepts only an Origin containing `localhost` or
  `127.0.0.1` (or a LAN address), and only while `NODE_ENV` is `development`, which is
  its default. Under any other `NODE_ENV` it requires an exact match with `FRONTEND_URL`,
  and this rewrite would not satisfy it. Read from the upstream source; not reproduced
  on an instance.
- **aio-studio runs both apps.** Under ADR 0042 it would carry two stages doing one job.
- **aio-studio was already broken.** CI resolves Wan2GP's latest commit regardless of
  the Dockerfile default, so the `2026-09-15` and `latest` promotions carry Wan2GP's
  origin check (ADR 0042, amendment).

## Options considered

1. **Copy the per-app stage once per app per image** — rejected. Four copies of the same
   logic, two of them in one image, free to drift independently.
2. **One generic stage per image, with an identical body and only the port list differing**
   — chosen. It makes the image, not the template, own the requirement. It ships with
   each image's next build and needs no base change. The copies are held identical by a
   test, not by care.
3. **A shared helper in the base image** — rejected for now. It is the natural home for a
   convention, but a base change ships only through a base promotion followed by a new
   base pin in every derivative. That is slow, and it is the riskiest path in this repo,
   for twenty lines of bash.
4. **The portal mechanism** — deferred, as in ADR 0042. It is tracked as portal backlog,
   specified as a rewrite applied only when the browser's Origin matches the address it
   actually used. When it lands, every copy of this stage is deleted.
5. **Leave it in the templates** — rejected. It is a property of the image, and a
   template the image cannot see is where it gets lost.

The strongest objection is that this entrenches the unconditional Origin rewrite, which
ADR 0042 and the portal backlog both describe as the wrong long-term shape. It does not
widen exposure: the ACE Step UI already had the rewrite through its templates, and
Wan2GP had it through ADR 0042. What changes is who owns the setting, not whether it is
applied. Option 4 removes it the same way whichever owner holds it.

## Decision

An image whose app needs the rewrite ships
`ROOT/etc/vast_boot.d/05-caddy-localhost-ports.sh`:

```bash
caddy_localhost_ports=(...)          # the only line that differs between images

# ---- shared body: identical in every image (ADR 0043) ----
...
# ---- end shared body ----
```

| Image | Declared ports |
|---|---|
| wan2gp | `"${WAN2GP_PORT:-7860}"` |
| ace-step | `3000` |
| aio-studio | `"${WAN2GP_PORT:-17861}" 3000` |

For each declared port, the body behaves as the ADR 0042 stage did:

- unset or empty → the variable is set to the port;
- a list without the port → the port is appended, and existing entries are kept;
- a list that already contains the port → unchanged, so a port listed twice appears once;
- `true`, in any case → the whole stage does nothing.

## Binding conditions

1. **The body is identical in every image,** and every image that ships the stage is
   covered by the test. A change to the body is a change to all of them. The test must
   run on the PR that makes such a change: `imagegen-tests.yml` triggers on derivative
   boot stages, launcher scripts and Dockerfiles for this reason.
2. **Each declared port is the port the image actually serves.** For Wan2GP that is the
   launcher's `${WAN2GP_PORT:-N}` default. The ACE Step UI's port is hardcoded upstream
   in `vite.config.ts` (`port: 3000`); `FRONTEND_PORT` in its `start.sh` is only echoed.
   Both Dockerfiles therefore fail the build if that line stops saying 3000. An image
   default `PORTAL_CONFIG` must route to the same internal port.
3. **The ports are internal ports** — the third field of a `PORTAL_CONFIG` entry.
4. **ADR 0042's conditions carry over:** `true` is never appended to, the parsing
   matches `caddy_config_manager.py`, and nothing is left in the sourced boot shell.

## Consequences

- The `CADDY_HEADER_UP_LOCALHOST` lines in the ace-step and aio-studio templates become
  redundant once templates point at images carrying the stage. Until then they are
  harmless: the stage finds the ports already listed.
- Adding an app with the same need is one edit to the port list, plus a row in the
  test's image table. The test fails if the stage appears in an image the table does
  not cover.
- The upstream ACE Step UI binds `0.0.0.0` in `vite.config.ts`. That is unrelated to
  this decision and is not addressed here.
- For the ACE Step UI the rewrite gives up very little. The backend's substring test
  already accepts an Origin such as `http://localhost.example.com`.
- **Evidence, stated precisely.** The live fix was first proven by setting the variable
  by hand. CI then built and a live instance confirmed the ADR 0042 per-app stage. The
  generic stage in this ADR has the same logic and is unit-tested against the real
  files, but it had not been built or booted in any image when this ADR was written.
  Each image needs a build and one boot showing the merged value in `/etc/environment`
  before its promotion is trusted.
- **Where the value can still be lost.** `$WORKSPACE/.env` is sourced after the stage,
  both at boot and by `caddy.sh`, so a `CADDY_HEADER_UP_LOCALHOST` line there replaces
  the merged value outright. That file is the user's, and is not rewritten.
- **The generator that parses the value is not always the one the test reads.** First
  boot replaces the portal with its latest release (ADR 0015), so a later portal that
  parses the variable differently runs under images whose stage is already baked. The
  test fails on the PR that changes the parser in this repo; the portal release that
  follows must keep accepting what shipped images write.
- **The build assertion is a tripwire, not a proof.** It matches a `port: 3000,` line
  anywhere in `vite.config.ts`, and Vite without `strictPort` moves to another port if
  3000 is taken. It catches the likely upstream change, not every one.

## What would reverse this

- The portal gains the same-origin-only rewrite as its default. Delete the stage from
  every image.
- A fourth image needs the stage, or the body needs a real change. Reconsider option 3.
- Upstream Wan2GP or ace-step-ui stops requiring the rewrite. Drop that port from the
  lists.
