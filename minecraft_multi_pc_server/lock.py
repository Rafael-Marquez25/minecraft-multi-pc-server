from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
import os

from .state import LOCK_FILE, StateError, iso_now, parse_iso, read_json, remove_file, utc_now, write_json, write_json_exclusive


class LockError(RuntimeError):
    """Raised when the shared server lock cannot be acquired or released safely."""


@dataclass(frozen=True)
class LockInfo:
    owner: str
    pid: int
    status: str
    created_at: str
    heartbeat_at: str
    connection_address: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "LockInfo":
        try:
            pid = int(payload.get("pid", 0))
        except (TypeError, ValueError) as exc:
            raise LockError("Invalid lock file: 'pid' must be an integer") from exc
        owner = payload.get("owner", "unknown")
        status = payload.get("status", "running")
        created_at = payload.get("created_at", "")
        heartbeat_at = payload.get("heartbeat_at", "")
        connection_address = payload.get("connection_address", "")
        if not all(isinstance(value, str) for value in (owner, status, created_at, heartbeat_at, connection_address)):
            raise LockError("Invalid lock file: text fields have invalid types")
        return cls(owner, pid, status, created_at, heartbeat_at, connection_address)

    def to_dict(self) -> dict[str, object]:
        return {
            "owner": self.owner,
            "pid": self.pid,
            "status": self.status,
            "created_at": self.created_at,
            "heartbeat_at": self.heartbeat_at,
            "connection_address": self.connection_address,
        }


class ServerLock:
    def __init__(
        self,
        state_dir: Path,
        machine_name: str,
        stale_after: timedelta,
        connection_address: str = "",
    ):
        self.state_dir = state_dir
        self.machine_name = machine_name
        self.stale_after = stale_after
        self.connection_address = connection_address
        self.path = state_dir / LOCK_FILE

    def read(self) -> LockInfo | None:
        try:
            payload = read_json(self.path)
        except StateError as exc:
            raise LockError(str(exc)) from exc
        if payload is None:
            return None
        return LockInfo.from_dict(payload)

    def is_stale(self, lock: LockInfo) -> bool:
        try:
            heartbeat = parse_iso(lock.heartbeat_at)
        except ValueError:
            return True
        return utc_now() - heartbeat > self.stale_after

    def acquire(self, force: bool = False) -> LockInfo:
        existing = self.read()
        if existing is not None and not force:
            raise LockError(_lock_message(existing, self.is_stale(existing)))

        self.state_dir.mkdir(parents=True, exist_ok=True)
        now = iso_now()
        lock = LockInfo(
            owner=self.machine_name,
            pid=os.getpid(),
            status="running",
            created_at=now,
            heartbeat_at=now,
            connection_address=self.connection_address,
        )
        try:
            if force:
                write_json(self.path, lock.to_dict())
            else:
                write_json_exclusive(self.path, lock.to_dict())
        except FileExistsError as exc:
            latest = self.read()
            if latest is not None:
                raise LockError(_lock_message(latest, self.is_stale(latest))) from exc
            raise
        return lock

    def heartbeat(self, status: str = "running") -> None:
        existing = self.read()
        if existing is None:
            raise LockError("Cannot heartbeat: lock file is missing")
        if existing.owner != self.machine_name:
            raise LockError(f"Cannot heartbeat: lock is owned by {existing.owner}")
        updated = LockInfo(
            owner=existing.owner,
            pid=existing.pid,
            status=status,
            created_at=existing.created_at,
            heartbeat_at=iso_now(),
            connection_address=existing.connection_address,
        )
        write_json(self.path, updated.to_dict())

    def mark_status(self, status: str) -> None:
        existing = self.read()
        if existing is None:
            raise LockError("Cannot update lock: lock file is missing")
        updated = LockInfo(
            owner=existing.owner,
            pid=existing.pid,
            status=status,
            created_at=existing.created_at,
            heartbeat_at=iso_now(),
            connection_address=existing.connection_address,
        )
        write_json(self.path, updated.to_dict())

    def release(self, force: bool = False) -> None:
        existing = self.read()
        if existing is not None and existing.owner != self.machine_name and not force:
            raise LockError(f"Cannot release lock owned by {existing.owner}")
        remove_file(self.path)


def _lock_message(lock: LockInfo, stale: bool) -> str:
    suffix = " It looks stale; use unlock --force or confirm override." if stale else ""
    connection = f" Connect to {lock.connection_address}." if lock.connection_address else ""
    return (
        f"Server is locked by {lock.owner} since {lock.created_at} "
        f"(last heartbeat {lock.heartbeat_at}, status {lock.status}).{connection}{suffix}"
    )
