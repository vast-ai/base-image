"""CLI entry point for the declarative provisioner.

Usage:
    python -m provisioner manifest.yaml [--dry-run] [--force]

Execution phases (sequential):
    1. Load & validate manifest, expand env vars
    2. Validate auth tokens, resolve conditional_downloads
    2b. Write early files (write_files)              [fail-fast]
    3. Install apt packages                          [fail-fast]
    3b. Run extensions (discover repos/downloads)    [fail-fast]
    4. Clone git repos + post commands (parallel)    [fail-fast]
    5. Install pip packages                          [fail-fast]
    5b. Install conda packages                       [fail-fast]
    6. Download all files (parallel, two pools)      [best-effort]
    7. Register supervisor services                  [always runs]
    7b. Write late files (write_files_late)          [always runs]
    8. Run post_commands                             [always runs]

Phases 3-5b are fail-fast: if any fails, later phases are skipped
and the provisioner exits 1.  Phases 6-8 always run even if an
earlier best-effort phase had failures.  The exit code is non-zero
when anything failed, so the boot script knows to retry on next start.

Each phase uses content-hash idempotency: if the inputs haven't
changed since the last successful run, the phase is skipped.
Use --force to clear state and re-run everything.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys

from .auth import validate_civitai_token, validate_hf_token
from .concurrency import run_parallel
from .extensions import run_extensions
from .downloaders.huggingface import download_hf
from .downloaders.wget import download_wget
from .failure import handle_failure
from .installers.apt import install_apt_packages
from .installers.files import write_files
from .installers.conda import install_conda_packages
from .installers.git import clone_git_repos
from .installers.pip import install_pip_packages
from .log import setup_logging
from .manifest import apply_env_merge, load_manifest, resolve_conditionals
from .schema import DownloadEntry, PipPackages
from .state import clear_all_state, compute_stage_hash, is_stage_complete, mark_stage_complete
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


def _pip_block_hash_data(block: PipPackages, default_venv: str) -> str:
    """Serialize a pip block for hashing."""
    venv = block.venv or default_venv
    return json.dumps({
        "venv": venv,
        "tool": block.tool,
        "packages": sorted(block.packages),
        "args": block.args,
        "requirements": sorted(block.requirements),
        "python": block.python,
    }, sort_keys=True)


def run(manifest_path: str, dry_run: bool = False, force: bool = False) -> int:
    """Execute the full provisioning pipeline.

    Phases 3-5b (apt/extensions/git/pip/conda) are fail-fast: a failure
    skips remaining phases and returns 1 immediately -- the app can't
    run without its dependencies.

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

    if dry_run:
        log.info("=== DRY RUN MODE ===")

    if force:
        log.info("=== FORCE MODE: clearing all state ===")
        clear_all_state()

    # Phase 2: Validate auth tokens
    hf_token_env = manifest.auth.huggingface.token_env
    civitai_token_env = manifest.auth.civitai.token_env

    hf_valid = validate_hf_token(hf_token_env)
    civitai_valid = validate_civitai_token(civitai_token_env)

    # Resolve conditional downloads based on token validity
    resolve_conditionals(manifest, hf_token_valid=hf_valid, civitai_token_valid=civitai_valid)

    # Merge env var downloads
    apply_env_merge(manifest)

    # Phase 2b: Write early files
    log.info("--- Phase 2b: Early file writes ---")
    wf_hash_data = json.dumps(
        [(f.path, f.content, f.permissions, f.owner) for f in manifest.write_files]
    )
    wf_hash = compute_stage_hash("write_files", wf_hash_data)
    if not dry_run and is_stage_complete("write_files", wf_hash):
        log.info("Early files unchanged, skipping")
    else:
        try:
            write_files(manifest.write_files, label="write_files", dry_run=dry_run)
            if not dry_run:
                mark_stage_complete("write_files", wf_hash)
        except Exception as e:
            log.error("Early file write failed: %s", e)
            handle_failure(manifest.on_failure, manifest_path, error=str(e))
            return 1

    # ---- Fail-fast phases (3-5): dependencies required for the app ----

    # Phase 3: Install apt packages
    log.info("--- Phase 3: APT packages ---")
    apt_hash_data = json.dumps(sorted(manifest.apt_packages))
    apt_hash = compute_stage_hash("apt", apt_hash_data)
    if not dry_run and is_stage_complete("apt", apt_hash):
        log.info("APT packages unchanged, skipping")
    else:
        try:
            install_apt_packages(manifest.apt_packages, dry_run=dry_run)
            if not dry_run:
                mark_stage_complete("apt", apt_hash)
        except Exception as e:
            log.error("APT installation failed: %s", e)
            handle_failure(manifest.on_failure, manifest_path, error=str(e))
            return 1

    # Phase 3b: Extensions (discovery -- may append to git_repos, downloads, etc.)
    log.info("--- Phase 3b: Extensions ---")
    ext_hash_data = json.dumps(
        [{"module": e.module, "config": e.config, "enabled": e.enabled} for e in manifest.extensions],
        sort_keys=True,
    )
    ext_hash = compute_stage_hash("extensions", ext_hash_data)
    if not dry_run and is_stage_complete("extensions", ext_hash):
        log.info("Extensions unchanged, skipping")
    else:
        try:
            run_extensions(manifest.extensions, manifest=manifest, dry_run=dry_run)
            if not dry_run:
                mark_stage_complete("extensions", ext_hash)
        except Exception as e:
            log.error("Extension failed: %s", e)
            handle_failure(manifest.on_failure, manifest_path, error=str(e))
            return 1

    # Phase 4: Clone git repos (parallel)
    log.info("--- Phase 4: Git repos ---")
    git_hash_data = json.dumps(
        sorted(
            [(r.url, r.ref, r.recursive, r.pull_if_exists, r.post_commands) for r in manifest.git_repos],
            key=lambda t: t[0],
        )
    )
    git_hash = compute_stage_hash("git", git_hash_data)
    if not dry_run and is_stage_complete("git", git_hash):
        log.info("Git repos unchanged, skipping")
    else:
        try:
            clone_git_repos(
                manifest.git_repos,
                dry_run=dry_run,
            )
            if not dry_run:
                mark_stage_complete("git", git_hash)
        except Exception as e:
            log.error("Git clone failed: %s", e)
            handle_failure(manifest.on_failure, manifest_path, error=str(e))
            return 1

    # Phase 5: Install pip packages
    log.info("--- Phase 5: Pip packages ---")
    pip_hash_data = json.dumps(
        [_pip_block_hash_data(b, manifest.settings.venv) for b in manifest.pip_packages],
        sort_keys=True,
    )
    pip_hash = compute_stage_hash("pip", pip_hash_data)
    if not dry_run and is_stage_complete("pip", pip_hash):
        log.info("Pip packages unchanged, skipping")
    else:
        try:
            for block in manifest.pip_packages:
                install_pip_packages(
                    block,
                    default_venv=manifest.settings.venv,
                    dry_run=dry_run,
                )
            if not dry_run:
                mark_stage_complete("pip", pip_hash)
        except Exception as e:
            log.error("Pip installation failed: %s", e)
            handle_failure(manifest.on_failure, manifest_path, error=str(e))
            return 1

    # Phase 5b: Install conda packages
    log.info("--- Phase 5b: Conda packages ---")
    conda_hash_data = json.dumps({
        "packages": sorted(manifest.conda_packages.packages),
        "channels": sorted(manifest.conda_packages.channels),
        "args": manifest.conda_packages.args,
        "env": manifest.conda_packages.env,
        "python": manifest.conda_packages.python,
    }, sort_keys=True)
    conda_hash = compute_stage_hash("conda", conda_hash_data)
    if not dry_run and is_stage_complete("conda", conda_hash):
        log.info("Conda packages unchanged, skipping")
    else:
        try:
            install_conda_packages(manifest.conda_packages, dry_run=dry_run)
            if not dry_run:
                mark_stage_complete("conda", conda_hash)
        except Exception as e:
            log.error("Conda installation failed: %s", e)
            handle_failure(manifest.on_failure, manifest_path, error=str(e))
            return 1

    # ---- Best-effort phase (6): downloads may fail without breaking the app ----

    # Phase 6: Download files (parallel, two pools)
    log.info("--- Phase 6: Downloads ---")
    dl_hash_data = json.dumps(
        sorted([(d.url, d.dest) for d in manifest.downloads], key=lambda t: t[0])
    )
    dl_hash = compute_stage_hash("downloads", dl_hash_data)
    if not dry_run and is_stage_complete("downloads", dl_hash):
        log.info("Downloads unchanged, skipping")
    else:
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

        # Only mark complete if no download errors
        if not had_errors and not dry_run:
            mark_stage_complete("downloads", dl_hash)

    # ---- Always-run phases (7-8): app must be registered even if downloads failed ----

    # Phase 7: Register supervisor services
    log.info("--- Phase 7: Supervisor services ---")
    svc_hash_data = json.dumps(
        sorted(
            [(s.name, s.command, s.venv, s.workdir, json.dumps(s.environment, sort_keys=True))
             for s in manifest.services],
            key=lambda t: t[0],
        )
    )
    svc_hash = compute_stage_hash("services", svc_hash_data)
    if not dry_run and is_stage_complete("services", svc_hash):
        log.info("Services unchanged, skipping")
    else:
        try:
            register_services(manifest.services, dry_run=dry_run)
            if not dry_run:
                mark_stage_complete("services", svc_hash)
        except Exception as e:
            log.error("Service registration failed: %s", e)
            had_errors = True

    # Phase 7b: Write late files
    log.info("--- Phase 7b: Late file writes ---")
    wfl_hash_data = json.dumps(
        [(f.path, f.content, f.permissions, f.owner) for f in manifest.write_files_late]
    )
    wfl_hash = compute_stage_hash("write_files_late", wfl_hash_data)
    if not dry_run and is_stage_complete("write_files_late", wfl_hash):
        log.info("Late files unchanged, skipping")
    else:
        try:
            write_files(manifest.write_files_late, label="write_files_late", dry_run=dry_run)
            if not dry_run:
                mark_stage_complete("write_files_late", wfl_hash)
        except Exception as e:
            log.error("Late file write failed: %s", e)
            had_errors = True

    # Phase 8: Post commands
    log.info("--- Phase 8: Post commands ---")
    post_hash_data = json.dumps(manifest.post_commands)
    post_hash = compute_stage_hash("post_commands", post_hash_data)
    if not dry_run and is_stage_complete("post_commands", post_hash):
        log.info("Post commands unchanged, skipping")
    else:
        for cmd in manifest.post_commands:
            if dry_run:
                log.info("[DRY RUN] Would run: %s", cmd)
                continue
            log.info("Running: %s", cmd)
            result = subprocess.run(cmd, shell=True)
            if result.returncode != 0:
                log.error("Post command failed (exit %d): %s", result.returncode, cmd)
                had_errors = True

        if not had_errors and not dry_run:
            mark_stage_complete("post_commands", post_hash)

    if had_errors:
        log.warning("Provisioning finished with errors")
        handle_failure(manifest.on_failure, manifest_path, error="provisioning finished with errors")
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Clear cached state and re-run all phases",
    )

    args = parser.parse_args()

    # Set up basic logging before manifest is loaded
    setup_logging()

    sys.exit(run(args.manifest, dry_run=args.dry_run, force=args.force))


if __name__ == "__main__":
    main()
