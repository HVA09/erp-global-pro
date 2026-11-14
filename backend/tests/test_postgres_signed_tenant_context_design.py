from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "erp_stage23_postgres_sql",
    BACKEND / "erp" / "db_adapters" / "postgres_sql.py",
)
_sql = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sql)
render_signed_tenant_context_migration = _sql.render_signed_tenant_context_migration
render_sql_template = _sql.render_sql_template
split_sql_statements = _sql.split_sql_statements


class SignedTenantContextSqlTests(unittest.TestCase):
    def render(self):
        return render_signed_tenant_context_migration(
            security_schema="erp_security_test",
            crypto_schema="erp_crypto",
            security_owner_role="erp_security_owner_test",
            app_role="erp_app_test",
            control_schema="erp_control_test",
        )

    def test_migration_template_renders_all_identifiers(self):
        rendered = self.render()
        self.assertNotIn("{{", rendered)
        self.assertIn("erp_security_test.current_company_id()", rendered)
        self.assertIn("erp_control_test.company_memberships", rendered)
        self.assertIn("erp_control_test.companies", rendered)

    def test_render_rejects_identifier_injection(self):
        with self.assertRaises(ValueError):
            render_sql_template("CREATE SCHEMA {{S}}", {"S": "safe; DROP SCHEMA public"})

    def test_render_requires_exact_placeholder_set(self):
        with self.assertRaises(ValueError):
            render_sql_template("CREATE SCHEMA {{S}}", {"OTHER": "safe_name"})

    def test_sql_script_splitter_keeps_function_body_as_one_statement(self):
        sql = self.render()
        statements = split_sql_statements(sql)
        function_statements = [s for s in statements if "CREATE OR REPLACE FUNCTION" in s]
        self.assertEqual(len(function_statements), 5)
        for statement in function_statements:
            self.assertTrue(statement.endswith("$function$"))
            self.assertIn("AS $function$", statement)
        self.assertGreaterEqual(len(statements), 25)

    def test_sql_splitter_handles_semicolons_in_quotes_and_comments(self):
        statements = split_sql_statements(
            "SELECT ';' AS a; -- ; comment\nSELECT 2 /* ; nested /* ; */ end */; "
            "DO $tag$ BEGIN PERFORM ';'; PERFORM 2; END $tag$;"
        )
        self.assertEqual(len(statements), 3)
        self.assertIn("';'", statements[0])
        self.assertIn("PERFORM 2;", statements[2])

    def test_sql_splitter_rejects_unterminated_construct(self):
        with self.assertRaises(ValueError):
            split_sql_statements("SELECT 'unterminated")

    def test_security_definer_uses_fixed_search_path_and_protected_key_table(self):
        sql = self.render()
        self.assertIn("SECURITY DEFINER", sql)
        self.assertIn("SET search_path = pg_catalog, erp_security_test, erp_crypto, pg_temp", sql)
        self.assertIn("REVOKE ALL ON erp_security_test.tenant_signing_keys FROM erp_app_test", sql)
        self.assertIn("REVOKE ALL PRIVILEGES", sql)
        self.assertIn("erp_crypto.hmac", sql)

    def test_policy_uses_verified_function_not_raw_company_guc(self):
        sql = self.render()
        self.assertIn("USING (company_id = erp_security_test.current_company_id())", sql)
        self.assertNotIn("current_setting('app.company_id'", sql)

    def test_database_function_checks_live_membership_and_company_status(self):
        sql = self.render()
        self.assertIn("m.status = 'active'", sql)
        self.assertIn("c.status = 'active'", sql)
        self.assertIn("u.is_active IS TRUE", sql)
        self.assertIn("claims ->> 'exp'", sql)
        self.assertIn("claims - ARRAY['iss','aud','kid','sub','company_id','iat','exp','jti']", sql)

    def test_runbook_migration_template_is_packaged(self):
        path = Path(__file__).resolve().parents[1] / "erp" / "db_adapters" / "postgres_signed_tenant_context.sql"
        self.assertTrue(path.exists())
        self.assertIn("disposable PostgreSQL", path.read_text(encoding="utf-8"))

    def test_application_role_has_no_direct_control_plane_access(self):
        sql = self.render()
        self.assertIn("REVOKE ALL PRIVILEGES", sql)
        self.assertIn("FROM PUBLIC, erp_app_test", sql)
        self.assertNotIn("TO erp_app_test", sql.split("GRANT SELECT ON erp_control_test.companies", 1)[-1].split(";", 1)[0])

    def test_template_validates_role_posture_and_trusted_schemas(self):
        sql = self.render()
        self.assertIn("pg_catalog.pg_auth_members", sql)
        self.assertIn("application_role.rolbypassrls", sql)
        self.assertIn("pg_catalog.has_schema_privilege", sql)
        self.assertIn("application role must not own control-plane tables", sql)
        self.assertIn("security schema must be owned", sql)
        self.assertIn("crypto schema must be owned by security owner", sql)
        self.assertIn("must be dedicated non-system schemas", sql)
        self.assertIn("pgcrypto must be installed", sql)

    def test_control_plane_read_routines_are_security_definer_and_granted_narrowly(self):
        sql = self.render()
        for function_name in ("current_user_id", "list_my_companies", "get_current_company_profile", "get_current_membership"):
            marker = f"FUNCTION erp_security_test.{function_name}()"
            self.assertIn(marker, sql)
            section = sql.split(f"CREATE OR REPLACE {marker}", 1)[1].split("$function$", 1)[0]
            self.assertIn("SECURITY DEFINER", section)
            self.assertIn("SET search_path = pg_catalog, erp_security_test, erp_crypto, pg_temp", section)
            self.assertIn(f"REVOKE ALL ON {marker} FROM PUBLIC", sql)
            self.assertIn(f"GRANT EXECUTE ON {marker} TO erp_app_test", sql)

    def test_company_switcher_is_bound_to_verified_user_and_active_memberships(self):
        sql = self.render()
        section = sql.split("CREATE OR REPLACE FUNCTION erp_security_test.list_my_companies()", 1)[1]
        section = section.split("$function$", 2)[1]
        self.assertIn("m.user_id = erp_security_test.current_user_id()", section)
        self.assertIn("m.status = 'active'", section)
        self.assertIn("c.status = 'active'", section)

    def test_current_user_id_is_never_read_before_signed_company_validation(self):
        sql = self.render()
        section = sql.split("CREATE OR REPLACE FUNCTION erp_security_test.current_user_id()", 1)[1]
        section = section.split("$function$", 2)[1]
        self.assertIn("verified_company_id := erp_security_test.current_company_id()", section)
        self.assertIn("IF verified_company_id IS NULL THEN", section)
        self.assertIn("RETURN NULL", section)

    def test_company_and_membership_read_routines_are_scoped(self):
        sql = self.render()
        company_section = sql.split("CREATE OR REPLACE FUNCTION erp_security_test.get_current_company_profile()", 1)[1].split("$function$", 2)[1]
        membership_section = sql.split("CREATE OR REPLACE FUNCTION erp_security_test.get_current_membership()", 1)[1].split("$function$", 2)[1]
        self.assertIn("c.id = erp_security_test.current_company_id()", company_section)
        self.assertIn("m.user_id = erp_security_test.current_user_id()", membership_section)
        self.assertIn("m.company_id = erp_security_test.current_company_id()", membership_section)

    def test_signature_comparison_checks_all_bytes(self):
        sql = self.render()
        self.assertIn("FOR signature_index IN 0..31 LOOP", sql)
        self.assertIn("signature_difference", sql)
        self.assertNotIn("supplied_signature IS DISTINCT FROM pg_catalog.encode", sql)

    def test_expiry_uses_statement_clock_not_transaction_start_clock(self):
        sql = self.render()
        self.assertIn("pg_catalog.statement_timestamp()", sql)
        self.assertNotIn("pg_catalog.now()", sql)


if __name__ == "__main__":
    unittest.main(verbosity=2)
