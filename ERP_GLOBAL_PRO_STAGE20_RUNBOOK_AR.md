# دليل Stage 20 — طبقة قاعدة البيانات

## فحص PostgreSQL الساكن

من مجلد `backend`:

```bash
python scripts/postgres_compat_preflight.py --output artifacts/postgres-compat-preflight.json
```

هذا الفحص read-only للمصدر. لا يتصل بخادم PostgreSQL ولا يثبت التوافق. قيمة علامات `?` عدد lexical وليست عدد معاملات مؤكدة؛ لا تستبدلها جماعيًا.

## اختبارات العقد والماسح

من مجلد `backend`:

```bash
python -m unittest tests.test_database_adapter_contract -v
python -m unittest tests.test_postgres_compat_preflight -v
python -m unittest discover -s tests -p 'test_tenant*.py' -v
```

## CI

يشغّل GitHub Actions مجموعة الاختبارات الكاملة وHTTP no-skip gate، وينتج تقرير PostgreSQL preflight كـartifact معلوماتي. لم يُشغّل workflow بعد لأن مستودع ERP غير موجود/غير مرفوع.

## قيد مهم

المحرك الوحيد المدعوم الآن SQLite. عقد adapter نقطة فصل أولية، وليس ترخيصًا لاستخدام `DATABASE_URL` مع PostgreSQL. المخطط والترحيلات وbackup/restore وإدارة المنصة ومعالجة بعض الأخطاء ما زالت SQLite-specific.

لا تغيّر `TENANT_DATA_SCOPING_ENABLED=False`.
