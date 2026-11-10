"""Run a script on a test instance over SSH, with a key that exists for one run only.

Some checks need the box itself — the model's tokenizer, already cached there, with
the transformers the engine runs on, and the engine's own trust_remote_code. Running
them on the client would mean installing transformers, downloading tokenizers and
executing model-repo code on our machines. Running them on the box needs none of it.

The key is ephemeral and per-instance:

1. generate an ed25519 key pair in a private temporary directory;
2. attach the PUBLIC half to this instance through the Vast API. A key that is not
   on the account is added to the instance only, never to the account;
3. connect (direct public IP + mapped port 22, else the SSH proxy), retrying while
   the machine pulls the key — the attach is asynchronous;
4. always, on success, failure or interrupt: detach the key from the instance and
   delete both halves — including when setting the session up fails part-way.

Nothing here raises to the caller: every failure, setup included, is returned as a
reason (`unavailable`, or run()'s (False, reason)), which the caller reports as an
error — the check was runnable and could not be run. A check that needs the box
never passes because the box was unreachable.

With a `deadline` (anything with remaining(), e.g. contract.Deadline), the connect
retries and the remote script are bounded by the time left; without one, by this
module's own timeouts.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time

ATTACH_PATH = "/api/v0/instances/{id}/ssh/"
DETACH_PATH = "/api/v0/instances/{id}/ssh/{key}/"
CONNECT_ATTEMPTS = 12          # the machine pulls an attached key asynchronously
CONNECT_WAIT = 10
SSH_TIMEOUT = 300
CONNECT_PROBE_TIMEOUT = 30
DEADLINE_EXCEEDED = "deadline exceeded"


def _key_id(attach_response):
    """The attached key's id from the attach answer (`key` as a JSON string or an
    object), or None when it cannot be read."""
    key = attach_response.get("key")
    try:
        doc = json.loads(key) if isinstance(key, (str, bytes)) else key
    except ValueError:
        return None
    kid = doc.get("id") if isinstance(doc, dict) else None
    if isinstance(kid, bool):
        return None
    if isinstance(kid, int) or (isinstance(kid, str) and kid.strip().isdigit()):
        return kid
    return None


def _ssh_target(instance):
    """(host, port) for direct SSH (public IP + mapped 22/tcp), else the proxy."""
    host = (instance.get("public_ipaddr") or "").strip()
    for key, mappings in (instance.get("ports") or {}).items():
        if key.startswith("22/") and isinstance(mappings, list) and mappings:
            if host:
                return host, int(mappings[0].get("HostPort"))
    if instance.get("ssh_host") and instance.get("ssh_port"):
        return instance["ssh_host"], int(instance["ssh_port"])
    return None


class RemoteSession:
    """Context manager: an ephemeral SSH key on one instance, removed on exit.

    `api` needs `_request_safe(method, path, body=None)` and `get_instance(id, safe=True)`
    (test_template.VastAPI). `run(script, python=...)` executes a Python script on the
    box from stdin and returns (ok, stdout_or_reason).
    """

    def __init__(self, api, instance_id, runner=subprocess.run, sleep=time.sleep,
                 attempts=CONNECT_ATTEMPTS, wait=CONNECT_WAIT, deadline=None):
        self.api, self.instance_id = api, instance_id
        self._run, self._sleep = runner, sleep
        self.attempts, self.wait = attempts, wait
        self.deadline = deadline
        self.dir = None
        self.key_id = None
        self.target = None
        self.unavailable = None          # a reason, when the session could not be made
        self._connected = False
        self._attach_sent = False

    # -- setup / teardown ----------------------------------------------------
    def __enter__(self):
        """Set the session up. Never raises: a failure part-way through detaches
        whatever was attached, deletes the key pair, and leaves `unavailable` set."""
        try:
            self._setup()
        except Exception as e:                                    # noqa: BLE001
            self.unavailable = f"could not set up SSH: {type(e).__name__}: {e}"
            if self._attach_sent and self.key_id is None:
                self.unavailable += ("; the attach may have reached the instance, and "
                                     "without its key id the public key may remain on "
                                     "the instance until the instance is destroyed")
        if self.unavailable:
            self.close()
        return self

    def _setup(self):
        left = self._left()
        if left is not None and left <= 0:
            self.unavailable = f"{DEADLINE_EXCEEDED} before an SSH key was attached"
            return
        if not shutil.which("ssh") or not shutil.which("ssh-keygen"):
            self.unavailable = "ssh or ssh-keygen is not installed on the client"
            return
        self.dir = tempfile.mkdtemp(prefix="vast-qa-ssh-")
        os.chmod(self.dir, 0o700)
        key = os.path.join(self.dir, "id")
        gen = self._run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
                         f"vast-qa-{self.instance_id}", "-f", key],
                        capture_output=True, text=True)
        if getattr(gen, "returncode", 1) != 0:
            self.unavailable = f"ssh-keygen failed: {getattr(gen, 'stderr', '')[:200]}"
            return
        with open(key + ".pub") as f:
            public = f.read().strip()
        self._attach_sent = True
        r = self.api._request_safe("POST", ATTACH_PATH.format(id=self.instance_id),
                                   {"ssh_key": public})
        if not isinstance(r, dict) or not r.get("success"):
            self.unavailable = f"could not attach a key to the instance: {str(r)[:200]}"
            return
        self.key_id = _key_id(r)
        if self.key_id is None:
            # Attached, but without its id it cannot be detached: do not use it.
            self.unavailable = ("the key was attached but the API's answer carries no "
                                f"readable key id ({str(r.get('key'))[:120]}), so it cannot "
                                "be detached; the public key may remain on the instance "
                                "until the instance is destroyed")
            return
        inst = self.api.get_instance(self.instance_id, safe=True) or {}
        self.target = _ssh_target(inst)
        if not self.target:
            self.unavailable = "the instance exposes no SSH endpoint"

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        """Detach the key and delete both halves. Safe to call more than once, and
        never raises: the key pair is deleted even if the detach call fails."""
        try:
            if self.key_id is not None:
                key_id, self.key_id = self.key_id, None
                self.api._request_safe("DELETE", DETACH_PATH.format(id=self.instance_id,
                                                                    key=key_id))
        except Exception:                                         # noqa: BLE001
            pass                    # best effort; a destroyed instance takes its keys
        finally:
            if self.dir:
                shutil.rmtree(self.dir, ignore_errors=True)
                self.dir = None

    def _left(self):
        """Seconds left before the deadline, or None without one."""
        return None if self.deadline is None else self.deadline.remaining()

    def _bounded(self, timeout):
        """`timeout` capped by the deadline: (seconds, capped_by_deadline)."""
        left = self._left()
        if left is None or left >= timeout:
            return timeout, False
        return max(left, 0), True

    # -- use -----------------------------------------------------------------
    def _ssh(self, command, stdin=None, timeout=SSH_TIMEOUT):
        host, port = self.target
        return self._run(
            ["ssh", "-i", os.path.join(self.dir, "id"), "-p", str(port),
             "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
             "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
             "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=15",
             f"root@{host}", command],
            input=stdin, capture_output=True, text=True, timeout=timeout)

    def _connect(self):
        """Wait until the attached key is accepted (the machine pulls it async)."""
        last = "not attempted"
        for attempt in range(self.attempts):
            timeout, capped = self._bounded(CONNECT_PROBE_TIMEOUT)
            if timeout <= 0:
                return False, f"{DEADLINE_EXCEEDED} while waiting for SSH: {last}"
            try:
                r = self._ssh("true", timeout=timeout)
            except subprocess.TimeoutExpired:
                last = "connection timed out"
            else:
                if r.returncode == 0:
                    self._connected = True
                    return True, ""
                last = (r.stderr or r.stdout or f"exit {r.returncode}").strip()[:200]
            if attempt + 1 < self.attempts:
                pause, capped = self._bounded(self.wait)
                if capped:
                    return False, f"{DEADLINE_EXCEEDED} while waiting for SSH: {last}"
                self._sleep(pause)
        return False, f"SSH not accepted after {self.attempts} attempts: {last}"

    def run(self, script, python="python3"):
        """Run a Python script on the box (via stdin). (ok, stdout or reason)."""
        if self.unavailable:
            return False, self.unavailable
        if not self._connected:
            ok, why = self._connect()
            if not ok:
                return False, why
        timeout, capped = self._bounded(SSH_TIMEOUT)
        if timeout <= 0:
            return False, f"{DEADLINE_EXCEEDED} before the remote script started"
        try:
            r = self._ssh(f"{python} -", stdin=script, timeout=timeout)
        except subprocess.TimeoutExpired:
            if capped:
                return False, f"{DEADLINE_EXCEEDED}: the remote script was stopped"
            return False, "the remote script timed out"
        if r.returncode != 0:
            return False, f"the remote script failed (exit {r.returncode}): {(r.stderr or '')[-300:]}"
        return True, r.stdout
