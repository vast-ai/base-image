# ADR 0058 — CA trust follows the OS store

- **Status:** Accepted
- **Date:** 2026-10-21
- **Decision owner:** Rob Ballantyne

## Context
In October 2026 the private keys of eight eMudhra intermediate CAs were found to be recoverable.
Ubuntu, Debian, certifi, pip, uv, Go and Electron shipped distrust updates within days; Node
released its fix (nodejs/node#66871, which blocks the eight intermediates by public key) on
2026-10-21 in 22.23.4, 24.22.1 and 26.12.1. An image carries many CA stores besides the OS one, and each went
stale on its own schedule:

- certifi in every venv we create, pinned in the portal's requirements;
- pip's vendored certifi, in each interpreter's pip. pip loads it IN ADDITION to the system store
  and to any bundle it is given (`--cert`, `PIP_CERT`, `REQUESTS_CA_BUNDLE` only add), so for pip
  only a newer pip helps;
- the pip wheel that `ensurepip` bundles inside each interpreter, which `python -m venv` installs
  into every new venv (uv-managed Pythons, Miniforge, the conda-based `/venv/main`). Debian and
  Ubuntu's own pip reads only the OS store and is not affected;
- conda's `ca-certificates`/`certifi` packages, and copies of them in the package cache;
- grpcio's bundled `roots.pem` (installed with tensorboard), which ignores `SSL_CERT_FILE`;
- Node's compiled-in roots;
- uv's compiled-in roots;
- in external images, the same set inside the upstream engine's interpreter.

The OS store is the one store an `apt-get upgrade` keeps current. Everything else waits for a
rebuild, a pin bump or an upstream release.

## Options considered
- **Point everything at the OS store image-wide** (`SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`,
  `PIP_CERT` in the launch environment). Rejected: ADR 0052 writes the launch environment
  literally for every user process, so this would override users' certifi choices, conda's own
  OpenSSL and private-CA setups.
- **Only upgrade the bundles at build time.** Necessary, but on its own it leaves our services
  trusting whatever certifi they were built with until the next rebuild.
- **`--use-system-ca` / `NODE_USE_SYSTEM_CA=1` for Node.** Rejected: tested on v24.21.0, it ADDS
  the OS store to Node's compiled-in roots, so a root the OS has distrusted stays trusted.
- **`NODE_OPTIONS=--use-openssl-ca` image-wide**, so Node uses the OS store instead of its own
  roots. Tested on v24.21.0 and adopted while Node had no released fix, then dropped once the
  fixed releases shipped: it changes Node's trust semantics for every user process, a user's own
  `NODE_OPTIONS` silently replaces it, and Node's own fix blocks the intermediates whichever root
  a chain reaches, including through a user-supplied bundle.

## Decision
1. **Every bundle we ship is current at build time.** The base Dockerfile and
   `convert-non-vast-image.sh` assert that the distro's distrust has landed before anything else
   is fetched; install current certifi and pip wherever we create or touch an interpreter,
   including the upstream engine's (before `venv-mirror build`, so the mirror links the new
   files); install current conda CA packages and empty conda's package cache; and install our
   own uv even when the upstream image ships one; and replace every stale bundled `ensurepip`
   pip wheel with a hash-pinned current one (`lib/refresh-bundled-pip.sh`).
2. **grpcio's `roots.pem` is a link to the OS bundle**, the approach Debian takes for certifi.
3. **Our own services follow the OS store.** `/opt/instance-tools/lib/trust-env.sh` sets
   `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` and `UV_SYSTEM_CERTS` as defaults (a value the
   template or user set wins). Only the services we own source it: the portal, tunnel manager,
   provisioner, `update-portal` and model-ui. It is never in `/etc/environment`.
4. **Node is a fixed release.** Node's trust only moves with a Node release, so the base build
   asserts that the Node it installs is at or above the first release that blocks the
   compromised intermediates on its line (22.23.4, 24.22.1, 26.12.1). A line without such a
   release fails the build.

## Binding conditions
- An image passes the trust probe before it is promoted. The probe finds bundles by content,
  checks the OS directory store, verifies Node by handshake, runs a positive control at a fixed
  check time, and fails rather than passes when it cannot run a check.
- The Node floor is raised when Node ships the next trust fix; the probe checks the same floor.

## Consequences
- The next OS-level distrust reaches our services with an `apt-get upgrade`, without waiting for
  a pin bump. Node still needs a Node release, which the build floor forces us to take.
- Node versions a user installs with nvm are theirs to keep current. Bun and Electron ship their
  own fixes.
- Upstream CA files we cannot safely replace (botocore's bundle; botocore uses certifi when it is
  importable) are reported by the probe on a dated allowlist.

## What would reverse this
Node gaining a way to follow the OS store's distrust without changing its trust semantics for
users, at which point the version floor could give way to that.
