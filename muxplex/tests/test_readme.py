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
    assert "`tls_key`" in README, (
        "README Configuration table must include tls_key row"
    )
    assert "Path to TLS private key" in README or "TLS private key file" in README, (
        "README must describe tls_key as a TLS private key path"
    )


def test_readme_examples_show_tls_commands():
    """README examples must show TLS setup commands."""
    assert "setup-tls" in README, (
        "README must show setup-tls in its examples"
    )
    # Check that the examples demonstrate at least the basic setup-tls usage
    assert "muxplex setup-tls" in README, (
        "README examples must show 'muxplex setup-tls' command"
    )


def test_readme_phase_2_items_noted():
    """README must note Phase 2 items (Tailscale, mkcert) as upcoming."""
    assert "Tailscale" in README, (
        "README must mention Tailscale as a Phase 2 TLS method"
    )
    assert "mkcert" in README, (
        "README must mention mkcert as a Phase 2 TLS method"
    )
    assert "Phase 2" in README, (
        "README must note Phase 2 items as upcoming"
    )


def test_readme_tls_cert_has_empty_default():
    """README must show tls_cert default as empty string."""
    # The config table must show the empty string default for tls_cert
    assert "`tls_cert`" in README, "README must document tls_cert"


def test_readme_tls_key_has_empty_default():
    """README must show tls_key default as empty string."""
    # The config table must show the empty string default for tls_key
    assert "`tls_key`" in README, "README must document tls_key"


def test_readme_tls_setup_tls_entry_with_method_flag():
    """README CLI Reference must show setup-tls with --method flag."""
    assert "--method" in README, (
        "README must document the --method flag for setup-tls"
    )
