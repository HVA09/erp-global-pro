"""Live PostgreSQL integration tests for the experimental Psycopg 3 adapter.

These tests require a disposable PostgreSQL database and the optional
requirements-postgres.txt dependencies. They create a temporary NOSUPERUSER
role and schema, enforce RLS on a test table, and remove both on completion.

Set ERP_TEST_POSTGRES_ADMIN_DSN to enable the suite. In CI,
REQUIRE_POSTGRES_INTEGRATION=1 makes missing dependencies/DSN a hard failure.
"""
from __future__ import annotations

import os
import secrets
import unittest
import uuid

REQUIRE_POSTGRES = os.environ.get("REQUIRE_POSTGRES_INTEGRATION", "").lower() in {
    "1", "true", "yes"
}
ADMIN_DSN = os.environ.get("ERP_TEST_POSTGRES_ADMIN_DSN", "").strip()


def _required_imports():
    try:
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        from psycopg_pool import ConnectionPool
    except ImportError as exc:  # pragma: no cover - environment-dependent branch
        if REQUIRE_POSTGRES:
            raise RuntimeError(
                "PostgreSQL integration is required but psycopg[binary,pool] is unavailable"
            ) from exc
        raise unittest.SkipTest("optional psycopg and psycopg_pool dependencies are not installed")
    return psycopg, sql, make_conninfo, ConnectionPool


class Psycopg3LivePostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ADMIN_DSN:
            if REQUIRE_POSTGRES:
                raise RuntimeError("ERP_TEST_POSTGRES_ADMIN_DSN is required for PostgreSQL integration")
            raise unittest.SkipTest("ERP_TEST_POSTGRES_ADMIN_DSN is not configured")

        cls.psycopg, cls.sql, cls.make_conninfo, cls.ConnectionPool = _required_imports()
        # Import the app package only after the opt-in guard; normal local
        # unittest discovery can skip this module without Flask installed.
        from erp.db_adapters.postgres_psycopg import Psycopg3Adapter
        cls.adapter = Psycopg3Adapter()
        suffix = uuid.uuid4().hex[:16]
        cls.role = f"erp_stage22_{suffix}"
        cls.schema = f"erp_stage22_{suffix}"
        cls.password = secrets.token_urlsafe(24)
        cls.admin = cls.psycopg.connect(ADMIN_DSN, autocommit=True)
        cls.app_dsn = None
        try:
            cls.admin.execute(
                cls.sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOINHERIT NOBYPASSRLS"
                ).format(cls.sql.Identifier(cls.role)),
                (cls.password,),
            )
            cls.admin.execute(
                cls.sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                    cls.sql.Identifier(cls.schema), cls.sql.Identifier(cls.role)
                )
            )
            cls.app_dsn = cls.make_conninfo(
                ADMIN_DSN,
                user=cls.role,
                password=cls.password,
                options=f"-c search_path={cls.schema}",
            )
            with cls.adapter.connect(cls.app_dsn, autocommit=True) as conn:
                cls.adapter.execute(
                    conn,
                    "CREATE TABLE tenant_rows ("
                    "id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
                    "company_id bigint NOT NULL, value text NOT NULL)",
                )
                cls.adapter.execute(conn, "ALTER TABLE tenant_rows ENABLE ROW LEVEL SECURITY")
                cls.adapter.execute(conn, "ALTER TABLE tenant_rows FORCE ROW LEVEL SECURITY")
                policy = (
                    "company_id = NULLIF(current_setting('app.company_id', true), '')::bigint"
                )
                cls.adapter.execute(
                    conn,
                    f"CREATE POLICY tenant_scope ON tenant_rows USING ({policy}) WITH CHECK ({policy})",
                )
                cls.adapter.execute(
                    conn,
                    "CREATE TABLE adapter_probe ("
                    "id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, value text NOT NULL)",
                )

            # Seed both companies using the same transaction-local context
            # mechanism the application is expected to use after verifying membership.
            with cls.adapter.connect(cls.app_dsn, autocommit=True) as conn:
                for company_id, value in ((101, "company-a"), (202, "company-b")):
                    cls.adapter.begin(conn)
                    cls.adapter.execute(
                        conn,
                        "SELECT set_config('app.company_id', ?, true)",
                        (str(company_id),),
                    )
                    cls.adapter.execute(
                        conn,
                        "INSERT INTO tenant_rows(company_id, value) VALUES(?, ?)",
                        (company_id, value),
                    )
                    cls.adapter.commit(conn)
        except Exception:
            cls._cleanup_resources()
            raise

    @classmethod
    def _cleanup_resources(cls):
        admin = getattr(cls, "admin", None)
        if admin is None:
            return
        try:
            if getattr(cls, "schema", None):
                admin.execute(
                    cls.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        cls.sql.Identifier(cls.schema)
                    )
                )
            if getattr(cls, "role", None):
                admin.execute(
                    cls.sql.SQL("DROP ROLE IF EXISTS {}").format(cls.sql.Identifier(cls.role))
                )
        finally:
            admin.close()

    @classmethod
    def tearDownClass(cls):
        cls._cleanup_resources()

    def _set_company(self, conn, company_id: int):
        self.adapter.begin(conn)
        self.adapter.execute(
            conn,
            "SELECT set_config('app.company_id', ?, true)",
            (str(company_id),),
        )

    def _visible_company_ids(self, conn):
        cursor = self.adapter.execute(
            conn,
            "SELECT company_id FROM tenant_rows ORDER BY company_id",
        )
        return [int(row[0]) for row in cursor.fetchall()]

    def test_live_connection_row_factory_and_qmark_binding(self):
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self._set_company(conn, 101)
            cursor = self.adapter.execute(
                conn,
                "SELECT id, value FROM tenant_rows WHERE company_id=?",
                (101,),
            )
            row = cursor.fetchone()
            self.assertEqual(row["value"], "company-a")
            self.assertGreater(int(row["id"]), 0)
            self.adapter.commit(conn)

    def test_real_executemany_and_literal_percent_binding(self):
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self.adapter.executemany(
                conn,
                "INSERT INTO adapter_probe(value) VALUES(?)",
                [("alpha marker",), ("beta marker",)],
            )
            rows = self.adapter.execute(
                conn,
                "SELECT value FROM adapter_probe WHERE value LIKE '%alpha%' AND id>?",
                (0,),
            ).fetchall()
            self.assertEqual([row[0] for row in rows], ["alpha marker"])

    def test_insert_returning_id_and_commit(self):
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            cursor = self.adapter.execute(
                conn,
                "INSERT INTO adapter_probe(value) VALUES(?) RETURNING id",
                ("returning-check",),
            )
            inserted_id = self.adapter.last_insert_id(cursor)
            self.assertIsInstance(inserted_id, int)
            self.assertGreater(inserted_id, 0)

    def test_rollback_discards_write(self):
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            before = self.adapter.execute(
                conn, "SELECT count(*) FROM adapter_probe"
            ).fetchone()[0]
            self.adapter.begin(conn)
            self.adapter.execute(
                conn,
                "INSERT INTO adapter_probe(value) VALUES(?)",
                ("must-rollback",),
            )
            self.adapter.rollback(conn)
            after = self.adapter.execute(
                conn, "SELECT count(*) FROM adapter_probe"
            ).fetchone()[0]
            self.assertEqual(after, before)

    def test_savepoint_rolls_back_inner_work(self):
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            before = self.adapter.execute(
                conn, "SELECT count(*) FROM adapter_probe"
            ).fetchone()[0]
            self.adapter.begin(conn)
            self.adapter.execute(conn, "SAVEPOINT stage22_inner")
            self.adapter.execute(
                conn,
                "INSERT INTO adapter_probe(value) VALUES(?)",
                ("savepoint-rollback",),
            )
            self.adapter.execute(conn, "ROLLBACK TO SAVEPOINT stage22_inner")
            self.adapter.execute(conn, "RELEASE SAVEPOINT stage22_inner")
            self.adapter.commit(conn)
            after = self.adapter.execute(
                conn, "SELECT count(*) FROM adapter_probe"
            ).fetchone()[0]
            self.assertEqual(after, before)

    def test_rls_filters_companies_and_rejects_missing_context(self):
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self._set_company(conn, 101)
            self.assertEqual(self._visible_company_ids(conn), [101])
            self.adapter.commit(conn)

            self._set_company(conn, 202)
            self.assertEqual(self._visible_company_ids(conn), [202])
            self.adapter.commit(conn)

            # With no transaction-local context, the RLS predicate is NULL/false.
            self.assertEqual(self._visible_company_ids(conn), [])
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                self.adapter.execute(
                    conn,
                    "INSERT INTO tenant_rows(company_id, value) VALUES(?, ?)",
                    (101, "missing-context"),
                )

    def test_rls_rejects_cross_company_row_reassignment(self):
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self._set_company(conn, 101)
            row = self.adapter.execute(
                conn, "SELECT id FROM tenant_rows WHERE company_id=?", (101,)
            ).fetchone()
            self.assertIsNotNone(row)
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                self.adapter.execute(
                    conn,
                    "UPDATE tenant_rows SET company_id=? WHERE id=?",
                    (202, int(row[0])),
                )
            self.adapter.rollback(conn)
            self._set_company(conn, 101)
            self.assertEqual(self._visible_company_ids(conn), [101])
            self.adapter.commit(conn)

    def test_transaction_local_context_is_cleared_after_commit_on_same_connection(self):
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self._set_company(conn, 101)
            self.assertEqual(self._visible_company_ids(conn), [101])
            self.adapter.commit(conn)
            self.assertEqual(self._visible_company_ids(conn), [])

    def test_pool_reuse_does_not_leak_company_context(self):
        pool = self.ConnectionPool(
            conninfo=self.app_dsn,
            min_size=1,
            max_size=1,
            open=True,
            kwargs={"autocommit": True},
        )
        try:
            with pool.connection() as conn:
                first_pid = conn.info.backend_pid
                self._set_company(conn, 101)
                self.assertEqual(self._visible_company_ids(conn), [101])
                self.adapter.commit(conn)
            with pool.connection() as conn:
                second_pid = conn.info.backend_pid
                self.assertEqual(first_pid, second_pid, "pool did not reuse the same backend connection")
                self._set_company(conn, 202)
                self.assertEqual(self._visible_company_ids(conn), [202])
                self.adapter.commit(conn)
            with pool.connection() as conn:
                third_pid = conn.info.backend_pid
                self.assertEqual(first_pid, third_pid)
                self.assertEqual(self._visible_company_ids(conn), [])
        finally:
            pool.close()

    def test_table_columns_uses_current_schema(self):
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self.assertEqual(
                self.adapter.table_columns(conn, "adapter_probe"),
                {"id", "value"},
            )

    def test_transaction_status_tracks_begin_commit_and_rollback(self):
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self.assertFalse(self.adapter.in_transaction(conn))
            self.adapter.begin(conn)
            self.assertTrue(self.adapter.in_transaction(conn))
            self.adapter.commit(conn)
            self.assertFalse(self.adapter.in_transaction(conn))
            self.adapter.begin(conn)
            self.adapter.rollback(conn)
            self.assertFalse(self.adapter.in_transaction(conn))


if __name__ == "__main__":
    unittest.main(verbosity=2)
