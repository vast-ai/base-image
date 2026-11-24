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
1. **The distrust lands first.** `lib/distrusted-roots.txt` is the one list of roots the image
   must not trust (name and SHA-256). `lib/assert-distrust.sh` fails unless the OS store has
   dropped every one, checking both the per-root file and the bundle's content. The base
   Dockerfile runs it right after its `apt-get upgrade` and `convert-non-vast-image.sh` right
   after its apt install, before either fetches anything else over TLS.
2. **Every bundle we ship is current at build time.** Current certifi and pip go wherever we
   create or touch an interpreter, including the upstream engine's, before `venv-mirror build`
   so the mirror links the new files. The engine is skipped where its certifi or pip already
   reads the OS store (a Debian/Ubuntu `python3-certifi`/`python3-pip`), because a PyPI copy
   would shadow that and stop it following apt. Conda's CA packages are current and its package
   cache is emptied. Our own uv is installed, and the build fails unless it is the uv on PATH
   and at least 0.13.4.
3. **CA data that does not follow the OS store is refreshed last.** `lib/refresh-ca-data.sh`
   links every grpcio `roots.pem` to the OS bundle, replaces every stale `ensurepip` pip wheel
   with a hash-pinned current one, and upgrades every installed pip below the floor from that
   same wheel (interpreters below the wheel's `Requires-Python` are left alone; Debian's own pip
   is skipped). The last is not redundant with conda: conda-forge builds pip 26.2.2 only for
   Python 3.11 and later, so `conda install pip` leaves a 3.10 env on 26.2.1 and succeeds. The base and convert builds run it as their last install step. Any later build
   that installs Python packages or interpreters can bring a stale copy back, so derivatives
   run it as their last step too, introduced with their next base pin bump.
4. **The base image's system-Python certifi follows the OS store.** It is a PyPI certifi that
   shadows Debian's, for jupyter, tensorboard and the other system tools, so its bundle is a
   link to the OS bundle.
5. **Our own services follow the OS store.** `lib/trust-env.sh` sets `SSL_CERT_FILE`,
   `REQUESTS_CA_BUNDLE` and `UV_SYSTEM_CERTS` as defaults (a value the template or user set
   wins). Only the services we own source it: the portal, tunnel manager, provisioner,
   `update-portal` and model-ui. It is never in `/etc/environment`. One deliberate exception:
   the provisioner runs the user's provisioning script and manifest installs as its children,
   so they inherit these defaults. A user who set their own value keeps it; one who added a
   private CA to certifi instead must set `REQUESTS_CA_BUNDLE` for provisioning. `PIP_CERT`
   is not set, because pip only adds it to its vendored certifi.
6. **Node is a fixed release.** Node's trust only moves with a Node release.
   `lib/node-trust-floor.txt` lists the first fixed release on each line (22.23.4, 24.22.1,
   26.12.1); the base build fails if the Node it installs is below it, or on a line not listed.

## Binding conditions
- `tests/base/29-ca-trust.sh` passes on the image before it is promoted. It is a required test
  in the base and pytorch promotion gates, so a skip fails the cell. It verifies the published
  chains through the compromised intermediates against every CA bundle found by content and
  against the OS directory store, after a positive control at a fixed check time, and checks
  bundled pip wheels, Node and uv against their floors. It fails rather than passes when it
  cannot run a check.
- The root list, the Node floor and the trust helper each have one source; the linter keeps the
  copies that cannot read them (the CI runner step) and the services that must source them in
  line.
- A floor or list changes when the next distrust ships; the build and the test read the same file.
- A derivative pinned to a base older than a CA-trust fix may overlay the current
  `refresh-ca-data.sh`, `pip-trust-floor.env` and `29-ca-trust.sh` until its base is rebuilt. Each
  copy is byte-identical to the base's (L115) and is deleted when the image moves to a base that
  ships the same files.

## Consequences
- The next OS-level distrust reaches our services and the system tools with an `apt-get
  upgrade`, without waiting for a pin bump. Node still needs a Node release, which the build
  floor forces us to take.
- Node versions a user installs with nvm are theirs to keep current. Bun and Electron ship their
  own fixes.
- Upstream CA files we cannot safely replace (botocore's bundle; botocore uses certifi when it is
  importable) are reported by the test on a dated allowlist.
- The line between "we fix it" and "upstream owns it" is the runtime that runs, not who shipped
  the file. An external image's engine environment is upstream's, but users and our services run
  it, so its certifi and pip are upgraded (decision 2). A vendor tool's private bundle that is not
  on PATH and that nothing of ours runs (NVIDIA Nsight Compute's embedded Python under
  `/opt/nvidia`, from the CUDA base image) is left to the vendor and reported on the dated
  allowlist, because patching a profiler's bundled runtime cannot be tested here.
- Every image carries the public test chains (about 60 KB) under `tests/ca-trust/`.

## What would reverse this
Node gaining a way to follow the OS store's distrust without changing its trust semantics for
users, at which point the version floor could give way to that.

## Amendment 2026-11-20: the in-image test is withdrawn

This replaces the first binding condition above (the test as a required promotion gate), the
overlay condition's mention of `29-ca-trust.sh`, and the two consequences that speak of a dated
allowlist and of test chains in every image. Decisions 1 to 6 stand: the build still applies
every fix and fails when it cannot.

### What happened
`tests/base/29-ca-trust.sh` searched each image for CA data the build had not fixed and failed
the promotion on any it found. Files the image could not fix were on a list of exemptions, each
with a review date after which the test failed. On 2026-11-06 the first date passed: the
exemption for the interpreter the serverless worker bootstrap installs at boot. Every serverless
QA cell failed for ten days. Nothing about that interpreter had changed, and moving the date
needed a base rebuild, a promotion and a pin bump in every derivative. Six more rows were due on
2026-12-31, for every image at once.

### Decision
7. **The test and its fixtures are removed**, and `base/29-ca-trust` leaves the required-test
   lists of the base and pytorch promotion gates. Rule L112 is retired.
8. **The image installs current, patched software and ships.** Decisions 1 to 6 are how. The
   image's own test suite does not audit the image for copies those steps missed.
9. **Detection moves out of the image.** A vulnerability scan over the published images is the
   place to find what an image still carries. It is separate work and is not built here.
10. **The image does not carry the serverless worker's Python.** The step that installed it at
    build, added so the refresh would cover it, is removed. What the bootstrap installs at boot
    belongs to the bootstrap's repository.

### Options considered
- **Move the dates.** Rejected: a full rollout each time, to keep a calendar that fixes nothing.
- **Keep the test, drop the dates, fix every former row at build, and report the exemptions that
  remain on every run.** Built on a branch and reviewed. It worked, and it needed two more lint
  rules, per-image repairs for upstream engine environments, a replacement of a third party's
  bundled Node, an exemption matcher with its own tests, and reporting in the QA gate. Each
  review round found a further case. Rejected: that is a standing cost, and several new ways
  for a build to fail, for one class of issue.
- **Keep the test as a report that never fails.** Rejected: it still has to be maintained
  against every image, and a report nobody is forced to read is what the vulnerability scan is
  for.

### Consequences
- Between the build's fixes and a future scan, nothing tells us that an image carries a stale CA
  bundle the build did not reach (an upstream engine's seed wheels, a vendor tool's private
  Python, an interpreter installed at boot). That gap is accepted.
- Images already built keep the test they were built with. A derivative takes it from its dated
  base, so one still pinned to a base that has the test fails on any rebuild from 2027-01-01,
  when the last of the old rows expires. The row for the worker's interpreter has already
  expired, so a serverless image fails on such a base today. The pins in this repository moved to
  the 2026-11-20 bases, which do not ship the test, in the same change that removed it; the two
  serverless derivatives (llama.cpp and ComfyUI) were rebuilt on them first. The aio-studio base
  is the one pin still on an older image and is rebuilt before that date. A derivative that has
  to stay on an older base deletes the file in its own Dockerfile.
- The QA gate still parses and reports exemption lines. With no test printing them it reports
  nothing; removing it is follow-up work.

### What would reverse this
A scan that cannot see what the in-image test saw, and a distrust event where that gap mattered.
