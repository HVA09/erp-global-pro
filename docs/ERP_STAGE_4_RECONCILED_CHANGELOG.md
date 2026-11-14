# ERP Global Pro — Stage 4 Reconciled Change Log

Date: 2026-11-13
Migration version: 4
Base: unarchived Stage 4 working tree found separately from the Stage 3 ZIP; no original archive changes.

## Provenance and reconciliation

The separate `erp-global-pro-stage4.zip` from the earlier conversation was not available in the Library. Inspection distinguished two artifacts: the previously delivered `erp-global-pro-stage3-rebuilt.zip` contains migration version 3 only, while a separate unarchived working tree in the active workspace contained migration version 4 but lacked a Stage 4 test file and current Stage 4 documentation. Stage 4 Reconciled continues from that unarchived v4 working tree, adds dedicated Stage 4 regression tests, corrects the three Stage 3 assertions to pin `target_version=3`, and records the actual behavior below. This is a verified reconciliation checkpoint, not a claim that the old Stage 4 ZIP was recovered byte-for-byte.

## Implemented and inspected

1. Migration version 4 enumerates the tenant-to-tenant foreign-key relationships from the real SQLite schema.
2. The expected relationship-group count is pinned at 55. Schema drift causes a fail-closed migration error instead of silently omitting a relationship.
3. The migration scans for existing cross-company references before adding guards. A detected mismatch aborts and rolls back the migration.
4. For each of the 55 relationships, it creates a child-side index on `(company_id, foreign_key_columns)`.
5. For each relationship, it creates `BEFORE INSERT` and `BEFORE UPDATE` guards. Total: 110 relationship triggers.
6. It creates the audit-log `(company_id, ts)` index. Thus Stage 4 adds 56 indexes in total: 55 relationship indexes plus one audit index.
7. The migration verifies the expected counts and runs `PRAGMA foreign_key_check` before committing.
8. The earlier fail-closed multi-company write gates remain in place. The implementation does not enable multi-company operations.

## Important technical qualification

SQLite does not add composite foreign-key constraints to existing tables using a simple `ALTER TABLE`. Stage 4 therefore uses database triggers to reject cross-company child-to-parent references and supporting indexes. These guards are useful defense in depth, but they are **not the same thing as actual composite foreign-key constraints**. A later PostgreSQL migration should implement real `(company_id, parent_id)` references against matching tenant-aware unique keys, plus RLS. The application remains fail-closed for multiple companies until every read/write/query path is tenant-scoped and tested.

## Regression test additions

New file: `backend/tests/test_tenant_relationship_migration.py`.

It covers:

- Exact 55 relationship, 110 trigger and 55 relationship-index counts.
- Presence of the audit timestamp index.
- Same-company reference acceptance.
- Cross-company reference rejection on INSERT.
- Cross-company reference rejection on UPDATE.
- Pre-existing cross-company reference detection and transaction rollback.
- Migration idempotency.

The prior Stage 3 tests now explicitly call `apply_migrations(..., target_version=3)` so they test Stage 3 rather than the current latest version. This corrects three stale assertions that began failing after migration version 4 was present.

## Validation run on 2026-11-13

| Check | Result |
|---|---|
| Stage 2 identity migration tests | PASS |
| Membership/service tests | PASS |
| Stage 3 ownership migration tests | PASS |
| Stage 4 relationship migration tests | PASS |
| Combined focused tests | PASS — 21 tests |
| Python compile check | PASS — recorded in `ERP_STAGE_4_VALIDATION.txt` |
| Frontend JavaScript syntax | PASS — recorded in `ERP_STAGE_4_VALIDATION.txt` |
| Full Flask application suite | NOT RUN — Flask is not installed in the current execution environment |
| Browser/E2E | NOT RUN |
| PostgreSQL/RLS | NOT IMPLEMENTED |

## Stage 5 preparation included (planning only)

The package includes `ERP_STAGE_5_UNIQUENESS_PLAN.md`, a read-only inventory of 15 business uniqueness rules discovered in the source SQLite schema and their proposed company-scoped keys. No Stage 5 schema migration or production-data duplicate scan has been performed.

## Release decision

Stage 4 is a database-relationship protection checkpoint, not a multi-company release. Keep `TENANT_DATA_SCOPING_ENABLED=False`. Do not run actual multi-company business operations or deploy as a multi-tenant production ERP.
