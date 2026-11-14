# ERP Global Pro — Stage 25A/25B Application Tenant Context Integration Plan

**Date:** 2026-11-14  
**Status:** Stage 25A evidence and architecture-boundary review completed; Stage 25B implementation not started  
**Baseline commit:** `6badac066a0fdd10ae6bda79f753fee5edae6210`  
**CI run:** [43640872392](https://github.com/HVA09/erp-global-pro/actions/runs/43640872392)

## 1. Objective

Connect the existing Flask/JWT authentication lifecycle to the signed PostgreSQL tenant context in a way that can eventually enforce PostgreSQL RLS for all tenant-owned business rows.

The integration must not create two competing sources of truth for users, companies, memberships or roles. It must preserve the currently working SQLite v8 application until a complete PostgreSQL migration and rollback path are proven. This plan does not authorize production deployment, production schema changes or activation of multi-company operations.

## 2. Verified baseline

GitHub Actions run `43640872392` completed with `success` on commit `6badac0`. All four matrix jobs passed:

| Job | Result |
|---|---:|
| Backend — Python 3.11 | PASS |
| Backend — Python 3.12 | PASS |
| Live PostgreSQL 16 — Python 3.11 | PASS |
| Live PostgreSQL 16 — Python 3.12 | PASS |

Per Python version:
- 32 Psycopg adapter prototype tests passed.
- 303 backend tests passed; the two PostgreSQL integration classes are intentionally skipped in this generic job because they run in the dedicated PostgreSQL job.
- 11 real Flask HTTP integration tests passed.
- 30 live PostgreSQL tests passed in the dedicated job: 11 adapter/RLS/pool tests plus 19 signed-context/control-plane tests.

The live PostgreSQL matrix uses disposable PostgreSQL 16 service containers. It proves the tested adapter and SQL prototype behaviors in CI; it does **not** prove that the production Flask application is connected to PostgreSQL.

## 3. Current application facts confirmed by source review

### 3.1 Flask authentication and tenant selection

- `backend/erp/__init__.py` calls `security.load_user()` before API request handling.
- `backend/erp/security.py` validates the existing HS256 JWT, reads the user and global role, then revalidates the selected company membership and obtains its permissions.
- The existing JWT includes `sub`, and when selected, `cid` and `mid`. Membership revocation and inactive company state invalidate the request.
- `backend/erp/api/auth.py` implements login, listing companies and switching companies using the current tenancy service.
- `backend/erp/services/tenancy.py` reads `users`, `roles`, `companies` and `company_memberships` through the current database module.

### 3.2 Database boundary

- `backend/erp/db.py` imports and directly uses `SQLITE_ADAPTER`.
- Schema initialization and versioned migrations are SQLite-specific.
- The current request connection is managed through Flask `g`; the transaction API uses SQLite `BEGIN IMMEDIATE` for outer write transactions and nested savepoints.
- `DatabaseAdapter` is a useful narrow contract, but it does not yet abstract every SQLite-specific schema, migration, backup, error, transaction and query behavior used by the app.
- `Psycopg3Adapter` is an isolated prototype. It is not selected by configuration or wired into `db.py`; its `begin(write=True)` deliberately rejects the current SQLite write-lock semantic.

### 3.3 Signed PostgreSQL context and Stage24 routines

- Stage23's SQL template expects dedicated PostgreSQL control tables for users, companies and memberships, and a protected signing-key table.
- The PostgreSQL application role has no direct SELECT on the control-plane tables or the signing-key table.
- Stage24 adds narrowly scoped read-only `SECURITY DEFINER` functions, including `list_my_companies()`, `get_current_company_profile()` and `get_current_membership()`.
- Those functions require an already valid signed company context. They are not a login endpoint and cannot bootstrap the first company selection.
- No finished PostgreSQL credential-verification/bootstrap API, trusted pre-company company-discovery flow, key provisioning/rotation procedure or Flask request integration exists yet.

## 4. Critical architecture decision

**Do not directly connect the signer to Flask at this point. Do not split authentication in SQLite and membership authorization in PostgreSQL without a designed consistency protocol.**

A hybrid arrangement could authenticate a user against one database while accepting company membership, role or company status from another. If those records diverge, revoked memberships or stale permissions could continue to authorize database access.

Before wiring the request path, Stage25 must select and test one authoritative source of truth for:
- user identity and active status;
- password credential material and login verification;
- company records and active/suspended status;
- memberships and active/suspended/revoked status;
- role and permission definitions, including the currently global role catalog.

### Recommended target

Move the complete identity/control-plane authority to PostgreSQL as one controlled migration, then let Flask authenticate and select companies through narrowly scoped PostgreSQL functions or a separately constrained authentication principal. Do not maintain asynchronous dual writes between SQLite and PostgreSQL.

SQLite remains the active application backend until this route, its data migration, rollback and end-to-end tests are complete.

## 5. Stage25 work packages

### Stage25B — Identity source of truth and migration contract

1. Inventory the authoritative fields and constraints in SQLite v8 for users, roles, companies and memberships.
2. Define PostgreSQL control-plane schema, uniqueness, foreign keys, status semantics and migration version/checksum strategy.
3. Define a one-way, repeatable import from a restored SQLite copy into a disposable PostgreSQL database.
4. Compare row counts, identity IDs, membership relationships, role permissions and credential-hash compatibility.
5. Define cutover and rollback criteria. Do not dual-write in production.

**Exit gate:** source-of-truth decision and a rehearsed, disposable migration with a reconciliation report.

### Stage25C — Login and pre-company bootstrap

1. Design an authentication boundary that allows password verification without granting the operational app role broad direct SELECT on users or credential tables.
2. Prefer narrowly scoped, audited `SECURITY DEFINER` routines or a distinct authentication principal with the minimum required database privileges.
3. Design a short-lived, one-use pre-authentication proof/challenge. It must not accept an arbitrary client-supplied `user_id` as proof of identity.
4. Permit company discovery only for the authenticated principal represented by that proof.
5. On company selection, revalidate the active membership, company status and permissions from the authoritative control plane before issuing the existing application JWT and the separate signed PostgreSQL tenant context.

Stage24's `list_my_companies()` cannot be reused as the pre-login company list because it requires an already valid company-scoped signed context.

**Exit gate:** tests for valid credentials, invalid credentials, disabled users, no membership, multiple memberships, suspended company, revoked membership and attempts to enumerate another user's companies.

### Stage25D — HMAC key lifecycle

1. Keep JWT signing and PostgreSQL tenant-context HMAC as distinct purposes with distinct keys and audiences.
2. Define a secure provisioning ceremony for placing the same tenant-context key material in the trusted signing service/secret store and the protected PostgreSQL key table, without exposing it to the operational app role.
3. Use explicit key IDs and support an overlap window during rotation.
4. Specify rotation, revocation, emergency replacement, backup and recovery procedures.
5. Ensure payloads, signatures and key material never enter logs, Git, ZIP artifacts or client-visible errors.

**Exit gate:** key rotation tests covering active key, previous key overlap, retired key rejection and unknown key ID rejection.

### Stage25E — Request-scoped PostgreSQL transaction context

1. Extend the adapter contract for pooled connection acquisition, transaction ownership and safe transaction-local context installation.
2. After authentication and membership validation, derive claims on the server; never trust client-provided claims or GUC values.
3. In the same database transaction used for tenant data, set `erp.tenant_payload` and `erp.tenant_signature` using transaction-local settings before the first RLS-protected query.
4. Ensure success, exception, rollback, disconnect and pool return cannot leak context to the next request.
5. Replace the SQLite-specific `BEGIN IMMEDIATE` assumption with explicit PostgreSQL isolation/locking semantics and concurrency tests.

**Exit gate:** same-connection pool reuse tests plus context reset tests for success, exception, rollback and cancellation.

### Stage25F — Disposable end-to-end slice

Before migrating all ERP tables, implement one disposable proof-of-integration slice:

`login → authenticated pre-auth proof → company selection → signed context → PostgreSQL transaction → current_company_id() verification → RLS-scoped test row`

Required negative cases:
- missing or malformed proof;
- expired or future-issued claims;
- tampered payload or signature;
- arbitrary `set_config` / forged GUC values;
- non-member user, revoked membership or suspended company;
- wrong company ID and cross-company row access;
- replay/revocation behavior for `jti`;
- connection pool reuse after successful and failed transactions.

**Exit gate:** full end-to-end integration tests pass on PostgreSQL 16 in CI for Python 3.11 and 3.12, with no missing-dependency skips.

### Stage25G — Only after the slice is accepted

- Build complete PostgreSQL migrations for tenant-owned ERP tables.
- Add tenant-aware composite foreign keys and matching indexes.
- Apply RLS to every tenant-owned table, with least-privilege grants and verified policy coverage.
- Port every API, CRUD callback, service, report, import/export, file, backup/restore and background operation.
- Reconcile accounting balances, inventory valuation, payroll totals and business invariants after migration.
- Rehearse rollback and recovery using a restored copy.

This package is explicitly **not** part of the current documentation-only Stage25A work.

## 6. Non-negotiable safety constraints

- Keep `TENANT_DATA_SCOPING_ENABLED=False`.
- Keep SQLite v8 as the only active application database.
- Do not apply Stage23/24 SQL templates to a production database.
- Do not connect a partially migrated PostgreSQL control plane to SQLite-backed login.
- Do not enable RLS on real ERP tables until their schema, composite FKs, app grants and all tenant query paths are migrated and tested.
- Keep the original archive unchanged and retain its recorded SHA-256.
- Use disposable databases and synthetic test data for all Stage25 prototypes.

## 7. Known non-blocking CI maintenance observations

The successful CI run surfaced a test file-stream `ResourceWarning` and deprecation warnings from a Flask version check and the Node-based GitHub Actions runtime. They did not fail the build. Track these separately from the security-critical tenant-context integration.

## 8. Current Stage25A outcome

Completed in this branch:
- verified the four-job CI run and live PostgreSQL logs;
- reviewed Flask/JWT, tenancy service, SQLite DB layer, Psycopg prototype, signed-context code and Stage24 SQL;
- documented the source-of-truth and login/bootstrap blockers;
- updated the project continuity record and implementation backlog.

Not completed:
- no application code was changed;
- no PostgreSQL adapter was wired into Flask;
- no PostgreSQL migration was applied to ERP business tables;
- no production or hosted database was touched;
- no tenant gate was enabled.

**Next step:** resolve Stage25B's source-of-truth and credential/bootstrap design on a disposable database before any code-level application integration.
