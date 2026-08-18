"""The portal's cert-usability path, including the in-process fallback.

`validate_cert_and_key()` gates `wait_for_valid_certs()`, which decides whether
Caddy comes up with TLS at all. `_validate_without_helper()` is the branch that
runs on the population this fallback EXISTS for — an older base image that
predates /opt/instance-tools/bin/cert-usable but took a newer portal tarball
(release-portal.yml publishes portal-aio; first_boot untars it over the older
image). That is most of the fleet on a portal release, and it had ZERO tests: a
second copy of the SPKI comparison, unreachable by the live-GPU QA gate because
QA images always have the helper, so CI is the only place it can be covered.

Fixtures are built with the real openssl at test time — no committed key material,
nothing that expires and starts failing in a year.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PORTAL_AIO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_PORTAL_AIO, "caddy_manager"))

import caddy_config_manager as ccm  # noqa: E402


def _openssl(*args: str) -> None:
    r = subprocess.run(("openssl", *args), capture_output=True, text=True)
    assert r.returncode == 0, f"openssl {' '.join(args)} failed: {r.stderr}"


@pytest.fixture(scope="module")
def certs(tmp_path_factory) -> Path:
    assert shutil.which("openssl"), "openssl is required to test the SPKI comparison"
    d = tmp_path_factory.mktemp("portal-certs")
    p = lambda n: str(d / n)  # noqa: E731

    _openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=t",
             "-keyout", p("rsa.key"), "-x509", "-days", "30", "-out", p("rsa.crt"))
    _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", p("ec.key"))
    _openssl("req", "-new", "-x509", "-key", p("ec.key"), "-subj", "/CN=t",
             "-days", "30", "-out", p("ec.crt"))
    _openssl("genrsa", "-out", p("other.key"), "2048")
    (d / "junk.crt").write_text("<html>502</html>\n")
    (d / "junk.key").write_text("not a key\n")
    return d


def _point_at(monkeypatch, certs: Path, crt: str, key: str) -> None:
    monkeypatch.setattr(ccm, "CERT_PATH", str(certs / crt))
    monkeypatch.setattr(ccm, "KEY_PATH", str(certs / key))


# ── _validate_without_helper: the second SPKI comparison ──────────────

def test_fallback_accepts_a_matching_rsa_pair(certs, monkeypatch):
    _point_at(monkeypatch, certs, "rsa.crt", "rsa.key")
    assert ccm._validate_without_helper() is True


def test_fallback_accepts_a_matching_ec_pair(certs, monkeypatch):
    """The case the RSA-only `openssl rsa -check` predecessor got wrong."""
    _point_at(monkeypatch, certs, "ec.crt", "ec.key")
    assert ccm._validate_without_helper() is True


@pytest.mark.parametrize("crt,key", [
    ("rsa.crt", "other.key"),   # mismatched pair
    ("junk.crt", "rsa.key"),    # unparseable cert
    ("rsa.crt", "junk.key"),    # unparseable key
])
def test_fallback_rejects_bad_pairs(certs, monkeypatch, crt, key):
    _point_at(monkeypatch, certs, crt, key)
    assert ccm._validate_without_helper() is False


def test_fallback_rejects_two_failures(certs, monkeypatch):
    """Two empty SPKI extractions must not compare equal (the digest-form bug)."""
    _point_at(monkeypatch, certs, "junk.crt", "junk.key")
    assert ccm._validate_without_helper() is False


# ── validate_cert_and_key: exit-code interpretation ───────────────────

class _FakeProc:
    def __init__(self, rc, stderr=""):
        self.returncode = rc
        self.stderr = stderr


def test_helper_rc0_is_usable(monkeypatch):
    monkeypatch.setattr(ccm.subprocess, "run", lambda *a, **k: _FakeProc(0))
    assert ccm.validate_cert_and_key() is True


def test_helper_expired_with_sentinel_is_served(monkeypatch):
    monkeypatch.setattr(ccm.subprocess, "run",
                        lambda *a, **k: _FakeProc(3, "cert-usable: /x has expired (key still matches)"))
    assert ccm.validate_cert_and_key() is True


def test_a_syntactically_broken_helper_exit2_is_not_expired(monkeypatch):
    """bash's own syntax-error exit is 2. It must fail CLOSED, never read as
    'expired, serve anyway' — the fail-open at the TLS gate ADR 0026 closes."""
    monkeypatch.setattr(ccm.subprocess, "run",
                        lambda *a, **k: _FakeProc(2, "syntax error near unexpected token"))
    assert ccm.validate_cert_and_key() is False


def test_exit3_without_the_sentinel_fails_closed(monkeypatch):
    """A stray 3 with no 'has expired' on stderr is not trusted as expiry."""
    monkeypatch.setattr(ccm.subprocess, "run", lambda *a, **k: _FakeProc(3, ""))
    assert ccm.validate_cert_and_key() is False


def test_helper_rc1_is_unusable(monkeypatch):
    monkeypatch.setattr(ccm.subprocess, "run", lambda *a, **k: _FakeProc(1, "does not match"))
    assert ccm.validate_cert_and_key() is False


def test_missing_helper_falls_back_to_in_process(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("no cert-usable")
    monkeypatch.setattr(ccm.subprocess, "run", boom)
    monkeypatch.setattr(ccm, "_validate_without_helper", lambda: "FELL_BACK")
    assert ccm.validate_cert_and_key() == "FELL_BACK"


def test_helper_timeout_falls_back_to_in_process(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="cert-usable", timeout=15)
    monkeypatch.setattr(ccm.subprocess, "run", boom)
    monkeypatch.setattr(ccm, "_validate_without_helper", lambda: "FELL_BACK")
    assert ccm.validate_cert_and_key() == "FELL_BACK"
