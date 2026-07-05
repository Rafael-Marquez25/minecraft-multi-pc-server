from pathlib import Path
import tempfile
import unittest

from minecraft_multi_pc_server.config import ConfigError, load_config, parse_config


class ConfigTests(unittest.TestCase):
    def test_parse_config_resolves_paths_and_defaults_machine_name(self):
        config = parse_config(
            {
                "remote_server_dir": "remote",
                "local_server_dir": "local",
                "start_command": ["bash", "start.sh"],
                "sync_ignore": ["*.tmp"],
            },
            base_dir=Path("/tmp/project"),
        )

        self.assertEqual(config.remote_server_dir, Path("/tmp/project/remote"))
        self.assertEqual(config.local_server_dir, Path("/tmp/project/local"))
        self.assertEqual(config.start_command, ["bash", "start.sh"])
        self.assertEqual(config.sync_ignore, ["*.tmp"])
        self.assertEqual(config.resolved_state_dir, Path("/tmp/project/.minecraft_multi_pc_state"))

    def test_parse_config_rejects_empty_start_command(self):
        with self.assertRaises(ConfigError):
            parse_config(
                {
                    "remote_server_dir": "remote",
                    "local_server_dir": "local",
                    "start_command": [],
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
