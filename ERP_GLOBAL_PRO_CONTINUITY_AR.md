# ERP Global Pro — سجل الاستمرارية الرئيسي

**آخر تحديث:** 16 نوفمبر 2026 — Stage 25C runbook مدمج؛ آخر تحديث للحكومة PR #12 مدمج في main.
**آخر مخطط:** SQLite v8؛ لا تغيير في قاعدة التطبيق النشطة.
**آخر حزمة:** `erp-global-pro-stage24-control-plane-read-routines.zip` (أرشيف الأساس الأصلي محفوظ دون تعديل).
**نقطة الاستئناف التالية:** اتبع `docs/ERP_STAGE_25C_OFFLINE_PREFLIGHT_RUNBOOK_AR.md` لإنشاء نسخة SQLite v8 محلية مستقلة والتحقق منها، ثم تشغيل preflight ومراجعة blockers/warnings. بعد اعتماد التقرير فقط، أنشئ control-plane schema وETL تجريبياً على PostgreSQL 16 disposable مع reconciliation وrollback؛ لا ربط للتطبيق قبل إثبات السلامة.
**حالة GitHub:** المستودع `HVA09/erp-global-pro`؛ `main` عند `67d16f05f7962785d486512f4873b7c49ed249b5` بعد PR #12. PR #5 (Stage 25B) مدمج؛ PR #10 (دليل Stage 25C) مدمج عند `2c1a4d1d7e00076fd0d4fc08c6aeb43c74e1bc26` وفحوصه قبل الدمج 4/4 ناجحة في [run 43743727556](https://github.com/HVA09/erp-global-pro/actions/runs/43743727556). فحوص ما بعد الدمج على main في [run 43743932059](https://github.com/HVA09/erp-global-pro/actions/runs/43743932059) كانت قيد التنفيذ عند آخر تحديث.
**حماية main:** أفاد مالك المستودع بأنه فعّل حماية الفرع. لم أغيّر إعدادات الحماية. واجهة التكامل لا تسمح لي بقراءة تفاصيل Branch Protection (403)؛ لا يُعد ذلك دليلاً على أن الحماية معطّلة.
**حالة تعدد الشركات:** حاجز التشغيل مغلق؛ `TENANT_DATA_SCOPING_ENABLED=False`.
**الترحيل:** لم يُنفذ preflight على قاعدة مستخدمين فعلية مستعادة بعد؛ لم تتوفر نسخة SQLite في مساحة العمل الحالية. لا ترحيل أو dual-write أو تعديل إنتاجي.

اقرأ هذا الملف أولًا عند فتح محادثة جديدة، ثم دليل Stage 25C ووثائق Stage 19–25B. هذا السجل يصف ما ثبت وجوده وما تم اختباره، ولا يحل محل فحص الكود.

## 1. هوية المشروع والحدود

- المشروع: ERP Global Pro، Flask + SQLite وواجهة JavaScript خالصة.
- المستودع المستقل: `HVA09/erp-global-pro`؛ تم رفع Stage24 baseline ثم إصلاحات CI في Commit `6badac0`.
- يجب أن يبقى مستقلًا عن `HVA09/ai-chat-app` و`HVA09/lumen-learning-platform`.
- الأرشيف الأصلي محفوظ ولا يُعدّل. SHA-256 الأصلي:

`7943d5f5b1f027463a9ef2bd65116286ad76abdcde7db2e90bcedb4434e9895b`

- لا توجد قاعدة إنتاج أو خدمة استضافة تم تعديلها في هذه المراحل.
- التسليم النهائي بعد اجتياز بوابات الإصدار: ZIP كامل مع المصدر والاختبارات والوثائق وREADME و`.env.example` ودليل الترحيل والتشغيل والنشر والنسخ الاحتياطي وchecksums.

## 2. قرارات المعمارية

- الهدف: منصة متعددة الشركات، ببيانات معزولة تمامًا.
- المستهدف النهائي: PostgreSQL مشترك مع `company_id NOT NULL` في كل جدول تابع للشركة، مفاتيح أجنبية مركبة، فهارس تراعي الشركة، وRLS.
- هوية المستخدم عامة، أما صلاحياته فتأتي من عضويته النشطة داخل الشركة.
- سياق الشركة من token/session موثّق وmembership فعالة؛ لا تثق بـ`X-Company-ID` الخام.
- RLS يجب أن يعتمد transaction-local context بعد التحقق من membership، مع اختبار connection-pool reuse.
- لا نعيد كتابة التطبيق أو نغير قاعدة البيانات دفعة واحدة دون prototype واختبارات قابلة للتكرار.

## 3. سجل المراحل

| المرحلة | النطاق / الحالة |
|---|---|
| Architecture | المعمارية والأمن وخطة التنفيذ |
| 1–4 | الشركات والعضويات، عزل البيانات والعلاقات، Stage 4 Reconciled لأن أرشيفها القديم لم يكن متاحًا |
| 5–8 | preflight uniqueness، تدقيق الاستعلامات، CRUD، callbacks |
| 9–14 | المحاسبة والمخزون والتجارة وHR والتصنيع وCRM والصيانة والمشاريع والتقارير والبيانات |
| 15–16 | المرفقات الخاصة، ClamAV wrapper والحجر والحصص والنسخ الاحتياطي والاستعادة المتكاملان |
| 17–18 | runtime preflight، HTTP gates، GitHub Actions CI؛ HTTP الحقيقي لم يعمل محليًا بسبب Flask/Werkzeug |
| 19 | تدقيق تبعيات SQLite وجرد ما يلزم لنقل PostgreSQL |
| 20 | `DatabaseAdapter` و`SQLiteAdapter` وربط DB functions المركزية، وماسح PostgreSQL |
| 21 | مقارنة Psycopg 3 وSQLAlchemy Core واختيار Psycopg 3 للـprototype؛ adapter spike و32 اختبار Fake DB-API |
| **22** | **اختبارات PostgreSQL حقيقية + RLS تجريبي + pool reuse ضمن CI PostgreSQL 16** | **اجتازت اختبارات CI الحية على PostgreSQL 16 في Python 3.11 و3.12** |
| **23** | **signed tenant context لـRLS: HMAC، دالة SECURITY DEFINER، حماية control plane** | **Prototype فقط؛ اختبارات التصميم والـ19 اختبارًا الحي اجتازت CI** |
| **24** | **واجهات قراءة آمنة لمخطط التحكم: قائمة شركات المستخدم، ملف الشركة الحالية، وعضويته** | **Prototype فقط؛ 78 اختبارًا محليًا، والـ30 اختبار PostgreSQL الحي اجتازت CI لاحقًا** |

## 4. حالة Stage 21

### القرار

تم اختيار Psycopg 3 كبداية prototype لأنه يقدم اتصالًا DB-API مطابقًا أقرب إلى هيكل التطبيق الحالي. SQLAlchemy Core مؤجل، لأنه يضيف نموذج Engine/Connection وCore statements؛ ويمكن استخدام `exec_driver_sql` مع paramstyle السائق، أو `text()` مع named binds، لكنه لا ينقل SQL الحالي تلقائيًا.

### الملفات الجديدة/المعدلة

- `backend/erp/db_adapters/postgres_psycopg.py`: prototype مستقل.
- `backend/erp/db_adapters/__init__.py`: تصدير prototype دون استيراد psycopg وقت import.
- `backend/tests/test_postgres_psycopg_adapter_spike.py`: 32 اختبارًا بـfake connection/cursor.
- `backend/requirements-postgres.txt`: كان اعتمادًا اختياريًا `psycopg[binary]>=3.3,<4` في Stage 21؛ وسّعته Stage 22 إلى `psycopg[binary,pool]>=3.3,<4`، ما زال منفصلًا عن `requirements.txt` العام.
- `docs/ERP_STAGE_21_PSYCOPG3_VS_SQLALCHEMY_CORE_AR.md`، changelog، runbook، validation.

### الوظائف التي يغطيها prototype

- row factory مخصصة `HybridRow` تدعم `row[0]`, `row['name']`, و`dict(row)`.
- مترجم محافظ لعلامات `?` إلى `%s` مع تجاهل strings/identifiers/comments/dollar-quoted literals.
- يرفض mismatch وJSON `?` operators والـSQL الملتبس بدل التخمين.
- يضاعف `%` داخل SQL عند وجود معاملات وفق Psycopg binding syntax.
- `executemany` يتحقق من اتساق معاملات الصفوف قبل التنفيذ.
- insert ID يحتاج `RETURNING id` صريحًا؛ لا يحاكي `lastrowid`.
- `executescript` غير مدعوم؛ ترحيلات PostgreSQL يجب أن تكون versioned.
- `begin(write=True)` يفشل عمدًا؛ لا بد من تصميم بديل لتأمين دلالة `BEGIN IMMEDIATE` قبل نقل أي write path.
- `table_columns` من `information_schema.columns`.

### ما لم يحدث

- لم يتم توصيل adapter بـ`erp/db.py` أو اختيار backend عبر environment.
- لا psycopg installed ولا PostgreSQL server متاح في بيئة البناء.
- لا PostgreSQL migrations/schema/driver integration.
- لا composite foreign keys أو RLS أو pool context leakage tests.
- لا ترحيل لـ`lastrowid`/`INSERT OR IGNORE`/`AUTOINCREMENT`/`PRAGMA`/triggers/SQLite-specific SQL.

## 5. نتائج التحقق في Stage 21

- Psycopg adapter prototype: **32/32 PASS** باستخدام fake DB-API objects.
- SQLite adapter contract: **10/10 PASS**.
- PostgreSQL compatibility preflight: **6/6 PASS**.
- المجموع المحدد: **48/48 PASS**.
- SQLAlchemy compile-only probe: النسخة المحلية 2.0.50، وتم compile لـ`text()` مع bind parameters مسماة إلى صيغة PostgreSQL؛ دون اتصال بقاعدة بيانات.
- فحوص Python/JavaScript تُراجع ضمن إنشاء الحزمة النهائية.

### القيود المستمرة

- Flask/Werkzeug غير مثبتين في بيئة البناء الحالية؛ اختبارات HTTP الحقيقية بقيت متخطاة في المرحلة السابقة.
- لا PostgreSQL أو psycopg runtime integration؛ tests Stage 21 fake DB-API فقط.
- لا Browser E2E ولا ClamAV حقيقي ولا PostgreSQL RLS ولا اختبارات حمل/تزامن.
- لا نشر، ولا رفع GitHub.

## 6. أوامر الاستئناف

من مجلد `backend`:

```bash
python -m unittest tests.test_postgres_psycopg_adapter_spike -v
python -m unittest tests.test_database_adapter_contract tests.test_postgres_compat_preflight -v
python -m compileall -q erp tests scripts manage.py run.py
```

في بيئة PostgreSQL تجريبية فقط، بعد إعداد DSN وعدم تخزينه في Git:

```bash
python -m pip install -r requirements-postgres.txt
```

لا يوجد بعد أمر تشغيل التطبيق باستخدام PostgreSQL؛ لا تغيّر `DB_PATH` أو `.env` لمحاولة التوصيل.

## 7. خطة Stage 23 الأصلية (اكتملت الآن في القسم 11)

1. بعد إنشاء المستودع المستقل ورفع الحزمة، شغّل GitHub Actions job `postgres-adapter-integration` على PostgreSQL 16 لكل من Python 3.11 و3.12.
2. احفظ logs ونتيجة الاختبارات الـ11، وإصدار Psycopg/psycopg_pool/PostgreSQL، وتأكد من نجاح pool backend PID reuse.
3. أصلح أي إخفاق في التوافق أو المعاملات أو RLS قبل بدء ترحيل schema.
4. نفّذ نسخة اختبار PostgreSQL schema/migration prototype على نطاق محدود، ولا توصلها بالتطبيق الكامل.
5. صمّم سياق tenant موثوقًا؛ لا تعتمد على custom GUC وحده كحاجز ضد SQL injection أو دور DB يستطيع تعيين متغيرات custom.
6. بعد ثبوت driver integration، ابدأ composite FKs وRLS على مجموعة صغيرة من الجداول مع rollback tests.
7. أبقِ SQLite backend مدعومًا وبوابة تعدد الشركات مغلقة إلى أن تنجح كل release gates.

## 8. بوابة إطلاق تعدد الشركات

لا تغيّر `TENANT_DATA_SCOPING_ENABLED=False` حتى يتحقق التالي:

- CRUD/services/callbacks/API/reports/import/export/jobs/files/backup/restore كلها scoped.
- composite FKs تمنع cross-company references.
- اختبار شركتين مع تكرار SKUs والأكواد وأرقام المستندات.
- HTTP authentication/IDOR/permissions بدون skips.
- PostgreSQL migrations وRLS وpool context leakage.
- backup/restore مع DB والملفات واختبار rollback.
- ClamAV فعلي وحصص/orphan recovery.
- Browser E2E وأمن الجلسات وCSP واختبارات التزامن والحمل.
- مراجعة بشرية نهائية وخطة rollback.

## 9. GitHub والتسليم

- مستودع ERP المستقل قائم: `HVA09/erp-global-pro`؛ لا تدمج تغييرات Stage25A في `main` قبل المراجعة.
- لا تضع ERP داخل مستودع AI Chat App أو Lumen.
- تم التحقق من رفع Commit `6badac0` ونجاح CI. لم يحدث نشر أو اتصال بقاعدة إنتاج.
- سلّم ZIP كاملًا بعد اجتياز release gate فقط.

---

**تنبيه إطلاق:** Stage 21/22 نموذج أولي وحزمة CI فقط. SQLite v8 ما زالت قاعدة البيانات الوحيدة المتصلة بالتطبيق، و`TENANT_DATA_SCOPING_ENABLED=False`.


## 10. Stage 22 — PostgreSQL live integration CI

### الملفات المضافة
- `backend/tests/test_postgres_psycopg_adapter_integration.py`: 11 اختبارًا حيًا تعتمد DSN لقاعدة مؤقتة.
- `backend/scripts/require_postgres_integration.py`: preflight يرفض غياب DSN أو Psycopg/pool أو اتصال PostgreSQL.
- `backend/requirements-postgres.txt`: أصبح `psycopg[binary,pool]>=3.3,<4`؛ ما زال اختياريًا ومنفصلًا عن requirements العامة.
- `.github/workflows/erp-backend-ci.yml`: job `postgres-adapter-integration` مع PostgreSQL 16 ومصفوفة Python 3.11/3.12.

### نطاق الاختبارات الحية
- اتصال فعلي وrow factory ومعاملات qmark وliteral percent.
- `executemany` و`INSERT ... RETURNING id`.
- commit/rollback/savepoint وحالة المعاملة و`information_schema`.
- دور PostgreSQL غير superuser مع `ENABLE/FORCE ROW LEVEL SECURITY`.
- قراءة الشركة A أو B فقط حسب `set_config('app.company_id', ..., true)`، ورفض الكتابة دون سياق.
- pool بحجم اتصال واحد وإعادة استخدام `backend_pid` نفسه للتحقق من عدم تسرب سياق الشركة بين الاتصالات المستعارة.

### نتائج Stage 22 الفعلية محليًا
- Stage 21 unit prototype + SQLite adapter contract + PostgreSQL preflight: **48/48 PASS** مجددًا.
- Stage 22 live PostgreSQL suite: **NOT RUN LOCALLY** (لا psycopg/psycopg_pool، لا PostgreSQL server، ولا Docker/Podman). تشغيل الوحدة دون DSN ينتج Skip صريحًا؛ CI يفرض `REQUIRE_POSTGRES_INTEGRATION=1` ويجب أن يفشل عند غياب البيئة.
- Python compileall وJavaScript syntax وYAML parse: PASS.
- GitHub Actions job أُضيف إلى الحزمة، لكنه لم يُنفّذ لأن مستودع ERP لم يُنشأ/يُرفع بعد.

### حد أمني مهم
سياسة RLS النموذجية تقرأ `app.company_id` من transaction-local custom GUC. هذا يثبت تقليل خطر نسيان tenant predicate، لكنه ليس مصادقة مستقلة ضد SQL injection أو ضد دور تطبيق يستطيع تعيين GUC اعتباطيًا. يجب ألا يُعتبر RLS حماية كاملة قبل تصميم مصدر سياق موثوق، وقصر صلاحيات DB، ومنع SQL injection، ومراجعة جميع المسارات.

### ما لم يحدث
- لم يتم تشغيل PostgreSQL integration tests الحقيقية بعد.
- لم يتصل التطبيق بـPostgreSQL ولم يتم تفعيل PostgreSQL backend.
- لا PostgreSQL schema/migrations كاملة أو composite FKs على جداول ERP.
- لم تنفذ RLS لجميع الجداول.
- SQLite v8 ما زالت المحرك الوحيد المستخدم بالتطبيق.
- `TENANT_DATA_SCOPING_ENABLED=False`، ولم يتم رفع شيء إلى GitHub.

**Stage 24:** رفع النسخة إلى المستودع المستقل بعد قرار المستخدم، تشغيل workflow PostgreSQL 16 لكلا مجموعتي Stage 22 و23 (22 اختبارًا حيًا إجمالًا)، معالجة أي فشل SQL/privilege، ثم إعداد تجربة control-plane migration صغيرة على قاعدة disposable. لا توصل النموذج بالتطبيق ولا تبدأ ترحيل schema كاملة قبل نجاح اختبارات CI الحية ومراجعة سياسة مفاتيح/عضويات التحكم.


## 11. Stage 23 — Signed Tenant Context RLS prototype

### سبب المرحلة

Stage 22 استخدم GUC باسم `app.company_id` داخل RLS التجريبي. هذا المتغير custom ويمكن لدور التطبيق تعيينه باستخدام `set_config`؛ لذلك لا يمثل هوية موثوقة ولا يصح اعتباره وحده حماية كاملة. Stage 23 أضاف إثبات مفهوم لتوقيع claims والتحقق منها داخل PostgreSQL.

### الملفات المضافة
- `backend/erp/db_adapters/signed_tenant_context.py`: إصدار claims canonical وتوقيعها بـHMAC-SHA256، والتحقق المحلي من issuer/audience/subject/company/key/expiry/tampering.
- `backend/erp/db_adapters/postgres_signed_tenant_context.sql`: SQL template لدالة `SECURITY DEFINER` تقرأ payload/signature من transaction-local GUC، تتحقق من HMAC باستخدام `pgcrypto`, ثم تعيد فحص membership status وcompany status.
- `backend/erp/db_adapters/postgres_sql.py`: render identifiers المسموح بها وتقسيم SQL statements مع حفظ PL/pgSQL dollar-quoted bodies.
- `backend/tests/test_signed_tenant_claims.py`: **12/12** اختبارًا محليًا، بما فيها رفض bool/float كمعرفات أو TTL/clock.
- `backend/tests/test_postgres_signed_tenant_context_design.py`: **14/14** اختبار تصميم/قالب محليًا، بما فيها posture الأدوار ورفض وصول التطبيق إلى جداول التحكم.
- `backend/tests/test_postgres_signed_tenant_context_integration.py`: **12 اختبارًا حيًا لـPostgreSQL** أضيفت إلى job CI.
- `ERP_GLOBAL_PRO_STAGE23_RLS_THREAT_MODEL_AR.md`, `ERP_GLOBAL_PRO_STAGE23_CHANGELOG_AR.md`, `ERP_GLOBAL_PRO_STAGE23_RUNBOOK_AR.md`.

### آلية النموذج
- يصدر auth service token claims قصيرة العمر (الحد الأقصى 900 ثانية) بعد التحقق من المستخدم والعضوية.
- التطبيق يمرر payload والتوقيع في `erp.tenant_payload` و`erp.tenant_signature` transaction-local.
- دالة security owner تقارن HMAC وتعيد فحص العضوية النشطة وحالة الشركة.
- دور التطبيق لا يقرأ مفتاح HMAC ولا يحصل على أي صلاحية مباشرة على control-plane tables؛ القالب يسحب جميع امتيازات PUBLIC والتطبيق عنها، ويتحقق أن الأدوار NOINHERIT ومن دون role memberships، وأن التطبيق ليس مالكًا للجداول أو مخططات الثقة ولا يستطيع CREATE فيها.
- RLS policy التجريبية تستخدم `security_schema.current_company_id()` لا `app.company_id`.
- `SECURITY DEFINER` تستخدم `search_path` محدودًا وتضع `pg_temp` أخيرًا. مخطط crypto ومخطط security يملكانهما security owner. مقارنة HMAC تمر على 32 بايت كاملة، لكن هذا ليس ادعاء ضمان constant-time على مستوى PL/pgSQL.

### نتائج Stage 23 المحلية
- Stage 21/22 focused regression: **48/48 PASS**.
- Stage 23 signer + SQL design: **26/26 PASS** (12 signer + 14 SQL design).
- الإجمالي المحلي المحدد: **74/74 PASS**.
- Python compileall: PASS.
- JavaScript syntax: **19/19 PASS**.
- GitHub Actions YAML parse: PASS.
- Stage 22 live PG suite (11) وStage 23 live PG suite (12): **NOT RUN LOCALLY**؛ لا PostgreSQL server أو psycopg/psycopg_pool أو Docker/Podman في البيئة الحالية. تم إعدادها للتشغيل في CI، لكن لم تُشغّل على GitHub لأن المشروع لم يُرفع بعد.

### حدود التصميم / متطلبات قبل الاعتماد
- هذا prototype غير موصول بـFlask auth/JWT ولا بتهيئة DB الحالية.
- دور التطبيق لا يملك أي امتياز مباشر على جداول companies/company_memberships/users، بما فيها SELECT. يجب بناء إجراءات قراءة/إدارة `SECURITY DEFINER` ضيقة ومراجعة قبل ربط واجهات الإدارة أو المصادقة بهذه الجداول.
- مفتاح HMAC يبقى سرًا في خدمة المصادقة/مخزن أسرار؛ لا يوضع في Git/ZIP/logs ولا يُمنح لدور DB التطبيق.
- هذا لا يعالج اختراق خادم التطبيق أو تسريب token الحالي، ولا يلغي الحاجة إلى منع SQL injection وIDOR.
- الـprototype SQL يعتمد على `pgcrypto` ووجود جداول companies/memberships/users في control schema مخصص؛ لم يُنفذ migration على قاعدة ERP فعلية. إذا كانت control tables تحت FORCE RLS يلزم policy منفصلة لدور security owner لأنه لا يملك BYPASSRLS.
- لا composite FKs أو RLS على كل جداول ERP حتى الآن.
- توقيع HMAC حامل (bearer) قصير العمر ولا يمنع replay حتى انتهاء `exp`؛ أضف jti revocation/nonce أو ربطًا بجلسة عند التصميم النهائي إن تطلب نموذج المخاطر ذلك.

### ما لم يحدث
- لم يتم تشغيل مجموعات PostgreSQL الحية الـ23 على خادم فعلي (Stage 22: 11، Stage 23: 12).
- لم يتم تفعيل PostgreSQL backend أو تغيير SQLite v8.
- لم يتم رفع أو نشر أي شيء إلى GitHub.
- `TENANT_DATA_SCOPING_ENABLED=False` ما زال إلزاميًا.

**Stage 25:** بعد رفع ZIP Stage 24 إلى `HVA09/erp-global-pro`، شغّل job `postgres-adapter-integration` في Python 3.11/3.12، واحفظ logs. يجب أن تنجح اختبارات PostgreSQL الحية الـ30. بعد ذلك فقط ابدأ control-plane migration prototype على قاعدة مؤقتة، ولا توصل السياق بالتطبيق ولا تبدأ ترحيل بيانات ERP حتى تتم مراجعة وظائف الإدارة وصلاحيات membership/key tables.


## 12. Stage 24 — Safe control-plane read routines

### سبب المرحلة
Stage 23 سحبت جميع امتيازات دور التطبيق على `companies` و`company_memberships` و`users` بما فيها SELECT. هذا صحيح أمنيًا لكنه يترك واجهة اختيار الشركة وبيانات الشركة الحالية دون مسار DB آمن. Stage 24 أضافت وظائف قراءة محدودة، دون السماح بإدارة أو تعديل هذه الجداول.

### الوظائف الجديدة
- `current_user_id()` لا تكشف معرف المستخدم إلا بعد نجاح `current_company_id()` في التحقق من signed context والعضوية وحالة الشركة والمستخدم.
- `list_my_companies()` تعرض العضويات النشطة للمستخدم الموثق فقط، ولا تتلقى `user_id` من العميل.
- `get_current_company_profile()` تعرض حقولًا غير سرية للشركة المحددة في claim الموثق.
- `get_current_membership()` تعرض العضوية الحالية ودورها بوصفه معلومة عرض فقط.

جميع هذه الوظائف `SECURITY DEFINER`، بمالك أمان NOLOGIN، و`search_path` ثابت مع `pg_temp` أخيرًا، وEXECUTE ممنوحة لدور التطبيق صراحةً بعد REVOKE من PUBLIC. لم تُضف وظائف تعديل أو إدارة.

### الاختبارات والنتائج
- Stage 21/22 regression: 48/48 PASS.
- Stage 23 signer + SQL design: 30/30 PASS (12 signer + 18 design).
- إجمالي الاختبارات المحلية المركزة: **78/78 PASS**.
- Python compileall: PASS؛ JavaScript syntax: 19/19 PASS؛ GitHub Actions YAML: PASS.
- Stage 22 live PostgreSQL: 11 اختبارًا؛ Stage 23/24 live PostgreSQL: 19 اختبارًا؛ الإجمالي **30 اختبارًا حيًا لم تُشغّل محليًا** لغياب PostgreSQL/Psycopg/DSN. يجب تشغيلها من CI بعد رفع المستودع.

### الحدود
- `list_my_companies()` ليست bootstrap/login endpoint؛ تتطلب سياق شركة موقّعًا صالحًا بالفعل.
- لا توجد وظائف إدارة عضويات أو إضافة/تعليق شركات أو مستخدمين.
- `membership_role` ليس وحده قرار تفويض؛ يلزم بناء دوال تفويض إدارية ضيقة ومراجعتها في مرحلة مستقلة.
- الوظائف لم تُربط بـFlask/JWT أو endpoints الحالية، ولم تُطبق على مخطط ERP فعلي.
- لم يُنفذ PostgreSQL migration أو Composite FKs أو RLS شامل.

**الحالة:** Stage 24 prototype لقراءة التحكم فقط. SQLite v8 تظل قاعدة التطبيق، و`TENANT_DATA_SCOPING_ENABLED=False` إلزامية. لا GitHub upload أو production deployment حتى الآن.


## 13. Stage 25A — نتائج CI الحية ومراجعة نقطة تكامل التطبيق
**التاريخ:** 14 نوفمبر 2026  
**Commit المختبر:** `6badac066a0fdd10ae6bda79f753fee5edae6210`  
**GitHub Actions run:** [43640872392](https://github.com/HVA09/erp-global-pro/actions/runs/43640872392)  
**النتيجة:** `success` — جميع المهام الأربع نجحت.

### نتائج المصفوفة
- Backend tests — Python 3.11: 32 اختبار adapter prototype ناجح، و303 اختبار Backend ناجح (مع تخطي فئتي PostgreSQL في هذا job لأنهما تشغّلان في job منفصل)، و11 اختبار HTTP حقيقي ناجح.
- Backend tests — Python 3.12: النتائج نفسها؛ 32 + 303 + 11، مع التخطيين المتوقعين في مجموعة Backend العامة.
- Live PostgreSQL — Python 3.11: **30/30 PASS** على خدمة PostgreSQL 16.
- Live PostgreSQL — Python 3.12: **30/30 PASS** على خدمة PostgreSQL 16.
- تتكون المجموعة الحية من 11 اختبار Psycopg/adapter/RLS/pool و19 اختبار signed tenant context وcontrol-plane read routines.
- Python compileall وJavaScript syntax ورفع artifact الخاص بجرد توافق PostgreSQL اجتازت خطوات CI.

### حدود هذه النتيجة
- النجاح يثبت prototype وقوالب RLS وقراءات control-plane في بيئة اختبار مؤقتة؛ لا يثبت أن التطبيق الفعلي يستخدم PostgreSQL.
- `backend/erp/db.py` ما زال يختار `SQLITE_ADAPTER` مباشرة ويشغّل schema/migrations خاصة بـSQLite.
- `security.load_user()` وواجهات login/companies/switch-company ما زالت تقرأ المستخدمين والأدوار والعضويات من طبقة قاعدة التطبيق الحالية.
- محول Psycopg 3 وsigned context غير موصولين بـFlask أو دورة الطلب.
- وظائف Stage24 لا تنشئ جلسة دخول أولية؛ تتطلب سياق شركة موقّعًا صالحًا مسبقًا.
- SQLite v8 تبقى قاعدة التطبيق الفعلية، و`TENANT_DATA_SCOPING_ENABLED=False`.

### ملاحظات صيانة غير مانعة
- ظهر ResourceWarning عن stream ملف في اختبار تنزيل مرفق؛ لم يفشل الاختبار. يلزم إصلاح إغلاق stream في fixture/test.
- ظهرت تحذيرات deprecation من `Flask.__version__` وتبعيات Node المستخدمة في GitHub Actions. لا تمثل إخفاقًا في هذا التشغيل.

## 14. Stage 25A — مراجعة تكامل Flask / JWT / PostgreSQL
أُجريت مراجعة ساكنة للملفات:
`backend/erp/__init__.py`, `security.py`, `api/auth.py`, `db.py`, `db_adapters/protocol.py`, `db_adapters/postgres_psycopg.py`, `db_adapters/signed_tenant_context.py`, `postgres_signed_tenant_context.sql`, و`services/tenancy.py`.

النتائج:
1. Flask يستدعي `security.load_user()` في `before_request`. هذه الدالة تتحقق من JWT ثم تقرأ `users` و`roles` وتعيد التحقق من `company_memberships` عبر DB layer الحالية.
2. `make_token()` و`/login` و`/companies` و`/switch-company` تعتمد حاليًا على خدمات tenancy وبيانات SQLite.
3. `db.py` يربط `SQLITE_ADAPTER` مباشرة؛ عقد `DatabaseAdapter` لا يكفي وحده لتشغيل التطبيق على PostgreSQL لأن schema/migrations والمعاملات وبعض SQL وعمليات الملفات والنسخ لا تزال خاصة بـSQLite.
4. SQL الخاص بـStage23/24 يفترض وجود control-plane tables في PostgreSQL، ويحجب SELECT المباشر على تلك الجداول عن app role. كما أن `tenant_signing_keys` لا يستطيع التطبيق قراءته مباشرة.
5. إجراءات القراءة في Stage24 مثل `list_my_companies()` تحتاج سياقًا موقّعًا قائمًا؛ لذلك لا تصلح بمفردها لتسجيل الدخول الأولي أو اختيار الشركة قبل وجود السياق.
6. لا توجد حتى الآن واجهة PostgreSQL ضيقة ومختبرة لتدفق credential verification وتهيئة أول tenant context، ولا آلية مكتملة لتوزيع/تدوير HMAC keys بين خدمة التوقيع ومخزن المفاتيح المحمي في PostgreSQL.

**القرار الوقائي:** لا نربط signer بـFlask ولا نضيف مسارًا هجينًا يقرأ الهوية من SQLite والعضويات من PostgreSQL في هذه الخطوة. ازدواج مصدر الحقيقة أو المزامنة غير الذرية قد ينتج صلاحيات قديمة أو متناقضة.

**Stage 25B:** تم اعتماد PostgreSQL مصدر الحقيقة النهائي والوحيد للمستخدمين والشركات والعضويات والأدوار وبيانات ERP بعد الترحيل الكامل. نُشر قرار معماري وعقد ترحيل في وثيقتي `docs/ERP_STAGE_25B_IDENTITY_SOURCE_OF_TRUTH_AR.md` و`docs/ERP_STAGE_25B_CONTROL_PLANE_MIGRATION_RUNBOOK_AR.md`. لا dual-write؛ SQLite v8 تبقى القاعدة النشطة حتى اجتياز جميع الاختبارات والقطع المخطط.


## 15. Stage 25B — قرار مصدر الحقيقة وعقد ترحيل control plane
**التاريخ:** 14 نوفمبر 2026  
**القرار:** PostgreSQL هو مصدر الحقيقة النهائي والوحيد للمستخدمين والشركات والعضويات والأدوار وبيانات ERP بعد اكتمال الترحيل والقطع. SQLite v8 تبقى قاعدة التطبيق الوحيدة حاليًا. يمنع dual-write أو تقسيم سلطة الهوية والعضوية بين قاعدتين.

### النموذج المعتمد
- المستخدم هوية عالمية، ويمكن أن يملك عضويات متعددة.
- العضوية هي مصدر الدور والصلاحيات الفعلية للشركة المختارة.
- `users.role` حقل legacy للتوافق/التدقيق فقط؛ لا يستخدم لتفويض عمليات الشركة بعد القطع.
- `roles` الحالي كتالوج قوالب عالمي؛ العضوية تربط الدور. الأدوار المخصصة لكل شركة لم تُعتمد بعد وتحتاج تصميمًا مستقلًا.
- بصمات كلمات المرور الحالية scrypt تُنقل حرفيًا بعد اختبارات تحقق؛ لا إعادة ضبط جماعية.
- البريد يحتفظ بقيمته الأصلية ويُنشأ له canonical value موحد بعد preflight تصادمات.

### تصميم المصادقة
- التحقق من بيانات الدخول يتم عبر خدمة مصادقة معزولة ذات هوية DB وصلاحيات محدودة، لا عبر دور التطبيق التشغيلي.
- تحديات ما قبل اختيار الشركة عشوائية وقصيرة العمر؛ تحفظ بصمتها فقط، وتتيح عرض عضويات صاحبها دون قبول user ID حر من العميل.
- اختيار الشركة يستهلك التحدي ذريًا ويصدر سياقًا موقّعًا قصير العمر؛ JWT التطبيق وسياق PostgreSQL عنصران منفصلان.
- مفاتيح HMAC في مخطط أمني محمي، ولا يقرأها دور التطبيق أو دور المصادقة مباشرة.

### عقد الترحيل
- مصدر الترحيل نسخة SQLite v8 متسقة ومستعادة وread-only.
- Preflight لا يعدّل المصدر، ويكشف التصادمات والعضويات والأدوار المجهولة والحالات غير الصالحة.
- يتم الاستيراد أولًا إلى staging في PostgreSQL مؤقتة، مع الحفاظ على IDs وFKs وscrypt hashes.
- تُطابق أعداد الصفوف والبصمات canonical row digests والمراجع والأدوار وحالات العضوية قبل قبول النتيجة.
- أي تعارض يوقف ETL؛ لا `ON CONFLICT DO NOTHING` لإخفاء السجلات.
- القطع النهائي مستقبلًا يتم بتجميد الكتابات وأخذ backup نهائي، ثم نقل ومطابقة كاملة وتبديل قاعدة واحدة فقط. الرجوع إلى SQLite مسموح فقط قبل قبول كتابات PostgreSQL جديدة، وإلا يلزم forward recovery.

### الملفات المرجعية
- `docs/ERP_STAGE_25B_IDENTITY_SOURCE_OF_TRUTH_AR.md`: قرار الهوية والصلاحيات والمصادقة.
- `docs/ERP_STAGE_25B_CONTROL_PLANE_MIGRATION_RUNBOOK_AR.md`: جداول النقل وpreflight وETL والمطابقة والقطع.
- `docs/ERP_STAGE_25_APPLICATION_TENANT_CONTEXT_PLAN_AR.md`: مراحل الربط اللاحقة.

### ما لم يحدث
- لم يُنشأ PostgreSQL schema أو migration تنفيذي بعد.
- لم تُنقل بيانات أو كلمات مرور إلى أي قاعدة PostgreSQL.
- لم تُربط خدمة المصادقة أو Flask بـPostgreSQL.
- لا تغيير لـSQLite v8 أو قاعدة إنتاج أو إعدادات تشغيل.
- `TENANT_DATA_SCOPING_ENABLED=False` ما زالت إلزامية.

**الحالة التنفيذية:** أضيف `backend/scripts/control_plane_preflight.py` لقراءة SQLite v8 دون تعديل، مع 11 اختبارًا مركزًا على كل من Python 3.11 و3.12. شملت الاختبارات بناء `schema.sql` الفعلي وتشغيل ترحيلات SQLite حتى v8 باستخدام بيانات اصطناعية. اجتاز التشغيل CI رقم `43643939785` في المهام الأربع.

**نقطة الاستئناف:** تشغيل preflight على نسخة SQLite v8 حقيقية ومستعادة في بيئة المستخدم ومراجعة التقرير، ثم بناء control-plane schema ومشغل migration تجريبي على PostgreSQL 16 disposable مع اختبارات rollback وreconciliation.


## 16. Stage25B — نتيجة التحقق من أداة preflight
- **GitHub Actions:** [43643939785](https://github.com/HVA09/erp-global-pro/actions/runs/43643939785) — `success`, 4/4 jobs.
- **Python 3.11 و3.12:** 11/11 اختبارًا مركزًا لأداة preflight في كل إصدار.
- الاختبار الفعلي الإضافي أنشأ قاعدة من `backend/erp/schema.sql` ثم نفذ `apply_migrations` حتى SQLite v8 قبل تشغيل preflight ببيانات اصطناعية؛ اجتاز في الإصدارين.
- Full backend suite: **314 tests PASS** لكل إصدار، مع تخطي فئتي PostgreSQL في المجموعة العامة لأنها تعمل في job منفصل.
- Live PostgreSQL 16: **30/30 PASS** في كل إصدار Python.
- HTTP integration: 11/11 PASS في كل إصدار.
- Python compileall وJavaScript syntax: PASS.

**الحد:** الاختبارات تستخدم بيانات اصطناعية. لم تُشغّل الأداة بعد على نسخة قاعدة مستخدم حقيقية؛ لا توجد نسخة SQLite v8 فعلية مقدمة للتشغيل في هذه الجلسة. لذلك لا يوجد تقرير جاهزية ETL لبيانات ERP الفعلية بعد.

مرجع التقرير: `ERP_GLOBAL_PRO_STAGE25B_PREFLIGHT_VALIDATION.txt`.


## آخر نقطة تحقق — 16 نوفمبر 2026

- GitHub يؤكد أن PR #12 — تحديث سجل الحوكمة بعد Stage 25C — مدمج في `main` عند `67d16f05f7962785d486512f4873b7c49ed249b5`.
- تمت مقارنة هذا الالتزام مع `main` وكانت النتيجة `identical`.
- لا يزال التنفيذ المتبقي لStage 25C محلياً: إنشاء نسخة مستقلة ومتسقة من SQLite v8 عبر Backup API، والتحقق من `integrity_check` و`foreign_key_check` وSHA-256، ثم تشغيل Preflight بوضع القراءة فقط.
- لا ترسل قاعدة البيانات أو ملفات WAL/SHM أو تقريراً غير منقح إلى GitHub أو ChatGPT؛ شارك ملخص blockers/warnings منقحاً فقط.
- بعد مراجعة التقرير الفعلي فقط يمكن الانتقال إلى ETL تجريبي على PostgreSQL 16 disposable مع reconciliation وrollback؛ لا يبدأ قبل ذلك.
- لا يوجد دليل على PostgreSQL cutover أو dual-write أو تفعيل multi-company. يبقى `TENANT_DATA_SCOPING_ENABLED=False`.
