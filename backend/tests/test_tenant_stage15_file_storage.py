"""Stage 15 tests: attachment migration, tenant storage and API guardrails.

Flask is not installed in this offline test environment. The real service code
runs against SQLite; the route tests use narrow Flask-compatible stubs only.
"""
from contextlib import contextmanager
import importlib.util
import io
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
ERP_ROOT = ROOT / 'erp'

# Load the real migration module without importing the Flask application.
spec = importlib.util.spec_from_file_location('erp_stage15_migrations', ERP_ROOT / 'migrations' / '__init__.py')
migrations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migrations)


class Stage15MigrationTests(unittest.TestCase):
    def test_v7_creates_tenant_owned_file_object_table_and_indexes(self):
        conn = sqlite3.connect(':memory:', isolation_level=None)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript((ERP_ROOT / 'schema.sql').read_text(encoding='utf-8'))
        conn.execute('INSERT INTO schema_version(version) VALUES(1)')
        conn.execute("INSERT INTO roles(name,label,permissions,is_system) VALUES('admin','Admin','[\"*\"]',1)")
        conn.execute("INSERT INTO settings(key,value) VALUES('company','\"Legacy\"')")
        conn.execute("INSERT INTO users(email,name,password_hash,role) VALUES('owner@example.test','Owner','hash','admin')")
        try:
            self.assertEqual(migrations.apply_migrations(conn, target_version=6), 6)
            conn.execute("INSERT INTO companies(name) VALUES('Second Co')")
            self.assertEqual(migrations.apply_migrations(conn, target_version=7), 7)
            columns = {r[1] for r in conn.execute('PRAGMA table_info(file_objects)')}
            self.assertTrue({'company_id', 'storage_key', 'sha256', 'uploaded_by'} <= columns)
            indexes = {r[1] for r in conn.execute('PRAGMA index_list(file_objects)')}
            self.assertIn('idx_file_objects_company_created', indexes)
            self.assertIn('idx_file_objects_company_hash', indexes)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(migrations.apply_migrations(conn, target_version=7), 7)
        finally:
            conn.close()

    def test_v7_rejects_invalid_sizes_and_unknown_companies(self):
        conn = sqlite3.connect(':memory:', isolation_level=None)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript((ERP_ROOT / 'schema.sql').read_text(encoding='utf-8'))
        conn.execute('INSERT INTO schema_version(version) VALUES(1)')
        conn.execute("INSERT INTO roles(name,label,permissions,is_system) VALUES('admin','Admin','[\"*\"]',1)")
        conn.execute("INSERT INTO users(email,name,password_hash,role) VALUES('owner@example.test','Owner','hash','admin')")
        try:
            migrations.apply_migrations(conn, target_version=7)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO file_objects(company_id,storage_key,original_name,title,mime_type,size_bytes,sha256) "
                    "VALUES(999,'a.pdf','a.pdf','A','application/pdf',10,?)", ('a' * 64,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO file_objects(company_id,storage_key,original_name,title,mime_type,size_bytes,sha256) "
                    "VALUES(1,'b.pdf','b.pdf','B','application/pdf',0,?)", ('b' * 64,))
        finally:
            conn.close()


class FakeG(dict):
    __getattr__ = dict.__getitem__

    def __setattr__(self, key, value):
        self[key] = value


class FakeResponse:
    def __init__(self):
        self.headers = {}


class FakeBlueprint:
    def __init__(self, *args, **kwargs):
        self.routes = {}

    def _route(self, *args, **kwargs):
        def decorate(fn):
            self.routes[(args, tuple(sorted(kwargs.get('methods', ()))))] = fn
            return fn
        return decorate

    get = _route
    post = _route
    delete = _route


class Stage15StorageApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prefix = 'erp_stage15_test'
        cls.saved = {}
        modules = [
            'flask', cls.prefix, f'{cls.prefix}.db', f'{cls.prefix}.audit', f'{cls.prefix}.errors',
            f'{cls.prefix}.security', f'{cls.prefix}.services', f'{cls.prefix}.services.tenancy',
            f'{cls.prefix}.services.file_storage', f'{cls.prefix}.api', f'{cls.prefix}.api.file_objects',
        ]
        for name in modules:
            cls.saved[name] = sys.modules.get(name)

        flask = types.ModuleType('flask')
        flask.g = FakeG(company={'id': 1}, user={'id': 1})
        flask.request = FakeG(args={}, form={}, files={})
        flask.current_app = FakeG(config={})
        flask.Blueprint = FakeBlueprint
        flask.jsonify = lambda *args, **kwargs: (args[0] if args else kwargs)
        def fake_send_file(*args, **kwargs):
            response = FakeResponse()
            response.headers['Content-Disposition'] = 'attachment; filename=\"' + kwargs.get('download_name', 'file') + '\"'
            return response
        flask.send_file = fake_send_file
        flask.has_request_context = lambda: True
        sys.modules['flask'] = flask
        cls.flask = flask

        package = types.ModuleType(cls.prefix)
        package.__path__ = [str(ERP_ROOT)]
        sys.modules[cls.prefix] = package

        db = types.ModuleType(f'{cls.prefix}.db')
        db.conn = sqlite3.connect(':memory:', isolation_level=None)
        db.conn.row_factory = sqlite3.Row
        db.conn.execute('PRAGMA foreign_keys=ON')
        db.conn.executescript('''
            CREATE TABLE companies(id INTEGER PRIMARY KEY, name TEXT, status TEXT DEFAULT 'active');
            INSERT INTO companies(id,name) VALUES(1,'One'),(2,'Two');
            CREATE TABLE users(id INTEGER PRIMARY KEY, email TEXT);
            INSERT INTO users(id,email) VALUES(1,'one@example.test'),(2,'two@example.test');
            CREATE TABLE file_objects(
              id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL REFERENCES companies(id),
              storage_key TEXT NOT NULL UNIQUE, original_name TEXT NOT NULL, title TEXT NOT NULL,
              category TEXT, related_to TEXT, note TEXT, mime_type TEXT NOT NULL,
              size_bytes INTEGER NOT NULL CHECK(size_bytes>0 AND size_bytes<=10485760),
              sha256 TEXT NOT NULL, uploaded_by INTEGER REFERENCES users(id),
              scan_status TEXT NOT NULL DEFAULT 'pending' CHECK(scan_status IN ('pending','clean','infected','error')),
              scan_engine TEXT, scanned_at TEXT,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              UNIQUE(company_id,id));
            CREATE TABLE file_upload_buckets(
              company_id INTEGER NOT NULL REFERENCES companies(id),
              user_id INTEGER NOT NULL REFERENCES users(id), bucket_start INTEGER NOT NULL,
              upload_count INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(company_id,user_id,bucket_start));
        ''')
        db.q = lambda sql, params=(): [dict(row) for row in db.conn.execute(sql, params).fetchall()]
        db.one = lambda sql, params=(): (dict(row) if (row := db.conn.execute(sql, params).fetchone()) else None)
        db.scalar = lambda sql, params=(): (row[0] if (row := db.conn.execute(sql, params).fetchone()) else None)
        db.execute = lambda sql, params=(): db.conn.execute(sql, params).lastrowid
        db.run = lambda sql, params=(): db.conn.execute(sql, params)

        @contextmanager
        def transaction():
            db.conn.execute('BEGIN IMMEDIATE')
            try:
                yield db.conn
            except BaseException:
                db.conn.rollback()
                raise
            else:
                db.conn.commit()
        db.transaction = transaction
        sys.modules[db.__name__] = db
        package.db = db
        cls.db = db

        audit = types.ModuleType(f'{cls.prefix}.audit')
        audit.events = []
        audit.log = lambda *args, **kwargs: audit.events.append((args, kwargs))
        sys.modules[audit.__name__] = audit
        package.audit = audit

        errors = types.ModuleType(f'{cls.prefix}.errors')
        class ApiError(Exception):
            def __init__(self, code, message=None, status=400, params=None, details=None):
                super().__init__(message or code)
                self.code, self.status, self.details = code, status, details
        errors.ApiError = ApiError
        sys.modules[errors.__name__] = errors
        cls.ApiError = ApiError

        security = types.ModuleType(f'{cls.prefix}.security')
        security.require = lambda *args: (lambda fn: fn)
        sys.modules[security.__name__] = security

        services = types.ModuleType(f'{cls.prefix}.services')
        services.__path__ = [str(ERP_ROOT / 'services')]
        sys.modules[services.__name__] = services
        package.services = services

        tenancy = types.ModuleType(f'{cls.prefix}.services.tenancy')
        def current_company_id(company_id=None):
            active_id = int(flask.g['company']['id'])
            if company_id is not None and int(company_id) != active_id:
                raise ApiError('company_context_mismatch', status=403)
            if not db.one("SELECT id FROM companies WHERE id=? AND status='active'", (active_id,)):
                raise ApiError('company_not_found', status=404)
            return active_id
        tenancy.current_company_id = current_company_id
        sys.modules[tenancy.__name__] = tenancy
        services.tenancy = tenancy

        spec = importlib.util.spec_from_file_location(
            f'{cls.prefix}.services.file_storage', ERP_ROOT / 'services' / 'file_storage.py')
        file_storage = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = file_storage
        spec.loader.exec_module(file_storage)
        services.file_storage = file_storage
        cls.file_storage = file_storage

        api = types.ModuleType(f'{cls.prefix}.api')
        api.__path__ = [str(ERP_ROOT / 'api')]
        sys.modules[api.__name__] = api
        spec = importlib.util.spec_from_file_location(
            f'{cls.prefix}.api.file_objects', ERP_ROOT / 'api' / 'file_objects.py')
        api_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = api_module
        spec.loader.exec_module(api_module)
        cls.api = api_module
        cls.fake_flask = flask
        cls.db = db
        cls._module_names = modules

    @classmethod
    def tearDownClass(cls):
        try:
            cls.db.conn.close()
        except Exception:
            pass
        for name, previous in cls.saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'private-files'
        self.frontend = Path(self.temp.name) / 'frontend'
        self.frontend.mkdir()
        self.fake_flask.current_app.config = {
            'FILE_STORAGE_DIR': str(self.root), 'FRONTEND_DIR': str(self.frontend),
            'FILE_QUOTA_BYTES': 250 * 1024 * 1024, 'FILE_UPLOADS_PER_HOUR': 30,
        }
        self.fake_flask.g['company'] = {'id': 1}
        self.fake_flask.g['user'] = {'id': 1}
        self.fake_flask.request.args = {}
        self.fake_flask.request.form = {}
        self.fake_flask.request.files = {}
        self.db.conn.execute('DELETE FROM file_objects')
        self.db.conn.execute('DELETE FROM file_upload_buckets')

    def tearDown(self):
        self.temp.cleanup()

    def upload(self, company_id=1, filename='proof.pdf', content=b'%PDF-1.7\nbody'):
        self.fake_flask.g['company'] = {'id': company_id}
        return self.file_storage.upload_bytes(
            filename=filename, content=content, title='Evidence', company_id=company_id,
            uploaded_by=company_id, storage_root=self.root, frontend_root=self.frontend,
        )

    def test_two_companies_can_upload_same_filename_and_list_only_their_own(self):
        first = self.upload(1)
        second = self.upload(2)
        self.assertEqual(first['original_name'], second['original_name'])
        self.assertNotEqual(first['id'], second['id'])
        self.fake_flask.g['company'] = {'id': 1}
        rows = self.file_storage.list_metadata()
        self.assertEqual([row['id'] for row in rows], [first['id']])
        self.assertTrue((self.root / '1' / self.db.one('SELECT storage_key FROM file_objects WHERE id=?', (first['id'],))['storage_key']).is_file())

    def test_foreign_company_cannot_read_download_or_delete_attachment(self):
        row = self.upload(1)
        self.fake_flask.g['company'] = {'id': 2}
        with self.assertRaises(self.ApiError) as get_error:
            self.file_storage.get_metadata(row['id'])
        self.assertEqual(get_error.exception.status, 404)
        with self.assertRaises(self.ApiError) as download_error:
            self.file_storage.get_download(row['id'], storage_root=self.root, frontend_root=self.frontend)
        self.assertEqual(download_error.exception.status, 404)
        with self.assertRaises(self.ApiError) as delete_error:
            self.file_storage.delete_file(row['id'], storage_root=self.root, frontend_root=self.frontend)
        self.assertEqual(delete_error.exception.status, 404)
        self.assertEqual(self.db.scalar('SELECT COUNT(*) FROM file_objects WHERE id=?', (row['id'],)), 1)

    def test_download_is_attachment_with_tenant_scoped_path(self):
        row = self.upload(1)
        metadata = self.db.one('SELECT storage_key FROM file_objects WHERE id=?', (row['id'],))
        path = self.root / '1' / metadata['storage_key']
        self.assertTrue(path.is_file())
        self.db.conn.execute("UPDATE file_objects SET scan_status='clean' WHERE id=?", (row['id'],))
        stored, returned_path = self.file_storage.get_download(row['id'], storage_root=self.root,
                                                                frontend_root=self.frontend)
        self.assertEqual(stored['company_id'], 1)
        self.assertEqual(returned_path, path.resolve())

    def test_delete_removes_only_owned_file_and_storage_bytes(self):
        row = self.upload(1)
        metadata = self.db.one('SELECT storage_key FROM file_objects WHERE id=?', (row['id'],))
        path = self.root / '1' / metadata['storage_key']
        self.assertTrue(path.exists())
        self.assertEqual(self.file_storage.delete_file(row['id'], storage_root=self.root,
                                                       frontend_root=self.frontend), {'ok': True})
        self.assertFalse(path.exists())
        self.assertEqual(self.db.scalar('SELECT COUNT(*) FROM file_objects'), 0)

    def test_rejects_active_content_and_mismatched_signatures(self):
        for name, content in [('active.html', b'<script>alert(1)</script>'),
                              ('spoofed.pdf', b'<html>not a pdf</html>'),
                              ('vector.svg', b'<svg></svg>')]:
            with self.subTest(name=name), self.assertRaises(self.ApiError):
                self.upload(filename=name, content=content)
        self.assertEqual(self.db.scalar('SELECT COUNT(*) FROM file_objects'), 0)

    def test_rejects_path_traversal_and_empty_names(self):
        with self.assertRaises(self.ApiError):
            self.upload(filename='../../../../evil.pdf')
        with self.assertRaises(self.ApiError):
            self.upload(filename='..\\..\\evil.pdf')

    def test_rejects_oversized_and_empty_uploads(self):
        with self.assertRaises(self.ApiError) as large:
            self.upload(content=b'%PDF-' + b'x' * self.file_storage.MAX_FILE_BYTES)
        self.assertEqual(large.exception.status, 413)
        with self.assertRaises(self.ApiError):
            self.upload(content=b'')

    def test_storage_root_cannot_be_inside_public_frontend(self):
        with self.assertRaises(self.ApiError) as ctx:
            self.file_storage.upload_bytes(filename='a.pdf', content=b'%PDF-1.7',
                                           storage_root=self.frontend / 'uploads',
                                           frontend_root=self.frontend)
        self.assertEqual(ctx.exception.code, 'unsafe_storage_root')

    def test_legacy_metadata_links_allow_http_and_https_only(self):
        self.assertEqual(self.file_storage.validate_legacy_link('https://example.test/docs/1'),
                         'https://example.test/docs/1')
        self.assertEqual(self.file_storage.validate_legacy_link('http://example.test/file'),
                         'http://example.test/file')
        for link in ('javascript:alert(1)', 'data:text/html,<script>', 'file:///etc/passwd',
                     '//example.test/path', 'https://user:pass@example.test/file', 'https://exa\nmple.test'):
            with self.subTest(link=link), self.assertRaises(self.ApiError):
                self.file_storage.validate_legacy_link(link)

    def test_api_upload_rejects_client_company_id(self):
        class Upload:
            filename = 'x.pdf'
            stream = io.BytesIO(b'%PDF-1.7')
        self.fake_flask.request.form = {'company_id': '2'}
        self.fake_flask.request.files = {'file': Upload()}
        with self.assertRaises(self.ApiError) as ctx:
            self.api.upload_file()
        self.assertEqual(ctx.exception.code, 'tenant_field_forbidden')

    def test_api_upload_and_download_handlers_use_active_company_and_attachment_headers(self):
        class Upload:
            filename = 'evidence.pdf'
            stream = io.BytesIO(b'%PDF-1.7\nhello')
        self.fake_flask.request.form = {'title': 'Evidence'}
        self.fake_flask.request.files = {'file': Upload()}
        result, status = self.api.upload_file()
        self.assertEqual(status, 201)
        file_id = result['file']['id']
        self.assertEqual(self.db.scalar('SELECT company_id FROM file_objects WHERE id=?', (file_id,)), 1)
        self.db.conn.execute("UPDATE file_objects SET scan_status='clean' WHERE id=?", (file_id,))
        response = self.api.download_file(file_id)
        self.assertTrue(response.headers['Content-Disposition'].startswith('attachment; filename='))
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_api_list_is_company_scoped(self):
        own = self.upload(1)
        self.upload(2)
        self.fake_flask.g['company'] = {'id': 1}
        result = self.api.list_files()
        self.assertEqual([x['id'] for x in result['items']], [own['id']])

    def test_integrity_check_detects_missing_and_modified_bytes(self):
        row = self.upload(1)
        storage = self.db.one('SELECT storage_key FROM file_objects WHERE id=?', (row['id'],))
        path = self.root / '1' / storage['storage_key']
        report = self.file_storage.verify_storage_integrity(storage_root=self.root, frontend_root=self.frontend)
        self.assertEqual((report['checked'], report['ok'], report['issues']), (1, 1, []))
        path.write_bytes(b'%PDF-1.7\ncopy')
        report = self.file_storage.verify_storage_integrity(storage_root=self.root, frontend_root=self.frontend)
        self.assertEqual(report['issues'][0]['issue'], 'hash_mismatch')
        path.unlink()
        report = self.file_storage.verify_storage_integrity(storage_root=self.root, frontend_root=self.frontend)
        self.assertEqual(report['issues'][0]['issue'], 'missing')

    def test_integrity_check_only_checks_records_that_exist_in_metadata(self):
        self.upload(1)
        self.upload(2)
        self.fake_flask.g['company'] = {'id': 1}
        report = self.file_storage.verify_storage_integrity(storage_root=self.root, frontend_root=self.frontend)
        self.assertEqual(report['checked'], 2)
        self.assertEqual(report['ok'], 2)


    def test_pending_uploads_cannot_be_downloaded_until_clean_scan(self):
        row = self.upload(1)
        self.assertEqual(row['scan_status'], 'pending')
        with self.assertRaises(self.ApiError) as blocked:
            self.file_storage.get_download(row['id'], storage_root=self.root, frontend_root=self.frontend)
        self.assertEqual(blocked.exception.code, 'file_scan_pending')

    def test_clamav_clean_result_enables_download_and_infected_result_is_quarantined(self):
        scanner = self.root.parent / 'fake-clamscan'
        scanner.write_text(
            '#!/usr/bin/env python3\nimport pathlib,sys\np=pathlib.Path(sys.argv[-1])\nsys.exit(1 if b"INFECTED" in p.read_bytes() else 0)\n',
            encoding='utf-8')
        scanner.chmod(0o700)
        clean = self.upload(1, filename='clean.pdf', content=b'%PDF-1.7\nsafe')
        infected = self.upload(1, filename='bad.pdf', content=b'%PDF-1.7\nINFECTED')
        result = self.file_storage.scan_pending_files(storage_root=self.root, frontend_root=self.frontend,
                                                      scanner_bin=str(scanner))
        self.assertEqual((result['checked'], result['clean'], result['infected'], result['errors']), (2, 1, 1, 0))
        self.assertEqual(self.db.one('SELECT scan_status FROM file_objects WHERE id=?', (clean['id'],))['scan_status'], 'clean')
        self.assertEqual(self.db.one('SELECT scan_status FROM file_objects WHERE id=?', (infected['id'],))['scan_status'], 'infected')
        self.file_storage.get_download(clean['id'], storage_root=self.root, frontend_root=self.frontend)
        with self.assertRaises(self.ApiError) as quarantined:
            self.file_storage.get_download(infected['id'], storage_root=self.root, frontend_root=self.frontend)
        self.assertEqual(quarantined.exception.code, 'file_quarantined')
        report = self.file_storage.verify_storage_integrity(storage_root=self.root, frontend_root=self.frontend)
        self.assertEqual(report['issues'], [])
        meta = self.db.one('SELECT storage_key FROM file_objects WHERE id=?', (infected['id'],))
        self.assertTrue((self.root / '.quarantine' / '1' / meta['storage_key']).is_file())
        self.file_storage.delete_file(infected['id'], storage_root=self.root, frontend_root=self.frontend)
        self.assertFalse((self.root / '.quarantine' / '1' / meta['storage_key']).exists())


    def test_scanner_refuses_to_bless_bytes_that_do_not_match_upload_hash(self):
        row = self.upload(1, filename='tampered.pdf', content=b'%PDF-1.7\noriginal')
        stored = self.db.one('SELECT storage_key FROM file_objects WHERE id=?', (row['id'],))
        path = self.root / '1' / stored['storage_key']
        path.write_bytes(b'%PDF-1.7\nmodified')
        scanner = self.root.parent / 'fake-clamscan-clean'
        scanner.write_text('#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n', encoding='utf-8')
        scanner.chmod(0o700)
        result = self.file_storage.scan_pending_files(storage_root=self.root, frontend_root=self.frontend,
                                                      scanner_bin=str(scanner))
        self.assertEqual(result['clean'], 0)
        self.assertEqual(result['errors'], 1)
        state = self.db.one('SELECT scan_status,scan_engine FROM file_objects WHERE id=?', (row['id'],))
        self.assertEqual(state['scan_status'], 'error')
        self.assertEqual(result['issues'][0]['issue'], 'content_integrity_mismatch')
        with self.assertRaises(self.ApiError):
            self.file_storage.get_download(row['id'], storage_root=self.root, frontend_root=self.frontend)

    def test_scanner_detects_file_modified_during_external_scan(self):
        row = self.upload(1, filename='race.pdf', content=b'%PDF-1.7\noriginal')
        stored = self.db.one('SELECT storage_key FROM file_objects WHERE id=?', (row['id'],))
        path = self.root / '1' / stored['storage_key']
        scanner = self.root.parent / 'fake-clamscan-mutator'
        scanner.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib,sys\n"
            "pathlib.Path(sys.argv[-1]).write_bytes(bytes([37,80,68,70,45,49,46,55,10,109,117,116,97,116,101,100]))\n"
            "sys.exit(0)\n",
            encoding='utf-8')
        scanner.chmod(0o700)
        result = self.file_storage.scan_pending_files(storage_root=self.root, frontend_root=self.frontend,
                                                      scanner_bin=str(scanner))
        self.assertEqual(result['clean'], 0)
        self.assertEqual(result['errors'], 1)
        self.assertEqual(result['issues'][0]['issue'], 'content_changed_during_scan')
        self.assertEqual(self.db.one('SELECT scan_status FROM file_objects WHERE id=?', (row['id'],))['scan_status'], 'error')


    def test_download_rechecks_hash_after_a_file_was_approved_clean(self):
        row = self.upload(1, filename='approved.pdf', content=b'%PDF-1.7\napproved')
        stored = self.db.one('SELECT storage_key FROM file_objects WHERE id=?', (row['id'],))
        path = self.root / '1' / stored['storage_key']
        self.db.conn.execute("UPDATE file_objects SET scan_status='clean' WHERE id=?", (row['id'],))
        path.write_bytes(b'%PDF-1.7\nchanged-after-scan')
        with self.assertRaises(self.ApiError) as blocked:
            self.file_storage.get_download(row['id'], storage_root=self.root, frontend_root=self.frontend)
        self.assertEqual(blocked.exception.code, 'file_integrity_failed')
        self.assertEqual(self.db.one('SELECT scan_status FROM file_objects WHERE id=?', (row['id'],))['scan_status'], 'error')

    def test_company_storage_quota_is_enforced_atomically(self):
        self.file_storage.upload_bytes(filename='a.pdf', content=b'%PDF-1.7-123', title='A',
            company_id=1, uploaded_by=1, storage_root=self.root, frontend_root=self.frontend,
            company_quota_bytes=15, uploads_per_hour=10)
        with self.assertRaises(self.ApiError) as blocked:
            self.file_storage.upload_bytes(filename='b.pdf', content=b'%PDF-1.7-more-bytes', title='B',
                company_id=1, uploaded_by=1, storage_root=self.root, frontend_root=self.frontend,
                company_quota_bytes=15, uploads_per_hour=10)
        self.assertEqual(blocked.exception.code, 'file_quota_exceeded')
        self.assertEqual(self.file_storage.storage_usage(company_id=1, quota_bytes=15)['file_count'], 1)
        self.assertEqual(self.db.scalar('SELECT COUNT(*) FROM file_objects WHERE company_id=1'), 1)

    def test_per_user_hourly_upload_throttle_is_enforced(self):
        self.file_storage.upload_bytes(filename='a.pdf', content=b'%PDF-1.7-a', title='A',
            company_id=1, uploaded_by=1, storage_root=self.root, frontend_root=self.frontend,
            company_quota_bytes=1000, uploads_per_hour=1)
        with self.assertRaises(self.ApiError) as blocked:
            self.file_storage.upload_bytes(filename='b.pdf', content=b'%PDF-1.7-b', title='B',
                company_id=1, uploaded_by=1, storage_root=self.root, frontend_root=self.frontend,
                company_quota_bytes=1000, uploads_per_hour=1)
        self.assertEqual(blocked.exception.status, 429)

    def test_storage_root_symlink_is_rejected(self):
        target = Path(self.temp.name) / 'real-private-files'
        target.mkdir()
        link = Path(self.temp.name) / 'linked-private-files'
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(self.ApiError) as blocked:
            self.file_storage.upload_bytes(
                filename='link.pdf', content=b'%PDF-1.7\nbody', title='Link',
                company_id=1, uploaded_by=1, storage_root=link, frontend_root=self.frontend,
            )
        self.assertEqual(blocked.exception.code, 'unsafe_storage_root')
        self.assertEqual(list(target.iterdir()), [])

    def test_tenant_directory_symlink_is_rejected(self):
        self.root.mkdir(mode=0o700)
        target = Path(self.temp.name) / 'other-company-files'
        target.mkdir()
        (self.root / '1').symlink_to(target, target_is_directory=True)
        with self.assertRaises(self.ApiError) as blocked:
            self.upload(1)
        self.assertEqual(blocked.exception.code, 'unsafe_storage_path')
        self.assertEqual(list(target.iterdir()), [])

    def test_infected_scan_fails_closed_when_quarantine_path_is_symlink(self):
        row = self.upload(1, filename='bad-link.pdf', content=b'%PDF-1.7\nINFECTED')
        quarantine_target = Path(self.temp.name) / 'quarantine-target'
        quarantine_target.mkdir()
        (self.root / '.quarantine').symlink_to(quarantine_target, target_is_directory=True)
        scanner = Path(self.temp.name) / 'fake-clamscan-infected'
        scanner.write_text('#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n', encoding='utf-8')
        scanner.chmod(0o700)
        result = self.file_storage.scan_pending_files(
            storage_root=self.root, frontend_root=self.frontend, scanner_bin=str(scanner),
        )
        self.assertEqual((result['infected'], result['errors']), (0, 1))
        self.assertEqual(result['issues'][0]['issue'], 'quarantine_move_failed_or_unsafe')
        state = self.db.one('SELECT scan_status FROM file_objects WHERE id=?', (row['id'],))
        self.assertEqual(state['scan_status'], 'error')
        self.assertEqual(list(quarantine_target.iterdir()), [])

    def test_orphan_audit_reports_without_deleting_unknown_files(self):
        self.upload(1)
        stray_dir = self.root / '1'
        stray = stray_dir / 'orphan.pdf'
        stray.write_bytes(b'%PDF-1.7-orphan')
        report = self.file_storage.audit_orphan_files(storage_root=self.root, frontend_root=self.frontend)
        self.assertEqual(report['orphan_count'], 1)
        self.assertEqual(report['deleted'], 0)
        self.assertTrue(stray.exists())

    def test_usage_endpoint_returns_only_active_company_quota(self):
        self.upload(1)
        self.upload(2)
        self.fake_flask.g['company'] = {'id': 1}
        result = self.api.file_usage()
        self.assertEqual(result['usage']['file_count'], 1)
        self.assertEqual(result['usage']['quota_bytes'], 250 * 1024 * 1024)



if __name__ == '__main__':
    unittest.main()
