"""
Tests for muxplex/tls.py — TLS certificate generation and inspection.
12 tests covering generate_self_signed() and get_cert_info().
"""

import stat


# ---------------------------------------------------------------------------
# 1. Importability
# ---------------------------------------------------------------------------


def test_tls_module_importable():
    """muxplex.tls must be importable."""
    import muxplex.tls  # noqa: F401

    assert hasattr(muxplex.tls, "generate_self_signed")
    assert hasattr(muxplex.tls, "get_cert_info")


# ---------------------------------------------------------------------------
# 2–7. generate_self_signed() tests
# ---------------------------------------------------------------------------


def test_generate_self_signed_creates_cert_and_key(tmp_path):
    """generate_self_signed() must create cert.pem and key.pem at the given paths."""
    from muxplex.tls import generate_self_signed

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    generate_self_signed(cert_path, key_path, hostnames=["localhost"])

    assert cert_path.exists(), "cert.pem was not created"
    assert key_path.exists(), "key.pem was not created"


def test_generate_self_signed_cert_is_valid_pem(tmp_path):
    """Generated cert must start with '-----BEGIN CERTIFICATE-----'."""
    from muxplex.tls import generate_self_signed

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    generate_self_signed(cert_path, key_path, hostnames=["localhost"])

    content = cert_path.read_text()
    assert content.startswith("-----BEGIN CERTIFICATE-----"), (
        f"cert.pem must start with '-----BEGIN CERTIFICATE-----', got: {content[:50]!r}"
    )


def test_generate_self_signed_key_is_valid_pem(tmp_path):
    """Generated key must start with '-----BEGIN'."""
    from muxplex.tls import generate_self_signed

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    generate_self_signed(cert_path, key_path, hostnames=["localhost"])

    content = key_path.read_text()
    assert content.startswith("-----BEGIN"), (
        f"key.pem must start with '-----BEGIN', got: {content[:50]!r}"
    )


def test_generate_self_signed_key_permissions(tmp_path):
    """Key file must have permissions 0o600 (owner read/write only)."""
    from muxplex.tls import generate_self_signed

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    generate_self_signed(cert_path, key_path, hostnames=["localhost"])

    mode = stat.S_IMODE(key_path.stat().st_mode)
    assert mode == 0o600, f"key.pem permissions must be 0o600, got: {oct(mode)}"


def test_generate_self_signed_returns_metadata(tmp_path):
    """generate_self_signed() must return a dict with method, cert_path, key_path, hostnames, expires."""
    from muxplex.tls import generate_self_signed

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    hostnames = ["myhost", "myhost.local", "localhost"]

    result = generate_self_signed(cert_path, key_path, hostnames=hostnames)

    assert isinstance(result, dict), "generate_self_signed() must return a dict"
    assert result.get("method") == "selfsigned", (
        f"method must be 'selfsigned', got: {result.get('method')!r}"
    )
    assert (
        result.get("cert_path") == str(cert_path)
        or result.get("cert_path") == cert_path
    ), f"cert_path missing or wrong in result: {result.get('cert_path')!r}"
    assert (
        result.get("key_path") == str(key_path) or result.get("key_path") == key_path
    ), f"key_path missing or wrong in result: {result.get('key_path')!r}"
    assert isinstance(result.get("hostnames"), list) and len(result["hostnames"]) > 0, (
        f"hostnames must be a non-empty list, got: {result.get('hostnames')!r}"
    )
    assert "expires" in result, "expires key must be in result"


def test_generate_self_signed_creates_parent_dirs(tmp_path):
    """generate_self_signed() must create parent directories if they don't exist."""
    from muxplex.tls import generate_self_signed

    cert_path = tmp_path / "a" / "b" / "cert.pem"
    key_path = tmp_path / "a" / "b" / "key.pem"

    # Parent dirs do not exist yet
    assert not cert_path.parent.exists()

    generate_self_signed(cert_path, key_path, hostnames=["localhost"])

    assert cert_path.exists(), "cert.pem was not created (parent dirs not created)"
    assert key_path.exists(), "key.pem was not created (parent dirs not created)"


# ---------------------------------------------------------------------------
# 8–9. get_cert_info() tests
# ---------------------------------------------------------------------------


def test_get_cert_info_returns_expiry(tmp_path):
    """get_cert_info() must return a dict with expires and hostnames for a valid cert."""
    from muxplex.tls import generate_self_signed, get_cert_info

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    generate_self_signed(cert_path, key_path, hostnames=["testhost", "localhost"])

    info = get_cert_info(cert_path)

    assert info is not None, "get_cert_info() must not return None for a valid cert"
    assert isinstance(info, dict), "get_cert_info() must return a dict"
    assert "expires" in info, "get_cert_info() result must have 'expires' key"
    assert "hostnames" in info, "get_cert_info() result must have 'hostnames' key"
    assert isinstance(info["hostnames"], list), "hostnames must be a list"


def test_get_cert_info_returns_none_for_missing_file(tmp_path):
    """get_cert_info() must return None for a missing or unreadable file."""
    from muxplex.tls import get_cert_info

    missing_path = tmp_path / "nonexistent.pem"

    result = get_cert_info(missing_path)

    assert result is None, (
        f"get_cert_info() must return None for missing file, got: {result!r}"
    )


# ---------------------------------------------------------------------------
# 10–12. Edge case tests
# ---------------------------------------------------------------------------


def test_generate_self_signed_with_custom_hostnames(tmp_path):
    """generate_self_signed() with custom hostnames must include all of them in result['hostnames']."""
    from muxplex.tls import generate_self_signed

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    custom_hostnames = ["mybox.local", "mybox.tailnet.ts.net"]

    result = generate_self_signed(cert_path, key_path, hostnames=custom_hostnames)

    assert isinstance(result, dict), "generate_self_signed() must return a dict"
    assert isinstance(result.get("hostnames"), list), (
        "result['hostnames'] must be a list"
    )
    assert "mybox.local" in result["hostnames"], (
        f"'mybox.local' must be in result['hostnames'], got: {result['hostnames']!r}"
    )
    assert "mybox.tailnet.ts.net" in result["hostnames"], (
        f"'mybox.tailnet.ts.net' must be in result['hostnames'], got: {result['hostnames']!r}"
    )


def test_get_cert_info_hostnames_include_ip(tmp_path):
    """get_cert_info() must include '127.0.0.1' in hostnames (from IP SANs added by generate_self_signed)."""
    from muxplex.tls import generate_self_signed, get_cert_info

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    generate_self_signed(cert_path, key_path, hostnames=["localhost"])

    info = get_cert_info(cert_path)

    assert info is not None, "get_cert_info() must not return None for a valid cert"
    assert "hostnames" in info, "get_cert_info() result must have 'hostnames' key"
    assert "127.0.0.1" in info["hostnames"], (
        f"'127.0.0.1' must be in info['hostnames'] (IP SANs), got: {info['hostnames']!r}"
    )


def test_get_cert_info_returns_none_for_corrupt_file(tmp_path):
    """get_cert_info() must return None for a file containing invalid PEM data."""
    from muxplex.tls import get_cert_info

    corrupt_pem = tmp_path / "corrupt.pem"
    corrupt_pem.write_text("THIS IS NOT A CERTIFICATE")

    result = get_cert_info(corrupt_pem)

    assert result is None, (
        f"get_cert_info() must return None for corrupt PEM file, got: {result!r}"
    )


# ---------------------------------------------------------------------------
# 13–16. detect_tailscale() tests
# ---------------------------------------------------------------------------


def test_detect_tailscale_returns_info_when_available(monkeypatch):
    """detect_tailscale() must return hostname, ips, cert_domains when Tailscale is available."""
    import json

    from muxplex.tls import detect_tailscale

    status_data = {
        "DNSName": "spark-1.tail8f3c4e.ts.net.",
        "TailscaleIPs": ["100.64.0.1"],
        "CertDomains": ["spark-1.tail8f3c4e.ts.net"],
    }

    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/bin/tailscale" if name == "tailscale" else None,
    )
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: type(
            "R", (), {"returncode": 0, "stdout": json.dumps(status_data)}
        )(),
    )

    result = detect_tailscale()

    assert result is not None, (
        "detect_tailscale() must not return None when Tailscale is available"
    )
    assert result["hostname"] == "spark-1.tail8f3c4e.ts.net", (
        f"hostname must be 'spark-1.tail8f3c4e.ts.net' (trailing dot stripped), got: {result['hostname']!r}"
    )
    assert result["ips"] == ["100.64.0.1"], (
        f"ips must be ['100.64.0.1'], got: {result['ips']!r}"
    )
    assert result["cert_domains"] == ["spark-1.tail8f3c4e.ts.net"], (
        f"cert_domains must be ['spark-1.tail8f3c4e.ts.net'], got: {result['cert_domains']!r}"
    )


def test_detect_tailscale_returns_none_when_not_installed(monkeypatch):
    """detect_tailscale() must return None when Tailscale CLI is not installed."""
    from muxplex.tls import detect_tailscale

    monkeypatch.setattr("shutil.which", lambda name: None)

    result = detect_tailscale()

    assert result is None, (
        f"detect_tailscale() must return None when not installed, got: {result!r}"
    )


def test_detect_tailscale_returns_none_when_not_connected(monkeypatch):
    """detect_tailscale() must return None when Tailscale is not connected (non-zero exit)."""
    from muxplex.tls import detect_tailscale

    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/bin/tailscale" if name == "tailscale" else None,
    )
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: type("R", (), {"returncode": 1, "stdout": ""})(),
    )

    result = detect_tailscale()

    assert result is None, (
        f"detect_tailscale() must return None when not connected (non-zero exit), got: {result!r}"
    )


def test_detect_tailscale_returns_none_when_no_cert_domains(monkeypatch):
    """detect_tailscale() must return None when CertDomains is empty."""
    import json

    from muxplex.tls import detect_tailscale

    status_data = {
        "DNSName": "spark-1.tail8f3c4e.ts.net.",
        "TailscaleIPs": ["100.64.0.1"],
        "CertDomains": [],
    }

    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/bin/tailscale" if name == "tailscale" else None,
    )
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: type(
            "R", (), {"returncode": 0, "stdout": json.dumps(status_data)}
        )(),
    )

    result = detect_tailscale()

    assert result is None, (
        f"detect_tailscale() must return None when CertDomains is empty, got: {result!r}"
    )


# ---------------------------------------------------------------------------
# 17-18. generate_tailscale() tests
# ---------------------------------------------------------------------------


def test_generate_tailscale_calls_tailscale_cert(monkeypatch, tmp_path):
    """generate_tailscale() must call 'tailscale cert' with --cert-file and --key-file flags."""
    from muxplex.tls import generate_tailscale, generate_self_signed

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    hostname = "myhost.tail1234.ts.net"

    captured_args = {}

    def fake_run(cmd, *args, **kwargs):
        captured_args["cmd"] = cmd
        # Create cert/key files to simulate success
        generate_self_signed(cert_path, key_path, hostnames=[hostname])
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)

    result = generate_tailscale(cert_path, key_path, hostname)

    assert "--cert-file" in captured_args["cmd"], (
        f"'--cert-file' must be in subprocess call, got: {captured_args['cmd']!r}"
    )
    assert "--key-file" in captured_args["cmd"], (
        f"'--key-file' must be in subprocess call, got: {captured_args['cmd']!r}"
    )
    assert result is not None, "generate_tailscale() must return a dict on success"
    assert result["method"] == "tailscale", (
        f"result['method'] must be 'tailscale', got: {result.get('method')!r}"
    )


def test_generate_tailscale_returns_none_on_failure(monkeypatch, tmp_path):
    """generate_tailscale() must return None when 'tailscale cert' exits with non-zero."""
    from muxplex.tls import generate_tailscale

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    hostname = "myhost.tail1234.ts.net"

    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: type(
            "R", (), {"returncode": 1, "stdout": "", "stderr": "error"}
        )(),
    )

    result = generate_tailscale(cert_path, key_path, hostname)

    assert result is None, (
        f"generate_tailscale() must return None on non-zero exit, got: {result!r}"
    )


# ---------------------------------------------------------------------------
# 19-22. detect_mkcert() and generate_mkcert() tests
# ---------------------------------------------------------------------------


def test_detect_mkcert_returns_true_when_installed(monkeypatch):
    """detect_mkcert() must return True when mkcert is on PATH."""
    from muxplex.tls import detect_mkcert

    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/local/bin/mkcert" if name == "mkcert" else None,
    )

    result = detect_mkcert()

    assert result is True, (
        f"detect_mkcert() must return True when mkcert is installed, got: {result!r}"
    )


def test_detect_mkcert_returns_false_when_not_installed(monkeypatch):
    """detect_mkcert() must return False when mkcert is not on PATH."""
    from muxplex.tls import detect_mkcert

    monkeypatch.setattr("shutil.which", lambda name: None)

    result = detect_mkcert()

    assert result is False, (
        f"detect_mkcert() must return False when mkcert is not installed, got: {result!r}"
    )


def test_generate_mkcert_calls_mkcert_install_and_generate(monkeypatch, tmp_path):
    """generate_mkcert() must call 'mkcert -install' then 'mkcert -cert-file ...' and return result dict."""
    from muxplex.tls import generate_mkcert, generate_self_signed

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        # If '-cert-file' is in the command, create fake cert/key files
        if "-cert-file" in cmd:
            generate_self_signed(cert_path, key_path, hostnames=["localhost"])
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)

    result = generate_mkcert(cert_path, key_path)

    # Verify '-install' was called
    install_calls = [c for c in calls if "-install" in c]
    assert len(install_calls) > 0, (
        f"'mkcert -install' must have been called, got calls: {calls!r}"
    )

    # Verify '-cert-file' was called
    cert_calls = [c for c in calls if "-cert-file" in c]
    assert len(cert_calls) > 0, (
        f"'mkcert -cert-file' must have been called, got calls: {calls!r}"
    )

    assert result is not None, "generate_mkcert() must return a dict on success"
    assert result["method"] == "mkcert", (
        f"result['method'] must be 'mkcert', got: {result.get('method')!r}"
    )


def test_generate_mkcert_falls_back_when_install_fails(monkeypatch, tmp_path):
    """generate_mkcert() must return None when 'mkcert -install' exits with non-zero."""
    from muxplex.tls import generate_mkcert

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    def fake_run(cmd, *args, **kwargs):
        if "-install" in cmd:
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": "error"})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)

    result = generate_mkcert(cert_path, key_path)

    assert result is None, (
        f"generate_mkcert() must return None when mkcert -install fails, got: {result!r}"
    )


# ---------------------------------------------------------------------------
# 23. Tailscale SAN integration test
# ---------------------------------------------------------------------------


def test_generate_mkcert_includes_tailscale_sans(monkeypatch, tmp_path):
    """generate_mkcert() with extra_hostnames must include Tailscale hostname in mkcert args."""
    from muxplex.tls import generate_mkcert, generate_self_signed

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    gen_calls = []

    def fake_run(cmd, *args, **kwargs):
        if "-cert-file" in cmd:
            gen_calls.append(cmd)
            # Create fake cert/key files to simulate mkcert success
            generate_self_signed(cert_path, key_path, hostnames=["localhost"])
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)

    result = generate_mkcert(
        cert_path,
        key_path,
        extra_hostnames=["spark-1.tail8f3c4e.ts.net", "100.64.0.1"],
    )

    assert result is not None, "generate_mkcert() must return a dict on success"
    assert len(gen_calls) > 0, (
        "generate_mkcert() must have called mkcert with -cert-file"
    )
    assert "spark-1.tail8f3c4e.ts.net" in gen_calls[0], (
        f"'spark-1.tail8f3c4e.ts.net' must appear in mkcert command args, got: {gen_calls[0]!r}"
    )


# ---------------------------------------------------------------------------
# 24-25. Bug-fix regression tests
# ---------------------------------------------------------------------------


def test_detect_tailscale_reads_dns_name_from_self(monkeypatch):
    """detect_tailscale() must read DNSName from Self nested object (actual Tailscale JSON structure)."""
    import json
    import shutil
    import subprocess

    import muxplex.tls as tls_mod

    class MockResult:
        returncode = 0
        stdout = json.dumps(
            {
                "BackendState": "Running",
                "TailscaleIPs": ["100.124.126.19"],
                "CertDomains": ["spark-1.tail8f3c4e.ts.net"],
                "Self": {
                    "DNSName": "spark-1.tail8f3c4e.ts.net.",
                    "HostName": "spark-1",
                },
            }
        )

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: MockResult())
    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/tailscale")

    result = tls_mod.detect_tailscale()
    assert result is not None, (
        "detect_tailscale() must not return None when DNSName is in Self"
    )
    assert result["hostname"] == "spark-1.tail8f3c4e.ts.net", (
        f"hostname must be 'spark-1.tail8f3c4e.ts.net' (stripped from Self.DNSName), got: {result['hostname']!r}"
    )


def test_generate_self_signed_no_deprecation_warning(tmp_path):
    """generate_self_signed() and get_cert_info() must not emit CryptographyDeprecationWarning."""
    import warnings

    from muxplex.tls import generate_self_signed, get_cert_info

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        generate_self_signed(cert_path, key_path, hostnames=["localhost"])
        get_cert_info(cert_path)

    deprecation_warnings = [
        str(warning.message)
        for warning in w
        if issubclass(warning.category, (DeprecationWarning, PendingDeprecationWarning))
        and "not_valid_after" in str(warning.message).lower()
    ]
    assert deprecation_warnings == [], (
        f"CryptographyDeprecationWarning must not be raised, got: {deprecation_warnings}"
    )


# ---------------------------------------------------------------------------
# 26-34. CommonName truncation regression tests (e6133c5)
#
# RFC 5280 caps a CommonName at 64 chars (ub-common-name); `cryptography`
# raises `ValueError: Attribute's length must be >= 1 and <= 64` rather than
# truncating. A GitHub macOS runner reports a 67-char hostname, and long
# Tailscale/corporate FQDNs get there too -- e6133c5 added `_common_name()`
# to truncate the CN at all three cert-generation call sites. These tests
# cover all three: they must not just "not raise" -- they must also prove
# the FULL, untruncated hostname still reaches the SAN, since SAN (not CN)
# is what modern TLS clients actually validate against (RFC 6125 superseded
# RFC 2818). A fix that truncated the SAN too would pass a naive
# success-only test while silently breaking certificate validity.
# ---------------------------------------------------------------------------

# Comfortably over the 64-char ub-common-name limit.
_LONG_HOSTNAME = "this-hostname-is-deliberately-longer-than-the-sixty-four-character-cn-limit.example.com"
assert len(_LONG_HOSTNAME) > 64, "test fixture must itself exceed the CN limit"


def _cn_value(cert_path):
    """Read back the CommonName attribute value from a PEM cert on disk."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    assert len(cn_attrs) == 1, (
        f"expected exactly one CommonName attribute, got: {cn_attrs!r}"
    )
    return cn_attrs[0].value


# --- generate_self_signed() -------------------------------------------------


def test_generate_self_signed_long_hostname_does_not_raise(tmp_path):
    """generate_self_signed() must not raise ValueError for a hostname > 64 chars.

    Regression test for e6133c5: building the CN directly from the hostname
    raised `ValueError: Attribute's length must be >= 1 and <= 64` for any
    hostname over RFC 5280's ub-common-name limit.
    """
    from muxplex.tls import generate_self_signed

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    generate_self_signed(cert_path, key_path, hostnames=[_LONG_HOSTNAME, "localhost"])

    assert cert_path.exists(), "cert.pem was not created"
    assert key_path.exists(), "key.pem was not created"


def test_generate_self_signed_long_hostname_full_name_in_san(tmp_path):
    """The FULL, untruncated hostname must be present in the subjectAltName.

    This is the load-bearing assertion: CN truncation is only safe because
    the SAN carries the real, full hostname clients actually validate
    against.
    """
    from muxplex.tls import generate_self_signed, get_cert_info

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    generate_self_signed(cert_path, key_path, hostnames=[_LONG_HOSTNAME, "localhost"])

    info = get_cert_info(cert_path)
    assert info is not None, "get_cert_info() must not return None for a valid cert"
    assert _LONG_HOSTNAME in info["hostnames"], (
        f"full hostname {_LONG_HOSTNAME!r} must be present, untruncated, in the SAN, "
        f"got: {info['hostnames']!r}"
    )


def test_generate_self_signed_long_hostname_cn_within_limit(tmp_path):
    """The CN attribute itself must be <= 64 chars (RFC 5280 ub-common-name)."""
    from muxplex.tls import generate_self_signed

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    generate_self_signed(cert_path, key_path, hostnames=[_LONG_HOSTNAME, "localhost"])

    cn_value = _cn_value(cert_path)
    assert len(cn_value) <= 64, (
        f"CN must be <= 64 chars (RFC 5280 ub-common-name), got {len(cn_value)}: {cn_value!r}"
    )


# --- generate_local_ca() ----------------------------------------------------


def test_generate_local_ca_long_common_name_does_not_raise(tmp_path):
    """generate_local_ca() must not raise ValueError for a common_name > 64 chars."""
    from muxplex.tls import generate_local_ca

    ca_cert_path = tmp_path / "ca.pem"
    ca_key_path = tmp_path / "ca-key.pem"

    generate_local_ca(ca_cert_path, ca_key_path, common_name=_LONG_HOSTNAME)

    assert ca_cert_path.exists(), "ca.pem was not created"
    assert ca_key_path.exists(), "ca-key.pem was not created"


def test_generate_local_ca_long_common_name_cn_within_limit(tmp_path):
    """The CA's CN attribute itself must be <= 64 chars (RFC 5280 ub-common-name)."""
    from muxplex.tls import generate_local_ca

    ca_cert_path = tmp_path / "ca.pem"
    ca_key_path = tmp_path / "ca-key.pem"

    generate_local_ca(ca_cert_path, ca_key_path, common_name=_LONG_HOSTNAME)

    cn_value = _cn_value(ca_cert_path)
    assert len(cn_value) <= 64, (
        f"CA CN must be <= 64 chars (RFC 5280 ub-common-name), got {len(cn_value)}: {cn_value!r}"
    )


def test_generate_local_ca_has_no_san_extension(tmp_path):
    """generate_local_ca() takes a `common_name`, not a hostname list, and
    does not add a SubjectAlternativeName extension at all (only
    BasicConstraints, KeyUsage, and SubjectKeyIdentifier -- see its
    implementation). A CA cert is not validated against a hostname the way a
    leaf is, so there is no "full hostname in SAN" assertion that applies
    here. This test documents that fact explicitly rather than silently
    omitting SAN coverage for this path, and pins get_cert_info()'s actual
    behavior (empty hostnames list, not an error) for a cert with no SAN.
    """
    from muxplex.tls import generate_local_ca, get_cert_info

    ca_cert_path = tmp_path / "ca.pem"
    ca_key_path = tmp_path / "ca-key.pem"

    generate_local_ca(ca_cert_path, ca_key_path, common_name=_LONG_HOSTNAME)

    info = get_cert_info(ca_cert_path)
    assert info is not None, "get_cert_info() must not return None for a valid CA cert"
    assert info["hostnames"] == [], (
        f"CA cert has no SAN extension by design; expected an empty hostnames list, "
        f"got: {info['hostnames']!r}"
    )


# --- generate_leaf_signed_by_ca() -------------------------------------------


def test_generate_leaf_signed_by_ca_long_hostname_does_not_raise(tmp_path):
    """generate_leaf_signed_by_ca() must not raise ValueError for a hostname > 64 chars."""
    from muxplex.tls import generate_leaf_signed_by_ca, generate_local_ca

    ca_cert_path = tmp_path / "ca.pem"
    ca_key_path = tmp_path / "ca-key.pem"
    leaf_cert_path = tmp_path / "leaf.pem"
    leaf_key_path = tmp_path / "leaf-key.pem"

    generate_local_ca(ca_cert_path, ca_key_path)

    generate_leaf_signed_by_ca(
        ca_cert_path,
        ca_key_path,
        leaf_cert_path,
        leaf_key_path,
        hostnames=[_LONG_HOSTNAME, "localhost"],
    )

    assert leaf_cert_path.exists(), "leaf.pem was not created"
    assert leaf_key_path.exists(), "leaf-key.pem was not created"


def test_generate_leaf_signed_by_ca_long_hostname_full_name_in_san(tmp_path):
    """The FULL, untruncated hostname must be present in the leaf's subjectAltName."""
    from muxplex.tls import generate_leaf_signed_by_ca, generate_local_ca, get_cert_info

    ca_cert_path = tmp_path / "ca.pem"
    ca_key_path = tmp_path / "ca-key.pem"
    leaf_cert_path = tmp_path / "leaf.pem"
    leaf_key_path = tmp_path / "leaf-key.pem"

    generate_local_ca(ca_cert_path, ca_key_path)
    generate_leaf_signed_by_ca(
        ca_cert_path,
        ca_key_path,
        leaf_cert_path,
        leaf_key_path,
        hostnames=[_LONG_HOSTNAME, "localhost"],
    )

    info = get_cert_info(leaf_cert_path)
    assert info is not None, "get_cert_info() must not return None for a valid cert"
    assert _LONG_HOSTNAME in info["hostnames"], (
        f"full hostname {_LONG_HOSTNAME!r} must be present, untruncated, in the SAN, "
        f"got: {info['hostnames']!r}"
    )


def test_generate_leaf_signed_by_ca_long_hostname_cn_within_limit(tmp_path):
    """The leaf's CN attribute itself must be <= 64 chars (RFC 5280 ub-common-name)."""
    from muxplex.tls import generate_leaf_signed_by_ca, generate_local_ca

    ca_cert_path = tmp_path / "ca.pem"
    ca_key_path = tmp_path / "ca-key.pem"
    leaf_cert_path = tmp_path / "leaf.pem"
    leaf_key_path = tmp_path / "leaf-key.pem"

    generate_local_ca(ca_cert_path, ca_key_path)
    generate_leaf_signed_by_ca(
        ca_cert_path,
        ca_key_path,
        leaf_cert_path,
        leaf_key_path,
        hostnames=[_LONG_HOSTNAME, "localhost"],
    )

    cn_value = _cn_value(leaf_cert_path)
    assert len(cn_value) <= 64, (
        f"leaf CN must be <= 64 chars (RFC 5280 ub-common-name), got {len(cn_value)}: {cn_value!r}"
    )
