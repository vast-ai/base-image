"""CLI entry point for the declarative provisioner.

Usage:
    python -m provisioner manifest.yaml [--dry-run]

Execution phases (sequential):
    1. Load & validate manifest, expand env vars
    2. Validate auth tokens, resolve conditional_downloads
    3. Install apt packages                          [fail-fast]
    4. Clone git repos (parallel)                    [fail-fast]
    5. Install pip packages + requirements files     [fail-fast]
    6. Download all files (parallel, two pools)      [best-effort]
    7. Register supervisor services                  [always runs]
    8. Run post_commands                             [always runs]

Phases 3-5 are fail-fast: if any fails, later phases are skipped
and the provisioner exits 1.  Phases 6-8 always run even if an
earlier best-effort phase had failures.  The exit code is non-zero
when anything failed, so the boot script knows to retry on next start.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys

from .auth import validate_civitai_token, validate_hf_token
from .concurrency import run_parallel
from .downloaders.huggingface import download_hf
from .downloaders.wget import download_wget
from .installers.apt import install_apt_packages
from .installers.git import clone_git_repos
from .installers.pip import install_pip_packages
from .log import setup_logging
from .manifest import apply_env_merge, load_manifest, resolve_conditionals
from .schema import DownloadEntry
from .supervisor import register_services

log = logging.getLogger("provisioner")


def _classify_downloads(
    downloads: list[DownloadEntry],
) -> tuple[list[DownloadEntry], list[DownloadEntry]]:
    """Split downloads into HuggingFace and wget lists based on URL."""
    hf = []
    wget = []
    for entry in downloads:
        if "huggingface.co" in entry.url:
            hf.append(entry)
        else:
            wget.append(entry)
    return hf, wget


def run(manifest_path: str, dry_run: bool = False) -> int:
    """Execute the full provisioning pipeline.

    Phases 3-5 (apt/git/pip) are fail-fast: a failure skips remaining
    phases and returns 1 immediately -- the app can't run without its
    dependencies.

    Phase 6 (downloads) is best-effort: individual download failures
    are logged but execution continues.  Missing models are recoverable
    and the app may still start.

    Phases 7-8 (services/post_commands) always run regardless of
    download failures -- the app must be registered with supervisor.

    Returns 0 on full success, 1 if anything failed.
    """
    had_errors = False

    # Phase 1: Load & validate manifest
    try:
        manifest = load_manifest(manifest_path)
    except Exception as e:
        log.error("Failed to load manifest: %s", e)
        return 1

    # Set up logging with the manifest's log file
    setup_logging(manifest.settings.log_file)

    log.info("Provisioner starting (version 1)")
    log.info("Workspace: %s", manifest.settings.workspace)

    if dry_run:
        log.info("=== DRY RUN MODE ===")

    # Phase 2: Validate auth tokens
    hf_token_env = manifest.auth.huggingface.token_env
    civitai_token_env = manifest.auth.civitai.token_env

    hf_valid = validate_hf_token(hf_token_env)
    civitai_valid = validate_civitai_token(civitai_token_env)

    # Resolve conditional downloads based on token validity
    resolve_conditionals(manifest, hf_token_valid=hf_valid)

    # Merge env var downloads
    apply_env_merge(manifest)

    # ---- Fail-fast phases (3-5): dependencies required for the app ----

    # Phase 3: Install apt packages
    log.info("--- Phase 3: APT packages ---")
    try:
        install_apt_packages(manifest.apt_packages, dry_run=dry_run)
    except Exception as e:
        log.error("APT installation failed: %s", e)
        return 1

    # Phase 4: Clone git repos (parallel)
    log.info("--- Phase 4: Git repos ---")
    try:
        clone_git_repos(
            manifest.git_repos,
            venv=manifest.settings.venv,
            dry_run=dry_run,
        )
    except Exception as e:
        log.error("Git clone failed: %s", e)
        return 1

    # Phase 5: Install pip packages (after git, so requirements files exist)
    log.info("--- Phase 5: Pip packages ---")
    try:
        install_pip_packages(
            manifest.pip_packages,
            venv=manifest.settings.venv,
            dry_run=dry_run,
        )
    except Exception as e:
        log.error("Pip installation failed: %s", e)
        return 1

    # ---- Best-effort phase (6): downloads may fail without breaking the app ----

    # Phase 6: Download files (parallel, two pools)
    log.info("--- Phase 6: Downloads ---")
    hf_downloads, wget_downloads = _classify_downloads(manifest.downloads)
    retry = manifest.settings.retry
    civitai_token = os.environ.get(civitai_token_env, "")
    concurrency = manifest.settings.concurrency

    if hf_downloads:
        def _dl_hf(entry: DownloadEntry) -> None:
            download_hf(entry, retry=retry, dry_run=dry_run)

        hf_results = run_parallel(
            _dl_hf, hf_downloads,
            max_workers=concurrency.hf_downloads,
            label="HF downloads",
        )
        if any(r is not None for r in hf_results):
            had_errors = True
            log.warning("Some HuggingFace downloads failed -- continuing anyway")

    if wget_downloads:
        def _dl_wget(entry: DownloadEntry) -> None:
            download_wget(entry, retry=retry, civitai_token=civitai_token, dry_run=dry_run)

        wget_results = run_parallel(
            _dl_wget, wget_downloads,
            max_workers=concurrency.wget_downloads,
            label="wget downloads",
        )
        if any(r is not None for r in wget_results):
            had_errors = True
            log.warning("Some wget downloads failed -- continuing anyway")

    if not hf_downloads and not wget_downloads:
        log.info("No downloads to process")

    # ---- Always-run phases (7-8): app must be registered even if downloads failed ----

    # Phase 7: Register supervisor services
    log.info("--- Phase 7: Supervisor services ---")
    try:
        register_services(manifest.services, dry_run=dry_run)
    except Exception as e:
        log.error("Service registration failed: %s", e)
        had_errors = True

    # Phase 8: Post commands
    log.info("--- Phase 8: Post commands ---")
    for cmd in manifest.post_commands:
        if dry_run:
            log.info("[DRY RUN] Would run: %s", cmd)
            continue
        log.info("Running: %s", cmd)
        result = subprocess.run(cmd, shell=True)
        if result.returncode != 0:
            log.error("Post command failed (exit %d): %s", result.returncode, cmd)
            had_errors = True

    if had_errors:
        log.warning("Provisioning finished with errors")
        return 1

    log.info("Provisioning complete!")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Declarative instance provisioner",
    )
    parser.add_argument(
        "manifest",
        help="Path to YAML manifest file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without executing",
    )

    args = parser.parse_args()

    # Set up basic logging before manifest is loaded
    setup_logging()

    sys.exit(run(args.manifest, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
