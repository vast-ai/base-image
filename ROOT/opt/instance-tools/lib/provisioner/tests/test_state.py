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

    def test_the_module_level_binding_is_validated(self, monkeypatch):
        """The seam that matters. Every other test calls _resolve_state_dir()
        directly or monkeypatches STATE_DIR, so deleting the validation from the
        module-level binding would leave them all green. Reload the module with a
        system dir in the environment and assert the BOUND value fell back."""
        import importlib
        import provisioner.state as st
        monkeypatch.setenv("PROVISIONER_STATE_DIR", "/etc")
        reloaded = importlib.reload(st)
        try:
            assert reloaded.STATE_DIR == "/.provisioner_state"
        finally:
            monkeypatch.delenv("PROVISIONER_STATE_DIR", raising=False)
            importlib.reload(st)

    def test_clear_refuses_a_directory_it_did_not_create(self, monkeypatch, tmp_path):
        """The part that turns a bad value into an incident."""
        import provisioner.state as st
        victim = tmp_path / "not-ours"
        victim.mkdir()
        (victim / "precious.conf").write_text("do not delete me")
        monkeypatch.setattr("provisioner.state.STATE_DIR", str(victim))
        assert st.clear_all_state() is False, "should have refused"
        assert (victim / "precious.conf").exists(), "clear_all_state deleted a foreign tree"

    def test_marking_a_foreign_directory_does_not_adopt_it(self, monkeypatch, tmp_path):
        """The self-granting hole: mark_stage_complete used makedirs(exist_ok=True),
        so ANY pre-existing directory it was pointed at got the ownership marker on
        the first normal run — and the next --force then rmtree'd it. The marker
        must be planted only in a directory the provisioner itself created."""
        import provisioner.state as st
        victim = tmp_path / "victim"
        victim.mkdir()
        (victim / "precious.conf").write_text("do not delete me")
        monkeypatch.setattr("provisioner.state.STATE_DIR", str(victim))
        st.mark_stage_complete("apt", "h")
        assert not (victim / st._OWNER_MARKER).exists(), "adopted a foreign directory"
        assert st.clear_all_state() is False
        assert (victim / "precious.conf").exists(), "a later --force deleted it"

    def test_a_symlinked_marker_is_not_followed(self, monkeypatch, tmp_path):
        """The marker write must be O_NOFOLLOW/O_EXCL. A DANGLING symlink named
        `.provisioner-state-dir` reads as absent under os.path.exists, so a plain
        open() would create-and-write root's file at the symlink target, OUTSIDE
        the state dir."""
        import provisioner.state as st
        sd = tmp_path / "state"
        sd.mkdir()
        outside = tmp_path / "outside.txt"
        (sd / st._OWNER_MARKER).symlink_to(outside)     # dangling
        monkeypatch.setattr("provisioner.state.STATE_DIR", str(sd))
        # mark creates the dir itself normally; here the dir already exists, so no
        # marker is (re)planted — but if a future edit reintroduces a plant on an
        # existing dir, it must still not follow the symlink.
        st._plant_owner_marker()
        assert not outside.exists(), "followed a symlink and wrote outside the state dir"

    def test_a_symlinked_marker_is_not_accepted_as_ownership(self, monkeypatch, tmp_path):
        """clear_all_state uses lstat, so a symlink cannot forge ownership of a
        directory holding foreign files."""
        import provisioner.state as st
        victim = tmp_path / "victim"
        victim.mkdir()
        (victim / "precious.conf").write_text("keep me")
        # Symlink the marker at an EXISTING regular file, so a stat() (which
        # follows) would see a regular file and forge ownership; lstat() sees the
        # symlink and refuses.
        existing = tmp_path / "some-real-file"
        existing.write_text("x")
        (victim / st._OWNER_MARKER).symlink_to(existing)
        monkeypatch.setattr("provisioner.state.STATE_DIR", str(victim))
        assert st.clear_all_state() is False
        assert (victim / "precious.conf").exists()

    def test_a_legacy_hash_only_directory_is_migrated(self, monkeypatch, tmp_path):
        """Already-deployed instances have a /.provisioner_state that predates the
        marker. A directory holding only stage-hash files must still be clearable
        by --force, or --force silently skips every stage on the existing fleet."""
        import provisioner.state as st
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        (legacy / "apt.hash").write_text("abc")
        (legacy / "pip.hash").write_text("def")
        monkeypatch.setattr("provisioner.state.STATE_DIR", str(legacy))
        assert st.clear_all_state() is True
        assert not legacy.is_dir()

    def test_clear_still_removes_its_own_directory(self, monkeypatch, tmp_path):
        """...without which the refusal above would just break --force."""
        import provisioner.state as st
        ours = tmp_path / "ours"
        monkeypatch.setattr("provisioner.state.STATE_DIR", str(ours))
        st.mark_stage_complete("apt", "h")
        assert (ours / st._OWNER_MARKER).exists()
        assert st.clear_all_state() is True
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


class TestGuardsThatCouldNotFail:
    """Checks added in an earlier round that no test could discriminate.

    Each of these passed the whole suite with the guard removed, which by this
    repo's own rule means the guard did not count. The discriminating input is
    the point: a symlink whose target holds the value under test, and a
    directory with nothing in it.
    """

    def test_a_symlinked_hash_file_is_not_READ(self, monkeypatch, tmp_path):
        """The existing symlink test used a target holding "original", which can
        never equal the hash being compared — so it passed with or without
        O_NOFOLLOW. Make the target hold the hash and the read side has to
        actually refuse to follow it."""
        import provisioner.state as st
        d = tmp_path / "state"
        d.mkdir()
        secret = tmp_path / "secret"
        secret.write_text("hash1")
        (d / "apt.hash").symlink_to(secret)
        monkeypatch.setattr("provisioner.state.STATE_DIR", str(d))
        assert st.is_stage_complete("apt", "hash1") is False

    def test_an_empty_foreign_directory_is_not_adopted(self, monkeypatch, tmp_path):
        """`all()` over an empty listing is vacuously True, so an empty directory
        the provisioner was merely pointed at read as ours and --force deleted
        it. A freshly-mounted volume is exactly that shape."""
        import provisioner.state as st
        victim = tmp_path / "empty-foreign"
        victim.mkdir()
        monkeypatch.setattr("provisioner.state.STATE_DIR", str(victim))
        assert st._dir_is_ours(str(victim)) is False
        assert st.clear_all_state() is False
        assert victim.is_dir(), "clear_all_state deleted an empty foreign directory"


class TestForceRefusalIsNotAProvisioningFailure:
    """`--force` against a directory we do not own must abort — but as a CONFIG
    error, not a run failure.

    A run failure enters `run_with_retries` (3 attempts, 30s apart) and then
    `handle_failure`, whose action is customer-settable and includes `destroy`.
    The refusal is deterministic: every retry re-reads the same directory and
    refuses identically. So retrying burns 90s to reach a destroy that a typo in
    PROVISIONER_STATE_DIR should never be able to trigger.
    """

    def test_refusal_returns_the_config_code_and_skips_the_failure_action(
            self, monkeypatch, tmp_path):
        from provisioner.__main__ import CONFIG_REFUSED, run_with_retries
        from provisioner.schema import Manifest

        called = {}
        monkeypatch.setattr("provisioner.__main__.clear_all_state", lambda: False)
        monkeypatch.setattr("provisioner.__main__.handle_failure",
                            lambda *a, **k: called.setdefault("handle_failure", True))
        monkeypatch.setattr("provisioner.__main__.load_manifest",
                            lambda p: Manifest(version=1))
        monkeypatch.setattr("provisioner.__main__.setup_logging", lambda *a, **k: None)
        monkeypatch.setattr("provisioner.__main__.time.sleep",
                            lambda s: called.setdefault("slept", True))

        rc = run_with_retries("m.yaml", force=True)
        assert rc == CONFIG_REFUSED
        assert "handle_failure" not in called, "a config refusal reached the failure action"
        assert "slept" not in called, "a deterministic refusal was retried"
