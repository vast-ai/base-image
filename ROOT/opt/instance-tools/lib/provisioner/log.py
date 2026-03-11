"""Logging setup for the provisioner.

Configures a logger that writes timestamped output to both
stdout and optional log files (portal + clean).
"""

import logging
import os
import sys

_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _derive_clean_path(portal_path: str) -> str | None:
    """Derive /var/log/X.log from /var/log/portal/X.log."""
    d, basename = os.path.split(portal_path)
    if os.path.basename(d) == "portal":
        return os.path.join(os.path.dirname(d), basename)
    return None


def setup_logging(log_file: str | None = None) -> logging.Logger:
    """Set up and return the provisioner logger.

    On the first call, adds a stdout handler. On subsequent calls
    with a log_file, adds a portal file handler (raw ANSI preserved)
    and a clean file handler (for /var/log/).
    """
    logger = logging.getLogger("provisioner")
    logger.setLevel(logging.DEBUG)

    # Add stdout handler only once
    if not logger.handlers:
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setLevel(logging.DEBUG)
        stdout_handler.setFormatter(_formatter)
        logger.addHandler(stdout_handler)

    # Add file handlers if requested and not already present
    if log_file:
        has_file = any(
            isinstance(h, logging.FileHandler) for h in logger.handlers
        )
        if not has_file:
            try:
                os.makedirs(os.path.dirname(log_file), exist_ok=True)
                file_handler = logging.FileHandler(log_file)
                file_handler.setLevel(logging.DEBUG)
                file_handler.setFormatter(_formatter)
                file_handler.portal = True  # type: ignore[attr-defined]
                logger.addHandler(file_handler)
            except OSError as e:
                logger.warning("Could not open log file %s: %s", log_file, e)

            # Add a clean log alongside the portal log
            clean_path = _derive_clean_path(log_file)
            if clean_path:
                try:
                    os.makedirs(os.path.dirname(clean_path), exist_ok=True)
                    clean_handler = logging.FileHandler(clean_path)
                    clean_handler.setLevel(logging.DEBUG)
                    clean_handler.setFormatter(_formatter)
                    logger.addHandler(clean_handler)
                except OSError as e:
                    logger.warning("Could not open clean log file %s: %s", clean_path, e)

    return logger
