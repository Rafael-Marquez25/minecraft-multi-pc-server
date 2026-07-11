from pathlib import Path
import unittest

from minecraft_multi_pc_server.config import Config
from minecraft_multi_pc_server.gui import drive_sync_target, lock_connection_detail, write_config
from minecraft_multi_pc_server.lock import LockInfo


class GuiDriveSyncTargetTests(unittest.TestCase):
    def test_uses_archive_dir(self):
        config = Config(
            remote_archive_dir=Path("drive"),
            local_server_dir=Path("local"),
            start_command=["python", "server.py"],
        )

        self.assertEqual(drive_sync_target(config), Path("drive"))

    def test_uses_fixed_archive_parent(self):
        config = Config(
            remote_archive_file=Path("drive/server.zip"),
            local_server_dir=Path("local"),
            start_command=["python", "server.py"],
        )

        self.assertEqual(drive_sync_target(config), Path("drive"))

    def test_uses_legacy_remote_dir(self):
        config = Config(
            remote_server_dir=Path("drive/server"),
            local_server_dir=Path("local"),
            start_command=["python", "server.py"],
        )

        self.assertEqual(drive_sync_target(config), Path("drive/server"))


class GuiConfigWriteTests(unittest.TestCase):
    def test_write_config_includes_server_ip(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            write_config(
                path,
                "drive",
                "local",
                ["python", "server.py"],
                "pc",
                ["logs/**"],
                "",
                30,
                30,
                1,
                "auto",
                "tailscale",
            )

            content = path.read_text(encoding="utf-8")
            self.assertIn('server_ip = "auto"', content)
            self.assertIn('connection_mode = "tailscale"', content)


class GuiLockDetailTests(unittest.TestCase):
    def test_lock_detail_shows_owner_and_connection_address(self):
        lock = LockInfo(
            owner="PC-Rafa",
            pid=123,
            status="running",
            created_at="now",
            heartbeat_at="now",
            connection_address="100.99.98.97:25565",
        )

        self.assertEqual(
            lock_connection_detail(lock),
            "PC-Rafa tiene el servidor iniciado. Conectate a 100.99.98.97:25565",
        )


if __name__ == "__main__":
    unittest.main()
