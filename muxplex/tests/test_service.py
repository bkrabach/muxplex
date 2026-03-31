"""Tests for muxplex/service.py — system service management module."""

import sys


def test_service_module_importable():
    """All 7 public service functions must be importable from muxplex.service."""
    from muxplex.service import (  # noqa: F401
        service_install,
        service_logs,
        service_restart,
        service_start,
        service_status,
        service_stop,
        service_uninstall,
    )


def test_is_darwin_detection(monkeypatch):
    """_is_darwin() must return True when sys.platform=='darwin', False for 'linux'."""
    from muxplex.service import _is_darwin

    monkeypatch.setattr(sys, "platform", "darwin")
    assert _is_darwin() is True

    monkeypatch.setattr(sys, "platform", "linux")
    assert _is_darwin() is False


def test_resolve_muxplex_bin():
    """_resolve_muxplex_bin() must return a string containing 'muxplex' or 'python'."""
    from muxplex.service import _resolve_muxplex_bin

    result = _resolve_muxplex_bin()
    assert isinstance(result, str)
    assert "muxplex" in result or "python" in result
