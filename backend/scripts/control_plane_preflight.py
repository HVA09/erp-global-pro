#!/usr/bin/env python3
"""Read-only SQLite v8 preflight for the future PostgreSQL control-plane ETL.

The report contains metadata and findings only. It never prints password hashes,
plaintext credentials, or permission values. Run it against a verified,
restored/offline SQLite copy, not a live production database.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPECTED_SCHEMA_VERSION = 8
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALLOWED_COMPANY_STATUS = {"active", "suspended", "closed"}
ALLOWED_MEMBERSHIP_STATUS = {"active", "invited", "suspended"}

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "schema_version": {"version"},
    "roles": {"name", "label", "permissions", "is_system"},
    "users": {"id", "email", "name", "password_hash", "role", "is_active", "last_login", "created_at"},
    "companies": {"id", "name", "industry", "country", "currency", "timezone", "status", "created_at"},
    "company_memberships": {
        "id", "company_id", "user_id", "role", "status", "invited_by", "joined_at", "created_at"
    },
}


def _quote(identifier: str) -> str:
    """Quote identifiers from this module's static allowlist."""
    return '"' + identifier.replace('"', '""') + '"'


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    if db_path.is_symlink():
        raise ValueError("The SQLite source path must not be a symlink")
    resolved = db_path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("The SQLite source path must be a regular file")
    conn = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    if int(conn.execute("PRAGMA query_only").fetchone()[0]) != 1:
        conn.close()
        raise RuntimeError("SQLite query_only could not be enabled")
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_scrypt_hash(value: Any) -> bool:
    """Validate structure only; this cannot verify a password without plaintext."""
    if not isinstance(value, str):
        return False
    parts = value.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n, r, p = (int(parts[i]) for i in (1, 2, 3))
        salt = base64.b64decode(parts[4], validate=True)
        derived_key = base64.b64decode(parts[5], validate=True)
    except (ValueError, TypeError):
        return False
    return (
        n >= 2 and n & (n - 1) == 0
        and r > 0 and p > 0
        and len(salt) >= 16
        and len(derived_key) >= 16
    )


def _valid_timestamp(value: Any) -> bool:
    if value in (None, ""):
        return True
    if not isinstance(value, str):
        return False
    normalized = value.strip()
    if not normalized:
        return True
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    # SQLite datetime('now') values have no offset but are UTC by SQLite contract.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.utcoffset() is not None


def _finding(code: str, message: str, severity: str = "blocker", count: int = 1) -> dict[str, Any]:
    return {"code": code, "severity": severity, "count": int(count), "message": message}


def build_report(
    db_path: str | Path,
    *,
    include_email_values: bool = False,
) -> dict[str, Any]:
    original = Path(db_path).expanduser()
    if original.is_symlink():
        raise ValueError("The SQLite source path must not be a symlink")
    path = original.resolve(strict=True)
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    table_counts: dict[str, int | None] = {}
    details: dict[str, Any] = {}

    conn = _open_readonly(path)
    try:
        present = _table_names(conn)
        missing_tables = sorted(set(REQUIRED_COLUMNS) - present)
        missing_columns = {
            table: sorted(REQUIRED_COLUMNS[table] - _columns(conn, table))
            for table in REQUIRED_COLUMNS if table in present
        }
        missing_columns = {k: v for k, v in missing_columns.items() if v}
        if missing_tables:
            blockers.append(_finding(
                "missing_required_tables",
                "Required control-plane tables are missing: " + ", ".join(missing_tables),
                count=len(missing_tables),
            ))
        if missing_columns:
            blockers.append(_finding(
                "missing_required_columns",
                "Required columns are missing from one or more control-plane tables.",
                count=sum(len(v) for v in missing_columns.values()),
            ))

        for table in REQUIRED_COLUMNS:
            if table in present:
                table_counts[table] = int(
                    conn.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0]
                )
            else:
                table_counts[table] = None

        if missing_tables or missing_columns:
            return {
                "report_type": "erp_global_pro_control_plane_preflight",
                "report_version": 1,
                "classification": "read-only SQLite control-plane inspection; not a migration",
                "source_database": path.name,
                "source_database_size_bytes": path.stat().st_size,
                "source_database_sha256": _file_sha256(path),
                "read_only": True,
                "expected_schema_version": EXPECTED_SCHEMA_VERSION,
                "table_counts": table_counts,
                "missing_tables": missing_tables,
                "missing_columns": missing_columns,
                "summary": {"blocker_count": len(blockers), "warning_count": 0, "ready_for_import": False},
                "blockers": blockers,
                "warnings": warnings,
                "details": details,
            }

        versions = [
            int(row[0]) for row in conn.execute("SELECT version FROM schema_version").fetchall()
        ]
        schema_version = max(versions, default=0)
        if schema_version != EXPECTED_SCHEMA_VERSION:
            blockers.append(_finding(
                "unexpected_schema_version",
                f"Expected SQLite schema v{EXPECTED_SCHEMA_VERSION}; found v{schema_version}.",
            ))
        if len(versions) != 1:
            warnings.append(_finding(
                "multiple_schema_version_rows",
                "schema_version contains more than one row; maximum version is reported.",
                severity="warning",
                count=len(versions),
            ))

        integrity = [str(row[0]) for row in conn.execute("PRAGMA integrity_check").fetchmany(20)]
        if integrity != ["ok"]:
            blockers.append(_finding(
                "sqlite_integrity_check_failed",
                "SQLite integrity_check did not return exactly 'ok'.",
                count=len(integrity),
            ))
        fk_rows = conn.execute("PRAGMA foreign_key_check").fetchmany(101)
        if fk_rows:
            blockers.append(_finding(
                "foreign_key_violations",
                "SQLite foreign_key_check found one or more violations.",
                count=len(fk_rows),
            ))

        roles = conn.execute(
            "SELECT name, label, permissions, is_system FROM roles ORDER BY name"
        ).fetchall()
        role_names = {str(row["name"]) for row in roles}
        invalid_role_permissions: list[str] = []
        for row in roles:
            try:
                permissions = json.loads(row["permissions"])
            except (TypeError, json.JSONDecodeError):
                invalid_role_permissions.append(str(row["name"]))
                continue
            if not isinstance(permissions, list) or any(not isinstance(item, str) for item in permissions):
                invalid_role_permissions.append(str(row["name"]))
            if not isinstance(row["is_system"], (int, bool)) or int(row["is_system"]) not in (0, 1):
                blockers.append(_finding(
                    "invalid_role_is_system",
                    "One or more roles have an invalid is_system value.",
                ))
                break
        if invalid_role_permissions:
            blockers.append(_finding(
                "invalid_role_permissions_json",
                "Role permissions must be valid JSON arrays containing strings only.",
                count=len(invalid_role_permissions),
            ))
            details["roles_with_invalid_permissions"] = invalid_role_permissions

        users = conn.execute(
            "SELECT id,email,name,password_hash,role,is_active,last_login,created_at FROM users ORDER BY id"
        ).fetchall()
        users_by_id = {int(row["id"]): row for row in users}
        normalized_emails: dict[str, list[int]] = defaultdict(list)
        invalid_email_ids: list[int] = []
        invalid_hash_ids: list[int] = []
        invalid_user_timestamps: list[int] = []
        unknown_legacy_roles: list[int] = []
        invalid_active_ids: list[int] = []

        for row in users:
            user_id = int(row["id"])
            email = row["email"]
            if not isinstance(email, str) or not EMAIL_RE.fullmatch(email.strip()):
                invalid_email_ids.append(user_id)
            else:
                normalized_emails[email.strip().casefold()].append(user_id)
            if not _valid_scrypt_hash(row["password_hash"]):
                invalid_hash_ids.append(user_id)
            if str(row["role"]) not in role_names:
                unknown_legacy_roles.append(user_id)
            if row["is_active"] not in (0, 1, False, True):
                invalid_active_ids.append(user_id)
            if not _valid_timestamp(row["created_at"]) or not _valid_timestamp(row["last_login"]):
                invalid_user_timestamps.append(user_id)

        email_collisions = [
            {"user_ids": ids, **({"canonical_email": key} if include_email_values else {})}
            for key, ids in sorted(normalized_emails.items()) if len(ids) > 1
        ]
        if email_collisions:
            blockers.append(_finding(
                "canonical_email_collisions",
                "Multiple users resolve to the same canonical email after strip().casefold().",
                count=len(email_collisions),
            ))
        if invalid_email_ids:
            blockers.append(_finding(
                "invalid_user_emails",
                "One or more users have an empty or invalid email address.",
                count=len(invalid_email_ids),
            ))
        if invalid_hash_ids:
            blockers.append(_finding(
                "unsupported_password_hashes",
                "One or more password hashes do not match the supported scrypt record format.",
                count=len(invalid_hash_ids),
            ))
        if unknown_legacy_roles:
            blockers.append(_finding(
                "unknown_legacy_user_roles",
                "One or more legacy users.role values do not exist in roles.name.",
                count=len(unknown_legacy_roles),
            ))
        if invalid_active_ids:
            blockers.append(_finding(
                "invalid_user_active_values",
                "users.is_active must contain only 0 or 1.",
                count=len(invalid_active_ids),
            ))
        if invalid_user_timestamps:
            blockers.append(_finding(
                "invalid_user_timestamps",
                "One or more user timestamps are not in a recognized ISO/SQLite datetime format.",
                count=len(invalid_user_timestamps),
            ))

        companies = conn.execute(
            "SELECT id,name,industry,country,currency,timezone,status,created_at,updated_at "
            "FROM companies ORDER BY id"
        ).fetchall()
        companies_by_id = {int(row["id"]): row for row in companies}
        invalid_company_status_ids = [
            int(row["id"]) for row in companies if row["status"] not in ALLOWED_COMPANY_STATUS
        ]
        invalid_company_fields: list[int] = []
        invalid_company_timestamps: list[int] = []
        for row in companies:
            company_id = int(row["id"])
            if not isinstance(row["name"], str) or not row["name"].strip():
                invalid_company_fields.append(company_id)
            currency = row["currency"]
            if not isinstance(currency, str) or len(currency.strip()) != 3:
                invalid_company_fields.append(company_id)
            if not _valid_timestamp(row["created_at"]) or not _valid_timestamp(row["updated_at"]):
                invalid_company_timestamps.append(company_id)
        if not companies:
            blockers.append(_finding("no_companies", "The source database contains no companies."))
        if invalid_company_status_ids:
            blockers.append(_finding(
                "invalid_company_statuses",
                "One or more company statuses are not in the supported set.",
                count=len(invalid_company_status_ids),
            ))
        if invalid_company_fields:
            blockers.append(_finding(
                "invalid_company_fields",
                "One or more companies have an empty name or invalid three-letter currency.",
                count=len(set(invalid_company_fields)),
            ))
        if invalid_company_timestamps:
            blockers.append(_finding(
                "invalid_company_timestamps",
                "One or more company timestamps are not in a recognized ISO/SQLite datetime format.",
                count=len(invalid_company_timestamps),
            ))

        memberships = conn.execute(
            "SELECT id,company_id,user_id,role,status,invited_by,joined_at,created_at "
            "FROM company_memberships ORDER BY id"
        ).fetchall()
        membership_keys: dict[tuple[int, int], list[int]] = defaultdict(list)
        orphan_membership_ids: list[int] = []
        unknown_membership_roles: list[int] = []
        invalid_membership_status_ids: list[int] = []
        invalid_membership_timestamps: list[int] = []
        active_memberships_for_inactive_identity: list[int] = []
        legacy_role_mismatches: list[int] = []
        memberships_by_user: dict[int, set[str]] = defaultdict(set)

        for row in memberships:
            membership_id = int(row["id"])
            company_id = int(row["company_id"])
            user_id = int(row["user_id"])
            membership_keys[(company_id, user_id)].append(membership_id)
            if company_id not in companies_by_id or user_id not in users_by_id:
                orphan_membership_ids.append(membership_id)
            if str(row["role"]) not in role_names:
                unknown_membership_roles.append(membership_id)
            if row["status"] not in ALLOWED_MEMBERSHIP_STATUS:
                invalid_membership_status_ids.append(membership_id)
            if row["invited_by"] is not None and int(row["invited_by"]) not in users_by_id:
                orphan_membership_ids.append(membership_id)
            if not _valid_timestamp(row["created_at"]) or not _valid_timestamp(row["joined_at"]):
                invalid_membership_timestamps.append(membership_id)
            memberships_by_user[user_id].add(str(row["role"]))
            if row["status"] == "active" and (
                user_id not in users_by_id
                or not bool(users_by_id[user_id]["is_active"])
                or company_id not in companies_by_id
                or companies_by_id[company_id]["status"] != "active"
            ):
                active_memberships_for_inactive_identity.append(membership_id)

        duplicate_membership_groups = [
            ids for ids in membership_keys.values() if len(ids) > 1
        ]
        for user_id, membership_roles in memberships_by_user.items():
            user = users_by_id.get(user_id)
            if user and membership_roles and str(user["role"]) not in membership_roles:
                legacy_role_mismatches.append(user_id)

        if orphan_membership_ids:
            blockers.append(_finding(
                "orphan_memberships",
                "One or more memberships reference a missing user, company or invited_by user.",
                count=len(set(orphan_membership_ids)),
            ))
        if unknown_membership_roles:
            blockers.append(_finding(
                "unknown_membership_roles",
                "One or more memberships reference a role absent from roles.name.",
                count=len(unknown_membership_roles),
            ))
        if invalid_membership_status_ids:
            blockers.append(_finding(
                "invalid_membership_statuses",
                "One or more membership statuses are not in the supported set.",
                count=len(invalid_membership_status_ids),
            ))
        if invalid_membership_timestamps:
            blockers.append(_finding(
                "invalid_membership_timestamps",
                "One or more membership timestamps are not in a recognized ISO/SQLite datetime format.",
                count=len(invalid_membership_timestamps),
            ))
        if duplicate_membership_groups:
            blockers.append(_finding(
                "duplicate_company_user_memberships",
                "More than one membership exists for a company/user pair.",
                count=len(duplicate_membership_groups),
            ))
        if active_memberships_for_inactive_identity:
            blockers.append(_finding(
                "active_membership_for_inactive_identity",
                "An active membership belongs to an inactive user or a non-active company.",
                count=len(active_memberships_for_inactive_identity),
            ))
        if legacy_role_mismatches:
            warnings.append(_finding(
                "legacy_user_role_differs_from_memberships",
                "users.role differs from one or more membership roles; this is legacy metadata, not an authorization source.",
                severity="warning",
                count=len(legacy_role_mismatches),
            ))

        if not users:
            blockers.append(_finding("no_users", "The source database contains no users."))
        if not roles:
            blockers.append(_finding("no_roles", "The source database contains no role definitions."))

        details.update({
            "canonical_email_collisions": email_collisions,
            "users_with_invalid_email": invalid_email_ids,
            "users_with_unsupported_password_hash": invalid_hash_ids,
            "users_with_unknown_legacy_role": unknown_legacy_roles,
            "companies_with_invalid_status": invalid_company_status_ids,
            "memberships_with_orphan_references": sorted(set(orphan_membership_ids)),
            "memberships_with_unknown_role": unknown_membership_roles,
            "memberships_with_invalid_status": invalid_membership_status_ids,
            "memberships_with_invalid_timestamps": invalid_membership_timestamps,
            "active_memberships_for_inactive_identity": active_memberships_for_inactive_identity,
            "users_with_legacy_role_mismatch": legacy_role_mismatches,
        })

        summary = {
            "users": len(users),
            "roles": len(roles),
            "companies": len(companies),
            "company_memberships": len(memberships),
            "canonical_email_collision_groups": len(email_collisions),
            "unsupported_password_hashes": len(invalid_hash_ids),
            "orphan_memberships": len(set(orphan_membership_ids)),
            "unknown_membership_roles": len(unknown_membership_roles),
            "invalid_role_permissions": len(invalid_role_permissions),
            "legacy_role_mismatches": len(legacy_role_mismatches),
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "ready_for_control_plane_import": not blockers,
        }
        return {
            "report_type": "erp_global_pro_control_plane_preflight",
            "report_version": 1,
            "classification": "read-only SQLite control-plane inspection; not a migration",
            "source_database": path.name,
            "source_database_size_bytes": path.stat().st_size,
            "source_database_sha256": _file_sha256(path),
            "read_only": True,
            "expected_schema_version": EXPECTED_SCHEMA_VERSION,
            "schema_version": schema_version,
            "table_counts": table_counts,
            "sqlite_integrity_check": integrity,
            "sqlite_foreign_key_violations_sampled": len(fk_rows),
            "summary": summary,
            "blockers": blockers,
            "warnings": warnings,
            "details": details,
            "privacy": {
                "password_hashes_included": False,
                "plaintext_credentials_included": False,
                "email_values_included": bool(include_email_values),
            },
            "limitations": [
                "This report validates control-plane shape and candidate migration blockers only; it does not migrate data.",
                "Password hashes are structure-checked only; password verification requires synthetic credential tests in a separate test suite.",
                "Email canonicalization uses Python strip().casefold(); collisions must be reviewed before adding a target unique constraint.",
                "SQLite default datetime('now') values are UTC; other timestamp forms must be checked before PostgreSQL conversion.",
                "A clean control-plane preflight does not prove business-data tenant isolation or PostgreSQL application readiness.",
            ],
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", help="Path to a verified restored/offline SQLite v8 copy")
    parser.add_argument("--output", help="Optional JSON report path")
    parser.add_argument(
        "--include-email-values",
        action="store_true",
        help="Include canonical email values in collision details; protect the report as PII",
    )
    parser.add_argument(
        "--fail-on-blockers",
        action="store_true",
        help="Return exit code 2 when the source is not ready for control-plane import",
    )
    args = parser.parse_args(argv)

    try:
        report = build_report(args.database, include_email_values=args.include_email_values)
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            output = Path(args.output).expanduser().resolve()
            source = Path(args.database).expanduser().resolve(strict=True)
            if output == source:
                parser.error("--output must not point to the SQLite database")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
            print(f"JSON report written to: {output}")
        else:
            print(rendered, end="")
        print(
            "Control-plane preflight: "
            + ("READY FOR DISPOSABLE IMPORT" if report["summary"]["ready_for_control_plane_import"] else "BLOCKED")
        )
        if args.fail_on_blockers and report["blockers"]:
            return 2
        return 0
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print(f"Control-plane preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
