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


class TestStateDirOverride:
    """PROVISIONER_STATE_DIR is a PRODUCTION knob, not a test-only seam.

    It was added so base/13-provisioner-selftest.sh could run the real
    provisioner at boot stage 70 without marking stages complete that the
    customer's own run at stage 75 has not performed. But every provisioner
    invocation honours it, and `clear_all_state()` does `shutil.rmtree` on it —
    so an unvalidated override makes `PROVISIONER_STATE_DIR=/etc provisioner
    --force` a recursive delete of /etc as root.
    """

    @staticmethod
    def _resolve(monkeypatch, value):
        import provisioner.state as st
        if value is None:
            monkeypatch.delenv("PROVISIONER_STATE_DIR", raising=False)
        else:
            monkeypatch.setenv("PROVISIONER_STATE_DIR", value)
        return st._resolve_state_dir()

    def test_unset_uses_the_default(self, monkeypatch):
        assert self._resolve(monkeypatch, None) == "/.provisioner_state"

    def test_a_normal_absolute_path_is_honoured(self, monkeypatch, tmp_path):
        assert self._resolve(monkeypatch, str(tmp_path / "s")) == str(tmp_path / "s")

    @pytest.mark.parametrize("bad", [
        "/", "/etc", "/etc/", "/usr", "/var", "/root", "/opt", "/home",
        "/etc/../etc",                       # normpath must collapse it first
    ])
    def test_system_directories_are_refused(self, monkeypatch, bad):
        assert self._resolve(monkeypatch, bad) == "/.provisioner_state"

    @pytest.mark.parametrize("bad", ["relative/path", "", "  "])
    def test_non_absolute_is_refused(self, monkeypatch, bad):
        assert self._resolve(monkeypatch, bad) == "/.provisioner_state"

    def test_clear_refuses_a_directory_it_did_not_create(self, monkeypatch, tmp_path):
        """The part that turns a bad value into an incident."""
        import provisioner.state as st
        victim = tmp_path / "not-ours"
        victim.mkdir()
        (victim / "precious.conf").write_text("do not delete me")
        monkeypatch.setattr("provisioner.state.STATE_DIR", str(victim))
        st.clear_all_state()
        assert (victim / "precious.conf").exists(), "clear_all_state deleted a foreign tree"

    def test_clear_still_removes_its_own_directory(self, monkeypatch, tmp_path):
        """...without which the refusal above would just break --force."""
        import provisioner.state as st
        ours = tmp_path / "ours"
        monkeypatch.setattr("provisioner.state.STATE_DIR", str(ours))
        st.mark_stage_complete("apt", "h")
        assert (ours / st._OWNER_MARKER).exists()
        st.clear_all_state()
        assert not ours.is_dir()

    def test_a_symlinked_hash_file_is_not_followed(self, monkeypatch, tmp_path):
        """O_NOFOLLOW. A pre-planted symlink named <stage>.hash in a directory a
        lower-privileged principal can write would otherwise be clobbered by
        root — an arbitrary-file write that did not exist before this directory
        became relocatable."""
        import provisioner.state as st
        d = tmp_path / "state"
        d.mkdir()
        target = tmp_path / "victim.txt"
        target.write_text("original")
        (d / "apt.hash").symlink_to(target)
        monkeypatch.setattr("provisioner.state.STATE_DIR", str(d))
        st.mark_stage_complete("apt", "hash1")
        assert target.read_text() == "original", "followed a symlink and clobbered it"
        # And the stage must NOT read as complete, or the refusal would silently
        # skip work instead of merely declining to record it.
        assert st.is_stage_complete("apt", "hash1") is False
