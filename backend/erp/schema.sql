-- ERP Global Pro - database schema (SQLite). Money is stored as integer minor units (*_cents).
-- Portable to PostgreSQL with minor syntax changes (see README roadmap).

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS roles (
  name TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  permissions TEXT NOT NULL,           -- JSON list, e.g. ["sales.*","reports.read"]
  is_system INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL REFERENCES roles(name),
  is_active INTEGER NOT NULL DEFAULT 1,
  last_login TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  user_id INTEGER, user_email TEXT,
  action TEXT NOT NULL, entity TEXT, entity_id TEXT, details TEXT, ip TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity, entity_id);

CREATE TABLE IF NOT EXISTS sequences (
  name TEXT PRIMARY KEY, prefix TEXT NOT NULL,
  next_number INTEGER NOT NULL DEFAULT 1, padding INTEGER NOT NULL DEFAULT 6
);

-- ---------------------------------------------------------------- master data
CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS warehouses (
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS partners (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  is_customer INTEGER NOT NULL DEFAULT 0,
  is_supplier INTEGER NOT NULL DEFAULT 0,
  phone TEXT, email TEXT, address TEXT, city TEXT, country TEXT, tax_number TEXT,
  credit_limit_cents INTEGER NOT NULL DEFAULT 0 CHECK (credit_limit_cents >= 0),
  payment_terms_days INTEGER NOT NULL DEFAULT 0 CHECK (payment_terms_days >= 0),
  notes TEXT,
  is_system INTEGER NOT NULL DEFAULT 0,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_partners_name ON partners(name);

CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sku TEXT NOT NULL UNIQUE,
  barcode TEXT,
  name TEXT NOT NULL,
  category_id INTEGER REFERENCES categories(id),
  kind TEXT NOT NULL DEFAULT 'storable' CHECK (kind IN ('storable','raw','spare','service')),
  unit TEXT NOT NULL DEFAULT 'pcs',
  price_cents INTEGER NOT NULL DEFAULT 0 CHECK (price_cents >= 0),
  cost_cents INTEGER NOT NULL DEFAULT 0 CHECK (cost_cents >= 0),
  tax_rate REAL,
  min_stock REAL NOT NULL DEFAULT 0,
  notes TEXT,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode) WHERE barcode IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);

-- ---------------------------------------------------------------- inventory
CREATE TABLE IF NOT EXISTS stock_moves (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES products(id),
  warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
  qty REAL NOT NULL,                    -- signed: + in, - out
  value_cents INTEGER NOT NULL,         -- signed, same sign as qty
  move_type TEXT NOT NULL,              -- purchase|sale|return_in|return_out|adjust|transfer_in|transfer_out|production_in|production_out|opening|reversal
  ref_type TEXT, ref_id INTEGER,
  date TEXT NOT NULL, note TEXT,
  lot_no TEXT, expiry_date TEXT,
  user_id INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_moves_pw ON stock_moves(product_id, warehouse_id);
CREATE INDEX IF NOT EXISTS idx_moves_ref ON stock_moves(ref_type, ref_id);

CREATE TABLE IF NOT EXISTS stock_levels (
  product_id INTEGER NOT NULL REFERENCES products(id),
  warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
  qty REAL NOT NULL DEFAULT 0,
  value_cents INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (product_id, warehouse_id)
);

CREATE TABLE IF NOT EXISTS stock_counts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT,
  warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
  date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','posted')),
  note TEXT, created_by INTEGER, created_at TEXT NOT NULL DEFAULT (datetime('now')), posted_at TEXT
);
CREATE TABLE IF NOT EXISTS stock_count_lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  count_id INTEGER NOT NULL REFERENCES stock_counts(id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products(id),
  system_qty REAL NOT NULL DEFAULT 0, counted_qty REAL
);

-- ---------------------------------------------------------------- accounting
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('asset','liability','equity','revenue','expense')),
  subtype TEXT,
  parent_id INTEGER REFERENCES accounts(id),
  is_system INTEGER NOT NULL DEFAULT 0,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cost_centers (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
  is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS journal_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  number TEXT NOT NULL UNIQUE,
  date TEXT NOT NULL, memo TEXT,
  ref_type TEXT, ref_id INTEGER,
  reversal_of INTEGER REFERENCES journal_entries(id),
  status TEXT NOT NULL DEFAULT 'posted' CHECK (status IN ('posted','reversed')),
  created_by INTEGER, created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_je_date ON journal_entries(date);
CREATE INDEX IF NOT EXISTS idx_je_ref ON journal_entries(ref_type, ref_id);

CREATE TABLE IF NOT EXISTS journal_lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id INTEGER NOT NULL REFERENCES journal_entries(id),
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  debit_cents INTEGER NOT NULL DEFAULT 0 CHECK (debit_cents >= 0),
  credit_cents INTEGER NOT NULL DEFAULT 0 CHECK (credit_cents >= 0),
  partner_id INTEGER REFERENCES partners(id),
  cost_center_id INTEGER REFERENCES cost_centers(id),
  description TEXT,
  CHECK (debit_cents = 0 OR credit_cents = 0)
);
CREATE INDEX IF NOT EXISTS idx_jl_entry ON journal_lines(entry_id);
CREATE INDEX IF NOT EXISTS idx_jl_account ON journal_lines(account_id);
CREATE INDEX IF NOT EXISTS idx_jl_partner ON journal_lines(partner_id);

-- The ledger is append-only: posted entries can never be edited or deleted (only reversed).
CREATE TRIGGER IF NOT EXISTS trg_jl_no_update BEFORE UPDATE ON journal_lines
BEGIN SELECT RAISE(ABORT, 'journal lines are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_jl_no_delete BEFORE DELETE ON journal_lines
BEGIN SELECT RAISE(ABORT, 'journal lines are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_je_no_delete BEFORE DELETE ON journal_entries
BEGIN SELECT RAISE(ABORT, 'journal entries are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_je_immutable BEFORE UPDATE ON journal_entries
WHEN NEW.date IS NOT OLD.date OR NEW.number IS NOT OLD.number OR NEW.memo IS NOT OLD.memo
  OR NEW.ref_type IS NOT OLD.ref_type OR NEW.ref_id IS NOT OLD.ref_id
BEGIN SELECT RAISE(ABORT, 'journal entries are immutable'); END;

CREATE TABLE IF NOT EXISTS fixed_assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  purchase_date TEXT NOT NULL,
  cost_cents INTEGER NOT NULL CHECK (cost_cents > 0),
  salvage_cents INTEGER NOT NULL DEFAULT 0 CHECK (salvage_cents >= 0),
  useful_life_months INTEGER NOT NULL CHECK (useful_life_months > 0),
  accumulated_cents INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','fully_depreciated')),
  entry_id INTEGER REFERENCES journal_entries(id), notes TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS depreciation_postings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  asset_id INTEGER NOT NULL REFERENCES fixed_assets(id),
  period TEXT NOT NULL, date TEXT NOT NULL, amount_cents INTEGER NOT NULL,
  entry_id INTEGER NOT NULL REFERENCES journal_entries(id),
  UNIQUE(asset_id, period)
);

-- ---------------------------------------------------------------- sales & purchases
CREATE TABLE IF NOT EXISTS trade_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_type TEXT NOT NULL CHECK (doc_type IN ('invoice','credit_note','bill','debit_note')),
  number TEXT,
  partner_id INTEGER NOT NULL REFERENCES partners(id),
  warehouse_id INTEGER REFERENCES warehouses(id),
  date TEXT NOT NULL, due_date TEXT,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','posted','cancelled')),
  payment_status TEXT NOT NULL DEFAULT 'unpaid' CHECK (payment_status IN ('unpaid','partial','paid')),
  subtotal_cents INTEGER NOT NULL DEFAULT 0,
  tax_cents INTEGER NOT NULL DEFAULT 0,
  total_cents INTEGER NOT NULL DEFAULT 0,
  paid_cents INTEGER NOT NULL DEFAULT 0,
  origin_id INTEGER REFERENCES trade_documents(id),
  source TEXT NOT NULL DEFAULT 'manual',
  notes TEXT, journal_entry_id INTEGER REFERENCES journal_entries(id),
  created_by INTEGER, created_at TEXT NOT NULL DEFAULT (datetime('now')),
  posted_at TEXT, cancelled_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_td_number ON trade_documents(doc_type, number) WHERE number IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_td_partner ON trade_documents(partner_id);
CREATE INDEX IF NOT EXISTS idx_td_date ON trade_documents(date);

CREATE TABLE IF NOT EXISTS trade_document_lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL REFERENCES trade_documents(id) ON DELETE CASCADE,
  product_id INTEGER REFERENCES products(id),
  account_id INTEGER REFERENCES accounts(id),
  description TEXT,
  qty REAL NOT NULL CHECK (qty > 0),
  unit_price_cents INTEGER NOT NULL DEFAULT 0 CHECK (unit_price_cents >= 0),
  discount_pct REAL NOT NULL DEFAULT 0,
  tax_rate REAL NOT NULL DEFAULT 0,
  net_cents INTEGER NOT NULL DEFAULT 0,
  tax_cents INTEGER NOT NULL DEFAULT 0,
  total_cents INTEGER NOT NULL DEFAULT 0,
  cost_cents INTEGER NOT NULL DEFAULT 0,
  lot_no TEXT, expiry_date TEXT
);
CREATE INDEX IF NOT EXISTS idx_tdl_doc ON trade_document_lines(document_id);
CREATE INDEX IF NOT EXISTS idx_tdl_product ON trade_document_lines(product_id);

CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  number TEXT,
  direction TEXT NOT NULL CHECK (direction IN ('in','out')),
  partner_id INTEGER NOT NULL REFERENCES partners(id),
  document_id INTEGER NOT NULL REFERENCES trade_documents(id),
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  date TEXT NOT NULL, method TEXT, reference TEXT, note TEXT,
  status TEXT NOT NULL DEFAULT 'posted' CHECK (status IN ('posted','cancelled')),
  journal_entry_id INTEGER REFERENCES journal_entries(id),
  created_by INTEGER, created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_payments_doc ON payments(document_id);

CREATE TABLE IF NOT EXISTS credit_applications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  credit_id INTEGER NOT NULL REFERENCES trade_documents(id),
  target_id INTEGER NOT NULL REFERENCES trade_documents(id),
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  date TEXT NOT NULL, created_by INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- HR
CREATE TABLE IF NOT EXISTS departments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS employees (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  department_id INTEGER REFERENCES departments(id),
  position TEXT, salary_cents INTEGER NOT NULL DEFAULT 0 CHECK (salary_cents >= 0),
  phone TEXT, email TEXT, hire_date TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
  advance_balance_cents INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT
);
CREATE TABLE IF NOT EXISTS attendance (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id INTEGER NOT NULL REFERENCES employees(id),
  date TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('present','late','absent','leave')),
  UNIQUE(employee_id, date)
);
CREATE TABLE IF NOT EXISTS leave_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id INTEGER NOT NULL REFERENCES employees(id),
  type TEXT NOT NULL DEFAULT 'annual' CHECK (type IN ('annual','sick','unpaid','other')),
  start_date TEXT NOT NULL, end_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  note TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT
);
CREATE TABLE IF NOT EXISTS advances (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id INTEGER NOT NULL REFERENCES employees(id),
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  date TEXT NOT NULL, account_id INTEGER REFERENCES accounts(id), note TEXT,
  journal_entry_id INTEGER REFERENCES journal_entries(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS payroll_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT,
  month TEXT NOT NULL, date TEXT NOT NULL,
  account_id INTEGER REFERENCES accounts(id),
  total_net_cents INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'posted' CHECK (status IN ('posted','cancelled')),
  journal_entry_id INTEGER REFERENCES journal_entries(id),
  created_by INTEGER, created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_payroll_month ON payroll_runs(month) WHERE status = 'posted';
CREATE TABLE IF NOT EXISTS payroll_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
  employee_id INTEGER NOT NULL REFERENCES employees(id),
  salary_cents INTEGER NOT NULL, bonus_cents INTEGER NOT NULL DEFAULT 0,
  deduction_cents INTEGER NOT NULL DEFAULT 0, advance_deduction_cents INTEGER NOT NULL DEFAULT 0,
  net_cents INTEGER NOT NULL
);

-- ---------------------------------------------------------------- projects
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  partner_id INTEGER REFERENCES partners(id),
  start_date TEXT, end_date TEXT, budget_cents INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','active','done','cancelled')),
  notes TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  title TEXT NOT NULL, assignee_id INTEGER REFERENCES employees(id),
  due_date TEXT, priority TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('low','medium','high')),
  status TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo','doing','done')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT
);
CREATE TABLE IF NOT EXISTS time_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id INTEGER NOT NULL REFERENCES employees(id),
  project_id INTEGER NOT NULL REFERENCES projects(id),
  task_id INTEGER REFERENCES tasks(id),
  hours REAL NOT NULL CHECK (hours > 0), date TEXT NOT NULL, note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- CRM
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT, email TEXT,
  source TEXT, notes TEXT,
  status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','contacted','qualified','lost','converted')),
  partner_id INTEGER REFERENCES partners(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT
);
CREATE TABLE IF NOT EXISTS opportunities (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
  partner_id INTEGER REFERENCES partners(id), contact_name TEXT,
  value_cents INTEGER NOT NULL DEFAULT 0, expected_close TEXT,
  stage TEXT NOT NULL DEFAULT 'prospecting' CHECK (stage IN ('prospecting','proposal','negotiation','won','lost')),
  probability INTEGER NOT NULL DEFAULT 10 CHECK (probability BETWEEN 0 AND 100),
  created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT
);
CREATE TABLE IF NOT EXISTS activities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL DEFAULT 'call' CHECK (type IN ('call','meeting','email','visit','task')),
  contact_name TEXT, date TEXT NOT NULL, notes TEXT, done INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT
);
CREATE TABLE IF NOT EXISTS campaigns (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, channel TEXT,
  start_date TEXT, end_date TEXT, budget_cents INTEGER NOT NULL DEFAULT 0, notes TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT
);

-- ---------------------------------------------------------------- manufacturing
CREATE TABLE IF NOT EXISTS boms (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  product_id INTEGER NOT NULL REFERENCES products(id),
  labor_cost_cents INTEGER NOT NULL DEFAULT 0, overhead_cost_cents INTEGER NOT NULL DEFAULT 0,
  is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS bom_components (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bom_id INTEGER NOT NULL REFERENCES boms(id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products(id),
  qty REAL NOT NULL CHECK (qty > 0)          -- per 1 unit of finished product
);
CREATE TABLE IF NOT EXISTS production_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT,
  bom_id INTEGER NOT NULL REFERENCES boms(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
  qty REAL NOT NULL CHECK (qty > 0), date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'done' CHECK (status IN ('done','cancelled')),
  material_cost_cents INTEGER NOT NULL DEFAULT 0, labor_cost_cents INTEGER NOT NULL DEFAULT 0,
  overhead_cost_cents INTEGER NOT NULL DEFAULT 0, total_cost_cents INTEGER NOT NULL DEFAULT 0,
  journal_entry_id INTEGER REFERENCES journal_entries(id),
  created_by INTEGER, created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- maintenance & documents
CREATE TABLE IF NOT EXISTS maintenance_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, asset_name TEXT NOT NULL, issue TEXT NOT NULL,
  priority TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('low','medium','high','critical')),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','resolved','closed')),
  reported_date TEXT NOT NULL, resolved_date TEXT, cost_cents INTEGER NOT NULL DEFAULT 0, notes TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT
);
CREATE TABLE IF NOT EXISTS maintenance_schedules (
  id INTEGER PRIMARY KEY AUTOINCREMENT, asset_name TEXT NOT NULL, task TEXT NOT NULL,
  frequency_days INTEGER NOT NULL CHECK (frequency_days > 0), last_done TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT
);
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, category TEXT, related_to TEXT,
  link TEXT, note TEXT, date TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT
);
