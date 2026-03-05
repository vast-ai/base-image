"""APT package installer."""

from __future__ import annotations

import logging

from ..subprocess_runner import run_cmd

log = logging.getLogger("provisioner")


def install_apt_packages(packages: list[str], dry_run: bool = False) -> None:
    """Install apt packages.

    Runs apt-get update then apt-get install in a single invocation.
    """
    if not packages:
        log.info("No apt packages to install")
        return

    log.info("Installing %d apt packages: %s", len(packages), ", ".join(packages))

    if dry_run:
        log.info("[DRY RUN] Would install apt packages: %s", ", ".join(packages))
        return

    # Update package lists
    run_cmd(
        ["apt-get", "update", "-qq"],
        label="apt-update",
    )

    # Install packages
    run_cmd(
        ["apt-get", "install", "-y", "-qq", "--no-install-recommends"] + packages,
        label="apt",
    )

    log.info("APT packages installed successfully")
