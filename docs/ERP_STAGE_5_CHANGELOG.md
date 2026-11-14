# ERP Global Pro — Stage 5 Change Log

Date: 2026-11-13
Status: **Read-only uniqueness preflight utility implemented; no schema migration applied.**

## Changes

1. Added `backend/scripts/tenant_uniqueness_preflight.py`, a standard-library-only SQLite inspection tool. It does not require Flask.
2. The tool opens the requested database with SQLite `mode=ro` and sets `PRAGMA query_only=ON`.
3. It inspects the 15 proposed company-scoped business uniqueness rules documented in `ERP_STAGE_5_UNIQUENESS_PLAN.md`.
4. It reports duplicate groups within the same `company_id`, affected row counts, and sample SQLite rowids. Business key values are redacted by default and can be explicitly included with `--include-values`.
5. It respects the existing partial predicates for posted payroll runs, non-NULL product barcodes, and non-NULL trade-document numbers.
6. It reports missing tables/columns instead of assuming they exist.
7. It can write a JSON report and supports deterministic exit codes for duplicate and schema gates.
8. Added unit tests proving same-company duplicates are detected, cross-company equal values are not grouped together, partial predicates are respected, NULL keys are skipped, rows with missing `company_id` are reported, missing predicate columns are reported, and read-only access blocks writes.

## Validation

- Stage 5 preflight tests: 9/9 PASS.
- Stage 2–4 focused migration/service tests: 21/21 PASS.
- Combined focused tests: 30/30 PASS.
- `python -m compileall -q backend`: PASS.
- `node --check` for every frontend JavaScript file: PASS.
- Full Flask application suite: NOT RUN; Flask is not installed in the current environment.
- No production database was supplied, opened, or modified.

## Deliberately not changed

- `LATEST_VERSION` remains 4. Stage 5 does not change the database schema.
- No global UNIQUE constraints or primary keys have been dropped or rebuilt.
- No business key or production record has been altered.
- `TENANT_DATA_SCOPING_ENABLED` remains false.
- No GitHub repository, hosted database, or deployment was changed.

## Next gate

Run this preflight only against a verified restored copy of a real database. Manually resolve any duplicates; confirm all table dependencies, external references, collation behavior, partial predicates, triggers, and tenant relation guards. Only then design a separate, reversible migration for company-scoped unique keys.
