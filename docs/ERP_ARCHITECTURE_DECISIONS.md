# Architecture Decision Records — ERP Global Pro

## ADR-001: Multi-tenant platform for all sectors

- **Status:** Accepted
- **Date:** 2026-11-13
- **Decision:** Build ERP Global Pro as one platform serving multiple companies with strict tenant isolation. Support all sectors through optional industry packs.
- **Context:** The current application is a single-company Flask + SQLite ERP. The product direction is broader than a single business and must support multiple organizations without data leakage.
- **Consequences:** The database, identity model, authorization, APIs, jobs, exports, files, caching, audit and reporting must become tenant-aware. This is a cross-cutting migration, not a simple extra column.
- **Rejected shortcuts:** Trusting a company ID supplied by the browser; adding `company_id` only to a subset of tables; using global unique business keys; treating UI filtering as security.

## ADR-002: Preserve the current application and evolve incrementally

- **Status:** Accepted
- **Date:** 2026-11-13
- **Decision:** Keep the current ZIP as an immutable baseline. Implement in a separate working copy/branch with additive migrations and rollback support. Keep Flask and vanilla JavaScript for the first tenancy phases.
- **Context:** The existing ERP already contains accounting, inventory, trade, HR, CRM, projects, manufacturing and maintenance functionality. A rewrite would create unnecessary regression risk.
- **Consequences:** Existing business logic must be characterized with tests before tenancy conversion. No production migration until tenant-isolation tests and financial reconciliation pass.

## ADR-003: Shared database with PostgreSQL Row-Level Security as target

- **Status:** Accepted as target; not implemented
- **Date:** 2026-11-13
- **Decision:** Target shared PostgreSQL with mandatory tenant keys, composite constraints and RLS. SQLite remains useful for local development only if equivalent application-level isolation tests run.
- **Context:** One shared platform is cost-efficient initially, but application-only filtering is insufficient as the sole defense for sensitive financial and operational data.
- **Consequences:** Add PostgreSQL support, transaction-local tenant context, connection-pool hygiene and RLS policies. Consider schema-per-tenant or database-per-tenant only for future high-isolation enterprise tiers.

## ADR-004: Industry capabilities as modular packs

- **Status:** Accepted
- **Date:** 2026-11-13
- **Decision:** Keep a sector-neutral ERP core and activate sector-specific modules through industry packs.
- **Context:** “All sectors” cannot be delivered safely as one giant, hard-coded workflow. Education, manufacturing, retail and healthcare have different data and compliance requirements.
- **Consequences:** Define module boundaries and entitlements. Add sector packs incrementally; regulated domains require separate compliance review.
