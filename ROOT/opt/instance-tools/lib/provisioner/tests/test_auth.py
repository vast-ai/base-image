"""Tests for provisioner.auth -- token validation with mocked HTTP."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from provisioner.auth import validate_civitai_token, validate_hf_token


class TestValidateHfToken:
    def test_no_token_returns_false(self):
        assert validate_hf_token("HF_TOKEN") is False

    def test_valid_token(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "hf_test_token_123")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("provisioner.auth.urlopen", return_value=mock_resp):
            assert validate_hf_token("HF_TOKEN") is True

    def test_invalid_token_http_error(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "bad_token")
        error = HTTPError(
            url="https://huggingface.co/api/whoami-v2",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=BytesIO(b""),
        )
        with patch("provisioner.auth.urlopen", side_effect=error):
            assert validate_hf_token("HF_TOKEN") is False

    def test_network_error(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "some_token")
        with patch("provisioner.auth.urlopen", side_effect=URLError("timeout")):
            assert validate_hf_token("HF_TOKEN") is False

    def test_custom_env_var(self, monkeypatch):
        monkeypatch.setenv("CUSTOM_HF_TOKEN", "tok")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("provisioner.auth.urlopen", return_value=mock_resp):
            assert validate_hf_token("CUSTOM_HF_TOKEN") is True

    def test_non_200_response(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "tok")
        mock_resp = MagicMock()
        mock_resp.status = 403
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("provisioner.auth.urlopen", return_value=mock_resp):
            assert validate_hf_token("HF_TOKEN") is False


class TestValidateCivitaiToken:
    def test_no_token_returns_false(self):
        assert validate_civitai_token("CIVITAI_TOKEN") is False

    def test_valid_token(self, monkeypatch):
        monkeypatch.setenv("CIVITAI_TOKEN", "civ_test_token")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("provisioner.auth.urlopen", return_value=mock_resp):
            assert validate_civitai_token("CIVITAI_TOKEN") is True

    def test_invalid_token(self, monkeypatch):
        monkeypatch.setenv("CIVITAI_TOKEN", "bad")
        error = HTTPError(
            url="https://civitai.com/api/v1/models",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=BytesIO(b""),
        )
        with patch("provisioner.auth.urlopen", side_effect=error):
            assert validate_civitai_token("CIVITAI_TOKEN") is False

    def test_network_error(self, monkeypatch):
        monkeypatch.setenv("CIVITAI_TOKEN", "tok")
        with patch("provisioner.auth.urlopen", side_effect=OSError("network")):
            assert validate_civitai_token("CIVITAI_TOKEN") is False
