"""Tests for provisioner.failure -- failure actions, webhook, retry counter."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from provisioner.failure import (
    ATTEMPTS_FILE,
    _call_webhook,
    _check_retries,
    _get_attempt_count,
    _increment_attempts,
    _vastai_command,
    handle_failure,
)
from provisioner.schema import OnFailure


@pytest.fixture(autouse=True)
def _use_tmp_attempts_file(tmp_path, monkeypatch):
    """Redirect ATTEMPTS_FILE to a temp path."""
    path = str(tmp_path / "provisioner_attempts")
    monkeypatch.setattr("provisioner.failure.ATTEMPTS_FILE", path)
    return path


class TestAttemptCounter:
    def test_initial_count_is_zero(self):
        assert _get_attempt_count() == 0

    def test_increment(self, _use_tmp_attempts_file):
        assert _increment_attempts() == 1
        assert _increment_attempts() == 2
        assert _increment_attempts() == 3

    def test_get_after_increment(self, _use_tmp_attempts_file):
        _increment_attempts()
        _increment_attempts()
        assert _get_attempt_count() == 2

    def test_check_retries_under_limit(self, _use_tmp_attempts_file):
        assert _check_retries(3) is True  # attempt 1 < 3
        assert _check_retries(3) is True  # attempt 2 < 3
        assert _check_retries(3) is False  # attempt 3 >= 3


class TestWebhook:
    @patch("provisioner.failure.urllib.request.urlopen")
    def test_call_webhook_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        _call_webhook("https://hooks.example.com", {"test": "data"})
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://hooks.example.com"
        assert json.loads(req.data) == {"test": "data"}

    @patch("provisioner.failure.urllib.request.urlopen", side_effect=Exception("network error"))
    def test_call_webhook_failure_does_not_raise(self, mock_urlopen):
        # Should log warning but not raise
        _call_webhook("https://hooks.example.com", {"test": "data"})


class TestVastaiCommand:
    @patch("provisioner.failure.subprocess.run")
    def test_destroy_command(self, mock_run, monkeypatch):
        monkeypatch.setenv("CONTAINER_ID", "12345")
        monkeypatch.setenv("CONTAINER_API_KEY", "key123")
        _vastai_command("destroy")
        cmd = mock_run.call_args[0][0]
        assert cmd == ["vastai", "destroy", "instance", "12345", "--api-key", "key123"]

    @patch("provisioner.failure.subprocess.run")
    def test_stop_command(self, mock_run, monkeypatch):
        monkeypatch.setenv("CONTAINER_ID", "12345")
        monkeypatch.setenv("CONTAINER_API_KEY", "key123")
        _vastai_command("stop")
        cmd = mock_run.call_args[0][0]
        assert cmd == ["vastai", "stop", "instance", "12345", "--api-key", "key123"]

    def test_missing_container_id(self, monkeypatch):
        monkeypatch.delenv("CONTAINER_ID", raising=False)
        # Should not raise, just log error
        _vastai_command("destroy")

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.setenv("CONTAINER_ID", "12345")
        monkeypatch.delenv("CONTAINER_API_KEY", raising=False)
        _vastai_command("destroy")


class TestHandleFailure:
    def test_continue_action(self):
        on_failure = OnFailure(action="continue")
        # Should not raise
        handle_failure(on_failure, "/path/manifest.yaml")

    @patch("provisioner.failure._call_webhook")
    def test_webhook_from_manifest(self, mock_webhook):
        on_failure = OnFailure(action="continue", webhook="https://hooks.example.com")
        handle_failure(on_failure, "/path/manifest.yaml", error="test error")
        mock_webhook.assert_called_once()
        payload = mock_webhook.call_args[0][1]
        assert payload["action"] == "continue"
        assert payload["error"] == "test error"
        assert payload["manifest"] == "/path/manifest.yaml"

    @patch("provisioner.failure._call_webhook")
    def test_webhook_env_overrides_manifest(self, mock_webhook, monkeypatch):
        monkeypatch.setenv("PROVISIONER_WEBHOOK_URL", "https://env-hook.example.com")
        on_failure = OnFailure(action="continue", webhook="https://manifest-hook.example.com")
        handle_failure(on_failure, "/path/manifest.yaml")
        url = mock_webhook.call_args[0][0]
        assert url == "https://env-hook.example.com"

    @patch("provisioner.failure._call_webhook")
    def test_no_webhook_when_not_configured(self, mock_webhook):
        on_failure = OnFailure(action="continue")
        handle_failure(on_failure, "/path/manifest.yaml")
        mock_webhook.assert_not_called()

    @patch("provisioner.failure._check_retries", return_value=True)
    def test_retry_action(self, mock_retries):
        on_failure = OnFailure(action="retry", max_retries=3)
        handle_failure(on_failure, "/path/manifest.yaml")
        mock_retries.assert_called_once_with(3)

    @patch("provisioner.failure._vastai_command")
    def test_destroy_action(self, mock_vastai):
        on_failure = OnFailure(action="destroy")
        handle_failure(on_failure, "/path/manifest.yaml")
        mock_vastai.assert_called_once_with("destroy")

    @patch("provisioner.failure._vastai_command")
    def test_stop_action(self, mock_vastai):
        on_failure = OnFailure(action="stop")
        handle_failure(on_failure, "/path/manifest.yaml")
        mock_vastai.assert_called_once_with("stop")

    def test_unknown_action(self):
        on_failure = OnFailure(action="invalid")
        # Should not raise
        handle_failure(on_failure, "/path/manifest.yaml")
