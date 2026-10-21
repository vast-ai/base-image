# ADR 0057 — Caddy is built from upstream with a build-time patch

- **Status:** Accepted
- **Date:** 2026-10-21
- **Decision owner:** Rob Ballantyne

## Context
Every image's front door is the Caddy we build in the base Dockerfile's `caddy_builder` stage.
It served TLS, token auth and the single-port HTTP-to-HTTPS redirect from a fork,
`ai-dock/caddy@httpredirect`, built with `golang:1.23.4` and `xcaddy@latest`. The fork's only
change was one line in `modules/caddyhttp/httpredirectlistener.go`: answer plain HTTP on the TLS
port with `307 Temporary Redirect` instead of upstream's `308 Permanent Redirect`. The fork had not
moved since January 2025, and its branch name was the build input, so a rebuild could never pick up
an upstream fix. govulncheck reported 117 reachable vulnerabilities in the resulting binary
(net/http, crypto/tls, crypto/x509, x/net, x/crypto, quic-go, grpc).

The 307 matters on Vast. Browsers cache a 308 for the origin indefinitely. Vast reuses ip:port
pairs across instances and tenants, and HTTPS can be switched off, so a cached 308 would send the
next tenant on that port to an HTTPS endpoint that is not there. Upstream v2.11.7 still hardcodes
308 and offers no option for it.

## Options considered
- **Keep the fork and rebase it.** Rejected: a fork whose only content is one line is a
  maintenance liability, and a branch name as a build input is unpinned by construction.
- **Copy upstream's redirect listener into our own module** (registered under another name).
  Rejected: about 170 lines of Go we would own, which would silently miss upstream fixes to that
  listener.
- **Use upstream's 308.** Rejected for the caching reason above.
- **Build upstream at a pinned release and patch the line at build time.** Chosen.

## Decision
`caddy_builder` clones `caddyserver/caddy` at a pinned release tag, checks the checkout against a
pinned commit SHA, replaces the 308 with a 307 in `httpredirectlistener.go`, and fails the build
unless the file then contains the 307 status and no 308. It builds with a pinned Go image (by
digest), a pinned xcaddy version and a pinned `replace-response` commit. Dependencies whose fixed
versions Caddy does not yet require are raised with `--replace` (currently `golang.org/x/net`). The
clone is checked by SHA because the Go image's own CA store is not ours to keep current; Go modules
are checked against sumdb.

## Binding conditions
- The patch check stays: a Caddy release that moves or rewrites that code fails the build rather
  than shipping a 308.
- Every build input is pinned; bumping one is a deliberate change.

## Consequences
- Caddy is current upstream. govulncheck on the built binaries (amd64 and arm64) reports one
  reachable vulnerability, which has no fix available yet; it is reviewed when one ships.
- Upgrading Caddy is a tag-and-SHA bump plus the existing checks.
- The `ai-dock/caddy` fork is no longer used.

## What would reverse this
Upstream offering a configurable redirect status, at which point the patch is dropped and the
option set in the generated config instead.
