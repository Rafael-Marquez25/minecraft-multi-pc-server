from contextlib import redirect_stdout
from datetime import timedelta
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from minecraft_multi_pc_server.config import Config
from minecraft_multi_pc_server.lock import LockError, ServerLock
from minecraft_multi_pc_server.runner import (
    LauncherError,
    ServerProcessControl,
    _prepare_machine_server_config,
    run_server,
    status,
    sync_up,
)
from minecraft_multi_pc_server.state import write_json
from minecraft_multi_pc_server.sync import SyncError, Syncer


class FailingUploadSyncer(Syncer):
    def __init__(self, local_dir: Path):
        super().__init__(prefer_robocopy=False)
        self.local_dir = local_dir

    def mirror(self, source: Path, destination: Path):
        if source == self.local_dir:
            raise SyncError("fake upload failure")
        return super().mirror(source, destination)


class FakeStdin:
    def __init__(self, process, stop_on: str | None = None):
        self.process = process
        self.stop_on = stop_on
        self.lines: list[str] = []

    def write(self, value: str) -> int:
        self.lines.append(value)
        if value.strip() == self.stop_on:
            self.process.returncode = 0
        return len(value)

    def flush(self) -> None:
        pass


class FakeProcess:
    def __init__(self, stop_on: str | None = None):
        self.returncode = None
        self.stdin = FakeStdin(self, stop_on)
        self.signals: list[object] = []

    def poll(self):
        return self.returncode

    def send_signal(self, value) -> None:
        self.signals.append(value)


class RunnerTests(unittest.TestCase):
    def run_quietly(self, *args, **kwargs):
        with redirect_stdout(io.StringIO()):
            return run_server(*args, **kwargs)

    def test_safe_stop_completes_after_script_confirmation(self):
        process = FakeProcess(stop_on="s")
        control = ServerProcessControl()
        control.attach(process)  # type: ignore[arg-type]

        self.assertTrue(control.request_stop(confirm_delay=0, confirm_line="s"))
        self.assertEqual(process.stdin.lines[:2], ["stop\n", "s\n"])

    def test_safe_stop_can_be_retried_when_process_does_not_exit(self):
        process = FakeProcess()
        control = ServerProcessControl()
        control.attach(process)  # type: ignore[arg-type]

        self.assertFalse(control.request_stop(confirm_delay=0, confirm_line="s"))
        self.assertFalse(control.stop_requested)
        self.assertTrue(control.is_running())

    def test_sync_up_rejects_recovery_from_a_different_machine(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote"
            local = root / "local"
            state = root / "state"
            remote.mkdir()
            local.mkdir()
            (local / "world.dat").write_text("local", encoding="utf-8")
            original_lock = ServerLock(state, "pc-owner", timedelta(minutes=30))
            original_lock.acquire()
            original_lock.mark_status("upload_failed")
            config = Config(
                remote_server_dir=remote,
                local_server_dir=local,
                start_command=[sys.executable, "start.py"],
                machine_name="pc-other",
                state_dir=state,
            )

            with self.assertRaisesRegex(LauncherError, "belong to pc-owner"):
                sync_up(config, syncer=Syncer(prefer_robocopy=False))

            self.assertEqual(original_lock.read().status, "upload_failed")

    def test_start_rejects_an_already_used_minecraft_port_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote"
            local = root / "local"
            state = root / "state"
            remote.mkdir()
            (remote / "server.properties").write_text("server-port=25565\n", encoding="utf-8")
            (remote / "start.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
            config = Config(
                remote_server_dir=remote,
                local_server_dir=local,
                start_command=[sys.executable, "start.py"],
                machine_name="port-test",
                state_dir=state,
            )

            with patch("minecraft_multi_pc_server.runner.is_tcp_port_available", return_value=False):
                with self.assertRaisesRegex(LauncherError, "Port 25565 is already in use"):
                    self.run_quietly(config, syncer=Syncer(prefer_robocopy=False))

            self.assertIsNone(status(config)["lock"])

    def test_run_server_copies_down_runs_command_uploads_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote"
            local = root / "local"
            state = root / "state"
            remote.mkdir()
            (remote / "start.py").write_text("from pathlib import Path\nPath('result.txt').write_text('changed')\n", encoding="utf-8")
            config = Config(
                remote_server_dir=remote,
                local_server_dir=local,
                start_command=[sys.executable, "start.py"],
                machine_name="ubuntu-dev",
                state_dir=state,
                heartbeat_seconds=0.05,
            )

            exit_code = self.run_quietly(config, syncer=Syncer(prefer_robocopy=False))

            self.assertEqual(exit_code, 0)
            self.assertEqual((remote / "result.txt").read_text(encoding="utf-8"), "changed")
            self.assertIsNone(status(config)["lock"])
            self.assertIsNotNone(status(config)["last_run"])

    def test_failed_upload_keeps_upload_failed_lock_and_sync_up_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote"
            local = root / "local"
            state = root / "state"
            remote.mkdir()
            (remote / "start.py").write_text("from pathlib import Path\nPath('result.txt').write_text('changed')\n", encoding="utf-8")
            config = Config(
                remote_server_dir=remote,
                local_server_dir=local,
                start_command=[sys.executable, "start.py"],
                machine_name="ubuntu-dev",
                state_dir=state,
                heartbeat_seconds=0.05,
            )

            with self.assertRaises(SyncError):
                self.run_quietly(config, syncer=FailingUploadSyncer(local))

            lock = status(config)["lock"]
            self.assertEqual(lock["status"], "upload_failed")
            self.assertIsNotNone(status(config)["last_error"])

            sync_up(config, syncer=Syncer(prefer_robocopy=False))

            self.assertEqual((remote / "result.txt").read_text(encoding="utf-8"), "changed")
            self.assertIsNone(status(config)["lock"])

    def test_run_server_can_override_stale_lock_after_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote"
            local = root / "local"
            state = root / "state"
            remote.mkdir()
            (remote / "start.py").write_text("from pathlib import Path\nPath('result.txt').write_text('ok')\n", encoding="utf-8")
            write_json(
                state / "lock.json",
                {
                    "owner": "old-pc",
                    "pid": 1,
                    "status": "running",
                    "created_at": "2020-01-01T00:00:00+00:00",
                    "heartbeat_at": "2020-01-01T00:00:00+00:00",
                },
            )
            config = Config(
                remote_server_dir=remote,
                local_server_dir=local,
                start_command=[sys.executable, "start.py"],
                machine_name="ubuntu-dev",
                state_dir=state,
                stale_lock_minutes=1,
                heartbeat_seconds=0.05,
            )

            exit_code = self.run_quietly(
                config,
                syncer=Syncer(prefer_robocopy=False),
                confirm_stale_lock=lambda lock: True,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual((remote / "result.txt").read_text(encoding="utf-8"), "ok")
            self.assertIsNone(status(config)["lock"])

    def test_tailscale_connection_mode_blanks_server_ip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / "local"
            local.mkdir()
            (local / "server.properties").write_text("server-ip=192.168.1.20\n", encoding="utf-8")
            config = Config(
                remote_archive_dir=root / "drive",
                local_server_dir=local,
                start_command=[sys.executable, "start.py"],
                connection_mode="tailscale",
                server_ip="auto",
            )

            with patch("minecraft_multi_pc_server.runner.detect_tailscale_ipv4", return_value="100.99.98.97"):
                _prepare_machine_server_config(config)

            self.assertEqual((local / "server.properties").read_text(encoding="utf-8"), "server-ip=\n")

    def test_tailscale_mode_rejects_start_when_vpn_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote"
            local = root / "local"
            state = root / "state"
            remote.mkdir()
            (remote / "start.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
            config = Config(
                remote_server_dir=remote,
                local_server_dir=local,
                start_command=[sys.executable, "start.py"],
                machine_name="vpn-test",
                state_dir=state,
                connection_mode="tailscale",
            )

            with patch("minecraft_multi_pc_server.runner.detect_tailscale_ipv4", return_value=None):
                with self.assertRaisesRegex(LauncherError, "Tailscale no esta conectado"):
                    self.run_quietly(config, syncer=Syncer(prefer_robocopy=False))

            self.assertFalse(local.exists())
            self.assertIsNone(status(config)["lock"])

    def test_existing_lock_is_reported_before_local_vpn_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote"
            local = root / "local"
            state = root / "state"
            remote.mkdir()
            ServerLock(
                state,
                "PC-Anfitrion",
                timedelta(minutes=30),
                connection_address="100.88.77.66:25565",
            ).acquire()
            config = Config(
                remote_server_dir=remote,
                local_server_dir=local,
                start_command=[sys.executable, "start.py"],
                machine_name="PC-Cliente",
                state_dir=state,
                connection_mode="tailscale",
            )

            with patch("minecraft_multi_pc_server.runner.detect_tailscale_ipv4", return_value=None) as detect:
                with self.assertRaisesRegex(LockError, "PC-Anfitrion.*100.88.77.66:25565"):
                    self.run_quietly(config, syncer=Syncer(prefer_robocopy=False))

            detect.assert_not_called()

    def test_tailscale_mode_starts_when_vpn_is_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote"
            local = root / "local"
            state = root / "state"
            remote.mkdir()
            (remote / "start.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
            config = Config(
                remote_server_dir=remote,
                local_server_dir=local,
                start_command=[sys.executable, "start.py"],
                machine_name="vpn-test",
                state_dir=state,
                connection_mode="tailscale",
                heartbeat_seconds=0.05,
            )

            with patch("minecraft_multi_pc_server.runner.detect_tailscale_ipv4", return_value="100.99.98.97"):
                exit_code = self.run_quietly(config, syncer=Syncer(prefer_robocopy=False))

            self.assertEqual(exit_code, 0)
            self.assertIsNone(status(config)["lock"])


if __name__ == "__main__":
    unittest.main()
