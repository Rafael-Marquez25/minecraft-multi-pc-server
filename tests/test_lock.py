from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from minecraft_multi_pc_server.lock import LockError, ServerLock
from minecraft_multi_pc_server.state import write_json


class LockTests(unittest.TestCase):
    def test_acquire_refuses_existing_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            lock = ServerLock(state_dir, "pc-a", timedelta(minutes=30))
            lock.acquire()

            other = ServerLock(state_dir, "pc-b", timedelta(minutes=30))
            with self.assertRaises(LockError):
                other.acquire()

    def test_lock_persists_connection_address_and_reports_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            lock = ServerLock(
                state_dir,
                "pc-host",
                timedelta(minutes=30),
                connection_address="100.99.98.97:25565",
            )
            acquired = lock.acquire()

            self.assertEqual(acquired.connection_address, "100.99.98.97:25565")
            self.assertEqual(lock.read().connection_address, "100.99.98.97:25565")
            with self.assertRaisesRegex(LockError, "Connect to 100.99.98.97:25565"):
                ServerLock(state_dir, "pc-client", timedelta(minutes=30)).acquire()

    def test_old_lock_without_connection_address_remains_compatible(self):
        payload = {
            "owner": "old-pc",
            "pid": 1,
            "status": "running",
            "created_at": "2026-01-01T00:00:00+00:00",
            "heartbeat_at": "2026-01-01T00:00:00+00:00",
        }

        from minecraft_multi_pc_server.lock import LockInfo

        self.assertEqual(LockInfo.from_dict(payload).connection_address, "")

    def test_force_acquire_replaces_stale_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            write_json(
                state_dir / "lock.json",
                {
                    "owner": "pc-a",
                    "pid": 1,
                    "status": "running",
                    "created_at": "2020-01-01T00:00:00+00:00",
                    "heartbeat_at": "2020-01-01T00:00:00+00:00",
                },
            )
            lock = ServerLock(state_dir, "pc-b", timedelta(minutes=30))

            existing = lock.read()
            self.assertIsNotNone(existing)
            self.assertTrue(lock.is_stale(existing))

            lock.acquire(force=True)
            self.assertEqual(lock.read().owner, "pc-b")

    def test_heartbeat_updates_status_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = ServerLock(Path(tmp), "pc-a", timedelta(minutes=30))
            first = lock.acquire()
            lock.heartbeat()
            updated = lock.read()

        self.assertEqual(updated.owner, "pc-a")
        self.assertGreaterEqual(updated.heartbeat_at, first.heartbeat_at)

    def test_force_release_removes_other_owner_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            ServerLock(state_dir, "pc-a", timedelta(minutes=30)).acquire()
            ServerLock(state_dir, "pc-b", timedelta(minutes=30)).release(force=True)

            self.assertIsNone(ServerLock(state_dir, "pc-b", timedelta(minutes=30)).read())

    def test_corrupt_lock_is_reported_as_lock_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "lock.json").write_text("not json", encoding="utf-8")

            with self.assertRaisesRegex(LockError, "Cannot read state file"):
                ServerLock(state_dir, "pc-a", timedelta(minutes=30)).read()


if __name__ == "__main__":
    unittest.main()
