# ERP Global Pro — Stage 14 Change Log

**Date:** 13 November 2026
**Base:** Stage 13 (`erp-global-pro-stage13-crm-maintenance-projects-scope.zip`)
**SQLite schema:** v6 (`LATEST_VERSION = 6`; no schema migration in this stage)

## Scope delivered

### 1. Reports and dashboard

- `services/reports.py` resolves an explicit trusted company context for every report query.
- Sales totals, grouped sales, top products, top customers, profitability, forecast, reorder suggestions, and dashboard metrics all filter by `company_id`.
- Joins from documents/lines to partners/products/accounts require the same `company_id` on both sides.
- Dashboard purchases, receivables, payables, cash, recent documents, low-stock data, lot alerts, leave counts, and maintenance alerts are scoped to the active company.
- `api/reports.py` passes the server-authenticated active company to each report service and to partner statements/ageing.

### 2. Import and export

- Every export in `api/data.py` receives the trusted active company ID and includes tenant predicates. The product stock subquery and all related joins are company-consistent.
- Partner imports look up existing names inside the active company only; inserts are stamped with server-side `company_id`; updates include both row ID and company ID.
- Product imports scope SKU lookup, category lookup/creation, and update statements by company. New products are stamped with server-side ownership.
- Client-supplied company IDs are not used for import ownership.
- Spreadsheet formula-injection neutralization and existing CSV/XLSX limits remain in place.

### 3. Audit and administration

- `/api/audit` always begins with `company_id=?`; filtering/search/pagination cannot widen the scope. Global events with NULL company ownership are intentionally not displayed as tenant events.
- `/api/system/info` reports company-scoped row counts (including membership count for users), not global tenant totals; global DB file size and demo-data eligibility are hidden when more than one company exists.
- SQLite native backup copies the whole database. The endpoint now rejects backup while more than one company record exists, including suspended companies, until a tenant-filtered backup format is implemented.
- Demo seeding and role-definition mutations are disabled while multiple companies exist because these are still global-schema operations. The CLI demo-seed command applies the same single-company check.
- Company administrators cannot change name, activation, or password for a user with multiple company memberships; those are global identity fields requiring platform-admin handling. Company membership role assignment remains company-scoped.

## Tests added

`backend/tests/test_tenant_stage14_reporting_data_audit_scope.py` adds 15 tests using actual SQLite queries and narrowly scoped Flask-compatible stubs:

- report aggregation and dashboard isolation for two companies;
- cross-company reference joins in reports;
- every CSV/XLSX export query constrained to the active company;
- partner import cannot update another company's same-name partner;
- product import permits the same SKU and category name across companies without altering the foreign company's rows;
- audit filtering excludes foreign and global events;
- multi-company backup, demo seed and global role mutations fail closed.

These are service/handler-level tests, not full Flask HTTP, browser, or production-database tests.

## Deliberately not delivered

- No schema migration; SQLite remains v6.
- No actual file upload/download storage API was added. The `files` resource remains tenant-scoped generic CRUD metadata/links only.
- No background job framework, notifications, or cross-process cache was introduced.
- PostgreSQL, composite foreign keys, and PostgreSQL RLS are not implemented.
- No change to `TENANT_DATA_SCOPING_ENABLED=False`.
- No deployment, production DB access, GitHub repository creation, or push.

## Remaining concerns for later stages

- Global role definitions (`roles`) and global user identity attributes still need an explicit platform-vs-company administration design. This stage blocks global role mutation in multi-company mode but does not redesign role storage.
- Native SQLite database backup is intentionally unavailable in multi-company mode. Build and test a tenant-safe backup/restore workflow before production.
- Review file metadata links for authorization and implement a tenant-aware file storage service before attaching sensitive documents.
