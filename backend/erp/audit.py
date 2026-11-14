import json

from flask import g, has_request_context, request

from . import db


def log(action, entity=None, entity_id=None, details=None, user=None):
    u = user or (g.get('user') if has_request_context() else None)
    ip = request.remote_addr if has_request_context() else None
    company = g.get('company') if has_request_context() else None
    db.execute(
        'INSERT INTO audit_log(company_id,user_id,user_email,action,entity,entity_id,details,ip) '
        'VALUES(?,?,?,?,?,?,?,?)',
        (company.get('id') if company else None, u['id'] if u else None,
         u['email'] if u else None, action, entity,
         str(entity_id) if entity_id is not None else None,
         json.dumps(details, ensure_ascii=False, default=str) if details is not None else None, ip))
