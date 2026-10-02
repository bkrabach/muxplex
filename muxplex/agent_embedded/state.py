"""Muxplex-owned session owner/interruption metadata, never SDK transcripts."""

from __future__ import annotations

import fcntl
import hmac
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .errors import AgentRequestError

_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
_INTERRUPTED = (
    "Start a new conversation. An interrupted external effect must not be replayed."
)


class SessionLease:
    def __init__(self, root: Path, session_id: str, owner: str, *, new: bool) -> None:
        if not _SESSION_ID.fullmatch(session_id):
            raise AgentRequestError(
                "invalid_session",
                "Invalid agent session id.",
                "Start a new conversation.",
            )
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / f"{session_id}.json"
        self.fd = os.open(root / f"{session_id}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AgentRequestError(
                    "session_busy",
                    "An agent turn is already active.",
                    "Wait for it to finish.",
                    409,
                ) from exc
            if new:
                if self.path.exists():
                    raise AgentRequestError(
                        "session_exists",
                        "Agent session already exists.",
                        "Start a new conversation.",
                        409,
                    )
                self.data: dict[str, Any] = {"version": 1, "owner": owner, "run": None}
                self.write()
            else:
                try:
                    self.data = json.loads(self.path.read_text())
                except FileNotFoundError as exc:
                    raise AgentRequestError(
                        "session_missing",
                        "Agent session owner record is missing.",
                        "Start a new conversation.",
                        410,
                    ) from exc
                except (ValueError, OSError) as exc:
                    raise AgentRequestError(
                        "session_metadata_invalid",
                        "Agent session metadata cannot be read.",
                        _INTERRUPTED,
                        409,
                    ) from exc
                if (
                    not isinstance(self.data, dict)
                    or self.data.get("version") != 1
                    or not isinstance(self.data.get("owner"), str)
                    or (
                        self.data.get("run") is not None
                        and not isinstance(self.data["run"], dict)
                    )
                ):
                    raise AgentRequestError(
                        "session_metadata_invalid",
                        "Agent session metadata is invalid.",
                        _INTERRUPTED,
                        409,
                    )
                if not hmac.compare_digest(self.data["owner"], owner):
                    raise AgentRequestError(
                        "session_owner_mismatch",
                        "Agent session belongs to another browser login.",
                        "Start a new conversation.",
                        403,
                    )
        except BaseException:
            self.close()
            raise

    def reconcile(self, history: list[Any]) -> None:
        marker = self.data.get("run")
        if marker is None:
            return
        # A crash AFTER the SDK persisted terminal is safe to reconcile through
        # public history. A missing/incomplete terminal is deliberately blocked.
        if (
            marker.get("turn_id")
            and history
            and history[-1].turn_id == marker["turn_id"]
            and history[-1].result.state
            in {"success", "failure", "rejected", "cancelled"}
        ):
            self.data["run"] = None
            self.write()
            return
        raise AgentRequestError(
            "session_interrupted",
            "The prior turn has no reconciled terminal record.",
            _INTERRUPTED,
            409,
        )

    def mark(self, run_id: str, turn_id: str | None = None) -> None:
        self.data["run"] = {"run_id": run_id, "turn_id": turn_id}
        self.write()

    def finished(self) -> None:
        self.data["run"] = None
        self.write()

    def write(self) -> None:
        fd, tmp = tempfile.mkstemp(prefix=".owner-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self.data, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
