"""Stage 10: tenant-scoped sequence key and business uniqueness migration."""
import importlib.util
import json
import sqlite3
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCHEMA = ROOT / 'erp' / 'schema.sql'
MIGRATIONS_PATH = ROOT / 'erp' / 'migrations' / '__init__.py'
SPEC = importlib.util.spec_from_file_location('erp_migrations_stage10', MIGRATIONS_PATH)
MIGRATIONS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATIONS)


class Stage10UniquenessMigrationTests(unittest.TestCase):
    def make_connection(self):
        conn = sqlite3.connect(':memory:', isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript(SCHEMA.read_text(encoding='utf-8'))
        conn.execute('INSERT INTO schema_version(version) VALUES(1)')
        conn.execute("INSERT INTO roles(name,label,permissions,is_system) VALUES('admin','Admin','[\"*\"]',1)")
        conn.execute("INSERT INTO users(email,name,password_hash,role) VALUES('owner@test.invalid','Owner','x','admin')")
        conn.execute("INSERT INTO settings(key,value) VALUES('company',?)", (json.dumps('Legacy Co'),))
        MIGRATIONS.apply_migrations(conn, target_version=5)
        return conn

    def add_second_company(self, conn):
        return conn.execute("INSERT INTO companies(name) VALUES('Company B')").lastrowid

    def drop_tenant_write_gates_for_schema_tests(self, conn):
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_tenant_%_block_multi_%'"
        ).fetchall()]
        for name in names:
            conn.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')

    def test_v6_scopes_sequences_and_all_reviewed_unique_keys(self):
        conn = self.make_connection()
        try:
            company_a = conn.execute('SELECT id FROM companies').fetchone()[0]
            # Seed representative legacy rows before rebuild to verify that their
            # IDs and business data survive the table swaps.
            conn.execute("INSERT INTO categories(name,company_id) VALUES('Legacy category',?)", (company_a,))
            category_id = conn.execute("SELECT id FROM categories WHERE name='Legacy category'").fetchone()[0]
            conn.execute("INSERT INTO warehouses(code,name,company_id) VALUES('LEG','Legacy warehouse',?)", (company_a,))
            warehouse_id = conn.execute("SELECT id FROM warehouses WHERE code='LEG'").fetchone()[0]
            conn.execute("INSERT INTO products(sku,barcode,name,category_id,company_id) VALUES('LEG-SKU','LEG-BC','Legacy product',?,?)",
                         (category_id, company_a))
            product_id = conn.execute("SELECT id FROM products WHERE sku='LEG-SKU'").fetchone()[0]
            conn.execute("INSERT INTO stock_levels(product_id,warehouse_id,qty,value_cents,company_id) VALUES(?,?,?,?,?)",
                         (product_id, warehouse_id, 3, 300, company_a))
            conn.execute("INSERT INTO accounts(code,name,type,company_id) VALUES('LEG-100','Legacy cash','asset',?)", (company_a,))
            conn.execute("INSERT INTO cost_centers(name,company_id) VALUES('Legacy CC',?)", (company_a,))
            conn.execute("INSERT INTO departments(name,company_id) VALUES('Legacy Dept',?)", (company_a,))
            department_id = conn.execute("SELECT id FROM departments WHERE name='Legacy Dept'").fetchone()[0]
            conn.execute("INSERT INTO employees(name,department_id,company_id) VALUES('Legacy employee',?,?)",
                         (department_id, company_a))
            employee_id = conn.execute("SELECT id FROM employees WHERE name='Legacy employee'").fetchone()[0]
            conn.execute("INSERT INTO attendance(employee_id,date,status,company_id) VALUES(?,?,?,?)",
                         (employee_id, '2026-11-13', 'present', company_a))
            conn.execute("INSERT INTO journal_entries(number,date,company_id) VALUES('LEG-JE','2026-11-13',?)", (company_a,))
            journal_id = conn.execute("SELECT id FROM journal_entries WHERE number='LEG-JE'").fetchone()[0]
            conn.execute("INSERT INTO fixed_assets(name,purchase_date,cost_cents,useful_life_months,company_id) VALUES('Legacy asset','2026-01-01',1000,12,?)",
                         (company_a,))
            asset_id = conn.execute("SELECT id FROM fixed_assets WHERE name='Legacy asset'").fetchone()[0]
            conn.execute("INSERT INTO depreciation_postings(asset_id,period,date,amount_cents,entry_id,company_id) VALUES(?,?,?,?,?,?)",
                         (asset_id, '2026-11', '2026-11-13', 100, journal_id, company_a))
            conn.execute("INSERT INTO payroll_runs(month,date,status,company_id) VALUES('2026-11','2026-11-30','posted',?)",
                         (company_a,))
            conn.execute("INSERT INTO partners(name,company_id) VALUES('Legacy partner',?)", (company_a,))
            partner_id = conn.execute("SELECT id FROM partners WHERE name='Legacy partner'").fetchone()[0]
            conn.execute("INSERT INTO trade_documents(doc_type,number,partner_id,date,company_id) VALUES('invoice','LEG-INV',?,'2026-11-13',?)",
                         (partner_id, company_a))
            conn.execute(
                'INSERT INTO sequences(name,prefix,next_number,padding,company_id) VALUES(?,?,?,?,?)',
                ('invoice', 'LEG-', 42, 5, company_a),
            )
            company_b = self.add_second_company(conn)
            self.assertEqual(MIGRATIONS.apply_migrations(conn, target_version=6), 6)
            self.assertEqual(conn.execute("SELECT qty FROM stock_levels WHERE company_id=? AND product_id=? AND warehouse_id=?",
                                          (company_a, product_id, warehouse_id)).fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT number FROM journal_entries WHERE id=?", (journal_id,)).fetchone()[0], 'LEG-JE')
            self.assertEqual(conn.execute("SELECT period FROM depreciation_postings WHERE asset_id=?", (asset_id,)).fetchone()[0], '2026-11')
            self.assertEqual(conn.execute("SELECT number FROM trade_documents WHERE number='LEG-INV'").fetchone()[0], 'LEG-INV')
            self.assertEqual(conn.execute("SELECT status FROM attendance WHERE employee_id=?", (employee_id,)).fetchone()[0], 'present')

            sequence_pk = [r['name'] for r in sorted(
                (r for r in conn.execute('PRAGMA table_info(sequences)').fetchall() if r['pk']),
                key=lambda r: r['pk'],
            )]
            self.assertEqual(sequence_pk, ['company_id', 'name'])
            sequence = conn.execute(
                "SELECT company_id,name,prefix,next_number,padding FROM sequences WHERE company_id=? AND name='invoice'",
                (company_a,),
            ).fetchone()
            self.assertEqual(tuple(sequence), (company_a, 'invoice', 'LEG-', 42, 5))

            settings_pk = [r['name'] for r in sorted(
                (r for r in conn.execute('PRAGMA table_info(settings)').fetchall() if r['pk']),
                key=lambda r: r['pk'],
            )]
            self.assertEqual(settings_pk, ['company_id', 'key'])

            expected = {
                'accounts': 'uq_tenant_accounts_code',
                'attendance': 'uq_tenant_attendance_employee_date',
                'categories': 'uq_tenant_categories_name',
                'cost_centers': 'uq_tenant_cost_centers_name',
                'departments': 'uq_tenant_departments_name',
                'depreciation_postings': 'uq_tenant_depreciation_asset_period',
                'journal_entries': 'uq_tenant_journal_entries_number',
                'payroll_runs': 'uq_tenant_payroll_runs_posted_month',
                'products': 'uq_tenant_products_sku',
                'trade_documents': 'uq_tenant_trade_documents_type_number',
                'warehouses': 'uq_tenant_warehouses_code',
            }
            for table, index_name in expected.items():
                with self.subTest(table=table):
                    indexes = {r['name']: r for r in conn.execute(f'PRAGMA index_list("{table}")')}
                    self.assertIn(index_name, indexes)
                    self.assertEqual(indexes[index_name]['unique'], 1)
            product_indexes = {r['name'] for r in conn.execute('PRAGMA index_list(products)')}
            self.assertIn('uq_tenant_products_barcode', product_indexes)
            self.assertNotIn('idx_products_barcode', product_indexes)
            self.assertNotIn('idx_td_number', {r['name'] for r in conn.execute('PRAGMA index_list(trade_documents)')})
            self.assertNotIn('idx_payroll_month', {r['name'] for r in conn.execute('PRAGMA index_list(payroll_runs)')})

            stock_pk = [r['name'] for r in sorted(
                (r for r in conn.execute('PRAGMA table_info(stock_levels)').fetchall() if r['pk']),
                key=lambda r: r['pk'],
            )]
            self.assertEqual(stock_pk, ['company_id', 'product_id', 'warehouse_id'])
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_tenant_rel_%'").fetchone()[0],
                110,
            )
            self.assertEqual(MIGRATIONS.apply_migrations(conn, target_version=6), 6)
            self.assertEqual(conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], 6)
        finally:
            conn.close()

    def test_same_business_keys_can_repeat_across_companies(self):
        conn = self.make_connection()
        try:
            company_a = conn.execute('SELECT id FROM companies').fetchone()[0]
            company_b = self.add_second_company(conn)
            self.assertEqual(MIGRATIONS.apply_migrations(conn, target_version=6), 6)
            self.drop_tenant_write_gates_for_schema_tests(conn)

            # Keys that were globally unique now have the same value in both tenants.
            conn.execute("INSERT INTO accounts(code,name,type,company_id) VALUES('1000','A Cash','asset',?)", (company_a,))
            conn.execute("INSERT INTO accounts(code,name,type,company_id) VALUES('1000','B Cash','asset',?)", (company_b,))
            conn.execute("INSERT INTO categories(name,company_id) VALUES('General',?)", (company_a,))
            conn.execute("INSERT INTO categories(name,company_id) VALUES('General',?)", (company_b,))
            conn.execute("INSERT INTO cost_centers(name,company_id) VALUES('Operations',?)", (company_a,))
            conn.execute("INSERT INTO cost_centers(name,company_id) VALUES('Operations',?)", (company_b,))
            conn.execute("INSERT INTO departments(name,company_id) VALUES('Sales',?)", (company_a,))
            conn.execute("INSERT INTO departments(name,company_id) VALUES('Sales',?)", (company_b,))
            conn.execute("INSERT INTO products(sku,barcode,name,company_id) VALUES('SKU-1','BC-1','A Product',?)", (company_a,))
            conn.execute("INSERT INTO products(sku,barcode,name,company_id) VALUES('SKU-1','BC-1','B Product',?)", (company_b,))
            conn.execute("INSERT INTO warehouses(code,name,company_id) VALUES('MAIN','A Main',?)", (company_a,))
            conn.execute("INSERT INTO warehouses(code,name,company_id) VALUES('MAIN','B Main',?)", (company_b,))
            conn.execute("INSERT INTO journal_entries(number,date,company_id) VALUES('JE-1','2026-11-13',?)", (company_a,))
            conn.execute("INSERT INTO journal_entries(number,date,company_id) VALUES('JE-1','2026-11-13',?)", (company_b,))
            conn.execute("INSERT INTO payroll_runs(month,date,status,company_id) VALUES('2026-11','2026-11-30','posted',?)", (company_a,))
            conn.execute("INSERT INTO payroll_runs(month,date,status,company_id) VALUES('2026-11','2026-11-30','posted',?)", (company_b,))
            conn.execute("INSERT INTO sequences(company_id,name,prefix,next_number,padding) VALUES(?,?,?,?,?)",
                         (company_a, 'invoice', 'A-', 1, 4))
            conn.execute("INSERT INTO sequences(company_id,name,prefix,next_number,padding) VALUES(?,?,?,?,?)",
                         (company_b, 'invoice', 'B-', 1, 4))
            conn.execute("INSERT INTO settings(company_id,key,value) VALUES(?,?,?)", (company_a, 'locale', 'ar'))
            conn.execute("INSERT INTO settings(company_id,key,value) VALUES(?,?,?)", (company_b, 'locale', 'en'))
            partners = []
            for cid, suffix in ((company_a, 'A'), (company_b, 'B')):
                partners.append(conn.execute(
                    'INSERT INTO partners(name,company_id) VALUES(?,?)', (f'Partner {suffix}', cid)
                ).lastrowid)
            conn.execute("INSERT INTO trade_documents(doc_type,number,partner_id,date,company_id) VALUES('invoice','INV-1',?,'2026-11-13',?)",
                         (partners[0], company_a))
            conn.execute("INSERT INTO trade_documents(doc_type,number,partner_id,date,company_id) VALUES('invoice','INV-1',?,'2026-11-13',?)",
                         (partners[1], company_b))

            self.assertEqual(conn.execute("SELECT COUNT(*) FROM accounts WHERE code='1000'").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM products WHERE sku='SKU-1'").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM products WHERE barcode='BC-1'").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trade_documents WHERE number='INV-1'").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sequences WHERE name='invoice'").fetchone()[0], 2)
        finally:
            conn.close()

    def test_duplicate_candidate_keys_within_same_company_are_rejected(self):
        conn = self.make_connection()
        try:
            company_a = conn.execute('SELECT id FROM companies').fetchone()[0]
            self.assertEqual(MIGRATIONS.apply_migrations(conn, target_version=6), 6)
            self.drop_tenant_write_gates_for_schema_tests(conn)
            conn.execute("INSERT INTO categories(name,company_id) VALUES('General',?)", (company_a,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO categories(name,company_id) VALUES('General',?)", (company_a,))
            conn.execute("INSERT INTO accounts(code,name,type,company_id) VALUES('1000','Cash','asset',?)", (company_a,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO accounts(code,name,type,company_id) VALUES('1000','Duplicate','asset',?)", (company_a,))
            conn.execute("INSERT INTO sequences(company_id,name,prefix) VALUES(?, 'invoice','INV-')", (company_a,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO sequences(company_id,name,prefix) VALUES(?, 'invoice','INV2-')", (company_a,))
            conn.execute("INSERT INTO journal_entries(number,date,company_id) VALUES('JE-1','2026-11-13',?)", (company_a,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO journal_entries(number,date,company_id) VALUES('JE-1','2026-11-13',?)", (company_a,))
            conn.execute("INSERT INTO payroll_runs(month,date,status,company_id) VALUES('2026-11','2026-11-30','posted',?)", (company_a,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO payroll_runs(month,date,status,company_id) VALUES('2026-11','2026-11-30','posted',?)", (company_a,))
        finally:
            conn.close()

    def test_sequence_service_generates_independent_numbers_per_company(self):
        conn = self.make_connection()
        saved = {key: sys.modules.get(key) for key in (
            'stage10_erp', 'stage10_erp.db', 'stage10_erp.errors',
            'stage10_erp.services', 'stage10_erp.services.tenancy',
        )}
        try:
            company_a = conn.execute('SELECT id FROM companies').fetchone()[0]
            conn.execute("INSERT INTO sequences(name,prefix,next_number,padding,company_id) VALUES('invoice','A-INV-',5,6,?)", (company_a,))
            company_b = self.add_second_company(conn)
            MIGRATIONS.apply_migrations(conn, target_version=6)
            self.drop_tenant_write_gates_for_schema_tests(conn)

            package = types.ModuleType('stage10_erp')
            package.__path__ = []
            db_module = types.ModuleType('stage10_erp.db')
            def one(sql, params=()):
                row = conn.execute(sql, params).fetchone()
                return dict(row) if row else None
            def execute(sql, params=()):
                return conn.execute(sql, params).lastrowid
            db_module.one = one
            db_module.execute = execute
            errors_module = types.ModuleType('stage10_erp.errors')
            class ApiError(Exception):
                pass
            errors_module.ApiError = ApiError
            services_package = types.ModuleType('stage10_erp.services')
            services_package.__path__ = []
            tenancy_module = types.ModuleType('stage10_erp.services.tenancy')
            state = {'company_id': company_a}
            tenancy_module.current_company_id = lambda explicit=None: state['company_id']
            package.db = db_module
            services_package.tenancy = tenancy_module
            for key, module in (
                ('stage10_erp', package), ('stage10_erp.db', db_module),
                ('stage10_erp.errors', errors_module), ('stage10_erp.services', services_package),
                ('stage10_erp.services.tenancy', tenancy_module),
            ):
                sys.modules[key] = module
            spec = importlib.util.spec_from_file_location(
                'stage10_erp.services.sequences', ROOT / 'erp' / 'services' / 'sequences.py'
            )
            service = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = service
            spec.loader.exec_module(service)

            self.assertEqual(service.next_number('invoice'), 'A-INV-000005')
            state['company_id'] = company_b
            self.assertEqual(service.next_number('invoice'), 'INV-000001')
            state['company_id'] = company_a
            self.assertEqual(service.next_number('invoice'), 'A-INV-000006')
            rows = {r['company_id']: r['next_number'] for r in conn.execute(
                "SELECT company_id,next_number FROM sequences WHERE name='invoice'"
            ).fetchall()}
            self.assertEqual(rows, {company_a: 7, company_b: 2})
        finally:
            conn.close()
            for key, module in saved.items():
                if module is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = module
            sys.modules.pop('stage10_erp.services.sequences', None)


if __name__ == '__main__':
    unittest.main()
