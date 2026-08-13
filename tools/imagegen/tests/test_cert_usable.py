"""The shipped cert-usability predicate, against real openssl fixtures.

/opt/instance-tools/bin/cert-usable answers one question — "can Caddy actually
serve TLS with this cert and this key?" — for three callers that used to answer
it three different ways (linter rule L066):

    ROOT/etc/vast_boot.d/55-tls-cert-gen.sh    decides whether to REGENERATE
    portal-aio/caddy_manager                   decides whether TLS COMES UP
    ROOT/.../tests/base/27-caddy-tls.sh        asserts on the result

A predicate with three callers and no fixtures is how the two defects below
shipped. Both are reproduced here as executable evidence, not prose: each
`test_old_*` builds the superseded implementation and demonstrates it giving the
wrong answer on a case the new one gets right.

Everything is generated with the real openssl at test time. No committed key
material, no network, and nothing that expires and starts failing in a year —
the expired fixture is expired by construction.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from imagegen.discover import find_repo_root

HELPER = find_repo_root(Path(__file__).resolve().parent) / \
    "ROOT/opt/instance-tools/bin/cert-usable"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def _openssl(*args: str) -> None:
    r = _run("openssl", *args)
    assert r.returncode == 0, f"openssl {' '.join(args)} failed: {r.stderr}"


@pytest.fixture(scope="module")
def certs(tmp_path_factory) -> Path:
    """RSA, EC, mismatched, expired and unreadable pairs, all real."""
    assert shutil.which("openssl"), "openssl is required to test the predicate"
    d = tmp_path_factory.mktemp("certs")
    p = lambda n: str(d / n)  # noqa: E731

    # A good RSA pair — what the boot script actually generates.
    _openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=t",
             "-keyout", p("rsa.key"), "-x509", "-days", "30", "-out", p("rsa.crt"))

    # A good EC pair. We never generate one, which is exactly why the RSA-only
    # check survived: the only way to hit it was to supply your own certificate.
    _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", p("ec.key"))
    _openssl("req", "-new", "-x509", "-key", p("ec.key"), "-subj", "/CN=t",
             "-days", "30", "-out", p("ec.crt"))

    # A second RSA key, for the mismatch case: cert from before a regeneration,
    # key from after.
    _openssl("genrsa", "-out", p("other.key"), "2048")

    # Expired, via a throwaway CA — `openssl req -x509` cannot backdate on
    # OpenSSL 3.0 (-not_before arrived in 3.5) but `openssl ca` can, so this
    # works on the runner and in the image without faketime.
    (d / "ca").mkdir()
    (d / "ca/newcerts").mkdir()
    (d / "ca/index.txt").touch()
    (d / "ca/serial").write_text("1000\n")
    (d / "ca.cnf").write_text(
        "[ca]\ndefault_ca = CA_default\n"
        f"[CA_default]\ndir = {d}/ca\ndatabase = $dir/index.txt\n"
        "new_certs_dir = $dir/newcerts\nserial = $dir/serial\n"
        "default_md = sha256\npolicy = pol\nemail_in_dn = no\n"
        "rand_serial = no\nunique_subject = no\n"
        "[pol]\ncommonName = optional\n"
    )
    _openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=ca",
             "-keyout", p("ca.key"), "-x509", "-days", "3650", "-out", p("ca.crt"))
    _openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=t",
             "-keyout", p("expired.key"), "-out", p("expired.csr"))
    _openssl("ca", "-batch", "-config", p("ca.cnf"), "-cert", p("ca.crt"),
             "-keyfile", p("ca.key"), "-startdate", "20200101000000Z",
             "-enddate", "20200102000000Z", "-in", p("expired.csr"),
             "-out", p("expired.crt"))

    # What the pre-fix curl actually wrote into /etc/instance.crt: a response
    # body. And an empty file, which `>` creates even when curl writes nothing.
    (d / "junk.crt").write_text("<html><body>502 Bad Gateway</body></html>\n")
    (d / "junk.key").write_text("not a key\n")
    (d / "empty.crt").write_text("")
    return d


def usable(certs: Path, crt: str, key: str) -> bool:
    return _run(str(HELPER), str(certs / crt), str(certs / key)).returncode == 0


# ── The predicate says yes only when TLS would actually work ──────────

def test_matching_rsa_pair_is_usable(certs):
    assert usable(certs, "rsa.crt", "rsa.key")


def test_matching_ec_pair_is_usable(certs):
    """The case that made the RSA-only check turn HTTPS off on a good cert."""
    assert usable(certs, "ec.crt", "ec.key")


@pytest.mark.parametrize("crt,key,reason", [
    ("rsa.crt", "other.key", "does not match"),
    ("expired.crt", "expired.key", "has expired"),
    ("junk.crt", "rsa.key", "not a parseable certificate"),
    ("rsa.crt", "junk.key", "not a parseable private key"),
    ("empty.crt", "rsa.key", "missing or empty"),
    ("absent.crt", "rsa.key", "missing or empty"),
])
def test_unusable_pairs_are_rejected_with_a_reason(certs, crt, key, reason):
    r = _run(str(HELPER), str(certs / crt), str(certs / key))
    assert r.returncode != 0, f"{crt}+{key} was accepted"
    assert reason in r.stderr, r.stderr


def test_both_sides_unreadable_is_rejected(certs):
    """Two failures must not certify each other — see
    test_old_digest_form_certifies_two_failures."""
    assert not usable(certs, "junk.crt", "junk.key")


def test_the_helper_defaults_to_the_instance_pair(certs):
    """Callers invoke it bare in the ENABLE_HTTPS guard. On a machine with no
    /etc/instance.crt that must be a clean "no", not a crash or a silent yes."""
    r = _run(str(HELPER))
    assert r.returncode == 1
    assert "/etc/instance.crt" in r.stderr


# ── The superseded implementations, demonstrated wrong ────────────────

def test_old_digest_form_certifies_two_failures(certs):
    """55-tls-cert-gen.sh hashed each side's DER public key and compared the
    hashes, guarded by `[[ -n "$c" ]]`.

    sha256sum of EMPTY input is e3b0c442…, a fixed non-empty string, so when
    both openssl calls fail the two digests are identical AND non-empty and the
    guard reports a matching pair. The expression fails OPEN, as asserted below.

    Be precise about what that did and did not mean. In the shipped script this
    sat below an `openssl x509 -noout` parse check, and no cert that clears that
    check goes on to fail `pkey -pubin` — checked against RSA, EC, DSA, Ed25519
    and a cert with a corrupted modulus. So the fail-open was UNREACHABLE: the
    script was safe by an ordering accident, with a guard above it that did
    nothing. This test pins the hazard that was removed, not a bug that bit."""
    old = (
        'c=$(openssl x509 -in "$1" -noout -pubkey 2>/dev/null '
        '| openssl pkey -pubin -outform DER 2>/dev/null | sha256sum); '
        'k=$(openssl pkey -in "$2" -pubout -outform DER 2>/dev/null | sha256sum); '
        '[[ -n "$c" && "$c" == "$k" ]]'
    )
    r = _run("bash", "-c", old, "_", str(certs / "junk.crt"), str(certs / "junk.key"))
    assert r.returncode == 0, "the old form was expected to fail open here"
    assert not usable(certs, "junk.crt", "junk.key"), "the helper must not"


def test_old_rsa_check_rejects_a_valid_ec_key(certs):
    """base/27-caddy-tls.sh and caddy_config_manager both ran
    `openssl rsa -in KEY -check`. It is the RSA-only entry point and cannot load
    an EC key at all, so a correct EC pair hard-failed the test and — more
    seriously — made the portal give up on TLS after MAX_RETRIES."""
    r = _run("openssl", "rsa", "-in", str(certs / "ec.key"), "-check", "-noout")
    assert r.returncode != 0, "the old form was expected to reject this good key"
    assert usable(certs, "ec.crt", "ec.key"), "the helper must accept it"


def test_old_rsa_check_accepts_a_mismatched_pair(certs):
    """The same two callers never compared the cert to the key at all, so a
    cert from before a regeneration and a key from after passed both. Caddy then
    served a listener that no client could complete a handshake with."""
    assert _run("openssl", "x509", "-in", str(certs / "rsa.crt"), "-noout").returncode == 0
    assert _run("openssl", "rsa", "-in", str(certs / "other.key"),
                "-check", "-noout").returncode == 0
    assert not usable(certs, "rsa.crt", "other.key"), "the helper must reject it"
