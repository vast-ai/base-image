"""Tests for provisioner.downloaders -- HF URL parsing, download logic (mocked)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, call, patch

import pytest

from provisioner.downloaders.base import retry_with_backoff
from provisioner.downloaders.huggingface import download_hf, parse_hf_url
from provisioner.downloaders.wget import (
    _get_content_disposition_filename,
    _is_civitai,
    download_wget,
)
from provisioner.schema import DownloadEntry, RetrySettings


# ---------- parse_hf_url ----------

class TestParseHfUrl:
    def test_basic(self):
        repo, rev, path = parse_hf_url(
            "https://huggingface.co/org/repo/resolve/main/model.safetensors"
        )
        assert repo == "org/repo"
        assert rev == "main"
        assert path == "model.safetensors"

    def test_nested_path(self):
        repo, rev, path = parse_hf_url(
            "https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/text_encoder/model.safetensors"
        )
        assert repo == "black-forest-labs/FLUX.1-dev"
        assert rev == "main"
        assert path == "text_encoder/model.safetensors"

    def test_specific_revision(self):
        repo, rev, path = parse_hf_url(
            "https://huggingface.co/org/repo/resolve/abc123/file.bin"
        )
        assert rev == "abc123"

    def test_deeply_nested(self):
        repo, rev, path = parse_hf_url(
            "https://huggingface.co/org/repo/resolve/main/a/b/c/d.bin"
        )
        assert path == "a/b/c/d.bin"

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Invalid HuggingFace URL"):
            parse_hf_url("https://example.com/not-hf")

    def test_missing_resolve_raises(self):
        with pytest.raises(ValueError):
            parse_hf_url("https://huggingface.co/org/repo/blob/main/file.bin")

    def test_hyphenated_org_and_repo(self):
        repo, rev, path = parse_hf_url(
            "https://huggingface.co/my-org/my-repo/resolve/v1.0/weights.bin"
        )
        assert repo == "my-org/my-repo"
        assert rev == "v1.0"

    def test_repo_url(self):
        repo, rev, path = parse_hf_url(
            "https://huggingface.co/meta-llama/Llama-3-8B"
        )
        assert repo == "meta-llama/Llama-3-8B"
        assert rev == ""
        assert path == ""

    def test_repo_url_trailing_slash(self):
        repo, rev, path = parse_hf_url(
            "https://huggingface.co/meta-llama/Llama-3-8B/"
        )
        assert repo == "meta-llama/Llama-3-8B"
        assert rev == ""
        assert path == ""

    def test_repo_url_dots_in_name(self):
        repo, rev, path = parse_hf_url(
            "https://huggingface.co/black-forest-labs/FLUX.1-dev"
        )
        assert repo == "black-forest-labs/FLUX.1-dev"
        assert rev == ""
        assert path == ""


# ---------- retry_with_backoff ----------

class TestRetryWithBackoff:
    @patch("provisioner.downloaders.base.time.sleep")
    def test_succeeds_first_try(self, mock_sleep):
        fn = MagicMock(return_value=True)
        assert retry_with_backoff(fn, "test", max_attempts=3) is True
        fn.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("provisioner.downloaders.base.time.sleep")
    def test_succeeds_after_retries(self, mock_sleep):
        fn = MagicMock(side_effect=[False, False, True])
        assert retry_with_backoff(
            fn, "test", max_attempts=5, initial_delay=1, backoff_multiplier=2
        ) is True
        assert fn.call_count == 3
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)

    @patch("provisioner.downloaders.base.time.sleep")
    def test_all_attempts_fail(self, mock_sleep):
        fn = MagicMock(return_value=False)
        assert retry_with_backoff(fn, "test", max_attempts=3) is False
        assert fn.call_count == 3

    @patch("provisioner.downloaders.base.time.sleep")
    def test_exception_counts_as_failure(self, mock_sleep):
        fn = MagicMock(side_effect=[RuntimeError("boom"), True])
        assert retry_with_backoff(fn, "test", max_attempts=3) is True
        assert fn.call_count == 2

    @patch("provisioner.downloaders.base.time.sleep")
    def test_exponential_backoff_delays(self, mock_sleep):
        fn = MagicMock(return_value=False)
        retry_with_backoff(
            fn, "test", max_attempts=4, initial_delay=2, backoff_multiplier=2
        )
        # Delays: 2, 4, 8 (no delay after last attempt)
        assert mock_sleep.call_args_list == [call(2), call(4), call(8)]

    @patch("provisioner.downloaders.base.time.sleep")
    def test_single_attempt(self, mock_sleep):
        fn = MagicMock(return_value=False)
        assert retry_with_backoff(fn, "test", max_attempts=1) is False
        fn.assert_called_once()
        mock_sleep.assert_not_called()


# ---------- _is_civitai ----------

class TestIsCivitai:
    def test_civitai_url(self):
        assert _is_civitai("https://civitai.com/api/download/models/123") is True

    def test_non_civitai_url(self):
        assert _is_civitai("https://example.com/file.bin") is False

    def test_civitai_in_path(self):
        assert _is_civitai("https://mirror.civitai.com/models/123") is True


# ---------- _get_content_disposition_filename ----------

class TestGetContentDispositionFilename:
    @patch("provisioner.downloaders.wget.subprocess.run")
    def test_extracts_filename(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout='Content-Disposition: attachment; filename="model_v2.safetensors"\r\n'
        )
        assert _get_content_disposition_filename("https://example.com/dl") == "model_v2.safetensors"

    @patch("provisioner.downloaders.wget.subprocess.run")
    def test_no_header(self, mock_run):
        mock_run.return_value = MagicMock(stdout="HTTP/1.1 200 OK\r\n")
        assert _get_content_disposition_filename("https://example.com/dl") == ""

    @patch("provisioner.downloaders.wget.subprocess.run")
    def test_filename_without_quotes(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout='Content-Disposition: attachment; filename=file.bin\r\n'
        )
        assert _get_content_disposition_filename("https://example.com/dl") == "file.bin"

    @patch("provisioner.downloaders.wget.subprocess.run")
    def test_with_auth_header(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout='Content-Disposition: attachment; filename="f.bin"\r\n'
        )
        _get_content_disposition_filename("https://example.com", "Authorization: Bearer tok")
        cmd = mock_run.call_args[0][0]
        assert "-H" in cmd
        assert "Authorization: Bearer tok" in cmd


# ---------- download_hf (single file with dest) ----------

class TestDownloadHfFile:
    def test_dry_run(self):
        entry = DownloadEntry(
            url="https://huggingface.co/org/repo/resolve/main/model.bin",
            dest="/tmp/model.bin",
        )
        retry = RetrySettings()
        download_hf(entry, retry=retry, dry_run=True)

    def test_dry_run_trailing_slash(self):
        entry = DownloadEntry(
            url="https://huggingface.co/org/repo/resolve/main/model.bin",
            dest="/tmp/models/",
        )
        retry = RetrySettings()
        download_hf(entry, retry=retry, dry_run=True)

    @patch("provisioner.downloaders.huggingface.FileLock")
    @patch("provisioner.downloaders.huggingface.os.path.isfile", return_value=True)
    def test_skips_existing_file(self, mock_isfile, mock_lock):
        mock_lock.return_value.__enter__ = MagicMock()
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        entry = DownloadEntry(
            url="https://huggingface.co/org/repo/resolve/main/model.bin",
            dest="/tmp/model.bin",
        )
        download_hf(entry, retry=RetrySettings())

    @patch("provisioner.downloaders.huggingface.retry_with_backoff", return_value=True)
    @patch("provisioner.downloaders.huggingface.FileLock")
    @patch("provisioner.downloaders.huggingface.os.path.isfile", return_value=False)
    @patch("provisioner.downloaders.huggingface.os.makedirs")
    def test_calls_retry(self, mock_makedirs, mock_isfile, mock_lock, mock_retry):
        mock_lock.return_value.__enter__ = MagicMock()
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        entry = DownloadEntry(
            url="https://huggingface.co/org/repo/resolve/main/model.bin",
            dest="/tmp/model.bin",
        )
        download_hf(entry, retry=RetrySettings(max_attempts=3))
        mock_retry.assert_called_once()
        kwargs = mock_retry.call_args[1]
        assert kwargs["max_attempts"] == 3


# ---------- download_hf (single file, no dest -> HF cache) ----------

class TestDownloadHfFileCache:
    def test_dry_run_no_dest(self, caplog):
        import logging
        caplog.set_level(logging.INFO)
        entry = DownloadEntry(
            url="https://huggingface.co/org/repo/resolve/main/model.bin",
            dest="",
        )
        download_hf(entry, retry=RetrySettings(), dry_run=True)
        assert "HF cache" in caplog.text

    @patch("provisioner.downloaders.huggingface.retry_with_backoff", return_value=True)
    def test_no_dest_calls_retry_without_local_dir(self, mock_retry):
        entry = DownloadEntry(
            url="https://huggingface.co/org/repo/resolve/main/model.bin",
            dest="",
        )
        download_hf(entry, retry=RetrySettings(max_attempts=2))
        mock_retry.assert_called_once()
        kwargs = mock_retry.call_args[1]
        assert kwargs["max_attempts"] == 2


# ---------- download_hf (full repo) ----------

class TestDownloadHfRepo:
    def test_dry_run_with_dest(self, caplog):
        import logging
        caplog.set_level(logging.INFO)
        entry = DownloadEntry(
            url="https://huggingface.co/meta-llama/Llama-3-8B",
            dest="/workspace/models/llama3",
        )
        download_hf(entry, retry=RetrySettings(), dry_run=True)
        assert "meta-llama/Llama-3-8B" in caplog.text
        assert "/workspace/models/llama3" in caplog.text

    def test_dry_run_no_dest(self, caplog):
        import logging
        caplog.set_level(logging.INFO)
        entry = DownloadEntry(
            url="https://huggingface.co/meta-llama/Llama-3-8B",
            dest="",
        )
        download_hf(entry, retry=RetrySettings(), dry_run=True)
        assert "HF cache" in caplog.text

    @patch("provisioner.downloaders.huggingface.retry_with_backoff", return_value=True)
    @patch("provisioner.downloaders.huggingface.os.makedirs")
    def test_repo_with_dest(self, mock_makedirs, mock_retry):
        entry = DownloadEntry(
            url="https://huggingface.co/meta-llama/Llama-3-8B",
            dest="/workspace/models/llama3",
        )
        download_hf(entry, retry=RetrySettings(max_attempts=3))
        mock_retry.assert_called_once()
        mock_makedirs.assert_called_once_with("/workspace/models/llama3", exist_ok=True)

    @patch("provisioner.downloaders.huggingface.retry_with_backoff", return_value=True)
    def test_repo_no_dest_cache_mode(self, mock_retry):
        entry = DownloadEntry(
            url="https://huggingface.co/meta-llama/Llama-3-8B",
            dest="",
        )
        download_hf(entry, retry=RetrySettings())
        mock_retry.assert_called_once()

    @patch("provisioner.downloaders.huggingface.retry_with_backoff", return_value=False)
    def test_repo_download_failure_raises(self, mock_retry):
        entry = DownloadEntry(
            url="https://huggingface.co/meta-llama/Llama-3-8B",
            dest="",
        )
        with pytest.raises(RuntimeError, match="Failed to download repo"):
            download_hf(entry, retry=RetrySettings())


# ---------- download_wget ----------

class TestDownloadWget:
    def test_dry_run(self):
        entry = DownloadEntry(
            url="https://example.com/file.bin",
            dest="/tmp/file.bin",
        )
        download_wget(entry, retry=RetrySettings(), dry_run=True)

    def test_dry_run_trailing_slash(self):
        entry = DownloadEntry(
            url="https://example.com/file.bin",
            dest="/tmp/models/",
        )
        download_wget(entry, retry=RetrySettings(), dry_run=True)

    @patch("provisioner.downloaders.wget.FileLock")
    @patch("provisioner.downloaders.wget.os.path.isfile", return_value=True)
    def test_skips_existing_file(self, mock_isfile, mock_lock):
        mock_lock.return_value.__enter__ = MagicMock()
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        entry = DownloadEntry(
            url="https://example.com/file.bin",
            dest="/tmp/file.bin",
        )
        download_wget(entry, retry=RetrySettings())

    def test_civitai_auth_header_in_dry_run(self, capsys):
        """CivitAI URLs should use auth headers but dry run doesn't execute."""
        entry = DownloadEntry(
            url="https://civitai.com/api/download/models/12345",
            dest="/tmp/models/",
        )
        download_wget(entry, retry=RetrySettings(), civitai_token="tok", dry_run=True)
