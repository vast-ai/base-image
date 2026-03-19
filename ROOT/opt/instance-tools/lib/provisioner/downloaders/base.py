"""Shared retry logic for downloaders."""

from __future__ import annotations

import logging
import time
from typing import Callable

log = logging.getLogger("provisioner")


def retry_with_backoff(
    fn: Callable[[], bool],
    label: str,
    max_attempts: int = 5,
    initial_delay: int = 2,
    backoff_multiplier: int = 2,
) -> bool:
    """Retry a function with exponential backoff.

    fn should return True on success, False on failure.
    Returns True if fn eventually succeeds, False otherwise.
    """
    delay = initial_delay

    for attempt in range(1, max_attempts + 1):
        log.info("Downloading %s (attempt %d/%d)...", label, attempt, max_attempts)
        try:
            if fn():
                return True
        except Exception as e:
            log.warning("Download error on attempt %d: %s", attempt, e)

        if attempt < max_attempts:
            log.info("Retrying in %ds...", delay)
            time.sleep(delay)
            delay *= backoff_multiplier

    log.error("Failed to download %s after %d attempts", label, max_attempts)
    return False
