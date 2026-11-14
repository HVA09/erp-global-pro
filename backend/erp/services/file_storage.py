"""Private tenant-aware attachment storage.

Files are stored outside the frontend/static root. The browser never supplies a
filesystem path or storage key; download and delete operations are always scoped
by the authenticated company context.
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
import time
import shutil
import subprocess
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from .. import audit, db
from ..errors import ApiError
from . import tenancy

MAX_FILE_BYTES = 10 * 1024 * 1024
_ALLOWED = {
    '.pdf': ('application/pdf',),
    '.png': ('image/png',),
    '.jpg': ('image/jpeg',),
    '.jpeg': ('image/jpeg',),
    '.webp': ('image/webp',),
    '.txt': ('text/plain; charset=utf-8',),
    '.csv': ('text/csv; charset=utf-8',),
}
_STORAGE_KEY_RE = re.compile(r'^[0-9a-f]{32}\.(?:pdf|png|jpg|jpeg|webp|txt|csv)$')



def validate_legacy_link(link):
    """Allow safe external links in the legacy file metadata CRUD resource."""
    link = str(link or '').strip()
    if not link:
        return None
    if any(ord(ch) < 32 for ch in link):
        raise ApiError('invalid_file_link', 'Document links cannot contain control characters.', 422)
    try:
        parsed = urlsplit(link)
        valid = (parsed.scheme.lower() in {'http', 'https'} and bool(parsed.hostname)
                 and bool(parsed.netloc) and parsed.username is None and parsed.password is None)
    except ValueError:
        valid = False
    if not valid:
        raise ApiError('invalid_file_link', 'Document links must be absolute HTTP or HTTPS URLs.', 422)
    return link

def _safe_display_name(filename: str) -> tuple[str, str]:
    supplied = str(filename or '')
    if '/' in supplied or '\\' in supplied:
        raise ApiError('invalid_file_name', 'Path components are not allowed in file names.', 422)
    raw = supplied.strip()
    if not raw or raw in {'.', '..'} or any(unicodedata.category(ch) == 'Cc' for ch in raw):
        raise ApiError('invalid_file_name', 'A safe file name is required.', 422)
    ext = Path(raw).suffix.lower()
    if ext not in _ALLOWED:
        raise ApiError('file_type_not_allowed', 'This file type is not allowed.', 415,
                       details={'allowed': sorted(_ALLOWED)})
    stem = raw[:-len(Path(raw).suffix)] if Path(raw).suffix else raw
    stem = re.sub(r'[^A-Za-z0-9._ -]+', '_', stem).strip(' ._')
    stem = re.sub(r'\s+', ' ', stem)[:120]
    if not stem:
        stem = 'attachment'
    return stem + ext, ext


def _sniff_content(ext: str, content: bytes) -> str:
    if not content:
        raise ApiError('empty_file', 'The uploaded file is empty.', 422)
    if len(content) > MAX_FILE_BYTES:
        raise ApiError('file_too_large', 'The file exceeds the 10 MiB upload limit.', 413)
    if ext == '.pdf':
        valid = content.startswith(b'%PDF-')
    elif ext == '.png':
        valid = content.startswith(b'\x89PNG\r\n\x1a\n')
    elif ext in ('.jpg', '.jpeg'):
        valid = content.startswith(b'\xff\xd8\xff')
    elif ext == '.webp':
        valid = len(content) >= 12 and content[:4] == b'RIFF' and content[8:12] == b'WEBP'
    else:
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ApiError('invalid_text_file', 'Text and CSV files must use UTF-8 encoding.', 422)
        if '\x00' in text or any(unicodedata.category(ch) == 'Cc' and ch not in '\r\n\t' for ch in text):
            raise ApiError('invalid_text_file', 'The text file contains unsupported control characters.', 422)
        valid = True
    if not valid:
        raise ApiError('file_signature_mismatch', 'The file contents do not match the file extension.', 415)
    return _ALLOWED[ext][0]


def _private_root(storage_root, frontend_root=None) -> Path:
    candidate = Path(storage_root).expanduser()
    if candidate.is_symlink():
        raise ApiError('unsafe_storage_root', 'File storage root must not be a symbolic link.', 500)
    if frontend_root:
        public = Path(frontend_root).expanduser().resolve()
        prospective = candidate.resolve(strict=False)
        try:
            prospective.relative_to(public)
        except ValueError:
            pass
        else:
            raise ApiError('unsafe_storage_root', 'File storage must be outside the public frontend directory.', 500)
    candidate.mkdir(parents=True, exist_ok=True, mode=0o700)
    if candidate.is_symlink():
        raise ApiError('unsafe_storage_root', 'File storage root was replaced by a symbolic link.', 500)
    root = candidate.resolve(strict=True)
    if root == Path(root.anchor):
        raise ApiError('unsafe_storage_root', 'A filesystem root cannot be used for file storage.', 500)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def _tenant_dir(root: Path, company_id: int, create=False) -> Path:
    candidate = root / str(int(company_id))
    if candidate.is_symlink():
        raise ApiError('unsafe_storage_path', 'Tenant storage directory must not be a symbolic link.', 500)
    if create:
        candidate.mkdir(mode=0o700, parents=False, exist_ok=True)
    if candidate.is_symlink():
        raise ApiError('unsafe_storage_path', 'Tenant storage directory was replaced by a symbolic link.', 500)
    resolved = candidate.resolve(strict=create)
    if resolved.parent != root or resolved.name != str(int(company_id)):
        raise ApiError('unsafe_storage_path', 'Invalid tenant storage directory.', 500)
    if create:
        try:
            os.chmod(resolved, 0o700)
        except OSError:
            pass
    return resolved


def _object_path(root: Path, company_id: int, storage_key: str, create_dir=False) -> Path:
    if not isinstance(storage_key, str) or not _STORAGE_KEY_RE.fullmatch(storage_key):
        raise ApiError('invalid_storage_key', 'The stored attachment key is invalid.', 500)
    tenant_dir = _tenant_dir(root, company_id, create=create_dir)
    path = tenant_dir / storage_key
    resolved = path.resolve(strict=False)
    if resolved.parent != tenant_dir:
        raise ApiError('unsafe_storage_path', 'The attachment path is invalid.', 500)
    return path


def _validate_metadata(title, category, related_to, note):
    title = str(title or '').strip()
    if not title:
        raise ApiError('validation', 'Attachment title is required.', 422, details={'title': 'required'})
    if len(title) > 200:
        raise ApiError('validation', 'Attachment title is too long.', 422, details={'title': 'max_length'})
    clean = {}
    for key, value, limit in (('category', category, 100), ('related_to', related_to, 150), ('note', note, 1000)):
        value = str(value or '').strip()
        if len(value) > limit:
            raise ApiError('validation', f'{key} is too long.', 422, details={key: 'max_length'})
        clean[key] = value or None
    return title, clean


def upload_bytes(*, filename, content, title=None, category=None, related_to=None, note=None,
                 company_id=None, uploaded_by=None, storage_root, frontend_root=None,
                 company_quota_bytes=250 * 1024 * 1024, uploads_per_hour=30):
    # Quota reservation, hourly throttling, bytes creation and metadata insertion
    # share one SQLite write transaction. A failed commit removes new bytes.
    cid = tenancy.current_company_id(company_id)
    user_id = int(uploaded_by) if uploaded_by is not None else None
    if user_id is not None and user_id <= 0:
        raise ApiError('invalid_user_context', 'Invalid uploader identity.', 400)
    safe_name, ext = _safe_display_name(filename)
    content = bytes(content or b'')
    mime_type = _sniff_content(ext, content)
    title, metadata = _validate_metadata(title or safe_name, category, related_to, note)
    quota = int(company_quota_bytes)
    rate = int(uploads_per_hour)
    if quota <= 0 or rate <= 0:
        raise ApiError('file_upload_disabled', 'File uploads are not configured for this company.', 503)
    if len(content) > quota:
        raise ApiError('file_quota_exceeded', 'The file exceeds the company storage quota.', 413)

    root = _private_root(storage_root, frontend_root)
    _tenant_dir(root, cid, create=True)
    key = uuid.uuid4().hex + ext
    path = _object_path(root, cid, key, create_dir=True)
    digest = hashlib.sha256(content).hexdigest()
    fd = None
    try:
        with db.transaction():
            used = int(db.scalar('SELECT COALESCE(SUM(size_bytes),0) FROM file_objects WHERE company_id=?', (cid,)) or 0)
            if used + len(content) > quota:
                raise ApiError('file_quota_exceeded', 'The company storage quota would be exceeded.', 413,
                               details={'quota_bytes': quota, 'used_bytes': used})
            if user_id is not None:
                now = int(time.time())
                bucket = (now // 3600) * 3600
                cursor = db.run(
                    'INSERT INTO file_upload_buckets(company_id,user_id,bucket_start,upload_count) '
                    'VALUES(?,?,?,1) ON CONFLICT(company_id,user_id,bucket_start) DO UPDATE '
                    'SET upload_count=upload_count+1 WHERE upload_count < ?',
                    (cid, user_id, bucket, rate),
                )
                if cursor.rowcount != 1:
                    raise ApiError('file_upload_rate_limited', 'Upload limit reached. Try again later.', 429,
                                   details={'uploads_per_hour': rate})
                db.execute('DELETE FROM file_upload_buckets WHERE bucket_start < ?', (now - 48 * 3600,))

            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                fd = None
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            file_id = db.execute(
                'INSERT INTO file_objects '
                '(company_id,storage_key,original_name,title,category,related_to,note,mime_type,'
                'size_bytes,sha256,uploaded_by,scan_status) '
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending')",
                (cid, key, safe_name, title, metadata['category'], metadata['related_to'], metadata['note'],
                 mime_type, len(content), digest, user_id),
            )
            audit.log('upload', 'file_object', file_id, {'title': title, 'size_bytes': len(content),
                                                        'mime_type': mime_type, 'scan_status': 'pending'})
        return get_metadata(file_id, company_id=cid)
    except BaseException:
        if fd is not None:
            os.close(fd)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

def list_metadata(*, company_id=None, limit=100, offset=0):
    cid = tenancy.current_company_id(company_id)
    limit = min(200, max(1, int(limit)))
    offset = max(0, int(offset))
    rows = db.q(
        '''SELECT id,company_id,original_name,title,category,related_to,note,mime_type,
                  size_bytes,sha256,scan_status,scanned_at,uploaded_by,created_at
           FROM file_objects WHERE company_id=? ORDER BY id DESC LIMIT ? OFFSET ?''',
        (cid, limit, offset),
    )
    for row in rows:
        row['download_url'] = f"/api/file-objects/{row['id']}/download"
    return rows


def get_metadata(file_id, *, company_id=None):
    cid = tenancy.current_company_id(company_id)
    row = db.one(
        '''SELECT id,company_id,original_name,title,category,related_to,note,mime_type,
                  size_bytes,sha256,scan_status,scanned_at,uploaded_by,created_at
           FROM file_objects WHERE id=? AND company_id=?''',
        (int(file_id), cid),
    )
    if not row:
        raise ApiError('not_found', 'Attachment not found.', 404)
    row['download_url'] = f"/api/file-objects/{row['id']}/download"
    return row


def _quarantine_dir(root: Path, company_id: int, create=False) -> Path:
    base = root / '.quarantine'
    if base.is_symlink():
        raise ApiError('unsafe_quarantine_path', 'The quarantine directory must not be a symbolic link.', 500)
    if create:
        base.mkdir(mode=0o700, parents=False, exist_ok=True)
    if base.is_symlink():
        raise ApiError('unsafe_quarantine_path', 'The quarantine directory was replaced by a symbolic link.', 500)
    base_resolved = base.resolve(strict=create)
    if base_resolved.parent != root or base_resolved.name != '.quarantine':
        raise ApiError('unsafe_quarantine_path', 'The quarantine directory is invalid.', 500)
    tenant = base_resolved / str(int(company_id))
    if tenant.is_symlink():
        raise ApiError('unsafe_quarantine_path', 'The quarantine tenant directory must not be a symbolic link.', 500)
    if create:
        tenant.mkdir(mode=0o700, parents=False, exist_ok=True)
    if tenant.is_symlink():
        raise ApiError('unsafe_quarantine_path', 'The quarantine tenant directory was replaced by a symbolic link.', 500)
    resolved = tenant.resolve(strict=create)
    if resolved.parent != base_resolved or resolved.name != str(int(company_id)):
        raise ApiError('unsafe_quarantine_path', 'The quarantine company directory is invalid.', 500)
    if create:
        try:
            os.chmod(resolved, 0o700)
        except OSError:
            pass
    return resolved


def _quarantine_object_path(root: Path, company_id: int, storage_key: str, create=False) -> Path:
    if not isinstance(storage_key, str) or not _STORAGE_KEY_RE.fullmatch(storage_key):
        raise ApiError('invalid_storage_key', 'The stored attachment key is invalid.', 500)
    return _quarantine_dir(root, company_id, create=create) / storage_key


def _flag_download_integrity_error(row, company_id):
    with db.transaction():
        cursor = db.run(
            "UPDATE file_objects SET scan_status='error',scan_engine='integrity-check',"
            "scanned_at=datetime('now') WHERE id=? AND company_id=? AND sha256=? AND scan_status='clean'",
            (int(row['id']), int(company_id), str(row['sha256'])),
        )
        if cursor.rowcount == 1:
            audit.log('file_integrity_error', 'file_object', row['id'],
                      {'company_id': int(company_id), 'issue': 'download_hash_or_size_mismatch'})


def get_download(file_id, *, company_id=None, storage_root, frontend_root=None):
    cid = tenancy.current_company_id(company_id)
    row = db.one(
        'SELECT id,company_id,storage_key,original_name,mime_type,size_bytes,sha256,scan_status FROM file_objects '
        'WHERE id=? AND company_id=?',
        (int(file_id), cid),
    )
    if not row:
        raise ApiError('not_found', 'Attachment not found.', 404)
    if row.get('scan_status') != 'clean':
        if row.get('scan_status') == 'infected':
            raise ApiError('file_quarantined', 'This file was quarantined by malware scanning.', 410)
        raise ApiError('file_scan_pending', 'The file is unavailable until an antivirus scan reports it clean.', 423)
    root = _private_root(storage_root, frontend_root)
    path = _object_path(root, cid, row['storage_key'])
    if not path.is_file():
        raise ApiError('file_missing', 'The attachment content is unavailable.', 410)
    resolved = path.resolve(strict=True)
    if path.is_symlink() or resolved.parent != _tenant_dir(root, cid):
        raise ApiError('unsafe_storage_path', 'The attachment path is invalid.', 500)
    if not _matches_file_metadata(resolved, row['size_bytes'], row['sha256']):
        _flag_download_integrity_error(row, cid)
        raise ApiError('file_integrity_failed',
                       'Attachment bytes no longer match the approved scan metadata; download is blocked.', 410)
    return row, resolved


def delete_file(file_id, *, company_id=None, storage_root, frontend_root=None):
    cid = tenancy.current_company_id(company_id)
    root = _private_root(storage_root, frontend_root)
    with db.transaction():
        row = db.one(
            'SELECT id,storage_key,title,scan_status FROM file_objects WHERE id=? AND company_id=?',
            (int(file_id), cid),
        )
        if not row:
            raise ApiError('not_found', 'Attachment not found.', 404)
        normal_path = _object_path(root, cid, row['storage_key'])
        quarantine_path = _quarantine_object_path(root, cid, row['storage_key'])
        db.execute('DELETE FROM file_objects WHERE id=? AND company_id=?', (int(file_id), cid))
        audit.log('delete', 'file_object', file_id, {'title': row['title']})
    try:
        normal_path.unlink(missing_ok=True)
        quarantine_path.unlink(missing_ok=True)
    except OSError as exc:
        raise ApiError('file_cleanup_failed', 'Attachment record was deleted but storage cleanup failed.', 500) from exc
    return {'ok': True}


def storage_usage(*, company_id=None, quota_bytes=250 * 1024 * 1024):
    cid = tenancy.current_company_id(company_id)
    used = int(db.scalar('SELECT COALESCE(SUM(size_bytes),0) FROM file_objects WHERE company_id=?', (cid,)) or 0)
    count = int(db.scalar('SELECT COUNT(*) FROM file_objects WHERE company_id=?', (cid,)) or 0)
    return {'used_bytes': used, 'quota_bytes': int(quota_bytes), 'file_count': count,
            'available_bytes': max(0, int(quota_bytes) - used)}


def _scan_state(file_id, company_id, expected_sha256, status, engine, detail=None):
    """Update a scan result only while the exact pending object is still current."""
    with db.transaction():
        cursor = db.run(
            "UPDATE file_objects SET scan_status=?,scan_engine=?,scanned_at=datetime('now') "
            "WHERE id=? AND company_id=? AND sha256=? AND scan_status IN ('pending','error')",
            (status, engine, int(file_id), int(company_id), str(expected_sha256)),
        )
        if cursor.rowcount != 1:
            return False
        audit.log('file_scan_' + status, 'file_object', file_id,
                  {'company_id': int(company_id), 'engine': engine, 'detail': detail})
        return True


def _matches_file_metadata(path: Path, expected_size: int, expected_sha256: str) -> bool:
    """Fail closed if bytes changed after upload or while antivirus scanning."""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        if path.stat().st_size != int(expected_size):
            return False
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest() == str(expected_sha256)
    except OSError:
        return False


def scan_pending_files(*, storage_root, frontend_root=None, scanner_bin='clamscan', include_errors=False,
                       timeout_seconds=60):
    # The scanner is invoked with argv (never shell=True); absence fails closed.
    binary = shutil.which(str(scanner_bin or 'clamscan'))
    if not binary:
        raise ApiError('antivirus_unavailable', 'ClamAV is not installed or ERP_CLAMSCAN_BIN is invalid.', 503)
    root = _private_root(storage_root, frontend_root)
    statuses = "('pending','error')" if include_errors else "('pending')"
    rows = db.q(
        'SELECT id,company_id,storage_key,size_bytes,sha256 FROM file_objects '
        f'WHERE scan_status IN {statuses} ORDER BY id'
    )
    results = {'checked': 0, 'clean': 0, 'infected': 0, 'errors': 0, 'issues': []}

    def mark_error(row, engine, issue):
        _scan_state(row['id'], row['company_id'], row['sha256'], 'error', engine, issue)
        results['errors'] += 1
        results['issues'].append({'id': row['id'], 'issue': issue})

    for row in rows:
        file_id = int(row['id'])
        engine = Path(binary).name
        path = _object_path(root, row['company_id'], row['storage_key'])
        try:
            resolved = path.resolve(strict=True)
            if path.is_symlink() or resolved.parent != _tenant_dir(root, row['company_id']):
                mark_error(row, engine, 'unsafe_path')
                continue
        except (OSError, ApiError):
            mark_error(row, engine, 'missing_file')
            continue

        # Stored metadata is the trust anchor. Do not let ClamAV bless altered bytes.
        if not _matches_file_metadata(resolved, row['size_bytes'], row['sha256']):
            mark_error(row, engine, 'content_integrity_mismatch')
            continue

        try:
            process = subprocess.run([binary, '--no-summary', str(resolved)], capture_output=True,
                                     text=True, timeout=int(timeout_seconds), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            mark_error(row, engine, type(exc).__name__)
            continue
        results['checked'] += 1

        # Detect replacement or modification during the external scanner call.
        if not _matches_file_metadata(resolved, row['size_bytes'], row['sha256']):
            mark_error(row, engine, 'content_changed_during_scan')
            continue

        if process.returncode == 0:
            if _scan_state(file_id, row['company_id'], row['sha256'], 'clean', engine):
                results['clean'] += 1
            else:
                results['errors'] += 1
                results['issues'].append({'id': file_id, 'issue': 'record_changed_during_scan'})
        elif process.returncode == 1:
            try:
                quarantine = _quarantine_object_path(root, row['company_id'], row['storage_key'], create=True)
                os.replace(resolved, quarantine)
            except (OSError, ApiError):
                mark_error(row, engine, 'quarantine_move_failed_or_unsafe')
                continue
            if _scan_state(file_id, row['company_id'], row['sha256'], 'infected', engine, 'signature_detected'):
                results['infected'] += 1
            else:
                # The metadata row changed while scanning; restore the bytes and
                # leave the object unavailable for a later reconciliation scan.
                try:
                    os.replace(quarantine, resolved)
                except OSError:
                    results['issues'].append({'id': file_id, 'issue': 'quarantine_rollback_failed'})
                results['errors'] += 1
                results['issues'].append({'id': file_id, 'issue': 'record_changed_during_scan'})
        else:
            mark_error(row, engine, 'scanner_failed')
    return results


def verify_storage_integrity(*, storage_root, frontend_root=None):
    root = _private_root(storage_root, frontend_root)
    rows = db.q('SELECT id,company_id,storage_key,size_bytes,sha256,scan_status FROM file_objects ORDER BY id')
    issues = []
    for row in rows:
        try:
            if row.get('scan_status') == 'infected':
                path = _quarantine_object_path(root, row['company_id'], row['storage_key'])
                expected_parent = _quarantine_dir(root, row['company_id'])
            else:
                path = _object_path(root, row['company_id'], row['storage_key'])
                expected_parent = _tenant_dir(root, row['company_id'])
            if not path.is_file():
                issues.append({'id': row['id'], 'issue': 'missing_quarantine' if row.get('scan_status') == 'infected' else 'missing'})
                continue
            resolved = path.resolve(strict=True)
            if resolved.parent != expected_parent:
                issues.append({'id': row['id'], 'issue': 'unsafe_path'})
                continue
            if resolved.stat().st_size != row['size_bytes']:
                issues.append({'id': row['id'], 'issue': 'size_mismatch'})
                continue
            if hashlib.sha256(resolved.read_bytes()).hexdigest() != row['sha256']:
                issues.append({'id': row['id'], 'issue': 'hash_mismatch'})
        except (OSError, ApiError):
            issues.append({'id': row['id'], 'issue': 'unsafe_or_unreadable'})
    return {'checked': len(rows), 'ok': len(rows) - len(issues), 'issues': issues}


def audit_orphan_files(*, storage_root, frontend_root=None):
    # Report only; unknown files are never deleted automatically.
    root = _private_root(storage_root, frontend_root)
    rows = db.q('SELECT company_id,storage_key,scan_status FROM file_objects')
    tracked = set()
    for row in rows:
        base = '.quarantine/' if row.get('scan_status') == 'infected' else ''
        tracked.add(f"{base}{int(row['company_id'])}/{row['storage_key']}")
    orphans, unsafe = [], []
    for tenant_dir in root.iterdir():
        if tenant_dir.name == '.quarantine':
            continue
        if tenant_dir.is_symlink() or not tenant_dir.is_dir() or not tenant_dir.name.isdigit():
            unsafe.append(tenant_dir.name)
            continue
        for item in tenant_dir.iterdir():
            rel = f'{tenant_dir.name}/{item.name}'
            if item.is_symlink() or not item.is_file():
                unsafe.append(rel)
            elif rel not in tracked:
                orphans.append(rel)
    quarantine = root / '.quarantine'
    if quarantine.exists():
        if quarantine.is_symlink() or not quarantine.is_dir():
            unsafe.append('.quarantine')
        else:
            for tenant_dir in quarantine.iterdir():
                if tenant_dir.is_symlink() or not tenant_dir.is_dir() or not tenant_dir.name.isdigit():
                    unsafe.append(f'.quarantine/{tenant_dir.name}')
                    continue
                for item in tenant_dir.iterdir():
                    rel = f'.quarantine/{tenant_dir.name}/{item.name}'
                    if item.is_symlink() or not item.is_file():
                        unsafe.append(rel)
                    elif rel not in tracked:
                        orphans.append(rel)
    return {'orphan_count': len(orphans), 'unsafe_count': len(unsafe),
            'orphans': sorted(orphans), 'unsafe_entries': sorted(unsafe), 'deleted': 0}
