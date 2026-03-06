"""Tests for provisioner.state -- stage hash computation and persistence."""

from __future__ import annotations

import os

import pytest

from provisioner.state import (
    STATE_DIR,
    clear_all_state,
    compute_stage_hash,
    is_stage_complete,
    mark_stage_complete,
)


@pytest.fixture(autouse=True)
def _use_tmp_state_dir(tmp_path, monkeypatch):
    """Redirect STATE_DIR to a temp directory for all tests."""
    state_dir = str(tmp_path / "provisioner_state")
    monkeypatch.setattr("provisioner.state.STATE_DIR", state_dir)
    return state_dir


class TestComputeStageHash:
    def test_deterministic(self):
        h1 = compute_stage_hash("apt", '["curl", "vim"]')
        h2 = compute_stage_hash("apt", '["curl", "vim"]')
        assert h1 == h2

    def test_different_data_different_hash(self):
        h1 = compute_stage_hash("apt", '["curl"]')
        h2 = compute_stage_hash("apt", '["vim"]')
        assert h1 != h2

    def test_different_stage_different_hash(self):
        h1 = compute_stage_hash("apt", '["curl"]')
        h2 = compute_stage_hash("pip", '["curl"]')
        assert h1 != h2

    def test_returns_hex_string(self):
        h = compute_stage_hash("test", "data")
        assert len(h) == 64  # SHA-256 hex length
        assert all(c in "0123456789abcdef" for c in h)


class TestIsStageComplete:
    def test_not_complete_when_no_file(self):
        assert is_stage_complete("apt", "somehash") is False

    def test_complete_when_hash_matches(self, _use_tmp_state_dir):
        mark_stage_complete("apt", "abc123")
        assert is_stage_complete("apt", "abc123") is True

    def test_not_complete_when_hash_differs(self, _use_tmp_state_dir):
        mark_stage_complete("apt", "abc123")
        assert is_stage_complete("apt", "def456") is False

    def test_different_stages_independent(self, _use_tmp_state_dir):
        mark_stage_complete("apt", "hash1")
        mark_stage_complete("pip", "hash2")
        assert is_stage_complete("apt", "hash1") is True
        assert is_stage_complete("pip", "hash2") is True
        assert is_stage_complete("apt", "hash2") is False


class TestMarkStageComplete:
    def test_creates_state_dir(self, _use_tmp_state_dir):
        mark_stage_complete("test", "hash")
        assert os.path.isdir(_use_tmp_state_dir)

    def test_writes_hash_file(self, _use_tmp_state_dir):
        mark_stage_complete("apt", "myhash")
        hash_file = os.path.join(_use_tmp_state_dir, "apt.hash")
        assert os.path.isfile(hash_file)
        with open(hash_file) as f:
            assert f.read() == "myhash"

    def test_overwrites_existing(self, _use_tmp_state_dir):
        mark_stage_complete("apt", "old")
        mark_stage_complete("apt", "new")
        assert is_stage_complete("apt", "new") is True
        assert is_stage_complete("apt", "old") is False


class TestClearAllState:
    def test_clears_existing_state(self, _use_tmp_state_dir):
        mark_stage_complete("apt", "hash1")
        mark_stage_complete("pip", "hash2")
        clear_all_state()
        assert not os.path.isdir(_use_tmp_state_dir)
        assert is_stage_complete("apt", "hash1") is False
        assert is_stage_complete("pip", "hash2") is False

    def test_noop_when_no_state(self, _use_tmp_state_dir):
        # Should not raise
        clear_all_state()
