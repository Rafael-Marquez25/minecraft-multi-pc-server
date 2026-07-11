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
    local_server_dir: Path
    start_command: list[str]
    remote_server_dir: Path | None = None
    remote_archive_file: Path | None = None
    remote_archive_dir: Path | None = None
    machine_name: str = field(default_factory=socket.gethostname)
    sync_ignore: list[str] = field(default_factory=list)
    state_dir: Path | None = None
    stale_lock_minutes: int = 30
    heartbeat_seconds: float = 30.0
    archive_compression_level: int = 1
    server_ip: str | None = None
    connection_mode: str = "manual"

    @property
    def resolved_state_dir(self) -> Path:
        if self.state_dir is not None:
            return self.state_dir
        if self.remote_archive_dir is not None:
            return self.remote_archive_dir / ".minecraft_multi_pc_state"
        if self.remote_archive_file is not None:
            return self.remote_archive_file.parent / ".minecraft_multi_pc_state"
        if self.remote_server_dir is not None:
            return self.remote_server_dir.parent / ".minecraft_multi_pc_state"
        raise ConfigError("Config must define a remote server directory or archive")

    @property
    def uses_archive(self) -> bool:
        return self.remote_archive_dir is not None or self.remote_archive_file is not None


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
    remote = _optional_path(raw, "remote_server_dir", base)
    archive = _optional_path(raw, "remote_archive_file", base)
    archive_dir = _optional_path(raw, "remote_archive_dir", base)
    if sum(value is not None for value in (remote, archive, archive_dir)) != 1:
        raise ConfigError("Define exactly one of 'remote_server_dir', 'remote_archive_file', or 'remote_archive_dir'")
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
    if isinstance(stale_lock_minutes, bool) or not isinstance(stale_lock_minutes, int) or stale_lock_minutes <= 0:
        raise ConfigError("'stale_lock_minutes' must be a positive integer")

    heartbeat_seconds = raw.get("heartbeat_seconds", 30)
    if isinstance(heartbeat_seconds, bool) or not isinstance(heartbeat_seconds, (int, float)) or heartbeat_seconds <= 0:
        raise ConfigError("'heartbeat_seconds' must be a positive number")
    if float(heartbeat_seconds) >= stale_lock_minutes * 60:
        raise ConfigError("'heartbeat_seconds' must be shorter than the stale lock timeout")

    archive_compression_level = raw.get("archive_compression_level", 1)
    if (
        isinstance(archive_compression_level, bool)
        or not isinstance(archive_compression_level, int)
        or archive_compression_level < 0
        or archive_compression_level > 9
    ):
        raise ConfigError("'archive_compression_level' must be an integer from 0 to 9")

    server_ip = raw.get("server_ip")
    if server_ip is not None and not isinstance(server_ip, str):
        raise ConfigError("'server_ip' must be a string")

    connection_mode = raw.get("connection_mode", "manual")
    if connection_mode not in {"manual", "tailscale"}:
        raise ConfigError("'connection_mode' must be 'manual' or 'tailscale'")

    config = Config(
        local_server_dir=local,
        start_command=list(start_command),
        remote_server_dir=remote,
        remote_archive_file=archive,
        remote_archive_dir=archive_dir,
        machine_name=machine_name,
        sync_ignore=list(sync_ignore),
        state_dir=resolved_state_dir,
        stale_lock_minutes=stale_lock_minutes,
        heartbeat_seconds=float(heartbeat_seconds),
        archive_compression_level=archive_compression_level,
        server_ip=server_ip,
        connection_mode=connection_mode,
    )
    _validate_path_layout(config)
    return config


def _required_path(raw: dict[str, Any], key: str, base: Path) -> Path:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"'{key}' must be a non-empty string")
    return _resolve_path(value, base)


def _optional_path(raw: dict[str, Any], key: str, base: Path) -> Path | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(f"'{key}' must be a non-empty string")
    return _resolve_path(value, base)


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (base / path).resolve()


def _validate_path_layout(config: Config) -> None:
    local = config.local_server_dir.resolve(strict=False)
    remote_paths = [
        path.resolve(strict=False)
        for path in (config.remote_archive_dir, config.remote_archive_file, config.remote_server_dir)
        if path is not None
    ]
    for remote in remote_paths:
        remote_root = remote.parent if config.remote_archive_file is not None and remote == config.remote_archive_file.resolve(strict=False) else remote
        if local == remote_root or local in remote_root.parents or remote_root in local.parents:
            raise ConfigError("'local_server_dir' and the remote server location must not overlap")
    state = config.resolved_state_dir.resolve(strict=False)
    if state == local or state in local.parents or local in state.parents:
        raise ConfigError("'state_dir' and 'local_server_dir' must not overlap")
