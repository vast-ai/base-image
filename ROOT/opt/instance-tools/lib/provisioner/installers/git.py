"""Git repository cloning and setup."""

from __future__ import annotations

import logging
import os
import subprocess

from ..schema import GitRepo

log = logging.getLogger("provisioner")


def _clone_single(repo: GitRepo, dry_run: bool = False) -> None:
    """Clone or update a single git repository, then run post commands."""
    dest = repo.dest

    if dry_run:
        log.info("[DRY RUN] Would clone %s -> %s", repo.url, dest)
        if repo.post_commands:
            for cmd in repo.post_commands:
                log.info("[DRY RUN] Would run post command in %s: %s", dest, cmd)
        return

    if os.path.isdir(dest):
        if repo.pull_if_exists:
            log.info("Pulling existing repo: %s", dest)
            subprocess.run(["git", "-C", dest, "pull"], check=True)
        else:
            log.info("Repo already exists: %s (skipping)", dest)
            return
    else:
        log.info("Cloning %s -> %s", repo.url, dest)
        cmd = ["git", "clone"]
        if repo.recursive:
            cmd.append("--recursive")
        cmd.extend([repo.url, dest])
        subprocess.run(cmd, check=True)

    # Checkout specific ref if specified
    if repo.ref:
        log.info("Checking out ref: %s", repo.ref)
        subprocess.run(["git", "-C", dest, "checkout", repo.ref], check=True)

    # Run post-clone commands
    if repo.post_commands:
        for cmd in repo.post_commands:
            log.info("Running post command in %s: %s", dest, cmd)
            subprocess.run(cmd, shell=True, cwd=dest, check=True)

    log.info("Git repo ready: %s", dest)


def clone_git_repos(
    repos: list[GitRepo],
    max_workers: int = 4,
    dry_run: bool = False,
) -> None:
    """Clone multiple git repositories in parallel.

    Each repo's post_commands run immediately after its clone+checkout,
    within the parallel pool.
    """
    if not repos:
        log.info("No git repos to clone")
        return

    from ..concurrency import run_parallel

    def _clone(repo: GitRepo) -> None:
        _clone_single(repo, dry_run=dry_run)

    results = run_parallel(_clone, repos, max_workers=max_workers, label="git clones")
    failed = [r for r in results if r is not None]
    if failed:
        raise RuntimeError(f"{len(failed)} git clone(s) failed")
