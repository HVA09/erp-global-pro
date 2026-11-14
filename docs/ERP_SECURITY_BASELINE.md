# ERP Global Pro — Initial Security Baseline

**Review type:** Static source review of the uploaded archive, 2026-11-13. No application was deployed and no production data was accessed.

## Findings to address before multi-tenant production

### High — Business-data tenant isolation is still incomplete

Stages 1 and 2 add `companies`, `company_memberships`, signed company-bound JWT claims, membership revalidation, and request-scoped company context. Stage 3 adds `company_id` to all 40 tenant-owned business tables plus a nullable company association for `audit_log`, backfills the single-company baseline, and blocks tenant-owned writes when more than one company exists. Stage 4 adds 55 SQLite same-company relationship guard groups (110 INSERT/UPDATE triggers), a preflight scan that aborts on existing cross-company references, 55 relationship indexes and one audit timestamp index. However, these triggers are not actual composite foreign-key constraints; business SQL/service/report/import/export/file paths are not yet comprehensively tenant-scoped, and global unique constraints remain. Operational APIs and database writes therefore remain fail-closed when more than one company exists. The platform must still be treated as single-company for real business operations.

### High — Effective role is membership-based, but role catalog is still global

Authenticated request permissions are now loaded from the selected `company_memberships.role`; the legacy `users.role` remains for compatibility. The `roles` definitions and permission templates are still shared globally and must be made tenant-aware before enabling multiple companies. Company selection is verified by the server; a client-supplied company ID/header is never sufficient authorization.

### High — Login throttling is process-local

Failed-login counters are stored in a Python module-level dictionary protected by a thread lock. They reset on restart and are not shared across workers/instances. Replace with shared storage (e.g. Redis) or a trusted gateway rate limit before horizontally scaled production.

### High — Migration tooling is not yet production-grade

`init_db` still executes the base schema script during application initialization. Ordered SQLite migrations (versions 2, 3 and 4) now use a write lock and transaction, but there are no migration checksums, operator preflight/rollback commands, or built-in verified backup/restore workflow. Add those controls and rehearse them against a restored copy before production schema changes.

### Medium — SQLite is the only configured database backend

The current database layer directly uses `sqlite3`. PostgreSQL support requires a deliberate adapter/driver, transaction semantics, migration tooling, query compatibility work and concurrency tests. Do not treat this as a connection-string-only change.

### Medium — Browser token storage needs a session design review

The frontend API client and token lifecycle should be reviewed together. For a browser-hosted multi-tenant business system, prefer secure, HttpOnly, SameSite cookies with CSRF protection where the deployment topology permits. If bearer tokens remain, add short lifetimes, refresh/revocation, XSS defenses and strict storage controls.

### Medium — CORS and deployment headers need production validation

CORS is allowlist-based, which is a good start. Verify exact origins, HTTPS-only deployment, secure cookie behavior if adopted, HSTS at the edge, proxy trust configuration and CSP compatibility with the real frontend.

### Medium — File/document isolation needs a separate design

The current `files` data model must be checked for actual upload/storage behavior. Future object paths, download authorization, export archives and signed URLs must be tenant-scoped and checked on every access.

## Existing positive controls observed

- Password hashes use `hashlib.scrypt` with random salts and constant-time comparison.
- JWT verification uses a configured signing key and expiry; company-bound tokens include signed `cid` and `mid` claims that are revalidated against current membership and company status on every request.
- API responses set `Cache-Control: no-store`.
- Security headers include `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` and CSP.
- The API uses explicit permission checks.
- Money values are stored in integer minor units.
- Journal-entry immutability is reinforced by SQLite triggers.

## Stage 3-specific transitional constraints

- SQLite adds a temporary default pointing to the one bootstrap company so the legacy single-company service code continues to create owned rows. This must be replaced with explicit request-scoped `company_id` writes before multi-company operation is enabled.
- `settings(key)` and `sequences(name)` remain globally unique, as do legacy master/account/document identifiers. Company-scoped unique rules remain pending.
- A multi-company database cannot be upgraded automatically by Stage 3; the migration refuses to infer record ownership. Use a verified single-company baseline or prepare a manual mapping plan.
- Stage 4 adds SQLite triggers for 55 tenant-to-tenant relationship groups. These are defense-in-depth and do not constitute composite foreign-key constraints or replace tenant-aware application queries / PostgreSQL RLS.

## Validation limitations

- Python syntax compilation and JavaScript syntax checks can be performed locally without installing the application dependencies.
- Full backend tests were not run in this environment because Flask is not installed and package installation/network access is unavailable.
- No browser E2E suite is present in the archive.
- Static review does not prove the absence of vulnerabilities. A dependency audit, dynamic test, authorization matrix and penetration test remain required.

## Required security tests for tenancy

1. Cross-tenant read/write/delete attempts for every resource.
2. IDOR tests by swapping record IDs between companies.
3. Cross-tenant foreign-key injection.
4. Company-switch token/session revocation and stale browser cache tests (route tests have been authored; execution remains pending the Flask test environment).
5. Export, import, report, file, audit and background-job isolation.
6. Concurrent requests using a pooled PostgreSQL connection to detect tenant-context leakage.
7. Suspended membership/company and role-change enforcement.
8. RLS bypass prevention for normal application roles.
