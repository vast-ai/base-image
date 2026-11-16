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

## Amendment 2026-11-16 — exemptions are fixed at build or stand without a date

This replaces "reported by the test on a dated allowlist" in the Consequences above, in both
places it appears. The decisions and the other binding conditions are unchanged.

### What happened
The test's exemptions each carried a review date, and the test failed once a date passed. On
2026-11-06 the first one did: the exemption for the Python 3.10 that the serverless worker
bootstrap installs at boot. Every serverless QA cell then failed for ten days. Nothing about
the exempted interpreter had changed, and the one action the date allowed was to move it,
which needs a base rebuild, a promotion and a pin bump in every derivative. Six more rows were
due on 2026-12-31, for every image at once.

A review of the list found the larger fault. Most rows broke this ADR's own rule, that the
line between "we fix it" and "upstream owns it" is the runtime that runs. They were marked
"left to upstream" for files in environments that users and our services run.

### Decision
7. **The image fixes what it can at build.** Each former row is fixed, with one exception
   (the table below):
   - botocore's bundle becomes a link to the OS bundle, in `lib/refresh-ca-data.sh`.
   - An engine environment's own `uv` and its `virtualenv` seed wheels are brought to the
     floor in `convert-non-vast-image.sh`, before `venv-mirror build`.
   - The Node that open-webui's Playwright driver bundles is replaced in that image's
     Dockerfile with a fixed release on the same major line.
   - Every image installs the interpreter the serverless worker bootstrap asks for
     (`lib/worker-python.sh`: uv's managed Python 3.10), before the refresh. The bootstrap
     then reuses it and nothing below the floor arrives at boot.
8. **The two Debian/Ubuntu files are not exemptions.** `python3-certifi` and `python3-pip`
   are patched to read the OS bundle. The test reports their unread copies as checked and
   unread, on two conditions it verifies: dpkg owns the file, and the sibling `core.py`
   returns the OS bundle. Otherwise the file is judged like any other.
9. **An exemption that remains has no date.** It is a row in `29-ca-trust.sh` with an id, the
   checks it may excuse, a path pattern, a condition the test verifies, and a reason. A
   pattern's `*` does not cross a path separator. A trailing `**` is allowed only on a row
   that has a condition. A malformed row or an unknown condition fails the test.
10. **Every applied exemption is reported, on every run.** The test prints one `EXEMPT:` line
    per file and states the count on its PASS line. The QA gate turns those lines into a
    warning per cell and a table in the run summary. It never changes the verdict.
11. **One exemption should never apply, and says so when it does.** With decision 7 the
    `worker-bootstrap` row is a fallback. Each engine's serverless worker test checks, after
    the worker is up, which interpreter the worker environment uses. If the bootstrap
    installed its own, that check prints the exemption line and the gate posts to Slack.

### Standing exemptions
A row may be added only with an entry here that says why the image cannot fix the file. "Left
to upstream" is not a reason for a runtime that users or our services run. The linter pairs
this table with the rows in the test (L127).

| Id | What it covers | Why the image does not fix it |
|---|---|---|
| `nsight-private-python` | The pip wheel and pip's vendored CA bundle in the Python that NVIDIA Nsight Compute embeds under `/opt/nvidia`, on CUDA base images that ship one. Applies only to files dpkg owns. | It belongs to a vendor profiler, is not on PATH, and nothing of ours runs it. Patching a profiler's bundled runtime cannot be tested here. |
| `worker-bootstrap` | The pip wheel and bundle in a uv-managed Python 3.10 that appeared after the image was built, on a serverless instance. | The bootstrap that would install it lives in another repository. Decision 7 removes the need for it; this row keeps a change there from blocking image publishing, and decision 11 makes that change loud. |

### Options considered
- **Keep the dates and extend them.** Rejected: the date pressed on image publishing, which
  cannot fix what the row exempts. Extending it costs a full rollout each time.
- **Keep dates in the repository only, checked on pull requests.** Rejected: it keeps a
  calendar as the trigger. What changes an exemption is a fix or a change upstream, and
  neither follows a calendar.
- **Deliver the exemption list from the QA client at run time.** Rejected: an image's verdict
  would depend on which client ran it, and a customer who runs the suite has no client. It
  also only made the date cheaper to move.
- **Drop the dates and change nothing else.** Rejected: most rows were fixable, and an
  undated list of fixable rows makes a defect permanent. A bare `*` in a bash pattern also
  crosses path separators, so an undated wildcard would cover files nobody reviewed.
- **Fail in the image when an exemption no longer matches anything.** Rejected: the list is
  the same on every image and most rows match nothing on most images.

### Binding conditions added
- `29-ca-trust.sh` carries no date and no expiry logic, and every row id is in the table above
  (L127).
- `lib/worker-python.sh` runs before the refresh in the base build, the extend build and the
  convert script (L128).
- A build step that fixes a runtime proves it at build: the step fails the build unless the
  fixed runtime still starts.

### Consequences
- A cell that passes by an exemption no longer reads like a clean pass.
- No date makes an image fail. The pressure to remove a row is its warning on every run, and
  for `worker-bootstrap` a Slack message.
- Every image grows by one Python 3.10 interpreter, about 100 MB.
- Python 3.10 is end-of-life. When a trust floor needs a pip that no longer supports it, the
  pre-installed interpreter fails the test and no row covers it. The lasting fix is for the
  bootstrap to use the image's interpreter or a supported Python. That is a change in the
  bootstrap's repository, proposed separately.
- On a serverless instance, a Python 3.10 that a provisioning script installs under uv's
  Python directory is also covered by `worker-bootstrap`. It is reported, not silent.
- The pre-install relies on the bootstrap asking for 3.10 with the image's `uv` in uv's
  default Python directory. Decision 11 is what detects a change.
- Images built before this amendment keep the dated list until they are rebuilt. The gate
  reports their rows with the date they fail from.

### What would reverse this
The bootstrap using the image's interpreter removes the `worker-bootstrap` row and the
pre-install. NVIDIA shipping a current pip in Nsight removes the other row.
