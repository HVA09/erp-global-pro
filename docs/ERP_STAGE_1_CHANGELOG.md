# ERP Global Pro — Stage 1 Change Log

Date: 2026-11-13

## Implemented

1. Added schema migration version 2 in `backend/erp/migrations/`.
2. Added `companies` and `company_memberships` tables, including foreign keys, status checks, unique company/user membership, and lookup indexes.
3. Added a transactional, lock-protected migration path that rechecks the schema version after acquiring the SQLite write lock.
4. Existing installations with users are mapped to one bootstrap company using their saved company name, country, and currency; existing roles are preserved as company memberships.
5. A legacy user referencing a missing role causes the migration to fail and roll back rather than inventing an authorization role.
6. First-run setup creates a company and an active `admin` membership for the first administrator.
7. Added tenant identity helper functions and a runbook describing rollout gates and limitations.
8. Added migration tests for the empty database, legacy-user backfill, repeat execution, full original schema compatibility, and rollback on invalid legacy role data.
9. Added a tenant ownership matrix covering all 44 original schema tables, tenant-aware uniqueness changes, composite foreign-key requirements, and high-risk SQL access areas.

## Validation results

| Check | Result |
|---|---|
| Migration tests (5 cases) | PASS |
| Migration against original full SQLite schema | PASS |
| Python compile check (`compileall`) | PASS |
| Frontend JavaScript syntax (`node --check`) | PASS |
| Full application backend suite | NOT RUN — Flask is unavailable in the execution environment |
| Dependency installation attempt | BLOCKED — package index could not be resolved because network/DNS was unavailable |

The five migration tests use Python's standard library and were executed successfully. The existing full backend suite and route-level setup test still require installing `backend/requirements.txt` in a network-enabled environment.

## Explicitly not implemented yet

- No `company_id` has been added to operational, accounting, inventory, HR, manufacturing, or other tenant-owned records.
- Existing API authorization still uses the legacy global `users.role` field.
- JWTs do not yet carry a verified active-company context, and no company-switch endpoint exists.
- No tenant filtering has been added to reads/writes, reports, exports, jobs, caches, files, or audit access.
- PostgreSQL support and Row-Level Security (RLS) are not implemented.
- Therefore this stage is an identity/migration foundation only, not a production-ready multi-tenant release.

## Preservation and deployment status

- Original uploaded ZIP SHA-256 remains: `7943d5f5b1f027463a9ef2bd65116286ad76abdcde7db2e90bcedb4434e9895b`.
- Work was performed in a separate copy.
- No GitHub repository, live ERP deployment, or production database was modified.
- Do not run this migration against production until a backup/restore rehearsal and the full backend suite pass.
