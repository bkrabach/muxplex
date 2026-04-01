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
