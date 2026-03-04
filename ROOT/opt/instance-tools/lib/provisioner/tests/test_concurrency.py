"""Tests for provisioner.concurrency -- FileLock and run_parallel."""

from __future__ import annotations

import os
import threading
import time

import pytest

from provisioner.concurrency import FileLock, run_parallel


class TestFileLock:
    def test_lock_creates_lockfile(self, tmp_path):
        target = str(tmp_path / "testfile.bin")
        lockfile = f"{target}.lock"

        with FileLock(target):
            assert os.path.exists(lockfile)

        # Lockfile intentionally NOT removed (avoids race condition)
        assert os.path.exists(lockfile)

    def test_lock_is_exclusive(self, tmp_path):
        """Two threads competing for the same lock: only one holds it at a time."""
        target = str(tmp_path / "shared.bin")
        results = []
        barrier = threading.Barrier(2)

        def worker(worker_id):
            barrier.wait()  # synchronize start
            with FileLock(target):
                results.append(f"enter-{worker_id}")
                time.sleep(0.05)
                results.append(f"exit-{worker_id}")

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # One worker must fully complete before the other starts
        assert len(results) == 4
        # Either [enter-1, exit-1, enter-2, exit-2] or [enter-2, exit-2, enter-1, exit-1]
        assert results[0].startswith("enter-")
        assert results[1].startswith("exit-")
        assert results[2].startswith("enter-")
        assert results[3].startswith("exit-")
        # The first exit and second enter must be different workers
        first_worker = results[0].split("-")[1]
        assert results[1] == f"exit-{first_worker}"

    def test_lock_creates_parent_dirs(self, tmp_path):
        target = str(tmp_path / "deep" / "nested" / "file.bin")
        with FileLock(target):
            pass
        # Should not raise, parent dirs created

    def test_lock_exception_cleanup(self, tmp_path):
        target = str(tmp_path / "failfile.bin")
        lockfile = f"{target}.lock"

        with pytest.raises(RuntimeError):
            with FileLock(target):
                raise RuntimeError("intentional")

        # Lock is released but file persists (avoids race condition)
        assert os.path.exists(lockfile)


class TestRunParallel:
    def test_empty_list(self):
        results = run_parallel(lambda x: None, [], max_workers=2)
        assert results == []

    def test_all_succeed(self):
        items = [1, 2, 3, 4]
        collected = []

        def fn(x):
            collected.append(x)

        results = run_parallel(fn, items, max_workers=2, label="test")
        assert all(r is None for r in results)
        assert sorted(collected) == [1, 2, 3, 4]

    def test_some_fail(self):
        def fn(x):
            if x == 2:
                raise ValueError("boom")

        results = run_parallel(fn, [1, 2, 3], max_workers=2, label="test")
        assert results[0] is None
        assert isinstance(results[1], ValueError)
        assert results[2] is None

    def test_all_fail(self):
        def fn(x):
            raise RuntimeError(f"fail-{x}")

        results = run_parallel(fn, [1, 2], max_workers=2, label="test")
        assert all(isinstance(r, RuntimeError) for r in results)

    def test_preserves_order(self):
        """Results list matches input order regardless of completion order."""
        results_order = []

        def fn(x):
            time.sleep(0.01 * (3 - x))  # item 0 finishes last
            results_order.append(x)

        results = run_parallel(fn, [0, 1, 2], max_workers=3, label="test")
        assert all(r is None for r in results)
        assert len(results) == 3

    def test_respects_max_workers(self):
        """At most max_workers run concurrently."""
        concurrent = []
        max_concurrent = []
        lock = threading.Lock()

        def fn(x):
            with lock:
                concurrent.append(1)
                max_concurrent.append(len(concurrent))
            time.sleep(0.05)
            with lock:
                concurrent.pop()

        run_parallel(fn, list(range(6)), max_workers=2, label="test")
        assert max(max_concurrent) <= 2

    def test_single_item(self):
        called_with = []

        def fn(x):
            called_with.append(x)

        results = run_parallel(fn, ["only"], max_workers=4, label="test")
        assert results == [None]
        assert called_with == ["only"]
