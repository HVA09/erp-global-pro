"""Configuration (environment variables override defaults)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = Path(os.environ.get('ERP_INSTANCE_DIR', BASE_DIR / 'instance'))


class Config:
    ENV = os.environ.get('ERP_ENV', 'development')
    DB_PATH = os.environ.get('ERP_DB_PATH', str(INSTANCE_DIR / 'erp.sqlite3'))
    FILE_STORAGE_DIR = os.environ.get('ERP_FILE_STORAGE_DIR', str(INSTANCE_DIR / 'files'))
    SECRET_KEY = os.environ.get('ERP_SECRET_KEY')
    TOKEN_TTL_HOURS = int(os.environ.get('ERP_TOKEN_TTL_HOURS', '12'))
    FRONTEND_DIR = os.environ.get('ERP_FRONTEND_DIR', str(BASE_DIR.parent / 'frontend'))
    CORS_ORIGINS = [o.strip() for o in os.environ.get('ERP_CORS_ORIGINS', '').split(',') if o.strip()]
    MAX_CONTENT_LENGTH = 11 * 1024 * 1024
    FILE_QUOTA_BYTES = int(os.environ.get('ERP_FILE_QUOTA_BYTES', str(250 * 1024 * 1024)))
    FILE_UPLOADS_PER_HOUR = int(os.environ.get('ERP_FILE_UPLOADS_PER_HOUR', '30'))
    CLAMSCAN_BIN = os.environ.get('ERP_CLAMSCAN_BIN', 'clamscan')
    MAINTENANCE_MODE = os.environ.get('ERP_MAINTENANCE_MODE', '').strip().lower() in {'1', 'true', 'yes'}
    LOGIN_MAX_FAILURES = 5
    LOGIN_WINDOW_SECONDS = 900
    # Keep false until all tenant-owned tables and every read/write path are scoped.
    TENANT_DATA_SCOPING_ENABLED = False
