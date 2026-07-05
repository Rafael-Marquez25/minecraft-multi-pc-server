from __future__ import annotations

import argparse
import json
import sys

from .config import ConfigError, load_config
from .lock import LockError
from .runner import LauncherError, force_unlock, run_server, status, sync_up


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minecraft-launcher")
    parser.add_argument("-c", "--config", default="config.toml", help="Path to config.toml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Sync down, start the server, sync up, and unlock")
    run_parser.add_argument("--force-lock", action="store_true", help="Override an existing lock")

    subparsers.add_parser("status", help="Show lock and last run information")

    unlock_parser = subparsers.add_parser("unlock", help="Remove a stale lock")
    unlock_parser.add_argument("--force", action="store_true", required=True, help="Required to remove the lock")

    subparsers.add_parser("sync-up", help="Retry upload after a failed shutdown/upload")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        if args.command == "run":
            return run_server(config, force_lock=args.force_lock, confirm_stale_lock=_confirm_stale_lock)
        if args.command == "status":
            print(json.dumps(status(config), indent=2, sort_keys=True))
            return 0
        if args.command == "unlock":
            force_unlock(config)
            print("Lock removed.")
            return 0
        if args.command == "sync-up":
            sync_up(config)
            print("Upload recovered. Lock released.")
            return 0
    except (ConfigError, LockError, LauncherError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2


def _confirm_stale_lock(lock) -> bool:
    print(
        "Existing lock looks stale:\n"
        f"  owner: {lock.owner}\n"
        f"  created: {lock.created_at}\n"
        f"  heartbeat: {lock.heartbeat_at}\n"
        f"  status: {lock.status}"
    )
    try:
        answer = input("Override this lock and start here? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes", "s", "si", "sí"}
