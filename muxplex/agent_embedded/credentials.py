# pyright: reportMissingImports=false
"""Legacy credentials adapted to the optional public Agent SDK.

True operator environment wins over the v1/legacy credentials file. Construction
applies a STABLE service-wide fallback with provenance: never swap/restore env
around an await. Saving changes only the file; a fresh agent captures it next turn.
Validation uses explicit candidates against fixed official provider HTTP APIs.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from amplifier_agent import Agent, AgentOptions


# Mirror the HTTP key-only allowlist, without importing the FastAPI app.
ALLOWED_PROVIDERS: frozenset[str] = frozenset({"anthropic", "openai"})
_ENV_VARS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
_credential_root: Path | None = None
_file_environment: dict[str, str] = {}
_credential_lock = threading.RLock()
_construction_lock = asyncio.Lock()


def credential_home() -> Path:
    """Cache the legacy root BEFORE stably removing the retired SDK env key.

    The new SDK rejects AMPLIFIER_AGENT_HOME as obsolete host configuration.
    Never change HOME/AMPLIFIER_HOME; the runner supplies explicit SDK storage.
    Subsequent changes to the legacy override cannot redirect the cached store.
    """
    global _credential_root
    with _credential_lock:
        legacy = os.environ.pop("AMPLIFIER_AGENT_HOME", None)
        if _credential_root is None:
            _credential_root = (
                Path(legacy).expanduser()
                if legacy
                else Path.home() / ".amplifier-agent"
            )
        return _credential_root


def _load_credentials() -> dict[str, Any]:
    """Read without modifying; malformed/unsupported envelopes fail loudly.

    Upgrade the old flat provider -> key (or entry) mapping in memory only.
    Preserve unrelated providers/fields. Error text never contains file contents.
    """
    path = credential_home() / "credentials.json"
    if path.is_symlink() and not path.exists():
        raise ValueError("Agent credentials point to a missing file; repair the link.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "providers": {}}
    except (ValueError, OSError) as exc:
        raise ValueError(
            "Cannot read agent credentials; repair the file before retrying."
        ) from exc
    if not isinstance(data, dict):
        raise ValueError("Agent credentials must be a JSON object.")
    if "providers" not in data:
        if "version" in data:
            raise ValueError("Agent credential envelope is missing providers.")
        data = {
            "version": 1,
            "providers": {
                name: entry if isinstance(entry, dict) else {"api_key": entry}
                for name, entry in data.items()
            },
        }
    if type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("Unsupported agent credential version; expected version 1.")
    if not isinstance(data["providers"], dict):
        raise ValueError("Agent credentials providers must be an object.")
    for entry in data["providers"].values():
        if not isinstance(entry, dict) or (
            "api_key" in entry and not isinstance(entry["api_key"], str)
        ):
            raise ValueError("Agent credential provider entry has invalid shape.")
    return data


def _operator_key(provider: str) -> str:
    """Distinguish operator env from the adapter's own persistent injection."""
    name = _ENV_VARS.get(provider)
    if name is None:
        return ""
    value = os.environ.get(name, "")
    injected = _file_environment.get(name)
    if injected is not None and value == injected:
        return ""
    # Changed/removed variables belong to the operator, not to this adapter.
    _file_environment.pop(name, None)
    return value


def _resolve_credential(provider: str) -> tuple[str, str]:
    with _credential_lock:
        credential_home()
        value = _operator_key(provider)
        if value:
            return value, "env"
        data = _load_credentials()
        value = data["providers"].get(provider, {}).get("api_key", "")
        return (value, "file") if value else ("", "not_set")


async def create_agent_with_credentials(options: AgentOptions) -> Agent:
    """Construct a fresh public Agent with stable, provenance-aware fallback.

    Serialize constructors across SDK capture. Sync saves serialize file IO but
    NEVER change environment during awaited construction. Existing agents keep
    engine-owned connection snapshots; new ones re-read the saved file.
    """
    async with _construction_lock:
        with _credential_lock:
            # Validate the whole envelope before applying any fallback.
            data = _load_credentials()
            for provider, name in _ENV_VARS.items():
                if _operator_key(provider):
                    continue
                value = data["providers"].get(provider, {}).get("api_key", "")
                if value:
                    os.environ[name] = value
                    _file_environment[name] = value
                elif name in _file_environment:
                    os.environ.pop(name, None)
                    _file_environment.pop(name, None)
        from amplifier_agent import create_agent

        return await create_agent(options)


def _mask(value: str) -> str:
    if not value:
        return "<not set>"
    if len(value) <= 12:
        return "***"
    return f"{value[:6]}...{value[-4:]}"


def resolve_status(provider: str) -> dict[str, Any]:
    """Sync status: injected fallback retains file provenance, not env shadow."""
    value, source = _resolve_credential(provider)
    return {
        "source": source,
        "masked": _mask(value) if value else None,
        "env_var": _ENV_VARS.get(provider),
    }


def persist_key(provider: str, api_key: str) -> Path:
    """Preserve the legacy store with atomic, private writes after validation.

    Read errors never overwrite existing bytes. Do not mutate fallback env here:
    a save during awaited construction must not change credentials being captured.
    """
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError("Unsupported key-only provider.")
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("A nonempty provider key is required.")
    with _credential_lock:
        data = _load_credentials()
        data["providers"].setdefault(provider, {})["api_key"] = api_key
        path = credential_home() / "credentials.json"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        fd, name = tempfile.mkstemp(prefix=".credentials-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
        return path


async def _enumerate_models(
    provider: str, api_key: str, *, timeout_seconds: float = 15.0
) -> tuple[str, str, list[Any] | None]:
    """Bounded official HTTP enumeration with an explicit candidate.

    Never echo response bodies/exception text (they can repeat keys). Ignore
    ambient endpoints/proxies; no redirects, env/file changes or private imports.
    Non-ok results carry None, never an empty list. Partial lists are not proof.
    """
    if provider == "anthropic":
        url = "https://api.anthropic.com/v1/models"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        params: dict[str, str | int] = {"limit": 1000}
    elif provider == "openai":
        url = "https://api.openai.com/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        params = {}
    else:
        return "error", "Unsupported key-only provider.", None

    async def fetch() -> list[Any]:
        models: list[Any] = []
        async with httpx.AsyncClient(
            timeout=timeout_seconds, follow_redirects=False, trust_env=False
        ) as client:
            for _page in range(20):
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                    raise ValueError("Unreadable model list")
                entries = body["data"]
                if _model_ids(entries) is None:
                    raise ValueError("Unreadable model ids")
                models.extend(entries)
                if provider != "anthropic":
                    return models
                if type(body.get("has_more")) is not bool:
                    raise ValueError("Missing model pagination metadata")
                if body["has_more"] is False:
                    return models
                cursor = body.get("last_id")
                if (
                    body.get("has_more") is not True
                    or not isinstance(cursor, str)
                    or not cursor
                    or params.get("after_id") == cursor
                ):
                    raise ValueError("Unreadable model pagination")
                params["after_id"] = cursor
            raise ValueError("Model list exceeds bounded pagination")

    try:
        models = await asyncio.wait_for(fetch(), timeout=timeout_seconds)
    except (TimeoutError, httpx.TimeoutException):
        return (
            "unreachable",
            f"Provider model-list request timed out after {timeout_seconds}s.",
            None,
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            return (
                "bad_key",
                f"Provider rejected the credential (HTTP {status}; invalid or unauthorized key).",
                None,
            )
        return (
            "unreachable",
            f"Provider model-list request returned HTTP {status}.",
            None,
        )
    except httpx.RequestError:
        return "unreachable", "Provider model-list connection failed.", None
    except (ValueError, TypeError, KeyError):
        return (
            "error",
            "Provider returned an unreadable or incomplete model list.",
            None,
        )
    return "ok", f"{len(models)} model(s) returned", models


async def validate_key(
    provider: str, api_key: str, *, timeout_seconds: float = 15.0
) -> tuple[str, str]:
    """Validate independently of persistence or shared SDK construction."""
    verdict, detail, _models = await _enumerate_models(
        provider, api_key, timeout_seconds=timeout_seconds
    )
    return verdict, detail


SERVED_MODELS_TTL_SECONDS: float = 300.0
# Success-only, credential-fingerprinted cache: never keys or partial lists.
_served_models_cache: dict[str, tuple[str, float, tuple[str, ...]]] = {}
_served_models_lock = asyncio.Lock()


def _credential_fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def clear_served_models_cache() -> None:
    _served_models_cache.clear()


def _model_ids(raw_models: list[Any]) -> list[str] | None:
    """All-or-nothing normalization; unreadable is None, never empty."""
    ids: list[str] = []
    for entry in raw_models:
        if isinstance(entry, str):
            value: Any = entry
        elif isinstance(entry, dict):
            value = entry.get("id") or entry.get("name") or entry.get("model")
        else:
            value = (
                getattr(entry, "id", None)
                or getattr(entry, "name", None)
                or getattr(entry, "model", None)
            )
        if not isinstance(value, str) or not value:
            return None
        ids.append(value)
    return ids


def _match_served(model: str, served: list[str]) -> str | None:
    """Exact first, then dash-delimited family match in either direction."""
    if model in served:
        return model
    for candidate in served:
        if candidate.startswith(model + "-") or model.startswith(candidate + "-"):
            return candidate
    return None


def _resolve_api_key(provider: str) -> str:
    return _resolve_credential(provider)[0]


async def served_model_check(*, timeout_seconds: float = 15.0) -> dict[str, Any]:
    """Separate from the gate: validated, not_served, or honestly unknown."""
    from . import runner as _runner

    library_reason = await _runner.library_unavailable_reason()
    if library_reason:
        return {
            "status": "unknown",
            "reason": "library_missing",
            "provider": None,
            "model": None,
            "served": None,
            "detail": library_reason,
        }
    provider = _runner.active_provider()
    model = _runner.default_model()

    def unknown(
        reason: str, detail: str, served: list[str] | None = None
    ) -> dict[str, Any]:
        return {
            "status": "unknown",
            "reason": reason,
            "provider": provider,
            "model": model,
            "served": served,
            "detail": detail,
        }

    try:
        value = _resolve_api_key(provider)
    except ValueError as exc:
        return unknown("credential_error", str(exc))
    if not value:
        return unknown(
            "no_credential",
            f"No {provider} credential is set, so the served model list cannot be read. "
            "This is not a sign the model is wrong.",
        )
    fingerprint = _credential_fingerprint(value)
    # Bound the entire single-flight wait, including lock contention.
    try:
        async with asyncio.timeout(timeout_seconds):
            async with _served_models_lock:
                cached = _served_models_cache.get(provider)
                if (
                    cached is not None
                    and cached[0] == fingerprint
                    and time.monotonic() - cached[1] < SERVED_MODELS_TTL_SECONDS
                ):
                    served = list(cached[2])
                else:
                    verdict, detail, raw_models = await _enumerate_models(
                        provider, value, timeout_seconds=timeout_seconds
                    )
                    if verdict != "ok" or raw_models is None:
                        return unknown(
                            verdict if verdict != "ok" else "error",
                            f"Could not read {provider}'s model list: {detail}. "
                            "This is not a sign the model is wrong.",
                        )
                    ids = _model_ids(raw_models)
                    if ids is None:
                        return unknown(
                            "unreadable_model_list",
                            f"{provider} returned a model list in a shape muxplex could not read.",
                        )
                    if not ids:
                        return unknown(
                            "no_enumeration",
                            f"{provider} does not publish a model list, so the model could not be checked.",
                            [],
                        )
                    served = ids
                    _served_models_cache[provider] = (
                        fingerprint,
                        time.monotonic(),
                        tuple(ids),
                    )
    except TimeoutError:
        return unknown(
            "unreachable",
            f"Model-list check timed out after {timeout_seconds}s. "
            "This is not a sign the model is wrong.",
        )
    matched = _match_served(model, served)
    if matched is None:
        status = "not_served"
        detail = (
            f"{provider} does not serve {model!r}. A turn using it will fail. "
            "Available models: " + ", ".join(sorted(served))
        )
    else:
        status = "validated"
        detail = (
            f"{provider} serves {model!r}."
            if matched == model
            else f"{provider} serves {model!r} as {matched!r}."
        )
    return {
        "status": status,
        "reason": None,
        "provider": provider,
        "model": model,
        "served": served,
        "detail": detail,
    }


async def full_status() -> dict[str, Any]:
    """Report active target and env/file availability without a provider call."""
    from . import runner as _runner

    library_reason = await _runner.library_unavailable_reason()
    if library_reason:
        return {
            "state": "not_installed",
            "message": library_reason,
            "providers": {},
            "sidecar": "running",
            "mode": "embedded",
            "active": {"provider": None, "model": None},
        }
    providers = {p: resolve_status(p) for p in sorted(ALLOWED_PROVIDERS)}
    provider = _runner.active_provider()
    active = providers.get(provider) or resolve_status(provider)
    if active["source"] not in ("env", "file"):
        state = "not_configured"
        message = "The Agent has no model provider key. It cannot run until one is set."
    elif active["source"] == "env":
        state = "configured_shadowed"
        message = "Embedded agent ready (using an environment-variable credential)."
    else:
        state = "configured"
        message = "Embedded agent ready."
    return {
        "state": state,
        "message": message,
        "providers": providers,
        "sidecar": "running",
        "mode": "embedded",
        "active": {"provider": provider, "model": _runner.default_model()},
    }


__all__ = [
    "ALLOWED_PROVIDERS",
    "SERVED_MODELS_TTL_SECONDS",
    "clear_served_models_cache",
    "create_agent_with_credentials",
    "credential_home",
    "full_status",
    "persist_key",
    "resolve_status",
    "served_model_check",
    "validate_key",
]
