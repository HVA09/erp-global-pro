"""Sales (invoices, credit notes), purchases (bills, debit notes), payments, credit application and POS."""
from flask import Blueprint, jsonify, request, g

from .. import audit, db
from ..crud import serialize
from ..errors import ApiError
from ..money import to_cents
from ..security import require, require_any
from ..services import settings, trade, zatca
from .common import body

bp = Blueprint('trade', __name__, url_prefix='/api')

DOCS = [  # (doc_type, url segment, permission module)
    ('invoice', 'sales/invoices', 'sales'), ('credit_note', 'sales/credit-notes', 'sales'),
    ('bill', 'purchases/bills', 'purchases'), ('debit_note', 'purchases/debit-notes', 'purchases'),
]


def _add_doc_routes(doc_type, path, perm):
    tag = doc_type
    read, write = require(f'{perm}.read'), require(f'{perm}.write')
    void = require(f'{perm}.void')
    pay = require_any(f'{perm}.write', 'payments.write')

    def list_():
        res = trade.list_docs(doc_type, request.args)
        return jsonify(serialize(res))

    def get_(did):
        return jsonify(serialize(trade.get_doc(did, doc_type)))

    def create_():
        b = body()
        with db.transaction():
            did = trade.save_draft(doc_type, b)
            audit.log('create', doc_type, did)
            if b.get('post'):
                trade.post(did, doc_type)
            doc = trade.get_doc(did)
        return jsonify(serialize(doc)), 201

    def update_(did):
        with db.transaction():
            trade.save_draft(doc_type, body(), did)
            audit.log('update', doc_type, did)
            doc = trade.get_doc(did)
        return jsonify(serialize(doc))

    def delete_(did):
        with db.transaction():
            trade.delete_draft(did, doc_type)
            audit.log('delete', doc_type, did)
        return jsonify(ok=True)

    def post_(did):
        with db.transaction():
            trade.post(did, doc_type)
            doc = trade.get_doc(did)
        return jsonify(serialize(doc))

    def cancel_(did):
        with db.transaction():
            trade.cancel(did, doc_type)
            doc = trade.get_doc(did)
        return jsonify(serialize(doc))

    def payment_(did):
        b = body()
        with db.transaction():
            trade.get_doc(did, doc_type)
            trade.add_payment(did, to_cents(b.get('amount')), b.get('account_id'), b.get('date'), b.get('method'),
                              b.get('reference'), b.get('note'))
            doc = trade.get_doc(did)
        return jsonify(serialize(doc)), 201

    def return_(did):
        with db.transaction():
            new_id = trade.create_return_from(did, body().get('lines'))
            audit.log('create_return', doc_type, did, {'return_id': new_id})
            doc = trade.get_doc(new_id)
        return jsonify(serialize(doc)), 201

    def qr_(did):
        doc = trade.get_doc(did, doc_type)
        if doc['status'] != 'posted':
            raise ApiError('not_posted', 'Only posted documents have a QR code.', 409)
        s = settings.get_settings()
        stamp = (doc['posted_at'] or doc['date']).replace(' ', 'T')
        stamp = stamp if 'T' in stamp else stamp + 'T00:00:00'
        payload = zatca.payload(s['company'], s['tax_number'], stamp + 'Z', doc['total_cents'] / 100,
                                doc['tax_cents'] / 100)
        return jsonify(payload=payload, svg=zatca.qr_svg(payload))

    base = f'/{path}'
    bp.add_url_rule(base, f'{tag}_list', read(list_), methods=['GET'])
    bp.add_url_rule(base, f'{tag}_create', write(create_), methods=['POST'])
    bp.add_url_rule(f'{base}/<int:did>', f'{tag}_get', read(get_), methods=['GET'])
    bp.add_url_rule(f'{base}/<int:did>', f'{tag}_update', write(update_), methods=['PUT'])
    bp.add_url_rule(f'{base}/<int:did>', f'{tag}_delete', write(delete_), methods=['DELETE'])
    bp.add_url_rule(f'{base}/<int:did>/post', f'{tag}_post', write(post_), methods=['POST'])
    bp.add_url_rule(f'{base}/<int:did>/cancel', f'{tag}_cancel', void(cancel_), methods=['POST'])
    bp.add_url_rule(f'{base}/<int:did>/payments', f'{tag}_pay', pay(payment_), methods=['POST'])
    bp.add_url_rule(f'{base}/<int:did>/qr', f'{tag}_qr', read(qr_), methods=['GET'])
    if doc_type in trade.RETURN_TYPE:
        bp.add_url_rule(f'{base}/<int:did>/return', f'{tag}_return', write(return_), methods=['POST'])


for _t, _p, _m in DOCS:
    _add_doc_routes(_t, _p, _m)


# ------------------------------------------------------------------ payments & credit
@bp.get('/payments')
@require_any('payments.read', 'sales.read', 'purchases.read')
def payments():
    return jsonify(serialize(trade.list_payments(request.args)))


@bp.post('/payments/<int:pid>/cancel')
@require_any('payments.write', 'sales.void', 'purchases.void')
def cancel_payment(pid):
    with db.transaction():
        trade.cancel_payment(pid)
    return jsonify(ok=True)


@bp.post('/credit-applications')
@require_any('payments.write', 'sales.write', 'purchases.write')
def apply_credit():
    b = body()
    with db.transaction():
        aid = trade.apply_credit(int(b.get('credit_id') or 0), int(b.get('target_id') or 0),
                                 to_cents(b.get('amount')), b.get('date'))
    return jsonify(id=aid), 201


@bp.delete('/credit-applications/<int:aid>')
@require_any('payments.write', 'sales.void', 'purchases.void')
def undo_credit(aid):
    with db.transaction():
        trade.undo_credit_application(aid)
    return jsonify(ok=True)


@bp.get('/payment-accounts')
@require()
def payment_accounts():
    """Active cash/bank accounts (any signed-in user may need them to take a payment)."""
    return jsonify(items=db.q("SELECT id, code, name, subtype FROM accounts WHERE company_id=? "
                              "AND subtype IN ('cash','bank') AND is_active=1 ORDER BY code",
                              ((g.get('company') or {}).get('id'),)))


@bp.post('/pos/checkout')
@require('pos.use')
def pos_checkout():
    with db.transaction():
        did = trade.pos_checkout(body())
        audit.log('pos_sale', 'invoice', did)
        doc = trade.get_doc(did)
    return jsonify(serialize(doc)), 201


def register(app):
    app.register_blueprint(bp)
