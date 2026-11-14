"""Tests for the Stage25B read-only control-plane preflight."""
import base64
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "control_plane_preflight.py"
SPEC = importlib.util.spec_from_file_location("control_plane_preflight", SCRIPT)
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)


def valid_hash():
    salt = base64.b64encode(b"s" * 16).decode("ascii")
    digest = base64.b64encode(b"d" * 32).decode("ascii")
    return f"scrypt$16384$8$1${salt}${digest}"


class ControlPlanePreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "source-v8.sqlite3"
        self._create_database()

    def tearDown(self):
        self.temp.cleanup()

    def _create_database(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
        conn.executemany(
            "INSERT INTO schema_version(version) VALUES(?)",
            [(version,) for version in range(1, 9)],
        )
        conn.execute("""
            CREATE TABLE roles(
                name TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                permissions TEXT NOT NULL,
                is_system INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE users(
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL REFERENCES roles(name),
                is_active INTEGER NOT NULL DEFAULT 1,
                last_login TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE companies(
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                industry TEXT NOT NULL DEFAULT 'general',
                country TEXT NOT NULL DEFAULT '',
                currency TEXT NOT NULL DEFAULT 'USD',
                timezone TEXT NOT NULL DEFAULT 'UTC',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE company_memberships(
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id),
                user_id INTEGER NOT NULL REFERENCES users(id),
                role TEXT NOT NULL REFERENCES roles(name),
                status TEXT NOT NULL DEFAULT 'active',
                invited_by INTEGER REFERENCES users(id),
                joined_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(company_id,user_id)
            )
        """)
        conn.execute(
            "INSERT INTO roles(name,label,permissions,is_system) VALUES(?,?,?,?)",
            ("admin", "Administrator", '["*"]', 1),
        )
        conn.execute(
            "INSERT INTO users(id,email,name,password_hash,role,is_active,last_login,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (1, "owner@example.test", "Owner", valid_hash(), "admin", 1, None, "2026-11-14 20:00:00"),
        )
        conn.execute(
            "INSERT INTO companies(id,name,industry,country,currency,timezone,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (10, "Company A", "general", "LY", "LYD", "Africa/Tripoli", "active",
             "2026-11-14 20:00:00", None),
        )
        conn.execute(
            "INSERT INTO company_memberships(id,company_id,user_id,role,status,invited_by,joined_at,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (100, 10, 1, "admin", "active", None, "2026-11-14 20:00:00", "2026-11-14 20:00:00"),
        )
        conn.commit()
        conn.close()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def test_clean_database_is_ready_and_sensitive_values_are_not_returned(self):
        report = PREFLIGHT.build_report(self.db_path)
        self.assertTrue(report["read_only"])
        self.assertEqual(report["schema_version"], 8)
        self.assertEqual(report["summary"]["users"], 1)
        self.assertEqual(report["summary"]["roles"], 1)
        self.assertEqual(report["summary"]["companies"], 1)
        self.assertEqual(report["summary"]["company_memberships"], 1)
        self.assertTrue(report["summary"]["ready_for_control_plane_import"])
        self.assertEqual(report["blockers"], [])
        self.assertFalse(report["privacy"]["password_hashes_included"])
        self.assertNotIn(valid_hash(), json.dumps(report))
        self.assertEqual(report["source_database_sha256"], PREFLIGHT._file_sha256(self.db_path))

    def test_casefold_email_collisions_are_blockers_and_values_are_redacted_by_default(self):
        conn = self._connect()
        conn.execute(
            "INSERT INTO users(id,email,name,password_hash,role,is_active,created_at) VALUES(?,?,?,?,?,?,?)",
            (2, "STRASSE@example.test", "Second", valid_hash(), "admin", 1, "2026-11-14 20:00:00"),
        )
        # casefold makes Straße and STRASSE equivalent; no PII is needed in the default report.
        conn.execute(
            "UPDATE users SET email=? WHERE id=1",
            ("Straße@example.test",),
        )
        conn.commit()
        conn.close()

        report = PREFLIGHT.build_report(self.db_path)
        self.assertFalse(report["summary"]["ready_for_control_plane_import"])
        self.assertIn("canonical_email_collisions", [x["code"] for x in report["blockers"]])
        self.assertNotIn("canonical_email", report["details"]["canonical_email_collisions"][0])

        detailed = PREFLIGHT.build_report(self.db_path, include_email_values=True)
        self.assertTrue(detailed["privacy"]["email_values_included"])
        self.assertEqual(
            detailed["details"]["canonical_email_collisions"][0]["canonical_email"],
            "strasse@example.test",
        )

    def test_unknown_membership_role_is_reported_without_exposing_credentials(self):
        conn = self._connect()
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "UPDATE company_memberships SET role='unknown-role' WHERE id=100"
        )
        conn.commit()
        conn.close()

        report = PREFLIGHT.build_report(self.db_path)
        codes = [item["code"] for item in report["blockers"]]
        self.assertIn("unknown_membership_roles", codes)
        self.assertNotIn(valid_hash(), json.dumps(report))

    def test_invalid_permissions_and_scrypt_format_are_blockers(self):
        conn = self._connect()
        conn.execute("UPDATE roles SET permissions='not-json' WHERE name='admin'")
        conn.execute("UPDATE users SET password_hash='pbkdf2$legacy$hash' WHERE id=1")
        conn.commit()
        conn.close()

        report = PREFLIGHT.build_report(self.db_path)
        codes = [item["code"] for item in report["blockers"]]
        self.assertIn("invalid_role_permissions_json", codes)
        self.assertIn("unsupported_password_hashes", codes)
        self.assertEqual(report["details"]["users_with_unsupported_password_hash"], [1])

    def test_invalid_schema_version_history_is_blocked(self):
        conn = self._connect()
        conn.execute("DELETE FROM schema_version WHERE version=4")
        conn.commit()
        conn.close()

        report = PREFLIGHT.build_report(self.db_path)
        codes = [item["code"] for item in report["blockers"]]
        self.assertIn("invalid_schema_migration_history", codes)

    def test_legacy_user_role_mismatch_is_warning_not_authorization_source(self):
        conn = self._connect()
        conn.execute("INSERT INTO roles(name,label,permissions,is_system) VALUES('viewer','Viewer','[]',1)")
        conn.execute("UPDATE users SET role='viewer' WHERE id=1")
        conn.commit()
        conn.close()

        report = PREFLIGHT.build_report(self.db_path)
        self.assertTrue(report["summary"]["ready_for_control_plane_import"])
        self.assertIn(
            "legacy_user_role_differs_from_memberships",
            [item["code"] for item in report["warnings"]],
        )

    def test_database_is_opened_query_only_and_unchanged(self):
        before = PREFLIGHT._file_sha256(self.db_path)
        report = PREFLIGHT.build_report(self.db_path)
        after = PREFLIGHT._file_sha256(self.db_path)
        self.assertEqual(before, after)
        self.assertTrue(report["read_only"])

        conn = PREFLIGHT._open_readonly(self.db_path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE users SET name='changed' WHERE id=1")
        finally:
            conn.close()

    def test_cli_writes_json_and_returns_blocker_exit_code(self):
        conn = self._connect()
        conn.execute("UPDATE users SET password_hash='unsupported' WHERE id=1")
        conn.commit()
        conn.close()
        output = Path(self.temp.name) / "preflight.json"
        process = subprocess.run(
            [
                sys.executable, str(SCRIPT), str(self.db_path),
                "--fail-on-blockers", "--output", str(output),
            ],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertTrue(output.is_file())
        parsed = json.loads(output.read_text(encoding="utf-8"))
        self.assertFalse(parsed["summary"]["ready_for_control_plane_import"])
        self.assertNotIn(valid_hash(), output.read_text(encoding="utf-8"))

    def test_symlink_database_path_is_rejected(self):
        link = Path(self.temp.name) / "source-link.sqlite3"
        link.symlink_to(self.db_path)
        with self.assertRaises(ValueError):
            PREFLIGHT.build_report(link)


if __name__ == "__main__":
    unittest.main()
