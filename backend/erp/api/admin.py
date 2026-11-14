import json
import os
import re
import sqlite3
import tempfile

from flask import Blueprint, after_this_request, current_app, g, jsonify, request, send_file

from .. import audit, db, security
from ..crud import serialize
from ..errors import ApiError
from ..security import require
from ..services import settings, tenancy, users
from ..util import today
from .common import body, paging

bp = Blueprint('admin', __name__, url_prefix='/api')
PERM_RE = re.compile(r'^(\*|\*\.(read|write|void)|[a-z_]+\.(\*|read|write|void|export|import|use|backup|manage))$')
VERSION = '6.0.0'


@bp.get('/health')
def health():
    return jsonify(status='ok', version=VERSION)


# ------------------------------------------------------------------ settings
@bp.get('/settings')
@require()
def get_settings():
    return jsonify(settings.get_settings())


@bp.put('/settings')
@require('settings.manage')
def put_settings():
    with db.transaction():
        changes = settings.save_settings(body())
        audit.log('update', 'settings', None, changes)
    return jsonify(settings.get_settings())


# ------------------------------------------------------------------ users & roles
def _require_single_company_for_global_admin_action():
    # Role definitions and the SQLite backup are global objects in the current schema.
    # Do not let a company administrator mutate/export them when tenant peers exist.
    if (db.scalar('SELECT COUNT(*) FROM companies') or 0) > 1:
        raise ApiError('platform_admin_required',
                       'This global administration action is disabled while multiple companies exist.', 503)


def _guard_global_user_fields(company_id, user_id, body_data):
    global_fields = {'name', 'is_active', 'password'}
    if global_fields.intersection(body_data):
        memberships = db.scalar('SELECT COUNT(*) FROM company_memberships WHERE user_id=?', (user_id,)) or 0
        if memberships > 1:
            raise ApiError('global_identity_change_requires_platform_admin',
                           'Name, activation and password are global identity fields. A platform administrator must change them for a multi-company user.',
                           403)


def _admin_guard():
    company_id = (g.get('company') or {}).get('id')
    if company_id is None:
        raise ApiError('active_company_required', 'An active company is required.', 409)
    if not db.scalar(
        """SELECT COUNT(*) FROM company_memberships m JOIN users u ON u.id=m.user_id
           WHERE m.company_id=? AND m.role='admin' AND m.status='active' AND u.is_active=1""",
        (company_id,),
    ):
        raise ApiError('last_admin', 'At least one active company administrator is required.', 409)


@bp.get('/users')
@require('users.manage')
def list_users():
    rows = db.q(
        '''SELECT u.id,u.email,u.name,m.role,u.is_active,u.last_login,u.created_at,m.status AS membership_status
           FROM company_memberships m JOIN users u ON u.id=m.user_id
           WHERE m.company_id=? ORDER BY u.id''',
        (g.company['id'],),
    )
    return jsonify(items=serialize(rows))


@bp.post('/users')
@require('users.manage')
def create_user():
    b = body()
    with db.transaction():
        role = b.get('role') or 'viewer'
        uid = users.create_user(b.get('email'), b.get('name'), b.get('password'), role)
        tenancy.add_membership(g.company['id'], uid, role, status='active', invited_by=g.user['id'])
        audit.log('create', 'user', uid, {'email': b.get('email'), 'role': role, 'company_id': g.company['id']})
    return jsonify(serialize(db.one('SELECT id,email,name,role,is_active,last_login,created_at FROM users WHERE id=?',
                                    (uid,)))), 201


@bp.put('/users/<int:uid>')
@require('users.manage')
def update_user(uid):
    b = body()
    with db.transaction():
        row = db.one('SELECT * FROM users WHERE id=?', (uid,))
        membership = db.one('SELECT * FROM company_memberships WHERE company_id=? AND user_id=?', (g.company['id'], uid))
        if not row or not membership:
            raise ApiError('not_found', 'Company member not found.', 404)
        _guard_global_user_fields(g.company['id'], uid, b)
        sets, params = [], []
        if 'name' in b:
            if not str(b['name']).strip():
                raise ApiError('validation', 'Name is required.', 422, details={'name': 'required'})
            sets.append('name=?'), params.append(str(b['name']).strip())
        if 'role' in b:
            if not db.one('SELECT name FROM roles WHERE name=?', (b['role'],)):
                raise ApiError('invalid_role', 'Unknown role.', 422)
            db.execute('UPDATE company_memberships SET role=? WHERE company_id=? AND user_id=?',
                       (b['role'], g.company['id'], uid))
        if 'is_active' in b:
            sets.append('is_active=?'), params.append(1 if b['is_active'] else 0)
        if b.get('password'):
            security.validate_password(b['password'], row['email'])
            sets.append('password_hash=?'), params.append(security.hash_password(b['password']))
        if sets:
            db.execute(f"UPDATE users SET {','.join(sets)} WHERE id=?", (*params, uid))
        _admin_guard()
        audit.log('update', 'user', uid, {k: v for k, v in b.items() if k != 'password'},
                  user=g.user)
    updated = db.one(
        '''SELECT u.id,u.email,u.name,m.role,u.is_active,u.last_login,u.created_at,m.status AS membership_status
           FROM company_memberships m JOIN users u ON u.id=m.user_id
           WHERE m.company_id=? AND m.user_id=?''',
        (g.company['id'], uid),
    )
    return jsonify(serialize(updated))


@bp.get('/roles')
@require('users.manage')
def list_roles():
    rows = db.q('SELECT name,label,permissions,is_system,(SELECT COUNT(*) FROM company_memberships m '
                'WHERE m.role=r.name AND m.company_id=?) AS users FROM roles r ORDER BY is_system DESC, name',
                (g.company['id'],))
    for r in rows:
        r['permissions'] = json.loads(r['permissions'])
    return jsonify(items=serialize(rows))


def _clean_perms(perms):
    if not isinstance(perms, list) or any(not isinstance(p, str) or not PERM_RE.match(p) for p in perms):
        raise ApiError('invalid_permissions', 'Invalid permission list.', 422)
    return sorted(set(perms))


@bp.post('/roles')
@require('users.manage')
def create_role():
    _require_single_company_for_global_admin_action()
    b = body()
    name = re.sub(r'[^a-z0-9_]', '', str(b.get('name') or '').lower())[:30]
    if not name or not str(b.get('label') or '').strip():
        raise ApiError('validation', 'Name and label are required.', 422)
    with db.transaction():
        db.execute('INSERT INTO roles(name,label,permissions) VALUES(?,?,?)',
                   (name, str(b['label']).strip()[:60], json.dumps(_clean_perms(b.get('permissions') or []))))
        audit.log('create', 'role', name)
    return jsonify(ok=True, name=name), 201


@bp.put('/roles/<name>')
@require('users.manage')
def update_role(name):
    _require_single_company_for_global_admin_action()
    b = body()
    if name == 'admin':
        raise ApiError('system_role', 'The administrator role cannot be changed.', 409)
    with db.transaction():
        if not db.one('SELECT name FROM roles WHERE name=?', (name,)):
            raise ApiError('not_found', 'Role not found.', 404)
        perms = _clean_perms(b.get('permissions'))
        db.execute('UPDATE roles SET permissions=? WHERE name=?', (json.dumps(perms), name))
        audit.log('update', 'role', name, {'permissions': perms})
    return jsonify(ok=True)


@bp.delete('/roles/<name>')
@require('users.manage')
def delete_role(name):
    _require_single_company_for_global_admin_action()
    with db.transaction():
        row = db.one('SELECT * FROM roles WHERE name=?', (name,))
        if not row:
            raise ApiError('not_found', 'Role not found.', 404)
        if row['is_system'] or db.scalar('SELECT COUNT(*) FROM users WHERE role=?', (name,)) or \
                db.scalar('SELECT COUNT(*) FROM company_memberships WHERE role=?', (name,)):
            raise ApiError('in_use', 'Built-in or assigned roles cannot be deleted.', 409)
        db.execute('DELETE FROM roles WHERE name=?', (name,))
        audit.log('delete', 'role', name)
    return jsonify(ok=True)


# ------------------------------------------------------------------ audit log
@bp.get('/audit')
@require('audit.read')
def audit_list():
    args = request.args
    company_id = int(g.company['id'])
    where, params = ['company_id=?'], [company_id]
    for key, col in (('entity', 'entity'), ('action', 'action'), ('user', 'user_email')):
        if args.get(key):
            where.append(f'{col}=?'), params.append(args[key])
    if args.get('q'):
        where.append('(details LIKE ? OR user_email LIKE ? OR entity LIKE ?)')
        params += [f"%{args['q']}%"] * 3
    if args.get('date_from'):
        where.append('ts >= ?'), params.append(args['date_from'])
    if args.get('date_to'):
        where.append('ts <= ?'), params.append(args['date_to'] + ' 23:59:59')
    page, per, offset = paging()
    sql = ' AND '.join(where)
    total = db.scalar(f'SELECT COUNT(*) FROM audit_log WHERE {sql}', params)
    rows = db.q(f'SELECT * FROM audit_log WHERE {sql} ORDER BY id DESC LIMIT ? OFFSET ?', (*params, per, offset))
    return jsonify(items=rows, total=total, page=page, per_page=per)


# ------------------------------------------------------------------ system
@bp.get('/system/info')
@require('settings.manage')
def system_info():
    path = current_app.config['DB_PATH']
    company_id = int(g.company['id'])
    company_count = db.scalar('SELECT COUNT(*) FROM companies') or 0
    counts = {
        'users': db.scalar('SELECT COUNT(*) FROM company_memberships WHERE company_id=? AND status=\'active\'',
                           (company_id,)),
        'products': db.scalar('SELECT COUNT(*) FROM products WHERE company_id=?', (company_id,)),
        'trade_documents': db.scalar('SELECT COUNT(*) FROM trade_documents WHERE company_id=?', (company_id,)),
        'journal_entries': db.scalar('SELECT COUNT(*) FROM journal_entries WHERE company_id=?', (company_id,)),
        'stock_moves': db.scalar('SELECT COUNT(*) FROM stock_moves WHERE company_id=?', (company_id,)),
        'audit_log': db.scalar('SELECT COUNT(*) FROM audit_log WHERE company_id=?', (company_id,)),
    }
    from ..seed import demo_allowed
    # Database size and demo eligibility are global facts; do not expose them to a tenant when peers exist.
    db_size = os.path.getsize(path) if company_count <= 1 and os.path.exists(path) else None
    can_seed_demo = bool(demo_allowed()) if company_count == 1 else False
    return jsonify(version=VERSION, db_size=db_size, counts=counts, demo_allowed=can_seed_demo)


@bp.post('/system/demo-data')
@require('settings.manage')
def demo_data():
    _require_single_company_for_global_admin_action()
    from ..seed import demo_allowed, seed_demo
    with db.transaction():
        if not demo_allowed():
            raise ApiError('demo_not_allowed', 'Demo data can only be loaded into an empty company.', 409)
        seed_demo()
        audit.log('demo_data', 'system')
    return jsonify(ok=True)


@bp.get('/system/backup')
@require('system.backup')
def backup():
    # SQLite's native backup copies the entire database, not a tenant-filtered snapshot.
    # Until a tenant-aware backup/export format exists, deny this route in multi-company mode.
    if (db.scalar('SELECT COUNT(*) FROM companies') or 0) > 1:
        raise ApiError('tenant_backup_unavailable',
                       'A company-scoped backup is not available while multiple companies are active.', 503)
    # The native SQLite backup excludes separately stored file bytes. Refuse a
    # misleading/incomplete backup until a DB+file archive/restore workflow ships.
    if db.scalar('SELECT COUNT(*) FROM file_objects'):
        raise ApiError('file_backup_unavailable',
                       'Attachment-aware backup is not available yet. No incomplete backup was created.', 503)
    tmp = tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False)
    tmp.close()
    dst = sqlite3.connect(tmp.name)
    db.get_db().backup(dst)
    dst.close()
    audit.log('backup', 'system')

    @after_this_request
    def _cleanup(resp):
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return resp
    return send_file(tmp.name, as_attachment=True, download_name=f'erp-backup-{today()}.sqlite3')


def register(app):
    app.register_blueprint(bp)
