"""Gap-free document numbering (call inside a transaction)."""
from .. import db
from . import tenancy

DEFAULT_SEQUENCES = {
    'invoice': ('INV-', 6), 'credit_note': ('CN-', 6), 'bill': ('BILL-', 6), 'debit_note': ('DN-', 6),
    'payment_in': ('RCT-', 6), 'payment_out': ('PAY-', 6), 'journal': ('JE-', 6),
    'production': ('MO-', 6), 'stock_count': ('SC-', 5), 'payroll': ('PR-', 5),
}


def next_number(name, company_id=None):
    cid = tenancy.current_company_id(company_id)
    row = db.one('SELECT prefix, next_number, padding FROM sequences WHERE name=? AND company_id=?',
                 (name, cid))
    if row is None:
        prefix, padding = DEFAULT_SEQUENCES.get(name, (name.upper() + '-', 6))
        db.execute('INSERT INTO sequences(company_id,name,prefix,next_number,padding) VALUES(?,?,?,1,?)',
                   (cid, name, prefix, padding))
        row = {'prefix': prefix, 'next_number': 1, 'padding': padding}
    db.execute('UPDATE sequences SET next_number = next_number + 1 WHERE name=? AND company_id=?', (name, cid))
    return f"{row['prefix']}{row['next_number']:0{row['padding']}d}"
