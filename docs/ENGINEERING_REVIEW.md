# CNC App Engineering Review

## Scope and safety rules

This review covers the Streamlit CNC operations app. Changes are made on
`improve/architecture-and-data-safety`; the default branch is not modified.
No database, uploaded file, backup, or historical JSON data is migrated or deleted
by this branch.

## Findings

### High priority

1. **Data durability is not guaranteed on Streamlit Community Cloud.**
   The current cloud path is `/tmp/cnc-app`. Temporary storage can be discarded
   when the app restarts or is redeployed. The app also stores the SQLite database,
   inbox, and backups under that directory. Before production use, configure a
   durable mounted volume or move records and attachments to a managed persistent
   store. Do not assume the repository filesystem is writable.

2. **Legacy JSON migration can hide failures.**
   The migration loop catches broad exceptions and silently continues. This makes
   it difficult to know whether all rows were imported. The next phase should add
   a migration ledger, transaction boundaries, row counts, and a downloadable
   error report. Existing JSON files should only be renamed after verified success.

3. **Receipt extraction is heuristic, not accounting-grade.**
   Selecting the largest comma-formatted number as the unit price can mistake a
   subtotal, tax, or total for an item price. Keep extracted values in a review
   state until a person confirms them; never auto-finalize financial records from
   OCR alone.

### Medium priority

4. **The application is concentrated in one large `app.py`.**
   Separate configuration, database access, migrations, receipt parsing, Ollama
   integration, and Streamlit pages into modules. Do this incrementally, keeping
   the current page labels and workflows stable.

5. **Runtime settings are hard-coded.**
   The Ollama URL, model name, context size, and generation limits should be
   environment-configurable, with safe defaults and a connection/model health check.

6. **Uploads need collision and path-safety protection.**
   The current branch now generates a UUID filename and validates the extension
   before writing an upload. A follow-up should enforce file-size limits and
   clean temporary uploads after processing.

7. **Automated tests are missing for the business-critical logic.**
   Add tests for amount calculations, date parsing, JSON migrations, read-only
   backup mode, receipt parsing, and duplicate imports before deeper refactoring.

## Changes made in this branch

- Fixed PDF page text concatenation so a newline is always appended after each page.
- Replaced the user-supplied upload filename with a UUID filename and allow-listed
  extension for receipt uploads.

## Recommended implementation sequence

1. Add tests and migration diagnostics without changing the schema.
2. Make storage location explicit through a configuration setting; document the
   ephemeral nature of temporary cloud storage.
3. Extract pure receipt parsing and calculation functions into tested modules.
4. Split database and migration code from Streamlit presentation.
5. Add a persistent storage adapter only after the deployment target is confirmed.
6. Add health checks and configurable Ollama connection/model settings.

## Validation status

The edits above were reviewed against the source, but this GitHub-only workflow
does not provide the user's deployed database, secrets, OCR binaries, or runtime.
Run the app and regression tests in a staging environment with a copy of the
database before merging. Do not test migrations against the only production copy.
