from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock

BACKEND = Path(__file__).resolve().parents[1]

def load_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, BACKEND / relative_path)
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

protocol_module = load_module("erp_stage21_protocol", "erp/db_adapters/protocol.py")
postgres_module = load_module("erp_stage21_postgres_spike", "erp/db_adapters/postgres_psycopg.py")
DatabaseAdapter = protocol_module.DatabaseAdapter
HybridRow = postgres_module.HybridRow
PlaceholderMismatch = postgres_module.PlaceholderMismatch
Psycopg3Adapter = postgres_module.Psycopg3Adapter
PsycopgCursorProxy = postgres_module.PsycopgCursorProxy
hybrid_row_factory = postgres_module.hybrid_row_factory
translate_qmark_sql = postgres_module.translate_qmark_sql


class FakeColumn:
    def __init__(self, name):
        self.name = name


class FakeCursor:
    def __init__(self):
        self.description = None
        self.rows = []
        self.executed = []
        self.rowcount = -1
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "RETURNING id" in sql:
            self.description = [FakeColumn("id")]
            self.rows = [HybridRow(["id"], [17])]
        elif "information_schema.columns" in sql:
            self.description = [FakeColumn("column_name")]
            self.rows = [HybridRow(["column_name"], ["id"]), HybridRow(["column_name"], ["company_id"])]
        else:
            self.description = None
            self.rows = []
        self.rowcount = 1
        return self

    def executemany(self, sql, rows):
        self.executed.append((sql, list(rows)))
        self.rowcount = len(self.executed[-1][1])
        return self

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows

    def close(self):
        self.closed = True

    def __iter__(self):
        return iter(self.rows)


class FakeInfo:
    def __init__(self, transaction_status):
        self.transaction_status = transaction_status


class FakeConnection:
    def __init__(self):
        self.cursors = []
        self.info = FakeInfo("IDLE")
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        cursor = FakeCursor()
        self.cursors.append(cursor)
        return cursor

    def commit(self):
        self.commits += 1
        self.info.transaction_status = "IDLE"

    def rollback(self):
        self.rollbacks += 1
        self.info.transaction_status = "IDLE"

    def close(self):
        self.closed = True


class HybridRowTests(unittest.TestCase):
    def test_supports_mapping_and_sequence_access_like_sqlite_row(self):
        row = HybridRow(["id", "name"], [7, "Main"])
        self.assertEqual(row[0], 7)
        self.assertEqual(row["name"], "Main")
        self.assertEqual(list(row), [7, "Main"])
        self.assertEqual(dict(row), {"id": 7, "name": "Main"})
        self.assertEqual(row.keys(), ("id", "name"))

    def test_duplicate_column_names_resolve_to_first_ordinal(self):
        row = HybridRow(["id", "id"], [1, 2])
        self.assertEqual(row["id"], 1)
        self.assertEqual(row[1], 2)

    def test_rejects_name_value_length_mismatch(self):
        with self.assertRaises(ValueError):
            HybridRow(["id"], [])

    def test_psycopg_factory_uses_description_names(self):
        cursor = FakeCursor()
        cursor.description = [FakeColumn("id"), FakeColumn("label")]
        make_row = hybrid_row_factory(cursor)
        row = make_row((9, "sample"))
        self.assertEqual(row["label"], "sample")
        self.assertEqual(row[0], 9)


class QmarkTranslatorTests(unittest.TestCase):
    def test_translates_plain_qmarks(self):
        self.assertEqual(
            translate_qmark_sql("SELECT * FROM items WHERE id=? AND company_id=?", (3, 8)),
            "SELECT * FROM items WHERE id=%s AND company_id=%s",
        )

    def test_ignores_question_marks_in_single_and_double_quotes(self):
        sql = "SELECT '?' AS mark, \"question?name\" FROM t WHERE id=?"
        self.assertEqual(
            translate_qmark_sql(sql, (5,)),
            "SELECT '?' AS mark, \"question?name\" FROM t WHERE id=%s",
        )

    def test_ignores_escaped_single_quotes(self):
        sql = "SELECT 'it''s ?' AS label WHERE id=?"
        self.assertEqual(translate_qmark_sql(sql, (1,)), "SELECT 'it''s ?' AS label WHERE id=%s")

    def test_ignores_escape_string_backslash_escaped_quote(self):
        sql = r"SELECT E'it\\\'s ?' AS label WHERE id=?"
        translated = translate_qmark_sql(sql, (1,))
        self.assertTrue(translated.endswith("WHERE id=%s"))
        self.assertIn("E'it", translated)

    def test_ignores_line_comments(self):
        sql = "SELECT * FROM t -- ? comment\nWHERE id=?"
        self.assertEqual(translate_qmark_sql(sql, (2,)), "SELECT * FROM t -- ? comment\nWHERE id=%s")

    def test_ignores_nested_block_comments(self):
        sql = "SELECT /* outer ? /* inner ? */ still ? */ id FROM t WHERE id=?"
        self.assertEqual(translate_qmark_sql(sql, (2,)), "SELECT /* outer ? /* inner ? */ still ? */ id FROM t WHERE id=%s")

    def test_ignores_dollar_quoted_bodies(self):
        sql = "DO $body$ BEGIN RAISE NOTICE '?'; END $body$; SELECT * FROM t WHERE id=?"
        self.assertEqual(
            translate_qmark_sql(sql, (4,)),
            "DO $body$ BEGIN RAISE NOTICE '?'; END $body$; SELECT * FROM t WHERE id=%s",
        )

    def test_escapes_literal_percent_when_parameters_are_bound(self):
        sql = "SELECT * FROM t WHERE label LIKE '%urgent%' AND id=?"
        self.assertEqual(
            translate_qmark_sql(sql, (1,)),
            "SELECT * FROM t WHERE label LIKE '%%urgent%%' AND id=%s",
        )

    def test_keeps_percent_when_no_parameters_are_bound(self):
        self.assertEqual(translate_qmark_sql("SELECT '%urgent%'"), "SELECT '%urgent%'")

    def test_escapes_percent_inside_strings_identifiers_and_comments_when_bound(self):
        sql = "SELECT '%done%' AS label /* 50% */ WHERE id=? -- 100%\n"
        self.assertEqual(
            translate_qmark_sql(sql, (1,)),
            "SELECT '%%done%%' AS label /* 50%% */ WHERE id=%s -- 100%%\n",
        )

    def test_rejects_json_question_operator_as_ambiguous(self):
        with self.assertRaises(PlaceholderMismatch):
            translate_qmark_sql("SELECT payload ? 'enabled' FROM events WHERE id=?", (1,))

    def test_rejects_dynamic_json_question_operator_as_ambiguous(self):
        with self.assertRaises(PlaceholderMismatch):
            translate_qmark_sql("SELECT payload ? ? FROM events", ("enabled",))

    def test_rejects_parameter_count_mismatch(self):
        with self.assertRaises(PlaceholderMismatch):
            translate_qmark_sql("SELECT * FROM t WHERE id=?", ())

    def test_rejects_mapping_params_for_legacy_qmark_sql(self):
        with self.assertRaises(TypeError):
            translate_qmark_sql("SELECT * FROM t WHERE id=?", {"id": 1})

    def test_rejects_unterminated_sql_construct(self):
        with self.assertRaises(ValueError):
            translate_qmark_sql("SELECT 'unterminated", ())


class PsycopgAdapterSpikeTests(unittest.TestCase):
    def setUp(self):
        self.adapter = Psycopg3Adapter()
        self.connection = FakeConnection()

    def test_structurally_implements_database_adapter_contract(self):
        self.assertIsInstance(self.adapter, DatabaseAdapter)
        self.assertFalse(self.adapter.supports_online_backup)
        self.assertFalse(self.adapter.supports_sql_script)

    def test_execute_returns_cursor_proxy_and_translates_bindings(self):
        cursor = self.adapter.execute(self.connection, "SELECT * FROM t WHERE id=?", (5,))
        self.assertIsInstance(cursor, PsycopgCursorProxy)
        self.assertEqual(self.connection.cursors[-1].executed[0], ("SELECT * FROM t WHERE id=%s", (5,)))

    def test_execute_without_params_does_not_pass_empty_tuple(self):
        self.adapter.execute(self.connection, "SELECT 1")
        self.assertEqual(self.connection.cursors[-1].executed[0], ("SELECT 1", None))

    def test_insert_id_requires_explicit_returning_id(self):
        no_id = self.adapter.execute(self.connection, "INSERT INTO t(label) VALUES(?)", ("a",))
        self.assertIsNone(self.adapter.last_insert_id(no_id))
        returned = self.adapter.execute(self.connection, "INSERT INTO t(label) VALUES(?) RETURNING id", ("a",))
        self.assertEqual(self.adapter.last_insert_id(returned), 17)

    def test_executemany_validates_consistent_parameter_counts(self):
        cursor = self.adapter.executemany(
            self.connection,
            "INSERT INTO t(a,b) VALUES(?,?)",
            [(1, 2), (3, 4)],
        )
        self.assertEqual(cursor.rowcount, 2)
        with self.assertRaises(PlaceholderMismatch):
            self.adapter.executemany(self.connection, "INSERT INTO t(a,b) VALUES(?,?)", [(1, 2), (3,)])

    def test_begin_commit_and_rollback_for_read_transaction(self):
        self.adapter.begin(self.connection)
        self.assertEqual(self.connection.cursors[-1].executed[0], ("BEGIN", None))
        self.connection.info.transaction_status = "INTRANS"
        self.assertTrue(self.adapter.in_transaction(self.connection))
        self.adapter.commit(self.connection)
        self.assertEqual(self.connection.commits, 1)
        self.assertFalse(self.adapter.in_transaction(self.connection))
        self.adapter.rollback(self.connection)
        self.assertEqual(self.connection.rollbacks, 1)

    def test_write_begin_fails_closed_until_immediate_lock_semantics_are_mapped(self):
        with self.assertRaisesRegex(NotImplementedError, "BEGIN IMMEDIATE"):
            self.adapter.begin(self.connection, write=True)
        self.assertEqual(self.connection.cursors, [])

    def test_table_columns_uses_parameter_binding(self):
        columns = self.adapter.table_columns(self.connection, "users")
        self.assertEqual(columns, {"id", "company_id"})
        sql, params = self.connection.cursors[-1].executed[0]
        self.assertIn("information_schema.columns", sql)
        self.assertEqual(params, ("users",))

    def test_executescript_is_explicitly_unsupported(self):
        with self.assertRaises(NotImplementedError):
            self.adapter.executescript(self.connection, "CREATE TABLE x(id int);")

    def test_connect_maps_current_sqlite_factory_options_to_psycopg(self):
        fake_psycopg = types.ModuleType("psycopg")
        fake_psycopg.connect = Mock(return_value="connection")
        previous = sys.modules.get("psycopg")
        sys.modules["psycopg"] = fake_psycopg
        try:
            result = self.adapter.connect(
                "postgresql://example/db",
                timeout=12,
                check_same_thread=False,
                isolation_level=None,
            )
        finally:
            if previous is None:
                sys.modules.pop("psycopg", None)
            else:
                sys.modules["psycopg"] = previous
        self.assertEqual(result, "connection")
        args, kwargs = fake_psycopg.connect.call_args
        self.assertEqual(args, ("postgresql://example/db",))
        self.assertEqual(kwargs["connect_timeout"], 12)
        self.assertTrue(kwargs["autocommit"])
        self.assertIs(kwargs["row_factory"], hybrid_row_factory)
        self.assertNotIn("check_same_thread", kwargs)

    def test_connect_reports_optional_driver_when_not_installed(self):
        previous = sys.modules.get("psycopg")
        sys.modules["psycopg"] = None
        try:
            with self.assertRaisesRegex(RuntimeError, "requirements-postgres.txt"):
                self.adapter.connect("postgresql://example/db")
        finally:
            if previous is None:
                sys.modules.pop("psycopg", None)
            else:
                sys.modules["psycopg"] = previous

    def test_rejects_non_autocommit_sqlite_isolation_mapping(self):
        with self.assertRaises(ValueError):
            self.adapter.connect("postgresql://example/db", isolation_level="DEFERRED")

    def test_close_delegates_to_connection(self):
        self.adapter.close(self.connection)
        self.assertTrue(self.connection.closed)


if __name__ == "__main__":
    unittest.main()
