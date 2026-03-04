"""HuggingFace download handler.

Parses HF URLs to extract repo/file, then uses `huggingface-cli download`
to fetch files with automatic token usage from $HF_TOKEN.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile

from ..concurrency import FileLock
from ..schema import DownloadEntry, RetrySettings
from .base import retry_with_backoff

log = logging.getLogger("provisioner")

# Pattern: https://huggingface.co/{org}/{repo}/resolve/{revision}/{file_path}
_HF_URL_RE = re.compile(
    r"https://huggingface\.co/([^/]+/[^/]+)/resolve/([^/]+)/(.*)"
)


def parse_hf_url(url: str) -> tuple[str, str, str]:
    """Extract (repo, revision, file_path) from a HuggingFace URL.

    Raises ValueError if the URL doesn't match the expected pattern.
    """
    m = _HF_URL_RE.match(url)
    if not m:
        raise ValueError(f"Invalid HuggingFace URL: {url}")
    return m.group(1), m.group(2), m.group(3)


def download_hf(entry: DownloadEntry, retry: RetrySettings, dry_run: bool = False) -> None:
    """Download a file from HuggingFace.

    Uses `huggingface-cli download` which automatically reads $HF_TOKEN.
    File locking prevents concurrent downloads of the same file.
    """
    repo, revision, file_path = parse_hf_url(entry.url)
    dest = entry.dest

    # If dest ends with /, append the filename from the URL
    if dest.endswith("/"):
        dest = os.path.join(dest, os.path.basename(file_path))

    if dry_run:
        log.info("[DRY RUN] Would download HF %s/%s -> %s", repo, file_path, dest)
        return

    with FileLock(dest):
        if os.path.isfile(dest):
            log.info("File already exists: %s (skipping)", dest)
            return

        os.makedirs(os.path.dirname(dest), exist_ok=True)

        def _do_download() -> bool:
            tmp_dir = tempfile.mkdtemp()
            try:
                cmd = [
                    "huggingface-cli", "download",
                    repo, file_path,
                    "--revision", revision,
                    "--local-dir", tmp_dir,
                    "--cache-dir", os.path.join(tmp_dir, ".cache"),
                ]
                result = subprocess.run(
                    cmd, capture_output=True, text=True,
                )
                if result.returncode != 0:
                    log.warning("hf download stderr: %s", result.stderr.strip())
                    return False

                downloaded = os.path.join(tmp_dir, file_path)
                if not os.path.isfile(downloaded):
                    log.warning("Downloaded file not found at %s", downloaded)
                    return False

                shutil.move(downloaded, dest)
                log.info("Successfully downloaded: %s", dest)
                return True
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        if not retry_with_backoff(
            _do_download,
            label=f"{repo}/{file_path}",
            max_attempts=retry.max_attempts,
            initial_delay=retry.initial_delay,
            backoff_multiplier=retry.backoff_multiplier,
        ):
            raise RuntimeError(f"Failed to download {entry.url}")
