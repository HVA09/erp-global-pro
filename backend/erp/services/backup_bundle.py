"""Integrity-checked SQLite + private attachment backup/restore bundles.

Restore requires maintenance mode and a stopped server. A journal prevents the
application from starting after an interrupted two-path swap until the operator
runs `python manage.py recover-bundle-restore`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

FORMAT_VERSION = 1
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_MANIFEST_BYTES = 20 * 1024 * 1024
MAX_DATABASE_BYTES = 4 * 1024 * 1024 * 1024
MAX_BUNDLE_BYTES = 20 * 1024 * 1024 * 1024
STORAGE_KEY_RE = re.compile(r"^[0-9a-f]{32}\.(?:pdf|png|jpg|jpeg|webp|txt|csv)$")
SCAN_STATES = {"pending", "clean", "infected", "error"}


class BackupBundleError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_root(storage_root: Path) -> Path:
    storage_root = Path(storage_root).expanduser()
    if storage_root.is_symlink():
        raise BackupBundleError("Storage root must not be a symbolic link.")
    storage_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if storage_root.is_symlink():
        raise BackupBundleError("Storage root was replaced by a symbolic link.")
    root = storage_root.resolve(strict=True)
    if root == Path(root.anchor):
        raise BackupBundleError("Refusing to use a filesystem root as attachment storage.")
    return root


def _object_path(root: Path, company_id: int, storage_key: str, infected: bool) -> Path:
    company_id = int(company_id)
    if company_id <= 0 or not STORAGE_KEY_RE.fullmatch(str(storage_key)):
        raise BackupBundleError("Attachment metadata contains an invalid tenant or storage key.")
    expected_parent = root / ".quarantine" if infected else root
    if expected_parent.is_symlink():
        raise BackupBundleError("Attachment storage parent contains a symbolic link.")
    expected_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if expected_parent.is_symlink():
        raise BackupBundleError("Attachment storage parent was replaced by a symbolic link.")
    expected_parent = expected_parent.resolve(strict=True)
    parent = expected_parent / str(company_id)
    if parent.is_symlink():
        raise BackupBundleError("Attachment tenant directory contains a symbolic link.")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if parent.is_symlink():
        raise BackupBundleError("Attachment tenant directory was replaced by a symbolic link.")
    resolved_parent = parent.resolve(strict=True)
    if resolved_parent.parent != expected_parent or resolved_parent.name != str(company_id):
        raise BackupBundleError("Attachment path escaped its tenant directory.")
    path = resolved_parent / storage_key
    if path.is_symlink():
        raise BackupBundleError("Attachment bytes must not be symbolic links.")
    return path


def _snapshot_database(source_path: Path, target_path: Path) -> None:
    if not source_path.is_file() or source_path.is_symlink():
        raise BackupBundleError("The configured SQLite database file is unavailable or unsafe.")
    source = sqlite3.connect(str(source_path), timeout=30)
    target = sqlite3.connect(str(target_path), timeout=30)
    try:
        source.backup(target)
        target.commit()
        result = target.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise BackupBundleError(f"Database integrity check failed: {result}")
        if target.execute("PRAGMA foreign_key_check").fetchall():
            raise BackupBundleError("Database contains foreign-key violations; backup refused.")
    finally:
        target.close()
        source.close()


def _read_attachment_rows(database_path: Path):
    conn = sqlite3.connect(str(database_path))
    conn.row_factory = sqlite3.Row
    try:
        version_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_objects'"
        ).fetchone()
        if not version_table or not exists:
            raise BackupBundleError("Database must be migrated to Stage 16 before backup.")
        version = conn.execute("SELECT COALESCE(MAX(version),0) FROM schema_version").fetchone()[0]
        if int(version) < 8:
            raise BackupBundleError("Database must be migrated to SQLite schema v8 before backup.")
        columns = {row[1] for row in conn.execute('PRAGMA table_info(file_objects)')}
        required = {"id", "company_id", "storage_key", "size_bytes", "sha256", "scan_status"}
        if not required <= columns:
            raise BackupBundleError("Database must be migrated to Stage 16 before attachment-aware backup.")
        return [dict(row) for row in conn.execute(
            "SELECT id,company_id,storage_key,size_bytes,sha256,scan_status FROM file_objects ORDER BY id"
        )]
    finally:
        conn.close()


def create_backup_bundle(database_path, storage_root, output_path):
    """Create a ZIP with a consistent SQLite snapshot, all referenced file bytes and hashes."""
    database_input = Path(database_path).expanduser()
    if database_input.is_symlink() or not database_input.is_file():
        raise BackupBundleError("The configured SQLite database must be a regular non-symlink file.")
    database_path = database_input.resolve(strict=True)
    storage_root = _safe_root(Path(storage_root))
    output_path = Path(output_path).expanduser()
    if output_path.is_symlink():
        raise BackupBundleError("Backup output must not be a symbolic link.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not output_path.is_file():
        raise BackupBundleError("Output path must be a regular file.")
    output_resolved = output_path.resolve(strict=False)
    if (output_resolved == database_path or _is_inside(output_resolved, storage_root)
            or _is_inside(database_path, storage_root)):
        raise BackupBundleError("Database, storage and backup paths overlap unsafely.")

    with tempfile.TemporaryDirectory(prefix=".erp-bundle-", dir=str(output_path.parent)) as work:
        work_dir = Path(work)
        snapshot_db = work_dir / "database.sqlite3"
        _snapshot_database(database_path, snapshot_db)
        try:
            os.chmod(snapshot_db, 0o600)
        except OSError:
            pass
        rows = _read_attachment_rows(snapshot_db)
        attachments = []
        for row in rows:
            status = row["scan_status"]
            if status not in SCAN_STATES:
                raise BackupBundleError(f"Attachment {row['id']} has an unknown scan status.")
            path = _object_path(storage_root, row["company_id"], row["storage_key"], status == "infected")
            if not path.is_file():
                raise BackupBundleError(f"Attachment {row['id']} is missing; backup refused.")
            resolved = path.resolve(strict=True)
            if resolved.parent != path.parent.resolve(strict=True):
                raise BackupBundleError(f"Attachment {row['id']} escaped its storage directory.")
            if resolved.stat().st_size != int(row["size_bytes"]):
                raise BackupBundleError(f"Attachment {row['id']} size mismatch; backup refused.")
            digest = _sha256_file(resolved)
            if digest != row["sha256"]:
                raise BackupBundleError(f"Attachment {row['id']} SHA-256 mismatch; backup refused.")
            attachments.append({
                "id": int(row["id"]), "company_id": int(row["company_id"]),
                "storage_key": row["storage_key"], "size_bytes": int(row["size_bytes"]),
                "sha256": row["sha256"], "scan_status": status,
                "archive_path": f"objects/{int(row['company_id'])}/{row['storage_key']}",
            })

        manifest = {
            "format": "erp-global-pro-backup",
            "format_version": FORMAT_VERSION,
            "created_at_unix": int(time.time()),
            "database_sha256": _sha256_file(snapshot_db),
            "attachment_count": len(attachments),
            "attachments": attachments,
        }
        temp_archive = work_dir / "bundle.zip"
        with zipfile.ZipFile(temp_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(snapshot_db, "database.sqlite3")
            for item in attachments:
                row_status_infected = item["scan_status"] == "infected"
                source = _object_path(storage_root, item["company_id"], item["storage_key"], row_status_infected)
                digest = hashlib.sha256()
                copied = 0
                info = zipfile.ZipInfo(item["archive_path"])
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o100600 & 0xFFFF) << 16
                with source.open("rb") as src, archive.open(info, "w") as dst:
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        copied += len(chunk)
                        digest.update(chunk)
                        dst.write(chunk)
                if copied != item["size_bytes"] or digest.hexdigest() != item["sha256"]:
                    raise BackupBundleError(f"Attachment {item['id']} changed while backup was being created.")
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )

        fd, temp_name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=str(output_path.parent))
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            shutil.copyfile(temp_archive, temp_path)
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
            os.replace(temp_path, output_path)
        finally:
            temp_path.unlink(missing_ok=True)
    return {"path": str(output_path.resolve()), "attachment_count": len(rows),
            "database_sha256": manifest["database_sha256"], "bundle_bytes": output_path.stat().st_size}


def _validated_zip_members(archive: zipfile.ZipFile):
    infos = archive.infolist()
    if len(infos) > 100_002:
        raise BackupBundleError("Backup archive contains too many entries.")
    names = set()
    total = 0
    for info in infos:
        name = info.filename
        path = PurePosixPath(name)
        mode = (info.external_attr >> 16) & 0xFFFF
        if (not name or "\\" in name or path.is_absolute() or ".." in path.parts
                or "." in path.parts or "" in path.parts or name.startswith("/")
                or info.is_dir() or stat.S_ISLNK(mode)):
            raise BackupBundleError("Backup archive contains an unsafe path, directory entry or symbolic link.")
        if name in names:
            raise BackupBundleError("Backup archive contains duplicate entries.")
        names.add(name)
        total += info.file_size
        if total > MAX_BUNDLE_BYTES:
            raise BackupBundleError("Backup archive exceeds the configured restore safety limit.")
        if name == "manifest.json" and info.file_size > MAX_MANIFEST_BYTES:
            raise BackupBundleError("Backup manifest is too large.")
        if name == "database.sqlite3" and info.file_size > MAX_DATABASE_BYTES:
            raise BackupBundleError("Backup database is too large.")
        if name.startswith("objects/") and info.file_size > MAX_FILE_BYTES:
            raise BackupBundleError("An attachment in the backup exceeds the maximum file size.")
    return names


def _validate_manifest(manifest, names):
    if not isinstance(manifest, dict) or manifest.get("format") != "erp-global-pro-backup":
        raise BackupBundleError("Unsupported or invalid backup manifest.")
    if manifest.get("format_version") != FORMAT_VERSION:
        raise BackupBundleError("Unsupported backup format version.")
    attachments = manifest.get("attachments")
    if not isinstance(attachments, list) or manifest.get("attachment_count") != len(attachments):
        raise BackupBundleError("Backup attachment inventory is inconsistent.")
    expected = {"database.sqlite3", "manifest.json"}
    ids, archive_paths = set(), set()
    for item in attachments:
        if not isinstance(item, dict):
            raise BackupBundleError("Invalid attachment entry in backup manifest.")
        try:
            file_id = int(item["id"])
            cid = int(item["company_id"])
            size = int(item["size_bytes"])
            key = str(item["storage_key"])
            status = str(item["scan_status"])
            digest = str(item["sha256"])
            archive_path = str(item["archive_path"])
        except (KeyError, TypeError, ValueError):
            raise BackupBundleError("Attachment manifest entry is incomplete.")
        if file_id <= 0 or cid <= 0 or size <= 0 or size > MAX_FILE_BYTES:
            raise BackupBundleError("Attachment manifest contains invalid numeric values.")
        if not STORAGE_KEY_RE.fullmatch(key) or status not in SCAN_STATES:
            raise BackupBundleError("Attachment manifest contains an invalid key or scan status.")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BackupBundleError("Attachment manifest contains an invalid SHA-256 value.")
        expected_path = f"objects/{cid}/{key}"
        if archive_path != expected_path:
            raise BackupBundleError("Attachment archive path does not match its tenant metadata.")
        if file_id in ids or archive_path in archive_paths:
            raise BackupBundleError("Backup manifest contains duplicate attachment identity.")
        ids.add(file_id)
        archive_paths.add(archive_path)
        expected.add(archive_path)
    if names != expected:
        raise BackupBundleError("Backup archive entries do not match the manifest inventory.")
    return attachments


def _validate_staged_database(db_path: Path, manifest_attachments):
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise BackupBundleError(f"Restored database integrity check failed: {result}")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise BackupBundleError("Restored database contains foreign-key violations.")
        version = conn.execute("SELECT COALESCE(MAX(version),0) FROM schema_version").fetchone()[0]
        if int(version) < 8:
            raise BackupBundleError("Backup database must be migrated to SQLite schema v8.")
        rows = [dict(row) for row in conn.execute(
            "SELECT id,company_id,storage_key,size_bytes,sha256,scan_status FROM file_objects ORDER BY id"
        )]
        by_id = {int(row["id"]): row for row in rows}
        if len(by_id) != len(rows) or len(rows) != len(manifest_attachments):
            raise BackupBundleError("Database attachment records do not match the manifest count.")
        for item in manifest_attachments:
            row = by_id.get(int(item["id"]))
            if not row:
                raise BackupBundleError(f"Attachment {item['id']} is missing from the database snapshot.")
            for field in ("company_id", "storage_key", "size_bytes", "sha256", "scan_status"):
                if str(row[field]) != str(item[field]):
                    raise BackupBundleError(f"Attachment {item['id']} does not match database metadata ({field}).")
        # Ensure the restored DB is self-contained before it becomes live.
        conn.execute("PRAGMA journal_mode=DELETE")
    finally:
        conn.close()


def _fsync_directory(path: Path):
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        # Directory fsync is not available on every supported platform.
        pass


def _write_restore_marker(marker_path: Path, state: dict):
    if marker_path.exists() or marker_path.is_symlink():
        raise BackupBundleError("A restore journal already exists; recover it before starting another restore.")
    temp = marker_path.with_name(marker_path.name + "." + uuid.uuid4().hex + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temp, flags, 0o600)
    try:
        payload = json.dumps(state, sort_keys=True).encode("utf-8")
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, marker_path)
        _fsync_directory(marker_path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temp.unlink(missing_ok=True)


def _validate_journal_path(value, parent: Path, expected_name: str, kind: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.parent != parent or path.name != expected_name:
        raise BackupBundleError("Restore journal contains an unexpected staging or rollback path.")
    if path.is_symlink():
        raise BackupBundleError("Restore journal paths must not be symbolic links.")
    if path.exists() and kind == "file" and not path.is_file():
        raise BackupBundleError("Restore journal database path is not a regular file.")
    if path.exists() and kind == "directory" and not path.is_dir():
        raise BackupBundleError("Restore journal storage path is not a directory.")
    return path


def recover_interrupted_restore(database_path, storage_root):
    database_input = Path(database_path).expanduser()
    storage_input = Path(storage_root).expanduser()
    if database_input.is_symlink() or storage_input.is_symlink():
        raise BackupBundleError("Recovery targets must not be symbolic links.")
    database_path = database_input.resolve(strict=False)
    storage_root = storage_input.resolve(strict=False)
    marker = database_path.with_name(database_path.name + ".restore-in-progress.json")
    if not marker.exists():
        return {"recovered": False, "reason": "no_marker"}
    if marker.is_symlink() or database_path.is_symlink() or storage_root.is_symlink():
        raise BackupBundleError("Restore journal and configured targets must not be symbolic links.")
    try:
        state = json.loads(marker.read_text(encoding="utf-8"))
        restore_id = str(state.get("restore_id", ""))
        if not re.fullmatch(r"[0-9a-f]{32}", restore_id):
            raise BackupBundleError("Restore journal identifier is invalid.")
        if type(state.get("had_database")) is not bool or type(state.get("had_storage")) is not bool:
            raise BackupBundleError("Restore journal flags are invalid.")
        db_backup = _validate_journal_path(
            state["database_backup"], database_path.parent,
            f".{database_path.name}.pre-restore-{restore_id}", "file")
        storage_backup = _validate_journal_path(
            state["storage_backup"], storage_root.parent,
            f".{storage_root.name}.pre-restore-{restore_id}", "directory")
        db_stage = _validate_journal_path(
            state["database_stage"], database_path.parent,
            f".{database_path.name}.restore-{restore_id}", "file")
        storage_stage = _validate_journal_path(
            state["storage_stage"], storage_root.parent,
            f".{storage_root.name}.restore-{restore_id}", "directory")
        # Restore old versions when they were moved. Preserve untouched originals
        # when a crash happened before the corresponding move.
        if state.get("had_database"):
            if db_backup.exists():
                Path(str(database_path) + "-wal").unlink(missing_ok=True)
                Path(str(database_path) + "-shm").unlink(missing_ok=True)
                database_path.unlink(missing_ok=True)
                os.replace(db_backup, database_path)
        else:
            database_path.unlink(missing_ok=True)
        if state.get("had_storage"):
            if storage_backup.exists():
                if storage_root.exists():
                    shutil.rmtree(storage_root)
                os.replace(storage_backup, storage_root)
        else:
            if storage_root.exists():
                shutil.rmtree(storage_root)
        for path in (db_stage, storage_stage):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        _fsync_directory(marker.parent)
        return {"recovered": True}
    except Exception as exc:
        raise BackupBundleError(f"Restore recovery failed; keep the server stopped: {exc}") from exc


def restore_backup_bundle(bundle_path, database_path, storage_root):
    """Validate everything first, then swap DB + files with rollback journal.

    Run only with ERP_MAINTENANCE_MODE=1 and the application server stopped.
    Old DB and storage directories are retained as rollback copies on success.
    """
    bundle_input = Path(bundle_path).expanduser()
    database_input = Path(database_path).expanduser()
    storage_input = Path(storage_root).expanduser()
    if bundle_input.is_symlink() or not bundle_input.is_file():
        raise BackupBundleError("Backup bundle must be a regular non-symlink file.")
    if database_input.is_symlink() or storage_input.is_symlink():
        raise BackupBundleError("Database and storage targets must not be symbolic links.")
    bundle_path = bundle_input.resolve(strict=True)
    database_path = database_input.resolve(strict=False)
    storage_root = storage_input.resolve(strict=False)
    if database_path.exists() and not database_path.is_file():
        raise BackupBundleError("Database target exists but is not a regular file.")
    if storage_root.exists() and not storage_root.is_dir():
        raise BackupBundleError("Storage target exists but is not a directory.")
    if storage_root == Path(storage_root.anchor):
        raise BackupBundleError("Refusing to restore attachments to a filesystem root.")
    if database_path == storage_root or _is_inside(database_path, storage_root) or _is_inside(storage_root, database_path):
        raise BackupBundleError("Database file and attachment storage paths overlap unsafely.")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    storage_root.parent.mkdir(parents=True, exist_ok=True)
    if os.stat(database_path.parent).st_dev != os.stat(storage_root.parent).st_dev:
        raise BackupBundleError("Database and storage parents must be on the same filesystem for rollback-safe restore.")

    marker = database_path.with_name(database_path.name + ".restore-in-progress.json")
    if marker.exists():
        raise BackupBundleError("A previous restore is incomplete. Run recover-bundle-restore first.")

    restore_id = uuid.uuid4().hex
    db_stage = database_path.parent / f".{database_path.name}.restore-{restore_id}"
    storage_stage = storage_root.parent / f".{storage_root.name}.restore-{restore_id}"
    db_backup = database_path.parent / f".{database_path.name}.pre-restore-{restore_id}"
    storage_backup = storage_root.parent / f".{storage_root.name}.pre-restore-{restore_id}"
    storage_stage.mkdir(mode=0o700)

    try:
        with zipfile.ZipFile(bundle_path, "r") as archive:
            names = _validated_zip_members(archive)
            if not {"manifest.json", "database.sqlite3"} <= names:
                raise BackupBundleError("Backup archive is missing required entries.")
            bad = archive.testzip()
            if bad:
                raise BackupBundleError(f"Backup archive CRC check failed for {bad}.")
            manifest = json.loads(archive.read("manifest.json"))
            attachments = _validate_manifest(manifest, names)

            with archive.open("database.sqlite3", "r") as src, db_stage.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            try:
                os.chmod(db_stage, 0o600)
            except OSError:
                pass
            if _sha256_file(db_stage) != manifest.get("database_sha256"):
                raise BackupBundleError("Database snapshot SHA-256 does not match the manifest.")
            _validate_staged_database(db_stage, attachments)

            for item in attachments:
                cid = int(item["company_id"])
                key = item["storage_key"]
                infected = item["scan_status"] == "infected"
                rel = Path(".quarantine") / str(cid) / key if infected else Path(str(cid)) / key
                target = storage_stage / rel
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                target_parent = target.parent.resolve(strict=True)
                if not _is_inside(target_parent, storage_stage.resolve(strict=True)):
                    raise BackupBundleError("Attachment restore path escaped the staging directory.")
                digest = hashlib.sha256()
                copied = 0
                with archive.open(item["archive_path"], "r") as src, target.open("xb") as dst:
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        copied += len(chunk)
                        digest.update(chunk)
                        dst.write(chunk)
                    dst.flush()
                    os.fsync(dst.fileno())
                if copied != int(item["size_bytes"]) or digest.hexdigest() != item["sha256"]:
                    raise BackupBundleError(f"Attachment {item['id']} failed SHA-256 or size validation.")
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    pass

        # Keep a recovery journal before the first live rename. On interruption,
        # create_app refuses to serve requests until the operator recovers.
        state = {
            "restore_id": restore_id,
            "database_backup": str(db_backup), "storage_backup": str(storage_backup),
            "database_stage": str(db_stage), "storage_stage": str(storage_stage),
            "had_database": database_path.exists(), "had_storage": storage_root.exists(),
        }
        _write_restore_marker(marker, state)
        if database_path.exists():
            conn = sqlite3.connect(str(database_path), timeout=5)
            try:
                checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint and (int(checkpoint[0]) != 0 or int(checkpoint[1]) != int(checkpoint[2])):
                    raise BackupBundleError("SQLite WAL checkpoint is busy or incomplete; stop all database users and retry.")
                conn.execute("BEGIN EXCLUSIVE")
                conn.rollback()
            finally:
                conn.close()
            os.replace(database_path, db_backup)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(database_path) + suffix)
            if sidecar.exists():
                # With the server stopped, sidecars should normally be empty/absent.
                # They are not reused with the restored database.
                sidecar.unlink()
        if storage_root.exists():
            os.replace(storage_root, storage_backup)
        os.replace(storage_stage, storage_root)
        os.replace(db_stage, database_path)
        try:
            os.chmod(database_path, 0o600)
            os.chmod(storage_root, 0o700)
        except OSError:
            pass
        marker.unlink(missing_ok=True)
        _fsync_directory(marker.parent)
        _fsync_directory(storage_root.parent)
        return {
            "restored": True, "attachment_count": len(attachments),
            "rollback_database": str(db_backup) if state["had_database"] else None,
            "rollback_storage": str(storage_backup) if state["had_storage"] else None,
        }
    except BaseException:
        if marker.exists():
            try:
                recover_interrupted_restore(database_path, storage_root)
            except Exception:
                # Preserve the journal and staged backups for manual recovery.
                pass
        else:
            db_stage.unlink(missing_ok=True)
            if storage_stage.exists():
                shutil.rmtree(storage_stage, ignore_errors=True)
        raise
