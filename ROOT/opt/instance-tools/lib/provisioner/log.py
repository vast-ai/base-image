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


# Secrets whose values must never reach a log. Manifest loading expands
# ${VAR} into post_commands, URLs and scripts, so any log line can carry them.
_SECRET_ENV_VARS = ("CONTAINER_API_KEY", "HF_TOKEN", "CIVITAI_TOKEN")
# Shorter values are not real credentials; masking them would mangle output.
_MIN_SECRET_LEN = 8


class _RedactSecrets(logging.Filter):
    """Mask secret env values in every record a provisioner handler emits.

    Attached to handlers rather than the logger: extension loggers
    (provisioner.ext.*) propagate to these handlers, but a logger-level
    filter on "provisioner" never sees their records.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = [v for v in (os.environ.get(k, "") for k in _SECRET_ENV_VARS)
                   if len(v) >= _MIN_SECRET_LEN]
        if not secrets:
            return True
        msg = record.getMessage()
        if record.exc_info and not record.exc_text:
            record.exc_text = _formatter.formatException(record.exc_info)
        for s in secrets:
            msg = msg.replace(s, "***")
            if record.exc_text:
                record.exc_text = record.exc_text.replace(s, "***")
        record.msg, record.args = msg, None
        return True


_redact = _RedactSecrets()


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
        stdout_handler.addFilter(_redact)
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
                file_handler.addFilter(_redact)
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
                    clean_handler.addFilter(_redact)
                    logger.addHandler(clean_handler)
                except OSError as e:
                    logger.warning("Could not open clean log file %s: %s", clean_path, e)

    return logger
