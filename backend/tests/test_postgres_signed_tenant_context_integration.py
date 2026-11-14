"""Live PostgreSQL tests for signed tenant-context RLS prototype.

Requires a disposable database via ERP_TEST_POSTGRES_ADMIN_DSN and the optional
Psycopg 3/pool dependencies. Set REQUIRE_POSTGRES_INTEGRATION=1 in CI to turn
missing prerequisites into hard failures.
"""
from __future__ import annotations

import importlib.util
import os
import secrets
import sys
import time
import unittest
from pathlib import Path

REQUIRE_POSTGRES = os.environ.get("REQUIRE_POSTGRES_INTEGRATION", "").lower() in {"1", "true", "yes"}
ADMIN_DSN = os.environ.get("ERP_TEST_POSTGRES_ADMIN_DSN", "").strip()


def _imports():
    try:
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        from psycopg_pool import ConnectionPool
    except ImportError as exc:  # pragma: no cover - external environment
        if REQUIRE_POSTGRES:
            raise RuntimeError("Live signed-context tests require psycopg[binary,pool]") from exc
        raise unittest.SkipTest("optional PostgreSQL dependencies are not installed")
    return psycopg, sql, make_conninfo, ConnectionPool


class SignedTenantContextPostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ADMIN_DSN:
            if REQUIRE_POSTGRES:
                raise RuntimeError("ERP_TEST_POSTGRES_ADMIN_DSN is required")
            raise unittest.SkipTest("ERP_TEST_POSTGRES_ADMIN_DSN is not configured")

        backend = Path(__file__).resolve().parents[1]
        def _load(name, relative):
            spec = importlib.util.spec_from_file_location(name, backend / relative)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(name, None)
                raise
            return module

        adapter_mod = _load("erp_stage23_pg_adapter", "erp/db_adapters/postgres_psycopg.py")
        claims_mod = _load("erp_stage23_pg_claims", "erp/db_adapters/signed_tenant_context.py")
        sql_mod = _load("erp_stage23_pg_sql", "erp/db_adapters/postgres_sql.py")
        Psycopg3Adapter = adapter_mod.Psycopg3Adapter
        sign_tenant_claims = claims_mod.sign_tenant_claims
        render_signed_tenant_context_migration = sql_mod.render_signed_tenant_context_migration
        split_sql_statements = sql_mod.split_sql_statements
        cls.sign_tenant_claims = staticmethod(sign_tenant_claims)
        cls.psycopg, cls.sql, cls.make_conninfo, cls.ConnectionPool = _imports()
        cls.adapter = Psycopg3Adapter()
        suffix = secrets.token_hex(4)
        cls.security_role = f"erpsec_{suffix}"
        cls.app_role = f"erpapp_{suffix}"
        cls.control_schema = f"erpctl_{suffix}"
        cls.security_schema = f"erpsec_schema_{suffix}"
        cls.app_schema = f"erpapp_schema_{suffix}"
        cls.crypto_schema = f"erpcrypto_{suffix}"
        cls.password = secrets.token_urlsafe(24)
        cls.hmac_secret = secrets.token_bytes(48)
        cls.key_id = "ci-key-1"
        cls.admin = cls.psycopg.connect(ADMIN_DSN, autocommit=True)
        cls.app_dsn = None

        try:
            cls.admin.execute(
                cls.sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(
                    cls.sql.Identifier(cls.security_role)
                )
            )
            cls.admin.execute(
                cls.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(
                    cls.sql.Identifier(cls.app_role), cls.sql.Literal(cls.password)
                )
            )
            cls.admin.execute(
                cls.sql.SQL("CREATE SCHEMA {} AUTHORIZATION CURRENT_USER").format(
                    cls.sql.Identifier(cls.control_schema)
                )
            )
            cls.admin.execute(
                cls.sql.SQL("CREATE SCHEMA {} AUTHORIZATION CURRENT_USER").format(
                    cls.sql.Identifier(cls.app_schema)
                )
            )
            # Keep schema USAGE but verify the migration removes direct table privileges.
            cls.admin.execute(
                cls.sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    cls.sql.Identifier(cls.control_schema), cls.sql.Identifier(cls.app_role)
                )
            )
            cls.admin.execute(
                cls.sql.SQL(
                    "CREATE TABLE {}.companies ("
                    "id bigint PRIMARY KEY, name text NOT NULL, legal_name text, industry text NOT NULL, "
                    "country text NOT NULL, currency text NOT NULL, timezone text NOT NULL, status text NOT NULL)"
                ).format(cls.sql.Identifier(cls.control_schema))
            )
            cls.admin.execute(
                cls.sql.SQL(
                    "CREATE TABLE {}.users (id bigint PRIMARY KEY, email text NOT NULL, is_active boolean NOT NULL)"
                ).format(cls.sql.Identifier(cls.control_schema))
            )
            cls.admin.execute(
                cls.sql.SQL("INSERT INTO {}.users(id,email,is_active) VALUES(7,'user7@example.test',true),(8,'user8@example.test',true)").format(
                    cls.sql.Identifier(cls.control_schema)
                )
            )
            cls.admin.execute(
                cls.sql.SQL(
                    "CREATE TABLE {}.company_memberships ("
                    "id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id bigint NOT NULL, "
                    "company_id bigint NOT NULL, role text NOT NULL, status text NOT NULL, joined_at timestamptz, "
                    "UNIQUE(user_id, company_id), FOREIGN KEY(company_id) REFERENCES {}.companies(id))"
                ).format(cls.sql.Identifier(cls.control_schema), cls.sql.Identifier(cls.control_schema))
            )
            cls.admin.execute(
                cls.sql.SQL(
                    "INSERT INTO {}.companies(id,name,legal_name,industry,country,currency,timezone,status) "
                    "VALUES(101,'Alpha Ltd','Alpha Legal','trading','LY','LYD','Africa/Tripoli','active'),"
                    "(202,'Beta Ltd','Beta Legal','services','LY','LYD','Africa/Tripoli','active')"
                ).format(cls.sql.Identifier(cls.control_schema))
            )
            cls.admin.execute(
                cls.sql.SQL(
                    "INSERT INTO {}.company_memberships(user_id,company_id,role,status,joined_at) "
                    "VALUES(7,101,'owner','active',now()),(7,202,'admin','active',now()),(8,202,'viewer','active',now())"
                ).format(cls.sql.Identifier(cls.control_schema))
            )

            rendered = render_signed_tenant_context_migration(
                security_schema=cls.security_schema,
                crypto_schema=cls.crypto_schema,
                security_owner_role=cls.security_role,
                app_role=cls.app_role,
                control_schema=cls.control_schema,
            )
            for statement in split_sql_statements(rendered):
                cls.admin.execute(statement)

            # The signed-context function runs with a NOLOGIN, non-BYPASSRLS owner.
            cls.admin.execute(
                cls.sql.SQL("INSERT INTO {}.tenant_signing_keys(key_id,secret,active) VALUES(%s,%s,true)").format(
                    cls.sql.Identifier(cls.security_schema)
                ),
                (cls.key_id, cls.hmac_secret),
            )

            cls.admin.execute(
                cls.sql.SQL(
                    "CREATE TABLE {}.tenant_rows (id bigint PRIMARY KEY, company_id bigint NOT NULL, value text NOT NULL)"
                ).format(cls.sql.Identifier(cls.app_schema))
            )
            cls.admin.execute(
                cls.sql.SQL("INSERT INTO {}.tenant_rows VALUES(1,101,'alpha'),(2,202,'beta')").format(
                    cls.sql.Identifier(cls.app_schema)
                )
            )
            cls.admin.execute(
                cls.sql.SQL("ALTER TABLE {}.tenant_rows ENABLE ROW LEVEL SECURITY").format(
                    cls.sql.Identifier(cls.app_schema)
                )
            )
            cls.admin.execute(
                cls.sql.SQL("ALTER TABLE {}.tenant_rows FORCE ROW LEVEL SECURITY").format(
                    cls.sql.Identifier(cls.app_schema)
                )
            )
            cls.admin.execute(
                cls.sql.SQL(
                    "CREATE POLICY tenant_scope ON {}.tenant_rows "
                    "USING (company_id = {}.current_company_id()) "
                    "WITH CHECK (company_id = {}.current_company_id())"
                ).format(
                    cls.sql.Identifier(cls.app_schema),
                    cls.sql.Identifier(cls.security_schema),
                    cls.sql.Identifier(cls.security_schema),
                )
            )
            cls.admin.execute(
                cls.sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    cls.sql.Identifier(cls.app_schema), cls.sql.Identifier(cls.app_role)
                )
            )
            cls.admin.execute(
                cls.sql.SQL("GRANT SELECT, INSERT, UPDATE ON {}.tenant_rows TO {}").format(
                    cls.sql.Identifier(cls.app_schema), cls.sql.Identifier(cls.app_role)
                )
            )
            cls.app_dsn = cls.make_conninfo(
                ADMIN_DSN,
                user=cls.app_role,
                password=cls.password,
                options=f"-c search_path={cls.app_schema}",
            )
        except Exception:
            try:
                cls.admin.rollback()
            except Exception:
                pass
            cls._cleanup()
            raise

    @classmethod
    def _cleanup(cls):
        admin = getattr(cls, "admin", None)
        if admin is None:
            return
        try:
            crypto_schema = getattr(cls, "crypto_schema", None)
            if crypto_schema:
                extension_row = admin.execute(
                    "SELECT e.extname FROM pg_catalog.pg_extension AS e "
                    "JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace "
                    "WHERE e.extname = 'pgcrypto' AND n.nspname = %s",
                    (crypto_schema,),
                ).fetchone()
                if extension_row:
                    admin.execute("DROP EXTENSION pgcrypto")
            for schema_attr in ("app_schema", "security_schema", "control_schema", "crypto_schema"):
                schema = getattr(cls, schema_attr, None)
                if schema:
                    admin.execute(
                        cls.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(cls.sql.Identifier(schema))
                    )
            for role_attr in ("app_role", "security_role"):
                role = getattr(cls, role_attr, None)
                if role:
                    admin.execute(
                        cls.sql.SQL("DROP ROLE IF EXISTS {}").format(cls.sql.Identifier(role))
                    )
        finally:
            admin.close()

    @classmethod
    def tearDownClass(cls):
        cls._cleanup()

    def _token(self, company_id=101, user_id=7, *, now=None, ttl=300):
        return self.sign_tenant_claims(
            company_id=company_id,
            user_id=user_id,
            key_id=self.key_id,
            secret=self.hmac_secret,
            ttl_seconds=ttl,
            now=now,
        )

    def _set_context(self, conn, payload, signature, legacy_company_id=None):
        self.adapter.begin(conn)
        self.adapter.execute(
            conn, "SELECT set_config('erp.tenant_payload', ?, true)", (payload,)
        )
        self.adapter.execute(
            conn, "SELECT set_config('erp.tenant_signature', ?, true)", (signature,)
        )
        if legacy_company_id is not None:
            self.adapter.execute(
                conn, "SELECT set_config('app.company_id', ?, true)", (str(legacy_company_id),)
            )

    def _visible(self, conn):
        rows = self.adapter.execute(
            conn, "SELECT company_id, value FROM tenant_rows ORDER BY id"
        ).fetchall()
        return [(int(row[0]), row[1]) for row in rows]

    def test_valid_signed_context_limits_rows_to_claimed_company(self):
        payload, signature = self._token(company_id=101)
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self._set_context(conn, payload, signature)
            self.assertEqual(self._visible(conn), [(101, "alpha")])
            self.adapter.commit(conn)

    def test_raw_custom_guc_cannot_forge_tenant_context(self):
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self.adapter.begin(conn)
            self.adapter.execute(conn, "SELECT set_config('app.company_id', ?, true)", ("202",))
            self.assertEqual(self._visible(conn), [])
            self.adapter.commit(conn)

    def test_legacy_company_guc_cannot_override_signed_claim(self):
        payload, signature = self._token(company_id=101)
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self._set_context(conn, payload, signature, legacy_company_id=202)
            self.assertEqual(self._visible(conn), [(101, "alpha")])
            self.adapter.commit(conn)

    def test_tampered_claim_payload_is_denied(self):
        payload, signature = self._token(company_id=101)
        tampered = payload.replace('"company_id":101', '"company_id":202')
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self._set_context(conn, tampered, signature)
            self.assertEqual(self._visible(conn), [])
            self.adapter.commit(conn)

    def test_expired_signed_context_is_denied(self):
        payload, signature = self._token(company_id=101, now=int(time.time()) - 1200, ttl=60)
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self._set_context(conn, payload, signature)
            self.assertEqual(self._visible(conn), [])
            self.adapter.commit(conn)

    def test_membership_revocation_invalidates_existing_signed_context(self):
        payload, signature = self._token(company_id=101)
        self.admin.execute(
            self.sql.SQL("UPDATE {}.company_memberships SET status='suspended' WHERE user_id=7 AND company_id=101").format(
                self.sql.Identifier(self.control_schema)
            )
        )
        try:
            with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
                self._set_context(conn, payload, signature)
                self.assertEqual(self._visible(conn), [])
                self.adapter.commit(conn)
        finally:
            self.admin.execute(
                self.sql.SQL("UPDATE {}.company_memberships SET status='active' WHERE user_id=7 AND company_id=101").format(
                    self.sql.Identifier(self.control_schema)
                )
            )

    def test_deactivated_user_invalidates_existing_signed_context(self):
        payload, signature = self._token(company_id=101, user_id=7)
        self.admin.execute(
            self.sql.SQL("UPDATE {}.users SET is_active=false WHERE id=7").format(
                self.sql.Identifier(self.control_schema)
            )
        )
        try:
            with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
                self._set_context(conn, payload, signature)
                self.assertEqual(self._visible(conn), [])
                self.adapter.commit(conn)
        finally:
            self.admin.execute(
                self.sql.SQL("UPDATE {}.users SET is_active=true WHERE id=7").format(
                    self.sql.Identifier(self.control_schema)
                )
            )

    def test_token_for_nonmember_is_denied(self):
        payload, signature = self._token(company_id=101, user_id=999)
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self._set_context(conn, payload, signature)
            self.assertEqual(self._visible(conn), [])
            self.adapter.commit(conn)

    def test_application_role_cannot_read_signing_keys_or_write_memberships(self):
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                self.adapter.execute(
                    conn,
                    f"SELECT secret FROM {self.security_schema}.tenant_signing_keys",
                )
            for control_table in ("companies", "company_memberships", "users"):
                with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                    self.adapter.execute(
                        conn,
                        f"SELECT * FROM {self.control_schema}.{control_table}",
                    )
                self.adapter.rollback(conn)
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                self.adapter.execute(
                    conn,
                    f"INSERT INTO {self.control_schema}.company_memberships(user_id,company_id,status) VALUES(999,101,'active')",
                )
            self.adapter.rollback(conn)
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                self.adapter.execute(
                    conn,
                    f"UPDATE {self.control_schema}.company_memberships SET status='active' WHERE user_id=7 AND company_id=101",
                )
            self.adapter.rollback(conn)
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                self.adapter.execute(
                    conn,
                    f"UPDATE {self.control_schema}.users SET is_active=false WHERE id=7",
                )
            self.adapter.rollback(conn)
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                self.adapter.execute(
                    conn,
                    f"UPDATE {self.control_schema}.companies SET status='suspended' WHERE id=101",
                )
            self.adapter.rollback(conn)

    def test_cross_company_row_reassignment_is_rejected(self):
        payload, signature = self._token(company_id=101)
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self._set_context(conn, payload, signature)
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                self.adapter.execute(conn, "UPDATE tenant_rows SET company_id=? WHERE id=?", (202, 1))
            self.adapter.rollback(conn)

    def test_token_expires_during_a_long_running_transaction(self):
        payload, signature = self._token(company_id=101, ttl=1)
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self._set_context(conn, payload, signature)
            self.adapter.execute(conn, "SELECT pg_sleep(1.2)")
            self.assertEqual(self._visible(conn), [])
            self.adapter.rollback(conn)

    def test_list_my_companies_returns_only_active_memberships_for_signed_user(self):
        payload, signature = self._token(company_id=101, user_id=7)
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self._set_context(conn, payload, signature)
            rows = self.adapter.execute(
                conn,
                f"SELECT company_id, company_name, membership_role "
                f"FROM {self.security_schema}.list_my_companies() ORDER BY company_id",
            ).fetchall()
            self.assertEqual(
                [(int(r[0]), r[1], r[2]) for r in rows],
                [(101, "Alpha Ltd", "owner"), (202, "Beta Ltd", "admin")],
            )
            self.adapter.commit(conn)

    def test_list_my_companies_never_returns_another_users_memberships(self):
        payload, signature = self._token(company_id=202, user_id=8)
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self._set_context(conn, payload, signature)
            rows = self.adapter.execute(
                conn,
                f"SELECT company_id, company_name, membership_role "
                f"FROM {self.security_schema}.list_my_companies() ORDER BY company_id",
            ).fetchall()
            self.assertEqual(
                [(int(r[0]), r[1], r[2]) for r in rows],
                [(202, "Beta Ltd", "viewer")],
            )
            self.adapter.commit(conn)

    def test_current_company_profile_is_limited_to_signed_company(self):
        payload, signature = self._token(company_id=101, user_id=7)
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self._set_context(conn, payload, signature)
            rows = self.adapter.execute(
                conn,
                f"SELECT company_id, company_name, legal_name, currency "
                f"FROM {self.security_schema}.get_current_company_profile()",
            ).fetchall()
            self.assertEqual(
                [(int(r[0]), r[1], r[2], r[3]) for r in rows],
                [(101, "Alpha Ltd", "Alpha Legal", "LYD")],
            )
            self.adapter.commit(conn)

    def test_current_membership_uses_verified_principal_and_company(self):
        payload, signature = self._token(company_id=202, user_id=7)
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self._set_context(conn, payload, signature)
            rows = self.adapter.execute(
                conn,
                f"SELECT user_id, company_id, membership_role, membership_status "
                f"FROM {self.security_schema}.get_current_membership()",
            ).fetchall()
            self.assertEqual(
                [(int(r[0]), int(r[1]), r[2], r[3]) for r in rows],
                [(7, 202, "admin", "active")],
            )
            self.adapter.commit(conn)

    def test_control_plane_read_functions_fail_closed_without_signed_context(self):
        with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
            self.adapter.begin(conn)
            self.assertEqual(
                self.adapter.execute(
                    conn, f"SELECT * FROM {self.security_schema}.list_my_companies()"
                ).fetchall(),
                [],
            )
            self.assertEqual(
                self.adapter.execute(
                    conn, f"SELECT * FROM {self.security_schema}.get_current_company_profile()"
                ).fetchall(),
                [],
            )
            self.assertEqual(
                self.adapter.execute(
                    conn, f"SELECT * FROM {self.security_schema}.get_current_membership()"
                ).fetchall(),
                [],
            )
            self.adapter.commit(conn)

    def test_suspended_membership_is_omitted_from_company_switcher(self):
        self.admin.execute(
            self.sql.SQL("UPDATE {}.company_memberships SET status='suspended' WHERE user_id=7 AND company_id=202").format(
                self.sql.Identifier(self.control_schema)
            )
        )
        try:
            payload, signature = self._token(company_id=101, user_id=7)
            with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
                self._set_context(conn, payload, signature)
                rows = self.adapter.execute(
                    conn,
                    f"SELECT company_id FROM {self.security_schema}.list_my_companies() ORDER BY company_id",
                ).fetchall()
                self.assertEqual([int(r[0]) for r in rows], [101])
                self.adapter.commit(conn)
        finally:
            self.admin.execute(
                self.sql.SQL("UPDATE {}.company_memberships SET status='active' WHERE user_id=7 AND company_id=202").format(
                    self.sql.Identifier(self.control_schema)
                )
            )

    def test_inactive_company_is_omitted_from_company_switcher(self):
        self.admin.execute(
            self.sql.SQL("UPDATE {}.companies SET status='suspended' WHERE id=202").format(
                self.sql.Identifier(self.control_schema)
            )
        )
        try:
            payload, signature = self._token(company_id=101, user_id=7)
            with self.adapter.connect(self.app_dsn, autocommit=True) as conn:
                self._set_context(conn, payload, signature)
                rows = self.adapter.execute(
                    conn,
                    f"SELECT company_id FROM {self.security_schema}.list_my_companies() ORDER BY company_id",
                ).fetchall()
                self.assertEqual([int(r[0]) for r in rows], [101])
                self.adapter.commit(conn)
        finally:
            self.admin.execute(
                self.sql.SQL("UPDATE {}.companies SET status='active' WHERE id=202").format(
                    self.sql.Identifier(self.control_schema)
                )
            )

    def test_pool_reuse_does_not_leak_signed_context(self):
        pool = self.ConnectionPool(
            conninfo=self.app_dsn,
            min_size=1,
            max_size=1,
            open=True,
            kwargs={"autocommit": True},
        )
        try:
            payload_a, sig_a = self._token(company_id=101)
            payload_b, sig_b = self._token(company_id=202)
            with pool.connection() as conn:
                first_pid = conn.info.backend_pid
                self._set_context(conn, payload_a, sig_a)
                self.assertEqual(self._visible(conn), [(101, "alpha")])
                self.adapter.commit(conn)
            with pool.connection() as conn:
                self.assertEqual(conn.info.backend_pid, first_pid)
                self._set_context(conn, payload_b, sig_b)
                self.assertEqual(self._visible(conn), [(202, "beta")])
                self.adapter.commit(conn)
            with pool.connection() as conn:
                self.assertEqual(conn.info.backend_pid, first_pid)
                self.assertEqual(self._visible(conn), [])
        finally:
            pool.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
