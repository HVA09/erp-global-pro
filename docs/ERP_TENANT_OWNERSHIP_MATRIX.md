# ERP Global Pro — Tenant Ownership Matrix

Baseline inspected: original SQLite schema (`backend/erp/schema.sql`), 44 tables. The Stage 1 migration adds two more tables (`companies`, `company_memberships`), bringing the working schema to 46 tables.

This matrix is the control document for the next migration stage. A table must not be treated as safely multi-tenant merely because it has a foreign key to another tenant-owned table; each row must carry or inherit a validated company boundary, and every access path must enforce it.

## 1. Ownership classification

| Ownership class | Tables | Target treatment |
|---|---|---|
| Global system metadata | `schema_version` | Global migration metadata; never exposed through tenant APIs. |
| Global identity | `users` | One account identity across companies; keep globally unique email. Do not store a single global business role here. |
| Global tenant registry | `companies` | One row per legal/business tenant; company lifecycle is global control-plane data. |
| Membership bridge | `company_memberships` | One row per user/company; authoritative company membership and company-scoped role assignment. |
| Hybrid authorization catalog | `roles` | Keep seeded system roles as global templates; add company-owned custom roles. Membership should ultimately reference a stable `role_id`, not a globally ambiguous role name. |
| Company configuration | `settings`, `sequences` | Scope keys/names by company. Company settings cannot remain globally keyed; document numbering must be independent by company. |
| Cross-cutting tenant records | `audit_log`, `files` | Attach company ownership where the event/file belongs to a tenant. Keep explicitly system-level security events distinguishable from tenant audit events. |
| Master data | `categories`, `warehouses`, `partners`, `products`, `accounts`, `cost_centers`, `departments` | Company-owned. Names, codes, SKUs, and account codes may legitimately repeat in different companies. |
| Accounting and finance | `journal_entries`, `journal_lines`, `payments`, `trade_documents`, `trade_document_lines`, `credit_applications`, `fixed_assets`, `depreciation_postings`, `advances`, `payroll_runs`, `payroll_items` | Company-owned, with company-consistent composite foreign keys and immutable-ledger safeguards preserved. |
| Inventory | `stock_moves`, `stock_levels`, `stock_counts`, `stock_count_lines` | Company-owned. Product and warehouse references must belong to the same company as the movement/count. |
| Manufacturing | `boms`, `bom_components`, `production_orders` | Company-owned. BOM, product, warehouse, and production references must not cross company boundaries. |
| Human resources | `employees`, `attendance`, `leave_requests` | Company-owned employment records; do not confuse these records with global login identities in `users`. |
| CRM and marketing | `leads`, `opportunities`, `activities`, `campaigns` | Company-owned; linked partners must be from the same company. |
| Projects and work | `projects`, `tasks`, `time_logs` | Company-owned; project, employee, task, and assignee relations must be company-consistent. |
| Maintenance | `maintenance_requests`, `maintenance_schedules` | Company-owned operational records. |

## 2. Tenant-aware uniqueness that must be reviewed

These uniqueness constraints currently apply globally or are keyed without company identity. Before multi-company onboarding, replace them with company-scoped uniqueness (or retain global scope only where explicitly intended):

- `settings(key)` → `(company_id, key)`.
- `sequences(name)` → `(company_id, name)`.
- `categories(name)`, `warehouses(code)`, `products(sku)`, `products(barcode)`, `accounts(code)`, `cost_centers(name)`, and `departments(name)` → company-scoped unique keys.
- `journal_entries(number)` → `(company_id, number)`.
- `trade_documents(doc_type, number)` → `(company_id, doc_type, number)`.
- `attendance(employee_id, date)` → `(company_id, employee_id, date)`.
- `depreciation_postings(asset_id, period)` → `(company_id, asset_id, period)`.
- Partial unique index `payroll_runs(month) WHERE status='posted'` → include company identity while preserving its partial predicate.
- Existing composite primary key `stock_levels(product_id, warehouse_id)` → include `company_id`.
- Keep `users(email)` globally unique because login accounts are global identities, not company-owned user rows.
- Redesign `roles(name)` deliberately: system templates may be global, while custom roles need company-scoped uniqueness and membership references by role ID.

All existing rows must be checked for duplicates before tenant-scoped unique indexes are created. Do not silently rename accounting numbers, SKUs, or account codes to make a migration pass.

## 3. Foreign-key and relationship requirements

Every tenant-owned parent should expose a unique `(id, company_id)` key (or equivalent) so children can reference both identity and tenant. The next schema migration must review at least:

- `journal_lines` → journal entry, account, partner, and cost center.
- `trade_documents` → partner, warehouse, and journal entry; `trade_document_lines` → document, product, and account.
- `stock_moves`, `stock_levels`, `stock_counts`, and `stock_count_lines` → product, warehouse, and count.
- HR/payroll records → employee, department, payroll run, and journal entry.
- Manufacturing records → BOM, product, warehouse, and journal entry.
- Projects/tasks/time logs → project, employee, and task.
- CRM records → partner.

A foreign key on a numeric `id` alone is insufficient to prove that both records belong to the same company.

## 4. Code paths requiring tenant-context propagation

A preliminary static inventory found database access concentrated in these areas (counts are `db.q/one/scalar/execute/run` call sites, not unique SQL statements):

| Code area | Access call sites | Main concern |
|---|---:|---|
| `services/trade.py` | 50 | Sales/purchase documents, payments, partners, journal posting, stock effects. |
| `services/accounting.py` | 30 | Chart of accounts, journal entries, balances, financial integrity. |
| `services/inventory.py` | 23 | Product/warehouse/stock state and stock counts. |
| `api/admin.py` | 17 | Users, roles, settings, admin operations. |
| `services/reports.py` | 16 | Cross-module reporting and aggregation. |
| `services/hr.py` | 16 | Employees, attendance, leave, payroll. |
| `api/ops.py` | 15 | Operational endpoints. |
| `seed.py` | 11 | Base and demo data initialization. |
| `services/manufacturing.py` | 10 | BOM and production records. |
| `services/assets.py` | 9 | Fixed assets and depreciation. |
| `api/data.py` | 9 | Data import/export; high-risk bulk access path. |
| `api/hr.py` | 6 | HR route handlers. |
| `services/settings.py` | 5 | Currently global settings cache and key/value storage. |
| `api/inventory.py` | 5 | Inventory route handlers. |
| `services/sequences.py` | 3 | Currently global document sequence namespace. |
| `security.py` | 3 | Must resolve verified active membership and company context. |

This is a static call-site inventory, not a complete taint/data-flow proof. The next code review must inspect every SQL statement, including raw `db.run`, imports/exports, joins, report builders, scheduled tasks, and frontend endpoints. Any omitted or unclassified path blocks multi-company release.

## 5. Required implementation order

1. Preserve the original database and rehearse a restore into an isolated environment.
2. Resolve role design and active-company selection; validate membership server-side. Never trust a raw client-supplied company ID.
3. Add company identity to master data/configuration with explicit backfill to the bootstrap company.
4. Add tenant-aware unique keys and composite parent/child foreign keys, after duplicate/data-integrity reports.
5. Propagate tenant context through services and APIs, including import/export, reports, files, audit, and background work.
6. Add two-company tests with overlapping names, SKUs, account codes, and document numbers; prove isolation for read, create, update, delete, list, aggregate, export, and error paths.
7. Add PostgreSQL support and Row-Level Security only after application-level isolation is consistently enforced and tested.

## Release rule

No production multi-company launch while any tenant-owned table or code path remains unclassified, unscoped, or untested. Stage 1 is only the identity/migration foundation.
