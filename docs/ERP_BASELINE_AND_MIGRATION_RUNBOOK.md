# ERP Global Pro — Baseline and Migration Runbook

## Scope of implemented stages

### Stage 1 — Identity migration foundation

- `companies` stores company identity and basic locale/industry metadata.
- `company_memberships` links global users to companies with a company-scoped role and membership status.
- Existing single-company databases are mapped to one bootstrap company. Existing users receive active memberships with their legacy roles.
- Stage 1 setup creates an active `admin` membership for the initial administrator. The Stage 3/4 reconciled worktree creates/reuses a bootstrap company and setup configures that company.

### Stage 2 — Verified active-company context

- `GET /api/auth/companies` lists a user's active memberships.
- `POST /api/auth/switch-company` validates membership before issuing a company-bound token.
- JWT `cid` and `mid` claims are revalidated against the database on each request. Suspended/revoked membership or company status invalidates that context.
- Effective role/permissions are loaded from the current membership. Protected API routes require an active company context.
- The frontend prompts for a company when an old token is ambiguous and exposes a company selector.
- **Fail-closed guard:** if more than one company exists while data isolation is incomplete, operational APIs return `503 tenant_isolation_pending`. Do not disable this guard.

**Important:** Stages 1 and 2 establish identity and active-company context. Stages 3 and 4 add record ownership and interim SQLite relationship guards, but existing SQL, reports, exports, imports, files, audit access and jobs are not yet comprehensively tenant-scoped. `roles` remains a shared role catalog. Do not enable multi-company operation or onboard multiple real companies until the remaining stages complete and pass all negative tests.


### Stage 3 — Tenant ownership columns and fail-closed database guards

- Adds `company_id` as a required foreign-key column to all 40 tenant-owned business tables, including settings, sequences, master data, accounting, inventory, trade, HR, manufacturing, CRM, projects, assets, maintenance and documents.
- Adds a nullable `company_id` to `audit_log`: company-originated events can be associated with a company; system/authentication events may remain global (`NULL`).
- Existing rows are assigned to the only existing company. If no company exists (fresh install), a bootstrap company is created so the first-run flow and initial base data have a valid owner.
- New single-company rows inherit the bootstrap company through a transitional SQLite default. `company_id` cannot be changed after creation. This default is **not** sufficient for multi-company operation and must be removed/replaced by explicit company context before the operational gate can be opened.
- Database triggers block insert, update and delete operations on tenant-owned business tables whenever more than one company exists. The API-level `tenant_isolation_pending` guard remains enabled as an additional defense. `audit_log` remains available for global security events.
- The initial setup route reuses the bootstrap company instead of accidentally creating a second company. Base tenant data is seeded only when exactly one company exists.
- A database with more than one company is rejected by the migration because the legacy rows cannot be attributed safely without a documented row-to-company mapping. No automatic guessing or splitting is performed.

### Stage 4 — Same-company relationship guards (reconciled)

- Enumerates 55 tenant-to-tenant foreign-key relationship groups from the current schema and fails closed if schema drift changes that count.
- Preflights existing references and aborts/rolls back if a child row points to a parent owned by another company.
- Adds 110 SQLite `BEFORE INSERT` / `BEFORE UPDATE` relationship guards, 55 child-side relationship indexes, and one `(company_id, ts)` audit index.
- These are trigger-based guards, not actual composite foreign-key constraints. They provide interim database defense but do not complete the PostgreSQL relational model.

**Still incomplete after Stage 4:** tenant-scoped unique constraints (e.g. SKU, account code, document number), real composite foreign keys, and explicit company scoping in every service/API/report/import/export/file/job path. Multi-company operation remains disabled.

## Migration behavior

- Base schema remains version 1; identity migration records version 2; ownership migration records version 3; relationship guards record version 4 (`LATEST_VERSION=4`).
- Each migration runs under a SQLite write lock and transaction. Version 2 adds identity/companies. Version 3 temporarily disables SQLite FK enforcement only outside the transaction to add required ownership columns, checks `PRAGMA foreign_key_check` before commit, assigns existing records to the single bootstrap company and adds fail-closed triggers; FK enforcement is restored afterward. Version 4 adds the relationship preflight, trigger guards and supporting indexes.
- Version 2 maps existing users to the legacy company. Version 3 creates a bootstrap company if the database is still empty; on a single-company legacy database, the existing company name/currency/country are retained.
- If a legacy user references a role that is missing from `roles`, the migration aborts rather than silently assigning a different role.
- Re-running is safe after the relevant version is recorded. Version 3 refuses to proceed when more than one company exists, rather than guessing how legacy business records should be split. The migration tests prove idempotency on the synthetic legacy schema; a restored production-database rehearsal is still required.
- Take a verified database backup before any production upgrade. Test restoration before relying on the backup.

## Reproducible checks

Run from `backend/` after installing `requirements.txt`:

```bash
python -m compileall -q erp tests
python -m unittest tests.test_tenant_migrations tests.test_tenancy_service tests.test_tenant_ownership_migration tests.test_tenant_relationship_migration -v
python -m unittest discover -s tests -v
```

The first migration unit-test module uses only Python's standard library and can also be run without Flask:

```bash
PYTHONPATH=. python -m unittest tests.test_tenant_migrations tests.test_tenancy_service tests.test_tenant_ownership_migration tests.test_tenant_relationship_migration -v
```

## Rollout gates still required

1. Capture a production backup and restore it into an isolated environment.
2. Verify pre-migration and post-migration row counts and financial integrity checks.
3. Exercise migration against a copy of a real legacy database, not only an empty test database.
4. Finish company creation/invitation workflows and tenant-owned role catalogs.
5. Complete tenant-scoped unique constraints and implement actual composite same-company foreign keys in the PostgreSQL target (Stage 4 SQLite triggers are interim guards, not composite FKs). The focused v2–v4 test command is recorded in `ERP_STAGE_4_VALIDATION.txt`.
6. Convert all API reads/writes, reports, exports, imports, background tasks, caches, files, and audit trails to enforce tenant scope.
7. Add cross-company negative tests and PostgreSQL RLS; prove a user from company A cannot read, mutate, export, or infer company B data.

## Preservation

The uploaded source archive remains the immutable reference. The Stage 3 rebuilt worktree came from the recovered Stage 2 package; Stage 4 Reconciled resolves the v4 code/documentation/test mismatch. No GitHub repository, production database or live deployment has been changed.


## Update — Stage 9 (2026-11-13)

The migration history has advanced to SQLite schema version 5. Stage 9 rebuilds `settings` with `PRIMARY KEY(company_id,key)` and adds tenant workload indexes. This is distinct from project Stage 5, which remains the read-only uniqueness preflight utility. The `sequences` table still has a globally unique `name` primary key and must not be treated as tenant-ready; its service fails closed when another company owns a sequence name. See `ERP_STAGE_9_SERVICE_SCOPE_CHANGELOG_AR.md`.
