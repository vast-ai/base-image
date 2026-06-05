## Linux desktop (this image)

A full **KDE Plasma** desktop running on a virtual X display, reachable from a
browser. It shows up as a desktop service in `/capabilities/services` — open it
via that entry's `direct_url` + token (base.md §5). It is **not** an OpenAI/web-API
endpoint; it's an interactive GUI to drive visually or automate.

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

**Drive it programmatically.** The session is `DISPLAY=:20`, owned by the
unprivileged user **`user`**. Launch GUI apps onto it and automate windows/input
with the preinstalled `xdotool` / `wmctrl` (run as `user`, not root, so they share
the session bus):
```
runuser -u user -- env DISPLAY=:20 xdotool getdisplaygeometry
runuser -u user -- env DISPLAY=:20 firefox https://example.com &
```
`x11-apps` / `x11-utils` are present for X queries and screenshots (e.g. `xwd`).

**Resolution** is `DISPLAY_SIZEW`×`DISPLAY_SIZEH` (default **1920×1080**), chosen at
launch; the live size is whatever `xrandr` reports on `:20`.

**Preinstalled GUI apps:** Firefox (all arches), Google Chrome (amd64 only),
Blender, and the KDE Plasma application set. Desktop-stack `supervisor` services:
`x-server` (Xvfb), `kde`, `x11vnc`, `tomcat` + `guacd` (Guacamole), the PipeWire
audio stack, and `selkies` (amd64 only).
