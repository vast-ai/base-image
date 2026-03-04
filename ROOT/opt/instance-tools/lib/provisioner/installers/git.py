"""Git repository cloning and setup."""

from __future__ import annotations

import logging
import os
import subprocess

from ..schema import GitRepo, PipPackages

log = logging.getLogger("provisioner")


def _clone_single(repo: GitRepo, venv: str, dry_run: bool = False) -> None:
    """Clone or update a single git repository."""
    dest = repo.dest

    if dry_run:
        log.info("[DRY RUN] Would clone %s -> %s", repo.url, dest)
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

    # Install requirements if specified
    if repo.requirements:
        req_path = os.path.join(dest, repo.requirements)
        if os.path.isfile(req_path):
            log.info("Installing requirements: %s", req_path)
            from .pip import install_pip_packages
            install_pip_packages(
                PipPackages(requirements=[req_path]),
                venv=venv,
            )
        else:
            log.warning("Requirements file not found: %s", req_path)

    # Editable install
    if repo.pip_install_editable:
        log.info("Installing %s in editable mode", dest)
        python_bin = f"{venv}/bin/python"
        subprocess.run(
            ["uv", "pip", "install", "--python", python_bin, "-e", dest],
            check=True,
        )

    log.info("Git repo ready: %s", dest)


def clone_git_repos(
    repos: list[GitRepo],
    venv: str,
    max_workers: int = 4,
    dry_run: bool = False,
) -> None:
    """Clone multiple git repositories in parallel."""
    if not repos:
        log.info("No git repos to clone")
        return

    from ..concurrency import run_parallel

    def _clone(repo: GitRepo) -> None:
        _clone_single(repo, venv=venv, dry_run=dry_run)

    results = run_parallel(_clone, repos, max_workers=max_workers, label="git clones")
    failed = [r for r in results if r is not None]
    if failed:
        raise RuntimeError(f"{len(failed)} git clone(s) failed")
