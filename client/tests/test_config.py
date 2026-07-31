"""Pure tests for muxplex_client.config -- no network, no real filesystem paths.

Every test injects `home=`/`env=` explicitly so nothing here ever touches the
real `~/.config/muxplex` or the process's actual environment, per AGENTS.md's
"NEVER run the test suite on a host running a live muxplex" rule applied to
config resolution specifically.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from muxplex_client import config
from muxplex_client.errors import ConfigError

# ---------------------------------------------------------------------------
# server_url precedence + the https/http scheme rule
# ---------------------------------------------------------------------------


def test_server_url_default_is_http_without_ca(tmp_path: Path) -> None:
    cfg = config.resolve_config(env={}, home=tmp_path)
    assert cfg.server_url == "http://127.0.0.1:8088"
    assert cfg.sources["server_url"] == "default"


def test_server_url_default_is_https_when_ca_discovered(tmp_path: Path) -> None:
    ca_path = tmp_path / ".config" / "muxplex" / "ca" / config.CA_FILENAME
    ca_path.parent.mkdir(parents=True)
    ca_path.write_text("fake ca pem", encoding="utf-8")

    cfg = config.resolve_config(env={}, home=tmp_path)
    assert cfg.server_url == "https://127.0.0.1:8088"
    assert cfg.ca_file == ca_path


def test_server_url_argument_wins_even_with_ca_discovered(tmp_path: Path) -> None:
    ca_path = tmp_path / ".config" / "muxplex" / "ca" / config.CA_FILENAME
    ca_path.parent.mkdir(parents=True)
    ca_path.write_text("fake ca pem", encoding="utf-8")

    cfg = config.resolve_config(
        server_url="http://example.com:9000", env={}, home=tmp_path
    )
    assert cfg.server_url == "http://example.com:9000"
    assert cfg.sources["server_url"] == "argument"


def test_server_url_env_wins_over_default(tmp_path: Path) -> None:
    cfg = config.resolve_config(
        env={"MUXPLEX_URL": "https://remote:8443"}, home=tmp_path
    )
    assert cfg.server_url == "https://remote:8443"
    assert cfg.sources["server_url"] == "env:MUXPLEX_URL"


def test_server_url_argument_wins_over_env(tmp_path: Path) -> None:
    cfg = config.resolve_config(
        server_url="http://argument:1",
        env={"MUXPLEX_URL": "http://env:2"},
        home=tmp_path,
    )
    assert cfg.server_url == "http://argument:1"
    assert cfg.sources["server_url"] == "argument"


# ---------------------------------------------------------------------------
# federation_key precedence: argument > env > key_file discovery > default
# ---------------------------------------------------------------------------


def test_federation_key_default_none(tmp_path: Path) -> None:
    cfg = config.resolve_config(env={}, home=tmp_path)
    assert cfg.federation_key is None
    assert cfg.sources["federation_key"] == "default"


def test_federation_key_discovered_from_default_key_file(tmp_path: Path) -> None:
    key_path = tmp_path / ".config" / "muxplex" / config.KEY_FILENAME
    key_path.parent.mkdir(parents=True)
    key_path.write_text("  secret-key-value  \n", encoding="utf-8")

    cfg = config.resolve_config(env={}, home=tmp_path)
    assert cfg.federation_key == "secret-key-value"
    assert cfg.sources["federation_key"] == f"discovered:{key_path}"


def test_federation_key_env_wins_over_key_file_discovery(tmp_path: Path) -> None:
    key_path = tmp_path / ".config" / "muxplex" / config.KEY_FILENAME
    key_path.parent.mkdir(parents=True)
    key_path.write_text("from-file", encoding="utf-8")

    cfg = config.resolve_config(env={"MUXPLEX_KEY": "from-env"}, home=tmp_path)
    assert cfg.federation_key == "from-env"
    assert cfg.sources["federation_key"] == "env:MUXPLEX_KEY"


def test_federation_key_argument_wins_over_env(tmp_path: Path) -> None:
    cfg = config.resolve_config(
        federation_key="from-argument",
        env={"MUXPLEX_KEY": "from-env"},
        home=tmp_path,
    )
    assert cfg.federation_key == "from-argument"
    assert cfg.sources["federation_key"] == "argument"


def test_federation_key_explicit_key_file_argument_location(tmp_path: Path) -> None:
    custom_key_path = tmp_path / "somewhere-else" / "key.txt"
    custom_key_path.parent.mkdir(parents=True)
    custom_key_path.write_text("custom-location-key", encoding="utf-8")

    cfg = config.resolve_config(key_file=custom_key_path, env={}, home=tmp_path)
    assert cfg.federation_key == "custom-location-key"
    assert cfg.sources["federation_key"] == f"discovered:{custom_key_path}"


def test_federation_key_env_key_file_location(tmp_path: Path) -> None:
    custom_key_path = tmp_path / "env-location" / "key.txt"
    custom_key_path.parent.mkdir(parents=True)
    custom_key_path.write_text("env-location-key", encoding="utf-8")

    cfg = config.resolve_config(
        env={"MUXPLEX_FEDERATION_KEY_FILE": str(custom_key_path)}, home=tmp_path
    )
    assert cfg.federation_key == "env-location-key"


def test_federation_key_no_key_file_present_is_default(tmp_path: Path) -> None:
    """A key_file location that resolves but doesn't exist on disk is default,
    not an error -- discovery is optional, not required."""
    cfg = config.resolve_config(env={}, home=tmp_path)
    assert cfg.federation_key is None
    assert cfg.sources["federation_key"] == "default"


# ---------------------------------------------------------------------------
# ca_file precedence: argument > env > disk discovery > default
# ---------------------------------------------------------------------------


def test_ca_file_default_none(tmp_path: Path) -> None:
    cfg = config.resolve_config(env={}, home=tmp_path)
    assert cfg.ca_file is None
    assert cfg.sources["ca_file"] == "default"


def test_ca_file_discovered_from_default_location(tmp_path: Path) -> None:
    ca_path = tmp_path / ".config" / "muxplex" / "ca" / config.CA_FILENAME
    ca_path.parent.mkdir(parents=True)
    ca_path.write_text("pem", encoding="utf-8")

    cfg = config.resolve_config(env={}, home=tmp_path)
    assert cfg.ca_file == ca_path
    assert cfg.sources["ca_file"] == f"discovered:{ca_path}"


def test_ca_file_env_wins_over_discovery(tmp_path: Path) -> None:
    discovered = tmp_path / ".config" / "muxplex" / "ca" / config.CA_FILENAME
    discovered.parent.mkdir(parents=True)
    discovered.write_text("pem", encoding="utf-8")

    override = tmp_path / "override-ca.crt"
    cfg = config.resolve_config(env={"MUXPLEX_CA_FILE": str(override)}, home=tmp_path)
    assert cfg.ca_file == override
    assert cfg.sources["ca_file"] == "env:MUXPLEX_CA_FILE"


def test_ca_file_argument_wins_over_env(tmp_path: Path) -> None:
    cfg = config.resolve_config(
        ca_file=tmp_path / "arg-ca.crt",
        env={"MUXPLEX_CA_FILE": str(tmp_path / "env-ca.crt")},
        home=tmp_path,
    )
    assert cfg.ca_file == tmp_path / "arg-ca.crt"
    assert cfg.sources["ca_file"] == "argument"


# ---------------------------------------------------------------------------
# timeout precedence + invalid MUXPLEX_TIMEOUT
# ---------------------------------------------------------------------------


def test_timeout_default(tmp_path: Path) -> None:
    cfg = config.resolve_config(env={}, home=tmp_path)
    assert cfg.timeout == 5.0
    assert cfg.sources["timeout"] == "default"


def test_timeout_env(tmp_path: Path) -> None:
    cfg = config.resolve_config(env={"MUXPLEX_TIMEOUT": "12.5"}, home=tmp_path)
    assert cfg.timeout == 12.5
    assert cfg.sources["timeout"] == "env:MUXPLEX_TIMEOUT"


def test_timeout_argument_wins_over_env(tmp_path: Path) -> None:
    cfg = config.resolve_config(
        timeout=1.0, env={"MUXPLEX_TIMEOUT": "99"}, home=tmp_path
    )
    assert cfg.timeout == 1.0
    assert cfg.sources["timeout"] == "argument"


def test_timeout_invalid_env_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        config.resolve_config(env={"MUXPLEX_TIMEOUT": "not-a-number"}, home=tmp_path)


# ---------------------------------------------------------------------------
# load_key_file
# ---------------------------------------------------------------------------


def test_load_key_file_strips_whitespace(tmp_path: Path) -> None:
    path = tmp_path / "federation_key"
    path.write_text("  abc123  \n", encoding="utf-8")
    assert config.load_key_file(path) == "abc123"


def test_load_key_file_missing_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        config.load_key_file(tmp_path / "does-not-exist")


def test_load_key_file_empty_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "federation_key"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError):
        config.load_key_file(path)


def test_load_key_file_whitespace_only_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "federation_key"
    path.write_text("   \n\t  ", encoding="utf-8")
    with pytest.raises(ConfigError):
        config.load_key_file(path)


def test_load_key_file_unreadable_raises_config_error(tmp_path: Path) -> None:
    """A path that exists but can't be read as a directory raises ConfigError."""
    directory = tmp_path / "not-a-file"
    directory.mkdir()
    with pytest.raises(ConfigError):
        config.load_key_file(directory)


# ---------------------------------------------------------------------------
# looks_like_leaf_certificate
# ---------------------------------------------------------------------------


def test_looks_like_leaf_certificate_true_when_sibling_ca_exists(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".config" / "muxplex"
    (config_dir / "ca").mkdir(parents=True)
    (config_dir / "ca" / config.CA_FILENAME).write_text("ca pem", encoding="utf-8")
    leaf_path = config_dir / config.LEAF_FILENAME
    leaf_path.write_text("leaf pem", encoding="utf-8")

    assert config.looks_like_leaf_certificate(leaf_path) is True


def test_looks_like_leaf_certificate_false_wrong_filename(tmp_path: Path) -> None:
    config_dir = tmp_path / ".config" / "muxplex"
    (config_dir / "ca").mkdir(parents=True)
    (config_dir / "ca" / config.CA_FILENAME).write_text("ca pem", encoding="utf-8")
    other_path = config_dir / "some-other-name.crt"
    other_path.write_text("pem", encoding="utf-8")

    assert config.looks_like_leaf_certificate(other_path) is False


def test_looks_like_leaf_certificate_false_no_sibling_ca(tmp_path: Path) -> None:
    config_dir = tmp_path / ".config" / "muxplex"
    config_dir.mkdir(parents=True)
    leaf_path = config_dir / config.LEAF_FILENAME
    leaf_path.write_text("leaf pem", encoding="utf-8")

    assert config.looks_like_leaf_certificate(leaf_path) is False


# ---------------------------------------------------------------------------
# ca_remediation_hint
# ---------------------------------------------------------------------------


def test_ca_remediation_hint_names_correct_path_for_leaf(tmp_path: Path) -> None:
    config_dir = tmp_path / ".config" / "muxplex"
    (config_dir / "ca").mkdir(parents=True)
    (config_dir / "ca" / config.CA_FILENAME).write_text("ca pem", encoding="utf-8")
    leaf_path = config_dir / config.LEAF_FILENAME
    leaf_path.write_text("leaf pem", encoding="utf-8")

    hint = config.ca_remediation_hint(leaf_path, home=tmp_path)
    assert hint is not None
    assert str(config_dir / "ca" / config.CA_FILENAME) in hint


def test_ca_remediation_hint_none_ca_file_but_ca_exists_on_disk(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".config" / "muxplex"
    (config_dir / "ca").mkdir(parents=True)
    ca_path = config_dir / "ca" / config.CA_FILENAME
    ca_path.write_text("ca pem", encoding="utf-8")

    hint = config.ca_remediation_hint(None, home=tmp_path)
    assert hint is not None
    assert str(ca_path) in hint


def test_ca_remediation_hint_none_when_nothing_useful_to_say(tmp_path: Path) -> None:
    assert config.ca_remediation_hint(None, home=tmp_path) is None


def test_ca_remediation_hint_none_for_non_leaf_ca_file(tmp_path: Path) -> None:
    other_path = tmp_path / "some-other-ca.crt"
    other_path.write_text("pem", encoding="utf-8")
    assert config.ca_remediation_hint(other_path, home=tmp_path) is None
