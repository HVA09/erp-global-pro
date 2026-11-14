# ERP Global Pro — Stage 22 Change Log

**العنوان:** اختبار Psycopg 3 على PostgreSQL حقيقي، واختبار RLS وسياق الشركة مع إعادة استخدام اتصال pool.

## ما أضيف

1. إضافة `backend/tests/test_postgres_psycopg_adapter_integration.py` لاختبارات PostgreSQL فعلية، مفعلة بمتغير `ERP_TEST_POSTGRES_ADMIN_DSN`.
2. إعداد دور مؤقت `NOSUPERUSER NOBYPASSRLS` ومخطط مؤقت فريد لكل تشغيل، ثم إزالتهما بعد الاختبار.
3. إنشاء جدول تجريبي بسياسة RLS و`FORCE ROW LEVEL SECURITY`، مع شركتين واختبار القراءة والكتابة دون سياق.
4. اختبار Psycopg الحقيقي للاتصال، row factory، معاملات qmark، `executemany`، literal percent، `RETURNING id`، commit/rollback، savepoints، `information_schema`، وحالة المعاملة.
5. اختبار `psycopg_pool.ConnectionPool` بحجم واحد للتأكد من إعادة استخدام backend PID نفسه دون تسرب `app.company_id` من الشركة A إلى B، أو بقاء سياق A بعد إعادة الاتصال إلى pool.
6. إضافة `backend/scripts/require_postgres_integration.py` لفشل CI صراحةً عند غياب DSN أو Psycopg أو PostgreSQL.
7. توسيع `backend/requirements-postgres.txt` إلى `psycopg[binary,pool]>=3.3,<4`.
8. إضافة job منفصل في GitHub Actions مع خدمة PostgreSQL 16 ومصفوفة Python 3.11/3.12.

## حدود ما تم تنفيذه

- الـadapter ما زال prototype غير موصول بتكوين التطبيق.
- اختبارات Stage 22 الجديدة لم تُشغّل في بيئة البناء الحالية؛ لا يوجد `psycopg`, `psycopg_pool`, `postgres`, `psql`, Docker أو Podman مثبت. تشغيلها محليًا يتطلب `requirements.txt` الأساسي أيضًا لأن package `erp` يعتمد على Flask.
- تمت إضافة CI job ليشغّل الاختبارات الحية عند GitHub Actions. لا يجوز وصفها بأنها اجتازت قبل تشغيل workflow فعليًا.
- اختبارات RLS هنا نموذج معزول يثبت سلوك `SET LOCAL`/`set_config(..., true)` وسياسة شركة لكل صف. لا يثبت بعد سلامة سياسات جميع جداول ERP أو تحقق العضوية في التطبيق.
- custom GUC مثل `app.company_id` هو سياق يضبطه التطبيق بعد التحقق من العضوية؛ RLS بهذه الصيغة دفاع ضد نسيان النطاق في الاستعلامات، وليس حاجزًا ضد مهاجم يملك تنفيذ SQL اعتباطيًا تحت دور التطبيق ويستطيع تعيين GUC لأي شركة. يجب معالجة SQL injection وأسرار الاتصال وصلاحيات DB على حدة.
- لم تنفذ PostgreSQL migrations للمخطط الكامل، أو composite FKs، أو دمج RLS في جميع جداول الأعمال.
- لا يزال `TENANT_DATA_SCOPING_ENABLED=False`.

## نتيجة التحقق المحلي

سيتم إدراج النتائج الفعلية في `ERP_GLOBAL_PRO_STAGE22_VALIDATION.txt`. يجب أن تظل live PostgreSQL tests مميزة كـ `NOT RUN LOCALLY / DELEGATED TO CI` إلى أن توجد خدمة PostgreSQL حقيقية.
