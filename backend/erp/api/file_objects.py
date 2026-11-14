"""Tenant-scoped private attachment API.

The existing /api/files endpoint remains the legacy metadata/link resource.
Binary objects use /api/file-objects and are never served from the frontend.
"""
from flask import Blueprint, current_app, g, jsonify, request, send_file

from ..errors import ApiError
from ..security import require
from ..services import file_storage, tenancy

bp = Blueprint('file_objects', __name__, url_prefix='/api/file-objects')


def _roots():
    return current_app.config['FILE_STORAGE_DIR'], current_app.config['FRONTEND_DIR']


def _active_company():
    company = g.get('company') or {}
    return tenancy.current_company_id(company.get('id'))


def _positive_int(value, field, default):
    if value in (None, ''):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ApiError('validation', f'{field} must be a number.', 422, details={field: 'invalid_number'})
    if parsed < 0:
        raise ApiError('validation', f'{field} must be non-negative.', 422, details={field: 'min'})
    return parsed


@bp.get('')
@require('documents.read')
def list_files():
    cid = _active_company()
    limit = _positive_int(request.args.get('limit'), 'limit', 100)
    offset = _positive_int(request.args.get('offset'), 'offset', 0)
    items = file_storage.list_metadata(company_id=cid, limit=limit, offset=offset)
    usage = file_storage.storage_usage(company_id=cid, quota_bytes=current_app.config['FILE_QUOTA_BYTES'])
    total = usage['file_count']
    return jsonify(items=items, total=total, usage=usage)


@bp.get('/usage')
@require('documents.read')
def file_usage():
    return jsonify(usage=file_storage.storage_usage(
        company_id=_active_company(), quota_bytes=current_app.config['FILE_QUOTA_BYTES'],
    ))


@bp.post('/upload')
@require('documents.write')
def upload_file():
    cid = _active_company()
    if 'company_id' in request.form:
        raise ApiError('tenant_field_forbidden', 'Company ownership is assigned by the server.', 422)
    upload = request.files.get('file')
    if upload is None:
        raise ApiError('file_required', 'Select a file to upload.', 422)
    # Read one byte beyond the limit so oversized payloads are rejected before storage.
    content = upload.stream.read(file_storage.MAX_FILE_BYTES + 1)
    storage_root, frontend_root = _roots()
    row = file_storage.upload_bytes(
        filename=upload.filename,
        content=content,
        title=request.form.get('title'),
        category=request.form.get('category'),
        related_to=request.form.get('related_to'),
        note=request.form.get('note'),
        company_id=cid,
        uploaded_by=(g.get('user') or {}).get('id'),
        storage_root=storage_root,
        frontend_root=frontend_root,
        company_quota_bytes=current_app.config['FILE_QUOTA_BYTES'],
        uploads_per_hour=current_app.config['FILE_UPLOADS_PER_HOUR'],
    )
    return jsonify(file=row), 201


@bp.get('/<int:file_id>')
@require('documents.read')
def get_file(file_id):
    return jsonify(file=file_storage.get_metadata(file_id, company_id=_active_company()))


@bp.get('/<int:file_id>/download')
@require('documents.read')
def download_file(file_id):
    cid = _active_company()
    storage_root, frontend_root = _roots()
    row, path = file_storage.get_download(
        file_id, company_id=cid, storage_root=storage_root, frontend_root=frontend_root,
    )
    response = send_file(
        path,
        mimetype=row['mime_type'],
        as_attachment=True,
        download_name=row['original_name'],
        conditional=False,
        max_age=0,
    )
    # Defense in depth: attachments are never rendered inline by the browser.
    # send_file(as_attachment=True) supplies the safe attachment disposition and filename.
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.delete('/<int:file_id>')
@require('documents.write')
def delete_file(file_id):
    storage_root, frontend_root = _roots()
    result = file_storage.delete_file(
        file_id, company_id=_active_company(), storage_root=storage_root, frontend_root=frontend_root,
    )
    return jsonify(result)


def register(app):
    app.register_blueprint(bp)
