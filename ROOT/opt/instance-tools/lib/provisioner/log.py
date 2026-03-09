"""Logging setup for the provisioner.

Configures a logger that writes timestamped output to both
stdout and an optional log file.
"""

import logging
import os
import sys

_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def setup_logging(log_file: str | None = None) -> logging.Logger:
    """Set up and return the provisioner logger.

    On the first call, adds a stdout handler. On subsequent calls
    with a log_file, adds a file handler without duplicating stdout.
    """
    logger = logging.getLogger("provisioner")
    logger.setLevel(logging.DEBUG)

    # Add stdout handler only once
    if not logger.handlers:
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setLevel(logging.DEBUG)
        stdout_handler.setFormatter(_formatter)
        logger.addHandler(stdout_handler)

    # Add file handler if requested and not already present
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
                logger.addHandler(file_handler)
            except OSError as e:
                logger.warning("Could not open log file %s: %s", log_file, e)

    return logger
