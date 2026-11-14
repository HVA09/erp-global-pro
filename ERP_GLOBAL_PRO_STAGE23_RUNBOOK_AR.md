# دليل Stage 23 — signed tenant context

## ما تحتويه المرحلة

هذه تجربة مستقلة لتصميم سياق شركة موقّع لـPostgreSQL RLS. لا تستخدم مع قاعدة بيانات إنتاج ولا تستبدل إعداد SQLite.

## الاختبارات المحلية المتاحة

من مجلد `backend`:

```bash
python -m unittest tests.test_signed_tenant_claims tests.test_postgres_signed_tenant_context_design -v
python -m unittest tests.test_postgres_psycopg_adapter_spike tests.test_database_adapter_contract tests.test_postgres_compat_preflight -v
```

يجب أن تعمل اختبارات Stage 23 المحلية دون Flask أو PostgreSQL، لأنها تختبر التوقيع والتعامل مع قالب SQL فقط.

## الاختبارات الحية

تحتاج إلى PostgreSQL 16 مخصص ومؤقت، وملف `requirements-postgres.txt`، وقاعدة لا تحتوي بيانات مهمة. ينفّذ bootstrap القالب مشغّل ترحيل مميزًا يستطيع إنشاء schema/roles، وتثبيت `pgcrypto`، ومنح CREATE مؤقتًا لمخطط crypto، وتعيين مالك الدالة إلى security owner؛ في CI يستخدم PostgreSQL superuser. لا تستخدم حساب التطبيق أو قاعدة إنتاج.

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-postgres.txt
export ERP_TEST_POSTGRES_ADMIN_DSN='postgresql://USER:PASSWORD@HOST:5432/erp_test_db'
export REQUIRE_POSTGRES_INTEGRATION=1
python scripts/require_postgres_integration.py
python -m unittest tests.test_postgres_signed_tenant_context_integration -v
```

تقوم مجموعة الاختبارات بإنشاء roles وschemas وجداول مؤقتة بأسماء عشوائية، وتزيل schemas والـpgcrypto extension والـroles عند اكتمالها. استخدم قاعدة disposable منفصلة فقط.

## القالب SQL

الملف:

`erp/db_adapters/postgres_signed_tenant_context.sql`

هو قالب يحتوي placeholders لأسماء schema/roles. يُرندر فقط باستخدام أسماء identifiers بسيطة يتم التحقق منها بواسطة `postgres_sql.py`. لا تستبدل placeholders يدويًا بقيم قادمة من المستخدم.

الـpreconditions:

- role أمني `NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT`.
- role تطبيق `LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT`.
- لا تكون أي من هذه الأدوار عضوًا في دور آخر؛ يتحقق القالب من ذلك.
- دور التطبيق لا يملك جداول التحكم أو مخططاتها أو مخططات security/crypto ولا يملك `CREATE` فيها.
- جداول التحكم `companies` و`company_memberships` و`users` في schema مخصص موثوق غير `public` وبالحقول المتوقعة (`users.is_active` من نوع boolean في PostgreSQL).
- دور التطبيق ليس مالكًا لأي جدول تحكم أو مخطط أمان/تشفير، ولا يملك CREATE فيها. مخطط crypto ومخطط security يجب أن يكونا مملوكين لدور security owner كما يتحقق القالب.
- امتداد `pgcrypto` متاح.
- امتيازات SELECT لمالك دالة الأمان على جداول التحكم، ومنع التطبيق من قراءة مفاتيح HMAC أو أي جدول control-plane مباشرة.
- لا يحصل دور التطبيق على أي امتياز مباشر على جداول companies/company_memberships/users؛ تُسحب صلاحيات PUBLIC والتطبيق كلها. يجب بناء إجراءات قراءة/إدارة `SECURITY DEFINER` ضيقة الصلاحيات قبل تكامل واجهات إدارتها.
- يتحقق القالب من خصائص الأدوار وعضوياتها وملكية المخططات والجداول ومن موقع pgcrypto، ويرفض المتطلبات غير المطابقة.
- migration operator يحتاج صلاحيات bootstrap موثوقة لتثبيت extension وإسناد الملكية؛ الـCI يستخدم postgres superuser، ولا تستخدم هذا المسار بحساب التطبيق.
- ينفذ القالب داخل معاملة واحدة حتى لا توجد نافذة بين إنشاء الدالة وتقييد صلاحية EXECUTE.
- القالب يسحب جميع امتيازات التطبيق وPUBLIC عن جداول التحكم، ويتحقق أن التطبيق لا يملكها ولا يملك إنشاء كائنات في مخططها. لا تربطه قبل بناء إجراءات `SECURITY DEFINER` موثوقة ومراجعة لإدارة/قراءة الشركات والعضويات والمستخدمين.
- يشغّل القالب DDL في معاملة واحدة حتى لا توجد نافذة بين إنشاء الدالة وسحب EXECUTE الافتراضي من PUBLIC.

## قبل اعتماد القالب

لا تمنح دور التطبيق أي صلاحية مباشرة لقراءة أو إضافة/تعليق عضويات الشركة؛ القالب يسحب الصلاحيات المباشرة. أضف وظائف قراءة وإدارة `SECURITY DEFINER` محدودة وتتحقق من صلاحية المشغل قبل أي تعديل. إذا كانت جداول control-plane تستخدم FORCE RLS، يجب توفير سياسات صريحة لدور security owner؛ فهو لا يملك BYPASSRLS. يجب تصميم إجراءات إدارية موثوقة أو طبقة تحكم منفصلة. لا بد أيضًا من نقل بقية business tables إلى PostgreSQL، وتطبيق composite FKs وسياسات RLS على كل جدول تابع لشركة.

## ملاحظة تشغيلية

التوقيع HMAC لا يعني أن التطبيق أصبح آمنًا تلقائيًا. يحتفظ خادم المصادقة بمفتاح التوقيع، بينما دور قاعدة البيانات لا يقرأه. لا تضع secret في Git أو logs أو ملف ZIP. مقارنة HMAC تعمل على 32 بايت كاملة لتجنب short-circuit، لكن لا تعتبرها ضمانًا constant-time تشفيريًا على مستوى PL/pgSQL. استخدم secrets في بيئة النشر عند بناء المسار الفعلي.

## الحالة الحالية

- SQLite v8 ما زالت المحرك الفعلي.
- Psycopg 3 adapter لا يزال prototype.
- 12 اختبار claims و14 اختبار SQL design محلية؛ 12 حالة اختبار Stage 23 الحية أضيفت إلى CI ولم تُشغّل هنا.
- `TENANT_DATA_SCOPING_ENABLED=False` يجب أن يبقى كما هو.
