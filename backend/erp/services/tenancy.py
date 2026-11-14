"""Tenant identity helpers. Operational data isolation is a separate release gate."""

from .. import db
from ..errors import ApiError


def create_company(name, country='', currency='USD', industry='general'):
    name = (name or '').strip()
    if not name:
        raise ApiError('validation', 'Company name is required.', 422, details={'company': 'required'})
    if len(name) > 200:
        raise ApiError('validation', 'Company name is too long.', 422, details={'company': 'max_length'})
    currency = (currency or 'USD').strip().upper()[:3] or 'USD'
    country = (country or '').strip()[:100]
    industry = (industry or 'general').strip().lower()[:64] or 'general'
    return db.execute(
        'INSERT INTO companies(name,country,currency,industry) VALUES(?,?,?,?)',
        (name, country, currency, industry),
    )


def add_membership(company_id, user_id, role, status='active', invited_by=None):
    if not db.one('SELECT id FROM companies WHERE id=?', (company_id,)):
        raise ApiError('company_not_found', 'Company not found.', 404)
    if not db.one('SELECT id FROM users WHERE id=?', (user_id,)):
        raise ApiError('user_not_found', 'User not found.', 404)
    if not db.one('SELECT name FROM roles WHERE name=?', (role,)):
        raise ApiError('invalid_role', 'Unknown role.', 422, details={'role': 'invalid'})
    if status not in {'active', 'invited', 'suspended'}:
        raise ApiError('invalid_membership_status', 'Invalid membership status.', 422)
    existing = db.one(
        'SELECT id FROM company_memberships WHERE company_id=? AND user_id=?',
        (company_id, user_id),
    )
    if existing:
        raise ApiError('membership_exists', 'User is already a member of this company.', 409)
    joined_at = "datetime('now')" if status == 'active' else 'NULL'
    # joined_at's expression is selected from a fixed internal allowlist, never user input.
    return db.execute(
        f'INSERT INTO company_memberships(company_id,user_id,role,status,invited_by,joined_at) '
        f'VALUES(?,?,?,?,?,{joined_at})',
        (company_id, user_id, role, status, invited_by),
    )


def list_user_memberships(user_id, active_only=False):
    """List memberships without exposing secrets; inactive memberships are excluded on request."""
    where = 'm.user_id=?'
    if active_only:
        where += " AND m.status='active' AND c.status='active'"
    return db.q(
        f'''SELECT m.id AS membership_id, m.company_id, c.name AS company_name,
                   c.country, c.currency, c.industry, m.role, m.status,
                   c.status AS company_status
            FROM company_memberships m JOIN companies c ON c.id=m.company_id
            WHERE {where} ORDER BY c.name, c.id''',
        (user_id,),
    )


def get_active_membership(user_id, company_id=None, membership_id=None):
    """Resolve a currently active membership and its role permissions from the database."""
    where = ["m.user_id=?", "m.status='active'", "c.status='active'"]
    params = [user_id]
    if company_id is not None:
        where.append('m.company_id=?')
        params.append(company_id)
    if membership_id is not None:
        where.append('m.id=?')
        params.append(membership_id)
    rows = db.q(
        f'''SELECT m.id AS membership_id, m.company_id, m.user_id, m.role,
                   m.status AS membership_status, c.name AS company_name,
                   c.country, c.currency, c.industry, c.status AS company_status,
                   r.permissions
            FROM company_memberships m
            JOIN companies c ON c.id=m.company_id
            JOIN roles r ON r.name=m.role
            WHERE {' AND '.join(where)} ORDER BY c.name, c.id LIMIT 2''',
        tuple(params),
    )
    return rows[0] if len(rows) == 1 else None


def get_default_active_membership(user_id):
    """Legacy tokens may auto-select only when exactly one active membership exists."""
    rows = list_user_memberships(user_id, active_only=True)
    if len(rows) != 1:
        return None
    return get_active_membership(user_id, rows[0]['company_id'], rows[0]['membership_id'])


def membership_company_payload(membership):
    if not membership:
        return None
    return {
        'id': membership['company_id'],
        'name': membership['company_name'],
        'country': membership['country'],
        'currency': membership['currency'],
        'industry': membership['industry'],
        'role': membership['role'],
        'membership_id': membership['membership_id'],
    }


def current_company_id(company_id=None):
    """Resolve a trusted company context for service operations.

    HTTP requests must use the company populated by JWT + active-membership
    validation. Background jobs should pass company_id explicitly. An implicit
    singleton fallback exists only for bootstrapping/tests on legacy one-company
    databases; it is never used when more than one active company exists.
    """
    try:
        from flask import g, has_request_context
        request_context = has_request_context()
    except ImportError:  # Minimal migration/test environments without Flask installed.
        g = None
        request_context = False

    requested = None
    if company_id is not None:
        try:
            requested = int(company_id)
        except (TypeError, ValueError):
            raise ApiError('invalid_company_context', 'Invalid company context.', 400)
        if requested <= 0:
            raise ApiError('invalid_company_context', 'Invalid company context.', 400)

    if request_context:
        active = g.get('company')
        if not active:
            raise ApiError('active_company_required', 'An active company is required.', 409)
        active_id = int(active['id'])
        if requested is not None and requested != active_id:
            raise ApiError('company_context_mismatch', 'The requested company does not match the authenticated company.', 403)
        requested = active_id

    if requested is None:
        rows = db.q("SELECT id FROM companies WHERE status='active' ORDER BY id LIMIT 2")
        if len(rows) != 1:
            raise ApiError('company_context_required', 'An explicit active company context is required.', 409)
        requested = int(rows[0]['id'])

    if not db.one("SELECT id FROM companies WHERE id=? AND status='active'", (requested,)):
        raise ApiError('company_not_found', 'The active company is not available.', 404)
    return requested
