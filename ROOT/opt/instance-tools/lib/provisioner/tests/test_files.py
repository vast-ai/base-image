"""Tests for provisioner.installers.files -- cloud-init style file writer."""

from __future__ import annotations

import os
import stat
from unittest.mock import patch

import pytest

from provisioner.installers.files import _parse_owner, write_files
from provisioner.schema import FileWrite


class TestWriteFiles:
    def test_empty_list(self):
        write_files([])

    def test_dry_run(self, caplog):
        import logging
        caplog.set_level(logging.INFO)
        files = [FileWrite(path="/tmp/test.txt", content="hello world")]
        write_files(files, dry_run=True)
        assert "/tmp/test.txt" in caplog.text
        assert "hello world" in caplog.text

    def test_dry_run_truncates_long_content(self, caplog):
        import logging
        caplog.set_level(logging.INFO)
        files = [FileWrite(path="/tmp/test.txt", content="x" * 200)]
        write_files(files, dry_run=True)
        assert "..." in caplog.text

    def test_skips_empty_path(self, caplog):
        import logging
        caplog.set_level(logging.WARNING)
        write_files([FileWrite(content="data")])
        assert "empty path" in caplog.text

    def test_writes_file(self, tmp_path):
        target = str(tmp_path / "output.txt")
        files = [FileWrite(path=target, content="hello\nworld\n")]
        write_files(files)
        assert os.path.isfile(target)
        with open(target) as f:
            assert f.read() == "hello\nworld\n"

    def test_creates_parent_dirs(self, tmp_path):
        target = str(tmp_path / "a" / "b" / "c" / "file.txt")
        files = [FileWrite(path=target, content="nested")]
        write_files(files)
        with open(target) as f:
            assert f.read() == "nested"

    def test_default_permissions(self, tmp_path):
        target = str(tmp_path / "default.txt")
        files = [FileWrite(path=target, content="data")]
        write_files(files)
        mode = stat.S_IMODE(os.stat(target).st_mode)
        assert mode == 0o644

    def test_executable_permissions(self, tmp_path):
        target = str(tmp_path / "script.sh")
        files = [FileWrite(path=target, content="#!/bin/bash\necho hi", permissions="0755")]
        write_files(files)
        mode = stat.S_IMODE(os.stat(target).st_mode)
        assert mode == 0o755

    def test_restrictive_permissions(self, tmp_path):
        target = str(tmp_path / "secret.key")
        files = [FileWrite(path=target, content="secret", permissions="0600")]
        write_files(files)
        mode = stat.S_IMODE(os.stat(target).st_mode)
        assert mode == 0o600

    def test_multiple_files(self, tmp_path):
        files = [
            FileWrite(path=str(tmp_path / "a.txt"), content="aaa"),
            FileWrite(path=str(tmp_path / "b.txt"), content="bbb", permissions="0755"),
        ]
        write_files(files)
        with open(tmp_path / "a.txt") as f:
            assert f.read() == "aaa"
        with open(tmp_path / "b.txt") as f:
            assert f.read() == "bbb"
        assert stat.S_IMODE(os.stat(tmp_path / "b.txt").st_mode) == 0o755

    def test_overwrites_existing(self, tmp_path):
        target = str(tmp_path / "overwrite.txt")
        with open(target, "w") as f:
            f.write("old content")
        write_files([FileWrite(path=target, content="new content")])
        with open(target) as f:
            assert f.read() == "new content"

    def test_label_in_log(self, caplog):
        import logging
        caplog.set_level(logging.INFO)
        write_files([], label="write_files_late")
        assert "write_files_late" in caplog.text

    @patch("provisioner.installers.files.os.chown")
    @patch("provisioner.installers.files._parse_owner", return_value=(1000, 1000))
    def test_owner_set(self, mock_parse, mock_chown, tmp_path):
        target = str(tmp_path / "owned.txt")
        files = [FileWrite(path=target, content="data", owner="appuser:appgroup")]
        write_files(files)
        mock_parse.assert_called_once_with("appuser:appgroup")
        mock_chown.assert_called_once_with(target, 1000, 1000)

    def test_owner_failure_warns(self, tmp_path, caplog):
        import logging
        caplog.set_level(logging.WARNING)
        target = str(tmp_path / "bad_owner.txt")
        files = [FileWrite(path=target, content="data", owner="nonexistent_user_xyz")]
        write_files(files)
        # File should still be written
        assert os.path.isfile(target)
        assert "could not set owner" in caplog.text


class TestParseOwner:
    @patch("provisioner.installers.files.grp.getgrnam")
    @patch("provisioner.installers.files.pwd.getpwnam")
    def test_user_and_group(self, mock_pwd, mock_grp):
        mock_pwd.return_value.pw_uid = 1000
        mock_pwd.return_value.pw_gid = 1000
        mock_grp.return_value.gr_gid = 2000
        uid, gid = _parse_owner("app:staff")
        mock_pwd.assert_called_once_with("app")
        mock_grp.assert_called_once_with("staff")
        assert uid == 1000
        assert gid == 2000

    @patch("provisioner.installers.files.pwd.getpwnam")
    def test_user_only(self, mock_pwd):
        mock_pwd.return_value.pw_uid = 1000
        mock_pwd.return_value.pw_gid = 1000
        uid, gid = _parse_owner("app")
        assert uid == 1000
        assert gid == 1000  # Falls back to user's primary group

    @patch("provisioner.installers.files.pwd.getpwnam", side_effect=KeyError("no such user"))
    def test_unknown_user_raises(self, mock_pwd):
        with pytest.raises(KeyError):
            _parse_owner("nobody_here")
