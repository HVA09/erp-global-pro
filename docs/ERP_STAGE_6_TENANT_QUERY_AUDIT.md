# ERP Global Pro — Stage 6 Tenant Query / API Scope Audit

**Date:** 2026-11-13
**Type:** Read-only source audit; no application code or database schema was changed.
**Purpose:** Identify the work required before operational APIs can safely serve more than one company.

## Executive result

The identity and active-company context exist, and the application has a fail-closed gate when more than one company exists. However, the operational query layer is not yet tenant-scoped. **Do not enable `TENANT_DATA_SCOPING_ENABLED` and do not operate multiple companies.**

The main issue is architectural, not a missing filter on one endpoint: the generic CRUD engine, resource definitions, bespoke API handlers, and business services still issue legacy single-company queries.

## Evidence reviewed

### 1. Generic CRUD engine — critical shared surface

File: `backend/erp/crud.py`

- `Resource.list_view()` builds SELECT/COUNT queries from `self.select`, filters, and optional `self.where`; it does not automatically add `company_id`.
- `Resource._get()` looks up a record using `id` and optional resource-specific `where`, not `company_id`.
- `Resource.create_view()` parses caller input and inserts the parsed fields; it does not inject the active company ID from trusted server context.
- `Resource.update_view()` and `delete_view()` target rows using `WHERE id=?` only.
- `F.parse()` for `type='ref'` validates a foreign key with `SELECT 1 FROM <ref> WHERE id=?`; it does not verify that the referenced row belongs to the active company.

The shared declarative registry in `backend/erp/resources.py` contains 21 business resources. It covers partners/customers/suppliers, products/categories/warehouses, accounts/cost centers, departments/employees/attendance/leave requests, projects/tasks/time logs, leads/opportunities/activities/campaigns, maintenance requests/schedules, and files. Fixing the shared engine is therefore a prerequisite, but it is not sufficient by itself because custom queries and callbacks also need review.

### 2. Business services — no explicit tenant filters found in the scan

A source scan of direct `db.q`, `db.one`, `db.scalar`, `db.execute`, and `db.run` call sites found the following call-site counts. These are static grep counts, not a semantic proof of every SQL statement:

| Service module | Matched DB call sites | `company_id` text references |
|---|---:|---:|
| `accounting.py` | 30 | 0 |
| `assets.py` | 9 | 0 |
| `hr.py` | 16 | 0 |
| `inventory.py` | 23 | 0 |
| `manufacturing.py` | 10 | 0 |
| `reports.py` | 16 | 0 |
| `sequences.py` | 3 | 0 |
| `settings.py` | 5 | 0 |
| `trade.py` | 50 | 0 |
| `users.py` | 2 | 0 |
| `tenancy.py` | 8 | 16 |

The `tenancy.py` references are expected because membership lookup and company selection are company-aware. The business modules above require line-by-line SQL review and explicit company scope; this scan does not claim all references are business rows (`users.py` and some settings may be global by design).

### 3. API handlers — bespoke paths also need tenant scope

A static scan found 68 direct database call sites across API modules. Review showed that most operational modules do not include explicit `company_id` predicates. High-priority paths include:

- `api/accounting.py`, `api/inventory.py`, `api/hr.py`, `api/trade.py`, and `api/ops.py`: custom operational actions and lookups.
- `api/data.py`: exports and imports. Match/update operations for products, partners, and categories currently use global SKU/name lookup patterns; all must be constrained to the active company.
- `api/admin.py`: membership-based user listing and membership assignment are company-scoped, but role catalog operations remain global by current design; the audit listing is not restricted by `audit_log.company_id`, and system counts are global.
- `api/auth.py`: company membership validation and active company switching are scoped; global user identity reads are intentional.

`audit.log()` writes `company_id` from the verified request context when available, but the audit listing route must apply a matching company predicate before it can safely be shown to a company administrator.

Two important service gaps were confirmed by direct source inspection:

- `services/sequences.py`: sequence lookup and increment use `WHERE name=?`; inserts do not set `company_id`. Per-company document numbering cannot work safely until this service and the global primary key on `sequences.name` are redesigned together.
- `services/settings.py`: `get_settings()` reads all rows, `save_settings()` upserts on the global `key`, and default-warehouse lookup is not tenant-scoped. The settings table key and all related lookups need a coordinated migration.

Two particularly important service gaps were confirmed by direct source inspection:

- `services/sequences.py`: both sequence lookup and increment use `WHERE name=?`; inserts do not set `company_id`. Per-company numbering therefore cannot work safely yet, and the current global primary key on `sequences.name` must be rebuilt before company-local sequences are possible.
- `services/settings.py`: `get_settings()` reads all rows, `save_settings()` upserts on the global `key`, and default-warehouse lookup is not tenant-scoped. The settings table primary key and all service lookups need a coordinated migration.

### 4. Existing safety gate

File: `backend/erp/__init__.py`

- The request guard requires an authenticated user and, for operational API requests, an active company.
- When the companies table contains more than one company and `TENANT_DATA_SCOPING_ENABLED` is false, operational requests return `503 tenant_isolation_pending`.
- Company-context paths remain available for listing memberships and switching companies.
- `backend/erp/config.py` sets `TENANT_DATA_SCOPING_ENABLED = False`.

This gate is a temporary fail-closed control. It does not itself provide tenant isolation. Enabling the flag now would expose legacy global queries to cross-company access.

## Required implementation sequence

1. **Define one trusted context contract:** request code obtains `company_id` only from the verified server-side membership in `g.company`; reject missing context for tenant-owned operations. Never accept ownership from the request body or an unverified header.
2. **Harden generic CRUD first:** automatically scope list/count/get/update/delete by `company_id`; force `company_id` from server context on create; reject client attempts to set or change it; scope ref-field validation to the same company; ensure custom SELECT joins and correlated subqueries are tenant-safe.
3. **Harden resource callbacks and calculations:** inspect every `before_save`, `after_save`, `before_delete`, derived balance/stock/cost subquery, and related-row count. Pass `company_id` explicitly where a service method is callable outside HTTP requests.
4. **Scope bespoke APIs/services:** accounting, inventory, trade, HR, manufacturing, CRM, maintenance, projects, reports, sequence generation, settings, ZATCA, imports/exports, uploads/downloads, and admin audit paths.
5. **Scope background and indirect channels:** scheduled jobs, async tasks, cache keys, files, backup/export jobs, logs, notifications, and all external integrations.
6. **Add two-company tests before setting the feature flag:** create Company A and Company B with overlapping business values; assert each can only read/update/delete its own rows and that cross-company references, IDs, bulk operations, reports, and exports are rejected or hidden.
7. Only after the entire matrix passes should `TENANT_DATA_SCOPING_ENABLED` be considered for change. That decision requires a separately reviewed release gate.

## Required negative test matrix

For each tenant-owned resource and each custom API operation, test:

- Company A cannot list or count Company B's records.
- Company A cannot GET, PUT/PATCH, or DELETE a Company B record by guessing its integer ID.
- Company A cannot create a child record referencing a Company B parent.
- Company A cannot change a record's `company_id` through JSON, import, or direct field mapping.
- A company export, report, backup view, dashboard total, or audit listing contains no rows from another company.
- Equal SKU, account code, partner name, category name, warehouse code, and document numbers are permitted across different companies only after the unique-key migration is approved; duplicates inside one company remain rejected.
- Company switching invalidates previous tenant-scoped assumptions, caches, and background jobs.

## Priority order from the audit

| Priority | Surface | Reason |
|---|---|---|
| P0 | `crud.py` and `resources.py` | Shared list/get/create/update/delete and reference-validation surface for 21 resources. |
| P0 | `settings.py`, `sequences.py`, `audit_list` | Tenant-local preferences, document numbering, and audit visibility. |
| P0 | Accounting/inventory/trade/HR/manufacturing services | High-impact financial and stock data; every query and relation needs company context. |
| P1 | Import/export, reports, dashboards, file access | Bulk and indirect paths often bypass ordinary resource routes. |
| P1 | Jobs, caches, integrations, notifications | Tenant context must be carried outside HTTP request scope. |
| P2 | Global role catalog and system administration | Decide deliberately whether roles are global templates or company-owned roles. |

## Stage status

- This is an audit and implementation plan only.
- No API or service query was modified by Stage 6.
- No migration version change was made; `LATEST_VERSION` remains 4.
- `TENANT_DATA_SCOPING_ENABLED` remains false.
- The existing 30 focused tests and 18 frontend syntax checks do not yet cover the full matrix above.
- Full Flask tests, browser E2E, PostgreSQL/RLS, and production rehearsal remain outstanding.
