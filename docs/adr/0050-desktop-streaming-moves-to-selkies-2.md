# ADR 0050 — Desktop streaming moves to Selkies 2.0

- **Status:** Accepted
- **Date:** 2026-10-02
- **Decision owner:** Rob Ballantyne
- **Amends:** ADR 0027, for this artifact only: Selkies is pinned to a version and checksum,
  not fetched from `releases/latest`.
- **Affects:** `derivatives/linux-desktop`, and the aio-studio base
  (`derivatives/pytorch/derivatives/aio-studio/Dockerfile.base`, `ROOT_BASE/`) plus the
  aio-studio app layer that boots it.

## Context

Both desktop images streamed with selkies-gstreamer 1.x, fetched from `releases/latest`.
When Selkies 2.0.0 came out (2026-09-23), upstream renamed the repository to
`selkies-project/selkies` and deleted every 1.x release. 1.x is gone from PyPI too, after a
default-credentials advisory against 1.5.2–1.6.2. Both images now fail to build. A
dispatched aio-studio-base build hit the 404 on 2026-09-24. aio-studio itself is broken
separately, and its 2026-10-01 scheduled build failed on an unrelated AI Toolkit pin.

2.0 is a rewrite, not an upgrade:

- **Packaging:** one `.deb` per distro and arch (`selkies-<v>-ubuntu24.04-{amd64,arm64}.deb`).
  It carries its own venv at `/opt/selkies` (on the system Python 3.12), the web client, and
  the interposers. There is no GStreamer.
- **Encoding:** capture and encode go through `pixelflux`. `h264enc` (the default; 1.x
  `x264enc` is now an alias for it) means NVENC where it opens and x264 otherwise. VA-API is
  used only on Intel and AMD, so the `nvidia-vaapi-driver` source build is dead weight.
- **Transport:** WebSockets by default over the single HTTP port, with no STUN or TURN.
  WebRTC is opt-in and needs TURN.
- **Defaults:** loopback bind by default. Basic auth is on by default, and Selkies refuses to
  start without a password unless `--enable-basic-auth=false`.

A design review (a critical pass and two competing designs) and a live check on two Vast
instances established the facts this decision rests on:

1. **The WebSockets client requires a secure context.** It decodes with WebCodecs, and
   `runPreflightChecks()` stops with "This application requires a secure connection (HTTPS)"
   otherwise. Live:
   - plain HTTP shows that error;
   - Caddy HTTPS, a Cloudflare quick tunnel, and `http://localhost` (an SSH port-forward)
     all stream;
   - the WebRTC client loads over plain HTTP.

   The image serves plain HTTP whenever `ENABLE_HTTPS` is false or no usable certificate
   exists (ADR 0026, 0049). Tunnels are not guaranteed, and forcing HTTPS on a user who has
   not installed the console certificate shows a browser warning the platform considers too
   alarming to impose.
2. **Caddy's Host rewrite breaks Selkies.** Caddy sends `Host: localhost:<port>` to every
   app. Selkies compares the browser's `Origin` with `Host` and rejected every browser
   WebSocket (`Rejected WebSocket upgrade from disallowed Origin`). With the Host line
   removed for the Desktop route, streaming works. A foreign `Origin` carrying valid Basic
   credentials gets `403` from Selkies, and a request with no credentials gets `401` from
   Caddy. With the ADR 0043 Origin rewrite instead, the same foreign request gets `101`: the
   origin defence is gone.
3. **Subset-GPU rentals break NVENC, and upstream's fix does not engage on Vast.** On a
   container given 1 of 4 GPUs (driver 580):
   - `/proc/driver/nvidia/gpus` lists all 4 GPUs, and every `/dev/nvidiaN` exists;
   - the unallocated nodes fail `open()` with `EPERM`, because the device cgroup refuses
     them;
   - pixelflux's hidden-GPU filter counts nodes that exist (`access(F_OK)`), sees 4 = 4,
     never installs, and NVENC fails. ffmpeg's `h264_nvenc` fails the same way, so the
     cause is the driver bug (nvidia-container-toolkit#1249).

   Deleting the unopenable nodes and running the encoder unprivileged makes the filter keep
   1 GPU. A live browser stream then ran `NVENC H264 ... RTX 3060`, 1 session at 59 fps.
   But any root process that loads the NVIDIA libraries re-creates the nodes. The portal
   runs as root and polls `nvidia-smi` through GPUtil while open, and users run
   `nvidia-smi` as root. Because the keep test runs on every enumeration, the next session
   then drops to x264.

   A full allocation (1 of 1, driver 595) opens NVENC with no help. A100, H100, and H200 have
   no NVENC at all.
4. **TCP and UDP 73478 map to different ports.** The ≥70000 1:1 directive maps each one to
   its own random port (for example 21345/tcp and 21361/udp). 1.x advertised the TCP number
   with protocol `udp`.
5. **With no TURN configured, WebRTC silently uses Metered's public Open Relay.** It logged
   `Using short-term shared secret HMAC for TURN credentials`. An empty `SELKIES_TURN_*`
   value falls back to that default; only the higher-priority RTC config file overrides it.

## Options considered

**Transport**

1. **WebSockets always, WebRTC opt-in.** This is upstream's default and the original
   proposal. Rejected: every plain-HTTP user gets an error page where 1.x worked.
2. **Always route the Desktop link to HTTPS or a tunnel.** Rejected: tunnels are not
   guaranteed, and forcing HTTPS without the installed certificate shows a warning judged
   too alarming.
3. **Choose the transport from the effective `ENABLE_HTTPS` at launch.** Chosen, see
   Decision.
4. **Upstream JPEG fallback on insecure contexts.** The WebSockets client already pins JPEG
   (decoded with `createImageBitmap`, which works on plain HTTP) when WebCodecs is missing.
   Only the secure-context check precedes that. Not chosen as the plan because it needs an
   upstream change. It is raised with upstream, and it would let plain-HTTP users move back
   to WebSockets (see What would reverse this).

**The Origin check**

1. **Add the Desktop port to `CADDY_HEADER_UP_LOCALHOST` (ADR 0043).** Both competing designs
   chose this. Rejected on the live result: it rewrites Origin on every request, so a
   cross-site handshake carrying Basic credentials (Caddy's fallback route; browsers cache
   them) is accepted.
2. **`--allowed-origins` computed at boot.** Rejected: tunnels are created later and
   `PUBLIC_IPADDR` is frozen at creation (ADR 0049), so the list goes stale and fails as a
   silent 403.
3. **Pass the real Host through for the Desktop route.** Chosen. Selkies' own same-origin
   check then works on every access path, including tunnels and IP changes.

**NVENC on subset rentals**

1. **Trust upstream's fallback, log only.** This was one competing design. Rejected: verified
   live to give x264 on subset rentals, which misses the requirement that NVENC be used when
   it works.
2. **A pre-launch probe that sets the encoder.** This was the other design, about 400 lines.
   Rejected: the probe runs the same code Selkies runs at startup, so it adds observation, not
   control. Selkies already logs the encoder it chose.
3. **Delete the unopenable nodes at boot.** Rejected as the mechanism: the portal and any
   root `nvidia-smi` re-create them, which defeats it in normal use.
4. **Fix upstream, and carry an `LD_PRELOAD` shim until the fix is released.** Chosen. The
   upstream change makes pixelflux's reachability test `open()` the node. The shim gives the
   same answer to Selkies' process alone in the meantime. The design review had rejected a
   shim as a large project. The live result shrank it: the filter already works, and only its
   existence test needs correcting.

**Version**

1. **Keep `releases/latest` (ADR 0027's convention).** Rejected: an upstream that deletes
   releases and silently changes behaviour under the same flag names makes a float unsafe.
   Unknown flags only warn, and a drifted auth flag exits 78. ADR 0027's reasoning, a client
   that must track a server-side protocol, does not transfer.
2. **Pin plus per-arch sha256.** Chosen. ADR 0027's own fallback, "pin and contract".
3. **Also mirror the `.deb`.** Not chosen now. A pin would not have prevented this outage,
   because 1.x was deleted outright. If 2.x releases are deleted too, a mirror is the next
   step.

## Decision

- **Install.** Both images install the pinned Selkies `.deb`, with its sha256 checked per
  arch. The two images share one version and one checksum. linux-desktop installs it on
  arm64 too. The `nvidia-vaapi-driver` build, the GStreamer environment, the 1.x `SELKIES_*`
  ENV, the `websockets<14` pin, and the unused novnc/websockify packages are removed.
- **Launch.**
  - Selkies runs as `user`, explicitly bound to `127.0.0.1:16100`, with `--enable-https=false`
    (Caddy terminates TLS) and `--enable-basic-auth=false` (Caddy authenticates).
  - Resize follows the browser, which is upstream's default. aio's periodic re-apply loop is
    removed.
  - The encoder is upstream's `h264enc`: hardware first, software when NVENC will not open.
- **Transport.** The launcher reads whether Caddy actually serves the Desktop route over TLS
  (its generated site carries a `tls` line), which is `ENABLE_HTTPS` AND a usable certificate:
  - **true:** WebSockets. WebRTC is available opt-in when TURN is reachable.
  - **false, and 73478 is mapped:** WebRTC, using the in-image coturn.
  - **false, and 73478 is not mapped:** WebSockets. The browser shows the HTTPS error, and the
    log names the remedy: install the console certificate and set `ENABLE_HTTPS=true`.

  The transport switch in the UI is enabled only when TURN is reachable.
- **TURN.**
  - The launcher always writes a mode-0600 RTC config file outside `/tmp`, so the public relay
    is never used by accident. Selkies treats an unparseable file as absent and falls back to
    that relay, so the file is built with a JSON encoder and re-read; if it does not parse,
    Selkies does not start.
  - Port and protocol are chosen as a pair: UDP first, each with its own mapped port.
  - coturn runs as `user`, one listener per mapped protocol (TCP and UDP 73478 get different
    ports), never with `allow-loopback-peers`, and denies relaying into loopback and private
    ranges except the container's own addresses. It sets no `external-ip`: relay addresses stay
    on the container's interfaces, where Selkies' media endpoint is, so no public relay port
    or hairpin NAT is needed. Its relay pool is off (`relay-threads=0`); its auth pool, which
    coturn sizes at `1 + cpus/2` with cpus capped at 128 and offers no setting for, bounds it
    at about 70 threads per listener on any host (31 measured on a 56-core host), against the
    PID cap high-core hosts put on a container.
  - Credentials stay off argv.
  - A user-supplied TURN server (`TURN_SERVER` / `SELKIES_TURN_*`) replaces the in-image one.
- **Origin.** The portal's Caddy config passes the browser's Host through for the Desktop
  route, so Selkies' same-origin check holds behind Caddy, behind a tunnel, and after an IP
  change.
- **NVENC.**
  - A small `LD_PRELOAD` shim, scoped to the Selkies process, makes `access()` on
    `/dev/nvidiaN` report absent when the node will not open.
  - The shim's source cites the upstream pixelflux change that makes the same correction:
    https://github.com/selkies-project/pixelflux/pull/44.
  - The shim is removed when the pinned Selkies carries that change. #44 merged on 2026-10-05
    as pixelflux commit `7f8369e`; Selkies 2.0.0 bundles pixelflux 2.1.0, which predates it, so
    the shim stays until a Selkies release bundles a pixelflux release containing that commit.
- **Desktop route.** The boot stages stop removing the Desktop portal entry based on a 1.x
  binary name. Selkies is installed wherever the image builds.

## Binding conditions

1. **The transport choice reads what Caddy serves, not the environment.** The supervisor
   scripts reload the template's `ENABLE_HTTPS` from `/etc/environment`, and Caddy also requires
   a usable certificate, so the launcher reads the `tls` line of Caddy's generated site.
   That includes the boot-time downgrade to false when no usable certificate exists (stage
   55). A launcher that sees a stale `true` from `/etc/environment` would pick WebSockets on a
   plain-HTTP instance.
2. **Host passthrough is scoped to the Desktop route, covered by a test, and released first.**
   It ships as portal v3.1.7 (`CADDY_HOST_PASSTHROUGH`), which instances pick up at first boot;
   no Selkies 2.0 image is promoted before that release is out, or its WebSockets fail as before. The test checks
   that a foreign Origin with valid credentials is refused and a same-origin request is
   upgraded. No other app's Host behaviour changes.
3. **The shim is inert outside the case it fixes.** It affects only `access(F_OK)` on
   `/dev/nvidia<N>`, passes everything else through, is preloaded into Selkies (its child
   processes inherit it, harmlessly), and has a test. A full allocation and a CPU-only instance behave exactly as without it.
4. **The aio-studio app-layer edits ship with the aio base bump that installs 2.0.** The
   Desktop-route strip, the Caddy configuration, and the QA templates must land together;
   otherwise the old strip silently removes the Desktop route.
5. **QA starts the desktop and streams through Caddy.** aio-studio-base QA maps 6100 and
   asserts that Selkies is bound to loopback only, that the WebSocket handshake through Caddy
   returns 101 with a token and 401 without one, and that the RTC config contains no public
   relay. It reports which encoder Selkies chose.
6. **New lint rules, each with a mutation test.** The Selkies pin and checksum, identical
   across the two images, are gated. No boot stage removes a portal route because a binary
   probe failed.
7. **The image moves to gVisor (`runsc`) only after a live re-test.** gVisor's `nvproxy`
   virtualizes the device nodes and `/proc/driver/nvidia`, and leaves the `video` capability
   (NVENC) off by default. The subset-GPU behaviour, the shim, and the filter must be
   re-verified on `runsc` before that runtime ships.

## Consequences

- Both images build again, on a maintained upstream, and linux-desktop gains Selkies on
  arm64.
- HTTPS users get WebSockets over the single Caddy port. Plain-HTTP users get WebRTC if 73478
  is mapped, otherwise an error page that names the fix. That gives users a visible reason to
  install the console certificate and set `ENABLE_HTTPS`.
- Subset-GPU rentals encode on NVENC, and full rentals are unaffected. Datacenter GPUs without
  NVENC, and CPU-only instances, encode in software.
- Accepted:
  - TURN stays on the default path for plain-HTTP users, with coturn in the image.
  - Removing the `nvidia-vaapi-driver` build drops VA-API hardware video decode for
    applications inside the desktop (a browser playing video, for one); they decode in
    software. Selkies itself never used it on NVIDIA.
  - The pin needs bumping by hand.
  - The shim is ours to carry until upstream releases the fix.
  - The TURN host still comes from `PUBLIC_IPADDR`, which goes stale after an IP change
    (ADR 0049's live-address work is the follow-up).
  - linux-desktop still has no QA gate of its own.
- Not addressed here, raised separately:
  - aio's x11vnc listens publicly on 5900 with the open-button token as its password.
  - The portal's auth cookie is not port-scoped.

## What would reverse this

- **Upstream ships an insecure-context JPEG fallback.** Plain-HTTP users can then use
  WebSockets, and TURN returns to opt-in only.
- **A pixelflux release with an `open()`-based reachability test.** The shim is deleted when
  the pin moves to it.
- **Upstream deletes 2.x releases.** The `.deb` is mirrored.
- **Vast stops creating nodes for unallocated GPUs, or drivers that no longer peer-init
  hidden GPUs become universal (610 and later reportedly).** The shim becomes inert and can
  go.
- **gVisor (`runsc`) shows different device semantics.** This decision's NVENC section is
  revisited.
