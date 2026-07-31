"""Client configuration resolution: argument > env > discovered > default.

Pure logic + filesystem reads only -- no HTTP, no imports from
`sync_client`/`async_client`, matching the `_protocol.py` precedent where
all testable logic is I/O-free. Every function here accepts `env=`/`home=`
injection points so callers (and tests) never have to touch the real
process environment or `~/.config/muxplex`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError

DEFAULT_PORT: int = 8088
DEFAULT_HOST: str = "127.0.0.1"
CONFIG_DIR: Path = Path.home() / ".config" / "muxplex"
CA_FILENAME: str = "muxplex-ca.crt"  # under CONFIG_DIR/ca/
LEAF_FILENAME: str = "muxplex.crt"  # the footgun
KEY_FILENAME: str = "federation_key"

DEFAULT_TIMEOUT: float = 5.0

__all__ = [
    "CA_FILENAME",
    "CONFIG_DIR",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_TIMEOUT",
    "KEY_FILENAME",
    "LEAF_FILENAME",
    "ClientConfig",
    "ca_remediation_hint",
    "load_key_file",
    "looks_like_leaf_certificate",
    "resolve_config",
]


@dataclass(frozen=True)
class ClientConfig:
    """The fully-resolved configuration for a muxplex client instance.

    `sources` maps each other field's name to where its value came from --
    `"argument"`, `"env:<VAR>"`, `"discovered:<path>"`, or `"default"` --
    printed by `muxplex-client info --verbose` (a future CLI consumer) so a
    confusing connection failure is a 30-second read of `sources`, not a
    30-minute debugging session re-deriving where a value came from.
    """

    server_url: str
    federation_key: str | None
    ca_file: Path | None
    timeout: float
    sources: Mapping[str, str]


def _config_dir(home: Path | None) -> Path:
    """Resolve the muxplex config directory for *home* (or the real HOME).

    Never reads `CONFIG_DIR` (fixed at import time to the real `Path.home()`)
    so every caller that supplies `home=` stays hermetic.
    """
    return (home if home is not None else Path.home()) / ".config" / "muxplex"


def resolve_config(
    *,
    server_url: str | None = None,
    federation_key: str | None = None,
    key_file: Path | str | None = None,
    ca_file: Path | str | None = None,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> ClientConfig:
    """Resolve a `ClientConfig` from explicit values, the environment, and disk.

    Precedence for every field: explicit argument > environment variable >
    discovered on disk > built-in default.

    | Field | Env var | Discovery | Default |
    |---|---|---|---|
    | `server_url` | `MUXPLEX_URL` | -- | scheme rule below |
    | `federation_key` | `MUXPLEX_KEY` (literal key) | read `key_file` if it exists | `None` |
    | `key_file` | `MUXPLEX_FEDERATION_KEY_FILE` | -- | `CONFIG_DIR/federation_key` |
    | `ca_file` | `MUXPLEX_CA_FILE` | `CONFIG_DIR/ca/muxplex-ca.crt` if it exists | `None` |
    | `timeout` | `MUXPLEX_TIMEOUT` | -- | `5.0` |

    Scheme rule for the default `server_url` (only applies when neither
    argument nor env supplied for it): if a CA file resolved to a real path
    (by any means -- argument, env, or disk discovery), the default is
    `https://127.0.0.1:8088`; otherwise `http://127.0.0.1:8088`. A muxplex
    with TLS set up has that CA file; one without it does not.

    `MUXPLEX_KEY` wins over reading `key_file` -- an explicitly exported key
    is a stronger signal than a file that merely happens to exist. `key_file`
    itself (the location, not the credential) is not part of the returned
    config; it only matters for the discovery tier of `federation_key`.

    `env` defaults to `os.environ`; `home` defaults to `Path.home()`. Tests
    should always pass both explicitly so nothing here ever touches the
    real environment or `~/.config/muxplex`.
    """
    resolved_env: Mapping[str, str] = os.environ if env is None else env
    config_dir = _config_dir(home)
    sources: dict[str, str] = {}

    # -- ca_file --
    resolved_ca_file: Path | None
    if ca_file is not None:
        resolved_ca_file = Path(ca_file)
        sources["ca_file"] = "argument"
    elif resolved_env.get("MUXPLEX_CA_FILE"):
        resolved_ca_file = Path(resolved_env["MUXPLEX_CA_FILE"])
        sources["ca_file"] = "env:MUXPLEX_CA_FILE"
    else:
        discovered_ca = config_dir / "ca" / CA_FILENAME
        if discovered_ca.exists():
            resolved_ca_file = discovered_ca
            sources["ca_file"] = f"discovered:{discovered_ca}"
        else:
            resolved_ca_file = None
            sources["ca_file"] = "default"

    # -- server_url --
    resolved_server_url: str
    if server_url is not None:
        resolved_server_url = server_url
        sources["server_url"] = "argument"
    elif resolved_env.get("MUXPLEX_URL"):
        resolved_server_url = resolved_env["MUXPLEX_URL"]
        sources["server_url"] = "env:MUXPLEX_URL"
    else:
        scheme = "https" if resolved_ca_file is not None else "http"
        resolved_server_url = f"{scheme}://{DEFAULT_HOST}:{DEFAULT_PORT}"
        sources["server_url"] = "default"

    # -- key_file location (not itself a ClientConfig field; only feeds the
    #    federation_key discovery tier below) --
    resolved_key_file: Path
    if key_file is not None:
        resolved_key_file = Path(key_file)
    elif resolved_env.get("MUXPLEX_FEDERATION_KEY_FILE"):
        resolved_key_file = Path(resolved_env["MUXPLEX_FEDERATION_KEY_FILE"])
    else:
        resolved_key_file = config_dir / KEY_FILENAME

    # -- federation_key --
    resolved_federation_key: str | None
    if federation_key is not None:
        resolved_federation_key = federation_key
        sources["federation_key"] = "argument"
    elif resolved_env.get("MUXPLEX_KEY"):
        resolved_federation_key = resolved_env["MUXPLEX_KEY"]
        sources["federation_key"] = "env:MUXPLEX_KEY"
    elif resolved_key_file.exists():
        resolved_federation_key = load_key_file(resolved_key_file)
        sources["federation_key"] = f"discovered:{resolved_key_file}"
    else:
        resolved_federation_key = None
        sources["federation_key"] = "default"

    # -- timeout --
    resolved_timeout: float
    if timeout is not None:
        resolved_timeout = timeout
        sources["timeout"] = "argument"
    elif resolved_env.get("MUXPLEX_TIMEOUT"):
        raw_timeout = resolved_env["MUXPLEX_TIMEOUT"]
        try:
            resolved_timeout = float(raw_timeout)
        except ValueError as exc:
            raise ConfigError(
                f"MUXPLEX_TIMEOUT={raw_timeout!r} is not a valid number of "
                "seconds; unset it or export a plain float like '5' or '7.5'."
            ) from exc
        sources["timeout"] = "env:MUXPLEX_TIMEOUT"
    else:
        resolved_timeout = DEFAULT_TIMEOUT
        sources["timeout"] = "default"

    return ClientConfig(
        server_url=resolved_server_url,
        federation_key=resolved_federation_key,
        ca_file=resolved_ca_file,
        timeout=resolved_timeout,
        sources=sources,
    )


def load_key_file(path: Path) -> str:
    """Read and strip a federation key from *path*.

    Raises `ConfigError` with an actionable message when the file is
    missing, empty, or unreadable, rather than letting a bare
    `OSError`/`FileNotFoundError` propagate to a caller that only expects
    `MuxplexError` subclasses.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"federation key file not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"could not read federation key file {path}: {exc}") from exc
    key = text.strip()
    if not key:
        raise ConfigError(f"federation key file is empty: {path}")
    return key


def looks_like_leaf_certificate(path: Path) -> bool:
    """Heuristically detect the muxplex.crt-instead-of-the-CA footgun.

    A filename+layout heuristic, not a certificate parser -- this project
    has no `cryptography` dependency and must not grow one for the client,
    and `ssl` exposes no public certificate-file parser. Returns `True`
    only when *path*'s name is exactly `LEAF_FILENAME` ("muxplex.crt") AND
    a sibling `ca/muxplex-ca.crt` exists next to it: exactly the
    documented footgun (API_SEMANTICS.md's "GET /api/ca" section) and
    nothing else.
    """
    path = Path(path)
    if path.name != LEAF_FILENAME:
        return False
    sibling_ca = path.parent / "ca" / CA_FILENAME
    return sibling_ca.exists()


def ca_remediation_hint(ca_file: Path | None, home: Path | None = None) -> str | None:
    """Return a ready-to-print remediation sentence for a TLS trust failure.

    Returns `None` when there is nothing more useful to say than the raw
    verification error. Two cases produce a hint:

    - *ca_file* looks like the leaf certificate (`looks_like_leaf_certificate`)
      -- names the correct CA path instead.
    - *ca_file* is `None` (system trust store was used) but a local CA
      certificate exists on disk at the default location -- suggests
      passing it explicitly.
    """
    config_dir = _config_dir(home)
    correct_path = config_dir / "ca" / CA_FILENAME
    if ca_file is not None and looks_like_leaf_certificate(Path(ca_file)):
        return (
            f"{ca_file} looks like the server's leaf certificate, not the "
            f"CA. Point --ca (or MUXPLEX_CA_FILE) at {correct_path} instead."
        )
    if ca_file is None and correct_path.exists():
        return (
            f"A local CA certificate was found at {correct_path}; pass it "
            "with --ca or MUXPLEX_CA_FILE."
        )
    return None
