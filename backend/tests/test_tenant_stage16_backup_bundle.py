"""Stage 16 tests for upload-scan migration and attachment-aware recovery bundles."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
ERP_ROOT = ROOT / "erp"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


migrations = _load_module("erp_stage16_migrations", ERP_ROOT / "migrations" / "__init__.py")
bundles = _load_module("erp_stage16_backup_bundle", ERP_ROOT / "services" / "backup_bundle.py")


class Stage16MigrationTests(unittest.TestCase):
    def _database_at_v7(self):
        conn = sqlite3.connect(":memory:", isolation_level=None)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript((ERP_ROOT / "schema.sql").read_text(encoding="utf-8"))
        conn.execute("INSERT INTO schema_version(version) VALUES(1)")
        conn.execute("INSERT INTO roles(name,label,permissions,is_system) VALUES('admin','Admin','[\"*\"]',1)")
        conn.execute("INSERT INTO settings(key,value) VALUES('company','\"Legacy\"')")
        conn.execute("INSERT INTO users(email,name,password_hash,role) VALUES('owner@example.test','Owner','hash','admin')")
        self.assertEqual(migrations.apply_migrations(conn, target_version=7), 7)
        return conn

    def test_v8_adds_fail_closed_scan_state_and_company_user_throttle(self):
        conn = self._database_at_v7()
        try:
            self.assertEqual(migrations.apply_migrations(conn, target_version=8), 8)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(file_objects)")}
            self.assertTrue({"scan_status", "scan_engine", "scanned_at"} <= columns)
            self.assertEqual(conn.execute("SELECT scan_status FROM file_objects").fetchall(), [])
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(file_objects)")}
            self.assertIn("idx_file_objects_company_scan", indexes)
            bucket_columns = {row[1] for row in conn.execute("PRAGMA table_info(file_upload_buckets)")}
            self.assertTrue({"company_id", "user_id", "bucket_start", "upload_count"} <= bucket_columns)
            pk = [row[1] for row in sorted(
                (r for r in conn.execute("PRAGMA table_info(file_upload_buckets)") if r[5]),
                key=lambda r: r[5],
            )]
            self.assertEqual(pk, ["company_id", "user_id", "bucket_start"])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()

    def test_v8_existing_objects_default_to_pending_and_migration_is_idempotent(self):
        conn = self._database_at_v7()
        try:
            conn.execute("INSERT INTO file_objects(company_id,storage_key,original_name,title,mime_type,size_bytes,sha256) "
                         "VALUES(1,?,'legacy.pdf','Legacy','application/pdf',4,?)",
                         ('a' * 32 + '.pdf', hashlib.sha256(b"test").hexdigest()))
            self.assertEqual(migrations.apply_migrations(conn, target_version=8), 8)
            self.assertEqual(conn.execute("SELECT scan_status FROM file_objects").fetchone()[0], "pending")
            self.assertEqual(migrations.apply_migrations(conn, target_version=8), 8)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_version WHERE version=8").fetchone()[0], 1)
        finally:
            conn.close()

    def test_v8_rejects_unknown_scan_state(self):
        conn = self._database_at_v7()
        try:
            self.assertEqual(migrations.apply_migrations(conn, target_version=8), 8)
            conn.execute("INSERT INTO file_objects(company_id,storage_key,original_name,title,mime_type,size_bytes,sha256) "
                         "VALUES(1,?,'f.pdf','F','application/pdf',4,?)",
                         ('f' * 32 + '.pdf', hashlib.sha256(b"test").hexdigest()))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE file_objects SET scan_status='bypassed' WHERE id=1")
        finally:
            conn.close()


class Stage16BackupBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="erp-stage16-bundle-")
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "live.sqlite3"
        self.storage = self.root / "private-files"
        self.storage.mkdir(mode=0o700)
        self._create_db(self.db_path)
        self.files = {
            "clean": (1, "1" * 32 + ".pdf", b"%PDF-1.7\nclean"),
            "infected": (2, "2" * 32 + ".txt", b"quarantined marker"),
            "pending": (1, "3" * 32 + ".csv", b"a,b\n1,2\n"),
        }
        conn = sqlite3.connect(self.db_path)
        for idx, (status, (company_id, key, payload)) in enumerate(self.files.items(), start=1):
            infected = status == "infected"
            folder = self.storage / ".quarantine" / str(company_id) if infected else self.storage / str(company_id)
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            (folder / key).write_bytes(payload)
            conn.execute(
                "INSERT INTO file_objects(id,company_id,storage_key,original_name,title,mime_type,size_bytes,sha256,scan_status) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (idx, company_id, key, f"{status}.{key.rsplit('.',1)[-1]}", status.title(),
                 "application/octet-stream", len(payload), hashlib.sha256(payload).hexdigest(),
                 "clean" if status == "clean" else ("infected" if infected else "pending")),
            )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _create_db(path: Path):
        conn = sqlite3.connect(path)
        conn.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE schema_version(version INTEGER NOT NULL);
            INSERT INTO schema_version(version) VALUES(8);
            CREATE TABLE companies(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            INSERT INTO companies VALUES(1,'Company One'),(2,'Company Two');
            CREATE TABLE users(id INTEGER PRIMARY KEY, email TEXT NOT NULL);
            INSERT INTO users VALUES(1,'one@example.test'),(2,'two@example.test');
            CREATE TABLE file_objects(
              id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
              storage_key TEXT NOT NULL UNIQUE, original_name TEXT NOT NULL, title TEXT NOT NULL,
              mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
              scan_status TEXT NOT NULL CHECK(scan_status IN ('pending','clean','infected','error')),
              UNIQUE(company_id,id));
        """)
        conn.commit()
        conn.close()

    def test_backup_captures_all_tenant_files_including_quarantine_and_pending(self):
        backup = self.root / "safe-backup.zip"
        result = bundles.create_backup_bundle(self.db_path, self.storage, backup)
        self.assertEqual(result["attachment_count"], 3)
        with zipfile.ZipFile(backup) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["attachment_count"], 3)
            self.assertEqual({x["scan_status"] for x in manifest["attachments"]}, {"clean", "infected", "pending"})
            self.assertIn("objects/2/" + self.files["infected"][1], archive.namelist())

    def test_restore_recreates_database_and_all_file_bytes_and_retains_rollback(self):
        backup = self.root / "safe-backup.zip"
        bundles.create_backup_bundle(self.db_path, self.storage, backup)
        target_db = self.root / "restored.sqlite3"
        target_storage = self.root / "restored-files"
        result = bundles.restore_backup_bundle(backup, target_db, target_storage)
        self.assertTrue(result["restored"])
        self.assertEqual(result["attachment_count"], 3)
        self.assertIsNone(result["rollback_database"])
        conn = sqlite3.connect(target_db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM file_objects").fetchone()[0], 3)
        conn.close()
        for state, (company_id, key, payload) in self.files.items():
            folder = target_storage / ".quarantine" / str(company_id) if state == "infected" else target_storage / str(company_id)
            restored = (folder / key).read_bytes()
            self.assertEqual(restored, payload)

    def test_failed_hash_validation_does_not_modify_live_targets(self):
        backup = self.root / "safe-backup.zip"
        bundles.create_backup_bundle(self.db_path, self.storage, backup)
        tampered = self.root / "tampered.zip"
        with zipfile.ZipFile(backup, "r") as src, zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as dst:
            for name in src.namelist():
                payload = src.read(name)
                if name == "objects/1/" + self.files["clean"][1]:
                    payload = b"%PDF-1.7\nchanged"
                dst.writestr(name, payload)
        before_db = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        before_file = (self.storage / "1" / self.files["clean"][1]).read_bytes()
        with self.assertRaises(bundles.BackupBundleError):
            bundles.restore_backup_bundle(tampered, self.db_path, self.storage)
        self.assertEqual(hashlib.sha256(self.db_path.read_bytes()).hexdigest(), before_db)
        self.assertEqual((self.storage / "1" / self.files["clean"][1]).read_bytes(), before_file)
        self.assertFalse(Path(str(self.db_path) + ".restore-in-progress.json").exists())


    def test_backup_refuses_missing_or_modified_attachment_bytes(self):
        backup = self.root / "unsafe-backup.zip"
        (self.storage / "1" / self.files["clean"][1]).write_bytes(b"changed bytes")
        with self.assertRaises(bundles.BackupBundleError):
            bundles.create_backup_bundle(self.db_path, self.storage, backup)
        self.assertFalse(backup.exists())

    def test_restore_keeps_rollback_copies_of_existing_live_database_and_storage(self):
        backup = self.root / "safe-backup.zip"
        bundles.create_backup_bundle(self.db_path, self.storage, backup)
        original_payload = self.files["clean"][2]
        changed_payload = b"post-backup changed payload"
        (self.storage / "1" / self.files["clean"][1]).write_bytes(changed_payload)
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE file_objects SET title='Changed after backup' WHERE id=1")
        conn.commit()
        conn.close()
        result = bundles.restore_backup_bundle(backup, self.db_path, self.storage)
        self.assertTrue(result["rollback_database"])
        self.assertTrue(result["rollback_storage"])
        self.assertTrue(Path(result["rollback_database"]).is_file())
        self.assertTrue(Path(result["rollback_storage"]).is_dir())
        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT title FROM file_objects WHERE id=1").fetchone()[0], "Clean")
        conn.close()
        self.assertEqual((self.storage / "1" / self.files["clean"][1]).read_bytes(), original_payload)
        self.assertEqual((Path(result["rollback_storage"]) / "1" / self.files["clean"][1]).read_bytes(), changed_payload)
        rollback_conn = sqlite3.connect(result["rollback_database"])
        self.assertEqual(rollback_conn.execute("SELECT title FROM file_objects WHERE id=1").fetchone()[0], "Changed after backup")
        rollback_conn.close()

    def test_restore_refuses_unsafe_path_overlap(self):
        backup = self.root / "safe-backup.zip"
        bundles.create_backup_bundle(self.db_path, self.storage, backup)
        with self.assertRaises(bundles.BackupBundleError):
            bundles.restore_backup_bundle(backup, self.db_path, self.db_path / "files")

    def test_restore_journal_recovery_rolls_back_both_targets(self):
        restore_id = "a" * 32
        db_backup = self.root / f".live.sqlite3.pre-restore-{restore_id}"
        storage_backup = self.root / f".private-files.pre-restore-{restore_id}"
        db_stage = self.root / f".live.sqlite3.restore-{restore_id}"
        storage_stage = self.root / f".private-files.restore-{restore_id}"
        # Simulate interruption after old paths were moved and new paths swapped.
        import shutil
        shutil.copy2(self.db_path, db_backup)
        shutil.copytree(self.storage, storage_backup)
        marker = Path(str(self.db_path) + ".restore-in-progress.json")
        marker.write_text(json.dumps({
            "restore_id": restore_id,
            "database_backup": str(db_backup), "storage_backup": str(storage_backup),
            "database_stage": str(db_stage), "storage_stage": str(storage_stage),
            "had_database": True, "had_storage": True,
        }), encoding="utf-8")
        self.db_path.write_bytes(b"new database")
        shutil.rmtree(self.storage)
        self.storage.mkdir()
        (self.storage / "new.txt").write_text("new files")
        result = bundles.recover_interrupted_restore(self.db_path, self.storage)
        self.assertTrue(result["recovered"])
        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM file_objects").fetchone()[0], 3)
        conn.close()
        self.assertEqual((self.storage / "1" / self.files["clean"][1]).read_bytes(), self.files["clean"][2])
        self.assertFalse(marker.exists())


    def test_backup_rejects_symlink_database_and_symlink_output(self):
        db_link = self.root / "db-link.sqlite3"
        db_link.symlink_to(self.db_path)
        with self.assertRaises(bundles.BackupBundleError):
            bundles.create_backup_bundle(db_link, self.storage, self.root / "link-db.zip")
        out_target = self.root / "actual.zip"
        out_target.write_bytes(b"keep")
        out_link = self.root / "link.zip"
        out_link.symlink_to(out_target)
        with self.assertRaises(bundles.BackupBundleError):
            bundles.create_backup_bundle(self.db_path, self.storage, out_link)
        self.assertEqual(out_target.read_bytes(), b"keep")

    def test_restore_rejects_symlink_backup_archive(self):
        backup = self.root / "safe-backup.zip"
        bundles.create_backup_bundle(self.db_path, self.storage, backup)
        link = self.root / "bundle-link.zip"
        link.symlink_to(backup)
        with self.assertRaises(bundles.BackupBundleError):
            bundles.restore_backup_bundle(link, self.root / "target.sqlite3", self.root / "target-files")

    def test_restored_database_and_bundle_files_are_private(self):
        backup = self.root / "safe-backup.zip"
        bundles.create_backup_bundle(self.db_path, self.storage, backup)
        target_db = self.root / "restored.sqlite3"
        target_storage = self.root / "restored-files"
        bundles.restore_backup_bundle(backup, target_db, target_storage)
        if os.name == "posix":
            self.assertEqual(target_db.stat().st_mode & 0o777, 0o600)
            self.assertEqual(target_storage.stat().st_mode & 0o777, 0o700)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_backup_refuses_pre_stage16_database_even_without_file_objects_table(self):
        old_db = self.root / "old.sqlite3"
        conn = sqlite3.connect(old_db)
        conn.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES(7)")
        conn.commit()
        conn.close()
        with self.assertRaises(bundles.BackupBundleError):
            bundles.create_backup_bundle(old_db, self.storage, self.root / "old.zip")

    def test_restore_rejects_directory_entries_in_zip(self):
        backup = self.root / "directory-entry.zip"
        with zipfile.ZipFile(backup, "w") as archive:
            archive.writestr("objects/", "")
            archive.writestr("manifest.json", "{}")
            archive.writestr("database.sqlite3", b"invalid")
        with self.assertRaises(bundles.BackupBundleError):
            bundles.restore_backup_bundle(backup, self.root / "restore.sqlite3", self.root / "restore-files")

    def test_recovery_rejects_journal_paths_outside_configured_parents(self):
        marker = Path(str(self.db_path) + ".restore-in-progress.json")
        marker.write_text(json.dumps({
            "restore_id": "b" * 32,
            "database_backup": str(self.root.parent / "attacker.sqlite3"),
            "storage_backup": str(self.root / f".private-files.pre-restore-{'b' * 32}"),
            "database_stage": str(self.root / f".live.sqlite3.restore-{'b' * 32}"),
            "storage_stage": str(self.root / f".private-files.restore-{'b' * 32}"),
            "had_database": True, "had_storage": True,
        }), encoding="utf-8")
        with self.assertRaises(bundles.BackupBundleError):
            bundles.recover_interrupted_restore(self.db_path, self.storage)
        self.assertTrue(marker.exists())


if __name__ == "__main__":
    unittest.main()
