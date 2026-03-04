"""Generic wget download handler.

Handles CivitAI (with token) and plain HTTP downloads.
Supports content-disposition filename extraction when dest ends with '/'.
"""

from __future__ import annotations

import logging
import os
import subprocess

from ..concurrency import FileLock
from ..schema import DownloadEntry, RetrySettings
from .base import retry_with_backoff

log = logging.getLogger("provisioner")


def _get_content_disposition_filename(url: str, auth_header: str | None = None) -> str:
    """Fetch the filename from Content-Disposition headers."""
    cmd = ["curl", "-sI", "-L", "--max-time", "30", url]
    if auth_header:
        cmd.extend(["-H", auth_header])

    result = subprocess.run(cmd, capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if "content-disposition" in line.lower():
            # Extract filename from: Content-Disposition: ... filename="file.ext"
            for part in line.split(";"):
                part = part.strip()
                if part.lower().startswith("filename="):
                    fname = part.split("=", 1)[1].strip().strip('"').strip("'")
                    return os.path.basename(fname)
    return ""


def _is_civitai(url: str) -> bool:
    return "civitai.com" in url


def download_wget(
    entry: DownloadEntry,
    retry: RetrySettings,
    civitai_token: str = "",
    dry_run: bool = False,
) -> None:
    """Download a file using wget.

    For CivitAI URLs, adds Authorization header.
    If dest ends with '/', uses content-disposition for filename.
    File locking prevents concurrent downloads of the same file.
    """
    url = entry.url
    dest = entry.dest
    auth_header = ""

    if _is_civitai(url) and civitai_token:
        auth_header = f"Authorization: Bearer {civitai_token}"

    # Resolve dest for content-disposition
    use_content_disposition = dest.endswith("/")
    if use_content_disposition:
        dest_dir = dest.rstrip("/")
        if not dry_run:
            filename = _get_content_disposition_filename(url, auth_header or None)
            if not filename:
                # Fallback: use last URL path segment
                filename = os.path.basename(url.split("?")[0]) or "download"
                log.warning("Could not determine filename from headers, using: %s", filename)
            dest = os.path.join(dest_dir, filename)
        else:
            dest = os.path.join(dest_dir, "<content-disposition>")

    if dry_run:
        log.info("[DRY RUN] Would download wget %s -> %s", url, dest)
        return

    with FileLock(dest):
        if os.path.isfile(dest):
            log.info("File already exists: %s (skipping)", dest)
            return

        os.makedirs(os.path.dirname(dest), exist_ok=True)

        def _do_download() -> bool:
            cmd = [
                "wget", "-q", "--show-progress",
                "--max-redirect=10",
                "-O", dest,
                url,
            ]
            if auth_header:
                cmd.extend(["--header", auth_header])

            result = subprocess.run(cmd)
            if result.returncode != 0:
                # Clean up partial downloads
                try:
                    os.unlink(dest)
                except OSError:
                    pass
                return False

            if os.path.isfile(dest) and os.path.getsize(dest) > 0:
                log.info("Successfully downloaded: %s", dest)
                return True

            return False

        if not retry_with_backoff(
            _do_download,
            label=url,
            max_attempts=retry.max_attempts,
            initial_delay=retry.initial_delay,
            backoff_multiplier=retry.backoff_multiplier,
        ):
            raise RuntimeError(f"Failed to download {url}")
