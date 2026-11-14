# ERP Global Pro — Implementation Backlog

The backlog is ordered to prevent unsafe partial multi-tenancy. A phase is not complete merely because the schema exists; code, tests, documentation and rollback must all pass.

## EPIC 0 — Preserve and establish a reproducible baseline

- [ ] Keep uploaded archive unchanged and record its SHA-256.
- [ ] Add deterministic dependency constraints/lock file.
- [ ] Run existing backend unit tests in a clean environment.
- [ ] Add CI for Python syntax, unit tests, dependency/security scan and frontend JavaScript syntax.
- [ ] Add a test database fixture and seeded test tenant.
- [ ] Capture current financial/inventory invariants and baseline row counts.
- [ ] Define backup and restore smoke test.

## EPIC 1 — Identity and tenant foundation

- [ ] Add versioned migration framework with checksums, locking and rollback guidance.
- [ ] Add `companies` with status, legal name, country, timezone, base currency, locale and fiscal-year settings.
- [ ] Add `company_memberships` with user, company, role, status and invitation lifecycle.
- [x] Move effective role assignment to membership-level roles for authenticated request context (custom role catalog is still global).
- [ ] Add company creation, invitation and membership acceptance workflows.
- [x] Add company-switch API and verified signed tenant context.
- [x] Reject requests with missing, suspended or unauthorized tenant context.
- [ ] Add full authorization matrix and run cross-tenant negative tests (route tests authored; execution pending dependencies).
- [ ] Keep operational API fail-closed until every record/read/write/job/report/export/cache/file is company-scoped.

## EPIC 2 — Tenant-aware master data and operations

- [x] Add `company_id` ownership columns to all 40 tenant-owned business tables plus the hybrid `audit_log` table (Stage 3; transitional single-company defaults only).
- [x] Inventory source-schema unique keys and define proposed company-scoped replacements.
- [x] Add read-only SQLite uniqueness preflight utility and tests (Stage 5 tool checkpoint; no schema change).
- [ ] Run duplicate preflight on a restored real database copy, then convert global unique constraints to tenant-scoped unique constraints with safe table rebuilds and rollback.
- [x] Add interim SQLite same-company relationship guards for 55 tenant-to-tenant FK groups (Stage 4: 110 INSERT/UPDATE triggers, 55 relationship indexes + one audit index).
- [ ] Replace interim triggers with actual tenant-aware composite foreign-key constraints in the PostgreSQL schema; retain a verified SQLite strategy where required.
- [ ] Convert inventory, POS, sales and purchasing services.
- [x] Add tenant-scoped list/count/get/create/update/delete and same-company `F(ref)` validation to the shared CRUD engine (Stage 7; bespoke callbacks/services remain pending).
- [ ] Scope import/export and document numbering.
- [ ] Add tenant-scoped audit events and file authorization.

## EPIC 3 — Financial integrity

- [ ] Scope chart of accounts, cost centers, fiscal periods, journals and journal lines.
- [ ] Add company-specific fiscal periods and closing controls.
- [ ] Scope payments, tax settings, assets and depreciation.
- [ ] Add currencies and effective-dated exchange rates.
- [ ] Reconcile migrated trial balance, P&L, balance sheet, cash flow and inventory valuation.
- [ ] Add explicit consolidation permissions and reports only after isolation is proven.

## EPIC 4 — Remaining business modules

- [ ] HR, attendance, leave, advances and payroll.
- [ ] CRM, projects, tasks and time logs.
- [ ] Manufacturing, BOM, production and costing.
- [ ] Maintenance, assets and document management.
- [ ] Sector-specific entitlements and configuration.

## EPIC 5 — PostgreSQL and production hardening

- [x] Add isolated Psycopg 3 adapter prototype and SQLite DB adapter contract (not wired into app).
- [x] Add PostgreSQL disposable CI job and live adapter/RLS/pool tests (Stage 22; execution still pending first GitHub Actions run).
- [ ] Wire PostgreSQL adapter into application only after live driver tests pass.
- [ ] Add PostgreSQL migration and verified data-transfer tooling.
- [ ] Add tenant-aware composite foreign keys and enable RLS on every tenant-owned table.
- [ ] Use transaction-local tenant context from verified membership and test connection-pool cleanup across all request paths.
- [ ] Add shared rate limiting, session revocation and production auth hardening.
- [ ] Add structured logs, metrics, alerts and audit retention.
- [ ] Test backup restore, disaster recovery and deployment rollback.

## EPIC 6 — Industry packs

- [ ] Trading/distribution pack.
- [ ] Services/professional firms pack.
- [ ] Manufacturing pack.
- [ ] Retail/POS pack.
- [ ] Education operations pack.
- [ ] Construction/contracting pack.
- [ ] Healthcare pack only after a dedicated privacy/security/compliance assessment.


## Stage 6 — Tenant query / API scope audit
- [x] Perform a read-only static review of the generic CRUD engine, resources, APIs, services, import/export and request guard.
- [x] Record the critical missing-scope paths and the temporary multi-company fail-closed gate.
- [ ] Implement company scoping in the generic CRUD engine and reference validation, with unit/API tests.
- [ ] Scope each custom service/API/report/import/export/file/background path and add two-company negative tests.
- [ ] Do not set `TENANT_DATA_SCOPING_ENABLED=True` before every scoped path and negative test passes.


## Stage 7 — Generic CRUD tenant scope
- [x] Resolve active company only from the verified server-side `g.company` context.
- [x] Fail closed if tenant context or a resource table's `company_id` column is missing.
- [x] Scope list/count/get/update/delete by `company_id`; inject server-owned `company_id` on create.
- [x] Reject client-supplied `company_id` and cross-company `F(ref)` references.
- [x] Add 11 focused negative/contract tests; combined Stage 2–7 focused suite passes 41/41.
- [ ] Audit and scope resource callbacks and their side-effect services.
- [ ] Convert bespoke APIs/services, reports, settings, sequences, imports/exports, audit listing, files and background work.
- [ ] Execute real Flask integration tests and full two-company negative matrix.
- [ ] Keep `TENANT_DATA_SCOPING_ENABLED=False` until every operational surface is covered.


## Stage 8 — Callback and side-effect audit (read-only)
- [x] Enumerate callbacks declared by the 21 CRUD resource definitions.
- [x] Trace product opening-stock creation through inventory, settings and accounting.
- [x] Record unscoped callback queries and resource aggregate subqueries.
- [x] Prioritize settings/sequences redesign and same-transaction inventory/accounting side effects.
- [ ] Convert inventory/accounting/settings/sequences to explicit company context.
- [ ] Add two-company integration and rollback tests for opening stock and journal posting.
- [ ] Keep the multi-company gate disabled until services, APIs, imports/exports, reports, files and jobs are tenant-aware.


## Stage 13 — CRM, maintenance and project summary API scope
- [x] Scope CRM lead conversion lookup, partner creation and lead update to the active company.
- [x] Scope CRM pipeline aggregation to the active company.
- [x] Scope maintenance schedule completion lookup and update to the active company.
- [x] Scope project summary, time-log employee joins and task aggregates to the active company.
- [x] Add 8 SQLite handler-level negative tests; combined tenant-focused suite is 96/96.
- [ ] Run actual Flask/HTTP integration tests when Flask dependencies are available.
- [ ] Scope reports/dashboard, audit listing, import/export, attachments and background work (Stage 14+).
- [ ] Keep `TENANT_DATA_SCOPING_ENABLED=False` until every operational surface is covered.

## Stage 14 — Reporting, import/export and audit isolation
- [x] Scope dashboard and report aggregations/joins to the active company.
- [x] Scope CSV/XLSX exports and partner/product imports to the active company.
- [x] Scope audit listing and company-visible system metrics.
- [x] Fail closed for global role/demo/backup operations in multi-company mode.

## Stage 15 — Private attachment storage
- [x] Migrate to SQLite v7 and add tenant-owned `file_objects` metadata.
- [x] Store uploads outside frontend/static with a per-company private directory and opaque storage keys.
- [x] Add scoped list/upload/get/download/delete API endpoints.
- [x] Restrict extensions, verify content signatures/UTF-8, enforce 10 MiB per-object limit.
- [x] Add SHA-256 integrity check command and reject DB-only backup/restore when external files exist.
- [ ] Integrate antivirus/malware scanning with quarantine and clean status before download.
- [ ] Add tenant storage quotas, rate limiting and retention policies.
- [ ] Connect frontend UI to file-object APIs.
- [ ] Build atomic DB + file archive/restore with manifest and disaster-recovery tests.
- [ ] Add orphan-object inventory/cleanup tooling with safe dry-run first.

## Stage 16 — Release-gate hardening
- [ ] Run real Flask/HTTP integration tests with installed pinned dependencies.
- [ ] Add browser E2E for two-company flows.
- [ ] Implement PostgreSQL adapter/migrations, composite foreign keys and RLS.
- [ ] Test connection pool/session tenant context leakage, concurrency and backups.
- [ ] Keep `TENANT_DATA_SCOPING_ENABLED=False` until all release-gate checks pass.


## Stage 22 — PostgreSQL live integration harness
- [x] Add live Psycopg 3 integration tests using a temporary non-superuser role and disposable schema.
- [x] Add FORCE RLS sample policy and two-company read/write tests.
- [x] Add transaction-local context reset and one-connection pool reuse tests.
- [x] Add a hard CI preflight that fails if PostgreSQL/DSN/driver is missing.
- [x] Add PostgreSQL 16 service job in GitHub Actions for Python 3.11/3.12.
- [ ] Execute the PostgreSQL job on GitHub Actions and inspect its real logs; tests have not run in the current offline environment.
- [ ] Build PostgreSQL schema/migrations and composite FKs after live driver integration is green.
- [ ] Design a trustworthy tenant context boundary; a user-settable custom GUC alone is not a standalone defense against SQL injection.
- [ ] Keep `TENANT_DATA_SCOPING_ENABLED=False` until all database, HTTP, UI, files and backup release gates pass.
