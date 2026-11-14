"""Stage 13 tests for CRM, maintenance and project-summary tenant isolation.

Loads the real API module with a tiny Flask-compatible harness because Flask is
not installed in the offline validation environment. SQL executes against real
SQLite tables and rows for two companies; decorators and request globals are
only lightweight substitutes.
"""
from contextlib import contextmanager
import importlib.util
import sqlite3
import sys
import types
import unittest
from pathlib import Path

ERP_ROOT = Path(__file__).parents[1] / 'erp'


class FakeG(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


class FakeBlueprint:
    def __init__(self, *args, **kwargs):
        self.name = args[0] if args else 'test'

    def _decorator(self, *args, **kwargs):
        return lambda fn: fn

    get = _decorator
    post = _decorator
    put = _decorator
    delete = _decorator


class FakeRequest:
    data = b''


class Stage13OpsScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = {}
        prefix = 'erp_stage13_test'
        names = [
            'flask', prefix, f'{prefix}.db', f'{prefix}.audit', f'{prefix}.crud',
            f'{prefix}.errors', f'{prefix}.security', f'{prefix}.services',
            f'{prefix}.services.tenancy', f'{prefix}.services.manufacturing',
            f'{prefix}.util', f'{prefix}.api', f'{prefix}.api.common',
        ]
        for name in names:
            cls.saved[name] = sys.modules.get(name)

        flask = types.ModuleType('flask')
        flask.g = FakeG(company={'id': 1})
        flask.request = FakeRequest()
        flask.Blueprint = FakeBlueprint
        flask.jsonify = lambda *args, **kwargs: args[0] if args else kwargs
        flask.has_request_context = lambda: True
        sys.modules['flask'] = flask
        cls.flask = flask

        package = types.ModuleType(prefix)
        package.__path__ = [str(ERP_ROOT)]
        sys.modules[prefix] = package

        db = types.ModuleType(f'{prefix}.db')
        db.conn = None
        db.q = lambda sql, params=(): [dict(r) for r in db.conn.execute(sql, params).fetchall()]
        db.one = lambda sql, params=(): (dict(r) if (r := db.conn.execute(sql, params).fetchone()) else None)
        db.scalar = lambda sql, params=(): (r[0] if (r := db.conn.execute(sql, params).fetchone()) else None)
        db.execute = lambda sql, params=(): db.conn.execute(sql, params).lastrowid

        @contextmanager
        def transaction():
            db.conn.execute('BEGIN')
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

        audit = types.ModuleType(f'{prefix}.audit')
        audit.calls = []
        audit.log = lambda *args, **kwargs: audit.calls.append((args, kwargs))
        sys.modules[audit.__name__] = audit
        package.audit = audit
        cls.audit = audit

        crud = types.ModuleType(f'{prefix}.crud')
        crud.serialize = lambda obj: obj
        sys.modules[crud.__name__] = crud

        errors = types.ModuleType(f'{prefix}.errors')
        class ApiError(Exception):
            def __init__(self, code, message=None, status=400, params=None, details=None):
                super().__init__(message or code)
                self.code, self.status = code, status
                self.params, self.details = params or {}, details
        errors.ApiError = ApiError
        sys.modules[errors.__name__] = errors
        cls.ApiError = ApiError

        security = types.ModuleType(f'{prefix}.security')
        security.require = lambda *perms: (lambda fn: fn)
        sys.modules[security.__name__] = security

        services = types.ModuleType(f'{prefix}.services')
        services.__path__ = []
        sys.modules[services.__name__] = services
        package.services = services
        tenancy = types.ModuleType(f'{prefix}.services.tenancy')
        tenancy.current_company_id = lambda: int(flask.g['company']['id'])
        sys.modules[tenancy.__name__] = tenancy
        services.tenancy = tenancy
        manufacturing = types.ModuleType(f'{prefix}.services.manufacturing')
        sys.modules[manufacturing.__name__] = manufacturing
        services.manufacturing = manufacturing

        util = types.ModuleType(f'{prefix}.util')
        util.parse_date = lambda value, *a, **kw: value
        util.today = lambda: '2026-11-13'
        sys.modules[util.__name__] = util

        api = types.ModuleType(f'{prefix}.api')
        api.__path__ = [str(ERP_ROOT / 'api')]
        sys.modules[api.__name__] = api
        common = types.ModuleType(f'{prefix}.api.common')
        common.body = lambda: {'date': '2026-11-12'}
        common.paging = lambda: (1, 25, 0)
        sys.modules[common.__name__] = common

        spec = importlib.util.spec_from_file_location(f'{prefix}.api.ops', ERP_ROOT / 'api' / 'ops.py')
        ops = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = ops
        spec.loader.exec_module(ops)
        cls.ops = ops
        cls.prefix = prefix

    @classmethod
    def tearDownClass(cls):
        names = [
            'flask', cls.prefix, f'{cls.prefix}.db', f'{cls.prefix}.audit', f'{cls.prefix}.crud',
            f'{cls.prefix}.errors', f'{cls.prefix}.security', f'{cls.prefix}.services',
            f'{cls.prefix}.services.tenancy', f'{cls.prefix}.services.manufacturing',
            f'{cls.prefix}.util', f'{cls.prefix}.api', f'{cls.prefix}.api.common',
            f'{cls.prefix}.api.ops',
        ]
        for name in names:
            old = cls.saved.get(name)
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
            CREATE TABLE leads(
              id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT NOT NULL,
              phone TEXT, email TEXT, notes TEXT, status TEXT NOT NULL, partner_id INTEGER, updated_at TEXT
            );
            CREATE TABLE partners(
              id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL,
              name TEXT NOT NULL, is_customer INTEGER NOT NULL DEFAULT 0,
              phone TEXT, email TEXT, notes TEXT
            );
            CREATE TABLE opportunities(
              id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, stage TEXT NOT NULL,
              value_cents INTEGER NOT NULL, probability INTEGER NOT NULL
            );
            CREATE TABLE maintenance_schedules(
              id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, last_done TEXT, updated_at TEXT
            );
            CREATE TABLE projects(
              id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT NOT NULL,
              budget_cents INTEGER NOT NULL
            );
            CREATE TABLE employees(
              id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT NOT NULL,
              salary_cents INTEGER NOT NULL
            );
            CREATE TABLE time_logs(
              id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, employee_id INTEGER NOT NULL,
              project_id INTEGER NOT NULL, hours REAL NOT NULL
            );
            CREATE TABLE tasks(
              id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, project_id INTEGER NOT NULL,
              status TEXT NOT NULL
            );
            INSERT INTO leads VALUES
              (11,1,'Alpha Lead','111','alpha@example.test','A notes','new',NULL,NULL),
              (22,2,'Beta Lead','222','beta@example.test','B notes','new',NULL,NULL);
            INSERT INTO opportunities VALUES
              (101,1,'proposal',10000,50), (201,2,'won',99000,100), (202,2,'won',1000,100);
            INSERT INTO maintenance_schedules VALUES
              (301,1,NULL,NULL), (302,2,NULL,NULL);
            INSERT INTO projects VALUES
              (401,1,'Alpha Project',50000), (402,2,'Beta Project',90000);
            INSERT INTO employees VALUES
              (501,1,'Alpha Employee',17600), (502,2,'Beta Employee',35200);
            INSERT INTO time_logs VALUES
              (601,1,501,401,4.0),
              (602,2,502,401,80.0),
              (603,2,502,402,20.0);
            INSERT INTO tasks VALUES
              (701,1,401,'todo'), (702,1,401,'done'), (703,2,401,'done'), (704,2,402,'doing');
        ''')
        self.db.conn = self.conn
        self.flask.g['company'] = {'id': 1}
        self.flask.request.data = b''
        self.audit.calls.clear()

    def tearDown(self):
        self.conn.close()

    def test_foreign_lead_cannot_be_converted(self):
        with self.assertRaises(self.ApiError) as ctx:
            self.ops.convert_lead(22)
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(self.db.scalar('SELECT COUNT(*) FROM partners'), 0)
        self.assertEqual(self.db.scalar('SELECT status FROM leads WHERE id=22'), 'new')

    def test_lead_conversion_stamps_company_and_updates_only_owned_lead(self):
        result = self.ops.convert_lead(11)
        self.assertIn('customer_id', result)
        partner = self.db.one('SELECT * FROM partners WHERE id=?', (result['customer_id'],))
        self.assertEqual(partner['company_id'], 1)
        self.assertEqual(partner['name'], 'Alpha Lead')
        self.assertEqual(self.db.scalar('SELECT status FROM leads WHERE id=11'), 'converted')
        self.assertEqual(self.db.scalar('SELECT status FROM leads WHERE id=22'), 'new')

    def test_pipeline_aggregates_only_active_company(self):
        result = self.ops.pipeline()
        self.assertEqual(result['items'], [{
            'stage': 'proposal', 'count': 1, 'value_cents': 10000, 'weighted_cents': 5000
        }])

    def test_foreign_maintenance_schedule_cannot_be_marked_done(self):
        with self.assertRaises(self.ApiError) as ctx:
            self.ops.schedule_done(302)
        self.assertEqual(ctx.exception.status, 404)
        self.assertIsNone(self.db.scalar('SELECT last_done FROM maintenance_schedules WHERE id=302'))

    def test_owned_maintenance_schedule_can_be_marked_done(self):
        result = self.ops.schedule_done(301)
        self.assertEqual(result, {'ok': True})
        self.assertEqual(self.db.scalar('SELECT last_done FROM maintenance_schedules WHERE id=301'), '2026-11-13')

    def test_foreign_project_summary_is_hidden(self):
        with self.assertRaises(self.ApiError) as ctx:
            self.ops.project_summary(402)
        self.assertEqual(ctx.exception.status, 404)

    def test_project_summary_excludes_foreign_time_logs_and_tasks(self):
        result = self.ops.project_summary(401)
        self.assertEqual(result['project']['name'], 'Alpha Project')
        self.assertEqual(result['hours'], 4.0)
        self.assertEqual(result['by_employee'], [{
            'employee_id': 501, 'name': 'Alpha Employee', 'hours': 4.0, 'cost_cents': 400
        }])
        self.assertEqual(result['tasks'], [{'status': 'done', 'count': 1}, {'status': 'todo', 'count': 1}])
        self.assertEqual(result['actual_cost_cents'], 400)
        self.assertEqual(result['budget_remaining_cents'], 49600)

    def test_ops_api_queries_include_company_scope(self):
        source = (ERP_ROOT / 'api' / 'ops.py').read_text(encoding='utf-8')
        self.assertIn('WHERE id=? AND company_id=?', source)
        self.assertIn('FROM opportunities WHERE company_id=? GROUP BY stage', source)
        self.assertIn('WHERE tl.project_id=? AND tl.company_id=?', source)
        self.assertIn('WHERE project_id=? AND company_id=? GROUP BY status', source)


if __name__ == '__main__':
    unittest.main()
