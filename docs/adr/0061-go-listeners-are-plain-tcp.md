# ADR 0061 — Go listeners are plain TCP, and the portal release carries the fix to older images

- **Status:** Accepted
- **Date:** 2026-11-02
- **Decision owner:** Rob Ballantyne

## Context
CVE-2026-98432 is a use-after-free in the Linux kernel's IPv4 TCP handshake (IPv4 option data
freed with `kfree()` while another CPU still reads it; fixed upstream by switching to
`kfree_rcu()`). A remote peer can reach it without authentication before any application code
runs. The fix is a patched kernel from the distribution (for Ubuntu, see
https://ubuntu.com/security/CVE-2026-98432). Containers share the host kernel, so no image change
fixes the bug.

The attack published against it needs an **MPTCP listener**, and Go 1.24+ opens MPTCP listeners
by default. Measured on `vastai/base-image:cuda-12.9.2-cudnn-devel-ubuntu24.04-py312-2026-10-23`
with `ss -lnM`: Caddy (every public port) and Syncthing's sync port (`VAST_TCP_PORT_72299`) were
MPTCP listeners; cloudflared and Syncthing's GUI were MPTCP on loopback only. MPTCP cannot be
switched off from inside a container (`net.mptcp.enabled` is per network namespace, and `/proc/sys`
is read-only), so an image controls only how its own programs listen.

Neither program sets MPTCP in its own code, so `GODEBUG=multipathtcp=0` turns it off. When the
setting appears more than once, the last value wins. Both verified on that image: stock Caddy
v2.11.7 listens as MPTCP with no `GODEBUG`, and as plain TCP with `multipathtcp=0` or with
`multipathtcp=1,multipathtcp=0`.

**What this protects, and what it cannot.** It stops our containers' own listeners being the way
in for the published attack, for a user who has not changed anything. It does not protect that
user from host compromise: anyone who can run a container on the same host can open an MPTCP
listener of their own, and someone with a shell in the container can turn MPTCP back on in our
services; that is their choice. Nothing in an image can stop a tenant from reaching the host
kernel, and this ADR does not try. The host kernel is the fix.

The other half is reach: most instances run images built before any fix, pinned by templates, and
users' saved copies of templates.

## Options considered
- **Rely on the kernel patch alone.** Rejected as the only measure: until a host runs a patched
  kernel, our images would offer the listener the published attack needs on every public port.
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
   non-loopback MPTCP listener, read from the kernel's socket table. It first opens its own MPTCP
   listener on loopback and skips unless `ss -M` lists it, so it never passes having checked
   nothing (MPTCP off in the namespace, or no MPTCP socket diagnostics). L118 gates all three.
2. **Carried by the portal release.** `caddy_manager/mptcp_off.py`, called before
   `/etc/portal.yaml` is published, wraps `/opt/portal-aio/caddy_manager/caddy` and
   `/opt/syncthing/syncthing` in a small `sh` script that sets the same `GODEBUG` and `exec`s the
   original, moved to `.mptcp_off/<name>` beside it. The original keeps its file name because older
   images' own tests find Caddy by process name (`pgrep -x caddy`, `(^|/)caddy run`); a `caddy.orig`
   would fail them. Caddy and Syncthing both wait for `/etc/portal.yaml` on first boot, so neither
   has started when the wrap happens. The release itself is unchanged: no binaries, no build step.

## Binding conditions
- A program is not wrapped where its launch script already carries the exact export line of
  decision 1, and a wrapper is never wrapped again.
- Every failure leaves the shipped binary in place and never stops config generation.
- The wrapper keeps the original's owner and passes every argument through unchanged, and the
  running program keeps its name.
- The wrap runs as root, and `/opt/syncthing` is owned by `user`, so it follows no symlink and
  never waits on a file: the binary is opened without following symlinks or blocking and must be a
  regular file, `.mptcp_off` must be a real directory, the temp file is created exclusively, and
  the link and unlink go through a directory opened without following symlinks. A lock in the
  portal's root-owned directory keeps two wraps from interleaving.

## Consequences
- Instances on older images that take the first-boot portal update get plain-TCP Caddy and
  Syncthing on first boot.
- The two changes are independent at runtime, but each one's documentation cites the other: the
  portal change carries this ADR, and the image change carries L118 and test 31. The portal change
  merges first and ships as usual; the image change is promoted from its branch first, as any base
  change is, then merges.
- Reverting decision 2: marking its release as a prerelease takes it out of `releases/latest` at
  once, so new instances fall back to the previous portal. Then revert it and publish the next
  portal version (the release workflow never re-publishes an older version as latest). That stops new wraps only. Instances already wrapped
  keep the wrapper for life, since the portal update runs on first boot only; it behaves like the
  original binary apart from the listener type, so nothing depends on undoing it.
- Loopback MPTCP listeners (Syncthing's GUI, cloudflared's metrics) remain; no remote peer reaches
  them.

## What would reverse this
- Kernels with the upstream fix are in place where our images run: decision 2 can be retired; decision 1 stays, since it costs nothing. Template pins are not a
  reason to retire it: users' saved templates keep pinning old images, and every new instance of
  one depends on decision 2.
- A Go release drops `multipathtcp`: test 31 fails on the first image built with it, and the
  source patch rejected above becomes the fix.
