"""
muxplex/tls.py — TLS certificate generation and inspection.

Provides:
  generate_self_signed(cert_path, key_path, hostnames=None, days_valid=3650)
  get_cert_info(cert_path)
"""

import ipaddress
import socket
from datetime import datetime, timezone
from pathlib import Path


def _default_hostnames() -> list[str]:
    hostname = socket.gethostname()
    return [hostname, f"{hostname}.local", "localhost"]


def generate_self_signed(
    cert_path,
    key_path,
    hostnames: list[str] | None = None,
    days_valid: int = 3650,
) -> dict:
    """Generate a self-signed TLS certificate and private key.

    Args:
        cert_path: Destination path for the certificate PEM file.
        key_path:  Destination path for the private key PEM file.
        hostnames: DNS names to include. Defaults to [hostname, hostname.local, localhost].
        days_valid: Certificate validity period in days. Default 3650 (≈10 years).

    Returns:
        dict with keys: method, cert_path, key_path, hostnames, expires.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    if hostnames is None:
        hostnames = _default_hostnames()

    cert_path = Path(cert_path)
    key_path = Path(key_path)

    # Create parent directories if needed
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    # Generate RSA 2048-bit private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    # Build subject / issuer with CN = first hostname, O = muxplex
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, hostnames[0]),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "muxplex"),
        ]
    )

    # Build SAN extension: DNS names for all hostnames + loopback IPs
    san_entries: list[x509.GeneralName] = [x509.DNSName(h) for h in hostnames]
    san_entries.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))
    san_entries.append(x509.IPAddress(ipaddress.IPv6Address("::1")))

    now = datetime.now(timezone.utc)

    # Build the certificate
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(
            datetime.fromtimestamp(
                now.timestamp() + days_valid * 86400,
                tz=timezone.utc,
            )
        )
        .add_extension(
            x509.SubjectAlternativeName(san_entries),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )

    try:
        expires = cert.not_valid_after_utc  # type: ignore[attr-defined]
    except AttributeError:
        expires = cert.not_valid_after  # type: ignore[attr-defined]

    # Write key PEM — create file with restricted permissions before writing
    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path.touch(mode=0o600, exist_ok=True)
    key_path.write_bytes(key_pem)
    key_path.chmod(0o600)

    # Write cert PEM
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    cert_path.write_bytes(cert_pem)

    return {
        "method": "selfsigned",
        "cert_path": str(cert_path),
        "key_path": str(key_path),
        "hostnames": hostnames,
        "expires": expires,
    }


def detect_tailscale() -> dict | None:
    """Probe for Tailscale and return connection info if available.

    Checks whether the Tailscale CLI is installed, verifies the node is
    connected, and confirms HTTPS certificate domains are enabled.

    Returns:
        dict with keys: hostname (str), ips (list[str]), cert_domains (list[str])
        if Tailscale is installed, connected, and cert domains are configured.
        Returns None if any of these conditions are not met.
    """
    import json
    import shutil
    import subprocess

    if not shutil.which("tailscale"):
        return None

    try:
        result = subprocess.run(
            ["tailscale", "status", "--self", "--json"],
            timeout=10,
            capture_output=True,
            text=True,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

    self_info = data.get("Self") or {}
    dns_name = self_info.get("DNSName", "") or data.get("DNSName", "")
    cert_domains = data.get("CertDomains") or []
    ips = data.get("TailscaleIPs") or []

    if not dns_name or not cert_domains:
        return None

    return {
        "hostname": dns_name.rstrip("."),
        "ips": ips,
        "cert_domains": cert_domains,
    }


def generate_tailscale(cert_path, key_path, hostname: str) -> dict | None:
    """Obtain a Let's Encrypt certificate via Tailscale.

    Args:
        cert_path: Destination path for the certificate PEM file.
        key_path:  Destination path for the private key PEM file.
        hostname:  Tailscale hostname to request a certificate for.

    Returns:
        dict with keys: method, cert_path, key_path, hostnames, expires.
        Returns None on failure (non-zero exit, timeout, OS error, or missing files).
    """
    import subprocess

    cert_path = Path(cert_path)
    key_path = Path(key_path)

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            [
                "tailscale",
                "cert",
                "--cert-file",
                str(cert_path),
                "--key-file",
                str(key_path),
                hostname,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    if not cert_path.exists() or not key_path.exists():
        return None

    key_path.chmod(0o600)

    info = get_cert_info(cert_path)
    if info is None:
        return None

    return {
        "method": "tailscale",
        "cert_path": str(cert_path),
        "key_path": str(key_path),
        "hostnames": info["hostnames"],
        "expires": info["expires"],
    }


def detect_mkcert() -> bool:
    """Return True if mkcert is available on PATH, False otherwise."""
    import shutil

    return shutil.which("mkcert") is not None


def generate_mkcert(
    cert_path,
    key_path,
    extra_hostnames: list[str] | None = None,
) -> dict | None:
    """Generate a locally-trusted certificate via mkcert.

    Args:
        cert_path: Destination path for the certificate PEM file.
        key_path:  Destination path for the private key PEM file.
        extra_hostnames: Additional hostnames to include in the certificate.

    Returns:
        dict with keys: method, cert_path, key_path, hostnames, expires.
        Returns None on failure (mkcert not found, non-zero exit, timeout, or missing files).
    """
    import subprocess

    cert_path = Path(cert_path)
    key_path = Path(key_path)

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    # Step 1: install the local CA
    try:
        result = subprocess.run(
            ["mkcert", "-install"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    # Step 2: build deduplicated hostname list (preserving order)
    hostname = socket.gethostname()
    base_hostnames = [hostname, f"{hostname}.local", "localhost", "127.0.0.1", "::1"]
    if extra_hostnames:
        base_hostnames.extend(extra_hostnames)

    seen: set[str] = set()
    unique_hostnames: list[str] = []
    for h in base_hostnames:
        if h not in seen:
            seen.add(h)
            unique_hostnames.append(h)

    # Step 3: generate the certificate
    try:
        result = subprocess.run(
            [
                "mkcert",
                "-cert-file",
                str(cert_path),
                "-key-file",
                str(key_path),
                *unique_hostnames,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    if not cert_path.exists() or not key_path.exists():
        return None

    key_path.chmod(0o600)

    info = get_cert_info(cert_path)
    expires = info["expires"] if info else None

    return {
        "method": "mkcert",
        "cert_path": str(cert_path),
        "key_path": str(key_path),
        "hostnames": unique_hostnames,
        "expires": expires,
    }


def get_cert_info(cert_path) -> dict | None:
    """Inspect a PEM certificate and return metadata.

    Args:
        cert_path: Path to the PEM certificate file.

    Returns:
        dict with expires, not_before, hostnames (DNS names + IPs from SANs), serial.
        Returns None if the file is missing or cannot be parsed.
    """
    from cryptography import x509
    from cryptography.x509.extensions import ExtensionNotFound

    cert_path = Path(cert_path)

    try:
        pem_data = cert_path.read_bytes()
    except (FileNotFoundError, PermissionError, OSError):
        return None

    try:
        cert = x509.load_pem_x509_certificate(pem_data)
    except Exception:
        return None

    # Extract hostnames from SAN extension
    hostnames: list[str] = []
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        for entry in san.value:
            if isinstance(entry, x509.DNSName):
                hostnames.append(entry.value)
            elif isinstance(entry, x509.IPAddress):
                hostnames.append(str(entry.value))
    except ExtensionNotFound:
        pass

    try:
        expires = cert.not_valid_after_utc  # type: ignore[attr-defined]
    except AttributeError:
        expires = cert.not_valid_after  # type: ignore[attr-defined]

    try:
        not_before = cert.not_valid_before_utc  # type: ignore[attr-defined]
    except AttributeError:
        not_before = cert.not_valid_before  # type: ignore[attr-defined]

    return {
        "expires": expires,
        "not_before": not_before,
        "hostnames": hostnames,
        "serial": cert.serial_number,
    }
