"""Extension loader and runner for the provisioner.

Extensions are Python modules that implement a ``run()`` function.
They are invoked in phase 1b, immediately after the manifest is loaded.
Their sole purpose is to append items to the manifest's lists (downloads,
git_repos, pip_packages, etc.) so that later phases handle the actual work.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass

from .schema import Extension, Manifest

log = logging.getLogger("provisioner")


@dataclass
class ExtensionContext:
    """Context passed to each extension's ``run()`` function."""

    manifest: Manifest
    log: logging.Logger


def run_extensions(
    extensions: list[Extension],
    manifest: Manifest,
    dry_run: bool = False,
) -> None:
    """Import and execute each enabled extension module.

    Raises on the first fatal error so the caller can set ``had_errors``.
    """
    for ext in extensions:
        if not ext.enabled:
            log.info("Extension %s disabled, skipping", ext.module)
            continue

        if not ext.module:
            log.warning("Extension entry with empty module, skipping")
            continue

        if dry_run:
            log.info("[DRY RUN] Would run extension %s with config: %s", ext.module, ext.config)
            continue

        log.info("Running extension %s", ext.module)
        try:
            mod = importlib.import_module(ext.module)
        except ImportError as e:
            raise RuntimeError(f"Extension module '{ext.module}' not found: {e}") from e

        if not hasattr(mod, "run"):
            raise RuntimeError(
                f"Extension module '{ext.module}' has no run() function"
            )

        ctx = ExtensionContext(
            manifest=manifest,
            log=logging.getLogger(f"provisioner.ext.{ext.module}"),
        )
        mod.run(config=ext.config, context=ctx, dry_run=dry_run)
        log.info("Extension %s completed", ext.module)
