"""Safe SQLite snapshot helpers for the CNC app.

Snapshots use SQLite's online backup API (not a raw file copy), are written to a
temporary file, checked with PRAGMA integrity_check, and atomically renamed.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def create_sqlite_backup(
    source_path: str | Path,
    backup_dir: str | Path,
    *,
    prefix: str = "manual",
    label: Optional[str] = None,
) -> Path:
    source = Path(source_path)
    destination_dir = Path(backup_dir)
    if not source.is_file():
        raise FileNotFoundError(f"Database does not exist: {source}")

    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_label = ""
    if label:
        safe_label = "_" + "".join(
            char if char.isalnum() or char in "-_" else "_" for char in label
        ).strip("_")[:48]
    filename = f"{prefix}_{timestamp}{safe_label}.db"
    destination = destination_dir / filename
    temporary = destination.with_suffix(".db.tmp")

    try:
        with sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True, timeout=30) as src:
            with sqlite3.connect(str(temporary), timeout=30) as dst:
                src.backup(dst)
                result = dst.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    raise sqlite3.DatabaseError(
                        f"Backup integrity check failed: {result[0] if result else 'no result'}"
                    )
                dst.commit()
        os.replace(temporary, destination)
        return destination
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def create_daily_backup(
    source_path: str | Path,
    backup_dir: str | Path,
    *,
    retention: int = 30,
) -> Optional[Path]:
    source = Path(source_path)
    destination_dir = Path(backup_dir)
    if not source.is_file() or source.stat().st_size == 0:
        return None

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    existing = sorted(destination_dir.glob(f"auto_{today}T*.db")) if destination_dir.exists() else []
    if existing:
        return existing[-1]

    created = create_sqlite_backup(source, destination_dir, prefix="auto")
    automatic = sorted(destination_dir.glob("auto_*.db"), key=lambda p: p.name, reverse=True)
    for old_backup in automatic[max(retention, 0):]:
        old_backup.unlink(missing_ok=True)
    return created
