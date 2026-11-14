# ERP Global Pro — Stage 23 Change Log

**العنوان:** نموذج سياق شركة موقّع لتقوية PostgreSQL RLS
**التصنيف:** Prototype / Security Design — غير موصول بالتطبيق
**قاعدة التطبيق الفعلية:** SQLite v8 دون تغيير

## المشكلة

استخدم نموذج Stage 22 متغير GUC باسم `app.company_id` لتمرير نطاق الشركة. يستطيع دور التطبيق تعيين هذا المتغير، لذلك فهو قناة نقل وليس إثبات هوية أو عضوية. أضافت Stage 23 نموذج HMAC للتحقق من claims داخل PostgreSQL قبل استخدام سياق الشركة في RLS.

## الإضافات البرمجية

- `backend/erp/db_adapters/signed_tenant_context.py`
  - إصدار payload canonical موقّع بـHMAC-SHA256.
  - claims ثابتة: `iss`, `aud`, `kid`, `sub`, `company_id`, `iat`, `exp`, `jti`.
  - TTL أقصى 900 ثانية، وفحص issuer/audience والتوقيع والتوقيت والحجم.
  - رفض `bool` و`float` كمعرفات أو TTL/clock؛ القيم يجب أن تكون أعدادًا صحيحة فعلية.
  - التحقق المحلي يستخدم `hmac.compare_digest`.
- `backend/erp/db_adapters/postgres_signed_tenant_context.sql`
  - قالب PostgreSQL مستقل لإنشاء جدول مفاتيح التوقيع ودالة `SECURITY DEFINER`.
  - `search_path` ثابت مع `pg_temp` أخيرًا.
  - فحص خصائص app/security roles وعضوياتها وملكية مخططات/جداول التحكم.
  - crypto وsecurity schema مملوكان لدور security owner، ومخطط control يجب أن يكون مخصصًا وليس `public`.
  - التطبيق لا يحصل على أي صلاحية مباشرة على `companies`, `company_memberships`, `users`، بما فيها `SELECT`. دور security owner فقط يحصل على `SELECT` للتحقق من العضوية والحالة.
  - إعادة التحقق من نشاط المستخدم والعضوية والشركة في كل statement.
  - الوقت يستخدم `statement_timestamp()` لتجنب استمرار صلاحية token طوال معاملة بدأت قبل انتهاء صلاحيته.
  - مقارنة HMAC تمر على 32 بايت كاملة لتجنب الخروج المبكر عند أول اختلاف؛ هذا لا يثبت ضمان constant-time منخفض المستوى داخل PL/pgSQL.
- `backend/erp/db_adapters/postgres_sql.py`: التحقق من identifiers وتقسيم SQL مع الحفاظ على PL/pgSQL dollar-quoted bodies.

## الاختبارات

- `backend/tests/test_signed_tenant_claims.py`: **12 اختبارًا محليًا**.
- `backend/tests/test_postgres_signed_tenant_context_design.py`: **14 اختبار تصميم محليًا**.
- `backend/tests/test_postgres_signed_tenant_context_integration.py`: **12 اختبار PostgreSQL حيًا** أضيفت إلى GitHub Actions، منها انتهاء token خلال معاملة طويلة.
- تم توسيع job `postgres-adapter-integration` لتشغيل مجموعة Stage 22 (11 اختبارًا حيًا) ومجموعة Stage 23 (12 اختبارًا حيًا) على PostgreSQL 16 مع Python 3.11 و3.12.

## نتائج Stage 23 المحلية

- Stage 21/22 focused regression: **48/48 PASS**.
- Stage 23 signer + SQL design: **26/26 PASS** (12 signer + 14 SQL design).
- إجمالي الاختبارات المحلية المركزة: **74/74 PASS**.
- Python compileall: PASS.
- JavaScript syntax: **19/19 PASS**.
- GitHub Actions YAML parse/wiring: PASS.
- PostgreSQL live suites: **NOT RUN LOCALLY**؛ Stage 22 فيها 11 اختبارًا وStage 23 فيها 12 اختبارًا. لا يوجد PostgreSQL server أو Psycopg runtime أو Docker/Podman في بيئة البناء. تم تأكيد تخطي مجموعتي الاختبار صراحةً بسبب غياب DSN.

## قيود وأمور لم تُنفّذ

- النموذج غير موصول بمصادقة Flask أو JWT أو إعداد DB الحالي.
- لم يُطبّق القالب على جداول ERP الفعلية، ولم يُنفّذ PostgreSQL migration أو Composite FKs أو RLS شامل.
- التطبيق لا يقرأ جداول التحكم مباشرة؛ يجب بناء وظائف قراءة/إدارة `SECURITY DEFINER` دقيقة ومراجعة قبل ربط APIs الحالية.
- إذا كانت جداول control-plane تحت FORCE RLS، تحتاج سياسة قراءة منفصلة لدور security owner؛ لا يملك الدور `BYPASSRLS`.
- token bearer قصير العمر وقابل لإعادة الاستخدام حتى `exp`؛ يمكن دراسة `jti` revocation أو session binding حسب نموذج المخاطر النهائي.
- HMAC لا يحمي من اختراق عملية التطبيق أو تسريب مفتاح التوقيع، ولا يغني عن منع SQL injection/IDOR أو المفاتيح المركبة.
- لم تُشغّل مجموعتا PostgreSQL الحيتان على GitHub؛ المستودع لم يُنشأ أو يُرفع بعد.

## القرار

تحفظ Stage 23 كنموذج أمني وتجهيز CI فقط. لا تفعّل PostgreSQL أو RLS في التطبيق، ولا تغيّر `TENANT_DATA_SCOPING_ENABLED=False`.
