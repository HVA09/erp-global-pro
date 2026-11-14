# دليل Stage 18 — تشغيل بوابة HTTP وCI

## في بيئة محلية متصلة

من مجلد `backend/`:

```bash
python -m pip install -r requirements.txt
python scripts/require_http_dependencies.py
python -m unittest discover -s tests -v
python scripts/run_http_tests.py
python -m compileall -q erp tests scripts manage.py run.py
```

يجب أن تنتهي اختبارات Flask HTTP دون أي `skipped`. لا تستخدم Flask stubs بديلًا عن Test Client الحقيقي.

## GitHub Actions

الملف: `.github/workflows/erp-backend-ci.yml`

يشغّل CI الاختبارات على Python 3.11 و3.12. بعد إنشاء مستودع ERP المستقل ورفع branch Stage 18، راجع كل job وتأكد من نجاح `backend-tests` على النسختين ومن عدم وجود skips.

## تعذر التثبيت

إذا فشل DNS أو package index:

- لا تتجاوز `require_http_dependencies.py`.
- لا تعتبر الاختبارات المتخطاة ناجحة.
- لا تفتح `TENANT_DATA_SCOPING_ENABLED`.
- استخدم بيئة CI متصلة لتشغيل HTTP tests، مع الاحتفاظ بهذه الحزمة وسجل الاستمرارية.

## حدود الأمان

المرحلة لا تشغّل ClamAV ولا PostgreSQL/RLS ولا Browser E2E. لا توجد عملية نشر أو تعديل قاعدة إنتاج أو رفع GitHub في هذه المرحلة.
