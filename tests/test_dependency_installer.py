import unittest
from unittest.mock import patch

from scripts.install_dependencies import installed_state, winget_install_command


class DependencyInstallerTests(unittest.TestCase):
    def test_winget_command_is_exact_and_noninteractive(self):
        command = winget_install_command("Tailscale.Tailscale")

        self.assertEqual(command[:4], ["winget", "install", "--id", "Tailscale.Tailscale"])
        self.assertIn("--exact", command)
        self.assertIn("--disable-interactivity", command)
        self.assertIn("--accept-package-agreements", command)

    @patch("scripts.install_dependencies.google_drive_installed", return_value=False)
    @patch("scripts.install_dependencies.tailscale_installed", return_value=True)
    def test_installed_state_reports_each_dependency(self, _tailscale, _drive):
        with patch.dict(
            "scripts.install_dependencies.CHECKS",
            {
                "Tailscale": lambda: True,
                "Google Drive Desktop": lambda: False,
            },
            clear=True,
        ):
            self.assertEqual(
                installed_state(),
                {"Tailscale": True, "Google Drive Desktop": False},
            )


if __name__ == "__main__":
    unittest.main()
