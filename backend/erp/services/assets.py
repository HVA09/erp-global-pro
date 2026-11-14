"""Fixed assets with straight-line depreciation posted to the general ledger."""
from .. import audit, db
from ..errors import ApiError
from ..money import round_half_up, to_cents
from ..util import month_end, parse_date, today, valid_period
from . import accounting, settings


def create_asset(data):
    name = (data.get('name') or '').strip()
    if not name:
        raise ApiError('validation', 'Name is required.', 422, details={'name': 'required'})
    cost = to_cents(data.get('cost'))
    salvage = to_cents(data.get('salvage'))
    life = int(data.get('useful_life_months') or 0)
    if cost <= 0 or life <= 0 or salvage < 0 or salvage >= cost:
        raise ApiError('invalid_value', 'Check cost, salvage value and useful life.', 422)
    date = parse_date(data.get('purchase_date') or today(), 'purchase_date')
    settings.check_period_open(date)
    aid = db.execute('INSERT INTO fixed_assets(name,purchase_date,cost_cents,salvage_cents,useful_life_months,notes) '
                     'VALUES(?,?,?,?,?,?)', (name, date, cost, salvage, life, data.get('notes')))
    credit = accounting.cash_account(int(data['account_id'])) if data.get('account_id') else 'opening'
    jid = accounting.post_entry(date, f'Fixed asset acquisition: {name}', [
        {'account': 'fixed_assets', 'debit': cost, 'description': name}, {'account': credit, 'credit': cost}],
        'asset', aid)
    db.execute('UPDATE fixed_assets SET entry_id=? WHERE id=?', (jid, aid))
    audit.log('create', 'fixed_asset', aid, {'name': name, 'cost': cost / 100})
    return aid


def list_assets():
    rows = db.q('SELECT * FROM fixed_assets ORDER BY id DESC')
    for r in rows:
        r['book_value_cents'] = r['cost_cents'] - r['accumulated_cents']
        r['monthly_cents'] = round_half_up((r['cost_cents'] - r['salvage_cents']) / r['useful_life_months'])
    return rows


def depreciate(asset_id, period):
    period = valid_period(period)
    a = db.one('SELECT * FROM fixed_assets WHERE id=?', (asset_id,))
    if not a:
        raise ApiError('not_found', 'Asset not found.', 404)
    if period < a['purchase_date'][:7]:
        raise ApiError('before_purchase', 'Cannot depreciate before the purchase month.', 422)
    if db.scalar('SELECT COUNT(*) FROM depreciation_postings WHERE asset_id=? AND period=?', (asset_id, period)):
        raise ApiError('already_depreciated', f'{period} is already depreciated for this asset.', 409)
    remaining = a['cost_cents'] - a['salvage_cents'] - a['accumulated_cents']
    done = db.scalar('SELECT COUNT(*) FROM depreciation_postings WHERE asset_id=?', (asset_id,))
    monthly = round_half_up((a['cost_cents'] - a['salvage_cents']) / a['useful_life_months'])
    amount = remaining if done + 1 >= a['useful_life_months'] else min(monthly, remaining)
    if amount <= 0:
        raise ApiError('fully_depreciated', 'This asset is fully depreciated.', 409)
    date = month_end(period)
    jid = accounting.post_entry(date, f"Depreciation {period}: {a['name']}", [
        {'account': 'depreciation', 'debit': amount, 'description': a['name']},
        {'account': 'accum_depr', 'credit': amount, 'description': a['name']}], 'depreciation', asset_id)
    db.execute('INSERT INTO depreciation_postings(asset_id,period,date,amount_cents,entry_id) VALUES(?,?,?,?,?)',
               (asset_id, period, date, amount, jid))
    new_acc = a['accumulated_cents'] + amount
    status = 'fully_depreciated' if new_acc >= a['cost_cents'] - a['salvage_cents'] else 'active'
    db.execute('UPDATE fixed_assets SET accumulated_cents=?, status=? WHERE id=?', (new_acc, status, asset_id))
    return amount


def depreciate_all(period):
    period = valid_period(period)
    done, skipped = 0, 0
    for a in db.q("SELECT id FROM fixed_assets WHERE status='active' AND substr(purchase_date,1,7) <= ?", (period,)):
        try:
            with db.transaction():
                depreciate(a['id'], period)
            done += 1
        except ApiError:
            skipped += 1
    return {'posted': done, 'skipped': skipped}
