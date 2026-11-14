"""Uniform JSON errors: {"error": {"code", "message", "params", "details"}}.
The frontend translates by `code` (+ `params`), so server messages stay in English."""
import logging
import sqlite3

from flask import jsonify
from werkzeug.exceptions import HTTPException

log = logging.getLogger('erp')


class ApiError(Exception):
    def __init__(self, code, message=None, status=400, params=None, details=None):
        super().__init__(message or code)
        self.code = code
        self.message = message or code.replace('_', ' ')
        self.status = status
        self.params = params or {}
        self.details = details


def _resp(code, message, status, params=None, details=None):
    return jsonify(error={'code': code, 'message': message, 'params': params or {}, 'details': details}), status


def register_error_handlers(app):
    @app.errorhandler(ApiError)
    def _api(e):
        return _resp(e.code, e.message, e.status, e.params, e.details)

    @app.errorhandler(sqlite3.IntegrityError)
    def _integrity(e):
        msg = str(e)
        if 'immutable' in msg:
            return _resp('immutable', 'Posted accounting records cannot be changed or deleted; reverse them instead.', 409)
        if 'UNIQUE' in msg:
            field = msg.rsplit('.', 1)[-1].strip()
            return _resp('duplicate', f'Duplicate value for {field}', 409, {'field': field})
        if 'FOREIGN KEY' in msg:
            return _resp('in_use', 'This record is referenced by other data.', 409)
        if 'CHECK' in msg:
            return _resp('invalid_value', 'A value is outside the allowed range.', 422)
        return _resp('integrity_error', 'Database constraint violated.', 409)

    @app.errorhandler(HTTPException)
    def _http(e):
        code = {404: 'not_found', 405: 'method_not_allowed', 413: 'too_large'}.get(e.code, 'http_error')
        return _resp(code, e.description or e.name, e.code)

    @app.errorhandler(Exception)
    def _unhandled(e):
        log.exception('Unhandled error')
        return _resp('server_error', 'Unexpected server error.', 500)
