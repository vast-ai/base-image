"""Tests for provisioner.dedup — download/git dedup and symlink creation."""

from __future__ import annotations

import os

import pytest

from provisioner.dedup import create_symlinks, dedup_downloads, dedup_git_repos
from provisioner.schema import DownloadEntry, GitRepo


# ── dedup_downloads ──────────────────────────────────────────────────


class TestDedupDownloads:
    def test_exact_duplicates_collapsed(self):
        """Same url + same dest → collapsed, no symlink."""
        entries = [
            DownloadEntry(url="https://example.com/model.bin", dest="/models/model.bin"),
            DownloadEntry(url="https://example.com/model.bin", dest="/models/model.bin"),
        ]
        unique, symlinks = dedup_downloads(entries)
        assert len(unique) == 1
        assert unique[0].dest == "/models/model.bin"
        assert symlinks == []

    def test_same_url_different_dest(self):
        """Same URL, different dest → one download + one symlink."""
        entries = [
            DownloadEntry(url="https://example.com/model.bin", dest="/models/a/model.bin"),
            DownloadEntry(url="https://example.com/model.bin", dest="/models/b/model.bin"),
        ]
        unique, symlinks = dedup_downloads(entries)
        assert len(unique) == 1
        assert unique[0].dest == "/models/a/model.bin"
        assert symlinks == [("/models/a/model.bin", "/models/b/model.bin")]

    def test_three_entries_same_url(self):
        """Three entries same URL, three dests → one download + two symlinks."""
        entries = [
            DownloadEntry(url="https://example.com/model.bin", dest="/a/model.bin"),
            DownloadEntry(url="https://example.com/model.bin", dest="/b/model.bin"),
            DownloadEntry(url="https://example.com/model.bin", dest="/c/model.bin"),
        ]
        unique, symlinks = dedup_downloads(entries)
        assert len(unique) == 1
        assert len(symlinks) == 2
        assert symlinks[0] == ("/a/model.bin", "/b/model.bin")
        assert symlinks[1] == ("/a/model.bin", "/c/model.bin")

    def test_all_unique(self):
        """All unique URLs → unchanged, no symlinks."""
        entries = [
            DownloadEntry(url="https://example.com/a.bin", dest="/models/a.bin"),
            DownloadEntry(url="https://example.com/b.bin", dest="/models/b.bin"),
        ]
        unique, symlinks = dedup_downloads(entries)
        assert len(unique) == 2
        assert symlinks == []

    def test_dest_ending_slash_excluded(self):
        """Entries with dest ending in '/' are excluded from dedup."""
        entries = [
            DownloadEntry(url="https://example.com/model.bin", dest="/models/"),
            DownloadEntry(url="https://example.com/model.bin", dest="/other/"),
        ]
        unique, symlinks = dedup_downloads(entries)
        assert len(unique) == 2
        assert symlinks == []

    def test_empty_list(self):
        unique, symlinks = dedup_downloads([])
        assert unique == []
        assert symlinks == []


# ── dedup_git_repos ──────────────────────────────────────────────────


class TestDedupGitRepos:
    def test_same_url_ref_different_dest(self):
        """Same (url, ref) + different dest → one clone + symlink."""
        repos = [
            GitRepo(url="https://github.com/org/repo.git", dest="/workspace/repo", ref="main"),
            GitRepo(url="https://github.com/org/repo.git", dest="/workspace/repo2", ref="main"),
        ]
        unique, symlinks = dedup_git_repos(repos)
        assert len(unique) == 1
        assert unique[0].dest == "/workspace/repo"
        assert symlinks == [("/workspace/repo", "/workspace/repo2")]

    def test_same_url_ref_same_dest_collapsed(self):
        """Same (url, ref) + same dest → collapsed."""
        repos = [
            GitRepo(url="https://github.com/org/repo.git", dest="/workspace/repo", ref="main"),
            GitRepo(url="https://github.com/org/repo.git", dest="/workspace/repo", ref="main"),
        ]
        unique, symlinks = dedup_git_repos(repos)
        assert len(unique) == 1
        assert symlinks == []

    def test_same_url_different_ref_different_dest(self):
        """Same URL + different ref + different dest → both kept, no dedup."""
        repos = [
            GitRepo(url="https://github.com/org/repo.git", dest="/workspace/repo-v1", ref="v1.0"),
            GitRepo(url="https://github.com/org/repo.git", dest="/workspace/repo-v2", ref="v2.0"),
        ]
        unique, symlinks = dedup_git_repos(repos)
        assert len(unique) == 2
        assert symlinks == []

    def test_same_url_different_ref_same_dest_mangled(self):
        """Same URL + different ref + same dest → dest mangled on later entry."""
        repos = [
            GitRepo(url="https://github.com/org/repo.git", dest="/workspace/repo", ref="main"),
            GitRepo(url="https://github.com/org/repo.git", dest="/workspace/repo", ref="v1.0"),
        ]
        unique, symlinks = dedup_git_repos(repos)
        assert len(unique) == 2
        assert unique[0].dest == "/workspace/repo"
        assert unique[1].dest == "/workspace/repo--v1.0"
        assert symlinks == []

    def test_dest_collision_ref_with_slashes(self):
        """Ref containing slashes is sanitized in mangled dest."""
        repos = [
            GitRepo(url="https://github.com/org/repo.git", dest="/workspace/repo", ref="main"),
            GitRepo(url="https://github.com/org/repo.git", dest="/workspace/repo", ref="refs/tags/v1.0"),
        ]
        unique, symlinks = dedup_git_repos(repos)
        assert unique[1].dest == "/workspace/repo--refs-tags-v1.0"

    def test_duplicate_with_post_commands_kept(self):
        """Duplicate with post_commands → kept as independent clone."""
        repos = [
            GitRepo(url="https://github.com/org/repo.git", dest="/workspace/repo", ref="main"),
            GitRepo(
                url="https://github.com/org/repo.git", dest="/workspace/repo2", ref="main",
                post_commands=["make build"],
            ),
        ]
        unique, symlinks = dedup_git_repos(repos)
        assert len(unique) == 2
        assert symlinks == []
        assert unique[1].post_commands == ["make build"]

    def test_exact_dup_merges_post_commands(self):
        """Exact duplicate (same dest) merges post_commands into primary if primary has none."""
        repos = [
            GitRepo(url="https://github.com/org/repo.git", dest="/workspace/repo", ref="main"),
            GitRepo(
                url="https://github.com/org/repo.git", dest="/workspace/repo", ref="main",
                post_commands=["make build"],
            ),
        ]
        unique, symlinks = dedup_git_repos(repos)
        assert len(unique) == 1
        assert unique[0].post_commands == ["make build"]

    def test_empty_list(self):
        unique, symlinks = dedup_git_repos([])
        assert unique == []
        assert symlinks == []

    def test_dest_collision_no_ref(self):
        """Dest collision where later entry has empty ref → uses 'default' suffix."""
        repos = [
            GitRepo(url="https://github.com/org/repo.git", dest="/workspace/repo", ref="main"),
            GitRepo(url="https://github.com/org/other.git", dest="/workspace/repo", ref=""),
        ]
        unique, symlinks = dedup_git_repos(repos)
        assert unique[1].dest == "/workspace/repo--default"


# ── create_symlinks ──────────────────────────────────────────────────


class TestCreateSymlinks:
    def test_creates_parent_dirs_and_symlink(self, tmp_path):
        src = tmp_path / "source_file"
        src.write_text("data")
        dest = tmp_path / "subdir" / "nested" / "link"

        create_symlinks([(str(src), str(dest))])

        assert dest.is_symlink()
        assert os.readlink(str(dest)) == str(src)

    def test_dry_run_logs_only(self, tmp_path):
        src = tmp_path / "source_file"
        src.write_text("data")
        dest = tmp_path / "link"

        create_symlinks([(str(src), str(dest))], dry_run=True)

        assert not dest.exists()

    def test_skips_existing_dest(self, tmp_path):
        src = tmp_path / "source_file"
        src.write_text("data")
        dest = tmp_path / "link"
        dest.write_text("existing")

        create_symlinks([(str(src), str(dest))])

        # Should not have been replaced
        assert not dest.is_symlink()
        assert dest.read_text() == "existing"

    def test_warns_missing_source(self, tmp_path):
        src = tmp_path / "nonexistent"
        dest = tmp_path / "link"

        # Should not raise, just warn
        create_symlinks([(str(src), str(dest))])

        assert not dest.exists()

    def test_creates_directory_symlink(self, tmp_path):
        src = tmp_path / "source_dir"
        src.mkdir()
        (src / "file.txt").write_text("hello")
        dest = tmp_path / "link_dir"

        create_symlinks([(str(src), str(dest))])

        assert dest.is_symlink()
        assert (dest / "file.txt").read_text() == "hello"
