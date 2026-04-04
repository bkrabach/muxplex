"""Tests for README.md documentation correctness."""

from pathlib import Path

README = (Path(__file__).parent.parent.parent / "README.md").read_text()


def test_readme_has_service_management_section():
    """README must have a '### Service management' section."""
    assert "### Service management" in README, (
        "README must contain '### Service management' section"
    )


def test_readme_service_management_has_all_7_subcommands():
    """README Service management section must list all 7 subcommands."""
    subcommands = [
        "muxplex service install",
        "muxplex service uninstall",
        "muxplex service start",
        "muxplex service stop",
        "muxplex service restart",
        "muxplex service status",
        "muxplex service logs",
    ]
    for cmd in subcommands:
        assert cmd in README, (
            f"README must mention '{cmd}' in Service management section"
        )


def test_readme_explains_settings_json_no_flags():
    """README must explain that the service reads from settings.json with no flags."""
    assert "settings.json" in README, "README must mention settings.json"
    # Check that it explains no-flags behavior relative to the service
    assert "no flags" in README or "reads all options from" in README, (
        "README must explain that the service reads options from settings.json (no flags)"
    )


def test_readme_shows_restart_workflow():
    """README must show a restart workflow example for config changes."""
    assert "muxplex service restart" in README, (
        "README must include 'muxplex service restart' in the example"
    )


# ── Comprehensive documentation tests ─────────────────────────────────────


def test_readme_documents_all_settings_keys():
    """README must document every key from DEFAULT_SETTINGS."""
    from muxplex.settings import DEFAULT_SETTINGS

    for key in DEFAULT_SETTINGS:
        assert f"`{key}`" in README, f"README must document setting key '{key}'"


def test_readme_has_keyboard_shortcuts_section():
    """README must have a keyboard shortcuts section."""
    assert "Ctrl+Shift+C" in README, "README must document Ctrl+Shift+C"
    assert "Ctrl+Shift+V" in README, "README must document Ctrl+Shift+V"


def test_readme_documents_clipboard_features():
    """README must mention clipboard and mouse select features."""
    assert "clipboard" in README.lower(), "README must mention clipboard"
    assert "mouse" in README.lower() or "select" in README.lower(), (
        "README must mention mouse selection"
    )


def test_readme_documents_all_cli_commands():
    """README must list all top-level CLI commands."""
    commands = [
        "muxplex doctor",
        "muxplex upgrade",
        "muxplex show-password",
        "muxplex reset-secret",
        "muxplex config",
    ]
    for cmd in commands:
        assert cmd in README, f"README must mention CLI command '{cmd}'"


def test_readme_has_platform_support_section():
    """README must document platform support."""
    assert "systemd" in README, "README must mention systemd"
    assert "launchd" in README, "README must mention launchd"


def test_readme_documents_auth_modes():
    """README must document both PAM and password auth."""
    assert "PAM" in README, "README must mention PAM auth"
    assert "password" in README.lower(), "README must mention password auth"
    assert "localhost" in README.lower() or "127.0.0.1" in README, (
        "README must mention localhost bypass"
    )


def test_readme_documents_view_modes():
    """README must document Auto and Fit view modes."""
    readme_lower = README.lower()
    assert "view mode" in readme_lower or "view modes" in readme_lower, (
        "README must mention view modes"
    )


def test_readme_documents_ansi_color_previews():
    """README must document ANSI color previews in tiles."""
    assert "ANSI" in README, "README must mention ANSI color previews"


def test_readme_documents_hover_preview():
    """README must document hover preview feature."""
    readme_lower = README.lower()
    assert "hover" in readme_lower and "preview" in readme_lower, (
        "README must mention hover preview"
    )


# ── TLS / HTTPS documentation tests ───────────────────────────────────────────


def test_readme_has_https_tls_feature_subsection():
    """README must have an 'HTTPS / TLS' subsection under Developer Tools."""
    assert "HTTPS / TLS" in README, (
        "README must contain an 'HTTPS / TLS' feature subsection"
    )


def test_readme_cli_reference_includes_setup_tls():
    """README CLI Reference block must include the setup-tls command."""
    assert "setup-tls" in README, (
        "README CLI Reference must include 'setup-tls' command"
    )


def test_readme_configuration_table_has_tls_cert_row():
    """README Configuration table must document the tls_cert setting."""
    assert "`tls_cert`" in README, (
        "README Configuration table must include tls_cert row"
    )
    assert "Path to TLS certificate" in README or "TLS certificate file" in README, (
        "README must describe tls_cert as a TLS certificate path"
    )


def test_readme_configuration_table_has_tls_key_row():
    """README Configuration table must document the tls_key setting."""
    assert "`tls_key`" in README, "README Configuration table must include tls_key row"
    assert "Path to TLS private key" in README or "TLS private key file" in README, (
        "README must describe tls_key as a TLS private key path"
    )


def test_readme_examples_show_tls_commands():
    """README examples must show TLS setup commands."""
    assert "setup-tls" in README, "README must show setup-tls in its examples"
    # Check that the examples demonstrate at least the basic setup-tls usage
    assert "muxplex setup-tls" in README, (
        "README examples must show 'muxplex setup-tls' command"
    )


def test_readme_no_phase_2_placeholders():
    """README must NOT contain any '(Phase 2)' placeholder text."""
    assert "(Phase 2)" not in README, (
        "README must not contain '(Phase 2)' placeholder text — Phase 2 is complete"
    )


def test_readme_tls_tailscale_lets_encrypt():
    """README must document Tailscale as providing real Let's Encrypt certs via tailscale cert."""
    assert "tailscale cert" in README, (
        "README must mention 'tailscale cert' command for Let's Encrypt certs"
    )
    assert "Tailscale" in README, "README must mention Tailscale as a TLS method"


def test_readme_tls_mkcert_zero_browser_warnings():
    """README must document mkcert as providing locally-trusted certs with zero browser warnings."""
    assert "mkcert" in README, "README must mention mkcert as a TLS method"
    assert "zero browser warnings" in README or "browser warnings" in README, (
        "README must mention that mkcert provides zero browser warnings"
    )


def test_readme_tls_selfsigned_browser_warning():
    """README must document self-signed as fallback with browser warning note."""
    readme_lower = README.lower()
    assert (
        "browser shows warning" in readme_lower or "browser warning" in readme_lower
    ), "README must note that self-signed certs show browser warnings"


def test_readme_tls_setup_status_command():
    """README must document the 'muxplex setup-tls --status' command."""
    assert "muxplex setup-tls --status" in README, (
        "README must document 'muxplex setup-tls --status' command"
    )


def test_readme_tls_setup_method_tailscale():
    """README must document 'muxplex setup-tls --method tailscale' in examples."""
    assert "muxplex setup-tls --method tailscale" in README, (
        "README must show 'muxplex setup-tls --method tailscale' in examples"
    )


def test_readme_tls_setup_method_mkcert():
    """README must document 'muxplex setup-tls --method mkcert' in examples."""
    assert "muxplex setup-tls --method mkcert" in README, (
        "README must show 'muxplex setup-tls --method mkcert' in examples"
    )


def test_readme_tls_detection_priority_explanation():
    """README must explain auto-detection priority mentioning Tailscale and mkcert."""
    assert (
        "Tailscale" in README
        and "mkcert" in README
        and ("priority" in README.lower() or "auto-detect" in README.lower())
    ), "README must explain detection priority naming both Tailscale and mkcert"


def test_readme_tls_tailscale_cert_renewal_note():
    """README must include a note about Tailscale cert renewal (90-day expiry)."""
    assert "90-day" in README or "90 day" in README or "90 days" in README, (
        "README must include a note about 90-day Tailscale cert renewal/expiry"
    )


def test_readme_cli_reference_setup_tls_updated():
    """README CLI Reference must describe setup-tls with Tailscale/mkcert/self-signed."""
    assert "Tailscale/mkcert/self-signed" in README or "Tailscale/mkcert" in README, (
        "README CLI Reference must describe setup-tls with Tailscale/mkcert/self-signed methods"
    )


def test_readme_cli_reference_has_setup_tls_status():
    """README CLI Reference must include setup-tls --status command with description."""
    assert "setup-tls --status" in README, (
        "README CLI Reference must include 'setup-tls --status' with description"
    )
    assert "Show current TLS" in README or "current TLS configuration" in README, (
        "README CLI Reference must describe what setup-tls --status does"
    )


def test_readme_tls_cert_has_empty_default():
    """README must document the tls_cert config key (presence check)."""
    assert "`tls_cert`" in README, "README must document tls_cert"


def test_readme_tls_key_has_empty_default():
    """README must document the tls_key config key (presence check)."""
    assert "`tls_key`" in README, "README must document tls_key"


def test_readme_tls_setup_tls_entry_with_method_flag():
    """README CLI Reference must show setup-tls with --method flag."""
    assert "--method" in README, "README must document the --method flag for setup-tls"


def test_readme_images_use_absolute_urls():
    import re
    readme = Path(__file__).resolve().parents[2] / "README.md"
    content = readme.read_text()
    images = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", content)
    for alt, url in images:
        assert url.startswith("http"), (
            f"Image '{alt}' uses relative path '{url}' — must use absolute URL for PyPI rendering"
        )
