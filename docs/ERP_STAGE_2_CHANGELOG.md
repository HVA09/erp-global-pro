# ERP Global Pro — Stage 2 Change Log

Date: 2026-11-13

## Implemented

1. Added authenticated `GET /api/auth/companies` to list the signed-in user's active company memberships and expose the currently verified company context.
2. Added authenticated `POST /api/auth/switch-company`. The server validates the submitted company against an active membership before issuing a new token.
3. Access tokens now carry signed `cid` (company ID) and `mid` (membership ID) claims when an unambiguous active membership is available. The API re-reads membership state and company status on every request; suspended/revoked memberships invalidate company-bound tokens.
4. Legacy tokens without tenant claims auto-resolve only when the user has exactly one active membership. Multiple active memberships require an explicit company selection.
5. Added request-scoped `g.company` and `g.membership` context. Effective role and permissions are derived from the selected membership, not from the global legacy `users.role` field.
6. Protected non-context API routes now require an active company context.
7. Added a fail-closed operational guard: while more than one company exists and tenant data scoping is not explicitly complete, business API operations return `503 tenant_isolation_pending`. This prevents the legacy unscoped SQL from exposing records across companies. `TENANT_DATA_SCOPING_ENABLED` remains hard-coded false in the default configuration and must not be enabled in this stage.
8. Updated company user administration to list company members and update membership-level role assignments; company administrator retention is checked within the active company.
9. Added frontend company selection for ambiguous sessions and a company switcher for multi-company sessions. A clear lock screen explains that multi-company operation remains blocked pending data isolation.
10. Added six route-level regression tests for single-company context, unauthorized switching, per-membership roles, and membership revocation.

## Validation results

| Check | Result |
|---|---|
| Existing tenant migration tests | PASS — 5/5 |
| Active-membership service contract tests | PASS — 4/4 |
| Python compile check (`compileall`) | PASS |
| Frontend JavaScript syntax (`node --check`) | PASS |
| New route-level tenant session tests | ADDED, NOT RUN — Flask is unavailable in this execution environment |
| Full backend test suite | NOT RUN — requires Flask; dependency installation remains blocked by unavailable package-index network access |
| Runtime browser test | NOT RUN — application dependencies/browser environment unavailable |

## Important limits

- No operational/accounting/inventory/HR table has been given a `company_id` yet.
- Existing services, reports, exports, imports, audit logs, files, and direct SQL reads/writes are not tenant-scoped.
- `roles` remains a shared legacy role catalog; effective role assignment is per membership, but custom role definitions are not yet company-owned.
- There is not yet a company-creation/invitation workflow for normal administrators.
- SQLite remains the only database adapter. PostgreSQL and RLS are not implemented.
- Therefore multi-company switching is an identity/context capability only. If multiple companies exist, operational endpoints are deliberately blocked until all data paths are isolated and tested.

## Preservation and deployment

- All work is performed in a new working copy extracted from the Stage 1 package.
- The uploaded original ZIP and the Stage 1 package are not modified.
- No GitHub repository, live deployment, or production database was changed.
- Do not set `TENANT_DATA_SCOPING_ENABLED=True` or deploy this as a multi-company operating release.
