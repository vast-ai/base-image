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
symlink: the target is opened O_NOFOLLOW|O_NONBLOCK and must be a regular file (a
symlink or FIFO is skipped, never waited on), .mptcp_off must be a real directory,
the temp file is created exclusively, and the link and unlink go through a directory
fd opened O_NOFOLLOW. Each wrap holds a lock in this root-owned directory, so two
runs at once cannot interleave the unlink, link and replace.

Retire this only once host kernels are confirmed patched. Users' saved templates
keep pinning old images, so new instances of them depend on this indefinitely.
"""

import errno
import fcntl
import os
import re
import stat
import tempfile

WRAPPER_MARK = "# portal mptcp_off wrapper"
ORIG_DIR = ".mptcp_off"
LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".mptcp_off.lock")
TARGETS = (
    ("Caddy", "/opt/portal-aio/caddy_manager/caddy", "/opt/supervisor-scripts/caddy.sh"),
    ("Syncthing", "/opt/syncthing/syncthing", "/opt/supervisor-scripts/syncthing.sh"),
)
# The line images built with the fix carry (L118 checks for the same line).
_BAKED = re.compile(r'^[ \t]*export GODEBUG="\$\{GODEBUG:\+\$\{GODEBUG\},\}multipathtcp=0"[ \t]*$', re.M)


def _image_has_fix(script):
    try:
        with open(script, encoding="utf-8", errors="replace") as f:
            return bool(_BAKED.search(f.read()))
    except OSError:
        return False


def wrap(target, script):
    """Run `target` with GODEBUG=multipathtcp=0 via a wrapper. Returns a short status string."""
    lock = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _wrap_locked(target, script)
    finally:
        os.close(lock)


def _wrap_locked(target, script):
    try:
        tfd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as e:
        if e.errno == errno.ENOENT:
            return "skip: not installed"
        if e.errno == errno.ELOOP:
            return "skip: is a symlink"
        return f"skip: cannot open: {e}"
    try:
        st = os.fstat(tfd)
        if not stat.S_ISREG(st.st_mode):
            return "skip: not a regular file"
        if WRAPPER_MARK.encode() in os.read(tfd, 512):
            return "ok: already wrapped"
    finally:
        os.close(tfd)
    if _image_has_fix(script):
        return "ok: image already runs it as plain TCP"

    parent = os.path.dirname(target)
    name = os.path.basename(target)
    orig_dir = os.path.join(parent, ORIG_DIR)
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
