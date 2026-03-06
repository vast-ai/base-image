"""Deduplicate downloads and git repos — download/clone once, symlink duplicates."""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict

from .schema import DownloadEntry, GitRepo

log = logging.getLogger("provisioner")


def dedup_downloads(
    downloads: list[DownloadEntry],
) -> tuple[list[DownloadEntry], list[tuple[str, str]]]:
    """Deduplicate download entries by URL.

    Returns (unique_downloads, symlinks) where symlinks is a list of
    (source_path, dest_path) pairs to create after downloads complete.

    Entries with dest ending in '/' are excluded from dedup because
    the final filename isn't resolved yet.
    """
    unique: list[DownloadEntry] = []
    symlinks: list[tuple[str, str]] = []
    # url -> first entry's dest
    seen: dict[str, str] = {}

    for entry in downloads:
        # Skip dedup for entries where dest is a directory (filename unknown)
        # or empty (HF cache mode — no local path to symlink)
        if not entry.dest or entry.dest.endswith("/"):
            unique.append(entry)
            continue

        if entry.url not in seen:
            seen[entry.url] = entry.dest
            unique.append(entry)
        elif entry.dest == seen[entry.url]:
            # Exact duplicate — drop silently
            log.debug("Dropping duplicate download: %s -> %s", entry.url, entry.dest)
        else:
            # Same URL, different dest — symlink
            primary_dest = seen[entry.url]
            symlinks.append((primary_dest, entry.dest))
            log.info(
                "Dedup: will symlink %s -> %s (same URL: %s)",
                entry.dest, primary_dest, entry.url,
            )

    if symlinks:
        log.info(
            "Download dedup: %d unique, %d symlinks (from %d total)",
            len(unique), len(symlinks), len(downloads),
        )

    return unique, symlinks


def _sanitize_ref(ref: str) -> str:
    """Sanitize a git ref for use in a directory name."""
    return re.sub(r"[/\\]", "-", ref)


def dedup_git_repos(
    repos: list[GitRepo],
) -> tuple[list[GitRepo], list[tuple[str, str]]]:
    """Deduplicate git repo entries and resolve dest collisions.

    Step 1: Resolve dest collisions — entries sharing the same dest but
    with different (url, ref) get the later entry's dest mangled by
    appending '--{sanitized_ref}'.

    Step 2: Dedup by (url, ref) — first occurrence is the primary,
    subsequent entries with different dest and no post_commands become
    symlinks. Entries with post_commands are kept as independent clones.

    Returns (unique_repos, symlinks).
    """
    # --- Step 1: Resolve dest collisions ---
    # dest -> (url, ref) of first occupant
    dest_owners: dict[str, tuple[str, str]] = {}
    resolved: list[GitRepo] = []

    for repo in repos:
        key = (repo.url, repo.ref)
        dest = repo.dest

        if dest in dest_owners:
            if dest_owners[dest] != key:
                # Different (url, ref) wants the same dest — mangle
                sanitized = _sanitize_ref(repo.ref) if repo.ref else "default"
                new_dest = f"{dest}--{sanitized}"
                log.warning(
                    "Git dest collision: %s claimed by (%s, ref=%s) and (%s, ref=%s). "
                    "Mangling later entry dest to %s",
                    dest, dest_owners[dest][0], dest_owners[dest][1],
                    repo.url, repo.ref, new_dest,
                )
                repo = GitRepo(
                    url=repo.url, dest=new_dest, ref=repo.ref,
                    recursive=repo.recursive, pull_if_exists=repo.pull_if_exists,
                    post_commands=list(repo.post_commands),
                )
                dest_owners[new_dest] = key
            # else: same (url, ref) same dest — will be deduped in step 2
        else:
            dest_owners[dest] = key

        resolved.append(repo)

    # --- Step 2: Dedup by (url, ref) ---
    unique: list[GitRepo] = []
    symlinks: list[tuple[str, str]] = []
    # (url, ref) -> primary dest
    seen: dict[tuple[str, str], str] = {}

    for repo in resolved:
        key = (repo.url, repo.ref)

        if key not in seen:
            seen[key] = repo.dest
            unique.append(repo)
        elif repo.dest == seen[key]:
            # Exact duplicate — drop, but merge post_commands if primary has none
            primary = next(r for r in unique if (r.url, r.ref) == key and r.dest == seen[key])
            if repo.post_commands and not primary.post_commands:
                primary.post_commands = list(repo.post_commands)
                log.debug("Merged post_commands from duplicate into primary: %s", repo.dest)
            else:
                log.debug("Dropping duplicate git repo: %s -> %s", repo.url, repo.dest)
        else:
            # Same (url, ref), different dest
            if repo.post_commands:
                # Can't symlink — post_commands need to run in the directory
                unique.append(repo)
                log.info(
                    "Keeping independent clone %s (has post_commands, same repo as %s)",
                    repo.dest, seen[key],
                )
            else:
                primary_dest = seen[key]
                symlinks.append((primary_dest, repo.dest))
                log.info(
                    "Dedup: will symlink %s -> %s (same repo: %s ref=%s)",
                    repo.dest, primary_dest, repo.url, repo.ref,
                )

    if symlinks:
        log.info(
            "Git dedup: %d unique, %d symlinks (from %d total)",
            len(unique), len(symlinks), len(repos),
        )

    return unique, symlinks


def create_symlinks(
    symlinks: list[tuple[str, str]],
    dry_run: bool = False,
) -> None:
    """Create symlinks from (source, dest) pairs.

    Idempotent: skips if dest already exists.
    Warns if source doesn't exist (e.g. download failed).
    """
    for src, dest in symlinks:
        if dry_run:
            log.info("[DRY RUN] Would symlink %s -> %s", dest, src)
            continue

        if os.path.exists(dest) or os.path.islink(dest):
            log.debug("Symlink dest already exists, skipping: %s", dest)
            continue

        if not os.path.exists(src):
            log.warning("Symlink source does not exist, skipping: %s -> %s", dest, src)
            continue

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.symlink(src, dest)
        log.info("Created symlink: %s -> %s", dest, src)
