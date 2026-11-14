"""ERP Global Pro - application factory."""
import logging
import os
import secrets
from pathlib import Path

from flask import Flask, g, request, send_from_directory
from werkzeug.exceptions import NotFound

from . import db as database
from . import security
from .config import Config
from .errors import ApiError, register_error_handlers

PUBLIC_PATHS = {'/api/health', '/api/auth/setup-status', '/api/auth/setup', '/api/auth/login'}
COMPANY_CONTEXT_PATHS = {
    '/api/auth/me', '/api/auth/companies', '/api/auth/switch-company', '/api/auth/change-password',
}


def _ensure_secret(app):
    if app.config.get('SECRET_KEY'):
        return
    if app.config['ENV'] == 'production':
        raise RuntimeError('ERP_SECRET_KEY must be set when ERP_ENV=production.')
    key_file = Path(app.config['DB_PATH']).parent / 'secret.key'
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if key_file.exists():
        app.config['SECRET_KEY'] = key_file.read_text().strip()
    else:
        key = secrets.token_urlsafe(48)
        key_file.write_text(key)
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        app.config['SECRET_KEY'] = key


def create_app(config=None):
    app = Flask(__name__, static_folder=None)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    restore_marker = Path(str(app.config['DB_PATH']) + '.restore-in-progress.json')
    if restore_marker.exists() or restore_marker.is_symlink():
        raise RuntimeError('A database + files restore was interrupted. Keep ERP offline and run: python manage.py recover-bundle-restore')
    _ensure_secret(app)
    app.json.ensure_ascii = False
    app.json.sort_keys = False
    logging.basicConfig(level=logging.INFO)
    register_error_handlers(app)
    database.init_db(app)
    app.teardown_appcontext(database.close_db)

    origins = set(app.config['CORS_ORIGINS'])

    @app.before_request
    def _guard():
        if app.config.get('MAINTENANCE_MODE') and request.path.startswith('/api/'):
            raise ApiError('maintenance_mode', 'ERP is in maintenance mode. Please try again later.', 503)
        if not request.path.startswith('/api/'):
            return None
        if request.method == 'OPTIONS' and request.headers.get('Origin') in origins:
            return '', 204
        security.load_user()
        if request.path not in PUBLIC_PATHS and not g.user:
            raise ApiError('unauthorized', 'Authentication required.', 401)
        if g.get('user') and request.path not in PUBLIC_PATHS and request.path not in COMPANY_CONTEXT_PATHS:
            if not g.get('company'):
                raise ApiError('active_company_required', 'Select an active company before using the ERP.', 409)
            company_count = database.scalar('SELECT COUNT(*) FROM companies') or 0
            if company_count > 1 and not app.config.get('TENANT_DATA_SCOPING_ENABLED'):
                raise ApiError(
                    'tenant_isolation_pending',
                    'Company selection is available, but operational access is blocked until all business data is isolated by company.',
                    503,
                )
        return None

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('X-Frame-Options', 'DENY')
        resp.headers.setdefault('Referrer-Policy', 'same-origin')
        resp.headers.setdefault(
            'Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
        if request.path.startswith('/api/'):
            resp.headers['Cache-Control'] = 'no-store'
            origin = request.headers.get('Origin')
            if origin in origins:
                resp.headers['Access-Control-Allow-Origin'] = origin
                resp.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
                resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
                resp.headers['Vary'] = 'Origin'
        else:
            resp.headers.setdefault('Cache-Control', 'no-cache')
        return resp

    from . import resources
    for resource in resources.ALL:
        resource.register(app)
    from .api import register_blueprints
    register_blueprints(app)

    frontend = Path(app.config['FRONTEND_DIR'])

    @app.get('/')
    def index():
        return send_from_directory(frontend, 'index.html')

    @app.get('/<path:path>')
    def assets(path):
        if path.startswith('api/'):
            raise NotFound()
        return send_from_directory(frontend, path)

    return app
