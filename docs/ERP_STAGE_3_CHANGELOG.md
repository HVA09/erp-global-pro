# ERP Global Pro — Stage 3 Change Log (Rebuilt and Verified)

Date: 2026-11-13
Migration version: 3
Base: archived `erp-global-pro-stage2.zip` (SHA-256 recorded in `ERP_RECOVERY_NOTE.md`)

## Why this package is labeled rebuilt

The Library recovery confirmed the original archive, Stage 1, Stage 2 and the architecture edition. A separate prior Stage 3/4 ZIP was not recoverable, so this worktree was rebuilt from the actual archived Stage 2 source. During the later Stage 4 reconciliation, inspection found that the recovered worktree's migration module also contained version-4 relationship-guard code while this Stage 3 changelog/tests still described version 3 only. The dedicated `ERP_STAGE_4_RECONCILED_CHANGELOG.md` and `ERP_STAGE_4_VALIDATION.txt` now govern the v4 behavior and validation; this file documents the v3 ownership milestone only.

## Implemented

1. Added schema migration version 3.
2. Added required `company_id` foreign-key columns to 40 tenant-owned business tables:
   `accounts`, `activities`, `advances`, `attendance`, `bom_components`, `boms`, `campaigns`, `categories`, `cost_centers`, `credit_applications`, `departments`, `depreciation_postings`, `employees`, `files`, `fixed_assets`, `journal_entries`, `journal_lines`, `leads`, `leave_requests`, `maintenance_requests`, `maintenance_schedules`, `opportunities`, `partners`, `payments`, `payroll_items`, `payroll_runs`, `production_orders`, `products`, `projects`, `sequences`, `settings`, `stock_count_lines`, `stock_counts`, `stock_levels`, `stock_moves`, `tasks`, `time_logs`, `trade_document_lines`, `trade_documents`, `warehouses`.
3. Added nullable `company_id` to `audit_log`, allowing company-scoped business events and global authentication/system events.
4. Backfilled existing business records to the single bootstrap company. Fresh databases receive one bootstrap company before initial base data and first-run setup.
5. Added a company index to every table with `company_id`.
6. Added triggers preventing tenant reassignment and blocking insert/update/delete on tenant-owned business tables when more than one company exists.
7. Kept the API-level `tenant_isolation_pending` gate; the database triggers are defense-in-depth, not a replacement for application scoping.
8. Updated first-run setup to reuse the bootstrap company instead of creating a second company; base tenant seeds are skipped when there are multiple companies.
9. Updated audit logging to include the active company where one is present.
10. Added standard-library SQLite tests for the v3 migration, ownership, safe refusal, and multi-company write blocking.

## Validation

| Check | Result |
|---|---|
| Existing identity migration tests | PASS |
| Active membership service tests | PASS |
| Stage 3 ownership migration tests | PASS — 7/7 |
| Combined focused unit tests | PASS — 16/16 |
| Python compile check | PASS |
| Frontend JavaScript syntax check | PASS — 19 files checked with `node --check` |
| Full Flask backend suite | NOT RUN — Flask is not installed in this execution environment |
| Browser/E2E test | NOT RUN |

## Explicitly not complete

- No tenant-scoped unique constraints yet (`SKU`, account code, document number, setting key, sequence names, etc.).
- No complete composite foreign keys to prove child and parent belong to the same company.
- Legacy service queries, API reads/writes, reports, import/export, files, background work and caches have not all been converted to explicit company scope.
- SQLite is still the only database adapter. PostgreSQL and RLS are not implemented.
- The transitional `DEFAULT bootstrap_company_id` exists only to preserve single-company compatibility and must be replaced by explicit active-company writes before the operational guard is removed.

**Release decision:** suitable only as a development/migration checkpoint for the existing single-company app. Not approved for multi-company production. Keep `TENANT_DATA_SCOPING_ENABLED=False`.
