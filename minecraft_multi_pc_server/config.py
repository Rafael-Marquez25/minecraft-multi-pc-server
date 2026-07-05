from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import socket
import tomllib
from typing import Any


class ConfigError(ValueError):
    """Raised when the launcher config is missing or invalid."""


@dataclass(frozen=True)
class Config:
    remote_server_dir: Path
    local_server_dir: Path
    start_command: list[str]
    machine_name: str = field(default_factory=socket.gethostname)
    sync_ignore: list[str] = field(default_factory=list)
    state_dir: Path | None = None
    stale_lock_minutes: int = 30
    heartbeat_seconds: float = 30.0

    @property
    def resolved_state_dir(self) -> Path:
        if self.state_dir is not None:
            return self.state_dir
        return self.remote_server_dir.parent / ".minecraft_multi_pc_state"


def load_config(path: str | Path) -> Config:
    config_path = Path(path)
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from exc

    return parse_config(raw, base_dir=config_path.parent)


def parse_config(raw: dict[str, Any], base_dir: Path | None = None) -> Config:
    base = base_dir or Path.cwd()
    remote = _required_path(raw, "remote_server_dir", base)
    local = _required_path(raw, "local_server_dir", base)
    start_command = raw.get("start_command")
    if not isinstance(start_command, list) or not start_command:
        raise ConfigError("'start_command' must be a non-empty array of strings")
    if not all(isinstance(part, str) and part for part in start_command):
        raise ConfigError("'start_command' must contain only non-empty strings")

    machine_name = raw.get("machine_name") or socket.gethostname()
    if not isinstance(machine_name, str):
        raise ConfigError("'machine_name' must be a string")

    sync_ignore = raw.get("sync_ignore", [])
    if not isinstance(sync_ignore, list) or not all(isinstance(item, str) for item in sync_ignore):
        raise ConfigError("'sync_ignore' must be an array of strings")

    state_dir = raw.get("state_dir")
    resolved_state_dir = None
    if state_dir is not None:
        if not isinstance(state_dir, str) or not state_dir:
            raise ConfigError("'state_dir' must be a non-empty string")
        resolved_state_dir = _resolve_path(state_dir, base)

    stale_lock_minutes = raw.get("stale_lock_minutes", 30)
    if not isinstance(stale_lock_minutes, int) or stale_lock_minutes <= 0:
        raise ConfigError("'stale_lock_minutes' must be a positive integer")

    heartbeat_seconds = raw.get("heartbeat_seconds", 30)
    if not isinstance(heartbeat_seconds, (int, float)) or heartbeat_seconds <= 0:
        raise ConfigError("'heartbeat_seconds' must be a positive number")

    return Config(
        remote_server_dir=remote,
        local_server_dir=local,
        start_command=list(start_command),
        machine_name=machine_name,
        sync_ignore=list(sync_ignore),
        state_dir=resolved_state_dir,
        stale_lock_minutes=stale_lock_minutes,
        heartbeat_seconds=float(heartbeat_seconds),
    )


def _required_path(raw: dict[str, Any], key: str, base: Path) -> Path:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"'{key}' must be a non-empty string")
    return _resolve_path(value, base)


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (base / path).resolve()
