# TLS Setup — Phase 2: Auto-detection + Tailscale + mkcert

> **Execution:** Use the subagent-driven-development workflow to implement this plan.
>
> **Phase 2 of 2.** Phase 1 (foundation) must be complete before starting this phase.
>
> **Design doc:** `docs/plans/2026-04-03-tls-setup-design.md`
>
> **Phase 1 plan:** `docs/plans/2026-04-03-tls-phase1-foundation.md`

**Goal:** Add Tailscale and mkcert detection to `setup-tls`, build the auto-detection chain (Tailscale → mkcert → self-signed), add `--status` display, and handle existing cert detection with regenerate prompts.

**Architecture:** `muxplex/tls.py` gains detection functions for Tailscale and mkcert, plus an auto-detection orchestrator. Each detection function uses `shutil.which` + `subprocess.run` to probe external tools. The `setup_tls()` function in `cli.py` is extended with the full auto chain. `--status` reuses `get_cert_info()` from Phase 1.

**Tech Stack:** Python stdlib (`subprocess`, `shutil`, `json`), `cryptography` (from Phase 1), external CLIs (`tailscale`, `mkcert`).

**Prerequisites from Phase 1:**
- `muxplex/tls.py` exists with `generate_self_signed()` and `get_cert_info()`
- `muxplex/cli.py` has `setup_tls()` function and `setup-tls` subparser
- `tls_cert` / `tls_key` in `DEFAULT_SETTINGS`
- `serve()` handles SSL params
- Doctor shows TLS status

**Scope boundaries:**
- **IN this phase:** Tailscale detection, mkcert detection, auto-detection chain, `--status`, existing cert regenerate prompt
- **OUT of scope:** Automatic cert renewal cron, Caddy integration, Let's Encrypt DNS-01

---

### Task 1: Tailscale detection in `tls.py`

**Files:**
- Modify: `muxplex/tls.py` (add `detect_tailscale()` function)
- Modify: `muxplex/tests/test_tls.py` (add Tailscale detection tests)

**Step 1: Write failing tests**

Append to `muxplex/tests/test_tls.py`:

```python
# ---------------------------------------------------------------------------
# Tailscale detection tests
# ---------------------------------------------------------------------------


def test_detect_tailscale_returns_info_when_available(monkeypatch):
    """detect_tailscale() returns dict with hostname and ip when Tailscale is connected with MagicDNS."""
    import shutil
    import subprocess
    from muxplex.tls import detect_tailscale

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tailscale" if name == "tailscale" else None)

    fake_status = {
        "Self": {
            "DNSName": "spark-1.tail8f3c4e.ts.net.",
            "TailscaleIPs": ["100.64.0.1", "fd7a:115c:a1e0::1"],
        },
        "CertDomains": ["spark-1.tail8f3c4e.ts.net"],
    }

    def fake_run(cmd, **kw):
        if "status" in cmd:
            return type("R", (), {"returncode": 0, "stdout": json.dumps(fake_status), "stderr": ""})()
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = detect_tailscale()

    assert result is not None
    assert result["hostname"] == "spark-1.tail8f3c4e.ts.net"
    assert "100.64.0.1" in result["ips"]


def test_detect_tailscale_returns_none_when_not_installed(monkeypatch):
    """detect_tailscale() returns None when tailscale is not in PATH."""
    import shutil
    from muxplex.tls import detect_tailscale

    monkeypatch.setattr(shutil, "which", lambda name: None)

    result = detect_tailscale()
    assert result is None


def test_detect_tailscale_returns_none_when_not_connected(monkeypatch):
    """detect_tailscale() returns None when tailscale status exits non-zero."""
    import shutil
    import subprocess
    from muxplex.tls import detect_tailscale

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tailscale" if name == "tailscale" else None)

    def fake_run(cmd, **kw):
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": "not connected"})()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = detect_tailscale()
    assert result is None


def test_detect_tailscale_returns_none_when_no_cert_domains(monkeypatch):
    """detect_tailscale() returns None when CertDomains is empty (HTTPS certs not enabled)."""
    import shutil
    import subprocess
    from muxplex.tls import detect_tailscale

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tailscale" if name == "tailscale" else None)

    fake_status = {
        "Self": {
            "DNSName": "spark-1.tail8f3c4e.ts.net.",
            "TailscaleIPs": ["100.64.0.1"],
        },
        "CertDomains": [],
    }

    def fake_run(cmd, **kw):
        return type("R", (), {"returncode": 0, "stdout": json.dumps(fake_status), "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = detect_tailscale()
    assert result is None
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_tls.py::test_detect_tailscale_returns_info_when_available muxplex/tests/test_tls.py::test_detect_tailscale_returns_none_when_not_installed -v
```

Expected: FAIL — `ImportError: cannot import name 'detect_tailscale'`.

**Step 3: Implement `detect_tailscale()` in `muxplex/tls.py`**

Add to `muxplex/tls.py`:

```python
def detect_tailscale() -> dict | None:
    """Detect if Tailscale is running with MagicDNS and HTTPS certs enabled.

    Returns:
        Dict with keys: hostname, ips, cert_domains.
        Returns None if Tailscale is not installed, not connected, or HTTPS certs are not enabled.
    """
    import json
    import shutil
    import subprocess

    if not shutil.which("tailscale"):
        return None

    try:
        result = subprocess.run(
            ["tailscale", "status", "--self", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None

    # Check CertDomains — empty means HTTPS certs not enabled in admin console
    cert_domains = data.get("CertDomains", [])
    if not cert_domains:
        return None

    self_info = data.get("Self", {})
    dns_name = self_info.get("DNSName", "").rstrip(".")
    ips = self_info.get("TailscaleIPs", [])

    if not dns_name:
        return None

    return {
        "hostname": dns_name,
        "ips": ips,
        "cert_domains": cert_domains,
    }
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_tls.py -v
```

Expected: ALL PASS.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/tls.py muxplex/tests/test_tls.py && git commit -m "feat: add Tailscale detection to tls.py"
```

---

### Task 2: Tailscale cert generation

**Files:**
- Modify: `muxplex/tls.py` (add `generate_tailscale()` function)
- Modify: `muxplex/tests/test_tls.py` (add Tailscale cert generation tests)

**Step 1: Write failing tests**

Append to `muxplex/tests/test_tls.py`:

```python
# ---------------------------------------------------------------------------
# Tailscale cert generation tests
# ---------------------------------------------------------------------------


def test_generate_tailscale_calls_tailscale_cert(tmp_path, monkeypatch):
    """generate_tailscale() calls 'tailscale cert --cert-file ... --key-file ...'."""
    import subprocess
    from muxplex.tls import generate_tailscale

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        # Create the cert/key files so the function finds them
        cert_path.write_text("-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n")
        key_path.write_text("-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----\n")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = generate_tailscale(
        cert_path=cert_path,
        key_path=key_path,
        hostname="spark-1.tail8f3c4e.ts.net",
    )

    assert result is not None
    assert result["method"] == "tailscale"
    # Must have called tailscale cert with the right flags
    tailscale_calls = [c for c in calls if "tailscale" in str(c) and "cert" in str(c)]
    assert len(tailscale_calls) > 0
    cmd = tailscale_calls[0]
    assert "--cert-file" in cmd
    assert "--key-file" in cmd


def test_generate_tailscale_returns_none_on_failure(tmp_path, monkeypatch):
    """generate_tailscale() returns None when tailscale cert fails."""
    import subprocess
    from muxplex.tls import generate_tailscale

    def fake_run(cmd, **kw):
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": "ACME error"})()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = generate_tailscale(
        cert_path=tmp_path / "cert.pem",
        key_path=tmp_path / "key.pem",
        hostname="spark-1.tail8f3c4e.ts.net",
    )
    assert result is None
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_tls.py::test_generate_tailscale_calls_tailscale_cert muxplex/tests/test_tls.py::test_generate_tailscale_returns_none_on_failure -v
```

Expected: FAIL — `ImportError: cannot import name 'generate_tailscale'`.

**Step 3: Implement `generate_tailscale()` in `muxplex/tls.py`**

Add to `muxplex/tls.py`:

```python
def generate_tailscale(
    cert_path: Path | str,
    key_path: Path | str,
    hostname: str,
) -> dict | None:
    """Generate a TLS certificate using Tailscale's built-in ACME integration.

    Args:
        cert_path: Where to write the certificate PEM file.
        key_path: Where to write the private key PEM file.
        hostname: Tailscale MagicDNS hostname (e.g., spark-1.tail8f3c4e.ts.net).

    Returns:
        Dict with keys: method, cert_path, key_path, hostnames, expires.
        Returns None if tailscale cert fails.
    """
    import subprocess

    cert_path = Path(cert_path)
    key_path = Path(key_path)

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            [
                "tailscale", "cert",
                "--cert-file", str(cert_path),
                "--key-file", str(key_path),
                hostname,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
    except (subprocess.TimeoutExpired, OSError):
        return None

    if not cert_path.is_file() or not key_path.is_file():
        return None

    key_path.chmod(0o600)

    # Read expiry from the generated cert
    info = get_cert_info(cert_path)
    expires = info["expires"] if info else "unknown"
    hostnames = info["hostnames"] if info else [hostname]

    return {
        "method": "tailscale",
        "cert_path": str(cert_path),
        "key_path": str(key_path),
        "hostnames": hostnames,
        "expires": expires,
    }
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_tls.py -v
```

Expected: ALL PASS.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/tls.py muxplex/tests/test_tls.py && git commit -m "feat: add Tailscale cert generation to tls.py"
```

---

### Task 3: mkcert detection and cert generation

**Files:**
- Modify: `muxplex/tls.py` (add `detect_mkcert()` and `generate_mkcert()`)
- Modify: `muxplex/tests/test_tls.py` (add mkcert tests)

**Step 1: Write failing tests**

Append to `muxplex/tests/test_tls.py`:

```python
# ---------------------------------------------------------------------------
# mkcert detection and generation tests
# ---------------------------------------------------------------------------


def test_detect_mkcert_returns_true_when_installed(monkeypatch):
    """detect_mkcert() returns True when mkcert is in PATH."""
    import shutil
    from muxplex.tls import detect_mkcert

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/mkcert" if name == "mkcert" else None)

    assert detect_mkcert() is True


def test_detect_mkcert_returns_false_when_not_installed(monkeypatch):
    """detect_mkcert() returns False when mkcert is not in PATH."""
    import shutil
    from muxplex.tls import detect_mkcert

    monkeypatch.setattr(shutil, "which", lambda name: None)

    assert detect_mkcert() is False


def test_generate_mkcert_calls_mkcert_install_and_generate(tmp_path, monkeypatch):
    """generate_mkcert() calls 'mkcert -install' then 'mkcert -cert-file ... -key-file ...'."""
    import subprocess
    from muxplex.tls import generate_mkcert

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if "-cert-file" in cmd:
            # Simulate mkcert creating the files
            cert_path.write_text("-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n")
            key_path.write_text("-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----\n")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = generate_mkcert(cert_path=cert_path, key_path=key_path)

    assert result is not None
    assert result["method"] == "mkcert"

    # Must have called mkcert -install
    install_calls = [c for c in calls if "-install" in c]
    assert len(install_calls) > 0, "must call mkcert -install"

    # Must have called mkcert with -cert-file and -key-file
    gen_calls = [c for c in calls if "-cert-file" in c]
    assert len(gen_calls) > 0, "must call mkcert with -cert-file"


def test_generate_mkcert_falls_back_when_install_fails(tmp_path, monkeypatch):
    """generate_mkcert() returns None when 'mkcert -install' fails."""
    import subprocess
    from muxplex.tls import generate_mkcert

    def fake_run(cmd, **kw):
        if "-install" in cmd:
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": "no sudo"})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = generate_mkcert(cert_path=tmp_path / "cert.pem", key_path=tmp_path / "key.pem")
    assert result is None
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_tls.py::test_detect_mkcert_returns_true_when_installed muxplex/tests/test_tls.py::test_generate_mkcert_calls_mkcert_install_and_generate -v
```

Expected: FAIL — `ImportError: cannot import name 'detect_mkcert'`.

**Step 3: Implement mkcert functions in `muxplex/tls.py`**

Add to `muxplex/tls.py`:

```python
def detect_mkcert() -> bool:
    """Check if mkcert is installed and available in PATH."""
    import shutil

    return shutil.which("mkcert") is not None


def generate_mkcert(
    cert_path: Path | str,
    key_path: Path | str,
    extra_hostnames: list[str] | None = None,
) -> dict | None:
    """Generate a TLS certificate using mkcert (locally-trusted CA).

    Runs ``mkcert -install`` first to ensure the local CA is in the trust store,
    then generates a cert for localhost, the machine hostname, and any extras.

    Args:
        cert_path: Where to write the certificate PEM file.
        key_path: Where to write the private key PEM file.
        extra_hostnames: Additional hostnames/IPs to include as SANs.

    Returns:
        Dict with keys: method, cert_path, key_path, hostnames, expires.
        Returns None if mkcert -install or cert generation fails.
    """
    import subprocess

    cert_path = Path(cert_path)
    key_path = Path(key_path)

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    # Step 1: Install local CA (may prompt for sudo/keychain)
    try:
        result = subprocess.run(
            ["mkcert", "-install"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
    except (subprocess.TimeoutExpired, OSError):
        return None

    # Step 2: Build hostname list
    hostname = socket.gethostname()
    hostnames = [hostname, f"{hostname}.local", "localhost", "127.0.0.1", "::1"]
    if extra_hostnames:
        hostnames.extend(extra_hostnames)
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_hostnames: list[str] = []
    for h in hostnames:
        if h not in seen:
            seen.add(h)
            unique_hostnames.append(h)

    # Step 3: Generate cert
    try:
        result = subprocess.run(
            [
                "mkcert",
                "-cert-file", str(cert_path),
                "-key-file", str(key_path),
                *unique_hostnames,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
    except (subprocess.TimeoutExpired, OSError):
        return None

    if not cert_path.is_file() or not key_path.is_file():
        return None

    key_path.chmod(0o600)

    # Read expiry from the generated cert
    info = get_cert_info(cert_path)
    expires = info["expires"] if info else "unknown"

    return {
        "method": "mkcert",
        "cert_path": str(cert_path),
        "key_path": str(key_path),
        "hostnames": unique_hostnames,
        "expires": expires,
    }
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_tls.py -v
```

Expected: ALL PASS.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/tls.py muxplex/tests/test_tls.py && git commit -m "feat: add mkcert detection and cert generation to tls.py"
```

---

### Task 4: Auto-detection chain in `setup_tls()`

**Files:**
- Modify: `muxplex/cli.py` (rewrite `setup_tls()` with full auto-detection)
- Modify: `muxplex/tests/test_cli.py` (add auto-detection tests)

**Step 1: Write failing tests**

Append to `muxplex/tests/test_cli.py`:

```python
# ---------------------------------------------------------------------------
# setup-tls auto-detection chain tests
# ---------------------------------------------------------------------------


def test_setup_tls_auto_uses_tailscale_when_available(tmp_path, monkeypatch, capsys):
    """setup_tls(method='auto') uses Tailscale when detect_tailscale returns info."""
    import muxplex.settings as settings_mod
    import muxplex.tls as tls_mod

    fake_config = tmp_path / ".config" / "muxplex"
    fake_config.mkdir(parents=True)
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_config / "settings.json")

    # Mock Tailscale detection as available
    monkeypatch.setattr(tls_mod, "detect_tailscale", lambda: {
        "hostname": "spark-1.tail8f3c4e.ts.net",
        "ips": ["100.64.0.1"],
        "cert_domains": ["spark-1.tail8f3c4e.ts.net"],
    })

    # Mock Tailscale cert generation as successful
    monkeypatch.setattr(tls_mod, "generate_tailscale", lambda **kw: {
        "method": "tailscale",
        "cert_path": str(fake_config / "cert.pem"),
        "key_path": str(fake_config / "key.pem"),
        "hostnames": ["spark-1.tail8f3c4e.ts.net"],
        "expires": "2026-07-03T00:00:00+00:00",
    })

    from muxplex.cli import setup_tls
    setup_tls(method="auto")

    captured = capsys.readouterr()
    assert "tailscale" in captured.out.lower()


def test_setup_tls_auto_falls_to_mkcert_when_no_tailscale(tmp_path, monkeypatch, capsys):
    """setup_tls(method='auto') falls through to mkcert when Tailscale not available."""
    import muxplex.settings as settings_mod
    import muxplex.tls as tls_mod

    fake_config = tmp_path / ".config" / "muxplex"
    fake_config.mkdir(parents=True)
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_config / "settings.json")

    monkeypatch.setattr(tls_mod, "detect_tailscale", lambda: None)
    monkeypatch.setattr(tls_mod, "detect_mkcert", lambda: True)
    monkeypatch.setattr(tls_mod, "generate_mkcert", lambda **kw: {
        "method": "mkcert",
        "cert_path": str(fake_config / "cert.pem"),
        "key_path": str(fake_config / "key.pem"),
        "hostnames": ["myhost", "localhost"],
        "expires": "2028-04-03T00:00:00+00:00",
    })

    from muxplex.cli import setup_tls
    setup_tls(method="auto")

    captured = capsys.readouterr()
    assert "mkcert" in captured.out.lower()


def test_setup_tls_auto_falls_to_selfsigned_when_nothing_available(tmp_path, monkeypatch, capsys):
    """setup_tls(method='auto') falls through to self-signed when nothing else is available."""
    import muxplex.settings as settings_mod
    import muxplex.tls as tls_mod

    fake_config = tmp_path / ".config" / "muxplex"
    fake_config.mkdir(parents=True)
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_config / "settings.json")

    monkeypatch.setattr(tls_mod, "detect_tailscale", lambda: None)
    monkeypatch.setattr(tls_mod, "detect_mkcert", lambda: False)

    from muxplex.cli import setup_tls
    setup_tls(method="auto")

    captured = capsys.readouterr()
    assert "self-signed" in captured.out.lower() or "selfsigned" in captured.out.lower()


def test_setup_tls_method_choices_expanded():
    """setup-tls --method must accept tailscale and mkcert in addition to auto and selfsigned."""
    import io
    from muxplex.cli import main

    buf = io.StringIO()
    with patch("sys.argv", ["muxplex", "setup-tls", "--help"]):
        try:
            with patch("sys.stdout", buf):
                main()
        except SystemExit:
            pass

    help_text = buf.getvalue()
    assert "tailscale" in help_text
    assert "mkcert" in help_text
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_cli.py::test_setup_tls_auto_uses_tailscale_when_available muxplex/tests/test_cli.py::test_setup_tls_method_choices_expanded -v
```

Expected: FAIL — `setup_tls()` doesn't know about Tailscale or mkcert yet.

**Step 3: Rewrite `setup_tls()` in `muxplex/cli.py`**

Replace the existing `setup_tls()` function with:

```python
def setup_tls(method: str = "auto") -> None:
    """Set up TLS certificates for HTTPS.

    Auto-detection chain: Tailscale → mkcert → self-signed.
    """
    from muxplex.settings import SETTINGS_PATH, patch_settings  # noqa: PLC0415
    from muxplex.tls import (  # noqa: PLC0415
        detect_mkcert,
        detect_tailscale,
        generate_mkcert,
        generate_self_signed,
        generate_tailscale,
    )

    config_dir = SETTINGS_PATH.parent
    cert_path = config_dir / "cert.pem"
    key_path = config_dir / "key.pem"

    result = None

    if method in ("auto", "tailscale"):
        ts_info = detect_tailscale()
        if ts_info:
            print(f"  Tailscale detected: {ts_info['hostname']}")
            result = generate_tailscale(
                cert_path=cert_path,
                key_path=key_path,
                hostname=ts_info["hostname"],
            )
            if result:
                print(f"\n  TLS configured (Tailscale — real Let's Encrypt cert)")
            else:
                print("  Tailscale cert generation failed.")
                if method == "tailscale":
                    print("  Check: are HTTPS Certificates enabled in your Tailscale admin console?", file=sys.stderr)
                    sys.exit(1)
        elif method == "tailscale":
            print("  Tailscale not detected or HTTPS certs not enabled.", file=sys.stderr)
            print("  Enable HTTPS Certificates in your Tailscale admin console, then re-run.", file=sys.stderr)
            sys.exit(1)

    if result is None and method in ("auto", "mkcert"):
        if detect_mkcert():
            print("  mkcert detected — generating locally-trusted certificate...")
            # If Tailscale is detected, add its hostname/IP as extra SANs
            extra = []
            ts_info = detect_tailscale()
            if ts_info:
                extra.append(ts_info["hostname"])
                extra.extend(ts_info["ips"])
            result = generate_mkcert(
                cert_path=cert_path,
                key_path=key_path,
                extra_hostnames=extra if extra else None,
            )
            if result:
                print(f"\n  TLS configured (mkcert — locally-trusted CA)")
            else:
                print("  mkcert cert generation failed.")
                if method == "mkcert":
                    sys.exit(1)
        elif method == "mkcert":
            print("  mkcert not found in PATH.", file=sys.stderr)
            print("  Install: https://github.com/FiloSottile/mkcert#installation", file=sys.stderr)
            sys.exit(1)

    if result is None and method in ("auto", "selfsigned"):
        result = generate_self_signed(cert_path=cert_path, key_path=key_path)
        print(f"\n  TLS configured (self-signed)")

    if result is None:
        print(f"  Failed to generate TLS certificates with method: {method}", file=sys.stderr)
        sys.exit(1)

    # Update settings with cert paths
    patch_settings({"tls_cert": result["cert_path"], "tls_key": result["key_path"]})

    print(f"  Certificate: {result['cert_path']}")
    print(f"  Key:         {result['key_path']}")
    print(f"  Hostnames:   {', '.join(result['hostnames'])}")
    print(f"  Expires:     {result['expires']}")

    if result["method"] == "selfsigned":
        print()
        print("  Warning: Browsers will show a security warning.")
        print("  For zero-warning HTTPS, install mkcert or use Tailscale.")
    elif result["method"] == "tailscale":
        print()
        print("  Tailscale certs expire in 90 days. Run `muxplex setup-tls` again to renew.")

    print()
    print("  Restart service to apply: muxplex service restart")
    print()
```

Also update the argparse `setup_tls_parser` choices in `main()` to include all methods:

```python
    setup_tls_parser = sub.add_parser("setup-tls", help="Set up TLS certificates for HTTPS")
    setup_tls_parser.add_argument(
        "--method",
        choices=["auto", "tailscale", "mkcert", "selfsigned"],
        default="auto",
        help="TLS method: auto (detect best), tailscale, mkcert, selfsigned (default: auto)",
    )
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_cli.py -v
```

Expected: ALL PASS.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "feat: full auto-detection chain in setup-tls (Tailscale → mkcert → self-signed)"
```

---

### Task 5: `setup-tls --status` display

**Files:**
- Modify: `muxplex/cli.py` (add `--status` flag and display logic)
- Modify: `muxplex/tests/test_cli.py` (add status tests)

**Step 1: Write failing tests**

Append to `muxplex/tests/test_cli.py`:

```python
# ---------------------------------------------------------------------------
# setup-tls --status tests
# ---------------------------------------------------------------------------


def test_setup_tls_status_shows_disabled(tmp_path, monkeypatch, capsys):
    """setup-tls --status shows 'TLS: not configured' when no certs set."""
    import muxplex.settings as settings_mod

    fake_config = tmp_path / ".config" / "muxplex"
    fake_config.mkdir(parents=True)
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_config / "settings.json")

    from muxplex.cli import setup_tls_status
    setup_tls_status()

    captured = capsys.readouterr()
    assert "not configured" in captured.out.lower() or "disabled" in captured.out.lower()


def test_setup_tls_status_shows_enabled(tmp_path, monkeypatch, capsys):
    """setup-tls --status shows cert info when TLS is configured."""
    import muxplex.settings as settings_mod

    fake_config = tmp_path / ".config" / "muxplex"
    fake_config.mkdir(parents=True)

    cert_file = fake_config / "cert.pem"
    key_file = fake_config / "key.pem"
    from muxplex.tls import generate_self_signed
    generate_self_signed(cert_path=cert_file, key_path=key_file)

    settings_file = fake_config / "settings.json"
    settings_file.write_text(json.dumps({
        "tls_cert": str(cert_file),
        "tls_key": str(key_file),
    }))
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    from muxplex.cli import setup_tls_status
    setup_tls_status()

    captured = capsys.readouterr()
    out = captured.out.lower()
    assert "enabled" in out or "certificate" in out
    assert "expires" in out
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_cli.py::test_setup_tls_status_shows_disabled muxplex/tests/test_cli.py::test_setup_tls_status_shows_enabled -v
```

Expected: FAIL — `ImportError: cannot import name 'setup_tls_status'`.

**Step 3: Add `setup_tls_status()` and `--status` flag**

Add `setup_tls_status()` to `muxplex/cli.py` (near `setup_tls()`):

```python
def setup_tls_status() -> None:
    """Show current TLS configuration status."""
    from muxplex.settings import load_settings  # noqa: PLC0415
    from muxplex.tls import get_cert_info  # noqa: PLC0415

    settings = load_settings()
    tls_cert = settings.get("tls_cert", "")
    tls_key = settings.get("tls_key", "")

    print("\n  muxplex TLS status\n")

    if not tls_cert or not tls_key:
        print("  TLS: not configured")
        print("  Run: muxplex setup-tls")
        print()
        return

    print(f"  Certificate: {tls_cert}")
    print(f"  Key:         {tls_key}")

    info = get_cert_info(tls_cert)
    if info:
        print(f"  Hostnames:   {', '.join(info['hostnames'])}")
        print(f"  Expires:     {info['expires']}")
        print(f"  Status:      enabled")
    else:
        print(f"  Status:      configured but cert not readable")

    print()
```

Add `--status` flag to the `setup_tls_parser` in `main()`:

```python
    setup_tls_parser.add_argument(
        "--status",
        action="store_true",
        help="Show current TLS configuration status",
    )
```

Update the dispatch in `main()`:

```python
    elif args.command == "setup-tls":
        if getattr(args, "status", False):
            setup_tls_status()
        else:
            setup_tls(method=args.method)
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_cli.py -v
```

Expected: ALL PASS.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "feat: add setup-tls --status display"
```

---

### Task 6: Existing cert detection and regenerate prompt

**Files:**
- Modify: `muxplex/cli.py` (add existing cert check at top of `setup_tls()`)
- Modify: `muxplex/tests/test_cli.py` (add regenerate prompt tests)

**Step 1: Write failing tests**

Append to `muxplex/tests/test_cli.py`:

```python
# ---------------------------------------------------------------------------
# Existing cert detection tests
# ---------------------------------------------------------------------------


def test_setup_tls_prompts_when_certs_exist(tmp_path, monkeypatch, capsys):
    """setup_tls() prompts when TLS is already configured and user says no."""
    import muxplex.settings as settings_mod
    import muxplex.tls as tls_mod

    fake_config = tmp_path / ".config" / "muxplex"
    fake_config.mkdir(parents=True)

    cert_file = fake_config / "cert.pem"
    key_file = fake_config / "key.pem"
    from muxplex.tls import generate_self_signed
    generate_self_signed(cert_path=cert_file, key_path=key_file)

    settings_file = fake_config / "settings.json"
    settings_file.write_text(json.dumps({
        "tls_cert": str(cert_file),
        "tls_key": str(key_file),
    }))
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    # User says "n" to regenerate
    monkeypatch.setattr("builtins.input", lambda _: "n")

    # Disable detection to isolate prompt behavior
    monkeypatch.setattr(tls_mod, "detect_tailscale", lambda: None)
    monkeypatch.setattr(tls_mod, "detect_mkcert", lambda: False)

    from muxplex.cli import setup_tls
    setup_tls(method="auto")

    captured = capsys.readouterr()
    assert "already configured" in captured.out.lower() or "regenerate" in captured.out.lower()


def test_setup_tls_regenerates_on_eof(tmp_path, monkeypatch, capsys):
    """setup_tls() does NOT regenerate when EOFError on prompt (non-interactive)."""
    import muxplex.settings as settings_mod
    import muxplex.tls as tls_mod

    fake_config = tmp_path / ".config" / "muxplex"
    fake_config.mkdir(parents=True)

    cert_file = fake_config / "cert.pem"
    key_file = fake_config / "key.pem"
    from muxplex.tls import generate_self_signed
    generate_self_signed(cert_path=cert_file, key_path=key_file)

    settings_file = fake_config / "settings.json"
    settings_file.write_text(json.dumps({
        "tls_cert": str(cert_file),
        "tls_key": str(key_file),
    }))
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(EOFError))
    monkeypatch.setattr(tls_mod, "detect_tailscale", lambda: None)
    monkeypatch.setattr(tls_mod, "detect_mkcert", lambda: False)

    from muxplex.cli import setup_tls
    # Should not crash
    setup_tls(method="auto")
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_cli.py::test_setup_tls_prompts_when_certs_exist -v
```

Expected: FAIL — `setup_tls()` doesn't check for existing certs.

**Step 3: Add existing cert check to top of `setup_tls()`**

At the top of the `setup_tls()` function, after defining `cert_path` and `key_path`, add:

```python
    # Check for existing TLS configuration
    from muxplex.settings import load_settings  # noqa: PLC0415
    from muxplex.tls import get_cert_info  # noqa: PLC0415

    existing_settings = load_settings()
    existing_cert = existing_settings.get("tls_cert", "")
    existing_key = existing_settings.get("tls_key", "")
    if existing_cert and existing_key and Path(existing_cert).is_file():
        info = get_cert_info(existing_cert)
        if info:
            expires = info["expires"][:10]  # YYYY-MM-DD
            print(f"\n  TLS already configured (expires {expires}).")
            try:
                answer = input("  Regenerate? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer not in ("y", "yes"):
                print("  Keeping existing certificates.")
                print()
                return
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_cli.py -v
```

Expected: ALL PASS.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "feat: setup-tls prompts before regenerating existing certs"
```

---

### Task 7: Full test sweep for Phase 2

**Files:**
- Modify: `muxplex/tests/test_tls.py` (add auto-detection integration tests)
- Modify: `muxplex/tests/test_cli.py` (add edge case tests)

**Step 1: Add mkcert Tailscale SAN integration test to `test_tls.py`**

Append to `muxplex/tests/test_tls.py`:

```python
def test_generate_mkcert_includes_tailscale_sans(tmp_path, monkeypatch):
    """generate_mkcert() includes Tailscale hostname/IP as extra SANs when provided."""
    import subprocess
    from muxplex.tls import generate_mkcert

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    gen_calls = []

    def fake_run(cmd, **kw):
        if "-cert-file" in cmd:
            gen_calls.append(list(cmd))
            cert_path.write_text("-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n")
            key_path.write_text("-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----\n")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = generate_mkcert(
        cert_path=cert_path,
        key_path=key_path,
        extra_hostnames=["spark-1.tail8f3c4e.ts.net", "100.64.0.1"],
    )

    assert result is not None
    # The mkcert command must include the Tailscale hostname
    assert any("spark-1.tail8f3c4e.ts.net" in str(c) for c in gen_calls)
```

**Step 2: Run full test suite**

```bash
cd muxplex && python -m pytest muxplex/tests/test_settings.py muxplex/tests/test_cli.py muxplex/tests/test_tls.py -v
```

Expected: ALL PASS.

**Step 3: Commit**

```bash
cd muxplex && git add muxplex/tests/test_tls.py muxplex/tests/test_cli.py && git commit -m "test: add Phase 2 integration and edge case tests"
```

---

### Task 8: README update with full TLS documentation

**Files:**
- Modify: `README.md`

**Step 1: Update the HTTPS / TLS feature section**

In `README.md`, update the `### HTTPS / TLS` section added in Phase 1 to remove "(Phase 2)" notes:

```markdown
### HTTPS / TLS

- `muxplex setup-tls` — auto-detect and set up TLS certificates
- **Tailscale** — real Let's Encrypt certs via `tailscale cert` (recommended)
- **mkcert** — locally-trusted certs, zero browser warnings
- **Self-signed** — fallback for immediate HTTPS (browser shows warning)
- Required for browser clipboard API on non-localhost
```

**Step 2: Update the HTTPS / TLS examples section**

In `README.md`, update the TLS examples section added in Phase 1:

```markdown
### HTTPS / TLS setup

```bash
# Auto-detect best TLS method (Tailscale → mkcert → self-signed)
muxplex setup-tls

# Force a specific method
muxplex setup-tls --method tailscale
muxplex setup-tls --method mkcert
muxplex setup-tls --method selfsigned

# Check current TLS status
muxplex setup-tls --status

# Override TLS cert for this run only
muxplex serve --tls-cert /path/cert.pem --tls-key /path/key.pem

# Check TLS status in doctor
muxplex doctor
```

**Detection priority:** If Tailscale is running with HTTPS Certificates enabled, `setup-tls` uses `tailscale cert` for real Let's Encrypt certificates (universally trusted, 90-day expiry). If mkcert is installed, it generates locally-trusted certificates. Otherwise, it falls back to self-signed.

**Tailscale cert renewal:** Tailscale certs expire in 90 days. Run `muxplex setup-tls` again to renew.
```

**Step 3: Update CLI reference**

In the CLI reference block, update the setup-tls line:

```
muxplex setup-tls [--method auto]   Set up TLS certs (Tailscale/mkcert/self-signed)
muxplex setup-tls --status          Show current TLS configuration
```

**Step 4: Commit**

```bash
cd muxplex && git add README.md && git commit -m "docs: update README with full TLS documentation (Tailscale, mkcert, auto-detect)"
```

---

## Phase 2 Checklist

After completing all 8 tasks, verify:

```bash
cd muxplex && python -m pytest muxplex/tests/ -v
```

All tests must pass. The following must work:

1. `muxplex setup-tls` — auto-detects Tailscale → mkcert → self-signed
2. `muxplex setup-tls --method tailscale` — uses Tailscale cert
3. `muxplex setup-tls --method mkcert` — uses mkcert
4. `muxplex setup-tls --method selfsigned` — uses self-signed
5. `muxplex setup-tls --status` — shows current TLS config
6. Re-running `muxplex setup-tls` prompts to regenerate existing certs
7. `muxplex doctor` — shows TLS enabled/disabled with cert expiry
8. `muxplex serve` — starts HTTPS when TLS configured, HTTP when not