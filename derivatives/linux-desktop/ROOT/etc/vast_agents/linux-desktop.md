## Linux desktop (this image)

A full **KDE Plasma** desktop running on a virtual X display, reachable from a
browser. It shows up as a desktop service in `/capabilities/services` — open it
via that entry's `direct_url` + token (base.md §5). It is **not** an OpenAI/web-API
endpoint; it's an interactive GUI to drive visually or automate. Built on the
**base image** (not pytorch) — base.md applies; there is no `/venv/main` torch stack here.

**Browser delivery path depends on arch** (this is the thing agents get wrong):
- **amd64** — **Selkies** low-latency WebRTC (internal port 16100), plus a
  **Guacamole** HTML5 VNC path (Tomcat, internal 16200).
- **arm64** — Selkies is published amd64-only upstream, so it is **not installed**
  here: its `supervisor` program and its `:16100:` portal entry are removed
  automatically at boot. **Guacamole VNC (16200) is the remote-access path.** Don't
  look for a `selkies` service under `supervisorctl` on arm64 — its absence is
  expected, not a fault.

Either way `x11vnc` also serves the raw display on `:5900` for a native VNC client
(reach it by SSH-forwarding 5900, base.md §7). The VNC password is **`VNC_PASSWORD`**
if set at launch, otherwise **`OPEN_BUTTON_TOKEN`** (the instance token). The
in-browser Guacamole path applies this for you; you only need it for a direct
client on `:5900`.

### Driving the desktop headlessly (no browser)

The session is `DISPLAY=:20`, owned by the unprivileged user **`user`**. From an SSH
shell (usually root) run GUI tools **as `user`** so they share the session bus —
`runuser -u user -- env DISPLAY=:20 <cmd>`. The loop is **see → act → launch**:

- **See the screen.** `ffmpeg` is installed and can grab the display straight to a
  PNG you can pull back over SSH:
  ```
  runuser -u user -- env DISPLAY=:20 ffmpeg -y -f x11grab -video_size 1920x1080 -i :20 -frames:v 1 /tmp/screen.png
  ```
  (`xwd -root` from `x11-apps` also captures, but ffmpeg gives a normal image — there
  is no ImageMagick on the box to convert an `.xwd`.)
- **Act.** `xdotool` injects keys/mouse and `wmctrl` lists/activates windows (both
  preinstalled):
  ```
  runuser -u user -- env DISPLAY=:20 wmctrl -l                       # list windows
  runuser -u user -- env DISPLAY=:20 xdotool type "hello"            # type into focused window
  runuser -u user -- env DISPLAY=:20 xdotool key Return
  ```
- **Launch GPU apps with `vglrun`.** This is a GPU desktop: menu-launched apps are
  auto-wrapped by the `vgl-desktop-patcher` service (it rewrites the `Exec=` lines in
  `/usr/share/applications/*.desktop`). An app you start **directly from a shell skips
  that**, so prefix `vglrun` yourself to get hardware GL:
  ```
  runuser -u user -- env DISPLAY=:20 vglrun blender &
  ```
  (No GPU on the instance, or `DISABLE_VGL=true`, and the patcher no-ops — apps still
  run on software GL.)

**Resolution** is `DISPLAY_SIZEW`×`DISPLAY_SIZEH` (default **1920×1080**), chosen at
launch; the live size is whatever `xrandr` reports on `:20` (use it in the ffmpeg
`-video_size` above if you changed it).

### Apps & services

**Preinstalled GUI apps:** Firefox (all arches), Google Chrome (amd64 only), Blender,
VLC, LibreOffice, and the KDE Plasma application set. Optional launchers come via the
provisioner (base.md §10): **Pinokio** (`provisioning_scripts/pinokio.sh`) and **Tari
Universe** (`tari-universe.sh`), each dropped as a `~/Desktop/*.desktop` entry.

Desktop-stack `supervisor` services: `x-server` (Xvfb on `:20`), `kde`, `x11vnc`,
`tomcat` + `guacd` (Guacamole), `vgl-desktop-patcher`, the PipeWire audio stack, and
`selkies` (amd64 only). `supervisorctl status` is the quick health check before you
assume the GUI is up.
