# ERP Global Pro — Recovery and Provenance Note

Historical recovery note: assembled at Stage 9; superseded by ERP_GLOBAL_PRO_CONTINUITY_AR.md (Stage 10, 2026-11-13)

## Recovered source files

| File | Size | SHA-256 | Purpose |
|---|---:|---|---|
| `erp-global-pro.zip` | 167,806 bytes | `7943d5f5b1f027463a9ef2bd65116286ad76abdcde7db2e90bcedb4434e9895b` | Original uploaded source; immutable baseline |
| `erp-global-pro-stage2.zip` | 206,414 bytes | `5e94439e3caf98369a41460c0678b27e9b17f21a9ac998153c78c5a9111dc452` | Latest recoverable implementation archive |
| `erp-global-pro-architecture.zip` | 384,420 bytes | `bab9256c5f04292ea72b8cfab0c2ad6081846f20b3d9faa35d2b0f0108e06352` | Original source plus architecture/decision/security/backlog documentation |

The original archive SHA-256 matches the previously recorded value. The original ZIP was not modified.

## Stage archive availability and reconciliation

The personal Library listing included the original archive, Stage 1, Stage 2 and architecture edition. The old Stage 3 and Stage 4 ZIPs from the earlier conversation were not present in the Library. The Stage 3 package delivered in the active workspace is explicitly a rebuilt v3 checkpoint from Stage 2. A separate unarchived worktree was then found containing v4 relationship guards; it was reconciled into a new `erp-global-pro-stage4-reconciled.zip`, not represented as the byte-for-byte recovery of the missing old Stage 4 ZIP.

The current Stage 5 checkpoint is built from the reconciled v4 worktree and adds a read-only uniqueness preflight utility only. It does not alter schema version 4 or apply unique-key migrations.

## Repository status

The authenticated GitHub repository listing for account `HVA09` showed `ai-chat-app`, `lumen-learning-platform` and other unrelated repositories; no ERP repository was listed. A repository search scoped to `user:HVA09` for ERP returned no matches. No ERP code has been pushed to GitHub, and no existing repository was modified.

Recommended future repository name: `HVA09/erp-global-pro` (independent repository). This is a recommendation only; the repository has not been created.

## Worktree integrity

- Original ZIP is kept separate from the Stage 2 source extraction and Stage 3 worktree.
- No GitHub repository, production database, hosted application or deployment was changed.
- The new Stage 3 ZIP is a development artifact only and must not be deployed as a multi-company production release.


## Stage 3 / Stage 4 reconciliation update (2026-11-13)

A separate unarchived Stage 4 working tree was found in the active workspace. Inspection confirmed the delivered `erp-global-pro-stage3-rebuilt.zip` itself contains migration version 3 only, while the separate working tree contains migration version 4. Its Stage 3 changelog/test assertions were stale relative to v4: the Stage 3 tests asserted the latest migration was version 3. The v4 migration code expected 55 tenant-to-tenant FK relationship groups and creates 110 INSERT/UPDATE guard triggers, 55 relationship indexes and one additional audit `(company_id, ts)` index (56 indexes total).

The previous separate Stage 4 ZIP still was not present in Library. Rather than claiming it was restored byte-for-byte, this update reconciles the implementation that was present in the separate unarchived Stage 4 worktree: Stage 3 tests now explicitly target version 3; dedicated Stage 4 relationship tests were added; documentation now records the difference between SQLite trigger guards and true composite foreign keys. Focused combined test result after reconciliation: 21/21 PASS.

`erp-global-pro-stage4-reconciled.zip` is a new checkpoint built from the separate unarchived Stage 4 worktree. The Stage 3 ZIP remains a distinct v3-only checkpoint, and this package is not the exact old Stage 4 archive. It does not imply GitHub upload or production deployment.


## Stage 5 read-only preflight checkpoint (2026-11-13)

`backend/scripts/tenant_uniqueness_preflight.py` inspects 15 planned company-scoped business keys using SQLite `mode=ro` and `PRAGMA query_only=ON`. It emits duplicate groups per company and redacts key values by default. It does not mutate any database or change schema version. Nine focused unit tests cover duplicate detection, partial predicates, NULL behavior, missing ownership, missing schema, read-only enforcement, JSON output, and CLI exit codes. Combined Stage 2–5 focused tests pass 30/30. This is a tool checkpoint only; a real database duplicate audit still requires a user-provided restored backup.


## Stage 6 static tenancy query audit (2026-11-13)

A read-only source audit is recorded in `ERP_STAGE_6_TENANT_QUERY_AUDIT.md`. The generic CRUD layer has no automatic `company_id` scope for list/get/create/update/delete, and reference validation checks IDs globally. A static search identified 68 direct DB call sites in API modules and 172 in service modules; the core business modules do not show explicit `company_id` query filters in source. `services/sequences.py`, `services/settings.py`, import/export and audit listing were inspected as specific high-risk examples. This stage changed documentation only; no runtime SQL was modified. The existing app guard continues to reject operational API requests when more than one company exists and `TENANT_DATA_SCOPING_ENABLED=False`.


Stage 6 validation record: the static query-scope audit is documented in `ERP_STAGE_6_TENANT_QUERY_AUDIT.md`, with its scope and release decision in `ERP_STAGE_6_AUDIT_VALIDATION.txt`. It changed documentation only. The code checkpoint still has 30/30 focused tests and 18/18 frontend JavaScript syntax checks passing; full Flask/E2E/PostgreSQL tests remain unavailable or unrun.


## Stage 7 generic CRUD tenant scope checkpoint (2026-11-13)

The Stage 7 package is built from the verified Stage 6 checkpoint and modifies only the shared generic CRUD engine plus focused tests and documentation. It does not change the SQLite schema version (still 4) or any real database.

Changes in `backend/erp/crud.py`:
- Resolve the active company exclusively from verified `g.company` context.
- Fail closed when no active company exists or when the Resource table has no `company_id` column.
- Add tenant predicates to list/count/get/update/delete.
- Inject `company_id` from trusted server context on create; reject client-supplied `company_id`.
- Validate `F(type='ref')` values using both reference ID and active company ID.

Added `backend/tests/test_tenant_crud_scoping.py` with 11 contract tests covering list/count, IDOR-style get/update/delete, server-side ownership assignment, body spoofing, cross-company refs, missing context and missing ownership columns. Combined focused Stage 2–7 tests pass 41/41. `compileall` passed; 19/19 frontend JS files pass `node --check`; AST validation confirms 21/21 generic CRUD resources map to tenant-owned tables.

Limitations: Flask is not installed in the environment, so the full Flask test suite and actual HTTP API integration were not run. Service callbacks/side effects, bespoke APIs/services, settings, sequences, reports, audit listing, imports/exports, files, background jobs, PostgreSQL composite FKs and RLS remain incomplete. `TENANT_DATA_SCOPING_ENABLED` remains false. Do not operate multiple companies or deploy as multi-tenant production.

New files: `docs/ERP_STAGE_7_CRUD_SCOPE_CHANGELOG_AR.md`, `docs/ERP_STAGE_7_VALIDATION.txt`, and the updated `docs/ERP_PROJECT_CONTINUITY_AR.md`. New package: `erp-global-pro-stage7-crud-scope.zip`. This checkpoint has not been uploaded to GitHub.


## Stage 8 callback / side-effect audit checkpoint (2026-11-13)

Stage 8 is a read-only audit; no runtime code or database schema changed. The audit report is `docs/ERP_STAGE_8_CALLBACK_AUDIT_AR.md`. It inventories callbacks in `resources.py` and traces the critical opening-stock path from product creation through `settings.default_warehouse_id()`, `inventory.opening_stock()` / `adjust()` / stock ledger writes, and `accounting.post_entry()`. It also identifies aggregate subqueries in partner/product/account/project resources that lack explicit company predicates.

P0 issues: inventory and accounting helpers do not consistently accept an explicit company_id; `settings` remains global key/value and reads all rows; `sequences` remains globally keyed; opening-stock side effects must write company_id explicitly and remain atomic with the journal entry. Stage 8 has no runtime code change.

Stage 7 regression suite remains 41/41 focused tests; compileall PASS; 19/19 frontend JavaScript files pass node --check; 21/21 CRUD resource definitions map to TENANT_TABLES. Flask integration tests, two-company financial integration, E2E, PostgreSQL/RLS remain unrun. Keep `TENANT_DATA_SCOPING_ENABLED=False`. The latest package is `erp-global-pro-stage8-callback-audit.zip`, not uploaded to GitHub.


## Stage 9 — Company-scoped inventory/accounting/settings/sequences (2026-11-13)

- SQLite schema version is now 5. `settings` uses `(company_id,key)`; `sequences` still has a global name primary key and is fail-closed across companies.
- Services now accept/pass a trusted company context for settings, inventory movements and counts/reports, accounting posting/reversal and financial reports. Resource-level aggregate joins are tenant-constrained.
- Added service contract tests using actual modules with a lightweight SQLite/Flask test adapter. Tenant focused suite: 52/52 PASS; Python compileall PASS; frontend JavaScript syntax 19/19 PASS.
- Flask is unavailable, so full HTTP/API tests were not run. PostgreSQL/RLS and browser E2E remain unimplemented/unrun.
- Keep `TENANT_DATA_SCOPING_ENABLED=False`. Do not activate multiple-company operations; trade/payments/HR/manufacturing and document-number uniqueness still require work.
- Next checkpoint: Stage 10 sequences/document numbering and composite business uniqueness migration, following read-only preflight on a restored DB copy.
- Stage 9 source checkpoint is `erp-global-pro-stage9-service-scope.zip`; it has not been uploaded to GitHub.
