"""Plain-TCP listeners for Caddy and Syncthing on images built before the fix (temporary, ADR 0061).

CVE-2026-98432 is a host-kernel IPv4 TCP handshake use-after-free. Its published
attack needs an MPTCP listener, and Go 1.24+ programs listen as MPTCP by default.
In images built before the fix, Caddy (every public port) and Syncthing (the
public sync port) both do. The first-boot portal update reaches those instances
whatever image they run, so the portal wraps each binary to run with
GODEBUG=multipathtcp=0, appended so it wins over any earlier value.

caddy_config_manager.py calls apply() before it publishes /etc/portal.yaml, and
Caddy and Syncthing both wait for that file on first boot, so neither has started.

Skipped where the image's launch script already sets it. The shipped binary moves
to .mptcp_off/<name> beside the wrapper, keeping its name: the image's own tests find
Caddy by process name (pgrep -x caddy, `caddy run`). Any failure leaves the shipped
binary in place, because the service must start.

Retire this once every template pins an image built with the fix.
"""

import os

WRAPPER_MARK = "# portal mptcp_off wrapper"
ORIG_DIR = ".mptcp_off"
TARGETS = (
    ("Caddy", "/opt/portal-aio/caddy_manager/caddy", "/opt/supervisor-scripts/caddy.sh"),
    ("Syncthing", "/opt/syncthing/syncthing", "/opt/supervisor-scripts/syncthing.sh"),
)


def _is_wrapper(path):
    try:
        with open(path, "rb") as f:
            return WRAPPER_MARK.encode() in f.read(512)
    except OSError:
        return False


def wrap(target, script):
    """Run `target` with GODEBUG=multipathtcp=0 via a wrapper. Returns a short status string."""
    if not os.path.isfile(target):
        return "skip: not installed"
    if _is_wrapper(target):
        return "ok: already wrapped"
    try:
        with open(script, encoding="utf-8", errors="replace") as f:
            if "multipathtcp=0" in f.read():
                return "ok: image already runs it as plain TCP"
    except OSError:
        pass

    parent = os.path.dirname(target)
    orig_dir = os.path.join(parent, ORIG_DIR)
    orig = os.path.join(orig_dir, os.path.basename(target))
    tmp = f"{target}.wrap.{os.getpid()}"
    st = os.stat(target)
    try:
        if not os.path.isdir(orig_dir):
            os.mkdir(orig_dir, 0o755)
            pst = os.stat(parent)
            try:
                os.chown(orig_dir, pst.st_uid, pst.st_gid)
            except PermissionError:
                pass
        with open(tmp, "w") as f:
            f.write(
                "#!/bin/sh\n"
                f"{WRAPPER_MARK}: plain-TCP listeners (ADR 0061). The last multipathtcp= wins.\n"
                'GODEBUG="${GODEBUG:+${GODEBUG},}multipathtcp=0"\n'
                "export GODEBUG\n"
                f'exec "{orig}" "$@"\n'
            )
        os.chmod(tmp, 0o755)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except PermissionError:
            pass
        # Hard link, then atomic replace: the path always holds a runnable binary.
        if os.path.lexists(orig):
            os.unlink(orig)
        os.link(target, orig)
        os.replace(tmp, target)
        return "installed: wrapped with GODEBUG=multipathtcp=0"
    except OSError as e:
        return f"skip: could not wrap: {e}"
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def apply(targets=TARGETS):
    for name, target, script in targets:
        try:
            print(f"{name}: {wrap(target, script)}")
        except Exception as e:  # never allowed to stop config generation
            print(f"{name}: skip: {e}")
