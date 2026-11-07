import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from legacy_migrations import migrate_legacy_json


def create_schema(connection):
    connection.executescript("""
        CREATE TABLE tool_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT, quote_date TEXT, company TEXT,
            category TEXT, part_name TEXT, unit_price INTEGER, quantity INTEGER, total_price INTEGER
        );
        CREATE TABLE setup_sheets (
            id TEXT PRIMARY KEY, quote_date TEXT, part_name TEXT, machine TEXT, category TEXT,
            material TEXT, g_code_coord TEXT, tool_info TEXT, knowhow TEXT, images_b64 TEXT
        );
        CREATE TABLE gcodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, machine TEXT, code TEXT, description TEXT
        );
        CREATE TABLE memos (id TEXT PRIMARY KEY, title TEXT, content TEXT, date TEXT);
    """)


class LegacyMigrationTests(unittest.TestCase):
    def test_ledger_is_imported_and_archived_after_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "공구_장부.json"
            source.write_text(json.dumps([{
                "일자": "2026-01-02", "거래처": "테스트", "분류": "공구",
                "품목명": "엔드밀", "단가": 12000, "수량": 2, "총액": 24000
            }], ensure_ascii=False), encoding="utf-8")
            connection = sqlite3.connect(root / "app.db")
            create_schema(connection)

            self.assertEqual(migrate_legacy_json(connection, root), [])
            self.assertFalse(source.exists())
            self.assertTrue((root / "공구_장부.json.backup").exists())
            self.assertEqual(connection.execute("SELECT total_price FROM tool_ledger").fetchone()[0], 24000)
            connection.close()

    def test_invalid_source_is_not_archived_or_partially_imported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "공구_장부.json"
            source.write_text('[{"일자":"2026-01-01"}, "bad row"]', encoding="utf-8")
            connection = sqlite3.connect(root / "app.db")
            create_schema(connection)

            errors = migrate_legacy_json(connection, root)
            self.assertEqual(len(errors), 1)
            self.assertTrue(source.exists())
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tool_ledger").fetchone()[0], 0)
            connection.close()

    def test_same_legacy_source_is_not_imported_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "공구_장부.json"
            source.write_text('[{"품목명":"드릴","단가":100,"수량":1,"총액":100}]', encoding="utf-8")
            connection = sqlite3.connect(root / "app.db")
            create_schema(connection)
            self.assertEqual(migrate_legacy_json(connection, root), [])
            # Simulate a failed archive operation by restoring the identical source.
            (root / "공구_장부.json.backup").replace(source)
            self.assertEqual(migrate_legacy_json(connection, root), [])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tool_ledger").fetchone()[0], 1)
            connection.close()


if __name__ == "__main__":
    unittest.main()
