"""Thread pool management and file locking for parallel downloads."""

from __future__ import annotations

import fcntl
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

log = logging.getLogger("provisioner")


class FileLock:
    """Context manager for fcntl-based file locking.

    Prevents concurrent downloads of the same file across
    processes and threads.
    """

    def __init__(self, path: str, timeout: int = 300):
        self.lockfile = f"{path}.lock"
        self.timeout = timeout
        self._fd: int | None = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.lockfile), exist_ok=True)
        self._fd = os.open(self.lockfile, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        except OSError:
            os.close(self._fd)
            self._fd = None
            raise
        return self

    def __exit__(self, *exc):
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
        try:
            os.unlink(self.lockfile)
        except OSError:
            pass
        return False


def run_parallel(
    fn: Callable[..., Any],
    items: list[Any],
    max_workers: int,
    label: str = "tasks",
) -> list[Exception | None]:
    """Run fn(item) in parallel using a ThreadPoolExecutor.

    Returns a list of results: None for success, Exception for failure.
    The order matches the input items list.
    """
    if not items:
        return []

    log.info("Starting %d %s (max %d parallel)", len(items), label, max_workers)
    results: list[Exception | None] = [None] * len(items)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_idx = {pool.submit(fn, item): i for i, item in enumerate(items)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                future.result()
            except Exception as e:
                results[idx] = e
                log.error("Failed %s item %d: %s", label, idx, e)

    failed = sum(1 for r in results if r is not None)
    if failed:
        log.warning("%d/%d %s failed", failed, len(items), label)
    else:
        log.info("All %d %s completed successfully", len(items), label)

    return results
