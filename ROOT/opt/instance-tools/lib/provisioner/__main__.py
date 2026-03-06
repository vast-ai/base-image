"""CLI entry point for the declarative provisioner.

Usage:
    python -m provisioner [manifest.yaml | URL] [--dry-run] [--force]

    When called without a manifest argument, the provisioner checks for
    PROVISIONING_SCRIPT and runs only that.  If neither a manifest nor
    PROVISIONING_SCRIPT is set, it exits 0 silently.

Execution phases (sequential):
    1.  Load & validate manifest, expand env vars
    1b. Run extensions (append to manifest)          [fail-fast]
    2.  Validate auth tokens, resolve conditional_downloads
    2b. Write early files (write_files)              [fail-fast]
    3.  Install apt packages                         [fail-fast]
    4.  Clone git repos + post commands (parallel)   [fail-fast]
    5.  Install pip packages                         [fail-fast]
    5b. Install conda packages                       [fail-fast]
    6.  Download all files (parallel, two pools)     [fail-fast]
    7.  Register supervisor services                 [fail-fast]
    7b. Write late files (write_files_late)          [fail-fast]
    8.  Run post_commands                             [fail-fast]
    9.  Run PROVISIONING_SCRIPT (legacy script)      [fail-fast]

All phases are fail-fast: if any phase fails, later phases are
skipped and the provisioner exits 1.  If provisioning hasn't
produced the intended environment, it is marked as failed.

Each phase uses content-hash idempotency: if the inputs haven't
changed since the last successful run, the phase is skipped.
Use --force to clear state and re-run everything.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.parse

from .auth import validate_civitai_token, validate_hf_token
from .concurrency import cleanup_lockfiles, run_parallel
from .dedup import create_symlinks, dedup_downloads, dedup_git_repos
from .extensions import run_extensions
from .downloaders.huggingface import download_hf
from .downloaders.wget import download_wget
from .failure import handle_failure, notify_success
from .installers.apt import install_apt_packages
from .installers.files import write_files
from .installers.conda import install_conda_packages
from .installers.git import clone_git_repos
from .installers.pip import install_pip_packages
from .log import setup_logging
from .subprocess_runner import run_cmd
from .manifest import apply_env_conventions, apply_env_merge, load_manifest, resolve_conditionals, resolve_manifest_source
from .schema import CondaPackages, DownloadEntry, Manifest, PipPackages
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
        hostname = urllib.parse.urlparse(entry.url).hostname or ""
        if hostname == "huggingface.co" or hostname.endswith(".huggingface.co"):
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


def _conda_block_hash_data(block: CondaPackages, default_conda_env: str) -> str:
    """Serialize a conda block for hashing."""
    env = block.env or default_conda_env
    return json.dumps({
        "env": env,
        "packages": sorted(block.packages),
        "channels": sorted(block.channels),
        "args": block.args,
        "python": block.python,
    }, sort_keys=True)


def _apply_env_overrides(manifest: Manifest) -> None:
    """Apply PROVISIONER_* env var overrides to a loaded manifest.

    Env vars override manifest values, letting operators tune behavior
    without editing manifests.
    """
    val = os.environ.get("PROVISIONER_RETRY_MAX")
    if val is not None:
        try:
            manifest.on_failure.max_retries = int(val)
        except ValueError:
            log.warning("PROVISIONER_RETRY_MAX=%r is not a valid integer, ignoring", val)

    val = os.environ.get("PROVISIONER_RETRY_DELAY")
    if val is not None:
        try:
            manifest.on_failure.retry_delay = int(val)
        except ValueError:
            log.warning("PROVISIONER_RETRY_DELAY=%r is not a valid integer, ignoring", val)

    val = os.environ.get("PROVISIONER_FAILURE_ACTION")
    if val is not None:
        manifest.on_failure.action = val

    val = os.environ.get("PROVISIONER_WEBHOOK_URL")
    if val is not None:
        manifest.on_failure.webhook = val

    val = os.environ.get("PROVISIONER_WEBHOOK_ON_SUCCESS")
    if val is not None:
        manifest.on_failure.webhook_on_success = val.lower() in ("1", "true", "yes")

    val = os.environ.get("PROVISIONER_LOG_FILE")
    if val is not None:
        manifest.settings.log_file = val

    val = os.environ.get("PROVISIONER_VENV")
    if val is not None:
        manifest.settings.venv = val

    val = os.environ.get("PROVISIONER_CONDA_ENV")
    if val is not None:
        manifest.settings.conda_env = val


def run(manifest_path: str, manifest: Manifest, dry_run: bool = False, force: bool = False) -> int:
    """Execute the full provisioning pipeline.

    All phases are fail-fast: if any phase fails, later phases are
    skipped and the function returns 1 immediately.  If provisioning
    hasn't produced the intended environment, it should be marked
    as failed so the retry loop can re-attempt.

    Returns 0 on full success, 1 if anything failed.
    """

    # Set up logging with the manifest's log file
    setup_logging(manifest.settings.log_file)

    log.info("Provisioner starting (version 1)")

    if dry_run:
        log.info("=== DRY RUN MODE ===")

    if force:
        log.info("=== FORCE MODE: clearing all state ===")
        clear_all_state()

    # Phase 1b: Extensions (discovery -- may append to git_repos, downloads, etc.)
    # Extensions run first so they can populate the manifest before any
    # installation phase sees it.  Their only purpose is to append items
    # to existing manifest lists (downloads, git_repos, pip_packages, etc.).
    log.info("--- Phase 1b: Extensions ---")
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
            return 1

    # Phase 2: Validate auth tokens
    hf_token_env = manifest.auth.huggingface.token_env
    civitai_token_env = manifest.auth.civitai.token_env

    hf_valid = validate_hf_token(hf_token_env)
    civitai_valid = validate_civitai_token(civitai_token_env)

    # Resolve conditional downloads based on token validity
    resolve_conditionals(manifest, hf_token_valid=hf_valid, civitai_token_valid=civitai_valid)

    # Merge env var downloads
    apply_env_merge(manifest)

    # Merge PROVISIONING_* convention env vars
    apply_env_conventions(manifest)

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
            return 1

    # Phase 4: Clone git repos (parallel)
    log.info("--- Phase 4: Git repos ---")
    # Hash on original list so adding/removing a duplicate dest invalidates cache
    git_hash_data = json.dumps(
        sorted(
            [(r.url, r.dest, r.ref, r.recursive, r.pull_if_exists, r.post_commands) for r in manifest.git_repos],
            key=lambda t: t[0],
        )
    )
    git_hash = compute_stage_hash("git", git_hash_data)
    if not dry_run and is_stage_complete("git", git_hash):
        log.info("Git repos unchanged, skipping")
    else:
        # Dedup: resolve dest collisions and collapse duplicates
        unique_repos, git_symlinks = dedup_git_repos(manifest.git_repos)
        try:
            clone_git_repos(
                unique_repos,
                dry_run=dry_run,
            )
            create_symlinks(git_symlinks, dry_run=dry_run)
            if not dry_run:
                mark_stage_complete("git", git_hash)
        except Exception as e:
            log.error("Git clone failed: %s", e)
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
            return 1

    # Phase 5b: Install conda packages
    log.info("--- Phase 5b: Conda packages ---")
    conda_hash_data = json.dumps(
        [_conda_block_hash_data(b, manifest.settings.conda_env) for b in manifest.conda_packages],
        sort_keys=True,
    )
    conda_hash = compute_stage_hash("conda", conda_hash_data)
    if not dry_run and is_stage_complete("conda", conda_hash):
        log.info("Conda packages unchanged, skipping")
    else:
        try:
            for block in manifest.conda_packages:
                install_conda_packages(block, default_conda_env=manifest.settings.conda_env, dry_run=dry_run)
            if not dry_run:
                mark_stage_complete("conda", conda_hash)
        except Exception as e:
            log.error("Conda installation failed: %s", e)
            return 1

    # Phase 6: Download files (parallel, two pools)
    log.info("--- Phase 6: Downloads ---")
    # Hash on original list so adding/removing a duplicate dest invalidates cache
    dl_hash_data = json.dumps(
        sorted([(d.url, d.dest) for d in manifest.downloads], key=lambda t: t[0])
    )
    dl_hash = compute_stage_hash("downloads", dl_hash_data)
    if not dry_run and is_stage_complete("downloads", dl_hash):
        log.info("Downloads unchanged, skipping")
    else:
        # Dedup: same URL, multiple dests → download once, symlink the rest
        unique_downloads, dl_symlinks = dedup_downloads(manifest.downloads)
        hf_downloads, wget_downloads = _classify_downloads(unique_downloads)
        retry = manifest.settings.retry
        civitai_token = os.environ.get(civitai_token_env, "")
        concurrency = manifest.settings.concurrency
        dl_failed = False

        if hf_downloads:
            def _dl_hf(entry: DownloadEntry) -> None:
                download_hf(entry, retry=retry, dry_run=dry_run)

            hf_results = run_parallel(
                _dl_hf, hf_downloads,
                max_workers=concurrency.hf_downloads,
                label="HF downloads",
            )
            if any(r is not None for r in hf_results):
                dl_failed = True
                log.error("Some HuggingFace downloads failed")

        if not dl_failed and wget_downloads:
            def _dl_wget(entry: DownloadEntry) -> None:
                download_wget(entry, retry=retry, civitai_token=civitai_token, dry_run=dry_run)

            wget_results = run_parallel(
                _dl_wget, wget_downloads,
                max_workers=concurrency.wget_downloads,
                label="wget downloads",
            )
            if any(r is not None for r in wget_results):
                dl_failed = True
                log.error("Some wget downloads failed")

        if not hf_downloads and not wget_downloads:
            log.info("No downloads to process")

        if dl_failed:
            return 1

        create_symlinks(dl_symlinks, dry_run=dry_run)

        if not dry_run:
            mark_stage_complete("downloads", dl_hash)

    # Phase 7: Register supervisor services
    log.info("--- Phase 7: Supervisor services ---")
    svc_hash_data = json.dumps(
        sorted(
            [(s.name, s.command, s.venv, s.workdir, json.dumps(s.environment, sort_keys=True),
              s.portal_search_term, s.skip_on_serverless, s.wait_for_provisioning, s.pre_commands)
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
            return 1

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
            return 1

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
            try:
                run_cmd(cmd, shell=True, label="post_command")
            except subprocess.CalledProcessError as e:
                log.error("Post command failed (exit %d): %s", e.returncode, cmd)
                return 1

        if not dry_run:
            mark_stage_complete("post_commands", post_hash)

    # Phase 9: Run PROVISIONING_SCRIPT (legacy script support)
    script_url = os.environ.get("PROVISIONING_SCRIPT", "")
    if script_url:
        log.info("--- Phase 9: Provisioning script ---")
        script_hash = compute_stage_hash("provisioning_script", script_url)
        if not dry_run and is_stage_complete("provisioning_script", script_hash):
            log.info("Provisioning script unchanged, skipping")
        else:
            if dry_run:
                log.info("[DRY RUN] Would download and run PROVISIONING_SCRIPT: %s", script_url)
            else:
                # Download script if URL, otherwise use local path
                try:
                    script_path = resolve_manifest_source(script_url, cache_path="/provisioning.sh", label="script")
                except RuntimeError as e:
                    log.error("Failed to download provisioning script: %s", e)
                    return 1

                # Prep: dos2unix + chmod (match legacy behavior)
                if shutil.which("dos2unix"):
                    subprocess.run(["dos2unix", script_path], capture_output=True)
                os.chmod(script_path, 0o755)

                # Execute and stream output through logger
                log.info("Running provisioning script: %s", script_path)
                try:
                    run_cmd([script_path], label="script")
                except subprocess.CalledProcessError as e:
                    log.error("Provisioning script failed (exit %d)", e.returncode)
                    return 1

                mark_stage_complete("provisioning_script", script_hash)

    # Cleanup: remove .lock files left by download file locking
    if not dry_run:
        lock_paths = [d.dest for d in manifest.downloads if d.dest and not d.dest.endswith("/")]
        cleanup_lockfiles(lock_paths)

    log.info("Provisioning complete!")
    return 0


def run_with_retries(manifest_source: str | None = None, dry_run: bool = False, force: bool = False) -> int:
    """Load manifest, apply env overrides, and run with retry loop.

    *manifest_source* may be a local file path, an HTTP(S) URL, or None.
    If a URL, the manifest is downloaded first (with retries).
    If None and PROVISIONING_SCRIPT is set, a default Manifest is used
    (script-only mode).  If neither is set, returns 0 immediately.

    The provisioner always retries on failure (default 3 retries, 30s delay).
    After retries are exhausted, on_failure.action controls post-exhaustion
    behavior: continue (default), stop, or destroy.

    Returns 0 on success, 1 on failure.
    """
    # Script-only mode: no manifest, just PROVISIONING_SCRIPT
    if manifest_source is None:
        if not os.environ.get("PROVISIONING_SCRIPT"):
            setup_logging()
            log.info("No manifest and no PROVISIONING_SCRIPT set, nothing to do")
            return 0

        # Use a default manifest with env overrides for retry/failure settings
        manifest = Manifest()
        _apply_env_overrides(manifest)
        manifest_path = "(script-only)"

        max_retries = manifest.on_failure.max_retries
        retry_delay = manifest.on_failure.retry_delay

        if dry_run or max_retries == 0:
            rc = run(manifest_path, manifest, dry_run=dry_run, force=force)
            if rc == 0 and not dry_run:
                notify_success(manifest.on_failure, manifest_path)
            elif rc != 0 and not dry_run:
                handle_failure(manifest.on_failure, manifest_path, error="provisioning script failed")
            return rc

        for attempt in range(1, max_retries + 1):
            rc = run(manifest_path, manifest, dry_run=dry_run, force=force)
            if rc == 0:
                notify_success(manifest.on_failure, manifest_path)
                return 0
            if attempt < max_retries:
                log.warning("Attempt %d/%d failed, retrying in %ds...", attempt, max_retries, retry_delay)
                time.sleep(retry_delay)
            else:
                log.error("All %d attempts exhausted", max_retries)

        handle_failure(manifest.on_failure, manifest_path, error="provisioning script failed after all retries")
        return 1

    # Resolve URL → local file (or pass through if already a path)
    try:
        manifest_path = resolve_manifest_source(manifest_source)
    except RuntimeError as e:
        setup_logging()
        log.error("%s", e)
        return 1

    # Load manifest
    try:
        manifest = load_manifest(manifest_path)
    except Exception as e:
        setup_logging()
        log.error("Failed to load manifest: %s", e)
        return 1

    # Apply env var overrides
    _apply_env_overrides(manifest)

    max_retries = manifest.on_failure.max_retries
    retry_delay = manifest.on_failure.retry_delay

    # Skip retry loop for dry-run or max_retries=0
    if dry_run or max_retries == 0:
        rc = run(manifest_path, manifest, dry_run=dry_run, force=force)
        if rc == 0 and not dry_run:
            notify_success(manifest.on_failure, manifest_path)
        elif rc != 0 and not dry_run:
            handle_failure(manifest.on_failure, manifest_path, error="provisioning failed")
        return rc

    # Retry loop
    for attempt in range(1, max_retries + 1):
        rc = run(manifest_path, manifest, dry_run=dry_run, force=force)
        if rc == 0:
            notify_success(manifest.on_failure, manifest_path)
            return 0

        if attempt < max_retries:
            log.warning("Attempt %d/%d failed, retrying in %ds...", attempt, max_retries, retry_delay)
            time.sleep(retry_delay)
        else:
            log.error("All %d attempts exhausted", max_retries)

    # All retries exhausted — dispatch failure action
    handle_failure(manifest.on_failure, manifest_path, error="provisioning failed after all retries")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Declarative instance provisioner",
    )
    parser.add_argument(
        "manifest",
        nargs="?",
        default=None,
        help="Path to YAML manifest file or HTTP(S) URL (optional if PROVISIONING_SCRIPT is set)",
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

    sys.exit(run_with_retries(args.manifest, dry_run=args.dry_run, force=args.force))


if __name__ == "__main__":
    main()
