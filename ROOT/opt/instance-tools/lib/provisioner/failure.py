"""Failure action dispatch for the provisioner.

Supports: continue, retry, destroy, stop.
Optional webhook notification on failure.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.request

from .schema import OnFailure

log = logging.getLogger("provisioner")

ATTEMPTS_FILE = "/.provisioner_attempts"


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


def _get_attempt_count() -> int:
    """Read the current attempt count from the attempts file."""
    try:
        with open(ATTEMPTS_FILE) as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _increment_attempts() -> int:
    """Increment and return the new attempt count."""
    count = _get_attempt_count() + 1
    with open(ATTEMPTS_FILE, "w") as f:
        f.write(str(count))
    return count


def _check_retries(max_retries: int) -> bool:
    """Check if we're under the retry limit. Returns True if should retry."""
    count = _increment_attempts()
    log.info("Retry attempt %d / %d", count, max_retries)
    return count < max_retries


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

    Called when the provisioner finishes with errors.
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

    elif action == "retry":
        if _check_retries(on_failure.max_retries):
            log.info("Will retry on next boot (attempt file: %s)", ATTEMPTS_FILE)
        else:
            log.warning("Max retries (%d) exceeded, falling through to continue",
                        on_failure.max_retries)
        return

    elif action == "destroy":
        log.warning("on_failure=destroy: destroying instance")
        _vastai_command("destroy")

    elif action == "stop":
        log.warning("on_failure=stop: stopping instance")
        _vastai_command("stop")

    else:
        log.error("Unknown on_failure action: '%s', treating as 'continue'", action)
