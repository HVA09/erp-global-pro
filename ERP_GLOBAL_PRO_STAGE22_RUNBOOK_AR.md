# Stage 22 — دليل تشغيل اختبارات PostgreSQL الحية

## 1. الاعتمادات

من مجلد `backend`:

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-postgres.txt
```

يتضمن الملف Psycopg 3 binary وpool كاعتمادات اختيارية منفصلة عن `requirements.txt` الأساسي.

## 2. تشغيل الاختبارات محليًا

أنشئ قاعدة PostgreSQL مؤقتة غير إنتاجية، ثم عيّن DSN لحساب اختبار مؤقت لديه صلاحية إنشاء/حذف دور PostgreSQL. لا تستخدم قاعدة أو حساب إنتاج؛ job CI ينشئ قاعدة disposable مستقلة:

```bash
export ERP_TEST_POSTGRES_ADMIN_DSN='postgresql://postgres:password@127.0.0.1:5432/erp_stage22_test'
export REQUIRE_POSTGRES_INTEGRATION=1
python scripts/require_postgres_integration.py
python -m unittest tests.test_postgres_psycopg_adapter_integration -v
```

الاختبارات تنشئ دورًا ومخططًا بأسماء عشوائية، وتزيلهما عند الانتهاء. استخدم قاعدة اختبار قابلة للحذف فقط، ولا تمرر DSN إنتاجي.

في Windows PowerShell:

```powershell
$env:ERP_TEST_POSTGRES_ADMIN_DSN = 'postgresql://postgres:password@127.0.0.1:5432/erp_stage22_test'
$env:REQUIRE_POSTGRES_INTEGRATION = '1'
python scripts/require_postgres_integration.py
python -m unittest tests.test_postgres_psycopg_adapter_integration -v
```

## 3. CI

يضيف `.github/workflows/erp-backend-ci.yml` job باسم `postgres-adapter-integration` مع خدمة مؤقتة PostgreSQL 16. يقوم job بتثبيت `requirements-postgres.txt`، ويتحقق من اتصال حقيقي، ثم يشغّل الاختبارات على Python 3.11 و3.12.

أي غياب للـDSN أو driver أو PostgreSQL connection يجعل job يفشل. لا توجد skips مسموحة داخل هذا job.

## 4. نطاق الاختبارات الحية

- Psycopg 3 connection وrow factory.
- placeholder binding وliteral percent و`executemany`.
- `INSERT ... RETURNING id`.
- commit/rollback/savepoint/transaction status.
- RLS على جدول تجريبي مع `FORCE ROW LEVEL SECURITY` ودور غير superuser.
- عدم رؤية أي صف دون سياق شركة.
- عدم قبول الكتابة عند غياب سياق الشركة.
- `set_config('app.company_id', ..., true)` يختفي بعد نهاية المعاملة.
- pool بحجم اتصال واحد يعيد backend PID نفسه، ويمنع تسرب السياق بين المستأجرين.

## 5. تحذيرات أمنية

- اختبار RLS التجريبي لا ينشئ بعد سياسات جميع جداول ERP.
- لا تثق بقيمة `company_id` قادمة من العميل؛ يجب استنتاجها من جلسة موثقة وعضوية خادمية.
- custom GUC ليس آلية مصادقة مستقلة؛ دور التطبيق يستطيع غالبًا تعيين متغير custom. يجب الاحتفاظ بتدابير منع SQL injection، واستخدام أقل صلاحيات، ومراجعة طريقة موثوقة لتثبيت سياق tenant قبل اعتبار RLS حدًا أمنيًا كاملًا.
- لا توصل prototype بقاعدة إنتاج في هذه المرحلة.
- لا تفعّل `TENANT_DATA_SCOPING_ENABLED`.

## 6. ما يجب تسجيله بعد أول CI execution

عند تشغيل الـworkflow لأول مرة، احتفظ بما يلي في سجل Stage 23:

- نتيجة job لكل نسخة Python (3.11 و3.12).
- إصدار PostgreSQL الفعلي ونسخة Psycopg/psycopg_pool.
- عدد الاختبارات الحية الناجحة من أصل 11.
- هل تم إعادة استخدام `backend_pid` نفسه في اختبار pool reuse.
- أي تحذيرات أو failures، مع إصلاحها وإعادة التشغيل قبل اعتبار Stage 22 live gate خضراء.

لا تنسخ DSN أو كلمة مرور قاعدة البيانات في التقرير أو commit.
