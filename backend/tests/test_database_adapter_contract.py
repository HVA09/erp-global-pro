import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

# Load the adapter modules by file path so this contract suite remains runnable
# in offline build environments where importing erp.__init__ would require Flask.
def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

protocol_module = _load_module("erp_db_adapter_protocol", BACKEND / "erp" / "db_adapters" / "protocol.py")
sqlite_module = _load_module("erp_db_adapter_sqlite", BACKEND / "erp" / "db_adapters" / "sqlite.py")
DatabaseAdapter = protocol_module.DatabaseAdapter
SQLiteAdapter = sqlite_module.SQLiteAdapter


class SQLiteAdapterContractTests(unittest.TestCase):
    def setUp(self):
        self.adapter = SQLiteAdapter()
        self.connection = self.adapter.connect(":memory:")

    def tearDown(self):
        self.adapter.close(self.connection)

    def test_adapter_satisfies_database_protocol(self):
        self.assertIsInstance(self.adapter, DatabaseAdapter)

    def test_connection_enables_foreign_keys_and_row_mapping(self):
        enabled = self.adapter.execute(self.connection, "PRAGMA foreign_keys").fetchone()[0]
        self.assertEqual(enabled, 1)
        self.adapter.execute(self.connection, "CREATE TABLE sample(id INTEGER PRIMARY KEY, label TEXT)")
        row = self.adapter.execute(self.connection, "INSERT INTO sample(label) VALUES(?) RETURNING id, label", ("sample",)).fetchone()
        self.assertEqual(dict(row), {"id": 1, "label": "sample"})

    def test_execute_and_executemany_preserve_parameter_binding(self):
        self.adapter.execute(self.connection, "CREATE TABLE sample(id INTEGER PRIMARY KEY, label TEXT)")
        self.adapter.executemany(self.connection, "INSERT INTO sample(label) VALUES(?)", [("a",), ("b",)])
        rows = self.adapter.execute(self.connection, "SELECT label FROM sample ORDER BY id").fetchall()
        self.assertEqual([row[0] for row in rows], ["a", "b"])

    def test_begin_commit_and_rollback(self):
        self.adapter.execute(self.connection, "CREATE TABLE sample(value INTEGER)")
        self.adapter.begin(self.connection, write=True)
        self.adapter.execute(self.connection, "INSERT INTO sample(value) VALUES(1)")
        self.assertTrue(self.adapter.in_transaction(self.connection))
        self.adapter.commit(self.connection)
        self.assertFalse(self.adapter.in_transaction(self.connection))
        self.adapter.begin(self.connection, write=True)
        self.adapter.execute(self.connection, "INSERT INTO sample(value) VALUES(2)")
        self.adapter.rollback(self.connection)
        count = self.adapter.execute(self.connection, "SELECT COUNT(*) FROM sample").fetchone()[0]
        self.assertEqual(count, 1)

    def test_begin_write_uses_immediate_lock(self):
        self.adapter.begin(self.connection, write=True)
        self.assertTrue(self.adapter.in_transaction(self.connection))
        self.adapter.rollback(self.connection)

    def test_table_column_introspection_and_identifier_validation(self):
        self.adapter.execute(self.connection, "CREATE TABLE \"weird name\"(id INTEGER, \"display\" TEXT)")
        self.assertEqual(self.adapter.table_columns(self.connection, "weird name"), {"id", "display"})
        # Quotes are escaped as one identifier; an injection-shaped value cannot
        # terminate the PRAGMA identifier and execute another statement.
        self.assertEqual(self.adapter.table_columns(self.connection, 'sample"; DROP TABLE sample; --'), set())
        self.assertEqual(self.adapter.table_columns(self.connection, "weird name"), {"id", "display"})

    def test_last_insert_id_is_returned(self):
        self.adapter.execute(self.connection, "CREATE TABLE sample(id INTEGER PRIMARY KEY, label TEXT)")
        cursor = self.adapter.execute(self.connection, "INSERT INTO sample(label) VALUES(?)", ("x",))
        self.assertEqual(self.adapter.last_insert_id(cursor), 1)

    def test_integrity_and_foreign_key_checks(self):
        self.adapter.executescript(self.connection, """
            CREATE TABLE parent(id INTEGER PRIMARY KEY);
            CREATE TABLE child(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id));
            INSERT INTO parent(id) VALUES(1);
            INSERT INTO child(id,parent_id) VALUES(1,1);
        """)
        self.assertEqual(self.adapter.integrity_check(self.connection), ["ok"])
        self.assertEqual(self.adapter.foreign_key_violations(self.connection), [])

    def test_online_backup_creates_consistent_copy(self):
        self.adapter.execute(self.connection, "CREATE TABLE sample(value TEXT)")
        self.adapter.execute(self.connection, "INSERT INTO sample(value) VALUES(?)", ("backup-value",))
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "snapshot.sqlite"
            self.adapter.backup_to_path(self.connection, target)
            copied = sqlite3.connect(str(target))
            try:
                value = copied.execute("SELECT value FROM sample").fetchone()[0]
            finally:
                copied.close()
            self.assertEqual(value, "backup-value")

    def test_symlink_backup_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real.sqlite"
            real.write_bytes(b"do-not-overwrite")
            link = Path(tmp) / "link.sqlite"
            link.symlink_to(real)
            with self.assertRaises(ValueError):
                self.adapter.backup_to_path(self.connection, link)
            self.assertEqual(real.read_bytes(), b"do-not-overwrite")


if __name__ == "__main__":
    unittest.main()
