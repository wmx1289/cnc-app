import sqlite3
import tempfile
import unittest
from pathlib import Path

from backup_manager import create_daily_backup, create_sqlite_backup


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
