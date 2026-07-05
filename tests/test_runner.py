from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest

from minecraft_multi_pc_server.config import Config
from minecraft_multi_pc_server.runner import run_server, status, sync_up
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


class RunnerTests(unittest.TestCase):
    def run_quietly(self, *args, **kwargs):
        with redirect_stdout(io.StringIO()):
            return run_server(*args, **kwargs)

    def test_run_server_copies_down_runs_command_uploads_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote"
            local = root / "local"
            state = root / "state"
            remote.mkdir()
            (remote / "start.sh").write_text("#!/bin/sh\nprintf changed > result.txt\n", encoding="utf-8")
            (remote / "start.sh").chmod(0o755)
            config = Config(
                remote_server_dir=remote,
                local_server_dir=local,
                start_command=["sh", "start.sh"],
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
            (remote / "start.sh").write_text("#!/bin/sh\nprintf changed > result.txt\n", encoding="utf-8")
            (remote / "start.sh").chmod(0o755)
            config = Config(
                remote_server_dir=remote,
                local_server_dir=local,
                start_command=["sh", "start.sh"],
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
            (remote / "start.sh").write_text("#!/bin/sh\nprintf ok > result.txt\n", encoding="utf-8")
            (remote / "start.sh").chmod(0o755)
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
                start_command=["sh", "start.sh"],
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


if __name__ == "__main__":
    unittest.main()
