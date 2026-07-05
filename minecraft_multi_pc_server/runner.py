from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import subprocess
import threading
import traceback
from typing import Callable

from .config import Config
from .lock import LockError, LockInfo, ServerLock
from .state import LAST_ERROR_FILE, LAST_RUN_FILE, iso_now, read_json, write_json
from .sync import Syncer


class LauncherError(RuntimeError):
    """Raised when the launcher workflow fails."""


def run_server(
    config: Config,
    force_lock: bool = False,
    syncer: Syncer | None = None,
    confirm_stale_lock: Callable[[LockInfo], bool] | None = None,
) -> int:
    lock = _make_lock(config)
    sync = syncer or Syncer(config.sync_ignore)
    acquired = False
    stop_heartbeat = threading.Event()
    heartbeat_thread: threading.Thread | None = None

    try:
        existing = lock.read()
        if existing is not None and not force_lock and lock.is_stale(existing) and confirm_stale_lock is not None:
            force_lock = confirm_stale_lock(existing)
        lock.acquire(force=force_lock)
        acquired = True
        print(f"Lock acquired for {config.machine_name}.")

        print("Copying remote server to local working folder...")
        sync.mirror(config.remote_server_dir, config.local_server_dir)

        heartbeat_thread = _start_heartbeat(lock, config.heartbeat_seconds, stop_heartbeat)
        print("Starting server command:")
        print(" ".join(config.start_command))
        completed = subprocess.run(config.start_command, cwd=config.local_server_dir, check=False)

        lock.mark_status("uploading")
        print("Server stopped. Copying local changes back to remote folder...")
        sync.mirror(config.local_server_dir, config.remote_server_dir)
        write_json(
            config.resolved_state_dir / LAST_RUN_FILE,
            {
                "machine": config.machine_name,
                "finished_at": iso_now(),
                "exit_code": completed.returncode,
            },
        )
        lock.release()
        acquired = False
        print("Upload complete. Lock released.")
        return completed.returncode
    except Exception as exc:
        _record_error(config, exc)
        if acquired:
            try:
                if _is_upload_failure(lock):
                    lock.mark_status("upload_failed")
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

    sync = syncer or Syncer(config.sync_ignore)
    lock.mark_status("uploading")
    try:
        sync.mirror(config.local_server_dir, config.remote_server_dir)
        write_json(
            config.resolved_state_dir / LAST_RUN_FILE,
            {
                "machine": config.machine_name,
                "finished_at": iso_now(),
                "exit_code": None,
                "recovered": True,
            },
        )
        lock.release(force=True)
    except Exception as exc:
        _record_error(config, exc)
        lock.mark_status("upload_failed")
        raise


def status(config: Config) -> dict[str, object]:
    state_dir = config.resolved_state_dir
    lock = _make_lock(config).read()
    return {
        "lock": lock.to_dict() if lock is not None else None,
        "last_run": read_json(state_dir / LAST_RUN_FILE),
        "last_error": read_json(state_dir / LAST_ERROR_FILE),
    }


def force_unlock(config: Config) -> None:
    _make_lock(config).release(force=True)


def _make_lock(config: Config) -> ServerLock:
    return ServerLock(
        config.resolved_state_dir,
        config.machine_name,
        stale_after=timedelta(minutes=config.stale_lock_minutes),
    )


def _start_heartbeat(lock: ServerLock, interval: float, stop_event: threading.Event) -> threading.Thread:
    def worker() -> None:
        while not stop_event.wait(interval):
            try:
                lock.heartbeat()
            except LockError:
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


def _is_upload_failure(lock: ServerLock) -> bool:
    existing = lock.read()
    return existing is not None and existing.status == "uploading"
