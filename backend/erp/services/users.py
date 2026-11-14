import re

from .. import db, security
from ..errors import ApiError

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def create_user(email, name, password, role):
    email, name = (email or '').strip(), (name or '').strip()
    if not EMAIL_RE.match(email):
        raise ApiError('invalid_email', 'Enter a valid email address.', 422, details={'email': 'invalid'})
    if not name:
        raise ApiError('validation', 'Name is required.', 422, details={'name': 'required'})
    if not db.one('SELECT name FROM roles WHERE name=?', (role,)):
        raise ApiError('invalid_role', 'Unknown role.', 422, details={'role': 'invalid'})
    security.validate_password(password, email)
    return db.execute('INSERT INTO users(email,name,password_hash,role) VALUES(?,?,?,?)',
                      (email, name, security.hash_password(password), role))
