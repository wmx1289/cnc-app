"""Idempotent, per-file migrations from legacy CNC JSON stores into SQLite."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _archive_source(path: Path) -> Path:
    candidate = path.with_name(path.name + ".backup")
    index = 1
    while candidate.exists():
        candidate = path.with_name(path.name + f".backup.{index}")
        index += 1
    os.replace(path, candidate)
    return candidate


def _already_migrated(connection: sqlite3.Connection, name: str, digest: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM migration_log WHERE source_name = ? AND sha256 = ?",
        (name, digest),
    ).fetchone()
    return row is not None


def _record_migration(
    connection: sqlite3.Connection, name: str, digest: str, row_count: int
) -> None:
    connection.execute(
        """INSERT INTO migration_log (source_name, sha256, row_count, migrated_at)
           VALUES (?, ?, ?, ?)""",
        (name, digest, row_count, datetime.now(timezone.utc).isoformat()),
    )


def _read_json(path: Path) -> tuple[Any, str]:
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def migrate_legacy_json(connection: sqlite3.Connection, base_dir: str | Path) -> list[str]:
    """Migrate each source atomically; archive only after its DB transaction commits.

    Errors are returned to the caller for display/logging. Failed sources remain in
    place for a later retry and do not prevent other sources from being processed.
    """
    root = Path(base_dir)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS migration_log (
            source_name TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            migrated_at TEXT NOT NULL,
            PRIMARY KEY (source_name, sha256)
        )"""
    )
    connection.commit()
    errors: list[str] = []

    for source in sorted(root.glob("*_장부.json")):
        savepoint = "legacy_ledger"
        try:
            data, digest = _read_json(source)
            if not isinstance(data, list):
                raise ValueError("legacy ledger must contain a JSON array")
            if _already_migrated(connection, source.name, digest):
                _archive_source(source)
                continue

            connection.execute(f"SAVEPOINT {savepoint}")
            for item in data:
                if not isinstance(item, dict):
                    raise ValueError("ledger rows must be JSON objects")
                connection.execute(
                    """INSERT INTO tool_ledger
                       (quote_date, company, category, part_name, unit_price, quantity, total_price)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(item.get("일자", "")),
                        str(item.get("거래처", "")),
                        str(item.get("분류", "미분류")),
                        str(item.get("품목명", "")),
                        int(item.get("단가", 0) or 0),
                        int(item.get("수량", 0) or 0),
                        int(item.get("총액", 0) or 0),
                    ),
                )
            _record_migration(connection, source.name, digest, len(data))
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            connection.commit()
            _archive_source(source)
        except Exception as error:
            try:
                connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
            errors.append(f"{source.name}: {error}")

    machining_source = root / "machining_db.json"
    if machining_source.exists():
        savepoint = "legacy_machining"
        try:
            data, digest = _read_json(machining_source)
            if not isinstance(data, dict):
                raise ValueError("machining database must contain a JSON object")
            if _already_migrated(connection, machining_source.name, digest):
                _archive_source(machining_source)
                return errors

            connection.execute(f"SAVEPOINT {savepoint}")
            setup_sheets = data.get("setup_sheets", [])
            gcodes = data.get("gcodes", [])
            memos = data.get("memos", [])
            if not all(isinstance(rows, list) for rows in (setup_sheets, gcodes, memos)):
                raise ValueError("setup_sheets, gcodes, and memos must be arrays")

            for sheet in setup_sheets:
                if not isinstance(sheet, dict):
                    raise ValueError("setup sheet rows must be JSON objects")
                images = sheet.get("images_b64", [])
                if sheet.get("image_b64") and not images:
                    images = [sheet.get("image_b64")]
                date_value = sheet.get("date", "")
                date_value = date_value.split(" ")[0] if date_value else datetime.now().strftime("%Y-%m-%d")
                connection.execute(
                    """INSERT OR IGNORE INTO setup_sheets
                       (id, quote_date, part_name, machine, category, material,
                        g_code_coord, tool_info, knowhow, images_b64)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(sheet.get("id", "")), date_value, str(sheet.get("part_name", "")),
                        str(sheet.get("machine", "")), str(sheet.get("category", "방산/일반")),
                        str(sheet.get("material", "")), str(sheet.get("g_code_coord", "")),
                        str(sheet.get("tool_info", "")), str(sheet.get("knowhow", "")),
                        json.dumps(images, ensure_ascii=False),
                    ),
                )
            for item in gcodes:
                if not isinstance(item, dict):
                    raise ValueError("G-code rows must be JSON objects")
                connection.execute(
                    "INSERT INTO gcodes (title, machine, code, description) VALUES (?, ?, ?, ?)",
                    (str(item.get("title", "")), str(item.get("machine", "")),
                     str(item.get("code", "")), str(item.get("description", ""))),
                )
            for item in memos:
                if not isinstance(item, dict):
                    raise ValueError("memo rows must be JSON objects")
                connection.execute(
                    """INSERT OR IGNORE INTO memos (id, title, content, date)
                       VALUES (?, ?, ?, ?)""",
                    (str(item.get("id", "")), str(item.get("title", "")),
                     str(item.get("content", "")), str(item.get("date", ""))),
                )

            _record_migration(
                connection, machining_source.name, digest,
                len(setup_sheets) + len(gcodes) + len(memos),
            )
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            connection.commit()
            _archive_source(machining_source)
        except Exception as error:
            try:
                connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
            errors.append(f"{machining_source.name}: {error}")

    return errors
