# ADR 0061 — Go listeners are plain TCP, and the portal release carries the fix to older images

- **Status:** Proposed
- **Date:** 2026-11-02
- **Decision owner:** Rob Ballantyne

## Context
CVE-2026-98432 is a use-after-free in the Linux kernel's IPv4 TCP handshake (IPv4 option data
freed with `kfree()` while another CPU still reads it; fixed upstream by switching to
`kfree_rcu()`). A remote peer can reach it without authentication before any application code
runs. The only fix is a patched host kernel and a reboot. Containers share the host kernel, so no
image change fixes the bug.

The attack published against it needs an **MPTCP listener**, and Go 1.24+ opens MPTCP listeners
by default. Measured on `vastai/base-image:cuda-12.9.2-cudnn-devel-ubuntu24.04-py312-2026-10-23`
with `ss -lnM`: Caddy (every public port) and Syncthing's sync port (`VAST_TCP_PORT_72299`) were
MPTCP listeners; cloudflared and Syncthing's GUI were MPTCP on loopback only. The container's
network namespace has `net.mptcp.enabled=1`, and the container cannot change it: `/proc/sys` is
read-only. Only whatever creates the container can set it (`docker run --sysctl`).

Neither program sets MPTCP in its own code, so `GODEBUG=multipathtcp=0` turns it off. When the
setting appears more than once, the last value wins. Both verified on that image: stock Caddy
v2.11.7 listens as MPTCP with no `GODEBUG`, and as plain TCP with `multipathtcp=0` or with
`multipathtcp=1,multipathtcp=0`.

**What this protects, and what it cannot.** It protects a user who has not changed anything: the
listeners our images open stop offering the path the known attack uses. Someone with a shell in
the container can turn MPTCP back on in our services, or open an MPTCP listener of their own; that
is their choice. Nothing in an image can stop a tenant from reaching the host kernel, and this ADR
does not try. The host kernel is the fix.

The other half is reach: most instances run images built before any fix, pinned by templates, and
users' saved copies of templates.

## Options considered
- **Rely on the host kernel patch alone.** Rejected as the only measure: patching and rebooting a
  fleet of independently operated hosts takes time, and our images offer the exact listener the
  published attack needs on every public port meanwhile. Recommended to the platform in parallel,
  together with `--sysctl net.mptcp.enabled=0` at container creation.
- **Patch Caddy's source (`config.SetMultipathTCP(false)`) instead of `GODEBUG`.** Built and
  verified, then rejected. Its only advantage is that the environment cannot undo it, which
  defends against a user who chose to undo it, not one who does not know. For older images it
  meant shipping two Caddy binaries (about 34 MB) in every portal release, building them in the
  release workflow from the Dockerfile on main (ahead of a base promote), and version and
  architecture checks in the installer. `GODEBUG` might one day be removed from Go; test 31 would
  catch that on the first image built with such a Go.
- **`HOTFIX_SCRIPT` set in our templates.** Built and tested, then rejected. It reaches only
  instances created from templates we publish, not users' saved copies. It needs a separate hosted
  script, and a GitHub release on this repository would become `releases/latest`, which every
  instance's first-boot portal update reads.
- **Disable Syncthing in every image.** Rejected for older images: it would remove a working
  feature from users' saved templates. Syncthing is wrapped instead (below).
- **Also remove Syncthing from our templates' `PORTAL_CONFIG` meanwhile.** Rejected: the portal
  release reaches every new instance from those templates anyway, and it would take a working
  feature away from users for no added cover.
- **The portal release carries the fix.** Chosen. The first-boot portal update reaches every
  instance that takes it, whatever image it runs, and on every boot `caddy.sh` runs the portal's
  `caddy_config_manager.py` before Caddy starts.

## Decision
1. **In the image.** `caddy.sh` and `syncthing.sh` each export
   `GODEBUG="${GODEBUG:+${GODEBUG},}multipathtcp=0"` before starting the program: appended, so a
   user's other settings stay and ours wins. `tests/base/31-no-mptcp-listeners.sh` fails on any
   non-loopback MPTCP listener, read from the kernel's socket table, and skips when MPTCP is off in
   the namespace rather than passing blind. L118 gates all three.
2. **Carried by the portal release.** `caddy_manager/mptcp_off.py`, called before
   `/etc/portal.yaml` is published, wraps `/opt/portal-aio/caddy_manager/caddy` and
   `/opt/syncthing/syncthing` in a small `sh` script that sets the same `GODEBUG` and `exec`s the
   original, moved to `.mptcp_off/<name>` beside it. The original keeps its file name because older
   images' own tests find Caddy by process name (`pgrep -x caddy`, `(^|/)caddy run`); a `caddy.orig`
   would fail them. Caddy and Syncthing both wait for `/etc/portal.yaml` on first boot, so neither
   has started when the wrap happens. The release itself is unchanged: no binaries, no build step.

## Binding conditions
- A program is not wrapped where its launch script already sets `multipathtcp=0`, and a wrapper is
  never wrapped again.
- Every failure leaves the shipped binary in place and never stops config generation.
- The wrapper keeps the original's owner and passes every argument through unchanged, and the
  running program keeps its name.

## Consequences
- Instances on older images that take the first-boot portal update get plain-TCP Caddy and
  Syncthing on first boot.
- Not reached: instances already running (the update runs on first boot only), instances with
  `PORTAL_VERSION` pinned or started with `--no-update-portal`, and serverless (which runs neither
  Caddy nor Syncthing). Those depend on the host kernel.
- The portal change and the image change are independent: the portal release ships from main as
  usual, and the base change is promoted from its branch first, as any base change is.
- Loopback MPTCP listeners (Syncthing's GUI, cloudflared's metrics) remain; no remote peer reaches
  them.

## What would reverse this
- Every template pins an image built with decision 1: remove `mptcp_off.py`.
- The host fleet is confirmed patched, or MPTCP is disabled at container creation everywhere:
  decision 2 can be retired early; decision 1 stays, since it costs nothing.
- A Go release drops `multipathtcp`: test 31 fails on the first image built with it, and the
  source patch rejected above becomes the fix.
