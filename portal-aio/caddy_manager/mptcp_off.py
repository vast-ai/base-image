"""Plain-TCP listeners for Caddy and Syncthing on images built before the fix (temporary, ADR 0061).

CVE-2026-98432 is a host-kernel IPv4 TCP handshake use-after-free. Its published
attack needs an MPTCP listener, and Go 1.24+ programs listen as MPTCP by default.
In images built before the fix, Caddy (every public port) and Syncthing (the
public sync port) both do. The first-boot portal update reaches those instances
whatever image they run, so the portal wraps each binary to run with
GODEBUG=multipathtcp=0, appended so it wins over any earlier value.

caddy_config_manager.py calls apply() before it publishes /etc/portal.yaml, and
Caddy and Syncthing both wait for that file on first boot, so neither has started.

Skipped where the image's launch script already exports it. The shipped binary moves
to .mptcp_off/<name> beside the wrapper, keeping its name: the image's own tests find
Caddy by process name (pgrep -x caddy, `caddy run`). Any failure leaves the shipped
binary in place, because the service must start.

This runs as root, and /opt/syncthing is owned by `user`, so nothing here follows a
symlink: a symlinked target or .mptcp_off is skipped, the temp file is created
exclusively, and the link and unlink go through a directory fd opened O_NOFOLLOW.

Retire this only once host kernels are confirmed patched. Users' saved templates
keep pinning old images, so new instances of them depend on this indefinitely.
"""

import os
import re
import stat
import tempfile

WRAPPER_MARK = "# portal mptcp_off wrapper"
ORIG_DIR = ".mptcp_off"
TARGETS = (
    ("Caddy", "/opt/portal-aio/caddy_manager/caddy", "/opt/supervisor-scripts/caddy.sh"),
    ("Syncthing", "/opt/syncthing/syncthing", "/opt/supervisor-scripts/syncthing.sh"),
)
# The line images built with the fix carry (L118 checks for the same line).
_BAKED = re.compile(r'^\s*export GODEBUG="\$\{GODEBUG:\+\$\{GODEBUG\},\}multipathtcp=0"\s*$', re.M)


def _is_wrapper(path):
    try:
        with open(path, "rb") as f:
            return WRAPPER_MARK.encode() in f.read(512)
    except OSError:
        return False


def _image_has_fix(script):
    try:
        with open(script, encoding="utf-8", errors="replace") as f:
            return bool(_BAKED.search(f.read()))
    except OSError:
        return False


def wrap(target, script):
    """Run `target` with GODEBUG=multipathtcp=0 via a wrapper. Returns a short status string."""
    if os.path.islink(target):
        return "skip: is a symlink"
    if not os.path.isfile(target):
        return "skip: not installed"
    if _is_wrapper(target):
        return "ok: already wrapped"
    if _image_has_fix(script):
        return "ok: image already runs it as plain TCP"

    parent = os.path.dirname(target)
    name = os.path.basename(target)
    orig_dir = os.path.join(parent, ORIG_DIR)
    st = os.stat(target)
    tmp = None
    dfd = None
    try:
        try:
            os.mkdir(orig_dir, 0o755)
            pst = os.stat(parent)
            try:
                os.chown(orig_dir, pst.st_uid, pst.st_gid, follow_symlinks=False)
            except PermissionError:
                pass
        except FileExistsError:
            pass
        if not stat.S_ISDIR(os.lstat(orig_dir).st_mode):
            return f"skip: {ORIG_DIR} is not a directory"
        dfd = os.open(orig_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

        fd, tmp = tempfile.mkstemp(dir=parent, prefix=f".{name}.wrap.")
        with os.fdopen(fd, "w") as f:
            f.write(
                "#!/bin/sh\n"
                f"{WRAPPER_MARK}: plain-TCP listeners (ADR 0061). The last multipathtcp= wins.\n"
                'GODEBUG="${GODEBUG:+${GODEBUG},}multipathtcp=0"\n'
                "export GODEBUG\n"
                f'exec "{os.path.join(orig_dir, name)}" "$@"\n'
            )
            os.fchmod(f.fileno(), 0o755)
            try:
                os.fchown(f.fileno(), st.st_uid, st.st_gid)
            except PermissionError:
                pass
        # Hard link, then atomic replace: the path always holds a runnable binary.
        try:
            os.unlink(name, dir_fd=dfd)
        except FileNotFoundError:
            pass
        os.link(target, name, dst_dir_fd=dfd, follow_symlinks=False)
        os.replace(tmp, target)
        tmp = None
        return "installed: wrapped with GODEBUG=multipathtcp=0"
    except OSError as e:
        return f"skip: could not wrap: {e}"
    finally:
        if dfd is not None:
            os.close(dfd)
        if tmp is not None and os.path.lexists(tmp):
            os.unlink(tmp)


def apply(targets=TARGETS):
    for name, target, script in targets:
        try:
            print(f"{name}: {wrap(target, script)}")
        except Exception as e:  # never allowed to stop config generation
            print(f"{name}: skip: {e}")
