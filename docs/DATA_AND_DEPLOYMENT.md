# CNC App data and deployment configuration

## Data directory

Set `CNC_DATA_DIR` to a persistent, writable directory on each installation.
The app stores its SQLite database, backups, uploads, and local knowledge under
this directory. Existing installations keep their historical paths when this
setting is omitted; no automatic relocation or deletion is performed.

Examples:

- macOS/Linux local machine: `CNC_DATA_DIR=/Users/Shared/GSE-CNC-Data`
- Windows: set `CNC_DATA_DIR` to a durable directory such as
  `D:\\GSE-CNC-Data`
- Docker: mount a persistent volume at `/var/lib/cnc-app` and set
  `CNC_DATA_DIR=/var/lib/cnc-app`

## Important: multiple installations

A local SQLite file is not a multi-device database. Do not put the same SQLite
file on a network share, Dropbox, OneDrive, or Google Drive and open it
simultaneously from multiple computers.

For Streamlit Cloud plus office PCs plus Macs to share live records, deploy one
central database service (recommended: managed PostgreSQL such as Supabase) and
connect every app instance to that same service. The current application still
uses SQLite directly in many UI workflows, so setting Supabase credentials alone
does not activate shared-database mode. A schema-preserving repository/service
migration is a separate step and must be tested against a copy of the production
database before switching writes.

Until that migration is complete:
- designate one installation as the writer;
- use automatic and manual snapshots;
- download backups to a separate, access-controlled storage location;
- do not merge independently edited SQLite files by copying one over another.

## Streamlit Community Cloud limitation

Streamlit Community Cloud does not provide a durable local disk volume for app
data. Setting `CNC_DATA_DIR` there only changes the temporary container path; it
does not make that path persistent across rebuilds or restarts. The warning in
the app is intentional. For production use with Cloud and office computers,
the central managed PostgreSQL backend must be completed before treating Cloud
as a writable source of truth. Until then, do not rely on Cloud-local writes for
records that must survive a restart.

## Backups

The app creates a daily SQLite online-backup snapshot before schema setup and
legacy JSON migration. Manual snapshots are also available in the backup tab.
Snapshots are integrity-checked before being made visible. Automatic snapshots
are retained for 30 days; manual snapshots are not pruned automatically.

Restore procedure:
1. Stop the app on every machine that can write to the database.
2. Copy the selected verified snapshot to a safe staging location.
3. Verify it with `python -c "import sqlite3; c=sqlite3.connect('snapshot.db'); print(c.execute('PRAGMA integrity_check').fetchone())"`.
4. Preserve the current database under a new filename before replacing it.
5. Restore the snapshot and verify record counts and key screens before resuming writes.

Never test a restore against the only copy of live data.
