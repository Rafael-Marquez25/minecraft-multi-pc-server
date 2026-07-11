from pathlib import Path
import tempfile
import time
import unittest

from minecraft_multi_pc_server.archive import ordered_archives_newest_first, prune_old_archives


class ArchiveRetentionTests(unittest.TestCase):
    def test_prune_always_preserves_just_created_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_dir = Path(tmp)
            future_names = [
                "server-20990101-000003.zip",
                "server-20990101-000002.zip",
                "server-20990101-000001.zip",
            ]
            for name in future_names:
                (archive_dir / name).write_bytes(b"old")
            created = archive_dir / "server-20260711-120000.zip"
            created.write_bytes(b"new")

            prune_old_archives(archive_dir, keep=3, protected=created)

            self.assertTrue(created.exists())
            self.assertEqual(len(list(archive_dir.glob("*.zip"))), 3)
    def test_prune_old_archives_keeps_three_newest_by_name_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_dir = Path(tmp)
            for name in [
                "server-20260709-120000.zip",
                "server-20260709-130000.zip",
                "server-20260709-140000.zip",
                "server-20260709-150000.zip",
                "server-20260709-160000.zip",
            ]:
                (archive_dir / name).write_text(name, encoding="utf-8")

            removed = prune_old_archives(archive_dir, keep=3)

            self.assertEqual(
                sorted(path.name for path in archive_dir.glob("*.zip")),
                [
                    "server-20260709-140000.zip",
                    "server-20260709-150000.zip",
                    "server-20260709-160000.zip",
                ],
            )
            self.assertEqual(
                sorted(path.name for path in removed),
                ["server-20260709-120000.zip", "server-20260709-130000.zip"],
            )

    def test_ordered_archives_uses_mtime_when_name_has_no_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_dir = Path(tmp)
            old = archive_dir / "old.zip"
            new = archive_dir / "new.zip"
            old.write_text("old", encoding="utf-8")
            time.sleep(0.01)
            new.write_text("new", encoding="utf-8")

            ordered = ordered_archives_newest_first(archive_dir)

            self.assertEqual([path.name for path in ordered], ["new.zip", "old.zip"])


if __name__ == "__main__":
    unittest.main()
