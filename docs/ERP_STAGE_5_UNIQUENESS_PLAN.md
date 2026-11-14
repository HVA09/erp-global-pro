# ERP Global Pro — Stage 5 Uniqueness Preflight Plan

Date: 2026-11-13
Status: **original inventory plan completed by Stage 10 / SQLite schema v6**

## Purpose

Stage 4 protects existing tenant-to-tenant references with SQLite triggers. Before enabling company-specific codes and identifiers, Stage 5 must convert globally unique business keys into tenant-scoped unique keys. This document records the unique constraints discovered in the original SQLite `schema.sql` by inspecting SQLite `PRAGMA index_list` / `PRAGMA index_info` after schema creation. It is not a report of duplicate values in production data; no production database was supplied or accessed.

## Business uniqueness keys to scope by company

| Table | Current unique key | Proposed company-scoped key | Notes |
|---|---|---|---|
| `accounts` | `code` | `(company_id, code)` | Preserve account code semantics within each company. |
| `attendance` | `(employee_id, date)` | `(company_id, employee_id, date)` | Keep one attendance row per employee/day in each company. |
| `categories` | `name` | `(company_id, name)` | Names may repeat between companies. |
| `cost_centers` | `name` | `(company_id, name)` | Company-local cost center namespace. |
| `departments` | `name` | `(company_id, name)` | Company-local organization structure. |
| `depreciation_postings` | `(asset_id, period)` | `(company_id, asset_id, period)` | Keep existing posting uniqueness. |
| `journal_entries` | `number` | `(company_id, number)` | Preserve numbering independently per company. |
| `payroll_runs` | `month` WHERE `status='posted'` | `(company_id, month)` with same predicate | Preserve the partial unique rule exactly. |
| `products` | `sku` | `(company_id, sku)` | Company-local SKU namespace. |
| `products` | `barcode` WHERE `barcode IS NOT NULL` | `(company_id, barcode)` with same predicate | Preserve the partial predicate. |
| `sequences` | `name` (primary key) | `(company_id, name)` | Requires rebuilding the table/primary key. |
| `settings` | `key` (primary key) | `(company_id, key)` | Requires rebuilding the table/primary key. |
| `stock_levels` | `(product_id, warehouse_id)` (primary key) | `(company_id, product_id, warehouse_id)` | Confirm no external references depend on the old key before rebuild. |
| `trade_documents` | `(doc_type, number)` WHERE `number IS NOT NULL` | `(company_id, doc_type, number)` with same predicate | Preserve partial predicate and document-type partition. |
| `warehouses` | `code` | `(company_id, code)` | Company-local warehouse code namespace. |

## Global identities intentionally not converted

- `users.email` remains globally unique because `users` is a global identity account, not a tenant-owned business table.
- `roles.name` is currently a shared role catalog. It needs a separate role-catalog design before custom roles can safely be company-specific; do not blindly add `company_id` to it.
- Surrogate integer primary keys such as `id` remain globally unique. They are identifiers, not human/business code namespaces.

## Safe implementation sequence

1. **Do not change schema yet.** Run a read-only duplicate/preflight report against a restored copy of the real database. The current evidence is from the source schema only.
2. Check all target tables for duplicate candidate keys within the same `company_id`. Report counts and sample row IDs. Abort on any duplicate; never silently alter SKU, account code, journal number, document number, department name, or other business identifiers.
3. Verify every parent/child relation and all indexes/triggers that depend on target tables. Stage 4's 55 relationship guards must be preserved or recreated after a table rebuild.
4. For tables with table-level `UNIQUE` constraints or primary keys (`settings`, `sequences`, `stock_levels`), create a deterministic rebuild migration with a temporary table, copy/validate, restore indexes/triggers, foreign-key checks, row-count comparison, and rollback path.
5. For standalone and partial unique indexes, drop/recreate them with `company_id` while preserving original partial predicates and collation semantics.
6. Add a migration manifest and checksums, plus preflight/dry-run commands; rehearse on a restored production-like backup.
7. Run duplicate-in-company and two-company tests. The key test is that Company A and Company B can use the same SKU/account code/document number while duplicate keys within the same company remain rejected.

## Release gate

No uniqueness migration is implemented by this document. Do not enable multi-company operations. Stage 5 implementation must be a separately tested checkpoint after reviewing real data and table dependencies.


## Implementation status — Stage 10 (13 November 2026)

The reviewed uniqueness migration is now implemented in `backend/erp/migrations/__init__.py` as v6. It converts the 15 listed candidate keys into company-scoped unique indexes or composite primary keys, after a fail-closed ownership/duplicate preflight. `sequences` and `stock_levels` now use tenant-inclusive primary keys; `settings` was already composite from v5. Global login email and shared role identity remain intentionally global.

Tests are in `backend/tests/test_tenant_stage10_uniqueness_migration.py`. This verifies synthetic two-company SQLite fixtures, not a real customer/production database. A production-like restored database still requires the read-only preflight tool and manual review before migration. Full Flask/HTTP tests and PostgreSQL/RLS are still outstanding.
