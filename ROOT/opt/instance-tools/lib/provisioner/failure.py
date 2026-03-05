"""Failure action dispatch for the provisioner.

Supports: continue, destroy, stop.
Optional webhook notification on failure.

The ``stop`` action uses a sentinel file (``/.provisioner_stopped``)
to prevent restart loops: provisioner fails → stops instance → user
restarts → provisioner fails again → stops again.  On second stop
the sentinel already exists, so the command is skipped.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import subprocess
import time
import urllib.request

from .schema import OnFailure

log = logging.getLogger("provisioner")

STOP_SENTINEL = "/.provisioner_stopped"


def _call_webhook(url: str, payload: dict) -> None:
    """POST JSON payload to webhook URL. Best-effort, never raises."""
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("Webhook response: %d", resp.status)
    except Exception as e:
        log.warning("Webhook call failed: %s", e)


def _vastai_command(action: str) -> None:
    """Run a vastai instance command (destroy or stop)."""
    container_id = os.environ.get("CONTAINER_ID", "")
    api_key = os.environ.get("CONTAINER_API_KEY", "")

    if not container_id:
        log.error("Cannot %s instance: CONTAINER_ID not set", action)
        return
    if not api_key:
        log.error("Cannot %s instance: CONTAINER_API_KEY not set", action)
        return

    cmd = ["vastai", action, "instance", container_id, "--api-key", api_key]
    log.info("Running: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, timeout=30)
        log.info("Instance %s command succeeded", action)
    except Exception as e:
        log.error("Instance %s command failed: %s", action, e)


def handle_failure(
    on_failure: OnFailure,
    manifest_path: str,
    error: str = "",
) -> None:
    """Dispatch failure action based on on_failure config.

    Called when the provisioner finishes with errors and retries are exhausted.
    """
    action = on_failure.action

    # Resolve webhook URL: env var takes priority
    webhook_url = os.environ.get("PROVISIONER_WEBHOOK_URL") or on_failure.webhook

    # Send webhook notification if configured
    if webhook_url:
        payload = {
            "action": action,
            "manifest": manifest_path,
            "error": error,
            "container_id": os.environ.get("CONTAINER_ID", ""),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _call_webhook(webhook_url, payload)

    if action == "continue":
        log.info("on_failure=continue: returning exit code 1")
        return

    elif action == "destroy":
        log.warning("on_failure=destroy: destroying instance")
        _vastai_command("destroy")

    elif action == "stop":
        if os.path.exists(STOP_SENTINEL):
            log.warning("Instance was already stopped by provisioner, skipping to prevent restart loop")
            return
        log.warning("on_failure=stop: stopping instance")
        try:
            pathlib.Path(STOP_SENTINEL).touch()
        except OSError as e:
            log.warning("Could not write stop sentinel %s: %s", STOP_SENTINEL, e)
        _vastai_command("stop")

    else:
        log.error("Unknown on_failure action: '%s', treating as 'continue'", action)


def notify_success(on_failure: OnFailure, manifest_path: str) -> None:
    """Send webhook notification on successful provisioning (opt-in)."""
    if not on_failure.webhook_on_success:
        return

    webhook_url = os.environ.get("PROVISIONER_WEBHOOK_URL") or on_failure.webhook
    if not webhook_url:
        return

    payload = {
        "action": "success",
        "manifest": manifest_path,
        "error": "",
        "container_id": os.environ.get("CONTAINER_ID", ""),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _call_webhook(webhook_url, payload)
