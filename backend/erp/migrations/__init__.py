"""Versioned SQLite migrations for ERP Global Pro.

Tenant migrations intentionally fail closed.  Stage 3 adds a company owner to
all tenant-owned records while retaining single-company compatibility.  The
application and database continue to block business writes once more than one
company exists until tenant-aware queries and writes are complete.
"""

import json

LATEST_VERSION = 8

# Global control-plane tables are deliberately excluded.  audit_log is hybrid:
# company events carry company_id, while authentication/system events may be NULL.
TENANT_TABLES = (
    'accounts', 'activities', 'advances', 'attendance', 'bom_components', 'boms',
    'campaigns', 'categories', 'cost_centers', 'credit_applications', 'departments',
    'depreciation_postings', 'employees', 'files', 'fixed_assets', 'journal_entries',
    'journal_lines', 'leads', 'leave_requests', 'maintenance_requests',
    'maintenance_schedules', 'opportunities', 'partners', 'payments', 'payroll_items',
    'payroll_runs', 'production_orders', 'products', 'projects', 'sequences', 'settings',
    'stock_count_lines', 'stock_counts', 'stock_levels', 'stock_moves', 'tasks',
    'time_logs', 'trade_document_lines', 'trade_documents', 'warehouses',
)


def _setting(conn, key, default=None):
    row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return row[0]


def _migrate_to_v2(conn):
    """Add the tenant identity foundation and map legacy users to one company."""
    statements = (
        """CREATE TABLE IF NOT EXISTS companies (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          legal_name TEXT,
          industry TEXT NOT NULL DEFAULT 'general',
          country TEXT NOT NULL DEFAULT '',
          currency TEXT NOT NULL DEFAULT 'USD',
          timezone TEXT NOT NULL DEFAULT 'UTC',
          status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active','suspended','closed')),
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS company_memberships (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          role TEXT NOT NULL REFERENCES roles(name),
          status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active','invited','suspended')),
          invited_by INTEGER REFERENCES users(id),
          joined_at TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE(company_id, user_id)
        )""",
        'CREATE INDEX IF NOT EXISTS idx_company_memberships_user ON company_memberships(user_id, status)',
        'CREATE INDEX IF NOT EXISTS idx_company_memberships_company ON company_memberships(company_id, status)',
    )
    for statement in statements:
        conn.execute(statement)

    user_count = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    if user_count and conn.execute('SELECT COUNT(*) FROM companies').fetchone()[0] == 0:
        name = str(_setting(conn, 'company', 'My Company') or 'My Company').strip() or 'My Company'
        country = str(_setting(conn, 'country', '') or '').strip()
        currency = str(_setting(conn, 'currency', 'USD') or 'USD').strip().upper()
        cur = conn.execute(
            'INSERT INTO companies(name, country, currency) VALUES(?,?,?)',
            (name[:200], country[:100], currency[:3] or 'USD'),
        )
        company_id = cur.lastrowid

        missing_roles = conn.execute(
            'SELECT DISTINCT u.role FROM users u LEFT JOIN roles r ON r.name=u.role WHERE r.name IS NULL'
        ).fetchall()
        if missing_roles:
            raise RuntimeError('Cannot migrate memberships: one or more users reference unknown roles.')

        conn.execute(
            """INSERT INTO company_memberships
               (company_id, user_id, role, status, joined_at)
               SELECT ?, id, role, 'active', COALESCE(created_at, datetime('now'))
               FROM users""",
            (company_id,),
        )


def _migrate_to_v3(conn):
    """Add company ownership to every tenant-owned table.

    The source application is single-company.  A database with more than one
    company cannot be safely backfilled without an explicit row-to-company map,
    so the migration refuses to guess.
    """
    company_rows = conn.execute('SELECT id FROM companies ORDER BY id').fetchall()
    if not company_rows:
        name = str(_setting(conn, 'company', 'My Company') or 'My Company').strip() or 'My Company'
        country = str(_setting(conn, 'country', '') or '').strip()
        currency = str(_setting(conn, 'currency', 'USD') or 'USD').strip().upper()
        cur = conn.execute(
            'INSERT INTO companies(name, country, currency) VALUES(?,?,?)',
            (name[:200], country[:100], currency[:3] or 'USD'),
        )
        bootstrap_company_id = int(cur.lastrowid)
    elif len(company_rows) == 1:
        bootstrap_company_id = int(company_rows[0][0])
    else:
        raise RuntimeError(
            'Stage 3 cannot infer ownership for a database with multiple companies. '
            'Restore a single-company backup or prepare an explicit row-to-company mapping first.'
        )

    existing_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    required = set(TENANT_TABLES) | {'audit_log'}
    missing = sorted(required - existing_tables)
    if missing:
        raise RuntimeError('Stage 3 requires the complete legacy schema; missing tables: ' + ', '.join(missing))

    for table in TENANT_TABLES:
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
        if 'company_id' not in columns:
            # Foreign keys are temporarily disabled by apply_migrations so SQLite
            # permits a NOT NULL REFERENCES column with a deterministic backfill.
            conn.execute(
                f'ALTER TABLE "{table}" ADD COLUMN company_id INTEGER NOT NULL '
                f'DEFAULT {bootstrap_company_id} REFERENCES companies(id) ON DELETE RESTRICT'
            )
        conn.execute(
            f'CREATE INDEX IF NOT EXISTS "idx_tenant_{table}_company_id" '
            f'ON "{table}"(company_id)'
        )
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS "trg_tenant_{table}_company_immutable"
            BEFORE UPDATE OF company_id ON "{table}"
            WHEN NEW.company_id IS NULL OR OLD.company_id <> NEW.company_id
            BEGIN SELECT RAISE(ABORT, 'company_id is immutable'); END''')
        # Defence in depth: the current legacy services are not tenant-scoped.
        # They remain usable for one company but cannot write once another exists.
        for operation in ('INSERT', 'UPDATE', 'DELETE'):
            trigger = f'trg_tenant_{table}_block_multi_{operation.lower()}'
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS "{trigger}"
                BEFORE {operation} ON "{table}"
                WHEN (SELECT COUNT(*) FROM companies) > 1
                BEGIN SELECT RAISE(ABORT, 'tenant_isolation_pending'); END''')

    # Audit records may be system-wide (NULL) or tied to an active company.
    audit_columns = {row[1] for row in conn.execute('PRAGMA table_info("audit_log")').fetchall()}
    if 'company_id' not in audit_columns:
        conn.execute(
            'ALTER TABLE audit_log ADD COLUMN company_id INTEGER REFERENCES companies(id) ON DELETE RESTRICT'
        )
    conn.execute('UPDATE audit_log SET company_id=? WHERE company_id IS NULL', (bootstrap_company_id,))
    conn.execute('CREATE INDEX IF NOT EXISTS idx_tenant_audit_log_company_id ON audit_log(company_id)')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS trg_tenant_audit_log_company_immutable
        BEFORE UPDATE OF company_id ON audit_log
        WHEN NEW.company_id IS NOT OLD.company_id
        BEGIN SELECT RAISE(ABORT, 'company_id is immutable'); END''')



def _quote_ident(value):
    """Quote a SQLite identifier discovered from trusted schema metadata."""
    return '"' + str(value).replace('"', '""') + '"'


def _tenant_foreign_key_groups(conn):
    """Return tenant-to-tenant foreign keys grouped by FK id and ordered seq."""
    tenant_tables = set(TENANT_TABLES) | {'audit_log'}
    existing = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    groups = []
    for child in sorted(tenant_tables & existing):
        rows = conn.execute(f'PRAGMA foreign_key_list({_quote_ident(child)})').fetchall()
        by_id = {}
        for row in rows:
            parent, from_col, to_col = row[2], row[3], row[4]
            if parent not in tenant_tables or parent not in existing:
                continue
            by_id.setdefault(row[0], []).append((row[1], from_col, to_col))
        for fk_id, parts in by_id.items():
            parts.sort(key=lambda item: item[0])
            groups.append((child, fk_id, parts))
    return groups


def _migrate_to_v4(conn):
    """Protect tenant-to-tenant references against cross-company links.

    SQLite cannot add composite foreign-key constraints to existing tables
    without rebuilding them. This migration uses fail-closed INSERT and UPDATE
    triggers for every tenant-to-tenant relationship, plus child-side indexes.
    PostgreSQL composite FKs/RLS remain a later stage.
    """
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    required = set(TENANT_TABLES) | {'audit_log'}
    missing = sorted(required - tables)
    if missing:
        raise RuntimeError('Stage 4 requires tenant ownership migration first; missing tables: ' + ', '.join(missing))

    for table in required:
        cols = {row[1] for row in conn.execute(f'PRAGMA table_info({_quote_ident(table)})').fetchall()}
        if 'company_id' not in cols:
            raise RuntimeError(f'Stage 4 cannot protect {table}: company_id is missing')

    relationships = _tenant_foreign_key_groups(conn)
    if len(relationships) != 55:
        raise RuntimeError(
            f'Stage 4 expected 55 tenant-to-tenant foreign-key relationships; found {len(relationships)}. '
            'Review schema drift before applying this migration.'
        )

    # Refuse to certify a database that already contains cross-company links.
    for child, fk_id, parts in relationships:
        child_q = _quote_ident(child)
        fk_rows = conn.execute(f'PRAGMA foreign_key_list({child_q})').fetchall()
        parent_name = next(row[2] for row in fk_rows if row[0] == fk_id)
        match_terms = [
            f'p.{_quote_ident(to_col)} = c.{_quote_ident(from_col)}'
            for _, from_col, to_col in parts
        ]
        non_null_terms = [f'c.{_quote_ident(from_col)} IS NOT NULL' for _, from_col, _ in parts]
        query = (
            f'SELECT c.rowid FROM {child_q} c JOIN {_quote_ident(parent_name)} p '
            f'ON {" AND ".join(match_terms)} '
            f'WHERE {" AND ".join(non_null_terms)} '
            f'AND p.company_id IS NOT c.company_id LIMIT 1'
        )
        bad = conn.execute(query).fetchone()
        if bad:
            raise RuntimeError(
                f'Stage 4 found a cross-company relationship: {child} foreign-key #{fk_id}, '
                f'rowid {bad[0]}. Reconcile ownership before applying this migration.'
            )

    for child, fk_id, parts in relationships:
        child_q = _quote_ident(child)
        fk_cols = [from_col for _, from_col, _ in parts]
        index_cols = ['company_id', *fk_cols]
        index_name = f'idx_tenant_rel_{child}_{fk_id}'
        conn.execute(
            f'CREATE INDEX IF NOT EXISTS {_quote_ident(index_name)} '
            f'ON {child_q} ({", ".join(_quote_ident(col) for col in index_cols)})'
        )

        fk_rows = conn.execute(f'PRAGMA foreign_key_list({child_q})').fetchall()
        parent_name = next(row[2] for row in fk_rows if row[0] == fk_id)
        parent_q = _quote_ident(parent_name)
        match = ' AND '.join(
            f'p.{_quote_ident(to_col)} = NEW.{_quote_ident(from_col)}'
            for _, from_col, to_col in parts
        )
        non_null = ' AND '.join(
            f'NEW.{_quote_ident(from_col)} IS NOT NULL'
            for _, from_col, _ in parts
        )
        guard_condition = (
            f'NEW.company_id IS NOT NULL AND {non_null} AND '
            f'EXISTS (SELECT 1 FROM {parent_q} p WHERE {match} '
            f'AND p.company_id IS NOT NEW.company_id)'
        )
        update_cols = ', '.join(_quote_ident(col) for col in ['company_id', *fk_cols])
        for suffix, event, update_clause in (
            ('ins', 'INSERT', ''),
            ('upd', 'UPDATE', f' OF {update_cols}'),
        ):
            trigger_name = f'trg_tenant_rel_{child}_{fk_id}_{suffix}'
            trigger_sql = (
                f'CREATE TRIGGER IF NOT EXISTS {_quote_ident(trigger_name)} '
                f'BEFORE {event}{update_clause} ON {child_q} '
                f'WHEN {guard_condition} '
                f"BEGIN SELECT RAISE(ABORT, 'cross_company_reference: {child} foreign-key {fk_id}'); END"
            )
            conn.execute(trigger_sql)

    # Audit records are read by company and ordered by their timestamp.
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_tenant_audit_log_company_ts '
        'ON audit_log(company_id, ts)'
    )

    trigger_count = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_tenant_rel_%'"
    ).fetchone()[0]
    index_count = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name LIKE 'idx_tenant_rel_%'"
    ).fetchone()[0]
    audit_index_count = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='index' "
        "AND name='idx_tenant_audit_log_company_ts'"
    ).fetchone()[0]
    if trigger_count != 110 or index_count != 55 or audit_index_count != 1:
        raise RuntimeError(
            f'Stage 4 object-count check failed: {trigger_count} relation triggers, '
            f'{index_count} relation indexes, and {audit_index_count} audit indexes were created; '
            'expected 110, 55, and 1 respectively.'
        )

    violations = conn.execute('PRAGMA foreign_key_check').fetchall()
    if violations:
        raise RuntimeError(f'Stage 4 foreign-key check failed: {violations[:10]}')


def _migrate_to_v5(conn):
    '''Make company settings truly tenant-keyed and add workload indexes.

    The legacy sequence name remains globally unique in SQLite. The service
    therefore refuses cross-company reuse until a later migration can rebuild
    document-number uniqueness constraints as a coordinated unit.
    '''
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()}
    if not {'companies', 'settings', 'sequences'} <= tables:
        raise RuntimeError('Stage 5 requires companies, settings, and sequences tables.')

    settings_cols = {row[1] for row in conn.execute('PRAGMA table_info("settings")').fetchall()}
    if 'company_id' not in settings_cols:
        raise RuntimeError('Stage 5 requires company ownership on settings before rebuilding its key.')
    bad = conn.execute('SELECT rowid FROM settings WHERE company_id IS NULL LIMIT 1').fetchone()
    if bad:
        raise RuntimeError('Stage 5 found a settings row without company ownership.')

    pk_cols = [row[1] for row in sorted(
        (r for r in conn.execute('PRAGMA table_info("settings")').fetchall() if r[5]), key=lambda r: r[5]
    )]
    if pk_cols != ['company_id', 'key']:
        conn.execute('''CREATE TABLE settings_v5 (
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY(company_id, key)
        )''')
        conn.execute('''INSERT INTO settings_v5(company_id,key,value)
                        SELECT company_id,key,value FROM settings''')
        conn.execute('DROP TABLE settings')
        conn.execute('ALTER TABLE settings_v5 RENAME TO settings')

    conn.execute('CREATE INDEX IF NOT EXISTS idx_tenant_settings_company_id ON settings(company_id)')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS trg_tenant_settings_company_immutable
        BEFORE UPDATE OF company_id ON settings
        WHEN NEW.company_id IS NULL OR OLD.company_id <> NEW.company_id
        BEGIN SELECT RAISE(ABORT, 'company_id is immutable'); END''')
    for operation in ('INSERT', 'UPDATE', 'DELETE'):
        trigger = f'trg_tenant_settings_block_multi_{operation.lower()}'
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS "{trigger}"
            BEFORE {operation} ON settings
            WHEN (SELECT COUNT(*) FROM companies) > 1
            BEGIN SELECT RAISE(ABORT, 'tenant_isolation_pending'); END''')

    # Tenant-oriented indexes for the first service conversion slice.
    indexes = (
        ('idx_stock_moves_tenant_product_warehouse_date',
         'stock_moves', 'company_id, product_id, warehouse_id, date'),
        ('idx_stock_levels_tenant_product_warehouse',
         'stock_levels', 'company_id, product_id, warehouse_id'),
        ('idx_journal_entries_tenant_date',
         'journal_entries', 'company_id, date'),
        ('idx_journal_lines_tenant_entry_account',
         'journal_lines', 'company_id, entry_id, account_id'),
    )
    for name, table, columns in indexes:
        cols = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
        if 'company_id' not in cols:
            raise RuntimeError(f'Stage 5 cannot index {table}: company_id is missing.')
        conn.execute(f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}"({columns})')

    # The sequence table still has a global PRIMARY KEY(name). Retain a lookup
    # index now; converting it and document-number UNIQUE keys is a separate
    # migration because these constraints must change atomically together.
    conn.execute('CREATE INDEX IF NOT EXISTS idx_sequences_tenant_name ON sequences(company_id,name)')

    violations = conn.execute('PRAGMA foreign_key_check').fetchall()
    if violations:
        raise RuntimeError(f'Stage 5 foreign-key check failed: {violations[:10]}')


# Candidate business keys that become unique within a company in Stage 10.
# `where` mirrors the existing partial-index semantics where applicable.
TENANT_UNIQUE_RULES_V6 = (
    ('accounts', ('code',), None),
    ('attendance', ('employee_id', 'date'), None),
    ('categories', ('name',), None),
    ('cost_centers', ('name',), None),
    ('departments', ('name',), None),
    ('depreciation_postings', ('asset_id', 'period'), None),
    ('journal_entries', ('number',), None),
    ('payroll_runs', ('month',), "status = 'posted'"),
    ('products', ('sku',), None),
    ('products', ('barcode',), 'barcode IS NOT NULL'),
    ('sequences', ('name',), None),
    ('settings', ('key',), None),
    ('stock_levels', ('product_id', 'warehouse_id'), None),
    ('trade_documents', ('doc_type', 'number'), 'number IS NOT NULL'),
    ('warehouses', ('code',), None),
)

# These SQLite objects represent global uniqueness and must be removed/replaced
# while the corresponding table is rebuilt. User email and role identities are
# deliberately not included: they remain global control-plane identities.
V6_REBUILD_COLUMN_UNIQUES = {
    'accounts': {'code'},
    'categories': {'name'},
    'cost_centers': {'name'},
    'departments': {'name'},
    'journal_entries': {'number'},
    'products': {'sku'},
    'warehouses': {'code'},
}
V6_REBUILD_TABLE_UNIQUES = {
    'attendance': {('employee_id', 'date')},
    'depreciation_postings': {('asset_id', 'period')},
}
V6_REBUILD_PRIMARY_KEYS = {'stock_levels'}
V6_REBUILD_TABLES = (tuple(V6_REBUILD_COLUMN_UNIQUES)
                     + tuple(V6_REBUILD_TABLE_UNIQUES)
                     + tuple(V6_REBUILD_PRIMARY_KEYS))
V6_REPLACED_INDEXES = {'idx_products_barcode', 'idx_td_number', 'idx_payroll_month'}


def _split_top_level_sql_list(value):
    """Split a table-definition body on commas outside nested SQL expressions."""
    parts = []
    start = 0
    depth = 0
    quote = None
    i = 0
    while i < len(value):
        char = value[i]
        if quote:
            if quote == ']' and char == ']':
                quote = None
            elif quote != ']' and char == quote:
                if i + 1 < len(value) and value[i + 1] == quote:
                    i += 1
                else:
                    quote = None
        elif char in ("'", '"', '`'):
            quote = char
        elif char == '[':
            quote = ']'
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        elif char == ',' and depth == 0:
            parts.append(value[start:i].strip())
            start = i + 1
        i += 1
    tail = value[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _matching_sql_paren(value, opening):
    depth = 0
    quote = None
    i = opening
    while i < len(value):
        char = value[i]
        if quote:
            if quote == ']' and char == ']':
                quote = None
            elif quote != ']' and char == quote:
                if i + 1 < len(value) and value[i + 1] == quote:
                    i += 1
                else:
                    quote = None
        elif char in ("'", '"', '`'):
            quote = char
        elif char == '[':
            quote = ']'
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise RuntimeError('Unbalanced SQL table definition during Stage 10 rebuild.')


def _column_clause_name(clause):
    import re
    match = re.match(r'\s*(?:"([^"]+)"|`([^`]+)`|\[([^]]+)\]|([A-Za-z_][A-Za-z0-9_]*))', clause)
    if not match:
        return None
    return next(group for group in match.groups() if group is not None)


def _tenant_rebuild_table(conn, table):
    """Rebuild one SQLite table while preserving its non-unique indexes/triggers."""
    import re
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not row or not row[0]:
        raise RuntimeError(f'Stage 10 cannot rebuild missing table: {table}')
    original_sql = row[0]
    open_at = original_sql.find('(')
    if open_at < 0:
        raise RuntimeError(f'Stage 10 found an invalid table definition for {table}.')
    close_at = _matching_sql_paren(original_sql, open_at)
    clauses = _split_top_level_sql_list(original_sql[open_at + 1:close_at])

    trigger_sql = [r[0] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name=? AND sql IS NOT NULL ORDER BY name",
        (table,),
    ).fetchall()]
    index_sql = [r[0] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL ORDER BY name",
        (table,),
    ).fetchall() if r[0] and not any(name in r[0] for name in V6_REPLACED_INDEXES)]

    transformed = []
    remove_columns = V6_REBUILD_COLUMN_UNIQUES.get(table, set())
    remove_table_uniques = V6_REBUILD_TABLE_UNIQUES.get(table, set())
    removed_table_unique_sets = set()
    for clause in clauses:
        stripped = clause.strip()
        upper = stripped.upper()
        if upper.startswith('UNIQUE') or re.match(r'(?is)^CONSTRAINT\s+[^\s]+\s+UNIQUE\s*\(', stripped):
            paren = stripped.find('(')
            close = _matching_sql_paren(stripped, paren) if paren >= 0 else -1
            cols = tuple(part.strip().strip('"`[]').split()[0] for part in
                         _split_top_level_sql_list(stripped[paren + 1:close])) if close > paren else ()
            if cols in remove_table_uniques:
                removed_table_unique_sets.add(cols)
                continue
        if table == 'stock_levels' and upper.startswith('PRIMARY KEY'):
            paren = stripped.find('(')
            close = _matching_sql_paren(stripped, paren)
            old_cols = [part.strip().strip('"`[]') for part in
                        _split_top_level_sql_list(stripped[paren + 1:close])]
            if tuple(old_cols) == ('product_id', 'warehouse_id'):
                clause = stripped[:paren + 1] + 'company_id, product_id, warehouse_id' + stripped[close:]
        column = _column_clause_name(clause)
        if column and column in remove_columns:
            clause = re.sub(r'\s+UNIQUE\b', '', clause, count=1, flags=re.IGNORECASE)
        transformed.append(clause)

    if removed_table_unique_sets != remove_table_uniques:
        raise RuntimeError(
            f'Stage 10 expected table UNIQUE clauses {sorted(remove_table_uniques)} '
            f'in {table}, found {sorted(removed_table_unique_sets)}.'
        )
    if table == 'stock_levels' and not any(c.strip().upper().startswith('PRIMARY KEY (COMPANY_ID, PRODUCT_ID, WAREHOUSE_ID)')
                                           for c in transformed):
        raise RuntimeError('Stage 10 could not convert the stock_levels primary key.')

    temp = f'{table}__stage10_new'
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (temp,)).fetchone()
    if exists:
        raise RuntimeError(f'Stage 10 temporary table already exists: {temp}')
    new_sql = f'CREATE TABLE "{temp}" (' + ', '.join(transformed) + ')' + original_sql[close_at + 1:]
    columns = [r[1] for r in conn.execute(f'PRAGMA table_info({_quote_ident(table)})').fetchall()]
    quoted_columns = ', '.join(_quote_ident(col) for col in columns)
    source_count = conn.execute(f'SELECT COUNT(*) FROM {_quote_ident(table)}').fetchone()[0]
    conn.execute(new_sql)
    conn.execute(
        f'INSERT INTO {_quote_ident(temp)} ({quoted_columns}) '
        f'SELECT {quoted_columns} FROM {_quote_ident(table)}'
    )
    copied_count = conn.execute(f'SELECT COUNT(*) FROM {_quote_ident(temp)}').fetchone()[0]
    if copied_count != source_count:
        raise RuntimeError(f'Stage 10 row-count mismatch while rebuilding {table}.')
    conn.execute(f'DROP TABLE {_quote_ident(table)}')
    conn.execute(f'ALTER TABLE {_quote_ident(temp)} RENAME TO {_quote_ident(table)}')

    for statement in index_sql:
        conn.execute(statement)
    for statement in trigger_sql:
        conn.execute(statement)


def _assert_v6_unique_preflight(conn):
    """Fail closed on missing ownership or in-company duplicate candidate keys."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()}
    for table, columns, predicate in TENANT_UNIQUE_RULES_V6:
        if table not in tables:
            raise RuntimeError(f'Stage 10 preflight is missing required table: {table}')
        present = {r[1] for r in conn.execute(f'PRAGMA table_info({_quote_ident(table)})').fetchall()}
        required = {'company_id', *columns}
        if predicate:
            # All predicates in this reviewed constant set refer only to known columns.
            required.update({'status'} if 'status' in predicate else {'barcode'} if 'barcode' in predicate else {'number'})
        missing = required - present
        if missing:
            raise RuntimeError(f'Stage 10 preflight missing columns in {table}: {sorted(missing)}')
        unowned = conn.execute(
            f'SELECT COUNT(*) FROM {_quote_ident(table)} WHERE company_id IS NULL'
        ).fetchone()[0]
        if unowned:
            raise RuntimeError(f'Stage 10 found {unowned} rows without company ownership in {table}.')
        key_sql = ', '.join(_quote_ident(col) for col in columns)
        where = 'company_id IS NOT NULL AND ' + ' AND '.join(f'{_quote_ident(col)} IS NOT NULL' for col in columns)
        if predicate:
            where += f' AND ({predicate})'
        duplicate = conn.execute(
            f'SELECT company_id, {key_sql}, COUNT(*) AS n FROM {_quote_ident(table)} '
            f'WHERE {where} GROUP BY company_id, {key_sql} HAVING COUNT(*) > 1 LIMIT 1'
        ).fetchone()
        if duplicate:
            raise RuntimeError(
                f'Stage 10 found an in-company duplicate candidate key in {table}; '
                'run tenant_uniqueness_preflight.py and reconcile the restored database before migration.'
            )


def _migrate_to_v6(conn):
    """Scope document sequences and all reviewed business UNIQUE keys by company.

    This migration changes only schema constraints/indexes and preserves rows.
    It does not enable multi-company writes; the existing fail-closed triggers
    remain in place until service/API/RLS release gates are independently met.
    """
    _assert_v6_unique_preflight(conn)

    # SQLite may reject DROP/RENAME of a referenced table while a trigger on a
    # different table still compiles against the temporarily absent table. Save
    # every trigger, remove all triggers inside this migration transaction, and
    # restore them only after every table has its final name again.
    preserved_triggers = [r[0] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL ORDER BY name"
    ).fetchall()]
    trigger_names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
    ).fetchall()]
    for trigger_name in trigger_names:
        conn.execute(f'DROP TRIGGER {_quote_ident(trigger_name)}')

    # Rebuild the sequence table so its identity is (company_id, name).
    seq_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sequences'"
    ).fetchone()
    if not seq_row:
        raise RuntimeError('Stage 10 requires the sequences table.')
    seq_columns = {r[1] for r in conn.execute('PRAGMA table_info("sequences")').fetchall()}
    if not {'company_id', 'name', 'prefix', 'next_number', 'padding'} <= seq_columns:
        raise RuntimeError('Stage 10 found an unsupported sequences schema.')
    seq_pk = [r[1] for r in sorted(
        (r for r in conn.execute('PRAGMA table_info("sequences")').fetchall() if r[5]),
        key=lambda r: r[5],
    )]
    if seq_pk != ['company_id', 'name']:
        index_sql = [r[0] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='sequences' AND sql IS NOT NULL ORDER BY name"
        ).fetchall()]
        sequence_count = conn.execute('SELECT COUNT(*) FROM sequences').fetchone()[0]
        conn.execute("""CREATE TABLE sequences__stage10_new (
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            name TEXT NOT NULL,
            prefix TEXT NOT NULL,
            next_number INTEGER NOT NULL DEFAULT 1,
            padding INTEGER NOT NULL DEFAULT 6,
            PRIMARY KEY(company_id, name)
        )""")
        conn.execute("""INSERT INTO sequences__stage10_new(company_id,name,prefix,next_number,padding)
                        SELECT company_id,name,prefix,next_number,padding FROM sequences""")
        copied_sequences = conn.execute('SELECT COUNT(*) FROM sequences__stage10_new').fetchone()[0]
        if copied_sequences != sequence_count:
            raise RuntimeError('Stage 10 sequence migration row-count mismatch.')
        conn.execute('DROP TABLE sequences')
        conn.execute('ALTER TABLE sequences__stage10_new RENAME TO sequences')
        for statement in index_sql:
            conn.execute(statement)

    # Remove legacy global UNIQUE constraints from table definitions, then add
    # tenant-scoped unique indexes. External child FKs remain pointed at the same
    # table names; foreign_keys is OFF only for this transaction and checked later.
    for table in V6_REBUILD_TABLES:
        _tenant_rebuild_table(conn, table)

    # Replace the three explicit global partial unique indexes.
    conn.execute('DROP INDEX IF EXISTS idx_products_barcode')
    conn.execute('DROP INDEX IF EXISTS idx_td_number')
    conn.execute('DROP INDEX IF EXISTS idx_payroll_month')

    tenant_indexes = (
        ('uq_tenant_accounts_code', 'accounts', 'company_id, code', None),
        ('uq_tenant_attendance_employee_date', 'attendance', 'company_id, employee_id, date', None),
        ('uq_tenant_categories_name', 'categories', 'company_id, name', None),
        ('uq_tenant_cost_centers_name', 'cost_centers', 'company_id, name', None),
        ('uq_tenant_departments_name', 'departments', 'company_id, name', None),
        ('uq_tenant_depreciation_asset_period', 'depreciation_postings', 'company_id, asset_id, period', None),
        ('uq_tenant_journal_entries_number', 'journal_entries', 'company_id, number', None),
        ('uq_tenant_payroll_runs_posted_month', 'payroll_runs', 'company_id, month', "status = 'posted'"),
        ('uq_tenant_products_sku', 'products', 'company_id, sku', None),
        ('uq_tenant_products_barcode', 'products', 'company_id, barcode', 'barcode IS NOT NULL'),
        ('uq_tenant_trade_documents_type_number', 'trade_documents', 'company_id, doc_type, number', 'number IS NOT NULL'),
        ('uq_tenant_warehouses_code', 'warehouses', 'company_id, code', None),
    )
    for name, table, columns, predicate in tenant_indexes:
        suffix = f' WHERE {predicate}' if predicate else ''
        conn.execute(f'CREATE UNIQUE INDEX "{name}" ON "{table}"({columns}){suffix}')

    # The settings key is already a composite PK from Stage 9; verify it rather
    # than rebuilding it again.
    settings_pk = [r[1] for r in sorted(
        (r for r in conn.execute('PRAGMA table_info("settings")').fetchall() if r[5]),
        key=lambda r: r[5],
    )]
    if settings_pk != ['company_id', 'key']:
        raise RuntimeError('Stage 10 expected settings PRIMARY KEY(company_id, key).')
    seq_pk = [r[1] for r in sorted(
        (r for r in conn.execute('PRAGMA table_info("sequences")').fetchall() if r[5]),
        key=lambda r: r[5],
    )]
    if seq_pk != ['company_id', 'name']:
        raise RuntimeError('Stage 10 failed to create sequences PRIMARY KEY(company_id, name).')

    expected_names = {item[0] for item in tenant_indexes}
    actual_names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'uq_tenant_%'"
    ).fetchall()}
    if not expected_names <= actual_names:
        raise RuntimeError(f'Stage 10 tenant unique indexes missing: {sorted(expected_names - actual_names)}')

    for statement in preserved_triggers:
        conn.execute(statement)
    restored_trigger_count = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'"
    ).fetchone()[0]
    if restored_trigger_count != len(preserved_triggers):
        raise RuntimeError(
            f'Stage 10 trigger restoration mismatch: {restored_trigger_count} restored, '
            f'{len(preserved_triggers)} expected.'
        )

    violations = conn.execute('PRAGMA foreign_key_check').fetchall()
    if violations:
        raise RuntimeError(f'Stage 10 foreign-key check failed: {violations[:10]}')


def _migrate_to_v7(conn):
    """Create private, company-owned attachment object metadata.

    Existing `files` rows are metadata/links only and cannot be mapped to binary
    objects, so this migration deliberately does not invent storage records.
    Uploads are stored outside the web root and are addressed by opaque keys.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS file_objects (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
          storage_key TEXT NOT NULL UNIQUE,
          original_name TEXT NOT NULL,
          title TEXT NOT NULL,
          category TEXT,
          related_to TEXT,
          note TEXT,
          mime_type TEXT NOT NULL,
          size_bytes INTEGER NOT NULL CHECK(size_bytes > 0 AND size_bytes <= 10485760),
          sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
          uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE(company_id, id)
        )"""
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_file_objects_company_created '
        'ON file_objects(company_id, created_at DESC, id DESC)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_file_objects_company_hash '
        'ON file_objects(company_id, sha256)'
    )
    columns = {row[1] for row in conn.execute('PRAGMA table_info("file_objects")').fetchall()}
    required = {
        'id', 'company_id', 'storage_key', 'original_name', 'title', 'category',
        'related_to', 'note', 'mime_type', 'size_bytes', 'sha256', 'uploaded_by', 'created_at'
    }
    if not required <= columns:
        raise RuntimeError('Stage 15 file_objects schema is incomplete.')
    fk_columns = {row[3] for row in conn.execute('PRAGMA foreign_key_list("file_objects")').fetchall()}
    if not {'company_id', 'uploaded_by'} <= fk_columns:
        raise RuntimeError('Stage 15 file_objects foreign keys are incomplete.')


def _migrate_to_v8(conn):
    """Add fail-closed malware scan state and per-company upload throttling."""
    columns = {row[1] for row in conn.execute('PRAGMA table_info("file_objects")').fetchall()}
    if not columns:
        raise RuntimeError('Stage 16 requires the Stage 15 file_objects table.')
    if 'scan_status' not in columns:
        conn.execute("""ALTER TABLE file_objects ADD COLUMN scan_status TEXT NOT NULL DEFAULT 'pending'
                       CHECK(scan_status IN ('pending','clean','infected','error'))""")
    if 'scan_engine' not in columns:
        conn.execute('ALTER TABLE file_objects ADD COLUMN scan_engine TEXT')
    if 'scanned_at' not in columns:
        conn.execute('ALTER TABLE file_objects ADD COLUMN scanned_at TEXT')
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_file_objects_company_scan '
        'ON file_objects(company_id, scan_status, id)'
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS file_upload_buckets (
        company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        bucket_start INTEGER NOT NULL,
        upload_count INTEGER NOT NULL DEFAULT 0 CHECK(upload_count >= 0),
        PRIMARY KEY(company_id, user_id, bucket_start)
    )""")
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_file_upload_buckets_time '
        'ON file_upload_buckets(bucket_start)'
    )
    required = {'scan_status', 'scan_engine', 'scanned_at'}
    actual = {row[1] for row in conn.execute('PRAGMA table_info("file_objects")').fetchall()}
    if not required <= actual:
        raise RuntimeError('Stage 16 file scan columns are incomplete.')
    invalid = conn.execute(
        "SELECT id FROM file_objects WHERE scan_status NOT IN ('pending','clean','infected','error') LIMIT 1"
    ).fetchone()
    if invalid:
        raise RuntimeError('Stage 16 found an invalid file scan state.')

def apply_migrations(conn, target_version=None):
    """Apply pending migrations using the caller's SQLite connection.

    `target_version=2` exists for isolated migration tests and rollback tooling;
    production startup always uses the current `LATEST_VERSION`.
    """
    target = LATEST_VERSION if target_version is None else int(target_version)
    if target < 1 or target > LATEST_VERSION:
        raise ValueError(f'Unsupported migration target: {target}')

    # SQLite disallows ADD COLUMN ... REFERENCES with a non-NULL default while
    # foreign_keys is ON.  Toggle it only outside a transaction, then verify all
    # references before committing and restore the original setting.
    foreign_keys_were_on = bool(conn.execute('PRAGMA foreign_keys').fetchone()[0])
    if foreign_keys_were_on:
        conn.execute('PRAGMA foreign_keys=OFF')
    try:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute('SELECT COALESCE(MAX(version), 0) FROM schema_version').fetchone()[0]
        if current < 2 and target >= 2:
            _migrate_to_v2(conn)
            conn.execute('INSERT INTO schema_version(version) VALUES(2)')
            current = 2
        if current < 3 and target >= 3:
            _migrate_to_v3(conn)
            conn.execute('INSERT INTO schema_version(version) VALUES(3)')
            current = 3
        if current < 4 and target >= 4:
            _migrate_to_v4(conn)
            conn.execute('INSERT INTO schema_version(version) VALUES(4)')
            current = 4
        if current < 5 and target >= 5:
            _migrate_to_v5(conn)
            conn.execute('INSERT INTO schema_version(version) VALUES(5)')
            current = 5
        if current < 6 and target >= 6:
            _migrate_to_v6(conn)
            conn.execute('INSERT INTO schema_version(version) VALUES(6)')
            current = 6
        if current < 7 and target >= 7:
            _migrate_to_v7(conn)
            conn.execute('INSERT INTO schema_version(version) VALUES(7)')
            current = 7
        if current < 8 and target >= 8:
            _migrate_to_v8(conn)
            conn.execute('INSERT INTO schema_version(version) VALUES(8)')
            current = 8
        violations = conn.execute('PRAGMA foreign_key_check').fetchall()
        if violations:
            raise RuntimeError(f'Foreign-key check failed during migration: {violations[:10]}')
        conn.commit()
        return current
    except BaseException:
        conn.rollback()
        raise
    finally:
        if foreign_keys_were_on:
            conn.execute('PRAGMA foreign_keys=ON')
