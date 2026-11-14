# ERP Global Pro — Multi-Tenant Architecture

**Status:** Accepted architectural direction; implementation is partial and multi-company operations remain blocked
**Decision date:** 2026-11-13
**Scope:** Future architecture for all-sector ERP; this document does not change the current database or application behavior.

## 1. Product decision

ERP Global Pro will become a multi-tenant platform for organizations across sectors. One deployment may host multiple companies, while every company's operational and financial data remains isolated. The product will support sector-specific capabilities through modular industry packs rather than forcing every customer into one oversized workflow.

The original uploaded project remains the immutable baseline. All implementation work must happen in a separate development branch/worktree and must preserve a tested rollback path.

## 2. Core tenancy model

Use a shared application and shared PostgreSQL cluster/database initially, with a mandatory `company_id` (tenant key) on every tenant-owned row. Enforce isolation in the application service/repository layer and reinforce it with PostgreSQL Row-Level Security (RLS) before production multi-tenant launch.

### Identity model

- `users`: global identity; email remains globally unique.
- `companies`: tenant master record, legal identity, country, base currency, timezone, fiscal-year settings, status and plan.
- `company_memberships`: joins users to companies; stores company-scoped role, membership status, invitation/activation timestamps and optional location scope.
- `roles` and `permissions`: permission templates; effective role assignment belongs to a membership, not to the global user. The current code still has a shared legacy role catalog, which must become tenant-aware before multi-company operation is enabled.
- `user_sessions` / token claims: selected active company is established only after verifying an active membership. A client-supplied company ID is never trusted as authorization.

### Tenant context

1. Authenticate the global user.
2. Resolve the selected company from a signed, short-lived access token or a server-side session.
3. Verify that the user has an active membership in that company on every request (or use short cache lifetimes with revocation handling).
4. Set immutable request-scoped `tenant_id` / `g.company_id`.
5. Require every tenant data access to use that context.
6. Fail closed if tenant context is absent, invalid, suspended or mismatched.

Do not accept a raw `X-Company-ID` header as proof of access. A company selector may request a switch, but the server must issue a new context only after checking membership.

## 3. Data classification

### Global identity / platform tables

- `users`
- platform-level feature catalog and permission definitions
- platform operations/audit events that are explicitly platform-scoped

### Tenant-owned configuration and business data

- company profile, settings, fiscal periods, tax profiles, document sequences
- roles/memberships where customization is tenant-specific
- categories, warehouses, partners, products, stock, stock counts and movements
- chart of accounts, cost centers, journal entries/lines, assets and depreciation
- trade documents/lines, payments and credit applications
- departments, employees, attendance, leave, advances and payroll
- projects, tasks, time logs, CRM leads/opportunities/activities/campaigns
- bills of materials, production orders, maintenance records and files/documents
- tenant audit log, import/export jobs and integration credentials

Every tenant-owned table must carry `company_id NOT NULL` after migration. Child tables must either carry their own `company_id` or be protected by composite foreign keys through a tenant-owned parent. For high-risk financial and inventory records, prefer explicit tenant keys plus composite constraints to make cross-tenant references impossible at the database layer.

## 4. Isolation controls

- PostgreSQL RLS policies on every tenant-owned table, using a transaction-local tenant setting (for example `SET LOCAL app.company_id = ...`).
- A single trusted database access layer sets tenant context inside each transaction; never reuse pooled connections with a leaked tenant setting.
- Composite unique constraints, e.g. `(company_id, sku)`, `(company_id, code)`, `(company_id, document_type, number)`.
- Composite foreign keys, e.g. `(company_id, partner_id)` references `(company_id, id)`.
- Tenant-aware indexes beginning with `company_id` for common access patterns.
- Background jobs, exports, reports, scheduled tasks and webhooks must carry explicit tenant context and revalidate it.
- Cache keys, object-storage paths, search indexes and idempotency keys must include tenant identity.
- No cross-tenant reporting or support impersonation without explicit, audited platform authorization.

## 5. Sector coverage: modular industry packs

The core ERP remains sector-neutral. Industry packs enable relevant features and defaults:

- **Trading & distribution:** sales, purchasing, inventory, pricing, receivables/payables.
- **Services & professional firms:** CRM, projects, timesheets, service billing, expenses.
- **Manufacturing:** BOM, routings, production orders, work centers, costing, quality.
- **Retail:** POS, branches, cash management, promotions, barcode workflows.
- **Education:** campuses, terms, admissions, student billing, staff and procurement; education-specific records remain a separate domain module.
- **Healthcare (later, only after compliance review):** strict access controls and sensitive-data governance; do not store clinical records in the generic ERP core.
- **Construction / contracting (later):** projects, contracts, progress claims, equipment and job costing.

A tenant activates only the packs it needs. Do not claim legal or regulatory compliance merely because a feature exists; compliance must be assessed per country and customer context.

## 6. Accounting and localization

- Preserve integer minor-unit money storage and double-entry accounting invariants.
- Add company-specific base currency, functional currency, fiscal year, timezone, locale, tax registration, tax rules and document numbering.
- Introduce a currency/rate model and explicit exchange-rate source/effective date before multi-currency posting.
- Keep tax calculations versioned and country-specific; do not hard-code one country's tax assumptions globally.
- Financial reports must always be scoped to one company and a defined accounting period unless an explicitly authorized consolidated report is requested.
- Consolidation across companies is a separate controlled feature, not a side effect of multi-tenancy.

## 7. API and application architecture

- Keep Flask initially; do not rewrite the application solely to add tenancy.
- Introduce a tenant-aware service/repository boundary before changing all SQL.
- API handlers obtain the company context from trusted authentication middleware and pass it explicitly to business services.
- Avoid scattered direct SQL that omits tenant predicates. New code must use tenant-aware repository helpers.
- Frontend company switching must refresh the authenticated context and clear tenant-specific cached state.
- Keep the frontend framework decision separate; current vanilla JavaScript can remain until measured needs justify a migration.

## 8. Migration strategy from the current single-company SQLite schema

Migration must be additive, resumable, observable and reversible until the cutover is accepted.

1. **Baseline and backup:** hash and preserve the original ZIP; run existing tests; create a verified SQLite backup; record row counts and accounting invariants.
2. **Schema migration framework:** introduce versioned migrations with checksums, locking and explicit upgrade status. Do not continue relying on `executescript` at every startup for production schema changes.
3. **Tenant bootstrap:** create one `companies` row representing the existing company and one owner membership for the current administrator.
4. **Tenant keys:** add nullable `company_id` to tenant-owned tables, backfill every existing row to the bootstrap company, verify no nulls/orphans, then enforce NOT NULL.
5. **Constraint conversion:** replace global business uniqueness with tenant-scoped uniqueness; add composite foreign keys and indexes.
6. **Service conversion:** pass tenant context through CRUD, services, reports, imports/exports, audit, sequence generation and background operations.
7. **Security enforcement:** enable PostgreSQL RLS and run cross-tenant negative tests. SQLite development tests must still verify application-level tenant predicates.
8. **PostgreSQL cutover:** migrate data using a tested export/import path; compare row counts, balances, stock valuations, document totals and audit history.
9. **Pilot:** onboard two isolated test companies with deliberately overlapping identifiers (same SKU, partner name, account code and document number) and prove isolation.
10. **Production rollout:** feature flag, monitored pilot, rollback window and restore drill before broad enablement.

Never add `company_id` to the current schema and deploy without converting every read/write path. Partial tenancy is a data-leak risk.

## 9. Acceptance gates

Multi-tenant release is blocked until all of these pass:

- A user in Company A cannot list, read, update, delete, export or infer Company B data by changing IDs, filters, pagination, references or request payloads.
- Cross-tenant foreign-key references are rejected by the database.
- Same business identifiers can coexist in different companies but remain unique inside one company.
- Background jobs, exports, reports, imports, files and caches remain tenant-scoped.
- Company switching cannot retain data from the previously selected tenant in the browser.
- Suspended membership/company access is rejected promptly.
- Financial and inventory reconciliation passes for migrated legacy data.
- Backup and restore procedures are tested, including tenant-scoped export where required.
- Security review and automated cross-tenant isolation tests pass in CI.

## 10. Non-goals for the first release

- Building a new language model or AI agent into ERP core.
- Replacing Flask or rewriting the frontend without evidence.
- Implementing every industry pack at once.
- Claiming ZATCA Phase 2, e-invoicing, healthcare or other jurisdictional compliance without end-to-end certification and legal review.
- Enabling cross-company consolidated accounting before tenant isolation is proven.

## 11. Immediate implementation sequence

**Phase 0 — Preserve and baseline:** immutable original, source inventory, tests, dependency lock, security baseline.
**Phase 1 — Identity and tenancy foundation:** companies and memberships migration is implemented; signed active-company context, membership revalidation, membership-level role resolution and company switching are implemented in Stage 2. Data isolation and tenant-owned role catalogs remain incomplete.
**Phase 2 — Master data and operational modules:** partners, products, warehouses, categories, settings, sequences, documents.
**Phase 3 — Financial core:** accounts, periods, journals, tax, assets, payments, reconciliation.
**Phase 4 — Remaining modules:** HR, CRM, projects, manufacturing, maintenance, files.
**Phase 5 — PostgreSQL/RLS and operational hardening:** monitoring, backups, rate limits, CI, deployment.
**Phase 6 — Industry packs and integrations:** enable sector modules based on real customer requirements.

Each phase must ship with migrations, tests, documentation and rollback instructions. The existing source is not replaced until the new version passes the gates. A fail-closed guard blocks all operational API routes whenever more than one company exists while `TENANT_DATA_SCOPING_ENABLED` is false; do not remove or bypass that guard before the isolation acceptance gates pass.
