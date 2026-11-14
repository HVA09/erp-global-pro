# ERP Global Pro — Stage 21 Runbook

## تشغيل اختبارات prototype (لا تحتاج Flask أو PostgreSQL)

من مجلد `backend`:

```bash
python -m unittest tests.test_postgres_psycopg_adapter_spike -v
python -m unittest tests.test_database_adapter_contract tests.test_postgres_compat_preflight -v
python -m compileall -q erp tests scripts manage.py run.py
```

## تثبيت Psycopg في بيئة مخصصة

لا تنفّذ ذلك في الإنتاج الحالي. في بيئة تطوير/CI منفصلة:

```bash
python -m pip install -r requirements-postgres.txt
```

يحتاج اختبار الاتصال الفعلي إلى PostgreSQL DSN قابل للاستخدام. لا يوجد DSN ضمن المشروع، ولا تضع بيانات الاتصال أو كلمات المرور في Git.

## حدود التشغيل

- لا تستخدم `Psycopg3Adapter` لتشغيل التطبيق الحالي.
- لا تغيّر `DB_PATH` إلى `postgresql://...`؛ طبقة `db.py` لا تختار PostgreSQL بعد.
- لا تستبدل `?` جماعيًا؛ المترجم prototype يرفض JSON `?` operators والـSQL الملتبس.
- أضف `RETURNING id` لكل insert يحتاج ID عند إجراء ترحيل call sites.
- لا تغيّر `TENANT_DATA_SCOPING_ENABLED=False`.

## Stage 22

قبل أي تفعيل: اختبارات مع PostgreSQL disposable، migrations، composite foreign keys، RLS، transaction-local company context، pool reuse، وbackup/restore.
