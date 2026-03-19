"""HuggingFace download handler.

Supports two modes:
  1. Single file: URL contains /resolve/ — downloads one file to dest.
  2. Full repo:   URL is just huggingface.co/org/repo — downloads entire model.

When dest is empty, downloads go to the HF cache ($HF_HOME or
~/.cache/huggingface/hub).  This is the standard approach for inference
engines like vLLM that read models from cache directly.
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
from ..subprocess_runner import run_cmd
from .base import retry_with_backoff

log = logging.getLogger("provisioner")

# Single file: https://huggingface.co/{org}/{repo}/resolve/{revision}/{file_path}
_HF_FILE_RE = re.compile(
    r"https://huggingface\.co/([^/]+/[^/]+)/resolve/([^/]+)/(.*)"
)

# Repo-level: https://huggingface.co/{org}/{repo} (optionally with trailing /)
_HF_REPO_RE = re.compile(
    r"https://huggingface\.co/([^/]+/[^/]+?)/?$"
)


def parse_hf_url(url: str) -> tuple[str, str, str]:
    """Extract (repo, revision, file_path) from a HuggingFace URL.

    For single-file URLs: returns (repo, revision, file_path).
    For repo-level URLs: returns (repo, "", "").

    Raises ValueError if the URL doesn't match any expected pattern.
    """
    m = _HF_FILE_RE.match(url)
    if m:
        return m.group(1), m.group(2), m.group(3)

    m = _HF_REPO_RE.match(url)
    if m:
        return m.group(1), "", ""

    raise ValueError(f"Invalid HuggingFace URL: {url}")


def _download_repo(
    repo: str, dest: str, retry: RetrySettings, dry_run: bool = False,
) -> None:
    """Download an entire HuggingFace repo."""
    cache_mode = not dest

    if dry_run:
        if cache_mode:
            log.info("[DRY RUN] Would download HF repo %s -> HF cache", repo)
        else:
            log.info("[DRY RUN] Would download HF repo %s -> %s", repo, dest)
        return

    label = repo

    def _do_download() -> bool:
        cmd = ["hf", "download", repo]
        if dest:
            cmd += ["--local-dir", dest]
        try:
            run_cmd(cmd, label="hf", check=True)
        except subprocess.CalledProcessError:
            return False
        if cache_mode:
            log.info("Successfully downloaded repo %s to HF cache", repo)
        else:
            log.info("Successfully downloaded repo %s -> %s", repo, dest)
        return True

    if dest:
        os.makedirs(dest, exist_ok=True)

    if not retry_with_backoff(
        _do_download,
        label=label,
        max_attempts=retry.max_attempts,
        initial_delay=retry.initial_delay,
        backoff_multiplier=retry.backoff_multiplier,
    ):
        raise RuntimeError(f"Failed to download repo {repo}")


def _download_file(
    repo: str, revision: str, file_path: str,
    dest: str, retry: RetrySettings, dry_run: bool = False,
) -> None:
    """Download a single file from a HuggingFace repo."""
    cache_mode = not dest

    if cache_mode:
        if dry_run:
            log.info("[DRY RUN] Would download HF %s/%s -> HF cache", repo, file_path)
            return

        def _do_download() -> bool:
            cmd = ["hf", "download", repo, file_path, "--revision", revision]
            try:
                run_cmd(cmd, label="hf", check=True)
            except subprocess.CalledProcessError:
                return False
            log.info("Successfully downloaded %s/%s to HF cache", repo, file_path)
            return True

        if not retry_with_backoff(
            _do_download,
            label=f"{repo}/{file_path}",
            max_attempts=retry.max_attempts,
            initial_delay=retry.initial_delay,
            backoff_multiplier=retry.backoff_multiplier,
        ):
            raise RuntimeError(f"Failed to download {repo}/{file_path}")
        return

    # dest mode: download to a specific path
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
                    "hf", "download",
                    repo, file_path,
                    "--revision", revision,
                    "--local-dir", tmp_dir,
                    "--cache-dir", os.path.join(tmp_dir, ".cache"),
                ]
                try:
                    run_cmd(cmd, label="hf", check=True)
                except subprocess.CalledProcessError:
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
            raise RuntimeError(f"Failed to download {repo}/{file_path}")


def download_hf(entry: DownloadEntry, retry: RetrySettings, dry_run: bool = False) -> None:
    """Download from HuggingFace.

    Handles both single-file URLs (/resolve/) and full repo URLs.
    When dest is empty, downloads go to the HF cache ($HF_HOME).
    Uses `hf download` which automatically reads $HF_TOKEN.
    """
    repo, revision, file_path = parse_hf_url(entry.url)

    if file_path:
        _download_file(repo, revision, file_path, entry.dest, retry, dry_run)
    else:
        _download_repo(repo, entry.dest, retry, dry_run)
