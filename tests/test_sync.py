from pathlib import Path
import tempfile
import unittest

from minecraft_multi_pc_server.sync import Syncer


class SyncTests(unittest.TestCase):
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
