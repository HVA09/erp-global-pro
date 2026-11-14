#!/usr/bin/env python3
"""Read-only preflight for future company-scoped unique constraints.

This tool inspects an existing SQLite database without changing it. It reports
candidate-key duplicates *within each company* before any uniqueness migration
is considered. It is intentionally independent of Flask so it can be run from
a maintenance environment with only Python's standard library installed.

Example:
    python backend/scripts/tenant_uniqueness_preflight.py /safe/copy/erp.db
    python backend/scripts/tenant_uniqueness_preflight.py /safe/copy/erp.db \
        --output /safe/copy/tenant-uniqueness-report.json

By default, values of business keys are redacted. Use --include-values only
when the report is stored in a protected location and key values are needed
for manual reconciliation. This script never repairs, deletes, or changes rows.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


# Static, reviewed identifiers only. The partial predicates mirror the legacy
# schema's business rules. Never accept SQL identifiers or predicates from CLI.
RULES: tuple[dict[str, Any], ...] = (
    {"id": "accounts_code", "table": "accounts", "columns": ("code",)},
    {"id": "attendance_employee_date", "table": "attendance", "columns": ("employee_id", "date")},
    {"id": "categories_name", "table": "categories", "columns": ("name",)},
    {"id": "cost_centers_name", "table": "cost_centers", "columns": ("name",)},
    {"id": "departments_name", "table": "departments", "columns": ("name",)},
    {"id": "depreciation_asset_period", "table": "depreciation_postings", "columns": ("asset_id", "period")},
    {"id": "journal_entries_number", "table": "journal_entries", "columns": ("number",)},
    {
        "id": "payroll_runs_posted_month",
        "table": "payroll_runs",
        "columns": ("month",),
        "where": "status = 'posted'",
        "where_description": "status = 'posted'",
        "required_extra_columns": ("status",),
    },
    {"id": "products_sku", "table": "products", "columns": ("sku",)},
    {
        "id": "products_barcode_nonnull",
        "table": "products",
        "columns": ("barcode",),
        "where": "barcode IS NOT NULL",
        "where_description": "barcode IS NOT NULL",
    },
    {"id": "sequences_name", "table": "sequences", "columns": ("name",)},
    {"id": "settings_key", "table": "settings", "columns": ("key",)},
    {"id": "stock_levels_product_warehouse", "table": "stock_levels", "columns": ("product_id", "warehouse_id")},
    {
        "id": "trade_documents_type_number_nonnull",
        "table": "trade_documents",
        "columns": ("doc_type", "number"),
        "where": "number IS NOT NULL",
        "where_description": "number IS NOT NULL",
    },
    {"id": "warehouses_code", "table": "warehouses", "columns": ("code",)},
)

GLOBAL_IDENTITIES = (
    {"table": "users", "columns": ("email",), "reason": "Global login identity; do not tenant-scope."},
    {"table": "roles", "columns": ("name",), "reason": "Shared role catalog; redesign separately before tenant custom roles."},
)


def _quote(identifier: str) -> str:
    """Quote an identifier from this module's reviewed static metadata."""
    return '"' + identifier.replace('"', '""') + '"'


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    resolved = db_path.expanduser().resolve(strict=True)
    uri = resolved.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    # Defend against accidental changes even if a future edit adds a write.
    if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
        conn.close()
        raise RuntimeError("SQLite query_only could not be enabled")
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()}


def _rule_result(
    conn: sqlite3.Connection,
    rule: dict[str, Any],
    *,
    include_values: bool,
    max_groups: int,
    max_rowids: int,
) -> dict[str, Any]:
    table = rule["table"]
    key_columns = tuple(rule["columns"])
    present_tables = _table_names(conn)
    if table not in present_tables:
        return {
            "id": rule["id"], "table": table, "key_columns": list(key_columns),
            "status": "missing_table", "duplicate_groups": 0, "affected_rows": 0,
        }

    present_columns = _columns(conn, table)
    required_columns = {"company_id", *key_columns, *rule.get("required_extra_columns", ())}
    missing_columns = sorted(required_columns - present_columns)
    if missing_columns:
        return {
            "id": rule["id"], "table": table, "key_columns": list(key_columns),
            "status": "missing_columns", "missing_columns": missing_columns,
            "duplicate_groups": 0, "affected_rows": 0,
        }

    quoted_table = _quote(table)
    quoted_keys = [_quote(c) for c in key_columns]
    non_null_keys = " AND ".join(f"{c} IS NOT NULL" for c in quoted_keys)
    where_parts = ["company_id IS NOT NULL", non_null_keys]
    if rule.get("where"):
        where_parts.append(f"({rule['where']})")
    where_sql = " AND ".join(where_parts)
    group_cols = ", ".join(["company_id", *quoted_keys])
    grouped = (
        f"SELECT company_id, {', '.join(quoted_keys)}, COUNT(*) AS row_count "
        f"FROM {quoted_table} WHERE {where_sql} "
        f"GROUP BY {group_cols} HAVING COUNT(*) > 1"
    )

    summary = conn.execute(
        f"SELECT COUNT(*) AS duplicate_groups, COALESCE(SUM(row_count), 0) AS affected_rows "
        f"FROM ({grouped}) AS duplicate_keys"
    ).fetchone()
    unowned_rows = int(conn.execute(
        f"SELECT COUNT(*) FROM {quoted_table} WHERE company_id IS NULL"
    ).fetchone()[0])
    groups = conn.execute(
        grouped + f" ORDER BY company_id, {', '.join(quoted_keys)} LIMIT ?",
        (max_groups + 1,),
    ).fetchall()

    truncated = len(groups) > max_groups
    groups = groups[:max_groups]
    samples = []
    for group in groups:
        values = [group[column] for column in ("company_id", *key_columns)]
        match_terms = ["company_id IS ?"]
        match_terms.extend(f"{column} IS ?" for column in quoted_keys)
        row_sql = (
            f"SELECT rowid AS row_id FROM {quoted_table} WHERE "
            + " AND ".join(match_terms)
        )
        if rule.get("where"):
            row_sql += f" AND ({rule['where']})"
        row_sql += " ORDER BY rowid LIMIT ?"
        row_ids = [r[0] for r in conn.execute(row_sql, (*values, max_rowids)).fetchall()]
        sample = {
            "company_id": group["company_id"],
            "row_count": group["row_count"],
            "sample_rowids": row_ids,
        }
        if include_values:
            sample["key_values"] = {col: group[col] for col in key_columns}
        samples.append(sample)

    result = {
        "id": rule["id"],
        "table": table,
        "key_columns": list(key_columns),
        "partial_predicate": rule.get("where_description"),
        "status": (
            "duplicates_found" if summary["duplicate_groups"]
            else "ownership_missing" if unowned_rows
            else "clear"
        ),
        "duplicate_groups": int(summary["duplicate_groups"]),
        "affected_rows": int(summary["affected_rows"]),
        "unowned_rows": unowned_rows,
        "samples_truncated": truncated or int(summary["duplicate_groups"]) > len(samples),
        "samples": samples,
    }
    return result


def run_preflight(
    db_path: str | Path,
    *,
    include_values: bool = False,
    max_groups: int = 20,
    max_rowids: int = 20,
) -> dict[str, Any]:
    """Inspect candidate tenant-scoped keys in a SQLite DB without modifying it."""
    if max_groups < 1 or max_groups > 1000:
        raise ValueError("max_groups must be between 1 and 1000")
    if max_rowids < 1 or max_rowids > 1000:
        raise ValueError("max_rowids must be between 1 and 1000")

    path = Path(db_path).expanduser().resolve(strict=True)
    conn = _open_readonly(path)
    try:
        tables = _table_names(conn)
        if "schema_version" in tables:
            schema_version = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
        else:
            schema_version = None
        company_count = (
            int(conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0])
            if "companies" in tables else None
        )
        integrity = [str(r[0]) for r in conn.execute("PRAGMA integrity_check").fetchmany(10)]
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchmany(100)

        rule_results = [
            _rule_result(
                conn, rule, include_values=include_values,
                max_groups=max_groups, max_rowids=max_rowids,
            )
            for rule in RULES
        ]
        unavailable = [r for r in rule_results if r["status"] in ("missing_table", "missing_columns")]
        duplicate_results = [r for r in rule_results if r["status"] == "duplicates_found"]
        ownership_results = [r for r in rule_results if r.get("unowned_rows", 0)]
        return {
            "report_type": "erp_global_pro_tenant_uniqueness_preflight",
            "report_version": 1,
            "database_filename": path.name,
            "read_only": True,
            "schema_version": schema_version,
            "company_count": company_count,
            "integrity_check": integrity,
            "foreign_key_violation_count_sampled": len(fk_violations),
            "foreign_key_violation_sample_truncated": len(fk_violations) == 100,
            "summary": {
                "rules_total": len(rule_results),
                "rules_clear": sum(r["status"] == "clear" for r in rule_results),
                "rules_with_duplicates": len(duplicate_results),
                "rules_with_unowned_rows": len(ownership_results),
                "rules_unavailable": len(unavailable),
                "duplicate_groups": sum(r["duplicate_groups"] for r in rule_results),
                "affected_rows": sum(r["affected_rows"] for r in rule_results),
                "unowned_rows": sum(r.get("unowned_rows", 0) for r in rule_results),
            },
            "global_identities_not_tenant_scoped": list(GLOBAL_IDENTITIES),
            "rules": rule_results,
            "limitations": [
                "This report checks duplicate candidate keys within each company; it does not alter schema or data.",
                "This report is not a production-data report unless it is run against a verified restored database copy.",
                "Current global UNIQUE constraints may prevent cross-company duplicates before tenant-scoped indexes are installed.",
                "SQLite trigger guards from Stage 4 are not composite foreign-key constraints.",
                "A clean report does not prove that application queries, APIs, exports, jobs, or caches are tenant-isolated.",
            ],
        }
    finally:
        conn.close()


def _render_text(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "ERP Global Pro — Tenant Uniqueness Preflight",
        f"Database: {report['database_filename']}",
        f"Read-only: {report['read_only']}",
        f"Schema version: {report['schema_version']}",
        f"Companies: {report['company_count']}",
        f"Integrity check: {', '.join(report['integrity_check'])}",
        f"Foreign-key violations (sampled): {report['foreign_key_violation_count_sampled']}",
        "",
        "Summary:",
        f"  Rules: {summary['rules_total']}",
        f"  Clear: {summary['rules_clear']}",
        f"  With duplicates: {summary['rules_with_duplicates']}",
        f"  With unowned rows: {summary['rules_with_unowned_rows']}",
        f"  Unavailable: {summary['rules_unavailable']}",
        f"  Duplicate groups: {summary['duplicate_groups']}",
        f"  Affected rows: {summary['affected_rows']}",
        f"  Rows without company_id: {summary['unowned_rows']}",
        "",
    ]
    for result in report["rules"]:
        lines.append(
            f"[{result['status']}] {result['table']}({', '.join(result['key_columns'])}) "
            f"— groups={result['duplicate_groups']}, rows={result['affected_rows']}, "
            f"unowned={result.get('unowned_rows', 0)}"
        )
        for sample in result.get("samples", []):
            lines.append(
                f"    company_id={sample['company_id']} row_count={sample['row_count']} "
                f"rowids={sample['sample_rowids']}"
            )
            if "key_values" in sample:
                lines.append(f"    key_values={sample['key_values']!r}")
    if report["summary"]["rules_unavailable"]:
        lines.extend(["", "WARNING: Some rules could not be inspected because tables/columns are missing."])
    if report["summary"]["duplicate_groups"]:
        lines.extend(["", "ACTION: Reconcile duplicates manually. This tool never changes business identifiers."])
    if report["summary"]["unowned_rows"]:
        lines.extend(["", "ACTION: Investigate rows with NULL company_id before any tenant-scoped uniqueness migration."])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", help="Path to a restored SQLite database copy")
    parser.add_argument("--output", help="Optional JSON report path; database remains read-only")
    parser.add_argument("--include-values", action="store_true", help="Include business key values in report; protect the output")
    parser.add_argument("--max-groups", type=int, default=20, help="Maximum duplicate groups per rule (1..1000)")
    parser.add_argument("--fail-on-duplicates", action="store_true", help="Return exit code 2 when duplicates are found")
    parser.add_argument("--strict-schema", action="store_true", help="Return exit code 3 if any rule cannot be inspected")
    args = parser.parse_args(argv)

    try:
        report = run_preflight(
            args.database,
            include_values=args.include_values,
            max_groups=args.max_groups,
        )
        rendered = _render_text(report)
        print(rendered)
        if args.output:
            out_path = Path(args.output).expanduser().resolve()
            db_path = Path(args.database).expanduser().resolve(strict=True)
            if out_path == db_path:
                parser.error("--output must not point to the SQLite database itself")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"\nJSON report written to: {out_path}")
        if args.fail_on_duplicates and report["summary"]["duplicate_groups"]:
            return 2
        if args.strict_schema and report["summary"]["rules_unavailable"]:
            return 3
        return 0
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print(f"Preflight failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
