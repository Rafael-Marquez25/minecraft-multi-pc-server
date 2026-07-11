from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from minecraft_multi_pc_server.sync import SyncError, Syncer


class SyncTests(unittest.TestCase):
    def test_extract_rejects_parent_traversal_and_preserves_existing_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "bad.zip"
            destination = root / "server"
            destination.mkdir()
            (destination / "world.dat").write_text("safe", encoding="utf-8")
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../outside.txt", "bad")

            with self.assertRaisesRegex(SyncError, "Unsafe archive member"):
                Syncer(prefer_robocopy=False).extract_archive(archive_path, destination)

            self.assertEqual((destination / "world.dat").read_text(encoding="utf-8"), "safe")
            self.assertFalse((root / "outside.txt").exists())

    def test_extract_restores_existing_local_when_final_replace_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "server.zip"
            destination = root / "server"
            destination.mkdir()
            (destination / "world.dat").write_text("old", encoding="utf-8")
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("world.dat", "new")

            original_replace = Path.replace

            def fail_temp_replace(path: Path, target: Path):
                if path.parent == root and path.name.startswith("tmp") and target == destination:
                    raise OSError("simulated replace failure")
                return original_replace(path, target)

            with patch.object(Path, "replace", autospec=True, side_effect=fail_temp_replace):
                with self.assertRaisesRegex(SyncError, "simulated replace failure"):
                    Syncer(prefer_robocopy=False).extract_archive(archive_path, destination)

            self.assertEqual((destination / "world.dat").read_text(encoding="utf-8"), "old")

    def test_create_archive_refuses_empty_source_and_leaves_no_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "empty"
            destination = root / "server.zip"
            source.mkdir()

            with self.assertRaisesRegex(SyncError, "empty server archive"):
                Syncer(prefer_robocopy=False).create_archive(source, destination)

            self.assertFalse(destination.exists())

    def test_python_mirror_copies_updates_and_deletes_extra_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "world").mkdir()
            (source / "world" / "level.dat").write_text("new", encoding="utf-8")
            (destination / "old.txt").write_text("old", encoding="utf-8")

            result = Syncer(prefer_robocopy=False).mirror(source, destination)

            self.assertEqual(result.backend, "python")
            self.assertEqual((destination / "world" / "level.dat").read_text(encoding="utf-8"), "new")
            self.assertFalse((destination / "old.txt").exists())

    def test_python_mirror_keeps_ignored_destination_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "keep.txt").write_text("keep", encoding="utf-8")
            (destination / "debug.tmp").write_text("ignore", encoding="utf-8")

            Syncer(["*.tmp"], prefer_robocopy=False).mirror(source, destination)

            self.assertTrue((destination / "debug.tmp").exists())
            self.assertEqual((destination / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_python_mirror_matches_nested_ignore_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            destination = root / "destination"
            (source / "logs").mkdir(parents=True)
            destination.mkdir()
            (source / "logs" / "latest.log").write_text("skip me", encoding="utf-8")
            (source / "logs" / "archive.log").write_text("copy me", encoding="utf-8")

            Syncer(["logs/latest.log"], prefer_robocopy=False).mirror(source, destination)

            self.assertFalse((destination / "logs" / "latest.log").exists())
            self.assertEqual((destination / "logs" / "archive.log").read_text(encoding="utf-8"), "copy me")


if __name__ == "__main__":
    unittest.main()
