from flask import Blueprint, current_app, g, jsonify

from .. import audit, db, security
from ..errors import ApiError
from ..security import require
from ..services import settings, tenancy, users
from .common import body

bp = Blueprint('auth', __name__, url_prefix='/api/auth')


def user_payload(row):
    return {'id': row['id'], 'email': row['email'], 'name': row['name'], 'role': row['role'],
            'permissions': row.get('permissions') or [], 'active_company': row.get('active_company')}


def _user_with_default_membership(row):
    membership = tenancy.get_default_active_membership(row['id'])
    if not membership:
        import json
        role_permissions = db.scalar('SELECT permissions FROM roles WHERE name=?', (row['role'],)) or '[]'
        permissions = json.loads(role_permissions)
        return {**row, 'permissions': permissions, 'active_company': None}
    import json
    return {
        **row,
        'role': membership['role'],
        'permissions': json.loads(membership['permissions']),
        'active_company': tenancy.membership_company_payload(membership),
    }


@bp.get('/setup-status')
def setup_status():
    return jsonify(needs_setup=db.scalar('SELECT COUNT(*) FROM users') == 0)


@bp.post('/setup')
def setup():
    """First run only: create the administrator and (optionally) a demo company."""
    b = body()
    with db.transaction():
        if db.scalar('SELECT COUNT(*) FROM users'):
            raise ApiError('already_setup', 'The system is already set up.', 409)
        uid = users.create_user(b.get('email'), b.get('name'), b.get('password'), 'admin')
        # Stage 3 creates one bootstrap company before base data is seeded. Reuse
        # that company during first-run setup instead of creating a second tenant.
        companies = db.q('SELECT id FROM companies ORDER BY id')
        if len(companies) != 1:
            raise ApiError('setup_company_conflict',
                           'Initial setup requires exactly one bootstrap company.', 409)
        company_id = companies[0]['id']
        # Bind the bootstrap request to a verified owner membership before calling
        # tenant-aware settings/services. Setup is public by design, so no JWT
        # company context exists yet; this is the only trusted bootstrap path.
        tenancy.add_membership(company_id, uid, 'admin', status='active')
        membership = tenancy.get_active_membership(uid, company_id=company_id)
        if membership is None:
            raise ApiError('setup_membership_failed', 'Could not establish the initial company membership.', 500)
        g.company = tenancy.membership_company_payload(membership)
        g.membership = membership
        settings.save_settings({k: b[k] for k in ('company', 'currency', 'tax', 'country') if b.get(k) not in (None, '')},
                               company_id=company_id)
        configured = settings.get_settings(company_id=company_id)
        db.execute(
            "UPDATE companies SET name=?, country=?, currency=?, updated_at=datetime('now') WHERE id=?",
            (configured.get('company') or 'My Company', configured.get('country') or '',
             configured.get('currency') or 'USD', company_id),
        )
        row = db.one('SELECT u.id,u.email,u.name,u.role,r.permissions FROM users u JOIN roles r ON r.name=u.role '
                     'WHERE u.id=?', (uid,))
        import json
        row['permissions'] = json.loads(row['permissions'])
        audit.log('setup', 'user', uid, {'email': row['email']}, user=row)
        if b.get('demo'):
            from ..seed import seed_demo
            g.user = row
            seed_demo()
            g.user = None
    session_user = _user_with_default_membership(row)
    return jsonify(token=security.make_token(row), user=user_payload(session_user)), 201


@bp.post('/login')
def login():
    b = body()
    row = security.authenticate(b.get('email'), b.get('password'))
    session_user = _user_with_default_membership(row)
    return jsonify(token=security.make_token(row), user=user_payload(session_user))


@bp.get('/me')
@require()
def me():
    return jsonify(user=user_payload(g.user))


@bp.get('/companies')
@require()
def companies():
    items = tenancy.list_user_memberships(g.user['id'], active_only=True)
    company_count = db.scalar('SELECT COUNT(*) FROM companies') or 0
    isolation_ready = bool(current_app.config.get('TENANT_DATA_SCOPING_ENABLED'))
    can_use_operations = company_count <= 1 or isolation_ready
    return jsonify(
        items=items,
        active_company=g.company,
        company_count=company_count,
        can_use_operations=can_use_operations,
    )


@bp.post('/switch-company')
@require()
def switch_company():
    b = body()
    raw_company_id = b.get('company_id')
    if isinstance(raw_company_id, bool) or (isinstance(raw_company_id, float) and not raw_company_id.is_integer()):
        raise ApiError('validation', 'A valid company_id is required.', 422)
    try:
        company_id = int(raw_company_id)
    except (TypeError, ValueError):
        raise ApiError('validation', 'A valid company_id is required.', 422)
    if company_id <= 0:
        raise ApiError('validation', 'A valid company_id is required.', 422)

    membership = tenancy.get_active_membership(g.user['id'], company_id=company_id)
    if membership is None:
        raise ApiError('company_access_denied', 'You do not have an active membership in this company.', 403)

    import json
    session_user = {
        **g.user,
        'role': membership['role'],
        'permissions': json.loads(membership['permissions']),
        'active_company': tenancy.membership_company_payload(membership),
        'membership_id': membership['membership_id'],
    }
    token = security.make_token(g.user, company_id=company_id)
    audit.log('switch_company', 'company', company_id, {'membership_id': membership['membership_id']}, user=g.user)
    return jsonify(token=token, user=user_payload(session_user), active_company=session_user['active_company'])


@bp.post('/change-password')
@require()
def change_password():
    b = body()
    row = db.one('SELECT * FROM users WHERE id=?', (g.user['id'],))
    if not security.verify_password(b.get('current_password') or '', row['password_hash']):
        raise ApiError('invalid_credentials', 'Current password is incorrect.', 401)
    security.validate_password(b.get('new_password'), row['email'])
    with db.transaction():
        db.execute('UPDATE users SET password_hash=? WHERE id=?', (security.hash_password(b['new_password']), row['id']))
        audit.log('change_password', 'user', row['id'])
    return jsonify(ok=True)


def register(app):
    app.register_blueprint(bp)
