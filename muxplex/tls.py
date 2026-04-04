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

    expires = getattr(cert, "not_valid_after_utc", cert.not_valid_after)

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

    dns_name = data.get("DNSName", "")
    cert_domains = data.get("CertDomains") or []
    ips = data.get("TailscaleIPs") or []

    if not dns_name or not cert_domains:
        return None

    return {
        "hostname": dns_name.rstrip("."),
        "ips": ips,
        "cert_domains": cert_domains,
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

    return {
        "expires": getattr(cert, "not_valid_after_utc", cert.not_valid_after),
        "not_before": getattr(cert, "not_valid_before_utc", cert.not_valid_before),
        "hostnames": hostnames,
        "serial": cert.serial_number,
    }
