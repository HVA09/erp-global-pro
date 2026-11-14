"""Passwords (scrypt), JWT tokens, role based permissions and login throttling."""
import base64
import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import current_app, g, request

from . import audit, db
from .errors import ApiError

# --------------------------------------------------------------------------- passwords
_N, _R, _P = 2 ** 14, 8, 1
_dummy_hash = None


def hash_password(password):
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return 'scrypt$%d$%d$%d$%s$%s' % (_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password, stored):
    try:
        _, n, r, p, salt, dk = stored.split('$')
        expected = base64.b64decode(dk)
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(expected))
        return hmac.compare_digest(calc, expected)
    except Exception:
        return False


def validate_password(password, email=''):
    if not isinstance(password, str) or len(password) < 8:
        raise ApiError('weak_password', 'Password must be at least 8 characters.', 422)
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        raise ApiError('weak_password', 'Password must contain letters and digits.', 422)
    if email and password.lower() == email.lower():
        raise ApiError('weak_password', 'Password must differ from the email.', 422)


# --------------------------------------------------------------------------- tokens
def make_token(user, company_id=None):
    """Issue a short-lived token bound to a verified active membership when available."""
    from .services import tenancy

    membership = (tenancy.get_active_membership(user['id'], company_id=company_id)
                  if company_id is not None else tenancy.get_default_active_membership(user['id']))
    if company_id is not None and membership is None:
        raise ApiError('company_access_denied', 'You do not have an active membership in this company.', 403)
    now = datetime.now(timezone.utc)
    payload = {'sub': str(user['id']), 'iat': now,
               'exp': now + timedelta(hours=current_app.config['TOKEN_TTL_HOURS'])}
    if membership:
        payload['cid'] = int(membership['company_id'])
        payload['mid'] = int(membership['membership_id'])
    return jwt.encode(payload, current_app.config['SECRET_KEY'], algorithm='HS256')


def load_user():
    """Load identity and revalidate its active company membership on every request."""
    g.user = None
    g.company = None
    g.membership = None
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        return
    try:
        payload = jwt.decode(header[7:].strip(), current_app.config['SECRET_KEY'], algorithms=['HS256'])
        uid = int(payload['sub'])
        company_id = int(payload['cid']) if 'cid' in payload else None
        membership_id = int(payload['mid']) if 'mid' in payload else None
        if (company_id is None) != (membership_id is None):
            return
    except Exception:
        return

    row = db.one('SELECT u.id,u.email,u.name,u.role,u.is_active,r.permissions FROM users u '
                 'JOIN roles r ON r.name=u.role WHERE u.id=?', (uid,))
    if not row or not row['is_active']:
        return

    from .services import tenancy
    if company_id is not None:
        membership = tenancy.get_active_membership(uid, company_id, membership_id)
        # A revoked membership or suspended company invalidates this company-bound token.
        if membership is None:
            return
    else:
        # Backward-compatible tokens are safe only when membership resolution is unambiguous.
        membership = tenancy.get_default_active_membership(uid)

    if membership:
        row['role'] = membership['role']
        row['permissions'] = json.loads(membership['permissions'])
        row['active_company'] = tenancy.membership_company_payload(membership)
        row['membership_id'] = membership['membership_id']
        g.company = row['active_company']
        g.membership = membership
    else:
        row['permissions'] = json.loads(row['permissions'])
        row['active_company'] = None
    g.user = row


# --------------------------------------------------------------------------- permissions
def has_perm(perms, needed):
    """Patterns understood: '*', 'module.*', '*.action', exact 'module.action'."""
    if '*' in perms or needed in perms:
        return True
    module, _, action = needed.partition('.')
    return f'{module}.*' in perms or f'*.{action}' in perms


def require(*needed):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            user = g.get('user')
            if not user:
                raise ApiError('unauthorized', 'Authentication required.', 401)
            for perm in needed:
                if not has_perm(user['permissions'], perm):
                    raise ApiError('forbidden', f'Missing permission: {perm}', 403, params={'permission': perm})
            return fn(*a, **kw)
        return wrapper
    return deco


def require_any(*needed):
    """Allow the request when the user holds at least one of the listed permissions."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            user = g.get('user')
            if not user:
                raise ApiError('unauthorized', 'Authentication required.', 401)
            if not any(has_perm(user['permissions'], p) for p in needed):
                raise ApiError('forbidden', f'Missing permission: {needed[0]}', 403, params={'permission': needed[0]})
            return fn(*a, **kw)
        return wrapper
    return deco


def can(perm):
    user = g.get('user')
    return bool(user and has_perm(user['permissions'], perm))


# --------------------------------------------------------------------------- login throttling
_failures = {}
_lock = threading.Lock()


def _throttle_key(email):
    return (request.remote_addr or '', (email or '').lower())


def _check_throttle(email):
    cfg = current_app.config
    with _lock:
        now = time.time()
        recent = [t for t in _failures.get(_throttle_key(email), []) if now - t < cfg['LOGIN_WINDOW_SECONDS']]
        _failures[_throttle_key(email)] = recent
        if len(recent) >= cfg['LOGIN_MAX_FAILURES']:
            raise ApiError('too_many_attempts', 'Too many failed logins. Try again later.', 429)


def _record_failure(email):
    with _lock:
        _failures.setdefault(_throttle_key(email), []).append(time.time())


def authenticate(email, password):
    global _dummy_hash
    email = (email or '').strip()
    _check_throttle(email)
    row = db.one('SELECT * FROM users WHERE email=?', (email,))
    if row is None:
        if _dummy_hash is None:
            _dummy_hash = hash_password('dummy-password-1')
        verify_password(password or '', _dummy_hash)  # keep timing similar
    ok = bool(row) and bool(row['is_active']) and verify_password(password or '', row['password_hash'])
    if not ok:
        _record_failure(email)
        audit.log('login_failed', 'user', row['id'] if row else None, {'email': email})
        raise ApiError('invalid_credentials', 'Invalid email or password.', 401)
    with _lock:
        _failures.pop(_throttle_key(email), None)
    db.execute("UPDATE users SET last_login=datetime('now') WHERE id=?", (row['id'],))
    audit.log('login', 'user', row['id'], user={'id': row['id'], 'email': row['email']})
    return row
