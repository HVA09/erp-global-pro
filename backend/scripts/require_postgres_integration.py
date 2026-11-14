"""Fail fast when the required live PostgreSQL integration environment is absent."""
from __future__ import annotations

import os
import sys


def main() -> int:
    dsn = os.environ.get("ERP_TEST_POSTGRES_ADMIN_DSN", "").strip()
    if not dsn:
        print("ERROR: ERP_TEST_POSTGRES_ADMIN_DSN is required", file=sys.stderr)
        return 2

    try:
        import psycopg
        import psycopg_pool  # noqa: F401
    except ImportError as exc:
        print(f"ERROR: psycopg[binary,pool] is required: {exc}", file=sys.stderr)
        return 2

    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            row = conn.execute(
                "SELECT current_database(), current_user, current_setting('server_version_num')"
            ).fetchone()
    except Exception as exc:  # pragma: no cover - external service-dependent
        print(f"ERROR: PostgreSQL connection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(
        "PostgreSQL integration environment ready: "
        f"database={row[0]} user={row[1]} server_version_num={row[2]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
