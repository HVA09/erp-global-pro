"""Company settings (key/value) and period locking."""
import json

from flask import g

from .. import db
from ..errors import ApiError

from . import tenancy

DEFAULTS = {
    'company': 'My Company', 'currency': 'USD', 'tax': 15.0, 'address': '', 'country': '', 'phone': '',
    'email': '', 'tax_number': '', 'cr_number': '', 'invoice_prefix': 'INV-', 'receipt_width': '80mm',
    'fiscal_year_start': '01-01', 'lock_date': '', 'allow_negative_stock': False,
    'default_warehouse_id': None, 'language': '',
}
_TYPES = {'tax': float, 'allow_negative_stock': bool, 'default_warehouse_id': (int, type(None))}


def get_settings(company_id=None):
    cid = tenancy.current_company_id(company_id)
    cache = g.setdefault('_settings_by_company', {})
    if cid not in cache:
        values = dict(DEFAULTS)
        for row in db.q('SELECT key, value FROM settings WHERE company_id=?', (cid,)):
            if row['key'] in DEFAULTS:
                values[row['key']] = json.loads(row['value'])
        cache[cid] = values
    return cache[cid]


def save_settings(data, company_id=None):
    cid = tenancy.current_company_id(company_id)
    changes = {}
    for key, value in data.items():
        if key not in DEFAULTS:
            continue
        if key in _TYPES:
            expected = _TYPES[key]
            try:
                if expected is float:
                    value = float(value)
                    if not 0 <= value <= 100:
                        raise ValueError
                elif expected is bool:
                    value = bool(value)
                elif value in (None, ''):
                    value = None
                else:
                    value = int(value)
            except (TypeError, ValueError):
                raise ApiError('invalid_value', f'Invalid value for {key}', 422, details={key: 'invalid'})
        elif key == 'lock_date':
            from ..util import parse_date
            value = parse_date(value, key, required=False) or ''
        else:
            value = str(value or '').strip()[:200]
        changes[key] = value

    if 'default_warehouse_id' in changes and changes['default_warehouse_id'] is not None:
        if not db.one('SELECT id FROM warehouses WHERE id=? AND company_id=? AND is_active=1',
                      (changes['default_warehouse_id'], cid)):
            raise ApiError('invalid_warehouse', 'Default warehouse must belong to the active company.', 422)

    for key, value in changes.items():
        db.execute(
            'INSERT INTO settings(company_id,key,value) VALUES(?,?,?) '
            'ON CONFLICT(company_id,key) DO UPDATE SET value=excluded.value',
            (cid, key, json.dumps(value, ensure_ascii=False)),
        )
    if 'invoice_prefix' in changes and changes['invoice_prefix']:
        # Sequences remain globally unique in the current schema. The sequence
        # service will reject a foreign-company row until Stage 10 migrates its key.
        db.execute("UPDATE sequences SET prefix=? WHERE name='invoice' AND company_id=?",
                   (changes['invoice_prefix'], cid))
    g.setdefault('_settings_by_company', {}).pop(cid, None)
    return changes

def check_period_open(date_str, company_id=None):
    cid = tenancy.current_company_id(company_id)
    lock = get_settings(cid).get('lock_date')
    if lock and date_str <= lock:
        raise ApiError('period_locked', f'Accounting period is locked up to {lock}.', 409, params={'lock_date': lock})


def default_warehouse_id(company_id=None):
    cid = tenancy.current_company_id(company_id)
    wid = get_settings(cid).get('default_warehouse_id')
    if wid and db.one('SELECT id FROM warehouses WHERE id=? AND company_id=? AND is_active=1', (wid, cid)):
        return wid
    row = db.one('SELECT id FROM warehouses WHERE company_id=? AND is_active=1 ORDER BY id LIMIT 1', (cid,))
    return row['id'] if row else None
