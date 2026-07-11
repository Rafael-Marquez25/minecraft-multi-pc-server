from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import signal
import shutil
import subprocess
import threading
import time
import traceback
from typing import Callable

from .archive import build_dated_archive_path, prune_old_archives, resolve_latest_archive
from .config import Config
from .lock import LockError, LockInfo, ServerLock
from .machine import (
    apply_server_ip_override,
    detect_local_ipv4,
    detect_tailscale_ipv4,
    is_tcp_port_available,
    read_minecraft_server_port,
)
from .state import LAST_ERROR_FILE, LAST_RUN_FILE, StateError, iso_now, read_json, remove_file, write_json, write_text_atomic
from .sync import Syncer


class LauncherError(RuntimeError):
    """Raised when the launcher workflow fails."""


class ServerProcessControl:
    """Thread-safe handle used by the GUI to stop the active server."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._stop_requested = False

    def attach(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            self._process = process
            self._stop_requested = False

    def detach(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            if self._process is process:
                self._process = None

    def is_running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    @property
    def stop_requested(self) -> bool:
        with self._lock:
            return self._stop_requested

    def send_line(self, value: str) -> bool:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                return False
            if process.stdin is None:
                raise LauncherError("Server process stdin is not available.")
            try:
                process.stdin.write(value + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise LauncherError(f"Could not send '{value}' to the server process: {exc}") from exc
            return True

    def request_stop(self, confirm_delay: float = 2.0, confirm_line: str = "s") -> bool:
        with self._lock:
            if self._stop_requested:
                return False
            self._stop_requested = True
        try:
            if not self.send_line("stop"):
                return not self.is_running()
            time.sleep(confirm_delay)
            if self.is_running():
                self.send_line(confirm_line)
            time.sleep(confirm_delay)
            if self.is_running():
                self.send_ctrl_c()
            time.sleep(confirm_delay)
            if self.is_running():
                self.send_line(confirm_line)
            return not self.is_running()
        finally:
            if self.is_running():
                with self._lock:
                    self._stop_requested = False

    def send_ctrl_c(self) -> bool:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                return False
            try:
                process.send_signal(_ctrl_c_signal())
            except (OSError, ValueError):
                return False
            return True


def run_server(
    config: Config,
    force_lock: bool = False,
    syncer: Syncer | None = None,
    confirm_stale_lock: Callable[[LockInfo], bool] | None = None,
    process_control: ServerProcessControl | None = None,
) -> int:
    lock = _make_lock(config)
    sync = syncer or Syncer(config.sync_ignore)
    acquired = False
    server_started = False
    stop_heartbeat = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    heartbeat_failures: list[Exception] = []

    try:
        existing = lock.read()
        if existing is not None and not force_lock and lock.is_stale(existing) and confirm_stale_lock is not None:
            force_lock = confirm_stale_lock(existing)
        if existing is not None and not force_lock:
            # Report the remote host before checking this machine's VPN state.
            lock.acquire()
        connection_address = _connection_address(config)
        lock = _make_lock(config, connection_address)
        lock.acquire(force=force_lock)
        acquired = True
        print(f"Lock acquired for {config.machine_name}.")

        print("Copying remote server to local working folder...")
        _sync_down(config, sync)
        _prepare_serverpackcreator_variables(config.local_server_dir)
        _prepare_machine_server_config(config)
        _require_connection_available(config)
        _validate_start_command(config)
        _require_server_port_available(config.local_server_dir)

        heartbeat_thread = _start_heartbeat(lock, config.heartbeat_seconds, stop_heartbeat, heartbeat_failures)
        print("Starting server command:")
        print(" ".join(config.start_command))
        process = subprocess.Popen(
            config.start_command,
            cwd=config.local_server_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_creation_flags(),
            startupinfo=_startupinfo(),
        )
        server_started = True
        if process_control is not None:
            process_control.attach(process)
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    print(line, end="")
            returncode = process.wait()
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stdin is not None:
                process.stdin.close()
            if process_control is not None:
                process_control.detach(process)

        stop_heartbeat.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=2)
            heartbeat_thread = None

        if heartbeat_failures:
            try:
                lock.mark_status("upload_failed")
            except (LockError, StateError):
                pass
            raise LauncherError(
                "The shared lock heartbeat failed while the server was running. "
                "Local changes were preserved and were not uploaded; restore Drive connectivity and use sync-up. "
                f"Cause: {heartbeat_failures[0]}"
            )

        lock.mark_status("uploading")
        print("Server stopped. Copying local changes back to remote folder...")
        _sync_up_files(config, sync)
        write_json(
            config.resolved_state_dir / LAST_RUN_FILE,
            {
                "machine": config.machine_name,
                "finished_at": iso_now(),
                "exit_code": returncode,
            },
        )
        remove_file(config.resolved_state_dir / LAST_ERROR_FILE)
        lock.release()
        acquired = False
        print("Upload complete. Lock released.")
        return returncode
    except Exception as exc:
        _record_error_safely(config, exc)
        if acquired:
            try:
                if server_started or _is_upload_failure(lock):
                    try:
                        lock.mark_status("upload_failed")
                    except LockError:
                        pass
                    print("Upload failed. Lock kept so another PC does not start from stale data.")
                else:
                    lock.release()
                    acquired = False
                    print("Startup failed. Lock released.")
            except LockError:
                pass
        raise
    finally:
        stop_heartbeat.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=2)


def sync_up(config: Config, syncer: Syncer | None = None) -> None:
    lock = _make_lock(config)
    existing = lock.read()
    if existing is None:
        raise LauncherError("No lock exists. Nothing to recover.")
    if existing.status != "upload_failed":
        raise LauncherError(f"Refusing sync-up while lock status is '{existing.status}', expected 'upload_failed'.")
    if existing.owner != config.machine_name:
        raise LauncherError(
            f"Refusing sync-up on {config.machine_name}: the recoverable local files belong to {existing.owner}. "
            "Run recovery on that PC, or force-unlock only after confirming its data cannot be recovered."
        )

    sync = syncer or Syncer(config.sync_ignore)
    lock.mark_status("uploading")
    try:
        _sync_up_files(config, sync)
        write_json(
            config.resolved_state_dir / LAST_RUN_FILE,
            {
                "machine": config.machine_name,
                "finished_at": iso_now(),
                "exit_code": None,
                "recovered": True,
            },
        )
        remove_file(config.resolved_state_dir / LAST_ERROR_FILE)
        lock.release(force=True)
    except Exception as exc:
        _record_error_safely(config, exc)
        lock.mark_status("upload_failed")
        raise


def status(config: Config) -> dict[str, object]:
    state_dir = config.resolved_state_dir
    lock = _make_lock(config).read()
    return {
        "lock": lock.to_dict() if lock is not None else None,
        "last_run": _read_optional_state(state_dir / LAST_RUN_FILE),
        "last_error": _read_optional_state(state_dir / LAST_ERROR_FILE),
    }


def force_unlock(config: Config) -> None:
    _make_lock(config).release(force=True)


def _make_lock(config: Config, connection_address: str = "") -> ServerLock:
    return ServerLock(
        config.resolved_state_dir,
        config.machine_name,
        stale_after=timedelta(minutes=config.stale_lock_minutes),
        connection_address=connection_address,
    )


def _sync_down(config: Config, sync: Syncer) -> None:
    if config.remote_archive_dir is not None:
        archive = resolve_latest_archive(config.remote_archive_dir)
        print(f"Using archive: {archive}")
        sync.extract_archive(archive, config.local_server_dir)
        return
    if config.remote_archive_file is not None:
        sync.extract_archive(config.remote_archive_file, config.local_server_dir)
        return
    if config.remote_server_dir is None:
        raise LauncherError("Config must define 'remote_server_dir', 'remote_archive_file', or 'remote_archive_dir'.")
    sync.mirror(config.remote_server_dir, config.local_server_dir)


def _sync_up_files(config: Config, sync: Syncer) -> None:
    if config.remote_archive_dir is not None:
        archive = build_dated_archive_path(config.remote_archive_dir)
        print(f"Creating archive: {archive}")
        sync.create_archive(config.local_server_dir, archive, compression_level=config.archive_compression_level)
        try:
            removed = prune_old_archives(config.remote_archive_dir, keep=3, protected=archive)
        except OSError as exc:
            print(f"Warning: old ZIP cleanup failed: {exc}")
        else:
            for old_archive in removed:
                print(f"Removed old archive: {old_archive.name}")
        return
    if config.remote_archive_file is not None:
        sync.create_archive(
            config.local_server_dir,
            config.remote_archive_file,
            compression_level=config.archive_compression_level,
        )
        return
    if config.remote_server_dir is None:
        raise LauncherError("Config must define 'remote_server_dir', 'remote_archive_file', or 'remote_archive_dir'.")
    sync.mirror(config.local_server_dir, config.remote_server_dir)


def _start_heartbeat(
    lock: ServerLock,
    interval: float,
    stop_event: threading.Event,
    failures: list[Exception] | None = None,
) -> threading.Thread:
    def worker() -> None:
        while not stop_event.wait(interval):
            try:
                lock.heartbeat()
            except Exception as exc:
                if failures is not None:
                    failures.append(exc)
                return

    thread = threading.Thread(target=worker, name="minecraft-launcher-heartbeat", daemon=True)
    thread.start()
    return thread


def _record_error(config: Config, exc: Exception) -> None:
    write_json(
        config.resolved_state_dir / LAST_ERROR_FILE,
        {
            "machine": config.machine_name,
            "time": iso_now(),
            "type": exc.__class__.__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        },
    )


def _record_error_safely(config: Config, exc: Exception) -> None:
    try:
        _record_error(config, exc)
    except Exception as state_exc:
        print(f"Warning: could not persist error details: {state_exc}")


def _read_optional_state(path: Path) -> dict[str, object] | None:
    try:
        return read_json(path)
    except StateError as exc:
        return {"corrupt": True, "message": str(exc)}


def _is_upload_failure(lock: ServerLock) -> bool:
    existing = lock.read()
    return existing is not None and existing.status == "uploading"


def _prepare_serverpackcreator_variables(server_dir: Path) -> None:
    variables = server_dir / "variables.txt"
    try:
        content = variables.read_text(encoding="utf-8")
    except FileNotFoundError:
        return

    changed = False
    lines: list[str] = []
    for line in content.splitlines(keepends=True):
        stripped = line.lstrip()
        prefix = line[: len(line) - len(stripped)]
        upper = stripped.upper()
        if upper.startswith("WAIT_FOR_USER_INPUT="):
            newline = "\n" if line.endswith("\n") else ""
            lines.append(f"{prefix}WAIT_FOR_USER_INPUT=false{newline}")
            changed = changed or stripped.strip() != "WAIT_FOR_USER_INPUT=false"
        elif upper.startswith("RESTART="):
            newline = "\n" if line.endswith("\n") else ""
            lines.append(f"{prefix}RESTART=false{newline}")
            changed = changed or stripped.strip() != "RESTART=false"
        else:
            lines.append(line)

    if changed:
        write_text_atomic(variables, "".join(lines))
        print("ServerPackCreator variables prepared: WAIT_FOR_USER_INPUT=false, RESTART=false")


def _prepare_machine_server_config(config: Config) -> None:
    server_ip_setting = config.server_ip
    if config.connection_mode == "tailscale":
        tailscale_ip = detect_tailscale_ipv4()
        if tailscale_ip:
            print(f"Tailscale activo: {tailscale_ip}")
        else:
            print("Aviso: no se detecto IP Tailscale activa.")
        server_ip_setting = "blank"

    message = apply_server_ip_override(config.local_server_dir, server_ip_setting)
    if message:
        print(message)


def _require_connection_available(config: Config) -> str | None:
    """Reject startup when the selected connection mode is unavailable."""
    if config.connection_mode != "tailscale":
        return None

    tailscale_ip = detect_tailscale_ipv4()
    if tailscale_ip is None:
        raise LauncherError(
            "Tailscale no esta conectado. Conecta la VPN, pulsa Actualizar estado "
            "y vuelve a iniciar el servidor."
        )
    print(f"Tailscale disponible para iniciar: {tailscale_ip}")
    return tailscale_ip


def _connection_address(config: Config) -> str:
    if config.connection_mode == "tailscale":
        address = _require_connection_available(config)
    else:
        address = detect_local_ipv4()
    return f"{address}:25565"


def _validate_start_command(config: Config) -> None:
    executable = config.start_command[0]
    executable_path = Path(executable)
    if executable_path.is_absolute():
        executable_exists = executable_path.is_file()
    else:
        executable_exists = shutil.which(executable) is not None
    if not executable_exists:
        raise LauncherError(f"Start command executable was not found: {executable}")

    lowered = [part.casefold() for part in config.start_command]
    script_index: int | None = None
    for marker in ("-file", "/c"):
        if marker in lowered:
            index = lowered.index(marker) + 1
            if index < len(config.start_command):
                script_index = index
                break
    if script_index is None:
        return
    script = Path(config.start_command[script_index])
    if not script.is_absolute():
        script = config.local_server_dir / script
    if not script.is_file():
        raise LauncherError(f"Configured server start script does not exist: {script}")


def _require_server_port_available(server_dir: Path) -> None:
    try:
        port = read_minecraft_server_port(server_dir)
    except ValueError as exc:
        raise LauncherError(str(exc)) from exc
    if port is not None and not is_tcp_port_available(port):
        raise LauncherError(
            f"Port {port} is already in use on this PC. Another Minecraft server may still be running; "
            "close it before starting to avoid corrupting or overwriting its world."
        )


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def _startupinfo() -> subprocess.STARTUPINFO | None:
    if not hasattr(subprocess, "STARTUPINFO"):
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    startupinfo.wShowWindow = 0
    return startupinfo


def _ctrl_c_signal() -> signal.Signals:
    return getattr(signal, "CTRL_C_EVENT", signal.SIGINT)
