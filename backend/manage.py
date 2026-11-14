"""Command line helpers:  python manage.py <command>

  create-admin   create (or reset) an administrator account
  seed-demo      load a realistic demo company into an EMPTY database
  backup FILE    copy the live database to FILE (safe while the server runs)
  restore FILE   replace the database with FILE (stop the server first!)
  check          run the accounting integrity checks
  check-files    verify private attachment bytes against tenant metadata
  scan-files     scan pending uploads with ClamAV (add --retry-errors to retry failures)
  audit-file-orphans [REPORT.json]  report untracked bytes; never deletes files
  backup-bundle FILE  create a consistent SQLite + attachments ZIP backup
  restore-bundle FILE  restore a DB + attachments bundle (maintenance mode required)
  recover-bundle-restore  roll back an interrupted restore journal
"""
import getpass
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

from erp import create_app
from erp import db
from erp import security
from erp.errors import ApiError
from erp.services import accounting, users


def main(argv):
    if len(argv) < 2 or argv[1] in ('-h', '--help'):
        print(__doc__)
        return 0
    cmd = argv[1]

    # Recovery must run before create_app: startup is deliberately blocked while
    # a restore journal exists.
    if cmd == 'recover-bundle-restore':
        from erp.config import Config
        from erp.services.backup_bundle import recover_interrupted_restore
        result = recover_interrupted_restore(Config.DB_PATH, Config.FILE_STORAGE_DIR)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if cmd == 'restore-bundle' and not ConfigMaintenanceEnabled():
        print('Restore is blocked. Stop the ERP server and set ERP_MAINTENANCE_MODE=1.')
        return 1

    app = create_app()
    with app.app_context():
        if cmd == 'create-admin':
            email = input('Admin email: ').strip()
            name = input('Full name: ').strip() or 'Administrator'
            password = getpass.getpass('Password (min 8, letters + digits): ')
            with db.transaction():
                row = db.one('SELECT id FROM users WHERE email=?', (email,))
                if row:
                    security.validate_password(password, email)
                    db.execute("UPDATE users SET password_hash=?, role='admin', is_active=1 WHERE id=?",
                               (security.hash_password(password), row['id']))
                    print('Existing user promoted to admin and password reset.')
                else:
                    users.create_user(email, name, password, 'admin')
                    print('Administrator created.')
        elif cmd == 'seed-demo':
            from erp.seed import demo_allowed, seed_demo
            if (db.scalar('SELECT COUNT(*) FROM companies') or 0) != 1:
                print('Demo seeding is disabled unless the database contains exactly one company.')
                return 1
            with db.transaction():
                if not demo_allowed():
                    print('The database already contains data; demo data is only loaded into an empty company.')
                    return 1
                seed_demo()
            print('Demo data loaded.')
        elif cmd == 'backup' and len(argv) > 2:
            if db.scalar('SELECT COUNT(*) FROM file_objects'):
                raise ApiError('file_backup_unavailable',
                               'Database-only backup would omit attachment bytes. Use backup-bundle.', 503)
            dst = sqlite3.connect(argv[2])
            try:
                db.get_db().backup(dst)
            finally:
                dst.close()
            print(f'Backup written to {argv[2]}')
        elif cmd == 'backup-bundle' and len(argv) > 2:
            from erp.services.backup_bundle import create_backup_bundle
            db.close_db()
            result = create_backup_bundle(app.config['DB_PATH'], app.config['FILE_STORAGE_DIR'], argv[2])
            print(json.dumps(result, ensure_ascii=False))
        elif cmd == 'restore' and len(argv) > 2:
            source = sqlite3.connect(argv[2])
            try:
                source_has_objects = source.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_objects'").fetchone()
                source_count = source.execute('SELECT COUNT(*) FROM file_objects').fetchone()[0] if source_has_objects else 0
            finally:
                source.close()
            current_count = db.scalar('SELECT COUNT(*) FROM file_objects') or 0
            if source_count or current_count:
                raise ApiError('file_restore_unavailable',
                               'Database-only restore could orphan or omit attachment bytes. Use restore-bundle.', 503)
            target = Path(app.config['DB_PATH'])
            db.close_db()
            if target.exists():
                shutil.copy(target, str(target) + '.before-restore')
            shutil.copy(argv[2], target)
            for ext in ('-wal', '-shm'):
                Path(str(target) + ext).unlink(missing_ok=True)
            print(f'Database restored from {argv[2]} (previous copy kept as .before-restore).')
        elif cmd == 'restore-bundle' and len(argv) > 2:
            from erp.services.backup_bundle import restore_backup_bundle
            db.close_db()
            result = restore_backup_bundle(argv[2], app.config['DB_PATH'], app.config['FILE_STORAGE_DIR'])
            print(json.dumps(result, ensure_ascii=False))
        elif cmd == 'check-files':
            from erp.services.file_storage import verify_storage_integrity
            result = verify_storage_integrity(storage_root=app.config['FILE_STORAGE_DIR'],
                                              frontend_root=app.config['FRONTEND_DIR'])
            print(f"Checked {result['checked']} files; OK: {result['ok']}; issues: {len(result['issues'])}")
            for issue in result['issues']:
                print('FAIL', issue['id'], issue['issue'])
            return 0 if not result['issues'] else 2
        elif cmd == 'scan-files':
            from erp.services.file_storage import scan_pending_files
            result = scan_pending_files(
                storage_root=app.config['FILE_STORAGE_DIR'], frontend_root=app.config['FRONTEND_DIR'],
                scanner_bin=app.config['CLAMSCAN_BIN'], include_errors='--retry-errors' in argv[2:],
            )
            print(json.dumps(result, ensure_ascii=False))
            return 2 if result['errors'] or result['infected'] else 0
        elif cmd == 'audit-file-orphans':
            from erp.services.file_storage import audit_orphan_files
            result = audit_orphan_files(storage_root=app.config['FILE_STORAGE_DIR'],
                                        frontend_root=app.config['FRONTEND_DIR'])
            report = json.dumps(result, ensure_ascii=False, indent=2)
            if len(argv) > 2:
                Path(argv[2]).write_text(report + '\n', encoding='utf-8')
                print(f"Orphan report written to {argv[2]} (no files deleted).")
            else:
                print(report)
            return 2 if result['unsafe_count'] else 0
        elif cmd == 'check':
            result = accounting.health_check()
            for c in result['checks']:
                print(('OK   ' if c['ok'] else 'FAIL ') + c['key'], c['params'] if not c['ok'] else '')
            return 0 if result['ok'] else 2
        else:
            print(__doc__)
            return 1
    return 0


def ConfigMaintenanceEnabled():
    from erp.config import Config
    return bool(getattr(Config, 'MAINTENANCE_MODE', False))


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv))
    except ApiError as e:
        print('Error:', e.message)
        sys.exit(1)
