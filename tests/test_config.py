from pathlib import Path
import tempfile
import unittest

from minecraft_multi_pc_server.config import ConfigError, load_config, parse_config


class ConfigTests(unittest.TestCase):
    def test_parse_config_resolves_paths_and_defaults_machine_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = parse_config(
                {
                    "remote_server_dir": "remote",
                    "local_server_dir": "local",
                    "start_command": ["python", "start.py"],
                    "sync_ignore": ["*.tmp"],
                },
                base_dir=base,
            )

        self.assertEqual(config.remote_server_dir, base / "remote")
        self.assertEqual(config.local_server_dir, base / "local")
        self.assertEqual(config.start_command, ["python", "start.py"])
        self.assertEqual(config.sync_ignore, ["*.tmp"])
        self.assertEqual(config.resolved_state_dir, base / ".minecraft_multi_pc_state")

    def test_parse_config_accepts_archive_dir_and_default_state_next_to_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = parse_config(
                {
                    "remote_archive_dir": "drive",
                    "local_server_dir": "local",
                    "start_command": ["python", "start.py"],
                    "archive_compression_level": 1,
                },
                base_dir=base,
            )

        self.assertEqual(config.remote_archive_dir, base / "drive")
        self.assertEqual(config.resolved_state_dir, base / "drive" / ".minecraft_multi_pc_state")

    def test_parse_config_rejects_multiple_remote_modes(self):
        with self.assertRaises(ConfigError):
            parse_config(
                {
                    "remote_archive_dir": "drive",
                    "remote_server_dir": "remote",
                    "local_server_dir": "local",
                    "start_command": ["python", "start.py"],
                }
            )

    def test_parse_config_rejects_empty_start_command(self):
        with self.assertRaises(ConfigError):
            parse_config(
                {
                    "remote_server_dir": "remote",
                    "local_server_dir": "local",
                    "start_command": [],
                }
            )

    def test_parse_config_accepts_server_ip_setting(self):
        config = parse_config(
            {
                "remote_archive_dir": "drive",
                "local_server_dir": "local",
                "start_command": ["python", "start.py"],
                "server_ip": "auto",
            }
        )

        self.assertEqual(config.server_ip, "auto")

    def test_parse_config_accepts_connection_modes(self):
        for mode in ("manual", "tailscale"):
            config = parse_config(
                {
                    "remote_archive_dir": "drive",
                    "local_server_dir": "local",
                    "start_command": ["python", "start.py"],
                    "connection_mode": mode,
                }
            )

            self.assertEqual(config.connection_mode, mode)

    def test_parse_config_rejects_unknown_connection_mode(self):
        with self.assertRaises(ConfigError):
            parse_config(
                {
                    "remote_archive_dir": "drive",
                    "local_server_dir": "local",
                    "start_command": ["python", "start.py"],
                    "connection_mode": "playit",
                }
            )

    def test_parse_config_rejects_overlapping_local_and_remote_paths(self):
        with self.assertRaisesRegex(ConfigError, "must not overlap"):
            parse_config(
                {
                    "remote_archive_dir": "shared",
                    "local_server_dir": "shared/local",
                    "start_command": ["python", "start.py"],
                }
            )

    def test_parse_config_rejects_heartbeat_longer_than_stale_timeout(self):
        with self.assertRaisesRegex(ConfigError, "shorter than"):
            parse_config(
                {
                    "remote_archive_dir": "drive",
                    "local_server_dir": "local",
                    "start_command": ["python", "start.py"],
                    "stale_lock_minutes": 1,
                    "heartbeat_seconds": 60,
                }
            )

    def test_load_config_reads_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                "\n".join(
                    [
                        'remote_server_dir = "remote"',
                        'local_server_dir = "local"',
                        'start_command = ["bash", "start.sh"]',
                        'machine_name = "devbox"',
                    ]
                ),
                encoding="utf-8",
            )

            config = load_config(path)

        self.assertEqual(config.machine_name, "devbox")


if __name__ == "__main__":
    unittest.main()
