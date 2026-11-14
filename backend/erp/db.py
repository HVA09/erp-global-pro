"""SQLite access: one connection per request, explicit transactions with savepoint nesting."""
from contextlib import contextmanager
from pathlib import Path

from .db_adapters import SQLITE_ADAPTER

from flask import current_app, g

SCHEMA_PATH = Path(__file__).with_name('schema.sql')
SCHEMA_VERSION = 1  # Base schema; forward changes are applied by erp.migrations.


def connect(path):
    return SQLITE_ADAPTER.connect(path, isolation_level=None, timeout=30, check_same_thread=False)


def get_db():
    db = g.get('_db')
    if db is None:
        db = g._db = connect(current_app.config['DB_PATH'])
        g._tx_depth = 0
    return db


def close_db(exc=None):
    db = g.pop('_db', None)
    if db is not None:
        try:
            if SQLITE_ADAPTER.in_transaction(db):
                SQLITE_ADAPTER.rollback(db)
        finally:
            SQLITE_ADAPTER.close(db)


@contextmanager
def transaction():
    """BEGIN IMMEDIATE at the outermost level (serialises writers), SAVEPOINT when nested."""
    db = get_db()
    depth = g.get('_tx_depth', 0)
    if depth == 0:
        SQLITE_ADAPTER.begin(db, write=True)
    else:
        SQLITE_ADAPTER.execute(db, f'SAVEPOINT sp{depth}')
    g._tx_depth = depth + 1
    try:
        yield db
    except BaseException:
        g._tx_depth = depth
        if depth == 0:
            SQLITE_ADAPTER.rollback(db)
        else:
            SQLITE_ADAPTER.execute(db, f'ROLLBACK TO sp{depth}')
            SQLITE_ADAPTER.execute(db, f'RELEASE sp{depth}')
        raise
    else:
        g._tx_depth = depth
        if depth == 0:
            SQLITE_ADAPTER.commit(db)
        else:
            SQLITE_ADAPTER.execute(db, f'RELEASE sp{depth}')


def q(sql, params=()):
    return [dict(r) for r in SQLITE_ADAPTER.execute(get_db(), sql, params).fetchall()]


def one(sql, params=()):
    r = SQLITE_ADAPTER.execute(get_db(), sql, params).fetchone()
    return dict(r) if r else None


def scalar(sql, params=()):
    r = SQLITE_ADAPTER.execute(get_db(), sql, params).fetchone()
    return r[0] if r else None


def execute(sql, params=()):
    return SQLITE_ADAPTER.last_insert_id(SQLITE_ADAPTER.execute(get_db(), sql, params))


def run(sql, params=()):
    return SQLITE_ADAPTER.execute(get_db(), sql, params)


_cols_cache = {}


def table_columns(table):
    if table not in _cols_cache:
        _cols_cache[table] = SQLITE_ADAPTER.table_columns(get_db(), table)
    return _cols_cache[table]


def init_db(app):
    path = Path(app.config['DB_PATH'])
    path.parent.mkdir(parents=True, exist_ok=True)
    with app.app_context():
        db = get_db()
        SQLITE_ADAPTER.executescript(db, SCHEMA_PATH.read_text(encoding='utf-8'))
        if scalar('SELECT COUNT(*) FROM schema_version') == 0:
            execute('INSERT INTO schema_version(version) VALUES(?)', (SCHEMA_VERSION,))
        from .migrations import apply_migrations
        apply_migrations(db)
        from .seed import ensure_base_data
        with transaction():
            ensure_base_data()
        close_db()
