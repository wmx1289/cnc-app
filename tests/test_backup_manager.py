import sqlite3
import tempfile
import unittest
from pathlib import Path

from backup_manager import create_daily_backup, create_sqlite_backup, sqlite_backup_bytes


class BackupManagerTests(unittest.TestCase):
    def test_backup_contains_consistent_database_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            with sqlite3.connect(source) as connection:
                connection.execute("CREATE TABLE ledger (id INTEGER PRIMARY KEY, amount INTEGER)")
                connection.execute("INSERT INTO ledger(amount) VALUES (12500)")

            backup = create_sqlite_backup(source, root / "backups", prefix="test")
            with sqlite3.connect(backup) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("SELECT amount FROM ledger").fetchone()[0], 12500)

    def test_in_memory_export_is_a_valid_database(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.db"
            with sqlite3.connect(source) as connection:
                connection.execute("CREATE TABLE notes (body TEXT)")
                connection.execute("INSERT INTO notes VALUES ('preserve me')")

            exported = sqlite_backup_bytes(source)
            restored = Path(directory) / "export.db"
            restored.write_bytes(exported)
            with sqlite3.connect(restored) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("SELECT body FROM notes").fetchone()[0], "preserve me")

    def test_daily_backup_is_idempotent_for_same_day(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            with sqlite3.connect(source) as connection:
                connection.execute("CREATE TABLE ledger (id INTEGER)")
                connection.execute("INSERT INTO ledger VALUES (1)")

            first = create_daily_backup(source, root / "backups")
            second = create_daily_backup(source, root / "backups")
            self.assertEqual(first, second)
            self.assertTrue(first.exists())


if __name__ == "__main__":
    unittest.main()
