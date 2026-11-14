# ERP Global Pro — Stage 20 Change Log

## التغييرات

1. إضافة عقد `DatabaseAdapter`.
2. إضافة `SQLiteAdapter` مطابق للسلوك الحالي، وربط دوال الاتصال والمعاملات المركزية في `erp/db.py` به.
3. إضافة `postgres_compat_preflight.py` لإنتاج جرد ساكن لتبعيات SQLite.
4. إضافة 10 اختبارات للعقد و6 اختبارات للماسح.
5. إضافة تقرير preflight إلى GitHub Actions كـartifact معلوماتي، من دون جعله ادعاء توافق أو شرط نجاح PostgreSQL.

## ما لم يتغير

- مخطط البيانات ما زال SQLite v8.
- لا psycopg ولا SQLAlchemy مضافان.
- لا PostgreSQL adapter ولا migrations PostgreSQL ولا RLS.
- لم تُفتح بوابة تعدد الشركات.
- لا رفع GitHub ولا نشر إنتاجي.

راجع `docs/ERP_STAGE_20_DB_ADAPTER_CONTRACT_AR.md` و`docs/ERP_STAGE_19_POSTGRES_AUDIT_AR.md`.
