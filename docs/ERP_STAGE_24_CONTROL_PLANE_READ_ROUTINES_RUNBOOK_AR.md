# Stage 24 — دليل التشغيل والاختبار

## النطاق

Stage 24 توسع نموذج Stage 23 بوظائف قراءة آمنة لمخطط التحكم. لا توصل هذه الوظائف بالتطبيق الحالي ولا تغيّر قاعدة SQLite.

## واجهات SQL التجريبية

بعد تطبيق قالب `backend/erp/db_adapters/postgres_signed_tenant_context.sql` على قاعدة اختبار مؤقتة، وتمرير `erp.tenant_payload` و`erp.tenant_signature` داخل transaction-local GUCs:

```sql
SELECT * FROM erp_security.list_my_companies();
SELECT * FROM erp_security.get_current_company_profile();
SELECT * FROM erp_security.get_current_membership();
```

استبدل `erp_security` باسم المخطط المستخدم في التجربة. يجب أن تكون الاستعلامات داخل المعاملة التي تحتوي السياق الموقّع. بدون سياق صالح تعيد وظائف القراءة صفوفًا فارغة.

## اختبارات محلية بلا PostgreSQL

من مجلد `backend/`:

```bash
python -m unittest \
  tests.test_signed_tenant_claims \
  tests.test_postgres_signed_tenant_context_design \
  tests.test_postgres_psycopg_adapter_spike \
  tests.test_database_adapter_contract \
  tests.test_postgres_compat_preflight -v
```

النتيجة المثبتة في Stage 24: **78/78 PASS**.

## اختبارات PostgreSQL الحية

تتطلب قاعدة بيانات اختبار مؤقتة، ودورًا إداريًا يملك CREATE ROLE/SCHEMA وامتياز تثبيت `pgcrypto`، وتثبيت:

```bash
python -m pip install -r requirements-postgres.txt
export ERP_TEST_POSTGRES_ADMIN_DSN='postgresql://postgres:password@127.0.0.1:5432/erp_stage24_test'
export REQUIRE_POSTGRES_INTEGRATION=1
python scripts/require_postgres_integration.py
python -m unittest \
  tests.test_postgres_psycopg_adapter_integration \
  tests.test_postgres_signed_tenant_context_integration -v
```

تتكون المجموعة الحية من 30 اختبارًا: 11 في محول Psycopg/RLS الأساسي، و19 في signed tenant context والواجهات الآمنة. في CI يجب عدم السماح بالتخطي عند فقدان DSN أو السائق.

## القيود

- لم تتوفر PostgreSQL/Psycopg محليًا في بيئة البناء عند إعداد Stage 24؛ لذا لا توجد نتيجة تشغيل حي موثقة.
- لا تستخدم هذه الوظائف كمسار تسجيل دخول أولي؛ فهي تتطلب سياق شركة موقّعًا مسبقًا.
- لا تستخدم `membership_role` وحده لتفويض أعمال إدارية.
- لا تمنح دور التطبيق SELECT مباشرًا على جداول الشركات/العضويات/المستخدمين.
- لا تطبق القالب على قاعدة إنتاج أو على مخطط ERP الفعلي قبل نجاح اختبارات CI الحية ومراجعة التهديدات.
- أبقِ `TENANT_DATA_SCOPING_ENABLED=False`.
