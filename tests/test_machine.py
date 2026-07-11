from pathlib import Path
import tempfile
import unittest

from minecraft_multi_pc_server.machine import (
    activate_tailscale,
    apply_server_ip_override,
    detect_tailscale_ipv4,
    is_tailscale_ipv4,
    read_minecraft_server_port,
    parse_tailscale_status,
    resolve_server_ip_setting,
    update_server_properties_ip_text,
)
from unittest.mock import patch


class MachineConfigTests(unittest.TestCase):
    def test_reads_custom_minecraft_server_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            server_dir = Path(tmp)
            (server_dir / "server.properties").write_text("motd=test\nserver-port=25570\n", encoding="utf-8")

            self.assertEqual(read_minecraft_server_port(server_dir), 25570)

    def test_rejects_invalid_minecraft_server_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            server_dir = Path(tmp)
            (server_dir / "server.properties").write_text("server-port=99999\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
                read_minecraft_server_port(server_dir)

    @patch("minecraft_multi_pc_server.machine.detect_tailscale_ipv4", return_value="100.99.98.97")
    def test_activate_tailscale_returns_existing_connection(self, _detect):
        address, message = activate_tailscale()

        self.assertEqual(address, "100.99.98.97")
        self.assertIn("ya estaba conectado", message)

    @patch("minecraft_multi_pc_server.machine.find_tailscale_executable", return_value=None)
    @patch("minecraft_multi_pc_server.machine.detect_tailscale_ipv4", return_value=None)
    def test_activate_tailscale_reports_missing_installation(self, _detect, _find):
        address, message = activate_tailscale()

        self.assertIsNone(address)
        self.assertIn("no esta instalado", message)

    def test_resolve_auto_uses_detected_ip(self):
        self.assertEqual(resolve_server_ip_setting("auto", detected_ip="192.168.1.44"), "192.168.1.44")

    def test_update_server_properties_replaces_existing_server_ip(self):
        updated, changed = update_server_properties_ip_text("motd=test\nserver-ip=1.2.3.4\n", "192.168.1.44")

        self.assertTrue(changed)
        self.assertEqual(updated, "motd=test\nserver-ip=192.168.1.44\n")

    def test_update_server_properties_adds_missing_server_ip(self):
        updated, changed = update_server_properties_ip_text("motd=test\n", "")

        self.assertTrue(changed)
        self.assertEqual(updated, "motd=test\nserver-ip=\n")

    def test_apply_server_ip_override_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            server_dir = Path(tmp)
            properties = server_dir / "server.properties"
            properties.write_text("server-ip=old\n", encoding="utf-8")

            message = apply_server_ip_override(server_dir, "192.168.1.50")

            self.assertIn("192.168.1.50", message or "")
            self.assertEqual(properties.read_text(encoding="utf-8"), "server-ip=192.168.1.50\n")

    def test_detect_tailscale_ipv4_recognizes_tailscale_range(self):
        self.assertEqual(detect_tailscale_ipv4(["192.168.1.4", "100.99.98.97"]), "100.99.98.97")

    def test_tailscale_ipv4_ignores_lan_ranges(self):
        self.assertFalse(is_tailscale_ipv4("192.168.1.44"))
        self.assertFalse(is_tailscale_ipv4("10.0.0.12"))
        self.assertFalse(is_tailscale_ipv4("172.16.0.12"))

    def test_parse_tailscale_status_reads_running_backend(self):
        result = parse_tailscale_status(
            '{"BackendState":"Running","TailscaleIPs":["100.99.98.97","fd7a:115c:a1e0::1"]}'
        )

        self.assertEqual(result, ("Running", ["100.99.98.97", "fd7a:115c:a1e0::1"]))

    def test_parse_tailscale_status_reads_disconnected_backend(self):
        result = parse_tailscale_status(
            '{"BackendState":"Stopped","TailscaleIPs":["100.99.98.97"]}'
        )

        self.assertEqual(result, ("Stopped", ["100.99.98.97"]))


if __name__ == "__main__":
    unittest.main()
