from flask import Blueprint, g, jsonify, request

from ..crud import serialize
from ..security import require
from ..services import reports as rep, trade
from ..util import today
from .common import date_arg, int_arg

bp = Blueprint('reports', __name__, url_prefix='/api/reports')


def _range():
    t = today()
    return date_arg('from', t[:8] + '01'), date_arg('to', t)


def _company_id():
    company = g.get('company') or {}
    return int(company['id'])


@bp.get('/dashboard')
@require('dashboard.read')
def dashboard():
    return jsonify(serialize(rep.dashboard(_company_id())))


@bp.get('/sales-summary')
@require('reports.read')
def sales_summary():
    f, t = _range()
    group = 'month' if request.args.get('group') == 'month' else 'day'
    return jsonify(serialize(rep.sales_summary(f, t, group, _company_id())))


@bp.get('/top-products')
@require('reports.read')
def top_products():
    f, t = _range()
    return jsonify(items=serialize(rep.top_products(f, t, int_arg('limit', 10), _company_id())))


@bp.get('/top-customers')
@require('reports.read')
def top_customers():
    f, t = _range()
    return jsonify(items=serialize(rep.top_customers(f, t, int_arg('limit', 10), _company_id())))


@bp.get('/profitability')
@require('reports.read')
def profitability():
    f, t = _range()
    return jsonify(serialize(rep.profitability(f, t, _company_id())))


@bp.get('/forecast')
@require('reports.read')
def forecast():
    return jsonify(serialize(rep.forecast(_company_id()) or {}))


@bp.get('/reorder')
@require('reports.read')
def reorder():
    return jsonify(items=rep.reorder_suggestions(_company_id()))


@bp.get('/statement')
@require('reports.read')
def statement():
    side = request.args.get('side')
    return jsonify(serialize(trade.partner_statement(
        int_arg('partner_id', 0), side if side in ('customer', 'supplier') else None,
        date_arg('from'), date_arg('to'), _company_id())))


@bp.get('/ageing')
@require('reports.read')
def ageing():
    side = 'supplier' if request.args.get('side') == 'supplier' else 'customer'
    return jsonify(serialize(trade.ageing(side, date_arg('as_of'), _company_id())))


def register(app):
    app.register_blueprint(bp)
